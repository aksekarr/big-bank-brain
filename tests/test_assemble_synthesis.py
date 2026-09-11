"""Synthetic semantics/state only; no publisher content or network."""
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import assemble_synthesis as a
from test_extract_bbva import SEMANTICS


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.url='https://www.bbvaresearch.com/en/publicaciones/test/'
        self.record=dict(institution='BBVA Research',source_name='BBVA Research',title='Test',
                         url=self.url,publication_date='2026-09-10',extraction=SEMANTICS,
                         input_scope='HTML introduction and key points')
        self.save({self.url:self.record})
        self.state({self.url:'2026-09-11T00:00:00Z'})

    def save(self,items,filename='bbva-ai-extractions.json'):
        (self.root/filename).write_text(json.dumps({'version':1,'items':items}))

    def state(self,items):
        (self.root/'processed-state.json').write_text(json.dumps({'version':1,'processed':items}))

    def run_assembly(self):
        return a.assemble(self.root,date(2026,9,11))

    def test_valid_intersection_projection_depth_and_no_writes(self):
        self.record['article_text']='RAW_MARKER'
        self.record['read_metadata']={'character_count':100,'raw_html':'RAW_MARKER'}
        self.save({self.url:self.record})
        before={p.name:p.read_bytes() for p in self.root.iterdir()}
        output=self.run_assembly()
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})
        item=output['articles'][0]
        self.assertEqual(item['article_ref'],'a1')
        self.assertEqual(item['claims'],[{'ref':'a1:c1','text':SEMANTICS['claims'][0]}])
        self.assertNotIn('RAW_MARKER',json.dumps(output))
        self.assertEqual(output['coverage']['article_count'],1)
        self.assertEqual(output['coverage']['institution_count'],1)

    def test_unprocessed_and_missing_extraction_are_reported(self):
        other=self.url+'other'
        self.state({other:'2026-09-11T00:00:00Z'})
        output=self.run_assembly()
        self.assertEqual(output['articles'],[])
        reasons={d['reason'] for d in output['diagnostics']}
        self.assertTrue({'not_processed','processed_without_extraction'}<=reasons)

    def test_calendar_boundaries_and_london_midnight(self):
        for published,included in [('2026-09-04',False),('2026-09-05',True),('2026-09-11',True),('2026-09-12',False)]:
            self.record['publication_date']=published
            self.save({self.url:self.record})
            self.assertEqual(bool(self.run_assembly()['articles']),included)
        output=a.assemble(self.root,datetime(2026,9,10,23,30,tzinfo=timezone.utc))
        self.assertEqual(output['window']['end_date'],'2026-09-11')
        with self.assertRaises(ValueError):
            a.assemble(self.root,datetime(2026,9,11))

    def test_invalid_article_does_not_hide_unrelated_valid_article(self):
        for changes in [{'title':None},{'publication_date':None},{'publication_date':'yesterday'},
                        {'publication_date':'2026-02-30'},{'extraction':{'summary':'bad'}},
                        {'institution':'Wrong'}]:
            bad=dict(self.record,**changes)
            good=dict(self.record,url=self.url+'good')
            self.save({self.url:bad,good['url']:good})
            self.state({self.url:'2026-09-11T00:00:00Z',good['url']:'2026-09-11T00:00:00Z'})
            output=self.run_assembly()
            self.assertEqual(len(output['articles']),1)
            self.assertIn('invalid_article',{d['reason'] for d in output['diagnostics']})

    def test_exact_duplicates_and_conflicts(self):
        duplicate=dict(self.record,url=self.url+'?utm_source=test#fragment')
        self.save({self.url:self.record,duplicate['url']:duplicate})
        self.assertEqual(len(self.run_assembly()['articles']),1)
        duplicate['extraction']=dict(SEMANTICS,summary='A different supported interpretation.')
        self.save({duplicate['url']:duplicate,self.url:self.record})
        output=self.run_assembly()
        self.assertEqual(output['articles'],[])
        self.assertIn('conflicting_duplicate',{d['reason'] for d in output['diagnostics']})

    def test_stable_order_and_claim_references(self):
        records=[dict(self.record,url=self.url+str(n),title=title,
                      extraction=dict(SEMANTICS,claims=['First finding.','Second finding.']))
                 for n,title in enumerate(['Zulu','Alpha','Alpha'])]
        self.state({r['url']:'2026-09-11T00:00:00Z' for r in records})
        self.save({r['url']:r for r in records})
        first=self.run_assembly()
        self.save({r['url']:r for r in reversed(records)})
        self.assertEqual(first,self.run_assembly())
        self.assertEqual([r['title'] for r in first['articles']],['Alpha','Alpha','Zulu'])
        self.assertEqual(first['articles'][1]['claims'][1]['ref'],'a2:c2')

    def test_depth_shapes_preserve_only_known_fields(self):
        self.assertEqual(a.depth(self.record),{'scope':self.record['input_scope']})
        self.assertNotIn('character_count',a.depth(self.record))
        for flag in ['listing_fallback_used','rss_fallback_used']:
            raw={'text_source':'rss_excerpt' if flag.startswith('rss') else 'article_html',
                 flag:True,'character_count':100,'approximate_word_count':20,
                 'complete_paper_extracted':False,'charts_tables_linked_reports_extracted':False}
            self.assertEqual(a.depth({'read_metadata':raw}),raw)
        with self.assertRaises(ValueError):
            a.depth({'read_metadata':{'character_count':-1}})

    def test_corrupt_file_reports_partial_but_corrupt_state_stops(self):
        (self.root/'bis-ai-extractions.json').write_text('{broken')
        output=self.run_assembly()
        self.assertEqual(len(output['articles']),1)
        self.assertIn('invalid_extraction_file',{d['reason'] for d in output['diagnostics']})
        (self.root/'processed-state.json').write_text('{broken')
        with self.assertRaises(ValueError):
            self.run_assembly()

    def test_missing_files_and_empty_window(self):
        (self.root/'bbva-ai-extractions.json').unlink()
        (self.root/'processed-state.json').unlink()
        output=self.run_assembly()
        self.assertEqual(output['coverage']['article_count'],0)
        self.assertEqual(output['coverage']['institution_count'],0)
        self.assertEqual(len(output['coverage']['per_institution']),4)


if __name__=='__main__':
    unittest.main()
