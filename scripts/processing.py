"""Local eligibility and successful-processing acknowledgements; no network or AI."""
import argparse
import fcntl
import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import dedupe

ROOT = dedupe.ROOT
STATE_NAME = 'processed-state.json'
READY_NAME = 'ready-for-processing.json'


def load_processed(root):
    path = root / STATE_NAME
    if not path.exists():
        return {}
    data = dedupe.read_json(path)
    if (not isinstance(data, dict) or type(data.get('version')) is not int
            or data['version'] != 1 or not isinstance(data.get('processed'), dict)):
        raise ValueError('Invalid processed state; refusing to reset it')
    for url, stamp in data['processed'].items():
        if dedupe.normalise_url(url) != url:
            raise ValueError('Invalid processed URL')
        dedupe.timestamp(stamp)
    return data['processed']


def write_json(data, path):
    temporary = dedupe.prepare_json(data, path)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def publication_date(item):
    value = item.get('publication_date')
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('Invalid publication date')
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('Publication date must be YYYY-MM-DD')
    return parsed



def prepare(root, now):
    # Reuse discovery validation without treating discovery-seen as processed.
    items = dedupe.load_discoveries(root, allow_missing_dates=True)
    processed = load_processed(root)
    today = now.astimezone(ZoneInfo('Europe/London')).date()
    start = today - timedelta(days=6)
    ready = []
    excluded = {'older': 0, 'future': 0, 'missing_date': 0, 'already_processed': 0}
    for url, item in items.items():
        published = publication_date(item)
        if published is None:
            excluded['missing_date'] += 1
        elif published < start:
            excluded['older'] += 1
        elif published > today:
            excluded['future'] += 1
        elif url in processed:
            excluded['already_processed'] += 1
        else:
            ready.append(item)
    output = {'prepared_at': now.isoformat(),
              'window': {'from': start.isoformat(), 'to': today.isoformat(),
                         'timezone': 'Europe/London', 'inclusive': True},
              'discovered_count': len(items), 'excluded': excluded,
              'ready_count': len(ready), 'items': ready}
    temporary = []
    try:
        ready_file = dedupe.prepare_json(output, root / READY_NAME)
        temporary.append(ready_file)
        state_file = None
        if not (root / STATE_NAME).exists():
            state_file = dedupe.prepare_json({'version': 1, 'processed': {}}, root / STATE_NAME)
            temporary.append(state_file)
        ready_file.replace(root / READY_NAME)
        if state_file is not None:
            state_file.replace(root / STATE_NAME)
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
    return output


def acknowledge(root, url, status, now):
    if status not in {'succeeded', 'failed', 'incomplete'}:
        raise ValueError('Unknown processing result')
    processed = load_processed(root)
    url = dedupe.normalise_url(url)
    if status != 'succeeded':
        return {'status': status, 'state_updated': False}
    if url in processed:
        return {'status': 'already_processed', 'state_updated': False}
    ready = dedupe.read_json(root / READY_NAME)
    if (not isinstance(ready, dict) or not isinstance(ready.get('items'), list)
            or type(ready.get('ready_count')) is not int
            or ready['ready_count'] != len(ready['items'])):
        raise ValueError('Invalid ready output')
    dedupe.timestamp(ready.get('prepared_at'))
    candidates = []
    for item in ready['items']:
        if not isinstance(item, dict):
            raise ValueError('Invalid ready item')
        item_url = dedupe.normalise_url(item.get('url'))
        source = next(s for s in dedupe.SOURCES if dedupe.urlsplit(s[3]).hostname == dedupe.urlsplit(item_url).hostname)
        if item.get('institution') != source[1] or item.get('source_name') != source[2]:
            raise ValueError('Invalid ready attribution')
        dedupe.text(item.get('title'))
        if publication_date(item) is None:
            raise ValueError('Ready item lacks a publication date')
        candidates.append(item_url)
    if url not in candidates:
        raise ValueError('Cannot acknowledge an item not in the ready output')
    processed[url] = now.isoformat()
    write_json({'version': 1, 'processed': processed}, root / STATE_NAME)
    return {'status': 'succeeded', 'state_updated': True}


def execute(action, root=ROOT, now=None, url=None, status=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Current time must include timezone')
    descriptor = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action == 'prepare':
            return prepare(root, now)
        if action == 'record':
            return acknowledge(root, url, status, now)
        raise ValueError('Unknown action')
    finally:
        os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('prepare', help='Write eligible, not-yet-processed metadata')
    record = commands.add_parser('record', help='Explicitly acknowledge a downstream result')
    record.add_argument('--url', required=True)
    record.add_argument('--status', choices=['succeeded', 'failed', 'incomplete'], required=True)
    args = parser.parse_args(argv)
    try:
        result = execute(args.action, url=getattr(args, 'url', None), status=getattr(args, 'status', None))
    except (ValueError, OSError) as error:
        print(f'Processing-state command failed: {error}', file=sys.stderr)
        return 1
    if args.action == 'prepare':
        print(f"{result['ready_count']} ready publications; window {result['window']['from']} to {result['window']['to']} inclusive.")
        print(f'Ready output: {ROOT / READY_NAME}\nProcessed state: {ROOT / STATE_NAME}')
    else:
        print(f"Result: {result['status']}; processed state updated: {result['state_updated']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
