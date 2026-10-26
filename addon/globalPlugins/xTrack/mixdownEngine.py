# mixdownEngine.py

import json
import math
import re
from collections import OrderedDict, namedtuple

# Plain-Python logic of the MP3 WAV Mixdown dialog: settings, filter chains,
# graphs, measurements. Nothing here touches wx or NVDA, so all of it can be
# run and checked outside NVDA.

# Tempo: slows down or speeds up the music without changing its pitch (key),
# within the range one atempo filter handles well.
MIN_TEMPO_FACTOR = 0.5
MAX_TEMPO_FACTOR = 2.0
TEMPO_STEP = 0.05
# Fast Speed: a separate effect for speeding a file up well beyond the tempo range.
MIN_FAST_SPEED_FACTOR = 1.0
MAX_FAST_SPEED_FACTOR = 10.0
DEFAULT_FAST_SPEED_FACTOR = 2.0
FAST_SPEED_STEP = 0.25
# Sample rate used when the pitch has to follow the speed: the audio is brought
# to this rate first so the change is exact whatever the source rate was.
FAST_SPEED_BASE_RATE = 44100
# areverse keeps the whole decoded file in memory (about 21 MB per minute of
# stereo audio), so rewinding is limited to files of a sensible length.
REVERSE_MAX_SECONDS = 20 * 60
ATEMPO_PATTERN = re.compile(r"atempo=([0-9]+(?:\.[0-9]+)?)")
ASETRATE_PATTERN = re.compile(r"asetrate=(\d+)")

PAN_DIRECTION_KEYS = ["center", "left_to_right", "right_to_left", "full_left", "full_right"]
AUTOPAN_DIRECTION_KEYS = ["left_to_right", "right_to_left"]
MAX_SECTIONS = 50

LOUDNORM_SLOT = "@LOUDNORM@"
LOUDNORM_LRA = 11

Param = namedtuple("Param", "kind default minimum maximum step digits choices")


def _bool(default=False):
	return Param("bool", default, None, None, None, None, None)


def _int(default, minimum, maximum, step=1):
	return Param("int", default, minimum, maximum, step, 0, None)


def _float(default, minimum, maximum, step=0.1, digits=1):
	return Param("float", default, minimum, maximum, step, digits, None)


def _choice(default, keys):
	return Param("choice", default, None, None, None, None, tuple(keys))


