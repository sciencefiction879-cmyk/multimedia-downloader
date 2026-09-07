"""
yt-dlp wrapper for metadata extraction, stream resolution, and playlist expansion.
"""

import os
from pathlib import Path
from typing import Dict, Any, List, Optional
import yt_dlp
from app.core.exceptions import MetadataError
from app.downloader.speed_optimizer import SpeedOptimizer
from app.models.settings_model import Settings
from app.utils.logger import logger


class YtdlpClient:
    """Provides high-level extraction functions using yt-dlp."""

    @staticmethod
    def is_playlist_url(url: str) -> bool:
        if not url:
            return False
        return "list=" in url or "/playlist" in url or "/sets/" in url

    @classmethod
    def _get_base_opts(cls, flat: bool = False) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": flat,
            "socket_timeout": 20,
        }
        js_info = SpeedOptimizer.get_js_runtime()
        if js_info:
            opts["js_runtimes"] = {js_info[0]: {"path": js_info[1]}}
            opts["remote_components"] = ["ejs:github"]
        return opts

    @classmethod
    def fetch_metadata(cls, url: str) -> Dict[str, Any]:
        ydl_opts = cls._get_base_opts(flat=False)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise MetadataError("No metadata returned by yt-dlp.")
                return info
        except Exception as e:
            logger.error(f"Failed to fetch metadata for {url}: {e}")
            raise MetadataError(f"Failed to fetch video metadata: {str(e)}")

    @classmethod
    def extract_playlist_entries(cls, url: str) -> List[Dict[str, Any]]:
        ydl_opts = cls._get_base_opts(flat=True)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return []
                entries = info.get("entries", [])
                return [e for e in entries if e is not None]
        except Exception as e:
            logger.error(f"Failed to extract playlist entries for {url}: {e}")
            raise MetadataError(f"Failed to extract playlist: {str(e)}")
