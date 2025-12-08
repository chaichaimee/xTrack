# resizeImage.py

import wx
import os
import subprocess
import threading
import ui
import tones
import json
import queue
import math
from gui import guiHelper
from .xTrackCore import load_config, save_config, get_file_size, get_unique_filename
import addonHandler

addonHandler.initTranslation()

class ResizeImageDialog(wx.Dialog):
    """Dialog for resizing images with simple percentage-based resizing."""
    def __init__(self, parent, selected_files, tools_path):
        super().__init__(parent, title=_("Resize Image"))
        self.selected_files = selected_files
        self.tools_path = tools_path
        self.output_path = os.path.dirname(self.selected_files[0]) if self.selected_files else os.getcwd()
        self.config_path = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "nvda", "config", "xTrack.json")
        self.ffmpeg_process = None
        self.is_paused = False
        self.resize_queue = queue.Queue()
        self.currently_processing = False
        self.file_sizes = {}
        self.image_dimensions = {}
        self.current_file_index = 0
        self.init_ui()
        self.SetTitle(_("Resize Image: {} files").format(len(self.selected_files)))
        self.load_settings()
        self.Bind(wx.EVT_CLOSE, self.on_close)
        # Pre-load file info in background
        threading.Thread(target=self.load_file_info, daemon=True).start()
        
        # Set focus to file listbox after UI is shown
        wx.CallAfter(self.file_listbox.SetFocus)

    def load_file_info(self):
        """Load sizes and dimensions for all selected files quickly."""
        for file_path in self.selected_files:
            try:
                size_bytes, size_str = get_file_size(file_path)
                self.file_sizes[file_path] = size_str
                width, height = self.get_image_dimensions_fast(file_path)
                self.image_dimensions[file_path] = (width, height)
            except Exception as e:
                log.error(f"Error loading file info for {file_path}: {e}")
                self.file_sizes[file_path] = "Error"
                self.image_dimensions[file_path] = (0, 0)
        
        wx.CallAfter(self.update_file_listbox)
        if self.selected_files:
            wx.CallAfter(self.update_output_info)

    def get_image_dimensions_fast(self, file_path):
        """Get image dimensions quickly using ffprobe."""
        ffprobe_path = os.path.join(self.tools_path, "ffprobe.exe")
        if not os.path.exists(ffprobe_path):
            return 0, 0
        
        cmd = [
            ffprobe_path,
            "-v", "quiet",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            file_path,
        ]
        
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                encoding='utf-8',
                errors='ignore',
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                dimensions = result.stdout.strip().split(',')
                if len(dimensions) == 2:
                    return int(dimensions[0]), int(dimensions[1])
        except Exception:
            pass
        return 0, 0

    def init_ui(self):
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # File selection section
        file_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Input Files"))
        file_list_sizer = wx.BoxSizer(wx.VERTICAL)
        file_list_sizer.Add(wx.StaticText(self, label=_("Selected files:")), 0, wx.EXPAND | wx.ALL, 5)
        self.file_listbox = wx.ListBox(self, choices=[self.get_file_display_name(f) for f in self.selected_files], style=wx.LB_EXTENDED)
        self.file_listbox.Bind(wx.EVT_LISTBOX, self.on_file_selection_change)
        file_list_sizer.Add(self.file_listbox, 1, wx.EXPAND | wx.ALL, 5)
        file_sizer.Add(file_list_sizer, 1, wx.EXPAND)
        
        # Output filename
        output_filename_sizer = wx.BoxSizer(wx.HORIZONTAL)
        output_filename_label = wx.StaticText(self, label=_("Output Filename:"))
        output_filename_sizer.Add(output_filename_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.output_filename_text = wx.TextCtrl(self, value="")
        output_filename_sizer.Add(self.output_filename_text, 1, wx.EXPAND | wx.ALL, 5)
        file_sizer.Add(output_filename_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Output folder selection
        output_sizer = wx.BoxSizer(wx.HORIZONTAL)
        output_label = wx.StaticText(self, label=_("Output folder:"))
        output_sizer.Add(output_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.output_path_ctrl = wx.TextCtrl(self, value=self.output_path)
        output_sizer.Add(self.output_path_ctrl, 1, wx.EXPAND | wx.ALL, 5)
        browse_btn = wx.Button(self, label=_("Browse..."))
        browse_btn.Bind(wx.EVT_BUTTON, self.on_browse_output)
        output_sizer.Add(browse_btn, 0, wx.ALL, 5)
        file_sizer.Add(output_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        main_sizer.Add(file_sizer, 1, wx.EXPAND | wx.ALL, 5)
        
        # Settings section - Simplified like web tool
        settings_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Resize Settings"))
        
        # Resize percentage
        resize_sizer = wx.BoxSizer(wx.HORIZONTAL)
        resize_label = wx.StaticText(self, label=_("Resize (%):"))
        resize_sizer.Add(resize_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.resize_percent = wx.SpinCtrl(self, min=1, max=100, initial=100)
        self.resize_percent.Bind(wx.EVT_SPINCTRL, self.on_setting_change)
        resize_sizer.Add(self.resize_percent, 1, wx.EXPAND | wx.ALL, 5)
        settings_sizer.Add(resize_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # New size display - Changed to ComboBox for better NVDA accessibility
        new_size_sizer = wx.BoxSizer(wx.HORIZONTAL)
        new_size_label = wx.StaticText(self, label=_("New Size:"))
        new_size_sizer.Add(new_size_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.new_size_display = wx.ComboBox(self, style=wx.CB_READONLY)
        new_size_sizer.Add(self.new_size_display, 1, wx.EXPAND | wx.ALL, 5)
        settings_sizer.Add(new_size_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Quality setting - Simple choices
        quality_sizer = wx.BoxSizer(wx.HORIZONTAL)
        quality_label = wx.StaticText(self, label=_("Quality:"))
        quality_sizer.Add(quality_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        quality_choices = [_("Best (No loss)"), _("Very Good (Minimal loss)"), _("Good (Small loss)"), _("Normal (Balanced)"), _("Small (Noticeable loss)")]
        self.quality_select = wx.ComboBox(self, choices=quality_choices, style=wx.CB_READONLY)
        self.quality_select.SetStringSelection(_("Very Good (Minimal loss)"))
        self.quality_select.Bind(wx.EVT_COMBOBOX, self.on_setting_change)
        quality_sizer.Add(self.quality_select, 1, wx.EXPAND | wx.ALL, 5)
        settings_sizer.Add(quality_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Format selection
        format_sizer = wx.BoxSizer(wx.HORIZONTAL)
        format_label = wx.StaticText(self, label=_("Format:"))
        format_sizer.Add(format_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        format_choices = ["JPEG", "WEBP", "PNG", "TIFF", "BMP", "GIF"]
        self.format_select = wx.ComboBox(self, choices=format_choices, style=wx.CB_READONLY)
        self.format_select.SetStringSelection("JPEG")
        self.format_select.Bind(wx.EVT_COMBOBOX, self.on_setting_change)
        format_sizer.Add(self.format_select, 1, wx.EXPAND | wx.ALL, 5)
        settings_sizer.Add(format_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Estimated size
        estimated_sizer = wx.BoxSizer(wx.HORIZONTAL)
        estimated_label = wx.StaticText(self, label=_("Estimated Output Size:"))
        estimated_sizer.Add(estimated_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.estimated_size = wx.ComboBox(self, style=wx.CB_READONLY)
        estimated_sizer.Add(self.estimated_size, 1, wx.EXPAND | wx.ALL, 5)
        settings_sizer.Add(estimated_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        main_sizer.Add(settings_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Output Information section
        info_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Output Information"))
        self.info_list = wx.ListCtrl(self, style=wx.LC_REPORT)
        self.info_list.InsertColumn(0, _("Output Name"), width=200)
        self.info_list.InsertColumn(1, _("New Size (W×H)"), width=120)
        self.info_list.InsertColumn(2, _("File Size"), width=100)
        self.info_list.InsertColumn(3, _("Status"), width=100)
        info_sizer.Add(self.info_list, 1, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(info_sizer, 1, wx.EXPAND | wx.ALL, 5)
        
        # Progress bars
        progress_sizer = wx.BoxSizer(wx.VERTICAL)
        current_progress_sizer = wx.BoxSizer(wx.HORIZONTAL)
        current_progress_sizer.Add(wx.StaticText(self, label=_("Current file:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.current_progress = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
        current_progress_sizer.Add(self.current_progress, 1, wx.EXPAND | wx.ALL, 5)
        progress_sizer.Add(current_progress_sizer, 0, wx.EXPAND)
        
        total_progress_sizer = wx.BoxSizer(wx.HORIZONTAL)
        total_progress_sizer.Add(wx.StaticText(self, label=_("Total progress:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.total_progress = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
        total_progress_sizer.Add(self.total_progress, 1, wx.EXPAND | wx.ALL, 5)
        progress_sizer.Add(total_progress_sizer, 0, wx.EXPAND)
        main_sizer.Add(progress_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Status label
        self.status_label = wx.StaticText(self, label=_("Ready"))
        main_sizer.Add(self.status_label, 0, wx.EXPAND | wx.ALL, 5)
        
        # Buttons
        btn_sizer = wx.StdDialogButtonSizer()
        self.start_btn = wx.Button(self, wx.ID_OK, label=_("Start"))
        self.start_btn.Bind(wx.EVT_BUTTON, self.on_start)
        btn_sizer.AddButton(self.start_btn)
        
        self.cancel_btn = wx.Button(self, wx.ID_CANCEL, label=_("Cancel"))
        self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
        btn_sizer.AddButton(self.cancel_btn)
        
        btn_sizer.Realize()
        main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        self.SetSizer(main_sizer)
        self.SetSize((800, 600))
        
        # Update file info table
        self.update_info_list()

    def on_file_selection_change(self, event):
        """Handle file selection change in the listbox."""
        selection = self.file_listbox.GetSelection()
        if selection != wx.NOT_FOUND:
            self.current_file_index = selection
            self.update_output_info()

    def update_output_info(self):
        """Update output information based on current settings."""
        if not self.selected_files:
            return
            
        file_path = self.selected_files[self.current_file_index]
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        
        # Set default output filename
        self.output_filename_text.SetValue(f"{base_name}_resized")
        
        # Update new size display
        self.update_new_size_display()
        
        # Calculate estimated size
        self.calculate_estimated_size()
        
        # Update info list
        self.update_info_list()

    def update_new_size_display(self):
        """Update the new size display based on resize percentage."""
        if not self.selected_files:
            self.new_size_display.SetValue("")
            return
            
        file_path = self.selected_files[self.current_file_index]
        orig_width, orig_height = self.image_dimensions.get(file_path, (0, 0))
        
        if orig_width > 0 and orig_height > 0:
            percent = self.resize_percent.GetValue() / 100.0
            new_width = int(orig_width * percent)
            new_height = int(orig_height * percent)
            
            size_text = f"{new_width}×{new_height}"
            self.new_size_display.SetValue(size_text)
            
            # Announce the new size for NVDA
            ui.message(_("New size: {} by {} pixels").format(new_width, new_height))
        else:
            self.new_size_display.SetValue("")

    def calculate_estimated_size(self):
        """Calculate estimated output size based on web tool logic."""
        if not self.selected_files:
            self.estimated_size.SetValue("N/A")
            return
            
        total_estimated_size_mb = 0
        
        for file_path in self.selected_files:
            try:
                original_size_bytes = os.path.getsize(file_path)
                percent = self.resize_percent.GetValue() / 100.0
                
                # Get quality factor based on selection
                quality_selection = self.quality_select.GetStringSelection()
                quality_factor = self.get_quality_factor(quality_selection)
                
                format_name = self.format_select.GetStringSelection().lower()
                
                # Web tool estimation logic
                reduction_factor = math.pow(percent, 2)
                
                if format_name in ['jpeg', 'webp']:
                    # Apply quality factor for lossy formats
                    reduction_factor *= quality_factor
                
                # Heuristic constant for codec efficiency
                reduction_factor *= 0.6
                
                estimated_size = original_size_bytes * reduction_factor
                total_estimated_size_mb += estimated_size / (1024 * 1024)
                
            except Exception as e:
                log.error(f"Error estimating size for {file_path}: {e}")
        
        if total_estimated_size_mb > 0:
            if total_estimated_size_mb < 1:
                size_kb = total_estimated_size_mb * 1024
                size_text = f"{size_kb:.1f} KB (Est.)"
            else:
                size_text = f"{total_estimated_size_mb:.2f} MB (Est.)"
            self.estimated_size.SetValue(size_text)
        else:
            self.estimated_size.SetValue("N/A")

    def get_quality_factor(self, quality_selection):
        """Convert quality selection to numeric factor."""
        quality_map = {
            _("Best (No loss)"): 1.0,
            _("Very Good (Minimal loss)"): 0.9,
            _("Good (Small loss)"): 0.8,
            _("Normal (Balanced)"): 0.7,
            _("Small (Noticeable loss)"): 0.6
        }
        return quality_map.get(quality_selection, 0.8)

    def get_quality_value(self, quality_selection, format_name):
        """Convert quality selection to ffmpeg quality value."""
        if format_name == "jpeg":
            quality_map = {
                _("Best (No loss)"): 2,      # Highest quality
                _("Very Good (Minimal loss)"): 5,
                _("Good (Small loss)"): 10,
                _("Normal (Balanced)"): 15,
                _("Small (Noticeable loss)"): 20
            }
            return quality_map.get(quality_selection, 10)
        elif format_name == "webp":
            quality_map = {
                _("Best (No loss)"): 100,
                _("Very Good (Minimal loss)"): 90,
                _("Good (Small loss)"): 80,
                _("Normal (Balanced)"): 70,
                _("Small (Noticeable loss)"): 60
            }
            return quality_map.get(quality_selection, 80)
        elif format_name == "png":
            quality_map = {
                _("Best (No loss)"): 0,      # No compression
                _("Very Good (Minimal loss)"): 3,
                _("Good (Small loss)"): 6,
                _("Normal (Balanced)"): 7,
                _("Small (Noticeable loss)"): 9
            }
            return quality_map.get(quality_selection, 6)
        else:
            return None

    def update_info_list(self):
        """Update the output information list."""
        self.info_list.DeleteAllItems()
        
        for i, file_path in enumerate(self.selected_files):
            base_name = os.path.basename(file_path)
            output_filename = self.output_filename_text.GetValue().strip()
            if not output_filename:
                output_filename = f"{base_name}_resized"
            
            # Calculate new dimensions
            orig_width, orig_height = self.image_dimensions.get(file_path, (0, 0))
            percent = self.resize_percent.GetValue() / 100.0
            new_width = int(orig_width * percent)
            new_height = int(orig_height * percent)
            
            # Get format
            format_name = self.format_select.GetStringSelection().lower()
            
            # Show output information
            index = self.info_list.InsertItem(i, f"{output_filename}.{format_name}")
            self.info_list.SetItem(index, 1, f"{new_width}×{new_height}")
            self.info_list.SetItem(index, 2, _("Calculating..."))
            self.info_list.SetItem(index, 3, _("Ready"))

    def on_setting_change(self, event):
        """Handle setting changes."""
        wx.CallLater(100, self.update_new_size_display)
        wx.CallLater(100, self.calculate_estimated_size)
        wx.CallLater(100, self.update_info_list)

    def get_file_display_name(self, file_path):
        """Get display name with dimensions and size for listbox."""
        base_name = os.path.basename(file_path)
        width, height = self.image_dimensions.get(file_path, (0, 0))
        size = self.file_sizes.get(file_path, _("Calculating..."))
        if width > 0 and height > 0:
            return f"{base_name} ({width}×{height}, {size})"
        return f"{base_name} ({size})"

    def update_file_listbox(self):
        """Update file listbox with names, dimensions and sizes."""
        self.file_listbox.Set([self.get_file_display_name(f) for f in self.selected_files])

    def on_browse_output(self, event):
        """Browse for output folder."""
        with wx.DirDialog(self, _("Choose output folder"), self.output_path) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.output_path_ctrl.SetValue(dialog.GetPath())
                self.output_path = dialog.GetPath()

    def load_settings(self):
        config_data = load_config(self.config_path)
        self.resize_percent.SetValue(config_data.get("ResizePercent", 100))
        
        quality = config_data.get("ResizeQuality", _("Very Good (Minimal loss)"))
        if quality in [_("Best (No loss)"), _("Very Good (Minimal loss)"), _("Good (Small loss)"), _("Normal (Balanced)"), _("Small (Noticeable loss)")]:
            self.quality_select.SetStringSelection(quality)
        
        format_name = config_data.get("ResizeFormat", "JPEG")
        if format_name in ["JPEG", "WEBP", "PNG", "TIFF", "BMP", "GIF"]:
            self.format_select.SetStringSelection(format_name)
        
        self.update_new_size_display()
        self.calculate_estimated_size()
        self.update_info_list()

    def save_settings(self):
        config_data = load_config(self.config_path)
        config_data["ResizePercent"] = self.resize_percent.GetValue()
        config_data["ResizeQuality"] = self.quality_select.GetStringSelection()
        config_data["ResizeFormat"] = self.format_select.GetStringSelection()
        save_config(self.config_path, config_data)

    def on_start(self, event):
        """Start the resize process."""
        self.save_settings()
        self.output_path = self.output_path_ctrl.GetValue()
        
        if not os.path.exists(self.output_path):
            os.makedirs(self.output_path)
        
        # Set focus to output information list
        self.info_list.SetFocus()
        
        # Add all files to queue
        for file_path in self.selected_files:
            self.resize_queue.put(file_path)
        
        # Start processing
        self.currently_processing = True
        self.total_files = len(self.selected_files)
        self.processed_files = 0
        self.process_next_file()

    def process_next_file(self):
        if self.resize_queue.empty():
            self.currently_processing = False
            wx.CallAfter(self.on_all_processing_complete)
            return
            
        file_path = self.resize_queue.get()
        self.current_file_index = self.selected_files.index(file_path)
        
        # Calculate new dimensions
        orig_width, orig_height = self.image_dimensions.get(file_path, (0, 0))
        percent = self.resize_percent.GetValue() / 100.0
        new_width = max(1, int(orig_width * percent))
        new_height = max(1, int(orig_height * percent))
        
        if new_width == 0 or new_height == 0:
            # Skip files with invalid dimensions
            self.processed_files += 1
            wx.CallAfter(self.update_progress)
            self.process_next_file()
            return
        
        # Prepare output file
        output_filename = self.output_filename_text.GetValue().strip()
        if not output_filename:
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            output_filename = f"{base_name}_resized"
        
        format_name = self.format_select.GetStringSelection().lower()
        output_file = f"{output_filename}.{format_name}"
        output_path = os.path.join(self.output_path, output_file)
        
        ffmpeg_path = os.path.join(self.tools_path, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_path):
            ui.message(_("ffmpeg.exe not found"))
            return
        
        # Build ffmpeg command based on format
        cmd = [
            ffmpeg_path,
            "-i", file_path,
            "-vf", f"scale={new_width}:{new_height}",
            "-y",
        ]
        
        # Add format-specific options
        quality_selection = self.quality_select.GetStringSelection()
        quality_value = self.get_quality_value(quality_selection, format_name)
        
        if format_name == "jpeg" and quality_value is not None:
            cmd.extend(["-q:v", str(quality_value)])
        elif format_name == "webp" and quality_value is not None:
            cmd.extend(["-quality", str(quality_value)])
        elif format_name == "png" and quality_value is not None:
            cmd.extend(["-compression_level", str(quality_value)])
        elif format_name == "tiff":
            cmd.extend(["-compression", "lzw"])
        
        cmd.append(output_path)
        
        wx.CallAfter(self.start_btn.Enable, False)
        wx.CallAfter(self.cancel_btn.Enable, False)
        wx.CallAfter(self.status_label.SetLabel, 
                    _("Processing: {}").format(os.path.basename(file_path)))
        wx.CallAfter(self.current_progress.SetValue, 0)
        
        # Update status in info list
        wx.CallAfter(self.update_processing_status, file_path, _("Processing..."))
        
        def run_resize():
            self.ffmpeg_process = None
            try:
                self.ffmpeg_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    text=True,
                    encoding='utf-8',
                    errors='ignore'
                )
                
                # Monitor progress
                self.ffmpeg_process.wait()
                
                if self.ffmpeg_process.returncode == 0:
                    # Get actual output file size
                    try:
                        output_size_bytes = os.path.getsize(output_path)
                        if output_size_bytes < 1024:
                            output_size_str = f"{output_size_bytes} B"
                        elif output_size_bytes < 1024 * 1024:
                            output_size_str = f"{output_size_bytes/1024:.1f} KB"
                        else:
                            output_size_str = f"{output_size_bytes/(1024*1024):.1f} MB"
                    except:
                        output_size_str = _("Unknown")
                    
                    wx.CallAfter(self.on_file_success, file_path, output_path, output_size_str)
                else:
                    stderr_output = self.ffmpeg_process.stderr.read()
                    wx.CallAfter(self.on_file_failure, file_path, stderr_output)
                    
            except Exception as e:
                wx.CallAfter(self.on_file_failure, file_path, str(e))
            finally:
                if self.ffmpeg_process:
                    self.ffmpeg_process.stdout.close()
                    self.ffmpeg_process.stderr.close()
                self.ffmpeg_process = None
                # Process next file
                if self.currently_processing:
                    self.process_next_file()
        
        threading.Thread(target=run_resize, daemon=True).start()

    def update_processing_status(self, file_path, status):
        """Update processing status in info list."""
        for i in range(self.info_list.GetItemCount()):
            item_text = self.info_list.GetItemText(i)
            if os.path.basename(file_path) in item_text:
                self.info_list.SetItem(i, 3, status)
                break

    def on_file_success(self, file_path, output_path, output_size_str):
        """Handle successful file processing."""
        self.processed_files += 1
        wx.CallAfter(self.current_progress.SetValue, 100)
        
        # Calculate new dimensions for display
        orig_width, orig_height = self.image_dimensions.get(file_path, (0, 0))
        percent = self.resize_percent.GetValue() / 100.0
        new_width = max(1, int(orig_width * percent))
        new_height = max(1, int(orig_height * percent))
        
        # Update info list with final results
        for i in range(self.info_list.GetItemCount()):
            item_text = self.info_list.GetItemText(i)
            if os.path.basename(file_path) in item_text:
                self.info_list.SetItem(i, 1, f"{new_width}×{new_height}")
                self.info_list.SetItem(i, 2, output_size_str)
                self.info_list.SetItem(i, 3, _("Completed"))
                break
        
        # Update progress
        wx.CallAfter(self.update_progress)
        
        # Announce progress and set focus to info list
        wx.CallAfter(self.info_list.SetFocus)
        ui.message(_("Processed: {} - New size: {}").format(os.path.basename(file_path), output_size_str))

    def on_file_failure(self, file_path, error_message):
        """Handle file processing failure."""
        self.processed_files += 1
        
        # Update status in info list
        wx.CallAfter(self.update_processing_status, file_path, _("Failed"))
        
        # Update progress
        wx.CallAfter(self.update_progress)
        
        log.error(f"Failed to process {file_path}: {error_message}")
        ui.message(_("Failed: {}").format(os.path.basename(file_path)))

    def update_progress(self):
        """Update progress bars."""
        if self.total_files > 0:
            total_progress = int((self.processed_files / self.total_files) * 100)
            self.total_progress.SetValue(total_progress)
            
            if self.processed_files == self.total_files:
                self.status_label.SetLabel(_("Complete!"))

    def on_all_processing_complete(self):
        """Handle completion of all file processing."""
        self.start_btn.Enable(True)
        self.cancel_btn.Enable(True)
        self.status_label.SetLabel(_("All images processed!"))
        try:
            tones.beep(1000, 300)
        except Exception:
            pass
        
        # Set focus to output information list and announce completion
        self.info_list.SetFocus()
        ui.message(_("Image processing complete! Check the output information list for results."))

    def on_cancel(self, event):
        """Handle cancel button."""
        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            try:
                self.ffmpeg_process.terminate()
            except Exception:
                pass
        # Clear queue
        while not self.resize_queue.empty():
            try:
                self.resize_queue.get_nowait()
            except queue.Empty:
                break
        self.currently_processing = False
        self.EndModal(wx.ID_CANCEL)

    def on_close(self, event):
        """Handle dialog close."""
        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            try:
                self.ffmpeg_process.terminate()
            except Exception:
                pass
        # Clear queue
        while not self.resize_queue.empty():
            try:
                self.resize_queue.get_nowait()
            except queue.Empty:
                break
        self.currently_processing = False
        self.EndModal(wx.ID_CANCEL)
