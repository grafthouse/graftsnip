"""Snip thumbnails that wait in the bottom-left corner until you drag them into another app (or close them).

Drag one into any app (chat, e-mail, browser upload, Explorer, Word...) to drop the screenshot there:
the drag carries a real PNG file plus the bitmap itself, so both file-style and image-style drop
targets accept it. Once a drop is accepted the thumbnail has done its job and disappears. While its
editor is open the thumbnail follows every edit, so whatever you drag is always the annotated version.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QMimeData,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import QColor, QDrag, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

from . import icons, winapi
from .settings import APP_NAME
from .ui import ACCENT, make_menu

log = logging.getLogger("graftsnip.thumbs")

MAX_W, MAX_H = 232, 150      # largest thumbnail image (logical px)
MIN_CARD_W, MIN_CARD_H = 176, 64
PAD = 6                      # card padding around the image
RADIUS = 10
SHADOW = 14                  # transparent margin the soft shadow is painted into
GAP = 10                     # between stacked thumbnails
EDGE = 14                    # distance from the work area's left/bottom edge
MAX_THUMBS = 3
CACHE_DAYS = 3
CAPTION = "Drag to share  ·  click to edit"


def drag_dir() -> Path:
    d = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME / "Drag"
    d.mkdir(parents=True, exist_ok=True)
    return d


def prune_drag_dir(days: float = CACHE_DAYS) -> None:
    """Drop files written for earlier drags. Kept for a few days because some apps (browsers in
    particular) only read a dropped file when the upload actually starts."""
    try:
        cutoff = time.time() - days * 86400
        for f in drag_dir().glob("*.png"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
    except OSError:
        pass


def safe_name(name: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name).strip(" .")
    return s or "Screenshot"


def _font(px: int) -> QFont:
    f = QFont("Segoe UI")
    f.setPixelSize(px)
    f.setWeight(QFont.Weight.DemiBold)
    return f


class SnipThumb(QWidget):
    def __init__(self, dock: "ThumbDock", image: QImage, unit: float, name: str, screen):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setObjectName("SnipThumb")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setAccessibleName(f"Snip thumbnail: {name}")
        self.setAccessibleDescription("Drag into another app to share it. Click to edit. Right-click for more.")
        self.dock = dock
        self.name = name
        self.unit = max(0.5, float(unit))
        self.screen_ = screen
        self.original = image        # the raw snip; annotations live in `state`
        self.state: dict | None = None
        self.image = image           # what a drag delivers right now (annotated render)
        self.editor = None
        self.rev = 0
        self.file: Path | None = None
        self.file_rev = -1
        self.excluded = False
        self.hover = False
        self.hover_close = False
        self.dragging = False
        self.press_pos: QPointF | None = None
        self.flash = ""
        self._anim = None
        self._pull = QTimer(self)
        self._pull.setSingleShot(True)
        self._pull.setInterval(220)
        self._pull.timeout.connect(self._pull_from_editor)
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._clear_flash)
        self.set_image(image)

    # ------------------------------------------------------------------ geometry
    def _dpr(self) -> float:
        sc = self.screen_ or QGuiApplication.primaryScreen()
        return max(1.0, sc.devicePixelRatio() if sc else 1.0)

    def set_image(self, image: QImage) -> None:
        self.image = image
        self.rev += 1
        lw, lh = max(1.0, image.width() / self.unit), max(1.0, image.height() / self.unit)
        s = min(1.0, MAX_W / lw, MAX_H / lh)
        self.tw, self.th = max(8, round(lw * s)), max(8, round(lh * s))
        self.card_w = max(MIN_CARD_W, self.tw + 2 * PAD)
        self.card_h = max(MIN_CARD_H, self.th + 2 * PAD)
        dpr = self._dpr()
        scaled = image.scaled(round(self.tw * dpr), round(self.th * dpr), Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        self.pm = QPixmap.fromImage(scaled)
        self.pm.setDevicePixelRatio(dpr)
        size = (self.card_w + 2 * SHADOW, self.card_h + 2 * SHADOW)
        if (self.width(), self.height()) != size:
            self.setFixedSize(*size)
            if self in self.dock.thumbs:  # e.g. a crop in the editor changed the shape
                self.dock.relayout()
        self.update()

    def card(self) -> QRectF:
        return QRectF(SHADOW, SHADOW, self.card_w, self.card_h)

    def image_rect(self) -> QRectF:
        return QRectF(SHADOW + (self.card_w - self.tw) / 2, SHADOW + (self.card_h - self.th) / 2, self.tw, self.th)

    def close_rect(self) -> QRectF:
        c = self.card()
        return QRectF(c.right() - 30, c.top() + 6, 24, 24)

    # ------------------------------------------------------------------ editor link
    def attach_editor(self, ed) -> None:
        self.editor = ed
        ed.canvas.changed.connect(self._pull.start)
        ed.closed.connect(self._editor_closed)
        self.update()

    def _pull_from_editor(self) -> None:
        ed = self.editor
        if ed is None:
            return
        try:
            self.set_image(ed.doc.render())
        except RuntimeError:  # editor already gone
            self.editor = None

    def _editor_closed(self, ed) -> None:
        if ed is not self.editor:
            return
        self._pull.stop()
        self.editor = None
        try:
            cv, doc = ed.canvas, ed.doc
            if getattr(ed, "discarded", False) and 0 <= cv.saved_index < len(cv.history):
                doc.restore(cv.history[cv.saved_index])  # back to the last copied/saved version
            self.state = doc.state()
            self.set_image(doc.render())
        except RuntimeError:
            self.update()

    # ------------------------------------------------------------------ file for drags
    def ensure_file(self) -> Path | None:
        if self.file is not None and self.file_rev == self.rev and self.file.exists():
            return self.file
        try:
            path = drag_dir() / f"{safe_name(self.name)}.png"
            if not self.image.save(str(path), "PNG"):
                log.warning("couldn't write drag file %s", path)
                return None
        except OSError:
            log.exception("couldn't write drag file")
            return None
        self.file, self.file_rev = path, self.rev
        return path

    def mime_data(self) -> QMimeData:
        mime = QMimeData()
        path = self.ensure_file()
        if path is not None:
            mime.setUrls([QUrl.fromLocalFile(str(path))])  # chat apps, browsers, e-mail, Explorer
        mime.setImageData(self.image)                       # Word, Paint, anything that takes a bitmap
        return mime

    # ------------------------------------------------------------------ show / move
    def prepare(self) -> None:
        """Create the native window and keep it out of screen grabs, before it is ever shown."""
        self.excluded = winapi.exclude_from_capture(int(self.winId()))

    def glide(self, target: QPoint, appear: bool = False) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        animate = winapi.animations_enabled() and (appear or self.isVisible())
        if not animate:
            self.move(target)
            if appear:
                self.setWindowOpacity(1.0)
                self.show()
            return
        group = QParallelAnimationGroup(self)
        pos = QPropertyAnimation(self, b"pos", group)
        pos.setDuration(240 if appear else 180)
        pos.setEasingCurve(QEasingCurve.Type.OutCubic)
        pos.setStartValue(target - QPoint(26, 0) if appear else self.pos())
        pos.setEndValue(target)
        group.addAnimation(pos)
        if appear:
            self.move(target - QPoint(26, 0))
            self.setWindowOpacity(0.0)
            self.show()
            fade = QPropertyAnimation(self, b"windowOpacity", group)
            fade.setDuration(200)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            group.addAnimation(fade)
        self._anim = group
        group.start()

    def fade_away(self) -> None:
        if self._anim is not None:
            self._anim.stop()
        if not winapi.animations_enabled() or not self.isVisible():
            self.hide()
            self.deleteLater()
            return
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(140)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(0.0)
        fade.finished.connect(self.hide)
        fade.finished.connect(self.deleteLater)
        self._anim = fade
        fade.start()

    # ------------------------------------------------------------------ feedback
    def show_flash(self, text: str, ms: int = 1100) -> None:
        self.flash = text
        self._flash_timer.start(ms)
        self.update()

    def _clear_flash(self) -> None:
        self.flash = ""
        self.update()

    # ------------------------------------------------------------------ events
    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = self.hover_close = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.press_pos = e.position()
        elif e.button() == Qt.MouseButton.RightButton:
            self._menu(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        pos = e.position()
        over_close = self.close_rect().contains(pos)
        if over_close != self.hover_close:
            self.hover_close = over_close
            self.update()
        self.setCursor(Qt.CursorShape.PointingHandCursor if over_close else Qt.CursorShape.OpenHandCursor)
        if self.press_pos is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            if (pos - self.press_pos).manhattanLength() >= QApplication.startDragDistance():
                start, self.press_pos = self.press_pos, None
                self.start_drag(start)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.press_pos is None:
            return
        self.press_pos = None
        if self.close_rect().contains(e.position()):
            self.dock.dismiss(self)
        else:
            self.dock.open(self)

    def start_drag(self, grab_at: QPointF) -> Qt.DropAction:
        drag = QDrag(self)
        drag.setMimeData(self.mime_data())
        drag.setPixmap(self.pm)
        ir = self.image_rect()
        hx = min(max(0.0, grab_at.x() - ir.left()), ir.width())
        hy = min(max(0.0, grab_at.y() - ir.top()), ir.height())
        drag.setHotSpot(QPoint(round(hx), round(hy)))
        self.dragging = True
        self.update()
        try:
            result = drag.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)
        finally:
            self.dragging = False
        log.info("thumbnail drag finished: %s", result)
        self.after_drop(result)
        return result

    def after_drop(self, result: Qt.DropAction) -> None:
        """A drop another app accepted means the job is done: the thumbnail goes. A cancelled drag
        (Esc, or let go somewhere that refuses it) leaves it in the corner to try again."""
        if result == Qt.DropAction.IgnoreAction:
            self.update()
            return
        ed = self.editor
        if ed is not None:  # the annotated image has been delivered: closing the editor needn't ask to keep it
            try:
                ed.canvas.mark_saved()
                ed._refresh()
            except RuntimeError:
                pass
        self.dock.dismiss(self)

    def _menu(self, at: QPoint) -> None:
        m = make_menu()
        m.addAction("Edit", lambda: self.dock.open(self))
        m.addAction("Copy", self.copy)
        m.addAction("Save as…", self.save_as)
        m.addSeparator()
        m.addAction("Close", lambda: self.dock.dismiss(self))
        if len(self.dock.thumbs) > 1:
            m.addAction("Close all thumbnails", self.dock.close_all)
        m.exec(at)

    def copy(self) -> None:
        QGuiApplication.clipboard().setImage(self.image)
        self.show_flash("\u2713  Copied")

    def save_as(self) -> None:
        folder = Path(self.dock.ctrl.settings["save_dir"]).expanduser()
        start = str(folder / f"{safe_name(self.name)}.png")
        path, _ = QFileDialog.getSaveFileName(None, "Save screenshot", start,
                                              "PNG image (*.png);;JPEG image (*.jpg *.jpeg)")
        if not path:
            return
        p = Path(path)
        if not p.suffix:
            p = p.with_suffix(".png")
        jpg = p.suffix.lower() in (".jpg", ".jpeg")
        img = self.image.convertToFormat(QImage.Format.Format_RGB32) if jpg else self.image
        ok = img.save(str(p), "JPG" if jpg else "PNG", 92 if jpg else -1)
        self.show_flash("\u2713  Saved" if ok else "Couldn't save")

    # ------------------------------------------------------------------ paint
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = self.card()
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(1, SHADOW - 2):  # soft shadow: rings fading outwards, nudged down a touch
            a = int(60 * (1 - i / (SHADOW - 2)) ** 2)
            p.setPen(QPen(QColor(0, 0, 0, a), 1.0))
            p.drawRoundedRect(card.adjusted(-i + 0.5, -i + 2.5, i - 0.5, i + 2.5), RADIUS + i, RADIUS + i)

        lit = self.hover or self.dragging
        p.setPen(QPen(QColor(ACCENT) if lit else QColor(255, 255, 255, 36), 1.4 if lit else 1.0))
        p.setBrush(QColor(30, 30, 33, 250))
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)

        ir = self.image_rect()
        clip = QPainterPath()
        clip.addRoundedRect(ir, 6, 6)
        p.save()
        p.setClipPath(clip)
        p.drawPixmap(ir.topLeft(), self.pm)
        p.restore()

        if self.hover and not self.dragging and not self.flash:
            p.save()
            cp = QPainterPath()
            cp.addRoundedRect(card.adjusted(1, 1, -1, -1), RADIUS - 1, RADIUS - 1)
            p.setClipPath(cp)
            band = QRectF(card.left(), card.bottom() - 42, card.width(), 42)
            grad = QLinearGradient(band.topLeft(), band.bottomLeft())
            grad.setColorAt(0.0, QColor(0, 0, 0, 0))
            grad.setColorAt(1.0, QColor(0, 0, 0, 205))
            p.fillRect(band, grad)
            p.restore()
            p.setFont(_font(11))
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRectF(card.left() + 6, card.bottom() - 24, card.width() - 12, 18),
                       int(Qt.AlignmentFlag.AlignCenter), CAPTION)
            cr = self.close_rect()
            p.setPen(QPen(QColor(255, 255, 255, 90 if self.hover_close else 50), 1))
            p.setBrush(QColor(70, 70, 76, 240) if self.hover_close else QColor(18, 18, 20, 225))
            p.drawEllipse(cr.adjusted(2, 2, -2, -2))
            x = icons.svg_pixmap("close", "#FFFFFF", 12, self._dpr())
            p.drawPixmap(QPointF(cr.center().x() - 6, cr.center().y() - 6), x)

        if self.editor is not None:
            self._badge(p, "Editing", card)
        if self.flash:
            self._centre_pill(p, self.flash, card)
        p.end()

    def _badge(self, p: QPainter, text: str, card: QRectF) -> None:
        p.setFont(_font(10))
        w = p.fontMetrics().horizontalAdvance(text) + 24
        box = QRectF(card.left() + 8, card.top() + 8, w, 18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(18, 18, 20, 220))
        p.drawRoundedRect(box, 9, 9)
        p.setBrush(QColor(ACCENT))
        p.drawEllipse(QPointF(box.left() + 9, box.center().y()), 3.2, 3.2)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(box.adjusted(16, 0, -6, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)

    def _centre_pill(self, p: QPainter, text: str, card: QRectF) -> None:
        p.setFont(_font(12))
        w = p.fontMetrics().horizontalAdvance(text) + 26
        box = QRectF(card.center().x() - w / 2, card.center().y() - 14, w, 28)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(18, 18, 20, 235))
        p.drawRoundedRect(box, 14, 14)
        p.setPen(QColor(ACCENT))
        p.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)


class ThumbDock(QObject):
    """Owns the stack of corner thumbnails: newest at the bottom, oldest pushed off past MAX_THUMBS."""

    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.thumbs: list[SnipThumb] = []
        self.screen_ = None
        self._hidden: list[SnipThumb] = []
        prune_drag_dir()

    def add(self, image: QImage, unit: float, name: str, screen=None) -> SnipThumb:
        self.screen_ = screen or QGuiApplication.primaryScreen()
        taken = {t.name for t in self.thumbs}
        base, n = name, 2
        while name in taken:
            name = f"{base} ({n})"
            n += 1
        t = SnipThumb(self, image, unit, name, self.screen_)
        t.prepare()
        self.thumbs.append(t)
        while len(self.thumbs) > MAX_THUMBS:
            self.dismiss(self.thumbs[0], relayout=False)
        self.relayout(appearing=t)
        log.info("thumbnail added (%dx%d, excluded from capture: %s)", image.width(), image.height(), t.excluded)
        return t

    def relayout(self, animate: bool = True, appearing: SnipThumb | None = None) -> None:
        sc = self.screen_ or QGuiApplication.primaryScreen()
        if sc is None:
            return
        av = sc.availableGeometry()
        card_bottom = av.bottom() + 1 - EDGE
        for t in reversed(self.thumbs):
            target = QPoint(av.left() + EDGE - SHADOW, card_bottom + SHADOW - t.height())
            if t is appearing:
                t.glide(target, appear=True)
            elif animate:
                t.glide(target)
            else:
                t.move(target)
            card_bottom = target.y() + SHADOW - GAP

    def column(self, screen=None) -> QRect | None:
        """The strip of the work area the thumbnails occupy, so editors can open beside it."""
        if not self.thumbs:
            return None
        if screen is not None and self.screen_ is not None and screen is not self.screen_:
            return None  # thumbnails live on another monitor
        sc = self.screen_ or screen or QGuiApplication.primaryScreen()
        av = sc.availableGeometry()
        return QRect(av.left(), av.top(), EDGE + max(t.card_w for t in self.thumbs) + EDGE, av.height())

    def open(self, t: SnipThumb) -> None:
        ed = t.editor
        if ed is not None:
            try:
                if ed.isMinimized():
                    ed.showNormal()
                ed.show()
                ed.raise_()
                ed.activateWindow()
                winapi.bring_to_front(int(ed.winId()))
                return
            except RuntimeError:
                t.editor = None
        ed = self.ctrl.open_editor(t.original, t.unit, t.screen_, name=t.name, state=t.state)
        t.attach_editor(ed)

    def dismiss(self, t: SnipThumb, relayout: bool = True) -> None:
        if t not in self.thumbs:
            return
        self.thumbs.remove(t)
        if t in self._hidden:
            self._hidden.remove(t)
        t._pull.stop()
        ed, t.editor = t.editor, None
        if ed is not None:
            try:
                ed.canvas.changed.disconnect(t._pull.start)
                ed.closed.disconnect(t._editor_closed)
            except (RuntimeError, TypeError):
                pass
        t.fade_away()
        if relayout:
            self.relayout()

    def close_all(self) -> None:
        for t in list(self.thumbs):
            self.dismiss(t, relayout=False)

    # Fallback for Windows builds without WDA_EXCLUDEFROMCAPTURE: hide while the screen is grabbed.
    def needs_hiding(self) -> bool:
        return any(t.isVisible() and not t.excluded for t in self.thumbs)

    def hide_for_capture(self) -> None:
        self._hidden = [t for t in self.thumbs if t.isVisible() and not t.excluded]
        for t in self._hidden:
            t.hide()

    def restore_after_capture(self) -> None:
        for t in self._hidden:
            if t in self.thumbs:
                t.show()
        self._hidden = []
