<p align="center">
  <img src="https://www.nvaccess.org/files/nvda/documentation/userGuide/images/nvda.ico" alt="NVDA Logo" width="120">
</p>

# xTrack

<p align="center">
  <b>Author:</b> Chai Chaimee &amp; Malee Yamazaki<br>
  <b>URL:</b> <a href="https://github.com/chaichaimee/xTrack">https://github.com/chaichaimee/xTrack</a>
</p>

<br>

xTrack is an NVDA add-on that lets you quickly save and access frequently used files and folders, while providing a versatile, highly accessible media processing suite directly within Windows File Explorer. Effortlessly convert, edit, gain-adjust, mix, and record audio, video, and images with full screen reader compatibility.

<br>

---

<br><br>

## How to Operate xTrack

To use xTrack features, simply navigate inside Windows File Explorer, select your target file(s) or folder(s), and press **ALT+Windows+X** to open the xTrack Context Menu. Choose your desired function using the arrow keys and press **Enter**.

<br>

* **ALT+Windows+X:** Opens the xTrack context menu on selected files/folders in File Explorer, containing:
  * Convert Audio
  * Convert Video
  * Convert MP3 to MP4
  * MP3 Gain
  * MP3 WAV Mixdown
  * Merge MP3
  * Trim Audio/Video File
  * Split Audio
  * Resize Image
  * Image Info
  * Video Info
  * Record Settings
  <br>
* **CTRL+Shift+Space Bar:** Start background audio recording; press again to pause or resume.
* **CTRL+Windows+Space Bar:** Stop recording and automatically save the output file.

---

<br><br>

## Features

* **1. Audio Conversion Engine:**

  Batch convert audio and video files into popular audio formats including MP3, WAV, FLAC, M4A, OGG, OPUS, M4R, and ALAC. Allows full adjustment of bitrates, sample rates, channel layouts, and output volume levels.

  <br>

* **2. Video Transcoding Engine:**

  Process video files into MP4, MKV, MOV, AVI, or WebM containers. Provides complete control over video codecs, frame rates, audio sample rates, and channel configurations.

  <br>

* **3. MP3 to MP4 Visualizer:**

  Transform audio files into video slideshows ready for uploading to video platforms. Uses a single static image, a folder of multiple rotating images, or custom solid background colors.

  <br>

* **4. MP3 Gain Volume Normalization:**

  Analyze peak amplitude and normalize audio levels across single tracks or entire folder trees without re-encoding or sacrificing original quality. Full undo history is retained for safety.

  <br>

* **5. MP3 WAV Mixdown Studio (Vocal Ducking & Presets):**

  Seamlessly blend speech recordings with background music using dedicated production presets or custom controls. The mixdown module is divided into two operational modes:

  * **Built-in Ready-to-Use Presets:**
    * **Podcast Standard:** Applies a -12 dB background music attenuation when voice is detected, with smooth 150ms attack and 500ms release curves.
    * **Voiceover / Audiobook:** Aggressive -18 dB music ducking with a rapid 50ms attack time to keep speech crisp and legible over continuous music.
    * **Background Chill:** Subtle -6 dB music ducking for gentle ambient leveling without jarring volume jumps.
    * **Radio Promo / Commercial:** Fast 30ms attack time, -20 dB deep ducking, and integrated peak output limiting for high-impact promotional spots.

    <br>

  * **Custom Mixdown Configuration:**
    * **Speech & Music Selection:** Select specific primary speech files and background tracks.
    * **Music Delay Offset:** Set an exact start delay for the background music in seconds or milliseconds.
    * **Ducking Level (dB):** Custom adjustment of how far background music drops during vocal detection.
    * **Sensitivity Threshold:** Fine-tune input audio sensitivity required to activate auto-ducking.
    * **Attack & Release Speeds:** Control how quickly background audio drops when speech begins and recovers during pauses.
    * **Master Gain & Balance:** Set output levels and L/R stereo panning independently for voice and music.

  <br>

