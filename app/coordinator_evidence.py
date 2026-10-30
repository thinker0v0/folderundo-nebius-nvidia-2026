"""Bounded common evidence around one verified legacy FolderUndo archive.

This format records synthetic local observations. It never authenticates history,
establishes original-fixture preservation, or accepts the original product gates.
Reading either layer never extracts files, performs inference, or resumes work.
"""
import hashlib
import io
import json
import math
import re
import stat
import uuid
import zipfile

from .core import Refusal

FORMAT = 'folderundo-coordinator-evidence-v1'
DOMAIN_FORMAT = 'folderundo-evidence-v1'
LIMIT = 8 * 1024 * 1024
JSON_LIMIT = 1024 * 1024
INDEX_LIMIT = 65536
TRACE_LIMIT = 200
MEMBERS = {'checks.json', 'trace.jsonl', 'usage.json', 'replay.json', 'result.json'}
ACTIONS = {'create', 'create-preserving', 'preview', 'approve', 'apply', 'undo',
           'cancel', 'resume', 'recover', 'conflict-fixture', 'refresh', 'scan', 'export'}
DOMAIN_STATES = {'APPLIED', 'UNDONE', 'CONFLICT'}
PRESERVATION = {'established': False,
                'scope': 'Original fixture preservation is not independently established by this receipt.'}
NOTE = 'Archive internal consistency only; no new inference or filesystem changes.'
REASON = 'Synthetic demonstration evidence; original product acceptance is not established.'
COMMAND = 'python -m app.cli replay --bundle <download> --offline'
CONTEXT_KEYS = {'run_id', 'fixture_id', 'config_sha', 'input_sha', 'oracle_sha',
                'codeidentity', 'request_id', 'action', 'state', 'result', 'events',
                'task', 'http_status', 'workspace_id', 'preservation'}
TASK_KEYS = {'id', 'run_id', 'kind', 'parent_task_id', 'origin', 'original_deadline',
             'timeout', 'paused_total', 'pause_started', 'status', 'finished_at', 'reason'}
EVENT_KEYS = {'seq', 'time', 'kind', 'message'}
EVENT_OPTIONAL = {'request_id', 'action', 'task_id', 'deadline', 'paused_total', 'http_status'}
RESULT_KEYS = {'format', 'run_id', 'fixture_id', 'workspace_id', 'request_id', 'action',
               'state', 'http_status', 'recorded', 'provider', 'accepted', 'error_code',
               'task', 'preservation', 'domain_payload'}


def _fail(reason):
    raise ValueError(reason)


