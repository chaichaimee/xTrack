# convertAudio.py

import wx
import os
import subprocess
import threading
import ui
import tones
import json
import queue
import time
from gui import guiHelper
from .xTrackCore import load_config, save_config, get_file_duration, get_config_path
import addonHandler

addonHandler.initTranslation()

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
		self.file_duration_seconds = 0
		self.output_path = os.path.dirname(self.selected_files[0]) if self.selected_files else os.getcwd()
		self.config_path = get_config_path()
		self.ffmpeg_process = None
		self.is_paused = False
		self.conversion_queue = queue.Queue()
		self.currently_processing = False
		self.file_durations = {}
		self.init_ui()
		self.SetTitle(_("Convert Audio: {} files").format(len(self.selected_files)))
		self.load_settings()
		self.Bind(wx.EVT_CLOSE, self.on_close)
		threading.Thread(target=self.load_file_durations, daemon=True).start()

	def load_file_durations(self):
		"""Load durations for all selected files."""
		for file_path in self.selected_files:
			duration_sec, duration_str = get_file_duration(self.libs_path, file_path)
			self.file_durations[file_path] = self.format_duration(duration_sec)
		wx.CallAfter(self.update_file_listbox)

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
			self.update_current_file_info(0)

	def get_file_display_name(self, file_path):
		"""Get display name with duration for listbox."""
		base_name = os.path.basename(file_path)
		duration = self.file_durations.get(file_path, _("Calculating..."))
		return f"{base_name} ({duration})"

	def update_file_listbox(self):
		"""Update file listbox with names and durations."""
		self.file_listbox.Set([self.get_file_display_name(f) for f in self.selected_files])

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

	def update_current_file_info(self, index):
		if index < len(self.selected_files):
			self.current_file_index = index
			self.current_file_label.SetLabel(os.path.basename(self.selected_files[index]))
			self.file_listbox.SetSelection(index)
			duration = self.file_durations.get(self.selected_files[index], _("Calculating..."))
			self.duration_label.SetLabel(_("File Duration: {}").format(duration))
			self.file_duration_seconds = self.get_duration_seconds(self.selected_files[index])

	def get_duration_seconds(self, file_path):
		"""Get duration in seconds for a file."""
		duration_sec, _ = get_file_duration(self.libs_path, file_path)
		return duration_sec

	def load_settings(self):
		config_data = load_config(self.config_path)
		self.format_ctrl.SetStringSelection(config_data.get("ConvertAudioFormat", "MP3"))
		self.quality_ctrl.SetStringSelection(config_data.get("ConvertAudioQuality", "320 kbps"))
		self.samplerate_ctrl.SetStringSelection(config_data.get("ConvertAudioSampleRate", "48 kHz"))
		self.volume_ctrl.SetStringSelection(config_data.get("ConvertAudioVolume", "100%"))
		self.on_format_change(None)

	def save_settings(self):
		config_data = load_config(self.config_path)
		config_data["ConvertAudioFormat"] = self.format_ctrl.GetStringSelection()
		config_data["ConvertAudioQuality"] = self.quality_ctrl.GetStringSelection()
		config_data["ConvertAudioSampleRate"] = self.samplerate_ctrl.GetStringSelection()
		config_data["ConvertAudioVolume"] = self.volume_ctrl.GetStringSelection()
		save_config(self.config_path, config_data)

	def get_file_duration(self):
		if self.current_file_index < len(self.selected_files):
			current_file = self.selected_files[self.current_file_index]
			duration_sec, duration_str = get_file_duration(self.libs_path, current_file)
			self.file_duration_seconds = duration_sec
			wx.CallAfter(self.duration_label.SetLabel, _("File Duration: {}").format(self.format_duration(duration_sec)))

	def on_convert_or_pause(self, event):
		if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
			self.toggle_pause()
		else:
			self.on_convert(event)

	def toggle_pause(self):
		if not self.ffmpeg_process:
			return
		
		if self.is_paused:
			self.ffmpeg_process.send_signal(subprocess.signal.SIGCONT)
			self.is_paused = False
			self.convert_btn.SetLabel(_("Pause"))
			self.status_label.SetLabel(_("Resuming..."))
		else:
			self.ffmpeg_process.send_signal(subprocess.signal.SIGSTOP)
			self.is_paused = True
			self.convert_btn.SetLabel(_("Resume"))
			self.status_label.SetLabel(_("Paused."))
		
		ui.message(self.status_label.GetLabel())

	def on_convert(self, event):
		self.save_settings()
		
		# Add all files to queue (including those with same format)
		for file_path in self.selected_files:
			self.conversion_queue.put(file_path)
		
		# Start processing
		self.currently_processing = True
		self.process_next_file()

	def process_next_file(self):
		if self.conversion_queue.empty():
			self.currently_processing = False
			wx.CallAfter(self.on_all_conversions_complete)
			return
			
		file_path = self.conversion_queue.get()
		self.update_current_file_info(self.selected_files.index(file_path))
		
		output_format = self.format_ctrl.GetStringSelection().lower()
		quality_selection = self.quality_ctrl.GetStringSelection()
		samplerate_str = self.samplerate_ctrl.GetStringSelection().replace(' kHz', '000')
		volume_percent = int(self.volume_ctrl.GetStringSelection().replace('%', '')) / 100
		
		base_name = os.path.splitext(os.path.basename(file_path))[0]
		input_ext = os.path.splitext(file_path)[1].lower()
		input_format = input_ext[1:] if input_ext else ""  # Remove the dot
		
		# Create output filename based on input format and output format
		# If input format is same as output format, append "_converted" to avoid overwriting
		if input_format == output_format:
			output_file = f"{base_name}_converted.{output_format}"
		else:
			output_file = f"{base_name}.{output_format}"
		
		output_path = os.path.join(self.output_path, output_file)
		
		ffmpeg_path = os.path.join(self.libs_path, "ffmpeg.exe")
		if not os.path.exists(ffmpeg_path):
			ui.message(_("ffmpeg.exe not found"))
			return
		
		# Volume boosts above 100% can push the signal past 0 dBFS, so an
		# alimiter is chained on afterwards to catch the peaks and prevent
		# clipping. Unboosted or reduced volume never needs limiting.
		audioFilterChain = f"volume={volume_percent}"
		if volume_percent > 1.0:
			audioFilterChain += ",alimiter=level_in=1:level_out=1:limit=0.99"
		
		# Build command based on format
		cmd = [
			ffmpeg_path,
			"-i", file_path,
			"-vn",  # No video
			"-ar", samplerate_str,
			"-af", audioFilterChain,
			"-progress", "pipe:1",
			"-nostats",
			"-y",  # Overwrite output file if exists
		]
		
		preset = AUDIO_FORMAT_PRESETS.get(output_format, AUDIO_FORMAT_PRESETS["mp3"])
		cmd.extend(preset["codecArgs"](quality_selection))
		
		# Some formats (M4R, ALAC) are not recognized by ffmpeg's
		# extension-based muxer lookup, so the container must be forced.
		if "container" in preset:
			cmd.extend(["-f", preset["container"]])
		
		cmd.append(output_path)
		
		wx.CallAfter(self.convert_btn.SetLabel, _("Pause"))
		wx.CallAfter(self.cancel_btn.Enable, False)
		wx.CallAfter(self.status_label.SetLabel, _("Starting conversion..."))
		wx.CallAfter(self.progress_bar.SetValue, 0)
		
		def run_conversion():
			self.ffmpeg_process = None
			try:
				self.ffmpeg_process = subprocess.Popen(
					cmd,
					stdout=subprocess.PIPE,
					stderr=subprocess.PIPE,
					creationflags=subprocess.CREATE_NO_WINDOW,
					cwd=self.output_path,
					text=True,
					encoding='utf-8',
					errors='ignore'
				)
				
				while True:
					line = self.ffmpeg_process.stdout.readline()
					if not line:
						break
					if "out_time_ms=" in line:
						try:
							time_ms = int(line.split("=")[1])
							current_time_seconds = time_ms / 1000000
							if self.file_duration_seconds > 0:
								progress = int((current_time_seconds / self.file_duration_seconds) * 100)
								if progress >= 0 and progress <= 100:
									wx.CallAfter(self.update_progress, progress)
						except (ValueError, IndexError):
							continue
				
				self.ffmpeg_process.wait()
				
				if self.ffmpeg_process.returncode == 0:
					wx.CallAfter(self.on_success)
				else:
					stderr_output = self.ffmpeg_process.stderr.read()
					wx.CallAfter(self.on_failure, stderr_output)
					
			except Exception as e:
				wx.CallAfter(self.on_failure, str(e))
			finally:
				if self.ffmpeg_process:
					self.ffmpeg_process.stdout.close()
					self.ffmpeg_process.stderr.close()
				self.ffmpeg_process = None
				# Process next file
				if self.currently_processing:
					self.process_next_file()
		
		threading.Thread(target=run_conversion, daemon=True).start()

	def update_progress(self, progress):
		self.progress_bar.SetValue(progress)
		self.status_label.SetLabel(_("Converting: {}%").format(progress))

	def on_success(self):
		self.progress_bar.SetValue(100)
		self.status_label.SetLabel(_("Conversion complete!"))
		try:
			tones.beep(1000, 300)  # High tone for success
		except Exception:
			pass
		ui.message(_("Conversion complete for {}").format(os.path.basename(self.selected_files[self.current_file_index])))

	def on_all_conversions_complete(self):
		self.convert_btn.SetLabel(_("Convert"))
		self.cancel_btn.Enable(True)
		self.status_label.SetLabel(_("All conversions complete!"))
		ui.message(_("All conversions complete!"))
		self.EndModal(wx.ID_OK)

	def on_failure(self, error_message):
		self.status_label.SetLabel(_("Conversion failed."))
		ui.message(_("Conversion failed: {}").format(error_message))
		self.convert_btn.SetLabel(_("Convert"))
		self.cancel_btn.Enable(True)
		# Stop processing further files
		self.currently_processing = False

	def on_cancel(self, event):
		if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
			try:
				self.ffmpeg_process.terminate()
			except Exception:
				pass
		# Clear queue
		while not self.conversion_queue.empty():
			try:
				self.conversion_queue.get_nowait()
			except queue.Empty:
				break
		self.currently_processing = False
		self.EndModal(wx.ID_CANCEL)

	def on_close(self, event):
		if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
			try:
				self.ffmpeg_process.terminate()
			except Exception:
				pass
		# Clear queue
		while not self.conversion_queue.empty():
			try:
				self.conversion_queue.get_nowait()
			except queue.Empty:
				break
		self.currently_processing = False
		self.EndModal(wx.ID_CANCEL)

