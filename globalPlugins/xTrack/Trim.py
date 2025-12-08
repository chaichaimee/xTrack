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
from logHandler import log
import addonHandler

addonHandler.initTranslation()

class TrimAudioVideoDialog(wx.Dialog):
    """Dialog for trimming audio and video files using FFmpeg."""
    def __init__(self, parent, selected_files, tools_path):
        super().__init__(parent, title=_("Trim Audio/Video File"))
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
        self.SetTitle(_("Trim Audio/Video: {}").format(os.path.basename(self.selected_file)))
        
        # Always reset end time to empty to force recalculation
        self.end_time_ctrl.SetValue("")
        
        threading.Thread(target=self.get_file_duration, daemon=True).start()
        self.Bind(wx.EVT_CLOSE, self.on_close)
        
    def load_config(self):
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            log.error(f"Failed to load configuration: {str(e)}")
            ui.message(_("Failed to load configuration: {}").format(str(e)))
            return {}
            
    def save_config(self, config_data):
        try:
            config_dir = os.path.dirname(self.config_path)
            if not os.path.exists(config_dir):
                os.makedirs(config_dir)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4)
        except Exception as e:
            log.error(f"Failed to save configuration: {str(e)}")
            ui.message(_("Failed to save configuration: {}").format(str(e)))
            
    def init_ui(self):
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        file_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Selected File"))
        self.file_label = wx.StaticText(self, label=os.path.basename(self.selected_file))
        file_sizer.Add(self.file_label, 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(file_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Output Type Selection (Audio/Video)
        type_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Output Type"))
        self.audio_radio = wx.RadioButton(self, label=_("Audio"), style=wx.RB_GROUP)
        self.video_radio = wx.RadioButton(self, label=_("Video"))
        type_sizer.Add(self.audio_radio, 0, wx.ALL, 5)
        type_sizer.Add(self.video_radio, 0, wx.ALL, 5)
        main_sizer.Add(type_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        time_sizer = wx.BoxSizer(wx.VERTICAL)
        time_helper = guiHelper.BoxSizerHelper(self, sizer=time_sizer)
        self.start_time_ctrl = time_helper.addLabeledControl(_("Start Time:"), wx.TextCtrl, value="")
        self.end_time_ctrl = time_helper.addLabeledControl(_("End Time:"), wx.TextCtrl, value="")
        main_sizer.Add(time_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Audio Settings Panel
        self.audio_panel = wx.Panel(self)
        audio_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Audio Format Selection
        audio_format_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.audio_panel, label=_("Audio Format"))
        self.mp3_radio = wx.RadioButton(self.audio_panel, label="MP3", style=wx.RB_GROUP)
        self.wav_radio = wx.RadioButton(self.audio_panel, label="WAV")
        audio_format_sizer.Add(self.mp3_radio, 0, wx.ALL, 5)
        audio_format_sizer.Add(self.wav_radio, 0, wx.ALL, 5)
        audio_sizer.Add(audio_format_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Audio Quality
        audio_quality_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.audio_panel, label=_("Audio Quality"))
        choices = ["320 kbps", "256 kbps", "192 kbps", "128 kbps"]
        self.audio_quality_ctrl = wx.ComboBox(self.audio_panel, choices=choices, style=wx.CB_READONLY)
        self.audio_quality_ctrl.SetStringSelection("320 kbps")
        audio_quality_sizer.Add(self.audio_quality_ctrl, 0, wx.ALL, 5)
        audio_sizer.Add(audio_quality_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Fade Settings for Audio
        fade_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.audio_panel, label=_("Fade Settings"))
        
        fade_in_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.audio_panel, label=_("Fade In"))
        fade_in_grid = wx.FlexGridSizer(2, 2, 5, 5)
        fade_in_grid.Add(wx.StaticText(self.audio_panel, label=_("Start Time:")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.fade_in_start_ctrl = wx.TextCtrl(self.audio_panel, value="0")
        fade_in_grid.Add(self.fade_in_start_ctrl, 1, wx.EXPAND)
        fade_in_grid.Add(wx.StaticText(self.audio_panel, label=_("End Time (100% volume):")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.fade_in_end_ctrl = wx.TextCtrl(self.audio_panel, value="3")
        fade_in_grid.Add(self.fade_in_end_ctrl, 1, wx.EXPAND)
        fade_in_grid.AddGrowableCol(1)
        fade_in_sizer.Add(fade_in_grid, 1, wx.EXPAND | wx.ALL, 5)
        fade_sizer.Add(fade_in_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        fade_out_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.audio_panel, label=_("Fade Out"))
        fade_out_grid = wx.FlexGridSizer(2, 2, 5, 5)
        fade_out_grid.Add(wx.StaticText(self.audio_panel, label=_("Start Time (100% volume):")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.fade_out_start_ctrl = wx.TextCtrl(self.audio_panel)
        fade_out_grid.Add(self.fade_out_start_ctrl, 1, wx.EXPAND)
        fade_out_grid.Add(wx.StaticText(self.audio_panel, label=_("End Time:")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.fade_out_end_ctrl = wx.TextCtrl(self.audio_panel)
        fade_out_grid.Add(self.fade_out_end_ctrl, 1, wx.EXPAND)
        fade_out_grid.AddGrowableCol(1)
        fade_out_sizer.Add(fade_out_grid, 1, wx.EXPAND | wx.ALL, 5)
        fade_sizer.Add(fade_out_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        audio_sizer.Add(fade_sizer, 0, wx.EXPAND | wx.ALL, 5)
        self.audio_panel.SetSizer(audio_sizer)
        main_sizer.Add(self.audio_panel, 0, wx.EXPAND | wx.ALL, 5)
        
        # Video Settings Panel
        self.video_panel = wx.Panel(self)
        video_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Video Format Selection
        video_format_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.video_panel, label=_("Video Format"))
        video_format_choices = ["MP4 (Copy Original Quality)", "MKV (Copy Original Quality)", "MOV (Copy Original Quality)", "AVI (Copy Original Quality)", "WebM (Copy Original Quality)"]
        self.video_format_ctrl = wx.ComboBox(self.video_panel, choices=video_format_choices, style=wx.CB_READONLY)
        self.video_format_ctrl.SetStringSelection("MP4 (Copy Original Quality)")
        video_format_sizer.Add(self.video_format_ctrl, 0, wx.ALL, 5)
        video_sizer.Add(video_format_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Audio settings for video
        video_audio_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.video_panel, label=_("Audio Settings"))
        
        # Sample rate selection
        sample_rate_sizer = wx.BoxSizer(wx.HORIZONTAL)
        sample_rate_label = wx.StaticText(self.video_panel, label=_("Sample Rate:"))
        sample_rate_sizer.Add(sample_rate_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        sample_rate_choices = ["44100 Hz", "48000 Hz", "Keep Original"]
        self.sample_rate_ctrl = wx.ComboBox(self.video_panel, choices=sample_rate_choices, style=wx.CB_READONLY)
        self.sample_rate_ctrl.SetStringSelection("Keep Original")
        sample_rate_sizer.Add(self.sample_rate_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        video_audio_sizer.Add(sample_rate_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Audio channels
        channels_sizer = wx.BoxSizer(wx.HORIZONTAL)
        channels_label = wx.StaticText(self.video_panel, label=_("Audio Channels:"))
        channels_sizer.Add(channels_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        channels_choices = ["Stereo (2 channels)", "Mono (1 channel)", "Keep Original"]
        self.channels_ctrl = wx.ComboBox(self.video_panel, choices=channels_choices, style=wx.CB_READONLY)
        self.channels_ctrl.SetStringSelection("Keep Original")
        channels_sizer.Add(self.channels_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        video_audio_sizer.Add(channels_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Audio codec selection
        codec_sizer = wx.BoxSizer(wx.HORIZONTAL)
        codec_label = wx.StaticText(self.video_panel, label=_("Audio Codec:"))
        codec_sizer.Add(codec_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        codec_choices = ["AAC (Recommended)", "MP3", "Vorbis", "Keep Original"]
        self.audio_codec_ctrl = wx.ComboBox(self.video_panel, choices=codec_choices, style=wx.CB_READONLY)
        self.audio_codec_ctrl.SetStringSelection("AAC (Recommended)")
        codec_sizer.Add(self.audio_codec_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        video_audio_sizer.Add(codec_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        video_sizer.Add(video_audio_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Quality preservation note
        quality_note = wx.StaticText(self.video_panel, label=_("Note: Video trimming preserves original quality by default. No re-encoding is performed."))
        quality_note.Wrap(400)
        video_sizer.Add(quality_note, 0, wx.EXPAND | wx.ALL, 5)
        
        self.video_panel.SetSizer(video_sizer)
        main_sizer.Add(self.video_panel, 0, wx.EXPAND | wx.ALL, 5)
        self.video_panel.Hide()
        
        # Output filename section - moved before preview section
        output_sizer = wx.BoxSizer(wx.HORIZONTAL)
        output_label = wx.StaticText(self, label=_("Output Filename:"))
        output_sizer.Add(output_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.output_text = wx.TextCtrl(self, value=os.path.splitext(os.path.basename(self.selected_file))[0] + "_trimmed")
        output_sizer.Add(self.output_text, 1, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(output_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Preview section - moved after output filename
        preview_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.duration_label = wx.StaticText(self, label=_("File Duration: Calculating..."))
        preview_sizer.Add(self.duration_label, 1, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.preview_btn = wx.Button(self, label=_("Preview"))
        self.preview_btn.Bind(wx.EVT_BUTTON, self.on_preview)
        preview_sizer.Add(self.preview_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        main_sizer.Add(preview_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        self.progress_bar = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
        main_sizer.Add(self.progress_bar, 0, wx.EXPAND | wx.ALL, 5)
        
        self.status_label = wx.StaticText(self, label="")
        main_sizer.Add(self.status_label, 0, wx.EXPAND | wx.ALL, 5)
        
        btn_sizer = wx.StdDialogButtonSizer()
        self.trim_btn = wx.Button(self, wx.ID_OK, label=_("Trim"))
        self.trim_btn.Bind(wx.EVT_BUTTON, self.on_trim)
        btn_sizer.AddButton(self.trim_btn)
        self.cancel_btn = wx.Button(self, wx.ID_CANCEL, label=_("Cancel"))
        self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
        btn_sizer.AddButton(self.cancel_btn)
        btn_sizer.Realize()
        main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        self.SetSizer(main_sizer)
        self.Fit()
        
        # Bind events
        self.audio_radio.Bind(wx.EVT_RADIOBUTTON, self.on_output_type_change)
        self.video_radio.Bind(wx.EVT_RADIOBUTTON, self.on_output_type_change)
        self.mp3_radio.Bind(wx.EVT_RADIOBUTTON, self.on_format_change)
        self.wav_radio.Bind(wx.EVT_RADIOBUTTON, self.on_format_change)
        self.start_time_ctrl.Bind(wx.EVT_TEXT, self.on_time_control_text)
        self.end_time_ctrl.Bind(wx.EVT_TEXT, self.on_time_control_text)
        self.fade_in_start_ctrl.Bind(wx.EVT_TEXT, self.on_fade_text)
        self.fade_in_end_ctrl.Bind(wx.EVT_TEXT, self.on_fade_text)
        self.fade_out_start_ctrl.Bind(wx.EVT_TEXT, self.on_fade_text)
        self.fade_out_end_ctrl.Bind(wx.EVT_TEXT, self.on_fade_text)
        
        config_data = self.load_config()
        last_output_type = config_data.get("TrimLastOutputType", "audio")
        last_format = config_data.get("TrimLastFormat", "mp3")
        last_quality = config_data.get("TrimLastQuality", "320")
        last_video_format = config_data.get("TrimLastVideoFormat", "MP4 (Copy Original Quality)")
        last_sample_rate = config_data.get("TrimLastSampleRate", "Keep Original")
        last_channels = config_data.get("TrimLastChannels", "Keep Original")
        last_audio_codec = config_data.get("TrimLastAudioCodec", "AAC (Recommended)")
        
        if last_output_type == "video":
            self.video_radio.SetValue(True)
            self.video_format_ctrl.SetStringSelection(last_video_format)
            self.sample_rate_ctrl.SetStringSelection(last_sample_rate)
            self.channels_ctrl.SetStringSelection(last_channels)
            self.audio_codec_ctrl.SetStringSelection(last_audio_codec)
        else:
            self.audio_radio.SetValue(True)
            if last_format == "wav":
                self.wav_radio.SetValue(True)
            else:
                self.mp3_radio.SetValue(True)
            self.audio_quality_ctrl.SetStringSelection(f"{last_quality} kbps")
        
        self.on_output_type_change(None)
        self.on_format_change(None)
        
    def on_output_type_change(self, event):
        """Show/hide audio and video panels based on output type selection."""
        is_audio = self.audio_radio.GetValue()
        self.audio_panel.Show(is_audio)
        self.video_panel.Show(not is_audio)
        self.Layout()
        self.Fit()
        
    def on_format_change(self, event):
        self.audio_quality_ctrl.Enable(self.mp3_radio.GetValue())
        
    def on_time_control_text(self, event):
        wx.CallAfter(self.update_duration_label)
        event.Skip()
        
    def on_fade_text(self, event):
        wx.CallAfter(self.update_duration_label)
        event.Skip()
        
    def get_file_duration(self):
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
                # Always set end time to file duration, don't use saved values
                wx.CallAfter(self.end_time_ctrl.SetValue, self.file_duration)
                config_data = self.load_config()
                config_data["TrimLastFile"] = self.selected_file
                config_data["TrimLastDuration"] = self.file_duration
                self.save_config(config_data)
                wx.CallAfter(self.update_duration_label)
            else:
                raise RuntimeError(result.stderr)
        except Exception as e:
            log.error(f"Failed to get file duration: {str(e)}")
            wx.CallAfter(
                wx.MessageBox,
                _("Failed to get file duration: {}").format(str(e)),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
            )
            
    def update_duration_label(self):
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
                
            fade_info = ""
            if self.audio_radio.GetValue():
                fade_in_start = self.time_to_seconds(self.fade_in_start_ctrl.GetValue()) if self.fade_in_start_ctrl.GetValue() else None
                fade_in_end = self.time_to_seconds(self.fade_in_end_ctrl.GetValue()) if self.fade_in_end_ctrl.GetValue() else None
                fade_out_start = self.time_to_seconds(self.fade_out_start_ctrl.GetValue()) if self.fade_out_start_ctrl.GetValue() else None
                fade_out_end = self.time_to_seconds(self.fade_out_end_ctrl.GetValue()) if self.fade_out_end_ctrl.GetValue() else None
                
                if fade_in_start is not None and fade_in_end is not None and fade_in_start < fade_in_end:
                    fade_info += _(" | Fade In: {} to {}").format(
                        self.fade_in_start_ctrl.GetValue(), self.fade_in_end_ctrl.GetValue()
                    )
                if fade_out_start is not None and fade_out_end is not None and fade_out_start < fade_out_end:
                    fade_info += _(" | Fade Out: {} to {}").format(
                        self.fade_out_start_ctrl.GetValue(), self.fade_out_end_ctrl.GetValue()
                    )
                
        except (ValueError, IndexError):
            period_str = _("invalid")
            fade_info = ""
            
        label = _("File duration: {duration}; Trim period: {period} seconds{fade_info}").format(
            duration=duration_str,
            period=period_str,
            fade_info=fade_info
        )
        self.duration_label.SetLabel(label)
        
    def build_fade_filter(self, fade_in_start, fade_in_end, fade_out_start, fade_out_end, trim_duration):
        filters = []
        if fade_in_start is not None and fade_in_end is not None and fade_in_start < fade_in_end:
            filters.append(f"afade=t=in:st={fade_in_start}:d={fade_in_end - fade_in_start}")
        if fade_out_start is not None and fade_out_end is not None and fade_out_start < fade_out_end:
            fade_out_duration = fade_out_end - fade_out_start
            filters.append(f"afade=t=out:st={fade_out_start}:d={fade_out_duration}")
        return ",".join(filters) if filters else None

    def on_preview(self, event):
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
                
            trim_duration = end_seconds - start_seconds
            
            # Check fade settings only for audio
            if self.audio_radio.GetValue():
                fade_in_start = self.time_to_seconds(self.fade_in_start_ctrl.GetValue()) if self.fade_in_start_ctrl.GetValue() else None
                fade_in_end = self.time_to_seconds(self.fade_in_end_ctrl.GetValue()) if self.fade_in_end_ctrl.GetValue() else None
                fade_out_start = self.time_to_seconds(self.fade_out_start_ctrl.GetValue()) if self.fade_out_start_ctrl.GetValue() else None
                fade_out_end = self.time_to_seconds(self.fade_out_end_ctrl.GetValue()) if self.fade_out_end_ctrl.GetValue() else None
                
                if fade_in_start is not None and fade_in_end is not None:
                    if fade_in_start < start_seconds or fade_in_end > end_seconds or fade_in_start >= fade_in_end:
                        wx.MessageBox(_("Invalid fade-in range"), _("Error"), wx.OK | wx.ICON_ERROR)
                        return
                if fade_out_start is not None and fade_out_end is not None:
                    if fade_out_start < start_seconds or fade_out_end > end_seconds or fade_out_start >= fade_out_end:
                        wx.MessageBox(_("Invalid fade-out range"), _("Error"), wx.OK | wx.ICON_ERROR)
                        return
                    
        except ValueError:
            wx.MessageBox(_("Invalid time values"), _("Error"), wx.OK | wx.ICON_ERROR)
            return
            
        ffmpeg_path = os.path.join(self.tools_path, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_path):
            wx.MessageBox(_("ffmpeg.exe not found"), _("Error"), wx.OK | wx.ICON_ERROR)
            return
            
        self.cleanup_temp_file()
        
        # Determine output format for preview
        if self.audio_radio.GetValue():
            self.temp_preview_file = os.path.join(self.output_path, f"temp_preview_{os.path.splitext(os.path.basename(self.selected_file))[0]}.mp3")
        else:
            self.temp_preview_file = os.path.join(self.output_path, f"temp_preview_{os.path.splitext(os.path.basename(self.selected_file))[0]}.mp4")
        
        cmd = [
            ffmpeg_path,
            "-y",
            "-i", self.selected_file,
            "-ss", str(start_seconds),
            "-to", str(end_seconds),
        ]
        
        # Build filters based on output type
        if self.audio_radio.GetValue():
            filter_complex = self.build_fade_filter(
                self.time_to_seconds(self.fade_in_start_ctrl.GetValue()) if self.fade_in_start_ctrl.GetValue() else None,
                self.time_to_seconds(self.fade_in_end_ctrl.GetValue()) if self.fade_in_end_ctrl.GetValue() else None,
                self.time_to_seconds(self.fade_out_start_ctrl.GetValue()) if self.fade_out_start_ctrl.GetValue() else None,
                self.time_to_seconds(self.fade_out_end_ctrl.GetValue()) if self.fade_out_end_ctrl.GetValue() else None,
                trim_duration
            )
            if filter_complex:
                cmd.extend(["-af", filter_complex])
            
            cmd.extend([
                "-c:a", "libmp3lame",
                "-b:a", "192k",
                "-ar", "44100",
            ])
        else:
            # Video preview - use simple copy for faster preview
            cmd.extend([
                "-c", "copy",
            ])
        
        cmd.append(self.temp_preview_file)
        
        def run_preview():
            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    encoding='utf-8',
                    errors='ignore'
                )
                if result.returncode == 0:
                    time.sleep(0.5)
                    if os.path.exists(self.temp_preview_file) and os.path.getsize(self.temp_preview_file) > 1024:
                        try:
                            os.startfile(self.temp_preview_file)
                        except Exception as e:
                            wx.CallAfter(wx.MessageBox, _("Could not play preview: {}").format(str(e)), _("Error"), wx.OK | wx.ICON_ERROR)
                    else:
                        wx.CallAfter(wx.MessageBox, _("Preview file was not created or is too small"), _("Error"), wx.OK | wx.ICON_ERROR)
                else:
                    error_msg = result.stderr.strip() or _("Unknown error")
                    wx.CallAfter(wx.MessageBox, _("Preview failed: {}").format(error_msg), _("Error"), wx.OK | wx.ICON_ERROR)
            except Exception as e:
                wx.CallAfter(wx.MessageBox, _("Preview process failed: {}").format(str(e)), _("Error"), wx.OK | wx.ICON_ERROR)
        
        threading.Thread(target=run_preview, daemon=True).start()
    
    def on_trim(self, event):
        # DEBUG: Log current state with more details
        log.info(f"=== DEBUG TRIM START ===")
        log.info(f"Audio radio: {self.audio_radio.GetValue()}")
        log.info(f"Video radio: {self.video_radio.GetValue()}")
        log.info(f"Audio panel visible: {self.audio_panel.IsShown()}")
        log.info(f"Video panel visible: {self.video_panel.IsShown()}")
        
        self.cleanup_temp_file()
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
                
            trim_duration = end_seconds - start_seconds
            
            # Check fade settings only for audio
            if self.audio_radio.GetValue():
                fade_in_start = self.time_to_seconds(self.fade_in_start_ctrl.GetValue()) if self.fade_in_start_ctrl.GetValue() else None
                fade_in_end = self.time_to_seconds(self.fade_in_end_ctrl.GetValue()) if self.fade_in_end_ctrl.GetValue() else None
                fade_out_start = self.time_to_seconds(self.fade_out_start_ctrl.GetValue()) if self.fade_out_start_ctrl.GetValue() else None
                fade_out_end = self.time_to_seconds(self.fade_out_end_ctrl.GetValue()) if self.fade_out_end_ctrl.GetValue() else None
                
                if fade_in_start is not None and fade_in_end is not None:
                    if fade_in_start < start_seconds or fade_in_end > end_seconds or fade_in_start >= fade_in_end:
                        wx.MessageBox(_("Invalid fade-in range"), _("Error"), wx.OK | wx.ICON_ERROR)
                        return
                if fade_out_start is not None and fade_out_end is not None:
                    if fade_out_start < start_seconds or fade_out_end > end_seconds or fade_out_start >= fade_out_end:
                        wx.MessageBox(_("Invalid fade-out range"), _("Error"), wx.OK | wx.ICON_ERROR)
                        return
                    
        except ValueError:
            wx.MessageBox(_("Invalid time values"), _("Error"), wx.OK | wx.ICON_ERROR)
            return
            
        # Determine output format and extension
        is_audio_mode = self.audio_radio.GetValue()
        log.info(f"is_audio_mode: {is_audio_mode}")
        
        if is_audio_mode:
            output_format = "mp3" if self.mp3_radio.GetValue() else "wav"
            quality_kbps = int(self.audio_quality_ctrl.GetStringSelection().split()[0]) if self.mp3_radio.GetValue() else None
            log.info(f"Audio mode - Format: {output_format}, Quality: {quality_kbps}")
        else:
            format_map = {
                "MP4 (Copy Original Quality)": "mp4",
                "MKV (Copy Original Quality)": "mkv", 
                "MOV (Copy Original Quality)": "mov",
                "AVI (Copy Original Quality)": "avi",
                "WebM (Copy Original Quality)": "webm"
            }
            output_format = format_map.get(self.video_format_ctrl.GetStringSelection(), "mp4")
            sample_rate = self.sample_rate_ctrl.GetStringSelection().replace(" Hz", "")
            channels_selection = self.channels_ctrl.GetStringSelection()
            audio_codec_selection = self.audio_codec_ctrl.GetStringSelection()
            
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
            
            log.info(f"Video mode - Format: {output_format}, Sample Rate: {sample_rate}, Channels: {audio_channels}, Audio Codec: {audio_codec}")
        
        output_file_name = self.output_text.GetValue().strip()
        if not output_file_name:
            output_file_name = os.path.splitext(os.path.basename(self.selected_file))[0] + "_trimmed"
        output_file = self.get_unique_filename(output_file_name, output_format)
        output_path = os.path.join(self.output_path, output_file)
        ffmpeg_path = os.path.join(self.tools_path, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_path):
            wx.MessageBox(_("ffmpeg.exe not found"), _("Error"), wx.OK | wx.ICON_ERROR)
            return
            
        self.trim_btn.Enable(False)
        self.cancel_btn.Enable(False)
        self.status_label.SetLabel(_("Starting trim operation..."))
        self.progress_bar.SetValue(0)
        
        cmd = [
            ffmpeg_path,
            "-y",
            "-i", self.selected_file,
            "-ss", str(start_seconds),
            "-to", str(end_seconds),
        ]
        
        # FIXED: Use explicit condition checking to avoid audio processing in video mode
        if is_audio_mode:
            # Audio processing
            log.info("Building command for AUDIO processing")
            filter_complex = self.build_fade_filter(
                self.time_to_seconds(self.fade_in_start_ctrl.GetValue()) if self.fade_in_start_ctrl.GetValue() else None,
                self.time_to_seconds(self.fade_in_end_ctrl.GetValue()) if self.fade_in_end_ctrl.GetValue() else None,
                self.time_to_seconds(self.fade_out_start_ctrl.GetValue()) if self.fade_out_start_ctrl.GetValue() else None,
                self.time_to_seconds(self.fade_out_end_ctrl.GetValue()) if self.fade_out_end_ctrl.GetValue() else None,
                trim_duration
            )
            if filter_complex:
                cmd.extend(["-af", filter_complex])
            
            if output_format == "mp3":
                cmd.extend([
                    "-c:a", "libmp3lame",
                    "-b:a", f"{quality_kbps}k",
                    "-ar", "44100",
                ])
            else:
                cmd.extend([
                    "-c:a", "pcm_s16le",
                    "-ar", "44100",
                ])
        else:
            # Video processing - COMPLETELY REWRITTEN: Force proper MKV to MP4 conversion
            log.info("Building command for VIDEO processing")
            # Always detect source audio codec first
            source_audio_codec = None
            try:
                ffprobe_path = os.path.join(self.tools_path, "ffprobe.exe")
                probe_cmd = [
                    ffprobe_path,
                    "-v", "error",
                    "-select_streams", "a:0",
                    "-show_entries", "stream=codec_name",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    self.selected_file
                ]
                result = subprocess.run(
                    probe_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    encoding='utf-8',
                    errors='ignore'
                )
                if result.returncode == 0:
                    source_audio_codec = result.stdout.strip().lower()
                    log.info(f"Detected source audio codec: {source_audio_codec}")
                else:
                    log.error(f"FFprobe error: {result.stderr}")
            except Exception as e:
                log.error(f"Failed to detect audio codec: {e}")

            # FIXED: Use explicit stream mapping with proper codec selection
            cmd.extend(["-c:v", "copy"])  # Always copy video stream
            
            # Handle audio based on source codec and output format
            if output_format == "mp4":
                if source_audio_codec and self.is_pcm_audio(source_audio_codec):
                    log.info("PCM audio detected, re-encoding to AAC for MP4 compatibility")
                    cmd.extend(["-c:a", "aac"])
                    cmd.extend(["-b:a", "192k"])
                    
                    # Set sample rate if specified, otherwise use 48000
                    if sample_rate != "Keep Original":
                        cmd.extend(["-ar", sample_rate])
                    else:
                        cmd.extend(["-ar", "48000"])
                    
                    # Set audio channels if specified
                    if audio_channels is not None:
                        cmd.extend(["-ac", audio_channels])
                    else:
                        cmd.extend(["-ac", "2"])  # Default to stereo
                else:
                    # For non-PCM audio, use copy if compatible
                    log.info("Using copy for audio stream")
                    cmd.extend(["-c:a", "copy"])
            else:
                # For non-MP4 formats, use copy for audio
                cmd.extend(["-c:a", "copy"])
        
        cmd.append(output_path)
        
        # Log the command for debugging
        log.info(f"FFmpeg command: {' '.join(cmd)}")
        
        def run_ffmpeg():
            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    encoding='utf-8',
                    errors='ignore'
                )
                if result.returncode == 0:
                    try:
                        tones.beep(1000, 300)
                    except Exception:
                        pass
                    wx.CallAfter(self.progress_bar.SetValue, 100)
                    wx.CallAfter(self.status_label.SetLabel, _("Trim complete!"))
                    wx.CallAfter(ui.message, _("Trimmed file saved as {}").format(output_path))
                    config_data = self.load_config()
                    config_data["TrimLastOutputType"] = "audio" if is_audio_mode else "video"
                    if is_audio_mode:
                        config_data["TrimLastFormat"] = output_format
                        if quality_kbps:
                            config_data["TrimLastQuality"] = quality_kbps
                    else:
                        config_data["TrimLastVideoFormat"] = self.video_format_ctrl.GetStringSelection()
                        config_data["TrimLastSampleRate"] = self.sample_rate_ctrl.GetStringSelection()
                        config_data["TrimLastChannels"] = self.channels_ctrl.GetStringSelection()
                        config_data["TrimLastAudioCodec"] = self.audio_codec_ctrl.GetStringSelection()
                    config_data["TrimLastStartTime"] = start_time
                    config_data["TrimLastEndTime"] = end_time
                    config_data["TrimLastFile"] = self.selected_file
                    config_data["TrimLastFadeInStart"] = self.fade_in_start_ctrl.GetValue()
                    config_data["TrimLastFadeInEnd"] = self.fade_in_end_ctrl.GetValue()
                    config_data["TrimLastFadeOutStart"] = self.fade_out_start_ctrl.GetValue()
                    config_data["TrimLastFadeOutEnd"] = self.fade_out_end_ctrl.GetValue()
                    self.save_config(config_data)
                    wx.CallAfter(self.EndModal, wx.ID_OK)
                else:
                    error_msg = result.stderr.strip() or _("Unknown error")
                    log.error(f"FFmpeg error: {error_msg}")
                    wx.CallAfter(self.status_label.SetLabel, _("Trim failed!"))
                    wx.CallAfter(ui.message, _("Trimming failed: {}").format(error_msg))
                    wx.CallAfter(self.trim_btn.Enable, True)
                    wx.CallAfter(self.cancel_btn.Enable, True)
            except Exception as e:
                log.error(f"Exception during trimming: {str(e)}")
                wx.CallAfter(self.status_label.SetLabel, _("Trim failed!"))
                wx.CallAfter(ui.message, _("Trimming failed: {}").format(str(e)))
                wx.CallAfter(self.trim_btn.Enable, True)
                wx.CallAfter(self.cancel_btn.Enable, True)
                
        threading.Thread(target=run_ffmpeg, daemon=True).start()
        
    def is_pcm_audio(self, codec_name):
        """Check if the audio codec is PCM (uncompressed audio)."""
        pcm_codecs = ['pcm_s16le', 'pcm_s24le', 'pcm_s32le', 'pcm_f32le', 'pcm_f64le']
        return codec_name in pcm_codecs
        
    def on_cancel(self, event):
        self.cleanup_temp_file()
        self.EndModal(wx.ID_CANCEL)
        
    def on_close(self, event):
        self.cleanup_temp_file()
        event.Skip()
        
    def cleanup_temp_file(self):
        if self.temp_preview_file and os.path.exists(self.temp_preview_file):
            try:
                os.remove(self.temp_preview_file)
            except Exception as e:
                log.error(f"Failed to remove temporary file: {str(e)}")
                
    def validate_time_format(self, time_str):
        if not time_str:
            return True
        pattern = re.compile(r"^((\d+):)?(\d{1,2}):(\d{2})$|^(\d+(\.\d+)?)$")
        return pattern.match(time_str) is not None
        
    def time_to_seconds(self, time_str):
        if not time_str:
            return 0
        try:
            if '.' in time_str:
                return float(time_str)
            parts = time_str.split(":")
            if len(parts) == 1:
                return float(parts[0])
            elif len(parts) == 2:
                minutes, seconds = map(float, parts)
                return minutes * 60 + seconds
            elif len(parts) == 3:
                hours, minutes, seconds = map(float, parts)
                return hours * 3600 + minutes * 60 + seconds
            else:
                raise ValueError("Invalid time format")
        except ValueError:
            raise ValueError("Invalid time values")
            
    def get_unique_filename(self, base_name, extension):
        counter = 1
        output_file = f"{base_name}.{extension}"
        while os.path.exists(os.path.join(self.output_path, output_file)):
            output_file = f"{base_name}_{counter}.{extension}"
            counter += 1
        return output_file
