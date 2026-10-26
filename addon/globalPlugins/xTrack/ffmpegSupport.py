# ffmpegSupport.py

import os
import re
import ctypes
import subprocess
import threading
import collections
import wx
import core
import ui
from logHandler import log
import addonHandler

addonHandler.initTranslation()

TIME_PATTERN = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$|^\d+(?:\.\d+)?$")
MAX_SPOKEN_MESSAGE_LENGTH = 200
MAX_STDERR_LINES_KEPT = 60
PROCESS_SUSPEND_RESUME = 0x0800


def parseTimeText(timeText):
	"""Seconds for "10", "10.5", "M:SS" or "H:MM:SS"; raises ValueError for anything else."""
	timeText = timeText.strip()
	if not TIME_PATTERN.match(timeText):
		raise ValueError(timeText)
	if ":" not in timeText:
		return float(timeText)
	parts = [int(part) for part in timeText.split(":")]
	if parts[-1] >= 60 or (len(parts) == 3 and parts[1] >= 60):
		raise ValueError(timeText)
	seconds = 0
	for part in parts:
		seconds = seconds * 60 + part
	return float(seconds)


def formatClock(totalSeconds):
	hours = int(totalSeconds // 3600)
	minutes = int((totalSeconds % 3600) // 60)
	seconds = int(totalSeconds % 60)
	return f"{hours}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes}:{seconds:02d}"


def limitSpokenText(text):
	if len(text) <= MAX_SPOKEN_MESSAGE_LENGTH:
		return text
	return text[:MAX_SPOKEN_MESSAGE_LENGTH].rstrip() + " " + _("(truncated)")


def summarizeFfmpegError(stderrLines):
	lowered = "\n".join(stderrLines).lower()
	if "moov atom not found" in lowered or "invalid data found" in lowered or "webp_pipe" in lowered:
		return _("the file looks damaged, incomplete, or is not a real media file")
	if "does not contain any stream" in lowered or "output file is empty" in lowered:
		return _("nothing could be read from this file")
	if "permission denied" in lowered:
		return _("the output file could not be written")
	if "no such file or directory" in lowered:
		return _("the file was not found")
	if "unknown encoder" in lowered or "encoder not found" in lowered:
		return _("this ffmpeg build cannot encode the chosen format")
	for line in reversed(stderrLines):
		stripped = line.strip()
		if stripped:
			return stripped[:120]
	return _("unknown error")


def setProcessSuspended(process, suspend):
	kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	ntdll = ctypes.WinDLL("ntdll")
	kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
	kernel32.OpenProcess.restype = ctypes.c_void_p
	kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
	suspendFunc = ntdll.NtSuspendProcess if suspend else ntdll.NtResumeProcess
	suspendFunc.argtypes = [ctypes.c_void_p]
	suspendFunc.restype = ctypes.c_long
	handle = kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, 0, process.pid)
	if not handle:
		return False
	try:
		return suspendFunc(handle) == 0
	finally:
		kernel32.CloseHandle(handle)


def announceResults(messages, delayMs=150):
	"""Speak a list of messages one after another.

	Safe to call from any thread: the speech itself is always scheduled on the
	main thread. The short delay lets Explorer finish announcing the focused
	file first, so the first result is not cut off by it.
	"""
	def speak():
		for message in messages:
			ui.message(limitSpokenText(message))

	wx.CallAfter(core.callLater, delayMs, speak)


def removeQuietly(path):
	if not path:
		return
	try:
		if os.path.exists(path):
			os.remove(path)
	except OSError as error:
		log.debugWarning("xTrack: could not remove %s: %s", path, error)


def partPathFor(finalPath):
	root, extension = os.path.splitext(finalPath)
	return root + ".xtrack_part" + extension


def parseProgressSeconds(line):
	"""Elapsed output time in seconds from one "-progress" line, or None.

	Different ffmpeg versions report the position under different keys
	(out_time_us, the older out_time_ms, and the formatted out_time), so all
	three are understood instead of relying on one of them.
	"""
	key, separator, value = line.strip().partition("=")
	if not separator:
		return None
	try:
		if key in ("out_time_us", "out_time_ms"):
			return int(value) / 1000000
		if key == "out_time" and value and value != "N/A":
			hours, minutes, seconds = value.split(":")
			return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
	except ValueError:
		return None
	return None


def runFfmpeg(command, cwd, durationSeconds, onProgress, registerProcess, lowPriority=False, stderrLineLimit=MAX_STDERR_LINES_KEPT):
	"""Run one ffmpeg command to completion; returns (exitCode, stderrLines).

	Raises OSError when the process cannot be started. onProgress receives an
	integer percentage and runs on the calling (worker) thread, so it must
	marshal any UI work itself.
	"""
	creationFlags = subprocess.CREATE_NO_WINDOW
	if lowPriority:
		creationFlags |= subprocess.BELOW_NORMAL_PRIORITY_CLASS
	stderrLines = collections.deque(maxlen=stderrLineLimit)
	process = subprocess.Popen(
		command,
		stdin=subprocess.DEVNULL,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		creationflags=creationFlags,
		cwd=cwd,
		text=True,
		encoding="utf-8",
		errors="ignore",
	)
	registerProcess(process)

	def drainStderr():
		for line in iter(process.stderr.readline, ""):
			stderrLines.append(line)

	drainThread = threading.Thread(target=drainStderr, daemon=True)
	drainThread.start()
	try:
		lastProgress = -1
		for line in process.stdout:
			if durationSeconds <= 0:
				continue
			elapsedSeconds = parseProgressSeconds(line)
			if elapsedSeconds is None:
				continue
			progress = int((elapsedSeconds / durationSeconds) * 100)
			if 0 <= progress <= 100 and progress != lastProgress:
				lastProgress = progress
				onProgress(progress)
		process.wait()
		drainThread.join(timeout=5)
	finally:
		registerProcess(None)
		process.stdout.close()
		process.stderr.close()
	return process.returncode, list(stderrLines)
