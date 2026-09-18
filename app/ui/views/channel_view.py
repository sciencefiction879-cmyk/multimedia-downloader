"""
Channel & Media Downloader Pro v3.0 with Custom Data Selection, Forced Transcript Discovery,
Video Quality Selection, Dynamic Sorting (New/Old), Correct V-Numbering, and Flexible V-Ranges.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Set

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QCheckBox,
    QGroupBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFileDialog,
    QMessageBox,
    QScrollArea,
    QFrame,
    QDialog,
    QTextEdit,
    QProgressBar,
    QApplication,
    QSpinBox,
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtCore import Qt, Signal, QThread, QTimer

from app.config import (
    AUDIO_FORMATS,
    AUDIO_QUALITIES,
    DEFAULT_AUDIO_QUALITY,
    VIDEO_FORMATS,
    VIDEO_QUALITIES,
    DEFAULT_VIDEO_QUALITY,
    DEFAULT_VIDEO_FORMAT,
    CHANNEL_ORDER_OPTIONS,
    CHANNEL_FETCH_RANGES,
    DEFAULT_CHANNEL_FETCH_COUNT,
    ORDER_LATEST_TO_OLDEST,
    ORDER_OLDEST_TO_LATEST,
)
from app.downloader.channel_fetcher import ChannelFetcher, ChannelCandidate
from app.downloader.transcript_fetcher import TranscriptFetcher
from app.downloader.metadata_purifier import MetadataPurifier
from app.downloader.channel_assets_fetcher import ChannelAssetsFetcher
from app.downloader.zip_packager import ZipPackager
from app.downloader.queue_manager import QueueManager
from app.downloader.ytdlp_client import YtdlpClient
from app.models.download_item import DownloadItem
from app.models.settings_model import Settings
from app.ui.dialogs.download_statistics_dialog import DownloadStatisticsDialog
from app.utils.range_parser import VRangeParser
from app.utils.logger import logger


class ScriptViewerDialog(QDialog):
    def __init__(self, title: str, version_label: str, script_text: str, diagnostic_info: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Script Viewer - {version_label}")
        self.resize(720, 540)
        layout = QVBoxLayout(self)

        lbl = QLabel(f"<b>{version_label}. {title}</b>")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        if script_text:
            word_count = len(script_text.split())
            lbl_info = QLabel(f"<i>Clean voiceover transcript ({word_count:,} words, formatted paragraphs, URLs & CTAs removed)</i>")
            lbl_info.setStyleSheet("color: #34c759; font-size: 11px;")
        else:
            lbl_info = QLabel("<i>No script available for this video track.</i>")
            lbl_info.setStyleSheet("color: #ff3b30; font-size: 11px;")
        layout.addWidget(lbl_info)

        self.txt = QTextEdit()
        if script_text:
            formatted_display = TranscriptFetcher.format_script_with_metadata(script_text, version_label)
            self.txt.setPlainText(formatted_display)
        else:
            diag_text = diagnostic_info or (
                "Forced discovery tried 5 retrieval methods (Official CC, Auto-Captions, Auto-Translation, yt-dlp, and TimedText).\n\n"
                "Result: Creator has closed captions disabled or YouTube has not generated captions for this upload."
            )
            self.txt.setPlainText(f"=== {version_label} SCRIPT NOT FOUND ===\n\n{diag_text}")
        self.txt.setReadOnly(True)
        self.txt.setStyleSheet("background-color: #1a1a20; font-size: 13px; line-height: 1.5; padding: 8px;")
        layout.addWidget(self.txt)

        btn_row = QHBoxLayout()
        btn_copy = QPushButton("📋 Copy Script")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.txt.toPlainText()))
        btn_close = QPushButton("Close")
        btn_close.setObjectName("primaryBtn")
        btn_close.clicked.connect(self.accept)

        btn_row.addStretch()
        btn_row.addWidget(btn_copy)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)


class MediaFetchThread(QThread):
    finished_signal = Signal(list)
    error_signal = Signal(str)

    def __init__(self, url: str, max_count: Optional[int], order: str):
        super().__init__()
        self.url = url.strip()
        self.max_count = max_count
        self.order = order
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            if self._is_cancelled:
                return

            if "watch?v=" in self.url or "youtu.be/" in self.url or "/shorts/" in self.url:
                if "list=" not in self.url:
                    info = YtdlpClient.fetch_metadata(self.url)
                    if self._is_cancelled:
                        return
                    cand = ChannelCandidate.from_dict(info, index=1)
                    cand.version_label = "V1"
                    cand.version_num = 1
                    cand.original_index = 1
                    self.finished_signal.emit([cand])
                    return

            fetcher = ChannelFetcher()
            candidates = fetcher.fetch_channel_videos(self.url, max_results=self.max_count, order=self.order)
            if not self._is_cancelled:
                self.finished_signal.emit(candidates)
        except Exception as e:
            if not self._is_cancelled:
                self.error_signal.emit(str(e))


class BatchTranscriptThread(QThread):
    progress_signal = Signal(int, int, str)  # current, total, current_title
    item_progress = Signal(int, int, int, int, str, str)  # cur, tot, done_count, skip_count, v_label, title
    item_fetched = Signal(str, str)  # video_id, transcript_text
    item_diagnostics = Signal(str, str, str)  # video_id, status_or_method, cause_or_solution
    all_finished = Signal(bool)  # is_completed (True) or cancelled (False)

    def __init__(
        self,
        candidates: List[ChannelCandidate],
        existing_transcripts: Optional[Dict[str, str]] = None,
        output_dir: Optional[Path] = None,
    ):
        super().__init__()
        self.candidates = candidates
        self.existing_transcripts = existing_transcripts or {}
        self.output_dir = output_dir
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        total = len(self.candidates)
        done_count = 0
        skip_count = 0

        for idx, cand in enumerate(self.candidates, start=1):
            if self._is_cancelled:
                self.all_finished.emit(False)
                return

            v_label = cand.version_label or f"V{idx}"
            title = cand.title or f"Video {idx}"

            # 1. Check if already fetched in memory
            if cand.video_id in self.existing_transcripts and self.existing_transcripts[cand.video_id]:
                skip_count += 1
                self.progress_signal.emit(idx, total, f"[{v_label}] (Resumed) {title}")
                self.item_progress.emit(idx, total, done_count, skip_count, v_label, title)
                self.item_fetched.emit(cand.video_id, self.existing_transcripts[cand.video_id])
                if self.output_dir:
                    disk_file = self.output_dir / f"{v_label} Script.txt"
                    if not disk_file.exists() or disk_file.stat().st_size == 0:
                        try:
                            self.output_dir.mkdir(parents=True, exist_ok=True)
                            formatted = TranscriptFetcher.format_script_with_metadata(
                                self.existing_transcripts[cand.video_id], v_label
                            )
                            disk_file.write_text(formatted, encoding="utf-8")
                        except Exception:
                            pass
                continue

            # 2. Check if already saved on disk (Resume from disk)
            if self.output_dir:
                disk_file = self.output_dir / f"{v_label} Script.txt"
                if disk_file.exists() and disk_file.stat().st_size > 0:
                    try:
                        with open(disk_file, "r", encoding="utf-8") as f:
                            saved_text = f.read()
                        if saved_text and not saved_text.startswith("[No transcript"):
                            skip_count += 1
                            self.progress_signal.emit(idx, total, f"[{v_label}] (Already on disk - Skipped) {title}")
                            self.item_progress.emit(idx, total, done_count, skip_count, v_label, title)
                            self.item_fetched.emit(cand.video_id, saved_text)
                            continue
                    except Exception:
                        pass

            self.progress_signal.emit(idx, total, f"[{v_label}] Downloading: {title}")
            try:
                text, method, diag = TranscriptFetcher.fetch_video_transcript_with_diagnostics(cand.video_id)
                if text:
                    done_count += 1
                    self.item_fetched.emit(cand.video_id, text)
                    self.item_diagnostics.emit(cand.video_id, method, "")
                    # Live Saving immediately to disk as each item is retrieved
                    if self.output_dir:
                        try:
                            self.output_dir.mkdir(parents=True, exist_ok=True)
                            disk_file = self.output_dir / f"{v_label} Script.txt"
                            formatted = TranscriptFetcher.format_script_with_metadata(text, v_label)
                            with open(disk_file, "w", encoding="utf-8") as f:
                                f.write(formatted)
                        except Exception as e:
                            logger.debug(f"Live script save failed for {v_label}: {e}")
                else:
                    self.item_diagnostics.emit(cand.video_id, method, diag or "")
            except Exception as e:
                logger.debug(f"Transcript fetch error for {cand.video_id}: {e}")
                self.item_diagnostics.emit(cand.video_id, "Error", str(e))

            self.item_progress.emit(idx, total, done_count, skip_count, v_label, title)

        if not self._is_cancelled:
            self.all_finished.emit(True)


class BatchMetadataThread(QThread):
    progress_signal = Signal(int, int, str)  # current, total, current_title
    item_fetched = Signal(str, dict)  # video_id, metadata_dict
    all_finished = Signal(bool)  # is_completed (True) or cancelled (False)

    def __init__(
        self,
        candidates: List[ChannelCandidate],
        existing_metadata: Optional[Dict[str, Dict[str, Any]]] = None,
        output_dir: Optional[Path] = None,
    ):
        super().__init__()
        self.candidates = candidates
        self.existing_metadata = existing_metadata or {}
        self.output_dir = output_dir
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        total = len(self.candidates)
        for idx, cand in enumerate(self.candidates, start=1):
            if self._is_cancelled:
                self.all_finished.emit(False)
                return

            # Check if already in memory
            if cand.video_id in self.existing_metadata and self.existing_metadata[cand.video_id]:
                self.progress_signal.emit(idx, total, f"(Cached) {cand.title}")
                self.item_fetched.emit(cand.video_id, self.existing_metadata[cand.video_id])
                continue

            self.progress_signal.emit(idx, total, cand.title)
            try:
                info = YtdlpClient.fetch_metadata(cand.url)
                if info:
                    self.item_fetched.emit(cand.video_id, info)
                else:
                    raise Exception("Empty metadata returned")
            except Exception as e:
                logger.debug(f"Metadata fetch fallback for {cand.video_id}: {e}")
                # Reliable fallback to candidate fields so video is never skipped
                info = {
                    "title": cand.title,
                    "duration": cand.duration,
                    "upload_date": cand.upload_date,
                    "uploader": cand.uploader,
                    "channel_url": cand.channel_url,
                    "description": "",
                    "tags": [],
                }
                self.item_fetched.emit(cand.video_id, info)

        if not self._is_cancelled:
            self.all_finished.emit(True)


class BatchThumbnailThread(QThread):
    progress_signal = Signal(int, int, str)
    item_progress = Signal(int, int, int, int, str, str)  # cur, tot, done_count, skip_count, v_label, title
    all_finished = Signal(bool, list)

    def __init__(self, candidates: List[ChannelCandidate], output_dir: Path):
        super().__init__()
        self.candidates = candidates
        self.output_dir = output_dir
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        output_dir = Path(self.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        total = len(self.candidates)
        done_count = 0
        skip_count = 0

        for idx, cand in enumerate(self.candidates, start=1):
            if self._is_cancelled:
                self.all_finished.emit(False, saved)
                return

            v_label = cand.version_label or f"V{idx}"
            title = cand.title or f"Video {idx}"
            out_file = output_dir / f"{v_label} Thumbnail.jpg"

            if out_file.exists() and out_file.stat().st_size > 1024:
                skip_count += 1
                saved.append(out_file)
            else:
                saved_path = ChannelAssetsFetcher.download_thumbnail_for_video(
                    video_id=cand.video_id,
                    version_label=v_label,
                    output_dir=output_dir,
                    fallback_thumb_url=cand.thumbnail,
                )
                if saved_path:
                    saved.append(saved_path)
                    done_count += 1

            self.progress_signal.emit(idx, total, f"[{v_label}] {title}")
            self.item_progress.emit(idx, total, done_count, skip_count, v_label, title)

        self.all_finished.emit(not self._is_cancelled, saved)


class ChannelAssetsThread(QThread):
    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, channel_url: str, output_dir: Path, banner_url: str = "", logo_url: str = ""):
        super().__init__()
        self.channel_url = channel_url
        self.output_dir = output_dir
        self.banner_url = banner_url
        self.logo_url = logo_url

    def run(self):
        try:
            res = ChannelAssetsFetcher.download_channel_assets(
                channel_url=self.channel_url,
                output_dir=self.output_dir,
                banner_url=self.banner_url,
                logo_url=self.logo_url,
            )
            self.finished_signal.emit(res)
        except Exception as e:
            self.error_signal.emit(str(e))


class ChannelView(QWidget):
    switch_to_downloads_requested = Signal()

    def __init__(self, queue_manager: QueueManager, settings: Settings, parent=None):
        super().__init__(parent)
        self.queue_manager = queue_manager
        self.settings = settings
        self.fetcher = ChannelFetcher()

        self.candidates: List[ChannelCandidate] = []
        self.transcripts_dict: Dict[str, str] = {}  # video_id -> text
        self.metadata_dict: Dict[str, Dict[str, Any]] = {}  # video_id -> metadata info
        self.diagnostics_dict: Dict[str, Dict[str, str]] = {}  # video_id -> {"method": ..., "diag": ...}

        self.fetch_thread: Optional[MediaFetchThread] = None
        self.transcripts_thread: Optional[BatchTranscriptThread] = None
        self.metadata_thread: Optional[BatchMetadataThread] = None
        self.thumbnail_thread: Optional[BatchThumbnailThread] = None
        self.channel_assets_thread: Optional[ChannelAssetsThread] = None

        self._is_populating_table = False
        self._active_pipeline: Optional[Dict[str, Any]] = None

        self._init_ui()

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header
        lbl_title = QLabel("Channel & Media Downloader Pro v3.2")
        lbl_title.setObjectName("viewTitle")
        lbl_sub = QLabel(
            "Unified YouTube data extractor with Custom Data Selection, Chronological V1..Vn Order (Oldest to Newest & Newest to Oldest), "
            "Custom V-Ranges (e.g. 1-10, 1-25, 20-30, 47-52), Parallel All-in-One Downloading, and Forced 5-Tier Discovery Cascade."
        )
        lbl_sub.setWordWrap(True)
        lbl_sub.setObjectName("viewSubtitle")
        layout.addWidget(lbl_title)
        layout.addWidget(lbl_sub)

        # -------------------------------------------------------------
        # 1. Fetch Options Card
        # -------------------------------------------------------------
        box_fetch = QGroupBox("1. Channel / Playlist / Video Link")
        fetch_layout = QGridLayout(box_fetch)
        fetch_layout.setSpacing(10)

        fetch_layout.addWidget(QLabel("YouTube URL:"), 0, 0)
        self.txt_url = QLineEdit()
        self.txt_url.setPlaceholderText("Paste Channel link (@Channel/videos), Playlist, or Video URL here...")
        fetch_layout.addWidget(self.txt_url, 0, 1, 1, 3)

        btn_paste = QPushButton("📋 Paste")
        btn_paste.clicked.connect(lambda: self.txt_url.setText(QApplication.clipboard().text().strip()))
        fetch_layout.addWidget(btn_paste, 0, 4)

        fetch_layout.addWidget(QLabel("Order:"), 1, 0)
        self.combo_order = QComboBox()
        self.combo_order.addItems(CHANNEL_ORDER_OPTIONS)
        self.combo_order.setCurrentText(ORDER_LATEST_TO_OLDEST)
        self.combo_order.currentTextChanged.connect(self._on_order_changed)
        fetch_layout.addWidget(self.combo_order, 1, 1)

        fetch_layout.addWidget(QLabel("Videos to Fetch:"), 1, 2)
        self.combo_count = QComboBox()
        self.combo_count.setEditable(True)
        self.combo_count.setToolTip("Enter count (e.g. 50) or exact custom range (e.g. 1-10, 1-25, 20-30, 47-52, All)")
        for count in CHANNEL_FETCH_RANGES:
            self.combo_count.addItem(str(count), count)
        self.combo_count.setCurrentText(str(DEFAULT_CHANNEL_FETCH_COUNT))
        fetch_layout.addWidget(self.combo_count, 1, 3)

        # Action button row: Fetch, Stop Fetch, Clear
        fetch_btn_row = QHBoxLayout()
        fetch_btn_row.setSpacing(6)

        self.btn_fetch = QPushButton("🔍 Fetch Videos")
        self.btn_fetch.setObjectName("primaryBtn")
        self.btn_fetch.clicked.connect(self._fetch_videos_clicked)

        self.btn_stop_fetch = QPushButton("⏹ Stop")
        self.btn_stop_fetch.setEnabled(False)
        self.btn_stop_fetch.clicked.connect(self._stop_fetch_clicked)

        self.btn_clear_all = QPushButton("✕ Clear")
        self.btn_clear_all.clicked.connect(self._clear_all_clicked)

        fetch_btn_row.addWidget(self.btn_fetch)
        fetch_btn_row.addWidget(self.btn_stop_fetch)
        fetch_btn_row.addWidget(self.btn_clear_all)
        fetch_layout.addLayout(fetch_btn_row, 1, 4)

        layout.addWidget(box_fetch)

        # -------------------------------------------------------------
        # 2. Custom Data Selection Panel (Requirement 1 & 2)
        # -------------------------------------------------------------
        box_selection = QGroupBox("2. Custom Data Selection (Choose What To Download)")
        box_selection.setStyleSheet("QGroupBox { border: 1.5px solid #007aff; }")
        sel_layout = QVBoxLayout(box_selection)
        sel_layout.setSpacing(10)

        lbl_sel_info = QLabel("Choose individual items to extract. Only checked items will be retrieved and downloaded:")
        lbl_sel_info.setStyleSheet("color: #007aff; font-weight: 600;")
        sel_layout.addWidget(lbl_sel_info)

        # 6 Clean Selection Checkboxes organized in grid (Removed: Metadata, Description, Tags)
        grid_sel = QGridLayout()
        grid_sel.setSpacing(10)

        self.chk_titles = QCheckBox("1. Video Titles (Single Titles.txt in root folder)")
        self.chk_titles.setChecked(True)
        self.chk_titles.setToolTip("Creates only one TXT file named Titles.txt containing V1 — Title, V2 — Title...")
        grid_sel.addWidget(self.chk_titles, 0, 0)

        self.chk_scripts = QCheckBox("2. Script / Transcript (Scripts/ folder: V1 Script.txt...)")
        self.chk_scripts.setChecked(True)
        self.chk_scripts.setToolTip("Active search across manual CC, auto-captions, translations, yt-dlp, and timedtext")
        grid_sel.addWidget(self.chk_scripts, 0, 1)

        self.chk_thumbnails = QCheckBox("3. Thumbnails (Thumbnails/ folder: V1 Thumbnail.jpg...)")
        self.chk_thumbnails.setChecked(True)
        self.chk_thumbnails.setToolTip("Highest resolution thumbnails named V1 Thumbnail.jpg, V2 Thumbnail.jpg...")
        grid_sel.addWidget(self.chk_thumbnails, 0, 2)

        self.chk_videos = QCheckBox("4. Videos (Videos/ folder: V1.mp4...)")
        self.chk_videos.setChecked(False)  # Unchecked by default to save bandwidth unless explicitly wanted
        self.chk_videos.setToolTip("Downloads actual video files (MP4) named V1.mp4, V2.mp4... at selected quality")
        grid_sel.addWidget(self.chk_videos, 1, 0)

        self.chk_mp3s = QCheckBox("5. Audio Voiceover (Audio/ folder: V1.mp3...)")
        self.chk_mp3s.setChecked(True)
        self.chk_mp3s.setToolTip("Parallel MP3 audio downloads named V1.mp3, V2.mp3... saved into Audio/ folder")
        grid_sel.addWidget(self.chk_mp3s, 1, 1)

        self.chk_channel_assets = QCheckBox("6. Channel Assets (Channel Assets/ folder: Banner & Logo)")
        self.chk_channel_assets.setChecked(True)
        self.chk_channel_assets.setToolTip("Competitor channel banner and avatar/logo images saved into Channel Assets/")
        grid_sel.addWidget(self.chk_channel_assets, 1, 2)

        sel_layout.addLayout(grid_sel)

        # Selection presets and Launch / Resume Buttons
        bottom_sel_row = QHBoxLayout()
        bottom_sel_row.setSpacing(8)

        lbl_presets = QLabel("Presets:")
        lbl_presets.setStyleSheet("color: #9d9da8; font-weight: 600;")
        bottom_sel_row.addWidget(lbl_presets)

        btn_preset_all = QPushButton("Select All")
        btn_preset_all.clicked.connect(self._preset_select_all_data)
        bottom_sel_row.addWidget(btn_preset_all)

        btn_preset_none = QPushButton("Deselect All")
        btn_preset_none.clicked.connect(self._preset_deselect_all_data)
        bottom_sel_row.addWidget(btn_preset_none)

        btn_preset_scripts = QPushButton("Scripts + Titles")
        btn_preset_scripts.clicked.connect(self._preset_scripts_titles_data)
        bottom_sel_row.addWidget(btn_preset_scripts)

        btn_preset_media = QPushButton("Media Only")
        btn_preset_media.clicked.connect(self._preset_media_only_data)
        bottom_sel_row.addWidget(btn_preset_media)

        btn_preset_core = QPushButton("Core Package (Default)")
        btn_preset_core.clicked.connect(self._preset_core_package_data)
        bottom_sel_row.addWidget(btn_preset_core)

        bottom_sel_row.addStretch()

        self.btn_resume_download = QPushButton("⏯ RESUME DOWNLOAD")
        self.btn_resume_download.setStyleSheet(
            "background-color: #ff9500; color: #ffffff; font-weight: 800; font-size: 13px; padding: 8px 16px; border-radius: 6px;"
        )
        self.btn_resume_download.setToolTip("Resumes download; automatically checks disk and skips already completed files instantly")
        self.btn_resume_download.clicked.connect(self._resume_download_clicked)
        bottom_sel_row.addWidget(self.btn_resume_download)

        self.btn_download_selected = QPushButton("🚀 DOWNLOAD SELECTED DATA")
        self.btn_download_selected.setStyleSheet(
            "background-color: #34c759; color: #ffffff; font-weight: 800; font-size: 13px; padding: 8px 18px; border-radius: 6px;"
        )
        self.btn_download_selected.setToolTip("Execute download pipeline for all checked items on selected video candidates")
        self.btn_download_selected.clicked.connect(self._download_selected_items_clicked)
        bottom_sel_row.addWidget(self.btn_download_selected)

        sel_layout.addLayout(bottom_sel_row)
        layout.addWidget(box_selection)

        # -------------------------------------------------------------
        # 3. Format & Quality Settings Card (Requirement 3)
        # -------------------------------------------------------------
        box_fmt = QGroupBox("3. Format & Quality Settings")
        fmt_layout = QGridLayout(box_fmt)
        fmt_layout.setSpacing(10)

        # Audio settings
        fmt_layout.addWidget(QLabel("Audio Format:"), 0, 0)
        self.combo_format = QComboBox()
        self.combo_format.addItems(AUDIO_FORMATS)
        self.combo_format.setCurrentText(self.settings.default_audio_format)
        fmt_layout.addWidget(self.combo_format, 0, 1)

        fmt_layout.addWidget(QLabel("Audio Quality:"), 0, 2)
        self.combo_quality = QComboBox()
        self.combo_quality.addItems(AUDIO_QUALITIES)
        self.combo_quality.setCurrentText(self.settings.default_audio_quality)
        fmt_layout.addWidget(self.combo_quality, 0, 3)

        fmt_layout.addWidget(QLabel("Concurrent Audio:"), 0, 4)
        self.spin_audio_concurrency = QSpinBox()
        self.spin_audio_concurrency.setRange(1, 16)
        initial_conc = getattr(self.settings, "audio_concurrent_downloads", 3)
        self.spin_audio_concurrency.setValue(initial_conc)
        self.spin_audio_concurrency.setToolTip("Select how many audio files download simultaneously (1-16)")
        self.spin_audio_concurrency.valueChanged.connect(self._on_audio_concurrency_changed)
        fmt_layout.addWidget(self.spin_audio_concurrency, 0, 5)

        # Video settings
        fmt_layout.addWidget(QLabel("Video Quality:"), 1, 0)
        self.combo_video_quality = QComboBox()
        self.combo_video_quality.addItems(VIDEO_QUALITIES)
        self.combo_video_quality.setCurrentText(DEFAULT_VIDEO_QUALITY)
        fmt_layout.addWidget(self.combo_video_quality, 1, 1)

        fmt_layout.addWidget(QLabel("Video Format:"), 1, 2)
        self.combo_video_format = QComboBox()
        self.combo_video_format.addItems(VIDEO_FORMATS)
        self.combo_video_format.setCurrentText(DEFAULT_VIDEO_FORMAT)
        fmt_layout.addWidget(self.combo_video_format, 1, 3)

        # Save Directory
        fmt_layout.addWidget(QLabel("Save Folder:"), 2, 0)
        self.txt_out_dir = QLineEdit(self.settings.channels_dir)
        self.txt_out_dir.setReadOnly(True)
        fmt_layout.addWidget(self.txt_out_dir, 2, 1, 1, 3)

        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_dir)
        fmt_layout.addWidget(btn_browse, 2, 4)

        layout.addWidget(box_fmt)

        # -------------------------------------------------------------
        # 4. Live Download Progress Box (Visible during batch downloads)
        # -------------------------------------------------------------
        self.box_transcript_progress = QGroupBox("⚡ Download Progress & Live Status")
        self.box_transcript_progress.setVisible(False)
        prog_layout = QVBoxLayout(self.box_transcript_progress)
        prog_layout.setSpacing(8)

        prog_top = QHBoxLayout()
        self.lbl_transcript_status = QLabel("Processing: 0 / 0...")
        self.lbl_transcript_status.setStyleSheet("font-weight: 700; color: #007aff; font-size: 13px;")

        self.btn_resume_prog = QPushButton("⏯ Resume")
        self.btn_resume_prog.setStyleSheet("background-color: #ff9500; color: #ffffff; font-weight: 700; padding: 4px 12px; border-radius: 4px;")
        self.btn_resume_prog.setVisible(False)
        self.btn_resume_prog.clicked.connect(self._resume_download_clicked)

        self.btn_view_diagnostics = QPushButton("📊 View Full Statistics")
        self.btn_view_diagnostics.setVisible(False)
        self.btn_view_diagnostics.clicked.connect(self._show_diagnostics_report)

        self.btn_stop_transcripts = QPushButton("⏹ Stop All Downloads")
        self.btn_stop_transcripts.setStyleSheet("background-color: #ff3b30; color: #ffffff; font-weight: 600; padding: 4px 12px; border-radius: 4px;")
        self.btn_stop_transcripts.clicked.connect(self._stop_all_batch_threads)

        prog_top.addWidget(self.lbl_transcript_status)
        prog_top.addStretch()
        prog_top.addWidget(self.btn_resume_prog)
        prog_top.addWidget(self.btn_view_diagnostics)
        prog_top.addWidget(self.btn_stop_transcripts)
        prog_layout.addLayout(prog_top)

        # Multi-category live status indicators
        self.prog_cats_frame = QFrame()
        self.prog_cats_frame.setFrameShape(QFrame.StyledPanel)
        self.prog_cats_frame.setStyleSheet("background-color: rgba(120, 120, 128, 0.08); border-radius: 6px; padding: 6px;")
        grid_p = QGridLayout(self.prog_cats_frame)
        grid_p.setContentsMargins(6, 6, 6, 6)
        grid_p.setSpacing(6)

        self.lbl_prog_scripts = QLabel("📜 <b>Scripts:</b> Waiting...")
        self.lbl_prog_thumbs = QLabel("🖼️ <b>Thumbnails:</b> Waiting...")
        self.lbl_prog_media = QLabel("🎵 <b>Audio & Videos:</b> Waiting...")
        self.lbl_prog_assets = QLabel("🎨 <b>Channel Assets:</b> Waiting...")

        grid_p.addWidget(self.lbl_prog_scripts, 0, 0)
        grid_p.addWidget(self.lbl_prog_thumbs, 0, 1)
        grid_p.addWidget(self.lbl_prog_media, 1, 0)
        grid_p.addWidget(self.lbl_prog_assets, 1, 1)
        prog_layout.addWidget(self.prog_cats_frame)

        # Detailed metrics label: current item, done, skipped, remaining
        self.lbl_progress_details = QLabel("Preparing simultaneous tasks...")
        self.lbl_progress_details.setStyleSheet("color: #8e8e93; font-size: 12px;")
        prog_layout.addWidget(self.lbl_progress_details)

        self.bar_transcripts = QProgressBar()
        self.bar_transcripts.setRange(0, 100)
        self.bar_transcripts.setValue(0)
        self.bar_transcripts.setFormat("%p%")
        prog_layout.addWidget(self.bar_transcripts)

        layout.addWidget(self.box_transcript_progress)

        # -------------------------------------------------------------
        # 5. Matched Candidates Table & Custom V-Range (Requirement 5 & 6)
        # -------------------------------------------------------------
        box_table = QGroupBox("4. Matched Videos & Custom V-Range Selection")
        tbl_layout = QVBoxLayout(box_table)

        top_tbl_row = QHBoxLayout()
        self.lbl_table_status = QLabel("No videos loaded yet. Paste a link above and click Fetch Videos.")
        self.lbl_table_status.setStyleSheet("font-weight: 600; color: #9d9da8;")
        top_tbl_row.addWidget(self.lbl_table_status)
        top_tbl_row.addStretch()
        tbl_layout.addLayout(top_tbl_row)

        # Custom V-Number Range Control Bar (Requirement 6)
        range_bar = QHBoxLayout()
        range_bar.setSpacing(8)

        lbl_range = QLabel("<b>Select V-Range:</b>")
        range_bar.addWidget(lbl_range)

        self.txt_v_range = QLineEdit()
        self.txt_v_range.setPlaceholderText("e.g. 1-10, 1-25, 20-30, 47-52, V1, V5, V10, all")
        self.txt_v_range.returnPressed.connect(self._apply_v_range_clicked)
        range_bar.addWidget(self.txt_v_range)

        self.btn_apply_range = QPushButton("Apply Range")
        self.btn_apply_range.clicked.connect(self._apply_v_range_clicked)
        range_bar.addWidget(self.btn_apply_range)

        # Preset range buttons matching user requirements
        btn_r10 = QPushButton("1-10")
        btn_r10.clicked.connect(lambda: self._select_range_preset("1-10"))
        range_bar.addWidget(btn_r10)

        btn_r25 = QPushButton("1-25")
        btn_r25.clicked.connect(lambda: self._select_range_preset("1-25"))
        range_bar.addWidget(btn_r25)

        btn_r2030 = QPushButton("20-30")
        btn_r2030.clicked.connect(lambda: self._select_range_preset("20-30"))
        range_bar.addWidget(btn_r2030)

        btn_r4752 = QPushButton("47-52")
        btn_r4752.clicked.connect(lambda: self._select_range_preset("47-52"))
        range_bar.addWidget(btn_r4752)

        btn_r_all = QPushButton("Select All")
        btn_r_all.clicked.connect(self._select_all_candidates)
        range_bar.addWidget(btn_r_all)

        btn_r_none = QPushButton("Deselect")
        btn_r_none.clicked.connect(self._deselect_all_candidates)
        range_bar.addWidget(btn_r_none)

        self.lbl_range_status = QLabel("Selected: 0 / 0 videos")
        self.lbl_range_status.setStyleSheet("color: #007aff; font-weight: 700; padding-left: 8px;")
        range_bar.addWidget(self.lbl_range_status)

        tbl_layout.addLayout(range_bar)

        self.table = QTableWidget(0, 8)
        headers = ["Sel", "Ver", "Title", "Duration", "Date", "Copy Title", "Script TXT", "Action"]
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 45)
        self.table.setColumnWidth(1, 55)
        self.table.setColumnWidth(3, 75)
        self.table.setColumnWidth(4, 90)
        self.table.setColumnWidth(5, 105)
        self.table.setColumnWidth(6, 110)
        self.table.setColumnWidth(7, 135)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._on_table_item_changed)
        tbl_layout.addWidget(self.table)

        layout.addWidget(box_table)

        # -------------------------------------------------------------
        # 6. Quick 1-Click Competitor Actions (Preserved for existing workflows)
        # -------------------------------------------------------------
        box_bulk = QGroupBox("5. Quick 1-Click Competitor Actions (All-in-One Shortcuts)")
        bulk_layout = QVBoxLayout(box_bulk)
        bulk_layout.setSpacing(8)

        lbl_bulk_desc = QLabel("1-Click shortcuts for rapid batch extraction:")
        lbl_bulk_desc.setStyleSheet("color: #9d9da8; font-weight: 600;")
        bulk_layout.addWidget(lbl_bulk_desc)

        btn_grid = QGridLayout()
        btn_grid.setSpacing(8)

        self.btn_copy_all_titles = QPushButton("📋 1. Copy All Titles (V1 — Title...)")
        self.btn_copy_all_titles.clicked.connect(self._copy_all_titles_clicked)
        btn_grid.addWidget(self.btn_copy_all_titles, 0, 0)

        self.btn_bulk_titles = QPushButton("📑 2. Download Titles (Titles.txt)")
        self.btn_bulk_titles.clicked.connect(self._bulk_download_titles)
        btn_grid.addWidget(self.btn_bulk_titles, 0, 1)

        self.btn_bulk_scripts = QPushButton("📄 3. Download All Scripts (Scripts/ folder)")
        self.btn_bulk_scripts.clicked.connect(self._bulk_download_scripts)
        btn_grid.addWidget(self.btn_bulk_scripts, 1, 0)

        self.btn_bulk_thumbnails = QPushButton("🖼 4. Download Thumbnails (Thumbnails/ folder)")
        self.btn_bulk_thumbnails.clicked.connect(self._bulk_download_thumbnails)
        btn_grid.addWidget(self.btn_bulk_thumbnails, 1, 1)

        self.btn_bulk_mp3s = QPushButton("🎵 5. Download All Audio (Audio/ folder)")
        self.btn_bulk_mp3s.clicked.connect(self._bulk_download_mp3s)
        btn_grid.addWidget(self.btn_bulk_mp3s, 2, 0)

        self.btn_bulk_videos = QPushButton("🎬 6. Download All Videos (Videos/ folder)")
        self.btn_bulk_videos.clicked.connect(self._bulk_download_videos)
        btn_grid.addWidget(self.btn_bulk_videos, 2, 1)

        self.btn_bulk_channel_assets = QPushButton("🎨 7. Channel Assets (Banner & Logo)")
        self.btn_bulk_channel_assets.clicked.connect(self._bulk_download_channel_assets)
        btn_grid.addWidget(self.btn_bulk_channel_assets, 3, 0)

        self.btn_bulk_all_3 = QPushButton("📦 8. Download Core (Titles.txt + Scripts + Audio)")
        self.btn_bulk_all_3.clicked.connect(self._bulk_download_all_3)
        btn_grid.addWidget(self.btn_bulk_all_3, 3, 1)

        self.btn_bulk_all = QPushButton("🌟 DOWNLOAD COMPLETE PACKAGE (Organized Folders)")
        self.btn_bulk_all.setStyleSheet("background-color: #34c759; color: #ffffff; font-weight: 700; font-size: 13px; padding: 10px;")
        self.btn_bulk_all.clicked.connect(self._bulk_download_all_together)
        btn_grid.addWidget(self.btn_bulk_all, 4, 0, 1, 2)

        bulk_layout.addLayout(btn_grid)
        layout.addWidget(box_bulk)

        self._update_audio_checkbox_label()

        scroll.setWidget(content)
        root_layout.addWidget(scroll)

    # ==================== CONTROLS & RANGE LOGIC ====================

    def _on_audio_concurrency_changed(self, val: int):
        self.settings.audio_concurrent_downloads = val
        self.settings.concurrent_downloads = val
        self.queue_manager.settings.concurrent_downloads = val
        self.settings.save()
        self._update_audio_checkbox_label()

    def _update_audio_checkbox_label(self):
        val = self.spin_audio_concurrency.value()
        self.chk_mp3s.setText(f"5. Audio Voiceover (Audio/ folder: V1.mp3... | ⚡ {val} Parallel)")

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.txt_out_dir.text())
        if d:
            self.txt_out_dir.setText(d)

    def _get_fetch_params(self) -> Tuple[Optional[int], Optional[Set[int]]]:
        val = self.combo_count.currentText().strip()
        return VRangeParser.parse_fetch_query(val)

    def _get_selected_count(self) -> Optional[int]:
        count, _ = self._get_fetch_params()
        return count

    def _on_order_changed(self):
        """Dynamic order switch: re-fetches channel so genuine oldest or newest uploads are loaded as V1."""
        order = self.combo_order.currentText()
        if not self.candidates:
            return
        url = self.txt_url.text().strip()
        if self.fetch_thread and self.fetch_thread.isRunning():
            return

        if url:
            self.lbl_table_status.setText(f"Switching order to {order}... fetching videos.")
            self._fetch_videos_clicked()
        else:
            self.candidates = ChannelFetcher.sort_candidates(self.candidates, order)
            self._populate_table()
            self._update_range_status()
            self.lbl_table_status.setText(f"Re-sorted {len(self.candidates)} videos to {order}. V1 is now the first video in this order.")

    def _apply_v_range_clicked(self):
        """Applies V-Number range e.g. '1-10', '1-25', '20-30', '47-52', 'V1-V10', 'V20 to V30', 'V1, V5, V10'."""
        query = self.txt_v_range.text().strip()
        if not query:
            return
        if not self.candidates:
            QMessageBox.warning(self, "No Videos", "Please fetch videos first before selecting a range.")
            return

        target_set = VRangeParser.parse(query, max_limit=len(self.candidates))
        if not target_set:
            QMessageBox.warning(
                self,
                "Invalid Range",
                f"Could not parse range '{query}'.\n\nExamples of valid formats:\n• 1-10\n• 1-25\n• 20-30\n• 47-52\n• V1 to V10\n• V1, V5, V10\n• all",
            )
            return

        self._is_populating_table = True
        self.table.blockSignals(True)
        for row, cand in enumerate(self.candidates):
            is_match = cand.version_num in target_set
            cand.is_selected = is_match
            chk = self.table.item(row, 0)
            if chk:
                chk.setCheckState(Qt.Checked if is_match else Qt.Unchecked)
        self.table.blockSignals(False)
        self._is_populating_table = False

        self._update_range_status()

    def _select_range_preset(self, preset_str: str):
        self.txt_v_range.setText(preset_str)
        self._apply_v_range_clicked()

    def _update_range_status(self):
        selected = [c for c in self.candidates if c.is_selected]
        tot = len(self.candidates)
        if not self.candidates:
            self.lbl_range_status.setText("Selected: 0 / 0 videos")
            return
        nums = [c.version_num for c in selected]
        formatted_range = VRangeParser.format_set(nums)
        self.lbl_range_status.setText(f"Selected: <b>{len(selected)} / {tot}</b> videos ({formatted_range})")

    def _on_table_item_changed(self, item: QTableWidgetItem):
        if item.column() == 0 and not self._is_populating_table:
            row = item.row()
            if 0 <= row < len(self.candidates):
                self.candidates[row].is_selected = (item.checkState() == Qt.Checked)
                self._update_range_status()

    # ==================== DATA PRESET BUTTONS ====================

    def _preset_select_all_data(self):
        self.chk_titles.setChecked(True)
        self.chk_scripts.setChecked(True)
        self.chk_thumbnails.setChecked(True)
        self.chk_videos.setChecked(True)
        self.chk_mp3s.setChecked(True)
        self.chk_channel_assets.setChecked(True)

    def _preset_deselect_all_data(self):
        self.chk_titles.setChecked(False)
        self.chk_scripts.setChecked(False)
        self.chk_thumbnails.setChecked(False)
        self.chk_videos.setChecked(False)
        self.chk_mp3s.setChecked(False)
        self.chk_channel_assets.setChecked(False)

    def _preset_scripts_titles_data(self):
        self._preset_deselect_all_data()
        self.chk_titles.setChecked(True)
        self.chk_scripts.setChecked(True)

    def _preset_media_only_data(self):
        self._preset_deselect_all_data()
        self.chk_thumbnails.setChecked(True)
        self.chk_videos.setChecked(True)
        self.chk_mp3s.setChecked(True)
        self.chk_channel_assets.setChecked(True)

    def _preset_core_package_data(self):
        self._preset_deselect_all_data()
        self.chk_titles.setChecked(True)
        self.chk_scripts.setChecked(True)
        self.chk_thumbnails.setChecked(True)
        self.chk_mp3s.setChecked(True)
        self.chk_channel_assets.setChecked(True)

    # ==================== FETCH ACTIONS ====================

    def _fetch_videos_clicked(self):
        url = self.txt_url.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", "Please enter a YouTube channel, playlist, or video URL.")
            return

        self.btn_fetch.setEnabled(False)
        self.btn_stop_fetch.setEnabled(True)
        count, auto_select_set = self._get_fetch_params()
        self._pending_auto_select_set = auto_select_set
        order = self.combo_order.currentText()
        count_label = f"{count} videos" if count else "All (Unlimited) videos"
        self.lbl_table_status.setText(f"Fetching {count_label} in {order} order...")

        self.fetch_thread = MediaFetchThread(url, count, order)
        self.fetch_thread.finished_signal.connect(self._on_fetch_finished)
        self.fetch_thread.error_signal.connect(self._on_fetch_error)
        self.fetch_thread.start()

    def _stop_fetch_clicked(self):
        if self.fetch_thread and self.fetch_thread.isRunning():
            self.fetch_thread.cancel()
            try:
                self.fetch_thread.terminate()
            except Exception:
                pass
            self.fetch_thread.quit()
        self.btn_fetch.setEnabled(True)
        self.btn_stop_fetch.setEnabled(False)
        self.lbl_table_status.setText("Fetch stopped immediately.")

    def _stop_all_batch_threads(self):
        for th in [self.transcripts_thread, self.thumbnail_thread, self.channel_assets_thread]:
            if th and th.isRunning():
                if hasattr(th, "cancel"):
                    th.cancel()
                try:
                    th.terminate()
                except Exception:
                    pass
                th.quit()
        if hasattr(self, "queue_manager") and self.queue_manager:
            self.queue_manager.pause()
        self.btn_resume_prog.setVisible(True)
        self.lbl_transcript_status.setText("⏸ Download paused/stopped.")
        self.lbl_progress_details.setText("Click 'Resume' to continue downloading remaining items.")
        self.lbl_table_status.setText("Processes stopped. Click 'Resume Download' anytime to continue.")

    def _stop_transcripts_clicked(self):
        self._stop_all_batch_threads()

    def _clear_all_clicked(self):
        self._stop_fetch_clicked()
        self._stop_all_batch_threads()
        self.txt_url.clear()
        self.candidates.clear()
        self.transcripts_dict.clear()
        self.metadata_dict.clear()
        self.diagnostics_dict.clear()
        self.table.setRowCount(0)
        self.lbl_table_status.setText("Cleared. Paste a link above and click Fetch Videos.")
        self.lbl_range_status.setText("Selected: 0 / 0 videos")
        self.box_transcript_progress.setVisible(False)
        self.btn_view_diagnostics.setVisible(False)

    def _on_fetch_finished(self, candidates: List[ChannelCandidate]):
        self.btn_fetch.setEnabled(True)
        self.btn_stop_fetch.setEnabled(False)
        self.candidates = candidates
        order = self.combo_order.currentText()

        # Apply pending auto-selection if range/count was specified
        pending = getattr(self, "_pending_auto_select_set", None)
        if pending is not None:
            for cand in self.candidates:
                cand.is_selected = (cand.version_num in pending)
            self.txt_v_range.setText(VRangeParser.format_set(pending))
            self._pending_auto_select_set = None
        else:
            for cand in self.candidates:
                cand.is_selected = True

        self.lbl_table_status.setText(f"Loaded {len(candidates)} videos ({order}: V1 is the first video).")
        self._populate_table()
        self._update_range_status()

    def _on_fetch_error(self, err_msg: str):
        self.btn_fetch.setEnabled(True)
        self.btn_stop_fetch.setEnabled(False)
        self.lbl_table_status.setText("Fetch failed.")
        QMessageBox.critical(self, "Fetch Error", f"Could not fetch videos: {err_msg}")

    # ==================== TABLE POPULATION ====================

    def _populate_table(self):
        self._is_populating_table = True
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.candidates))
        for row, cand in enumerate(self.candidates):
            # 0: Checkbox
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if cand.is_selected else Qt.Unchecked)
            self.table.setItem(row, 0, chk)

            # 1: Version (V1, V2, V3...)
            it_ver = QTableWidgetItem(cand.version_label)
            it_ver.setTextAlignment(Qt.AlignCenter)
            it_ver.setForeground(QColor("#007aff"))
            f = QFont()
            f.setBold(True)
            it_ver.setFont(f)
            self.table.setItem(row, 1, it_ver)

            # 2: Title
            it_title = QTableWidgetItem(cand.title)
            self.table.setItem(row, 2, it_title)

            # 3: Duration
            it_dur = QTableWidgetItem(cand.duration_str)
            it_dur.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 3, it_dur)

            # 4: Date
            it_date = QTableWidgetItem(cand.upload_date)
            it_date.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 4, it_date)

            # 5: Dedicated Copy Title button (Format: "V1. Title")
            w_copy = QWidget()
            l_copy = QHBoxLayout(w_copy)
            l_copy.setContentsMargins(2, 2, 2, 2)
            btn_copy_title = QPushButton("📋 Copy")
            btn_copy_title.setToolTip(f"Copy formatted title: '{cand.version_label}. {cand.title}'")
            btn_copy_title.setStyleSheet("font-size: 11px; padding: 4px;")
            btn_copy_title.clicked.connect(lambda _, c=cand, b=btn_copy_title: self._copy_single_title(c, b))
            l_copy.addWidget(btn_copy_title)
            self.table.setCellWidget(row, 5, w_copy)

            # 6: Script / TXT Status & View
            has_script = cand.video_id in self.transcripts_dict and bool(self.transcripts_dict[cand.video_id])
            w_script = QWidget()
            l_script = QHBoxLayout(w_script)
            l_script.setContentsMargins(2, 2, 2, 2)
            btn_script = QPushButton("View Script" if has_script else "Get Script")
            btn_script.setStyleSheet("font-size: 11px; padding: 4px;")
            btn_script.clicked.connect(lambda _, c=cand: self._on_single_script_clicked(c))
            l_script.addWidget(btn_script)
            self.table.setCellWidget(row, 6, w_script)

            # 7: Dual Actions: ⬇ MP3 and ⬇ Video
            w_act = QWidget()
            l_act = QHBoxLayout(w_act)
            l_act.setContentsMargins(2, 2, 2, 2)
            l_act.setSpacing(4)

            btn_mp3 = QPushButton("⬇ MP3")
            btn_mp3.setObjectName("primaryBtn")
            btn_mp3.setStyleSheet("font-size: 10px; padding: 3px 5px;")
            btn_mp3.setToolTip(f"Download {cand.version_label}.mp3 audio voiceover")
            btn_mp3.clicked.connect(lambda _, c=cand: self._download_single_candidate(c, media_type="Audio"))
            l_act.addWidget(btn_mp3)

            btn_vid = QPushButton("⬇ Video")
            btn_vid.setStyleSheet("font-size: 10px; padding: 3px 5px;")
            btn_vid.setToolTip(f"Download {cand.version_label}.mp4 video file")
            btn_vid.clicked.connect(lambda _, c=cand: self._download_single_candidate(c, media_type="Video"))
            l_act.addWidget(btn_vid)

            self.table.setCellWidget(row, 7, w_act)

        self.table.blockSignals(False)
        self._is_populating_table = False

    def _copy_single_title(self, cand: ChannelCandidate, btn: QPushButton):
        formatted_title = f"{cand.version_label}. {cand.title}"
        QApplication.clipboard().setText(formatted_title)
        btn.setText("✓ Copied!")
        QTimer.singleShot(1400, lambda: btn.setText("📋 Copy"))

    def _copy_all_titles_clicked(self):
        selected = self._sync_selected_candidates()
        if not selected:
            selected = self.candidates

        if not selected:
            QMessageBox.warning(self, "No Videos", "No video titles available to copy. Please fetch a channel first.")
            return

        lines = [f"{cand.version_label}. {cand.title}" for cand in selected]
        all_titles_text = "\n".join(lines)
        QApplication.clipboard().setText(all_titles_text)

        self.btn_copy_all_titles.setText("✓ All Titles Copied!")
        QTimer.singleShot(1500, lambda: self.btn_copy_all_titles.setText("📋 1. Copy All Titles (V1. Title...)"))

    def _sync_selected_candidates(self) -> List[ChannelCandidate]:
        selected = []
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk and chk.checkState() == Qt.Checked:
                self.candidates[row].is_selected = True
                selected.append(self.candidates[row])
            else:
                self.candidates[row].is_selected = False
        return selected

    def _select_all_candidates(self):
        self._is_populating_table = True
        self.table.blockSignals(True)
        for row, cand in enumerate(self.candidates):
            cand.is_selected = True
            chk = self.table.item(row, 0)
            if chk:
                chk.setCheckState(Qt.Checked)
        self.table.blockSignals(False)
        self._is_populating_table = False
        self._update_range_status()

    def _deselect_all_candidates(self):
        self._is_populating_table = True
        self.table.blockSignals(True)
        for row, cand in enumerate(self.candidates):
            cand.is_selected = False
            chk = self.table.item(row, 0)
            if chk:
                chk.setCheckState(Qt.Unchecked)
        self.table.blockSignals(False)
        self._is_populating_table = False
        self._update_range_status()

    def _on_single_script_clicked(self, cand: ChannelCandidate):
        text = self.transcripts_dict.get(cand.video_id)
        diag_entry = self.diagnostics_dict.get(cand.video_id, {})
        if not text:
            self.lbl_table_status.setText(f"Running forced 5-tier discovery for {cand.title}...")
            text, method, diag = TranscriptFetcher.fetch_video_transcript_with_diagnostics(cand.video_id)
            diag_entry = {"method": method, "diag": diag}
            self.diagnostics_dict[cand.video_id] = diag_entry
            if text:
                self.transcripts_dict[cand.video_id] = text
                self._populate_table()
                self.lbl_table_status.setText(f"Script loaded for {cand.version_label} via {method}.")
            else:
                self.lbl_table_status.setText(f"No script available for {cand.version_label}.")

        dlg = ScriptViewerDialog(
            title=cand.title,
            version_label=cand.version_label,
            script_text=text or "",
            diagnostic_info=diag_entry.get("diag", ""),
            parent=self,
        )
        dlg.exec()

    def _download_single_candidate(self, cand: ChannelCandidate, media_type: str = "Audio"):
        out_dir = self.txt_out_dir.text().strip()
        if media_type == "Video":
            v_dir = Path(out_dir) / "Videos"
            v_dir.mkdir(parents=True, exist_ok=True)
            custom_dir = str(v_dir)
            quality = self.combo_video_quality.currentText()
            fmt = self.combo_video_format.currentText()
        else:
            a_dir = Path(out_dir) / "Audio"
            a_dir.mkdir(parents=True, exist_ok=True)
            custom_dir = str(a_dir)
            quality = self.combo_quality.currentText()
            fmt = self.combo_format.currentText()

        item = DownloadItem(
            url=cand.url,
            media_type=media_type,
            quality=quality,
            format_ext=fmt,
            custom_output_dir=custom_dir,
            version_label=cand.version_label,
            title=cand.title,
            channel=cand.uploader,
            duration_sec=cand.duration,
            video_id=cand.video_id,
        )
        self.queue_manager.add_item(item)
        self.queue_manager.start()
        self.switch_to_downloads_requested.emit()

    # ==================== UNIFIED CUSTOM DATA PIPELINE ====================

    # ==================== PARALLEL ALL-IN-ONE DOWNLOAD ENGINE (v3.2) ====================

    def _download_selected_items_clicked(self):
        """
        Unified v3.2 Parallel Download Pipeline:
        Launches all selected data types (Titles, Scripts, Thumbnails, Audio, Videos, Channel Assets)
        simultaneously in parallel with real-time multi-task tracking and custom audio concurrency.
        """
        selected = self._sync_selected_candidates()
        if not selected:
            if self.candidates:
                res = QMessageBox.question(
                    self,
                    "No Videos Selected",
                    f"No individual videos are selected in the table.\n\nWould you like to download data for all {len(self.candidates)} videos?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if res == QMessageBox.Yes:
                    self._select_all_candidates()
                    selected = self.candidates
                else:
                    return
            else:
                QMessageBox.warning(self, "No Videos", "Please paste a link and click 'Fetch Videos' first.")
                return

        # Check which checkboxes are enabled in Custom Data Selection Panel
        want_titles = self.chk_titles.isChecked()
        want_scripts = self.chk_scripts.isChecked()
        want_thumbnails = self.chk_thumbnails.isChecked()
        want_videos = self.chk_videos.isChecked()
        want_mp3s = self.chk_mp3s.isChecked()
        want_channel_assets = self.chk_channel_assets.isChecked()

        if not any([
            want_titles,
            want_scripts,
            want_thumbnails,
            want_videos,
            want_mp3s,
            want_channel_assets,
        ]):
            QMessageBox.warning(
                self,
                "No Data Selected",
                "Please select at least one item to download in the 'Custom Data Selection' panel.",
            )
            return

        base_dir = Path(self.txt_out_dir.text().strip())
        base_dir.mkdir(parents=True, exist_ok=True)

        channel_name = self.fetcher.channel_name or (selected[0].uploader if selected else "")
        channel_url = self.fetcher.channel_url or (selected[0].channel_url if selected else self.txt_url.text().strip())

        # Update Audio concurrency setting
        audio_concurrency = self.spin_audio_concurrency.value()
        self.settings.audio_concurrent_downloads = audio_concurrency
        self.settings.concurrent_downloads = audio_concurrency
        self.settings.save()
        self.queue_manager.settings.concurrent_downloads = audio_concurrency

        # Reset diagnostics and progress UI
        self.diagnostics_dict.clear()
        self.btn_view_diagnostics.setVisible(False)
        self.btn_resume_prog.setVisible(False)
        self.box_transcript_progress.setVisible(True)
        self.bar_transcripts.setValue(0)
        self.lbl_transcript_status.setText(f"⚡ Downloading All Selected Data in Parallel (Audio Concurrency: {audio_concurrency})...")
        self.lbl_progress_details.setText(f"Initializing concurrent streams for {len(selected)} videos...")

        # Setup parallel coordinator state
        self._parallel_state = {
            "base_dir": base_dir,
            "selected": selected,
            "channel_name": channel_name,
            "channel_url": channel_url,
            "want_titles": want_titles,
            "want_scripts": want_scripts,
            "want_thumbnails": want_thumbnails,
            "want_channel_assets": want_channel_assets,
            "want_mp3s": want_mp3s,
            "want_videos": want_videos,
            # Completion flags
            "titles_done": not want_titles,
            "scripts_done": not want_scripts,
            "thumbs_done": not want_thumbnails,
            "assets_done": not want_channel_assets,
            "media_done": not (want_mp3s or want_videos),
            # Counts
            "titles_saved": 0,
            "scripts_saved": 0,
            "scripts_skipped": 0,
            "thumbs_saved": 0,
            "thumbs_skipped": 0,
            "assets_saved": 0,
            "media_items_map": {},
            "media_completed_count": 0,
            "media_failed_count": 0,
            "failed_items": [],
            "completed": False,
        }

        # 1. LIVE SAVING: Single Titles.txt in root folder
        if want_titles:
            titles_file = base_dir / "Titles.txt"
            try:
                ZipPackager.export_single_titles_file(
                    candidates=selected,
                    output_file=titles_file,
                    channel_name=channel_name,
                    channel_url=channel_url,
                )
                self._parallel_state["titles_saved"] = len(selected)
            except Exception as e:
                logger.error(f"Failed to export Titles.txt: {e}")
            self._parallel_state["titles_done"] = True

        # 2. PARALLEL: Channel Assets (Banner & Logo)
        if want_channel_assets and channel_url:
            assets_dir = base_dir / "Channel Assets"
            assets_dir.mkdir(parents=True, exist_ok=True)
            self.lbl_prog_assets.setText("🎨 <b>Channel Assets:</b> Downloading...")
            self.channel_assets_thread = ChannelAssetsThread(
                channel_url=channel_url,
                output_dir=assets_dir,
                banner_url=self.fetcher.banner_url,
                logo_url=self.fetcher.logo_url,
            )
            self.channel_assets_thread.all_finished.connect(self._on_parallel_assets_done)
            self.channel_assets_thread.start()
        else:
            self.lbl_prog_assets.setText("🎨 <b>Channel Assets:</b> " + ("Skipped (No URL)" if want_channel_assets else "Not Selected"))

        # 3. PARALLEL: Thumbnails
        if want_thumbnails:
            thumb_dir = base_dir / "Thumbnails"
            thumb_dir.mkdir(parents=True, exist_ok=True)
            self.lbl_prog_thumbs.setText(f"🖼️ <b>Thumbnails:</b> Starting (0/{len(selected)})...")
            self.thumbnail_thread = BatchThumbnailThread(candidates=selected, output_dir=thumb_dir)
            self.thumbnail_thread.item_progress.connect(self._on_parallel_thumb_item_prog)
            self.thumbnail_thread.all_finished.connect(self._on_parallel_thumb_done)
            self.thumbnail_thread.start()
        else:
            self.lbl_prog_thumbs.setText("🖼️ <b>Thumbnails:</b> Not Selected")

        # 4. PARALLEL: Scripts / Transcripts (Strict Discovery Cascade)
        if want_scripts:
            scripts_dir = base_dir / "Scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            self.lbl_prog_scripts.setText(f"📜 <b>Scripts:</b> Searching (0/{len(selected)})...")
            self.transcripts_thread = BatchTranscriptThread(
                candidates=selected,
                existing_transcripts=self.transcripts_dict,
                output_dir=scripts_dir,
            )
            self.transcripts_thread.item_progress.connect(self._on_parallel_script_item_prog)
            self.transcripts_thread.item_fetched.connect(self._on_script_item)
            self.transcripts_thread.item_diagnostics.connect(self._on_script_diag)
            self.transcripts_thread.all_finished.connect(self._on_parallel_script_done)
            self.transcripts_thread.start()
        else:
            self.lbl_prog_scripts.setText("📜 <b>Scripts:</b> Not Selected")

        # 5. PARALLEL: Media (Audio & Videos) via QueueManager with Custom Concurrency
        media_items = []
        if want_mp3s:
            audio_dir = base_dir / "Audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            for cand in selected:
                item = DownloadItem(
                    url=cand.url,
                    media_type="Audio",
                    quality=self.combo_quality.currentText(),
                    format_ext=self.combo_format.currentText(),
                    custom_output_dir=str(audio_dir),
                    version_label=cand.version_label,
                    title=cand.title,
                    channel=cand.uploader,
                    duration_sec=cand.duration,
                    video_id=cand.video_id,
                )
                media_items.append(item)
                self._parallel_state["media_items_map"][item.id] = item

        if want_videos:
            v_dir = base_dir / "Videos"
            v_dir.mkdir(parents=True, exist_ok=True)
            for cand in selected:
                item = DownloadItem(
                    url=cand.url,
                    media_type="Video",
                    quality=self.combo_video_quality.currentText(),
                    format_ext=self.combo_video_format.currentText(),
                    custom_output_dir=str(v_dir),
                    version_label=cand.version_label,
                    title=cand.title,
                    channel=cand.uploader,
                    duration_sec=cand.duration,
                    video_id=cand.video_id,
                )
                media_items.append(item)
                self._parallel_state["media_items_map"][item.id] = item

        if media_items:
            self.lbl_prog_media.setText(f"🎵 <b>Audio/Media:</b> Queued {len(media_items)} tasks (⚡ {audio_concurrency} Simultaneous)")
            try:
                self.queue_manager.item_completed.disconnect(self._on_parallel_media_completed)
            except Exception:
                pass
            try:
                self.queue_manager.item_failed.disconnect(self._on_parallel_media_failed)
            except Exception:
                pass
            try:
                self.queue_manager.all_finished.disconnect(self._on_parallel_media_all_finished)
            except Exception:
                pass

            self.queue_manager.item_completed.connect(self._on_parallel_media_completed)
            self.queue_manager.item_failed.connect(self._on_parallel_media_failed)
            self.queue_manager.all_finished.connect(self._on_parallel_media_all_finished)

            self.queue_manager.add_items(media_items)
            self.queue_manager.start()
        else:
            self.lbl_prog_media.setText("🎵 <b>Audio/Media:</b> Not Selected")

        self._update_parallel_overall_progress()
        self._check_parallel_completion()

    def _resume_download_clicked(self):
        """Resumes download: checks on disk and skips existing files instantly."""
        self._download_selected_items_clicked()

    def _on_parallel_assets_done(self, saved_list):
        state = getattr(self, "_parallel_state", None)
        if not state:
            return
        state["assets_done"] = True
        state["assets_saved"] = len(saved_list) if saved_list else 0
        self.lbl_prog_assets.setText("🎨 <b>Channel Assets:</b> ✓ Download Complete")
        self._update_parallel_overall_progress()
        self._check_parallel_completion()

    def _on_parallel_thumb_item_prog(self, cur, tot, done_c, skip_c, v_lbl, title):
        state = getattr(self, "_parallel_state", None)
        if not state:
            return
        state["thumbs_saved"] = done_c
        state["thumbs_skipped"] = skip_c
        self.lbl_prog_thumbs.setText(f"🖼️ <b>Thumbnails:</b> {done_c + skip_c}/{tot} (✓ {done_c}, ⏭ {skip_c})")
        self._update_parallel_overall_progress()

    def _on_parallel_thumb_done(self, is_ok, saved_list):
        state = getattr(self, "_parallel_state", None)
        if not state:
            return
        state["thumbs_done"] = True
        state["thumbs_saved"] = len(saved_list) if saved_list else 0
        self.lbl_prog_thumbs.setText(f"🖼️ <b>Thumbnails:</b> ✓ Complete ({len(saved_list)} saved)")
        self._update_parallel_overall_progress()
        self._check_parallel_completion()

    def _on_script_item(self, vid_id: str, text: str):
        self.transcripts_dict[vid_id] = text

    def _on_script_diag(self, vid_id: str, method: str, diag: str):
        self.diagnostics_dict[vid_id] = {"method": method, "diag": diag}

    def _on_parallel_script_item_prog(self, cur, tot, done_c, skip_c, v_lbl, title):
        state = getattr(self, "_parallel_state", None)
        if not state:
            return
        state["scripts_saved"] = done_c
        state["scripts_skipped"] = skip_c
        self.lbl_prog_scripts.setText(f"📜 <b>Scripts:</b> {done_c + skip_c}/{tot} (✓ {done_c}, ⏭ {skip_c})")
        self._update_parallel_overall_progress()

    def _on_parallel_script_done(self, is_ok):
        state = getattr(self, "_parallel_state", None)
        if not state:
            return
        state["scripts_done"] = True
        if is_ok:
            try:
                scripts_dir = state["base_dir"] / "Scripts"
                saved_s = TranscriptFetcher.export_transcripts_to_folder(
                    candidates=state["selected"],
                    transcripts_dict=self.transcripts_dict,
                    output_dir=scripts_dir,
                )
                state["scripts_saved"] = len(saved_s)
                self._populate_table()
            except Exception as e:
                logger.debug(f"Error finalizing transcripts: {e}")
        self.lbl_prog_scripts.setText(f"📜 <b>Scripts:</b> ✓ Complete ({state['scripts_saved']} saved)")
        self._update_parallel_overall_progress()
        self._check_parallel_completion()

    def _on_parallel_media_completed(self, item):
        state = getattr(self, "_parallel_state", None)
        if not state or item.id not in state["media_items_map"]:
            return
        state["media_completed_count"] += 1
        total_media = len(state["media_items_map"])
        if total_media > 0 and (state["media_completed_count"] + state["media_failed_count"] >= total_media):
            state["media_done"] = True
        self._update_parallel_media_status()
        self._update_parallel_overall_progress()
        self._check_parallel_completion()

    def _on_parallel_media_failed(self, item, err_msg):
        state = getattr(self, "_parallel_state", None)
        if not state or item.id not in state["media_items_map"]:
            return
        state["media_failed_count"] += 1
        state["failed_items"].append({
            "version_label": item.version_label or "V?",
            "title": item.title or "Unknown",
            "category": item.media_type or "Media",
            "reason": str(err_msg),
            "item_id": item.id,
            "url": item.url,
        })
        total_media = len(state["media_items_map"])
        if total_media > 0 and (state["media_completed_count"] + state["media_failed_count"] >= total_media):
            state["media_done"] = True
        self._update_parallel_media_status()
        self._update_parallel_overall_progress()
        self._check_parallel_completion()

    def _on_parallel_media_all_finished(self):
        state = getattr(self, "_parallel_state", None)
        if not state:
            return
        state["media_done"] = True
        self._update_parallel_media_status()
        self._update_parallel_overall_progress()
        self._check_parallel_completion()

    def _update_parallel_media_status(self):
        state = getattr(self, "_parallel_state", None)
        if not state or not (state["want_mp3s"] or state["want_videos"]):
            return
        total_media = len(state["media_items_map"])
        done_c = state["media_completed_count"]
        fail_c = state["media_failed_count"]
        concurrency = self.spin_audio_concurrency.value()
        status_text = f"🎵 <b>Audio/Media:</b> {done_c}/{total_media} (⚡ {concurrency} Simultaneous)"
        if fail_c > 0:
            status_text += f" | ❌ {fail_c} Failed"
        if state["media_done"]:
            status_text = f"🎵 <b>Audio/Media:</b> ✓ Complete ({done_c}/{total_media})"
        self.lbl_prog_media.setText(status_text)

    def _update_parallel_overall_progress(self):
        state = getattr(self, "_parallel_state", None)
        if not state:
            return

        total_weight = 0
        completed_weight = 0
        num_vids = len(state["selected"])

        if state["want_titles"]:
            total_weight += 5
            if state["titles_done"]:
                completed_weight += 5

        if state["want_channel_assets"]:
            total_weight += 5
            if state["assets_done"]:
                completed_weight += 5

        if state["want_thumbnails"]:
            total_weight += 30
            thumbs_progress = (state["thumbs_saved"] + state["thumbs_skipped"]) / max(1, num_vids)
            completed_weight += int(30 * min(1.0, thumbs_progress))

        if state["want_scripts"]:
            total_weight += 30
            scripts_progress = (state["scripts_saved"] + state["scripts_skipped"]) / max(1, num_vids)
            completed_weight += int(30 * min(1.0, scripts_progress))

        if state["want_mp3s"] or state["want_videos"]:
            total_weight += 30
            tot_media = max(1, len(state["media_items_map"]))
            media_progress = (state["media_completed_count"] + state["media_failed_count"]) / tot_media
            completed_weight += int(30 * min(1.0, media_progress))

        pct = int((completed_weight / max(1, total_weight)) * 100) if total_weight > 0 else 100
        self.bar_transcripts.setValue(min(100, pct))
        self.lbl_progress_details.setText(f"Overall Progress: {pct}% complete across all active streams")

    def _check_parallel_completion(self):
        state = getattr(self, "_parallel_state", None)
        if not state or state.get("completed"):
            return

        all_done = (
            state["titles_done"]
            and state["assets_done"]
            and state["thumbs_done"]
            and state["scripts_done"]
            and state["media_done"]
        )

        if all_done:
            state["completed"] = True
            self._finalize_parallel_download()

    def _finalize_parallel_download(self):
        state = getattr(self, "_parallel_state", None)
        if not state:
            return

        selected = state["selected"]
        base_dir = state["base_dir"]

        # Collect missing scripts as failed items with diagnostics
        if state["want_scripts"]:
            for cand in selected:
                vid_id = cand.video_id
                has_script = vid_id in self.transcripts_dict and bool(self.transcripts_dict[vid_id])
                if not has_script:
                    diag_info = self.diagnostics_dict.get(vid_id, {}).get(
                        "diag", "Creator has closed captions disabled or YouTube speech processing pending."
                    )
                    state["failed_items"].append({
                        "version_label": cand.version_label or "V?",
                        "title": cand.title or "Unknown",
                        "category": "Script",
                        "reason": diag_info,
                        "candidate": cand,
                    })

        # Collect missing thumbnails as failed items
        if state["want_thumbnails"]:
            thumb_dir = base_dir / "Thumbnails"
            for cand in selected:
                v_lbl = cand.version_label or f"V{cand.version_num}"
                expected_thumb = thumb_dir / f"{v_lbl} Thumbnail.jpg"
                if not expected_thumb.exists() or expected_thumb.stat().st_size <= 1024:
                    state["failed_items"].append({
                        "version_label": v_lbl,
                        "title": cand.title or "Unknown",
                        "category": "Thumbnail",
                        "reason": "Thumbnail could not be downloaded from YouTube server.",
                        "candidate": cand,
                    })

        total_succ = (
            state["titles_saved"]
            + state["thumbs_saved"]
            + state["scripts_saved"]
            + state["media_completed_count"]
            + state["assets_saved"]
        )
        total_skip = state["thumbs_skipped"] + state["scripts_skipped"]

        stats = {
            "total_videos": len(selected),
            "total_succeeded": total_succ,
            "total_skipped": total_skip,
            "titles_status": f"1 master file at {base_dir / 'Titles.txt'}" if state["want_titles"] else "Not Selected",
            "scripts_status": (
                f"{state['scripts_saved']} saved, {state['scripts_skipped']} skipped on disk"
                if state["want_scripts"] else "Not Selected"
            ),
            "thumbnails_status": (
                f"{state['thumbs_saved']} saved, {state['thumbs_skipped']} skipped on disk"
                if state["want_thumbnails"] else "Not Selected"
            ),
            "audio_status": (
                f"{state['media_completed_count']} completed (⚡ {self.spin_audio_concurrency.value()} concurrent)"
                if state["want_mp3s"] else "Not Selected"
            ),
            "video_status": (
                f"{state['media_completed_count']} completed ({self.combo_video_quality.currentText()})"
                if state["want_videos"] else "Not Selected"
            ),
            "assets_status": (
                f"Saved into {base_dir / 'Channel Assets'}"
                if state["want_channel_assets"] else "Not Selected"
            ),
        }

        self._last_stats = stats
        self._last_failed_items = state["failed_items"]

        self.bar_transcripts.setValue(100)
        self.lbl_transcript_status.setText("✅ All Parallel Downloads Finished!")
        self.lbl_progress_details.setText(f"Completed processing {len(selected)} videos. Click below to view full statistics.")
        self.btn_view_diagnostics.setVisible(True)

        # Automatically pop up DownloadStatisticsDialog
        self._show_diagnostics_report()

    def _show_diagnostics_report(self):
        """Displays full statistics and diagnostics modal dialog with failure retry."""
        stats = getattr(self, "_last_stats", None)
        failed_items = getattr(self, "_last_failed_items", [])
        base_dir = Path(self.txt_out_dir.text().strip())

        if not stats:
            selected = self._sync_selected_candidates() or self.candidates
            stats = {
                "total_videos": len(selected),
                "total_succeeded": len(self.transcripts_dict),
                "total_skipped": 0,
                "titles_status": "Saved" if self.chk_titles.isChecked() else "Not Selected",
                "scripts_status": f"{len(self.transcripts_dict)} transcripts cached",
                "thumbnails_status": "Ready",
                "audio_status": "Ready",
                "video_status": "Ready",
                "assets_status": "Ready",
            }

        dlg = DownloadStatisticsDialog(
            stats=stats,
            failed_items=failed_items,
            output_dir=base_dir,
            on_retry=self._retry_failed_items,
            parent=self,
        )
        dlg.exec()

    def _retry_failed_items(self, failed_items: List[Dict[str, Any]]):
        """Re-downloads only the items that failed without touching succeeded items."""
        if not failed_items:
            return

        failed_scripts = [it["candidate"] for it in failed_items if it.get("category") == "Script" and "candidate" in it]
        failed_thumbs = [it["candidate"] for it in failed_items if it.get("category") in ("Thumbnail", "Thumbnails") and "candidate" in it]
        failed_media_ids = [it["item_id"] for it in failed_items if it.get("category") in ("Audio", "Video") and "item_id" in it]

        self.box_transcript_progress.setVisible(True)
        self.lbl_transcript_status.setText(f"🔄 Retrying {len(failed_items)} failed item(s)...")

        if failed_scripts:
            scripts_dir = Path(self.txt_out_dir.text().strip()) / "Scripts"
            self.lbl_prog_scripts.setText(f"📜 <b>Scripts:</b> Retrying {len(failed_scripts)} items...")
            self.transcripts_thread = BatchTranscriptThread(
                candidates=failed_scripts,
                existing_transcripts=self.transcripts_dict,
                output_dir=scripts_dir,
            )
            self.transcripts_thread.item_progress.connect(self._on_parallel_script_item_prog)
            self.transcripts_thread.item_fetched.connect(self._on_script_item)
            self.transcripts_thread.item_diagnostics.connect(self._on_script_diag)
            self.transcripts_thread.all_finished.connect(self._on_parallel_script_done)
            self.transcripts_thread.start()

        if failed_thumbs:
            thumb_dir = Path(self.txt_out_dir.text().strip()) / "Thumbnails"
            self.lbl_prog_thumbs.setText(f"🖼️ <b>Thumbnails:</b> Retrying {len(failed_thumbs)} items...")
            self.thumbnail_thread = BatchThumbnailThread(
                candidates=failed_thumbs,
                output_dir=thumb_dir,
            )
            self.thumbnail_thread.item_progress.connect(self._on_parallel_thumb_item_prog)
            self.thumbnail_thread.all_finished.connect(self._on_parallel_thumb_done)
            self.thumbnail_thread.start()

        if failed_media_ids:
            self.lbl_prog_media.setText(f"🎵 <b>Audio/Media:</b> Retrying {len(failed_media_ids)} failed tasks...")
            for item_id in failed_media_ids:
                self.queue_manager.retry_item(item_id)
            self.queue_manager.start()

    # ==================== PRESERVED 1-CLICK BULK ACTIONS ====================

    def _bulk_download_titles(self):
        selected = self._sync_selected_candidates()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select at least one video.")
            return

        out_dir = Path(self.txt_out_dir.text().strip())
        out_dir.mkdir(parents=True, exist_ok=True)
        channel_name = self.fetcher.channel_name or (selected[0].uploader if selected else "")
        channel_url = self.fetcher.channel_url or (selected[0].channel_url if selected else "")

        titles_file = out_dir / "Titles.txt"
        ZipPackager.export_single_titles_file(
            candidates=selected,
            output_file=titles_file,
            channel_name=channel_name,
            channel_url=channel_url,
        )
        QMessageBox.information(
            self,
            "Titles Saved",
            f"Successfully saved all titles into single file:\n\n{titles_file}\n\n"
            f"Total videos listed: {len(selected)}\n"
            "Format: V1 — Title of video 1, V2 — Title of video 2...",
        )
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(titles_file)])

    def _bulk_download_scripts(self):
        selected = self._sync_selected_candidates()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select at least one video.")
            return

        out_dir = Path(self.txt_out_dir.text().strip()) / "Scripts"
        out_dir.mkdir(parents=True, exist_ok=True)

        self.box_transcript_progress.setVisible(True)
        self.btn_resume_prog.setVisible(False)
        self.bar_transcripts.setValue(0)
        self.lbl_transcript_status.setText(f"Fetching scripts: 0 / {len(selected)} (0%)...")
        self.lbl_progress_details.setText("Checking disk & retrieving transcripts live...")

        self.transcripts_thread = BatchTranscriptThread(
            candidates=selected,
            existing_transcripts=self.transcripts_dict,
            output_dir=out_dir,
        )

        def _on_item_prog(cur, tot, done_c, skip_c, v_lbl, title):
            pct = int((cur / tot) * 100) if tot > 0 else 0
            self.bar_transcripts.setValue(pct)
            short_title = (title[:32] + "...") if len(title) > 32 else title
            self.lbl_transcript_status.setText(f"Scripts: {cur} / {tot} ({pct}%) - [{v_lbl}] {short_title}")
            rem = tot - cur
            self.lbl_progress_details.setText(f"✓ Completed: {done_c}  |  ⏭ Skipped: {skip_c}  |  ⏳ Remaining: {rem}  |  Total: {tot}")

        def _on_item(vid_id, text):
            self.transcripts_dict[vid_id] = text

        def _on_diag(vid_id, method, diag):
            self.diagnostics_dict[vid_id] = {"method": method, "diag": diag}

        def _on_done(is_completed):
            self.box_transcript_progress.setVisible(False)
            saved = TranscriptFetcher.export_transcripts_to_folder(
                candidates=selected,
                transcripts_dict=self.transcripts_dict,
                output_dir=out_dir,
            )
            self._populate_table()
            if is_completed:
                self.lbl_table_status.setText(f"Saved {len(saved)} script files live into Scripts/.")
                QMessageBox.information(
                    self,
                    "Scripts Saved",
                    f"Successfully saved {len(saved)} clean script files (V1 Script.txt, V2 Script.txt...) to:\n\n{out_dir}",
                )
                if sys.platform == "darwin":
                    subprocess.run(["open", str(out_dir)])
            else:
                self.lbl_table_status.setText(f"Script download stopped. Saved {len(saved)} files.")

        self.transcripts_thread.item_progress.connect(_on_item_prog)
        self.transcripts_thread.item_fetched.connect(_on_item)
        self.transcripts_thread.item_diagnostics.connect(_on_diag)
        self.transcripts_thread.all_finished.connect(_on_done)
        self.transcripts_thread.start()

    def _bulk_download_thumbnails(self):
        selected = self._sync_selected_candidates()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select at least one video.")
            return

        out_dir = Path(self.txt_out_dir.text().strip()) / "Thumbnails"
        out_dir.mkdir(parents=True, exist_ok=True)

        self.box_transcript_progress.setVisible(True)
        self.btn_resume_prog.setVisible(False)
        self.bar_transcripts.setValue(0)
        self.lbl_transcript_status.setText(f"Downloading thumbnails: 0 / {len(selected)} (0%)...")
        self.lbl_progress_details.setText("Checking disk & downloading thumbnails live...")

        self.thumbnail_thread = BatchThumbnailThread(
            candidates=selected,
            output_dir=out_dir,
        )

        def _on_thumb_item_prog(cur, tot, done_c, skip_c, v_lbl, title):
            pct = int((cur / tot) * 100) if tot > 0 else 0
            self.bar_transcripts.setValue(pct)
            short_t = (title[:32] + "...") if len(title) > 32 else title
            self.lbl_transcript_status.setText(f"Thumbnails: {cur} / {tot} ({pct}%) - [{v_lbl}] {short_t}")
            rem = tot - cur
            self.lbl_progress_details.setText(f"✓ Completed: {done_c}  |  ⏭ Skipped: {skip_c}  |  ⏳ Remaining: {rem}  |  Total: {tot}")

        def _on_done(is_completed, saved):
            self.box_transcript_progress.setVisible(False)
            if is_completed:
                self.lbl_table_status.setText(f"Downloaded {len(saved)} thumbnails into Thumbnails/.")
                QMessageBox.information(
                    self,
                    "Thumbnails Downloaded",
                    f"Successfully downloaded {len(saved)} thumbnails (V1 Thumbnail.jpg, V2 Thumbnail.jpg...) to:\n\n{out_dir}",
                )
                if sys.platform == "darwin":
                    subprocess.run(["open", str(out_dir)])
            else:
                self.lbl_table_status.setText(f"Thumbnail download stopped. Saved {len(saved)} files.")

        self.thumbnail_thread.item_progress.connect(_on_thumb_item_prog)
        self.thumbnail_thread.all_finished.connect(_on_done)
        self.thumbnail_thread.start()

    def _bulk_download_mp3s(self):
        selected = self._sync_selected_candidates()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select at least one video.")
            return

        out_dir = Path(self.txt_out_dir.text().strip()) / "Audio"
        out_dir.mkdir(parents=True, exist_ok=True)
        items = []
        for cand in selected:
            item = DownloadItem(
                url=cand.url,
                media_type="Audio",
                quality=self.combo_quality.currentText(),
                format_ext=self.combo_format.currentText(),
                custom_output_dir=str(out_dir),
                version_label=cand.version_label,
                title=cand.title,
                channel=cand.uploader,
                duration_sec=cand.duration,
                video_id=cand.video_id,
            )
            items.append(item)

        self.queue_manager.add_items(items)
        self.queue_manager.start()
        QMessageBox.information(
            self,
            "Queued for Download",
            f"Added {len(items)} MP3 audio tasks (V1.mp3, V2.mp3...) to the queue!\n"
            f"Folder: {out_dir}\n"
            "Will download simultaneously in parallel until all complete.",
        )
        self.switch_to_downloads_requested.emit()

    def _bulk_download_videos(self):
        selected = self._sync_selected_candidates()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select at least one video.")
            return

        out_dir = Path(self.txt_out_dir.text().strip()) / "Videos"
        out_dir.mkdir(parents=True, exist_ok=True)
        items = []
        for cand in selected:
            item = DownloadItem(
                url=cand.url,
                media_type="Video",
                quality=self.combo_video_quality.currentText(),
                format_ext=self.combo_video_format.currentText(),
                custom_output_dir=str(out_dir),
                version_label=cand.version_label,
                title=cand.title,
                channel=cand.uploader,
                duration_sec=cand.duration,
                video_id=cand.video_id,
            )
            items.append(item)

        self.queue_manager.add_items(items)
        self.queue_manager.start()
        QMessageBox.information(
            self,
            "Queued for Download",
            f"Added {len(items)} Video tasks ({self.combo_video_quality.currentText()}) to the queue!\n"
            f"Folder: {out_dir}\n"
            "Will download simultaneously in parallel until all complete.",
        )
        self.switch_to_downloads_requested.emit()

    def _bulk_download_channel_assets(self):
        channel_url = self.fetcher.channel_url or (self.candidates[0].channel_url if self.candidates else self.txt_url.text().strip())
        if not channel_url:
            QMessageBox.warning(self, "No Channel URL", "Please enter or fetch a channel first to download its assets.")
            return

        out_dir = Path(self.txt_out_dir.text().strip()) / "Channel Assets"
        out_dir.mkdir(parents=True, exist_ok=True)

        self.lbl_table_status.setText("Downloading channel banner and logo...")
        self.channel_assets_thread = ChannelAssetsThread(
            channel_url=channel_url,
            output_dir=out_dir,
            banner_url=self.fetcher.banner_url,
            logo_url=self.fetcher.logo_url,
        )

        def _on_done(res):
            self.lbl_table_status.setText("Channel assets downloaded.")
            b_path = res.get("banner_path")
            l_path = res.get("logo_path")
            details = []
            if b_path:
                details.append(f"• Banner: {b_path.name}")
            if l_path:
                details.append(f"• Logo: {l_path.name}")
            if not details:
                details.append("No banner or logo found for this URL.")

            msg = "\n".join(details)
            QMessageBox.information(
                self,
                "Channel Assets Downloaded",
                f"Competitor channel assets saved to:\n{out_dir}\n\n{msg}",
            )
            if sys.platform == "darwin":
                subprocess.run(["open", str(out_dir)])

        def _on_error(err):
            self.lbl_table_status.setText("Failed to download channel assets.")
            QMessageBox.warning(self, "Channel Assets Error", f"Could not download channel assets: {err}")

        self.channel_assets_thread.finished_signal.connect(_on_done)
        self.channel_assets_thread.error_signal.connect(_on_error)
        self.channel_assets_thread.start()

    def _bulk_download_all_3(self):
        selected = self._sync_selected_candidates()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select at least one video.")
            return

        out_dir = Path(self.txt_out_dir.text().strip())
        out_dir.mkdir(parents=True, exist_ok=True)
        scripts_dir = out_dir / "Scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        audio_dir = out_dir / "Audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        channel_name = self.fetcher.channel_name or (selected[0].uploader if selected else "")
        channel_url = self.fetcher.channel_url or (selected[0].channel_url if selected else "")

        # 1. Single Titles.txt in root folder
        ZipPackager.export_single_titles_file(
            candidates=selected,
            output_file=out_dir / "Titles.txt",
            channel_name=channel_name,
            channel_url=channel_url,
        )

        self.box_transcript_progress.setVisible(True)
        self.btn_resume_prog.setVisible(False)
        self.bar_transcripts.setValue(0)
        self.lbl_transcript_status.setText(f"Fetching scripts: 0 / {len(selected)}...")
        self.lbl_progress_details.setText("Checking disk & retrieving scripts live...")

        self.transcripts_thread = BatchTranscriptThread(
            candidates=selected,
            existing_transcripts=self.transcripts_dict,
            output_dir=scripts_dir,
        )

        def _on_item_prog(cur, tot, done_c, skip_c, v_lbl, title):
            pct = int((cur / tot) * 100) if tot > 0 else 0
            self.bar_transcripts.setValue(pct)
            short_title = (title[:30] + "...") if len(title) > 30 else title
            self.lbl_transcript_status.setText(f"Scripts: {cur} / {tot} ({pct}%) - [{v_lbl}] {short_title}")
            rem = tot - cur
            self.lbl_progress_details.setText(f"✓ Completed: {done_c}  |  ⏭ Skipped: {skip_c}  |  ⏳ Remaining: {rem}  |  Total: {tot}")

        def _on_item(vid_id, text):
            self.transcripts_dict[vid_id] = text

        def _on_done(is_completed):
            self.box_transcript_progress.setVisible(False)
            TranscriptFetcher.export_transcripts_to_folder(
                candidates=selected,
                transcripts_dict=self.transcripts_dict,
                output_dir=scripts_dir,
            )
            self._populate_table()

            items = []
            for cand in selected:
                item = DownloadItem(
                    url=cand.url,
                    media_type="Audio",
                    quality=self.combo_quality.currentText(),
                    format_ext=self.combo_format.currentText(),
                    custom_output_dir=str(audio_dir),
                    version_label=cand.version_label,
                    title=cand.title,
                    channel=cand.uploader,
                    duration_sec=cand.duration,
                    video_id=cand.video_id,
                )
                items.append(item)

            self.queue_manager.add_items(items)
            self.queue_manager.start()

            QMessageBox.information(
                self,
                "Batch Package Started",
                f"Queued all {len(items)} versions for matched Title + Script + MP3 generation!\n\n"
                f"• Titles: {out_dir / 'Titles.txt'} (1 single TXT)\n"
                f"• Scripts: {scripts_dir}\n"
                f"• Audio: {audio_dir} (V1.mp3, V2.mp3...)",
            )
            self.switch_to_downloads_requested.emit()

        self.transcripts_thread.item_progress.connect(_on_item_prog)
        self.transcripts_thread.item_fetched.connect(_on_item)
        self.transcripts_thread.all_finished.connect(_on_done)
        self.transcripts_thread.start()

    def _bulk_download_all_together(self):
        """Unified simultaneous download of all selected items in parallel."""
        self._download_selected_items_clicked()


