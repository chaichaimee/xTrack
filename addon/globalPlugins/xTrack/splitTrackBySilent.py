# splitTrackBySilent.py

import wx
import ctypes
import os
import shutil
import tempfile
import threading
import time
import tones
import ui
from logHandler import log
import addonHandler
from .xTrackCore import get_config_path, load_config, save_config, get_file_duration, get_unique_filename
from .ffmpegSupport import (
	limitSpokenText, summarizeFfmpegError, removeQuietly, partPathFor, runFfmpeg, formatClock,
)
from .splitAlbumByTracks import (
	parse_silences, build_silence_command, safe_title, ANALYSIS_STDERR_LINES,
	lead_padding, tail_padding, pad_arguments, PAD_ENCODER_ARGS,
	DEFAULT_LEVEL_DB, DEFAULT_END_LEVEL_DB, DEFAULT_LEAD_SILENCE, DEFAULT_TAIL_SILENCE,
)

addonHandler.initTranslation()

PREVIEW_SECONDS = 8
DEFAULT_MIN_SILENCE = 0.3
# How much of the quiet stretch before the complete silence the second pass looks at.
TAIL_LOOKBACK_SECONDS = 2.0
# Less than this on either side counts as "nothing to trim".
NOTHING_TO_TRIM_SECONDS = 0.05
MIN_KEPT_SECONDS = 0.5
# A trimmed copy whose length is further than this from the planned length is never allowed to replace an original.
DURATION_TOLERANCE_SECONDS = 2.5
PLACE_RETRIES = 10
PLACE_RETRY_DELAY_SECONDS = 0.3
FOF_SILENT = 0x4
FOF_NOCONFIRMATION = 0x10
FOF_ALLOWUNDO = 0x40
FOF_NOERRORUI = 0x400
FO_DELETE = 3


def compute_trim(silences, durationSeconds, keepStart, keepEnd, canPad=True):
	"""(status, start, end, leadPad, tailPad) for one song: where to cut, and how much silence to add, so the song begins and ends with exactly keepStart and keepEnd seconds of silence.

	status is "ok" (the file is cut, padded or both), "nothing" (it already has
	the wanted silence) or "silent" (the file is silent, or nothing worth keeping
	is left). Silence in the middle of the song is never touched. Silence is only
	added when canPad is true, because adding it means encoding the song again.
	"""
	gaps = sorted(silences)
	start = 0.0
	end = durationSeconds
	onset = 0.0
	offset = durationSeconds
	leading = gaps[0] if gaps and gaps[0][0] <= 0.05 else None
	trailing = gaps[-1] if gaps and gaps[-1][1] >= durationSeconds - 0.05 else None
	if leading and trailing and leading is trailing:
		return "silent", 0.0, durationSeconds, 0.0, 0.0
	if leading:
		onset = leading[1]
		start = max(leading[0], leading[1] - keepStart)
	if trailing:
		offset = trailing[0]
		end = min(trailing[1], trailing[0] + keepEnd)
	if end - start < MIN_KEPT_SECONDS:
		return "silent", 0.0, durationSeconds, 0.0, 0.0
	leadPad = lead_padding(start, onset, keepStart) if canPad else 0.0
	tailPad = tail_padding(end, offset, keepEnd) if canPad else 0.0
	if start < NOTHING_TO_TRIM_SECONDS and durationSeconds - end < NOTHING_TO_TRIM_SECONDS and leadPad <= 0 and tailPad <= 0:
		return "nothing", 0.0, durationSeconds, 0.0, 0.0
	return "ok", start, end, leadPad, tailPad


def same_file_path(firstPath, secondPath):
	return os.path.normcase(os.path.abspath(firstPath)) == os.path.normcase(os.path.abspath(secondPath))


