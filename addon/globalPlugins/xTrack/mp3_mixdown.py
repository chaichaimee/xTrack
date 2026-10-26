# mp3_mixdown.py

import wx
import os
import re
import shutil
import subprocess
import threading
import tempfile
import ui
import tones
from gui import guiHelper
from .xTrackCore import (
	get_config_path, get_unique_filename, get_file_duration, get_addon_data_dir, load_config, save_config,
)
from .ffmpegSupport import limitSpokenText, summarizeFfmpegError, removeQuietly, partPathFor, runFfmpeg
from .floatingMenu import chooseMenuAction
from .mixdownEngine import (
	PARAMS, DEFAULT_SETTINGS, PAN_DIRECTION_KEYS, AUTOPAN_DIRECTION_KEYS, MAX_SECTIONS, REVERSE_MAX_SECONDS,
	MIN_FAST_SPEED_FACTOR, MAX_FAST_SPEED_FACTOR, MixdownPlan, sanitize_settings, normalize_preset_name,
	build_custom_plan, find_effect_conflicts, find_unavailable_effects, parse_filter_list,
	parse_loudnorm_measurement, parse_integrated_loudness, two_pass_loudnorm,
)
import addonHandler
from logHandler import log

addonHandler.initTranslation()

AUDIO_EXTENSIONS = (".mp3", ".wav")
USER_PRESETS_FILE_NAME = "mixdown_presets.json"
MIN_PREVIEW_SECONDS = 5
MAX_PREVIEW_SECONDS = 120

# Fixed presets exactly as specified: each is a complete, self-contained
# filter chain that bypasses the custom effect controls entirely when chosen.
# shortTag names the output file (e.g. "..._bassboost.mp3") instead of a
# generic "_mixdown" suffix that would collide across different presets.
MIXDOWN_PRESETS = [
	{
		"name": _("Flat / Default"),
		"description": _("Clean audio export with no added filters."),
		"shortTag": "flat",
		"filterChain": "",
	},
	{
		"name": _("Bass Boosted Warmth"),
		"description": _("Enhanced low frequencies with controlled peaks."),
		"shortTag": "bassboost",
		"filterChain": "bass=g=10:f=100:w=0.5,treble=g=2:f=3000:w=0.5,acompressor=threshold=-15dB:ratio=2:attack=10:release=100,alimiter=limit=-1dB",
	},
	{
		"name": _("Vocal Clarity & Denoise (ANR)"),
		"description": _("Removes background hiss and boosts vocal presence."),
		"shortTag": "vocal",
		"filterChain": "afftdn=nf=-30,treble=g=5:f=2500:w=0.5,acompressor=threshold=-18dB:ratio=3",
	},
	{
		"name": _("Cinematic Binaural Space"),
		"description": _("Immersive 3D headphone experience with real stereo width and warm reverb."),
		"shortTag": "binaural",
		"filterChain": "crossfeed=strength=0.3:range=0.9,extrastereo=m=2.5:c=disabled,aecho=0.8:0.88:60|150:0.4|0.2,alimiter=limit=-1dB",
	},
	{
		"name": _("Dreamy Chorus & Wide"),
		"description": _("Thickens sound with chorus, modulation, and extra stereo width."),
		"shortTag": "chorus",
		"filterChain": "chorus=0.5:0.9:50|60|40:0.4|0.3|0.2:0.25|0.4|0.3:2|2.3|1.3,extrastereo=m=1.5:c=disabled,alimiter=limit=-1dB",
	},
	{
		"name": _("Reversed & Accelerated"),
		"description": _("Plays audio backward at an increased tempo."),
		"shortTag": "reversed",
		"filterChain": "areverse,atempo=1.25",
	},
	{
		"name": _("Muffled Room (Pub / Bathroom)"),
		"description": _("Dull, muffled ambience as if heard through a wall or in a tiled room."),
		"shortTag": "muffled",
		"filterChain": "lowpass=f=1200,bass=g=4:f=150:w=0.6,aecho=0.6:0.7:40|60:0.5|0.3",
	},
	{
		"name": _("Broadcast Standard (EBU R128)"),
		"description": _("Normalizes loudness to the international streaming/broadcast standard (-16 LUFS, -1.5 dBTP true peak) so exports sound consistent on YouTube and podcast platforms."),
		"shortTag": "broadcast",
		"filterChain": "loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=-1.5dB",
	},
	{
		"name": _("Podcast Clarity"),
		"description": _("Cuts rumble below 80 Hz, lifts vocal presence around 2-4 kHz, gates out background noise between words, and applies a dynamic limiter for clear, consistent speech."),
		"shortTag": "podcast",
		"filterChain": "highpass=f=80,firequalizer=gain_entry='entry(2000\\,3);entry(3000\\,4);entry(4000\\,3)',agate=threshold=-35dB:ratio=2:attack=20:release=250,acompressor=threshold=-18dB:ratio=3:attack=10:release=150,alimiter=limit=-1dB",
	},
	{
		"name": _("Music Master"),
		"description": _("Widens the stereo image, applies gentle multi-stage compression, and adds a touch of reverb for a fuller, more polished vocal and music mix."),
		"shortTag": "musicmaster",
		"filterChain": "extrastereo=m=1.3:c=disabled,acompressor=threshold=-18dB:ratio=2.5:attack=15:release=150,aecho=0.7:0.7:40:0.15,alimiter=limit=-1dB",
	},
	{
		"name": _("Bass & Treble Boost"),
		"description": _("Adds punchy low-end weight and bright, clear highs."),
		"shortTag": "basstreble",
		"filterChain": "bass=g=8:f=100:w=0.6,treble=g=6:f=8000:w=0.6,alimiter=limit=-1dB",
	},
]

# Genre EQ curves, tuned from commonly-used real-world mastering conventions
# for each style. Each band uses ffmpeg's parametric `equalizer` filter
# (center frequency, Q-based width, gain), which is more precise than the
# simple bass/treble shelves used elsewhere in this module.
GENRE_EQ_PRESETS = [
	{
		"name": _("Pop"),
		"shortTag": "pop",
		"filterChain": "equalizer=f=100:width_type=o:width=1:g=4,equalizer=f=3000:width_type=q:width=1.2:g=5,equalizer=f=12000:width_type=o:width=1:g=4,acompressor=threshold=-16dB:ratio=2.5:attack=8:release=80,alimiter=limit=-1dB",
	},
	{
		"name": _("Rock"),
		"shortTag": "rock",
		"filterChain": "equalizer=f=70:width_type=o:width=1:g=5,equalizer=f=400:width_type=q:width=1.5:g=-6,equalizer=f=2500:width_type=q:width=1.3:g=5,equalizer=f=6000:width_type=o:width=1:g=3,acompressor=threshold=-16dB:ratio=4:attack=5:release=60,alimiter=limit=-1dB",
	},
	{
		"name": _("Dance / EDM"),
		"shortTag": "dance",
		"filterChain": "equalizer=f=50:width_type=q:width=1:g=8,equalizer=f=300:width_type=q:width=1.5:g=-5,equalizer=f=10000:width_type=o:width=1:g=6,acompressor=threshold=-12dB:ratio=6:attack=3:release=40,alimiter=limit=-0.5dB",
	},
	{
		"name": _("Hip-Hop"),
		"shortTag": "hiphop",
		"filterChain": "equalizer=f=45:width_type=q:width=0.8:g=9,equalizer=f=200:width_type=q:width=1.5:g=-4,equalizer=f=4000:width_type=o:width=1:g=3,acompressor=threshold=-14dB:ratio=5:attack=4:release=50,alimiter=limit=-0.5dB",
	},
	{
		# No compressor here: jazz reads as clearly different from the
		# louder genres precisely because its dynamics are left open.
		"name": _("Jazz"),
		"shortTag": "jazz",
		"filterChain": "equalizer=f=250:width_type=o:width=1:g=3,equalizer=f=1200:width_type=q:width=1:g=1.5,equalizer=f=9000:width_type=o:width=1:g=-4",
	},
	{
		# Boosted midrange (not scooped, unlike rock/metal) is what makes
		# this read as "acoustic" rather than "produced/electric".
		"name": _("Folk / Acoustic"),
		"shortTag": "folk",
		"filterChain": "equalizer=f=150:width_type=o:width=1:g=2,equalizer=f=1000:width_type=q:width=1:g=3,equalizer=f=6000:width_type=o:width=1:g=4",
	},
	{
		# Deliberately close to untouched -- classical is meant to be the
		# most transparent, widest-dynamic-range option, distinct precisely
		# because it does the least.
		"name": _("Classical"),
		"shortTag": "classical",
		"filterChain": "equalizer=f=60:width_type=o:width=1:g=1,equalizer=f=12000:width_type=o:width=1:g=1",
	},
	{
		"name": _("R&B / Soul"),
		"shortTag": "rnb",
		"filterChain": "equalizer=f=80:width_type=o:width=1:g=5,equalizer=f=600:width_type=q:width=1:g=2,equalizer=f=3500:width_type=q:width=1.2:g=3,equalizer=f=9000:width_type=o:width=1:g=1,acompressor=threshold=-16dB:ratio=3:attack=15:release=150,alimiter=limit=-1dB",
	},
	{
		"name": _("Metal"),
		"shortTag": "metal",
		"filterChain": "equalizer=f=90:width_type=q:width=1:g=3,equalizer=f=500:width_type=q:width=1.8:g=-8,equalizer=f=3000:width_type=q:width=1.3:g=4,equalizer=f=7000:width_type=o:width=1:g=6,acompressor=threshold=-12dB:ratio=6:attack=2:release=30,alimiter=limit=-0.3dB",
	},
]

# Filter names this ffmpeg build offers, read once per NVDA session.
FILTER_CACHE = {"names": None}
FILTER_LIST_TIMEOUT_SECONDS = 20

# Spoken names of the choices of each option, in the same order as the
# option's choices in mixdownEngine.PARAMS.
CHOICE_LABELS = {
	"clickRemoverMode": [_("Clicks and crackle"), _("Clipping (distorted peaks)"), _("Both")],
	"silenceTrimMode": [_("Start of the file only"), _("Start and silent gaps")],
	"nightcoreMode": [_("Nightcore (faster and higher)"), _("Slowed (slower and lower)")],
	"channelTool": [_("Stereo to Mono"), _("Mono to Stereo (same sound in both ears)"), _("Swap Left and Right")],
	"modulationType": [_("Flanger"), _("Phaser"), _("Tremolo"), _("Vibrato")],
	"haasDelayedSide": [_("Right"), _("Left")],
	"roomType": [_("Small Room"), _("Medium Room"), _("Hall"), _("Cathedral")],
	"autoPanDirection": [_("Left to Right"), _("Right to Left")],
	"expanderPreset": [_("Night Mode (quiet parts up, loud parts down)"), _("Gentle (lower very quiet noise)"), _("Strong (close to a gate)")],
	"softClipType": [
		_("Tanh (smooth)"), _("Arctangent (smooth)"), _("Cubic"), _("Exponential"),
		_("Algebraic"), _("Quintic"), _("Sine"), _("Error function"),
	],
	"telephoneStyle": [_("Telephone"), _("Radio"), _("Walkie-talkie")],
}

