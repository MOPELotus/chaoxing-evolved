"""Client for the Lotus question bank's existing OCS /search endpoint."""
from __future__ import annotations

import html
import re
import threading
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from api.logger import logger
from api.response_ai import ResponseSite, _as_bool, question_cache_key

DEFAULT_LOTUS_URL = "https://tiku.lotusshared.cn"
ANSWER_BACKENDS = {"responses": "Responses AI", "lotus": "荷花题库"}
_IMAGE = re.compile(r"\[QUESTION_IMAGE:([^\]]+)\]", re.IGNORECASE)
_BLANK = re.compile(r"\[BLANK_\d+\]", re.IGNORECASE)


class LotusQuestionError(ValueError):
    """Unsupported or ambiguous desktop input; never guess its structure."""


class LotusAnswerService:
    def __init__(self, config: Mapping[str, Any], cache=None) -> None:
        self.config = dict(config)
        self.cache = cache
        self.semantic_cache_enabled = _as_bool(config.get("semantic_cache_enabled"), False)
        self.base_url = str(config.get("lotus_url") or DEFAULT_LOTUS_URL).strip().rstrip("/")
        self.token = str(config.get("lotus_token") or "").strip()
        self.timeout = max(10.0, float(config.get("request_timeout_seconds") or 180))
        self.min_interval = max(0.0, float(config.get("min_interval_seconds") or 0))
        self._lock = threading.Lock()
        self._last_request_time = None
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("荷花题库地址必须是不含凭据、查询参数的 HTTP(S) 地址")
        path = parsed.path.rstrip("/")
        if not path.endswith("/search"):
            path += "/search"
        self.url = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        # The server manages model selection.  Keep Lotus cache separate from
        # direct Responses answers and from other configured Lotus endpoints.
        self.site = ResponseSite("lotus", self.url, "", protocol="lotus")

    @staticmethod
    def _render(value: Any) -> str:
        if not isinstance(value, str):
            raise LotusQuestionError("荷花题库需要有明确边界的文字或图片选项")
        def image(match):
            url = match.group(1).strip()
            if not url or url.casefold() == "embedded":
                raise LotusQuestionError("图片缺少可读取的地址")
            return f'<img src="{html.escape(url, quote=True)}">'
        return _BLANK.sub("____", _IMAGE.sub(image, value))

    @classmethod
    def build_request(cls, question: Mapping[str, Any]) -> dict:
        qtype = str(question.get("type") or "")
        if qtype in {"shortanswer", "calculation"}:
            wire_type = "completion"
        elif qtype in {"single", "multiple", "judgement", "completion"}:
            wire_type = qtype
        else:
            raise LotusQuestionError(f"荷花题库暂不支持题型：{qtype or 'unknown'}")
        title = cls._render(question.get("title") or question.get("title_text") or "")
        material = question.get("material") or ""
        if material:
            title = f"【材料】\n{cls._render(material)}\n【题目】\n{title}"
        raw_options = question.get("option_items") or question.get("options") or []
        if not isinstance(raw_options, (list, tuple)):
            raise LotusQuestionError("选项必须为数组，不能从多行文字猜测选项边界")
        options = [cls._render(value) for value in raw_options]
        # image_urls also includes images already located in options; only
        # unlocated supplementary/material images are added to the stem.
        located = title + "\n" + "\n".join(options)
        image_urls = []
        for field in ("material_image_urls", "image_urls"):
            values = question.get(field) or []
            if not isinstance(values, (list, tuple)) or not all(isinstance(url, str) for url in values):
                raise LotusQuestionError("图片地址必须为字符串数组")
            image_urls.extend(values)
        for url in dict.fromkeys(image_urls):
            if url not in html.unescape(located):
                attachment = cls._render(f"[QUESTION_IMAGE:{url}]")
                title += "\n补充题干图片：" + attachment
                located += attachment
        request = {"type": wire_type, "title": title, "options": options}
        if wire_type == "completion":
            count = 1 if qtype != "completion" else question.get("blank_count", question.get("blankCount"))
            if count is None:
                count = len(question.get("answer_fields") or []) or None
            if count is not None:
                if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                    raise LotusQuestionError("填空数量必须为正整数")
                request["blankCount"] = count
        return request

    @staticmethod
    def parse_response(payload: Any, request: Mapping[str, Any]) -> Any:
        if not isinstance(payload, dict) or payload.get("code") != 1:
            code = str(payload.get("error") or "LOTUS_ERROR") if isinstance(payload, dict) else "LOTUS_FORMAT"
            # Never reflect upstream error text, prompts, or credentials.
            raise RuntimeError(code)
        answers = payload.get("answers")
        if not isinstance(answers, list) or not answers or not all(isinstance(x, str) and x.strip() for x in answers):
            raise RuntimeError("LOTUS_INVALID_ANSWER")
        answers = [x.strip() for x in answers]
        qtype = request["type"]
        if qtype in {"single", "multiple"}:
            if qtype == "single" and len(answers) != 1:
                raise RuntimeError("LOTUS_INVALID_ANSWER")
            if any(not re.fullmatch("[A-Z]", x) or ord(x)-65 >= len(request["options"]) for x in answers):
                raise RuntimeError("LOTUS_INVALID_ANSWER")
            return "".join(dict.fromkeys(answers))
        if qtype == "judgement":
            if len(answers) != 1 or answers[0] not in {"正确", "错误"}:
                raise RuntimeError("LOTUS_INVALID_ANSWER")
            return answers[0]
        if request.get("blankCount") and len(answers) != request["blankCount"]:
            raise RuntimeError("LOTUS_INVALID_ANSWER")
        return answers  # Native desktop blank editors consume a list, not OCS JSON text.

    def answer(self, question: Mapping[str, Any], force_refresh: bool = False) -> Any | None:
        if not self.token or "\n" in self.token or "\r" in self.token:
            raise RuntimeError("请在全局设置填写荷花题库访问令牌")
        request = self.build_request(question)
        cache_key = "lotus:" + question_cache_key({**request, "native_type": question.get("type")}, self.site, "server-managed", "server-managed")
        if self.cache is not None and self.semantic_cache_enabled and not force_refresh:
            cached = self.cache.get_cache(cache_key)
            if cached:
                return cached
        # The adapter already manages execution, timeouts, and failures.  Do
        # not duplicate a possibly running CLI job after a client timeout.
        try:
            with self._lock:
                if self._last_request_time is not None:
                    delay = self._last_request_time + self.min_interval - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                self._last_request_time = time.monotonic()
            with httpx.Client(timeout=self.timeout, proxy=self.config.get("http_proxy") or None, trust_env=False) as client:
                response = client.post(self.url, headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}, json=request)
                response.raise_for_status()
                answer = self.parse_response(response.json(), request)
                if question.get("type") in {"shortanswer", "calculation"}:
                    answer = answer[0]
        except httpx.HTTPStatusError as error:
            logger.error("荷花题库请求失败：HTTP {}", error.response.status_code)
            return None
        except (httpx.HTTPError, ValueError, RuntimeError) as error:
            logger.error("荷花题库请求失败：{}", type(error).__name__)
            return None
        if self.cache is not None and self.semantic_cache_enabled:
            self.cache.add_cache(cache_key, answer)
        return answer

    def check_connection(self) -> bool:
        try:
            return self.answer({"type": "single", "title": "1+1等于多少？", "options": ["1", "2"]}, force_refresh=True) == "B"
        except (ValueError, RuntimeError):
            logger.error("荷花题库连接检查失败，请检查地址及访问令牌")
            return False
