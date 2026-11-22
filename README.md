# GraftSnip

Snip, point and label. GraftSnip is a quick screenshot and annotation tool for Windows: take a snip, mark it up with arrows, labels, numbered steps and redaction, then copy or save it.

Part of the [Grafthouse](https://github.com/grafthouse) family of tools.

## Features

- **Snip** any area with a global shortcut (Ctrl+Shift+S by default), or from the tray icon. A magnifier helps you land on the exact pixel.
- **Timed snips** in 3 or 10 seconds, for menus and hover states.
- **Annotate** with arrows, pen and highlighter, shapes, text labels, numbered steps, spotlight, eraser and crop.
- **Redact** with pixelate or blur.
- **Copy all text** from an image (Ctrl+Shift+C), and **quick redact** e-mail addresses and phone or card-like numbers it finds.
- **Thumbnails** of recent snips in the corner of the screen, ready to drag into other apps. GraftSnip hides its own windows from screen captures, so they never end up in your snips.
- Copies to the clipboard and saves to `Pictures\GraftSnip` automatically (both can be turned off in Settings).

## Privacy

GraftSnip works entirely offline. It makes no network calls, and text recognition uses the OCR engine built into Windows. Settings and a log file are kept in `%APPDATA%\GraftSnip`, and your snips stay where you save them.

## Download

Get the Windows installer from the [GraftSnip page](https://grafthouse.vercel.app/projects/graftsnip) or the [Grafthouse releases](https://github.com/grafthouse/releases/releases). It installs for your user account only, so no admin rights are needed, and it can be removed from Settings > Apps.

The installer is not code-signed yet, so Windows SmartScreen may show "Windows protected your PC". Choose **More info**, then **Run anyway**.

## Requirements

- Windows 10 or 11
- An OCR language pack for your Windows display language (only for the text features; most Windows installs already have one)
- To run from source: Python 3.15 or later

## Run from source

```powershell
git clone https://github.com/grafthouse/graftsnip.git
cd graftsnip
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
```

`install.ps1` builds the app icon and adds GraftSnip shortcuts to the Start menu and your desktop (current user only).

## Use

```powershell
.venv\Scripts\pythonw.exe GraftSnip.pyw            # take a snip now (starts the tray app if needed)
.venv\Scripts\pythonw.exe GraftSnip.pyw --tray     # start quietly in the system tray
.venv\Scripts\pythonw.exe GraftSnip.pyw image.png  # open an image for annotation
```

Press the shortcut, drag around what you want, then annotate in the editor. The editor's help panel lists every keyboard shortcut.

## Build the installer

Needs [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`).

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build.ps1
```

It freezes the app with PyInstaller in its own build environment, then writes `packaging\out\GraftSnip-<version>-windows-x64-setup.exe`.

## Tests

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest
```

## Licence

The code is MIT licensed. The GraftSnip and Grafthouse names and logos (`graftsnip/assets/` and the app icon built from them) are not, and all rights are reserved. See [LICENSE](LICENSE) for the code and
[TRADEMARKS.md](TRADEMARKS.md) for what you may do with the names and logos.
