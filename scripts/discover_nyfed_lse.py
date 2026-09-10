"""Read only RSS item metadata; ignore content:encoded, including its text."""
import io
import re
import sys
from email.utils import parsedate_to_datetime
from xml.sax import make_parser, SAXException
from xml.sax.handler import ContentHandler, feature_namespaces, feature_external_ges
import discovery_common as common

ENDPOINT = 'https://libertystreeteconomics.newyorkfed.org/feed/'
OUTPUT = common.ROOT / 'nyfed-lse-discovery.json'


class Metadata(ContentHandler):
    def __init__(self):
        super().__init__()
        self.path, self.items = [], []
        self.item = None
        self.field = None

    def startElementNS(self, name, qname, attrs):
        self.path.append(name)
        if self.path == [(None, 'rss'), (None, 'channel'), (None, 'item')]:
            self.item = {}
        elif self.item is not None and len(self.path) == 4 and name in {
                (None, 'title'), (None, 'link'), (None, 'pubDate'), (None, 'description')}:
            self.field = name[1]
            if self.field in self.item:
                raise ValueError('Repeated RSS metadata field')
            self.item[self.field] = []

    def characters(self, content):
        if self.field:
            self.item[self.field].append(content)

    def endElementNS(self, name, qname):
        if len(self.path) == 4:
            self.field = None
        if self.item is not None and len(self.path) == 3:
            self.items.append({k: ''.join(v).strip() for k, v in self.item.items()})
            self.item = None
        self.path.pop()


def parse_feed(xml):
    if '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
        raise ValueError('DTD/entities are not accepted in discovery feeds')
    parser = make_parser()
    parser.setFeature(feature_namespaces, True)
    parser.setFeature(feature_external_ges, False)
    handler = Metadata()
    parser.setContentHandler(handler)
    try:
        parser.parse(io.StringIO(xml))
    except SAXException as error:
        raise ValueError('Malformed LSE RSS') from error
    items = []
    for raw in handler.items:
        item = {'title': ' '.join(raw.get('title', '').split()),
                'url': common.public_url(raw.get('link'), ENDPOINT, '/')}
        if not re.fullmatch(r'/\d{4}/\d{2}/[^/]+/', common.urlsplit(item['url']).path):
            raise ValueError('Expected a dated LSE article URL')
        if raw.get('pubDate'):
            published = parsedate_to_datetime(raw['pubDate'])
            if published.tzinfo is None:
                raise ValueError('RSS publication date lacks timezone')
            item['publication_date'] = published.date().isoformat()
        excerpt = common.excerpt_text(raw.get('description', ''))
        if excerpt:
            item['excerpt'] = excerpt
        items.append(item)
    return common.unique_items(items)


def collect():
    return parse_feed(common.retrieve(ENDPOINT, {'application/rss+xml', 'application/xml', 'text/xml'}))


def main():
    return common.run(ENDPOINT, 'Federal Reserve Bank of New York', 'Liberty Street Economics', OUTPUT, collect)


if __name__ == '__main__':
    sys.exit(main())
