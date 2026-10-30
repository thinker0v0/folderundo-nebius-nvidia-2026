"""PROPOSED: bounded worker actions around the unchanged synthetic Store.

This module must run only in the coordinator's isolated worker process.  It has
no HTTP listener, provider adapter, subprocess launcher, or automatic retry.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid
import zipfile
import io

from . import core, classifier, policy, live_provider
from .export import bundle, parse, verify, LIMIT, journey_context


CONFIG_SHA = core.digest({'fixture': 'synthetic-v1', 'proposal': 'reference-cycle-v1', 'provider': 'CLOSED'})
FIXTURES = {'synthetic-4': 4, 'synthetic-12': 12}
MUTATIONS = frozenset(('create', 'create-preserving', 'preview', 'approve', 'apply',
                       'resume', 'undo', 'cancel', 'recover', 'conflict-fixture'))
EMPTY_ACTIONS = frozenset(('resume', 'cancel', 'recover', 'refresh', 'scan', 'export', 'conflict-fixture'))
TERMINAL_EXPORT = frozenset(('APPLIED', 'UNDONE', 'CONFLICT'))


class Error(Exception):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def classification_context():
    return {'policy_sha': core.digest(policy.public_policy()), 'declared_policy_sha': policy.POLICY_SHA,
            'classifier_source_sha': hashlib.sha256(Path(classifier.__file__).read_bytes()).hexdigest(),
            'policy_source_sha': hashlib.sha256(Path(policy.__file__).read_bytes()).hexdigest()}


def config_sha(mode):
    if mode == 'B1':
        return core.digest({'mode':'B1', 'fixtures':live_provider.FIXTURES, 'classification':classification_context(), 'adapter_sha':hashlib.sha256(Path(live_provider.__file__).read_bytes()).hexdigest()})
    return core.digest({'fixture': 'registered-dev20', 'mode': 'B0', 'classification': classification_context(),
                        'provider': 'CLOSED'}) if mode == 'B0' else CONFIG_SHA


def identifier(value):
    if not isinstance(value, str):
        raise Error('INVALID_ID')
    try:
        return core.uid(value)
    except (core.Refusal, ValueError, TypeError, AttributeError):
        raise Error('INVALID_ID') from None


def exact(body, fields):
    if not isinstance(body, dict) or set(body) != set(fields):
        raise Error('INVALID_SCHEMA')
    for key, value in body.items():
        if key == 'operation_ids':
            if not isinstance(value, list) or not 1 <= len(value) <= 100:
                raise Error('INVALID_SCHEMA')
            for item in value:
                identifier(item)
        elif not isinstance(value, str) or not 1 <= len(value) <= 128:
            raise Error('INVALID_SCHEMA')


def failure(code):
    return {'error': {'code': code,
                      'changed': 'No new movement is authorized by this error; inspect the journal for earlier committed operations.',
                      'preserved': 'Existing synthetic files and journal retained; no automatic overwrite or deletion.',
                      'next_action': 'Refresh status and scan. For conflicts, inspect both paths and export diagnostics; do not retry with changed approval.'}}


def _hash(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise Error('INVALID_SCHEMA')


def validate_request(action, run_or_None, body, live_config=None):
    """Syntactic/current-config admission only; the coordinator owns queue checks."""
    run = run_or_None
    if action in ('create', 'create-preserving'):
        exact(body, ('fixture_id', 'mode', 'config_sha'))
        if body['mode'] == 'B2' or (body['mode'] == 'B1' and live_provider.config(live_config) is None):
            raise Error('PROVIDER_CLOSED', 423)
        if not ((body['mode'] == 'reference' and body['fixture_id'] in FIXTURES)
                or (body['mode'] == 'B0' and body['fixture_id'] == 'dev20')
                or (body['mode'] == 'B1' and body['fixture_id'] in live_provider.FIXTURES)):
            raise Error('INVALID_CONFIGURATION')
        if body['config_sha'] != config_sha(body['mode']):
            raise Error('INVALID_CONFIGURATION')
        return
    if run is None:
        raise Error('UNKNOWN_RUN', 404)
    if action == 'preview':
        exact(body, ('manifest_sha', 'policy_version', 'mode'))
        if body['mode'] == 'B2' or (body['mode'] == 'B1' and live_provider.config(live_config) is None):
            raise Error('PROVIDER_CLOSED', 423)
        if body['mode'] != run['mode']:
            raise Error('INVALID_MODE')
        _hash(body['manifest_sha'])
    elif action == 'approve':
        exact(body, ('plan_sha', 'source_manifest_sha', 'policy_version', 'approval_nonce', 'operation_ids'))
        _hash(body['plan_sha']); _hash(body['source_manifest_sha'])
        identifier(body['approval_nonce'])
    elif action == 'apply':
        exact(body, ('approval_id',)); identifier(body['approval_id'])
    elif action == 'undo':
        exact(body, ('batch_id', 'expected_after_sha'))
        identifier(body['batch_id']); _hash(body['expected_after_sha'])
    elif action in EMPTY_ACTIONS:
        exact(body, ())
    else:
        raise Error('UNKNOWN_ACTION', 404)


def _path(paths, key):
    value = paths[key]
    if not isinstance(value, str) or not Path(value).is_absolute() or '..' in Path(value).parts:
        raise Error('INVALID_WORKER_PATH', 500)
    return Path(value)


def _directory(path, create=False):
    if create:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for segment in Path(path).parts[1:]:
                created = False
                try:
                    os.mkdir(segment, 0o700, dir_fd=fd)
                    created = True
                except FileExistsError:
                    pass
                child = os.open(segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if created:
                        os.fsync(child); os.fsync(fd)
                except BaseException:
                    os.close(child)
                    raise
                os.close(fd); fd = child
        except BaseException:
            os.close(fd)
            raise
    else:
        fd = core.open_directory_chain(path)
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
        os.close(fd)
        raise Error('UNSAFE_COORDINATOR_DIRECTORY', 409)
    return fd


def _exists_at(fd, name):
    try:
        return os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _read_at(fd, name, limit):
    f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        before = os.fstat(f)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.getuid()
                or before.st_size > limit):
            raise Error('ERROR_EVIDENCE', 409)
        chunks = []; size = 0
        while size <= limit:
            part = os.read(f, min(65536, limit + 1 - size))
            if not part:
                break
            chunks.append(part); size += len(part)
        after = os.fstat(f)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_mode, s.st_nlink)
        if size != before.st_size or identity(before) != identity(after):
            raise Error('ERROR_EVIDENCE', 409)
        return b''.join(chunks)
    finally:
        os.close(f)


def _fresh(store, fixture, mode):
    """Validate completion, not merely Store.__init__'s directory existence."""
    expected_count = 1 if mode == 'B1' and fixture in live_provider.FIXTURES else 20 if mode == 'B0' and fixture == 'dev20' else FIXTURES.get(fixture)
    if expected_count is None:
        raise Error('CREATION_INCOMPLETE', 409)
    with store.locked() as (fd, db):
        entries = store.manifest(fd)
        homefd = _directory(store.home)
        try:
            baseline = parse(_read_at(homefd, 'baseline.json', 256 * 1024))
            marker = parse(_read_at(homefd, 'registered.json', 4096))
            if marker != {'workspace_id': store.id, 'synthetic': True} or baseline != entries:
                raise Error('CREATION_INCOMPLETE', 409)
            allowed = {'files', 'baseline.json', 'registered.json', 'journal.sqlite',
                       'journal.sqlite-wal', 'journal.sqlite-shm', 'lock'}
            if not set(os.listdir(homefd)) <= allowed:
                raise Error('CREATION_INCOMPLETE', 409)
        finally:
            os.close(homefd)
        if len(entries) != expected_count or any('/' in e['relative_path'] for e in entries):
            raise Error('CREATION_INCOMPLETE', 409)
        if mode == 'reference' and {e['relative_path'] for e in entries} != {
                f'document-{i:03}.txt' for i in range(expected_count)}:
            raise Error('CREATION_INCOMPLETE', 409)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != {'batch', 'op'}:
            raise Error('CREATION_INCOMPLETE', 409)
        schemas = {'batch': ('id', 'mode', 'state', 'plan', 'approval', 'before_sha', 'after_sha'),
                   'op': ('id', 'batch', 'seq', 'direction', 'from_path', 'to_path', 'expected', 'state', 'parent_forward_op_id')}
        for table, names in schemas.items():
            columns = db.execute('PRAGMA table_info(' + table + ')').fetchall()
            expected = [(i, n, 'INTEGER' if n == 'seq' else 'TEXT', 0, None, 1 if n == 'id' else 0)
                        for i, n in enumerate(names)]
            if [tuple(c) for c in columns] != expected or db.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone():
                raise Error('CREATION_INCOMPLETE', 409)
        if {n for n in os.listdir(fd) if stat.S_ISDIR(os.stat(n, dir_fd=fd, follow_symlinks=False).st_mode)} != set(core.FOLDERS):
            raise Error('CREATION_INCOMPLETE', 409)


