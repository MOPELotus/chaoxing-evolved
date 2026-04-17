from __future__ import annotations

import sys

from api.study_runner import run_named_profile

WORKER_FLAG = "--desktop-worker"


def is_worker_invocation(argv: list[str] | None = None) -> bool:
    args = list(sys.argv[1:] if argv is None else argv)
    return bool(args) and args[0] == WORKER_FLAG


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == [WORKER_FLAG]:
        args = args[1:]

    if len(args) != 1:
        print("桌面运行器需要传入唯一的配置名称。", file=sys.stderr)
        return 2

    run_named_profile(args[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
