# mp3_mixdown.py

import wx
import os
import subprocess
import threading
import queue
import tempfile
import ui
import tones
from gui import guiHelper
from .xTrackCore import get_config_path, get_unique_filename, get_file_duration
import addonHandler
from logHandler import log

addonHandler.initTranslation()

AUDIO_EXTENSIONS = (".mp3", ".wav")

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

# Index 0 is "Custom"; indices 1+ line up with the list above.
PAN_DIRECTION_KEYS = ["center", "left_to_right", "right_to_left", "full_left", "full_right"]


def build_tempo_stages(tempoFactor):
	"""Break a tempo factor outside ffmpeg's single-filter 0.5-2.0 atempo range into a chain of atempo filters, since atempo cannot exceed that range in one step."""
	remainingFactor = tempoFactor
	stages = []
	while remainingFactor > 2.0:
		stages.append("atempo=2.0")
		remainingFactor /= 2.0
	while remainingFactor < 0.5:
		stages.append("atempo=0.5")
		remainingFactor /= 0.5
	stages.append(f"atempo={remainingFactor:.3f}")
	return stages


def build_pre_panning_stages(effectSettings):
	"""Everything that runs before the spatial/panning stage: noise reduction, time/pitch, EQ, then binaural/chorus/reverb, in the fixed order required to avoid distorting the signal."""
	stages = []

	if effectSettings.get("noiseReductionEnabled"):
		noiseFloorDb = effectSettings.get("noiseReductionLevel", -25)
		stages.append(f"afftdn=nf={noiseFloorDb}")

	if effectSettings.get("noiseGateEnabled"):
		gateThresholdDb = effectSettings.get("noiseGateThresholdDb", -40)
		stages.append(f"agate=threshold={gateThresholdDb}dB:ratio=2:attack=20:release=250")

	if effectSettings.get("reversalEnabled"):
		stages.append("areverse")

	if effectSettings.get("tempoEnabled"):
		stages.extend(build_tempo_stages(effectSettings.get("tempoFactor", 1.0)))

	if effectSettings.get("eqEnabled"):
		bassGain = effectSettings.get("bassGain", 0)
		trebleGain = effectSettings.get("trebleGain", 0)
		stages.append(f"bass=g={bassGain}:f=100:w=0.5,treble=g={trebleGain}:f=3000:w=0.5")

	if effectSettings.get("parametricEqEnabled"):
		lowGainDb = effectSettings.get("parametricEqLowGain", 0)
		midGainDb = effectSettings.get("parametricEqMidGain", 0)
		highGainDb = effectSettings.get("parametricEqHighGain", 0)
		stages.append(f"firequalizer=gain_entry='entry(100\\,{lowGainDb});entry(1000\\,{midGainDb});entry(8000\\,{highGainDb})'")

	if effectSettings.get("binauralEnabled"):
		# bs2b requires an ffmpeg build compiled with --enable-libbs2b, which
		# the bundled ffmpeg.exe does not have. crossfeed is a core filter
		# built for exactly this purpose (headphone crossfeed simulation,
		# the same concept bs2b implements) and extrastereo widens the image
		# further -- earwax alone was too subtle to read as "3D".
		widthIntensity = effectSettings.get("binauralWidth", 2.5)
		stages.append(f"crossfeed=strength=0.3:range=0.9,extrastereo=m={widthIntensity}:c=disabled")

	if effectSettings.get("chorusEnabled"):
		chorusDelayMs = effectSettings.get("chorusDelayMs", 50)
		stages.append(f"chorus=0.5:0.9:{chorusDelayMs}:0.4:0.25:2")

	if effectSettings.get("reverbEnabled"):
		echoDelayMs = effectSettings.get("echoDelayMs", 150)
		stages.append(f"aecho=0.8:0.88:{echoDelayMs}:0.4")

	if effectSettings.get("muffledRoomEnabled"):
		muffleCutoffHz = effectSettings.get("muffleCutoffHz", 1200)
		stages.append(f"lowpass=f={muffleCutoffHz},bass=g=4:f=150:w=0.6,aecho=0.6:0.7:40|60:0.5|0.3")

	if effectSettings.get("autoPanLoopEnabled"):
		# apulsator modulates left/right gain 180 degrees out of phase,
		# giving a continuous circular pan loop across the whole file -- the
		# auto-pan style heard throughout dance tracks, as opposed to
		# Advanced Time-Based Panning's one-shot sweep. Swapping which
		# channel leads (offset_l/offset_r) reverses the perceived direction
		# of the loop.
		loopSpeedHz = effectSettings.get("autoPanSpeedHz", 0.5)
		if effectSettings.get("autoPanDirection", "left_to_right") == "right_to_left":
			offsetL, offsetR = 0.5, 0
		else:
			offsetL, offsetR = 0, 0.5
		stages.append(f"apulsator=mode=sine:hz={loopSpeedHz}:amount=1:offset_l={offsetL}:offset_r={offsetR}")

	return stages


def build_post_panning_stages(effectSettings):
	"""Dynamics & Limiter, always the final stage."""
	stages = []
	if effectSettings.get("compressorEnabled"):
		thresholdDb = effectSettings.get("compressorThresholdDb", -20)
		stages.append(f"acompressor=threshold={thresholdDb}dB:ratio=4:attack=5:release=50")
	if effectSettings.get("loudnormEnabled"):
		targetLufs = effectSettings.get("loudnormTargetLufs", -16)
		truePeakDb = effectSettings.get("loudnormTruePeakDb", -1.5)
		stages.append(f"loudnorm=I={targetLufs}:TP={truePeakDb}:LRA=11")
	if effectSettings.get("limiterEnabled"):
		limitDb = effectSettings.get("limiterLimitDb", -1)
		stages.append(f"alimiter=limit={limitDb}dB")
	return stages


def build_filter_graph(effectSettings):
	"""Build the plain linear filter chain. Advanced Time-Based Panning is never part of this chain -- any active section always routes through build_pan_multi_filter_complex instead, since even a single section needs the channel-split treatment for a click-free result."""
	stages = build_pre_panning_stages(effectSettings)
	stages.extend(build_post_panning_stages(effectSettings))
	return ",".join(stages)


def get_active_pan_sections(panSections):
	"""Drop sections that are no-ops (Center) or have an invalid/empty time window."""
	return [s for s in (panSections or []) if s.get("direction") != "center" and s.get("endSec", 0) > s.get("startSec", 0)]


