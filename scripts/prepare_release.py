from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare release assets for GitHub Actions.")
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--ref-name", required=True)
    parser.add_argument("--sha", required=True)
    return parser.parse_args()


def find_dist_dir(build_dir: Path) -> Path:
    dist_dirs = sorted(path for path in build_dir.iterdir() if path.is_dir() and path.name.endswith(".dist"))
    if not dist_dirs:
        raise FileNotFoundError(f"未找到 Nuitka 构建输出目录: {build_dir}")
    return dist_dirs[0]


def recent_commits() -> str:
    result = subprocess.run(
        ["git", "log", "--oneline", "-20"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def write_release_notes(path: Path, ref_name: str, sha: str) -> None:
    notes = [
        "本次版本由 GitHub Actions 手动触发构建生成。",
        "",
        "- 目标平台：Windows x64",
        "- 构建方式：Nuitka standalone",
        f"- 触发分支：{ref_name}",
        f"- 提交哈希：{sha}",
        "",
        "最近提交：",
        recent_commits(),
    ]
    path.write_text("\n".join(notes) + "\n", encoding="utf-8")


def create_archive(dist_dir: Path, release_dir: Path, tag: str) -> Path:
    archive_base = release_dir / f"chaoxing-evolved-windows-x64-{tag}"
    archive_path = shutil.make_archive(str(archive_base), "zip", root_dir=dist_dir)
    return Path(archive_path)


def main() -> int:
    args = parse_args()
    args.release_dir.mkdir(parents=True, exist_ok=True)

    dist_dir = find_dist_dir(args.build_dir)
    archive_path = create_archive(dist_dir, args.release_dir, args.tag)
    write_release_notes(args.release_dir / "RELEASE_NOTES.md", args.ref_name, args.sha)

    print(f"Prepared archive: {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
