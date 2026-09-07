"""
Download item data model and lifecycle state machine with V1..Vn version support.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any


class DownloadStatus(str, Enum):
    QUEUED = "Queued"
    FETCHING_INFO = "Fetching Info"
    DOWNLOADING = "Downloading"
    CONVERTING = "Converting"
    MERGING = "Merging"
    VALIDATING = "Validating"
    COMPLETED = "Completed"
    PAUSED = "Paused"
    CANCELLED = "Cancelled"
    FAILED = "Failed"


@dataclass
class DownloadItem:
    url: str
    media_type: str = "Audio"
    quality: str = "192 kbps (High Quality - ~86MB/hr)"
    format_ext: str = "MP3"
    custom_output_dir: Optional[str] = None
    version_label: Optional[str] = None  # e.g., "V1", "V2", "V3"

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    number: int = 1
    title: str = "Fetching video title..."
    channel: str = "Unknown Channel"
    upload_date: str = ""
    duration_sec: float = 0.0
    duration_str: str = "00:00"
    thumbnail_url: str = ""
    video_id: str = ""

    status: DownloadStatus = DownloadStatus.QUEUED
    progress_percent: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    size_str: str = "0 MB"
    speed_bytes_sec: float = 0.0
    speed_str: str = "0 KB/s"
    eta_sec: int = 0
    eta_str: str = "--:--"

    sample_rate: str = "Auto"
    channels: str = "Auto"
    fps: str = "Best Available"
    video_codec: str = "Auto"

    error_message: str = ""
    backend_output: str = ""
    retry_count: int = 0
    max_retries: int = 3
    is_cancelled: bool = False
    is_paused: bool = False

    output_filepath: Optional[str] = None
    transcript_filepath: Optional[str] = None
    completed_at: Optional[datetime] = None
    extra_metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.recalculate_estimated_size()

    def recalculate_estimated_size(self):
        if self.duration_sec > 0 and self.duration_str == "00:00":
            self.duration_str = self.format_duration(self.duration_sec)

        if self.status == DownloadStatus.COMPLETED and self.output_filepath:
            p = Path(self.output_filepath)
            if p.exists():
                self.size_str = self.format_bytes(p.stat().st_size)
                return

        if self.media_type.lower() == "audio" and self.duration_sec > 0:
            bitrate = 192
            q_str = str(self.quality)
            for part in q_str.split():
                if part.isdigit():
                    bitrate = int(part)
                    break
            est_mb = self.estimate_audio_size_mb(self.duration_sec, bitrate)
            self.size_str = f"~{est_mb:.1f} MB"
        elif self.total_bytes > 0:
            self.size_str = self.format_bytes(self.total_bytes)

    @staticmethod
    def format_duration(seconds: float) -> str:
        s = int(seconds)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    @staticmethod
    def format_bytes(num_bytes: int) -> str:
        if num_bytes < 1024:
            return f"{num_bytes} B"
        elif num_bytes < 1024 * 1024:
            return f"{num_bytes / 1024:.1f} KB"
        elif num_bytes < 1024 * 1024 * 1024:
            return f"{num_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"

    @staticmethod
    def estimate_audio_size_mb(duration_sec: float, bitrate_kbps: int) -> float:
        # bitrate in kbps -> (bitrate * 1000 / 8) bytes/sec -> MB
        bytes_per_sec = (bitrate_kbps * 1000) / 8
        return (duration_sec * bytes_per_sec) / (1024 * 1024)

    def update_progress(self, downloaded: int, total: int, speed: float = 0.0, eta: int = 0):
        self.downloaded_bytes = downloaded
        self.total_bytes = total
        self.speed_bytes_sec = speed
        self.eta_sec = eta

        if total > 0:
            self.progress_percent = min(max((downloaded / total) * 100.0, 0.0), 100.0)
            self.size_str = f"{self.format_bytes(downloaded)} / {self.format_bytes(total)}"
        elif downloaded > 0:
            self.size_str = self.format_bytes(downloaded)

        if speed > 0:
            if speed < 1024:
                self.speed_str = f"{speed:.0f} B/s"
            elif speed < 1024 * 1024:
                self.speed_str = f"{speed / 1024:.1f} KB/s"
            else:
                self.speed_str = f"{speed / (1024 * 1024):.2f} MB/s"
        else:
            self.speed_str = "--"

        if eta > 0:
            m, s = divmod(int(eta), 60)
            self.eta_str = f"{m:02d}:{s:02d}"
        else:
            self.eta_str = "--:--"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "media_type": self.media_type,
            "quality": self.quality,
            "format_ext": self.format_ext,
            "custom_output_dir": self.custom_output_dir,
            "version_label": self.version_label,
            "number": self.number,
            "title": self.title,
            "channel": self.channel,
            "upload_date": self.upload_date,
            "duration_sec": self.duration_sec,
            "duration_str": self.duration_str,
            "thumbnail_url": self.thumbnail_url,
            "video_id": self.video_id,
            "status": self.status.value,
            "progress_percent": self.progress_percent,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "size_str": self.size_str,
            "error_message": self.error_message,
            "output_filepath": self.output_filepath,
            "transcript_filepath": self.transcript_filepath,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DownloadItem":
        status_str = data.get("status", "Queued")
        try:
            status = DownloadStatus(status_str)
        except Exception:
            status = DownloadStatus.QUEUED

        item = cls(
            url=data.get("url", ""),
            media_type=data.get("media_type", "Audio"),
            quality=data.get("quality", "192 kbps (High Quality - ~86MB/hr)"),
            format_ext=data.get("format_ext", "MP3"),
            custom_output_dir=data.get("custom_output_dir"),
            version_label=data.get("version_label"),
            id=data.get("id", str(uuid.uuid4())),
            number=data.get("number", 1),
            title=data.get("title", "Fetching video title..."),
            channel=data.get("channel", "Unknown Channel"),
            upload_date=data.get("upload_date", ""),
            duration_sec=data.get("duration_sec", 0.0),
            duration_str=data.get("duration_str", "00:00"),
            thumbnail_url=data.get("thumbnail_url", ""),
            video_id=data.get("video_id", ""),
            status=status,
            progress_percent=data.get("progress_percent", 0.0),
            downloaded_bytes=data.get("downloaded_bytes", 0),
            total_bytes=data.get("total_bytes", 0),
            size_str=data.get("size_str", "0 MB"),
            error_message=data.get("error_message", ""),
            output_filepath=data.get("output_filepath"),
            transcript_filepath=data.get("transcript_filepath"),
        )
        return item

