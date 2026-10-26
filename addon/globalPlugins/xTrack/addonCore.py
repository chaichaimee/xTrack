# addonCore.py

import addonHandler
import globalPluginHandler
import scriptHandler
import ui
import wx
import os
import gui
import api
import comtypes.client
from comtypes import COMError
import core
import shutil
import threading
from logHandler import log

addonHandler.initTranslation()

import config

# Everything is stored under the configuration folder NVDA is really using in
# this session (installed, portable, or a custom --config-path), not under a
# folder guessed from the install type. xTrackCore holds the path rules and the
# one-time migration so that every module resolves the same location.
from .xTrackCore import (
	get_user_config_root, get_legacy_config_roots, get_vendor_dir, migrate_user_data,
	resolve_shared_bin_dir, load_config as _load_config_file, save_config as _save_config_file,
	get_config_path, SHARED_TOOL_NAMES, normalize_record_settings, remove_obsolete_settings,
)
from .ffmpegSupport import limitSpokenText
from .floatingMenu import showFloatingMenu, closeFloatingMenu, updateMenuLabel

CONFIG_ROOT = get_user_config_root()
LEGACY_CONFIG_ROOTS = get_legacy_config_roots()
NEW_CONFIG_DIR = get_vendor_dir(CONFIG_ROOT)

# Runs once, at import. It only moves or copies small settings files and never
# touches the network, so it cannot hold up NVDA (Section 5.1). Files that sit
# in the current configuration folder under an older layout are moved; files
# found in a different configuration folder are copied and left where they
# are. A settings file that already exists at its final place is never replaced.
for _migrationNote in migrate_user_data(CONFIG_ROOT, LEGACY_CONFIG_ROOTS):
	log.info(f"xTrack: {_migrationNote}")

addon_dir = os.path.dirname(__file__)
libs_dir = os.path.join(addon_dir, "libs")

# SharedBinDir is deliberately one level above this add-on's own config
# folder -- userConfig\ChaiChaimee\bin\, not
# userConfig\ChaiChaimee\xTrack\bin\ -- so it acts as a shared library hub
# any of Chai's "ChaiChaimee"-namespaced add-ons can point to for ffmpeg.exe
# and ffprobe.exe, instead of every add-on bundling and keeping its own
# separate copy of the same binaries. Mirrors the shared-hub model already
# used by AbsoluteYoutube's own ffmpeg.exe migration.
SharedBinDir = resolve_shared_bin_dir(CONFIG_ROOT, LEGACY_CONFIG_ROOTS)
SharedFfmpegEXE = os.path.join(SharedBinDir, SHARED_TOOL_NAMES[0])
SharedFfprobeEXE = os.path.join(SharedBinDir, SHARED_TOOL_NAMES[1])
BundledFfmpegEXE = os.path.join(libs_dir, "ffmpeg.exe")
BundledFfprobeEXE = os.path.join(libs_dir, "ffprobe.exe")


def _migrate_bundled_ffmpeg_tools_to_shared_bin():
	# Runs once, synchronously, at module import -- this only moves or
	# deletes files already sitting on local disk and never touches the
	# network, so there is no watchdog/blocking concern (Section 5.1) that
	# would require deferring it to a background thread.
	#
	# Two cases per binary:
	# 1. First run / first update after this migration existed: bin\ has
	#    no copy yet, but libs\ (this add-on's own bundled copy) does --
	#    move it into the shared hub so every "ChaiChaimee"-namespaced
	#    add-on that also needs ffmpeg/ffprobe can point at the same
	#    shared copy instead of bundling its own.
	# 2. A later add-on update re-bundles the binary into libs\ again (as
	#    part of the installed package) while bin\ already has one from a
	#    previous run -- delete the freshly-reinstalled libs\ copy so it
	#    doesn't end up existing in both places at once, with bin\
	#    remaining the single source of truth either way.
	try:
		if not os.path.exists(SharedBinDir):
			os.makedirs(SharedBinDir, exist_ok=True)
		for bundledEXE, sharedEXE, exeName in (
			(BundledFfmpegEXE, SharedFfmpegEXE, "ffmpeg.exe"),
			(BundledFfprobeEXE, SharedFfprobeEXE, "ffprobe.exe"),
		):
			if not os.path.exists(sharedEXE) and os.path.exists(bundledEXE):
				shutil.move(bundledEXE, sharedEXE)
				log.info(f"xTrack: Migrated bundled {exeName} to shared bin: {sharedEXE}")
			elif os.path.exists(sharedEXE) and os.path.exists(bundledEXE):
				os.remove(bundledEXE)
				log.info(f"xTrack: Removed duplicate bundled {exeName} from {bundledEXE} (already present in shared bin)")
	except OSError as e:
		log.error(f"xTrack: Error migrating ffmpeg/ffprobe to shared bin: {e}")


