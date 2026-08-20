# xTrackCore.py

import os
import json
import logging
import subprocess
import re

def get_config_path():
	import config
	return os.path.join(config.getUserDefaultConfigPath(), "ChaiChaimee", "xTrack", "xTrack.json")

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

def get_video_resolution(libs_path, file_path):
	ffprobe_path = os.path.join(libs_path, "ffprobe.exe")
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
	except Exception as e:
		logging.error(f"get_quick_image_label failed for {file_path}: {e}")
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
	except Exception as e:
		logging.error(f"get_quick_video_label failed for {file_path}: {e}")
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

# libs/recorder_backend.py

import os
import wave
import threading
import queue
from datetime import datetime
import struct
import subprocess
import time
import ctypes

try:
	import pyaudiowpatch as pyaudio
except ImportError:
	import pyaudio

try:
	import comtypes
	from comtypes import GUID, COMMETHOD, HRESULT, IUnknown, COMError, CLSCTX_ALL
	WASAPI_COM_AVAILABLE = True
except ImportError:
	WASAPI_COM_AVAILABLE = False
	COMError = OSError

from logHandler import log

# Windows Core Audio constants used to request the Communications audio processing
# pipeline (AUDCLNT_STREAMCATEGORY_COMMUNICATIONS) on a WASAPI capture stream.
if WASAPI_COM_AVAILABLE:
	CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
	IID_IMMDeviceEnumerator = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
	IID_IAudioClient2 = GUID("{726778CD-F60A-4EDA-82DE-E47610CD78AA}")
	IID_IAudioCaptureClient = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")

	EDATAFLOW_CAPTURE = 1
	EROLE_MULTIMEDIA = 1
	AUDIO_CATEGORY_COMMUNICATIONS = 3
	AUDCLNT_SHAREMODE_SHARED = 0
	AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
	AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
	AUDCLNT_BUFFERFLAGS_SILENT = 0x2
	WASAPI_BUFFER_DURATION_HNS = 2000000

	class WAVEFORMATEX(ctypes.Structure):
		_fields_ = [
			("wFormatTag", ctypes.c_ushort),
			("nChannels", ctypes.c_ushort),
			("nSamplesPerSec", ctypes.c_ulong),
			("nAvgBytesPerSec", ctypes.c_ulong),
			("nBlockAlign", ctypes.c_ushort),
			("wBitsPerSample", ctypes.c_ushort),
			("cbSize", ctypes.c_ushort),
		]

	class AudioClientProperties(ctypes.Structure):
		# Layout matches Windows 10 version 1607 and later (includes the
		# trailing Options field). Older Windows 10 builds are not targeted.
		_fields_ = [
			("cbSize", ctypes.c_uint32),
			("bIsOffload", ctypes.c_int32),
			("eCategory", ctypes.c_int32),
			("Options", ctypes.c_int32),
		]

	class IMMDevice(IUnknown):
		_iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
		_methods_ = [
			COMMETHOD([], HRESULT, "Activate",
				(["in"], ctypes.POINTER(GUID), "iid"),
				(["in"], ctypes.c_ulong, "dwClsCtx"),
				(["in"], ctypes.c_void_p, "pActivationParams"),
				(["out"], ctypes.POINTER(ctypes.c_void_p), "ppInterface")),
			COMMETHOD([], HRESULT, "OpenPropertyStore",
				(["in"], ctypes.c_ulong, "stgmAccess"),
				(["out"], ctypes.POINTER(ctypes.c_void_p), "ppProperties")),
			COMMETHOD([], HRESULT, "GetId",
				(["out"], ctypes.POINTER(ctypes.c_wchar_p), "ppstrId")),
			COMMETHOD([], HRESULT, "GetState",
				(["out"], ctypes.POINTER(ctypes.c_ulong), "pdwState")),
		]

	class IMMDeviceEnumerator(IUnknown):
		_iid_ = IID_IMMDeviceEnumerator
		_methods_ = [
			COMMETHOD([], HRESULT, "EnumAudioEndpoints",
				(["in"], ctypes.c_int, "dataFlow"),
				(["in"], ctypes.c_ulong, "dwStateMask"),
				(["out"], ctypes.POINTER(ctypes.c_void_p), "ppDevices")),
			COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
				(["in"], ctypes.c_int, "dataFlow"),
				(["in"], ctypes.c_int, "role"),
				(["out"], ctypes.POINTER(ctypes.POINTER(IMMDevice)), "ppEndpoint")),
			COMMETHOD([], HRESULT, "GetDevice",
				(["in"], ctypes.c_wchar_p, "pwstrId"),
				(["out"], ctypes.POINTER(ctypes.POINTER(IMMDevice)), "ppDevice")),
			COMMETHOD([], HRESULT, "RegisterEndpointNotificationCallback",
				(["in"], ctypes.c_void_p, "pClient")),
			COMMETHOD([], HRESULT, "UnregisterEndpointNotificationCallback",
				(["in"], ctypes.c_void_p, "pClient")),
		]

	class IAudioCaptureClient(IUnknown):
		_iid_ = IID_IAudioCaptureClient
		_methods_ = [
			COMMETHOD([], HRESULT, "GetBuffer",
				(["out"], ctypes.POINTER(ctypes.POINTER(ctypes.c_byte)), "ppData"),
				(["out"], ctypes.POINTER(ctypes.c_uint32), "pNumFramesToRead"),
				(["out"], ctypes.POINTER(ctypes.c_ulong), "pdwFlags"),
				(["out"], ctypes.POINTER(ctypes.c_uint64), "pu64DevicePosition"),
				(["out"], ctypes.POINTER(ctypes.c_uint64), "pu64QPCPosition")),
			COMMETHOD([], HRESULT, "ReleaseBuffer",
				(["in"], ctypes.c_uint32, "NumFramesRead")),
			COMMETHOD([], HRESULT, "GetNextPacketSize",
				(["out"], ctypes.POINTER(ctypes.c_uint32), "pNumFramesInNextPacket")),
		]

	class IAudioClient(IUnknown):
		_iid_ = GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
		_methods_ = [
			COMMETHOD([], HRESULT, "Initialize",
				(["in"], ctypes.c_int, "ShareMode"),
				(["in"], ctypes.c_ulong, "StreamFlags"),
				(["in"], ctypes.c_longlong, "hnsBufferDuration"),
				(["in"], ctypes.c_longlong, "hnsPeriodicity"),
				(["in"], ctypes.POINTER(WAVEFORMATEX), "pFormat"),
				(["in"], ctypes.c_void_p, "AudioSessionGuid")),
			COMMETHOD([], HRESULT, "GetBufferSize",
				(["out"], ctypes.POINTER(ctypes.c_uint32), "pNumBufferFrames")),
			COMMETHOD([], HRESULT, "GetStreamLatency",
				(["out"], ctypes.POINTER(ctypes.c_longlong), "phnsLatency")),
			COMMETHOD([], HRESULT, "GetCurrentPadding",
				(["out"], ctypes.POINTER(ctypes.c_uint32), "pNumPaddingFrames")),
			COMMETHOD([], HRESULT, "IsFormatSupported",
				(["in"], ctypes.c_int, "ShareMode"),
				(["in"], ctypes.POINTER(WAVEFORMATEX), "pFormat"),
				(["out"], ctypes.POINTER(ctypes.c_void_p), "ppClosestMatch")),
			COMMETHOD([], HRESULT, "GetMixFormat",
				(["out"], ctypes.POINTER(ctypes.c_void_p), "ppDeviceFormat")),
			COMMETHOD([], HRESULT, "GetDevicePeriod",
				(["out"], ctypes.POINTER(ctypes.c_longlong), "phnsDefaultDevicePeriod"),
				(["out"], ctypes.POINTER(ctypes.c_longlong), "phnsMinimumDevicePeriod")),
			COMMETHOD([], HRESULT, "Start"),
			COMMETHOD([], HRESULT, "Stop"),
			COMMETHOD([], HRESULT, "Reset"),
			COMMETHOD([], HRESULT, "SetEventHandle",
				(["in"], ctypes.c_void_p, "eventHandle")),
			COMMETHOD([], HRESULT, "GetService",
				(["in"], ctypes.POINTER(GUID), "riid"),
				(["out"], ctypes.POINTER(ctypes.c_void_p), "ppv")),
		]

	class IAudioClient2(IAudioClient):
		_iid_ = IID_IAudioClient2
		_methods_ = [
			COMMETHOD([], HRESULT, "IsOffloadCapable",
				(["in"], ctypes.c_int, "Category"),
				(["out"], ctypes.POINTER(ctypes.c_int32), "pbOffloadCapable")),
			COMMETHOD([], HRESULT, "SetClientProperties",
				(["in"], ctypes.POINTER(AudioClientProperties), "pProperties")),
			COMMETHOD([], HRESULT, "GetBufferSizeLimits",
				(["in"], ctypes.POINTER(WAVEFORMATEX), "pFormat"),
				(["in"], ctypes.c_int32, "bEventDriven"),
				(["out"], ctypes.POINTER(ctypes.c_longlong), "phnsMinBufferDuration"),
				(["out"], ctypes.POINTER(ctypes.c_longlong), "phnsMaxBufferDuration")),
		]