PARAMS = OrderedDict([
	# Noise
	("noiseReductionEnabled", _bool()),
	("noiseReductionLevel", _int(-25, -80, -20, 5)),
	("noiseGateEnabled", _bool()),
	("noiseGateThresholdDb", _int(-40, -60, -10, 5)),
	("rumbleEnabled", _bool()),
	("rumbleCutoffHz", _int(80, 20, 300, 10)),
	("clickRemoverEnabled", _bool()),
	("clickRemoverMode", _choice("clicks", ("clicks", "clipping", "both"))),
	("clickRemoverThreshold", _int(2, 1, 50, 1)),
	("deesserEnabled", _bool()),
	("deesserIntensity", _int(50, 0, 100, 5)),
	("deesserFrequency", _int(50, 0, 100, 5)),
	("silenceTrimEnabled", _bool()),
	("silenceTrimMode", _choice("start", ("start", "gaps"))),
	("silenceTrimThresholdDb", _int(-50, -90, -20, 5)),
	("silenceTrimMinSeconds", _float(1.0, 0.2, 10.0, 0.1, 1)),
	# Tempo and speed
	("reversalEnabled", _bool()),
	("tempoEnabled", _bool()),
	("tempoFactor", _float(1.0, MIN_TEMPO_FACTOR, MAX_TEMPO_FACTOR, TEMPO_STEP, 2)),
	("tempoKeepPitch", _bool(True)),
	("fastSpeedEnabled", _bool()),
	("fastSpeedFactor", _float(DEFAULT_FAST_SPEED_FACTOR, MIN_FAST_SPEED_FACTOR, MAX_FAST_SPEED_FACTOR, FAST_SPEED_STEP, 2)),
	("nightcoreEnabled", _bool()),
	("nightcoreMode", _choice("nightcore", ("nightcore", "slowed"))),
	("nightcoreAmountPercent", _int(20, 5, 50, 5)),
	# Equalizer and tone
	("eqEnabled", _bool()),
	("bassGain", _int(0, -20, 20, 5)),
	("trebleGain", _int(0, -20, 20, 5)),
	("parametricEqEnabled", _bool()),
	("parametricEqLowGain", _int(0, -20, 20, 5)),
	("parametricEqMidGain", _int(0, -20, 20, 5)),
	("parametricEqHighGain", _int(0, -20, 20, 5)),
	("voiceClarityEnabled", _bool()),
	("voiceClarityAmount", _int(5, 1, 10, 1)),
	("loudnessContourEnabled", _bool()),
	("loudnessContourAmount", _int(5, 1, 10, 1)),
	("tiltEnabled", _bool()),
	("tiltDb", _int(3, -10, 10, 1)),
	# Spatial and panning
	("channelToolEnabled", _bool()),
	("channelTool", _choice("stereo_to_mono", ("stereo_to_mono", "mono_to_stereo", "swap"))),
	("binauralEnabled", _bool()),
	("binauralWidth", _float(2.5, 1.0, 5.0, 0.5, 1)),
	("chorusEnabled", _bool()),
	("chorusDelayMs", _int(50, 20, 100, 5)),
	("modulationEnabled", _bool()),
	("modulationType", _choice("flanger", ("flanger", "phaser", "tremolo", "vibrato"))),
	("modulationRateHz", _float(1.0, 0.1, 10.0, 0.1, 1)),
	("modulationDepthPercent", _int(50, 10, 100, 5)),
	("haasEnabled", _bool()),
	("haasDelayMs", _int(15, 1, 40, 1)),
	("haasDelayedSide", _choice("right", ("right", "left"))),
	("freqShiftEnabled", _bool()),
	("freqShiftHz", _int(100, -1000, 1000, 10)),
	("reverbEnabled", _bool()),
	("echoDelayMs", _int(150, 50, 1000, 5)),
	("roomReverbEnabled", _bool()),
	("roomType", _choice("medium", ("small", "medium", "hall", "cathedral"))),
	("roomReverbAmount", _int(50, 10, 100, 5)),
	("muffledRoomEnabled", _bool()),
	("muffleCutoffHz", _int(1200, 200, 4000, 100)),
	("effectMixPercent", _int(100, 0, 100, 5)),
	("autoPanLoopEnabled", _bool()),
	("autoPanSpeedHz", _float(0.5, 0.1, 5.0, 0.1, 1)),
	("autoPanDirection", _choice("left_to_right", AUTOPAN_DIRECTION_KEYS)),
	("autoPanKeepVocalCenter", _bool()),
	("autoPanVocalLowHz", _int(120, 80, 400, 10)),
	("autoPanVocalHighHz", _int(5000, 2000, 10000, 500)),
	("panningEnabled", _bool()),
	# Dynamics
	("volumeGainEnabled", _bool()),
	("volumeGainDb", _int(3, -30, 30, 1)),
	("expanderEnabled", _bool()),
	("expanderPreset", _choice("night", ("night", "gentle", "strong"))),
	("dynaudnormEnabled", _bool()),
	("dynaudnormFrameMs", _int(500, 100, 2000, 50)),
	("dynaudnormMaxGain", _int(10, 1, 30, 1)),
	("speechnormEnabled", _bool()),
	("speechnormPeakPercent", _int(95, 10, 100, 5)),
	("speechnormExpansion", _int(2, 1, 20, 1)),
	("compressorEnabled", _bool()),
	("compressorThresholdDb", _int(-20, -40, 0, 5)),
	("compressorRatio", _float(4.0, 1.0, 20.0, 0.5, 1)),
	("compressorAttackMs", _int(5, 1, 500, 1)),
	("compressorReleaseMs", _int(50, 10, 2000, 10)),
	("compressorMakeupDb", _int(0, 0, 24, 1)),
	("compressorKneeDb", _int(9, 0, 18, 1)),
	("softClipEnabled", _bool()),
	("softClipType", _choice("tanh", ("tanh", "atan", "cubic", "exp", "alg", "quintic", "sin", "erf"))),
	("softClipDriveDb", _int(6, 0, 24, 1)),
	("lofiEnabled", _bool()),
	("lofiBits", _int(8, 2, 16, 1)),
	("lofiSampleReduction", _int(4, 1, 40, 1)),
	("loudnormEnabled", _bool()),
	("loudnormTargetLufs", _int(-16, -30, -5, 1)),
	("loudnormTruePeakDb", _float(-1.5, -3.0, 0.0, 0.5, 1)),
	("loudnormTwoPass", _bool()),
	("limiterEnabled", _bool()),
	("limiterLimitDb", _int(-1, -20, 0, 5)),
	# Creative
	("telephoneEnabled", _bool()),
	("telephoneStyle", _choice("telephone", ("telephone", "radio", "walkie"))),
	("subBoostEnabled", _bool()),
	("subBoostAmount", _int(4, 1, 12, 1)),
	("subBoostCutoffHz", _int(100, 50, 300, 10)),
	("enhancerEnabled", _bool()),
	("enhancerIntensity", _float(2.0, 0.0, 10.0, 0.5, 1)),
	# Not an effect: also measure and report loudness before and after.
	("reportLoudness", _bool()),
])

DEFAULT_SETTINGS = OrderedDict((key, param.default) for key, param in PARAMS.items())
DEFAULT_SETTINGS["panSections"] = []
DEFAULT_SETTINGS["autoPanSections"] = []


def _finite_number(value):
	if isinstance(value, bool) or not isinstance(value, (int, float)):
		return None
	if not math.isfinite(value):
		return None
	return float(value)


def _sanitize_value(param, value):
	if param.kind == "bool":
		return value if isinstance(value, bool) else param.default
	if param.kind == "choice":
		return value if value in param.choices else param.default
	number = _finite_number(value)
	if number is None:
		return param.default
	number = max(param.minimum, min(param.maximum, number))
	if param.kind == "int":
		return int(round(number))
	return number


def _sanitize_pan_sections(rawSections):
	sections = []
	for raw in (rawSections if isinstance(rawSections, list) else [])[:MAX_SECTIONS]:
		if not isinstance(raw, dict):
			continue
		startSec = _finite_number(raw.get("startSec"))
		endSec = _finite_number(raw.get("endSec"))
		amount = _finite_number(raw.get("amountPercent"))
		direction = raw.get("direction")
		sections.append({
			"startSec": max(0.0, startSec) if startSec is not None else 0.0,
			"endSec": max(0.0, endSec) if endSec is not None else 0.0,
			"direction": direction if direction in PAN_DIRECTION_KEYS else "center",
			"amountPercent": int(max(0, min(100, amount))) if amount is not None else 100,
		})
	return sections


