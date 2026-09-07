"""
Modern macOS dark & light stylesheet design system for Multi Downloader.
"""

DARK_THEME = """
QWidget {
    background-color: #1e1e24;
    color: #f0f0f5;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 13px;
}

QMainWindow, QDialog {
    background-color: #18181c;
}

QScrollArea {
    background-color: transparent;
    border: none;
}

QFrame[frameShape="4"], QFrame[frameShape="5"] { /* HLine, VLine */
    border: none;
    background-color: #2e2e38;
}

/* Sidebar */
#Sidebar {
    background-color: #141417;
    border-right: 1px solid #282830;
}

#Sidebar QPushButton {
    background-color: transparent;
    color: #9d9da8;
    border: none;
    border-radius: 8px;
    padding: 10px 14px;
    text-align: left;
    font-weight: 500;
    font-size: 13px;
}

#Sidebar QPushButton:hover {
    background-color: #22222a;
    color: #ffffff;
}

#Sidebar QPushButton:checked {
    background-color: #007aff;
    color: #ffffff;
    font-weight: 600;
}

/* Cards & Groups */
QGroupBox {
    background-color: #24242c;
    border: 1px solid #32323c;
    border-radius: 10px;
    margin-top: 14px;
    padding: 14px;
    font-weight: 600;
    font-size: 13px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
    color: #007aff;
}

/* Inputs */
QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #1c1c22;
    border: 1px solid #3a3a46;
    border-radius: 8px;
    padding: 8px 12px;
    color: #ffffff;
    selection-background-color: #007aff;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border: 1.5px solid #007aff;
    background-color: #202028;
}

/* Buttons */
QPushButton {
    background-color: #2d2d38;
    color: #ffffff;
    border: 1px solid #3e3e4c;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #383846;
    border-color: #4f4f60;
}

QPushButton:pressed {
    background-color: #22222a;
}

QPushButton:disabled {
    background-color: #1c1c22;
    color: #555562;
    border-color: #282830;
}

/* Primary Action Buttons */
QPushButton#primaryBtn, QPushButton[primary="true"] {
    background-color: #007aff;
    border: 1px solid #0062cc;
    color: #ffffff;
    font-weight: 600;
}

QPushButton#primaryBtn:hover, QPushButton[primary="true"]:hover {
    background-color: #006be6;
}

QPushButton#successBtn, QPushButton[success="true"] {
    background-color: #34c759;
    border: 1px solid #28a745;
    color: #ffffff;
    font-weight: 600;
}

QPushButton#successBtn:hover, QPushButton[success="true"]:hover {
    background-color: #2eb350;
}

QPushButton#dangerBtn, QPushButton[danger="true"] {
    background-color: #ff3b30;
    border: 1px solid #dc3545;
    color: #ffffff;
    font-weight: 600;
}

QPushButton#dangerBtn:hover, QPushButton[danger="true"]:hover {
    background-color: #e63329;
}

/* Combo Box */
QComboBox {
    background-color: #1c1c22;
    border: 1px solid #3a3a46;
    border-radius: 8px;
    padding: 7px 12px;
    color: #ffffff;
    min-width: 120px;
}

QComboBox:hover {
    border-color: #007aff;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
}

QComboBox QAbstractItemView {
    background-color: #24242c;
    border: 1px solid #3a3a46;
    border-radius: 8px;
    selection-background-color: #007aff;
    selection-color: #ffffff;
    padding: 4px;
}

/* Tables */
QTableWidget, QTableView {
    background-color: #202028;
    border: 1px solid #32323c;
    border-radius: 10px;
    gridline-color: #2b2b36;
    selection-background-color: #007aff;
    selection-color: #ffffff;
}

QHeaderView::section {
    background-color: #18181f;
    color: #9d9da8;
    border: none;
    border-bottom: 1px solid #32323c;
    padding: 8px 10px;
    font-weight: 600;
    font-size: 12px;
}

/* Progress Bar */
QProgressBar {
    background-color: #1a1a20;
    border: 1px solid #2e2e38;
    border-radius: 6px;
    text-align: center;
    color: #ffffff;
    font-size: 11px;
    font-weight: 600;
    height: 14px;
}

QProgressBar::chunk {
    background-color: #007aff;
    border-radius: 5px;
}

/* Checkbox */
QCheckBox {
    spacing: 8px;
    color: #f0f0f5;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1.5px solid #4a4a58;
    background-color: #1c1c22;
}

QCheckBox::indicator:checked {
    background-color: #007aff;
    border-color: #007aff;
}

/* Status Bar */
QStatusBar {
    background-color: #141417;
    border-top: 1px solid #24242c;
    color: #888894;
    font-size: 12px;
}
"""

LIGHT_THEME = DARK_THEME.replace("#1e1e24", "#f5f5f7").replace("#18181c", "#ffffff").replace("#24242c", "#ffffff").replace("#1c1c22", "#f0f0f2").replace("#f0f0f5", "#1d1d1f").replace("#ffffff", "#000000").replace("#32323c", "#e5e5ea").replace("#3a3a46", "#d1d1d6")


def get_stylesheet(theme: str = "dark") -> str:
    return DARK_THEME if theme.lower() == "dark" else LIGHT_THEME
