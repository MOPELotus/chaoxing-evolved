from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import Mock, patch

from api.response_ai import ResponsesAnswerService


class ResponseAIMediaTests(unittest.TestCase):
    def test_current_runner_labels_stem_material_and_option_images(self):
        question = {
            "title": "题干 [QUESTION_IMAGE:https://img.test/stem.png]",
            "material": "材料 [QUESTION_IMAGE:https://img.test/material.png]",
            "options": "文字选项\n图片选项 [QUESTION_IMAGE:https://img.test/b.png]",
            "option_items": [
                "文字选项\n即使选项自身换行也仍属于 A",
                "图片选项 [QUESTION_IMAGE:https://img.test/b.png]",
            ],
            "image_urls": [
                "https://img.test/stem.png",
                "https://img.test/material.png",
                "https://img.test/b.png",
            ],
            "material_image_urls": ["https://img.test/material.png"],
        }
        with patch.object(ResponsesAnswerService, "_image_data", side_effect=lambda value: value):
            blocks = ResponsesAnswerService._media_blocks(question)
        labels = [block["text"] for block in blocks if block["type"] == "input_text"]
        self.assertTrue(any("题干图片 1" in label for label in labels))
        self.assertTrue(any("材料图片 1" in label for label in labels))
        self.assertTrue(any("选项 B 图片 1" in label for label in labels))
        self.assertEqual(sum(block["type"] == "input_image" for block in blocks), 3)

    def test_remote_image_is_downloaded_and_encoded_as_data_url(self):
        response = Mock()
        response.content = b"\x89PNG\r\n\x1a\nimage-bytes"
        response.headers = {"Content-Type": "image/png"}
        response.raise_for_status.return_value = None
        with patch("api.response_ai.requests.get", return_value=response):
            value = ResponsesAnswerService._image_data("https://p.ananas.chaoxing.com/example.png")
        prefix, encoded = value.split(",", 1)
        self.assertEqual(prefix, "data:image/png;base64")
        self.assertEqual(base64.b64decode(encoded), response.content)

    def test_request_contains_data_image_but_not_source_url(self):
        service = ResponsesAnswerService(
            {"base_url": "https://api.openai.com", "api_key": "test", "model": "test-model"}
        )
        question = {
            "type": "single",
            "title": "题干 [QUESTION_IMAGE:https://private.test/stem.png]",
            "options": "yes\nno",
            "option_items": ["yes", "no"],
            "image_urls": ["https://private.test/stem.png"],
        }
        data_url = "data:image/png;base64," + base64.b64encode(b"image-bytes").decode("ascii")
        with patch.object(ResponsesAnswerService, "_image_data", return_value=data_url):
            request = service.build_request(question)
        serialized = json.dumps(request, ensure_ascii=False)
        self.assertIn(data_url, serialized)
        self.assertNotIn("https://private.test/stem.png", serialized)

    def test_failed_download_never_falls_back_to_original_url(self):
        question = {
            "title": "题干 [QUESTION_IMAGE:https://private.test/stem.png]",
            "image_urls": ["https://private.test/stem.png"],
        }
        with patch.object(ResponsesAnswerService, "_image_data", side_effect=ValueError("not an image")):
            blocks = ResponsesAnswerService._media_blocks(question)
        self.assertFalse(any(block["type"] == "input_image" for block in blocks))
        self.assertNotIn("https://private.test/stem.png", json.dumps(blocks, ensure_ascii=False))

        service = ResponsesAnswerService(
            {"base_url": "https://api.openai.com", "api_key": "test", "model": "test-model"}
        )
        with patch.object(ResponsesAnswerService, "_image_data", side_effect=ValueError("not an image")):
            request = service.build_request(question)
        serialized = json.dumps(request, ensure_ascii=False)
        self.assertNotIn("https://private.test/stem.png", serialized)
        self.assertIn("[图片附件]", serialized)

    def test_choice_request_has_explicit_stable_labels(self):
        service = ResponsesAnswerService(
            {
                "base_url": "https://api.openai.com",
                "api_key": "test",
                "model": "test-model",
            }
        )
        request = service.build_request(
            {
                "type": "single",
                "title": "Which is not a benefit?",
                "options": "Some benefit\nfoo bar",
                "option_items": ["Some benefit", "foo bar"],
            }
        )
        payload = json.loads(request["input"][0]["content"][0]["text"])
        self.assertEqual(payload["question"]["options"], "A. Some benefit\nB. foo bar")
        self.assertEqual(payload["question"]["option_count"], 2)
        self.assertIn("只返回一个大写选项字母", request["instructions"])


if __name__ == "__main__":
    unittest.main()
