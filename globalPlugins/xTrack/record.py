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

addonHandler.initTranslation()

# Add Tools directory to sys.path
addon_dir = os.path.dirname(__file__)
tools_dir = os.path.join(addon_dir, "Tools")
pyaudiowpatch_dir = os.path.join(tools_dir, "pyaudiowpatch")

if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

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
        self.tools_path = os.path.join(self.addon_dir, "Tools")
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
        """Generate beep tones using system tones with shorter final beep."""
        for _ in range(3):
            tones.beep(440, 200)
            time.sleep(0.5)
        tones.beep(880, 200)  # Changed from 500ms to 200ms
        time.sleep(0.1)  # Add small delay to ensure beep ends before recording starts

    def _apply_ffmpeg_audio_enhancement(self, input_file, enhancement_config, is_microphone=True):
        """Apply audio enhancement using ffmpeg 8.0 filters."""
        if not is_microphone:
            return input_file
        
        try:
            # Create temporary output file
            temp_file = tempfile.NamedTemporaryFile(
                delete=False, 
                suffix=os.path.splitext(input_file)[1]
            )
            temp_file.close()
            output_path = temp_file.name
            
            # Build filter chain based on enhancement configuration
            filter_parts = []
            
            # 1. Hum removal (high-pass filter)
            if enhancement_config.get("hum_removal", False):
                # Remove low frequency hum (50-60 Hz and harmonics)
                filter_parts.append("highpass=f=80,lowpass=f=16000")
            
            # 2. Noise reduction based on preset
            if enhancement_config.get("noise_suppression", False):
                noise_preset = enhancement_config.get("noise_preset", "medium")
                noise_filters = {
                    "light": "afftdn=nr=20:nf=-15:nt=w:tn=1:om=o",  # Gentle noise reduction
                    "medium": "afftdn=nr=40:nf=-25:nt=w:tn=1:om=o:track_noise=1",  # Balanced reduction
                    "aggressive": "afftdn=nr=60:nf=-35:nt=w:tn=1:om=o:track_noise=1:track_residual=1",  # Strong reduction
                    "studio": "afftdn=nr=50:nf=-30:nt=w:tn=1:om=o:track_noise=1:track_residual=1:band_multiplier=1.5",  # Studio quality
                    "broadcast": "afftdn=nr=55:nf=-28:nt=w:tn=1:om=o:track_noise=1:track_residual=1:band_multiplier=2.0",  # Broadcast quality
                    "custom": enhancement_config.get("custom_filter", "afftdn=nr=45:nf=-22:nt=w:tn=1:om=o")
                }
                filter_parts.append(noise_filters.get(noise_preset, noise_filters["medium"]))
            
            # 3. Clarity enhancement (presence boost)
            if enhancement_config.get("clarity_boost", False):
                # Multi-band equalizer for voice clarity
                # Boost presence frequencies (2-5 kHz) and reduce muddiness (200-500 Hz)
                clarity_filter = (
                    "equalizer=f=300:width_type=h:width=100:g=-3,"
                    "equalizer=f=500:width_type=h:width=200:g=-2,"
                    "equalizer=f=3000:width_type=h:width=1000:g=4,"
                    "equalizer=f=5000:width_type=h:width=2000:g=2"
                )
                filter_parts.append(clarity_filter)
            
            # 4. Dynamic range compression for more consistent volume
            if enhancement_config.get("dynamic_compression", False):
                # Gentle compression for voice recording
                compression_filter = "compand=attacks=0:decays=0.2:points=-80/-80|-30/-15|-20/-10|0/0"
                filter_parts.append(compression_filter)
            
            # 5. Limiter to prevent clipping
            if enhancement_config.get("limiter", False):
                filter_parts.append("alimiter=limit=0.8")
            
            # Combine all filters
            if filter_parts:
                filter_str = ",".join(filter_parts)
                log.info(f"xTrack: Applying audio enhancement filters: {filter_str}")
                
                # Build ffmpeg command with enhancement chain
                cmd = [
                    self.ffmpeg_exe, "-y", "-i", input_file,
                    "-af", filter_str,
                    "-ar", "48000",  # Upsample to 48kHz for better quality
                    "-ac", "2",
                    "-codec:a", "libmp3lame",
                    "-q:a", "2",  # High quality MP3
                    output_path
                ]
                
                # Run ffmpeg
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=45  # Increased timeout for complex processing
                )
                
                if result.returncode == 0:
                    # Replace original file with processed file
                    shutil.move(output_path, input_file)
                    log.info("xTrack: Audio enhancement applied successfully")
                    return input_file
                else:
                    log.error(f"xTrack: Audio enhancement failed: {result.stderr}")
                    if os.path.exists(output_path):
                        os.remove(output_path)
                    return input_file
            else:
                # No filters to apply
                return input_file
                
        except Exception as e:
            log.error(f"xTrack: Error applying audio enhancement: {e}")
            if 'output_path' in locals() and os.path.exists(output_path):
                os.remove(output_path)
            return input_file

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
            
            # Map our mode to backend mode
            backend_mode = self.recording_mode_map.get(mode, "system audio and microphone")
            
            # Get destination folder
            dest = conf.get("destinationFolder", os.path.expanduser("~/xTrack_recordings"))
            
            # Create the backend recorder
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
            noise_suppression = conf.get("noiseSuppression", False)
            noise_preset = conf.get("noiseReductionPreset", "medium")
            hum_removal = conf.get("humRemoval", False)
            clarity_boost = conf.get("clarityBoost", False)
            dynamic_compression = conf.get("dynamicCompression", False)
            limiter = conf.get("limiter", True)
            custom_filter = conf.get("noiseCustomFilter", "afftdn=nr=45:nf=-22:nt=w:tn=1:om=o")
            
            # Setup backend recorder
            if not self._setup_backend_recorder():
                log.error("xTrack: Failed to setup backend recorder")
                self.output_files = []
                return
            
            # Start recording
            self.backend_recorder.start_recording()
            log.info("xTrack: Backend recording started")
            
            # Monitor recording
            while not self._stop_event.is_set():
                # Handle pause
                if self._pause_event.is_set():
                    if hasattr(self.backend_recorder, 'pause_recording'):
                        self.backend_recorder.pause_recording()
                    
                    # Wait while paused
                    while self._pause_event.is_set() and not self._stop_event.is_set():
                        time.sleep(0.1)
                    
                    if hasattr(self.backend_recorder, 'resume_recording'):
                        self.backend_recorder.resume_recording()
                
                time.sleep(0.1)
            
            # Stop recording
            if hasattr(self.backend_recorder, 'stop_recording'):
                self.backend_recorder.stop_recording()
            
            # Get output files from recording folder
            dest = conf.get("destinationFolder", os.path.expanduser("~/xTrack_recordings"))
            
            # Find the latest recording files
            if os.path.exists(dest):
                files = []
                for f in os.listdir(dest):
                    if f.lower().endswith(('.mp3', '.wav', '.m4a', '.flac')):
                        files.append(os.path.join(dest, f))
                
                # Sort by modification time, newest first
                files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                
                # Get files based on recording mode
                if mode == "system_and_mic_sep":
                    # Look for system and mic files
                    system_files = [f for f in files if 'system' in f.lower()]
                    mic_files = [f for f in files if 'mic' in f.lower()]
                    self.output_files = system_files[:1] + mic_files[:1]
                    
                    # Apply audio enhancement to microphone files if enabled
                    if (noise_suppression or hum_removal or clarity_boost or dynamic_compression) and mic_files:
                        for i, mic_file in enumerate(mic_files[:1]):
                            enhancement_config = {
                                "noise_suppression": noise_suppression,
                                "noise_preset": noise_preset,
                                "hum_removal": hum_removal,
                                "clarity_boost": clarity_boost,
                                "dynamic_compression": dynamic_compression,
                                "limiter": limiter,
                                "custom_filter": custom_filter
                            }
                            processed_file = self._apply_ffmpeg_audio_enhancement(
                                mic_file, 
                                enhancement_config,
                                is_microphone=True
                            )
                            if i < len(self.output_files):
                                # Find the mic file in output_files and update it
                                for j, out_file in enumerate(self.output_files):
                                    if 'mic' in out_file.lower():
                                        self.output_files[j] = processed_file
                                        break
                else:
                    # Look for recording files (not system or mic specific)
                    recording_files = [f for f in files if 'recording' in f.lower()]
                    self.output_files = recording_files[:1]  # Get most recent
                    
                    # Apply audio enhancement if enabled and mode includes microphone
                    if (noise_suppression or hum_removal or clarity_boost or dynamic_compression) and mode in ["system_and_mic", "mic_only"]:
                        for i, rec_file in enumerate(recording_files[:1]):
                            enhancement_config = {
                                "noise_suppression": noise_suppression,
                                "noise_preset": noise_preset,
                                "hum_removal": hum_removal,
                                "clarity_boost": clarity_boost,
                                "dynamic_compression": dynamic_compression,
                                "limiter": limiter,
                                "custom_filter": custom_filter
                            }
                            processed_file = self._apply_ffmpeg_audio_enhancement(
                                rec_file,
                                enhancement_config,
                                is_microphone=True
                            )
                            self.output_files[i] = processed_file
            
            log.info(f"xTrack: Recording completed. Found files: {self.output_files}")
            
        except Exception as e:
            log.error(f"xTrack: Recording error: {str(e)}")
            import traceback
            log.error(f"xTrack: Traceback: {traceback.format_exc()}")
            self.output_files = []
            
            # Try to stop backend if it exists
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

    def start(self):
        """Start recording."""
        if self.is_recording:
            log.warning("xTrack: Already recording")
            return
        
        # Play count-in if enabled
        conf = config.conf["xTrack"]["record"]
        if conf.get("countIn", False):
            self._play_count_in()
        
        # Reset events
        self._stop_event.clear()
        self._pause_event.clear()
        
        # Start recording thread
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
            # Already paused, resume instead
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
        
        # Signal stop
        self._stop_event.set()
        
        # Wait for thread to finish
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                log.warning("xTrack: Recording thread did not finish in time")
        
        # Clean up
        self.is_recording = False
        self.is_paused = False
        
        # Open folder if enabled
        conf = config.conf["xTrack"]["record"]
        if conf.get("openFolderAfter", False) and self.output_files:
            try:
                dest_dir = os.path.dirname(self.output_files[0])
                log.info(f"xTrack: Opening folder: {dest_dir}")
                subprocess.Popen(f'explorer "{dest_dir}"', shell=True)
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
        
        # Set defaults if not present
        defaults = {
            "recordingMode": "system_and_mic",
            "format": "mp3",
            "mp3Quality": 192,
            "countIn": False,
            "openFolderAfter": False,
            "noiseSuppression": False,
            "noiseReductionPreset": "medium",
            "noiseCustomFilter": "afftdn=nr=45:nf=-22:nt=w:tn=1:om=o",
            "humRemoval": False,
            "clarityBoost": False,
            "dynamicCompression": False,
            "limiter": True,
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

        # 1. Mode
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

        # 2. Format
        self.formatCombo = sHelper.addLabeledControl(_("&Format:"), wx.Choice, choices=["mp3", "wav", "m4a"])
        curr_fmt = self.settings.get("format", "mp3")
        self.formatCombo.SetStringSelection(curr_fmt)
        self.formatCombo.Bind(wx.EVT_CHOICE, self.onFormatChange)

        # 3. Quality settings
        self.qualityLabel = wx.StaticText(self, label=_("MP3 Quality (kbps):"))
        sHelper.addItem(self.qualityLabel)
        self.qualityCombo = wx.Choice(self, choices=["64", "96", "128", "160", "192", "256", "320"])
        sHelper.addItem(self.qualityCombo)
        
        # Set current quality
        curr_quality = str(self.settings.get("mp3Quality", "192"))
        if curr_quality in ["64", "96", "128", "160", "192", "256", "320"]:
            self.qualityCombo.SetStringSelection(curr_quality)
        else:
            self.qualityCombo.SetSelection(4)  # Default to 192

        # 4. Advanced Audio Enhancement Settings
        enhancement_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, _("Audio Enhancement (Microphone)"))
        enhancement_box = enhancement_sizer.GetStaticBox()
        
        # Noise suppression checkbox
        self.noiseCheck = wx.CheckBox(enhancement_box, label=_("&Enable Noise Reduction"))
        self.noiseCheck.SetValue(bool(self.settings.get("noiseSuppression", False)))
        self.noiseCheck.Bind(wx.EVT_CHECKBOX, self.onEnhancementCheck)
        enhancement_sizer.Add(self.noiseCheck, 0, wx.ALL, 5)
        
        # Noise reduction preset
        preset_label = wx.StaticText(enhancement_box, label=_("Noise Reduction Preset:"))
        enhancement_sizer.Add(preset_label, 0, wx.LEFT | wx.TOP, 5)
        
        self.noisePresetCombo = wx.Choice(enhancement_box, choices=[
            _("Light - Gentle reduction for minimal noise"),
            _("Medium - Balanced reduction for normal environments"),
            _("Aggressive - Strong reduction for noisy environments"),
            _("Studio - Professional quality for studio recording"),
            _("Broadcast - Broadcast quality with voice clarity"),
            _("Custom - Custom filter settings")
        ])
        
        preset_map = {
            "light": 0,
            "medium": 1,
            "aggressive": 2,
            "studio": 3,
            "broadcast": 4,
            "custom": 5
        }
        curr_preset = self.settings.get("noiseReductionPreset", "medium")
        self.noisePresetCombo.SetSelection(preset_map.get(curr_preset, 1))
        self.noisePresetCombo.Bind(wx.EVT_CHOICE, self.onNoisePresetChange)
        enhancement_sizer.Add(self.noisePresetCombo, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        
        # Custom filter settings (hidden by default)
        custom_sizer = wx.BoxSizer(wx.VERTICAL)
        custom_label = wx.StaticText(enhancement_box, label=_("Custom FFmpeg Filter (Advanced):"))
        custom_sizer.Add(custom_label, 0, wx.TOP, 5)
        
        self.customFilterText = wx.TextCtrl(enhancement_box, value=self.settings.get("noiseCustomFilter", "afftdn=nr=45:nf=-22:nt=w:tn=1:om=o"))
        custom_sizer.Add(self.customFilterText, 0, wx.EXPAND | wx.TOP, 2)
        
        # Help text for custom filter
        help_text = _("Example filters:") + "\n" + \
                   _("- Light: afftdn=nr=20:nf=-15:nt=w") + "\n" + \
                   _("- Medium: afftdn=nr=40:nf=-25:nt=w") + "\n" + \
                   _("- Aggressive: afftdn=nr=60:nf=-35:nt=w")
        help_label = wx.StaticText(enhancement_box, label=help_text)
        help_label.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        custom_sizer.Add(help_label, 0, wx.TOP, 5)
        
        self.customFilterSizer = custom_sizer
        enhancement_sizer.Add(custom_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Hum removal checkbox
        self.humRemovalCheck = wx.CheckBox(enhancement_box, label=_("&Remove low-frequency hum (50-60 Hz)"))
        self.humRemovalCheck.SetValue(bool(self.settings.get("humRemoval", False)))
        enhancement_sizer.Add(self.humRemovalCheck, 0, wx.ALL, 5)
        
        # Clarity boost checkbox
        self.clarityBoostCheck = wx.CheckBox(enhancement_box, label=_("Boost voice &clarity (reduce muddiness)"))
        self.clarityBoostCheck.SetValue(bool(self.settings.get("clarityBoost", False)))
        enhancement_sizer.Add(self.clarityBoostCheck, 0, wx.ALL, 5)
        
        # Dynamic compression checkbox
        self.dynamicCompressionCheck = wx.CheckBox(enhancement_box, label=_("Apply &dynamic compression"))
        self.dynamicCompressionCheck.SetValue(bool(self.settings.get("dynamicCompression", False)))
        enhancement_sizer.Add(self.dynamicCompressionCheck, 0, wx.ALL, 5)
        
        # Limiter checkbox
        self.limiterCheck = wx.CheckBox(enhancement_box, label=_("Enable &limiter (prevent clipping)"))
        self.limiterCheck.SetValue(bool(self.settings.get("limiter", True)))
        enhancement_sizer.Add(self.limiterCheck, 0, wx.ALL, 5)
        
        sHelper.addItem(enhancement_sizer)
        
        # Show/hide custom filter based on initial state
        self.onEnhancementCheck(None)
        self.onNoisePresetChange(None)

        # 5. Gain controls
        self.micGainEdit = sHelper.addLabeledControl(_("&Microphone Gain (0-10):"), wx.SpinCtrl, min=0, max=10, initial=int(self.settings.get("microphoneGain", 0)))
        self.sysGainEdit = sHelper.addLabeledControl(_("&System Gain (0-10):"), wx.SpinCtrl, min=0, max=10, initial=int(self.settings.get("systemGain", 0)))

        # 6. Other options
        self.countInCheck = sHelper.addItem(wx.CheckBox(self, label=_("&Count-in before recording")))
        self.countInCheck.SetValue(bool(self.settings.get("countIn", False)))
        
        self.openFolderCheck = sHelper.addItem(wx.CheckBox(self, label=_("&Open folder after recording")))
        self.openFolderCheck.SetValue(bool(self.settings.get("openFolderAfter", False)))

        # 7. Destination folder
        destLabel = wx.StaticText(self, label=_("&Destination Folder:"))
        sHelper.addItem(destLabel)
        dest_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.destEdit = wx.TextCtrl(self, value=self.settings.get("destinationFolder", ""))
        dest_sizer.Add(self.destEdit, 1, wx.EXPAND | wx.RIGHT, 5)
        browseBtn = wx.Button(self, label=_("&Browse..."))
        browseBtn.Bind(wx.EVT_BUTTON, self.onBrowse)
        dest_sizer.Add(browseBtn)
        sHelper.sizer.Add(dest_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # Library status
        status_text = _("Audio Backend Status: ")
        if BACKEND_AVAILABLE:
            status_text += _("WasapiSoundRecorder (system audio available)")
        else:
            status_text += _("WasapiSoundRecorder (check Tools/recorder_backend.py)")
        
        status_label = wx.StaticText(self, label=status_text)
        status_label.Wrap(400)
        sHelper.addItem(status_label)

        # Note about audio enhancement
        note_text = _("Note: Audio enhancement uses FFmpeg 8.0 filters for professional audio processing.")
        note_label = wx.StaticText(self, label=note_text)
        note_label.Wrap(400)
        sHelper.addItem(note_label)

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

    def onEnhancementCheck(self, event):
        """Handle enhancement checkbox."""
        enabled = self.noiseCheck.GetValue()
        # Enable/disable all enhancement controls
        for child in self.noisePresetCombo.GetParent().GetChildren():
            if child not in [self.humRemovalCheck, self.clarityBoostCheck, self.dynamicCompressionCheck, self.limiterCheck]:
                child.Enable(enabled)
        self.onNoisePresetChange(None)
        self.Layout()

    def onNoisePresetChange(self, event):
        """Handle noise preset change."""
        enabled = self.noiseCheck.GetValue()
        preset_idx = self.noisePresetCombo.GetSelection()
        
        # Show custom filter settings only for "Custom" preset
        if enabled and preset_idx == 5:  # Custom
            self.customFilterSizer.ShowItems(True)
        else:
            self.customFilterSizer.ShowItems(False)
        
        self.Layout()

    def onBrowse(self, event):
        dlg = wx.DirDialog(self, _("Select Destination Folder"), self.destEdit.GetValue())
        if dlg.ShowModal() == wx.ID_OK:
            self.destEdit.SetValue(dlg.GetPath())
        dlg.Destroy()

    def onOk(self, event):
        fmt = self.formatCombo.GetStringSelection()
        
        # Map preset selection back to string value
        preset_map = {
            0: "light",
            1: "medium", 
            2: "aggressive",
            3: "studio",
            4: "broadcast",
            5: "custom"
        }
        preset_idx = self.noisePresetCombo.GetSelection()
        noise_preset = preset_map.get(preset_idx, "medium")
        
        self.settings.update({
            "recordingMode": self.modeValues[self.modeCombo.GetSelection()],
            "format": fmt,
            "mp3Quality": int(self.qualityCombo.GetStringSelection()) if fmt == "mp3" else 192,
            "countIn": self.countInCheck.GetValue(),
            "noiseSuppression": self.noiseCheck.GetValue(),
            "noiseReductionPreset": noise_preset,
            "noiseCustomFilter": self.customFilterText.GetValue(),
            "humRemoval": self.humRemovalCheck.GetValue(),
            "clarityBoost": self.clarityBoostCheck.GetValue(),
            "dynamicCompression": self.dynamicCompressionCheck.GetValue(),
            "limiter": self.limiterCheck.GetValue(),
            "openFolderAfter": self.openFolderCheck.GetValue(),
            "destinationFolder": self.destEdit.GetValue(),
            "systemGain": self.sysGainEdit.GetValue(),
            "microphoneGain": self.micGainEdit.GetValue()
        })
        event.Skip()