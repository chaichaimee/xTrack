# splitAlbumByTracks.py

import wx
import core
import ctypes
import math
import os
import re
import shutil
import tempfile
import threading
import nvwave
import tones
import ui
from logHandler import log
import addonHandler
from .xTrackCore import get_config_path, load_config, save_config, get_file_duration
from .ffmpegSupport import (
	limitSpokenText, summarizeFfmpegError, removeQuietly, partPathFor, runFfmpeg, formatClock,
)

addonHandler.initTranslation()

SILENCE_START_PATTERN = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
SILENCE_END_PATTERN = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")
INVALID_FILENAME_CHARS = '<>:"/\\|?*'
PREVIEW_SECONDS = 15
# silencedetect prints a line for every silence, so a long album needs far more than the default tail.
ANALYSIS_STDERR_LINES = 200000
# How much of the silence stays around a track: a short lead-in before the first
# note and a short tail after the last, so a track never starts or ends abruptly.
LEAD_IN_SECONDS = 0.15
TAIL_SECONDS = 0.25
# 0 means the gap between tracks is chosen automatically from the gaps the file really has.
DEFAULT_MIN_SILENCE = 0.0
DEFAULT_EXPECTED_TRACKS = 0
# The first pass keeps every silence of at least this length; the minimum silence is then applied
# without running ffmpeg again, which is what makes trying several values cheap.
PROBE_MIN_SILENCE = 0.5
AUTO_FLOOR_SILENCE = 1.0
AUTO_CEILING_SILENCE = 8.0
AUTO_STEP = 0.25
# Tracks of an album are normally in this range; a split into much longer or shorter pieces is less likely to be right.
AUTO_TYPICAL_TRACK_SECONDS = (60.0, 600.0)
AUTO_ATYPICAL_WEIGHT = 0.4
_NOT_CACHED = object()
# Silence put in front of and after every track, so a song never starts or stops the moment the file begins or ends.
DEFAULT_LEAD_SILENCE = 0.5
DEFAULT_TAIL_SILENCE = 0.5
# Silence that is not already in the album has to be added, which means the track is encoded again.
PAD_ENCODER_ARGS = {
	".mp3": ["-c:a", "libmp3lame", "-q:a", "0"],
	".flac": ["-c:a", "flac"],
	".wav": ["-c:a", "pcm_s16le"],
	".m4a": ["-c:a", "aac", "-b:a", "256k"],
	".aac": ["-c:a", "aac", "-b:a", "256k"],
	".ogg": ["-c:a", "libvorbis", "-q:a", "6"],
	".opus": ["-c:a", "libopus", "-b:a", "192k"],
	".wma": ["-c:a", "wmav2", "-b:a", "256k"],
}
MIN_PAD_SECONDS = 0.02
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
SW_RESTORE = 9
REFOCUS_DELAY_MS = 1500
DEFAULT_LEVEL_DB = -40
DEFAULT_MIN_TRACK = 10
# A track ends where the sound reaches this level, not where it first gets quiet, so a
# gradual fade out at the end of a track is kept until it is really silent.
DEFAULT_END_LEVEL_DB = -70
# How much of the quiet stretch before a gap the second look includes.
TAIL_LOOKBACK_SECONDS = 2.0
# The stricter silence only has to last this long: a fade out uses up part of the gap.
STRICT_MIN_SILENCE = 0.3
CUE_FRAMES_PER_SECOND = 75
CUE_MAX_BYTES = 1024 * 1024
CUE_LINE_PATTERN = re.compile(r"^\s*([A-Za-z]+)\s*(.*?)\s*$")
CUE_INDEX_PATTERN = re.compile(r"^(\d{1,3})\s+(\d{1,3}):(\d{1,2}):(\d{1,2})$")
CUE_TRACK_PATTERN = re.compile(r"^(\d{1,3})\b")


def parse_silences(stderrLines, durationSeconds):
	"""List of (start, end) silent gaps from silencedetect's messages; a silence that runs to the end of the file ends at durationSeconds."""
	silences = []
	pendingStart = None
	for line in stderrLines:
		startMatch = SILENCE_START_PATTERN.search(line)
		if startMatch:
			pendingStart = max(0.0, float(startMatch.group(1)))
			continue
		endMatch = SILENCE_END_PATTERN.search(line)
		if endMatch:
			end = float(endMatch.group(1))
			start = pendingStart if pendingStart is not None else 0.0
			if end > start:
				silences.append((start, end))
			pendingStart = None
	if pendingStart is not None and durationSeconds > pendingStart:
		silences.append((pendingStart, durationSeconds))
	return silences


def apply_complete_silence(gap, strictGaps, isTrailing):
	"""The gap (start, end) of a track end after looking at it again with the stricter level.

	The gap starts where the sound is completely silent instead of where it first
	became quiet. When the sound never gets that quiet inside the gap, the start
	moves to the middle of the gap, so the fade out is kept as far as the cut
	anyway falls there; for the very end of the file None is returned and the end
	of the file is left alone.
	"""
	inside = sorted(strict for strict in strictGaps if strict[0] >= gap[0] - 0.05 and strict[1] > strict[0])
	if inside:
		return (max(inside[0][0], gap[0]), gap[1])
	if isTrailing:
		return None
	return ((gap[0] + gap[1]) / 2, gap[1])


