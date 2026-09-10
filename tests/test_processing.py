"""Synthetic eligibility/state tests, with an explicit clock and no network."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import processing as p

NOW = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)


def item(day='2026-09-10', suffix='example'):
    return {'institution': 'BBVA Research', 'source_name': 'BBVA Research',
            'title': 'Invented analysis', 'url': 'https://www.bbvaresearch.com/en/publicaciones/'+suffix+'/',
            'publication_date': day, 'discovered_at': NOW.isoformat()}


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def discoveries(self, records):
        return patch.object(p.dedupe, 'load_discoveries', return_value={i['url']: i for i in records})

    def test_first_backfill_boundaries_missing_future_and_repeat(self):
        records = [item(day, str(n)) for n, day in enumerate([
            '2026-09-10', '2026-09-04', '2026-09-03', '2020-01-01', '2026-09-11', None])]
        with self.discoveries(records):
            result = p.execute('prepare', self.root, NOW)
            self.assertEqual([i['publication_date'] for i in result['items']], ['2026-09-10', '2026-09-04'])
            self.assertEqual(result['excluded'], {'older': 2, 'future': 1, 'missing_date': 1, 'already_processed': 0})
            self.assertEqual(p.load_processed(self.root), {})
            self.assertEqual(p.execute('prepare', self.root, NOW)['ready_count'], 2)

    def test_success_excluded_but_failed_and_incomplete_stay_ready(self):
        with self.discoveries([item(), item(suffix='second')]):
            p.execute('prepare', self.root, NOW)
            for status in ['failed', 'incomplete']:
                p.execute('record', self.root, NOW, item()['url'], status)
                self.assertEqual(p.execute('prepare', self.root, NOW)['ready_count'], 2)
            p.execute('record', self.root, NOW, item()['url'], 'succeeded')
            result = p.execute('prepare', self.root, NOW)
            self.assertEqual(result['ready_count'], 1)
            self.assertEqual(result['items'][0]['url'], item(suffix='second')['url'])
            self.assertEqual(result['excluded']['already_processed'], 1)

    def test_london_calendar_boundary_and_dst(self):
        # 23:30 UTC is already the next date in London during summer.
        with self.discoveries([item('2026-09-11')]):
            result = p.execute('prepare', self.root, datetime(2026, 9, 10, 23, 30, tzinfo=timezone.utc))
            self.assertEqual(result['window']['to'], '2026-09-11')
            self.assertEqual(result['window']['from'], '2026-09-05')
            self.assertEqual(result['ready_count'], 1)
        with self.discoveries([item('2026-01-11')]):
            result = p.execute('prepare', self.root, datetime(2026, 1, 10, 23, 30, tzinfo=timezone.utc))
            self.assertEqual(result['window']['to'], '2026-01-10')
            self.assertEqual(result['ready_count'], 0)

    def test_malformed_dates_preserve_ready_and_state(self):
        with self.discoveries([item()]):
            p.execute('prepare', self.root, NOW)
        before = {name: (self.root/name).read_bytes() for name in [p.READY_NAME, p.STATE_NAME]}
        for bad in ['2026-02-30', '20260910', '', 'yesterday', 123]:
            with self.discoveries([item(bad)]), self.assertRaises(ValueError):
                p.execute('prepare', self.root, NOW)
            for name, content in before.items():
                self.assertEqual((self.root/name).read_bytes(), content)

    def test_real_loader_accepts_missing_dates_without_changing_dedupe_default(self):
        for filename, institution, source, endpoint, prefix in p.dedupe.SOURCES:
            url = 'https://' + p.dedupe.urlsplit(endpoint).hostname + prefix + 'example/'
            if source == 'Liberty Street Economics':
                url = 'https://libertystreeteconomics.newyorkfed.org/2026/09/example/'
            record = dict(item(), institution=institution, source_name=source, url=url)
            if filename.startswith('bbva'):
                del record['publication_date']
            elif filename.startswith('bis'):
                record['publication_date'] = None
            data = {'items': [record], 'item_count': 1, 'discovered_at': NOW.isoformat()}
            if filename.startswith('bbva'):
                data.update(institution=institution, listing_url=endpoint)
            else:
                data['discovery_url'] = endpoint
            (self.root/filename).write_text(json.dumps(data))
        result = p.execute('prepare', self.root, NOW)
        self.assertEqual(result['ready_count'], 2)
        self.assertEqual(result['excluded']['missing_date'], 2)
        self.assertEqual({i['institution'] for i in result['items']}, {'ABN AMRO', 'Federal Reserve Bank of New York'})
        with self.assertRaises(ValueError):
            p.dedupe.load_discoveries(self.root)
        path = self.root/'abn-amro-discovery.json'
        data = json.loads(path.read_text())
        data['items'][0]['publication_date'] = '2026-99-99'
        path.write_text(json.dumps(data))
        before = (self.root/p.READY_NAME).read_bytes()
        with self.assertRaises(ValueError):
            p.execute('prepare', self.root, NOW)
        self.assertEqual((self.root/p.READY_NAME).read_bytes(), before)

    def test_prepare_write_failure_and_concurrent_run(self):
        with self.discoveries([item()]):
            p.execute('prepare', self.root, NOW)
            before = {name: (self.root/name).read_bytes() for name in [p.READY_NAME, p.STATE_NAME]}
            with patch.object(Path, 'replace', side_effect=OSError('write failed')), self.assertRaises(OSError):
                p.execute('prepare', self.root, NOW)
            for name, content in before.items():
                self.assertEqual((self.root/name).read_bytes(), content)
            self.assertEqual(list(self.root.glob('.dedupe-*.tmp')), [])
            descriptor = p.os.open(self.root, p.os.O_RDONLY)
            try:
                p.fcntl.flock(descriptor, p.fcntl.LOCK_EX | p.fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    p.execute('prepare', self.root, NOW)
            finally:
                p.os.close(descriptor)

    def ready_fixture(self):
        (self.root/p.READY_NAME).write_text(json.dumps({'prepared_at': NOW.isoformat(), 'ready_count': 1, 'items': [item()]}))

    def test_success_records_normalised_url_and_is_idempotent(self):
        self.ready_fixture()
        result = p.execute('record', self.root, NOW, item()['url']+'?utm_source=test#part', 'succeeded')
        self.assertTrue(result['state_updated'])
        self.assertEqual(p.load_processed(self.root), {item()['url']: NOW.isoformat()})
        before = (self.root/p.STATE_NAME).read_bytes()
        self.assertFalse(p.execute('record', self.root, NOW, item()['url'], 'succeeded')['state_updated'])
        self.assertEqual((self.root/p.STATE_NAME).read_bytes(), before)

    def test_failed_or_incomplete_never_marks_processed(self):
        for status in ['failed', 'incomplete']:
            result = p.execute('record', self.root, NOW, item()['url'], status)
            self.assertFalse(result['state_updated'])
            self.assertFalse((self.root/p.STATE_NAME).exists())
        self.ready_fixture()
        p.execute('record', self.root, NOW, item()['url'], 'succeeded')
        before = (self.root/p.STATE_NAME).read_bytes()
        p.execute('record', self.root, NOW, item(suffix='different')['url'], 'failed')
        self.assertEqual((self.root/p.STATE_NAME).read_bytes(), before)

    def test_unknown_success_and_corrupt_state_rejected(self):
        self.ready_fixture()
        with self.assertRaises(ValueError):
            p.execute('record', self.root, NOW, item(suffix='unknown')['url'], 'succeeded')
        path = self.root/p.STATE_NAME
        path.write_text('{broken')
        with self.assertRaises(ValueError):
            p.execute('record', self.root, NOW, item()['url'], 'succeeded')
        self.assertEqual(path.read_text(), '{broken')

    def test_failed_state_write_preserves_previous_state(self):
        self.ready_fixture()
        path = self.root/p.STATE_NAME
        path.write_text('{"version":1,"processed":{}}')
        before = path.read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('Synthetic failure')), self.assertRaises(OSError):
            p.execute('record', self.root, NOW, item()['url'], 'succeeded')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.dedupe-*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
