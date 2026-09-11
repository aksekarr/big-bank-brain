"""Canonical synthesis accounting/storage with synthetic records and no network."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import synthesise_candidate as c
from test_extract_bbva import NOW, SEMANTICS
from test_synthesise import output


def response(kind='valid'):
    content=NS(type='output_text',text=json.dumps(output()))
    status='completed'
    if kind=='refusal':content=NS(type='refusal',refusal='PRIVATE')
    if kind=='invalid_json':content.text='PRIVATE'
    if kind=='validation_failed_unclassified':content.text='{}'
    if kind=='incomplete':status='incomplete'
    return NS(status=status,error=None,output=[NS(type='message',status='completed',content=[content])])


class CanonicalSynthesisTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.records={}
        for n in (1,2):
            url=f'https://www.bbvaresearch.com/en/publicaciones/test{n}/'
            self.records[url]=dict(institution='BBVA Research',source_name='BBVA Research',
                title=f'Test {n}',url=url,publication_date='2026-09-10',extraction=SEMANTICS)
        self.save_sources()
        (self.root/'processed-state.json').write_text(json.dumps({'version':1,'processed':{
            url:NOW.isoformat() for url in self.records}}))
        self.client=NS(responses=NS(create=Mock(return_value=response())))
        blocker=patch('socket.socket.connect',side_effect=AssertionError('No network'))
        blocker.start();self.addCleanup(blocker.stop)
        (self.root/c.synthesis.LEDGER).write_text('HISTORICAL UNTOUCHED')

    def save_sources(self):
        (self.root/'bbva-ai-extractions.json').write_text(json.dumps({'version':1,'items':self.records}))

    def run_fake(self):
        return c.run(self.root,live=True,client=self.client,now=NOW)

    def test_success_identity_repeat_and_new_input(self):
        before=c.run(self.root,now=NOW)
        self.assertTrue(before['live_eligible_ignoring_credentials'])
        self.assertFalse((self.root/c.LEDGER).exists())
        self.run_fake()
        payload=self.client.responses.create.call_args.kwargs
        sha=hashlib.sha256(payload['input'][0]['content'].encode()).hexdigest()
        stored=json.loads((self.root/c.synthesis.RESULT).read_text())
        self.assertEqual(stored['source_input_sha256'],sha)
        self.assertEqual(before['source_input_sha256'],sha)
        attempt=c.load_ledger(self.root)['attempts'][0]
        self.assertEqual(attempt['diagnostic_code'],'stored')
        self.assertEqual(attempt['stored_result_sha256'],hashlib.sha256(
            (self.root/c.synthesis.RESULT).read_bytes()).hexdigest())
        self.assertNotIn('stored_result_sha256',stored)
        self.assertEqual(attempt['reserved_micro_usd'],100560)
        with patch.object(c.synthesis.article,'make_client') as factory:
            with self.assertRaisesRegex(c.SafetyError,'consumed'):c.run(self.root,live=True,now=NOW)
            factory.assert_not_called()
        self.records[next(iter(self.records))]['extraction']=dict(SEMANTICS,claims=['A changed finding.'])
        self.save_sources()
        self.assertTrue(c.run(self.root,now=NOW)['live_eligible_ignoring_credentials'])
        self.run_fake()
        self.assertEqual(len(c.load_ledger(self.root)['attempts']),2)
        self.assertEqual((self.root/c.synthesis.LEDGER).read_text(),'HISTORICAL UNTOUCHED')

    def test_returned_failures_preserve_and_consume(self):
        for kind in ('refusal','incomplete','invalid_json','validation_failed_unclassified'):
            with self.subTest(kind=kind):
                (self.root/c.LEDGER).unlink(missing_ok=True)
                (self.root/c.synthesis.RESULT).write_text('previous good')
                self.client.responses.create.return_value=response(kind)
                with self.assertRaises(c.SafetyError):self.run_fake()
                attempt=c.load_ledger(self.root)['attempts'][0]
                self.assertEqual(attempt['outcome'],'result')
                self.assertEqual(attempt['diagnostic_code'],kind)
                self.assertNotIn('PRIVATE',(self.root/c.LEDGER).read_text())
                calls=self.client.responses.create.call_count
                with self.assertRaisesRegex(c.SafetyError,'consumed'):self.run_fake()
                self.assertEqual(self.client.responses.create.call_count,calls)
                self.assertEqual((self.root/c.synthesis.RESULT).read_text(),'previous good')

    def test_network_and_rejected_accounting(self):
        for status in (401,429,None):
            (self.root/c.LEDGER).unlink(missing_ok=True)
            (self.root/c.synthesis.RESULT).write_text('previous good')
            error=RuntimeError('PRIVATE')
            if status:error.status_code=status
            self.client.responses.create.side_effect=error
            with self.assertRaises(c.SafetyError):self.run_fake()
            d=c.run(self.root,now=NOW)
            self.assertEqual(d['live_eligible_ignoring_credentials'],status is not None)
            self.assertEqual(d['snapshot_pending'],status is None)
            self.assertEqual((self.root/c.synthesis.RESULT).read_text(),'previous good')
            if status is None:
                with self.assertRaisesRegex(c.SafetyError,'pending'):self.run_fake()
            else:
                self.client.responses.create.side_effect=None
                self.run_fake()
                self.assertEqual([a['outcome'] for a in c.load_ledger(self.root)['attempts']],['rejected','result'])

    def test_preflight_offline_size_empty_credentials_and_invalid_state(self):
        (self.root/c.synthesis.RESULT).write_text('previous good')
        with patch.object(c.synthesis.article,'make_client') as factory:
            with patch.object(c,'MAX_REQUEST_BYTES',1):
                d=c.run(self.root,now=NOW)
                self.assertFalse(d['request_size_permitted'])
                with self.assertRaisesRegex(c.SafetyError,'request-size'):c.run(self.root,live=True,now=NOW)
            with patch.dict('os.environ',{},clear=True):
                with self.assertRaisesRegex(c.SafetyError,'Missing'):c.run(self.root,live=True,now=NOW)
            (self.root/'processed-state.json').write_text('{broken')
            with self.assertRaises(ValueError):c.run(self.root,live=True,now=NOW)
            (self.root/'processed-state.json').write_text('{"version":1,"processed":{}}')
            self.assertIn('No valid articles to synthesise',c.run(self.root,now=NOW)['blockers'])
            factory.assert_not_called()
        self.assertFalse((self.root/c.LEDGER).exists())
        self.assertEqual((self.root/c.synthesis.RESULT).read_text(),'previous good')

    def test_storage_failure_preserves_previous(self):
        (self.root/c.synthesis.RESULT).write_text('previous good')
        write=c.synthesis.assembler.processing.write_json
        def store(data,path):
            if path.name==c.synthesis.RESULT:raise OSError('PRIVATE')
            return write(data,path)
        with patch.object(c.synthesis.assembler.processing,'write_json',side_effect=store):
            with self.assertRaisesRegex(c.SafetyError,'storage'):self.run_fake()
        self.assertEqual((self.root/c.synthesis.RESULT).read_text(),'previous good')
        self.assertEqual(c.load_ledger(self.root)['attempts'][0]['diagnostic_code'],'storage_failed')

    def test_settings_and_cli(self):
        packet,payload,provenance=c.prepare(self.root,NOW)
        self.assertEqual(payload,c.synthesis.request_payload(packet))
        self.assertEqual((c.MAX_REQUEST_BYTES,c.RESERVE_MICRO_USD),(20000,100560))
        self.assertEqual(provenance['max_output_tokens'],4000)
        with patch.object(c,'run',return_value={}) as run:
            self.assertEqual(c.main([]),0)
            run.assert_called_once_with(live=False)
        with self.assertRaises(SystemExit):c.main(['--limit','2'])
