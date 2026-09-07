"""
Detailed error report dialog with copy to clipboard and error details view.
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QPushButton,
    QApplication,
)
from PySide6.QtCore import Qt


class ErrorDialog(QDialog):
    def __init__(self, title: str, message: str, details: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title or "Error")
        self.setMinimumSize(520, 320)
        self._init_ui(message, details)

    def _init_ui(self, message: str, details: str):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        lbl_msg = QLabel(message)
        lbl_msg.setWordWrap(True)
        lbl_msg.setStyleSheet("font-weight: 600; font-size: 14px; color: #ff453a;")
        layout.addWidget(lbl_msg)

        if details:
            self.txt_details = QTextEdit()
            self.txt_details.setPlainText(details)
            self.txt_details.setReadOnly(True)
            self.txt_details.setStyleSheet("background-color: #1a1a20; font-family: monospace; font-size: 11px;")
            layout.addWidget(self.txt_details)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        if details:
            btn_copy = QPushButton("Copy Error Details")
            btn_copy.clicked.connect(self._copy_details)
            btn_box.addWidget(btn_copy)

        btn_ok = QPushButton("OK")
        btn_ok.setObjectName("primaryBtn")
        btn_ok.clicked.connect(self.accept)
        btn_box.addWidget(btn_ok)

        layout.addLayout(btn_box)

    def _copy_details(self):
        if hasattr(self, "txt_details"):
            QApplication.clipboard().setText(self.txt_details.toPlainText())
