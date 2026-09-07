"""
Application settings view for directories, concurrency, and default formats.
"""

from pathlib import Path
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QSpinBox,
    QCheckBox,
    QGroupBox,
    QFileDialog,
    QMessageBox,
    QScrollArea,
    QFrame,
)
from app.config import (
    AUDIO_FORMATS,
    AUDIO_QUALITIES,
    VIDEO_FORMATS,
    VIDEO_QUALITIES,
    LOG_LEVELS,
    MAX_CONCURRENT_DOWNLOADS,
    MIN_CONCURRENT_DOWNLOADS,
)
from app.models.settings_model import Settings
from app.downloader.speed_optimizer import SpeedOptimizer
from app.utils.logger import logger


class SettingsView(QWidget):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._init_ui()

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # Header
        lbl_title = QLabel("Settings & Preferences")
        lbl_title.setStyleSheet("font-size: 22px; font-weight: 700; color: #ffffff;")
        layout.addWidget(lbl_title)

        # Download Directories
        box_dirs = QGroupBox("Default Download Locations")
        d_layout = QGridLayout(box_dirs)
        d_layout.setSpacing(10)

        d_layout.addWidget(QLabel("Audio Folder:"), 0, 0)
        self.txt_audio_dir = QLineEdit(self.settings.audio_dir)
        d_layout.addWidget(self.txt_audio_dir, 0, 1)
        btn_a = QPushButton("Browse...")
        btn_a.clicked.connect(lambda: self._browse_dir(self.txt_audio_dir))
        d_layout.addWidget(btn_a, 0, 2)

        d_layout.addWidget(QLabel("Channels Folder:"), 1, 0)
        self.txt_chan_dir = QLineEdit(self.settings.channels_dir)
        d_layout.addWidget(self.txt_chan_dir, 1, 1)
        btn_c = QPushButton("Browse...")
        btn_c.clicked.connect(lambda: self._browse_dir(self.txt_chan_dir))
        d_layout.addWidget(btn_c, 1, 2)

        layout.addWidget(box_dirs)

        # Concurrency & Performance
        box_perf = QGroupBox("Performance & Concurrency")
        p_layout = QGridLayout(box_perf)
        p_layout.setSpacing(10)

        p_layout.addWidget(QLabel("Concurrent Downloads:"), 0, 0)
        self.spin_concurrent = QSpinBox()
        self.spin_concurrent.setRange(MIN_CONCURRENT_DOWNLOADS, MAX_CONCURRENT_DOWNLOADS)
        self.spin_concurrent.setValue(self.settings.concurrent_downloads)
        p_layout.addWidget(self.spin_concurrent, 0, 1)

        p_layout.addWidget(QLabel("Max Retries:"), 0, 2)
        self.spin_retries = QSpinBox()
        self.spin_retries.setRange(0, 10)
        self.spin_retries.setValue(self.settings.max_retries)
        p_layout.addWidget(self.spin_retries, 0, 3)

        self.chk_gpu = QCheckBox("Enable Hardware / GPU Acceleration (Apple VideoToolbox)")
        self.chk_gpu.setChecked(self.settings.gpu_acceleration)
        p_layout.addWidget(self.chk_gpu, 1, 0, 1, 4)

        layout.addWidget(box_perf)

        # Default Audio/Video Presets
        box_def = QGroupBox("Default Media Presets")
        def_layout = QGridLayout(box_def)
        def_layout.setSpacing(10)

        def_layout.addWidget(QLabel("Default Audio Format:"), 0, 0)
        self.combo_audio_fmt = QComboBox()
        self.combo_audio_fmt.addItems(AUDIO_FORMATS)
        self.combo_audio_fmt.setCurrentText(self.settings.default_audio_format)
        def_layout.addWidget(self.combo_audio_fmt, 0, 1)

        def_layout.addWidget(QLabel("Default Audio Quality:"), 0, 2)
        self.combo_audio_q = QComboBox()
        self.combo_audio_q.addItems(AUDIO_QUALITIES)
        self.combo_audio_q.setCurrentText(self.settings.default_audio_quality)
        def_layout.addWidget(self.combo_audio_q, 0, 3)

        def_layout.addWidget(QLabel("Theme:"), 1, 0)
        self.combo_theme = QComboBox()
        self.combo_theme.addItems(["Dark", "Light"])
        self.combo_theme.setCurrentText(self.settings.theme.capitalize())
        def_layout.addWidget(self.combo_theme, 1, 1)

        def_layout.addWidget(QLabel("Log Level:"), 1, 2)
        self.combo_log = QComboBox()
        self.combo_log.addItems(LOG_LEVELS)
        self.combo_log.setCurrentText(self.settings.log_level)
        def_layout.addWidget(self.combo_log, 1, 3)

        layout.addWidget(box_def)

        # Speed Limit Bypass & Bandwidth Acceleration
        box_speed = QGroupBox("⚡ Speed Limit Bypass & Bandwidth Acceleration")
        s_layout = QVBoxLayout(box_speed)
        s_layout.setSpacing(10)

        # 1. JS Runtime n-challenge solver
        js_info = SpeedOptimizer.get_js_runtime()
        self.chk_bypass_throttling = QCheckBox("Bypass YouTube Speed Throttling (Node.js / Deno JS Engine)")
        self.chk_bypass_throttling.setChecked(getattr(self.settings, "bypass_throttling", True))
        s_layout.addWidget(self.chk_bypass_throttling)

        lbl_js_status = QLabel(
            f"🟢 <b>Status:</b> Connected to {js_info[0]} ({js_info[1]}) — eliminates YouTube 50 KB/s rate limit"
            if js_info else
            "⚠️ <b>Status:</b> No JS runtime found. Run <code>brew install node</code> in Terminal to bypass throttling."
        )
        lbl_js_status.setStyleSheet("color: #7ce38b; font-size: 11px;" if js_info else "color: #e3a97c; font-size: 11px;")
        s_layout.addWidget(lbl_js_status)

        # 2. aria2c 16-connection multi-part downloader
        aria2c_path = SpeedOptimizer.get_aria2c_path()
        self.chk_use_aria2c = QCheckBox("Enable 16-Connection Parallel Chunking (aria2c Multi-Stream Engine)")
        self.chk_use_aria2c.setChecked(getattr(self.settings, "use_aria2c", True))
        s_layout.addWidget(self.chk_use_aria2c)

        lbl_aria_status = QLabel(
            f"🟢 <b>Status:</b> Found aria2 at {aria2c_path} — 16 parallel split connections active"
            if aria2c_path else
            "⚪ <b>Status:</b> Using native 16-fragment streaming. Optional: Run <code>brew install aria2</code> for maximum speed."
        )
        lbl_aria_status.setStyleSheet("color: #7ce38b; font-size: 11px;" if aria2c_path else "color: #9d9da8; font-size: 11px;")
        s_layout.addWidget(lbl_aria_status)

        # 3. Force IPv4 routing
        self.chk_force_ipv4 = QCheckBox("Force IPv4 CDN Edge Routing (Bypasses congested/throttled ISP IPv6 transit)")
        self.chk_force_ipv4.setChecked(getattr(self.settings, "force_ipv4", False))
        s_layout.addWidget(self.chk_force_ipv4)

        # 4. PO Token
        po_row = QHBoxLayout()
        po_row.addWidget(QLabel("YouTube PO Token (Optional):"))
        self.txt_po_token = QLineEdit(getattr(self.settings, "po_token", ""))
        self.txt_po_token.setPlaceholderText("Optional Proof-of-Origin token for YouTube (leave blank for auto)")
        po_row.addWidget(self.txt_po_token)
        s_layout.addLayout(po_row)

        layout.addWidget(box_speed)

        # Browser Cookie Acceleration & Authentication
        box_cookies = QGroupBox("🍪 Browser Cookie Acceleration & Authentication")
        c_layout = QGridLayout(box_cookies)
        c_layout.setSpacing(10)

        self.chk_cookies = QCheckBox("Enable Browser Cookie Authentication (Bypasses YouTube rate limits & restrictions)")
        self.chk_cookies.setChecked(self.settings.use_browser_cookies)
        c_layout.addWidget(self.chk_cookies, 0, 0, 1, 3)

        c_layout.addWidget(QLabel("Browser Source:"), 1, 0)
        self.combo_cookie_browser = QComboBox()
        self.combo_cookie_browser.addItems(["Chrome", "Safari", "Brave", "Firefox", "Edge", "Opera", "Vivaldi", "Custom cookies.txt File"])
        
        # Match current
        cur_b = self.settings.cookie_browser.lower()
        mapping = {"chrome": "Chrome", "safari": "Safari", "brave": "Brave", "firefox": "Firefox", "edge": "Edge", "opera": "Opera", "vivaldi": "Vivaldi", "custom_file": "Custom cookies.txt File"}
        self.combo_cookie_browser.setCurrentText(mapping.get(cur_b, "Chrome"))
        c_layout.addWidget(self.combo_cookie_browser, 1, 1, 1, 2)

        c_layout.addWidget(QLabel("Custom Cookie File:"), 2, 0)
        self.txt_custom_cookie = QLineEdit(self.settings.custom_cookie_file)
        self.txt_custom_cookie.setPlaceholderText("/path/to/cookies.txt (optional)")
        c_layout.addWidget(self.txt_custom_cookie, 2, 1)
        btn_cookie_browse = QPushButton("Browse...")
        btn_cookie_browse.clicked.connect(self._browse_cookie_file)
        c_layout.addWidget(btn_cookie_browse, 2, 2)

        lbl_cookie_info = QLabel("<i>Tip: Using cookies from your browser allows YouTube to serve verified unthrottled gigabit downloads.</i>")
        lbl_cookie_info.setStyleSheet("color: #9d9da8; font-size: 11px;")
        c_layout.addWidget(lbl_cookie_info, 3, 0, 1, 3)

        layout.addWidget(box_cookies)

        # Save Button
        btn_save = QPushButton("💾 Save Settings")
        btn_save.setObjectName("primaryBtn")
        btn_save.setMinimumHeight(40)
        btn_save.clicked.connect(self._save_settings)
        layout.addWidget(btn_save)


        layout.addStretch()
        scroll.setWidget(content)
        root_layout.addWidget(scroll)

    def _browse_dir(self, line_edit: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, "Select Directory", line_edit.text())
        if d:
            line_edit.setText(d)

    def _browse_cookie_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select cookies.txt File", self.txt_custom_cookie.text(), "Text Files (*.txt);;All Files (*)")
        if f:
            self.txt_custom_cookie.setText(f)

    def _save_settings(self):
        self.settings.audio_dir = self.txt_audio_dir.text().strip()
        self.settings.channels_dir = self.txt_chan_dir.text().strip()
        self.settings.concurrent_downloads = self.spin_concurrent.value()
        self.settings.max_retries = self.spin_retries.value()
        self.settings.gpu_acceleration = self.chk_gpu.isChecked()
        self.settings.default_audio_format = self.combo_audio_fmt.currentText()
        self.settings.default_audio_quality = self.combo_audio_q.currentText()
        self.settings.theme = self.combo_theme.currentText().lower()
        self.settings.log_level = self.combo_log.currentText()

        # Speed settings
        self.settings.bypass_throttling = self.chk_bypass_throttling.isChecked()
        self.settings.use_aria2c = self.chk_use_aria2c.isChecked()
        self.settings.force_ipv4 = self.chk_force_ipv4.isChecked()
        self.settings.po_token = self.txt_po_token.text().strip()

        # Cookie settings
        self.settings.use_browser_cookies = self.chk_cookies.isChecked()
        reverse_map = {
            "Chrome": "chrome",
            "Safari": "safari",
            "Brave": "brave",
            "Firefox": "firefox",
            "Edge": "edge",
            "Opera": "opera",
            "Vivaldi": "vivaldi",
            "Custom cookies.txt File": "custom_file",
        }
        self.settings.cookie_browser = reverse_map.get(self.combo_cookie_browser.currentText(), "chrome")
        self.settings.custom_cookie_file = self.txt_custom_cookie.text().strip()

        self.settings.save()
        QMessageBox.information(self, "Settings Saved", "Your preferences and cookie settings have been saved successfully!")

