# image.py

import struct
import subprocess
import os
import ui
from logHandler import log
import addonHandler
import threading
from .ffmpegSupport import announceResults

addonHandler.initTranslation()

MAX_JPEG_SCAN_BYTES = 262144
INCHES_PER_METER = 0.0254
CM_PER_INCH = 2.54
TIFF_TAG_X_RESOLUTION = 0x011A
TIFF_TAG_RESOLUTION_UNIT = 0x0128


def get_image_dimensions_fast(libs_path, file_path):
	"""Get image dimensions quickly using ffprobe."""
	ffprobe_path = os.path.join(libs_path, "ffprobe.exe")
	if not os.path.exists(ffprobe_path):
		log.error(f"ffprobe not found at {ffprobe_path}")
		return 0, 0

	cmd = [
		ffprobe_path,
		"-v", "error",
		"-select_streams", "v:0",
		"-show_entries", "stream=width,height",
		"-of", "csv=p=0",
		file_path,
	]

	try:
		result = subprocess.run(
			cmd,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			stdin=subprocess.DEVNULL,
			text=True,
			creationflags=subprocess.CREATE_NO_WINDOW,
			encoding='utf-8',
			errors='ignore',
			timeout=10
		)
		if result.returncode == 0 and result.stdout.strip():
			dimensions = result.stdout.strip().splitlines()[0].split(',')
			if len(dimensions) == 2:
				width = int(dimensions[0])
				height = int(dimensions[1])
				log.info(f"Image dimensions for {file_path}: {width}x{height}")
				return width, height
	except (OSError, subprocess.SubprocessError, ValueError) as e:
		log.error(f"Error getting dimensions for {file_path}: {e}")

	return 0, 0


def _dpi_from_pixels_per_meter(pixelsPerMeter):
	dpi = round(pixelsPerMeter * INCHES_PER_METER)
	return dpi if dpi > 0 else None


