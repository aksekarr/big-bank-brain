"""One-page BBVA discovery spike: Python standard library plus installed curl."""

import json
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

LISTING_URL = "https://www.bbvaresearch.com/en/publications/"
ROBOTS_URL = "https://www.bbvaresearch.com/robots.txt"
USER_AGENT = "BigBankBrain/0.1 (public metadata discovery)"
OUTPUT = Path(__file__).resolve().parents[1] / "bbva-discovery.json"
# Conservative limits for this spike, not a decision about the future pipeline.
TIMEOUT_SECONDS = 20
MIN_DELAY_SECONDS = 3
MAX_RESPONSE_BYTES = 2_000_000
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
             "link", "meta", "param", "source", "track", "wbr"}


def fetch(url):
    # No redirects, retries, browser impersonation or raw response files.
    result = subprocess.run(
        ["curl", "--silent", "--show-error", "--noproxy", "*",
         "--max-time", str(TIMEOUT_SECONDS),
         "--max-filesize", str(MAX_RESPONSE_BYTES),
         "--user-agent", USER_AGENT, url,
         "--write-out", "\n%{http_code}\n%{content_type}"],
        capture_output=True, timeout=TIMEOUT_SECONDS + 5,
    )
    if result.returncode:
        raise ValueError(f"Request failed for {url} (curl exit {result.returncode})")
    body, status, content_type = result.stdout.rsplit(b"\n", 2)
    if status != b"200":
        raise ValueError(f"HTTP {status.decode()} for {url}; stopping without retry")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("Response exceeded this spike's size limit")
    return body.decode("utf-8-sig"), content_type.decode()


class Cards(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.card = None
        self.card_depth = None
        self.items = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        if tag == "div" and attrs.get("id", "").startswith("card-"):
            if self.card is not None:
                raise ValueError("Unexpected nested publication cards")
            self.card = {"title": [], "summary": [],
                         "publication_date": attrs.get("data-fecha"),
                         "url": attrs.get("data-permalink")}
            self.card_depth = len(self.stack)
        if tag not in VOID_TAGS:
            self.stack.append((tag, classes))
        elif tag == "br":
            self.handle_data(" ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, text):
        if self.card is None or any(t in {"script", "style"} for t, _ in self.stack):
            return
        classes = set().union(*(c for _, c in self.stack[self.card_depth:]))
        if "cardTitulo" in classes:
            self.card["title"].append(text)
        if "cardTexto" in classes:
            self.card["summary"].append(text)

    def handle_endtag(self, tag):
        match = next((i for i in range(len(self.stack) - 1, -1, -1)
                      if self.stack[i][0] == tag), None)
        if match is None:
            return
        del self.stack[match:]
        if self.card is not None and len(self.stack) <= self.card_depth:
            self.items.append(self.card)
            self.card = None
            self.card_depth = None


def parse_listing(html):
    parser = Cards()
    parser.feed(html)
    parser.close()
    if parser.card is not None or not parser.items:
        raise ValueError("No complete publication cards; listing may have changed or be blocked")
    unique = {}
    for card in parser.items:
        title = " ".join("".join(card["title"]).split())
        published = card["publication_date"]
        if not title or not published or not card["url"]:
            raise ValueError("Publication card is missing title, date or URL")
        if date.fromisoformat(published).isoformat() != published:
            raise ValueError("Publication date is not YYYY-MM-DD")
        parsed = urlsplit(urljoin(LISTING_URL, card["url"]))
        if (parsed.scheme != "https" or parsed.netloc != "www.bbvaresearch.com"
                or not parsed.path.startswith("/en/publicaciones/")):
            raise ValueError("Unexpected publication URL")
        # Use the listing's public permalink; no article request to inspect canonical tags.
        url = urlunsplit(parsed._replace(fragment=""))
        item = {"title": title, "publication_date": published, "url": url}
        summary = " ".join("".join(card["summary"]).split())
        if summary:
            item["listing_summary"] = summary
        if url in unique and unique[url] != item:
            raise ValueError("Conflicting metadata for a duplicate URL")
        unique[url] = item
    return sorted(unique.values(), key=lambda item: item["publication_date"], reverse=True)


def collect():
    robots, content_type = fetch(ROBOTS_URL)
    if "text/plain" not in content_type.lower() or "<html" in robots.lower():
        raise ValueError("Unexpected robots.txt response; not assuming access is allowed")
    rules = RobotFileParser(ROBOTS_URL)
    rules.parse(robots.splitlines())
    if not rules.can_fetch(USER_AGENT, LISTING_URL):
        raise ValueError("robots.txt disallows the BBVA listing")
    delay = max(MIN_DELAY_SECONDS, rules.crawl_delay(USER_AGENT) or 0)
    rate = rules.request_rate(USER_AGENT)
    if rate:
        delay = max(delay, rate.seconds / rate.requests)
    time.sleep(delay)
    html, content_type = fetch(LISTING_URL)
    if "text/html" not in content_type.lower():
        raise ValueError("Listing response is not HTML")
    items = parse_listing(html)
    return {
        "institution": "BBVA Research", "listing_url": LISTING_URL,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Initial recent publication cards only; no pagination, date-window filter or cross-run deduplication.",
        "url_provenance": "Public permalinks from listing cards; article canonical tags not fetched.",
        "listing_summary_provenance": "Publisher text from cardTexto when present; not an AI-generated summary.",
        "robots_check": {"url": ROBOTS_URL, "http_status": 200, "listing_allowed": True},
        "article_bodies_fetched": False, "summaries_generated": False,
        "item_count": len(items), "items": items,
    }


def write_output(data, output=OUTPUT):
    # Only JSON reaches disk. Failed fetches/parses leave the previous snapshot intact.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".bbva-discovery-", suffix=".json",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary.replace(output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main():
    try:
        data = collect()
        write_output(data)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"BBVA discovery failed: {error}. Previous output unchanged.", file=sys.stderr)
        return 1
    summaries = sum("listing_summary" in item for item in data["items"])
    print(f"Wrote {data['item_count']} unique publications ({summaries} listing summaries) to {OUTPUT}")
    print(f"Fetched at {data['discovered_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
