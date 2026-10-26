# convertVideo.py

import wx
import os
import subprocess
import threading
import ui
import tones
from logHandler import log
import addonHandler

addonHandler.initTranslation()

from .xTrackCore import load_config, save_config, get_config_path
from .ffmpegSupport import limitSpokenText, summarizeFfmpegError, removeQuietly, partPathFor, runFfmpeg

def get_video_info(libs_path, file_path):
	ffprobe = os.path.join(libs_path, "ffprobe.exe")
	if not os.path.exists(ffprobe):
		return 0, 0, None, 0
	cmd = [
		ffprobe, "-v", "error",
		"-select_streams", "v:0",
		"-show_entries", "stream=width,height,avg_frame_rate",
		"-show_entries", "format=duration",
		"-of", "default=noprint_wrappers=1:nokey=1",
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
		if result.returncode != 0:
			return 0, 0, None, 0
		lines = result.stdout.strip().splitlines()
		width = height = 0
		fps = None
		duration = 0.0
		for line in lines:
			if line.isdigit():
				if width == 0:
					width = int(line)
				else:
					height = int(line)
			elif '/' in line:
				parts = line.split('/')
				if len(parts) == 2:
					try:
						num = float(parts[0])
						den = float(parts[1])
						if den > 0:
							fps = round(num / den, 2)
					except (ValueError, ZeroDivisionError):
						pass
			else:
				try:
					duration = float(line)
				except ValueError:
					pass
		return width, height, fps, duration
	except (OSError, subprocess.SubprocessError, ValueError) as e:
		log.error(f"get_video_info error: {e}")
		return 0, 0, None, 0

def format_duration(seconds):
	if seconds <= 0:
		return "0sec"
	h = int(seconds // 3600)
	m = int((seconds % 3600) // 60)
	s = int(seconds % 60)
	parts = []
	if h > 0:
		parts.append(f"{h}H")
	if m > 0 or h > 0:
		parts.append(f"{m}min")
	parts.append(f"{s}sec")
	return ''.join(parts)

def format_size(bytes):
	if bytes <= 0:
		return "0 B"
	if bytes < 1024:
		return f"{bytes} B"
	elif bytes < 1024*1024:
		return f"{bytes/1024:.1f} KB"
	elif bytes < 1024*1024*1024:
		return f"{bytes/(1024*1024):.1f} MB"
	else:
		return f"{bytes/(1024*1024*1024):.1f} GB"

# Folder, next to the originals, that holds the converted files when the user asks for a separate one.
OUTPUT_SUBFOLDER_NAME = "Converted"


class ConvertVideoDialog(wx.Dialog):
	def __init__(self, parent, selected_files, libs_path):
		super().__init__(parent, title=_("Convert Video"))
		self.selected_files = list(selected_files)
		self.libs_path = libs_path
		self.output_path = os.path.dirname(self.selected_files[0]) if self.selected_files else os.getcwd()
		self.config_path = get_config_path()
		self.ffmpeg_process = None
		self.is_running = False
		self.is_closed = False
		self.info_ready = False
		self.cancel_event = threading.Event()
		self.file_info = {}
		self.current_index = 0
		self.user_selected_target = None
		self.last_announced_progress = -10
		self.used_subfolder = False
		self.init_ui()
		self.SetTitle(_("Convert Video: {} files").format(len(self.selected_files)))
		self.load_settings()
		self.Bind(wx.EVT_CLOSE, self.on_close)
		self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
		self.status.SetLabel(_("Reading file information..."))
		threading.Thread(target=self._load_all_info, daemon=True).start()

	def post(self, handler, *args):
		wx.CallAfter(self.run_if_open, handler, *args)

	def run_if_open(self, handler, *args):
		if self.is_closed:
			return
		handler(*args)

	def on_key(self, event):
		if event.GetKeyCode() == wx.WXK_ESCAPE:
			self.on_cancel(None)
		else:
			event.Skip()

	def _load_all_info(self):
		for path in self.selected_files:
			if self.is_closed:
				return
			w, h, fps, dur_sec = get_video_info(self.libs_path, path)
			try:
				size_bytes = os.path.getsize(path)
			except OSError:
				size_bytes = 0
			self.file_info[path] = {
				'duration': format_duration(dur_sec),
				'size': format_size(size_bytes),
				'width': w,
				'height': h,
				'fps': fps,
				'duration_sec': dur_sec,
			}
			log.info(f"Loaded {os.path.basename(path)}: {w}x{h}, fps={fps}, dur={dur_sec}")
		self.info_ready = True
		self.post(self._on_info_loaded)

	def _on_info_loaded(self):
		self.status.SetLabel("")
		selection = self.file_list.GetSelection()
		self.update_file_listbox()
		if not self.selected_files:
			return
		index = selection if selection != wx.NOT_FOUND else 0
		self.update_current_info(index)
		self.file_list.SetSelection(index)

	def init_ui(self):
		main_sizer = wx.BoxSizer(wx.VERTICAL)

		file_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Files to Convert"))
		self.file_list = wx.ListBox(self)
		self.file_list.Bind(wx.EVT_LISTBOX, self.on_file_selection)
		file_sizer.Add(self.file_list, 1, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(file_sizer, 1, wx.EXPAND | wx.ALL, 5)

		info_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Current File"))
		self.file_label = wx.StaticText(self, label="")
		info_sizer.Add(self.file_label, 0, wx.EXPAND | wx.ALL, 5)
		self.duration_label = wx.StaticText(self, label=_("Duration: ..."))
		self.res_label = wx.StaticText(self, label=_("Resolution: ..."))
		self.fps_label = wx.StaticText(self, label=_("Frame rate: ..."))
		self.size_label = wx.StaticText(self, label=_("Size: ..."))
		info_sizer.Add(self.duration_label, 0, wx.EXPAND | wx.ALL, 5)
		info_sizer.Add(self.res_label, 0, wx.EXPAND | wx.ALL, 5)
		info_sizer.Add(self.fps_label, 0, wx.EXPAND | wx.ALL, 5)
		info_sizer.Add(self.size_label, 0, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(info_sizer, 0, wx.EXPAND | wx.ALL, 5)

		settings_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Conversion Settings"))

		format_sizer = wx.BoxSizer(wx.HORIZONTAL)
		format_sizer.Add(wx.StaticText(self, label=_("Output Format:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.format_combo = wx.ComboBox(self, choices=["MP4", "MKV", "MOV", "AVI", "WebM"], style=wx.CB_READONLY)
		self.format_combo.SetStringSelection("MP4")
		format_sizer.Add(self.format_combo, 1, wx.EXPAND | wx.ALL, 5)
		settings_sizer.Add(format_sizer, 0, wx.EXPAND | wx.ALL, 5)

		target_sizer = wx.BoxSizer(wx.HORIZONTAL)
		target_sizer.Add(wx.StaticText(self, label=_("Target Size:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.target_combo = wx.ComboBox(self, choices=[
			"Keep Original (No resize)",
			"3840x2160 (4K)",
			"1920x1080 (Full HD)",
			"1280x720 (HD)",
			"1024x576 (576p)",
			"854x480 (480p)",
			"640x360 (360p)",
			"426x240 (240p)",
			"320x180 (180p)"
		], style=wx.CB_READONLY)
		self.target_combo.SetStringSelection("Keep Original (No resize)")
		self.target_combo.Bind(wx.EVT_COMBOBOX, self.on_target_changed)
		target_sizer.Add(self.target_combo, 1, wx.EXPAND | wx.ALL, 5)
		settings_sizer.Add(target_sizer, 0, wx.EXPAND | wx.ALL, 5)

		audio_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Audio Settings"))

		sr_sizer = wx.BoxSizer(wx.HORIZONTAL)
		sr_sizer.Add(wx.StaticText(self, label=_("Sample Rate:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.sample_rate_combo = wx.ComboBox(self, choices=["Keep Original", "44100 Hz", "48000 Hz"], style=wx.CB_READONLY)
		self.sample_rate_combo.SetStringSelection("Keep Original")
		sr_sizer.Add(self.sample_rate_combo, 1, wx.EXPAND | wx.ALL, 5)
		audio_sizer.Add(sr_sizer, 0, wx.EXPAND | wx.ALL, 5)

		ch_sizer = wx.BoxSizer(wx.HORIZONTAL)
		ch_sizer.Add(wx.StaticText(self, label=_("Channels:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.channels_combo = wx.ComboBox(self, choices=["Keep Original", "Stereo (2)", "Mono (1)"], style=wx.CB_READONLY)
		self.channels_combo.SetStringSelection("Keep Original")
		ch_sizer.Add(self.channels_combo, 1, wx.EXPAND | wx.ALL, 5)
		audio_sizer.Add(ch_sizer, 0, wx.EXPAND | wx.ALL, 5)

		codec_sizer = wx.BoxSizer(wx.HORIZONTAL)
		codec_sizer.Add(wx.StaticText(self, label=_("Audio Codec:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.codec_combo = wx.ComboBox(self, choices=["Keep Original", "AAC (Recommended)", "MP3", "Vorbis"], style=wx.CB_READONLY)
		self.codec_combo.SetStringSelection("AAC (Recommended)")
		codec_sizer.Add(self.codec_combo, 1, wx.EXPAND | wx.ALL, 5)
		audio_sizer.Add(codec_sizer, 0, wx.EXPAND | wx.ALL, 5)

		settings_sizer.Add(audio_sizer, 0, wx.EXPAND | wx.ALL, 5)

		main_sizer.Add(settings_sizer, 0, wx.EXPAND | wx.ALL, 5)

		# Unchecked by default: the converted files are saved next to the originals.
		self.subfolder_checkbox = wx.CheckBox(self, label=_("Save converted files in a separate subfolder"))
		self.subfolder_checkbox.SetValue(False)
		self.subfolder_checkbox.SetToolTip(_("Unchecked: the converted files are saved together with the originals. Checked: they are saved in a subfolder named {}.").format(OUTPUT_SUBFOLDER_NAME))
		main_sizer.Add(self.subfolder_checkbox, 0, wx.ALL, 5)

		self.progress = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL)
		main_sizer.Add(self.progress, 0, wx.EXPAND | wx.ALL, 5)
		self.status = wx.StaticText(self, label="")
		main_sizer.Add(self.status, 0, wx.EXPAND | wx.ALL, 5)

		btn_sizer = wx.StdDialogButtonSizer()
		self.convert_btn = wx.Button(self, wx.ID_OK, _("Convert"))
		self.convert_btn.Bind(wx.EVT_BUTTON, self.on_convert)
		btn_sizer.AddButton(self.convert_btn)
		self.cancel_btn = wx.Button(self, wx.ID_CANCEL, _("Cancel"))
		self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
		btn_sizer.AddButton(self.cancel_btn)
		btn_sizer.Realize()
		main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

		self.SetSizer(main_sizer)
		self.Fit()

	def on_target_changed(self, event):
		self.user_selected_target = self.target_combo.GetStringSelection()
		event.Skip()

	def update_file_listbox(self):
		items = []
		for path in self.selected_files:
			info = self.file_info.get(path, {})
			name = os.path.basename(path)
			dur = info.get('duration', _("..."))
			w = info.get('width', 0)
			h = info.get('height', 0)
			fps = info.get('fps')
			fps_str = f"{fps:.2f}fps" if fps else "?"
			size = info.get('size', _("..."))
			items.append(f"{name} ({dur}, {w}x{h}, {fps_str}, {size})")
		self.file_list.Set(items)

	def on_file_selection(self, event):
		idx = self.file_list.GetSelection()
		if idx != wx.NOT_FOUND:
			self.update_current_info(idx)

	def update_current_info(self, idx):
		if idx >= len(self.selected_files):
			return
		self.current_index = idx
		path = self.selected_files[idx]
		info = self.file_info.get(path, {})
		self.file_label.SetLabel(os.path.basename(path))
		self.duration_label.SetLabel(_("Duration: {}").format(info.get('duration', _("?"))))
		w = info.get('width', 0)
		h = info.get('height', 0)
		fps = info.get('fps')
		fps_str = f"{fps:.2f}fps" if fps else _("unknown")
		self.res_label.SetLabel(_("Resolution: {}x{}").format(w, h) if w and h else _("Resolution: unknown"))
		self.fps_label.SetLabel(_("Frame rate: {}").format(fps_str))
		self.size_label.SetLabel(_("Size: {}").format(info.get('size', _("?"))))
		if self.user_selected_target is None:
			self._auto_select_preset(w, h)

	def _auto_select_preset(self, w, h):
		presets = {
			(3840, 2160): "3840x2160 (4K)",
			(1920, 1080): "1920x1080 (Full HD)",
			(1280, 720): "1280x720 (HD)",
			(1024, 576): "1024x576 (576p)",
			(854, 480): "854x480 (480p)",
			(640, 360): "640x360 (360p)",
			(426, 240): "426x240 (240p)",
			(320, 180): "320x180 (180p)"
		}
		if (w, h) in presets:
			self.target_combo.SetStringSelection(presets[(w, h)])
			self.user_selected_target = presets[(w, h)]
		else:
			self.target_combo.SetStringSelection("Keep Original (No resize)")
			self.user_selected_target = "Keep Original (No resize)"

	def load_settings(self):
		cfg = load_config(self.config_path)
		self.format_combo.SetStringSelection(cfg.get("ConvertVideoFormat", "MP4"))
		saved_target = cfg.get("ConvertVideoTarget", "Keep Original (No resize)")
		self.target_combo.SetStringSelection(saved_target)
		self.user_selected_target = saved_target
		self.sample_rate_combo.SetStringSelection(cfg.get("ConvertVideoSampleRate", "Keep Original"))
		self.channels_combo.SetStringSelection(cfg.get("ConvertVideoChannels", "Keep Original"))
		self.codec_combo.SetStringSelection(cfg.get("ConvertVideoCodec", "AAC (Recommended)"))

	def save_settings(self):
		cfg = load_config(self.config_path)
		cfg["ConvertVideoFormat"] = self.format_combo.GetStringSelection()
		cfg["ConvertVideoTarget"] = self.target_combo.GetStringSelection()
		cfg["ConvertVideoSampleRate"] = self.sample_rate_combo.GetStringSelection()
		cfg["ConvertVideoChannels"] = self.channels_combo.GetStringSelection()
		cfg["ConvertVideoCodec"] = self.codec_combo.GetStringSelection()
		save_config(self.config_path, cfg)

	def audio_args_for(self, outFormat, codecChoice, sampleRate, channels):
		args = []
		if sampleRate != "Keep Original":
			args.extend(["-ar", sampleRate.replace(" Hz", "")])
		if channels == "Stereo (2)":
			args.extend(["-ac", "2"])
		elif channels == "Mono (1)":
			args.extend(["-ac", "1"])

		# Containers only accept certain audio codecs: WebM needs Vorbis (a
		# copied or AAC stream is rejected) and MP4/MOV cannot hold Vorbis.
		if outFormat == "webm":
			codecChoice = "Vorbis"
		elif outFormat in ("mp4", "mov") and codecChoice == "Vorbis":
			codecChoice = "AAC (Recommended)"

		if codecChoice == "AAC (Recommended)":
			args.extend(["-c:a", "aac", "-b:a", "192k"])
		elif codecChoice == "MP3":
			args.extend(["-c:a", "libmp3lame", "-b:a", "192k"])
		elif codecChoice == "Vorbis":
			args.extend(["-c:a", "libvorbis", "-b:a", "192k"])
		else:
			args.extend(["-c:a", "copy"])
		return args

	def build_jobs(self):
		ffmpegPath = os.path.join(self.libs_path, "ffmpeg.exe")
		if not os.path.exists(ffmpegPath):
			ui.message(_("ffmpeg.exe not found"))
			return []

		targetString = self.target_combo.GetStringSelection()
		outFormat = self.format_combo.GetStringSelection().lower()
		if outFormat not in ("mp4", "mkv", "mov", "avi", "webm"):
			outFormat = "mp4"
		audioArgs = self.audio_args_for(
			outFormat,
			self.codec_combo.GetStringSelection(),
			self.sample_rate_combo.GetStringSelection(),
			self.channels_combo.GetStringSelection(),
		)
		# Leave one core free for the rest of the system (document editors,
		# NVDA itself, etc.) instead of letting x264 claim every core.
		# os.cpu_count() is used instead of the multiprocessing module,
		# since multiprocessing is not available in NVDA's bundled Python.
		encoderThreads = max(1, (os.cpu_count() or 4) - 1)

		useSubfolder = self.subfolder_checkbox.GetValue()
		outputDir = os.path.join(self.output_path, OUTPUT_SUBFOLDER_NAME) if useSubfolder else self.output_path
		self.used_subfolder = useSubfolder

		jobs = []
		reservedNames = set()
		for listIndex, path in enumerate(self.selected_files):
			info = self.file_info.get(path, {})
			srcWidth = info.get('width', 0)
			srcHeight = info.get('height', 0)

			doResize = False
			targetWidth = targetHeight = 0
			if targetString != "Keep Original (No resize)":
				try:
					targetWidth, targetHeight = map(int, targetString.split()[0].split('x'))
				except (ValueError, IndexError):
					log.error(f"Invalid target string: {targetString}")
					ui.message(_("Invalid target size"))
					return []
				targetWidth -= targetWidth % 2
				targetHeight -= targetHeight % 2
				if srcWidth == 0 or srcHeight == 0:
					ui.message(limitSpokenText(
						_("Cannot read the video size of {}").format(os.path.basename(path))
					))
					return []
				doResize = srcWidth > targetWidth or srcHeight > targetHeight

			baseName = os.path.splitext(os.path.basename(path))[0]
			finalName = f"{baseName}.{outFormat}"
			counter = 1
			while os.path.join(outputDir, finalName).lower() in reservedNames or os.path.exists(os.path.join(outputDir, finalName)):
				finalName = f"{baseName}_{counter}.{outFormat}"
				counter += 1
			reservedNames.add(os.path.join(outputDir, finalName).lower())
			finalPath = os.path.join(outputDir, finalName)
			partPath = partPathFor(finalPath)

			# Only the first video stream and the audio streams are kept;
			# subtitle streams are dropped because several containers reject them.
			command = [ffmpegPath, "-hide_banner", "-i", path, "-map", "0:v:0", "-map", "0:a?", "-sn"]
			command.extend(["-progress", "pipe:1", "-nostats", "-y"])

			if doResize:
				command.extend(["-vf", f"scale={targetWidth}:{targetHeight}:force_original_aspect_ratio=decrease,format=yuv420p,crop=trunc(iw/2)*2:trunc(ih/2)*2"])
				if outFormat == "webm":
					command.extend(["-c:v", "libvpx", "-crf", "10", "-b:v", "1M"])
				else:
					# "veryfast" keeps the same -crf quality target as "medium" but
					# encodes several times quicker; the trade-off is a somewhat
					# larger file for the same visual quality, not lower quality.
					command.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"])
			elif outFormat == "webm":
				# A copied stream is almost never WebM-compatible.
				command.extend(["-c:v", "libvpx", "-crf", "10", "-b:v", "1M"])
			else:
				command.extend(["-c:v", "copy"])

			command.extend(["-threads", str(encoderThreads)])
			command.extend(audioArgs)
			command.append(partPath)
			log.info(f"FFmpeg cmd: {' '.join(command)}")
			jobs.append((path, finalPath, partPath, command, listIndex, info.get('duration_sec', 0)))
		return jobs

	def on_convert(self, event):
		if self.is_running:
			return
		if not self.info_ready:
			ui.message(_("Still reading file information, please try again in a moment."))
			return
		jobs = self.build_jobs()
		if not jobs:
			return
		self.save_settings()
		tones.beep(800, 200)
		self.is_running = True
		self.cancel_event.clear()
		self.convert_btn.Enable(False)
		self.cancel_btn.Enable(False)
		threading.Thread(target=self.run_queue, args=(jobs,), daemon=True).start()

	def run_queue(self, jobs):
		failure = None
		for job in jobs:
			if self.cancel_event.is_set():
				break
			sourcePath, finalPath, partPath, command, listIndex, durationSeconds = job
			self.post(self.on_job_started, listIndex)
			succeeded, reason = self.run_job(sourcePath, partPath, finalPath, command, durationSeconds)
			if self.cancel_event.is_set():
				break
			if not succeeded:
				failure = (sourcePath, reason)
				break
			self.post(self.on_success, sourcePath, finalPath)
		if not self.cancel_event.is_set():
			self.post(self.on_queue_finished, failure)

	def run_job(self, sourcePath, partPath, finalPath, command, durationSeconds):
		def registerProcess(process):
			self.ffmpeg_process = process

		try:
			os.makedirs(os.path.dirname(finalPath), exist_ok=True)
		except OSError as error:
			log.error(f"xTrack: could not create the folder for {finalPath}: {error}")
			return False, _("the folder for the converted files could not be created")

		try:
			# BELOW_NORMAL_PRIORITY_CLASS lets Windows favor whatever the user
			# is actively doing over the encode, so a long conversion does not
			# make the rest of the machine feel sluggish on older PCs.
			exitCode, stderrLines = runFfmpeg(
				command, self.output_path, durationSeconds,
				lambda progress: self.post(self.update_progress, progress), registerProcess,
				lowPriority=True,
			)
		except OSError as error:
			log.error(f"xTrack: could not run ffmpeg for {sourcePath}: {error}")
			removeQuietly(partPath)
			return False, _("ffmpeg could not be started")

		if self.cancel_event.is_set():
			removeQuietly(partPath)
			return False, ""
		if exitCode == 0:
			try:
				os.replace(partPath, finalPath)
			except OSError as error:
				log.error(f"xTrack: could not move {partPath} to {finalPath}: {error}")
				removeQuietly(partPath)
				return False, _("the output file could not be written")
			return True, ""
		log.error(
			f"Conversion failed for {sourcePath} (exit code {exitCode}): "
			+ " | ".join(line.strip() for line in stderrLines)
		)
		removeQuietly(partPath)
		return False, summarizeFfmpegError(stderrLines)

	def on_job_started(self, listIndex):
		self.update_current_info(listIndex)
		self.file_list.SetSelection(listIndex)
		self.last_announced_progress = -10
		self.progress.SetValue(0)
		self.status.SetLabel(_("Converting..."))

	def update_progress(self, val):
		self.progress.SetValue(val)
		self.status.SetLabel(_("{}%").format(val))
		if val % 10 == 0 and val > 0 and val != self.last_announced_progress:
			self.last_announced_progress = val
			ui.message(_("Progress: {}%").format(val))
			tones.beep(800, 50)

	def on_success(self, sourcePath, outPath):
		ui.message(limitSpokenText(_("Conversion complete: {}").format(os.path.basename(outPath))))

	def on_queue_finished(self, failure):
		self.is_running = False
		self.convert_btn.Enable(True)
		self.cancel_btn.Enable(True)
		if failure:
			sourcePath, reason = failure
			self.status.SetLabel(_("Conversion failed"))
			tones.beep(300, 300)
			ui.message(limitSpokenText(
				_("Conversion failed for {name}: {reason}").format(name=os.path.basename(sourcePath), reason=reason)
			))
			return
		self.status.SetLabel(_("All conversions finished"))
		message = _("All conversions finished")
		if self.used_subfolder:
			message += ". " + _("Saved in the folder {}").format(OUTPUT_SUBFOLDER_NAME)
		ui.message(limitSpokenText(message))
		tones.beep(1000, 200)
		self.EndModal(wx.ID_OK)

	def stop_conversion(self):
		self.is_closed = True
		self.cancel_event.set()
		process = self.ffmpeg_process
		if process and process.poll() is None:
			try:
				process.terminate()
			except OSError as error:
				log.debugWarning(f"xTrack: could not stop ffmpeg: {error}")

	def on_cancel(self, event):
		self.stop_conversion()
		self.EndModal(wx.ID_CANCEL)

	def on_close(self, event):
		self.stop_conversion()
		self.EndModal(wx.ID_CANCEL)
