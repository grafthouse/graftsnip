"""Annotation model and rendering for GraftSnip.

All geometry is stored in *image pixels* of the original capture, so the zoom
level never changes what gets exported. Sizes are presets (S/M/L/XL) that are
multiplied by the document's ``unit`` (the capture's device-pixel ratio), so a
"medium" arrow looks the same on a 100% and a 200% display.
"""
from __future__ import annotations

import bisect
import math
import uuid
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
)

SIZES: dict[str, tuple[float, ...]] = {
    "stroke": (2.0, 3.5, 6.0, 9.0),
    "arrow": (5.0, 8.0, 12.0, 17.0),
    "pen": (2.0, 4.0, 7.0, 11.0),
    "marker": (12.0, 18.0, 28.0, 40.0),
    "font": (14.0, 19.0, 27.0, 38.0),
    "step": (11.0, 15.0, 20.0, 27.0),
    "pixel": (6.0, 10.0, 16.0, 26.0),
}
SIZE_LABELS = ("S", "M", "L", "XL")
PALETTE = ("#FF3B30", "#FF9500", "#FFD60A", "#34C759", "#0A84FF", "#BF5AF2", "#FFFFFF", "#1C1C1E")

ROUND_CAP = Qt.PenCapStyle.RoundCap
ROUND_JOIN = Qt.PenJoinStyle.RoundJoin


# --------------------------------------------------------------------------- helpers
def size_value(table: str, index: int, unit: float) -> float:
    values = SIZES[table]
    return values[max(0, min(len(values) - 1, int(index)))] * unit


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def contrast_color(color) -> QColor:
    """Black or white, whichever reads better on ``color``."""
    c = QColor(color)
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return QColor("#111111") if lum > 165 else QColor("#FFFFFF")


def _pt(v) -> QPointF:
    return QPointF(float(v[0]), float(v[1]))


def _xy(p: QPointF) -> list[float]:
    return [round(p.x(), 2), round(p.y(), 2)]


def _rect(v) -> QRectF:
    return QRectF(float(v[0]), float(v[1]), float(v[2]), float(v[3]))


def _rl(r: QRectF) -> list[float]:
    return [round(r.x(), 2), round(r.y(), 2), round(r.width(), 2), round(r.height(), 2)]


def _len(p: QPointF) -> float:
    return math.hypot(p.x(), p.y())


def _unit_vec(p: QPointF) -> QPointF:
    n = _len(p)
    return QPointF(p.x() / n, p.y() / n) if n > 1e-9 else QPointF(1.0, 0.0)


def _perp(p: QPointF) -> QPointF:
    return QPointF(-p.y(), p.x())


def snap_angle(origin: QPointF, pt: QPointF, step_deg: float = 15.0) -> QPointF:
    d = pt - origin
    length = _len(d)
    if length < 1e-6:
        return QPointF(pt)
    step = math.radians(step_deg)
    a = round(math.atan2(d.y(), d.x()) / step) * step
    return QPointF(origin.x() + math.cos(a) * length, origin.y() + math.sin(a) * length)


def rect_from_points(a: QPointF, b: QPointF, square: bool = False, from_center: bool = False) -> QRectF:
    dx, dy = b.x() - a.x(), b.y() - a.y()
    if square:
        m = max(abs(dx), abs(dy))
        dx, dy = math.copysign(m, dx or 1.0), math.copysign(m, dy or 1.0)
    if from_center:
        return QRectF(a.x() - abs(dx), a.y() - abs(dy), 2 * abs(dx), 2 * abs(dy))
    return QRectF(a, QPointF(a.x() + dx, a.y() + dy)).normalized()


def box_handles(r: QRectF) -> list[tuple[str, QPointF]]:
    cx, cy = r.center().x(), r.center().y()
    return [
        ("tl", r.topLeft()), ("t", QPointF(cx, r.top())), ("tr", r.topRight()),
        ("r", QPointF(r.right(), cy)), ("br", r.bottomRight()), ("b", QPointF(cx, r.bottom())),
        ("bl", r.bottomLeft()), ("l", QPointF(r.left(), cy)),
    ]


