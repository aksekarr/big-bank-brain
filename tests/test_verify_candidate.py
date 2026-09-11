"""Canonical integration contracts, with mocked responses and blocked network."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import verify_candidate as c
from test_verify_synthesis import record, verdict


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.snapshot=record()
        self.snapshot.update(generated_at='2026-09-11T00:00:00+00:00',window={
            'timezone':'Europe/London','start_date':'2026-09-05','end_date':'2026-09-11'})
        self.raw=json.dumps(self.snapshot).encode()
        (self.root/c.base.synthesis.RESULT).write_bytes(self.raw)
        self.client=Mock()
        blocker=patch('socket.socket.connect',side_effect=AssertionError('No network'))
        blocker.start();self.addCleanup(blocker.stop)

    def response(self,fail=False):
        findings=verdict('unsupported_statement' if fail else None)
        for t in findings['targets']:t['presupposition_audit']=[]
        findings['targets'][-1]['presupposition_audit']=[{
            'trigger_wording':'test wording','implied_proposition':'Test background state.',
            'references':[] if fail else ['a1:c1'],
            'support_verdict':'unsupported' if fail else 'supported'}]
        return NS(status='completed',error=None,output=[NS(type='message',status='completed',
            content=[NS(type='output_text',text=json.dumps(findings))])])

    def test_pass_then_fail_preserves_review_and_synthesis(self):
        self.client.responses.create.return_value=self.response()
        result=c.run(self.root,live=True,client=self.client)
        self.assertEqual(result['status'],'human_review_required')
        self.assertFalse(result['publication_approved'])
        previous=(self.root/c.REVIEW_RESULT).read_bytes()
        stored=json.loads(previous)
        self.assertEqual(stored['targets'][-1]['presupposition_audit'],
                         json.loads(self.response().output[0].content[0].text)['targets'][-1]['presupposition_audit'])
        self.assertEqual(stored['source_snapshot_sha256'],hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(self.client.responses.create.call_args.kwargs,c.verifier.request_payload(self.snapshot))
        with self.assertRaisesRegex(c.SafetyError, 'consumed'):
            c.run(self.root,live=True,client=self.client)
        self.raw += b'\n'
        (self.root/c.base.synthesis.RESULT).write_bytes(self.raw)
        self.client.responses.create.return_value=self.response(True)
        result=c.run(self.root,live=True,client=self.client)
        self.assertEqual(result['status'],'blocked')
        self.assertIsNone(result['review_result_file'])
        self.assertEqual((self.root/c.REVIEW_RESULT).read_bytes(),previous)
        self.assertEqual(json.loads((self.root/c.ATTEMPT_RESULT).read_text())['review_status'],'blocked')
        self.assertEqual((self.root/c.base.synthesis.RESULT).read_bytes(),self.raw)
        with self.assertRaises(ValueError):c.run(self.root,live=True,client=self.client)
        self.assertEqual(self.client.responses.create.call_count,2)
        self.assertTrue(all(a['reserved_micro_usd']==137060 for a in c.load_ledger(self.root)['attempts']))

    def test_invalid_response_and_storage_failure_preserve_previous(self):
        for mode in ('invalid','refusal','storage'):
            with self.subTest(mode=mode):
                ledger=self.root/c.LEDGER
                if ledger.exists():ledger.unlink()
                (self.root/c.REVIEW_RESULT).write_text('previous review')
                (self.root/c.ATTEMPT_RESULT).write_text('previous diagnostic')
                response=self.response()
                if mode=='invalid':response.output[0].content[0].text='bad'
                if mode=='refusal':response.output[0].content[0].type='refusal'
                self.client.responses.create.return_value=response
                write=c.base.synthesis.assembler.processing.write_json
                def store(data,path):
                    if mode=='storage' and path.name==c.REVIEW_RESULT:raise OSError('storage')
                    return write(data,path)
                with patch.object(c.base.synthesis.assembler.processing,'write_json',side_effect=store):
                    with self.assertRaises((ValueError,OSError)):c.run(self.root,live=True,client=self.client)
                self.assertEqual((self.root/c.REVIEW_RESULT).read_text(),'previous review')
                if mode!='storage':self.assertEqual((self.root/c.ATTEMPT_RESULT).read_text(),'previous diagnostic')
                self.assertEqual(c.load_ledger(self.root)['attempts'][0]['outcome'],'result')

    def test_preflight_offline_and_historical_isolation(self):
        historical = self.root/c.base.LEDGER
        historical.write_text('historical ledger must never be read or written')
        for name in (c.REVIEW_RESULT,c.ATTEMPT_RESULT):
            (self.root/name).write_text('previous')
        with patch.object(c.base.synthesis.article,'make_client') as factory:
            with patch.object(c,'MAX_REQUEST_BYTES',1):
                diagnostic=c.run(self.root)
                self.assertFalse(diagnostic['request_size_permitted'])
                self.assertFalse(diagnostic['live_eligible_ignoring_credentials'])
                self.assertGreater(diagnostic['request_bytes'],1)
                with self.assertRaisesRegex(c.SafetyError,'Request-size'):
                    c.run(self.root,live=True)
            factory.assert_not_called()
        self.assertFalse((self.root/c.LEDGER).exists())
        self.assertEqual(historical.read_text(),'historical ledger must never be read or written')
        for name in (c.REVIEW_RESULT,c.ATTEMPT_RESULT):
            self.assertEqual((self.root/name).read_text(),'previous')
        self.client.responses.create.return_value=self.response()
        c.run(self.root,live=True,client=self.client)
        self.assertEqual(historical.read_text(),'historical ledger must never be read or written')
        self.assertTrue(c.run(self.root)['snapshot_consumed'])
        with patch.object(c.base.synthesis.article,'make_client') as factory:
            with self.assertRaisesRegex(c.SafetyError,'consumed'):c.run(self.root,live=True)
            factory.assert_not_called()
        self.assertEqual((c.MAX_REQUEST_BYTES,c.RESERVE_MICRO_USD),(25000,137060))

    def test_rejections_and_uncertain_outcomes_no_retry(self):
        for status in (401,429,None):
            p=self.root/c.LEDGER
            if p.exists():p.unlink()
            for name in (c.REVIEW_RESULT,c.ATTEMPT_RESULT):
                (self.root/name).write_text('previous')
            error=TimeoutError('private')
            if status:error.status_code=status
            self.client.responses.create.side_effect=error
            with self.assertRaises(ValueError):c.run(self.root,live=True,client=self.client)
            attempt=c.load_ledger(self.root)['attempts'][0]
            self.assertEqual(attempt['outcome'],'rejected' if status else 'pending')
            for name in (c.REVIEW_RESULT,c.ATTEMPT_RESULT):
                self.assertEqual((self.root/name).read_text(),'previous')
            if status is None:
                before=self.client.responses.create.call_count
                with self.assertRaisesRegex(ValueError,'pending'):c.run(self.root,live=True,client=self.client)
                self.assertEqual(self.client.responses.create.call_count,before)
                self.assertTrue(c.run(self.root)['snapshot_pending'])
            else:
                self.assertTrue(c.run(self.root)['live_eligible_ignoring_credentials'])
        self.assertEqual(self.client.responses.create.call_count,3)

    def test_single_read_and_cli_fail_exit(self):
        original=Path.read_bytes;reads=[]
        def read(path):reads.append(path);return original(path)
        with patch.object(Path,'read_bytes',read):c.prepare(self.root)
        self.assertEqual(reads.count(self.root/c.base.synthesis.RESULT),1)
        with patch.object(c,'run',return_value={'status':'blocked'}) as run,patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(c.main(['--live','--limit','1']),1)
            run.assert_called_once_with(live=True)

    def test_rejected_snapshot_can_be_manually_attempted_again(self):
        error=RuntimeError('sensitive body');error.status_code=429
        self.client.responses.create.side_effect=error
        with self.assertRaises(c.SafetyError):c.run(self.root,live=True,client=self.client)
        self.assertEqual(self.client.responses.create.call_count,1)
        self.client.responses.create.side_effect=None
        self.client.responses.create.return_value=self.response()
        c.run(self.root,live=True,client=self.client)
        attempts=c.load_ledger(self.root)['attempts']
        self.assertEqual([a['outcome'] for a in attempts],['rejected','result'])
        self.assertTrue(all(a['source_snapshot_sha256']==hashlib.sha256(self.raw).hexdigest() for a in attempts))

    def test_missing_key_and_safe_cli_message(self):
        with patch.dict('os.environ',{},clear=True), patch.object(c.base.synthesis.article,'make_client') as factory:
            with self.assertRaisesRegex(c.SafetyError,'Missing OPENAI_API_KEY'):
                c.run(self.root,live=True)
            factory.assert_not_called()
        self.assertFalse((self.root/c.LEDGER).exists())
        with patch.object(c,'run',side_effect=c.SafetyError('Request-size blocker')), patch('sys.stderr',new_callable=io.StringIO) as err:
            self.assertEqual(c.main(['--live']),1)
            self.assertIn('Request-size blocker',err.getvalue())

    def test_malformed_ledger_fails_closed(self):
        (self.root/c.LEDGER).write_text('{bad')
        with patch.object(c.base.synthesis.article,'make_client') as factory:
            with self.assertRaisesRegex(c.SafetyError,'ledger invalid'):c.run(self.root,live=True)
            factory.assert_not_called()
        self.assertEqual((self.root/c.LEDGER).read_text(),'{bad')

    def test_canonical_size_boundary(self):
        for size in (25000,25001):
            with self.subTest(size=size):
                payload=c.verifier.request_payload(self.snapshot)
                original=len(json.dumps(payload,ensure_ascii=False).encode('utf-8'))
                payload['instructions'] += ' ' * (size-original)
                with patch.object(c.verifier,'request_payload',return_value=payload), patch.object(c.base.synthesis.article,'make_client') as factory:
                    diagnostic=c.run(self.root)
                    self.assertEqual(diagnostic['request_bytes'],size)
                    self.assertEqual(diagnostic['request_cap'],25000)
                    self.assertEqual(diagnostic['request_size_permitted'],size==25000)
                    if size>25000:
                        with self.assertRaisesRegex(c.SafetyError,'Request-size'):
                            c.run(self.root,live=True)
                    else:
                        self.client.responses.create.return_value=self.response()
                        c.run(self.root,live=True,client=self.client)
                    factory.assert_not_called()
                if size==25000:
                    self.assertEqual(c.load_ledger(self.root)['attempts'][0]['reserved_micro_usd'],137060)
                    (self.root/c.LEDGER).unlink()
                else:
                    self.assertFalse((self.root/c.LEDGER).exists())

    def test_current_real_synthesis_offline(self):
        path=c.ROOT/c.base.synthesis.RESULT
        if not path.exists():
            self.skipTest('Local synthesis runtime snapshot unavailable')
        raw=path.read_bytes()
        # Keep the test independent of local accounting and never invoke live mode.
        (self.root/c.base.synthesis.RESULT).write_bytes(raw)
        with patch.object(c.base.synthesis.article,'make_client') as factory:
            diagnostic=c.run(self.root)
            factory.assert_not_called()
        self.assertEqual(diagnostic['request_bytes'],23765)
        self.assertEqual(diagnostic['request_cap'],25000)
        self.assertTrue(diagnostic['request_size_permitted'])
        self.assertEqual(diagnostic['reserved_micro_usd'],137060)
        self.assertTrue(diagnostic['live_eligible_ignoring_credentials'])
        self.assertFalse(diagnostic['publication_approved'])
        self.assertFalse((self.root/c.LEDGER).exists())
        self.assertFalse((self.root/c.ATTEMPT_RESULT).exists())
        self.assertFalse((self.root/c.REVIEW_RESULT).exists())

    def test_safe_response_failure_diagnostics(self):
        for kind in ('refusal','incomplete','invalid_json','validation_failed_unclassified',
                     'unexpected_internal_error'):
            with self.subTest(kind=kind):
                (self.root/c.LEDGER).unlink(missing_ok=True)
                for name in (c.REVIEW_RESULT,c.ATTEMPT_RESULT):
                    (self.root/name).write_text('previous valid output')
                response=self.response()
                if kind=='refusal':
                    response.output[0].content=[NS(type='refusal',refusal='PRIVATE RESPONSE')]
                elif kind=='incomplete':
                    response.status='incomplete'
                elif kind=='invalid_json':
                    response.output[0].content[0].text='PRIVATE INVALID JSON'
                elif kind=='validation_failed_unclassified':
                    response.output[0].content[0].text='{"private":"PRIVATE OUTPUT"}'
                self.client.responses.create.return_value=response
                parser=c.verifier.parse_response
                def parse(*args):
                    if kind=='unexpected_internal_error':
                        raise RuntimeError('PRIVATE INTERNAL ERROR')
                    return parser(*args)
                with patch.object(c.verifier,'parse_response',side_effect=parse):
                    with self.assertRaisesRegex(c.SafetyError,kind):
                        c.run(self.root,live=True,client=self.client)
                attempt=c.load_ledger(self.root)['attempts'][0]
                self.assertEqual(attempt['diagnostic_code'],kind)
                self.assertEqual(attempt['diagnostic_stage'],'validation')
                self.assertTrue(attempt['response_returned'])
                self.assertEqual(attempt['outcome'],'result')
                self.assertIn('diagnostic_at',attempt)
                self.assertNotIn('PRIVATE',(self.root/c.LEDGER).read_text())
                before=self.client.responses.create.call_count
                with self.assertRaisesRegex(c.SafetyError,'consumed'):
                    c.run(self.root,live=True,client=self.client)
                self.assertEqual(self.client.responses.create.call_count,before)
                for name in (c.REVIEW_RESULT,c.ATTEMPT_RESULT):
                    self.assertEqual((self.root/name).read_text(),'previous valid output')

    def test_success_diagnostic_and_safe_status(self):
        self.client.responses.create.return_value=self.response()
        c.run(self.root,live=True,client=self.client)
        attempt=c.load_ledger(self.root)['attempts'][0]
        self.assertEqual(attempt['diagnostic_code'],'stored')
        self.assertEqual(attempt['response_status'],'completed')
        self.assertTrue(attempt['response_returned'])
        (self.root/c.LEDGER).unlink()
        response=self.response();response.status='PRIVATE STATUS'
        self.client.responses.create.return_value=response
        with self.assertRaises(c.SafetyError):c.run(self.root,live=True,client=self.client)
        attempt=c.load_ledger(self.root)['attempts'][0]
        self.assertEqual(attempt['response_status'],'unknown')
        self.assertEqual(attempt['diagnostic_code'],'validation_failed_unclassified')
        self.assertNotIn('PRIVATE',(self.root/c.LEDGER).read_text())
