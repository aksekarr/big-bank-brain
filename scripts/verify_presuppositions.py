"""Evaluation-only presupposition audit construction; no live execution path."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import verify_synthesis as base

FIXTURES = Path(__file__).resolve().parents[1] / 'tests/fixtures/presupposition_pairs'
CASES = tuple(t + '_' + s for t in ('retain', 'continue', 'remain', 'return', 'still', 'again')
              for s in ('supported', 'unsupported'))
AUDIT_INSTRUCTIONS = """
Experimental audit: before assigning each target's normal verdict, explicitly record
its material presuppositions in presupposition_audit. Inspect meaning, not keywords
alone. Do not assume every use of retain, continue, remain, return, still or again
creates an unsupported presupposition. Do not invent a prior state merely because
trigger-like wording appears. Conditional or hypothetical wording does not automatically
assert its premise as fact. Allow ordinary paraphrases of supporting evidence.
Use only source claims permitted for this target, never outside knowledge or generated
prose. Uncited global claims cannot supply support for a point's audit.
For each material presupposition record trigger_wording, implied_proposition,
supporting references (empty if no permitted claim supports it), and support_verdict
(supported or unsupported). References must support the implied proposition, not merely
the main assertion. Only material presuppositions affect the target verdict; use an
empty audit when none are present. An unsupported material presupposition requires
that target to fail with a normal unsupported_statement issue explaining the gap.
Supported presuppositions do not override other normal verification failures.
Return concise evidence assessments, not a chain-of-thought narrative. Do not repair.
"""
SCHEMA = copy.deepcopy(base.SCHEMA)
TARGET = SCHEMA['properties']['targets']['items']
TARGET['properties']['presupposition_audit'] = {
    'type': 'array', 'maxItems': 8, 'items': base.synthesis.obj({
        'trigger_wording': base.synthesis.string(200),
        'implied_proposition': base.synthesis.string(500),
        'references': {'type': 'array', 'maxItems': 32, 'items': base.synthesis.string(40)},
        'support_verdict': dict(base.synthesis.string(11), enum=['supported', 'unsupported'])
    })}
TARGET['required'].append('presupposition_audit')


class ValidationStageError(ValueError):
    """Diagnostic stage only; acceptance rules and legacy messages are unchanged."""
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


def checked(stage, function, *args):
    try:
        return function(*args)
    except ValueError as error:
        raise ValidationStageError(stage, str(error)) from error


def load_fixture(case):
    if case not in CASES:
        raise ValueError('Unknown synthetic case')
    manifest = base.dedupe.read_json(FIXTURES / 'manifest.json')
    entries = [c for c in manifest['cases'] if c['case_id'] == case]
    if len(entries) != 1 or entries[0]['fixture'] != case + '.json':
        raise ValueError('Invalid fixture manifest')
    raw = (FIXTURES / (case + '.json')).read_bytes()
    if hashlib.sha256(raw).hexdigest() != entries[0]['fixture_sha256']:
        raise ValueError('Fixture hash mismatch')
    return json.loads(raw, object_pairs_hook=base.dedupe.object_pairs)


def request_payload(record):
    payload = base.request_payload(record)
    payload['instructions'] += '\n' + AUDIT_INSTRUCTIONS
    payload['text']['format'] = {**payload['text']['format'],
                               'name': 'experimental_presupposition_verification', 'schema': SCHEMA}
    return payload


def validate(output, record):
    checked('shape_text_validation', base.synthesis.article.validate, output, SCHEMA)
    normal = copy.deepcopy(output)
    for target in normal['targets']:
        del target['presupposition_audit']
    checked('normal_validation', base.validate, normal, record)
    scopes = {t['target_ref']: set(t['references']) for t in checked('revalidated_input', base.build_input, record)['synthesis']['targets']}
    for target in output['targets']:
        for audit in target['presupposition_audit']:
            refs = audit['references']
            if len(refs) != len(set(refs)) or not set(refs) <= scopes[target['target_ref']]:
                raise ValidationStageError('audit_reference_scope', 'Invalid audit references')
            if audit['support_verdict'] == 'supported' and not refs:
                raise ValidationStageError('audit_support_consistency', 'Supported audit requires evidence references')
            if audit['support_verdict'] == 'unsupported':
                if target['verdict'] != 'fail' or not any(i['type'] == 'unsupported_statement' for i in target['issues']):
                    raise ValidationStageError('audit_support_consistency', 'Unsupported audit requires normal failure issue')
    return output


def parse_response(response, record):
    # Follow the small production envelope pattern; validation below delegates
    # normal target/issue checks to production without patching its globals.
    if getattr(response, 'status', None) != 'completed' or getattr(response, 'error', None):
        raise ValidationStageError('response_envelope', 'Incomplete response')
    texts = []
    for part in response.output:
        if part.type == 'reasoning':
            continue
        if part.type != 'message' or getattr(part, 'status', None) != 'completed':
            raise ValidationStageError('response_envelope', 'Unexpected response')
        for content in part.content:
            if content.type != 'output_text':
                raise ValidationStageError('response_envelope', 'Refusal or unexpected content')
            texts.append(content.text)
    if len(texts) != 1:
        raise ValidationStageError('response_envelope', 'Expected one response')
    def invalid(_):
        raise ValidationStageError('json_value_validation', 'Invalid JSON constant')
    def pairs(values):
        return checked('json_value_validation', base.dedupe.object_pairs, values)
    return validate(json.loads(texts[0], object_pairs_hook=pairs,
                               parse_constant=invalid), record)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=CASES)
    args = parser.parse_args(argv)
    reports = []
    for case in ([args.case] if args.case else CASES):
        payload = request_payload(load_fixture(case))
        size = len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        reports.append({'case': case, 'request_bytes': size,
                        'within_guard': size <= base.MAX_REQUEST_BYTES})
    print(json.dumps(reports, indent=2))
    return 0 if all(r['within_guard'] for r in reports) else 1


if __name__ == '__main__':
    raise SystemExit(main())
