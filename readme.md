# xTrack

Record system audio and your microphone, then convert, trim, merge, split, normalize, enhance and resize your media straight from File Explorer.

**author:** chai chaimee

**url:** https://github.com/chaichaimee/xTrack

## Introduction

xTrack is a complete audio, video and image toolkit built into NVDA. It records everything your computer plays together with your voice, and it adds a context menu to File Explorer so you can convert, trim, merge, split, normalize, enhance and resize your media files without ever leaving the keyboard.

Every dialog is designed for screen reader users. Progress is spoken and signalled with tones, file lists describe each item in a single line, and the result is announced when the work is finished.

Select one or more files in File Explorer, press **Alt+Windows+X**, and xTrack shows only the commands that make sense for your selection.

## Hot Keys

- **Alt+Windows+X** : Open the xTrack context menu (single tap)
- **Ctrl+Shift+Space** : Start recording. While recording, pause. While paused, continue (single tap)
- **Ctrl+Windows+Space** : Stop recording and save the file (single tap)

## Features

### xTrack Context Menu

Press **Alt+Windows+X** with File Explorer in the foreground to open the xTrack menu. The menu is rebuilt every time, and a command appears only when it can work for the files you selected. If you select a single folder that holds no supported files itself, the supported files inside that folder are used.

- **Convert Audio** : appears when audio or video files are selected.
- **Convert Video** : appears when video files are selected.
- **Convert MP3 to MP4** : appears when an MP3 file is selected.
- **MP3 Gain** : always available.
- **Mixdown** : always available.
- **Merge MP3** : appears when two or more MP3 files are selected.
- **Trim Audio/Video File** : appears when an audio or video file is selected.
- **Split Audio** : appears when exactly one audio file is selected.
- **Split Album By Tracks** : appears when exactly one audio file is selected.
- **Split Track by Silent** : appears when one or more audio files are selected.
- **Resize Image** : appears when image files are selected.
- **Image Info** : appears when image files are selected. With a single image, its dimensions and file size are already part of the menu item, so you hear them as soon as you arrow onto it.
- **Video Info** : appears when video files are selected. With a single video, its dimensions, length, frame rate and file size are part of the menu item.
- **Record Settings** : always available.

Outside File Explorer, only MP3 Gain, Mixdown and Record Settings are shown. The commands also appear in NVDA's Input Gestures dialog under the xTrack category, where their keys can be changed.

Supported audio formats: MP3, WAV, OGG, FLAC, M4A, AAC, AMR, CAF, OPUS, 3GA, WMA, M4R, ALAC, AIFF, AIF and PCM. Supported video formats: MP4, AVI, MKV, MOV, WMV, FLV, WEBM, M4V, 3GP, 3G2, TS, MTS, M2TS, M3U8, OGV, H264, HEVC, 264, 265 and VOB. Supported image formats: JPG, JPEG, PNG, BMP, TIFF, TIF, WEBP, AVIF, GIF, ICO, SVG, HEIC, HEIF, JXL, DNG, RAW and APNG.

### Recording System Audio and Microphone

xTrack records what your computer plays, what your microphone hears, or both at once. Recording is controlled entirely with two keys, so you can start and stop without touching the mouse.

1. Press **Ctrl+Shift+Space**. NVDA says "start" and recording begins. If Count-in is enabled in Record Settings, four beeps play first.
2. Press **Ctrl+Shift+Space** again to pause. NVDA says "paused".
3. Press **Ctrl+Shift+Space** once more to continue. NVDA says "continue". System audio is muted for a moment when you pause or continue, so the announcement itself is not captured in the recording.
4. Press **Ctrl+Windows+Space** to stop. NVDA says "stopped" followed by the folder where the file was saved. If the file is still being written, NVDA says "stopped, saving the file" and announces the folder as soon as it is ready. If nothing was saved, NVDA says "stopped No file saved".

If you press **Ctrl+Windows+Space** while nothing is recording, the key is passed on to Windows as usual. If you start a new recording while the previous one is still being saved, NVDA says "Still saving the previous recording". If NVDA is closed during a recording, the recording is stopped.

Files are named with the date and time of the recording, for example `recording_17-10-2026_14-30-05.mp3`. In the separate files mode, two files are created: `recording_system_` and `recording_mic_` followed by the same date and time. When more than one microphone is selected, the microphones are mixed together into a single microphone track. If the merged recording cannot be combined, NVDA tells you and the separate recordings are kept.