def compute_segments_with_onsets(silences, durationSeconds, minTrackSeconds, leadInSeconds=LEAD_IN_SECONDS, tailSeconds=TAIL_SECONDS):
	"""Tracks as (start, end, onset, offset) tuples, cut inside the silent gaps.

	onset and offset are where the sound of the track really begins and ends; the
	silence between start and onset (and between offset and end) is the lead-in
	(and tail) the track already has. Silence at the very start or end of the file
	is trimmed away, and a piece shorter than minTrackSeconds is joined to its
	neighbour instead of being reported as a track of its own.
	"""
	gaps = sorted(silences)
	trackStart = 0.0
	onset = 0.0
	if gaps and gaps[0][0] <= 0.05:
		leading = gaps.pop(0)
		trackStart = max(leading[0], leading[1] - leadInSeconds)
		onset = leading[1]
	finalEnd = durationSeconds
	finalOffset = durationSeconds
	if gaps and gaps[-1][1] >= durationSeconds - 0.05:
		trailing = gaps.pop()
		finalEnd = min(trailing[1], trailing[0] + tailSeconds)
		finalOffset = trailing[0]

	segments = []
	for gapStart, gapEnd in gaps:
		middle = (gapStart + gapEnd) / 2
		segments.append([trackStart, min(gapStart + tailSeconds, middle), onset, gapStart])
		onset = gapEnd
		trackStart = max(gapEnd - leadInSeconds, middle)
	segments.append([trackStart, finalEnd, onset, finalOffset])
	segments = [segment for segment in segments if segment[1] > segment[0]]

	index = 0
	while len(segments) > 1 and index < len(segments):
		if segments[index][1] - segments[index][0] >= minTrackSeconds:
			index += 1
			continue
		if index < len(segments) - 1:
			segments[index + 1][0] = segments[index][0]
			segments[index + 1][2] = segments[index][2]
			del segments[index]
		else:
			segments[index - 1][1] = segments[index][1]
			segments[index - 1][3] = segments[index][3]
			del segments[index]
			index = max(0, index - 1)
	return [tuple(segment) for segment in segments]


def compute_segments(silences, durationSeconds, minTrackSeconds, leadInSeconds=LEAD_IN_SECONDS, tailSeconds=TAIL_SECONDS):
	return [(segment[0], segment[1]) for segment in compute_segments_with_onsets(silences, durationSeconds, minTrackSeconds, leadInSeconds, tailSeconds)]


def lead_padding(start, onset, leadSeconds):
	"""Seconds of silence still missing in front of a track that should have leadSeconds of it."""
	missing = leadSeconds - max(0.0, onset - start)
	return missing if missing >= MIN_PAD_SECONDS else 0.0


def tail_padding(end, offset, tailSeconds):
	"""Seconds of silence still missing after a track that should have tailSeconds of it."""
	missing = tailSeconds - max(0.0, end - offset)
	return missing if missing >= MIN_PAD_SECONDS else 0.0


def pad_arguments(extension, leadPad, tailPad):
	"""ffmpeg options that put silence in front of and after a track; empty when none is needed or the format is not supported."""
	encoder = PAD_ENCODER_ARGS.get(extension.lower())
	if not encoder or (leadPad <= 0 and tailPad <= 0):
		return []
	filters = []
	if leadPad > 0:
		filters.append(f"adelay=delays={int(round(leadPad * 1000))}:all=1")
	if tailPad > 0:
		filters.append(f"apad=pad_dur={tailPad:.3f}")
	return ["-af", ",".join(filters)] + encoder


def filter_gaps(silences, minSilence):
	return [gap for gap in silences if gap[1] - gap[0] >= minSilence - 0.001]


def candidate_thresholds(lowest):
	thresholds = []
	value = lowest
	while value <= AUTO_CEILING_SILENCE + 1e-9:
		thresholds.append(round(value, 2))
		value += AUTO_STEP
	return thresholds


