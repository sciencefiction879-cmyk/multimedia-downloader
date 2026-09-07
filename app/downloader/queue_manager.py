import json
from pathlib import Path
from typing import List, Dict, Optional
from PySide6.QtCore import QObject, Signal
from app.config import QUEUE_FILE
from app.models.download_item import DownloadItem, DownloadStatus
from app.models.history_item import HistoryManager, HistoryItem
from app.models.settings_model import Settings
from app.downloader.worker import DownloadWorker
from app.utils.logger import logger


class QueueManager(QObject):
    item_added = Signal(object)  # DownloadItem
    item_updated = Signal(object)  # DownloadItem
    item_completed = Signal(object)  # DownloadItem
    item_failed = Signal(object, str)  # DownloadItem, error
    all_finished = Signal()

    def __init__(self, settings: Optional[Settings] = None, parent=None):
        super().__init__(parent)
        self.settings = settings or Settings.load()
        self.history_mgr = HistoryManager()
        self.items: List[DownloadItem] = []
        self.active_workers: Dict[str, DownloadWorker] = {}
        self.is_running = False
        self.load_queue()

    def save_queue(self):
        """Persists unfinished or interrupted items so they can be resumed."""
        try:
            QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
            unfinished = [
                it.to_dict()
                for it in self.items
                if it.status != DownloadStatus.COMPLETED
            ]
            with open(QUEUE_FILE, "w", encoding="utf-8") as f:
                json.dump(unfinished, f, indent=2)
        except Exception as e:
            logger.debug(f"Failed to save queue: {e}")

    def load_queue(self):
        """Loads previously interrupted or queued downloads on startup."""
        if not QUEUE_FILE.exists():
            return
        try:
            with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded = []
            for item_dict in data:
                it = DownloadItem.from_dict(item_dict)
                # If was in-flight, mark as Paused / Interrupted ready for Resume
                if it.status in (DownloadStatus.DOWNLOADING, DownloadStatus.CONVERTING, DownloadStatus.FETCHING_INFO):
                    it.status = DownloadStatus.PAUSED
                it.number = len(loaded) + 1
                loaded.append(it)
            self.items = loaded
            logger.info(f"Loaded {len(loaded)} pending/interrupted downloads from saved queue.")
        except Exception as e:
            logger.debug(f"Failed to load queue: {e}")

    def add_item(self, item: DownloadItem):
        item.number = len(self.items) + 1
        self.items.append(item)
        self.item_added.emit(item)
        self.save_queue()
        if self.is_running:
            self._process_queue()

    def add_items(self, new_items: List[DownloadItem]):
        for item in new_items:
            item.number = len(self.items) + 1
            self.items.append(item)
            self.item_added.emit(item)
        self.save_queue()
        if self.is_running:
            self._process_queue()

    def start(self):
        self.is_running = True
        self._process_queue()

    def pause(self):
        self.is_running = False
        for it in self.items:
            if it.status == DownloadStatus.DOWNLOADING:
                it.status = DownloadStatus.PAUSED
                it.is_paused = True
                self.item_updated.emit(it)
        for worker in list(self.active_workers.values()):
            try:
                worker.cancel()
            except Exception:
                pass
        self.active_workers.clear()
        self.save_queue()

    def resume_all(self):
        """Resumes all interrupted, paused, or failed downloads from their saved partial byte positions."""
        for item in self.items:
            if item.status in (DownloadStatus.PAUSED, DownloadStatus.FAILED, DownloadStatus.CANCELLED, DownloadStatus.QUEUED):
                item.status = DownloadStatus.QUEUED
                item.is_cancelled = False
                item.is_paused = False
                item.error_message = ""
                self.item_updated.emit(item)
        self.start()
        self.save_queue()

    def resume_item(self, item_id: str):
        """Resumes a specific interrupted download from its partial progress."""
        for item in self.items:
            if item.id == item_id:
                item.status = DownloadStatus.QUEUED
                item.is_cancelled = False
                item.is_paused = False
                item.error_message = ""
                self.item_updated.emit(item)
                break
        self.start()
        self.save_queue()

    def cancel_all(self):
        """Immediately stops all active downloads and cancels all remaining queued tasks."""
        self.is_running = False
        for worker in list(self.active_workers.values()):
            try:
                worker.cancel()
                if worker.isRunning():
                    worker.terminate()
            except Exception as e:
                logger.debug(f"Worker termination error: {e}")
        self.active_workers.clear()

        for item in self.items:
            if item.status in (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.FETCHING_INFO, DownloadStatus.CONVERTING, DownloadStatus.PAUSED):
                item.status = DownloadStatus.CANCELLED
                item.is_cancelled = True
                self.item_updated.emit(item)
        self.save_queue()

    def cancel_item(self, item_id: str):
        """Cancels all active downloads and remaining queued tasks immediately."""
        self.cancel_all()

    def retry_item(self, item_id: str):
        self.resume_item(item_id)

    def retry_all_failed(self) -> bool:
        """Batch-retries all failed download tasks at once and resumes queue processing."""
        failed_found = False
        for item in self.items:
            if item.status == DownloadStatus.FAILED:
                item.status = DownloadStatus.QUEUED
                item.is_cancelled = False
                item.is_paused = False
                item.error_message = ""
                item.retries_count = 0
                self.item_updated.emit(item)
                failed_found = True
        if failed_found:
            self.start()
            self.save_queue()
        return failed_found

    def remove_item(self, item_id: str):
        if item_id in self.active_workers:
            try:
                self.active_workers[item_id].cancel()
                self.active_workers[item_id].terminate()
            except Exception:
                pass
            del self.active_workers[item_id]

        self.items = [item for item in self.items if item.id != item_id]
        for idx, it in enumerate(self.items, start=1):
            it.number = idx
            self.item_updated.emit(it)
        self.save_queue()
        self._process_queue()

    def clear_completed(self):
        self.items = [it for it in self.items if it.status != DownloadStatus.COMPLETED]
        for idx, it in enumerate(self.items, start=1):
            it.number = idx
            self.item_updated.emit(it)
        self.save_queue()

    def _process_queue(self):
        if not self.is_running:
            return

        max_concurrent = max(1, self.settings.concurrent_downloads)
        active_count = len(self.active_workers)

        if active_count >= max_concurrent:
            return

        for item in self.items:
            if active_count >= max_concurrent:
                break
            if item.status == DownloadStatus.QUEUED and item.id not in self.active_workers:
                self._launch_worker(item)
                active_count += 1

        if active_count == 0 and not any(it.status == DownloadStatus.QUEUED for it in self.items):
            self.all_finished.emit()

    def _launch_worker(self, item: DownloadItem):
        worker = DownloadWorker(item, settings=self.settings)
        self.active_workers[item.id] = worker


        worker.progress_signal.connect(self._on_worker_progress)
        worker.status_signal.connect(self._on_worker_status)
        worker.finished_signal.connect(self._on_worker_finished)
        worker.error_signal.connect(self._on_worker_error)

        worker.start()
        self.item_updated.emit(item)

    def _on_worker_progress(self, item: DownloadItem):
        self.item_updated.emit(item)

    def _on_worker_status(self, item: DownloadItem, msg: str):
        self.item_updated.emit(item)

    def _on_worker_finished(self, item: DownloadItem):
        if item.id in self.active_workers:
            del self.active_workers[item.id]

        # Save to history
        try:
            h = HistoryItem(
                id=item.id,
                title=item.title,
                url=item.url,
                media_type=item.media_type,
                format_ext=item.format_ext,
                file_path=item.output_filepath or "",
                file_size_bytes=item.total_bytes or item.downloaded_bytes,
                duration_sec=item.duration_sec,
                completed_at=item.completed_at.isoformat() if item.completed_at else "",
                channel=item.channel,
                version_label=item.version_label or "",
            )
            self.history_mgr.add_item(h)
        except Exception as e:
            logger.debug(f"Could not write history item: {e}")

        self.item_completed.emit(item)
        self.save_queue()
        self._process_queue()

    def _on_worker_error(self, item: DownloadItem, error_msg: str):
        if item.id in self.active_workers:
            del self.active_workers[item.id]

        self.item_failed.emit(item, error_msg)
        self.save_queue()
        self._process_queue()

