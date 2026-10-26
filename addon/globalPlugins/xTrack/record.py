# record.py

import os
import threading
import subprocess
import ctypes
import wx
import gui
import config
from logHandler import log
import ui
import addonHandler
import tones
import comtypes.client
from comtypes import COMError
from .ffmpegSupport import limitSpokenText
from .xTrackCore import normalize_record_settings, default_recording_folder

addonHandler.initTranslation()

BACKEND_AVAILABLE = False
WasapiSoundRecorder = None
list_audio_devices = None

try:
	from recorder_backend import WasapiSoundRecorder, list_audio_devices
	BACKEND_AVAILABLE = True
	log.info("xTrack: WasapiSoundRecorder backend loaded successfully")
except (ImportError, OSError) as e:
	log.error(f"xTrack: Failed to import WasapiSoundRecorder: {e}")

# Returned by Recorder.stop() when the recording thread is still writing the
# final file (a long merge can take longer than a key press should block).
STOP_PENDING = object()
STOP_WAIT_SECONDS = 3
RECORDING_ERRORS = (OSError, RuntimeError, ValueError, TypeError, KeyError, IndexError, AttributeError, COMError)
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
SW_RESTORE = 9


def readIntSetting(settings, key, default):
	try:
		return int(settings.get(key, default))
	except (TypeError, ValueError):
		return default


