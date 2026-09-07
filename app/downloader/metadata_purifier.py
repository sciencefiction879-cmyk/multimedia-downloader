"""
Competitor video metadata extraction and purification engine.
Removes competitor-specific elements: channel names, external links, URLs,
promotional links, social links, and other identifying personal branding.
Exports purified metadata to V1 Metadata.txt, V2 Metadata.txt...
"""

import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.utils.logger import logger


class MetadataPurifier:
    """Purifies YouTube metadata from competitor branding, links, and promotional material."""

    URL_REGEX = re.compile(
        r"(https?://\S+|www\.\S+|bit\.ly/\S+|linktr\.ee/\S+|amzn\.to/\S+|t\.co/\S+|t\.me/\S+|tinyurl\.com/\S+|forms\.gle/\S+|wa\.me/\S+|discord\.gg/\S+|patreon\.com/\S+)",
        re.IGNORECASE,
    )

    SOCIAL_KEYWORDS = (
        "instagram", "twitter", "tiktok", "facebook", "discord", "patreon",
        "telegram", "reddit", "threads", "spotify", "pinterest", "linkedin",
        "snapchat", "twitch", "follow me", "follow us", "socials", "social media",
    )

    PROMO_KEYWORDS = (
        "sponsored by", "sponsor", "promo code", "discount code", "use code",
        "affiliate", "commission", "amazon associate", "buy my", "check out my course",
        "my merch", "merchandise", "store:", "shop:", "order here", "buy tickets",
        "patrons", "become a patron", "channel member", "join the channel",
        "buy me a coffee", "support the channel", "donate:", "paypal.me",
    )

    CTA_KEYWORDS = (
        "subscribe", "subscribing", "subscriber", "subscribers",
        "bell icon", "notification bell", "notifications on",
        "like and comment", "smash the like", "smash that like", "leave a like",
        "thumbs up", "comment below", "comment down below", "let me know in the comments",
        "link in description", "link in bio", "links below", "links in description",
        "all rights reserved", "copyright ©", "copyright (c)",
    )

    @classmethod
    def purify_title(cls, title: str, channel_name: str = "") -> str:
        """Purifies video title by stripping competitor channel name and handle tags."""
        if not title:
            return "Untitled Video"

        cleaned = title.strip()

        # Remove channel name if appended or prepended: e.g. "Video Title | ChannelName" or "ChannelName - Video Title"
        if channel_name:
            cn = re.escape(channel_name.strip())
            cleaned = re.sub(rf"^\s*{cn}\s*[-–—:|]\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(rf"\s*[-–—:|]\s*{cn}\s*$", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(rf"\s*\[{cn}\]\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(rf"\s*\({cn}\)\s*", "", cleaned, flags=re.IGNORECASE)

        # Remove @ChannelName handle mentions
        cleaned = re.sub(r"@[A-Za-z0-9_.-]+", "", cleaned)

        # Remove standalone hashtags that match channel name or official branding
        if channel_name:
            compact_cn = re.sub(r"\s+", "", channel_name)
            if compact_cn:
                cleaned = re.sub(rf"#{re.escape(compact_cn)}\b", "", cleaned, flags=re.IGNORECASE)

        # Clean trailing separators and whitespace
        cleaned = re.sub(r"\s*[-–—:|]\s*$", "", cleaned).strip()
        return cleaned or title

    @classmethod
    def purify_description(cls, description: str, channel_name: str = "", channel_url: str = "") -> str:
        """
        Removes competitor URLs, social links, promotional CTAs, affiliate disclosures,
        and competitor identifying branding from the video description.
        """
        if not description:
            return ""

        lines = description.splitlines()
        cleaned_lines = []

        channel_terms = []
        if channel_name:
            channel_terms.append(channel_name.strip().lower())
            compact_cn = re.sub(r"\s+", "", channel_name.strip().lower())
            if len(compact_cn) > 2:
                channel_terms.append(compact_cn)

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                cleaned_lines.append("")
                continue

            # 1. Skip lines containing URLs or web links
            if cls.URL_REGEX.search(line):
                continue

            line_lower = line.lower()

            # 2. Skip social media links / mentions
            if any(kw in line_lower for kw in cls.SOCIAL_KEYWORDS):
                continue

            # 3. Skip promotional & sponsor keywords
            if any(kw in line_lower for kw in cls.PROMO_KEYWORDS):
                continue

            # 4. Skip subscription & CTA keywords
            if any(kw in line_lower for kw in cls.CTA_KEYWORDS):
                continue

            # 5. Skip competitor channel name or handle mentions
            if any(term in line_lower for term in channel_terms):
                continue

            # 6. Skip decorative divider lines (e.g. "-----------------", "=======", "***")
            if re.match(r"^[-=_*~#]{3,}$", line):
                continue

            cleaned_lines.append(raw_line)

        # Collapse excess empty lines (max 2 consecutive newlines)
        result = "\n".join(cleaned_lines)
        result = re.sub(r"\n{3,}", "\n\n", result).strip()
        return result

    @classmethod
    def purify_tags(cls, tags: List[str], channel_name: str = "") -> List[str]:
        """Filters out tags containing competitor channel names or generic branding."""
        if not tags:
            return []

        cleaned_tags = []
        channel_name_lower = channel_name.strip().lower() if channel_name else ""
        compact_cn = re.sub(r"\s+", "", channel_name_lower)

        for tag in tags:
            if not isinstance(tag, str):
                continue
            t = tag.strip()
            if not t:
                continue

            t_lower = t.lower()

            # Skip if tag contains channel name
            if channel_name_lower and channel_name_lower in t_lower:
                continue
            if compact_cn and compact_cn in re.sub(r"\s+", "", t_lower):
                continue

            # Skip common promotional/branding tags
            if any(k in t_lower for k in ("subscribe", "official video", "official audio", "official music video", "official channel")):
                continue

            cleaned_tags.append(t)

        return cleaned_tags

    @classmethod
    def format_purified_metadata(
        cls,
        info: Dict[str, Any],
        version_label: str = "V1",
        channel_name: str = "",
        channel_url: str = "",
    ) -> str:
        """
        Creates a structured, purified metadata document suitable for saving into V{i} Metadata.txt.
        """
        raw_title = info.get("title") or f"Video {version_label}"
        purified_title = cls.purify_title(raw_title, channel_name=channel_name)

        raw_desc = info.get("description") or ""
        purified_desc = cls.purify_description(raw_desc, channel_name=channel_name, channel_url=channel_url)
        if not purified_desc:
            purified_desc = "(No description or all promotional competitor links removed)"

        raw_tags = info.get("tags") or []
        purified_tags = cls.purify_tags(raw_tags, channel_name=channel_name)
        tags_str = ", ".join(purified_tags) if purified_tags else "(None)"

        duration_sec = float(info.get("duration") or 0.0)
        dur_str = f"{int(duration_sec // 60):02d}:{int(duration_sec % 60):02d}"
        if duration_sec >= 3600:
            dur_str = f"{int(duration_sec // 3600):02d}:{dur_str}"

        categories = info.get("categories") or []
        category_str = ", ".join(categories) if categories else "General"

        raw_date = str(info.get("upload_date") or "")
        if len(raw_date) == 8 and raw_date.isdigit():
            date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        else:
            date_str = raw_date or "N/A"

        content = (
            f"=== {version_label} METADATA ===\n\n"
            f"Title:\n{purified_title}\n\n"
            f"Description:\n{purified_desc}\n\n"
            f"Tags:\n{tags_str}\n\n"
            f"Duration: {dur_str}\n"
            f"Upload Date: {date_str}\n"
            f"Category: {category_str}\n"
        )
        return content

    @classmethod
    def export_metadata_to_folder(
        cls,
        candidates: List[Any],
        metadata_dict: Dict[str, Dict[str, Any]],
        output_dir: Path,
        channel_name: str = "",
        channel_url: str = "",
    ) -> List[Path]:
        """
        Saves each video's purified metadata as a separate TXT file:
        - V1 Metadata.txt
        - V2 Metadata.txt
        - V3 Metadata.txt...
        into one dedicated folder (output_dir).
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        saved_files: List[Path] = []
        for idx, cand in enumerate(candidates, start=1):
            vid_id = getattr(cand, "video_id", None) or (cand.get("video_id") if isinstance(cand, dict) else "")
            v_label = getattr(cand, "version_label", None) or (cand.get("version_label", f"V{idx}") if isinstance(cand, dict) else f"V{idx}")
            c_name = getattr(cand, "uploader", None) or channel_name
            c_url = getattr(cand, "channel_url", None) or channel_url

            info = metadata_dict.get(vid_id) or {}
            # Fallback to candidate fields if full info dictionary not present
            if not info:
                info = {
                    "title": getattr(cand, "title", f"Video {idx}"),
                    "duration": getattr(cand, "duration", 0),
                    "upload_date": getattr(cand, "upload_date", ""),
                    "description": "",
                    "tags": [],
                }

            purified_content = cls.format_purified_metadata(
                info=info,
                version_label=v_label,
                channel_name=c_name,
                channel_url=c_url,
            )

            file_path = output_dir / f"{v_label} Metadata.txt"
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(purified_content)

            saved_files.append(file_path)

        logger.info(f"Exported {len(saved_files)} purified metadata files to {output_dir}")
        return saved_files
