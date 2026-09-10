"""Offline API fakes and invented publisher-shaped HTML; never paid calls."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import extract_bbva as ai
from test_dedupe import seed
from test_read_bbva import HTML, INTRO, POINT, ROBOTS

NOW = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
SEMANTICS = {'summary': 'The analysis describes steady demand and gradual economic changes.',
             'topics': ['Economic conditions'], 'claims': ['Consumer demand is stable.'],
             'geographies': [], 'markets_or_asset_classes': [], 'time_horizon': None}


def response(data=None, status='completed', refusal=False):
    content = NS(type='refusal', refusal='Synthetic refusal') if refusal else NS(type='output_text', text=json.dumps(SEMANTICS if data is None else data))
    return NS(status=status, error=None, output=[NS(type='message', status='completed', content=[content])])


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        seed(self.root)
        self.client = NS(responses=NS(create=Mock(return_value=response())))

    def run_live_fake(self):
        with patch.object(ai.read_bbva, 'fetch', side_effect=[ROBOTS, (HTML, 'text/html')]), patch.object(ai.read_bbva.time, 'sleep'):
            return ai.run_spike(self.client, self.root, NOW)

    def test_success_saves_semantics_then_marks_processed_and_skips_repeat(self):
        self.run_live_fake()
        saved = ai.dedupe.read_json(self.root/ai.RESULTS)
        url = next(iter(saved['items']))
        self.assertEqual(saved['items'][url]['extraction'], SEMANTICS)
        self.assertIn(url, ai.processing.load_processed(self.root))
        args = self.client.responses.create.call_args.kwargs
        self.assertEqual(args['model'], 'gpt-5.6-terra')
        self.assertEqual(args['reasoning'], {'effort': 'low'})
        self.assertEqual(args['max_output_tokens'], 2000)
        self.assertEqual(args['tools'], [])
        self.assertEqual(args['tool_choice'], 'none')
        self.assertFalse(args['store'])
        self.assertTrue(args['text']['format']['strict'])
        self.assertIn(INTRO, args['input'][0]['content'])
        for path in self.root.iterdir():
            if path.name in [ai.RESULTS, ai.LEDGER, ai.processing.STATE_NAME]:
                self.assertNotIn(INTRO, path.read_text())
                self.assertNotIn(POINT, path.read_text())
                self.assertNotIn('<html>', path.read_text())
        self.assertEqual(ai.run_spike(self.client, self.root, NOW), [])
        self.assertEqual(self.client.responses.create.call_count, 1)

    def test_api_error_refusal_incomplete_and_invalid_schema_remain_unprocessed(self):
        failures = [RuntimeError('SECRET REQUEST CONTENT'), response(refusal=True), response(status='incomplete'), response({'summary': 'missing fields'}), response(dict(SEMANTICS, unexpected='extra')), response(dict(SEMANTICS, time_horizon=12))]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                seed(root)
                client = NS(responses=NS(create=Mock(side_effect=failure if isinstance(failure, Exception) else None, return_value=failure)))
                with patch.object(ai.read_bbva, 'fetch', side_effect=[ROBOTS,(HTML,'text/html')]), patch.object(ai.read_bbva.time, 'sleep'), self.assertRaises(ai.SpikeError) as error:
                    ai.run_spike(client, root, NOW)
                self.assertNotIn('SECRET', str(error.exception))
                self.assertEqual(ai.processing.load_processed(root), {})
                self.assertFalse((root/ai.RESULTS).exists())
                self.assertEqual(len(ai.load_ledger(root)['attempts']), 1)

    def test_read_failure_or_denial_never_calls_api(self):
        for replies in [[('User-agent: *\nDisallow: /', 'text/plain')], [ROBOTS, ('<html>empty</html>', 'text/html')]]:
            with patch.object(ai.read_bbva, 'fetch', side_effect=replies), patch.object(ai.read_bbva.time, 'sleep'):
                result = ai.run_spike(self.client, self.root, NOW)
            self.assertFalse(result[0]['extraction_success'])
            self.client.responses.create.assert_not_called()
            self.assertEqual(ai.processing.load_processed(self.root), {})
            self.assertFalse((self.root/ai.LEDGER).exists())

    def test_persistent_three_call_limit_counts_errors_across_runs(self):
        self.client.responses.create.side_effect = RuntimeError('offline fake error')
        for _ in range(3):
            with self.assertRaises(ai.SpikeError):
                self.run_live_fake()
        with patch.object(ai.read_bbva, 'fetch') as fetch, self.assertRaises(ai.SpikeError):
            ai.run_spike(self.client, self.root, NOW)
        fetch.assert_not_called()
        self.assertEqual(self.client.responses.create.call_count, 3)
        self.assertEqual(sum(a['reserved_micro_usd'] for a in ai.load_ledger(self.root)['attempts']), 139680)

    def test_rejections_release_slots_but_results_consume_them_across_runs(self):
        for status in [401, 429, 429, 401]:
            error = RuntimeError('private provider details')
            error.status_code = status
            self.client.responses.create.side_effect = error
            before = self.client.responses.create.call_count
            with self.assertRaises(ai.SpikeError):
                self.run_live_fake()
            self.assertEqual(self.client.responses.create.call_count, before + 1)
            ledger = ai.load_ledger(self.root)
            self.assertEqual(ai.held_calls(ledger), 0)
            self.assertEqual(ledger['attempts'][-1]['http_status'], status)
            self.assertEqual(ledger['attempts'][-1]['outcome'], 'rejected')
        self.client.responses.create.side_effect = None
        for result in [response(refusal=True), response({'summary': 'invalid'}), response(status='incomplete')]:
            self.client.responses.create.return_value = result
            with self.assertRaises(ai.SpikeError):
                self.run_live_fake()
        ledger = ai.load_ledger(self.root)
        self.assertEqual(len(ledger['attempts']), 7)
        self.assertEqual(ai.held_calls(ledger), 3)
        self.assertEqual([a['outcome'] for a in ledger['attempts'][-3:]], ['result']*3)
        with patch.object(ai.read_bbva, 'fetch') as fetch, self.assertRaises(ai.SpikeError):
            ai.run_spike(self.client, self.root, NOW)
        fetch.assert_not_called()
        self.assertEqual(self.client.responses.create.call_count, 7)
        self.assertEqual(ai.processing.load_processed(self.root), {})

    def test_failed_outcome_save_keeps_pending_reservation_on_disk(self):
        error = RuntimeError('synthetic')
        error.status_code = 429
        self.client.responses.create.side_effect = error
        original = ai.processing.write_json
        def write(data, path):
            if path.name == ai.LEDGER and data['attempts'][-1]['outcome'] != 'pending':
                raise OSError('synthetic persistence failure')
            return original(data, path)
        with patch.object(ai.processing, 'write_json', side_effect=write), self.assertRaises(OSError):
            self.run_live_fake()
        self.assertEqual(ai.held_calls(ai.load_ledger(self.root)), 1)
        self.assertEqual(ai.load_ledger(self.root)['attempts'][0]['outcome'], 'pending')

    def test_legacy_reservations_held_and_invalid_release_rejected(self):
        attempt = {'url': ai.select(self.root, NOW, 1)[0]['url'],
                   'reserved_at': NOW.isoformat(), 'reserved_micro_usd': ai.RESERVE_MICRO_USD}
        ai.processing.write_json({'version': 1, 'attempts': [attempt]}, self.root/ai.LEDGER)
        self.assertEqual(ai.held_calls(ai.load_ledger(self.root)), 1)
        attempt.update(outcome='rejected', http_status=500)
        ai.processing.write_json({'version': 1, 'attempts': [attempt]}, self.root/ai.LEDGER)
        with self.assertRaises(ai.SpikeError):
            ai.load_ledger(self.root)

    def test_selection_refreshes_stale_ready_after_success(self):
        path = self.root / 'bbva-discovery.json'
        snapshot = ai.dedupe.read_json(path)
        original = snapshot['items'][0]
        snapshot['items'] = [dict(original, url=original['url']+str(n)+'/') for n in range(3)]
        snapshot['item_count'] = 3
        path.write_text(json.dumps(snapshot))
        first = ai.select(self.root, NOW, 1)[0]
        ai.processing.acknowledge(self.root, first['url'], 'succeeded', NOW)
        stale = ai.dedupe.read_json(self.root/ai.processing.READY_NAME)
        self.assertIn(first['url'], [i['url'] for i in stale['items']])
        selected = ai.select(self.root, NOW, 1)
        self.assertEqual(selected[0]['url'], snapshot['items'][1]['url'])

    def test_input_size_cap_prevents_call_and_reservation(self):
        item = ai.select(self.root, NOW, 3)[0]
        with self.assertRaises(ai.SpikeError):
            ai.extract_one(self.client, item, 'x'*8000, self.root, NOW, ai.load_ledger(self.root))
        self.client.responses.create.assert_not_called()
        self.assertFalse((self.root/ai.LEDGER).exists())

    def test_schema_and_copy_checks(self):
        self.assertEqual(ai.validate(SEMANTICS), SEMANTICS)
        for bad in [dict(SEMANTICS, summary='word '*61), dict(SEMANTICS, claims=[]), dict(SEMANTICS, topics=[''] ), dict(SEMANTICS, geographies='France')]:
            with self.assertRaises(ai.SpikeError):
                ai.validate(bad)
        with self.assertRaises(ai.SpikeError):
            ai.reject_source_copy(dict(SEMANTICS, summary=POINT), POINT)

    def test_persistence_failures_never_prematurely_acknowledge(self):
        for failed_file in [ai.LEDGER, ai.RESULTS, ai.processing.STATE_NAME]:
            with self.subTest(file=failed_file), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); seed(root)
                ai.processing.execute('prepare', root, NOW)
                write = ai.processing.write_json
                def fail(data, path):
                    if path.name == failed_file:
                        raise OSError('synthetic write failure')
                    return write(data, path)
                with patch.object(ai.processing, 'write_json', fail), patch.object(ai.read_bbva, 'fetch', side_effect=[ROBOTS,(HTML,'text/html')]), patch.object(ai.read_bbva.time, 'sleep'), self.assertRaises(OSError):
                    ai.run_spike(self.client, root, NOW)
                self.assertEqual(ai.processing.load_processed(root), {})

    def test_client_configuration_and_missing_key_safety(self):
        constructor = Mock()
        http = Mock()
        with patch.dict('sys.modules', {'openai': NS(OpenAI=constructor, DefaultHttpxClient=http)}), patch.dict(ai.os.environ, {'OPENAI_API_KEY': 'synthetic-test-key'}, clear=True):
            ai.make_client()
        self.assertEqual(constructor.call_args.kwargs['max_retries'], 0)
        self.assertEqual(constructor.call_args.kwargs['base_url'], 'https://api.openai.com/v1')
        self.assertEqual(http.call_args.kwargs, {'trust_env': False, 'follow_redirects': False})
        with patch.dict(ai.os.environ, {}, clear=True), self.assertRaises(ai.SpikeError):
            ai.make_client()
        with patch.dict(ai.os.environ, {'OPENAI_LOG': 'debug'}, clear=True), self.assertRaises(ai.SpikeError):
            ai.make_client()

    def test_limit_one_selects_and_processes_only_one_article_offline(self):
        path = self.root / 'bbva-discovery.json'
        snapshot = ai.dedupe.read_json(path)
        original = snapshot['items'][0]
        snapshot['items'] = [dict(original, url=original['url']+str(n)+'/') for n in range(3)]
        snapshot['item_count'] = 3
        path.write_text(json.dumps(snapshot))
        self.assertEqual(len(ai.select(self.root, NOW, 1)), 1)
        with patch.object(ai.read_bbva, 'fetch', side_effect=[ROBOTS, (HTML, 'text/html')]), patch.object(ai.read_bbva.time, 'sleep'):
            diagnostics = ai.run_spike(self.client, self.root, NOW, limit=1)
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(self.client.responses.create.call_count, 1)
        self.assertEqual(len(ai.processing.load_processed(self.root)), 1)
        self.assertEqual(len(ai.load_ledger(self.root)['attempts']), 1)
        self.assertEqual(ai.MAX_CALLS, 3)

    def test_cli_limit_one_is_forwarded_without_real_requests(self):
        client = Mock()
        client.__enter__ = Mock(return_value=self.client)
        client.__exit__ = Mock(return_value=False)
        with patch.object(ai, 'make_client', return_value=client), patch.object(ai, 'run_spike', return_value=[]) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ai.main(['--live', '--limit', '1']), 0)
        run.assert_called_once_with(self.client, limit=1)
        for invalid in ['0', '4']:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                ai.main(['--limit', invalid])

    def test_metadata_only_default_never_fetches_or_creates_client(self):
        ready = {'items': ai.select(self.root, NOW, 3)}
        with patch.object(ai.processing, 'execute', return_value=ready), patch.object(ai, 'load_ledger', return_value={'attempts': []}), patch.object(ai, 'make_client') as client, patch.object(ai.read_bbva, 'fetch') as fetch, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ai.main([]), 0)
        fetch.assert_not_called(); client.assert_not_called()


if __name__ == '__main__':
    unittest.main()
