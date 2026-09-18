"""
Channel and playlist video extractor with New-to-Old V1..Vn ordering and candidate management.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional
import yt_dlp
from app.config import (
    ORDER_LATEST_TO_OLDEST,
    ORDER_OLDEST_TO_LATEST,
    ORDER_POPULAR_TO_LEAST,
    ORDER_LEAST_TO_POPULAR,
)
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
    original_index: int = 0
    custom_title: Optional[str] = None
    match_score: float = 100.0
    view_count: int = 0
    view_count_str: str = ""

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

        raw_views = data.get("view_count")
        view_cnt = 0
        view_cnt_str = ""
        if raw_views is not None:
            try:
                view_cnt = int(raw_views)
                view_cnt_str = f"{view_cnt:,}"
            except (ValueError, TypeError):
                view_cnt = 0
                view_cnt_str = ""

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
            original_index=index,
            view_count=view_cnt,
            view_count_str=view_cnt_str,
        )


import json
import urllib.request


def _parse_views_to_int(view_text: str) -> int:
    """Parses strings like '1.2M views', '45K views', '1,234 views', '12M' into integer."""
    if not view_text:
        return 0
    clean = str(view_text).lower().replace("views", "").replace("view", "").replace(",", "").strip()
    try:
        if clean.endswith("k"):
            return int(float(clean[:-1].strip()) * 1_000)
        if clean.endswith("m"):
            return int(float(clean[:-1].strip()) * 1_000_000)
        if clean.endswith("b"):
            return int(float(clean[:-1].strip()) * 1_000_000_000)
        return int(float(clean))
    except Exception:
        return 0


def parse_view_count_input(val: Any) -> Optional[int]:
    """Parses user-entered view thresholds like '100k', '500K', '1M', '1,000,000', '50000', '100K+' into integer. Returns None if empty or invalid."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val) if val >= 0 else None
    s = str(val).strip().lower().replace(",", "").replace("views", "").replace("view", "").replace("+", "").strip()
    if not s:
        return None
    try:
        if s.endswith("k"):
            return int(float(s[:-1].strip()) * 1_000)
        if s.endswith("m"):
            return int(float(s[:-1].strip()) * 1_000_000)
        if s.endswith("b"):
            return int(float(s[:-1].strip()) * 1_000_000_000)
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _parse_duration_to_sec(dur_str: str) -> float:
    if not dur_str:
        return 0.0
    parts = dur_str.strip().split(":")
    try:
        if len(parts) == 3:
            return float(int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]))
        elif len(parts) == 2:
            return float(int(parts[0]) * 60 + int(parts[1]))
        elif len(parts) == 1:
            return float(int(parts[0]))
    except Exception:
        return 0.0
    return 0.0


