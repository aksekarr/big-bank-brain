"""ABN public listing discovery; never calls the site's restricted APIs."""
import re
import sys
from datetime import date
from html.parser import HTMLParser
import discovery_common as common

ENDPOINT = 'https://www.abnamro.com/research/en/overview/our-research'
OUTPUT = common.ROOT / 'abn-amro-discovery.json'
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
MONTHS = 'January February March April May June July August September October November December'.split()


class Cards(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.items = [], []
        self.card = None
        self.depth = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('data-element-type') == 'list item':
            if self.card is not None:
                raise ValueError('Nested listing cards')
            self.card = {'url': attrs.get('href'), 'title': [], 'date': [], 'excerpt': []}
            self.depth = len(self.stack)
        field = None
        if tag == 'h3':
            field = 'title'
        elif tag == 'time':
            field = 'date'
        elif tag == 'p' and 'sm:emc-line-clamp-4' in attrs.get('class', '').split():
            field = 'excerpt'
        if tag not in VOID:
            self.stack.append((tag, field))
        elif tag == 'br':
            self.handle_data(' ')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.card is None or any(t in {'script', 'style'} for t, _ in self.stack):
            return
        for field in {f for _, f in self.stack[self.depth:] if f}:
            self.card[field].append(data)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        match = next((i for i in range(len(self.stack)-1, -1, -1) if self.stack[i][0] == tag), None)
        if match is None:
            return
        del self.stack[match:]
        if self.card is not None and len(self.stack) <= self.depth:
            self.items.append(self.card)
            self.card = None


def parse_listing(html):
    parser = Cards()
    parser.feed(html)
    parser.close()
    if parser.card is not None:
        raise ValueError('Incomplete publication card')
    items = []
    for card in parser.items:
        item = {'title': ' '.join(''.join(card['title']).split()),
                'url': common.public_url(card['url'], ENDPOINT, '/research/en/our-research/')}
        published = ' '.join(''.join(card['date']).split())
        if published:
            match = re.fullmatch(r'(\d{1,2}) ([A-Za-z]+) (\d{4})', published)
            if not match or match[2] not in MONTHS:
                raise ValueError('Unrecognised ABN date; relative dates are not guessed')
            item['publication_date'] = date(int(match[3]), MONTHS.index(match[2])+1, int(match[1])).isoformat()
        excerpt = ' '.join(''.join(card['excerpt']).split())
        if excerpt:
            item['excerpt'] = excerpt
        items.append(item)
    return common.unique_items(items)


def collect():
    return parse_listing(common.retrieve(ENDPOINT, {'text/html'}))


def main():
    return common.run(ENDPOINT, 'ABN AMRO', 'ABN AMRO Group Economics', OUTPUT, collect)


if __name__ == '__main__':
    sys.exit(main())
