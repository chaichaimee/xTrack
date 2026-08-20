# installTasks.py
# Copyright (C) 2026 Chai Chaimee
# Licensed under GNU General Public License. See COPYING.txt for details.

import os
import sys
import shutil
import logHandler


def onInstall():
	"""
	NVDA 2025.x still runs on 32-bit (x86) Python, while NVDA 2026.x runs on
	64-bit (x64) Python 3.13. Both architectures of the bundled numpy and
	pyaudiowpatch packages are shipped inside the add-on package under
	globalPlugins/xTrack/libs/x86 and globalPlugins/xTrack/libs/x64. This
	removes whichever architecture folder does NOT match the NVDA process
	currently performing the install, so only the architecture actually
	needed remains on disk once installation finishes.

	This replaces the previous approach, where overlay_loader.py copied and
	deleted files under the add-on's libs folder on every NVDA startup.
	Doing that cleanup once here, at install time, means overlay_loader.py
	no longer has to touch the filesystem at runtime at all.
	"""
	addonDir = os.path.dirname(os.path.abspath(__file__))
	libsDir = os.path.join(addonDir, "globalPlugins", "xTrack", "libs")

	if not os.path.isdir(libsDir):
		logHandler.log.debug(
			"xTrack: libs directory not found at %s during install; skipping "
			"architecture cleanup." % libsDir
		)
		return

	targetArch = "x64" if sys.maxsize > 2 ** 32 else "x86"
	unusedArch = "x86" if targetArch == "x64" else "x64"

	unusedArchPath = os.path.join(libsDir, unusedArch)
	if not os.path.isdir(unusedArchPath):
		logHandler.log.debug(
			"xTrack: libs/%s already absent; nothing to clean up for this "
			"install." % unusedArch
		)
		return

	try:
		shutil.rmtree(unusedArchPath)
		logHandler.log.info(
			"xTrack: removed unused libs/%s folder during install, keeping "
			"libs/%s for this NVDA process." % (unusedArch, targetArch)
		)
	except (OSError, PermissionError):
		# The add-on still works with the matching architecture present;
		# log so the leftover folder can be investigated rather than
		# failing installation or silently leaving dead weight behind.
		logHandler.log.exception(
			"xTrack: failed to remove unused libs/%s folder during install."
			% unusedArch
		)
