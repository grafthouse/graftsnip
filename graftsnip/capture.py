"""Frozen-screen capture overlay: region (with click-to-grab-window), window, full screen, freeform."""
from __future__ import annotations

import math

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QToolButton, QWidget

from . import icons, winapi
from .ui import Pill, separator

MODES = ("region", "window", "screen", "freeform")
MODE_UI = [("region", "region", "Rectangle  (R)\nDrag an area · or click a window"),
           ("window", "window", "Window  (W)\nClick a window · or drag an area"),
           ("screen", "screen", "Full screen  (F)\nClick anywhere · or drag an area"),
           ("freeform", "lasso", "Freeform  (L)\nDraw any shape")]
ACCENT = QColor("#00BFDB")  # GraftSnip cyan
HINT = QColor(0, 191, 219, 170)


class CaptureSession(QObject):
    captured = Signal(QImage, float, object)  # image (physical px), device-pixel ratio, QScreen
    cancelled = Signal()

    def __init__(self, settings, windows):
        super().__init__()
        self.settings = settings
        # Every snip starts as a rectangle. W / F / L switch mode for THIS snip only: a remembered mode
        # once left every later drag grabbing the whole window instead of the area dragged.
        self.mode = "region"
        self.windows = windows
        self.overlays: list[CaptureOverlay] = []
        self.finished = False

    def start(self) -> None:
        cursor_screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        for sc in QGuiApplication.screens():
            pm = sc.grabWindow(0)
            ov = CaptureOverlay(self, sc, pm, self._windows_for(sc, pm), show_toolbar=sc is cursor_screen)
            self.overlays.append(ov)
        for ov in self.overlays:
            ov.present()
        for ov in self.overlays:
            if ov.screen_ is cursor_screen:
                ov.activate()

    def _windows_for(self, sc, pm) -> list[tuple[str, QRectF]]:
        geo = sc.geometry()
        ratio = pm.width() / max(1, geo.width())
        ox, oy = geo.x(), geo.y()  # Qt keeps a screen's top-left in native pixels
        bounds = QRectF(0, 0, geo.width(), geo.height())
        out = []
        for _hwnd, title, (l, t, r, b) in self.windows:
            rect = QRectF((l - ox) / ratio, (t - oy) / ratio, (r - l) / ratio, (b - t) / ratio).intersected(bounds)
            if rect.width() > 4 and rect.height() > 4:
                out.append((title, rect))
        return out

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        for ov in self.overlays:
            ov.mode_changed()

    def finish(self, image: QImage, ratio: float, screen) -> None:
        if self.finished:
            return
        self.finished = True
        self._close_all()
        self.captured.emit(image, ratio, screen)

    def cancel(self) -> None:
        if self.finished:
            return
        self.finished = True
        self._close_all()
        self.cancelled.emit()

    def _close_all(self) -> None:
        for ov in self.overlays:
            ov.hide()
            ov.deleteLater()
        self.overlays = []


