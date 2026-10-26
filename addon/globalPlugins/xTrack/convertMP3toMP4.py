# convertMP3toMP4.py

import wx
import os
import tempfile
import threading
import ui
import tones
from logHandler import log
from .xTrackCore import get_file_duration, get_unique_filename
from .ffmpegSupport import (
	limitSpokenText, summarizeFfmpegError, setProcessSuspended, removeQuietly, partPathFor, runFfmpeg,
)
import addonHandler

addonHandler.initTranslation()

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
MAX_CONCAT_ENTRIES = 20000
LETTERBOX_FILTER = (
	f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
	f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1"
)
EVEN_SIZE_FILTER = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
COLOR_HEX = {
	"Black": "000000", "White": "FFFFFF", "Red": "FF0000", "Green": "00FF00",
	"Blue": "0000FF", "Yellow": "FFFF00", "Magenta": "FF00FF", "Cyan": "00FFFF",
	"Gray": "808080", "Orange": "FFA500", "Purple": "800080",
}


def buildPhotoSequence(photos, loopSeconds, targetSeconds):
	"""Cycle the photo list until it covers the audio, so -shortest ends on the audio."""
	if targetSeconds <= 0:
		return list(photos)
	needed = max(len(photos), int(targetSeconds // loopSeconds) + 1)
	needed = min(needed, MAX_CONCAT_ENTRIES)
	return [photos[index % len(photos)] for index in range(needed)]


def writeConcatFile(sequence, loopSeconds):
	handle, concatPath = tempfile.mkstemp(prefix="xtrack_concat_", suffix=".txt")
	with os.fdopen(handle, "w", encoding="utf-8") as concatFile:
		for photo in sequence:
			escaped = photo.replace("\\", "/").replace("'", "'\\''")
			concatFile.write(f"file '{escaped}'\n")
			concatFile.write(f"duration {loopSeconds}\n")
		# The concat demuxer ignores the duration of the final entry unless
		# that entry is listed once more without one.
		escaped = sequence[-1].replace("\\", "/").replace("'", "'\\''")
		concatFile.write(f"file '{escaped}'\n")
	return concatPath


class ConvertMP3toMP4Dialog(wx.Dialog):
	"""Dialog for converting an MP3 file and a photo to an MP4 video."""
	def __init__(self, parent, selected_file, libs_path):
		super().__init__(parent, title=_("Convert MP3 to MP4"))
		self.selected_mp3 = selected_file
		self.libs_path = libs_path
		self.selected_photos = []
		self.output_path = os.path.dirname(self.selected_mp3)
		self.mp3_duration_seconds = 0
		self.duration_ready = False
		self.ffmpeg_process = None
		self.is_paused = False
		self.is_running = False
		self.is_closed = False
		self.cancel_event = threading.Event()
		self.loop_duration = 5
		self.init_ui()
		self.SetTitle(_("Convert MP3 to MP4: {}").format(os.path.basename(self.selected_mp3)))
		threading.Thread(target=self.get_mp3_duration, daemon=True).start()
		self.Bind(wx.EVT_CLOSE, self.on_close)

	def post(self, handler, *args):
		wx.CallAfter(self.run_if_open, handler, *args)

	def run_if_open(self, handler, *args):
		if self.is_closed:
			return
		handler(*args)

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
		durationSeconds, durationLabel = get_file_duration(self.libs_path, self.selected_mp3)
		self.mp3_duration_seconds = durationSeconds
		self.duration_ready = True
		self.post(self.mp3_duration_label.SetLabel, _("Duration: {}").format(durationLabel))

	def on_add_photo(self, event):
		wildcard = _("Image files (*.png;*.jpg;*.jpeg;*.bmp)|*.png;*.jpg;*.jpeg;*.bmp")
		with wx.FileDialog(self, _("Select photos"), wildcard=wildcard,
						  style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE) as dlg:
			if dlg.ShowModal() != wx.ID_OK:
				return
			for path in dlg.GetPaths():
				if path in self.selected_photos or not os.path.exists(path):
					continue
				try:
					with open(path, 'rb') as photoFile:
						photoFile.read(1)
				except OSError:
					ui.message(_("Invalid image file: {}").format(os.path.basename(path)))
					continue
				self.selected_photos.append(path)
		self.update_photo_listbox()
		self.update_loop_duration_visibility()

	def on_remove_photo(self, event):
		selection = self.photo_listbox.GetSelection()
		if selection == wx.NOT_FOUND:
			return
		self.selected_photos.pop(selection)
		self.update_photo_listbox()
		if self.selected_photos:
			self.photo_listbox.SetSelection(min(selection, len(self.selected_photos) - 1))
		self.update_loop_duration_visibility()

	def update_photo_listbox(self):
		self.photo_listbox.Clear()
		for photo in self.selected_photos:
			self.photo_listbox.Append(os.path.basename(photo))

	def update_loop_duration_visibility(self):
		"""Show loop duration control only when there are multiple photos."""
		self.loop_sizer.ShowItems(len(self.selected_photos) > 1)
		self.Layout()
		self.Fit()

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

	def build_command(self, ffmpegPath, partPath):
		"""Returns (command, concatPath); concatPath is None unless a photo list file was written."""
		selectedColor = self.color_ctrl.GetStringSelection()
		photos = self.selected_photos
		cropEnabled = self.crop_checkbox.GetValue()
		concatPath = None

		videoArgs = ["-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p"]
		audioArgs = ["-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart"]

		if len(photos) == 1:
			# Odd image dimensions make libx264 refuse the input, so the size
			# is always normalized even when 16:9 fitting is off.
			videoFilter = LETTERBOX_FILTER if cropEnabled else EVEN_SIZE_FILTER
			command = [
				ffmpegPath, "-hide_banner", "-loop", "1", "-framerate", "25", "-i", photos[0],
				"-i", self.selected_mp3, *videoArgs, "-tune", "stillimage", "-vf", videoFilter,
				"-r", "25", *audioArgs,
			]
		elif photos:
			sequence = buildPhotoSequence(photos, self.loop_duration, self.mp3_duration_seconds)
			concatPath = writeConcatFile(sequence, self.loop_duration)
			# Photos of different sizes cannot share one video stream, so the
			# multi-photo path always fits every image into the same frame.
			command = [
				ffmpegPath, "-hide_banner", "-f", "concat", "-safe", "0",
				"-protocol_whitelist", "file,pipe,crypto,data", "-i", concatPath,
				"-i", self.selected_mp3, *videoArgs, "-vf", LETTERBOX_FILTER,
				"-r", "25", *audioArgs,
			]
		else:
			colorHex = COLOR_HEX.get(self.color_choice_key(selectedColor), "000000")
			# No fixed duration on the color source: -shortest ends the video with the audio.
			command = [
				ffmpegPath, "-hide_banner", "-f", "lavfi",
				"-i", f"color=c={colorHex}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r=25",
				"-i", self.selected_mp3, *videoArgs, *audioArgs,
			]

		command.extend(["-progress", "pipe:1", "-nostats", "-y", partPath])
		return command, concatPath

	def color_choice_key(self, translatedName):
		colorKeys = ["Black", "White", "Red", "Green", "Blue", "Yellow", "Magenta", "Cyan", "Gray", "Orange", "Purple"]
		for key in colorKeys:
			if _(key) == translatedName:
				return key
		return "Black"

	def on_convert(self, event):
		if self.is_running:
			return
		self.loop_duration = self.loop_duration_ctrl.GetValue()
		if not self.selected_photos and self.color_ctrl.GetStringSelection() == _("None"):
			ui.message(_("Please select at least one photo or choose a background color."))
			return
		if len(self.selected_photos) > 1 and not self.duration_ready:
			ui.message(_("Still reading the audio length, please try again in a moment."))
			return

		ffmpegPath = os.path.join(self.libs_path, "ffmpeg.exe")
		if not os.path.exists(ffmpegPath):
			ui.message(_("ffmpeg.exe not found"))
			return

		baseName = os.path.splitext(os.path.basename(self.selected_mp3))[0]
		finalPath = os.path.join(self.output_path, get_unique_filename(self.output_path, baseName, "mp4"))
		partPath = partPathFor(finalPath)
		try:
			command, concatPath = self.build_command(ffmpegPath, partPath)
		except OSError as error:
			log.error("xTrack: could not write the photo list: %s", error)
			ui.message(_("Failed to create file list"))
			return

		self.is_running = True
		self.is_paused = False
		self.cancel_event.clear()
		self.convert_btn.SetLabel(_("Pause"))
		self.cancel_btn.Enable(False)
		self.status_label.SetLabel(_("Starting conversion..."))
		self.progress_bar.SetValue(0)
		threading.Thread(target=self.run_conversion, args=(command, concatPath, partPath, finalPath), daemon=True).start()

	def run_conversion(self, command, concatPath, partPath, finalPath):
		def registerProcess(process):
			self.ffmpeg_process = process

		try:
			exitCode, stderrLines = runFfmpeg(
				command, self.output_path, self.mp3_duration_seconds,
				lambda progress: self.post(self.update_progress, progress), registerProcess,
			)
		except OSError as error:
			log.error("xTrack: could not run ffmpeg: %s", error)
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
				"xTrack: MP3 to MP4 failed (exit code %s): %s",
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

	def update_progress(self, progress):
		self.progress_bar.SetValue(progress)
		self.status_label.SetLabel(_("Converting: {}%").format(progress))

	def finish_run(self):
		self.is_running = False
		self.is_paused = False
		self.convert_btn.SetLabel(_("Convert"))
		self.cancel_btn.Enable(True)

	def on_success(self, finalPath):
		self.finish_run()
		self.progress_bar.SetValue(100)
		self.status_label.SetLabel(_("Conversion complete!"))
		tones.beep(1000, 300)
		ui.message(limitSpokenText(_("Conversion complete: {}").format(os.path.basename(finalPath))))
		self.EndModal(wx.ID_OK)

	def on_failure(self, reason):
		self.finish_run()
		self.status_label.SetLabel(_("Conversion failed."))
		tones.beep(300, 300)
		ui.message(limitSpokenText(_("Conversion failed: {}").format(reason)))

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
