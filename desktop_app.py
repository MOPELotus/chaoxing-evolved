from __future__ import annotations

import contextlib
import io
import os

from PyQt5.QtCore import QCoreApplication, Qt
from PyQt5.QtWidgets import QApplication


os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

if hasattr(Qt, "AA_EnableHighDpiScaling"):
    QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
if hasattr(Qt, "AA_UseHighDpiPixmaps"):
    QCoreApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
if hasattr(QApplication, "setHighDpiScaleFactorRoundingPolicy") and hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    from desktop.ui import run_desktop_app


if __name__ == "__main__":
    run_desktop_app()
