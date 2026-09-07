"""
YouTube transcript / subtitle fetcher and exporter (generating V1.txt, V2.txt, titles.txt, etc.).
"""

import os
import re
import zipfile
from pathlib import Path
from typing import List, Dict, Any, Optional
from youtube_transcript_api import YouTubeTranscriptApi
from app.core.exceptions import TranscriptError
from app.utils.logger import logger
from app.utils.filename import sanitize_filename


class TranscriptFetcher:
    """Fetches and formats transcripts and exports V1 Script.txt, V2 Script.txt, titles catalogs, and zips."""

    @staticmethod
    def clean_voiceover_script(raw_text: str) -> str:
        """
        Cleans transcript text for voiceover:
        1. Strips all timestamps (e.g. 00:15, 01:23:45, [02:30], etc.)
        2. Strips bracketed audio annotations: [Music], [Applause], [Laughter], [Cheering], etc.
        3. Strips competitor personal info / CTAs (Subscribe, Like, Comment, Bell icon, Social media links, Patreon, Sponsors, Merch).
        4. Formats clean narrative sentences into readable paragraphs (double newlines between 3-4 sentences).
        """
        if not raw_text:
            return ""

        text = raw_text

        # 1. Remove all timestamp formats (e.g. 00:00, 01:23:45, [00:12], (1:23))
        text = re.sub(r"\[?\b\d{1,2}:\d{2}(?::\d{2})?\b\]?", "", text)
        text = re.sub(r"\(\d{1,2}:\d{2}(?::\d{2})?\)", "", text)

        # 2. Remove audio bracket tags
        text = re.sub(r"\[.*?\]", "", text)
        text = re.sub(
            r"\[(?:Music|Applause|Laughter|Cheering|Silence|Sound|Inaudible|Whispering|Audio|Intro|Outro|Music Plays|Background Music)\]",
            "",
            text,
            flags=re.IGNORECASE,
        )

        # 3. Clean extra whitespace
        text = re.sub(r"\s+", " ", text).strip()

        # 4. Split into clean sentences
        raw_sentences = re.split(r"(?<=[.!?])\s+", text)
        cleaned_sentences = []

        # Filter out sentences that contain competitor CTAs or promotional boilerplates
        cta_keywords = [
            "subscribe", "subscribing", "subscriber", "subscribers",
            "bell icon", "notification bell", "turn on notification",
            "like and comment", "smash that like", "leave a like", "thumbs up",
            "comment below", "comments below", "comment down below", "let me know in the comment",
            "link in description", "links in description", "link in the description",
            "links in the description", "link in bio", "link down below", "link in my bio",
            "patreon", "patrons", "channel member", "channel membership", "join the membership", "buy me a coffee",
            "sponsored by", "sponsor of today", "sponsor of this", "our sponsor", "promo code", "discount code",
            "check out my course", "buy my merch", "my merchandise", "visit my website",
            "follow me on", "follow us on", "instagram", "tiktok", "twitter", "facebook page", "telegram channel",
        ]

        for s in raw_sentences:
            s = s.strip()
            if not s or re.search(r"https?://|www\.", s, re.IGNORECASE):
                continue

            s_lower = s.lower()
            if any(kw in s_lower for kw in cta_keywords):
                continue

            if s and s[0].islower():
                s = s[0].upper() + s[1:]
            if not s.endswith((".", "!", "?", '."', '!"', '?"')):
                s += "."

            cleaned_sentences.append(s)

        if not cleaned_sentences:
            return ""

        # 5. Group into readable paragraphs (3-4 sentences per paragraph)
        paragraphs = []
        curr_para = []
        for s in cleaned_sentences:
            curr_para.append(s)
            if len(curr_para) >= 4:
                paragraphs.append(" ".join(curr_para))
                curr_para = []

        if curr_para:
            paragraphs.append(" ".join(curr_para))

        return "\n\n".join(paragraphs)


    @staticmethod
    def fetch_video_transcript(video_id: str, languages: List[str] = None) -> Optional[str]:
        if not video_id:
            return None

        if languages is None:
            languages = ["en", "en-US", "en-GB", "en-CA", "en-AU", "auto", "a.en"]

        # 1. Try modern youtube_transcript_api (v1.x instance methods) or legacy (v0.x class methods)
        try:
            if not hasattr(YouTubeTranscriptApi, "get_transcript") and hasattr(YouTubeTranscriptApi, "fetch"):
                api = YouTubeTranscriptApi()
                fetched = api.fetch(video_id, languages=languages)
                if fetched:
                    lines = []
                    for item in fetched:
                        text = getattr(item, "text", None) or (item.get("text", "") if isinstance(item, dict) else str(item))
                        clean_line = text.strip()
                        if clean_line:
                            lines.append(clean_line)
                    if lines:
                        raw = " ".join(lines)
                        return TranscriptFetcher.clean_voiceover_script(raw)
            elif hasattr(YouTubeTranscriptApi, "get_transcript"):
                transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
                if transcript_list:
                    lines = []
                    for item in transcript_list:
                        text = (item.get("text", "") if isinstance(item, dict) else getattr(item, "text", "")).strip()
                        if text:
                            lines.append(text)
                    if lines:
                        raw = " ".join(lines)
                        return TranscriptFetcher.clean_voiceover_script(raw)
        except Exception as e:
            logger.debug(f"Direct transcript API failed for {video_id}: {e}")

        # 2. Fallback: inspect available transcripts list
        try:
            if hasattr(YouTubeTranscriptApi, "list_transcripts"):
                transcript_list_obj = YouTubeTranscriptApi.list_transcripts(video_id)
            elif hasattr(YouTubeTranscriptApi, "list"):
                api = YouTubeTranscriptApi()
                transcript_list_obj = api.list(video_id)
            else:
                transcript_list_obj = []

            for t in transcript_list_obj:
                try:
                    data = t.fetch()
                    lines = []
                    for item in data:
                        text = getattr(item, "text", None) or (item.get("text", "") if isinstance(item, dict) else str(item))
                        clean_line = text.strip()
                        if clean_line:
                            lines.append(clean_line)
                    if lines:
                        raw = " ".join(lines)
                        return TranscriptFetcher.clean_voiceover_script(raw)
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"Transcript list inspection failed for {video_id}: {e}")

        # 3. Fallback: yt-dlp automatic captions / subtitles extraction
        try:
            import yt_dlp
            ydl_opts = {
                "skip_download": True,
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                sub_tracks = info.get("subtitles") or info.get("automatic_captions") or {}

                en_track = None
                for lang_key in ["en", "en-US", "en-GB", "en-orig"]:
                    if lang_key in sub_tracks:
                        en_track = sub_tracks[lang_key]
                        break
                if not en_track and sub_tracks:
                    en_track = next(iter(sub_tracks.values()))

                if en_track:
                    sub_url = None
                    for fmt in en_track:
                        if fmt.get("ext") in ("json3", "vtt", "srv1", "ttml"):
                            sub_url = fmt.get("url")
                            break
                    if not sub_url and en_track:
                        sub_url = en_track[0].get("url")

                    if sub_url:
                        import urllib.request
                        import json
                        req = urllib.request.Request(sub_url, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=8) as resp:
                            raw_data = resp.read()
                            try:
                                parsed = json.loads(raw_data.decode("utf-8"))
                                events = parsed.get("events", [])
                                lines = []
                                for ev in events:
                                    for seg in ev.get("segs", []):
                                        t_seg = seg.get("utf8", "").strip()
                                        if t_seg and t_seg != "\n":
                                            lines.append(t_seg)
                                if lines:
                                    raw = " ".join(lines)
                                    return TranscriptFetcher.clean_voiceover_script(raw)
                            except Exception:
                                decoded = raw_data.decode("utf-8", errors="ignore")
                                cleaned = re.sub(r"<[^>]+>", "", decoded)
                                cleaned = re.sub(r"\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}", "", cleaned)
                                lines = [l.strip() for l in cleaned.splitlines() if l.strip() and not l.strip().isdigit() and not l.startswith("WEBVTT")]
                                if lines:
                                    raw = " ".join(lines)
                                    return TranscriptFetcher.clean_voiceover_script(raw)
        except Exception as e:
            logger.debug(f"yt-dlp fallback transcript fetch failed for {video_id}: {e}")

        return None


    @staticmethod
    def format_script_with_metadata(text: str, version_label: str = "V1") -> str:
        """
        Formats competitor script with:
        <Version>
        Competitor Script Word Count: <count>

        <Narrative text>
        """
        clean_body = text.strip() if text else ""
        if clean_body.startswith(f"{version_label}\nCompetitor Script Word Count:"):
            return clean_body + "\n"

        words = len(clean_body.split()) if clean_body and not clean_body.startswith("[No transcript") else 0
        return f"{version_label}\nCompetitor Script Word Count: {words:,}\n\n{clean_body}\n"

    @staticmethod
    def export_single_transcript(
        video_id: str,
        text: str,
        output_dir: Path,
        version_label: str = "V1",
        custom_title: Optional[str] = None,
    ) -> Path:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{version_label} Script.txt"
        file_path = output_dir / filename

        formatted_content = TranscriptFetcher.format_script_with_metadata(text, version_label)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(formatted_content)

        logger.info(f"Saved transcript: {file_path}")
        return file_path

    @staticmethod
    def export_transcripts_to_folder(
        candidates: List[Any],
        transcripts_dict: Dict[str, str],
        output_dir: Path,
        match_info_dict: Optional[Dict[str, Any]] = None,
        use_custom_seq: bool = False,
    ) -> List[Path]:
        """
        Exports all fetched scripts to output_dir matching their corresponding version:
        V1 -> V1 Script.txt
        V2 -> V2 Script.txt
        V3 -> V3 Script.txt
        Formatted with Version and Competitor Script Word Count at the top.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        saved_paths = []

        for idx, cand in enumerate(candidates, start=1):
            vid_id = getattr(cand, "video_id", None) or (cand.get("video_id") if isinstance(cand, dict) else "")
            title = getattr(cand, "title", None) or (cand.get("title", f"Video {idx}") if isinstance(cand, dict) else f"Video {idx}")
            v_label = getattr(cand, "version_label", None) or (cand.get("version_label", f"V{idx}") if isinstance(cand, dict) else f"V{idx}")

            if use_custom_seq and match_info_dict and vid_id in match_info_dict:
                v_label = match_info_dict[vid_id].get("version_label", v_label)

            text = transcripts_dict.get(vid_id)
            if not text:
                text = f"[No transcript available for {title} ({vid_id})]"

            formatted_content = TranscriptFetcher.format_script_with_metadata(text, v_label)
            txt_file = output_dir / f"{v_label} Script.txt"
            with open(txt_file, "w", encoding="utf-8") as f:
                f.write(formatted_content)

            saved_paths.append(txt_file)

        logger.info(f"Exported {len(saved_paths)} transcript files to {output_dir}")
        return saved_paths

    @staticmethod
    def export_transcripts_zip(
        candidates: List[Any],
        transcripts_dict: Dict[str, str],
        zip_path: Path,
        match_info_dict: Optional[Dict[str, Any]] = None,
        use_custom_seq: bool = False,
    ) -> Path:
        """Packages all V1 Script.txt, V2 Script.txt... transcripts into a single zip archive."""
        zip_path = Path(zip_path)
        zip_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, cand in enumerate(candidates, start=1):
                vid_id = getattr(cand, "video_id", None) or (cand.get("video_id") if isinstance(cand, dict) else "")
                title = getattr(cand, "title", None) or (cand.get("title", f"Video {idx}") if isinstance(cand, dict) else f"Video {idx}")
                v_label = getattr(cand, "version_label", None) or (cand.get("version_label", f"V{idx}") if isinstance(cand, dict) else f"V{idx}")

                if use_custom_seq and match_info_dict and vid_id in match_info_dict:
                    v_label = match_info_dict[vid_id].get("version_label", v_label)

                text = transcripts_dict.get(vid_id) or f"[No transcript available for {title}]"
                formatted_content = TranscriptFetcher.format_script_with_metadata(text, v_label)
                zf.writestr(f"{v_label} Script.txt", formatted_content)

        logger.info(f"Exported transcripts ZIP to {zip_path}")
        return zip_path



    @staticmethod
    def export_titles_file(
        candidates: List[Any],
        output_file: Path,
        match_info_dict: Optional[Dict[str, Any]] = None,
        use_custom_seq: bool = False,
    ) -> Path:
        """
        Exports titles list in New to Old order:
        First title: V1. First Title
        Second title: V2. Second Title
        Third title: V3. Third Title
        ...
        """
        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        lines = []
        for idx, cand in enumerate(candidates, start=1):
            vid_id = getattr(cand, "video_id", None) or (cand.get("video_id") if isinstance(cand, dict) else "")
            title = getattr(cand, "title", None) or (cand.get("title", f"Video {idx}") if isinstance(cand, dict) else f"Video {idx}")
            v_label = getattr(cand, "version_label", None) or (cand.get("version_label", f"V{idx}") if isinstance(cand, dict) else f"V{idx}")

            if use_custom_seq and match_info_dict and vid_id in match_info_dict:
                v_label = match_info_dict[vid_id].get("version_label", v_label)
                custom_t = match_info_dict[vid_id].get("custom_title")
                if custom_t:
                    title = custom_t

            # Format strictly as "V1. Title"
            lines.append(f"{v_label}. {title}")

        content = "\n".join(lines) + "\n"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"Exported titles file to {output_file}")
        return output_file