def _sync_tree(fd, depth=0, budget=None):
    """Flush bounded owned creation data; parent fsync is not recursive."""
    if budget is None:
        budget = [140]
    if depth > 4:
        raise Error('CREATION_INCOMPLETE', 409)
    for name in os.listdir(fd):
        core.valid(name)
        budget[0] -= 1
        if budget[0] < 0:
            raise Error('CREATION_INCOMPLETE', 409)
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if info.st_uid != os.getuid():
            raise Error('CREATION_INCOMPLETE', 409)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                _sync_tree(child, depth + 1, budget)
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                actual = os.fstat(f)
                if (actual.st_dev, actual.st_ino, actual.st_nlink) != (info.st_dev, info.st_ino, 1):
                    raise Error('CREATION_INCOMPLETE', 409)
                os.fsync(f)
            finally:
                os.close(f)
        else:
            raise Error('CREATION_INCOMPLETE', 409)
    os.fsync(fd)


def _creation_context(body):
    return {'mode': body['mode'], 'fixture_id': body['fixture_id'], 'config_sha': body['config_sha'],
            'execution_policy_version': core.POLICY,
            'classification': classification_context() if body['mode'] in ('B0','B1') else None}


def _create(job, paths, run, record):
    body, rid = job['body'], job['run_id']
    canonical = _path(paths, 'workspace_base')
    staging = _path(paths, 'staging_base')
    if run.get('workspace_id') or job.get('intent'):
        raise Error('CREATION_INTERRUPTED', 409)
    context = _creation_context(body)
    record({'action': job['action'], 'run_id': rid, 'stage': 'CREATING',
            'request_body': body, 'creation_context': context})
    stagingfd = _directory(staging, create=True)
    try:
        # Existence means an earlier attempt, even if apparently empty. Never retry.
        try:
            os.mkdir(rid, 0o700, dir_fd=stagingfd)
        except FileExistsError:
            raise Error('CREATION_INTERRUPTED', 409) from None
        os.fsync(stagingfd)
    finally:
        os.close(stagingfd)
    core.BASE = staging / rid
    try:
        store = live_provider.create(body['fixture_id']) if body['mode'] == 'B1' else core.Store.create_registered_dev() if body['mode'] == 'B0' else core.Store.create(FIXTURES[body['fixture_id']])
        # This durable record precedes every publication attempt.
        record({'workspace_id': store.id, 'stage': 'VALIDATING_CREATION'})
        _fresh(store, body['fixture_id'], body['mode'])
        homefd = _directory(store.home)
        try:
            _sync_tree(homefd)
        finally:
            os.close(homefd)
        sourcefd = _directory(staging / rid)
        destfd = _directory(canonical, create=True)
        try:
            if os.fstat(sourcefd).st_dev != os.fstat(destfd).st_dev:
                raise Error('NO_GO_CROSS_DEVICE', 409)
            record({'stage': 'PUBLISHING'})
            core.noreplace(sourcefd, store.id, destfd, store.id)
            os.fsync(sourcefd); os.fsync(destfd)
        finally:
            os.close(sourcefd); os.close(destfd)
        record({'stage': 'PUBLISHED'})
        workspace_id = store.id
    finally:
        core.BASE = canonical
    store = core.Store(workspace_id)
    run.update(workspace_id=workspace_id, fixture=body['fixture_id'], mode=body['mode'],
               batch=None, creation_context=context, preview_context=None)
    return store, {'run_id': rid, 'state': 'SCANNED', 'provider': 'LIVE_BROKER' if body['mode'] == 'B1' else 'CLOSED', 'poll_url': '/runs/' + rid}


