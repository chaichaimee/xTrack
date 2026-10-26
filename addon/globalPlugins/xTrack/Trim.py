# Trim.py

import wx
import os
import shutil
import subprocess
import tempfile
import threading
import tones
from gui import guiHelper
import ui
from logHandler import log
import addonHandler
from .xTrackCore import get_config_path, load_config, save_config, get_file_duration
from .ffmpegSupport import (
	limitSpokenText, summarizeFfmpegError, removeQuietly, partPathFor, runFfmpeg,
	parseTimeText,
)

addonHandler.initTranslation()

INVALID_FILENAME_CHARS = '<>:"/\\|?*'
# The Preview button only ever plays the first seconds of the part that will be kept.
PREVIEW_SECONDS = 15
# Converted files go into this folder next to the originals unless the user opts out.
TRIMMED_SUBFOLDER_NAME = "Trimmed"
# Dialog choice -> (ffmpeg encoder, codec name ffprobe reports for it)
AUDIO_CODEC_MAP = {
	"AAC (Recommended)": ("aac", "aac"),
	"MP3": ("libmp3lame", "mp3"),
	"Vorbis": ("libvorbis", "vorbis"),
}
SOURCE_ENCODERS = {"aac": "aac", "mp3": "libmp3lame", "vorbis": "libvorbis", "opus": "libopus"}
PCM_AUDIO_CODECS = ("pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le")
WEBM_VIDEO_CODECS = ("vp8", "vp9", "av1")
WEBM_AUDIO_CODECS = ("vorbis", "opus")
VIDEO_FORMAT_MAP = {
	"MP4 (Copy Original Quality)": "mp4",
	"MKV (Copy Original Quality)": "mkv",
	"MOV (Copy Original Quality)": "mov",
	"AVI (Copy Original Quality)": "avi",
	"WebM (Copy Original Quality)": "webm",
}


def probeStreamCodec(libs_path, file_path, selector):
	"""Codec name of the first stream matching selector ("v:0" or "a:0"), or "" if there is none."""
	ffprobe_path = os.path.join(libs_path, "ffprobe.exe")
	if not os.path.exists(ffprobe_path):
		return ""
	command = [
		ffprobe_path, "-v", "error", "-select_streams", selector,
		"-show_entries", "stream=codec_name", "-of", "default=noprint_wrappers=1:nokey=1", file_path,
	]
	try:
		result = subprocess.run(
			command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
			creationflags=subprocess.CREATE_NO_WINDOW, text=True, encoding="utf-8", errors="ignore", timeout=15,
		)
	except (OSError, subprocess.SubprocessError) as error:
		log.error("xTrack: could not probe %s of %s: %s", selector, file_path, error)
		return ""
	if result.returncode != 0:
		return ""
	lines = result.stdout.strip().splitlines()
	return lines[0].strip().lower() if lines else ""


class ItemSkipped(Exception):
	"""One file of a multi-file trim cannot be processed; the rest of the queue goes on."""


