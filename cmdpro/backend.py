from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cmdpro import service


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def print_json(payload: Any) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default))
    sys.stdout.write("\n")
    return 0


def _read_json_stdin() -> Any:
    data = sys.stdin.read()
    if not data.strip():
        raise service.ServiceError("未从标准输入读取到 JSON 内容。")
    return json.loads(data)


def command_provider_catalog(_args: argparse.Namespace) -> int:
    return print_json(service.available_provider_catalog())


def command_list_profiles(_args: argparse.Namespace) -> int:
    return print_json(service.list_profiles_view())


def command_get_profile(args: argparse.Namespace) -> int:
    return print_json(service.profile_view(args.name))


def command_save_profile(_args: argparse.Namespace) -> int:
    return print_json(service.save_profile(_read_json_stdin()))


def command_create_profile(args: argparse.Namespace) -> int:
    return print_json(service.create_profile(args.name))


def command_delete_profile(args: argparse.Namespace) -> int:
    return print_json(service.remove_profile(args.name, force=args.force))


def command_get_global(_args: argparse.Namespace) -> int:
    return print_json(service.load_global_view())


def command_save_global(_args: argparse.Namespace) -> int:
    return print_json(service.save_global_view(_read_json_stdin()))


def command_fetch_courses(args: argparse.Namespace) -> int:
    return print_json(service.fetch_courses(args.name))


def command_list_runs(_args: argparse.Namespace) -> int:
    return print_json(service.list_runs_view())


def command_start_run(args: argparse.Namespace) -> int:
    return print_json(service.start_run(args.name))


def command_stop_run(args: argparse.Namespace) -> int:
    return print_json(service.stop_run(args.name))


def command_read_log(args: argparse.Namespace) -> int:
    return print_json(service.read_log(args.name, lines=args.lines))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chaoxing command line Pro backend bridge.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("provider-catalog").set_defaults(func=command_provider_catalog)
    subparsers.add_parser("list-profiles").set_defaults(func=command_list_profiles)

    get_profile = subparsers.add_parser("get-profile")
    get_profile.add_argument("--name", required=True)
    get_profile.set_defaults(func=command_get_profile)

    subparsers.add_parser("save-profile").set_defaults(func=command_save_profile)

    create_profile = subparsers.add_parser("create-profile")
    create_profile.add_argument("--name", required=True)
    create_profile.set_defaults(func=command_create_profile)

    delete_profile = subparsers.add_parser("delete-profile")
    delete_profile.add_argument("--name", required=True)
    delete_profile.add_argument("--force", action="store_true")
    delete_profile.set_defaults(func=command_delete_profile)

    subparsers.add_parser("get-global").set_defaults(func=command_get_global)
    subparsers.add_parser("save-global").set_defaults(func=command_save_global)

    fetch_courses = subparsers.add_parser("fetch-courses")
    fetch_courses.add_argument("--name", required=True)
    fetch_courses.set_defaults(func=command_fetch_courses)

    subparsers.add_parser("list-runs").set_defaults(func=command_list_runs)

    start_run = subparsers.add_parser("start-run")
    start_run.add_argument("--name", required=True)
    start_run.set_defaults(func=command_start_run)

    stop_run = subparsers.add_parser("stop-run")
    stop_run.add_argument("--name", required=True)
    stop_run.set_defaults(func=command_stop_run)

    read_log = subparsers.add_parser("read-log")
    read_log.add_argument("--name", required=True)
    read_log.add_argument("--lines", type=int, default=30)
    read_log.set_defaults(func=command_read_log)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except service.ServiceError as exc:
        print_json({"error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

