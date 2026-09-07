"""
Playlist video selection modal dialog.
"""

from typing import List, Dict, Any
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QHeaderView,
    QCheckBox,
)
from PySide6.QtCore import Qt


class PlaylistDialog(QDialog):
    def __init__(self, entries: List[Dict[str, Any]], parent=None):
        super().__init__(parent)
        self.entries = entries
        self.selected_entries: List[Dict[str, Any]] = []
        self.setWindowTitle(f"Select Playlist Videos ({len(entries)} items)")
        self.resize(750, 480)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        lbl = QLabel("Select the videos you want to add to the download queue:")
        lbl.setStyleSheet("font-weight: 600; margin-bottom: 8px;")
        layout.addWidget(lbl)

        # Top select all / deselect all
        row_sel = QHBoxLayout()
        btn_all = QPushButton("Select All")
        btn_all.clicked.connect(self._select_all)
        btn_none = QPushButton("Deselect All")
        btn_none.clicked.connect(self._deselect_all)
        row_sel.addWidget(btn_all)
        row_sel.addWidget(btn_none)
        row_sel.addStretch()
        layout.addLayout(row_sel)

        self.table = QTableWidget(len(self.entries), 3)
        self.table.setHorizontalHeaderLabels(["Select", "Title", "Duration"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(2, 100)

        for row, item in enumerate(self.entries):
            # Checkbox
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked)
            self.table.setItem(row, 0, chk)

            # Title
            title = item.get("title", "Untitled")
            t_item = QTableWidgetItem(title)
            t_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 1, t_item)

            # Duration
            dur = float(item.get("duration") or 0.0)
            m, s = divmod(int(dur), 60)
            d_item = QTableWidgetItem(f"{m:02d}:{s:02d}" if dur > 0 else "--:--")
            d_item.setFlags(Qt.ItemIsEnabled)
            d_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 2, d_item)

        layout.addWidget(self.table)

        # Bottom buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_add = QPushButton("Add Selected to Queue")
        btn_add.setObjectName("primaryBtn")
        btn_add.clicked.connect(self._on_accept)
        btn_box.addWidget(btn_add)

        layout.addLayout(btn_box)

    def _select_all(self):
        for row in range(self.table.rowCount()):
            it = self.table.item(row, 0)
            if it:
                it.setCheckState(Qt.Checked)

    def _deselect_all(self):
        for row in range(self.table.rowCount()):
            it = self.table.item(row, 0)
            if it:
                it.setCheckState(Qt.Unchecked)

    def _on_accept(self):
        self.selected_entries = []
        for row in range(self.table.rowCount()):
            it = self.table.item(row, 0)
            if it and it.checkState() == Qt.Checked:
                self.selected_entries.append(self.entries[row])
        self.accept()
