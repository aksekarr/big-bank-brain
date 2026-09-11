"""Optional AI interpretation of the stored briefing; no collection, synthesis or verification changes."""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import dedupe
import synthesise as synthesis

ROOT = synthesis.assembler.processing.ROOT
RESULT = 'interpretation.json'
LEDGER = 'interpretation-ledger.json'
MAX_REQUEST_BYTES = synthesis.MAX_REQUEST_BYTES
RESERVE_MICRO_USD = synthesis.RESERVE_MICRO_USD
MAX_ATTEMPTS = 2


def string(limit):
    return {'type': 'string', 'minLength': 1, 'maxLength': limit}


def obj(properties):
    return {'type': 'object', 'additionalProperties': False,
            'properties': properties, 'required': list(properties)}


CLAIM_ID = dict(string(40), pattern=r'^a[1-9][0-9]*:c[1-9][0-9]*$')
THEME_ID = dict(string(16), pattern=r'^t[1-9][0-9]*$')
SCHEMA = obj({
    'bottom_line': string(300),
    'themes': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': obj({
        'theme_id': THEME_ID,
        'takeaway': string(100),
        'read': string(250),
        'claim_ids': {'type': 'array', 'minItems': 1, 'maxItems': 32, 'items': CLAIM_ID},
    })},
})
INSTRUCTIONS = """Write 'BBB's read': a short, plain-English morning interpretation of the
supplied research evidence, for a busy professional skimming in 30 seconds.
This is BBB's analysis, not any institution's view.
Lead with implications, not data. Explain what the evidence means and why
it matters. Connect institutions only where the claims support it. Keep
every condition explicit and short (e.g. 'only if oil < $90').
For bottom_line, state the single most important implication across the
research in plain English a non-specialist understands. Do not try to cover
every theme. No specialist jargon: if a technical term is essential, say
what it means in plain words.
Rules: Use only the supplied claims, no outside knowledge. Every statement
must be supported by the claim_ids you return. Never present your
interpretation as an institution's view; name an institution only when
stating what its claims say. Make no forecasts of your own; you may refer
to institutions' forecasts in the claims. No advice or recommendations.
Never turn 'could', 'if' or 'only if' into a certainty. No filler.
Length (hard limits): bottom_line max 35 words. Per theme: takeaway max 8
words, read max 25 words, max 2 numbers. Return only JSON."""


class SafetyError(ValueError):
    pass


def load_ledger(root):
    path = root / LEDGER
    ledger = dedupe.read_json(path) if path.exists() else {'version': 1, 'attempts': []}
    if not isinstance(ledger, dict) or ledger.get('version') != 1 or not isinstance(ledger.get('attempts'), list):
        raise SafetyError('Interpretation ledger invalid; preserved without reset')
    for attempt in ledger['attempts']:
        if (not isinstance(attempt, dict) or not re.fullmatch(r'[0-9a-f]{64}', attempt.get('source_synthesis_sha256', ''))
                or attempt.get('reserved_micro_usd') != RESERVE_MICRO_USD
                or attempt.get('outcome') not in {'pending', 'result'}):
            raise SafetyError('Interpretation ledger invalid; preserved without reset')
    return ledger


def load_snapshot(root):
    raw = (root / synthesis.RESULT).read_bytes()
    snapshot = json.loads(raw.decode('utf-8'), object_pairs_hook=dedupe.object_pairs)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('articles'), list):
        raise ValueError('Invalid stored synthesis')
    briefing = snapshot.get('synthesis')
    if not isinstance(briefing, dict) or not isinstance(briefing.get('themes'), list):
        raise ValueError('Invalid stored briefing')
    claims, articles = {}, []
    for article in snapshot['articles']:
        if not isinstance(article, dict) or not isinstance(article.get('claims'), list):
            raise ValueError('Invalid stored article')
        article_ref = article.get('article_ref')
        if not isinstance(article_ref, str) or not re.fullmatch(r'a[1-9][0-9]*', article_ref):
            raise ValueError('Invalid article reference')
        projected_claims = []
        for number, claim in enumerate(article['claims'], 1):
            if not isinstance(claim, dict) or claim.get('ref') != f'{article_ref}:c{number}':
                raise ValueError('Invalid stored claim reference')
            text = dedupe.text(claim.get('text'))
            if claim['ref'] in claims:
                raise ValueError('Duplicate stored claim reference')
            institution = dedupe.text(article.get('institution'))
            claims[claim['ref']] = {'claim_id': claim['ref'], 'institution': institution, 'text': text}
            projected_claims.append(claim['ref'])
        articles.append({'article_ref': article_ref, 'claim_ids': projected_claims})
    themes = []
    for index, theme in enumerate(briefing['themes'], 1):
        if not isinstance(theme, dict) or not isinstance(theme.get('points'), list):
            raise ValueError('Invalid stored theme')
        point_refs = set()
        points = []
        for point in theme['points']:
            if not isinstance(point, dict) or not isinstance(point.get('references'), list):
                raise ValueError('Invalid stored point')
            refs = point['references']
            if not refs or len(set(refs)) != len(refs) or any(ref not in claims for ref in refs):
                raise ValueError('Invalid stored point references')
            point_refs.update(refs)
            points.append({'text': dedupe.text(point.get('text')), 'claim_ids': refs})
        themes.append({'theme_id': f't{index}', 'title': dedupe.text(theme.get('title')), 'points': points,
                       'allowed_claim_ids': sorted(point_refs)})
    if not themes:
        raise ValueError('Stored briefing has no themes')
    return raw, snapshot, {'headline': dedupe.text(briefing.get('headline')),
                            'overview': dedupe.text(briefing.get('overview')), 'themes': themes,
                            'articles': articles, 'claims': list(claims.values())}


