"""Offline audit contract tests, not model sensitivity tests."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import verify_presuppositions as e

class PresuppositionVerifierTests(unittest.TestCase):
    def setUp(self):
        blocker = patch('socket.socket.connect', side_effect=AssertionError('No network'))
        blocker.start(); self.addCleanup(blocker.stop)
        self.record = e.load_fixture('retain_supported')

    def output(self):
        return {'verdict':'pass','targets':[{'target_ref':t['target_ref'],'verdict':'pass',
                'issues':[], 'presupposition_audit':[]} for t in e.base.build_input(self.record)['synthesis']['targets']]}

    def test_frozen_identity_requests_and_instructions(self):
        self.assertEqual(hashlib.sha256(Path(e.base.__file__).read_bytes()).hexdigest(),
                         'cf69583b59add41f23acd1c27e1365da1a57d5255f4efea2db5ae384fd41d209')
        schema = copy.deepcopy(e.base.SCHEMA)
        instructions = e.base.INSTRUCTIONS
        for case in e.CASES:
            record = e.load_fixture(case)
            original = e.base.request_payload(record)
            payload = e.request_payload(record)
            self.assertEqual(payload['input'], original['input'])
            for key in ('model','reasoning','max_output_tokens','tools','store'):
                self.assertEqual(payload[key], original[key])
            self.assertLessEqual(len(json.dumps(payload,ensure_ascii=False).encode()),20000)
            for label in ('expected_verdict','implied_prior_state','category','rationale'):
                self.assertNotIn(label, payload['input'][0]['content'])
        self.assertEqual(e.base.SCHEMA, schema)
        self.assertEqual(e.base.INSTRUCTIONS, instructions)
        for phrase in ('Inspect meaning, not keywords', 'Allow ordinary paraphrases',
                       'Conditional or hypothetical', 'Only material presuppositions',
                       'never outside knowledge', 'Uncited global claims cannot',
                       'Do not invent a prior state', 'unsupported_statement'):
            self.assertIn(phrase, e.AUDIT_INSTRUCTIONS)
        self.assertNotIn('Senate', e.AUDIT_INSTRUCTIONS)

    def test_supported_unsupported_contract(self):
        output = self.output()
        audit = {'trigger_wording':'retain','implied_proposition':'The firm already leads.',
                 'references':['a1:c1'],'support_verdict':'supported'}
        output['targets'][-1]['presupposition_audit'] = [audit]
        self.assertEqual(e.validate(output,self.record),output)
        audit.update(support_verdict='unsupported',references=[])
        with self.assertRaises(ValueError): e.validate(output,self.record)
        output['verdict']='fail'; output['targets'][-1]['verdict']='fail'
        output['targets'][-1]['issues']=[{'type':'unsupported_statement','explanation':'Prior state not supported.', 'references':[]}]
        self.assertEqual(e.validate(output,self.record),output)

    def test_bad_targets_refs_and_audit_fields(self):
        for ref in ('invalid','t9:p9'):
            output=self.output();output['targets'][-1]['target_ref']=ref
            with self.assertRaises(ValueError):e.validate(output,self.record)
        for refs in (['a9:c1'], [], ['a1:c1','a1:c1']):
            output=self.output();output['targets'][-1]['presupposition_audit']=[{
                'trigger_wording':'retain','implied_proposition':'Prior state',
                'references':refs,'support_verdict':'supported'}]
            with self.assertRaises(ValueError):e.validate(output,self.record)
        output=self.output();del output['targets'][0]['presupposition_audit']
        with self.assertRaises(ValueError):e.validate(output,self.record)

    def test_response_envelope_and_no_live_cli(self):
        output=self.output()
        response=NS(status='completed',error=None,output=[NS(type='message',status='completed',
                    content=[NS(type='output_text',text=json.dumps(output))])])
        self.assertEqual(e.parse_response(response,self.record),output)
        response.output[0].content[0].type='refusal'
        with self.assertRaises(ValueError):e.parse_response(response,self.record)
        response.status='incomplete'
        with self.assertRaises(ValueError):e.parse_response(response,self.record)
        with self.assertRaises(SystemExit):e.main(['--live'])

    def test_audit_rejects_existing_but_uncited_global_claim(self):
        record = copy.deepcopy(self.record)
        record['articles'][0]['claims'].append({
            'ref': 'a1:c2', 'text': 'The firm currently holds the leading position.'})
        point = record['synthesis']['themes'][0]['points'][0]
        self.assertEqual(point['references'], ['a1:c1'])
        output = self.output()
        output['targets'][-1]['presupposition_audit'] = [{
            'trigger_wording': 'retain', 'implied_proposition': 'The firm already leads.',
            'references': ['a1:c2'], 'support_verdict': 'supported'}]
        normal = copy.deepcopy(output)
        for target in normal['targets']:
            del target['presupposition_audit']
        # Establish that the input and normal verdict pass production validation.
        e.base.validate(normal, record)
        with self.assertRaisesRegex(ValueError, '^Invalid audit references$'):
            e.validate(output, record)

    def test_unsupported_audit_rejects_wrong_normal_issue_type(self):
        output = self.output()
        output['verdict'] = 'fail'
        target = output['targets'][-1]
        target['verdict'] = 'fail'
        target['issues'] = [{'type': 'partial_support',
                             'explanation': 'The evidence supplies only part of the assertion.',
                             'references': ['a1:c1']}]
        target['presupposition_audit'] = [{
            'trigger_wording': 'retain', 'implied_proposition': 'The firm already leads.',
            'references': [], 'support_verdict': 'unsupported'}]
        normal = copy.deepcopy(output)
        for item in normal['targets']:
            del item['presupposition_audit']
        # Fail/fail and a valid normal issue are consistent; only the audit rule fails.
        e.base.validate(normal, self.record)
        with self.assertRaisesRegex(ValueError, '^Unsupported audit requires normal failure issue$'):
            e.validate(output, self.record)
