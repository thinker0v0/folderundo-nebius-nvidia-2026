"""PROPOSED: one trusted job file, one bounded durable JSON result, no shell/API."""
import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import urllib.parse
import uuid

from . import core
from .export import parse
from .execution import Error, execute, failure, identifier


LIMIT = 1024 * 1024


def _json(raw):
    value = parse(raw)
    def check(item, depth=0):
        if depth > 24:
            raise ValueError('JSON_DEPTH')
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError('JSON_KEY')
                check(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check(child, depth + 1)
    check(value)
    return value


def _read(path, limit=LIMIT):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('PATH')
    directory = core.open_directory_chain(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_uid != os.getuid() or before.st_size > limit):
                raise ValueError('FILE')
            chunks = []; size = 0
            while size <= limit:
                block = os.read(fd, min(65536, limit + 1 - size))
                if not block:
                    break
                chunks.append(block); size += len(block)
            after = os.fstat(fd)
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_mode, s.st_nlink)
            if size != before.st_size or identity(before) != identity(after):
                raise ValueError('CHANGED_FILE')
            return b''.join(chunks)
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def _code(identity):
    if not isinstance(identity, dict) or not identity or len(identity) > 100:
        raise ValueError('SOURCE_BINDING')
    root = Path(__file__).resolve().parents[1]
    for relative, expected in identity.items():
        core.valid(relative)
        if not isinstance(expected, str) or re.fullmatch('[0-9a-f]{64}', expected) is None:
            raise ValueError('SOURCE_BINDING')
        if hashlib.sha256(_read(root / relative)).hexdigest() != expected:
            raise ValueError('SOURCE_BINDING')


def _intent_writer(database, job_id):
    database = Path(database)
    # Do not create an accidental control database or follow a linked parent.
    parentfd = core.open_directory_chain(database.parent)
    try:
        info = os.stat(database.name, dir_fd=parentfd, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise ValueError('DATABASE')
    finally:
        os.close(parentfd)
    uri = 'file:' + urllib.parse.quote(str(database), safe='/') + '?mode=rw'

    def write(update):
        if not isinstance(update, dict):
            raise ValueError('INTENT')
        db = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT intent FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise ValueError('UNKNOWN_JOB')
            old = _json(row[0]) if row[0] else {}
            if not isinstance(old, dict):
                raise ValueError('INTENT')
            old.update(update)
            raw = core.canonical(old)
            if len(raw.encode()) > LIMIT:
                raise ValueError('INTENT_LIMIT')
            if db.execute('UPDATE jobs SET intent=? WHERE id=?', (raw, job_id)).rowcount != 1:
                raise ValueError('UNKNOWN_JOB')
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
    return write


def _publish(directory, value):
    raw = core.canonical(value).encode()
    if len(raw) > LIMIT:
        value = {key: value[key] for key in ('job_id', 'job_file_sha256', 'codeidentity')}
        value.update(http_status=500, result=failure('WORKER_RESULT_LIMIT'),
                     run_patch={'blocked': True, 'export_current': None})
        raw = core.canonical(value).encode()
    temporary = 'result-' + str(uuid.uuid4()) + '.pending'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    core.noreplace(directory, temporary, directory, 'result.json')
    os.fsync(directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run one trusted, synthetic coordinator job', add_help=False)
    parser.add_argument('--job-file', required=True)
    args = parser.parse_args(argv)
    directory = lock = None
    binding = None
    try:
        raw = _read(args.job_file)
        value = _json(raw)
        if not isinstance(value, dict) or set(value) != {'job', 'paths', 'codeidentity'}:
            raise ValueError('ENVELOPE')
        job, paths, identity = value['job'], value['paths'], value['codeidentity']
        if not isinstance(job, dict) or not isinstance(paths, dict):
            raise ValueError('ENVELOPE')
        identifier(job['id']); identifier(job['run_id'])
        for key in ('database', 'workspace_base', 'staging_base', 'export_base', 'job_dir'):
            item = paths[key]
            if not isinstance(item, str) or not Path(item).is_absolute() or '..' in Path(item).parts:
                raise ValueError('PATH')
        job_dir = Path(paths['job_dir'])
        if Path(args.job_file).parent != job_dir:
            raise ValueError('JOB_PATH')
        directory = core.open_directory_chain(job_dir)
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('JOB_DIRECTORY')
        lock = os.open('worker.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise ValueError('WORKER_LOCK')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _code(identity)
        binding = {'job_id': job['id'], 'job_file_sha256': hashlib.sha256(raw).hexdigest(), 'codeidentity': identity}
        try:
            prior = _json(_read(job_dir / 'result.json'))
        except FileNotFoundError:
            prior = None
        if prior is not None:
            if not isinstance(prior, dict) or any(prior.get(key) != val for key, val in binding.items()):
                raise ValueError('RESULT_BINDING')
            return 0
        writer = _intent_writer(paths['database'], job['id'])
        outcome = execute(job, paths, writer)
        _publish(directory, dict(binding, **outcome))
        return 0
    except Exception:
        if binding is not None and directory is not None:
            try:
                _publish(directory, dict(binding, http_status=500, result=failure('WORKER_ERROR'),
                                         run_patch={'blocked': True, 'export_current': None}))
                return 0
            except Exception:
                pass
        return 2
    finally:
        if lock is not None:
            os.close(lock)
        if directory is not None:
            os.close(directory)


if __name__ == '__main__':
    sys.exit(main())
