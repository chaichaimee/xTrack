# overlay_loader.py
# Copyright (C) 2026 Chai Chaimee
# Licensed under GNU General Public License.

import os
import sys
import shutil

def _is_64bit_process():
	return sys.maxsize > 2**32

def _get_architecture_subdir():
	return "x64" if _is_64bit_process() else "x86"

def _add_dll_directory(path):
	if hasattr(os, 'add_dll_directory'):
		try:
			os.add_dll_directory(path)
		except (OSError, FileNotFoundError):
			pass

def _log(msg):
	try:
		from logHandler import log
		log.info(f"[overlay_loader] {msg}")
	except ImportError:
		import builtins
		builtins.print(f"[overlay_loader] {msg}")

def _log_error(msg):
	try:
		from logHandler import log
		log.error(f"[overlay_loader] {msg}")
	except ImportError:
		import builtins
		builtins.print(f"[overlay_loader] ERROR: {msg}")

def overlayBinaries():
	base_dir = os.path.dirname(os.path.abspath(__file__))
	tools_dir = os.path.join(base_dir, "tools")
	arch = _get_architecture_subdir()
	src_arch_dir = os.path.join(tools_dir, arch)

	_log(f"Architecture: {arch}")
	_log(f"Tools dir: {tools_dir}")

	pkg_name = "pyaudiowpatch"
	src_pkg = os.path.join(src_arch_dir, pkg_name)
	dst_pkg = os.path.join(tools_dir, pkg_name)

	old_root_pkg = os.path.join(base_dir, pkg_name)
	if os.path.exists(old_root_pkg):
		_log(f"Removing old root package: {old_root_pkg}")
		shutil.rmtree(old_root_pkg, ignore_errors=True)

	if os.path.isdir(src_pkg):
		if os.path.exists(dst_pkg):
			_log(f"Removing existing package: {dst_pkg}")
			shutil.rmtree(dst_pkg, ignore_errors=True)
		_log(f"Copying {src_pkg} -> {dst_pkg}")
		shutil.copytree(src_pkg, dst_pkg)
	else:
		_log_error(f"Source package not found: {src_pkg}")

	for folder in ["x86", "x64"]:
		folder_path = os.path.join(tools_dir, folder)
		if os.path.exists(folder_path):
			_log(f"Removing architecture folder: {folder_path}")
			shutil.rmtree(folder_path, ignore_errors=True)

	if tools_dir not in sys.path:
		sys.path.insert(0, tools_dir)
		_log(f"Added {tools_dir} to sys.path")

	_add_dll_directory(tools_dir)
	_log(f"Added {tools_dir} to DLL search path")

	pkg_path = os.path.join(tools_dir, pkg_name)
	if os.path.isdir(pkg_path):
		if pkg_path not in sys.path:
			sys.path.insert(0, pkg_path)
			_log(f"Added {pkg_path} to sys.path")
		_add_dll_directory(pkg_path)
		_log(f"Added {pkg_path} to DLL search path")

		try:
			import pyaudiowpatch
			_log("pyaudiowpatch imported successfully")
		except ImportError as e:
			_log_error(f"Failed to import pyaudiowpatch: {e}")

	_log("overlayBinaries completed.")

overlayBinaries()