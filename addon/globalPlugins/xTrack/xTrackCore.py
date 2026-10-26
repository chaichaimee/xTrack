# xTrackCore.py

import os
import json
import shutil
import subprocess
import re
import globalVars
from logHandler import log

VENDOR_FOLDER_NAME = "ChaiChaimee"
ADDON_FOLDER_NAME = "xTrack"
SETTINGS_FILE_NAME = "xTrack.json"
GAIN_FILE_NAME = "gain.json"
SHARED_BIN_FOLDER_NAME = "bin"
SHARED_TOOL_NAMES = ("ffmpeg.exe", "ffprobe.exe")

# Earlier releases kept the files in these places (relative to NVDA's
# configuration folder), newest layout first. They are only ever read from.
LEGACY_SETTINGS_LAYOUTS = (
	(VENDOR_FOLDER_NAME, SETTINGS_FILE_NAME),
	(SETTINGS_FILE_NAME,),
)
LEGACY_GAIN_LAYOUTS = (
	(VENDOR_FOLDER_NAME, "mp3gain_history.json"),
)


def _same_folder(first, second):
	return os.path.normcase(os.path.normpath(first)) == os.path.normcase(os.path.normpath(second))


def get_user_config_root():
	"""The configuration folder NVDA is actually using in this session.

	This is the folder of the running copy, whether NVDA is installed or
	portable and including a custom --config-path, so settings follow the
	user's real profile instead of a path guessed from the install type.
	"""
	configPath = getattr(getattr(globalVars, "appArgs", None), "configPath", None)
	if configPath:
		return configPath
	import config
	log.error("xTrack: NVDA did not report its configuration folder; using the default location")
	return config.getUserDefaultConfigPath()


def get_legacy_config_roots():
	"""Other places earlier releases may have stored data (the default location when it differs from the real one)."""
	current = get_user_config_root()
	roots = []
	try:
		import config
		defaultRoot = config.getUserDefaultConfigPath()
	except (ImportError, AttributeError, OSError) as error:
		log.debugWarning(f"xTrack: default configuration folder is not available: {error}")
		return roots
	if defaultRoot and not _same_folder(defaultRoot, current):
		roots.append(defaultRoot)
	return roots


def get_vendor_dir(configRoot=None):
	return os.path.join(configRoot or get_user_config_root(), VENDOR_FOLDER_NAME)


def get_addon_data_dir(configRoot=None):
	return os.path.join(get_vendor_dir(configRoot), ADDON_FOLDER_NAME)


def get_config_path():
	return os.path.join(get_addon_data_dir(), SETTINGS_FILE_NAME)


def get_gain_history_path():
	return os.path.join(get_addon_data_dir(), GAIN_FILE_NAME)


def _copy_atomically(sourcePath, destinationPath):
	os.makedirs(os.path.dirname(destinationPath), exist_ok=True)
	temporaryPath = destinationPath + ".tmp"
	shutil.copy2(sourcePath, temporaryPath)
	os.replace(temporaryPath, destinationPath)


def _migrate_one_file(finalPath, sameRootCandidates, otherRootCandidates, label):
	"""Bring one data file to finalPath. Returns a short description of what was done, or None.

	A file already at finalPath is never replaced, so running this on every
	load is harmless. Files from the same configuration folder are moved (that
	layout is retired); files from a different folder are copied and left
	where they are, because another NVDA copy may still be using them.
	"""
	if os.path.exists(finalPath):
		return None
	for candidate in sameRootCandidates:
		if os.path.isfile(candidate):
			try:
				os.makedirs(os.path.dirname(finalPath), exist_ok=True)
				shutil.move(candidate, finalPath)
			except OSError as error:
				log.error(f"xTrack: could not move {label} from {candidate}: {error}")
				continue
			return f"moved {label} from {candidate}"
	for candidate in otherRootCandidates:
		if os.path.isfile(candidate):
			try:
				_copy_atomically(candidate, finalPath)
			except OSError as error:
				log.error(f"xTrack: could not copy {label} from {candidate}: {error}")
				continue
			return f"copied {label} from {candidate}"
	return None


