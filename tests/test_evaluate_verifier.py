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

    def test_v2_three_cases_isolation_provenance_and_cap(self):
        protected = ['verification-spike-ledger.json', 'verification-result.json',
                     'verification-result-2.json', e.LEDGER]
        protected += [f'verifier-eval-{case}-r1.json' for case in e.CASES]
        for name in protected:
            (self.root/name).write_text('untouched')
        original_read = Path.open
        def guarded_open(path, *args, **kwargs):
            if path in [self.root/name for name in protected]:
                raise AssertionError('Production/Campaign 1 state accessed')
            return original_read(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded_open):
            for case in ('clean', 'harmless_paraphrase', 'abn_retain'):
                record, payload, fingerprint, size = e.load_case(case, campaign='v2')
                self.assertIn('BBVA’s rate outlooks depend', record['synthesis']['headline'])
                self.assertEqual(payload, e.verifier.request_payload(record))
                self.assertLessEqual(size, 20000)
                self.assertNotIn('expected_overall', payload['input'][0]['content'])
                e.run(case, root=self.root, client=self.client, campaign='v2')
                result = json.loads((self.root/f'verifier-eval-v2-{case}-r1.json').read_text())
                self.assertEqual(result['campaign'], 'v2')
                self.assertEqual(result['fixture_sha256'], fingerprint)
                with self.assertRaises(ValueError):
                    e.run(case, root=self.root, client=self.client, campaign='v2')
        ledger, held = e.load_ledger(self.root, 'v2')
        self.assertEqual(len(held), 3)
        self.assertEqual(sum(a['reserved_micro_usd'] for a in ledger['attempts']), 373680)
        self.assertTrue(all(a['stage'] == 'stored' and a['campaign'] == 'v2' for a in ledger['attempts']))
        self.assertEqual(self.client.responses.create.call_count, 3)
        for name in protected:
            self.assertEqual((self.root/name).read_text(), 'untouched')
        # Exercise the aggregate branch independently of duplicate-case blocking.
        with patch.object(e, 'load_ledger', return_value=(ledger, {'x','y','z'})):
            with self.assertRaises(ValueError):
                e.run('clean', root=self.root, client=self.client, campaign='v2')
        self.assertEqual(self.client.responses.create.call_count, 3)

    def test_v2_disallowed_hash_and_existing_output_preflight(self):
        import shutil
        with patch.object(e.verifier.synthesis.article, 'make_client') as make_client:
            for case in set(e.CASES) - {'clean', 'harmless_paraphrase', 'abn_retain'}:
                with self.assertRaises(ValueError):
                    e.run(case, root=self.root, campaign='v2')
            fixtures = self.root/'v2-fixtures'
            shutil.copytree(e.campaign_settings('v2')[0], fixtures)
            with (fixtures/'clean.json').open('a') as f:
                f.write(' ')
            with self.assertRaisesRegex(ValueError, 'hash'):
                e.run('clean', root=self.root, fixtures=fixtures, campaign='v2')
            destination = self.root/'verifier-eval-v2-clean-r1.json'
            destination.write_text('preserve')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                e.run('clean', root=self.root, campaign='v2')
            make_client.assert_not_called()
            self.assertEqual(destination.read_text(), 'preserve')
            self.assertFalse((self.root/'verifier-eval-v2-ledger.json').exists())

    def test_v2_failure_audit_and_blocking(self):
        scenarios = [(NS(status='incomplete', error=None), 'result', 'validation_failed'),
                     (TimeoutError(), 'pending', 'send_uncertain'),
                     (None, 'result', 'storage_failed')]
        for index, (response, outcome, stage) in enumerate(scenarios):
            root = self.root/str(index)
            root.mkdir()
            if response is None:
                self.client.responses.create.side_effect = self.response
            elif isinstance(response, Exception):
                self.client.responses.create.side_effect = response
            else:
                self.client.responses.create.side_effect = None
                self.client.responses.create.return_value = response
            with patch.object(e.verifier, 'save_new_result', side_effect=OSError('storage')):
                with self.assertRaises((ValueError, OSError)):
                    e.run('clean', root=root, client=self.client, campaign='v2')
            attempt = e.load_ledger(root, 'v2')[0]['attempts'][0]
            self.assertEqual((attempt['outcome'], attempt['stage']), (outcome, stage))
            self.assertEqual(attempt['campaign'], 'v2')
            with self.assertRaises(ValueError):
                e.run('clean', root=root, client=self.client, campaign='v2')
        self.assertEqual(self.client.responses.create.call_count, 3)

    def test_campaign_cli_routing(self):
        with patch.object(e, 'run', return_value={}) as run:
            self.assertEqual(e.main(['--campaign','v2','--case','clean','--live','--limit','1']), 0)
            run.assert_called_once_with('clean', campaign='v2')
        with patch.object(e, 'run', return_value={}) as run:
            self.assertEqual(e.main(['--case','clean','--live','--limit','1']), 0)
            run.assert_called_once_with('clean', campaign='v1')
        with self.assertRaises(SystemExit):
            e.main(['--campaign','v3','--case','clean'])
