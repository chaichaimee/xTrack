# mp3Gain.py

import wx
import os
import re
import subprocess
import threading
import queue
import ui
import tones
from gui import guiHelper
from .xTrackCore import get_config_path, get_unique_filename, load_config, save_config
import addonHandler
from logHandler import log

addonHandler.initTranslation()

DEFAULT_TARGET_VOLUME_DB = 89.0
# Classic mp3gain expresses loudness on an "89 dB" reference scale rather
# than dBFS. This offset maps ffmpeg's volumedetect mean-volume (dBFS) onto
# that same reference scale, so a track's reported value and the target
# setting are directly comparable (e.g. both land somewhere around 80-90).
# It is an approximation, not the exact ReplayGain psychoacoustic algorithm.
LOUDNESS_REFERENCE_OFFSET_DB = 103.0


def measure_mean_volume(ffmpeg_path, file_path):
	"""Run ffmpeg's volumedetect filter and return the measured mean volume in dBFS, or None if it could not be determined."""
	if not os.path.exists(ffmpeg_path):
		return None
	nullOutput = "NUL" if os.name == "nt" else "/dev/null"
	cmd = [
		ffmpeg_path,
		"-i", file_path,
		"-af", "volumedetect",
		"-f", "null", nullOutput,
	]
	try:
		result = subprocess.run(
			cmd,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			creationflags=subprocess.CREATE_NO_WINDOW,
			text=True,
			encoding='utf-8',
			errors='ignore',
			timeout=60,
		)
		match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", result.stderr)
		if match:
			return float(match.group(1))
	except (subprocess.TimeoutExpired, OSError) as e:
		log.error(f"MP3 Gain failed to measure volume for {file_path}: {e}")
	return None


def get_audio_bitrate_kbps(ffprobe_path, file_path):
	"""Read the source audio bitrate so the generated output preserves it, falling back to a safe default."""
	if not os.path.exists(ffprobe_path):
		return 192
	cmd = [
		ffprobe_path,
		"-v", "error",
		"-select_streams", "a:0",
		"-show_entries", "stream=bit_rate",
		"-of", "default=noprint_wrappers=1:nokey=1",
		file_path,
	]
	try:
		result = subprocess.run(
			cmd,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			creationflags=subprocess.CREATE_NO_WINDOW,
			text=True,
			encoding='utf-8',
			errors='ignore',
			timeout=10,
		)
		if result.returncode == 0 and result.stdout.strip().isdigit():
			bitrateBps = int(result.stdout.strip())
			if bitrateBps > 0:
				return max(64, round(bitrateBps / 1000))
	except (subprocess.TimeoutExpired, OSError) as e:
		log.error(f"MP3 Gain failed to read bitrate for {file_path}: {e}")
	return 192


