# overlay_loader.py
# Dual-architecture binary loader for NVDA add-ons
# Cleans up unused architecture folders after deployment.
# Copyright (C) 2026 Chai Chaimee
# Licensed under GNU General Public License.

import os
import sys
import shutil
import time

PACKAGES_TO_DEPLOY = ["pyaudiowpatch", "numpy"]
ARCH_FOLDERS = ["x86", "x64"]

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
    import builtins
    builtins.print(f"[overlay_loader] {msg}")

def overlayBinaries():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    tools_dir = os.path.join(base_dir, "tools")   # พิมพ์เล็ก
    arch = _get_architecture_subdir()
    src_arch_dir = os.path.join(tools_dir, arch)

    _log(f"Architecture: {arch}")
    _log(f"Tools dir: {tools_dir}")

    # -------------------- คัดลอก (ถ้ามีโฟลเดอร์สถาปัตยกรรม) --------------------
    if os.path.isdir(src_arch_dir):
        # ลบแพ็กเกจเก่าที่ root
        for pkg in PACKAGES_TO_DEPLOY:
            old_root = os.path.join(base_dir, pkg)
            if os.path.exists(old_root):
                _log(f"Removing old root: {old_root}")
                shutil.rmtree(old_root, ignore_errors=True)

        # คัดลอกจากสถาปัตยกรรมที่ถูกต้องไปยัง tools/
        for pkg in PACKAGES_TO_DEPLOY:
            src = os.path.join(src_arch_dir, pkg)
            dst = os.path.join(tools_dir, pkg)
            if os.path.isdir(src):
                if os.path.exists(dst):
                    _log(f"Removing old: {dst}")
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst)
                _log(f"Copied {src} -> {dst}")
            else:
                _log(f"WARNING: {src} not found, skipping")
    else:
        _log(f"WARNING: {src_arch_dir} not found! Architecture-specific packages will NOT be deployed.")

    # -------------------- ลบโฟลเดอร์ x86/x64 (ถ้ายังเหลือ) --------------------
    for folder in ARCH_FOLDERS:
        path = os.path.join(tools_dir, folder)
        if os.path.exists(path):
            _log(f"Removing {path}")
            shutil.rmtree(path, ignore_errors=True)

    # -------------------- เพิ่ม sys.path และ DLL search path เสมอ --------------------
    if os.path.isdir(tools_dir):
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
            _log(f"Added {tools_dir} to sys.path")
        _add_dll_directory(tools_dir)

        pyaudiowpatch_dir = os.path.join(tools_dir, "pyaudiowpatch")
        if os.path.isdir(pyaudiowpatch_dir):
            if pyaudiowpatch_dir not in sys.path:
                sys.path.insert(0, pyaudiowpatch_dir)
                _log(f"Added {pyaudiowpatch_dir} to sys.path")
            _add_dll_directory(pyaudiowpatch_dir)
            _log(f"Added {pyaudiowpatch_dir} to DLL search path")
    else:
        _log(f"ERROR: {tools_dir} not found!")

    time.sleep(0.2)
    _log("overlayBinaries completed.")

overlayBinaries()