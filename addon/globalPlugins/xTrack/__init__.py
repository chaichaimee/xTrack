# __init__.py
# Copyright (C) 2026 Chai Chaimee
# Licensed under GNU General Public License. See COPYING.txt for details.

import addonHandler
import globalPluginHandler
import scriptHandler
import ui
import wx
import os
import gui
import api
import comtypes.client
import sys
import json
import core
import shutil
from logHandler import log

addonHandler.initTranslation()

from . import overlay_loader

import config

OLD_CONFIG_DIR = config.getUserDefaultConfigPath()
OLD_CONFIG_FILE = os.path.join(OLD_CONFIG_DIR, "xTrack.json")
NEW_CONFIG_DIR = os.path.join(OLD_CONFIG_DIR, "ChaiChaimee")
NEW_CONFIG_FILE = os.path.join(NEW_CONFIG_DIR, "xTrack.json")

if os.path.exists(OLD_CONFIG_FILE) and not os.path.exists(NEW_CONFIG_FILE):
	try:
		os.makedirs(NEW_CONFIG_DIR, exist_ok=True)
		shutil.move(OLD_CONFIG_FILE, NEW_CONFIG_FILE)
		log.info(f"Migrated config from {OLD_CONFIG_FILE} to {NEW_CONFIG_FILE}")
	except Exception as e:
		log.error(f"Failed to migrate config: {e}")

# Second-stage migration: group every xTrack file (settings and the MP3 Gain
# history) under its own "xTrack" subfolder instead of sitting loose in
# ChaiChaimee alongside other add-ons' files. Old-path files are removed by
# the move itself.
FINAL_CONFIG_DIR = os.path.join(NEW_CONFIG_DIR, "xTrack")
FINAL_CONFIG_FILE = os.path.join(FINAL_CONFIG_DIR, "xTrack.json")
OLD_GAIN_HISTORY_FILE = os.path.join(NEW_CONFIG_DIR, "mp3gain_history.json")
FINAL_GAIN_HISTORY_FILE = os.path.join(FINAL_CONFIG_DIR, "gain.json")

if os.path.exists(NEW_CONFIG_FILE) and not os.path.exists(FINAL_CONFIG_FILE):
	try:
		os.makedirs(FINAL_CONFIG_DIR, exist_ok=True)
		shutil.move(NEW_CONFIG_FILE, FINAL_CONFIG_FILE)
		log.info(f"Migrated config from {NEW_CONFIG_FILE} to {FINAL_CONFIG_FILE}")
	except Exception as e:
		log.error(f"Failed to migrate config to xTrack subfolder: {e}")

if os.path.exists(OLD_GAIN_HISTORY_FILE) and not os.path.exists(FINAL_GAIN_HISTORY_FILE):
	try:
		os.makedirs(FINAL_CONFIG_DIR, exist_ok=True)
		shutil.move(OLD_GAIN_HISTORY_FILE, FINAL_GAIN_HISTORY_FILE)
		log.info(f"Migrated MP3 Gain history from {OLD_GAIN_HISTORY_FILE} to {FINAL_GAIN_HISTORY_FILE}")
	except Exception as e:
		log.error(f"Failed to migrate MP3 Gain history: {e}")

CONFIG_DIR = FINAL_CONFIG_DIR
CONFIG_FILE = FINAL_CONFIG_FILE

def load_config(config_path=CONFIG_FILE):
	if os.path.exists(config_path):
		try:
			with open(config_path, "r", encoding="utf-8") as f:
				return json.load(f)
		except Exception as e:
			log.error(f"xTrack failed to load config: {e}")
			return {}
	return {}

def save_config(data, config_path=CONFIG_FILE):
	try:
		os.makedirs(os.path.dirname(config_path), exist_ok=True)
		with open(config_path, "w", encoding="utf-8") as f:
			json.dump(data, f, indent=4, ensure_ascii=False)
		log.info(f"Config saved to: {config_path}")
	except Exception as e:
		log.error(f"Failed to save config: {e}")

config_data = load_config()