def refine_trailing_silence(ffmpegPath, filePath, durationSeconds, silences, key, registerProcess, cancelEvent):
	"""Move the start of the silence at the end of a song from "very quiet" to "completely silent".

	The first pass counts everything below the normal level as silence, which
	also swallows the last, very quiet part of a fade out. Only the stretch the
	first pass called silent is looked at again, with the stricter end level, and
	the song then ends where that stricter silence really begins. If the sound
	never gets that quiet, the end of the song is left as it is.
	"""
	level, minSilence, endLevel = key
	gaps = sorted(silences)
	if not gaps or gaps[-1][1] < durationSeconds - 0.05 or endLevel >= level:
		return silences
	coarseStart = gaps[-1][0]
	offset = max(0.0, coarseStart - TAIL_LOOKBACK_SECONDS)
	tailSeconds = durationSeconds - offset
	command = [
		ffmpegPath, "-hide_banner", "-ss", f"{offset:.3f}", "-i", filePath, "-vn",
		"-af", f"silencedetect=noise={endLevel}dB:d={minSilence:g}",
		"-progress", "pipe:1", "-nostats", "-f", "null", "-",
	]
	try:
		exitCode, stderrLines = runFfmpeg(
			command, os.path.dirname(filePath), tailSeconds, lambda percent: None, registerProcess,
			stderrLineLimit=ANALYSIS_STDERR_LINES,
		)
	except OSError as error:
		log.error("xTrack: could not run ffmpeg for the end of %s: %s", filePath, error)
		return silences
	if exitCode != 0 or cancelEvent.is_set():
		return silences
	strictGaps = [(offset + start, offset + end) for start, end in parse_silences(stderrLines, tailSeconds)]
	strictTrailing = [gap for gap in strictGaps if gap[1] >= durationSeconds - 0.05]
	kept = gaps[:-1]
	if strictTrailing:
		kept.append((max(strictTrailing[-1][0], coarseStart), durationSeconds))
	return kept


class _ShFileOpStruct(ctypes.Structure):
	_fields_ = [
		("hwnd", ctypes.c_void_p),
		("wFunc", ctypes.c_uint),
		("pFrom", ctypes.c_void_p),
		("pTo", ctypes.c_void_p),
		("fFlags", ctypes.c_ushort),
		("fAnyOperationsAborted", ctypes.c_int),
		("hNameMappings", ctypes.c_void_p),
		("lpszProgressTitle", ctypes.c_void_p),
	]


def move_to_recycle_bin(path):
	"""True when the file was sent to the Recycle Bin and is gone from its folder. Any failure returns False and leaves the file where it is."""
	try:
		fromBuffer = ctypes.create_unicode_buffer(os.path.abspath(path) + "\0")
		operation = _ShFileOpStruct()
		operation.wFunc = FO_DELETE
		operation.pFrom = ctypes.cast(fromBuffer, ctypes.c_void_p)
		operation.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
		result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
	except (AttributeError, OSError, ValueError) as error:
		log.error("xTrack: could not use the Recycle Bin for %s: %s", path, error)
		return False
	if result != 0 or operation.fAnyOperationsAborted:
		log.error("xTrack: the Recycle Bin refused %s (code %s)", path, result)
		return False
	return not os.path.exists(path)


def place_finished_file(partPath, finalPath):
	"""Move the finished part file to its final name; True on success. The name can stay busy for a moment after the Recycle Bin or Explorer touched it, so it is tried again a few times."""
	for attempt in range(PLACE_RETRIES):
		try:
			os.replace(partPath, finalPath)
			return True
		except OSError as error:
			log.debugWarning("xTrack: attempt %d to place %s failed: %s", attempt + 1, finalPath, error)
			time.sleep(PLACE_RETRY_DELAY_SECONDS)
	log.error("xTrack: could not place %s after %d attempts", finalPath, PLACE_RETRIES)
	return False


