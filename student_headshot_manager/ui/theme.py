"""Neutral workstation theme, using colors supported by Qt stylesheets."""

from PySide6.QtGui import QColor, QFont, QPalette


def apply_theme(app):
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    palette = QPalette()
    for role, color in {
        QPalette.Window: "#252525", QPalette.WindowText: "#eeeeee",
        QPalette.Base: "#202020", QPalette.AlternateBase: "#292929",
        QPalette.Text: "#eeeeee", QPalette.Button: "#363636",
        QPalette.ButtonText: "#eeeeee", QPalette.Highlight: "#555555",
        QPalette.HighlightedText: "#ffffff", QPalette.ToolTipBase: "#eeeeee",
        QPalette.ToolTipText: "#202020", QPalette.PlaceholderText: "#b5b5b5",
    }.items():
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet("""
        QMainWindow, QDialog { background: #252525; }
        QLabel { background: transparent; }
        QLabel#title { font-size: 25px; font-weight: 600; }
        QLabel#section { font-size: 17px; font-weight: 600; }
        QLabel#muted { color: #bdbdbd; }
        QLabel#error { color: #ffb4ab; }
        QFrame#sidebar { background: #1c1c1c; border: none; }
        QPushButton { min-height: 24px; padding: 6px 12px; border: 1px solid #606060; border-radius: 4px; background: #363636; }
        QPushButton:hover { background: #454545; }
        QPushButton:pressed { background: #555555; }
        QPushButton:focus, QLineEdit:focus, QComboBox:focus, QTableWidget:focus, QListWidget:focus { border: 1px solid #eeeeee; }
        QPushButton:disabled { color: #909090; border-color: #454545; background: #2b2b2b; }
        QPushButton#primary { background: #dedede; color: #202020; border-color: #dedede; font-weight: 600; }
        QPushButton#primary:hover { background: #ffffff; }
        QPushButton#primary:disabled { background: #555555; color: #aaaaaa; border-color: #555555; }
        QPushButton#nav { text-align: left; border: 1px solid transparent; padding: 10px 14px; background: transparent; }
        QPushButton#nav:checked { background: #3c3c3c; border-color: #606060; }
        QPushButton#nav:hover { background: #303030; }
        QPushButton#nav:focus { border-color: #eeeeee; }
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { min-height: 26px; padding: 4px 8px; background: #202020; border: 1px solid #606060; border-radius: 3px; }
        QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #eeeeee; }
        QComboBox QAbstractItemView { background: #252525; selection-background-color: #555555; }
        QTableWidget, QListWidget { background: #202020; border: 1px solid #4b4b4b; gridline-color: #383838; selection-background-color: #484848; }
        QListWidget::item { padding: 10px 8px; }
        QHeaderView::section { background: #303030; color: #d9d9d9; border: none; border-bottom: 1px solid #555555; padding: 10px; font-weight: 600; }
        QStatusBar { color: #bdbdbd; background: #1c1c1c; }
        QSplitter::handle { background: #393939; }
    """)
