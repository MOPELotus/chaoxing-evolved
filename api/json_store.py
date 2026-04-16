from __future__ import annotations

import configparser
import json
from copy import deepcopy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DESKTOP_STATE_DIR = PROJECT_ROOT / "desktop_state"
JSON_PROFILE_DIR = DESKTOP_STATE_DIR / "profiles"
RUNTIME_CONFIG_DIR = DESKTOP_STATE_DIR / "runtime_configs"
GLOBAL_SETTINGS_PATH = DESKTOP_STATE_DIR / "global_settings.json"
LEGACY_PROFILE_DIR = PROJECT_ROOT / "profiles"

CURRENT_SCHEMA_VERSION = 1

DEFAULT_GLOBAL_SETTINGS = {
    "schema_version": CURRENT_SCHEMA_VERSION,
    "theme": {
        "accent": "snow",
    },
    "defaults": {
        "tiku": {
            "tokens": "",
            "endpoint": "",
            "key": "",
            "model": "",
            "http_proxy": "",
            "min_interval_seconds": "3",
            "siliconflow_key": "",
            "siliconflow_model": "deepseek-ai/DeepSeek-R1",
            "siliconflow_endpoint": "https://api.siliconflow.cn/v1/chat/completions",
            "url": "",
            "likeapi_search": "false",
            "likeapi_vision": "true",
            "likeapi_model": "glm-4.5-air",
            "likeapi_retry": "true",
            "likeapi_retry_times": "3",
        },
        "notification": {
            "provider": "",
            "url": "",
            "tg_chat_id": "",
        },
    },
}

DEFAULT_PROFILE = {
    "schema_version": CURRENT_SCHEMA_VERSION,
    "name": "",
    "common": {
        "use_cookies": False,
        "cookies_path": "",
        "cache_path": "",
        "username": "",
        "password": "",
        "course_list": [],
        "speed": 1.0,
        "jobs": 4,
        "notopen_action": "retry",
    },
    "tiku": {
        "provider": "TikuYanxi",
        "providers": [],
        "decision_provider": "SiliconFlow",
        "check_llm_connection": True,
        "submit": False,
        "cover_rate": 0.9,
        "delay": 1.0,
        "tokens": "",
        "likeapi_search": False,
        "likeapi_vision": True,
        "likeapi_model": "glm-4.5-air",
        "likeapi_retry": True,
        "likeapi_retry_times": 3,
        "url": "",
        "endpoint": "",
        "key": "",
        "model": "",
        "min_interval_seconds": 3,
        "http_proxy": "",
        "siliconflow_key": "",
        "siliconflow_model": "deepseek-ai/DeepSeek-R1",
        "siliconflow_endpoint": "https://api.siliconflow.cn/v1/chat/completions",
        "true_list": ["正确", "对", "√", "是"],
        "false_list": ["错误", "错", "×", "否", "不对", "不正确"],
    },
    "notification": {
        "provider": "",
        "url": "",
        "tg_chat_id": "",
    },
}


def ensure_desktop_state() -> None:
    DESKTOP_STATE_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _deep_merge(base: dict, override: dict) -> dict:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def sanitize_profile_name(name: str) -> str:
    cleaned = name.strip()
    for invalid in '<>:"/\\|?*':
        cleaned = cleaned.replace(invalid, "_")
    if not cleaned:
        raise ValueError("配置名称不能为空")
    return cleaned


def profile_json_path(name: str) -> Path:
    ensure_desktop_state()
    safe_name = sanitize_profile_name(name)
    return JSON_PROFILE_DIR / f"{safe_name}.json"


def list_json_profiles() -> list[Path]:
    ensure_desktop_state()
    return sorted(JSON_PROFILE_DIR.glob("*.json"))


def load_json_file(path: Path, default: dict) -> dict:
    if not path.exists():
        return deepcopy(default)

    with path.open("r", encoding="utf8") as fp:
        data = json.load(fp)
    return _deep_merge(default, data)


