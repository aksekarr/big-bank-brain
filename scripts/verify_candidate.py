"""Canonical MVP verification: PASS requires human review; never publishes."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import verify_presuppositions as verifier

base = verifier.base
ROOT = base.synthesis.assembler.processing.ROOT
LEDGER = 'candidate-verification-ledger.json'
# Approved canonical-only bounds; never expand automatically.
# (21,000 + 1,024) * $2.50/million + 6,000 * $12/million.
MAX_REQUEST_BYTES = 21000
RESERVE_MICRO_USD = 127060
REVIEW_RESULT = 'candidate-review.json'
ATTEMPT_RESULT = 'candidate-verification.json'


class SafetyError(ValueError):
    """Only controlled, non-sensitive messages may reach the CLI."""


def load_ledger(root):
    path = root / LEDGER
    try:
        ledger = base.dedupe.read_json(path) if path.exists() else {'version': 1, 'attempts': []}
        if not isinstance(ledger, dict) or ledger.get('version') != 1 or not isinstance(ledger.get('attempts'), list):
            raise ValueError()
        held = set()
        for attempt in ledger['attempts']:
            sha = attempt['source_snapshot_sha256']
            if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
                raise ValueError()
            if (type(attempt['reserved_micro_usd']) is not int or attempt['reserved_micro_usd'] <= 0
                    or attempt['outcome'] not in {'pending', 'result', 'rejected'}):
                raise ValueError()
            base.dedupe.timestamp(attempt['reserved_at'])
            if attempt['outcome'] == 'rejected':
                if type(attempt.get('http_status')) is not int or attempt['http_status'] not in {401, 429}:
                    raise ValueError()
            else:
                if sha in held:
                    raise ValueError()
                held.add(sha)
        return ledger
    except (ValueError, KeyError, TypeError, OSError):
        raise SafetyError('Canonical ledger invalid or unreadable; preserved without reset') from None


def diagnostics(provenance, ledger):
    attempts = [a for a in ledger['attempts']
                if a['source_snapshot_sha256'] == provenance['source_snapshot_sha256']]
    consumed = any(a['outcome'] == 'result' for a in attempts)
    pending = any(a['outcome'] == 'pending' for a in attempts)
    fits = provenance['request_bytes'] <= MAX_REQUEST_BYTES
    blockers = []
    if not fits:
        blockers.append('Request-size blocker: exceeds configured cap; no truncation')
    if consumed:
        blockers.append('Synthesis snapshot already consumed')
    if pending:
        blockers.append('Synthesis snapshot has pending uncertain attempt; no retry')
    return {**provenance, 'request_cap': MAX_REQUEST_BYTES, 'request_size_permitted': fits,
            'snapshot_consumed': consumed, 'snapshot_pending': pending,
            'live_eligible_ignoring_credentials': not blockers, 'blockers': blockers,
            'pass_means': 'human_review_required', 'publication_approved': False}


def prepare(root):
    raw = (root / base.synthesis.RESULT).read_bytes()
    def invalid(_):
        raise ValueError('Non-finite JSON number')
    snapshot = json.loads(raw.decode('utf-8'), object_pairs_hook=base.dedupe.object_pairs,
                          parse_constant=invalid)
    payload = verifier.request_payload(snapshot)
    generated_at = base.dedupe.timestamp(snapshot.get('generated_at'))
    window = snapshot.get('window')
    if not isinstance(window, dict) or window.get('timezone') != 'Europe/London':
        raise ValueError('Invalid synthesis window')
    start, end = [base.synthesis.date.fromisoformat(window[k]) for k in ('start_date', 'end_date')]
    if (end-start).days != 6:
        raise ValueError('Invalid synthesis window')
    size = len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    provenance = {
        'model': payload['model'], 'reasoning': payload['reasoning']['effort'],
        'max_output_tokens': payload['max_output_tokens'], 'request_bytes': size,
        'source_synthesis_generated_at': generated_at,
        'source_snapshot_sha256': hashlib.sha256(raw).hexdigest(),
        'window': {'timezone': 'Europe/London', 'start_date': start.isoformat(), 'end_date': end.isoformat()},
        'verifier': 'verify_presuppositions',
        'verifier_sha256': hashlib.sha256(Path(verifier.__file__).read_bytes()).hexdigest(),
        'instruction_sha256': hashlib.sha256(payload['instructions'].encode()).hexdigest(),
        'schema_sha256': hashlib.sha256(json.dumps(payload['text']['format']['schema'], sort_keys=True).encode()).hexdigest(),
    }
    return snapshot, payload, provenance


def response_diagnostic(response, error):
    """Classify safe envelope facts/types only; the evaluated parser decides validity."""
    if not isinstance(error, ValueError):
        return 'unexpected_internal_error'
    if getattr(response, 'status', None) == 'incomplete':
        return 'incomplete'
    for part in getattr(response, 'output', []) or []:
        if getattr(part, 'type', None) == 'message':
            for content in getattr(part, 'content', []) or []:
                if getattr(content, 'type', None) == 'refusal':
                    return 'refusal'
    if isinstance(error, json.JSONDecodeError):
        return 'invalid_json'
    # ValueError/SpikeError do not expose separate schema/reference/audit codes.
    return 'validation_failed_unclassified'


def record_diagnostic(attempt, stage, code):
    attempt.update(diagnostic_stage=stage, diagnostic_code=code,
                   diagnostic_at=datetime.now(timezone.utc).isoformat())


def run(root=ROOT, *, live=False, client=None):
    descriptor = os.open(root, os.O_RDONLY)
    owned = None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        snapshot, payload, provenance = prepare(root)
        ledger = load_ledger(root)
        diagnostic = diagnostics(provenance, ledger)
        if not live:
            return {'mode': 'offline', **diagnostic}
        if diagnostic['blockers']:
            raise SafetyError('; '.join(diagnostic['blockers']))
        if client is None:
            if not os.environ.get('OPENAI_API_KEY'):
                raise SafetyError('Missing OPENAI_API_KEY; no reservation or send')
            if os.environ.get('OPENAI_LOG'):
                raise SafetyError('Unset OPENAI_LOG before live verification')
            try:
                owned = base.synthesis.article.make_client()
            except Exception:
                raise SafetyError('Client initialization failed; no reservation or send') from None
            client = owned
        attempt = {'reserved_at': datetime.now(timezone.utc).isoformat(),
                   'reserved_micro_usd': RESERVE_MICRO_USD,
                   **provenance, 'outcome': 'pending', 'response_returned': False}
        record_diagnostic(attempt, 'send', 'pending')
        ledger['attempts'].append(attempt)
        write = base.synthesis.assembler.processing.write_json
        write(ledger, root / LEDGER)
        try:
            response = client.responses.create(**payload)
        except Exception as error:
            status = getattr(error, 'status_code', None)
            if type(status) is int and status in {401, 429}:
                attempt.update(outcome='rejected', http_status=status)
            record_diagnostic(attempt, 'send', 'rejected' if attempt['outcome']=='rejected' else 'uncertain')
            write(ledger, root / LEDGER)
            raise SafetyError('Request rejected (401/429); no automatic retry' if attempt['outcome'] == 'rejected'
                              else 'Send outcome uncertain; snapshot pending, no retry') from None
        attempt.update(outcome='result', response_returned=True)
        status = getattr(response, 'status', None)
        attempt['response_status'] = status if isinstance(status, str) and status in {
            'completed', 'incomplete', 'failed', 'cancelled', 'queued', 'in_progress'} else 'unknown'
        record_diagnostic(attempt, 'response', 'response_returned')
        write(ledger, root / LEDGER)
        try:
            findings = verifier.parse_response(response, snapshot)
        except Exception as error:
            code = response_diagnostic(response, error)
            record_diagnostic(attempt, 'validation', code)
            write(ledger, root / LEDGER)
            raise SafetyError('Returned response rejected: ' + code +
                              '; snapshot consumed, previous outputs preserved') from None
        result = {'version': 1, **provenance,
                  'verified_at': datetime.now(timezone.utc).isoformat(), **findings,
                  'review_status': 'human_review_required' if findings['verdict']=='pass' else 'blocked',
                  'publication_approved': False}
        # Latest valid diagnostic may be FAIL. Only PASS can replace the review candidate.
        # Neither file is a published digest or evidence of human approval.
        try:
            write(result, root / ATTEMPT_RESULT)
            if findings['verdict'] == 'pass':
                write(result, root / REVIEW_RESULT)
        except Exception:
            record_diagnostic(attempt, 'storage', 'storage_failed')
            write(ledger, root / LEDGER)
            raise SafetyError('Result storage failed; snapshot consumed') from None
        record_diagnostic(attempt, 'storage', 'stored')
        write(ledger, root / LEDGER)
        return {'status': result['review_status'], 'verdict': findings['verdict'],
                'publication_approved': False, 'request_bytes': provenance['request_bytes'],
                'result_file': ATTEMPT_RESULT,
                'review_result_file': REVIEW_RESULT if findings['verdict']=='pass' else None}
    finally:
        try:
            if owned is not None:
                owned.close()
        finally:
            os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--limit', type=int, choices=[1], default=1)
    args = parser.parse_args(argv)
    try:
        result = run(live=args.live)
        print(json.dumps(result, indent=2))
        return 1 if result.get('status') == 'blocked' else 0
    except SafetyError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print('Candidate verification stopped by an internal/input/storage error. No publication approval.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
