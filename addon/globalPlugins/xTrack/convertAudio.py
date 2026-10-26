# convertAudio.py

import wx
import os
import threading
import collections
import ui
import tones
from logHandler import log
from .xTrackCore import load_config, save_config, get_file_duration, get_config_path
from .ffmpegSupport import (
	limitSpokenText, summarizeFfmpegError, setProcessSuspended, removeQuietly, partPathFor, runFfmpeg,
)
import addonHandler

addonHandler.initTranslation()

SAMPLE_RATE_HZ = {"44.1 kHz": "44100", "48 kHz": "48000", "96 kHz": "96000"}
# Folder, next to the originals, that holds the converted files when the user asks for a separate one.
OUTPUT_SUBFOLDER_NAME = "Converted"

ConversionJob = collections.namedtuple(
	"ConversionJob", ("source_path", "final_path", "part_path", "command", "list_index")
)

# Per-format conversion presets. Each format has its own quality control
# label/choices/default and a "codecArgs" callable that turns the selected
# choice string into the right ffmpeg audio-codec arguments, so adding a
# new output format only means adding one entry here rather than touching
# process_next_file's branching logic.
def _mp3CodecArgs(qualityChoice):
	bitrateKbps = qualityChoice.split()[0]
	return ["-c:a", "libmp3lame", "-b:a", f"{bitrateKbps}k"]

def _wavCodecArgs(qualityChoice):
	return ["-c:a", "pcm_s16le"]

def _flacCodecArgs(qualityChoice):
	compressionLevel = qualityChoice.split()[0]
	return ["-c:a", "flac", "-compression_level", compressionLevel]

def _m4aCodecArgs(qualityChoice):
	bitrateKbps = qualityChoice.split()[0]
	return ["-c:a", "aac", "-b:a", f"{bitrateKbps}k"]

def _oggCodecArgs(qualityChoice):
	qualityLevel = qualityChoice.split()[0].lstrip("Qq")
	return ["-c:a", "libvorbis", "-q:a", qualityLevel]

def _opusCodecArgs(qualityChoice):
	bitrateKbps = qualityChoice.split()[0]
	return ["-c:a", "libopus", "-b:a", f"{bitrateKbps}k"]

def _m4rCodecArgs(qualityChoice):
	bitrateKbps = qualityChoice.split()[0]
	return ["-c:a", "aac", "-b:a", f"{bitrateKbps}k"]

def _alacCodecArgs(qualityChoice):
	return ["-c:a", "alac"]

AUDIO_FORMAT_PRESETS = {
	"mp3": {
		"label": _("MP3 Quality:"),
		"choices": ["320 kbps", "256 kbps", "192 kbps", "128 kbps"],
		"default": "320 kbps",
		"codecArgs": _mp3CodecArgs,
		"enableQualityControl": True,
	},
	"wav": {
		"label": _("Quality:"),
		"choices": [_("Lossless (16-bit PCM)")],
		"default": _("Lossless (16-bit PCM)"),
		"codecArgs": _wavCodecArgs,
		"enableQualityControl": False,
	},
	"flac": {
		"label": _("FLAC Compression:"),
		"choices": ["0 (Fastest, Larger)", "3", "5 (Balanced)", "8 (Smallest, Slowest)"],
		"default": "5 (Balanced)",
		"codecArgs": _flacCodecArgs,
		"enableQualityControl": True,
	},
	"m4a": {
		"label": _("AAC Quality:"),
		"choices": ["256 kbps", "192 kbps", "128 kbps", "96 kbps"],
		"default": "192 kbps",
		"codecArgs": _m4aCodecArgs,
		"enableQualityControl": True,
	},
	"ogg": {
		"label": _("OGG Quality:"),
		"choices": ["Q10 (Best)", "Q8", "Q6 (Balanced)", "Q4", "Q2 (Smallest)"],
		"default": "Q6 (Balanced)",
		"codecArgs": _oggCodecArgs,
		"enableQualityControl": True,
	},
	"opus": {
		"label": _("Opus Quality:"),
		"choices": ["256 kbps", "192 kbps", "128 kbps", "96 kbps", "64 kbps"],
		"default": "128 kbps",
		"codecArgs": _opusCodecArgs,
		"enableQualityControl": True,
	},
	"m4r": {
		"label": _("M4R Quality:"),
		"choices": ["256 kbps", "192 kbps", "128 kbps"],
		"default": "192 kbps",
		"codecArgs": _m4rCodecArgs,
		"enableQualityControl": True,
		# .m4r is not recognized by ffmpeg's extension-based muxer lookup,
		# so the mp4 muxer must be forced explicitly.
		"container": "mp4",
	},
	"alac": {
		"label": _("Quality:"),
		"choices": [_("Lossless (ALAC)")],
		"default": _("Lossless (ALAC)"),
		"codecArgs": _alacCodecArgs,
		"enableQualityControl": False,
		# ALAC requires an MP4-family container; .alac is not a recognized
		# ffmpeg muxer extension, so the mp4 muxer must be forced explicitly.
		"container": "mp4",
	},
}


