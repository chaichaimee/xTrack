# convertMP3toMP4.py

import wx
import os
import subprocess
import threading
import ui
import tones
import tempfile
import queue
from gui import guiHelper
from .xTrackCore import get_file_duration
import addonHandler

addonHandler.initTranslation()

class ConvertMP3toMP4Dialog(wx.Dialog):
    """Dialog for converting an MP3 file and a photo to an MP4 video."""
    def __init__(self, parent, selected_file, tools_path):
        super().__init__(parent, title=_("Convert MP3 to MP4"))
        self.selected_mp3 = selected_file
        self.tools_path = tools_path
        self.selected_photos = []
        self.output_path = os.path.dirname(self.selected_mp3)
        self.mp3_duration_seconds = 0
        self.ffmpeg_process = None
        self.is_paused = False
        self.loop_duration = 5  # Default loop duration in seconds
        self.stderr_queue = queue.Queue()
        self.init_ui()
        self.SetTitle(_("Convert MP3 to MP4: {}").format(os.path.basename(self.selected_mp3)))
        threading.Thread(target=self.get_mp3_duration, daemon=True).start()
        self.Bind(wx.EVT_CLOSE, self.on_close)

    def init_ui(self):
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # MP3 file info
        mp3_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Selected MP3 File"))
        self.mp3_label = wx.StaticText(self, label=os.path.basename(self.selected_mp3))
        mp3_sizer.Add(self.mp3_label, 0, wx.EXPAND | wx.ALL, 5)
        self.mp3_duration_label = wx.StaticText(self, label=_("Duration: Calculating..."))
        mp3_sizer.Add(self.mp3_duration_label, 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(mp3_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Photo selection
        photo_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Photo List"))
        self.photo_listbox = wx.ListBox(self, style=wx.LB_SINGLE)
        photo_sizer.Add(self.photo_listbox, 1, wx.EXPAND | wx.ALL, 5)
        
        photo_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.add_photo_btn = wx.Button(self, label=_("Add Photo..."))
        self.add_photo_btn.Bind(wx.EVT_BUTTON, self.on_add_photo)
        photo_btn_sizer.Add(self.add_photo_btn, 1, wx.EXPAND | wx.ALL, 5)
        
        self.remove_photo_btn = wx.Button(self, label=_("Remove Photo"))
        self.remove_photo_btn.Bind(wx.EVT_BUTTON, self.on_remove_photo)
        photo_btn_sizer.Add(self.remove_photo_btn, 1, wx.EXPAND | wx.ALL, 5)
        
        photo_sizer.Add(photo_btn_sizer, 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(photo_sizer, 1, wx.EXPAND | wx.ALL, 5)
        
        # Loop duration setting (initially hidden)
        self.loop_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.loop_label = wx.StaticText(self, label=_("Loop Duration (seconds):"))
        self.loop_sizer.Add(self.loop_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.loop_duration_ctrl = wx.SpinCtrl(self, min=1, max=3600, initial=5)
        self.loop_sizer.Add(self.loop_duration_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(self.loop_sizer, 0, wx.EXPAND | wx.ALL, 5)
        self.loop_sizer.ShowItems(False)  # Hide initially
        
        # Background color setting with 12 colors
        color_sizer = wx.BoxSizer(wx.HORIZONTAL)
        color_label = wx.StaticText(self, label=_("Background Color:"))
        color_sizer.Add(color_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        color_choices = [
            _("None"), _("Black"), _("White"), _("Red"), _("Green"), _("Blue"),
            _("Yellow"), _("Magenta"), _("Cyan"), _("Gray"), _("Orange"), _("Purple")
        ]
        self.color_ctrl = wx.ComboBox(self, choices=color_choices, style=wx.CB_READONLY)
        self.color_ctrl.SetStringSelection(_("None"))
        color_sizer.Add(self.color_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(color_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Auto crop setting
        self.crop_checkbox = wx.CheckBox(self, label=_("Auto crop images to 16:9 aspect ratio"))
        self.crop_checkbox.SetValue(True)
        main_sizer.Add(self.crop_checkbox, 0, wx.EXPAND | wx.ALL, 5)
        
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
        self.CentreOnParent()

    def get_mp3_duration(self):
        duration_sec, duration_str = get_file_duration(self.tools_path, self.selected_mp3)
        self.mp3_duration_seconds = duration_sec
        wx.CallAfter(self.mp3_duration_label.SetLabel, _("Duration: {}").format(duration_str))

    def on_add_photo(self, event):
        wildcard = _("Image files (*.png;*.jpg;*.jpeg;*.bmp)|*.png;*.jpg;*.jpeg;*.bmp")
        with wx.FileDialog(self, _("Select photos"), wildcard=wildcard, 
                          style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                paths = dlg.GetPaths()
                for path in paths:
                    if path not in self.selected_photos and os.path.exists(path):
                        try:
                            with open(path, 'rb') as f:
                                f.read(1)  # Test if file is accessible
                            self.selected_photos.append(path)
                        except Exception as e:
                            ui.message(_("Invalid image file: {}").format(path))
                self.update_photo_listbox()
                # Show/hide loop duration based on number of photos
                self.update_loop_duration_visibility()

    def on_remove_photo(self, event):
        selection = self.photo_listbox.GetSelection()
        if selection != wx.NOT_FOUND:
            self.selected_photos.pop(selection)
            self.update_photo_listbox()
            # Show/hide loop duration based on number of photos
            self.update_loop_duration_visibility()

    def update_photo_listbox(self):
        self.photo_listbox.Clear()
        for photo in self.selected_photos:
            self.photo_listbox.Append(os.path.basename(photo))

    def update_loop_duration_visibility(self):
        """Show loop duration control only when there are multiple photos."""
        if len(self.selected_photos) > 1:
            self.loop_sizer.ShowItems(True)
        else:
            self.loop_sizer.ShowItems(False)
        self.Layout()
        self.Fit()

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
        self.loop_duration = self.loop_duration_ctrl.GetValue()
        selected_color = self.color_ctrl.GetStringSelection()
        
        # If no photos selected but a background color is chosen, use color background
        if not self.selected_photos and selected_color == _("None"):
            ui.message(_("Please select at least one photo or choose a background color."))
            return
        
        base_name = os.path.splitext(os.path.basename(self.selected_mp3))[0]
        output_file = f"{base_name}.mp4"
        output_path = os.path.join(self.output_path, output_file)
        
        ffmpeg_path = os.path.join(self.tools_path, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_path):
            ui.message(_("ffmpeg.exe not found"))
            return
        
        # Build FFmpeg command based on whether we have photos or just a background color
        if self.selected_photos:
            # For single photo, use it for the entire duration with proper video encoding
            if len(self.selected_photos) == 1:
                photo = self.selected_photos[0]
                cmd = [
                    ffmpeg_path,
                    "-loop", "1",
                    "-i", photo,
                    "-i", self.selected_mp3,
                    "-c:v", "libx264",
                    "-preset", "medium",
                    "-tune", "stillimage",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-pix_fmt", "yuv420p",
                    "-shortest",
                    "-movflags", "+faststart"
                ]
                
                # Add crop filter if enabled
                if self.crop_checkbox.GetValue():
                    cmd.extend(["-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2"])
            else:
                # For multiple photos, use proper concat with image2 demuxer
                temp_dir = tempfile.gettempdir()
                concat_file = os.path.join(temp_dir, f"xtrack_concat_{id(self)}.txt")
                try:
                    with open(concat_file, 'w', encoding='utf-8') as f:
                        for photo in self.selected_photos:
                            escaped_photo = photo.replace("'", "'\\''")
                            f.write(f"file '{escaped_photo}'\n")
                            f.write(f"duration {self.loop_duration}\n")
                except Exception as e:
                    ui.message(_("Failed to create file list: {}").format(str(e)))
                    return
                
                cmd = [
                    ffmpeg_path,
                    "-f", "concat",
                    "-safe", "0",
                    "-protocol_whitelist", "file,pipe,crypto,data",
                    "-i", concat_file,
                    "-i", self.selected_mp3,
                    "-c:v", "libx264",
                    "-preset", "medium",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-pix_fmt", "yuv420p",
                    "-shortest",
                    "-movflags", "+faststart"
                ]
                
                # Add crop filter if enabled
                if self.crop_checkbox.GetValue():
                    cmd.extend(["-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2"])
        else:
            # Use color background
            color_map = {
                _("Black"): "000000",
                _("White"): "FFFFFF",
                _("Red"): "FF0000",
                _("Green"): "00FF00",
                _("Blue"): "0000FF",
                _("Yellow"): "FFFF00",
                _("Magenta"): "FF00FF",
                _("Cyan"): "00FFFF",
                _("Gray"): "808080",
                _("Orange"): "FFA500",
                _("Purple"): "800080"
            }
            
            color_hex = color_map.get(selected_color, "000000")
            duration = self.mp3_duration_seconds
            
            cmd = [
                ffmpeg_path,
                "-f", "lavfi",
                "-i", f"color=c={color_hex}:s=1920x1080:d={duration}",
                "-i", self.selected_mp3,
                "-c:v", "libx264",
                "-preset", "medium",
                "-c:a", "aac",
                "-b:a", "192k",
                "-shortest",
                "-movflags", "+faststart"
            ]
        
        # Add common parameters
        cmd.extend([
            "-progress", "pipe:1",
            "-nostats",
            "-y",
            output_path
        ])
        
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
                            if self.mp3_duration_seconds > 0:
                                progress = int((current_time_seconds / self.mp3_duration_seconds) * 100)
                                if progress >= 0 and progress <= 100:
                                    wx.CallAfter(self.update_progress, progress)
                        except (ValueError, IndexError):
                            continue
                
                self.ffmpeg_process.wait()
                
                # Join stderr thread
                stderr_thread.join(timeout=5)
                stderr_output = ''.join(list(self.stderr_queue.queue))
                
                # Clean up temporary file if it exists
                if self.selected_photos and len(self.selected_photos) > 1:
                    try:
                        concat_file = os.path.join(tempfile.gettempdir(), f"xtrack_concat_{id(self)}.txt")
                        if os.path.exists(concat_file):
                            os.remove(concat_file)
                    except Exception:
                        pass
                
                if self.ffmpeg_process.returncode == 0:
                    wx.CallAfter(self.on_success)
                else:
                    wx.CallAfter(self.on_failure, stderr_output)
                    
            except Exception as e:
                wx.CallAfter(self.on_failure, str(e))
            finally:
                if self.ffmpeg_process:
                    self.ffmpeg_process.stdout.close()
                    self.ffmpeg_process.stderr.close()
                self.ffmpeg_process = None
        
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
        ui.message(_("Conversion complete!"))
        self.convert_btn.SetLabel(_("Convert"))
        self.cancel_btn.Enable(True)
        wx.CallAfter(self.EndModal, wx.ID_OK)

    def on_failure(self, error_message):
        self.status_label.SetLabel(_("Conversion failed."))
        ui.message(_("Conversion failed: {}").format(error_message))
        self.convert_btn.SetLabel(_("Convert"))
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



