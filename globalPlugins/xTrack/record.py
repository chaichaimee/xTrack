# record.py

import os
import time
import threading
import wx
import gui
import tones
import config
import json
import ui
import addonHandler
import subprocess
import tempfile
from logHandler import log
try:
    addonHandler.initTranslation()
except addonHandler.AddonError:
    _ = lambda x: x
# Import from Tools directory
import sys
addon_dir = os.path.dirname(__file__)
tools_dir = os.path.join(addon_dir, "Tools")
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)
try:
    from recorder_backend import WasapiSoundRecorder
    log.info("Successfully imported WasapiSoundRecorder from recorder_backend")
except ImportError as e:
    log.error(f"Failed to import WasapiSoundRecorder from recorder_backend: {e}")
    WasapiSoundRecorder = None

class NoiseReductionProgressDialog(wx.Dialog):
    """Dialog for showing noise reduction progress."""
    def __init__(self, parent, total_files):
        super().__init__(parent, title=_("Noise Reduction Progress"))
        self.total_files = total_files
        self.current_file = 0
        self.init_ui()
        self.Bind(wx.EVT_CLOSE, self.on_close)
    def init_ui(self):
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.status_label = wx.StaticText(self, label=_("Preparing noise reduction..."))
        main_sizer.Add(self.status_label, 0, wx.EXPAND | wx.ALL, 10)
        self.progress_bar = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
        main_sizer.Add(self.progress_bar, 0, wx.EXPAND | wx.ALL, 10)
        self.file_label = wx.StaticText(self, label="")
        main_sizer.Add(self.file_label, 0, wx.EXPAND | wx.ALL, 5)
        self.SetSizer(main_sizer)
        self.Fit()
        self.CenterOnParent()
    def update_progress(self, current, total, filename):
        """Update progress for current file."""
        self.current_file = current
        progress = int((current / total) * 100) if total > 0 else 0
        self.progress_bar.SetValue(progress)
        self.file_label.SetLabel(_("Processing: {} ({} of {})").format(
            os.path.basename(filename), current, total))
        self.status_label.SetLabel(_("Applying noise reduction..."))
        self.Refresh()
    def on_close(self, event):
        event.Veto()

