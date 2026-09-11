"""BIS public HTML READ spike; diagnostics only, no model calls or raw files."""
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

import dedupe
import processing

ROOT = processing.ROOT
ROBOTS = 'https://www.bis.org/robots.txt'
USER_AGENT = 'BigBankBrain/0.1 (BIS public research READ feasibility)'
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


def select(root=ROOT, now=None, limit=3, *, url=None):
    if limit not in (1, 2, 3):
        raise ValueError('Limit must be one, two or three')
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo('Europe/London')).date()
    processed = processing.load_processed(root)
    items = dedupe.load_discoveries(root, allow_missing_dates=True)
    eligible = []
    for item in items.values():
        if item['source_name'] != 'BIS and FSI publications':
            continue
        published = processing.publication_date(item)
        if published and today-timedelta(days=6) <= published <= today and item['url'] not in processed:
            eligible.append(item)
    if url is not None:
        eligible = [item for item in eligible if item['url'] == url]
        if not eligible:
            raise ValueError('Requested BIS URL is not eligible and unprocessed')
    return sorted(eligible, key=lambda i: i['publication_date'], reverse=True)[:limit]


def fetch(url):
    # Explicit user agent; no proxy, redirect, retry, cookies or output file.
    result = subprocess.run(
        ['curl', '--silent', '--show-error', '--noproxy', '*', '--max-time', '20',
         '--max-filesize', '2000000', '--user-agent', USER_AGENT, url,
         '--write-out', '\n%{http_code}\n%{content_type}'],
        capture_output=True, timeout=25)
    if result.returncode:
        raise ValueError('request_failed')
    body, status, mime = result.stdout.rsplit(b'\n', 2)
    if status != b'200' or len(body) > 2_000_000:
        raise ValueError('http_failure_or_size_limit')
    return body.decode('utf-8-sig'), mime.decode().split(';')[0].strip().lower()


class ArticleText(HTMLParser):
    """Observed BIS text components within the publication article only."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.body_parts = []
        self.panels = 0
        self.closed = 0
        self.malformed = False
        self.challenge = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get('class', '').split())
        if attrs.get('id') in {'challenge-form', 'cf-challenge-running', 'captcha'}:
            self.challenge = True
        hidden = (tag in {'nav', 'footer', 'header', 'aside', 'script', 'style', 'form',
                          'button', 'noscript', 'figure', 'table'}
                  or 'hidden' in attrs or attrs.get('aria-hidden') == 'true'
                  or attrs.get('role') in {'navigation', 'dialog'}
                  or bool(classes & {'cookie', 'cookie-banner', 'menu'})
                  or bool(re.search(r'display\s*:\s*none|visibility\s*:\s*hidden', attrs.get('style', ''), re.I)))
        panel = tag == 'div' and 'text__component' in classes and any(t == 'article' for t, _ in self.stack)
        if panel:
            self.panels += 1
        flags = {'panel': panel, 'hidden': hidden,
                 'text': tag in {'p', 'li', 'h2', 'h3'},
                 'body': tag == 'p'}
        if tag not in VOID:
            self.stack.append((tag, flags))
        if tag in {'p', 'li', 'h2', 'h3', 'br'}:
            self.handle_data('\n')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        flags = [f for _, f in self.stack]
        if (any(f['hidden'] for f in flags) or not any(f['panel'] for f in flags)
                or not any(f['text'] for f in flags)):
            return
        self.parts.append(data)
        if any(f['body'] for f in flags):
            self.body_parts.append(data)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        match = next((i for i in range(len(self.stack)-1, -1, -1) if self.stack[i][0] == tag), None)
        if match is None:
            return
        if any(f['panel'] for _, f in self.stack) and match != len(self.stack)-1:
            self.malformed = True
        if tag in {'p', 'li', 'h2', 'h3'}:
            self.handle_data('\n')
        if self.stack[match][1]['panel']:
            self.closed += 1
        del self.stack[match:]


def extract_article(html):
    parser = ArticleText()
    parser.feed(html)
    parser.close()
    if parser.challenge:
        raise ValueError('challenge_page')
    if parser.panels < 1 or parser.closed != parser.panels or parser.malformed:
        raise ValueError('unrecognised_or_malformed_article')
    text = '\n'.join(' '.join(line.split()) for line in ''.join(parser.parts).splitlines() if line.strip())
    if len(text) < 200 or len(''.join(parser.body_parts).split()) < 35:
        raise ValueError('insufficient_article_content')
    return text


def diagnostic(item, text, source, reason=None):
    return {'title': item['title'], 'url': item['url'],
            'publication_date': item['publication_date'],
            'institution': item['institution'], 'source_name': item['source_name'],
            'complete_paper_extracted': False,
            'text_available': bool(text), 'text_source': source,
            'rss_fallback_used': source == 'rss_excerpt',
            'character_count': len(text), 'approximate_word_count': len(text.split()),
            'reason': reason}


def run(items, on_text=None):
    if len(items) > 3:
        raise ValueError('At most three articles')
    for item in items:
        url = urlsplit(item['url'])
        if (url.scheme != 'https' or url.netloc != 'www.bis.org'
                or not url.path.startswith('/publications/')
                or url.path.lower().endswith('.pdf') or url.query or url.fragment or re.search(r'[\s\\]', item['url'])):
            raise ValueError('Unexpected BIS article URL')
        if not isinstance(item.get('excerpt', ''), str):
            raise ValueError('Invalid excerpt')
    if not items:
        return []
    rules = None
    reason = None
    try:
        robots, mime = fetch(ROBOTS)
        if mime != 'text/plain' or '<html' in robots.lower() or not re.search(r'^\s*user-agent\s*:', robots, re.I | re.M):
            raise ValueError('invalid_robots')
        rules = RobotFileParser(ROBOTS)
        rules.parse(robots.splitlines())
    except (ValueError, OSError, subprocess.SubprocessError):
        reason = 'robots_unavailable_or_invalid'
    delay = max(3, rules.crawl_delay(USER_AGENT) or 0) if rules else 3
    rate = rules.request_rate(USER_AGENT) if rules else None
    if rate:
        delay = max(delay, rate.seconds / rate.requests)
    results = []
    for item in items:
        text = ''
        failure = reason
        if rules and not reason:
            if not rules.can_fetch(USER_AGENT, item['url']):
                failure = 'robots_denied'
            else:
                time.sleep(delay)
                try:
                    html, mime = fetch(item['url'])
                    if mime != 'text/html':
                        raise ValueError('non_html')
                except (ValueError, OSError, subprocess.SubprocessError):
                    failure = reason = 'access_failed_remaining_requests_stopped'
                else:
                    try:
                        text = extract_article(html)
                    except ValueError as error:
                        failure = str(error)
                        if failure == 'challenge_page':
                            reason = failure
                    finally:
                        del html
        source = 'publication_page_html'
        if not text:
            text = item.get('excerpt', '').strip()
            source = 'rss_excerpt' if text else 'none'
        # Caller can consume transient text; diagnostics never contain it.
        if text and on_text is not None:
            on_text(item, text, source)
        results.append(diagnostic(item, text, source, failure))
        del text
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, choices=[1, 2, 3], default=3)
    args = parser.parse_args(argv)
    try:
        results = run(select(limit=args.limit))
    except Exception:
        print('BIS READ stopped; no raw content logged.', file=sys.stderr)
        return 1
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(i['text_available'] for i in results) else 1


if __name__ == '__main__':
    sys.exit(main())
