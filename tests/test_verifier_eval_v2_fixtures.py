"""V2 headline-only revision; no model execution or semantic truth assertions."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from test_verifier_eval_fixtures import targets
import verify_synthesis as verifier

ROOT = Path(__file__).parent / 'fixtures'
V1 = ROOT / 'verifier_eval'
V2 = ROOT / 'verifier_eval_v2'
HEADLINE = ('BBVA’s rate outlooks depend on inflation and energy conditions, '
            'while BIS research links interbank conditions to reserve demand')
# Frozen Campaign 1 inventory, independent of either mutable manifest.
CAMPAIGN_1_HASHES = {'README.md': '8371a601da4ee750758026712a3903a885873b612f5eef9e497de39166fdf54b', 'abn_retain.json': 'e49a26e24da2bdd10f3dce78a9f02ae9566a1a7c12dadcf0aa4012f04d24a88e', 'argentina_overview.json': '9e21fed6b213fdc48903432c0d3588e3acb6702ab72bd4140000025add6c99bf', 'bis_reference_support.json': 'd3007d8b7dc67473cec792054d7a0bdb251810bbbe43fedc434c3eb311930195', 'clean.json': '1ce323d6f441214af8b2de8dcc962f6b088d779f774db2137b74e8fc224f84dd', 'ecb_despite.json': 'fb45e7abe24a23d77c3d58a95cf1b74c1f42d3ec5c666edcb03d64c403e311b4', 'harmless_paraphrase.json': 'bf6d8b6c63c126a6de5e26cb663fe7956e230639fd8961f3a47948478ad56816', 'manifest.json': 'aaafe59cd1d73820aadd54f2bac823d7dc1f7639e9a0c53eff1d5ec76a2d0277', 'turkiye_conditionality.json': '4128775ffa26f0bba6b2c38c9077c2e00016c4e1e060bd75fb67f2949f800b64'}


class RevisedEvaluationFixtureTests(unittest.TestCase):
    def test_campaign_one_bytes_and_revision_provenance(self):
        actual = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in V1.iterdir() if p.is_file()}
        self.assertEqual(actual, CAMPAIGN_1_HASHES)
        manifest = json.loads((V2 / 'manifest.json').read_text())
        self.assertEqual(manifest['version'], 2)
        self.assertEqual(manifest['revision']['parent_file_sha256'], actual)
        self.assertEqual(manifest['revision']['only_cross_set_change'], 'synthesis.headline')
        self.assertEqual(manifest['revision']['headline'], HEADLINE)

    def test_only_headline_changes_and_hashes_labels_requests(self):
        original = json.loads((V1 / 'manifest.json').read_text())
        revised = json.loads((V2 / 'manifest.json').read_text())
        self.assertEqual(len(revised['cases']), 7)
        for before, after in zip(original['cases'], revised['cases']):
            self.assertEqual({k:v for k,v in before.items() if k != 'fixture_sha256'},
                             {k:v for k,v in after.items() if k != 'fixture_sha256'})
            raw = (V1 / before['fixture']).read_bytes()
            new_raw = (V2 / after['fixture']).read_bytes()
            old = json.loads(raw)
            new = json.loads(new_raw)
            expected_bytes = raw.replace(
                json.dumps(old['synthesis']['headline'], ensure_ascii=False).encode(),
                json.dumps(HEADLINE, ensure_ascii=False).encode(), 1)
            self.assertEqual(new_raw, expected_bytes)
            self.assertEqual(hashlib.sha256(new_raw).hexdigest(), after['fixture_sha256'])
            old['synthesis']['headline'] = HEADLINE
            # Full equality also protects claims, metadata, references and absence
            # of any newly introduced raw publisher content or other fields.
            self.assertEqual(new, old)
            with patch('socket.socket.connect', side_effect=AssertionError('No network')):
                payload = verifier.request_payload(new)
            self.assertNotIn('expected_overall', payload['input'][0]['content'])

    def test_v2_mutations_remain_isolated(self):
        base = targets(json.loads((V2 / 'clean.json').read_text()))
        expected = {'clean': set(), 'turkiye_conditionality': {'t1:p3'},
                    'argentina_overview': {'overview'}, 'bis_reference_support': {'t2:p2'},
                    'ecb_despite': {'t1:p2'}, 'abn_retain': {'t3:p1'},
                    'harmless_paraphrase': {'t1:p1', 't1:p3', 't3:p2'}}
        for case, changed in expected.items():
            current = targets(json.loads((V2 / (case + '.json')).read_text()))
            self.assertEqual(list(current), list(base))
            self.assertEqual(current['headline'], HEADLINE)
            self.assertEqual({k for k in base if base[k] != current[k]}, changed)
            for key in base:
                if ':p' in key:
                    if case == 'bis_reference_support' and key == 't2:p2':
                        self.assertEqual(current[key]['text'], base[key]['text'])
                        self.assertEqual(current[key]['references'],
                                         [r for r in base[key]['references'] if r not in {'a5:c1', 'a5:c2'}])
                    else:
                        self.assertEqual(current[key]['references'], base[key]['references'])
