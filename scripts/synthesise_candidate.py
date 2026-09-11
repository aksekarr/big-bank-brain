"""Canonical manual synthesis; offline by default, never publishes or verifies."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

import synthesise as synthesis
from verify_candidate import SafetyError, record_diagnostic, response_diagnostic

ROOT = synthesis.assembler.processing.ROOT
LEDGER = 'candidate-synthesis-ledger.json'
MAX_REQUEST_BYTES = synthesis.MAX_REQUEST_BYTES
# Same request/model/output bounds: (20,000 + 1,024)*2.50/M + 4,000*12/M.
RESERVE_MICRO_USD = synthesis.RESERVE_MICRO_USD


def load_ledger(root):
    path = root / LEDGER
    try:
        ledger = synthesis.dedupe.read_json(path) if path.exists() else {'version': 1, 'attempts': []}
        if not isinstance(ledger, dict) or ledger.get('version') != 1 or not isinstance(ledger.get('attempts'), list):
            raise ValueError()
        held = set()
        for attempt in ledger['attempts']:
            sha = attempt['source_input_sha256']
            if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
                raise ValueError()
            if (type(attempt['reserved_micro_usd']) is not int or attempt['reserved_micro_usd'] <= 0
                    or attempt['outcome'] not in {'pending', 'result', 'rejected'}):
                raise ValueError()
            synthesis.dedupe.timestamp(attempt['reserved_at'])
            if attempt['outcome'] == 'rejected':
                if type(attempt.get('http_status')) is not int or attempt['http_status'] not in {401, 429}:
                    raise ValueError()
            else:
                if sha in held:
                    raise ValueError()
                held.add(sha)
        return ledger
    except (ValueError, KeyError, TypeError, OSError):
        raise SafetyError('Canonical synthesis ledger invalid or unreadable; no reset') from None


def prepare(root, now=None):
    packet = synthesis.assembler.assemble(root, now)
    payload = synthesis.request_payload(packet)
    # Hash the exact UTF-8 string placed in the request, not the broader assembler
    # report (which also contains diagnostics/coverage that are not model input).
    content = payload['input'][0]['content'] if payload else None
    provenance = {
        'source_input_sha256': hashlib.sha256(content.encode('utf-8')).hexdigest() if content else None,
        'request_bytes': len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) if payload else 0,
        'window': packet['window'], 'reserved_micro_usd': RESERVE_MICRO_USD,
    }
    if payload:
        provenance.update(model=payload['model'], reasoning=payload['reasoning']['effort'],
                          max_output_tokens=payload['max_output_tokens'],
                          instruction_sha256=hashlib.sha256(payload['instructions'].encode()).hexdigest(),
                          schema_sha256=hashlib.sha256(json.dumps(payload['text']['format']['schema'], sort_keys=True).encode()).hexdigest(),
                          synthesis_sha256=hashlib.sha256(Path(synthesis.__file__).read_bytes()).hexdigest())
    return packet, payload, provenance


def diagnostics(payload, provenance, ledger):
    attempts = [a for a in ledger['attempts'] if a['source_input_sha256'] == provenance['source_input_sha256']]
    consumed = any(a['outcome'] == 'result' for a in attempts)
    pending = any(a['outcome'] == 'pending' for a in attempts)
    fits = provenance['request_bytes'] <= MAX_REQUEST_BYTES
    blockers = []
    if payload is None:
        blockers.append('No valid articles to synthesise')
    if not fits:
        blockers.append('Synthesis request-size blocker; no truncation')
    if consumed:
        blockers.append('Assembled input snapshot already consumed')
    if pending:
        blockers.append('Assembled input snapshot pending; no retry')
    return {**provenance, 'request_cap': MAX_REQUEST_BYTES, 'request_size_permitted': fits,
            'snapshot_consumed': consumed, 'snapshot_pending': pending,
            'live_eligible_ignoring_credentials': not blockers, 'blockers': blockers,
            'publication_approved': False}


def run(root=ROOT, *, live=False, client=None, now=None):
    # Assemble once under its shared lock; this immutable packet is the input for
    # this invocation. Exclusive accounting lock follows, as in historical synthesis.
    packet, payload, provenance = prepare(root, now)
    descriptor = os.open(root, os.O_RDONLY)
    owned = None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = load_ledger(root)
        diagnostic = diagnostics(payload, provenance, ledger)
        if not live:
            return {'mode': 'offline', **diagnostic, 'coverage': packet['coverage'],
                    'assembly_diagnostics': packet['diagnostics']}
        if diagnostic['blockers']:
            raise SafetyError('; '.join(diagnostic['blockers']))
        if client is None:
            if not os.environ.get('OPENAI_API_KEY'):
                raise SafetyError('Missing OPENAI_API_KEY; no reservation or send')
            if os.environ.get('OPENAI_LOG'):
                raise SafetyError('Unset OPENAI_LOG before live synthesis')
            try:
                owned = synthesis.article.make_client()
            except Exception:
                raise SafetyError('Synthesis client initialization failed; no reservation') from None
            client = owned
        attempt = {**provenance, 'reserved_at': datetime.now(timezone.utc).isoformat(),
                   'outcome': 'pending', 'response_returned': False}
        record_diagnostic(attempt, 'send', 'pending')
        ledger['attempts'].append(attempt)
        write = synthesis.assembler.processing.write_json
        write(ledger, root / LEDGER)
        try:
            response = client.responses.create(**payload)
        except Exception as error:
            status = getattr(error, 'status_code', None)
            if type(status) is int and status in {401, 429}:
                attempt.update(outcome='rejected', http_status=status)
            record_diagnostic(attempt, 'send', 'rejected' if attempt['outcome']=='rejected' else 'uncertain')
            write(ledger, root / LEDGER)
            raise SafetyError('Synthesis send failed; inspect safe ledger status; no automatic retry') from None
        attempt.update(outcome='result', response_returned=True)
        status = getattr(response, 'status', None)
        attempt['response_status'] = status if isinstance(status, str) and status in {
            'completed', 'incomplete', 'failed', 'cancelled', 'queued', 'in_progress'} else 'unknown'
        record_diagnostic(attempt, 'response', 'response_returned')
        write(ledger, root / LEDGER)
        try:
            findings = synthesis.parse_response(response, packet)
        except Exception as error:
            code = response_diagnostic(response, error)
            record_diagnostic(attempt, 'validation', code)
            write(ledger, root / LEDGER)
            raise SafetyError('Returned synthesis rejected: '+code+'; input consumed') from None
        # Use the exact request input for stored source context, not a fresh assembly.
        data = json.loads(payload['input'][0]['content'])
        result = {'version': 1, **provenance, 'generated_at': datetime.now(timezone.utc).isoformat(),
                  'coverage': {'article_count': len(data['articles']),
                               'institutions': sorted({a['institution'] for a in data['articles']})},
                  'articles': [{k: a[k] for k in ['article_ref', 'institution', 'source_name',
                                                 'title', 'url', 'publication_date', 'claims', 'read_depth']}
                               for a in data['articles']],
                  'synthesis': findings}
        try:
            write(result, root / synthesis.RESULT)
        except Exception:
            record_diagnostic(attempt, 'storage', 'storage_failed')
            write(ledger, root / LEDGER)
            raise SafetyError('Synthesis storage failed; previous candidate preserved, input consumed') from None
        # Read the successful atomic replacement while holding the directory lock.
        # Do not hash a reconstructed JSON representation or modify the result.
        attempt['stored_result_sha256'] = hashlib.sha256((root / synthesis.RESULT).read_bytes()).hexdigest()
        record_diagnostic(attempt, 'storage', 'stored')
        write(ledger, root / LEDGER)
        return {'status': 'stored', 'result_file': synthesis.RESULT, **provenance,
                'publication_approved': False}
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
        print(json.dumps(run(live=args.live), indent=2))
        return 0
    except SafetyError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print('Canonical synthesis stopped: input, ledger, lock or internal/storage error; no publication.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
