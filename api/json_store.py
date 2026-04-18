from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DESKTOP_STATE_DIR = PROJECT_ROOT / "desktop_state"
JSON_PROFILE_DIR = DESKTOP_STATE_DIR / "profiles"
GLOBAL_SETTINGS_PATH = DESKTOP_STATE_DIR / "global_settings.json"

CURRENT_SCHEMA_VERSION = 1

OVERRIDABLE_PROFILE_FIELDS = {
    "tiku": [
        "tokens",
        "endpoint",
        "key",
        "model",
        "http_proxy",
        "min_interval_seconds",
        "request_timeout_seconds",
        "siliconflow_key",
        "siliconflow_model",
        "siliconflow_endpoint",
        "url",
        "likeapi_search",
        "likeapi_vision",
        "likeapi_model",
        "likeapi_retry",
        "likeapi_retry_times",
    ],
    "notification": [
        "provider",
        "url",
        "tg_chat_id",
        "onebot_host",
        "onebot_port",
        "onebot_path",
        "onebot_access_token",
        "onebot_target_type",
        "onebot_user_id",
        "onebot_group_id",
        "notify_on_start",
        "notify_on_success",
        "notify_on_failure",
        "notify_on_stop",
        "attach_log_file",
        "include_log_excerpt",
    ],
}

DEFAULT_GLOBAL_SETTINGS = {
    "schema_version": CURRENT_SCHEMA_VERSION,
    "theme": {
        "accent": "snow",
    },
    "desktop": {
        "system_notifications": True,
        "in_app_notifications": True,
        "notify_on_completed": True,
        "notify_on_failed": True,
        "notify_on_stopped": True,
    },
    "defaults": {
        "tiku": {
            "tokens": "",
            "endpoint": "",
            "key": "",
            "model": "",
            "http_proxy": "",
            "min_interval_seconds": "3",
            "request_timeout_seconds": "600",
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
            "onebot_host": "127.0.0.1",
            "onebot_port": 3001,
            "onebot_path": "/",
            "onebot_access_token": "",
            "onebot_target_type": "private",
            "onebot_user_id": "",
            "onebot_group_id": "",
            "notify_on_start": False,
            "notify_on_success": True,
            "notify_on_failure": True,
            "notify_on_stop": True,
            "attach_log_file": True,
            "include_log_excerpt": True,
        },
    },
}

DEFAULT_PROFILE = {
    "schema_version": CURRENT_SCHEMA_VERSION,
    "name": "",
    "overrides": {
        "tiku": {},
        "notification": {},
    },
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
        "tokens": None,
        "likeapi_search": None,
        "likeapi_vision": None,
        "likeapi_model": None,
        "likeapi_retry": None,
        "likeapi_retry_times": None,
        "url": None,
        "endpoint": None,
        "key": None,
        "model": None,
        "min_interval_seconds": None,
        "request_timeout_seconds": None,
        "http_proxy": None,
        "siliconflow_key": None,
        "siliconflow_model": None,
        "siliconflow_endpoint": None,
        "true_list": ["正确", "对", "√", "是"],
        "false_list": ["错误", "错", "×", "否", "不对", "不正确"],
    },
    "notification": {
        "provider": None,
        "url": None,
        "tg_chat_id": None,
        "onebot_host": None,
        "onebot_port": None,
        "onebot_path": None,
        "onebot_access_token": None,
        "onebot_target_type": None,
        "onebot_user_id": None,
        "onebot_group_id": None,
        "notify_on_start": None,
        "notify_on_success": None,
        "notify_on_failure": None,
        "notify_on_stop": None,
        "attach_log_file": None,
        "include_log_excerpt": None,
    },
}


def ensure_desktop_state() -> None:
    DESKTOP_STATE_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PROFILE_DIR.mkdir(parents=True, exist_ok=True)


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


def profile_sidecar_paths(name: str) -> list[Path]:
    profile_path = profile_json_path(name)
    return [
        profile_path.with_suffix(".cookies.txt"),
        profile_path.with_suffix(".cache.json"),
    ]


def _is_profile_config_path(path: Path) -> bool:
    return path.is_file() and path.suffix == ".json" and not path.name.endswith(".cache.json")


