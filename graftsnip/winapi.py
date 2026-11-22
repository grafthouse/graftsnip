"""Thin ctypes wrappers for the Win32 bits Qt does not cover.

Everything degrades to a no-op off Windows so the editor can still be tested elsewhere.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
from ctypes import wintypes

log = logging.getLogger("graftsnip.winapi")
IS_WINDOWS = sys.platform == "win32"

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT, WM_USER, PM_NOREMOVE = 0x0312, 0x0012, 0x0400, 0x0000

_NAMED_KEYS = {
    "PRINT": 0x2C, "PRINTSCREEN": 0x2C, "SYSREQ": 0x2C, "PAUSE": 0x13, "INS": 0x2D, "INSERT": 0x2D,
    "DEL": 0x2E, "DELETE": 0x2E, "HOME": 0x24, "END": 0x23, "PGUP": 0x21, "PGDOWN": 0x22,
    "SPACE": 0x20, "SCROLLLOCK": 0x91, "TAB": 0x09, "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB,
    "]": 0xDD, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
}

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    shell32 = ctypes.WinDLL("shell32")

    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                    wintypes.UINT]
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def parse_hotkey(seq: str) -> tuple[int, int] | None:
    """'Ctrl+Alt+S' -> (modifiers, virtual-key). None when unsupported."""
    if not seq:
        return None
    parts = [p for p in seq.replace(" ", "").split("+")]
    if seq.endswith("++"):
        parts = parts[:-2] + ["="]
    mods, key = 0, None
    for part in parts:
        u = part.upper()
        if not u:
            continue
        if u in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif u == "ALT":
            mods |= MOD_ALT
        elif u == "SHIFT":
            mods |= MOD_SHIFT
        elif u in ("META", "WIN", "WINDOWS"):
            mods |= MOD_WIN
        else:
            key = u
    if key is None:
        return None
    if len(key) == 1 and key.isalnum():
        vk = ord(key)
    elif key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif key in _NAMED_KEYS:
        vk = _NAMED_KEYS[key]
    else:
        return None
    if mods == 0 and vk not in (0x2C, 0x13, 0x91) and not (0x70 <= vk <= 0x87):
        return None  # a bare letter as a global hotkey would hijack normal typing
    return mods, vk


class HotkeyListener:
    """Registers one global hotkey on a private thread with its own message loop."""

    def __init__(self, callback):
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ready = threading.Event()
        self.ok = False
        self.error = ""

    def start(self, seq: str) -> bool:
        self.stop()
        if not IS_WINDOWS:
            self.ok, self.error = False, "Global hotkeys need Windows"
            return False
        parsed = parse_hotkey(seq)
        if parsed is None:
            self.ok, self.error = False, f"'{seq}' can't be used as a global hotkey"
            return False
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, args=parsed, name="GraftSnipHotkey", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        return self.ok

    def _run(self, mods: int, vk: int) -> None:
        msg = wintypes.MSG()
        self._tid = kernel32.GetCurrentThreadId()
        user32.PeekMessageW(ctypes.byref(msg), None, WM_USER, WM_USER, PM_NOREMOVE)  # create the queue
        if not user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk):
            self.ok = False
            self.error = "That shortcut is already used by another app"
            self._ready.set()
            return
        self.ok, self.error = True, ""
        self._ready.set()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    log.info("WM_HOTKEY received")
                    try:
                        self._callback()
                    except Exception:  # never let a slot error kill the hotkey thread
                        log.exception("hotkey callback failed")
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self._thread and self._thread.is_alive() and self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._thread.join(1.5)
        self._thread, self._tid, self.ok = None, 0, False


def _text(fn, hwnd, n=512) -> str:
    buf = ctypes.create_unicode_buffer(n)
    fn(hwnd, buf, n)
    return buf.value


def list_windows() -> list[tuple[int, str, tuple[int, int, int, int]]]:
    """Visible top-level windows, top of the Z-order first, as physical-pixel rects."""
    if not IS_WINDOWS:
        return []
    out: list[tuple[int, str, tuple[int, int, int, int]]] = []
    me = os.getpid()

    def cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == me:
                return True
            cloaked = ctypes.c_int(0)
            if dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4) == 0 and cloaked.value:
                return True
            if user32.GetWindowLongW(hwnd, -20) & 0x20:  # WS_EX_TRANSPARENT click-through overlays
                return True
            cls = _text(user32.GetClassNameW, hwnd, 256)
            if cls in ("Progman", "WorkerW"):
                return True
            title = _text(user32.GetWindowTextW, hwnd)
            if not title and cls not in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                return True
            r = wintypes.RECT()
            if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
                user32.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left >= 8 and r.bottom - r.top >= 8:
                out.append((int(hwnd or 0), title or "Taskbar", (r.left, r.top, r.right, r.bottom)))
        except Exception:
            pass
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def bring_to_front(hwnd: int) -> None:
    if not IS_WINDOWS or not hwnd:
        return
    fg = user32.GetForegroundWindow()
    cur = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_tid and fg_tid != cur and user32.AttachThreadInput(fg_tid, cur, True))
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    if attached:
        user32.AttachThreadInput(fg_tid, cur, False)


def set_dark_titlebar(hwnd: int, caption_rgb: tuple[int, int, int] | None = (31, 31, 34)) -> None:
    if not IS_WINDOWS or not hwnd:
        return
    on = ctypes.c_int(1)
    dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4)  # DWMWA_USE_IMMERSIVE_DARK_MODE
    if caption_rgb:
        r, g, b = caption_rgb
        colour = ctypes.c_uint(r | (g << 8) | (b << 16))
        dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colour), 4)  # DWMWA_CAPTION_COLOR (Win11)


def animations_enabled() -> bool:
    """Windows' Settings > Accessibility > Visual effects > Animation effects (SPI_GETCLIENTAREAANIMATION)."""
    if not IS_WINDOWS:
        return True
    try:
        val = wintypes.BOOL(True)
        if user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(val), 0):
            return bool(val.value)
    except Exception:
        pass
    return True


def exclude_from_capture(hwnd: int) -> bool:
    """Keep a window out of every screen grab (GraftSnip's own included) while it stays on screen.

    Needs Windows 10 2004+; returns False where unsupported so the caller can hide the window instead.
    """
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        fn = user32.SetWindowDisplayAffinity
        fn.argtypes = [wintypes.HWND, wintypes.DWORD]
        fn.restype = wintypes.BOOL
        return bool(fn(hwnd, 0x11))  # WDA_EXCLUDEFROMCAPTURE
    except Exception:
        return False


def set_app_id(app_id: str) -> None:
    if IS_WINDOWS:
        try:
            shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
        except Exception:
            pass


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def get_autostart(name: str) -> bool:
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, name)
            return True
    except OSError:
        return False


def set_autostart(name: str, command: str | None) -> bool:
    """Add (command given) or remove (None) a per-user Run entry."""
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if command:
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(k, name)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
