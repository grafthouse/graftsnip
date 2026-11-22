"""Crisp vector icons (inline SVG, recoloured per state), the GraftSnip brand marks and the app icon."""
from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parent / "assets"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

NORMAL = "#ECECEE"
ACTIVE = "#111113"
DISABLED = "#5E5E64"

_SVG = {
    "select": '<path d="M6 3.8v15.4l4.1-3.9 2.8 6 2.6-1.2-2.8-5.9 5.7-.3z"/>',
    "rect": '<rect x="3.5" y="5" width="17" height="14" rx="2.5"/>',
    "ellipse": '<ellipse cx="12" cy="12" rx="8.8" ry="7"/>',
    "arrow": '<path d="M5 19 18.5 5.5"/><path d="M9.5 5h9.5v9.5"/>',
    "pen": '<path d="M16.6 3.9a2.1 2.1 0 0 1 3 3L8 18.5l-4 1 1-4z"/><path d="M14.5 6l3 3"/>',
    "marker": '<path d="m9 11-5.5 5.5V20h7.5l3-3"/>'
              '<path d="m21 11.5-4.4 4.4a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L13 3.5"/>',
    "text": '<path d="M3 18.5 7.6 6l4.6 12.5"/><path d="M4.8 14h5.6"/>'
            '<circle cx="17.3" cy="15.3" r="3.2"/><path d="M20.5 11.6v6.9"/>',
    "pixel": '<rect x="3.5" y="3.5" width="17" height="17" rx="2.5"/>'
             '<path d="M3.5 3.5h5.7v5.7H3.5zM14.8 3.5h5.7v5.7h-5.7zM9.2 9.2h5.6v5.6H9.2zM3.5 14.8h5.7v5.7H3.5z'
             'M14.8 14.8h5.7v5.7h-5.7z" fill="{c}" stroke="none"/>',
    "step": '<circle cx="12" cy="12" r="8.8"/><path d="M10.4 9.4 12.6 8v8.2"/><path d="M10.3 16.2h4.6"/>',
    "spot": '<path d="M4 8.5V6a2 2 0 0 1 2-2h2.5M15.5 4H18a2 2 0 0 1 2 2v2.5M20 15.5V18a2 2 0 0 1-2 2h-2.5'
            'M8.5 20H6a2 2 0 0 1-2-2v-2.5"/><circle cx="12" cy="12" r="3.4" fill="{c}" stroke="none"/>',
    "eraser": '<path d="m7 20.5-3.6-3.6a2 2 0 0 1 0-2.8L13.2 4.3a2 2 0 0 1 2.8 0l4.2 4.2a2 2 0 0 1 0 2.8'
              'L11.5 20.5"/><path d="M21 20.5H7"/><path d="m6.5 10.9 7.1 7.1"/>',
    "crop": '<path d="M6.5 2.5v13.5a1.5 1.5 0 0 0 1.5 1.5h13.5"/>'
            '<path d="M17.5 21.5V8a1.5 1.5 0 0 0-1.5-1.5H2.5"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "redo": '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    "ocr": '<path d="M3.5 7.5V6A2.5 2.5 0 0 1 6 3.5h1.5M16.5 3.5H18A2.5 2.5 0 0 1 20.5 6v1.5'
           'M20.5 16.5V18a2.5 2.5 0 0 1-2.5 2.5h-1.5M7.5 20.5H6A2.5 2.5 0 0 1 3.5 18v-1.5"/>'
           '<path d="M8 9h8M8 12h8M8 15h5"/>',
    "more": '<circle cx="5.5" cy="12" r="1.6" fill="{c}" stroke="none"/>'
            '<circle cx="12" cy="12" r="1.6" fill="{c}" stroke="none"/>'
            '<circle cx="18.5" cy="12" r="1.6" fill="{c}" stroke="none"/>',
    "copy": '<rect x="8.5" y="8.5" width="12" height="12" rx="2.2"/>'
            '<path d="M15.5 8.5V5.7a2.2 2.2 0 0 0-2.2-2.2H5.7a2.2 2.2 0 0 0-2.2 2.2v7.6a2.2 2.2 0 0 0 2.2 2.2h2.8"/>',
    "check": '<path d="M5 12.5l4.6 4.6L19.5 7"/>',
    "zoom_in": '<circle cx="10.8" cy="10.8" r="6.8"/><path d="m20.5 20.5-4.9-4.9M10.8 8v5.6M8 10.8h5.6"/>',
    "zoom_out": '<circle cx="10.8" cy="10.8" r="6.8"/><path d="m20.5 20.5-4.9-4.9M8 10.8h5.6"/>',
    "region": '<rect x="3.5" y="5" width="17" height="14" rx="1.5" stroke-dasharray="3 2.4"/>',
    "window": '<rect x="3" y="4.5" width="18" height="15" rx="2.2"/><path d="M3 9h18"/>',
    "screen": '<rect x="2.5" y="4" width="19" height="12.5" rx="2"/><path d="M8.5 20.5h7M12 16.5v4"/>',
    "lasso": '<path d="M7.5 17.4C4.6 16 3 13.8 3 11.5 3 7.4 7 4 12 4s9 3.4 9 7.5-4 7.5-9 7.5c-1 0-2-.1-2.9-.4"/>'
             '<circle cx="7.5" cy="18.4" r="1.7"/><path d="M7.4 20.1c0 1.2-.6 1.8-1.7 1.8"/>',
    "close": '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    "timer": '<circle cx="12" cy="13.5" r="7.5"/><path d="M12 9.5v4l2.6 1.8M9.5 2.5h5"/>',
}

