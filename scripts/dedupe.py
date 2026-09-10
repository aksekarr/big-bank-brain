"""Local URL-based dedupe spike. Reads snapshots only; no network or model calls."""
import fcntl
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote_plus, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
# These describe the existing collector outputs, not a production source registry.
SOURCES = (
    ('bbva-discovery.json', 'BBVA Research', 'BBVA Research',
     'https://www.bbvaresearch.com/en/publications/', '/en/publicaciones/'),
    ('abn-amro-discovery.json', 'ABN AMRO', 'ABN AMRO Group Economics',
     'https://www.abnamro.com/research/en/overview/our-research', '/research/en/our-research/'),
    ('nyfed-lse-discovery.json', 'Federal Reserve Bank of New York', 'Liberty Street Economics',
     'https://libertystreeteconomics.newyorkfed.org/feed/', '/'),
    ('bis-discovery.json', 'Bank for International Settlements', 'BIS and FSI publications',
     'https://www.bis.org/doclist/bis_fsi_publs.rss', '/publications/'),
)
TRACKING = {'gclid', 'dclid', 'fbclid', 'msclkid', 'mc_cid', 'mc_eid'}
STATE_NAME = 'dedupe-seen.json'
OUTPUT_NAME = 'dedupe-new.json'


def text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Expected non-empty text')
    return value


def timestamp(value):
    parsed = datetime.fromisoformat(text(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp must include timezone')
    return value


def normalise_url(value):
    value = text(value)
    if re.search(r'[\s\x00-\x1f\x7f\\]', value) or re.search(r'%(?![0-9a-fA-F]{2})', value):
        raise ValueError('Malformed publication URL')
    parts = urlsplit(value)
    if (parts.scheme != 'https' or parts.username is not None or parts.password is not None
            or parts.port not in (None, 443) or not parts.hostname):
        raise ValueError('Expected public HTTPS URL without credentials')
    host = parts.hostname.lower()
    matching = [source for source in SOURCES if urlsplit(source[3]).hostname == host]
    if not matching or not parts.path.startswith(matching[0][4]) or parts.path.lower().endswith('.pdf'):
        raise ValueError('URL does not belong to an approved publication source')
    if host == 'libertystreeteconomics.newyorkfed.org' and not re.fullmatch(r'/\d{4}/\d{2}/[^/]+/', parts.path):
        raise ValueError('Expected a dated LSE publication URL')
    # Keep unknown query components byte-for-byte and in order. Do not guess that
    # slashes, path case, query ordering or HTTP/HTTPS variants are interchangeable.
    kept = []
    for component in parts.query.split('&') if parts.query else []:
        key = unquote_plus(component.split('=', 1)[0]).lower()
        if not key.startswith('utm_') and key not in TRACKING:
            kept.append(component)
    return urlunsplit(('https', host, parts.path, '&'.join(kept), ''))


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key')
        result[key] = value
    return result


def read_json(path):
    def invalid_constant(value):
        raise ValueError('Non-finite JSON number')
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=object_pairs,
                      parse_constant=invalid_constant)


def load_discoveries(root, *, allow_missing_dates=False):
    unique = {}
    for filename, institution, source_name, endpoint, _ in SOURCES:
        data = read_json(root / filename)
        if not isinstance(data, dict) or not isinstance(data.get('items'), list):
            raise ValueError(f'{filename}: expected a discovery object with items')
        if type(data.get('item_count')) is not int or data['item_count'] != len(data['items']):
            raise ValueError(f'{filename}: item_count mismatch')
        discovered = timestamp(data.get('discovered_at'))
        bbva = filename == 'bbva-discovery.json'
        if data.get('listing_url' if bbva else 'discovery_url') != endpoint:
            raise ValueError(f'{filename}: incorrect discovery endpoint')
        if bbva and data.get('institution') != institution:
            raise ValueError('Incorrect BBVA institution')
        for raw in data['items']:
            if not isinstance(raw, dict):
                raise ValueError(f'{filename}: invalid item')
            url = normalise_url(raw.get('url'))
            if urlsplit(url).hostname != urlsplit(endpoint).hostname:
                raise ValueError(f'{filename}: item belongs to another source')
            item = {'institution': institution, 'source_name': source_name,
                    'title': text(raw.get('title')), 'url': url, 'discovered_at': discovered}
            if not bbva:
                if raw.get('institution') != institution or raw.get('source_name') != source_name:
                    raise ValueError(f'{filename}: incorrect item attribution')
                item['discovered_at'] = timestamp(raw.get('discovered_at'))
            if 'publication_date' in raw and not (allow_missing_dates and raw['publication_date'] is None):
                published = text(raw['publication_date'])
                if date.fromisoformat(published).isoformat() != published:
                    raise ValueError('Publication date must be YYYY-MM-DD')
                item['publication_date'] = published
            elif bbva and not allow_missing_dates:
                raise ValueError('BBVA item is missing its required publication date')
            excerpt_key = 'listing_summary' if bbva else 'excerpt'
            if excerpt_key in raw:
                item['excerpt'] = text(raw[excerpt_key])
            # Identity is the URL, even when a headline or standfirst has changed.
            # Deterministically retain the first encountered record within this run.
            unique.setdefault(url, item)
    return unique


def load_seen(path):
    if not path.exists():
        return {}
    data = read_json(path)
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('seen'), dict):
        raise ValueError('Malformed seen-state; refusing to reset it')
    for url, first_seen in data['seen'].items():
        if normalise_url(url) != url:
            raise ValueError('Seen-state contains a non-normalised URL')
        timestamp(first_seen)
    return data['seen']


def prepare_json(data, destination):
    """Serialize completely before either live file is replaced."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                         prefix='.dedupe-', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
        return temporary
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def dedupe(root=ROOT):
    # Lock the directory itself: no stale lock file and no overlapping local writers.
    # This small Unix-only guard matches the current macOS environment.
    descriptor = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return dedupe_locked(root)
    finally:
        os.close(descriptor)


def dedupe_locked(root):
    items = load_discoveries(root)
    seen = load_seen(root / STATE_NAME)
    now = datetime.now(timezone.utc).isoformat()
    new = [item for url, item in items.items() if url not in seen]
    output = {'deduplicated_at': now, 'input_files': [s[0] for s in SOURCES],
              'discovered_unique_count': len(items), 'new_item_count': len(new), 'items': new}
    updated = dict(seen)
    updated.update({item['url']: now for item in new})
    temporary = []
    try:
        new_file = prepare_json(output, root / OUTPUT_NAME)
        temporary.append(new_file)
        state_file = prepare_json({'version': 1, 'seen': updated}, root / STATE_NAME)
        temporary.append(state_file)
        new_file.replace(root / OUTPUT_NAME)
        # Commit marker is last: failure before this replacement cannot mark items seen.
        state_file.replace(root / STATE_NAME)
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
    return output


def main():
    try:
        result = dedupe()
    except (ValueError, OSError) as error:
        print(f'Dedupe failed: {error}. Seen-state was not advanced.', file=sys.stderr)
        return 1
    print(f"{result['new_item_count']} new publications from {result['discovered_unique_count']} unique discovered URLs.")
    print(f'Output: {ROOT / OUTPUT_NAME}\nSeen-state: {ROOT / STATE_NAME}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