def resize_box(orig: QRectF, handle: str, pt: QPointF, square: bool = False) -> QRectF:
    left, top, right, bottom = orig.left(), orig.top(), orig.right(), orig.bottom()
    if "l" in handle:
        left = pt.x()
    if "r" in handle:
        right = pt.x()
    if handle.startswith("t"):
        top = pt.y()
    if handle.startswith("b"):
        bottom = pt.y()
    if square and len(handle) == 2:
        w, h = right - left, bottom - top
        m = max(abs(w), abs(h))
        if "l" in handle:
            left = right - math.copysign(m, w or 1.0)
        else:
            right = left + math.copysign(m, w or 1.0)
        if handle.startswith("t"):
            top = bottom - math.copysign(m, h or 1.0)
        else:
            bottom = top + math.copysign(m, h or 1.0)
    return QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()


def stroke_path(path: QPainterPath, width: float, cap=ROUND_CAP, join=ROUND_JOIN) -> QPainterPath:
    s = QPainterPathStroker()
    s.setWidth(max(0.01, width))
    s.setCapStyle(cap)
    s.setJoinStyle(join)
    return s.createStroke(path)


def draw_soft_shadow(p: QPainter, path: QPainterPath, unit: float, strength: float = 1.0) -> None:
    """Cheap soft drop shadow: a few widening translucent strokes under the shape."""
    if path.isEmpty():
        return
    p.save()
    p.translate(0.0, 1.4 * unit)
    for width, alpha in ((6.0, 0.035), (3.6, 0.05), (1.8, 0.07)):
        pen = QPen(QColor(0, 0, 0, int(255 * alpha * strength)))
        pen.setWidthF(width * unit)
        pen.setCapStyle(ROUND_CAP)
        pen.setJoinStyle(ROUND_JOIN)
        p.strokePath(path, pen)
    p.fillPath(path, QBrush(QColor(0, 0, 0, int(255 * 0.16 * strength))))
    p.restore()


@dataclass
class RenderContext:
    image: QImage
    unit: float = 1.0
    shadows: bool = True
    editing_id: str | None = None
    cache: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- base
class Annotation:
    kind = "base"
    labelable = False  # typing right after drawing one creates a linked label

    def __init__(self, color: str = PALETTE[0], size: int = 1, id: str | None = None):
        self.id = id or new_id()
        self.color = color
        self.size = int(size)

    # serialisation -----------------------------------------------------------
    def to_dict(self) -> dict:
        d = {"kind": self.kind, "id": self.id, "color": self.color, "size": self.size}
        d.update(self._data())
        return d

    def _data(self) -> dict:
        return {}

    @classmethod
    def _base(cls, d: dict) -> dict:
        return {"color": d.get("color", PALETTE[0]), "size": d.get("size", 1), "id": d.get("id")}

    def clone(self) -> "Annotation":
        return annotation_from_dict(self.to_dict())

    # geometry ----------------------------------------------------------------
    def shape_path(self, ctx: RenderContext) -> QPainterPath:
        return QPainterPath()

    def bounds(self, ctx: RenderContext) -> QRectF:
        return self.shape_path(ctx).boundingRect()

    def hit(self, pt: QPointF, tol: float, ctx: RenderContext) -> bool:
        path = self.shape_path(ctx)
        if path.contains(pt):
            return True
        return tol > 0 and stroke_path(path, tol * 2).contains(pt)

    def handles(self, ctx: RenderContext) -> list[tuple[str, QPointF]]:
        return []

    def drag_handle(self, name: str, pt: QPointF, orig: "Annotation", mods, ctx: RenderContext) -> None:
        pass

    def translate(self, dx: float, dy: float) -> None:
        pass

    def paint(self, p: QPainter, ctx: RenderContext) -> None:
        pass

    def label_anchor(self, ctx: RenderContext, area: QRectF, size: QSizeF):
        """(point, dx, dy, gap) a linked label should sit beyond, or None."""
        return None


