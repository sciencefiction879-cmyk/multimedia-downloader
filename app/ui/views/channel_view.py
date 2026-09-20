"""
Channel & Media Downloader Pro v3.0 with Custom Data Selection, Forced Transcript Discovery,
Video Quality Selection, Dynamic Sorting (New/Old), Correct V-Numbering, and Flexible V-Ranges.
"""

import os
import re
import subprocess
import sys
import time
import threading
from queue import Queue
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
    QInputDialog,
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtCore import Qt, Signal, QThread, QTimer

from app.config import (
    APP_VERSION,
    AUDIO_FORMATS,
    AUDIO_QUALITIES,
    DEFAULT_AUDIO_QUALITY,
    DEFAULT_AUDIO_CONCURRENT_DOWNLOADS,
    DEFAULT_SCRIPT_CONCURRENT_DOWNLOADS,
    MAX_CONCURRENT_DOWNLOADS,
    VIDEO_FORMATS,
    VIDEO_QUALITIES,
    DEFAULT_VIDEO_QUALITY,
    DEFAULT_VIDEO_FORMAT,
    CHANNEL_ORDER_OPTIONS,
    CHANNEL_FETCH_RANGES,
    DEFAULT_CHANNEL_FETCH_COUNT,
    ORDER_LATEST_TO_OLDEST,
    ORDER_OLDEST_TO_LATEST,
    ORDER_POPULAR_TO_LEAST,
    ORDER_LEAST_TO_POPULAR,
)
from app.downloader.channel_fetcher import ChannelFetcher, ChannelCandidate, parse_view_count_input
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


class SummaryFetchThread(QThread):
    summary_ready = Signal(dict)

    def __init__(self, url: str):
        super().__init__()
        self.url = url.strip()

    def run(self):
        try:
            fetcher = ChannelFetcher()
            summary = fetcher.fetch_channel_summary(self.url)
            self.summary_ready.emit(summary)
        except Exception:
            pass


