# mp3Gain.py

import wx
import os
import re
import subprocess
import threading
import ui
import tones
from gui import guiHelper
from .xTrackCore import get_config_path, get_unique_filename, load_config, save_config
from .ffmpegSupport import limitSpokenText, summarizeFfmpegError, removeQuietly, partPathFor, runFfmpeg
import addonHandler
from logHandler import log

addonHandler.initTranslation()

# MP3 Gain also accepts WAV source files: WAV output is re-encoded as PCM
# rather than forced through libmp3lame, so the format the user selected is
# the format they get back at the new gain.
SUPPORTED_GAIN_EXTENSIONS = (".mp3", ".wav")

DEFAULT_TARGET_VOLUME_DB = 89.0
# Where the gain copies go when the original file names are kept.
GAIN_SUBFOLDER_NAME = "Gain"
# Classic mp3gain expresses loudness on an "89 dB" reference scale rather
# than dBFS. This offset maps ffmpeg's volumedetect mean-volume (dBFS) onto
# that same reference scale, so a track's reported value and the target
# setting are directly comparable (e.g. both land somewhere around 80-90).
# It is an approximation, not the exact ReplayGain psychoacoustic algorithm.
LOUDNESS_REFERENCE_OFFSET_DB = 103.0

MEAN_VOLUME_PATTERN = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


