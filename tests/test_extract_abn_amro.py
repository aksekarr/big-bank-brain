"""ABN extraction with synthetic input and fake responses only."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import extract_abn_amro as abn
from test_dedupe import seed
from test_extract_bbva import NOW, SEMANTICS, response
from test_read_abn_amro import HTML, BODY, ROBOTS


class AbnExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        seed(self.root)
        snapshot_path = self.root/'abn-amro-discovery.json'
        snapshot = json.loads(snapshot_path.read_text())
        for item in snapshot['items']:
            item['excerpt'] = 'Invented listing describes steady economic demand.'
        snapshot_path.write_text(json.dumps(snapshot))
        self.client = NS(responses=NS(create=Mock(return_value=response())))
        # A completed BBVA ledger must be unaffected by every ABN operation.
        self.bbva = self.root/abn.ai.LEDGER
        self.bbva.write_text('{"historical_bbva":"unchanged"}')

    def run_fake(self, html=HTML):
        with patch.object(abn.read,'fetch',side_effect=[ROBOTS,(html,'text/html')]), patch.object(abn.read.time,'sleep'):
            return abn.run_spike(self.client,self.root,NOW)

    def test_html_success_stores_depth_not_raw_and_skips_processed(self):
        self.run_fake()
        record = next(iter(abn.ai.dedupe.read_json(self.root/abn.RESULTS)['items'].values()))
        self.assertEqual(record['extraction'],SEMANTICS)
        self.assertEqual(record['read_metadata']['text_source'],'article_html')
        self.assertEqual(record['read_metadata']['character_count'],len(abn.read.extract_article(HTML)))
        self.assertIn(record['url'],abn.processing.load_processed(self.root))
        payload = self.client.responses.create.call_args.kwargs
        self.assertIs(payload['text']['format']['schema'],abn.ai.SCHEMA)
        self.assertIn('ABN AMRO',payload['instructions'])
        self.assertNotIn('BBVA',payload['instructions'])
        self.assertFalse(payload['store'])
        self.assertEqual(payload['tools'],[])
        self.assertEqual(payload['reasoning'],{'effort':'low'})
        for filename in [abn.RESULTS,abn.LEDGER,abn.processing.STATE_NAME]:
            self.assertNotIn(BODY,(self.root/filename).read_text())
            self.assertNotIn(HTML,(self.root/filename).read_text())
        self.assertEqual(abn.run_spike(self.client,self.root,NOW),[])
        self.assertEqual(self.client.responses.create.call_count,1)
        self.assertEqual(self.bbva.read_text(),'{"historical_bbva":"unchanged"}')

    def test_short_listing_fallback_is_processed_with_explicit_provenance(self):
        self.run_fake('<main>No recognised body</main>')
        record=next(iter(abn.ai.dedupe.read_json(self.root/abn.RESULTS)['items'].values()))
        self.assertTrue(record['read_metadata']['listing_fallback_used'])
        self.assertEqual(record['read_metadata']['text_source'],'listing_excerpt')
        self.assertIn('listing_excerpt',self.client.responses.create.call_args.kwargs['instructions'])
        self.assertIn(record['url'],abn.processing.load_processed(self.root))

    def test_refusal_and_invalid_structure_consume_calls_without_processing(self):
        for result in [response(refusal=True),response({'summary':'invalid'})]:
            self.client.responses.create.return_value=result
            with self.assertRaises(abn.ai.SpikeError):
                self.run_fake()
        ledger=abn.load_ledger(self.root)
        self.assertEqual(abn.ai.held_calls(ledger),2)
        self.assertTrue(all(a['outcome']=='result' for a in ledger['attempts']))
        self.assertEqual(abn.processing.load_processed(self.root),{})
        self.assertFalse((self.root/abn.RESULTS).exists())

    def test_rejected_requests_release_slots_uncertain_attempts_hold_and_stop_at_three(self):
        for status in [401,429]:
            error=RuntimeError('synthetic rejection');error.status_code=status
            self.client.responses.create.side_effect=error
            with self.assertRaises(abn.ai.SpikeError):
                self.run_fake()
        self.assertEqual(abn.ai.held_calls(abn.load_ledger(self.root)),0)
        self.client.responses.create.side_effect=TimeoutError('uncertain')
        for _ in range(3):
            with self.assertRaises(abn.ai.SpikeError):
                self.run_fake()
        with patch.object(abn.read,'fetch') as fetch,self.assertRaises(abn.ai.SpikeError):
            abn.run_spike(self.client,self.root,NOW)
        fetch.assert_not_called()
        self.assertEqual(self.client.responses.create.call_count,5)
        self.assertEqual(abn.ai.held_calls(abn.load_ledger(self.root)),3)
        self.assertEqual(self.bbva.read_text(),'{"historical_bbva":"unchanged"}')

    def test_three_returned_results_exhaust_separate_allowance(self):
        self.client.responses.create.return_value=response(refusal=True)
        for _ in range(3):
            with self.assertRaises(abn.ai.SpikeError):
                self.run_fake()
        with self.assertRaises(abn.ai.SpikeError):
            self.run_fake()
        self.assertEqual(self.client.responses.create.call_count,3)

    def test_failed_output_save_never_marks_processed(self):
        original=abn.processing.write_json
        def write(data,path):
            if path.name==abn.RESULTS:
                raise OSError('synthetic')
            return original(data,path)
        with patch.object(abn.processing,'write_json',side_effect=write),self.assertRaises(OSError):
            self.run_fake()
        self.assertEqual(abn.processing.load_processed(self.root),{})
        self.assertEqual(abn.ai.held_calls(abn.load_ledger(self.root)),1)

    def test_oversized_text_stops_before_request_or_reservation(self):
        def read(items,on_text):
            on_text(items[0],'x'*8000,'article_html')
        with patch.object(abn.read,'run',side_effect=read),self.assertRaises(abn.ai.SpikeError):
            abn.run_spike(self.client,self.root,NOW)
        self.client.responses.create.assert_not_called()
        self.assertFalse((self.root/abn.LEDGER).exists())
        self.assertEqual(abn.processing.load_processed(self.root),{})

    def test_one_item_cli_and_safe_preview(self):
        client=Mock()
        client.__enter__=Mock(return_value=self.client)
        client.__exit__=Mock(return_value=False)
        with patch.object(abn.ai,'make_client',return_value=client),patch.object(abn,'run_spike',return_value=[]) as run,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(abn.main(['--live','--limit','1']),0)
        run.assert_called_once_with(self.client,limit=1,url=None)
        for limit in ['0','2','3']:
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                abn.main(['--limit',limit])
        with patch.object(abn.read,'select',return_value=[]),patch.object(abn,'load_ledger',return_value={'attempts':[]}),patch.object(abn.ai,'make_client') as make,patch.object(abn.read,'fetch') as fetch,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(abn.main([]),0)
        make.assert_not_called();fetch.assert_not_called()


    def test_exact_url_selects_beyond_default_and_follows_normal_flow(self):
        path = self.root/'abn-amro-discovery.json'
        snapshot = json.loads(path.read_text())
        original = snapshot['items'][0]
        snapshot['items'] = [dict(original, url=original['url']+str(n)) for n in range(5)]
        snapshot['item_count'] = 5
        path.write_text(json.dumps(snapshot))
        target = snapshot['items'][-1]['url']
        self.assertNotEqual(abn.read.select(self.root,NOW,1)[0]['url'],target)
        with patch.object(abn.read,'fetch',side_effect=[ROBOTS,(HTML,'text/html')]) as fetch, patch.object(abn.read.time,'sleep'):
            abn.run_spike(self.client,self.root,NOW,url=target)
        self.assertEqual(fetch.call_args.args[0],target)
        self.assertEqual(set(abn.processing.load_processed(self.root)),{target})
        self.assertEqual(set(abn.ai.dedupe.read_json(self.root/abn.RESULTS)['items']),{target})
        self.assertEqual(abn.ai.held_calls(abn.load_ledger(self.root)),1)
        with patch.object(abn.read,'fetch') as fetch, self.assertRaises(ValueError):
            abn.run_spike(self.client,self.root,NOW,url=target)
        fetch.assert_not_called()
        self.assertEqual(self.client.responses.create.call_count,1)

    def test_explicit_url_cannot_bypass_dates_or_source(self):
        path = self.root/'abn-amro-discovery.json'
        snapshot = json.loads(path.read_text())
        target = snapshot['items'][0]['url']
        for date in ['2026-09-01','2026-09-12',None]:
            snapshot['items'][0]['publication_date'] = date
            path.write_text(json.dumps(snapshot))
            with self.subTest(date=date), patch.object(abn.read,'fetch') as fetch, self.assertRaises(ValueError):
                abn.run_spike(self.client,self.root,NOW,url=target)
            fetch.assert_not_called()
        for url in ['https://example.com/unknown',target+'-missing']:
            with self.assertRaises(ValueError):
                abn.run_spike(self.client,self.root,NOW,url=url)
        self.client.responses.create.assert_not_called()
        self.assertFalse((self.root/abn.LEDGER).exists())

    def test_cli_forwards_exact_url(self):
        client=Mock()
        client.__enter__=Mock(return_value=self.client)
        client.__exit__=Mock(return_value=False)
        target='https://www.abnamro.com/research/en/our-research/invented'
        with patch.object(abn.ai,'make_client',return_value=client), patch.object(abn,'run_spike',return_value=[]) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(abn.main(['--live','--limit','1','--url',target]),0)
        run.assert_called_once_with(self.client,limit=1,url=target)


if __name__ == '__main__':
    unittest.main()
