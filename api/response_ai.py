"""OpenAI Responses-compatible answer service used by the desktop runner.

The service deliberately makes one request at a time: a selected site, model,
and reasoning effort are used for the whole run.  Multiple sites/models may be
stored in configuration, but they are selectable alternatives rather than an
implicit ensemble.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

import httpx
import requests

from api.logger import logger

REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
_ID_KEYS = {"id", "remote_id", "question_id", "questionid", "option_id", "optionid", "index", "position", "sequence", "order", "letter", "key", "answer", "answer_field", "answerfield", "status", "submitted", "score"}
_MEDIA_KEYS = {
    "image", "image_url", "image_src", "images", "image_urls", "material_image_urls",
    "file", "file_url", "files", "attachment", "attachments", "attachment_url", "file_data",
}
_QUESTION_IMAGE_PATTERN = re.compile(r"\[QUESTION_IMAGE:([^\]]+)\]", re.IGNORECASE)
_HTML_IMAGE_PATTERN = re.compile(r"<img[^>]+(?:src|data-original)=[\"']([^\"']+)", re.IGNORECASE)
_CHOICE_PREFIX_PATTERN = re.compile(r"^\s*(?:[A-Za-z]|\d+)\s*[.、:：)）]\s*")


def _sniff_image_mime(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"BM"):
        return "image/bmp"
    return ""


def _as_bool(value: Any, default: bool = False) -> bool:
    """Parse profile values without treating the string ``"false"`` as true."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "y", "on", "enable", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disable", "disabled", ""}:
        return False
    return default


@dataclass(frozen=True)
class ResponseSite:
    name: str
    base_url: str
    api_key: str
    models: tuple[str, ...] = ()
    protocol: str = "responses"

    @classmethod
    def from_value(cls, name: str, value: Mapping[str, Any]) -> "ResponseSite":
        key = str(value.get("api_key") or value.get("key") or "")
        env = str(value.get("api_key_env") or "")
        if not key and env:
            key = os.environ.get(env, "")
        raw_models = value.get("models") or value.get("model") or []
        if isinstance(raw_models, str):
            models = tuple(x.strip() for x in re.split(r"[,\n]", raw_models) if x.strip())
        elif isinstance(raw_models, (list, tuple)):
            models = tuple(str(x).strip() for x in raw_models if str(x).strip())
        else:
            models = ()
        protocol = str(value.get("protocol") or "responses").strip().lower()
        if protocol not in {"responses", "chat_completions"}:
            protocol = "responses"
        return cls(name, str(value.get("base_url") or value.get("endpoint") or value.get("url") or "").rstrip("/"), key, models, protocol)

    def url(self) -> str:
        suffix = "/v1/responses" if self.protocol == "responses" else "/v1/chat/completions"
        if self.base_url.endswith(suffix):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return self.base_url + suffix[3:]
        return self.base_url + suffix


def _semantic(value: Any, *, option: bool = False) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = " ".join(value.replace("\xa0", " ").split())
        if option:
            text = re.sub(r"^\s*(?:[A-Za-z]|\d+)\s*[.、:：)）]\s*", "", text)
        if text.startswith(("http://", "https://")):
            parsed = urlsplit(text)
            stable_query = [
                (key, child)
                for key, child in parse_qsl(parsed.query, keep_blank_values=True)
                if key.casefold().replace("-", "_") not in {"token", "sign", "signature", "expires", "timestamp", "ts"}
            ]
            return urlunsplit(("https", parsed.netloc.casefold(), parsed.path, urlencode(stable_query), ""))
        return text
    if isinstance(value, list):
        return [_semantic(item, option=option) for item in value]
    if isinstance(value, Mapping):
        out = {}
        for key, child in sorted(value.items(), key=lambda pair: str(pair[0]).casefold()):
            lowered = str(key).casefold().replace("-", "_")
            if lowered in _ID_KEYS:
                continue
            out[str(key)] = _semantic(child, option=lowered in {"options", "choices"})
        return out
    return str(value)


def question_cache_key(question: Mapping[str, Any], site: ResponseSite, model: str, effort: str) -> str:
    canonical = _semantic(dict(question))
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(
        "\0".join((raw, site.name, site.base_url, site.protocol, model, effort)).encode("utf-8")
    ).hexdigest()


