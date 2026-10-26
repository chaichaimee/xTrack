# recorder_backend.py
# Copyright (C) 2026 Chai Chaimee
# Licensed under GNU General Public License. See COPYING.txt for details.

import os
import wave
import threading
import queue
from datetime import datetime
import struct
import math
import subprocess
import time
import ctypes
import functools

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


class MicLevelProcessor:
	"""Adaptive noise gate and automatic level for one microphone stream.

	Inexpensive microphones often deliver speech at only a few hundred units
	of a 16-bit range, which records as very quiet audio. A gate with a
	fixed threshold cannot tell such quiet speech from noise and silences
	it, so this processor works relative to the noise it actually measures.
	It never switches the sound off completely, and it only raises the
	level while speech is present, so room noise between words is not
	pumped up.
	"""
	TARGET_RMS = 3000.0
	MAX_GAIN = 20.0
	MIN_GAIN = 1.0
	GATE_OPEN_RATIO = 2.0
	GATE_CLOSED_GAIN = 0.3
	MIN_NOISE_FLOOR = 3.0
	MIN_SPEECH_RMS = 8.0
	FLOOR_RISE_SECONDS = 30.0
	WARMUP_SECONDS = 0.6
	GATE_HOLD_SECONDS = 0.3
	GATE_ATTACK_SECONDS = 0.005
	GATE_RELEASE_SECONDS = 0.12
	GAIN_ATTACK_SECONDS = 0.04
	GAIN_RELEASE_SECONDS = 1.2
	PEAK_LIMIT = 32000.0

	def __init__(self, rate, channels, gate_enabled, auto_level_enabled):
		self.rate = float(rate or 44100)
		self.channels = max(1, int(channels or 1))
		self.gate_enabled = bool(gate_enabled)
		self.auto_level_enabled = bool(auto_level_enabled)
		self.noise_floor = None
		self.gate_gain = 1.0
		self.level_gain = 1.0
		self.elapsed = 0.0
		self.gate_open_until = 0.0

	@staticmethod
	def _smooth(current, target, blockSeconds, timeConstant):
		return target + (current - target) * math.exp(-blockSeconds / timeConstant)

	def process(self, data):
		if not data or len(data) < 2:
			return data
		sampleCount = len(data) // 2
		samples = struct.unpack(f'<{sampleCount}h', data[:sampleCount * 2])
		frames = sampleCount // self.channels
		if frames == 0:
			return data
		blockSeconds = frames / self.rate
		rms = (sum(s * s for s in samples) / sampleCount) ** 0.5
		peak = max(max(samples), -min(samples))

		warmingUp = self.elapsed < self.WARMUP_SECONDS
		if self.noise_floor is None:
			self.noise_floor = max(self.MIN_NOISE_FLOOR, rms)
		elif rms < self.noise_floor:
			self.noise_floor = max(self.MIN_NOISE_FLOOR, 0.7 * self.noise_floor + 0.3 * rms)
		elif not warmingUp:
			self.noise_floor += (rms - self.noise_floor) * min(1.0, blockSeconds / self.FLOOR_RISE_SECONDS)
		self.elapsed += blockSeconds

		speechPresent = rms > max(self.MIN_SPEECH_RMS, self.noise_floor * self.GATE_OPEN_RATIO)
		if speechPresent or warmingUp:
			self.gate_open_until = self.elapsed + self.GATE_HOLD_SECONDS
		gateIsOpen = self.elapsed <= self.gate_open_until

		startGain = self.gate_gain * self.level_gain
		if self.gate_enabled:
			gateTarget = 1.0 if gateIsOpen else self.GATE_CLOSED_GAIN
			timeConstant = self.GATE_ATTACK_SECONDS if gateTarget > self.gate_gain else self.GATE_RELEASE_SECONDS
			self.gate_gain = self._smooth(self.gate_gain, gateTarget, blockSeconds, timeConstant)

		if self.auto_level_enabled and speechPresent and rms > 0:
			desired = min(self.MAX_GAIN, max(self.MIN_GAIN, self.TARGET_RMS / rms))
			timeConstant = self.GAIN_ATTACK_SECONDS if desired < self.level_gain else self.GAIN_RELEASE_SECONDS
			self.level_gain = self._smooth(self.level_gain, desired, blockSeconds, timeConstant)

		endGain = self.gate_gain * self.level_gain
		if peak > 0:
			# Never let the boosted block exceed the 16-bit range: reduce the gain instead of clipping.
			ceiling = self.PEAK_LIMIT / peak
			if endGain > ceiling:
				self.level_gain = max(0.01, self.level_gain * ceiling / endGain)
				endGain = self.gate_gain * self.level_gain
			startGain = min(startGain, ceiling)
		if abs(endGain - 1.0) < 0.001 and abs(startGain - 1.0) < 0.001:
			return data
		step = (endGain - startGain) / frames
		channels = self.channels
		output = [
			max(-32768, min(32767, int(sample * (startGain + step * (index // channels)))))
			for index, sample in enumerate(samples)
		]
		return struct.pack(f'<{len(output)}h', *output)


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
				 microphone_device_names=None,
				 system_device_name="",
				 audio_bitrate_kbps=192,
				 microphone_auto_level=False):
		self.recording_format = recording_format.strip().lower()
		self.recording_folder = recording_folder.strip()
		self.recording_mode = recording_mode.strip().lower()
		self.ffmpeg_path = ffmpeg_path.strip()
		try:
			self.audio_bitrate_kbps = max(64, min(320, int(audio_bitrate_kbps)))
		except (TypeError, ValueError):
			self.audio_bitrate_kbps = 192
		self.system_volume = max(0, min(300, system_volume))
		self.microphone_volume = max(0, min(300, microphone_volume))
		self.audio_processing = (audio_processing or "default").strip().lower()
		self.microphone_device_name = (microphone_device_name or "").strip()
		# microphone_device_names (plural) is the multi-mic entry point: a list
		# of configured device names to capture simultaneously. The singular
		# microphone_device_name is kept only for backward compatibility with
		# older saved configs and is folded into the list when the list itself
		# is empty, so existing single-device setups behave identically.
		self.microphone_device_names = [n.strip() for n in (microphone_device_names or []) if n and n.strip()]
		if not self.microphone_device_names and self.microphone_device_name:
			self.microphone_device_names = [self.microphone_device_name]
		self.system_device_name = (system_device_name or "").strip()
		# High-pass filter and noise gate are only meaningful for the Default
		# audio-processing path; Communications already runs its own OS-level
		# voice-processing chain, so these are ignored in that mode.
		self.high_pass_filter = bool(high_pass_filter) and self.audio_processing == "default"
		self.noise_gate = bool(noise_gate) and self.audio_processing == "default"
		# Automatic level raises quiet microphones (see MicLevelProcessor).
		# Like the two options above it only applies to the Default path.
		self.auto_level = bool(microphone_auto_level) and self.audio_processing == "default"
		self._micLevel = None
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
		# mic_devices[0] is always what stream_mic/mic_queue/_mic_callback
		# capture (the pre-existing single-microphone pipeline, untouched).
		# mic_devices[1:], if any, are additional simultaneously-recorded
		# microphones, each with its own stream/queue/writer/temp file below.
		self.mic_devices = []
		self.extra_mic_streams = []
		self.extra_mic_queues = []
		self.extra_mic_temp_paths = []
		self.extra_mic_outfiles = []
		self.extra_mic_writer_threads = []
		self.extra_mic_highpass_state = []
		self.should_merge_mics_only = False
		self.should_merge_separate_mic_files = False
		self._separateMicFinalPath = None
		self._micOnlyTempPaths = []
		self._separateMicTempPaths = []

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
		# The actual file path(s) written by the current/last recording.
		# record.py reads this directly instead of guessing which file is
		# ours from a folder listing, which was unreliable for short or
		# quiet recordings (see stop_recording()).
		self.output_paths = []
		# Set when the final merge/mix could not be completed and the raw
		# recordings were kept instead; record.py tells the user about it.
		self.merge_warning = ""
		self.init_error = ""

		self._init_audio()

	def _init_audio(self):
		try:
			self.audio_interface = pyaudio.PyAudio()
			wasapi_info = self.audio_interface.get_host_api_info_by_type(pyaudio.paWASAPI)

			if self.recording_mode in ["microphone only", "system audio and microphone", "system audio and microphone (separate files)"]:
				self.mic_devices = self._get_microphone_devices()
				self.default_mic = self.mic_devices[0]
				if len(self.mic_devices) > 1:
					log.info(f"Recording from {len(self.mic_devices)} microphones: {', '.join(self._describe_device(d) for d in self.mic_devices)}")
				else:
					log.info(f"Microphone device: {self._describe_device(self.default_mic)}")

			if self.recording_mode in ["system audio only", "system audio and microphone", "system audio and microphone (separate files)"]:
				self.default_speakers = self._get_loopback_device()
				log.info(f"System audio device: {self.default_speakers['name']}")

		except Exception as e:
			self.init_error = str(e)
			log.error(f"Audio init error: {e}")
			if self.audio_interface:
				self.audio_interface.terminate()
			raise

	def _describe_device(self, dev):
		try:
			hostName = self.audio_interface.get_host_api_info_by_index(dev["hostApi"])["name"]
		except (OSError, IOError, KeyError, TypeError) as hostError:
			log.debug(f"Host API of {dev.get('name')} is not available: {hostError}")
			hostName = "unknown"
		return f"{dev['name']} (index {dev['index']}, host API {hostName}, {dev.get('maxInputChannels', 0)} input channels)"

	def _get_microphone_devices(self):
		"""
		Resolves the configured list of microphone device names into actual
		device dicts, for simultaneous multi-microphone recording. Falls back
		to the single automatic-default-microphone selection (unchanged, see
		_get_microphone_device() below) whenever no explicit devices are
		configured or none of the configured names could be matched, so
		"Automatic" and existing single-device setups behave exactly as
		before. Communications audio processing only supports one physical
		device at a time, so a multi-device selection is trimmed to just the
		first device in that mode.
		"""
		resolved = []
		seenIndices = set()
		for configuredName in self.microphone_device_names:
			matched = None
			for i in range(self.audio_interface.get_device_count()):
				dev = self.audio_interface.get_device_info_by_index(i)
				if dev.get('maxInputChannels', 0) > 0 and configuredName.lower() in dev['name'].lower():
					matched = dev
					break
			if matched and matched['index'] not in seenIndices:
				resolved.append(matched)
				seenIndices.add(matched['index'])
				log.info(f"Selected configured microphone: {matched['name']}")
			else:
				log.error(f"Configured microphone '{configuredName}' not found; skipping")

		if not resolved:
			resolved = [self._get_microphone_device()]

		if len(resolved) > 1 and self.audio_processing == "communications":
			log.error("Multiple microphones were selected, but Communications audio processing only supports one device at a time; recording from the first selected microphone only.")
			resolved = resolved[:1]

		return resolved

	def _get_microphone_device(self):
		if self.microphone_device_name:
			for i in range(self.audio_interface.get_device_count()):
				dev = self.audio_interface.get_device_info_by_index(i)
				if dev.get('maxInputChannels', 0) > 0 and self.microphone_device_name.lower() in dev['name'].lower():
					log.info(f"Selected configured microphone: {dev['name']}")
					return dev
			log.error(f"Configured microphone '{self.microphone_device_name}' not found; falling back to automatic selection")

		# Prefer the OS-configured default input device first, mirroring how
		# _get_loopback_device() prefers the OS default output device. Without
		# this, "Automatic" silently picked whichever input happened to enumerate
		# first, which is not necessarily the microphone actually in use.
		try:
			defaultInputIndex = self.audio_interface.get_default_input_device_info()['index']
			defaultInput = self.audio_interface.get_device_info_by_index(defaultInputIndex)
			if defaultInput.get('maxInputChannels', 0) > 0 and 'loopback' not in defaultInput['name'].lower():
				log.info(f"Selected default hardware microphone: {defaultInput['name']}")
				return defaultInput
		except (OSError, IOError, KeyError) as defaultInputError:
			log.debug(f"No default input device reported: {defaultInputError}")

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
		except (OSError, IOError, KeyError) as defaultOutputError:
			log.debug(f"No default output device reported: {defaultOutputError}")

		if default_output:
			for dev in loopbacks:
				if default_output['name'].lower() in dev['name'].lower():
					log.info(f"Matched loopback for default output: {dev['name']}")
					return dev

		selected = loopbacks[0]
		log.info(f"Using first available loopback: {selected['name']}")
		return selected

	def _encoder_settings(self):
		"""(ffmpeg muxer name, codec arguments) for the chosen recording format and bitrate."""
		bitrate = f"{self.audio_bitrate_kbps}k"
		format_map = {
			"mp3": ("mp3", ["-c:a", "libmp3lame", "-b:a", bitrate]),
			"flac": ("flac", ["-c:a", "flac"]),
			"m4a": ("ipod", ["-c:a", "aac", "-b:a", bitrate]),
			"wav": ("wav", [])
		}
		return format_map.get(self.recording_format, ("wav", []))

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

		format_arg, extra_args = self._encoder_settings()

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
		if self.noise_gate or self.auto_level:
			self._micLevel = MicLevelProcessor(self.mic_rate, self.mic_channels, self.noise_gate, self.auto_level)

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

	def open_extra_mic_stream(self, writerIndex, dev):
		"""
		Opens one of the additional simultaneous microphone streams beyond
		the first (see mic_devices in __init__). Always uses the Default
		audio-processing path: Communications mode is restricted to a single
		device by _get_microphone_devices(), so this is never reached with
		more than one configured microphone in that mode.
		"""
		channels = min(2, dev["maxInputChannels"])
		rate = int(dev["defaultSampleRate"])
		stream = self.audio_interface.open(
			format=pyaudio.paInt16,
			channels=channels,
			rate=rate,
			input=True,
			input_device_index=dev["index"],
			frames_per_buffer=1024,
			stream_callback=functools.partial(self._extra_mic_callback, writerIndex)
		)
		return stream

	def _apply_volume(self, data, volume_percent):
		if volume_percent == 100:
			return data
		multiplier = max(0.0, volume_percent / 100.0)
		samples = struct.unpack(f'<{len(data)//2}h', data)
		scaled = [max(-32768, min(32767, int(s * multiplier))) for s in samples]
		return struct.pack(f'<{len(scaled)}h', *scaled)

	def _run_high_pass_filter(self, data, channels, rate, prevX, prevY):
		# Single-pole IIR high-pass filter, ~100 Hz cutoff, to remove
		# low-frequency rumble such as fan or handling noise while leaving
		# speech frequencies untouched. (150 Hz cut into the fundamental of a
		# low voice, which made quiet microphones sound thinner and weaker.) Filter state (prevX/prevY) persists
		# per channel across callbacks so there is no audible reset click.
		# Shared by the primary microphone and any additional simultaneous
		# microphones, each of which keeps its own independent state.
		if not data:
			return data, prevX, prevY
		channels = max(1, channels)
		cutoffHz = 100.0
		samples = struct.unpack(f'<{len(data)//2}h', data)
		numFrames = len(samples) // channels
		if numFrames == 0:
			return data, prevX, prevY
		if not prevX or len(prevX) != channels:
			prevX = [0.0] * channels
			prevY = [0.0] * channels
		dt = 1.0 / float(rate or 44100)
		rc = 1.0 / (2 * 3.14159265358979 * cutoffHz)
		alpha = rc / (rc + dt)
		output = list(samples)
		for frameIndex in range(numFrames):
			for channelIndex in range(channels):
				sampleIndex = frameIndex * channels + channelIndex
				x = float(samples[sampleIndex])
				y = alpha * (prevY[channelIndex] + x - prevX[channelIndex])
				prevX[channelIndex] = x
				prevY[channelIndex] = y
				output[sampleIndex] = max(-32768, min(32767, int(y)))
		return struct.pack(f'<{len(output)}h', *output), prevX, prevY

	def _apply_high_pass_filter(self, data):
		output, self._highPassPrevX, self._highPassPrevY = self._run_high_pass_filter(
			data, self.mic_channels, self.mic_rate, self._highPassPrevX, self._highPassPrevY
		)
		return output

	def _apply_high_pass_filter_for(self, data, writerIndex):
		state = self.extra_mic_highpass_state[writerIndex]
		output, state["prevX"], state["prevY"] = self._run_high_pass_filter(
			data, state["channels"], state["rate"], state["prevX"], state["prevY"]
		)
		return output

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
			if in_data and self._micLevel:
				in_data = self._micLevel.process(in_data)
			if in_data and self.microphone_volume != 100:
				in_data = self._apply_volume(in_data, self.microphone_volume)
			self.mic_queue.put(in_data)
			self.last_mic_data = in_data
		except Exception as e:
			log.error(f"Mic callback error: {e}")
		return (in_data, pyaudio.paContinue)

	def _extra_mic_callback(self, writerIndex, in_data, frame_count, time_info, status):
		# Sibling of _mic_callback() for the second and subsequent
		# simultaneously-recorded microphones. writerIndex is bound ahead of
		# time via functools.partial in open_extra_mic_stream() and indexes
		# into the extra_mic_* lists set up by _setup_extra_mic_writers().
		if self.recording != 1:
			return (in_data, pyaudio.paAbort)
		if "microphone" not in self.recording_mode:
			return (in_data, pyaudio.paContinue)
		try:
			if in_data and self.high_pass_filter:
				in_data = self._apply_high_pass_filter_for(in_data, writerIndex)
			levelProcessor = self.extra_mic_highpass_state[writerIndex].get("level")
			if in_data and levelProcessor:
				in_data = levelProcessor.process(in_data)
			if in_data and self.microphone_volume != 100:
				in_data = self._apply_volume(in_data, self.microphone_volume)
			self.extra_mic_queues[writerIndex].put(in_data)
		except Exception as e:
			log.error(f"Additional microphone {writerIndex} callback error: {e}")
		return (in_data, pyaudio.paContinue)

	def _extra_mic_writer(self, writerIndex):
		while self.recording == 1:
			self.pause_event.wait()
			try:
				data = self.extra_mic_queues[writerIndex].get(timeout=0.1)
				if data and len(data) > 0:
					self.extra_mic_outfiles[writerIndex].writeframes(data)
			except queue.Empty:
				continue
			except Exception as e:
				if self.recording == 1:
					log.error(f"Additional microphone {writerIndex} writer error: {e}")

	def _setup_extra_mic_writers(self, timestamp):
		"""
		Starts capture for every configured microphone beyond the first
		(mic_devices[1:]), each to its own temporary WAV file. Called from
		start_recording() whenever more than one microphone is configured;
		the resulting temp files are mixed into the recording's real output
		by _merge_temp_files_to_final(), _merge_mic_only_files_to_final(), or
		_merge_separate_mic_files_to_final() once recording stops, for the
		same clock-drift reasons the existing system+microphone merge
		already uses temp files instead of live sample-level mixing.
		"""
		self.extra_mic_streams = []
		self.extra_mic_queues = []
		self.extra_mic_temp_paths = []
		self.extra_mic_outfiles = []
		self.extra_mic_writer_threads = []
		self.extra_mic_highpass_state = []
		for writerIndex, dev in enumerate(self.mic_devices[1:]):
			channels = min(2, dev["maxInputChannels"])
			rate = int(dev["defaultSampleRate"])
			tempPath = os.path.join(self.recording_folder, f"_xtrack_temp_mic_extra{writerIndex}_{timestamp}.wav")
			self.extra_mic_temp_paths.append(tempPath)
			self.extra_mic_outfiles.append(self.create_wave_file(tempPath, channels, rate))
			self.extra_mic_queues.append(queue.Queue())
			self.extra_mic_highpass_state.append({
				"channels": channels, "rate": rate,
				"prevX": [0.0] * channels, "prevY": [0.0] * channels,
				# Each microphone measures its own noise floor and level.
				"level": MicLevelProcessor(rate, channels, self.noise_gate, self.auto_level) if (self.noise_gate or self.auto_level) else None
			})
			writerThread = threading.Thread(target=self._extra_mic_writer, args=(writerIndex,), daemon=True)
			self.extra_mic_writer_threads.append(writerThread)
			writerThread.start()
			stream = self.open_extra_mic_stream(writerIndex, dev)
			stream.start_stream()
			self.extra_mic_streams.append(stream)
			log.info(f"Additional microphone stream started: {dev['name']}")

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

			mode = self.recording_mode
			needsMic = "microphone" in mode
			needsSystem = mode.startswith("system audio")
			if (needsMic and not self.default_mic) or (needsSystem and not self.default_speakers):
				raise RuntimeError(self.init_error or "No audio device is available for this recording mode")

			self.recording = 1
			self.pause_event.set()
			timestamp = datetime.now().strftime("%d-%m-%Y_%H-%M-%S")
			self.output_paths = []

			mode = self.recording_mode
			fmt = self.recording_format

			if mode == "microphone only":
				rate = int(self.default_mic["defaultSampleRate"])
				channels = min(2, self.default_mic["maxInputChannels"])
				out = os.path.join(self.recording_folder, f"recording_{timestamp}.{fmt}")
				self.output_paths = [out]

				if len(self.mic_devices) <= 1:
					if fmt == "wav":
						self.outfile = self.create_wave_file(out, channels, rate)
					else:
						self.ffmpeg_process = self.create_ffmpeg_process(out, rate, channels)
					self.writer_thread = threading.Thread(target=self._mic_only_writer, daemon=True)
					self.writer_thread.start()
				else:
					# Multiple microphones: mic_devices[0] also goes to a temp
					# WAV file (like mic_devices[1:], see
					# _setup_extra_mic_writers) instead of the real output, so
					# every microphone can be mixed together by ffmpeg once
					# recording stops (see _merge_mic_only_files_to_final).
					mic0TempPath = os.path.join(self.recording_folder, f"_xtrack_temp_mic0_{timestamp}.wav")
					self.outfile_mic = self.create_wave_file(mic0TempPath, channels, rate)
					self.writer_thread_mic = threading.Thread(target=self._mic_separate_writer, daemon=True)
					self.writer_thread_mic.start()
					self._setup_extra_mic_writers(timestamp)
					self._micOnlyTempPaths = [mic0TempPath] + self.extra_mic_temp_paths
					self._mergeFinalOutputPath = out
					self.should_merge_mics_only = True

				if self.stream_mic is None:
					self.stream_mic = self.open_mic_stream()
					self.stream_mic.start_stream()

			elif mode == "system audio only":
				rate = int(self.default_speakers["defaultSampleRate"])
				channels = min(2, self.default_speakers["maxInputChannels"])
				out = os.path.join(self.recording_folder, f"recording_{timestamp}.{fmt}")
				self.output_paths = [out]
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
				self.output_paths = [self._mergeFinalOutputPath]

				self.outfile_system = self.create_wave_file(self._mergeSystemTempPath, ch_sys, rate_sys)
				self.outfile_mic = self.create_wave_file(self._mergeMicTempPath, ch_mic, rate_mic)
				self.writer_thread_system = threading.Thread(target=self._system_separate_writer, daemon=True)
				self.writer_thread_mic = threading.Thread(target=self._mic_separate_writer, daemon=True)
				self.writer_thread_system.start()
				self.writer_thread_mic.start()

				if len(self.mic_devices) > 1:
					self._setup_extra_mic_writers(timestamp)

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
				self.output_paths = [out_sys, out_mic]
				rate_sys = int(self.default_speakers["defaultSampleRate"])
				ch_sys = min(2, self.default_speakers["maxInputChannels"])
				rate_mic = int(self.default_mic["defaultSampleRate"])
				ch_mic = min(2, self.default_mic["maxInputChannels"])

				if len(self.mic_devices) > 1:
					# Always route the mic side through temp-WAV-then-ffmpeg-mix
					# when there is more than one microphone, regardless of the
					# chosen output format, mirroring the merged-mode approach
					# above (see _merge_separate_mic_files_to_final).
					mic0TempPath = os.path.join(self.recording_folder, f"_xtrack_temp_mic0_{timestamp}.wav")
					self.outfile_mic = self.create_wave_file(mic0TempPath, ch_mic, rate_mic)
					self.writer_thread_mic = threading.Thread(target=self._mic_separate_writer, daemon=True)
					self.writer_thread_mic.start()
					self._setup_extra_mic_writers(timestamp)
					self._separateMicTempPaths = [mic0TempPath] + self.extra_mic_temp_paths
					self._separateMicFinalPath = out_mic
					self.should_merge_separate_mic_files = True

					if fmt == "wav":
						self.outfile_system = self.create_wave_file(out_sys, ch_sys, rate_sys)
						self.writer_thread_system = threading.Thread(target=self._system_separate_writer, daemon=True)
					else:
						self.ffmpeg_process_system = self.create_ffmpeg_process(out_sys, rate_sys, ch_sys)
						self.writer_thread_system = threading.Thread(target=self._system_ffmpeg_separate_writer, daemon=True)
					self.writer_thread_system.start()
				elif fmt == "wav":
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

		pendingExtraMics = []
		for extraQueue in self.extra_mic_queues:
			pending = []
			while not extraQueue.empty():
				try:
					pending.append(extraQueue.get_nowait())
				except queue.Empty:
					break
			pendingExtraMics.append(pending)

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
					if self.outfile_mic:
						# Multiple microphones: mic_devices[0] writes to its
						# own temp file (see start_recording), just like the
						# system+microphone merge above.
						self.outfile_mic.writeframes(data)
					elif self.outfile:
						self.outfile.writeframes(data)
					elif self.ffmpeg_process:
						self.ffmpeg_process.stdin.write(data)

			for writerIndex, pending in enumerate(pendingExtraMics):
				outfile = self.extra_mic_outfiles[writerIndex] if writerIndex < len(self.extra_mic_outfiles) else None
				if not outfile:
					continue
				for data in pending:
					outfile.writeframes(data)
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

			streamsToClose = [self.stream_system] + self.extra_mic_streams
			if not keepMicStreamAlive:
				streamsToClose.append(self.stream_mic)

			# Stop new audio from being captured first...
			for stream in streamsToClose:
				if stream:
					try:
						stream.stop_stream()
					except OSError as stopStreamError:
						log.debug(f"Error stopping stream during pause: {stopStreamError}")

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
					except OSError as closeStreamError:
						log.debug(f"Error closing stream during pause: {closeStreamError}")
			self.stream_system = None
			if not keepMicStreamAlive:
				self.stream_mic = None
			self.extra_mic_streams = []

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

				# Additional microphones (beyond mic_devices[0]) are always
				# plain Default-processing streams, so they are simply
				# reopened here; their queues/temp files/highpass state were
				# already set up by _setup_extra_mic_writers() and persist
				# across the pause.
				self.extra_mic_streams = []
				for writerIndex, dev in enumerate(self.mic_devices[1:]):
					stream = self.open_extra_mic_stream(writerIndex, dev)
					stream.start_stream()
					self.extra_mic_streams.append(stream)

			if self.recording_mode == "microphone only":
				if len(self.mic_devices) > 1:
					self.writer_thread_mic = threading.Thread(target=self._mic_separate_writer, daemon=True)
					self.writer_thread_mic.start()
				else:
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

			if len(self.mic_devices) > 1 and "microphone" in self.recording_mode:
				self.extra_mic_writer_threads = []
				for writerIndex in range(len(self.mic_devices) - 1):
					writerThread = threading.Thread(target=self._extra_mic_writer, args=(writerIndex,), daemon=True)
					self.extra_mic_writer_threads.append(writerThread)
					writerThread.start()

			log.info("Recording resumed")
		except Exception as e:
			log.error(f"Resume error: {e}")
			self.recording = 0
			raise

	def stop_recording(self):
		try:
			self.recording = 0
			self.pause_event.set()

			for stream in [self.stream_mic, self.stream_system] + self.extra_mic_streams:
				if stream:
					try:
						stream.stop_stream()
						stream.close()
					except OSError as closeStreamError:
						log.debug(f"Error closing stream during stop: {closeStreamError}")

			time.sleep(0.1)

			self._drain_and_write_queues()

			time.sleep(0.1)

			for wf in [self.outfile, self.outfile_system, self.outfile_mic] + self.extra_mic_outfiles:
				if wf:
					try:
						wf.close()
					except OSError as closeWaveError:
						log.debug(f"Error closing wave file during stop: {closeWaveError}")

			for proc in [self.ffmpeg_process, self.ffmpeg_process_system, self.ffmpeg_process_mic]:
				if proc:
					try:
						proc.stdin.close()
						# An m4a/mp3 file is only complete once ffmpeg has written its
						# trailer, so a long recording is given time to finish.
						proc.wait(timeout=20)
					except (OSError, subprocess.TimeoutExpired) as ffmpegWaitError:
						log.error(f"ffmpeg process did not exit cleanly, terminating: {ffmpegWaitError}")
						try:
							proc.terminate()
						except OSError as terminateError:
							log.debug(f"Error terminating ffmpeg process: {terminateError}")

			if self.should_merge_to_single_file:
				self._merge_temp_files_to_final()
			if self.should_merge_mics_only:
				self._merge_mic_only_files_to_final()
			if self.should_merge_separate_mic_files:
				self._merge_separate_mic_files_to_final()

			for writtenPath in self.output_paths:
				actualSize = os.path.getsize(writtenPath) if os.path.exists(writtenPath) else -1
				log.info(f"Recording output: {writtenPath} (size={actualSize} bytes)")

			log.info("Recording stopped")
			return list(self.output_paths)
		except Exception as e:
			log.error(f"Stop error: {e}")
			raise
		finally:
			if self.audio_interface:
				try:
					self.audio_interface.terminate()
				except OSError as terminateError:
					log.debug(f"Error releasing the audio interface: {terminateError}")
				self.audio_interface = None

	def _merge_timeout_seconds(self, inputPaths):
		"""Generous limit based on the amount of recorded audio, so a long recording is not cut off."""
		totalBytes = sum(os.path.getsize(p) for p in inputPaths if p and os.path.isfile(p))
		# 16-bit stereo at 44.1 kHz is about 176 KB per second of audio.
		recordedSeconds = totalBytes / 176400.0
		return max(300, int(recordedSeconds * 2) + 120)

	def _run_merge_command(self, cmd, inputPaths):
		"""Run one ffmpeg merge; returns True only when it finished successfully."""
		creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
		try:
			result = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
									creationflags=creationflags, timeout=self._merge_timeout_seconds(inputPaths))
		except subprocess.TimeoutExpired:
			log.error("ffmpeg merge timed out")
			return False
		except OSError as runError:
			log.error(f"ffmpeg merge could not be started: {runError}")
			return False
		if result.returncode != 0:
			stderrTail = result.stderr.decode("utf-8", errors="ignore").strip().splitlines()[-6:]
			log.error(f"ffmpeg merge exited with code {result.returncode}: {' | '.join(stderrTail)}")
			return False
		return True

	def _preserve_unmerged_temp_files(self, tempPaths, labels, finalPath):
		"""A failed merge must never cost the user the recording itself.

		The raw temp WAV files are renamed next to the intended output and
		offered as the result instead of being deleted.
		"""
		base = os.path.splitext(finalPath)[0]
		preserved = []
		for tempPath, label in zip(tempPaths, labels):
			try:
				if not tempPath or not os.path.isfile(tempPath):
					continue
				if os.path.getsize(tempPath) <= 44:
					os.remove(tempPath)
					continue
				keptPath = f"{base}_unmerged_{label}.wav"
				os.replace(tempPath, keptPath)
				preserved.append(keptPath)
			except OSError as keepError:
				log.error(f"Could not keep the unmerged recording {tempPath}: {keepError}")
				if tempPath and os.path.isfile(tempPath):
					preserved.append(tempPath)
		if preserved:
			self.output_paths = [p for p in self.output_paths if p != finalPath and os.path.exists(p)] + preserved
			self.merge_warning = "merge_failed"
			log.error(f"Merge failed; the raw recordings were kept: {preserved}")
		return preserved

	def _remove_temp_files(self, tempPaths):
		for tempPath in tempPaths:
			try:
				if tempPath and os.path.isfile(tempPath):
					os.remove(tempPath)
			except OSError as cleanupError:
				log.error(f"Failed to remove temp file {tempPath}: {cleanupError}")

	def _merge_temp_files_to_final(self):
		"""
		Merges the separately recorded system-audio and microphone temp WAV
		files into the single final output file using ffmpeg's own resampling
		and mixing (aformat + amix), then removes the temp files. This is far
		more robust than mixing raw PCM sample-for-sample in Python, since the
		microphone and system audio device can be on physically different
		sound cards with different native sample rates.
		"""
		allMicTempPaths = [self._mergeMicTempPath] + self.extra_mic_temp_paths
		keepTemps = True
		try:
			if not self._mergeSystemTempPath or not any(allMicTempPaths) or not self._mergeFinalOutputPath:
				log.error("Merge skipped: temp file paths were not set")
				keepTemps = False
				return

			systemExists = os.path.isfile(self._mergeSystemTempPath) and os.path.getsize(self._mergeSystemTempPath) > 44
			existingMicPaths = [p for p in allMicTempPaths if p and os.path.isfile(p) and os.path.getsize(p) > 44]
			micExists = len(existingMicPaths) > 0

			ffmpeg_exe = self.ffmpeg_path or os.path.join(self.libs_path, "ffmpeg.exe")
			if not os.path.isfile(ffmpeg_exe):
				raise RuntimeError(f"ffmpeg.exe not found: {ffmpeg_exe}")

			format_arg, extra_args = self._encoder_settings()
			targetRate = self.system_rate or self.mic_rate or 44100

			if systemExists and micExists:
				micInputArgs, micChain, micMixLabel = self._build_mic_mix_filter(existingMicPaths, targetRate, inputStartIndex=1)
				aformatChain = f"[0:a]aformat=sample_rates={targetRate}:channel_layouts=stereo[a0];" + micChain
				inputArgs = ["-i", self._mergeSystemTempPath] + micInputArgs

				filterComplex = (
					aformatChain +
					f"[a0][{micMixLabel}]amix=inputs=2:duration=longest:dropout_transition=0:weights=1 1.4,volume=2[aout]"
				)
				cmd = [
					ffmpeg_exe, "-y"
				] + inputArgs + [
					"-filter_complex", filterComplex,
					"-map", "[aout]"
				] + extra_args + ["-f", format_arg, self._mergeFinalOutputPath]
			elif systemExists:
				cmd = [ffmpeg_exe, "-y", "-i", self._mergeSystemTempPath] + extra_args + ["-f", format_arg, self._mergeFinalOutputPath]
			elif micExists:
				if len(existingMicPaths) == 1:
					cmd = [ffmpeg_exe, "-y", "-i", existingMicPaths[0]] + extra_args + ["-f", format_arg, self._mergeFinalOutputPath]
				else:
					inputArgs, chain, mixLabel = self._build_mic_mix_filter(existingMicPaths, targetRate, inputStartIndex=0)
					cmd = [ffmpeg_exe, "-y"] + inputArgs + ["-filter_complex", chain, "-map", f"[{mixLabel}]"] + extra_args + ["-f", format_arg, self._mergeFinalOutputPath]
			else:
				log.error("Merge skipped: neither temp file has usable audio")
				keepTemps = False
				return

			mergeInputs = ([self._mergeSystemTempPath] if systemExists else []) + existingMicPaths
			if self._run_merge_command(cmd, mergeInputs):
				keepTemps = False
				log.info(f"Merged system audio and microphone into: {self._mergeFinalOutputPath}")
		except (RuntimeError, OSError, ValueError) as mergeError:
			log.error(f"Failed to merge system and microphone recordings: {mergeError}")
		finally:
			allTempPaths = [self._mergeSystemTempPath] + allMicTempPaths
			if keepTemps:
				labels = ["system"] + ["mic"] + [f"mic{index + 2}" for index in range(len(self.extra_mic_temp_paths))]
				self._preserve_unmerged_temp_files(allTempPaths, labels, self._mergeFinalOutputPath)
			else:
				self._remove_temp_files(allTempPaths)

	def _build_mic_mix_filter(self, micTempPaths, targetRate, inputStartIndex):
		"""
		Builds the ffmpeg -i arguments and filter_complex fragment that
		decodes each of micTempPaths (resampling/reformatting to
		targetRate/stereo) and, if there is more than one, mixes them
		together into a single labeled stream. inputStartIndex is the
		ffmpeg -i index of the first mic file (0 when the mics are the only
		inputs, 1 when a system-audio file already occupies -i index 0).
		Shared by the system+microphone merge above and the
		microphone-only/separate-files mixes below, so all three multi-mic
		mixing paths stay consistent.
		"""
		inputArgs = []
		labels = []
		chain = ""
		for offset, micPath in enumerate(micTempPaths):
			inputArgs += ["-i", micPath]
			inputIdx = inputStartIndex + offset
			label = f"m{offset}"
			chain += f"[{inputIdx}:a]aformat=sample_rates={targetRate}:channel_layouts=stereo[{label}];"
			labels.append(label)
		if len(labels) > 1:
			mixInputs = "".join(f"[{l}]" for l in labels)
			chain += f"{mixInputs}amix=inputs={len(labels)}:duration=longest:dropout_transition=0,volume={len(labels)}[micmix];"
			outputLabel = "micmix"
		else:
			outputLabel = labels[0]
		return inputArgs, chain, outputLabel

	def _mix_mic_temp_files(self, tempPaths, finalPath, logNoun):
		"""
		Shared implementation for mixing N recorded-microphone temp WAV
		files into a single final output, with no system audio involved. Used for "microphone only" mode (final path is the
		recording's actual output) and the microphone side of "system and
		microphone (separate files)" mode (final path is that mode's
		separate mic output file).
		"""
		keepTemps = True
		try:
			existingPaths = [p for p in tempPaths if p and os.path.isfile(p) and os.path.getsize(p) > 44]
			if not existingPaths or not finalPath:
				log.error(f"{logNoun} mix skipped: no usable microphone temp files")
				keepTemps = False
				return

			ffmpeg_exe = self.ffmpeg_path or os.path.join(self.libs_path, "ffmpeg.exe")
			if not os.path.isfile(ffmpeg_exe):
				raise RuntimeError(f"ffmpeg.exe not found: {ffmpeg_exe}")

			format_arg, extra_args = self._encoder_settings()
			targetRate = self.mic_rate or 44100

			if len(existingPaths) == 1:
				cmd = [ffmpeg_exe, "-y", "-i", existingPaths[0]] + extra_args + ["-f", format_arg, finalPath]
			else:
				inputArgs, chain, mixLabel = self._build_mic_mix_filter(existingPaths, targetRate, inputStartIndex=0)
				cmd = [ffmpeg_exe, "-y"] + inputArgs + ["-filter_complex", chain, "-map", f"[{mixLabel}]"] + extra_args + ["-f", format_arg, finalPath]

			if self._run_merge_command(cmd, existingPaths):
				keepTemps = False
				log.info(f"Mixed {len(existingPaths)} microphone(s) into: {finalPath}")
		except (RuntimeError, OSError, ValueError) as mergeError:
			log.error(f"Failed to mix microphone recordings: {mergeError}")
		finally:
			if keepTemps and finalPath:
				labels = ["mic"] + [f"mic{index + 2}" for index in range(len(tempPaths) - 1)]
				self._preserve_unmerged_temp_files(tempPaths, labels, finalPath)
			else:
				self._remove_temp_files(tempPaths)

	def _merge_mic_only_files_to_final(self):
		self._mix_mic_temp_files(self._micOnlyTempPaths, self._mergeFinalOutputPath, "Microphone")

	def _merge_separate_mic_files_to_final(self):
		self._mix_mic_temp_files(self._separateMicTempPaths, self._separateMicFinalPath, "Microphone")
