"""BBVA READ feasibility: three HTML pages at most, memory-only text, safe stdout."""
import json
import re
import subprocess
import sys
import time
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / 'bbva-discovery.json'
ROBOTS = 'https://www.bbvaresearch.com/robots.txt'
USER_AGENT = 'BigBankBrain/0.1 (BBVA public research READ feasibility)'
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


def fetch(url):
    # Capture only in memory: no output file, cookies, redirect following or retries.
    result = subprocess.run(
        ['curl', '--silent', '--show-error', '--noproxy', '*', '--max-time', '20',
         '--max-filesize', '2000000', '--user-agent', USER_AGENT, url,
         '--write-out', '\n%{http_code}\n%{content_type}'],
        capture_output=True, timeout=25)
    if result.returncode:
        raise ValueError('request_failed')
    body, status, mime = result.stdout.rsplit(b'\n', 2)
    if status != b'200':
        raise ValueError('http_not_200')
    if len(body) > 2_000_000:
        raise ValueError('response_too_large')
    return body.decode('utf-8-sig'), mime.decode().split(';')[0].strip().lower()


def select_items(path=SNAPSHOT):
    data = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or data.get('institution') != 'BBVA Research'
            or data.get('listing_url') != 'https://www.bbvaresearch.com/en/publications/'
            or not isinstance(data.get('items'), list)
            or type(data.get('item_count')) is not int
            or data['item_count'] != len(data['items'])):
        raise ValueError('invalid_snapshot')
    items = {}
    for raw in data['items']:
        if not isinstance(raw, dict):
            raise ValueError('invalid_item')
        for field in ['title', 'url', 'publication_date']:
            if not isinstance(raw.get(field), str) or not raw[field].strip():
                raise ValueError('missing_metadata')
        if date.fromisoformat(raw['publication_date']).isoformat() != raw['publication_date']:
            raise ValueError('invalid_date')
        if re.search(r'[\s\x00-\x1f\x7f\\]', raw['url']):
            raise ValueError('invalid_url')
        url = urlsplit(raw['url'])
        if (url.scheme != 'https' or url.netloc != 'www.bbvaresearch.com'
                or not url.path.startswith('/en/publicaciones/') or url.query):
            raise ValueError('unexpected_url')
        if 'listing_summary' in raw and not isinstance(raw['listing_summary'], str):
            raise ValueError('invalid_listing_summary')
        item = {k: raw[k] for k in ['title', 'publication_date']}
        item['url'] = urlunsplit(url._replace(fragment=''))
        item['listing_summary'] = raw.get('listing_summary', '')
        items.setdefault(item['url'], item)
    if not items:
        raise ValueError('empty_snapshot')
    return sorted(items.values(), key=lambda i: i['publication_date'], reverse=True)[:3]


