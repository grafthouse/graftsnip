"""Launch GraftSnip (use pythonw.exe so no console window appears).

    GraftSnip.pyw            take a snip now (starts the tray app if needed)
    GraftSnip.pyw --tray     start quietly in the system tray
    GraftSnip.pyw image.png  open an image for annotation
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from graftsnip.app import main  # noqa: E402

raise SystemExit(main())
