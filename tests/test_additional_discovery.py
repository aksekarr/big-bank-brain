"""Invented discovery fixtures; every network call is mocked."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import discovery_common as common
import discover_abn_amro as abn
import discover_nyfed_lse as lse
import discover_bis as bis

ABN = '''<a data-element-type="list item" href="/research/en/our-research/example">
<div><h3>Example &amp; <span>analysis</span></h3><time><span>10 September 2026</span></time>
<p class="sm:emc-line-clamp-4">A <em>short</em> introduction.</p></div></a>'''
LSE_ITEM = '''<item><title>Example &amp; analysis</title>
<link>https://libertystreeteconomics.newyorkfed.org/2026/09/example/</link>
<pubDate>Thu, 10 Sep 2026 11:00:00 +0000</pubDate>
<description><![CDATA[<p>A <em>short</em> introduction.</p>]]></description>
<content:encoded><![CDATA[UNUSED FULL BODY]]></content:encoded></item>'''
BIS_ITEM = '''<item><title>Example &amp; analysis</title>
<link>https://www.bis.org/publications/example</link>
<dc:date>2026-09-10T00:00:00Z</dc:date><description>A short introduction.</description>
<extra><link>https://www.bis.org/sites/default/files/unused.pdf</link></extra></item>'''


def lse_xml(items):
    return '<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>'+items+'</channel></rss>'


def bis_xml(items):
    return '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">'+items+'</rdf:RDF>'


CASES = [(abn, abn.parse_listing, ABN, lambda s: s, 'text/html'),
         (lse, lse.parse_feed, LSE_ITEM, lse_xml, 'application/rss+xml'),
         (bis, bis.parse_feed, BIS_ITEM, bis_xml, 'application/rss+xml')]


class ParsingTests(unittest.TestCase):
    def test_success_duplicates_and_only_discovery_metadata(self):
        for module, parse, item, wrap, _ in CASES:
            with self.subTest(source=module.__name__):
                records = parse(wrap(item+item))
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]['title'], 'Example & analysis')
                self.assertEqual(records[0]['publication_date'], '2026-09-10')
                self.assertEqual(records[0]['excerpt'], 'A short introduction.')
                self.assertNotIn('UNUSED', json.dumps(records))
                self.assertNotIn('unused.pdf', json.dumps(records))

    def test_missing_optional_metadata_not_invented(self):
        variants = [(abn.parse_listing, '<a data-element-type="list item" href="/research/en/our-research/example"><h3>Example</h3></a>'),
                    (lse.parse_feed, lse_xml('<item><title>Example</title><link>https://libertystreeteconomics.newyorkfed.org/2026/09/example/</link><content:encoded>BODY</content:encoded></item>')),
                    (bis.parse_feed, bis_xml('<item><title>Example</title><link>https://www.bis.org/publications/example</link></item>'))]
        for parse, fixture in variants:
            record = parse(fixture)[0]
            self.assertNotIn('publication_date', record)
            self.assertNotIn('excerpt', record)

    def test_malformed_empty_offdomain_dates_and_conflicts(self):
        for module, parse, item, wrap, _ in CASES:
            invalid_date = item.replace('10 September 2026', '31 September 2026').replace('10 Sep 2026', '31 Sep 2026').replace('2026-09-10T', '2026-09-31T')
            offdomain = item.replace('/research/en/our-research/example', 'https://example.com/bad') if module is abn else item.replace('https://'+module.ENDPOINT.split('/')[2], 'https://example.com')
            for bad in [wrap(''), '<html>Access denied</html>', wrap(invalid_date), wrap(offdomain), wrap(item+item.replace('Example &amp;', 'Conflicting &amp;')), wrap(item)[:-8]]:
                with self.subTest(source=module.__name__, bad=bad[:50]), self.assertRaises(ValueError):
                    parse(bad)

    def test_xml_entities_and_relative_abn_dates_rejected(self):
        for parse in [lse.parse_feed, bis.parse_feed]:
            with self.assertRaises(ValueError):
                parse('<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss/>')
        with self.assertRaises(ValueError):
            abn.parse_listing(ABN.replace('10 September 2026', 'Yesterday'))


class AccessAndOutputTests(unittest.TestCase):
    def test_each_source_requests_only_robots_and_discovery(self):
        for module, _, item, wrap, mime in CASES:
            with self.subTest(source=module.__name__), patch.object(common, 'fetch', side_effect=[
                    ('User-agent: *\nDisallow:\nCrawl-delay: 7', 'text/plain'), (wrap(item), mime)]) as fetch, patch.object(common.time, 'sleep') as sleep:
                self.assertEqual(len(module.collect()), 1)
                self.assertEqual([c.args[0] for c in fetch.call_args_list],
                                 [common.urljoin(module.ENDPOINT, '/robots.txt'), module.ENDPOINT])
                sleep.assert_called_once_with(7)

    def test_denied_invalid_and_failed_access_preserves_each_output(self):
        failures = [ValueError('HTTP 403'), ('User-agent: *\nDisallow: /', 'text/plain'),
                    ('<html>Challenge</html>', 'text/html'), ('garbage', 'text/plain')]
        for module, _, item, wrap, mime in CASES:
            for failure in failures:
                with self.subTest(source=module.__name__, failure=failure), tempfile.TemporaryDirectory() as folder:
                    output = Path(folder)/'snapshot.json'
                    output.write_text('previous successful output')
                    with patch.object(module, 'OUTPUT', output), patch.object(common, 'fetch', side_effect=[failure]) as fetch, contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(module.main(), 1)
                    self.assertEqual(fetch.call_count, 1)
                    self.assertEqual(output.read_text(), 'previous successful output')
            for response in [('<html>Challenge</html>', mime), (wrap(item), 'text/plain')]:
                with tempfile.TemporaryDirectory() as folder:
                    output = Path(folder)/'snapshot.json'
                    output.write_text('previous')
                    with patch.object(module, 'OUTPUT', output), patch.object(common, 'fetch', side_effect=[('User-agent: *\nDisallow:', 'text/plain'), response]), patch.object(common.time, 'sleep'), contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(module.main(), 1)
                    self.assertEqual(output.read_text(), 'previous')

    def test_success_writes_required_provenance_for_each_item(self):
        for module, _, item, wrap, mime in CASES:
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder)/'snapshot.json'
                with patch.object(module, 'OUTPUT', output), patch.object(common, 'fetch', side_effect=[('User-agent: *\nDisallow:', 'text/plain'), (wrap(item), mime)]), patch.object(common.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(module.main(), 0)
                data = json.loads(output.read_text())
                self.assertEqual(data['item_count'], 1)
                self.assertTrue(all(data['items'][0][k] for k in ['institution', 'source_name', 'title', 'url', 'discovered_at']))

    def test_http_failures_have_no_retry_or_redirect(self):
        for status in ['301', '403', '429']:
            with patch.object(common.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, ('Denied\n'+status+'\ntext/html').encode())) as run:
                with self.assertRaises(ValueError):
                    common.fetch(abn.ENDPOINT)
                self.assertEqual(run.call_count, 1)
                self.assertNotIn('--location', run.call_args.args[0])

    def test_serialization_failure_preserves_previous_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'snapshot.json'
            common.write_output({'old': True}, output)
            before = output.read_bytes()
            with self.assertRaises(TypeError):
                common.write_output({'invalid': object()}, output)
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(list(Path(folder).iterdir()), [output])


if __name__ == '__main__':
    unittest.main()
