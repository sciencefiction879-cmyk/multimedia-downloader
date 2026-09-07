"""
High-performance FFmpeg media processing engine for audio conversion and stream merging on macOS.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List
from app.core.exceptions import TranscodeError
from app.core.gpu_detector import GPUDetector
from app.utils.logger import logger


class FFmpegEngine:
    """Invokes FFmpeg to extract audio, convert formats, and merge streams."""

    _ffmpeg_path: Optional[str] = None

    @classmethod
    def get_ffmpeg_path(cls) -> str:
        if cls._ffmpeg_path:
            return cls._ffmpeg_path

        # 1. Try imageio_ffmpeg if installed
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            if exe and os.path.isfile(exe):
                cls._ffmpeg_path = exe
                return exe
        except Exception:
            pass

        # 2. Candidate paths
        exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent.parent
        candidates = [
            shutil.which("ffmpeg"),
            shutil.which("ffmpeg.exe"),
            str(exe_dir / "ffmpeg.exe"),
            str(exe_dir / "bin" / "ffmpeg.exe"),
            str(exe_dir / "ffmpeg"),
            str(exe_dir / "bin" / "ffmpeg"),
            "/opt/homebrew/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
        ]
        for c in candidates:
            if c and os.path.isfile(c):
                cls._ffmpeg_path = c
                return c

        cls._ffmpeg_path = "ffmpeg"
        return "ffmpeg"

    @classmethod
    def extract_audio_bitrate_kbps(cls, quality_str: str) -> int:
        if not quality_str:
            return 192
        for part in str(quality_str).split():
            if part.isdigit():
                return int(part)
        return 192

    @classmethod
    def convert_to_audio(
        cls,
        input_file: Path,
        output_file: Path,
        audio_format: str = "MP3",
        bitrate_kbps: int = 192,
        sample_rate: str = "Auto",
        channels: str = "Auto",
    ) -> Path:
        ffmpeg_bin = cls.get_ffmpeg_path()
        output_file.parent.mkdir(parents=True, exist_ok=True)

        cmd = [ffmpeg_bin, "-y", "-threads", "0", "-i", str(input_file), "-vn"]


        # Codec & quality
        fmt_lower = audio_format.lower()
        if fmt_lower == "mp3":
            cmd.extend(["-c:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k"])
        elif fmt_lower == "m4a" or fmt_lower == "aac":
            cmd.extend(["-c:a", "aac", "-b:a", f"{bitrate_kbps}k"])
        elif fmt_lower == "opus":
            cmd.extend(["-c:a", "libopus", "-b:a", f"{bitrate_kbps}k"])
        elif fmt_lower == "flac":
            cmd.extend(["-c:a", "flac"])
        elif fmt_lower == "wav":
            cmd.extend(["-c:a", "pcm_s16le"])
        else:
            cmd.extend(["-c:a", "copy"])

        # Sample rate
        if sample_rate and "44100" in sample_rate:
            cmd.extend(["-ar", "44100"])
        elif sample_rate and "48000" in sample_rate:
            cmd.extend(["-ar", "48000"])

        # Channels
        if channels == "Mono":
            cmd.extend(["-ac", "1"])
        elif channels == "Stereo":
            cmd.extend(["-ac", "2"])

        cmd.append(str(output_file))

        logger.debug(f"Running FFmpeg: {' '.join(cmd)}")
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            return output_file
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg conversion failed: {e.stderr}")
            raise TranscodeError(f"Audio conversion failed: {e.stderr[-300:] if e.stderr else str(e)}")
