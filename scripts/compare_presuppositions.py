"""Isolated one-shot presupposition comparison. Offline unless --live is explicit."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
import verify_synthesis as base
import verify_presuppositions as experimental

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/presupposition_pairs'
VARIANTS = {'frozen': base, 'experimental': experimental}
CASES = tuple(t+'_'+s for t in ('retain','continue','remain','return','still','again')
              for s in ('supported','unsupported'))
LEDGER = 'presupposition-comparison-ledger.json'
MAX_CALLS = 24
RESERVATION = 124560
BUDGET = 2989440
MAX_REQUEST_BYTES = 20000
CODE_HASHES = {'verify_synthesis.py': 'cf69583b59add41f23acd1c27e1365da1a57d5255f4efea2db5ae384fd41d209', 'verify_presuppositions.py': '419d801c6e1ae5754858ca6a5971c294cea255ab1171fb4c4142789ae33e334a', 'synthesise.py': 'd30e94338cef8da8d56f55e392aecb3573e25868204451221bd6d6af557e8655', 'extract_bbva.py': '5ff311493b9fea07593b6c701b4e34edf3cd24c47c73832b6c8b1fbd8b8d5200'}
MANIFEST_HASH = 'e8d0f0049381f754531c98f381bf27d5aef76a9215da1f519f126cafff58095a'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    def invalid(_):
        raise ValueError('Non-finite JSON')
    return json.loads(raw, object_pairs_hook=base.dedupe.object_pairs, parse_constant=invalid)


def prepare(variant, case):
    if variant not in VARIANTS or case not in CASES:
        raise ValueError('Unknown comparison combination')
    for name, expected in CODE_HASHES.items():
        if digest((ROOT/'scripts'/name).read_bytes()) != expected:
            raise ValueError('Verifier implementation identity mismatch')
    raw_manifest = (FIXTURES/'manifest.json').read_bytes()
    if digest(raw_manifest) != MANIFEST_HASH:
        raise ValueError('Manifest identity mismatch')
    manifest = decode(raw_manifest)
    entry = next(c for c in manifest['cases'] if c['case_id'] == case)
    if entry['fixture'] != case+'.json':
        raise ValueError('Invalid fixture filename')
    raw = (FIXTURES/entry['fixture']).read_bytes()
    if digest(raw) != entry['fixture_sha256']:
        raise ValueError('Fixture identity mismatch')
    record = decode(raw)
    payload = VARIANTS[variant].request_payload(record)
    size = len(json.dumps(payload, ensure_ascii=False).encode())
    if size > MAX_REQUEST_BYTES:
        raise ValueError('Request exceeds comparison guard')
    if (payload['model'] != 'gpt-5.6-terra' or payload['reasoning'] != {'effort':'medium'}
            or payload['max_output_tokens'] != 6000 or payload['store'] is not False
            or payload['tools'] != [] or payload['tool_choice'] != 'none'):
        raise ValueError('Comparison settings mismatch')
    provenance = {'variant': variant, 'case_id': case, 'repetition':'r1',
                  'fixture_sha256': digest(raw), 'manifest_sha256': MANIFEST_HASH,
                  'code_sha256': dict(CODE_HASHES),
                  'instruction_sha256': digest(payload['instructions'].encode()),
                  'schema_sha256': digest(json.dumps(payload['text']['format']['schema'], sort_keys=True).encode()),
                  'model': payload['model'], 'reasoning': payload['reasoning']['effort'],
                  'max_output_tokens': payload['max_output_tokens'], 'request_bytes':size,
                  'reservation_assumption_micro_usd': RESERVATION}
    return record, payload, provenance


def git_context():
    try:
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=ROOT, text=True,
                                           stderr=subprocess.DEVNULL, timeout=5).strip()
        return {'git_checkpoint': git('rev-parse','HEAD'),
                'git_dirty': bool(git('--no-optional-locks','status','--porcelain'))}
    except (OSError, subprocess.SubprocessError):
        return {'git_checkpoint': None, 'git_dirty': None}


def load_ledger(root):
    path = root/LEDGER
    ledger = base.dedupe.read_json(path) if path.exists() else {'version':1, 'attempts':[]}
    if not isinstance(ledger,dict) or ledger.get('version') != 1 or not isinstance(ledger.get('attempts'),list):
        raise ValueError('Invalid comparison ledger')
    attempted, held = set(), 0
    for a in ledger['attempts']:
        if not isinstance(a,dict):
            raise ValueError('Invalid comparison attempt')
        key = (a.get('variant'), a.get('case_id'))
        if (key[0] not in VARIANTS or key[1] not in CASES or key in attempted
                or a.get('repetition') != 'r1' or a.get('attempt_eligibility') != 'closed'
                or a.get('reserved_micro_usd') != RESERVATION
                or a.get('outcome') not in {'pending','result','rejected'}):
            raise ValueError('Invalid comparison attempt')
        base.dedupe.timestamp(a.get('reserved_at'))
        rejected = a['outcome'] == 'rejected'
        if a.get('reservation_held') is not (not rejected):
            raise ValueError('Invalid reservation treatment')
        if rejected and (type(a.get('http_status')) is not int or a['http_status'] not in {401,429}):
            raise ValueError('Invalid rejection')
        attempted.add(key)
        held += not rejected
    if len(attempted) > MAX_CALLS or held*RESERVATION > BUDGET:
        raise ValueError('Comparison cap exceeded')
    return ledger, attempted, held


def run(variant, case, *, root=ROOT, client=None, live=False):
    descriptor = os.open(root, os.O_RDONLY)
    owned = None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record, payload, provenance = prepare(variant, case)
        ledger, attempted, held = load_ledger(root)
        if (variant, case) in attempted or len(attempted) >= MAX_CALLS or (held+1)*RESERVATION > BUDGET:
            raise ValueError('Comparison combination or allowance exhausted')
        destination = root / f'presupposition-comparison-{variant}-{case}-r1.json'
        if os.path.lexists(destination):
            raise ValueError('Comparison result already exists')
        provenance.update(git_context())
        if not live:
            return {'mode': 'offline', 'variant': variant, 'case_id': case,
                    'request_bytes': provenance['request_bytes'], 'eligible': True,
                    'attempts_remaining': MAX_CALLS-len(attempted)}
        if client is None:
            owned = base.synthesis.article.make_client()
            client = owned
        attempt = {**provenance, 'reserved_micro_usd': RESERVATION,
                   'reserved_at': datetime.now(timezone.utc).isoformat(), 'outcome': 'pending',
                   'stage': 'send_pending', 'attempt_eligibility': 'closed', 'reservation_held': True}
        ledger['attempts'].append(attempt)
        base.synthesis.assembler.processing.write_json(ledger, root / LEDGER)
        try:
            response = client.responses.create(**payload)
        except Exception as error:
            status = getattr(error, 'status_code', None)
            if type(status) is int and status in {401, 429}:
                attempt.update(outcome='rejected', http_status=status, stage='request_rejected', reservation_held=False)
            else:
                attempt['stage'] = 'send_uncertain'
            base.synthesis.assembler.processing.write_json(ledger, root / LEDGER)
            raise ValueError('Evaluation request failed; no automatic retry') from None
        attempt.update(outcome='result', stage='response_returned')
        base.synthesis.assembler.processing.write_json(ledger, root / LEDGER)
        try:
            findings = VARIANTS[variant].parse_response(response, record)
        except Exception:
            # Fixed stage names only: never persist response text or exception messages.
            attempt['stage'] = 'validation_failed'
            base.synthesis.assembler.processing.write_json(ledger, root / LEDGER)
            raise
        result = {'version': 1, **provenance,
                  'verified_at': datetime.now(timezone.utc).isoformat(), 'stage': 'stored', 'findings': findings}
        attempt['stage'] = 'storage_pending'
        base.synthesis.assembler.processing.write_json(ledger, root / LEDGER)
        try:
            base.save_new_result(result, destination)
        except Exception:
            attempt['stage'] = 'storage_failed'
            base.synthesis.assembler.processing.write_json(ledger, root / LEDGER)
            raise
        attempt['stage'] = 'stored'
        base.synthesis.assembler.processing.write_json(ledger, root / LEDGER)
        return {'status': 'stored', 'variant': variant, 'case_id': case, 'verdict': findings['verdict'], 'result_file': destination.name}
    finally:
        try:
            if owned is not None:
                owned.close()
        finally:
            os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant', choices=tuple(VARIANTS), required=True)
    parser.add_argument('--case', choices=CASES, required=True)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--limit', type=int, choices=[1], default=1)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run(args.variant, args.case, live=args.live), indent=2))
        return 0
    except Exception:
        print('Comparison stopped; inspect comparison ledger. Do not retry an attempted combination.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
