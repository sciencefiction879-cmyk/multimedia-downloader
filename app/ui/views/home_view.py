"""
Home view for direct single URL and batch URL media downloads.
"""

import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, List

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
    QFileDialog,
    QMessageBox,
    QScrollArea,
    QFrame,
    QApplication,
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, Signal, QThread, QTimer

from app.config import (
    AUDIO_FORMATS,
    AUDIO_QUALITIES,
    AUDIO_SAMPLE_RATES,
    AUDIO_CHANNELS,
    VIDEO_QUALITIES,
    VIDEO_FORMATS,
    VIDEO_FPS_OPTIONS,
    VIDEO_CODECS,
)
from app.downloader.ffmpeg_engine import FFmpegEngine
from app.downloader.queue_manager import QueueManager
from app.downloader.ytdlp_client import YtdlpClient
from app.models.download_item import DownloadItem
from app.models.settings_model import Settings
from app.ui.dialogs.playlist_dialog import PlaylistDialog
from app.utils.logger import logger


class MetadataWorker(QThread):
    finished_metadata = Signal(dict)
    error_metadata = Signal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        try:
            info = YtdlpClient.fetch_metadata(self.url)
            self.finished_metadata.emit(info)
        except Exception as e:
            self.error_metadata.emit(str(e))


class ThumbnailLoader(QThread):
    loaded_thumbnail = Signal(bytes)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        try:
            req = urllib.request.Request(self.url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                self.loaded_thumbnail.emit(resp.read())
        except Exception:
            pass


class HomeView(QWidget):
    switch_to_downloads_requested = Signal()

    def __init__(self, queue_manager: QueueManager, settings: Settings, parent=None):
        super().__init__(parent)
        self.queue_manager = queue_manager
        self.settings = settings
        self.current_metadata: Optional[Dict[str, Any]] = None
        self.meta_worker: Optional[MetadataWorker] = None
        self.thumb_worker: Optional[ThumbnailLoader] = None

        self._url_timer = QTimer(self)
        self._url_timer.setSingleShot(True)
        self._url_timer.setInterval(600)
        self._url_timer.timeout.connect(self._fetch_metadata_async)

        self._init_ui()


    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # Header
        lbl_title = QLabel("Media Downloader")
        lbl_title.setStyleSheet("font-size: 22px; font-weight: 700; color: #ffffff;")
        lbl_sub = QLabel("Paste any YouTube, Playlist, or Direct URL to fetch and convert high quality MP3 audio / video.")
        lbl_sub.setStyleSheet("color: #9d9da8; margin-bottom: 4px;")
        layout.addWidget(lbl_title)
        layout.addWidget(lbl_sub)

        # URL Input Card
        box_url = QGroupBox("Video or Playlist Link")
        url_layout = QVBoxLayout(box_url)
        url_layout.setSpacing(10)

        row_input = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://www.youtube.com/watch?v=... or playlist link")
        self.url_input.textChanged.connect(self._on_url_changed)
        row_input.addWidget(self.url_input)

        btn_paste = QPushButton("📋 Paste")
        btn_paste.clicked.connect(self._paste_clipboard)
        btn_import = QPushButton("📁 Import TXT")
        btn_import.clicked.connect(self._import_txt)
        btn_clear = QPushButton("✕ Clear")
        btn_clear.clicked.connect(self._clear_input)

        row_input.addWidget(btn_paste)
        row_input.addWidget(btn_import)
        row_input.addWidget(btn_clear)
        url_layout.addLayout(row_input)

        self.lbl_meta_status = QLabel("")
        self.lbl_meta_status.setStyleSheet("color: #007aff; font-size: 11px;")
        url_layout.addWidget(self.lbl_meta_status)

        layout.addWidget(box_url)

        # Metadata Preview Card
        self.box_preview = QGroupBox("Video Information Preview")
        prev_layout = QHBoxLayout(self.box_preview)

        self.lbl_thumb = QLabel("No Preview")
        self.lbl_thumb.setFixedSize(140, 80)
        self.lbl_thumb.setAlignment(Qt.AlignCenter)
        self.lbl_thumb.setStyleSheet("background-color: #1a1a20; border-radius: 6px; color: #666;")
        prev_layout.addWidget(self.lbl_thumb)

        info_col = QVBoxLayout()
        self.lbl_meta_title = QLabel("Title: --")
        self.lbl_meta_title.setWordWrap(True)
        self.lbl_meta_title.setStyleSheet("font-weight: 600; font-size: 13px; color: #ffffff;")
        self.lbl_meta_channel = QLabel("Channel: --")
        self.lbl_meta_channel.setStyleSheet("color: #9d9da8;")

        meta_row = QHBoxLayout()
        self.lbl_meta_duration = QLabel("Duration: --:--")
        self.lbl_meta_date = QLabel("Date: --")
        self.lbl_meta_est_size = QLabel("Est. MP3 Size: --")
        self.lbl_meta_est_size.setStyleSheet("font-weight: 600; color: #34c759;")

        meta_row.addWidget(self.lbl_meta_duration)
        meta_row.addWidget(self.lbl_meta_date)
        meta_row.addWidget(self.lbl_meta_est_size)
        meta_row.addStretch()

        info_col.addWidget(self.lbl_meta_title)
        info_col.addWidget(self.lbl_meta_channel)
        info_col.addLayout(meta_row)
        prev_layout.addLayout(info_col)

        layout.addWidget(self.box_preview)

        # Options Card
        box_opt = QGroupBox("Output & Format Settings")
        opt_layout = QGridLayout(box_opt)
        opt_layout.setSpacing(12)

        opt_layout.addWidget(QLabel("Media Type:"), 0, 0)
        self.combo_type = QComboBox()
        self.combo_type.addItems(["Audio", "Video"])
        self.combo_type.currentTextChanged.connect(self._on_type_changed)
        opt_layout.addWidget(self.combo_type, 0, 1)

        # Audio options
        self.lbl_audio_fmt = QLabel("Audio Format:")
        self.combo_audio_fmt = QComboBox()
        self.combo_audio_fmt.addItems(AUDIO_FORMATS)
        self.combo_audio_fmt.setCurrentText(self.settings.default_audio_format)
        opt_layout.addWidget(self.lbl_audio_fmt, 0, 2)
        opt_layout.addWidget(self.combo_audio_fmt, 0, 3)

        self.lbl_audio_q = QLabel("Audio Quality:")
        self.combo_audio_q = QComboBox()
        self.combo_audio_q.addItems(AUDIO_QUALITIES)
        self.combo_audio_q.setCurrentText(self.settings.default_audio_quality)
        self.combo_audio_q.currentTextChanged.connect(self._update_est_size)
        opt_layout.addWidget(self.lbl_audio_q, 1, 0)
        opt_layout.addWidget(self.combo_audio_q, 1, 1)

        self.lbl_samplerate = QLabel("Sample Rate:")
        self.combo_samplerate = QComboBox()
        self.combo_samplerate.addItems(AUDIO_SAMPLE_RATES)
        opt_layout.addWidget(self.lbl_samplerate, 1, 2)
        opt_layout.addWidget(self.combo_samplerate, 1, 3)

        layout.addWidget(box_opt)

        # Output Folder Card
        box_dir = QGroupBox("Save Location")
        dir_layout = QHBoxLayout(box_dir)
        self.txt_out_dir = QLineEdit(self.settings.audio_dir)
        self.txt_out_dir.setReadOnly(True)
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_dir)
        dir_layout.addWidget(self.txt_out_dir)
        dir_layout.addWidget(btn_browse)
        layout.addWidget(box_dir)

        # Action button
        layout.addSpacing(8)
        self.btn_download = QPushButton("⚡ Download Now")
        self.btn_download.setObjectName("primaryBtn")
        self.btn_download.setMinimumHeight(44)
        self.btn_download.setStyleSheet("font-size: 14px; font-weight: 700;")
        self.btn_download.clicked.connect(self._on_download_clicked)
        layout.addWidget(self.btn_download)

        layout.addStretch()
        scroll_area.setWidget(content)
        root_layout.addWidget(scroll_area)

    def _on_type_changed(self, new_type: str):
        is_audio = new_type == "Audio"
        self.txt_out_dir.setText(self.settings.audio_dir if is_audio else self.settings.video_dir)

    def _paste_clipboard(self):
        t = QApplication.clipboard().text().strip()
        if t:
            self.url_input.setText(t)

    def _clear_input(self):
        self.url_input.clear()
        self.current_metadata = None
        self.lbl_meta_title.setText("Title: --")
        self.lbl_meta_channel.setText("Channel: --")
        self.lbl_meta_duration.setText("Duration: --:--")
        self.lbl_meta_date.setText("Date: --")
        self.lbl_meta_est_size.setText("Est. MP3 Size: --")
        self.lbl_thumb.setText("No Preview")
        self.lbl_meta_status.setText("")

    def _on_url_changed(self, text: str):
        url = text.strip()
        if len(url) < 10 or not (url.startswith("http://") or url.startswith("https://")):
            return

        self.lbl_meta_status.setText("Fetching metadata...")
        if self.meta_worker and self.meta_worker.isRunning():
            self.meta_worker.terminate()

        self.meta_worker = MetadataWorker(url)
        self.meta_worker.finished_metadata.connect(self._on_meta_loaded)
        self.meta_worker.error_metadata.connect(lambda err: self.lbl_meta_status.setText(f"Info: {err[:80]}"))
        self.meta_worker.start()

    def _on_meta_loaded(self, info: dict):
        self.current_metadata = info
        self.lbl_meta_status.setText("Metadata ready.")
        self.lbl_meta_title.setText(f"Title: {info.get('title', 'Untitled')}")
        self.lbl_meta_channel.setText(f"Channel: {info.get('uploader') or info.get('channel') or 'YouTube'}")

        dur = float(info.get("duration") or 0.0)
        m, s = divmod(int(dur), 60)
        h, m = divmod(m, 60)
        self.lbl_meta_duration.setText(f"Duration: {h:02d}:{m:02d}:{s:02d}" if h > 0 else f"Duration: {m:02d}:{s:02d}")
        self.lbl_meta_date.setText(f"Date: {info.get('upload_date', '--')}")
        self._update_est_size()

        thumb_url = info.get("thumbnail")
        if thumb_url:
            self.thumb_worker = ThumbnailLoader(thumb_url)
            self.thumb_worker.loaded_thumbnail.connect(self._on_thumb_loaded)
            self.thumb_worker.start()

    def _on_thumb_loaded(self, img_bytes: bytes):
        pixmap = QPixmap()
        pixmap.loadFromData(img_bytes)
        if not pixmap.isNull():
            self.lbl_thumb.setPixmap(pixmap.scaled(self.lbl_thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _update_est_size(self):
        if not self.current_metadata:
            return
        dur = float(self.current_metadata.get("duration") or 0.0)
        if dur > 0:
            bitrate = FFmpegEngine.extract_audio_bitrate_kbps(self.combo_audio_q.currentText())
            est_mb = DownloadItem.estimate_audio_size_mb(dur, bitrate)
            self.lbl_meta_est_size.setText(f"Est. MP3 Size: ~{est_mb:.1f} MB")

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.txt_out_dir.text())
        if d:
            self.txt_out_dir.setText(d)

    def _import_txt(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Text File with URLs", "", "Text Files (*.txt);;All Files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                urls = [line.strip() for line in f if line.strip().startswith("http")]
            if not urls:
                QMessageBox.warning(self, "Empty File", "No valid URLs found in file.")
                return

            items = []
            for u in urls:
                item = DownloadItem(
                    url=u,
                    media_type=self.combo_type.currentText(),
                    quality=self.combo_audio_q.currentText(),
                    format_ext=self.combo_audio_fmt.currentText(),
                    custom_output_dir=self.txt_out_dir.text(),
                )
                items.append(item)

            self.queue_manager.add_items(items)
            self.queue_manager.start()
            QMessageBox.information(self, "Import Successful", f"Added {len(items)} URLs to the download queue!")
            self.switch_to_downloads_requested.emit()
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))

    def _on_download_clicked(self):
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "URL Missing", "Please enter a valid video or playlist link.")
            return

        # Check if playlist
        if YtdlpClient.is_playlist_url(url):
            try:
                entries = YtdlpClient.extract_playlist_entries(url)
                if entries:
                    dlg = PlaylistDialog(entries, self)
                    if dlg.exec():
                        items = []
                        for e in dlg.selected_entries:
                            e_url = e.get("url") or (f"https://www.youtube.com/watch?v={e.get('id')}" if e.get("id") else url)
                            items.append(
                                DownloadItem(
                                    url=e_url,
                                    media_type=self.combo_type.currentText(),
                                    quality=self.combo_audio_q.currentText(),
                                    format_ext=self.combo_audio_fmt.currentText(),
                                    custom_output_dir=self.txt_out_dir.text(),
                                    title=e.get("title", "Untitled"),
                                    duration_sec=float(e.get("duration") or 0.0),
                                )
                            )
                        self.queue_manager.add_items(items)
                        self.queue_manager.start()
                        self.switch_to_downloads_requested.emit()
                        return
            except Exception as e:
                logger.warning(f"Playlist check error: {e}")

        # Single item download
        item = DownloadItem(
            url=url,
            media_type=self.combo_type.currentText(),
            quality=self.combo_audio_q.currentText(),
            format_ext=self.combo_audio_fmt.currentText(),
            custom_output_dir=self.txt_out_dir.text(),
        )
        if self.current_metadata:
            item.title = self.current_metadata.get("title", "Untitled")
            item.channel = self.current_metadata.get("uploader") or self.current_metadata.get("channel") or ""
            item.duration_sec = float(self.current_metadata.get("duration") or 0.0)
            item.video_id = self.current_metadata.get("id", "")
            item.upload_date = self.current_metadata.get("upload_date", "")

        self.queue_manager.add_item(item)
        self.queue_manager.start()
        self.switch_to_downloads_requested.emit()
