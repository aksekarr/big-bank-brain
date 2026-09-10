"""BIS/FSI RSS 1.0 metadata discovery, with explicit RSS and Dublin Core namespaces."""
import re
import sys
from datetime import datetime
from xml.etree import ElementTree as ET
import discovery_common as common

ENDPOINT = 'https://www.bis.org/doclist/bis_fsi_publs.rss'
OUTPUT = common.ROOT / 'bis-discovery.json'
RSS = '{http://purl.org/rss/1.0/}'
DC = '{http://purl.org/dc/elements/1.1/}'
RDF = '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}'


def parse_feed(xml):
    if '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
        raise ValueError('DTD/entities are not accepted in discovery feeds')
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as error:
        raise ValueError('Malformed BIS RSS') from error
    if root.tag != RDF + 'RDF':
        raise ValueError('Expected BIS RSS 1.0/RDF')
    items = []
    for raw in root.findall(RSS + 'item'):
        for field in [RSS+'title', RSS+'link', RSS+'description', DC+'date']:
            if len(raw.findall(field)) > 1:
                raise ValueError('Repeated RSS metadata field')
        item = {'title': ' '.join((raw.findtext(RSS+'title') or '').split()),
                'url': common.public_url(raw.findtext(RSS+'link'), ENDPOINT, '/publications/')}
        published = raw.findtext(DC+'date')
        if published:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})', published):
                raise ValueError('Unexpected BIS date format')
            item['publication_date'] = datetime.fromisoformat(published.replace('Z', '+00:00')).date().isoformat()
        excerpt = common.excerpt_text(raw.findtext(RSS+'description') or '')
        if excerpt:
            item['excerpt'] = excerpt
        items.append(item)
    return common.unique_items(items)


def collect():
    return parse_feed(common.retrieve(ENDPOINT, {'application/rss+xml', 'application/xml', 'text/xml'}))


def main():
    return common.run(ENDPOINT, 'Bank for International Settlements', 'BIS and FSI publications', OUTPUT, collect)


if __name__ == '__main__':
    sys.exit(main())
