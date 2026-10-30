"""Bounded evidence archive verification; never extracts or executes archive content."""
import argparse
import hashlib
import io
import json
import os
import re
import stat
import sys
import zipfile
from .core import Refusal, canonical, digest, parent, valid, uid

LIMIT = 8 * 1024 * 1024

def parse(raw):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out: raise ValueError('duplicate JSON key')
            out[k] = v
        return out
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))

def journey_context(value, batch=None, plan_sha=None):
    if not isinstance(value, dict) or set(value) != {'creation','preview'}: raise ValueError('journey context schema')
    creation, preview = value['creation'], value['preview']
    keys = {'mode','fixture_id','config_sha','execution_policy_version','classification'}
    if not isinstance(creation, dict) or set(creation) != keys or not isinstance(preview, dict) or set(preview) != keys | {'batch_id','plan_sha'} | ({'provider_evidence'} if creation.get('mode')=='B1' else set()): raise ValueError('journey context schema')
    if {k:preview[k] for k in keys} != creation: raise ValueError('journey snapshot mismatch')
    if creation['mode'] not in ('B0','B1','reference') or creation['execution_policy_version'] != 'synthetic-v1': raise ValueError('journey context mode')
    if creation['fixture_id'] not in (('live-purchase','live-injection') if creation['mode']=='B1' else ('dev20',) if creation['mode'] == 'B0' else ('synthetic-4','synthetic-12')): raise ValueError('journey fixture')
    hashes = [creation['config_sha'], preview['plan_sha']]
    classification = creation['classification']
    if creation['mode'] in ('B0','B1'):
        if not isinstance(classification, dict) or set(classification) != {'policy_sha','declared_policy_sha','classifier_source_sha','policy_source_sha'}: raise ValueError('classification context')
        hashes.extend(classification.values())
        if classification['policy_sha'] != classification['declared_policy_sha']: raise ValueError('classification policy')
    elif classification is not None: raise ValueError('reference context')
    if any(not isinstance(h, str) or not re.fullmatch('[0-9a-f]{64}',h) for h in hashes): raise ValueError('context hash')
    uid(preview['batch_id'])
    if batch is not None and (preview['batch_id'] != batch or preview['plan_sha'] != plan_sha): raise ValueError('context batch')
    if creation['mode']=='B1':
        from .live_provider import validate_evidence
        validate_evidence(preview['provider_evidence'])
    return value

def bundle(store, batch, context=None):
    with store.locked() as (fd, db):
        store.terminal_check(fd, db, batch)
        journal = store.result(db, batch, fd)
        before = parse((store.home / (batch + '-before.json')).read_bytes())
        current = store.manifest(fd)
        payload = {'before.json': canonical(before).encode(), 'current.json': canonical(current).encode(),
                   'journal.json': canonical(journal).encode()}
        if journal['state'] in ('APPLIED','UNDONE'):
            name = batch + ('-after.json' if journal['state'] == 'APPLIED' else '-undone.json')
            payload['recorded-terminal.json'] = canonical(parse((store.home / name).read_bytes())).encode()
        if context is not None:
            journey_context(context, batch, parse(journal['plan'])['plan_sha'])
            payload['journey-context.json'] = canonical(context).encode()
        for entry in current:
            with parent(fd, entry['relative_path']) as (p, n):
                f = os.open(n, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=p)
                try:
                    s = os.fstat(f)
                    if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1: raise Refusal('ERROR_EVIDENCE')
                    raw = os.read(f, 65537)
                finally: os.close(f)
            if len(raw) != entry['size_bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']: raise Refusal('ERROR_EVIDENCE')
            payload['content/' + entry['relative_path']] = raw
        meta = {'format': 'folderundo-evidence-v1', 'workspace_id': store.id, 'batch_id': batch,
                'recorded': True, 'provider': 'LIVE_RECORDED' if context and context['creation']['mode']=='B1' else 'CLOSED', 'history_authenticated': False,
                'members': {name: {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)} for name, raw in payload.items()}}
        payload['index.json'] = canonical(meta).encode()
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            for name, raw in payload.items(): z.writestr(name, raw)
        raw = out.getvalue()
        verify(raw)
        return raw

