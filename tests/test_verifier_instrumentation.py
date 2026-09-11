"""Parity against the committed pre-instrumentation verifier; no model calls."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import verify_presuppositions as current
import verify_candidate

BASELINE='1a83f8b155f21a7266bc16bd81888cdd122069f4'


class InstrumentationParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old=types.ModuleType('pre_instrumentation')
        cls.old.__file__=current.__file__
        source=subprocess.check_output(['git','show',BASELINE+':scripts/verify_presuppositions.py'],text=True)
        exec(compile(source,'pre_instrumentation','exec'),cls.old.__dict__)

    def setUp(self):
        p=patch('socket.socket.connect',side_effect=AssertionError('No network'))
        p.start();self.addCleanup(p.stop)

    def result(self,record):
        return {'verdict':'pass','targets':[{'target_ref':t['target_ref'],'verdict':'pass',
            'issues':[],'presupposition_audit':[]} for t in current.base.build_input(record)['synthesis']['targets']]}

    def test_requests_and_acceptance_across_all_pairs_and_abn(self):
        records=[current.load_fixture(case) for case in current.CASES]
        root=Path(current.__file__).resolve().parents[1]
        records += [json.loads(p.read_bytes()) for p in (root/'tests/fixtures/abn_heldout').glob('abn_*.json')]
        current_snapshot=root/'synthesis-result.json'
        if current_snapshot.exists():
            records.append(json.loads(current_snapshot.read_bytes()))
        for record in records:
            with self.subTest(title=record['synthesis']['headline']):
                self.assertEqual(self.old.request_payload(record),current.request_payload(record))
                good=self.result(record)
                self.assertEqual(self.old.validate(good,record),current.validate(good,record))
                cases=[]
                x=copy.deepcopy(good);del x['targets'][0]['presupposition_audit'];cases.append((x,'shape_text_validation'))
                x=copy.deepcopy(good);x['targets'][-1]['target_ref']='t99:p99';cases.append((x,'normal_validation'))
                x=copy.deepcopy(good);x['verdict']='fail';cases.append((x,'normal_validation'))
                x=copy.deepcopy(good);x['targets'][-1]['presupposition_audit']=[{'trigger_wording':'test','implied_proposition':'Test prior state','references':['a99:c1'],'support_verdict':'supported'}];cases.append((x,'audit_reference_scope'))
                x=copy.deepcopy(good);x['targets'][-1]['presupposition_audit']=[{'trigger_wording':'test','implied_proposition':'Test prior state','references':[],'support_verdict':'supported'}];cases.append((x,'audit_support_consistency'))
                for value,stage in cases:
                    with self.assertRaises(ValueError):self.old.validate(value,record)
                    with self.assertRaises(current.ValidationStageError) as error:current.validate(value,record)
                    self.assertEqual(error.exception.stage,stage)
                    response=types.SimpleNamespace(status='completed',output=[])
                    self.assertEqual(verify_candidate.response_diagnostic(response,error.exception),stage)

    def test_historical_stored_experimental_results(self):
        root=Path(current.__file__).resolve().parents[1]
        checked=0
        for path in list(root.glob('presupposition-comparison-experimental-*-r1.json'))+list(root.glob('abn-heldout-comparison-experimental-*-r1.json')):
            name=path.name
            if name.startswith('presupposition-'):
                case=name.removeprefix('presupposition-comparison-experimental-').removesuffix('-r1.json')
                record=current.load_fixture(case)
            else:
                case=name.removeprefix('abn-heldout-comparison-experimental-').removesuffix('-r1.json')
                record=json.loads((root/'tests/fixtures/abn_heldout'/f'{case}.json').read_bytes())
            saved=json.loads(path.read_bytes())
            candidates=[saved]+[v for v in saved.values() if isinstance(v,dict)]
            output=next(v for v in candidates if 'targets' in v and 'verdict' in v)
            output={k:output[k] for k in ('verdict','targets')}
            self.assertEqual(self.old.validate(output,record),current.validate(output,record));checked+=1
        if not checked:self.skipTest('Historical local runtime results unavailable')

    def test_envelope_json_and_internal_errors(self):
        record=current.load_fixture('retain_supported')
        for text,stage in [('{"x":1,"x":2}','json_value_validation'),('NaN','json_value_validation'),('bad','invalid_json')]:
            response=types.SimpleNamespace(status='completed',error=None,output=[types.SimpleNamespace(type='message',status='completed',content=[types.SimpleNamespace(type='output_text',text=text)])])
            with self.assertRaises(ValueError):self.old.parse_response(response,record)
            with self.assertRaises(ValueError) as error:current.parse_response(response,record)
            self.assertEqual(verify_candidate.response_diagnostic(response,error.exception),stage)
        with patch.object(current.base,'validate',side_effect=RuntimeError('PRIVATE')):
            with self.assertRaises(RuntimeError):current.validate(self.result(record),record)

    def test_historical_pins_remain_closed(self):
        import compare_presuppositions as synthetic
        import compare_abn_heldout as abn
        import hashlib
        actual=hashlib.sha256(Path(current.__file__).read_bytes()).hexdigest()
        for runner,case in ((synthetic,'retain_supported'),(abn,'abn_clean')):
            self.assertNotEqual(runner.CODE_HASHES['verify_presuppositions.py'],actual)
            with self.assertRaisesRegex(ValueError,'identity mismatch'):
                runner.prepare('experimental',case)
