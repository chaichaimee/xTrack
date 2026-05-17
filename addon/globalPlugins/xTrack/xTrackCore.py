# xTrackCore.py

import os
import json
import logging
import subprocess
import re

def get_config_path():
	import config
	return os.path.join(config.getUserDefaultConfigPath(), "ChaiChaimee", "xTrack.json")

def load_config(config_path):
	try:
		if os.path.exists(config_path):
			with open(config_path, 'r', encoding='utf-8') as f:
				return json.load(f)
		return {}
	except Exception as e:
		logging.error(f"Failed to load configuration: {str(e)}")
		return {}

def save_config(config_path, config_data):
	try:
		config_dir = os.path.dirname(config_path)
		if not os.path.exists(config_dir):
			os.makedirs(config_dir)
		with open(config_path, 'w', encoding='utf-8') as f:
			json.dump(config_data, f, indent=4)
	except Exception as e:
		logging.error(f"Failed to save configuration: {str(e)}")

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

def get_file_duration(tools_path, file_path):
	ffprobe_path = os.path.join(tools_path, "ffprobe.exe")
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
	except Exception:
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
	except Exception:
		return 0, "N/A"

def get_video_resolution(tools_path, file_path):
	ffprobe_path = os.path.join(tools_path, "ffprobe.exe")
	if not os.path.exists(ffprobe_path):
		logging.error(f"ffprobe not found at {ffprobe_path}")
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
				logging.info(f"get_video_resolution: {file_path} -> {width}x{height}")
				return width, height
		else:
			logging.error(f"ffprobe returned error for {file_path}: {result.stderr}")
	except subprocess.TimeoutExpired:
		logging.error(f"Timeout getting resolution for {file_path}")
	except Exception as e:
		logging.error(f"get_video_resolution failed for {file_path}: {e}")
	return 0, 0