"""Synthetic pair structure/integrity only, not semantic detection tests."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import verify_synthesis as verifier
ROOT = Path(__file__).parent / 'fixtures' / 'presupposition_pairs'
PAIRS = [('retain', 'The firm expects to retain its leading position.', 'The firm already holds the leading position.', 'The firm expects to hold the leading position next year.', 'The firm currently holds the leading position.'), ('continue', 'Borrowing costs will continue rising.', 'Borrowing costs are already rising.', 'Borrowing costs will rise over the next quarter.', 'Borrowing costs are currently rising.'), ('remain', 'Demand is expected to remain weak.', 'Demand is already weak.', 'Demand is expected to be weak next quarter.', 'Demand is currently weak.'), ('return', 'Margins are expected to return to 10%.', 'Margins previously reached 10%.', 'Margins are currently 8% and are expected to reach 10% next year.', 'Margins were 10% two years ago.'), ('still', 'Households are still reducing debt.', 'Households were previously reducing debt.', 'Households are reducing debt today.', 'Households were reducing debt last month.'), ('again', 'The company will increase investment again.', 'The company increased investment previously.', 'The company will increase investment next year.', 'The company increased investment last year.')]

class PresuppositionFixtureTests(unittest.TestCase):
    def test_pairs_evidence_isolation_and_request_contract(self):
        manifest = json.loads((ROOT/'manifest.json').read_text())
        self.assertEqual(len(manifest['cases']), 12)
        self.assertEqual(len(manifest['pairs']), 6)
        names = {trigger+'_'+suffix for trigger, *_ in PAIRS for suffix in ('supported','unsupported')}
        self.assertEqual({p.stem for p in ROOT.glob('*.json') if p.name != 'manifest.json'}, names)
        self.assertEqual({c['case_id'] for c in manifest['cases']}, names)
        for trigger, candidate, prior, main, background in PAIRS:
            records = []
            for suffix in ('supported', 'unsupported'):
                case = trigger+'_'+suffix
                r = json.loads((ROOT/(case+'.json')).read_text())
                records.append(r)
                self.assertEqual(r['synthesis']['themes'][0]['points'][0]['text'].encode(), candidate.encode())
                expected = main + (' '+background if suffix == 'supported' else '')
                self.assertEqual(r['articles'][0]['claims'], [{'ref':'a1:c1','text':expected}])
                self.assertEqual(set(r), {'version','articles','synthesis'})
                self.assertEqual(len(r['articles']), 1)
                self.assertEqual(set(r['articles'][0]), {'article_ref','institution','source_name','title','publication_date','claims'})
                with patch('socket.socket.connect', side_effect=AssertionError('No network')):
                    payload = verifier.request_payload(r)
                data = json.loads(payload['input'][0]['content'])
                self.assertEqual([t['target_ref'] for t in data['synthesis']['targets']], ['headline','overview','t1','t1:p1'])
                self.assertEqual(data['evidence'][0]['article_ref'], 'a1')
                for label in ('expected_verdict','category','rationale','implied_prior_state','pair_id'):
                    self.assertNotIn(label, payload['input'][0]['content'])
                entry = next(c for c in manifest['cases'] if c['case_id'] == case)
                self.assertEqual(entry['candidate_sentence'], candidate)
                self.assertEqual(entry['implied_prior_state'], prior)
                self.assertEqual(entry['expected_verdict'], 'pass' if suffix == 'supported' else 'fail')
                self.assertEqual(entry['category'], 'supported_control' if suffix == 'supported' else 'unsupported_presupposition')
            left = copy.deepcopy(records[0])
            left['articles'][0]['claims'] = records[1]['articles'][0]['claims']
            self.assertEqual(left, records[1])
            pair = next(p for p in manifest['pairs'] if p['trigger'] == trigger)
            self.assertEqual(pair['members'], [trigger+'_supported', trigger+'_unsupported'])

    def test_hashes_and_historical_sets_unchanged(self):
        manifest = json.loads((ROOT/'manifest.json').read_text())
        for entry in manifest['cases']:
            self.assertEqual(hashlib.sha256((ROOT/entry['fixture']).read_bytes()).hexdigest(), entry['fixture_sha256'])
        for relative, digest in manifest['historical_fixture_sha256'].items():
            self.assertEqual(hashlib.sha256((ROOT.parent/relative).read_bytes()).hexdigest(), digest)
