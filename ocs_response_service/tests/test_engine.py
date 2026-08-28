from __future__ import annotations

import base64
import json
import unittest

import httpx

from ocs_response_service.config import Settings
from ocs_response_service.engine import (
    ImagePreparationError,
    OCSResponseEngine,
    normalize_question,
)

PIXEL = "data:image/png;base64," + base64.b64encode(b"png").decode("ascii")


def settings(**overrides):
    values = {
        "openai_base_url": "https://example.test",
        "openai_api_key": "key",
        "openai_model": "model",
        "reasoning_effort": "medium",
        "service_access_token": "",
        "request_timeout_seconds": 30.0,
        "image_timeout_seconds": 10.0,
        "max_images": 24,
        "max_image_bytes": 1024,
        "answer_separator": "#",
        "require_declared_images": True,
    }
    values.update(overrides)
    return Settings(**values)


class OCSResponseEngineTests(unittest.TestCase):
    def test_normalize_labels_title_options_and_blanks(self):
        question = normalize_question(
            {
                "type": "single",
                "title": "看图 https://cx.test/stem.png 填写____，另一个答案写在（     ）",
                "options": "红色 https://cx.test/a.png\n蓝色 https://cx.test/b.png",
                "images": [
                    {"source_url": "https://cx.test/stem.png", "data_url": PIXEL},
                    {"source_url": "https://cx.test/a.png", "data_url": PIXEL},
                    {"source_url": "https://cx.test/b.png", "data_url": PIXEL},
                ],
            }
        )
        self.assertIn("[题干/材料图片 1]", question.title)
        self.assertIn("[BLANK_1]", question.title)
        self.assertIn("（[BLANK_2]）", question.title)
        self.assertIn("[选项 A 图片 1]", question.options[0])
        self.assertEqual(
            [item.label for item in question.attachments],
            ["题干/材料图片 1", "选项 A 图片 1", "选项 B 图片 1"],
        )

    def test_build_request_interleaves_each_label_and_image(self):
        engine = OCSResponseEngine(settings())
        request, _, warnings = engine.build_request(
            {
                "type": "single",
                "title": "看图 https://cx.test/stem.png",
                "options": "甲\n乙 https://cx.test/b.png",
                "images": [
                    {"source_url": "https://cx.test/stem.png", "data_url": PIXEL},
                    {"source_url": "https://cx.test/b.png", "data_url": PIXEL},
                ],
            }
        )
        content = request["input"][0]["content"]
        self.assertEqual([item["type"] for item in content], ["input_text", "input_text", "input_image", "input_text", "input_image"])
        self.assertIn("题干/材料图片 1", content[1]["text"])
        self.assertIn("选项 B 图片 1", content[3]["text"])
        self.assertEqual(warnings, [])

    def test_structured_option_items_preserve_internal_newlines(self):
        question = normalize_question(
            {
                "title": "选择图片",
                "options": "旧的扁平字符串",
                "option_items": [
                    "选项 A 第一行\n选项 A 第二行 https://cx.test/a.png",
                    "选项 B https://cx.test/b.png",
                ],
                "images": [
                    {"source_url": "https://cx.test/a.png", "data_url": PIXEL},
                    {"source_url": "https://cx.test/b.png", "data_url": PIXEL},
                ],
            }
        )
        self.assertEqual(len(question.options), 2)
        self.assertIn("[选项 A 图片 1]", question.options[0])
        self.assertIn("[选项 B 图片 1]", question.options[1])

    def test_declared_but_unreadable_images_fail_instead_of_guessing(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        engine = OCSResponseEngine(settings(), client=client)
        with self.assertRaises(ImagePreparationError):
            engine.build_request({"title": "https://cx.test/protected.png", "type": "single"})
        client.close()

    def test_server_download_uses_chaoxing_headers_and_sniffs_octet_stream(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return httpx.Response(
                200,
                content=b"\x89PNG\r\n\x1a\nimage",
                headers={"Content-Type": "application/octet-stream"},
                request=request,
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        engine = OCSResponseEngine(settings(), client=client)
        request, _, warnings = engine.build_request(
            {"title": "https://p.ananas.chaoxing.com/objectid/abc", "type": "single"}
        )
        image = next(item for item in request["input"][0]["content"] if item["type"] == "input_image")
        self.assertTrue(image["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual(seen.get("referer"), "https://p.ananas.chaoxing.com/")
        self.assertIn("Chrome/120", seen.get("user-agent", ""))
        self.assertEqual(warnings, [])

    def test_answer_formats_multiple_letters_for_ocs(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = {"output_text": json.dumps({"answer": ["A", "C"], "confidence": 0.9})}
            return httpx.Response(200, json=body, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        engine = OCSResponseEngine(settings(), client=client)
        result = engine.answer({"title": "选择", "options": "甲\n乙\n丙", "type": "multiple"})
        self.assertEqual(result["answer"], "A#C")
        self.assertEqual(result["confidence"], 0.9)
        client.close()

    def test_line_pairs_are_flattened_in_left_side_order(self):
        engine = OCSResponseEngine(settings())
        question = normalize_question({"title": "连线", "type": "line"})
        answer = engine.format_ocs_answer(
            {"pairs": [{"left": "甲", "right": "2"}, {"left": "乙", "right": "1"}]},
            question,
        )
        self.assertEqual(answer, "2#1")


if __name__ == "__main__":
    unittest.main()
