"""Small I/O and metadata helpers for the three additional discovery spikes."""
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

USER_AGENT = 'BigBankBrain/0.1 (public metadata discovery)'
ROOT = Path(__file__).resolve().parents[1]
# Local spike limits; the LSE feed includes unused content fields and is larger.
MAX_BYTES = 8_000_000


def fetch(url):
    result = subprocess.run(
        ['curl', '--silent', '--show-error', '--noproxy', '*',
         '--max-time', '30', '--max-filesize', str(MAX_BYTES),
         '--user-agent', USER_AGENT, url,
         '--write-out', '\n%{http_code}\n%{content_type}'],
        capture_output=True, timeout=35)
    if result.returncode:
        raise ValueError(f'Request failed (curl exit {result.returncode}): {url}')
    body, status, mime = result.stdout.rsplit(b'\n', 2)
    if status != b'200':
        raise ValueError(f'HTTP {status.decode()} for {url}; no retry')
    if len(body) > MAX_BYTES:
        raise ValueError('Response exceeds spike size limit')
    return body.decode('utf-8-sig'), mime.decode().split(';')[0].strip().lower()


def retrieve(endpoint, media_types):
    robots_url = urljoin(endpoint, '/robots.txt')
    robots, mime = fetch(robots_url)
    if mime != 'text/plain' or '<html' in robots.lower():
        raise ValueError('Unexpected robots response; access not assumed')
    if not re.search(r'^\s*user-agent\s*:', robots, re.I | re.M):
        raise ValueError('No recognisable robots rules; manual review needed')
    rules = RobotFileParser(robots_url)
    rules.parse(robots.splitlines())
    if not rules.can_fetch(USER_AGENT, endpoint):
        raise ValueError('robots.txt denies discovery endpoint')
    delay = max(3, rules.crawl_delay(USER_AGENT) or 0)
    rate = rules.request_rate(USER_AGENT)
    if rate:
        delay = max(delay, rate.seconds / rate.requests)
    time.sleep(delay)
    body, mime = fetch(endpoint)
    if mime not in media_types:
        raise ValueError(f'Unexpected discovery content type: {mime}')
    return body


def public_url(value, endpoint, path_prefix):
    if not value or re.search(r'[\s\x00-\x1f\x7f]', value):
        raise ValueError('Missing or malformed publication URL')
    url = urlsplit(urljoin(endpoint, value))
    if (url.scheme != 'https' or url.netloc != urlsplit(endpoint).netloc
            or not url.path.startswith(path_prefix)
            or url.path.lower().endswith('.pdf')):
        raise ValueError('Unexpected public publication URL')
    return urlunsplit(url._replace(fragment=''))


def unique_items(items):
    unique = {}
    for item in items:
        if not item.get('title') or not item.get('url'):
            raise ValueError('Missing title or URL')
        if item['url'] in unique and unique[item['url']] != item:
            raise ValueError('Conflicting metadata for duplicate URL')
        unique[item['url']] = item
    if not unique:
        raise ValueError('No publications parsed; discovery format may have changed')
    return sorted(unique.values(), key=lambda x: x.get('publication_date', ''), reverse=True)


class ExcerptText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.hidden += 1
        if tag in {'p', 'div', 'br', 'li'}:
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'} and self.hidden:
            self.hidden -= 1
        if tag in {'p', 'div', 'li'}:
            self.parts.append(' ')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def excerpt_text(value):
    parser = ExcerptText()
    parser.feed(value)
    parser.close()
    return ' '.join(''.join(parser.parts).split())


def write_output(data, output):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent,
                                         prefix='.discovery-', suffix='.json', delete=False) as f:
            temporary = Path(f.name)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n')
        temporary.replace(output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def run(endpoint, institution, source_name, output, collect):
    try:
        items = unique_items(collect())
        timestamp = datetime.now(timezone.utc).isoformat()
        for item in items:
            item.update(institution=institution, source_name=source_name, discovered_at=timestamp)
        data = {'discovery_url': endpoint, 'discovered_at': timestamp,
                'scope': 'Current discovery response only; no pagination or date-window filter.',
                'excerpt_provenance': 'Publisher listing/RSS excerpt when supplied; not a generated summary.',
                'robots_check': {'url': urljoin(endpoint, '/robots.txt'), 'discovery_allowed': True},
                'item_count': len(items), 'items': items}
        write_output(data, output)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'{source_name} discovery failed: {error}. Previous output unchanged.', file=sys.stderr)
        return 1
    print(f"Wrote {len(items)} unique publications ({sum('excerpt' in i for i in items)} excerpts) to {output}")
    print(f'Fetched at {timestamp}')
    return 0
