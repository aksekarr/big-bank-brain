"""Comparison controls with synthetic responses and network blocked."""
import os
import hashlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import compare_abn_heldout as c

class ABNHeldoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        env=patch.dict(os.environ,{'OPENAI_API_KEY':'offline-test-only'})
        env.start();self.addCleanup(env.stop)
        git=patch.object(c,'git_context',return_value={'git_checkpoint':'a'*40,'git_dirty':False})
        self.git=git.start();self.addCleanup(git.stop)
        self.client=Mock()
        self.client.responses.create.side_effect=self.response
        p=patch('socket.socket.connect',side_effect=AssertionError('No network'))
        p.start();self.addCleanup(p.stop)

    def response(self,**payload):
        data=json.loads(payload['input'][0]['content'])
        targets=[{'target_ref':t['target_ref'],'verdict':'pass','issues':[]} for t in data['synthesis']['targets']]
        if 'presupposition_audit' in payload['text']['format']['schema']['properties']['targets']['items']['properties']:
            for t in targets:t['presupposition_audit']=[]
            targets[-1]['presupposition_audit']=[{'trigger_wording':'synthetic wording',
                'implied_proposition':'Synthetic assessment for transport test.',
                'references':['a4:c4'],'support_verdict':'supported'}]
        return NS(status='completed',error=None,output=[NS(type='message',status='completed',
            content=[NS(type='output_text',text=json.dumps({'verdict':'pass','targets':targets}))])])

    def run_case(self,variant='frozen',case='abn_clean',root=None):
        return c.run(variant,case,root=root or self.root,client=self.client,live=True)

    def test_all_four_isolated_one_shot_and_requests(self):
        protected=['presupposition-comparison-ledger.json',
                   'presupposition-comparison-frozen-retain_supported-r1.json',
                   'presupposition-comparison-experimental-again_supported-r1.json',
                   'verification-spike-ledger.json','verification-result.json','verification-result-2.json',
                   'verifier-eval-ledger.json','verifier-eval-v2-ledger.json','synthesis-result.json']
        protected += [f'verifier-eval-{case}-r1.json' for case in ('clean','turkiye_conditionality','argentina_overview','bis_reference_support','ecb_despite','abn_retain','harmless_paraphrase')]
        protected += [f'verifier-eval-v2-{case}-r1.json' for case in ('clean','harmless_paraphrase','abn_retain')]
        for name in protected:(self.root/name).write_text('untouched')
        original=Path.open
        def guarded(path,*args,**kwargs):
            if path in [self.root/n for n in protected]:raise AssertionError('Protected runtime accessed')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',guarded):
            for variant in c.VARIANTS:
                for case in c.CASES:
                    record,payload,meta=c.prepare(variant,case)
                    self.assertEqual(payload,c.VARIANTS[variant].request_payload(record))
                    for field in ('expected_verdict','implied_prior_state','category','rationale'):
                        self.assertNotIn(field,payload['input'][0]['content'])
                    self.assertLessEqual(meta['request_bytes'],20000)
                    self.run_case(variant,case)
                    with self.assertRaises(ValueError):self.run_case(variant,case)
                    result=json.loads((self.root/f'abn-heldout-comparison-{variant}-{case}-r1.json').read_text())
                    self.assertEqual(result['fixture_sha256'],meta['fixture_sha256'])
                    self.assertEqual(result['variant'],variant)
                    self.assertEqual(result['campaign'],'abn-heldout')
                    if variant=='experimental':self.assertTrue(result['findings']['targets'][-1]['presupposition_audit'])
        ledger,attempted,held=c.load_ledger(self.root)
        self.assertEqual((len(attempted),held,self.client.responses.create.call_count),(4,4,4))
        self.assertEqual(sum(a['reserved_micro_usd'] for a in ledger['attempts']),498240)
        for n in protected:self.assertEqual((self.root/n).read_text(),'untouched')
        # Independent aggregate branch, excluding duplicate-key short circuit.
        with patch.object(c,'load_ledger',return_value=(ledger,{('x',str(i)) for i in range(4)},4)):
            with self.assertRaises(ValueError):self.run_case()
        with self.assertRaises(ValueError):self.run_case('other')
        self.assertEqual(self.client.responses.create.call_count,4)

    def test_identity_and_settings_before_client(self):
        original=Path.read_bytes
        paths=[c.FIXTURES/'manifest.json',c.FIXTURES/'abn_clean.json',
               c.ROOT/'scripts/verify_synthesis.py',c.ROOT/'scripts/verify_presuppositions.py']
        with patch.object(c.base.synthesis.article,'make_client') as client:
            for changed in paths:
                def read(path):return original(path)+(b' ' if path==changed else b'')
                with patch.object(Path,'read_bytes',read):
                    with self.assertRaisesRegex(ValueError,'identity mismatch'):
                        c.run('frozen','abn_clean',root=self.root,live=True)
            for variant,case in [('wrong','abn_clean'),('frozen','wrong')]:
                with self.assertRaises(ValueError):c.run(variant,case,root=self.root,live=True)
            record,payload,_=c.prepare('frozen','abn_clean')
            for key,value in [('instructions','x'*20001),('model','wrong')]:
                bad=copy.deepcopy(payload);bad[key]=value
                with patch.object(c.base,'request_payload',return_value=bad):
                    with self.assertRaises(ValueError):c.run('frozen','abn_clean',root=self.root,live=True)
            client.assert_not_called()
        self.assertFalse((self.root/c.LEDGER).exists())

    def test_single_fixture_read_and_diagnostics(self):
        original=Path.read_bytes;reads=[]
        def read(path):reads.append(path);return original(path)
        with patch.object(Path,'read_bytes',read), patch.object(c.base.synthesis.article,'make_client') as client:
            result=c.run('experimental','abn_clean',root=self.root)
            client.assert_not_called()
        self.assertEqual(reads.count(c.FIXTURES/'abn_clean.json'),1)
        self.assertEqual(result['mode'],'offline')
        self.assertEqual(list(self.root.iterdir()),[])

    def test_all_failures_close_combination(self):
        scenarios=[('401','rejected','request_rejected'),('429','rejected','request_rejected'),
                   ('timeout','pending','send_uncertain'),('invalid','result','validation_failed'),
                   ('incomplete','result','validation_failed'),('refusal','result','validation_failed'),
                   ('storage','result','storage_failed')]
        for kind,outcome,stage in scenarios:
            root=self.root/kind;root.mkdir()
            self.client.responses.create.side_effect=self.response
            if kind in ('401','429','timeout'):
                error=TimeoutError('private diagnostic')
                if kind!='timeout':error.status_code=int(kind)
                self.client.responses.create.side_effect=error
            elif kind in ('invalid','incomplete','refusal'):
                response=self.response(**c.prepare('frozen','abn_clean')[1])
                if kind=='incomplete':response.status='incomplete'
                elif kind=='refusal':response.output[0].content[0].type='refusal'
                else:response.output[0].content[0].text='RAW_INVALID_SENTINEL'
                self.client.responses.create.side_effect=None
                self.client.responses.create.return_value=response
            with patch.object(c.base,'save_new_result',side_effect=OSError('storage')):
                with self.assertRaises((ValueError,OSError)):self.run_case(root=root)
            ledger,attempted,held=c.load_ledger(root);a=ledger['attempts'][0]
            self.assertEqual((a['outcome'],a['stage']),(outcome,stage))
            self.assertEqual(held,0 if outcome=='rejected' else 1)
            self.assertEqual(a['attempt_eligibility'],'closed')
            with self.assertRaises(ValueError):self.run_case(root=root)
            self.assertNotIn('RAW_INVALID_SENTINEL',(root/c.LEDGER).read_text())
        self.assertEqual(self.client.responses.create.call_count,7)

    def test_nonoverwrite_and_budget(self):
        p=self.root/'abn-heldout-comparison-frozen-abn_clean-r1.json';p.write_text('preserve')
        with self.assertRaises(ValueError):self.run_case()
        self.assertEqual(p.read_text(),'preserve')
        with self.assertRaises(FileExistsError):c.base.save_new_result({},p)
        with patch.object(c,'BUDGET',124559):
            with self.assertRaises(ValueError):self.run_case(case='abn_retaining')
        self.client.responses.create.assert_not_called()

    def test_cli_requires_one_explicit_combination(self):
        for args in ([], ['--variant','frozen'], ['--variant','frozen','--case','abn_clean','--limit','2']):
            with self.assertRaises(SystemExit):c.main(args)
        with patch.object(c,'run',return_value={'mode':'offline'}) as run, patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(c.main(['--variant','experimental','--case','abn_retaining']),0)
            run.assert_called_once_with('experimental','abn_retaining',live=False)

    def test_missing_key_before_client_or_reservation(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(c.base.synthesis.article,'make_client') as client:
            with self.assertRaisesRegex(ValueError,'OPENAI_API_KEY unavailable'):
                c.run('frozen','abn_clean',root=self.root,live=True)
            client.assert_not_called()
        self.assertEqual(list(self.root.iterdir()),[])

    def test_pair_isolation_and_model_input(self):
        clean,fp,_=c.prepare('frozen','abn_clean')
        mutated,ep,_=c.prepare('experimental','abn_retaining')
        original=copy.deepcopy(mutated)
        mutated['synthesis']['themes'][2]['points'][0]['text']=clean['synthesis']['themes'][2]['points'][0]['text']
        self.assertEqual(clean,mutated)
        self.assertEqual(original['synthesis']['themes'][2]['points'][0]['text'],
                         clean['synthesis']['themes'][2]['points'][0]['text'].replace('Republican Senate','Republicans retaining the Senate'))
        for case in c.CASES:
            record,payload,meta=c.prepare('experimental',case)
            data=json.loads(payload['input'][0]['content'])
            point=next(t for t in data['synthesis']['targets'] if t['target_ref']=='t3:p1')
            self.assertEqual(point['references'],['a4:c1','a4:c2','a4:c3'])
            for label in ('abn_clean','abn_retaining','expected_verdict','primary_target','evaluation_rule'):
                self.assertNotIn(label,payload['input'][0]['content'])
        self.assertEqual(c.MAX_CALLS,4)
        self.assertEqual(c.RESERVATION,124560)
        self.assertEqual(c.BUDGET,498240)

    def test_reservation_precedes_send_and_client_has_no_retries(self):
        def send(**payload):
            ledger,attempted,held=c.load_ledger(self.root)
            self.assertEqual(attempted,{('frozen','abn_clean')})
            self.assertEqual(ledger['attempts'][0]['stage'],'send_pending')
            self.assertEqual(held,1)
            return self.response(**payload)
        self.client.responses.create.side_effect=send
        self.run_case()
        self.client.responses.create.assert_called_once()
        sdk=NS(OpenAI=Mock(),DefaultHttpxClient=Mock())
        with patch.dict(sys.modules,{'openai':sdk}), patch.dict(os.environ,{'OPENAI_LOG':''}):
            c.base.synthesis.article.make_client()
        self.assertEqual(sdk.OpenAI.call_args.kwargs['max_retries'],0)

    def test_live_requires_known_clean_git_before_side_effects(self):
        states=[({'git_checkpoint':'a'*40,'git_dirty':True},'Clean Git'),
                ({'git_checkpoint':None,'git_dirty':False},'Known Git'),
                ({'git_checkpoint':'','git_dirty':False},'Known Git'),
                ({'git_checkpoint':'a'*40,'git_dirty':None},'Known Git'),
                ({'git_checkpoint':None,'git_dirty':None},'Known Git')]
        for state,message in states:
            with self.subTest(state=state):
                self.git.return_value=state
                with patch.object(c.base.synthesis.article,'make_client') as factory:
                    with self.assertRaisesRegex(ValueError,message):
                        c.run('frozen','abn_clean',root=self.root,live=True)
                    factory.assert_not_called()
                with self.assertRaisesRegex(ValueError,message):self.run_case()
                self.client.responses.create.assert_not_called()
                self.assertEqual(list(self.root.iterdir()),[])
                self.assertEqual(c.run('frozen','abn_clean',root=self.root)['mode'],'offline')

    def test_clean_git_and_runner_fingerprint_provenance(self):
        self.run_case()
        ledger,_,_=c.load_ledger(self.root)
        result=json.loads((self.root/'abn-heldout-comparison-frozen-abn_clean-r1.json').read_text())
        runner=Path(c.__file__)
        expected=hashlib.sha256(runner.read_bytes()).hexdigest()
        for record in (ledger['attempts'][0],result):
            self.assertEqual(record['git_checkpoint'],'a'*40)
            self.assertIs(record['git_dirty'],False)
            self.assertEqual(record['code_sha256']['compare_abn_heldout.py'],expected)
        self.client.responses.create.assert_called_once()
        original=Path.read_bytes
        changed=original(runner)+b'\n# supplied offline test bytes\n'
        def read(path):return changed if path==runner else original(path)
        with patch.object(Path,'read_bytes',read):
            _,_,provenance=c.prepare('frozen','abn_clean')
        self.assertEqual(provenance['code_sha256']['compare_abn_heldout.py'],hashlib.sha256(changed).hexdigest())
        self.assertNotEqual(provenance['code_sha256']['compare_abn_heldout.py'],expected)
