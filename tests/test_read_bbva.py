"""Synthetic BBVA structure only. Never fetch or archive publisher content."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import read_bbva as read

INTRO = 'Invented introductory research text explains the current economic situation in plain language.'
POINT = 'Synthetic analysis finds that household demand remains steady while prices and employment change gradually across the economy.'
HTML = f'''<html><nav>NAVIGATION SECRET</nav><article id="article_publicacionDetalle">
<h1>Invented title</h1><div id="detalle_tabs_publi"><p class="detalle_text_intro">{INTRO}</p>
<ul class="lista_puntosClave"><li class="lista_itemTitulo">LIST LABEL</li><li>{POINT}</li><li>{POINT}</li></ul>
<footer>FOOTER SECRET</footer><div class="descargas rs_skip">DOWNLOAD SECRET</div></div>
<div id="detalle_autores">AUTHOR SECRET</div></article><footer>OUTSIDE SECRET</footer></html>'''
ITEM = {'title': 'Invented article', 'url': 'https://www.bbvaresearch.com/en/publicaciones/example/',
        'publication_date': '2026-09-10', 'listing_summary': 'Synthetic fallback excerpt.'}
ROBOTS = ('User-agent: *\nDisallow:\nCrawl-delay: 4', 'text/plain')


class ReadTests(unittest.TestCase):
    def test_extracts_only_article_content_and_excludes_noise(self):
        result = read.extract_article(HTML)
        self.assertIn(INTRO, result)
        self.assertIn(POINT, result)
        self.assertNotIn('SECRET', result)
        self.assertNotIn('LIST LABEL', result)
        self.assertNotIn('Invented title', result)

    def test_unclosed_outer_layout_does_not_invalidate_closed_content(self):
        html = HTML.replace('<h1>', '<div><div><h1>')
        self.assertIn(POINT, read.extract_article(html))

    def test_nested_markup_hidden_content_and_entities(self):
        html = HTML.replace(INTRO, INTRO+' <em>Prices &amp; wages</em><span hidden>HIDDEN SECRET</span><nav>INNER SECRET</nav>')
        result = read.extract_article(html)
        self.assertIn('Prices & wages', result)
        self.assertNotIn('SECRET', result)

    def test_empty_malformed_and_intro_only_fail_safely(self):
        for html in ['', '<html>Access denied</html>', HTML.replace('</article>', ''), HTML.replace('</ul>', ''), HTML.replace(POINT, ''), HTML+HTML]:
            with self.subTest(html=html[:40]), self.assertRaises(ValueError):
                read.extract_article(html)
        result = read.diagnose(ITEM, '<html>No article</html>')
        self.assertTrue(result['listing_fallback_used'])
        self.assertFalse(result['extraction_success'])
        self.assertEqual(result['character_count'], len(ITEM['listing_summary']))
        self.assertFalse(read.diagnose(dict(ITEM, listing_summary=''), '')['listing_fallback_used'])

    def test_selection_validates_and_caps_three_distinct_recent_urls(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'snapshot.json'
            items = [dict(ITEM, url=ITEM['url']+str(i)+'/', publication_date=f'2026-09-0{i+1}') for i in range(5)]
            items.append(items[-1])
            data = {'institution': 'BBVA Research', 'listing_url': 'https://www.bbvaresearch.com/en/publications/', 'items': items, 'item_count': len(items)}
            path.write_text(json.dumps(data))
            selected = read.select_items(path)
            self.assertEqual([i['publication_date'] for i in selected], ['2026-09-05','2026-09-04','2026-09-03'])
            data['items'][0]['url'] = 'https://example.com/bad'
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                read.select_items(path)

    @patch.object(read, 'select_items', return_value=[ITEM]*3)
    @patch.object(read.time, 'sleep')
    def test_robots_denial_and_failure_make_no_article_requests(self, sleep, select):
        for response in [('User-agent: *\nDisallow: /en/publicaciones/', 'text/plain'), ('<html>Challenge</html>', 'text/html'), OSError('offline')]:
            with patch.object(read, 'fetch', side_effect=[response]) as fetch:
                result = read.run()
                fetch.assert_called_once_with(read.ROBOTS)
                self.assertTrue(all(i['listing_fallback_used'] for i in result))

    @patch.object(read, 'select_items', return_value=[ITEM]*3)
    @patch.object(read.time, 'sleep')
    def test_fetch_or_challenge_failure_stops_remaining_requests(self, sleep, select):
        for response in [ValueError('http_not_200'), ('<form id="challenge-form"></form>', 'text/html')]:
            with patch.object(read, 'fetch', side_effect=[ROBOTS, response]) as fetch:
                result = read.run()
                self.assertEqual(fetch.call_count, 2)
                self.assertTrue(all(i['listing_fallback_used'] for i in result))

    @patch.object(read, 'select_items', return_value=[ITEM]*3)
    @patch.object(read.time, 'sleep')
    def test_only_safe_diagnostics_and_no_disk_writes(self, sleep, select):
        # Block all Python file opens during execution; select_items is mocked.
        # The real fetch implementation uses captured subprocess output, never -o.
        with patch.object(read, 'fetch', side_effect=[ROBOTS]+[(HTML,'text/html')]*3) as fetch, patch('builtins.open', side_effect=AssertionError('file I/O')), patch('io.open', side_effect=AssertionError('file I/O')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(read.main(), 0)
        self.assertEqual([c.args[0] for c in fetch.call_args_list], [read.ROBOTS]+[ITEM['url']]*3)
        self.assertEqual(sleep.call_count, 3)
        sleep.assert_called_with(4)
        self.assertNotIn(INTRO, output.getvalue())
        self.assertNotIn(POINT, output.getvalue())
        self.assertNotIn('<html>', output.getvalue())
        diagnostics = json.loads(output.getvalue())
        self.assertTrue(all(i['extraction_success'] and not i['listing_fallback_used'] for i in diagnostics))

    def test_fetch_captures_memory_only_and_rejects_non200(self):
        result = read.subprocess.CompletedProcess([], 0, b'blocked\n403\ntext/html')
        with patch.object(read.subprocess, 'run', return_value=result) as run:
            with self.assertRaises(ValueError):
                read.fetch(ITEM['url'])
            args = run.call_args.args[0]
            self.assertNotIn('-o', args)
            self.assertNotIn('--output', args)
            self.assertNotIn('--location', args)
            self.assertTrue(run.call_args.kwargs['capture_output'])
            self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
