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
from globalPlugins.xTrack.Trim import TrimAudioVideoDialog
from globalPlugins.xTrack.merge import MergeAudioDialog
from globalPlugins.xTrack.convertAudio import ConvertAudioDialog
from globalPlugins.xTrack.convertMP3toMP4 import ConvertMP3toMP4Dialog

addonHandler.initTranslation()

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    """
    Main global plugin for xTrack add-on.
    Provides a context menu in File Explorer for various audio/video operations.
    """
    def __init__(self):
        super(GlobalPlugin, self).__init__()
        self.tools_path = os.path.join(os.path.dirname(__file__), "tools")
        try:
            self.bindGesture("kb:NVDA+x", "present_xtrack_menu")
        except Exception:
            pass

    @scriptHandler.script(
        gesture="kb:NVDA+x",
        description="xTrack context menu",
        category="xTrack",
        canPropagate=True,
    )
    def script_present_xtrack_menu(self, gesture):
        focused_object = api.getFocusObject()
        if not focused_object or getattr(getattr(focused_object, "appModule", None), "appName", "").lower() != "explorer":
            ui.message(_("This command is only available in File Explorer."))
            return

        supported_audio_exts = [".mp3", ".wav", ".ogg", ".flac"]
        supported_video_exts = [".mp4", ".avi", ".mkv", ".mov", ".wmv"]
        supported_all_exts = supported_audio_exts + supported_video_exts

        selected_files = self.getSelectedFiles()
        valid_files = [f for f in selected_files if os.path.splitext(f)[1].lower() in supported_all_exts]

        # Allow folder selection for convert audio and mp3 gain
        if not valid_files and len(selected_files) == 1 and os.path.isdir(selected_files[0]):
            folder_path = selected_files[0]
            valid_files = [
                os.path.join(folder_path, f)
                for f in os.listdir(folder_path)
                if os.path.splitext(f)[1].lower() in supported_all_exts
            ]
            valid_files.sort()

        if not valid_files:
            return

        trim_enabled = False
        merge_enabled = False
        convert_audio_enabled = False
        convert_mp3_to_mp4_enabled = False

        if len(valid_files) > 1:
            merge_enabled = True
            convert_audio_enabled = True  # Enable convert audio for multiple files

        if len(valid_files) >= 1:
            # Enable convert audio if at least one file is selected
            convert_audio_enabled = True
            
            # Check if any file is MP3 for MP3 to MP4 conversion
            mp3_files = [f for f in valid_files if os.path.splitext(f)[1].lower() == ".mp3"]
            if mp3_files:
                convert_mp3_to_mp4_enabled = True
            
            # Enable trim if at least one file is selected
            trim_enabled = True

        menu = wx.Menu()
        menu_items_config = [
            (_("Convert Audio"), self.openConvertAudioDialog, convert_audio_enabled),
            (_("Convert MP3 to MP4"), self.openConvertMP3toMP4Dialog, convert_mp3_to_mp4_enabled),
            (_("Merge MP3"), self.openMergeDialog, merge_enabled),
            (_("Trim Audio/Video File"), self.openTrimDialog, trim_enabled),
        ]

        menu_items_config.sort(key=lambda x: x[0])

        for label, handler, enabled in menu_items_config:
            if label == _("Merge MP3"):
                item = menu.Append(wx.ID_ANY, label)
                menu.Enable(item.GetId(), enabled)
                menu.Bind(wx.EVT_MENU, lambda event, h=handler: h(valid_files), item)
            elif label == _("Convert Audio"):
                item = menu.Append(wx.ID_ANY, label)
                menu.Enable(item.GetId(), enabled)
                menu.Bind(wx.EVT_MENU, lambda event, h=handler: h(valid_files), item)
            else:
                item = menu.Append(wx.ID_ANY, label)
                menu.Enable(item.GetId(), enabled)
                menu.Bind(wx.EVT_MENU, lambda event, h=handler: h(valid_files[0]), item)

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
        try:
            def _open():
                dialog = TrimAudioVideoDialog(gui.mainFrame, [selected_file], self.tools_path)
                dialog.ShowModal()
                dialog.Destroy()
            wx.CallAfter(_open)
        except Exception as e:
            ui.message(_("Failed to open Trim dialog: {}").format(str(e)))

    def openMergeDialog(self, selected_files):
        try:
            def _open():
                dialog = MergeAudioDialog(gui.mainFrame, selected_files, self.tools_path)
                dialog.ShowModal()
                dialog.Destroy()
            wx.CallAfter(_open)
        except Exception as e:
            ui.message(_("Failed to open Merge dialog: {}").format(str(e)))

    def openConvertAudioDialog(self, selected_files):
        try:
            def _open():
                dialog = ConvertAudioDialog(gui.mainFrame, selected_files, self.tools_path)
                dialog.ShowModal()
                dialog.Destroy()
            wx.CallAfter(_open)
        except Exception as e:
            ui.message(_("Failed to open Convert Audio dialog: {}").format(str(e)))

    def openConvertMP3toMP4Dialog(self, selected_file):
        try:
            def _open():
                dialog = ConvertMP3toMP4Dialog(gui.mainFrame, selected_file, self.tools_path)
                dialog.ShowModal()
                dialog.Destroy()
            wx.CallAfter(_open)
        except Exception as e:
            ui.message(_("Failed to open Convert MP3 to MP4 dialog: {}").format(str(e)))

    def terminate(self):
        pass
