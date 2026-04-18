from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from desktop.worker import is_worker_invocation, main as run_worker_main


PROJECT_ROOT = Path(__file__).resolve().parent
TUI_SCRIPT = PROJECT_ROOT / "tui.ps1"


def _configure_stdio_utf8() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            continue


def _find_powershell() -> str | None:
    for candidate in ("pwsh", "powershell"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _run_tui() -> int:
    shell = _find_powershell()
    if shell is None:
        print("未找到 PowerShell 运行环境，请使用 pwsh 执行 tui.ps1。", file=sys.stderr)
        return 1

    command = [shell, "-NoLogo", "-NoProfile"]
    if Path(shell).name.lower().startswith("powershell"):
        command += ["-ExecutionPolicy", "Bypass"]
    command += ["-File", str(TUI_SCRIPT)]
    return subprocess.call(command, cwd=PROJECT_ROOT)


def main(argv: list[str] | None = None) -> int:
    _configure_stdio_utf8()
    args = list(sys.argv[1:] if argv is None else argv)
    if is_worker_invocation(args):
        return run_worker_main(args)
    return _run_tui()


if __name__ == "__main__":
    raise SystemExit(main())