def _sanitize_autopan_sections(rawSections):
	sections = []
	speedParam = PARAMS["autoPanSpeedHz"]
	for raw in (rawSections if isinstance(rawSections, list) else [])[:MAX_SECTIONS]:
		if not isinstance(raw, dict):
			continue
		startSec = _finite_number(raw.get("startSec"))
		endSec = _finite_number(raw.get("endSec"))
		direction = raw.get("direction")
		sections.append({
			"startSec": max(0.0, startSec) if startSec is not None else 0.0,
			"endSec": max(0.0, endSec) if endSec is not None else 0.0,
			"direction": direction if direction in AUTOPAN_DIRECTION_KEYS else "left_to_right",
			"speedHz": _sanitize_value(speedParam, raw.get("speedHz")),
		})
	return sections


def sanitize_settings(rawSettings):
	"""A complete, range-checked settings dict from anything (a saved preset, a damaged file, a half-filled dict). Every number that reaches a filter string passes through here, so editing a preset file by hand can never put text into an ffmpeg expression."""
	source = rawSettings if isinstance(rawSettings, dict) else {}
	settings = OrderedDict()
	for key, param in PARAMS.items():
		settings[key] = _sanitize_value(param, source.get(key, param.default))
	settings["panSections"] = _sanitize_pan_sections(source.get("panSections"))
	settings["autoPanSections"] = _sanitize_autopan_sections(source.get("autoPanSections"))
	return settings


def normalize_preset_name(rawName):
	"""A user preset name, or None when nothing usable is left."""
	if not isinstance(rawName, str):
		return None
	name = " ".join("".join(ch for ch in rawName if ch.isprintable()).split())
	return name[:60] if name else None


# ---- helpers moved unchanged from the dialog module

def build_tempo_stages(tempoFactor, keepPitch=True):
	"""Stages for the Tempo effect.

	With keepPitch (the original behaviour) the music is slowed down or sped up
	without changing its key. Without it the pitch follows the speed, like a
	record played slower or faster. A tempo factor outside ffmpeg's single-filter
	0.5-2.0 atempo range is broken into a chain of atempo filters.
	"""
	if not keepPitch:
		tapeRate = int(round(FAST_SPEED_BASE_RATE * tempoFactor))
		return [f"aresample={FAST_SPEED_BASE_RATE}", f"asetrate={tapeRate}", f"aresample={FAST_SPEED_BASE_RATE}"]
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


def build_fast_speed_stages(speedFactor):
	"""Stages that play a file speedFactor times faster without changing its key.

	A chain of atempo stages (each limited to a factor of 2) reaches the high
	factors the Tempo effect cannot. This logic is separate from Tempo.
	"""
	stages = []
	remainingFactor = speedFactor
	while remainingFactor > 2.0:
		stages.append("atempo=2.0")
		remainingFactor /= 2.0
	stages.append(f"atempo={remainingFactor:.3f}")
	return stages


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


# ---- Auto-Pan Loop with sections

def _number_text(value):
	text = f"{value:.6f}".rstrip("0").rstrip(".")
	return text if text and text != "-0" else "0"


def build_autopan_segments(baseSpeedHz, baseDirection, sections):
	"""Chronological, non-overlapping (startSec, endSec or None, signedRate) segments covering the whole file. Outside every section the base speed and direction apply; the last segment runs to the end of the file. signedRate is radians per second, negative for Right to Left."""
	def rateFor(direction, speedHz):
		return (-1.0 if direction == "right_to_left" else 1.0) * 2.0 * math.pi * speedHz

	valid = sorted(
		(s for s in (sections or []) if s["endSec"] > s["startSec"]),
		key=lambda s: (s["startSec"], s["endSec"]),
	)
	segments = []
	cursor = 0.0
	for section in valid:
		startSec = max(section["startSec"], cursor)
		endSec = section["endSec"]
		if endSec <= startSec:
			continue
		if startSec > cursor:
			segments.append((cursor, startSec, rateFor(baseDirection, baseSpeedHz)))
		segments.append((startSec, endSec, rateFor(section["direction"], section["speedHz"])))
		cursor = endSec
	segments.append((cursor, None, rateFor(baseDirection, baseSpeedHz)))
	return segments


def build_autopan_phase_expr(baseSpeedHz, baseDirection, sections):
	"""The loop's phase in radians as a function of t. The phase is carried over from one segment to the next, so reversing the direction at a section boundary turns the movement around at exactly the position it had reached, with no jump."""
	segments = build_autopan_segments(baseSpeedHz, baseDirection, sections)
	startPhases = []
	phase = 0.0
	for startSec, endSec, rate in segments:
		startPhases.append(phase)
		if endSec is not None:
			phase += rate * (endSec - startSec)

	def segmentExpr(index):
		startSec, _endSec, rate = segments[index]
		return f"{_number_text(startPhases[index])}+({_number_text(rate)})*(t-{_number_text(startSec)})"

	expr = segmentExpr(len(segments) - 1)
	for index in range(len(segments) - 2, -1, -1):
		expr = f"if(lt(t\\,{_number_text(segments[index][1])})\\,{segmentExpr(index)}\\,{expr})"
	return expr


def build_autopan_gain_exprs(baseSpeedHz, baseDirection, sections):
	"""Left and right gain expressions: the same 0..1 sine pair the plain Auto-Pan Loop uses, so a loop without sections sounds the same either way."""
	phase = build_autopan_phase_expr(baseSpeedHz, baseDirection, sections)
	return f"0.5+0.5*sin({phase})", f"0.5-0.5*sin({phase})"


# ---- stage builders (each returns a list of filter strings, empty when the effect is off)

def _on(settings, key):
	return bool(settings.get(key))