class MediaFetchThread(QThread):
    finished_signal = Signal(list)
    error_signal = Signal(str)

    def __init__(
        self,
        url: str,
        max_count: Optional[int],
        order: str,
        force_refresh: bool = False,
        min_views: Optional[int] = None,
        max_views: Optional[int] = None,
    ):
        super().__init__()
        self.url = url.strip()
        self.max_count = max_count
        self.order = order
        self.force_refresh = force_refresh
        self.min_views = min_views
        self.max_views = max_views
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
            candidates = fetcher.fetch_channel_videos(
                self.url,
                max_results=self.max_count,
                order=self.order,
                force_refresh=self.force_refresh,
                min_views=self.min_views,
                max_views=self.max_views,
            )
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
        concurrency: int = 3,
    ):
        super().__init__()
        self.candidates = candidates
        self.existing_transcripts = existing_transcripts or {}
        self.output_dir = output_dir
        self.concurrency = max(1, min(MAX_CONCURRENT_DOWNLOADS, concurrency))
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        total = len(self.candidates)
        if total == 0:
            self.all_finished.emit(True)
            return

        lock = threading.Lock()
        done_count = 0
        skip_count = 0
        completed_items = 0

        task_queue = Queue()
        for idx, cand in enumerate(self.candidates, start=1):
            task_queue.put((idx, cand))

        def worker_loop():
            nonlocal done_count, skip_count, completed_items
            while not self._is_cancelled:
                try:
                    idx, cand = task_queue.get_nowait()
                except Exception:
                    break

                v_label = cand.version_label or f"V{idx}"
                title = cand.title or f"Video {idx}"

                # 1. In-memory check
                if cand.video_id in self.existing_transcripts and self.existing_transcripts[cand.video_id]:
                    mem_text = self.existing_transcripts[cand.video_id]
                    is_val, _ = TranscriptFetcher.validate_script_content(mem_text)
                    if is_val:
                        with lock:
                            skip_count += 1
                            completed_items += 1
                            cur_comp = completed_items
                            cur_done = done_count
                            cur_skip = skip_count
                        self.progress_signal.emit(cur_comp, total, f"[{v_label}] (Resumed) {title}")
                        self.item_progress.emit(cur_comp, total, cur_done, cur_skip, v_label, title)
                        self.item_fetched.emit(cand.video_id, mem_text)
                        if self.output_dir:
                            disk_file = self.output_dir / f"{v_label} Script.txt"
                            if not disk_file.exists() or disk_file.stat().st_size == 0:
                                try:
                                    self.output_dir.mkdir(parents=True, exist_ok=True)
                                    formatted = TranscriptFetcher.format_script_with_metadata(mem_text, v_label)
                                    disk_file.write_text(formatted, encoding="utf-8")
                                except Exception:
                                    pass
                        task_queue.task_done()
                        continue

                # 2. Disk check (Instant resume)
                if self.output_dir:
                    disk_file = self.output_dir / f"{v_label} Script.txt"
                    if disk_file.exists() and disk_file.stat().st_size > 0:
                        try:
                            with open(disk_file, "r", encoding="utf-8") as f:
                                saved_text = f.read()
                            is_val, _ = TranscriptFetcher.validate_script_content(saved_text)
                            if is_val:
                                with lock:
                                    skip_count += 1
                                    completed_items += 1
                                    cur_comp = completed_items
                                    cur_done = done_count
                                    cur_skip = skip_count
                                self.progress_signal.emit(cur_comp, total, f"[{v_label}] (Already on disk - Skipped) {title}")
                                self.item_progress.emit(cur_comp, total, cur_done, cur_skip, v_label, title)
                                self.item_fetched.emit(cand.video_id, saved_text)
                                task_queue.task_done()
                                continue
                        except Exception:
                            pass

                self.progress_signal.emit(completed_items + 1, total, f"[{v_label}] Downloading: {title}")

                valid_text = None
                last_method = ""
                last_diag = ""
                for attempt in range(1, 6):
                    if self._is_cancelled:
                        break
                    try:
                        t_text, t_method, t_diag = TranscriptFetcher.fetch_video_transcript_with_diagnostics(cand.video_id)
                        last_method = t_method
                        last_diag = t_diag or ""
                        is_valid, reason = TranscriptFetcher.validate_script_content(t_text)
                        if is_valid:
                            valid_text = t_text
                            break
                        else:
                            last_diag = f"Attempt {attempt}/5: {reason}"
                            if attempt < 5:
                                threading.Event().wait(0.3)
                    except Exception as e:
                        last_method = "Error"
                        last_diag = f"Attempt {attempt}/5 error: {e}"
                        if attempt < 5:
                            threading.Event().wait(0.3)

                if valid_text:
                    with lock:
                        done_count += 1
                    self.item_fetched.emit(cand.video_id, valid_text)
                    self.item_diagnostics.emit(cand.video_id, last_method, "")
                    # Live Saving immediately to disk as each item is retrieved
                    if self.output_dir:
                        try:
                            self.output_dir.mkdir(parents=True, exist_ok=True)
                            disk_file = self.output_dir / f"{v_label} Script.txt"
                            formatted = TranscriptFetcher.format_script_with_metadata(valid_text, v_label)
                            with open(disk_file, "w", encoding="utf-8") as f:
                                f.write(formatted)
                        except Exception as e:
                            logger.debug(f"Live script save failed for {v_label}: {e}")
                else:
                    self.item_diagnostics.emit(cand.video_id, last_method or "Validation Failed", last_diag or "Script was empty or invalid after 5 retries")

                with lock:
                    completed_items += 1
                    cur_comp = completed_items
                    cur_done = done_count
                    cur_skip = skip_count

                self.item_progress.emit(cur_comp, total, cur_done, cur_skip, v_label, title)
                task_queue.task_done()

        threads = []
        pool_size = min(self.concurrency, total)
        for _ in range(pool_size):
            t = threading.Thread(target=worker_loop, daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        if not self._is_cancelled:
            self.all_finished.emit(True)
        else:
            self.all_finished.emit(False)


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

            v_label = cand.version_label or f"V{idx}"
            title = cand.title or f"Video {idx}"

            if cand.video_id in self.existing_metadata and self.existing_metadata[cand.video_id]:
                self.progress_signal.emit(idx, total, f"[{v_label}] (Cached) {title}")
                self.item_fetched.emit(cand.video_id, self.existing_metadata[cand.video_id])
                continue

            self.progress_signal.emit(idx, total, f"[{v_label}] Metadata: {title}")
            try:
                info = MetadataPurifier.fetch_and_purify(cand.video_id)
            except Exception:
                info = {
                    "title": cand.title,
                    "description": "",
                    "tags": [],
                    "duration": cand.duration,
                    "upload_date": cand.upload_date,
                    "view_count": 0,
                    "channel_url": cand.channel_url,
                    "channel_name": cand.uploader,
                }

            self.item_fetched.emit(cand.video_id, info)

        if not self._is_cancelled:
            self.all_finished.emit(True)


class BatchThumbnailThread(QThread):
    progress_signal = Signal(int, int, str)
    item_progress = Signal(int, int, int, int, str, str)  # cur, tot, done_count, skip_count, v_label, title
    all_finished = Signal(bool, list)

    def __init__(self, candidates: List[ChannelCandidate], output_dir: Path, max_count: Optional[int] = None):
        super().__init__()
        if max_count is not None and max_count > 0:
            self.candidates = candidates[:max_count]
        else:
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
                saved_path = None
                for attempt in range(1, 6):
                    if self._is_cancelled:
                        break
                    try:
                        res = ChannelAssetsFetcher.download_thumbnail_for_video(
                            video_id=cand.video_id,
                            version_label=v_label,
                            output_dir=output_dir,
                            fallback_thumb_url=cand.thumbnail,
                        )
                        if res and Path(res).exists() and Path(res).stat().st_size > 1024:
                            saved_path = res
                            break
                    except Exception:
                        if attempt < 5:
                            threading.Event().wait(0.3)

                if saved_path and Path(saved_path).exists() and Path(saved_path).stat().st_size > 1024:
                    saved.append(saved_path)
                    done_count += 1

            self.progress_signal.emit(idx, total, f"[{v_label}] {title}")
            self.item_progress.emit(idx, total, done_count, skip_count, v_label, title)

        self.all_finished.emit(not self._is_cancelled, saved)


class ChannelAssetsThread(QThread):
    finished_signal = Signal(dict)
    error_signal = Signal(str)
    all_finished = Signal(bool, list)

    def __init__(self, channel_url: str, output_dir: Path, banner_url: str = "", logo_url: str = ""):
        super().__init__()
        self.channel_url = channel_url
        self.output_dir = output_dir
        self.banner_url = banner_url
        self.logo_url = logo_url

    def run(self):
        res = None
        for attempt in range(1, 6):
            try:
                res = ChannelAssetsFetcher.download_channel_assets(
                    channel_url=self.channel_url,
                    output_dir=self.output_dir,
                    banner_url=self.banner_url,
                    logo_url=self.logo_url,
                )
                if res:
                    break
            except Exception as e:
                if attempt >= 5:
                    self.error_signal.emit(str(e))
                    self.all_finished.emit(False, [])
                    return
                threading.Event().wait(0.3)

        saved_list = list(res.values()) if isinstance(res, dict) else []
        self.finished_signal.emit(res if isinstance(res, dict) else {})
        self.all_finished.emit(True, saved_list)


class ChannelView(QWidget):
    switch_to_downloads_requested = Signal()

    def __init__(self, queue_manager: QueueManager, settings: Settings, parent=None):
        super().__init__(parent)
        self.queue_manager = queue_manager
        self.settings = settings
        self.fetcher = ChannelFetcher()

        self.all_fetched_candidates: List[ChannelCandidate] = []
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
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header
        lbl_title = QLabel(f"Channel & Media Downloader Pro v{APP_VERSION}")
        lbl_title.setObjectName("viewTitle")
        lbl_sub = QLabel(
            "Unified YouTube data extractor with Chronological & Popularity V-Numbering (Newest, Oldest, Most Popular, Least Popular), "
            "View Count Filtering (Min/Max Views), Selective V-Range Skip & Download, Zero Competitor Word-Count Scripts, 5-Retry Auto Recovery, and Concurrent Scripts & Audio."
        )
        lbl_sub.setWordWrap(True)
        lbl_sub.setObjectName("viewSubtitle")
        layout.addWidget(lbl_title)
        layout.addWidget(lbl_sub)

        # -------------------------------------------------------------
        # 1. Fetch Options Card
        # -------------------------------------------------------------
        box_fetch = QGroupBox("1. Channel / Playlist / Video Link")
        fetch_vbox = QVBoxLayout(box_fetch)
        fetch_vbox.setSpacing(10)

        # Row 0: URL input + Paste + Channel Videos Count
        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        lbl_url = QLabel("YouTube URL:")
        lbl_url.setFixedWidth(85)
        self.txt_url = QLineEdit()
        self.txt_url.setPlaceholderText("Paste Channel link (@Channel/videos), Playlist, or Video URL here...")
        
        btn_paste = QPushButton("📋 Paste")
        btn_paste.clicked.connect(lambda: self.txt_url.setText(QApplication.clipboard().text().strip()))

        self.lbl_total_available_videos = QLabel("Channel Videos: —")
        self.lbl_total_available_videos.setStyleSheet("color: #007aff; font-weight: 700; font-size: 12px; margin-left: 4px;")

        url_row.addWidget(lbl_url)
        url_row.addWidget(self.txt_url, 1)
        url_row.addWidget(btn_paste)
        url_row.addWidget(self.lbl_total_available_videos)
        fetch_vbox.addLayout(url_row)

        # Row 1: Order + Count + Action Buttons
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)

        lbl_ord = QLabel("Order:")
        lbl_ord.setFixedWidth(85)
        self.combo_order = QComboBox()
        self.combo_order.addItems(CHANNEL_ORDER_OPTIONS)
        self.combo_order.setCurrentText(ORDER_LATEST_TO_OLDEST)
        self.combo_order.currentTextChanged.connect(self._on_order_changed)

        lbl_cnt = QLabel("Videos to Fetch:")
        self.combo_count = QComboBox()
        self.combo_count.setEditable(True)
        self.combo_count.setToolTip("Enter count (e.g. 50) or exact custom range (e.g. 1-10, 1-25, 20-30, 47-52, All)")
        for count in CHANNEL_FETCH_RANGES:
            self.combo_count.addItem(str(count), count)
        self.combo_count.setCurrentText(str(DEFAULT_CHANNEL_FETCH_COUNT))

        self.btn_fetch = QPushButton("🔍 Fetch Videos")
        self.btn_fetch.setObjectName("primaryBtn")
        self.btn_fetch.clicked.connect(self._fetch_videos_clicked)

        self.btn_force_fetch = QPushButton("⚡ Force Fetch")
        self.btn_force_fetch.setToolTip("Bypasses any cache to extract completely fresh videos and data")
        self.btn_force_fetch.setStyleSheet("background-color: #5856d6; color: #ffffff; font-weight: 700;")
        self.btn_force_fetch.clicked.connect(self._force_fetch_videos_clicked)

        self.btn_stop_fetch = QPushButton("⏹ Stop")
        self.btn_stop_fetch.setEnabled(False)
        self.btn_stop_fetch.clicked.connect(self._stop_fetch_clicked)

        self.btn_clear_all = QPushButton("✕ Clear")
        self.btn_clear_all.clicked.connect(self._clear_all_clicked)

        ctrl_row.addWidget(lbl_ord)
        ctrl_row.addWidget(self.combo_order)
        ctrl_row.addWidget(lbl_cnt)
        ctrl_row.addWidget(self.combo_count)
        ctrl_row.addStretch()
        ctrl_row.addWidget(self.btn_fetch)
        ctrl_row.addWidget(self.btn_force_fetch)
        ctrl_row.addWidget(self.btn_stop_fetch)
        ctrl_row.addWidget(self.btn_clear_all)
        fetch_vbox.addLayout(ctrl_row)

        # Row 2: View Count Filter Bar
        vf_frame = QFrame()
        vf_frame.setStyleSheet("background-color: rgba(0, 122, 255, 0.04); border: 1px solid rgba(0, 122, 255, 0.2); border-radius: 6px; padding: 4px;")
        vf_layout = QHBoxLayout(vf_frame)
        vf_layout.setContentsMargins(8, 4, 8, 4)
        vf_layout.setSpacing(6)

        lbl_vf = QLabel("👁 <b>View Filter:</b>")
        lbl_vf.setStyleSheet("color: #007aff;")

        self.txt_min_views = QLineEdit()
        self.txt_min_views.setPlaceholderText("Min (e.g. 100K)")
        self.txt_min_views.setToolTip("Filter videos with at least this many views (e.g. 100K, 500,000, 1M). Applied before V-numbering.")
        self.txt_min_views.returnPressed.connect(self._apply_view_filter_and_sort)
        self.txt_min_views.setMaximumWidth(120)

        lbl_to = QLabel("to")
        lbl_to.setStyleSheet("color: #8e8e93; font-weight: 600;")

        self.txt_max_views = QLineEdit()
        self.txt_max_views.setPlaceholderText("Max (e.g. 2M)")
        self.txt_max_views.setToolTip("Filter videos with at most this many views (e.g. 1M, 2,000,000). Applied before V-numbering.")
        self.txt_max_views.returnPressed.connect(self._apply_view_filter_and_sort)
        self.txt_max_views.setMaximumWidth(120)

        btn_v_100k = QPushButton("100K+")
        btn_v_100k.setToolTip("Quick preset: 100,000+ views")
        btn_v_100k.clicked.connect(lambda: self._set_view_filter_preset("100K", ""))

        btn_v_500k = QPushButton("500K-2M")
        btn_v_500k.setToolTip("Quick preset: 500,000 to 2,000,000 views")
        btn_v_500k.clicked.connect(lambda: self._set_view_filter_preset("500K", "2M"))

        btn_v_less100k = QPushButton("< 100K")
        btn_v_less100k.setToolTip("Quick preset: less than 100,000 views")
        btn_v_less100k.clicked.connect(lambda: self._set_view_filter_preset("", "100K"))

        btn_v_apply = QPushButton("⚡ Apply Filter")
        btn_v_apply.setStyleSheet("background-color: #007aff; color: #ffffff; font-weight: 700; padding: 4px 10px;")
        btn_v_apply.clicked.connect(self._apply_view_filter_and_sort)

        btn_v_clear = QPushButton("✕ Clear")
        btn_v_clear.clicked.connect(lambda: self._set_view_filter_preset("", ""))

        self.lbl_unavailable_count = QLabel("Unavailable/Private: 0")
        self.lbl_unavailable_count.setStyleSheet("color: #8e8e93; font-size: 11px; margin-left: 8px;")

        vf_layout.addWidget(lbl_vf)
        vf_layout.addWidget(self.txt_min_views)
        vf_layout.addWidget(lbl_to)
        vf_layout.addWidget(self.txt_max_views)
        vf_layout.addWidget(btn_v_100k)
        vf_layout.addWidget(btn_v_500k)
        vf_layout.addWidget(btn_v_less100k)
        vf_layout.addWidget(btn_v_apply)
        vf_layout.addWidget(btn_v_clear)
        vf_layout.addStretch()
        vf_layout.addWidget(self.lbl_unavailable_count)

        fetch_vbox.addWidget(vf_frame)

        # Auto-fetch debounced timer
        self.auto_fetch_timer = QTimer(self)
        self.auto_fetch_timer.setSingleShot(True)
        self.auto_fetch_timer.setInterval(800)
        self.auto_fetch_timer.timeout.connect(self._on_auto_fetch_timeout)
        self.txt_url.textChanged.connect(self._on_url_text_changed)

        layout.addWidget(box_fetch)

        # -------------------------------------------------------------
        # 2. Custom Data Selection Panel (Requirement 1, 2, 3, 4, 8)
        # -------------------------------------------------------------
        box_selection = QGroupBox("2. Custom Data Selection & Selective Download Pipeline")
        box_selection.setStyleSheet("QGroupBox { border: 1.5px solid #007aff; }")
        sel_layout = QVBoxLayout(box_selection)
        sel_layout.setSpacing(10)

        lbl_sel_info = QLabel("Sequential Phased Pipeline (Titles → Thumbnails → Assets → Parallel Scripts & Audio), saved live in real-time:")
        lbl_sel_info.setStyleSheet("color: #007aff; font-weight: 600;")
        sel_layout.addWidget(lbl_sel_info)

        # Preset Management Bar
        preset_bar = QHBoxLayout()
        preset_bar.setSpacing(8)
        lbl_presets_head = QLabel("⚙️ <b>Selection Preset:</b>")
        lbl_presets_head.setStyleSheet("color: #007aff;")
        self.combo_presets = QComboBox()
        self._refresh_presets_combo()
        self.combo_presets.currentIndexChanged.connect(self._on_preset_dropdown_changed)
        
        self.btn_save_preset = QPushButton("💾 Save Preset...")
        self.btn_save_preset.setToolTip("Save current custom checkbox selection as a new named preset")
        self.btn_save_preset.clicked.connect(self._on_save_preset_clicked)
        
        preset_bar.addWidget(lbl_presets_head)
        preset_bar.addWidget(self.combo_presets, 1)
        preset_bar.addWidget(self.btn_save_preset)
        sel_layout.addLayout(preset_bar)

        # Skip / Exclude Range Bar (Requirement 8)
        skip_frame = QFrame()
        skip_frame.setStyleSheet("background-color: rgba(255, 149, 0, 0.08); border-radius: 6px; padding: 4px;")
        skip_layout = QHBoxLayout(skip_frame)
        skip_layout.setContentsMargins(6, 4, 6, 4)
        lbl_skip = QLabel("🚫 Skip / Already Downloaded V-Ranges:")
        lbl_skip.setStyleSheet("color: #ff9500; font-weight: 700; font-size: 11px;")
        self.txt_skip_ranges = QLineEdit()
        self.txt_skip_ranges.setPlaceholderText("e.g. V1-V10, V25-V35, V1-V10 + V25-V35, V3, V8 (skipped across all downloads)")
        self.txt_skip_ranges.setToolTip("V-numbers to skip completely from this download session")
        skip_layout.addWidget(lbl_skip)
        skip_layout.addWidget(self.txt_skip_ranges)
        sel_layout.addWidget(skip_frame)

        # 6 Clean Selection Checkboxes organized in grid with custom controls
        grid_sel = QGridLayout()
        grid_sel.setHorizontalSpacing(14)
        grid_sel.setVerticalSpacing(8)

        # 1. Video Titles + Linked option
        w_title = QWidget()
        l_title = QVBoxLayout(w_title)
        l_title.setContentsMargins(0, 0, 0, 0)
        l_title.setSpacing(3)
        self.chk_titles = QCheckBox("1. Video Titles (Titles.txt)")
        self.chk_titles.setChecked(True)
        self.chk_titles.setToolTip("Creates single TXT file named Titles.txt containing V1 — Title, V2 — Title...")
        self.chk_titles_linked = QCheckBox("🔗 Only active script/audio videos in Titles.txt")
        self.chk_titles_linked.setChecked(False)
        self.chk_titles_linked.setStyleSheet("color: #8e8e93; font-size: 11px; margin-left: 18px;")
        self.chk_titles_linked.setToolTip("When checked, Titles.txt only includes videos being actively downloaded in Scripts or Audio")
        l_title.addWidget(self.chk_titles)
        l_title.addWidget(self.chk_titles_linked)
        grid_sel.addWidget(w_title, 0, 0)

        # 2. Thumbnails with custom total count and custom V-range
        w_thumb = QWidget()
        l_thumb = QVBoxLayout(w_thumb)
        l_thumb.setContentsMargins(0, 0, 0, 0)
        l_thumb.setSpacing(3)
        l_thumb_top = QHBoxLayout()
        l_thumb_top.setSpacing(6)
        self.chk_thumbnails = QCheckBox("2. Thumbnails (Thumbnails/)")
        self.chk_thumbnails.setChecked(True)
        self.chk_thumbnails.setToolTip("Highest resolution thumbnails named V1 Thumbnail.jpg, V2 Thumbnail.jpg...")
        self.lbl_thumb_count = QLabel("Total:")
        self.lbl_thumb_count.setStyleSheet("color: #007aff; font-weight: 600;")
        self.spin_thumb_count = QSpinBox()
        self.spin_thumb_count.setRange(1, 9999)
        self.spin_thumb_count.setValue(50)
        self.spin_thumb_count.setToolTip("Custom select total number of video thumbnails to download (e.g. 10, 25, 50, all)")
        self.spin_thumb_count.valueChanged.connect(self._on_thumb_count_changed)
        l_thumb_top.addWidget(self.chk_thumbnails)
        l_thumb_top.addStretch()
        l_thumb_top.addWidget(self.lbl_thumb_count)
        l_thumb_top.addWidget(self.spin_thumb_count)
        l_thumb.addLayout(l_thumb_top)

        self.txt_thumb_v_range = QLineEdit()
        self.txt_thumb_v_range.setPlaceholderText("Thumbnail Range (e.g. V1-V20, empty = all)")
        self.txt_thumb_v_range.setStyleSheet("font-size: 11px;")
        l_thumb.addWidget(self.txt_thumb_v_range)
        grid_sel.addWidget(w_thumb, 0, 1)

        # 3. Channel Assets
        w_asset = QWidget()
        l_asset = QVBoxLayout(w_asset)
        l_asset.setContentsMargins(0, 0, 0, 0)
        l_asset.setSpacing(3)
        self.chk_channel_assets = QCheckBox("3. Channel Assets (Banner & Logo)")
        self.chk_channel_assets.setChecked(True)
        self.chk_channel_assets.setToolTip("Competitor channel banner and avatar/logo images saved into Channel Assets/")
        lbl_asset_sub = QLabel("Avatar, banner, & metadata")
        lbl_asset_sub.setStyleSheet("color: #8e8e93; font-size: 11px; margin-left: 18px;")
        l_asset.addWidget(self.chk_channel_assets)
        l_asset.addWidget(lbl_asset_sub)
        grid_sel.addWidget(w_asset, 0, 2)

        # 4. Scripts with custom parallel concurrency and custom V-range
        w_script = QWidget()
        l_script = QVBoxLayout(w_script)
        l_script.setContentsMargins(0, 0, 0, 0)
        l_script.setSpacing(3)

        l_script_top = QHBoxLayout()
        l_script_top.setSpacing(6)
        self.chk_scripts = QCheckBox("4. Scripts (Scripts/)")
        self.chk_scripts.setChecked(True)
        self.chk_scripts.setToolTip("Active search across captions with custom parallel concurrency (1-500)")
        self.lbl_script_conc = QLabel("⚡ Parallel:")
        self.lbl_script_conc.setStyleSheet("color: #007aff; font-weight: 600;")
        self.spin_script_concurrency = QSpinBox()
        self.spin_script_concurrency.setRange(1, 500)
        initial_script_conc = getattr(self.settings, "script_concurrent_downloads", 3)
        self.spin_script_concurrency.setValue(initial_script_conc)
        self.spin_script_concurrency.setToolTip("Custom select how many scripts download in parallel (up to 500)")
        self.spin_script_concurrency.valueChanged.connect(self._on_script_concurrency_changed)
        l_script_top.addWidget(self.chk_scripts)
        l_script_top.addStretch()
        l_script_top.addWidget(self.lbl_script_conc)
        l_script_top.addWidget(self.spin_script_concurrency)
        l_script.addLayout(l_script_top)

        self.txt_script_v_range = QLineEdit()
        self.txt_script_v_range.setPlaceholderText("Script Range (e.g. V11-V30, empty = all)")
        self.txt_script_v_range.setStyleSheet("font-size: 11px;")
        l_script.addWidget(self.txt_script_v_range)
        grid_sel.addWidget(w_script, 1, 0)

        # 5. Audio with custom parallel concurrency and custom V-range
        w_audio = QWidget()
        l_audio = QVBoxLayout(w_audio)
        l_audio.setContentsMargins(0, 0, 0, 0)
        l_audio.setSpacing(3)

        l_audio_top = QHBoxLayout()
        l_audio_top.setSpacing(6)
        self.chk_mp3s = QCheckBox("5. Audio (MP3)")
        self.chk_mp3s.setChecked(True)
        self.chk_mp3s.setToolTip("Parallel MP3 audio downloads named V1.mp3, V2.mp3... (concurrency 1-500)")
        self.lbl_audio_conc = QLabel("⚡ Parallel:")
        self.lbl_audio_conc.setStyleSheet("color: #007aff; font-weight: 600;")
        self.spin_audio_concurrency = QSpinBox()
        self.spin_audio_concurrency.setRange(1, 500)
        initial_audio_conc = getattr(self.settings, "audio_concurrent_downloads", 3)
        self.spin_audio_concurrency.setValue(initial_audio_conc)
        self.spin_audio_concurrency.setToolTip("Custom select how many audio files download in parallel (up to 500)")
        self.spin_audio_concurrency.valueChanged.connect(self._on_audio_concurrency_changed)
        l_audio_top.addWidget(self.chk_mp3s)
        l_audio_top.addStretch()
        l_audio_top.addWidget(self.lbl_audio_conc)
        l_audio_top.addWidget(self.spin_audio_concurrency)
        l_audio.addLayout(l_audio_top)

        self.txt_audio_v_range = QLineEdit()
        self.txt_audio_v_range.setPlaceholderText("Audio Range (e.g. V1-V19 + V31+, empty = all)")
        self.txt_audio_v_range.setStyleSheet("font-size: 11px;")
        l_audio.addWidget(self.txt_audio_v_range)
        grid_sel.addWidget(w_audio, 1, 1)

        # 6. Videos with custom V-range
        w_video = QWidget()
        l_video = QVBoxLayout(w_video)
        l_video.setContentsMargins(0, 0, 0, 0)
        l_video.setSpacing(3)
        self.chk_videos = QCheckBox("6. Videos (MP4)")
        self.chk_videos.setChecked(False)
        self.chk_videos.setToolTip("Downloads actual video files (MP4) named V1.mp4, V2.mp4... at selected quality")
        l_video.addWidget(self.chk_videos)

        self.txt_video_v_range = QLineEdit()
        self.txt_video_v_range.setPlaceholderText("Video Range (e.g. V1-V10, empty = all)")
        self.txt_video_v_range.setStyleSheet("font-size: 11px;")
        l_video.addWidget(self.txt_video_v_range)
        grid_sel.addWidget(w_video, 1, 2)

        sel_layout.addLayout(grid_sel)

        # Preset Buttons and Operational Modes Row
        preset_modes_row = QHBoxLayout()
        preset_modes_row.setSpacing(8)

        lbl_presets = QLabel("Presets:")
        lbl_presets.setStyleSheet("color: #9d9da8; font-weight: 600; font-size: 11px;")
        preset_modes_row.addWidget(lbl_presets)

        btn_preset_all = QPushButton("All")
        btn_preset_all.clicked.connect(self._preset_select_all_data)
        preset_modes_row.addWidget(btn_preset_all)

        btn_preset_none = QPushButton("None")
        btn_preset_none.clicked.connect(self._preset_deselect_all_data)
        preset_modes_row.addWidget(btn_preset_none)

        btn_preset_scripts = QPushButton("Scripts + Titles")
        btn_preset_scripts.clicked.connect(self._preset_scripts_titles_data)
        preset_modes_row.addWidget(btn_preset_scripts)

        btn_preset_media = QPushButton("Media Only")
        btn_preset_media.clicked.connect(self._preset_media_only_data)
        preset_modes_row.addWidget(btn_preset_media)

        btn_preset_core = QPushButton("Core Package (Default)")
        btn_preset_core.clicked.connect(self._preset_core_package_data)
        preset_modes_row.addWidget(btn_preset_core)

        preset_modes_row.addStretch()

        self.chk_mode_missing_only = QCheckBox("⚡ Download Missing Only")
        self.chk_mode_missing_only.setChecked(True)
        self.chk_mode_missing_only.setToolTip("Strictly downloads missing files and skips all valid completed files on disk")

        self.chk_mode_force_overwrite = QCheckBox("🔄 Force Redownload All")
        self.chk_mode_force_overwrite.setChecked(False)
        self.chk_mode_force_overwrite.setToolTip("Forces redownloading and overwriting of all selected files even if present")

        self.chk_mode_missing_only.toggled.connect(lambda checked: self.chk_mode_force_overwrite.setChecked(False) if checked else None)
        self.chk_mode_force_overwrite.toggled.connect(lambda checked: self.chk_mode_missing_only.setChecked(False) if checked else None)

        preset_modes_row.addWidget(self.chk_mode_missing_only)
        preset_modes_row.addWidget(self.chk_mode_force_overwrite)
        sel_layout.addLayout(preset_modes_row)

        # Primary Action Command Center Row
        action_cmd_row = QHBoxLayout()
        action_cmd_row.setSpacing(8)

        self.btn_regenerate_titles = QPushButton("📝 Regenerate Titles.txt")
        self.btn_regenerate_titles.setToolTip("Re-exports Titles.txt for selected candidates directly into folder without downloading media")
        self.btn_regenerate_titles.clicked.connect(self._regenerate_titles_clicked)
        action_cmd_row.addWidget(self.btn_regenerate_titles)

        self.btn_sync_channel = QPushButton("🔄 SYNC CHANNEL")
        self.btn_sync_channel.setStyleSheet(
            "background-color: #5856d6; color: #ffffff; font-weight: 800; font-size: 12px; padding: 8px 14px; border-radius: 6px;"
        )
        self.btn_sync_channel.setToolTip("Compares local folder with channel, selects only un-downloaded items, and downloads them")
        self.btn_sync_channel.clicked.connect(self._sync_channel_clicked)
        action_cmd_row.addWidget(self.btn_sync_channel)

        self.btn_resume_download = QPushButton("⏯ RESUME")
        self.btn_resume_download.setStyleSheet(
            "background-color: #ff9500; color: #ffffff; font-weight: 800; font-size: 12px; padding: 8px 14px; border-radius: 6px;"
        )
        self.btn_resume_download.setToolTip("Resumes download; automatically checks disk and skips already completed files instantly")
        self.btn_resume_download.clicked.connect(self._resume_download_clicked)
        action_cmd_row.addWidget(self.btn_resume_download)

        self.btn_smart_download = QPushButton("🌟 SMART DOWNLOAD")
        self.btn_smart_download.setStyleSheet(
            "background-color: #30d158; color: #ffffff; font-weight: 800; font-size: 12px; padding: 8px 14px; border-radius: 6px;"
        )
        self.btn_smart_download.setToolTip("Master 1-click execution: sort, filter by views, scan disk, download missing assets in parallel, and show full statistics")
        self.btn_smart_download.clicked.connect(self._smart_download_clicked)
        action_cmd_row.addWidget(self.btn_smart_download)

        self.btn_download_selected = QPushButton("🚀 DOWNLOAD SELECTED")
        self.btn_download_selected.setStyleSheet(
            "background-color: #34c759; color: #ffffff; font-weight: 800; font-size: 13px; padding: 8px 18px; border-radius: 6px;"
        )
        self.btn_download_selected.setToolTip("Execute download pipeline for all checked items on selected videos")
        self.btn_download_selected.clicked.connect(self._download_selected_items_clicked)
        action_cmd_row.addWidget(self.btn_download_selected)
        sel_layout.addLayout(action_cmd_row)

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

        # Multi-category live status indicators in 5-phase sequential order
        self.prog_cats_frame = QFrame()
        self.prog_cats_frame.setFrameShape(QFrame.StyledPanel)
        self.prog_cats_frame.setStyleSheet("background-color: rgba(120, 120, 128, 0.08); border-radius: 6px; padding: 6px;")
        grid_p = QGridLayout(self.prog_cats_frame)
        grid_p.setContentsMargins(6, 6, 6, 6)
        grid_p.setSpacing(6)

        self.lbl_prog_titles = QLabel("📝 <b>1. Titles:</b> Waiting...")
        self.lbl_prog_thumbs = QLabel("🖼️ <b>2. Thumbnails:</b> Waiting...")
        self.lbl_prog_assets = QLabel("🎨 <b>3. Channel Assets:</b> Waiting...")
        self.lbl_prog_scripts = QLabel("📜 <b>4. Scripts:</b> Waiting...")
        self.lbl_prog_media = QLabel("🎵 <b>5. Audio/Media:</b> Waiting...")

        grid_p.addWidget(self.lbl_prog_titles, 0, 0)
        grid_p.addWidget(self.lbl_prog_thumbs, 0, 1)
        grid_p.addWidget(self.lbl_prog_assets, 0, 2)
        grid_p.addWidget(self.lbl_prog_scripts, 1, 0)
        grid_p.addWidget(self.lbl_prog_media, 1, 1, 1, 2)
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

        # Action Bar: Invert Selection, Select Missing, Select Failed, Select Incomplete, Select Skipped
        sel_actions_bar = QHBoxLayout()
        sel_actions_bar.setSpacing(6)
        lbl_act = QLabel("<b>Actions:</b>")
        lbl_act.setStyleSheet("color: #8e8e93; font-size: 11px;")
        sel_actions_bar.addWidget(lbl_act)

        self.btn_invert_sel = QPushButton("Invert")
        self.btn_invert_sel.setToolTip("Invert checkbox selection states across all matched videos")
        self.btn_invert_sel.clicked.connect(self._on_invert_selection)
        sel_actions_bar.addWidget(self.btn_invert_sel)

        self.btn_select_missing = QPushButton("Select Missing")
        self.btn_select_missing.setToolTip("Scans save folder and selects only videos with missing files on disk")
        self.btn_select_missing.clicked.connect(self._on_select_missing)
        sel_actions_bar.addWidget(self.btn_select_missing)

        self.btn_select_failed = QPushButton("Select Failed")
        self.btn_select_failed.setToolTip("Selects only videos that previously failed or produced errors")
        self.btn_select_failed.clicked.connect(self._on_select_failed)
        sel_actions_bar.addWidget(self.btn_select_failed)

        self.btn_select_incomplete = QPushButton("Select Incomplete")
        self.btn_select_incomplete.setToolTip("Selects partial/corrupt files (<10KB audio / <50KB video / empty scripts)")
        self.btn_select_incomplete.clicked.connect(self._on_select_incomplete)
        sel_actions_bar.addWidget(self.btn_select_incomplete)

        self.btn_select_skipped = QPushButton("Select Skipped")
        self.btn_select_skipped.setToolTip("Selects candidates matching skip V-ranges or marked as skipped")
        self.btn_select_skipped.clicked.connect(self._on_select_skipped)
        sel_actions_bar.addWidget(self.btn_select_skipped)

        sel_actions_bar.addStretch()
        tbl_layout.addLayout(sel_actions_bar)

        # Interactive Table Filter Bar (Keyword, Min Views, Duration, Date, Type)
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(6)

        self.txt_filter_keyword = QLineEdit()
        self.txt_filter_keyword.setPlaceholderText("🔍 Search by title keyword...")
        self.txt_filter_keyword.setToolTip("Instant real-time search: filters table rows matching keyword")
        self.txt_filter_keyword.textChanged.connect(self._apply_table_filters)
        filter_bar.addWidget(self.txt_filter_keyword, 2)

        self.spin_filter_min_views = QSpinBox()
        self.spin_filter_min_views.setRange(0, 1000000000)
        self.spin_filter_min_views.setSingleStep(50000)
        self.spin_filter_min_views.setPrefix("Min Views: ")
        self.spin_filter_min_views.setSpecialValueText("Min Views: Any")
        self.spin_filter_min_views.setToolTip("Filters table rows showing only videos with at least this many views")
        self.spin_filter_min_views.valueChanged.connect(self._apply_table_filters)
        self.spin_filter_min_views.setMaximumWidth(120)
        filter_bar.addWidget(self.spin_filter_min_views)

        self.spin_filter_min_dur = QSpinBox()
        self.spin_filter_min_dur.setRange(0, 600)
        self.spin_filter_min_dur.setPrefix("Min Dur: ")
        self.spin_filter_min_dur.setSuffix("m")
        self.spin_filter_min_dur.setSpecialValueText("Min Dur: 0m")
        self.spin_filter_min_dur.valueChanged.connect(self._apply_table_filters)
        self.spin_filter_min_dur.setMaximumWidth(100)
        filter_bar.addWidget(self.spin_filter_min_dur)

        self.spin_filter_max_dur = QSpinBox()
        self.spin_filter_max_dur.setRange(0, 600)
        self.spin_filter_max_dur.setPrefix("Max Dur: ")
        self.spin_filter_max_dur.setSuffix("m")
        self.spin_filter_max_dur.setSpecialValueText("Max Dur: Any")
        self.spin_filter_max_dur.valueChanged.connect(self._apply_table_filters)
        self.spin_filter_max_dur.setMaximumWidth(100)
        filter_bar.addWidget(self.spin_filter_max_dur)

        self.combo_filter_date = QComboBox()
        self.combo_filter_date.addItems(["All Dates", "Last 30 Days", "Last 3 Months", "Last Year"])
        self.combo_filter_date.currentTextChanged.connect(self._apply_table_filters)
        self.combo_filter_date.setMaximumWidth(105)
        filter_bar.addWidget(self.combo_filter_date)

        self.combo_filter_type = QComboBox()
        self.combo_filter_type.addItems(["All Types", "Videos Only", "Shorts Only"])
        self.combo_filter_type.currentTextChanged.connect(self._apply_table_filters)
        self.combo_filter_type.setMaximumWidth(100)
        filter_bar.addWidget(self.combo_filter_type)

        btn_reset_filters = QPushButton("✕ Reset")
        btn_reset_filters.setToolTip("Reset all table search and filter criteria")
        btn_reset_filters.clicked.connect(self._reset_table_filters)
        filter_bar.addWidget(btn_reset_filters)

        tbl_layout.addLayout(filter_bar)

        self.table = QTableWidget(0, 10)
        headers = ["Sel", "Ver", "Title", "Views", "Duration", "Date", "Copy Title", "Script TXT", "Status", "Action"]
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 45)
        self.table.setColumnWidth(1, 55)
        self.table.setColumnWidth(3, 85)
        self.table.setColumnWidth(4, 75)
        self.table.setColumnWidth(5, 90)
        self.table.setColumnWidth(6, 85)
        self.table.setColumnWidth(7, 95)
        self.table.setColumnWidth(8, 95)
        self.table.setColumnWidth(9, 135)
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

        self._update_thumb_checkbox_label()
        self._update_script_checkbox_label()
        self._update_audio_checkbox_label()

        scroll.setWidget(content)
        root_layout.addWidget(scroll)

    # ==================== CONTROLS & RANGE LOGIC ====================

    def _on_thumb_count_changed(self, val: int):
        self._update_thumb_checkbox_label()

    def _update_thumb_checkbox_label(self):
        if hasattr(self, "chk_thumbnails"):
            self.chk_thumbnails.setText("2. Thumbnails (Thumbnails/)")

    def _on_script_concurrency_changed(self, val: int):
        self.settings.script_concurrent_downloads = val
        self.settings.save()
        self._update_script_checkbox_label()

    def _update_script_checkbox_label(self):
        if hasattr(self, "chk_scripts"):
            self.chk_scripts.setText("4. Scripts (Scripts/)")

    def _on_audio_concurrency_changed(self, val: int):
        self.settings.audio_concurrent_downloads = val
        self.settings.concurrent_downloads = val
        self.queue_manager.settings.concurrent_downloads = val
        self.settings.save()
        self._update_audio_checkbox_label()

    def _update_audio_checkbox_label(self):
        if hasattr(self, "chk_mp3s"):
            self.chk_mp3s.setText("5. Audio (MP3)")

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
        """Dynamic order switch: re-sorts and re-indexes V1..Vn dynamically."""
        order = self.combo_order.currentText()
        if self.all_fetched_candidates:
            self._apply_view_filter_and_sort(re_filter_from_all=True)
        elif self.candidates:
            self._apply_view_filter_and_sort(re_filter_from_all=False)

    def _apply_view_filter_and_sort(self, re_filter_from_all: bool = True):
        """
        Filters candidates by minimum and/or maximum view counts,
        sorts them according to order, and reassigns V1, V2, V3... labels sequentially.
        """
        min_v = parse_view_count_input(self.txt_min_views.text()) if hasattr(self, "txt_min_views") else None
        max_v = parse_view_count_input(self.txt_max_views.text()) if hasattr(self, "txt_max_views") else None
        order = self.combo_order.currentText()

        source_list = self.all_fetched_candidates if (re_filter_from_all and self.all_fetched_candidates) else self.candidates
        if not source_list:
            return

        filtered = ChannelFetcher.filter_and_sort_candidates(
            list(source_list), order=order, min_views=min_v, max_views=max_v
        )
        self.candidates = filtered
        self._populate_table()
        self._update_range_status()

        filter_desc = []
        if min_v is not None:
            filter_desc.append(f"Min: {min_v:,}")
        if max_v is not None:
            filter_desc.append(f"Max: {max_v:,}")
        desc_str = f" ({', '.join(filter_desc)})" if filter_desc else ""
        tot_all = len(self.all_fetched_candidates) if self.all_fetched_candidates else len(self.candidates)
        self.lbl_table_status.setText(
            f"Showing {len(self.candidates)} of {tot_all} videos matching view count criteria{desc_str}. "
            f"V1 is the #1 video in {order}."
        )

    def _set_view_filter_preset(self, min_str: str, max_str: str):
        if hasattr(self, "txt_min_views"):
            self.txt_min_views.setText(min_str)
        if hasattr(self, "txt_max_views"):
            self.txt_max_views.setText(max_str)
        self._apply_view_filter_and_sort(re_filter_from_all=True)

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

    # ==================== PRESET MANAGEMENT ====================

    def _refresh_presets_combo(self):
        if not hasattr(self, "combo_presets"):
            return
        self.combo_presets.blockSignals(True)
        self.combo_presets.clear()
        self.combo_presets.addItem("Custom Selection...")
        presets = self.settings.custom_presets or {}
        for name in presets.keys():
            self.combo_presets.addItem(name)
        self.combo_presets.blockSignals(False)

    def _on_preset_dropdown_changed(self, idx: int):
        if idx <= 0:
            return
        name = self.combo_presets.currentText()
        presets = self.settings.custom_presets or {}
        cfg = presets.get(name)
        if not cfg:
            return
        self.chk_titles.setChecked(cfg.get("want_titles", True))
        self.chk_thumbnails.setChecked(cfg.get("want_thumbnails", True))
        self.chk_channel_assets.setChecked(cfg.get("want_channel_assets", True))
        self.chk_scripts.setChecked(cfg.get("want_scripts", True))
        self.chk_mp3s.setChecked(cfg.get("want_mp3s", True))
        self.chk_videos.setChecked(cfg.get("want_videos", False))

    def _on_save_preset_clicked(self):
        name, ok = QInputDialog.getText(self, "Save Custom Preset", "Enter a name for this preset:")
        if ok and name.strip():
            preset_name = name.strip()
            if self.settings.custom_presets is None:
                self.settings.custom_presets = {}
            self.settings.custom_presets[preset_name] = {
                "want_titles": self.chk_titles.isChecked(),
                "want_thumbnails": self.chk_thumbnails.isChecked(),
                "want_channel_assets": self.chk_channel_assets.isChecked(),
                "want_scripts": self.chk_scripts.isChecked(),
                "want_mp3s": self.chk_mp3s.isChecked(),
                "want_videos": self.chk_videos.isChecked(),
            }
            self.settings.save()
            self._refresh_presets_combo()
            self.combo_presets.setCurrentText(preset_name)
            QMessageBox.information(self, "Preset Saved", f"Preset '{preset_name}' saved successfully!")

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
        if hasattr(self, "btn_force_fetch"):
            self.btn_force_fetch.setEnabled(False)
        self.btn_stop_fetch.setEnabled(True)
        count, auto_select_set = self._get_fetch_params()
        self._pending_auto_select_set = auto_select_set
        order = self.combo_order.currentText()
        min_v = parse_view_count_input(self.txt_min_views.text()) if hasattr(self, "txt_min_views") else None
        max_v = parse_view_count_input(self.txt_max_views.text()) if hasattr(self, "txt_max_views") else None
        count_label = f"{count} videos" if count else "All (Unlimited) videos"
        self.lbl_table_status.setText(f"Fetching {count_label} in {order} order...")

        self.fetch_thread = MediaFetchThread(
            url, count, order, force_refresh=False, min_views=min_v, max_views=max_v
        )
        self.fetch_thread.finished_signal.connect(self._on_fetch_finished)
        self.fetch_thread.error_signal.connect(self._on_fetch_error)
        self.fetch_thread.start()

    def _force_fetch_videos_clicked(self):
        url = self.txt_url.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", "Please enter a YouTube channel, playlist, or video URL.")
            return

        self.btn_fetch.setEnabled(False)
        if hasattr(self, "btn_force_fetch"):
            self.btn_force_fetch.setEnabled(False)
        self.btn_stop_fetch.setEnabled(True)
        count, auto_select_set = self._get_fetch_params()
        self._pending_auto_select_set = auto_select_set
        order = self.combo_order.currentText()
        min_v = parse_view_count_input(self.txt_min_views.text()) if hasattr(self, "txt_min_views") else None
        max_v = parse_view_count_input(self.txt_max_views.text()) if hasattr(self, "txt_max_views") else None
        count_label = f"{count} videos" if count else "All (Unlimited) videos"
        self.lbl_table_status.setText(f"⚡ Force fetching {count_label} in {order} order (bypassing cache)...")

        self.fetch_thread = MediaFetchThread(
            url, count, order, force_refresh=True, min_views=min_v, max_views=max_v
        )
        self.fetch_thread.finished_signal.connect(self._on_fetch_finished)
        self.fetch_thread.error_signal.connect(self._on_fetch_error)
        self.fetch_thread.start()

    def _on_url_text_changed(self, text: str):
        raw = text.strip()
        if len(raw) > 10 and any(k in raw for k in ("youtube.com", "youtu.be")):
            if hasattr(self, "lbl_total_available_videos"):
                self.lbl_total_available_videos.setText("Channel Videos: Detecting...")
            self.auto_fetch_timer.start()
        else:
            if hasattr(self, "lbl_total_available_videos"):
                self.lbl_total_available_videos.setText("Channel Videos: —")

    def _on_auto_fetch_timeout(self):
        url = self.txt_url.text().strip()
        if not url:
            return
        self.summary_thread = SummaryFetchThread(url)
        self.summary_thread.summary_ready.connect(self._on_summary_ready)
        self.summary_thread.start()

    def _on_summary_ready(self, summary: dict):
        vid_count = summary.get("video_count")
        c_name = summary.get("channel_name")
        if hasattr(self, "lbl_total_available_videos"):
            if vid_count:
                self.lbl_total_available_videos.setText(f"Channel Videos: {vid_count:,}")
            elif c_name:
                self.lbl_total_available_videos.setText(f"Channel: {c_name[:20]}")
            else:
                self.lbl_total_available_videos.setText("Channel Videos: Available")

    def _stop_fetch_clicked(self):
        if self.fetch_thread and self.fetch_thread.isRunning():
            self.fetch_thread.cancel()
            try:
                self.fetch_thread.terminate()
            except Exception:
                pass
            self.fetch_thread.quit()
        self.btn_fetch.setEnabled(True)
        if hasattr(self, "btn_force_fetch"):
            self.btn_force_fetch.setEnabled(True)
        self.btn_stop_fetch.setEnabled(False)
        self.lbl_table_status.setText("Fetch stopped immediately.")

    def _stop_all_batch_threads(self):
        if hasattr(self, "_phased_pipeline_state") and self._phased_pipeline_state:
            self._phased_pipeline_state["cancelled"] = True
        if hasattr(self, "_parallel_state") and self._parallel_state:
            self._parallel_state["cancelled"] = True
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
        self.all_fetched_candidates.clear()
        self.candidates.clear()
        self.transcripts_dict.clear()
        self.metadata_dict.clear()
        self.diagnostics_dict.clear()
        self.table.setRowCount(0)
        if hasattr(self, "txt_min_views"):
            self.txt_min_views.clear()
        if hasattr(self, "txt_max_views"):
            self.txt_max_views.clear()
        self._reset_table_filters()
        self.lbl_table_status.setText("Cleared. Paste a link above and click Fetch Videos.")
        self.lbl_range_status.setText("Selected: 0 / 0 videos")
        self.box_transcript_progress.setVisible(False)
        self.btn_view_diagnostics.setVisible(False)
        if hasattr(self, "lbl_total_available_videos"):
            self.lbl_total_available_videos.setText("Channel Videos: —")
        if hasattr(self, "lbl_unavailable_count"):
            self.lbl_unavailable_count.setText("Unavailable/Private: 0")

    def _on_fetch_finished(self, candidates: List[ChannelCandidate]):
        self.btn_fetch.setEnabled(True)
        if hasattr(self, "btn_force_fetch"):
            self.btn_force_fetch.setEnabled(True)
        self.btn_stop_fetch.setEnabled(False)
        self.all_fetched_candidates = list(candidates)
        order = self.combo_order.currentText()

        # Dynamic smart default concurrency matching total fetched videos count
        count = len(candidates)
        if count > 0:
            if hasattr(self, "spin_script_concurrency"):
                self.spin_script_concurrency.setValue(min(500, max(1, count)))
            if hasattr(self, "spin_audio_concurrency"):
                self.spin_audio_concurrency.setValue(min(500, max(1, count)))
            if hasattr(self, "spin_thumb_count"):
                self.spin_thumb_count.setValue(min(9999, max(1, count)))
            if hasattr(self, "lbl_total_available_videos"):
                self.lbl_total_available_videos.setText(f"Channel Videos: {count} loaded")

        # Apply view count filter if specified
        min_v = parse_view_count_input(self.txt_min_views.text()) if hasattr(self, "txt_min_views") else None
        max_v = parse_view_count_input(self.txt_max_views.text()) if hasattr(self, "txt_max_views") else None
        if min_v is not None or max_v is not None:
            self.candidates = ChannelFetcher.filter_and_sort_candidates(
                list(self.all_fetched_candidates), order=order, min_views=min_v, max_views=max_v
            )
        else:
            self.candidates = candidates

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

        self.lbl_table_status.setText(f"Loaded {len(self.candidates)} videos ({order}: V1 is the first video).")
        self._populate_table()
        self._update_range_status()

    def _on_fetch_error(self, err_msg: str):
        self.btn_fetch.setEnabled(True)
        if hasattr(self, "btn_force_fetch"):
            self.btn_force_fetch.setEnabled(True)
        self.btn_stop_fetch.setEnabled(False)
        self.lbl_table_status.setText("Fetch failed.")
        QMessageBox.critical(self, "Fetch Error", f"Could not fetch videos: {err_msg}")

    # ==================== TABLE SELECTION & ACTION BUTTONS ====================

    def _on_invert_selection(self):
        self._is_populating_table = True
        self.table.blockSignals(True)
        for row, cand in enumerate(self.candidates):
            cand.is_selected = not cand.is_selected
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked if cand.is_selected else Qt.Unchecked)
        self.table.blockSignals(False)
        self._is_populating_table = False
        self._update_range_status()

    def _on_select_missing(self):
        out_dir = Path(self.txt_out_dir.text())
        out_dir.mkdir(parents=True, exist_ok=True)
        self._is_populating_table = True
        self.table.blockSignals(True)
        for row, cand in enumerate(self.candidates):
            v_label = cand.version_label
            is_missing = False
            if self.chk_scripts.isChecked():
                script_path = out_dir / "Scripts" / f"{v_label} Script.txt"
                alt_script = out_dir / f"{v_label} Script.txt"
                if not script_path.exists() and not alt_script.exists():
                    is_missing = True
            if self.chk_mp3s.isChecked():
                mp3_path = out_dir / "Audio" / f"{v_label}.mp3"
                alt_mp3 = out_dir / f"{v_label}.mp3"
                if not mp3_path.exists() and not alt_mp3.exists():
                    is_missing = True
            if self.chk_thumbnails.isChecked():
                thumb_path = out_dir / "Thumbnails" / f"{v_label} Thumbnail.jpg"
                alt_thumb = out_dir / f"{v_label} Thumbnail.jpg"
                if not thumb_path.exists() and not alt_thumb.exists():
                    is_missing = True
            if self.chk_videos.isChecked():
                vid_path = out_dir / "Videos" / f"{v_label}.mp4"
                alt_vid = out_dir / f"{v_label}.mp4"
                if not vid_path.exists() and not alt_vid.exists():
                    is_missing = True

            cand.is_selected = is_missing
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked if is_missing else Qt.Unchecked)
        self.table.blockSignals(False)
        self._is_populating_table = False
        self._update_range_status()

    def _on_select_failed(self):
        self._is_populating_table = True
        self.table.blockSignals(True)
        for row, cand in enumerate(self.candidates):
            status_item = self.table.item(row, 8)
            stat = status_item.text() if status_item else ""
            diag = self.diagnostics_dict.get(cand.video_id, {})
            is_failed = ("Fail" in stat) or ("Error" in stat) or (diag.get("status") == "Failed")
            cand.is_selected = is_failed
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked if is_failed else Qt.Unchecked)
        self.table.blockSignals(False)
        self._is_populating_table = False
        self._update_range_status()

    def _on_select_incomplete(self):
        out_dir = Path(self.txt_out_dir.text())
        self._is_populating_table = True
        self.table.blockSignals(True)
        for row, cand in enumerate(self.candidates):
            v_label = cand.version_label
            is_incomplete = False
            # Audio < 10KB
            mp3_path = out_dir / "Audio" / f"{v_label}.mp3"
            alt_mp3 = out_dir / f"{v_label}.mp3"
            target_mp3 = mp3_path if mp3_path.exists() else (alt_mp3 if alt_mp3.exists() else None)
            if target_mp3 and target_mp3.stat().st_size < 10240:
                is_incomplete = True
            # Video < 50KB
            vid_path = out_dir / "Videos" / f"{v_label}.mp4"
            alt_vid = out_dir / f"{v_label}.mp4"
            target_vid = vid_path if vid_path.exists() else (alt_vid if alt_vid.exists() else None)
            if target_vid and target_vid.stat().st_size < 51200:
                is_incomplete = True
            # Script < 10 bytes
            sc_path = out_dir / "Scripts" / f"{v_label} Script.txt"
            alt_sc = out_dir / f"{v_label} Script.txt"
            target_sc = sc_path if sc_path.exists() else (alt_sc if alt_sc.exists() else None)
            if target_sc and target_sc.stat().st_size < 10:
                is_incomplete = True

            cand.is_selected = is_incomplete
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked if is_incomplete else Qt.Unchecked)
        self.table.blockSignals(False)
        self._is_populating_table = False
        self._update_range_status()

    def _on_select_skipped(self):
        skip_spec = self.txt_skip_ranges.text().strip() if hasattr(self, "txt_skip_ranges") else ""
        self._is_populating_table = True
        self.table.blockSignals(True)
        for row, cand in enumerate(self.candidates):
            status_item = self.table.item(row, 8)
            stat = status_item.text() if status_item else ""
            is_skipped = ("Skip" in stat)
            if skip_spec and not is_skipped:
                skipped_set = VRangeParser.parse(skip_spec, max_limit=len(self.candidates))
                if cand.version_num in skipped_set:
                    is_skipped = True
            cand.is_selected = is_skipped
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked if is_skipped else Qt.Unchecked)
        self.table.blockSignals(False)
        self._is_populating_table = False
        self._update_range_status()

    # ==================== TABLE INTERACTIVE FILTERS ====================

    def _apply_table_filters(self):
        kw = self.txt_filter_keyword.text().strip().lower() if hasattr(self, "txt_filter_keyword") else ""
        min_v = self.spin_filter_min_views.value() if hasattr(self, "spin_filter_min_views") else 0
        min_dur = (self.spin_filter_min_dur.value() * 60) if hasattr(self, "spin_filter_min_dur") else 0
        max_dur = (self.spin_filter_max_dur.value() * 60) if hasattr(self, "spin_filter_max_dur") else 0
        date_opt = self.combo_filter_date.currentText() if hasattr(self, "combo_filter_date") else "All Dates"
        type_opt = self.combo_filter_type.currentText() if hasattr(self, "combo_filter_type") else "All Types"

        for row, cand in enumerate(self.candidates):
            visible = True
            if kw and kw not in cand.title.lower():
                visible = False
            if visible and min_v > 0 and cand.view_count < min_v:
                visible = False
            if visible and min_dur > 0 and cand.duration < min_dur:
                visible = False
            if visible and max_dur > 0 and cand.duration > max_dur:
                visible = False
            if visible:
                if type_opt == "Shorts Only" and cand.duration > 60:
                    visible = False
                elif type_opt == "Videos Only" and (cand.duration <= 60 and cand.duration > 0):
                    visible = False
            if visible and date_opt != "All Dates" and cand.upload_date:
                u_date = cand.upload_date.lower()
                if date_opt == "Last 30 Days":
                    if not any(x in u_date for x in ("day", "hour", "minute", "second", "yesterday", "1 week", "2 week", "3 week", "4 week")):
                        visible = False
                elif date_opt == "Last 3 Months":
                    if any(x in u_date for x in ("year", "4 month", "5 month", "6 month", "7 month", "8 month", "9 month", "10 month", "11 month")):
                        visible = False
                elif date_opt == "Last Year":
                    if any(x in u_date for x in ("2 year", "3 year", "4 year", "5 year", "10 year")):
                        visible = False

            self.table.setRowHidden(row, not visible)

    def _reset_table_filters(self):
        if hasattr(self, "txt_filter_keyword"):
            self.txt_filter_keyword.clear()
        if hasattr(self, "spin_filter_min_views"):
            self.spin_filter_min_views.setValue(0)
        if hasattr(self, "spin_filter_min_dur"):
            self.spin_filter_min_dur.setValue(0)
        if hasattr(self, "spin_filter_max_dur"):
            self.spin_filter_max_dur.setValue(0)
        if hasattr(self, "combo_filter_date"):
            self.combo_filter_date.setCurrentIndex(0)
        if hasattr(self, "combo_filter_type"):
            self.combo_filter_type.setCurrentIndex(0)
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, False)

    # ==================== MASTER ACTIONS & SMART OPERATIONS ====================

    def _regenerate_titles_clicked(self):
        selected = self._sync_selected_candidates()
        if not selected:
            selected = self.candidates
        if not selected:
            QMessageBox.warning(self, "No Videos", "No videos available to generate Titles.txt.")
            return
        out_dir = Path(self.txt_out_dir.text())
        out_dir.mkdir(parents=True, exist_ok=True)
        titles_path = out_dir / "Titles.txt"
        try:
            channel_name = getattr(self, "current_channel_name", "") or getattr(self.fetcher, "channel_name", "")
            channel_url = self.txt_url.text().strip() or getattr(self.fetcher, "channel_url", "")
            saved_file = ZipPackager.export_single_titles_file(
                selected, titles_path, channel_name=channel_name, channel_url=channel_url
            )
            QMessageBox.information(
                self,
                "Titles.txt Regenerated",
                f"Successfully regenerated Titles.txt with {len(selected)} entries at:\n{saved_file}",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to regenerate Titles.txt: {e}")

    def _smart_download_clicked(self):
        """
        Master Smart Download:
        Automatically orchestrates:
        1. Fetch channel if needed
        2. Apply view count filter & popularity sort
        3. Scan existing disk files & skip completed files
        4. Download all selected missing data in parallel
        5. Live save in real-time
        6. Display comprehensive statistics report
        """
        if not self.candidates:
            url = self.txt_url.text().strip()
            if not url:
                QMessageBox.warning(self, "Missing URL", "Please enter a YouTube URL to run Smart Download.")
                return
            self._fetch_videos_clicked()
            return

        if hasattr(self, "chk_mode_missing_only"):
            self.chk_mode_missing_only.setChecked(True)
        if hasattr(self, "chk_mode_force_overwrite"):
            self.chk_mode_force_overwrite.setChecked(False)

        self._download_selected_items_clicked()

    def _sync_channel_clicked(self):
        """
        Sync Channel:
        Compares local folder with channel videos and downloads only new/missing videos.
        """
        if not self.candidates:
            self._fetch_videos_clicked()
            return
        self._on_select_missing()
        selected = [c for c in self.candidates if c.is_selected]
        if not selected:
            QMessageBox.information(self, "Channel In Sync", "All videos in this channel are already fully downloaded on disk!")
            return
        self._download_selected_items_clicked()

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

            # 3: Views
            it_views = QTableWidgetItem(cand.view_count_str or "—")
            it_views.setTextAlignment(Qt.AlignCenter)
            it_views.setForeground(QColor("#8e8e93"))
            self.table.setItem(row, 3, it_views)

            # 4: Duration
            it_dur = QTableWidgetItem(cand.duration_str)
            it_dur.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 4, it_dur)

            # 5: Date
            it_date = QTableWidgetItem(cand.upload_date)
            it_date.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 5, it_date)

            # 6: Dedicated Copy Title button (Format: "V1. Title")
            w_copy = QWidget()
            l_copy = QHBoxLayout(w_copy)
            l_copy.setContentsMargins(2, 2, 2, 2)
            btn_copy_title = QPushButton("📋 Copy")
            btn_copy_title.setToolTip(f"Copy formatted title: '{cand.version_label}. {cand.title}'")
            btn_copy_title.setStyleSheet("font-size: 11px; padding: 4px;")
            btn_copy_title.clicked.connect(lambda _, c=cand, b=btn_copy_title: self._copy_single_title(c, b))
            l_copy.addWidget(btn_copy_title)
            self.table.setCellWidget(row, 6, w_copy)

            # 7: Script / TXT Status & View
            has_script = cand.video_id in self.transcripts_dict and bool(self.transcripts_dict[cand.video_id])
            w_script = QWidget()
            l_script = QHBoxLayout(w_script)
            l_script.setContentsMargins(2, 2, 2, 2)
            btn_script = QPushButton("View Script" if has_script else "Get Script")
            btn_script.setStyleSheet("font-size: 11px; padding: 4px;")
            btn_script.clicked.connect(lambda _, c=cand: self._on_single_script_clicked(c))
            l_script.addWidget(btn_script)
            self.table.setCellWidget(row, 7, w_script)

            # 8: Item Status
            st = getattr(cand, "status_text", "Ready")
            it_status = QTableWidgetItem(st)
            it_status.setTextAlignment(Qt.AlignCenter)
            if "Completed" in st or "Done" in st or "✓" in st:
                it_status.setForeground(QColor("#34c759"))
            elif "Retry" in st:
                it_status.setForeground(QColor("#ff9500"))
            elif "Fail" in st or "Error" in st:
                it_status.setForeground(QColor("#ff3b30"))
            elif "Download" in st or "Active" in st:
                it_status.setForeground(QColor("#007aff"))
            else:
                it_status.setForeground(QColor("#8e8e93"))
            self.table.setItem(row, 8, it_status)

            # 9: Dual Actions: ⬇ MP3 and ⬇ Video
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

            self.table.setCellWidget(row, 9, w_act)

        self.table.blockSignals(False)
        self._is_populating_table = False

    def _update_candidate_status(self, video_id: str, status: str):
        """Updates live item status in table."""
        for row, cand in enumerate(self.candidates):
            if cand.video_id == video_id:
                cand.status_text = status
                it = self.table.item(row, 8)
                if it:
                    it.setText(status)
                    if "Completed" in status or "Done" in status or "✓" in status:
                        it.setForeground(QColor("#34c759"))
                    elif "Retry" in status:
                        it.setForeground(QColor("#ff9500"))
                    elif "Fail" in status or "Error" in status:
                        it.setForeground(QColor("#ff3b30"))
                    elif "Download" in status:
                        it.setForeground(QColor("#007aff"))
                    else:
                        it.setForeground(QColor("#8e8e93"))
                break

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
        if self.table.rowCount() == 0 and self.candidates:
            return [c for c in self.candidates if getattr(c, "is_selected", True)]

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

    # ==================== SEQUENTIAL PHASED PIPELINE (v3.4) ====================
    # Strict execution order:
    # Phase 1: Titles (Titles.txt) -> Real-time save
    # Phase 2: Thumbnails (custom total count) -> Real-time save
    # Phase 3: Channel Assets (Banner & Logo) -> Real-time save
    # Phase 4: Scripts / Transcripts (Parallel worker pool, custom concurrency) -> Real-time save
    # Phase 5: Audio / Media (Parallel queue, custom concurrency) -> Real-time save
    # Finalize: Full statistics modal with retry

    def _download_selected_items_clicked(self):
        """
        Sequential Phased & Parallel Download Pipeline (v3.5):
        1. Titles (immediate real-time save to Titles.txt, selective/linked)
        2. Thumbnails (custom total limit, real-time live save)
        3. Channel Assets (competitor banner & logo, real-time live save)
        4. Parallel Scripts & Audio (concurrent streams, unlimited concurrency up to 500, real-time live save)
        Finalize: Full statistics modal with auto-retry
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

        want_titles = self.chk_titles.isChecked()
        want_thumbnails = self.chk_thumbnails.isChecked()
        want_channel_assets = self.chk_channel_assets.isChecked()
        want_scripts = self.chk_scripts.isChecked()
        want_mp3s = self.chk_mp3s.isChecked()
        want_videos = self.chk_videos.isChecked()

        if not any([
            want_titles,
            want_thumbnails,
            want_channel_assets,
            want_scripts,
            want_mp3s,
            want_videos,
        ]):
            QMessageBox.warning(
                self,
                "No Data Selected",
                "Please select at least one item to download in the 'Custom Data Selection' panel.",
            )
            return

        # V3.5 Selective Download & Skip Manager
        skip_spec = self.txt_skip_ranges.text().strip() if hasattr(self, "txt_skip_ranges") else ""
        script_v_spec = self.txt_script_v_range.text().strip() if hasattr(self, "txt_script_v_range") else ""
        audio_v_spec = self.txt_audio_v_range.text().strip() if hasattr(self, "txt_audio_v_range") else ""
        want_linked_titles = self.chk_titles_linked.isChecked() if hasattr(self, "chk_titles_linked") else False

        if skip_spec:
            active_selected = VRangeParser.filter_candidates(selected, exclude_spec=skip_spec)
            for cand in selected:
                if cand not in active_selected:
                    self._update_candidate_status(cand.video_id, "Skipped")
        else:
            active_selected = list(selected)

        if not active_selected:
            QMessageBox.warning(
                self,
                "All Videos Skipped",
                "All selected videos were excluded by the Skip V-Ranges filter.",
            )
            return

        # Filter candidate lists per asset type
        thumb_v_spec = self.txt_thumb_v_range.text().strip() if hasattr(self, "txt_thumb_v_range") else ""
        video_v_spec = self.txt_video_v_range.text().strip() if hasattr(self, "txt_video_v_range") else ""

        script_candidates = (
            VRangeParser.filter_candidates(active_selected, include_spec=script_v_spec)
            if (want_scripts and script_v_spec)
            else (list(active_selected) if want_scripts else [])
        )

        audio_candidates = (
            VRangeParser.filter_candidates(active_selected, include_spec=audio_v_spec)
            if (want_mp3s and audio_v_spec)
            else (list(active_selected) if want_mp3s else [])
        )

        video_candidates = (
            VRangeParser.filter_candidates(active_selected, include_spec=video_v_spec)
            if (want_videos and video_v_spec)
            else (list(active_selected) if want_videos else [])
        )

        thumb_candidates = (
            VRangeParser.filter_candidates(active_selected, include_spec=thumb_v_spec)
            if (want_thumbnails and thumb_v_spec)
            else (list(active_selected) if want_thumbnails else [])
        )
        if want_thumbnails and not thumb_v_spec:
            thumb_count = self.spin_thumb_count.value() if hasattr(self, "spin_thumb_count") else len(thumb_candidates)
            thumb_candidates = thumb_candidates[: min(thumb_count, len(thumb_candidates))]
        else:
            thumb_count = len(thumb_candidates)

        if want_titles:
            if want_linked_titles:
                linked_ids = {c.video_id for c in script_candidates} | {c.video_id for c in audio_candidates} | {c.video_id for c in video_candidates}
                title_candidates = [c for c in active_selected if c.video_id in linked_ids]
            else:
                title_candidates = list(active_selected)
        else:
            title_candidates = []

        combined_media_candidates = list({c.video_id: c for c in (audio_candidates + video_candidates)}.values())

        if not any([
            title_candidates,
            thumb_candidates,
            want_channel_assets,
            script_candidates,
            audio_candidates,
            video_candidates,
        ]):
            QMessageBox.warning(
                self,
                "No Matching Items",
                "No items match the selected V-ranges.",
            )
            return

        base_dir = Path(self.txt_out_dir.text().strip())
        base_dir.mkdir(parents=True, exist_ok=True)

        channel_name = self.fetcher.channel_name or (selected[0].uploader if selected else "")
        channel_url = self.fetcher.channel_url or (selected[0].channel_url if selected else self.txt_url.text().strip())

        script_concurrency = self.spin_script_concurrency.value() if hasattr(self, "spin_script_concurrency") else 3
        audio_concurrency = self.spin_audio_concurrency.value() if hasattr(self, "spin_audio_concurrency") else 3

        self.settings.script_concurrent_downloads = script_concurrency
        self.settings.audio_concurrent_downloads = audio_concurrency
        self.settings.concurrent_downloads = audio_concurrency
        self.settings.save()
        self.queue_manager.settings.concurrent_downloads = audio_concurrency

        self.diagnostics_dict.clear()
        self.btn_view_diagnostics.setVisible(False)
        self.btn_resume_prog.setVisible(False)
        self.box_transcript_progress.setVisible(True)
        self.bar_transcripts.setValue(0)
        self.lbl_transcript_status.setText("🚀 Starting Phased & Parallel Downloads...")
        self.lbl_progress_details.setText(f"Preparing download for {len(active_selected)} videos...")

        # Set table status to Queued for active videos
        for cand in active_selected:
            self._update_candidate_status(cand.video_id, "Queued")

        force_overwrite = bool(self.chk_mode_force_overwrite.isChecked()) if hasattr(self, "chk_mode_force_overwrite") else False
        missing_only = bool(self.chk_mode_missing_only.isChecked()) if hasattr(self, "chk_mode_missing_only") else True

        has_media = bool((want_mp3s and audio_candidates) or (want_videos and video_candidates))

        self._phased_pipeline_state = {
            "base_dir": base_dir,
            "selected": active_selected,
            "all_selected": selected,
            "channel_name": channel_name,
            "channel_url": channel_url,
            "want_titles": bool(want_titles and title_candidates),
            "want_thumbnails": bool(want_thumbnails and thumb_candidates),
            "want_channel_assets": want_channel_assets,
            "want_scripts": bool(want_scripts and script_candidates),
            "want_mp3s": bool(want_mp3s and audio_candidates),
            "want_videos": bool(want_videos and video_candidates),
            "title_candidates": title_candidates,
            "thumb_candidates": thumb_candidates,
            "script_candidates": script_candidates,
            "audio_candidates": audio_candidates,
            "video_candidates": video_candidates,
            "media_candidates": combined_media_candidates,
            "thumb_count": thumb_count,
            "script_concurrency": script_concurrency,
            "audio_concurrency": audio_concurrency,
            "force_overwrite": force_overwrite,
            "missing_only": missing_only,
            # Phase done flags
            "titles_done": not bool(want_titles and title_candidates),
            "thumbs_done": not bool(want_thumbnails and thumb_candidates),
            "assets_done": not want_channel_assets,
            "scripts_done": not bool(want_scripts and script_candidates),
            "media_done": not has_media,
            # Counts
            "titles_saved": 0,
            "thumbs_saved": 0,
            "thumbs_skipped": 0,
            "assets_saved": 0,
            "scripts_saved": 0,
            "scripts_skipped": 0,
            "media_items_map": {},
            "audio_completed_count": 0,
            "audio_failed_count": 0,
            "video_completed_count": 0,
            "video_failed_count": 0,
            "media_completed_count": 0,
            "media_failed_count": 0,
            "failed_items": [],
            "cancelled": False,
            "completed": False,
        }
        self._parallel_state = self._phased_pipeline_state

        # Update category labels
        self.lbl_prog_titles.setText("📝 <b>1. Titles:</b> " + (f"Waiting... ({len(title_candidates)} titles)" if title_candidates else "Not Selected"))
        self.lbl_prog_thumbs.setText("🖼️ <b>2. Thumbnails:</b> " + (f"Waiting... ({len(thumb_candidates)} thumbs)" if thumb_candidates else "Not Selected"))
        self.lbl_prog_assets.setText("🎨 <b>3. Channel Assets:</b> " + ("Waiting..." if want_channel_assets else "Not Selected"))
        self.lbl_prog_scripts.setText("📜 <b>4. Scripts:</b> " + (f"Waiting... ({len(script_candidates)} scripts, ⚡ {script_concurrency} streams)" if script_candidates else "Not Selected"))
        
        v_qual = self.combo_video_quality.currentText() if hasattr(self, "combo_video_quality") else "Best"
        if want_mp3s and want_videos and audio_candidates and video_candidates:
            self.lbl_prog_media.setText(f"🎵 <b>5. Audio:</b> Waiting... ({len(audio_candidates)}) | 🎬 <b>Videos:</b> Waiting... ({len(video_candidates)})")
        elif want_mp3s and audio_candidates:
            self.lbl_prog_media.setText(f"🎵 <b>5. Audio:</b> Waiting... ({len(audio_candidates)} audio, ⚡ {audio_concurrency} streams)")
        elif want_videos and video_candidates:
            self.lbl_prog_media.setText(f"🎬 <b>5. Videos:</b> Waiting... ({len(video_candidates)} videos, {v_qual})")
        else:
            self.lbl_prog_media.setText("🎵 <b>5. Audio/Media:</b> Not Selected")

        # Launch Phase 1: Titles
        self._run_phase_1_titles()

    def _resume_download_clicked(self):
        """Resumes download: checks on disk and skips existing files instantly."""
        self._download_selected_items_clicked()

    # ---------- PHASE 1: TITLES ----------
    def _run_phase_1_titles(self):
        state = self._phased_pipeline_state
        if state.get("cancelled"):
            return

        if state["want_titles"] and state["title_candidates"]:
            tot_t = len(state["title_candidates"])
            self.lbl_transcript_status.setText(f"Phase 1: Exporting {tot_t} Video Titles...")
            self.lbl_prog_titles.setText(f"📝 <b>1. Titles:</b> Writing Titles.txt ({tot_t} titles)...")
            titles_file = state["base_dir"] / "Titles.txt"
            success = False
            last_err = ""
            for attempt in range(1, 6):
                try:
                    ZipPackager.export_single_titles_file(
                        candidates=state["title_candidates"],
                        output_file=titles_file,
                        channel_name=state["channel_name"],
                        channel_url=state["channel_url"],
                    )
                    success = True
                    break
                except Exception as e:
                    last_err = str(e)
                    time.sleep(0.3)

            if success:
                state["titles_saved"] = tot_t
                self.lbl_prog_titles.setText(f"📝 <b>1. Titles:</b> ✓ Saved ({tot_t} titles)")
            else:
                logger.error(f"Failed to export Titles.txt after 5 retries: {last_err}")
                self.lbl_prog_titles.setText(f"📝 <b>1. Titles:</b> ⚠️ Error after 5 retries: {last_err}")
                state["failed_items"].append({
                    "version_label": "All",
                    "title": "Titles.txt",
                    "category": "Title",
                    "reason": last_err,
                })
        else:
            self.lbl_prog_titles.setText("📝 <b>1. Titles:</b> Not Selected")

        state["titles_done"] = True
        self._update_phased_overall_progress()

        # Chain to Phase 2: Thumbnails
        self._run_phase_2_thumbnails()

    # ---------- PHASE 2: THUMBNAILS ----------
    def _run_phase_2_thumbnails(self):
        state = self._phased_pipeline_state
        if state.get("cancelled"):
            return

        if state["want_thumbnails"] and state["thumb_candidates"]:
            thumb_dir = state["base_dir"] / "Thumbnails"
            thumb_dir.mkdir(parents=True, exist_ok=True)
            max_c = len(state["thumb_candidates"])
            self.lbl_transcript_status.setText(f"Phase 2: Downloading Thumbnails (1 to {max_c})...")
            self.lbl_prog_thumbs.setText(f"🖼️ <b>2. Thumbnails:</b> Starting (0/{max_c})...")

            for cand in state["thumb_candidates"]:
                self._update_candidate_status(cand.video_id, "Downloading Thumbnail")

            self.thumbnail_thread = BatchThumbnailThread(
                candidates=state["thumb_candidates"],
                output_dir=thumb_dir,
                max_count=max_c,
            )
            self.thumbnail_thread.item_progress.connect(self._on_phased_thumb_item_prog)
            self.thumbnail_thread.all_finished.connect(self._on_phased_thumb_done)
            self.thumbnail_thread.start()
        else:
            state["thumbs_done"] = True
            self.lbl_prog_thumbs.setText("🖼️ <b>2. Thumbnails:</b> Not Selected")
            self._update_phased_overall_progress()
            self._run_phase_3_channel_assets()

    def _on_phased_thumb_item_prog(self, cur, tot, done_c, skip_c, v_lbl, title):
        state = getattr(self, "_phased_pipeline_state", None) or getattr(self, "_parallel_state", None)
        if not state:
            return
        state["thumbs_saved"] = done_c
        state["thumbs_skipped"] = skip_c
        self.lbl_prog_thumbs.setText(f"🖼️ <b>2. Thumbnails:</b> {done_c + skip_c}/{tot} (✓ {done_c}, ⏭ {skip_c})")
        self.lbl_progress_details.setText(f"Saving thumbnail live: [{v_lbl}] {title}")
        self._update_phased_overall_progress()

    def _on_phased_thumb_done(self, is_ok, saved_list):
        state = getattr(self, "_phased_pipeline_state", None) or getattr(self, "_parallel_state", None)
        if not state:
            return
        state["thumbs_done"] = True
        state["thumbs_saved"] = len(saved_list) if saved_list else 0
        self.lbl_prog_thumbs.setText(f"🖼️ <b>2. Thumbnails:</b> ✓ Complete ({len(saved_list)} saved)")
        self._update_phased_overall_progress()

        # Chain to Phase 3: Channel Assets
        self._run_phase_3_channel_assets()

    # ---------- PHASE 3: CHANNEL ASSETS ----------
    def _run_phase_3_channel_assets(self):
        state = self._phased_pipeline_state
        if state.get("cancelled"):
            return

        channel_url = state["channel_url"]
        if state["want_channel_assets"] and channel_url:
            assets_dir = state["base_dir"] / "Channel Assets"
            assets_dir.mkdir(parents=True, exist_ok=True)
            self.lbl_transcript_status.setText("Phase 3: Downloading Channel Assets (Banner & Logo)...")
            self.lbl_prog_assets.setText("🎨 <b>3. Channel Assets:</b> Downloading...")

            self.channel_assets_thread = ChannelAssetsThread(
                channel_url=channel_url,
                output_dir=assets_dir,
                banner_url=getattr(self.fetcher, "banner_url", ""),
                logo_url=getattr(self.fetcher, "logo_url", ""),
            )
            self.channel_assets_thread.finished_signal.connect(self._on_phased_assets_finished)
            self.channel_assets_thread.error_signal.connect(self._on_phased_assets_error)
            self.channel_assets_thread.start()
        else:
            state["assets_done"] = True
            reason = "Skipped (No URL)" if state["want_channel_assets"] else "Not Selected"
            self.lbl_prog_assets.setText(f"🎨 <b>3. Channel Assets:</b> {reason}")
            self._update_phased_overall_progress()
            self._run_phase_4_and_5_parallel()

    def _on_phased_assets_finished(self, res: dict):
        state = getattr(self, "_phased_pipeline_state", None) or getattr(self, "_parallel_state", None)
        if not state:
            return
        state["assets_done"] = True
        state["assets_saved"] = len(res) if res else 0
        self.lbl_prog_assets.setText(f"🎨 <b>3. Channel Assets:</b> ✓ Saved ({len(res)} assets)")
        self._update_phased_overall_progress()
        self._run_phase_4_and_5_parallel()

    def _on_phased_assets_error(self, err_msg: str):
        state = getattr(self, "_phased_pipeline_state", None) or getattr(self, "_parallel_state", None)
        if not state:
            return
        state["assets_done"] = True
        self.lbl_prog_assets.setText(f"🎨 <b>3. Channel Assets:</b> ⚠️ {err_msg}")
        self._update_phased_overall_progress()
        self._run_phase_4_and_5_parallel()

    # ---------- PHASES 4 & 5: CONCURRENT PARALLEL SCRIPTS & AUDIO ----------
    def _run_phase_4_and_5_parallel(self):
        """
        Executes Scripts and Audio concurrently in parallel (v3.5).
        Both pipelines run simultaneously with their independent concurrency streams.
        """
        state = self._phased_pipeline_state
        if state.get("cancelled"):
            return

        scripts_started = False
        media_started = False

        # 1. Launch Scripts in parallel
        if state["want_scripts"] and state["script_candidates"]:
            scripts_dir = state["base_dir"] / "Scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            conc = state["script_concurrency"]
            total_cand = len(state["script_candidates"])
            self.lbl_prog_scripts.setText(f"📜 <b>4. Scripts:</b> Starting 0/{total_cand} (⚡ {conc} Concurrent Streams)...")

            for cand in state["script_candidates"]:
                self._update_candidate_status(cand.video_id, "Downloading Script")

            self.transcripts_thread = BatchTranscriptThread(
                candidates=state["script_candidates"],
                existing_transcripts=self.transcripts_dict,
                output_dir=scripts_dir,
                concurrency=conc,
            )
            self.transcripts_thread.item_progress.connect(self._on_phased_script_item_prog)
            self.transcripts_thread.item_fetched.connect(self._on_script_item)
            self.transcripts_thread.item_diagnostics.connect(self._on_script_diag)
            self.transcripts_thread.all_finished.connect(self._on_phased_script_done)
            self.transcripts_thread.start()
            scripts_started = True
        else:
            state["scripts_done"] = True
            self.lbl_prog_scripts.setText("📜 <b>4. Scripts:</b> Not Selected")

        # 2. Launch Audio & Video Media in parallel
        media_items = []
        if state["want_mp3s"] and state["audio_candidates"]:
            audio_dir = state["base_dir"] / "Audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            for cand in state["audio_candidates"]:
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
                state["media_items_map"][item.id] = item

        if state["want_videos"] and state["video_candidates"]:
            v_dir = state["base_dir"] / "Videos"
            v_dir.mkdir(parents=True, exist_ok=True)
            for cand in state["video_candidates"]:
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
                state["media_items_map"][item.id] = item

        if media_items:
            conc = state["audio_concurrency"]
            v_qual = self.combo_video_quality.currentText() if hasattr(self, "combo_video_quality") else "Best"
            if state.get("want_mp3s") and state.get("want_videos"):
                self.lbl_prog_media.setText(
                    f"🎵 <b>5. Audio:</b> 0/{len(state['audio_candidates'])} | 🎬 <b>Videos:</b> 0/{len(state['video_candidates'])} (⚡ {conc} Concurrent)"
                )
            elif state.get("want_mp3s"):
                self.lbl_prog_media.setText(f"🎵 <b>5. Audio:</b> 0/{len(state['audio_candidates'])} (⚡ {conc} Concurrent)")
            else:
                self.lbl_prog_media.setText(f"🎬 <b>5. Videos:</b> 0/{len(state['video_candidates'])} ({v_qual})")

            if state.get("want_mp3s"):
                for cand in state.get("audio_candidates", []):
                    cur = getattr(cand, "status_text", "")
                    if not cur or cur == "Queued":
                        self._update_candidate_status(cand.video_id, "Downloading Audio")
            if state.get("want_videos"):
                for cand in state.get("video_candidates", []):
                    cur = getattr(cand, "status_text", "")
                    if not cur or cur == "Queued":
                        self._update_candidate_status(cand.video_id, "Downloading Video")

            try:
                self.queue_manager.item_completed.disconnect(self._on_phased_media_completed)
            except Exception:
                pass
            try:
                self.queue_manager.item_failed.disconnect(self._on_phased_media_failed)
            except Exception:
                pass
            try:
                self.queue_manager.all_finished.disconnect(self._on_phased_media_all_finished)
            except Exception:
                pass

            self.queue_manager.item_completed.connect(self._on_phased_media_completed)
            self.queue_manager.item_failed.connect(self._on_phased_media_failed)
            self.queue_manager.all_finished.connect(self._on_phased_media_all_finished)

            self.queue_manager.add_items(media_items)
            self.queue_manager.start()
            media_started = True
        else:
            state["media_done"] = True
            self.lbl_prog_media.setText("🎵 <b>5. Audio/Media:</b> Not Selected")

        if scripts_started and media_started:
            self.lbl_transcript_status.setText(
                f"Phase 4 & 5: Parallel Streaming (Scripts ⚡ {state['script_concurrency']} & Audio ⚡ {state['audio_concurrency']})..."
            )
        elif scripts_started:
            self.lbl_transcript_status.setText(f"Phase 4/5: Downloading Scripts (⚡ {state['script_concurrency']} Concurrent Streams)...")
        elif media_started:
            self.lbl_transcript_status.setText(f"Phase 5/5: Downloading Audio (⚡ {state['audio_concurrency']} Concurrent Streams)...")
        else:
            self._update_phased_overall_progress()
            self._finalize_phased_pipeline()
            return

        self._update_phased_overall_progress()

    def _run_phase_4_scripts(self):
        """Backward compatibility alias."""
        self._run_phase_4_and_5_parallel()

    def _run_phase_5_media(self):
        """Backward compatibility alias."""
        self._run_phase_4_and_5_parallel()

    def _on_script_item(self, vid_id: str, text: str):
        self.transcripts_dict[vid_id] = text

    def _on_script_diag(self, vid_id: str, method: str, diag: str):
        self.diagnostics_dict[vid_id] = {"method": method, "diag": diag}

    def _on_phased_script_item_prog(self, cur, tot, done_c, skip_c, v_lbl, title):
        state = getattr(self, "_phased_pipeline_state", None) or getattr(self, "_parallel_state", None)
        if not state:
            return
        state["scripts_saved"] = done_c
        state["scripts_skipped"] = skip_c
        self.lbl_prog_scripts.setText(f"📜 <b>4. Scripts:</b> {done_c + skip_c}/{tot} (✓ {done_c}, ⏭ {skip_c})")
        self.lbl_progress_details.setText(f"Saving script live: [{v_lbl}] {title}")
        self._update_phased_overall_progress()

    def _on_phased_script_done(self, is_ok: bool):
        state = getattr(self, "_phased_pipeline_state", None) or getattr(self, "_parallel_state", None)
        if not state:
            return
        state["scripts_done"] = True
        try:
            scripts_dir = state["base_dir"] / "Scripts"
            saved_s = TranscriptFetcher.export_transcripts_to_folder(
                candidates=state.get("script_candidates", state.get("selected", [])),
                transcripts_dict=self.transcripts_dict,
                output_dir=scripts_dir,
            )
            state["scripts_saved"] = len(saved_s)
            self._populate_table()
        except Exception as e:
            logger.debug(f"Error finalizing transcripts: {e}")

        # Update candidate status in table
        for cand in state.get("script_candidates", []):
            if cand.video_id in self.transcripts_dict and self.transcripts_dict[cand.video_id]:
                if not (state.get("want_mp3s") or state.get("want_videos")):
                    self._update_candidate_status(cand.video_id, "Completed")
            else:
                self._update_candidate_status(cand.video_id, "Script Missing")

        self.lbl_prog_scripts.setText(f"📜 <b>4. Scripts:</b> ✓ Complete ({state['scripts_saved']} saved)")
        self._update_phased_overall_progress()

        # If audio media is also completed, finalize the pipeline!
        if state.get("media_done"):
            self._finalize_phased_pipeline()

    def _on_phased_media_completed(self, item):
        state = getattr(self, "_phased_pipeline_state", None) or getattr(self, "_parallel_state", None)
        if not state or item.id not in state["media_items_map"]:
            return
        if getattr(item, "media_type", "") == "Audio":
            state["audio_completed_count"] = state.get("audio_completed_count", 0) + 1
        elif getattr(item, "media_type", "") == "Video":
            state["video_completed_count"] = state.get("video_completed_count", 0) + 1
        state["media_completed_count"] = state.get("media_completed_count", 0) + 1

        if item.video_id:
            self._update_candidate_status(item.video_id, "Completed")
        total_media = len(state["media_items_map"])
        if total_media > 0 and (state["media_completed_count"] + state["media_failed_count"] >= total_media):
            state["media_done"] = True
        self._update_phased_media_status()
        self._update_phased_overall_progress()
        if state["media_done"] and state.get("scripts_done"):
            self._finalize_phased_pipeline()

    def _on_phased_media_failed(self, item, err_msg):
        state = getattr(self, "_phased_pipeline_state", None) or getattr(self, "_parallel_state", None)
        if not state or item.id not in state["media_items_map"]:
            return
        if getattr(item, "media_type", "") == "Audio":
            state["audio_failed_count"] = state.get("audio_failed_count", 0) + 1
        elif getattr(item, "media_type", "") == "Video":
            state["video_failed_count"] = state.get("video_failed_count", 0) + 1
        state["media_failed_count"] = state.get("media_failed_count", 0) + 1

        if item.video_id:
            self._update_candidate_status(item.video_id, f"Failed: {err_msg[:25]}")
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
        self._update_phased_media_status()
        self._update_phased_overall_progress()
        if state["media_done"] and state.get("scripts_done"):
            self._finalize_phased_pipeline()

    def _on_phased_media_all_finished(self):
        state = getattr(self, "_phased_pipeline_state", None) or getattr(self, "_parallel_state", None)
        if not state:
            return
        state["media_done"] = True
        self._update_phased_media_status()
        self._update_phased_overall_progress()
        if state.get("scripts_done"):
            self._finalize_phased_pipeline()

    def _update_phased_media_status(self):
        state = getattr(self, "_phased_pipeline_state", None) or getattr(self, "_parallel_state", None)
        if not state or not (state.get("want_mp3s") or state.get("want_videos")):
            return

        want_audio = state.get("want_mp3s")
        want_video = state.get("want_videos")
        audio_tot = len(state.get("audio_candidates", []))
        video_tot = len(state.get("video_candidates", []))
        audio_done = state.get("audio_completed_count", 0)
        audio_fail = state.get("audio_failed_count", 0)
        video_done = state.get("video_completed_count", 0)
        video_fail = state.get("video_failed_count", 0)
        conc = state.get("audio_concurrency", 3)
        v_qual = getattr(self, "combo_video_quality", None)
        v_qual_text = v_qual.currentText() if v_qual else "Best"

        parts = []
        if want_audio:
            part = f"🎵 <b>5. Audio:</b> {audio_done}/{audio_tot}"
            if audio_fail > 0:
                part += f" (❌ {audio_fail} Failed)"
            parts.append(part)

        if want_video:
            prefix = "🎬 <b>5. Videos:</b> " if not want_audio else "🎬 <b>Videos:</b> "
            part = f"{prefix}{video_done}/{video_tot}"
            if video_fail > 0:
                part += f" (❌ {video_fail} Failed)"
            parts.append(part)

        if state.get("media_done"):
            status_text = " | ".join(p.replace("<b>", "<b>✓ ") for p in parts)
        else:
            status_text = " | ".join(parts)
            if want_audio and not want_video:
                status_text += f" (⚡ {conc} Concurrent)"
            elif want_video and not want_audio:
                status_text += f" ({v_qual_text})"

        self.lbl_prog_media.setText(status_text)

    def _update_phased_overall_progress(self):
        state = getattr(self, "_phased_pipeline_state", None) or getattr(self, "_parallel_state", None)
        if not state:
            return

        total_weight = 0
        completed_weight = 0

        if state.get("want_titles"):
            total_weight += 5
            if state.get("titles_done"):
                completed_weight += 5

        if state.get("want_thumbnails"):
            total_weight += 25
            target_t = max(1, len(state.get("thumb_candidates", [])))
            thumbs_progress = (state.get("thumbs_saved", 0) + state.get("thumbs_skipped", 0)) / target_t
            completed_weight += int(25 * min(1.0, thumbs_progress))

        if state.get("want_channel_assets"):
            total_weight += 5
            if state.get("assets_done"):
                completed_weight += 5

        if state.get("want_scripts"):
            total_weight += 35
            target_s = max(1, len(state.get("script_candidates", [])))
            scripts_progress = (state.get("scripts_saved", 0) + state.get("scripts_skipped", 0)) / target_s
            completed_weight += int(35 * min(1.0, scripts_progress))

        if state.get("want_mp3s") or state.get("want_videos"):
            total_weight += 30
            tot_media = max(1, len(state.get("media_items_map", {})))
            media_progress = (state.get("media_completed_count", 0) + state.get("media_failed_count", 0)) / tot_media
            completed_weight += int(30 * min(1.0, media_progress))

        pct = int((completed_weight / max(1, total_weight)) * 100) if total_weight > 0 else 100
        self.bar_transcripts.setValue(min(100, pct))
        self.lbl_progress_details.setText(f"Overall Progress: {pct}% complete across pipeline")

    def _finalize_phased_pipeline(self):
        state = getattr(self, "_phased_pipeline_state", None) or getattr(self, "_parallel_state", None)
        if not state or state.get("completed"):
            return
        state["completed"] = True

        selected = state.get("selected", [])
        base_dir = state.get("base_dir", Path(self.txt_out_dir.text().strip()))

        # Collect missing scripts as failed items with diagnostics
        if state.get("want_scripts"):
            for cand in state.get("script_candidates", selected):
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
        if state.get("want_thumbnails"):
            thumb_dir = base_dir / "Thumbnails"
            for cand in state.get("thumb_candidates", selected):
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

        audio_cand_len = len(state.get("audio_candidates", []))
        video_cand_len = len(state.get("video_candidates", []))
        audio_done = state.get("audio_completed_count", 0)
        audio_fail = state.get("audio_failed_count", 0)
        video_done = state.get("video_completed_count", 0)
        video_fail = state.get("video_failed_count", 0)

        scripts_cand_len = len(state.get("script_candidates", []))
        scripts_saved = state.get("scripts_saved", 0)
        scripts_skip = state.get("scripts_skipped", 0)

        thumbs_cand_len = len(state.get("thumb_candidates", []))
        thumbs_saved = state.get("thumbs_saved", 0)
        thumbs_skip = state.get("thumbs_skipped", 0)

        titles_cand_len = len(state.get("title_candidates", []))
        titles_saved = state.get("titles_saved", titles_cand_len)

        assets_saved = state.get("assets_saved", 0)

        total_succ = (
            (titles_saved if state.get("want_titles") else 0)
            + (thumbs_saved if state.get("want_thumbnails") else 0)
            + (scripts_saved if state.get("want_scripts") else 0)
            + (audio_done if state.get("want_mp3s") else 0)
            + (video_done if state.get("want_videos") else 0)
            + (assets_saved if state.get("want_channel_assets") else 0)
        )
        total_skip = thumbs_skip + scripts_skip

        v_qual = getattr(self, "combo_video_quality", None)
        v_qual_text = v_qual.currentText() if v_qual else "Best"

        stats = {
            "total_videos": len(selected),
            "total_succeeded": total_succ,
            "total_skipped": total_skip,
            "titles_status": (
                f"{titles_saved} / {titles_cand_len} titles in Titles.txt"
                if state.get("want_titles") else "Not Selected"
            ),
            "thumbnails_status": (
                f"{thumbs_saved} / {thumbs_cand_len} saved" + (f" ({thumbs_skip} existing)" if thumbs_skip > 0 else "")
                if state.get("want_thumbnails") else "Not Selected"
            ),
            "assets_status": (
                f"{assets_saved} saved into Channel Assets"
                if state.get("want_channel_assets") else "Not Selected"
            ),
            "scripts_status": (
                f"{scripts_saved} / {scripts_cand_len} saved (⚡ {state.get('script_concurrency', 3)} concurrent)" + (f" [{scripts_skip} cached]" if scripts_skip > 0 else "")
                if state.get("want_scripts") else "Not Selected"
            ),
            "audio_status": (
                f"{audio_done} / {audio_cand_len} completed" + (f" (❌ {audio_fail} failed)" if audio_fail > 0 else "") + f" (⚡ {state.get('audio_concurrency', 3)} concurrent)"
                if state.get("want_mp3s") else "Not Selected"
            ),
            "video_status": (
                f"{video_done} / {video_cand_len} completed" + (f" (❌ {video_fail} failed)" if video_fail > 0 else "") + f" ({v_qual_text})"
                if state.get("want_videos") else "Not Selected"
            ),
        }

        self._last_stats = stats
        self._last_failed_items = state["failed_items"]

        self.bar_transcripts.setValue(100)
        self.lbl_transcript_status.setText("✅ Phased & Parallel Downloads Finished Successfully!")
        self.lbl_progress_details.setText(f"Completed processing {len(selected)} videos live in real-time. View statistics below.")
        self.btn_view_diagnostics.setVisible(True)

        # Automatically pop up DownloadStatisticsDialog
        self._show_diagnostics_report()

    # Backward compatibility aliases for existing test suites
    def _on_parallel_thumb_item_prog(self, cur, tot, done_c, skip_c, v_lbl, title):
        self._on_phased_thumb_item_prog(cur, tot, done_c, skip_c, v_lbl, title)

    def _on_parallel_thumb_done(self, is_ok, saved_list):
        self._on_phased_thumb_done(is_ok, saved_list)

    def _on_parallel_assets_done(self, saved_list):
        self._on_phased_assets_finished({f"asset_{i}": p for i, p in enumerate(saved_list)} if saved_list else {})

    def _on_parallel_script_item_prog(self, cur, tot, done_c, skip_c, v_lbl, title):
        self._on_phased_script_item_prog(cur, tot, done_c, skip_c, v_lbl, title)

    def _on_parallel_script_done(self, is_ok):
        self._on_phased_script_done(is_ok)

    def _on_parallel_media_completed(self, item):
        self._on_phased_media_completed(item)

    def _on_parallel_media_failed(self, item, err_msg):
        self._on_phased_media_failed(item, err_msg)

    def _on_parallel_media_all_finished(self):
        self._on_phased_media_all_finished()

    def _update_parallel_media_status(self):
        self._update_phased_media_status()

    def _update_parallel_overall_progress(self):
        self._update_phased_overall_progress()

    def _check_parallel_completion(self):
        pass

    def _finalize_parallel_download(self):
        self._finalize_phased_pipeline()


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
                concurrency=self.spin_script_concurrency.value() if hasattr(self, "spin_script_concurrency") else 3,
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


