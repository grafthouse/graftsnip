"""GraftSnip application: tray icon, global hotkey, single-instance IPC and window management."""
from __future__ import annotations

import getpass
import logging
import os
import sys
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication, QImage, QImageReader, QKeySequence
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from . import icons, winapi
from .capture import CaptureSession
from .settings import APP_NAME, Settings, __version__, config_dir
from .thumbs import ThumbDock
from .ui import Pill, apply_theme, make_menu

SERVER_NAME = f"GraftSnip-{getpass.getuser()}"
log = logging.getLogger("graftsnip")


def _launch_command() -> str:
    # Installed build (PyInstaller): GraftSnip.exe is the program itself.
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    script = Path(__file__).resolve().parent.parent / "GraftSnip.pyw"
    return f'"{pythonw if pythonw.exists() else exe}" "{script}" --tray'


class Countdown(QWidget):
    """Little "3… 2… 1…" pill for delayed snips; hidden just before the screen is grabbed."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.pill = Pill(self)
        lay = QHBoxLayout(self.pill)
        lay.setContentsMargins(16, 8, 16, 8)
        self.label = QLabel()
        self.label.setStyleSheet("color:#ffffff;font-size:12pt;font-weight:600;")
        lay.addWidget(self.label)

    def show_text(self, text: str) -> None:
        self.label.setText(text)
        self.pill.adjustSize()
        self.resize(self.pill.width() + 40, self.pill.height() + 40)
        self.pill.move(20, 14)
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g = scr.geometry()
        self.move(g.x() + (g.width() - self.width()) // 2, g.y() + 24)
        self.show()


def brand_header(subtitle: str = "") -> QWidget:
    """GraftSnip lockup on the left, version (and an optional subtitle) on the right, then a hairline."""
    box = QWidget()
    outer = QVBoxLayout(box)
    outer.setContentsMargins(0, 0, 0, 4)
    outer.setSpacing(12)
    row = QHBoxLayout()
    row.setSpacing(12)
    logo = QLabel()
    screen = QGuiApplication.primaryScreen()
    logo.setPixmap(icons.logo_pixmap(44, max(1.0, screen.devicePixelRatio() if screen else 1.0)))
    logo.setAccessibleName(APP_NAME)
    row.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addStretch(1)
    meta = QLabel(f"{subtitle}<br>Version {__version__}" if subtitle else f"Version {__version__}")
    meta.setObjectName("Version")
    meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    row.addWidget(meta, 0, Qt.AlignmentFlag.AlignVCenter)
    outer.addLayout(row)
    rule = QWidget()
    rule.setObjectName("BrandRule")
    rule.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    rule.setFixedHeight(1)
    outer.addWidget(rule)
    return box


class AboutDialog(QDialog):
    def __init__(self, ctrl: "Controller"):
        super().__init__(None)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setWindowIcon(icons.app_icon())
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(10)
        lay.addWidget(brand_header())
        blurb = QLabel("Snip any part of the screen, point at it with an arrow and say what's wrong — "
                       "then copy it straight into a chat, ticket or e-mail.")
        blurb.setWordWrap(True)
        lay.addWidget(blurb)
        hk = ctrl.settings["hotkey"] if ctrl.hotkey.ok else "not set"
        facts = QLabel(f"Snip shortcut: <b>{hk}</b><br>Screenshots: {ctrl.settings['save_dir']}")
        facts.setObjectName("Muted")
        facts.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(facts)
        lay.addSpacing(6)
        close = QPushButton("Close")
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def showEvent(self, e):
        super().showEvent(e)
        winapi.set_dark_titlebar(int(self.winId()))


class SettingsDialog(QDialog):
    def __init__(self, ctrl: "Controller"):
        super().__init__(None)
        self.ctrl = ctrl
        s = ctrl.settings
        self.setWindowTitle("GraftSnip settings")
        self.setWindowIcon(icons.app_icon())
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(brand_header("Settings"))

        lay.addWidget(QLabel("<b>Snip shortcut</b> (works from any app)"))
        row = QHBoxLayout()
        self.keys = QKeySequenceEdit(QKeySequence(s["hotkey"]))
        try:
            self.keys.setMaximumSequenceLength(1)
        except AttributeError:
            pass
        self.keys.editingFinished.connect(self._hotkey_changed)
        row.addWidget(self.keys, 1)
        reset = QPushButton("Default")
        reset.clicked.connect(lambda: (self.keys.setKeySequence(QKeySequence("Ctrl+Shift+S")), self._hotkey_changed()))
        row.addWidget(reset)
        lay.addLayout(row)
        self.status = QLabel()
        self.status.setObjectName("Muted")
        lay.addWidget(self.status)
        self._show_status()

        lay.addSpacing(6)
        lay.addWidget(QLabel("<b>Save folder</b>"))
        row = QHBoxLayout()
        self.folder = QLineEdit(s["save_dir"])
        self.folder.editingFinished.connect(lambda: s.set("save_dir", self.folder.text().strip() or s["save_dir"]))
        row.addWidget(self.folder, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        lay.addLayout(row)

        lay.addSpacing(6)
        for key, text in (("copy_on_capture", "Copy every snip to the clipboard straight away"),
                          ("thumbnails", "Keep each snip in the bottom-left corner until I drag it into an app"),
                          ("editor_on_capture", "Open the editor after each snip (off: click the thumbnail)"),
                          ("copy_on_done", "Done copies the annotated image"),
                          ("save_on_done", "Done also saves a PNG to the save folder"),
                          ("magnifier", "Show the pixel magnifier while snipping"),
                          ("shadows", "Soft drop shadows on arrows, boxes and labels")):
            cb = QCheckBox(text)
            cb.setChecked(bool(s[key]))
            cb.toggled.connect(lambda v, k=key: s.set(k, bool(v)))
            lay.addWidget(cb)
        auto = QCheckBox("Start GraftSnip when I sign in to Windows")
        auto.setChecked(winapi.get_autostart(APP_NAME))
        auto.toggled.connect(lambda v: winapi.set_autostart(APP_NAME, _launch_command() if v else None))
        lay.addWidget(auto)

        lay.addSpacing(8)
        close = QPushButton("Close")
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def _show_status(self) -> None:
        hk = self.ctrl.hotkey
        if hk.ok:
            self.status.setText(f"✓ {self.ctrl.settings['hotkey']} is active")
        else:
            self.status.setText(f"✗ {hk.error or 'Not registered'} — pick another combination")

    def _hotkey_changed(self) -> None:
        seq = self.keys.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        if seq:
            self.ctrl.apply_hotkey(seq)
        self._show_status()

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Save screenshots to", self.folder.text())
        if d:
            self.folder.setText(d)
            self.ctrl.settings.set("save_dir", d)

    def showEvent(self, e):
        super().showEvent(e)
        winapi.set_dark_titlebar(int(self.winId()))


class Controller(QObject):
    hotkeyPressed = Signal()

    def __init__(self, app: QApplication, settings: Settings):
        super().__init__()
        self.app = app
        self.settings = settings
        self.editors: list = []
        self.session: CaptureSession | None = None
        self._session_deadline = 0.0
        self._hidden: list = []
        self._settings_dlg: SettingsDialog | None = None
        self._about_dlg: AboutDialog | None = None
        self._countdown: Countdown | None = None
        self.hotkeyPressed.connect(lambda: self.start_capture(), Qt.ConnectionType.QueuedConnection)
        self.hotkey = winapi.HotkeyListener(self.hotkeyPressed.emit)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_connection)
        self.thumbs = ThumbDock(self)
        self._build_tray()

    # ------------------------------------------------------------------ setup
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(icons.app_icon(), self)
        m = make_menu()
        self._snip_action = m.addAction("New snip", self.start_capture)
        m.addAction("Snip in 3 seconds", lambda: self.start_capture(delay_s=3))
        m.addAction("Snip in 10 seconds", lambda: self.start_capture(delay_s=10))
        m.addSeparator()
        m.addAction("Open image…", self.open_dialog)
        m.addAction("Paste image from clipboard", self.paste_clipboard)
        m.addAction("Open screenshots folder", self.open_save_folder)
        m.addAction("Close corner thumbnails", self.thumbs.close_all)
        m.addSeparator()
        m.addAction("Settings…", self.show_settings)
        m.addAction("About GraftSnip", self.show_about)
        m.addAction("Quit GraftSnip", self.quit)
        self._tray_menu = m
        self.tray.setContextMenu(m)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def start(self) -> None:
        QLocalServer.removeServer(SERVER_NAME)
        # Only apps running as this Windows user may send commands (snip / open / settings).
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.listen(SERVER_NAME)
        ok = self.apply_hotkey(self.settings["hotkey"])
        if not ok:
            self.tray.showMessage("GraftSnip", f"{self.settings['hotkey']} is taken by another app — choose a "
                                  "different shortcut in Settings.", QSystemTrayIcon.MessageIcon.Warning, 6000)
        elif not self.settings["welcomed"]:
            self.tray.showMessage("GraftSnip is ready", f"Press {self.settings['hotkey']} to snip, or click the "
                                  "tray icon.", icons.app_icon(), 6000)
            self.settings.set("welcomed", True)

    def apply_hotkey(self, seq: str) -> bool:
        ok = self.hotkey.start(seq)
        if ok:
            self.settings.set("hotkey", seq)
        else:  # keep the previous working shortcut if the new one is unavailable
            prev = self.settings["hotkey"]
            if prev != seq:
                self.hotkey.start(prev)
                err = self.hotkey.error
                self.hotkey.error = f"{seq}: unavailable" if self.hotkey.ok else err
        label = self.settings["hotkey"] if self.hotkey.ok else "no shortcut"
        self.tray.setToolTip(f"GraftSnip — {label}")
        self._snip_action.setText(f"New snip\t{self.settings['hotkey']}" if self.hotkey.ok else "New snip")
        return ok

    def _tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.start_capture(delay_ms=120)

    # ------------------------------------------------------------------ IPC
    def _on_connection(self) -> None:
        sock = self.server.nextPendingConnection()
        if sock is None:
            return
        sock.waitForReadyRead(800)
        cmd = bytes(sock.readAll()).decode("utf-8", "replace").strip()
        sock.disconnectFromServer()
        self.handle_command(cmd)

    def handle_command(self, cmd: str) -> None:
        if cmd.startswith("open "):
            self.open_file(cmd[5:])
        elif cmd == "snip":
            self.start_capture(delay_ms=60)
        elif cmd == "settings":
            self.show_settings()

    # ------------------------------------------------------------------ capture
    def start_capture(self, delay_s: int = 0, delay_ms: int = 0, hide_editors: bool = False) -> None:
        if self.session is not None:
            live = any(ov.isVisible() for ov in self.session.overlays)
            if live or time.monotonic() < self._session_deadline:
                log.info("capture requested but one is already in progress")
                return
            log.warning("discarding a stale capture session")
            self.session = None
        log.info("capture requested (delay %ss)", delay_s)
        self._session_deadline = time.monotonic() + (delay_s or 0) + (delay_ms or 0) / 1000 + 5
        self._hidden = [e for e in self.editors if e.isVisible()] if hide_editors else []
        for e in self._hidden:
            e.hide()
        hide_thumbs = self.thumbs.needs_hiding()  # only on Windows builds that can't exclude them from grabs
        if hide_thumbs:
            self.thumbs.hide_for_capture()
        base = delay_ms + (220 if self._hidden or hide_thumbs else 0)
        self.session = CaptureSession(self.settings, [])  # reserve so repeated hotkeys are ignored
        if delay_s > 0:
            self._countdown = self._countdown or Countdown()
            self._tick(delay_s)
        else:
            QTimer.singleShot(base, self._grab)

    def _tick(self, remaining: int) -> None:
        if remaining <= 0:
            self._countdown.hide()
            QTimer.singleShot(180, self._grab)  # let the countdown pill disappear first
            return
        self._countdown.show_text(f"Snipping in {remaining}…")
        QTimer.singleShot(1000, lambda: self._tick(remaining - 1))

    def _grab(self) -> None:
        log.info("grabbing screens")
        try:
            windows = winapi.list_windows()
        except Exception:
            log.exception("window enumeration failed")
            windows = []
        try:
            self.session = CaptureSession(self.settings, windows)
            self.session.captured.connect(self._captured)
            self.session.cancelled.connect(self._capture_over)
            self.session.start()
            log.info("capture overlay shown (%d windows)", len(windows))
        except Exception:
            log.exception("capture failed to start")
            self.session = None
            self._restore_hidden()

    def _captured(self, image: QImage, ratio: float, screen) -> None:
        self.session = None
        if self.settings["copy_on_capture"]:
            QGuiApplication.clipboard().setImage(image)
        self._restore_hidden()
        name = time.strftime("Screenshot %Y-%m-%d at %H.%M.%S")
        thumb = None
        if self.settings["thumbnails"]:
            try:
                thumb = self.thumbs.add(image, ratio, name, screen)
                name = thumb.name
            except Exception:
                log.exception("couldn't show the corner thumbnail")
        if thumb is None or self.settings["editor_on_capture"]:
            ed = self.open_editor(image, ratio, screen, name=name)
            if thumb is not None:
                thumb.attach_editor(ed)

    def _capture_over(self) -> None:
        self.session = None
        self._restore_hidden()

    def _restore_hidden(self) -> None:
        for e in self._hidden:
            try:
                e.show()
            except RuntimeError:
                pass
        self._hidden = []
        self.thumbs.restore_after_capture()

    # ------------------------------------------------------------------ editors
    def open_editor(self, image: QImage, unit: float, screen=None, name: str | None = None, state: dict | None = None):
        from .editor import EditorWindow
        name = name or time.strftime("Screenshot %Y-%m-%d at %H.%M.%S")
        ed = EditorWindow(self, image, unit, name, state)
        screen = screen or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        av = screen.availableGeometry()
        iw, ih = image.width() / unit, image.height() / unit
        w = int(min(av.width() * 0.92, max(1000, iw + 96)))
        h = int(min(av.height() * 0.92, max(620, ih + 170)))
        x, y = av.x() + (av.width() - w) // 2, av.y() + (av.height() - h) // 2
        col = self.thumbs.column(screen)
        if col is not None and x <= col.right():  # open beside the corner thumbnails, not under them
            room = av.right() - col.right() - 12
            if room >= ed.minimumWidth():
                w = min(w, room)
                x = col.right() + 1 + max(0, (room - w) // 2)
        ed.resize(w, h)
        ed.move(x, y)
        ed.closed.connect(self._editor_closed)
        self.editors.append(ed)
        ed.show()
        ed.raise_()
        ed.activateWindow()
        winapi.bring_to_front(int(ed.winId()))
        ed.canvas.setFocus()
        return ed

    def _editor_closed(self, ed) -> None:
        if ed in self.editors:
            self.editors.remove(ed)

    def open_file(self, path: str) -> None:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        img = reader.read()
        if img.isNull():
            self.tray.showMessage("GraftSnip", f"Couldn't open {Path(path).name}", QSystemTrayIcon.MessageIcon.Warning)
            return
        unit = max(1.0, max(img.width(), img.height()) / 2400.0)
        self.open_editor(img, unit, name=Path(path).stem)

    def open_dialog(self) -> None:
        start = self.settings["save_dir"] if Path(self.settings["save_dir"]).exists() else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(None, "Open image", start,
                                              "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)")
        if path:
            self.open_file(path)

    def paste_clipboard(self) -> None:
        img = QGuiApplication.clipboard().image()
        if img.isNull():
            self.tray.showMessage("GraftSnip", "There's no image on the clipboard.", icons.app_icon(), 3000)
            return
        self.open_editor(img, 1.0, name=time.strftime("Pasted %Y-%m-%d at %H.%M.%S"))

    def open_save_folder(self) -> None:
        folder = Path(self.settings["save_dir"]).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def show_settings(self) -> None:
        if self._settings_dlg is None:
            self._settings_dlg = SettingsDialog(self)
            self._settings_dlg.finished.connect(lambda _r: setattr(self, "_settings_dlg", None))
        self._settings_dlg.show()
        self._settings_dlg.raise_()
        self._settings_dlg.activateWindow()

    def show_about(self) -> None:
        if self._about_dlg is None:
            self._about_dlg = AboutDialog(self)
            self._about_dlg.finished.connect(lambda _r: setattr(self, "_about_dlg", None))
        self._about_dlg.show()
        self._about_dlg.raise_()
        self._about_dlg.activateWindow()

    def quit(self) -> None:
        for ed in list(self.editors):
            ed.close()
        if any(e.isVisible() for e in self.editors):
            return  # user cancelled a close prompt
        self.thumbs.close_all()
        self.hotkey.stop()
        self.tray.hide()
        self.app.quit()


# ---------------------------------------------------------------------- entry
def _setup_logging() -> None:
    logfile = config_dir() / "graftsnip.log"
    logging.basicConfig(filename=str(logfile), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if sys.stderr is None:  # pythonw: keep Qt/PySide tracebacks somewhere readable
        sys.stderr = open(logfile, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = sys.stderr

    def hook(exc_type, exc, tb):
        log.error("Unhandled exception:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = hook


def _command_from_args(argv: list[str]) -> str:
    args = [a for a in argv[1:] if a]
    if "--tray" in args:
        return "tray"
    if "--settings" in args:
        return "settings"
    files = [a for a in args if not a.startswith("--") and os.path.isfile(a)]
    if files:
        return "open " + os.path.abspath(files[0])
    return "snip"


def _send_to_running(cmd: str) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(400):
        return False
    sock.write(cmd.encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(800)
    sock.disconnectFromServer()
    return True


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    _setup_logging()
    if "--write-icon" in argv:
        app = QApplication(argv)
        target = argv[argv.index("--write-icon") + 1]
        if target.lower().endswith(".ico"):
            return 0 if icons.write_ico(target) else 1
        return 0 if icons.app_pixmap(256).save(target, "PNG") else 1
    winapi.set_app_id("Grafthouse.GraftSnip")
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(icons.app_icon())
    cmd = _command_from_args(argv)
    if _send_to_running(cmd):
        return 0
    apply_theme(app)
    ctrl = Controller(app, Settings())
    ctrl.start()
    if cmd != "tray":
        QTimer.singleShot(250, lambda: ctrl.handle_command(cmd))
    log.info("GraftSnip started (%s)", cmd)
    return app.exec()
