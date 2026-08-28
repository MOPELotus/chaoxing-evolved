from __future__ import annotations

import os
from dataclasses import dataclass

_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on", "enabled"}


@dataclass(frozen=True)
class Settings:
    openai_base_url: str
    openai_api_key: str
    openai_model: str
    reasoning_effort: str
    service_access_token: str
    request_timeout_seconds: float
    image_timeout_seconds: float
    max_images: int
    max_image_bytes: int
    answer_separator: str
    require_declared_images: bool

    @classmethod
    def from_env(cls) -> Settings:
        effort = os.getenv("OPENAI_REASONING_EFFORT", "medium").strip().casefold()
        if effort not in _EFFORTS:
            effort = "medium"
        return cls(
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/"),
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_model=os.getenv("OPENAI_MODEL", "").strip(),
            reasoning_effort=effort,
            service_access_token=os.getenv("SERVICE_ACCESS_TOKEN", "").strip(),
            request_timeout_seconds=max(10.0, float(os.getenv("REQUEST_TIMEOUT_SECONDS", "180"))),
            image_timeout_seconds=max(3.0, float(os.getenv("IMAGE_TIMEOUT_SECONDS", "20"))),
            max_images=max(1, int(os.getenv("MAX_IMAGES", "24"))),
            max_image_bytes=max(1024, int(os.getenv("MAX_IMAGE_BYTES", str(12 * 1024 * 1024)))),
            answer_separator=os.getenv("ANSWER_SEPARATOR", "#") or "#",
            require_declared_images=_env_bool("REQUIRE_DECLARED_IMAGES", True),
        )

    @property
    def responses_url(self) -> str:
        if self.openai_base_url.endswith("/v1/responses"):
            return self.openai_base_url
        if self.openai_base_url.endswith("/v1"):
            return self.openai_base_url + "/responses"
        return self.openai_base_url + "/v1/responses"

    def validate_ai(self) -> None:
        missing = []
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if not self.openai_model:
            missing.append("OPENAI_MODEL")
        if missing:
            raise RuntimeError("服务端缺少配置：" + ", ".join(missing))
