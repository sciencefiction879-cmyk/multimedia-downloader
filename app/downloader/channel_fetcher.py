"""
Channel and playlist video extractor with New-to-Old V1..Vn ordering and candidate management.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional
import yt_dlp
from app.config import ORDER_LATEST_TO_OLDEST, ORDER_OLDEST_TO_LATEST
from app.core.exceptions import MetadataError
from app.utils.logger import logger


@dataclass
class ChannelCandidate:
    video_id: str
    url: str
    title: str
    duration: float = 0.0
    duration_str: str = "00:00"
    upload_date: str = ""
    uploader: str = ""
    channel_url: str = ""
    thumbnail: str = ""
    is_selected: bool = True
    version_label: str = "V1"
    version_num: int = 1
    custom_title: Optional[str] = None
    match_score: float = 100.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any], index: int = 1, parent_channel: str = "", parent_channel_url: str = "") -> "ChannelCandidate":
        vid_id = data.get("id") or data.get("video_id") or ""
        url = data.get("url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else "")
        title = data.get("title") or "Untitled Video"
        dur = float(data.get("duration") or 0.0)

        # Format duration
        s = int(dur)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        dur_str = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

        # Upload date format YYYYMMDD -> YYYY-MM-DD
        raw_date = str(data.get("upload_date") or "")
        if len(raw_date) == 8 and raw_date.isdigit():
            date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        else:
            date_str = raw_date

        thumb = data.get("thumbnail") or (f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg" if vid_id else "")
        uploader = data.get("uploader") or data.get("channel") or parent_channel or ""
        c_url = data.get("channel_url") or data.get("uploader_url") or parent_channel_url or ""

        return cls(
            video_id=vid_id,
            url=url,
            title=title,
            duration=dur,
            duration_str=dur_str,
            upload_date=date_str,
            uploader=uploader,
            channel_url=c_url,
            thumbnail=thumb,
            version_label=f"V{index}",
            version_num=index,
        )


class ChannelFetcher:
    """Fetches video lists from YouTube channels or playlists with New-to-Old V1..Vn assignment."""

    def __init__(self):
        self.ydl_opts = {
            "extract_flat": True,
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
        }
        self.channel_name: str = ""
        self.channel_url: str = ""
        self.banner_url: str = ""
        self.logo_url: str = ""

    def fetch_channel_videos(
        self,
        url: str,
        max_results: int = 50,
        order: str = ORDER_LATEST_TO_OLDEST,
    ) -> List[ChannelCandidate]:
        logger.info(f"Fetching channel/playlist videos from: {url} (max: {max_results}, order: {order})")
        opts = dict(self.ydl_opts)
        opts["playlistend"] = max_results

        # Reset channel info
        self.channel_name = ""
        self.channel_url = url.strip()
        self.banner_url = ""
        self.logo_url = ""

        # Ensure correct channel tab URL if generic channel link
        target_url = url.strip()
        if (
            ("youtube.com/@" in target_url or "youtube.com/c/" in target_url or "youtube.com/channel/" in target_url)
            and not any(tab in target_url for tab in ["/videos", "/shorts", "/playlists", "/streams"])
        ):
            target_url = target_url.rstrip("/") + "/videos"

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target_url, download=False)
        except Exception as e:
            logger.error(f"Error fetching channel/playlist: {e}")
            raise MetadataError(f"Failed to fetch channel or playlist: {str(e)}")

        if not info:
            return []

        # Extract channel-level metadata
        self.channel_name = info.get("channel") or info.get("uploader") or ""
        self.channel_url = info.get("channel_url") or info.get("uploader_url") or self.channel_url

        # Parse banner and logo URLs from channel thumbnails
        thumbnails = info.get("thumbnails", [])
        banner_cands = []
        avatar_cands = []
        for t in thumbnails:
            t_id = str(t.get("id") or "").lower()
            t_url = t.get("url")
            if not t_url:
                continue
            w = t.get("width") or 0
            h = t.get("height") or 0
            if "banner" in t_id or (w and h and (w / h) > 2.2):
                banner_cands.append((w, t_url))
            if "avatar" in t_id or (w and h and 0.95 <= (w / h) <= 1.05):
                avatar_cands.append((w, t_url))

        if banner_cands:
            banner_cands.sort(key=lambda x: x[0], reverse=True)
            self.banner_url = banner_cands[0][1]
        if avatar_cands:
            avatar_cands.sort(key=lambda x: x[0], reverse=True)
            self.logo_url = avatar_cands[0][1]

        entries = []
        if "entries" in info:
            entries = [e for e in info["entries"] if e is not None]
        else:
            entries = [info]

        candidates = []
        for raw_entry in entries:
            cand = ChannelCandidate.from_dict(
                raw_entry,
                parent_channel=self.channel_name,
                parent_channel_url=self.channel_url,
            )
            if cand.video_id:
                candidates.append(cand)

        # Apply sorting:
        # ORDER_LATEST_TO_OLDEST (New to Old) is default:
        # YouTube returns newest first in /videos tab.
        # If user chooses Oldest -> Latest, reverse list.
        if order == ORDER_OLDEST_TO_LATEST:
            candidates.reverse()

        # Assign version labels V1, V2, V3... in exact order
        self.reassign_version_labels(candidates)
        logger.info(f"Successfully loaded {len(candidates)} video candidates with V1..V{len(candidates)} sequencing.")
        return candidates

    @staticmethod
    def reassign_version_labels(candidates: List[ChannelCandidate]):
        """Reassigns V1, V2, V3... labels sequentially from top to bottom."""
        for idx, cand in enumerate(candidates, start=1):
            cand.version_num = idx
            cand.version_label = f"V{idx}"
