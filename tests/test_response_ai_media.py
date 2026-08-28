from __future__ import annotations

import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