def measure_mean_volume(ffmpeg_path, file_path, registerProcess=None):
	"""Run ffmpeg's volumedetect filter and return the measured mean volume in dBFS, or None if it could not be determined."""
	if not os.path.exists(ffmpeg_path):
		return None
	nullOutput = "NUL" if os.name == "nt" else "/dev/null"
	command = [
		ffmpeg_path, "-hide_banner",
		"-i", file_path,
		"-vn",
		"-af", "volumedetect",
		"-f", "null", nullOutput,
	]
	try:
		_exitCode, stderrLines = runFfmpeg(
			command, None, 0, lambda percent: None, registerProcess or (lambda process: None),
		)
	except OSError as e:
		log.error(f"MP3 Gain failed to measure volume for {file_path}: {e}")
		return None
	match = MEAN_VOLUME_PATTERN.search("\n".join(stderrLines))
	return float(match.group(1)) if match else None


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
	"""Dialog for analyzing and normalizing MP3/WAV loudness (mp3gain-style) using ffmpeg.

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
		self.selected_files = self._expand_to_gain_files(selected_files)
		self.analysisResults = {}
		self.currently_processing = False
		self.is_closed = False
		self.cancel_event = threading.Event()
		self.ffmpeg_process = None
		self.current_action = None
		self.target_volume_db = DEFAULT_TARGET_VOLUME_DB
		self.destination_folder = ""
		self.output_keep_same_folder = True
		self.output_destination_folder = ""
		self.output_keep_names = False
		self.init_ui()
		self.load_settings()
		self.SetTitle(_("MP3 Gain: {} files").format(len(self.selected_files)))
		self.Bind(wx.EVT_CLOSE, self.on_close)
		wx.CallAfter(self.file_listbox.SetFocus)

	def post(self, handler, *args):
		wx.CallAfter(self.run_if_open, handler, *args)

	def run_if_open(self, handler, *args):
		if self.is_closed:
			return
		handler(*args)

	def register_process(self, process):
		self.ffmpeg_process = process

	def _expand_to_gain_files(self, selected_files):
		"""Expand any selected folders into their contained .mp3/.wav files (recursively, so album subfolders nested inside a main folder are included too) and drop unsupported entries, since MP3 Gain only operates on MP3 and WAV audio."""
		expandedFiles = []
		for path in selected_files or []:
			if os.path.isdir(path):
				for filePath in self._scan_folder_for_gain_files(path):
					expandedFiles.append(filePath)
					self.fileImportRoot[filePath] = path
			elif path.lower().endswith(SUPPORTED_GAIN_EXTENSIONS):
				expandedFiles.append(path)
		return expandedFiles

	def _scan_folder_for_gain_files(self, folder_path):
		"""Walk a folder tree and return every .mp3/.wav file found, including ones nested inside album subfolders."""
		foundFiles = []
		try:
			for root, dirs, files in os.walk(folder_path):
				for name in sorted(files):
					if name.lower().endswith(SUPPORTED_GAIN_EXTENSIONS):
						foundFiles.append(os.path.join(root, name))
		except OSError as e:
			log.error(f"MP3 Gain failed to scan folder {folder_path}: {e}")
		return foundFiles

	def init_ui(self):
		main_sizer = wx.BoxSizer(wx.VERTICAL)

		# Title 1 + 2: Browse buttons and file list
		file_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("MP3/WAV Files"))
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

		# Unchecked by default: the result is then named after the source with the target volume added.
		self.keep_names_checkbox = wx.CheckBox(self, label=_("Keep the original &file names (save in a Gain subfolder)"))
		self.keep_names_checkbox.SetValue(False)
		main_sizer.Add(self.keep_names_checkbox, 0, wx.ALL, 5)

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
		wildcard = _("Audio files (*.mp3;*.wav)|*.mp3;*.wav")
		with wx.FileDialog(self, _("Select MP3/WAV files"), wildcard=wildcard,
							style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE) as dlg:
			if dlg.ShowModal() == wx.ID_OK:
				for path in dlg.GetPaths():
					if path not in self.selected_files:
						self.selected_files.append(path)
				self.refresh_file_list()
				self.SetTitle(_("MP3 Gain: {} files").format(len(self.selected_files)))
		wx.CallAfter(self.file_listbox.SetFocus)

	def on_browse_folder(self, event):
		with wx.DirDialog(self, _("Select a folder containing MP3/WAV files")) as dlg:
			if dlg.ShowModal() == wx.ID_OK:
				folderPath = dlg.GetPath()
				foundFiles = self._scan_folder_for_gain_files(folderPath)
				addedCount = 0
				for filePath in foundFiles:
					if filePath not in self.selected_files:
						self.selected_files.append(filePath)
						self.fileImportRoot[filePath] = folderPath
						addedCount += 1
				self.refresh_file_list()
				self.SetTitle(_("MP3 Gain: {} files").format(len(self.selected_files)))
				if addedCount == 0:
					ui.message(_("No MP3 or WAV files found in the selected folder."))
		wx.CallAfter(self.file_listbox.SetFocus)

	def on_toggle_keep_same_folder(self, event):
		self.update_destination_visibility()
		self.save_settings()

	def on_destination_text_changed(self, event):
		# Only remembered here; it is written to disk when the folder is used or the dialog closes.
		self.destination_folder = self.dest_folder_ctrl.GetValue()
		event.Skip()

	def update_destination_visibility(self):
		showDestination = not self.keep_same_folder_checkbox.GetValue()
		self.dest_folder_sizer.ShowItems(show=showDestination)
		self.Layout()

	def load_settings(self):
		configData = load_config(self.config_path)
		self.keep_same_folder_checkbox.SetValue(configData.get("MP3GainKeepSameFolder", True))
		self.keep_names_checkbox.SetValue(bool(configData.get("MP3GainKeepNames", False)))
		self.destination_folder = configData.get("MP3GainDestinationFolder", "")
		self.dest_folder_ctrl.SetValue(self.destination_folder)
		self.update_destination_visibility()

	def save_settings(self):
		configData = load_config(self.config_path)
		configData["MP3GainKeepSameFolder"] = self.keep_same_folder_checkbox.GetValue()
		configData["MP3GainKeepNames"] = self.keep_names_checkbox.GetValue()
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
			ui.message(_("Please select MP3 or WAV files first, using the Browse button."))
			return
		if action == self.ACTION_CLEAR_ANALYSIS:
			self.analysisResults.clear()
			self.refresh_file_list()
			ui.message(_("Analysis cleared"))
			return
		if not os.path.exists(self.ffmpeg_exe):
			ui.message(_("ffmpeg.exe not found"))
			return

		writesAudio = action in (self.ACTION_APPLY_TRACK, self.ACTION_APPLY_ALBUM)
		keepSameFolder = self.keep_same_folder_checkbox.GetValue()
		self.destination_folder = self.dest_folder_ctrl.GetValue().strip()
		# A destination that was asked for is never silently swapped for the source folder.
		if writesAudio and not keepSameFolder and not self.destination_folder:
			ui.message(_("Please choose a destination folder, or turn on Keep in Same Folder."))
			return

		self.current_action = action
		self.target_volume_db = self.target_volume_ctrl.GetValue()
		self.output_keep_same_folder = keepSameFolder
		self.output_keep_names = self.keep_names_checkbox.GetValue()
		self.output_destination_folder = self.destination_folder
		self.save_settings()

		self.cancel_event.clear()
		self.currently_processing = True
		self.set_ui_processing_state(True)
		threading.Thread(target=self.run_action, args=(action,), daemon=True).start()

	def set_ui_processing_state(self, isProcessing):
		self.check_btn.Enable(not isProcessing)
		self.apply_gain_btn.Enable(not isProcessing)
		self.browse_btn.Enable(not isProcessing)
		self.browse_folder_btn.Enable(not isProcessing)
		self.progress_bar.SetValue(0)

	def run_action(self, action):
		okCount = failCount = 0
		try:
			if action in (self.ACTION_ALBUM_ANALYSIS, self.ACTION_APPLY_ALBUM):
				okCount, failCount = self.run_album_job(action)
			else:
				okCount, failCount = self.run_track_job(action)
		except OSError as e:
			log.error(f"MP3 Gain error: {e}")
			failCount += 1
		if not self.cancel_event.is_set():
			self.post(self.on_batch_complete, okCount, failCount)

	def run_track_job(self, action):
		files = list(self.selected_files)
		okCount = failCount = 0
		for index, file_path in enumerate(files):
			if self.cancel_event.is_set():
				break
			self.post(self.status_label.SetLabel, _("Processing: {}").format(os.path.basename(file_path)))
			if action == self.ACTION_TRACK_ANALYSIS:
				succeeded = self.do_track_analysis(file_path)
			else:
				succeeded = self.do_apply_gain(file_path, isAlbumMode=False)
			if succeeded:
				okCount += 1
			else:
				failCount += 1
			self.post(self.progress_bar.SetValue, min(int(((index + 1) / len(files)) * 100), 100))
		return okCount, failCount

	def do_track_analysis(self, file_path):
		measuredDb = measure_mean_volume(self.ffmpeg_exe, file_path, self.register_process)
		if self.cancel_event.is_set():
			return False
		if measuredDb is None:
			log.error(f"MP3 Gain could not measure volume for {file_path}")
			self.post(ui.message, limitSpokenText(_("Could not analyze {}").format(os.path.basename(file_path))))
			return False
		self.analysisResults[file_path] = measuredDb + LOUDNESS_REFERENCE_OFFSET_DB
		self.post(self.refresh_file_list)
		return True

	def do_apply_gain(self, file_path, isAlbumMode, albumTrackDb=None):
		trackDb = albumTrackDb if isAlbumMode else self.analysisResults.get(file_path)
		if trackDb is None:
			measuredDb = measure_mean_volume(self.ffmpeg_exe, file_path, self.register_process)
			if self.cancel_event.is_set():
				return False
			if measuredDb is None:
				self.post(ui.message, limitSpokenText(_("Could not analyze {}").format(os.path.basename(file_path))))
				return False
			trackDb = measuredDb + LOUDNESS_REFERENCE_OFFSET_DB
			self.analysisResults[file_path] = trackDb

		gainDb = self.target_volume_db - trackDb
		# Preserve the source format on output: an MP3 in stays MP3 (at its
		# detected source bitrate), a WAV in stays WAV (PCM), rather than
		# forcing every result through the MP3 encoder.
		sourceExtension = os.path.splitext(file_path)[1].lower().lstrip(".")
		if sourceExtension == "wav":
			codecArgs = ["-c:a", "pcm_s16le"]
		else:
			bitrateKbps = get_audio_bitrate_kbps(self.ffprobe_exe, file_path)
			codecArgs = ["-c:a", "libmp3lame", "-b:a", f"{bitrateKbps}k"]

		# A positive gain can push peaks past 0 dBFS, which would clip audibly,
		# so a limiter is chained on after the volume change.
		audioFilter = f"volume={gainDb:.2f}dB"
		if gainDb > 0:
			audioFilter += ",alimiter=level_in=1:level_out=1:limit=0.99"

		outputDir = self.get_output_directory(file_path)
		if outputDir is not None and self.output_keep_names:
			outputDir = self.get_named_output_directory(file_path, outputDir)
		if outputDir is None:
			self.post(ui.message, limitSpokenText(_("Could not create the destination folder for {}").format(os.path.basename(file_path))))
			return False
		sourceBaseName = os.path.splitext(os.path.basename(file_path))[0]
		baseName = sourceBaseName if self.output_keep_names else "{}_gain_{}dB".format(sourceBaseName, round(self.target_volume_db))
		outputPath = os.path.join(outputDir, get_unique_filename(outputDir, baseName, sourceExtension))
		partPath = partPathFor(outputPath)

		command = [self.ffmpeg_exe, "-hide_banner", "-i", file_path, "-af", audioFilter] + codecArgs + ["-y", partPath]
		try:
			exitCode, stderrLines = runFfmpeg(command, outputDir, 0, lambda percent: None, self.register_process)
		except OSError as e:
			log.error(f"MP3 Gain could not run ffmpeg for {file_path}: {e}")
			removeQuietly(partPath)
			self.post(ui.message, limitSpokenText(_("Failed to apply gain to {}").format(os.path.basename(file_path))))
			return False

		if self.cancel_event.is_set():
			removeQuietly(partPath)
			return False
		if exitCode != 0 or not os.path.exists(partPath):
			log.error(f"MP3 Gain ffmpeg failed for {file_path}: " + " | ".join(line.strip() for line in stderrLines))
			removeQuietly(partPath)
			self.post(ui.message, limitSpokenText(
				_("Failed to apply gain to {name}: {reason}").format(
					name=os.path.basename(file_path), reason=summarizeFfmpegError(stderrLines))
			))
			return False
		try:
			os.replace(partPath, outputPath)
		except OSError as e:
			log.error(f"MP3 Gain could not move {partPath} to {outputPath}: {e}")
			removeQuietly(partPath)
			self.post(ui.message, limitSpokenText(_("Failed to apply gain to {}").format(os.path.basename(file_path))))
			return False
		self.post(ui.message, limitSpokenText(
			_("Created {name} at {target:.1f} dB").format(name=os.path.basename(outputPath), target=self.target_volume_db)
		))
		return True

	def get_named_output_directory(self, file_path, outputDir):
		"""The folder for a copy that keeps the source name: a Gain subfolder whenever the output folder is the source folder, so the copy never lands next to the original under the same name. None when that subfolder cannot be created."""
		sourceDir = os.path.dirname(file_path)
		if os.path.normcase(os.path.abspath(outputDir)) != os.path.normcase(os.path.abspath(sourceDir)):
			return outputDir
		gainDir = os.path.join(outputDir, GAIN_SUBFOLDER_NAME)
		try:
			os.makedirs(gainDir, exist_ok=True)
		except OSError as e:
			log.error(f"MP3 Gain failed to create {gainDir}: {e}")
			return None
		return gainDir

	def get_output_directory(self, file_path):
		"""Resolve where Apply should write its output, or None if a requested destination cannot be created. When the file came from a folder import, its relative album subfolder (e.g. b\\ or c\\) is replicated under a custom destination so albums stay separated there too, instead of being dumped together flat."""
		if self.output_keep_same_folder:
			return os.path.dirname(file_path)
		targetDir = self.output_destination_folder
		importRoot = self.fileImportRoot.get(file_path)
		if importRoot:
			relSubdir = os.path.relpath(os.path.dirname(file_path), importRoot)
			if relSubdir != ".":
				targetDir = os.path.join(targetDir, relSubdir)
		try:
			os.makedirs(targetDir, exist_ok=True)
		except OSError as e:
			log.error(f"MP3 Gain failed to create destination folder {targetDir}: {e}")
			return None
		return targetDir

	def run_album_job(self, action):
		"""Group selected files by their containing folder -- mirroring mp3gain's own semantics, where importing a parent folder full of album subfolders treats each subfolder as its own album -- then measure and apply one uniform gain per group, rather than blending unrelated albums together into a single average."""
		filesByAlbumFolder = {}
		for file_path in list(self.selected_files):
			filesByAlbumFolder.setdefault(os.path.dirname(file_path), []).append(file_path)

		albumCount = len(filesByAlbumFolder)
		processedAlbums = 0
		analyzedAlbumCount = 0
		okCount = failCount = 0

		for albumFolder, albumFiles in filesByAlbumFolder.items():
			if self.cancel_event.is_set():
				break
			measuredVolumes = {}
			for file_path in albumFiles:
				if self.cancel_event.is_set():
					break
				self.post(self.status_label.SetLabel, _("Analyzing: {}").format(os.path.basename(file_path)))
				measuredDb = measure_mean_volume(self.ffmpeg_exe, file_path, self.register_process)
				if measuredDb is not None:
					measuredVolumes[file_path] = measuredDb
				else:
					failCount += 1
			if self.cancel_event.is_set():
				break

			processedAlbums += 1
			if not measuredVolumes:
				log.error(f"MP3 Gain could not analyze any files in album folder {albumFolder}")
				continue

			albumMeasuredDb = sum(measuredVolumes.values()) / len(measuredVolumes)
			albumTrackDb = albumMeasuredDb + LOUDNESS_REFERENCE_OFFSET_DB
			for file_path in measuredVolumes:
				self.analysisResults[file_path] = albumTrackDb
			analyzedAlbumCount += 1
			self.post(self.refresh_file_list)

			if action == self.ACTION_APPLY_ALBUM:
				for file_path in measuredVolumes:
					if self.cancel_event.is_set():
						break
					if self.do_apply_gain(file_path, isAlbumMode=True, albumTrackDb=albumTrackDb):
						okCount += 1
					else:
						failCount += 1
			else:
				okCount += len(measuredVolumes)
			self.post(self.progress_bar.SetValue, min(int((processedAlbums / albumCount) * 100), 100))

		if analyzedAlbumCount and action == self.ACTION_ALBUM_ANALYSIS:
			self.post(ui.message, _("Album analysis complete for {count} folder(s)").format(count=analyzedAlbumCount))
		return okCount, failCount

	def on_batch_complete(self, okCount, failCount):
		self.currently_processing = False
		self.set_ui_processing_state(False)
		if okCount == 0 and failCount > 0:
			self.status_label.SetLabel(_("Done"))
			tones.beep(300, 300)
			ui.message(_("Could not process any of the selected files"))
			return
		self.status_label.SetLabel(_("Done"))
		tones.beep(1000, 200)
		if failCount:
			ui.message(_("MP3 Gain finished: {ok} done, {failed} failed").format(ok=okCount, failed=failCount))
		else:
			ui.message(_("MP3 Gain processing complete"))

	def stop_processing(self):
		self.is_closed = True
		self.cancel_event.set()
		process = self.ffmpeg_process
		if process and process.poll() is None:
			try:
				process.terminate()
			except OSError as e:
				log.debugWarning(f"MP3 Gain could not stop ffmpeg: {e}")

	def on_cancel(self, event):
		self.stop_processing()
		self.save_settings()
		self.EndModal(wx.ID_CANCEL)

	def on_close(self, event):
		self.on_cancel(event)