class CommunicationsCaptureStream:
	"""
	Captures microphone audio directly through WASAPI with the
	AUDCLNT_STREAMCATEGORY_COMMUNICATIONS category, so Windows applies its
	Communications voice-processing pipeline (noise suppression, echo
	cancellation, automatic gain control) instead of the Default pipeline.

	Exposes start_stream()/stop_stream()/close() so it can be used as a
	drop-in replacement for a pyaudio Stream in the recorder above.
	"""

	def __init__(self, targetRate, targetChannels, dataCallback, blockAlign):
		self.targetRate = targetRate
		self.targetChannels = targetChannels
		self.dataCallback = dataCallback
		self.blockAlign = blockAlign

		self._prepared = False
		self._running = False
		self._pollThread = None
		self._deviceEnumerator = None
		self._captureDevice = None
		self._audioClient = None
		self._captureClient = None
		self._comInitializedOnPrepareThread = False

	def prepare(self):
		try:
			comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
			self._comInitializedOnPrepareThread = True
		except OSError as comInitError:
			log.debug(f"Communications capture: CoInitializeEx already active: {comInitError}")

		self._deviceEnumerator = comtypes.CoCreateInstance(
			CLSID_MMDeviceEnumerator, interface=IMMDeviceEnumerator, clsctx=CLSCTX_ALL
		)
		self._captureDevice = self._deviceEnumerator.GetDefaultAudioEndpoint(EDATAFLOW_CAPTURE, EROLE_MULTIMEDIA)

		rawClientPtr = self._captureDevice.Activate(IID_IAudioClient2, CLSCTX_ALL, None)
		if not rawClientPtr:
			raise RuntimeError("Failed to activate IAudioClient2 on the default capture device")
		self._audioClient = ctypes.cast(ctypes.c_void_p(rawClientPtr), ctypes.POINTER(IAudioClient2))

		clientProperties = AudioClientProperties()
		clientProperties.cbSize = ctypes.sizeof(AudioClientProperties)
		clientProperties.bIsOffload = 0
		clientProperties.eCategory = AUDIO_CATEGORY_COMMUNICATIONS
		clientProperties.Options = 0
		self._audioClient.SetClientProperties(clientProperties)

		waveFormat = WAVEFORMATEX()
		waveFormat.wFormatTag = 1
		waveFormat.nChannels = self.targetChannels
		waveFormat.nSamplesPerSec = self.targetRate
		waveFormat.wBitsPerSample = 16
		waveFormat.nBlockAlign = self.blockAlign
		waveFormat.nAvgBytesPerSec = self.targetRate * self.blockAlign
		waveFormat.cbSize = 0

		streamFlags = AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
		self._audioClient.Initialize(
			AUDCLNT_SHAREMODE_SHARED, streamFlags, WASAPI_BUFFER_DURATION_HNS, 0, waveFormat, None
		)

		rawCaptureClientPtr = self._audioClient.GetService(IID_IAudioCaptureClient)
		if not rawCaptureClientPtr:
			raise RuntimeError("Failed to obtain the IAudioCaptureClient service")
		self._captureClient = ctypes.cast(ctypes.c_void_p(rawCaptureClientPtr), ctypes.POINTER(IAudioCaptureClient))

		self._prepared = True

	def start_stream(self):
		if not self._prepared:
			raise RuntimeError("start_stream called before prepare() succeeded")
		self._audioClient.Start()
		self._running = True
		self._pollThread = threading.Thread(target=self._pollLoop, daemon=True)
		self._pollThread.start()

	def _pollLoop(self):
		pollThreadComInitialized = False
		try:
			comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
			pollThreadComInitialized = True
		except OSError as comInitError:
			log.debug(f"Communications capture poll thread: CoInitializeEx already active: {comInitError}")

		consecutiveErrors = 0
		try:
			while self._running:
				time.sleep(0.01)
				try:
					packetFrames = self._captureClient.GetNextPacketSize()
					while packetFrames != 0 and self._running:
						dataPtr, framesAvailable, bufferFlags, _devicePos, _qpcPos = self._captureClient.GetBuffer()
						if not framesAvailable:
							break
						if (bufferFlags & AUDCLNT_BUFFERFLAGS_SILENT) or not dataPtr:
							pcmBytes = b"\x00" * (framesAvailable * self.blockAlign)
						else:
							pcmBytes = ctypes.string_at(dataPtr, framesAvailable * self.blockAlign)
						self._captureClient.ReleaseBuffer(framesAvailable)
						if pcmBytes:
							self.dataCallback(pcmBytes, framesAvailable, None, 0)
						consecutiveErrors = 0
						packetFrames = self._captureClient.GetNextPacketSize()
				except (OSError, COMError) as captureBufferError:
					consecutiveErrors += 1
					log.error(f"Communications capture buffer error: {captureBufferError}")
					if consecutiveErrors >= 20:
						log.error("Communications capture aborted after repeated errors")
						self._running = False
		finally:
			try:
				if self._audioClient:
					self._audioClient.Stop()
			except (OSError, COMError) as stopError:
				log.error(f"Communications capture stop error: {stopError}")
			if pollThreadComInitialized:
				try:
					comtypes.CoUninitialize()
				except OSError:
					pass

	def stop_stream(self):
		self._running = False
		if self._pollThread and self._pollThread.is_alive():
			self._pollThread.join(timeout=2)
		self._pollThread = None

	def close(self):
		self._captureClient = None
		self._audioClient = None
		self._captureDevice = None
		self._deviceEnumerator = None
		if self._comInitializedOnPrepareThread:
			try:
				comtypes.CoUninitialize()
			except OSError:
				pass
			self._comInitializedOnPrepareThread = False


