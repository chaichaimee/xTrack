# splitAudio.py

import wx
import os
import subprocess
import re
import threading
import tones
from gui import guiHelper
import ui
import json
from logHandler import log
import addonHandler

addonHandler.initTranslation()

class SplitAudioDialog(wx.Dialog):
    """Dialog for splitting audio files into multiple tracks."""
    def __init__(self, parent, selected_file, tools_path):
        super().__init__(parent, title=_("Split Audio File"))
        if not selected_file:
            raise ValueError("No file was selected.")
        self.selected_file = selected_file
        self.tools_path = tools_path
        self.file_duration_seconds = 0
        self.file_duration_str = ""
        self.output_path = os.path.dirname(self.selected_file)
        self.config_path = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "nvda", "config", "xTrack.json")
        self.track_controls = []  # List to store track end time controls
        self.init_ui()
        self.SetTitle(_("Split Audio: {}").format(os.path.basename(self.selected_file)))
        
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
        
        # File information section
        file_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Selected File"))
        self.file_label = wx.StaticText(self, label=os.path.basename(self.selected_file))
        file_sizer.Add(self.file_label, 0, wx.EXPAND | wx.ALL, 5)
        self.duration_label = wx.StaticText(self, label=_("Duration: Calculating..."))
        file_sizer.Add(self.duration_label, 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(file_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Split point control (number of tracks)
        split_box = wx.StaticBox(self, label=_("Split Settings"))
        split_sizer = wx.StaticBoxSizer(split_box, wx.VERTICAL)
        
        split_control_sizer = wx.BoxSizer(wx.HORIZONTAL)
        split_label = wx.StaticText(self, label=_("Number of tracks:"))
        split_control_sizer.Add(split_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.split_point_ctrl = wx.SpinCtrl(self, min=2, max=50, initial=2)
        self.split_point_ctrl.Bind(wx.EVT_SPINCTRL, self.on_split_point_change)
        split_control_sizer.Add(self.split_point_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        split_sizer.Add(split_control_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        main_sizer.Add(split_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Tracks section with Scrollable Panel
        tracks_panel = wx.ScrolledWindow(self)
        tracks_panel.SetScrollRate(10, 10)
        tracks_panel.SetMinSize((400, 300))  # Set minimum size
        
        tracks_box = wx.StaticBox(tracks_panel, label=_("Tracks"))
        self.tracks_sizer = wx.StaticBoxSizer(tracks_box, wx.VERTICAL)
        
        # Create initial track controls
        self.create_track_controls()
        
        tracks_panel.SetSizer(self.tracks_sizer)
        
        # Add the tracks panel to main sizer
        main_sizer.Add(tracks_panel, 1, wx.EXPAND | wx.ALL, 5)
        
        # Progress bar
        self.progress_bar = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
        main_sizer.Add(self.progress_bar, 0, wx.EXPAND | wx.ALL, 5)
        
        self.status_label = wx.StaticText(self, label="")
        main_sizer.Add(self.status_label, 0, wx.EXPAND | wx.ALL, 5)
        
        # Buttons
        btn_sizer = wx.StdDialogButtonSizer()
        self.split_btn = wx.Button(self, wx.ID_OK, label=_("Split"))
        self.split_btn.Bind(wx.EVT_BUTTON, self.on_split)
        btn_sizer.AddButton(self.split_btn)
        self.cancel_btn = wx.Button(self, wx.ID_CANCEL, label=_("Cancel"))
        self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
        btn_sizer.AddButton(self.cancel_btn)
        btn_sizer.Realize()
        main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        self.SetSizer(main_sizer)
        self.Fit()
        
        # Load saved configuration
        config_data = self.load_config()
        last_split_point = config_data.get("SplitLastSplitPoint", 2)
        self.split_point_ctrl.SetValue(last_split_point)
        
        # Adjust dialog size
        self.SetSize((500, 600))
        
    def create_track_controls(self):
        """Create track controls based on split point."""
        # Clear existing controls in tracks_sizer
        if hasattr(self, 'track_controls'):
            for ctrl in self.track_controls:
                ctrl.Destroy()
        self.track_controls = []
        
        # Get the static box from the tracks_sizer
        static_box = self.tracks_sizer.GetStaticBox()
        
        # Clear all children of the static box
        for child in static_box.GetChildren():
            child.Destroy()
        
        num_tracks = self.split_point_ctrl.GetValue()
        
        # Create a new sizer for the static box
        tracks_inner_sizer = wx.BoxSizer(wx.VERTICAL)
        
        for i in range(1, num_tracks + 1):
            track_sizer = wx.BoxSizer(wx.HORIZONTAL)
            track_label = wx.StaticText(static_box, label=_(" {:02d} End Time:").format(i))
            track_sizer.Add(track_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
            
            # Create edit field for each track end time
            time_ctrl = wx.TextCtrl(static_box)
            time_ctrl.track_num = i
            time_ctrl.Bind(wx.EVT_TEXT, self.on_track_time_change)
            
            # Set default values
            if i == 1:
                # First track default end time
                time_ctrl.SetValue("0:00")
            elif i == num_tracks:
                # Last track default end time is file duration (will be set when duration is known)
                time_ctrl.SetValue("")
            else:
                # Middle tracks - leave empty
                time_ctrl.SetValue("")
            
            track_sizer.Add(time_ctrl, 1, wx.EXPAND | wx.ALL, 5)
            self.track_controls.append(time_ctrl)
            
            tracks_inner_sizer.Add(track_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Set the new sizer for the static box
        static_box.SetSizer(tracks_inner_sizer)
        
        # Update the tracks_sizer
        self.tracks_sizer.SetMinSize(static_box.GetBestSize())
        
        # Update the scrollable panel
        scroll_panel = self.tracks_sizer.GetStaticBox().GetParent()
        scroll_panel.SetSizerAndFit(self.tracks_sizer)
        
        # Update the entire dialog
        self.Layout()
        self.update_track_controls()
    
    def get_file_duration(self):
        """Get file duration using ffprobe."""
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
            if result.returncode == 0 and result.stdout.strip():
                self.file_duration_seconds = float(result.stdout.strip())
                hours = int(self.file_duration_seconds // 3600)
                minutes = int((self.file_duration_seconds % 3600) // 60)
                seconds = int(self.file_duration_seconds % 60)
                self.file_duration_str = f"{hours}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes}:{seconds:02d}"
                wx.CallAfter(self.duration_label.SetLabel, _("Duration: {}").format(self.file_duration_str))
                wx.CallAfter(self.update_track_controls)
        except Exception as e:
            log.error(f"Failed to get file duration: {str(e)}")
            wx.CallAfter(
                wx.MessageBox,
                _("Failed to get file duration: {}").format(str(e)),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
            )
    
    def on_split_point_change(self, event):
        """Handle split point change event."""
        self.create_track_controls()
        event.Skip()
    
    def update_track_controls(self):
        """Update track time controls with calculated values."""
        if not self.file_duration_seconds > 0:
            return
            
        num_tracks = self.split_point_ctrl.GetValue()
        
        # Update last track with file duration
        if num_tracks >= 1 and len(self.track_controls) >= num_tracks:
            last_track_ctrl = self.track_controls[num_tracks - 1]
            if not last_track_ctrl.GetValue():
                last_track_ctrl.SetValue(self.file_duration_str)
        
        # Calculate and set suggested times for empty middle tracks
        if num_tracks > 2:
            for i in range(1, num_tracks - 1):  # Skip first and last
                track_ctrl = self.track_controls[i]
                if not track_ctrl.GetValue():
                    # Calculate suggested time: distribute evenly
                    suggested_seconds = (i * self.file_duration_seconds) / num_tracks
                    hours = int(suggested_seconds // 3600)
                    minutes = int((suggested_seconds % 3600) // 60)
                    seconds = int(suggested_seconds % 60)
                    if hours > 0:
                        suggested_time = f"{hours}:{minutes:02d}:{seconds:02d}"
                    else:
                        suggested_time = f"{minutes}:{seconds:02d}"
                    track_ctrl.SetValue(suggested_time)
    
    def on_track_time_change(self, event):
        """Handle track time change event."""
        ctrl = event.GetEventObject()
        time_str = ctrl.GetValue()
        
        if time_str and not self.validate_time_format(time_str):
            wx.CallLater(100, self.show_time_warning, ctrl)
        
        event.Skip()
    
    def show_time_warning(self, ctrl):
        """Show warning for invalid time format."""
        if not self or not self.IsShown():
            return
            
        time_str = ctrl.GetValue()
        if time_str and not self.validate_time_format(time_str):
            ui.message(_("Invalid time format. Use seconds (e.g., 10), MM:SS (e.g., 1:30), or HH:MM:SS (e.g., 1:30:45)"))
    
    def validate_time_format(self, time_str):
        """Validate time string format."""
        if not time_str:
            return True
        pattern = re.compile(r"^((\d+):)?(\d{1,2}):(\d{2})$|^(\d+(\.\d+)?)$")
        return pattern.match(time_str) is not None
    
    def time_to_seconds(self, time_str):
        """Convert time string to seconds."""
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
    
    def on_split(self, event):
        """Handle split button click."""
        num_tracks = self.split_point_ctrl.GetValue()
        
        # Validate all time entries
        end_times = []
        for i, ctrl in enumerate(self.track_controls):
            time_str = ctrl.GetValue()
            if not time_str:
                wx.MessageBox(_("Please fill in all time entries"), _("Error"), wx.OK | wx.ICON_ERROR)
                return
                
            if not self.validate_time_format(time_str):
                wx.MessageBox(_("Invalid time format for track {:02d}").format(i+1), _("Error"), wx.OK | wx.ICON_ERROR)
                return
                
            try:
                seconds = self.time_to_seconds(time_str)
                end_times.append(seconds)
            except ValueError:
                wx.MessageBox(_("Invalid time value for track {:02d}").format(i+1), _("Error"), wx.OK | wx.ICON_ERROR)
                return
        
        # Validate time order (end times must be increasing)
        for i in range(1, len(end_times)):
            if end_times[i] <= end_times[i-1]:
                wx.MessageBox(_("Track end times must be in increasing order"), _("Error"), wx.OK | wx.ICON_ERROR)
                return
        
        # Validate last time against file duration (allow up to file duration)
        if end_times[-1] > self.file_duration_seconds:
            wx.MessageBox(_("Last track end time cannot exceed file duration"), _("Error"), wx.OK | wx.ICON_ERROR)
            return
        
        # Disable buttons during processing
        self.split_btn.Enable(False)
        self.cancel_btn.Enable(False)
        self.status_label.SetLabel(_("Starting split operation..."))
        self.progress_bar.SetValue(0)
        
        # Start split in background thread
        threading.Thread(target=self.perform_split, args=(end_times,), daemon=True).start()
    
    def perform_split(self, end_times):
        """Perform the actual split operation using ffmpeg."""
        ffmpeg_path = os.path.join(self.tools_path, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_path):
            wx.CallAfter(wx.MessageBox, _("ffmpeg.exe not found"), _("Error"), wx.OK | wx.ICON_ERROR)
            wx.CallAfter(self.reset_buttons)
            return
        
        # Get file extension
        file_ext = os.path.splitext(self.selected_file)[1].lower()
        base_name = os.path.splitext(os.path.basename(self.selected_file))[0]
        
        success_count = 0
        total_tracks = len(end_times)
        
        for i in range(total_tracks):
            track_num = i + 1
            
            # Calculate start and end times for this track
            start_time = end_times[i-1] if i > 0 else 0
            end_time = end_times[i]
            
            # Skip if start and end are the same
            if start_time >= end_time:
                continue
            
            # Generate output filename
            output_filename = f"{base_name}_track{track_num:02d}{file_ext}"
            output_path = os.path.join(self.output_path, output_filename)
            
            # Make filename unique if needed
            counter = 1
            while os.path.exists(output_path):
                output_filename = f"{base_name}_track{track_num:02d}_{counter}{file_ext}"
                output_path = os.path.join(self.output_path, output_filename)
                counter += 1
            
            # Build ffmpeg command - copy codec to preserve original quality
            cmd = [
                ffmpeg_path,
                "-y",
                "-i", self.selected_file,
                "-ss", str(start_time),
                "-to", str(end_time),
                "-c", "copy",  # Copy codec to preserve original quality
                output_path,
            ]
            
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
                    success_count += 1
                else:
                    log.error(f"FFmpeg error for track {track_num}: {result.stderr}")
                
                # Update progress
                progress = int((i + 1) * 100 / total_tracks)
                wx.CallAfter(self.progress_bar.SetValue, progress)
                wx.CallAfter(self.status_label.SetLabel, _("Processing track {} of {}").format(track_num, total_tracks))
                
            except Exception as e:
                log.error(f"Exception processing track {track_num}: {str(e)}")
        
        # Operation complete
        wx.CallAfter(self.progress_bar.SetValue, 100)
        
        if success_count > 0:
            try:
                tones.beep(1000, 300)
            except Exception:
                pass
            
            wx.CallAfter(self.status_label.SetLabel, _("Split complete! {} tracks created").format(success_count))
            wx.CallAfter(ui.message, _("Audio file split into {} tracks").format(success_count))
            
            # Save configuration
            config_data = self.load_config()
            config_data["SplitLastSplitPoint"] = self.split_point_ctrl.GetValue()
            config_data["SplitLastFile"] = self.selected_file
            self.save_config(config_data)
            
            wx.CallAfter(self.EndModal, wx.ID_OK)
        else:
            wx.CallAfter(self.status_label.SetLabel, _("Split failed!"))
            wx.CallAfter(ui.message, _("Failed to split audio file"))
            wx.CallAfter(self.reset_buttons)
    
    def reset_buttons(self):
        """Reset button states."""
        if self and self.IsShown():
            self.split_btn.Enable(True)
            self.cancel_btn.Enable(True)
    
    def on_cancel(self, event):
        """Handle cancel button click."""
        self.EndModal(wx.ID_CANCEL)
    
    def on_close(self, event):
        """Handle dialog close event."""
        event.Skip()