def median_track_length(segments):
	lengths = sorted(end - start for start, end in segments)
	return lengths[len(lengths) // 2] if lengths else 0.0


def pick_auto_min_silence(silences, durationSeconds, minTrackSeconds, expectedTracks=0):
	"""The minimum silence (seconds) that splits the file best, tried against the gaps already found.

	With a known number of tracks the value that gives that many tracks wins (the
	largest such value, or the closest count when none matches). Otherwise the value
	whose track count stays the same over the widest range of values wins, and a
	range that gives implausibly long or short tracks counts for less.
	"""
	if expectedTracks > 0:
		results = [
			(threshold, len(compute_segments(filter_gaps(silences, threshold), durationSeconds, minTrackSeconds)))
			for threshold in candidate_thresholds(PROBE_MIN_SILENCE)
		]
		return min(results, key=lambda item: (abs(item[1] - expectedTracks), -item[0]))[0]

	plateaus = []
	for threshold in candidate_thresholds(AUTO_FLOOR_SILENCE):
		segments = compute_segments(filter_gaps(silences, threshold), durationSeconds, minTrackSeconds)
		if plateaus and plateaus[-1]["count"] == len(segments):
			plateaus[-1]["high"] = threshold
			continue
		plateaus.append({"count": len(segments), "low": threshold, "high": threshold, "segments": segments})

	bestScore = -1.0
	bestLow = None
	for plateau in reversed(plateaus):
		if plateau["count"] < 2:
			continue
		width = math.log((plateau["high"] + AUTO_STEP) / plateau["low"])
		median = median_track_length(plateau["segments"])
		typicalMin, typicalMax = AUTO_TYPICAL_TRACK_SECONDS
		weight = 1.0 if typicalMin <= median <= typicalMax else AUTO_ATYPICAL_WEIGHT
		if width * weight > bestScore:
			bestScore = width * weight
			bestLow = plateau["low"]
	return bestLow if bestLow is not None else PROBE_MIN_SILENCE


def decode_cue_bytes(rawBytes, ansiCodePage="cp1252"):
	"""Text of a CUE sheet. UTF-8 (with or without a byte order mark) is tried first; an older sheet saved in the system code page (for example Thai Windows) is decoded with that page, and anything else falls back to latin-1, which never fails."""
	if rawBytes.startswith((b"\xff\xfe", b"\xfe\xff")):
		return rawBytes.decode("utf-16")
	for encoding in ("utf-8-sig", ansiCodePage):
		try:
			return rawBytes.decode(encoding)
		except (UnicodeDecodeError, LookupError):
			continue
	return rawBytes.decode("latin-1")


def _unquote(text):
	text = text.strip()
	if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
		return text[1:-1]
	if text.startswith('"'):
		closing = text.find('"', 1)
		return text[1:closing] if closing > 0 else text[1:]
	return text.split(" ")[0] if text else ""


def cue_time_to_seconds(minutes, seconds, frames):
	if seconds >= 60 or frames >= CUE_FRAMES_PER_SECOND:
		return None
	return minutes * 60 + seconds + frames / CUE_FRAMES_PER_SECOND


def parse_cue_sheet(text):
	"""Tracks of a CUE sheet as {"audioFiles": n, "tracks": [{"number", "title", "startSec"}]}. The start of a track is its INDEX 01. Tracks without a usable INDEX 01 are left out, and only tracks of the first FILE are listed because the times of a later FILE restart from zero."""
	audioFiles = 0
	tracks = []
	current = None
	inFirstFile = False
	for rawLine in str(text).splitlines():
		match = CUE_LINE_PATTERN.match(rawLine)
		if not match:
			continue
		command = match.group(1).upper()
		argument = match.group(2)
		if command == "FILE":
			audioFiles += 1
			inFirstFile = audioFiles == 1
			current = None
		elif command == "TRACK":
			trackMatch = CUE_TRACK_PATTERN.match(argument)
			isAudio = argument.upper().endswith("AUDIO")
			if inFirstFile and trackMatch and isAudio:
				current = {"number": int(trackMatch.group(1)), "title": "", "startSec": None}
				tracks.append(current)
			else:
				current = None
		elif command == "TITLE" and current is not None:
			current["title"] = _unquote(argument)
		elif command == "INDEX" and current is not None:
			indexMatch = CUE_INDEX_PATTERN.match(argument)
			if indexMatch and int(indexMatch.group(1)) == 1:
				current["startSec"] = cue_time_to_seconds(int(indexMatch.group(2)), int(indexMatch.group(3)), int(indexMatch.group(4)))
	usable = [track for track in tracks if track["startSec"] is not None]
	return {"audioFiles": audioFiles, "tracks": usable}


def build_cue_segments(tracks, durationSeconds):
	"""(segments, droppedCount): exact (start, end, title) cuts for the tracks of a CUE sheet. A track ends where the next one starts and the last one ends with the file. Tracks that start out of order or after the end of the file are dropped instead of producing an empty or negative cut."""
	ordered = []
	lastStart = -1.0
	dropped = 0
	for track in tracks:
		start = track["startSec"]
		if start <= lastStart or start >= durationSeconds - 0.05:
			dropped += 1
			continue
		ordered.append(track)
		lastStart = start
	segments = []
	for index, track in enumerate(ordered):
		end = ordered[index + 1]["startSec"] if index + 1 < len(ordered) else durationSeconds
		segments.append((track["startSec"], end, track["title"].strip()))
	return segments, dropped


def get_ansi_code_page():
	try:
		return f"cp{ctypes.windll.kernel32.GetACP()}"
	except (AttributeError, OSError):
		return "cp1252"


def safe_title(title, fallback):
	cleaned = "".join("_" if (char in INVALID_FILENAME_CHARS or ord(char) < 32) else char for char in title).strip().rstrip(".")
	return cleaned[:120] if cleaned else fallback


def build_silence_command(ffmpegPath, filePath, levelDb, minSilenceSeconds):
	return [
		ffmpegPath, "-hide_banner", "-i", filePath, "-vn",
		"-af", f"silencedetect=noise={levelDb}dB:d={minSilenceSeconds:g}",
		"-progress", "pipe:1", "-nostats", "-f", "null", "-",
	]


class SplitAlbumByTracksDialog(wx.Dialog):
	"""Finds the silent gaps in one long audio file (for example a whole album) and splits it into tracks there."""
	def __init__(self, parent, selected_file, libs_path):
		super().__init__(parent, title=_("Split Album By Tracks"))
		if not selected_file:
			raise ValueError("No file was selected.")
		self.selected_file = selected_file
		self.libs_path = libs_path
		self.config_path = get_config_path()
		self.duration_seconds = 0
		self.silences = []
		self.coarse_key = None
		self.coarse_silences = []
		self.refine_cache = {}
		self.gap_note = ""
		self.refocus_timer = None
		self.segments = []
		self.is_busy = False
		self.is_closed = False
		self.cancel_event = threading.Event()
		self.ffmpeg_process = None
		self.preview_dir = ""
		self.updating_title_text = False
		self.cue_active = False
		self.init_ui()
		self.load_settings()
		self.SetTitle(_("Split Album By Tracks: {}").format(os.path.basename(self.selected_file)))
		self.Bind(wx.EVT_CLOSE, self.on_close)
		self.start_analysis()

	def post(self, handler, *args):
		wx.CallAfter(self.run_if_open, handler, *args)

	def run_if_open(self, handler, *args):
		if self.is_closed:
			return
		handler(*args)

	def register_process(self, process):
		self.ffmpeg_process = process

	def init_ui(self):
		main_sizer = wx.BoxSizer(wx.VERTICAL)

		file_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Selected File"))
		self.file_label = wx.StaticText(self, label=os.path.basename(self.selected_file))
		file_sizer.Add(self.file_label, 0, wx.EXPAND | wx.ALL, 5)
		self.duration_label = wx.StaticText(self, label=_("Duration: Calculating..."))
		file_sizer.Add(self.duration_label, 0, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(file_sizer, 0, wx.EXPAND | wx.ALL, 5)

		settings_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Detection"))
		settingsBox = settings_sizer.GetStaticBox()
		grid = wx.FlexGridSizer(cols=2, vgap=5, hgap=8)
		grid.Add(wx.StaticText(settingsBox, label=_("&Minimum silence between tracks (seconds, 0 = automatic):")), 0, wx.ALIGN_CENTER_VERTICAL)
		# Plain edit fields: a spin box that holds decimals does not pass its label on to NVDA.
		self.min_silence_ctrl = wx.TextCtrl(settingsBox, value=f"{DEFAULT_MIN_SILENCE:g}")
		grid.Add(self.min_silence_ctrl, 0)
		grid.Add(wx.StaticText(settingsBox, label=_("E&xpected number of tracks (0 = not known, used in automatic mode):")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.expected_tracks_ctrl = wx.SpinCtrl(settingsBox, min=0, max=500, initial=DEFAULT_EXPECTED_TRACKS)
		grid.Add(self.expected_tracks_ctrl, 0)
		grid.Add(wx.StaticText(settingsBox, label=_("Silence &level (dB, higher finds quieter gaps less):")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.level_ctrl = wx.SpinCtrl(settingsBox, min=-90, max=-10, initial=DEFAULT_LEVEL_DB)
		grid.Add(self.level_ctrl, 0)
		grid.Add(wx.StaticText(settingsBox, label=_("Level of complete silence at the e&nd of a track (dB, lower keeps more of a fade out):")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.end_level_ctrl = wx.SpinCtrl(settingsBox, min=-100, max=-20, initial=DEFAULT_END_LEVEL_DB)
		grid.Add(self.end_level_ctrl, 0)
		grid.Add(wx.StaticText(settingsBox, label=_("Silence &before each track (seconds, 0 = none):")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.lead_ctrl = wx.TextCtrl(settingsBox, value=f"{DEFAULT_LEAD_SILENCE:g}")
		grid.Add(self.lead_ctrl, 0)
		grid.Add(wx.StaticText(settingsBox, label=_("Silence &after each track (seconds, 0 = none):")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.tail_ctrl = wx.TextCtrl(settingsBox, value=f"{DEFAULT_TAIL_SILENCE:g}")
		grid.Add(self.tail_ctrl, 0)
		grid.Add(wx.StaticText(settingsBox, label=_("Shortest &track (seconds):")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.min_track_ctrl = wx.SpinCtrl(settingsBox, min=1, max=900, initial=DEFAULT_MIN_TRACK)
		grid.Add(self.min_track_ctrl, 0)
		settings_sizer.Add(grid, 0, wx.ALL, 5)
		self.analyze_btn = wx.Button(settingsBox, label=_("&Analyze again"))
		self.analyze_btn.Bind(wx.EVT_BUTTON, self.on_analyze)
		self.cue_btn = wx.Button(settingsBox, label=_("Import &CUE sheet..."))
		self.cue_btn.Bind(wx.EVT_BUTTON, self.on_import_cue)
		analyzeRow = wx.BoxSizer(wx.HORIZONTAL)
		analyzeRow.Add(self.analyze_btn, 0, wx.ALL, 5)
		analyzeRow.Add(self.cue_btn, 0, wx.ALL, 5)
		settings_sizer.Add(analyzeRow, 0)
		main_sizer.Add(settings_sizer, 0, wx.EXPAND | wx.ALL, 5)

		tracks_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Tracks found"))
		tracksBox = tracks_sizer.GetStaticBox()
		self.track_list = wx.ListBox(tracksBox, style=wx.LB_SINGLE)
		self.track_list.SetName(_("Tracks"))
		self.track_list.Bind(wx.EVT_LISTBOX, self.on_track_selected)
		tracks_sizer.Add(self.track_list, 1, wx.EXPAND | wx.ALL, 5)
		title_sizer = wx.BoxSizer(wx.HORIZONTAL)
		title_sizer.Add(wx.StaticText(tracksBox, label=_("Track &title:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.title_ctrl = wx.TextCtrl(tracksBox)
		self.title_ctrl.Bind(wx.EVT_TEXT, self.on_title_changed)
		title_sizer.Add(self.title_ctrl, 1, wx.EXPAND | wx.ALL, 5)
		tracks_sizer.Add(title_sizer, 0, wx.EXPAND)
		main_sizer.Add(tracks_sizer, 1, wx.EXPAND | wx.ALL, 5)

		self.progress_bar = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
		main_sizer.Add(self.progress_bar, 0, wx.EXPAND | wx.ALL, 5)
		self.status_label = wx.StaticText(self, label="")
		main_sizer.Add(self.status_label, 0, wx.EXPAND | wx.ALL, 5)

		btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
		self.preview_btn = wx.Button(self, label=_("&Preview 15 seconds"))
		self.preview_btn.Bind(wx.EVT_BUTTON, self.on_preview)
		btn_sizer.Add(self.preview_btn, 0, wx.ALL, 5)
		self.split_btn = wx.Button(self, wx.ID_OK, label=_("&Split"))
		self.split_btn.Bind(wx.EVT_BUTTON, self.on_split)
		btn_sizer.Add(self.split_btn, 0, wx.ALL, 5)
		self.cancel_btn = wx.Button(self, wx.ID_CANCEL, label=_("Close"))
		self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
		btn_sizer.Add(self.cancel_btn, 0, wx.ALL, 5)
		main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

		self.SetSizer(main_sizer)
		self.Fit()
		self.SetSize((620, 640))
		self.update_buttons()

	def load_settings(self):
		config_data = load_config(self.config_path)
		try:
			self.min_silence_ctrl.SetValue(f"{float(config_data.get('SplitSilenceGap', DEFAULT_MIN_SILENCE)):g}")
			self.expected_tracks_ctrl.SetValue(int(config_data.get("SplitExpectedTracks", DEFAULT_EXPECTED_TRACKS)))
			self.lead_ctrl.SetValue(f"{float(config_data.get('SplitLeadSilence', DEFAULT_LEAD_SILENCE)):g}")
			self.tail_ctrl.SetValue(f"{float(config_data.get('SplitTailSilence', DEFAULT_TAIL_SILENCE)):g}")
			self.level_ctrl.SetValue(int(config_data.get("SplitSilenceLevelDb", DEFAULT_LEVEL_DB)))
			self.end_level_ctrl.SetValue(int(config_data.get("SplitSilenceEndLevelDb", DEFAULT_END_LEVEL_DB)))
			self.min_track_ctrl.SetValue(int(config_data.get("SplitSilenceMinTrack", DEFAULT_MIN_TRACK)))
		except (TypeError, ValueError):
			log.error("xTrack: the saved Split Album By Tracks settings are not valid; using the defaults")

	@staticmethod
	def read_seconds(control, default, low, high):
		try:
			value = float(control.GetValue().strip().replace(",", "."))
		except ValueError:
			return default
		if value != value:
			return default
		return min(max(value, low), high)

	def get_gap_setting(self):
		return self.read_seconds(self.min_silence_ctrl, DEFAULT_MIN_SILENCE, 0.0, 60.0)

	def get_lead_setting(self):
		return self.read_seconds(self.lead_ctrl, DEFAULT_LEAD_SILENCE, 0.0, 5.0)

	def get_tail_setting(self):
		return self.read_seconds(self.tail_ctrl, DEFAULT_TAIL_SILENCE, 0.0, 5.0)

	def normalize_seconds_fields(self):
		"""Show the value that is really used, so a mistyped or out-of-range entry is not silently ignored."""
		for control, value in (
			(self.min_silence_ctrl, self.get_gap_setting()),
			(self.lead_ctrl, self.get_lead_setting()),
			(self.tail_ctrl, self.get_tail_setting()),
		):
			text = f"{value:g}"
			if control.GetValue() != text:
				control.SetValue(text)

	def save_settings(self):
		config_data = load_config(self.config_path)
		config_data["SplitSilenceGap"] = self.get_gap_setting()
		config_data["SplitExpectedTracks"] = self.expected_tracks_ctrl.GetValue()
		config_data["SplitLeadSilence"] = self.get_lead_setting()
		config_data["SplitTailSilence"] = self.get_tail_setting()
		config_data["SplitSilenceLevelDb"] = self.level_ctrl.GetValue()
		config_data["SplitSilenceEndLevelDb"] = self.end_level_ctrl.GetValue()
		config_data["SplitSilenceMinTrack"] = self.min_track_ctrl.GetValue()
		save_config(self.config_path, config_data)

	def update_buttons(self):
		hasTracks = len(self.segments) > 1
		self.analyze_btn.Enable(not self.is_busy)
		self.cue_btn.Enable(not self.is_busy)
		self.preview_btn.Enable(hasTracks and not self.is_busy)
		self.split_btn.Enable(hasTracks and not self.is_busy)

	def set_busy(self, isBusy):
		self.is_busy = isBusy
		self.update_buttons()

	# ---- analysis
	def on_analyze(self, event):
		self.start_analysis()

	def start_analysis(self):
		if self.is_busy:
			return
		self.cue_active = False
		ffmpegPath = os.path.join(self.libs_path, "ffmpeg.exe")
		if not os.path.exists(ffmpegPath):
			ui.message(_("ffmpeg.exe not found"))
			return
		levelDb = self.level_ctrl.GetValue()
		self.normalize_seconds_fields()
		gapSetting = self.get_gap_setting()
		minTrack = self.min_track_ctrl.GetValue()
		endLevel = self.end_level_ctrl.GetValue()
		expectedTracks = self.expected_tracks_ctrl.GetValue()
		self.cancel_event.clear()
		self.set_busy(True)
		self.progress_bar.SetValue(0)
		self.status_label.SetLabel(_("Analyzing..."))
		ui.message(_("Analyzing the file for silent gaps"))
		threading.Thread(target=self.run_analysis, args=(ffmpegPath, levelDb, gapSetting, endLevel, minTrack, expectedTracks), daemon=True).start()

	def run_analysis(self, ffmpegPath, levelDb, gapSetting, endLevel, minTrack, expectedTracks):
		durationSeconds = self.duration_seconds
		if durationSeconds <= 0:
			durationSeconds, durationLabel = get_file_duration(self.libs_path, self.selected_file)
			if durationSeconds <= 0:
				self.post(self.on_analysis_failed, _("the duration of this file could not be read"))
				return
			self.duration_seconds = durationSeconds
			self.post(self.duration_label.SetLabel, _("Duration: {}").format(durationLabel))
		if self.coarse_key == levelDb:
			# Only the gap length or the fade out level changed: every silence of the file is already known.
			silences = list(self.coarse_silences)
		else:
			willRefine = endLevel < levelDb
			command = build_silence_command(ffmpegPath, self.selected_file, levelDb, PROBE_MIN_SILENCE)
			try:
				exitCode, stderrLines = runFfmpeg(
					command, os.path.dirname(self.selected_file), durationSeconds,
					lambda percent: self.post(self.progress_bar.SetValue, int(percent * 0.7) if willRefine else percent),
					self.register_process, stderrLineLimit=ANALYSIS_STDERR_LINES,
				)
			except OSError as error:
				log.error("xTrack: could not run ffmpeg for the silence analysis: %s", error)
				self.post(self.on_analysis_failed, _("ffmpeg could not be started"))
				return
			if self.cancel_event.is_set():
				return
			if exitCode != 0:
				log.error("xTrack: silence analysis failed (exit code %s): %s", exitCode, " | ".join(line.strip() for line in stderrLines[-10:]))
				self.post(self.on_analysis_failed, summarizeFfmpegError(stderrLines))
				return
			silences = parse_silences(stderrLines, durationSeconds)
			self.coarse_silences = list(silences)
			self.coarse_key = levelDb
			self.refine_cache = {}
		autoNote = ""
		if gapSetting > 0:
			minSilence = max(gapSetting, PROBE_MIN_SILENCE)
		else:
			minSilence = pick_auto_min_silence(silences, durationSeconds, minTrack, expectedTracks)
			autoNote = _("automatic gap {} seconds").format(f"{minSilence:g}")
			log.info("xTrack: automatic gap for the album split is %s seconds (%d silences known)", minSilence, len(silences))
		gaps = filter_gaps(silences, minSilence)
		gaps = self.refine_fade_outs(ffmpegPath, gaps, durationSeconds, levelDb, endLevel)
		if self.cancel_event.is_set():
			return
		self.post(self.on_analysis_done, gaps, autoNote, minTrack, expectedTracks)

	def detect_complete_silence(self, ffmpegPath, gap, endLevel):
		"""Silent stretches, in file time, inside one gap when it is measured with the stricter level; None when ffmpeg failed."""
		offset = max(0.0, gap[0] - TAIL_LOOKBACK_SECONDS)
		length = gap[1] - offset
		command = [
			ffmpegPath, "-hide_banner", "-ss", f"{offset:.3f}", "-t", f"{length:.3f}", "-i", self.selected_file, "-vn",
			"-af", f"silencedetect=noise={endLevel}dB:d={STRICT_MIN_SILENCE:g}",
			"-progress", "pipe:1", "-nostats", "-f", "null", "-",
		]
		try:
			exitCode, stderrLines = runFfmpeg(
				command, os.path.dirname(self.selected_file), length, lambda percent: None, self.register_process,
				stderrLineLimit=ANALYSIS_STDERR_LINES,
			)
		except OSError as error:
			log.error("xTrack: could not run ffmpeg for the end of a track: %s", error)
			return None
		if exitCode != 0 or self.cancel_event.is_set():
			return None
		return [(offset + start, offset + end) for start, end in parse_silences(stderrLines, length)]

	def refine_fade_outs(self, ffmpegPath, silences, durationSeconds, levelDb, endLevel):
		"""Move the start of every gap between tracks from "very quiet" to "completely silent", so a track that fades out keeps its fade. The silence at the very start of the file is not touched."""
		if endLevel >= levelDb:
			return silences
		gaps = sorted(silences)
		toCheck = [gap for gap in gaps if gap[0] > 0.05]
		if not toCheck:
			return silences
		self.post(self.status_label.SetLabel, _("Checking fade outs..."))
		refined = []
		done = 0
		for gap in gaps:
			if self.cancel_event.is_set():
				return silences
			if gap[0] <= 0.05:
				refined.append(gap)
				continue
			cacheKey = (round(gap[0], 3), round(gap[1], 3), endLevel)
			cached = self.refine_cache.get(cacheKey, _NOT_CACHED)
			if cached is not _NOT_CACHED:
				if cached is not None:
					refined.append(cached)
				done += 1
				continue
			strictGaps = self.detect_complete_silence(ffmpegPath, gap, endLevel)
			done += 1
			self.post(self.progress_bar.SetValue, 70 + int(30 * done / len(toCheck)))
			if strictGaps is None:
				refined.append(gap)
				continue
			newGap = apply_complete_silence(gap, strictGaps, gap[1] >= durationSeconds - 0.05)
			self.refine_cache[cacheKey] = newGap
			if newGap is not None:
				refined.append(newGap)
		return refined

	def on_analysis_done(self, silences, autoNote, minTrack, expectedTracks):
		self.silences = silences
		self.progress_bar.SetValue(100)
		self.set_busy(False)
		leadSeconds = self.get_lead_setting()
		leadIn = leadSeconds if leadSeconds > 0 else LEAD_IN_SECONDS
		tailSeconds = self.get_tail_setting()
		tailOut = tailSeconds if tailSeconds > 0 else TAIL_SECONDS
		tracks = compute_segments_with_onsets(silences, self.duration_seconds, minTrack, leadIn, tailOut)
		if autoNote and expectedTracks and len(tracks) != expectedTracks:
			autoNote += ", " + _("expected {}").format(expectedTracks)
		self.gap_note = autoNote
		self.show_segments(
			[(track[0], track[1]) for track in tracks],
			onsets=[track[2] for track in tracks], offsets=[track[3] for track in tracks],
		)

	def on_analysis_failed(self, reason):
		self.set_busy(False)
		self.status_label.SetLabel(_("Analysis failed"))
		tones.beep(300, 300)
		ui.message(limitSpokenText(_("Could not analyze the file: {}").format(reason)))

	def on_import_cue(self, event):
		if self.is_busy:
			return
		if self.duration_seconds <= 0:
			ui.message(_("The length of this file is not known yet, so a CUE sheet cannot be checked against it."))
			return
		startDir = os.path.dirname(self.selected_file)
		cueGuess = os.path.splitext(os.path.basename(self.selected_file))[0] + ".cue"
		with wx.FileDialog(self, _("Select a CUE sheet"), defaultDir=startDir,
							defaultFile=cueGuess if os.path.exists(os.path.join(startDir, cueGuess)) else "",
							wildcard=_("CUE sheets (*.cue)|*.cue"), style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
			if dialog.ShowModal() != wx.ID_OK:
				return
			cuePath = dialog.GetPath()
		try:
			with open(cuePath, "rb") as cueFile:
				rawBytes = cueFile.read(CUE_MAX_BYTES + 1)
		except OSError as error:
			log.error("xTrack: could not read the CUE sheet %s: %s", cuePath, error)
			ui.message(_("The CUE sheet could not be read."))
			return
		if len(rawBytes) > CUE_MAX_BYTES:
			ui.message(_("This file is too large to be a CUE sheet."))
			return
		sheet = parse_cue_sheet(decode_cue_bytes(rawBytes, get_ansi_code_page()))
		if sheet["audioFiles"] > 1:
			ui.message(_("This CUE sheet refers to several audio files. Only sheets for one album file are supported."))
			return
		segments, dropped = build_cue_segments(sheet["tracks"], self.duration_seconds)
		if len(segments) < 2:
			ui.message(_("No usable tracks were found in this CUE sheet for this file."))
			return
		self.cue_active = True
		self.show_segments([(start, end) for start, end, _title in segments], [title for _start, _end, title in segments], dropped)

	def show_segments(self, segments, titles=None, droppedCount=0, onsets=None, offsets=None):
		# Without a known onset or offset (a CUE sheet) the sound is taken to run right up to both cuts.
		self.segments = [
			{
				"start": start, "end": end, "onset": onsets[number - 1] if onsets else start,
				"offset": offsets[number - 1] if offsets else end,
				"title": (titles[number - 1] if titles and titles[number - 1] else _("Track {:02d}").format(number)),
			}
			for number, (start, end) in enumerate(segments, 1)
		]
		self.refresh_track_list()
		self.update_buttons()
		if len(self.segments) < 2:
			self.segments = []
			self.refresh_track_list()
			self.update_buttons()
			self.status_label.SetLabel(_("No silent gaps found"))
			ui.message(_("No silent gaps were found in this file. Try a higher silence level, or set the minimum silence to 0.5."))
			return
		statusText = _("{} tracks found").format(len(self.segments))
		if self.gap_note and not self.cue_active:
			statusText += " (" + self.gap_note + ")"
		self.status_label.SetLabel(statusText)
		tones.beep(1000, 200)
		if self.cue_active:
			message = _("{} tracks loaded from the CUE sheet. Review them in the list, then press Split.").format(len(self.segments))
			if droppedCount:
				message += " " + _("{} entries did not fit this file and were skipped.").format(droppedCount)
			ui.message(limitSpokenText(message))
		else:
			message = _("{} tracks found. Review them in the list, then press Split.").format(len(self.segments))
			if self.gap_note:
				message = _("{} tracks found, {}. Review them in the list, then press Split.").format(len(self.segments), self.gap_note)
			ui.message(limitSpokenText(message))
		self.track_list.SetSelection(0)
		self.on_track_selected(None)
		self.track_list.SetFocus()

	def describe_segment(self, number, segment):
		return _("{number}. {title}: {start} to {end}, length {length}").format(
			number=number, title=segment["title"], start=formatClock(segment["start"]),
			end=formatClock(segment["end"]), length=formatClock(segment["end"] - segment["start"]),
		)

	def refresh_track_list(self):
		selection = self.track_list.GetSelection()
		self.track_list.Set([self.describe_segment(number, segment) for number, segment in enumerate(self.segments, 1)])
		if self.segments and selection != wx.NOT_FOUND and selection < len(self.segments):
			self.track_list.SetSelection(selection)

	def on_track_selected(self, event):
		index = self.track_list.GetSelection()
		if index == wx.NOT_FOUND or index >= len(self.segments):
			return
		self.updating_title_text = True
		self.title_ctrl.SetValue(self.segments[index]["title"])
		self.updating_title_text = False

	def on_title_changed(self, event):
		if self.updating_title_text:
			return
		index = self.track_list.GetSelection()
		if index == wx.NOT_FOUND or index >= len(self.segments):
			return
		self.segments[index]["title"] = self.title_ctrl.GetValue()
		self.track_list.SetString(index, self.describe_segment(index + 1, self.segments[index]))

	# ---- preview
	def on_preview(self, event):
		index = self.track_list.GetSelection()
		if self.is_busy or index == wx.NOT_FOUND or index >= len(self.segments):
			ui.message(_("Select a track in the list first."))
			return
		ffmpegPath = os.path.join(self.libs_path, "ffmpeg.exe")
		segment = self.segments[index]
		length = min(PREVIEW_SECONDS, segment["end"] - segment["start"])
		padSeconds = lead_padding(segment["start"], segment["onset"], self.get_lead_setting())
		self.cleanup_preview_dir()
		self.preview_dir = tempfile.mkdtemp(prefix="xtrack_silence_preview_")
		previewPath = os.path.join(self.preview_dir, "preview.wav")
		command = [
			ffmpegPath, "-hide_banner", "-y", "-ss", f"{segment['start']:.3f}", "-i", self.selected_file,
			"-t", f"{length + padSeconds:.3f}", "-vn",
		]
		if padSeconds > 0:
			command += ["-af", f"adelay=delays={int(round(padSeconds * 1000))}:all=1"]
		command += ["-c:a", "pcm_s16le", previewPath]
		self.cancel_event.clear()
		self.set_busy(True)
		self.status_label.SetLabel(_("Preparing preview..."))
		threading.Thread(target=self.run_preview, args=(command, previewPath, index), daemon=True).start()

	def run_preview(self, command, previewPath, index):
		try:
			exitCode, stderrLines = runFfmpeg(command, self.preview_dir, 0, lambda percent: None, self.register_process)
		except OSError as error:
			log.error("xTrack: could not run ffmpeg for the preview: %s", error)
			self.post(self.on_preview_done, None, _("ffmpeg could not be started"), index)
			return
		if self.cancel_event.is_set():
			return
		if exitCode != 0 or not os.path.exists(previewPath):
			self.post(self.on_preview_done, None, summarizeFfmpegError(stderrLines), index)
			return
		self.post(self.on_preview_done, previewPath, "", index)

	def on_preview_done(self, previewPath, reason, index):
		self.set_busy(False)
		self.status_label.SetLabel("")
		if not previewPath:
			tones.beep(300, 300)
			ui.message(limitSpokenText(_("Preview failed: {}").format(reason)))
			return
		try:
			nvwave.playWaveFile(previewPath, asynchronous=True)
		except (OSError, TypeError, AttributeError) as error:
			log.warning("xTrack: NVDA could not play the preview, opening it in the default player: %s", error)
			try:
				os.startfile(previewPath)
			except OSError as openError:
				log.error("xTrack: could not open the preview: %s", openError)
				ui.message(_("The preview was created but could not be played."))
				return
			# The player takes the focus a moment after it starts, so the track list gets it back later.
			self.refocus_timer = core.callLater(REFOCUS_DELAY_MS, self.restore_track_focus)
		self.track_list.SetFocus()
		ui.message(_("Playing {seconds} seconds of track {number}").format(seconds=PREVIEW_SECONDS, number=index + 1))

	def restore_track_focus(self):
		self.refocus_timer = None
		if self.is_closed:
			return
		try:
			user32 = ctypes.windll.user32
			hwnd = self.GetHandle()
			user32.ShowWindow(hwnd, SW_RESTORE)
			# A harmless Alt tap lets SetForegroundWindow succeed from a process that does not hold the input focus.
			user32.keybd_event(VK_MENU, 0, 0, 0)
			user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
			user32.SetForegroundWindow(hwnd)
		except (OSError, AttributeError) as error:
			log.error("xTrack: could not bring the split dialog back to the front: %s", error)
		self.track_list.SetFocus()

	def cleanup_preview_dir(self):
		if self.preview_dir:
			shutil.rmtree(self.preview_dir, ignore_errors=True)
		self.preview_dir = ""

	# ---- split
	def on_split(self, event):
		if self.is_busy or len(self.segments) < 2:
			return
		ffmpegPath = os.path.join(self.libs_path, "ffmpeg.exe")
		if not os.path.exists(ffmpegPath):
			ui.message(_("ffmpeg.exe not found"))
			return
		baseName = os.path.splitext(os.path.basename(self.selected_file))[0]
		outputDir = os.path.join(os.path.dirname(self.selected_file), safe_title(baseName, "album") + _(""))
		counter = 1
		uniqueDir = outputDir
		while os.path.exists(uniqueDir):
			uniqueDir = f"{outputDir} ({counter})"
			counter += 1
		try:
			os.makedirs(uniqueDir)
		except OSError as error:
			log.error("xTrack: could not create %s: %s", uniqueDir, error)
			ui.message(_("The output folder could not be created."))
			return
		self.normalize_seconds_fields()
		leadSeconds = self.get_lead_setting()
		tailSeconds = self.get_tail_setting()
		jobs = [
			dict(
				segment, leadPad=lead_padding(segment["start"], segment["onset"], leadSeconds),
				tailPad=tail_padding(segment["end"], segment["offset"], tailSeconds),
			)
			for segment in self.segments
		]
		self.save_settings()
		self.cancel_event.clear()
		self.set_busy(True)
		self.progress_bar.SetValue(0)
		self.status_label.SetLabel(_("Starting split operation..."))
		threading.Thread(target=self.perform_split, args=(ffmpegPath, jobs, uniqueDir), daemon=True).start()

	def perform_split(self, ffmpegPath, jobs, outputDir):
		extension = os.path.splitext(self.selected_file)[1]
		total = len(jobs)
		created = 0
		failed = []
		if any(job["leadPad"] > 0 or job["tailPad"] > 0 for job in jobs) and extension.lower() not in PAD_ENCODER_ARGS:
			log.warning("xTrack: silence around tracks is not supported for %s files", extension)
			self.post(ui.message, _("Silence around tracks is not supported for {} files; the tracks are cut without it.").format(extension))
		for index, job in enumerate(jobs):
			if self.cancel_event.is_set():
				return
			number = index + 1
			title = safe_title(job["title"], _("Track {:02d}").format(number))
			finalPath = os.path.join(outputDir, f"{number:02d} - {title}{extension}")
			partPath = partPathFor(finalPath)
			padArgs = pad_arguments(extension, job["leadPad"], job["tailPad"])
			segmentLength = job["end"] - job["start"]
			length = segmentLength + (job["leadPad"] + job["tailPad"] if padArgs else 0.0)
			self.post(self.status_label.SetLabel, _("Processing track {} of {}").format(number, total))
			command = [ffmpegPath, "-hide_banner", "-y", "-ss", f"{job['start']:.3f}"]
			if padArgs:
				# The input has to end where the track ends, or the silence would be added after the end of the album.
				command += ["-t", f"{segmentLength:.3f}"]
			command += [
				"-i", self.selected_file,
				"-t", f"{length:.3f}", "-c", "copy", "-map_metadata", "0",
				"-metadata", f"title={job['title'].strip() or title}", "-metadata", f"track={number}/{total}",
			]
			if extension.lower() == ".mp3":
				command += ["-id3v2_version", "3"]
			elif extension.lower() == ".flac":
				# Copying a FLAC stream keeps the header of the whole album, so players
				# would show the full album length for every track. Encoding FLAC again
				# is lossless and writes the correct length (the cover picture is still copied).
				command += ["-c:a", "flac"]
			command += padArgs
			command += ["-progress", "pipe:1", "-nostats", partPath]

			def reportProgress(percent, index=index):
				self.post(self.progress_bar.SetValue, int(((index + percent / 100) / total) * 100))

			try:
				exitCode, stderrLines = runFfmpeg(command, outputDir, length, reportProgress, self.register_process)
			except OSError as error:
				log.error("xTrack: could not run ffmpeg for track %s: %s", number, error)
				removeQuietly(partPath)
				failed.append(number)
				continue
			if self.cancel_event.is_set():
				removeQuietly(partPath)
				return
			if exitCode != 0:
				log.error("xTrack: track %s failed (exit code %s): %s", number, exitCode, " | ".join(line.strip() for line in stderrLines))
				removeQuietly(partPath)
				failed.append(number)
				continue
			try:
				os.replace(partPath, finalPath)
			except OSError as error:
				log.error("xTrack: could not move %s to %s: %s", partPath, finalPath, error)
				removeQuietly(partPath)
				failed.append(number)
				continue
			created += 1
		self.post(self.on_split_finished, created, failed, outputDir)

	def on_split_finished(self, created, failed, outputDir):
		self.set_busy(False)
		self.progress_bar.SetValue(100)
		if created == 0:
			self.status_label.SetLabel(_("Split failed!"))
			tones.beep(300, 300)
			ui.message(_("Failed to split the audio file"))
			return
		tones.beep(1000, 300)
		if failed:
			self.status_label.SetLabel(_("Split finished with errors"))
			ui.message(limitSpokenText(_("{count} tracks created. Track {failed} could not be created.").format(
				count=created, failed=", ".join(f"{number:02d}" for number in failed))))
			return
		self.status_label.SetLabel(_("Split complete! {} tracks created").format(created))
		ui.message(limitSpokenText(_("Album split into {count} tracks, saved in the folder {folder}").format(
			count=created, folder=os.path.basename(outputDir))))
		self.EndModal(wx.ID_OK)

	def stop_work(self):
		self.is_closed = True
		self.cancel_event.set()
		if self.refocus_timer is not None:
			self.refocus_timer.Stop()
			self.refocus_timer = None
		process = self.ffmpeg_process
		if process and process.poll() is None:
			try:
				process.terminate()
			except OSError as error:
				log.debugWarning("xTrack: could not stop ffmpeg: %s", error)
		self.cleanup_preview_dir()

	def on_cancel(self, event):
		self.stop_work()
		self.EndModal(wx.ID_CANCEL)

	def on_close(self, event):
		self.stop_work()
		event.Skip()