addon_dir = os.path.dirname(__file__)
libs_dir = os.path.join(addon_dir, "libs")
ffmpeg_path = os.path.join(libs_dir, "ffmpeg.exe")
ffprobe_path = os.path.join(libs_dir, "ffprobe.exe")
if not os.path.exists(ffmpeg_path) or not os.path.exists(ffprobe_path):
	ui.message(_("ffmpeg.exe or ffprobe.exe not found in libs folder"))
	log.error("xTrack: ffmpeg.exe or ffprobe.exe missing in libs directory.")

if "record" not in config_data:
	config_data["record"] = {
		"recordingMode": "system_and_mic",
		"format": "mp3",
		"mp3Quality": 192,
		"countIn": False,
		"openFolderAfter": False,
		"noiseSuppression": False,
		"noiseReductionPreset": "medium",
		"humRemoval": False,
		"clarityBoost": False,
		"dynamicCompression": False,
		"limiter": True,
		"systemVolume": 100,
		"microphoneVolume": 100,
		"audioProcessing": "default",
		"highPassFilter": False,
		"noiseGate": False,
		"microphoneDevice": "",
		"systemDevice": "",
		"autoDucking": False,
		"duckingLevel": -18,
		"duckingThreshold": -30,
		"duckingAttack": 5,
		"duckingRelease": 250,
		"destinationFolder": os.path.expanduser("~/xTrack_recordings")
	}
	save_config(config_data)

if "audioProcessing" not in config_data["record"]:
	config_data["record"]["audioProcessing"] = "default"
	save_config(config_data)

if "microphoneVolume" not in config_data["record"]:
	config_data["record"]["microphoneVolume"] = 100
	save_config(config_data)

if "systemVolume" not in config_data["record"]:
	config_data["record"]["systemVolume"] = 100
	save_config(config_data)

if "highPassFilter" not in config_data["record"]:
	config_data["record"]["highPassFilter"] = False
	save_config(config_data)

if "noiseGate" not in config_data["record"]:
	config_data["record"]["noiseGate"] = False
	save_config(config_data)

if "microphoneDevice" not in config_data["record"]:
	config_data["record"]["microphoneDevice"] = ""
	save_config(config_data)

if "systemDevice" not in config_data["record"]:
	config_data["record"]["systemDevice"] = ""
	save_config(config_data)

if "autoDucking" not in config_data["record"]:
	config_data["record"]["autoDucking"] = False
	config_data["record"]["duckingLevel"] = -18
	config_data["record"]["duckingThreshold"] = -30
	config_data["record"]["duckingAttack"] = 5
	config_data["record"]["duckingRelease"] = 250
	save_config(config_data)

if "xTrack" not in config.conf:
	config.conf["xTrack"] = {}
if "record" not in config.conf["xTrack"]:
	config.conf["xTrack"]["record"] = {}
for key, value in config_data["record"].items():
	config.conf["xTrack"]["record"][key] = value

