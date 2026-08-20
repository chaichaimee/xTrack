# record.py

import os
import time
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

addonHandler.initTranslation()

BACKEND_AVAILABLE = False
WasapiSoundRecorder = None
list_audio_devices = None

try:
	from recorder_backend import WasapiSoundRecorder, list_audio_devices
	BACKEND_AVAILABLE = True
	log.info("xTrack: WasapiSoundRecorder backend loaded successfully")
except ImportError as e:
	log.error(f"xTrack: Failed to import WasapiSoundRecorder: {e}")

class Recorder:
	def __init__(self):
		self.is_recording = False
		self.is_paused = False
		self.output_files = []
		self._thread = None
		self._stop_event = threading.Event()
		self._pause_event = threading.Event()
		
		self.addon_dir = os.path.dirname(__file__)
		self.libs_path = os.path.join(self.addon_dir, "libs")
		self.ffmpeg_exe = os.path.join(self.libs_path, "ffmpeg.exe")
		
		self.backend_recorder = None
		
		self.recording_mode_map = {
			"system_and_mic": "system audio and microphone",
			"system_and_mic_sep": "system audio and microphone (separate files)",
			"system_only": "system audio only",
			"mic_only": "microphone only"
		}

	COUNT_IN_BEEP_SEQUENCE = [(440, 200), (440, 200), (440, 200), (880, 200)]
	COUNT_IN_BEEP_GAP_SECONDS = 0.5

	def _setup_backend_recorder(self):
		global BACKEND_AVAILABLE, WasapiSoundRecorder, list_audio_devices
		
		if not BACKEND_AVAILABLE:
			try:
				from recorder_backend import WasapiSoundRecorder, list_audio_devices
				BACKEND_AVAILABLE = True
				log.info("xTrack: WasapiSoundRecorder loaded on demand")
			except ImportError as e:
				log.error(f"xTrack: Backend recorder not available: {e}")
				return False
		
		try:
			conf = config.conf["xTrack"]["record"]
			mode = conf.get("recordingMode", "system_and_mic")
			format_map = {"mp3": "mp3", "wav": "wav", "m4a": "m4a"}
			fmt = format_map.get(conf.get("format", "mp3"), "mp3")
			
			backend_mode = self.recording_mode_map.get(mode, "system audio and microphone")
			dest = conf.get("destinationFolder", os.path.expanduser("~/xTrack_recordings"))
			
			self.backend_recorder = WasapiSoundRecorder(
				recording_format=fmt,
				recording_folder=dest,
				recording_mode=backend_mode,
				ffmpeg_path=self.ffmpeg_exe,
				system_volume=int(conf.get("systemVolume", 100)),
				microphone_volume=int(conf.get("microphoneVolume", 100)),
				audio_processing=conf.get("audioProcessing", "default"),
				high_pass_filter=bool(conf.get("highPassFilter", False)),
				noise_gate=bool(conf.get("noiseGate", False)),
				microphone_device_name=conf.get("microphoneDevice", ""),
				system_device_name=conf.get("systemDevice", ""),
				auto_ducking=bool(conf.get("autoDucking", False)) and mode == "system_and_mic",
				ducking_level_db=int(conf.get("duckingLevel", -18)),
				ducking_threshold_db=int(conf.get("duckingThreshold", -30)),
				ducking_attack_ms=int(conf.get("duckingAttack", 5)),
				ducking_release_ms=int(conf.get("duckingRelease", 250))
			)
			
			log.info(f"xTrack: Backend recorder initialized with mode: {backend_mode}")
			return True
			
		except Exception as e:
			log.error(f"xTrack: Failed to setup backend recorder: {e}")
			return False

	def _record_audio(self):
		try:
			conf = config.conf["xTrack"]["record"]
			mode = conf.get("recordingMode", "system_and_mic")
			
			if not self._setup_backend_recorder():
				log.error("xTrack: Failed to setup backend recorder")
				self.output_files = []
				return
			
			try:
				self.backend_recorder.prewarm_microphone()
			except Exception as prewarmError:
				log.error(f"xTrack: Microphone pre-warm failed: {prewarmError}")
			
			if conf.get("countIn", False):
				for freq, duration in self.COUNT_IN_BEEP_SEQUENCE:
					if self._stop_event.is_set():
						break
					tones.beep(freq, duration)
					time.sleep(self.COUNT_IN_BEEP_GAP_SECONDS)
			
			if self._stop_event.is_set():
				log.info("xTrack: Recording cancelled before capture started")
				try:
					self.backend_recorder.stop_recording()
				except Exception:
					pass
				self.output_files = []
				return
			
			self.backend_recorder.start_recording()
			log.info("xTrack: Backend recording started")
			
			while not self._stop_event.is_set():
				if self._pause_event.is_set():
					if hasattr(self.backend_recorder, 'pause_recording'):
						self.backend_recorder.pause_recording()
					
					while self._pause_event.is_set() and not self._stop_event.is_set():
						time.sleep(0.1)
					
					if hasattr(self.backend_recorder, 'resume_recording'):
						self.backend_recorder.resume_recording()
				
				time.sleep(0.1)
			
			if hasattr(self.backend_recorder, 'stop_recording'):
				self.backend_recorder.stop_recording()
			
			dest = conf.get("destinationFolder", os.path.expanduser("~/xTrack_recordings"))
			
			if os.path.exists(dest):
				files = []
				for f in os.listdir(dest):
					if f.lower().endswith(('.mp3', '.wav', '.m4a', '.flac')):
						filepath = os.path.join(dest, f)
						if os.path.getsize(filepath) > 1024:
							files.append(filepath)
				
				files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
				
				if mode == "system_and_mic_sep":
					system_files = [f for f in files if 'system' in f.lower()]
					mic_files = [f for f in files if 'mic' in f.lower()]
					self.output_files = system_files[:1] + mic_files[:1]
				else:
					recording_files = [f for f in files if 'recording' in f.lower()]
					self.output_files = recording_files[:1]
			
			log.info(f"xTrack: Recording completed. Found files: {self.output_files}")
			
		except Exception as e:
			log.error(f"xTrack: Recording error: {str(e)}")
			import traceback
			log.error(f"xTrack: Traceback: {traceback.format_exc()}")
			self.output_files = []
			
			try:
				if self.backend_recorder and hasattr(self.backend_recorder, 'stop_recording'):
					self.backend_recorder.stop_recording()
			except:
				pass
		finally:
			self.is_recording = False
			self.is_paused = False
			self._stop_event.clear()
			self._pause_event.clear()
			self.backend_recorder = None
			log.info("xTrack: Recording thread finished")

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
		SW_RESTORE = 9
		try:
			ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
			ctypes.windll.user32.SetForegroundWindow(hwnd)
		except (OSError, AttributeError) as focusError:
			log.error(f"xTrack: Failed to bring existing folder window to front: {focusError}")

	def start(self):
		if self.is_recording:
			log.warning("xTrack: Already recording")
			return
		
		self._stop_event.clear()
		self._pause_event.clear()
		self.output_files = []
		
		self._thread = threading.Thread(target=self._record_audio, daemon=True)
		self._thread.start()
		
		self.is_recording = True
		log.info("xTrack: Recording started")

	def _mute_system_audio_for_announcement(self):
		if self.backend_recorder and hasattr(self.backend_recorder, 'mute_system_audio'):
			try:
				self.backend_recorder.mute_system_audio(1.5)
			except Exception as e:
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

	def stop(self):
		if not self.is_recording:
			log.warning("xTrack: Not recording, cannot stop")
			return None
		
		log.info("xTrack: Stopping recording...")
		
		self._stop_event.set()
		
		if self._thread and self._thread.is_alive():
			self._thread.join(timeout=5)
			if self._thread.is_alive():
				log.warning("xTrack: Recording thread did not finish in time")
		
		self.is_recording = False
		self.is_paused = False
		
		conf = config.conf["xTrack"]["record"]
		if conf.get("openFolderAfter", False) and self.output_files:
			try:
				dest_dir = os.path.dirname(self.output_files[0])
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
			except Exception as e:
				log.error(f"xTrack: Failed to open folder: {e}")
		
		if self.output_files:
			log.info(f"xTrack: Recording stopped. Files saved: {self.output_files}")
			return self.output_files
		else:
			log.warning("xTrack: Recording stopped but no files were saved")
			return None

