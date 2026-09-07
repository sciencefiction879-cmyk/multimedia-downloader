"""
Zip and bundle packager for matched V1..Vn asset exports (Titles, Transcripts, MP3s, and Combined bundles).
"""

import os
import shutil
import zipfile
from pathlib import Path
from typing import List, Dict, Any, Optional
from app.utils.logger import logger


class ZipPackager:
    """Creates ZIP archives and organized folder bundles for bulk asset downloads."""

    @staticmethod
    def create_titles_file(
        candidates: List[Any],
        output_file: Path,
        match_info_dict: Optional[Dict[str, Any]] = None,
        channel_name: str = "",
        channel_url: str = "",
    ) -> Path:
        """Saves all titles in New to Old order with channel header if present."""
        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Derive channel name/url from candidates if not explicitly provided
        if not channel_name and candidates:
            first = candidates[0]
            channel_name = getattr(first, "uploader", None) or (first.get("uploader") if isinstance(first, dict) else "")
        if not channel_url and candidates:
            first = candidates[0]
            channel_url = getattr(first, "channel_url", None) or (first.get("channel_url") if isinstance(first, dict) else "")

        lines = []
        if channel_name:
            lines.append(f"Channel: {channel_name}")
        if channel_url:
            lines.append(f"Link: {channel_url}")
        if channel_name or channel_url:
            lines.append("")

        for idx, cand in enumerate(candidates, start=1):
            vid_id = getattr(cand, "video_id", None) or (cand.get("video_id") if isinstance(cand, dict) else "")
            title = getattr(cand, "title", None) or (cand.get("title", f"Video {idx}") if isinstance(cand, dict) else f"Video {idx}")
            v_label = getattr(cand, "version_label", None) or (cand.get("version_label", f"V{idx}") if isinstance(cand, dict) else f"V{idx}")

            if match_info_dict and vid_id in match_info_dict:
                v_label = match_info_dict[vid_id].get("version_label", v_label)
                custom_t = match_info_dict[vid_id].get("custom_title")
                if custom_t:
                    title = custom_t

            lines.append(f"{v_label}. {title}")

        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        logger.info(f"Created titles file: {output_file}")
        return output_file

    @classmethod
    def export_titles_folder(
        cls,
        candidates: List[Any],
        output_dir: Path,
        channel_name: str = "",
        channel_url: str = "",
    ) -> List[Path]:
        """
        Saves all individual title TXT files in the single 'Titles' folder.
        Each title TXT file contains:
        Line 1: Competitor Channel Name
        Line 2: Competitor Channel Link
        Line 3: V{i}. Video Title
        Also keeps master titles.txt in both Titles/ and output_dir for compatibility.
        Does not create separate subfolders for titles.
        """
        base_dir = Path(output_dir)
        titles_dir = base_dir / "Titles" if base_dir.name != "Titles" else base_dir
        titles_dir.mkdir(parents=True, exist_ok=True)

        # Derive channel name/url if not passed
        if not channel_name and candidates:
            first = candidates[0]
            channel_name = getattr(first, "uploader", None) or (first.get("uploader") if isinstance(first, dict) else "")
        if not channel_url and candidates:
            first = candidates[0]
            channel_url = getattr(first, "channel_url", None) or (first.get("channel_url") if isinstance(first, dict) else "")

        c_name = channel_name or "Competitor Channel"
        c_url = channel_url or "https://www.youtube.com"

        saved_files: List[Path] = []
        for idx, cand in enumerate(candidates, start=1):
            title = getattr(cand, "title", None) or (cand.get("title", f"Video {idx}") if isinstance(cand, dict) else f"Video {idx}")
            v_label = getattr(cand, "version_label", None) or (cand.get("version_label", f"V{idx}") if isinstance(cand, dict) else f"V{idx}")

            cand_c_name = getattr(cand, "uploader", None) or c_name
            cand_c_url = getattr(cand, "channel_url", None) or c_url

            # Format strictly:
            # Line 1: Competitor Channel Name
            # Line 2: Competitor Channel Link
            # Line 3: Existing Video Title
            content = f"{cand_c_name}\n{cand_c_url}\n{v_label}. {title}\n"

            file_path = titles_dir / f"{v_label} Title.txt"
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            saved_files.append(file_path)

        # Also save master titles.txt in Titles folder and in base_dir
        cls.create_titles_file(candidates, titles_dir / "titles.txt", channel_name=c_name, channel_url=c_url)
        if base_dir != titles_dir:
            cls.create_titles_file(candidates, base_dir / "titles.txt", channel_name=c_name, channel_url=c_url)

        logger.info(f"Exported {len(saved_files)} individual title TXT files into single folder: {titles_dir}")
        return saved_files

    @staticmethod
    def create_transcripts_zip(
        candidates: List[Any],
        transcripts_dict: Dict[str, str],
        zip_path: Path,
        match_info_dict: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """Packages all V1 Script.txt, V2 Script.txt... transcripts into a ZIP file."""
        from app.downloader.transcript_fetcher import TranscriptFetcher
        zip_path = Path(zip_path)
        zip_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, cand in enumerate(candidates, start=1):
                vid_id = getattr(cand, "video_id", None) or (cand.get("video_id") if isinstance(cand, dict) else "")
                title = getattr(cand, "title", None) or (cand.get("title", f"Video {idx}") if isinstance(cand, dict) else f"Video {idx}")
                v_label = getattr(cand, "version_label", None) or (cand.get("version_label", f"V{idx}") if isinstance(cand, dict) else f"V{idx}")

                if match_info_dict and vid_id in match_info_dict:
                    v_label = match_info_dict[vid_id].get("version_label", v_label)

                text = transcripts_dict.get(vid_id) or f"[No transcript available for {title}]"
                formatted_content = TranscriptFetcher.format_script_with_metadata(text, v_label)
                zf.writestr(f"{v_label} Script.txt", formatted_content)

        logger.info(f"Created transcripts ZIP: {zip_path}")
        return zip_path

    @staticmethod
    def create_audio_zip(
        audio_files: Dict[str, Path],  # maps version_label -> audio file path (e.g. "V1" -> Path(".../V1.mp3"))
        zip_path: Path,
    ) -> Path:
        """Packages all V1.mp3, V2.mp3... audio files into a ZIP file."""
        zip_path = Path(zip_path)
        zip_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for v_label, file_path in audio_files.items():
                if file_path.exists():
                    arcname = f"{v_label}{file_path.suffix}"
                    zf.write(str(file_path), arcname=arcname)

        logger.info(f"Created audio ZIP: {zip_path}")
        return zip_path

    @staticmethod
    def create_all_assets_bundle(
        candidates: List[Any],
        transcripts_dict: Dict[str, str],
        audio_files: Dict[str, Path],
        output_dir: Path,
        as_zip: bool = False,
        match_info_dict: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """
        Creates a grouped package containing Title + Script TXT + MP3 for every version:
        V1/
          ├── V1.mp3
          ├── V1 Script.txt
          └── V1_title.txt
        V2/
          ├── V2.mp3
          ├── V2 Script.txt
          └── V2_title.txt
        ...
        Also includes a master titles.txt catalog.
        """
        from app.downloader.transcript_fetcher import TranscriptFetcher
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        bundle_folder = output_dir / "Matched_Assets_Package"
        bundle_folder.mkdir(parents=True, exist_ok=True)

        # 1. Master titles.txt
        ZipPackager.create_titles_file(candidates, bundle_folder / "all_titles.txt", match_info_dict)

        # 2. Per-version assets
        for idx, cand in enumerate(candidates, start=1):
            vid_id = getattr(cand, "video_id", None) or (cand.get("video_id") if isinstance(cand, dict) else "")
            title = getattr(cand, "title", None) or (cand.get("title", f"Video {idx}") if isinstance(cand, dict) else f"Video {idx}")
            v_label = getattr(cand, "version_label", None) or (cand.get("version_label", f"V{idx}") if isinstance(cand, dict) else f"V{idx}")

            if match_info_dict and vid_id in match_info_dict:
                v_label = match_info_dict[vid_id].get("version_label", v_label)
                custom_t = match_info_dict[vid_id].get("custom_title")
                if custom_t:
                    title = custom_t

            version_dir = bundle_folder / v_label
            version_dir.mkdir(parents=True, exist_ok=True)

            # Title file
            with open(version_dir / f"{v_label}_title.txt", "w", encoding="utf-8") as f:
                f.write(f"{v_label}. {title}\n")

            # Script TXT file (V1 Script.txt) - formatted with Version and Word Count
            text = transcripts_dict.get(vid_id) or f"[No transcript available for {title}]"
            formatted_content = TranscriptFetcher.format_script_with_metadata(text, v_label)
            with open(version_dir / f"{v_label} Script.txt", "w", encoding="utf-8") as f:
                f.write(formatted_content)

            # MP3 file (V1.mp3)
            audio_path = audio_files.get(v_label) or audio_files.get(vid_id)
            if audio_path and audio_path.exists():
                shutil.copy2(str(audio_path), str(version_dir / f"{v_label}{audio_path.suffix}"))



        if as_zip:
            zip_dest = output_dir / "All_Assets_Matched_Package.zip"
            with zipfile.ZipFile(zip_dest, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(bundle_folder):
                    for file in files:
                        full_p = Path(root) / file
                        arcname = full_p.relative_to(bundle_folder)
                        zf.write(str(full_p), arcname=str(arcname))
            return zip_dest

        return bundle_folder
