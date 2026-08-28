from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings

URL_PATTERN = re.compile(r"https?://[^\s<>\"'\])}，。；;]+", re.IGNORECASE)
BLANK_PATTERN = re.compile(r"([\(（\[])[ \t\xa0]{2,}([\)）\]])|(?:_{3,}|＿{2,}|﹍{2,})")
IMAGE_DATA_PATTERN = re.compile(
    r"^data:(image/[a-z0-9.+-]+);base64,([a-z0-9+/=\r\n]+)$", re.IGNORECASE
)


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


class ImagePreparationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Attachment:
    label: str
    source_url: str = ""
    data_url: str = ""


@dataclass(frozen=True)
class NormalizedQuestion:
    question_type: str
    title: str
    options: tuple[str, ...]
    attachments: tuple[Attachment, ...]


def _option_label(index: int) -> str:
    if index < 26:
        return chr(65 + index)
    return str(index + 1)


def _split_options(value: Any) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return [line.strip() for line in text.split("\n") if line.strip()]


def _normalize_blanks(value: str) -> str:
    existing = [int(item) for item in re.findall(r"\[BLANK_(\d+)\]", value, re.IGNORECASE)]
    counter = max(existing, default=0)

    def replace(match: re.Match[str]) -> str:
        nonlocal counter
        counter += 1
        marker = f"[BLANK_{counter}]"
        if match.group(1):
            return f"{match.group(1)}{marker}{match.group(2)}"
        return marker

    return BLANK_PATTERN.sub(replace, value)


def _incoming_attachments(value: Any) -> list[Attachment]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[Attachment] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            result.append(Attachment(label=f"未分类图片 {index + 1}", data_url=item if item.startswith("data:") else "", source_url=item if item.startswith("http") else ""))
            continue
        if not isinstance(item, Mapping):
            continue
        result.append(
            Attachment(
                label=str(item.get("label") or f"未分类图片 {index + 1}"),
                source_url=str(item.get("source_url") or item.get("url") or ""),
                data_url=str(item.get("data_url") or item.get("data") or ""),
            )
        )
    return result


def normalize_question(payload: Mapping[str, Any]) -> NormalizedQuestion:
    raw_title = str(payload.get("title") or "").strip()
    raw_options = _split_options(payload.get("option_items") or payload.get("options"))
    supplied = _incoming_attachments(payload.get("images") or payload.get("attachments"))

    discovered: list[Attachment] = []
    title_urls = URL_PATTERN.findall(raw_title)
    for index, url in enumerate(title_urls, 1):
        discovered.append(Attachment(label=f"题干/材料图片 {index}", source_url=url))
    for option_index, option in enumerate(raw_options):
        letter = _option_label(option_index)
        for image_index, url in enumerate(URL_PATTERN.findall(option), 1):
            discovered.append(Attachment(label=f"选项 {letter} 图片 {image_index}", source_url=url))

    supplied_by_url: dict[str, list[Attachment]] = {}
    for item in supplied:
        if item.source_url:
            supplied_by_url.setdefault(item.source_url, []).append(item)

    attachments: list[Attachment] = []
    used_supplied: set[int] = set()
    for discovered_item in discovered:
        matching = supplied_by_url.get(discovered_item.source_url, [])
        supplied_item = next((item for item in matching if id(item) not in used_supplied), None)
        if supplied_item is not None:
            used_supplied.add(id(supplied_item))
            attachments.append(
                Attachment(
                    label=discovered_item.label,
                    source_url=discovered_item.source_url,
                    data_url=supplied_item.data_url,
                )
            )
        else:
            attachments.append(discovered_item)
    attachments.extend(item for item in supplied if id(item) not in used_supplied)

    def mark_urls(text: str, scope: str) -> str:
        index = 0

        def replace(_: re.Match[str]) -> str:
            nonlocal index
            index += 1
            return f"[{scope}图片 {index}]"

        return URL_PATTERN.sub(replace, text)

    title = _normalize_blanks(mark_urls(raw_title, "题干/材料"))
    options = tuple(
        _normalize_blanks(mark_urls(option, f"选项 {_option_label(index)} "))
        for index, option in enumerate(raw_options)
    )
    return NormalizedQuestion(
        question_type=str(payload.get("type") or payload.get("question_type") or "unknown").strip().casefold(),
        title=title,
        options=options,
        attachments=tuple(attachments),
    )


