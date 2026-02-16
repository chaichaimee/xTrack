# convertVideo.py

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
from .xTrackCore import load_config, save_config, get_file_duration, get_file_size
import addonHandler

addonHandler.initTranslation()

class ConvertVideoDialog(wx.Dialog):
    """Dialog for converting various video formats to different output formats with quality preservation."""
    def __init__(self, parent, selected_files, tools_path):
        super().__init__(parent, title=_("Convert Video"))
        self.selected_files = selected_files
        self.tools_path = tools_path
        self.current_file_index = 0
        self.file_duration_seconds = 0
        self.file_size_bytes = 0
        self.output_path = os.path.dirname(self.selected_files[0]) if self.selected_files else os.getcwd()
        self.config_path = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "nvda", "config", "xTrack.json")
        self.ffmpeg_process = None
        self.is_paused = False
        self.conversion_queue = queue.Queue()
        self.currently_processing = False
        self.file_durations = {}
        self.file_sizes = {}
        self.init_ui()
        self.SetTitle(_("Convert Video: {} files").format(len(self.selected_files)))
        self.load_settings()
        self.Bind(wx.EVT_CLOSE, self.on_close)
        threading.Thread(target=self.load_file_info, daemon=True).start()

    def load_file_info(self):
        """Load durations and sizes for all selected files."""
        for file_path in self.selected_files:
            duration_sec, duration_str = get_file_duration(self.tools_path, file_path)
            size_bytes, size_str = get_file_size(file_path)
            self.file_durations[file_path] = self.format_duration(duration_sec)
            self.file_sizes[file_path] = size_str
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
        
        file_info_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.duration_label = wx.StaticText(self, label=_("Duration: Calculating..."))
        file_info_sizer.Add(self.duration_label, 1, wx.EXPAND | wx.ALL, 5)
        self.size_label = wx.StaticText(self, label=_("Size: Calculating..."))
        file_info_sizer.Add(self.size_label, 1, wx.EXPAND | wx.ALL, 5)
        current_file_sizer.Add(file_info_sizer, 0, wx.EXPAND)
        
        main_sizer.Add(current_file_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Settings section
        settings_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Conversion Settings"))
        
        # Output format setting
        format_sizer = wx.BoxSizer(wx.HORIZONTAL)
        format_label = wx.StaticText(self, label=_("Output Format:"))
        format_sizer.Add(format_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        format_choices = ["MP4 (Copy Original Quality)", "MKV (Copy Original Quality)", "MOV (Copy Original Quality)", "AVI (Copy Original Quality)", "WebM (Copy Original Quality)"]
        self.format_ctrl = wx.ComboBox(self, choices=format_choices, style=wx.CB_READONLY)
        self.format_ctrl.SetStringSelection("MP4 (Copy Original Quality)")
        format_sizer.Add(self.format_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        settings_sizer.Add(format_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Audio settings for video
        video_audio_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Audio Settings"))
        
        # Sample rate selection
        sample_rate_sizer = wx.BoxSizer(wx.HORIZONTAL)
        sample_rate_label = wx.StaticText(self, label=_("Sample Rate:"))
        sample_rate_sizer.Add(sample_rate_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        sample_rate_choices = ["44100 Hz", "48000 Hz", "Keep Original"]
        self.sample_rate_ctrl = wx.ComboBox(self, choices=sample_rate_choices, style=wx.CB_READONLY)
        self.sample_rate_ctrl.SetStringSelection("Keep Original")
        sample_rate_sizer.Add(self.sample_rate_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        video_audio_sizer.Add(sample_rate_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Audio channels
        channels_sizer = wx.BoxSizer(wx.HORIZONTAL)
        channels_label = wx.StaticText(self, label=_("Audio Channels:"))
        channels_sizer.Add(channels_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        channels_choices = ["Stereo (2 channels)", "Mono (1 channel)", "Keep Original"]
        self.channels_ctrl = wx.ComboBox(self, choices=channels_choices, style=wx.CB_READONLY)
        self.channels_ctrl.SetStringSelection("Keep Original")
        channels_sizer.Add(self.channels_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        video_audio_sizer.Add(channels_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Audio codec selection
        codec_sizer = wx.BoxSizer(wx.HORIZONTAL)
        codec_label = wx.StaticText(self, label=_("Audio Codec:"))
        codec_sizer.Add(codec_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        codec_choices = ["AAC (Recommended)", "MP3", "Vorbis", "Keep Original"]
        self.audio_codec_ctrl = wx.ComboBox(self, choices=codec_choices, style=wx.CB_READONLY)
        self.audio_codec_ctrl.SetStringSelection("AAC (Recommended)")
        codec_sizer.Add(self.audio_codec_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        video_audio_sizer.Add(codec_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        settings_sizer.Add(video_audio_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Quality preservation note
        quality_note = wx.StaticText(self, label=_("Note: Video conversion preserves original quality by default. No re-encoding is performed."))
        quality_note.Wrap(400)
        settings_sizer.Add(quality_note, 0, wx.EXPAND | wx.ALL, 5)
        
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
        self.convert_btn.Bind(wx.EVT_BUTTON, self.on_convert)
        btn_sizer.AddButton(self.convert_btn)
        
        self.cancel_btn = wx.Button(self, wx.ID_CANCEL, label=_("Cancel"))
        self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
        btn_sizer.AddButton(self.cancel_btn)
        
        btn_sizer.Realize()
        main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        self.SetSizer(main_sizer)
        self.Fit()
        
        # Update current file info
        if self.selected_files:
            self.update_current_file_info(0)

    def get_file_display_name(self, file_path):
        """Get display name with duration and size for listbox."""
        base_name = os.path.basename(file_path)
        duration = self.file_durations.get(file_path, _("Calculating..."))
        size = self.file_sizes.get(file_path, _("Calculating..."))
        return f"{base_name} ({duration}, {size})"

    def update_file_listbox(self):
        """Update file listbox with names, durations and sizes."""
        self.file_listbox.Set([self.get_file_display_name(f) for f in self.selected_files])

    def update_current_file_info(self, index):
        if index < len(self.selected_files):
            self.current_file_index = index
            current_file = self.selected_files[index]
            self.current_file_label.SetLabel(os.path.basename(current_file))
            self.file_listbox.SetSelection(index)
            duration = self.file_durations.get(current_file, _("Calculating..."))
            size = self.file_sizes.get(current_file, _("Calculating..."))
            self.duration_label.SetLabel(_("Duration: {}").format(duration))
            self.size_label.SetLabel(_("Size: {}").format(size))
            self.file_duration_seconds = self.get_duration_seconds(current_file)
            self.file_size_bytes = self.get_size_bytes(current_file)

    def get_duration_seconds(self, file_path):
        """Get duration in seconds for a file."""
        duration_sec, _ = get_file_duration(self.tools_path, file_path)
        return duration_sec

    def get_size_bytes(self, file_path):
        """Get file size in bytes."""
        size_bytes, _ = get_file_size(file_path)
        return size_bytes

    def load_settings(self):
        config_data = load_config(self.config_path)
        self.format_ctrl.SetStringSelection(config_data.get("ConvertVideoFormat", "MP4 (Copy Original Quality)"))
        self.sample_rate_ctrl.SetStringSelection(config_data.get("ConvertVideoSampleRate", "Keep Original"))
        self.channels_ctrl.SetStringSelection(config_data.get("ConvertVideoChannels", "Keep Original"))
        self.audio_codec_ctrl.SetStringSelection(config_data.get("ConvertVideoAudioCodec", "AAC (Recommended)"))

    def save_settings(self):
        config_data = load_config(self.config_path)
        config_data["ConvertVideoFormat"] = self.format_ctrl.GetStringSelection()
        config_data["ConvertVideoSampleRate"] = self.sample_rate_ctrl.GetStringSelection()
        config_data["ConvertVideoChannels"] = self.channels_ctrl.GetStringSelection()
        config_data["ConvertVideoAudioCodec"] = self.audio_codec_ctrl.GetStringSelection()
        save_config(self.config_path, config_data)

    def on_convert(self, event):
        self.save_settings()
        
        # Play start tone
        try:
            tones.beep(800, 200)
        except Exception:
            pass
        
        # Add all files to queue
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
        
        format_selection = self.format_ctrl.GetStringSelection()
        sample_rate = self.sample_rate_ctrl.GetStringSelection().replace(" Hz", "")
        channels_selection = self.channels_ctrl.GetStringSelection()
        audio_codec_selection = self.audio_codec_ctrl.GetStringSelection()
        
        # Map format selection to file extension
        format_map = {
            "MP4 (Copy Original Quality)": "mp4",
            "MKV (Copy Original Quality)": "mkv",
            "MOV (Copy Original Quality)": "mov",
            "AVI (Copy Original Quality)": "avi",
            "WebM (Copy Original Quality)": "webm"
        }
        output_format = format_map.get(format_selection, "mp4")
        
        # Map channels selection to ffmpeg parameter
        if channels_selection == "Stereo (2 channels)":
            audio_channels = "2"
        elif channels_selection == "Mono (1 channel)":
            audio_channels = "1"
        else:  # "Keep Original"
            audio_channels = None
            
        # Map audio codec selection
        if audio_codec_selection == "AAC (Recommended)":
            audio_codec = "aac"
        elif audio_codec_selection == "MP3":
            audio_codec = "libmp3lame"
        elif audio_codec_selection == "Vorbis":
            audio_codec = "libvorbis"
        else:  # "Keep Original"
            audio_codec = None
        
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        output_file = f"{base_name}.{output_format}"
        output_path = os.path.join(self.output_path, output_file)
        
        ffmpeg_path = os.path.join(self.tools_path, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_path):
            ui.message(_("ffmpeg.exe not found"))
            return
        
        # Build ffmpeg command - use copy for video and handle audio based on settings
        cmd = [
            ffmpeg_path,
            "-i", file_path,
            "-c:v", "copy",  # Copy video stream without re-encoding
            "-progress", "pipe:1",
            "-nostats",
            "-y",
        ]
        
        # Handle audio settings
        if audio_codec is not None:
            cmd.extend(["-c:a", audio_codec])
            # Set audio bitrate for certain codecs
            if audio_codec == "aac":
                cmd.extend(["-b:a", "192k"])
            elif audio_codec == "libmp3lame":
                cmd.extend(["-b:a", "192k"])
        else:
            cmd.extend(["-c:a", "copy"])  # Copy original audio
        
        # Set sample rate if specified and not keeping original
        if sample_rate != "Keep Original" and audio_codec is not None:
            cmd.extend(["-ar", sample_rate])
        
        # Set audio channels if specified and not keeping original
        if audio_channels is not None and audio_codec is not None:
            cmd.extend(["-ac", audio_channels])
        
        cmd.append(output_path)
        
        wx.CallAfter(self.convert_btn.Enable, False)
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
                
                # Read progress from ffmpeg
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
                                progress = min(100, max(0, progress))  # Clamp between 0-100
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
        # Announce progress for screen readers every 10%
        if progress % 10 == 0:
            ui.message(_("{}%").format(progress))

    def on_success(self):
        self.progress_bar.SetValue(100)
        self.status_label.SetLabel(_("Conversion complete!"))
        try:
            tones.beep(1000, 300)  # Success tone
        except Exception:
            pass
        ui.message(_("Conversion complete for {}").format(os.path.basename(self.selected_files[self.current_file_index])))
        wx.CallAfter(self.convert_btn.Enable, True)
        wx.CallAfter(self.cancel_btn.Enable, True)

    def on_all_conversions_complete(self):
        self.convert_btn.Enable(True)
        self.cancel_btn.Enable(True)
        self.status_label.SetLabel(_("All conversions complete!"))
        ui.message(_("All conversions complete!"))
        self.EndModal(wx.ID_OK)

    def on_failure(self, error_message):
        self.status_label.SetLabel(_("Conversion failed."))
        ui.message(_("Conversion failed: {}").format(error_message))
        self.convert_btn.Enable(True)
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