_migrate_bundled_ffmpeg_tools_to_shared_bin()
# bin\ is the path used from here on regardless of which branch above ran
# (or whether neither did, e.g. on a system where nothing was ever bundled
# and nothing has been placed in the shared hub either -- the existence
# check below still catches that case).
ffmpeg_path = SharedFfmpegEXE
ffprobe_path = SharedFfprobeEXE
if not os.path.exists(ffmpeg_path) or not os.path.exists(ffprobe_path):
	ui.message(_("ffmpeg.exe or ffprobe.exe not found"))
	log.error("xTrack: ffmpeg.exe or ffprobe.exe missing from shared bin directory.")

# "from . import overlay_loader" must run AFTER SharedBinDir/SharedFfmpegEXE/
# SharedFfprobeEXE above are already set as attributes on this package
# module, and BEFORE anything below this point reads the settings file.
# record.py creates its module-level singleton (`recorder = Recorder()`) the
# instant it is imported, and Recorder resolves `from . import SharedFfmpegEXE`
# when it needs ffmpeg -- that only resolves if this (partially initialized)
# package already has the name bound, which requires this block to sit above
# those imports, not below them.
# Root-caused from a live traceback: "ImportError: cannot import name
# 'SharedBinDir' from partially initialized module 'globalPlugins.xTrack'
# (most likely due to a circular import)" -- moving this block earlier is
# the actual fix; the failure was purely ordering, not a bad value.
from . import overlay_loader

FINAL_CONFIG_DIR = os.path.dirname(get_config_path())
CONFIG_DIR = FINAL_CONFIG_DIR
CONFIG_FILE = get_config_path()

def load_config(config_path=CONFIG_FILE):
	return _load_config_file(config_path)

def save_config(data, config_path=CONFIG_FILE):
	if _save_config_file(config_path, data):
		log.info(f"Config saved to: {config_path}")

config_data = load_config()

# Missing record settings are filled from the shared defaults (see
# xTrackCore.RECORD_SETTING_DEFAULTS); values that are already there are never
# changed. A settings file from an older version is written back once, only
# when something was actually added.
_recordSettings, _unused = normalize_record_settings(config_data.get("record"))
if _recordSettings != config_data.get("record"):
	config_data["record"] = _recordSettings
	save_config(config_data)

if "xTrack" not in config.conf:
	config.conf["xTrack"] = {}
if "record" not in config.conf["xTrack"]:
	config.conf["xTrack"]["record"] = {}
for key, value in config_data["record"].items():
	config.conf["xTrack"]["record"][key] = value


def apply_record_settings(settings):
	"""Save the Record Settings dialog's values and clear settings of retired features.

	Used by both OK and Apply. The record section is rebuilt from the known
	keys only, and settings of features that no longer exist are removed from
	the whole file; the previous file is kept as xTrack.json.bak by save_config.
	"""
	config_data["record"] = dict(settings)
	removed = remove_obsolete_settings(config_data)
	if removed:
		log.info(f"xTrack: removed settings of retired features: {', '.join(removed)}")
	if not _save_config_file(CONFIG_FILE, config_data):
		return False
	# Replace the whole section (not key by key) so nothing stale stays in nvda.ini.
	config.conf["xTrack"]["record"] = dict(config_data["record"])
	return True

from . import record
from . import update

