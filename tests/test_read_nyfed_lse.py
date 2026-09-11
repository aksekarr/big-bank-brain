"""Synthetic LSE HTML and mocked transport; no publisher archives or network."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import read_nyfed_lse as read
from test_dedupe import seed
from test_extract_bbva import NOW

INTRO = 'Invented introduction describing the economic outlook.'
BODY = ('Household demand remains steady while industrial activity gradually improves. '
        'Researchers expect moderate growth over the coming months, with uncertainty '
        'around energy costs and investment intentions. Employment conditions remain '
        'stable and financial conditions are expected to support further expansion.')
HTML = ('<nav>Navigation noise</nav><div class="ts-article-text"><p>Outside noise</p></div>'
        '<main><h1>Title</h1><div class="ts-article-text"><h3>Focus</h3><p>'+INTRO+
        '</p><p>'+BODY+'<span hidden>Hidden noise</span><script>Script noise</script></p>'
        '<div class="cite-container"><p>Citation noise</p></div><p class="is-style-bio-contact">Biography noise</p><div class="sharedaddy"><p>Sharing noise</p></div><footer>Footer noise</footer><table><tr><td>Table noise</td></tr></table>'
        '</div><aside><p>Related noise</p></aside></main>')

ITEM = {'title': 'Invented analysis', 'url': 'https://libertystreeteconomics.newyorkfed.org/2026/09/invented/',
        'publication_date': '2026-09-09', 'institution': 'Federal Reserve Bank of New York',
        'source_name': 'Liberty Street Economics', 'excerpt': 'Invented RSS excerpt.'}
ROBOTS = ('User-agent: *\nAllow: /\nDisallow: /api/search', 'text/plain')


class LseReadTests(unittest.TestCase):
    def test_article_text_excludes_chrome_and_handles_inline_text(self):
        text = read.extract_article(HTML)
        self.assertIn(INTRO, text)
        self.assertIn(BODY, text)
        for noise in ['Navigation', 'Hidden', 'Script', 'Footer', 'Related', 'Title', 'Outside', 'Table', 'Citation', 'Biography', 'Sharing']:
            self.assertNotIn(noise, text)

    def test_empty_changed_malformed_and_challenge_fail_safely(self):
        for html in ['', '<main><p>Unknown structure</p></main>',
                     HTML.replace(BODY, 'Short'),
                     HTML.replace('</script></p>', '</script>'),
                     HTML+'<form id="challenge-form"></form>']:
            with self.subTest(html=html[:20]), self.assertRaises(ValueError):
                read.extract_article(html)

    def test_transient_callback_diagnostics_and_no_writes(self):
        consume = Mock()
        with patch.object(read, 'fetch', side_effect=[ROBOTS,(HTML,'text/html')]) as fetch, patch.object(read.time,'sleep'), patch('builtins.open', side_effect=AssertionError('No files')), patch.object(Path,'write_text',side_effect=AssertionError('No files')):
            result = read.run([ITEM],consume)
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(consume.call_args.args,(ITEM,read.extract_article(HTML),'article_body_html'))
        self.assertEqual(result[0]['character_count'],len(read.extract_article(HTML)))
        self.assertNotIn(BODY,json.dumps(result))
        self.assertNotIn(INTRO,json.dumps(result))
        self.assertFalse(result[0]['rss_fallback_used'])

    def test_denied_robots_and_failed_access_use_existing_excerpt_only(self):
        for replies in [[('User-agent: *\nDisallow: /','text/plain')],
                        [ValueError('unavailable')],
                        [ROBOTS,ValueError('blocked')]]:
            consume = Mock()
            with patch.object(read,'fetch',side_effect=replies) as fetch, patch.object(read.time,'sleep'):
                results = read.run([ITEM,dict(ITEM,url=ITEM['url'].rstrip('/')+'-two/')],consume)
            self.assertEqual(fetch.call_count,len(replies))
            self.assertTrue(all(r['rss_fallback_used'] for r in results))
            self.assertEqual(consume.call_args.args[1],ITEM['excerpt'])

    def test_insufficient_html_fallback_missing_excerpt_and_callback_failure(self):
        with patch.object(read,'fetch',side_effect=[ROBOTS,('<main></main>','text/html')]), patch.object(read.time,'sleep'):
            result = read.run([dict(ITEM,excerpt='')])
        self.assertFalse(result[0]['text_available'])
        with patch.object(read,'fetch',side_effect=[ROBOTS,(HTML,'text/html')]), patch.object(read.time,'sleep'), self.assertRaises(RuntimeError):
            read.run([ITEM],Mock(side_effect=RuntimeError('downstream failed')))

    def test_bad_url_and_too_many_items_never_fetch(self):
        with patch.object(read,'fetch') as fetch:
            for items in [[dict(ITEM,url='https://libertystreeteconomics.newyorkfed.org/api/search')],[ITEM]*4]:
                with self.assertRaises(ValueError):
                    read.run(items)
            fetch.assert_not_called()

    def test_selection_uses_current_state_and_window_without_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            seed(root)
            before={p.name:p.read_bytes() for p in root.iterdir()}
            items=read.select(root,NOW,1)
            self.assertEqual(len(items),1)
            self.assertEqual(items[0]['source_name'],'Liberty Street Economics')
            self.assertEqual(before,{p.name:p.read_bytes() for p in root.iterdir()})
            read.processing.write_json({'version':1,'processed':{items[0]['url']:NOW.isoformat()}},root/read.processing.STATE_NAME)
            self.assertEqual(read.select(root,NOW,1),[])


if __name__ == '__main__':
    unittest.main()