class ArticleText(HTMLParser):
    """Allow only BBVA's observed intro/key-points structure, not the whole article tag."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.key_parts = []
        self.article_count = 0
        self.closed = False
        self.malformed = False
        self.challenge = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get('class', '').split())
        ident = attrs.get('id', '')
        if ident in {'challenge-form', 'cf-challenge-running', 'captcha'}:
            self.challenge = True
        article = tag == 'article' and ident == 'article_publicacionDetalle'
        if article:
            self.article_count += 1
        hidden = (tag in {'nav', 'footer', 'header', 'aside', 'script', 'style', 'form', 'button', 'noscript'}
                  or 'hidden' in attrs or attrs.get('aria-hidden') == 'true'
                  or attrs.get('role') in {'navigation', 'dialog'}
                  or bool(classes & {'rs_skip', 'lista_itemTitulo', 'cookie', 'cookie-banner', 'menu'})
                  or bool(re.search(r'display\s*:\s*none|visibility\s*:\s*hidden', attrs.get('style', ''), re.I)))
        flags = {'article': article, 'tab': ident == 'detalle_tabs_publi', 'hidden': hidden,
                 'intro': tag == 'p' and 'detalle_text_intro' in classes,
                 'keys': tag == 'ul' and 'lista_puntosClave' in classes, 'li': tag == 'li'}
        if tag not in VOID:
            self.stack.append((tag, flags))
        if tag in {'p', 'li', 'br'}:
            self.handle_data('\n')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        flags = [f for _, f in self.stack]
        if any(f['hidden'] for f in flags):
            return
        if not any(f['article'] for f in flags) or not any(f['tab'] for f in flags):
            return
        keys = any(f['keys'] for f in flags) and any(f['li'] for f in flags)
        if keys or any(f['intro'] for f in flags):
            self.parts.append(data)
            if keys:
                self.key_parts.append(data)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        match = next((i for i in range(len(self.stack)-1, -1, -1) if self.stack[i][0] == tag), None)
        if match is None:
            return
        # BBVA can leave outer layout divs unclosed. Require well-formed content
        # inside the selected tab, without rejecting unrelated layout defects.
        if any(f['tab'] for _, f in self.stack) and match != len(self.stack)-1:
            self.malformed = True
        if tag in {'p', 'li'}:
            self.handle_data('\n')
        if self.stack[match][1]['article']:
            self.closed = True
        del self.stack[match:]


def extract_article(html):
    parser = ArticleText()
    parser.feed(html)
    parser.close()
    if parser.challenge:
        raise ValueError('challenge_page')
    if parser.article_count != 1 or not parser.closed or parser.malformed:
        raise ValueError('unrecognised_or_malformed_article')
    text = '\n'.join(' '.join(line.split()) for line in ''.join(parser.parts).splitlines() if line.strip())
    # Require content beyond the introductory standfirst. Thresholds are spike
    # sanity checks, not a claim that the complete linked report is present.
    if len(text) < 200 or len(text.split()) < 35 or len(''.join(parser.key_parts).split()) < 20:
        raise ValueError('insufficient_article_content')
    return text


def fallback(item, reason, fetched=False):
    excerpt = item['listing_summary'].strip()
    return {'title': item['title'], 'url': item['url'], 'fetch_success': fetched,
            'extraction_success': False, 'listing_fallback_used': bool(excerpt),
            'text_source': 'listing_excerpt' if excerpt else 'none',
            'character_count': len(excerpt), 'approximate_word_count': len(excerpt.split()),
            'reason': reason}


def diagnose(item, html):
    try:
        article_text = extract_article(html)
    except ValueError as error:
        return fallback(item, str(error), fetched=True)
    # Only counts leave this function; no raw string is returned or logged.
    return {'title': item['title'], 'url': item['url'], 'fetch_success': True,
            'extraction_success': True, 'listing_fallback_used': False,
            'text_source': 'article_page_intro_and_key_points',
            'character_count': len(article_text), 'approximate_word_count': len(article_text.split()),
            'complete_linked_report_extracted': False}


def run(path=SNAPSHOT):
    items = select_items(path)
    try:
        robots, mime = fetch(ROBOTS)
        if mime != 'text/plain' or '<html' in robots.lower() or not re.search(r'^\s*user-agent\s*:', robots, re.I | re.M):
            raise ValueError('unrecognised_robots')
        rules = RobotFileParser(ROBOTS)
        rules.parse(robots.splitlines())
    except (ValueError, OSError, subprocess.SubprocessError):
        return [fallback(item, 'robots_unavailable_or_invalid') for item in items]
    delay = max(3, rules.crawl_delay(USER_AGENT) or 0)
    rate = rules.request_rate(USER_AGENT)
    if rate:
        delay = max(delay, rate.seconds / rate.requests)
    results = []
    stopped = False
    for item in items:
        if stopped:
            results.append(fallback(item, 'remaining_requests_stopped_after_access_failure'))
            continue
        if not rules.can_fetch(USER_AGENT, item['url']):
            results.append(fallback(item, 'robots_denied'))
            continue
        time.sleep(delay)
        try:
            html, mime = fetch(item['url'])
            if mime != 'text/html':
                raise ValueError('unexpected_content_type')
        except (ValueError, OSError, subprocess.SubprocessError):
            results.append(fallback(item, 'fetch_failed_or_non_html'))
            stopped = True
            continue
        diagnostic = diagnose(item, html)
        del html
        results.append(diagnostic)
        if diagnostic.get('reason') == 'challenge_page':
            stopped = True
    return results


def main():
    try:
        diagnostics = run()
    except (ValueError, OSError):
        print('BBVA READ failed: invalid or unavailable snapshot. No articles requested.', file=sys.stderr)
        return 1
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))
    return 0 if all(i['extraction_success'] for i in diagnostics) else 1


if __name__ == '__main__':
    sys.exit(main())
