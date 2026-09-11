"""Read-only deterministic synthesis inputs; no publisher reads or model requests."""
import argparse
import fcntl
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import dedupe
import processing
from extract_bbva import validate

FILES = (
    ('bbva-ai-extractions.json', 'BBVA Research', 'BBVA Research'),
    ('abn-amro-ai-extractions.json', 'ABN AMRO', 'ABN AMRO Group Economics'),
    ('bis-ai-extractions.json', 'Bank for International Settlements', 'BIS and FSI publications'),
    ('nyfed-lse-ai-extractions.json', 'Federal Reserve Bank of New York', 'Liberty Street Economics'),
)


def window_date(value=None):
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError('Current time must include timezone')
        return value.astimezone(ZoneInfo('Europe/London')).date()
    if type(value) is date:
        return value
    raise ValueError('Expected a date or timezone-aware datetime')


def depth(record):
    result = {}
    if 'input_scope' in record:
        result['scope'] = dedupe.text(record['input_scope'])
    if 'read_metadata' not in record:
        return result
    raw = record['read_metadata']
    if not isinstance(raw, dict):
        raise ValueError('Invalid READ metadata')
    if 'text_source' in raw:
        if raw['text_source'] not in {'article_html', 'publication_page_html', 'article_body_html',
                                      'listing_excerpt', 'rss_excerpt', 'none'}:
            raise ValueError('Unknown READ source')
        result['text_source'] = raw['text_source']
    for key in ['listing_fallback_used', 'rss_fallback_used', 'complete_paper_extracted',
                'charts_tables_linked_reports_extracted']:
        if key in raw:
            if type(raw[key]) is not bool:
                raise ValueError('Invalid READ flag')
            result[key] = raw[key]
    for key in ['character_count', 'approximate_word_count']:
        if key in raw:
            if type(raw[key]) is not int or raw[key] < 0:
                raise ValueError('Invalid READ count')
            result[key] = raw[key]
    return result


def article(record, key, institution, source):
    if not isinstance(record, dict):
        raise ValueError('Invalid record')
    url = dedupe.normalise_url(record.get('url'))
    if dedupe.normalise_url(key) != url:
        raise ValueError('Record key/URL mismatch')
    owner = next(s for s in dedupe.SOURCES if dedupe.urlsplit(s[3]).hostname == dedupe.urlsplit(url).hostname)
    if (record.get('institution'), record.get('source_name')) != (institution, source) or owner[1:3] != (institution, source):
        raise ValueError('Incorrect attribution')
    published = processing.publication_date(record)
    if published is None:
        raise ValueError('Missing date')
    semantics = validate(record.get('extraction'))
    # Explicit projection excludes raw/unknown fields and request envelopes.
    return dict(institution=institution, source_name=source,
                title=dedupe.text(record.get('title')), url=url,
                publication_date=published.isoformat(), **semantics, read_depth=depth(record))


def assemble(root=processing.ROOT, now=None):
    today = window_date(now)
    start = today - timedelta(days=6)
    descriptor = os.open(root, os.O_RDONLY)
    try:
        # Readers must not observe the middle of a supported extraction/state write.
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        processed = processing.load_processed(root)
        diagnostics = []
        by_url = {}
        found = set()
        blocked = set()
        if not (root / processing.STATE_NAME).exists():
            diagnostics.append({'reason': 'missing_processed_state'})
        for filename, institution, source in FILES:
            path = root / filename
            if not path.exists():
                diagnostics.append({'file': filename, 'reason': 'missing_extraction_file'})
                continue
            try:
                data = dedupe.read_json(path)
                if (not isinstance(data, dict) or type(data.get('version')) is not int
                        or data['version'] != 1 or not isinstance(data.get('items'), dict)):
                    raise ValueError('Invalid extraction envelope')
            except (ValueError, OSError):
                diagnostics.append({'file': filename, 'reason': 'invalid_extraction_file'})
                continue
            for key, record in sorted(data['items'].items()):
                try:
                    url = dedupe.normalise_url(key)
                except ValueError:
                    diagnostics.append({'file': filename, 'reason': 'invalid_record_key'})
                    continue
                found.add(url)
                try:
                    value = article(record, key, institution, source)
                except (ValueError, TypeError, KeyError, StopIteration):
                    blocked.add(url)
                    diagnostics.append({'file': filename, 'url': url, 'reason': 'invalid_article'})
                    continue
                if url in by_url and by_url[url] != value:
                    blocked.add(url)
                    diagnostics.append({'url': url, 'reason': 'conflicting_duplicate'})
                elif url in by_url:
                    diagnostics.append({'url': url, 'reason': 'exact_duplicate'})
                else:
                    by_url[url] = value
        for url in sorted(set(processed)-found):
            diagnostics.append({'url': url, 'reason': 'processed_without_extraction'})
        selected = []
        for url, value in sorted(by_url.items()):
            if url in blocked:
                continue
            if url not in processed:
                diagnostics.append({'url': url, 'reason': 'not_processed'})
            elif not start.isoformat() <= value['publication_date'] <= today.isoformat():
                diagnostics.append({'url': url, 'reason': 'outside_window'})
            else:
                selected.append(value)
        selected.sort(key=lambda a: (-date.fromisoformat(a['publication_date']).toordinal(),
                                     a['institution'], a['title'], a['url']))
        for n, value in enumerate(selected, 1):
            ref = 'a'+str(n)
            value['article_ref'] = ref
            value['claims'] = [{'ref': ref+':c'+str(i), 'text': text}
                               for i, text in enumerate(value['claims'], 1)]
        counts = {institution: sum(a['institution'] == institution for a in selected)
                  for _, institution, _ in FILES}
        diagnostics.sort(key=lambda d: json.dumps(d, sort_keys=True))
        return {'window': {'timezone': 'Europe/London', 'start_date': start.isoformat(),
                           'end_date': today.isoformat()},
                'coverage': {'article_count': len(selected),
                             'institution_count': sum(n > 0 for n in counts.values()),
                             'per_institution': dict(sorted(counts.items()))},
                'articles': selected, 'diagnostics': diagnostics}
    finally:
        os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', help='Explicit London calendar date, YYYY-MM-DD')
    args = parser.parse_args(argv)
    try:
        day = date.fromisoformat(args.date) if args.date else None
        if args.date and day.isoformat() != args.date:
            raise ValueError('Invalid date')
        output = assemble(now=day)
    except (ValueError, OSError):
        print('Assembly stopped: invalid date, processed state or unavailable local files/lock.', file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