def _classification_check(run, preview=False):
    context = run.get('preview_context' if preview else 'creation_context')
    if run['mode'] in ('B0','B1') and (not isinstance(context, dict)
            or context.get('classification') != classification_context()
            or context.get('config_sha') != config_sha(run['mode'])):
        raise Error('CLASSIFICATION_POLICY_CHANGED', 409)
    return context


def _proposals(scan, mode):
    if mode == 'B0':
        return [classifier.baseline({'file_id': e['file_id'], 'filename': Path(e['relative_path']).name,
                                     'snippet': e['snippet']}) for e in scan['files'] if e['status'] == 'READY']
    return [{'file_id': e['file_id'], 'destination_folder': core.FOLDERS[i % 4],
             'new_basename': Path(e['relative_path']).name,
             'reason': 'Deterministic reference cycle by sorted file order; no semantic classification or AI inference.',
             'confidence': 0.0} for i, e in enumerate(scan['files']) if e['status'] == 'READY']


def _preview(store, run, body, record, job=None):
    _classification_check(run)
    scan = store.scan()
    if body['manifest_sha'] != scan['manifest_sha'] or body['policy_version'] != core.POLICY:
        raise Error('REAPPROVAL_REQUIRED', 409)
    evidence = None
    if run['mode'] == 'B1':
        if (run.get('last_intent') or {}).get('provider_started'):
            raise Error('PROVIDER_ALREADY_ATTEMPTED',409)
        ready = [e for e in scan['files'] if e['status']=='READY']
        if len(ready)!=1: raise Error('INVALID_LIVE_FIXTURE',409)
        e=ready[0]
        proposals,evidence = live_provider.propose({'file_id':e['file_id'],'filename':Path(e['relative_path']).name,'snippet':e['snippet']},job.get('live_config'),job.get('deadline_monotonic_ns'),job['run_id'],record)
    else:
        proposals = _proposals(scan, run['mode'])
    with store.locked() as (fd, db):
        if db.execute("SELECT 1 FROM batch WHERE state NOT IN ('UNDONE','REJECTED')").fetchone():
            raise Error('ACTIVE_BATCH', 409)
        prior = [r[0] for r in db.execute('SELECT id FROM batch ORDER BY id LIMIT 10001')]
        if len(prior) > 10000:
            raise Error('JOURNAL_LIMIT', 409)
    context = run.get('creation_context')
    if not isinstance(context, dict):
        raise Error('ERROR_EVIDENCE', 409)
    record({'action': 'preview', 'stage': 'PREVIEW_STARTED', 'workspace_id': store.id,
            'request_body': body, 'prior_batch_ids': prior, 'proposals': proposals,
            'creation_context': context})
    try:
        result = store.preview(proposals, body['manifest_sha'], body['policy_version'])
    except core.Refusal:
        record({'stage': 'PREVIEW_REFUSED'})
        raise
    snapshot = dict(context, batch_id=result['batch_id'], plan_sha=result['plan_sha'])
    if evidence is not None: snapshot['provider_evidence']=evidence
    record({'stage': 'PREVIEW_COMMITTED', 'batch': result['batch_id'], 'preview_context': snapshot})
    run.update(batch=result['batch_id'], preview_context=snapshot)
    return result


