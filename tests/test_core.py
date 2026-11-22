import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from graftsnip import annotations as A  # noqa: E402
from graftsnip import ocr, winapi  # noqa: E402
from graftsnip.settings import Settings  # noqa: E402


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def grey_image(w=800, h=500):
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor("#808080"))
    p = QPainter(img)
    for x in range(0, w, 20):  # stripes so pixelation has something to average
        p.fillRect(x, 0, 10, h, QColor("#202020"))
    p.end()
    return img


def test_roundtrip_every_kind(app):
    items = [
        A.ShapeAnn(rect=QRectF(10, 10, 50, 40), shape="ellipse", fill=True, color="#0A84FF", size=2),
        A.ArrowAnn(p1=QPointF(1, 2), p2=QPointF(100, 80), ctrl=QPointF(50, 0), style="double"),
        A.PenAnn(points=[QPointF(0, 0), QPointF(5, 5), QPointF(9, 2)], marker=True),
        A.TextAnn(pos=QPointF(3, 4), text="hello\nworld", style="outline", link="abc"),
        A.StepAnn(center=QPointF(30, 30), number=7),
        A.PixelAnn(rect=QRectF(0, 0, 20, 20), mode="blur"),
        A.SpotAnn(rect=QRectF(5, 5, 30, 30), shape="ellipse"),
    ]
    for a in items:
        d = a.to_dict()
        assert A.annotation_from_dict(d).to_dict() == d


def test_arrow_geometry_hits_shaft_and_head(app):
    doc = A.Document(grey_image())
    a = A.ArrowAnn(p1=QPointF(100, 300), p2=QPointF(400, 100))
    path = a.shape_path(doc.ctx)
    assert not path.isEmpty()
    assert path.contains(QPointF(398, 102))          # right at the tip
    assert path.contains(QPointF(250, 200))          # middle of the shaft
    assert not path.contains(QPointF(100, 100))      # nowhere near
    a.ctrl = QPointF(150, 80)                        # curved arrows still produce a closed shape
    assert a.hit(a.mid_point(), 1.0, doc.ctx)


def test_label_sits_beyond_arrow_tail(app):
    doc = A.Document(grey_image())
    arrow = A.ArrowAnn(p1=QPointF(300, 300), p2=QPointF(500, 150))
    label = A.TextAnn(text="Misaligned button", link=arrow.id)
    doc.items += [arrow, label]
    doc.relayout()
    box = label.box(doc.ctx)
    assert not box.contains(arrow.p1)
    assert box.center().x() < arrow.p1.x() and box.center().y() > arrow.p1.y()  # down-left of the tail
    arrow.translate(-50, 0)                                                     # label follows its arrow
    doc.relayout()
    assert label.box(doc.ctx).center().x() == pytest.approx(box.center().x() - 50, abs=0.01)


def test_label_clamped_inside_image(app):
    doc = A.Document(grey_image(400, 300))
    arrow = A.ArrowAnn(p1=QPointF(5, 5), p2=QPointF(200, 200))
    label = A.TextAnn(text="Edge case", link=arrow.id)
    doc.items += [arrow, label]
    doc.relayout()
    assert doc.full_rect().contains(label.box(doc.ctx))


def test_render_respects_crop_pixelate_and_spotlight(app):
    doc = A.Document(grey_image())
    doc.crop = QRectF(100, 50, 300, 200)
    assert doc.render().size().width() == 300 and doc.render().size().height() == 200
    doc.crop = None
    before = doc.render().pixelColor(5, 5).name()
    doc.items.append(A.PixelAnn(rect=QRectF(0, 0, 60, 60), size=2))
    after = doc.render().pixelColor(5, 5).name()
    assert before != after                            # stripes averaged into a block
    doc.items = [A.SpotAnn(rect=QRectF(400, 200, 100, 100))]
    out = doc.render()
    assert out.pixelColor(450, 250).name() == doc.image.pixelColor(450, 250).name()  # inside untouched
    assert out.pixelColor(700, 450).lightness() < doc.image.pixelColor(700, 450).lightness()  # outside dimmed


