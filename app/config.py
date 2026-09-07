"""
Application configuration, defaults, and constant definitions for Multi Downloader on macOS.
"""

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "Multi Downloader"
APP_VERSION = "2.8.0"
ORGANIZATION = "Antigravity Engineering"






BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
APP_ICON_ICO = ASSETS_DIR / "icon.ico"
APP_ICON_PNG = ASSETS_DIR / "icon.png"
APP_LOGO_PNG = ASSETS_DIR / "logo.png"

USER_HOME = Path.home()
DEFAULT_DOWNLOAD_DIR = USER_HOME / "Downloads" / "MultiDownloader"
DEFAULT_AUDIO_DIR = DEFAULT_DOWNLOAD_DIR / "Audio"
DEFAULT_VIDEO_DIR = DEFAULT_DOWNLOAD_DIR / "Video"
DEFAULT_CHANNELS_DIR = DEFAULT_DOWNLOAD_DIR / "Channels"
DEFAULT_DIRECT_DIR = DEFAULT_DOWNLOAD_DIR / "Direct"
DEFAULT_AIVOICE_DIR = DEFAULT_DOWNLOAD_DIR / "AIVoice"

if sys.platform == "darwin":
    APP_DATA_DIR = USER_HOME / "Library" / "Application Support" / "MultiDownloader"
elif os.name == "nt":
    LOCAL_APP_DATA = os.environ.get("LOCALAPPDATA", str(USER_HOME / "AppData" / "Local"))
    APP_DATA_DIR = Path(LOCAL_APP_DATA) / "MultiDownloader"
else:
    APP_DATA_DIR = USER_HOME / ".config" / "multidownloader"

CONFIG_FILE = APP_DATA_DIR / "settings.json"
HISTORY_DB = APP_DATA_DIR / "history.db"
SECTIONS_FILE = APP_DATA_DIR / "sections.json"
QUEUE_FILE = APP_DATA_DIR / "queue.json"
LOGS_DIR = APP_DATA_DIR / "logs"
LOG_FILE = LOGS_DIR / "application.log"

APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_CHANNELS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONCURRENT_DOWNLOADS = 3
MIN_CONCURRENT_DOWNLOADS = 1
MAX_CONCURRENT_DOWNLOADS = 3
DEFAULT_MAX_RETRIES = 3


SEGMENT_CONNECTIONS_OPTIONS = ("AUTO", "1", "2", "4", "8", "16", "32")
DEFAULT_SEGMENT_CONNECTIONS = "AUTO"

CHANNEL_FETCH_RANGES = (10, 25, 50, 75, 100, 250, 500)
DEFAULT_CHANNEL_FETCH_COUNT = 50

ORDER_LATEST_TO_OLDEST = "Latest → Oldest (New to Old: V1=Newest)"
ORDER_OLDEST_TO_LATEST = "Oldest → Latest"
CHANNEL_ORDER_OPTIONS = (ORDER_LATEST_TO_OLDEST, ORDER_OLDEST_TO_LATEST)

AUDIO_FORMATS = ("MP3", "M4A", "WAV", "FLAC", "OPUS", "AAC")
AUDIO_QUALITIES = (
    "64 kbps (Ultra Low MB - ~28MB/hr)",
    "128 kbps (Recommended - ~55MB/hr)",
    "160 kbps (Standard - ~72MB/hr)",
    "192 kbps (High Quality - ~86MB/hr)",
    "256 kbps (Studio Quality - ~115MB/hr)",
    "320 kbps (Maximum - ~144MB/hr)",
    "Best Available",
)
DEFAULT_AUDIO_QUALITY = "128 kbps (Recommended - ~55MB/hr)"

AUDIO_SAMPLE_RATES = ("Auto", "44100 Hz", "48000 Hz")
AUDIO_CHANNELS = ("Auto", "Stereo", "Mono")

VIDEO_QUALITIES = (
    "Best Available",
    "2160p (4K)",
    "1440p (2K)",
    "1080p (FHD)",
    "720p (HD)",
    "480p (SD)",
    "360p",
    "240p",
)
VIDEO_FORMATS = ("MP4", "MKV", "WEBM")
VIDEO_FPS_OPTIONS = ("Best Available", "60", "50", "30", "25", "24")
VIDEO_CODECS = ("Auto", "H.264", "H.265/HEVC", "VP9", "AV1")

FILENAME_TEMPLATES = (
    "{version}_{title}",
    "{version}",
    "{title}",
    "{channel} - {title}",
    "{upload_date} - {title}",
    "{channel} - {upload_date} - {title}",
)

ZIP_COMPRESSION_MODES = ("STORE (Fastest / No CPU overhead)", "FAST", "NORMAL", "HIGH")
DEFAULT_THEME = "dark"
LOG_LEVELS = ("INFO", "WARNING", "ERROR", "DEBUG")
DEFAULT_LOG_LEVEL = "INFO"
MAX_FILENAME_LENGTH = 180