def _repair_creation(run, rid, paths, intent):
    if intent.get('action') not in ('create', 'create-preserving') or intent.get('run_id') != rid:
        raise Error('CREATION_INTERRUPTED', 409)
    wid = identifier(intent.get('workspace_id'))
    if intent.get('stage') not in ('PUBLISHING', 'PUBLISHED'):
        raise Error('CREATION_INTERRUPTED', 409)
    body = intent.get('request_body')
    validate_request('create', None, body)
    context = intent.get('creation_context')
    if context != _creation_context(body):
        raise Error('CREATION_INTERRUPTED', 409)
    canonicalfd = _directory(_path(paths, 'workspace_base'))
    stagefd = _directory(_path(paths, 'staging_base') / rid)
    try:
        if _exists_at(canonicalfd, wid) is None or os.listdir(stagefd):
            raise Error('CREATION_INTERRUPTED', 409)
    finally:
        os.close(canonicalfd); os.close(stagefd)
    store = core.Store(wid)
    _fresh(store, body['fixture_id'], body['mode'])
    run.update(workspace_id=wid, fixture=body['fixture_id'], mode=body['mode'], batch=None,
               creation_context=context, preview_context=None)
    return store


def _repair_preview(store, run, intent, live_config=None):
    if intent.get('workspace_id') != store.id or intent.get('action') != 'preview':
        raise Error('PREVIEW_INTERRUPTED', 409)
    prior = intent.get('prior_batch_ids')
    body, proposals = intent.get('request_body'), intent.get('proposals')
    if not isinstance(prior, list) or len(prior) > 10000 or len(set(prior)) != len(prior):
        raise Error('PREVIEW_INTERRUPTED', 409)
    for bid in prior:
        identifier(bid)
    validate_request('preview', run, body, live_config)
    if not isinstance(proposals, list) or intent.get('creation_context') != run.get('creation_context'):
        raise Error('PREVIEW_INTERRUPTED', 409)
    with store.locked() as (fd, db):
        current = [r[0] for r in db.execute('SELECT id FROM batch ORDER BY id LIMIT 10001')]
        if len(current) > 10000:
            raise Error('PREVIEW_INTERRUPTED', 409)
        candidates = [bid for bid in current if bid not in prior]
        if not candidates and set(prior) == set(current) and intent.get('stage') == 'PREVIEW_REFUSED':
            return
        if not set(prior) <= set(current) or len(candidates) != 1:
            raise Error('PREVIEW_INTERRUPTED', 409)
        bid = candidates[0]
        if intent.get('batch') not in (None, bid):
            raise Error('PREVIEW_INTERRUPTED', 409)
        row = store.batch(db, bid)
        plan = parse(row['plan'])
        if (plan['source_manifest_sha'] != body['manifest_sha'] or plan['policy_version'] != body['policy_version']
                or [o['proposal'] for o in plan['operations']] != proposals):
            raise Error('PREVIEW_INTERRUPTED', 409)
    context = dict(run['creation_context'], batch_id=bid, plan_sha=plan['plan_sha'])
    if run['mode']=='B1': context['provider_evidence']=intent.get('provider_evidence')
    journey_context({'creation': run['creation_context'], 'preview': context}, bid, plan['plan_sha'])
    run.update(batch=bid, preview_context=context)