def build_clean_stages(s):
	stages = []
	if _on(s, "rumbleEnabled"):
		stages.append(f"highpass=f={s['rumbleCutoffHz']}")
	if _on(s, "clickRemoverEnabled"):
		mode = s["clickRemoverMode"]
		if mode in ("clicks", "both"):
			stages.append(f"adeclick=t={s['clickRemoverThreshold']}")
		if mode in ("clipping", "both"):
			stages.append("adeclip")
	if _on(s, "noiseReductionEnabled"):
		stages.append(f"afftdn=nf={s['noiseReductionLevel']}")
	if _on(s, "noiseGateEnabled"):
		stages.append(f"agate=threshold={s['noiseGateThresholdDb']}dB:ratio=2:attack=20:release=250")
	if _on(s, "deesserEnabled"):
		stages.append(f"deesser=i={s['deesserIntensity'] / 100:g}:f={s['deesserFrequency'] / 100:g}")
	if _on(s, "silenceTrimEnabled"):
		thresholdDb = s["silenceTrimThresholdDb"]
		stage = f"silenceremove=start_periods=1:start_threshold={thresholdDb}dB:start_silence=0.1"
		if s["silenceTrimMode"] == "gaps":
			minSeconds = s["silenceTrimMinSeconds"]
			stage += f":stop_periods=-1:stop_threshold={thresholdDb}dB:stop_duration={minSeconds:g}:stop_silence={min(0.3, minSeconds / 2):g}"
		stages.append(stage)
	return stages


def build_time_stages(s):
	stages = []
	if _on(s, "reversalEnabled"):
		stages.append("areverse")
	if _on(s, "tempoEnabled"):
		stages.extend(build_tempo_stages(s["tempoFactor"], s["tempoKeepPitch"]))
	if _on(s, "fastSpeedEnabled"):
		stages.extend(build_fast_speed_stages(s["fastSpeedFactor"]))
	if _on(s, "nightcoreEnabled"):
		amount = 1.0 + s["nightcoreAmountPercent"] / 100.0
		factor = amount if s["nightcoreMode"] == "nightcore" else 1.0 / amount
		stages.extend(build_tempo_stages(factor, keepPitch=False))
	return stages


def build_channel_stages(s):
	if not _on(s, "channelToolEnabled"):
		return []
	tool = s["channelTool"]
	if tool == "mono_to_stereo":
		return ["aformat=channel_layouts=stereo"]
	if tool == "swap":
		return ["aformat=channel_layouts=stereo", "pan=stereo|c0=c1|c1=c0"]
	return ["aformat=channel_layouts=stereo", "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1"]


TELEPHONE_CHAINS = {
	"telephone": ["highpass=f=300", "lowpass=f=3400", "acompressor=threshold=-18dB:ratio=3:attack=5:release=60"],
	"radio": ["highpass=f=120", "lowpass=f=5000", "equalizer=f=1500:width_type=q:width=1:g=4"],
	"walkie": ["highpass=f=400", "lowpass=f=2800", "volume=6dB", "asoftclip=type=tanh"],
}


def build_tone_stages(s):
	stages = []
	if _on(s, "eqEnabled"):
		stages.append(f"bass=g={s['bassGain']}:f=100:w=0.5,treble=g={s['trebleGain']}:f=3000:w=0.5")
	if _on(s, "parametricEqEnabled"):
		stages.append(
			f"firequalizer=gain_entry='entry(100\\,{s['parametricEqLowGain']});"
			f"entry(1000\\,{s['parametricEqMidGain']});entry(8000\\,{s['parametricEqHighGain']})'"
		)
	if _on(s, "voiceClarityEnabled"):
		amount = s["voiceClarityAmount"]
		stages.append(
			f"equalizer=f=250:width_type=q:width=1:g={-0.4 * amount:.1f},"
			f"equalizer=f=3000:width_type=q:width=1.2:g={0.8 * amount:.1f},"
			f"equalizer=f=8000:width_type=o:width=1:g={0.4 * amount:.1f}"
		)
	if _on(s, "loudnessContourEnabled"):
		amount = s["loudnessContourAmount"]
		stages.append(
			f"bass=g={1.2 * amount:.1f}:f=100:w=0.6,treble=g={0.8 * amount:.1f}:f=8000:w=0.6,"
			f"equalizer=f=1000:width_type=q:width=0.7:g={-0.3 * amount:.1f}"
		)
	if _on(s, "tiltEnabled"):
		tilt = s["tiltDb"]
		stages.append(f"bass=g={-tilt / 2:.1f}:f=250:w=0.7,treble=g={tilt / 2:.1f}:f=2500:w=0.7")
	if _on(s, "telephoneEnabled"):
		stages.extend(TELEPHONE_CHAINS[s["telephoneStyle"]])
	if _on(s, "subBoostEnabled"):
		stages.append(f"asubboost=boost={s['subBoostAmount']}:cutoff={s['subBoostCutoffHz']}")
	if _on(s, "enhancerEnabled"):
		stages.append(f"crystalizer=i={s['enhancerIntensity']:g}:c=1")
	return stages


def build_binaural_stages(s):
	if not _on(s, "binauralEnabled"):
		return []
	# bs2b requires an ffmpeg build compiled with --enable-libbs2b, which
	# the bundled ffmpeg.exe does not have. crossfeed is a core filter
	# built for exactly this purpose (headphone crossfeed simulation,
	# the same concept bs2b implements) and extrastereo widens the image
	# further -- earwax alone was too subtle to read as "3D".
	return [f"crossfeed=strength=0.3:range=0.9,extrastereo=m={s['binauralWidth']}:c=disabled"]


