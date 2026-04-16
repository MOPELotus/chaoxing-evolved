from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock


@dataclass(frozen=True)
class RuntimeContext:
    config_path: Path
    cookies_path: Path
    cache_path: Path
    workspace_dir: Path


_runtime_lock = RLock()
_default_workspace = Path.cwd().resolve()
_runtime_context = RuntimeContext(
    config_path=_default_workspace / "config.ini",
    cookies_path=_default_workspace / "cookies.txt",
    cache_path=_default_workspace / "cache.json",
    workspace_dir=_default_workspace,
)


def _resolve_path(path: str | Path | None, base_dir: Path) -> Path | None:
    if path is None:
        return None

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def _get_default_sidecar_path(config_path: Path, legacy_name: str, suffix: str) -> Path:
    if config_path.name.lower() == "config.ini":
        return config_path.with_name(legacy_name)
    return config_path.with_suffix(suffix)


def build_runtime_context(
    config_path: str | Path | None = None,
    cookies_path: str | Path | None = None,
    cache_path: str | Path | None = None,
    workspace_dir: str | Path | None = None,
) -> RuntimeContext:
    cwd = Path.cwd().resolve()
    resolved_config_path = _resolve_path(config_path, cwd) if config_path else None

    if workspace_dir is not None:
        workspace = _resolve_path(workspace_dir, cwd)
    elif resolved_config_path is not None:
        workspace = resolved_config_path.parent
    else:
        workspace = cwd

    runtime_config_path = resolved_config_path or workspace / "config.ini"
    runtime_cookies_path = _resolve_path(cookies_path, workspace)
    runtime_cache_path = _resolve_path(cache_path, workspace)

    if runtime_cookies_path is None:
        runtime_cookies_path = _get_default_sidecar_path(runtime_config_path, "cookies.txt", ".cookies.txt")
    if runtime_cache_path is None:
        runtime_cache_path = _get_default_sidecar_path(runtime_config_path, "cache.json", ".cache.json")

    return RuntimeContext(
        config_path=runtime_config_path,
        cookies_path=runtime_cookies_path,
        cache_path=runtime_cache_path,
        workspace_dir=workspace,
    )


def set_runtime_context(context: RuntimeContext) -> RuntimeContext:
    global _runtime_context
    with _runtime_lock:
        _runtime_context = context
        return _runtime_context


def configure_runtime(
    config_path: str | Path | None = None,
    cookies_path: str | Path | None = None,
    cache_path: str | Path | None = None,
    workspace_dir: str | Path | None = None,
) -> RuntimeContext:
    context = build_runtime_context(
        config_path=config_path,
        cookies_path=cookies_path,
        cache_path=cache_path,
        workspace_dir=workspace_dir,
    )
    return set_runtime_context(context)


def get_runtime_context() -> RuntimeContext:
    with _runtime_lock:
        return _runtime_context