class CaptureOverlay(QWidget):
    def __init__(self, session: CaptureSession, screen, pixmap, windows, show_toolbar: bool):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.session = session
        self.screen_ = screen
        geo = screen.geometry()
        self.ratio = pixmap.width() / max(1, geo.width())
        pixmap.setDevicePixelRatio(self.ratio)
        self.pm = pixmap
        self.img = pixmap.toImage()
        self.windows = windows
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.cursor_pos = QPointF(self.mapFromGlobal(QCursor.pos()))
        self.press: QPointF | None = None
        self.sel: QRectF | None = None
        self.dragging = False
        self.lasso: list[QPointF] = []
        self.space = False
        self.last_pos = QPointF()
        self.flash = ""
        self.toolbar = self._build_toolbar() if show_toolbar else None

    # ------------------------------------------------------------------ setup
    def present(self) -> None:
        self.create()
        handle = self.windowHandle()
        if handle is not None:
            handle.setScreen(self.screen_)
        self.setGeometry(self.screen_.geometry())
        self.showFullScreen()
        if self.toolbar:
            self.toolbar.adjustSize()
            self.toolbar.move((self.width() - self.toolbar.width()) // 2, 18)
            self.toolbar.raise_()

    def activate(self) -> None:
        self.raise_()
        self.activateWindow()
        winapi.bring_to_front(int(self.winId()))
        self.setFocus()

    def _build_toolbar(self):
        bar = Pill(self)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(2)
        self.mode_buttons = {}
        group = QButtonGroup(bar)
        for mode, icon_name, tip in MODE_UI:
            b = QToolButton()
            b.setIcon(icons.icon(icon_name))
            b.setIconSize(QSize(18, 18))
            b.setCheckable(True)
            b.setFixedSize(34, 32)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.ArrowCursor)
            b.setChecked(mode == self.session.mode)
            b.clicked.connect(lambda _=False, m=mode: self.session.set_mode(m))
            group.addButton(b)
            lay.addWidget(b)
            self.mode_buttons[mode] = b
        lay.addWidget(separator())
        close = QToolButton()
        close.setIcon(icons.icon("close"))
        close.setIconSize(QSize(18, 18))
        close.setFixedSize(32, 32)
        close.setToolTip("Cancel  (Esc)")
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.setCursor(Qt.CursorShape.ArrowCursor)
        close.clicked.connect(self.session.cancel)
        lay.addWidget(close)
        return bar

    def mode_changed(self) -> None:
        if self.toolbar:
            self.mode_buttons[self.session.mode].setChecked(True)
        self.press, self.sel, self.dragging, self.lasso = None, None, False, []
        self.update()

    # ------------------------------------------------------------------ helpers
    def _window_at(self, pos: QPointF):
        for title, r in self.windows:
            if r.contains(pos):
                return title, r
        return None

    def _phys(self, r: QRectF) -> QRect:
        k = self.ratio
        return QRect(round(r.x() * k), round(r.y() * k), round(r.width() * k), round(r.height() * k)) \
            .intersected(self.img.rect())

    def _capture_rect(self, r: QRectF) -> None:
        pr = self._phys(r)
        if pr.width() < 2 or pr.height() < 2:
            return
        self.session.finish(self.img.copy(pr), self.ratio, self.screen_)

    def _capture_lasso(self) -> None:
        path = QPainterPath(self.lasso[0])
        for q in self.lasso[1:]:
            path.lineTo(q)
        path.closeSubpath()
        pp = QTransform.fromScale(self.ratio, self.ratio).map(path)
        pr = pp.boundingRect().toAlignedRect().intersected(self.img.rect())
        if pr.width() < 3 or pr.height() < 3:
            return
        out = QImage(pr.size(), QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(Qt.GlobalColor.transparent)
        mask = QImage(pr.size(), QImage.Format.Format_ARGB32_Premultiplied)
        mask.fill(Qt.GlobalColor.transparent)
        mp = QPainter(mask)
        mp.setRenderHint(QPainter.RenderHint.Antialiasing)
        mp.translate(-pr.x(), -pr.y())
        mp.fillPath(pp, QBrush(QColor("#FFFFFF")))
        mp.end()
        p = QPainter(out)
        p.drawImage(QPoint(0, 0), self.img, pr)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        p.drawImage(QPoint(0, 0), mask)
        p.end()
        self.session.finish(out, self.ratio, self.screen_)

    def _over_toolbar(self, pos: QPointF) -> bool:
        return bool(self.toolbar and self.toolbar.geometry().contains(pos.toPoint()))

    # ------------------------------------------------------------------ events
    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() == Qt.MouseButton.RightButton:
            self.session.cancel()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        mode = self.session.mode
        self.press = pos
        self.last_pos = pos
        if mode == "freeform":
            self.lasso = [pos]
            self.dragging = True
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        self.cursor_pos = pos
        mode = self.session.mode
        if self.press is not None and mode != "freeform":  # a drag is always an area, whatever the mode
            if not self.dragging and math.hypot(pos.x() - self.press.x(), pos.y() - self.press.y()) > 3:
                self.dragging = True
            if self.dragging:
                if self.space and self.sel is not None:
                    d = pos - self.last_pos
                    self.sel.translate(d)
                    self.press += d
                else:
                    end = pos
                    if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                        dx, dy = pos.x() - self.press.x(), pos.y() - self.press.y()
                        m = max(abs(dx), abs(dy))
                        end = QPointF(self.press.x() + math.copysign(m, dx or 1), self.press.y() + math.copysign(m, dy or 1))
                    self.sel = QRectF(self.press, end).normalized()
        elif mode == "freeform" and self.dragging:
            last = self.lasso[-1]
            if math.hypot(pos.x() - last.x(), pos.y() - last.y()) > 1.5:
                self.lasso.append(pos)
        self.last_pos = pos
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.press is None:
            return
        pos = e.position()
        mode = self.session.mode
        was_drag = self.dragging
        self.press, self.dragging = None, False
        if mode == "freeform":
            if len(self.lasso) > 5:
                self._capture_lasso()
            self.lasso = []
        else:
            sel, self.sel = self.sel, None
            if was_drag:
                if sel is not None and sel.width() >= 3 and sel.height() >= 3:
                    self._capture_rect(sel)
            elif mode == "region":
                w = self._window_at(pos)
                if w:
                    self._capture_rect(w[1])
            elif mode == "window":
                w = self._window_at(pos)
                self._capture_rect(w[1] if w else QRectF(self.rect()))
            elif mode == "screen":
                self._capture_rect(QRectF(self.rect()))
        self.update()

    def keyPressEvent(self, e):
        k = e.key()
        keys = {Qt.Key.Key_R: "region", Qt.Key.Key_1: "region", Qt.Key.Key_W: "window", Qt.Key.Key_2: "window",
                Qt.Key.Key_F: "screen", Qt.Key.Key_3: "screen", Qt.Key.Key_L: "freeform", Qt.Key.Key_4: "freeform"}
        if k == Qt.Key.Key_Escape:
            self.session.cancel()
        elif k in keys:
            self.session.set_mode(keys[k])
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._capture_rect(self.sel if self.sel is not None else QRectF(self.rect()))
        elif k == Qt.Key.Key_Space and not e.isAutoRepeat():
            self.space = True
        elif k == Qt.Key.Key_C:
            px = self.img.pixelColor(self._phys_point(self.cursor_pos))
            QGuiApplication.clipboard().setText(px.name().upper())
            self.flash = f"Copied {px.name().upper()}"
            QTimer.singleShot(1200, self._clear_flash)
            self.update()
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            d = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                 Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}[k]
            QCursor.setPos(QCursor.pos() + QPoint(*d))

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self.space = False

    def _clear_flash(self):
        self.flash = ""
        self.update()

    def _phys_point(self, p: QPointF) -> QPoint:
        x = min(max(0, int(p.x() * self.ratio)), self.img.width() - 1)
        y = min(max(0, int(p.y() * self.ratio)), self.img.height() - 1)
        return QPoint(x, y)

    # ------------------------------------------------------------------ paint
    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(0, 0, self.pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = QRectF(self.rect())
        mode = self.session.mode
        hole = QPainterPath()
        outline = None
        label = None
        win_title = None
        hover_hint = False  # Rectangle mode: the window under the cursor is only outlined, never lit up
        if mode != "freeform" and self.dragging and self.sel is not None:
            hole.addRect(self.sel)
            outline = self.sel
        elif mode in ("region", "window") and not self.dragging and not self._over_toolbar(self.cursor_pos):
            w = self._window_at(self.cursor_pos)
            if w:
                outline = w[1]
                win_title = w[0]
                if mode == "window":
                    hole.addRect(w[1])
                else:
                    hover_hint = True
            elif mode == "window":
                hole.addRect(full)
        elif mode == "screen":
            hole.addRect(full)
            outline = full.adjusted(1, 1, -1, -1)
        elif mode == "freeform" and len(self.lasso) > 1:
            lp = QPainterPath(self.lasso[0])
            for q in self.lasso[1:]:
                lp.lineTo(q)
            lp.closeSubpath()
            hole = lp
        dim = QPainterPath()
        dim.addRect(full)
        p.fillPath(dim.subtracted(hole) if not hole.isEmpty() else dim, QBrush(QColor(0, 0, 0, 120)))

        if mode == "freeform" and len(self.lasso) > 1:
            pen = QPen(QColor("#FFFFFF"), 1.6)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolyline(self.lasso)
        if outline is not None:
            dragging_sel = mode != "freeform" and self.dragging
            p.setBrush(Qt.BrushStyle.NoBrush)
            if hover_hint:
                pen = QPen(HINT, 1.5, Qt.PenStyle.DashLine)
            else:
                pen = QPen(QColor("#FFFFFF") if dragging_sel else ACCENT, 1.4 if dragging_sel else 2.4)
            p.setPen(pen)
            p.drawRect(outline.adjusted(1, 1, -1, -1) if hover_hint else outline)
            pr = self._phys(outline)
            label = f"{pr.width():,} \u00d7 {pr.height():,}"
            if hover_hint:
                label = "Drag to snip an area  ·  or click to snip this window"
            elif win_title and mode == "window":
                label = f"{win_title[:60]}  ·  {label}"
            self._pill(p, label, outline, below=dragging_sel)

        if mode in ("region", "freeform") and not self.dragging and not self._over_toolbar(self.cursor_pos):
            c = self.cursor_pos
            p.setPen(QPen(QColor(255, 255, 255, 70), 1))
            p.drawLine(QPointF(0, c.y() + 0.5), QPointF(self.width(), c.y() + 0.5))
            p.drawLine(QPointF(c.x() + 0.5, 0), QPointF(c.x() + 0.5, self.height()))
        if self.session.settings["magnifier"] and (mode in ("region", "freeform") or self.dragging) \
                and not self._over_toolbar(self.cursor_pos):
            self._paint_loupe(p)
        p.end()

    def _pill(self, p: QPainter, text: str, anchor: QRectF, below: bool) -> None:
        f = QFont("Segoe UI")
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        w = p.fontMetrics().horizontalAdvance(text) + 18
        h = 24
        if below:
            x, y = anchor.right() - w, anchor.bottom() + 8
            if y + h > self.height() - 4:
                y = anchor.bottom() - h - 8
        else:
            x, y = anchor.left() + 8, anchor.top() + 8
        x = min(max(4, x), self.width() - w - 4)
        y = min(max(4, y), self.height() - h - 4)
        box = QRectF(x, y, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(18, 18, 20, 225))
        p.drawRoundedRect(box, 7, 7)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)

    def _paint_loupe(self, p: QPainter) -> None:
        n, cell = 15, 8
        size = n * cell
        c = self.cursor_pos
        phys = self._phys_point(c)
        sub = self.img.copy(QRect(phys.x() - n // 2, phys.y() - n // 2, n, n))
        x, y = c.x() + 24, c.y() + 24
        if x + size > self.width() - 4:
            x = c.x() - 24 - size
        if y + size + 34 > self.height() - 4:
            y = c.y() - 24 - size - 34
        box = QRectF(x, y, size, size)
        clip = QPainterPath()
        clip.addRoundedRect(box, 10, 10)
        p.save()
        p.setClipPath(clip)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(box, sub)
        p.setPen(QPen(QColor(0, 0, 0, 30), 1))
        for i in range(1, n):
            p.drawLine(QPointF(x + i * cell, y), QPointF(x + i * cell, y + size))
            p.drawLine(QPointF(x, y + i * cell), QPointF(x + size, y + i * cell))
        mid = QRectF(x + (n // 2) * cell, y + (n // 2) * cell, cell, cell)
        p.setPen(QPen(QColor("#000000"), 2))
        p.drawRect(mid)
        p.setPen(QPen(QColor("#FFFFFF"), 1))
        p.drawRect(mid)
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box, 10, 10)
        colour = self.img.pixelColor(phys)
        text = self.flash or f"{phys.x()}, {phys.y()}   {colour.name().upper()}"
        f = QFont("Segoe UI")
        f.setPixelSize(11)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        info = QRectF(x, y + size + 6, size, 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(18, 18, 20, 230))
        p.drawRoundedRect(info, 7, 7)
        if not self.flash:
            p.setBrush(colour)
            p.setPen(QPen(QColor(255, 255, 255, 120), 1))
            p.drawRoundedRect(QRectF(info.right() - 19, info.top() + 6, 12, 12), 3, 3)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(info.adjusted(8, 0, -22, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