def _verify_domain(raw):
    """Independent structural/content/mapping checks, not a signature or semantic oracle."""
    try:
        if len(raw) > LIMIT: raise ValueError('archive limit')
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos = z.infolist(); names = [i.filename for i in infos]
            if len(names) != len(set(names)) or len(names) > 106: raise ValueError('member multiplicity')
            if sum(i.file_size for i in infos) > LIMIT: raise ValueError('expanded limit')
            for i in infos:
                if i.flag_bits & 1 or i.is_dir() or stat.S_ISLNK(i.external_attr >> 16): raise ValueError('unsafe member')
                if i.filename not in ('index.json', 'before.json', 'current.json', 'journal.json', 'journey-context.json', 'recorded-terminal.json'):
                    if not i.filename.startswith('content/'): raise ValueError('unknown member')
                    valid(i.filename[8:])
            data = {n: z.read(n) for n in names}
        meta = parse(data.pop('index.json'))
        uid(meta['workspace_id']); uid(meta['batch_id'])
        if meta['format'] != 'folderundo-evidence-v1' or meta['history_authenticated'] is not False or meta['provider'] not in ('CLOSED','LIVE_RECORDED'): raise ValueError('format')
        if set(data) != set(meta['members']): raise ValueError('member mapping')
        for n, content in data.items():
            if meta['members'][n] != {'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content)}: raise ValueError('member hash')
        before, current, journal = (parse(data[n]) for n in ('before.json', 'current.json', 'journal.json'))
        def mapping(entries):
            if not isinstance(entries, list) or not 1 <= len(entries) <= 100: raise ValueError('file count')
            paths = {}; ids = set()
            for e in entries:
                if set(e) != {'relative_path','file_id','sha256','size_bytes','device','inode','mode','mtime_ns','link_count'}: raise ValueError('manifest schema')
                uid(e['file_id'])
                if any(type(e[k]) is not int for k in ('device','inode','mode','mtime_ns','link_count')) or e['link_count'] != 1: raise ValueError('file identity')
                path = valid(e['relative_path'])
                if path.casefold() in paths or e['file_id'] in ids: raise ValueError('file multiplicity')
                if not re.fullmatch('[0-9a-f]{64}', e['sha256']) or type(e['size_bytes']) is not int or not 0 <= e['size_bytes'] <= 65536: raise ValueError('file bounds')
                ids.add(e['file_id']); paths[path.casefold()] = e
            return {e['file_id']: e for e in entries}
        old, now = mapping(before), mapping(current)
        if set(data) != ({'before.json', 'current.json', 'journal.json'} | (set(data) & {'journey-context.json','recorded-terminal.json'})) | {'content/' + e['relative_path'] for e in current}: raise ValueError('content mapping')
        for e in current:
            content = data['content/' + e['relative_path']]
            if len(content) != e['size_bytes'] or hashlib.sha256(content).hexdigest() != e['sha256']: raise ValueError('content hash')
        plan = parse(journal['plan']); unsigned = dict(plan); claimed = unsigned.pop('plan_sha')
        if digest(unsigned) != claimed or digest(before) != journal['before_sha'] or plan['source_manifest_sha'] != journal['before_sha']: raise ValueError('plan hashes')
        if journal['id'] != meta['batch_id'] or plan['batch_id'] != journal['id']: raise ValueError('batch mapping')
        if journal['state'] not in ('AWAITING_APPROVAL','APPROVED','APPLYING','APPLIED','PARTIALLY_APPLIED','UNDO_REQUESTED','UNDOING','UNDONE','CONFLICT','RECOVERY_REQUIRED','REJECTED') or journal['mode'] not in ('FORWARD','UNDO'): raise ValueError('journal state')
        context = journey_context(parse(data['journey-context.json']), journal['id'], claimed) if 'journey-context.json' in data else None
        if (meta['provider']=='LIVE_RECORDED') != bool(context and context['creation']['mode']=='B1'): raise ValueError('provider context')
        if context and context['creation']['mode']=='B1':
            from .classifier import validate
            evidence=context['preview']['provider_evidence']
            proposals=validate(evidence['response']['provider']['choices'][0]['message']['content'].encode(),list(old))
            if len(proposals)!=len(plan['operations']): raise ValueError('provider plan count')
            for proposal,operation in zip(proposals,plan['operations']):
                if proposal!=operation['proposal'] or proposal['file_id']!=operation['expected']['file_id'] or operation['dst']!=proposal['destination_folder']+'/'+proposal['new_basename']: raise ValueError('provider file binding')
        ops = journal['operations']; forward = sorted((o for o in ops if o['direction'] == 'FORWARD'), key=lambda o: o['seq'])
        if len({o['id'] for o in ops}) != len(ops) or len(ops) > 200 or any(o['direction'] not in ('FORWARD','UNDO') for o in ops): raise ValueError('operation multiplicity')
        if len(forward) != len(plan['operations']): raise ValueError('plan count')
        for operation in ops:
            uid(operation['id'])
            if operation['state'] not in ('PENDING','PREPARED','UNDO_PREPARED','COMMITTED','UNDONE','CANCELLED','CONFLICT'): raise ValueError('operation state')
            if operation['batch'] != meta['batch_id']: raise ValueError('operation batch')
        positions = {k: dict(v) for k, v in old.items()}; forwards = {}
        for seq, (o, p) in enumerate(zip(forward, plan['operations'])):
            expected = parse(o['expected'])
            if (o['id'], o['seq'], o['from_path'], o['to_path'], expected) != (p['op_id'], seq, p['src'], p['dst'], p['expected']): raise ValueError('operation mapping')
            if expected != old.get(expected['file_id']): raise ValueError('baseline identity')
            valid(o['from_path']); valid(o['to_path']); forwards[o['id']] = o
            if o['state'] == 'COMMITTED': positions[expected['file_id']]['relative_path'] = o['to_path']
        reverse = [o for o in ops if o['direction'] == 'UNDO']; parents = set()
        for o in reverse:
            p = forwards[o['parent_forward_op_id']]
            if o['parent_forward_op_id'] in parents or (o['from_path'], o['to_path'], o['expected']) != (p['to_path'], p['from_path'], p['expected']): raise ValueError('reverse mapping')
            parents.add(o['parent_forward_op_id'])
            if o['state'] == 'UNDONE': positions[parse(o['expected'])['file_id']]['relative_path'] = o['to_path']
        approval = parse(journal['approval']) if journal['approval'] else None
        if approval:
            wanted = {k: plan[k] for k in ('plan_sha','source_manifest_sha','policy_version','approval_nonce')}
            wanted['operation_ids'] = [o['op_id'] for o in plan['operations']]
            if approval['body'] != wanted: raise ValueError('approval mapping')
        drift = False
        if 'conflicts' in journal:
            expected_paths = {e['relative_path']:e for e in positions.values()}
            current_paths = {e['relative_path']:e for e in current}
            expected_conflicts = [{'path':p,'expected':expected_paths.get(p),'current':current_paths.get(p),'reason':'CURRENT_PATH_DIFFERS'} for p in sorted(expected_paths.keys() | current_paths.keys()) if expected_paths.get(p) != current_paths.get(p)]
            if journal['conflicts'] != expected_conflicts: raise ValueError('conflict evidence')
        if journal['state'] in ('APPLIED','UNDONE'):
            recorded = parse(data['recorded-terminal.json']) if 'recorded-terminal.json' in data else current
            recorded_map = mapping(recorded)
            drift = digest(current) != journal['after_sha']
            if 'current_drift' in journal and journal['current_drift'] != {'detected':drift,'expected_sha':journal['after_sha'],'manifest_sha':digest(current)}: raise ValueError('drift evidence')
            if (not approval and any(o['state'] == 'COMMITTED' for o in forward)) or digest(recorded) != journal['after_sha'] or positions != recorded_map: raise ValueError('terminal mapping')
            if journal['state'] == 'APPLIED' and not approval: raise ValueError('missing approval')
            if journal['state'] == 'UNDONE' and old != recorded_map: raise ValueError('undo mapping')
        return {'verified': True, 'recorded': True, 'current_drift': drift, 'current_exact_restoration': journal['state'] == 'UNDONE' and not drift, 'state': journal['state'], 'files': len(current),
                'journey_context': context, 'classification_context_present': context is not None and context['creation']['mode'] in ('B0','B1'),
                'history_authenticated': False, 'note': 'Archive internal consistency only; no new inference or filesystem changes.'}
    except (KeyError, TypeError, ValueError, OSError, zipfile.BadZipFile, UnicodeError, RuntimeError, RecursionError, Refusal) as e:
        raise Refusal('ERROR_EVIDENCE:' + str(e)) from e

def verify(raw):
    """Route one bounded coordinator envelope or the unchanged legacy verifier."""
    try:
        if not isinstance(raw, bytes) or len(raw) > LIMIT: raise ValueError('archive limit')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            indexes = [item for item in archive.infolist() if item.filename == 'index.json']
            if len(indexes) != 1 or indexes[0].file_size > 65536: raise ValueError('index limit')
            meta = parse(archive.read(indexes[0]))
        if isinstance(meta, dict) and meta.get('format') == 'folderundo-coordinator-evidence-v1':
            from .coordinator_evidence import verify_evidence
            return verify_evidence(raw, _verify_domain)
    except (KeyError, TypeError, ValueError, OSError, zipfile.BadZipFile, UnicodeError, RuntimeError, RecursionError, Refusal) as error:
        raise Refusal('ERROR_EVIDENCE:' + str(error)) from error
    return _verify_domain(raw)


def main():
    p = argparse.ArgumentParser(description='Verify recorded evidence without extracting or moving files')
    p.add_argument('--bundle', required=True); p.add_argument('--offline', action='store_true', required=True)
    a = p.parse_args()
    try:
        with open(a.bundle, 'rb') as f: result = verify(f.read(LIMIT + 1))
        print(json.dumps(result)); return 0
    except (Refusal, OSError) as e: print(json.dumps({'verified': False, 'error': str(e)})); return 2
if __name__ == '__main__': sys.exit(main())
