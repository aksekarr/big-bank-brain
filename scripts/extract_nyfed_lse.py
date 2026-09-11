"""One-item Liberty Street extraction; default metadata preview makes no network requests."""
import argparse
import fcntl
import json
import os
import sys
from datetime import datetime, timezone

import extract_bbva as ai
import processing
import read_nyfed_lse as read

LEDGER = 'nyfed-lse-ai-spike-ledger.json'
RESULTS = 'nyfed-lse-ai-extractions.json'
MAX_REQUEST_BYTES = 16000
# Existing conservative formula in micro-USD: input bytes + framing at $2.50/M,
# plus 2,000 output tokens at $12/M. Integer arithmetic keeps reservations exact.
RESERVE_MICRO_USD = (MAX_REQUEST_BYTES + 1024) * 5 // 2 + ai.MAX_OUTPUT_TOKENS * 12
BUDGET_MICRO_USD = 200000
# One schema, validator, client and budget implementation; only source framing differs.
INSTRUCTIONS = ai.INSTRUCTIONS.replace('BBVA article-page text', 'Liberty Street research text').replace(
    'The input is HTML summary and key\npoints, not the complete linked report.',
    'The input is publication-page research summary or an RSS excerpt, as labelled below. '
    'Charts, tables and linked reports are not included. A short RSS excerpt '
    'may support only a limited claim; do not fill gaps with outside knowledge. '
    'Liberty Street posts may express named authors’ analysis rather than an official '
    'Federal Reserve Bank of New York position. Preserve attribution where it matters: '
    'use “the authors find”, “the analysis finds” or “the post argues/suggests”. '
    'Do not infer an official institutional position merely from publication on Liberty Street.')


def load_ledger(root):
    return ai.load_ledger(root, ledger_name=LEDGER, reserve_micro_usd=RESERVE_MICRO_USD)


def run_spike(client, root=processing.ROOT, now=None, limit=1, url=None):
    if limit != 1:
        raise ai.SpikeError('Liberty Street spike permits exactly one article per invocation')
    now = now or datetime.now(timezone.utc)
    descriptor = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = load_ledger(root)
        items = read.select(root, now, limit, url=url)
        if not items:
            return []
        if ai.held_calls(ledger) >= ai.MAX_CALLS:
            raise ai.SpikeError('Liberty Street three-call allowance exhausted; review required')
        path = root / RESULTS
        saved = ai.dedupe.read_json(path) if path.exists() else {'version': 1, 'items': {}}
        if not isinstance(saved, dict) or saved.get('version') != 1 or not isinstance(saved.get('items'), dict):
            raise ai.SpikeError('Invalid saved Liberty Street extraction file')
        for record in saved['items'].values():
            ai.validate(record['extraction'])
        # Refresh the acknowledgement input; the stale ready-list never selects items.
        processing.prepare(root, now)

        def consume(item, text, source):
            instructions = INSTRUCTIONS + '\nInput text source: ' + source + '.'
            extraction = ai.extract_one(client, item, text, root, now, ledger,
                                        ledger_name=LEDGER, instructions=instructions,
                                        max_request_bytes=MAX_REQUEST_BYTES,
                                        reserve_micro_usd=RESERVE_MICRO_USD,
                                        budget_micro_usd=BUDGET_MICRO_USD)
            record = {k: item[k] for k in
                      ['institution', 'source_name', 'title', 'url', 'publication_date']}
            record.update(extraction=extraction, model=ai.MODEL,
                          extracted_at=datetime.now(timezone.utc).isoformat(),
                          read_metadata={'text_source': source,
                                         'rss_fallback_used': source == 'rss_excerpt',
                                         'complete_paper_extracted': False,
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
        print(f'Liberty Street extraction stopped: {error}. Item remains unprocessed.', file=sys.stderr)
        return 1
    except Exception:
        print('Liberty Street extraction stopped; no raw content or provider errors logged.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
