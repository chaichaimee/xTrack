# merge.py

import wx
import json
import math
import os
import re
import shutil
import tempfile
import threading
import ui
import tones
from logHandler import log
from .xTrackCore import get_file_duration, get_unique_filename
from .floatingMenu import chooseMenuAction
from .ffmpegSupport import (
	limitSpokenText, summarizeFfmpegError, setProcessSuspended, removeQuietly, partPathFor, runFfmpeg,
)
import addonHandler

addonHandler.initTranslation()

INVALID_FILENAME_CHARS = '<>:"/\\|?*'


def formatDuration(seconds):
	"""Format duration in seconds to HhMMminSSsec format."""
	if seconds <= 0:
		return "0sec"
	hours = int(seconds // 3600)
	minutes = int((seconds % 3600) // 60)
	seconds = int(seconds % 60)
	result = ""
	if hours > 0:
		result += f"{hours}H"
	if minutes > 0 or hours > 0:
		result += f"{minutes}min"
	result += f"{seconds}sec"
	return result


SECTION_TIME_PATTERN = re.compile(r"^\s*(?:(\d+):)?(\d+(?:\.\d+)?)(?::(\d{1,3}))?\s*$")
MIN_SECTION_SECONDS = 0.1
PREVIEW_FILE_NAME = "section_preview.wav"
MODE_WHOLE, MODE_SECTIONS, MODE_GAP, MODE_FADES = range(4)
DEFAULT_GAP_SECONDS = 2.0
DEFAULT_SECTION_FADE_SECONDS = 1.0


def parseSectionTime(text):
	"""Seconds from minutes:seconds:hundredths (mm:ss:ff), minutes:seconds or plain seconds; None when the text is not a time.

	The last part of mm:ss:ff is a fraction of a second, so 01:30:50 is one minute, thirty and a half seconds.
	"""
	match = SECTION_TIME_PATTERN.match(str(text))
	if not match:
		return None
	minutesText, secondsText, fractionText = match.groups()
	seconds = float(secondsText)
	if minutesText is not None and seconds >= 60:
		return None
	total = (int(minutesText) * 60 if minutesText is not None else 0) + seconds
	if fractionText is not None:
		total += float("0." + fractionText)
	return total


def formatSectionTime(seconds):
	"""mm:ss:ff for a number of seconds, rounded to hundredths."""
	hundredths = max(0, int(round(seconds * 100)))
	minutes, rest = divmod(hundredths, 6000)
	return f"{minutes:02d}:{rest // 100:02d}:{rest % 100:02d}"


def floorToHundredths(seconds):
	return math.floor(seconds * 100 + 1e-6) / 100


def checkSection(startSeconds, endSeconds, durationSeconds):
	"""None when a section can be cut from its track, otherwise why not: "order", "short" or "beyond"."""
	if endSeconds <= startSeconds:
		return "order"
	if endSeconds - startSeconds < MIN_SECTION_SECONDS:
		return "short"
	if durationSeconds > 0 and (startSeconds >= durationSeconds or endSeconds > durationSeconds + 0.05):
		return "beyond"
	return None


MERGE_AUDIO_EXTENSIONS = (
	".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".amr", ".caf", ".opus", ".3ga", ".wma", ".m4r", ".alac", ".aiff", ".aif", ".pcm",
)
PROJECT_TYPE = "xTrackMergeProject"
PROJECT_VERSION = 1
PROJECT_MAX_BYTES = 1024 * 1024
PROJECT_MAX_FILES = 500
PROJECT_DEFAULT_FILE_NAME = "merge_project.json"
QUALITY_CHOICES = ("320 kbps", "256 kbps", "192 kbps", "128 kbps")


def naturalSortKey(name):
	"""Sort key that puts "track 2" before "track 10"."""
	return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name)]


def audioFilesInFolder(folderPath):
	"""The audio files directly inside a folder, in natural name order; subfolders are not searched."""
	try:
		names = [name for name in os.listdir(folderPath) if name.lower().endswith(MERGE_AUDIO_EXTENSIONS)]
	except OSError as error:
		log.error("xTrack: could not read the folder %s: %s", folderPath, error)
		return []
	return [os.path.join(folderPath, name) for name in sorted(names, key=naturalSortKey) if os.path.isfile(os.path.join(folderPath, name))]


def finiteNumber(value, minimum, maximum, default):
	if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
		return default
	return max(minimum, min(maximum, float(value)))


def buildProject(mode, files, sections, gapSeconds, crossfadeEnabled, crossfadeSeconds, fadeInNextSeconds, quality, outputName):
	"""The settings of a merge as a plain dict that can be written to a file."""
	return {
		"type": PROJECT_TYPE,
		"version": PROJECT_VERSION,
		"mode": mode,
		"files": list(files),
		"sections": {
			file: {"start": sections[file][0], "end": sections[file][1], "fadeIn": sections[file][2], "fadeOut": sections[file][3]}
			for file in files if file in sections
		},
		"gapSeconds": gapSeconds,
		"crossfade": {"enabled": bool(crossfadeEnabled), "durationSeconds": crossfadeSeconds, "fadeInNextSeconds": fadeInNextSeconds},
		"quality": quality,
		"outputName": outputName,
	}


def sanitizeProject(raw):
	"""A complete, range-checked project from the contents of a project file, or None when the file is not a merge project. Every value is checked here, so a file edited by hand cannot put anything unexpected into the dialog or into an ffmpeg command."""
	if not isinstance(raw, dict) or raw.get("type") != PROJECT_TYPE:
		return None
	rawFiles = raw.get("files")
	if not isinstance(rawFiles, list):
		return None
	files = []
	for entry in rawFiles[:PROJECT_MAX_FILES]:
		if isinstance(entry, str) and entry and entry not in files:
			files.append(entry)
	mode = raw.get("mode")
	if isinstance(mode, bool) or mode not in (MODE_SECTIONS, MODE_GAP, MODE_FADES):
		mode = MODE_SECTIONS
	rawSections = raw.get("sections") if isinstance(raw.get("sections"), dict) else {}
	sections = {}
	for file in files:
		entry = rawSections.get(file)
		entry = entry if isinstance(entry, dict) else {}
		start = finiteNumber(entry.get("start"), 0.0, 360000.0, 0.0)
		end = entry.get("end")
		end = finiteNumber(end, 0.0, 360000.0, None) if end is not None else None
		if end is not None and end <= start:
			start, end = 0.0, None
		sections[file] = [
			start, end,
			finiteNumber(entry.get("fadeIn"), 0.0, 60.0, DEFAULT_SECTION_FADE_SECONDS),
			finiteNumber(entry.get("fadeOut"), 0.0, 60.0, DEFAULT_SECTION_FADE_SECONDS),
		]
	crossfade = raw.get("crossfade") if isinstance(raw.get("crossfade"), dict) else {}
	quality = raw.get("quality")
	outputName = raw.get("outputName")
	return {
		"mode": mode,
		"files": files,
		"sections": sections,
		"gapSeconds": finiteNumber(raw.get("gapSeconds"), 0.1, 60.0, DEFAULT_GAP_SECONDS),
		"crossfadeEnabled": crossfade.get("enabled") is True,
		"crossfadeSeconds": int(finiteNumber(crossfade.get("durationSeconds"), 1, 30, 5)),
		"fadeInNextSeconds": int(finiteNumber(crossfade.get("fadeInNextSeconds"), 0, 10, 0)),
		"quality": quality if quality in QUALITY_CHOICES else None,
		"outputName": outputName.strip()[:120] if isinstance(outputName, str) and outputName.strip() else None,
	}