class OCSResponseEngine:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client

    def _validate_data_url(self, value: str) -> str:
        matched = IMAGE_DATA_PATTERN.match(value.strip())
        if not matched:
            raise ImagePreparationError("浏览器提交的图片不是有效的 image data URL")
        try:
            size = len(base64.b64decode(matched.group(2), validate=True))
        except (ValueError, binascii.Error) as error:
            raise ImagePreparationError("浏览器提交的图片 Base64 无效") from error
        if size > self.settings.max_image_bytes:
            raise ImagePreparationError(f"图片超过服务端大小限制（{size} bytes）")
        return value.strip()

    def _download_image(self, url: str) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://p.ananas.chaoxing.com/",
        }
        client = self._client or httpx.Client(follow_redirects=True, timeout=self.settings.image_timeout_seconds)
        close_client = self._client is None
        try:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
            if not content_type.startswith("image/"):
                content_type = mimetypes.guess_type(url)[0] or ""
            if not content_type.startswith("image/"):
                content_type = _sniff_image_mime(response.content)
            if not content_type.startswith("image/"):
                raise ImagePreparationError(f"远端内容不是图片：{content_type or 'unknown'}")
            if len(response.content) > self.settings.max_image_bytes:
                raise ImagePreparationError(f"图片超过服务端大小限制（{len(response.content)} bytes）")
            return f"data:{content_type};base64,{base64.b64encode(response.content).decode('ascii')}"
        except httpx.HTTPError as error:
            raise ImagePreparationError(f"服务端无法读取图片 {url}: {error}") from error
        finally:
            if close_client:
                client.close()

    def _prepare_attachments(self, question: NormalizedQuestion) -> tuple[list[tuple[str, str]], list[str]]:
        prepared: list[tuple[str, str]] = []
        warnings: list[str] = []
        for attachment in question.attachments[: self.settings.max_images]:
            try:
                image = self._validate_data_url(attachment.data_url) if attachment.data_url else self._download_image(attachment.source_url)
                prepared.append((attachment.label, image))
            except ImagePreparationError as error:
                warnings.append(f"{attachment.label}: {error}")
        if question.attachments and not prepared and self.settings.require_declared_images:
            raise ImagePreparationError(
                "题目包含图片，但服务端没有拿到任何可读图片。请安装 OCS 异步取图补丁，让浏览器提交 data URL。"
            )
        return prepared, warnings

    def build_request(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], NormalizedQuestion, list[str]]:
        question = normalize_question(payload)
        prepared, warnings = self._prepare_attachments(question)
        blank_count = len(re.findall(r"\[BLANK_\d+\]", question.title, re.IGNORECASE))
        completion_hint = (
            "填空题 answer 返回按 [BLANK_n] 顺序排列的字符串数组"
            if blank_count
            else "填空/主观题若只有一个作答区，answer 直接返回可填写正文；有多个作答区才返回顺序数组"
        )
        shape_hint = {
            "single": "单选题 answer 返回选项字母，例如 A",
            "multiple": "多选题 answer 返回选项字母数组，例如 [\"A\", \"C\"]",
            "judgement": "判断题 answer 只返回 正确 或 错误",
            "completion": completion_hint,
            "fill": "完形填空 answer 返回各小题选项字母的顺序数组",
            "line": "连线或匹配题 answer 返回按左侧项目顺序排列的右侧值数组",
            "reader": "阅读理解 answer 返回各小题选项字母的顺序数组",
        }.get(question.question_type, "按题目要求返回字符串、数组或保留对应关系的对象")
        instructions = (
            "你是 OCS 的课程题目答题服务。只输出 JSON 对象 "
            "{\"answer\": ..., \"confidence\": 0到1}，不要解释、不要 Markdown。"
            "必须严格依据附件前的对应标签判断图片属于题干、材料还是某个选项。"
            "[BLANK_n] 表示第 n 个挖空，[UNDERLINE]...[/UNDERLINE] 表示原文下划线，答案必须保持顺序和对应关系。"
            f"{shape_hint}。主观题 answer 只放可直接填写的正文。"
        )
        user_payload = {
            "question_type": question.question_type,
            "title": question.title,
            "options": [
                {"label": _option_label(index), "text": option}
                for index, option in enumerate(question.options)
            ],
        }
        content: list[dict[str, Any]] = [
            {"type": "input_text", "text": json.dumps(user_payload, ensure_ascii=False)}
        ]
        for index, (label, image) in enumerate(prepared, 1):
            content.append(
                {
                    "type": "input_text",
                    "text": f"[附件 {index} 对应关系] {label}。紧随其后的图片只属于这个标签。",
                }
            )
            content.append({"type": "input_image", "image_url": image, "detail": "auto"})
        request = {
            "model": self.settings.openai_model,
            "instructions": instructions,
            "input": [{"role": "user", "content": content}],
            "reasoning": {"effort": self.settings.reasoning_effort},
            "text": {"format": {"type": "json_object"}},
            "store": False,
        }
        return request, question, warnings

    @staticmethod
    def _parse_response(body: Mapping[str, Any]) -> tuple[Any, float]:
        text = body.get("output_text")
        if not isinstance(text, str):
            for item in body.get("output", []) if isinstance(body.get("output"), list) else []:
                if not isinstance(item, Mapping):
                    continue
                for child in item.get("content", []) if isinstance(item.get("content"), list) else []:
                    if isinstance(child, Mapping) and child.get("type") in {"output_text", "text"}:
                        text = child.get("text")
                        break
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("Responses 返回中没有文本答案")
        cleaned = text.strip()
        fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.IGNORECASE | re.DOTALL)
        if fenced:
            cleaned = fenced.group(1).strip()
        decoded = json.loads(cleaned)
        if not isinstance(decoded, Mapping) or "answer" not in decoded:
            raise RuntimeError("Responses JSON 缺少 answer")
        confidence = decoded.get("confidence", 0.0)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            confidence = 0.0
        return decoded.get("answer"), max(0.0, min(1.0, float(confidence)))

    def format_ocs_answer(self, answer: Any, question: NormalizedQuestion) -> str:
        if isinstance(answer, Mapping):
            if "options" in answer:
                answer = answer["options"]
            elif "text" in answer:
                answer = answer["text"]
            elif question.question_type == "line" and "pairs" in answer:
                answer = answer["pairs"]
            elif question.question_type == "line":
                answer = list(answer.values())
            else:
                return json.dumps(answer, ensure_ascii=False, separators=(",", ":"))
        if isinstance(answer, Sequence) and not isinstance(answer, (str, bytes, bytearray)):
            values: list[str] = []
            for item in answer:
                if isinstance(item, Mapping):
                    value = item.get("right") or item.get("answer") or item.get("value") or item.get("option")
                    if value is None and len(item) == 1:
                        value = next(iter(item.values()))
                else:
                    value = item
                if str(value or "").strip():
                    values.append(str(value).strip())
            return self.settings.answer_separator.join(values)
        text = str(answer or "").strip()
        if question.question_type == "single":
            matched = re.fullmatch(r"\s*([A-Z])(?:[.、:：)）])?\s*", text, re.IGNORECASE)
            if matched:
                return matched.group(1).upper()
        if question.question_type == "multiple":
            letters = re.findall(r"[A-Z]", text.upper())
            if letters and re.fullmatch(r"[\sA-Z,，、#;；|]+", text.upper()):
                return self.settings.answer_separator.join(dict.fromkeys(letters))
        if question.question_type == "judgement":
            lowered = text.casefold()
            if lowered in {"true", "t", "yes", "1", "对", "是", "正确", "√"}:
                return "正确"
            if lowered in {"false", "f", "no", "0", "错", "否", "错误", "×", "x"}:
                return "错误"
        return text

    def answer(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self.settings.validate_ai()
        request, question, warnings = self.build_request(payload)
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        client = self._client or httpx.Client(timeout=self.settings.request_timeout_seconds)
        close_client = self._client is None
        try:
            response = client.post(self.settings.responses_url, headers=headers, json=request)
            response.raise_for_status()
            raw_answer, confidence = self._parse_response(response.json())
        finally:
            if close_client:
                client.close()
        answer = self.format_ocs_answer(raw_answer, question)
        if not answer:
            raise RuntimeError("AI 返回了空答案")
        return {
            "question": str(payload.get("title") or ""),
            "answer": answer,
            "confidence": confidence,
            "model": self.settings.openai_model,
            "warnings": warnings,
        }