CONFLICT_NAMES = {
	"binaural": _("Binaural"),
	"haas": _("Haas Effect"),
	"vocalCenter": _("Keep voice in the center"),
}


def describe_conflict(code, details):
	"""One spoken sentence for a finding of mixdownEngine.find_effect_conflicts."""
	names = ", ".join(CONFLICT_NAMES.get(name, name) for name in details.get("names", []))
	if code == "stackedNormalizers":
		return _("Several loudness normalizers are on together ({names}); they work against each other.").format(names=names)
	if code == "gainWithLoudnorm":
		return _("Volume Gain is on together with Loudness Normalization, which will undo the gain.")
	if code == "gateAndExpander":
		return _("Noise Gate and Expander both lower quiet sounds, so they may cut them twice.")
	if code == "doubleReverb":
		return _("Reverb / Echo and Multi-Room Reverb are both on and will pile up.")
	if code == "autoPanAndSections":
		return _("Auto-Pan Loop and Advanced Time-Based Panning are both on; their movements will fight each other.")
	if code == "stackedSpeed":
		return _("More than one speed effect is on; together they change the speed {factor:.2f} times.").format(factor=details.get("factor", 1.0))
	if code == "timelineShifted":
		return _("Panning sections are counted on the timeline after speed, rewind or silence trimming, not on the original file.")
	if code == "monoWithStereoEffects":
		return _("Stereo to Mono is on together with stereo-only effects ({names}), which will then have little or no effect.").format(names=names)
	if code == "mixAtZero":
		return _("Effects Mix is 0 percent, so Chorus, Modulation, Haas, Reverb and Room effects will not be heard.")
	return code


def get_audio_bitrate_kbps(ffprobe_path, file_path):
	"""Read the source audio bitrate so an MP3 re-encode preserves it, falling back to a safe default."""
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
		log.error(f"Mixdown failed to read bitrate for {file_path}: {e}")
	return 192