def _snapshot(store, run):
    scan = store.scan()
    with store.locked() as (fd, db):
        if core.digest(store.manifest(fd)) != scan['manifest_sha']:
            raise Error('SNAPSHOT_CHANGED', 409)
        result = store.result(db, run['batch'], fd) if run.get('batch') else {'state': 'SCANNED', 'operations': []}
    return {'latest_result': result, 'latest_scan': scan, 'state': result['state'],
            'snapshot_observed_at': time.time(), 'blocked': False}


def _conflict_fixture(store, batch):
    with store.locked() as (fd, journal):
        row = store.batch(journal, batch)
        if row['state'] != 'APPLIED':
            raise Error('APPLIED_REQUIRED', 409)
        path = parse(row['plan'])['operations'][0]['src']
        with core.parent(fd, path) as (parentfd, name):
            try:
                f = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parentfd)
            except FileExistsError:
                raise Error('CONFLICT_ALREADY_PRESENT', 409) from None
            with os.fdopen(f, 'wb') as stream:
                stream.write(b'Synthetic conflict fixture. Preserve this new file and the moved document.\n')
                stream.flush(); os.fsync(stream.fileno())
            os.fsync(parentfd)
    return {'state': 'CONFLICT_FIXTURE_READY', 'path': path,
            'next_action': 'Request Undo to observe refusal and preservation.'}


