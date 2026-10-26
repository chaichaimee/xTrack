# resizeImage.py

import wx
import os
import subprocess
import threading
import ui
import tones
from gui import guiHelper
from .xTrackCore import load_config, save_config, get_file_size, get_config_path
from .ffmpegSupport import limitSpokenText, summarizeFfmpegError, removeQuietly, partPathFor, runFfmpeg
from .floatingMenu import chooseMenuAction
import addonHandler
from logHandler import log

addonHandler.initTranslation()

INVALID_FILENAME_CHARS = '<>:"/\\|?*'
QUALITY_KEYS = ("Best (No loss)", "Very Good", "Good", "Normal", "Small")
QUALITY_VALUES = {
	"Best (No loss)": {"jpeg": 2, "webp": 100, "png": 0, "tiff": 0, "bmp": 0, "gif": 0},
	"Very Good": {"jpeg": 5, "webp": 90, "png": 2, "tiff": 2, "bmp": 0, "gif": 0},
	"Good": {"jpeg": 10, "webp": 80, "png": 4, "tiff": 4, "bmp": 0, "gif": 0},
	"Normal": {"jpeg": 15, "webp": 70, "png": 6, "tiff": 6, "bmp": 0, "gif": 0},
	"Small": {"jpeg": 20, "webp": 60, "png": 9, "tiff": 9, "bmp": 0, "gif": 0},
}


def formatByteSize(sizeBytes):
	if sizeBytes < 1024:
		return f"{sizeBytes} B"
	if sizeBytes < 1024 * 1024:
		return f"{sizeBytes / 1024:.1f} KB"
	return f"{sizeBytes / (1024 * 1024):.1f} MB"


def fitDimensions(baseWidth, baseHeight, targetWidth, targetHeight, keepAspect):
	"""Final pixel size; never below 1x1, which ffmpeg's scale filter rejects."""
	if keepAspect:
		baseAspect = baseWidth / baseHeight
		if targetWidth / targetHeight > baseAspect:
			targetWidth = int(targetHeight * baseAspect)
		else:
			targetHeight = int(targetWidth / baseAspect)
	return max(1, targetWidth), max(1, targetHeight)