ROOM_TAPS = {
	"small": ((20, 35, 50), (0.4, 0.3, 0.2)),
	"medium": ((40, 70, 110, 160), (0.4, 0.35, 0.25, 0.2)),
	"hall": ((80, 140, 220, 320, 450), (0.45, 0.4, 0.3, 0.22, 0.15)),
	"cathedral": ((120, 250, 400, 600, 850, 1100), (0.5, 0.45, 0.38, 0.3, 0.22, 0.15)),
}


def build_room_reverb_stage(roomType, amountPercent):
	delays, decays = ROOM_TAPS[roomType]
	scale = amountPercent / 50.0
	delayText = "|".join(str(delay) for delay in delays)
	decayText = "|".join(f"{min(0.9, decay * scale):.2f}" for decay in decays)
	return f"aecho=0.8:0.9:{delayText}:{decayText}"


def build_wet_stages(s):
	"""The effects the Effects Mix (dry/wet) control applies to, in the order they run."""
	stages = []
	if _on(s, "chorusEnabled"):
		stages.append(f"chorus=0.5:0.9:{s['chorusDelayMs']}:0.4:0.25:2")
	if _on(s, "modulationEnabled"):
		kind = s["modulationType"]
		rate = s["modulationRateHz"]
		depth = s["modulationDepthPercent"] / 100.0
		if kind == "flanger":
			stages.append(f"flanger=delay=0:depth={depth * 10:.1f}:speed={rate:g}")
		elif kind == "phaser":
			stages.append(f"aphaser=speed={min(rate, 2.0):g}:decay={depth * 0.9:.2f}")
		elif kind == "tremolo":
			stages.append(f"tremolo=f={rate:g}:d={depth:g}")
		else:
			stages.append(f"vibrato=f={rate:g}:d={depth:g}")
	if _on(s, "haasEnabled"):
		delayMs = s["haasDelayMs"]
		stages.append(f"adelay={delayMs}|0" if s["haasDelayedSide"] == "left" else f"adelay=0|{delayMs}")
	if _on(s, "freqShiftEnabled"):
		stages.append(f"afreqshift=shift={s['freqShiftHz']}")
	if _on(s, "reverbEnabled"):
		stages.append(f"aecho=0.8:0.88:{s['echoDelayMs']}:0.4")
	if _on(s, "roomReverbEnabled"):
		stages.append(build_room_reverb_stage(s["roomType"], s["roomReverbAmount"]))
	if _on(s, "muffledRoomEnabled"):
		stages.append(f"lowpass=f={s['muffleCutoffHz']},bass=g=4:f=150:w=0.6,aecho=0.6:0.7:40|60:0.5|0.3")
	return stages


def autopan_uses_graph(s):
	if not _on(s, "autoPanLoopEnabled"):
		return False
	return bool(s["autoPanSections"]) or _on(s, "autoPanKeepVocalCenter")


def build_autopan_loop_stage(s):
	"""The plain loop: apulsator modulates left/right gain 180 degrees out of phase, giving a continuous circular pan loop across the whole file -- the auto-pan style heard throughout dance tracks, as opposed to Advanced Time-Based Panning's one-shot sweep. Swapping which channel leads (offset_l/offset_r) reverses the perceived direction of the loop."""
	if s["autoPanDirection"] == "right_to_left":
		offsetL, offsetR = 0.5, 0
	else:
		offsetL, offsetR = 0, 0.5
	return f"apulsator=mode=sine:hz={s['autoPanSpeedHz']}:amount=1:offset_l={offsetL}:offset_r={offsetR}"


COMPAND_PRESETS = {
	# Quiet parts come up, loud parts come down: the range between them narrows.
	"night": "compand=attacks=0.05:decays=0.4:points=-90/-90|-60/-48|-40/-30|-20/-16|0/-8:soft-knee=6",
	# Pushes very quiet noise down a little without touching the music.
	"gentle": "compand=attacks=0.02:decays=0.3:points=-90/-110|-60/-68|-45/-45|0/0:soft-knee=6",
	# Close to a gate: everything below about -45 dB is pushed far down.
	"strong": "compand=attacks=0.01:decays=0.2:points=-90/-120|-60/-90|-45/-60|-30/-30|0/0:soft-knee=3",
}


def build_post_stages(s, loudnormStage=None):
	"""Dynamics and limiter, always the final stages. Volume Gain comes first so that everything after it reacts to the gain it applies."""
	stages = []
	if _on(s, "volumeGainEnabled"):
		stages.append(f"volume={s['volumeGainDb']}dB")
	if _on(s, "expanderEnabled"):
		stages.append(COMPAND_PRESETS[s["expanderPreset"]])
	if _on(s, "dynaudnormEnabled"):
		stages.append(f"dynaudnorm=f={s['dynaudnormFrameMs']}:m={s['dynaudnormMaxGain']}")
	if _on(s, "speechnormEnabled"):
		stages.append(f"speechnorm=p={s['speechnormPeakPercent'] / 100:g}:e={s['speechnormExpansion']}")
	if _on(s, "compressorEnabled"):
		stage = (
			f"acompressor=threshold={s['compressorThresholdDb']}dB:ratio={s['compressorRatio']:g}"
			f":attack={s['compressorAttackMs']}:release={s['compressorReleaseMs']}"
		)
		if s["compressorMakeupDb"] > 0:
			stage += f":makeup={min(64.0, 10 ** (s['compressorMakeupDb'] / 20)):.3f}"
		if s["compressorKneeDb"] != 9:
			stage += f":knee={max(1.0, min(8.0, 10 ** (s['compressorKneeDb'] / 20))):.3f}"
		stages.append(stage)
	if _on(s, "softClipEnabled"):
		drive = s["softClipDriveDb"]
		clipStage = f"asoftclip=type={s['softClipType']}"
		if drive > 0:
			stages.extend([f"volume={drive}dB", clipStage, f"volume={-drive / 2:g}dB"])
		else:
			stages.append(clipStage)
	if _on(s, "lofiEnabled"):
		stages.append(f"acrusher=bits={s['lofiBits']}:samples={s['lofiSampleReduction']}:mix=1")
	if _on(s, "loudnormEnabled"):
		stages.append(loudnormStage if loudnormStage is not None else single_pass_loudnorm(s))
	if _on(s, "limiterEnabled"):
		stages.append(f"alimiter=limit={s['limiterLimitDb']}dB")
	return stages