def test_draw_arrow_then_type_label_then_undo(app, tmp_path):
    from graftsnip.canvas import Canvas
    settings = Settings(tmp_path / "s.json")
    doc = A.Document(grey_image())
    cv = Canvas(doc, settings)
    cv.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    cv.resize(1000, 700)
    cv.show()
    QApplication.processEvents()
    assert cv.tool == "arrow"
    start = cv.to_scr(QPointF(200, 350)).toPoint()
    end = cv.to_scr(QPointF(500, 150)).toPoint()
    QTest.mousePress(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for i in range(1, 11):
        QTest.mouseMove(cv, start + (end - start) * (i / 10))
    QTest.mouseRelease(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    assert [a.kind for a in doc.items] == ["arrow"]
    assert cv.pending == doc.items[0].id
    QTest.keyClicks(cv, "B")                      # first key starts the label …
    assert cv.editor is not None
    QTest.keyClicks(cv.editor, "roken link")      # … the rest goes into the inline editor
    QTest.keyClick(cv.editor, Qt.Key.Key_Return)
    kinds = [a.kind for a in doc.items]
    assert kinds == ["arrow", "text"]
    text = doc.items[1]
    assert text.text == "Broken link" and text.link == doc.items[0].id
    assert text.color == doc.items[0].color
    cv.undo()
    assert [a.kind for a in doc.items] == ["arrow"]
    cv.undo()
    assert doc.items == []
    cv.redo()
    cv.redo()
    assert [a.kind for a in doc.items] == ["arrow", "text"]
    cv.close()


def test_click_without_drag_does_not_create(app, tmp_path):
    from graftsnip.canvas import Canvas
    cv = Canvas(A.Document(grey_image()), Settings(tmp_path / "s.json"))
    cv.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    cv.resize(900, 600)
    cv.show()
    QApplication.processEvents()
    QTest.mouseClick(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(450, 300))
    assert cv.doc.items == [] and not cv.can_undo()
    cv.close()


def test_parse_hotkey():
    assert winapi.parse_hotkey("Ctrl+Alt+S") == (winapi.MOD_CONTROL | winapi.MOD_ALT, ord("S"))
    assert winapi.parse_hotkey("Print") == (0, 0x2C)
    assert winapi.parse_hotkey("Shift+F12") == (winapi.MOD_SHIFT, 0x7B)
    assert winapi.parse_hotkey("S") is None        # bare letters would hijack typing
    assert winapi.parse_hotkey("") is None


def test_sensitive_rects_finds_email_and_phone(app):
    def word(t, x):
        return {"t": t, "rect": QRectF(x, 0, 10 * len(t), 12)}
    lines = [
        {"text": "Mail me at jo@example.co.uk today", "words": [word("Mail", 0), word("me", 50), word("at", 80),
                                                                 word("jo@example.co.uk", 110), word("today", 290)]},
        {"text": "Call +44 7700 900123 now", "words": [word("Call", 0), word("+44", 50), word("7700", 90),
                                                        word("900123", 140), word("now", 210)]},
        {"text": "Version 2026-10-18", "words": [word("Version", 0), word("2026-10-18", 80)]},
    ]
    rects = ocr.sensitive_rects(lines)
    assert len(rects) == 2
    assert rects[0].left() == 110
    assert rects[1].left() == 50 and rects[1].right() == 200



def test_brand_assets_and_icon(app, tmp_path):
    import struct

    from graftsnip import icons
    from graftsnip.settings import APP_NAME, DEFAULTS
    assert APP_NAME == "GraftSnip" and DEFAULTS["save_dir"].endswith("GraftSnip")
    for name in ("graftsnip-mark.png", "graftsnip-logo.png"):
        img = QImage(str(icons.ASSETS / name))
        assert not img.isNull() and img.hasAlphaChannel(), name
        assert img.pixelColor(0, 0).alpha() == 0, f"{name}: background not keyed out"
    logo = icons.logo_pixmap(44, 2.0)
    assert logo.height() == 88 and logo.width() > 2 * logo.height()  # mark + wordmark lockup
    sizes = {s.width() for s in icons.app_icon().availableSizes()}
    assert {16, 32, 256} <= sizes
    tile = icons.app_pixmap(64).toImage()
    centre = tile.pixelColor(32, 32)
    assert tile.pixelColor(0, 0).alpha() == 0 and centre.alpha() == 255  # rounded tile, opaque mark
    ico = tmp_path / "g.ico"
    assert icons.write_ico(ico)
    data = ico.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind, count) == (0, 1, len(icons.ICO_SIZES))
    for i, sz in enumerate(icons.ICO_SIZES):
        w, _h, _c, _r, _planes, _bpp, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        assert w == sz % 256 and data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n"
        assert offset + length <= len(data)
