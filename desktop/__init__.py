def run_desktop_app():
    from .ui import run_desktop_app as run_ui

    return run_ui()

__all__ = ["run_desktop_app"]
