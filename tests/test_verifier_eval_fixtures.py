"""Fixture integrity and controlled mutations only; human semantic review required."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import verify_synthesis as verifier

ROOT=Path(__file__).parent/'fixtures'/'verifier_eval'
def read(name):return json.loads((ROOT/name).read_text())
def targets(record):
    s=record['synthesis'];out={'headline':s['headline'],'overview':s['overview']}
    for i,t in enumerate(s['themes'],1):
        out[f't{i}']=t['title']
        for j,p in enumerate(t['points'],1):out[f't{i}:p{j}']=p
    return out

class EvaluationFixtureTests(unittest.TestCase):
    def test_structures_metadata_hashes_and_no_raw_fields(self):
        manifest=read('manifest.json');clean=read('clean.json')
        self.assertEqual(len(manifest['cases']),7)
        expected=['headline','overview','t1','t1:p1','t1:p2','t1:p3','t2','t2:p1','t2:p2','t3','t3:p1','t3:p2']
        for case in manifest['cases']:
            r=read(case['fixture'])
            self.assertEqual(hashlib.sha256((ROOT/case['fixture']).read_bytes()).hexdigest(),case['fixture_sha256'])
            metadata={k:v for k,v in r.items() if k!='synthesis'}
            digest=hashlib.sha256(json.dumps(metadata,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            self.assertEqual(digest,manifest['preserved_metadata_sha256'])
            self.assertEqual(r['articles'],clean['articles'])
            self.assertEqual(set(r),{'version','model','generated_at','window','request_bytes','coverage','articles','synthesis'})
            for a in r['articles']:
                self.assertEqual(set(a),{'article_ref','institution','source_name','title','url','publication_date','claims','read_depth'})
                for c in a['claims']:self.assertEqual(set(c),{'ref','text'})
                self.assertLessEqual(set(a['read_depth']),{'scope','text_source','listing_fallback_used','rss_fallback_used','complete_paper_extracted','charts_tables_linked_reports_extracted','character_count','approximate_word_count'})
            with patch('socket.socket.connect',side_effect=AssertionError('No network')):
                payload=verifier.request_payload(r)
            self.assertEqual(list(targets(r)),expected)
            self.assertEqual(len(verifier.build_input(r)['synthesis']['targets']),12)
            self.assertNotIn('expected_overall',payload['input'][0]['content'])

    def test_only_intended_mutations(self):
        clean=read('clean.json');base=targets(clean)
        expected={'clean':set(),'turkiye_conditionality':{'t1:p3'},'argentina_overview':{'overview'},
                  'bis_reference_support':{'t2:p2'},'ecb_despite':{'t1:p2'},'abn_retain':{'t3:p1'},
                  'harmless_paraphrase':{'t1:p1','t1:p3','t3:p2'}}
        for name,wanted in expected.items():
            r=read(name+'.json');ts=targets(r)
            self.assertEqual({k for k in base if base[k]!=ts[k]},wanted)
            for key in base:
                if ':p' in key:
                    if name=='bis_reference_support' and key=='t2:p2':
                        self.assertEqual(base[key]['text'],ts[key]['text'])
                    else:self.assertEqual(base[key]['references'],ts[key]['references'])
        labels={c['case_id']:c for c in read('manifest.json')['cases']}
        self.assertEqual(labels['bis_reference_support']['expected_overall'],'review/provisional_fail')
        self.assertEqual(labels['ecb_despite']['expected_overall'],'review/borderline')

    def test_abn_correction_is_only_continuity_mutation(self):
        clean=targets(read('clean.json'))['t3:p1']
        variant=targets(read('abn_retain.json'))['t3:p1']
        self.assertTrue(clean['text'].startswith('ABN AMRO’s base case is a Democratic House and Republican Senate;'))
        self.assertEqual(variant['text'],clean['text'].replace('Republican Senate;', 'Republicans retaining the Senate;'))
        self.assertEqual(variant['references'],clean['references'])