# --------------------------------------------------------------------------- shapes
class ShapeAnn(Annotation):
    kind = "shape"
    labelable = True

    def __init__(self, rect: QRectF | None = None, shape: str = "rect", fill: bool = False, **kw):
        super().__init__(**kw)
        self.rect = QRectF(rect) if rect is not None else QRectF()
        self.shape = shape
        self.fill = bool(fill)

    def _data(self):
        return {"rect": _rl(self.rect), "shape": self.shape, "fill": self.fill}

    @classmethod
    def _from(cls, d):
        return cls(rect=_rect(d["rect"]), shape=d.get("shape", "rect"), fill=d.get("fill", False), **cls._base(d))

    def width(self, ctx):
        return size_value("stroke", self.size, ctx.unit)

    def outline(self, ctx) -> QPainterPath:
        path = QPainterPath()
        r = self.rect
        if self.shape == "ellipse":
            path.addEllipse(r)
        else:
            rad = min(self.width(ctx) * 1.6, r.width() / 2, r.height() / 2)
            path.addRoundedRect(r, rad, rad)
        return path

    def shape_path(self, ctx):
        o = self.outline(ctx)
        s = stroke_path(o, self.width(ctx))
        return s.united(o) if self.fill else s

    def hit(self, pt, tol, ctx):
        if self.fill and self.outline(ctx).contains(pt):
            return True
        return stroke_path(self.outline(ctx), self.width(ctx) + tol * 2).contains(pt)

    def bounds(self, ctx):
        w = self.width(ctx) / 2
        return self.rect.adjusted(-w, -w, w, w)

    def paint(self, p, ctx):
        c = QColor(self.color)
        o = self.outline(ctx)
        if ctx.shadows:
            draw_soft_shadow(p, self.shape_path(ctx), ctx.unit, 0.8)
        if self.fill:
            fc = QColor(c)
            fc.setAlphaF(0.22)
            p.fillPath(o, QBrush(fc))
        pen = QPen(c, self.width(ctx))
        pen.setJoinStyle(ROUND_JOIN)
        p.strokePath(o, pen)

    def handles(self, ctx):
        return box_handles(self.rect)

    def drag_handle(self, name, pt, orig, mods, ctx):
        self.rect = resize_box(orig.rect, name, pt, square=bool(mods & Qt.KeyboardModifier.ShiftModifier))

    def translate(self, dx, dy):
        self.rect.translate(dx, dy)

    def label_anchor(self, ctx, area, size):
        w = self.width(ctx)
        gap = w * 1.5 + 3 * ctx.unit
        cx = self.rect.center().x()
        if self.rect.bottom() + w / 2 + gap + size.height() <= area.bottom():
            return QPointF(cx, self.rect.bottom() + w / 2), 0.0, 1.0, gap
        return QPointF(cx, self.rect.top() - w / 2), 0.0, -1.0, gap


# --------------------------------------------------------------------------- arrow
def _cumulative(points: list[QPointF]) -> list[float]:
    cum = [0.0]
    for a, b in zip(points, points[1:]):
        cum.append(cum[-1] + _len(b - a))
    return cum


def _point_at(points: list[QPointF], cum: list[float], s: float) -> QPointF:
    if s <= 0:
        return QPointF(points[0])
    if s >= cum[-1]:
        return QPointF(points[-1])
    i = max(0, min(len(points) - 2, bisect.bisect_right(cum, s) - 1))
    seg = cum[i + 1] - cum[i]
    t = (s - cum[i]) / seg if seg > 0 else 0.0
    return points[i] + (points[i + 1] - points[i]) * t


def _sub_polyline(points, cum, s0, s1) -> list[QPointF]:
    out = [_point_at(points, cum, s0)]
    out.extend(QPointF(p) for p, c in zip(points, cum) if s0 < c < s1)
    out.append(_point_at(points, cum, s1))
    return out


def _ribbon(pts: list[QPointF], widths: list[float]) -> QPolygonF:
    left, right = [], []
    n = len(pts)
    for i in range(n):
        if i == 0:
            t = pts[1] - pts[0]
        elif i == n - 1:
            t = pts[-1] - pts[-2]
        else:
            t = pts[i + 1] - pts[i - 1]
        nrm = _perp(_unit_vec(t))
        h = widths[i] / 2
        left.append(pts[i] + nrm * h)
        right.append(pts[i] - nrm * h)
    return QPolygonF(left + right[::-1])


def _head(tip: QPointF, d: QPointF, length: float, half: float) -> QPainterPath:
    n = _perp(d)
    base = tip - d * length
    notch = tip - d * (length * 0.78)
    path = QPainterPath()
    path.addPolygon(QPolygonF([tip, base + n * half, notch, base - n * half]))
    path.closeSubpath()
    return path


