"""
Downloads queue view with queue controls, progress table, and history clearing.
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QMessageBox,
)
from app.downloader.queue_manager import QueueManager
from app.ui.widgets.queue_table import QueueTable
from app.models.download_item import DownloadItem


class DownloadsView(QWidget):
    def __init__(self, queue_manager: QueueManager, parent=None):
        super().__init__(parent)
        self.queue_manager = queue_manager
        self._init_ui()
        self._connect_signals()
        self._load_initial_items()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Header & Actions row
        top_row = QHBoxLayout()

        col_titles = QVBoxLayout()
        lbl_title = QLabel("Downloads & Active Queue")
        lbl_title.setStyleSheet("font-size: 22px; font-weight: 700; color: #ffffff;")
        self.lbl_queue_count = QLabel("0 active items in queue")
        self.lbl_queue_count.setStyleSheet("color: #9d9da8;")
        col_titles.addWidget(lbl_title)
        col_titles.addWidget(self.lbl_queue_count)
        top_row.addLayout(col_titles)

        top_row.addStretch()

        btn_pause_all = QPushButton("⏸ Pause All")
        btn_pause_all.clicked.connect(self.queue_manager.pause)

        btn_resume_all = QPushButton("▶ Resume All")
        btn_resume_all.setObjectName("primaryBtn")
        btn_resume_all.setToolTip("Resume all interrupted or queued downloads from partial progress")
        btn_resume_all.clicked.connect(self.queue_manager.resume_all)

        btn_retry_failed = QPushButton("🔄 Retry Failed")
        btn_retry_failed.clicked.connect(self._retry_failed)

        btn_clear_comp = QPushButton("🧹 Clear Finished")
        btn_clear_comp.clicked.connect(self._clear_completed)

        btn_clear_all = QPushButton("✕ Clear All")
        btn_clear_all.clicked.connect(self._clear_all)

        btn_cancel_all = QPushButton("⏹ Cancel All")
        btn_cancel_all.setObjectName("dangerBtn")
        btn_cancel_all.clicked.connect(self._cancel_all)

        top_row.addWidget(btn_resume_all)
        top_row.addWidget(btn_pause_all)
        top_row.addWidget(btn_retry_failed)
        top_row.addWidget(btn_clear_comp)
        top_row.addWidget(btn_clear_all)
        top_row.addWidget(btn_cancel_all)

        layout.addLayout(top_row)

        # Table
        self.table = QueueTable()
        self.table.cancel_requested.connect(self.queue_manager.cancel_item)
        self.table.retry_requested.connect(self.queue_manager.retry_item)
        self.table.resume_requested.connect(self.queue_manager.resume_item)
        self.table.remove_requested.connect(self.queue_manager.remove_item)
        layout.addWidget(self.table)

    def _load_initial_items(self):
        for it in self.queue_manager.items:
            self.table.update_or_add_item(it)
        if self.queue_manager.items:
            paused = sum(1 for it in self.queue_manager.items if it.status.value in ("Paused", "Failed", "Cancelled"))
            self.lbl_queue_count.setText(f"{len(self.queue_manager.items)} saved tasks in queue ({paused} ready to resume)")

    def _connect_signals(self):
        self.queue_manager.item_added.connect(self._on_item_updated)
        self.queue_manager.item_updated.connect(self._on_item_updated)
        self.queue_manager.item_completed.connect(self._on_item_updated)
        self.queue_manager.item_failed.connect(lambda item, err: self._on_item_updated(item))


    def _on_item_updated(self, item: DownloadItem):
        self.table.update_or_add_item(item)
        downloading = sum(1 for it in self.queue_manager.items if it.status.value in ("Downloading", "Converting"))
        queued = sum(1 for it in self.queue_manager.items if it.status.value in ("Queued", "Fetching Info"))
        completed = sum(1 for it in self.queue_manager.items if it.status.value == "Completed")
        self.lbl_queue_count.setText(f"{downloading} downloading (max 3 parallel), {queued} in queue, {completed} completed")

    def _retry_failed(self):
        retried = self.queue_manager.retry_all_failed()
        if not retried:
            QMessageBox.information(self, "No Failed Tasks", "There are no failed download tasks to retry.")

    def _clear_completed(self):
        self.queue_manager.clear_completed()
        self.table.setRowCount(0)
        self.table.id_to_row.clear()
        self.table.row_to_id.clear()
        for it in self.queue_manager.items:
            self.table.update_or_add_item(it)

    def _clear_all(self):
        if not self.queue_manager.items:
            return
        if QMessageBox.question(self, "Clear All Queue", "Are you sure you want to clear the entire downloads queue?") == QMessageBox.Yes:
            self.queue_manager.cancel_all()
            self.queue_manager.items.clear()
            self.table.setRowCount(0)
            self.table.id_to_row.clear()
            self.table.row_to_id.clear()
            self.lbl_queue_count.setText("0 active items in queue")

    def _cancel_all(self):
        """Immediately stops all active downloads and cancels all queued tasks."""
        self.queue_manager.cancel_all()
        for it in self.queue_manager.items:
            self.table.update_or_add_item(it)
        completed = sum(1 for it in self.queue_manager.items if it.status.value == "Completed")
        self.lbl_queue_count.setText(f"Stopped all processes. 0 downloading, 0 in queue, {completed} completed")