class Recorder:
    def __init__(self):
        self.is_recording = False
        self.is_paused = False
        self.recorder = None
        self.count_in_thread = None
        self.recorded_files = []
    def start(self):
        if self.is_recording and not self.is_paused:
            return False
        if "xTrack" not in config.conf or "record" not in config.conf["xTrack"]:
            ui.message(_("Please configure record settings first"))
            return False
        
        record_config = config.conf["xTrack"]["record"]
        
        # Convert configuration values to correct types
        try:
            mode = str(record_config.get("recordingMode", "system_and_mic"))
            fmt = str(record_config.get("format", "mp3"))
            dest_folder = str(record_config.get("destinationFolder") or os.path.expanduser("~/xTrack_recordings"))
            
            # Convert gain values to integer
            system_gain = int(record_config.get("systemGain", 0))
            microphone_gain = int(record_config.get("microphoneGain", 0))
            
        except (ValueError, TypeError) as e:
            log.error(f"Config value conversion error: {e}")
            ui.message(_("Configuration error: invalid values"))
            return False
        
        mode_map = {
            "system_and_mic": "system audio and microphone",
            "system_only": "system audio only", 
            "mic_only": "microphone only",
            "system_and_mic_separate": "system audio and microphone (separate files)"
        }
        recording_mode = mode_map.get(mode, "system audio and microphone")
        
        os.makedirs(dest_folder, exist_ok=True)
        
        try:
            if WasapiSoundRecorder is None:
                ui.message(_("Recording backend not available"))
                return False
                
            log.info(f"Creating WasapiSoundRecorder with mode={recording_mode}, format={fmt}, folder={dest_folder}, system_gain={system_gain}, mic_gain={microphone_gain}")
            
            self.recorder = WasapiSoundRecorder(
                recording_format=fmt,
                recording_folder=dest_folder,
                recording_mode=recording_mode,
                ffmpeg_path="",
                system_gain=system_gain,
                microphone_gain=microphone_gain
            )
            
            if record_config.get("countIn", False):
                self._do_count_in_with_delay()
            else:
                self._start_recording_immediately()
                ui.message(_("Recording"))
            return True
            
        except Exception as e:
            log.error(f"Failed to start recording: {e}")
            ui.message(_("Failed to start recording"))
            return False
    def _start_recording_immediately(self):
        """Start recording immediately without count-in"""
        try:
            self.recorder.start_recording()
            self.is_recording = True
            self.is_paused = False
            log.info("Recording started immediately")
        except Exception as e:
            log.error(f"Failed to start recording immediately: {e}")
            raise
    def _do_count_in_with_delay(self):
        """Do count-in with delayed recording start - no UI messages"""
        def count_in_and_start():
            for i in range(1, 3):
                try:
                    tones.beep(800, 200)
                except Exception:
                    pass
                time.sleep(0.6)
            try:
                tones.beep(1000, 500)
            except Exception:
                pass
            time.sleep(0.3)
            wx.CallAfter(self._start_recording_after_count_in)
        self.count_in_thread = threading.Thread(target=count_in_and_start, daemon=True)
        self.count_in_thread.start()
    def _start_recording_after_count_in(self):
        """Start recording after count-in completes"""
        try:
            self.recorder.start_recording()
            self.is_recording = True
            self.is_paused = False
            log.info("Recording started after count-in")
        except Exception as e:
            log.error(f"Failed to start recording after count-in: {e}")
            ui.message(_("Failed to start recording"))
    def pause(self):
        if not self.is_recording or self.is_paused:
            return False
        self.is_paused = True
        if self.recorder:
            self.recorder.pause_recording()
        ui.message(_("Pause"))
        return True
    def resume(self):
        if not self.is_recording or not self.is_paused:
            return False
        self.is_paused = False
        if self.recorder:
            self.recorder.resume_recording()
        ui.message(_("Recording"))
        return True
    def stop(self):
        if not self.is_recording:
            return None
        files = []
        if self.recorder:
            try:
                self.recorder.stop_recording()
                dest_folder = config.conf["xTrack"]["record"].get("destinationFolder") or os.path.expanduser("~/xTrack_recordings")
                if os.path.exists(dest_folder):
                    all_files = [os.path.join(dest_folder, f) for f in os.listdir(dest_folder)
                               if f.startswith("recording_") and f.endswith((".wav", ".mp3", ".flac", ".m4a"))]
                    all_files.sort(key=os.path.getmtime, reverse=True)
                    recent_files = [f for f in all_files if time.time() - os.path.getmtime(f) < 60]
                    files = recent_files
                    self.recorded_files = files
                log.info(f"Recorder stopped, files: {files}")
            except Exception as e:
                log.error(f"Error during recorder stop: {e}")
                ui.message(_("Error stopping recording"))
        self.is_recording = False
        self.is_paused = False
        if files:
            record_config = config.conf["xTrack"]["record"]
            if record_config.get("noiseSuppression", False):
                mic_files = self._get_microphone_files(files, record_config.get("recordingMode"))
                if mic_files:
                    log.info(f"Applying noise reduction to microphone files: {mic_files}")
                    self.apply_noise_reduction_to_files(mic_files, record_config)
                else:
                    log.info("No microphone files found for noise reduction")
                    if record_config.get("openFolderAfter", False):
                        self._open_destination_folder()
                    ui.message(_("Stop"))
            else:
                if record_config.get("openFolderAfter", False):
                    self._open_destination_folder()
                ui.message(_("Stop"))
        else:
            ui.message(_("No file saved"))
        return files
    def _get_microphone_files(self, files, recording_mode):
        """Identify microphone files based on recording mode - COMPLETELY EXCLUDE SYSTEM FILES."""
        if recording_mode == "system_only":
            return []
        if recording_mode == "mic_only":
            return files
        if recording_mode == "system_and_mic":
            return files
        if recording_mode == "system_and_mic_separate":
            mic_files = []
            for f in files:
                filename = os.path.basename(f).lower()
                if "mic" in filename:
                    mic_files.append(f)
                    log.info(f"Identified microphone file by name: {filename}")
                elif "system" in filename:
                    log.info(f"Excluded system audio file: {filename}")
                    continue
                elif len(files) == 2 and f == files[1]:
                    mic_files.append(f)
                    log.info(f"Identified microphone file by order (fallback): {filename}")
            log.info(f"Final microphone files for processing: {mic_files}")
            return mic_files
        return []
    def _open_destination_folder(self):
        """Open destination folder after recording."""
        dest = config.conf["xTrack"]["record"].get("destinationFolder") or os.path.expanduser("~/xTrack_recordings")
        try:
            os.startfile(dest)
        except Exception as e:
            log.error(f"Failed to open destination folder: {e}")
    def apply_noise_reduction_to_files(self, files, record_config):
        """Apply noise reduction to microphone files after recording stops."""
        try:
            def show_noise_reduction_progress():
                dlg = NoiseReductionProgressDialog(gui.mainFrame, len(files))
                dlg.Show()
                def process_files():
                    try:
                        preset = record_config.get("noiseReductionPreset", "medium")
                        use_compressor = record_config.get("useCompressor", False)
                        compressor_preset = record_config.get("compressorPreset", "medium")
                        for i, file_path in enumerate(files):
                            wx.CallAfter(dlg.update_progress, i + 1, len(files), file_path)
                            success = self._apply_audio_processing(file_path, preset, use_compressor, compressor_preset)
                            if not success:
                                log.error(f"Audio processing failed for: {file_path}")
                        wx.CallAfter(dlg.Destroy)
                        wx.CallAfter(tones.beep, 1000, 300)
                        wx.CallAfter(ui.message, _("Audio processing completed"))
                        if record_config.get("openFolderAfter", False):
                            self._open_destination_folder()
                    except Exception as e:
                        log.error(f"Error in audio processing: {e}")
                        wx.CallAfter(dlg.Destroy)
                        wx.CallAfter(ui.message, _("Audio processing failed: {}").format(str(e)))
                threading.Thread(target=process_files, daemon=True).start()
            wx.CallAfter(show_noise_reduction_progress)
        except Exception as e:
            log.error(f"Failed to start audio processing: {e}")
            ui.message(_("Audio processing failed to start"))
    def _apply_audio_processing(self, file_path, noise_preset, use_compressor, compressor_preset):
        """Apply advanced audio processing with noise reduction and optional compressor."""
        try:
            base_name = os.path.splitext(file_path)[0]
            extension = os.path.splitext(file_path)[1]
            output_file = f"{base_name}_processed{extension}"
            if self.recorder is None or not hasattr(self.recorder, 'tools_path'):
                 log.error("Recorder or tools_path not initialized")
                 return False
            ffmpeg_path = os.path.join(self.recorder.tools_path, "ffmpeg.exe")
            if not os.path.exists(ffmpeg_path):
                log.error("ffmpeg.exe not found")
                return False
            
            # Use only basic filters that are widely supported
            cmd = [ffmpeg_path, "-i", file_path, "-y"]
            filter_chain = []
            
            # Basic noise reduction with compatible filters only
            if noise_preset == "light":
                # Light: subtle noise reduction preserving voice quality
                filter_chain.append("highpass=f=80,lowpass=f=8000")
                filter_chain.append("afftdn=nf=-15")
                filter_chain.append("compand=attacks=0.1:decays=0.8:points=-90/-90|-70/-50|-30/-15|-10/-8|0/-4")
            elif noise_preset == "medium":
                # Medium: balanced noise reduction for general use
                filter_chain.append("highpass=f=100,lowpass=f=7500")
                filter_chain.append("afftdn=nf=-20")
                filter_chain.append("compand=attacks=0.05:decays=0.6:points=-90/-90|-65/-45|-35/-20|-15/-10|0/-5")
            elif noise_preset == "strong":
                # Strong: aggressive noise reduction for noisy environments
                filter_chain.append("highpass=f=120,lowpass=f=7000")
                filter_chain.append("afftdn=nf=-25")
                filter_chain.append("compand=attacks=0.02:decays=0.4:points=-90/-90|-60/-40|-30/-18|-12/-8|0/-6")
            elif noise_preset == "aggressive":
                # Aggressive: maximum noise reduction for very noisy recordings
                filter_chain.append("highpass=f=150,lowpass=f=6500")
                filter_chain.append("afftdn=nf=-30")
                filter_chain.append("compand=attacks=0.01:decays=0.3:points=-90/-90|-55/-35|-25/-15|-10/-6|0/-8")
            else:
                # Default to medium
                filter_chain.append("highpass=f=100,lowpass=f=7500")
                filter_chain.append("afftdn=nf=-20")
                filter_chain.append("compand=attacks=0.05:decays=0.6:points=-90/-90|-65/-45|-35/-20|-15/-10|0/-5")
            
            # Basic voice enhancement
            filter_chain.append("equalizer=f=200:t=q:w=2:g=-4")  # Reduce muddiness
            filter_chain.append("equalizer=f=800:t=q:w=1.5:g=2")  # Boost presence
            filter_chain.append("equalizer=f=2500:t=q:w=2:g=3")   # Boost clarity
            
            # Optional compressor for dynamic range control
            if use_compressor:
                if compressor_preset == "light":
                    filter_chain.append("acompressor=threshold=-20dB:ratio=2:attack=20:release=200:makeup=3")
                elif compressor_preset == "medium":
                    filter_chain.append("acompressor=threshold=-18dB:ratio=3:attack=15:release=150:makeup=4")
                elif compressor_preset == "strong":
                    filter_chain.append("acompressor=threshold=-15dB:ratio=4:attack=10:release=100:makeup=5")
                elif compressor_preset == "aggressive":
                    filter_chain.append("acompressor=threshold=-12dB:ratio=6:attack=5:release=80:makeup=6")
                else:
                    filter_chain.append("acompressor=threshold=-18dB:ratio=3:attack=15:release=150:makeup=4")
            
            # Final loudness normalization (EBU R128 standard)
            filter_chain.append("loudnorm=I=-16:TP=-1.5:LRA=11")
            
            # Join all filters
            filter_complex = ",".join(filter_chain)
            cmd.extend(["-af", filter_complex, "-ar", "48000", "-ac", "2", output_file])
            
            log.info(f"Audio processing command: {' '.join(cmd)}")
            process = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
                encoding='utf-8',
                errors='ignore'
            )
            if process.returncode == 0:
                log.info(f"Audio processing successful for: {os.path.basename(file_path)}")
                try:
                    os.remove(file_path)
                    os.rename(output_file, file_path)
                except Exception as e:
                    log.error(f"Error replacing original file: {e}")
                return True
            else:
                log.error(f"Audio processing failed for {file_path}: {process.stderr}")
                try:
                    if os.path.exists(output_file):
                        os.remove(output_file)
                except Exception:
                    pass
                return False
        except Exception as e:
            log.error(f"Error applying audio processing to {file_path}: {e}")
            return False

