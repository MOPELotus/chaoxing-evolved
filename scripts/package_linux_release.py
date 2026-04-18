from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path


APP_ID = "chaoxing-desktop"
APP_TITLE = "超星助手桌面版"
REPO_URL = "https://github.com/MOPELotus/chaoxing-evolved"
DESCRIPTION = "超星助手桌面客户端，提供多账号、多题库协同与集中管理能力。"
MAINTAINER = "MOPELotus <noreply@users.noreply.github.com>"
LICENSE_NAME = "GPL-3.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package Linux release assets from a Nuitka standalone directory.")
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--arch", choices=("x64", "arm64"), required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def ensure_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"未找到必需路径: {path}")


def executable_name(dist_dir: Path) -> str:
    candidate = dist_dir / APP_ID
    if candidate.exists():
        return candidate.name

    for path in sorted(dist_dir.iterdir()):
        if path.is_file() and os.access(path, os.X_OK):
            return path.name
    raise FileNotFoundError(f"未找到 Linux 主程序可执行文件: {dist_dir}")


def write_text(path: Path, content: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)


def arch_info(arch: str) -> dict[str, str]:
    if arch == "arm64":
        return {
            "platform_label": "Linux ARM64",
            "deb_arch": "arm64",
            "rpm_arch": "aarch64",
            "appimage_arch": "aarch64",
            "asset_arch": "arm64",
        }
    return {
        "platform_label": "Linux x64",
        "deb_arch": "amd64",
        "rpm_arch": "x86_64",
        "appimage_arch": "x86_64",
        "asset_arch": "x64",
    }


def system_wrapper_script(executable: str) -> str:
    return f"""#!/bin/sh
set -eu
exec /opt/{APP_ID}/{executable} "$@"
"""


def appdir_wrapper_script(executable: str) -> str:
    return f"""#!/bin/sh
set -eu
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec "$SCRIPT_DIR/../lib/{APP_ID}/{executable}" "$@"
"""


def apprun_script() -> str:
    return f"""#!/bin/sh
set -eu
APPDIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec "$APPDIR/usr/bin/{APP_ID}" "$@"
"""


def desktop_file_contents() -> str:
    return f"""[Desktop Entry]
Type=Application
Name={APP_TITLE}
Exec={APP_ID}
Icon={APP_ID}
Categories=Utility;Education;
Terminal=false
StartupWMClass=ChaoxingDesktop
Comment={DESCRIPTION}
"""


def create_system_stage(dist_dir: Path, work_dir: Path, executable: str) -> Path:
    stage_root = work_dir / "system-stage"
    install_dir = stage_root / "opt" / APP_ID
    copy_tree(dist_dir, install_dir)

    write_text(stage_root / "usr" / "bin" / APP_ID, system_wrapper_script(executable), executable=True)
    desktop_path = stage_root / "usr" / "share" / "applications" / f"{APP_ID}.desktop"
    write_text(desktop_path, desktop_file_contents())
    system_icon_path = stage_root / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
    system_icon_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path("packaging") / "app_icon.svg", system_icon_path)
    return stage_root


def create_appdir(dist_dir: Path, work_dir: Path, executable: str) -> Path:
    app_dir = work_dir / f"{APP_ID}.AppDir"
    lib_dir = app_dir / "usr" / "lib" / APP_ID
    copy_tree(dist_dir, lib_dir)

    write_text(app_dir / "AppRun", apprun_script(), executable=True)
    write_text(app_dir / "usr" / "bin" / APP_ID, appdir_wrapper_script(executable), executable=True)

    desktop_path = app_dir / "usr" / "share" / "applications" / f"{APP_ID}.desktop"
    write_text(desktop_path, desktop_file_contents())

    icon_source = Path("packaging") / "app_icon.svg"
    ensure_file(icon_source)
    icon_target = app_dir / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
    icon_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_source, icon_target)

    shutil.copy2(icon_source, app_dir / f"{APP_ID}.svg")
    shutil.copy2(icon_source, app_dir / ".DirIcon")
    shutil.copy2(desktop_path, app_dir / f"{APP_ID}.desktop")
    return app_dir


def run_command(command: list[str], *, env: dict[str, str] | None = None, dry_run: bool = False) -> None:
    print(" ".join(command))
    if dry_run:
        return
    subprocess.run(command, check=True, env=env)


def download_appimagetool(appimage_arch: str, work_dir: Path, *, dry_run: bool) -> Path:
    tool_path = work_dir / f"appimagetool-{appimage_arch}.AppImage"
    url = f"https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-{appimage_arch}.AppImage"
    command = ["curl", "-L", url, "-o", str(tool_path)]
    run_command(command, dry_run=dry_run)
    if not dry_run:
        tool_path.chmod(tool_path.stat().st_mode | stat.S_IXUSR)
    return tool_path


def build_appimage(app_dir: Path, release_dir: Path, tag: str, arch_meta: dict[str, str], *, dry_run: bool) -> Path:
    tool_path = download_appimagetool(arch_meta["appimage_arch"], release_dir.parent, dry_run=dry_run)
    output_path = release_dir / f"chaoxing-evolved-linux-{arch_meta['asset_arch']}-{tag}.AppImage"
    env = os.environ.copy()
    env["ARCH"] = arch_meta["appimage_arch"]
    env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
    run_command([str(tool_path), str(app_dir), str(output_path)], env=env, dry_run=dry_run)
    return output_path


def build_fpm_package(stage_root: Path, release_dir: Path, tag: str, arch_meta: dict[str, str], package_type: str, *, dry_run: bool) -> Path:
    package_arch = arch_meta["deb_arch"] if package_type == "deb" else arch_meta["rpm_arch"]
    extension = "deb" if package_type == "deb" else "rpm"
    output_path = release_dir / f"chaoxing-evolved-linux-{arch_meta['asset_arch']}-{tag}.{extension}"
    command = [
        "fpm",
        "-s",
        "dir",
        "-t",
        package_type,
        "-n",
        APP_ID,
        "-v",
        tag,
        "--architecture",
        package_arch,
        "--license",
        LICENSE_NAME,
        "--url",
        REPO_URL,
        "--maintainer",
        MAINTAINER,
        "--description",
        DESCRIPTION,
        "--package",
        str(output_path),
        "-C",
        str(stage_root),
        ".",
    ]
    run_command(command, dry_run=dry_run)
    return output_path


def main() -> int:
    args = parse_args()
    ensure_file(args.dist_dir)
    ensure_file(Path("packaging") / "app_icon.svg")
    args.release_dir.mkdir(parents=True, exist_ok=True)

    arch_meta = arch_info(args.arch)
    executable = executable_name(args.dist_dir)

    with tempfile.TemporaryDirectory(prefix="chaoxing-linux-package-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        system_stage = create_system_stage(args.dist_dir, temp_dir, executable)
        app_dir = create_appdir(args.dist_dir, temp_dir, executable)

        appimage_path = build_appimage(app_dir, args.release_dir, args.tag, arch_meta, dry_run=args.dry_run)
        deb_path = build_fpm_package(system_stage, args.release_dir, args.tag, arch_meta, "deb", dry_run=args.dry_run)
        rpm_path = build_fpm_package(system_stage, args.release_dir, args.tag, arch_meta, "rpm", dry_run=args.dry_run)

        print(f"Linux package assets prepared for {arch_meta['platform_label']}:")
        print(f"  AppImage: {appimage_path}")
        print(f"  Debian: {deb_path}")
        print(f"  RPM: {rpm_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
