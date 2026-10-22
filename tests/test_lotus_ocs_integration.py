"""Run against an installed OCS adapter with OCS_AI_SOURCE=/path/to/ocs-ai.

Uses the adapter's real HTTP server, parser and normalizer with a fake model.
No production server, credentials, course submissions or model quota are used.
"""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

import httpx

from api.lotus_tiku import LotusAnswerService, LotusResponseError


FIXTURE = r"""
import {pathToFileURL} from 'node:url';
const root = pathToFileURL(process.argv[1] + '/');
const {createServer} = await import(new URL('src/server.mjs', root));
const {config} = await import(new URL('test/helpers.mjs', root));
const server = createServer(config({backend:'codex', codexModel:'test-model'}), {
  codexRunner: async content => {
    const text = JSON.stringify(content);
    let answers = ['B'];
    if (text.includes('真假')) answers = ['正确'];
    if (text.includes('两空')) answers = ['C#', '第二空'];
    if (text.includes('少一空')) answers = ['第一空'];
    if (text.includes('图文')) {
      if (content.filter(x => x.type === 'input_image').length !== 2)
        throw new Error('missing images');
      if (!text.includes('选项A-图片1') || !text.includes('题干-图片1'))
        throw new Error('missing image ownership');
    }
    return JSON.stringify({answers});
  },
  downloader: async () => {throw new Error('native images must not download');},
});
server.listen(0, '127.0.0.1', () => console.log(server.address().port));
"""


@unittest.skipUnless(os.environ.get('OCS_AI_SOURCE') and shutil.which('node'),
                     'set OCS_AI_SOURCE to test against the installed Node adapter')
class LotusOcsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = str(Path(os.environ['OCS_AI_SOURCE']).resolve())
        cls.process = subprocess.Popen(['node', '--input-type=module', '-e', FIXTURE, root],
                                       stdout=subprocess.PIPE, text=True)
        cls.addClassCleanup(cls.stop_server)
        port = int(cls.process.stdout.readline().strip())
        cls.service = LotusAnswerService({'lotus_url': f'http://127.0.0.1:{port}',
                                         'lotus_token': 'test-adapter-token-123456'})

    @classmethod
    def stop_server(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.process.stdout.close()

    def response(self, question):
        request = self.service.build_request(question)
        with httpx.Client(trust_env=False) as client:
            response = client.post(self.service.url, json=request,
                                   headers={'Authorization': f'Bearer {self.service.token}'})
            response.raise_for_status()
        return response.json(), request

    def test_normal_answers_and_reversed_truth_options(self):
        for question, expected in [
            ({'type': 'single', 'title': '普通单选', 'options': ['甲', '乙']}, 'B'),
            ({'type': 'single', 'title': '真假单选', 'options': ['错误', '正确']}, 'B'),
            ({'type': 'completion', 'title': '两空 [BLANK_1] [BLANK_2]', 'blank_count': 2}, ['C#', '第二空']),
        ]:
            payload, request = self.response(question)
            self.assertEqual(self.service.parse_response(payload, request), expected)

    def test_manual_blank_mismatch_and_duplicate_choices_are_rejected(self):
        for question in [
            {'type': 'completion', 'title': '少一空', 'blank_count': 2},
            {'type': 'single', 'title': '重复选项', 'options': ['甲', '甲']},
        ]:
            payload, request = self.response(question)
            self.assertEqual(payload['code'], 1)
            self.assertTrue(payload['manual'])
            with self.assertRaises(LotusResponseError) as error:
                self.service.parse_response(payload, request)
            self.assertEqual(error.exception.code, 'LOTUS_MANUAL_REQUIRED')

    def test_native_images_keep_stem_option_and_placeholder_order(self):
        image = ('data:image/png;base64,'
                 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jT1cAAAAASUVORK5CYII=')
        payload, request = self.response({
            'type': 'single', 'title': '图文 [BLANK_1] https://example.edu/stem',
            'options': ['甲 https://example.edu/option', '乙'],
            'images': [image, image],
            'original_title': '图文 https://example.edu/stem',
            'original_options': '甲 https://example.edu/option\n乙',
            'suggestion_title': '图文 [图片2]', 'suggestion_options': '甲 [图片1]\n乙',
        })
        self.assertEqual(payload['meta']['imageCount'], 2)
        self.assertEqual(self.service.parse_response(payload, request), 'B')
