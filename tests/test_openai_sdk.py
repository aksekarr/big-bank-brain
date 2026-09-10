"""Installed SDK compatibility, entirely in-memory HTTP transport; no real API calls."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import extract_bbva as ai
from test_extract_bbva import SEMANTICS

try:
    import openai
    import httpx
except ImportError:
    openai = None
    httpx = None


@unittest.skipUnless(openai is not None, 'Optional SDK: run with .venv/bin/python')
class InstalledSDKTests(unittest.TestCase):
    def invoke(self, refusal=False):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(str(request.url), 'https://api.openai.com/v1/responses')
            self.assertEqual(request.method, 'POST')
            payload = json.loads(request.content)
            self.assertEqual(payload['model'], ai.MODEL)
            self.assertEqual(payload['reasoning'], {'effort': 'low'})
            self.assertEqual(payload['max_output_tokens'], 2000)
            self.assertEqual(payload['tools'], [])
            self.assertFalse(payload['store'])
            self.assertEqual(payload['text']['format']['type'], 'json_schema')
            self.assertTrue(payload['text']['format']['strict'])
            self.assertEqual(payload['text']['format']['schema'], ai.SCHEMA)
            content = {'type': 'refusal', 'refusal': 'Synthetic refusal'} if refusal else {
                'type': 'output_text', 'text': json.dumps(SEMANTICS), 'annotations': []}
            return httpx.Response(200, json={
                'id': 'resp_offline_fixture', 'object': 'response', 'created_at': 1,
                'model': ai.MODEL, 'status': 'completed', 'error': None,
                'output': [{'id': 'msg_offline_fixture', 'type': 'message',
                            'role': 'assistant', 'status': 'completed', 'content': [content]}],
                'usage': {'input_tokens': 100, 'output_tokens': 100, 'total_tokens': 200}})
        actual_http_client = openai.DefaultHttpxClient
        def fake_transport_client(**kwargs):
            self.assertFalse(kwargs['trust_env'])
            self.assertFalse(kwargs['follow_redirects'])
            return actual_http_client(transport=httpx.MockTransport(handler), **kwargs)
        item = {'title': 'Invented research', 'institution': 'BBVA Research', 'publication_date': '2026-09-11'}
        # Dummy credential only. Socket operations are forbidden even if the mock is misconfigured.
        with patch.dict(ai.os.environ, {'OPENAI_API_KEY': 'offline-placeholder-not-a-real-key'}, clear=True), patch.object(openai, 'DefaultHttpxClient', side_effect=fake_transport_client), patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')):
            with ai.make_client() as client:
                self.assertEqual(client.max_retries, 0)
                response = client.responses.create(**ai.request_payload(item, 'Invented economic observations for an offline test.'))
        self.assertEqual(len(requests), 1)
        return response

    def test_real_sdk_serialises_strict_request_and_parses_mock_response(self):
        response = self.invoke()
        self.assertEqual(ai.parse_response(response, 'Invented source.'), SEMANTICS)

    def test_real_sdk_refusal_is_rejected(self):
        response = self.invoke(refusal=True)
        with self.assertRaises(ai.SpikeError):
            ai.parse_response(response, 'Invented source.')


if __name__ == '__main__':
    unittest.main()
