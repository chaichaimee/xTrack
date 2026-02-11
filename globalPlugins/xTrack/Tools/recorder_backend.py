# Tools/recorder_backend.py

import os
import sys
import wave
import threading
import queue
from datetime import datetime
import struct
import subprocess
import time
import shutil
module_path = os.path.dirname(__file__)
if module_path not in sys.path:
    sys.path.append(module_path)
import pyaudiowpatch as pyaudio
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
        self.audio_interface = pyaudio.PyAudio()
        self.recording = 0
        self.stream_mic = None
        self.stream_system = None
        self.default_mic = None
        self.default_speakers = None
        self.nvda_muted = False

        try:
            log.info(f"Initializing audio devices for mode: {self.recording_mode}")

            # List all available devices for debugging
            self._log_all_devices()

            if self.recording_mode in ["microphone only", "system audio and microphone", "system audio and microphone (separate files)"]:
                self.default_mic = self._get_true_hardware_microphone()
                log.info(f"Microphone device selected: {self.default_mic['name']}")
            else:
                log.info("Microphone not needed in this mode - SKIPPED")

            if self.recording_mode in ["system audio only", "system audio and microphone", "system audio and microphone (separate files)"]:
                self.default_speakers = self._get_reliable_system_audio()
                log.info(f"System audio device selected: {self.default_speakers['name']}")
            else:
                log.info("System audio not needed in this mode - SKIPPED")

        except Exception as e:
            log.error(f"Error initializing audio devices: {e}")
            self.audio_interface.terminate()
            raise RuntimeError(f"Failed to initialize audio devices: {e}")

        self.speaker_queue = queue.Queue()
        self.mic_queue = queue.Queue()
        self.writer_thread = None
        self.writer_thread_system = None
        self.writer_thread_mic = None
        self.outfile = None
        self.outfile_system = None
        self.outfile_mic = None
        self.ffmpeg_process = None
        self.ffmpeg_process_system = None
        self.ffmpeg_process_mic = None
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.tools_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Tools")

    def _log_all_devices(self):
        """Log all available audio devices for debugging"""
        log.info("=== ALL AVAILABLE AUDIO DEVICES ===")
        for i in range(self.audio_interface.get_device_count()):
            device = self.audio_interface.get_device_info_by_index(i)
            log.info(f"Device {i}: {device['name']} - InputChannels: {device.get('maxInputChannels', 0)}, OutputChannels: {device.get('maxOutputChannels', 0)}")

    def _get_true_hardware_microphone(self):
        """Get true hardware microphone avoiding virtual devices and sound mappers"""
        try:
            # Get all input devices
            input_devices = []
            for i in range(self.audio_interface.get_device_count()):
                device = self.audio_interface.get_device_info_by_index(i)
                if device.get('maxInputChannels', 0) > 0:
                    input_devices.append(device)

            if not input_devices:
                raise RuntimeError("No input devices found")

            log.info("=== TRUE HARDWARE MICROPHONE SELECTION ===")
            
            # Strict filtering to avoid any virtual or mixed devices
            hardware_keywords = ['microphone', 'mic']
            virtual_avoid_keywords = [
                'mapper', 'primary', 'sound capture', 'loopback', 
                'stereo mix', 'what u hear', 'virtual', 'cable', 
                'voicemeeter', 'output', 'speaker', 'headphones'
            ]
            
            # Score devices based on hardware authenticity
            scored_devices = []
            
            for device in input_devices:
                name_lower = device['name'].lower()
                score = 0
                
                # Reject any device with virtual/loopback keywords
                if any(virtual in name_lower for virtual in virtual_avoid_keywords):
                    log.info(f"REJECTED (virtual/mapper): {device['name']}")
                    continue
                
                # Strong preference for devices with explicit microphone keywords
                if any(hw in name_lower for hw in hardware_keywords):
                    score += 20
                
                # Additional points for standard hardware characteristics
                sample_rate = device.get('defaultSampleRate', 0)
                if sample_rate in [44100, 48000, 96000]:
                    score += 5
                
                channels = device.get('maxInputChannels', 0)
                if 1 <= channels <= 2:  # Most hardware mics are mono or stereo
                    score += 3
                
                scored_devices.append((score, device))
                log.info(f"Device scored {score}: {device['name']}")

            if not scored_devices:
                # Last resort: use any input device but log warning
                log.warning("No ideal microphone found, using fallback input device")
                for device in input_devices:
                    name_lower = device['name'].lower()
                    if 'input' in name_lower and not any(virtual in name_lower for virtual in ['loopback', 'output']):
                        scored_devices.append((5, device))
                        log.info(f"Fallback device: {device['name']}")

            if not scored_devices:
                raise RuntimeError("No suitable microphone devices found")

            # Select the highest scored device
            scored_devices.sort(key=lambda x: x[0], reverse=True)
            selected = scored_devices[0][1]
            log.info(f"TRUE HARDWARE MICROPHONE SELECTED: {selected['name']} (score: {scored_devices[0][0]})")
            
            return selected

        except Exception as e:
            log.error(f"Error getting true hardware microphone: {e}")
            raise

    def _get_reliable_system_audio(self):
        """Get system audio using reliable method that guarantees audio capture"""
        try:
            # Get default output device
            wasapi_info = self.audio_interface.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_output_idx = wasapi_info["defaultOutputDevice"]
            default_output = self.audio_interface.get_device_info_by_index(default_output_idx)
            
            log.info(f"Default output device: {default_output['name']}")

            # Find the exact loopback device for the default output
            loopback_devices = list(self.audio_interface.get_loopback_device_info_generator())
            
            log.info("=== AVAILABLE LOOPBACK DEVICES ===")
            for device in loopback_devices:
                log.info(f"Loopback: {device['name']}")

            # Try to find loopback that matches default output name
            matching_loopback = None
            for device in loopback_devices:
                # Remove "[Loopback]" from name for comparison
                clean_name = device['name'].replace('[Loopback]', '').strip()
                if clean_name == default_output['name']:
                    matching_loopback = device
                    log.info(f"Found exact loopback match: {device['name']}")
                    break

            if matching_loopback:
                return matching_loopback

            # If no exact match, use any loopback device
            if loopback_devices:
                selected = loopback_devices[0]
                log.info(f"Using available loopback: {selected['name']}")
                return selected

            # Fallback: use default output with loopback flag
            log.info(f"Using default output as loopback: {default_output['name']}")
            return default_output

        except Exception as e:
            log.error(f"Error getting reliable system audio: {e}")
            raise

    def create_wave_file(self, path, channels, rate):
        wf = wave.open(path, "wb")
        wf.setnchannels(channels)
        wf.setsampwidth(self.audio_interface.get_sample_size(pyaudio.paInt16))
        wf.setframerate(rate)
        return wf

    def create_ffmpeg_process(self, output_file, rate, channels):
        ffmpeg_exe = os.path.join(self.tools_path, "ffmpeg.exe")
        if not os.path.isfile(ffmpeg_exe):
            raise RuntimeError(f"ffmpeg.exe not found at {ffmpeg_exe}")

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
        if not hasattr(self, 'default_speakers') or self.default_speakers is None:
            raise RuntimeError("System audio device not initialized")
        
        device_info = self.default_speakers
        log.info(f"Opening system audio stream: {device_info['name']} - Rate: {device_info['defaultSampleRate']}, Channels: {min(2, device_info['maxInputChannels'])}")
        
        try:
            stream = self.audio_interface.open(
                format=pyaudio.paInt16,
                channels=min(2, device_info["maxInputChannels"]),
                rate=int(device_info["defaultSampleRate"]),
                input=True,
                input_device_index=device_info["index"],
                frames_per_buffer=1024,
                stream_callback=self.system_callback
            )
            log.info("System audio stream opened successfully")
            return stream
        except Exception as e:
            log.error(f"Failed to open system audio stream: {e}")
            raise

    def open_mic_stream(self):
        if not hasattr(self, 'default_mic') or self.default_mic is None:
            raise RuntimeError("Microphone device not initialized")
        
        device_info = self.default_mic
        log.info(f"Opening microphone stream: {device_info['name']} - Rate: {device_info['defaultSampleRate']}, Channels: {min(2, device_info['maxInputChannels'])}")
        
        try:
            # Use shared mode as requested
            stream = self.audio_interface.open(
                format=pyaudio.paInt16,
                channels=min(2, device_info["maxInputChannels"]),
                rate=int(device_info["defaultSampleRate"]),
                input=True,
                input_device_index=device_info["index"],
                frames_per_buffer=1024,
                stream_callback=self.mic_callback
            )
            log.info("True hardware microphone stream opened successfully")
            return stream
        except Exception as e:
            log.error(f"Failed to open microphone stream: {e}")
            raise

    def system_callback(self, in_data, frame_count, time_info, status):
        if self.recording != 1:
            return (in_data, pyaudio.paContinue)
        if not self.recording_mode.startswith("system audio"):
            return (in_data, pyaudio.paContinue)
        try:
            if in_data and len(in_data) > 0 and self.system_gain > 0:
                in_data = self._apply_gain(in_data, self.system_gain)
            self.speaker_queue.put(in_data)
        except Exception as e:
            log.error(f"Error in system callback: {e}")
        return (in_data, pyaudio.paContinue)

    def mic_callback(self, in_data, frame_count, time_info, status):
        if self.recording != 1:
            return (in_data, pyaudio.paContinue)
        if "microphone" not in self.recording_mode:
            return (in_data, pyaudio.paContinue)
        try:
            if in_data and len(in_data) > 0 and self.microphone_gain > 0:
                in_data = self._apply_gain(in_data, self.microphone_gain)
            self.mic_queue.put(in_data)
        except Exception as e:
            log.error(f"Error in mic callback: {e}")
        return (in_data, pyaudio.paContinue)

    def _apply_gain(self, audio_data, gain_level):
        if gain_level <= 0:
            return audio_data
        multiplier = 1.0 + (gain_level * 0.3)
        samples = struct.unpack(f'<{len(audio_data)//2}h', audio_data)
        amplified = [max(-32768, min(32767, int(s * multiplier))) for s in samples]
        return struct.pack(f'<{len(amplified)}h', *amplified)

    def _mute_nvda_output(self):
        try:
            import win32gui
            import win32con
            hwnd = win32gui.FindWindow(None, "NVDA")
            if hwnd:
                win32gui.PostMessage(hwnd, win32con.WM_SYSCOMMAND, win32con.SC_CLOSE, 0)
                log.info("NVDA output muted during recording")
                self.nvda_muted = True
        except ImportError:
            log.warning("win32gui not available for NVDA muting")
        except Exception as e:
            log.error(f"Error muting NVDA: {e}")

    def _restore_nvda_output(self):
        if self.nvda_muted:
            try:
                import subprocess
                subprocess.Popen(["nvda.exe"], shell=True)
                log.info("NVDA output restored after recording")
                self.nvda_muted = False
            except Exception as e:
                log.error(f"Failed to restore NVDA: {e}")

    def start_recording(self):
        try:
            log.info(f"Starting recording in mode: {self.recording_mode}")
            if not os.path.exists(self.recording_folder):
                os.makedirs(self.recording_folder)
            self.pause_event.set()
            self.recording = 1
            timestamp = datetime.now().strftime("%d-%m-%Y_%H-%M-%S")

            if self.recording_mode == "microphone only":
                if not self.default_mic:
                    raise RuntimeError("Microphone not initialized")
                self._mute_nvda_output()
                output_file = os.path.join(self.recording_folder, f"recording_{timestamp}.{self.recording_format}")
                rate = int(self.default_mic["defaultSampleRate"])
                channels = min(2, self.default_mic["maxInputChannels"])
                if self.recording_format == "wav":
                    self.outfile = self.create_wave_file(output_file, channels, rate)
                else:
                    self.ffmpeg_process = self.create_ffmpeg_process(output_file, rate, channels)
                self.writer_thread = threading.Thread(target=self._mic_only_writer, daemon=True)
                self.writer_thread.start()
                self.stream_mic = self.open_mic_stream()
                self.stream_mic.start_stream()
                log.info("TRUE HARDWARE microphone-only recording started - NO SYSTEM AUDIO LEAKAGE")

            elif self.recording_mode == "system audio only":
                if not self.default_speakers:
                    raise RuntimeError("System audio not initialized")
                output_file = os.path.join(self.recording_folder, f"recording_{timestamp}.{self.recording_format}")
                rate = int(self.default_speakers["defaultSampleRate"])
                channels = min(2, self.default_speakers["maxInputChannels"])
                if self.recording_format == "wav":
                    self.outfile = self.create_wave_file(output_file, channels, rate)
                else:
                    self.ffmpeg_process = self.create_ffmpeg_process(output_file, rate, channels)
                self.writer_thread = threading.Thread(target=self._system_only_writer, daemon=True)
                self.writer_thread.start()
                self.stream_system = self.open_system_audio_stream()
                self.stream_system.start_stream()
                log.info("System audio-only recording started")

            elif self.recording_mode == "system audio and microphone":
                output_file = os.path.join(self.recording_folder, f"recording_{timestamp}.{self.recording_format}")
                rate = int(self.default_speakers["defaultSampleRate"])
                channels = 2
                if self.recording_format == "wav":
                    self.outfile = self.create_wave_file(output_file, channels, rate)
                else:
                    self.ffmpeg_process = self.create_ffmpeg_process(output_file, rate, channels)
                self.writer_thread = threading.Thread(target=self._combined_writer, daemon=True)
                self.writer_thread.start()
                self.stream_system = self.open_system_audio_stream()
                self.stream_mic = self.open_mic_stream()
                self.stream_system.start_stream()
                self.stream_mic.start_stream()
                log.info("Combined recording started")

            elif self.recording_mode == "system audio and microphone (separate files)":
                output_system = os.path.join(self.recording_folder, f"recording_system_{timestamp}.{self.recording_format}")
                output_mic = os.path.join(self.recording_folder, f"recording_mic_{timestamp}.{self.recording_format}")
                rate_system = int(self.default_speakers["defaultSampleRate"])
                channels_system = min(2, self.default_speakers["maxInputChannels"])
                rate_mic = int(self.default_mic["defaultSampleRate"])
                channels_mic = min(2, self.default_mic["maxInputChannels"])
                if self.recording_format == "wav":
                    self.outfile_system = self.create_wave_file(output_system, channels_system, rate_system)
                    self.outfile_mic = self.create_wave_file(output_mic, channels_mic, rate_mic)
                    self.writer_thread_system = threading.Thread(target=self._system_separate_writer, daemon=True)
                    self.writer_thread_mic = threading.Thread(target=self._mic_separate_writer, daemon=True)
                    self.writer_thread_system.start()
                    self.writer_thread_mic.start()
                else:
                    self.ffmpeg_process_system = self.create_ffmpeg_process(output_system, rate_system, channels_system)
                    self.ffmpeg_process_mic = self.create_ffmpeg_process(output_mic, rate_mic, channels_mic)
                    self.writer_thread_system = threading.Thread(target=self._system_ffmpeg_separate_writer, daemon=True)
                    self.writer_thread_mic = threading.Thread(target=self._mic_ffmpeg_separate_writer, daemon=True)
                    self.writer_thread_system.start()
                    self.writer_thread_mic.start()
                self.stream_system = self.open_system_audio_stream()
                self.stream_mic = self.open_mic_stream()
                self.stream_system.start_stream()
                self.stream_mic.start_stream()
                log.info("Separate files recording started")

            log.info("Recording started successfully")
        except Exception as e:
            self.recording = 0
            log.error(f"Failed to start recording: {e}")
            raise RuntimeError(f"Failed to start recording: {e}")

    def _mic_only_writer(self):
        while self.recording == 1:
            self.pause_event.wait()
            try:
                data = self.mic_queue.get(timeout=0.1)
                if data:
                    if self.recording_format == "wav":
                        self.outfile.writeframes(data)
                    else:
                        self.ffmpeg_process.stdin.write(data)
            except queue.Empty:
                continue
            except Exception as e:
                if self.recording == 1:
                    log.error(f"Error in mic only writer: {e}")

    def _system_only_writer(self):
        while self.recording == 1:
            self.pause_event.wait()
            try:
                data = self.speaker_queue.get(timeout=0.1)
                if data:
                    if self.recording_format == "wav":
                        self.outfile.writeframes(data)
                    else:
                        self.ffmpeg_process.stdin.write(data)
            except queue.Empty:
                continue
            except Exception as e:
                if self.recording == 1:
                    log.error(f"Error in system only writer: {e}")

    def _combined_writer(self):
        while self.recording == 1:
            self.pause_event.wait()
            try:
                sys_data = self.speaker_queue.get(timeout=0.1)
                mic_data = self.mic_queue.get(timeout=0.1)
                if sys_data and mic_data:
                    mixed = self._mix_audio_data(sys_data, mic_data)
                    if self.recording_format == "wav":
                        self.outfile.writeframes(mixed)
                    else:
                        self.ffmpeg_process.stdin.write(mixed)
            except queue.Empty:
                continue
            except Exception as e:
                if self.recording == 1:
                    log.error(f"Error in combined writer: {e}")

    def _mix_audio_data(self, sys_data, mic_data):
        min_len = min(len(sys_data), len(mic_data))
        sys_data = sys_data[:min_len]
        mic_data = mic_data[:min_len]
        s_samples = struct.unpack(f'<{min_len//2}h', sys_data)
        m_samples = struct.unpack(f'<{min_len//2}h', mic_data)
        mixed = [max(-32768, min(32767, int(s * 0.7 + m * 0.7))) for s, m in zip(s_samples, m_samples)]
        return struct.pack(f'<{len(mixed)}h', *mixed)

    def _system_separate_writer(self):
        while self.recording == 1:
            self.pause_event.wait()
            try:
                data = self.speaker_queue.get(timeout=0.1)
                if data and self.outfile_system:
                    self.outfile_system.writeframes(data)
            except queue.Empty:
                continue
            except Exception as e:
                if self.recording == 1:
                    log.error(f"Error in system separate writer: {e}")

    def _mic_separate_writer(self):
        while self.recording == 1:
            self.pause_event.wait()
            try:
                data = self.mic_queue.get(timeout=0.1)
                if data and self.outfile_mic:
                    self.outfile_mic.writeframes(data)
            except queue.Empty:
                continue
            except Exception as e:
                if self.recording == 1:
                    log.error(f"Error in mic separate writer: {e}")

    def _system_ffmpeg_separate_writer(self):
        while self.recording == 1:
            self.pause_event.wait()
            try:
                data = self.speaker_queue.get(timeout=0.1)
                if data and self.ffmpeg_process_system:
                    self.ffmpeg_process_system.stdin.write(data)
            except queue.Empty:
                continue
            except Exception as e:
                if self.recording == 1:
                    log.error(f"Error in system FFmpeg writer: {e}")

    def _mic_ffmpeg_separate_writer(self):
        while self.recording == 1:
            self.pause_event.wait()
            try:
                data = self.mic_queue.get(timeout=0.1)
                if data and self.ffmpeg_process_mic:
                    self.ffmpeg_process_mic.stdin.write(data)
            except queue.Empty:
                continue
            except Exception as e:
                if self.recording == 1:
                    log.error(f"Error in mic FFmpeg writer: {e}")

    def pause_recording(self):
        try:
            self.recording = 2
            self.pause_event.clear()
            if self.stream_system:
                self.stream_system.stop_stream()
            if self.stream_mic:
                self.stream_mic.stop_stream()
            log.info("Recording paused")
        except Exception as e:
            log.error(f"Error pausing: {e}")
            raise

    def resume_recording(self):
        try:
            self.recording = 1
            self.pause_event.set()
            if self.stream_system:
                self.stream_system.start_stream()
            if self.stream_mic:
                self.stream_mic.start_stream()
            log.info("Recording resumed")
        except Exception as e:
            log.error(f"Error resuming: {e}")
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
            for f in [self.outfile, self.outfile_system, self.outfile_mic]:
                if f: 
                    f.close()
            for p in [self.ffmpeg_process, self.ffmpeg_process_system, self.ffmpeg_process_mic]:
                if p:
                    try: 
                        p.stdin.close()
                        p.wait(timeout=2)
                    except: 
                        p.terminate()
            self._restore_nvda_output()
            self.audio_interface.terminate()
            log.info("Recording stopped and cleaned up")
        except Exception as e:
            log.error(f"Error stopping: {e}")
            raise
