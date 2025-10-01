# Trim.py

import wx
import os
import subprocess
import re
import threading
import tones
import time
from gui import guiHelper
import ui
import json
import logging
AddOnName = "xTrack"
sectionName = AddOnName
# Set up logging for debugging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

class TrimAudioVideoDialog(wx.Dialog):
    """Dialog for trimming audio files using FFmpeg."""
    def __init__(self, parent, selected_files, tools_path):
        super().__init__(parent, title=_("Trim Audio"))
        # Ensure only a single file is passed to the dialog
        if not selected_files:
            raise ValueError("No file was selected.")
        self.selected_file = selected_files[0]
        self.tools_path = tools_path
        self.file_duration = ""
        self.file_duration_seconds = 0
        self.output_path = os.path.dirname(self.selected_file)
        self.config_path = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "nvda", "config", "xTrack.json")
        self.temp_preview_file = ""
        self.init_ui()
        # Set title to show selected file name
        self.SetTitle(_("Trim Audio: {}").format(os.path.basename(self.selected_file)))
        # Load last used times if available
        config_data = self.load_config()
        last_file = config_data.get("TrimLastFile", "")
        if self.selected_file == last_file:
            self.start_time_ctrl.SetValue(config_data.get("TrimLastStartTime", ""))
            self.end_time_ctrl.SetValue(config_data.get("TrimLastEndTime", ""))
        # Fetch file duration in a background thread
        threading.Thread(target=self.get_file_duration, daemon=True).start()
        # Bind events for dialog closing
        self.Bind(wx.EVT_CLOSE, self.on_close)
        # Handle button clicks
        self.Bind(wx.EVT_BUTTON, self.on_button_click)
        
    def on_button_click(self, event):
        """Handle button clicks for OK and Cancel buttons."""
        if event.GetId() == wx.ID_OK:
            self.on_trim(event)
        elif event.GetId() == wx.ID_CANCEL:
            self.cleanup_temp_file()
            self.EndModal(wx.ID_CANCEL)
        event.Skip()
        
    def load_config(self):
        """Load configuration from xTrack.json."""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logging.error(f"Failed to load configuration: {str(e)}")
            ui.message(_("Failed to load configuration: {}").format(str(e)))
            return {}
            
    def save_config(self, config_data):
        """Save configuration to xTrack.json."""
        try:
            config_dir = os.path.dirname(self.config_path)
            if not os.path.exists(config_dir):
                os.makedirs(config_dir)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4)
        except Exception as e:
            logging.error(f"Failed to save configuration: {str(e)}")
            ui.message(_("Failed to save configuration: {}").format(str(e)))
            
    def init_ui(self):
        """Build dialog UI."""
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        # Selected file
        file_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Selected File"))
        self.file_label = wx.StaticText(self, label=os.path.basename(self.selected_file))
        file_sizer.Add(self.file_label, 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(file_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Time
        time_sizer = wx.BoxSizer(wx.VERTICAL)
        time_helper = guiHelper.BoxSizerHelper(self, sizer=time_sizer)
        self.start_time_ctrl = time_helper.addLabeledControl(
            _("Start Time:"), wx.TextCtrl, value=""
        )
        self.end_time_ctrl = time_helper.addLabeledControl(
            _("End Time:"), wx.TextCtrl, value=""
        )
        main_sizer.Add(time_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Duration and preview
        preview_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.duration_label = wx.StaticText(self, label=_("File Duration: Calculating..."))
        preview_sizer.Add(self.duration_label, 1, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.preview_btn = wx.Button(self, label=_("Preview"))
        self.preview_btn.Bind(wx.EVT_BUTTON, self.on_preview)
        preview_sizer.Add(self.preview_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        main_sizer.Add(preview_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Output filename
        output_sizer = wx.BoxSizer(wx.HORIZONTAL)
        output_label = wx.StaticText(self, label=_("Output Filename:"))
        output_sizer.Add(output_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.output_text = wx.TextCtrl(self, value=os.path.splitext(os.path.basename(self.selected_file))[0] + "_trimmed")
        output_sizer.Add(self.output_text, 1, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(output_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Format
        format_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Output Format"))
        self.mp3_radio = wx.RadioButton(self, label="MP3", style=wx.RB_GROUP)
        self.wav_radio = wx.RadioButton(self, label="WAV")
        format_sizer.Add(self.mp3_radio, 0, wx.ALL, 5)
        format_sizer.Add(self.wav_radio, 0, wx.ALL, 5)
        main_sizer.Add(format_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Quality (for MP3)
        quality_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("MP3 Quality"))
        choices = ["320 kbps", "256 kbps", "192 kbps", "128 kbps"]
        self.quality_ctrl = wx.ComboBox(self, choices=choices, style=wx.CB_READONLY)
        self.quality_ctrl.SetStringSelection("320 kbps")
        quality_sizer.Add(self.quality_ctrl, 0, wx.ALL, 5)
        main_sizer.Add(quality_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Buttons
        btn_sizer = wx.StdDialogButtonSizer()
        trim_btn = wx.Button(self, wx.ID_OK, label=_("Trim"))
        btn_sizer.AddButton(trim_btn)
        cancel_btn = wx.Button(self, wx.ID_CANCEL, label=_("Cancel"))
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        self.SetSizer(main_sizer)
        self.Fit()
        # Bind events
        self.mp3_radio.Bind(wx.EVT_RADIOBUTTON, self.on_format_change)
        self.wav_radio.Bind(wx.EVT_RADIOBUTTON, self.on_format_change)
        self.start_time_ctrl.Bind(wx.EVT_TEXT, self.on_time_control_text)
        self.end_time_ctrl.Bind(wx.EVT_TEXT, self.on_time_control_text)
        # Load last format and quality
        config_data = self.load_config()
        last_format = config_data.get("TrimLastFormat", "mp3")
        last_quality = config_data.get("TrimLastQuality", "320")
        if last_format == "wav":
            self.wav_radio.SetValue(True)
        else:
            self.mp3_radio.SetValue(True)
        self.quality_ctrl.SetStringSelection(f"{last_quality} kbps")
        self.on_format_change(None)
        
    def on_format_change(self, event):
        """Enable/disable quality control based on format selection."""
        self.quality_ctrl.Enable(self.mp3_radio.GetValue())
        
    def on_time_control_text(self, event):
        """Update duration label when time controls change."""
        wx.CallAfter(self.update_duration_label)
        event.Skip()
        
    def get_file_duration(self):
        """
        Uses ffprobe.exe to get the duration of the selected media file.
        """
        ffprobe_path = os.path.join(self.tools_path, "ffprobe.exe")
        if not os.path.exists(ffprobe_path):
            wx.CallAfter(wx.MessageBox, _("ffprobe.exe not found"), _("Error"), wx.OK | wx.ICON_ERROR)
            return
        cmd = [
            ffprobe_path,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            self.selected_file,
        ]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                encoding='utf-8',
                errors='ignore'
            )
            if result.returncode == 0:
                duration_sec = float(result.stdout.strip())
                self.file_duration_seconds = duration_sec
                hours = int(duration_sec // 3600)
                minutes = int((duration_sec % 3600) // 60)
                seconds = int(duration_sec % 60)
                self.file_duration = f"{hours}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes}:{seconds:02d}"
                # Set end time to file duration if not set
                if not self.end_time_ctrl.GetValue():
                    self.end_time_ctrl.SetValue(self.file_duration)
                # Cache duration
                config_data = self.load_config()
                config_data["TrimLastFile"] = self.selected_file
                config_data["TrimLastDuration"] = self.file_duration
                self.save_config(config_data)
                wx.CallAfter(self.update_duration_label)
            else:
                raise RuntimeError(result.stderr)
        except Exception as e:
            logging.error(f"Failed to get file duration: {str(e)}")
            wx.CallAfter(
                wx.MessageBox,
                _("Failed to get file duration: {}").format(str(e)),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
            )
            
    def update_duration_label(self):
        """Updates the GUI label with the calculated file duration and period."""
        if not self or not self.IsShown():
            return
        if self.file_duration_seconds > 0:
            duration_str = self.file_duration
        else:
            duration_str = _("unknown")
        try:
            start_seconds = self.time_to_seconds(self.start_time_ctrl.GetValue()) if self.start_time_ctrl.GetValue() else 0
            end_seconds = self.time_to_seconds(self.end_time_ctrl.GetValue()) if self.end_time_ctrl.GetValue() else self.file_duration_seconds
            period = end_seconds - start_seconds
            if period < 0:
                period_str = _("invalid (negative)")
            else:
                period_str = f"{period:.2f}"
        except (ValueError, IndexError):
            period_str = _("invalid")
        label = _("File duration: {duration}; Trim period: {period} seconds").format(
            duration=duration_str,
            period=period_str
        )
        self.duration_label.SetLabel(label)
        
    def on_preview(self, event):
        """Preview the selected file from start time to end time."""
        start_time = self.start_time_ctrl.GetValue() or "0"
        end_time = self.end_time_ctrl.GetValue() or self.file_duration
        if not self.validate_time_format(start_time) or not self.validate_time_format(end_time):
            wx.MessageBox(_("Invalid time format. Use seconds (e.g., 10), MM:SS (e.g., 1:30), or HH:MM:SS (e.g., 1:30:45)"), _("Error"), wx.OK | wx.ICON_ERROR)
            return
        try:
            start_seconds = self.time_to_seconds(start_time)
            end_seconds = self.time_to_seconds(end_time)
            if start_seconds >= end_seconds:
                wx.MessageBox(_("Start time must be less than end time"), _("Error"), wx.OK | wx.ICON_ERROR)
                return
            if end_seconds > self.file_duration_seconds:
                wx.MessageBox(_("End time cannot be greater than file duration"), _("Error"), wx.OK | wx.ICON_ERROR)
                return
        except ValueError:
            wx.MessageBox(_("Invalid time values"), _("Error"), wx.OK | wx.ICON_ERROR)
            return
        ffmpeg_path = os.path.join(self.tools_path, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_path):
            wx.MessageBox(_("ffmpeg.exe not found"), _("Error"), wx.OK | wx.ICON_ERROR)
            return
        # Clean up existing temp file before creating a new one
        self.cleanup_temp_file()
        # Create temporary file for preview
        self.temp_preview_file = os.path.join(self.output_path, f"temp_preview_{os.path.splitext(os.path.basename(self.selected_file))[0]}.mp3")
        cmd = [
            ffmpeg_path,
            "-y",  # Overwrite output file if exists
            "-i", self.selected_file,
            "-ss", str(self.time_to_seconds(start_time)),
            "-to", str(self.time_to_seconds(end_time)),
            "-c:a", "mp3",
            "-b:a", "192k",
            self.temp_preview_file,
        ]
        def run_preview():
            try:
                logging.debug(f"Running FFmpeg command: {' '.join(cmd)}")
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    encoding='utf-8',
                    errors='ignore'
                )
                if result.returncode == 0:
                    logging.debug(f"FFmpeg completed successfully, checking file: {self.temp_preview_file}")
                    # Wait to ensure file is written
                    time.sleep(1.0)
                    if os.path.exists(self.temp_preview_file) and os.path.getsize(self.temp_preview_file) > 0:
                        logging.debug(f"Preview file created: {self.temp_preview_file}, size: {os.path.getsize(self.temp_preview_file)} bytes")
                        try:
                            # Use os.startfile to open with default player
                            os.startfile(self.temp_preview_file)
                            logging.debug("Preview played with system default player")
                        except Exception as e:
                            logging.error(f"System player failed: {str(e)}")
                            wx.CallAfter(
                                wx.MessageBox,
                                _("Could not play preview with system player: {}").format(str(e)),
                                _("Error"),
                                wx.OK | wx.ICON_ERROR,
                            )
                    else:
                        logging.error(f"Preview file not created or empty: {self.temp_preview_file}")
                        wx.CallAfter(
                            wx.MessageBox,
                            _("Preview file was not created or is empty"),
                            _("Error"),
                            wx.OK | wx.ICON_ERROR,
                        )
                else:
                    logging.error(f"FFmpeg failed: {result.stderr}")
                    wx.CallAfter(
                        wx.MessageBox,
                        _("Preview failed: {}").format(result.stderr),
                        _("Error"),
                        wx.OK | wx.ICON_ERROR,
                    )
            except Exception as e:
                logging.error(f"Preview process failed: {str(e)}")
                wx.CallAfter(
                    wx.MessageBox,
                    _("Preview failed: {}").format(str(e)),
                    _("Error"),
                    wx.OK | wx.ICON_ERROR,
                )
        threading.Thread(target=run_preview, daemon=True).start()
        
    def on_trim(self, event):
        """Handles the trimming process using FFmpeg."""
        self.cleanup_temp_file()
        start_time = self.start_time_ctrl.GetValue() or "0"
        end_time = self.end_time_ctrl.GetValue() or self.file_duration
        output_format = "mp3" if self.mp3_radio.GetValue() else "wav"
        quality_kbps = int(self.quality_ctrl.GetStringSelection().split()[0]) if self.mp3_radio.GetValue() else None
        if not self.validate_time_format(start_time) or not self.validate_time_format(end_time):
            wx.MessageBox(_("Invalid time format. Use seconds (e.e., 10), MM:SS (e.g., 1:30), or HH:MM:SS (e.g., 1:30:45)"), _("Error"), wx.OK | wx.ICON_ERROR)
            return
        # Calculate time in seconds
        try:
            start_seconds = self.time_to_seconds(start_time)
            end_seconds = self.time_to_seconds(end_time)
            if start_seconds >= end_seconds:
                wx.MessageBox(_("Start time must be less than end time"), _("Error"), wx.OK | wx.ICON_ERROR)
                return
            if end_seconds > self.file_duration_seconds:
                wx.MessageBox(_("End time cannot be greater than file duration"), _("Error"), wx.OK | wx.ICON_ERROR)
                return
        except ValueError:
            wx.MessageBox(_("Invalid time values"), _("Error"), wx.OK | wx.ICON_ERROR)
            return
        # Create new unique filename
        output_file_name = self.output_text.GetValue().strip()
        if not output_file_name:
            output_file_name = os.path.splitext(os.path.basename(self.selected_file))[0] + "_trimmed"
        output_file = self.get_unique_filename(output_file_name, output_format)
        output_path = os.path.join(self.output_path, output_file)
        ffmpeg_path = os.path.join(self.tools_path, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_path):
            wx.CallAfter(
                wx.MessageBox,
                _("ffmpeg.exe not found"),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
            )
            return
        # Construct FFmpeg command
        cmd = [
            ffmpeg_path,
            "-y",  # Overwrite output file if exists
            "-i", self.selected_file,
            "-ss", str(self.time_to_seconds(start_time)),
            "-to", str(self.time_to_seconds(end_time)),
        ]
        if output_format == "mp3":
            cmd.extend([
                "-c:a", "libmp3lame",
                "-b:a", f"{quality_kbps}k",
            ])
        else:
            cmd.extend([
                "-c:a", "pcm_s16le",
            ])
        cmd.append(output_path)
        # Run FFmpeg in a background thread
        def run_ffmpeg():
            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    encoding='utf-8',
                    errors='ignore'
                )
                if result.returncode == 0:
                    # Play success tone
                    try:
                        tones.beep(1000, 300)  # High tone for success
                    except Exception:
                        pass
                    wx.CallAfter(
                        wx.MessageBox,
                        _("Trimmed file saved as {}").format(output_path),
                        _("Success"),
                        wx.OK | wx.ICON_INFORMATION,
                    )
                    # Save settings
                    config_data = self.load_config()
                    config_data["TrimLastFormat"] = output_format
                    if quality_kbps:
                        config_data["TrimLastQuality"] = quality_kbps
                    config_data["TrimLastStartTime"] = start_time
                    config_data["TrimLastEndTime"] = end_time
                    config_data["TrimLastFile"] = self.selected_file
                    self.save_config(config_data)
                else:
                    logging.error(f"Trimming failed: {result.stderr}")
                    wx.CallAfter(
                        wx.MessageBox,
                        _("Trimming failed: {}").format(result.stderr),
                        _("Error"),
                        wx.OK | wx.ICON_ERROR,
                    )
            except Exception as e:
                logging.error(f"Trimming process failed: {str(e)}")
                wx.CallAfter(
                    wx.MessageBox,
                    _("Trimming failed: {}").format(str(e)),
                    _("Error"),
                    wx.OK | wx.ICON_ERROR,
                )
        threading.Thread(target=run_ffmpeg, daemon=True).start()
        self.EndModal(wx.ID_OK)
        
    def on_close(self, event):
        """Called when the dialog is closed."""
        self.cleanup_temp_file()
        event.Skip()
        
    def cleanup_temp_file(self):
        """Removes the temporary preview file."""
        if self.temp_preview_file and os.path.exists(self.temp_preview_file):
            try:
                os.remove(self.temp_preview_file)
                logging.debug(f"Temporary file removed: {self.temp_preview_file}")
            except Exception as e:
                logging.error(f"Failed to remove temporary file: {str(e)}")
                ui.message(_("Failed to remove temporary file: {}").format(str(e)))
                
    def validate_time_format(self, time_str):
        """Validates if the time string is in seconds, MM:SS, or HH:MM:SS format."""
        if not time_str:
            return True  # Empty string is valid (will use 0 or file duration)
        pattern = re.compile(r"^((\d+):)?(\d{1,2}):(\d{2})$|^(\d+)$")
        return pattern.match(time_str) is not None
        
    def time_to_seconds(self, time_str):
        """Converts time string to seconds. Supports seconds, MM:SS, or HH:MM:SS."""
        if not time_str:
            return 0
        try:
            parts = time_str.split(":")
            if len(parts) == 1:  # Seconds only (e.g., "10")
                return int(parts[0])
            elif len(parts) == 2:  # MM:SS (e.g., "1:30")
                minutes, seconds = map(int, parts)
                return minutes * 60 + seconds
            elif len(parts) == 3:  # HH:MM:SS (e.g., "1:30:45")
                hours, minutes, seconds = map(int, parts)
                return hours * 3600 + minutes * 60 + seconds
            else:
                raise ValueError("Invalid time format")
        except ValueError:
            raise ValueError("Invalid time values")
            
    def get_unique_filename(self, base_name, extension):
        """Generates a unique filename by appending a number if the file already exists."""
        counter = 1
        output_file = f"{base_name}.{extension}"
        while os.path.exists(os.path.join(self.output_path, output_file)):
            output_file = f"{base_name}_{counter}.{extension}"
            counter += 1
        return output_file