def single_pass_loudnorm(s):
	return f"loudnorm=I={s['loudnormTargetLufs']}:TP={s['loudnormTruePeakDb']:g}:LRA={LOUDNORM_LRA}"


def two_pass_loudnorm(baseStage, measured):
	"""The second loudnorm stage, fed with what the first pass measured, so the result lands on the target instead of near it."""
	return (
		f"{baseStage}:measured_I={measured['input_i']:.2f}:measured_LRA={measured['input_lra']:.2f}"
		f":measured_TP={measured['input_tp']:.2f}:measured_thresh={measured['input_thresh']:.2f}"
		f":offset={measured['target_offset']:.2f}:linear=true"
	)


# ---- plan: one chain, or one filter graph

class MixdownPlan:
	"""What ffmpeg has to do for one run: a plain -af chain, or a -filter_complex graph when some effect needs several streams (dry/wet mixing, per-channel gain, vocal separation)."""

	def __init__(self, chain="", graph="", outputLabel="", loudnormStage="", twoPassLoudnorm=False):
		self.chain = chain
		self.graph = graph
		self.outputLabel = outputLabel
		self.loudnormStage = loudnormStage
		self.twoPassLoudnorm = twoPassLoudnorm

	@classmethod
	def fromChain(cls, chain):
		return cls(chain=chain)

	@property
	def isGraph(self):
		return bool(self.graph)

	@property
	def hasFilters(self):
		return bool(self.chain or self.graph)

	def filterText(self, loudnormStage=None):
		text = self.graph or self.chain
		if LOUDNORM_SLOT in text:
			text = text.replace(LOUDNORM_SLOT, loudnormStage if loudnormStage is not None else self.loudnormStage)
		return text

	def filterArgs(self, loudnormStage=None):
		text = self.filterText(loudnormStage)
		if self.isGraph:
			return ["-filter_complex", text, "-map", f"[{self.outputLabel}]"]
		return ["-af", text] if text else []

	def analysisStage(self):
		return f"{self.loudnormStage}:print_format=json"

	def usesReverse(self):
		return "areverse" in self.filterText()

	def speedFactor(self):
		text = self.filterText()
		factor = 1.0
		for value in ATEMPO_PATTERN.findall(text):
			factor *= float(value)
		for value in ASETRATE_PATTERN.findall(text):
			factor *= int(value) / FAST_SPEED_BASE_RATE
		return factor if factor > 0 else 1.0


class _GraphAssembler:
	def __init__(self):
		self.items = []
		self.counter = 0

	def addChain(self, stages):
		stages = [stage for stage in stages if stage]
		if not stages:
			return
		if self.items and self.items[-1][0] == "chain":
			self.items[-1][1].extend(stages)
		else:
			self.items.append(("chain", list(stages)))

	def addBlock(self, builder):
		self.items.append(("block", builder))

	def isLinear(self):
		return all(kind == "chain" for kind, _payload in self.items)

	def linearChain(self):
		return ",".join(stage for kind, payload in self.items if kind == "chain" for stage in payload)

	def graph(self):
		segments = []
		label = "0:a"
		for kind, payload in self.items:
			self.counter += 1
			prefix = f"b{self.counter}"
			if kind == "chain":
				outLabel = f"{prefix}out"
				segments.append(f"[{label}]{','.join(payload)}[{outLabel}]")
			else:
				blockSegments, outLabel = payload(label, prefix)
				segments.extend(blockSegments)
			label = outLabel
		return ";".join(segments), label


def _dry_wet_block(wetStages, wetPercent):
	wet = wetPercent / 100.0
	dry = 1.0 - wet

	def builder(inLabel, prefix):
		return [
			f"[{inLabel}]asplit=2[{prefix}dry][{prefix}wetIn]",
			f"[{prefix}wetIn]{','.join(wetStages)}[{prefix}wet]",
			f"[{prefix}dry][{prefix}wet]amix=inputs=2:weights='{dry:g} {wet:g}':normalize=0:duration=first[{prefix}out]",
		], f"{prefix}out"
	return builder


def _gain_pair_segments(sourceLabel, prefix, leftExpr, rightExpr, outLabel):
	return [
		f"[{sourceLabel}]channelsplit=channel_layout=stereo[{prefix}L][{prefix}R]",
		f"[{prefix}L]volume=eval=frame:volume='{leftExpr}'[{prefix}Lg]",
		f"[{prefix}R]volume=eval=frame:volume='{rightExpr}'[{prefix}Rg]",
		f"[{prefix}Lg][{prefix}Rg]amerge=inputs=2[{outLabel}]",
	]


