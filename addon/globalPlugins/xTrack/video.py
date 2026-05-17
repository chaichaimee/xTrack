# video.py
import subprocess
import os
import ui
from logHandler import log
import addonHandler
import threading
import re

addonHandler.initTranslation()

def get_video_info_fast(tools_path, file_path):
	ffprobe_path = os.path.join(tools_path, "ffprobe.exe")
	if not os.path.exists(ffprobe_path):
		log.error(f"ffprobe not found at {ffprobe_path}")
		return None, None, None

	cmd = [
		ffprobe_path,
		"-v", "error",
		"-select_streams", "v:0",
		"-show_entries", "stream=width,height,avg_frame_rate",
		"-of", "default=noprint_wrappers=1:nokey=1",
		file_path
	]

	width = height = None
	fps = None

	try:
		result = subprocess.run(
			cmd,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
			creationflags=subprocess.CREATE_NO_WINDOW,
			encoding='utf-8',
			errors='ignore',
			timeout=15
		)

		if result.returncode != 0:
			log.error(f"ffprobe failed for {file_path}: {result.stderr}")
			return None, None, None

		output = result.stdout.strip()
		if not output:
			return None, None, None

		lines = output.splitlines()
		for line in lines:
			line = line.strip()
			if not line:
				continue
			if line.isdigit():
				if width is None:
					width = int(line)
				else:
					height = int(line)
			elif '/' in line:
				fps_raw = re.sub(r'\s+', '', line)
				if fps_raw.count('/') > 1:
					first_num = fps_raw.split('/')[0]
					if first_num.isdigit():
						fps = float(first_num)
				else:
					parts = fps_raw.split('/')
					if len(parts) == 2:
						try:
							num = float(parts[0])
							den = float(parts[1])
							if den > 0:
								fps = round(num / den, 2)
						except ValueError:
							pass
				if fps is None:
					numbers = re.findall(r'[\d.]+', fps_raw)
					if numbers:
						try:
							fps = round(float(numbers[0]), 2)
						except ValueError:
							pass

		if width is not None and height is not None:
			log.info(f"Video resolution for {file_path}: {width}x{height}")
		if fps:
			log.info(f"Video frame rate for {file_path}: {fps}")

	except subprocess.TimeoutExpired:
		log.error(f"Timeout getting video info for {file_path}")
	except Exception as e:
		log.error(f"Error getting video info for {file_path}: {e}")

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

def process_single_video(file_path, tools_path):
	try:
		log.info(f"Processing video: {file_path}")

		if not os.path.exists(file_path):
			return _("File not found")

		width, height, fps = get_video_info_fast(tools_path, file_path)

		if width is None or height is None:
			return _("Could not read video dimensions")

		file_size_bytes = os.path.getsize(file_path)
		size_str = format_file_size(file_size_bytes)

		if fps is None:
			msg = _("{width} by {height} pixels, size {size}").format(
				width=width, height=height, size=size_str
			)
		else:
			msg = _("{width} by {height} pixels, {fps} fps, size {size}").format(
				width=width, height=height, fps=fps, size=size_str
			)
		return msg

	except Exception as e:
		log.error(f"Error processing video {file_path}: {e}")
		return _("Error getting video information")

def show_video_info(selected_files, tools_path):
	if not selected_files:
		log.error("No files selected for video info")
		ui.message(_("No files selected"))
		return

	log.info(f"Starting video info processing for {len(selected_files)} files")

	results = []
	for file_path in selected_files:
		result = process_single_video(file_path, tools_path)
		results.append((os.path.basename(file_path), result))

	def announce_results():
		import time
		time.sleep(0.1)

		for filename, result in results:
			if not result.startswith(_("File not found")) and not result.startswith(_("Could not read")):
				ui.message(result)
			else:
				ui.message(_("{filename}: {result}").format(
					filename=filename, result=result
				))
			time.sleep(0.05)

	thread = threading.Thread(target=announce_results, daemon=True)
	thread.start()