def pan_section_end_value(section, side):
	"""The exact gain a section settles on at its own endSec. For sweeps this doubles as their ramp target; for Full Left/Right (a static, non-ramping bias) it's just the constant value held throughout."""
	direction = section["direction"]
	if direction == "left_to_right":
		return 0.0 if side == "L" else 1.0
	if direction == "right_to_left":
		return 1.0 if side == "L" else 0.0
	scale = max(0.0, min(100.0, section.get("amountPercent", 100))) / 100.0
	if direction == "full_left":
		return 1.0 if side == "L" else (1.0 - scale)
	return (1.0 - scale) if side == "L" else 1.0


def build_pan_section_channel_expr(section, side, entryValue):
	"""The gain expression for one channel ('L' or 'R') within a single section's own time window. Center never reaches here (filtered out by get_active_pan_sections). Sweeps ramp linearly from entryValue -- whatever the channel is actually carrying in, whether that's normal (1.0) before the first section or a value held over from an earlier section -- to this direction's target extreme at endSec, so the ramp is always continuous with whatever precedes it, not just when starting from normal. Full Left/Right jump immediately to their amount-scaled target and hold it for the whole window, since there's no in-between motion to interpolate for a static bias."""
	direction = section["direction"]
	startSec = section["startSec"]
	endSec = section["endSec"]
	if direction in ("left_to_right", "right_to_left"):
		targetValue = pan_section_end_value(section, side)
		if targetValue == entryValue:
			return str(entryValue)
		rampProgress = f"(t-{startSec})/({endSec}-{startSec})"
		delta = targetValue - entryValue
		return f"{entryValue}+({delta})*{rampProgress}"
	scale = max(0.0, min(100.0, section.get("amountPercent", 100))) / 100.0
	if direction == "full_left":
		return "1" if side == "L" else f"{1 - scale}"
	return f"{1 - scale}" if side == "L" else "1"


def build_pan_multi_channel_expr(activeSections, side):
	"""Combine every section's own window into one expression for a single channel, as a chronological piecewise step function: before the first section, gain is normal (1.0); during a section, its own ramp/static formula applies (ramping from whatever value the timeline is currently carrying); in a gap between sections (and after the last one), gain holds constant at whatever the previous section ended on -- so the join between sections, and the tail after the last one, never jumps. Commas inside the expression must be escaped, since ffmpeg's graph syntax otherwise reads an unescaped comma as the next chained filter."""
	sortedSections = sorted(activeSections, key=lambda s: s["startSec"])
	if not sortedSections:
		return "1"

	timeSegments = []  # list of (breakpointTime, valueExprBeforeThatBreakpoint), in chronological order
	cursor = 0.0
	heldValue = 1.0
	for section in sortedSections:
		startSec = section["startSec"]
		endSec = section["endSec"]
		if startSec > cursor:
			timeSegments.append((startSec, str(heldValue)))
		timeSegments.append((endSec, build_pan_section_channel_expr(section, side, heldValue)))
		heldValue = pan_section_end_value(section, side)
		cursor = endSec

	expr = str(heldValue)  # tail: holds the last section's ending value for the rest of the file
	for breakpoint, valueExpr in reversed(timeSegments):
		expr = f"if(lt(t\\,{breakpoint})\\,{valueExpr}\\,{expr})"
	return expr


def build_pan_multi_filter_complex(preChainStr, postChainStr, activeSections):
	"""A true continuously-varying stereo pan needs independent, time-varying gain per channel, which the simple linear -af chain cannot express (volume applies identically to every channel). This splits to mono per channel, applies one combined multi-section volume=eval=frame expression to each, then merges back to stereo -- all within one -filter_complex graph so the rest of the effect chain still runs in the correct order around it."""
	leftExpr = build_pan_multi_channel_expr(activeSections, "L")
	rightExpr = build_pan_multi_channel_expr(activeSections, "R")
	segments = []
	sourceLabel = "0:a"
	if preChainStr:
		segments.append(f"[{sourceLabel}]{preChainStr}[panSrc]")
		sourceLabel = "panSrc"
	segments.append(f"[{sourceLabel}]channelsplit=channel_layout=stereo[panL][panR]")
	segments.append(f"[panL]volume=eval=frame:volume='{leftExpr}'[panLg]")
	segments.append(f"[panR]volume=eval=frame:volume='{rightExpr}'[panRg]")
	segments.append("[panLg][panRg]amerge=inputs=2[panMerged]")
	outputLabel = "panMerged"
	if postChainStr:
		segments.append(f"[panMerged]{postChainStr}[panOut]")
		outputLabel = "panOut"
	return ";".join(segments), outputLabel


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
		log.error(f"MP3 WAV Mixdown failed to read bitrate for {file_path}: {e}")
	return 192