class Recorder:
	COUNT_IN_BEEP_SEQUENCE = [(440, 200), (440, 200), (440, 200), (880, 200)]
	COUNT_IN_BEEP_GAP_SECONDS = 0.5

	def __init__(self):
		self.is_recording = False
		self.is_paused = False
		self.output_files = []
		self._thread = None
		self._stop_event = threading.Event()
		self._pause_event = threading.Event()
		self._stateLock = threading.Lock()
		self._threadDone = True
		self._announceLate = False
		self._settings = {}
		self._setupError = ""
		self._mergeWarning = False

		self.addon_dir = os.path.dirname(__file__)
		self.backend_recorder = None

		self.recording_mode_map = {
			"system_and_mic": "system audio and microphone",
			"system_and_mic_sep": "system audio and microphone (separate files)",
			"system_only": "system audio only",
			"mic_only": "microphone only"
		}

	def _resolve_ffmpeg_path(self):
		# ffmpeg.exe lives in the shared "ChaiChaimee\bin" hub, not in this
		# add-on's own libs folder. addonCore imports this module, so the name
		# is imported when needed rather than at module load.
		try:
			from .addonCore import SharedFfmpegEXE
			return SharedFfmpegEXE
		except ImportError as importError:
			log.error(f"xTrack: shared ffmpeg path is not available, using the bundled location: {importError}")
			return os.path.join(self.addon_dir, "libs", "ffmpeg.exe")

	def _setup_backend_recorder(self):
		global BACKEND_AVAILABLE, WasapiSoundRecorder, list_audio_devices

		if not BACKEND_AVAILABLE:
			try:
				from recorder_backend import WasapiSoundRecorder, list_audio_devices
				BACKEND_AVAILABLE = True
				log.info("xTrack: WasapiSoundRecorder loaded on demand")
			except (ImportError, OSError) as e:
				log.error(f"xTrack: Backend recorder not available: {e}")
				self._setupError = _("the audio backend is not available")
				return False

		settings = self._settings
		try:
			mode = settings.get("recordingMode", "system_and_mic")
			fmt = settings.get("format", "mp3")
			if fmt not in ("mp3", "wav", "m4a"):
				fmt = "mp3"

			backend_mode = self.recording_mode_map.get(mode, "system audio and microphone")
			dest = settings.get("destinationFolder") or default_recording_folder()

			self.backend_recorder = WasapiSoundRecorder(
				recording_format=fmt,
				recording_folder=dest,
				recording_mode=backend_mode,
				ffmpeg_path=self._resolve_ffmpeg_path(),
				system_volume=readIntSetting(settings, "systemVolume", 100),
				microphone_volume=readIntSetting(settings, "microphoneVolume", 100),
				audio_processing=settings.get("audioProcessing", "default"),
				high_pass_filter=bool(settings.get("highPassFilter", False)),
				noise_gate=bool(settings.get("noiseGate", False)),
				microphone_auto_level=bool(settings.get("microphoneAutoLevel", True)),
				microphone_device_name=settings.get("microphoneDevice", ""),
				microphone_device_names=list(settings.get("microphoneDevices", []) or []),
				system_device_name=settings.get("systemDevice", ""),
				audio_bitrate_kbps=readIntSetting(settings, "mp3Quality", 192),
			)

			log.info(f"xTrack: Backend recorder initialized with mode: {backend_mode}")
			return True

		except RECORDING_ERRORS as e:
			log.error(f"xTrack: Failed to setup backend recorder: {e}")
			self._setupError = str(e)
			self.backend_recorder = None
			return False

	def _count_in(self):
		for freq, duration in self.COUNT_IN_BEEP_SEQUENCE:
			if self._stop_event.is_set():
				return
			wx.CallAfter(tones.beep, freq, duration)
			self._stop_event.wait(self.COUNT_IN_BEEP_GAP_SECONDS)

	def _capture_loop(self):
		"""Runs until stop() is requested; returns False if recording could not continue after a pause."""
		backend = self.backend_recorder
		while not self._stop_event.is_set():
			if self._pause_event.is_set():
				backend.pause_recording()
				while self._pause_event.is_set() and not self._stop_event.is_set():
					self._stop_event.wait(0.1)
				if self._stop_event.is_set():
					# Stopping while paused: the audio captured before the
					# pause was already flushed, so there is nothing to reopen.
					break
				try:
					backend.resume_recording()
				except RECORDING_ERRORS as resumeError:
					log.error(f"xTrack: Could not resume recording: {resumeError}")
					return False
			self._stop_event.wait(0.1)
		return True

	def _collect_output(self, returnedPaths):
		existingPaths = [p for p in returnedPaths if os.path.exists(p)]
		for missingPath in (p for p in returnedPaths if p not in existingPaths):
			log.error(f"xTrack: Expected recording output was not created: {missingPath}")
		self.output_files = existingPaths

	def _record_audio(self):
		failureMessage = ""
		try:
			if not self._setup_backend_recorder():
				log.error("xTrack: Failed to setup backend recorder")
				self.output_files = []
				failureMessage = _("Recording could not start: {}").format(self._setupError or _("unknown error"))
				return

			try:
				self.backend_recorder.prewarm_microphone()
			except RECORDING_ERRORS as prewarmError:
				log.error(f"xTrack: Microphone pre-warm failed: {prewarmError}")

			if self._settings.get("countIn", False):
				self._count_in()

			if self._stop_event.is_set():
				log.info("xTrack: Recording cancelled before capture started")
				try:
					self.backend_recorder.stop_recording()
				except RECORDING_ERRORS as cancelError:
					log.error(f"xTrack: Error while cancelling the recording: {cancelError}")
				self.output_files = []
				return

			self.backend_recorder.start_recording()
			log.info("xTrack: Backend recording started")

			resumedOk = self._capture_loop()
			if not resumedOk:
				failureMessage = _("Recording could not continue after the pause. What was recorded so far is being saved.")

			# Trust the backend's own record of what it wrote instead of
			# re-discovering it by scanning the destination folder: a folder
			# scan filtered by a fixed byte-size cutoff wrongly reported "no
			# file saved" for perfectly valid but short or quiet recordings.
			self._collect_output(self.backend_recorder.stop_recording() or [])
			self._mergeWarning = bool(getattr(self.backend_recorder, "merge_warning", ""))
			log.info(f"xTrack: Recording completed. Found files: {self.output_files}")

		except RECORDING_ERRORS as e:
			log.error(f"xTrack: Recording error: {e}")
			import traceback
			log.error(f"xTrack: Traceback: {traceback.format_exc()}")
			if not failureMessage:
				failureMessage = _("Recording error: {}").format(e)
			self._salvage_after_error()
		finally:
			self.backend_recorder = None
			self.is_recording = False
			self.is_paused = False
			stopRequested = self._stop_event.is_set()
			self._stop_event.clear()
			self._pause_event.clear()
			with self._stateLock:
				self._threadDone = True
				# When the recording ended on its own (for example it could not
				# continue after a pause) nobody is waiting on stop(), so the
				# saved files are announced from here.
				announceLate = self._announceLate or (not stopRequested and bool(self.output_files))
			if failureMessage:
				wx.CallAfter(ui.message, limitSpokenText(failureMessage))
			if announceLate:
				wx.CallAfter(self._announce_late_result)
			log.info("xTrack: Recording thread finished")

	def _salvage_after_error(self):
		"""After a failure, still release the devices and keep whatever audio was written."""
		backend = self.backend_recorder
		self.output_files = []
		if not backend:
			return
		try:
			returnedPaths = backend.stop_recording() or []
		except RECORDING_ERRORS as cleanupError:
			log.error(f"xTrack: Error while stopping backend recorder during cleanup: {cleanupError}")
			returnedPaths = list(getattr(backend, "output_paths", []))
		self._collect_output(returnedPaths)
		self._mergeWarning = bool(getattr(backend, "merge_warning", ""))

	def _find_existing_folder_window(self, folder_path):
		try:
			normalizedTarget = os.path.normcase(os.path.normpath(folder_path))
			shell = comtypes.client.CreateObject("Shell.Application")
			for window in shell.Windows():
				try:
					document = window.Document
					if document is None:
						continue
					windowPath = document.Folder.Self.Path
					if windowPath and os.path.normcase(os.path.normpath(windowPath)) == normalizedTarget:
						return int(window.hwnd)
				except (AttributeError, OSError, COMError):
					continue
		except (OSError, COMError) as shellError:
			log.error(f"xTrack: Failed to enumerate Explorer windows: {shellError}")
		return None

	def _bring_window_to_front(self, hwnd):
		try:
			user32 = ctypes.windll.user32
			user32.ShowWindow(hwnd, SW_RESTORE)
			# Windows ignores SetForegroundWindow from a process that does not
			# hold input focus; a harmless Alt tap satisfies its focus-stealing
			# check so the folder window really comes to the front.
			user32.keybd_event(VK_MENU, 0, 0, 0)
			user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
			user32.SetForegroundWindow(hwnd)
		except (OSError, AttributeError) as focusError:
			log.error(f"xTrack: Failed to bring existing folder window to front: {focusError}")

	def start(self):
		with self._stateLock:
			if self.is_recording:
				log.warning("xTrack: Already recording")
				return
			if self._thread and self._thread.is_alive():
				ui.message(_("Still saving the previous recording"))
				return
			self._stop_event.clear()
			self._pause_event.clear()
			self.output_files = []
			self._mergeWarning = False
			self._setupError = ""
			# Settings are read here, on the main thread, and handed to the
			# recording thread as plain values.
			self._settings = dict(config.conf["xTrack"]["record"].items())
			self._threadDone = False
			self._announceLate = False
			# Set before the thread starts: a thread that fails at once clears
			# this flag itself, and must not be overwritten afterwards.
			self.is_recording = True
			self._thread = threading.Thread(target=self._record_audio, daemon=True)
		self._thread.start()
		log.info("xTrack: Recording started")

	def _mute_system_audio_for_announcement(self):
		backend = self.backend_recorder
		if backend is None or not hasattr(backend, 'mute_system_audio'):
			return
		try:
			backend.mute_system_audio(1.5)
		except (AttributeError, TypeError, ValueError) as e:
			log.error(f"xTrack: Failed to mute system audio for announcement: {e}")

	def pause(self):
		if self.is_recording and not self.is_paused:
			self._mute_system_audio_for_announcement()
			self.is_paused = True
			self._pause_event.set()
			ui.message(_("paused"))
			log.info("xTrack: Recording paused")
		elif self.is_recording and self.is_paused:
			self.resume()

	def resume(self):
		if self.is_recording and self.is_paused:
			self._mute_system_audio_for_announcement()
			self.is_paused = False
			self._pause_event.clear()
			ui.message(_("continue"))
			log.info("xTrack: Recording continue")

	def _deliver_result(self):
		"""Open the folder if asked and return the saved files (or None). Main thread only."""
		files = list(self.output_files)
		if self._mergeWarning:
			ui.message(_("The recordings could not be merged. The separate recordings were kept."))
		if not files:
			log.warning("xTrack: Recording stopped but no files were saved")
			return None

		if self._settings.get("openFolderAfter", False):
			try:
				dest_dir = os.path.dirname(files[0])
				existingHwnd = self._find_existing_folder_window(dest_dir)
				if existingHwnd:
					self._bring_window_to_front(existingHwnd)
					log.info(f"xTrack: Existing folder window brought to front: {dest_dir}")
				else:
					try:
						os.startfile(dest_dir)
					except AttributeError:
						subprocess.Popen(['explorer', dest_dir])
					log.info(f"xTrack: Folder opened: {dest_dir}")
			except (OSError, COMError) as e:
				log.error(f"xTrack: Failed to open folder: {e}")

		log.info(f"xTrack: Recording stopped. Files saved: {files}")
		return files

	def _announce_late_result(self):
		files = self._deliver_result()
		if files:
			ui.message(limitSpokenText(_("Saved to: {}").format(os.path.dirname(files[0]))))
		else:
			ui.message(_("No file saved"))

	def stop(self):
		"""Stop recording. Returns the saved files, None if nothing was saved, or STOP_PENDING while the final file is still being written (the result is then announced when it is ready)."""
		if not self.is_recording:
			log.warning("xTrack: Not recording, cannot stop")
			return None

		log.info("xTrack: Stopping recording...")
		self._stop_event.set()

		thread = self._thread
		if thread and thread.is_alive():
			thread.join(timeout=STOP_WAIT_SECONDS)

		with self._stateLock:
			stillWriting = not self._threadDone
			if stillWriting:
				self._announceLate = True
		self.is_recording = False
		self.is_paused = False
		if stillWriting:
			log.info("xTrack: Recording thread is still saving the file; the result will be announced when ready")
			return STOP_PENDING
		return self._deliver_result()


