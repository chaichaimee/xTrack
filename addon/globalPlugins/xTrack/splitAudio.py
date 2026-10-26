# splitAudio.py

import wx
import os
import threading
import tones
import ui
from logHandler import log
import addonHandler
from .xTrackCore import get_config_path, load_config, save_config, get_file_duration
from .ffmpegSupport import (
	limitSpokenText, removeQuietly, partPathFor, runFfmpeg, parseTimeText, formatClock,
)

addonHandler.initTranslation()

MAX_TRACKS = 50


def validateEndTimes(endTimes, durationSeconds):
	"""Returns None when valid, otherwise a short reason key."""
	for index in range(1, len(endTimes)):
		if endTimes[index] <= endTimes[index - 1]:
			return "order"
	if endTimes and endTimes[0] <= 0:
		return "first"
	if endTimes and endTimes[-1] > durationSeconds + 0.5:
		return "duration"
	return None


class SplitAudioDialog(wx.Dialog):
	"""Dialog for splitting audio files into multiple tracks."""
	def __init__(self, parent, selected_file, libs_path):
		super().__init__(parent, title=_("Split Audio File"))
		if not selected_file:
			raise ValueError("No file was selected.")
		self.selected_file = selected_file
		self.libs_path = libs_path
		self.file_duration_seconds = 0
		self.file_duration_str = ""
		self.duration_ready = False
		self.output_path = os.path.dirname(self.selected_file)
		self.config_path = get_config_path()
		self.track_controls = []
		self.ffmpeg_process = None
		self.is_running = False
		self.is_closed = False
		self.cancel_event = threading.Event()
		self.init_ui()
		self.SetTitle(_("Split Audio: {}").format(os.path.basename(self.selected_file)))
		self.Bind(wx.EVT_CLOSE, self.on_close)
		threading.Thread(target=self.read_file_duration, daemon=True).start()

	def post(self, handler, *args):
		wx.CallAfter(self.run_if_open, handler, *args)

	def run_if_open(self, handler, *args):
		if self.is_closed:
			return
		handler(*args)

	def init_ui(self):
		main_sizer = wx.BoxSizer(wx.VERTICAL)

		file_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Selected File"))
		self.file_label = wx.StaticText(self, label=os.path.basename(self.selected_file))
		file_sizer.Add(self.file_label, 0, wx.EXPAND | wx.ALL, 5)
		self.duration_label = wx.StaticText(self, label=_("Duration: Calculating..."))
		file_sizer.Add(self.duration_label, 0, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(file_sizer, 0, wx.EXPAND | wx.ALL, 5)

		split_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Split Settings"))
		split_control_sizer = wx.BoxSizer(wx.HORIZONTAL)
		split_label = wx.StaticText(self, label=_("Number of tracks:"))
		split_control_sizer.Add(split_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.split_point_ctrl = wx.SpinCtrl(self, min=2, max=MAX_TRACKS, initial=2)
		self.split_point_ctrl.Bind(wx.EVT_SPINCTRL, self.on_split_point_change)
		split_control_sizer.Add(self.split_point_ctrl, 1, wx.EXPAND | wx.ALL, 5)
		split_sizer.Add(split_control_sizer, 0, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(split_sizer, 0, wx.EXPAND | wx.ALL, 5)

		self.tracks_panel = wx.ScrolledWindow(self)
		self.tracks_panel.SetScrollRate(10, 10)
		self.tracks_panel.SetMinSize((400, 300))
		self.tracks_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.tracks_panel, label=_("Tracks"))
		self.tracks_panel.SetSizer(self.tracks_sizer)
		main_sizer.Add(self.tracks_panel, 1, wx.EXPAND | wx.ALL, 5)

		self.progress_bar = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
		main_sizer.Add(self.progress_bar, 0, wx.EXPAND | wx.ALL, 5)
		self.status_label = wx.StaticText(self, label="")
		main_sizer.Add(self.status_label, 0, wx.EXPAND | wx.ALL, 5)

		btn_sizer = wx.StdDialogButtonSizer()
		self.split_btn = wx.Button(self, wx.ID_OK, label=_("Split"))
		self.split_btn.Bind(wx.EVT_BUTTON, self.on_split)
		btn_sizer.AddButton(self.split_btn)
		self.cancel_btn = wx.Button(self, wx.ID_CANCEL, label=_("Cancel"))
		self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
		btn_sizer.AddButton(self.cancel_btn)
		btn_sizer.Realize()
		main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

		self.SetSizer(main_sizer)
		self.Fit()

		# The saved track count has to be applied before the rows are built,
		# otherwise the spin box and the row list disagree about how many tracks exist.
		savedCount = load_config(self.config_path).get("SplitLastSplitPoint", 2)
		if not isinstance(savedCount, int) or not 2 <= savedCount <= MAX_TRACKS:
			savedCount = 2
		self.split_point_ctrl.SetValue(savedCount)
		self.create_track_controls()
		self.SetSize((500, 600))

	def create_track_controls(self):
		"""Rebuild one end-time row per track."""
		self.tracks_sizer.Clear(True)
		self.track_controls = []
		staticBox = self.tracks_sizer.GetStaticBox()
		for trackNumber in range(1, self.split_point_ctrl.GetValue() + 1):
			rowSizer = wx.BoxSizer(wx.HORIZONTAL)
			rowLabel = wx.StaticText(staticBox, label=_(" {:02d} End Time:").format(trackNumber))
			rowSizer.Add(rowLabel, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
			timeCtrl = wx.TextCtrl(staticBox)
			# Checking while typing would announce an error after every character
			# ("1:" is invalid on the way to "1:30"), so the check runs on leaving the field.
			timeCtrl.Bind(wx.EVT_KILL_FOCUS, self.on_track_time_leave)
			rowSizer.Add(timeCtrl, 1, wx.EXPAND | wx.ALL, 5)
			self.track_controls.append(timeCtrl)
			self.tracks_sizer.Add(rowSizer, 0, wx.EXPAND | wx.ALL, 5)
		self.update_track_controls()
		self.tracks_panel.Layout()
		self.tracks_panel.FitInside()
		self.Layout()

	def read_file_duration(self):
		durationSeconds, durationLabel = get_file_duration(self.libs_path, self.selected_file)
		self.file_duration_seconds = durationSeconds
		self.file_duration_str = durationLabel
		self.duration_ready = True
		if durationSeconds <= 0:
			log.error("xTrack: could not read the duration of %s", self.selected_file)
			self.post(self.duration_label.SetLabel, _("Duration: unknown"))
			return
		self.post(self.duration_label.SetLabel, _("Duration: {}").format(durationLabel))
		self.post(self.update_track_controls)

	def on_split_point_change(self, event):
		self.create_track_controls()
		event.Skip()

	def update_track_controls(self):
		"""Fill empty rows with evenly spaced end times once the duration is known."""
		if self.file_duration_seconds <= 0:
			return
		trackCount = len(self.track_controls)
		for index, timeCtrl in enumerate(self.track_controls):
			if timeCtrl.GetValue():
				continue
			if index == trackCount - 1:
				timeCtrl.SetValue(formatClock(self.file_duration_seconds))
			else:
				timeCtrl.SetValue(formatClock(((index + 1) * self.file_duration_seconds) / trackCount))

	def on_track_time_leave(self, event):
		ctrl = event.GetEventObject()
		event.Skip()
		timeText = ctrl.GetValue()
		if not timeText:
			return
		try:
			parseTimeText(timeText)
		except ValueError:
			ui.message(_("Invalid time format. Use seconds (e.g., 10), MM:SS (e.g., 1:30), or HH:MM:SS (e.g., 1:30:45)"))

	def show_error(self, message):
		wx.MessageBox(message, _("Error"), wx.OK | wx.ICON_ERROR)

	def on_split(self, event):
		if self.is_running:
			return
		if not self.duration_ready:
			ui.message(_("Still reading the file duration, please try again in a moment."))
			return
		if self.file_duration_seconds <= 0:
			self.show_error(_("The duration of this file could not be read."))
			return

		endTimes = []
		for index, ctrl in enumerate(self.track_controls):
			timeText = ctrl.GetValue().strip()
			if not timeText:
				self.show_error(_("Please fill in all time entries"))
				return
			try:
				endTimes.append(parseTimeText(timeText))
			except ValueError:
				self.show_error(_("Invalid time format for track {:02d}").format(index + 1))
				return

		problem = validateEndTimes(endTimes, self.file_duration_seconds)
		if problem == "order":
			self.show_error(_("Track end times must be in increasing order"))
			return
		if problem == "first":
			self.show_error(_("The first track must end after 0:00"))
			return
		if problem == "duration":
			self.show_error(_("Last track end time cannot exceed file duration"))
			return

		ffmpegPath = os.path.join(self.libs_path, "ffmpeg.exe")
		if not os.path.exists(ffmpegPath):
			self.show_error(_("ffmpeg.exe not found"))
			return

		self.is_running = True
		self.cancel_event.clear()
		self.split_btn.Enable(False)
		self.cancel_btn.Enable(False)
		self.status_label.SetLabel(_("Starting split operation..."))
		self.progress_bar.SetValue(0)
		threading.Thread(target=self.perform_split, args=(ffmpegPath, endTimes), daemon=True).start()

	def perform_split(self, ffmpegPath, endTimes):
		fileExtension = os.path.splitext(self.selected_file)[1].lower()
		baseName = os.path.splitext(os.path.basename(self.selected_file))[0]
		totalTracks = len(endTimes)
		createdCount = 0
		failedTracks = []

		def registerProcess(process):
			self.ffmpeg_process = process

		for index in range(totalTracks):
			if self.cancel_event.is_set():
				return
			trackNumber = index + 1
			startSeconds = endTimes[index - 1] if index > 0 else 0
			endSeconds = endTimes[index]
			trackSeconds = endSeconds - startSeconds
			self.post(self.status_label.SetLabel, _("Processing track {} of {}").format(trackNumber, totalTracks))

			finalName = f"{baseName}_track{trackNumber:02d}{fileExtension}"
			counter = 1
			while os.path.exists(os.path.join(self.output_path, finalName)):
				finalName = f"{baseName}_track{trackNumber:02d}_{counter}{fileExtension}"
				counter += 1
			finalPath = os.path.join(self.output_path, finalName)
			partPath = partPathFor(finalPath)

			# -t (a length) rather than -to keeps the cut identical across ffmpeg versions.
			command = [
				ffmpegPath, "-hide_banner", "-y",
				"-i", self.selected_file,
				"-ss", f"{startSeconds:.3f}",
				"-t", f"{trackSeconds:.3f}",
				"-c", "copy",
			]
			if fileExtension == ".flac":
				# Copying a FLAC stream keeps the header of the whole file, so each track
				# would declare the full length. Encoding FLAC again is lossless and exact.
				command += ["-c:a", "flac"]
			command += ["-progress", "pipe:1", "-nostats", partPath]

			def reportProgress(percent, index=index):
				overall = int(((index + percent / 100) / totalTracks) * 100)
				self.post(self.progress_bar.SetValue, overall)

			try:
				exitCode, stderrLines = runFfmpeg(command, self.output_path, trackSeconds, reportProgress, registerProcess)
			except OSError as error:
				log.error("xTrack: could not run ffmpeg for track %s: %s", trackNumber, error)
				removeQuietly(partPath)
				failedTracks.append(trackNumber)
				continue

			if self.cancel_event.is_set():
				removeQuietly(partPath)
				return
			if exitCode != 0:
				log.error(
					"xTrack: split track %s failed (exit code %s): %s",
					trackNumber, exitCode, " | ".join(line.strip() for line in stderrLines),
				)
				removeQuietly(partPath)
				failedTracks.append(trackNumber)
				continue
			try:
				os.replace(partPath, finalPath)
			except OSError as error:
				log.error("xTrack: could not move %s to %s: %s", partPath, finalPath, error)
				removeQuietly(partPath)
				failedTracks.append(trackNumber)
				continue
			createdCount += 1

		self.post(self.on_split_finished, createdCount, failedTracks)

	def on_split_finished(self, createdCount, failedTracks):
		self.is_running = False
		self.split_btn.Enable(True)
		self.cancel_btn.Enable(True)
		self.progress_bar.SetValue(100)
		if createdCount == 0:
			self.status_label.SetLabel(_("Split failed!"))
			tones.beep(300, 300)
			ui.message(_("Failed to split audio file"))
			return

		configData = load_config(self.config_path)
		configData["SplitLastSplitPoint"] = self.split_point_ctrl.GetValue()
		configData["SplitLastFile"] = self.selected_file
		save_config(self.config_path, configData)

		tones.beep(1000, 300)
		if failedTracks:
			failedText = ", ".join(f"{number:02d}" for number in failedTracks)
			self.status_label.SetLabel(_("Split finished with errors"))
			ui.message(limitSpokenText(
				_("{count} tracks created. Track {failed} could not be created.").format(count=createdCount, failed=failedText)
			))
			return
		self.status_label.SetLabel(_("Split complete! {} tracks created").format(createdCount))
		ui.message(_("Audio file split into {} tracks").format(createdCount))
		self.EndModal(wx.ID_OK)

	def stop_split(self):
		self.is_closed = True
		self.cancel_event.set()
		process = self.ffmpeg_process
		if process and process.poll() is None:
			try:
				process.terminate()
			except OSError as error:
				log.debugWarning("xTrack: could not stop ffmpeg: %s", error)

	def on_cancel(self, event):
		self.stop_split()
		self.EndModal(wx.ID_CANCEL)

	def on_close(self, event):
		self.stop_split()
		event.Skip()
