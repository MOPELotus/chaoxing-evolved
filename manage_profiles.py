import argparse
from pathlib import Path

from api.profile_config import (
    batch_update_profiles,
    create_profile,
    list_profiles,
    profile_path_from_name,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="批量管理超星配置文件",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="配置文件目录，默认使用仓库下的 profiles 目录",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="列出所有配置文件")
    list_parser.set_defaults(command="list")

    create_parser = subparsers.add_parser("create", help="从模板批量创建配置文件")
    create_parser.add_argument("names", nargs="+", help="配置名称，例如 user1 user2")
    create_parser.add_argument("--force", action="store_true", help="覆盖已存在的配置文件")

    batch_parser = subparsers.add_parser("batch-set", help="批量设置多个配置项")
    batch_parser.add_argument("--all", action="store_true", help="修改目录下所有配置文件")
    batch_parser.add_argument(
        "--profiles",
        nargs="*",
        default=[],
        help="指定要修改的配置名称，例如 user1 user2",
    )
    batch_parser.add_argument(
        "--set",
        dest="assignments",
        action="append",
        required=True,
        help="使用 section.key=value 形式设置配置，例如 tiku.provider=MultiTiku",
    )

    return parser.parse_args()


def resolve_target_profiles(args) -> list[Path]:
    if args.all:
        return list_profiles(args.root)

    return [profile_path_from_name(name, args.root) for name in args.profiles]


def main():
    args = parse_args()

    if args.command == "list":
        profiles = list_profiles(args.root)
        if not profiles:
            print("当前没有配置文件")
            return
        for profile in profiles:
            print(profile)
        return

    if args.command == "create":
        for name in args.names:
            profile_path = create_profile(name, profile_dir=args.root, force=args.force)
            print(f"已创建: {profile_path}")
        return

    if args.command == "batch-set":
        target_profiles = resolve_target_profiles(args)
        if not target_profiles:
            raise SystemExit("未找到要修改的配置文件，请传 --all 或 --profiles")

        missing_profiles = [profile for profile in target_profiles if not profile.exists()]
        if missing_profiles:
            missing_text = "\n".join(str(profile) for profile in missing_profiles)
            raise SystemExit(f"以下配置文件不存在，请先 create：\n{missing_text}")

        updated_profiles = batch_update_profiles(target_profiles, args.assignments)
        for profile in updated_profiles:
            print(f"已更新: {profile}")


if __name__ == "__main__":
    main()
