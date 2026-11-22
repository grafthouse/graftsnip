"""The interactive annotation canvas.

Coordinates: annotations live in image pixels. ``zoom`` is widget (logical)
pixels per image pixel and ``offset`` is where image (0, 0) sits on screen.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QMarginsF, QPointF, QRect, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPixmap, QTextCursor
from PySide6.QtWidgets import QFrame, QPlainTextEdit, QWidget

from .annotations import (
    ArrowAnn,
    PenAnn,
    PixelAnn,
    ShapeAnn,
    SpotAnn,
    StepAnn,
    TextAnn,
    box_handles,
    contrast_color,
    rect_from_points,
    resize_box,
    snap_angle,
)
from .ui import make_menu

ACCENT = QColor("#00BFDB")  # GraftSnip cyan
BG = QColor("#141416")
TOOLS = ("select", "shape", "arrow", "pen", "marker", "text", "pixel", "step", "spot", "eraser", "crop")
DRAW_TOOLS = ("shape", "arrow", "pen", "marker", "pixel", "spot")
ZOOM_STEPS = (5, 10, 15, 20, 25, 33, 50, 67, 75, 100, 125, 150, 200, 300, 400, 600, 800, 1200, 1600)
STYLE_KEYS = ("color", "marker_color", "size", "shape", "shape_fill", "arrow_style", "text_style",
              "pixel_mode", "spot_shape")
K = Qt.Key
TOOL_KEYS = {K.Key_V: "select", K.Key_R: "shape", K.Key_A: "arrow", K.Key_P: "pen", K.Key_H: "marker",
             K.Key_T: "text", K.Key_B: "pixel", K.Key_N: "step", K.Key_S: "spot", K.Key_E: "eraser",
             K.Key_C: "crop"}
VARIANTS = {"shape": ("shape", ("rect", "ellipse")), "arrow": ("arrow_style", ArrowAnn.STYLES),
            "pixel": ("pixel_mode", ("pixelate", "blur")), "spot": ("spot_shape", ("rect", "ellipse")),
            "text": ("text_style", TextAnn.STYLES)}
C = Qt.CursorShape
BOX_CURSORS = {"tl": C.SizeFDiagCursor, "br": C.SizeFDiagCursor, "tr": C.SizeBDiagCursor,
               "bl": C.SizeBDiagCursor, "t": C.SizeVerCursor, "b": C.SizeVerCursor,
               "l": C.SizeHorCursor, "r": C.SizeHorCursor}
SHIFT = Qt.KeyboardModifier.ShiftModifier
CTRL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier


class LabelEditor(QPlainTextEdit):
    """Borderless, transparent text box overlaid on a text annotation while typing."""

    commit = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.document().setDocumentMargin(0)
        self.setContentsMargins(0, 0, 0, 0)
        self.setViewportMargins(0, 0, 0, 0)
        self.viewport().setAutoFillBackground(False)
        self.setTabChangesFocus(True)

    def keyPressEvent(self, e):
        if e.key() in (K.Key_Return, K.Key_Enter) and not (e.modifiers() & SHIFT):
            self.commit.emit()
            return
        if e.key() == K.Key_Escape:
            self.commit.emit()
            return
        super().keyPressEvent(e)

    def scrollContentsBy(self, dx, dy):  # never scroll: the box always grows to fit
        pass


class Canvas(QWidget):
    changed = Signal()
    selectionChanged = Signal()
    zoomChanged = Signal()
    toolChanged = Signal(str)
    cropModeChanged = Signal(bool)
    styleChanged = Signal()
    fileDropped = Signal(str)
    message = Signal(str)

    def __init__(self, doc, settings, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.settings = settings
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.style_ = {k: settings[k] for k in STYLE_KEYS}
        self.doc.ctx.shadows = bool(settings["shadows"])
        tool = settings["tool"]
        self.tool = tool if tool in TOOLS and tool != "crop" else "arrow"
        self.selection: list[str] = []
        self.zoom = 1.0
        self.offset = QPointF(0, 0)
        self.fit_mode = True
        self.margins = QMarginsF(24, 72, 24, 66)
        self.history = [doc.state()]
        self.hindex = 0
        self.saved_index = 0
        self.drag: dict | None = None
        self.hover: str | None = None
        self.hover_pos: QPointF | None = None
        self.pending: str | None = None
        self.hint_until = 0.0
        self.editor: LabelEditor | None = None
        self.editing: TextAnn | None = None
        self.edit_new = False
        self.space = False
        self.crop_rect: QRectF | None = None
        self._crop_prev_tool = self.tool
        self._base_cache: tuple | None = None
        self._cache_timer = QTimer(self)
        self._cache_timer.setSingleShot(True)
        self._cache_timer.timeout.connect(self._build_cache)
        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.timeout.connect(self.update)
        self._shown = False

    # ------------------------------------------------------------------ coords
    def to_img(self, p: QPointF) -> QPointF:
        return QPointF((p.x() - self.offset.x()) / self.zoom, (p.y() - self.offset.y()) / self.zoom)

    def to_scr(self, p: QPointF) -> QPointF:
        return QPointF(p.x() * self.zoom + self.offset.x(), p.y() * self.zoom + self.offset.y())

    def rect_to_scr(self, r: QRectF) -> QRectF:
        return QRectF(self.to_scr(r.topLeft()), QSizeF(r.width() * self.zoom, r.height() * self.zoom))

    def view_area(self) -> QRectF:
        return self.doc.full_rect() if self.crop_rect is not None else self.doc.output_rect()

    def avail(self) -> QRectF:
        return QRectF(self.rect()).marginsRemoved(self.margins)

    def dpr(self) -> float:
        return self.devicePixelRatioF() or 1.0

    def tol(self) -> float:
        return max(1.0, 5.0 / self.zoom)

    # ------------------------------------------------------------------ view
    def fit(self) -> None:
        a, av = self.view_area(), self.avail()
        if a.width() <= 0 or av.width() < 20 or av.height() < 20:
            return
        self.zoom = max(0.02, min(av.width() / a.width(), av.height() / a.height(), 1.0 / self.dpr()))
        self.fit_mode = True
        self.offset = QPointF(av.center().x() - a.center().x() * self.zoom,
                              av.center().y() - a.center().y() * self.zoom)
        self._clamp()
        self._after_view_change()

    def _clamp(self) -> None:
        a, av = self.rect_to_scr(self.view_area()), self.avail()
        dx = dy = 0.0
        if a.width() <= av.width():
            dx = av.center().x() - a.center().x()
        elif a.left() > av.left():
            dx = av.left() - a.left()
        elif a.right() < av.right():
            dx = av.right() - a.right()
        if a.height() <= av.height():
            dy = av.center().y() - a.center().y()
        elif a.top() > av.top():
            dy = av.top() - a.top()
        elif a.bottom() < av.bottom():
            dy = av.bottom() - a.bottom()
        self.offset = QPointF(round(self.offset.x() + dx), round(self.offset.y() + dy))

    def set_zoom(self, z: float, anchor: QPointF | None = None) -> None:
        z = max(0.02, min(16.0, z))
        if anchor is None:
            anchor = QRectF(self.rect()).center()
        ip = self.to_img(anchor)
        self.zoom = z
        self.offset = QPointF(anchor.x() - ip.x() * z, anchor.y() - ip.y() * z)
        self.fit_mode = False
        self._clamp()
        self._after_view_change()

    def zoom_percent(self) -> float:
        return self.zoom * self.dpr() * 100.0

    def set_percent(self, pct: float) -> None:
        self.set_zoom(pct / 100.0 / self.dpr())

    def zoom_step(self, direction: int) -> None:
        cur = self.zoom_percent()
        if direction > 0:
            nxt = next((s for s in ZOOM_STEPS if s > cur * 1.01), ZOOM_STEPS[-1])
        else:
            nxt = next((s for s in reversed(ZOOM_STEPS) if s < cur * 0.99), ZOOM_STEPS[0])
        self.set_percent(nxt)

    def _after_view_change(self) -> None:
        self._position_editor()
        self.zoomChanged.emit()
        self.update()

    def resizeEvent(self, e):
        if self.fit_mode:
            self.fit()
        else:
            self._clamp()
            self._after_view_change()
        super().resizeEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        if not self._shown:
            self._shown = True
            self.fit()

    # ------------------------------------------------------------------ history
    def commit(self) -> None:
        st = self.doc.state()
        if st == self.history[self.hindex]:
            return
        del self.history[self.hindex + 1:]
        self.history.append(st)
        if len(self.history) > 300:
            self.history.pop(0)
            self.saved_index -= 1
        self.hindex = len(self.history) - 1
        self.changed.emit()
        self.update()

    def can_undo(self) -> bool:
        return self.hindex > 0

    def can_redo(self) -> bool:
        return self.hindex < len(self.history) - 1

    def undo(self) -> None:
        self._end_transients()
        if self.can_undo():
            self.hindex -= 1
            self._restore()

    def redo(self) -> None:
        self._end_transients()
        if self.can_redo():
            self.hindex += 1
            self._restore()

    def _restore(self) -> None:
        old_crop = self.doc.crop
        self.doc.restore(self.history[self.hindex])
        self.selection = [i for i in self.selection if self.doc.find(i)]
        self.pending = None
        self.drag = None
        if old_crop != self.doc.crop:
            self.fit()
        self.selectionChanged.emit()
        self.changed.emit()
        self.update()

    def _end_transients(self) -> None:
        if self.editing:
            self.finish_edit()
        if self.crop_rect is not None:
            self.cancel_crop()

    def is_dirty(self) -> bool:
        return self.hindex != self.saved_index

    def mark_saved(self) -> None:
        self.saved_index = self.hindex

    def has_content(self) -> bool:
        return bool(self.doc.items) or self.doc.crop is not None

    # ------------------------------------------------------------------ style / tools
    def colour_key(self) -> str:
        kinds = self.context_kinds()
        return "marker_color" if kinds == {"marker"} else "color"

    def context_kinds(self) -> set[str]:
        if self.editing:
            return {"text"}
        items = [self.doc.find(i) for i in self.selection]
        kinds = {("marker" if a.kind == "pen" and a.marker else a.kind) for a in items if a}
        return kinds or {self.tool}

    def set_style(self, key: str, value) -> None:
        self.style_[key] = value
        self.settings.set(key, value)
        targets = [self.doc.find(i) for i in self.selection]
        if self.editing and self.editing not in targets:
            targets.append(self.editing)
        changed = False
        for a in targets:
            if a is None:
                continue
            if key == "color" and a.kind not in ("pixel", "spot") and not (a.kind == "pen" and a.marker):
                a.color = value
            elif key == "marker_color" and a.kind == "pen" and a.marker:
                a.color = value
            elif key == "size":
                a.size = int(value)
            elif key == "arrow_style" and a.kind == "arrow":
                a.style = value
            elif key == "shape" and a.kind == "shape":
                a.shape = value
            elif key == "shape_fill" and a.kind == "shape":
                a.fill = bool(value)
            elif key == "spot_shape" and a.kind == "spot":
                a.shape = value
            elif key == "text_style" and a.kind == "text":
                a.style = value
            elif key == "pixel_mode" and a.kind == "pixel":
                a.mode = value
            else:
                continue
            changed = True
        if changed:
            self.doc.relayout()
            if self.editing:
                self._position_editor()
            else:
                self.commit()
        self.styleChanged.emit()
        self.update()

    def set_shadows(self, on: bool) -> None:
        self.doc.ctx.shadows = bool(on)
        self.settings.set("shadows", bool(on))
        self.update()

    def set_tool(self, tool: str) -> None:
        if tool == "crop":
            self.enter_crop()
            return
        if self.crop_rect is not None:
            self.apply_crop()
        if self.editing:
            self.finish_edit()
        self.tool = tool
        self.settings.set("tool", tool)
        self.pending = None
        if tool != "select":
            self.selection = []
            self.selectionChanged.emit()
        self.toolChanged.emit(tool)
        self._update_cursor()
        self.update()

    def cycle_variant(self, tool: str) -> None:
        if tool not in VARIANTS:
            return
        key, values = VARIANTS[tool]
        cur = self.style_.get(key)
        nxt = values[(values.index(cur) + 1) % len(values)] if cur in values else values[0]
        self.set_style(key, nxt)
        self.message.emit({"rect": "Rectangle", "ellipse": "Ellipse", "tapered": "Tapered arrow",
                           "line": "Line arrow", "double": "Double arrow", "plain": "Plain line",
                           "pixelate": "Pixelate", "blur": "Blur", "label": "Label text",
                           "outline": "Outlined text"}.get(nxt, nxt.title()))

    # ------------------------------------------------------------------ crop
    def enter_crop(self) -> None:
        if self.crop_rect is not None:
            return
        if self.editing:
            self.finish_edit()
        self.selection, self.pending = [], None
        self.crop_rect = QRectF(self.doc.output_rect())
        self._crop_prev_tool = self.tool if self.tool != "crop" else "arrow"
        self.tool = "crop"
        self.fit()
        self.cropModeChanged.emit(True)
        self.toolChanged.emit("crop")
        self._update_cursor()

    def _leave_crop(self) -> None:
        self.crop_rect = None
        self.drag = None
        self.tool = self._crop_prev_tool
        self.fit()
        self.cropModeChanged.emit(False)
        self.toolChanged.emit(self.tool)
        self._update_cursor()

    def apply_crop(self) -> None:
        if self.crop_rect is None:
            return
        full = self.doc.full_rect()
        r = QRectF(self.crop_rect.normalized().intersected(full).toAlignedRect())
        self.doc.crop = None if (r.width() < 2 or r.height() < 2 or r == full) else r
        self.doc.relayout()
        self._leave_crop()
        self.commit()

    def cancel_crop(self) -> None:
        if self.crop_rect is not None:
            self._leave_crop()

    def reset_crop(self) -> None:
        if self.crop_rect is not None:
            self.crop_rect = QRectF(self.doc.full_rect())
            self.update()

    def _clamp_img(self, p: QPointF) -> QPointF:
        r = self.doc.full_rect()
        return QPointF(min(max(p.x(), 0.0), r.width()), min(max(p.y(), 0.0), r.height()))

    def _crop_handle_at(self, pos: QPointF) -> str | None:
        for name, pt in box_handles(self.crop_rect):
            q = self.to_scr(pt)
            if abs(q.x() - pos.x()) <= 10 and abs(q.y() - pos.y()) <= 10:
                return name
        return None

    # ------------------------------------------------------------------ editing text
    def start_edit(self, t: TextAnn, new: bool = False, initial: str | None = None) -> None:
        if self.editing:
            self.finish_edit()
        self.pending = None
        self.editing = t
        self.edit_new = new
        self.selection = [t.id]
        self.doc.ctx.editing_id = t.id
        ed = LabelEditor(self)
        ed.setPlainText(t.text)
        ed.commit.connect(self.finish_edit)
        ed.textChanged.connect(self._edit_changed)
        self.editor = ed
        self._position_editor()
        ed.show()
        ed.setFocus()
        cur = ed.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        ed.setTextCursor(cur)
        if initial:
            ed.insertPlainText(initial)
        self.selectionChanged.emit()
        self.styleChanged.emit()
        self.update()

    def _edit_changed(self) -> None:
        if self.editing and self.editor:
            self.editing.text = self.editor.toPlainText()
            self.doc.relayout()
            self._position_editor()
            self.update()

    def _position_editor(self) -> None:
        if not (self.editor and self.editing):
            return
        t = self.editing
        L = t.layout(self.doc.ctx)
        f = QFont(L.font)
        f.setPointSizeF(max(1.0, L.fs * self.zoom * 72.0 / max(1, self.logicalDpiY())))
        self.editor.setFont(f)
        col = contrast_color(t.color) if t.style == "label" else QColor(t.color)
        self.editor.setStyleSheet(
            "QPlainTextEdit{background:transparent;border:none;padding:0px;"
            f"color:{col.name()};selection-background-color:rgba(10,132,255,140);selection-color:white;}}")
        top_left = self.to_scr(QPointF(t.pos.x() + L.px, t.pos.y() + L.py))
        w = (L.w - 2 * L.px) * self.zoom + 40
        h = (L.h - 2 * L.py) * self.zoom + 6
        self.editor.setGeometry(QRect(round(top_left.x()), round(top_left.y()), max(12, round(w)), max(8, round(h))))

    def finish_edit(self) -> None:
        if not self.editing:
            return
        t, ed = self.editing, self.editor
        self.editing, self.editor = None, None
        self.doc.ctx.editing_id = None
        text = ed.toPlainText().rstrip() if ed else t.text
        if ed:
            ed.hide()
            ed.deleteLater()
        if not text.strip():
            self.doc.remove(t.id)
            self.selection = [i for i in self.selection if i != t.id]
        else:
            t.text = text
        self.doc.relayout()
        self.commit()
        self.setFocus()
        self.selectionChanged.emit()
        self.update()

    # ------------------------------------------------------------------ item ops
    def selected_items(self):
        return [a for a in (self.doc.find(i) for i in self.selection) if a]

    def delete_selection(self) -> None:
        if not self.selection:
            return
        for aid in list(self.selection):
            self.doc.remove(aid)
        self.selection = []
        self.pending = None
        self.doc.relayout()
        self.commit()
        self.selectionChanged.emit()

    def select_all(self) -> None:
        self.selection = [a.id for a in self.doc.items]
        self.selectionChanged.emit()
        self.update()

    def duplicate_selection(self) -> None:
        items = self.selected_items()
        if not items:
            return
        off = 14 * self.doc.unit
        new_ids = []
        for a in items:
            d = a.to_dict()
            d.pop("id")
            from .annotations import annotation_from_dict
            n = annotation_from_dict(d)
            if isinstance(n, TextAnn):
                n.link = None
            n.translate(off, off)
            self.doc.items.append(n)
            new_ids.append(n.id)
        self.selection = new_ids
        self.commit()
        self.selectionChanged.emit()

    def reorder(self, front: bool) -> None:
        sel = set(self.selection)
        moving = [a for a in self.doc.items if a.id in sel]
        rest = [a for a in self.doc.items if a.id not in sel]
        self.doc.items = rest + moving if front else moving + rest
        self.commit()

    def clear_all(self) -> None:
        self._end_transients()
        self.doc.items = []
        self.selection = []
        self.commit()
        self.selectionChanged.emit()

    def add_items(self, items) -> None:
        for a in items:
            self.doc.items.append(a)
        self.commit()
        self.update()

    def _nudge(self, dx: float, dy: float) -> None:
        for a in self.selected_items():
            if isinstance(a, TextAnn) and a.link and a.link not in self.selection:
                a.link = None
            a.translate(dx, dy)
        self.doc.relayout()
        self.commit()

    def _set_pending(self, aid: str) -> None:
        self.pending = aid
        self.hint_until = time.monotonic() + 3.0
        self._hint_timer.start(3050)

    def _label_pending(self, txt: str) -> None:
        target = self.doc.find(self.pending)
        self.pending = None
        if target is None:
            return
        t = TextAnn(pos=QPointF(0, 0), text="", style=self.style_["text_style"], color=target.color,
                    size=target.size, link=target.id)
        self.doc.items.append(t)
        self.doc.relayout()
        self.start_edit(t, new=True, initial=txt)

    # ------------------------------------------------------------------ hit tests
    def _handle_at(self, pos: QPointF):
        if len(self.selection) != 1 or self.editing:
            return None
        a = self.doc.find(self.selection[0])
        if a is None:
            return None
        best, best_d = None, 9.0
        for name, pt in a.handles(self.doc.ctx):
            q = self.to_scr(pt)
            d = math.hypot(q.x() - pos.x(), q.y() - pos.y())
            if d <= best_d and not (name == "mid" and best is not None):
                best, best_d = name, d
        return (a, best) if best else None

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, e):
        self.setFocus()
        pos = e.position()
        ip = self.to_img(pos)
        btn = e.button()
        mods = e.modifiers()
        if btn == Qt.MouseButton.MiddleButton or (btn == Qt.MouseButton.LeftButton and self.space):
            self.drag = {"mode": "pan", "last": pos}
            self._update_cursor()
            return
        if self.editing:
            self.finish_edit()
            return
        if btn == Qt.MouseButton.RightButton:
            if self.crop_rect is None:
                self._context_menu(pos, ip)
            return
        if btn != Qt.MouseButton.LeftButton:
            return
        if self.crop_rect is not None:
            self._crop_press(pos, ip)
            return
        self.pending = None
        h = self._handle_at(pos)
        if h:
            a, name = h
            self.drag = {"mode": "handle", "id": a.id, "name": name, "orig": a.clone()}
            return
        if self.tool == "eraser":
            self.drag = {"mode": "erase", "hit": False}
            self._erase_at(ip)
            return
        hit = self.doc.item_at(ip, self.tol())
        if self.tool == "select":
            if hit is None:
                base = list(self.selection) if mods & SHIFT else []
                self.drag = {"mode": "rubber", "start": pos, "cur": pos, "base": base}
                self.selection = list(base)
            elif mods & SHIFT:
                if hit.id in self.selection:
                    self.selection.remove(hit.id)
                else:
                    self.selection.append(hit.id)
            else:
                if hit.id not in self.selection:
                    self.selection = [hit.id]
                self._begin_move(ip, pos)
            self.selectionChanged.emit()
            self.update()
            return
        if self.tool == "text":
            if hit is not None and hit.kind == "text":
                self.start_edit(hit)
                return
            t = TextAnn(pos=ip, text="", style=self.style_["text_style"], color=self.style_["color"],
                        size=self.style_["size"])
            L = t.layout(self.doc.ctx)
            t.pos = QPointF(ip.x() - L.px, ip.y() - L.h / 2)
            self.doc.items.append(t)
            self.start_edit(t, new=True)
            return
        if self.tool == "step":
            if hit is not None and hit.id in self.selection:
                self._begin_move(ip, pos)
                return
            s = StepAnn(center=ip, number=self.doc.next_step(), color=self.style_["color"], size=self.style_["size"])
            self.doc.items.append(s)
            self.selection = [s.id]
            self.commit()
            self._set_pending(s.id)
            self._begin_move(ip, pos, keep_pending=True)
            self.selectionChanged.emit()
            return
        if hit is not None and hit.id in self.selection:
            self._begin_move(ip, pos)
            return
        self._begin_create(ip, pos)

    def _begin_move(self, ip: QPointF, pos: QPointF, keep_pending: bool = False) -> None:
        unlinked = []
        for a in self.selected_items():
            if isinstance(a, TextAnn) and a.link and a.link not in self.selection:
                unlinked.append((a.id, a.link))
                a.link = None
        self.drag = {"mode": "move", "start": ip, "start_scr": pos, "moved": False, "unlinked": unlinked,
                     "orig": {a.id: a.clone() for a in self.selected_items()}, "keep_pending": keep_pending}

    def _begin_create(self, ip: QPointF, pos: QPointF) -> None:
        s = self.style_
        t = self.tool
        if t == "arrow":
            a = ArrowAnn(p1=ip, p2=ip, style=s["arrow_style"], color=s["color"], size=s["size"])
        elif t == "shape":
            a = ShapeAnn(rect=QRectF(ip, ip), shape=s["shape"], fill=s["shape_fill"], color=s["color"], size=s["size"])
        elif t in ("pen", "marker"):
            marker = t == "marker"
            a = PenAnn(points=[ip], marker=marker, color=s["marker_color"] if marker else s["color"], size=s["size"])
        elif t == "pixel":
            a = PixelAnn(rect=QRectF(ip, ip), mode=s["pixel_mode"], size=s["size"])
        elif t == "spot":
            a = SpotAnn(rect=QRectF(ip, ip), shape=s["spot_shape"], size=s["size"])
        else:
            return
        self.doc.items.append(a)
        self.selection = [a.id]
        self.drag = {"mode": "create", "id": a.id, "start": ip, "start_scr": pos}
        self.selectionChanged.emit()

    def _crop_press(self, pos: QPointF, ip: QPointF) -> None:
        name = self._crop_handle_at(pos)
        if name:
            self.drag = {"mode": "crop_handle", "name": name, "orig": QRectF(self.crop_rect)}
        elif self.rect_to_scr(self.crop_rect).contains(pos):
            self.drag = {"mode": "crop_move", "start": ip, "orig": QRectF(self.crop_rect)}
        else:
            self.drag = {"mode": "crop_new", "start": self._clamp_img(ip), "orig": QRectF(self.crop_rect)}

    def mouseMoveEvent(self, e):
        pos = e.position()
        ip = self.to_img(pos)
        mods = e.modifiers()
        self.hover_pos = pos
        d = self.drag
        if d is None:
            if self.crop_rect is None:
                hit = self.doc.item_at(ip, self.tol())
                self.hover = hit.id if hit else None
            self._update_cursor(pos)
            self.update()
            return
        mode = d["mode"]
        if mode == "pan":
            self.offset += pos - d["last"]
            d["last"] = pos
            self.fit_mode = False
            self._clamp()
            self._after_view_change()
            return
        if mode == "create":
            a = self.doc.find(d["id"])
            st = d["start"]
            if isinstance(a, ArrowAnn):
                a.p2 = snap_angle(a.p1, ip) if mods & SHIFT else ip
            elif isinstance(a, PenAnn):
                if mods & SHIFT:
                    a.points = [QPointF(st), ip]
                else:
                    last = a.points[-1]
                    if math.hypot(last.x() - ip.x(), last.y() - ip.y()) * self.zoom >= 1.5:
                        a.points.append(ip)
            elif a is not None:
                a.rect = rect_from_points(st, ip, square=bool(mods & SHIFT), from_center=bool(mods & ALT))
        elif mode == "move":
            delta = ip - d["start"]
            if mods & SHIFT:
                delta = QPointF(delta.x(), 0) if abs(delta.x()) >= abs(delta.y()) else QPointF(0, delta.y())
            sd = pos - d["start_scr"]
            if math.hypot(sd.x(), sd.y()) > 2:
                d["moved"] = True
            if d["moved"]:
                for aid, o in d["orig"].items():
                    idx = self.doc.index_of(aid)
                    if idx >= 0:
                        n = o.clone()
                        n.translate(delta.x(), delta.y())
                        self.doc.items[idx] = n
        elif mode == "handle":
            a = self.doc.find(d["id"])
            if a is not None:
                a.drag_handle(d["name"], ip, d["orig"], mods, self.doc.ctx)
        elif mode == "rubber":
            d["cur"] = pos
            r = QRectF(d["start"], pos).normalized()
            ir = QRectF(self.to_img(r.topLeft()), self.to_img(r.bottomRight()))
            ids = [a.id for a in self.doc.items if a.bounds(self.doc.ctx).intersects(ir)]
            self.selection = list(dict.fromkeys(d["base"] + ids))
            self.selectionChanged.emit()
        elif mode == "erase":
            self._erase_at(ip)
        elif mode == "crop_handle":
            self.crop_rect = resize_box(d["orig"], d["name"], self._clamp_img(ip), square=bool(mods & SHIFT))
        elif mode == "crop_move":
            delta = ip - d["start"]
            r = QRectF(d["orig"])
            full = self.doc.full_rect()
            dx = min(max(delta.x(), -r.left()), full.width() - r.right())
            dy = min(max(delta.y(), -r.top()), full.height() - r.bottom())
            r.translate(dx, dy)
            self.crop_rect = r
        elif mode == "crop_new":
            self.crop_rect = rect_from_points(d["start"], self._clamp_img(ip), square=bool(mods & SHIFT))
        self.doc.relayout()
        self.update()

    def mouseReleaseEvent(self, e):
        d = self.drag
        if d is None:
            return
        self.drag = None
        pos = e.position()
        mode = d["mode"]
        if mode == "create":
            a = self.doc.find(d["id"])
            sd = pos - d["start_scr"]
            moved = math.hypot(sd.x(), sd.y())
            if a is not None and (isinstance(a, PenAnn) or moved >= 4):
                self.commit()
                if a.labelable:
                    self._set_pending(a.id)
            else:
                self.doc.remove(d["id"])
                hit = self.doc.item_at(self.to_img(pos), self.tol())
                self.selection = [hit.id] if hit else []
                self.selectionChanged.emit()
        elif mode == "move":
            if d["moved"]:
                self.doc.relayout()
                self.commit()
            else:
                for tid, link in d["unlinked"]:
                    t = self.doc.find(tid)
                    if t is not None:
                        t.link = link
            if not d.get("keep_pending"):
                self.pending = None
        elif mode in ("handle", "erase"):
            if mode == "handle" or d["hit"]:
                self.doc.relayout()
                self.commit()
        elif mode == "crop_new":
            r = self.crop_rect
            if r is None or r.width() * self.zoom < 6 or r.height() * self.zoom < 6:
                self.crop_rect = d["orig"]
        self._update_cursor(pos)
        self.update()

    def mouseDoubleClickEvent(self, e):
        if self.crop_rect is not None:
            self.apply_crop()
            return
        ip = self.to_img(e.position())
        hit = self.doc.item_at(ip, self.tol())
        if isinstance(hit, TextAnn):
            self.drag = None
            self.start_edit(hit)
        elif isinstance(hit, ArrowAnn) and hit.ctrl is not None:
            hit.ctrl = None
            self.doc.relayout()
            self.commit()

    def wheelEvent(self, e):
        mods = e.modifiers()
        if mods & CTRL:
            steps = e.angleDelta().y() / 120.0
            self.set_zoom(self.zoom * (1.18 ** steps), e.position())
            return
        delta = e.pixelDelta()
        if delta.isNull():
            ad = e.angleDelta()
            delta = ad / 2.4
        dx, dy = float(delta.x()), float(delta.y())
        if mods & SHIFT and dx == 0:
            dx, dy = dy, 0.0
        self.offset += QPointF(dx, dy)
        self.fit_mode = False
        self._clamp()
        self._after_view_change()

    def leaveEvent(self, e):
        self.hover = None
        self.hover_pos = None
        self.update()

    def _erase_at(self, ip: QPointF) -> None:
        for a in list(reversed(self.doc.paint_order())):
            if a.hit(ip, self.tol() * 2, self.doc.ctx):
                self.doc.remove(a.id)
                self.selection = [i for i in self.selection if i != a.id]
                if self.drag:
                    self.drag["hit"] = True

    def _context_menu(self, pos: QPointF, ip: QPointF) -> None:
        hit = self.doc.item_at(ip, self.tol())
        if hit is None:
            return
        if hit.id not in self.selection:
            self.selection = [hit.id]
            self.selectionChanged.emit()
            self.update()
        m = make_menu(self)
        if isinstance(hit, TextAnn):
            m.addAction("Edit text", lambda: self.start_edit(hit))
        if isinstance(hit, ArrowAnn) and hit.ctrl is not None:
            def straighten():
                hit.ctrl = None
                self.doc.relayout()
                self.commit()
            m.addAction("Straighten arrow", straighten)
        m.addAction("Duplicate\tCtrl+D", self.duplicate_selection)
        m.addAction("Bring to front", lambda: self.reorder(True))
        m.addAction("Send to back", lambda: self.reorder(False))
        m.addSeparator()
        m.addAction("Delete\tDel", self.delete_selection)
        m.exec(self.mapToGlobal(pos.toPoint()))

    # ------------------------------------------------------------------ keys
    def keyPressEvent(self, e):
        k = e.key()
        mods = e.modifiers()
        ctrl, alt, shift = bool(mods & CTRL), bool(mods & ALT), bool(mods & SHIFT)
        if self.crop_rect is not None:
            if k in (K.Key_Return, K.Key_Enter):
                self.apply_crop()
            elif k == K.Key_Escape:
                self.cancel_crop()
            elif k == K.Key_C and not ctrl:
                self.apply_crop()
            return
        if k == K.Key_Space and not e.isAutoRepeat() and not self.pending:
            self.space = True
            self._update_cursor()
            return
        if self.pending and not ctrl and not alt:
            txt = e.text()
            if txt and txt.isprintable() and txt.strip():
                self._label_pending(txt)
                return
        if k == K.Key_Escape:
            if self.drag and self.drag["mode"] == "create":
                self.doc.remove(self.drag["id"])
                self.drag = None
            self.pending = None
            self.selection = []
            self.selectionChanged.emit()
            self.update()
            return
        if k in (K.Key_Delete, K.Key_Backspace):
            self.delete_selection()
            return
        arrows = {K.Key_Left: (-1, 0), K.Key_Right: (1, 0), K.Key_Up: (0, -1), K.Key_Down: (0, 1)}
        if k in arrows and self.selection:
            step = (10 if shift else 1) * self.doc.unit
            dx, dy = arrows[k]
            self._nudge(dx * step, dy * step)
            return
        if ctrl and not alt and k == K.Key_A:
            self.select_all()
            return
        if ctrl and not alt and k == K.Key_D:
            self.duplicate_selection()
            return
        if not ctrl and not alt:
            if k in TOOL_KEYS:
                tool = TOOL_KEYS[k]
                if tool == self.tool and tool in VARIANTS:
                    self.cycle_variant(tool)
                else:
                    self.set_tool(tool)
                return
            if k in (K.Key_BracketLeft, K.Key_BracketRight):
                self.set_style("size", max(0, min(3, self.style_["size"] + (1 if k == K.Key_BracketRight else -1))))
                return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == K.Key_Space and not e.isAutoRepeat():
            self.space = False
            self._update_cursor()
            return
        super().keyReleaseEvent(e)

    def focusOutEvent(self, e):
        if self.space:
            self.space = False
            self._update_cursor()
        super().focusOutEvent(e)

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, e):
        src = e.source()
        if src is not None and src.objectName() == "SnipThumb":
            e.ignore()  # our own corner thumbnail passing over the editor on its way to another app
            return
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            if url.isLocalFile():
                self.fileDropped.emit(url.toLocalFile())
                break

    # ------------------------------------------------------------------ cursor
    def _update_cursor(self, pos: QPointF | None = None) -> None:
        if pos is None:
            pos = self.hover_pos or QPointF(-100, -100)
        if self.drag and self.drag["mode"] == "pan":
            self.setCursor(C.ClosedHandCursor)
            return
        if self.space:
            self.setCursor(C.OpenHandCursor)
            return
        if self.crop_rect is not None:
            name = self._crop_handle_at(pos)
            if name:
                self.setCursor(BOX_CURSORS[name])
            elif self.rect_to_scr(self.crop_rect).contains(pos):
                self.setCursor(C.SizeAllCursor)
            else:
                self.setCursor(C.CrossCursor)
            return
        h = self._handle_at(pos)
        if h:
            self.setCursor(BOX_CURSORS.get(h[1], C.SizeAllCursor))
            return
        ip = self.to_img(pos)
        hit = self.doc.item_at(ip, self.tol())
        if self.tool == "select":
            self.setCursor(C.SizeAllCursor if hit else C.ArrowCursor)
        elif self.tool == "text":
            self.setCursor(C.IBeamCursor)
        elif self.tool == "eraser":
            self.setCursor(C.BlankCursor)
        elif hit is not None and hit.id in self.selection:
            self.setCursor(C.SizeAllCursor)
        else:
            self.setCursor(C.CrossCursor)

    # ------------------------------------------------------------------ painting
    def _build_cache(self) -> None:
        z = self.zoom * self.dpr()
        if z >= 0.999:
            self._base_cache = None
            return
        img = self.doc.image
        w, h = max(1, round(img.width() * z)), max(1, round(img.height() * z))
        pm = QPixmap.fromImage(img.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
        pm.setDevicePixelRatio(self.dpr())
        self._base_cache = ((round(self.zoom, 6), round(self.dpr(), 3)), pm)
        self.update()

    def _draw_base(self, p: QPainter) -> None:
        z = self.zoom * self.dpr()
        if z < 0.999:
            key = (round(self.zoom, 6), round(self.dpr(), 3))
            if self._base_cache and self._base_cache[0] == key:
                p.drawPixmap(self.offset, self._base_cache[1])
                return
            self._cache_timer.start(90)
        p.save()
        p.translate(self.offset)
        p.scale(self.zoom, self.zoom)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, z < 1.999)
        p.drawImage(QPointF(0, 0), self.doc.image)
        p.restore()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), BG)
        a_scr = self.rect_to_scr(self.view_area())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i in range(1, 7):  # soft frame shadow behind the image
            p.setPen(QPen(QColor(0, 0, 0, 34 - i * 5), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(a_scr.adjusted(-i, -i + 2, i, i + 2), i, i)
        if self.doc.has_alpha:
            self._paint_checker(p, a_scr)
        p.save()
        p.setClipRect(a_scr)
        self._draw_base(p)
        p.translate(self.offset)
        p.scale(self.zoom, self.zoom)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom * self.dpr() < 1.999)
        self.doc.paint(p, draw_base=False)
        p.restore()
        if self.crop_rect is not None:
            self._paint_crop(p, a_scr)
        else:
            self._paint_overlays(p)
        p.end()

    def _paint_checker(self, p: QPainter, r: QRectF) -> None:
        tile = QPixmap(16, 16)
        tile.fill(QColor("#2a2a2e"))
        tp = QPainter(tile)
        tp.fillRect(0, 0, 8, 8, QColor("#36363b"))
        tp.fillRect(8, 8, 8, 8, QColor("#36363b"))
        tp.end()
        p.fillRect(r, QBrush(tile))

    def _draw_handle(self, p: QPainter, pt: QPointF, small: bool = False) -> None:
        r = 4.5 if small else 5.5
        p.setPen(QPen(QColor("#FFFFFF") if small else ACCENT, 1.6))
        p.setBrush(ACCENT if small else QColor("#FFFFFF"))
        p.drawEllipse(pt, r, r)

    def _dashed_pen(self, alpha: int = 255) -> QPen:
        c = QColor(ACCENT)
        c.setAlpha(alpha)
        pen = QPen(c, 1.2)
        pen.setDashPattern([4, 3])
        return pen

    def _paint_overlays(self, p: QPainter) -> None:
        ctx = self.doc.ctx
        if self.hover and self.drag is None and self.hover not in self.selection and self.tool in ("select", "eraser"):
            a = self.doc.find(self.hover)
            if a is not None:
                c = QColor("#FF453A") if self.tool == "eraser" else QColor(ACCENT)
                c.setAlpha(150)
                p.setPen(QPen(c, 1.2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(self.rect_to_scr(a.bounds(ctx)).adjusted(-3, -3, 3, 3), 3, 3)
        sel = self.selected_items()
        creating = self.drag is not None and self.drag["mode"] == "create"
        p.setBrush(Qt.BrushStyle.NoBrush)
        for a in sel:
            if a.kind == "arrow" and not self.editing:
                continue
            if creating and a.kind in ("pen",):
                continue
            p.setPen(self._dashed_pen())
            p.drawRoundedRect(self.rect_to_scr(a.bounds(ctx)).adjusted(-3, -3, 3, 3), 3, 3)
        if len(sel) == 1 and not creating and not self.editing:
            for name, pt in sel[0].handles(ctx):
                self._draw_handle(p, self.to_scr(pt), small=(name == "mid"))
        if self.drag and self.drag["mode"] == "rubber":
            r = QRectF(self.drag["start"], self.drag["cur"]).normalized()
            fill = QColor(ACCENT)
            fill.setAlpha(36)
            p.setPen(QPen(ACCENT, 1.0))
            p.setBrush(fill)
            p.drawRect(r)
        if self.tool == "eraser" and self.hover_pos is not None:
            p.setPen(QPen(QColor(255, 255, 255, 220), 1.4))
            p.setBrush(QColor(255, 69, 58, 40))
            p.drawEllipse(self.hover_pos, 9, 9)
        if self.pending and time.monotonic() < self.hint_until and self.drag is None:
            self._paint_hint(p)

    def _paint_hint(self, p: QPainter) -> None:
        a = self.doc.find(self.pending)
        if a is None:
            return
        anchor = a.label_anchor(self.doc.ctx, self.doc.output_rect(), QSizeF(0, 0))
        if anchor is None:
            return
        pt, dx, dy, gap = anchor
        q = self.to_scr(pt)
        text = "Type to add a label"
        f = QFont("Segoe UI")
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        w = p.fontMetrics().horizontalAdvance(text) + 20
        h = 24
        cx = q.x() + dx * (14 + w / 2 * abs(dx) + h / 2 * abs(dy))
        cy = q.y() + dy * (14 + w / 2 * abs(dx) + h / 2 * abs(dy))
        box = QRectF(cx - w / 2, cy - h / 2, w, h)
        box.moveLeft(min(max(box.left(), 6), self.width() - w - 6))
        box.moveTop(min(max(box.top(), 6), self.height() - h - 6))
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(20, 20, 22, 225))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(QColor("#EDEDF0"))
        p.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)

    def _paint_crop(self, p: QPainter, a_scr: QRectF) -> None:
        cr = self.rect_to_scr(self.crop_rect)
        dim = QPainterPath()
        dim.addRect(a_scr)
        hole = QPainterPath()
        hole.addRect(cr)
        p.fillPath(dim.subtracted(hole), QBrush(QColor(0, 0, 0, 160)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.drag:
            p.setPen(QPen(QColor(255, 255, 255, 80), 1))
            for i in (1, 2):
                x = cr.left() + cr.width() * i / 3
                y = cr.top() + cr.height() * i / 3
                p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
                p.drawLine(QPointF(cr.left(), y), QPointF(cr.right(), y))
        p.setPen(QPen(QColor(255, 255, 255, 230), 1.2))
        p.drawRect(cr)
        pen = QPen(QColor("#FFFFFF"), 4)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        L = min(20.0, cr.width() / 3, cr.height() / 3)
        for (x, y, sx, sy) in ((cr.left(), cr.top(), 1, 1), (cr.right(), cr.top(), -1, 1),
                               (cr.right(), cr.bottom(), -1, -1), (cr.left(), cr.bottom(), 1, -1)):
            p.drawLine(QPointF(x - sx * 1.5, y), QPointF(x + sx * L, y))
            p.drawLine(QPointF(x, y - sy * 1.5), QPointF(x, y + sy * L))
        for name, pt in box_handles(self.crop_rect):
            if len(name) == 1:
                q = self.to_scr(pt)
                horiz = name in ("t", "b")
                p.drawLine(q - QPointF(8 if horiz else 0, 0 if horiz else 8), q + QPointF(8 if horiz else 0, 0 if horiz else 8))
        r = self.crop_rect.toAlignedRect()
        label = f"{r.width():,} \u00d7 {r.height():,}"
        f = QFont("Segoe UI")
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        w = p.fontMetrics().horizontalAdvance(label) + 16
        box = QRectF(cr.left(), cr.top() - 30, w, 22)
        if box.top() < 4:
            box.moveTop(cr.top() + 8)
            box.moveLeft(cr.left() + 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 20, 22, 220))
        p.drawRoundedRect(box, 6, 6)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(box, int(Qt.AlignmentFlag.AlignCenter), label)