def _autopan_block(s):
	leftExpr, rightExpr = build_autopan_gain_exprs(s["autoPanSpeedHz"], s["autoPanDirection"], s["autoPanSections"])
	keepVocal = _on(s, "autoPanKeepVocalCenter")
	lowHz = s["autoPanVocalLowHz"]
	highHz = s["autoPanVocalHighHz"]

	def builder(inLabel, prefix):
		if not keepVocal:
			return _gain_pair_segments(inLabel, prefix, leftExpr, rightExpr, f"{prefix}out"), f"{prefix}out"
		# The voice is estimated as what the two channels share (their mid),
		# limited to the vocal band. That estimate is taken out of the music,
		# the music is panned, and the same estimate is added back in the
		# centre. Anything else that sits in the middle of the mix in that
		# band stays in the middle with the voice.
		segments = [
			f"[{inLabel}]asplit=2[{prefix}full][{prefix}probe]",
			f"[{prefix}probe]pan=mono|c0=0.5*c0+0.5*c1,highpass=f={lowHz},lowpass=f={highHz}[{prefix}vox]",
			f"[{prefix}vox]asplit=2[{prefix}vox1][{prefix}vox2]",
			f"[{prefix}full][{prefix}vox1]amerge=inputs=2,pan=stereo|c0=c0-c2|c1=c1-c2[{prefix}music]",
		]
		segments.extend(_gain_pair_segments(f"{prefix}music", f"{prefix}p", leftExpr, rightExpr, f"{prefix}panned"))
		segments.append(f"[{prefix}panned][{prefix}vox2]amerge=inputs=2,pan=stereo|c0=c0+c2|c1=c1+c2[{prefix}out]")
		return segments, f"{prefix}out"
	return builder


def _pan_sections_block(activeSections):
	leftExpr = build_pan_multi_channel_expr(activeSections, "L")
	rightExpr = build_pan_multi_channel_expr(activeSections, "R")

	def builder(inLabel, prefix):
		return _gain_pair_segments(inLabel, prefix, leftExpr, rightExpr, f"{prefix}out"), f"{prefix}out"
	return builder


def build_custom_plan(rawSettings):
	s = sanitize_settings(rawSettings)
	assembler = _GraphAssembler()
	assembler.addChain(build_clean_stages(s))
	assembler.addChain(build_time_stages(s))
	assembler.addChain(build_channel_stages(s))
	assembler.addChain(build_tone_stages(s))
	assembler.addChain(build_binaural_stages(s))

	wetStages = build_wet_stages(s)
	if wetStages and s["effectMixPercent"] < 100:
		assembler.addBlock(_dry_wet_block(wetStages, s["effectMixPercent"]))
	else:
		assembler.addChain(wetStages)

	if _on(s, "autoPanLoopEnabled"):
		if autopan_uses_graph(s):
			assembler.addBlock(_autopan_block(s))
		else:
			assembler.addChain([build_autopan_loop_stage(s)])

	activeSections = get_active_pan_sections(s["panSections"]) if _on(s, "panningEnabled") else []
	if activeSections:
		assembler.addBlock(_pan_sections_block(activeSections))

	hasLoudnorm = _on(s, "loudnormEnabled")
	assembler.addChain(build_post_stages(s, LOUDNORM_SLOT if hasLoudnorm else None))

	loudnormStage = single_pass_loudnorm(s) if hasLoudnorm else ""
	twoPass = hasLoudnorm and _on(s, "loudnormTwoPass")
	if assembler.isLinear():
		return MixdownPlan(chain=assembler.linearChain(), loudnormStage=loudnormStage, twoPassLoudnorm=twoPass)
	graphText, outputLabel = assembler.graph()
	return MixdownPlan(graph=graphText, outputLabel=outputLabel, loudnormStage=loudnormStage, twoPassLoudnorm=twoPass)


# ---- measurements

_JSON_BLOCK = re.compile(r"\{[^{}]*\}", re.S)
LOUDNORM_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")


def parse_loudnorm_measurement(stderrLines):
	"""The numbers loudnorm printed after its first pass, or None when they are missing or not finite (silent audio measures as -inf), in which case the caller falls back to one pass."""
	text = "".join(stderrLines if isinstance(stderrLines, (list, tuple)) else [str(stderrLines)])
	for block in reversed(_JSON_BLOCK.findall(text)):
		try:
			raw = json.loads(block)
		except ValueError:
			continue
		if not all(key in raw for key in LOUDNORM_KEYS):
			continue
		try:
			measured = {key: float(raw[key]) for key in LOUDNORM_KEYS}
		except (TypeError, ValueError):
			return None
		if not all(math.isfinite(value) for value in measured.values()):
			return None
		return measured
	return None


_INTEGRATED_LOUDNESS = re.compile(r"^\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", re.M)


def parse_integrated_loudness(stderrLines):
	"""Integrated loudness in LUFS from the ebur128 summary, or None."""
	text = "".join(stderrLines if isinstance(stderrLines, (list, tuple)) else [str(stderrLines)])
	matches = _INTEGRATED_LOUDNESS.findall(text)
	return float(matches[-1]) if matches else None


_FILTER_LINE = re.compile(r"^\s[T.][S.][C.]\s+(\S+)\s+\S+->\S+")


def parse_filter_list(filtersOutput):
	"""Names of the filters in the output of `ffmpeg -filters`."""
	names = set()
	for line in str(filtersOutput).splitlines():
		match = _FILTER_LINE.match(line)
		if match:
			names.add(match.group(1))
	return names