class ConvertAudioDialog(wx.Dialog):
	"""Dialog for converting various audio/video formats to MP3 or WAV, including same-format re-encoding."""
	def __init__(self, parent, selected_files, libs_path):
		super().__init__(parent, title=_("Convert Audio"))
		self.selected_files = selected_files
		self.libs_path = libs_path
		self.current_file_index = 0
		self.output_path = os.path.dirname(self.selected_files[0]) if self.selected_files else os.getcwd()
		self.config_path = get_config_path()
		self.ffmpeg_process = None
		self.is_paused = False
		self.is_running = False
		self.is_closed = False
		self.cancel_event = threading.Event()
		self.file_durations = {}
		self.file_duration_seconds = {}
		self.used_subfolder = False
		self.init_ui()
		self.SetTitle(_("Convert Audio: {} files").format(len(self.selected_files)))
		self.load_settings()
		self.Bind(wx.EVT_CLOSE, self.on_close)
		threading.Thread(target=self.load_file_durations, daemon=True).start()

	def post(self, handler, *args):
		wx.CallAfter(self.run_if_open, handler, *args)

	def run_if_open(self, handler, *args):
		if self.is_closed:
			return
		handler(*args)

	def load_file_durations(self):
		"""Load durations for all selected files."""
		for file_path in self.selected_files:
			if self.is_closed:
				return
			durationSeconds, _label = get_file_duration(self.libs_path, file_path)
			self.file_duration_seconds[file_path] = durationSeconds
			self.file_durations[file_path] = self.format_duration(durationSeconds)
		self.post(self.update_file_listbox)

	def format_duration(self, seconds):
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

	def init_ui(self):
		main_sizer = wx.BoxSizer(wx.VERTICAL)
		
		# File info section
		file_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Files to Convert"))
		self.file_listbox = wx.ListBox(self, choices=[self.get_file_display_name(f) for f in self.selected_files])
		file_sizer.Add(self.file_listbox, 1, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(file_sizer, 1, wx.EXPAND | wx.ALL, 5)
		
		# Current file info
		current_file_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Current File"))
		self.current_file_label = wx.StaticText(self, label="")
		current_file_sizer.Add(self.current_file_label, 0, wx.EXPAND | wx.ALL, 5)
		self.duration_label = wx.StaticText(self, label=_("File Duration: Calculating..."))
		current_file_sizer.Add(self.duration_label, 0, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(current_file_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Settings section
		settings_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Conversion Settings"))
		
		# Output format setting
		format_sizer = wx.BoxSizer(wx.HORIZONTAL)
		format_label = wx.StaticText(self, label=_("Output Format:"))
		format_sizer.Add(format_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		format_choices = ["MP3", "WAV", "FLAC", "M4A", "OGG", "OPUS", "M4R", "ALAC"]
		self.format_ctrl = wx.ComboBox(self, choices=format_choices, style=wx.CB_READONLY)
		self.format_ctrl.SetStringSelection("MP3")
		format_sizer.Add(self.format_ctrl, 1, wx.EXPAND | wx.ALL, 5)
		settings_sizer.Add(format_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Quality setting -- label and choices depend on the selected output
		# format (see AUDIO_FORMAT_PRESETS); on_format_change repopulates
		# this control whenever the format changes.
		quality_sizer = wx.BoxSizer(wx.HORIZONTAL)
		self.quality_label = wx.StaticText(self, label=_("MP3 Quality:"))
		quality_sizer.Add(self.quality_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.quality_ctrl = wx.ComboBox(self, choices=AUDIO_FORMAT_PRESETS["mp3"]["choices"], style=wx.CB_READONLY)
		quality_sizer.Add(self.quality_ctrl, 1, wx.EXPAND | wx.ALL, 5)
		settings_sizer.Add(quality_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Sample rate setting
		samplerate_sizer = wx.BoxSizer(wx.HORIZONTAL)
		samplerate_label = wx.StaticText(self, label=_("Sample Rate:"))
		samplerate_sizer.Add(samplerate_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		samplerate_choices = ["44.1 kHz", "48 kHz", "96 kHz"]
		self.samplerate_ctrl = wx.ComboBox(self, choices=samplerate_choices, style=wx.CB_READONLY)
		samplerate_sizer.Add(self.samplerate_ctrl, 1, wx.EXPAND | wx.ALL, 5)
		settings_sizer.Add(samplerate_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Volume setting
		volume_sizer = wx.BoxSizer(wx.HORIZONTAL)
		volume_label = wx.StaticText(self, label=_("Volume:"))
		volume_sizer.Add(volume_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		volume_choices = [f"{i}%" for i in range(200, 0, -5)]
		self.volume_ctrl = wx.ComboBox(self, choices=volume_choices, style=wx.CB_READONLY)
		volume_sizer.Add(self.volume_ctrl, 1, wx.EXPAND | wx.ALL, 5)
		settings_sizer.Add(volume_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Add same-format conversion note
		note_sizer = wx.BoxSizer(wx.HORIZONTAL)
		note_label = wx.StaticText(self, label=_("Note: You can now convert files to the same format (e.g., MP3 to MP3) to change quality, volume, or sample rate."))
		note_label.Wrap(400)  # Make text wrap
		note_sizer.Add(note_label, 1, wx.EXPAND | wx.ALL, 5)
		settings_sizer.Add(note_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		main_sizer.Add(settings_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Progress bar
		# Unchecked by default: the converted files are saved next to the originals.
		self.subfolder_checkbox = wx.CheckBox(self, label=_("Save converted files in a separate subfolder"))
		self.subfolder_checkbox.SetValue(False)
		self.subfolder_checkbox.SetToolTip(_("Unchecked: the converted files are saved together with the originals. Checked: they are saved in a subfolder named {}.").format(OUTPUT_SUBFOLDER_NAME))
		main_sizer.Add(self.subfolder_checkbox, 0, wx.ALL, 5)

		self.progress_bar = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
		main_sizer.Add(self.progress_bar, 0, wx.EXPAND | wx.ALL, 5)
		
		# Status label
		self.status_label = wx.StaticText(self, label="")
		main_sizer.Add(self.status_label, 0, wx.EXPAND | wx.ALL, 5)
		
		# Buttons
		btn_sizer = wx.StdDialogButtonSizer()
		self.convert_btn = wx.Button(self, wx.ID_OK, label=_("Convert"))
		self.convert_btn.Bind(wx.EVT_BUTTON, self.on_convert_or_pause)
		btn_sizer.AddButton(self.convert_btn)
		
		self.cancel_btn = wx.Button(self, wx.ID_CANCEL, label=_("Cancel"))
		self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
		btn_sizer.AddButton(self.cancel_btn)
		
		btn_sizer.Realize()
		main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
		
		self.SetSizer(main_sizer)
		self.Fit()
		
		# Bind format change event
		self.format_ctrl.Bind(wx.EVT_COMBOBOX, self.on_format_change)
		
		# Update current file info
		if self.selected_files:
			self.update_current_file_info(0, select=False)

	def get_file_display_name(self, file_path):
		"""Get display name with duration for listbox."""
		base_name = os.path.basename(file_path)
		duration = self.file_durations.get(file_path, _("Calculating..."))
		return f"{base_name} ({duration})"

	def update_file_listbox(self):
		"""Update file listbox with names and durations."""
		selection = self.file_listbox.GetSelection()
		self.file_listbox.Set([self.get_file_display_name(f) for f in self.selected_files])
		if selection != wx.NOT_FOUND:
			self.file_listbox.SetSelection(selection)
		self.update_current_file_info(self.current_file_index, select=False)

	def on_format_change(self, event=None):
		"""Repopulate the quality control to match the selected output format's preset."""
		outputFormat = self.format_ctrl.GetStringSelection().lower()
		preset = AUDIO_FORMAT_PRESETS.get(outputFormat, AUDIO_FORMAT_PRESETS["mp3"])

		previousSelection = self.quality_ctrl.GetStringSelection()
		self.quality_label.SetLabel(preset["label"])
		self.quality_ctrl.Set(preset["choices"])
		if previousSelection in preset["choices"]:
			self.quality_ctrl.SetStringSelection(previousSelection)
		else:
			self.quality_ctrl.SetStringSelection(preset["default"])
		self.quality_ctrl.Enable(preset["enableQualityControl"])

	def update_current_file_info(self, index, select=True):
		if index >= len(self.selected_files):
			return
		self.current_file_index = index
		filePath = self.selected_files[index]
		self.current_file_label.SetLabel(os.path.basename(filePath))
		if select:
			self.file_listbox.SetSelection(index)
		duration = self.file_durations.get(filePath, _("Calculating..."))
		self.duration_label.SetLabel(_("File Duration: {}").format(duration))

	def load_settings(self):
		config_data = load_config(self.config_path)
		self.format_ctrl.SetStringSelection(config_data.get("ConvertAudioFormat", "MP3"))
		self.on_format_change(None)
		self.quality_ctrl.SetStringSelection(config_data.get("ConvertAudioQuality", ""))
		self.samplerate_ctrl.SetStringSelection(config_data.get("ConvertAudioSampleRate", "48 kHz"))
		self.volume_ctrl.SetStringSelection(config_data.get("ConvertAudioVolume", "100%"))

	def save_settings(self):
		config_data = load_config(self.config_path)
		config_data["ConvertAudioFormat"] = self.format_ctrl.GetStringSelection()
		config_data["ConvertAudioQuality"] = self.quality_ctrl.GetStringSelection()
		config_data["ConvertAudioSampleRate"] = self.samplerate_ctrl.GetStringSelection()
		config_data["ConvertAudioVolume"] = self.volume_ctrl.GetStringSelection()
		save_config(self.config_path, config_data)

	def on_convert_or_pause(self, event):
		if self.is_running:
			self.toggle_pause()
		else:
			self.on_convert(event)

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
			ui.message(_("Pause is not available for this conversion"))
			return
		self.is_paused = suspend
		if suspend:
			self.convert_btn.SetLabel(_("Resume"))
			self.status_label.SetLabel(_("Paused."))
		else:
			self.convert_btn.SetLabel(_("Pause"))
			self.status_label.SetLabel(_("Resuming..."))
		ui.message(self.status_label.GetLabel())

	def get_volume_fraction(self):
		try:
			return int(self.volume_ctrl.GetStringSelection().replace("%", "")) / 100
		except ValueError:
			return 1.0

	def build_jobs(self):
		ffmpegPath = os.path.join(self.libs_path, "ffmpeg.exe")
		if not os.path.exists(ffmpegPath):
			ui.message(_("ffmpeg.exe not found"))
			return []

		outputFormat = self.format_ctrl.GetStringSelection().lower() or "mp3"
		preset = AUDIO_FORMAT_PRESETS.get(outputFormat, AUDIO_FORMAT_PRESETS["mp3"])
		qualitySelection = self.quality_ctrl.GetStringSelection() or preset["default"]
		sampleRate = SAMPLE_RATE_HZ.get(self.samplerate_ctrl.GetStringSelection(), "48000")
		volumeFraction = self.get_volume_fraction()

		# Volume boosts above 100% can push the signal past 0 dBFS, so an
		# alimiter is chained on afterwards to catch the peaks and prevent
		# clipping. Unboosted or reduced volume never needs limiting.
		audioFilterChain = f"volume={volumeFraction:.2f}"
		if volumeFraction > 1.0:
			audioFilterChain += ",alimiter=level_in=1:level_out=1:limit=0.99"

		useSubfolder = self.subfolder_checkbox.GetValue()
		outputDir = os.path.join(self.output_path, OUTPUT_SUBFOLDER_NAME) if useSubfolder else self.output_path
		self.used_subfolder = useSubfolder

		jobs = []
		for listIndex, filePath in enumerate(self.selected_files):
			baseName = os.path.splitext(os.path.basename(filePath))[0]
			inputFormat = os.path.splitext(filePath)[1].lower().lstrip(".")
			# Same-format re-encodes next to the source get a "_converted" suffix so the source is never
			# overwritten; in a separate subfolder the original name can be kept.
			if inputFormat == outputFormat and not useSubfolder:
				finalName = f"{baseName}_converted.{outputFormat}"
			else:
				finalName = f"{baseName}.{outputFormat}"
			finalPath = os.path.join(outputDir, finalName)
			# ffmpeg writes to a side file first, so a failed or cancelled run
			# never leaves a broken file (or clobbers a good one) at finalPath.
			partPath = partPathFor(finalPath)

			command = [
				ffmpegPath,
				"-hide_banner",
				"-i", filePath,
				"-vn",
				"-ar", sampleRate,
				"-af", audioFilterChain,
				"-progress", "pipe:1",
				"-nostats",
				"-y",
			]
			command.extend(preset["codecArgs"](qualitySelection))
			# Some formats (M4R, ALAC) are not recognized by ffmpeg's
			# extension-based muxer lookup, so the container must be forced.
			if "container" in preset:
				command.extend(["-f", preset["container"]])
			command.append(partPath)
			jobs.append(ConversionJob(filePath, finalPath, partPath, command, listIndex))
		return jobs

	def on_convert(self, event):
		if self.is_running:
			return
		jobs = self.build_jobs()
		if not jobs:
			return
		self.save_settings()
		self.is_running = True
		self.is_paused = False
		self.cancel_event.clear()
		self.convert_btn.SetLabel(_("Pause"))
		self.cancel_btn.Enable(False)
		self.status_label.SetLabel(_("Starting conversion..."))
		self.progress_bar.SetValue(0)
		threading.Thread(target=self.run_queue, args=(jobs,), daemon=True).start()

	def run_queue(self, jobs):
		failure = None
		for job in jobs:
			if self.cancel_event.is_set():
				break
			self.post(self.on_job_started, job.list_index)
			succeeded, reason = self.run_job(job)
			if self.cancel_event.is_set():
				break
			if not succeeded:
				failure = (job.source_path, reason)
				break
			self.post(self.on_success, job.source_path)
		if not self.cancel_event.is_set():
			self.post(self.on_queue_finished, failure)

	def run_job(self, job):
		try:
			os.makedirs(os.path.dirname(job.final_path), exist_ok=True)
		except OSError as error:
			log.error("xTrack: could not create the folder for %s: %s", job.final_path, error)
			return False, _("the folder for the converted files could not be created")

		durationSeconds = self.file_duration_seconds.get(job.source_path)
		if durationSeconds is None:
			durationSeconds, _label = get_file_duration(self.libs_path, job.source_path)

		def registerProcess(process):
			self.ffmpeg_process = process

		try:
			exitCode, stderrLines = runFfmpeg(
				job.command, self.output_path, durationSeconds,
				lambda progress: self.post(self.update_progress, progress), registerProcess,
			)
		except OSError as error:
			log.error("xTrack: could not run ffmpeg for %s: %s", job.source_path, error)
			removeQuietly(job.part_path)
			return False, _("ffmpeg could not be started")

		if self.cancel_event.is_set():
			removeQuietly(job.part_path)
			return False, ""

		if exitCode == 0:
			try:
				os.replace(job.part_path, job.final_path)
			except OSError as error:
				log.error("xTrack: could not move %s to %s: %s", job.part_path, job.final_path, error)
				removeQuietly(job.part_path)
				return False, _("the output file could not be written")
			return True, ""

		log.error(
			"xTrack: ffmpeg failed for %s (exit code %s): %s",
			job.source_path, exitCode, " | ".join(line.strip() for line in stderrLines),
		)
		removeQuietly(job.part_path)
		return False, summarizeFfmpegError(stderrLines)

	def on_job_started(self, listIndex):
		self.update_current_file_info(listIndex)
		self.progress_bar.SetValue(0)
		self.status_label.SetLabel(_("Starting conversion..."))

	def update_progress(self, progress):
		self.progress_bar.SetValue(progress)
		self.status_label.SetLabel(_("Converting: {}%").format(progress))

	def on_success(self, sourcePath):
		self.progress_bar.SetValue(100)
		self.status_label.SetLabel(_("Conversion complete!"))
		tones.beep(1000, 300)
		ui.message(limitSpokenText(_("Conversion complete for {}").format(os.path.basename(sourcePath))))

	def on_queue_finished(self, failure):
		self.is_running = False
		self.is_paused = False
		self.convert_btn.SetLabel(_("Convert"))
		self.cancel_btn.Enable(True)
		if failure:
			sourcePath, reason = failure
			self.status_label.SetLabel(_("Conversion failed."))
			tones.beep(300, 300)
			ui.message(limitSpokenText(
				_("Conversion failed for {name}: {reason}").format(name=os.path.basename(sourcePath), reason=reason)
			))
			return
		self.status_label.SetLabel(_("All conversions complete!"))
		message = _("All conversions complete!")
		if self.used_subfolder:
			message += " " + _("Saved in the folder {}").format(OUTPUT_SUBFOLDER_NAME)
		ui.message(limitSpokenText(message))
		self.EndModal(wx.ID_OK)

	def stop_conversion(self):
		self.is_closed = True
		self.cancel_event.set()
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
		self.stop_conversion()
		self.EndModal(wx.ID_CANCEL)

	def on_close(self, event):
		self.stop_conversion()
		self.EndModal(wx.ID_CANCEL)
