"""Capture overlay always snips the dragged rectangle; corner thumbnails stack, follow the editor and drag out."""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from graftsnip import annotations as A  # noqa: E402
from graftsnip import thumbs  # noqa: E402
from graftsnip.settings import Settings  # noqa: E402


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def _overlay(session, windows):
    from graftsnip.capture import CaptureOverlay
    sc = QGuiApplication.primaryScreen()
    geo = sc.geometry()
    pm = QPixmap(geo.width(), geo.height())  # ratio 1.0: overlay px == image px
    pm.fill(QColor("#556677"))
    ov = CaptureOverlay(session, sc, pm, windows, show_toolbar=True)
    ov.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    ov.resize(geo.size())
    ov.show()
    session.overlays.append(ov)
    QApplication.processEvents()
    return ov, geo


def _drag(widget, a: QPoint, b: QPoint, steps: int = 8):
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
    for i in range(1, steps + 1):
        QTest.mouseMove(widget, a + (b - a) * (i / steps))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)


@pytest.mark.parametrize("mode", ["region", "window", "screen"])
def test_drag_snips_the_dragged_area_in_every_mode(app, tmp_path, mode):
    from graftsnip.capture import CaptureSession
    session = CaptureSession(Settings(tmp_path / "s.json"), [])
    got = []
    session.captured.connect(lambda img, _r, _s: got.append(img.size()))
    browser = ("Browser", QRectF(0, 0, 2000, 2000))  # a maximised window under the whole drag
    ov, _geo = _overlay(session, [browser])
    session.mode = mode
    _drag(ov, QPoint(100, 120), QPoint(420, 320))
    assert got == [QSize(320, 200)], f"{mode}: expected the dragged 320x200 area, got {got}"


def test_click_in_rectangle_mode_still_snips_the_window(app, tmp_path):
    from graftsnip.capture import CaptureSession
    session = CaptureSession(Settings(tmp_path / "s.json"), [])
    got = []
    session.captured.connect(lambda img, _r, _s: got.append(img.size()))
    ov, _geo = _overlay(session, [("Dialog", QRectF(50, 60, 300, 200))])
    QTest.mouseClick(ov, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(120, 150))
    assert got == [QSize(300, 200)]


def test_every_snip_starts_as_a_rectangle(app, tmp_path):
    from graftsnip.capture import CaptureSession
    path = tmp_path / "s.json"
    path.write_text('{"capture_mode": "window"}', encoding="utf-8")  # the setting that caused the bug
    settings = Settings(path)
    first = CaptureSession(settings, [])
    assert first.mode == "region"
    first.set_mode("window")
    assert CaptureSession(settings, []).mode == "region"
    assert "capture_mode" not in settings.data


# --------------------------------------------------------------------------- thumbnails
def _photo(w=1600, h=900):
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor("#3a6ea5"))
    return img


@pytest.fixture
def dock(app, tmp_path, monkeypatch):
    monkeypatch.setattr(thumbs, "drag_dir", lambda: tmp_path)

    def prepare(self):  # keep test windows off the real desktop
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.excluded = True

    monkeypatch.setattr(thumbs.SnipThumb, "prepare", prepare)
    settings = Settings(tmp_path / "s.json")
    settings.data.update(copy_on_done=False, save_on_done=False)
    opened = []
    ctrl = SimpleNamespace(settings=settings, opened=opened)

    def open_editor(image, unit, screen=None, name=None, state=None):
        ed = _editor(ctrl, image, unit, name or "x", state)
        opened.append(ed)
        return ed

    ctrl.open_editor = open_editor
    for attr in ("open_file", "start_capture", "open_dialog", "paste_clipboard", "open_save_folder",
                 "show_settings", "show_about"):
        setattr(ctrl, attr, lambda *a, **k: None)
    d = thumbs.ThumbDock(ctrl)
    yield d
    d.close_all()
    for ed in opened:
        try:
            ed.force_close = True
            ed.close()
        except RuntimeError:  # closed (and deleted) by the test itself
            pass
    QApplication.processEvents()


def _editor(ctrl, image, unit, name, state=None):
    from graftsnip.editor import EditorWindow
    ed = EditorWindow(ctrl, image, unit, name, state)
    ed.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    ed.show()
    QApplication.processEvents()
    return ed


def test_thumbnail_sits_bottom_left_and_keeps_aspect(dock):
    t = dock.add(_photo(), 1.0, "Screenshot A")
    QTest.qWait(320)  # let the slide-in finish
    assert t.tw <= thumbs.MAX_W and t.th <= thumbs.MAX_H
    assert t.tw / t.th == pytest.approx(1600 / 900, rel=0.02)
    av = QGuiApplication.primaryScreen().availableGeometry()
    card = t.card().translated(t.pos().x(), t.pos().y())
    assert card.left() == pytest.approx(av.left() + thumbs.EDGE, abs=1)
    assert card.bottom() == pytest.approx(av.bottom() + 1 - thumbs.EDGE, abs=1)