def list_audio_devices():
	"""
	Enumerates available microphone and system-audio (loopback) devices so
	the settings dialog can offer explicit device selection instead of only
	relying on the OS default. Safe to call even while a recording is not
	in progress; opens and closes its own throwaway PyAudio instance.
	"""
	microphones = []
	systemDevices = []
	audioInterface = None
	try:
		audioInterface = pyaudio.PyAudio()
		for i in range(audioInterface.get_device_count()):
			dev = audioInterface.get_device_info_by_index(i)
			name = dev.get('name', '')
			if dev.get('maxInputChannels', 0) > 0 and 'loopback' not in name.lower() and 'mapper' not in name.lower():
				microphones.append(name)
		for dev in audioInterface.get_loopback_device_info_generator():
			systemDevices.append(dev.get('name', ''))
	except Exception as enumError:
		log.error(f"Failed to enumerate audio devices: {enumError}")
	finally:
		if audioInterface:
			try:
				audioInterface.terminate()
			except Exception:
				pass
	return {"microphones": microphones, "systemDevices": systemDevices}


class WasapiSoundRecorder:
	def __init__(self, recording_format="wav",
				 recording_folder=os.path.join(os.environ['USERPROFILE'], "Documents", "Easy Sound Recorder"),
				 recording_mode="system audio and microphone",
				 ffmpeg_path="",
				 system_volume=100,
				 microphone_volume=100,
				 audio_processing="default",
				 high_pass_filter=False,
				 noise_gate=False,
				 microphone_device_name="",
				 system_device_name=""):
		self.recording_format = recording_format.strip().lower()
		self.recording_folder = recording_folder.strip()
		self.recording_mode = recording_mode.strip().lower()
		self.ffmpeg_path = ffmpeg_path.strip()
		self.system_volume = max(0, min(300, system_volume))
		self.microphone_volume = max(0, min(300, microphone_volume))
		self.audio_processing = (audio_processing or "default").strip().lower()
		self.microphone_device_name = (microphone_device_name or "").strip()
		self.system_device_name = (system_device_name or "").strip()
		# High-pass filter and noise gate are only meaningful for the Default
		# audio-processing path; Communications already runs its own OS-level
		# voice-processing chain, so these are ignored in that mode.
		self.high_pass_filter = bool(high_pass_filter) and self.audio_processing == "default"
		self.noise_gate = bool(noise_gate) and self.audio_processing == "default"
		self._highPassPrevX = None
		self._highPassPrevY = None
		self.mic_channels = 2
		self.mic_rate = 44100
		self.system_channels = 2
		self.system_rate = 44100
		self._systemMuteUntil = 0.0
		self.should_merge_to_single_file = (self.recording_mode == "system audio and microphone")
		self._mergeSystemTempPath = None
		self._mergeMicTempPath = None
		self._mergeFinalOutputPath = None

		self.audio_interface = None
		self.stream_mic = None
		self.stream_system = None
		self.default_mic = None
		self.default_speakers = None

		self.recording = 0
		self.pause_event = threading.Event()
		self.pause_event.set()

		self.speaker_queue = queue.Queue()
		self.mic_queue = queue.Queue()
		self.last_speaker_data = None
		self.last_mic_data = None

		self.writer_thread = None
		self.writer_thread_system = None
		self.writer_thread_mic = None
		self.outfile = None
		self.outfile_system = None
		self.outfile_mic = None
		self.ffmpeg_process = None
		self.ffmpeg_process_system = None
		self.ffmpeg_process_mic = None

		self.libs_path = os.path.dirname(os.path.dirname(__file__))

		self._init_audio()

	def _init_audio(self):
		try:
			self.audio_interface = pyaudio.PyAudio()
			wasapi_info = self.audio_interface.get_host_api_info_by_type(pyaudio.paWASAPI)

			if self.recording_mode in ["microphone only", "system audio and microphone", "system audio and microphone (separate files)"]:
				self.default_mic = self._get_microphone_device()
				log.info(f"Microphone device: {self.default_mic['name']}")

			if self.recording_mode in ["system audio only", "system audio and microphone", "system audio and microphone (separate files)"]:
				self.default_speakers = self._get_loopback_device()
				log.info(f"System audio device: {self.default_speakers['name']}")

		except Exception as e:
			log.error(f"Audio init error: {e}")
			if self.audio_interface:
				self.audio_interface.terminate()
			raise

	def _get_microphone_device(self):
		if self.microphone_device_name:
			for i in range(self.audio_interface.get_device_count()):
				dev = self.audio_interface.get_device_info_by_index(i)
				if dev.get('maxInputChannels', 0) > 0 and self.microphone_device_name.lower() in dev['name'].lower():
					log.info(f"Selected configured microphone: {dev['name']}")
					return dev
			log.error(f"Configured microphone '{self.microphone_device_name}' not found; falling back to automatic selection")

		candidates = []
		for i in range(self.audio_interface.get_device_count()):
			dev = self.audio_interface.get_device_info_by_index(i)
			if dev.get('maxInputChannels', 0) > 0 and 'loopback' not in dev['name'].lower():
				if 'mapper' not in dev['name'].lower():
					candidates.append(dev)
		if candidates:
			selected = candidates[0]
			log.info(f"Selected hardware microphone: {selected['name']}")
			return selected
		for i in range(self.audio_interface.get_device_count()):
			dev = self.audio_interface.get_device_info_by_index(i)
			if dev.get('maxInputChannels', 0) > 0:
				return dev
		raise RuntimeError("No microphone found")

	def _get_loopback_device(self):
		loopbacks = list(self.audio_interface.get_loopback_device_info_generator())
		if not loopbacks:
			raise RuntimeError("No loopback device found")

		if self.system_device_name:
			for dev in loopbacks:
				if self.system_device_name.lower() in dev['name'].lower():
					log.info(f"Selected configured system audio device: {dev['name']}")
					return dev
			log.error(f"Configured system audio device '{self.system_device_name}' not found; falling back to automatic selection")

		default_output = None
		try:
			default_output_idx = self.audio_interface.get_default_output_device_info()['index']
			default_output = self.audio_interface.get_device_info_by_index(default_output_idx)
		except:
			pass

		if default_output:
			for dev in loopbacks:
				if default_output['name'] in dev['name']:
					log.info(f"Matched loopback for default output: {dev['name']}")
					return dev

		selected = loopbacks[0]
		log.info(f"Using first available loopback: {selected['name']}")
		return selected

	def create_wave_file(self, path, channels, rate):
		wf = wave.open(path, "wb")
		wf.setnchannels(channels)
		wf.setsampwidth(self.audio_interface.get_sample_size(pyaudio.paInt16))
		wf.setframerate(rate)
		return wf

	def create_ffmpeg_process(self, output_file, rate, channels):
		ffmpeg_exe = self.ffmpeg_path or os.path.join(self.libs_path, "ffmpeg.exe")
		if not os.path.isfile(ffmpeg_exe):
			raise RuntimeError(f"ffmpeg.exe not found: {ffmpeg_exe}")

		format_map = {
			"mp3": ("mp3", ["-c:a", "libmp3lame", "-b:a", "192k"]),
			"flac": ("flac", ["-c:a", "flac"]),
			"m4a": ("ipod", ["-c:a", "aac", "-b:a", "192k"]),
			"wav": ("wav", [])
		}
		format_arg, extra_args = format_map.get(self.recording_format, ("wav", []))

		cmd = [
			ffmpeg_exe, "-y", "-f", "s16le", "-ar", str(rate), "-ac", str(channels), "-i", "-"
		] + extra_args + ["-f", format_arg, output_file]

		creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
		return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
								stderr=subprocess.DEVNULL, creationflags=creationflags)

	def open_system_audio_stream(self):
		dev = self.default_speakers
		self.system_channels = min(2, dev["maxInputChannels"])
		self.system_rate = int(dev["defaultSampleRate"])
		stream = self.audio_interface.open(
			format=pyaudio.paInt16,
			channels=self.system_channels,
			rate=self.system_rate,
			input=True,
			input_device_index=dev["index"],
			frames_per_buffer=1024,
			stream_callback=self._system_callback
		)
		return stream

	def open_mic_stream(self):
		dev = self.default_mic
		self.mic_channels = min(2, dev["maxInputChannels"])
		self.mic_rate = int(dev["defaultSampleRate"])
		self._highPassPrevX = [0.0] * self.mic_channels
		self._highPassPrevY = [0.0] * self.mic_channels

		if self.audio_processing == "communications":
			if WASAPI_COM_AVAILABLE:
				try:
					commStream = CommunicationsCaptureStream(
						targetRate=self.mic_rate,
						targetChannels=self.mic_channels,
						dataCallback=self._mic_callback,
						blockAlign=2 * self.mic_channels
					)
					commStream.prepare()
					log.info("Microphone opened with Windows Communications audio processing")
					return commStream
				except (OSError, RuntimeError, COMError, AttributeError) as commSetupError:
					log.error(f"Communications audio processing unavailable, falling back to Default: {commSetupError}")
			else:
				log.debug("Communications audio processing requested but comtypes is unavailable; using Default")

		stream = self.audio_interface.open(
			format=pyaudio.paInt16,
			channels=self.mic_channels,
			rate=self.mic_rate,
			input=True,
			input_device_index=dev["index"],
			frames_per_buffer=1024,
			stream_callback=self._mic_callback
		)
		return stream

	def _apply_volume(self, data, volume_percent):
		if volume_percent == 100:
			return data
		multiplier = max(0.0, volume_percent / 100.0)
		samples = struct.unpack(f'<{len(data)//2}h', data)
		scaled = [max(-32768, min(32767, int(s * multiplier))) for s in samples]
		return struct.pack(f'<{len(scaled)}h', *scaled)

	def _apply_high_pass_filter(self, data):
		# Single-pole IIR high-pass filter, ~150 Hz cutoff, to remove
		# low-frequency rumble such as fan or handling noise while leaving
		# speech frequencies untouched. Filter state persists per channel
		# across callbacks so there is no audible reset click.
		if not data:
			return data
		channels = max(1, self.mic_channels)
		cutoffHz = 150.0
		samples = struct.unpack(f'<{len(data)//2}h', data)
		numFrames = len(samples) // channels
		if numFrames == 0:
			return data
		if not self._highPassPrevX or len(self._highPassPrevX) != channels:
			self._highPassPrevX = [0.0] * channels
			self._highPassPrevY = [0.0] * channels
		dt = 1.0 / float(self.mic_rate or 44100)
		rc = 1.0 / (2 * 3.14159265358979 * cutoffHz)
		alpha = rc / (rc + dt)
		output = list(samples)
		for frameIndex in range(numFrames):
			for channelIndex in range(channels):
				sampleIndex = frameIndex * channels + channelIndex
				x = float(samples[sampleIndex])
				y = alpha * (self._highPassPrevY[channelIndex] + x - self._highPassPrevX[channelIndex])
				self._highPassPrevX[channelIndex] = x
				self._highPassPrevY[channelIndex] = y
				output[sampleIndex] = max(-32768, min(32767, int(y)))
		return struct.pack(f'<{len(output)}h', *output)

	def _apply_noise_gate(self, data):
		# Attenuates (rather than hard-mutes, to avoid clicking) buffers
		# whose RMS level falls below a fixed noise-floor threshold, cutting
		# steady background noise picked up between spoken words.
		if not data:
			return data
		threshold = 500
		attenuation = 0.15
		samples = struct.unpack(f'<{len(data)//2}h', data)
		if not samples:
			return data
		rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
		if rms >= threshold:
			return data
		gated = [int(s * attenuation) for s in samples]
		return struct.pack(f'<{len(gated)}h', *gated)

	def mute_system_audio(self, duration_seconds=1.5):
		"""
		Replaces captured system audio with silence for the given duration.
		Used to stop NVDA's own pause/resume speech announcements (which
		play through the system audio output and would otherwise be picked
		up by the system audio loopback) from being written into the
		recording, without affecting the microphone.
		"""
		self._systemMuteUntil = time.time() + max(0.0, duration_seconds)

	def _system_callback(self, in_data, frame_count, time_info, status):
		if self.recording != 1:
			return (in_data, pyaudio.paAbort)
		if not self.recording_mode.startswith("system audio"):
			return (in_data, pyaudio.paContinue)
		try:
			if in_data and time.time() < self._systemMuteUntil:
				in_data = b"\x00" * len(in_data)
			elif in_data and self.system_volume != 100:
				in_data = self._apply_volume(in_data, self.system_volume)
			self.speaker_queue.put(in_data)
			self.last_speaker_data = in_data
		except Exception as e:
			log.error(f"System callback error: {e}")
		return (in_data, pyaudio.paContinue)

	def _mic_callback(self, in_data, frame_count, time_info, status):
		if self.recording != 1:
			return (in_data, pyaudio.paAbort)
		if "microphone" not in self.recording_mode:
			return (in_data, pyaudio.paContinue)
		try:
			if in_data and self.high_pass_filter:
				in_data = self._apply_high_pass_filter(in_data)
			if in_data and self.noise_gate:
				in_data = self._apply_noise_gate(in_data)
			if in_data and self.microphone_volume != 100:
				in_data = self._apply_volume(in_data, self.microphone_volume)
			self.mic_queue.put(in_data)
			self.last_mic_data = in_data
		except Exception as e:
			log.error(f"Mic callback error: {e}")
		return (in_data, pyaudio.paContinue)


	def prewarm_microphone(self):
		"""
		Starts driving the microphone stream immediately, ahead of the actual
		recording start (for example during a count-in). This is primarily
		needed for the Communications audio-processing path: Windows' voice
		processing pipeline (noise suppression / echo cancellation / AGC)
		takes noticeable time to initialize after WASAPI activation, which
		otherwise costs the first 1-3 seconds of real audio. Frames received
		before start_recording() sets self.recording = 1 are discarded by
		_mic_callback's own guard, so nothing is written prematurely.
		"""
		if self.audio_processing != "communications":
			return
		if "microphone" not in self.recording_mode:
			return
		if self.stream_mic is not None:
			return
		try:
			self.stream_mic = self.open_mic_stream()
			self.stream_mic.start_stream()
			log.info("Microphone Communications stream pre-warmed ahead of recording start")
		except (OSError, RuntimeError, COMError, AttributeError) as prewarmError:
			log.error(f"Microphone pre-warm failed, recording will open the stream normally: {prewarmError}")
			self.stream_mic = None

	def start_recording(self):
		try:
			if not os.path.exists(self.recording_folder):
				os.makedirs(self.recording_folder)

			self.recording = 1
			self.pause_event.set()
			timestamp = datetime.now().strftime("%d-%m-%Y_%H-%M-%S")

			mode = self.recording_mode
			fmt = self.recording_format

			if mode == "microphone only":
				rate = int(self.default_mic["defaultSampleRate"])
				channels = min(2, self.default_mic["maxInputChannels"])
				out = os.path.join(self.recording_folder, f"recording_{timestamp}.{fmt}")
				if fmt == "wav":
					self.outfile = self.create_wave_file(out, channels, rate)
				else:
					self.ffmpeg_process = self.create_ffmpeg_process(out, rate, channels)
				self.writer_thread = threading.Thread(target=self._mic_only_writer, daemon=True)
				self.writer_thread.start()
				if self.stream_mic is None:
					self.stream_mic = self.open_mic_stream()
					self.stream_mic.start_stream()

			elif mode == "system audio only":
				rate = int(self.default_speakers["defaultSampleRate"])
				channels = min(2, self.default_speakers["maxInputChannels"])
				out = os.path.join(self.recording_folder, f"recording_{timestamp}.{fmt}")
				if fmt == "wav":
					self.outfile = self.create_wave_file(out, channels, rate)
				else:
					self.ffmpeg_process = self.create_ffmpeg_process(out, rate, channels)
				self.writer_thread = threading.Thread(target=self._system_only_writer, daemon=True)
				self.writer_thread.start()
				self.stream_system = self.open_system_audio_stream()
				self.stream_system.start_stream()

			elif mode == "system audio and microphone":
				# System audio and the microphone are captured to their own
				# temporary WAV files, each through the exact same pipeline
				# used by "separate files" mode (proven correct on its own).
				# They are merged into the single final file by ffmpeg once
				# recording stops (see _merge_temp_files_to_final), which lets
				# ffmpeg reconcile any sample-rate or channel differences
				# between two physically different sound cards, instead of a
				# hand-rolled live mix that is sensitive to clock drift.
				rate_sys = int(self.default_speakers["defaultSampleRate"])
				ch_sys = min(2, self.default_speakers["maxInputChannels"])
				rate_mic = int(self.default_mic["defaultSampleRate"])
				ch_mic = min(2, self.default_mic["maxInputChannels"])

				self._mergeSystemTempPath = os.path.join(self.recording_folder, f"_xtrack_temp_system_{timestamp}.wav")
				self._mergeMicTempPath = os.path.join(self.recording_folder, f"_xtrack_temp_mic_{timestamp}.wav")
				self._mergeFinalOutputPath = os.path.join(self.recording_folder, f"recording_{timestamp}.{fmt}")

				self.outfile_system = self.create_wave_file(self._mergeSystemTempPath, ch_sys, rate_sys)
				self.outfile_mic = self.create_wave_file(self._mergeMicTempPath, ch_mic, rate_mic)
				self.writer_thread_system = threading.Thread(target=self._system_separate_writer, daemon=True)
				self.writer_thread_mic = threading.Thread(target=self._mic_separate_writer, daemon=True)
				self.writer_thread_system.start()
				self.writer_thread_mic.start()

				self.stream_system = self.open_system_audio_stream()
				self.stream_system.start_stream()
				if self.stream_mic is None:
					self.stream_mic = self.open_mic_stream()
					self.stream_mic.start_stream()
				else:
					log.info("Reusing pre-warmed microphone stream for combined recording")

			elif mode == "system audio and microphone (separate files)":
				out_sys = os.path.join(self.recording_folder, f"recording_system_{timestamp}.{fmt}")
				out_mic = os.path.join(self.recording_folder, f"recording_mic_{timestamp}.{fmt}")
				rate_sys = int(self.default_speakers["defaultSampleRate"])
				ch_sys = min(2, self.default_speakers["maxInputChannels"])
				rate_mic = int(self.default_mic["defaultSampleRate"])
				ch_mic = min(2, self.default_mic["maxInputChannels"])

				if fmt == "wav":
					self.outfile_system = self.create_wave_file(out_sys, ch_sys, rate_sys)
					self.outfile_mic = self.create_wave_file(out_mic, ch_mic, rate_mic)
					self.writer_thread_system = threading.Thread(target=self._system_separate_writer, daemon=True)
					self.writer_thread_mic = threading.Thread(target=self._mic_separate_writer, daemon=True)
					self.writer_thread_system.start()
					self.writer_thread_mic.start()
				else:
					self.ffmpeg_process_system = self.create_ffmpeg_process(out_sys, rate_sys, ch_sys)
					self.ffmpeg_process_mic = self.create_ffmpeg_process(out_mic, rate_mic, ch_mic)
					self.writer_thread_system = threading.Thread(target=self._system_ffmpeg_separate_writer, daemon=True)
					self.writer_thread_mic = threading.Thread(target=self._mic_ffmpeg_separate_writer, daemon=True)
					self.writer_thread_system.start()
					self.writer_thread_mic.start()

				self.stream_system = self.open_system_audio_stream()
				self.stream_system.start_stream()
				if self.stream_mic is None:
					self.stream_mic = self.open_mic_stream()
					self.stream_mic.start_stream()
				else:
					log.info("Reusing pre-warmed microphone stream for separate-files recording")

			log.info("Recording started successfully")
		except Exception as e:
			self.recording = 0
			log.error(f"Failed to start recording: {e}")
			raise

	def _mic_only_writer(self):
		while self.recording == 1:
			self.pause_event.wait()
			try:
				data = self.mic_queue.get(timeout=0.1)
				if data and len(data) > 0:
					if self.recording_format == "wav":
						self.outfile.writeframes(data)
					else:
						self.ffmpeg_process.stdin.write(data)
			except queue.Empty:
				continue
			except Exception as e:
				if self.recording == 1:
					log.error(f"Mic writer error: {e}")

	def _system_only_writer(self):
		while self.recording == 1:
			self.pause_event.wait()
			try:
				data = self.speaker_queue.get(timeout=0.1)
				if data and len(data) > 0:
					if self.recording_format == "wav":
						self.outfile.writeframes(data)
					else:
						self.ffmpeg_process.stdin.write(data)
			except queue.Empty:
				continue
			except Exception as e:
				if self.recording == 1:
					log.error(f"System writer error: {e}")

	def _system_separate_writer(self):
		while self.recording == 1:
			self.pause_event.wait()
			try:
				data = self.speaker_queue.get(timeout=0.1)
				if data and self.outfile_system and len(data) > 0:
					self.outfile_system.writeframes(data)
			except queue.Empty:
				continue
			except Exception as e:
				if self.recording == 1:
					log.error(f"System separate writer error: {e}")

	def _mic_separate_writer(self):
		while self.recording == 1:
			self.pause_event.wait()
			try:
				data = self.mic_queue.get(timeout=0.1)
				if data and self.outfile_mic and len(data) > 0:
					self.outfile_mic.writeframes(data)
			except queue.Empty:
				continue
			except Exception as e:
				if self.recording == 1:
					log.error(f"Mic separate writer error: {e}")

	def _system_ffmpeg_separate_writer(self):
		while self.recording == 1:
			self.pause_event.wait()
			try:
				data = self.speaker_queue.get(timeout=0.1)
				if data and self.ffmpeg_process_system and len(data) > 0:
					self.ffmpeg_process_system.stdin.write(data)
			except queue.Empty:
				continue
			except Exception as e:
				if self.recording == 1:
					log.error(f"System FFmpeg writer error: {e}")

	def _mic_ffmpeg_separate_writer(self):
		while self.recording == 1:
			self.pause_event.wait()
			try:
				data = self.mic_queue.get(timeout=0.1)
				if data and self.ffmpeg_process_mic and len(data) > 0:
					self.ffmpeg_process_mic.stdin.write(data)
			except queue.Empty:
				continue
			except Exception as e:
				if self.recording == 1:
					log.error(f"Mic FFmpeg writer error: {e}")

	def _drain_and_write_queues(self):
		"""
		Writes out any audio still sitting in the queues instead of
		discarding it. This is a safety net for pause_recording() and
		stop_recording(): the writer thread normally drains the queues on
		its own, but if it has already exited (self.recording no longer 1)
		before finishing, whatever is left here would otherwise be silently
		lost, cutting off the last moment of audio before the button press.
		"""
		pendingSystem = []
		while not self.speaker_queue.empty():
			try:
				pendingSystem.append(self.speaker_queue.get_nowait())
			except queue.Empty:
				break

		pendingMic = []
		while not self.mic_queue.empty():
			try:
				pendingMic.append(self.mic_queue.get_nowait())
			except queue.Empty:
				break

		try:
			if self.recording_mode in ("system audio and microphone", "system audio and microphone (separate files)"):
				for data in pendingSystem:
					if self.outfile_system:
						self.outfile_system.writeframes(data)
					elif self.ffmpeg_process_system:
						self.ffmpeg_process_system.stdin.write(data)
				for data in pendingMic:
					if self.outfile_mic:
						self.outfile_mic.writeframes(data)
					elif self.ffmpeg_process_mic:
						self.ffmpeg_process_mic.stdin.write(data)
			elif self.recording_mode == "system audio only":
				for data in pendingSystem:
					if self.outfile:
						self.outfile.writeframes(data)
					elif self.ffmpeg_process:
						self.ffmpeg_process.stdin.write(data)
			elif self.recording_mode == "microphone only":
				for data in pendingMic:
					if self.outfile:
						self.outfile.writeframes(data)
					elif self.ffmpeg_process:
						self.ffmpeg_process.stdin.write(data)
		except Exception as flushError:
			log.error(f"Failed to flush pending audio queues: {flushError}")

	def pause_recording(self):
		try:
			if self.recording != 1:
				return

			# The Communications-category WASAPI capture stream is kept running
			# through the pause instead of being closed. Re-activating it on
			# resume would re-trigger the OS voice-processing pipeline's
			# warm-up delay, producing the same kind of gap/glitch seen at the
			# very start of a recording. Frames arriving while paused are
			# already discarded by _mic_callback's self.recording guard.
			keepMicStreamAlive = isinstance(self.stream_mic, CommunicationsCaptureStream)

			streamsToClose = [self.stream_system]
			if not keepMicStreamAlive:
				streamsToClose.append(self.stream_mic)

			# Stop new audio from being captured first...
			for stream in streamsToClose:
				if stream:
					try:
						stream.stop_stream()
					except:
						pass

			# ...then stop the writer loop immediately. Do NOT keep it running
			# for a "grace" window: once one side has no more real data, the
			# writer's empty-queue fallback fills in silence, and with the
			# microphone still capturing live audio during that window this
			# would write actual paused-time audio into the file. A brief
			# settle time lets any in-flight queue.get() call return, then
			# _drain_and_write_queues() writes out only genuinely queued,
			# already-captured audio (no silence is ever manufactured here).
			self.recording = 2
			time.sleep(0.1)
			self._drain_and_write_queues()

			for stream in streamsToClose:
				if stream:
					try:
						stream.close()
					except:
						pass
			self.stream_system = None
			if not keepMicStreamAlive:
				self.stream_mic = None

			self.last_speaker_data = None
			self.last_mic_data = None

			self.pause_event.clear()
			log.info(f"Recording paused (microphone stream kept warm: {keepMicStreamAlive})")
		except Exception as e:
			log.error(f"Pause error: {e}")

	def resume_recording(self):
		try:
			if self.recording != 2:
				return

			self.recording = 1
			self.pause_event.set()

			if self.recording_mode in ["system audio only", "system audio and microphone", "system audio and microphone (separate files)"]:
				self.stream_system = self.open_system_audio_stream()
				self.stream_system.start_stream()
			if self.recording_mode in ["microphone only", "system audio and microphone", "system audio and microphone (separate files)"]:
				if self.stream_mic is None:
					self.stream_mic = self.open_mic_stream()
					self.stream_mic.start_stream()
				else:
					log.info("Reusing microphone stream that was kept warm through pause")

			if self.recording_mode == "microphone only":
				self.writer_thread = threading.Thread(target=self._mic_only_writer, daemon=True)
				self.writer_thread.start()
			elif self.recording_mode == "system audio only":
				self.writer_thread = threading.Thread(target=self._system_only_writer, daemon=True)
				self.writer_thread.start()
			elif self.recording_mode == "system audio and microphone":
				self.writer_thread_system = threading.Thread(target=self._system_separate_writer, daemon=True)
				self.writer_thread_mic = threading.Thread(target=self._mic_separate_writer, daemon=True)
				self.writer_thread_system.start()
				self.writer_thread_mic.start()
			elif self.recording_mode == "system audio and microphone (separate files)":
				self.writer_thread_system = threading.Thread(target=self._system_separate_writer, daemon=True)
				self.writer_thread_mic = threading.Thread(target=self._mic_separate_writer, daemon=True)
				self.writer_thread_system.start()
				self.writer_thread_mic.start()

			log.info("Recording resumed")
		except Exception as e:
			log.error(f"Resume error: {e}")
			self.recording = 0
			raise

	def stop_recording(self):
		try:
			self.recording = 0
			self.pause_event.set()

			for stream in [self.stream_mic, self.stream_system]:
				if stream:
					try:
						stream.stop_stream()
						stream.close()
					except:
						pass

			time.sleep(0.1)

			self._drain_and_write_queues()

			time.sleep(0.1)

			for wf in [self.outfile, self.outfile_system, self.outfile_mic]:
				if wf:
					try:
						wf.close()
					except:
						pass

			for proc in [self.ffmpeg_process, self.ffmpeg_process_system, self.ffmpeg_process_mic]:
				if proc:
					try:
						proc.stdin.close()
						proc.wait(timeout=3)
					except:
						try:
							proc.terminate()
						except:
							pass

			if self.should_merge_to_single_file:
				self._merge_temp_files_to_final()

			if self.audio_interface:
				self.audio_interface.terminate()

			log.info("Recording stopped")
		except Exception as e:
			log.error(f"Stop error: {e}")
			raise

	def _merge_temp_files_to_final(self):
		"""
		Merges the separately recorded system-audio and microphone temp WAV
		files into the single final output file using ffmpeg's own resampling
		and mixing (aformat + amix), then removes the temp files. This is far
		more robust than mixing raw PCM sample-for-sample in Python, since the
		microphone and system audio device can be on physically different
		sound cards with different native sample rates.
		"""
		try:
			if not self._mergeSystemTempPath or not self._mergeMicTempPath or not self._mergeFinalOutputPath:
				log.error("Merge skipped: temp file paths were not set")
				return

			systemExists = os.path.isfile(self._mergeSystemTempPath) and os.path.getsize(self._mergeSystemTempPath) > 44
			micExists = os.path.isfile(self._mergeMicTempPath) and os.path.getsize(self._mergeMicTempPath) > 44

			ffmpeg_exe = self.ffmpeg_path or os.path.join(self.libs_path, "ffmpeg.exe")
			if not os.path.isfile(ffmpeg_exe):
				raise RuntimeError(f"ffmpeg.exe not found: {ffmpeg_exe}")

			format_map = {
				"mp3": ("mp3", ["-c:a", "libmp3lame", "-b:a", "192k"]),
				"flac": ("flac", ["-c:a", "flac"]),
				"m4a": ("ipod", ["-c:a", "aac", "-b:a", "192k"]),
				"wav": ("wav", [])
			}
			format_arg, extra_args = format_map.get(self.recording_format, ("wav", []))
			targetRate = self.system_rate or self.mic_rate or 44100
			creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

			if systemExists and micExists:
				filterComplex = (
					f"[0:a]aformat=sample_rates={targetRate}:channel_layouts=stereo[a0];"
					f"[1:a]aformat=sample_rates={targetRate}:channel_layouts=stereo[a1];"
					f"[a0][a1]amix=inputs=2:duration=longest:dropout_transition=0,volume=2[aout]"
				)
				cmd = [
					ffmpeg_exe, "-y",
					"-i", self._mergeSystemTempPath,
					"-i", self._mergeMicTempPath,
					"-filter_complex", filterComplex,
					"-map", "[aout]"
				] + extra_args + ["-f", format_arg, self._mergeFinalOutputPath]
			elif systemExists:
				cmd = [ffmpeg_exe, "-y", "-i", self._mergeSystemTempPath] + extra_args + ["-f", format_arg, self._mergeFinalOutputPath]
			elif micExists:
				cmd = [ffmpeg_exe, "-y", "-i", self._mergeMicTempPath] + extra_args + ["-f", format_arg, self._mergeFinalOutputPath]
			else:
				log.error("Merge skipped: neither temp file has usable audio")
				return

			result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
									creationflags=creationflags, timeout=60)
			if result.returncode != 0:
				log.error(f"ffmpeg merge exited with code {result.returncode}")
			else:
				log.info(f"Merged system audio and microphone into: {self._mergeFinalOutputPath}")
		except Exception as mergeError:
			log.error(f"Failed to merge system and microphone recordings: {mergeError}")
		finally:
			for tempPath in [self._mergeSystemTempPath, self._mergeMicTempPath]:
				try:
					if tempPath and os.path.isfile(tempPath):
						os.remove(tempPath)
				except Exception as cleanupError:
					log.error(f"Failed to remove temp file {tempPath}: {cleanupError}")
