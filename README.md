# Multi Downloader 🚀

A modern, high-speed YouTube Channel & Media Downloader and Competitor Extraction Suite for macOS and Windows.

![Python](https://img.shields.io/badge/Python-3.12-blue.svg)
![Qt](https://img.shields.io/badge/GUI-PySide6-green.svg)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS-orange.svg)
![Release](https://img.shields.io/badge/Release-v2.8.0-blue.svg)

---

## ✨ Key Features

1. **Competitor Metadata Extraction & Purification**:
   - Extracts complete metadata for every video from V1 onward.
   - **Purification**: Automatically removes competitor channel branding, external URLs/links, promotional links, affiliate codes, sponsor plugs, and CTAs ("subscribe", "leave a like").
   - Saves purified metadata into a dedicated `Metadata/` folder as `V1 Metadata.txt`, `V2 Metadata.txt`, etc.

2. **Sequential Competitor Thumbnails**:
   - Downloads highest-resolution thumbnails sequentially (`V1 Thumbnail.jpg`, `V2 Thumbnail.jpg`, ...).
   - Saved into a dedicated `Thumbnails/` folder.

3. **Competitor Channel Assets**:
   - Extracts and downloads high-resolution channel banners (`Channel Banner.jpg`) and avatars/logos (`Channel Logo.png`).
   - Saved into a dedicated `Channel Assets/` folder.

4. **Organized Titles & Scripts**:
   - Saves all title TXT files in a single `Titles/` folder with:
     - **Line 1**: Competitor Channel Name
     - **Line 2**: Competitor Channel Link
     - **Line 3**: Sequential Video Title (`V{i}. Title`)
   - Cleans voiceover transcripts and exports readable paragraphs to `Scripts/` (`V1 Script.txt`...).

5. **High-Speed MP3 Voiceovers**:
   - High-speed concurrent audio extraction in user-selectable bitrates (64kbps to 320kbps).
   - Parallel download queue with real-time speed, ETA, and progress indicators.

6. **1-Click Complete Package**:
   - Single-click **"DOWNLOAD COMPLETE COMPETITOR PACKAGE"** button automatically extracts and organizes all 6 assets into clean subdirectories.

---

## 📂 Output Folder Organization

```text
Output Folder/
├── Titles/
│   ├── V1 Title.txt
│   ├── V2 Title.txt
│   └── titles.txt
├── Scripts/
│   ├── V1 Script.txt
│   ├── V2 Script.txt
│   └── ...
├── Metadata/
│   ├── V1 Metadata.txt
│   ├── V2 Metadata.txt
│   └── ...
├── Thumbnails/
│   ├── V1 Thumbnail.jpg
│   ├── V2 Thumbnail.jpg
│   └── ...
├── Channel Assets/
│   ├── Channel Banner.jpg
│   └── Channel Logo.png
├── V1.mp3
├── V2.mp3
└── ...
```

---

## 📦 Downloads & Releases

Pre-compiled standalone binaries are available on the [Releases](https://github.com/sciencefiction879-cmyk/multimedia-downloader/releases) page:
- **Windows (x64)**: `MultiDownloader.exe` (Single-file standalone application)
- **macOS (Apple Silicon & Intel)**: `MultiDownloader.dmg`

---

## 🛠️ Building From Source

### Prerequisites
- Python 3.10+
- FFmpeg (optional if using bundled `imageio-ffmpeg`)

### Installation
```bash
git clone https://github.com/sciencefiction879-cmyk/multimedia-downloader.git
cd multimedia-downloader
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Run
```bash
python main.py
```

### Build Executable
- **macOS**: `bash build_macos.sh`
- **Windows**: `build_windows.bat` or `python -m PyInstaller MultiDownloader.spec`
