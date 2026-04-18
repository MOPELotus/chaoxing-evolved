from __future__ import annotations

import contextlib
import io
import os
import sys

from desktop.worker import is_worker_invocation, main as run_worker_main


def _configure_stdio_utf8() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            continue


def _run_desktop_ui() -> int:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

    if hasattr(QApplication, "setHighDpiScaleFactorRoundingPolicy") and hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from desktop.ui import run_desktop_app

    return run_desktop_app()


def main(argv: list[str] | None = None) -> int:
    _configure_stdio_utf8()
    args = list(sys.argv[1:] if argv is None else argv)
    if is_worker_invocation(args):
        return run_worker_main(args)
    return _run_desktop_ui()


if __name__ == "__main__":
    raise SystemExit(main())