def arrow_path(points: list[QPointF], W: float, style: str) -> QPainterPath:
    if len(points) < 2:
        return QPainterPath()
    cum = _cumulative(points)
    L = cum[-1]
    if L < 0.5:
        return QPainterPath()
    if style == "plain":
        line = QPainterPath(points[0])
        for q in points[1:]:
            line.lineTo(q)
        return stroke_path(line, W * 0.7)

    head_len, head_half = (W * 3.0, W * 1.55) if style == "tapered" else (W * 2.6, W * 1.35)
    limit = L * (0.45 if style == "double" else 0.6)
    if head_len > limit:
        k = limit / head_len
        head_len, head_half = head_len * k, head_half * k

    tip = points[-1]
    path = _head(tip, _unit_vec(tip - _point_at(points, cum, L - head_len)), head_len, head_half)
    s0, s1 = 0.0, L - head_len * 0.7
    if style == "double":
        tail = points[0]
        path = path.united(_head(tail, _unit_vec(tail - _point_at(points, cum, head_len)), head_len, head_half))
        s0 = head_len * 0.7
    if s1 <= s0:
        return path
    shaft = _sub_polyline(points, cum, s0, s1)
    if style == "tapered":
        sub = _cumulative(shaft)
        ls = sub[-1] or 1.0
        w0, w1 = W * 0.3, W
        body = QPainterPath()
        body.addPolygon(_ribbon(shaft, [w0 + (w1 - w0) * (c / ls) for c in sub]))
        body.closeSubpath()
        body.addEllipse(shaft[0], w0 / 2, w0 / 2)
        body.setFillRule(Qt.FillRule.WindingFill)
        body = body.simplified()
    else:
        line = QPainterPath(shaft[0])
        for q in shaft[1:]:
            line.lineTo(q)
        body = stroke_path(line, W * 0.62)
    return path.united(body)


class ArrowAnn(Annotation):
    kind = "arrow"
    labelable = True
    STYLES = ("tapered", "line", "double", "plain")

    def __init__(self, p1: QPointF | None = None, p2: QPointF | None = None, ctrl: QPointF | None = None,
                 style: str = "tapered", **kw):
        super().__init__(**kw)
        self.p1 = QPointF(p1) if p1 is not None else QPointF()
        self.p2 = QPointF(p2) if p2 is not None else QPointF()
        self.ctrl = QPointF(ctrl) if ctrl is not None else None
        self.style = style if style in self.STYLES else "tapered"
        self._geo_key = None
        self._geo = QPainterPath()

    def _data(self):
        return {"p1": _xy(self.p1), "p2": _xy(self.p2), "ctrl": _xy(self.ctrl) if self.ctrl is not None else None,
                "style": self.style}

    @classmethod
    def _from(cls, d):
        ctrl = d.get("ctrl")
        return cls(p1=_pt(d["p1"]), p2=_pt(d["p2"]), ctrl=_pt(ctrl) if ctrl else None,
                   style=d.get("style", "tapered"), **cls._base(d))

    def W(self, ctx):
        return size_value("arrow", self.size, ctx.unit)

    def curve_points(self) -> list[QPointF]:
        if self.ctrl is None:
            return [QPointF(self.p1), QPointF(self.p2)]
        pts = []
        n = 48
        for i in range(n + 1):
            t = i / n
            mt = 1 - t
            pts.append(self.p1 * (mt * mt) + self.ctrl * (2 * mt * t) + self.p2 * (t * t))
        return pts

    def mid_point(self) -> QPointF:
        if self.ctrl is None:
            return (self.p1 + self.p2) / 2
        return self.p1 * 0.25 + self.ctrl * 0.5 + self.p2 * 0.25

    def length(self) -> float:
        return _cumulative(self.curve_points())[-1]

    def shape_path(self, ctx):
        c = self.ctrl
        key = (self.p1.x(), self.p1.y(), self.p2.x(), self.p2.y(),
               None if c is None else (c.x(), c.y()), self.style, self.W(ctx))
        if key != self._geo_key:
            self._geo = arrow_path(self.curve_points(), self.W(ctx), self.style)
            self._geo_key = key
        return self._geo

    def paint(self, p, ctx):
        path = self.shape_path(ctx)
        if ctx.shadows:
            draw_soft_shadow(p, path, ctx.unit)
        p.fillPath(path, QBrush(QColor(self.color)))

    def handles(self, ctx):
        return [("p1", QPointF(self.p1)), ("p2", QPointF(self.p2)), ("mid", self.mid_point())]

    def drag_handle(self, name, pt, orig, mods, ctx):
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if name in ("p1", "p2"):
            other = orig.p2 if name == "p1" else orig.p1
            q = snap_angle(other, pt) if shift and orig.ctrl is None else QPointF(pt)
            if name == "p1":
                self.p1 = q
            else:
                self.p2 = q
            if orig.ctrl is not None:
                shift_mid = (self.p1 + self.p2) / 2 - (orig.p1 + orig.p2) / 2
                self.ctrl = orig.ctrl + shift_mid
        elif name == "mid":
            chord_mid = (self.p1 + self.p2) / 2
            if _len(pt - chord_mid) < 3 * ctx.unit:
                self.ctrl = None  # snap back to straight
            else:
                self.ctrl = pt * 2 - chord_mid  # makes the curve pass through ``pt``

    def translate(self, dx, dy):
        d = QPointF(dx, dy)
        self.p1 += d
        self.p2 += d
        if self.ctrl is not None:
            self.ctrl += d

    def tail_direction(self) -> QPointF:
        """Unit vector pointing *away* from the arrow at its tail."""
        ref = self.ctrl if self.ctrl is not None and _len(self.ctrl - self.p1) > 1e-3 else self.p2
        return _unit_vec(self.p1 - ref)

    def label_anchor(self, ctx, area, size):
        d = self.tail_direction()
        return QPointF(self.p1), d.x(), d.y(), self.W(ctx) * 0.6