class MP3WavMixdownDialog(wx.Dialog):
	"""Dialog for chaining multiple FFmpeg audio effects (noise reduction, tempo/reversal, EQ, spatial/panning, dynamics) onto MP3 or WAV files, either through named presets or custom per-effect controls, with an async 15-second preview."""

	PREVIEW_SECONDS = 15

	def __init__(self, parent, selected_files, libs_path):
		super().__init__(parent, title=_("Mixdown"))
		self.libs_path = libs_path
		self.ffmpeg_exe = os.path.join(libs_path, "ffmpeg.exe")
		self.ffprobe_exe = os.path.join(libs_path, "ffprobe.exe")
		self.config_path = get_config_path()
		self.user_presets_path = os.path.join(get_addon_data_dir(), USER_PRESETS_FILE_NAME)
		self.fileImportRoot = {}
		self.selected_files = self._expand_to_audio_files(selected_files)
		self.currently_processing = False
		self.is_closed = False
		self.cancel_event = threading.Event()
		self.ffmpeg_process = None
		self.preview_dir = ""
		self.customEffectControls = []
		self.effect_pages = []
		self.setting_controls = {}
		self.effect_widgets = {}
		self.effect_labels = {}
		self.toggle_refreshers = []
		self.pan_section_widgets = []
		self.autopan_section_widgets = []
		self.blocked_widgets = set()
		self.unsupported_effects = {}
		self.user_presets = {}
		self.user_last_settings = None
		self.load_user_store()
		self.init_ui()
		if self.user_last_settings is not None:
			self.set_effect_settings(self.user_last_settings)
		self.SetTitle(_("Mixdown: {} files").format(len(self.selected_files)))
		self.Bind(wx.EVT_CLOSE, self.on_close)
		wx.CallAfter(self.file_listbox.SetFocus)
		self.start_filter_detection()

	# ---- which filters this ffmpeg can run

	def start_filter_detection(self):
		cachedNames = FILTER_CACHE["names"]
		if cachedNames:
			self.apply_filter_availability(cachedNames)
			return
		threading.Thread(target=self.run_filter_detection, daemon=True).start()

	def run_filter_detection(self):
		names = set()
		if os.path.exists(self.ffmpeg_exe):
			try:
				result = subprocess.run(
					[self.ffmpeg_exe, "-hide_banner", "-filters"],
					stdin=subprocess.DEVNULL,
					stdout=subprocess.PIPE,
					stderr=subprocess.PIPE,
					creationflags=subprocess.CREATE_NO_WINDOW,
					text=True,
					encoding="utf-8",
					errors="ignore",
					timeout=FILTER_LIST_TIMEOUT_SECONDS,
				)
				if result.returncode == 0:
					names = parse_filter_list(result.stdout)
			except (OSError, subprocess.SubprocessError) as error:
				log.error(f"Mixdown could not read the ffmpeg filter list: {error}")
		if names:
			FILTER_CACHE["names"] = names
		self.post(self.apply_filter_availability, names)

	def apply_filter_availability(self, names):
		"""Turn off, and say so, every effect whose filters this ffmpeg build does not have. An empty list means detection failed, in which case nothing is turned off."""
		unavailable = find_unavailable_effects(names)
		if not unavailable:
			return
		self.unsupported_effects = unavailable
		spokenNames = []
		for key in unavailable:
			widgets = self.effect_widgets.get(key)
			if not widgets:
				continue
			label = self.effect_labels.get(key, key)
			spokenNames.append(label)
			for widget in widgets:
				self.blocked_widgets.add(widget)
			entry = self.setting_controls.get(key)
			if entry:
				entry["set"](False)
			checkbox = widgets[0]
			if isinstance(checkbox, wx.CheckBox):
				checkbox.SetLabel(label + _(" (not available in this ffmpeg)"))
		self.refresh_all_toggles()
		if not self.currently_processing:
			self.set_custom_controls_enabled(self.preset_combo.GetSelection() == 0)
		log.info("Mixdown: unavailable in this ffmpeg: " + ", ".join(f"{key} (needs {'/'.join(missing)})" for key, missing in unavailable.items()))
		if spokenNames:
			ui.message(limitSpokenText(_("{count} effects are not available in this ffmpeg and were turned off: {names}").format(
				count=len(spokenNames), names=", ".join(spokenNames))))

	def post(self, handler, *args):
		wx.CallAfter(self.run_if_open, handler, *args)

	def run_if_open(self, handler, *args):
		if self.is_closed:
			return
		handler(*args)

	def register_process(self, process):
		self.ffmpeg_process = process

	def _expand_to_audio_files(self, selected_files):
		"""Expand any selected folders into their contained .mp3/.wav files (recursively, so album subfolders nested inside a main folder are included too) and drop anything else."""
		expandedFiles = []
		for path in selected_files or []:
			if os.path.isdir(path):
				for filePath in self._scan_folder_for_audio_files(path):
					expandedFiles.append(filePath)
					self.fileImportRoot[filePath] = path
			elif path.lower().endswith(AUDIO_EXTENSIONS):
				expandedFiles.append(path)
		return expandedFiles

	def _scan_folder_for_audio_files(self, folder_path):
		"""Walk a folder tree and return every .mp3/.wav file found, including ones nested inside album subfolders."""
		foundFiles = []
		try:
			for root, dirs, files in os.walk(folder_path):
				for name in sorted(files):
					if name.lower().endswith(AUDIO_EXTENSIONS):
						foundFiles.append(os.path.join(root, name))
		except OSError as e:
			log.error(f"Mixdown failed to scan folder {folder_path}: {e}")
		return foundFiles

	def make_stepped_spin(self, parent, minVal, maxVal, initial, increment=5):
		"""SpinCtrlDouble displayed as a whole number but stepping by `increment` per arrow press, since wx.SpinCtrl has no configurable step."""
		ctrl = wx.SpinCtrlDouble(parent, min=minVal, max=maxVal, initial=initial, inc=increment)
		ctrl.SetDigits(0)
		return ctrl

	def make_effect_page(self, title):
		page = wx.ScrolledWindow(self.effects_notebook)
		page.SetScrollRate(0, 12)
		pageSizer = wx.BoxSizer(wx.VERTICAL)
		page.SetSizer(pageSizer)
		self.effects_notebook.AddPage(page, title)
		self.effect_pages.append(page)
		return page, pageSizer

	def relayout(self):
		"""Re-flow the dialog after something was shown, hidden or added, and refresh each tab's scroll size."""
		for page in self.effect_pages:
			page.Layout()
			page.FitInside()
		self.Layout()

	def get_file_display_name(self, file_path):
		importRoot = self.fileImportRoot.get(file_path)
		return os.path.relpath(file_path, importRoot) if importRoot else os.path.basename(file_path)

	def refresh_file_list(self):
		self.file_listbox.Set([self.get_file_display_name(f) for f in self.selected_files])

	def on_browse(self, event):
		wildcard = _("MP3/WAV files (*.mp3;*.wav)|*.mp3;*.wav")
		with wx.FileDialog(self, _("Select MP3 or WAV files"), wildcard=wildcard,
							style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE) as dlg:
			if dlg.ShowModal() == wx.ID_OK:
				for path in dlg.GetPaths():
					if path not in self.selected_files:
						self.selected_files.append(path)
				self.refresh_file_list()
				self.SetTitle(_("Mixdown: {} files").format(len(self.selected_files)))
		wx.CallAfter(self.file_listbox.SetFocus)

	def on_browse_folder(self, event):
		with wx.DirDialog(self, _("Select a folder containing MP3 or WAV files")) as dlg:
			if dlg.ShowModal() == wx.ID_OK:
				folderPath = dlg.GetPath()
				foundFiles = self._scan_folder_for_audio_files(folderPath)
				addedCount = 0
				for filePath in foundFiles:
					if filePath not in self.selected_files:
						self.selected_files.append(filePath)
						self.fileImportRoot[filePath] = folderPath
						addedCount += 1
				self.refresh_file_list()
				self.SetTitle(_("Mixdown: {} files").format(len(self.selected_files)))
				if addedCount == 0:
					ui.message(_("No MP3 or WAV files found in the selected folder."))
		wx.CallAfter(self.file_listbox.SetFocus)

	def on_file_list_key_down(self, event):
		if event.GetKeyCode() in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
			self.remove_selected_files()
		else:
			event.Skip()

	def on_file_list_context_menu(self, event):
		if not self.file_listbox.GetSelections():
			return
		chooseMenuAction(self, _("Files"), [(_("Delete"), self.remove_selected_files)])

	def remove_selected_files(self):
		"""Remove the selected entries from the import list only -- this never touches anything on disk, whether the entry came from an individual file pick or was expanded out of a folder import."""
		if self.currently_processing:
			ui.message(_("Mixdown is already processing files"))
			return
		selectedIndices = list(self.file_listbox.GetSelections())
		if not selectedIndices:
			return
		removedCount = 0
		for index in sorted(selectedIndices, reverse=True):
			if 0 <= index < len(self.selected_files):
				filePath = self.selected_files.pop(index)
				self.fileImportRoot.pop(filePath, None)
				removedCount += 1
		self.refresh_file_list()
		self.SetTitle(_("Mixdown: {} files").format(len(self.selected_files)))
		if removedCount:
			ui.message(_("Removed {} file(s) from the list").format(removedCount))
		wx.CallAfter(self.file_listbox.SetFocus)

	def _parse_float_field(self, textCtrl, fallbackValue):
		rawText = textCtrl.GetValue().strip()
		if not rawText:
			return fallbackValue
		try:
			return float(rawText)
		except ValueError:
			log.error(f"Mixdown could not parse '{rawText}' as a number, using {fallbackValue}")
			return fallbackValue

	def get_preview_source_file(self):
		selectedIndices = self.file_listbox.GetSelections()
		if selectedIndices and selectedIndices[0] < len(self.selected_files):
			return self.selected_files[selectedIndices[0]]
		return self.selected_files[0]

	def cleanup_preview_dir(self):
		# A media player may still hold the last preview open; anything that cannot
		# be removed now stays in the system temp folder, not in the user's music folder.
		if self.preview_dir:
			shutil.rmtree(self.preview_dir, ignore_errors=True)
		self.preview_dir = ""

	def on_batch_complete(self, okCount, failCount):
		self.currently_processing = False
		self.set_ui_processing_state(False)
		self.status_label.SetLabel(_("Done"))
		if okCount == 0 and failCount > 0:
			tones.beep(300, 300)
			ui.message(_("Could not process any of the selected files"))
			return
		tones.beep(1000, 200)
		if failCount:
			ui.message(_("Mixdown finished: {ok} done, {failed} failed").format(ok=okCount, failed=failCount))
		else:
			ui.message(_("Mixdown processing complete"))

	def stop_processing(self):
		self.is_closed = True
		self.cancel_event.set()
		process = self.ffmpeg_process
		if process and process.poll() is None:
			try:
				process.terminate()
			except OSError as e:
				log.debugWarning(f"Mixdown could not stop ffmpeg: {e}")

	def on_close(self, event):
		self.on_cancel(event)

	def init_ui(self):
		main_sizer = wx.BoxSizer(wx.VERTICAL)

		# MP3/WAV files: Browse buttons and file list
		file_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Audio Files"))
		fileBox = file_sizer.GetStaticBox()
		browse_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
		self.browse_btn = wx.Button(fileBox, label=_("Browse Files..."))
		self.browse_btn.Bind(wx.EVT_BUTTON, self.on_browse)
		browse_btn_sizer.Add(self.browse_btn, 0, wx.ALL, 5)
		self.browse_folder_btn = wx.Button(fileBox, label=_("Browse Folder..."))
		self.browse_folder_btn.Bind(wx.EVT_BUTTON, self.on_browse_folder)
		browse_btn_sizer.Add(self.browse_folder_btn, 0, wx.ALL, 5)
		file_sizer.Add(browse_btn_sizer, 0, wx.ALL, 0)
		self.file_listbox = wx.ListBox(fileBox, choices=[self.get_file_display_name(f) for f in self.selected_files], style=wx.LB_EXTENDED)
		self.file_listbox.Bind(wx.EVT_KEY_DOWN, self.on_file_list_key_down)
		self.file_listbox.Bind(wx.EVT_CONTEXT_MENU, self.on_file_list_context_menu)
		self.file_listbox.SetMinSize((-1, 90))
		file_sizer.Add(self.file_listbox, 1, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(file_sizer, 0, wx.EXPAND | wx.ALL, 5)

		# Preset selector. Index 0 is "Custom" (use the effect controls
		# below). Index 1 is "Equalizer", which reveals a genre combo below
		# and applies a researched genre EQ curve. Indices 2+ map onto
		# MIXDOWN_PRESETS and use its fixed filter chain verbatim,
		# overriding the custom controls entirely while selected.
		preset_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Preset"))
		presetBox = preset_sizer.GetStaticBox()
		presetChoices = [_("Custom (use effect settings below)"), _("Equalizer (choose a music style)")] + [preset["name"] for preset in MIXDOWN_PRESETS]
		self.preset_combo = wx.ComboBox(presetBox, choices=presetChoices, style=wx.CB_READONLY)
		self.preset_combo.SetName(_("Preset"))
		self.preset_combo.SetSelection(0)
		self.preset_combo.Bind(wx.EVT_COMBOBOX, self.on_preset_selected)
		preset_sizer.Add(self.preset_combo, 0, wx.EXPAND | wx.ALL, 5)
		self.genre_sizer = wx.BoxSizer(wx.HORIZONTAL)
		self.genre_sizer.Add(wx.StaticText(presetBox, label=_("Music Style:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.genre_combo = wx.ComboBox(presetBox, choices=[genre["name"] for genre in GENRE_EQ_PRESETS], style=wx.CB_READONLY)
		self.genre_combo.SetName(_("Music Style"))
		self.genre_combo.SetSelection(0)
		self.genre_sizer.Add(self.genre_combo, 0, wx.ALL, 5)
		preset_sizer.Add(self.genre_sizer, 0, wx.EXPAND)
		self.genre_sizer.ShowItems(show=False)
		self.preset_description_label = wx.StaticText(presetBox, label="")
		preset_sizer.Add(self.preset_description_label, 0, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(preset_sizer, 0, wx.EXPAND | wx.ALL, 5)

		# One button holds the preset actions (Load, Save, Delete, Reset, Read Summary) so the
		# window stays short. They only apply while the Custom preset is chosen, so the
		# button is enabled and disabled with the effect controls.
		self.actions_btn = wx.Button(presetBox, label=_("Preset &Actions"))
		self.actions_btn.Bind(wx.EVT_BUTTON, self.on_actions)
		preset_sizer.Add(self.actions_btn, 0, wx.ALL, 5)
		self.customEffectControls.append(self.actions_btn)

		# The effect settings sit in tabs (Ctrl+Tab / Ctrl+PageDown changes tab) so every
		# control stays inside the window instead of running off the bottom of one very
		# tall dialog; each tab scrolls by itself if it ever needs more room.
		main_sizer.Add(wx.StaticText(self, label=_("Effect settings (use Ctrl+Tab to change tab):")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		self.effects_notebook = wx.Notebook(self)
		self.effects_notebook.SetName(_("Effect settings"))
		main_sizer.Add(self.effects_notebook, 1, wx.EXPAND | wx.ALL, 5)
		noisePage, noisePageSizer = self.make_effect_page(_("Noise"))
		tempoPage, tempoPageSizer = self.make_effect_page(_("Tempo and Speed"))
		tonePage, tonePageSizer = self.make_effect_page(_("Equalizer"))
		spatialPage, spatialPageSizer = self.make_effect_page(_("Spatial and Panning"))
		dynamicsPage, dynamicsPageSizer = self.make_effect_page(_("Dynamics"))
		creativePage, creativePageSizer = self.make_effect_page(_("Creative"))

		self.build_noise_page(noisePage, noisePageSizer)
		self.build_tempo_page(tempoPage, tempoPageSizer)
		self.build_tone_page(tonePage, tonePageSizer)
		self.build_spatial_page(spatialPage, spatialPageSizer)
		self.build_dynamics_page(dynamicsPage, dynamicsPageSizer)
		self.build_creative_page(creativePage, creativePageSizer)

		self.progress_bar = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
		main_sizer.Add(self.progress_bar, 0, wx.EXPAND | wx.ALL, 5)
		self.status_label = wx.StaticText(self, label="")
		main_sizer.Add(self.status_label, 0, wx.EXPAND | wx.ALL, 5)

		preview_row = wx.BoxSizer(wx.HORIZONTAL)
		preview_row.Add(wx.StaticText(self, label=_("Preview start (seconds):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.preview_start_ctrl = wx.SpinCtrlDouble(self, min=0, max=36000, initial=0, inc=5)
		self.preview_start_ctrl.SetDigits(0)
		self.preview_start_ctrl.SetName(_("Preview start"))
		preview_row.Add(self.preview_start_ctrl, 0, wx.ALL, 5)
		preview_row.Add(wx.StaticText(self, label=_("Preview length (seconds):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.preview_length_ctrl = wx.SpinCtrlDouble(self, min=MIN_PREVIEW_SECONDS, max=MAX_PREVIEW_SECONDS, initial=self.PREVIEW_SECONDS, inc=5)
		self.preview_length_ctrl.SetDigits(0)
		self.preview_length_ctrl.SetName(_("Preview length"))
		preview_row.Add(self.preview_length_ctrl, 0, wx.ALL, 5)
		main_sizer.Add(preview_row, 0, wx.ALIGN_CENTER | wx.ALL, 0)

		btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
		self.preview_btn = wx.Button(self, label=_("Preview"))
		self.preview_btn.Bind(wx.EVT_BUTTON, self.on_preview)
		btn_sizer.Add(self.preview_btn, 0, wx.ALL, 5)
		self.preview_original_btn = wx.Button(self, label=_("Preview Original (A/B)"))
		self.preview_original_btn.Bind(wx.EVT_BUTTON, self.on_preview_original)
		btn_sizer.Add(self.preview_original_btn, 0, wx.ALL, 5)
		self.apply_btn = wx.Button(self, label=_("Apply"))
		self.apply_btn.Bind(wx.EVT_BUTTON, self.on_apply)
		btn_sizer.Add(self.apply_btn, 0, wx.ALL, 5)
		self.reset_custom_btn = wx.Button(self, label=_("Reset Custom Effect"))
		self.reset_custom_btn.Bind(wx.EVT_BUTTON, self.on_reset_custom_effect)
		btn_sizer.Add(self.reset_custom_btn, 0, wx.ALL, 5)
		self.customEffectControls.append(self.reset_custom_btn)
		self.close_btn = wx.Button(self, wx.ID_CANCEL, label=_("Close"))
		self.close_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
		btn_sizer.Add(self.close_btn, 0, wx.ALL, 5)
		main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

		self.SetSizer(main_sizer)
		self.SetMinSize((620, 680))
		self.SetSize((700, 840))
		self.relayout()

	# ---- settings controls: every option lives in mixdownEngine.PARAMS and is
	# built, read, saved and restored from the same table.

	def register_control(self, key, widget, getValue, setValue):
		self.setting_controls[key] = {"widget": widget, "get": getValue, "set": setValue}
		self.customEffectControls.append(widget)

	def add_row(self, parent, sizer, key, label, name=None):
		"""One labelled control for the option `key`, built from its definition in PARAMS."""
		param = PARAMS[key]
		accessibleName = name or label.rstrip(":").strip()
		row = wx.BoxSizer(wx.HORIZONTAL)
		if param.kind != "bool":
			row.Add(wx.StaticText(parent, label=label), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		if param.kind == "bool":
			widget = wx.CheckBox(parent, label=label)
			getValue = widget.GetValue
			setValue = widget.SetValue
			widget.SetValue(param.default)
		elif param.kind == "choice":
			widget = wx.ComboBox(parent, choices=CHOICE_LABELS[key], style=wx.CB_READONLY)
			widget.SetName(accessibleName)

			def getValue(widget=widget, param=param):
				index = widget.GetSelection()
				return param.choices[index] if 0 <= index < len(param.choices) else param.default

			def setValue(value, widget=widget, param=param):
				widget.SetSelection(param.choices.index(value) if value in param.choices else 0)
			setValue(param.default)
		elif param.kind == "int":
			widget = self.make_stepped_spin(parent, param.minimum, param.maximum, param.default, param.step)
			widget.SetName(accessibleName)

			def getValue(widget=widget):
				return int(round(widget.GetValue()))

			def setValue(value, widget=widget):
				widget.SetValue(float(value))
		else:
			widget = wx.SpinCtrlDouble(parent, min=param.minimum, max=param.maximum, initial=param.default, inc=param.step)
			widget.SetDigits(param.digits)
			widget.SetName(accessibleName)
			getValue = widget.GetValue

			def setValue(value, widget=widget):
				widget.SetValue(float(value))
		row.Add(widget, 0, wx.ALL, 5)
		sizer.Add(row, 0, wx.EXPAND)
		self.register_control(key, widget, getValue, setValue)
		return widget

	def add_effect(self, box, boxSizer, enableKey, label, rows):
		"""A checkbox that switches an effect on, with that effect's own settings shown only while it is checked. Returns the settings sizer so a page can add more to it."""
		checkbox = wx.CheckBox(box, label=label)
		boxSizer.Add(checkbox, 0, wx.ALL, 5)
		self.register_control(enableKey, checkbox, checkbox.GetValue, checkbox.SetValue)
		settingsSizer = wx.BoxSizer(wx.VERTICAL)
		widgets = [checkbox]
		for spec in rows:
			widgets.append(self.add_row(box, settingsSizer, *spec))
		boxSizer.Add(settingsSizer, 0, wx.EXPAND)
		self.effect_widgets[enableKey] = widgets
		self.effect_labels[enableKey] = label
		self.bind_effect_toggle(checkbox, settingsSizer)
		return settingsSizer

	def build_noise_page(self, page, pageSizer):
		sizer = wx.StaticBoxSizer(wx.VERTICAL, page, label=_("Noise Reduction"))
		box = sizer.GetStaticBox()
		self.add_effect(box, sizer, "noiseReductionEnabled", _("Noise Reduction / Suppression (ANR)"), [
			("noiseReductionLevel", _("Noise Floor Level (dB):"), _("Noise Floor Level")),
		])
		self.add_effect(box, sizer, "noiseGateEnabled", _("Noise Gate (Cut Background Noise Between Words)"), [
			("noiseGateThresholdDb", _("Gate Threshold (dB):"), _("Noise Gate Threshold")),
		])
		self.add_effect(box, sizer, "rumbleEnabled", _("Rumble Filter (Cut Low Rumble)"), [
			("rumbleCutoffHz", _("Cut Everything Below (Hz):"), _("Rumble Cutoff")),
		])
		self.add_effect(box, sizer, "clickRemoverEnabled", _("Click and Crackle Remover"), [
			("clickRemoverMode", _("Remove:")),
			("clickRemoverThreshold", _("Click Detection Threshold (higher finds fewer clicks):"), _("Click Detection Threshold")),
		])
		self.add_effect(box, sizer, "deesserEnabled", _("De-esser (Soften Harsh S Sounds)"), [
			("deesserIntensity", _("De-esser Intensity (%):"), _("De-esser Intensity")),
			("deesserFrequency", _("De-esser Frequency (0 low to 100 high):"), _("De-esser Frequency")),
		])
		self.add_effect(box, sizer, "silenceTrimEnabled", _("Silence Trimmer"), [
			("silenceTrimMode", _("Trim:")),
			("silenceTrimThresholdDb", _("Silence Level (dB):"), _("Silence Level")),
			("silenceTrimMinSeconds", _("Shortest Gap to Remove (seconds):"), _("Shortest Gap to Remove")),
		])
		pageSizer.Add(sizer, 0, wx.EXPAND | wx.ALL, 5)

	def build_tempo_page(self, page, pageSizer):
		# Tempo and Fast Speed: two separate effects, each in its own group.
		tempo_sizer = wx.StaticBoxSizer(wx.VERTICAL, page, label=_("Tempo"))
		tempoBox = tempo_sizer.GetStaticBox()
		self.add_effect(tempoBox, tempo_sizer, "tempoEnabled", _("Adjust Tempo"), [
			("tempoFactor", _("Tempo Factor (0.5-2.0):"), _("Tempo Factor")),
		])
		# Always shown (not hidden with the factor), so it is never missing from the tab order.
		self.add_row(tempoBox, tempo_sizer, "tempoKeepPitch", _("Keep the original pitch (key)"))
		pageSizer.Add(tempo_sizer, 0, wx.EXPAND | wx.ALL, 5)

		fast_sizer = wx.StaticBoxSizer(wx.VERTICAL, page, label=_("Fast Speed"))
		fastBox = fast_sizer.GetStaticBox()
		direction_sizer = wx.BoxSizer(wx.HORIZONTAL)
		direction_sizer.Add(wx.StaticText(fastBox, label=_("Direction:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.direction_combo = wx.Choice(fastBox, choices=[_("Fast-forward (normal direction)"), _("Rewind (backwards)")])
		self.direction_combo.SetSelection(0)
		self.direction_combo.SetName(_("Direction"))
		direction_sizer.Add(self.direction_combo, 0, wx.ALL, 5)
		fast_sizer.Add(direction_sizer, 0, wx.EXPAND)
		self.register_control(
			"reversalEnabled", self.direction_combo,
			lambda: self.direction_combo.GetSelection() == 1,
			lambda value: self.direction_combo.SetSelection(1 if value else 0),
		)
		self.effect_labels["reversalEnabled"] = _("Rewind (backwards)")
		self.effect_widgets["reversalEnabled"] = [self.direction_combo]
		self.add_effect(fastBox, fast_sizer, "fastSpeedEnabled", _("Fast Speed (fast-forward)"), [
			("fastSpeedFactor", _("Fast Speed ({low}x to {high}x):").format(
				low=f"{MIN_FAST_SPEED_FACTOR:g}", high=f"{MAX_FAST_SPEED_FACTOR:g}"), _("Fast Speed")),
		])
		pageSizer.Add(fast_sizer, 0, wx.EXPAND | wx.ALL, 5)

		night_sizer = wx.StaticBoxSizer(wx.VERTICAL, page, label=_("Nightcore and Slowed"))
		nightBox = night_sizer.GetStaticBox()
		self.add_effect(nightBox, night_sizer, "nightcoreEnabled", _("Nightcore / Slowed (pitch follows speed)"), [
			("nightcoreMode", _("Style:")),
			("nightcoreAmountPercent", _("Amount (%):"), _("Nightcore Amount")),
		])
		pageSizer.Add(night_sizer, 0, wx.EXPAND | wx.ALL, 5)

	def build_tone_page(self, page, pageSizer):
		eq_sizer = wx.StaticBoxSizer(wx.VERTICAL, page, label=_("Equalization"))
		eqBox = eq_sizer.GetStaticBox()
		self.add_effect(eqBox, eq_sizer, "eqEnabled", _("Enable Bass / Treble Equalization"), [
			("bassGain", _("Bass Gain (dB):"), _("Bass Gain")),
			("trebleGain", _("Treble Gain (dB):"), _("Treble Gain")),
		])
		self.add_effect(eqBox, eq_sizer, "parametricEqEnabled", _("Advanced Parametric Equalizer (3-Band)"), [
			("parametricEqLowGain", _("Low Gain @ 100 Hz (dB):"), _("Parametric EQ Low Gain")),
			("parametricEqMidGain", _("Mid Gain @ 1 kHz (dB):"), _("Parametric EQ Mid Gain")),
			("parametricEqHighGain", _("High Gain @ 8 kHz (dB):"), _("Parametric EQ High Gain")),
		])
		self.add_effect(eqBox, eq_sizer, "voiceClarityEnabled", _("Voice Clarity (lift speech presence)"), [
			("voiceClarityAmount", _("Amount (1-10):"), _("Voice Clarity Amount")),
		])
		self.add_effect(eqBox, eq_sizer, "loudnessContourEnabled", _("Loudness Contour (smile curve for quiet listening)"), [
			("loudnessContourAmount", _("Amount (1-10):"), _("Loudness Contour Amount")),
		])
		self.add_effect(eqBox, eq_sizer, "tiltEnabled", _("Tilt EQ (warmer or brighter in one control)"), [
			("tiltDb", _("Tilt (dB, positive is brighter):"), _("Tilt")),
		])
		pageSizer.Add(eq_sizer, 0, wx.EXPAND | wx.ALL, 5)

	def build_spatial_page(self, page, pageSizer):
		spatial_sizer = wx.StaticBoxSizer(wx.VERTICAL, page, label=_("Spatial, Modulation && Panning"))
		spatialBox = spatial_sizer.GetStaticBox()
		self.spatialBox = spatialBox
		self.add_effect(spatialBox, spatial_sizer, "channelToolEnabled", _("Channel Tools (mono, stereo, swap sides)"), [
			("channelTool", _("Tool:")),
		])
		self.add_effect(spatialBox, spatial_sizer, "binauralEnabled", _("Binaural Audio (3D Headphone Simulation)"), [
			("binauralWidth", _("Width Intensity:"), _("Binaural Width Intensity")),
		])
		self.add_effect(spatialBox, spatial_sizer, "chorusEnabled", _("Chorus"), [
			("chorusDelayMs", _("Chorus Delay (ms):"), _("Chorus Delay")),
		])
		self.add_effect(spatialBox, spatial_sizer, "modulationEnabled", _("Flanger / Phaser / Tremolo / Vibrato"), [
			("modulationType", _("Type:")),
			("modulationRateHz", _("Rate (Hz):"), _("Modulation Rate")),
			("modulationDepthPercent", _("Depth (%):"), _("Modulation Depth")),
		])
		self.add_effect(spatialBox, spatial_sizer, "haasEnabled", _("Haas Effect (wide stereo without an echo)"), [
			("haasDelayMs", _("Delay (ms):"), _("Haas Delay")),
			("haasDelayedSide", _("Delayed Side:")),
		])
		self.add_effect(spatialBox, spatial_sizer, "reverbEnabled", _("Reverb / Echo"), [
			("echoDelayMs", _("Echo Delay (ms):"), _("Echo Delay")),
		])
		self.add_effect(spatialBox, spatial_sizer, "roomReverbEnabled", _("Multi-Room Reverb"), [
			("roomType", _("Room:")),
			("roomReverbAmount", _("Amount (%):"), _("Room Reverb Amount")),
		])
		self.add_effect(spatialBox, spatial_sizer, "muffledRoomEnabled", _("Muffled Room (Pub / Bathroom)"), [
			("muffleCutoffHz", _("Muffle Cutoff (Hz):"), _("Muffle Cutoff")),
		])
		self.add_row(spatialBox, spatial_sizer, "effectMixPercent",
			_("Effects Mix (% of Chorus, Modulation, Haas, Reverb, Room, Muffled, Frequency Shift):"), _("Effects Mix"))

		autopan_settings_sizer = self.add_effect(spatialBox, spatial_sizer, "autoPanLoopEnabled", _("Auto-Pan Loop (Circular, Dance Style)"), [
			("autoPanSpeedHz", _("Loop Speed (Hz):"), _("Auto-Pan Loop Speed")),
			("autoPanDirection", _("Loop Direction:"), _("Auto-Pan Loop Direction")),
			("autoPanKeepVocalCenter", _("Keep the singer's voice in the center (only the music moves)")),
			("autoPanVocalLowHz", _("Voice Band Low Edge (Hz):"), _("Voice Band Low Edge")),
			("autoPanVocalHighHz", _("Voice Band High Edge (Hz):"), _("Voice Band High Edge")),
		])
		autopan_settings_sizer.Add(wx.StaticText(spatialBox, label=_(
			"Loop sections change the speed or turn the loop around for a time range. Outside the sections the loop above is used.")),
			0, wx.ALL, 5)
		autopan_count_row = wx.BoxSizer(wx.HORIZONTAL)
		autopan_count_row.Add(wx.StaticText(spatialBox, label=_("Number of Loop Sections (0 = one steady loop):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.autopan_section_count_ctrl = wx.SpinCtrlDouble(spatialBox, min=0, max=MAX_SECTIONS, initial=0, inc=1)
		self.autopan_section_count_ctrl.SetDigits(0)
		self.autopan_section_count_ctrl.SetName(_("Number of Loop Sections"))
		self.autopan_section_count_ctrl.Bind(wx.EVT_SPINCTRLDOUBLE, self.on_autopan_section_count_changed)
		autopan_count_row.Add(self.autopan_section_count_ctrl, 0, wx.ALL, 5)
		autopan_settings_sizer.Add(autopan_count_row, 0, wx.EXPAND)
		self.autopan_sections_container = wx.BoxSizer(wx.VERTICAL)
		autopan_settings_sizer.Add(self.autopan_sections_container, 0, wx.EXPAND)
		self.customEffectControls.append(self.autopan_section_count_ctrl)
		self.effect_widgets["autoPanLoopEnabled"].append(self.autopan_section_count_ctrl)

		self.panning_checkbox = wx.CheckBox(spatialBox, label=_("Advanced Time-Based Panning"))
		spatial_sizer.Add(self.panning_checkbox, 0, wx.ALL, 5)
		self.register_control("panningEnabled", self.panning_checkbox, self.panning_checkbox.GetValue, self.panning_checkbox.SetValue)
		panning_settings_sizer = wx.BoxSizer(wx.VERTICAL)
		section_count_row = wx.BoxSizer(wx.HORIZONTAL)
		section_count_row.Add(wx.StaticText(spatialBox, label=_("Number of Sections:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.pan_section_count_ctrl = wx.SpinCtrlDouble(spatialBox, min=1, max=MAX_SECTIONS, initial=1, inc=1)
		self.pan_section_count_ctrl.SetDigits(0)
		self.pan_section_count_ctrl.SetName(_("Number of Sections"))
		self.pan_section_count_ctrl.Bind(wx.EVT_SPINCTRLDOUBLE, self.on_pan_section_count_changed)
		section_count_row.Add(self.pan_section_count_ctrl, 0, wx.ALL, 5)
		panning_settings_sizer.Add(section_count_row, 0, wx.EXPAND)
		self.pan_sections_container = wx.BoxSizer(wx.VERTICAL)
		panning_settings_sizer.Add(self.pan_sections_container, 0, wx.EXPAND)
		spatial_sizer.Add(panning_settings_sizer, 0, wx.EXPAND | wx.ALL, 5)
		pageSizer.Add(spatial_sizer, 0, wx.EXPAND | wx.ALL, 5)
		self.rebuild_pan_sections(1)
		self.customEffectControls.append(self.pan_section_count_ctrl)
		self.effect_widgets["panningEnabled"] = [self.panning_checkbox, self.pan_section_count_ctrl]
		self.effect_labels["panningEnabled"] = _("Advanced Time-Based Panning")
		self.bind_effect_toggle(self.panning_checkbox, panning_settings_sizer)

	def build_dynamics_page(self, page, pageSizer):
		dynamics_sizer = wx.StaticBoxSizer(wx.VERTICAL, page, label=_("Dynamics && Limiter"))
		box = dynamics_sizer.GetStaticBox()
		self.add_effect(box, dynamics_sizer, "volumeGainEnabled", _("Volume Gain"), [
			("volumeGainDb", _("Gain (dB):"), _("Volume Gain")),
		])
		self.add_effect(box, dynamics_sizer, "expanderEnabled", _("Expander / Compand (Night Mode)"), [
			("expanderPreset", _("Style:")),
		])
		self.add_effect(box, dynamics_sizer, "dynaudnormEnabled", _("Dynamic Normalizer (even out loud and quiet parts)"), [
			("dynaudnormFrameMs", _("Frame Length (ms):"), _("Dynamic Normalizer Frame Length")),
			("dynaudnormMaxGain", _("Maximum Gain (times):"), _("Dynamic Normalizer Maximum Gain")),
		])
		self.add_effect(box, dynamics_sizer, "speechnormEnabled", _("Speech Normalizer (even out speech)"), [
			("speechnormPeakPercent", _("Peak Level (%):"), _("Speech Normalizer Peak")),
			("speechnormExpansion", _("Maximum Expansion (times):"), _("Speech Normalizer Expansion")),
		])
		self.add_effect(box, dynamics_sizer, "compressorEnabled", _("Apply Compressor"), [
			("compressorThresholdDb", _("Compressor Threshold (dB):"), _("Compressor Threshold")),
			("compressorRatio", _("Ratio (to 1):"), _("Compressor Ratio")),
			("compressorAttackMs", _("Attack (ms):"), _("Compressor Attack")),
			("compressorReleaseMs", _("Release (ms):"), _("Compressor Release")),
			("compressorMakeupDb", _("Makeup Gain (dB):"), _("Compressor Makeup Gain")),
			("compressorKneeDb", _("Knee Softness (dB, 9 is the default):"), _("Compressor Knee")),
		])
		self.add_effect(box, dynamics_sizer, "softClipEnabled", _("Soft Clipper / Saturation (warmth)"), [
			("softClipType", _("Curve:")),
			("softClipDriveDb", _("Drive (dB):"), _("Soft Clipper Drive")),
		])
		self.add_effect(box, dynamics_sizer, "lofiEnabled", _("Lo-fi / Bitcrusher"), [
			("lofiBits", _("Bit Depth (bits):"), _("Lo-fi Bit Depth")),
			("lofiSampleReduction", _("Sample Rate Reduction (times):"), _("Lo-fi Sample Reduction")),
		])
		self.add_effect(box, dynamics_sizer, "loudnormEnabled", _("Loudness Normalization (EBU R128)"), [
			("loudnormTargetLufs", _("Target Loudness (LUFS):"), _("Loudness Normalization Target")),
			("loudnormTruePeakDb", _("True Peak Limit (dBTP):"), _("Loudness Normalization True Peak")),
			("loudnormTwoPass", _("Two-pass (measure the file first, lands closer to the target)")),
		])
		self.add_effect(box, dynamics_sizer, "limiterEnabled", _("Apply Limiter"), [
			("limiterLimitDb", _("Limit (dB):"), _("Limiter Limit")),
		])
		self.add_row(box, dynamics_sizer, "reportLoudness", _("Report loudness before and after (LUFS) when applying"))
		pageSizer.Add(dynamics_sizer, 0, wx.EXPAND | wx.ALL, 5)

	def build_creative_page(self, page, pageSizer):
		sizer = wx.StaticBoxSizer(wx.VERTICAL, page, label=_("Creative"))
		box = sizer.GetStaticBox()
		self.add_effect(box, sizer, "telephoneEnabled", _("Telephone / Radio Voice"), [
			("telephoneStyle", _("Style:")),
		])
		self.add_effect(box, sizer, "subBoostEnabled", _("Bass Boost (strong sub-bass)"), [
			("subBoostAmount", _("Boost (1-12):"), _("Bass Boost Amount")),
			("subBoostCutoffHz", _("Cutoff (Hz):"), _("Bass Boost Cutoff")),
		])
		self.add_effect(box, sizer, "enhancerEnabled", _("Audio Enhancer (crisper sound)"), [
			("enhancerIntensity", _("Intensity:"), _("Audio Enhancer Intensity")),
		])
		self.add_effect(box, sizer, "freqShiftEnabled", _("Frequency Shift (robot voice)"), [
			("freqShiftHz", _("Shift (Hz):"), _("Frequency Shift")),
		])
		pageSizer.Add(sizer, 0, wx.EXPAND | wx.ALL, 5)

	def bind_effect_toggle(self, checkbox, settingsSizer):
		"""Show an effect's settings only once it's checked, so the dialog isn't cluttered with controls for effects that aren't in use."""
		def refresh():
			settingsSizer.ShowItems(show=checkbox.GetValue())

		def _onToggle(event):
			refresh()
			self.refresh_section_visibility()
			self.relayout()
			event.Skip()
		checkbox.Bind(wx.EVT_CHECKBOX, _onToggle)
		self.toggle_refreshers.append(refresh)
		refresh()

	def refresh_all_toggles(self):
		for refresh in self.toggle_refreshers:
			refresh()
		self.refresh_section_visibility()
		self.relayout()

	def refresh_section_visibility(self):
		"""Showing a whole effect group also shows rows inside it that were hidden on purpose, so those rows are hidden again."""
		for widgets in self.pan_section_widgets:
			widgets["update_amount"]()

	def _resize_sections(self, widgetsList, container, newCount, createFn, readFn, seed=None):
		previous = seed if seed is not None else [readFn(widgets) for widgets in widgetsList]
		for widgets in widgetsList:
			container.Detach(widgets["sizer"])
			staticBox = widgets["sizer"].GetStaticBox()
			widgets["sizer"].Clear(delete_windows=True)
			staticBox.Destroy()
		widgetsList.clear()
		for index in range(newCount):
			sectionSizer, widgets = createFn(index + 1, previous[index] if index < len(previous) else None)
			container.Add(sectionSizer, 0, wx.EXPAND | wx.ALL, 5)
			widgetsList.append(widgets)
		self.relayout()

	def create_pan_section_widgets(self, sectionNumber, previousValues):
		"""Build one "Section N" group (Start/End/Direction, plus an Amount row shown only for Full Left/Full Right), optionally seeded from a previous section's values so resizing the section count doesn't lose existing settings."""
		sectionSizer = wx.StaticBoxSizer(wx.VERTICAL, self.spatialBox, label=_("Section {}").format(sectionNumber))
		sectionBox = sectionSizer.GetStaticBox()
		grid = wx.FlexGridSizer(rows=3, cols=2, gap=(5, 5))
		grid.Add(wx.StaticText(sectionBox, label=_("Start Second:")), 0, wx.ALIGN_CENTER_VERTICAL)
		startCtrl = wx.TextCtrl(sectionBox, value=str(previousValues["start"]) if previousValues else "0")
		startCtrl.SetName(_("Section {} Start Second").format(sectionNumber))
		grid.Add(startCtrl, 0, wx.EXPAND)
		grid.Add(wx.StaticText(sectionBox, label=_("End Second:")), 0, wx.ALIGN_CENTER_VERTICAL)
		endCtrl = wx.TextCtrl(sectionBox, value=str(previousValues["end"]) if previousValues else "0")
		endCtrl.SetName(_("Section {} End Second").format(sectionNumber))
		grid.Add(endCtrl, 0, wx.EXPAND)
		grid.Add(wx.StaticText(sectionBox, label=_("Pan Direction:")), 0, wx.ALIGN_CENTER_VERTICAL)
		directionCombo = wx.ComboBox(
			sectionBox,
			choices=[_("Center"), _("Left to Right"), _("Right to Left"), _("Full Left"), _("Full Right")],
			style=wx.CB_READONLY,
		)
		directionCombo.SetName(_("Section {} Pan Direction").format(sectionNumber))
		directionCombo.SetSelection(previousValues["direction"] if previousValues else 0)
		grid.Add(directionCombo, 0, wx.EXPAND)
		sectionSizer.Add(grid, 0, wx.EXPAND | wx.ALL, 5)

		# Amount only makes sense for Full Left/Full Right (100 = fully
		# panned, 50 sits the sound partway to that side, 0 = center).
		amountSizer = wx.BoxSizer(wx.HORIZONTAL)
		amountSizer.Add(wx.StaticText(sectionBox, label=_("Pan Amount (%):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		amountCtrl = wx.SpinCtrlDouble(sectionBox, min=0, max=100, initial=previousValues["amount"] if previousValues else 100, inc=5)
		amountCtrl.SetDigits(0)
		amountCtrl.SetName(_("Section {} Pan Amount").format(sectionNumber))
		amountSizer.Add(amountCtrl, 0, wx.ALL, 5)
		sectionSizer.Add(amountSizer, 0, wx.EXPAND)

		def update_amount_visibility(event=None):
			directionIndex = directionCombo.GetSelection()
			directionKey = PAN_DIRECTION_KEYS[directionIndex] if 0 <= directionIndex < len(PAN_DIRECTION_KEYS) else "center"
			amountSizer.ShowItems(show=directionKey in ("full_left", "full_right"))
			self.relayout()
			if event is not None:
				event.Skip()
		directionCombo.Bind(wx.EVT_COMBOBOX, update_amount_visibility)
		update_amount_visibility()

		widgets = {
			"sizer": sectionSizer,
			"start_ctrl": startCtrl,
			"end_ctrl": endCtrl,
			"direction_combo": directionCombo,
			"amount_ctrl": amountCtrl,
			"update_amount": update_amount_visibility,
		}
		return sectionSizer, widgets

	@staticmethod
	def _read_pan_section(widgets):
		return {
			"start": widgets["start_ctrl"].GetValue(),
			"end": widgets["end_ctrl"].GetValue(),
			"direction": widgets["direction_combo"].GetSelection(),
			"amount": widgets["amount_ctrl"].GetValue(),
		}

	def rebuild_pan_sections(self, newCount, seed=None):
		"""Resize the number of panning sections, preserving existing sections' values where possible so growing or shrinking the count doesn't discard what's already set up."""
		self._resize_sections(self.pan_section_widgets, self.pan_sections_container, newCount,
			self.create_pan_section_widgets, self._read_pan_section, seed)

	def on_pan_section_count_changed(self, event):
		self.rebuild_pan_sections(int(self.pan_section_count_ctrl.GetValue()))
		event.Skip()

	def create_autopan_section_widgets(self, sectionNumber, previousValues):
		"""One "Loop Section N" group: a time range in which the Auto-Pan Loop turns the other way and/or runs at its own speed."""
		sectionSizer = wx.StaticBoxSizer(wx.VERTICAL, self.spatialBox, label=_("Loop Section {}").format(sectionNumber))
		sectionBox = sectionSizer.GetStaticBox()
		grid = wx.FlexGridSizer(rows=4, cols=2, gap=(5, 5))
		grid.Add(wx.StaticText(sectionBox, label=_("Start Second:")), 0, wx.ALIGN_CENTER_VERTICAL)
		startCtrl = wx.TextCtrl(sectionBox, value=str(previousValues["start"]) if previousValues else "0")
		startCtrl.SetName(_("Loop Section {} Start Second").format(sectionNumber))
		grid.Add(startCtrl, 0, wx.EXPAND)
		grid.Add(wx.StaticText(sectionBox, label=_("End Second:")), 0, wx.ALIGN_CENTER_VERTICAL)
		endCtrl = wx.TextCtrl(sectionBox, value=str(previousValues["end"]) if previousValues else "0")
		endCtrl.SetName(_("Loop Section {} End Second").format(sectionNumber))
		grid.Add(endCtrl, 0, wx.EXPAND)
		grid.Add(wx.StaticText(sectionBox, label=_("Loop Direction:")), 0, wx.ALIGN_CENTER_VERTICAL)
		directionCombo = wx.ComboBox(sectionBox, choices=CHOICE_LABELS["autoPanDirection"], style=wx.CB_READONLY)
		directionCombo.SetName(_("Loop Section {} Direction").format(sectionNumber))
		directionCombo.SetSelection(previousValues["direction"] if previousValues else 1)
		grid.Add(directionCombo, 0, wx.EXPAND)
		grid.Add(wx.StaticText(sectionBox, label=_("Loop Speed (Hz):")), 0, wx.ALIGN_CENTER_VERTICAL)
		speedParam = PARAMS["autoPanSpeedHz"]
		speedCtrl = wx.SpinCtrlDouble(
			sectionBox, min=speedParam.minimum, max=speedParam.maximum,
			initial=previousValues["speed"] if previousValues else self.setting_controls["autoPanSpeedHz"]["get"](), inc=speedParam.step,
		)
		speedCtrl.SetDigits(speedParam.digits)
		speedCtrl.SetName(_("Loop Section {} Speed").format(sectionNumber))
		grid.Add(speedCtrl, 0, wx.EXPAND)
		sectionSizer.Add(grid, 0, wx.EXPAND | wx.ALL, 5)
		widgets = {
			"sizer": sectionSizer,
			"start_ctrl": startCtrl,
			"end_ctrl": endCtrl,
			"direction_combo": directionCombo,
			"speed_ctrl": speedCtrl,
		}
		return sectionSizer, widgets

	@staticmethod
	def _read_autopan_section(widgets):
		return {
			"start": widgets["start_ctrl"].GetValue(),
			"end": widgets["end_ctrl"].GetValue(),
			"direction": widgets["direction_combo"].GetSelection(),
			"speed": widgets["speed_ctrl"].GetValue(),
		}

	def rebuild_autopan_sections(self, newCount, seed=None):
		self._resize_sections(self.autopan_section_widgets, self.autopan_sections_container, newCount,
			self.create_autopan_section_widgets, self._read_autopan_section, seed)

	def on_autopan_section_count_changed(self, event):
		self.rebuild_autopan_sections(int(self.autopan_section_count_ctrl.GetValue()))
		event.Skip()

	def set_pan_section_widgets_enabled(self, isEnabled):
		for widgets in self.pan_section_widgets:
			widgets["start_ctrl"].Enable(isEnabled)
			widgets["end_ctrl"].Enable(isEnabled)
			widgets["direction_combo"].Enable(isEnabled)
			widgets["amount_ctrl"].Enable(isEnabled)
		for widgets in self.autopan_section_widgets:
			widgets["start_ctrl"].Enable(isEnabled)
			widgets["end_ctrl"].Enable(isEnabled)
			widgets["direction_combo"].Enable(isEnabled)
			widgets["speed_ctrl"].Enable(isEnabled)

	def set_custom_controls_enabled(self, isEnabled):
		for ctrl in self.customEffectControls:
			ctrl.Enable(isEnabled and ctrl not in self.blocked_widgets)
		self.set_pan_section_widgets_enabled(isEnabled)

	# ---- reading and restoring all settings

	def get_effect_settings(self):
		rawSettings = {key: entry["get"]() for key, entry in self.setting_controls.items()}
		rawSettings["panSections"] = [
			{
				"startSec": self._parse_float_field(widgets["start_ctrl"], 0.0),
				"endSec": self._parse_float_field(widgets["end_ctrl"], 0.0),
				"direction": PAN_DIRECTION_KEYS[widgets["direction_combo"].GetSelection()] if 0 <= widgets["direction_combo"].GetSelection() < len(PAN_DIRECTION_KEYS) else "center",
				"amountPercent": int(widgets["amount_ctrl"].GetValue()),
			}
			for widgets in self.pan_section_widgets
		]
		rawSettings["autoPanSections"] = [
			{
				"startSec": self._parse_float_field(widgets["start_ctrl"], 0.0),
				"endSec": self._parse_float_field(widgets["end_ctrl"], 0.0),
				"direction": AUTOPAN_DIRECTION_KEYS[widgets["direction_combo"].GetSelection()] if 0 <= widgets["direction_combo"].GetSelection() < len(AUTOPAN_DIRECTION_KEYS) else "left_to_right",
				"speedHz": widgets["speed_ctrl"].GetValue(),
			}
			for widgets in self.autopan_section_widgets
		]
		return sanitize_settings(rawSettings)

	def set_effect_settings(self, rawSettings):
		settings = sanitize_settings(rawSettings)
		for key, entry in self.setting_controls.items():
			value = settings[key]
			if key in self.unsupported_effects:
				value = False
			entry["set"](value)
		self.rebuild_pan_sections(max(1, len(settings["panSections"])), [
			{
				"start": f"{section['startSec']:g}",
				"end": f"{section['endSec']:g}",
				"direction": PAN_DIRECTION_KEYS.index(section["direction"]),
				"amount": section["amountPercent"],
			}
			for section in settings["panSections"]
		] or [None])
		self.pan_section_count_ctrl.SetValue(float(max(1, len(settings["panSections"]))))
		self.rebuild_autopan_sections(len(settings["autoPanSections"]), [
			{
				"start": f"{section['startSec']:g}",
				"end": f"{section['endSec']:g}",
				"direction": AUTOPAN_DIRECTION_KEYS.index(section["direction"]),
				"speed": section["speedHz"],
			}
			for section in settings["autoPanSections"]
		])
		self.autopan_section_count_ctrl.SetValue(float(len(settings["autoPanSections"])))
		self.refresh_all_toggles()
		if self.preset_combo.GetSelection() != 0:
			self.set_custom_controls_enabled(False)
		else:
			self.set_custom_controls_enabled(True)

	# ---- my presets and the remembered last settings

	def load_user_store(self):
		data = load_config(self.user_presets_path)
		rawPresets = data.get("presets")
		self.user_presets = {}
		if isinstance(rawPresets, dict):
			for rawName, rawSettings in rawPresets.items():
				name = normalize_preset_name(rawName)
				if name and isinstance(rawSettings, dict):
					self.user_presets[name] = sanitize_settings(rawSettings)
		lastSettings = data.get("last")
		self.user_last_settings = lastSettings if isinstance(lastSettings, dict) else None

	def save_user_store(self):
		data = {"version": 1, "presets": self.user_presets}
		if self.user_last_settings is not None:
			data["last"] = self.user_last_settings
		return save_config(self.user_presets_path, data)

	def remember_last_settings(self, settings=None):
		"""Keep the Custom settings that were last used, so they are back the next time the dialog opens."""
		if self.preset_combo.GetSelection() != 0:
			return
		self.user_last_settings = settings if settings is not None else self.get_effect_settings()
		if not self.save_user_store():
			ui.message(_("Your last settings could not be saved."))

	@staticmethod
	def menu_text(text):
		return text.replace("&", "&&")

	def on_actions(self, event):
		"""The single Actions menu: every item that used to be a button of its own."""
		names = sorted(self.user_presets, key=str.casefold)
		entries = []
		if names:
			loadEntries = [(self.menu_text(name), lambda name=name: self.load_user_preset(name)) for name in names]
			entries.append((_("Load Preset"), loadEntries))
		else:
			entries.append((_("Load Preset (none saved)"), lambda: ui.message(_("There is no saved preset to load."))))
		entries.append((_("Save Current Settings..."), lambda: self.on_save_user_preset(None)))
		if names:
			deleteEntries = [(self.menu_text(name), lambda name=name: self.delete_user_preset(name)) for name in names]
			entries.append((_("Delete Preset"), deleteEntries))
		else:
			entries.append((_("Delete Preset (none saved)"), lambda: ui.message(_("There is no saved preset to delete."))))
		entries.append((_("Reset All Effects"), lambda: self.on_reset_effects(None)))
		entries.append((_("Read Effect Summary"), lambda: self.on_read_summary(None)))
		chooseMenuAction(self, _("Preset Actions"), entries)

	def load_user_preset(self, name):
		if name not in self.user_presets:
			ui.message(_("There is no saved preset to load."))
			return
		self.set_effect_settings(self.user_presets[name])
		ui.message(limitSpokenText(_("Loaded preset {}").format(name)))

	def on_save_user_preset(self, event):
		with wx.TextEntryDialog(self, _("Name for this preset:"), _("Save My Preset")) as dialog:
			if dialog.ShowModal() != wx.ID_OK:
				return
			name = normalize_preset_name(dialog.GetValue())
		if not name:
			ui.message(_("A preset needs a name."))
			return
		if name in self.user_presets:
			with wx.MessageDialog(self, _("A preset named {} already exists. Replace it?").format(name),
									_("Save My Preset"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION) as confirm:
				if confirm.ShowModal() != wx.ID_YES:
					return
		self.user_presets[name] = self.get_effect_settings()
		savedToDisk = self.save_user_store()
		if savedToDisk:
			ui.message(limitSpokenText(_("Preset {} saved").format(name)))
		else:
			ui.message(limitSpokenText(_("Preset {} could not be written to disk and is only kept until this window closes").format(name)))

	def delete_user_preset(self, name):
		if name not in self.user_presets:
			ui.message(_("There is no saved preset to delete."))
			return
		with wx.MessageDialog(self, _("Delete the preset {}?").format(name), _("Delete My Preset"),
								wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION) as confirm:
			if confirm.ShowModal() != wx.ID_YES:
				return
		self.user_presets.pop(name, None)
		self.save_user_store()
		ui.message(limitSpokenText(_("Preset {} deleted").format(name)))

	def on_reset_effects(self, event):
		self.set_effect_settings(DEFAULT_SETTINGS)
		ui.message(_("All effects were reset to their defaults"))

	def on_reset_custom_effect(self, event):
		self.set_effect_settings(DEFAULT_SETTINGS)
		self.remember_last_settings()
		ui.message(_("Custom effect settings were reset"))
		wx.CallAfter(self.reset_custom_btn.SetFocus)

	def on_read_summary(self, event):
		settings = self.get_effect_settings()
		lines = []
		for key, label in self.effect_labels.items():
			if settings.get(key):
				lines.append(label)
		if not lines:
			text = _("No effects are switched on.")
		else:
			text = _("{count} effects are switched on:").format(count=len(lines)) + "\n" + "\n".join(lines)
		for code, details in find_effect_conflicts(settings):
			text += "\n" + _("Warning: ") + describe_conflict(code, details)
		with wx.MessageDialog(self, text, _("Effect summary"), wx.OK) as summary:
			summary.ShowModal()

	def on_preset_selected(self, event):
		presetIndex = self.preset_combo.GetSelection()
		isCustom = presetIndex == 0
		isEqualizer = presetIndex == 1
		self.set_custom_controls_enabled(isCustom)
		self.genre_combo.Enable(isEqualizer)
		self.genre_sizer.ShowItems(show=isEqualizer)
		if isCustom:
			self.preset_description_label.SetLabel("")
		elif isEqualizer:
			self.preset_description_label.SetLabel(_("Applies a genre-tuned EQ curve for the selected music style."))
		else:
			self.preset_description_label.SetLabel(MIXDOWN_PRESETS[presetIndex - 2]["description"])
		self.relayout()
		event.Skip()

	def resolve_processing_plan(self, effectSettings):
		"""The MixdownPlan for the chosen preset: a fixed filter chain for the named presets and genre curves, or the plan built from the effect settings for Custom (a plain -af chain, or a -filter_complex graph when an effect needs several streams)."""
		presetIndex = self.preset_combo.GetSelection()
		if presetIndex == 1:
			genreIndex = self.genre_combo.GetSelection()
			if 0 <= genreIndex < len(GENRE_EQ_PRESETS):
				return MixdownPlan.fromChain(GENRE_EQ_PRESETS[genreIndex]["filterChain"])
			return MixdownPlan.fromChain("")
		if presetIndex > 1:
			return MixdownPlan.fromChain(MIXDOWN_PRESETS[presetIndex - 2]["filterChain"])
		return build_custom_plan(effectSettings)

	def resolve_output_tag(self, effectSettings=None):
		"""Short tag naming the output file after whatever processing was actually used (e.g. "_pop", "_binaural"), instead of a generic "_mixdown" that collides across different presets applied to the same source file."""
		presetIndex = self.preset_combo.GetSelection()
		if presetIndex == 0:
			tags = []
			if effectSettings and effectSettings.get("reversalEnabled"):
				tags.append("rewind")
			if effectSettings and effectSettings.get("tempoEnabled") and abs(effectSettings.get("tempoFactor", 1.0) - 1.0) > 0.001:
				tags.append(f"tempo{effectSettings['tempoFactor']:g}" + ("" if effectSettings.get("tempoKeepPitch", True) else "_tape"))
			if effectSettings and effectSettings.get("fastSpeedEnabled") and effectSettings.get("fastSpeedFactor", 1.0) > 1.001:
				tags.append(f"x{effectSettings['fastSpeedFactor']:g}")
			if effectSettings and effectSettings.get("nightcoreEnabled"):
				tags.append(effectSettings.get("nightcoreMode", "nightcore"))
			return "_".join(["custom"] + tags)
		if presetIndex == 1:
			genreIndex = self.genre_combo.GetSelection()
			if 0 <= genreIndex < len(GENRE_EQ_PRESETS):
				return GENRE_EQ_PRESETS[genreIndex]["shortTag"]
			return "eq"
		mixdownIndex = presetIndex - 2
		if 0 <= mixdownIndex < len(MIXDOWN_PRESETS):
			return MIXDOWN_PRESETS[mixdownIndex]["shortTag"]
		return "mixdown"

	def set_ui_processing_state(self, isProcessing):
		for ctrl in (
			self.browse_btn, self.browse_folder_btn, self.preset_combo, self.preview_btn, self.preview_original_btn,
			self.apply_btn, self.preview_start_ctrl, self.preview_length_ctrl,
		):
			ctrl.Enable(not isProcessing)
		if isProcessing:
			self.set_custom_controls_enabled(False)
			self.genre_combo.Enable(False)
		else:
			presetIndex = self.preset_combo.GetSelection()
			if presetIndex == 0:
				self.set_custom_controls_enabled(True)
			self.genre_combo.Enable(presetIndex == 1)
		self.progress_bar.SetValue(0)

	def confirm_settings_before_run(self, effectSettings, askToContinue):
		"""For Custom: refuse effects this ffmpeg lacks, and tell about effects that fight each other. Returns False when the run should not start."""
		if self.preset_combo.GetSelection() != 0:
			return True
		missing = [self.effect_labels.get(key, key) for key in self.unsupported_effects if effectSettings.get(key)]
		if missing:
			ui.message(limitSpokenText(_("These effects are not available in this ffmpeg: {}").format(", ".join(missing))))
			return False
		conflicts = find_effect_conflicts(effectSettings)
		if not conflicts:
			return True
		sentences = [describe_conflict(code, details) for code, details in conflicts]
		if not askToContinue:
			ui.message(limitSpokenText(sentences[0] + (" " + _("{} more warnings.").format(len(sentences) - 1) if len(sentences) > 1 else "")))
			return True
		tones.beep(500, 150)
		text = "\n".join(sentences) + "\n\n" + _("Continue anyway?")
		with wx.MessageDialog(self, text, _("Effects that conflict"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING) as confirm:
			return confirm.ShowModal() == wx.ID_YES

	def build_mixdown_command(self, file_path, plan, outputPath, isPreview, previewStart=0, previewLength=15, loudnormStage=None):
		"""Run in the worker thread: reading the source bitrate needs ffprobe, which must not run on the main thread."""
		sourceExt = os.path.splitext(file_path)[1].lower()
		cmd = [self.ffmpeg_exe, "-hide_banner"]
		usesReverse = plan.usesReverse()
		if isPreview and usesReverse:
			# Rewinding needs the whole input in memory, so the preview rewinds
			# only the first stretch of the file (long enough for the chosen speed).
			if previewStart > 0:
				cmd += ["-ss", f"{previewStart:.3f}"]
			cmd += ["-t", f"{previewLength * plan.speedFactor():.3f}"]
		cmd += ["-i", file_path]
		if isPreview and not usesReverse:
			# Seeking on the output side keeps panning sections on the same
			# timeline as the whole file.
			if previewStart > 0:
				cmd += ["-ss", f"{previewStart:.3f}"]
			cmd += ["-t", str(previewLength)]

		filterArgs = plan.filterArgs(loudnormStage)
		cmd += filterArgs
		if filterArgs:
			if sourceExt == ".wav":
				cmd += ["-c:a", "pcm_s16le"]
			else:
				bitrateKbps = get_audio_bitrate_kbps(self.ffprobe_exe, file_path)
				cmd += ["-c:a", "libmp3lame", "-b:a", f"{bitrateKbps}k"]
		else:
			cmd += ["-c:a", "copy"]
		cmd += ["-progress", "pipe:1", "-nostats", "-y", outputPath]
		return cmd

	def build_analysis_command(self, file_path, plan):
		"""The first pass of a two-pass loudness normalization: the whole chain runs, loudnorm only measures, and nothing is written."""
		cmd = [self.ffmpeg_exe, "-hide_banner", "-i", file_path]
		cmd += plan.filterArgs(plan.analysisStage())
		cmd += ["-progress", "pipe:1", "-nostats", "-f", "null", "-"]
		return cmd

	def measure_for_two_pass(self, file_path, plan, durationSeconds, onProgress):
		"""Run the measuring pass and return the second-pass loudnorm stage, or None when the measurement is unusable (the single pass is used then)."""
		name = os.path.basename(file_path)
		self.post(self.status_label.SetLabel, _("Measuring loudness: {}").format(name))
		try:
			exitCode, stderrLines = runFfmpeg(
				self.build_analysis_command(file_path, plan), os.path.dirname(file_path), durationSeconds,
				lambda percent: onProgress(percent // 2), self.register_process,
			)
		except OSError as e:
			log.error(f"Mixdown could not run the loudness measurement for {file_path}: {e}")
			return None
		if self.cancel_event.is_set():
			return None
		measured = parse_loudnorm_measurement(stderrLines) if exitCode == 0 else None
		if measured is None:
			log.info(f"Mixdown: two-pass loudness measurement gave no usable numbers for {file_path}; using one pass")
			self.post(ui.message, limitSpokenText(_("Two-pass loudness could not measure {}, so one pass is used").format(name)))
			return None
		return two_pass_loudnorm(plan.loudnormStage, measured)

	def do_process_file(self, file_path, plan, outputTag="mixdown", isPreview=False, onProgress=None, previewStart=0, previewLength=15):
		"""Run ffmpeg with the resolved plan. A plain filter chain (or none at all) uses -af / stream copy as before; a plan with several streams uses -filter_complex with an explicit -map. Output always matches the source's own extension (.mp3 stays .mp3, .wav stays .wav) so a no-filter stream copy is always valid, and a filtered re-encode uses the matching codec for that container. Returns the output path, or None after reporting the failure."""
		sourceExt = os.path.splitext(file_path)[1].lower()
		progressCallback = onProgress or (lambda percent: None)
		name = os.path.basename(file_path)
		if isPreview:
			finalPath = os.path.join(self.preview_dir, f"preview{sourceExt}")
			partPath = finalPath
			durationSeconds = previewLength
			if previewStart > 0:
				fileDuration, fileDurationLabel = get_file_duration(self.libs_path, file_path)
				if fileDuration > 0 and previewStart >= fileDuration - 0.5:
					self.post(ui.message, limitSpokenText(
						_("The preview start is past the end of {name}, which is {length} long").format(name=name, length=fileDurationLabel)
					))
					return None
		else:
			outputDir = os.path.dirname(file_path)
			baseName = f"{os.path.splitext(os.path.basename(file_path))[0]}_{outputTag}"
			finalPath = os.path.join(outputDir, get_unique_filename(outputDir, baseName, sourceExt.lstrip(".")))
			partPath = partPathFor(finalPath)
			durationSeconds, _durationLabel = get_file_duration(self.libs_path, file_path)
			if plan.usesReverse() and durationSeconds > REVERSE_MAX_SECONDS:
				self.post(ui.message, limitSpokenText(
					_("Rewind is limited to files up to {minutes} minutes: {name}").format(
						minutes=REVERSE_MAX_SECONDS // 60, name=name)
				))
				return None
			# The output is shorter or longer than the source by the speed factor.
			durationSeconds = durationSeconds / plan.speedFactor()

		loudnormStage = None
		encodeProgress = progressCallback
		if plan.twoPassLoudnorm and not isPreview:
			loudnormStage = self.measure_for_two_pass(file_path, plan, durationSeconds, progressCallback)
			if self.cancel_event.is_set():
				return None
			if loudnormStage is not None:
				def encodeProgress(percent):
					progressCallback(50 + percent // 2)
				self.post(self.status_label.SetLabel, _("Processing: {}").format(name))

		try:
			command = self.build_mixdown_command(file_path, plan, partPath, isPreview, previewStart, previewLength, loudnormStage)
			exitCode, stderrLines = runFfmpeg(
				command, os.path.dirname(partPath), durationSeconds, encodeProgress, self.register_process,
			)
		except OSError as e:
			log.error(f"Mixdown could not run ffmpeg for {file_path}: {e}")
			removeQuietly(partPath)
			self.post(ui.message, limitSpokenText(_("Failed to process {}").format(name)))
			return None

		if self.cancel_event.is_set():
			removeQuietly(partPath)
			return None
		if exitCode != 0 or not os.path.exists(partPath):
			log.error(f"Mixdown ffmpeg failed for {file_path}: " + " | ".join(line.strip() for line in stderrLines))
			removeQuietly(partPath)
			self.post(ui.message, limitSpokenText(
				_("Failed to process {name}: {reason}").format(name=name, reason=summarizeFfmpegError(stderrLines))
			))
			return None
		if partPath != finalPath:
			try:
				os.replace(partPath, finalPath)
			except OSError as e:
				log.error(f"Mixdown could not move {partPath} to {finalPath}: {e}")
				removeQuietly(partPath)
				self.post(ui.message, limitSpokenText(_("Failed to process {}").format(name)))
				return None
		return finalPath

	def measure_integrated_loudness(self, filePath):
		"""Integrated loudness of a file in LUFS, or None when it cannot be measured."""
		command = [self.ffmpeg_exe, "-hide_banner", "-nostats", "-i", filePath, "-vn", "-af", "ebur128=framelog=quiet", "-f", "null", "-"]
		try:
			exitCode, stderrLines = runFfmpeg(command, os.path.dirname(filePath), 0, lambda percent: None, self.register_process)
		except OSError as e:
			log.error(f"Mixdown could not measure loudness of {filePath}: {e}")
			return None
		if exitCode != 0 or self.cancel_event.is_set():
			return None
		return parse_integrated_loudness(stderrLines)

	def report_loudness(self, sourcePath, outputPath):
		self.post(self.status_label.SetLabel, _("Measuring loudness: {}").format(os.path.basename(outputPath)))
		beforeLufs = self.measure_integrated_loudness(sourcePath)
		afterLufs = self.measure_integrated_loudness(outputPath)
		if self.cancel_event.is_set():
			return
		if beforeLufs is None and afterLufs is None:
			log.info(f"Mixdown: no loudness could be measured for {outputPath}")
			return
		unknown = _("unknown")
		self.post(ui.message, limitSpokenText(_("Loudness of {name}: before {before}, after {after} LUFS").format(
			name=os.path.basename(outputPath),
			before=f"{beforeLufs:.1f}" if beforeLufs is not None else unknown,
			after=f"{afterLufs:.1f}" if afterLufs is not None else unknown,
		)))

	def on_preview(self, event):
		self.start_preview(useOriginal=False)

	def on_preview_original(self, event):
		self.start_preview(useOriginal=True)

	def start_preview(self, useOriginal):
		if not self.selected_files:
			ui.message(_("Please select at least one MP3 or WAV file first."))
			return
		if self.currently_processing:
			ui.message(_("Mixdown is already processing files"))
			return
		if not os.path.exists(self.ffmpeg_exe):
			ui.message(_("ffmpeg.exe not found"))
			return
		previewSourceFile = self.get_preview_source_file()
		effectSettings = self.get_effect_settings()
		if not useOriginal:
			if not self.confirm_settings_before_run(effectSettings, askToContinue=False):
				return
			plan = self.resolve_processing_plan(effectSettings)
			self.remember_last_settings(effectSettings)
		else:
			plan = MixdownPlan.fromChain("")
		previewStart = int(self.preview_start_ctrl.GetValue())
		previewLength = int(self.preview_length_ctrl.GetValue())
		self.cleanup_preview_dir()
		self.preview_dir = tempfile.mkdtemp(prefix="xtrack_mixdown_preview_")
		self.cancel_event.clear()
		self.currently_processing = True
		self.set_ui_processing_state(True)
		self.status_label.SetLabel(_("Generating preview: {}").format(os.path.basename(previewSourceFile)))
		threading.Thread(
			target=self.run_preview_job, args=(previewSourceFile, plan, previewStart, previewLength, useOriginal), daemon=True,
		).start()

	def run_preview_job(self, file_path, plan, previewStart, previewLength, useOriginal):
		outputPath = self.do_process_file(file_path, plan, isPreview=True, previewStart=previewStart, previewLength=previewLength)
		if not self.cancel_event.is_set():
			self.post(self.on_preview_finished, outputPath, previewLength, useOriginal)

	def on_preview_finished(self, outputPath, previewLength, useOriginal):
		self.currently_processing = False
		self.set_ui_processing_state(False)
		self.status_label.SetLabel(_("Ready"))
		if not outputPath:
			return
		try:
			os.startfile(outputPath)
		except OSError as e:
			log.error(f"Mixdown failed to launch preview player: {e}")
			ui.message(_("Preview created but could not be opened automatically"))
			return
		if useOriginal:
			ui.message(_("Playing {}-second preview of the original").format(previewLength))
		else:
			ui.message(_("Playing {}-second preview").format(previewLength))

	def on_apply(self, event):
		if not self.selected_files:
			ui.message(_("Please select at least one MP3 or WAV file first."))
			return
		if self.currently_processing:
			ui.message(_("Mixdown is already processing files"))
			return
		if not os.path.exists(self.ffmpeg_exe):
			ui.message(_("ffmpeg.exe not found"))
			return
		effectSettings = self.get_effect_settings()
		if not self.confirm_settings_before_run(effectSettings, askToContinue=True):
			return
		plan = self.resolve_processing_plan(effectSettings)
		outputTag = self.resolve_output_tag(effectSettings)
		self.remember_last_settings(effectSettings)
		shouldReportLoudness = self.preset_combo.GetSelection() == 0 and effectSettings.get("reportLoudness", False)
		self.cancel_event.clear()
		self.currently_processing = True
		self.set_ui_processing_state(True)
		threading.Thread(
			target=self.run_apply_job, args=(list(self.selected_files), plan, outputTag, shouldReportLoudness), daemon=True,
		).start()

	def run_apply_job(self, files, plan, outputTag, shouldReportLoudness):
		okCount = failCount = 0
		total = len(files)
		for index, file_path in enumerate(files):
			if self.cancel_event.is_set():
				return
			self.post(self.status_label.SetLabel, _("Processing: {}").format(os.path.basename(file_path)))

			def reportProgress(percent, index=index):
				self.post(self.progress_bar.SetValue, min(int(((index + percent / 100) / total) * 100), 100))

			outputPath = self.do_process_file(file_path, plan, outputTag=outputTag, isPreview=False, onProgress=reportProgress)
			if self.cancel_event.is_set():
				return
			if outputPath:
				okCount += 1
				self.post(ui.message, limitSpokenText(_("Created {}").format(os.path.basename(outputPath))))
				if shouldReportLoudness:
					self.report_loudness(file_path, outputPath)
					if self.cancel_event.is_set():
						return
			else:
				failCount += 1
			self.post(self.progress_bar.SetValue, min(int(((index + 1) / total) * 100), 100))
		self.post(self.on_batch_complete, okCount, failCount)

	def on_cancel(self, event):
		if not self.currently_processing:
			self.remember_last_settings()
		self.stop_processing()
		self.cleanup_preview_dir()
		self.EndModal(wx.ID_CANCEL)