class ChannelFetcher:
    """Fetches video lists from YouTube channels or playlists with accurate V1..Vn assignment."""

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
        max_results: Optional[int] = 50,
        order: str = ORDER_LATEST_TO_OLDEST,
        force_refresh: bool = False,
        min_views: Optional[int] = None,
        max_views: Optional[int] = None,
    ) -> List[ChannelCandidate]:
        logger.info(
            f"Fetching channel/playlist videos from: {url} (max: {max_results}, order: {order}, "
            f"min_views: {min_views}, max_views: {max_views}, force: {force_refresh})"
        )

        # Reset channel info
        self.channel_name = ""
        self.channel_url = url.strip()
        self.banner_url = ""
        self.logo_url = ""

        # Normalize channel URL
        target_url = url.strip()
        is_channel_url = any(k in target_url for k in ("youtube.com/@", "youtube.com/c/", "youtube.com/channel/", "youtube.com/user/"))
        if is_channel_url and not any(tab in target_url for tab in ["/videos", "/shorts", "/playlists", "/streams"]):
            target_url = target_url.rstrip("/") + "/videos"

        # -----------------------------------------------------------------
        # 1. Innertube direct chips:
        #    - ORDER_OLDEST_TO_LATEST -> "Oldest" chip
        #    - ORDER_POPULAR_TO_LEAST -> "Popular" chip
        # -----------------------------------------------------------------
        has_view_filter = (min_views is not None) or (max_views is not None)
        fetch_limit = max(int(max_results) * 5, 250) if (has_view_filter and max_results) else max_results

        if is_channel_url and order in (ORDER_OLDEST_TO_LATEST, ORDER_POPULAR_TO_LEAST):
            chip_to_use = "oldest" if order == ORDER_OLDEST_TO_LATEST else "popular"
            try:
                c_id, c_name, c_url, b_url, l_url, _ = self._resolve_channel_metadata(target_url)
                if c_name:
                    self.channel_name = c_name
                if c_url:
                    self.channel_url = c_url
                if b_url:
                    self.banner_url = b_url
                if l_url:
                    self.logo_url = l_url

                if c_id:
                    chip_candidates = self._fetch_channel_chip_innertube(
                        channel_id=c_id,
                        channel_name=self.channel_name,
                        channel_url=self.channel_url,
                        chip_name=chip_to_use,
                        max_results=fetch_limit,
                    )
                    if chip_candidates:
                        chip_candidates = self.filter_and_sort_candidates(
                            chip_candidates, order=order, min_views=min_views, max_views=max_views
                        )
                        if max_results and len(chip_candidates) > int(max_results):
                            chip_candidates = chip_candidates[: int(max_results)]
                            self.reassign_version_labels(chip_candidates)
                        logger.info(
                            f"Successfully fetched {len(chip_candidates)} videos via Innertube '{chip_to_use}' chip "
                            f"(V1: {chip_candidates[0].title if chip_candidates else 'None'})."
                        )
                        return chip_candidates
            except Exception as e:
                logger.warning(f"Innertube '{chip_to_use}' fetch fallback to yt-dlp: {e}")

        # -----------------------------------------------------------------
        # 2. Default yt-dlp fetch (used for Latest order, playlists, or fallback)
        # -----------------------------------------------------------------
        opts = dict(self.ydl_opts)
        if force_refresh:
            opts["no_cache_dir"] = True

        # Only cap yt-dlp playlistend if latest order without view filter
        if order == ORDER_LATEST_TO_OLDEST and not has_view_filter and max_results is not None and int(max_results) > 0:
            opts["playlistend"] = int(max_results)
        elif order in (ORDER_OLDEST_TO_LATEST, ORDER_POPULAR_TO_LEAST, ORDER_LEAST_TO_POPULAR) or has_view_filter:
            if max_results is not None and int(max_results) > 0:
                opts["playlistend"] = max(int(max_results) * 5, 250)
            else:
                opts.pop("playlistend", None)
        else:
            opts.pop("playlistend", None)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target_url, download=False)
        except Exception as e:
            logger.error(f"Error fetching channel/playlist: {e}")
            raise MetadataError(f"Failed to fetch channel or playlist: {str(e)}")

        if not info:
            return []

        # Extract channel-level metadata
        if not self.channel_name:
            self.channel_name = info.get("channel") or info.get("uploader") or ""
        if not self.channel_url:
            self.channel_url = info.get("channel_url") or info.get("uploader_url") or self.channel_url

        if not self.banner_url or not self.logo_url:
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

            if banner_cands and not self.banner_url:
                banner_cands.sort(key=lambda x: x[0], reverse=True)
                self.banner_url = banner_cands[0][1]
            if avatar_cands and not self.logo_url:
                avatar_cands.sort(key=lambda x: x[0], reverse=True)
                self.logo_url = avatar_cands[0][1]

        entries = []
        if "entries" in info:
            entries = [e for e in info["entries"] if e is not None]
        else:
            entries = [info]

        candidates = []
        for orig_idx, raw_entry in enumerate(entries, start=1):
            cand = ChannelCandidate.from_dict(
                raw_entry,
                index=orig_idx,
                parent_channel=self.channel_name,
                parent_channel_url=self.channel_url,
            )
            if cand.video_id:
                candidates.append(cand)

        # Apply view count filter and sorting:
        candidates = self.filter_and_sort_candidates(
            candidates, order=order, min_views=min_views, max_views=max_views
        )

        if max_results and len(candidates) > int(max_results):
            candidates = candidates[: int(max_results)]
            self.reassign_version_labels(candidates)

        logger.info(f"Successfully loaded {len(candidates)} video candidates with V1..V{len(candidates)} sequencing.")
        return candidates

    def _resolve_channel_metadata(self, channel_url: str):
        """Quickly resolves channel ID, name, URL, banner, logo, and video count via yt-dlp."""
        opts = {
            "extract_flat": True,
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "playlistend": 1,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(channel_url, download=False) or {}
            c_id = info.get("channel_id") or info.get("id") or ""
            c_name = info.get("channel") or info.get("uploader") or ""
            c_url = info.get("channel_url") or info.get("uploader_url") or channel_url
            vid_count = info.get("playlist_count") or info.get("video_count") or None

            banner_url = ""
            logo_url = ""
            for t in info.get("thumbnails", []):
                t_id = str(t.get("id") or "").lower()
                t_url = t.get("url")
                if not t_url:
                    continue
                w = t.get("width") or 0
                h = t.get("height") or 0
                if "banner" in t_id or (w and h and (w / h) > 2.2):
                    if not banner_url:
                        banner_url = t_url
                if "avatar" in t_id or (w and h and 0.95 <= (w / h) <= 1.05):
                    if not logo_url:
                        logo_url = t_url

            return c_id, c_name, c_url, banner_url, logo_url, vid_count

    def fetch_channel_summary(self, url: str) -> Dict[str, Any]:
        """Resolves basic channel summary: name, url, banner, logo, and video count."""
        c_id, c_name, c_url, b_url, l_url, vid_count = self._resolve_channel_metadata(url)
        return {
            "channel_id": c_id,
            "channel_name": c_name,
            "channel_url": c_url,
            "banner_url": b_url,
            "logo_url": l_url,
            "video_count": vid_count,
        }

    def _fetch_channel_chip_innertube(
        self,
        channel_id: str,
        channel_name: str,
        channel_url: str,
        chip_name: str = "oldest",
        max_results: Optional[int] = None,
    ) -> List[ChannelCandidate]:
        """
        Uses YouTube's official Innertube browse chips (e.g. 'Oldest' or 'Popular') to retrieve
        videos in guaranteed server-side order.
        """
        url = "https://www.youtube.com/youtubei/v1/browse?prettyPrint=false"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
        }

        # 1. Fetch Videos tab browse payload
        payload1 = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20240901.00.00",
                    "hl": "en",
                    "gl": "US",
                }
            },
            "browseId": channel_id,
            "params": "EgZ2aWRlb3PyBgQKAjoA",
        }
        req1 = urllib.request.Request(url, data=json.dumps(payload1).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req1, timeout=15) as resp:
                res1 = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.debug(f"Innertube videos tab request failed: {e}")
            return []

        # 2. Find chip continuation token
        target_token = None
        tabs = res1.get("contents", {}).get("twoColumnBrowseResultsRenderer", {}).get("tabs", [])
        for tab in tabs:
            tab_r = tab.get("tabRenderer", {})
            chips = tab_r.get("content", {}).get("richGridRenderer", {}).get("header", {}).get("chipBarViewModel", {}).get("chips", [])
            for c in chips:
                vm = c.get("chipViewModel", {})
                txt_chip = vm.get("text", "").strip().lower()
                target_match = False
                if chip_name == "oldest" and txt_chip == "oldest":
                    target_match = True
                elif chip_name == "popular" and (txt_chip in ("popular", "most popular") or "popular" in txt_chip):
                    target_match = True

                if target_match:
                    target_token = (
                        vm.get("tapCommand", {})
                        .get("innertubeCommand", {})
                        .get("continuationCommand", {})
                        .get("token")
                    )
                    break
            if target_token:
                break

        if not target_token:
            logger.debug(f"No '{chip_name}' chip continuation token in YouTube response.")
            return []

        candidates: List[ChannelCandidate] = []
        token = target_token
        seen_ids = set()

        while token and (max_results is None or len(candidates) < max_results):
            payload = {
                "context": {
                    "client": {
                        "clientName": "WEB",
                        "clientVersion": "2.20240901.00.00",
                        "hl": "en",
                        "gl": "US",
                    }
                },
                "continuation": token,
            }
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                logger.debug(f"Innertube continuation failed: {e}")
                break

            next_token = None
            actions = res.get("onResponseReceivedActions", [])
            for act in actions:
                cmd = act.get("reloadContinuationItemsCommand") or act.get("appendContinuationItemsAction") or {}
                for item in cmd.get("continuationItems", []):
                    if "richItemRenderer" in item:
                        rir_content = item["richItemRenderer"].get("content", {})
                        lum = rir_content.get("lockupViewModel", {})
                        cid = lum.get("contentId")
                        meta = lum.get("metadata", {}).get("lockupMetadataViewModel", {})
                        title = meta.get("title", {}).get("content") or "Untitled Video"

                        # Extract duration string
                        dur_str = "00:00"
                        badges = lum.get("contentImage", {}).get("thumbnailViewModel", {}).get("overlays", [])
                        for b in badges:
                            badge_vm = b.get("thumbnailBottomOverlayViewModel", {}).get("badges", [])
                            for tb in badge_vm:
                                txt = tb.get("thumbnailBadgeViewModel", {}).get("text")
                                if txt and any(ch.isdigit() for ch in txt):
                                    dur_str = txt
                                    break

                        # Extract relative date or view count
                        date_str = ""
                        view_cnt = 0
                        view_str = ""
                        meta_rows = meta.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
                        for r in meta_rows:
                            for p in r.get("metadataParts", []):
                                txt = p.get("text", {}).get("content", "")
                                if "ago" in txt:
                                    date_str = txt
                                elif "view" in txt.lower():
                                    view_str = txt
                                    view_cnt = _parse_views_to_int(txt)

                        if cid and cid not in seen_ids:
                            seen_ids.add(cid)
                            idx = len(candidates) + 1
                            cand = ChannelCandidate(
                                video_id=cid,
                                url=f"https://www.youtube.com/watch?v={cid}",
                                title=title,
                                duration=_parse_duration_to_sec(dur_str),
                                duration_str=dur_str,
                                upload_date=date_str,
                                uploader=channel_name,
                                channel_url=channel_url,
                                thumbnail=f"https://i.ytimg.com/vi/{cid}/hqdefault.jpg",
                                version_label=f"V{idx}",
                                version_num=idx,
                                original_index=idx,
                                view_count=view_cnt,
                                view_count_str=view_str or (f"{view_cnt:,}" if view_cnt else ""),
                            )
                            candidates.append(cand)
                            if max_results and len(candidates) >= max_results:
                                break

                    elif "continuationItemRenderer" in item:
                        cir = item["continuationItemRenderer"]
                        next_token = cir.get("continuationEndpoint", {}).get("continuationCommand", {}).get("token")

            if max_results and len(candidates) >= max_results:
                break
            token = next_token

        return candidates

    def _fetch_channel_oldest_innertube(
        self,
        channel_id: str,
        channel_name: str,
        channel_url: str,
        max_results: Optional[int] = None,
    ) -> List[ChannelCandidate]:
        """Backward-compatible method mapping to _fetch_channel_chip_innertube with chip_name='oldest'."""
        return self._fetch_channel_chip_innertube(
            channel_id=channel_id,
            channel_name=channel_name,
            channel_url=channel_url,
            chip_name="oldest",
            max_results=max_results,
        )

    @staticmethod
    def filter_and_sort_candidates(
        candidates: List[ChannelCandidate],
        order: str = ORDER_LATEST_TO_OLDEST,
        min_views: Optional[int] = None,
        max_views: Optional[int] = None,
    ) -> List[ChannelCandidate]:
        """
        Filters candidates by minimum and/or maximum view count thresholds,
        then sorts them according to order (Most Popular, Least Popular, Oldest, Newest),
        and reassigns V1, V2, V3... labels sequentially from top to bottom.
        """
        filtered = []
        for c in candidates:
            if min_views is not None and c.view_count < min_views:
                continue
            if max_views is not None and c.view_count > max_views:
                continue
            filtered.append(c)

        if order == ORDER_POPULAR_TO_LEAST:
            filtered.sort(key=lambda c: c.view_count, reverse=True)
        elif order == ORDER_LEAST_TO_POPULAR:
            filtered.sort(key=lambda c: c.view_count, reverse=False)
        elif order == ORDER_OLDEST_TO_LATEST:
            filtered.sort(key=lambda c: c.original_index, reverse=False)
        else:
            filtered.sort(key=lambda c: c.original_index, reverse=False)

        ChannelFetcher.reassign_version_labels(filtered)
        return filtered

    @staticmethod
    def sort_candidates(candidates: List[ChannelCandidate], order: str = ORDER_LATEST_TO_OLDEST) -> List[ChannelCandidate]:
        """
        Sorts candidates by chronological or popularity order and dynamically updates V1, V2, V3... numbering:
        - ORDER_LATEST_TO_OLDEST (Newest to Oldest): index 1 is newest (V1=Newest).
        - ORDER_OLDEST_TO_LATEST (Oldest to Newest): index 1 is oldest (V1=Oldest).
        - ORDER_POPULAR_TO_LEAST (Most Popular to Least Popular): index 1 is highest view count (V1=Most Popular).
        - ORDER_LEAST_TO_POPULAR (Least Popular to Most Popular): index 1 is lowest view count (V1=Least Popular).
        """
        if order == ORDER_POPULAR_TO_LEAST:
            candidates.sort(key=lambda c: c.view_count, reverse=True)
        elif order == ORDER_LEAST_TO_POPULAR:
            candidates.sort(key=lambda c: c.view_count, reverse=False)
        elif order == ORDER_OLDEST_TO_LATEST:
            candidates.sort(key=lambda c: c.original_index, reverse=False)
        else:
            candidates.sort(key=lambda c: c.original_index, reverse=False)

        ChannelFetcher.reassign_version_labels(candidates)
        return candidates

    @staticmethod
    def reassign_version_labels(candidates: List[ChannelCandidate]):
        """Reassigns V1, V2, V3... labels sequentially from top to bottom."""
        for idx, cand in enumerate(candidates, start=1):
            cand.version_num = idx
            cand.version_label = f"V{idx}"

