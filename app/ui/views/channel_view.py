"""
Channel & Media Downloader Pro v3.0 with Custom Data Selection, Forced Transcript Discovery,
Video Quality Selection, Dynamic Sorting (New/Old), Correct V-Numbering, and Flexible V-Ranges.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

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
        for idx, cand in enumerate(self.candidates, start=1):
            if self._is_cancelled:
                self.all_finished.emit(False)
                return

            # 1. Check if already fetched in memory
            if cand.video_id in self.existing_transcripts and self.existing_transcripts[cand.video_id]:
                self.progress_signal.emit(idx, total, f"(Resumed) {cand.title}")
                self.item_fetched.emit(cand.video_id, self.existing_transcripts[cand.video_id])
                continue

            # 2. Check if already saved on disk
            if self.output_dir:
                disk_file = self.output_dir / f"{cand.version_label} Script.txt"
                if disk_file.exists() and disk_file.stat().st_size > 0:
                    try:
                        with open(disk_file, "r", encoding="utf-8") as f:
                            saved_text = f.read()
                        if saved_text and not saved_text.startswith("[No transcript"):
                            self.progress_signal.emit(idx, total, f"(Saved on disk) {cand.title}")
                            self.item_fetched.emit(cand.video_id, saved_text)
                            continue
                    except Exception:
                        pass

            self.progress_signal.emit(idx, total, cand.title)
            try:
                text, method, diag = TranscriptFetcher.fetch_video_transcript_with_diagnostics(cand.video_id)
                if text:
                    self.item_fetched.emit(cand.video_id, text)
                    self.item_diagnostics.emit(cand.video_id, method, "")
                else:
                    self.item_diagnostics.emit(cand.video_id, method, diag or "")
            except Exception as e:
                logger.debug(f"Transcript fetch error for {cand.video_id}: {e}")
                self.item_diagnostics.emit(cand.video_id, "Error", str(e))

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
    all_finished = Signal(bool, list)

    def __init__(self, candidates: List[ChannelCandidate], output_dir: Path):
        super().__init__()
        self.candidates = candidates
        self.output_dir = output_dir
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        saved = ChannelAssetsFetcher.download_all_thumbnails(
            candidates=self.candidates,
            output_dir=self.output_dir,
            progress_callback=lambda cur, tot, title: self.progress_signal.emit(cur, tot, title),
            is_cancelled=lambda: self._is_cancelled,
        )
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
        lbl_title = QLabel("Channel & Media Downloader Pro v3.0")
        lbl_title.setStyleSheet("font-size: 22px; font-weight: 700; color: #ffffff;")
        lbl_sub = QLabel(
            "Unified YouTube data extractor with Custom Data Selection, Forced 5-Tier Discovery Cascade, "
            "Video Quality Selection, Dynamic Sorting (New/Old), and Correct V-Numbering."
        )
        lbl_sub.setWordWrap(True)
        lbl_sub.setStyleSheet("color: #9d9da8;")
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

        fetch_layout.addWidget(QLabel("Max Videos:"), 1, 2)
        self.combo_count = QComboBox()
        self.combo_count.setEditable(True)
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

        # 9 Selection Checkboxes organized in clean grid
        grid_sel = QGridLayout()
        grid_sel.setSpacing(10)

        self.chk_titles = QCheckBox("1. Video Title (Formatted TXT in Titles/ folder)")
        self.chk_titles.setChecked(True)
        self.chk_titles.setToolTip("Saves each title in Titles/ with Competitor Channel Name & Link at the top")
        grid_sel.addWidget(self.chk_titles, 0, 0)

        self.chk_scripts = QCheckBox("2. Script / Transcript (Forced 5-tier discovery cascade in Scripts/)")
        self.chk_scripts.setChecked(True)
        self.chk_scripts.setToolTip("Active search across manual CC, auto-captions, translations, yt-dlp, and timedtext")
        grid_sel.addWidget(self.chk_scripts, 0, 1)

        self.chk_mp3s = QCheckBox("3. Audio Voiceover (V1.mp3... parallel download)")
        self.chk_mp3s.setChecked(True)
        self.chk_mp3s.setToolTip("Parallel MP3 audio downloads named V1.mp3, V2.mp3...")
        grid_sel.addWidget(self.chk_mp3s, 0, 2)

        self.chk_metadata = QCheckBox("4. Purified Metadata (Cleaned TXT in Metadata/ folder)")
        self.chk_metadata.setChecked(True)
        self.chk_metadata.setToolTip("Strips all competitor URLs, promotional links, social media handles, and branding")
        grid_sel.addWidget(self.chk_metadata, 1, 0)

        self.chk_descriptions = QCheckBox("5. Description (Descriptions/ folder)")
        self.chk_descriptions.setChecked(True)
        self.chk_descriptions.setToolTip("Saves full original video descriptions named V1 Description.txt...")
        grid_sel.addWidget(self.chk_descriptions, 1, 1)

        self.chk_tags = QCheckBox("6. Tags (Tags/ folder + master all_tags.txt)")
        self.chk_tags.setChecked(True)
        self.chk_tags.setToolTip("Extracts video keywords into individual TXT files and a combined master list")
        grid_sel.addWidget(self.chk_tags, 1, 2)

        self.chk_thumbnails = QCheckBox("7. Thumbnails (Sequential V1 Thumbnail.. in Thumbnails/)")
        self.chk_thumbnails.setChecked(True)
        self.chk_thumbnails.setToolTip("Highest resolution thumbnails named V1 Thumbnail.jpg, V2 Thumbnail.jpg...")
        grid_sel.addWidget(self.chk_thumbnails, 2, 0)

        self.chk_channel_assets = QCheckBox("8. Channel Assets (Banner & Logo in Channel Assets/)")
        self.chk_channel_assets.setChecked(True)
        self.chk_channel_assets.setToolTip("Competitor channel banner and avatar/logo images")
        grid_sel.addWidget(self.chk_channel_assets, 2, 1)

        self.chk_videos = QCheckBox("9. Video File (Full video in selected resolution & format)")
        self.chk_videos.setChecked(False)  # Unchecked by default to save bandwidth unless explicitly wanted
        self.chk_videos.setToolTip("Downloads actual video file (MP4/MKV) at selected quality (4K, 1080p, 720p...)")
        grid_sel.addWidget(self.chk_videos, 2, 2)

        sel_layout.addLayout(grid_sel)

        # Selection presets and Launch Button
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

        btn_preset_text = QPushButton("Text & Metadata Only")
        btn_preset_text.clicked.connect(self._preset_text_metadata_data)
        bottom_sel_row.addWidget(btn_preset_text)

        btn_preset_media = QPushButton("Media Only")
        btn_preset_media.clicked.connect(self._preset_media_only_data)
        bottom_sel_row.addWidget(btn_preset_media)

        btn_preset_comp = QPushButton("Competitor Package (Default)")
        btn_preset_comp.clicked.connect(self._preset_competitor_package_data)
        bottom_sel_row.addWidget(btn_preset_comp)

        bottom_sel_row.addStretch()

        self.btn_download_selected = QPushButton("🚀 DOWNLOAD SELECTED DATA NOW")
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
        fmt_layout.addWidget(self.txt_out_dir, 2, 1, 1, 2)

        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_dir)
        fmt_layout.addWidget(btn_browse, 2, 3)

        layout.addWidget(box_fmt)

        # -------------------------------------------------------------
        # 4. Live Download Progress Box (Visible during batch downloads)
        # -------------------------------------------------------------
        self.box_transcript_progress = QGroupBox("⚡ Download Progress")
        self.box_transcript_progress.setVisible(False)
        prog_layout = QVBoxLayout(self.box_transcript_progress)
        prog_layout.setSpacing(6)

        prog_top = QHBoxLayout()
        self.lbl_transcript_status = QLabel("Processing: 0 / 0...")
        self.lbl_transcript_status.setStyleSheet("font-weight: 600; color: #007aff;")
        self.btn_view_diagnostics = QPushButton("ℹ View Diagnostics Report")
        self.btn_view_diagnostics.setVisible(False)
        self.btn_view_diagnostics.clicked.connect(self._show_diagnostics_report)

        self.btn_stop_transcripts = QPushButton("⏹ Stop Progress")
        self.btn_stop_transcripts.setStyleSheet("background-color: #ff3b30; color: #ffffff; font-weight: 600;")
        self.btn_stop_transcripts.clicked.connect(self._stop_all_batch_threads)

        prog_top.addWidget(self.lbl_transcript_status)
        prog_top.addStretch()
        prog_top.addWidget(self.btn_view_diagnostics)
        prog_top.addWidget(self.btn_stop_transcripts)
        prog_layout.addLayout(prog_top)

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
        self.txt_v_range.setPlaceholderText("e.g. V1-V10, V20 to V30, V5, 1-15, all")
        self.txt_v_range.returnPressed.connect(self._apply_v_range_clicked)
        range_bar.addWidget(self.txt_v_range)

        self.btn_apply_range = QPushButton("Apply Range")
        self.btn_apply_range.clicked.connect(self._apply_v_range_clicked)
        range_bar.addWidget(self.btn_apply_range)

        # Preset range buttons
        btn_r10 = QPushButton("V1-V10")
        btn_r10.clicked.connect(lambda: self._select_range_preset("V1-V10"))
        range_bar.addWidget(btn_r10)

        btn_r25 = QPushButton("V1-V25")
        btn_r25.clicked.connect(lambda: self._select_range_preset("V1-V25"))
        range_bar.addWidget(btn_r25)

        btn_r50 = QPushButton("V1-V50")
        btn_r50.clicked.connect(lambda: self._select_range_preset("V1-V50"))
        range_bar.addWidget(btn_r50)

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

        self.btn_copy_all_titles = QPushButton("📋 1. Copy All Titles (V1. Title...)")
        self.btn_copy_all_titles.clicked.connect(self._copy_all_titles_clicked)
        btn_grid.addWidget(self.btn_copy_all_titles, 0, 0)

        self.btn_bulk_titles = QPushButton("📑 2. Download Titles (Titles Folder)")
        self.btn_bulk_titles.clicked.connect(self._bulk_download_titles)
        btn_grid.addWidget(self.btn_bulk_titles, 0, 1)

        self.btn_bulk_scripts = QPushButton("📄 3. Download All Scripts (V1 Script.txt...)")
        self.btn_bulk_scripts.clicked.connect(self._bulk_download_scripts)
        btn_grid.addWidget(self.btn_bulk_scripts, 1, 0)

        self.btn_bulk_mp3s = QPushButton("🎵 4. Download All MP3s (V1.mp3, V2.mp3...)")
        self.btn_bulk_mp3s.clicked.connect(self._bulk_download_mp3s)
        btn_grid.addWidget(self.btn_bulk_mp3s, 1, 1)

        self.btn_bulk_metadata = QPushButton("🏷 5. Download Metadata (V1 Metadata.txt...)")
        self.btn_bulk_metadata.clicked.connect(self._bulk_download_metadata)
        btn_grid.addWidget(self.btn_bulk_metadata, 2, 0)

        self.btn_bulk_thumbnails = QPushButton("🖼 6. Download Thumbnails (V1 Thumbnail...)")
        self.btn_bulk_thumbnails.clicked.connect(self._bulk_download_thumbnails)
        btn_grid.addWidget(self.btn_bulk_thumbnails, 2, 1)

        self.btn_bulk_channel_assets = QPushButton("🎨 7. Channel Assets (Banner & Logo)")
        self.btn_bulk_channel_assets.clicked.connect(self._bulk_download_channel_assets)
        btn_grid.addWidget(self.btn_bulk_channel_assets, 3, 0)

        self.btn_bulk_all_3 = QPushButton("📦 8. Download 3 (Titles + Scripts + MP3s)")
        self.btn_bulk_all_3.clicked.connect(self._bulk_download_all_3)
        btn_grid.addWidget(self.btn_bulk_all_3, 3, 1)

        self.btn_bulk_all = QPushButton("🌟 DOWNLOAD COMPLETE COMPETITOR PACKAGE (All Organized)")
        self.btn_bulk_all.setStyleSheet("background-color: #34c759; color: #ffffff; font-weight: 700; font-size: 13px; padding: 10px;")
        self.btn_bulk_all.clicked.connect(self._bulk_download_all_together)
        btn_grid.addWidget(self.btn_bulk_all, 4, 0, 1, 2)

        bulk_layout.addLayout(btn_grid)
        layout.addWidget(box_bulk)

        scroll.setWidget(content)
        root_layout.addWidget(scroll)

    # ==================== CONTROLS & RANGE LOGIC ====================

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.txt_out_dir.text())
        if d:
            self.txt_out_dir.setText(d)

    def _get_selected_count(self) -> Optional[int]:
        val = self.combo_count.currentText().strip()
        if not val or val.lower().startswith("all"):
            return None
        digits = "".join(filter(str.isdigit, val))
        if digits:
            return int(digits)
        return DEFAULT_CHANNEL_FETCH_COUNT

    def _on_order_changed(self):
        """Dynamic Re-sorting: switches order (New/Old) and correctly re-numbers V1..Vn."""
        if not self.candidates:
            return
        order = self.combo_order.currentText()
        self.candidates = ChannelFetcher.sort_candidates(self.candidates, order)
        self._populate_table()
        self._update_range_status()
        self.lbl_table_status.setText(f"Re-sorted {len(self.candidates)} videos to {order}. V1 is now the first video in this order.")

    def _apply_v_range_clicked(self):
        """Applies V-Number range e.g. 'V1-V10', 'V20 to V30', 'V1, V5, V10', '1-15'."""
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
                f"Could not parse range '{query}'.\n\nExamples of valid formats:\n• V1-V10\n• V20 to V30\n• V1, V5, V10\n• 1-10, 15\n• all",
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
        self.chk_mp3s.setChecked(True)
        self.chk_videos.setChecked(True)
        self.chk_metadata.setChecked(True)
        self.chk_descriptions.setChecked(True)
        self.chk_tags.setChecked(True)
        self.chk_thumbnails.setChecked(True)
        self.chk_channel_assets.setChecked(True)

    def _preset_deselect_all_data(self):
        self.chk_titles.setChecked(False)
        self.chk_scripts.setChecked(False)
        self.chk_mp3s.setChecked(False)
        self.chk_videos.setChecked(False)
        self.chk_metadata.setChecked(False)
        self.chk_descriptions.setChecked(False)
        self.chk_tags.setChecked(False)
        self.chk_thumbnails.setChecked(False)
        self.chk_channel_assets.setChecked(False)

    def _preset_text_metadata_data(self):
        self._preset_deselect_all_data()
        self.chk_titles.setChecked(True)
        self.chk_scripts.setChecked(True)
        self.chk_metadata.setChecked(True)
        self.chk_descriptions.setChecked(True)
        self.chk_tags.setChecked(True)

    def _preset_media_only_data(self):
        self._preset_deselect_all_data()
        self.chk_thumbnails.setChecked(True)
        self.chk_mp3s.setChecked(True)
        self.chk_videos.setChecked(True)
        self.chk_channel_assets.setChecked(True)

    def _preset_competitor_package_data(self):
        self._preset_deselect_all_data()
        self.chk_titles.setChecked(True)
        self.chk_scripts.setChecked(True)
        self.chk_mp3s.setChecked(True)
        self.chk_metadata.setChecked(True)
        self.chk_descriptions.setChecked(True)
        self.chk_tags.setChecked(True)
        self.chk_thumbnails.setChecked(True)
        self.chk_channel_assets.setChecked(True)

    # ==================== FETCH ACTIONS ====================

    def _fetch_videos_clicked(self):
        url = self.txt_url.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", "Please enter a YouTube channel, playlist, or video URL.")
            return

        self.btn_fetch.setEnabled(False)
        self.btn_stop_fetch.setEnabled(True)
        count = self._get_selected_count()
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
        for th in [self.transcripts_thread, self.metadata_thread, self.thumbnail_thread, self.channel_assets_thread]:
            if th and th.isRunning():
                if hasattr(th, "cancel"):
                    th.cancel()
                try:
                    th.terminate()
                except Exception:
                    pass
                th.quit()
        self.box_transcript_progress.setVisible(False)
        self.lbl_table_status.setText("All background processes stopped.")

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
            custom_dir = out_dir
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

    def _download_selected_items_clicked(self):
        """Unified download pipeline: processes only checked data types for selected range."""
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
        want_mp3s = self.chk_mp3s.isChecked()
        want_videos = self.chk_videos.isChecked()
        want_metadata = self.chk_metadata.isChecked()
        want_descriptions = self.chk_descriptions.isChecked()
        want_tags = self.chk_tags.isChecked()
        want_thumbnails = self.chk_thumbnails.isChecked()
        want_channel_assets = self.chk_channel_assets.isChecked()

        if not any([
            want_titles,
            want_scripts,
            want_mp3s,
            want_videos,
            want_metadata,
            want_descriptions,
            want_tags,
            want_thumbnails,
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

        # Reset diagnostics
        self.diagnostics_dict.clear()
        self.btn_view_diagnostics.setVisible(False)

        # 1. Immediate sync export: Titles (Instant)
        saved_titles_count = 0
        if want_titles:
            titles_dir = base_dir / "Titles"
            titles_dir.mkdir(parents=True, exist_ok=True)
            saved = ZipPackager.export_titles_folder(
                candidates=selected,
                output_dir=base_dir,
                channel_name=channel_name,
                channel_url=channel_url,
            )
            saved_titles_count = len(saved)

        # 2. Async Channel Assets (fires in background)
        if want_channel_assets and channel_url:
            assets_dir = base_dir / "Channel Assets"
            assets_dir.mkdir(parents=True, exist_ok=True)
            self.channel_assets_thread = ChannelAssetsThread(
                channel_url=channel_url,
                output_dir=assets_dir,
                banner_url=self.fetcher.banner_url,
                logo_url=self.fetcher.logo_url,
            )
            self.channel_assets_thread.start()

        # Build sequential phase queue for remaining heavy tasks
        phases = []
        if want_thumbnails:
            phases.append("thumbnails")
        if want_metadata or want_tags or want_descriptions:
            phases.append("metadata")
        if want_scripts:
            phases.append("scripts")

        self.box_transcript_progress.setVisible(True)
        self.bar_transcripts.setValue(0)

        self._active_pipeline = {
            "phases": phases,
            "current_index": 0,
            "selected": selected,
            "base_dir": base_dir,
            "channel_name": channel_name,
            "channel_url": channel_url,
            "want_titles": want_titles,
            "want_descriptions": want_descriptions,
            "want_tags": want_tags,
            "want_metadata": want_metadata,
            "want_scripts": want_scripts,
            "want_thumbnails": want_thumbnails,
            "want_channel_assets": want_channel_assets,
            "want_mp3s": want_mp3s,
            "want_videos": want_videos,
            "saved_titles": saved_titles_count,
            "saved_thumbs": 0,
            "saved_meta": 0,
            "saved_scripts": 0,
            "queued_media": 0,
        }

        self._run_current_pipeline_phase()

    def _run_current_pipeline_phase(self):
        pipe = getattr(self, "_active_pipeline", None)
        if not pipe:
            return

        phases = pipe["phases"]
        idx = pipe["current_index"]
        selected = pipe["selected"]
        base_dir = pipe["base_dir"]
        total_phases = len(phases)

        if idx >= total_phases:
            # All background phases finished! Now finalize & queue media
            self._finalize_download_pipeline()
            return

        current_phase = phases[idx]
        step_num = idx + 1

        if current_phase == "thumbnails":
            thumb_dir = base_dir / "Thumbnails"
            thumb_dir.mkdir(parents=True, exist_ok=True)
            self.bar_transcripts.setValue(0)
            self.lbl_transcript_status.setText(f"Phase {step_num}/{total_phases}: Downloading thumbnails (0 / {len(selected)})...")

            self.thumbnail_thread = BatchThumbnailThread(candidates=selected, output_dir=thumb_dir)

            def _on_thumb_prog(cur, tot, t):
                pct = int((cur / tot) * 100) if tot > 0 else 0
                self.bar_transcripts.setValue(pct)
                short_t = (t[:35] + "...") if len(t) > 35 else t
                self.lbl_transcript_status.setText(f"Phase {step_num}/{total_phases}: Thumbnails {cur}/{tot} ({pct}%) - {short_t}")

            def _on_thumb_done(is_ok, saved_list):
                if not is_ok:
                    self.box_transcript_progress.setVisible(False)
                    return
                pipe["saved_thumbs"] = len(saved_list)
                pipe["current_index"] += 1
                self._run_current_pipeline_phase()

            self.thumbnail_thread.progress_signal.connect(_on_thumb_prog)
            self.thumbnail_thread.all_finished.connect(_on_thumb_done)
            self.thumbnail_thread.start()

        elif current_phase == "metadata":
            meta_dir = base_dir / "Metadata"
            tags_dir = base_dir / "Tags"
            desc_dir = base_dir / "Descriptions"
            if pipe["want_metadata"]:
                meta_dir.mkdir(parents=True, exist_ok=True)
            if pipe["want_tags"]:
                tags_dir.mkdir(parents=True, exist_ok=True)
            if pipe["want_descriptions"]:
                desc_dir.mkdir(parents=True, exist_ok=True)

            self.bar_transcripts.setValue(0)
            self.lbl_transcript_status.setText(f"Phase {step_num}/{total_phases}: Fetching metadata & tags (0 / {len(selected)})...")

            self.metadata_thread = BatchMetadataThread(
                candidates=selected,
                existing_metadata=self.metadata_dict,
                output_dir=meta_dir,
            )

            def _on_meta_prog(cur, tot, t):
                pct = int((cur / tot) * 100) if tot > 0 else 0
                self.bar_transcripts.setValue(pct)
                short_t = (t[:35] + "...") if len(t) > 35 else t
                self.lbl_transcript_status.setText(f"Phase {step_num}/{total_phases}: Metadata {cur}/{tot} ({pct}%) - {short_t}")

            def _on_meta_item(vid_id, info):
                self.metadata_dict[vid_id] = info

            def _on_meta_done(is_ok):
                if not is_ok:
                    self.box_transcript_progress.setVisible(False)
                    return

                if pipe["want_metadata"]:
                    saved_m = MetadataPurifier.export_metadata_to_folder(
                        candidates=selected,
                        metadata_dict=self.metadata_dict,
                        output_dir=meta_dir,
                        channel_name=pipe["channel_name"],
                        channel_url=pipe["channel_url"],
                    )
                    pipe["saved_meta"] = len(saved_m)

                if pipe["want_tags"]:
                    MetadataPurifier.export_tags_to_folder(
                        candidates=selected,
                        metadata_dict=self.metadata_dict,
                        output_dir=tags_dir,
                        channel_name=pipe["channel_name"],
                    )

                if pipe["want_descriptions"]:
                    MetadataPurifier.export_descriptions_to_folder(
                        candidates=selected,
                        metadata_dict=self.metadata_dict,
                        output_dir=desc_dir,
                        channel_name=pipe["channel_name"],
                        channel_url=pipe["channel_url"],
                    )

                pipe["current_index"] += 1
                self._run_current_pipeline_phase()

            self.metadata_thread.progress_signal.connect(_on_meta_prog)
            self.metadata_thread.item_fetched.connect(_on_meta_item)
            self.metadata_thread.all_finished.connect(_on_meta_done)
            self.metadata_thread.start()

        elif current_phase == "scripts":
            scripts_dir = base_dir / "Scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            self.bar_transcripts.setValue(0)
            self.lbl_transcript_status.setText(f"Phase {step_num}/{total_phases}: Discovering transcripts (0 / {len(selected)})...")

            self.transcripts_thread = BatchTranscriptThread(
                candidates=selected,
                existing_transcripts=self.transcripts_dict,
                output_dir=scripts_dir,
            )

            def _on_script_prog(cur, tot, t):
                pct = int((cur / tot) * 100) if tot > 0 else 0
                self.bar_transcripts.setValue(pct)
                short_t = (t[:35] + "...") if len(t) > 35 else t
                self.lbl_transcript_status.setText(f"Phase {step_num}/{total_phases}: Scripts {cur}/{tot} ({pct}%) - {short_t}")

            def _on_script_item(vid_id, text):
                self.transcripts_dict[vid_id] = text

            def _on_script_diag(vid_id, method, diag):
                self.diagnostics_dict[vid_id] = {"method": method, "diag": diag}

            def _on_script_done(is_ok):
                if not is_ok:
                    self.box_transcript_progress.setVisible(False)
                    return

                saved_s = TranscriptFetcher.export_transcripts_to_folder(
                    candidates=selected,
                    transcripts_dict=self.transcripts_dict,
                    output_dir=scripts_dir,
                )
                pipe["saved_scripts"] = len(saved_s)
                self._populate_table()

                pipe["current_index"] += 1
                self._run_current_pipeline_phase()

            self.transcripts_thread.progress_signal.connect(_on_script_prog)
            self.transcripts_thread.item_fetched.connect(_on_script_item)
            self.transcripts_thread.item_diagnostics.connect(_on_script_diag)
            self.transcripts_thread.all_finished.connect(_on_script_done)
            self.transcripts_thread.start()

    def _finalize_download_pipeline(self):
        pipe = getattr(self, "_active_pipeline", None)
        if not pipe:
            return

        self.box_transcript_progress.setVisible(False)
        selected = pipe["selected"]
        base_dir = pipe["base_dir"]

        media_items = []
        # Queue MP3s if selected
        if pipe["want_mp3s"]:
            for cand in selected:
                item = DownloadItem(
                    url=cand.url,
                    media_type="Audio",
                    quality=self.combo_quality.currentText(),
                    format_ext=self.combo_format.currentText(),
                    custom_output_dir=str(base_dir),
                    version_label=cand.version_label,
                    title=cand.title,
                    channel=cand.uploader,
                    duration_sec=cand.duration,
                    video_id=cand.video_id,
                )
                media_items.append(item)

        # Queue Videos if selected
        if pipe["want_videos"]:
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

        if media_items:
            self.queue_manager.add_items(media_items)
            self.queue_manager.start()
            pipe["queued_media"] = len(media_items)

        # Build summary report
        order = self.combo_order.currentText()
        summary_lines = [f"Successfully processed {len(selected)} videos in {order} order!\n"]
        if pipe["want_titles"]:
            summary_lines.append(f"• Titles: {base_dir / 'Titles'} ({pipe['saved_titles']} files)")
        if pipe["want_descriptions"]:
            summary_lines.append(f"• Descriptions: {base_dir / 'Descriptions'}")
        if pipe["want_tags"]:
            summary_lines.append(f"• Tags: {base_dir / 'Tags'} (+ master all_tags.txt)")
        if pipe["want_metadata"]:
            summary_lines.append(f"• Purified Metadata: {base_dir / 'Metadata'} ({pipe['saved_meta']} files)")
        if pipe["want_thumbnails"]:
            summary_lines.append(f"• Thumbnails: {base_dir / 'Thumbnails'} ({pipe['saved_thumbs']} files)")
        if pipe["want_scripts"]:
            summary_lines.append(f"• Scripts: {base_dir / 'Scripts'} ({pipe['saved_scripts']} transcripts found)")
        if pipe["want_channel_assets"]:
            summary_lines.append(f"• Channel Assets: {base_dir / 'Channel Assets'}")
        if pipe["want_mp3s"]:
            summary_lines.append(f"• Audio MP3: Queued {len(selected)} tasks to Download Queue")
        if pipe["want_videos"]:
            summary_lines.append(
                f"• Video Files: Queued {len(selected)} tasks ({self.combo_video_quality.currentText()}) to {base_dir / 'Videos'}"
            )

        # Check if any transcripts were missing
        failed_scripts = [
            cand
            for cand in selected
            if pipe["want_scripts"] and (cand.video_id not in self.transcripts_dict or not self.transcripts_dict[cand.video_id])
        ]
        if failed_scripts:
            summary_lines.append(
                f"\nNotice: {len(failed_scripts)} video(s) had no CC available on YouTube. Click 'View Diagnostics Report' to see reasons and solutions."
            )
            self.btn_view_diagnostics.setVisible(True)

        QMessageBox.information(
            self,
            "Data Download Complete",
            "\n".join(summary_lines),
        )

        if media_items:
            self.switch_to_downloads_requested.emit()
        elif sys.platform == "darwin":
            subprocess.run(["open", str(base_dir)])

        self._active_pipeline = None

    def _show_diagnostics_report(self):
        """Displays modal diagnostics for forced discovery attempts."""
        selected = self._sync_selected_candidates() or self.candidates
        dlg = QDialog(self)
        dlg.setWindowTitle("Forced Script & Data Discovery Diagnostics")
        dlg.resize(760, 520)
        l = QVBoxLayout(dlg)

        lbl = QLabel("<b>Forced Discovery Diagnostics & Recovery Report</b>")
        lbl.setStyleSheet("font-size: 15px; color: #ffffff;")
        l.addWidget(lbl)

        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setStyleSheet("background-color: #1a1a20; font-family: monospace; font-size: 12px; line-height: 1.4; padding: 8px;")

        lines = ["=== FORCED DATA DISCOVERY REPORT ===", ""]
        found_count = 0
        missing_count = 0

        for cand in selected:
            vid_id = cand.video_id
            diag_entry = self.diagnostics_dict.get(vid_id, {})
            has_script = vid_id in self.transcripts_dict and bool(self.transcripts_dict[vid_id])

            if has_script:
                found_count += 1
                lines.append(f"[{cand.version_label}] ✓ RETRIEVED: {cand.title}")
                method = diag_entry.get("method", "Standard/Cascade")
                lines.append(f"     Source Method: {method}")
            else:
                missing_count += 1
                lines.append(f"[{cand.version_label}] ✗ NOT FOUND: {cand.title}")
                diag_msg = diag_entry.get("diag") or "Creator has captions disabled or YouTube auto-captions have not processed yet."
                lines.append(f"     Diagnostics: {diag_msg}")
                lines.append(f"     URL: {cand.url}")
            lines.append("-" * 65)

        lines.insert(2, f"Total Checked: {len(selected)} | Found: {found_count} | Unavailable: {missing_count}\n")
        txt.setPlainText("\n".join(lines))
        l.addWidget(txt)

        btn_row = QHBoxLayout()
        btn_copy = QPushButton("📋 Copy Report")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(txt.toPlainText()))
        btn_close = QPushButton("Close")
        btn_close.setObjectName("primaryBtn")
        btn_close.clicked.connect(dlg.accept)
        btn_row.addStretch()
        btn_row.addWidget(btn_copy)
        btn_row.addWidget(btn_close)
        l.addLayout(btn_row)

        dlg.exec()

    # ==================== PRESERVED 1-CLICK BULK ACTIONS ====================

    def _bulk_download_titles(self):
        selected = self._sync_selected_candidates()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select at least one video.")
            return

        out_dir = Path(self.txt_out_dir.text().strip())
        channel_name = self.fetcher.channel_name or (selected[0].uploader if selected else "")
        channel_url = self.fetcher.channel_url or (selected[0].channel_url if selected else "")

        saved = ZipPackager.export_titles_folder(
            candidates=selected,
            output_dir=out_dir,
            channel_name=channel_name,
            channel_url=channel_url,
        )
        titles_dir = out_dir / "Titles"
        QMessageBox.information(
            self,
            "Titles Saved",
            f"Successfully saved {len(saved)} title TXT files to:\n\n{titles_dir}\n\n"
            "Format in each file:\n"
            "Line 1: Competitor Channel Name\n"
            "Line 2: Competitor Channel Link\n"
            "Line 3: V{i}. Video Title",
        )
        if sys.platform == "darwin":
            subprocess.run(["open", str(titles_dir)])

    def _bulk_download_scripts(self):
        selected = self._sync_selected_candidates()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select at least one video.")
            return

        out_dir = Path(self.txt_out_dir.text().strip()) / "Scripts"
        out_dir.mkdir(parents=True, exist_ok=True)

        self.box_transcript_progress.setVisible(True)
        self.bar_transcripts.setValue(0)
        self.lbl_transcript_status.setText(f"Fetching scripts: 0 / {len(selected)} (0%)...")

        self.transcripts_thread = BatchTranscriptThread(
            candidates=selected,
            existing_transcripts=self.transcripts_dict,
            output_dir=out_dir,
        )

        def _on_progress(cur, tot, title):
            pct = int((cur / tot) * 100)
            self.bar_transcripts.setValue(pct)
            short_title = (title[:40] + "...") if len(title) > 40 else title
            self.lbl_transcript_status.setText(f"Downloading scripts: {cur} / {tot} ({pct}%) - {short_title}")

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
                self.lbl_table_status.setText(f"Exported {len(saved)} script files (V1 Script.txt..).")
                QMessageBox.information(
                    self,
                    "Scripts Exported",
                    f"Successfully exported {len(saved)} clean script files (V1 Script.txt, V2 Script.txt...) to:\n\n{out_dir}",
                )
                if sys.platform == "darwin":
                    subprocess.run(["open", str(out_dir)])
            else:
                self.lbl_table_status.setText(f"Script download stopped. Saved {len(saved)} files.")

        self.transcripts_thread.progress_signal.connect(_on_progress)
        self.transcripts_thread.item_fetched.connect(_on_item)
        self.transcripts_thread.item_diagnostics.connect(_on_diag)
        self.transcripts_thread.all_finished.connect(_on_done)
        self.transcripts_thread.start()

    def _bulk_download_mp3s(self):
        selected = self._sync_selected_candidates()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select at least one video.")
            return

        out_dir = self.txt_out_dir.text().strip()
        items = []
        for cand in selected:
            item = DownloadItem(
                url=cand.url,
                media_type="Audio",
                quality=self.combo_quality.currentText(),
                format_ext=self.combo_format.currentText(),
                custom_output_dir=out_dir,
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
            f"Added {len(items)} MP3 tasks (V1.mp3, V2.mp3...) to the queue!\n"
            "Will download 3 files simultaneously in parallel until all complete.",
        )
        self.switch_to_downloads_requested.emit()

    def _bulk_download_metadata(self):
        selected = self._sync_selected_candidates()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select at least one video.")
            return

        out_dir = Path(self.txt_out_dir.text().strip()) / "Metadata"
        out_dir.mkdir(parents=True, exist_ok=True)

        channel_name = self.fetcher.channel_name or (selected[0].uploader if selected else "")
        channel_url = self.fetcher.channel_url or (selected[0].channel_url if selected else "")

        self.box_transcript_progress.setVisible(True)
        self.bar_transcripts.setValue(0)
        self.lbl_transcript_status.setText(f"Purifying metadata: 0 / {len(selected)} (0%)...")

        self.metadata_thread = BatchMetadataThread(
            candidates=selected,
            existing_metadata=self.metadata_dict,
            output_dir=out_dir,
        )

        def _on_progress(cur, tot, title):
            pct = int((cur / tot) * 100)
            self.bar_transcripts.setValue(pct)
            short_t = (title[:40] + "...") if len(title) > 40 else title
            self.lbl_transcript_status.setText(f"Purifying metadata: {cur} / {tot} ({pct}%) - {short_t}")

        def _on_item(vid_id, info):
            self.metadata_dict[vid_id] = info

        def _on_done(is_completed):
            self.box_transcript_progress.setVisible(False)
            saved = MetadataPurifier.export_metadata_to_folder(
                candidates=selected,
                metadata_dict=self.metadata_dict,
                output_dir=out_dir,
                channel_name=channel_name,
                channel_url=channel_url,
            )
            if is_completed:
                self.lbl_table_status.setText(f"Exported {len(saved)} purified metadata files.")
                QMessageBox.information(
                    self,
                    "Metadata Exported",
                    f"Successfully exported {len(saved)} purified metadata files (V1 Metadata.txt, V2 Metadata.txt...) to:\n\n{out_dir}\n\n"
                    "All URLs, external links, promotional links, social links, and competitor branding have been removed.",
                )
                if sys.platform == "darwin":
                    subprocess.run(["open", str(out_dir)])
            else:
                self.lbl_table_status.setText(f"Metadata download stopped. Saved {len(saved)} files.")

        self.metadata_thread.progress_signal.connect(_on_progress)
        self.metadata_thread.item_fetched.connect(_on_item)
        self.metadata_thread.all_finished.connect(_on_done)
        self.metadata_thread.start()

    def _bulk_download_thumbnails(self):
        selected = self._sync_selected_candidates()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select at least one video.")
            return

        out_dir = Path(self.txt_out_dir.text().strip()) / "Thumbnails"
        out_dir.mkdir(parents=True, exist_ok=True)

        self.box_transcript_progress.setVisible(True)
        self.bar_transcripts.setValue(0)
        self.lbl_transcript_status.setText(f"Downloading thumbnails: 0 / {len(selected)} (0%)...")

        self.thumbnail_thread = BatchThumbnailThread(
            candidates=selected,
            output_dir=out_dir,
        )

        def _on_progress(cur, tot, title):
            pct = int((cur / tot) * 100)
            self.bar_transcripts.setValue(pct)
            short_t = (title[:40] + "...") if len(title) > 40 else title
            self.lbl_transcript_status.setText(f"Downloading thumbnails: {cur} / {tot} ({pct}%) - {short_t}")

        def _on_done(is_completed, saved):
            self.box_transcript_progress.setVisible(False)
            if is_completed:
                self.lbl_table_status.setText(f"Downloaded {len(saved)} thumbnails.")
                QMessageBox.information(
                    self,
                    "Thumbnails Downloaded",
                    f"Successfully downloaded {len(saved)} thumbnails (V1 Thumbnail.jpg, V2 Thumbnail.jpg...) to:\n\n{out_dir}",
                )
                if sys.platform == "darwin":
                    subprocess.run(["open", str(out_dir)])
            else:
                self.lbl_table_status.setText(f"Thumbnail download stopped. Saved {len(saved)} files.")

        self.thumbnail_thread.progress_signal.connect(_on_progress)
        self.thumbnail_thread.all_finished.connect(_on_done)
        self.thumbnail_thread.start()

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

        channel_name = self.fetcher.channel_name or (selected[0].uploader if selected else "")
        channel_url = self.fetcher.channel_url or (selected[0].channel_url if selected else "")

        ZipPackager.export_titles_folder(selected, out_dir, channel_name=channel_name, channel_url=channel_url)

        self.box_transcript_progress.setVisible(True)
        self.lbl_transcript_status.setText(f"Fetching scripts: 0 / {len(selected)}...")

        self.transcripts_thread = BatchTranscriptThread(
            candidates=selected,
            existing_transcripts=self.transcripts_dict,
            output_dir=scripts_dir,
        )

        def _on_progress(cur, tot, title):
            pct = int((cur / tot) * 100)
            self.bar_transcripts.setValue(pct)
            short_title = (title[:40] + "...") if len(title) > 40 else title
            self.lbl_transcript_status.setText(f"Downloading scripts: {cur} / {tot} ({pct}%) - {short_title}")

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
                "Batch Package Started",
                f"Queued all {len(items)} versions for matched Title + Script + MP3 generation!\n\n"
                f"• Titles saved to: {out_dir / 'Titles'}\n"
                f"• Scripts saved to: {scripts_dir}\n"
                f"• MP3s (V1.mp3, V2.mp3...) downloading to: {out_dir}",
            )
            self.switch_to_downloads_requested.emit()

        self.transcripts_thread.progress_signal.connect(_on_progress)
        self.transcripts_thread.item_fetched.connect(_on_item)
        self.transcripts_thread.all_finished.connect(_on_done)
        self.transcripts_thread.start()

    def _bulk_download_all_together(self):
        selected = self._sync_selected_candidates()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select at least one video.")
            return

        base_dir = Path(self.txt_out_dir.text().strip())
        base_dir.mkdir(parents=True, exist_ok=True)
        titles_dir = base_dir / "Titles"
        scripts_dir = base_dir / "Scripts"
        meta_dir = base_dir / "Metadata"
        thumb_dir = base_dir / "Thumbnails"
        assets_dir = base_dir / "Channel Assets"

        titles_dir.mkdir(parents=True, exist_ok=True)
        scripts_dir.mkdir(parents=True, exist_ok=True)
        meta_dir.mkdir(parents=True, exist_ok=True)
        thumb_dir.mkdir(parents=True, exist_ok=True)
        assets_dir.mkdir(parents=True, exist_ok=True)

        channel_name = self.fetcher.channel_name or (selected[0].uploader if selected else "")
        channel_url = self.fetcher.channel_url or (selected[0].channel_url if selected else self.txt_url.text().strip())

        ZipPackager.export_titles_folder(selected, titles_dir, channel_name=channel_name, channel_url=channel_url)

        if channel_url:
            self.channel_assets_thread = ChannelAssetsThread(
                channel_url=channel_url,
                output_dir=assets_dir,
                banner_url=self.fetcher.banner_url,
                logo_url=self.fetcher.logo_url,
            )
            self.channel_assets_thread.start()

        self.box_transcript_progress.setVisible(True)
        self.bar_transcripts.setValue(0)
        self.lbl_transcript_status.setText(f"Phase 1/3: Downloading thumbnails (0 / {len(selected)})...")

        self.thumbnail_thread = BatchThumbnailThread(
            candidates=selected,
            output_dir=thumb_dir,
        )

        def _on_thumb_prog(cur, tot, t):
            pct = int((cur / tot) * 100)
            self.bar_transcripts.setValue(pct)
            short_t = (t[:35] + "...") if len(t) > 35 else t
            self.lbl_transcript_status.setText(f"Phase 1/3: Thumbnails {cur}/{tot} ({pct}%) - {short_t}")

        def _on_thumb_done(is_ok, _):
            if not is_ok:
                self.box_transcript_progress.setVisible(False)
                return

            self.bar_transcripts.setValue(0)
            self.lbl_transcript_status.setText(f"Phase 2/3: Purifying metadata (0 / {len(selected)})...")

            self.metadata_thread = BatchMetadataThread(
                candidates=selected,
                existing_metadata=self.metadata_dict,
                output_dir=meta_dir,
            )

            def _on_meta_prog(cur, tot, t):
                pct = int((cur / tot) * 100)
                self.bar_transcripts.setValue(pct)
                short_t = (t[:35] + "...") if len(t) > 35 else t
                self.lbl_transcript_status.setText(f"Phase 2/3: Metadata {cur}/{tot} ({pct}%) - {short_t}")

            def _on_meta_item(vid_id, info):
                self.metadata_dict[vid_id] = info

            def _on_meta_done(is_meta_ok):
                if not is_meta_ok:
                    self.box_transcript_progress.setVisible(False)
                    return

                MetadataPurifier.export_metadata_to_folder(
                    candidates=selected,
                    metadata_dict=self.metadata_dict,
                    output_dir=meta_dir,
                    channel_name=channel_name,
                    channel_url=channel_url,
                )

                self.bar_transcripts.setValue(0)
                self.lbl_transcript_status.setText(f"Phase 3/3: Downloading scripts (0 / {len(selected)})...")

                self.transcripts_thread = BatchTranscriptThread(
                    candidates=selected,
                    existing_transcripts=self.transcripts_dict,
                    output_dir=scripts_dir,
                )

                def _on_script_prog(cur, tot, t):
                    pct = int((cur / tot) * 100)
                    self.bar_transcripts.setValue(pct)
                    short_t = (t[:35] + "...") if len(t) > 35 else t
                    self.lbl_transcript_status.setText(f"Phase 3/3: Scripts {cur}/{tot} ({pct}%) - {short_t}")

                def _on_script_item(vid_id, text):
                    self.transcripts_dict[vid_id] = text

                def _on_script_done(is_script_ok):
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
                            custom_output_dir=str(base_dir),
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
                        "Competitor Assets Package Complete",
                        f"All {len(selected)} videos processed from V1 onward!\n\n"
                        f"Organized output folders at: {base_dir}\n"
                        f"• Titles: {titles_dir}\n"
                        f"• Scripts: {scripts_dir}\n"
                        f"• Metadata: {meta_dir}\n"
                        f"• Thumbnails: {thumb_dir}\n"
                        f"• Channel Assets: {assets_dir}\n"
                        f"• MP3 Voiceovers: downloading to {base_dir}",
                    )
                    self.switch_to_downloads_requested.emit()
                    if sys.platform == "darwin":
                        subprocess.run(["open", str(base_dir)])

                self.transcripts_thread.progress_signal.connect(_on_script_prog)
                self.transcripts_thread.item_fetched.connect(_on_script_item)
                self.transcripts_thread.all_finished.connect(_on_script_done)
                self.transcripts_thread.start()

            self.metadata_thread.progress_signal.connect(_on_meta_prog)
            self.metadata_thread.item_fetched.connect(_on_meta_item)
            self.metadata_thread.all_finished.connect(_on_meta_done)
            self.metadata_thread.start()

        self.thumbnail_thread.progress_signal.connect(_on_thumb_prog)
        self.thumbnail_thread.all_finished.connect(_on_thumb_done)
        self.thumbnail_thread.start()