# --------------------------------------------------------------------------- pen / highlighter
class PenAnn(Annotation):
    kind = "pen"

    def __init__(self, points: list[QPointF] | None = None, marker: bool = False, **kw):
        super().__init__(**kw)
        self.points = [QPointF(p) for p in (points or [])]
        self.marker = bool(marker)

    def _data(self):
        return {"points": [[round(p.x(), 1), round(p.y(), 1)] for p in self.points], "marker": self.marker}

    @classmethod
    def _from(cls, d):
        return cls(points=[_pt(v) for v in d.get("points", [])], marker=d.get("marker", False), **cls._base(d))

    def width(self, ctx):
        return size_value("marker" if self.marker else "pen", self.size, ctx.unit)

    def path(self) -> QPainterPath:
        pts = self.points
        if not pts:
            return QPainterPath()
        path = QPainterPath(pts[0])
        if len(pts) == 1:
            path.lineTo(pts[0] + QPointF(0.01, 0.0))
        elif len(pts) == 2:
            path.lineTo(pts[1])
        else:
            for i in range(1, len(pts) - 1):
                path.quadTo(pts[i], (pts[i] + pts[i + 1]) / 2)
            path.lineTo(pts[-1])
        return path

    def shape_path(self, ctx):
        return stroke_path(self.path(), self.width(ctx))

    def hit(self, pt, tol, ctx):
        return stroke_path(self.path(), self.width(ctx) + tol * 2).contains(pt)

    def paint(self, p, ctx):
        c = QColor(self.color)
        path = self.path()
        if self.marker:
            c.setAlphaF(0.42)
        elif ctx.shadows:
            draw_soft_shadow(p, self.shape_path(ctx), ctx.unit, 0.7)
        pen = QPen(c, self.width(ctx))
        pen.setCapStyle(ROUND_CAP)
        pen.setJoinStyle(ROUND_JOIN)
        p.strokePath(path, pen)

    def translate(self, dx, dy):
        d = QPointF(dx, dy)
        self.points = [q + d for q in self.points]


# --------------------------------------------------------------------------- text
@dataclass
class TextLayout:
    font: QFont
    lines: list[str]
    px: float
    py: float
    w: float
    h: float
    ascent: float
    line_h: float
    fs: float