# convertMP3toMP4.py

import wx
import os
import subprocess
import threading
import ui
import tones
import tempfile
import queue
from gui import guiHelper
from .xTrackCore import get_file_duration
import addonHandler

addonHandler.initTranslation()

class ConvertMP3toMP4Dialog(wx.Dialog):
	"""Dialog for converting an MP3 file and a photo to an MP4 video."""
	def __init__(self, parent, selected_file, libs_path):
		super().__init__(parent, title=_("Convert MP3 to MP4"))
		self.selected_mp3 = selected_file
		self.libs_path = libs_path
		self.selected_photos = []
		self.output_path = os.path.dirname(self.selected_mp3)
		self.mp3_duration_seconds = 0
		self.ffmpeg_process = None
		self.is_paused = False
		self.loop_duration = 5  # Default loop duration in seconds
		self.stderr_queue = queue.Queue()
		self.init_ui()
		self.SetTitle(_("Convert MP3 to MP4: {}").format(os.path.basename(self.selected_mp3)))
		threading.Thread(target=self.get_mp3_duration, daemon=True).start()
		self.Bind(wx.EVT_CLOSE, self.on_close)

	def init_ui(self):
		main_sizer = wx.BoxSizer(wx.VERTICAL)
		
		# MP3 file info
		mp3_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Selected MP3 File"))
		self.mp3_label = wx.StaticText(self, label=os.path.basename(self.selected_mp3))
		mp3_sizer.Add(self.mp3_label, 0, wx.EXPAND | wx.ALL, 5)
		self.mp3_duration_label = wx.StaticText(self, label=_("Duration: Calculating..."))
		mp3_sizer.Add(self.mp3_duration_label, 0, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(mp3_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Photo selection
		photo_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Photo List"))
		self.photo_listbox = wx.ListBox(self, style=wx.LB_SINGLE)
		photo_sizer.Add(self.photo_listbox, 1, wx.EXPAND | wx.ALL, 5)
		
		photo_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
		self.add_photo_btn = wx.Button(self, label=_("Add Photo..."))
		self.add_photo_btn.Bind(wx.EVT_BUTTON, self.on_add_photo)
		photo_btn_sizer.Add(self.add_photo_btn, 1, wx.EXPAND | wx.ALL, 5)
		
		self.remove_photo_btn = wx.Button(self, label=_("Remove Photo"))
		self.remove_photo_btn.Bind(wx.EVT_BUTTON, self.on_remove_photo)
		photo_btn_sizer.Add(self.remove_photo_btn, 1, wx.EXPAND | wx.ALL, 5)
		
		photo_sizer.Add(photo_btn_sizer, 0, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(photo_sizer, 1, wx.EXPAND | wx.ALL, 5)
		
		# Loop duration setting (initially hidden)
		self.loop_sizer = wx.BoxSizer(wx.HORIZONTAL)
		self.loop_label = wx.StaticText(self, label=_("Loop Duration (seconds):"))
		self.loop_sizer.Add(self.loop_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.loop_duration_ctrl = wx.SpinCtrl(self, min=1, max=3600, initial=5)
		self.loop_sizer.Add(self.loop_duration_ctrl, 1, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(self.loop_sizer, 0, wx.EXPAND | wx.ALL, 5)
		self.loop_sizer.ShowItems(False)  # Hide initially
		
		# Background color setting with 12 colors
		color_sizer = wx.BoxSizer(wx.HORIZONTAL)
		color_label = wx.StaticText(self, label=_("Background Color:"))
		color_sizer.Add(color_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		color_choices = [
			_("None"), _("Black"), _("White"), _("Red"), _("Green"), _("Blue"),
			_("Yellow"), _("Magenta"), _("Cyan"), _("Gray"), _("Orange"), _("Purple")
		]
		self.color_ctrl = wx.ComboBox(self, choices=color_choices, style=wx.CB_READONLY)
		self.color_ctrl.SetStringSelection(_("None"))
		color_sizer.Add(self.color_ctrl, 1, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(color_sizer, 0, wx.EXPAND | wx.ALL, 5)
		
		# Auto crop setting
		self.crop_checkbox = wx.CheckBox(self, label=_("Auto crop images to 16:9 aspect ratio"))
		self.crop_checkbox.SetValue(True)
		main_sizer.Add(self.crop_checkbox, 0, wx.EXPAND | wx.ALL, 5)
		
		# Progress bar
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
		self.CentreOnParent()

	def get_mp3_duration(self):
		duration_sec, duration_str = get_file_duration(self.libs_path, self.selected_mp3)
		self.mp3_duration_seconds = duration_sec
		wx.CallAfter(self.mp3_duration_label.SetLabel, _("Duration: {}").format(duration_str))

	def on_add_photo(self, event):
		wildcard = _("Image files (*.png;*.jpg;*.jpeg;*.bmp)|*.png;*.jpg;*.jpeg;*.bmp")
		with wx.FileDialog(self, _("Select photos"), wildcard=wildcard, 
						  style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE) as dlg:
			if dlg.ShowModal() == wx.ID_OK:
				paths = dlg.GetPaths()
				for path in paths:
					if path not in self.selected_photos and os.path.exists(path):
						try:
							with open(path, 'rb') as f:
								f.read(1)  # Test if file is accessible
							self.selected_photos.append(path)
						except Exception as e:
							ui.message(_("Invalid image file: {}").format(path))
				self.update_photo_listbox()
				# Show/hide loop duration based on number of photos
				self.update_loop_duration_visibility()

	def on_remove_photo(self, event):
		selection = self.photo_listbox.GetSelection()
		if selection != wx.NOT_FOUND:
			self.selected_photos.pop(selection)
			self.update_photo_listbox()
			# Show/hide loop duration based on number of photos
			self.update_loop_duration_visibility()

	def update_photo_listbox(self):
		self.photo_listbox.Clear()
		for photo in self.selected_photos:
			self.photo_listbox.Append(os.path.basename(photo))

	def update_loop_duration_visibility(self):
		"""Show loop duration control only when there are multiple photos."""
		if len(self.selected_photos) > 1:
			self.loop_sizer.ShowItems(True)
		else:
			self.loop_sizer.ShowItems(False)
		self.Layout()
		self.Fit()

	def on_convert_or_pause(self, event):
		if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
			self.toggle_pause()
		else:
			self.on_convert(event)

	def toggle_pause(self):
		if not self.ffmpeg_process:
			return
		
		if self.is_paused:
			self.ffmpeg_process.send_signal(subprocess.signal.SIGCONT)
			self.is_paused = False
			self.convert_btn.SetLabel(_("Pause"))
			self.status_label.SetLabel(_("Resuming..."))
		else:
			self.ffmpeg_process.send_signal(subprocess.signal.SIGSTOP)
			self.is_paused = True
			self.convert_btn.SetLabel(_("Resume"))
			self.status_label.SetLabel(_("Paused."))
		
		ui.message(self.status_label.GetLabel())

	def on_convert(self, event):
		self.loop_duration = self.loop_duration_ctrl.GetValue()
		selected_color = self.color_ctrl.GetStringSelection()
		
		# If no photos selected but a background color is chosen, use color background
		if not self.selected_photos and selected_color == _("None"):
			ui.message(_("Please select at least one photo or choose a background color."))
			return
		
		base_name = os.path.splitext(os.path.basename(self.selected_mp3))[0]
		output_file = f"{base_name}.mp4"
		output_path = os.path.join(self.output_path, output_file)
		
		ffmpeg_path = os.path.join(self.libs_path, "ffmpeg.exe")
		if not os.path.exists(ffmpeg_path):
			ui.message(_("ffmpeg.exe not found"))
			return
		
		# Build FFmpeg command based on whether we have photos or just a background color
		if self.selected_photos:
			# For single photo, use it for the entire duration with proper video encoding
			if len(self.selected_photos) == 1:
				photo = self.selected_photos[0]
				cmd = [
					ffmpeg_path,
					"-loop", "1",
					"-i", photo,
					"-i", self.selected_mp3,
					"-c:v", "libx264",
					"-preset", "medium",
					"-tune", "stillimage",
					"-c:a", "aac",
					"-b:a", "192k",
					"-pix_fmt", "yuv420p",
					"-shortest",
					"-movflags", "+faststart"
				]
				
				# Add crop filter if enabled
				if self.crop_checkbox.GetValue():
					cmd.extend(["-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2"])
			else:
				# For multiple photos, use proper concat with image2 demuxer
				temp_dir = tempfile.gettempdir()
				concat_file = os.path.join(temp_dir, f"xtrack_concat_{id(self)}.txt")
				try:
					with open(concat_file, 'w', encoding='utf-8') as f:
						for photo in self.selected_photos:
							escaped_photo = photo.replace("'", "'\\''")
							f.write(f"file '{escaped_photo}'\n")
							f.write(f"duration {self.loop_duration}\n")
				except Exception as e:
					ui.message(_("Failed to create file list: {}").format(str(e)))
					return
				
				cmd = [
					ffmpeg_path,
					"-f", "concat",
					"-safe", "0",
					"-protocol_whitelist", "file,pipe,crypto,data",
					"-i", concat_file,
					"-i", self.selected_mp3,
					"-c:v", "libx264",
					"-preset", "medium",
					"-c:a", "aac",
					"-b:a", "192k",
					"-pix_fmt", "yuv420p",
					"-shortest",
					"-movflags", "+faststart"
				]
				
				# Add crop filter if enabled
				if self.crop_checkbox.GetValue():
					cmd.extend(["-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2"])
		else:
			# Use color background
			color_map = {
				_("Black"): "000000",
				_("White"): "FFFFFF",
				_("Red"): "FF0000",
				_("Green"): "00FF00",
				_("Blue"): "0000FF",
				_("Yellow"): "FFFF00",
				_("Magenta"): "FF00FF",
				_("Cyan"): "00FFFF",
				_("Gray"): "808080",
				_("Orange"): "FFA500",
				_("Purple"): "800080"
			}
			
			color_hex = color_map.get(selected_color, "000000")
			duration = self.mp3_duration_seconds
			
			cmd = [
				ffmpeg_path,
				"-f", "lavfi",
				"-i", f"color=c={color_hex}:s=1920x1080:d={duration}",
				"-i", self.selected_mp3,
				"-c:v", "libx264",
				"-preset", "medium",
				"-c:a", "aac",
				"-b:a", "192k",
				"-shortest",
				"-movflags", "+faststart"
			]
		
		# Add common parameters
		cmd.extend([
			"-progress", "pipe:1",
			"-nostats",
			"-y",
			output_path
		])
		
		wx.CallAfter(self.convert_btn.SetLabel, _("Pause"))
		wx.CallAfter(self.cancel_btn.Enable, False)
		wx.CallAfter(self.status_label.SetLabel, _("Starting conversion..."))
		wx.CallAfter(self.progress_bar.SetValue, 0)
		
		def run_conversion():
			self.ffmpeg_process = None
			try:
				self.ffmpeg_process = subprocess.Popen(
					cmd,
					stdout=subprocess.PIPE,
					stderr=subprocess.PIPE,
					creationflags=subprocess.CREATE_NO_WINDOW,
					cwd=self.output_path,
					text=True,
					encoding='utf-8',
					errors='ignore'
				)
				
				# Start thread to read stderr to prevent buffer overflow
				def read_stderr():
					for line in iter(self.ffmpeg_process.stderr.readline, ''):
						self.stderr_queue.put(line)
				
				stderr_thread = threading.Thread(target=read_stderr, daemon=True)
				stderr_thread.start()
				
				while True:
					line = self.ffmpeg_process.stdout.readline()
					if not line:
						break
					if "out_time_ms=" in line:
						try:
							time_ms = int(line.split("=")[1])
							current_time_seconds = time_ms / 1000000
							if self.mp3_duration_seconds > 0:
								progress = int((current_time_seconds / self.mp3_duration_seconds) * 100)
								if progress >= 0 and progress <= 100:
									wx.CallAfter(self.update_progress, progress)
						except (ValueError, IndexError):
							continue
				
				self.ffmpeg_process.wait()
				
				# Join stderr thread
				stderr_thread.join(timeout=5)
				stderr_output = ''.join(list(self.stderr_queue.queue))
				
				# Clean up temporary file if it exists
				if self.selected_photos and len(self.selected_photos) > 1:
					try:
						concat_file = os.path.join(tempfile.gettempdir(), f"xtrack_concat_{id(self)}.txt")
						if os.path.exists(concat_file):
							os.remove(concat_file)
					except Exception:
						pass
				
				if self.ffmpeg_process.returncode == 0:
					wx.CallAfter(self.on_success)
				else:
					wx.CallAfter(self.on_failure, stderr_output)
					
			except Exception as e:
				wx.CallAfter(self.on_failure, str(e))
			finally:
				if self.ffmpeg_process:
					self.ffmpeg_process.stdout.close()
					self.ffmpeg_process.stderr.close()
				self.ffmpeg_process = None
		
		threading.Thread(target=run_conversion, daemon=True).start()

	def update_progress(self, progress):
		self.progress_bar.SetValue(progress)
		self.status_label.SetLabel(_("Converting: {}%").format(progress))

	def on_success(self):
		self.progress_bar.SetValue(100)
		self.status_label.SetLabel(_("Conversion complete!"))
		try:
			tones.beep(1000, 300)  # High tone for success
		except Exception:
			pass
		ui.message(_("Conversion complete!"))
		self.convert_btn.SetLabel(_("Convert"))
		self.cancel_btn.Enable(True)
		wx.CallAfter(self.EndModal, wx.ID_OK)

	def on_failure(self, error_message):
		self.status_label.SetLabel(_("Conversion failed."))
		ui.message(_("Conversion failed: {}").format(error_message))
		self.convert_btn.SetLabel(_("Convert"))
		self.cancel_btn.Enable(True)

	def on_cancel(self, event):
		if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
			try:
				self.ffmpeg_process.terminate()
			except Exception:
				pass
		self.EndModal(wx.ID_CANCEL)

	def on_close(self, event):
		if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
			try:
				self.ffmpeg_process.terminate()
			except Exception:
				pass
		self.EndModal(wx.ID_CANCEL)