### Record Settings

Open Record Settings from the xTrack menu. The dialog has OK, Cancel and Apply buttons. Apply saves the settings without closing the dialog.

- **Recording Mode** : System and Microphone (Merged), System and Microphone (Separate Files), System Only, or Microphone Only.
- **Microphone Device(s)** : a check list of your microphones. Check none for Automatic, which uses the default microphone. Check one to use that microphone, for example one on a separate sound card. Check several to record from them all at once.
- **System Audio Device** : choose which output device's audio is recorded, or leave it on Automatic (Default).
- **Format** : mp3, wav or m4a.
- **MP3 Quality (kbps)** : 64, 96, 128, 160, 192, 256 or 320. For m4a this is the AAC bitrate. For wav the list is hidden because WAV is uncompressed.
- **Microphone Volume (%)** and **System Volume (%)** : from 0 to 300 percent in steps of 5. The default is 100.
- **Audio Processing** : Default, or Communications. Communications asks Windows for voice processing such as noise suppression, echo cancellation and automatic gain control, when your system and audio driver support it. Communications records from one microphone only.
- **Reduce low-frequency noise (High-pass filter)** : removes low rumble such as fan or handling noise from the microphone.
- **Reduce background noise (Noise gate)** : lowers steady background noise between spoken words.
- **Automatic microphone level** : raises the volume of a quiet microphone while you speak. A microphone that is already loud is left unchanged. This option is on by default.
- **Count-in before recording** : plays four beeps before the recording starts.
- **Open folder after recording** : opens the destination folder when you stop. If a File Explorer window for that folder is already open, it is brought to the front instead.
- **Destination Folder** : the full path of the folder for your recordings, with a Browse button. The default is a folder named xTrack_recordings in your user folder. The folder is created if it does not exist.

The three audio-quality options (High-pass filter, Noise gate and Automatic microphone level) apply only when Audio Processing is set to Default. In System Only mode the microphone settings are disabled, and in Microphone Only mode the system audio settings are disabled. At the bottom, the dialog reports whether the audio backend is available.

### Convert Audio

Convert audio or video files to an audio format. When the selection contains video files, only their sound is kept. Several files can be converted in one run.

1. Select the files in File Explorer and press **Alt+Windows+X**, then choose Convert Audio.
2. Review the Files to Convert list. Each file is shown with its duration.
3. Choose the Output Format: MP3, WAV, FLAC, M4A, OGG, OPUS, M4R or ALAC.
4. Choose the quality. The list changes with the format: MP3 offers 320, 256, 192 or 128 kbps; FLAC offers compression levels 0 (Fastest, Larger), 3, 5 (Balanced) and 8 (Smallest, Slowest); M4A offers 256, 192, 128 or 96 kbps; OGG offers Q10 (Best) down to Q2 (Smallest); OPUS offers 256, 192, 128, 96 or 64 kbps; M4R offers 256, 192 or 128 kbps. WAV (16-bit PCM) and ALAC are lossless and have no quality choice.
5. Choose the Sample Rate: 44.1, 48 or 96 kHz.
6. Choose the Volume, from 5 to 200 percent. Above 100 percent a limiter is added to avoid distortion.
7. Optionally check Save converted files in a separate subfolder. The files are then saved in a folder named Converted next to the originals.
8. Press Convert. The button becomes Pause, and then Resume if you pause. Progress is shown and spoken, and a tone plays when each file is done.

You can convert a file to its own format, for example MP3 to MP3, to change its quality, volume or sample rate. In that case, when no subfolder is used, the new file is named with the suffix `_converted`. Your choices are remembered for next time.

### Convert Video

Convert video files to MP4, MKV, MOV, AVI or WebM, and optionally make them smaller.

1. Select the video files in File Explorer and choose Convert Video from the xTrack menu.
2. Each file is listed with its duration, resolution, frame rate and size. The Current File box shows the same details for the file you are on.
3. Choose the Output Format.
4. Choose the Target Size: Keep Original (No resize), or 3840x2160 (4K), 1920x1080 (Full HD), 1280x720 (HD), 1024x576, 854x480, 640x360, 426x240 or 320x180. The list starts on the preset that matches your video. A video is only made smaller; a video that is already at or below the target size is not enlarged.
5. Under Audio Settings, choose the Sample Rate (Keep Original, 44100 Hz or 48000 Hz), the Channels (Keep Original, Stereo or Mono) and the Audio Codec (Keep Original, AAC (Recommended), MP3 or Vorbis).
6. Optionally check Save converted files in a separate subfolder to use a folder named Converted.
7. Press Convert. Progress is spoken every 10 percent with a short tone.