def writeProjectFile(path, project):
	"""Write the project so that a crash or a full disk cannot leave a half-written file: a temporary file is written first and then replaces the real one in a single step."""
	temporaryPath = path + ".tmp"
	try:
		with open(temporaryPath, "w", encoding="utf-8") as projectFile:
			json.dump(project, projectFile, indent=2, ensure_ascii=False)
			projectFile.flush()
			os.fsync(projectFile.fileno())
		os.replace(temporaryPath, path)
		return True
	except (OSError, TypeError, ValueError) as error:
		log.error("xTrack: could not save the merge project %s: %s", path, error)
		try:
			if os.path.exists(temporaryPath):
				os.remove(temporaryPath)
		except OSError:
			pass
		return False


def readProjectFile(path):
	"""The sanitized project in a file, or None when the file cannot be read or is not a merge project."""
	try:
		with open(path, "rb") as projectFile:
			rawBytes = projectFile.read(PROJECT_MAX_BYTES + 1)
		if len(rawBytes) > PROJECT_MAX_BYTES:
			return None
		return sanitizeProject(json.loads(rawBytes.decode("utf-8-sig")))
	except (OSError, ValueError) as error:
		log.error("xTrack: could not read the merge project %s: %s", path, error)
		return None


def checkFades(sectionSeconds, fadeInSeconds, fadeOutSeconds):
	"""True when a section is long enough for its fade in and fade out."""
	return fadeInSeconds + fadeOutSeconds <= sectionSeconds + 0.001


def normalizedInputs(trackCount, fadeInSeconds, sections, gapSeconds=0):
	"""One normalizing chain per input: same sample rate, format and layout (acrossfade and concat need that), cut to its section when there is one, with the section's own fade in and fade out when it has them, then the optional extra fade-in and, before the next track, the silence of a gap.

	sections holds one (start, end) pair per input, or (start, end, fadeIn, fadeOut) when the
	sections fade.
	"""
	parts = []
	for index in range(trackCount):
		fadeIn = ""
		if fadeInSeconds > 0 and index > 0:
			fadeIn = f"afade=t=in:st=0:d={fadeInSeconds},"
		trim = ""
		if sections:
			entry = sections[index]
			trim = f"atrim=start={entry[0]:.3f}:end={entry[1]:.3f},asetpts=PTS-STARTPTS,"
			if len(entry) > 2:
				sectionSeconds = entry[1] - entry[0]
				if entry[2] > 0:
					trim += f"afade=t=in:st=0:d={entry[2]:.3f},"
				if entry[3] > 0:
					trim += f"afade=t=out:st={max(0.0, sectionSeconds - entry[3]):.3f}:d={entry[3]:.3f},"
		pad = f"apad=pad_dur={gapSeconds:.3f}," if gapSeconds > 0 and index < trackCount - 1 else ""
		parts.append(
			f"[{index}:a:0]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,{trim}{fadeIn}{pad}anull[n{index}]"
		)
	return parts


def buildCrossfadeFilter(trackCount, crossfadeSeconds, fadeInSeconds, sections=None):
	"""Filter graph that normalizes every input, then chains acrossfade; output label is [out].

	During the overlap the first track fades out over the whole cross-fade duration while the
	next track plays at its own full volume (c2=nofade), so the overlap is heard for its full
	length. The next track is only faded in when Fade-in for Next Track asks for it. Two tracks
	at full volume can add up past full scale, so a limiter follows the last cross-fade.
	sections, when given, holds one (start, end) pair in seconds per input.
	"""
	parts = normalizedInputs(trackCount, fadeInSeconds, sections)
	previous = "n0"
	for index in range(1, trackCount):
		label = "mix" if index == trackCount - 1 else f"x{index}"
		parts.append(f"[{previous}][n{index}]acrossfade=d={crossfadeSeconds}:c1=tri:c2=nofade[{label}]")
		previous = label
	parts.append("[mix]alimiter=limit=0.97:level=0[out]")
	return ";".join(parts)


def buildConcatFilter(trackCount, sections=None, gapSeconds=0):
	"""Filter graph that joins the inputs one after the other (each cut to its section when sections are given, with gapSeconds of silence between them); output label is [out]."""
	parts = normalizedInputs(trackCount, 0, sections, gapSeconds)
	inputs = "".join(f"[n{index}]" for index in range(trackCount))
	parts.append(f"{inputs}concat=n={trackCount}:v=0:a=1[out]")
	return ";".join(parts)