def _tiff_resolution_dpi(readAt):
	"""DPI from a TIFF-structured block (a TIFF file, or the body of a JPEG's EXIF segment).

	readAt(offset, length) returns that many bytes starting at offset within the block.
	"""
	header = readAt(0, 8)
	if len(header) < 8:
		return None
	if header[:2] == b"II":
		order = "<"
	elif header[:2] == b"MM":
		order = ">"
	else:
		return None
	if struct.unpack(order + "H", header[2:4])[0] != 42:
		return None
	firstIfdOffset = struct.unpack(order + "I", header[4:8])[0]
	countBytes = readAt(firstIfdOffset, 2)
	if len(countBytes) < 2:
		return None
	entryCount = min(struct.unpack(order + "H", countBytes)[0], 512)
	entries = readAt(firstIfdOffset + 2, entryCount * 12)

	xResolution = None
	unit = 2
	for index in range(len(entries) // 12):
		tag, fieldType, _count, valueField = struct.unpack(order + "HHI4s", entries[index * 12:index * 12 + 12])
		if tag == TIFF_TAG_X_RESOLUTION and fieldType == 5:
			rational = readAt(struct.unpack(order + "I", valueField)[0], 8)
			if len(rational) == 8:
				numerator, denominator = struct.unpack(order + "II", rational)
				if denominator:
					xResolution = numerator / denominator
		elif tag == TIFF_TAG_RESOLUTION_UNIT and fieldType == 3:
			unit = struct.unpack(order + "H", valueField[:2])[0]
	if not xResolution or unit == 1:
		return None
	dpi = round(xResolution * CM_PER_INCH) if unit == 3 else round(xResolution)
	return dpi if 0 < dpi < 100000 else None


def _png_dpi(imageFile):
	imageFile.seek(8)
	while True:
		chunkHeader = imageFile.read(8)
		if len(chunkHeader) < 8:
			return None
		length, chunkType = struct.unpack(">I4s", chunkHeader)
		if chunkType == b"pHYs":
			data = imageFile.read(9)
			if len(data) == 9 and data[8] == 1:
				return _dpi_from_pixels_per_meter(struct.unpack(">I", data[:4])[0])
			return None
		if chunkType in (b"IDAT", b"IEND"):
			return None
		imageFile.seek(length + 4, os.SEEK_CUR)


def _jpeg_dpi(imageFile):
	data = imageFile.read(MAX_JPEG_SCAN_BYTES)
	position = 2
	exifDpi = None
	while position + 4 <= len(data):
		if data[position] != 0xFF:
			break
		marker = data[position + 1]
		if marker == 0xFF:
			position += 1
			continue
		if marker == 0xDA or marker == 0xD9:
			break
		length = struct.unpack(">H", data[position + 2:position + 4])[0]
		segment = data[position + 4:position + 2 + length]
		if marker == 0xE0 and segment[:5] == b"JFIF\x00" and len(segment) >= 12:
			units = segment[7]
			density = struct.unpack(">H", segment[8:10])[0]
			if units == 1 and density > 0:
				return density
			if units == 2 and density > 0:
				return round(density * CM_PER_INCH)
		elif marker == 0xE1 and segment[:6] == b"Exif\x00\x00" and exifDpi is None:
			tiffBody = segment[6:]
			exifDpi = _tiff_resolution_dpi(lambda offset, size: tiffBody[offset:offset + size])
		position += 2 + length
	return exifDpi


def _bmp_dpi(imageFile):
	header = imageFile.read(42)
	if len(header) < 42:
		return None
	return _dpi_from_pixels_per_meter(struct.unpack("<i", header[38:42])[0])


def _tiff_file_dpi(imageFile):
	def readAt(offset, size):
		imageFile.seek(offset)
		return imageFile.read(size)
	return _tiff_resolution_dpi(readAt)


def read_image_dpi(file_path):
	"""The DPI stored in the file itself (PNG pHYs, JPEG JFIF/EXIF, BMP, TIFF), or None.

	ffprobe does not report image resolution, so the headers are read directly;
	nothing here starts a process, and only the first part of the file is read.
	"""
	try:
		with open(file_path, "rb") as imageFile:
			signature = imageFile.read(8)
			if signature[:8] == b"\x89PNG\r\n\x1a\n":
				return _png_dpi(imageFile)
			if signature[:2] == b"\xff\xd8":
				imageFile.seek(0)
				return _jpeg_dpi(imageFile)
			if signature[:2] == b"BM":
				imageFile.seek(0)
				return _bmp_dpi(imageFile)
			if signature[:4] in (b"II*\x00", b"MM\x00*"):
				return _tiff_file_dpi(imageFile)
	except (OSError, struct.error, ValueError) as e:
		log.error(f"Error reading the DPI of {file_path}: {e}")
	return None


def process_single_image(file_path, libs_path):
	"""Returns (isError, message)."""
	log.info(f"Processing image: {file_path}")

	if not os.path.exists(file_path):
		return True, _("File not found")

	width, height = get_image_dimensions_fast(libs_path, file_path)
	if width == 0 or height == 0:
		return True, _("Could not read image dimensions")

	dpi = read_image_dpi(file_path)
	if dpi is None:
		return False, _("{width} by {height} pixels, DPI not stored in file").format(width=width, height=height)
	return False, _("{width} by {height} pixels, {dpi} DPI").format(width=width, height=height, dpi=dpi)


def collect_image_messages(selected_files, libs_path):
	showNames = len(selected_files) > 1
	messages = []
	for file_path in selected_files:
		isError, text = process_single_image(file_path, libs_path)
		name = os.path.basename(file_path)
		# A single result is spoken without the name, because Explorer has just
		# announced the focused file; several results need it to tell them apart.
		if isError or showNames:
			messages.append(_("{filename}: {result}").format(filename=name, result=text))
		else:
			messages.append(text)
	return messages


def show_image_info(selected_files, libs_path):
	"""Display image information (dimensions and DPI) for selected files."""
	if not selected_files:
		log.error("No files selected for image info")
		ui.message(_("No files selected"))
		return

	log.info(f"Starting image info processing for {len(selected_files)} files")

	# ffprobe can take seconds per file, so none of this runs on the main thread.
	def work():
		announceResults(collect_image_messages(list(selected_files), libs_path))

	threading.Thread(target=work, daemon=True).start()