from . import record

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	scriptCategory = "xTrack"

	def __init__(self):
		super(GlobalPlugin, self).__init__()
		self.libs_path = os.path.join(os.path.dirname(__file__), "libs")
		self.ffmpeg_exe = os.path.join(self.libs_path, "ffmpeg.exe")
		self.ffprobe_exe = os.path.join(self.libs_path, "ffprobe.exe")

	__gestures = {
		"kb:alt+windows+x": "present_xtrack_menu",
		"kb:control+shift+space": "toggleRecordPause",
		"kb:control+windows+space": "stopRecord",
	}

	@scriptHandler.script(
		description="xTrack context menu",
		category="xTrack",
		canPropagate=True,
	)
	def script_present_xtrack_menu(self, gesture):
		core.callLater(0, self._present_xtrack_menu_deferred)

	def _present_xtrack_menu_deferred(self):
		focused_object = api.getFocusObject()
	   
		app_name = getattr(getattr(focused_object, "appModule", None), "appName", "").lower()
		in_explorer = focused_object and app_name == "explorer"
	   
		# Audio: includes Android voice-memo formats (.amr, .3ga), iPhone Core
		# Audio Format (.caf), and .opus used by many messaging apps. Also
		# includes .m4r (iPhone ringtone), .alac (Apple Lossless), .aiff/.aif
		# (Mac/iOS), and raw .pcm captures.
		supported_audio_exts = [".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".amr", ".caf", ".opus", ".3ga", ".wma", ".m4r", ".alac", ".aiff", ".aif", ".pcm"]
		# Video: includes .3g2 (an alternate 3GPP variant used on some phones),
		# .m3u8 (HLS playlist output from screen recorders and streaming apps),
		# and .ogv for completeness alongside the existing container formats.
		# Also includes raw H.264/HEVC elementary streams (.h264, .hevc, .264,
		# .265) commonly produced by modern phone cameras, and .vob from DVDs.
		supported_video_exts = [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".3gp", ".3g2", ".ts", ".mts", ".m2ts", ".m3u8", ".ogv", ".h264", ".hevc", ".264", ".265", ".vob"]
		# Image: includes .heic/.heif, the default photo format on modern
		# iPhones. Also includes .jxl (JPEG XL), .dng/.raw (phone ProRAW /
		# Android RAW), and .apng.
		supported_image_exts = [".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp", ".avif", ".gif", ".ico", ".svg", ".heic", ".heif", ".jxl", ".dng", ".raw", ".apng"]
		supported_all_exts = supported_audio_exts + supported_video_exts + supported_image_exts
		selected_files = []
		valid_files = []
	   
		if in_explorer:
			selected_files = self.getSelectedFiles()
			log.info(f"Selected files: {selected_files}")
		   
			valid_files = [f for f in selected_files if os.path.splitext(f)[1].lower() in supported_all_exts]
			log.info(f"Valid files: {valid_files}")
			if not valid_files and len(selected_files) == 1 and os.path.isdir(selected_files[0]):
				folder_path = selected_files[0]
				try:
					valid_files = [
						os.path.join(folder_path, f)
						for f in os.listdir(folder_path)
						if os.path.splitext(f)[1].lower() in supported_all_exts
					]
					valid_files.sort()
				except Exception as e:
					log.error(f"xTrack failed to list folder: {e}")

		menu = wx.Menu()
	   
		has_audio_files = any(f.lower().endswith(tuple(supported_audio_exts)) for f in valid_files)
		has_video_files = any(f.lower().endswith(tuple(supported_video_exts)) for f in valid_files)
		has_image_files = any(f.lower().endswith(tuple(supported_image_exts)) for f in valid_files)
		has_media_files = has_audio_files or has_video_files
		has_mp3_files = any(f.lower().endswith(".mp3") for f in valid_files)
		multiple_mp3_files = len([f for f in valid_files if f.lower().endswith(".mp3")]) > 1

		# Dynamic labels: when exactly one image/video is selected, probe it
		# right now (single ffprobe call, ~1.5s hard timeout) and fold the
		# result straight into the menu label, so arrowing down to
		# "Image Info"/"Video Info" speaks the dimensions/size/duration
		# immediately -- no need to activate the item and wait for a
		# separate announcement. With zero or multiple files selected we
		# fall back to the plain label, since probing many files up front
		# would delay the menu opening.
		image_info_label = _("Image Info")
		if has_image_files and len(valid_files) == 1:
			from .xTrackCore import get_quick_image_label
			quickLabel = get_quick_image_label(self.libs_path, valid_files[0])
			if quickLabel:
				image_info_label = _("Image Info: {info}").format(info=quickLabel)

		video_info_label = _("Video Info")
		if has_video_files and len(valid_files) == 1:
			from .xTrackCore import get_quick_video_label
			quickLabel = get_quick_video_label(self.libs_path, valid_files[0])
			if quickLabel:
				video_info_label = _("Video Info: {info}").format(info=quickLabel)

		menu_items_config = [
			(_("Convert Audio"), self.openConvertAudioDialog, in_explorer and len(valid_files) >= 1 and has_media_files, "multiple"),
			(_("Convert Video"), self.openConvertVideoDialog, in_explorer and len(valid_files) >= 1 and has_video_files, "multiple"),
			(_("Convert MP3 to MP4"), self.openConvertMP3toMP4Dialog, in_explorer and len(valid_files) >= 1 and has_mp3_files, "single"),
			(_("MP3 Gain"), self.openMP3GainDialog, True, "raw"),
			(_("MP3 WAV Mixdown"), self.openMP3WavMixdownDialog, True, "raw"),
			(_("Merge MP3"), self.openMergeDialog, in_explorer and multiple_mp3_files, "multiple"),
			(_("Trim Audio/Video File"), self.openTrimDialog, in_explorer and len(valid_files) >= 1 and (has_audio_files or has_video_files), "single"),
			(_("Split Audio"), self.openSplitAudioDialog, in_explorer and len(valid_files) == 1 and has_audio_files, "single"),
			(_("Resize Image"), self.openResizeImageDialog, in_explorer and len(valid_files) >= 1 and has_image_files, "multiple"),
			(image_info_label, self.openImageInfo, in_explorer and len(valid_files) >= 1 and has_image_files, "multiple"),
			(video_info_label, self.openVideoInfo, in_explorer and len(valid_files) >= 1 and has_video_files, "multiple"),
			(_("Record Settings"), self.openRecordSettings, True, "none"),
		]

		for label, handler, enabled, arg_type in menu_items_config:
			item = menu.Append(wx.ID_ANY, label)
			menu.Enable(item.GetId(), enabled)
			if arg_type == "multiple":
				menu.Bind(wx.EVT_MENU, lambda e, h=handler, files=valid_files: core.callLater(0, h, files), item)
			elif arg_type == "single":
				menu.Bind(wx.EVT_MENU, lambda e, h=handler, file=valid_files[0] if valid_files else None: core.callLater(0, h, file), item)
			elif arg_type == "raw":
				# Some features (MP3 Gain, MP3 WAV Mixdown) need the raw selection
				# (which may itself be a folder) rather than valid_files,
				# since valid_files only expands one folder level deep and
				# would silently drop nested album subfolders before their
				# own recursive scan ever gets a chance to see them.
				menu.Bind(wx.EVT_MENU, lambda e, h=handler, files=selected_files: core.callLater(0, h, files), item)
			else:
				menu.Bind(wx.EVT_MENU, lambda e, h=handler: core.callLater(0, h), item)

		frame = wx.Frame(None, -1, "", pos=(0, 0), size=(0, 0))
		frame.Show()
		frame.Raise()

		def show_menu():
			frame.PopupMenu(menu)
			menu.Destroy()
			frame.Destroy()

		wx.CallAfter(show_menu)

	def getSelectedFiles(self):
		try:
			fg = api.getForegroundObject()
			if not (fg.appModule and fg.appModule.appName == "explorer"):
				return []
		   
			shell = comtypes.client.CreateObject("Shell.Application")
		   
			target_window = None
			for window in shell.Windows():
				try:
					if window.hwnd == fg.windowHandle:
						target_window = window
						break
				except Exception:
					continue
		   
			if not target_window:
				return []
		   
			paths = []
		   
			try:
				selected_items = target_window.Document.SelectedItems()
				if selected_items:
					for i in range(selected_items.Count):
						try:
							item = selected_items.Item(i)
							if hasattr(item, 'Path'):
								path = item.Path
								if path and os.path.exists(path):
									paths.append(path)
						except Exception as e:
							log.error(f"Error getting selected item {i}: {e}")
			except Exception as e:
				log.warning(f"Could not get selected items: {e}")
		   
			if not paths:
				try:
					focused_item = target_window.Document.FocusedItem
					if focused_item and hasattr(focused_item, 'Path'):
						path = focused_item.Path
						if path and os.path.exists(path):
							paths.append(path)
				except Exception as e:
					log.warning(f"Could not get focused item: {e}")
		   
			if not paths:
				try:
					folder = target_window.Document.Folder
					if hasattr(folder, 'Self'):
						folder_path = folder.Self.Path
						focused = api.getFocusObject()
						if hasattr(focused, 'name') and focused.name:
							file_name = focused.name
							full_path = os.path.join(folder_path, file_name)
							if os.path.exists(full_path):
								paths.append(full_path)
				except Exception as e:
					log.warning(f"Could not construct path from folder: {e}")
		   
			return paths
		   
		except Exception as e:
			log.error(f"Failed to retrieve selected files: {e}")
			return []

	def openTrimDialog(self, selected_file):
		if not selected_file:
			ui.message(_("Please select an audio or video file first"))
			return
		try:
			def _open():
				from .Trim import TrimAudioVideoDialog
				dialog = TrimAudioVideoDialog(gui.mainFrame, [selected_file], self.libs_path)
				dialog.ShowModal()
				dialog.Destroy()
			wx.CallAfter(_open)
		except Exception as e:
			log.error(f"Failed to open Trim dialog: {e}")
			ui.message(_("Failed to open Trim dialog: {}").format(str(e)))

	def openMergeDialog(self, selected_files):
		if not selected_files or len(selected_files) < 2:
			ui.message(_("Please select at least 2 MP3 files first."))
			return
		try:
			def _open():
				from .merge import MergeAudioDialog
				dialog = MergeAudioDialog(gui.mainFrame, selected_files, self.libs_path)
				dialog.ShowModal()
				dialog.Destroy()
			wx.CallAfter(_open)
		except Exception as e:
			log.error(f"Failed to open Merge dialog: {e}")
			ui.message(_("Failed to open Merge dialog: {}").format(str(e)))

	def openConvertAudioDialog(self, selected_files):
		if not selected_files:
			ui.message(_("Please select audio or video files first."))
			return
		try:
			def _open():
				from .convertAudio import ConvertAudioDialog
				dialog = ConvertAudioDialog(gui.mainFrame, selected_files, self.libs_path)
				dialog.ShowModal()
				dialog.Destroy()
			wx.CallAfter(_open)
		except Exception as e:
			log.error(f"Failed to open Convert Audio dialog: {e}")
			ui.message(_("Failed to open Convert Audio dialog: {}").format(str(e)))

	def openConvertVideoDialog(self, selected_files):
		if not selected_files:
			ui.message(_("Please select video files first."))
			return
		try:
			def _open():
				from .convertVideo import ConvertVideoDialog
				dialog = ConvertVideoDialog(gui.mainFrame, selected_files, self.libs_path)
				dialog.ShowModal()
				dialog.Destroy()
			wx.CallAfter(_open)
		except Exception as e:
			log.error(f"Failed to open Convert Video dialog: {e}")
			ui.message(_("Failed to open Convert Video dialog: {}").format(str(e)))

	def openResizeImageDialog(self, selected_files):
		if not selected_files:
			ui.message(_("Please select image files first."))
			return
		try:
			def _open():
				from .resizeImage import ResizeImageDialog
				dialog = ResizeImageDialog(gui.mainFrame, selected_files, self.libs_path)
				dialog.ShowModal()
				dialog.Destroy()
			wx.CallAfter(_open)
		except Exception as e:
			log.error(f"Failed to open Resize Image dialog: {e}")
			ui.message(_("Failed to open Resize Image dialog: {}").format(str(e)))

	def openConvertMP3toMP4Dialog(self, selected_file):
		if not selected_file:
			ui.message(_("Please select an MP3 file first."))
			return
		try:
			def _open():
				from .convertAudio import ConvertMP3toMP4Dialog
				dialog = ConvertMP3toMP4Dialog(gui.mainFrame, selected_file, self.libs_path)
				dialog.ShowModal()
				dialog.Destroy()
			wx.CallAfter(_open)
		except Exception as e:
			log.error(f"Failed to open Convert MP3 to MP4 dialog: {e}")
			ui.message(_("Failed to open Convert MP3 to MP4 dialog: {}").format(str(e)))

	def openMP3GainDialog(self, selected_files):
		# No early-exit guard here: the dialog's own Browse button lets the
		# user pick MP3 files after opening, so an empty selection is valid.
		try:
			def _open():
				from .mp3Gain import MP3GainDialog
				dialog = MP3GainDialog(gui.mainFrame, selected_files, self.libs_path)
				dialog.ShowModal()
				dialog.Destroy()
			wx.CallAfter(_open)
		except Exception as e:
			log.error(f"Failed to open MP3 Gain dialog: {e}")
			ui.message(_("Failed to open MP3 Gain dialog: {}").format(str(e)))

	def openMP3WavMixdownDialog(self, selected_files):
		# No early-exit guard here either: the dialog's own Browse buttons
		# let the user pick MP3 or WAV files after opening.
		try:
			def _open():
				from .mp3_mixdown import MP3WavMixdownDialog
				dialog = MP3WavMixdownDialog(gui.mainFrame, selected_files, self.libs_path)
				dialog.ShowModal()
				dialog.Destroy()
			wx.CallAfter(_open)
		except Exception as e:
			log.error(f"Failed to open MP3 WAV Mixdown dialog: {e}")
			ui.message(_("Failed to open MP3 WAV Mixdown dialog: {}").format(str(e)))

	def openSplitAudioDialog(self, selected_file):
		if not selected_file:
			ui.message(_("Please select an audio file first"))
			return
		try:
			def _open():
				from .splitAudio import SplitAudioDialog
				dialog = SplitAudioDialog(gui.mainFrame, selected_file, self.libs_path)
				dialog.ShowModal()
				dialog.Destroy()
			wx.CallAfter(_open)
		except Exception as e:
			log.error(f"Failed to open Split Audio dialog: {e}")
			ui.message(_("Failed to open Split Audio dialog: {}").format(str(e)))

	def openImageInfo(self, selected_files):
		if not selected_files:
			ui.message(_("Please select image files first."))
			return
	   
		try:
			from .image import show_image_info
			show_image_info(selected_files, self.libs_path)
		except Exception as e:
			log.error(f"Failed to get image info: {e}")
			ui.message(_("Failed to get image info: {}").format(str(e)))

	def openVideoInfo(self, selected_files):
		if not selected_files:
			ui.message(_("Please select video files first."))
			return
	   
		try:
			from .video import show_video_info
			show_video_info(selected_files, self.libs_path)
		except Exception as e:
			log.error(f"Failed to get video info: {e}")
			ui.message(_("Failed to get video info: {}").format(str(e)))

	def openRecordSettings(self):
		try:
			def _open():
				dlg = record.RecordSettingsDialog(gui.mainFrame)
				result = dlg.ShowModal()
				if result == wx.ID_OK:
					config_data["record"] = dlg.settings
					save_config(config_data)
				   
					for k, v in dlg.settings.items():
						config.conf["xTrack"]["record"][k] = v
				   
					ui.message(_("Record settings saved"))
				dlg.Destroy()
			wx.CallAfter(_open)
		except Exception as e:
			log.error(f"Failed to open Record Settings: {e}")
			ui.message(_("Failed to open Record Settings: {}").format(str(e)))

	@scriptHandler.script(
		description=_("Start recording; press again to pause; press again to continue"),
		category="xTrack"
	)
	def script_toggleRecordPause(self, gesture):
		if not record.recorder.is_recording:
			ui.message(_("start"))
			record.recorder.start()
		elif record.recorder.is_paused:
			record.recorder.resume()
		else:
			record.recorder.pause()

	@scriptHandler.script(
		description=_("Stop recording"),
		category="xTrack"
	)
	def script_stopRecord(self, gesture):
		if not record.recorder.is_recording:
			gesture.send()
			return
		files = record.recorder.stop()
		if files:
			ui.message(_("stopped Saved to: {}").format(os.path.dirname(files[0])))
		else:
			ui.message(_("stopped No file saved"))

	def makeSettings(self, settingsSizer):
		panel = wx.Panel(settingsSizer.GetContainingWindow())
		sizer = wx.BoxSizer(wx.VERTICAL)
		btn = wx.Button(panel, label=_("Record Settings..."))
		btn.Bind(wx.EVT_BUTTON, lambda e: self.openRecordSettings())
		sizer.Add(btn, flag=wx.EXPAND | wx.ALL, border=5)
		panel.SetSizer(sizer)
		settingsSizer.Add(panel, flag=wx.EXPAND)

	def terminate(self):
		if hasattr(record, "recorder") and record.recorder.is_recording:
			try:
				record.recorder.stop()
			except Exception as e:
				log.error(f"xTrack: Error during terminate: {e}")







