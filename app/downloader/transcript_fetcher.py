"""
YouTube transcript / subtitle fetcher and exporter (generating V1.txt, V2.txt, titles.txt, etc.).
"""

import os
import re
import zipfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
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
        text, _, _ = TranscriptFetcher.fetch_video_transcript_with_diagnostics(video_id, languages)
        return text

    @staticmethod
    def fetch_video_transcript_with_diagnostics(
        video_id: str,
        languages: List[str] = None,
    ) -> Tuple[Optional[str], str, Optional[str]]:
        """
        Actively and exhaustively searches for transcripts using 5 retrieval methods:
        1. youtube_transcript_api direct manual captions (English & requested variants)
        2. youtube_transcript_api auto-generated captions
        3. youtube_transcript_api translatable tracks (translated to English)
        4. yt-dlp subtitle tracks & automatic captions extraction (JSON3, VTT, SRV1, TTML)
        5. Direct YouTube Innertube timedtext extraction from player response
        
        Returns:
            Tuple[Optional[str], str, Optional[str]]: (cleaned_text, status_or_method, cause_and_solution)
        """
        if not video_id:
            return None, "Invalid video ID", "The provided video ID or URL is empty."

        if languages is None:
            languages = ["en", "en-US", "en-GB", "en-CA", "en-AU", "auto", "a.en"]

        failure_reasons = []

        # ==========================================================
        # Method 1: youtube_transcript_api direct fetch
        # ==========================================================
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
                        cleaned = TranscriptFetcher.clean_voiceover_script(" ".join(lines))
                        if cleaned:
                            return cleaned, "YouTube Official Captions (API Direct)", None
            elif hasattr(YouTubeTranscriptApi, "get_transcript"):
                transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
                if transcript_list:
                    lines = []
                    for item in transcript_list:
                        text = (item.get("text", "") if isinstance(item, dict) else getattr(item, "text", "")).strip()
                        if text:
                            lines.append(text)
                    if lines:
                        cleaned = TranscriptFetcher.clean_voiceover_script(" ".join(lines))
                        if cleaned:
                            return cleaned, "YouTube Official Captions (API Direct)", None
        except Exception as e:
            err_msg = str(e)
            logger.debug(f"Direct transcript API failed for {video_id}: {err_msg}")
            if "TranscriptsDisabled" in err_msg:
                failure_reasons.append("Creator explicitly disabled subtitles/transcripts for this video.")
            elif "NoTranscriptFound" in err_msg:
                failure_reasons.append("No primary English transcript track found.")
            else:
                failure_reasons.append(f"Direct API error: {err_msg[:80]}")

        # ==========================================================
        # Method 2 & 3: youtube_transcript_api transcript list & auto-translation
        # ==========================================================
        try:
            if hasattr(YouTubeTranscriptApi, "list_transcripts"):
                transcript_list_obj = YouTubeTranscriptApi.list_transcripts(video_id)
            elif hasattr(YouTubeTranscriptApi, "list"):
                api = YouTubeTranscriptApi()
                transcript_list_obj = api.list(video_id)
            else:
                transcript_list_obj = []

            # Priority 1: Check manual or generated English transcript tracks first
            for t in transcript_list_obj:
                lang_code = getattr(t, "language_code", "").lower()
                if lang_code.startswith("en") or lang_code in languages:
                    try:
                        data = t.fetch()
                        lines = []
                        for item in data:
                            text = getattr(item, "text", None) or (item.get("text", "") if isinstance(item, dict) else str(item))
                            clean_line = text.strip()
                            if clean_line:
                                lines.append(clean_line)
                        if lines:
                            cleaned = TranscriptFetcher.clean_voiceover_script(" ".join(lines))
                            if cleaned:
                                kind = "Manual" if not getattr(t, "is_generated", False) else "Auto-Generated"
                                return cleaned, f"YouTube {kind} Captions ({getattr(t, 'language_code', 'en')})", None
                    except Exception:
                        continue

            # Priority 2: Check auto-translate to English if foreign language transcript exists
            for t in transcript_list_obj:
                if getattr(t, "is_translatable", False):
                    try:
                        translated_t = t.translate("en")
                        data = translated_t.fetch()
                        lines = []
                        for item in data:
                            text = getattr(item, "text", None) or (item.get("text", "") if isinstance(item, dict) else str(item))
                            clean_line = text.strip()
                            if clean_line:
                                lines.append(clean_line)
                        if lines:
                            cleaned = TranscriptFetcher.clean_voiceover_script(" ".join(lines))
                            if cleaned:
                                orig_lang = getattr(t, "language", "Original Language")
                                return cleaned, f"Translated Captions (from {orig_lang} to English)", None
                    except Exception as e:
                        logger.debug(f"Translation failed for {video_id}: {e}")

            # Priority 3: Fallback to ANY available native caption track rather than failing
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
                        cleaned = TranscriptFetcher.clean_voiceover_script(" ".join(lines))
                        if cleaned:
                            orig_lang = getattr(t, "language", getattr(t, "language_code", "Original Language"))
                            return cleaned, f"YouTube Native Captions ({orig_lang})", None
                except Exception:
                    continue

        except Exception as e:
            logger.debug(f"Transcript list inspection failed for {video_id}: {e}")

        # ==========================================================
        # Method 4: yt-dlp Subtitle & Auto-Captions Extraction
        # ==========================================================
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
                sub_tracks = info.get("subtitles") or {}
                auto_tracks = info.get("automatic_captions") or {}

                # Check manual subtitles first, then automatic captions
                combined_tracks = dict(sub_tracks)
                for k, v in auto_tracks.items():
                    if k not in combined_tracks:
                        combined_tracks[k] = v

                chosen_track = None
                chosen_lang = ""
                # Priority: English codes, then any track
                for lang_key in ["en", "en-US", "en-GB", "en-orig", "en-CA", "en-IN", "en-AU"]:
                    if lang_key in combined_tracks:
                        chosen_track = combined_tracks[lang_key]
                        chosen_lang = lang_key
                        break

                if not chosen_track and combined_tracks:
                    chosen_lang, chosen_track = next(iter(combined_tracks.items()))

                if chosen_track:
                    sub_url = None
                    for fmt in chosen_track:
                        if fmt.get("ext") in ("json3", "vtt", "srv1", "ttml"):
                            sub_url = fmt.get("url")
                            break
                    if not sub_url:
                        sub_url = chosen_track[0].get("url")

                    if sub_url:
                        import urllib.request
                        import json
                        req = urllib.request.Request(sub_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            raw_data = resp.read()
                            # Try parsing as JSON3 timedtext
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
                                    cleaned = TranscriptFetcher.clean_voiceover_script(" ".join(lines))
                                    if cleaned:
                                        return cleaned, f"yt-dlp Captions ({chosen_lang})", None
                            except Exception:
                                pass

                            # Try parsing as WEBVTT / XML text
                            decoded = raw_data.decode("utf-8", errors="ignore")
                            cleaned_xml = re.sub(r"<[^>]+>", "", decoded)
                            cleaned_vtt = re.sub(r"\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}", "", cleaned_xml)
                            lines = [
                                l.strip() for l in cleaned_vtt.splitlines()
                                if l.strip() and not l.strip().isdigit() and not l.startswith("WEBVTT") and not l.startswith("NOTE")
                            ]
                            if lines:
                                cleaned = TranscriptFetcher.clean_voiceover_script(" ".join(lines))
                                if cleaned:
                                    return cleaned, f"yt-dlp Subtitle Stream ({chosen_lang})", None
        except Exception as e:
            logger.debug(f"yt-dlp fallback transcript fetch failed for {video_id}: {e}")

        # ==========================================================
        # Method 5: Direct YouTube Web Player TimedText Discovery
        # ==========================================================
        try:
            import urllib.request
            import json
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            req = urllib.request.Request(video_url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            # Look for captionTracks JSON in ytInitialPlayerResponse
            match = re.search(r'"captionTracks":\s*(\[.*?\])', html)
            if match:
                tracks_json = json.loads(match.group(1))
                if tracks_json:
                    base_url = tracks_json[0].get("baseUrl")
                    if base_url:
                        # Append fmt=json3 for structured output
                        if "fmt=" not in base_url:
                            base_url += "&fmt=json3"
                        sub_req = urllib.request.Request(base_url, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(sub_req, timeout=10) as sub_resp:
                            sub_content = sub_resp.read().decode("utf-8", errors="ignore")
                            try:
                                sub_obj = json.loads(sub_content)
                                lines = []
                                for ev in sub_obj.get("events", []):
                                    for seg in ev.get("segs", []):
                                        t_seg = seg.get("utf8", "").strip()
                                        if t_seg and t_seg != "\n":
                                            lines.append(t_seg)
                                if lines:
                                    cleaned = TranscriptFetcher.clean_voiceover_script(" ".join(lines))
                                    if cleaned:
                                        return cleaned, "YouTube Web Player TimedText", None
                            except Exception:
                                pass
        except Exception as e:
            logger.debug(f"Direct web timedtext discovery failed for {video_id}: {e}")

        # ==========================================================
        # All 5 Discovery Methods Failed - Build Diagnostic Info
        # ==========================================================
        cause = (
            "YouTube reports no subtitles or automated closed captions (CC) exist for this video. "
            "Possible reasons: Creator has closed captions disabled, the video contains no detectable speech / audio-only music, "
            "or automatic speech recognition has not finished processing for this upload."
        )
        solution = (
            "1. Verify if the video displays a 'CC' button when watched directly on YouTube in your web browser.\n"
            "2. If this is a newly uploaded video, YouTube's auto-caption processing may take 1-2 hours to become available.\n"
            "3. If the video is speech-heavy and CC is enabled, ensure your IP is not temporarily rate-limited by YouTube."
        )

        return None, "Not Available on YouTube", f"{cause}\n\nSuggested Action:\n{solution}"


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