class MergeAudioDialog(wx.Dialog):
	def __init__(self, parent, selected_files, libs_path):
		super().__init__(parent, title=_("Merge MP3"))
		self.selected_files = list(selected_files)
		self.libs_path = libs_path
		self.output_path = os.path.dirname(self.selected_files[0]) if self.selected_files else os.getcwd()
		self.ffmpeg_process = None
		self.is_paused = False
		self.is_running = False
		self.is_closed = False
		self.cancel_event = threading.Event()
		self.last_announced_progress = -1
		self.all_mp3 = all(os.path.splitext(f)[1].lower() == '.mp3' for f in self.selected_files)
		self.file_durations = {}
		self.file_duration_seconds = {}
		self.sections = {}
		self.section_mode = False
		self.merge_mode = MODE_WHOLE
		self.project_path = None
		self.updating_section_fields = False
		self.preview_dir = ""
		self.preview_busy = False
		self.init_ui()
		self.Bind(wx.EVT_CLOSE, self.on_close)
		threading.Thread(target=self.load_durations, daemon=True).start()

	def post(self, handler, *args):
		wx.CallAfter(self.run_if_open, handler, *args)

	def run_if_open(self, handler, *args):
		if self.is_closed:
			return
		handler(*args)

	def load_durations(self, files=None):
		for file in list(files if files is not None else self.selected_files):
			if self.is_closed:
				return
			durationSeconds, _label = get_file_duration(self.libs_path, file)
			self.file_duration_seconds[file] = durationSeconds
			self.file_durations[file] = formatDuration(durationSeconds)
		self.post(self.update_file_list)

	def init_ui(self):
		main_sizer = wx.BoxSizer(wx.VERTICAL)
		# Mode comes first, before the list of files
		mode_row = wx.BoxSizer(wx.HORIZONTAL)
		mode_row.Add(wx.StaticText(self, label=_("&Mode:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.mode_combo = wx.ComboBox(self, choices=[
			_("Merge whole tracks"), _("Merge selected sections of tracks"),
			_("Merge with gap or silence between tracks"), _("Merge with fade at each section"),
		], style=wx.CB_READONLY)
		self.mode_combo.SetName(_("Mode"))
		self.mode_combo.SetSelection(0)
		self.mode_combo.Bind(wx.EVT_COMBOBOX, self.on_mode_changed)
		mode_row.Add(self.mode_combo, 1, wx.EXPAND | wx.ALL, 5)
		# Saving and loading a project is offered for the section, gap and fade modes only.
		self.project_btn = wx.Button(self, label=_("Project &Actions"))
		self.project_btn.Bind(wx.EVT_BUTTON, self.on_project_actions)
		# Update Project sits below Project Actions and works once a project file is known.
		self.update_project_btn = wx.Button(self, label=_("&Update Project"))
		self.update_project_btn.Bind(wx.EVT_BUTTON, self.on_update_project)
		self.update_project_btn.Enable(False)
		project_buttons = wx.BoxSizer(wx.VERTICAL)
		project_buttons.Add(self.project_btn, 0, wx.ALL, 2)
		project_buttons.Add(self.update_project_btn, 0, wx.ALL, 2)
		mode_row.Add(project_buttons, 0, wx.ALL, 3)
		self.project_btn.Hide()
		self.update_project_btn.Hide()
		main_sizer.Add(mode_row, 0, wx.EXPAND | wx.ALL, 5)
		# File list
		file_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Files to Merge"))
		self.file_list = wx.ListBox(self)
		file_sizer.Add(self.file_list, 1, wx.EXPAND | wx.ALL, 5)
		# The order of the files is changed from the context menu of the list (right-click or the Applications key).
		self.file_list.Bind(wx.EVT_CONTEXT_MENU, self.on_file_list_context_menu)
		self.file_list.Bind(wx.EVT_KEY_DOWN, self.on_file_list_key_down)
		main_sizer.Add(file_sizer, 1, wx.EXPAND | wx.ALL, 5)

		# Silence between tracks (gap mode only)
		self.gap_sizer = wx.StaticBoxSizer(wx.HORIZONTAL, self, label=_("Gap Between Tracks"))
		gapBox = self.gap_sizer.GetStaticBox()
		self.gap_sizer.Add(wx.StaticText(gapBox, label=_("Silence between tracks (seconds):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.gap_ctrl = wx.SpinCtrlDouble(gapBox, min=0.1, max=60.0, initial=DEFAULT_GAP_SECONDS, inc=0.5)
		self.gap_ctrl.SetDigits(1)
		self.gap_ctrl.SetName(_("Silence between tracks"))
		self.gap_sizer.Add(self.gap_ctrl, 0, wx.ALL, 5)
		main_sizer.Add(self.gap_sizer, 0, wx.EXPAND | wx.ALL, 5)
		main_sizer.Hide(self.gap_sizer)

		# The part of the selected track that is merged (sections mode only)
		self.section_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Section of the Selected Track"))
		sectionBox = self.section_sizer.GetStaticBox()
		section_grid = wx.FlexGridSizer(cols=2, vgap=5, hgap=8)
		section_grid.Add(wx.StaticText(sectionBox, label=_("&Start (mm:ss:ff):")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.section_start_ctrl = wx.TextCtrl(sectionBox, value=formatSectionTime(0))
		self.section_start_ctrl.SetName(_("Section start, minutes seconds hundredths"))
		section_grid.Add(self.section_start_ctrl, 0, wx.EXPAND)
		section_grid.Add(wx.StaticText(sectionBox, label=_("&End (mm:ss:ff):")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.section_end_ctrl = wx.TextCtrl(sectionBox, value=formatSectionTime(0))
		self.section_end_ctrl.SetName(_("Section end, minutes seconds hundredths"))
		section_grid.Add(self.section_end_ctrl, 0, wx.EXPAND)
		self.section_sizer.Add(section_grid, 0, wx.EXPAND | wx.ALL, 5)
		self.section_sizer.Add(wx.StaticText(sectionBox, label=_("The last part is hundredths of a second: 01:30:50 is one minute and 30.5 seconds.")), 0, wx.ALL, 5)
		# The fades of this section (fade mode only)
		self.section_fade_row = wx.BoxSizer(wx.HORIZONTAL)
		self.section_fade_row.Add(wx.StaticText(sectionBox, label=_("Fade &in (seconds):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.section_fade_in_ctrl = wx.SpinCtrlDouble(sectionBox, min=0.0, max=60.0, initial=DEFAULT_SECTION_FADE_SECONDS, inc=0.5)
		self.section_fade_in_ctrl.SetDigits(1)
		self.section_fade_in_ctrl.SetName(_("Section fade in"))
		self.section_fade_row.Add(self.section_fade_in_ctrl, 0, wx.ALL, 5)
		self.section_fade_row.Add(wx.StaticText(sectionBox, label=_("Fade o&ut (seconds):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.section_fade_out_ctrl = wx.SpinCtrlDouble(sectionBox, min=0.0, max=60.0, initial=DEFAULT_SECTION_FADE_SECONDS, inc=0.5)
		self.section_fade_out_ctrl.SetDigits(1)
		self.section_fade_out_ctrl.SetName(_("Section fade out"))
		self.section_fade_row.Add(self.section_fade_out_ctrl, 0, wx.ALL, 5)
		self.section_sizer.Add(self.section_fade_row, 0, wx.EXPAND)
		self.section_fade_in_ctrl.Bind(wx.EVT_SPINCTRLDOUBLE, self.on_section_fade_changed)
		self.section_fade_out_ctrl.Bind(wx.EVT_SPINCTRLDOUBLE, self.on_section_fade_changed)
		self.section_fade_row.ShowItems(False)
		self.preview_section_btn = wx.Button(sectionBox, label=_("&Preview Section"))
		self.preview_section_btn.Bind(wx.EVT_BUTTON, self.on_preview_section)
		self.section_sizer.Add(self.preview_section_btn, 0, wx.ALL, 5)
		self.section_start_ctrl.Bind(wx.EVT_TEXT, self.on_section_text)
		self.section_end_ctrl.Bind(wx.EVT_TEXT, self.on_section_text)
		self.section_start_ctrl.Bind(wx.EVT_KILL_FOCUS, self.on_section_commit)
		self.section_end_ctrl.Bind(wx.EVT_KILL_FOCUS, self.on_section_commit)
		main_sizer.Add(self.section_sizer, 0, wx.EXPAND | wx.ALL, 5)
		main_sizer.Hide(self.section_sizer)
		self.main_sizer = main_sizer

		# Output filename
		output_sizer = wx.BoxSizer(wx.HORIZONTAL)
		output_label = wx.StaticText(self, label=_("Output Filename:"))
		output_sizer.Add(output_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.output_text = wx.TextCtrl(self, value="merged_audio")
		output_sizer.Add(self.output_text, 1, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(output_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Cross-fade settings - Updated labels for clarity
		crossfade_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Cross-fade Settings"))
		self.crossfade_checkbox = wx.CheckBox(self, label=_("Enable Cross-fade between tracks"))
		self.crossfade_checkbox.SetToolTip(_("Overlap the end of each track with the beginning of the next: the first track fades out while the next one plays"))
		self.crossfade_checkbox.Bind(wx.EVT_CHECKBOX, self.on_crossfade_changed)
		crossfade_sizer.Add(self.crossfade_checkbox, 0, wx.ALL, 5)
		
		# Cross-fade duration
		crossfade_duration_sizer = wx.BoxSizer(wx.HORIZONTAL)
		crossfade_duration_label = wx.StaticText(self, label=_("Cross-fade Duration (seconds):"))
		crossfade_duration_sizer.Add(crossfade_duration_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.crossfade_duration_ctrl = wx.SpinCtrl(self, min=1, max=30, initial=5)
		self.crossfade_duration_ctrl.SetToolTip(_("Duration in seconds for cross-fading between tracks. Recommended value is 5 seconds."))
		crossfade_duration_sizer.Add(self.crossfade_duration_ctrl, 1, wx.EXPAND | wx.ALL, 5)
		crossfade_sizer.Add(crossfade_duration_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Fade-in for next track setting
		fade_in_next_sizer = wx.BoxSizer(wx.HORIZONTAL)
		fade_in_next_label = wx.StaticText(self, label=_("Fade-in for Next Track (seconds):"))
		fade_in_next_sizer.Add(fade_in_next_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.fade_in_next_ctrl = wx.SpinCtrl(self, min=0, max=10, initial=0)
		self.fade_in_next_ctrl.SetToolTip(_("Additional fade-in duration for the beginning of each track (except the first track)"))
		fade_in_next_sizer.Add(self.fade_in_next_ctrl, 1, wx.EXPAND | wx.ALL, 5)
		crossfade_sizer.Add(fade_in_next_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		main_sizer.Add(crossfade_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Merge mode
		mode_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Encoding Method"))
		self.reencode_radio = wx.RadioButton(self, label=_("Re-encode with quality (slower)"), style=wx.RB_GROUP)
		self.copy_radio = wx.RadioButton(self, label=_("Copy without re-encoding (faster)"))
		self.copy_radio.Enable(self.all_mp3)
		mode_sizer.Add(self.reencode_radio, 0, wx.ALL, 5)
		mode_sizer.Add(self.copy_radio, 0, wx.ALL, 5)
		main_sizer.Add(mode_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Audio quality setting (enabled only for re-encode)
		quality_sizer = wx.BoxSizer(wx.HORIZONTAL)
		quality_label = wx.StaticText(self, label=_("Audio Quality:"))
		quality_sizer.Add(quality_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		quality_choices = ["320 kbps", "256 kbps", "192 kbps", "128 kbps"]
		self.quality_ctrl = wx.ComboBox(self, choices=quality_choices, style=wx.CB_READONLY)
		self.quality_ctrl.SetStringSelection("320 kbps")
		quality_sizer.Add(self.quality_ctrl, 1, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(quality_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Progress bar
		self.progress_bar = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
		main_sizer.Add(self.progress_bar, 0, wx.EXPAND | wx.ALL, 5)
		
		# Status label
		self.status_label = wx.StaticText(self, label="")
		main_sizer.Add(self.status_label, 0, wx.EXPAND | wx.ALL, 5)
		
		# Buttons
		btn_sizer = wx.StdDialogButtonSizer()
		self.merge_btn = wx.Button(self, wx.ID_OK, label=_("Merge"))
		self.merge_btn.Bind(wx.EVT_BUTTON, self.on_merge_or_pause)
		btn_sizer.AddButton(self.merge_btn)
		self.cancel_btn = wx.Button(self, wx.ID_CANCEL, label=_("Cancel"))
		self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
		btn_sizer.AddButton(self.cancel_btn)
		btn_sizer.Realize()
		main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
		self.SetSizer(main_sizer)
		self.Fit()
		
		# Bind events
		self.file_list.Bind(wx.EVT_LISTBOX, self.on_select)
		self.reencode_radio.Bind(wx.EVT_RADIOBUTTON, self.on_mode_change)
		self.copy_radio.Bind(wx.EVT_RADIOBUTTON, self.on_mode_change)
		
		# Set initial state
		if self.all_mp3:
			self.copy_radio.SetValue(True)
		else:
			self.reencode_radio.SetValue(True)
		self.on_mode_change(None)
		self.on_crossfade_changed(None)
		

	def update_file_list(self):
		"""Update file list with file names and durations (and, in sections mode, each track's start and end)."""
		selection = self.file_list.GetSelection()
		file_display_names = []
		for number, file in enumerate(self.selected_files, 1):
			base_name = os.path.basename(file)
			duration = self.file_durations.get(file, _("Calculating..."))
			if self.section_mode:
				file_display_names.append(self.describe_section(number, file))
			else:
				file_display_names.append(f"{base_name} ({duration})")
		self.file_list.Set(file_display_names)
		if selection != wx.NOT_FOUND:
			self.file_list.SetSelection(selection)

	def get_section(self, file):
		"""[start, end] of a track's section; the end is the end of the track once its length is known."""
		section = self.sections.setdefault(file, [0.0, None, DEFAULT_SECTION_FADE_SECONDS, DEFAULT_SECTION_FADE_SECONDS])
		durationSeconds = self.file_duration_seconds.get(file, 0)
		if section[1] is None and durationSeconds > 0:
			section[1] = floorToHundredths(durationSeconds)
		return section

	def describe_section(self, number, file):
		start, end, fadeIn, fadeOut = self.get_section(file)
		name = os.path.basename(file)
		if end is None:
			text = _("{number}. {name}: from {start}, to the end of the track").format(number=number, name=name, start=formatSectionTime(start))
		else:
			text = _("{number}. {name}: from {start} to {end}, length {length}").format(
				number=number, name=name, start=formatSectionTime(start), end=formatSectionTime(end), length=formatDuration(end - start),
			)
		if self.merge_mode == MODE_FADES:
			text += _(", fade in {fadeIn} seconds, fade out {fadeOut} seconds").format(fadeIn=f"{fadeIn:g}", fadeOut=f"{fadeOut:g}")
		return text

	def set_section_times(self, file, start, end):
		section = self.get_section(file)
		section[0] = start
		section[1] = end

	def selected_file(self):
		index = self.file_list.GetSelection()
		if index == wx.NOT_FOUND or index >= len(self.selected_files):
			return None
		return self.selected_files[index]

	def show_section_fields(self):
		file = self.selected_file()
		if file is None or not self.section_mode:
			return
		start, end, fadeIn, fadeOut = self.get_section(file)
		self.updating_section_fields = True
		self.section_start_ctrl.SetValue(formatSectionTime(start))
		self.section_end_ctrl.SetValue(formatSectionTime(end) if end is not None else "")
		self.section_fade_in_ctrl.SetValue(fadeIn)
		self.section_fade_out_ctrl.SetValue(fadeOut)
		self.updating_section_fields = False

	def on_section_fade_changed(self, event):
		if self.updating_section_fields:
			return
		file = self.selected_file()
		if file is None:
			return
		section = self.get_section(file)
		section[2] = self.section_fade_in_ctrl.GetValue()
		section[3] = self.section_fade_out_ctrl.GetValue()
		index = self.file_list.GetSelection()
		self.file_list.SetString(index, self.describe_section(index + 1, file))
		event.Skip()

	def on_mode_changed(self, event):
		self.merge_mode = max(0, self.mode_combo.GetSelection())
		self.section_mode = self.merge_mode in (MODE_SECTIONS, MODE_FADES)
		showSections = self.section_mode
		self.main_sizer.Show(self.section_sizer) if showSections else self.main_sizer.Hide(self.section_sizer)
		self.main_sizer.Show(self.gap_sizer) if self.merge_mode == MODE_GAP else self.main_sizer.Hide(self.gap_sizer)
		self.section_fade_row.ShowItems(self.merge_mode == MODE_FADES)
		# A cross-fade overlaps tracks; a gap or a fade at every section would work against it.
		crossfadeAllowed = self.merge_mode in (MODE_WHOLE, MODE_SECTIONS)
		if not crossfadeAllowed:
			self.crossfade_checkbox.SetValue(False)
		self.crossfade_checkbox.Enable(crossfadeAllowed)
		self.crossfade_duration_ctrl.Enable(crossfadeAllowed and self.crossfade_checkbox.GetValue())
		self.fade_in_next_ctrl.Enable(crossfadeAllowed and self.crossfade_checkbox.GetValue())
		if showSections:
			if self.file_list.GetSelection() == wx.NOT_FOUND and self.selected_files:
				self.file_list.SetSelection(0)
			self.show_section_fields()
		messages = {
			MODE_WHOLE: _("Merge whole tracks"),
			MODE_SECTIONS: _("Merge selected sections of tracks. Select a track, then set where its section starts and ends."),
			MODE_GAP: _("Merge with silence between tracks. Set how many seconds of silence to leave between the tracks."),
			MODE_FADES: _("Merge with a fade at each section. Select a track, then set its section and its fade in and fade out."),
		}
		ui.message(limitSpokenText(messages[self.merge_mode]))
		self.project_btn.Show(self.merge_mode != MODE_WHOLE)
		self.update_project_btn.Show(self.merge_mode != MODE_WHOLE)
		self.update_file_list()
		self.apply_encoding_rules(announce=self.merge_mode != MODE_WHOLE)
		self.Layout()
		self.Fit()

	def on_section_text(self, event):
		"""Keep a section up to date while its times are typed, as long as both times make sense."""
		if self.updating_section_fields:
			return
		file = self.selected_file()
		if file is None:
			return
		start = parseSectionTime(self.section_start_ctrl.GetValue())
		end = parseSectionTime(self.section_end_ctrl.GetValue())
		if start is None or end is None or end <= start:
			return
		self.set_section_times(file, start, end)
		index = self.file_list.GetSelection()
		self.file_list.SetString(index, self.describe_section(index + 1, file))

	def on_section_commit(self, event):
		"""When a time field is left, a time that cannot be used is put back to the last good value, with a spoken reason."""
		event.Skip()
		file = self.selected_file()
		if file is None or self.is_closed:
			return
		problem = self.section_text_problem(file)
		if problem:
			ui.message(limitSpokenText(problem))
			self.show_section_fields()

	def section_text_problem(self, file):
		"""A spoken reason when the times in the two fields cannot be used for this track, else None."""
		start = parseSectionTime(self.section_start_ctrl.GetValue())
		end = parseSectionTime(self.section_end_ctrl.GetValue())
		if start is None or end is None:
			return _("That is not a time. Use minutes, seconds and hundredths, for example 01:30:00.")
		return self.section_problem(file, start, end)

	def section_problem(self, file, start, end):
		reason = checkSection(start, end, self.file_duration_seconds.get(file, 0))
		name = os.path.basename(file)
		if reason == "order":
			return _("In {}, the end must come after the start.").format(name)
		if reason == "short":
			return _("The section of {} is too short.").format(name)
		if reason == "beyond":
			return _("The section of {} goes past the end of the track.").format(name)
		return None

	def on_preview_section(self, event):
		file = self.selected_file()
		if file is None:
			ui.message(_("Select a track in the list first."))
			return
		if self.is_running or self.preview_busy:
			ui.message(_("Please wait until the current work is finished."))
			return
		problem = self.section_text_problem(file)
		if problem:
			ui.message(limitSpokenText(problem))
			return
		start = parseSectionTime(self.section_start_ctrl.GetValue())
		end = parseSectionTime(self.section_end_ctrl.GetValue())
		self.set_section_times(file, start, end)
		ffmpegPath = os.path.join(self.libs_path, "ffmpeg.exe")
		if not os.path.exists(ffmpegPath):
			ui.message(_("ffmpeg.exe not found"))
			return
		self.cleanup_preview_dir()
		self.preview_dir = tempfile.mkdtemp(prefix="xtrack_merge_preview_")
		previewPath = os.path.join(self.preview_dir, PREVIEW_FILE_NAME)
		command = [ffmpegPath, "-hide_banner", "-y", "-ss", f"{start:.3f}", "-i", file, "-t", f"{end - start:.3f}", "-vn"]
		if self.merge_mode == MODE_FADES:
			fadeIn = self.section_fade_in_ctrl.GetValue()
			fadeOut = self.section_fade_out_ctrl.GetValue()
			if not checkFades(end - start, fadeIn, fadeOut):
				ui.message(_("The fade in and fade out are longer than the section."))
				return
			fades = []
			if fadeIn > 0:
				fades.append(f"afade=t=in:st=0:d={fadeIn:.3f}")
			if fadeOut > 0:
				fades.append(f"afade=t=out:st={max(0.0, end - start - fadeOut):.3f}:d={fadeOut:.3f}")
			if fades:
				command.extend(["-af", ",".join(fades)])
		command.extend(["-c:a", "pcm_s16le", "-progress", "pipe:1", "-nostats", previewPath])
		self.preview_busy = True
		self.preview_section_btn.Enable(False)
		self.status_label.SetLabel(_("Preparing preview..."))
		threading.Thread(target=self.run_section_preview, args=(command, previewPath, end - start), daemon=True).start()

	def run_section_preview(self, command, previewPath, lengthSeconds):
		def registerProcess(process):
			self.ffmpeg_process = process

		try:
			exitCode, stderrLines = runFfmpeg(command, self.preview_dir, lengthSeconds, lambda progress: None, registerProcess)
		except OSError as error:
			log.error("xTrack: could not run ffmpeg for the section preview: %s", error)
			self.post(self.on_section_preview_done, None, _("ffmpeg could not be started"))
			return
		if self.cancel_event.is_set():
			return
		if exitCode != 0 or not os.path.exists(previewPath):
			self.post(self.on_section_preview_done, None, summarizeFfmpegError(stderrLines))
			return
		self.post(self.on_section_preview_done, previewPath, "")

	def on_section_preview_done(self, previewPath, reason):
		self.preview_busy = False
		self.preview_section_btn.Enable(True)
		self.status_label.SetLabel("")
		if not previewPath:
			tones.beep(300, 300)
			ui.message(limitSpokenText(_("Preview failed: {}").format(reason)))
			return
		try:
			os.startfile(previewPath)
		except OSError as error:
			log.error("xTrack: could not open the section preview: %s", error)
			ui.message(_("The preview was created but could not be played."))
			return
		ui.message(_("Playing the section"))

	def cleanup_preview_dir(self):
		if self.preview_dir:
			shutil.rmtree(self.preview_dir, ignore_errors=True)
		self.preview_dir = ""

	def apply_encoding_rules(self, announce=False):
		"""Cross-fades and sections are cut and joined with filters, which only works when the audio is encoded again."""
		needsReencode = self.crossfade_checkbox.GetValue() or self.merge_mode != MODE_WHOLE
		if needsReencode:
			self.reencode_radio.SetValue(True)
			self.copy_radio.Enable(False)
			if announce:
				ui.message(_("Cross-fade requires re-encoding mode") if self.crossfade_checkbox.GetValue() else _("This mode requires re-encoding"))
		else:
			self.copy_radio.Enable(self.all_mp3)
		self.on_mode_change(None)

	def on_crossfade_changed(self, event):
		"""Enable/disable cross-fade controls based on checkbox state."""
		enabled = self.crossfade_checkbox.GetValue()
		self.crossfade_duration_ctrl.Enable(enabled)
		self.fade_in_next_ctrl.Enable(enabled)
		self.apply_encoding_rules(announce=enabled)

	def on_mode_change(self, event):
		self.quality_ctrl.Enable(self.reencode_radio.GetValue())

	def on_select(self, event):
		self.show_section_fields()
		event.Skip()

	def on_project_actions(self, event):
		chooseMenuAction(self, _("Project Actions"), [
			(_("Save Project..."), lambda: self.on_save_project(None)),
			(_("Load Project..."), lambda: self.on_load_project(None)),
		])

	def on_save_project(self, event):
		if self.is_running:
			ui.message(_("Please wait until the merge is finished."))
			return
		with wx.FileDialog(
			self, _("Save merge project"), defaultDir=self.output_path, defaultFile=PROJECT_DEFAULT_FILE_NAME,
			wildcard=_("Merge projects (*.json)|*.json|Text files (*.txt)|*.txt"), style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
		) as dialog:
			if dialog.ShowModal() != wx.ID_OK:
				return
			projectPath = dialog.GetPath()
		if writeProjectFile(projectPath, self.current_project()):
			self.remember_project_path(projectPath)
			ui.message(limitSpokenText(_("Project saved as {}").format(os.path.basename(projectPath))))
		else:
			tones.beep(300, 300)
			ui.message(_("The project could not be saved."))

	def current_project(self):
		"""The settings of the dialog as they are now, as a project."""
		sections = {file: list(self.get_section(file)) for file in self.selected_files}
		return buildProject(
			self.merge_mode, self.selected_files, sections, self.gap_ctrl.GetValue(), self.crossfade_checkbox.GetValue(),
			self.crossfade_duration_ctrl.GetValue(), self.fade_in_next_ctrl.GetValue(),
			self.quality_ctrl.GetStringSelection(), self.output_text.GetValue(),
		)

	def remember_project_path(self, projectPath):
		"""The project file that Update Project and the automatic backup on Merge write to."""
		self.project_path = projectPath
		self.update_project_btn.Enable(True)

	def update_project_file(self):
		"""Write the current settings to the project file; True when it worked."""
		if not self.project_path:
			return False
		if writeProjectFile(self.project_path, self.current_project()):
			return True
		tones.beep(300, 300)
		ui.message(_("The project could not be updated."))
		return False

	def on_update_project(self, event):
		if self.is_running:
			ui.message(_("Please wait until the merge is finished."))
			return
		if not self.project_path:
			ui.message(_("Save the project first, or load one."))
			return
		if self.update_project_file():
			ui.message(limitSpokenText(_("Project {} updated").format(os.path.basename(self.project_path))))

	def on_load_project(self, event):
		if self.is_running:
			ui.message(_("Please wait until the merge is finished."))
			return
		with wx.FileDialog(
			self, _("Load merge project"), defaultDir=self.output_path,
			wildcard=_("Merge projects (*.json;*.txt)|*.json;*.txt"), style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
		) as dialog:
			if dialog.ShowModal() != wx.ID_OK:
				return
			projectPath = dialog.GetPath()
		project = readProjectFile(projectPath)
		if project is None:
			tones.beep(300, 300)
			ui.message(_("This file is not a merge project."))
			return
		self.apply_project(project, os.path.basename(projectPath))
		self.remember_project_path(projectPath)

	def apply_project(self, project, projectName):
		files = [file for file in project["files"] if os.path.isfile(file)]
		missing = [os.path.basename(file) for file in project["files"] if not os.path.isfile(file)]
		if not files:
			ui.message(_("None of the tracks of this project could be found."))
			return
		self.selected_files = files
		self.all_mp3 = all(os.path.splitext(file)[1].lower() == ".mp3" for file in files)
		self.sections = {file: project["sections"][file] for file in files}
		newFiles = [file for file in files if file not in self.file_duration_seconds]
		if newFiles:
			threading.Thread(target=self.load_durations, args=(newFiles,), daemon=True).start()
		self.gap_ctrl.SetValue(project["gapSeconds"])
		if project["quality"]:
			self.quality_ctrl.SetStringSelection(project["quality"])
		if project["outputName"]:
			self.output_text.SetValue(project["outputName"])
		self.mode_combo.SetSelection(project["mode"])
		self.on_mode_changed(None)
		if self.merge_mode in (MODE_WHOLE, MODE_SECTIONS):
			self.crossfade_checkbox.SetValue(project["crossfadeEnabled"])
			self.crossfade_duration_ctrl.SetValue(project["crossfadeSeconds"])
			self.fade_in_next_ctrl.SetValue(project["fadeInNextSeconds"])
			self.on_crossfade_changed(None)
		self.file_list.SetSelection(0)
		self.show_section_fields()
		message = _("Project {name} loaded: {count} tracks").format(name=projectName, count=len(files))
		if missing:
			message += ". " + _("{count} tracks were not found and were left out: {names}").format(count=len(missing), names=", ".join(missing))
		ui.message(limitSpokenText(message))

	def on_file_list_context_menu(self, event):
		"""Move Up, Move Down, Delete Track, Add Track and Add Folder."""
		index = self.file_list.GetSelection()
		hasSelection = index != wx.NOT_FOUND and index < len(self.selected_files)
		idle = not self.is_running
		candidates = [
			(hasSelection and index > 0 and idle, _("Move Up"), self.on_up),
			(hasSelection and index < len(self.selected_files) - 1 and idle, _("Move Down"), self.on_down),
			(hasSelection and idle, _("Delete Track"), self.on_delete_track),
			(idle, _("Add Track..."), self.on_add_tracks),
			(idle, _("Add Folder..."), self.on_add_folder),
		]
		entries = [(label, lambda h=handler: h(None)) for enabled, label, handler in candidates if enabled]
		if not entries:
			return
		chooseMenuAction(self, _("Files to Merge"), entries)

	def on_file_list_key_down(self, event):
		if event.GetKeyCode() == wx.WXK_DELETE:
			self.on_delete_track(None)
			return
		event.Skip()

	def refresh_after_file_change(self):
		"""Everything that depends on which files are in the list."""
		self.all_mp3 = all(os.path.splitext(file)[1].lower() == ".mp3" for file in self.selected_files)
		if not self.all_mp3:
			self.reencode_radio.SetValue(True)
		self.apply_encoding_rules(announce=False)
		self.update_file_list()

	def add_files(self, newFiles):
		"""Add files to the end of the list (files already in it are skipped); returns how many were added."""
		added = [file for file in dict.fromkeys(newFiles) if file not in self.selected_files]
		if not added:
			return 0
		firstNewIndex = len(self.selected_files)
		self.selected_files.extend(added)
		unknown = [file for file in added if file not in self.file_duration_seconds]
		if unknown:
			threading.Thread(target=self.load_durations, args=(unknown,), daemon=True).start()
		self.refresh_after_file_change()
		self.file_list.SetSelection(firstNewIndex)
		self.show_section_fields()
		return len(added)

	def on_delete_track(self, event):
		index = self.file_list.GetSelection()
		if self.is_running or index == wx.NOT_FOUND or index >= len(self.selected_files):
			return
		removed = self.selected_files.pop(index)
		self.sections.pop(removed, None)
		self.refresh_after_file_change()
		remaining = len(self.selected_files)
		if remaining:
			self.file_list.SetSelection(min(index, remaining - 1))
			self.show_section_fields()
			ui.message(limitSpokenText(_("Removed {name}. {count} tracks left.").format(name=os.path.basename(removed), count=remaining)))
		else:
			ui.message(limitSpokenText(_("Removed {}. The list is empty.").format(os.path.basename(removed))))

	def on_add_tracks(self, event):
		if self.is_running:
			return
		wildcard = _("Audio files") + "|" + ";".join("*" + extension for extension in MERGE_AUDIO_EXTENSIONS)
		with wx.FileDialog(
			self, _("Add tracks"), defaultDir=self.output_path, wildcard=wildcard,
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
		) as dialog:
			if dialog.ShowModal() != wx.ID_OK:
				wx.CallAfter(self.file_list.SetFocus)
				return
			chosen = dialog.GetPaths()
		added = self.add_files(chosen)
		skipped = len(chosen) - added
		message = _("Added {} tracks").format(added) if added else _("No track was added")
		if skipped:
			message += ". " + _("{} were already in the list").format(skipped)
		ui.message(limitSpokenText(message))
		wx.CallAfter(self.file_list.SetFocus)

	def on_add_folder(self, event):
		if self.is_running:
			return
		with wx.DirDialog(self, _("Select a folder with audio files"), defaultPath=self.output_path) as dialog:
			if dialog.ShowModal() != wx.ID_OK:
				wx.CallAfter(self.file_list.SetFocus)
				return
			folderPath = dialog.GetPath()
		found = audioFilesInFolder(folderPath)
		if not found:
			ui.message(_("No audio files were found in that folder."))
			wx.CallAfter(self.file_list.SetFocus)
			return
		added = self.add_files(found)
		message = _("Added {count} tracks from {folder}").format(count=added, folder=os.path.basename(folderPath) or folderPath) if added else _("All tracks of that folder were already in the list")
		ui.message(limitSpokenText(message))
		wx.CallAfter(self.file_list.SetFocus)

	def on_up(self, event):
		selected_index = self.file_list.GetSelection()
		if self.is_running or selected_index == wx.NOT_FOUND or selected_index <= 0:
			return
		self.selected_files[selected_index - 1], self.selected_files[selected_index] = \
			self.selected_files[selected_index], self.selected_files[selected_index - 1]
		self.update_file_list()
		self.file_list.SetSelection(selected_index - 1)
		ui.message(_("Moved up to position {}").format(selected_index))

	def on_down(self, event):
		selected_index = self.file_list.GetSelection()
		if self.is_running or selected_index == wx.NOT_FOUND or selected_index >= len(self.selected_files) - 1:
			return
		self.selected_files[selected_index + 1], self.selected_files[selected_index] = \
			self.selected_files[selected_index], self.selected_files[selected_index + 1]
		self.update_file_list()
		self.file_list.SetSelection(selected_index + 1)
		ui.message(_("Moved down to position {}").format(selected_index + 2))

	def on_merge_or_pause(self, event):
		if self.is_running:
			self.toggle_pause()
		else:
			self.on_merge(event)

	def toggle_pause(self):
		process = self.ffmpeg_process
		if not process or process.poll() is not None:
			return
		suspend = not self.is_paused
		try:
			changed = setProcessSuspended(process, suspend)
		except (OSError, AttributeError) as error:
			log.error("xTrack: pause/resume failed: %s", error)
			changed = False
		if not changed:
			ui.message(_("Pause is not available for this merge"))
			return
		self.is_paused = suspend
		if suspend:
			self.merge_btn.SetLabel(_("Resume"))
			self.status_label.SetLabel(_("Paused."))
		else:
			self.merge_btn.SetLabel(_("Pause"))
			self.status_label.SetLabel(_("Resuming..."))
		ui.message(self.status_label.GetLabel())

	def on_merge(self, event):
		if self.is_running:
			return
		# Once a project file exists, pressing Merge also keeps it up to date, so the latest work is always saved.
		if self.merge_mode != MODE_WHOLE and self.project_path and self.update_project_file():
			ui.message(_("Project updated"))
		if len(self.selected_files) < 2:
			ui.message(_("Please select at least 2 files"))
			return
		outputName = self.output_text.GetValue().strip()
		if not outputName:
			ui.message(_("Please enter an output filename."))
			return
		if any(char in INVALID_FILENAME_CHARS for char in outputName):
			ui.message(_("The output filename contains characters that are not allowed."))
			return
		if not outputName.lower().endswith('.mp3'):
			outputName += '.mp3'

		ffmpegPath = os.path.join(self.libs_path, "ffmpeg.exe")
		if not os.path.exists(ffmpegPath):
			ui.message(_("ffmpeg.exe not found"))
			return
		for file in self.selected_files:
			if not os.path.exists(file):
				ui.message(limitSpokenText(_("File not found: {}").format(os.path.basename(file))))
				return

		useCrossfade = self.crossfade_checkbox.GetValue() and self.reencode_radio.GetValue() and self.merge_mode in (MODE_WHOLE, MODE_SECTIONS)
		reencode = self.reencode_radio.GetValue()
		gapSeconds = self.gap_ctrl.GetValue() if self.merge_mode == MODE_GAP else 0
		crossfadeSeconds = self.crossfade_duration_ctrl.GetValue()

		sections = None
		if self.section_mode:
			sections = self.collect_sections()
			if sections is None:
				return
		if useCrossfade:
			for index, file in enumerate(self.selected_files):
				knownSeconds = (sections[index][1] - sections[index][0]) if sections else self.file_duration_seconds.get(file, 0)
				if 0 < knownSeconds <= crossfadeSeconds:
					ui.message(limitSpokenText(
						_("{} is shorter than the cross-fade duration").format(os.path.basename(file))
					))
					return

		if sections:
			knownTotal = sum(entry[1] - entry[0] for entry in sections)
		else:
			knownTotal = sum(self.file_duration_seconds.get(file, 0) for file in self.selected_files)
		if useCrossfade:
			knownTotal -= crossfadeSeconds * (len(self.selected_files) - 1)
		knownTotal += gapSeconds * (len(self.selected_files) - 1)

		# A name that already exists, including one of the source files, is
		# never overwritten: ffmpeg would otherwise read from a file it is rewriting.
		finalPath = os.path.join(self.output_path, get_unique_filename(self.output_path, outputName[:-4], "mp3"))
		partPath = partPathFor(finalPath)
		quality = self.quality_ctrl.GetStringSelection().split()[0]

		concatPath = None
		try:
			if useCrossfade or sections or gapSeconds > 0:
				command = self.build_graph_command(ffmpegPath, partPath, quality, sections, useCrossfade, gapSeconds)
			else:
				concatPath = self.write_concat_file()
				command = self.build_concat_command(ffmpegPath, concatPath, partPath, quality, reencode)
		except OSError as error:
			log.error("xTrack: could not write the merge file list: %s", error)
			ui.message(_("Failed to create file list"))
			return

		self.is_running = True
		self.is_paused = False
		self.cancel_event.clear()
		self.last_announced_progress = -1
		self.merge_btn.SetLabel(_("Pause"))
		self.cancel_btn.Enable(False)
		self.status_label.SetLabel(_("Starting merge..."))
		self.progress_bar.SetValue(0)
		threading.Thread(
			target=self.run_merge, args=(command, concatPath, partPath, finalPath, knownTotal), daemon=True
		).start()

	def run_merge(self, command, concatPath, partPath, finalPath, totalSeconds):
		def registerProcess(process):
			self.ffmpeg_process = process

		try:
			exitCode, stderrLines = runFfmpeg(
				command, self.output_path, totalSeconds,
				lambda progress: self.post(self.update_progress, progress), registerProcess,
			)
		except OSError as error:
			log.error("xTrack: could not run ffmpeg for the merge: %s", error)
			removeQuietly(partPath)
			removeQuietly(concatPath)
			self.post(self.on_failure, _("ffmpeg could not be started"))
			return
		removeQuietly(concatPath)

		if self.cancel_event.is_set():
			removeQuietly(partPath)
			return
		if exitCode != 0:
			log.error(
				"xTrack: merge failed (exit code %s): %s",
				exitCode, " | ".join(line.strip() for line in stderrLines),
			)
			removeQuietly(partPath)
			self.post(self.on_failure, summarizeFfmpegError(stderrLines))
			return
		try:
			os.replace(partPath, finalPath)
		except OSError as error:
			log.error("xTrack: could not move %s to %s: %s", partPath, finalPath, error)
			removeQuietly(partPath)
			self.post(self.on_failure, _("the output file could not be written"))
			return
		self.post(self.on_success, finalPath)

	def write_concat_file(self):
		handle, concatPath = tempfile.mkstemp(prefix="xtrack_concat_", suffix=".txt")
		with os.fdopen(handle, 'w', encoding='utf-8') as concatFile:
			for file in self.selected_files:
				escaped = file.replace("\\", "/").replace("'", "'\\''")
				concatFile.write(f"file '{escaped}'\n")
		return concatPath

	def build_concat_command(self, ffmpegPath, concatPath, partPath, quality, reencode):
		command = [
			ffmpegPath, "-hide_banner",
			"-f", "concat",
			"-safe", "0",
			"-protocol_whitelist", "file,crypto,data,pipe",
			"-i", concatPath,
			"-progress", "pipe:1",
			"-nostats",
			"-y",
		]
		if reencode:
			command.extend(["-c:a", "libmp3lame", "-b:a", f"{quality}k"])
		else:
			command.extend(["-c", "copy"])
		command.append(partPath)
		return command

	def collect_sections(self):
		"""The (start, end) of every track in list order, or None after saying what is wrong with one of them."""
		current = self.selected_file()
		if current is not None:
			problem = self.section_text_problem(current)
			if problem:
				ui.message(limitSpokenText(problem))
				return None
			self.set_section_times(current, parseSectionTime(self.section_start_ctrl.GetValue()), parseSectionTime(self.section_end_ctrl.GetValue()))
		collected = []
		for file in self.selected_files:
			start, end, fadeIn, fadeOut = self.get_section(file)
			if end is None:
				ui.message(limitSpokenText(_("The length of {} is not known yet. Please wait a moment.").format(os.path.basename(file))))
				return None
			problem = self.section_problem(file, start, end)
			if problem:
				ui.message(limitSpokenText(problem))
				return None
			if self.merge_mode == MODE_FADES:
				if not checkFades(end - start, fadeIn, fadeOut):
					ui.message(limitSpokenText(_("The fade in and fade out of {} are longer than its section.").format(os.path.basename(file))))
					return None
				collected.append((start, end, fadeIn, fadeOut))
			else:
				collected.append((start, end))
		return collected

	def build_graph_command(self, ffmpegPath, partPath, quality, sections, useCrossfade, gapSeconds=0):
		"""Merge with a filter graph: a cross-fade chain, sections cut out of the tracks, fades, or silence between the tracks."""
		command = [ffmpegPath, "-hide_banner"]
		for file in self.selected_files:
			command.extend(["-i", file])
		if useCrossfade:
			filterGraph = buildCrossfadeFilter(
				len(self.selected_files), self.crossfade_duration_ctrl.GetValue(), self.fade_in_next_ctrl.GetValue(), sections
			)
		else:
			filterGraph = buildConcatFilter(len(self.selected_files), sections, gapSeconds)
		command.extend([
			"-filter_complex", filterGraph,
			"-map", "[out]",
			"-progress", "pipe:1",
			"-nostats",
			"-y",
			"-c:a", "libmp3lame",
			"-b:a", f"{quality}k",
			partPath,
		])
		return command

	def update_progress(self, progress):
		self.progress_bar.SetValue(progress)
		self.status_label.SetLabel(_("Merging: {}%").format(progress))
		if progress % 10 == 0 and progress > 0 and progress != self.last_announced_progress:
			self.last_announced_progress = progress
			ui.message(_("{} percent").format(progress))

	def finish_run(self):
		self.is_running = False
		self.is_paused = False
		self.merge_btn.SetLabel(_("Merge"))
		self.cancel_btn.Enable(True)

	def on_success(self, finalPath):
		self.finish_run()
		self.progress_bar.SetValue(100)
		self.status_label.SetLabel(_("Merge complete!"))
		tones.beep(1000, 300)
		ui.message(limitSpokenText(_("Merge successful. Saved as: {}").format(os.path.basename(finalPath))))
		# The dialog stays open with all its settings, so a change can be made and merged again.

	def on_failure(self, reason):
		self.finish_run()
		self.status_label.SetLabel(_("Merge failed."))
		tones.beep(300, 300)
		ui.message(limitSpokenText(_("Merge failed: {}").format(reason)))

	def stop_merge(self):
		self.is_closed = True
		self.cancel_event.set()
		self.cleanup_preview_dir()
		process = self.ffmpeg_process
		if not process or process.poll() is not None:
			return
		try:
			if self.is_paused:
				setProcessSuspended(process, False)
			process.terminate()
		except (OSError, AttributeError) as error:
			log.debugWarning("xTrack: could not stop ffmpeg: %s", error)

	def on_cancel(self, event):
		self.stop_merge()
		self.EndModal(wx.ID_CANCEL)

	def on_close(self, event):
		self.stop_merge()
		self.EndModal(wx.ID_CANCEL)
