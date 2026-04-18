from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from api.study_runner import run_named_profile

from tui.state import ensure_tui_state, update_run_record


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a profile inside the TUI host process.")
    parser.add_argument("--profile", required=True, help="Profile name")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--log-path", required=True, help="Log file path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ensure_tui_state()
    log_path = Path(args.log_path).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    os.environ["DESKTOP_MANAGED_RUN"] = "1"
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"

    update_run_record(
        args.profile,
        {
            "run_id": args.run_id,
            "host_pid": os.getpid(),
            "status": "running",
            "stop_requested": False,
        },
    )

    exit_code = 0
    final_status = "completed"
    with log_path.open("a", encoding="utf8", buffering=1) as stream:
        with redirect_stdout(stream), redirect_stderr(stream):
            print(f"[系统] {time.strftime('%Y-%m-%d %H:%M:%S')} 任务宿主已启动")
            print(f"[系统] 档案: {args.profile}")
            print(f"[系统] 宿主 PID: {os.getpid()}")
            try:
                run_named_profile(args.profile)
            except KeyboardInterrupt:
                final_status = "stopped"
                exit_code = 130
                print(f"[系统] {time.strftime('%Y-%m-%d %H:%M:%S')} 任务被中断")
            except BaseException as exc:
                final_status = "failed"
                exit_code = 1
                print(f"[系统] {time.strftime('%Y-%m-%d %H:%M:%S')} 任务异常: {type(exc).__name__}: {exc}")
                traceback.print_exc()
            else:
                print(f"[系统] {time.strftime('%Y-%m-%d %H:%M:%S')} 任务执行完成")

    update_run_record(
        args.profile,
        {
            "run_id": args.run_id,
            "status": final_status,
            "exit_code": exit_code,
            "ended_at": time.time(),
            "host_pid": os.getpid(),
            "log_path": str(log_path),
        },
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

