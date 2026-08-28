from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from api.decode import _rich_node_text


class RichTextDecodeTests(unittest.TestCase):
    def test_underlined_whitespace_is_a_blank(self):
        node = BeautifulSoup(
            '<div>A <span style="text-decoration: underline;">&nbsp; &nbsp; &nbsp;</span> B</div>',
            "html.parser",
        ).div
        text, blank_count, underline_count = _rich_node_text(node)
        self.assertIn("[BLANK_1]", text)
        self.assertEqual(blank_count, 1)
        self.assertEqual(underline_count, 0)

    def test_underlined_text_and_parenthesized_blank_keep_distinct_meanings(self):
        node = BeautifulSoup(
            '<div>Replace <span style="text-decoration: underline;">in service</span> (&nbsp; &nbsp; )</div>',
            "html.parser",
        ).div
        text, blank_count, underline_count = _rich_node_text(node)
        self.assertIn("[UNDERLINE]in service[/UNDERLINE]", text)
        self.assertIn("([BLANK_1])", text)
        self.assertEqual(blank_count, 1)
        self.assertEqual(underline_count, 1)


if __name__ == "__main__":
    unittest.main()