def test_thumbnails_stack_newest_at_bottom_and_cap_at_three(dock):
    made = [dock.add(_photo(800, 400 + 50 * i), 1.0, f"Snip {i}") for i in range(4)]
    QTest.qWait(320)
    assert dock.thumbs == made[1:]                      # the oldest was pushed off
    ys = [t.pos().y() for t in dock.thumbs]             # oldest first
    assert ys == sorted(ys)                             # so the newest sits lowest, in the corner
    for upper, lower in zip(dock.thumbs[:-1], dock.thumbs[1:]):  # no overlap between cards
        assert upper.pos().y() + thumbs.SHADOW + upper.card_h <= lower.pos().y() + thumbs.SHADOW


def test_drag_payload_is_a_real_png_plus_the_bitmap(dock, tmp_path):
    t = dock.add(_photo(640, 360), 1.0, 'Screenshot 2026-10-19 at 22.15.03')
    mime = t.mime_data()
    urls = mime.urls()
    assert len(urls) == 1 and urls[0].isLocalFile()
    path = os.path.normpath(urls[0].toLocalFile())
    assert os.path.dirname(path) == os.path.normpath(str(tmp_path))
    assert os.path.basename(path) == "Screenshot 2026-10-19 at 22.15.03.png"
    assert QImage(path).size() == QSize(640, 360)
    assert mime.hasImage() and mime.imageData().size() == QSize(640, 360)
    first = os.path.getmtime(path)
    again = os.path.normpath(t.mime_data().urls()[0].toLocalFile())
    assert again == path and os.path.getmtime(path) == first  # unchanged image: file not rewritten


def test_thumbnail_follows_editor_and_reopens_editable(dock):
    t = dock.add(_photo(), 1.0, "Live")
    ed = dock.ctrl.open_editor(t.original, 1.0, name=t.name)
    t.attach_editor(ed)
    rev = t.rev
    before = t.image.pixelColor(600, 450).name()
    ed.canvas.add_items([A.ShapeAnn(rect=QRectF(500, 350, 200, 200), fill=True, color="#FF3B30")])
    QTest.qWait(400)                                     # debounced pull from the editor
    assert t.rev > rev and t.image.pixelColor(600, 450).name() != before
    path = t.mime_data().urls()[0].toLocalFile()
    assert QImage(path).pixelColor(600, 450).name() != before  # a drag now delivers the annotated image
    ed.done()
    QApplication.processEvents()
    assert t.editor is None and len(t.state["items"]) == 1
    dock.open(t)                                         # click the thumbnail after Done
    ed2 = dock.ctrl.opened[-1]
    assert ed2 is not ed and len(ed2.doc.items) == 1 and not ed2.canvas.is_dirty()
    assert t.editor is ed2


def test_discarding_edits_reverts_the_thumbnail(dock):
    t = dock.add(_photo(), 1.0, "Discard")
    ed = dock.ctrl.open_editor(t.original, 1.0, name=t.name)
    t.attach_editor(ed)
    clean = t.image.pixelColor(600, 450).name()
    ed.canvas.add_items([A.ShapeAnn(rect=QRectF(500, 350, 200, 200), fill=True, color="#FF3B30")])
    ed.discarded = True
    t._editor_closed(ed)
    assert t.state["items"] == [] and t.image.pixelColor(600, 450).name() == clean


def test_accepted_drop_removes_thumbnail_cancelled_drag_keeps_it(dock):
    kept = dock.add(_photo(), 1.0, "Cancelled")
    kept.after_drop(Qt.DropAction.IgnoreAction)          # Esc / let go over nothing
    assert kept in dock.thumbs
    t = dock.add(_photo(), 1.0, "Shared")
    ed = dock.ctrl.open_editor(t.original, 1.0, name=t.name)
    t.attach_editor(ed)
    ed.canvas.add_items([A.ShapeAnn(rect=QRectF(10, 10, 50, 50))])
    assert ed.canvas.is_dirty()
    t.after_drop(Qt.DropAction.CopyAction)               # another app accepted it
    assert t not in dock.thumbs and dock.thumbs == [kept]
    assert not ed.canvas.is_dirty()                      # closing the editor won't nag about saving
    QTest.qWait(250)                                     # fade-out finishes


def test_dismiss_removes_and_restacks(dock):
    a = dock.add(_photo(), 1.0, "A")
    b = dock.add(_photo(), 1.0, "B")
    QTest.qWait(320)
    low = b.pos().y()
    dock.dismiss(b)
    QTest.qWait(320)
    assert dock.thumbs == [a] and a.pos().y() == low
