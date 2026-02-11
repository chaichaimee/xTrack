# image.py

import subprocess
import os
import ui
from logHandler import log
import addonHandler
import threading

addonHandler.initTranslation()

def get_image_dimensions_fast(tools_path, file_path):
    """Get image dimensions quickly using ffprobe."""
    ffprobe_path = os.path.join(tools_path, "ffprobe.exe")
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
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            encoding='utf-8',
            errors='ignore',
            timeout=10
        )
        
        if result.returncode == 0 and result.stdout.strip():
            dimensions = result.stdout.strip().split(',')
            if len(dimensions) == 2:
                width = int(dimensions[0])
                height = int(dimensions[1])
                log.info(f"Image dimensions for {file_path}: {width}x{height}")
                return width, height
    except Exception as e:
        log.error(f"Error getting dimensions for {file_path}: {e}")
    
    return 0, 0

def get_image_dpi_fast(tools_path, file_path):
    """Get image DPI quickly using ffprobe."""
    ffprobe_path = os.path.join(tools_path, "ffprobe.exe")
    if not os.path.exists(ffprobe_path):
        log.error(f"ffprobe not found at {ffprobe_path}")
        return 96
    
    # Try multiple methods to get DPI
    methods = [
        # Method 1: Check stream tags
        [
            ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream_tags=dpi",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
        ],
        # Method 2: Check format tags
        [
            ffprobe_path,
            "-v", "error",
            "-show_entries", "format_tags=dpi",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
        ],
        # Method 3: Check resolution in metadata
        [
            ffprobe_path,
            "-v", "error",
            "-show_entries", "stream_tags=resolution",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
        ],
    ]
    
    for cmd in methods:
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                encoding='utf-8',
                errors='ignore',
                timeout=5
            )
            
            if result.returncode == 0 and result.stdout.strip():
                output = result.stdout.strip()
                log.info(f"DPI output for {file_path}: {output}")
                
                # Parse DPI from various formats
                if 'x' in output:
                    # Format like "72x72" or "300x300"
                    parts = output.split('x')
                    try:
                        dpi_value = int(float(parts[0]))
                        if dpi_value > 0:
                            log.info(f"Found DPI for {file_path}: {dpi_value}")
                            return dpi_value
                    except:
                        pass
                else:
                    try:
                        dpi_value = int(float(output))
                        if dpi_value > 0:
                            log.info(f"Found DPI for {file_path}: {dpi_value}")
                            return dpi_value
                    except:
                        pass
        except Exception as e:
            log.error(f"Error in DPI method for {file_path}: {e}")
            continue
    
    log.info(f"No DPI found for {file_path}, using default 96")
    return 96  # Default DPI

def process_single_image(file_path, tools_path):
    """Process a single image and return the info message."""
    try:
        log.info(f"Processing image: {file_path}")
        
        if not os.path.exists(file_path):
            return _("File not found")
        
        # Get dimensions
        width, height = get_image_dimensions_fast(tools_path, file_path)
        
        if width == 0 or height == 0:
            return _("Could not read image dimensions")
        
        # Get DPI
        dpi = get_image_dpi_fast(tools_path, file_path)
        
        # Create message without filename to avoid duplicate announcements
        message = _("{width} by {height} pixels, {dpi} DPI").format(
            width=width,
            height=height,
            dpi=dpi
        )
        
        log.info(f"Image info result: {message}")
        return message
        
    except Exception as e:
        log.error(f"Error processing image {file_path}: {e}")
        return _("Error getting image information")

def show_image_info(selected_files, tools_path):
    """Display image information (dimensions and DPI) for selected files."""
    if not selected_files:
        log.error("No files selected for image info")
        ui.message(_("No files selected"))
        return
    
    log.info(f"Starting image info processing for {len(selected_files)} files")
    
    # Process all images first
    results = []
    for file_path in selected_files:
        result = process_single_image(file_path, tools_path)
        results.append((os.path.basename(file_path), result))
    
    # Announce results with delay to avoid interruption from Explorer
    def announce_results():
        import time
        
        # Wait a bit to ensure Explorer announcements are done
        time.sleep(0.1)
        
        for filename, result in results:
            # Only announce if we got valid information
            if not result.startswith(_("File not found")) and not result.startswith(_("Could not read")):
                # Announce without filename to avoid duplicate announcements
                ui.message(result)
            else:
                # If error, include filename in announcement
                ui.message(_("{filename}: {result}").format(
                    filename=filename,
                    result=result
                ))
            
            # Small delay between announcements
            time.sleep(0.05)
    
    # Start announcement in a separate thread to avoid blocking
    thread = threading.Thread(target=announce_results, daemon=True)
    thread.start()

