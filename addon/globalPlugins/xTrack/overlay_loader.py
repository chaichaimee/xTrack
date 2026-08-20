# overlay_loader.py
# Copyright (C) 2026 Chai Chaimee
# Licensed under GNU General Public License. See COPYING.txt for details.

import os
import sys


def _isRunning64Bit():
	return sys.maxsize > 2 ** 32


def _getRuntimeArchDirName():
	return "x64" if _isRunning64Bit() else "x86"


def _addDllDirectorySafe(path):
	if not os.path.isdir(path):
		return
	if hasattr(os, "add_dll_directory"):
		try:
			os.add_dll_directory(path)
		except (OSError, FileNotFoundError):
			pass


def _addSysPathSafe(path):
	if os.path.isdir(path) and path not in sys.path:
		sys.path.insert(0, path)


def _logWarning(message):
	try:
		from logHandler import log
		log.warning("[overlay_loader] %s" % message)
	except ImportError:
		import builtins
		builtins.print("[overlay_loader] WARNING: %s" % message)


def overlayBinaries():
	"""
	Wires up sys.path so the bundled 'libs' folder (recorder_backend.py,
	pydub) and the architecture-matched subfolder (libs/x64 or libs/x86,
	which each hold their own copy of numpy and pyaudiowpatch) become
	importable.

	installTasks.py already deletes whichever architecture folder does NOT
	match this NVDA process at install time, so only the folder for the
	architecture actually running remains on disk. This function therefore
	never copies or deletes files at runtime; it just inserts paths, which
	keeps this well under NVDA's main-thread execution budget on every
	startup.

	IMPORTANT: pyaudiowpatch/__init__.py does an ABSOLUTE import,
	`import _portaudiowpatch as pa` (not a relative `from . import`), so
	for that to resolve, the pyaudiowpatch package folder itself
	(libs/{arch}/pyaudiowpatch) must be inserted into sys.path directly --
	adding only its parent (libs/{arch}) is not enough, since Python has
	no way to find a top-level "_portaudiowpatch" module sitting one
	level deeper than a sys.path entry. add_dll_directory is a separate,
	unrelated mechanism (it only affects how Windows resolves DLLs that
	_portaudiowpatch.pyd itself depends on); it does not make the .pyd
	importable, so both steps are required.
	"""
	baseDir = os.path.dirname(os.path.abspath(__file__))
	libsDir = os.path.join(baseDir, "libs")

	if not os.path.isdir(libsDir):
		_logWarning("libs directory not found at %s, skipping binary load" % libsDir)
		return

	_addSysPathSafe(libsDir)

	runtimeArch = _getRuntimeArchDirName()
	archDir = os.path.join(libsDir, runtimeArch)

	if not os.path.isdir(archDir):
		_logWarning(
			"Architecture folder '%s' not found under %s. installTasks.py "
			"may not have run during install, or the add-on package is "
			"incomplete. numpy/pyaudiowpatch imports will likely fail."
			% (runtimeArch, libsDir)
		)
		return

	# archDir on sys.path makes "import numpy" and "import pyaudiowpatch"
	# resolve (both are normal packages found one level under a sys.path
	# entry).
	_addSysPathSafe(archDir)

	# pyaudiowpatch's own package folder must ALSO be on sys.path directly,
	# and be a registered DLL directory, so its absolute
	# "import _portaudiowpatch" (the compiled .pyd living in that same
	# folder) can resolve along with any DLLs it depends on.
	pyaudiowpatchPkgDir = os.path.join(archDir, "pyaudiowpatch")
	if os.path.isdir(pyaudiowpatchPkgDir):
		_addSysPathSafe(pyaudiowpatchPkgDir)
		_addDllDirectorySafe(pyaudiowpatchPkgDir)
	else:
		_logWarning(
			"pyaudiowpatch package folder not found at %s. Recording via "
			"WasapiSoundRecorder will not be available." % pyaudiowpatchPkgDir
		)

	try:
		import pyaudiowpatch  # noqa: F401
		_logWarning("Successfully loaded pyaudiowpatch for %s" % runtimeArch)
	except ImportError as e:
		_logWarning("pyaudiowpatch import failed: %s" % e)
	except Exception as e:
		_logWarning("Unexpected error during import: %s" % e)


overlayBinaries()