def migrate_user_data(configRoot, legacyRoots):
	"""One-time, idempotent move of settings files to the layout used now. Returns what was done."""
	actions = []
	finalSettings = os.path.join(get_addon_data_dir(configRoot), SETTINGS_FILE_NAME)
	finalGain = os.path.join(get_addon_data_dir(configRoot), GAIN_FILE_NAME)
	settingsCurrent = [os.path.join(configRoot, *layout) for layout in LEGACY_SETTINGS_LAYOUTS]
	gainCurrent = [os.path.join(configRoot, *layout) for layout in LEGACY_GAIN_LAYOUTS]
	settingsOther = []
	gainOther = []
	for root in legacyRoots:
		# The previous final layout of another folder is the best source, then the older ones.
		settingsOther.append(os.path.join(get_addon_data_dir(root), SETTINGS_FILE_NAME))
		settingsOther.extend(os.path.join(root, *layout) for layout in LEGACY_SETTINGS_LAYOUTS)
		gainOther.append(os.path.join(get_addon_data_dir(root), GAIN_FILE_NAME))
		gainOther.extend(os.path.join(root, *layout) for layout in LEGACY_GAIN_LAYOUTS)
	for finalPath, current, other, label in (
		(finalSettings, settingsCurrent, settingsOther, "settings"),
		(finalGain, gainCurrent, gainOther, "MP3 Gain history"),
	):
		result = _migrate_one_file(finalPath, current, other, label)
		if result:
			actions.append(result)
			log.info(f"xTrack: migrated user data: {result} -> {finalPath}")
	return actions


def resolve_shared_bin_dir(configRoot, legacyRoots):
	"""Folder holding ffmpeg.exe and ffprobe.exe.

	Normally the shared hub under the real configuration folder. Only when
	that hub has neither tool yet and another known location already has both
	is that location used, so a user is not left without ffmpeg after the
	configuration folder changes; nothing is copied or deleted.
	"""
	hub = os.path.join(get_vendor_dir(configRoot), SHARED_BIN_FOLDER_NAME)
	if any(os.path.isfile(os.path.join(hub, name)) for name in SHARED_TOOL_NAMES):
		return hub
	for root in legacyRoots:
		candidate = os.path.join(get_vendor_dir(root), SHARED_BIN_FOLDER_NAME)
		if all(os.path.isfile(os.path.join(candidate, name)) for name in SHARED_TOOL_NAMES):
			log.info(f"xTrack: using ffmpeg tools from the previous location {candidate}")
			return candidate
	return hub


# The "record" section of xTrack.json belongs entirely to the Record Settings
# dialog, so its list of keys is closed: anything else in it is left over from
# an earlier version and is dropped when the settings are saved.
RECORD_SETTING_DEFAULTS = {
	"recordingMode": "system_and_mic",
	"format": "mp3",
	"mp3Quality": 192,
	"countIn": False,
	"openFolderAfter": False,
	"systemVolume": 100,
	"microphoneVolume": 100,
	"audioProcessing": "default",
	"highPassFilter": False,
	"noiseGate": False,
	"microphoneAutoLevel": True,
	"microphoneDevice": "",
	"microphoneDevices": [],
	"systemDevice": "",
	"destinationFolder": "",
}

# Settings of features that no longer exist, in the top level of the file.
# A fixed list (rather than "everything not recognized") so a key written by
# any module that was not checked is never deleted by mistake.
OBSOLETE_TOP_LEVEL_KEYS = (
	"ConvertVideoWidth",
	"ConvertVideoHeight",
	"ConvertVideoAspectLock",
	"ConvertVideoAudioCodec",
	"TrimLastDuration",
)


def default_recording_folder():
	return os.path.join(get_addon_data_dir(), "recordings")