class TrimAudioVideoDialog(wx.Dialog):
	"""Dialog for trimming audio and video files using FFmpeg."""
	def __init__(self, parent, selected_files, libs_path):
		super().__init__(parent, title=_("Trim Audio/Video File"))
		if not selected_files:
			raise ValueError("No file was selected.")
		self.items = [self.new_item(path) for path in selected_files]
		self.current_index = 0
		self.sync_checkbox = None
		# The first Start Time the user enters is kept and offered to every other file.
		self.remembered_start = ""
		self.libs_path = libs_path
		self.config_path = get_config_path()
		self.preview_dir = ""
		self.ffmpeg_process = None
		self.is_running = False
		self.is_previewing = False
		self.preview_length = PREVIEW_SECONDS
		self.is_closed = False
		self.cancel_event = threading.Event()
		self.init_ui()
		self.update_title()

		self.Bind(wx.EVT_CLOSE, self.on_close)
		threading.Thread(target=self.load_all_file_info, daemon=True).start()

	@staticmethod
	def new_item(path):
		return {
			"path": path,
			"duration_seconds": 0,
			"duration_label": "",
			"video_codec": "",
			"audio_codec": "",
			"info_ready": False,
			"start": "",
			"start_auto": False,
			"end": "",
			"fade_in_start": "0",
			"fade_in_end": "3",
			"fade_out_start": "",
			"fade_out_end": "",
		}

	@property
	def selected_file(self):
		return self.items[self.current_index]["path"]

	@property
	def file_duration(self):
		return self.items[self.current_index]["duration_label"]

	@property
	def file_duration_seconds(self):
		return self.items[self.current_index]["duration_seconds"]

	@property
	def info_ready(self):
		return self.items[self.current_index]["info_ready"]

	def update_title(self):
		if len(self.items) > 1:
			self.SetTitle(_("Trim Audio/Video: {} files").format(len(self.items)))
		else:
			self.SetTitle(_("Trim Audio/Video: {}").format(os.path.basename(self.selected_file)))

	def save_controls_to_item(self, index):
		item = self.items[index]
		startText = self.start_time_ctrl.GetValue()
		if startText != item["start"]:
			item["start"] = startText
			item["start_auto"] = False
		if startText and not self.remembered_start and not item["start_auto"]:
			self.remembered_start = startText
			self.offer_remembered_start(index)
		item["end"] = self.end_time_ctrl.GetValue()
		item["fade_in_start"] = self.fade_in_start_ctrl.GetValue()
		item["fade_in_end"] = self.fade_in_end_ctrl.GetValue()
		item["fade_out_start"] = self.fade_out_start_ctrl.GetValue()
		item["fade_out_end"] = self.fade_out_end_ctrl.GetValue()

	def offer_remembered_start(self, enteredIndex):
		"""Files whose Start Time was never typed in start from the remembered value."""
		for position, other in enumerate(self.items):
			if position == enteredIndex or other["start"]:
				continue
			other["start"] = self.remembered_start
			other["start_auto"] = True

	def load_item_into_controls(self, index):
		item = self.items[index]
		self.start_time_ctrl.ChangeValue(item["start"])
		self.end_time_ctrl.ChangeValue(item["end"])
		self.fade_in_start_ctrl.ChangeValue(item["fade_in_start"])
		self.fade_in_end_ctrl.ChangeValue(item["fade_in_end"])
		self.fade_out_start_ctrl.ChangeValue(item["fade_out_start"])
		self.fade_out_end_ctrl.ChangeValue(item["fade_out_end"])
		self.update_duration_label()

	def sync_all_times(self):
		return self.sync_checkbox is not None and self.sync_checkbox.GetValue()

	def apply_common_times(self):
		"""Copy the start and end shown on screen to every file; used while the sync checkbox is checked."""
		startText = self.start_time_ctrl.GetValue()
		endText = self.end_time_ctrl.GetValue()
		for item in self.items:
			item["start"] = startText
			item["start_auto"] = False
			item["end"] = endText

	def end_counts_back(self):
		"""While syncing, End Time is the amount cut off the end of every file (0 leaves the end untouched)."""
		return self.sync_all_times()

	def update_time_labels(self):
		syncOn = self.sync_all_times()
		if self.start_label is not None:
			self.start_label.SetLabel(_("Start Time (cut off this much from the beginning of each file):") if syncOn else _("Start Time:"))
		if self.end_label is not None:
			self.end_label.SetLabel(_("End Time (cut off this much from the end of each file):") if syncOn else _("End Time:"))

	def on_sync_changed(self, event):
		syncOn = self.sync_all_times()
		self.update_time_labels()
		endText = self.end_time_ctrl.GetValue()
		if syncOn:
			# This file's own duration as the automatic end would mean "cut everything".
			if endText in ("", self.file_duration):
				self.end_time_ctrl.ChangeValue("0")
			self.apply_common_times()
		else:
			# An amount cut off the end means nothing for a single file: every file returns to its own end.
			for item in self.items:
				item["end"] = item["duration_label"]
			self.end_time_ctrl.ChangeValue(self.file_duration)
		self.update_duration_label()

	def select_item(self, index):
		if index == self.current_index:
			return
		self.save_controls_to_item(self.current_index)
		commonStart = self.start_time_ctrl.GetValue()
		commonEnd = self.end_time_ctrl.GetValue()
		self.current_index = index
		self.load_item_into_controls(index)
		if self.sync_all_times():
			# Start and end stay shared; fades still belong to each file.
			self.start_time_ctrl.ChangeValue(commonStart)
			self.end_time_ctrl.ChangeValue(commonEnd)
			self.update_duration_label()
		if self.file_listbox is not None:
			self.file_listbox.SetSelection(index)

	def on_file_selected(self, event):
		index = self.file_listbox.GetSelection()
		if index == wx.NOT_FOUND or index == self.current_index:
			return
		self.select_item(index)
		if not self.items[index]["info_ready"]:
			ui.message(_("Still reading the file information, please try again in a moment."))

	def post(self, handler, *args):
		wx.CallAfter(self.run_if_open, handler, *args)

	def run_if_open(self, handler, *args):
		if self.is_closed:
			return
		handler(*args)

	def load_config(self):
		return load_config(self.config_path)

	def save_config(self, config_data):
		save_config(self.config_path, config_data)

	def load_all_file_info(self):
		"""Duration and stream codecs of every file are read one after another, off the main thread."""
		for index, item in enumerate(self.items):
			if self.is_closed:
				return
			durationSeconds, durationLabel = get_file_duration(self.libs_path, item["path"])
			item["video_codec"] = probeStreamCodec(self.libs_path, item["path"], "v:0")
			item["audio_codec"] = probeStreamCodec(self.libs_path, item["path"], "a:0")
			item["duration_seconds"] = durationSeconds
			item["duration_label"] = durationLabel if durationSeconds > 0 else ""
			item["info_ready"] = True
			self.post(self.on_item_info_loaded, index)

	def on_item_info_loaded(self, index):
		item = self.items[index]
		isCurrent = index == self.current_index
		if item["duration_seconds"] <= 0:
			log.error("xTrack: could not read the duration of %s", item["path"])
			if isCurrent:
				ui.message(_("The duration of this file could not be read."))
				self.update_duration_label()
			return
		# The end time always starts at the file duration; saved values are never reused.
		if isCurrent:
			if not self.end_time_ctrl.GetValue() and not self.sync_all_times():
				self.end_time_ctrl.ChangeValue(item["duration_label"])
			self.update_duration_label()
		elif not item["end"] and not self.sync_all_times():
			item["end"] = item["duration_label"]

	def init_ui(self):
		main_sizer = wx.BoxSizer(wx.VERTICAL)
		isBatch = len(self.items) > 1
		file_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Files to Trim") if isBatch else _("Selected File"))
		if isBatch:
			self.file_listbox = wx.ListBox(
				self, choices=[os.path.basename(item["path"]) for item in self.items], style=wx.LB_SINGLE,
			)
			self.file_listbox.SetSelection(0)
			self.file_listbox.Bind(wx.EVT_LISTBOX, self.on_file_selected)
			file_sizer.Add(self.file_listbox, 1, wx.EXPAND | wx.ALL, 5)
		else:
			self.file_listbox = None
			self.file_label = wx.StaticText(self, label=os.path.basename(self.selected_file))
			file_sizer.Add(self.file_label, 0, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(file_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Output Type Selection (Audio/Video)
		type_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Output Type"))
		self.audio_radio = wx.RadioButton(self, label=_("Audio"), style=wx.RB_GROUP)
		self.video_radio = wx.RadioButton(self, label=_("Video"))
		type_sizer.Add(self.audio_radio, 0, wx.ALL, 5)
		type_sizer.Add(self.video_radio, 0, wx.ALL, 5)
		main_sizer.Add(type_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		time_sizer = wx.BoxSizer(wx.VERTICAL)
		time_helper = guiHelper.BoxSizerHelper(self, sizer=time_sizer)
		self.start_time_ctrl = time_helper.addLabeledControl(_("Start Time:"), wx.TextCtrl, value="")
		self.end_time_ctrl = time_helper.addLabeledControl(_("End Time:"), wx.TextCtrl, value="")
		if isBatch:
			self.sync_checkbox = wx.CheckBox(self, label=_("Use the same start and end time for all files"))
			self.sync_checkbox.SetValue(False)
			self.sync_checkbox.SetToolTip(
				_("When checked, Start Time is cut off the beginning and End Time is cut off the end of every file, whatever its length. 0 leaves that side untouched.")
			)
			self.sync_checkbox.Bind(wx.EVT_CHECKBOX, self.on_sync_changed)
			time_sizer.Add(self.sync_checkbox, 0, wx.ALL, 5)
		else:
			self.sync_checkbox = None
		startLabel = self.start_time_ctrl.GetPrevSibling()
		self.start_label = startLabel if isinstance(startLabel, wx.StaticText) else None
		endLabel = self.end_time_ctrl.GetPrevSibling()
		self.end_label = endLabel if isinstance(endLabel, wx.StaticText) else None
		main_sizer.Add(time_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Audio Settings Panel
		self.audio_panel = wx.Panel(self)
		audio_sizer = wx.BoxSizer(wx.VERTICAL)
		
		# Audio Format Selection
		audio_format_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.audio_panel, label=_("Audio Format"))
		self.mp3_radio = wx.RadioButton(self.audio_panel, label="MP3", style=wx.RB_GROUP)
		self.wav_radio = wx.RadioButton(self.audio_panel, label="WAV")
		audio_format_sizer.Add(self.mp3_radio, 0, wx.ALL, 5)
		audio_format_sizer.Add(self.wav_radio, 0, wx.ALL, 5)
		audio_sizer.Add(audio_format_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Audio Quality
		audio_quality_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.audio_panel, label=_("Audio Quality"))
		choices = ["320 kbps", "256 kbps", "192 kbps", "128 kbps"]
		self.audio_quality_ctrl = wx.ComboBox(self.audio_panel, choices=choices, style=wx.CB_READONLY)
		self.audio_quality_ctrl.SetStringSelection("320 kbps")
		audio_quality_sizer.Add(self.audio_quality_ctrl, 0, wx.ALL, 5)
		audio_sizer.Add(audio_quality_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Fade Settings for Audio
		self.fade_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.audio_panel, label=_("Fade Settings"))
		
		# Fade enable checkbox
		fade_enable_sizer = wx.BoxSizer(wx.HORIZONTAL)
		self.fade_checkbox = wx.CheckBox(self.audio_panel, label=_("Enable Fade In/Out"))
		fade_enable_sizer.Add(self.fade_checkbox, 0, wx.ALL, 5)
		self.fade_sizer.Add(fade_enable_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Fade in controls panel
		self.fade_in_panel = wx.Panel(self.audio_panel)
		fade_in_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.fade_in_panel, label=_("Fade In"))
		fade_in_grid = wx.FlexGridSizer(2, 2, 5, 5)
		fade_in_grid.Add(wx.StaticText(self.fade_in_panel, label=_("Start Time:")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.fade_in_start_ctrl = wx.TextCtrl(self.fade_in_panel, value="0")
		fade_in_grid.Add(self.fade_in_start_ctrl, 1, wx.EXPAND)
		fade_in_grid.Add(wx.StaticText(self.fade_in_panel, label=_("End Time (100% volume):")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.fade_in_end_ctrl = wx.TextCtrl(self.fade_in_panel, value="3")
		fade_in_grid.Add(self.fade_in_end_ctrl, 1, wx.EXPAND)
		fade_in_grid.AddGrowableCol(1)
		fade_in_sizer.Add(fade_in_grid, 1, wx.EXPAND | wx.ALL, 5)
		self.fade_in_panel.SetSizer(fade_in_sizer)
		self.fade_sizer.Add(self.fade_in_panel, 0, wx.EXPAND | wx.ALL, 5)
		
		# Fade out controls panel
		self.fade_out_panel = wx.Panel(self.audio_panel)
		fade_out_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.fade_out_panel, label=_("Fade Out"))
		fade_out_grid = wx.FlexGridSizer(2, 2, 5, 5)
		fade_out_grid.Add(wx.StaticText(self.fade_out_panel, label=_("Start Time (100% volume):")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.fade_out_start_ctrl = wx.TextCtrl(self.fade_out_panel)
		fade_out_grid.Add(self.fade_out_start_ctrl, 1, wx.EXPAND)
		fade_out_grid.Add(wx.StaticText(self.fade_out_panel, label=_("End Time:")), 0, wx.ALIGN_CENTER_VERTICAL)
		self.fade_out_end_ctrl = wx.TextCtrl(self.fade_out_panel)
		fade_out_grid.Add(self.fade_out_end_ctrl, 1, wx.EXPAND)
		fade_out_grid.AddGrowableCol(1)
		fade_out_sizer.Add(fade_out_grid, 1, wx.EXPAND | wx.ALL, 5)
		self.fade_out_panel.SetSizer(fade_out_sizer)
		self.fade_sizer.Add(self.fade_out_panel, 0, wx.EXPAND | wx.ALL, 5)
		
		audio_sizer.Add(self.fade_sizer, 0, wx.EXPAND | wx.ALL, 5)
		self.audio_panel.SetSizer(audio_sizer)
		main_sizer.Add(self.audio_panel, 0, wx.EXPAND | wx.ALL, 5)
		
		# Video Settings Panel
		self.video_panel = wx.Panel(self)
		video_sizer = wx.BoxSizer(wx.VERTICAL)
		
		# Video Format Selection
		video_format_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.video_panel, label=_("Video Format"))
		video_format_choices = ["MP4 (Copy Original Quality)", "MKV (Copy Original Quality)", "MOV (Copy Original Quality)", "AVI (Copy Original Quality)", "WebM (Copy Original Quality)"]
		self.video_format_ctrl = wx.ComboBox(self.video_panel, choices=video_format_choices, style=wx.CB_READONLY)
		self.video_format_ctrl.SetStringSelection("MP4 (Copy Original Quality)")
		video_format_sizer.Add(self.video_format_ctrl, 0, wx.ALL, 5)
		video_sizer.Add(video_format_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Audio settings for video
		video_audio_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.video_panel, label=_("Audio Settings"))
		
		# Sample rate selection
		sample_rate_sizer = wx.BoxSizer(wx.HORIZONTAL)
		sample_rate_label = wx.StaticText(self.video_panel, label=_("Sample Rate:"))
		sample_rate_sizer.Add(sample_rate_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		sample_rate_choices = ["44100 Hz", "48000 Hz", "Keep Original"]
		self.sample_rate_ctrl = wx.ComboBox(self.video_panel, choices=sample_rate_choices, style=wx.CB_READONLY)
		self.sample_rate_ctrl.SetStringSelection("Keep Original")
		sample_rate_sizer.Add(self.sample_rate_ctrl, 1, wx.EXPAND | wx.ALL, 5)
		video_audio_sizer.Add(sample_rate_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Audio channels
		channels_sizer = wx.BoxSizer(wx.HORIZONTAL)
		channels_label = wx.StaticText(self.video_panel, label=_("Audio Channels:"))
		channels_sizer.Add(channels_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		channels_choices = ["Stereo (2 channels)", "Mono (1 channel)", "Keep Original"]
		self.channels_ctrl = wx.ComboBox(self.video_panel, choices=channels_choices, style=wx.CB_READONLY)
		self.channels_ctrl.SetStringSelection("Keep Original")
		channels_sizer.Add(self.channels_ctrl, 1, wx.EXPAND | wx.ALL, 5)
		video_audio_sizer.Add(channels_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Audio codec selection
		codec_sizer = wx.BoxSizer(wx.HORIZONTAL)
		codec_label = wx.StaticText(self.video_panel, label=_("Audio Codec:"))
		codec_sizer.Add(codec_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		codec_choices = ["AAC (Recommended)", "MP3", "Vorbis", "Keep Original"]
		self.audio_codec_ctrl = wx.ComboBox(self.video_panel, choices=codec_choices, style=wx.CB_READONLY)
		self.audio_codec_ctrl.SetStringSelection("AAC (Recommended)")
		codec_sizer.Add(self.audio_codec_ctrl, 1, wx.EXPAND | wx.ALL, 5)
		video_audio_sizer.Add(codec_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		video_sizer.Add(video_audio_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Quality preservation note
		quality_note = wx.StaticText(self.video_panel, label=_("Note: Video trimming preserves original quality by default. No re-encoding is performed."))
		quality_note.Wrap(400)
		video_sizer.Add(quality_note, 0, wx.EXPAND | wx.ALL, 5)
		
		self.video_panel.SetSizer(video_sizer)
		main_sizer.Add(self.video_panel, 0, wx.EXPAND | wx.ALL, 5)
		self.video_panel.Hide()
		
		# Output filename section - moved before preview section
		output_sizer = wx.BoxSizer(wx.HORIZONTAL)
		output_label = wx.StaticText(self, label=_("Output Names (automatic):") if isBatch else _("Output Filename:"))
		output_sizer.Add(output_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.output_text = wx.TextCtrl(self, value=os.path.splitext(os.path.basename(self.selected_file))[0] + "_trimmed")
		output_sizer.Add(self.output_text, 1, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(output_sizer, 0, wx.EXPAND | wx.ALL, 5)
		if isBatch:
			self.output_text.SetValue(_("original name + _trimmed"))
			self.output_text.Enable(False)

		self.subfolder_checkbox = wx.CheckBox(self, label=_("Save trimmed files in a separate subfolder"))
		self.subfolder_checkbox.SetValue(False)
		self.subfolder_checkbox.SetToolTip(
			_("Unchecked: the trimmed files are saved together with the originals. Checked: they are saved in a subfolder named {}.").format(TRIMMED_SUBFOLDER_NAME)
		)
		main_sizer.Add(self.subfolder_checkbox, 0, wx.ALL, 5)
		
		# Preview section - moved after output filename
		preview_sizer = wx.BoxSizer(wx.HORIZONTAL)
		self.duration_label = wx.StaticText(self, label=_("File Duration: Calculating..."))
		preview_sizer.Add(self.duration_label, 1, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.preview_btn = wx.Button(self, label=_("Preview (first {} seconds)").format(PREVIEW_SECONDS))
		self.preview_btn.Bind(wx.EVT_BUTTON, self.on_preview)
		preview_sizer.Add(self.preview_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.preview_end_btn = wx.Button(self, label=_("Preview (end {} seconds)").format(PREVIEW_SECONDS))
		self.preview_end_btn.Bind(wx.EVT_BUTTON, self.on_preview_end)
		preview_sizer.Add(self.preview_end_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		main_sizer.Add(preview_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		self.progress_bar = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
		main_sizer.Add(self.progress_bar, 0, wx.EXPAND | wx.ALL, 5)
		
		self.status_label = wx.StaticText(self, label="")
		main_sizer.Add(self.status_label, 0, wx.EXPAND | wx.ALL, 5)
		
		btn_sizer = wx.StdDialogButtonSizer()
		self.trim_btn = wx.Button(self, wx.ID_OK, label=_("Trim"))
		self.trim_btn.Bind(wx.EVT_BUTTON, self.on_trim)
		btn_sizer.AddButton(self.trim_btn)
		self.cancel_btn = wx.Button(self, wx.ID_CANCEL, label=_("Cancel"))
		self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
		btn_sizer.AddButton(self.cancel_btn)
		btn_sizer.Realize()
		main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
		
		self.SetSizer(main_sizer)
		self.Fit()
		
		# Bind events
		self.audio_radio.Bind(wx.EVT_RADIOBUTTON, self.on_output_type_change)
		self.video_radio.Bind(wx.EVT_RADIOBUTTON, self.on_output_type_change)
		self.mp3_radio.Bind(wx.EVT_RADIOBUTTON, self.on_format_change)
		self.wav_radio.Bind(wx.EVT_RADIOBUTTON, self.on_format_change)
		self.start_time_ctrl.Bind(wx.EVT_TEXT, self.on_time_control_text)
		self.end_time_ctrl.Bind(wx.EVT_TEXT, self.on_time_control_text)
		self.fade_in_start_ctrl.Bind(wx.EVT_TEXT, self.on_fade_text)
		self.fade_in_end_ctrl.Bind(wx.EVT_TEXT, self.on_fade_text)
		self.fade_out_start_ctrl.Bind(wx.EVT_TEXT, self.on_fade_text)
		self.fade_out_end_ctrl.Bind(wx.EVT_TEXT, self.on_fade_text)
		self.fade_checkbox.Bind(wx.EVT_CHECKBOX, self.on_fade_checkbox)
		
		config_data = self.load_config()
		last_output_type = config_data.get("TrimLastOutputType", "audio")
		last_format = config_data.get("TrimLastFormat", "mp3")
		last_quality = config_data.get("TrimLastQuality", "320")
		last_video_format = config_data.get("TrimLastVideoFormat", "MP4 (Copy Original Quality)")
		last_sample_rate = config_data.get("TrimLastSampleRate", "Keep Original")
		last_channels = config_data.get("TrimLastChannels", "Keep Original")
		last_audio_codec = config_data.get("TrimLastAudioCodec", "AAC (Recommended)")
		last_fade_enabled = config_data.get("TrimLastFadeEnabled", False)
		self.subfolder_checkbox.SetValue(bool(config_data.get("TrimLastSubfolder", False)))
		
		if last_output_type == "video":
			self.video_radio.SetValue(True)
			self.video_format_ctrl.SetStringSelection(last_video_format)
			self.sample_rate_ctrl.SetStringSelection(last_sample_rate)
			self.channels_ctrl.SetStringSelection(last_channels)
			self.audio_codec_ctrl.SetStringSelection(last_audio_codec)
		else:
			self.audio_radio.SetValue(True)
			if last_format == "wav":
				self.wav_radio.SetValue(True)
			else:
				self.mp3_radio.SetValue(True)
			self.audio_quality_ctrl.SetStringSelection(f"{last_quality} kbps")
		
		# Set fade checkbox state
		self.fade_checkbox.SetValue(last_fade_enabled)
		
		self.on_output_type_change(None)
		self.on_format_change(None)
		# Call on_fade_checkbox to set initial state
		self.on_fade_checkbox(None)
		
	def on_output_type_change(self, event):
		"""Show/hide audio and video panels based on output type selection."""
		is_audio = self.audio_radio.GetValue()
		self.audio_panel.Show(is_audio)
		self.video_panel.Show(not is_audio)
		self.Layout()
		self.Fit()
		
	def on_format_change(self, event):
		self.audio_quality_ctrl.Enable(self.mp3_radio.GetValue())
		
	def on_fade_checkbox(self, event):
		"""Show/hide fade controls based on checkbox state."""
		fade_enabled = self.fade_checkbox.GetValue()
		
		# Show/hide fade in and fade out panels
		self.fade_in_panel.Show(fade_enabled)
		self.fade_out_panel.Show(fade_enabled)
		
		self.Layout()
		self.Fit()
		
	def on_time_control_text(self, event):
		if self.sync_all_times():
			self.apply_common_times()
		wx.CallAfter(self.update_duration_label)
		event.Skip()
		
	def on_fade_text(self, event):
		wx.CallAfter(self.update_duration_label)
		event.Skip()
		

	def update_duration_label(self):
		if not self or not self.IsShown():
			return
		if self.file_duration_seconds > 0:
			duration_str = self.file_duration
		else:
			duration_str = _("unknown")
		try:
			start_seconds = self.time_to_seconds(self.start_time_ctrl.GetValue()) if self.start_time_ctrl.GetValue() else 0
			if self.end_counts_back():
				end_seconds = self.file_duration_seconds - self.time_to_seconds(self.end_time_ctrl.GetValue())
			else:
				end_seconds = self.time_to_seconds(self.end_time_ctrl.GetValue()) if self.end_time_ctrl.GetValue() else self.file_duration_seconds
			period = end_seconds - start_seconds
			if period < 0:
				period_str = _("invalid (negative)")
			else:
				period_str = f"{period:.2f}"
				
			fade_info = ""
			if self.audio_radio.GetValue() and self.fade_checkbox.GetValue():
				fade_in_start = self.time_to_seconds(self.fade_in_start_ctrl.GetValue()) if self.fade_in_start_ctrl.GetValue() else None
				fade_in_end = self.time_to_seconds(self.fade_in_end_ctrl.GetValue()) if self.fade_in_end_ctrl.GetValue() else None
				fade_out_start = self.time_to_seconds(self.fade_out_start_ctrl.GetValue()) if self.fade_out_start_ctrl.GetValue() else None
				fade_out_end = self.time_to_seconds(self.fade_out_end_ctrl.GetValue()) if self.fade_out_end_ctrl.GetValue() else None
				
				if fade_in_start is not None and fade_in_end is not None and fade_in_start < fade_in_end:
					fade_info += _(" | Fade In: {} to {}").format(
						self.fade_in_start_ctrl.GetValue(), self.fade_in_end_ctrl.GetValue()
					)
				if fade_out_start is not None and fade_out_end is not None and fade_out_start < fade_out_end:
					fade_info += _(" | Fade Out: {} to {}").format(
						self.fade_out_start_ctrl.GetValue(), self.fade_out_end_ctrl.GetValue()
					)
				
		except (ValueError, IndexError):
			period_str = _("invalid")
			fade_info = ""
			
		label = _("File duration: {duration}; Trim period: {period} seconds{fade_info}").format(
			duration=duration_str,
			period=period_str,
			fade_info=fade_info
		)
		self.duration_label.SetLabel(label)
		

	def build_fade_filter(self, fade_in_start, fade_in_end, fade_out_start, fade_out_end, trim_duration):
		filters = []
		if fade_in_start is not None and fade_in_end is not None and fade_in_start < fade_in_end:
			filters.append(f"afade=t=in:st={fade_in_start}:d={fade_in_end - fade_in_start}")
		if fade_out_start is not None and fade_out_end is not None and fade_out_start < fade_out_end:
			fade_out_duration = fade_out_end - fade_out_start
			filters.append(f"afade=t=out:st={fade_out_start}:d={fade_out_duration}")
		return ",".join(filters) if filters else None

	def validate_time_format(self, time_str):
		if not time_str:
			return True
		try:
			parseTimeText(time_str)
		except ValueError:
			return False
		return True

	def time_to_seconds(self, time_str):
		if not time_str:
			return 0
		try:
			return parseTimeText(time_str)
		except ValueError:
			raise ValueError("Invalid time values")

	def show_error(self, message):
		wx.MessageBox(message, _("Error"), wx.OK | wx.ICON_ERROR)

	def read_fade_value(self, ctrl):
		text = ctrl.GetValue()
		return self.time_to_seconds(text) if text else None

	def report_item_error(self, index, message):
		if len(self.items) > 1:
			self.select_item(index)
			message = "{}: {}".format(os.path.basename(self.items[index]["path"]), message)
		self.show_error(message)

	def collect_trim_settings(self, forPreview, index=None, batch=False):
		"""Validate one file's fields on the main thread and return the settings the worker needs.

		Returns None after reporting a problem. With batch=True, a file that simply cannot be
		processed (unreadable duration, no video stream) raises ItemSkipped instead of stopping the queue.
		"""
		if index is None:
			index = self.current_index
		item = self.items[index]
		if not item["info_ready"]:
			ui.message(_("Still reading the file information, please try again in a moment."))
			return None
		if item["duration_seconds"] <= 0:
			if batch:
				raise ItemSkipped(_("the duration could not be read"))
			self.report_item_error(index, _("The duration of this file could not be read."))
			return None

		isAudio = self.audio_radio.GetValue()
		if not isAudio and not item["video_codec"]:
			if batch:
				raise ItemSkipped(_("it has no video stream"))
			if forPreview:
				self.report_item_error(index, _("The selected file has no video stream and cannot be previewed as video."))
			else:
				self.report_item_error(index, _("The selected file has no video stream and cannot be trimmed as video. Choose Audio output instead."))
			return None

		syncAll = self.sync_all_times()
		backFromEnd = syncAll and self.end_counts_back()
		start_time = item["start"] or "0"
		if backFromEnd:
			end_time = item["end"] or "0"
		else:
			end_time = item["end"] or item["duration_label"]
		if item["start_auto"] and self.validate_time_format(start_time) and self.validate_time_format(end_time):
			if self.time_to_seconds(start_time) >= self.time_to_seconds(end_time):
				start_time = "0"
		if not self.validate_time_format(start_time) or not self.validate_time_format(end_time):
			self.report_item_error(index, _("Invalid time format. Use seconds (e.g., 10), MM:SS (e.g., 1:30), or HH:MM:SS (e.g., 1:30:45)"))
			return None
		try:
			startSeconds = self.time_to_seconds(start_time)
			endAmount = self.time_to_seconds(end_time)
			if backFromEnd:
				# Counted from this file's own end, so files of different lengths all lose the same tail; 0 keeps the end.
				endSeconds = item["duration_seconds"] - endAmount
			else:
				endSeconds = endAmount
			if startSeconds >= endSeconds:
				if syncAll and batch:
					raise ItemSkipped(_("nothing is left after applying the common start and end"))
				if syncAll:
					self.report_item_error(index, _("Nothing is left of this file after applying the common start and end time."))
				else:
					self.report_item_error(index, _("Start time must be less than end time"))
				return None
			if endSeconds > item["duration_seconds"] + 0.5:
				self.report_item_error(index, _("End time cannot be greater than file duration"))
				return None

			fadeFilter = None
			if isAudio and self.fade_checkbox.GetValue():
				fadeInStart = self.parse_fade_text(item["fade_in_start"])
				fadeInEnd = self.parse_fade_text(item["fade_in_end"])
				fadeOutStart = self.parse_fade_text(item["fade_out_start"])
				fadeOutEnd = self.parse_fade_text(item["fade_out_end"])
				if fadeInStart is not None and fadeInEnd is not None:
					if fadeInStart < startSeconds or fadeInEnd > endSeconds or fadeInStart >= fadeInEnd:
						self.report_item_error(index, _("Invalid fade-in range"))
						return None
				if fadeOutStart is not None and fadeOutEnd is not None:
					if fadeOutStart < startSeconds or fadeOutEnd > endSeconds or fadeOutStart >= fadeOutEnd:
						self.report_item_error(index, _("Invalid fade-out range"))
						return None
				fadeFilter = self.build_fade_filter(fadeInStart, fadeInEnd, fadeOutStart, fadeOutEnd, endSeconds - startSeconds)
		except ValueError:
			self.report_item_error(index, _("Invalid time values"))
			return None

		ffmpegPath = os.path.join(self.libs_path, "ffmpeg.exe")
		if not os.path.exists(ffmpegPath):
			self.show_error(_("ffmpeg.exe not found"))
			return None

		return {
			"isAudio": isAudio,
			"path": item["path"],
			"sourceAudioCodec": item["audio_codec"],
			"sourceVideoCodec": item["video_codec"],
			"start": startSeconds,
			"length": endSeconds - startSeconds,
			"startText": start_time,
			"endText": end_time,
			"fadeFilter": fadeFilter,
			"fades": (fadeInStart, fadeInEnd, fadeOutStart, fadeOutEnd) if fadeFilter else None,
			"ffmpegPath": ffmpegPath,
		}

	def parse_fade_text(self, text):
		return self.time_to_seconds(text) if text else None

	def on_preview(self, event):
		self.start_preview(fromEnd=False)

	def on_preview_end(self, event):
		self.start_preview(fromEnd=True)

	def set_preview_buttons_enabled(self, enabled):
		self.preview_btn.Enable(enabled)
		self.preview_end_btn.Enable(enabled)

	def shift_fades(self, fades, offset):
		"""Fade times moved to a timeline that starts at offset; a fade that ends before it disappears."""
		fadeInStart, fadeInEnd, fadeOutStart, fadeOutEnd = fades
		if fadeInStart is not None and fadeInEnd is not None:
			if fadeInEnd <= offset:
				fadeInStart = fadeInEnd = None
			else:
				fadeInStart, fadeInEnd = max(fadeInStart - offset, 0), fadeInEnd - offset
		if fadeOutStart is not None and fadeOutEnd is not None:
			if fadeOutEnd <= offset:
				fadeOutStart = fadeOutEnd = None
			else:
				fadeOutStart, fadeOutEnd = max(fadeOutStart - offset, 0), fadeOutEnd - offset
		return fadeInStart, fadeInEnd, fadeOutStart, fadeOutEnd

	def start_preview(self, fromEnd):
		if self.is_previewing or self.is_running:
			return
		self.save_controls_to_item(self.current_index)
		settings = self.collect_trim_settings(forPreview=True)
		if settings is None:
			return

		self.cleanup_temp_file()
		self.preview_dir = tempfile.mkdtemp(prefix="xtrack_preview_")
		sourceName = os.path.splitext(os.path.basename(settings["path"]))[0]
		previewPath = os.path.join(self.preview_dir, f"preview_{sourceName}.{'mp3' if settings['isAudio'] else 'mp4'}")

		self.preview_length = min(settings["length"], PREVIEW_SECONDS)
		# The end preview plays the last seconds of the part that will be kept.
		previewStart = settings["start"] + settings["length"] - self.preview_length if fromEnd else settings["start"]
		fadeFilter = settings["fadeFilter"]
		if fromEnd:
			# -ss before -i jumps straight to the tail instead of decoding hours of audio or
			# video first. Timestamps then restart at zero, so fade times are shifted to match.
			command = [
				settings["ffmpegPath"], "-hide_banner", "-y",
				"-ss", f"{previewStart:.3f}",
				"-i", settings["path"],
				"-t", f"{self.preview_length:.3f}",
			]
			if settings["fades"]:
				fadeFilter = self.build_fade_filter(*self.shift_fades(settings["fades"], previewStart), self.preview_length)
		else:
			command = [
				settings["ffmpegPath"], "-hide_banner", "-y",
				"-i", settings["path"],
				"-ss", f"{previewStart:.3f}",
				"-t", f"{self.preview_length:.3f}",
			]
		if settings["isAudio"]:
			command.append("-vn")
			if fadeFilter:
				command.extend(["-af", fadeFilter])
			command.extend(["-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100"])
		else:
			# Video preview - use simple copy for faster preview. When the
			# trim start does not land on a keyframe, copy mode can leave the
			# first frame frozen or audio out of sync, so timestamps are
			# renormalized to zero.
			command.extend(["-c", "copy", "-avoid_negative_ts", "make_zero"])
		command.append(previewPath)

		self.is_previewing = True
		self.set_preview_buttons_enabled(False)
		self.status_label.SetLabel(_("Preparing preview..."))
		threading.Thread(target=self.run_preview, args=(command, previewPath), daemon=True).start()

	def run_preview(self, command, previewPath):
		def registerProcess(process):
			self.ffmpeg_process = process

		try:
			exitCode, stderrLines = runFfmpeg(command, self.preview_dir, 0, lambda percent: None, registerProcess)
		except OSError as error:
			log.error("xTrack: could not run ffmpeg for the preview: %s", error)
			self.post(self.on_preview_failed, _("ffmpeg could not be started"))
			return
		if self.cancel_event.is_set():
			return
		if exitCode != 0:
			log.error("xTrack: preview failed (exit code %s): %s", exitCode, " | ".join(line.strip() for line in stderrLines))
			self.post(self.on_preview_failed, summarizeFfmpegError(stderrLines))
			return
		if not os.path.exists(previewPath) or os.path.getsize(previewPath) <= 1024:
			self.post(self.on_preview_failed, _("the preview file was not created or is too small"))
			return
		self.post(self.on_preview_ready, previewPath)

	def on_preview_ready(self, previewPath):
		self.is_previewing = False
		self.set_preview_buttons_enabled(True)
		self.status_label.SetLabel("")
		try:
			os.startfile(previewPath)
		except OSError as error:
			log.error("xTrack: could not open the preview: %s", error)
			ui.message(limitSpokenText(_("Could not play preview: {}").format(error.strerror or _("unknown error"))))
			return
		ui.message(_("Playing a {} second preview").format(int(round(self.preview_length))))

	def on_preview_failed(self, reason):
		self.is_previewing = False
		self.set_preview_buttons_enabled(True)
		self.status_label.SetLabel(_("Preview failed"))
		tones.beep(300, 300)
		ui.message(limitSpokenText(_("Preview failed: {}").format(reason)))

	def effective_audio_codec(self, outputFormat, choice):
		"""The audio codec choice that actually applies to every file for this container, or None to keep the original."""
		if choice not in AUDIO_CODEC_MAP:
			return None
		if outputFormat == "webm":
			return "Vorbis"
		if outputFormat in ("mp4", "mov") and choice == "Vorbis":
			return "AAC (Recommended)"
		return choice

	def build_audio_args(self, settings):
		args = []
		if settings["isAudio"]:
			# -vn: a video file trimmed to audio must not drag its picture stream into an MP3/WAV.
			args.append("-vn")
			if settings["fadeFilter"]:
				args.extend(["-af", settings["fadeFilter"]])
			if settings["outputFormat"] == "mp3":
				args.extend(["-c:a", "libmp3lame", "-b:a", f"{settings['quality']}k", "-ar", "44100"])
			else:
				args.extend(["-c:a", "pcm_s16le", "-ar", "44100"])
			return args

		outputFormat = settings["outputFormat"]
		sourceAudio = settings["sourceAudioCodec"]
		sampleRate = settings["sampleRate"]
		channels = settings["channels"]
		args.extend(["-c:v", "copy"])
		# When the trim start does not land on a keyframe, copy mode can
		# leave the first frame frozen, show a black screen, or drift
		# audio out of sync, so timestamps are renormalized to zero.
		args.extend(["-avoid_negative_ts", "make_zero"])

		encoder = None
		forcedDefaults = False
		wantedChoice = self.effective_audio_codec(outputFormat, settings["audioCodec"])
		if wantedChoice is not None:
			# One codec for every file of the batch. A file already in that codec is copied
			# untouched unless a different sample rate or channel layout was asked for.
			wantedEncoder, wantedSource = AUDIO_CODEC_MAP[wantedChoice]
			if wantedSource != sourceAudio or sampleRate != "Keep Original" or channels is not None:
				encoder = wantedEncoder
		elif outputFormat == "mp4" and sourceAudio in PCM_AUDIO_CODECS:
			encoder = "aac"
			forcedDefaults = True
		elif outputFormat == "webm" and sourceAudio and sourceAudio not in WEBM_AUDIO_CODECS:
			# WebM only holds Vorbis or Opus audio, so anything else has to be re-encoded.
			encoder = "libvorbis"
		elif (sampleRate != "Keep Original" or channels is not None) and sourceAudio in SOURCE_ENCODERS:
			encoder = SOURCE_ENCODERS[sourceAudio]

		if encoder is None:
			args.extend(["-c:a", "copy"])
			return args
		args.extend(["-c:a", encoder, "-b:a", "192k"])
		if sampleRate != "Keep Original":
			args.extend(["-ar", sampleRate])
		elif forcedDefaults:
			args.extend(["-ar", "48000"])
		if channels is not None:
			args.extend(["-ac", channels])
		elif forcedDefaults:
			args.extend(["-ac", "2"])
		return args

	def on_trim(self, event):
		if self.is_running or self.is_previewing:
			return
		self.cleanup_temp_file()
		self.save_controls_to_item(self.current_index)
		if self.sync_all_times():
			self.apply_common_times()
		isBatch = len(self.items) > 1
		isAudio = self.audio_radio.GetValue()

		# One format for the whole queue: every file is exported with the same settings.
		common = {}
		if isAudio:
			common["outputFormat"] = "mp3" if self.mp3_radio.GetValue() else "wav"
			common["quality"] = int(self.audio_quality_ctrl.GetStringSelection().split()[0]) if self.mp3_radio.GetValue() else None
		else:
			common["outputFormat"] = VIDEO_FORMAT_MAP.get(self.video_format_ctrl.GetStringSelection(), "mp4")
			common["audioCodec"] = self.audio_codec_ctrl.GetStringSelection()
			common["sampleRate"] = self.sample_rate_ctrl.GetStringSelection().replace(" Hz", "")
			channelsSelection = self.channels_ctrl.GetStringSelection()
			common["channels"] = "2" if channelsSelection == "Stereo (2 channels)" else "1" if channelsSelection == "Mono (1 channel)" else None

		customName = self.output_text.GetValue().strip()
		if not isBatch and any(char in INVALID_FILENAME_CHARS for char in customName):
			self.show_error(_("The output filename contains characters that are not allowed."))
			return
		useSubfolder = self.subfolder_checkbox.GetValue()

		jobs = []
		skipped = []
		for index, item in enumerate(self.items):
			try:
				settings = self.collect_trim_settings(False, index, isBatch)
			except ItemSkipped as skip:
				skipped.append((os.path.basename(item["path"]), str(skip)))
				continue
			if settings is None:
				return
			settings.update(common)
			if not isAudio and common["outputFormat"] == "webm" and settings["sourceVideoCodec"] not in WEBM_VIDEO_CODECS:
				message = _("WebM cannot hold this video without re-encoding. Choose MP4 or MKV instead.")
				if isBatch:
					skipped.append((os.path.basename(item["path"]), message))
					continue
				self.show_error(message)
				return
			sourceBase = os.path.splitext(os.path.basename(item["path"]))[0]
			baseName = sourceBase + "_trimmed" if isBatch or not customName else customName
			sourceDir = os.path.dirname(item["path"])
			jobs.append({
				"settings": settings,
				"baseName": baseName,
				"outputDir": os.path.join(sourceDir, TRIMMED_SUBFOLDER_NAME) if useSubfolder else sourceDir,
			})
		if not jobs:
			self.show_error(_("None of the selected files can be trimmed with these settings."))
			return

		# Everything read from the dialog's controls is captured here, on the
		# main thread, so the worker never touches a wx widget.
		savedState = self.capture_state(jobs[0]["settings"])

		self.is_running = True
		self.cancel_event.clear()
		self.trim_btn.Enable(False)
		self.cancel_btn.Enable(isBatch)
		self.set_preview_buttons_enabled(False)
		if self.file_listbox is not None:
			self.file_listbox.Enable(False)
		self.status_label.SetLabel(_("Starting trim operation..."))
		self.progress_bar.SetValue(0)
		threading.Thread(
			target=self.run_trim_queue, args=(jobs, skipped, savedState, useSubfolder), daemon=True
		).start()

	def capture_state(self, settings):
		state = {
			"TrimLastOutputType": "audio" if settings["isAudio"] else "video",
			"TrimLastFadeEnabled": self.fade_checkbox.GetValue(),
			"TrimLastStartTime": settings["startText"],
			"TrimLastEndTime": settings["endText"],
			"TrimLastFile": settings["path"],
			"TrimLastSubfolder": self.subfolder_checkbox.GetValue(),
			"TrimLastFadeInStart": self.fade_in_start_ctrl.GetValue(),
			"TrimLastFadeInEnd": self.fade_in_end_ctrl.GetValue(),
			"TrimLastFadeOutStart": self.fade_out_start_ctrl.GetValue(),
			"TrimLastFadeOutEnd": self.fade_out_end_ctrl.GetValue(),
		}
		if settings["isAudio"]:
			state["TrimLastFormat"] = settings["outputFormat"]
			if settings["quality"]:
				state["TrimLastQuality"] = settings["quality"]
		else:
			state["TrimLastVideoFormat"] = self.video_format_ctrl.GetStringSelection()
			state["TrimLastSampleRate"] = self.sample_rate_ctrl.GetStringSelection()
			state["TrimLastChannels"] = self.channels_ctrl.GetStringSelection()
			state["TrimLastAudioCodec"] = self.audio_codec_ctrl.GetStringSelection()
		return state

	def build_trim_command(self, settings, partPath):
		command = [
			settings["ffmpegPath"], "-hide_banner", "-y",
			"-i", settings["path"],
			"-ss", f"{settings['start']:.3f}",
			"-t", f"{settings['length']:.3f}",
		]
		command.extend(self.build_audio_args(settings))
		command.extend(["-progress", "pipe:1", "-nostats", partPath])
		return command

	def run_trim_queue(self, jobs, skipped, savedState, useSubfolder):
		def registerProcess(process):
			self.ffmpeg_process = process

		created = []
		failures = list(skipped)
		total = len(jobs)
		for number, job in enumerate(jobs):
			if self.cancel_event.is_set():
				return
			settings = job["settings"]
			sourceName = os.path.basename(settings["path"])
			try:
				os.makedirs(job["outputDir"], exist_ok=True)
			except OSError as error:
				log.error("xTrack: could not create the folder %s: %s", job["outputDir"], error)
				failures.append((sourceName, _("the folder for the trimmed files could not be created")))
				continue
			finalPath = os.path.join(job["outputDir"], self.get_unique_filename(job["outputDir"], job["baseName"], settings["outputFormat"]))
			partPath = partPathFor(finalPath)
			command = self.build_trim_command(settings, partPath)
			log.info("xTrack: trim command: %s", " ".join(command))
			if total > 1:
				self.post(self.status_label.SetLabel, _("Trimming {current} of {total}: {name}").format(current=number + 1, total=total, name=sourceName))

			def reportProgress(percent, number=number):
				self.post(self.progress_bar.SetValue, int((number + percent / 100) / total * 100))

			try:
				exitCode, stderrLines = runFfmpeg(command, job["outputDir"], settings["length"], reportProgress, registerProcess)
			except OSError as error:
				log.error("xTrack: could not run ffmpeg for the trim: %s", error)
				removeQuietly(partPath)
				failures.append((sourceName, _("ffmpeg could not be started")))
				continue
			if self.cancel_event.is_set():
				removeQuietly(partPath)
				return
			if exitCode != 0:
				log.error("xTrack: trim failed for %s (exit code %s): %s", sourceName, exitCode, " | ".join(line.strip() for line in stderrLines))
				removeQuietly(partPath)
				failures.append((sourceName, summarizeFfmpegError(stderrLines)))
				continue
			try:
				os.replace(partPath, finalPath)
			except OSError as error:
				log.error("xTrack: could not move %s to %s: %s", partPath, finalPath, error)
				removeQuietly(partPath)
				failures.append((sourceName, _("the output file could not be written")))
				continue
			created.append(finalPath)
		self.post(self.on_queue_finished, created, failures, savedState, useSubfolder)

	def on_queue_finished(self, created, failures, savedState, useSubfolder):
		for name, reason in failures:
			log.error("xTrack: %s was not trimmed: %s", name, reason)
		if not created:
			self.on_trim_failed(failures[0][1] if failures else _("nothing was trimmed"))
			return
		self.is_running = False
		self.progress_bar.SetValue(100)
		self.status_label.SetLabel(_("Trim complete!"))
		configData = self.load_config()
		configData.update(savedState)
		self.save_config(configData)
		tones.beep(1000, 300)
		if len(self.items) > 1:
			message = _("{count} files trimmed.").format(count=len(created))
		else:
			message = _("Trimmed file saved as {}").format(os.path.basename(created[0]))
		if failures:
			message += " " + _("{count} could not be processed.").format(count=len(failures))
		if useSubfolder:
			message += " " + _("Saved in the folder {}").format(TRIMMED_SUBFOLDER_NAME)
		ui.message(limitSpokenText(message))
		self.EndModal(wx.ID_OK)

	def on_trim_failed(self, reason):
		self.is_running = False
		self.status_label.SetLabel(_("Trim failed!"))
		self.trim_btn.Enable(True)
		self.cancel_btn.Enable(True)
		self.set_preview_buttons_enabled(True)
		if self.file_listbox is not None:
			self.file_listbox.Enable(True)
		tones.beep(300, 300)
		ui.message(limitSpokenText(_("Trimming failed: {}").format(reason)))

	def stop_work(self):
		self.is_closed = True
		self.cancel_event.set()
		process = self.ffmpeg_process
		if process and process.poll() is None:
			try:
				process.terminate()
			except OSError as error:
				log.debugWarning("xTrack: could not stop ffmpeg: %s", error)

	def on_cancel(self, event):
		self.stop_work()
		self.cleanup_temp_file()
		self.EndModal(wx.ID_CANCEL)

	def on_close(self, event):
		self.stop_work()
		self.cleanup_temp_file()
		event.Skip()

	def cleanup_temp_file(self):
		if self.preview_dir and os.path.isdir(self.preview_dir):
			# A media player may still hold the preview open; whatever cannot be
			# removed now stays in the system temp folder rather than the user's music folder.
			shutil.rmtree(self.preview_dir, ignore_errors=True)
		self.preview_dir = ""

	def get_unique_filename(self, directory, base_name, extension):
		counter = 1
		output_file = f"{base_name}.{extension}"
		while os.path.exists(os.path.join(directory, output_file)):
			output_file = f"{base_name}_{counter}.{extension}"
			counter += 1
		return output_file
