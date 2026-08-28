from __future__ import annotations

import secrets
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .engine import ImagePreparationError, OCSResponseEngine


class ImageInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str = ""
    source_url: str = ""
    data_url: str = ""


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str = Field(min_length=1)
    options: str | list[str] = ""
    option_items: list[str] = Field(default_factory=list)
    type: str = "unknown"
    images: list[ImageInput | str] = Field(default_factory=list)


settings = Settings.from_env()
engine = OCSResponseEngine(settings)
app = FastAPI(title="OCS Responses Question Bank", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def _authorize(x_access_token: str | None, authorization: str | None) -> None:
    expected = settings.service_access_token
    if not expected:
        return
    bearer = ""
    if authorization and authorization.casefold().startswith("bearer "):
        bearer = authorization[7:].strip()
    if not (secrets.compare_digest(x_access_token or "", expected) or secrets.compare_digest(bearer, expected)):
        raise HTTPException(status_code=401, detail="题库访问令牌无效")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "model_configured": bool(settings.openai_model),
        "api_key_configured": bool(settings.openai_api_key),
        "auth_enabled": bool(settings.service_access_token),
    }


@app.post("/v1/answer")
def answer(
    request: AnswerRequest,
    x_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(x_access_token, authorization)
    try:
        result = engine.answer(request.model_dump())
        return {"code": 1, "message": "ok", **result}
    except ImagePreparationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Responses 请求失败：{error}") from error