class MP3GainDialog(wx.Dialog):
	"""Dialog for analyzing and normalizing MP3 loudness (mp3gain-style) using ffmpeg.

	Analysis and Modify Gain are deliberately independent: Analysis only ever
	reads files and reports their loudness in the list, while Modify Gain is
	the sole action that writes audio. It always writes a new file rather
	than overwriting the original, either next to the source or into a
	configured destination folder, depending on Keep in Same Folder. Album
	actions group files by their containing folder, so importing a parent
	folder with several album subfolders treats each subfolder as its own
	album rather than blending them all into one average.
	"""

	ACTION_TRACK_ANALYSIS = "track_analysis"
	ACTION_ALBUM_ANALYSIS = "album_analysis"
	ACTION_CLEAR_ANALYSIS = "clear_analysis"
	ACTION_APPLY_TRACK = "apply_track_gain"
	ACTION_APPLY_ALBUM = "apply_album_gain"

	def __init__(self, parent, selected_files, libs_path):
		super().__init__(parent, title=_("MP3 Gain"))
		self.libs_path = libs_path
		self.ffmpeg_exe = os.path.join(libs_path, "ffmpeg.exe")
		self.ffprobe_exe = os.path.join(libs_path, "ffprobe.exe")
		self.config_path = get_config_path()
		# Maps a file path to the folder it was imported from (the folder the
		# user selected or browsed to), when known. Used to show each file's
		# album subfolder in the list and to replicate that subfolder under a
		# custom destination folder.
		self.fileImportRoot = {}
		self.selected_files = self._expand_to_mp3_files(selected_files)
		self.analysisResults = {}
		self.processing_queue = queue.Queue()
		self.currently_processing = False
		self.current_action = None
		self.target_volume_db = DEFAULT_TARGET_VOLUME_DB
		self.processed_count = 0
		self.destination_folder = ""
		self.output_keep_same_folder = True
		self.output_destination_folder = ""
		self.init_ui()
		self.load_settings()
		self.SetTitle(_("MP3 Gain: {} files").format(len(self.selected_files)))
		self.Bind(wx.EVT_CLOSE, self.on_close)
		wx.CallAfter(self.file_listbox.SetFocus)

	def _expand_to_mp3_files(self, selected_files):
		"""Expand any selected folders into their contained .mp3 files (recursively, so album subfolders nested inside a main folder are included too) and drop non-mp3 entries, since MP3 Gain only operates on MP3 audio."""
		expandedFiles = []
		for path in selected_files or []:
			if os.path.isdir(path):
				for filePath in self._scan_folder_for_mp3s(path):
					expandedFiles.append(filePath)
					self.fileImportRoot[filePath] = path
			elif path.lower().endswith(".mp3"):
				expandedFiles.append(path)
		return expandedFiles

	def _scan_folder_for_mp3s(self, folder_path):
		"""Walk a folder tree and return every .mp3 file found, including ones nested inside album subfolders."""
		foundFiles = []
		try:
			for root, dirs, files in os.walk(folder_path):
				for name in sorted(files):
					if name.lower().endswith(".mp3"):
						foundFiles.append(os.path.join(root, name))
		except OSError as e:
			log.error(f"MP3 Gain failed to scan folder {folder_path}: {e}")
		return foundFiles

	def init_ui(self):
		main_sizer = wx.BoxSizer(wx.VERTICAL)

		# Title 1 + 2: Browse buttons and file list
		file_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("MP3 Files"))
		browse_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
		self.browse_btn = wx.Button(self, label=_("Browse Files..."))
		self.browse_btn.Bind(wx.EVT_BUTTON, self.on_browse)
		browse_btn_sizer.Add(self.browse_btn, 0, wx.ALL, 5)
		self.browse_folder_btn = wx.Button(self, label=_("Browse Folder..."))
		self.browse_folder_btn.Bind(wx.EVT_BUTTON, self.on_browse_folder)
		browse_btn_sizer.Add(self.browse_folder_btn, 0, wx.ALL, 5)
		file_sizer.Add(browse_btn_sizer, 0, wx.ALL, 0)
		self.file_listbox = wx.ListBox(self, choices=[self.get_file_display_name(f) for f in self.selected_files], style=wx.LB_EXTENDED)
		file_sizer.Add(self.file_listbox, 1, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(file_sizer, 1, wx.EXPAND | wx.ALL, 5)

		# Title 3: target "Normal" volume, on the same dB scale the analysis list reports
		volume_sizer = wx.BoxSizer(wx.HORIZONTAL)
		volume_label = wx.StaticText(self, label=_("\"Normal\" Volume (dB):"))
		volume_sizer.Add(volume_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.target_volume_ctrl = wx.SpinCtrlDouble(self, min=40.0, max=120.0, initial=DEFAULT_TARGET_VOLUME_DB, inc=0.5)
		self.target_volume_ctrl.SetDigits(1)
		volume_sizer.Add(self.target_volume_ctrl, 0, wx.ALL, 5)
		main_sizer.Add(volume_sizer, 0, wx.EXPAND | wx.ALL, 5)

		# Where Apply writes its output. Same folder as the source by default;
		# unchecking reveals a folder picker for a fixed destination instead.
		self.keep_same_folder_checkbox = wx.CheckBox(self, label=_("Keep in Same Folder"))
		self.keep_same_folder_checkbox.SetValue(True)
		self.keep_same_folder_checkbox.Bind(wx.EVT_CHECKBOX, self.on_toggle_keep_same_folder)
		main_sizer.Add(self.keep_same_folder_checkbox, 0, wx.ALL, 5)

		self.dest_folder_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Destination Folder"))
		destFolderBox = self.dest_folder_sizer.GetStaticBox()
		destFolderHelper = guiHelper.BoxSizerHelper(self, sizer=self.dest_folder_sizer)
		destPathHelper = guiHelper.PathSelectionHelper(destFolderBox, _("&Browse..."), _("Select a directory"))
		destPathCtrl = destFolderHelper.addItem(destPathHelper)
		self.dest_folder_ctrl = destPathCtrl.pathControl
		self.dest_folder_ctrl.Bind(wx.EVT_TEXT, self.on_destination_text_changed)
		main_sizer.Add(self.dest_folder_sizer, 0, wx.EXPAND | wx.ALL, 5)

		# Analysis: read-only reporting. Check runs the selected mode and
		# writes each file's loudness (on the target's own dB scale) into the list.
		analysis_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Analysis"))
		self.track_analysis_radio = wx.RadioButton(self, label=_("Track Analysis"), style=wx.RB_GROUP)
		self.album_analysis_radio = wx.RadioButton(self, label=_("Album Analysis"))
		self.clear_analysis_radio = wx.RadioButton(self, label=_("Clear Analysis"))
		analysis_sizer.Add(self.track_analysis_radio, 0, wx.ALL, 5)
		analysis_sizer.Add(self.album_analysis_radio, 0, wx.ALL, 5)
		analysis_sizer.Add(self.clear_analysis_radio, 0, wx.ALL, 5)
		self.check_btn = wx.Button(self, label=_("Check"))
		self.check_btn.Bind(wx.EVT_BUTTON, self.on_check)
		analysis_sizer.Add(self.check_btn, 0, wx.ALL, 5)
		main_sizer.Add(analysis_sizer, 0, wx.EXPAND | wx.ALL, 5)

		# Modify Gain: the only action that writes audio. Apply always
		# creates a new file rather than overwriting the original, writing to
		# the same folder or the configured destination above.
		gain_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Modify Gain"))
		self.apply_track_radio = wx.RadioButton(self, label=_("Apply Track Gain"), style=wx.RB_GROUP)
		self.apply_album_radio = wx.RadioButton(self, label=_("Apply Album Gain"))
		gain_sizer.Add(self.apply_track_radio, 0, wx.ALL, 5)
		gain_sizer.Add(self.apply_album_radio, 0, wx.ALL, 5)
		self.apply_gain_btn = wx.Button(self, label=_("Apply"))
		self.apply_gain_btn.Bind(wx.EVT_BUTTON, self.on_apply_gain)
		gain_sizer.Add(self.apply_gain_btn, 0, wx.ALL, 5)
		main_sizer.Add(gain_sizer, 0, wx.EXPAND | wx.ALL, 5)

		self.progress_bar = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
		main_sizer.Add(self.progress_bar, 0, wx.EXPAND | wx.ALL, 5)
		self.status_label = wx.StaticText(self, label="")
		main_sizer.Add(self.status_label, 0, wx.EXPAND | wx.ALL, 5)

		btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
		self.close_btn = wx.Button(self, wx.ID_CANCEL, label=_("Close"))
		self.close_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
		btn_sizer.Add(self.close_btn, 0, wx.ALL, 5)
		main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

		self.SetSizer(main_sizer)
		self.SetSize((650, 660))
		self.track_analysis_radio.SetValue(True)
		self.apply_track_radio.SetValue(True)

	def get_file_display_name(self, file_path):
		importRoot = self.fileImportRoot.get(file_path)
		displayName = os.path.relpath(file_path, importRoot) if importRoot else os.path.basename(file_path)
		trackDb = self.analysisResults.get(file_path)
		if trackDb is not None:
			return _("{name} ({db:.1f} dB)").format(name=displayName, db=trackDb)
		return displayName

	def refresh_file_list(self):
		self.file_listbox.Set([self.get_file_display_name(f) for f in self.selected_files])

	def on_browse(self, event):
		wildcard = _("MP3 files (*.mp3)|*.mp3")
		with wx.FileDialog(self, _("Select MP3 files"), wildcard=wildcard,
							style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE) as dlg:
			if dlg.ShowModal() == wx.ID_OK:
				for path in dlg.GetPaths():
					if path not in self.selected_files:
						self.selected_files.append(path)
				self.refresh_file_list()
				self.SetTitle(_("MP3 Gain: {} files").format(len(self.selected_files)))
		wx.CallAfter(self.file_listbox.SetFocus)

	def on_browse_folder(self, event):
		with wx.DirDialog(self, _("Select a folder containing MP3 files")) as dlg:
			if dlg.ShowModal() == wx.ID_OK:
				folderPath = dlg.GetPath()
				foundFiles = self._scan_folder_for_mp3s(folderPath)
				addedCount = 0
				for filePath in foundFiles:
					if filePath not in self.selected_files:
						self.selected_files.append(filePath)
						self.fileImportRoot[filePath] = folderPath
						addedCount += 1
				self.refresh_file_list()
				self.SetTitle(_("MP3 Gain: {} files").format(len(self.selected_files)))
				if addedCount == 0:
					ui.message(_("No MP3 files found in the selected folder."))
		wx.CallAfter(self.file_listbox.SetFocus)

	def on_toggle_keep_same_folder(self, event):
		self.update_destination_visibility()
		self.save_settings()

	def on_destination_text_changed(self, event):
		self.destination_folder = self.dest_folder_ctrl.GetValue()
		self.save_settings()
		event.Skip()

	def update_destination_visibility(self):
		showDestination = not self.keep_same_folder_checkbox.GetValue()
		self.dest_folder_sizer.ShowItems(show=showDestination)
		self.Layout()

	def load_settings(self):
		configData = load_config(self.config_path)
		self.keep_same_folder_checkbox.SetValue(configData.get("MP3GainKeepSameFolder", True))
		self.destination_folder = configData.get("MP3GainDestinationFolder", "")
		self.dest_folder_ctrl.SetValue(self.destination_folder)
		self.update_destination_visibility()

	def save_settings(self):
		configData = load_config(self.config_path)
		configData["MP3GainKeepSameFolder"] = self.keep_same_folder_checkbox.GetValue()
		configData["MP3GainDestinationFolder"] = self.destination_folder
		save_config(self.config_path, configData)

	def get_selected_analysis_mode(self):
		if self.track_analysis_radio.GetValue():
			return self.ACTION_TRACK_ANALYSIS
		if self.album_analysis_radio.GetValue():
			return self.ACTION_ALBUM_ANALYSIS
		return self.ACTION_CLEAR_ANALYSIS

	def get_selected_gain_mode(self):
		if self.apply_track_radio.GetValue():
			return self.ACTION_APPLY_TRACK
		return self.ACTION_APPLY_ALBUM

	def on_check(self, event):
		self.start_action(self.get_selected_analysis_mode())

	def on_apply_gain(self, event):
		self.start_action(self.get_selected_gain_mode())

	def start_action(self, action):
		if self.currently_processing:
			ui.message(_("MP3 Gain is already processing files"))
			return
		if not self.selected_files and action != self.ACTION_CLEAR_ANALYSIS:
			ui.message(_("Please select MP3 files first, using the Browse button."))
			return
		if action == self.ACTION_CLEAR_ANALYSIS:
			self.analysisResults.clear()
			self.refresh_file_list()
			ui.message(_("Analysis cleared"))
			return

		self.current_action = action
		self.target_volume_db = self.target_volume_ctrl.GetValue()
		self.output_keep_same_folder = self.keep_same_folder_checkbox.GetValue()
		self.output_destination_folder = self.destination_folder
		self.processed_count = 0

		if action in (self.ACTION_ALBUM_ANALYSIS, self.ACTION_APPLY_ALBUM):
			# Album-level actions need every file's loudness measured together
			# before any per-file gain can be computed, so they run as one
			# background job rather than through the per-file queue below.
			self.set_ui_processing_state(True)
			self.currently_processing = True
			threading.Thread(target=self.run_album_job, args=(action,), daemon=True).start()
			return

		for file_path in self.selected_files:
			self.processing_queue.put(file_path)
		self.currently_processing = True
		self.set_ui_processing_state(True)
		self.process_next_file()

	def set_ui_processing_state(self, isProcessing):
		self.check_btn.Enable(not isProcessing)
		self.apply_gain_btn.Enable(not isProcessing)
		self.browse_btn.Enable(not isProcessing)
		self.browse_folder_btn.Enable(not isProcessing)
		self.progress_bar.SetValue(0)

	def process_next_file(self):
		if self.processing_queue.empty():
			self.currently_processing = False
			wx.CallAfter(self.on_batch_complete)
			return
		file_path = self.processing_queue.get()
		wx.CallAfter(self.status_label.SetLabel, _("Processing: {}").format(os.path.basename(file_path)))
		threading.Thread(target=self.run_single_file_job, args=(file_path, self.current_action), daemon=True).start()

	def run_single_file_job(self, file_path, action):
		try:
			if action == self.ACTION_TRACK_ANALYSIS:
				self.do_track_analysis(file_path)
			elif action == self.ACTION_APPLY_TRACK:
				self.do_apply_gain(file_path, isAlbumMode=False)
		except (OSError, subprocess.SubprocessError) as e:
			log.error(f"MP3 Gain error processing {file_path}: {e}")
			wx.CallAfter(ui.message, _("Failed to process {}").format(os.path.basename(file_path)))
		finally:
			wx.CallAfter(self.advance_after_file)

	def advance_after_file(self):
		self.processed_count += 1
		total = max(len(self.selected_files), 1)
		progress = int((self.processed_count / total) * 100)
		self.progress_bar.SetValue(min(progress, 100))
		self.process_next_file()

	def do_track_analysis(self, file_path):
		measuredDb = measure_mean_volume(self.ffmpeg_exe, file_path)
		if measuredDb is None:
			log.error(f"MP3 Gain could not measure volume for {file_path}")
			wx.CallAfter(ui.message, _("Could not analyze {}").format(os.path.basename(file_path)))
			return
		trackDb = measuredDb + LOUDNESS_REFERENCE_OFFSET_DB
		self.analysisResults[file_path] = trackDb
		wx.CallAfter(self.refresh_file_list)

	def do_apply_gain(self, file_path, isAlbumMode, albumTrackDb=None):
		trackDb = albumTrackDb if isAlbumMode else self.analysisResults.get(file_path)
		if trackDb is None:
			measuredDb = measure_mean_volume(self.ffmpeg_exe, file_path)
			if measuredDb is None:
				wx.CallAfter(ui.message, _("Could not analyze {}").format(os.path.basename(file_path)))
				return
			trackDb = measuredDb + LOUDNESS_REFERENCE_OFFSET_DB
			self.analysisResults[file_path] = trackDb

		gainDb = self.target_volume_db - trackDb
		bitrateKbps = get_audio_bitrate_kbps(self.ffprobe_exe, file_path)

		outputDir = self.get_output_directory(file_path)
		baseName = "{}_gain_{}dB".format(os.path.splitext(os.path.basename(file_path))[0], round(self.target_volume_db))
		outputFileName = get_unique_filename(outputDir, baseName, "mp3")
		outputPath = os.path.join(outputDir, outputFileName)

		cmd = [
			self.ffmpeg_exe,
			"-i", file_path,
			"-af", f"volume={gainDb}dB",
			"-c:a", "libmp3lame",
			"-b:a", f"{bitrateKbps}k",
			"-y",
			outputPath,
		]
		try:
			result = subprocess.run(
				cmd,
				stdout=subprocess.PIPE,
				stderr=subprocess.PIPE,
				creationflags=subprocess.CREATE_NO_WINDOW,
				text=True,
				encoding='utf-8',
				errors='ignore',
				timeout=120,
			)
			if result.returncode != 0 or not os.path.exists(outputPath):
				log.error(f"MP3 Gain ffmpeg failed for {file_path}: {result.stderr}")
				wx.CallAfter(ui.message, _("Failed to apply gain to {}").format(os.path.basename(file_path)))
				if os.path.exists(outputPath):
					os.remove(outputPath)
				return
			wx.CallAfter(ui.message, _("Created {name} at {target:.1f} dB").format(name=os.path.basename(outputPath), target=self.target_volume_db))
		except subprocess.TimeoutExpired:
			log.error(f"MP3 Gain ffmpeg timed out for {file_path}")
			wx.CallAfter(ui.message, _("Timed out applying gain to {}").format(os.path.basename(file_path)))
			if os.path.exists(outputPath):
				os.remove(outputPath)

	def get_output_directory(self, file_path):
		"""Resolve where Apply should write its output, falling back to the source folder if a configured destination is missing or unusable. When the file came from a folder import, its relative album subfolder (e.g. b\\ or c\\) is replicated under a custom destination so albums stay separated there too, instead of being dumped together flat."""
		if self.output_keep_same_folder:
			return os.path.dirname(file_path)
		if self.output_destination_folder:
			targetDir = self.output_destination_folder
			importRoot = self.fileImportRoot.get(file_path)
			if importRoot:
				relSubdir = os.path.relpath(os.path.dirname(file_path), importRoot)
				if relSubdir != ".":
					targetDir = os.path.join(targetDir, relSubdir)
			try:
				os.makedirs(targetDir, exist_ok=True)
				return targetDir
			except OSError as e:
				log.error(f"MP3 Gain failed to create destination folder {targetDir}: {e}")
		return os.path.dirname(file_path)

	def run_album_job(self, action):
		"""Group selected files by their containing folder -- mirroring mp3gain's own semantics, where importing a parent folder full of album subfolders treats each subfolder as its own album -- then measure and apply one uniform gain per group, rather than blending unrelated albums together into a single average."""
		filesByAlbumFolder = {}
		for file_path in self.selected_files:
			albumFolder = os.path.dirname(file_path)
			filesByAlbumFolder.setdefault(albumFolder, []).append(file_path)

		albumCount = len(filesByAlbumFolder)
		processedAlbums = 0
		analyzedAlbumCount = 0

		for albumFolder, albumFiles in filesByAlbumFolder.items():
			measuredVolumes = {}
			for file_path in albumFiles:
				wx.CallAfter(self.status_label.SetLabel, _("Analyzing: {}").format(os.path.basename(file_path)))
				measuredDb = measure_mean_volume(self.ffmpeg_exe, file_path)
				if measuredDb is not None:
					measuredVolumes[file_path] = measuredDb

			if not measuredVolumes:
				log.error(f"MP3 Gain could not analyze any files in album folder {albumFolder}")
				processedAlbums += 1
				continue

			albumMeasuredDb = sum(measuredVolumes.values()) / len(measuredVolumes)
			albumTrackDb = albumMeasuredDb + LOUDNESS_REFERENCE_OFFSET_DB
			for file_path in measuredVolumes:
				self.analysisResults[file_path] = albumTrackDb
			analyzedAlbumCount += 1
			wx.CallAfter(self.refresh_file_list)

			if action == self.ACTION_APPLY_ALBUM:
				for file_path in measuredVolumes:
					self.do_apply_gain(file_path, isAlbumMode=True, albumTrackDb=albumTrackDb)

			processedAlbums += 1
			progressValue = int((processedAlbums / albumCount) * 100)
			wx.CallAfter(self.progress_bar.SetValue, min(progressValue, 100))

		if analyzedAlbumCount == 0:
			wx.CallAfter(ui.message, _("Could not analyze any of the selected files"))
			wx.CallAfter(self.on_batch_complete)
			return

		if action == self.ACTION_ALBUM_ANALYSIS:
			wx.CallAfter(ui.message, _("Album analysis complete for {count} folder(s)").format(count=analyzedAlbumCount))
		wx.CallAfter(self.on_batch_complete)

	def on_batch_complete(self):
		self.set_ui_processing_state(False)
		self.status_label.SetLabel(_("Done"))
		try:
			tones.beep(1000, 200)
		except Exception:
			pass
		ui.message(_("MP3 Gain processing complete"))

	def on_cancel(self, event):
		while not self.processing_queue.empty():
			try:
				self.processing_queue.get_nowait()
			except queue.Empty:
				break
		self.currently_processing = False
		self.EndModal(wx.ID_CANCEL)

	def on_close(self, event):
		self.on_cancel(event)
