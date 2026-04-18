from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TUI_STATE_DIR = PROJECT_ROOT / "desktop_state" / "tui"
RUNS_STATE_PATH = TUI_STATE_DIR / "runs.json"
RUN_LOG_DIR = PROJECT_ROOT / "desktop_state" / "logs"


def ensure_tui_state() -> None:
    TUI_STATE_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _default_runs_payload() -> dict[str, Any]:
    return {"schema_version": 1, "runs": {}}


def load_runs_payload() -> dict[str, Any]:
    ensure_tui_state()
    if not RUNS_STATE_PATH.exists():
        return _default_runs_payload()

    with RUNS_STATE_PATH.open("r", encoding="utf8") as fp:
        payload = json.load(fp)

    if not isinstance(payload, dict):
        return _default_runs_payload()
    payload.setdefault("schema_version", 1)
    payload.setdefault("runs", {})
    return payload


def save_runs_payload(payload: dict[str, Any]) -> Path:
    ensure_tui_state()
    RUNS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUNS_STATE_PATH.open("w", encoding="utf8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    return RUNS_STATE_PATH


def build_run_log_path(profile_name: str, started_at: float, run_id: str) -> Path:
    date_prefix = datetime.fromtimestamp(started_at).strftime("%Y%m%d-%H%M%S")
    profile_dir = RUN_LOG_DIR / profile_name
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir / f"{date_prefix}-{run_id}.log"


def process_exists(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False

    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf8",
            errors="replace",
            check=False,
        )
        output = result.stdout.strip()
        return bool(output) and "No tasks are running" not in output and "信息:" not in output

    with suppress(ProcessLookupError):
        os.kill(pid, 0)
        return True
    return False


def update_run_record(profile_name: str, patch: dict[str, Any]) -> dict[str, Any]:
    payload = load_runs_payload()
    record = dict(payload.get("runs", {}).get(profile_name, {}))
    record.update(patch)
    record["profile_name"] = profile_name
    payload["runs"][profile_name] = record
    save_runs_payload(payload)
    return record


def get_run_record(profile_name: str, refresh: bool = False) -> dict[str, Any] | None:
    payload = refresh_runs_payload() if refresh else load_runs_payload()
    record = payload.get("runs", {}).get(profile_name)
    if not isinstance(record, dict):
        return None
    return record


def refresh_runs_payload() -> dict[str, Any]:
    payload = load_runs_payload()
    changed = False

    for profile_name, record in list(payload.get("runs", {}).items()):
        if not isinstance(record, dict):
            payload["runs"].pop(profile_name, None)
            changed = True
            continue

        if record.get("status") == "running" and not process_exists(int(record.get("host_pid") or 0)):
            record["ended_at"] = record.get("ended_at") or time.time()
            if record.get("stop_requested"):
                record["status"] = "stopped"
            elif record.get("exit_code") is None:
                record["status"] = "failed"
                record["note"] = "后台宿主已退出。"
            else:
                record["status"] = "completed" if int(record.get("exit_code", 1)) == 0 else "failed"
            changed = True

    if changed:
        save_runs_payload(payload)
    return payload


def list_run_records(refresh: bool = True) -> list[dict[str, Any]]:
    payload = refresh_runs_payload() if refresh else load_runs_payload()
    runs = payload.get("runs", {})
    if not isinstance(runs, dict):
        return []
    return sorted(
        [record for record in runs.values() if isinstance(record, dict)],
        key=lambda item: float(item.get("started_at") or 0),
        reverse=True,
    )


def terminate_process_tree(pid: int | None) -> None:
    if not pid or pid <= 0:
        return

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf8",
            errors="replace",
            check=False,
        )
        return

    with suppress(ProcessLookupError):
        os.killpg(pid, 15)


def python_entry_command() -> list[str]:
    return [sys.executable]

