# __init__.py
# Copyright (C) 2025 ['chai chaimee']
# Licensed under GNU General Public License. See COPYING.txt for details.

import addonHandler
import globalPluginHandler
import scriptHandler
import ui
import wx
import os
import gui
import api
import ctypes
import comtypes.client
import sys
import json
from logHandler import log

addonHandler.initTranslation()

# --- Import config for proper path ---
import config

# --- Config folder ---
CONFIG_DIR = config.getUserDefaultConfigPath()
CONFIG_FILE = os.path.join(CONFIG_DIR, "xTrack.json")

# --- Load/save config ---
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_config(data):
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        log.info(f"Config saved to: {CONFIG_FILE}")
    except Exception as e:
        log.error(f"Failed to save config: {e}")

# --- Load config at startup ---
config_data = load_config()

# --- Add Tools to sys.path ---
addon_dir = os.path.dirname(__file__)
tools_dir = os.path.join(addon_dir, "Tools")
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

# --- Check ffmpeg/ffprobe ---
ffmpeg_path = os.path.join(tools_dir, "ffmpeg.exe")
ffprobe_path = os.path.join(tools_dir, "ffprobe.exe")
if not os.path.exists(ffmpeg_path) or not os.path.exists(ffprobe_path):
    ui.message("ffmpeg.exe or ffprobe.exe not found in Tools folder")
    raise FileNotFoundError("Missing ffmpeg/ffprobe")

# --- Initialize config data ---
if "record" not in config_data:
    config_data["record"] = {
        "recordingMode": "system_and_mic",
        "format": "mp3",
        "mp3Quality": 192,
        "countIn": False,
        "openFolderAfter": False,
        "noiseSuppression": False,
        "noiseReductionPreset": "medium",
        "systemGain": 0,
        "microphoneGain": 0,
        "destinationFolder": os.path.expanduser("~/xTrack_recordings")
    }
    save_config(config_data)

# Update config.conf to match JSON
if "xTrack" not in config.conf:
    config.conf["xTrack"] = {}
if "record" not in config.conf["xTrack"]:
    config.conf["xTrack"]["record"] = {}

for key, value in config_data["record"].items():
    config.conf["xTrack"]["record"][key] = value