_TEMPLATE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" '
             'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


def svg_pixmap(name: str, color: str, size: int = 18, scale: float = 2.0) -> QPixmap:
    body = _SVG[name].replace("{c}", color)
    svg = _TEMPLATE.replace("{body}", body).replace("{c}", color)
    px = max(1, round(size * scale))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, px, px))
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm


@lru_cache(maxsize=None)
def icon(name: str, size: int = 18, normal: str = NORMAL, active: str = ACTIVE) -> QIcon:
    ic = QIcon()
    ic.addPixmap(svg_pixmap(name, normal, size), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(svg_pixmap(name, active, size), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(svg_pixmap(name, active, size), QIcon.Mode.Active, QIcon.State.On)
    ic.addPixmap(svg_pixmap(name, DISABLED, size), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic


def write_ico(path: str | Path, sizes: tuple[int, ...] = ICO_SIZES) -> bool:
    """Write a multi-resolution .ico (PNG-compressed entries, Vista+) so Windows never upscales a tiny frame."""
    entries = []
    for sz in sizes:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        if not app_pixmap(sz).toImage().save(buf, "PNG"):
            return False
        buf.close()
        entries.append((sz, bytes(data)))
    offset = 6 + 16 * len(entries)
    head = struct.pack("<HHH", 0, 1, len(entries))
    body = b""
    for sz, png in entries:
        head += struct.pack("<BBBBHHII", sz % 256, sz % 256, 0, 0, 1, 32, len(png), offset + len(body))
        body += png
    try:
        Path(path).write_bytes(head + body)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------- brand
@lru_cache(maxsize=None)
def _asset(name: str) -> QImage:
    img = QImage(str(ASSETS / name))
    return img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _scaled(img: QImage, w: int, h: int) -> QImage:
    # QImage's smooth scale area-averages large reductions; QPainter would alias the 500 px mark at 16 px
    return img.scaled(max(1, w), max(1, h), Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)


def logo_pixmap(height: int, dpr: float = 2.0) -> QPixmap:
    """The GraftSnip lockup (GS mark + wordmark) on transparency, `height` logical pixels tall."""
    src = _asset("graftsnip-logo.png")
    if src.isNull():
        return QPixmap()
    px_h = round(height * dpr)
    pm = QPixmap.fromImage(_scaled(src, round(src.width() * px_h / src.height()), px_h))
    pm.setDevicePixelRatio(dpr)
    return pm


def mark_pixmap(height: int, dpr: float = 2.0) -> QPixmap:
    src = _asset("graftsnip-mark.png")
    if src.isNull():
        return QPixmap()
    px_h = round(height * dpr)
    pm = QPixmap.fromImage(_scaled(src, round(src.width() * px_h / src.height()), px_h))
    pm.setDevicePixelRatio(dpr)
    return pm


def app_pixmap(size: int = 256) -> QPixmap:
    """The GraftSnip GS mark on the brand's near-black rounded tile."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    inset = 0.0 if size < 32 else size / 32
    tile = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    radius = tile.width() * 0.22
    g = QLinearGradient(tile.topLeft(), tile.bottomLeft())
    g.setColorAt(0.0, QColor("#1F1F24"))
    g.setColorAt(1.0, QColor("#08080A"))
    path = QPainterPath()
    path.addRoundedRect(tile, radius, radius)
    p.fillPath(path, QBrush(g))
    if size >= 32:  # hairline rim so the dark tile still reads on a dark taskbar
        p.setPen(QPen(QColor(255, 255, 255, 34), max(1.0, size / 128)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        half = max(1.0, size / 128) / 2
        p.drawRoundedRect(tile.adjusted(half, half, -half, -half), radius - half, radius - half)
    src = _asset("graftsnip-mark.png")
    if not src.isNull():
        fill = 0.86 if size < 32 else 0.78
        mark = _scaled(src, round(tile.width() * fill), round(tile.height() * fill))
        x = tile.x() + (tile.width() - mark.width()) / 2
        y = tile.y() + (tile.height() - mark.height()) / 2
        p.drawImage(QPointF(round(x), round(y)), mark)
    p.end()
    return pm


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    ic = QIcon()
    for sz in ICO_SIZES:
        ic.addPixmap(app_pixmap(sz))
    return ic