class ResizeImageDialog(wx.Dialog):
	"""Dialog for resizing images with width and height in pixels."""
	def __init__(self, parent, selected_files, libs_path):
		super().__init__(parent, title=_("Resize Image"))
		self.selected_files = list(selected_files)
		self.libs_path = libs_path
		self.output_path = os.path.dirname(self.selected_files[0]) if self.selected_files else os.getcwd()
		self.config_path = get_config_path()
		self.ffmpeg_process = None
		self.currently_processing = False
		self.is_closed = False
		self.cancel_event = threading.Event()
		self.file_sizes = {}
		self.image_dimensions = {}
		# Per-file results shown in the output list; kept here so that a refresh
		# of the list never wipes out what has already been processed.
		self.file_results = {}
		self.current_file_index = 0
		self.init_ui()
		self.SetTitle(_("Resize Image: {} files").format(len(self.selected_files)))
		self.load_settings()
		self.Bind(wx.EVT_CLOSE, self.on_close)
		# Pre-load file info in background
		threading.Thread(target=self.load_file_info, args=(list(self.selected_files),), daemon=True).start()

		# Set focus to file listbox after UI is shown
		wx.CallAfter(self.file_listbox.SetFocus)

	def post(self, handler, *args):
		wx.CallAfter(self.run_if_open, handler, *args)

	def run_if_open(self, handler, *args):
		if self.is_closed:
			return
		handler(*args)

	def load_file_info(self, files):
		"""Load sizes and dimensions for all selected files quickly."""
		for file_path in files:
			if self.is_closed:
				return
			try:
				size_bytes, size_str = get_file_size(file_path)
				self.file_sizes[file_path] = size_str
			except OSError:
				self.file_sizes[file_path] = _("Error")
			self.image_dimensions[file_path] = self.get_image_dimensions_fast(file_path)
		self.post(self.update_file_listbox)
		self.post(self.update_output_info)

	def get_image_dimensions_fast(self, file_path):
		"""Get image dimensions quickly using ffprobe."""
		ffprobe_path = os.path.join(self.libs_path, "ffprobe.exe")
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
				stdin=subprocess.DEVNULL,
				text=True,
				creationflags=subprocess.CREATE_NO_WINDOW,
				encoding='utf-8',
				errors='ignore',
				timeout=10
			)
			if result.returncode == 0 and result.stdout.strip():
				dimensions = result.stdout.strip().splitlines()[0].split(',')
				if len(dimensions) == 2:
					return int(dimensions[0]), int(dimensions[1])
		except (OSError, subprocess.SubprocessError, ValueError) as e:
			log.debugWarning("xTrack: could not read the size of %s: %s", file_path, e)
		return 0, 0

	def init_ui(self):
		main_sizer = wx.BoxSizer(wx.VERTICAL)
		
		# File selection section
		file_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Input Files"))
		file_list_sizer = wx.BoxSizer(wx.VERTICAL)
		file_list_sizer.Add(wx.StaticText(self, label=_("Selected files:")), 0, wx.EXPAND | wx.ALL, 5)
		self.file_listbox = wx.ListBox(self, choices=[self.get_file_display_name(f) for f in self.selected_files], style=wx.LB_MULTIPLE)
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
		chooseMenuAction(self, _("Files"), [(_("Delete Selected Files"), lambda: self.on_delete_selected(None))])

	def on_key_down(self, event):
		"""Handle key events in file listbox."""
		if event.GetKeyCode() == wx.WXK_DELETE:
			self.on_delete_selected(event)
		else:
			event.Skip()

	def on_delete_selected(self, event):
		"""Delete selected files from the list."""
		if self.currently_processing:
			ui.message(_("Resize Image is already processing files"))
			return
		selections = self.file_listbox.GetSelections()
		if not selections:
			return

		# Remove files in reverse order to maintain indices
		for idx in sorted(selections, reverse=True):
			if idx < len(self.selected_files):
				self.file_results.pop(self.selected_files[idx], None)
				del self.selected_files[idx]

		self.current_file_index = min(self.current_file_index, max(len(self.selected_files) - 1, 0))
		self.file_listbox.Set([self.get_file_display_name(f) for f in self.selected_files])
		self.update_info_list()
		self.update_output_filename_state()
		self.SetTitle(_("Resize Image: {} files").format(len(self.selected_files)))
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
		self.current_file_index = min(self.current_file_index, len(self.selected_files) - 1)
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

	def get_crop_box(self, orig_width, orig_height):
		"""(width, height, left, top) of the crop; the whole image when crop is off or leaves nothing."""
		if not self.crop_checkbox.GetValue():
			return orig_width, orig_height, 0, 0
		crop_left = self.crop_left_spin.GetValue()
		crop_top = self.crop_top_spin.GetValue()
		crop_width = orig_width - (crop_left + self.crop_right_spin.GetValue())
		crop_height = orig_height - (crop_top + self.crop_bottom_spin.GetValue())
		if crop_width <= 0 or crop_height <= 0:
			return orig_width, orig_height, 0, 0
		return crop_width, crop_height, crop_left, crop_top

	def calculate_final_dimensions(self, orig_width, orig_height):
		"""Calculate final dimensions after crop and resize with aspect ratio lock."""
		crop_width, crop_height, _left, _top = self.get_crop_box(orig_width, orig_height)
		return fitDimensions(
			crop_width, crop_height, self.width_spin.GetValue(), self.height_spin.GetValue(),
			self.aspect_lock_btn.GetValue(),
		)

	def output_base_name(self, file_path):
		if len(self.selected_files) > 1:
			return f"{os.path.splitext(os.path.basename(file_path))[0]}_resized"
		custom_name = self.output_filename_text.GetValue().strip()
		return custom_name or f"{os.path.splitext(os.path.basename(file_path))[0]}_resized"

	def update_info_list(self):
		"""Update the output information list."""
		self.info_list.DeleteAllItems()
		format_name = self.format_select.GetStringSelection().lower()
		extension = "jpg" if format_name == "jpeg" else format_name

		for i, file_path in enumerate(self.selected_files):
			result = self.file_results.get(file_path)
			if result:
				index = self.info_list.InsertItem(i, result["name"])
				self.info_list.SetItem(index, 1, result["dimensions"])
				self.info_list.SetItem(index, 2, result["size"])
				self.info_list.SetItem(index, 3, result["status"])
				continue

			output_filename = f"{self.output_base_name(file_path)}.{extension}"
			orig_width, orig_height = self.image_dimensions.get(file_path, (0, 0))
			if orig_width > 0 and orig_height > 0:
				final_width, final_height = self.calculate_final_dimensions(orig_width, orig_height)
				dimensions_str = f"{final_width} x {final_height}"
			else:
				dimensions_str = f"{self.width_spin.GetValue()} x {self.height_spin.GetValue()}"

			index = self.info_list.InsertItem(i, output_filename)
			self.info_list.SetItem(index, 1, dimensions_str)
			self.info_list.SetItem(index, 2, self.file_sizes.get(file_path, _("Calculating...")))
			self.info_list.SetItem(index, 3, _("Ready"))

	def on_setting_change(self, event):
		"""Handle setting changes."""
		# Handle aspect ratio lock
		if self.aspect_lock_btn.GetValue() and self.selected_files:
			obj = event.GetEventObject()
			self.current_file_index = min(self.current_file_index, len(self.selected_files) - 1)
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

		# Settings changed after a run: results shown so far no longer describe the next run.
		if not self.currently_processing:
			self.file_results.clear()
		wx.CallLater(100, self.run_if_open, self.update_info_list)

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
		selections = self.file_listbox.GetSelections()
		self.file_listbox.Set([self.get_file_display_name(f) for f in self.selected_files])
		for index in selections:
			if index < len(self.selected_files):
				self.file_listbox.SetSelection(index)

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

	def build_jobs(self):
		"""Everything read from the dialog's controls happens here, on the main thread; returns (jobs, skippedPaths)."""
		ffmpeg_path = os.path.join(self.libs_path, "ffmpeg.exe")
		format_name = self.format_select.GetStringSelection().lower()
		ext = "jpg" if format_name == "jpeg" else format_name
		quality_key = next((key for key in QUALITY_KEYS if _(key) == self.quality_select.GetStringSelection()), "Normal")
		quality_value = QUALITY_VALUES[quality_key].get(format_name, 0)

		jobs = []
		skipped = []
		reservedNames = set()
		for file_path in self.selected_files:
			orig_width, orig_height = self.image_dimensions.get(file_path, (0, 0))
			if orig_width <= 0 or orig_height <= 0:
				# Files whose dimensions could not be read, for example an image
				# codec the bundled ffprobe build cannot decode, are skipped and
				# reported instead of being dropped silently.
				log.error("xTrack: skipping resize for %s: unreadable dimensions", file_path)
				skipped.append(file_path)
				continue

			crop_width, crop_height, crop_left, crop_top = self.get_crop_box(orig_width, orig_height)
			if self.crop_checkbox.GetValue() and (crop_width, crop_height) == (orig_width, orig_height) and (
				self.crop_left_spin.GetValue() or self.crop_right_spin.GetValue()
				or self.crop_top_spin.GetValue() or self.crop_bottom_spin.GetValue()
			):
				ui.message(limitSpokenText(
					_("Warning: Invalid crop dimensions for {}. Crop disabled for this image.").format(os.path.basename(file_path))
				))
			target_width, target_height = fitDimensions(
				crop_width, crop_height, self.width_spin.GetValue(), self.height_spin.GetValue(),
				self.aspect_lock_btn.GetValue(),
			)

			base_name = self.output_base_name(file_path)
			output_filename = f"{base_name}.{ext}"
			output_path = self.get_unique_file_path(os.path.join(self.output_path, output_filename))
			counter = 1
			while output_path.lower() in reservedNames:
				output_path = self.get_unique_file_path(os.path.join(self.output_path, f"{base_name}_{counter}.{ext}"))
				counter += 1
			reservedNames.add(output_path.lower())

			filter_chain = []
			if (crop_width, crop_height) != (orig_width, orig_height):
				filter_chain.append(f"crop={crop_width}:{crop_height}:{crop_left}:{crop_top}")
			filter_chain.append(f"scale={target_width}:{target_height}")

			# -frames:v 1: a video or animated source would otherwise keep
			# writing frames to the same still-image file and fail in the muxer.
			cmd = [ffmpeg_path, "-hide_banner", "-i", file_path, "-vf", ",".join(filter_chain), "-frames:v", "1", "-y"]
			if format_name == "jpeg":
				cmd.extend(["-q:v", str(quality_value)])
			elif format_name == "webp":
				cmd.extend(["-quality", str(quality_value)])
			elif format_name == "png":
				cmd.extend(["-compression_level", str(quality_value)])
			elif format_name == "tiff":
				cmd.extend(["-compression", "lzw"])
			partPath = partPathFor(output_path)
			cmd.append(partPath)
			jobs.append({
				"source": file_path, "output": output_path, "part": partPath, "command": cmd,
				"width": target_width, "height": target_height,
			})
		return jobs, skipped

	def on_start(self, event):
		"""Start the resize process."""
		if self.currently_processing:
			return
		if not self.selected_files:
			ui.message(_("There are no files to resize"))
			return
		if not os.path.exists(os.path.join(self.libs_path, "ffmpeg.exe")):
			ui.message(_("ffmpeg.exe not found"))
			return

		outputFolder = self.output_path_ctrl.GetValue().strip()
		if not outputFolder:
			ui.message(_("Please enter an output folder"))
			return
		if len(self.selected_files) == 1:
			customName = self.output_filename_text.GetValue().strip()
			if any(char in INVALID_FILENAME_CHARS for char in customName):
				ui.message(_("The output filename contains characters that are not allowed."))
				return
		try:
			os.makedirs(outputFolder, exist_ok=True)
		except OSError as error:
			log.error("xTrack: could not create the output folder %s: %s", outputFolder, error)
			ui.message(_("The output folder could not be created"))
			return
		self.output_path = outputFolder
		self.save_settings()

		jobs, skipped = self.build_jobs()
		self.file_results.clear()
		for skippedPath in skipped:
			self.file_results[skippedPath] = {
				"name": os.path.basename(skippedPath), "dimensions": "-", "size": "-", "status": _("Skipped"),
			}
		if skipped:
			ui.message(limitSpokenText(_("Skipped {count} file(s): unsupported or unreadable image format").format(count=len(skipped))))
		if not jobs:
			self.update_info_list()
			return

		for job in jobs:
			self.file_results[job["source"]] = {
				"name": os.path.basename(job["output"]), "dimensions": f"{job['width']} x {job['height']}",
				"size": "-", "status": _("Ready"),
			}
		self.update_info_list()

		# Set focus to output information list
		self.info_list.SetFocus()
		self.currently_processing = True
		self.cancel_event.clear()
		self.total_files = len(jobs) + len(skipped)
		self.processed_files = len(skipped)
		self.update_progress()
		self.start_btn.Enable(False)
		self.cancel_btn.Enable(False)
		threading.Thread(target=self.run_jobs, args=(jobs,), daemon=True).start()

	def run_jobs(self, jobs):
		def registerProcess(process):
			self.ffmpeg_process = process

		failures = 0
		for job in jobs:
			if self.cancel_event.is_set():
				return
			self.post(self.on_job_started, job["source"])
			try:
				exitCode, stderrLines = runFfmpeg(job["command"], self.output_path, 0, lambda percent: None, registerProcess)
			except OSError as error:
				log.error("xTrack: could not run ffmpeg for %s: %s", job["source"], error)
				removeQuietly(job["part"])
				failures += 1
				self.post(self.on_file_failure, job["source"], _("ffmpeg could not be started"))
				continue
			if self.cancel_event.is_set():
				removeQuietly(job["part"])
				return
			if exitCode != 0:
				log.error("xTrack: resize failed for %s (exit code %s): %s",
					job["source"], exitCode, " | ".join(line.strip() for line in stderrLines))
				removeQuietly(job["part"])
				failures += 1
				self.post(self.on_file_failure, job["source"], summarizeFfmpegError(stderrLines))
				continue
			try:
				os.replace(job["part"], job["output"])
				output_size_str = formatByteSize(os.path.getsize(job["output"]))
			except OSError as error:
				log.error("xTrack: could not finish %s: %s", job["output"], error)
				removeQuietly(job["part"])
				failures += 1
				self.post(self.on_file_failure, job["source"], _("the output file could not be written"))
				continue
			self.post(self.on_file_success, job, output_size_str)
		self.post(self.on_all_processing_complete, failures)

	def set_result(self, file_path, status, size=None, dimensions=None):
		result = self.file_results.get(file_path)
		if result is None:
			return
		result["status"] = status
		if size is not None:
			result["size"] = size
		if dimensions is not None:
			result["dimensions"] = dimensions
		if file_path in self.selected_files:
			row = self.selected_files.index(file_path)
			if row < self.info_list.GetItemCount():
				self.info_list.SetItem(row, 1, result["dimensions"])
				self.info_list.SetItem(row, 2, result["size"])
				self.info_list.SetItem(row, 3, result["status"])

	def on_job_started(self, file_path):
		self.current_progress.SetValue(0)
		self.status_label.SetLabel(_("Processing: {}").format(os.path.basename(file_path)))
		self.set_result(file_path, _("Processing..."))

	def on_file_success(self, job, output_size_str):
		"""Handle successful file processing."""
		self.processed_files += 1
		self.current_progress.SetValue(100)
		self.set_result(job["source"], _("Completed"), output_size_str, f"{job['width']} x {job['height']}")
		self.update_progress()
		ui.message(limitSpokenText(_("Processed: {} - New size: {}, Dimensions: {} x {}").format(
			os.path.basename(job["output"]), output_size_str, job["width"], job["height"])))

	def on_file_failure(self, file_path, reason):
		self.processed_files += 1
		self.set_result(file_path, _("Failed"))
		self.update_progress()
		ui.message(limitSpokenText(_("Failed: {name} ({reason})").format(name=os.path.basename(file_path), reason=reason)))

	def update_progress(self):
		"""Update progress bars."""
		if self.total_files > 0:
			self.total_progress.SetValue(int((self.processed_files / self.total_files) * 100))

	def on_all_processing_complete(self, failures):
		"""Handle completion of all file processing."""
		self.currently_processing = False
		self.start_btn.Enable(True)
		self.cancel_btn.Enable(True)
		self.info_list.SetFocus()
		if failures:
			self.status_label.SetLabel(_("Finished with errors"))
			tones.beep(300, 300)
			ui.message(_("Image processing finished, {count} file(s) failed. Check the output information list for results.").format(count=failures))
			return
		self.status_label.SetLabel(_("All images processed!"))
		tones.beep(1000, 300)
		ui.message(_("Image processing complete! Check the output information list for results."))

	def stop_processing(self):
		self.is_closed = True
		self.cancel_event.set()
		process = self.ffmpeg_process
		if process and process.poll() is None:
			try:
				process.terminate()
			except OSError as error:
				log.debugWarning("xTrack: could not stop ffmpeg: %s", error)

	def on_cancel(self, event):
		"""Handle cancel button."""
		self.stop_processing()
		self.EndModal(wx.ID_CANCEL)

	def on_close(self, event):
		"""Handle dialog close."""
		self.stop_processing()
		self.EndModal(wx.ID_CANCEL)
