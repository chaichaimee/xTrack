# record.py

import os
import time
import threading
import subprocess
import wx
import gui
import config
from logHandler import log
import ui
import addonHandler
import tones
import sys
import shutil
import tempfile
import psutil

addonHandler.initTranslation()

# Import the working recorder backend
try:
    from recorder_backend import WasapiSoundRecorder
    BACKEND_AVAILABLE = True
    log.info("xTrack: WasapiSoundRecorder backend loaded successfully")
except ImportError as e:
    log.error(f"xTrack: Failed to import WasapiSoundRecorder: {e}")
    BACKEND_AVAILABLE = False

class Recorder:
    def __init__(self):
        self.is_recording = False
        self.is_paused = False
        self.output_files = []
        self._thread = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        
        # Get addon directory
        self.addon_dir = os.path.dirname(__file__)
        self.tools_path = os.path.join(self.addon_dir, "tools")   # เปลี่ยน Tools → tools
        self.ffmpeg_exe = os.path.join(self.tools_path, "ffmpeg.exe")
        
        # Backend recorder
        self.backend_recorder = None
        
        # Recording parameters
        self.recording_mode_map = {
            "system_and_mic": "system audio and microphone",
            "system_and_mic_sep": "system audio and microphone (separate files)",
            "system_only": "system audio only",
            "mic_only": "microphone only"
        }

    def _play_count_in(self):
        """Generate beep tones using system tones with shorter final beep and extra delay."""
        for _ in range(3):
            tones.beep(440, 200)
            time.sleep(0.5)
        tones.beep(880, 200)
        time.sleep(0.3)

    def _setup_backend_recorder(self):
        """Setup the backend recorder with current settings."""
        if not BACKEND_AVAILABLE:
            log.error("xTrack: Backend recorder not available")
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
                system_gain=int(conf.get("systemGain", 0)),
                microphone_gain=int(conf.get("microphoneGain", 0))
            )
            
            log.info(f"xTrack: Backend recorder initialized with mode: {backend_mode}")
            return True
            
        except Exception as e:
            log.error(f"xTrack: Failed to setup backend recorder: {e}")
            return False

    def _record_audio(self):
        """Main recording function using backend."""
        try:
            conf = config.conf["xTrack"]["record"]
            mode = conf.get("recordingMode", "system_and_mic")
            
            if not self._setup_backend_recorder():
                log.error("xTrack: Failed to setup backend recorder")
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
                        files.append(os.path.join(dest, f))
                
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

    def is_folder_already_open(self, folder_path):
        """Check if the folder is already open in File Explorer."""
        try:
            folder_path = os.path.normpath(folder_path)
            
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if proc.info['name'] and proc.info['name'].lower() == 'explorer.exe':
                        cmdline = proc.info['cmdline']
                        if cmdline:
                            for arg in cmdline:
                                if arg and folder_path in arg:
                                    return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return False
        except Exception as e:
            log.error(f"Error checking if folder is open: {e}")
            return False

    def start(self):
        """Start recording."""
        if self.is_recording:
            log.warning("xTrack: Already recording")
            return
        
        conf = config.conf["xTrack"]["record"]
        if conf.get("countIn", False):
            self._play_count_in()
        
        self._stop_event.clear()
        self._pause_event.clear()
        
        self._thread = threading.Thread(target=self._record_audio)
        self._thread.daemon = True
        self._thread.start()
        
        self.is_recording = True
        log.info("xTrack: Recording started")

    def pause(self):
        """Pause recording."""
        if self.is_recording and not self.is_paused:
            self.is_paused = True
            self._pause_event.set()
            log.info("xTrack: Recording paused")
        elif self.is_recording and self.is_paused:
            self.resume()

    def resume(self):
        """Resume recording."""
        if self.is_recording and self.is_paused:
            self.is_paused = False
            self._pause_event.clear()
            log.info("xTrack: Recording resumed")

    def stop(self):
        """Stop recording and return saved files."""
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
                log.info(f"xTrack: Opening folder: {dest_dir}")
                
                if not self.is_folder_already_open(dest_dir):
                    subprocess.Popen(f'explorer "{dest_dir}"', shell=True)
                    log.info(f"xTrack: Folder opened: {dest_dir}")
                else:
                    log.info(f"xTrack: Folder already open: {dest_dir}")
            except Exception as e:
                log.error(f"xTrack: Failed to open folder: {e}")
        
        if self.output_files:
            log.info(f"xTrack: Recording stopped. Files saved: {self.output_files}")
            return self.output_files
        else:
            log.warning("xTrack: Recording stopped but no files were saved")
            return None

# Global recorder instance
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
            "systemGain": 0,
            "microphoneGain": 0,
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

        self.micGainEdit = sHelper.addLabeledControl(_("&Microphone Gain (0-10):"), wx.SpinCtrl, min=0, max=10, initial=int(self.settings.get("microphoneGain", 0)))
        self.sysGainEdit = sHelper.addLabeledControl(_("&System Gain (0-10):"), wx.SpinCtrl, min=0, max=10, initial=int(self.settings.get("systemGain", 0)))

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
            status_text += _("WasapiSoundRecorder (system audio available)")
        else:
            status_text += _("WasapiSoundRecorder (check tools/recorder_backend.py)")
        
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
            else:  # m4a
                self.qualityLabel.SetLabel(_("AAC Bitrate (kbps):"))
            self.qualityCombo.Show()
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
            "systemGain": self.sysGainEdit.GetValue(),
            "microphoneGain": self.micGainEdit.GetValue()
        })
        event.Skip()