class TextAnn(Annotation):
    kind = "text"
    STYLES = ("label", "outline", "plain")

    def __init__(self, pos: QPointF | None = None, text: str = "", style: str = "label",
                 link: str | None = None, **kw):
        super().__init__(**kw)
        self.pos = QPointF(pos) if pos is not None else QPointF()
        self.text = text
        self.style = style if style in self.STYLES else "label"
        self.link = link

    def _data(self):
        return {"pos": _xy(self.pos), "text": self.text, "style": self.style, "link": self.link}

    @classmethod
    def _from(cls, d):
        return cls(pos=_pt(d["pos"]), text=d.get("text", ""), style=d.get("style", "label"),
                   link=d.get("link"), **cls._base(d))

    def font(self, ctx) -> QFont:
        f = QFont()
        f.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Arial"])
        f.setPixelSize(max(4, round(size_value("font", self.size, ctx.unit))))
        f.setWeight(QFont.Weight.DemiBold)
        return f

    def layout(self, ctx) -> TextLayout:
        f = self.font(ctx)
        fm = QFontMetricsF(f)
        fs = float(f.pixelSize())
        lines = self.text.split("\n") if self.text else [""]
        tw = max([fm.horizontalAdvance(line) for line in lines] + [fs * 0.5])
        line_h = fm.lineSpacing()
        th = fm.height() + line_h * (len(lines) - 1)
        if self.style == "label":
            px, py = fs * 0.5, fs * 0.26
        else:
            px, py = fs * 0.14, fs * 0.06
        return TextLayout(f, lines, px, py, tw + 2 * px, th + 2 * py, fm.ascent(), line_h, fs)

    def box(self, ctx) -> QRectF:
        L = self.layout(ctx)
        return QRectF(self.pos, QSizeF(L.w, L.h))

    def shape_path(self, ctx):
        path = QPainterPath()
        path.addRect(self.box(ctx))
        return path

    def bounds(self, ctx):
        return self.box(ctx)

    def hit(self, pt, tol, ctx):
        return self.box(ctx).adjusted(-tol, -tol, tol, tol).contains(pt)

    def _text_path(self, L: TextLayout) -> QPainterPath:
        tp = QPainterPath()
        for i, line in enumerate(L.lines):
            tp.addText(QPointF(self.pos.x() + L.px, self.pos.y() + L.py + L.ascent + i * L.line_h), L.font, line)
        return tp

    def paint(self, p, ctx):
        L = self.layout(ctx)
        box = QRectF(self.pos, QSizeF(L.w, L.h))
        c = QColor(self.color)
        editing = ctx.editing_id == self.id
        if self.style == "label":
            rad = min(box.height() / 2, L.fs * 0.42)
            bg = QPainterPath()
            bg.addRoundedRect(box, rad, rad)
            if ctx.shadows:
                draw_soft_shadow(p, bg, ctx.unit, 0.9)
            p.fillPath(bg, QBrush(c))
            if editing:
                return
            p.setPen(contrast_color(c))
            p.setFont(L.font)
            for i, line in enumerate(L.lines):
                p.drawText(QPointF(box.x() + L.px, box.y() + L.py + L.ascent + i * L.line_h), line)
            return
        if editing:
            return
        if self.style == "outline":
            tp = self._text_path(L)
            outline = stroke_path(tp, L.fs * 0.2)
            if ctx.shadows:
                draw_soft_shadow(p, outline, ctx.unit, 0.8)
            p.fillPath(outline, QBrush(contrast_color(c)))
            p.fillPath(tp, QBrush(c))
            return
        p.setFont(L.font)
        for i, line in enumerate(L.lines):
            base = QPointF(box.x() + L.px, box.y() + L.py + L.ascent + i * L.line_h)
            if ctx.shadows:
                p.setPen(QColor(0, 0, 0, 120))
                p.drawText(base + QPointF(0, ctx.unit), line)
            p.setPen(c)
            p.drawText(base, line)

    def place(self, anchor, ctx, area: QRectF) -> None:
        """Position the box just beyond ``anchor`` along its direction, inside ``area``."""
        pt, dx, dy, gap = anchor
        L = self.layout(ctx)
        ext = (abs(dx) * L.w + abs(dy) * L.h) / 2
        cx = pt.x() + dx * (gap + ext)
        cy = pt.y() + dy * (gap + ext)
        x = min(max(cx - L.w / 2, area.left()), max(area.left(), area.right() - L.w))
        y = min(max(cy - L.h / 2, area.top()), max(area.top(), area.bottom() - L.h))
        self.pos = QPointF(x, y)

    def translate(self, dx, dy):
        self.pos += QPointF(dx, dy)


# --------------------------------------------------------------------------- numbered step
class StepAnn(Annotation):
    kind = "step"
    labelable = True

    def __init__(self, center: QPointF | None = None, number: int = 1, **kw):
        super().__init__(**kw)
        self.center = QPointF(center) if center is not None else QPointF()
        self.number = int(number)

    def _data(self):
        return {"center": _xy(self.center), "number": self.number}

    @classmethod
    def _from(cls, d):
        return cls(center=_pt(d["center"]), number=d.get("number", 1), **cls._base(d))

    def radius(self, ctx):
        return size_value("step", self.size, ctx.unit)

    def shape_path(self, ctx):
        path = QPainterPath()
        r = self.radius(ctx)
        path.addEllipse(self.center, r, r)
        return path

    def paint(self, p, ctx):
        r = self.radius(ctx)
        c = QColor(self.color)
        path = self.shape_path(ctx)
        if ctx.shadows:
            draw_soft_shadow(p, path, ctx.unit)
        p.fillPath(path, QBrush(c))
        ring = contrast_color(c)
        pen = QPen(ring, r * 0.13)
        p.strokePath(path, pen)
        f = QFont()
        f.setFamilies(["Segoe UI Variable Display", "Segoe UI", "Arial"])
        f.setPixelSize(max(4, round(r * (1.1 if self.number < 10 else 0.9))))
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(ring)
        p.drawText(QRectF(self.center.x() - r, self.center.y() - r, 2 * r, 2 * r),
                   int(Qt.AlignmentFlag.AlignCenter), str(self.number))

    def translate(self, dx, dy):
        self.center += QPointF(dx, dy)

    def label_anchor(self, ctx, area, size):
        r = self.radius(ctx)
        gap = r * 0.35
        if self.center.x() + r + gap + size.width() <= area.right():
            return QPointF(self.center.x() + r, self.center.y()), 1.0, 0.0, gap
        return QPointF(self.center.x() - r, self.center.y()), -1.0, 0.0, gap