class MP3WavMixdownDialog(wx.Dialog):
	"""Dialog for chaining multiple FFmpeg audio effects (noise reduction, tempo/reversal, EQ, spatial/panning, dynamics) onto MP3 or WAV files, either through named presets or custom per-effect controls, with an async 15-second preview."""

	PREVIEW_SECONDS = 15

	def __init__(self, parent, selected_files, libs_path):
		super().__init__(parent, title=_("MP3 WAV Mixdown"))
		self.libs_path = libs_path
		self.ffmpeg_exe = os.path.join(libs_path, "ffmpeg.exe")
		self.ffprobe_exe = os.path.join(libs_path, "ffprobe.exe")
		self.config_path = get_config_path()
		self.fileImportRoot = {}
		self.selected_files = self._expand_to_audio_files(selected_files)
		self.processing_queue = queue.Queue()
		self.currently_processing = False
		self.current_processing_plan = ("simple", "")
		self.current_output_tag = "mixdown"
		self.processed_count = 0
		self.customEffectControls = []
		self.init_ui()
		self.SetTitle(_("MP3 WAV Mixdown: {} files").format(len(self.selected_files)))
		self.Bind(wx.EVT_CLOSE, self.on_close)
		wx.CallAfter(self.file_listbox.SetFocus)

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
			log.error(f"MP3 WAV Mixdown failed to scan folder {folder_path}: {e}")
		return foundFiles

	def make_stepped_spin(self, parent, minVal, maxVal, initial, increment=5):
		"""SpinCtrlDouble displayed as a whole number but stepping by `increment` per arrow press, since wx.SpinCtrl has no configurable step."""
		ctrl = wx.SpinCtrlDouble(parent, min=minVal, max=maxVal, initial=initial, inc=increment)
		ctrl.SetDigits(0)
		return ctrl

	def bind_effect_toggle(self, checkbox, settingsSizer):
		"""Show an effect's settings only once it's checked, so the dialog isn't cluttered with controls for effects that aren't in use."""
		def _onToggle(event):
			settingsSizer.ShowItems(show=checkbox.GetValue())
			self.Layout()
			event.Skip()
		checkbox.Bind(wx.EVT_CHECKBOX, _onToggle)
		settingsSizer.ShowItems(show=checkbox.GetValue())

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
			self.Layout()
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
		}
		return sectionSizer, widgets

	def rebuild_pan_sections(self, newCount):
		"""Resize the number of panning sections, preserving existing sections' values where possible so growing or shrinking the count doesn't discard what's already set up."""
		previousValues = []
		for widgets in self.pan_section_widgets:
			previousValues.append({
				"start": widgets["start_ctrl"].GetValue(),
				"end": widgets["end_ctrl"].GetValue(),
				"direction": widgets["direction_combo"].GetSelection(),
				"amount": widgets["amount_ctrl"].GetValue(),
			})

		for widgets in self.pan_section_widgets:
			self.pan_sections_container.Detach(widgets["sizer"])
			widgets["sizer"].Clear(delete_windows=True)
		self.pan_section_widgets = []

		for index in range(newCount):
			previous = previousValues[index] if index < len(previousValues) else None
			sectionSizer, widgets = self.create_pan_section_widgets(index + 1, previous)
			self.pan_sections_container.Add(sectionSizer, 0, wx.EXPAND | wx.ALL, 5)
			self.pan_section_widgets.append(widgets)

		self.Layout()

	def on_pan_section_count_changed(self, event):
		self.rebuild_pan_sections(int(self.pan_section_count_ctrl.GetValue()))
		event.Skip()

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
		file_sizer.Add(self.file_listbox, 1, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(file_sizer, 1, wx.EXPAND | wx.ALL, 5)

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

		# 1. Noise Reduction / Suppression
		noise_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Noise Reduction"))
		noiseBox = noise_sizer.GetStaticBox()
		self.noise_reduction_checkbox = wx.CheckBox(noiseBox, label=_("Noise Reduction / Suppression (ANR)"))
		noise_sizer.Add(self.noise_reduction_checkbox, 0, wx.ALL, 5)
		self.customEffectControls.append(self.noise_reduction_checkbox)
		noise_settings_sizer = wx.BoxSizer(wx.HORIZONTAL)
		noise_settings_sizer.Add(wx.StaticText(noiseBox, label=_("Noise Floor Level (dB):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.noise_level_ctrl = self.make_stepped_spin(noiseBox, -80, -20, -25)
		self.noise_level_ctrl.SetName(_("Noise Floor Level"))
		noise_settings_sizer.Add(self.noise_level_ctrl, 0, wx.ALL, 5)
		noise_sizer.Add(noise_settings_sizer, 0, wx.EXPAND)
		main_sizer.Add(noise_sizer, 0, wx.EXPAND | wx.ALL, 5)
		self.customEffectControls.append(self.noise_level_ctrl)
		self.bind_effect_toggle(self.noise_reduction_checkbox, noise_settings_sizer)

		self.noise_gate_checkbox = wx.CheckBox(noiseBox, label=_("Noise Gate (Cut Background Noise Between Words)"))
		noise_sizer.Add(self.noise_gate_checkbox, 0, wx.ALL, 5)
		self.customEffectControls.append(self.noise_gate_checkbox)
		noise_gate_settings_sizer = wx.BoxSizer(wx.HORIZONTAL)
		noise_gate_settings_sizer.Add(wx.StaticText(noiseBox, label=_("Gate Threshold (dB):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.noise_gate_threshold_ctrl = self.make_stepped_spin(noiseBox, -60, -10, -40)
		self.noise_gate_threshold_ctrl.SetName(_("Noise Gate Threshold"))
		noise_gate_settings_sizer.Add(self.noise_gate_threshold_ctrl, 0, wx.ALL, 5)
		noise_sizer.Add(noise_gate_settings_sizer, 0, wx.EXPAND)
		self.customEffectControls.append(self.noise_gate_threshold_ctrl)
		self.bind_effect_toggle(self.noise_gate_checkbox, noise_gate_settings_sizer)

		# 2. Time & Pitch
		timePitch_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Time && Pitch"))
		timePitchBox = timePitch_sizer.GetStaticBox()
		self.reversal_checkbox = wx.CheckBox(timePitchBox, label=_("Reverse Audio"))
		timePitch_sizer.Add(self.reversal_checkbox, 0, wx.ALL, 5)
		self.customEffectControls.append(self.reversal_checkbox)
		self.tempo_checkbox = wx.CheckBox(timePitchBox, label=_("Adjust Tempo"))
		timePitch_sizer.Add(self.tempo_checkbox, 0, wx.ALL, 5)
		self.customEffectControls.append(self.tempo_checkbox)
		tempo_settings_sizer = wx.BoxSizer(wx.HORIZONTAL)
		tempo_settings_sizer.Add(wx.StaticText(timePitchBox, label=_("Tempo Factor (0.5-2.0):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.tempo_ctrl = wx.SpinCtrlDouble(timePitchBox, min=0.5, max=2.0, initial=1.0, inc=0.05)
		self.tempo_ctrl.SetDigits(2)
		self.tempo_ctrl.SetName(_("Tempo Factor"))
		tempo_settings_sizer.Add(self.tempo_ctrl, 0, wx.ALL, 5)
		timePitch_sizer.Add(tempo_settings_sizer, 0, wx.EXPAND)
		main_sizer.Add(timePitch_sizer, 0, wx.EXPAND | wx.ALL, 5)
		self.customEffectControls.append(self.tempo_ctrl)
		self.bind_effect_toggle(self.tempo_checkbox, tempo_settings_sizer)

		# 3. Equalization & Tone
		eq_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Equalization"))
		eqBox = eq_sizer.GetStaticBox()
		self.eq_checkbox = wx.CheckBox(eqBox, label=_("Enable Bass / Treble Equalization"))
		eq_sizer.Add(self.eq_checkbox, 0, wx.ALL, 5)
		self.customEffectControls.append(self.eq_checkbox)
		eq_settings_sizer = wx.BoxSizer(wx.VERTICAL)
		bass_row_sizer = wx.BoxSizer(wx.HORIZONTAL)
		bass_row_sizer.Add(wx.StaticText(eqBox, label=_("Bass Gain (dB):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.bass_gain_ctrl = self.make_stepped_spin(eqBox, -20, 20, 0)
		self.bass_gain_ctrl.SetName(_("Bass Gain"))
		bass_row_sizer.Add(self.bass_gain_ctrl, 0, wx.ALL, 5)
		eq_settings_sizer.Add(bass_row_sizer, 0, wx.EXPAND)
		treble_row_sizer = wx.BoxSizer(wx.HORIZONTAL)
		treble_row_sizer.Add(wx.StaticText(eqBox, label=_("Treble Gain (dB):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.treble_gain_ctrl = self.make_stepped_spin(eqBox, -20, 20, 0)
		self.treble_gain_ctrl.SetName(_("Treble Gain"))
		treble_row_sizer.Add(self.treble_gain_ctrl, 0, wx.ALL, 5)
		eq_settings_sizer.Add(treble_row_sizer, 0, wx.EXPAND)
		eq_sizer.Add(eq_settings_sizer, 0, wx.EXPAND)
		main_sizer.Add(eq_sizer, 0, wx.EXPAND | wx.ALL, 5)
		self.customEffectControls.extend([self.bass_gain_ctrl, self.treble_gain_ctrl])
		self.bind_effect_toggle(self.eq_checkbox, eq_settings_sizer)

		self.parametric_eq_checkbox = wx.CheckBox(eqBox, label=_("Advanced Parametric Equalizer (3-Band)"))
		eq_sizer.Add(self.parametric_eq_checkbox, 0, wx.ALL, 5)
		self.customEffectControls.append(self.parametric_eq_checkbox)
		parametric_eq_settings_sizer = wx.BoxSizer(wx.VERTICAL)
		peq_low_row = wx.BoxSizer(wx.HORIZONTAL)
		peq_low_row.Add(wx.StaticText(eqBox, label=_("Low Gain @ 100 Hz (dB):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.parametric_eq_low_ctrl = self.make_stepped_spin(eqBox, -20, 20, 0)
		self.parametric_eq_low_ctrl.SetName(_("Parametric EQ Low Gain"))
		peq_low_row.Add(self.parametric_eq_low_ctrl, 0, wx.ALL, 5)
		parametric_eq_settings_sizer.Add(peq_low_row, 0, wx.EXPAND)
		peq_mid_row = wx.BoxSizer(wx.HORIZONTAL)
		peq_mid_row.Add(wx.StaticText(eqBox, label=_("Mid Gain @ 1 kHz (dB):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.parametric_eq_mid_ctrl = self.make_stepped_spin(eqBox, -20, 20, 0)
		self.parametric_eq_mid_ctrl.SetName(_("Parametric EQ Mid Gain"))
		peq_mid_row.Add(self.parametric_eq_mid_ctrl, 0, wx.ALL, 5)
		parametric_eq_settings_sizer.Add(peq_mid_row, 0, wx.EXPAND)
		peq_high_row = wx.BoxSizer(wx.HORIZONTAL)
		peq_high_row.Add(wx.StaticText(eqBox, label=_("High Gain @ 8 kHz (dB):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.parametric_eq_high_ctrl = self.make_stepped_spin(eqBox, -20, 20, 0)
		self.parametric_eq_high_ctrl.SetName(_("Parametric EQ High Gain"))
		peq_high_row.Add(self.parametric_eq_high_ctrl, 0, wx.ALL, 5)
		parametric_eq_settings_sizer.Add(peq_high_row, 0, wx.EXPAND)
		eq_sizer.Add(parametric_eq_settings_sizer, 0, wx.EXPAND)
		self.customEffectControls.extend([self.parametric_eq_low_ctrl, self.parametric_eq_mid_ctrl, self.parametric_eq_high_ctrl])
		self.bind_effect_toggle(self.parametric_eq_checkbox, parametric_eq_settings_sizer)

		# 4. Spatial, Modulation & Panning
		spatial_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Spatial, Modulation && Panning"))
		spatialBox = spatial_sizer.GetStaticBox()
		self.binaural_checkbox = wx.CheckBox(spatialBox, label=_("Binaural Audio (3D Headphone Simulation)"))
		spatial_sizer.Add(self.binaural_checkbox, 0, wx.ALL, 5)
		self.customEffectControls.append(self.binaural_checkbox)
		binaural_settings_sizer = wx.BoxSizer(wx.HORIZONTAL)
		binaural_settings_sizer.Add(wx.StaticText(spatialBox, label=_("Width Intensity:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.binaural_width_ctrl = wx.SpinCtrlDouble(spatialBox, min=1.0, max=5.0, initial=2.5, inc=0.5)
		self.binaural_width_ctrl.SetDigits(1)
		self.binaural_width_ctrl.SetName(_("Binaural Width Intensity"))
		binaural_settings_sizer.Add(self.binaural_width_ctrl, 0, wx.ALL, 5)
		spatial_sizer.Add(binaural_settings_sizer, 0, wx.EXPAND)
		self.customEffectControls.append(self.binaural_width_ctrl)
		self.bind_effect_toggle(self.binaural_checkbox, binaural_settings_sizer)

		self.chorus_checkbox = wx.CheckBox(spatialBox, label=_("Chorus"))
		spatial_sizer.Add(self.chorus_checkbox, 0, wx.ALL, 5)
		self.customEffectControls.append(self.chorus_checkbox)
		chorus_settings_sizer = wx.BoxSizer(wx.HORIZONTAL)
		chorus_settings_sizer.Add(wx.StaticText(spatialBox, label=_("Chorus Delay (ms):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.chorus_delay_ctrl = self.make_stepped_spin(spatialBox, 20, 100, 50)
		self.chorus_delay_ctrl.SetName(_("Chorus Delay"))
		chorus_settings_sizer.Add(self.chorus_delay_ctrl, 0, wx.ALL, 5)
		spatial_sizer.Add(chorus_settings_sizer, 0, wx.EXPAND)
		self.customEffectControls.append(self.chorus_delay_ctrl)
		self.bind_effect_toggle(self.chorus_checkbox, chorus_settings_sizer)

		self.reverb_checkbox = wx.CheckBox(spatialBox, label=_("Reverb / Echo"))
		spatial_sizer.Add(self.reverb_checkbox, 0, wx.ALL, 5)
		self.customEffectControls.append(self.reverb_checkbox)
		echo_settings_sizer = wx.BoxSizer(wx.HORIZONTAL)
		echo_settings_sizer.Add(wx.StaticText(spatialBox, label=_("Echo Delay (ms):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.echo_delay_ctrl = self.make_stepped_spin(spatialBox, 50, 1000, 150)
		self.echo_delay_ctrl.SetName(_("Echo Delay"))
		echo_settings_sizer.Add(self.echo_delay_ctrl, 0, wx.ALL, 5)
		spatial_sizer.Add(echo_settings_sizer, 0, wx.EXPAND)
		self.customEffectControls.append(self.echo_delay_ctrl)
		self.bind_effect_toggle(self.reverb_checkbox, echo_settings_sizer)

		self.muffled_room_checkbox = wx.CheckBox(spatialBox, label=_("Muffled Room (Pub / Bathroom)"))
		spatial_sizer.Add(self.muffled_room_checkbox, 0, wx.ALL, 5)
		self.customEffectControls.append(self.muffled_room_checkbox)
		muffled_settings_sizer = wx.BoxSizer(wx.HORIZONTAL)
		muffled_settings_sizer.Add(wx.StaticText(spatialBox, label=_("Muffle Cutoff (Hz):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.muffle_cutoff_ctrl = wx.SpinCtrlDouble(spatialBox, min=200, max=4000, initial=1200, inc=100)
		self.muffle_cutoff_ctrl.SetDigits(0)
		self.muffle_cutoff_ctrl.SetName(_("Muffle Cutoff"))
		muffled_settings_sizer.Add(self.muffle_cutoff_ctrl, 0, wx.ALL, 5)
		spatial_sizer.Add(muffled_settings_sizer, 0, wx.EXPAND)
		self.customEffectControls.append(self.muffle_cutoff_ctrl)
		self.bind_effect_toggle(self.muffled_room_checkbox, muffled_settings_sizer)

		self.autopan_loop_checkbox = wx.CheckBox(spatialBox, label=_("Auto-Pan Loop (Circular, Dance Style)"))
		spatial_sizer.Add(self.autopan_loop_checkbox, 0, wx.ALL, 5)
		self.customEffectControls.append(self.autopan_loop_checkbox)
		autopan_settings_sizer = wx.BoxSizer(wx.VERTICAL)
		autopan_speed_row = wx.BoxSizer(wx.HORIZONTAL)
		autopan_speed_row.Add(wx.StaticText(spatialBox, label=_("Loop Speed (Hz):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.autopan_speed_ctrl = wx.SpinCtrlDouble(spatialBox, min=0.1, max=5.0, initial=0.5, inc=0.1)
		self.autopan_speed_ctrl.SetDigits(1)
		self.autopan_speed_ctrl.SetName(_("Auto-Pan Loop Speed"))
		autopan_speed_row.Add(self.autopan_speed_ctrl, 0, wx.ALL, 5)
		autopan_settings_sizer.Add(autopan_speed_row, 0, wx.EXPAND)
		autopan_direction_row = wx.BoxSizer(wx.HORIZONTAL)
		autopan_direction_row.Add(wx.StaticText(spatialBox, label=_("Loop Direction:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.autopan_direction_combo = wx.ComboBox(
			spatialBox,
			choices=[_("Left to Right"), _("Right to Left")],
			style=wx.CB_READONLY,
		)
		self.autopan_direction_combo.SetName(_("Auto-Pan Loop Direction"))
		self.autopan_direction_combo.SetSelection(0)
		autopan_direction_row.Add(self.autopan_direction_combo, 0, wx.ALL, 5)
		autopan_settings_sizer.Add(autopan_direction_row, 0, wx.EXPAND)
		spatial_sizer.Add(autopan_settings_sizer, 0, wx.EXPAND)
		self.customEffectControls.extend([self.autopan_speed_ctrl, self.autopan_direction_combo])
		self.bind_effect_toggle(self.autopan_loop_checkbox, autopan_settings_sizer)

		self.panning_checkbox = wx.CheckBox(spatialBox, label=_("Advanced Time-Based Panning"))
		spatial_sizer.Add(self.panning_checkbox, 0, wx.ALL, 5)
		self.customEffectControls.append(self.panning_checkbox)
		panning_settings_sizer = wx.BoxSizer(wx.VERTICAL)
		section_count_row = wx.BoxSizer(wx.HORIZONTAL)
		section_count_row.Add(wx.StaticText(spatialBox, label=_("Number of Sections:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.pan_section_count_ctrl = wx.SpinCtrlDouble(spatialBox, min=1, max=50, initial=1, inc=1)
		self.pan_section_count_ctrl.SetDigits(0)
		self.pan_section_count_ctrl.SetName(_("Number of Sections"))
		self.pan_section_count_ctrl.Bind(wx.EVT_SPINCTRLDOUBLE, self.on_pan_section_count_changed)
		section_count_row.Add(self.pan_section_count_ctrl, 0, wx.ALL, 5)
		panning_settings_sizer.Add(section_count_row, 0, wx.EXPAND)
		self.pan_sections_container = wx.BoxSizer(wx.VERTICAL)
		panning_settings_sizer.Add(self.pan_sections_container, 0, wx.EXPAND)
		spatial_sizer.Add(panning_settings_sizer, 0, wx.EXPAND | wx.ALL, 5)
		main_sizer.Add(spatial_sizer, 0, wx.EXPAND | wx.ALL, 5)
		self.spatialBox = spatialBox
		self.pan_section_widgets = []
		self.rebuild_pan_sections(1)
		self.customEffectControls.append(self.pan_section_count_ctrl)
		self.bind_effect_toggle(self.panning_checkbox, panning_settings_sizer)

		# 5. Dynamics & Limiter
		dynamics_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Dynamics && Limiter"))
		dynamicsBox = dynamics_sizer.GetStaticBox()
		self.compressor_checkbox = wx.CheckBox(dynamicsBox, label=_("Apply Compressor"))
		dynamics_sizer.Add(self.compressor_checkbox, 0, wx.ALL, 5)
		self.customEffectControls.append(self.compressor_checkbox)
		compressor_settings_sizer = wx.BoxSizer(wx.HORIZONTAL)
		compressor_settings_sizer.Add(wx.StaticText(dynamicsBox, label=_("Compressor Threshold (dB):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.compressor_threshold_ctrl = self.make_stepped_spin(dynamicsBox, -40, 0, -20)
		self.compressor_threshold_ctrl.SetName(_("Compressor Threshold"))
		compressor_settings_sizer.Add(self.compressor_threshold_ctrl, 0, wx.ALL, 5)
		dynamics_sizer.Add(compressor_settings_sizer, 0, wx.EXPAND)
		self.customEffectControls.append(self.compressor_threshold_ctrl)
		self.bind_effect_toggle(self.compressor_checkbox, compressor_settings_sizer)

		self.loudnorm_checkbox = wx.CheckBox(dynamicsBox, label=_("Loudness Normalization (EBU R128)"))
		dynamics_sizer.Add(self.loudnorm_checkbox, 0, wx.ALL, 5)
		self.customEffectControls.append(self.loudnorm_checkbox)
		loudnorm_settings_sizer = wx.BoxSizer(wx.VERTICAL)
		loudnorm_target_row = wx.BoxSizer(wx.HORIZONTAL)
		loudnorm_target_row.Add(wx.StaticText(dynamicsBox, label=_("Target Loudness (LUFS):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.loudnorm_target_ctrl = self.make_stepped_spin(dynamicsBox, -30, -5, -16, increment=1)
		self.loudnorm_target_ctrl.SetName(_("Loudness Normalization Target"))
		loudnorm_target_row.Add(self.loudnorm_target_ctrl, 0, wx.ALL, 5)
		loudnorm_settings_sizer.Add(loudnorm_target_row, 0, wx.EXPAND)
		loudnorm_peak_row = wx.BoxSizer(wx.HORIZONTAL)
		loudnorm_peak_row.Add(wx.StaticText(dynamicsBox, label=_("True Peak Limit (dBTP):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.loudnorm_truepeak_ctrl = wx.SpinCtrlDouble(dynamicsBox, min=-3.0, max=0.0, initial=-1.5, inc=0.5)
		self.loudnorm_truepeak_ctrl.SetDigits(1)
		self.loudnorm_truepeak_ctrl.SetName(_("Loudness Normalization True Peak"))
		loudnorm_peak_row.Add(self.loudnorm_truepeak_ctrl, 0, wx.ALL, 5)
		loudnorm_settings_sizer.Add(loudnorm_peak_row, 0, wx.EXPAND)
		dynamics_sizer.Add(loudnorm_settings_sizer, 0, wx.EXPAND)
		self.customEffectControls.extend([self.loudnorm_target_ctrl, self.loudnorm_truepeak_ctrl])
		self.bind_effect_toggle(self.loudnorm_checkbox, loudnorm_settings_sizer)

		self.limiter_checkbox = wx.CheckBox(dynamicsBox, label=_("Apply Limiter"))
		dynamics_sizer.Add(self.limiter_checkbox, 0, wx.ALL, 5)
		self.customEffectControls.append(self.limiter_checkbox)
		limiter_settings_sizer = wx.BoxSizer(wx.HORIZONTAL)
		limiter_settings_sizer.Add(wx.StaticText(dynamicsBox, label=_("Limit (dB):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
		self.limiter_limit_ctrl = self.make_stepped_spin(dynamicsBox, -20, 0, -1)
		self.limiter_limit_ctrl.SetName(_("Limiter Limit"))
		limiter_settings_sizer.Add(self.limiter_limit_ctrl, 0, wx.ALL, 5)
		dynamics_sizer.Add(limiter_settings_sizer, 0, wx.EXPAND)
		self.customEffectControls.append(self.limiter_limit_ctrl)
		self.bind_effect_toggle(self.limiter_checkbox, limiter_settings_sizer)

		main_sizer.Add(dynamics_sizer, 0, wx.EXPAND | wx.ALL, 5)

		self.progress_bar = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
		main_sizer.Add(self.progress_bar, 0, wx.EXPAND | wx.ALL, 5)
		self.status_label = wx.StaticText(self, label="")
		main_sizer.Add(self.status_label, 0, wx.EXPAND | wx.ALL, 5)

		btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
		self.preview_btn = wx.Button(self, label=_("Preview (15s)"))
		self.preview_btn.Bind(wx.EVT_BUTTON, self.on_preview)
		btn_sizer.Add(self.preview_btn, 0, wx.ALL, 5)
		self.apply_btn = wx.Button(self, label=_("Apply"))
		self.apply_btn.Bind(wx.EVT_BUTTON, self.on_apply)
		btn_sizer.Add(self.apply_btn, 0, wx.ALL, 5)
		self.close_btn = wx.Button(self, wx.ID_CANCEL, label=_("Close"))
		self.close_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
		btn_sizer.Add(self.close_btn, 0, wx.ALL, 5)
		main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

		self.SetSizer(main_sizer)
		self.SetSize((650, 820))

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
				self.SetTitle(_("MP3 WAV Mixdown: {} files").format(len(self.selected_files)))
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
				self.SetTitle(_("MP3 WAV Mixdown: {} files").format(len(self.selected_files)))
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
		menu = wx.Menu()
		deleteItem = menu.Append(wx.ID_ANY, _("Delete"))
		self.Bind(wx.EVT_MENU, lambda e: self.remove_selected_files(), deleteItem)
		self.file_listbox.PopupMenu(menu)
		menu.Destroy()

	def remove_selected_files(self):
		"""Remove the selected entries from the import list only -- this never touches anything on disk, whether the entry came from an individual file pick or was expanded out of a folder import."""
		if self.currently_processing:
			ui.message(_("MP3 WAV Mixdown is already processing files"))
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
		self.SetTitle(_("MP3 WAV Mixdown: {} files").format(len(self.selected_files)))
		if removedCount:
			ui.message(_("Removed {} file(s) from the list").format(removedCount))
		wx.CallAfter(self.file_listbox.SetFocus)

	def on_preset_selected(self, event):
		presetIndex = self.preset_combo.GetSelection()
		isCustom = presetIndex == 0
		isEqualizer = presetIndex == 1
		for ctrl in self.customEffectControls:
			ctrl.Enable(isCustom)
		self.set_pan_section_widgets_enabled(isCustom)
		self.genre_combo.Enable(isEqualizer)
		self.genre_sizer.ShowItems(show=isEqualizer)
		if isCustom:
			self.preset_description_label.SetLabel("")
		elif isEqualizer:
			self.preset_description_label.SetLabel(_("Applies a genre-tuned EQ curve for the selected music style."))
		else:
			self.preset_description_label.SetLabel(MIXDOWN_PRESETS[presetIndex - 2]["description"])
		self.Layout()
		event.Skip()

	def resolve_processing_plan(self, effectSettings):
		"""Return either ("simple", filterGraphString) for the plain -af chain, or ("pan_multi", preChainStr, postChainStr, activeSections) when at least one panning section needs the -filter_complex graph."""
		presetIndex = self.preset_combo.GetSelection()
		if presetIndex == 1:
			genreIndex = self.genre_combo.GetSelection()
			if 0 <= genreIndex < len(GENRE_EQ_PRESETS):
				return ("simple", GENRE_EQ_PRESETS[genreIndex]["filterChain"])
			return ("simple", "")
		if presetIndex > 1:
			return ("simple", MIXDOWN_PRESETS[presetIndex - 2]["filterChain"])

		activeSections = get_active_pan_sections(effectSettings.get("panSections")) if effectSettings.get("panningEnabled") else []
		if activeSections:
			preChainStr = ",".join(build_pre_panning_stages(effectSettings))
			postChainStr = ",".join(build_post_panning_stages(effectSettings))
			return ("pan_multi", preChainStr, postChainStr, activeSections)

		return ("simple", build_filter_graph(effectSettings))

	def resolve_output_tag(self):
		"""Short tag naming the output file after whatever processing was actually used (e.g. "_pop", "_binaural"), instead of a generic "_mixdown" that collides across different presets applied to the same source file."""
		presetIndex = self.preset_combo.GetSelection()
		if presetIndex == 0:
			return "custom"
		if presetIndex == 1:
			genreIndex = self.genre_combo.GetSelection()
			if 0 <= genreIndex < len(GENRE_EQ_PRESETS):
				return GENRE_EQ_PRESETS[genreIndex]["shortTag"]
			return "eq"
		mixdownIndex = presetIndex - 2
		if 0 <= mixdownIndex < len(MIXDOWN_PRESETS):
			return MIXDOWN_PRESETS[mixdownIndex]["shortTag"]
		return "mixdown"

	def get_effect_settings(self):
		panSections = []
		for widgets in self.pan_section_widgets:
			directionIndex = widgets["direction_combo"].GetSelection()
			panSections.append({
				"startSec": self._parse_float_field(widgets["start_ctrl"], 0.0),
				"endSec": self._parse_float_field(widgets["end_ctrl"], 0.0),
				"direction": PAN_DIRECTION_KEYS[directionIndex] if 0 <= directionIndex < len(PAN_DIRECTION_KEYS) else "center",
				"amountPercent": int(widgets["amount_ctrl"].GetValue()),
			})
		return {
			"noiseReductionEnabled": self.noise_reduction_checkbox.GetValue(),
			"noiseReductionLevel": int(self.noise_level_ctrl.GetValue()),
			"noiseGateEnabled": self.noise_gate_checkbox.GetValue(),
			"noiseGateThresholdDb": int(self.noise_gate_threshold_ctrl.GetValue()),
			"reversalEnabled": self.reversal_checkbox.GetValue(),
			"tempoEnabled": self.tempo_checkbox.GetValue(),
			"tempoFactor": self.tempo_ctrl.GetValue(),
			"eqEnabled": self.eq_checkbox.GetValue(),
			"bassGain": int(self.bass_gain_ctrl.GetValue()),
			"trebleGain": int(self.treble_gain_ctrl.GetValue()),
			"parametricEqEnabled": self.parametric_eq_checkbox.GetValue(),
			"parametricEqLowGain": int(self.parametric_eq_low_ctrl.GetValue()),
			"parametricEqMidGain": int(self.parametric_eq_mid_ctrl.GetValue()),
			"parametricEqHighGain": int(self.parametric_eq_high_ctrl.GetValue()),
			"binauralEnabled": self.binaural_checkbox.GetValue(),
			"binauralWidth": self.binaural_width_ctrl.GetValue(),
			"chorusEnabled": self.chorus_checkbox.GetValue(),
			"chorusDelayMs": int(self.chorus_delay_ctrl.GetValue()),
			"reverbEnabled": self.reverb_checkbox.GetValue(),
			"echoDelayMs": int(self.echo_delay_ctrl.GetValue()),
			"muffledRoomEnabled": self.muffled_room_checkbox.GetValue(),
			"muffleCutoffHz": int(self.muffle_cutoff_ctrl.GetValue()),
			"autoPanLoopEnabled": self.autopan_loop_checkbox.GetValue(),
			"autoPanSpeedHz": self.autopan_speed_ctrl.GetValue(),
			"autoPanDirection": "right_to_left" if self.autopan_direction_combo.GetSelection() == 1 else "left_to_right",
			"panningEnabled": self.panning_checkbox.GetValue(),
			"panSections": panSections,
			"compressorEnabled": self.compressor_checkbox.GetValue(),
			"compressorThresholdDb": int(self.compressor_threshold_ctrl.GetValue()),
			"loudnormEnabled": self.loudnorm_checkbox.GetValue(),
			"loudnormTargetLufs": int(self.loudnorm_target_ctrl.GetValue()),
			"loudnormTruePeakDb": self.loudnorm_truepeak_ctrl.GetValue(),
			"limiterEnabled": self.limiter_checkbox.GetValue(),
			"limiterLimitDb": int(self.limiter_limit_ctrl.GetValue()),
		}

	def set_pan_section_widgets_enabled(self, isEnabled):
		for widgets in self.pan_section_widgets:
			widgets["start_ctrl"].Enable(isEnabled)
			widgets["end_ctrl"].Enable(isEnabled)
			widgets["direction_combo"].Enable(isEnabled)
			widgets["amount_ctrl"].Enable(isEnabled)

	def _parse_float_field(self, textCtrl, fallbackValue):
		rawText = textCtrl.GetValue().strip()
		if not rawText:
			return fallbackValue
		try:
			return float(rawText)
		except ValueError:
			log.error(f"MP3 WAV Mixdown could not parse '{rawText}' as a number, using {fallbackValue}")
			return fallbackValue

	def set_ui_processing_state(self, isProcessing):
		self.browse_btn.Enable(not isProcessing)
		self.browse_folder_btn.Enable(not isProcessing)
		self.preset_combo.Enable(not isProcessing)
		self.preview_btn.Enable(not isProcessing)
		self.apply_btn.Enable(not isProcessing)
		if isProcessing:
			for ctrl in self.customEffectControls:
				ctrl.Enable(False)
			self.genre_combo.Enable(False)
			self.set_pan_section_widgets_enabled(False)
		else:
			presetIndex = self.preset_combo.GetSelection()
			if presetIndex == 0:
				for ctrl in self.customEffectControls:
					ctrl.Enable(True)
				self.set_pan_section_widgets_enabled(True)
			self.genre_combo.Enable(presetIndex == 1)
		self.progress_bar.SetValue(0)

	def do_process_file(self, file_path, plan, outputTag="mixdown", isPreview=False):
		"""Run ffmpeg with the resolved plan. A plain filter chain (or none at all) uses -af / stream copy as before; a pan sweep needs -filter_complex with an explicit -map. Output always matches the source's own extension (.mp3 stays .mp3, .wav stays .wav) so a no-filter stream copy is always valid, and a filtered re-encode uses the matching codec for that container."""
		sourceExt = os.path.splitext(file_path)[1].lower()
		if isPreview:
			outputPath = os.path.join(tempfile.gettempdir(), f"xtrack_mp3_wav_mixdown_preview{sourceExt}")
		else:
			outputDir = os.path.dirname(file_path)
			baseName = f"{os.path.splitext(os.path.basename(file_path))[0]}_{outputTag}"
			outputPath = os.path.join(outputDir, get_unique_filename(outputDir, baseName, sourceExt.lstrip(".")))

		cmd = [self.ffmpeg_exe, "-i", file_path]
		if isPreview:
			cmd += ["-t", str(self.PREVIEW_SECONDS)]

		planType = plan[0]
		hasFilters = False
		if planType == "pan_multi":
			_planType, preChainStr, postChainStr, activeSections = plan
			filterComplexStr, outputLabel = build_pan_multi_filter_complex(preChainStr, postChainStr, activeSections)
			hasFilters = True
			cmd += ["-filter_complex", filterComplexStr, "-map", f"[{outputLabel}]"]
		else:
			filterGraph = plan[1]
			if filterGraph:
				hasFilters = True
				cmd += ["-af", filterGraph]

		if hasFilters:
			if sourceExt == ".wav":
				cmd += ["-c:a", "pcm_s16le"]
			else:
				bitrateKbps = get_audio_bitrate_kbps(self.ffprobe_exe, file_path)
				cmd += ["-c:a", "libmp3lame", "-b:a", f"{bitrateKbps}k"]
		else:
			cmd += ["-c:a", "copy"]

		cmd += ["-y", outputPath]

		if isPreview:
			timeoutSeconds = 60
		else:
			durationSeconds, _durationLabel = get_file_duration(self.libs_path, file_path)
			timeoutSeconds = max(180, int(durationSeconds * 3) + 60) if durationSeconds else 300

		try:
			result = subprocess.run(
				cmd,
				stdout=subprocess.PIPE,
				stderr=subprocess.PIPE,
				creationflags=subprocess.CREATE_NO_WINDOW,
				text=True,
				encoding='utf-8',
				errors='ignore',
				timeout=timeoutSeconds,
			)
			if result.returncode != 0 or not os.path.exists(outputPath):
				log.error(f"MP3 WAV Mixdown ffmpeg failed for {file_path}: {result.stderr}")
				wx.CallAfter(ui.message, _("Failed to process {}").format(os.path.basename(file_path)))
				if os.path.exists(outputPath):
					os.remove(outputPath)
				return None
			return outputPath
		except subprocess.TimeoutExpired:
			log.error(f"MP3 WAV Mixdown ffmpeg timed out for {file_path}")
			wx.CallAfter(ui.message, _("Timed out processing {}").format(os.path.basename(file_path)))
			if os.path.exists(outputPath):
				os.remove(outputPath)
			return None

	def get_preview_source_file(self):
		selectedIndices = self.file_listbox.GetSelections()
		if selectedIndices:
			return self.selected_files[selectedIndices[0]]
		return self.selected_files[0]

	def on_preview(self, event):
		if not self.selected_files:
			ui.message(_("Please select at least one MP3 or WAV file first."))
			return
		if self.currently_processing:
			ui.message(_("MP3 WAV Mixdown is already processing files"))
			return
		previewSourceFile = self.get_preview_source_file()
		plan = self.resolve_processing_plan(self.get_effect_settings())
		self.currently_processing = True
		self.set_ui_processing_state(True)
		self.status_label.SetLabel(_("Generating preview: {}").format(os.path.basename(previewSourceFile)))
		threading.Thread(target=self.run_preview_job, args=(previewSourceFile, plan), daemon=True).start()

	def run_preview_job(self, file_path, plan):
		outputPath = self.do_process_file(file_path, plan, isPreview=True)
		if outputPath:
			try:
				os.startfile(outputPath)
				wx.CallAfter(ui.message, _("Playing {}-second preview").format(self.PREVIEW_SECONDS))
			except OSError as e:
				log.error(f"MP3 WAV Mixdown failed to launch preview player: {e}")
				wx.CallAfter(ui.message, _("Preview created but could not be opened automatically: {}").format(outputPath))
		self.currently_processing = False
		wx.CallAfter(self.set_ui_processing_state, False)
		wx.CallAfter(self.status_label.SetLabel, _("Ready"))

	def on_apply(self, event):
		if not self.selected_files:
			ui.message(_("Please select at least one MP3 or WAV file first."))
			return
		if self.currently_processing:
			ui.message(_("MP3 WAV Mixdown is already processing files"))
			return
		self.current_processing_plan = self.resolve_processing_plan(self.get_effect_settings())
		self.current_output_tag = self.resolve_output_tag()
		self.processed_count = 0
		for file_path in self.selected_files:
			self.processing_queue.put(file_path)
		self.currently_processing = True
		self.set_ui_processing_state(True)
		self.process_next_file()

	def process_next_file(self):
		if self.processing_queue.empty():
			self.currently_processing = False
			wx.CallAfter(self.on_batch_complete)
			return
		file_path = self.processing_queue.get()
		wx.CallAfter(self.status_label.SetLabel, _("Processing: {}").format(os.path.basename(file_path)))
		threading.Thread(target=self.run_single_file_job, args=(file_path,), daemon=True).start()

	def run_single_file_job(self, file_path):
		try:
			outputPath = self.do_process_file(file_path, self.current_processing_plan, outputTag=self.current_output_tag, isPreview=False)
			if outputPath:
				wx.CallAfter(ui.message, _("Created {}").format(os.path.basename(outputPath)))
		except (OSError, subprocess.SubprocessError) as e:
			log.error(f"MP3 WAV Mixdown error processing {file_path}: {e}")
			wx.CallAfter(ui.message, _("Failed to process {}").format(os.path.basename(file_path)))
		finally:
			wx.CallAfter(self.advance_after_file)

	def advance_after_file(self):
		self.processed_count += 1
		total = max(len(self.selected_files), 1)
		progress = int((self.processed_count / total) * 100)
		self.progress_bar.SetValue(min(progress, 100))
		self.process_next_file()

	def on_batch_complete(self):
		self.set_ui_processing_state(False)
		self.status_label.SetLabel(_("Done"))
		try:
			tones.beep(1000, 200)
		except Exception:
			pass
		ui.message(_("MP3 WAV Mixdown processing complete"))

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