recorder = Recorder()


class RecordSettingsDialog(wx.Dialog):
	def __init__(self, parent, onApply=None):
		super(RecordSettingsDialog, self).__init__(parent, title=_("Record Settings"))
		# Called with the current settings when the Apply button is pressed;
		# returns True when they were saved. None hides the Apply button.
		self.onApply = onApply
		
		raw = config.conf.get("xTrack", {}).get("record", {})
		self.settings = {}
		for k, v in raw.items():
			if isinstance(v, str):
				if v.lower() == "true":
					self.settings[k] = True
				elif v.lower() == "false":
					self.settings[k] = False
				else:
					self.settings[k] = v
			else:
				self.settings[k] = v
		
		# Settings that are missing (a first run, or a version that predates an
		# option) take the shared defaults.
		self.settings, _unused = normalize_record_settings(self.settings)

		self.makeSettings()
		self.modeCombo.SetFocus()

	def makeSettings(self):
		mainSizer = wx.BoxSizer(wx.VERTICAL)
		sHelper = gui.guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)

		modes = [
			("system_and_mic", _("System and Microphone (Merged)")),
			("system_and_mic_sep", _("System and Microphone (Separate Files)")),
			("system_only", _("System Only")),
			("mic_only", _("Microphone Only"))
		]
		self.modeChoices = [m[1] for m in modes]
		self.modeValues = [m[0] for m in modes]
		curr_mode = self.settings.get("recordingMode", "system_and_mic")
		self.modeCombo = sHelper.addLabeledControl(_("Recording &Mode:"), wx.Choice, choices=self.modeChoices)
		self.modeCombo.SetSelection(self.modeValues.index(curr_mode) if curr_mode in self.modeValues else 0)
		self.modeCombo.Bind(wx.EVT_CHOICE, self.onModeChange)

		availableMics = []
		availableSystemDevices = []
		if BACKEND_AVAILABLE and list_audio_devices is not None:
			try:
				deviceLists = list_audio_devices()
				availableMics = deviceLists.get("microphones", [])
				availableSystemDevices = deviceLists.get("systemDevices", [])
			except (OSError, RuntimeError, ValueError, KeyError, AttributeError) as deviceListError:
				log.error(f"xTrack: Failed to list audio devices for settings dialog: {deviceListError}")

		automaticLabel = _("Automatic (Default)")

		self.micDeviceChoices = availableMics
		# A check list lets more than one microphone be selected for
		# simultaneous recording (mixed together into a single microphone
		# track); leaving every item unchecked keeps the previous "Automatic"
		# single-default-microphone behavior.
		self.micDeviceCheckList = sHelper.addLabeledControl(
			_("&Microphone Device(s) (check none for Automatic; check more than one to record from multiple microphones at once):"),
			wx.CheckListBox, choices=self.micDeviceChoices
		)
		configuredMics = self.settings.get("microphoneDevices", [])
		if not configuredMics:
			legacyMic = self.settings.get("microphoneDevice", "")
			if legacyMic:
				configuredMics = [legacyMic]
		for index, deviceName in enumerate(self.micDeviceChoices):
			self.micDeviceCheckList.Check(index, deviceName in configuredMics)
		self.micDeviceCheckList.SetToolTip(_(
			"Check a specific microphone if it is not your system's default recording device, for example when the microphone is on a separate sound card. "
			"Check more than one to record from several microphones at the same time; they are mixed together into a single microphone track."
		))

		self.systemDeviceChoices = [automaticLabel] + availableSystemDevices
		self.systemDeviceCombo = sHelper.addLabeledControl(_("System Audio De&vice:"), wx.Choice, choices=self.systemDeviceChoices)
		currSystemDevice = self.settings.get("systemDevice", "")
		self.systemDeviceCombo.SetSelection(self.systemDeviceChoices.index(currSystemDevice) if currSystemDevice in self.systemDeviceChoices else 0)
		self.systemDeviceCombo.SetToolTip(_(
			"Choose which output device's audio to record, for example when speech and music play through different speakers than the system default."
		))

		self.formatCombo = sHelper.addLabeledControl(_("&Format:"), wx.Choice, choices=["mp3", "wav", "m4a"])
		curr_fmt = self.settings.get("format", "mp3")
		self.formatCombo.SetStringSelection(curr_fmt)
		self.formatCombo.Bind(wx.EVT_CHOICE, self.onFormatChange)

		self.qualityLabel = wx.StaticText(self, label=_("MP3 Quality (kbps):"))
		sHelper.addItem(self.qualityLabel)
		self.qualityCombo = wx.Choice(self, choices=["64", "96", "128", "160", "192", "256", "320"])
		sHelper.addItem(self.qualityCombo)
		
		curr_quality = str(self.settings.get("mp3Quality", "192"))
		if curr_quality in ["64", "96", "128", "160", "192", "256", "320"]:
			self.qualityCombo.SetStringSelection(curr_quality)
		else:
			self.qualityCombo.SetSelection(4)
		# Reflect the saved format right away (the quality list is hidden for WAV).
		self.onFormatChange(None)

		self.micVolumeEdit = self._addVolumeRow(sHelper, _("&Microphone Volume (%):"), _("Microphone Volume"), "microphoneVolume")
		self.sysVolumeEdit = self._addVolumeRow(sHelper, _("&System Volume (%):"), _("System Volume"), "systemVolume")

		audioProcessingOptions = [
			("default", _("Default")),
			("communications", _("Communications"))
		]
		self.audioProcessingChoices = [option[1] for option in audioProcessingOptions]
		self.audioProcessingValues = [option[0] for option in audioProcessingOptions]
		currAudioProcessing = self.settings.get("audioProcessing", "default")
		self.audioProcessingCombo = sHelper.addLabeledControl(_("Audio &Processing:"), wx.Choice, choices=self.audioProcessingChoices)
		self.audioProcessingCombo.SetSelection(
			self.audioProcessingValues.index(currAudioProcessing) if currAudioProcessing in self.audioProcessingValues else 0
		)
		self.audioProcessingCombo.SetToolTip(_(
			"Communications requests Windows voice-processing features such as noise suppression, "
			"echo cancellation, and automatic gain control when supported by the operating system and audio driver."
		))
		self.audioProcessingCombo.Bind(wx.EVT_CHOICE, self.onAudioProcessingChange)

		self.highPassCheck = sHelper.addItem(wx.CheckBox(self, label=_("Reduce low-frequency noise (&High-pass filter)")))
		self.highPassCheck.SetValue(bool(self.settings.get("highPassFilter", False)))
		self.highPassCheck.SetToolTip(_(
			"Removes low-frequency rumble, such as fan or handling noise, from the microphone. Only applies when Audio Processing is set to Default."
		))

		self.noiseGateCheck = sHelper.addItem(wx.CheckBox(self, label=_("Reduce background &noise (Noise gate)")))
		self.noiseGateCheck.SetValue(bool(self.settings.get("noiseGate", False)))
		self.noiseGateCheck.SetToolTip(_(
			"Lowers steady background noise picked up between spoken words. Only applies when Audio Processing is set to Default."
		))

		self.autoLevelCheck = sHelper.addItem(wx.CheckBox(self, label=_("Au&tomatic microphone level (for quiet microphones)")))
		self.autoLevelCheck.SetValue(bool(self.settings.get("microphoneAutoLevel", True)))
		self.autoLevelCheck.SetToolTip(_(
			"Raises the volume of a quiet or inexpensive microphone while you speak, so the recording is clear and loud enough. "
			"A microphone that is already loud is left unchanged. Only applies when Audio Processing is set to Default."
		))

		self._updateModeSubOptions()

		self.countInCheck = sHelper.addItem(wx.CheckBox(self, label=_("&Count-in before recording")))
		self.countInCheck.SetValue(bool(self.settings.get("countIn", False)))
		
		self.openFolderCheck = sHelper.addItem(wx.CheckBox(self, label=_("&Open folder after recording")))
		self.openFolderCheck.SetValue(bool(self.settings.get("openFolderAfter", False)))

		destLabel = wx.StaticText(self, label=_("&Destination Folder:"))
		sHelper.addItem(destLabel)
		dest_sizer = wx.BoxSizer(wx.HORIZONTAL)
		self.destEdit = wx.TextCtrl(self, value=self.settings.get("destinationFolder", ""))
		dest_sizer.Add(self.destEdit, 1, wx.EXPAND | wx.RIGHT, 5)
		browseBtn = wx.Button(self, label=_("&Browse..."))
		browseBtn.Bind(wx.EVT_BUTTON, self.onBrowse)
		dest_sizer.Add(browseBtn)
		sHelper.sizer.Add(dest_sizer, 0, wx.EXPAND | wx.ALL, 5)

		status_text = _("Audio Backend Status: ")
		if BACKEND_AVAILABLE:
			status_text += _("Available")
		else:
			status_text += _("Not available - check libs/recorder_backend.py")
		
		status_label = wx.StaticText(self, label=status_text)
		status_label.Wrap(400)
		sHelper.addItem(status_label)

		btnSizer = wx.StdDialogButtonSizer()
		okButton = wx.Button(self, wx.ID_OK)
		okButton.SetDefault()
		btnSizer.AddButton(okButton)
		btnSizer.AddButton(wx.Button(self, wx.ID_CANCEL))
		if self.onApply:
			# Applies the settings without closing the dialog.
			btnSizer.AddButton(wx.Button(self, wx.ID_APPLY, label=_("Appl&y")))
		btnSizer.Realize()
		mainSizer.Add(sHelper.sizer, 1, wx.ALL | wx.EXPAND, 10)
		mainSizer.Add(btnSizer, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
		self.SetSizer(mainSizer)
		mainSizer.Fit(self)
		self.Bind(wx.EVT_BUTTON, self.onOk, id=wx.ID_OK)
		self.Bind(wx.EVT_BUTTON, self.onApplyClicked, id=wx.ID_APPLY)

	def _addVolumeRow(self, sHelper, label, accessibleName, settingKey):
		# guiHelper.addLabeledControl does not accept wx.SpinCtrlDouble, and wx.SpinCtrl cannot
		# change its step, so the row is built by hand: whole numbers, 5% per arrow press.
		row = wx.BoxSizer(wx.HORIZONTAL)
		row.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
		spin = wx.SpinCtrlDouble(self, min=0, max=300, initial=readIntSetting(self.settings, settingKey, 100), inc=5)
		spin.SetDigits(0)
		spin.SetName(accessibleName)
		row.Add(spin, 0)
		sHelper.addItem(row)
		return spin

	def onFormatChange(self, event):
		fmt = self.formatCombo.GetStringSelection()
		if fmt == "wav":
			self.qualityLabel.SetLabel(_("Note: WAV format uses uncompressed audio"))
			self.qualityCombo.Hide()
		else:
			if fmt == "mp3":
				self.qualityLabel.SetLabel(_("MP3 Quality (kbps):"))
			else:
				self.qualityLabel.SetLabel(_("AAC Bitrate (kbps):"))
			self.qualityCombo.Show()
		self.Layout()

	def onAudioProcessingChange(self, event):
		self._updateModeSubOptions()
		event.Skip()

	def onModeChange(self, event):
		self._updateModeSubOptions()
		event.Skip()

	def _updateModeSubOptions(self):
		selectedMode = self.modeValues[self.modeCombo.GetSelection()]
		# Microphone-related settings have no effect in System Only mode: that
		# mode never opens a microphone stream at all, so leaving Audio
		# Processing, microphone device selection, volume, or noise controls
		# enabled falsely suggests they change system-only behavior (this was
		# reported as actively misleading for Audio Processing specifically).
		micRelevant = selectedMode != "system_only"
		self.micDeviceCheckList.Enable(micRelevant)
		self.micVolumeEdit.Enable(micRelevant)
		self.audioProcessingCombo.Enable(micRelevant)
		isCommunications = self.audioProcessingValues[self.audioProcessingCombo.GetSelection()] == "communications"
		self.highPassCheck.Enable(micRelevant and not isCommunications)
		self.noiseGateCheck.Enable(micRelevant and not isCommunications)
		self.autoLevelCheck.Enable(micRelevant and not isCommunications)

		# Symmetrically, system-audio settings have no effect in Microphone
		# Only mode, since that mode never opens a system-audio loopback
		# stream.
		systemRelevant = selectedMode != "mic_only"
		self.systemDeviceCombo.Enable(systemRelevant)
		self.sysVolumeEdit.Enable(systemRelevant)

		self.Layout()

	def onBrowse(self, event):
		dlg = wx.DirDialog(self, _("Select Destination Folder"), self.destEdit.GetValue())
		if dlg.ShowModal() == wx.ID_OK:
			self.destEdit.SetValue(dlg.GetPath())
		dlg.Destroy()

	def onOk(self, event):
		if not self.collectSettings():
			return
		event.Skip()

	def onApplyClicked(self, event):
		if not self.collectSettings():
			return
		if self.onApply(self.settings):
			ui.message(_("Record settings applied"))
		else:
			ui.message(_("Record settings could not be saved"))

	def collectSettings(self):
		"""Validate the controls and copy them into self.settings; False (after speaking why) when something is wrong."""
		fmt = self.formatCombo.GetStringSelection()

		destination = self.destEdit.GetValue().strip()
		if not destination or not os.path.isabs(destination):
			ui.message(_("Please enter the full path of the destination folder."))
			self.destEdit.SetFocus()
			return False
		try:
			os.makedirs(destination, exist_ok=True)
		except OSError as folderError:
			log.error(f"xTrack: could not create the destination folder {destination}: {folderError}")
			ui.message(_("The destination folder could not be created."))
			self.destEdit.SetFocus()
			return False

		qualityText = self.qualityCombo.GetStringSelection()
		bitrate = int(qualityText) if fmt != "wav" and qualityText.isdigit() else 192
		checkedMics = [self.micDeviceChoices[i] for i in range(len(self.micDeviceChoices)) if self.micDeviceCheckList.IsChecked(i)]
		self.settings.update({
			"recordingMode": self.modeValues[self.modeCombo.GetSelection()],
			"format": fmt,
			"mp3Quality": bitrate,
			"countIn": self.countInCheck.GetValue(),
			"openFolderAfter": self.openFolderCheck.GetValue(),
			"destinationFolder": destination,
			"systemVolume": int(round(self.sysVolumeEdit.GetValue())),
			"microphoneVolume": int(round(self.micVolumeEdit.GetValue())),
			"audioProcessing": self.audioProcessingValues[self.audioProcessingCombo.GetSelection()],
			"highPassFilter": self.highPassCheck.GetValue(),
			"noiseGate": self.noiseGateCheck.GetValue(),
			"microphoneAutoLevel": self.autoLevelCheck.GetValue(),
			"microphoneDevices": checkedMics,
			"microphoneDevice": checkedMics[0] if len(checkedMics) == 1 else "",
			"systemDevice": self.systemDeviceCombo.GetStringSelection() if self.systemDeviceCombo.GetSelection() > 0 else ""
		})
		return True
