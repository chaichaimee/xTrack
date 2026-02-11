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
from .xTrackCore import load_config, save_config, get_file_size
import addonHandler
import core

addonHandler.initTranslation()

class ResizeImageDialog(wx.Dialog):
    """Dialog for resizing images with width and height in pixels."""
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
        self.file_listbox.Bind(wx.EVT_CONTEXT_MENU, self.on_context_menu)
        self.file_listbox.Bind(wx.EVT_KEY_DOWN, self.on_key_down)
        file_list_sizer.Add(self.file_listbox, 1, wx.EXPAND | wx.ALL, 5)
        
        # Delete selected files button
        self.delete_btn = wx.Button(self, label=_("Delete Selected Files"))
        self.delete_btn.Bind(wx.EVT_BUTTON, self.on_delete_selected)
        file_list_sizer.Add(self.delete_btn, 0, wx.EXPAND | wx.ALL, 5)
        file_sizer.Add(file_list_sizer, 1, wx.EXPAND)
        
        # Output filename
        output_filename_sizer = wx.BoxSizer(wx.HORIZONTAL)
        output_filename_label = wx.StaticText(self, label=_("Output Filename Pattern:"))
        output_filename_sizer.Add(output_filename_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.output_filename_text = wx.TextCtrl(self, value="")
        self.output_filename_text.SetHint(_("For single file only"))
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
        
        # Settings section
        settings_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Resize Settings"))
        
        # Width and height controls
        size_sizer = wx.GridBagSizer(5, 5)
        
        # Width
        width_label = wx.StaticText(self, label=_("Width (pixels):"))
        size_sizer.Add(width_label, pos=(0, 0), flag=wx.ALIGN_CENTER_VERTICAL | wx.ALL, border=5)
        self.width_spin = wx.SpinCtrl(self, min=1, max=30000, initial=1920)
        self.width_spin.Bind(wx.EVT_SPINCTRL, self.on_setting_change)
        size_sizer.Add(self.width_spin, pos=(0, 1), flag=wx.EXPAND | wx.ALL, border=5)
        
        # Height
        height_label = wx.StaticText(self, label=_("Height (pixels):"))
        size_sizer.Add(height_label, pos=(1, 0), flag=wx.ALIGN_CENTER_VERTICAL | wx.ALL, border=5)
        self.height_spin = wx.SpinCtrl(self, min=1, max=30000, initial=1080)
        self.height_spin.Bind(wx.EVT_SPINCTRL, self.on_setting_change)
        size_sizer.Add(self.height_spin, pos=(1, 1), flag=wx.EXPAND | wx.ALL, border=5)
        
        # Aspect ratio lock button
        self.aspect_lock_btn = wx.ToggleButton(self, label=_("Lock Aspect Ratio"))
        self.aspect_lock_btn.SetValue(True)
        self.aspect_lock_btn.Bind(wx.EVT_TOGGLEBUTTON, self.on_aspect_lock_toggle)
        size_sizer.Add(self.aspect_lock_btn, pos=(0, 2), span=(2, 1), flag=wx.EXPAND | wx.ALL, border=5)
        
        settings_sizer.Add(size_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Crop settings
        crop_sizer = wx.BoxSizer(wx.VERTICAL)
        self.crop_checkbox = wx.CheckBox(self, label=_("Crop Image"))
        self.crop_checkbox.SetValue(False)
        self.crop_checkbox.Bind(wx.EVT_CHECKBOX, self.on_crop_toggle)
        crop_sizer.Add(self.crop_checkbox, 0, wx.EXPAND | wx.ALL, 5)
        
        # Crop controls (initially hidden)
        self.crop_controls_sizer = wx.GridBagSizer(5, 5)
        
        # Top crop
        top_label = wx.StaticText(self, label=_("Top (pixels):"))
        self.crop_controls_sizer.Add(top_label, pos=(0, 0), flag=wx.ALIGN_CENTER_VERTICAL | wx.ALL, border=5)
        self.crop_top_spin = wx.SpinCtrl(self, min=0, max=10000, initial=0)
        self.crop_top_spin.Bind(wx.EVT_SPINCTRL, self.on_setting_change)
        self.crop_controls_sizer.Add(self.crop_top_spin, pos=(0, 1), flag=wx.EXPAND | wx.ALL, border=5)
        
        # Bottom crop
        bottom_label = wx.StaticText(self, label=_("Bottom (pixels):"))
        self.crop_controls_sizer.Add(bottom_label, pos=(1, 0), flag=wx.ALIGN_CENTER_VERTICAL | wx.ALL, border=5)
        self.crop_bottom_spin = wx.SpinCtrl(self, min=0, max=10000, initial=0)
        self.crop_bottom_spin.Bind(wx.EVT_SPINCTRL, self.on_setting_change)
        self.crop_controls_sizer.Add(self.crop_bottom_spin, pos=(1, 1), flag=wx.EXPAND | wx.ALL, border=5)
        
        # Left crop
        left_label = wx.StaticText(self, label=_("Left (pixels):"))
        self.crop_controls_sizer.Add(left_label, pos=(0, 2), flag=wx.ALIGN_CENTER_VERTICAL | wx.ALL, border=5)
        self.crop_left_spin = wx.SpinCtrl(self, min=0, max=10000, initial=0)
        self.crop_left_spin.Bind(wx.EVT_SPINCTRL, self.on_setting_change)
        self.crop_controls_sizer.Add(self.crop_left_spin, pos=(0, 3), flag=wx.EXPAND | wx.ALL, border=5)
        
        # Right crop
        right_label = wx.StaticText(self, label=_("Right (pixels):"))
        self.crop_controls_sizer.Add(right_label, pos=(1, 2), flag=wx.ALIGN_CENTER_VERTICAL | wx.ALL, border=5)
        self.crop_right_spin = wx.SpinCtrl(self, min=0, max=10000, initial=0)
        self.crop_right_spin.Bind(wx.EVT_SPINCTRL, self.on_setting_change)
        self.crop_controls_sizer.Add(self.crop_right_spin, pos=(1, 3), flag=wx.EXPAND | wx.ALL, border=5)
        
        crop_sizer.Add(self.crop_controls_sizer, 0, wx.EXPAND | wx.ALL, 5)
        self.crop_controls_sizer.ShowItems(show=False)
        
        settings_sizer.Add(crop_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Quality setting
        quality_sizer = wx.BoxSizer(wx.HORIZONTAL)
        quality_label = wx.StaticText(self, label=_("Quality:"))
        quality_sizer.Add(quality_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        quality_choices = [_("Best (No loss)"), _("Very Good"), _("Good"), _("Normal"), _("Small")]
        self.quality_select = wx.ComboBox(self, choices=quality_choices, style=wx.CB_READONLY)
        self.quality_select.SetStringSelection(_("Normal"))
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
        
        main_sizer.Add(settings_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Output Information section
        info_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Output Information"))
        self.info_list = wx.ListCtrl(self, style=wx.LC_REPORT)
        self.info_list.InsertColumn(0, _("Output Name"), width=250)
        self.info_list.InsertColumn(1, _("Dimensions"), width=120)
        self.info_list.InsertColumn(2, _("File Size"), width=100)
        self.info_list.InsertColumn(3, _("Status"), width=120)
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
        self.SetSize((800, 650))
        
        # Update file info table and output filename state
        self.update_info_list()
        self.update_output_filename_state()

    def get_unique_file_path(self, path):
        """Generate a unique file path by adding a number if the file already exists."""
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        i = 1
        while True:
            new_path = f"{base} ({i}){ext}"
            if not os.path.exists(new_path):
                return new_path
            i += 1

    def on_context_menu(self, event):
        """Show context menu for file listbox."""
        menu = wx.Menu()
        delete_item = menu.Append(wx.ID_ANY, _("Delete Selected Files"))
        # Use deferred execution for translation scope
        core.callLater(0, self._bind_context_menu, menu, delete_item)
        self.PopupMenu(menu)
        menu.Destroy()

    def _bind_context_menu(self, menu, delete_item):
        """Bind context menu event with proper translation scope."""
        self.Bind(wx.EVT_MENU, self.on_delete_selected, delete_item)

    def on_key_down(self, event):
        """Handle key events in file listbox."""
        if event.GetKeyCode() == wx.WXK_DELETE:
            self.on_delete_selected(event)
        else:
            event.Skip()

    def on_delete_selected(self, event):
        """Delete selected files from the list."""
        selections = self.file_listbox.GetSelections()
        if not selections:
            return
        
        # Remove files in reverse order to maintain indices
        for idx in sorted(selections, reverse=True):
            if idx < len(self.selected_files):
                del self.selected_files[idx]
        
        # Update listbox
        self.file_listbox.Set([self.get_file_display_name(f) for f in self.selected_files])
        
        # Update info list and output filename state
        self.update_info_list()
        self.update_output_filename_state()
        
        # Update dialog title
        self.SetTitle(_("Resize Image: {} files").format(len(self.selected_files)))
        
        # Disable start button if no files
        if not self.selected_files:
            self.start_btn.Enable(False)
        
        ui.message(_("{} files removed").format(len(selections)))

    def on_crop_toggle(self, event):
        """Handle crop checkbox toggle."""
        is_checked = self.crop_checkbox.GetValue()
        if is_checked:
            self.crop_controls_sizer.ShowItems(show=True)
        else:
            self.crop_controls_sizer.ShowItems(show=False)
        self.crop_controls_sizer.Layout()
        self.Layout()
        self.on_setting_change(event)

    def on_aspect_lock_toggle(self, event):
        """Handle aspect ratio lock toggle."""
        self.on_setting_change(event)

    def update_output_filename_state(self):
        """Enable or disable output filename field based on number of files."""
        if len(self.selected_files) > 1:
            self.output_filename_text.Enable(False)
            self.output_filename_text.SetValue("")
        else:
            self.output_filename_text.Enable(True)

    def on_file_selection_change(self, event):
        """Handle file selection change in the listbox."""
        selections = self.file_listbox.GetSelections()
        if selections:
            self.current_file_index = selections[0]
            self.update_output_info()

    def update_output_info(self):
        """Update output information based on current settings."""
        if not self.selected_files:
            return
            
        file_path = self.selected_files[self.current_file_index]
        
        # Set default output filename for single file
        if len(self.selected_files) == 1 and not self.output_filename_text.GetValue().strip():
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            self.output_filename_text.SetValue(f"{base_name}_resized")
        
        # Set width and height to current image dimensions
        width, height = self.image_dimensions.get(file_path, (0, 0))
        if width > 0 and height > 0:
            self.width_spin.SetValue(width)
            self.height_spin.SetValue(height)
        
        self.update_info_list()

    def calculate_final_dimensions(self, orig_width, orig_height):
        """Calculate final dimensions after crop and resize with aspect ratio lock."""
        # Calculate crop dimensions if enabled
        crop_width = orig_width
        crop_height = orig_height
        
        if self.crop_checkbox.GetValue():
            crop_top = self.crop_top_spin.GetValue()
            crop_bottom = self.crop_bottom_spin.GetValue()
            crop_left = self.crop_left_spin.GetValue()
            crop_right = self.crop_right_spin.GetValue()
            
            # Calculate crop width and height
            crop_width = orig_width - (crop_left + crop_right)
            crop_height = orig_height - (crop_top + crop_bottom)
            
            # Validate crop dimensions
            if crop_width <= 0 or crop_height <= 0:
                # Invalid crop dimensions, use original
                crop_width = orig_width
                crop_height = orig_height
        
        # Calculate target dimensions with aspect ratio lock
        base_width = crop_width
        base_height = crop_height
        
        target_width = self.width_spin.GetValue()
        target_height = self.height_spin.GetValue()
        
        if self.aspect_lock_btn.GetValue():
            orig_aspect = base_width / base_height
            target_aspect = target_width / target_height
            
            if target_aspect > orig_aspect:
                # Height is limiting factor
                target_width = int(target_height * orig_aspect)
            else:
                # Width is limiting factor
                target_height = int(target_width / orig_aspect)
        
        return target_width, target_height

    def update_info_list(self):
        """Update the output information list."""
        self.info_list.DeleteAllItems()
        
        format_name = self.format_select.GetStringSelection().lower()
        
        for i, file_path in enumerate(self.selected_files):
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            
            # Determine output filename
            if len(self.selected_files) > 1:
                output_filename = f"{base_name}_resized.{format_name}"
            else:
                custom_name = self.output_filename_text.GetValue().strip()
                if custom_name:
                    output_filename = f"{custom_name}.{format_name}"
                else:
                    output_filename = f"{base_name}_resized.{format_name}"
            
            # Calculate final dimensions
            orig_width, orig_height = self.image_dimensions.get(file_path, (0, 0))
            if orig_width > 0 and orig_height > 0:
                final_width, final_height = self.calculate_final_dimensions(orig_width, orig_height)
                dimensions_str = f"{final_width} x {final_height}"
            else:
                dimensions_str = f"{self.width_spin.GetValue()} x {self.height_spin.GetValue()}"
            
            # Insert item
            index = self.info_list.InsertItem(i, output_filename)
            self.info_list.SetItem(index, 1, dimensions_str)
            self.info_list.SetItem(index, 2, _("Calculating..."))
            self.info_list.SetItem(index, 3, _("Ready"))

    def on_setting_change(self, event):
        """Handle setting changes."""
        # Handle aspect ratio lock
        if self.aspect_lock_btn.GetValue() and self.selected_files:
            obj = event.GetEventObject()
            file_path = self.selected_files[self.current_file_index]
            orig_width, orig_height = self.image_dimensions.get(file_path, (0, 0))
            
            if orig_width > 0 and orig_height > 0:
                aspect_ratio = orig_width / orig_height
                
                if obj == self.width_spin:
                    # Adjust height based on new width
                    new_height = int(self.width_spin.GetValue() / aspect_ratio)
                    self.height_spin.SetValue(new_height)
                elif obj == self.height_spin:
                    # Adjust width based on new height
                    new_width = int(self.height_spin.GetValue() * aspect_ratio)
                    self.width_spin.SetValue(new_width)
        
        # Update UI with slight delay
        wx.CallLater(100, self.update_info_list)

    def get_file_display_name(self, file_path):
        """Get display name with dimensions and size for listbox."""
        base_name = os.path.basename(file_path)
        width, height = self.image_dimensions.get(file_path, (0, 0))
        size = self.file_sizes.get(file_path, _("Calculating..."))
        if width > 0 and height > 0:
            return f"{base_name} ({width} x {height}, {size})"
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
        """Load saved settings."""
        config_data = load_config(self.config_path)
        self.width_spin.SetValue(config_data.get("ResizeWidth", 1920))
        self.height_spin.SetValue(config_data.get("ResizeHeight", 1080))
        self.aspect_lock_btn.SetValue(config_data.get("AspectLock", True))
        
        # Crop settings
        self.crop_checkbox.SetValue(config_data.get("CropEnabled", False))
        self.crop_top_spin.SetValue(config_data.get("CropTop", 0))
        self.crop_bottom_spin.SetValue(config_data.get("CropBottom", 0))
        self.crop_left_spin.SetValue(config_data.get("CropLeft", 0))
        self.crop_right_spin.SetValue(config_data.get("CropRight", 0))
        
        # Show/hide crop controls based on saved state
        if self.crop_checkbox.GetValue():
            self.crop_controls_sizer.ShowItems(show=True)
        else:
            self.crop_controls_sizer.ShowItems(show=False)
        self.crop_controls_sizer.Layout()
        
        quality = config_data.get("ResizeQuality", _("Normal"))
        if quality in [_("Best (No loss)"), _("Very Good"), _("Good"), _("Normal"), _("Small")]:
            self.quality_select.SetStringSelection(quality)
        
        format_name = config_data.get("ResizeFormat", "JPEG")
        if format_name in ["JPEG", "WEBP", "PNG", "TIFF", "BMP", "GIF"]:
            self.format_select.SetStringSelection(format_name)
        
        self.update_info_list()

    def save_settings(self):
        """Save current settings."""
        config_data = load_config(self.config_path)
        config_data["ResizeWidth"] = self.width_spin.GetValue()
        config_data["ResizeHeight"] = self.height_spin.GetValue()
        config_data["AspectLock"] = self.aspect_lock_btn.GetValue()
        config_data["CropEnabled"] = self.crop_checkbox.GetValue()
        config_data["CropTop"] = self.crop_top_spin.GetValue()
        config_data["CropBottom"] = self.crop_bottom_spin.GetValue()
        config_data["CropLeft"] = self.crop_left_spin.GetValue()
        config_data["CropRight"] = self.crop_right_spin.GetValue()
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
        """Process next file in the queue."""
        if self.resize_queue.empty():
            self.currently_processing = False
            wx.CallAfter(self.on_all_processing_complete)
            return
            
        file_path = self.resize_queue.get()
        self.current_file_index = self.selected_files.index(file_path)
        
        # Get original dimensions
        orig_width, orig_height = self.image_dimensions.get(file_path, (0, 0))
        if orig_width <= 0 or orig_height <= 0:
            # Skip files with invalid dimensions
            self.processed_files += 1
            wx.CallAfter(self.update_progress)
            self.process_next_file()
            return
        
        # Calculate crop dimensions if enabled
        crop_width = orig_width
        crop_height = orig_height
        crop_top = 0
        crop_left = 0
        
        if self.crop_checkbox.GetValue():
            crop_top = self.crop_top_spin.GetValue()
            crop_bottom = self.crop_bottom_spin.GetValue()
            crop_left = self.crop_left_spin.GetValue()
            crop_right = self.crop_right_spin.GetValue()
            
            # Calculate crop width and height
            crop_width = orig_width - (crop_left + crop_right)
            crop_height = orig_height - (crop_top + crop_bottom)
            
            # Validate crop dimensions
            if crop_width <= 0 or crop_height <= 0:
                # Invalid crop dimensions, skip crop
                ui.message(_("Warning: Invalid crop dimensions for {}. Crop disabled for this image.").format(os.path.basename(file_path)))
                crop_width = orig_width
                crop_height = orig_height
                crop_top = 0
                crop_left = 0
        
        # Calculate target dimensions with aspect ratio lock
        # We need to use the cropped dimensions as base if crop is enabled
        base_width = crop_width
        base_height = crop_height
        
        target_width = self.width_spin.GetValue()
        target_height = self.height_spin.GetValue()
        
        if self.aspect_lock_btn.GetValue():
            orig_aspect = base_width / base_height
            target_aspect = target_width / target_height
            
            if target_aspect > orig_aspect:
                # Height is limiting factor
                target_width = int(target_height * orig_aspect)
            else:
                # Width is limiting factor
                target_height = int(target_width / orig_aspect)
        
        # Prepare output file
        format_name = self.format_select.GetStringSelection().lower()
        ext = "jpg" if format_name == "jpeg" else format_name
        
        if len(self.selected_files) > 1:
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            output_filename = f"{base_name}_resized.{ext}"
        else:
            custom_name = self.output_filename_text.GetValue().strip()
            if custom_name:
                output_filename = f"{custom_name}.{ext}"
            else:
                base_name = os.path.splitext(os.path.basename(file_path))[0]
                output_filename = f"{base_name}_resized.{ext}"
        
        output_path = self.get_unique_file_path(os.path.join(self.output_path, output_filename))
        
        ffmpeg_path = os.path.join(self.tools_path, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_path):
            ui.message(_("ffmpeg.exe not found"))
            return
        
        # Build ffmpeg command
        cmd = [
            ffmpeg_path,
            "-i", file_path,
        ]
        
        # Add crop filter if enabled
        filter_chain = []
        
        if self.crop_checkbox.GetValue() and (crop_width != orig_width or crop_height != orig_height):
            crop_filter = f"crop={crop_width}:{crop_height}:{crop_left}:{crop_top}"
            filter_chain.append(crop_filter)
        
        # Always add scale filter
        scale_filter = f"scale={target_width}:{target_height}"
        filter_chain.append(scale_filter)
        
        # Combine filters
        if filter_chain:
            cmd.extend(["-vf", ",".join(filter_chain)])
        
        cmd.append("-y")
        
        # Add format-specific options
        quality_selection = self.quality_select.GetStringSelection()
        
        # Quality mapping for different formats
        quality_map = {
            _("Best (No loss)"): {"jpeg": 2, "webp": 100, "png": 0, "tiff": 0, "bmp": 0, "gif": 0},
            _("Very Good"): {"jpeg": 5, "webp": 90, "png": 2, "tiff": 2, "bmp": 0, "gif": 0},
            _("Good"): {"jpeg": 10, "webp": 80, "png": 4, "tiff": 4, "bmp": 0, "gif": 0},
            _("Normal"): {"jpeg": 15, "webp": 70, "png": 6, "tiff": 6, "bmp": 0, "gif": 0},
            _("Small"): {"jpeg": 20, "webp": 60, "png": 9, "tiff": 9, "bmp": 0, "gif": 0},
        }
        
        quality_value = quality_map.get(quality_selection, quality_map[_("Normal")]).get(format_name, 0)
        
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
                    
                    wx.CallAfter(self.on_file_success, file_path, output_path, output_size_str, target_width, target_height)
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

    def on_file_success(self, file_path, output_path, output_size_str, new_width, new_height):
        """Handle successful file processing."""
        self.processed_files += 1
        wx.CallAfter(self.current_progress.SetValue, 100)
        
        # Update info list with final results
        for i in range(self.info_list.GetItemCount()):
            item_text = self.info_list.GetItemText(i)
            if os.path.basename(file_path) in item_text:
                self.info_list.SetItem(i, 1, f"{new_width} x {new_height}")
                self.info_list.SetItem(i, 2, output_size_str)
                self.info_list.SetItem(i, 3, _("Completed"))
                break
        
        # Update progress
        wx.CallAfter(self.update_progress)
        
        # Announce progress and set focus to info list
        wx.CallAfter(self.info_list.SetFocus)
        ui.message(_("Processed: {} - New size: {}, Dimensions: {} x {}").format(
            os.path.basename(file_path), output_size_str, new_width, new_height))

    def on_file_failure(self, file_path, error_message):
        """Handle file processing failure."""
        self.processed_files += 1
        
        # Update status in info list
        wx.CallAfter(self.update_processing_status, file_path, _("Failed"))
        
        # Update progress
        wx.CallAfter(self.update_progress)
        
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