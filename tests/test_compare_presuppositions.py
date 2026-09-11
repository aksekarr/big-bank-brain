"""Comparison controls with synthetic responses and network blocked."""
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
import compare_presuppositions as c

class ComparisonTests(unittest.TestCase):
    def setUp(self):
        # Historical runners intentionally retain old production pins. Transport
        # tests use a test-only identity for the instrumented, parity-tested module.
        import hashlib
        verifier_path=Path(__file__).resolve().parents[1]/'scripts/verify_presuppositions.py'
        pin=patch.dict(c.CODE_HASHES, {'verify_presuppositions.py': hashlib.sha256(verifier_path.read_bytes()).hexdigest()})
        pin.start();self.addCleanup(pin.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
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
                'references':['a1:c1'],'support_verdict':'supported'}]
        return NS(status='completed',error=None,output=[NS(type='message',status='completed',
            content=[NS(type='output_text',text=json.dumps({'verdict':'pass','targets':targets}))])])

    def run_case(self,variant='frozen',case='retain_supported',root=None):
        return c.run(variant,case,root=root or self.root,client=self.client,live=True)

    def test_all_24_isolated_one_shot_and_requests(self):
        protected=['verification-spike-ledger.json','verification-result.json','verification-result-2.json',
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
                    result=json.loads((self.root/f'presupposition-comparison-{variant}-{case}-r1.json').read_text())
                    self.assertEqual(result['fixture_sha256'],meta['fixture_sha256'])
                    self.assertEqual(result['variant'],variant)
                    if variant=='experimental':self.assertTrue(result['findings']['targets'][-1]['presupposition_audit'])
        ledger,attempted,held=c.load_ledger(self.root)
        self.assertEqual((len(attempted),held,self.client.responses.create.call_count),(24,24,24))
        self.assertEqual(sum(a['reserved_micro_usd'] for a in ledger['attempts']),2989440)
        for n in protected:self.assertEqual((self.root/n).read_text(),'untouched')
        # Independent aggregate branch, excluding duplicate-key short circuit.
        with patch.object(c,'load_ledger',return_value=(ledger,{('x',str(i)) for i in range(24)},24)):
            with self.assertRaises(ValueError):self.run_case()
        with self.assertRaises(ValueError):self.run_case('other')
        self.assertEqual(self.client.responses.create.call_count,24)

    def test_identity_and_settings_before_client(self):
        original=Path.read_bytes
        paths=[c.FIXTURES/'manifest.json',c.FIXTURES/'retain_supported.json',
               c.ROOT/'scripts/verify_synthesis.py',c.ROOT/'scripts/verify_presuppositions.py']
        with patch.object(c.base.synthesis.article,'make_client') as client:
            for changed in paths:
                def read(path):return original(path)+(b' ' if path==changed else b'')
                with patch.object(Path,'read_bytes',read):
                    with self.assertRaisesRegex(ValueError,'identity mismatch'):
                        c.run('frozen','retain_supported',root=self.root,live=True)
            for variant,case in [('wrong','retain_supported'),('frozen','wrong')]:
                with self.assertRaises(ValueError):c.run(variant,case,root=self.root,live=True)
            record,payload,_=c.prepare('frozen','retain_supported')
            for key,value in [('instructions','x'*20001),('model','wrong')]:
                bad=copy.deepcopy(payload);bad[key]=value
                with patch.object(c.base,'request_payload',return_value=bad):
                    with self.assertRaises(ValueError):c.run('frozen','retain_supported',root=self.root,live=True)
            client.assert_not_called()
        self.assertFalse((self.root/c.LEDGER).exists())

    def test_single_fixture_read_and_diagnostics(self):
        original=Path.read_bytes;reads=[]
        def read(path):reads.append(path);return original(path)
        with patch.object(Path,'read_bytes',read), patch.object(c.base.synthesis.article,'make_client') as client:
            result=c.run('experimental','retain_supported',root=self.root)
            client.assert_not_called()
        self.assertEqual(reads.count(c.FIXTURES/'retain_supported.json'),1)
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
                response=self.response(**c.prepare('frozen','retain_supported')[1])
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
        p=self.root/'presupposition-comparison-frozen-retain_supported-r1.json';p.write_text('preserve')
        with self.assertRaises(ValueError):self.run_case()
        self.assertEqual(p.read_text(),'preserve')
        with self.assertRaises(FileExistsError):c.base.save_new_result({},p)
        with patch.object(c,'BUDGET',124559):
            with self.assertRaises(ValueError):self.run_case(case='again_supported')
        self.client.responses.create.assert_not_called()

    def test_cli_requires_one_explicit_combination(self):
        for args in ([], ['--variant','frozen'], ['--variant','frozen','--case','retain_supported','--limit','2']):
            with self.assertRaises(SystemExit):c.main(args)
        with patch.object(c,'run',return_value={'mode':'offline'}) as run, patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(c.main(['--variant','experimental','--case','still_supported']),0)
            run.assert_called_once_with('experimental','still_supported',live=False)