When the video does not need resizing, the picture is copied without re-encoding, which is fast and keeps the original quality. WebM is the exception: its picture is always re-encoded, and it always uses the Vorbis audio codec. MP4 and MOV use AAC when Vorbis is chosen. If a file with the same name already exists, a number is added to the new file name. Your choices are remembered.

### Convert MP3 to MP4

Turn an MP3 file into a video, for example to upload audio to a video site. The video is built from one photo, several photos, or a plain colour.

1. Select one MP3 file in File Explorer and choose Convert MP3 to MP4. The dialog shows the file name and its duration.
2. Press Add Photo to choose one or more images (PNG, JPG, JPEG or BMP). Use Remove Photo to take one out of the Photo List.
3. With one photo, it stays on screen for the whole song. With more than one photo, a Loop Duration box appears (1 to 3600 seconds, 5 by default). The photos are shown one after another and repeated until the audio ends.
4. If you add no photos, choose a Background Color instead: Black, White, Red, Green, Blue, Yellow, Magenta, Cyan, Gray, Orange or Purple. The choice None is only valid when you add at least one photo.
5. Leave Auto crop images to 16:9 aspect ratio checked to fit each photo inside a 1920 by 1080 frame. With a single photo you can uncheck it to keep the photo's own proportions. A background color alone always gives a 1920 by 1080 video.
6. Press Convert. The button becomes Pause and Resume while the video is made. A tone plays when it is finished.

The new MP4 is saved next to the MP3 with the same name. If that name is taken, a number is added.

### Trim Audio/Video File

Cut a part out of an audio or video file.

1. Select a file in File Explorer and choose Trim Audio/Video File. If several files are selected, the first one is used.
2. Choose the Output Type: Audio or Video.
3. Enter the Start Time and End Time. Use seconds (10), minutes and seconds (1:30) or hours, minutes and seconds (1:30:45). The End Time is filled in with the length of the file. A line below shows the file duration and the length of the trimmed part.
4. For Audio output, choose MP3 (320, 256, 192 or 128 kbps) or WAV.
5. For Audio output you can check Enable Fade In/Out. Fade In has a Start Time and an End Time (100% volume), and Fade Out has a Start Time (100% volume) and an End Time. Each fade must lie inside the trimmed part.
6. For Video output, choose MP4, MKV, MOV, AVI or WebM. The video is copied at its original quality with no re-encoding. WebM is only possible for videos that already use VP8, VP9 or AV1; otherwise xTrack asks you to choose MP4 or MKV.
7. Edit the Output Filename if you wish. It starts as the original name followed by `_trimmed`, and characters that Windows does not allow are refused.
8. Press Preview (first 15 seconds) to hear or see the start of your selection in your default player before committing.
9. Press Trim. The new file is saved next to the original, and a number is added if the name is already used.

Audio output is saved at 44.1 kHz. The last settings you used are remembered.

### Merge MP3

Join several audio tracks into one MP3, with four ways of doing it.

1. Select two or more MP3 files in File Explorer and choose Merge MP3. The tracks appear in a list with their durations.
2. Open the Mode list and choose how to merge: Merge whole tracks, Merge selected sections of tracks, Merge with gap or silence between tracks, or Merge with fade at each section. NVDA explains the chosen mode.
3. Arrange the tracks. Open the list's context menu with the Applications key or Shift+F10 to Move Up, Move Down, Delete Track, Add Track or Add Folder. The Delete key also removes the selected track. Add Folder adds every audio file in the folder in natural number order.
4. Type the Output Filename. It starts as merged_audio and .mp3 is added for you.
5. Choose the quality: Re-encode with quality (320, 256, 192 or 128 kbps), or Copy without re-encoding (faster), which is only available when every track is an MP3 and you merge whole tracks without a cross-fade.
6. Press Merge. The button becomes Pause and Resume, and progress is spoken every 10 percent. The merged file is saved in the folder of the files you selected.

**Merge whole tracks** joins the tracks as they are. Optionally check Enable Cross-fade between tracks, so the end of each track overlaps the beginning of the next one while the first fades out. Set the Cross-fade Duration (1 to 30 seconds, 5 by default) and an extra Fade-in for Next Track (0 to 10 seconds). A cross-fade needs re-encoding, and no track may be shorter than the cross-fade.