def _archive(raw, store, batch, context):
    verify(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        meta = parse(archive.read('index.json'))
        journal = parse(archive.read('journal.json'))
        current = parse(archive.read('current.json'))
        recorded_context = parse(archive.read('journey-context.json'))
    if (meta['workspace_id'] != store.id or meta['batch_id'] != batch
            or journal['state'] not in TERMINAL_EXPORT or recorded_context != context):
        raise Error('ERROR_EVIDENCE', 409)
    return journal, core.digest(current)


def _export(store, run, rid, paths, record):
    batch = run.get('batch')
    if not batch:
        raise Error('PREVIEW_REQUIRED', 409)
    context = {'creation': run.get('creation_context'), 'preview': run.get('preview_context')}
    if store.status(batch)['state'] not in TERMINAL_EXPORT:
        raise Error('EXPORT_NOT_READY', 409)
    raw = bundle(store, batch, context=context)
    if run['mode']=='B1' and len(raw)>1024*1024: raise Error('LIVE_EXPORT_LIMIT',409)
    journal, manifest_sha = _archive(raw, store, batch, context)
    snapshot = _snapshot(store, run)
    if snapshot['latest_result'] != journal or snapshot['latest_scan']['manifest_sha'] != manifest_sha:
        raise Error('SNAPSHOT_CHANGED', 409)
    revision = core.digest({'workspace_id': store.id, 'batch_id': batch, 'journal': journal,
                            'manifest_sha': manifest_sha, 'context': context})
    root = _path(paths, 'export_base')
    rootfd = _directory(root, create=True)
    try:
        try:
            os.mkdir(rid, 0o700, dir_fd=rootfd); os.fsync(rootfd)
        except FileExistsError:
            pass
        runfd = os.open(rid, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rootfd)
    finally:
        os.close(rootfd)
    name = revision + '.zip'
    try:
        info = os.fstat(runfd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise Error('ERROR_EVIDENCE', 409)
        if _exists_at(runfd, name) is not None:
            existing = _read_at(runfd, name, LIMIT)
            old_journal, old_sha = _archive(existing, store, batch, context)
            if old_journal != journal or old_sha != manifest_sha:
                raise Error('ERROR_EVIDENCE', 409)
            raw = existing
        else:
            record({'action': 'export', 'stage': 'EXPORT_PUBLISHING', 'revision': revision,
                    'path': rid + '/' + name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
            pending = revision + '.' + str(uuid.uuid4()) + '.pending'
            f = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=runfd)
            with os.fdopen(f, 'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            core.noreplace(runfd, pending, runfd, name); os.fsync(runfd)
        descriptor = {'revision': revision, 'path': rid + '/' + name,
                      'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        record({'action': 'export', 'stage': 'EXPORT_PUBLISHED', **descriptor})
        return descriptor, snapshot
    finally:
        os.close(runfd)


def execute(job, paths, intent_writer):
    """Return JSON only. Store side effects never share the control-DB transaction."""
    run = dict(job.get('run') or {})
    patch = {}
    intent = dict(job.get('intent') or {})
    store = None
    action = job.get('action')

    def record(update):
        payload = dict(update, job_id=job['id'], run_id=job['run_id'])
        intent_writer(payload)
        intent.update(payload)
        patch['last_intent'] = dict(intent)

    try:
        identifier(job['id']); rid = identifier(job['run_id'])
        validate_request(action, job.get('run'), job.get('body'), job.get('live_config'))
        core.BASE = _path(paths, 'workspace_base')
        if action in MUTATIONS:
            patch['export_current'] = None
        if run.get('blocked') and action not in ('refresh', 'scan', 'recover', 'cancel'):
            raise Error('RUN_BLOCKED', 409)
        if action in ('create', 'create-preserving'):
            store, result = _create(job, paths, run, record)
        elif not run.get('workspace_id') and (action == 'cancel'
                or action in ('refresh', 'scan') and run.get('state') == 'CANCELLED'):
            if action == 'cancel':
                record({'action': action, 'stage': 'CANCELLED_BEFORE_WORKSPACE'})
            result = {'state': 'CANCELLED', 'operations': []}
            patch.update(state='CANCELLED', latest_result=result, latest_scan=None,
                         blocked=False, snapshot_observed_at=time.time())
            return {'http_status': 200, 'result': result, 'run_patch': patch}
        else:
            prior_intent = run.get('last_intent') or {}
            if action == 'refresh' and not run.get('workspace_id'):
                store = _repair_creation(run, rid, paths, prior_intent)
            elif not run.get('workspace_id'):
                raise Error('WORKSPACE_NOT_READY', 409)
            else:
                store = core.Store(identifier(run['workspace_id']))
            if action == 'cancel' and not run.get('batch'):
                if prior_intent.get('action') == 'preview':
                    with store.locked() as (fd,db):
                        current={r[0] for r in db.execute('SELECT id FROM batch')}
                    prior=set(prior_intent.get('prior_batch_ids') or [])
                    if current-prior:_repair_preview(store,run,prior_intent,job.get('live_config'))
                    elif current!=prior:raise Error('PREVIEW_INTERRUPTED',409)
                if not run.get('batch'):
                    # No approved journal batch exists: cancellation has no file
                    # movement to reverse. Retain workspace and any orphan intent.
                    snapshot=_snapshot(store,run)
                    result={'state':'CANCELLED','operations':[]}
                    record({'action':'cancel','stage':'CANCELLED_BEFORE_BATCH','workspace_id':store.id})
                    patch.update(snapshot,state='CANCELLED',latest_result=result,blocked=False,
                                 workspace_id=store.id,batch=None,export_current=None)
                    return {'http_status':200,'result':result,'run_patch':patch}
            if action == 'refresh':
                if prior_intent.get('action') == 'preview':
                    _repair_preview(store, run, prior_intent, job.get('live_config'))
                result = None
            elif action == 'scan':
                result = None
            elif action == 'preview':
                result = _preview(store, run, job['body'], record, job)
            elif action == 'export':
                descriptor, snapshot = _export(store, run, rid, paths, record)
                patch.update(snapshot, export_record=descriptor, export_current=descriptor)
                result = {'run_id': rid, 'state': snapshot['state'], 'export_record': descriptor,
                          'download_url': '/runs/' + rid + '/export'}
            else:
                batch = run.get('batch')
                if not batch:
                    raise Error('PREVIEW_REQUIRED', 409)
                identifier(batch)
                body = job['body']
                record({'action': action, 'stage': 'ACTION_STARTED', 'workspace_id': store.id,
                        'batch': batch, 'request_body': body})
                if action == 'approve':
                    result = store.approve(batch, body)
                elif action == 'apply':
                    context = _classification_check(run, preview=True)
                    if run['mode'] in ('B0','B1') and (context['batch_id'] != batch
                            or context['plan_sha'] != parse(store.status(batch)['plan'])['plan_sha']):
                        raise Error('CLASSIFICATION_POLICY_CHANGED', 409)
                    result = store.apply(batch, body['approval_id'])
                elif action == 'resume':
                    state = store.status(batch)
                    if state['mode'] == 'UNDO' or state['state'] == 'PARTIALLY_APPLIED':
                        result = store.undo(batch, cancel=True)
                    else:
                        # Explicit Resume continues only the durable direction.
                        # Store rechecks the exact recorded approval and projection.
                        approval=parse(state['approval']) if state.get('approval') else None
                        if not approval:raise Error('EXACT_APPLY_REQUIRED',409)
                        _classification_check(run,preview=True)
                        result=store.apply(batch,approval['approval_id'])
                elif action == 'undo':
                    if body['batch_id'] != batch:
                        raise Error('REAPPROVAL_REQUIRED', 409)
                    result = store.undo(batch, body['expected_after_sha'])
                elif action == 'cancel':
                    result = store.undo(batch, cancel=True)
                elif action == 'recover':
                    result = store.recover(batch)
                elif action == 'conflict-fixture':
                    result = _conflict_fixture(store, batch)
                else:
                    raise Error('UNKNOWN_ACTION', 404)
                record({'stage': 'ACTION_FINISHED'})
        patch.update({key: run.get(key) for key in ('workspace_id', 'batch', 'fixture', 'mode',
                                                   'creation_context', 'preview_context')})
        if action != 'export':
            snapshot = _snapshot(store, run)
            if (action in ('scan', 'refresh') and (snapshot['latest_result'] != run.get('latest_result')
                    or snapshot['latest_scan'] != run.get('latest_scan'))):
                patch['export_current'] = None
            patch.update(snapshot)
        if (action in ('apply', 'undo', 'cancel', 'resume', 'recover', 'refresh', 'scan')
                and patch['state'] in TERMINAL_EXPORT):
            # Export belongs to this bounded machine action, never a download GET.
            # Keep the observed journal if evidence publication fails after moves.
            try:
                descriptor, exported_snapshot = _export(store, run, rid, paths, record)
                patch.update(exported_snapshot, export_record=descriptor, export_current=descriptor)
            except Exception:
                raise Error('ERROR_EVIDENCE', 409) from None
        if action == 'scan':
            result = patch['latest_scan']
        elif action == 'refresh':
            result = patch['latest_result']
        return {'http_status': 201 if action in ('create', 'create-preserving') else 200,
                'result': result, 'run_patch': patch}
    except Error as exc:
        status, code = exc.status, exc.code
    except core.Refusal as exc:
        status = 409
        value = str(exc)
        code = value if re.fullmatch('[A-Z][A-Z0-9_]*(?::[A-Za-z][A-Za-z0-9_]*)?', value) else 'ERROR_EVIDENCE'
    except Exception:
        status, code = 500, 'INTERNAL_ERROR'
    # Unknown acknowledgement never becomes an automatic mutation retry.
    patch.update(blocked=True, export_current=None)
    if store is not None and run.get('workspace_id'):
        try:
            observed = _snapshot(store, run)
            observed['blocked'] = True
            patch.update(observed)
        except Exception:
            pass
    return {'http_status': status, 'result': failure(code), 'run_patch': patch}
