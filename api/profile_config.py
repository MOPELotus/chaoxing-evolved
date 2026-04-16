from __future__ import annotations

import configparser
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE_PATH = PROJECT_ROOT / "config_template.ini"
DEFAULT_PROFILE_DIR = PROJECT_ROOT / "profiles"


def ensure_profile_dir(profile_dir: Path | None = None) -> Path:
    target_dir = Path(profile_dir) if profile_dir else DEFAULT_PROFILE_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def normalize_profile_name(name: str) -> str:
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("配置名称不能为空")
    return cleaned_name if cleaned_name.endswith(".ini") else f"{cleaned_name}.ini"


def profile_path_from_name(name: str, profile_dir: Path | None = None) -> Path:
    return ensure_profile_dir(profile_dir) / normalize_profile_name(name)


def list_profiles(profile_dir: Path | None = None) -> list[Path]:
    target_dir = ensure_profile_dir(profile_dir)
    return sorted(target_dir.glob("*.ini"))


def load_profile_config(path: Path) -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(path, encoding="utf8")
    return config


def config_to_dict(config: configparser.ConfigParser) -> dict[str, dict[str, str]]:
    return {section: dict(config.items(section)) for section in config.sections()}


def save_profile_config(config: configparser.ConfigParser, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf8") as fp:
        config.write(fp)


def create_profile(name: str, profile_dir: Path | None = None, template_path: Path | None = None, force: bool = False) -> Path:
    profile_path = profile_path_from_name(name, profile_dir)
    source_template = Path(template_path) if template_path else DEFAULT_TEMPLATE_PATH
    if profile_path.exists() and not force:
        return profile_path

    profile_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_template, profile_path)
    return profile_path


def parse_assignment(assignment: str) -> tuple[str, str, str]:
    if "=" not in assignment:
        raise ValueError(f"无效的赋值表达式: {assignment}")
    key, value = assignment.split("=", 1)
    if "." not in key:
        raise ValueError(f"赋值表达式必须使用 section.key=value 形式: {assignment}")
    section, option = key.split(".", 1)
    section = section.strip()
    option = option.strip()
    if not section or not option:
        raise ValueError(f"赋值表达式缺少 section 或 key: {assignment}")
    return section, option, value


def update_profile_values(path: Path, assignments: list[str]) -> Path:
    config = load_profile_config(path)
    for assignment in assignments:
        section, option, value = parse_assignment(assignment)
        if not config.has_section(section):
            config.add_section(section)
        config.set(section, option, value)
    save_profile_config(config, path)
    return path


def batch_update_profiles(profile_paths: list[Path], assignments: list[str]) -> list[Path]:
    updated_paths = []
    for profile_path in profile_paths:
        updated_paths.append(update_profile_values(profile_path, assignments))
    return updated_paths


def read_profile_raw(path: Path) -> str:
    return path.read_text(encoding="utf8")


def save_profile_raw(path: Path, raw_text: str) -> Path:
    config = configparser.ConfigParser()
    config.read_string(raw_text)
    path.write_text(raw_text, encoding="utf8")
    return path


def build_profile_snapshot(path: Path) -> dict:
    config = load_profile_config(path)
    config_dict = config_to_dict(config)
    common = config_dict.get("common", {})
    tiku = config_dict.get("tiku", {})
    return {
        "name": path.name,
        "path": str(path),
        "username": common.get("username", ""),
        "use_cookies": common.get("use_cookies", "false"),
        "course_list": common.get("course_list", ""),
        "speed": common.get("speed", "1"),
        "provider": tiku.get("provider", ""),
        "providers": tiku.get("providers", ""),
        "decision_provider": tiku.get("decision_provider", ""),
        "sections": config_dict,
    }
