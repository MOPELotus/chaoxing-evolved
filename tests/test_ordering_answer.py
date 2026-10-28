import unittest

from api.base import prepare_submission_answer
from api.decode import decode_questions_info


class OrderingAnswerTests(unittest.TestCase):
    def test_native_sort_order_uses_concatenated_letters(self):
        controls = '<select><option value="-">-</option><option value="A">A</option><option value="B">B</option><option value="C">C</option></select>'
        page = '<form><div class="singleQuesId" data="123"><div class="TiMu" data="13"><div class="Zy_TItle">Sort</div><div class="sortQuesSelect">' + controls * 3 + '</div><input name="answer123" value=""></div></div></form>'
        question = decode_questions_info(page)['questions'][0]
        self.assertEqual(question['option_values'], ['A', 'B', 'C'])
        self.assertEqual(prepare_submission_answer(['B', 'A', 'C'], question), ('BAC', {}))
        self.assertEqual(prepare_submission_answer('B > A > C', question), ('BAC', {}))
        for invalid in ['BBA', 'BC', 'BAX', 'BAC explanation']:
            self.assertEqual(prepare_submission_answer(invalid, question), ('', {}))


if __name__ == '__main__':
    unittest.main()
