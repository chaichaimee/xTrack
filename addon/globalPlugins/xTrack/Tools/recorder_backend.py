# tools/recorder_backend.py

import os
import wave
import threading
import queue
from datetime import datetime
import struct
import subprocess
import time

try:
	import pyaudiowpatch as pyaudio
except ImportError:
	import pyaudio

from logHandler import log

class WasapiSoundRecorder:
	def __init__(self, recording_format="wav",
				 recording_folder=os.path.join(os.environ['USERPROFILE'], "Documents", "Easy Sound Recorder"),
				 recording_mode="system audio and microphone",
				 ffmpeg_path="",
				 system_gain=0,
				 microphone_gain=0):
		self.recording_format = recording_format.strip().lower()
		self.recording_folder = recording_folder.strip()
		self.recording_mode = recording_mode.strip().lower()
		self.ffmpeg_path = ffmpeg_path.strip()
		self.system_gain = max(0, min(10, system_gain))
		self.microphone_gain = max(0, min(10, microphone_gain))

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

		self.tools_path = os.path.dirname(os.path.dirname(__file__))

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
		ffmpeg_exe = self.ffmpeg_path or os.path.join(self.tools_path, "ffmpeg.exe")
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
		stream = self.audio_interface.open(
			format=pyaudio.paInt16,
			channels=min(2, dev["maxInputChannels"]),
			rate=int(dev["defaultSampleRate"]),
			input=True,
			input_device_index=dev["index"],
			frames_per_buffer=1024,
			stream_callback=self._system_callback
		)
		return stream

	def open_mic_stream(self):
		dev = self.default_mic
		stream = self.audio_interface.open(
			format=pyaudio.paInt16,
			channels=min(2, dev["maxInputChannels"]),
			rate=int(dev["defaultSampleRate"]),
			input=True,
			input_device_index=dev["index"],
			frames_per_buffer=1024,
			stream_callback=self._mic_callback
		)
		return stream

	def _apply_gain(self, data, gain):
		if gain <= 0:
			return data
		multiplier = 1.0 + (gain * 0.3)
		samples = struct.unpack(f'<{len(data)//2}h', data)
		amplified = [max(-32768, min(32767, int(s * multiplier))) for s in samples]
		return struct.pack(f'<{len(amplified)}h', *amplified)

	def _system_callback(self, in_data, frame_count, time_info, status):
		if self.recording != 1:
			return (in_data, pyaudio.paAbort)
		if not self.recording_mode.startswith("system audio"):
			return (in_data, pyaudio.paContinue)
		try:
			if in_data and self.system_gain > 0:
				in_data = self._apply_gain(in_data, self.system_gain)
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
			if in_data and self.microphone_gain > 0:
				in_data = self._apply_gain(in_data, self.microphone_gain)
			self.mic_queue.put(in_data)
			self.last_mic_data = in_data
		except Exception as e:
			log.error(f"Mic callback error: {e}")
		return (in_data, pyaudio.paContinue)

	def _mix_audio(self, sys_data, mic_data):
		min_len = min(len(sys_data), len(mic_data))
		sys_data = sys_data[:min_len]
		mic_data = mic_data[:min_len]
		s_samples = struct.unpack(f'<{min_len//2}h', sys_data)
		m_samples = struct.unpack(f'<{min_len//2}h', mic_data)
		mixed = [max(-32768, min(32767, int(s * 0.7 + m * 0.7))) for s, m in zip(s_samples, m_samples)]
		return struct.pack(f'<{len(mixed)}h', *mixed)

	def _get_data_with_fallback(self, data_queue, last_data):
		try:
			return data_queue.get(timeout=0.05)
		except queue.Empty:
			if last_data is not None:
				return last_data
			return None

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
				rate = int(self.default_speakers["defaultSampleRate"])
				channels = 2
				out = os.path.join(self.recording_folder, f"recording_{timestamp}.{fmt}")
				if fmt == "wav":
					self.outfile = self.create_wave_file(out, channels, rate)
				else:
					self.ffmpeg_process = self.create_ffmpeg_process(out, rate, channels)
				self.writer_thread = threading.Thread(target=self._combined_writer, daemon=True)
				self.writer_thread.start()
				self.stream_system = self.open_system_audio_stream()
				self.stream_mic = self.open_mic_stream()
				self.stream_system.start_stream()
				self.stream_mic.start_stream()

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
				self.stream_mic = self.open_mic_stream()
				self.stream_system.start_stream()
				self.stream_mic.start_stream()

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

	def _combined_writer(self):
		while self.recording == 1:
			self.pause_event.wait()
			try:
				sys_data = self._get_data_with_fallback(self.speaker_queue, self.last_speaker_data)
				mic_data = self._get_data_with_fallback(self.mic_queue, self.last_mic_data)

				if sys_data is not None and mic_data is not None and len(sys_data) > 0 and len(mic_data) > 0:
					mixed = self._mix_audio(sys_data, mic_data)
					if self.recording_format == "wav":
						self.outfile.writeframes(mixed)
					else:
						self.ffmpeg_process.stdin.write(mixed)
				elif sys_data is not None and mic_data is None:
					if self.recording_format == "wav":
						self.outfile.writeframes(sys_data)
					else:
						self.ffmpeg_process.stdin.write(sys_data)
				elif mic_data is not None and sys_data is None:
					if self.recording_format == "wav":
						self.outfile.writeframes(mic_data)
					else:
						self.ffmpeg_process.stdin.write(mic_data)
			except queue.Empty:
				continue
			except Exception as e:
				if self.recording == 1:
					log.error(f"Combined writer error: {e}")

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

	def pause_recording(self):
		try:
			if self.recording != 1:
				return

			self.recording = 2

			for stream in [self.stream_mic, self.stream_system]:
				if stream:
					try:
						stream.stop_stream()
						stream.close()
					except:
						pass
			self.stream_mic = None
			self.stream_system = None

			while not self.speaker_queue.empty():
				try:
					self.speaker_queue.get_nowait()
				except queue.Empty:
					break
			while not self.mic_queue.empty():
				try:
					self.mic_queue.get_nowait()
				except queue.Empty:
					break

			self.last_speaker_data = None
			self.last_mic_data = None

			self.pause_event.clear()
			log.info("Recording paused")
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
				self.stream_mic = self.open_mic_stream()
				self.stream_mic.start_stream()

			if self.recording_mode == "microphone only":
				self.writer_thread = threading.Thread(target=self._mic_only_writer, daemon=True)
				self.writer_thread.start()
			elif self.recording_mode == "system audio only":
				self.writer_thread = threading.Thread(target=self._system_only_writer, daemon=True)
				self.writer_thread.start()
			elif self.recording_mode == "system audio and microphone":
				self.writer_thread = threading.Thread(target=self._combined_writer, daemon=True)
				self.writer_thread.start()
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

			while not self.speaker_queue.empty():
				try:
					data = self.speaker_queue.get_nowait()
					if self.recording_mode == "system audio and microphone":
						if self.outfile:
							self.outfile.writeframes(data)
						elif self.ffmpeg_process:
							self.ffmpeg_process.stdin.write(data)
				except queue.Empty:
					break
				except Exception as e:
					log.error(f"Final flush system error: {e}")

			while not self.mic_queue.empty():
				try:
					data = self.mic_queue.get_nowait()
					if self.recording_mode == "system audio and microphone":
						if self.outfile:
							self.outfile.writeframes(data)
						elif self.ffmpeg_process:
							self.ffmpeg_process.stdin.write(data)
				except queue.Empty:
					break
				except Exception as e:
					log.error(f"Final flush mic error: {e}")

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

			if self.audio_interface:
				self.audio_interface.terminate()

			log.info("Recording stopped")
		except Exception as e:
			log.error(f"Stop error: {e}")
			raise