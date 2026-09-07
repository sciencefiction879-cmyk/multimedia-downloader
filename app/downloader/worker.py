"""
Background QThread download worker with yt-dlp streaming, speed-limit bypass, and FFmpeg transcoding.
"""

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional
from PySide6.QtCore import QThread, Signal
import yt_dlp

from app.config import DEFAULT_AUDIO_DIR, DEFAULT_VIDEO_DIR
from app.core.exceptions import DownloadError
from app.downloader.ffmpeg_engine import FFmpegEngine
from app.downloader.speed_optimizer import SpeedOptimizer
from app.downloader.transcript_fetcher import TranscriptFetcher
from app.models.download_item import DownloadItem, DownloadStatus
from app.models.settings_model import Settings
from app.utils.filename import sanitize_filename
from app.utils.logger import logger


class DownloadWorker(QThread):
    progress_signal = Signal(object)  # DownloadItem
    status_signal = Signal(object, str)  # DownloadItem, status string
    finished_signal = Signal(object)  # DownloadItem
    error_signal = Signal(object, str)  # DownloadItem, error message

    def __init__(self, item: DownloadItem, settings: Optional[Settings] = None, parent=None):
        super().__init__(parent)
        self.item = item
        self.settings = settings or Settings.load()
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True
        self.item.is_cancelled = True
        self.item.status = DownloadStatus.CANCELLED

    def run(self):
        # Defensive guard: ensure settings attribute always exists
        settings = getattr(self, "settings", None)
        if settings is None:
            settings = Settings.load()
            self.settings = settings

        logger.info(f"Starting download task for: {self.item.url} (version: {self.item.version_label})")
        self.item.status = DownloadStatus.FETCHING_INFO
        self.status_signal.emit(self.item, "Connecting with speed accelerator...")

        try:
            # Determine output directory
            if self.item.custom_output_dir:
                out_dir = Path(self.item.custom_output_dir)
            elif self.item.media_type.lower() == "audio":
                out_dir = DEFAULT_AUDIO_DIR
            else:
                out_dir = DEFAULT_VIDEO_DIR
            out_dir.mkdir(parents=True, exist_ok=True)

            is_audio = self.item.media_type.lower() == "audio"

            # Clean and deterministic temporary filename template
            temp_template = str(out_dir / f"temp_{self.item.id}.%(ext)s")

            def _progress_hook(d):
                if self._is_cancelled or self.item.is_cancelled:
                    raise DownloadError("Download cancelled by user.")

                if d.get("status") == "downloading":
                    downloaded = d.get("downloaded_bytes") or 0
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    speed = d.get("speed") or 0.0
                    eta = d.get("eta") or 0

                    self.item.status = DownloadStatus.DOWNLOADING
                    self.item.update_progress(downloaded, total, speed, eta)
                    self.progress_signal.emit(self.item)

                elif d.get("status") == "finished":
                    self.item.status = DownloadStatus.CONVERTING
                    self.status_signal.emit(self.item, "Processing media...")

            # Build high-speed yt-dlp options from SpeedOptimizer
            ydl_opts = SpeedOptimizer.build_speed_options(settings=self.settings, is_audio=is_audio)
            ydl_opts["outtmpl"] = temp_template
            ydl_opts["progress_hooks"] = [_progress_hook]

            # Cookie Acceleration & Authentication (with safe validation)
            use_cookies = False
            if self.settings.use_browser_cookies and self.settings.cookie_browser:
                c_browser = self.settings.cookie_browser.lower()
                if c_browser == "custom_file" and self.settings.custom_cookie_file:
                    cookie_p = Path(self.settings.custom_cookie_file)
                    if cookie_p.exists():
                        ydl_opts["cookiefile"] = str(cookie_p)
                        use_cookies = True
                    else:
                        logger.warning(f"Custom cookie file not found: {cookie_p}")
                elif c_browser != "none":
                    ydl_opts["cookiesfrombrowser"] = (c_browser,)
                    use_cookies = True

            info = None
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(self.item.url, download=True)
            except Exception as e:
                # If cookie authentication failed or expired, immediately retry without cookies
                if use_cookies:
                    logger.warning(f"Cookie authentication failed ({e}). Retrying with direct unthrottled stream...")
                    ydl_opts.pop("cookiesfrombrowser", None)
                    ydl_opts.pop("cookiefile", None)
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(self.item.url, download=True)
                else:
                    raise

            if not info:
                raise DownloadError("yt-dlp could not extract stream info.")

            # Update item metadata with resolved information
            if not self.item.title or self.item.title == "Fetching video title...":
                self.item.title = info.get("title") or "Untitled Video"
            if not self.item.channel or self.item.channel == "Unknown Channel":
                self.item.channel = info.get("uploader") or info.get("channel") or "YouTube"
            if not self.item.duration_sec:
                self.item.duration_sec = float(info.get("duration") or 0.0)
            if not self.item.video_id:
                self.item.video_id = info.get("id") or ""

            # Determine final base filename:
            # If version_label (e.g. "V1", "V2") is specified: name directly as "V1.mp3"
            # Otherwise use the sanitized actual video title (never "Fetching video title")
            if self.item.version_label:
                base_name = self.item.version_label
            else:
                base_name = sanitize_filename(self.item.title) if self.item.title else f"media_{self.item.id[:8]}"

            # Locate downloaded temp file (matching temp_{self.item.id}.*, excluding .part files)
            temp_files = [p for p in out_dir.glob(f"temp_{self.item.id}.*") if not p.name.endswith(".part")]
            if not temp_files:
                # Fallback check for any files matching prefix
                temp_files = [p for p in out_dir.glob(f"temp_{self.item.id}_*") if not p.name.endswith(".part")]
            if not temp_files:
                raise DownloadError("Downloaded file could not be found on disk.")
            temp_file = temp_files[0]

            # Convert to target format
            target_ext = (self.item.format_ext or ("MP3" if is_audio else "MP4")).lower()
            final_path = out_dir / f"{base_name}.{target_ext}"

            if is_audio:
                self.item.status = DownloadStatus.CONVERTING
                self.status_signal.emit(self.item, f"Converting to {self.item.format_ext}...")

                bitrate = FFmpegEngine.extract_audio_bitrate_kbps(self.item.quality)
                FFmpegEngine.convert_to_audio(
                    input_file=temp_file,
                    output_file=final_path,
                    audio_format=self.item.format_ext,
                    bitrate_kbps=bitrate,
                    sample_rate=self.item.sample_rate,
                    channels=self.item.channels,
                )
                # Cleanup temp file
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except Exception as e:
                        logger.debug(f"Could not remove temp file: {e}")
            else:
                # Video file rename/move
                if temp_file != final_path:
                    if final_path.exists():
                        final_path.unlink()
                    shutil.move(str(temp_file), str(final_path))

            self.item.output_filepath = str(final_path)
            self.item.status = DownloadStatus.COMPLETED
            self.item.progress_percent = 100.0
            self.item.completed_at = datetime.now()
            self.item.recalculate_estimated_size()

            logger.info(f"Download complete: {final_path}")
            self.finished_signal.emit(self.item)

        except Exception as e:
            if self._is_cancelled:
                self.item.status = DownloadStatus.CANCELLED
                self.item.is_cancelled = True
                self.item.error_message = "Download cancelled by user."
            else:
                err_str = str(e).lower()
                is_net = any(k in err_str for k in ("network", "connection", "disconnected", "timed out", "unreachable", "internet", "offline"))
                if is_net:
                    self.item.status = DownloadStatus.FAILED
                    self.item.error_message = "Connection lost. Click Resume when internet reconnects."
                else:
                    self.item.status = DownloadStatus.FAILED
                    self.item.error_message = str(e)
            logger.error(f"Worker task error for {self.item.id}: {e}")
            self.error_signal.emit(self.item, self.item.error_message)