EFFECT_FILTERS = OrderedDict([
	("noiseReductionEnabled", ("afftdn",)),
	("noiseGateEnabled", ("agate",)),
	("rumbleEnabled", ("highpass",)),
	("clickRemoverEnabled", ("adeclick", "adeclip")),
	("deesserEnabled", ("deesser",)),
	("silenceTrimEnabled", ("silenceremove",)),
	("reversalEnabled", ("areverse",)),
	("tempoEnabled", ("atempo", "asetrate", "aresample")),
	("fastSpeedEnabled", ("atempo",)),
	("nightcoreEnabled", ("asetrate", "aresample")),
	("eqEnabled", ("bass", "treble")),
	("parametricEqEnabled", ("firequalizer",)),
	("voiceClarityEnabled", ("equalizer",)),
	("loudnessContourEnabled", ("bass", "treble", "equalizer")),
	("tiltEnabled", ("bass", "treble")),
	("channelToolEnabled", ("aformat", "pan")),
	("binauralEnabled", ("crossfeed", "extrastereo")),
	("chorusEnabled", ("chorus",)),
	("modulationEnabled", ("flanger", "aphaser", "tremolo", "vibrato")),
	("haasEnabled", ("adelay",)),
	("freqShiftEnabled", ("afreqshift",)),
	("reverbEnabled", ("aecho",)),
	("roomReverbEnabled", ("aecho",)),
	("muffledRoomEnabled", ("lowpass", "bass", "aecho")),
	("autoPanLoopEnabled", ("apulsator", "volume", "channelsplit", "amerge", "asplit", "pan", "highpass", "lowpass")),
	("panningEnabled", ("volume", "channelsplit", "amerge")),
	("volumeGainEnabled", ("volume",)),
	("expanderEnabled", ("compand",)),
	("dynaudnormEnabled", ("dynaudnorm",)),
	("speechnormEnabled", ("speechnorm",)),
	("compressorEnabled", ("acompressor",)),
	("softClipEnabled", ("asoftclip", "volume")),
	("lofiEnabled", ("acrusher",)),
	("loudnormEnabled", ("loudnorm",)),
	("limiterEnabled", ("alimiter",)),
	("telephoneEnabled", ("highpass", "lowpass", "acompressor", "asoftclip", "equalizer", "volume")),
	("subBoostEnabled", ("asubboost",)),
	("enhancerEnabled", ("crystalizer",)),
])


def find_unavailable_effects(availableFilters):
	"""{enabled-key: [missing filter names]} for every effect this ffmpeg cannot run. An empty or missing filter list means detection failed, so nothing is reported rather than everything."""
	if not availableFilters:
		return {}
	unavailable = OrderedDict()
	for key, needed in EFFECT_FILTERS.items():
		missing = [name for name in needed if name not in availableFilters]
		if missing:
			unavailable[key] = missing
	return unavailable


# ---- conflicts between effects that are switched on together

def find_effect_conflicts(rawSettings):
	"""Short codes (with details) for combinations that cancel each other out, repeat the same job, or make a time setting mean something unexpected. The dialog turns each into a spoken sentence."""
	s = sanitize_settings(rawSettings)
	conflicts = []

	normalizers = [name for name, key in (("loudnorm", "loudnormEnabled"), ("dynaudnorm", "dynaudnormEnabled"), ("speechnorm", "speechnormEnabled")) if _on(s, key)]
	if len(normalizers) >= 2:
		conflicts.append(("stackedNormalizers", {"names": normalizers}))
	if _on(s, "volumeGainEnabled") and _on(s, "loudnormEnabled"):
		conflicts.append(("gainWithLoudnorm", {}))
	if _on(s, "noiseGateEnabled") and _on(s, "expanderEnabled"):
		conflicts.append(("gateAndExpander", {}))
	if _on(s, "reverbEnabled") and _on(s, "roomReverbEnabled"):
		conflicts.append(("doubleReverb", {}))
	if _on(s, "autoPanLoopEnabled") and _on(s, "panningEnabled") and get_active_pan_sections(s["panSections"]):
		conflicts.append(("autoPanAndSections", {}))

	speedFactor = 1.0
	speedEffects = 0
	if _on(s, "tempoEnabled") and abs(s["tempoFactor"] - 1.0) > 0.001:
		speedFactor *= s["tempoFactor"]
		speedEffects += 1
	if _on(s, "fastSpeedEnabled") and s["fastSpeedFactor"] > 1.001:
		speedFactor *= s["fastSpeedFactor"]
		speedEffects += 1
	if _on(s, "nightcoreEnabled"):
		amount = 1.0 + s["nightcoreAmountPercent"] / 100.0
		speedFactor *= amount if s["nightcoreMode"] == "nightcore" else 1.0 / amount
		speedEffects += 1
	if speedEffects >= 2:
		conflicts.append(("stackedSpeed", {"factor": speedFactor}))

	hasTimedSections = bool(s["autoPanSections"]) and _on(s, "autoPanLoopEnabled") or bool(get_active_pan_sections(s["panSections"])) and _on(s, "panningEnabled")
	changesTimeline = _on(s, "reversalEnabled") or _on(s, "silenceTrimEnabled") or speedEffects > 0
	if hasTimedSections and changesTimeline:
		conflicts.append(("timelineShifted", {}))

	if _on(s, "channelToolEnabled") and s["channelTool"] == "stereo_to_mono":
		stereoOnly = [name for name, active in (
			("binaural", _on(s, "binauralEnabled")),
			("haas", _on(s, "haasEnabled")),
			("vocalCenter", _on(s, "autoPanLoopEnabled") and _on(s, "autoPanKeepVocalCenter")),
		) if active]
		if stereoOnly:
			conflicts.append(("monoWithStereoEffects", {"names": stereoOnly}))

	if s["effectMixPercent"] == 0 and wet_effects_active(s):
		conflicts.append(("mixAtZero", {}))
	return conflicts


def wet_effects_active(settings):
	return bool(build_wet_stages(sanitize_settings(settings)))