* **6. Audio Merging & Cross-fading:**

  Merge multiple MP3 files sequentially into a single track, or apply smooth cross-fade overlaps ranging from 1 to 10 seconds between songs.

  <br>

* **7. Lossless Audio & Video Trimming:**

  Cut audio and video files by entering precise start and end timestamps (HH:MM:SS.ms). Features non-destructive stream-copy trimming for videos and audio fade-in/out envelopes.

  <br>

* **8. Audio Splitting:**

  Divide long audio files or audiobooks into smaller, manageable clips by specifying segment durations or total split counts.

  <br>

* **9. Image Processing & Resizing:**

  Resize, crop, and convert image dimensions individually or in batch across formats like JPG, PNG, WebP, and AVIF while maintaining aspect ratios.

  <br>

* **10. File Metadata Inspection (Image & Video Info):**

  Instantly retrieve and speak technical metadata via an accessible popup dialog, including resolution, color space, duration, codecs, frame rates, and bitrates.

  <br>

* **11. Multi-Source Background Audio Recorder:**

  Capture system audio output, external microphone input, or both simultaneously. Includes configurable noise gating, high-pass rumble filters, and real-time voice-over-system auto-ducking.

  <br>

* **12. Wide Format & Codec Compatibility:**

  Full support across mobile and professional desktop formats:

  * **Audio Formats:** MP3, WAV, OGG, FLAC, M4A, AAC, AMR, CAF, OPUS, 3GA, WMA, M4R, ALAC, AIFF, AIF, PCM.
  * **Video Formats:** MP4, AVI, MKV, MOV, WMV, FLV, WebM, M4V, 3GP, 3G2, TS, MTS, M2TS, M3U8, OGV, H.264, HEVC, VOB.
  * **Image Formats:** JPG, JPEG, PNG, BMP, TIFF, TIF, WebP, AVIF, GIF, ICO, SVG, HEIC, HEIF, JXL, DNG, RAW, APNG.

---

<br><br>

## Benefits

* **Accelerated Editing Workflow:** Complete complex audio mixes, conversions, and image edits directly inside File Explorer without opening bulky software.
* **Reduced Keystroke Fatigue:** Seamless NVDA screen reader integration removes the need to navigate complex, inaccessible graphical control panels.
* **Lossless & Safe Editing:** Non-destructive video copy modes and MP3 Gain undo histories protect original source media from permanent damage.
* **Studio-Grade Output Quality:** Integrated DSP processing (auto-ducking, limiters, noise gates) yields professional sound for podcasts and voiceovers.
* **Universal Device Support:** Easily handle locked smartphone audio memos (iOS CAF/ALAC, Android AMR/3GA) by converting them into standard playback formats.

---

<br><br>

## Why Use It

Managing multimedia tasks often forces screen reader users to jump between multiple complex, inaccessible, or slow desktop applications. xTrack solves this core problem by placing a powerful, streamlined production suite directly inside your native Windows Context Menu. Whether you need to normalize folder audio, overlay voice over music with precision ducking, losslessly trim video files, or record system sound, xTrack offers an efficient, reliable, and screen-reader-friendly environment built specifically for screen reader users.

---

<br><br>

## Marketing

Transform how you interact with media on Windows. Upgrade your daily productivity with xTrack today and experience total control over audio, video, and image processing through quick, accessible shortcuts designed specifically for NVDA users!

<br><br>

## Support Me

If this tool has made your life easier, consider fueling the next update with a small donation.

<br>

[![Support me](https://img.shields.io/badge/Donate-Support%20Me-blue?style=for-the-badge&logo=stripe)](https://buy.stripe.com/dRm9AU1xQ3Ds22N6VK1VK01)

<br>

Your support means the world. Let's build something great together.

<br>

<p align="center">
  <sub>&copy; 2026 Chai Chaimee. NVDA Add-on Released under GNU General Public License.</sub>
</p>