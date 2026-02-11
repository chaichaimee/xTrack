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
from .xTrackCore import load_config, save_config, get_file_duration
import addonHandler

addonHandler.initTranslation()

class ConvertAudioDialog(wx.Dialog):
    """Dialog for converting various audio/video formats to MP3 or WAV, including same-format re-encoding."""
    def __init__(self, parent, selected_files, tools_path):
        super().__init__(parent, title=_("Convert Audio"))
        self.selected_files = selected_files
        self.tools_path = tools_path
        self.current_file_index = 0
        self.file_duration_seconds = 0
        self.output_path = os.path.dirname(self.selected_files[0]) if self.selected_files else os.getcwd()
        self.config_path = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "nvda", "config", "xTrack.json")
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
            duration_sec, duration_str = get_file_duration(self.tools_path, file_path)
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
        format_choices = ["MP3", "WAV"]
        self.format_ctrl = wx.ComboBox(self, choices=format_choices, style=wx.CB_READONLY)
        self.format_ctrl.SetStringSelection("MP3")
        format_sizer.Add(self.format_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        settings_sizer.Add(format_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Quality setting
        quality_sizer = wx.BoxSizer(wx.HORIZONTAL)
        quality_label = wx.StaticText(self, label=_("MP3 Quality:"))
        quality_sizer.Add(quality_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        quality_choices = ["320 kbps", "256 kbps", "192 kbps", "128 kbps"]
        self.quality_ctrl = wx.ComboBox(self, choices=quality_choices, style=wx.CB_READONLY)
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

    def on_format_change(self, event):
        """Enable/disable quality control based on format selection."""
        self.quality_ctrl.Enable(self.format_ctrl.GetStringSelection() == "MP3")

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
        duration_sec, _ = get_file_duration(self.tools_path, file_path)
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
            duration_sec, duration_str = get_file_duration(self.tools_path, current_file)
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
        quality_kbps = self.quality_ctrl.GetStringSelection().split()[0] if output_format == "mp3" else None
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
        
        ffmpeg_path = os.path.join(self.tools_path, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_path):
            ui.message(_("ffmpeg.exe not found"))
            return
        
        # Build command based on format
        cmd = [
            ffmpeg_path,
            "-i", file_path,
            "-vn",  # No video
            "-ar", samplerate_str,
            "-af", f"volume={volume_percent}",
            "-progress", "pipe:1",
            "-nostats",
            "-y",  # Overwrite output file if exists
        ]
        
        if output_format == "mp3":
            cmd.extend([
                "-c:a", "libmp3lame",
                "-b:a", f"{quality_kbps}k",
            ])
        else:  # WAV
            cmd.extend([
                "-c:a", "pcm_s16le",
            ])
        
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