# Import record module after config is ready
from . import record

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    scriptCategory = "xTrack"

    def __init__(self):
        super(GlobalPlugin, self).__init__()
        self.tools_path = os.path.join(os.path.dirname(__file__), "Tools")

    __gestures = {
        "kb:NVDA+X": "present_xtrack_menu",
        "kb:control+shift+space": "toggleRecordPause",
        "kb:control+windows+space": "stopRecord",
    }

    @scriptHandler.script(
        gesture="kb:NVDA+x",
        description="xTrack context menu",
        category="xTrack",
        canPropagate=True,
    )
    def script_present_xtrack_menu(self, gesture):
        focused_object = api.getFocusObject()
        
        # Check if we're in File Explorer for file operations
        in_explorer = focused_object and getattr(getattr(focused_object, "appModule", None), "appName", "").lower() == "explorer"
        
        supported_audio_exts = [".mp3", ".wav", ".ogg", ".flac"]
        supported_video_exts = [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".3gp", ".ts", ".mts", ".m2ts"]
        supported_image_exts = [".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp", ".avif", ".gif", ".ico", ".svg"]
        supported_all_exts = supported_audio_exts + supported_video_exts + supported_image_exts

        selected_files = []
        valid_files = []
        
        if in_explorer:
            selected_files = self.getSelectedFiles()
            valid_files = [f for f in selected_files if os.path.splitext(f)[1].lower() in supported_all_exts]

            if not valid_files and len(selected_files) == 1 and os.path.isdir(selected_files[0]):
                folder_path = selected_files[0]
                valid_files = [
                    os.path.join(folder_path, f)
                    for f in os.listdir(folder_path)
                    if os.path.splitext(f)[1].lower() in supported_all_exts
                ]
                valid_files.sort()

        menu = wx.Menu()
        
        # Check file types for each function
        has_audio_files = any(f.lower().endswith(tuple(supported_audio_exts)) for f in valid_files)
        has_video_files = any(f.lower().endswith(tuple(supported_video_exts)) for f in valid_files)
        has_image_files = any(f.lower().endswith(tuple(supported_image_exts)) for f in valid_files)
        has_media_files = has_audio_files or has_video_files  # For Convert Audio which can convert video to audio
        has_mp3_files = any(f.lower().endswith(".mp3") for f in valid_files)
        multiple_mp3_files = len([f for f in valid_files if f.lower().endswith(".mp3")]) > 1
        
        menu_items_config = [
            (_("Convert Audio"), self.openConvertAudioDialog, in_explorer and len(valid_files) >= 1 and has_media_files, "multiple"),
            (_("Convert Video"), self.openConvertVideoDialog, in_explorer and len(valid_files) >= 1 and has_video_files, "multiple"),
            (_("Convert MP3 to MP4"), self.openConvertMP3toMP4Dialog, in_explorer and len(valid_files) >= 1 and has_mp3_files, "single"),
            (_("Merge MP3"), self.openMergeDialog, in_explorer and multiple_mp3_files, "multiple"),
            (_("Trim Audio/Video File"), self.openTrimDialog, in_explorer and len(valid_files) >= 1 and (has_audio_files or has_video_files), "single"),
            (_("Resize Image"), self.openResizeImageDialog, in_explorer and len(valid_files) >= 1 and has_image_files, "multiple"),
            (_("Split Audio"), self.openSplitAudioDialog, in_explorer and len(valid_files) == 1 and has_audio_files, "single"),  # Added Split Audio
            (_("Record Settings"), self.openRecordSettings, True, "none"),  # Always enabled
        ]

        menu_items_config.sort(key=lambda x: x[0])

        for label, handler, enabled, arg_type in menu_items_config:
            item = menu.Append(wx.ID_ANY, label)
            menu.Enable(item.GetId(), enabled)
            if arg_type == "multiple":
                menu.Bind(wx.EVT_MENU, lambda e, h=handler, files=valid_files: h(files), item)
            elif arg_type == "single":
                menu.Bind(wx.EVT_MENU, lambda e, h=handler, file=valid_files[0] if valid_files else None: h(file), item)
            else:  # "none"
                menu.Bind(wx.EVT_MENU, lambda e, h=handler: h(), item)

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        frame = wx.Frame(None, -1, "", pos=(0,0), size=(0,0))
        frame.Show()
        frame.Raise()

        def show_menu():
            frame.PopupMenu(menu)
            menu.Destroy()
            frame.Destroy()

        wx.CallAfter(show_menu)

    def getSelectedFiles(self):
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            shell = comtypes.client.CreateObject("Shell.Application")
            for win in shell.Windows():
                try:
                    if getattr(win, "HWND", None) == hwnd:
                        try:
                            selected = win.Document.SelectedItems()
                            paths = [item.Path for item in selected if getattr(item, "Path", None)]
                            if paths:
                                return paths
                        except Exception:
                            focused = api.getFocusObject()
                            if hasattr(focused, "name") and focused.name:
                                return [focused.name]
                except Exception:
                    continue
            return []
        except Exception as e:
            ui.message(_("Failed to retrieve selected files: {}").format(str(e)))
            return []

    def openTrimDialog(self, selected_file):
        if not selected_file:
            ui.message(_("Please select an audio or video file first"))
            return
        try:
            def _open():
                from globalPlugins.xTrack.Trim import TrimAudioVideoDialog
                dialog = TrimAudioVideoDialog(gui.mainFrame, [selected_file], self.tools_path)
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
                from globalPlugins.xTrack.merge import MergeAudioDialog
                dialog = MergeAudioDialog(gui.mainFrame, selected_files, self.tools_path)
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
                from globalPlugins.xTrack.convertAudio import ConvertAudioDialog
                dialog = ConvertAudioDialog(gui.mainFrame, selected_files, self.tools_path)
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
                from globalPlugins.xTrack.convertVideo import ConvertVideoDialog
                dialog = ConvertVideoDialog(gui.mainFrame, selected_files, self.tools_path)
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
                from globalPlugins.xTrack.resizeImage import ResizeImageDialog
                dialog = ResizeImageDialog(gui.mainFrame, selected_files, self.tools_path)
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
                from globalPlugins.xTrack.convertMP3toMP4 import ConvertMP3toMP4Dialog
                dialog = ConvertMP3toMP4Dialog(gui.mainFrame, selected_file, self.tools_path)
                dialog.ShowModal()
                dialog.Destroy()
            wx.CallAfter(_open)
        except Exception as e:
            log.error(f"Failed to open Convert MP3 to MP4 dialog: {e}")
            ui.message(_("Failed to open Convert MP3 to MP4 dialog: {}").format(str(e)))

    def openSplitAudioDialog(self, selected_file):
        if not selected_file:
            ui.message(_("Please select an audio file first"))
            return
        try:
            def _open():
                from globalPlugins.xTrack.splitAudio import SplitAudioDialog
                dialog = SplitAudioDialog(gui.mainFrame, selected_file, self.tools_path)
                dialog.ShowModal()
                dialog.Destroy()
            wx.CallAfter(_open)
        except Exception as e:
            log.error(f"Failed to open Split Audio dialog: {e}")
            ui.message(_("Failed to open Split Audio dialog: {}").format(str(e)))

    def openRecordSettings(self):
        try:
            def _open():
                dlg = record.RecordSettingsDialog(gui.mainFrame)
                result = dlg.ShowModal()
                if result == wx.ID_OK:
                    # Save to JSON
                    config_data["record"] = {
                        "recordingMode": dlg.settings["recordingMode"],
                        "format": dlg.settings["format"],
                        "mp3Quality": dlg.settings["mp3Quality"],
                        "countIn": dlg.settings["countIn"],
                        "openFolderAfter": dlg.settings["openFolderAfter"],
                        "noiseSuppression": dlg.settings["noiseSuppression"],
                        "noiseReductionPreset": dlg.settings["noiseReductionPreset"],
                        "systemGain": dlg.settings["systemGain"],
                        "microphoneGain": dlg.settings["microphoneGain"],
                        "destinationFolder": dlg.settings["destinationFolder"]
                    }
                    save_config(config_data)
                    # Update config.conf
                    for k, v in config_data["record"].items():
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
            ui.message(_("resume"))
            record.recorder.resume()
        else:
            ui.message(_("paused"))
            record.recorder.pause()

    @scriptHandler.script(
        description=_("Stop recording"),
        category="xTrack"
    )
    def script_stopRecord(self, gesture):
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
        if record.recorder.is_recording:
            record.recorder.stop()

