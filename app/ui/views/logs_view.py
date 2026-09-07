"""
Activity Logs and Error Reporting view for real-time diagnostics and 1-click troubleshooting copy.
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QApplication,
    QFileDialog,
)
from PySide6.QtCore import QTimer
from app.config import LOG_FILE


class LogsView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._reload_logs)
        self.timer.start(1000)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        top_row = QHBoxLayout()
        col_lbl = QVBoxLayout()
        lbl = QLabel("Activity Logs & Error Reporting")
        lbl.setStyleSheet("font-size: 22px; font-weight: 700; color: #ffffff;")
        lbl_sub = QLabel("Real-time operational events and error diagnostics. 1-click copy available for troubleshooting.")
        lbl_sub.setStyleSheet("color: #9d9da8;")
        col_lbl.addWidget(lbl)
        col_lbl.addWidget(lbl_sub)
        top_row.addLayout(col_lbl)

        top_row.addStretch()

        self.btn_copy = QPushButton("📋 Copy Logs")
        self.btn_copy.setObjectName("primaryBtn")
        self.btn_copy.setStyleSheet("font-size: 13px; font-weight: 600; padding: 8px 18px;")
        self.btn_copy.clicked.connect(self._copy_logs_clicked)

        btn_export = QPushButton("💾 Export to File")
        btn_export.clicked.connect(self._export_logs)

        btn_clear = QPushButton("✕ Clear Logs")
        btn_clear.clicked.connect(self._clear_logs)

        top_row.addWidget(self.btn_copy)
        top_row.addWidget(btn_export)
        top_row.addWidget(btn_clear)
        layout.addLayout(top_row)

        self.txt_logs = QPlainTextEdit()
        self.txt_logs.setReadOnly(True)
        self.txt_logs.setStyleSheet(
            "background-color: #141417; font-family: monospace; font-size: 12px; line-height: 1.4; color: #a0a0b0; border: 1px solid #2e2e38; border-radius: 8px; padding: 10px;"
        )
        layout.addWidget(self.txt_logs)

        self._reload_logs()

    def _copy_logs_clicked(self):
        content = self.txt_logs.toPlainText()
        if not content and LOG_FILE.exists():
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                pass
        QApplication.clipboard().setText(content or "No logs recorded yet.")
        self.btn_copy.setText("✓ Logs Copied!")
        QTimer.singleShot(1500, lambda: self.btn_copy.setText("📋 Copy Logs"))

    def _clear_logs(self):
        self.txt_logs.clear()
        if LOG_FILE.exists():
            try:
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    f.write("")
            except Exception:
                pass

    def _reload_logs(self):
        if LOG_FILE.exists():
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    last_lines = "".join(lines[-400:])
                    if self.txt_logs.toPlainText() != last_lines:
                        self.txt_logs.setPlainText(last_lines)
                        self.txt_logs.verticalScrollBar().setValue(self.txt_logs.verticalScrollBar().maximum())
            except Exception:
                pass

    def _export_logs(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Log File", "activity_logs.log", "Log Files (*.log);;Text Files (*.txt)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.txt_logs.toPlainText())

