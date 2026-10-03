import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

import httpx

from api.answer import AI
from api.base import prepare_submission_answer
from api.response_ai import ResponsesAnswerService
from api.json_store import DEFAULT_GLOBAL_SETTINGS, DEFAULT_PROFILE, build_config_sections, build_effective_profile, profile_summary
from api.lotus_tiku import LotusAnswerService, LotusQuestionError


class LotusTests(unittest.TestCase):
    def service(self, **kwargs):
        return LotusAnswerService({'lotus_url': 'https://lotus.example', 'lotus_token': 'test-token', **kwargs})

    def test_url_and_credentials(self):
        for address in ('https://lotus.example/', 'https://lotus.example/search/'):
            self.assertEqual(self.service(lotus_url=address).url, 'https://lotus.example/search')
        for address in ('file:///tmp/key', 'https://user:pass@lotus.example', 'https://lotus.example?key=secret'):
            with self.assertRaises(ValueError):
                self.service(lotus_url=address)
        with self.assertRaises(RuntimeError):
            self.service(lotus_token='').answer({'type': 'single', 'title': 'test'})

    def test_image_and_material_locations(self):
        wire = self.service().build_request({'type': 'single', 'title': 'Choose [QUESTION_IMAGE:https://x/stem?a=1&b=2]',
            'material': 'Material [QUESTION_IMAGE:https://x/material]',
            'option_items': ['[QUESTION_IMAGE:https://x/option]', 'Text'],
            'image_urls': ['https://x/option', 'https://x/stem?a=1&b=2', 'https://x/extra'],
            'material_image_urls': ['https://x/material']})
        self.assertNotIn('https://x/option', wire['title'])
        self.assertIn('https://x/option', wire['options'][0])
        self.assertEqual(wire['title'].count('https://x/material'), 1)
        self.assertEqual(wire['title'].count('https://x/extra'), 1)
        self.assertIn('a=1&amp;b=2', wire['title'])

    def test_ambiguous_and_unsupported_inputs_stop(self):
        for question in ({'type': 'matching'}, {'type': 'single', 'options': 'A\nB'},
                         {'type': 'single', 'image_urls': 'https://x/image'},
                         {'type': 'single', 'title': '[QUESTION_IMAGE:embedded]'}):
            with self.assertRaises(LotusQuestionError):
                self.service().build_request(question)
        for count in (True, 0, -1, '2'):
            with self.assertRaises(LotusQuestionError):
                self.service().build_request({'type': 'completion', 'blank_count': count})

    def test_native_answer_shapes_and_wire_answer_is_not_used(self):
        cases = [('single', ['B'], 'B'), ('multiple', ['A', 'C'], 'AC'),
                 ('judgement', ['错误'], '错误'), ('completion', ['甲', '乙'], ['甲', '乙'])]
        for qtype, answers, expected in cases:
            wire = {'type': qtype, 'options': ['A', 'B', 'C']}
            if qtype == 'completion':
                wire['blankCount'] = 2
            with self.subTest(qtype=qtype):
                self.assertEqual(self.service().parse_response({'code': 1, 'answer': 'misleading content', 'answers': answers}, wire), expected)
        wire = self.service().build_request({'type': 'shortanswer', 'title': 'Explain'})
        self.assertEqual(wire['blankCount'], 1)
        self.assertEqual(wire['type'], 'completion')

    def test_invalid_answers_and_business_failures(self):
        wire = {'type': 'single', 'options': ['A', 'B']}
        for payload in ({'code': 0, 'error': 'ADAPTER_FAILED'}, {'code': 1, 'answers': ['C']},
                        {'code': 1, 'answers': ['A', 'B']}, {'code': 1, 'answers': []}):
            with self.assertRaises(RuntimeError):
                self.service().parse_response(payload, wire)
        with self.assertRaises(RuntimeError):
            self.service().parse_response({'code': 1, 'answers': ['甲']}, {'type': 'completion', 'blankCount': 2})

    def test_timeout_and_http_failures_do_not_duplicate_requests(self):
        for failure in (httpx.ReadTimeout('timeout'), httpx.Response(401, request=httpx.Request('POST', 'https://lotus.example/search')),
                        httpx.Response(200, json={'code': 0}, request=httpx.Request('POST', 'https://lotus.example/search'))):
            with patch('api.lotus_tiku.httpx.Client') as client:
                post = client.return_value.__enter__.return_value.post
                if isinstance(failure, Exception):
                    post.side_effect = failure
                else:
                    post.return_value = failure
                self.assertIsNone(self.service().answer({'type': 'single', 'title': 'Choose', 'options': ['A', 'B']}))
                self.assertEqual(post.call_count, 1)

    def test_cache_opt_in_and_refresh(self):
        cache = Mock()
        cache.get_cache.return_value = 'A'
        question = {'type': 'single', 'title': 'Choose', 'options': ['A', 'B']}
        service = self.service(semantic_cache_enabled=True)
        service.cache = cache
        self.assertEqual(service.answer(question), 'A')
        self.assertTrue(cache.get_cache.call_args.args[0].startswith('lotus:'))
        with patch('api.lotus_tiku.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value = httpx.Response(200, json={'code': 1, 'answers': ['B']}, request=httpx.Request('POST', service.url))
            self.assertEqual(service.answer(question, force_refresh=True), 'B')
            cache.add_cache.assert_called_once()
            self.assertEqual(client.call_args.kwargs['trust_env'], False)
        self.assertEqual(cache.get_cache.call_count, 1)

    def test_runtime_routes_and_native_subjective_fields(self):
        provider = AI()
        provider.config_set({'answer_backend': 'lotus', 'lotus_token': 'test-token'})
        provider._init_tiku()
        self.assertIsInstance(provider._response_service, LotusAnswerService)
        self.assertEqual(provider.name, '荷花题库')
        provider.config_set({'endpoint': 'https://ai.example', 'key': 'test-key', 'model': 'test-model'})
        provider._init_tiku()
        self.assertIsInstance(provider._response_service, ResponsesAnswerService)
        for kind in ('shortanswer', 'calculation'):
            with patch('api.lotus_tiku.httpx.Client') as client:
                client.return_value.__enter__.return_value.post.return_value = httpx.Response(200, json={'code': 1, 'answers': ['response']}, request=httpx.Request('POST', self.service().url))
                question = {'type': kind, 'title': 'Explain', 'answer_fields': ['editor1']}
                answer = self.service().answer(question)
                self.assertEqual(prepare_submission_answer(answer, question), ('response', {'editor1': '<p>response</p>'}))
                self.assertEqual(prepare_submission_answer(answer, {'type': kind, 'native_answer_field': 'text1'}), ('response', {'text1': 'response'}))

    def test_configured_interval_between_request_starts(self):
        service = self.service(min_interval_seconds=3)
        question = {'type': 'single', 'title': 'Choose', 'options': ['A', 'B']}
        with patch('api.lotus_tiku.httpx.Client') as client, patch('api.lotus_tiku.time.monotonic', side_effect=[10, 11, 13]), patch('api.lotus_tiku.time.sleep') as sleep:
            client.return_value.__enter__.return_value.post.return_value = httpx.Response(200, json={'code': 1, 'answers': ['B']}, request=httpx.Request('POST', service.url))
            self.assertEqual(service.answer(question), 'B')
            self.assertEqual(service.answer(question), 'B')
            sleep.assert_called_once_with(2)

    def test_global_inheritance_and_profile_override(self):
        settings = deepcopy(DEFAULT_GLOBAL_SETTINGS)
        settings['defaults']['tiku'].update(answer_backend='lotus', lotus_token='global-token')
        profile = deepcopy(DEFAULT_PROFILE)
        profile['name'] = 'test'
        effective = build_effective_profile(profile, settings)
        self.assertEqual(effective['tiku']['answer_backend'], 'lotus')
        self.assertEqual(build_config_sections(profile, settings)['tiku']['lotus_token'], 'global-token')
        self.assertEqual(profile_summary(profile, settings)['provider'], '荷花题库')
        profile['tiku']['answer_backend'] = 'responses'
        profile['overrides'] = {'tiku': {'answer_backend': True}}
        self.assertEqual(build_effective_profile(profile, settings)['tiku']['answer_backend'], 'responses')
        profile['overrides']['tiku']['answer_backend'] = False
        self.assertEqual(build_effective_profile(profile, settings)['tiku']['answer_backend'], 'lotus')


if __name__ == '__main__':
    unittest.main()
