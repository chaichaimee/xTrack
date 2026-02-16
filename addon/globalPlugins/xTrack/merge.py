# merge.py

import wx
import os
import subprocess
import gui
import ui
import threading
import tones
import queue
import tempfile
from gui import guiHelper
from .xTrackCore import get_file_duration
import addonHandler

addonHandler.initTranslation()

class MergeAudioDialog(wx.Dialog):
    def __init__(self, parent, selected_files, tools_path):
        super().__init__(parent, title=_("Merge MP3"))
        self.selected_files = selected_files
        self.tools_path = tools_path
        self.output_path = os.path.dirname(selected_files[0]) if selected_files else os.getcwd()
        self.ffmpeg_process = None
        self.is_paused = False
        self.total_duration = 0
        self.all_mp3 = all(os.path.splitext(f)[1].lower() == '.mp3' for f in selected_files)
        self.stderr_queue = queue.Queue()
        self.file_durations = {}
        self.init_ui()
        self.Bind(wx.EVT_CLOSE, self.on_close)
        threading.Thread(target=self.calculate_total_duration_and_load_durations, daemon=True).start()

    def calculate_total_duration_and_load_durations(self):
        """Calculate total duration and load individual file durations."""
        total = 0
        for file in self.selected_files:
            duration_sec, duration_str = get_file_duration(self.tools_path, file)
            total += duration_sec
            self.file_durations[file] = self.format_duration(duration_sec)
        self.total_duration = total
        wx.CallAfter(self.update_file_list)

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
        # File list
        file_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Files to Merge"))
        self.file_list = wx.ListBox(self)
        file_sizer.Add(self.file_list, 1, wx.EXPAND | wx.ALL, 5)
        # File order buttons
        order_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.up_btn = wx.Button(self, label=_("Move Up"))
        self.down_btn = wx.Button(self, label=_("Move Down"))
        order_btn_sizer.Add(self.up_btn, 0, wx.ALL, 5)
        order_btn_sizer.Add(self.down_btn, 0, wx.ALL, 5)
        file_sizer.Add(order_btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        main_sizer.Add(file_sizer, 1, wx.EXPAND | wx.ALL, 5)
        
        # Output filename
        output_sizer = wx.BoxSizer(wx.HORIZONTAL)
        output_label = wx.StaticText(self, label=_("Output Filename:"))
        output_sizer.Add(output_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.output_text = wx.TextCtrl(self, value="merged_audio")
        output_sizer.Add(self.output_text, 1, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(output_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Cross-fade settings - Updated labels for clarity
        crossfade_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Cross-fade Settings"))
        self.crossfade_checkbox = wx.CheckBox(self, label=_("Enable Cross-fade between tracks"))
        self.crossfade_checkbox.SetToolTip(_("Enable smooth transitions between tracks by fading out the end of one track and fading in the beginning of the next"))
        self.crossfade_checkbox.Bind(wx.EVT_CHECKBOX, self.on_crossfade_changed)
        crossfade_sizer.Add(self.crossfade_checkbox, 0, wx.ALL, 5)
        
        # Cross-fade duration
        crossfade_duration_sizer = wx.BoxSizer(wx.HORIZONTAL)
        crossfade_duration_label = wx.StaticText(self, label=_("Cross-fade Duration (seconds):"))
        crossfade_duration_sizer.Add(crossfade_duration_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.crossfade_duration_ctrl = wx.SpinCtrl(self, min=1, max=30, initial=5)
        self.crossfade_duration_ctrl.SetToolTip(_("Duration in seconds for cross-fading between tracks. Recommended value is 5 seconds."))
        crossfade_duration_sizer.Add(self.crossfade_duration_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        crossfade_sizer.Add(crossfade_duration_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Fade-in for next track setting
        fade_in_next_sizer = wx.BoxSizer(wx.HORIZONTAL)
        fade_in_next_label = wx.StaticText(self, label=_("Fade-in for Next Track (seconds):"))
        fade_in_next_sizer.Add(fade_in_next_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.fade_in_next_ctrl = wx.SpinCtrl(self, min=0, max=10, initial=0)
        self.fade_in_next_ctrl.SetToolTip(_("Additional fade-in duration for the beginning of each track (except the first track)"))
        fade_in_next_sizer.Add(self.fade_in_next_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        crossfade_sizer.Add(fade_in_next_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        main_sizer.Add(crossfade_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Merge mode
        mode_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Merge Mode"))
        self.reencode_radio = wx.RadioButton(self, label=_("Re-encode with quality (slower)"), style=wx.RB_GROUP)
        self.copy_radio = wx.RadioButton(self, label=_("Copy without re-encoding (faster)"))
        self.copy_radio.Enable(self.all_mp3)
        mode_sizer.Add(self.reencode_radio, 0, wx.ALL, 5)
        mode_sizer.Add(self.copy_radio, 0, wx.ALL, 5)
        main_sizer.Add(mode_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Audio quality setting (enabled only for re-encode)
        quality_sizer = wx.BoxSizer(wx.HORIZONTAL)
        quality_label = wx.StaticText(self, label=_("Audio Quality:"))
        quality_sizer.Add(quality_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        quality_choices = ["320 kbps", "256 kbps", "192 kbps", "128 kbps"]
        self.quality_ctrl = wx.ComboBox(self, choices=quality_choices, style=wx.CB_READONLY)
        self.quality_ctrl.SetStringSelection("320 kbps")
        quality_sizer.Add(self.quality_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(quality_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Progress bar
        self.progress_bar = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
        main_sizer.Add(self.progress_bar, 0, wx.EXPAND | wx.ALL, 5)
        
        # Status label
        self.status_label = wx.StaticText(self, label="")
        main_sizer.Add(self.status_label, 0, wx.EXPAND | wx.ALL, 5)
        
        # Buttons
        btn_sizer = wx.StdDialogButtonSizer()
        self.merge_btn = wx.Button(self, wx.ID_OK, label=_("Merge"))
        self.merge_btn.Bind(wx.EVT_BUTTON, self.on_merge_or_pause)
        btn_sizer.AddButton(self.merge_btn)
        self.cancel_btn = wx.Button(self, wx.ID_CANCEL, label=_("Cancel"))
        self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
        btn_sizer.AddButton(self.cancel_btn)
        btn_sizer.Realize()
        main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        self.SetSizer(main_sizer)
        self.Fit()
        
        # Bind events
        self.up_btn.Bind(wx.EVT_BUTTON, self.on_up)
        self.down_btn.Bind(wx.EVT_BUTTON, self.on_down)
        self.file_list.Bind(wx.EVT_LISTBOX, self.on_select)
        self.reencode_radio.Bind(wx.EVT_RADIOBUTTON, self.on_mode_change)
        self.copy_radio.Bind(wx.EVT_RADIOBUTTON, self.on_mode_change)
        
        # Set initial state
        if self.all_mp3:
            self.copy_radio.SetValue(True)
        else:
            self.reencode_radio.SetValue(True)
        self.on_mode_change(None)
        self.on_crossfade_changed(None)
        self.update_buttons()
        
    def update_file_list(self):
        """Update file list with file names and durations."""
        file_display_names = []
        for file in self.selected_files:
            base_name = os.path.basename(file)
            duration = self.file_durations.get(file, _("Calculating..."))
            file_display_names.append(f"{base_name} ({duration})")
        self.file_list.Set(file_display_names)
        
    def on_crossfade_changed(self, event):
        """Enable/disable cross-fade controls based on checkbox state."""
        enabled = self.crossfade_checkbox.GetValue()
        self.crossfade_duration_ctrl.Enable(enabled)
        self.fade_in_next_ctrl.Enable(enabled)
        
        # If cross-fade is enabled, force re-encode mode
        if enabled:
            self.reencode_radio.SetValue(True)
            self.copy_radio.Enable(False)
            ui.message(_("Cross-fade requires re-encoding mode"))
        else:
            self.copy_radio.Enable(self.all_mp3)

    def on_mode_change(self, event):
        self.quality_ctrl.Enable(self.reencode_radio.GetValue())

    def update_buttons(self):
        selected_index = self.file_list.GetSelection()
        if selected_index == wx.NOT_FOUND:
            self.up_btn.Enable(False)
            self.down_btn.Enable(False)
        else:
            self.up_btn.Enable(selected_index > 0)
            self.down_btn.Enable(selected_index < len(self.selected_files) - 1)
            
    def on_select(self, event):
        self.update_buttons()
        event.Skip()
        
    def on_up(self, event):
        selected_index = self.file_list.GetSelection()
        if selected_index > 0:
            self.selected_files[selected_index - 1], self.selected_files[selected_index] = \
                self.selected_files[selected_index], self.selected_files[selected_index - 1]
            self.update_file_list()
            self.file_list.SetSelection(selected_index - 1)
            self.update_buttons()
            
    def on_down(self, event):
        selected_index = self.file_list.GetSelection()
        if selected_index != wx.NOT_FOUND and selected_index < len(self.selected_files) - 1:
            self.selected_files[selected_index + 1], self.selected_files[selected_index] = \
                self.selected_files[selected_index], self.selected_files[selected_index + 1]
            self.update_file_list()
            self.file_list.SetSelection(selected_index + 1)
            self.update_buttons()
            
    def on_merge_or_pause(self, event):
        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            self.toggle_pause()
        else:
            self.on_merge(event)

    def toggle_pause(self):
        if not self.ffmpeg_process:
            return
        
        if self.is_paused:
            self.ffmpeg_process.send_signal(subprocess.signal.SIGCONT)
            self.is_paused = False
            self.merge_btn.SetLabel(_("Pause"))
            self.status_label.SetLabel(_("Resuming..."))
        else:
            self.ffmpeg_process.send_signal(subprocess.signal.SIGSTOP)
            self.is_paused = True
            self.merge_btn.SetLabel(_("Resume"))
            self.status_label.SetLabel(_("Paused."))
        
        ui.message(self.status_label.GetLabel())
            
    def on_merge(self, event):
        if len(self.selected_files) < 2:
            ui.message(_("Please select at least 2 files"))
            return
        output_file_name = self.output_text.GetValue().strip()
        if not output_file_name:
            ui.message(_("Please enter an output filename."))
            return
            
        # Ensure .mp3 extension
        if not output_file_name.lower().endswith('.mp3'):
            output_file_name += '.mp3'
            
        output_file_path = os.path.join(self.output_path, output_file_name)
        
        # Get selected quality if re-encode
        quality_kbps = self.quality_ctrl.GetStringSelection().split()[0] if self.reencode_radio.GetValue() else None
        
        ffmpeg_path = os.path.join(self.tools_path, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_path):
            ui.message(_("ffmpeg.exe not found"))
            return
        
        # Validate all files exist
        for file in self.selected_files:
            if not os.path.exists(file):
                ui.message(_("File not found: {}").format(file))
                return
        
        wx.CallAfter(self.merge_btn.SetLabel, _("Pause"))
        wx.CallAfter(self.cancel_btn.Enable, False)
        wx.CallAfter(self.status_label.SetLabel, _("Starting merge..."))
        wx.CallAfter(self.progress_bar.SetValue, 0)
        
        # Run FFmpeg in a separate thread
        def run_merge():
            self.ffmpeg_process = None
            try:
                # Build FFmpeg command based on cross-fade selection
                if self.crossfade_checkbox.GetValue() and self.reencode_radio.GetValue():
                    # Use complex filter for cross-fade
                    cmd = self.build_crossfade_command(ffmpeg_path, output_file_path, quality_kbps)
                else:
                    # Use simple concat method
                    cmd = self.build_concat_command(ffmpeg_path, output_file_path, quality_kbps)
                
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
                
                # Start thread to read stderr to prevent buffer overflow
                def read_stderr():
                    for line in iter(self.ffmpeg_process.stderr.readline, ''):
                        self.stderr_queue.put(line)
                
                stderr_thread = threading.Thread(target=read_stderr, daemon=True)
                stderr_thread.start()
                
                while True:
                    line = self.ffmpeg_process.stdout.readline()
                    if not line:
                        break
                    if "out_time_ms=" in line:
                        try:
                            time_ms = int(line.split("=")[1])
                            current_time_seconds = time_ms / 1000000
                            if self.total_duration > 0:
                                progress = int((current_time_seconds / self.total_duration) * 100)
                                if progress >= 0 and progress <= 100:
                                    wx.CallAfter(self.update_progress, progress)
                        except (ValueError, IndexError):
                            continue
                
                self.ffmpeg_process.wait()
                
                # Join stderr thread
                stderr_thread.join(timeout=5)
                stderr_output = ''.join(list(self.stderr_queue.queue))
                
                if self.ffmpeg_process.returncode == 0:
                    tones.beep(1000, 300)  # High tone for success
                    wx.CallAfter(ui.message, _("Merge successful. Output saved to: {}").format(output_file_path))
                    wx.CallAfter(self.on_success)
                else:
                    wx.CallAfter(ui.message, _("Merge failed: {}").format(stderr_output))
                    wx.CallAfter(self.on_failure, stderr_output)
            except Exception as e:
                wx.CallAfter(ui.message, _("An error occurred during merge: {}").format(str(e)))
                wx.CallAfter(self.on_failure, str(e))
            finally:
                if self.ffmpeg_process:
                    self.ffmpeg_process.stdout.close()
                    self.ffmpeg_process.stderr.close()
                self.ffmpeg_process = None
        
        threading.Thread(target=run_merge, daemon=True).start()
    
    def build_concat_command(self, ffmpeg_path, output_file_path, quality_kbps):
        """Build command for simple concatenation."""
        # Create a temporary text file for concat demuxer
        temp_dir = tempfile.gettempdir()
        concat_file = os.path.join(temp_dir, f"xtrack_concat_{os.getpid()}.txt")
        
        with open(concat_file, 'w', encoding='utf-8') as f:
            for file in self.selected_files:
                escaped_file = file.replace("'", "'\\''")
                f.write(f"file '{escaped_file}'\n")
        
        cmd = [
            ffmpeg_path,
            "-f", "concat",
            "-safe", "0",
            "-protocol_whitelist", "file,crypto,data,pipe",
            "-i", concat_file,
            "-progress", "pipe:1",
            "-nostats",
            "-y",
        ]
        
        if self.reencode_radio.GetValue():
            cmd.extend([
                "-c:a", "libmp3lame",
                "-b:a", f"{quality_kbps}k",
            ])
        else:
            cmd.extend([
                "-c", "copy",
            ])
        
        cmd.append(output_file_path)
        return cmd
    
    def build_crossfade_command(self, ffmpeg_path, output_file_path, quality_kbps):
        """Build command for cross-fade merging."""
        # Build complex filter for cross-fading multiple files
        crossfade_duration = self.crossfade_duration_ctrl.GetValue()
        fade_in_next = self.fade_in_next_ctrl.GetValue()
        
        # Start with input files
        cmd = [ffmpeg_path]
        for file in self.selected_files:
            cmd.extend(["-i", file])
        
        # Build filter complex
        filter_complex = ""
        
        if len(self.selected_files) == 2:
            # Simple case for 2 files
            filter_complex = f"[0:a][1:a]acrossfade=d={crossfade_duration}:c1=tri:c2=tri[a]"
        else:
            # Chain cross-fades for multiple files
            for i in range(len(self.selected_files) - 1):
                if i == 0:
                    filter_complex += f"[{i}:a][{i+1}:a]acrossfade=d={crossfade_duration}:c1=tri:c2=tri[a{i+1}];"
                else:
                    filter_complex += f"[a{i}][{i+1}:a]acrossfade=d={crossfade_duration}:c1=tri:c2=tri[a{i+1}];"
            
            # Remove last semicolon and set final output
            filter_complex = filter_complex[:-1]
            filter_complex = filter_complex.replace(f"a{len(self.selected_files)-1}", "a")
        
        # Add fade-in for next tracks if specified (except first track)
        if fade_in_next > 0 and len(self.selected_files) > 1:
            # We need to modify the filter complex to add fade-in for tracks 2 onwards
            # This is a simplified approach - for exact implementation we'd need more complex filter graph
            filter_complex += f";[a]afade=t=in:st=0:d={fade_in_next}[out]"
            output_stream = "[out]"
        else:
            output_stream = "[a]"
        
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", output_stream,
            "-progress", "pipe:1",
            "-nostats",
            "-y",
            "-c:a", "libmp3lame",
            "-b:a", f"{quality_kbps}k",
            output_file_path
        ])
        
        return cmd
        
    def update_progress(self, progress):
        self.progress_bar.SetValue(progress)
        self.status_label.SetLabel(_("Merging: {}%").format(progress))
        # Announce progress for screen readers
        if progress % 10 == 0:  # Announce every 10%
            ui.message(_("{} percent").format(progress))

    def on_success(self):
        self.progress_bar.SetValue(100)
        self.status_label.SetLabel(_("Merge complete!"))
        self.merge_btn.SetLabel(_("Merge"))
        self.cancel_btn.Enable(True)
        self.EndModal(wx.ID_OK)

    def on_failure(self, error_message):
        self.status_label.SetLabel(_("Merge failed."))
        ui.message(_("Merge failed: {}").format(error_message))
        self.merge_btn.SetLabel(_("Merge"))
        self.cancel_btn.Enable(True)

    def on_cancel(self, event):
        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            try:
                self.ffmpeg_process.terminate()
            except Exception:
                pass
        self.EndModal(wx.ID_CANCEL)

    def on_close(self, event):
        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            try:
                self.ffmpeg_process.terminate()
            except Exception:
                pass
        self.EndModal(wx.ID_CANCEL)