def request_payload(data):
    safe = {'headline': data['headline'], 'overview': data['overview'],
            'themes': [{'theme_id': theme['theme_id'], 'title': theme['title'],
                        'claim_ids': theme['allowed_claim_ids']} for theme in data['themes']],
            'claims': data['claims']}
    return {'model': synthesis.article.MODEL, 'reasoning': {'effort': 'low'},
            'instructions': INSTRUCTIONS,
            'input': [{'role': 'user', 'content': json.dumps(safe, ensure_ascii=False, separators=(',', ':'))}],
            'text': {'format': {'type': 'json_schema', 'name': 'briefing_interpretation', 'strict': True, 'schema': SCHEMA}},
            'max_output_tokens': synthesis.MAX_OUTPUT_TOKENS, 'tools': [], 'tool_choice': 'none',
            'store': False, 'service_tier': 'default'}


def validate(output, data):
    synthesis.article.validate(output, SCHEMA)
    words = lambda text: re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", text)
    if len(words(output['bottom_line'])) > 35:
        raise ValueError('Bottom line exceeds word limit')
    allowed = {claim['claim_id'] for claim in data['claims']}
    expected = {theme['theme_id'] for theme in data['themes']}
    ids = [theme['theme_id'] for theme in output['themes']]
    if set(ids) != expected or len(ids) != len(set(ids)):
        raise ValueError('Interpretation must cover each theme exactly once')
    by_id = {theme['theme_id']: theme for theme in data['themes']}
    for theme in output['themes']:
        refs = theme['claim_ids']
        if len(words(theme['takeaway'])) > 8 or len(words(theme['read'])) > 25:
            raise ValueError('Theme exceeds word limit')
        if len(re.findall(r'(?<![A-Za-z])\d+(?:[.,]\d+)?', theme['read'])) > 2:
            raise ValueError('Theme exceeds number limit')
        if len(set(refs)) != len(refs) or any(ref not in allowed or ref not in by_id[theme['theme_id']]['allowed_claim_ids'] for ref in refs):
            raise ValueError('Invalid theme claim IDs')
    return output


def parse_response(response, data):
    if getattr(response, 'status', None) != 'completed' or getattr(response, 'error', None):
        raise ValueError('Incomplete or failed response')
    texts = [content.text for part in getattr(response, 'output', []) if getattr(part, 'type', None) == 'message'
             and getattr(part, 'status', None) == 'completed' for content in getattr(part, 'content', [])
             if getattr(content, 'type', None) == 'output_text']
    if len(texts) != 1:
        raise ValueError('Expected one structured response')
    output = json.loads(texts[0], object_pairs_hook=dedupe.object_pairs)
    return validate(output, data)


def prepare(root=ROOT):
    raw, snapshot, data = load_snapshot(root)
    payload = request_payload(data)
    request_bytes = len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    return data, payload, {'source_synthesis_sha256': hashlib.sha256(raw).hexdigest(),
                           'request_bytes': request_bytes, 'request_cap': MAX_REQUEST_BYTES,
                           'reserved_micro_usd_per_call': RESERVE_MICRO_USD,
                           'max_attempts': MAX_ATTEMPTS,
                           'estimated_max_micro_usd': RESERVE_MICRO_USD * MAX_ATTEMPTS}


def run(root=ROOT, *, live=False, client=None):
    data, payload, report = prepare(root)
    ledger = load_ledger(root)
    previous = [a for a in ledger['attempts'] if a['source_synthesis_sha256'] == report['source_synthesis_sha256']]
    if not live:
        return {'mode': 'offline', 'request_size_permitted': report['request_bytes'] <= MAX_REQUEST_BYTES,
                'attempts_already_reserved': len(previous), **report, 'prompt': INSTRUCTIONS}
    if report['request_bytes'] > MAX_REQUEST_BYTES:
        raise SafetyError('Interpretation request exceeds cap; no truncation')
    if client is None:
        if not os.environ.get('OPENAI_API_KEY'):
            raise SafetyError('Missing OPENAI_API_KEY; no request sent')
        client = synthesis.article.make_client()
    last_error = None
    for retry_number in range(1, MAX_ATTEMPTS + 1):
        attempt_number = len(previous) + retry_number
        attempt = {'reserved_at': datetime.now(timezone.utc).isoformat(),
                   'source_synthesis_sha256': report['source_synthesis_sha256'],
                   'reserved_micro_usd': RESERVE_MICRO_USD, 'attempt': attempt_number,
                   'outcome': 'pending'}
        ledger['attempts'].append(attempt)
        synthesis.assembler.processing.write_json(ledger, root / LEDGER)
        try:
            response = client.responses.create(**payload)
            attempt['outcome'] = 'result'
            synthesis.assembler.processing.write_json(ledger, root / LEDGER)
            result = parse_response(response, data)
        except ValueError as error:
            last_error = error
            continue
        output = {'version': 1, **report, 'model': payload['model'], 'reasoning': payload['reasoning']['effort'],
                  'generated_at': datetime.now(timezone.utc).isoformat(), **result}
        synthesis.assembler.processing.write_json(output, root / RESULT)
        return {'status': 'stored', 'result_file': RESULT, 'attempts': attempt_number, **report}
    raise SafetyError(f'Interpretation response invalid after {MAX_ATTEMPTS} attempts; no output written') from last_error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--show-prompt', action='store_true')
    args = parser.parse_args(argv)
    try:
        report = run(live=args.live)
        if args.show_prompt:
            print(report['prompt'])
        else:
            print(json.dumps({key: value for key, value in report.items() if key != 'prompt'}, indent=2))
        return 0
    except SafetyError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
