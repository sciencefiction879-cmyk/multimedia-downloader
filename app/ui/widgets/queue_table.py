"""
Download queue table widget displaying real-time progress, status, and action buttons.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict
from PySide6.QtWidgets import (
    QWidget,
    QTableWidget,
    QTableWidgetItem,
    QProgressBar,
    QPushButton,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, Signal
from app.models.download_item import DownloadItem, DownloadStatus
from app.utils.logger import logger



class QueueTable(QTableWidget):
    cancel_requested = Signal(str)
    retry_requested = Signal(str)
    resume_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(0, 8, parent)
        self.row_to_id: Dict[int, str] = {}
        self.id_to_row: Dict[str, int] = {}
        self._init_ui()


    def _init_ui(self):
        headers = ["#", "Ver", "Title", "Format", "Size", "Progress", "Status", "Actions"]
        self.setHorizontalHeaderLabels(headers)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.setColumnWidth(0, 45)
        self.setColumnWidth(1, 55)
        self.setColumnWidth(3, 75)
        self.setColumnWidth(4, 120)
        self.setColumnWidth(5, 140)
        self.setColumnWidth(6, 125)
        self.setColumnWidth(7, 120)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QTableWidget.SelectRows)

    def update_or_add_item(self, item: DownloadItem):
        if item.id in self.id_to_row:
            row = self.id_to_row[item.id]
            self._update_row(row, item)
        else:
            row = self.rowCount()
            self.insertRow(row)
            self.id_to_row[item.id] = row
            self.row_to_id[row] = item.id
            self._populate_row(row, item)

    def _populate_row(self, row: int, item: DownloadItem):
        # 0: #
        it_num = QTableWidgetItem(str(item.number))
        it_num.setTextAlignment(Qt.AlignCenter)
        self.setItem(row, 0, it_num)

        # 1: Version (V1, V2...)
        v_text = item.version_label or f"V{item.number}"
        it_ver = QTableWidgetItem(v_text)
        it_ver.setTextAlignment(Qt.AlignCenter)
        it_ver.setForeground(Qt.cyan)
        self.setItem(row, 1, it_ver)

        # 2: Title
        it_title = QTableWidgetItem(item.title)
        self.setItem(row, 2, it_title)

        # 3: Format
        it_fmt = QTableWidgetItem(f"{item.format_ext}")
        it_fmt.setTextAlignment(Qt.AlignCenter)
        self.setItem(row, 3, it_fmt)

        # 4: Size
        it_sz = QTableWidgetItem(item.size_str)
        it_sz.setTextAlignment(Qt.AlignCenter)
        self.setItem(row, 4, it_sz)

        # 5: Progress Bar
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(int(item.progress_percent))
        bar.setFormat(f"{int(item.progress_percent)}%")
        self.setCellWidget(row, 5, bar)

        # 6: Status
        st_text = item.status.value
        if item.status == DownloadStatus.QUEUED:
            st_text = "Queued (Waiting)"
        elif item.status == DownloadStatus.DOWNLOADING:
            st_text = f"Downloading ({int(item.progress_percent)}%)"
        it_st = QTableWidgetItem(st_text)
        it_st.setTextAlignment(Qt.AlignCenter)
        if item.status == DownloadStatus.COMPLETED:
            it_st.setForeground(Qt.green)
        elif item.status == DownloadStatus.FAILED:
            it_st.setForeground(Qt.red)
        elif item.status == DownloadStatus.DOWNLOADING:
            it_st.setForeground(Qt.cyan)
        self.setItem(row, 6, it_st)

        # 7: Action Buttons
        self.setCellWidget(row, 7, self._create_action_widget(item))

    def _update_row(self, row: int, item: DownloadItem):
        # Update progress bar
        bar = self.cellWidget(row, 5)
        if isinstance(bar, QProgressBar):
            bar.setValue(int(item.progress_percent))
            bar.setFormat(f"{int(item.progress_percent)}%")

        # Title
        t_it = self.item(row, 2)
        if t_it and t_it.text() != item.title:
            t_it.setText(item.title)

        # Size
        sz_it = self.item(row, 4)
        if sz_it:
            sz_it.setText(item.size_str)

        # Status
        st_it = self.item(row, 6)
        if st_it:
            st_text = item.status.value
            if item.status == DownloadStatus.QUEUED:
                st_text = "Queued (Waiting)"
            elif item.status == DownloadStatus.DOWNLOADING:
                st_text = f"Downloading ({int(item.progress_percent)}%)"
            st_it.setText(st_text)
            if item.status == DownloadStatus.COMPLETED:
                st_it.setForeground(Qt.green)
            elif item.status == DownloadStatus.FAILED:
                st_it.setForeground(Qt.red)
            elif item.status == DownloadStatus.DOWNLOADING:
                st_it.setForeground(Qt.cyan)
            else:
                st_it.setForeground(QColor("#9d9da8"))

        # Refresh action buttons state
        self.setCellWidget(row, 7, self._create_action_widget(item))


    def _create_action_widget(self, item: DownloadItem) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)

        if item.status == DownloadStatus.COMPLETED:
            btn_folder = QPushButton("📂")
            btn_folder.setToolTip("Open in Finder")
            btn_folder.setFixedWidth(32)
            btn_folder.clicked.connect(lambda: self._open_finder(item))
            lay.addWidget(btn_folder)
        elif item.status in (DownloadStatus.PAUSED, DownloadStatus.FAILED, DownloadStatus.CANCELLED):
            btn_resume = QPushButton("▶")
            btn_resume.setToolTip("Resume Download from where it stopped")
            btn_resume.setStyleSheet("background-color: #34c759; color: #ffffff; font-weight: bold;")
            btn_resume.setFixedWidth(32)
            btn_resume.clicked.connect(lambda: self.resume_requested.emit(item.id))
            lay.addWidget(btn_resume)
        elif item.status in (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.FETCHING_INFO):
            btn_cancel = QPushButton("⏹")
            btn_cancel.setToolTip("Stop & Cancel Download")
            btn_cancel.setFixedWidth(32)
            btn_cancel.clicked.connect(lambda: self.cancel_requested.emit(item.id))
            lay.addWidget(btn_cancel)

        btn_del = QPushButton("✕")
        btn_del.setToolTip("Remove from List")
        btn_del.setFixedWidth(32)
        btn_del.clicked.connect(lambda: self.remove_requested.emit(item.id))
        lay.addWidget(btn_del)

        return w


    def _open_finder(self, item: DownloadItem):
        if item.output_filepath and Path(item.output_filepath).exists():
            target = item.output_filepath
        elif item.custom_output_dir and Path(item.custom_output_dir).exists():
            target = item.custom_output_dir
        else:
            return

        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(target)] if os.path.isfile(target) else ["open", str(target)])
        else:
            os.startfile(str(target))
