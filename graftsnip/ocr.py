"""Text actions: copy all text and quick-redact, using the OCR engine built into Windows."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage

SCRIPT = Path(__file__).with_name("ocr.ps1")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
NUMBERISH = re.compile(r"\+?\d[\d ()\-.]{6,}\d")


class OcrError(RuntimeError):
    pass


def recognise(image: QImage, timeout: float = 45.0) -> list[dict]:
    """Return [{'text': str, 'words': [{'t': str, 'rect': QRectF}]}] in the image's own pixels."""
    if sys.platform != "win32":
        raise OcrError("Text recognition uses the Windows OCR engine")
    # Small UI text reads far better upscaled; Windows OCR caps dimensions (~10k px).
    scale = 2.0 if max(image.width(), image.height()) * 2 <= 8000 else 1.0
    src = image.convertToFormat(QImage.Format.Format_RGB32)
    if scale != 1.0:
        src = src.scaled(round(src.width() * scale), round(src.height() * scale),
                         Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    work = Path(tempfile.mkdtemp(prefix="graftsnip-ocr-"))
    try:
        png, out = work / "in.png", work / "out.json"
        if not src.save(str(png), "PNG"):
            raise OcrError("Could not write a temporary image for OCR")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(SCRIPT), "-ImagePath", str(png), "-OutPath", str(out)],
            capture_output=True, timeout=timeout, creationflags=flags, check=False)
        if not out.exists():
            raise OcrError("The Windows OCR engine did not respond")
        data = json.loads(out.read_text(encoding="utf-8-sig"))
    except subprocess.TimeoutExpired as exc:
        raise OcrError("Text recognition timed out") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not data.get("ok"):
        raise OcrError(data.get("error") or "Text recognition failed")
    lines = []
    for line in data.get("lines") or []:
        words = [{"t": w.get("t", ""), "rect": QRectF(w["x"] / scale, w["y"] / scale, w["w"] / scale, w["h"] / scale)}
                 for w in (line.get("words") or [])]
        lines.append({"text": line.get("text", ""), "words": words})
    return lines


def plain_text(lines: list[dict]) -> str:
    return "\n".join(line["text"] for line in lines if line["text"].strip())


def sensitive_rects(lines: list[dict]) -> list[QRectF]:
    """Bounding boxes of e-mail addresses and phone/card-like numbers."""
    rects: list[QRectF] = []
    for line in lines:
        words = line["words"]
        if not words:
            continue
        spans, pos = [], 0
        for w in words:
            spans.append((pos, pos + len(w["t"])))
            pos += len(w["t"]) + 1
        joined = " ".join(w["t"] for w in words)
        matches = [m.span() for m in EMAIL.finditer(joined)]
        for m in NUMBERISH.finditer(joined):
            digits = sum(ch.isdigit() for ch in m.group())
            if 9 <= digits <= 19:
                matches.append(m.span())
        for a, b in matches:
            hit = [w["rect"] for w, (s, e) in zip(words, spans) if s < b and e > a]
            if hit:
                r = QRectF(hit[0])
                for h in hit[1:]:
                    r = r.united(h)
                rects.append(r)
    return rects
