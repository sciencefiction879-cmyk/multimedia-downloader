"""
Application entry point for Multi Downloader on macOS.
"""

import sys
import os
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

# Ensure root directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import APP_NAME, APP_VERSION, ORGANIZATION
from app.utils.crash_handler import setup_crash_handler
from app.utils.logger import logger
from app.ui.main_window import MainWindow


def main():
    setup_crash_handler()
    logger.info(f"Starting {APP_NAME} v{APP_VERSION} on {sys.platform}...")

    # High DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(ORGANIZATION)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
