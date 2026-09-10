"""Synthetic four-source snapshots; no network calls or publisher fixtures."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dedupe as d

STAMP = '2026-09-10T12:00:00+00:00'


def seed(root):
    for filename, institution, source, endpoint, prefix in d.SOURCES:
        url = 'https://' + d.urlsplit(endpoint).hostname + prefix + 'example/'
        if source == 'Liberty Street Economics':
            url = 'https://libertystreeteconomics.newyorkfed.org/2026/09/example/'
        item = {'title': 'Invented analysis', 'url': url, 'publication_date': '2026-09-10'}
        data = {'discovered_at': STAMP, 'item_count': 1, 'items': [item]}
        if filename.startswith('bbva'):
            data.update(institution=institution, listing_url=endpoint)
            item['listing_summary'] = 'Invented listing excerpt.'
        else:
            data['discovery_url'] = endpoint
            item.update(institution=institution, source_name=source, discovered_at=STAMP)
        (root / filename).write_text(json.dumps(data))


class DedupeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        seed(self.root)

    def change_bbva(self, change):
        path = self.root / 'bbva-discovery.json'
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data))

    def test_first_second_and_one_added_item(self):
        first = d.dedupe(self.root)
        self.assertEqual(first['new_item_count'], 4)
        self.assertEqual(len({i['source_name'] for i in first['items']}), 4)
        self.assertEqual(first['items'][0]['excerpt'], 'Invented listing excerpt.')
        before = (self.root / d.STATE_NAME).read_bytes()
        self.assertEqual(d.dedupe(self.root)['items'], [])
        self.assertEqual((self.root / d.STATE_NAME).read_bytes(), before)
        def add(data):
            data['items'].append(dict(data['items'][0], url=data['items'][0]['url']+'new/'))
            data['item_count'] += 1
        self.change_bbva(add)
        result = d.dedupe(self.root)
        self.assertEqual(result['new_item_count'], 1)
        self.assertTrue(result['items'][0]['url'].endswith('/new/'))
        self.assertEqual(len(d.load_seen(self.root / d.STATE_NAME)), 5)

    def test_equivalent_urls_and_changed_titles_are_not_new(self):
        def duplicate(data):
            original = data['items'][0]
            data['items'].append(dict(original, url=original['url'].replace('www.bbvaresearch.com', 'WWW.BBVARESEARCH.COM:443')+'?utm_source=test&fbclid=abc#section', title='Updated title'))
            data['item_count'] += 1
        self.change_bbva(duplicate)
        self.assertEqual(d.dedupe(self.root)['new_item_count'], 4)
        self.assertEqual(d.dedupe(self.root)['new_item_count'], 0)

    def test_destination_affecting_url_parts_preserved(self):
        base = 'https://www.bis.org/publications/Example/'
        self.assertEqual(d.normalise_url(base+'?lang=en&utm_campaign=x&id=2%2F3#part'), base+'?lang=en&id=2%2F3')
        for other in [base.rstrip('/'), base.lower(), base+'?id=1', base+'?id=2']:
            self.assertNotEqual(d.normalise_url(base), d.normalise_url(other))
        for bad in ['https://example.com/a', base+'?bad=%ZZ', 'https://user@www.bis.org/publications/a', base+'\n', 'http://www.bis.org/publications/a']:
            with self.assertRaises(ValueError):
                d.normalise_url(bad)

    def test_all_inputs_validated_before_any_write(self):
        d.dedupe(self.root)
        before = (self.root / d.STATE_NAME).read_bytes()
        output = (self.root / d.OUTPUT_NAME).read_bytes()
        invalid = [None, {}, {'items': 'wrong'}, {'items': [], 'item_count': True}]
        path = self.root / 'bis-discovery.json'
        for value in invalid:
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                d.dedupe(self.root)
            self.assertEqual((self.root / d.STATE_NAME).read_bytes(), before)
            self.assertEqual((self.root / d.OUTPUT_NAME).read_bytes(), output)
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            d.dedupe(self.root)
        self.assertEqual((self.root / d.STATE_NAME).read_bytes(), before)

    def test_invalid_item_date_attribution_and_json(self):
        for key, value in [('url', 'https://example.com/a'), ('title', ''), ('publication_date', '2026-02-30')]:
            seed(self.root)
            self.change_bbva(lambda data: data['items'][0].update({key: value}))
            with self.assertRaises(ValueError):
                d.dedupe(self.root)
            self.assertFalse((self.root / d.STATE_NAME).exists())
        seed(self.root)
        path = self.root / 'bis-discovery.json'
        data = json.loads(path.read_text())
        data['items'][0]['institution'] = 'Wrong institution'
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            d.dedupe(self.root)
        for raw in ['{broken', '{"items": [], "items": []}', '{"items": NaN}']:
            path.write_text(raw)
            with self.assertRaises(ValueError):
                d.dedupe(self.root)

    def test_corrupt_seen_state_is_never_reset(self):
        path = self.root / d.STATE_NAME
        for content in ['broken', '{"version":1,"seen":[]}', '{"version":1,"seen":{"invalid":"date"}}']:
            path.write_text(content)
            with self.assertRaises(ValueError):
                d.dedupe(self.root)
            self.assertEqual(path.read_text(), content)

    def test_write_failures_do_not_advance_seen_and_retry_recovers(self):
        # Cover both first-run and existing-state failures at each replacement.
        for existing in [False, True]:
            for fail_target in [d.OUTPUT_NAME, d.STATE_NAME]:
                with tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    seed(root)
                    state = root / d.STATE_NAME
                    if existing:
                        state.write_text('{"version":1,"seen":{}}')
                    before = state.read_bytes() if existing else None
                    replace = Path.replace
                    def fail(path, target):
                        if target.name == fail_target:
                            raise OSError('Synthetic write failure')
                        return replace(path, target)
                    with patch.object(Path, 'replace', fail), self.assertRaises(OSError):
                        d.dedupe(root)
                    self.assertEqual(state.read_bytes() if state.exists() else None, before)
                    self.assertEqual(list(root.glob('.dedupe-*.tmp')), [])
                    self.assertEqual(d.dedupe(root)['new_item_count'], 4)

    def test_preparation_failure_changes_neither_file(self):
        d.dedupe(self.root)
        before = {name: (self.root/name).read_bytes() for name in [d.STATE_NAME, d.OUTPUT_NAME]}
        prepare = d.prepare_json
        def fail(data, destination):
            if destination.name == d.STATE_NAME:
                raise OSError('Synthetic preparation failure')
            return prepare(data, destination)
        with patch.object(d, 'prepare_json', fail), self.assertRaises(OSError):
            d.dedupe(self.root)
        for name, content in before.items():
            self.assertEqual((self.root/name).read_bytes(), content)
        self.assertEqual(list(self.root.glob('.dedupe-*.tmp')), [])

    def test_overlapping_run_is_rejected(self):
        descriptor = d.os.open(self.root, d.os.O_RDONLY)
        try:
            d.fcntl.flock(descriptor, d.fcntl.LOCK_EX | d.fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                d.dedupe(self.root)
            self.assertFalse((self.root/d.STATE_NAME).exists())
        finally:
            d.os.close(descriptor)


if __name__ == '__main__':
    unittest.main()