recorder = Recorder()

class RecordSettingsDialog(wx.Dialog):
	def __init__(self, parent):
		super(RecordSettingsDialog, self).__init__(parent, title=_("Record Settings"))
		
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
		
		defaults = {
			"recordingMode": "system_and_mic",
			"format": "mp3",
			"mp3Quality": 192,
			"countIn": False,
			"openFolderAfter": False,
			"systemVolume": 100,
			"microphoneVolume": 100,
			"audioProcessing": "default",
			"highPassFilter": False,
			"noiseGate": False,
			"microphoneDevice": "",
			"systemDevice": "",
			"autoDucking": False,
			"duckingLevel": -18,
			"duckingThreshold": -30,
			"duckingAttack": 5,
			"duckingRelease": 250,
			"destinationFolder": os.path.expanduser("~/xTrack_recordings")
		}
		
		for key, value in defaults.items():
			if key not in self.settings:
				self.settings[key] = value
		
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

		self.duckingCheck = sHelper.addItem(wx.CheckBox(self, label=_("Enable Auto-&Ducking (lower music while you speak)")))
		self.duckingCheck.SetValue(bool(self.settings.get("autoDucking", False)))
		self.duckingCheck.SetToolTip(_(
			"Automatically lowers the system audio whenever the microphone picks up speech, then raises it back once you stop talking. Only applies when Recording Mode is System and Microphone (Merged)."
		))
		self.duckingCheck.Bind(wx.EVT_CHECKBOX, self.onDuckingCheckboxChange)

		self.duckingLevelSpin = sHelper.addLabeledControl(_("Ducking &Level (dB):"), wx.SpinCtrl, min=-30, max=-6, initial=int(self.settings.get("duckingLevel", -18)))
		self.duckingLevelSpin.SetToolTip(_("How much the music is lowered by while you are speaking."))
		self.duckingThresholdSpin = sHelper.addLabeledControl(_("Ducking &Sensitivity Threshold (dB):"), wx.SpinCtrl, min=-60, max=-10, initial=int(self.settings.get("duckingThreshold", -30)))
		self.duckingThresholdSpin.SetToolTip(_("The microphone loudness, in dB, that starts ducking the music. Lower values duck more easily."))
		self.duckingAttackSpin = sHelper.addLabeledControl(_("Ducking &Attack Time (ms):"), wx.SpinCtrl, min=1, max=500, initial=int(self.settings.get("duckingAttack", 5)))
		self.duckingAttackSpin.SetToolTip(_("How quickly the music fades down once speech is detected."))
		self.duckingReleaseSpin = sHelper.addLabeledControl(_("Ducking &Release Time (ms):"), wx.SpinCtrl, min=50, max=2000, initial=int(self.settings.get("duckingRelease", 250)))
		self.duckingReleaseSpin.SetToolTip(_("How quickly the music fades back up once you stop speaking."))

		availableMics = []
		availableSystemDevices = []
		if BACKEND_AVAILABLE and list_audio_devices is not None:
			try:
				deviceLists = list_audio_devices()
				availableMics = deviceLists.get("microphones", [])
				availableSystemDevices = deviceLists.get("systemDevices", [])
			except Exception as deviceListError:
				log.error(f"xTrack: Failed to list audio devices for settings dialog: {deviceListError}")

		automaticLabel = _("Automatic (Default)")
		self.micDeviceChoices = [automaticLabel] + availableMics
		self.micDeviceCombo = sHelper.addLabeledControl(_("Microphone &Device:"), wx.Choice, choices=self.micDeviceChoices)
		currMicDevice = self.settings.get("microphoneDevice", "")
		self.micDeviceCombo.SetSelection(self.micDeviceChoices.index(currMicDevice) if currMicDevice in self.micDeviceChoices else 0)
		self.micDeviceCombo.SetToolTip(_(
			"Choose a specific microphone if it is not on your system's default recording device, for example when the microphone is on a separate sound card."
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

		self.micVolumeEdit = sHelper.addLabeledControl(_("&Microphone Volume (%):"), wx.SpinCtrl, min=0, max=300, initial=int(self.settings.get("microphoneVolume", 100)))
		self.sysVolumeEdit = sHelper.addLabeledControl(_("&System Volume (%):"), wx.SpinCtrl, min=0, max=300, initial=int(self.settings.get("systemVolume", 100)))

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

		self._updateAudioProcessingSubOptions()
		self._updateDuckingSubOptions()

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

		btnSizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)
		mainSizer.Add(sHelper.sizer, 1, wx.ALL | wx.EXPAND, 10)
		mainSizer.Add(btnSizer, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
		self.SetSizer(mainSizer)
		mainSizer.Fit(self)
		self.Bind(wx.EVT_BUTTON, self.onOk, id=wx.ID_OK)

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
		self._updateAudioProcessingSubOptions()
		event.Skip()

	def _updateAudioProcessingSubOptions(self):
		selectedValue = self.audioProcessingValues[self.audioProcessingCombo.GetSelection()]
		isCommunications = selectedValue == "communications"
		self.highPassCheck.Show(not isCommunications)
		self.noiseGateCheck.Show(not isCommunications)
		self.Layout()

	def onModeChange(self, event):
		self._updateDuckingSubOptions()
		event.Skip()

	def onDuckingCheckboxChange(self, event):
		self._updateDuckingSubOptions()
		event.Skip()

	def _updateDuckingSubOptions(self):
		selectedMode = self.modeValues[self.modeCombo.GetSelection()]
		isMergedMode = selectedMode == "system_and_mic"
		self.duckingCheck.Enable(isMergedMode)
		if not isMergedMode:
			self.duckingCheck.SetValue(False)
		duckingControlsEnabled = isMergedMode and self.duckingCheck.GetValue()
		self.duckingLevelSpin.Enable(duckingControlsEnabled)
		self.duckingThresholdSpin.Enable(duckingControlsEnabled)
		self.duckingAttackSpin.Enable(duckingControlsEnabled)
		self.duckingReleaseSpin.Enable(duckingControlsEnabled)
		self.Layout()

	def onBrowse(self, event):
		dlg = wx.DirDialog(self, _("Select Destination Folder"), self.destEdit.GetValue())
		if dlg.ShowModal() == wx.ID_OK:
			self.destEdit.SetValue(dlg.GetPath())
		dlg.Destroy()

	def onOk(self, event):
		fmt = self.formatCombo.GetStringSelection()
		
		self.settings.update({
			"recordingMode": self.modeValues[self.modeCombo.GetSelection()],
			"format": fmt,
			"mp3Quality": int(self.qualityCombo.GetStringSelection()) if fmt == "mp3" else 192,
			"countIn": self.countInCheck.GetValue(),
			"openFolderAfter": self.openFolderCheck.GetValue(),
			"destinationFolder": self.destEdit.GetValue(),
			"systemVolume": self.sysVolumeEdit.GetValue(),
			"microphoneVolume": self.micVolumeEdit.GetValue(),
			"audioProcessing": self.audioProcessingValues[self.audioProcessingCombo.GetSelection()],
			"highPassFilter": self.highPassCheck.GetValue(),
			"noiseGate": self.noiseGateCheck.GetValue(),
			"microphoneDevice": self.micDeviceCombo.GetStringSelection() if self.micDeviceCombo.GetSelection() > 0 else "",
			"systemDevice": self.systemDeviceCombo.GetStringSelection() if self.systemDeviceCombo.GetSelection() > 0 else "",
			"autoDucking": self.duckingCheck.GetValue(),
			"duckingLevel": self.duckingLevelSpin.GetValue(),
			"duckingThreshold": self.duckingThresholdSpin.GetValue(),
			"duckingAttack": self.duckingAttackSpin.GetValue(),
			"duckingRelease": self.duckingReleaseSpin.GetValue()
		})
		event.Skip()




