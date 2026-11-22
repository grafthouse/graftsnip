"""Cut GraftSnip brand assets out of the master logo (mark + wordmark on solid black).

    .venv\\Scripts\\python.exe tools\\build_brand.py [path\\to\\Graftsnip.png]

Writes graftsnip/assets/graftsnip-mark.png (the GS mark) and graftsnip/assets/graftsnip-logo.png
(mark + wordmark), both with the black background keyed out to transparency. Re-run it whenever the
master logo changes, then run install.ps1 to rebuild graftsnip.ico and the shortcuts.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication, QImage

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "graftsnip" / "assets"
# Master logo in a "Branding" folder next to this project; pass a path to use another file.
DEFAULT_SOURCE = ROOT.parent / "Branding" / "Graftsnip.png"

# Where the mark ends and the wordmark starts in the master artwork (first empty column run).
SPLIT_X = 640
# Brightness at which a pixel counts as fully opaque. Low for the mark so its dark ribbon shading stays
# solid; 255 for the white wordmark so its anti-aliased edges become exact alpha.
KEY_MARK = 64
KEY_WORD = 255
NOISE = 2  # the master's background is 0-1: treat that as pure black


def key_black(img: QImage, split_x: int) -> QImage:
    """Turn a logo drawn on black into straight-alpha RGBA. Composited back over black it is identical."""
    src = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h, bpl = src.width(), src.height(), src.bytesPerLine()
    data = bytes(src.constBits())
    out = bytearray(w * h * 4)
    o = 0
    for y in range(h):
        row = y * bpl
        for x in range(w):
            i = row + x * 4
            r, g, b = data[i], data[i + 1], data[i + 2]
            m = max(r, g, b)
            if m > NOISE:
                k = KEY_MARK if x < split_x else KEY_WORD
                a = min(255, (m * 255 + k // 2) // k)
                out[o] = min(255, (r * 255 + a // 2) // a)
                out[o + 1] = min(255, (g * 255 + a // 2) // a)
                out[o + 2] = min(255, (b * 255 + a // 2) // a)
                out[o + 3] = a
            o += 4
    res = QImage(bytes(out), w, h, w * 4, QImage.Format.Format_RGBA8888)
    return res.copy()  # detach from the temporary buffer


def content_box(img: QImage, x0: int, x1: int, threshold: int = 8) -> tuple[int, int, int, int]:
    """Bounding box (x, y, w, h) of non-black pixels between columns x0 and x1."""
    src = img.convertToFormat(QImage.Format.Format_RGBA8888)
    bpl = src.bytesPerLine()
    data = bytes(src.constBits())
    xs, ys = [], []
    for y in range(src.height()):
        row = y * bpl
        for x in range(x0, min(x1, src.width())):
            i = row + x * 4
            if max(data[i], data[i + 1], data[i + 2]) > threshold:
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else DEFAULT_SOURCE
    app = QGuiApplication([])  # noqa: F841  (QImage I/O needs the plugins a GUI app loads)
    master = QImage(str(source))
    if master.isNull():
        print(f"Couldn't read {source}")
        return 1
    ASSETS.mkdir(parents=True, exist_ok=True)
    pad = 6
    mx, my, mw, mh = content_box(master, 0, SPLIT_X)
    ax, ay, aw, ah = content_box(master, 0, master.width())
    mark = master.copy(mx - pad, my - pad, mw + 2 * pad, mh + 2 * pad)
    logo = master.copy(ax - pad, ay - pad, aw + 2 * pad, ah + 2 * pad)
    key_black(mark, SPLIT_X - (mx - pad)).save(str(ASSETS / "graftsnip-mark.png"))
    key_black(logo, SPLIT_X - (ax - pad)).save(str(ASSETS / "graftsnip-logo.png"))
    print(f"mark {mw}x{mh} at ({mx},{my}); logo {aw}x{ah} at ({ax},{ay}) -> {ASSETS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
