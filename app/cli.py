"""Registered synthetic fixtures and bounded local command adapters; provider CLOSED."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from .core import Store, Refusal, valid, open_directory_chain, parent

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_MANIFEST = 'fixtures/manifest.json'
PUBLIC_MANIFEST_SHA = '9dae618d1fda6c125f595feb660b49b9ea905170309ae8fc50d017c09a57322f'
RECOVERY_FIXTURES = {'early': ('fixtures/early4', 4), 'full': ('fixtures/stress100', 100)}


def read_local(relative, limit):
    """Read only caller-allowlisted project paths, with no symlink ancestors."""
    valid(relative)
    rootfd = open_directory_chain(ROOT)
    try:
        with parent(rootfd, relative) as (directory, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                before = os.fstat(fd)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                    raise Refusal('UNSAFE_LOCAL_INPUT')
                raw = bytearray()
                while len(raw) <= limit:
                    chunk = os.read(fd, min(65536, limit + 1 - len(raw)))
                    if not chunk: break
                    raw.extend(chunk)
                after = os.fstat(fd)
                if len(raw) != before.st_size or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    raise Refusal('CHANGED_LOCAL_INPUT')
                return bytes(raw)
            finally: os.close(fd)
    finally: os.close(rootfd)


def public_manifest():
    from .export import parse
    raw = read_local(PUBLIC_MANIFEST, 65536)
    if hashlib.sha256(raw).hexdigest() != PUBLIC_MANIFEST_SHA: raise Refusal('PUBLIC_MANIFEST_CHANGED')
    result = parse(raw)
    if result['format'] != 'folderundo-public-fixtures-v1' or result['scope'] != 'public-development-only':
        raise Refusal('PUBLIC_MANIFEST_SCHEMA')
    return result


def verify_fixtures(manifest):
    if manifest != PUBLIC_MANIFEST: raise Refusal('UNREGISTERED_MANIFEST')
    data = public_manifest()
    for source in data['sources']:
        raw = read_local(source['path'], 65536)
        if hashlib.sha256(raw).hexdigest() != source['sha256']: raise Refusal('SOURCE_MANIFEST_CHANGED')
    paths = set(); families = {}; size = 0
    for item in data['files']:
        path = item['path']
        if not path.startswith(('fixtures/dev/', 'fixtures/classification/dev/')) or item['split'] != 'dev':
            raise Refusal('UNREGISTERED_FIXTURE')
        if path.casefold() in paths or Path(path).suffix not in ('.txt', '.md'): raise Refusal('FIXTURE_PATH')
        paths.add(path.casefold())
        raw = read_local(path, 65536)
        if len(raw) != item['size_bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']: raise Refusal('FIXTURE_DIGEST')
        if '\x00' in raw.decode('utf-8'): raise Refusal('FIXTURE_ENCODING')
        size += len(raw); families[item['family']] = families.get(item['family'], 0) + 1
    if families != {'generic-development': 20, 'classification-development': 20}: raise Refusal('FIXTURE_COUNT')
    return {'verified': True, 'scope': data['scope'], 'files': len(paths), 'bytes': size,
            'families': families, 'manifest_sha256': PUBLIC_MANIFEST_SHA, 'license': data['license'],
            'holdout': 'NOT_CHECKED', 'oracle': 'NOT_CHECKED', 'classification_accuracy': 'NOT_CHECKED'}


def write_report(relative, result, replace=False):
    valid(relative)
    if not relative.startswith('artifacts/'): raise Refusal('OUTPUT_PATH')
    rootfd = open_directory_chain(ROOT)
    try:
        with parent(rootfd, relative) as (directory, name):
            if replace:
                # Registered report only, atomically publish without following an old link.
                if relative != 'artifacts/fixture-checks.json': raise Refusal('OUTPUT_PATH')
                import uuid
                temporary = 'fixture-checks-' + str(uuid.uuid4()) + '.tmp'
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                try:
                    with os.fdopen(fd, 'w') as stream:
                        json.dump(result, stream, indent=2); stream.flush(); os.fsync(stream.fileno())
                    os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory); os.fsync(directory)
                finally:
                    try: os.unlink(temporary, dir_fd=directory)
                    except FileNotFoundError: pass
            else:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                with os.fdopen(fd, 'w') as stream:
                    json.dump(result, stream, indent=2); stream.flush(); os.fsync(stream.fileno())
    finally: os.close(rootfd)


def replay_bundle(path):
    from .export import LIMIT, verify
    valid(path)
    parts = path.split('/')
    if Path(path).suffix != '.zip' or (len(parts) > 1 and parts[0] != 'artifacts'):
        raise Refusal('UNREGISTERED_BUNDLE_PATH')
    return verify(read_local(path, LIMIT))


def parser():
    p = argparse.ArgumentParser(description='Synthetic copies only; provider CLOSED')
    p.add_argument('command', choices=['create','scan','preview','approve','apply','recover','undo','cancel','status','probe-recovery','probe-primitive','smoke','fixtures','serve','replay'])
    p.add_argument('--workspace'); p.add_argument('--batch'); p.add_argument('--approval-id'); p.add_argument('--expected-after-sha'); p.add_argument('--count', type=int, default=4)
    p.add_argument('--resume', help='Existing source-bound artifacts progress JSONL'); p.add_argument('--scope', choices=['early','full'], default='early'); p.add_argument('--out'); p.add_argument('--json', help='Exact JSON body; never a path')
    p.add_argument('--manifest'); p.add_argument('--verify', action='store_true'); p.add_argument('--fixtures')
    p.add_argument('--bundle'); p.add_argument('--offline', action='store_true'); p.add_argument('--provider', action='store_true')
    p.add_argument('--host', choices=['127.0.0.1'], default='127.0.0.1'); p.add_argument('--port', type=int, default=8080)
    return p


def main(argv=None):
    p = parser(); a = p.parse_args(argv)
    # Refuse misplaced flags rather than silently ignoring a typo on an action.
    specific = {'manifest': 'fixtures', 'verify': 'fixtures', 'fixtures': 'probe-recovery',
                'bundle': 'replay', 'provider': 'smoke'}
    for flag, command in specific.items():
        if getattr(a, flag) and a.command != command: p.error('--' + flag + ' is only valid for ' + command)
    if a.offline and a.command not in ('replay', 'smoke'): p.error('--offline is only valid for replay or smoke')
    if a.command == 'fixtures' and (not a.verify or a.manifest is None): p.error('fixtures requires --manifest and --verify')
    if a.command == 'replay' and (not a.offline or not a.bundle): p.error('replay requires --bundle and --offline')
    try:
        if a.command == 'smoke':
            r = {'provider': 'CLOSED', 'reason': 'Owner/account/free balance/expiry/charge prevention/model caps unverified'}
            if a.out: write_report(a.out, r)
            print(json.dumps(r)); return 3
        if a.command == 'fixtures':
            r = verify_fixtures(a.manifest)
            write_report('artifacts/fixture-checks.json', r, replace=True)
        elif a.command == 'replay': r = replay_bundle(a.bundle)
        elif a.command == 'serve':
            if not 0 <= a.port <= 65535: raise Refusal('INVALID_PORT')
            from .server import Server
            with Server((a.host, a.port)) as server:
                print(f'FolderUndo owner bootstrap URL is in private file: {server.bootstrap_file}. Open that URL in your local browser. Linux local synthetic lab only.', flush=True)
                server.serve_forever()
            return 0
        elif a.command.startswith('probe-'):
            if a.command == 'probe-recovery' and a.fixtures:
                alias, count = RECOVERY_FIXTURES[a.scope]
                if a.fixtures != alias: raise Refusal('UNREGISTERED_RECOVERY_FIXTURES')
                expected = {'scope': a.scope, 'factory': 'app.core.Store.create', 'count': count}
                if public_manifest()['recovery_fixture_aliases'].get(alias) != expected: raise Refusal('RECOVERY_FIXTURE_MAPPING')
            from tools.probes import recovery, primitive
            r = recovery(a.scope, a.resume) if a.command == 'probe-recovery' else primitive()
            if a.out: write_report(a.out, r)
        elif a.command == 'create': r = {'workspace_id': Store.create(a.count).id}
        else:
            s = Store(a.workspace)
            if a.command == 'scan': r = s.scan()
            elif a.command == 'preview':
                body = json.loads(a.json); r = s.preview(body['proposals'], body['manifest_sha'], body.get('policy_version','synthetic-v1'))
            elif a.command == 'approve': r = s.approve(a.batch, json.loads(a.json))
            elif a.command == 'apply': r = s.apply(a.batch, a.approval_id)
            elif a.command == 'recover': r = s.recover(a.batch)
            elif a.command in ('undo','cancel'): r = s.undo(a.batch, a.expected_after_sha, a.command == 'cancel')
            else: r = s.status(a.batch)
        print(json.dumps(r, indent=2)); return 1 if a.command.startswith('probe-') and r.get('passed') is False else 0
    except (Refusal, ValueError, KeyError, TypeError, OSError) as e:
        code = str(e) if isinstance(e, Refusal) else type(e).__name__
        print(json.dumps({'error': code})); return 2


if __name__ == '__main__': sys.exit(main())
