# video.py

import subprocess
import os
import ui
from logHandler import log
import addonHandler
import threading
from .ffmpegSupport import announceResults

addonHandler.initTranslation()


def parse_frame_rate(rawValue):
	"""fps from ffprobe's "30000/1001" style value; None for 0/0, empty or malformed values."""
	rawValue = (rawValue or "").strip()
	if "/" in rawValue:
		numerator, _separator, denominator = rawValue.partition("/")
		try:
			numerator = float(numerator)
			denominator = float(denominator)
		except ValueError:
			return None
		if numerator > 0 and denominator > 0:
			return round(numerator / denominator, 2)
		return None
	try:
		value = float(rawValue)
	except ValueError:
		return None
	return round(value, 2) if value > 0 else None


def get_video_info_fast(libs_path, file_path):
	ffprobe_path = os.path.join(libs_path, "ffprobe.exe")
	if not os.path.exists(ffprobe_path):
		log.error(f"ffprobe not found at {ffprobe_path}")
		return None, None, None

	cmd = [
		ffprobe_path,
		"-v", "error",
		"-select_streams", "v:0",
		"-show_entries", "stream=width,height,avg_frame_rate",
		"-of", "default=noprint_wrappers=1",
		file_path
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
			timeout=15
		)
	except subprocess.TimeoutExpired:
		log.error(f"Timeout getting video info for {file_path}")
		return None, None, None
	except OSError as e:
		log.error(f"Error getting video info for {file_path}: {e}")
		return None, None, None

	if result.returncode != 0:
		log.error(f"ffprobe failed for {file_path}: {result.stderr}")
		return None, None, None

	# Key=value output, so the result does not depend on the order of the fields.
	values = {}
	for line in result.stdout.splitlines():
		key, separator, value = line.strip().partition("=")
		if separator:
			values[key] = value

	try:
		width = int(values["width"])
		height = int(values["height"])
	except (KeyError, ValueError):
		return None, None, None
	fps = parse_frame_rate(values.get("avg_frame_rate"))
	log.info(f"Video {file_path}: {width}x{height}, fps={fps}")
	return width, height, fps


def format_file_size(size_bytes):
	if size_bytes < 1024:
		return f"{size_bytes} B"
	elif size_bytes < 1024 * 1024:
		return f"{size_bytes / 1024:.1f} KB"
	elif size_bytes < 1024 * 1024 * 1024:
		return f"{size_bytes / (1024 * 1024):.1f} MB"
	else:
		return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def process_single_video(file_path, libs_path):
	"""Returns (isError, message)."""
	log.info(f"Processing video: {file_path}")

	if not os.path.exists(file_path):
		return True, _("File not found")

	width, height, fps = get_video_info_fast(libs_path, file_path)
	if width is None or height is None:
		return True, _("Could not read video dimensions")

	try:
		size_str = format_file_size(os.path.getsize(file_path))
	except OSError as e:
		log.error(f"Error reading the size of {file_path}: {e}")
		return True, _("Error getting video information")

	if fps is None:
		return False, _("{width} by {height} pixels, size {size}").format(width=width, height=height, size=size_str)
	return False, _("{width} by {height} pixels, {fps} fps, size {size}").format(
		width=width, height=height, fps=fps, size=size_str
	)


def collect_video_messages(selected_files, libs_path):
	showNames = len(selected_files) > 1
	messages = []
	for file_path in selected_files:
		isError, text = process_single_video(file_path, libs_path)
		name = os.path.basename(file_path)
		# A single result is spoken without the name, because Explorer has just
		# announced the focused file; several results need it to tell them apart.
		if isError or showNames:
			messages.append(_("{filename}: {result}").format(filename=name, result=text))
		else:
			messages.append(text)
	return messages


def show_video_info(selected_files, libs_path):
	if not selected_files:
		log.error("No files selected for video info")
		ui.message(_("No files selected"))
		return

	log.info(f"Starting video info processing for {len(selected_files)} files")

	# ffprobe can take seconds per file, so none of this runs on the main thread.
	def work():
		announceResults(collect_video_messages(list(selected_files), libs_path))

	threading.Thread(target=work, daemon=True).start()