**Merge selected sections of tracks** uses only part of each track. Select a track in the list, then type where its section starts and ends in minutes, seconds and hundredths, for example `01:30:00`. The last part is a fraction of a second, so `01:30:50` is one minute and thirty and a half seconds. Press Preview Section to listen to it. Cross-fade can be combined with this mode.

**Merge with gap or silence between tracks** puts silence between the tracks. Choose the length from 0.1 to 60 seconds; the default is 2.

**Merge with fade at each section** works like sections, and every section also gets its own fade in and fade out (0 to 60 seconds, 1 by default). The two fades together cannot be longer than the section.

In the section, gap and fade modes, a Project Actions button and an Update Project button appear. Project Actions offers Save Project and Load Project. A project is a small file (`merge_project.json` by default) that remembers the tracks, their order, the sections, fades, gap, cross-fade, quality and output name. After you save or load a project, Update Project saves your latest changes into it, and pressing Merge updates it automatically. When a project is loaded, tracks that can no longer be found are left out and NVDA tells you which ones.

### Split Audio

Cut one audio file into a number of tracks at times you choose.

1. Select one audio file in File Explorer and choose Split Audio.
2. Set Number of tracks, from 2 to 50.
3. A row appears for each track, labelled with its number and End Time. xTrack fills them in so the file is divided into equal parts, and the last track always ends at the end of the file. Change any time you like, using seconds, minutes and seconds, or hours, minutes and seconds.
4. Make sure the end times keep increasing, the first track ends after 0:00, and the last time does not go past the length of the file.
5. Press Split.

The tracks are cut without re-encoding, so there is no loss in quality. They are saved next to the original as `name_track01`, `name_track02` and so on, with the original extension. If some tracks fail, NVDA tells you which ones. The number of tracks is remembered.

### Split Album By Tracks

Split a long recording, such as a whole album or a concert, into separate songs by finding the silent gaps between them, or by reading a CUE sheet.

1. Select one audio file in File Explorer and choose Split Album By Tracks. The file is analysed right away, and NVDA announces how many tracks were found.
2. Review the Tracks found list. Each track is shown with its title, start, end and length. Select a track and change its title in the Track title box.
3. If the result is not what you expect, change the settings in the Detection group and press Analyze again.
4. Press Preview 15 seconds to hear the beginning of the selected track.
5. Press Split. The tracks are saved in a new folder next to the album file, named after the album. If that folder exists, a number is added to its name.

The Detection settings are:

- **Minimum silence between tracks (seconds, 0 = automatic)** : with 0, xTrack chooses the best value itself.
- **Expected number of tracks** : if you know how many songs there are, enter it to help the automatic mode. 0 means not known.
- **Silence level (dB)** : the loudness below which sound counts as silence. The default is -40.
- **Level of complete silence at the end of a track (dB)** : a lower value keeps more of a fade out. The default is -70.
- **Silence before each track** and **Silence after each track** (seconds, 0 = none) : the quiet lead-in and tail each song should have. Where a track has less, silence is added. The defaults are 0.5.
- **Shortest track (seconds)** : a piece shorter than this is joined to its neighbour. The default is 10.

To use a CUE sheet instead, press Import CUE sheet and choose the .cue file. The tracks and titles in the sheet replace the detected ones. Only sheets for a single album file are supported, and entries that do not fit the file are skipped.

Files are named like `01 - Title.mp3`. The title and track number (for example 3/12) are written into each file, and the other tags of the original are kept. Tracks are cut without re-encoding, except when silence has to be added at their start or end. Your detection settings are remembered.

### Split Track by Silent

Remove silence from the start and the end of songs. Silence in the middle of a song is never touched. Several files can be processed together.

1. Select one or more audio files in File Explorer and choose Split Track by Silent. The files are analysed right away.
2. NVDA announces how many files have silence to trim. Review the Tracks list: each file says how many seconds will be cut at the start and at the end and what the new length will be, or that there is no silence to trim, or that only silence was found and the file is left alone.
3. Select a file and change its name in the Track title box if you wish.
4. Adjust the settings if needed and press Analyze again.
5. Press Preview beginning or Preview ending to hear the first or last 8 seconds of the trimmed song.
6. Optionally check Remove original track (sent to the Recycle Bin). You are asked to confirm before anything is done.
7. Press Split.

