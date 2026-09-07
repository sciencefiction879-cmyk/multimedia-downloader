"""
Real-time system resource and download throughput performance bar.
"""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QProgressBar
from PySide6.QtCore import QTimer, Qt
from app.core.performance import PerformanceMonitor


class PerformanceBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.monitor = PerformanceMonitor()
        self._init_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_metrics)
        self.timer.start(1000)

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(16)

        # Download Speed
        self.lbl_speed = QLabel("Speed: 0 KB/s")
        self.lbl_speed.setStyleSheet("font-weight: 600; color: #34c759;")
        layout.addWidget(self.lbl_speed)

        # CPU
        self.lbl_cpu = QLabel("CPU: 0%")
        layout.addWidget(self.lbl_cpu)
        self.bar_cpu = QProgressBar()
        self.bar_cpu.setRange(0, 100)
        self.bar_cpu.setFixedWidth(80)
        self.bar_cpu.setTextVisible(False)
        layout.addWidget(self.bar_cpu)

        # Memory
        self.lbl_mem = QLabel("RAM: 0%")
        layout.addWidget(self.lbl_mem)
        self.bar_mem = QProgressBar()
        self.bar_mem.setRange(0, 100)
        self.bar_mem.setFixedWidth(80)
        self.bar_mem.setTextVisible(False)
        layout.addWidget(self.bar_mem)

        layout.addStretch()

    def _update_metrics(self):
        snap = self.monitor.get_snapshot()
        self.lbl_speed.setText(f"Speed: {snap['network_speed_str']}")
        self.lbl_cpu.setText(f"CPU: {snap['cpu_percent']:.0f}%")
        self.bar_cpu.setValue(int(snap["cpu_percent"]))

        self.lbl_mem.setText(f"RAM: {snap['memory_percent']:.0f}% ({snap['memory_used_gb']}/{snap['memory_total_gb']}GB)")
        self.bar_mem.setValue(int(snap["memory_percent"]))
