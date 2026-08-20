# convertVideo.py
import wx
import os
import subprocess
import threading
import ui
import tones
import queue
from logHandler import log
import addonHandler

addonHandler.initTranslation()

from .xTrackCore import load_config, save_config, get_config_path, get_unique_filename

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
					except:
						pass
			else:
				try:
					duration = float(line)
				except:
					pass
		return width, height, fps, duration
	except Exception as e:
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

class ConvertVideoDialog(wx.Dialog):
	def __init__(self, parent, selected_files, libs_path):
		super().__init__(parent, title=_("Convert Video"))
		self.selected_files = selected_files
		self.libs_path = libs_path
		self.output_path = os.path.dirname(self.selected_files[0]) if self.selected_files else os.getcwd()
		self.config_path = get_config_path()
		self.ffmpeg_process = None
		self.conversion_queue = queue.Queue()
		self.is_processing = False
		self.file_info = {}
		self.current_index = 0
		self.user_selected_target = None
		self.last_announced_progress = -10  # Store last announced percentage to avoid repeats
		self.init_ui()
		self.SetTitle(_("Convert Video: {} files").format(len(self.selected_files)))
		self.load_settings()
		self.Bind(wx.EVT_CLOSE, self.on_close)
		self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
		self._load_all_info()

	def on_key(self, event):
		if event.GetKeyCode() == wx.WXK_ESCAPE:
			self.on_cancel(None)
		else:
			event.Skip()

	def _load_all_info(self):
		wx.BeginBusyCursor()
		try:
			for path in self.selected_files:
				w, h, fps, dur_sec = get_video_info(self.libs_path, path)
				size_bytes = os.path.getsize(path) if os.path.exists(path) else 0
				size_str = format_size(size_bytes)
				dur_str = format_duration(dur_sec)
				fps_str = f"{fps:.2f}fps" if fps else "?"
				self.file_info[path] = {
					'duration': dur_str,
					'size': size_str,
					'width': w,
					'height': h,
					'fps': fps,
					'duration_sec': dur_sec
				}
				log.info(f"Loaded {os.path.basename(path)}: {w}x{h}, {fps_str}, dur={dur_sec}")
		finally:
			wx.EndBusyCursor()
		self.update_file_listbox()
		if self.selected_files:
			self.update_current_info(0)
			self.file_list.SetSelection(0)

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

	def on_convert(self, event):
		self.save_settings()
		try:
			tones.beep(800, 200)
		except:
			pass
		for path in self.selected_files:
			self.conversion_queue.put(path)
		self.is_processing = True
		self.convert_btn.Enable(False)
		self.cancel_btn.Enable(False)
		self.process_next()

	def process_next(self):
		if self.conversion_queue.empty():
			self.is_processing = False
			wx.CallAfter(self.on_all_done)
			return

		path = self.conversion_queue.get()
		idx = self.selected_files.index(path)
		self.update_current_info(idx)

		info = self.file_info.get(path, {})
		src_w = info.get('width', 0)
		src_h = info.get('height', 0)
		if src_w == 0 or src_h == 0:
			log.error(f"Cannot get resolution for {path}")
			self.on_failure(_("Cannot read video resolution"), path)
			return

		target_str = self.target_combo.GetStringSelection()
		log.info(f"Target string from combo: {target_str}")

		if target_str == "Keep Original (No resize)":
			target_w, target_h = src_w, src_h
			do_resize = False
		else:
			try:
				dim_part = target_str.split()[0]
				target_w, target_h = map(int, dim_part.split('x'))
				if target_w % 2 != 0:
					target_w -= 1
				if target_h % 2 != 0:
					target_h -= 1
			except:
				log.error(f"Invalid target string: {target_str}")
				self.on_failure(_("Invalid target size"), path)
				return
			do_resize = (src_w > target_w or src_h > target_h)

		out_fmt = self.format_combo.GetStringSelection().lower()
		ext_map = {"mp4":"mp4", "mkv":"mkv", "mov":"mov", "avi":"avi", "webm":"webm"}
		out_ext = ext_map.get(out_fmt, "mp4")

		base = os.path.splitext(os.path.basename(path))[0]
		out_name = get_unique_filename(self.output_path, base, out_ext)
		out_path = os.path.join(self.output_path, out_name)

		ffmpeg = os.path.join(self.libs_path, "ffmpeg.exe")
		if not os.path.exists(ffmpeg):
			self.on_failure(_("ffmpeg.exe not found"), path)
			return

		cmd = [ffmpeg, "-i", path, "-progress", "pipe:1", "-nostats", "-y"]

		if do_resize:
			cmd.extend(["-vf", f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,format=yuv420p,crop=trunc(iw/2)*2:trunc(ih/2)*2"])
			if out_fmt in ["mp4", "mkv", "mov", "avi"]:
				cmd.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "23"])
			elif out_fmt == "webm":
				cmd.extend(["-c:v", "libvpx", "-crf", "10", "-b:v", "1M"])
		else:
			cmd.extend(["-c:v", "copy"])

		sr = self.sample_rate_combo.GetStringSelection()
		if sr != "Keep Original":
			cmd.extend(["-ar", sr.replace(" Hz", "")])
		ch = self.channels_combo.GetStringSelection()
		if ch == "Stereo (2)":
			cmd.extend(["-ac", "2"])
		elif ch == "Mono (1)":
			cmd.extend(["-ac", "1"])
		codec = self.codec_combo.GetStringSelection()
		if codec == "AAC (Recommended)":
			cmd.extend(["-c:a", "aac", "-b:a", "192k"])
		elif codec == "MP3":
			cmd.extend(["-c:a", "libmp3lame", "-b:a", "192k"])
		elif codec == "Vorbis":
			cmd.extend(["-c:a", "libvorbis", "-b:a", "192k"])
		else:
			cmd.extend(["-c:a", "copy"])

		cmd.append(out_path)

		log.info(f"FFmpeg cmd: {' '.join(cmd)}")
		log.info(f"Resize: {do_resize}, src={src_w}x{src_h}, target={target_w}x{target_h}")

		wx.CallAfter(self.status.SetLabel, _("Converting..."))
		wx.CallAfter(self.progress.SetValue, 0)
		self.last_announced_progress = -10  # Reset for each new file

		dur_sec = info.get('duration_sec', 0)

		def run():
			# Use threads to read both stdout and stderr to prevent blocking
			self.ffmpeg_process = subprocess.Popen(
				cmd,
				stdout=subprocess.PIPE,
				stderr=subprocess.PIPE,
				creationflags=subprocess.CREATE_NO_WINDOW,
				text=True,
				encoding='utf-8',
				errors='ignore',
				bufsize=1
			)

			stdout_queue = queue.Queue()
			stderr_queue = queue.Queue()

			def reader(pipe, q):
				for line in iter(pipe.readline, ''):
					q.put(line)
				pipe.close()

			t_out = threading.Thread(target=reader, args=(self.ffmpeg_process.stdout, stdout_queue), daemon=True)
			t_err = threading.Thread(target=reader, args=(self.ffmpeg_process.stderr, stderr_queue), daemon=True)
			t_out.start()
			t_err.start()

			error_lines = []
			while self.ffmpeg_process.poll() is None:
				# Process stdout for progress
				while not stdout_queue.empty():
					line = stdout_queue.get_nowait()
					if "out_time_ms=" in line:
						try:
							t_ms = int(line.split('=')[1])
							cur_sec = t_ms / 1_000_000.0
							if dur_sec > 0:
								prog = int((cur_sec / dur_sec) * 100)
								prog = min(100, max(0, prog))
								wx.CallAfter(self.update_progress, prog)
						except Exception as e:
							log.debug(f"Progress parse error: {e}")

				# Process stderr for errors (non-blocking)
				while not stderr_queue.empty():
					err_line = stderr_queue.get_nowait().strip()
					if err_line:
						log.debug(f"FFmpeg stderr: {err_line}")
						error_lines.append(err_line)
						# If fatal error detected, abort early
						if "error" in err_line.lower() or "moov atom" in err_line.lower():
							self.ffmpeg_process.terminate()
							break

				threading.Event().wait(0.1)

			# Collect remaining stderr after process ends
			while not stderr_queue.empty():
				err_line = stderr_queue.get_nowait().strip()
				if err_line:
					error_lines.append(err_line)

			retcode = self.ffmpeg_process.returncode
			if retcode == 0:
				wx.CallAfter(self.on_success, path, out_path)
				wx.CallAfter(self.process_next)
			else:
				error_msg = "\n".join(error_lines) if error_lines else "Unknown error"
				wx.CallAfter(self.on_failure, error_msg, path)
			self.ffmpeg_process = None

		threading.Thread(target=run, daemon=True).start()

	def update_progress(self, val):
		self.progress.SetValue(val)
		self.status.SetLabel(_("{}%").format(val))
		# Check if value is multiple of 10 and not already announced
		if val % 10 == 0 and val > 0 and val != self.last_announced_progress:
			self.last_announced_progress = val
			ui.message(_("Progress: {}%").format(val))
			try:
				tones.beep(800, 50)
			except:
				pass

	def on_success(self, src_path, out_path):
		ui.message(_("Conversion complete: {}").format(os.path.basename(out_path)))

	def on_failure(self, error_msg, file_path):
		ui.message(_("Conversion failed for {}: {}").format(os.path.basename(file_path), error_msg[:150]))
		log.error(f"Conversion failed for {file_path}: {error_msg}")
		self.convert_btn.Enable(True)
		self.cancel_btn.Enable(True)
		self.is_processing = False
		while not self.conversion_queue.empty():
			try:
				self.conversion_queue.get_nowait()
			except:
				break
		self.status.SetLabel(_("Conversion failed"))
		wx.CallAfter(self.EndModal, wx.ID_CANCEL)

	def on_all_done(self):
		self.convert_btn.Enable(True)
		self.cancel_btn.Enable(True)
		self.status.SetLabel(_("All conversions finished"))
		ui.message(_("All conversions finished"))
		try:
			tones.beep(1000, 200)
		except:
			pass
		wx.CallAfter(self.EndModal, wx.ID_OK)

	def on_cancel(self, event):
		if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
			self.ffmpeg_process.terminate()
		while not self.conversion_queue.empty():
			try:
				self.conversion_queue.get_nowait()
			except:
				break
		self.is_processing = False
		self.EndModal(wx.ID_CANCEL)

	def on_close(self, event):
		self.on_cancel(event)

