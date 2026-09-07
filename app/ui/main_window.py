"""
Main application window with minimal sidebar navigation, stacked views, and modern macOS styling.
"""

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QStackedWidget,
    QLabel,
    QButtonGroup,
)
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtCore import Qt

from app.config import APP_NAME, APP_VERSION, APP_ICON_PNG
from app.downloader.queue_manager import QueueManager
from app.models.settings_model import Settings
from app.ui.styles import get_stylesheet
from app.ui.views.channel_view import ChannelView
from app.ui.views.downloads_view import DownloadsView
from app.ui.views.settings_view import SettingsView
from app.ui.views.logs_view import LogsView


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self.queue_manager = QueueManager(self.settings)

        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1120, 760)
        self.setMinimumSize(920, 620)

        if APP_ICON_PNG.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_PNG)))

        self._init_ui()
        self._apply_theme()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Left Sidebar
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(220)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(14, 18, 14, 18)
        side_layout.setSpacing(8)

        # App branding
        brand_row = QHBoxLayout()
        lbl_logo = QLabel()
        if APP_ICON_PNG.exists():
            pix = QPixmap(str(APP_ICON_PNG)).scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            lbl_logo.setPixmap(pix)
        brand_row.addWidget(lbl_logo)

        lbl_app_name = QLabel(APP_NAME)
        lbl_app_name.setStyleSheet("font-size: 16px; font-weight: 700; color: #ffffff;")
        brand_row.addWidget(lbl_app_name)
        brand_row.addStretch()
        side_layout.addLayout(brand_row)

        side_layout.addSpacing(16)

        # Nav Buttons
        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)

        self.btn_channel = self._create_nav_button("📺  Channel & Media", 0)
        self.btn_downloads = self._create_nav_button("⬇  Downloads Queue", 1)
        self.btn_logs = self._create_nav_button("📋  Activity Logs", 2)
        self.btn_settings = self._create_nav_button("⚙  Settings", 3)

        side_layout.addWidget(self.btn_channel)
        side_layout.addWidget(self.btn_downloads)
        side_layout.addWidget(self.btn_logs)
        side_layout.addWidget(self.btn_settings)

        side_layout.addStretch()

        # Version label
        lbl_ver = QLabel(f"Version {APP_VERSION}")
        lbl_ver.setStyleSheet("color: #666675; font-size: 11px; text-align: center;")
        lbl_ver.setAlignment(Qt.AlignCenter)
        side_layout.addWidget(lbl_ver)

        root_layout.addWidget(sidebar)

        # 2. Main Stacked Area
        main_col = QWidget()
        col_layout = QVBoxLayout(main_col)
        col_layout.setContentsMargins(0, 0, 0, 0)
        col_layout.setSpacing(0)

        self.stacked_widget = QStackedWidget()

        self.channel_view = ChannelView(self.queue_manager, self.settings)
        self.downloads_view = DownloadsView(self.queue_manager)
        self.logs_view = LogsView()
        self.settings_view = SettingsView(self.settings)

        self.stacked_widget.addWidget(self.channel_view)
        self.stacked_widget.addWidget(self.downloads_view)
        self.stacked_widget.addWidget(self.logs_view)
        self.stacked_widget.addWidget(self.settings_view)

        # Connect view switch signals
        self.channel_view.switch_to_downloads_requested.connect(lambda: self._set_active_page(1))

        col_layout.addWidget(self.stacked_widget)
        root_layout.addWidget(main_col)

        # Select first tab by default
        self.btn_channel.setChecked(True)

    def _create_nav_button(self, text: str, index: int) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.clicked.connect(lambda: self._set_active_page(index))
        self.btn_group.addButton(btn, index)
        return btn

    def _set_active_page(self, index: int):
        self.stacked_widget.setCurrentIndex(index)
        btn = self.btn_group.button(index)
        if btn:
            btn.setChecked(True)

    def _apply_theme(self):
        self.setStyleSheet(get_stylesheet(self.settings.theme))

    def closeEvent(self, event):
        self.queue_manager.cancel_all()
        for worker in list(self.queue_manager.active_workers.values()):
            if worker.isRunning():
                worker.wait(1000)
        event.accept()