def normalize_record_settings(record, dropUnknown=False):
	"""Complete record settings: every known key present, optionally only the known keys. Returns (settings, droppedKeys)."""
	source = dict(record) if isinstance(record, dict) else {}
	# An older single-microphone choice becomes a one-item list, so upgrading
	# users keep recording from the microphone they already picked.
	if "microphoneDevices" not in source:
		legacyMic = source.get("microphoneDevice", "")
		source["microphoneDevices"] = [legacyMic] if legacyMic else []
	settings = {}
	for key, default in RECORD_SETTING_DEFAULTS.items():
		if key in source:
			settings[key] = source[key]
		elif key == "destinationFolder":
			settings[key] = default_recording_folder()
		else:
			settings[key] = list(default) if isinstance(default, list) else default
	dropped = []
	if dropUnknown:
		dropped = sorted(key for key in source if key not in RECORD_SETTING_DEFAULTS)
	else:
		for key in source:
			if key not in settings:
				settings[key] = source[key]
	return settings, dropped


def remove_obsolete_settings(configData):
	"""Remove settings of retired features from the whole settings file. Returns the removed keys."""
	removed = []
	for key in OBSOLETE_TOP_LEVEL_KEYS:
		if key in configData:
			del configData[key]
			removed.append(key)
	if isinstance(configData.get("record"), dict):
		cleaned, dropped = normalize_record_settings(configData["record"], dropUnknown=True)
		configData["record"] = cleaned
		removed.extend(f"record.{key}" for key in dropped)
	return removed


def load_config(config_path):
	if not os.path.exists(config_path):
		return {}
	try:
		with open(config_path, "r", encoding="utf-8") as configFile:
			data = json.load(configFile)
		if not isinstance(data, dict):
			raise ValueError("the settings file does not contain a settings object")
		return data
	except OSError as error:
		log.error(f"xTrack: could not read the settings file {config_path}: {error}")
		return {}
	except ValueError as error:
		log.error(f"xTrack: the settings file {config_path} is damaged: {error}")
	# A damaged file must not be silently replaced by the next save: keep it
	# for inspection and fall back to the last good copy when there is one.
	damagedPath = config_path + ".corrupt"
	try:
		os.replace(config_path, damagedPath)
		log.info(f"xTrack: the damaged settings file was kept as {damagedPath}")
	except OSError as error:
		log.error(f"xTrack: could not set the damaged settings file aside: {error}")
		return {}
	backupPath = config_path + ".bak"
	try:
		with open(backupPath, "r", encoding="utf-8") as backupFile:
			data = json.load(backupFile)
		if isinstance(data, dict):
			shutil.copy2(backupPath, config_path)
			log.info(f"xTrack: settings restored from {backupPath}")
			return data
	except (OSError, ValueError):
		log.debugWarning("xTrack: no usable settings backup was found")
	return {}


def save_config(config_path, config_data):
	"""Write the settings so that a crash or full disk can never leave a half-written file.

	The data goes to a temporary file first, the last good file is kept as
	.bak, and the new file replaces the old one in a single step.
	"""
	temporaryPath = config_path + ".tmp"
	try:
		os.makedirs(os.path.dirname(config_path), exist_ok=True)
		with open(temporaryPath, "w", encoding="utf-8") as configFile:
			json.dump(config_data, configFile, indent=4, ensure_ascii=False)
			configFile.flush()
			os.fsync(configFile.fileno())
		if os.path.exists(config_path):
			shutil.copy2(config_path, config_path + ".bak")
		os.replace(temporaryPath, config_path)
	except (OSError, TypeError, ValueError) as error:
		log.error(f"xTrack: could not save the settings to {config_path}: {error}")
		try:
			if os.path.exists(temporaryPath):
				os.remove(temporaryPath)
		except OSError:
			pass
		return False
	return True


def validate_time_format(time_str):
	if not time_str:
		return True
	pattern = re.compile(r"^((\d+):)?(\d{1,2}):(\d{2})$|^(\d+)$")
	return pattern.match(time_str) is not None

