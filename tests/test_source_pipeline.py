"""Four real extraction adapters into synthesis input, using invented HTML and mock responses."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import assemble_synthesis as assembly
import synthesise
import extract_bbva as bbva
import extract_abn_amro as abn
import extract_bis as bis
import extract_nyfed_lse as lse
from test_dedupe import seed
from test_extract_bbva import NOW, SEMANTICS, response
import test_read_bbva as bbva_fixture
import test_read_abn_amro as abn_fixture
import test_read_bis as bis_fixture
import test_read_nyfed_lse as lse_fixture


class SourcePipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        seed(self.root)
        for target in ('socket.socket.connect', 'subprocess.run'):
            blocker = patch(target, side_effect=AssertionError('No network/process calls'))
            blocker.start()
            self.addCleanup(blocker.stop)

    def extract_sources(self, fail_lse=False):
        for adapter, reader, fixture in (
            (bbva, bbva.read_bbva, bbva_fixture),
            (abn, abn.read, abn_fixture),
            (bis, bis.read, bis_fixture),
            (lse, lse.read, lse_fixture),
        ):
            client = NS(responses=NS(create=Mock(return_value=response(refusal=fail_lse and adapter is lse))))
            with patch.object(reader, 'fetch', side_effect=[fixture.ROBOTS, (fixture.HTML, 'text/html')]), patch.object(reader.time, 'sleep'):
                if fail_lse and adapter is lse:
                    with self.assertRaises(ValueError):
                        adapter.run_spike(client, self.root, NOW, limit=1)
                else:
                    adapter.run_spike(client, self.root, NOW, limit=1)
            self.assertEqual(client.responses.create.call_count, 1)

    def test_all_four_adapters_feed_synthesis_without_raw_content(self):
        self.extract_sources()
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        packet = assembly.assemble(self.root, NOW)
        self.assertEqual(packet['coverage']['article_count'], 4)
        self.assertEqual(packet['coverage']['institution_count'], 4)
        self.assertEqual(packet['diagnostics'], [])
        request = synthesise.request_payload(packet)
        projected = json.loads(request['input'][0]['content'])
        self.assertEqual(len(projected['articles']), 4)
        for n, article in enumerate(projected['articles'], 1):
            self.assertEqual(article['article_ref'], f'a{n}')
            self.assertEqual(article['claims'], [{'ref': f'a{n}:c1', 'text': SEMANTICS['claims'][0]}])
            self.assertEqual(article['summary'], SEMANTICS['summary'])
        depths = {a['source_name']: a['read_depth'] for a in packet['articles']}
        self.assertIn('scope', depths['BBVA Research'])
        self.assertEqual(depths['ABN AMRO Group Economics']['text_source'], 'article_html')
        self.assertEqual(depths['BIS and FSI publications']['text_source'], 'publication_page_html')
        self.assertEqual(depths['Liberty Street Economics']['text_source'], 'article_body_html')
        persisted = b''.join(before.values()).decode()
        for fixture in (bbva_fixture, abn_fixture, bis_fixture, lse_fixture):
            self.assertNotIn(fixture.HTML, persisted)
            self.assertNotIn(fixture.HTML, json.dumps(request))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        self.assertFalse((self.root / synthesise.RESULT).exists())

    def test_failed_source_does_not_block_other_valid_sources(self):
        self.extract_sources(fail_lse=True)
        packet = assembly.assemble(self.root, NOW)
        self.assertEqual(packet['coverage']['article_count'], 3)
        self.assertEqual(packet['coverage']['per_institution']['Federal Reserve Bank of New York'], 0)
        self.assertIn({'file': lse.RESULTS, 'reason': 'missing_extraction_file'}, packet['diagnostics'])
        self.assertEqual(len(json.loads(synthesise.request_payload(packet)['input'][0]['content'])['articles']), 3)
        self.assertFalse((self.root/lse.RESULTS).exists())

    def test_canonical_synthesis_into_exact_verification_input(self):
        import hashlib
        import synthesise_candidate as canonical
        import verify_candidate
        from test_synthesise import output
        for missing_lse in (False, True):
            with self.subTest(missing_lse=missing_lse), tempfile.TemporaryDirectory() as directory:
                original=self.root
                self.root=Path(directory)
                try:
                    seed(self.root)
                    self.extract_sources(fail_lse=missing_lse)
                    client=NS(responses=NS(create=Mock(return_value=NS(status='completed',error=None,
                        output=[NS(type='message',status='completed',content=[NS(type='output_text',text=json.dumps(output()))])]))))
                    canonical.run(self.root,live=True,client=client,now=NOW)
                    raw=(self.root/synthesise.RESULT).read_bytes()
                    stored=json.loads(raw)
                    self.assertEqual(stored['coverage']['article_count'],3 if missing_lse else 4)
                    self.assertEqual(stored['source_input_sha256'],hashlib.sha256(
                        client.responses.create.call_args.kwargs['input'][0]['content'].encode()).hexdigest())
                    before={p.name:p.read_bytes() for p in self.root.iterdir()}
                    diagnostic=verify_candidate.run(self.root)
                    self.assertEqual(diagnostic['source_snapshot_sha256'],hashlib.sha256(raw).hexdigest())
                    self.assertTrue(diagnostic['request_size_permitted'])
                    self.assertFalse(diagnostic['publication_approved'])
                    self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})
                finally:
                    self.root=original
