"""Persistent user settings (JSON in %APPDATA%\\GraftSnip\\settings.json)."""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "GraftSnip"
__version__ = "1.2.0"


def config_dir() -> Path:
    d = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


DEFAULTS: dict = {
    "hotkey": "Ctrl+Shift+S",
    "save_dir": str(Path.home() / "Pictures" / "GraftSnip"),
    "copy_on_capture": True,
    "copy_on_done": True,
    "save_on_done": True,
    "magnifier": True,
    "shadows": True,
    "thumbnails": True,          # keep each snip as a draggable thumbnail in the bottom-left corner
    "editor_on_capture": True,   # open the editor straight after a snip (off = thumbnail only)
    "tool": "arrow",
    "color": "#FF3B30",
    "marker_color": "#FFD60A",
    "size": 1,
    "shape": "rect",
    "shape_fill": False,
    "arrow_style": "tapered",
    "text_style": "label",
    "pixel_mode": "pixelate",
    "spot_shape": "rect",
    "welcomed": False,
}


class Settings:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else config_dir() / "settings.json"
        self.data = dict(DEFAULTS)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                if k in DEFAULTS and type(v) is type(DEFAULTS[k]):
                    self.data[k] = v

    def __getitem__(self, key: str):
        return self.data[key]

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        if self.data.get(key) == value:
            return
        self.data[key] = value
        self.save()

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass
