"""The editor window that opens after a capture (toolbar layout modelled on macOS markup)."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QGuiApplication, QImage, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import icons, winapi
from .annotations import PALETTE, SIZE_LABELS, Document, PixelAnn
from .canvas import Canvas
from .ui import Pill, Toast, make_menu, separator

TOOL_UI = [
    ("select", "select", "Select & move  (V)\nClick to select · drag to move · Shift-click to add"),
    ("shape", "rect", "Shape  (R)\nDrag a box · press R again for an ellipse · Shift = square"),
    ("arrow", "arrow", "Arrow  (A)\nDrag to point at something — then just start typing to label it"),
    ("pen", "pen", "Pen  (P)\nDraw freehand · hold Shift for a straight line"),
    ("marker", "marker", "Highlighter  (H)\nDrag over text to highlight it"),
    ("text", "text", "Text  (T)\nClick to type · Enter to finish · Shift+Enter for a new line"),
    ("pixel", "pixel", "Redact  (B)\nDrag over private info to pixelate · press B again for blur"),
    ("step", "step", "Numbered steps  (N)\nClick to drop 1, 2, 3… · type straight after to label"),
    ("spot", "spot", "Spotlight  (S)\nDrag around what matters — everything else is dimmed"),
    ("eraser", "eraser", "Eraser  (E)\nClick or drag across annotations to remove them"),
]

SHORTCUTS_HTML = """
<style>td{padding:3px 14px 3px 0;color:#ececee} td.k{color:#7FDDEE;font-weight:600;white-space:nowrap}
h3{color:#ffffff;margin:10px 0 4px 0}</style>
<h3>Quick point &amp; label</h3><table>
<tr><td class=k>A, then drag</td><td>Draw an arrow (tail where you start, head where you let go)</td></tr>
<tr><td class=k>…then just type</td><td>Adds a label at the arrow's tail — Enter to finish</td></tr>
<tr><td class=k>Drag the middle dot</td><td>Curve a selected arrow · double-click to straighten</td></tr>
<tr><td class=k>Shift while drawing</td><td>Snap arrows/lines to 15° · square boxes</td></tr></table>
<h3>Tools</h3><table>
<tr><td class=k>V  R  A  P  H</td><td>Select · Shape · Arrow · Pen · Highlighter</td></tr>
<tr><td class=k>T  B  N  S  E  C</td><td>Text · Redact · Steps · Spotlight · Eraser · Crop</td></tr>
<tr><td class=k>Same key again</td><td>Cycle the tool's style (ellipse, blur, double arrow…)</td></tr>
<tr><td class=k>[  ]</td><td>Smaller / bigger</td></tr></table>
<h3>Editing</h3><table>
<tr><td class=k>Ctrl+Z / Ctrl+Y</td><td>Undo / redo</td></tr>
<tr><td class=k>Del · Ctrl+D · Ctrl+A</td><td>Delete · duplicate · select all</td></tr>
<tr><td class=k>Arrow keys</td><td>Nudge selection (Shift = ×10)</td></tr>
<tr><td class=k>Space+drag · Ctrl+wheel</td><td>Pan · zoom</td></tr>
<tr><td class=k>Ctrl+0 · Ctrl+1</td><td>Fit · actual size</td></tr></table>
<h3>Finish</h3><table>
<tr><td class=k>Ctrl+C</td><td>Copy image</td></tr>
<tr><td class=k>Ctrl+Enter</td><td>Done — copy, save and close</td></tr>
<tr><td class=k>Corner thumbnail</td><td>Drag it into any app to share · click to edit · right-click for more</td></tr>
<tr><td class=k>Ctrl+S · Ctrl+Shift+S</td><td>Save to your GraftSnip folder · Save as…</td></tr>
<tr><td class=k>Ctrl+Shift+C</td><td>Copy all text in the image (OCR)</td></tr>
<tr><td class=k>Ctrl+N · Ctrl+O · Ctrl+V</td><td>New snip · open image · paste image</td></tr></table>
"""


def _tool_button(name: str, tip: str = "", checkable: bool = False, size: int = 32) -> QToolButton:
    b = QToolButton()
    b.setIcon(icons.icon(name))
    b.setIconSize(QSize(18, 18))
    b.setCheckable(checkable)
    b.setToolTip(tip)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setAutoRaise(True)
    b.setFixedSize(size, size)
    return b


class ColorButton(QToolButton):
    def __init__(self):
        super().__init__()
        self.colour = QColor("#FF3B30")
        self.setFixedSize(40, 32)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("Colour, size & style")

    def set_colour(self, c: str) -> None:
        self.colour = QColor(c)
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cy = self.height() / 2
        p.setPen(QPen(QColor(255, 255, 255, 210), 1.6))
        p.setBrush(self.colour)
        p.drawEllipse(QPointF(15, cy), 7.5, 7.5)
        chevron = QPen(QColor("#b4b4ba"), 1.5)
        chevron.setCapStyle(Qt.PenCapStyle.RoundCap)
        chevron.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(chevron)
        p.drawPolyline([QPointF(28, cy - 2), QPointF(31, cy + 1.2), QPointF(34, cy - 2)])
        p.end()


class Swatch(QToolButton):
    def __init__(self, colour: str, selected: bool):
        super().__init__()
        self.colour = QColor(colour)
        self.selected = selected
        self.setFixedSize(28, 28)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(colour.upper())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(14, 14)
        if self.selected:
            p.setPen(QPen(QColor("#FFFFFF"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 12.5, 12.5)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.setBrush(self.colour)
        p.drawEllipse(c, 9, 9)
        p.end()


class SizeDot(QToolButton):
    def __init__(self, index: int, selected: bool):
        super().__init__()
        self.index = index
        self.selected = selected
        self.setFixedSize(46, 30)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(f"Size {SIZE_LABELS[index]}  ( [ / ] )")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#f2f2f4") if self.selected else QColor(255, 255, 255, 22))
        p.drawRoundedRect(r, 7, 7)
        p.setBrush(QColor("#111113") if self.selected else QColor("#ececee"))
        rad = (2.0, 3.2, 4.6, 6.2)[self.index]
        p.drawEllipse(QPointF(r.center().x(), r.center().y()), rad, rad)
        p.end()


class StylePopover(QFrame):
    """Colour palette + size + per-tool style options for the current tool/selection."""

    SEGMENTS = {
        "arrow": ("Arrow", "arrow_style", [("Tapered", "tapered"), ("Line", "line"), ("Double", "double"), ("Plain", "plain")]),
        "shape": ("Shape", "shape", [("Rectangle", "rect"), ("Ellipse", "ellipse")]),
        "shape_fill": ("Fill", "shape_fill", [("Outline", False), ("Filled", True)]),
        "text": ("Text", "text_style", [("Label", "label"), ("Outline", "outline"), ("Plain", "plain")]),
        "pixel": ("Redact", "pixel_mode", [("Pixelate", "pixelate"), ("Blur", "blur")]),
        "spot": ("Spotlight", "spot_shape", [("Rectangle", "rect"), ("Ellipse", "ellipse")]),
    }

    def __init__(self, canvas: Canvas):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.canvas = canvas
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._inner = None

    def _head(self, text: str) -> QLabel:
        lab = QLabel(text.upper())
        lab.setObjectName("Head")
        return lab

    def rebuild(self) -> None:
        if self._inner is not None:
            self._outer.removeWidget(self._inner)
            self._inner.hide()
            self._inner.deleteLater()
        cv = self.canvas
        inner = QFrame(self)
        inner.setObjectName("Popover")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(8)
        kinds = cv.context_kinds()
        if not kinds <= {"pixel", "spot", "eraser", "crop"}:
            key = cv.colour_key()
            cur = QColor(cv.style_[key]).name().lower()
            lay.addWidget(self._head("Colour"))
            grid = QGridLayout()
            grid.setSpacing(4)
            for i, col in enumerate(PALETTE):
                sw = Swatch(col, col.lower() == cur)
                sw.clicked.connect(lambda _=False, c=col, k=key: self._pick(k, c))
                grid.addWidget(sw, 0, i)
            custom = QToolButton()
            custom.setText("+")
            custom.setFixedSize(28, 28)
            custom.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            custom.setToolTip("Custom colour…")
            custom.setStyleSheet("QToolButton{background:rgba(255,255,255,22);border-radius:14px;color:#ececee;"
                                 "font-size:13pt;} QToolButton:hover{background:rgba(255,255,255,40);}")
            custom.clicked.connect(lambda _=False, k=key: self._custom(k))
            grid.addWidget(custom, 0, len(PALETTE))
            lay.addLayout(grid)
        if not kinds <= {"spot", "eraser", "crop"}:
            lay.addWidget(self._head("Size"))
            row = QHBoxLayout()
            row.setSpacing(4)
            for i in range(len(SIZE_LABELS)):
                b = SizeDot(i, cv.style_["size"] == i)
                b.clicked.connect(lambda _=False, i=i: self._set("size", i))
                row.addWidget(b)
            row.addStretch(1)
            lay.addLayout(row)
        sections = []
        for k in ("arrow", "shape", "text", "pixel", "spot"):
            if k in kinds:
                sections.append(k)
                if k == "shape":
                    sections.append("shape_fill")
        for sec in sections:
            title, key, options = self.SEGMENTS[sec]
            lay.addWidget(self._head(title))
            row = QHBoxLayout()
            row.setSpacing(4)
            group = QButtonGroup(inner)
            for label, value in options:
                b = QPushButton(label)
                b.setCheckable(True)
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                b.setChecked(cv.style_[key] == value)
                b.clicked.connect(lambda _=False, k=key, v=value: self._set(k, v))
                group.addButton(b)
                row.addWidget(b)
            row.addStretch(1)
            lay.addLayout(row)
        shadow = QCheckBox("Soft drop shadows")
        shadow.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        shadow.setChecked(cv.doc.ctx.shadows)
        shadow.toggled.connect(cv.set_shadows)
        lay.addSpacing(2)
        lay.addWidget(shadow)
        self._outer.addWidget(inner)
        self._inner = inner
        self.adjustSize()

    def _pick(self, key: str, colour: str) -> None:
        self.canvas.set_style(key, colour)
        self.hide()

    def _set(self, key: str, value) -> None:
        self.canvas.set_style(key, value)
        self.rebuild()

    def _custom(self, key: str) -> None:
        self.hide()
        c = QColorDialog.getColor(QColor(self.canvas.style_[key]), self.canvas.window(), "Custom colour")
        if c.isValid():
            self.canvas.set_style(key, c.name())

    def popup_under(self, widget: QWidget) -> None:
        self.rebuild()
        g = widget.mapToGlobal(widget.rect().bottomLeft())
        x = g.x() - 12
        scr = QGuiApplication.screenAt(g) or QGuiApplication.primaryScreen()
        av = scr.availableGeometry()
        x = max(av.left() + 4, min(x, av.right() - self.width() - 4))
        self.move(x, g.y() + 8)
        self.show()


class OcrRunner(QObject):
    done = Signal(object, object)  # (lines | None, error | None)

    def run(self, image: QImage) -> None:
        from . import ocr

        def work():
            try:
                self.done.emit(ocr.recognise(image), None)
            except Exception as exc:  # surfaced to the user as a toast
                self.done.emit(None, str(exc))

        threading.Thread(target=work, name="GraftSnipOCR", daemon=True).start()


class EditorWindow(QMainWindow):
    closed = Signal(object)

    def __init__(self, controller, image: QImage, unit: float, name: str, state: dict | None = None):
        super().__init__()
        self.ctrl = controller
        self.settings = controller.settings
        self.name = name
        self.force_close = False
        self.discarded = False  # closed with "Discard": the corner thumbnail falls back to the last kept version
        self.setWindowTitle(f"{name} — GraftSnip")
        self.setWindowIcon(icons.app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(760, 480)

        self.doc = Document(image, unit)
        if state:  # reopened from a corner thumbnail: annotations stay editable
            self.doc.restore(state)
        self.canvas = Canvas(self.doc, self.settings)
        self.setCentralWidget(self.canvas)
        self.popover = StylePopover(self.canvas)
        self.toast = Toast(self.canvas)
        self.ocr = OcrRunner(self)
        self.ocr.done.connect(self._ocr_done)
        self._ocr_mode = None

        self._build_topbar()
        self._build_bottombar()
        self._build_cropbar()
        self._build_actions()

        cv = self.canvas
        cv.changed.connect(self._refresh)
        cv.selectionChanged.connect(self._refresh)
        cv.styleChanged.connect(self._refresh)
        cv.toolChanged.connect(self._tool_changed)
        cv.zoomChanged.connect(self._zoom_changed)
        cv.cropModeChanged.connect(self._crop_mode)
        cv.fileDropped.connect(self.ctrl.open_file)
        cv.message.connect(lambda m: self.toast.show_message(m, 1100))
        cv.installEventFilter(self)
        self._tool_changed(cv.tool)
        self._refresh()

    # ------------------------------------------------------------------ build
    def _build_topbar(self) -> None:
        bar = self.top = Pill(self.canvas)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(2)
        self.tool_buttons: dict[str, QToolButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for tool, icon_name, tip in TOOL_UI:
            b = _tool_button(icon_name, tip, checkable=True)
            b.clicked.connect(lambda _=False, t=tool: self._tool_clicked(t))
            group.addButton(b)
            lay.addWidget(b)
            self.tool_buttons[tool] = b
        lay.addWidget(separator())
        self.colour_btn = ColorButton()
        self.colour_btn.clicked.connect(lambda: self.popover.popup_under(self.colour_btn))
        lay.addWidget(self.colour_btn)
        lay.addWidget(separator())
        self.crop_btn = _tool_button("crop", "Crop  (C)\nDrag the edges · Enter to apply · Esc to cancel", checkable=True)
        self.crop_btn.clicked.connect(self._crop_clicked)
        lay.addWidget(self.crop_btn)
        lay.addWidget(separator())
        self.undo_btn = _tool_button("undo", "Undo  (Ctrl+Z)")
        self.undo_btn.clicked.connect(self.canvas.undo)
        self.redo_btn = _tool_button("redo", "Redo  (Ctrl+Y)")
        self.redo_btn.clicked.connect(self.canvas.redo)
        lay.addWidget(self.undo_btn)
        lay.addWidget(self.redo_btn)
        lay.addWidget(separator())
        self.ocr_btn = _tool_button("ocr", "Text actions\nCopy all text · auto-redact e-mails & phone numbers")
        self.ocr_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        om = make_menu(self.ocr_btn)
        om.addAction("Copy all text\tCtrl+Shift+C", lambda: self.run_ocr("copy"))
        om.addAction("Quick redact e-mails && phone numbers", lambda: self.run_ocr("redact"))
        self.ocr_btn.setMenu(om)
        lay.addWidget(self.ocr_btn)
        self.more_btn = _tool_button("more", "More")
        self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_btn.setMenu(self._more_menu())
        lay.addWidget(self.more_btn)
        lay.addSpacing(4)
        self.copy_btn = QToolButton()
        self.copy_btn.setObjectName("Copy")
        self.copy_btn.setIcon(icons.icon("copy", 16))
        self.copy_btn.setIconSize(QSize(16, 16))
        self.copy_btn.setText("Copy")
        self.copy_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.copy_btn.setFixedHeight(32)
        self.copy_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.copy_btn.setToolTip("Copy the annotated image  (Ctrl+C)")
        self.copy_btn.clicked.connect(self.copy_image)
        lay.addWidget(self.copy_btn)
        lay.addSpacing(2)
        self.done_btn = QToolButton()
        self.done_btn.setObjectName("Done")
        self.done_btn.setIcon(icons.icon("check", 16, normal="#111113", active="#111113"))
        self.done_btn.setIconSize(QSize(16, 16))
        self.done_btn.setText("Done")
        self.done_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.done_btn.setFixedHeight(32)
        self.done_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.done_btn.setToolTip("Copy, save and close  (Ctrl+Enter)")
        self.done_btn.clicked.connect(self.done)
        lay.addWidget(self.done_btn)
        bar.adjustSize()

    def _more_menu(self):
        m = make_menu(self.more_btn)
        m.addAction("New snip\tCtrl+N", lambda: self.ctrl.start_capture(hide_editors=True))
        m.addAction("New snip in 3 seconds", lambda: self.ctrl.start_capture(delay_s=3, hide_editors=True))
        m.addAction("New snip in 10 seconds", lambda: self.ctrl.start_capture(delay_s=10, hide_editors=True))
        m.addSeparator()
        m.addAction("Open image…\tCtrl+O", self.ctrl.open_dialog)
        m.addAction("Paste image from clipboard\tCtrl+V", self.ctrl.paste_clipboard)
        m.addSeparator()
        m.addAction("Save\tCtrl+S", self.save_quick)
        m.addAction("Save as…\tCtrl+Shift+S", self.save_as)
        m.addAction("Open screenshots folder", self.ctrl.open_save_folder)
        m.addSeparator()
        m.addAction("Clear all annotations", self.canvas.clear_all)
        m.addAction("Keyboard shortcuts\tF1", self.show_shortcuts)
        m.addAction("Settings…", self.ctrl.show_settings)
        m.addAction("About GraftSnip", self.ctrl.show_about)
        return m

    def _build_bottombar(self) -> None:
        bar = self.bottom = Pill(self.canvas)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 4, 6, 4)
        lay.setSpacing(2)
        self.dims = QLabel()
        lay.addWidget(self.dims)
        lay.addWidget(separator())
        zo = _tool_button("zoom_out", "Zoom out  (Ctrl+−)", size=30)
        zo.clicked.connect(lambda: self.canvas.zoom_step(-1))
        lay.addWidget(zo)
        self.pct = QToolButton()
        self.pct.setObjectName("Wide")
        self.pct.setFixedHeight(30)
        self.pct.setMinimumWidth(56)
        self.pct.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pct.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        pm = make_menu(self.pct)
        for v in (25, 50, 75, 100, 150, 200, 400):
            pm.addAction(f"{v}%", lambda v=v: self.canvas.set_percent(v))
        self.pct.setMenu(pm)
        lay.addWidget(self.pct)
        zi = _tool_button("zoom_in", "Zoom in  (Ctrl+=)", size=30)
        zi.clicked.connect(lambda: self.canvas.zoom_step(1))
        lay.addWidget(zi)
        lay.addWidget(separator())
        for text, slot, tip in (("Fit", self.canvas.fit, "Fit to window  (Ctrl+0)"),
                                ("100%", lambda: self.canvas.set_percent(100), "Actual size  (Ctrl+1)")):
            b = QToolButton()
            b.setObjectName("Wide")
            b.setText(text)
            b.setFixedHeight(30)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            lay.addWidget(b)

    def _build_cropbar(self) -> None:
        bar = self.cropbar = Pill(self.canvas)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 5, 6, 5)
        lay.setSpacing(4)
        lay.addWidget(QLabel("Drag the edges to crop · Enter to apply"))
        for text, slot, name in (("Reset", self.canvas.reset_crop, "Wide"), ("Cancel", self.canvas.cancel_crop, "Wide"),
                                 ("Apply", self.canvas.apply_crop, "Done")):
            b = QToolButton()
            b.setObjectName(name)
            b.setText(text)
            b.setFixedHeight(30)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            lay.addWidget(b)
        bar.hide()

    def _build_actions(self) -> None:
        def add(shortcuts, slot):
            a = QAction(self)
            a.setShortcuts([QKeySequence(s) for s in shortcuts])
            a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            a.triggered.connect(slot)
            self.addAction(a)
            return a

        cv = self.canvas
        add(["Ctrl+Z"], cv.undo)
        add(["Ctrl+Y", "Ctrl+Shift+Z"], cv.redo)
        add(["Ctrl+C"], self.copy_image)
        add(["Ctrl+Shift+C"], lambda: self.run_ocr("copy"))
        add(["Ctrl+S"], self.save_quick)
        add(["Ctrl+Shift+S"], self.save_as)
        add(["Ctrl+N"], lambda: self.ctrl.start_capture(hide_editors=True))
        add(["Ctrl+O"], self.ctrl.open_dialog)
        add(["Ctrl+V"], self.ctrl.paste_clipboard)
        add(["Ctrl+0"], cv.fit)
        add(["Ctrl+1"], lambda: cv.set_percent(100))
        add(["Ctrl+=", "Ctrl++"], lambda: cv.zoom_step(1))
        add(["Ctrl+-"], lambda: cv.zoom_step(-1))
        add(["Ctrl+Return", "Ctrl+Enter"], self.done)
        add(["Ctrl+W"], self.close)
        add(["F1"], self.show_shortcuts)

    # ------------------------------------------------------------------ layout
    def eventFilter(self, obj, ev):
        if obj is self.canvas and ev.type() == QEvent.Type.Resize:
            self._layout_overlays()
        return False

    def _layout_overlays(self) -> None:
        w, h = self.canvas.width(), self.canvas.height()
        for bar in (self.top, self.bottom, self.cropbar):
            bar.adjustSize()
        self.top.move(max(8, (w - self.top.width()) // 2), 12)
        self.bottom.move((w - self.bottom.width()) // 2, h - self.bottom.height() - 12)
        self.cropbar.move((w - self.cropbar.width()) // 2, h - self.bottom.height() - self.cropbar.height() - 22)
        top_m = 12 + self.top.height() + 14
        bot_m = 12 + self.bottom.height() + 14
        self.canvas.margins.setTop(top_m)
        self.canvas.margins.setBottom(bot_m)
        self.toast.bottom_offset = bot_m + 4

    # ------------------------------------------------------------------ state sync
    def _tool_clicked(self, tool: str) -> None:
        if tool == self.canvas.tool and tool in ("shape", "arrow", "pixel", "spot", "text"):
            self.canvas.cycle_variant(tool)
        else:
            self.canvas.set_tool(tool)

    def _crop_clicked(self) -> None:
        if self.canvas.crop_rect is None:
            self.canvas.enter_crop()
        else:
            self.canvas.apply_crop()

    def _tool_changed(self, tool: str) -> None:
        b = self.tool_buttons.get(tool)
        if b:
            b.setChecked(True)
        self._refresh()

    def _crop_mode(self, on: bool) -> None:
        self.crop_btn.setChecked(on)
        self.cropbar.setVisible(on)
        self._layout_overlays()
        if on:
            self.cropbar.raise_()

    def _zoom_changed(self) -> None:
        self.pct.setText(f"{round(self.canvas.zoom_percent())}%")

    def _refresh(self) -> None:
        cv = self.canvas
        self.undo_btn.setEnabled(cv.can_undo())
        self.redo_btn.setEnabled(cv.can_redo())
        self.colour_btn.set_colour(cv.style_[cv.colour_key()])
        self.tool_buttons["shape"].setIcon(icons.icon("ellipse" if cv.style_["shape"] == "ellipse" else "rect"))
        r = self.doc.output_rect().toAlignedRect()
        self.dims.setText(f"{r.width():,} \u00d7 {r.height():,} px")
        dirty = "• " if cv.is_dirty() else ""
        self.setWindowTitle(f"{dirty}{self.name} — GraftSnip")
        self._zoom_changed()

    def showEvent(self, e):
        super().showEvent(e)
        winapi.set_dark_titlebar(int(self.winId()))
        self._layout_overlays()

    # ------------------------------------------------------------------ output
    def rendered(self) -> QImage:
        if self.canvas.editing:
            self.canvas.finish_edit()
        if self.canvas.crop_rect is not None:
            self.canvas.apply_crop()
        return self.doc.render()

    def copy_image(self) -> None:
        QGuiApplication.clipboard().setImage(self.rendered())
        self.canvas.mark_saved()
        self._refresh()
        self.toast.show_message("Copied to clipboard")

    def _default_path(self) -> Path:
        folder = Path(self.settings["save_dir"]).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.name}.png"
        n = 2
        while path.exists():
            path = folder / f"{self.name} ({n}).png"
            n += 1
        return path

    def _write(self, path: Path) -> bool:
        img = self.rendered()
        fmt = "JPG" if path.suffix.lower() in (".jpg", ".jpeg") else "PNG"
        if fmt == "JPG":
            img = img.convertToFormat(QImage.Format.Format_RGB32)
        ok = img.save(str(path), fmt, 92 if fmt == "JPG" else -1)
        if ok:
            self.canvas.mark_saved()
            self._refresh()
        return ok

    def save_quick(self) -> Path | None:
        try:
            path = self._default_path()
        except OSError as exc:
            self.toast.show_message(f"Can't save: {exc}")
            return None
        if self._write(path):
            self.toast.show_message(f"Saved to {path.parent.name}\\{path.name}")
            return path
        self.toast.show_message("Couldn't save the image")
        return None

    def save_as(self) -> None:
        try:
            start = str(self._default_path())
        except OSError:
            start = self.name + ".png"
        path, _ = QFileDialog.getSaveFileName(self, "Save screenshot", start,
                                              "PNG image (*.png);;JPEG image (*.jpg *.jpeg)")
        if path:
            p = Path(path)
            if not p.suffix:
                p = p.with_suffix(".png")
            if self._write(p):
                self.toast.show_message(f"Saved {p.name}")
            else:
                self.toast.show_message("Couldn't save the image")

    def done(self) -> None:
        img = self.rendered()
        if self.settings["copy_on_done"]:
            QGuiApplication.clipboard().setImage(img)
        if self.settings["save_on_done"]:
            try:
                path = self._default_path()
                self._write(path)
            except OSError:
                pass
        self.force_close = True
        self.close()

    # ------------------------------------------------------------------ text actions
    def run_ocr(self, mode: str) -> None:
        if self._ocr_mode:
            return
        if self.canvas.editing:
            self.canvas.finish_edit()
        r = self.doc.output_rect().toAlignedRect()
        self._ocr_mode = (mode, r.topLeft())
        self.toast.show_message("Reading text…", 30000)
        self.ocr.run(self.doc.image.copy(r))

    def _ocr_done(self, lines, error) -> None:
        mode, origin = self._ocr_mode or ("copy", None)
        self._ocr_mode = None
        if error:
            self.toast.show_message(f"Text recognition failed: {error}", 4000)
            return
        from . import ocr
        if mode == "copy":
            text = ocr.plain_text(lines)
            if not text:
                self.toast.show_message("No text found")
                return
            QGuiApplication.clipboard().setText(text)
            n = text.count("\n") + 1
            self.toast.show_message(f"Copied {n} line{'s' if n != 1 else ''} of text")
            return
        pad = 2 * self.doc.unit
        rects = ocr.sensitive_rects(lines)
        items = [PixelAnn(rect=r.translated(origin.x(), origin.y()).adjusted(-pad, -pad, pad, pad),
                          mode=self.canvas.style_["pixel_mode"], size=max(1, self.canvas.style_["size"]))
                 for r in rects]
        if not items:
            self.toast.show_message("No e-mail addresses or phone numbers found")
            return
        self.canvas.add_items(items)
        self.toast.show_message(f"Redacted {len(items)} item{'s' if len(items) != 1 else ''}")

    # ------------------------------------------------------------------ misc
    def show_shortcuts(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("GraftSnip — keyboard shortcuts")
        lay = QVBoxLayout(dlg)
        lab = QLabel(SHORTCUTS_HTML)
        lab.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(lab)
        ok = QPushButton("Got it")
        ok.setObjectName("Primary")
        ok.clicked.connect(dlg.accept)
        lay.addWidget(ok, alignment=Qt.AlignmentFlag.AlignRight)
        dlg.show()
        winapi.set_dark_titlebar(int(dlg.winId()))
        dlg.exec()

    def closeEvent(self, e):
        if not self.force_close and self.canvas.is_dirty() and self.canvas.has_content():
            box = QMessageBox(self)
            box.setWindowTitle("GraftSnip")
            box.setText("Keep your annotated screenshot?")
            box.setInformativeText("It hasn't been copied or saved since your last change.")
            keep = box.addButton("Copy && save", QMessageBox.ButtonRole.AcceptRole)
            discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(keep)
            box.exec()
            clicked = box.clickedButton()
            if clicked is keep:
                self.done()
                e.ignore()
                return
            if clicked is not discard:
                e.ignore()
                return
            self.discarded = True
        self.popover.close()
        self.closed.emit(self)
        super().closeEvent(e)