def save_json_file(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    return path


def load_global_settings() -> dict:
    ensure_desktop_state()
    return load_json_file(GLOBAL_SETTINGS_PATH, DEFAULT_GLOBAL_SETTINGS)


def save_global_settings(settings: dict) -> Path:
    ensure_desktop_state()
    payload = _deep_merge(DEFAULT_GLOBAL_SETTINGS, settings)
    return save_json_file(GLOBAL_SETTINGS_PATH, payload)


def create_json_profile(name: str, force: bool = False) -> dict:
    ensure_desktop_state()
    profile_name = sanitize_profile_name(name)
    path = profile_json_path(profile_name)
    if path.exists() and not force:
        return load_json_profile(profile_name)

    profile = deepcopy(DEFAULT_PROFILE)
    profile["name"] = profile_name
    save_json_profile(profile)
    return profile


def load_json_profile(name: str) -> dict:
    ensure_desktop_state()
    path = profile_json_path(name)
    profile = load_json_file(path, DEFAULT_PROFILE)
    profile["name"] = sanitize_profile_name(profile.get("name") or name)
    return profile


def save_json_profile(profile: dict) -> Path:
    ensure_desktop_state()
    profile_name = sanitize_profile_name(profile.get("name", ""))
    payload = _deep_merge(DEFAULT_PROFILE, profile)
    payload["name"] = profile_name
    return save_json_file(profile_json_path(profile_name), payload)


def _bool_to_ini(value: object) -> str:
    return "true" if bool(value) else "false"


def _serialize_profile_section(profile_section: dict, defaults_section: dict | None = None) -> dict[str, str]:
    result: dict[str, str] = {}
    defaults_section = defaults_section or {}

    for key, value in profile_section.items():
        candidate = value
        if candidate in ("", None, []):
            candidate = defaults_section.get(key, candidate)

        if isinstance(candidate, bool):
            result[key] = _bool_to_ini(candidate)
        elif isinstance(candidate, list):
            result[key] = ",".join(str(item) for item in candidate)
        else:
            result[key] = str(candidate)

    for key, value in defaults_section.items():
        if key in result:
            continue
        if isinstance(value, bool):
            result[key] = _bool_to_ini(value)
        elif isinstance(value, list):
            result[key] = ",".join(str(item) for item in value)
        else:
            result[key] = str(value)

    return result


def build_runtime_sections(profile: dict, global_settings: dict | None = None) -> dict[str, dict[str, str]]:
    settings = global_settings or load_global_settings()
    defaults = settings.get("defaults", {})

    common_section = _serialize_profile_section(profile.get("common", {}))
    common_section["course_list"] = ",".join(profile.get("common", {}).get("course_list", []))

    tiku_section = _serialize_profile_section(
        profile.get("tiku", {}),
        defaults.get("tiku", {}),
    )
    notification_section = _serialize_profile_section(
        profile.get("notification", {}),
        defaults.get("notification", {}),
    )
    return {
        "common": common_section,
        "tiku": tiku_section,
        "notification": notification_section,
    }


def write_runtime_ini(profile: dict, global_settings: dict | None = None) -> Path:
    ensure_desktop_state()
    config = configparser.ConfigParser()
    runtime_sections = build_runtime_sections(profile, global_settings)
    for section_name, values in runtime_sections.items():
        config[section_name] = values

    runtime_path = RUNTIME_CONFIG_DIR / f"{sanitize_profile_name(profile['name'])}.ini"
    with runtime_path.open("w", encoding="utf8") as fp:
        config.write(fp)
    return runtime_path


def load_ini_profile(path: Path) -> dict:
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf8")

    profile = deepcopy(DEFAULT_PROFILE)
    profile["name"] = path.stem
    common = dict(parser.items("common")) if parser.has_section("common") else {}
    tiku = dict(parser.items("tiku")) if parser.has_section("tiku") else {}
    notification = dict(parser.items("notification")) if parser.has_section("notification") else {}

    profile["common"].update(
        {
            "use_cookies": common.get("use_cookies", "false").lower() == "true",
            "cookies_path": common.get("cookies_path", ""),
            "cache_path": common.get("cache_path", ""),
            "username": common.get("username", ""),
            "password": common.get("password", ""),
            "course_list": [item.strip() for item in common.get("course_list", "").split(",") if item.strip()],
            "speed": float(common.get("speed", 1.0)),
            "jobs": int(common.get("jobs", 4)),
            "notopen_action": common.get("notopen_action", "retry"),
        }
    )

    profile["tiku"].update(
        {
            "provider": tiku.get("provider", profile["tiku"]["provider"]),
            "providers": [item.strip() for item in tiku.get("providers", "").split(",") if item.strip()],
            "decision_provider": tiku.get("decision_provider", profile["tiku"]["decision_provider"]),
            "check_llm_connection": tiku.get("check_llm_connection", "true").lower() == "true",
            "submit": tiku.get("submit", "false").lower() == "true",
            "cover_rate": float(tiku.get("cover_rate", 0.9)),
            "delay": float(tiku.get("delay", 1.0)),
            "tokens": tiku.get("tokens", ""),
            "likeapi_search": tiku.get("likeapi_search", "false").lower() == "true",
            "likeapi_vision": tiku.get("likeapi_vision", "true").lower() == "true",
            "likeapi_model": tiku.get("likeapi_model", profile["tiku"]["likeapi_model"]),
            "likeapi_retry": tiku.get("likeapi_retry", "true").lower() == "true",
            "likeapi_retry_times": int(tiku.get("likeapi_retry_times", 3)),
            "url": tiku.get("url", ""),
            "endpoint": tiku.get("endpoint", ""),
            "key": tiku.get("key", ""),
            "model": tiku.get("model", ""),
            "min_interval_seconds": int(tiku.get("min_interval_seconds", 3)),
            "http_proxy": tiku.get("http_proxy", ""),
            "siliconflow_key": tiku.get("siliconflow_key", ""),
            "siliconflow_model": tiku.get("siliconflow_model", profile["tiku"]["siliconflow_model"]),
            "siliconflow_endpoint": tiku.get("siliconflow_endpoint", profile["tiku"]["siliconflow_endpoint"]),
            "true_list": [item.strip() for item in tiku.get("true_list", "").split(",") if item.strip()] or profile["tiku"]["true_list"],
            "false_list": [item.strip() for item in tiku.get("false_list", "").split(",") if item.strip()] or profile["tiku"]["false_list"],
        }
    )

    profile["notification"].update(
        {
            "provider": notification.get("provider", ""),
            "url": notification.get("url", ""),
            "tg_chat_id": notification.get("tg_chat_id", ""),
        }
    )
    return profile


def import_legacy_ini_profile(path: Path, force: bool = False) -> dict:
    profile = load_ini_profile(path)
    create_json_profile(profile["name"], force=force)
    save_json_profile(profile)
    return profile


def bootstrap_json_profiles_from_legacy(force: bool = False) -> list[dict]:
    ensure_desktop_state()
    imported_profiles = []
    if not LEGACY_PROFILE_DIR.exists():
        return imported_profiles

    for ini_path in sorted(LEGACY_PROFILE_DIR.glob("*.ini")):
        json_path = profile_json_path(ini_path.stem)
        if json_path.exists() and not force:
            continue
        imported_profiles.append(import_legacy_ini_profile(ini_path, force=True))
    return imported_profiles


def profile_summary(profile: dict, global_settings: dict | None = None) -> dict:
    runtime_sections = build_runtime_sections(profile, global_settings)
    return {
        "name": profile["name"],
        "provider": profile["tiku"].get("provider", ""),
        "providers": profile["tiku"].get("providers", []),
        "decision_provider": profile["tiku"].get("decision_provider", ""),
        "username": profile["common"].get("username", ""),
        "use_cookies": profile["common"].get("use_cookies", False),
        "course_count": len(profile["common"].get("course_list", [])),
        "runtime_common": runtime_sections["common"],
    }
