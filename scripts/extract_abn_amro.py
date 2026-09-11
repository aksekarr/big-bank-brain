"""One-item ABN extraction; default metadata preview makes no network requests."""
import argparse
import fcntl
import json
import os
import sys
from datetime import datetime, timezone

import extract_bbva as ai
import processing
import read_abn_amro as read

LEDGER = 'abn-amro-ai-spike-ledger.json'
RESULTS = 'abn-amro-ai-extractions.json'
# One schema, validator, client and budget implementation; only source framing differs.
INSTRUCTIONS = ai.INSTRUCTIONS.replace('BBVA article-page text', 'ABN AMRO research text').replace(
    'The input is HTML summary and key\npoints, not the complete linked report.',
    'The input is public article prose or a listing excerpt, as labelled below. '
    'Charts, tables and linked reports are not included. A short listing excerpt '
    'may support only a limited claim; do not fill gaps with outside knowledge.')


def load_ledger(root):
    return ai.load_ledger(root, ledger_name=LEDGER)


def run_spike(client, root=processing.ROOT, now=None, limit=1, url=None):
    if limit != 1:
        raise ai.SpikeError('ABN spike permits exactly one article per invocation')
    now = now or datetime.now(timezone.utc)
    descriptor = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = load_ledger(root)
        items = read.select(root, now, limit, url=url)
        if not items:
            return []
        if ai.held_calls(ledger) >= ai.MAX_CALLS:
            raise ai.SpikeError('ABN three-call allowance exhausted; review required')
        path = root / RESULTS
        saved = ai.dedupe.read_json(path) if path.exists() else {'version': 1, 'items': {}}
        if not isinstance(saved, dict) or saved.get('version') != 1 or not isinstance(saved.get('items'), dict):
            raise ai.SpikeError('Invalid saved ABN extraction file')
        for record in saved['items'].values():
            ai.validate(record['extraction'])
        # Refresh the acknowledgement input; the stale ready-list never selects items.
        processing.prepare(root, now)

        def consume(item, text, source):
            instructions = INSTRUCTIONS + '\nInput text source: ' + source + '.'
            extraction = ai.extract_one(client, item, text, root, now, ledger,
                                        ledger_name=LEDGER, instructions=instructions)
            record = {k: item[k] for k in
                      ['institution', 'source_name', 'title', 'url', 'publication_date']}
            record.update(extraction=extraction, model=ai.MODEL,
                          extracted_at=datetime.now(timezone.utc).isoformat(),
                          read_metadata={'text_source': source,
                                         'listing_fallback_used': source == 'listing_excerpt',
                                         'character_count': len(text),
                                         'approximate_word_count': len(text.split()),
                                         'charts_tables_linked_reports_extracted': False})
            saved['items'][item['url']] = record
            processing.write_json(saved, path)
            processing.acknowledge(root, item['url'], 'succeeded', datetime.now(timezone.utc))

        return read.run(items, on_text=consume)
    finally:
        os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Paid call; requires Avi approval')
    parser.add_argument('--limit', type=int, choices=[1], default=1)
    parser.add_argument('--url', help='Exact discovery URL; must be eligible and unprocessed')
    args = parser.parse_args(argv)
    try:
        if args.live:
            with ai.make_client() as client:
                results = run_spike(client, limit=args.limit, url=args.url)
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return 0 if all(r['text_available'] for r in results) else 1
        items = read.select(limit=1, url=args.url)
        ledger = load_ledger(processing.ROOT)
        print(json.dumps({'mode': 'offline_plan', 'model': ai.MODEL,
                          'remaining_calls': ai.MAX_CALLS-ai.held_calls(ledger),
                          'selected': [{k: i[k] for k in ['title', 'url', 'publication_date']}
                                       for i in items]}, ensure_ascii=False, indent=2))
        return 0
    except ai.SpikeError as error:
        print(f'ABN extraction stopped: {error}. Item remains unprocessed.', file=sys.stderr)
        return 1
    except Exception:
        print('ABN extraction stopped; no raw content or provider errors logged.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