# --------------------------------------------------------------------------- redact
def _redact(image: QImage, r, mode: str, block: float) -> QImage:
    src = image.copy(r)
    w, h = r.width(), r.height()
    ignore = Qt.AspectRatioMode.IgnoreAspectRatio
    smooth = Qt.TransformationMode.SmoothTransformation
    if mode == "blur":
        f = max(2.0, block * 0.9)
        sw, sh = max(1, round(w / f)), max(1, round(h / f))
        small = src.scaled(sw, sh, ignore, smooth)
        out = small.scaled(max(1, sw * 2), max(1, sh * 2), ignore, smooth).scaled(w, h, ignore, smooth)
    else:
        sw, sh = max(1, round(w / block)), max(1, round(h / block))
        out = src.scaled(sw, sh, ignore, smooth).scaled(w, h, ignore, Qt.TransformationMode.FastTransformation)
    return out.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


class PixelAnn(Annotation):
    kind = "pixel"

    def __init__(self, rect: QRectF | None = None, mode: str = "pixelate", **kw):
        super().__init__(**kw)
        self.rect = QRectF(rect) if rect is not None else QRectF()
        self.mode = mode if mode in ("pixelate", "blur") else "pixelate"

    def _data(self):
        return {"rect": _rl(self.rect), "mode": self.mode}

    @classmethod
    def _from(cls, d):
        return cls(rect=_rect(d["rect"]), mode=d.get("mode", "pixelate"), **cls._base(d))

    def shape_path(self, ctx):
        path = QPainterPath()
        path.addRect(self.rect)
        return path

    def paint(self, p, ctx):
        r = self.rect.toAlignedRect().intersected(ctx.image.rect())
        if r.width() < 1 or r.height() < 1:
            return
        block = size_value("pixel", self.size, ctx.unit)
        key = ("pix", r.x(), r.y(), r.width(), r.height(), self.mode, round(block, 2))
        img = ctx.cache.get(key)
        if img is None:
            if len(ctx.cache) > 48:
                ctx.cache.clear()
            img = ctx.cache[key] = _redact(ctx.image, r, self.mode, block)
        p.drawImage(r.topLeft(), img)

    def handles(self, ctx):
        return box_handles(self.rect)

    def drag_handle(self, name, pt, orig, mods, ctx):
        self.rect = resize_box(orig.rect, name, pt, square=bool(mods & Qt.KeyboardModifier.ShiftModifier))

    def translate(self, dx, dy):
        self.rect.translate(dx, dy)


# --------------------------------------------------------------------------- spotlight
class SpotAnn(Annotation):
    kind = "spot"

    def __init__(self, rect: QRectF | None = None, shape: str = "rect", **kw):
        super().__init__(**kw)
        self.rect = QRectF(rect) if rect is not None else QRectF()
        self.shape = shape if shape in ("rect", "ellipse") else "rect"

    def _data(self):
        return {"rect": _rl(self.rect), "shape": self.shape}

    @classmethod
    def _from(cls, d):
        return cls(rect=_rect(d["rect"]), shape=d.get("shape", "rect"), **cls._base(d))

    def area_path(self) -> QPainterPath:
        path = QPainterPath()
        if self.shape == "ellipse":
            path.addEllipse(self.rect)
        else:
            rad = min(8.0, self.rect.width() / 2, self.rect.height() / 2)
            path.addRoundedRect(self.rect, rad, rad)
        return path

    def shape_path(self, ctx):
        return self.area_path()

    def handles(self, ctx):
        return box_handles(self.rect)

    def drag_handle(self, name, pt, orig, mods, ctx):
        self.rect = resize_box(orig.rect, name, pt, square=bool(mods & Qt.KeyboardModifier.ShiftModifier))

    def translate(self, dx, dy):
        self.rect.translate(dx, dy)


