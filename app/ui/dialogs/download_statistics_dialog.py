"""
Download Statistics & Failure Diagnostics Dialog for MultiDownloader v3.2.0.
Provides comprehensive metrics, category breakdowns, detailed failed item lists, and one-click retry.
"""

import sys
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

import csv
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFrame,
    QApplication,
    QScrollArea,
    QWidget,
    QFileDialog,
    QMessageBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from app.config import APP_VERSION


class DownloadStatisticsDialog(QDialog):
    """
    Comprehensive post-download statistics dialog showing:
    - Overview KPIs (Total, Succeeded, Skipped, Failed)
    - Category-by-category breakdown
    - Failed items inspection table with exact root-cause diagnostics
    - Direct 'Retry Failed' callback button
    """
    retry_requested = Signal(list)  # list of failed item dictionaries

    def __init__(
        self,
        stats: Dict[str, Any],
        failed_items: List[Dict[str, Any]],
        output_dir: Optional[Path] = None,
        on_retry: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.stats = stats
        self.failed_items = failed_items or []
        self.output_dir = Path(output_dir) if output_dir else None
        self.on_retry = on_retry

        self.setWindowTitle("Download Statistics & Health Report")
        self.resize(840, 620)
        self.setMinimumSize(720, 500)

        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        # Header Title
        top_row = QHBoxLayout()
        lbl_title = QLabel("📊 Download Summary & Full Statistics")
        lbl_title.setObjectName("viewTitle")
        lbl_title.setStyleSheet("font-size: 18px; font-weight: 700;")
        top_row.addWidget(lbl_title)
        top_row.addStretch()

        if self.output_dir and self.output_dir.exists():
            btn_folder = QPushButton("📁 Open Folder")
            btn_folder.setToolTip(str(self.output_dir))
            btn_folder.clicked.connect(self._open_output_folder)
            top_row.addWidget(btn_folder)

        root.addLayout(top_row)

        # KPI Cards Row (Total, Succeeded, Skipped, Failed)
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)

        total_count = self.stats.get("total_videos", 0)
        succ_count = self.stats.get("total_succeeded", 0)
        skip_count = self.stats.get("total_skipped", 0)
        fail_count = len(self.failed_items)

        kpi_row.addWidget(self._create_kpi_card("Total Videos", str(total_count), "#007aff"))
        kpi_row.addWidget(self._create_kpi_card("Succeeded", str(succ_count), "#34c759"))
        kpi_row.addWidget(self._create_kpi_card("Skipped (On Disk)", str(skip_count), "#0a84ff"))
        kpi_row.addWidget(self._create_kpi_card("Failed Items", str(fail_count), "#ff3b30" if fail_count > 0 else "#8e8e93"))

        root.addLayout(kpi_row)

        # Category Breakdown Grid
        box_cats = QFrame()
        box_cats.setFrameShape(QFrame.StyledPanel)
        box_cats.setStyleSheet("background-color: rgba(120, 120, 128, 0.08); border-radius: 8px; padding: 10px;")
        grid_cats = QGridLayout(box_cats)
        grid_cats.setSpacing(8)

        categories = [
            ("📝 Titles File", self.stats.get("titles_status", "Not Selected")),
            ("📜 Scripts / CC", self.stats.get("scripts_status", "Not Selected")),
            ("🖼️ Thumbnails", self.stats.get("thumbnails_status", "Not Selected")),
            ("🎵 Audio MP3s", self.stats.get("audio_status", "Not Selected")),
            ("🎬 Video Files", self.stats.get("video_status", "Not Selected")),
            ("🎨 Channel Assets", self.stats.get("assets_status", "Not Selected")),
        ]

        for i, (label, val) in enumerate(categories):
            row = i // 2
            col = (i % 2) * 2
            lbl_k = QLabel(f"<b>{label}:</b>")
            lbl_k.setStyleSheet("font-size: 12px;")
            lbl_v = QLabel(str(val))
            lbl_v.setStyleSheet("font-size: 12px; color: #007aff;" if "Not Selected" not in str(val) else "font-size: 12px; color: #8e8e93;")
            grid_cats.addWidget(lbl_k, row, col)
            grid_cats.addWidget(lbl_v, row, col + 1)

        root.addWidget(box_cats)

        # Failed Items Table or 100% Success Banner
        if fail_count > 0:
            lbl_failed_header = QLabel(f"⚠️ <b>Failed Items Requiring Attention ({fail_count}):</b>")
            lbl_failed_header.setStyleSheet("color: #ff3b30; font-size: 13px; margin-top: 4px;")
            root.addWidget(lbl_failed_header)

            self.table_failed = QTableWidget()
            self.table_failed.setColumnCount(4)
            self.table_failed.setHorizontalHeaderLabels(["Video", "Title", "Category", "Diagnosis & Reason"])
            self.table_failed.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
            self.table_failed.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
            self.table_failed.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
            self.table_failed.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
            self.table_failed.setRowCount(len(self.failed_items))
            self.table_failed.setAlternatingRowColors(True)

            for row, item in enumerate(self.failed_items):
                v_lbl = item.get("version_label", f"V{row + 1}")
                title = item.get("title", "Unknown")
                category = item.get("category", "General")
                reason = item.get("reason", "Unknown error")

                it_v = QTableWidgetItem(v_lbl)
                it_v.setTextAlignment(Qt.AlignCenter)
                it_v.setFont(QFont("sans-serif", 11, QFont.Bold))

                it_t = QTableWidgetItem(title)
                it_t.setToolTip(title)

                it_c = QTableWidgetItem(category)
                it_c.setTextAlignment(Qt.AlignCenter)
                it_c.setForeground(QColor("#ff9500"))

                it_r = QTableWidgetItem(reason)
                it_r.setToolTip(reason)
                it_r.setForeground(QColor("#ff453a"))

                self.table_failed.setItem(row, 0, it_v)
                self.table_failed.setItem(row, 1, it_t)
                self.table_failed.setItem(row, 2, it_c)
                self.table_failed.setItem(row, 3, it_r)

            root.addWidget(self.table_failed)
        else:
            success_frame = QFrame()
            success_frame.setStyleSheet("background-color: rgba(52, 199, 89, 0.12); border: 1px solid #34c759; border-radius: 8px; padding: 14px;")
            succ_layout = QVBoxLayout(success_frame)
            lbl_succ = QLabel("🎉 <b>100% Download Complete!</b>")
            lbl_succ.setStyleSheet("color: #34c759; font-size: 15px; font-weight: 700;")
            lbl_succ_sub = QLabel("All selected assets were retrieved, verified, and saved to disk with zero failures.")
            lbl_succ_sub.setStyleSheet("color: #34c759; font-size: 12px;")
            succ_layout.addWidget(lbl_succ)
            succ_layout.addWidget(lbl_succ_sub)
            root.addWidget(success_frame)

        # Bottom Button Bar
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(10)

        btn_copy = QPushButton("📋 Copy Diagnostic Report")
        btn_copy.clicked.connect(self._copy_diagnostic_report)
        btn_bar.addWidget(btn_copy)

        self.btn_export_csv = QPushButton("📄 Export CSV")
        self.btn_export_csv.setToolTip("Export complete statistics and diagnostic rows as a CSV spreadsheet")
        self.btn_export_csv.clicked.connect(self._export_csv_report)
        btn_bar.addWidget(self.btn_export_csv)

        self.btn_export_txt = QPushButton("📝 Export TXT")
        self.btn_export_txt.setToolTip("Export formatted text report directly to a file")
        self.btn_export_txt.clicked.connect(self._export_txt_report)
        btn_bar.addWidget(self.btn_export_txt)

        btn_bar.addStretch()

        if fail_count > 0:
            self.btn_retry = QPushButton(f"🔄 Retry Failed Downloads ({fail_count})")
            self.btn_retry.setObjectName("primaryBtn")
            self.btn_retry.setStyleSheet("background-color: #ff9500; color: #ffffff; font-weight: 700; padding: 8px 18px; border-radius: 6px;")
            self.btn_retry.clicked.connect(self._on_retry_clicked)
            btn_bar.addWidget(self.btn_retry)

        btn_close = QPushButton("Close")
        btn_close.setObjectName("primaryBtn")
        btn_close.clicked.connect(self.accept)
        btn_bar.addWidget(btn_close)

        root.addLayout(btn_bar)

    def _create_kpi_card(self, title: str, value: str, color: str) -> QFrame:
        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        card.setStyleSheet(
            f"background-color: rgba(120, 120, 128, 0.08); border-left: 4px solid {color}; border-radius: 6px; padding: 8px;"
        )
        l = QVBoxLayout(card)
        l.setContentsMargins(8, 4, 8, 4)
        l.setSpacing(2)

        lbl_val = QLabel(value)
        lbl_val.setStyleSheet(f"font-size: 20px; font-weight: 800; color: {color};")
        lbl_k = QLabel(title)
        lbl_k.setStyleSheet("font-size: 11px; color: #8e8e93; font-weight: 600;")

        l.addWidget(lbl_val)
        l.addWidget(lbl_k)
        return card

    def _open_output_folder(self):
        if not self.output_dir:
            return
        if sys.platform == "darwin":
            subprocess.run(["open", str(self.output_dir)])
        elif sys.platform.startswith("win"):
            subprocess.run(["explorer", str(self.output_dir)])
        else:
            subprocess.run(["xdg-open", str(self.output_dir)])

    def get_report_text(self) -> str:
        report = [
            "==================================================",
            f" MultiDownloader v{APP_VERSION} - Download Statistics Report",
            "==================================================",
            f"Total Videos: {self.stats.get('total_videos', 0)}",
            f"Succeeded: {self.stats.get('total_succeeded', 0)}",
            f"Skipped on Disk: {self.stats.get('total_skipped', 0)}",
            f"Failed: {len(self.failed_items)}",
            "--------------------------------------------------",
            "Category Status:",
            f"• Titles: {self.stats.get('titles_status', 'N/A')}",
            f"• Scripts: {self.stats.get('scripts_status', 'N/A')}",
            f"• Thumbnails: {self.stats.get('thumbnails_status', 'N/A')}",
            f"• Audio: {self.stats.get('audio_status', 'N/A')}",
            f"• Video: {self.stats.get('video_status', 'N/A')}",
            f"• Channel Assets: {self.stats.get('assets_status', 'N/A')}",
            "--------------------------------------------------",
        ]
        if self.failed_items:
            report.append("Failed Items Details:")
            for item in self.failed_items:
                report.append(
                    f"[{item.get('version_label', '')}] {item.get('title', '')} | "
                    f"Category: {item.get('category', '')} | Reason: {item.get('reason', '')}"
                )
        else:
            report.append("All items downloaded cleanly with 0 failures.")
        return "\n".join(report)

    def _copy_diagnostic_report(self):
        report_str = self.get_report_text()
        QApplication.clipboard().setText(report_str)

    def export_csv(self, file_path: Path | str) -> bool:
        """Export statistics and itemized diagnostic data to CSV."""
        try:
            p = Path(file_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Type", "Key / V-Number", "Value / Title", "Category", "Status", "Reason / Diagnostics"])
                # Summary rows
                writer.writerow(["Summary", "Total Videos", self.stats.get("total_videos", 0), "All", "Info", ""])
                writer.writerow(["Summary", "Succeeded", self.stats.get("total_succeeded", 0), "All", "Success", ""])
                writer.writerow(["Summary", "Skipped", self.stats.get("total_skipped", 0), "All", "Skipped", ""])
                writer.writerow(["Summary", "Failed", len(self.failed_items), "All", "Failed", ""])
                for cat in ["titles", "scripts", "thumbnails", "audio", "video", "assets"]:
                    k = f"{cat}_status"
                    writer.writerow(["Category", cat.title(), self.stats.get(k, "N/A"), cat.title(), "Status", ""])
                # Item rows
                if self.failed_items:
                    for item in self.failed_items:
                        writer.writerow([
                            "Failed Item",
                            item.get("version_label", ""),
                            item.get("title", ""),
                            item.get("category", ""),
                            "Failed",
                            item.get("reason", "")
                        ])
                else:
                    writer.writerow(["Result", "All Items", "100% Downloaded Successfully", "All", "Clean", "No errors"])
            return True
        except Exception:
            return False

    def export_txt(self, file_path: Path | str) -> bool:
        """Export formatted text report directly to a file on disk."""
        try:
            p = Path(file_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            text_content = self.get_report_text()
            with open(p, "w", encoding="utf-8") as f:
                f.write(text_content)
            return True
        except Exception:
            return False

    def _export_csv_report(self):
        default_name = str(self.output_dir / "download_statistics.csv") if self.output_dir else "download_statistics.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV Report", default_name, "CSV Files (*.csv)")
        if path:
            if self.export_csv(path):
                QMessageBox.information(self, "Export Successful", f"CSV report exported to:\n{path}")
            else:
                QMessageBox.warning(self, "Export Failed", f"Could not write CSV report to:\n{path}")

    def _export_txt_report(self):
        default_name = str(self.output_dir / "download_statistics.txt") if self.output_dir else "download_statistics.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Export TXT Report", default_name, "Text Files (*.txt)")
        if path:
            if self.export_txt(path):
                QMessageBox.information(self, "Export Successful", f"TXT report exported to:\n{path}")
            else:
                QMessageBox.warning(self, "Export Failed", f"Could not write TXT report to:\n{path}")

    def _on_retry_clicked(self):
        self.retry_requested.emit(self.failed_items)
        if self.on_retry:
            self.on_retry(self.failed_items)
        self.accept()
