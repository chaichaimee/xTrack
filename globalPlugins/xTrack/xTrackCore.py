# xTrackCore.py

import os
import json
import logging
import subprocess
import re

def load_config(config_path):
    """Load configuration from JSON file."""
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        logging.error(f"Failed to load configuration: {str(e)}")
        return {}

def save_config(config_path, config_data):
    """Save configuration to JSON file."""
    try:
        config_dir = os.path.dirname(config_path)
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=4)
    except Exception as e:
        logging.error(f"Failed to save configuration: {str(e)}")

def validate_time_format(time_str):
    """Validates if the time string is in seconds, MM:SS, or HH:MM:SS format."""
    if not time_str:
        return True  # Empty string is valid (will use 0 or file duration)
    pattern = re.compile(r"^((\d+):)?(\d{1,2}):(\d{2})$|^(\d+)$")
    return pattern.match(time_str) is not None

def time_to_seconds(time_str):
    """Converts time string to seconds. Supports seconds, MM:SS, or HH:MM:SS."""
    if not time_str:
        return 0
    try:
        parts = time_str.split(":")
        if len(parts) == 1:  # Seconds only (e.g., "10")
            return int(parts[0])
        elif len(parts) == 2:  # MM:SS (e.g., "1:30")
            minutes, seconds = map(int, parts)
            return minutes * 60 + seconds
        elif len(parts) == 3:  # HH:MM:SS (e.g., "1:30:45")
            hours, minutes, seconds = map(int, parts)
            return hours * 3600 + minutes * 60 + seconds
        else:
            raise ValueError("Invalid time format")
    except ValueError:
        raise ValueError("Invalid time values")

def get_unique_filename(output_path, base_name, extension):
    """Generates a unique filename by appending a number if the file already exists."""
    counter = 1
    output_file = f"{base_name}.{extension}"
    while os.path.exists(os.path.join(output_path, output_file)):
        output_file = f"{base_name}_{counter}.{extension}"
        counter += 1
    return output_file

def get_file_duration(tools_path, file_path):
    """
    Uses ffprobe.exe to get the duration of the selected media file.
    Returns duration in seconds and formatted string.
    """
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
            errors='ignore'
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