The settings are Shortest silence to trim (seconds, 0.3 by default), Silence level (dB, -45 by default), Level of complete silence at the end of a song (dB, -70 by default, a lower value keeps more of a fade out), Silence to keep at the start (0.2 seconds) and Silence to keep at the end (0.3 seconds).

Without the removal option, the trimmed songs are saved in a folder named Trimmed next to the originals. With it, the trimmed song is saved in the same folder as the original, and the original is sent to the Recycle Bin. Songs are cut without re-encoding and their tags are kept.

### MP3 Gain

Bring MP3 and WAV files to the same loudness. The original files are never overwritten: a new file is always created.

1. Select files or folders in File Explorer and choose MP3 Gain, or open it from anywhere and use Browse Files or Browse Folder to add MP3 and WAV files. A folder is searched together with its subfolders.
2. Set the "Normal" Volume (dB), the loudness every file should reach. It goes from 40 to 120 and starts at 89.
3. Choose where the new files go. Keep in Same Folder is on by default. Turn it off to choose a Destination Folder; files that came from a folder keep their subfolder structure there.
4. Under analysis, choose Track Analysis, Album Analysis or Clear Analysis and press Check. Analysis only reads the files and shows each file's loudness in the list, for example `song.mp3 (86.4 dB)`.
5. Under gain, choose Apply Track Gain or Apply Album Gain and press Apply.

**Track** actions treat every file on its own, so every song ends up at the same loudness. **Album** actions group files by the folder that holds them and use the average of that folder, so the differences between the songs of an album are kept. When you import a parent folder with several album subfolders, each subfolder is treated as its own album. Apply measures a file first if it was not analysed.

New files are named like `song_gain_89dB.mp3`. MP3 files keep their original bitrate, WAV files stay 16-bit, and a limiter is used when the sound is made louder. NVDA tells you when each file is created and when everything is done.

### Mixdown

Improve or transform MP3 and WAV files with ready-made presets or your own combination of effects. The original files are never overwritten.

1. Select files or folders in File Explorer and choose Mixdown, or open it from anywhere and use Browse Files or Browse Folder. Press Delete on the list to remove files.
2. Choose a Preset. The first choice, Custom, lets you set every effect yourself. The second, Equalizer, lets you choose a Music Style: Pop, Rock, Dance / EDM, Hip-Hop, Jazz, Folk / Acoustic, Classical, R&B / Soul or Metal. The others are ready-made: Flat / Default, Bass Boosted Warmth, Vocal Clarity & Denoise (ANR), Cinematic Binaural Space, Dreamy Chorus & Wide, Reversed & Accelerated, Muffled Room (Pub / Bathroom), Broadcast Standard (EBU R128), Podcast Clarity, Music Master and Bass & Treble Boost. A short description of the chosen preset is shown.
3. With Custom, go through the effect tabs and tick the effects you want. Each effect shows its own settings only when it is ticked.
4. Set Preview start and Preview length (5 to 120 seconds, 15 by default), then press Preview to hear the result in your default player. Press Preview Original (A/B) to hear the same part without effects.
5. Press Apply to process every file in the list. If your settings contain effects that work against each other, you are warned and asked whether to continue.

The effect tabs are:

