"""
Channel & Media Downloader view with unified single input, New-to-Old V1..Vn asset matching,
real-time transcript download progress, stop/retry/clear controls, and 1-click title copying.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
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
    CHANNEL_ORDER_OPTIONS,
    CHANNEL_FETCH_RANGES,
    DEFAULT_CHANNEL_FETCH_COUNT,
    ORDER_LATEST_TO_OLDEST,
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
from app.utils.logger import logger


class ScriptViewerDialog(QDialog):
    def __init__(self, title: str, version_label: str, script_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Script Viewer - {version_label}")
        self.resize(680, 520)
        layout = QVBoxLayout(self)

        lbl = QLabel(f"<b>{version_label}. {title}</b>")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        lbl_info = QLabel("<i>Clean voiceover transcript (paragraphs formatted, timestamps & promotional CTAs removed)</i>")
        lbl_info.setStyleSheet("color: #9d9da8; font-size: 11px;")
        layout.addWidget(lbl_info)

        self.txt = QTextEdit()
        formatted_display = TranscriptFetcher.format_script_with_metadata(script_text, version_label)
        self.txt.setPlainText(formatted_display)
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

    def __init__(self, url: str, max_count: int, order: str):
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
                text = TranscriptFetcher.fetch_video_transcript(cand.video_id)
                if text:
                    self.item_fetched.emit(cand.video_id, text)
            except Exception as e:
                logger.debug(f"Transcript fetch error for {cand.video_id}: {e}")

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

        self.fetch_thread: Optional[MediaFetchThread] = None
        self.transcripts_thread: Optional[BatchTranscriptThread] = None
        self.metadata_thread: Optional[BatchMetadataThread] = None
        self.thumbnail_thread: Optional[BatchThumbnailThread] = None
        self.channel_assets_thread: Optional[ChannelAssetsThread] = None

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
        lbl_title = QLabel("Channel & Media Downloader")
        lbl_title.setStyleSheet("font-size: 22px; font-weight: 700; color: #ffffff;")
        lbl_sub = QLabel(
            "Extract videos in New to Old order (V1=Newest, V2=2nd, etc.) with matched Titles (V1. Title), Scripts (V1 Script.txt), and MP3s (V1.mp3)."
        )
        lbl_sub.setStyleSheet("color: #9d9da8;")
        layout.addWidget(lbl_title)
        layout.addWidget(lbl_sub)

        # 1. Fetch Options Card (Single Unified Input Field with Stop & Clear)
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
        fetch_layout.addWidget(self.combo_order, 1, 1)

        fetch_layout.addWidget(QLabel("Max Videos:"), 1, 2)
        self.combo_count = QComboBox()
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

        # 2. Output Settings Card
        box_fmt = QGroupBox("2. Format & Output Location")
        fmt_layout = QGridLayout(box_fmt)
        fmt_layout.setSpacing(10)

        fmt_layout.addWidget(QLabel("Media Format:"), 0, 0)
        self.combo_format = QComboBox()
        self.combo_format.addItems(AUDIO_FORMATS)
        self.combo_format.setCurrentText(self.settings.default_audio_format)
        fmt_layout.addWidget(self.combo_format, 0, 1)

        fmt_layout.addWidget(QLabel("Audio Quality:"), 0, 2)
        self.combo_quality = QComboBox()
        self.combo_quality.addItems(AUDIO_QUALITIES)
        self.combo_quality.setCurrentText(self.settings.default_audio_quality)
        fmt_layout.addWidget(self.combo_quality, 0, 3)

        fmt_layout.addWidget(QLabel("Save Folder:"), 1, 0)
        self.txt_out_dir = QLineEdit(self.settings.channels_dir)
        self.txt_out_dir.setReadOnly(True)
        fmt_layout.addWidget(self.txt_out_dir, 1, 1, 1, 2)

        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_dir)
        fmt_layout.addWidget(btn_browse, 1, 3)

        layout.addWidget(box_fmt)

        # 3. Live Batch Download Progress Box (Visible during batch downloads)
        self.box_transcript_progress = QGroupBox("⚡ Download Progress")
        self.box_transcript_progress.setVisible(False)
        prog_layout = QVBoxLayout(self.box_transcript_progress)
        prog_layout.setSpacing(6)

        prog_top = QHBoxLayout()
        self.lbl_transcript_status = QLabel("Processing: 0 / 0...")
        self.lbl_transcript_status.setStyleSheet("font-weight: 600; color: #007aff;")
        self.btn_stop_transcripts = QPushButton("⏹ Stop Progress")
        self.btn_stop_transcripts.setStyleSheet("background-color: #ff3b30; color: #ffffff; font-weight: 600;")
        self.btn_stop_transcripts.clicked.connect(self._stop_all_batch_threads)

        prog_top.addWidget(self.lbl_transcript_status)
        prog_top.addStretch()
        prog_top.addWidget(self.btn_stop_transcripts)
        prog_layout.addLayout(prog_top)

        self.bar_transcripts = QProgressBar()
        self.bar_transcripts.setRange(0, 100)
        self.bar_transcripts.setValue(0)
        self.bar_transcripts.setFormat("%p%")
        prog_layout.addWidget(self.bar_transcripts)

        layout.addWidget(self.box_transcript_progress)

        # 4. Simple Bulk Download Action Card
        box_bulk = QGroupBox("⚡ Bulk Actions & Competitor Assets (1-Click)")
        box_bulk.setStyleSheet("QGroupBox { border: 1.5px solid #007aff; }")
        bulk_layout = QVBoxLayout(box_bulk)
        bulk_layout.setSpacing(10)

        lbl_bulk_desc = QLabel("Select an option below to download matched competitor assets:")
        lbl_bulk_desc.setStyleSheet("color: #007aff; font-weight: 600;")
        bulk_layout.addWidget(lbl_bulk_desc)

        btn_grid = QGridLayout()
        btn_grid.setSpacing(10)

        # Option 1: Copy All Titles (1-Click)
        self.btn_copy_all_titles = QPushButton("📋 1. Copy All Titles (V1. Title...)")
        self.btn_copy_all_titles.setToolTip("Copy all formatted titles to clipboard in 1 click (V1. Title, V2. Title...)")
        self.btn_copy_all_titles.clicked.connect(self._copy_all_titles_clicked)
        btn_grid.addWidget(self.btn_copy_all_titles, 0, 0)

        # Option 2: Download Titles TXT (Titles Folder with Channel Name & Link)
        self.btn_bulk_titles = QPushButton("📑 2. Download Titles (Titles Folder)")
        self.btn_bulk_titles.setToolTip("Export video titles to Titles folder with Competitor Channel Name and Link at the top")
        self.btn_bulk_titles.clicked.connect(self._bulk_download_titles)
        btn_grid.addWidget(self.btn_bulk_titles, 0, 1)

        # Option 3: All TXT scripts
        self.btn_bulk_scripts = QPushButton("📄 3. Download All Scripts (V1 Script.txt...)")
        self.btn_bulk_scripts.setToolTip("Fetch and save all clean transcript paragraphs named V1 Script.txt, V2 Script.txt...")
        self.btn_bulk_scripts.clicked.connect(self._bulk_download_scripts)
        btn_grid.addWidget(self.btn_bulk_scripts, 1, 0)

        # Option 4: All MP3s
        self.btn_bulk_mp3s = QPushButton("🎵 4. Download All MP3s (V1.mp3, V2.mp3...)")
        self.btn_bulk_mp3s.setObjectName("primaryBtn")
        self.btn_bulk_mp3s.setToolTip("Queue MP3s named V1.mp3, V2.mp3... (3 parallel downloads)")
        self.btn_bulk_mp3s.clicked.connect(self._bulk_download_mp3s)
        btn_grid.addWidget(self.btn_bulk_mp3s, 1, 1)

        # Option 5: Competitor Metadata TXT
        self.btn_bulk_metadata = QPushButton("🏷 5. Download Metadata (V1 Metadata.txt...)")
        self.btn_bulk_metadata.setToolTip("Extract and purify metadata (remove URLs, social, promo, competitor branding) into Metadata/ folder")
        self.btn_bulk_metadata.clicked.connect(self._bulk_download_metadata)
        btn_grid.addWidget(self.btn_bulk_metadata, 2, 0)

        # Option 6: Competitor Thumbnails
        self.btn_bulk_thumbnails = QPushButton("🖼 6. Download Thumbnails (V1 Thumbnail...)")
        self.btn_bulk_thumbnails.setToolTip("Download highest-resolution thumbnails sequentially named V1 Thumbnail.jpg... into Thumbnails/ folder")
        self.btn_bulk_thumbnails.clicked.connect(self._bulk_download_thumbnails)
        btn_grid.addWidget(self.btn_bulk_thumbnails, 2, 1)

        # Option 7: Competitor Channel Assets (Banner & Logo)
        self.btn_bulk_channel_assets = QPushButton("🎨 7. Channel Assets (Banner & Logo)")
        self.btn_bulk_channel_assets.setToolTip("Download competitor channel banner and logo/avatar into Channel Assets/ folder")
        self.btn_bulk_channel_assets.clicked.connect(self._bulk_download_channel_assets)
        btn_grid.addWidget(self.btn_bulk_channel_assets, 3, 0)

        # Option 8: Download Original 3 (Titles + Scripts + MP3s)
        self.btn_bulk_all_3 = QPushButton("📦 8. Download 3 (Titles + Scripts + MP3s)")
        self.btn_bulk_all_3.setToolTip("Downloads Titles list, V{i} Script.txt, and V{i}.mp3 together, perfectly matched.")
        self.btn_bulk_all_3.clicked.connect(self._bulk_download_all_3)
        btn_grid.addWidget(self.btn_bulk_all_3, 3, 1)

        # Option 9: Complete Competitor Package (All 6 Assets Organized)
        self.btn_bulk_all = QPushButton("🌟 DOWNLOAD COMPLETE COMPETITOR PACKAGE (All Organized)")
        self.btn_bulk_all.setStyleSheet("background-color: #34c759; color: #ffffff; font-weight: 700; font-size: 13px; padding: 10px;")
        self.btn_bulk_all.setToolTip("One-click download: Titles folder, Scripts, MP3s, Purified Metadata, Thumbnails, and Channel Banner & Logo into organized folders.")
        self.btn_bulk_all.clicked.connect(self._bulk_download_all_together)
        btn_grid.addWidget(self.btn_bulk_all, 4, 0, 1, 2)

        bulk_layout.addLayout(btn_grid)
        layout.addWidget(box_bulk)

        # 5. Candidates Table
        box_table = QGroupBox("Matched Video Candidates & Assets")
        tbl_layout = QVBoxLayout(box_table)

        top_tbl_row = QHBoxLayout()
        self.lbl_table_status = QLabel("No videos loaded yet. Paste a link above and click Fetch Videos.")
        self.lbl_table_status.setStyleSheet("font-weight: 600; color: #9d9da8;")
        top_tbl_row.addWidget(self.lbl_table_status)
        top_tbl_row.addStretch()

        btn_select_all = QPushButton("Select All")
        btn_select_all.clicked.connect(self._select_all_candidates)
        btn_deselect_all = QPushButton("Deselect All")
        btn_deselect_all.clicked.connect(self._deselect_all_candidates)
        top_tbl_row.addWidget(btn_select_all)
        top_tbl_row.addWidget(btn_deselect_all)
        tbl_layout.addLayout(top_tbl_row)

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
        self.table.setColumnWidth(7, 115)
        self.table.verticalHeader().setVisible(False)
        tbl_layout.addWidget(self.table)

        layout.addWidget(box_table)

        scroll.setWidget(content)
        root_layout.addWidget(scroll)

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.txt_out_dir.text())
        if d:
            self.txt_out_dir.setText(d)

    def _fetch_videos_clicked(self):
        url = self.txt_url.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", "Please enter a YouTube channel, playlist, or video URL.")
            return

        self.btn_fetch.setEnabled(False)
        self.btn_stop_fetch.setEnabled(True)
        self.lbl_table_status.setText("Fetching videos in New to Old order...")
        count = int(self.combo_count.currentData() or self.combo_count.currentText())
        order = self.combo_order.currentText()

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
        self.lbl_table_status.setText("All background processes stopped immediately.")

    def _stop_transcripts_clicked(self):
        self._stop_all_batch_threads()

    def _clear_all_clicked(self):
        self._stop_fetch_clicked()
        self._stop_all_batch_threads()
        self.txt_url.clear()
        self.candidates.clear()
        self.transcripts_dict.clear()
        self.metadata_dict.clear()
        self.table.setRowCount(0)
        self.lbl_table_status.setText("Cleared. Paste a link above and click Fetch Videos.")
        self.box_transcript_progress.setVisible(False)


    def _on_fetch_finished(self, candidates: List[ChannelCandidate]):
        self.btn_fetch.setEnabled(True)
        self.btn_stop_fetch.setEnabled(False)
        self.candidates = candidates
        self.lbl_table_status.setText(f"Loaded {len(candidates)} videos (Ordered New to Old: V1=Newest).")
        self._populate_table()

    def _on_fetch_error(self, err_msg: str):
        self.btn_fetch.setEnabled(True)
        self.btn_stop_fetch.setEnabled(False)
        self.lbl_table_status.setText("Fetch failed.")
        QMessageBox.critical(self, "Fetch Error", f"Could not fetch videos: {err_msg}")

    def _populate_table(self):
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
            has_script = cand.video_id in self.transcripts_dict
            w_script = QWidget()
            l_script = QHBoxLayout(w_script)
            l_script.setContentsMargins(2, 2, 2, 2)
            btn_script = QPushButton("View Script" if has_script else "Get Script")
            btn_script.setStyleSheet("font-size: 11px; padding: 4px;")
            btn_script.clicked.connect(lambda _, c=cand: self._on_single_script_clicked(c))
            l_script.addWidget(btn_script)
            self.table.setCellWidget(row, 6, w_script)

            # 7: Single Action Download MP3
            w_act = QWidget()
            l_act = QHBoxLayout(w_act)
            l_act.setContentsMargins(2, 2, 2, 2)
            btn_dl = QPushButton("⬇ MP3")
            btn_dl.setObjectName("primaryBtn")
            btn_dl.setStyleSheet("font-size: 11px; padding: 4px;")
            btn_dl.clicked.connect(lambda _, c=cand: self._download_single_candidate(c))
            l_act.addWidget(btn_dl)
            self.table.setCellWidget(row, 7, w_act)

    def _copy_single_title(self, cand: ChannelCandidate, btn: QPushButton):
        formatted_title = f"{cand.version_label}. {cand.title}"
        QApplication.clipboard().setText(formatted_title)
        btn.setText("✓ Copied!")
        QTimer.singleShot(1400, lambda: btn.setText("📋 Copy"))

    def _copy_all_titles_clicked(self):
        """1-Click Copy All Titles formatted sequentially: V1. First Title, V2. Second Title..."""
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
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk:
                chk.setCheckState(Qt.Checked)

    def _deselect_all_candidates(self):
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk:
                chk.setCheckState(Qt.Unchecked)

    def _on_single_script_clicked(self, cand: ChannelCandidate):
        text = self.transcripts_dict.get(cand.video_id)
        if not text:
            self.lbl_table_status.setText(f"Fetching script for {cand.title}...")
            text = TranscriptFetcher.fetch_video_transcript(cand.video_id)
            if text:
                self.transcripts_dict[cand.video_id] = text
                self._populate_table()
                self.lbl_table_status.setText(f"Script loaded for {cand.version_label}.")
            else:
                self.lbl_table_status.setText(f"No script available for {cand.version_label}.")
                QMessageBox.warning(self, "No Script", f"No subtitles or transcript available for {cand.title}.")
                return

        dlg = ScriptViewerDialog(cand.title, cand.version_label, text, self)
        dlg.exec()

    def _download_single_candidate(self, cand: ChannelCandidate):
        out_dir = self.txt_out_dir.text().strip()
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
        self.queue_manager.add_item(item)
        self.queue_manager.start()
        self.switch_to_downloads_requested.emit()

    # ==================== BULK ACTIONS ====================

    def _bulk_download_titles(self):
        """Bulk Download Titles to single Titles folder with Channel Name & Link."""
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
        """Bulk Download All TXT scripts (V1 Script.txt, V2 Script.txt...) with live progress bar."""
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
        self.transcripts_thread.all_finished.connect(_on_done)
        self.transcripts_thread.start()

    def _bulk_download_mp3s(self):
        """Bulk Download All MP3s named V1.mp3, V2.mp3... (3 parallel downloads)."""
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
        """Bulk Download Purified Metadata (V1 Metadata.txt, V2 Metadata.txt...)."""
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
        """Bulk Download Thumbnails (V1 Thumbnail.jpg, V2 Thumbnail.jpg...)."""
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
        """Bulk Download Competitor Channel Assets (Banner & Logo)."""
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
        """Original 3 Assets Together (Titles + Scripts + MP3s)."""
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

        # 1. Export titles into Titles folder
        ZipPackager.export_titles_folder(selected, out_dir, channel_name=channel_name, channel_url=channel_url)

        # 2. Fetch and save all transcripts
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

            # 3. Queue all MP3 audio downloads
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
        """Bulk Download ALL Competitor Assets Organized (Titles, Scripts, MP3s, Metadata, Thumbnails, Channel Assets)."""
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

        # 1. Export Titles folder (V1 Title.txt... with Channel Name, Link, Title)
        ZipPackager.export_titles_folder(selected, titles_dir, channel_name=channel_name, channel_url=channel_url)

        # 2. Download Channel Assets in background
        if channel_url:
            self.channel_assets_thread = ChannelAssetsThread(
                channel_url=channel_url,
                output_dir=assets_dir,
                banner_url=self.fetcher.banner_url,
                logo_url=self.fetcher.logo_url,
            )
            self.channel_assets_thread.start()

        # 3. Download Thumbnails sequentially
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

            # Phase 2: Metadata Extraction & Purification
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

                # Phase 3: Transcripts (Scripts)
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

                    # Phase 4: Queue MP3 audio downloads
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


