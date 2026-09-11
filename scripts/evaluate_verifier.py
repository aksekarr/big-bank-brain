"""Evaluation-only frozen verifier campaign; offline by default, one case per call."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

import verify_synthesis as verifier
import dedupe

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/verifier_eval'
CASES = ('clean', 'turkiye_conditionality', 'argentina_overview',
         'bis_reference_support', 'ecb_despite', 'abn_retain', 'harmless_paraphrase')
LEDGER = 'verifier-eval-ledger.json'
MAX_CALLS = 7
RESERVATION = 124560
BUDGET = 871920
MAX_REQUEST_BYTES = 20000


def load_case(case, fixtures=FIXTURES):
    if case not in CASES:
        raise ValueError('Unknown evaluation case')
    manifest = dedupe.read_json(fixtures / 'manifest.json')
    entries = manifest['cases']
    if len(entries) != len(CASES) or {e['case_id'] for e in entries} != set(CASES):
        raise ValueError('Invalid campaign manifest')
    entry = next(e for e in entries if e['case_id'] == case)
    if entry['fixture'] != case + '.json':
        raise ValueError('Invalid fixture path')
    raw = (fixtures / entry['fixture']).read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    if fingerprint != entry['fixture_sha256']:
        raise ValueError('Fixture hash mismatch')
    def invalid(_):
        raise ValueError('Non-finite JSON')
    record = json.loads(raw.decode('utf-8'), object_pairs_hook=dedupe.object_pairs, parse_constant=invalid)
    payload = verifier.request_payload(record)
    size = len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    if size > MAX_REQUEST_BYTES or payload['max_output_tokens'] != 6000:
        raise ValueError('Evaluation request exceeds approved bounds')
    return record, payload, fingerprint, size


def load_ledger(root):
    path = root / LEDGER
    ledger = dedupe.read_json(path) if path.exists() else {'version': 1, 'attempts': []}
    if not isinstance(ledger, dict) or ledger.get('version') != 1 or not isinstance(ledger.get('attempts'), list):
        raise ValueError('Invalid evaluation ledger')
    held = set()
    for a in ledger['attempts']:
        if (not isinstance(a, dict) or a.get('case_id') not in CASES or a.get('repetition') != 'r1'
                or a.get('reserved_micro_usd') != RESERVATION
                or a.get('outcome') not in {'pending', 'result', 'rejected'}):
            raise ValueError('Invalid evaluation attempt')
        dedupe.timestamp(a.get('reserved_at'))
        if a['outcome'] == 'rejected':
            if type(a.get('http_status')) is not int or a['http_status'] not in {401, 429}:
                raise ValueError('Invalid released reservation')
        else:
            if a['case_id'] in held:
                raise ValueError('Duplicate held case slot')
            held.add(a['case_id'])
    if len(held) > MAX_CALLS or len(held) * RESERVATION > BUDGET:
        raise ValueError('Campaign limit exceeded')
    return ledger, held


def checkpoint():
    try:
        return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def run(case, *, root=ROOT, fixtures=FIXTURES, client=None):
    descriptor = os.open(root, os.O_RDONLY)
    owned = None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record, payload, fixture_hash, size = load_case(case, fixtures)
        ledger, held = load_ledger(root)
        if case in held or len(held) >= MAX_CALLS or (len(held)+1)*RESERVATION > BUDGET:
            raise ValueError('Evaluation case or campaign allowance exhausted')
        destination = root / f'verifier-eval-{case}-r1.json'
        if os.path.lexists(destination):
            raise ValueError('Evaluation result already exists')
        provenance = {'case_id': case, 'repetition': 'r1',
                      'fixture_sha256': fixture_hash,
                      'prompt_sha256': hashlib.sha256(payload['instructions'].encode()).hexdigest(),
                      'verifier_code_sha256': hashlib.sha256(Path(verifier.__file__).read_bytes()).hexdigest(),
                      'repo_checkpoint': checkpoint(), 'model': payload['model'],
                      'reasoning': payload['reasoning']['effort'], 'request_bytes': size}
        if client is None:
            owned = verifier.synthesis.article.make_client()
            client = owned
        attempt = {**provenance, 'reserved_micro_usd': RESERVATION,
                   'reserved_at': datetime.now(timezone.utc).isoformat(), 'outcome': 'pending',
                   'stage': 'send_pending'}
        ledger['attempts'].append(attempt)
        verifier.synthesis.assembler.processing.write_json(ledger, root / LEDGER)
        try:
            response = client.responses.create(**payload)
        except Exception as error:
            status = getattr(error, 'status_code', None)
            if type(status) is int and status in {401, 429}:
                attempt.update(outcome='rejected', http_status=status, stage='request_rejected')
            else:
                attempt['stage'] = 'send_uncertain'
            verifier.synthesis.assembler.processing.write_json(ledger, root / LEDGER)
            raise ValueError('Evaluation request failed; no automatic retry') from None
        attempt.update(outcome='result', stage='response_returned')
        verifier.synthesis.assembler.processing.write_json(ledger, root / LEDGER)
        try:
            findings = verifier.parse_response(response, record)
        except Exception:
            # Fixed stage names only: never persist response text or exception messages.
            attempt['stage'] = 'validation_failed'
            verifier.synthesis.assembler.processing.write_json(ledger, root / LEDGER)
            raise
        result = {'version': 1, **provenance,
                  'verified_at': datetime.now(timezone.utc).isoformat(), 'findings': findings}
        attempt['stage'] = 'storage_pending'
        verifier.synthesis.assembler.processing.write_json(ledger, root / LEDGER)
        try:
            verifier.save_new_result(result, destination)
        except Exception:
            attempt['stage'] = 'storage_failed'
            verifier.synthesis.assembler.processing.write_json(ledger, root / LEDGER)
            raise
        attempt['stage'] = 'stored'
        verifier.synthesis.assembler.processing.write_json(ledger, root / LEDGER)
        return {'status': 'stored', 'case_id': case, 'verdict': findings['verdict'], 'result_file': destination.name}
    finally:
        try:
            if owned is not None:
                owned.close()
        finally:
            os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=CASES, required=True)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--limit', type=int, choices=[1], default=1)
    args = parser.parse_args(argv)
    try:
        if args.live:
            result = run(args.case)
        else:
            _, _, fingerprint, size = load_case(args.case)
            result = {'mode': 'offline', 'case_id': args.case, 'fixture_sha256': fingerprint, 'request_bytes': size}
        print(json.dumps(result, indent=2))
        return 0
    except Exception:
        print('Evaluation stopped; inspect evaluation ledger before any further attempt.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