def list_json_profiles() -> list[Path]:
    ensure_desktop_state()
    return sorted(path for path in JSON_PROFILE_DIR.glob("*.json") if _is_profile_config_path(path))


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


def _is_blank_override_value(value: object) -> bool:
    return value in ("", None, [])


def profile_override_enabled(profile: dict, section: str, key: str) -> bool:
    overrides = profile.get("overrides", {}).get(section, {})
    if key in overrides:
        return bool(overrides.get(key))
    return not _is_blank_override_value(profile.get(section, {}).get(key))


def _prune_profile_payload(profile: dict) -> dict:
    payload = deepcopy(profile)
    overrides = payload.setdefault("overrides", {})

    for section, keys in OVERRIDABLE_PROFILE_FIELDS.items():
        section_payload = payload.setdefault(section, {})
        section_overrides = overrides.setdefault(section, {})
        cleaned_overrides = {key: True for key, enabled in section_overrides.items() if key in keys and bool(enabled)}

        for key in keys:
            if key not in cleaned_overrides:
                section_payload.pop(key, None)

        if cleaned_overrides:
            overrides[section] = cleaned_overrides
        else:
            overrides.pop(section, None)

    if not overrides:
        payload.pop("overrides", None)

    return payload


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
    return save_json_file(profile_json_path(profile_name), _prune_profile_payload(payload))


def delete_json_profile(name: str, remove_runtime_state: bool = True) -> list[Path]:
    ensure_desktop_state()
    profile_name = sanitize_profile_name(name)
    deleted_paths: list[Path] = []

    targets = [profile_json_path(profile_name)]
    if remove_runtime_state:
        targets.extend(profile_sidecar_paths(profile_name))

    for path in targets:
        if path.exists():
            path.unlink()
            deleted_paths.append(path)
    return deleted_paths


def _bool_to_ini(value: object) -> str:
    return "true" if bool(value) else "false"


def _merge_blank_values(section: dict, defaults: dict | None = None) -> dict:
    merged = deepcopy(section)
    defaults = defaults or {}

    for key, value in defaults.items():
        if merged.get(key) in ("", None, []):
            merged[key] = deepcopy(value)
    return merged


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


def build_effective_profile(profile: dict, global_settings: dict | None = None) -> dict:
    settings = global_settings or load_global_settings()
    defaults = settings.get("defaults", {})
    payload = _deep_merge(DEFAULT_PROFILE, profile)
    payload["name"] = sanitize_profile_name(payload.get("name") or profile.get("name", ""))
    payload["common"]["course_list"] = list(payload.get("common", {}).get("course_list", []) or [])

    for section, keys in OVERRIDABLE_PROFILE_FIELDS.items():
        section_payload = payload.setdefault(section, {})
        section_defaults = defaults.get(section, {})
        for key in keys:
            if not profile_override_enabled(payload, section, key):
                section_payload[key] = deepcopy(section_defaults.get(key))

    return payload


def build_config_sections(profile: dict, global_settings: dict | None = None) -> dict[str, dict[str, str]]:
    effective_profile = build_effective_profile(profile, global_settings)
    settings = global_settings or load_global_settings()
    defaults = settings.get("defaults", {})

    common_section = _serialize_profile_section(effective_profile.get("common", {}))
    common_section["course_list"] = ",".join(effective_profile.get("common", {}).get("course_list", []))

    tiku_section = _serialize_profile_section(
        effective_profile.get("tiku", {}),
        defaults.get("tiku", {}),
    )
    notification_section = _serialize_profile_section(
        effective_profile.get("notification", {}),
        defaults.get("notification", {}),
    )
    return {
        "common": common_section,
        "tiku": tiku_section,
        "notification": notification_section,
    }


def profile_summary(profile: dict, global_settings: dict | None = None) -> dict:
    effective_profile = build_effective_profile(profile, global_settings)
    return {
        "name": effective_profile["name"],
        "provider": effective_profile["tiku"].get("provider", ""),
        "providers": effective_profile["tiku"].get("providers", []),
        "decision_provider": effective_profile["tiku"].get("decision_provider", ""),
        "username": effective_profile["common"].get("username", ""),
        "use_cookies": effective_profile["common"].get("use_cookies", False),
        "course_count": len(effective_profile["common"].get("course_list", [])),
    }