class SplitTrackBySilentDialog(wx.Dialog):
	"""Trims the silence at the start and the end of one or more songs so every song begins and ends with the same short stretch of silence."""
	def __init__(self, parent, selected_files, libs_path):
		super().__init__(parent, title=_("Split Track by Silent"))
		files = []
		for filePath in selected_files if isinstance(selected_files, (list, tuple)) else [selected_files]:
			if filePath and os.path.isfile(filePath) and filePath not in files:
				files.append(filePath)
		if not files:
			raise ValueError("No file was selected.")
		self.libs_path = libs_path
		self.config_path = get_config_path()
		self.rows = []
		for filePath in files:
			baseName, extension = os.path.splitext(os.path.basename(filePath))
			self.rows.append({
				"path": filePath, "base": baseName, "ext": extension, "title": baseName,
				"duration": 0.0, "silences": None, "key": None, "status": "pending",
				"start": 0.0, "end": 0.0, "leadPad": 0.0, "tailPad": 0.0, "reason": "",
			})
		self.is_busy = False
		self.is_closed = False
		self.cancel_event = threading.Event()
		self.ffmpeg_process = None
		self.preview_dir = ""
		self.updating_title_text = False
		self.init_ui()
		self.load_settings()
		if len(self.rows) == 1:
			self.SetTitle(_("Split Track by Silent: {}").format(os.path.basename(files[0])))
		else:
			self.SetTitle(_("Split Track by Silent: {} files").format(len(self.rows)))
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

		settings_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Detection"))
		settingsBox = settings_sizer.GetStaticBox()
		grid = wx.FlexGridSizer(cols=2, vgap=5, hgap=8)
		# The same fields, wording and stored values as Split Album By Tracks. Plain edit
		# fields are used because a spin box that holds decimals does not pass its label on to NVDA.
		grid.Add(wx.StaticText(settingsBox, label=_("Sh&ortest silence to trim (seconds):")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.min_silence_ctrl = wx.TextCtrl(settingsBox, value=f"{DEFAULT_MIN_SILENCE:g}")
		grid.Add(self.min_silence_ctrl, 0)
		grid.Add(wx.StaticText(settingsBox, label=_("Silence &level (dB, higher finds quieter gaps less):")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.level_ctrl = wx.SpinCtrl(settingsBox, min=-90, max=-10, initial=DEFAULT_LEVEL_DB)
		grid.Add(self.level_ctrl, 0)
		grid.Add(wx.StaticText(settingsBox, label=_("Level of complete silence at the e&nd of a track (dB, lower keeps more of a fade out):")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.end_level_ctrl = wx.SpinCtrl(settingsBox, min=-100, max=-20, initial=DEFAULT_END_LEVEL_DB)
		grid.Add(self.end_level_ctrl, 0)
		grid.Add(wx.StaticText(settingsBox, label=_("Silence &before each track (seconds, 0 = none):")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.lead_ctrl = wx.TextCtrl(settingsBox, value=f"{DEFAULT_LEAD_SILENCE:g}")
		self.lead_ctrl.Bind(wx.EVT_KILL_FOCUS, self.on_gap_field_left)
		grid.Add(self.lead_ctrl, 0)
		grid.Add(wx.StaticText(settingsBox, label=_("Silence &after each track (seconds, 0 = none):")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.tail_ctrl = wx.TextCtrl(settingsBox, value=f"{DEFAULT_TAIL_SILENCE:g}")
		self.tail_ctrl.Bind(wx.EVT_KILL_FOCUS, self.on_gap_field_left)
		grid.Add(self.tail_ctrl, 0)
		settings_sizer.Add(grid, 0, wx.ALL, 5)
		self.analyze_btn = wx.Button(settingsBox, label=_("Analy&ze again"))
		self.analyze_btn.Bind(wx.EVT_BUTTON, self.on_analyze)
		settings_sizer.Add(self.analyze_btn, 0, wx.ALL, 5)
		main_sizer.Add(settings_sizer, 0, wx.EXPAND | wx.ALL, 5)

		tracks_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Tracks"))
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

		# Unchecked by default, so the originals are kept unless the user asks otherwise.
		self.remove_original_check = wx.CheckBox(self, label=_("&Remove original track (sent to the Recycle Bin)"))
		self.remove_original_check.SetValue(False)
		main_sizer.Add(self.remove_original_check, 0, wx.ALL, 8)

		self.progress_bar = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
		main_sizer.Add(self.progress_bar, 0, wx.EXPAND | wx.ALL, 5)
		self.status_label = wx.StaticText(self, label="")
		main_sizer.Add(self.status_label, 0, wx.EXPAND | wx.ALL, 5)

		btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
		self.preview_start_btn = wx.Button(self, label=_("&Preview beginning"))
		self.preview_start_btn.Bind(wx.EVT_BUTTON, lambda event: self.on_preview(atEnd=False))
		btn_sizer.Add(self.preview_start_btn, 0, wx.ALL, 5)
		self.preview_end_btn = wx.Button(self, label=_("Preview &ending"))
		self.preview_end_btn.Bind(wx.EVT_BUTTON, lambda event: self.on_preview(atEnd=True))
		btn_sizer.Add(self.preview_end_btn, 0, wx.ALL, 5)
		self.split_btn = wx.Button(self, wx.ID_OK, label=_("&Split"))
		self.split_btn.Bind(wx.EVT_BUTTON, self.on_split)
		btn_sizer.Add(self.split_btn, 0, wx.ALL, 5)
		self.cancel_btn = wx.Button(self, wx.ID_CANCEL, label=_("Close"))
		self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
		btn_sizer.Add(self.cancel_btn, 0, wx.ALL, 5)
		main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

		self.SetSizer(main_sizer)
		self.Fit()
		self.SetSize((640, 700))
		self.refresh_track_list()
		self.update_buttons()

	def load_settings(self):
		config_data = load_config(self.config_path)
		try:
			self.min_silence_ctrl.SetValue(f"{float(config_data.get('TrimSilentMinSilence', DEFAULT_MIN_SILENCE)):g}")
			self.level_ctrl.SetValue(int(config_data.get("SplitSilenceLevelDb", DEFAULT_LEVEL_DB)))
			self.end_level_ctrl.SetValue(int(config_data.get("SplitSilenceEndLevelDb", DEFAULT_END_LEVEL_DB)))
			self.lead_ctrl.SetValue(f"{float(config_data.get('SplitLeadSilence', DEFAULT_LEAD_SILENCE)):g}")
			self.tail_ctrl.SetValue(f"{float(config_data.get('SplitTailSilence', DEFAULT_TAIL_SILENCE)):g}")
		except (TypeError, ValueError):
			log.error("xTrack: the saved Split Track by Silent settings are not valid; using the defaults")

	@staticmethod
	def read_seconds(control, default, low, high):
		try:
			value = float(control.GetValue().strip().replace(",", "."))
		except ValueError:
			return default
		if value != value:
			return default
		return min(max(value, low), high)

	def get_min_silence_setting(self):
		return self.read_seconds(self.min_silence_ctrl, DEFAULT_MIN_SILENCE, 0.1, 30.0)

	def get_lead_setting(self):
		return self.read_seconds(self.lead_ctrl, DEFAULT_LEAD_SILENCE, 0.0, 5.0)

	def get_tail_setting(self):
		return self.read_seconds(self.tail_ctrl, DEFAULT_TAIL_SILENCE, 0.0, 5.0)

	def normalize_seconds_fields(self):
		"""Show the value that is really used, so a mistyped or out-of-range entry is not silently ignored."""
		for control, value in (
			(self.min_silence_ctrl, self.get_min_silence_setting()),
			(self.lead_ctrl, self.get_lead_setting()),
			(self.tail_ctrl, self.get_tail_setting()),
		):
			text = f"{value:g}"
			if control.GetValue() != text:
				control.SetValue(text)

	def save_settings(self):
		config_data = load_config(self.config_path)
		config_data["TrimSilentMinSilence"] = self.get_min_silence_setting()
		config_data["SplitSilenceLevelDb"] = self.level_ctrl.GetValue()
		config_data["SplitSilenceEndLevelDb"] = self.end_level_ctrl.GetValue()
		config_data["SplitLeadSilence"] = self.get_lead_setting()
		config_data["SplitTailSilence"] = self.get_tail_setting()
		save_config(self.config_path, config_data)

	def update_buttons(self):
		hasTrim = any(row["status"] == "ok" for row in self.rows)
		self.analyze_btn.Enable(not self.is_busy)
		self.preview_start_btn.Enable(hasTrim and not self.is_busy)
		self.preview_end_btn.Enable(hasTrim and not self.is_busy)
		self.split_btn.Enable(hasTrim and not self.is_busy)
		self.remove_original_check.Enable(not self.is_busy)

	def set_busy(self, isBusy):
		self.is_busy = isBusy
		self.update_buttons()

	# ---- analysis
	def on_analyze(self, event):
		self.start_analysis()

	def current_key(self):
		return (self.level_ctrl.GetValue(), round(self.get_min_silence_setting(), 3), self.end_level_ctrl.GetValue())

	def start_analysis(self):
		if self.is_busy:
			return
		ffmpegPath = os.path.join(self.libs_path, "ffmpeg.exe")
		if not os.path.exists(ffmpegPath):
			ui.message(_("ffmpeg.exe not found"))
			return
		key = self.current_key()
		pending = [row for row in self.rows if row["key"] != key]
		if not pending:
			# Only the silence before and after each track can have changed: the silent gaps are already known.
			self.recompute_all()
			self.announce_result()
			return
		self.cancel_event.clear()
		self.set_busy(True)
		self.progress_bar.SetValue(0)
		self.status_label.SetLabel(_("Analyzing..."))
		ui.message(_("Analyzing {} files for silence at the start and end").format(len(pending)) if len(pending) > 1 else _("Analyzing the file for silence at the start and end"))
		threading.Thread(target=self.run_analysis, args=(ffmpegPath, key, pending), daemon=True).start()

	def run_analysis(self, ffmpegPath, key, pendingRows):
		total = len(pendingRows)
		for index, row in enumerate(pendingRows):
			if self.cancel_event.is_set():
				return
			self.post(self.status_label.SetLabel, _("Analyzing {} of {}: {}").format(index + 1, total, os.path.basename(row["path"])))
			durationSeconds = row["duration"]
			if durationSeconds <= 0:
				durationSeconds, _durationLabel = get_file_duration(self.libs_path, row["path"])
			if durationSeconds <= 0:
				self.post(self.on_row_failed, row, _("the duration of this file could not be read"))
				continue

			def reportProgress(percent, index=index):
				self.post(self.progress_bar.SetValue, int(((index + percent / 100) / total) * 100))

			command = build_silence_command(ffmpegPath, row["path"], key[0], key[1])
			try:
				exitCode, stderrLines = runFfmpeg(
					command, os.path.dirname(row["path"]), durationSeconds, reportProgress, self.register_process,
					stderrLineLimit=ANALYSIS_STDERR_LINES,
				)
			except OSError as error:
				log.error("xTrack: could not run ffmpeg for the silence analysis: %s", error)
				self.post(self.on_row_failed, row, _("ffmpeg could not be started"))
				continue
			if self.cancel_event.is_set():
				return
			if exitCode != 0:
				log.error("xTrack: silence analysis of %s failed (exit code %s): %s", row["path"], exitCode, " | ".join(line.strip() for line in stderrLines[-10:]))
				self.post(self.on_row_failed, row, summarizeFfmpegError(stderrLines))
				continue
			silences = self.refine_trailing_silence(ffmpegPath, row["path"], durationSeconds, parse_silences(stderrLines, durationSeconds), key)
			if self.cancel_event.is_set():
				return
			self.post(self.on_row_analyzed, row, durationSeconds, silences, key)
		self.post(self.on_analysis_done)

	def refine_trailing_silence(self, ffmpegPath, filePath, durationSeconds, silences, key):
		return refine_trailing_silence(
			ffmpegPath, filePath, durationSeconds, silences, key, self.register_process, self.cancel_event,
		)

	def on_row_failed(self, row, reason):
		row["status"] = "failed"
		row["reason"] = reason
		row["key"] = None

	def on_row_analyzed(self, row, durationSeconds, silences, key):
		row["duration"] = durationSeconds
		row["silences"] = silences
		row["key"] = key
		row["reason"] = ""
		self.recompute_row(row)

	def on_analysis_done(self):
		self.progress_bar.SetValue(100)
		self.set_busy(False)
		self.recompute_all()
		self.announce_result()

	def recompute_row(self, row):
		if row["silences"] is None:
			return
		canPad = row["ext"].lower() in PAD_ENCODER_ARGS
		status, start, end, leadPad, tailPad = compute_trim(
			row["silences"], row["duration"], self.get_lead_setting(), self.get_tail_setting(), canPad,
		)
		row["status"] = status
		row["start"] = start
		row["end"] = end
		row["leadPad"] = leadPad
		row["tailPad"] = tailPad

	def recompute_all(self):
		for row in self.rows:
			self.recompute_row(row)
		self.refresh_track_list()
		self.update_buttons()

	def on_gap_field_left(self, event):
		if self.is_closed:
			event.Skip()
			return
		self.normalize_seconds_fields()
		self.recompute_all()
		event.Skip()

	def announce_result(self):
		trimmable = sum(1 for row in self.rows if row["status"] == "ok")
		self.status_label.SetLabel(_("{} of {} files have silence to trim").format(trimmable, len(self.rows)))
		if trimmable:
			tones.beep(1000, 200)
			ui.message(limitSpokenText(_("{count} of {total} files have silence to trim. Review them in the list, then press Split.").format(count=trimmable, total=len(self.rows))))
		else:
			tones.beep(500, 200)
			ui.message(_("No silence to trim was found with these settings."))
		# As in Split Album By Tracks, the list of tracks takes the focus once the analysis is done.
		self.track_list.SetFocus()

	# ---- the list
	def describe_row(self, number, row):
		status = row["status"]
		title = row["title"] or row["base"]
		if status == "ok":
			text = _("{number}. {title}: cut {head} seconds at the start and {tail} seconds at the end, new length {length}").format(
				number=number, title=title, head=f"{row['start']:.1f}", tail=f"{row['duration'] - row['end']:.1f}",
				length=formatClock(row["end"] - row["start"] + row["leadPad"] + row["tailPad"]),
			)
			if row["leadPad"] > 0 or row["tailPad"] > 0:
				text += _(", added {head} seconds of silence at the start and {tail} seconds at the end").format(
					head=f"{row['leadPad']:.1f}", tail=f"{row['tailPad']:.1f}",
				)
			return text
		if status == "nothing":
			return _("{number}. {title}: no change needed, it is copied as it is").format(number=number, title=title)
		if status == "silent":
			return _("{number}. {title}: only silence was found, so it is left alone").format(number=number, title=title)
		if status == "failed":
			return _("{number}. {title}: could not be analyzed ({reason})").format(number=number, title=title, reason=row["reason"])
		return _("{number}. {title}: waiting for analysis").format(number=number, title=title)

	def refresh_track_list(self):
		selection = self.track_list.GetSelection()
		self.track_list.Set([self.describe_row(number, row) for number, row in enumerate(self.rows, 1)])
		if self.rows:
			self.track_list.SetSelection(selection if selection != wx.NOT_FOUND and selection < len(self.rows) else 0)
			self.show_selected_title()

	def selected_row(self):
		index = self.track_list.GetSelection()
		if index == wx.NOT_FOUND or index >= len(self.rows):
			return None
		return self.rows[index]

	def show_selected_title(self):
		row = self.selected_row()
		if row is None:
			return
		self.updating_title_text = True
		self.title_ctrl.SetValue(row["title"])
		self.updating_title_text = False

	def on_track_selected(self, event):
		self.show_selected_title()

	def on_title_changed(self, event):
		if self.updating_title_text:
			return
		index = self.track_list.GetSelection()
		if index == wx.NOT_FOUND or index >= len(self.rows):
			return
		self.rows[index]["title"] = self.title_ctrl.GetValue()
		self.track_list.SetString(index, self.describe_row(index + 1, self.rows[index]))

	# ---- preview
	def on_preview(self, atEnd):
		row = self.selected_row()
		if self.is_busy or row is None:
			ui.message(_("Select a track in the list first."))
			return
		if row["status"] != "ok":
			ui.message(_("This track has no silence to trim, so there is nothing to preview."))
			return
		ffmpegPath = os.path.join(self.libs_path, "ffmpeg.exe")
		length = min(PREVIEW_SECONDS, row["end"] - row["start"])
		start = max(row["start"], row["end"] - length) if atEnd else row["start"]
		leadPad = 0.0 if atEnd else row["leadPad"]
		tailPad = row["tailPad"] if atEnd else 0.0
		filters = []
		if leadPad > 0:
			filters.append(f"adelay=delays={int(round(leadPad * 1000))}:all=1")
		if tailPad > 0:
			filters.append(f"apad=pad_dur={tailPad:.3f}")
		self.cleanup_preview_dir()
		self.preview_dir = tempfile.mkdtemp(prefix="xtrack_trim_preview_")
		previewPath = os.path.join(self.preview_dir, "preview.wav")
		command = [
			ffmpegPath, "-hide_banner", "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", row["path"],
			"-t", f"{length + leadPad + tailPad:.3f}", "-vn",
		]
		if filters:
			command += ["-af", ",".join(filters)]
		command += ["-c:a", "pcm_s16le", previewPath]
		self.cancel_event.clear()
		self.set_busy(True)
		self.status_label.SetLabel(_("Preparing preview..."))
		threading.Thread(target=self.run_preview, args=(command, previewPath, atEnd), daemon=True).start()

	def run_preview(self, command, previewPath, atEnd):
		try:
			exitCode, stderrLines = runFfmpeg(command, self.preview_dir, 0, lambda percent: None, self.register_process)
		except OSError as error:
			log.error("xTrack: could not run ffmpeg for the preview: %s", error)
			self.post(self.on_preview_done, None, _("ffmpeg could not be started"), atEnd)
			return
		if self.cancel_event.is_set():
			return
		if exitCode != 0 or not os.path.exists(previewPath):
			self.post(self.on_preview_done, None, summarizeFfmpegError(stderrLines), atEnd)
			return
		self.post(self.on_preview_done, previewPath, "", atEnd)

	def on_preview_done(self, previewPath, reason, atEnd):
		self.set_busy(False)
		self.status_label.SetLabel("")
		if not previewPath:
			tones.beep(300, 300)
			ui.message(limitSpokenText(_("Preview failed: {}").format(reason)))
			return
		try:
			os.startfile(previewPath)
		except OSError as error:
			log.error("xTrack: could not open the preview: %s", error)
			ui.message(_("The preview was created but could not be played."))
			return
		ui.message(_("Playing the end of the track") if atEnd else _("Playing the beginning of the track"))

	def cleanup_preview_dir(self):
		if self.preview_dir:
			shutil.rmtree(self.preview_dir, ignore_errors=True)
		self.preview_dir = ""

	# ---- split
	def on_split(self, event):
		if self.is_busy:
			return
		self.normalize_seconds_fields()
		if any(row["key"] != self.current_key() for row in self.rows if row["status"] != "failed"):
			ui.message(_("The detection settings changed. Press Analyze again first."))
			return
		self.recompute_all()
		jobs = [dict(row) for row in self.rows if row["status"] in ("ok", "nothing")]
		changedCount = sum(1 for job in jobs if job["status"] == "ok")
		if not changedCount:
			ui.message(_("No track has silence to trim."))
			return
		ffmpegPath = os.path.join(self.libs_path, "ffmpeg.exe")
		if not os.path.exists(ffmpegPath):
			ui.message(_("ffmpeg.exe not found"))
			return
		removeOriginal = self.remove_original_check.GetValue()
		if removeOriginal:
			question = _("{} original tracks will be sent to the Recycle Bin after they have been trimmed. Continue?").format(changedCount) if changedCount > 1 else _("The original track will be sent to the Recycle Bin after it has been trimmed. Continue?")
			with wx.MessageDialog(self, question, _("Remove original track"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING) as confirm:
				if confirm.ShowModal() != wx.ID_YES:
					return
		self.save_settings()
		self.cancel_event.clear()
		self.set_busy(True)
		self.progress_bar.SetValue(0)
		self.status_label.SetLabel(_("Starting split operation..."))
		threading.Thread(target=self.perform_split, args=(ffmpegPath, jobs, removeOriginal, _("Trimmed")), daemon=True).start()

	def plan_output_path(self, job, removeOriginal, folderName):
		"""Where the trimmed copy goes. Removing the original puts it in the original's place; keeping the original puts it, under the same name, in a folder next to it."""
		sourceDir = os.path.dirname(job["path"])
		baseName = safe_title(job["title"], job["base"])
		extension = job["ext"]
		if removeOriginal:
			outputDir = sourceDir
			wanted = os.path.join(outputDir, baseName + extension)
			if not os.path.exists(wanted) or same_file_path(wanted, job["path"]):
				return outputDir, wanted
		else:
			outputDir = os.path.join(sourceDir, folderName)
		os.makedirs(outputDir, exist_ok=True)
		return outputDir, os.path.join(outputDir, get_unique_filename(outputDir, baseName, extension.lstrip(".")))

	def build_cut_command(self, ffmpegPath, job, partPath):
		"""(command, outputSeconds) for one trimmed copy. Silence that has to be added means the song is encoded again."""
		padArgs = pad_arguments(job["ext"], job["leadPad"], job["tailPad"])
		segmentLength = job["end"] - job["start"]
		outputSeconds = segmentLength + (job["leadPad"] + job["tailPad"] if padArgs else 0.0)
		command = [ffmpegPath, "-hide_banner", "-y", "-ss", f"{job['start']:.3f}"]
		if padArgs:
			# The input has to end where the song ends, or the silence would be added after the end of the file.
			command += ["-t", f"{segmentLength:.3f}"]
		command += [
			"-i", job["path"],
			"-t", f"{outputSeconds:.3f}", "-c", "copy", "-map_metadata", "0",
		]
		if job["ext"].lower() == ".mp3":
			command += ["-id3v2_version", "3"]
		elif job["ext"].lower() == ".flac":
			# Copying a FLAC stream keeps the header of the whole file; encoding again
			# is lossless and writes the correct length.
			command += ["-c:a", "flac"]
		command += padArgs
		command += ["-progress", "pipe:1", "-nostats", partPath]
		return command, outputSeconds

	def part_is_complete(self, partPath, expectedSeconds):
		"""True when the finished part file exists and is about as long as planned. An original is never sent away for a copy that fails this."""
		try:
			if os.path.getsize(partPath) <= 0:
				return False
		except OSError:
			return False
		actualSeconds, _durationLabel = get_file_duration(self.libs_path, partPath)
		if actualSeconds <= 0:
			# The length could not be read, so the size check above is all there is.
			return True
		if abs(actualSeconds - expectedSeconds) > DURATION_TOLERANCE_SECONDS:
			log.error("xTrack: %s is %.1f seconds long but %.1f were planned", partPath, actualSeconds, expectedSeconds)
			return False
		return True

	def perform_split(self, ffmpegPath, jobs, removeOriginal, folderName):
		total = len(jobs)
		created = []
		failed = []
		leftover = []
		renamed = []
		unchanged = []
		for index, job in enumerate(jobs):
			if self.cancel_event.is_set():
				return
			name = os.path.basename(job["path"])
			self.post(self.status_label.SetLabel, _("Processing {} of {}: {}").format(index + 1, total, name))
			if job["status"] == "nothing":
				if removeOriginal:
					unchanged.append(name)
				elif self.copy_unchanged(job, folderName):
					unchanged.append(name)
				else:
					failed.append(name)
				continue
			try:
				outputDir, finalPath = self.plan_output_path(job, removeOriginal, folderName)
			except OSError as error:
				log.error("xTrack: could not prepare the output for %s: %s", job["path"], error)
				failed.append(name)
				continue
			partPath = partPathFor(finalPath)
			command, outputSeconds = self.build_cut_command(ffmpegPath, job, partPath)

			def reportProgress(percent, index=index):
				self.post(self.progress_bar.SetValue, int(((index + percent / 100) / total) * 100))

			try:
				exitCode, stderrLines = runFfmpeg(command, outputDir, outputSeconds, reportProgress, self.register_process)
			except OSError as error:
				log.error("xTrack: could not run ffmpeg for %s: %s", job["path"], error)
				removeQuietly(partPath)
				failed.append(name)
				continue
			if self.cancel_event.is_set():
				removeQuietly(partPath)
				return
			if exitCode != 0 or not os.path.exists(partPath) or os.path.getsize(partPath) == 0:
				log.error("xTrack: trimming %s failed (exit code %s): %s", job["path"], exitCode, " | ".join(line.strip() for line in stderrLines))
				removeQuietly(partPath)
				failed.append(name)
				continue
			if not self.finish_output(job, partPath, finalPath, removeOriginal, outputSeconds, leftover, renamed):
				failed.append(name)
				continue
			created.append(finalPath)
		self.post(self.on_split_finished, created, failed, leftover, renamed, unchanged, removeOriginal, folderName)

	def copy_unchanged(self, job, folderName):
		"""Put a song that needs no change into the output folder as it is, so the folder holds every selected song."""
		try:
			outputDir, finalPath = self.plan_output_path(job, False, folderName)
			partPath = partPathFor(finalPath)
			shutil.copy2(job["path"], partPath)
		except OSError as error:
			log.error("xTrack: could not copy %s: %s", job["path"], error)
			return False
		if place_finished_file(partPath, finalPath):
			return True
		removeQuietly(partPath)
		return False

	def finish_output(self, job, partPath, finalPath, removeOriginal, outputSeconds, leftover, renamed):
		"""Put the finished part file in place. An original is only sent away after the trimmed copy has been checked, and the trimmed copy always ends up under a visible name."""
		originalPath = job["path"]
		if not removeOriginal:
			if place_finished_file(partPath, finalPath):
				return True
			removeQuietly(partPath)
			return False
		if not self.part_is_complete(partPath, outputSeconds):
			removeQuietly(partPath)
			return False
		if not same_file_path(finalPath, originalPath):
			if not place_finished_file(partPath, finalPath):
				removeQuietly(partPath)
				return False
			if not move_to_recycle_bin(originalPath):
				leftover.append(originalPath)
			return True
		if not move_to_recycle_bin(originalPath):
			removeQuietly(partPath)
			return False
		if place_finished_file(partPath, finalPath):
			return True
		# The original is already in the Recycle Bin, so the finished copy must not stay under the hidden part name.
		folder = os.path.dirname(finalPath)
		baseName, extension = os.path.splitext(os.path.basename(finalPath))
		rescuePath = os.path.join(folder, get_unique_filename(folder, baseName + " (trimmed)", extension.lstrip(".")))
		if place_finished_file(partPath, rescuePath):
			renamed.append(os.path.basename(rescuePath))
			return True
		log.error("xTrack: the trimmed copy of %s was left as %s", originalPath, partPath)
		renamed.append(os.path.basename(partPath))
		return True

	def on_split_finished(self, created, failed, leftover, renamed, unchanged, removeOriginal, folderName):
		self.set_busy(False)
		self.progress_bar.SetValue(100)
		if not created:
			self.status_label.SetLabel(_("Split failed!"))
			tones.beep(300, 300)
			ui.message(_("Failed to trim the audio files"))
			return
		tones.beep(1000, 300)
		parts = [_("{} tracks trimmed").format(len(created)) if len(created) > 1 else _("The track was trimmed")]
		if unchanged:
			if removeOriginal:
				parts.append(_("{} files needed no change and were left where they are").format(len(unchanged)))
			else:
				parts.append(_("{} files needed no change and were copied as they are").format(len(unchanged)))
		if failed:
			parts.append(_("{} could not be trimmed: {}").format(len(failed), ", ".join(failed)))
		if leftover:
			parts.append(_("{} originals could not be sent to the Recycle Bin and were kept").format(len(leftover)))
		if renamed:
			parts.append(_("{} trimmed copies could not take the name of the original and were saved as: {}").format(len(renamed), ", ".join(renamed)))
		if not removeOriginal:
			parts.append(_("Saved in the folder {} next to the originals").format(folderName))
		self.status_label.SetLabel(parts[0])
		ui.message(limitSpokenText(". ".join(parts)))
		if not failed and not leftover and not renamed:
			self.EndModal(wx.ID_OK)

	def stop_work(self):
		self.is_closed = True
		self.cancel_event.set()
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
