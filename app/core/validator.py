"""
URL and parameter validation routines.
"""

import re
from urllib.parse import urlparse
from typing import Tuple, Optional


class Validator:
    """Validates YouTube and generic video/audio URLs and options."""

    YOUTUBE_DOMAINS = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "music.youtube.com",
    }

    YOUTUBE_VIDEO_REGEX = re.compile(
        r"(?:v=|\/embed\/|\/watch\?v=|\/shorts\/|youtu\.be\/)([a-zA-Z0-9_-]{11})"
    )
    YOUTUBE_CHANNEL_REGEX = re.compile(
        r"(?:channel\/|c\/|user\/|@)([a-zA-Z0-9_\-\.]+)"
    )
    YOUTUBE_PLAYLIST_REGEX = re.compile(
        r"[?&]list=([a-zA-Z0-9_\-]+)"
    )

    @classmethod
    def is_valid_url(cls, url: str) -> bool:
        if not url or not isinstance(url, str):
            return False
        url = url.strip()
        try:
            result = urlparse(url)
            return all([result.scheme in ("http", "https"), result.netloc])
        except Exception:
            return False

    @classmethod
    def is_youtube_url(cls, url: str) -> bool:
        if not cls.is_valid_url(url):
            return False
        try:
            parsed = urlparse(url.strip())
            netloc = parsed.netloc.lower()
            return any(netloc == domain or netloc.endswith("." + domain) for domain in cls.YOUTUBE_DOMAINS)
        except Exception:
            return False

    @classmethod
    def extract_youtube_video_id(cls, url: str) -> Optional[str]:
        if not url:
            return None
        match = cls.YOUTUBE_VIDEO_REGEX.search(url.strip())
        return match.group(1) if match else None

    @classmethod
    def is_youtube_channel_or_playlist(cls, url: str) -> bool:
        if not cls.is_youtube_url(url):
            return False
        url_clean = url.strip()
        if cls.YOUTUBE_PLAYLIST_REGEX.search(url_clean):
            return True
        if "/videos" in url_clean or "/shorts" in url_clean or "/streams" in url_clean or "/featured" in url_clean:
            return True
        if cls.YOUTUBE_CHANNEL_REGEX.search(url_clean):
            return True
        return False
