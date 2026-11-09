from __future__ import annotations

import argparse
import json
import mimetypes
import signal
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from api.logger import logger
from web_dashboard.service import DashboardService


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "ChaoxingDashboard/1.0"

    @property
    def service(self) -> DashboardService:
        return self.server.dashboard_service  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}", flush=True)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                self._json({"ok": True})
                return
            if parsed.path == "/api/dashboard":
                self._json(self.service.dashboard())
                return
            match = self._match_tasks(parsed.path)
            if match:
                username, key, point_id = match
                self._json(self.service.chapter_tasks(username, key, point_id))
                return
            match = self._match_assessments(parsed.path)
            if match:
                kind = parse_qs(parsed.query).get("kind", ["homework"])[0]
                self._json(self.service.assessments(match, kind))
                return
            self._static(parsed.path)
        except (BrokenPipeError, ConnectionResetError):
            return
        except KeyError as exc:
            self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.BAD_GATEWAY)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/refresh":
            try:
                accepted = self.service.request_refresh()
                self._json({"accepted": accepted}, HTTPStatus.ACCEPTED)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    @staticmethod
    def _match_tasks(path: str) -> tuple[str, str, str] | None:
        parts = path.strip("/").split("/")
        if len(parts) == 8 and parts[:2] == ["api", "accounts"] and parts[3] == "courses" and parts[5] == "chapters" and parts[7] == "tasks":
            return unquote(parts[2]), unquote(parts[4]), unquote(parts[6])
        return None

    @staticmethod
    def _match_assessments(path: str) -> str | None:
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "accounts"] and parts[3] == "assessments":
            return unquote(parts[2])
        return None

    def _json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, url_path: str) -> None:
        names = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/styles.css": "styles.css"}
        name = names.get(url_path)
        if not name:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        path = STATIC_DIR / name
        body = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime}; charset=utf-8" if mime.startswith("text/") or mime == "application/javascript" else mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(body)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chaoxing read-only intranet dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--refresh-seconds", type=int, default=180)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="INFO", colorize=False)
    service = DashboardService(ROOT, refresh_seconds=args.refresh_seconds)
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    server.daemon_threads = True
    server.dashboard_service = service  # type: ignore[attr-defined]
    stop_once = threading.Event()

    def stop_server(_signum=None, _frame=None):
        if stop_once.is_set():
            return
        stop_once.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop_server)
    signal.signal(signal.SIGTERM, stop_server)
    service.start()
    print(f"Dashboard listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        service.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
