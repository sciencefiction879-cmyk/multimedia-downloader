"""
Settings data model with JSON persistence and macOS default directories.
"""

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
from app.config import (
    CONFIG_FILE,
    DEFAULT_DOWNLOAD_DIR,
    DEFAULT_AUDIO_DIR,
    DEFAULT_VIDEO_DIR,
    DEFAULT_CHANNELS_DIR,
    DEFAULT_THEME,
    DEFAULT_AUDIO_QUALITY,
    DEFAULT_CONCURRENT_DOWNLOADS,
    MAX_CONCURRENT_DOWNLOADS,
    MIN_CONCURRENT_DOWNLOADS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_LOG_LEVEL,
    ORDER_LATEST_TO_OLDEST,
)


@dataclass
class Settings:
    download_dir: str = str(DEFAULT_DOWNLOAD_DIR)
    audio_dir: str = str(DEFAULT_AUDIO_DIR)
    video_dir: str = str(DEFAULT_VIDEO_DIR)
    channels_dir: str = str(DEFAULT_CHANNELS_DIR)

    theme: str = DEFAULT_THEME
    default_audio_format: str = "MP3"
    default_audio_quality: str = DEFAULT_AUDIO_QUALITY
    default_audio_samplerate: str = "Auto"
    default_audio_channels: str = "Auto"

    default_video_format: str = "MP4"
    default_video_quality: str = "1080p (FHD)"
    default_video_fps: str = "Best Available"
    default_video_codec: str = "Auto"

    concurrent_downloads: int = DEFAULT_CONCURRENT_DOWNLOADS
    max_retries: int = DEFAULT_MAX_RETRIES
    segment_connections: str = "AUTO"
    gpu_acceleration: bool = True
    auto_download_transcripts: bool = True
    default_channel_order: str = ORDER_LATEST_TO_OLDEST
    default_fetch_count: int = 50

    # Cookie acceleration & authentication
    use_browser_cookies: bool = False
    cookie_browser: str = "chrome"
    custom_cookie_file: str = ""

    # Speed limit bypass & acceleration
    bypass_throttling: bool = True
    use_aria2c: bool = True
    force_ipv4: bool = False
    po_token: str = ""
    preferred_player_client: str = "auto"

    log_level: str = DEFAULT_LOG_LEVEL


    @classmethod
    def load(cls, file_path: Path = CONFIG_FILE) -> "Settings":
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    s = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
                    s.concurrent_downloads = min(MAX_CONCURRENT_DOWNLOADS, max(MIN_CONCURRENT_DOWNLOADS, s.concurrent_downloads))
                    return s
            except Exception:
                pass
        s = cls()
        s.save(file_path)
        return s


    def save(self, file_path: Path = CONFIG_FILE):
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)
