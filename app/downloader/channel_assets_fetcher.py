"""
Competitor thumbnails and channel assets (Banner & Logo) downloader.
Downloads sequential video thumbnails: V1 Thumbnail.jpg, V2 Thumbnail.jpg...
Downloads competitor channel banner and logo/avatar into Channel Assets folder.
"""

import os
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from PySide6.QtGui import QImage
import yt_dlp
from app.utils.logger import logger


class ChannelAssetsFetcher:
    """Handles downloading and organizing channel assets and video thumbnails."""

    USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

    @classmethod
    def _download_image_bytes(cls, url: str, timeout: int = 10) -> Optional[bytes]:
        """Fetches raw image bytes with a modern desktop User-Agent."""
        if not url:
            return None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": cls.USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return resp.read()
        except Exception as e:
            logger.debug(f"Failed to download image from {url}: {e}")
        return None

    @classmethod
    def download_thumbnail_for_video(
        cls,
        video_id: str,
        version_label: str,
        output_dir: Path,
        fallback_thumb_url: str = "",
    ) -> Optional[Path]:
        """
        Downloads thumbnail for a video sequentially named:
        '{version_label} Thumbnail.jpg' (e.g. 'V1 Thumbnail.jpg').
        Tries maxresdefault -> sddefault -> hqdefault -> fallback URL.
        Converts/saves via QImage for optimal universal format compatibility.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{version_label} Thumbnail.jpg"

        # Sequential quality cascade
        urls_to_try = [
            f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
            f"https://i.ytimg.com/vi/{video_id}/sddefault.jpg",
            f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        ]
        if fallback_thumb_url and fallback_thumb_url not in urls_to_try:
            urls_to_try.append(fallback_thumb_url)

        for thumb_url in urls_to_try:
            data = cls._download_image_bytes(thumb_url)
            if data and len(data) > 1024:
                img = QImage.fromData(data)
                if not img.isNull():
                    # Successfully loaded image
                    img.save(str(out_file), "JPG", 95)
                    logger.debug(f"Saved {version_label} Thumbnail from {thumb_url}")
                    return out_file

        logger.warning(f"Could not download thumbnail for video {video_id} ({version_label})")
        return None

    @classmethod
    def download_all_thumbnails(
        cls,
        candidates: List[Any],
        output_dir: Path,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> List[Path]:
        """
        Downloads sequential thumbnails for all candidates into output_dir.
        Names them V1 Thumbnail.jpg, V2 Thumbnail.jpg...
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        saved: List[Path] = []
        total = len(candidates)

        for idx, cand in enumerate(candidates, start=1):
            if is_cancelled and is_cancelled():
                break

            vid_id = getattr(cand, "video_id", None) or (cand.get("video_id") if isinstance(cand, dict) else "")
            v_label = getattr(cand, "version_label", None) or (cand.get("version_label", f"V{idx}") if isinstance(cand, dict) else f"V{idx}")
            title = getattr(cand, "title", None) or (cand.get("title", f"Video {idx}") if isinstance(cand, dict) else f"Video {idx}")
            thumb = getattr(cand, "thumbnail", None) or (cand.get("thumbnail", "") if isinstance(cand, dict) else "")

            if progress_callback:
                progress_callback(idx, total, f"{v_label}: {title}")

            saved_path = cls.download_thumbnail_for_video(
                video_id=vid_id,
                version_label=v_label,
                output_dir=output_dir,
                fallback_thumb_url=thumb,
            )
            if saved_path:
                saved.append(saved_path)

        logger.info(f"Downloaded {len(saved)} thumbnails to {output_dir}")
        return saved

    @classmethod
    def extract_channel_images_urls(cls, channel_url: str) -> Dict[str, Optional[str]]:
        """
        Extracts high-resolution Banner and Logo/Avatar URLs from YouTube channel URL.
        """
        result: Dict[str, Optional[str]] = {
            "banner_url": None,
            "logo_url": None,
            "channel_name": None,
            "channel_url": channel_url,
        }

        if not channel_url:
            return result

        target_url = channel_url.strip()
        if (
            ("youtube.com/@" in target_url or "youtube.com/c/" in target_url or "youtube.com/channel/" in target_url)
            and not any(tab in target_url for tab in ["/videos", "/shorts", "/playlists", "/streams", "/featured"])
        ):
            target_url = target_url.rstrip("/") + "/videos"

        ydl_opts = {
            "extract_flat": True,
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(target_url, download=False)
                if not info:
                    return result

                result["channel_name"] = info.get("channel") or info.get("uploader") or ""
                result["channel_url"] = info.get("channel_url") or info.get("uploader_url") or channel_url

                thumbnails = info.get("thumbnails", [])
                banner_cands = []
                avatar_cands = []

                for t in thumbnails:
                    t_id = str(t.get("id") or "").lower()
                    url = t.get("url")
                    if not url:
                        continue

                    w = t.get("width")
                    h = t.get("height")

                    # Banner detection
                    if "banner" in t_id or (w and h and (w / h) > 2.2):
                        w_val = w or 0
                        banner_cands.append((w_val, url))

                    # Avatar/Logo detection
                    if "avatar" in t_id or (w and h and 0.95 <= (w / h) <= 1.05):
                        w_val = w or 0
                        avatar_cands.append((w_val, url))

                # Pick highest resolution banner
                if banner_cands:
                    banner_cands.sort(key=lambda x: x[0], reverse=True)
                    result["banner_url"] = banner_cands[0][1]

                # Pick highest resolution avatar/logo
                if avatar_cands:
                    avatar_cands.sort(key=lambda x: x[0], reverse=True)
                    result["logo_url"] = avatar_cands[0][1]

        except Exception as e:
            logger.error(f"Error extracting channel images: {e}")

        return result

    @classmethod
    def download_channel_assets(
        cls,
        channel_url: str,
        output_dir: Path,
        banner_url: Optional[str] = None,
        logo_url: Optional[str] = None,
    ) -> Dict[str, Optional[Path]]:
        """
        Downloads Channel Banner and Channel Logo to output_dir ('Channel Assets').
        Returns dict with 'banner_path' and 'logo_path'.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        result: Dict[str, Optional[Path]] = {
            "banner_path": None,
            "logo_path": None,
        }

        # If URLs not already provided, extract from channel_url
        if not banner_url or not logo_url:
            extracted = cls.extract_channel_images_urls(channel_url)
            banner_url = banner_url or extracted.get("banner_url")
            logo_url = logo_url or extracted.get("logo_url")

        # 1. Download Channel Banner
        if banner_url:
            b_data = cls._download_image_bytes(banner_url)
            if b_data:
                img = QImage.fromData(b_data)
                if not img.isNull():
                    b_path = output_dir / "Channel Banner.jpg"
                    img.save(str(b_path), "JPG", 95)
                    result["banner_path"] = b_path
                    logger.info(f"Downloaded Channel Banner to {b_path}")

        # 2. Download Channel Logo / Profile Image
        if logo_url:
            l_data = cls._download_image_bytes(logo_url)
            if l_data:
                img = QImage.fromData(l_data)
                if not img.isNull():
                    l_path = output_dir / "Channel Logo.png"
                    img.save(str(l_path), "PNG")
                    result["logo_path"] = l_path
                    logger.info(f"Downloaded Channel Logo to {l_path}")

        return result