KINDS = {c.kind: c for c in (ShapeAnn, ArrowAnn, PenAnn, TextAnn, StepAnn, PixelAnn, SpotAnn)}


def annotation_from_dict(d: dict) -> Annotation:
    return KINDS[d["kind"]]._from(d)


# --------------------------------------------------------------------------- document
class Document:
    """A captured image plus its annotations and an optional (non-destructive) crop."""

    SPOT_DIM = QColor(0, 0, 0, 150)

    def __init__(self, image: QImage, unit: float = 1.0):
        self.has_alpha = image.hasAlphaChannel()
        fmt = QImage.Format.Format_ARGB32_Premultiplied
        self.image = image if image.format() == fmt else image.convertToFormat(fmt)
        self.unit = max(0.5, float(unit))
        self.items: list[Annotation] = []
        self.crop: QRectF | None = None
        self.ctx = RenderContext(self.image, self.unit)

    # lookup ------------------------------------------------------------------
    def full_rect(self) -> QRectF:
        return QRectF(0, 0, self.image.width(), self.image.height())

    def output_rect(self) -> QRectF:
        return QRectF(self.crop) if self.crop is not None else self.full_rect()

    def find(self, aid: str | None) -> Annotation | None:
        for a in self.items:
            if a.id == aid:
                return a
        return None

    def index_of(self, aid: str) -> int:
        for i, a in enumerate(self.items):
            if a.id == aid:
                return i
        return -1

    def remove(self, aid: str) -> None:
        self.items = [a for a in self.items if a.id != aid]
        for a in self.items:
            if isinstance(a, TextAnn) and a.link == aid:
                a.link = None

    def paint_order(self) -> list[Annotation]:
        pix = [a for a in self.items if a.kind == "pixel"]
        spots = [a for a in self.items if a.kind == "spot"]
        rest = [a for a in self.items if a.kind not in ("pixel", "spot")]
        return pix + spots + rest

    def item_at(self, pt: QPointF, tol: float, exclude: str | None = None) -> Annotation | None:
        for a in reversed(self.paint_order()):
            if a.id != exclude and a.hit(pt, tol, self.ctx):
                return a
        return None

    def next_step(self) -> int:
        return max([a.number for a in self.items if isinstance(a, StepAnn)] + [0]) + 1

    # layout ------------------------------------------------------------------
    def relayout(self) -> None:
        """Re-seat every label that is linked to an arrow/shape/step."""
        area = self.output_rect()
        for t in self.items:
            if isinstance(t, TextAnn) and t.link:
                target = self.find(t.link)
                if target is None:
                    t.link = None
                    continue
                L = t.layout(self.ctx)
                anchor = target.label_anchor(self.ctx, area, QSizeF(L.w, L.h))
                if anchor is not None:
                    t.place(anchor, self.ctx, area)

    # rendering ---------------------------------------------------------------
    def paint(self, p: QPainter, draw_base: bool = True) -> None:
        if draw_base:
            p.drawImage(QPointF(0, 0), self.image)
        order = self.paint_order()
        for a in order:
            if a.kind == "pixel":
                a.paint(p, self.ctx)
        spots = [a for a in order if a.kind == "spot"]
        if spots:
            hole = QPainterPath()
            for s in spots:
                hole = hole.united(s.area_path())
            dim = QPainterPath()
            dim.addRect(self.full_rect())
            p.fillPath(dim.subtracted(hole), QBrush(self.SPOT_DIM))
        for a in order:
            if a.kind not in ("pixel", "spot"):
                a.paint(p, self.ctx)

    def render(self) -> QImage:
        r = self.output_rect().toAlignedRect().intersected(self.image.rect())
        out = QImage(max(1, r.width()), max(1, r.height()), QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(Qt.GlobalColor.transparent)
        editing, self.ctx.editing_id = self.ctx.editing_id, None
        p = QPainter(out)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)
        p.translate(-r.x(), -r.y())
        self.paint(p)
        p.end()
        self.ctx.editing_id = editing
        return out.convertToFormat(QImage.Format.Format_ARGB32 if self.has_alpha else QImage.Format.Format_RGB32)

    # history -----------------------------------------------------------------
    def state(self) -> dict:
        return {"items": [a.to_dict() for a in self.items], "crop": _rl(self.crop) if self.crop is not None else None}

    def restore(self, state: dict) -> None:
        self.items = [annotation_from_dict(d) for d in state["items"]]
        self.crop = _rect(state["crop"]) if state.get("crop") else None
