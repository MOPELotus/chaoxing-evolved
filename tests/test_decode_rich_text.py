from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from api.decode import _rich_node_text, decode_questions_info


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

    def test_editor_scripts_are_not_question_text(self):
        node = BeautifulSoup(
            '<div><textarea name="answerEditor1231"></textarea><script>只能录入不能粘贴</script></div>',
            "html.parser",
        ).div
        text, blank_count, _ = _rich_node_text(node)
        self.assertEqual(text, "[BLANK_1]")
        self.assertEqual(blank_count, 1)
        self.assertNotIn("只能录入不能粘贴", text)

    def test_completion_editor_fields_are_preserved_in_page_order(self):
        html = """
        <form><div class="singleQuesId" data="123">
          <div class="TiMu" data="2">
            <div class="Zy_TItle">【填空题】Match the paragraphs.</div>
            <div class="textDIV"><textarea name="answerEditor12310"></textarea><script>noise10</script></div>
            <div class="textDIV"><textarea name="answerEditor1232"></textarea><script>noise2</script></div>
            <div class="textDIV"><textarea name="answerEditor1231"></textarea><script>noise1</script></div>
            <input name="answertype123" value="2">
          </div>
        </div></form>
        """
        question = decode_questions_info(html)["questions"][0]
        self.assertEqual(
            question["answer_fields"],
            ["answerEditor1231", "answerEditor1232", "answerEditor12310"],
        )
        self.assertEqual(question["blank_count"], 3)
        self.assertEqual(question["option_items"], [])


if __name__ == "__main__":
    unittest.main()
