"""Mocked evaluation execution; no live requests."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import evaluate_verifier as e

class EvaluationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.client=Mock()
        blocker=patch('socket.socket.connect',side_effect=AssertionError('Network forbidden'))
        blocker.start();self.addCleanup(blocker.stop)
        self.client.responses.create.side_effect=self.response

    def response(self,**payload):
        data=json.loads(payload['input'][0]['content'])
        findings={'verdict':'pass','targets':[{'target_ref':t['target_ref'],'verdict':'pass','issues':[]} for t in data['synthesis']['targets']]}
        return NS(status='completed',error=None,output=[NS(type='message',status='completed',content=[NS(type='output_text',text=json.dumps(findings))])])

    def run_case(self,case='clean'):
        return e.run(case,root=self.root,client=self.client)

    def test_seven_slots_and_production_isolation(self):
        names=['verification-spike-ledger.json','verification-result.json','verification-result-2.json']
        for name in names:(self.root/name).write_text('untouched')
        for case in e.CASES:
            self.run_case(case)
            p=self.root/f'verifier-eval-{case}-r1.json'
            self.assertTrue(p.exists())
            self.assertEqual(json.loads(p.read_text())['repetition'],'r1')
            with self.assertRaises(ValueError):self.run_case(case)
        self.assertEqual(self.client.responses.create.call_count,7)
        ledger,held=e.load_ledger(self.root)
        self.assertEqual(len(held),7)
        self.assertEqual(sum(a['reserved_micro_usd'] for a in ledger['attempts']),871920)
        self.assertTrue(all(a['stage'] == 'stored' for a in ledger['attempts']))
        for name in names:self.assertEqual((self.root/name).read_text(),'untouched')

    def test_all_fixture_sizes_and_frozen_request(self):
        for case in e.CASES:
            record,payload,_,size=e.load_case(case)
            self.assertLessEqual(size,20000)
            self.assertEqual(payload,e.verifier.request_payload(record))
            self.assertEqual(payload['reasoning'],{'effort':'medium'})
            self.assertEqual(payload['max_output_tokens'],6000)
            for label in ['expected_overall','mutation','rationale','category','case_id']:
                self.assertNotIn(label,payload['input'][0]['content'])

    def test_unknown_case_and_hash_mismatch(self):
        with self.assertRaises(ValueError):self.run_case('../clean')
        import shutil
        fixtures=self.root/'fixtures';shutil.copytree(e.FIXTURES,fixtures)
        with (fixtures/'clean.json').open('a') as f:f.write(' ')
        with self.assertRaisesRegex(ValueError,'hash'):
            e.run('clean',root=self.root,fixtures=fixtures,client=self.client)
        self.client.responses.create.assert_not_called()
        self.assertFalse((self.root/e.LEDGER).exists())

    def test_refused_invalid_pending_consume_slot(self):
        for case,response in zip(e.CASES,[NS(status='incomplete',error=None),
                NS(status='completed',error=None,output=[NS(type='message',status='completed',content=[NS(type='refusal')])]),
                NS(status='completed',error=None,output=[NS(type='message',status='completed',content=[NS(type='output_text',text='RAW_RESPONSE_SENTINEL')])])]):
            self.client.responses.create.side_effect=None
            self.client.responses.create.return_value=response
            with self.assertRaises(ValueError):self.run_case(case)
            with self.assertRaises(ValueError):self.run_case(case)
        self.client.responses.create.side_effect=TimeoutError()
        with self.assertRaises(ValueError):self.run_case('ecb_despite')
        with self.assertRaises(ValueError):self.run_case('ecb_despite')
        self.assertEqual(self.client.responses.create.call_count,4)
        ledger,_=e.load_ledger(self.root)
        self.assertEqual([a['outcome'] for a in ledger['attempts']],['result']*3+['pending'])
        self.assertEqual([a['stage'] for a in ledger['attempts']],
                         ['validation_failed']*3+['send_uncertain'])
        self.assertNotIn('RAW_RESPONSE_SENTINEL', (self.root/e.LEDGER).read_text())
        for attempt in ledger['attempts']:
            for key in ['fixture_sha256', 'prompt_sha256', 'verifier_code_sha256',
                        'repo_checkpoint', 'model', 'reasoning', 'request_bytes']:
                self.assertIn(key, attempt)

    def test_rejection_audited_no_automatic_retry(self):
        for code in [401,429]:
            err=Exception();err.status_code=code
            self.client.responses.create.side_effect=err
            with self.assertRaises(ValueError):self.run_case()
        self.assertEqual(self.client.responses.create.call_count,2)
        ledger, held = e.load_ledger(self.root)
        self.assertEqual(held,set())
        self.assertEqual([a['outcome'] for a in ledger['attempts']], ['rejected']*2)
        self.assertEqual([a['stage'] for a in ledger['attempts']], ['request_rejected']*2)

    def test_existing_output_and_storage_failure(self):
        p=self.root/'verifier-eval-clean-r1.json';p.write_text('preserve')
        with self.assertRaises(ValueError):self.run_case()
        self.client.responses.create.assert_not_called()
        with self.assertRaises(FileExistsError):e.verifier.save_new_result({},p)
        self.assertEqual(p.read_text(),'preserve')
        with patch.object(e.verifier,'save_new_result',side_effect=OSError()):
            with self.assertRaises(OSError):self.run_case('abn_retain')
        with self.assertRaises(ValueError):self.run_case('abn_retain')
        attempt = e.load_ledger(self.root)[0]['attempts'][-1]
        self.assertEqual((attempt['outcome'], attempt['stage']), ('result', 'storage_failed'))
        self.assertIn('prompt_sha256', attempt)

    def test_budget_and_cli_limits(self):
        with patch.object(e,'BUDGET',124559):
            with self.assertRaises(ValueError):self.run_case()
        self.client.responses.create.assert_not_called()
        with self.assertRaises(SystemExit):e.main(['--case','clean','--live','--limit','2'])

    def test_provenance_captured_before_send_and_reused(self):
        def send(**payload):
            attempt = e.load_ledger(self.root)[0]['attempts'][0]
            self.assertEqual(attempt['stage'], 'send_pending')
            self.assertEqual(attempt['repo_checkpoint'], 'checkpoint-before-send')
            self.assertEqual(attempt['model'], payload['model'])
            self.assertEqual(attempt['reasoning'], payload['reasoning']['effort'])
            return self.response(**payload)
        self.client.responses.create.side_effect = send
        with patch.object(e, 'checkpoint', return_value='checkpoint-before-send') as checkpoint:
            self.run_case()
            checkpoint.assert_called_once_with()
        attempt = e.load_ledger(self.root)[0]['attempts'][0]
        result = json.loads((self.root/'verifier-eval-clean-r1.json').read_text())
        for key in ['case_id', 'repetition', 'fixture_sha256', 'prompt_sha256',
                    'verifier_code_sha256', 'repo_checkpoint', 'model', 'reasoning', 'request_bytes']:
            self.assertEqual(attempt[key], result[key])

    def test_optional_git_checkpoint(self):
        with patch.object(e.subprocess, 'check_output', side_effect=OSError()):
            self.run_case()
        self.assertIsNone(e.load_ledger(self.root)[0]['attempts'][0]['repo_checkpoint'])

    def test_aggregate_branch_independent_of_case_block(self):
        # Isolate the aggregate guard: seven held slots, selected case not held.
        # Real ledgers additionally enforce the seven fixed IDs and per-case cap.
        held = {str(i) for i in range(7)}
        with patch.object(e, 'load_ledger', return_value=({'version': 1, 'attempts': []}, held)):
            with self.assertRaisesRegex(ValueError, 'allowance exhausted'):
                self.run_case()
        self.client.responses.create.assert_not_called()
        self.assertFalse((self.root/e.LEDGER).exists())