# Marks a menu entry whose handler takes no argument.
NO_PAYLOAD = object()


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	scriptCategory = "xTrack"

	def __init__(self):
		super(GlobalPlugin, self).__init__()
		# libs_path now resolves to the shared "ChaiChaimee\bin" hub rather
		# than this add-on's own bundled libs\ folder -- see
		# _migrate_bundled_ffmpeg_tools_to_shared_bin() above. The name is
		# kept as "libs_path" since every dialog it gets passed to (Trim,
		# Merge, Convert, Split, MP3 Gain, etc.) already expects that
		# parameter name and only ever uses it to locate ffmpeg.exe/
		# ffprobe.exe -- renaming it across every one of those files would
		# be a purely cosmetic change for no functional benefit.
		self.libs_path = SharedBinDir
		self.ffmpeg_exe = SharedFfmpegEXE
		self.ffprobe_exe = SharedFfprobeEXE
		update.start()

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
				except OSError as e:
					log.error(f"xTrack failed to list folder: {e}")

		audio_exts = tuple(supported_audio_exts)
		video_exts = tuple(supported_video_exts)
		image_exts = tuple(supported_image_exts)
		audio_files = [f for f in valid_files if f.lower().endswith(audio_exts)]
		video_files = [f for f in valid_files if f.lower().endswith(video_exts)]
		image_files = [f for f in valid_files if f.lower().endswith(image_exts)]
		media_files = [f for f in valid_files if f.lower().endswith(audio_exts + video_exts)]
		mp3_files = [f for f in valid_files if f.lower().endswith(".mp3")]

		# The Image Info / Video Info entry starts with its plain label. When exactly
		# one image or video is selected, a worker thread probes the file with one
		# ffprobe call and then puts the dimensions/size/duration into that label
		# while the menu is already open, so ffprobe never runs on the main thread.
		image_info_label = _("Image Info")
		video_info_label = _("Video Info")

		# Each entry is (label, handler, available, payload). An entry that is not
		# available is left out of the menu altogether, so the user only ever
		# hears commands that can work for the current selection; the menu is
		# rebuilt on every call, so an item appears as soon as the selection
		# contains a file type it can handle. The payload is what the handler
		# receives, already narrowed to the file types that handler supports.
		# MP3 Gain and Mixdown take the raw selection (which may itself
		# be a folder) because valid_files only expands one folder level deep
		# and would silently drop nested album subfolders before their own
		# recursive scan ever gets a chance to see them.
		menu_items_config = [
			(_("Convert Audio"), self.openConvertAudioDialog, in_explorer and bool(media_files), media_files),
			(_("Convert Video"), self.openConvertVideoDialog, in_explorer and bool(video_files), video_files),
			(_("Convert MP3 to MP4"), self.openConvertMP3toMP4Dialog, in_explorer and bool(mp3_files), mp3_files[:1] and mp3_files[0]),
			(_("MP3 Gain"), self.openMP3GainDialog, True, selected_files),
			(_("Mixdown"), self.openMP3WavMixdownDialog, True, selected_files),
			(_("Merge MP3"), self.openMergeDialog, in_explorer and len(mp3_files) > 1, audio_files),
			(_("Trim Audio/Video File"), self.openTrimDialog, in_explorer and bool(media_files), media_files),
			(_("Split Audio"), self.openSplitAudioDialog, in_explorer and len(valid_files) == 1 and bool(audio_files), audio_files[:1] and audio_files[0]),
			(_("Split Album By Tracks"), self.openSplitAlbumByTracksDialog, in_explorer and len(valid_files) == 1 and bool(audio_files), audio_files[:1] and audio_files[0]),
			(_("Split Track by Silent"), self.openSplitTrackBySilentDialog, in_explorer and bool(audio_files), audio_files),
			(_("Resize Image"), self.openResizeImageDialog, in_explorer and bool(image_files), image_files),
			(image_info_label, self.openImageInfo, in_explorer and bool(image_files), image_files),
			(video_info_label, self.openVideoInfo, in_explorer and bool(video_files), video_files),
			(_("Record Settings"), self.openRecordSettings, True, NO_PAYLOAD),
		]

		menuEntries = []
		infoEntryIndex = None
		for label, handler, available, payload in menu_items_config:
			if not available:
				continue
			if handler in (self.openImageInfo, self.openVideoInfo):
				infoEntryIndex = len(menuEntries)
			if payload is NO_PAYLOAD:
				menuEntries.append((label, handler))
			else:
				menuEntries.append((label, lambda h=handler, arg=payload: h(arg)))

		menuFrame = showFloatingMenu("xTrack", menuEntries)
		if menuFrame is None or infoEntryIndex is None or len(valid_files) != 1:
			return
		if image_files:
			self.probeInfoLabel(menuFrame, infoEntryIndex, "get_quick_image_label", _("Image Info: {info}"), valid_files[0])
		elif video_files:
			self.probeInfoLabel(menuFrame, infoEntryIndex, "get_quick_video_label", _("Video Info: {info}"), valid_files[0])

	def probeInfoLabel(self, menuFrame, entryIndex, probeName, template, filePath):
		libsPath = self.libs_path

		def _probe():
			from . import xTrackCore
			info = getattr(xTrackCore, probeName)(libsPath, filePath)
			if not info:
				return
			wx.CallAfter(updateMenuLabel, menuFrame, entryIndex, limitSpokenText(template.format(info=info)))

		threading.Thread(target=_probe, name="xTrackInfoProbe", daemon=True).start()

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
				except (AttributeError, OSError, COMError):
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
						except (AttributeError, OSError, COMError, ValueError) as e:
							log.error(f"Error getting selected item {i}: {e}")
			except (AttributeError, OSError, COMError, ValueError) as e:
				log.warning(f"Could not get selected items: {e}")
		   
			if not paths:
				try:
					focused_item = target_window.Document.FocusedItem
					if focused_item and hasattr(focused_item, 'Path'):
						path = focused_item.Path
						if path and os.path.exists(path):
							paths.append(path)
				except (AttributeError, OSError, COMError, ValueError) as e:
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
				except (AttributeError, OSError, COMError, ValueError) as e:
					log.warning(f"Could not construct path from folder: {e}")
		   
			return paths
		   
		except (AttributeError, OSError, COMError, ValueError) as e:
			log.error(f"Failed to retrieve selected files: {e}")
			return []

	def showModalDialog(self, label, createDialog, onFinished=None):
		"""Show one of this add-on's dialogs the way NVDA shows its own.

		prePopup()/postPopup() must surround every modal dialog: without them the
		dialog can appear but not take keyboard input, and focus is not given back
		afterwards. Everything that can fail runs inside the callback, so a problem
		creating the dialog is spoken to the user instead of only reaching the log.
		"""
		def _open():
			gui.mainFrame.prePopup()
			dialog = None
			try:
				dialog = createDialog()
				result = dialog.ShowModal()
				if onFinished:
					onFinished(dialog, result)
			except (ImportError, OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError) as e:
				log.error(f"Failed to open {label} dialog: {e}", exc_info=True)
				ui.message(limitSpokenText(_("Failed to open {name} dialog: {error}").format(name=label, error=str(e))))
			finally:
				if dialog is not None:
					dialog.Destroy()
				gui.mainFrame.postPopup()
		wx.CallAfter(_open)

	def openSplitAlbumByTracksDialog(self, selected_file):
		def createDialog():
			from .splitAlbumByTracks import SplitAlbumByTracksDialog
			return SplitAlbumByTracksDialog(gui.mainFrame, selected_file, self.libs_path)
		self.showModalDialog(_("Split Album By Tracks"), createDialog)

	def openSplitTrackBySilentDialog(self, selected_files):
		def createDialog():
			from .splitTrackBySilent import SplitTrackBySilentDialog
			return SplitTrackBySilentDialog(gui.mainFrame, selected_files, self.libs_path)
		self.showModalDialog(_("Split Track by Silent"), createDialog)

	def openTrimDialog(self, selected_files):
		if not selected_files:
			ui.message(_("Please select an audio or video file first"))
			return
		def createDialog():
			from .Trim import TrimAudioVideoDialog
			return TrimAudioVideoDialog(gui.mainFrame, list(selected_files), self.libs_path)
		self.showModalDialog(_("Trim"), createDialog)

	def openMergeDialog(self, selected_files):
		if not selected_files or len(selected_files) < 2:
			ui.message(_("Please select at least 2 MP3 files first."))
			return
		def createDialog():
			from .merge import MergeAudioDialog
			return MergeAudioDialog(gui.mainFrame, selected_files, self.libs_path)
		self.showModalDialog(_("Merge"), createDialog)

	def openConvertAudioDialog(self, selected_files):
		if not selected_files:
			ui.message(_("Please select audio or video files first."))
			return
		def createDialog():
			from .convertAudio import ConvertAudioDialog
			return ConvertAudioDialog(gui.mainFrame, selected_files, self.libs_path)
		self.showModalDialog(_("Convert Audio"), createDialog)

	def openConvertVideoDialog(self, selected_files):
		if not selected_files:
			ui.message(_("Please select video files first."))
			return
		def createDialog():
			from .convertVideo import ConvertVideoDialog
			return ConvertVideoDialog(gui.mainFrame, selected_files, self.libs_path)
		self.showModalDialog(_("Convert Video"), createDialog)

	def openResizeImageDialog(self, selected_files):
		if not selected_files:
			ui.message(_("Please select image files first."))
			return
		def createDialog():
			from .resizeImage import ResizeImageDialog
			return ResizeImageDialog(gui.mainFrame, selected_files, self.libs_path)
		self.showModalDialog(_("Resize Image"), createDialog)

	def openConvertMP3toMP4Dialog(self, selected_file):
		if not selected_file:
			ui.message(_("Please select an MP3 file first."))
			return
		def createDialog():
			from .convertMP3toMP4 import ConvertMP3toMP4Dialog
			return ConvertMP3toMP4Dialog(gui.mainFrame, selected_file, self.libs_path)
		self.showModalDialog(_("Convert MP3 to MP4"), createDialog)

	def openMP3GainDialog(self, selected_files):
		# No early-exit guard here: the dialog's own Browse button lets the
		# user pick MP3 files after opening, so an empty selection is valid.
		def createDialog():
			from .mp3Gain import MP3GainDialog
			return MP3GainDialog(gui.mainFrame, selected_files, self.libs_path)
		self.showModalDialog(_("MP3 Gain"), createDialog)

	def openMP3WavMixdownDialog(self, selected_files):
		# No early-exit guard here either: the dialog's own Browse buttons
		# let the user pick MP3 or WAV files after opening.
		def createDialog():
			from .mp3_mixdown import MP3WavMixdownDialog
			return MP3WavMixdownDialog(gui.mainFrame, selected_files, self.libs_path)
		self.showModalDialog(_("Mixdown"), createDialog)

	def openSplitAudioDialog(self, selected_file):
		if not selected_file:
			ui.message(_("Please select an audio file first"))
			return
		def createDialog():
			from .splitAudio import SplitAudioDialog
			return SplitAudioDialog(gui.mainFrame, selected_file, self.libs_path)
		self.showModalDialog(_("Split Audio"), createDialog)

	def openImageInfo(self, selected_files):
		if not selected_files:
			ui.message(_("Please select image files first."))
			return
	   
		try:
			from .image import show_image_info
			show_image_info(selected_files, self.libs_path)
		except (ImportError, OSError, RuntimeError, ValueError, TypeError) as e:
			log.error(f"Failed to get image info: {e}")
			ui.message(limitSpokenText(_("Failed to get image info: {}").format(str(e))))

	def openVideoInfo(self, selected_files):
		if not selected_files:
			ui.message(_("Please select video files first."))
			return
	   
		try:
			from .video import show_video_info
			show_video_info(selected_files, self.libs_path)
		except (ImportError, OSError, RuntimeError, ValueError, TypeError) as e:
			log.error(f"Failed to get video info: {e}")
			ui.message(limitSpokenText(_("Failed to get video info: {}").format(str(e))))

	def openRecordSettings(self):
		def createDialog():
			from . import record
			return record.RecordSettingsDialog(gui.mainFrame, onApply=apply_record_settings)

		def saveSettings(dialog, result):
			if result != wx.ID_OK:
				return
			if apply_record_settings(dialog.settings):
				ui.message(_("Record settings saved"))
			else:
				ui.message(_("Record settings could not be saved"))

		self.showModalDialog(_("Record Settings"), createDialog, saveSettings)

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
		if files is record.STOP_PENDING:
			ui.message(_("stopped, saving the file"))
		elif files:
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
		update.stop()
		closeFloatingMenu()
		if hasattr(record, "recorder") and record.recorder.is_recording:
			try:
				record.recorder.stop()
			except record.RECORDING_ERRORS as e:
				log.error(f"xTrack: Error during terminate: {e}")