def _dump(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _object(value, required, optional=frozenset()):
    if not isinstance(value, dict) or not required <= set(value) <= required | optional:
        _fail('object schema')
    return value


def _json(raw, limit=JSON_LIMIT):
    if not isinstance(raw, bytes) or len(raw) > limit:
        _fail('JSON limit')
    def pairs(items):
        value = {}
        for key, child in items:
            if key in value:
                _fail('duplicate JSON key')
            value[key] = child
        return value
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: _fail('nonfinite JSON'))
    def bounded(item, depth=0):
        if depth > 20:
            _fail('JSON depth')
        if isinstance(item, dict):
            if len(item) > 1000:
                _fail('JSON object size')
            for key, child in item.items():
                if len(key) > 160:
                    _fail('JSON key size')
                bounded(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 1000:
                _fail('JSON array size')
            for child in item:
                bounded(child, depth + 1)
        elif isinstance(item, str) and len(item.encode('utf-8')) > JSON_LIMIT:
            _fail('JSON text size')
    bounded(value)
    return value


def _hash(value, nullable=False):
    if nullable and value is None:
        return value
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        _fail('hash schema')
    return value


def _uuid(value, nullable=False):
    if nullable and value is None:
        return value
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        _fail('UUID schema')
    return value


def _number(value, nullable=False):
    if nullable and value is None:
        return value
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        _fail('number schema')
    return value


def _integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        _fail('integer schema')
    return value


def _code(value, nullable=False):
    if nullable and value is None:
        return value
    if not isinstance(value, str) or re.fullmatch('[A-Z][A-Z0-9_]{0,95}', value) is None:
        _fail('code schema')
    return value


def _relative(value):
    if (not isinstance(value, str) or len(value) > 240
            or re.fullmatch('[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*', value) is None
            or any(part in ('.', '..') or len(part) > 80 for part in value.split('/'))):
        _fail('relative path schema')
    return value


def _identity(value):
    if not isinstance(value, dict) or not 1 <= len(value) <= 100:
        _fail('code identity schema')
    for name, sha in value.items():
        _relative(name)
        _hash(sha)
    return value


def _task(value, run_id):
    _object(value, TASK_KEYS)
    _uuid(value['id'])
    if value['run_id'] != run_id:
        _fail('task run binding')
    _uuid(value['parent_task_id'], True)
    if value['kind'] not in ('FORWARD', 'UNDO', 'SAFETY_RECOVERY', 'OBSERVATION'):
        _fail('task kind')
    if value['status'] not in ('ACTIVE', 'PAUSED', 'COMPLETE', 'FAILED'):
        _fail('task status')
    for name in ('origin', 'original_deadline', 'timeout', 'paused_total'):
        _number(value[name])
    for name in ('pause_started', 'finished_at'):
        _number(value[name], True)
    if not 0 < value['timeout'] <= 180:
        _fail('task timeout')
    if abs(value['original_deadline'] - (value['origin'] + value['timeout'])) > 0.00001:
        _fail('task deadline binding')
    if ((value['status'] == 'PAUSED' and value['pause_started'] is None)
            or (value['status'] == 'ACTIVE' and value['pause_started'] is not None)):
        _fail('task pause schema')
    _code(value['reason'], True)
    return value


def _events(value):
    if not isinstance(value, list) or len(value) > TRACE_LIMIT:
        _fail('trace count')
    previous = 0
    for event in value:
        _object(event, EVENT_KEYS, EVENT_OPTIONAL)
        seq = _integer(event['seq'], 1, 2**53 - 1)
        if seq <= previous:
            _fail('trace sequence')
        previous = seq
        _number(event['time'])
        kind = event['kind']
        if kind not in ACTIONS:_code(kind)  # Preserve bounded historical action event names.
        if event['message'] != kind.replace('_', ' ').capitalize():
            _fail('trace message schema')
        for name in ('request_id', 'task_id'):
            if name in event:
                _uuid(event[name])
        for name in ('deadline', 'paused_total'):
            if name in event:
                _number(event[name])
        if 'http_status' in event:
            _integer(event['http_status'], 200, 599)
        if 'action' in event and event['action'] not in ACTIONS:
            _fail('trace action')
    return value


def _error_code(value):
    if not isinstance(value, dict):
        _fail('HTTP result schema')
    error = value.get('error')
    if error is None:
        return None
    code = error.get('code') if isinstance(error, dict) else None
    if isinstance(code, str) and re.fullmatch('[A-Z][A-Z0-9_]{0,95}', code):
        return code
    # Never copy arbitrary exception text, paths, or credentials into the receipt.
    return 'RECORDED_FAILURE'


def _context(value):
    _object(value, CONTEXT_KEYS, {'domain_sha','runtime_versions'})
    runtime=value.get('runtime_versions')
    if runtime is not None:
        _object(runtime, {'python','sqlite','implementation'})
        if any(not isinstance(runtime[k],str) or re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}',runtime[k]) is None for k in ('python','sqlite')):
            _fail('runtime version schema')
        if runtime['implementation'] not in ('cpython','pypy'):_fail('runtime implementation')
    _uuid(value['run_id'])
    _uuid(value['request_id'])
    _uuid(value['workspace_id'], True)
    if value['fixture_id'] not in ('dev20', 'synthetic-4', 'synthetic-12', 'live-purchase', 'live-injection'):
        _fail('fixture schema')
    _hash(value['config_sha'], True)
    _hash(value['input_sha'], True)
    if value['oracle_sha'] is not None:
        _fail('oracle evidence unavailable')
    if 'domain_sha' in value:
        _hash(value['domain_sha'], True)
    _identity(value['codeidentity'])
    if value['action'] not in ACTIONS:
        _fail('action schema')
    _code(value['state'])
    _integer(value['http_status'], 200, 599)
    _task(value['task'], value['run_id'])
    _events(value['events'])
    if _dump(value['preservation']) != _dump(PRESERVATION):
        _fail('unestablished preservation')
    _error_code(value['result'])
    return value


def _read_outer(raw):
    if not isinstance(raw, bytes) or len(raw) > LIMIT:
        _fail('archive limit')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        allowed = MEMBERS | {'index.json', 'domain.zip'}
        if len(names) != len(set(names)) or set(names) not in (MEMBERS | {'index.json'}, allowed):
            _fail('archive member whitelist')
        expanded = sum(item.file_size for item in infos)
        if expanded > LIMIT:
            _fail('expanded limit')
        for item in infos:
            kind = stat.S_IFMT(item.external_attr >> 16)
            if (item.is_dir() or item.flag_bits & 1 or kind not in (0, stat.S_IFREG)
                    or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                _fail('unsafe archive member')
            if item.filename != 'domain.zip' and item.file_size > JSON_LIMIT:
                _fail('document limit')
            if item.filename == 'index.json' and item.file_size > INDEX_LIMIT:
                _fail('index limit')
        data = {item.filename: archive.read(item) for item in infos}
    return data, expanded


def _manifest_entry(value):
    if value is None:
        return None
    return {'file_id': _uuid(value['file_id']), 'relative_path': _relative(value['relative_path']),
            'sha256': _hash(value['sha256']), 'size_bytes': _integer(value['size_bytes'], 0, 65536)}


def _domain(raw, domain_verify, outer_expanded):
    if not callable(domain_verify):
        _fail('domain verifier required')
    if raw is None:
        return None
    if not isinstance(raw, bytes) or len(raw) > LIMIT:
        _fail('domain archive limit')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        if len(infos) > 106 or outer_expanded + sum(item.file_size for item in infos) > LIMIT:
            _fail('aggregate nested expansion limit')
        indexes = [item for item in infos if item.filename == 'index.json']
        if len(indexes) != 1 or indexes[0].file_size > INDEX_LIMIT:
            _fail('domain index limit')
        meta = _json(archive.read(indexes[0]), INDEX_LIMIT)
        if not isinstance(meta, dict) or meta.get('format') != DOMAIN_FORMAT or meta.get('recorded') is not True:
            _fail('nested coordinator or unknown domain format')
        verified = domain_verify(raw)
        if (not isinstance(verified, dict) or verified.get('verified') is not True
                or verified.get('recorded') is not True or verified.get('history_authenticated') is not False):
            _fail('domain verification result')
        journal = _json(archive.read('journal.json'))
        journey = _json(archive.read('journey-context.json'))
        current = _json(archive.read('current.json'))
        plan = _json(journal['plan'].encode('utf-8'))
    if journal['state'] not in DOMAIN_STATES or verified.get('state') != journal['state']:
        _fail('domain not export eligible')
    if type(verified.get('current_drift')) is not bool or type(verified.get('current_exact_restoration')) is not bool:
        _fail('domain observation schema')
    operations = []
    for item in journal['operations']:
        expected = _json(item['expected'].encode('utf-8'))
        operations.append({'id': _uuid(item['id']), 'direction': item['direction'],
                           'state': item['state'], 'from_path': _relative(item['from_path']),
                           'to_path': _relative(item['to_path']), 'expected': _manifest_entry(expected),
                           'parent_forward_op_id': _uuid(item['parent_forward_op_id'], True)})
    reverse_parents = {item['parent_forward_op_id'] for item in operations if item['direction'] == 'UNDO'}
    effective = [item for item in operations if item['direction'] == 'UNDO' or item['id'] not in reverse_parents]
    counts = {name: sum(item['state'] == name for item in effective)
              for name in ('PENDING', 'PREPARED', 'UNDO_PREPARED', 'COMMITTED', 'UNDONE', 'CANCELLED', 'CONFLICT')}
    conflicts = []
    for item in journal.get('conflicts', []):
        if item['reason'] != 'CURRENT_PATH_DIFFERS':
            _fail('domain conflict schema')
        conflicts.append({'path': _relative(item['path']), 'reason': item['reason'],
                          'expected': _manifest_entry(item['expected']), 'current': _manifest_entry(item['current'])})
    payload = {'batch_id': _uuid(journal['id']), 'mode': journal['mode'], 'state': journal['state'],
               'before_sha': _hash(journal['before_sha']), 'after_sha': _hash(journal['after_sha'], True),
               'plan_sha': _hash(plan['plan_sha']), 'current_manifest_sha': _sha(_dump(current)),
               'current_drift': verified['current_drift'],
               'current_exact_restoration': verified['current_exact_restoration'],
               'files': _integer(verified['files'], 1, 100), 'operations': operations,
               'effective_operation_counts': counts, 'conflicts': conflicts}
    return {'sha256': _sha(raw), 'workspace_id': _uuid(meta['workspace_id']),
            'fixture_id': journey['creation']['fixture_id'], 'config_sha': journey['creation']['config_sha'],
            'payload': payload, 'verification': verified, 'provider_evidence':journey['preview'].get('provider_evidence')}


def _documents(context, domain):
    context = _context(context)
    error_code = _error_code(context['result'])
    if domain is None:
        cancelled = context['state'] == 'CANCELLED' and context['http_status'] == 200 and context['action'] == 'cancel'
        failed = context['state'] in ('ERROR', 'ERROR_INTERRUPTED') and context['http_status'] >= 400
        if not (cancelled or failed) or context['input_sha'] is not None or context.get('domain_sha') is not None:
            _fail('receipt without domain is not finalized failure or cancellation')
    else:
        if (context['state'] != domain['payload']['state'] or context['workspace_id'] != domain['workspace_id']
                or context['fixture_id'] != domain['fixture_id'] or context['config_sha'] != domain['config_sha']
                or context['input_sha'] != domain['payload']['before_sha']
                or context.get('domain_sha', domain['sha256']) != domain['sha256']):
            _fail('domain receipt binding')
    live = context['fixture_id'] in ('live-purchase','live-injection')
    live_evidence = domain.get('provider_evidence') if domain else None
    provider_state = ('LIVE_RECORDED' if live_evidence else 'UNKNOWN') if live else 'CLOSED'
    code_sha = _sha(_dump(context['codeidentity']))
    candidate_sha = domain['sha256'] if domain is not None else None
    checks = {'run_id': context['run_id'], 'fixture_id': context['fixture_id'],
              'input_sha': context['input_sha'], 'oracle_sha': None, 'candidate_sha': candidate_sha,
              'config_sha': context['config_sha'], 'code_sha': code_sha,
              'checks': [
                  {'name': 'registered_archive_members', 'pass': True,
                   'expected': 'Exact whitelist, hashes, sizes and bounded nested content', 'actual': 'VERIFIED'},
                  {'name': 'domain_archive_consistency', 'pass': domain is not None,
                   'expected': 'Independent legacy domain verifier', 'actual': 'VERIFIED' if domain is not None else 'NOT_AVAILABLE'},
                  {'name': 'original_fixture_preservation', 'pass': False,
                   'expected': 'Independent original-fixture comparison', 'actual': 'UNKNOWN'},
                  {'name': 'provider_configuration', 'pass': True, 'expected': 'Explicit live broker' if live else 'CLOSED', 'actual':provider_state},
                  {'name': 'original_product_acceptance', 'pass': False,
                   'expected': 'All frozen original product gates accepted', 'actual': 'NOT_ESTABLISHED'}],
              'accepted': False, 'reason': REASON}
    usage = {'format': FORMAT, 'run_id': context['run_id'], 'request_id': context['request_id'],
             'provider': 'CLOSED', 'model': None, 'calls': 0, 'input_tokens': 0, 'output_tokens': 0,
             'total_tokens': 0, 'inference_performed': False, 'history_authenticated': False,
             'scope': 'Recorded local execution; no provider inference requested.'}
    if live:
        actual = live_evidence['response']['provider'] if live_evidence else None
        tokens = actual['usage'] if actual else {}
        usage.update(provider=provider_state, model=actual['model'] if actual else None, calls=1 if actual else None, input_tokens=tokens.get('prompt_tokens'),output_tokens=tokens.get('completion_tokens'),total_tokens=tokens.get('total_tokens'),inference_performed=True if actual else None,scope='Recorded live proposal; replay performs no inference. Failed receipt does not establish provider usage.')
    replay = {'format': FORMAT, 'run_id': context['run_id'], 'request_id': context['request_id'],
              'command': COMMAND, 'codeidentity': context['codeidentity'], 'code_sha': code_sha,
              'dependency_lock_sha': context['codeidentity'].get('requirements.lock'),
              'runtime_versions': context.get('runtime_versions'),
              'environment_verified': False, 'domain_format': DOMAIN_FORMAT if domain is not None else None,
              'recorded': True, 'provider': provider_state, 'history_authenticated': False,
              'trace_scope': 'At most the last 200 coordinator events; earlier events may be omitted.', 'note': NOTE}
    result = {'format': FORMAT, 'run_id': context['run_id'], 'fixture_id': context['fixture_id'],
              'workspace_id': context['workspace_id'], 'request_id': context['request_id'],
              'action': context['action'], 'state': context['state'], 'http_status': context['http_status'],
              'recorded': True, 'provider': provider_state, 'accepted': False, 'error_code': error_code,
              'task': context['task'], 'preservation': PRESERVATION,
              'domain_payload': domain['payload'] if domain is not None else None}
    trace = b''.join(_dump(event) + b'\n' for event in context['events'])
    documents = {'checks.json': _dump(checks), 'trace.jsonl': trace, 'usage.json': _dump(usage),
                 'replay.json': _dump(replay), 'result.json': _dump(result)}
    if any(len(raw) > JSON_LIMIT for raw in documents.values()):
        _fail('document limit')
    return documents


def verify_evidence(raw: bytes, domain_verify: callable) -> dict:
    """Verify structure and bindings, using one independent legacy domain check."""
    try:
        data, expanded = _read_outer(raw)
        meta = _json(data['index.json'], INDEX_LIMIT)
        _object(meta, {'format', 'run_id', 'recorded', 'provider', 'history_authenticated', 'members'})
        if (meta['format'] != FORMAT or meta['recorded'] is not True or meta['provider'] not in ('CLOSED','LIVE_RECORDED','UNKNOWN')
                or meta['history_authenticated'] is not False):
            _fail('index format')
        _uuid(meta['run_id'])
        payload = {name: content for name, content in data.items() if name != 'index.json'}
        expected_members = {name: {'sha256': _sha(content), 'size': len(content)} for name, content in payload.items()}
        if _dump(meta['members']) != _dump(expected_members):
            _fail('member hash or size binding')
        result = _json(data['result.json'])
        _object(result, RESULT_KEYS)
        checks = _json(data['checks.json'])
        replay = _json(data['replay.json'])
        usage = _json(data['usage.json'])
        trace = data['trace.jsonl']
        if trace and (not trace.endswith(b'\n') or len(trace.splitlines()) > TRACE_LIMIT):
            _fail('trace framing')
        events = [_json(line, 8192) for line in trace.splitlines()]
        _events(events)
        if result['run_id'] != meta['run_id'] or result['provider']!=meta['provider']:
            _fail('index run binding')
        _code(result['error_code'], True)
        domain = _domain(data.get('domain.zip'), domain_verify, expanded)
        context = {key: result[key] for key in ('run_id', 'fixture_id', 'workspace_id', 'request_id',
                   'action', 'state', 'http_status', 'task', 'preservation')}
        context.update(config_sha=checks['config_sha'], input_sha=checks['input_sha'], oracle_sha=checks['oracle_sha'],
                       codeidentity=replay['codeidentity'], events=events,runtime_versions=replay['runtime_versions'],
                       result={'error': {'code': result['error_code']}} if result['error_code'] else {})
        expected = _documents(context, domain)
        for name in MEMBERS:
            # Canonical bytes bind types as well as values (True must not equal 1).
            if data[name] != expected[name]:
                _fail('document schema or semantic binding')
        verified = dict(domain['verification']) if domain is not None else {
            'verified': True, 'recorded': True, 'state': result['state'], 'files': None,
            'current_drift': None, 'current_exact_restoration': None,
            'journey_context': None, 'classification_context_present': False,
            'history_authenticated': False, 'note': NOTE}
        verified.update(format=FORMAT, run_id=result['run_id'], request_id=result['request_id'],
                        accepted=False, domain_verified=domain is not None,
                        original_fixture_preservation='UNKNOWN', provider=result['provider'],
                        receipt_kind='domain_evidence' if domain is not None else 'failure_receipt')
        return verified
    except (KeyError, TypeError, ValueError, OSError, zipfile.BadZipFile, UnicodeError,
            RuntimeError, RecursionError, OverflowError, Refusal) as error:
        raise Refusal('ERROR_EVIDENCE:coordinator envelope') from error


def build_evidence(context: dict, domain_raw: bytes | None) -> bytes:
    """Build a deterministic receipt; refuse unsupported claims and unbound data."""
    from .export import verify
    try:
        # Leave room for outer metadata; the complete aggregate cap is checked below.
        domain = _domain(domain_raw, verify, len(domain_raw) if domain_raw is not None else 0)
        payload = _documents(context, domain)
        if domain_raw is not None:
            payload['domain.zip'] = domain_raw
        meta = {'format': FORMAT, 'run_id': context['run_id'], 'recorded': True,
                'provider':_json(payload['result.json'])['provider'], 'history_authenticated': False,
                'members': {name: {'sha256': _sha(content), 'size': len(content)} for name, content in payload.items()}}
        payload['index.json'] = _dump(meta)
        if sum(len(content) for content in payload.values()) > LIMIT:
            _fail('expanded limit')
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(payload):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                archive.writestr(info, payload[name])
        raw = output.getvalue()
        verify_evidence(raw, verify)
        return raw
    except (KeyError, TypeError, ValueError, OSError, zipfile.BadZipFile, UnicodeError,
            RuntimeError, RecursionError, OverflowError, Refusal) as error:
        raise Refusal('ERROR_EVIDENCE:coordinator envelope') from error