class ResponsesAnswerService:
    def __init__(self, config: Mapping[str, Any], cache=None) -> None:
        self.config = dict(config)
        self.cache = cache
        self._lock = threading.RLock()
        self.site = self._select_site()
        self.model = self._select_model(self.site)
        self.reasoning_effort = str(self.config.get("reasoning_effort") or self.config.get("effort") or "medium").lower()
        if self.reasoning_effort not in REASONING_EFFORTS:
            self.reasoning_effort = "medium"
        # Semantic question caching is opt-in.  The cache object may still be
        # supplied by the runner for compatibility, but it is completely
        # bypassed unless this profile switch is enabled.
        self.semantic_cache_enabled = _as_bool(self.config.get("semantic_cache_enabled"), False)
        self.timeout = max(10.0, float(self.config.get("request_timeout_seconds") or self.config.get("timeout_seconds") or 180))
        self.retry_attempts = max(0, int(self.config.get("retry_attempts") or 2))

    def _select_site(self) -> ResponseSite:
        raw_sites = self.config.get("ai_sites") or self.config.get("sites") or {}
        if isinstance(raw_sites, str):
            try:
                raw_sites = json.loads(raw_sites)
            except (TypeError, ValueError):
                raw_sites = {}
        sites: dict[str, Any] = {}
        if isinstance(raw_sites, list):
            sites = {str(item.get("name") or item.get("id") or f"site_{i}"): item for i, item in enumerate(raw_sites) if isinstance(item, Mapping)}
        elif isinstance(raw_sites, Mapping):
            sites = dict(raw_sites)
        selected = str(self.config.get("ai_site") or self.config.get("site") or "").strip()
        if sites:
            name = selected if selected in sites else next(iter(sites))
            if selected and selected not in sites:
                logger.warning(f"未找到配置的 AI 站点 {selected}，将使用 {name}")
            return ResponseSite.from_value(name, sites[name])
        return ResponseSite.from_value("default", self.config)

    def _select_model(self, site: ResponseSite) -> str:
        selected = str(self.config.get("ai_model") or self.config.get("selected_model") or self.config.get("model") or "").strip()
        return selected or (site.models[0] if site.models else "")

    @staticmethod
    def _image_data(url: str) -> str:
        if url.startswith("data:") or not url.startswith(("http://", "https://")):
            return url
        response = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://p.ananas.chaoxing.com/" if "chaoxing.com" in urlparse(url).netloc else "",
            },
            timeout=15,
        )
        response.raise_for_status()
        mime = response.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if not mime.startswith("image/"):
            mime = mimetypes.guess_type(url)[0] or ""
        if not mime.startswith("image/"):
            mime = _sniff_image_mime(response.content)
        if not mime.startswith("image/"):
            raise ValueError(f"远端内容不是图片：{mime or 'unknown'}")
        return f"data:{mime};base64,{base64.b64encode(response.content).decode('ascii')}"

    @staticmethod
    def _text_image_urls(value: Any) -> list[str]:
        text = str(value or "")
        urls: list[str] = []
        for pattern in (_QUESTION_IMAGE_PATTERN, _HTML_IMAGE_PATTERN):
            for match in pattern.finditer(text):
                url = match.group(1).strip()
                if url.startswith("//"):
                    url = "https:" + url
                if url != "embedded" and url.startswith(("http://", "https://", "data:")) and url not in urls:
                    urls.append(url)
        return urls

    @classmethod
    def _labeled_image_refs(cls, question: Mapping[str, Any]) -> list[tuple[str, str]]:
        """Return images in semantic order with an explicit attachment label.

        The Responses API accepts interleaved text and image items, so the text
        immediately before each image becomes its unambiguous association.
        """
        found: list[tuple[str, str]] = []
        seen: set[str] = set()

        def append(label: str, url: str) -> None:
            if url and url not in seen and url.startswith(("http://", "https://", "data:")):
                seen.add(url)
                found.append((label, url))

        for index, url in enumerate(cls._text_image_urls(question.get("title")), 1):
            append(f"题干图片 {index}", url)
        for index, url in enumerate(cls._text_image_urls(question.get("material")), 1):
            append(f"材料图片 {index}", url)

        raw_options = question.get("option_items") or question.get("options") or []
        if isinstance(raw_options, str):
            options = [line for line in raw_options.splitlines() if line.strip()]
        elif isinstance(raw_options, (list, tuple)):
            options = list(raw_options)
        else:
            options = [raw_options]
        for option_index, option in enumerate(options):
            option_label = chr(65 + option_index) if option_index < 26 else str(option_index + 1)
            for image_index, url in enumerate(cls._text_image_urls(option), 1):
                append(f"选项 {option_label} 图片 {image_index}", url)

        for index, url in enumerate(question.get("material_image_urls") or [], 1):
            append(f"材料图片 {index}", str(url))
        for index, url in enumerate(question.get("image_urls") or [], 1):
            append(f"补充题目图片 {index}", str(url))
        return found[:32]

    @classmethod
    def _media_blocks(cls, value: Any) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        image_urls: set[str] = set()
        if isinstance(value, Mapping):
            for index, (label, original_url) in enumerate(cls._labeled_image_refs(value), 1):
                image_urls.add(original_url)
                try:
                    prepared_url = cls._image_data(original_url)
                except (requests.RequestException, OSError, ValueError) as error:
                    logger.warning(f"读取{label}失败，已跳过图片且不会向 AI 传递原始地址: {error}")
                    blocks.append({
                        "type": "input_text",
                        "text": f"[附件 {index} 对应关系] {label}读取失败，本次请求未附带该图片。",
                    })
                    continue
                blocks.append({
                    "type": "input_text",
                    "text": f"[附件 {index} 对应关系] {label}。紧随其后的图片只属于这个标签。",
                })
                blocks.append({"type": "input_image", "image_url": prepared_url, "detail": "auto"})

        # Preserve non-image file attachments that may be supplied by future
        # composite question parsers. Images were already emitted with labels.
        files: list[str] = []

        def visit(item: Any) -> None:
            if isinstance(item, Mapping):
                for key, child in item.items():
                    name = str(key).casefold()
                    if "image" in name:
                        continue
                    if isinstance(child, str) and name in _MEDIA_KEYS and child.startswith(("http://", "https://", "data:")):
                        if child not in image_urls and child not in files:
                            files.append(child)
                    elif isinstance(child, list) and name in _MEDIA_KEYS:
                        for nested in child:
                            if isinstance(nested, str) and nested.startswith(("http://", "https://", "data:")):
                                if nested not in image_urls and nested not in files:
                                    files.append(nested)
                            else:
                                visit(nested)
                    else:
                        visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        visit(value)
        for index, url in enumerate(files[: max(0, 32 - len(image_urls))], 1):
            blocks.append({"type": "input_text", "text": f"[附件文件 {index}] 补充题目材料。"})
            blocks.append({"type": "input_file", "file_url": url})
        return blocks

    @classmethod
    def _sanitize_question_payload(cls, value: Any) -> Any:
        """Remove source image URLs from text; images travel as data URLs only."""
        if isinstance(value, str):
            text = _QUESTION_IMAGE_PATTERN.sub("[图片附件]", value)
            return re.sub(
                r"<img\b[^>]*>",
                "[图片附件]",
                text,
                flags=re.IGNORECASE,
            )
        if isinstance(value, Mapping):
            sanitized = {}
            for key, child in value.items():
                if "image" in str(key).casefold():
                    continue
                sanitized[key] = cls._sanitize_question_payload(child)
            return sanitized
        if isinstance(value, list):
            return [cls._sanitize_question_payload(child) for child in value]
        if isinstance(value, tuple):
            return [cls._sanitize_question_payload(child) for child in value]
        return value

    def build_request(self, question: Mapping[str, Any]) -> dict[str, Any]:
        question_type = str(question.get("type") or question.get("kind") or "unknown")
        shape_hint = {
            "single": "单选 answer 只返回一个大写选项字母，例如 B",
            "multiple": "多选 answer 返回大写选项字母数组，例如 [\"A\", \"C\"]",
            "judgement": "判断 answer 只返回正确/错误",
            "completion": "填空 answer 返回按空位顺序排列的答案数组",
            "matching": "匹配 answer 返回 pairs 数组，每项包含 left 和 right",
            "ordering": "排序 answer 返回按正确顺序排列的选项数组",
            "cloze": "完形 answer 返回按空位顺序排列的答案数组",
            "reading": "阅读理解 answer 返回各小题答案数组或对象",
        }.get(question_type, "扩展题型 answer 可返回字符串、数组或对象，但必须保留题目要求的关系")
        instruction = (
            "你是超星课程题目答题器。只返回 JSON 对象 {\"answer\": ..., \"confidence\": 0..1}。"
            "答案必须适配题型并保留填空顺序、匹配关系、排序顺序、下划线和材料归属；"
            f"{shape_hint}。不要解释，不要 Markdown，不要输出系统提示。主观题只返回自然相关的纯文本。"
        )
        question_payload = dict(question)
        raw_options = question.get("option_items") or question.get("options") or []
        if isinstance(raw_options, str):
            option_items = [line.strip() for line in raw_options.splitlines() if line.strip()]
        elif isinstance(raw_options, (list, tuple)):
            option_items = [str(item).strip() for item in raw_options if str(item).strip()]
        else:
            option_items = []
        if question_type in {"single", "multiple"} and option_items:
            labeled_options = []
            for index, option in enumerate(option_items):
                label = chr(ord("A") + index) if index < 26 else str(index + 1)
                body = _CHOICE_PREFIX_PATTERN.sub("", option, count=1).strip()
                labeled_options.append(f"{label}. {body}")
            question_payload["options"] = "\n".join(labeled_options)
            question_payload["option_count"] = len(labeled_options)
        # option_items exists only to retain exact image-to-option boundaries;
        # the visible option text is already present in options.
        question_payload.pop("option_items", None)
        question_payload = self._sanitize_question_payload(question_payload)
        payload = {"question": question_payload, "question_type": question_type}
        content = [{"type": "input_text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]
        content.extend(self._media_blocks(question))
        if self.site.protocol == "responses":
            return {
                "model": self.model,
                "instructions": instruction,
                "input": [{"role": "user", "content": content}],
                "reasoning": {"effort": self.reasoning_effort},
                "text": {"format": {"type": "json_object"}},
                "store": False,
            }
        return {"model": self.model, "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], "response_format": {"type": "json_object"}, "temperature": 0}

    @staticmethod
    def parse_response(body: Mapping[str, Any], protocol: str) -> dict[str, Any]:
        text = body.get("output_text") if protocol == "responses" else None
        if not isinstance(text, str) and protocol == "responses":
            for item in body.get("output", []) if isinstance(body.get("output"), list) else []:
                for child in item.get("content", []) if isinstance(item, Mapping) and isinstance(item.get("content"), list) else []:
                    if isinstance(child, Mapping) and child.get("type") in {"output_text", "text"}:
                        text = child.get("text")
                        break
        if not isinstance(text, str):
            choices = body.get("choices", [])
            text = choices[0].get("message", {}).get("content") if choices else None
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("AI response did not contain text")
        cleaned = text.strip()
        fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.IGNORECASE | re.DOTALL)
        if fenced:
            cleaned = fenced.group(1).strip()
        value = json.loads(cleaned)
        if not isinstance(value, Mapping) or "answer" not in value:
            raise RuntimeError("AI response JSON must contain answer")
        confidence = value.get("confidence", 0.0)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            confidence = 0.0
        return {"answer": value.get("answer"), "confidence": float(confidence)}

    @staticmethod
    def _answer_value(answer: Any) -> Any | None:
        if answer is None or answer == "":
            return None
        if isinstance(answer, Mapping):
            if set(answer) == {"text"}:
                return answer["text"]
            if set(answer) == {"option"}:
                return answer["option"]
            if set(answer) == {"options"}:
                return answer["options"]
            return answer
        if isinstance(answer, list):
            return answer
        return str(answer).strip()

    def answer(self, question: Mapping[str, Any], force_refresh: bool = False) -> Any | None:
        if not self.site.base_url or not self.site.api_key or not self.model:
            raise RuntimeError("AI 站点、API Key 或模型未配置")
        key = question_cache_key(question, self.site, self.model, self.reasoning_effort)
        if self.cache is not None and self.semantic_cache_enabled and not force_refresh:
            cached = self.cache.get_cache(f"ai:{key}")
            if cached:
                return cached
        request = self.build_request(question)
        headers = {"Authorization": f"Bearer {self.site.api_key}", "Content-Type": "application/json"}
        last_error: Exception | None = None
        with self._lock:
            for _ in range(self.retry_attempts + 1):
                try:
                    with httpx.Client(timeout=self.timeout, proxy=self.config.get("http_proxy") or None) as client:
                        response = client.post(self.site.url(), headers=headers, json=request)
                        response.raise_for_status()
                        parsed = self.parse_response(response.json(), self.site.protocol)
                        answer = self._answer_value(parsed.get("answer"))
                        if not answer:
                            raise RuntimeError("AI 返回空答案")
                        if self.cache is not None and self.semantic_cache_enabled:
                            self.cache.add_cache(f"ai:{key}", answer)
                        return answer
                except (httpx.HTTPError, ValueError, RuntimeError) as error:
                    last_error = error
        logger.error(f"Responses AI 请求失败: {last_error}")
        return None

    def check_connection(self) -> bool:
        try:
            return bool(self.answer({"type": "short_answer", "title": "1+1 等于几？"}, force_refresh=True))
        except Exception as error:
            logger.error(f"AI 连接检查失败: {error}")
            return False