recorder = Recorder()

class RecordSettingsDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title=_("Record Settings"),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.parent = parent
        self.settings = self.load_settings()
        self.main = gui.guiHelper.BoxSizerHelper(self, wx.VERTICAL)
        self.makeSettings()
        self.main.addDialogDismissButtons(self.CreateButtonSizer(wx.OK | wx.CANCEL))
        self.Bind(wx.EVT_BUTTON, self.onOk, id=wx.ID_OK)
        self.Bind(wx.EVT_BUTTON, self.onCancel, id=wx.ID_CANCEL)
        self.SetSizer(self.main.sizer)
        self.Fit()
        self.CenterOnScreen()
        wx.CallLater(100, self.set_initial_focus)
    def set_initial_focus(self):
        if hasattr(self, 'modeCombo') and self.modeCombo:
            self.modeCombo.SetFocus()
    def load_settings(self):
        try:
            if "xTrack" in config.conf and "record" in config.conf["xTrack"]:
                settings = config.conf["xTrack"]["record"].copy()
            else:
                config_path = os.path.join(config.getUserDefaultConfigPath(), "xTrack.json")
                if os.path.exists(config_path):
                    with open(config_path, "r", encoding="utf-8") as f:
                        config_data = json.load(f)
                        settings = config_data.get("record", {})
                else:
                    settings = {}
            
            # Convert all values to correct types
            for key in ["countIn", "openFolderAfter", "noiseSuppression", "useCompressor"]:
                if key in settings:
                    if isinstance(settings[key], str):
                        settings[key] = settings[key].lower() in ["true", "1", "yes"]
                    else:
                        settings[key] = bool(settings[key])
                else:
                    settings[key] = False
            
            # Convert gain values to integer
            for key in ["systemGain", "microphoneGain", "mp3Quality"]:
                if key in settings:
                    if isinstance(settings[key], str):
                        try:
                            settings[key] = int(settings[key])
                        except ValueError:
                            settings[key] = 0
                    elif not isinstance(settings[key], int):
                        settings[key] = 0
                else:
                    settings[key] = 0 if key in ["systemGain", "microphoneGain"] else 192
            
            # Limit values between 0-10 for gain
            settings["systemGain"] = max(0, min(10, settings["systemGain"]))
            settings["microphoneGain"] = max(0, min(10, settings["microphoneGain"]))
            
            # Default values
            if "recordingMode" not in settings: 
                settings["recordingMode"] = "system_and_mic"
            if "format" not in settings: 
                settings["format"] = "mp3"
            if "mp3Quality" not in settings: 
                settings["mp3Quality"] = 192
            if "noiseReductionPreset" not in settings: 
                settings["noiseReductionPreset"] = "medium"
            if "compressorPreset" not in settings: 
                settings["compressorPreset"] = "medium"
            if "destinationFolder" not in settings: 
                settings["destinationFolder"] = os.path.expanduser("~/xTrack_recordings")
            
            return settings
            
        except Exception as e:
            log.error(f"Failed to load settings: {e}")
            return self._get_default_settings()
    
    def _get_default_settings(self):
        """Get default settings when loading fails."""
        return {
            "recordingMode": "system_and_mic",
            "format": "mp3",
            "mp3Quality": 192,
            "countIn": False,
            "openFolderAfter": False,
            "noiseSuppression": False,
            "noiseReductionPreset": "medium",
            "useCompressor": False,
            "compressorPreset": "medium",
            "systemGain": 0,
            "microphoneGain": 0,
            "destinationFolder": os.path.expanduser("~/xTrack_recordings")
        }
    
    def save_settings(self):
        self.settings["recordingMode"] = [
            "system_and_mic",
            "system_only",
            "mic_only",
            "system_and_mic_separate"
        ][self.modeCombo.GetSelection()]
        self.settings["format"] = self.formatCombo.GetStringSelection()
        self.settings["mp3Quality"] = int(self.qualityCombo.GetStringSelection())
        self.settings["countIn"] = self.countInCheck.GetValue()
        self.settings["systemGain"] = self.system_gain_ctrl.GetValue()
        self.settings["microphoneGain"] = self.microphone_gain_ctrl.GetValue()
        self.settings["noiseSuppression"] = self.noiseSuppressionCheck.GetValue()
        self.settings["noiseReductionPreset"] = ["light", "medium", "strong", "aggressive"][self.presetCombo.GetSelection()]
        self.settings["useCompressor"] = self.compressorCheck.GetValue()
        self.settings["compressorPreset"] = ["light", "medium", "strong", "aggressive"][self.compressorPresetCombo.GetSelection()]
        self.settings["destinationFolder"] = self.folderText.GetValue()
        
        try:
            if "xTrack" not in config.conf:
                config.conf["xTrack"] = {}
            config.conf["xTrack"]["record"] = self.settings.copy()
            config_path = os.path.join(config.getUserDefaultConfigPath(), "xTrack.json")
            config_data = {}
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
            config_data["record"] = self.settings.copy()
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=4, ensure_ascii=False)
            log.info("Record settings saved successfully")
            return True
        except Exception as e:
            log.error(f"Failed to save settings: {e}")
            ui.message(_("Failed to save settings"))
            return False
    def onFormatChange(self, event):
        """Toggle MP3 Quality combo box visibility based on format selection."""
        is_mp3 = self.formatCombo.GetStringSelection() == "mp3"
        self.qualityCombo.Show(is_mp3)
        self.main.sizer.Layout()
    def onOk(self, event):
        if self.save_settings():
            self.EndModal(wx.ID_OK)
    def onCancel(self, event):
        self.EndModal(wx.ID_CANCEL)
    def makeSettings(self):
        modeChoices = [
            _("System audio and Microphone"),
            _("System audio only"),
            _("Microphone only"),
            _("System audio and Microphone separate files")
        ]
        self.modeCombo = self.main.addLabeledControl(
            _("Recording Mode:"), wx.Choice, choices=modeChoices
        )
        modeMap = {
            "system_and_mic": 0,
            "system_only": 1,
            "mic_only": 2,
            "system_and_mic_separate": 3
        }
        current_mode = self.settings["recordingMode"]
        if current_mode in modeMap:
            self.modeCombo.SetSelection(modeMap[current_mode])
        else:
            self.modeCombo.SetSelection(0)
        formatChoices = ["mp3", "wav"]
        self.formatCombo = self.main.addLabeledControl(
            _("Format:"), wx.Choice, choices=formatChoices
        )
        current_format = self.settings["format"]
        if current_format in formatChoices:
            self.formatCombo.SetSelection(formatChoices.index(current_format))
        else:
            self.formatCombo.SetSelection(0)
        self.formatCombo.Bind(wx.EVT_CHOICE, self.onFormatChange)
        qualityChoices = ["320", "256", "192", "128"]
        self.qualityCombo = self.main.addLabeledControl(
            _("MP3 Quality:"), wx.Choice, choices=qualityChoices
        )
        current_quality = str(self.settings["mp3Quality"])
        if current_quality in qualityChoices:
            self.qualityCombo.SetSelection(qualityChoices.index(current_quality))
        else:
            self.qualityCombo.SetSelection(2)
        self.qualityCombo.Show(self.settings["format"] == "mp3")
        self.countInCheck = self.main.addItem(
            wx.CheckBox(self, label=_("Count in (3 beeps before recording)"))
        )
        self.countInCheck.SetValue(bool(self.settings.get("countIn", False)))
        system_gain_value = self.settings.get("systemGain", 0)
        system_gain_value = max(0, min(10, system_gain_value))
        self.system_gain_ctrl = self.main.addLabeledControl(
            _("System Gain"), wx.SpinCtrl, initial=system_gain_value, min=0, max=10
        )
        self.system_gain_ctrl.SetToolTip(_("Adjust system audio volume level (0-10). Higher values increase volume."))
        mic_gain_value = self.settings.get("microphoneGain", 0)
        mic_gain_value = max(0, min(10, mic_gain_value))
        self.microphone_gain_ctrl = self.main.addLabeledControl(
            _("Microphone Gain"), wx.SpinCtrl, initial=mic_gain_value, min=0, max=10
        )
        self.microphone_gain_ctrl.SetToolTip(_("Adjust microphone volume level (0-10). Higher values increase volume."))
        noise_group = wx.StaticBox(self, label=_("Noise Reduction Settings"))
        noise_sizer = wx.StaticBoxSizer(noise_group, wx.VERTICAL)
        self.main.addItem(noise_sizer)
        noise_helper = gui.guiHelper.BoxSizerHelper(self, sizer=noise_sizer)
        self.noiseSuppressionCheck = noise_helper.addItem(
            wx.CheckBox(self, label=_("Apply noise reduction after recording"))
        )
        self.noiseSuppressionCheck.SetToolTip(_("Noise reduction will be applied automatically to microphone audio only after stopping recording. System audio files will not be processed."))
        self.noiseSuppressionCheck.SetValue(bool(self.settings.get("noiseSuppression", False)))
        preset_sizer = wx.BoxSizer(wx.HORIZONTAL)
        preset_label = wx.StaticText(self, label=_("Noise Reduction Preset:"))
        preset_sizer.Add(preset_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        preset_choices = [
            _("Light (maximum voice clarity)"),
            _("Medium (balanced noise reduction)"),
            _("Strong (aggressive noise reduction)"),
            _("Aggressive (maximum noise reduction)")
        ]
        self.presetCombo = wx.ComboBox(self, choices=preset_choices, style=wx.CB_READONLY)
        preset_map = {
            "light": 0,
            "medium": 1,
            "strong": 2,
            "aggressive": 3
        }
        current_preset = self.settings.get("noiseReductionPreset", "medium")
        if current_preset in preset_map:
            self.presetCombo.SetSelection(preset_map[current_preset])
        else:
            self.presetCombo.SetSelection(1)
        preset_sizer.Add(self.presetCombo, 1, wx.EXPAND | wx.ALL, 5)
        noise_sizer.Add(preset_sizer, 0, wx.EXPAND | wx.ALL, 5)
        compressor_group = wx.StaticBox(self, label=_("Dynamic Range Compression"))
        compressor_sizer = wx.StaticBoxSizer(compressor_group, wx.VERTICAL)
        self.main.addItem(compressor_sizer)
        compressor_helper = gui.guiHelper.BoxSizerHelper(self, sizer=compressor_sizer)
        self.compressorCheck = compressor_helper.addItem(
            wx.CheckBox(self, label=_("Apply dynamic range compression after noise reduction"))
        )
        self.compressorCheck.SetToolTip(_("Compression makes quiet sounds louder and loud sounds quieter, improving overall audio clarity after noise reduction."))
        self.compressorCheck.SetValue(bool(self.settings.get("useCompressor", False)))
        compressor_preset_sizer = wx.BoxSizer(wx.HORIZONTAL)
        compressor_preset_label = wx.StaticText(self, label=_("Compression Preset:"))
        compressor_preset_sizer.Add(compressor_preset_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        compressor_preset_choices = [
            _("Light (subtile enhancement)"),
            _("Medium (balanced compression)"),
            _("Strong (aggressive compression)"),
            _("Aggressive (maximum compression)")
        ]
        self.compressorPresetCombo = wx.ComboBox(self, choices=compressor_preset_choices, style=wx.CB_READONLY)
        compressor_preset_map = {
            "light": 0,
            "medium": 1,
            "strong": 2,
            "aggressive": 3
        }
        current_compressor_preset = self.settings.get("compressorPreset", "medium")
        if current_compressor_preset in compressor_preset_map:
            self.compressorPresetCombo.SetSelection(compressor_preset_map[current_compressor_preset])
        else:
            self.compressorPresetCombo.SetSelection(1)
        compressor_preset_sizer.Add(self.compressorPresetCombo, 1, wx.EXPAND | wx.ALL, 5)
        compressor_sizer.Add(compressor_preset_sizer, 0, wx.EXPAND | wx.ALL, 5)
        self.openFolderAfterCheck = self.main.addItem(
            wx.CheckBox(self, label=_("Open destination folder after stopping recording"))
        )
        self.openFolderAfterCheck.SetValue(bool(self.settings.get("openFolderAfter", False)))
        folder_sizer = wx.BoxSizer(wx.HORIZONTAL)
        folder_label = wx.StaticText(self, label=_("Destination Folder:"))
        folder_sizer.Add(folder_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.folderText = wx.TextCtrl(self, value=self.settings["destinationFolder"])
        folder_sizer.Add(self.folderText, 1, wx.EXPAND | wx.ALL, 5)
        self.browseButton = wx.Button(self, label=_("Browse..."))
        self.browseButton.Bind(wx.EVT_BUTTON, self.onBrowse)
        folder_sizer.Add(self.browseButton, 0, wx.ALL, 5)
        self.main.addItem(folder_sizer)
    def onBrowse(self, event):
        """Open a directory dialog to choose the destination folder."""
        dlg = wx.DirDialog(
            self, _("Choose recording destination folder"),
            self.folderText.GetValue(),
            wx.DD_DEFAULT_STYLE | wx.DD_NEW_DIR_BUTTON
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.folderText.SetValue(dlg.GetPath())
            self.settings["destinationFolder"] = dlg.GetPath()
        dlg.Destroy()
