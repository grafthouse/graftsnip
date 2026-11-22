"""Dark theme + small shared widgets (floating pills, toast, rounded menus)."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QLabel, QMenu

ACCENT = "#00BFDB"     # GraftSnip cyan, sampled from the logo's S ribbon
ON_ACCENT = "#03161B"  # text on the cyan (white on it fails contrast)

QSS = """
QWidget { font-family: "Segoe UI"; font-size: 9.5pt; }
#Pill { background: rgba(34,34,37,244); border: 1px solid rgba(255,255,255,26); border-radius: 12px; }
#Pill QToolButton { background: transparent; border: none; border-radius: 7px; color: #ececee; }
#Pill QToolButton:hover { background: rgba(255,255,255,26); }
#Pill QToolButton:pressed { background: rgba(255,255,255,44); }
#Pill QToolButton:checked { background: #f2f2f4; color: #111113; }
#Pill QToolButton::menu-indicator { image: none; width: 0px; }
#Pill QToolButton#Wide { padding: 0px 9px; font-weight: 600; }
#Pill QToolButton#Copy { background: rgba(255,255,255,30); border-radius: 9px; padding: 0px 12px 0px 9px; font-weight: 600; }
#Pill QToolButton#Copy:hover { background: rgba(255,255,255,48); }
#Pill QToolButton#Done { background: #f2f2f4; color: #111113; border-radius: 9px; padding: 0px 14px 0px 10px; font-weight: 600; }
#Pill QToolButton#Done:hover { background: #ffffff; }
#Pill QLabel { color: #b4b4ba; padding: 0px 8px; }
#Sep { background: rgba(255,255,255,30); }
#Popover { background: rgba(38,38,42,252); border: 1px solid rgba(255,255,255,30); border-radius: 12px; }
#Popover QLabel#Head { color: #8e8e94; font-size: 8.5pt; font-weight: 600; }
#Popover QPushButton { background: rgba(255,255,255,18); color: #ececee; border: none; border-radius: 6px; padding: 5px 10px; }
#Popover QPushButton:hover { background: rgba(255,255,255,34); }
#Popover QPushButton:checked { background: #f2f2f4; color: #111113; font-weight: 600; }
#Popover QCheckBox { color: #ececee; spacing: 8px; }
#Toast { background: rgba(22,22,24,238); color: #f4f4f6; border: 1px solid rgba(255,255,255,30); border-radius: 10px; padding: 8px 14px; font-weight: 600; }
QMenu { background: rgba(40,40,44,252); color: #ececee; border: 1px solid rgba(255,255,255,32); border-radius: 10px; padding: 6px; }
QMenu::item { padding: 6px 28px 6px 12px; border-radius: 6px; }
QMenu::item:selected { background: rgba(0,191,219,64); color: #ffffff; }
QMenu::item:disabled { color: #6b6b70; }
QMenu::separator { height: 1px; background: rgba(255,255,255,28); margin: 5px 8px; }
QToolTip { background: #2b2b2f; color: #ececee; border: 1px solid rgba(255,255,255,40); padding: 5px 8px; }
QDialog { background: #1f1f22; }
QDialog QLabel { color: #ececee; }
QLabel#Muted { color: #9a9aa0; }
QLineEdit, QKeySequenceEdit { background: #151517; border: 1px solid rgba(255,255,255,40); border-radius: 6px; padding: 5px 8px; color: #ececee; }
QPushButton { background: #2c2c30; color: #ececee; border: 1px solid rgba(255,255,255,30); border-radius: 6px; padding: 6px 14px; }
QPushButton:hover { background: #36363b; }
QPushButton#Primary { background: #00BFDB; border: none; color: #03161B; font-weight: 600; }
QPushButton#Primary:hover { background: #2BD1E9; }
#BrandRule { background: rgba(255,255,255,26); }
QLabel#Version { color: #8e8e94; font-size: 8.5pt; }
QCheckBox { color: #ececee; spacing: 8px; }
QMessageBox { background: #1f1f22; }
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: "#1f1f22", QPalette.ColorRole.WindowText: "#ececee",
        QPalette.ColorRole.Base: "#151517", QPalette.ColorRole.AlternateBase: "#232326",
        QPalette.ColorRole.Text: "#ececee", QPalette.ColorRole.Button: "#2a2a2e",
        QPalette.ColorRole.ButtonText: "#ececee", QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: ON_ACCENT, QPalette.ColorRole.ToolTipBase: "#2b2b2f",
        QPalette.ColorRole.ToolTipText: "#ececee", QPalette.ColorRole.PlaceholderText: "#8a8a90",
        QPalette.ColorRole.Link: ACCENT,
    }
    for role, col in roles.items():
        pal.setColor(role, QColor(col))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor("#6b6b70"))
    app.setPalette(pal)
    app.setStyleSheet(QSS)


def make_menu(parent=None) -> QMenu:
    m = QMenu(parent)
    m.setWindowFlags(m.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
    m.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    return m


class Pill(QFrame):
    """Floating rounded toolbar surface with a soft shadow."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Pill")
        fx = QGraphicsDropShadowEffect(self)
        fx.setBlurRadius(28)
        fx.setOffset(0, 6)
        fx.setColor(QColor(0, 0, 0, 150))
        self.setGraphicsEffect(fx)


def separator(parent=None) -> QFrame:
    s = QFrame(parent)
    s.setObjectName("Sep")
    s.setFixedSize(1, 20)
    return s


class Toast(QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self.hide()
        self.bottom_offset = 70

    def show_message(self, text: str, ms: int = 1800) -> None:
        self.setText(text)
        self.adjustSize()
        par = self.parentWidget()
        self.move((par.width() - self.width()) // 2, par.height() - self.height() - self.bottom_offset)
        self.raise_()
        self.show()
        self._anim.stop()
        self._anim.setDuration(140)
        self._anim.setStartValue(self._fx.opacity() if self.isVisible() else 0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(ms)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setDuration(380)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.start()