- **Noise** : Noise Reduction / Suppression, Noise Gate, Rumble Filter, Click and Crackle Remover, De-esser and Silence Trimmer.
- **Tempo and Speed** : Adjust Tempo (with an option to keep the original pitch), Rewind (backwards) or Fast-forward, Fast Speed (1x to 10x) and Nightcore or Slowed.
- **Equalizer** : Bass / Treble, a 3-band Parametric Equalizer, Voice Clarity, Loudness Contour and Tilt EQ.
- **Spatial and Panning** : Channel Tools (Stereo to Mono, Mono to Stereo, Swap Left and Right), Binaural Audio, Chorus, Flanger / Phaser / Tremolo / Vibrato, Haas Effect, Reverb / Echo, Multi-Room Reverb, Muffled Room, an Effects Mix percentage, Auto-Pan Loop (with the option to keep the singer's voice in the center) and Advanced Time-Based Panning with up to 50 sections.
- **Dynamics** : Volume Gain, Expander / Compand (Night Mode), Dynamic Normalizer, Speech Normalizer, Compressor, Soft Clipper / Saturation, Lo-fi / Bitcrusher, Loudness Normalization (EBU R128, with an optional two-pass mode) and Limiter, plus an option to report the loudness before and after in LUFS.
- **Creative** : Telephone / Radio Voice, Bass Boost, and Frequency Shift (robot voice).

Press Preset Actions to Load Preset, Save Current Settings, Delete Preset, Reset All Effects, or Read Effect Summary, which lists the effects that are switched on and any warnings. Your own presets are saved in a file in xTrack's settings folder, and your last Custom settings come back the next time you open Mixdown. Reset Custom Effect returns every effect to its default.

New files are saved next to the originals and named after the preset, for example `song_bassboost.mp3`, `song_podcast.mp3` or `song_custom.mp3`. Custom names also carry tags such as `rewind`, the tempo or the speed. Rewind works on files up to 20 minutes long. If your copy of ffmpeg does not include an effect, that effect is switched off and NVDA tells you which ones.

### Resize Image

Resize, crop and convert one or many images.

1. Select image files in File Explorer and choose Resize Image. Each file is listed with its size in pixels and its file size. Press Delete or use the context menu to take files out of the list.
2. Set the Width and Height in pixels, from 1 to 30000. When you move to another image in the list, they are set to that image's size. With Lock Aspect Ratio on (the default), changing the width changes the height to match, and the other way round.
3. Optionally check Crop Image and enter how many pixels to remove from the Top, Bottom, Left and Right.
4. Choose the Quality: Best (No loss), Very Good, Good, Normal or Small.
5. Choose the Format: JPEG, WEBP, PNG, TIFF, BMP or GIF.
6. Choose the Output folder. It starts as the folder of the first image.
7. When only one image is selected you can type an Output Filename Pattern. By default each result is named after the original with `_resized`.
8. Press Start. The list shows the output name, dimensions, file size and status of each image, and NVDA announces every finished image with its new size.

If a file name already exists, a number in brackets is added so nothing is overwritten. Files that cannot be read are skipped and counted. Your settings are remembered.

### Image Info and Video Info

Hear the details of the selected files without opening them.

- **Image Info** speaks the width and height in pixels and the DPI stored in the file, for example "1920 by 1080 pixels, 300 DPI". If the file has no DPI, NVDA says "DPI not stored in file".
- **Video Info** speaks the width and height in pixels, the frame rate and the file size.

When several files are selected, each result begins with the file name.

### Automatic Update Check

xTrack checks GitHub once a day for a newer release, so you do not have to look for updates yourself.

1. About 30 seconds after NVDA starts, xTrack asks GitHub for the latest release of xTrack. If the network is not ready, it tries again after 1, 3 and 10 minutes, then waits until the next day.
2. If a newer version exists, a dialog tells you the new version and the one installed, with the buttons Update now and Later. Escape does the same as Later.
3. Choose Update now. NVDA says "Downloading xTrack" followed by the version, and the add-on package is downloaded in the background. When the release provides a checksum, the download is verified against it.
4. NVDA's own add-on installer then takes over, so you confirm the installation and restart NVDA in the usual way.

If the download fails, NVDA says "Could not download the update. Opening the releases page." and the releases page opens in your browser. Draft and pre-release versions are ignored. The check is skipped when xTrack was installed from the NVDA Add-on Store, because the Store handles updates, and when NVDA runs in secure mode. The date of the last check is kept in the same settings folder as xTrack.json.

### Settings and Shared Tools

xTrack keeps its settings in a file named xTrack.json, inside a ChaiChaimee folder in the NVDA configuration folder that is actually in use, whether NVDA is installed, portable or started with a custom configuration path. Settings from earlier versions are moved or copied there automatically. The file is saved safely, with a backup, so a power cut cannot leave it half written.

The conversion, trim, merge, split, gain and resize tools use ffmpeg.exe and ffprobe.exe. They are kept in a shared folder named bin inside the same ChaiChaimee folder, so every add-on of the same author can use one copy. If they are missing, NVDA says "ffmpeg.exe or ffprobe.exe not found". The add-on works with both 32-bit and 64-bit versions of NVDA. When it is installed, the audio libraries for the other architecture are removed, so only the matching ones stay on disk.

## Support Me

If this tool has made your life easier, consider fueling the next update with a small donation.

[![Support me](https://img.shields.io/badge/Donate-Support%20Me-blue?style=for-the-badge&logo=stripe)](https://buy.stripe.com/dRm9AU1xQ3Ds22N6VK1VK01)

Your support means the world. Let's build something great together

© 2026 Chai Chaimee NVDA Add-on Released under GNU GPL