def time_to_seconds(time_str):
	if not time_str:
		return 0
	try:
		parts = time_str.split(":")
		if len(parts) == 1:
			return int(parts[0])
		elif len(parts) == 2:
			minutes, seconds = map(int, parts)
			return minutes * 60 + seconds
		elif len(parts) == 3:
			hours, minutes, seconds = map(int, parts)
			return hours * 3600 + minutes * 60 + seconds
		else:
			raise ValueError("Invalid time format")
	except ValueError:
		raise ValueError("Invalid time values")

def get_unique_filename(output_path, base_name, extension):
	counter = 1
	output_file = f"{base_name}.{extension}"
	while os.path.exists(os.path.join(output_path, output_file)):
		output_file = f"{base_name}_{counter}.{extension}"
		counter += 1
	return output_file

def get_file_duration(libs_path, file_path):
	ffprobe_path = os.path.join(libs_path, "ffprobe.exe")
	if not os.path.exists(ffprobe_path):
		return 0, "N/A"

	cmd = [
		ffprobe_path,
		"-v", "error",
		"-show_entries", "format=duration",
		"-of", "default=noprint_wrappers=1:nokey=1",
		file_path,
	]

	try:
		result = subprocess.run(
			cmd,
			stdin=subprocess.DEVNULL,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
			creationflags=subprocess.CREATE_NO_WINDOW,
			encoding='utf-8',
			errors='ignore',
			timeout=10
		)
		if result.returncode == 0 and result.stdout.strip():
			duration_sec = float(result.stdout.strip())
			hours = int(duration_sec // 3600)
			minutes = int((duration_sec % 3600) // 60)
			seconds = int(duration_sec % 60)
			file_duration_str = f"{hours}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes}:{seconds:02d}"
			return duration_sec, file_duration_str
		else:
			return 0, "N/A"
	except (OSError, subprocess.SubprocessError, ValueError):
		return 0, "N/A"

def get_file_size(file_path):
	try:
		size_bytes = os.path.getsize(file_path)
		if size_bytes < 1024:
			return size_bytes, f"{size_bytes} B"
		elif size_bytes < 1024 * 1024:
			return size_bytes, f"{size_bytes/1024:.1f} KB"
		elif size_bytes < 1024 * 1024 * 1024:
			return size_bytes, f"{size_bytes/(1024*1024):.1f} MB"
		else:
			return size_bytes, f"{size_bytes/(1024*1024*1024):.1f} GB"
	except OSError:
		return 0, "N/A"

def get_video_resolution(libs_path, file_path):
	ffprobe_path = os.path.join(libs_path, "ffprobe.exe")
	if not os.path.exists(ffprobe_path):
		log.error(f"ffprobe not found at {ffprobe_path}")
		return 0, 0

	cmd = [
		ffprobe_path,
		"-v", "error",
		"-select_streams", "v:0",
		"-show_entries", "stream=width,height",
		"-of", "csv=p=0",
		file_path
	]

	try:
		result = subprocess.run(
			cmd,
			stdin=subprocess.DEVNULL,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
			creationflags=subprocess.CREATE_NO_WINDOW,
			encoding='utf-8',
			errors='ignore',
			timeout=15
		)
		if result.returncode == 0 and result.stdout.strip():
			dimensions = result.stdout.strip().split(',')
			if len(dimensions) == 2:
				width = int(dimensions[0])
				height = int(dimensions[1])
				log.info(f"get_video_resolution: {file_path} -> {width}x{height}")
				return width, height
		else:
			log.error(f"ffprobe returned error for {file_path}: {result.stderr}")
	except subprocess.TimeoutExpired:
		log.error(f"Timeout getting resolution for {file_path}")
	except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as e:
		log.error(f"get_video_resolution failed for {file_path}: {e}")
	return 0, 0

def format_file_size(size_bytes):
	if size_bytes < 1024:
		return f"{size_bytes} B"
	elif size_bytes < 1024 * 1024:
		return f"{size_bytes / 1024:.1f} KB"
	elif size_bytes < 1024 * 1024 * 1024:
		return f"{size_bytes / (1024 * 1024):.1f} MB"
	else:
		return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

def format_duration_short(seconds):
	if seconds <= 0:
		return None
	hours = int(seconds // 3600)
	minutes = int((seconds % 3600) // 60)
	secs = int(seconds % 60)
	result = ""
	if hours > 0:
		result += f"{hours}h"
	if minutes > 0 or hours > 0:
		result += f"{minutes}min"
	result += f"{secs}sec"
	return result

def get_quick_image_label(libs_path, file_path):
	"""
	Build a short, ready-to-speak label for a single image file using ONE
	ffprobe call plus a stat() call, so it is cheap enough to run
	synchronously while a context menu is being built (no DPI probe here;
	DPI needs separate ffprobe passes and isn't worth the extra latency
	for a menu label -- process_single_image in image.py still reports it
	on demand).

	Returns a string like "1920 by 1080 pixels, 2.3 MB", or None if the
	file couldn't be probed in time.
	"""
	ffprobe_path = os.path.join(libs_path, "ffprobe.exe")
	if not os.path.exists(ffprobe_path):
		return None

	cmd = [
		ffprobe_path,
		"-v", "error",
		"-select_streams", "v:0",
		"-show_entries", "stream=width,height",
		"-of", "csv=p=0",
		file_path,
	]

	try:
		result = subprocess.run(
			cmd,
			stdin=subprocess.DEVNULL,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
			creationflags=subprocess.CREATE_NO_WINDOW,
			encoding='utf-8',
			errors='ignore',
			timeout=1.5
		)
		if result.returncode != 0 or not result.stdout.strip():
			return None
		dimensions = result.stdout.strip().split(',')
		if len(dimensions) != 2:
			return None
		width, height = int(dimensions[0]), int(dimensions[1])
	except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as e:
		log.error(f"get_quick_image_label failed for {file_path}: {e}")
		return None

	size_bytes, size_str = get_file_size(file_path)
	return f"{width} by {height} pixels, {size_str}"

def get_quick_video_label(libs_path, file_path):
	"""
	Build a short, ready-to-speak label for a single video file using ONE
	combined ffprobe call (stream dimensions/fps and format duration in a
	single JSON query) plus a stat() call, so it is cheap enough to run
	synchronously while a context menu is being built.

	Returns a string like "1920 by 1080 pixels, 5min23sec, 30 fps, 45.2
	MB", or None if the file couldn't be probed in time.
	"""
	ffprobe_path = os.path.join(libs_path, "ffprobe.exe")
	if not os.path.exists(ffprobe_path):
		return None

	cmd = [
		ffprobe_path,
		"-v", "error",
		"-select_streams", "v:0",
		"-show_entries", "stream=width,height,avg_frame_rate:format=duration",
		"-of", "json",
		file_path,
	]

	try:
		result = subprocess.run(
			cmd,
			stdin=subprocess.DEVNULL,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
			creationflags=subprocess.CREATE_NO_WINDOW,
			encoding='utf-8',
			errors='ignore',
			timeout=1.5
		)
		if result.returncode != 0 or not result.stdout.strip():
			return None
		probeData = json.loads(result.stdout)
	except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as e:
		log.error(f"get_quick_video_label failed for {file_path}: {e}")
		return None

	streams = probeData.get("streams") or [{}]
	streamInfo = streams[0]
	width = streamInfo.get("width")
	height = streamInfo.get("height")
	if not width or not height:
		return None

	fps = None
	fpsRaw = streamInfo.get("avg_frame_rate")
	if fpsRaw and fpsRaw != "0/0":
		try:
			num, den = fpsRaw.split('/')
			num, den = float(num), float(den)
			if den > 0:
				fps = round(num / den, 2)
		except (ValueError, ZeroDivisionError):
			pass

	durationStr = None
	try:
		durationSec = float(probeData.get("format", {}).get("duration", 0))
		durationStr = format_duration_short(durationSec)
	except (TypeError, ValueError):
		pass

	size_bytes, size_str = get_file_size(file_path)

	parts = [f"{width} by {height} pixels"]
	if durationStr:
		parts.append(durationStr)
	if fps:
		parts.append(f"{fps} fps")
	parts.append(size_str)
	return ", ".join(parts)
