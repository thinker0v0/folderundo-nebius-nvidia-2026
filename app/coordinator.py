"""Durable single-run execution lane; HTTP handlers never execute workspace jobs.

PROPOSED ONLY. Logical forward-task clocks retain admission origin, with explicit
human approval pause intervals. Job execution and all evidence remain synthetic.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import threading
import time
import uuid

from . import core
from .execution import Error, validate_request, failure, config_sha, classification_context, _directory
from .worker_runtime import WorkerProcess
from .coordinator_evidence import build_evidence
from .export import verify as verify_archive

CAP = 3221225472
RESERVE = 33554432
HOST_FLOOR = 1073741824
JOB_RESERVE = 32 * 1024 * 1024
RESULT_LIMIT = 1024 * 1024
JSON_RUN = {'creation_context','preview_context','latest_result','latest_scan','last_intent','export_current','error'}
JSON_JOB = {'body','intent','result','codeidentity'}
TERMINAL = {'APPLIED','UNDONE','CONFLICT'}
FORWARD = {'preview','approve','apply'}
SAFETY = {'undo','cancel','resume','recover'}
READS = {'scan','refresh','export'}
PHASES = {'create','create-preserving','preview','approve','apply','undo','cancel','resume','recover','conflict-fixture','scan','refresh','export'}

class SystemClock:
    @staticmethod
    def time():
        return time.time()

    @staticmethod
    def monotonic():
        return time.monotonic()


def encode(value):
    return core.canonical(value)


def decode(row, fields):
    if row is None:
        return None
    value = dict(row)
    for key in fields:
        if key in value and value[key] is not None:
            value[key] = json.loads(value[key])
    return value


def safe_directory(path):
    path = Path(path).absolute()
    fd = _directory(path, create=True)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise Error('UNSAFE_COORDINATOR_DIRECTORY', 409)
    finally:
        os.close(fd)
    return path


def bounded_read(path, limit):
    path = Path(path)
    fd = core.open_directory_chain(path.parent)
    try:
        handle = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            first = os.fstat(handle)
            if not stat.S_ISREG(first.st_mode) or first.st_nlink != 1 or first.st_uid != os.getuid() or first.st_size > limit:
                raise Error('ERROR_EVIDENCE', 409)
            blocks = []; count = 0
            while count <= limit:
                block = os.read(handle, min(65536, limit + 1 - count))
                if not block:
                    break
                count += len(block); blocks.append(block)
            last = os.fstat(handle)
            if count != first.st_size or (first.st_dev,first.st_ino,first.st_size,first.st_mtime_ns) != (last.st_dev,last.st_ino,last.st_size,last.st_mtime_ns):
                raise Error('ERROR_EVIDENCE', 409)
            return b''.join(blocks)
        finally:
            os.close(handle)
    finally:
        os.close(fd)


def write_once(path, value):
    path = Path(path)
    raw = encode(value).encode()
    if len(raw) > RESULT_LIMIT:
        raise Error('CONTROL_RECORD_LIMIT', 409)
    fd = core.open_directory_chain(path.parent)
    try:
        file = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(file, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.fsync(fd)
    finally:
        os.close(fd)
    return hashlib.sha256(raw).hexdigest()


class Coordinator:
    def __init__(self, database, *, workspace_base=None, task_timeout=180, worker_command=None,
                 autostart=True, clock=None, legacy_quiescent=False, live_config=None, bounded_live_runtime=False):
        if isinstance(task_timeout, bool) or not isinstance(task_timeout, (int,float)) or not 0 < task_timeout <= 180:
            raise ValueError('task_timeout must be positive and at most original180 seconds')
        from .live_provider import config
        self.live_config = config(live_config)
        if type(bounded_live_runtime) is not bool or (bounded_live_runtime and self.live_config is None):
            raise ValueError('BOUNDED_LIVE_REQUIRES_TRUSTED_BROKER')
        self.bounded_live_runtime = bounded_live_runtime
        self.legacy_database = Path(database).absolute()
        safe_directory(self.legacy_database.parent)
        self.database = self.legacy_database.with_name(self.legacy_database.name + '.coordinator-v1.sqlite')
        self.root = safe_directory(self.legacy_database.parent / (self.legacy_database.stem + '-coordinator-v1'))
        os.chmod(self.root, 0o700)
        self.workspace_base = Path(workspace_base or core.BASE).absolute()
        self.staging_base = safe_directory(self.root / 'staging')
        self.export_base = safe_directory(self.root / 'exports')
        self.jobs_base = safe_directory(self.root / 'jobs')
        for p in (self.staging_base,self.export_base,self.jobs_base): os.chmod(p,0o700)
        self.owner_fd = os.open(self.root / 'owner.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(self.owner_fd)
            if info.st_uid != os.getuid() or info.st_nlink != 1 or not stat.S_ISREG(info.st_mode):
                raise Error('UNSAFE_COORDINATOR_OWNER',409)
            fcntl.flock(self.owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.owner_fd); raise
        self.legacy_quiescent = legacy_quiescent is True
        self.clock = clock or SystemClock()
        self.task_timeout = float(task_timeout)
        self.worker_command = worker_command
        self.condition = threading.Condition(threading.RLock())
        self.accepting = True
        self.closing = False
        self.thread = None
        self.active = None
        self.active_worker = None
        self.active_deadline = None
        self.fatal = None
        self.closed = False
        self.last_wall = float(self.clock.time())
        self.codeidentity = self._identity()
        try:
            self._schema()
            self._import_legacy()
            self._restart()
            self._resource_guard()
        except BaseException:
            os.close(self.owner_fd); self.owner_fd = None; raise
        if autostart:
            self.start()

    def _identity(self):
        root = Path(__file__).resolve().parents[1]
        names = ['app/core.py','app/execution.py','app/coordinator_worker.py','app/coordinator.py',
                 'app/worker_runtime.py','app/live_provider.py','app/provider_runtime.py','app/coordinator_evidence.py','app/service.py','app/server.py','app/export.py','app/classifier.py','app/policy.py','requirements.lock']
        return {name: hashlib.sha256(bounded_read(root/name,RESULT_LIMIT)).hexdigest() for name in names}

    def connect(self):
        if self.database.exists():
            s = self.database.lstat()
            if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_uid != os.getuid():
                raise Error('UNSAFE_COORDINATOR_DATABASE',409)
        db = sqlite3.connect(self.database, timeout=5)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA synchronous=FULL')
            return db
        except BaseException:
            db.close()
            raise

    @contextmanager
    def connection(self):
        db = self.connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally:
            db.close()

    def _schema(self):
        with self.connection() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(
              id TEXT PRIMARY KEY,workspace_id TEXT,batch TEXT,fixture TEXT NOT NULL,mode TEXT NOT NULL,
              creation_context TEXT,preview_context TEXT,latest_result TEXT,latest_scan TEXT,last_intent TEXT,
              state TEXT NOT NULL,stage TEXT NOT NULL,blocked INTEGER NOT NULL DEFAULT 0,
              observed_at REAL,export_current TEXT,error TEXT,task_id TEXT,forward_task_id TEXT,last_job_id TEXT,
              created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(
              id TEXT PRIMARY KEY,run_id TEXT NOT NULL,kind TEXT NOT NULL,parent_task_id TEXT,
              origin REAL NOT NULL,original_deadline REAL NOT NULL,timeout REAL NOT NULL,
              paused_total REAL NOT NULL DEFAULT 0,pause_started REAL,status TEXT NOT NULL,
              finished_at REAL,reason TEXT);
            CREATE TABLE IF NOT EXISTS jobs(
              seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,run_id TEXT NOT NULL,
              action TEXT NOT NULL,signature TEXT NOT NULL,body TEXT NOT NULL,state TEXT NOT NULL,
              task_id TEXT,accepted_at REAL NOT NULL,deadline REAL,intent TEXT NOT NULL DEFAULT '{}',
              codeidentity TEXT NOT NULL,http_status INTEGER,result TEXT,finished_at REAL,pid INTEGER,
              job_file_sha256 TEXT,worker_exit INTEGER,cleanup TEXT,evidence_record TEXT);
            CREATE TABLE IF NOT EXISTS events(run TEXT,seq INTEGER,payload TEXT,PRIMARY KEY(run,seq));
            CREATE TABLE IF NOT EXISTS legacy_requests(key TEXT PRIMARY KEY,signature TEXT,status INTEGER,result TEXT);
            CREATE TABLE IF NOT EXISTS exports(run TEXT,revision TEXT,path TEXT,sha256 TEXT,bytes INTEGER,
              PRIMARY KEY(run,revision));
            ''')
            db.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)',('schema_version','1'))
            if db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]!='1':
                raise Error('UNKNOWN_COORDINATOR_SCHEMA',409)
            db.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)',('last_wall',str(self.last_wall)))
        os.chmod(self.database,0o600)
        fd = core.open_directory_chain(self.database.parent)
        try: os.fsync(fd)
        finally: os.close(fd)

    def _event(self, db, run, kind, **extra):
        seq = db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE run=?',(run,)).fetchone()[0]
        value = dict(seq=seq,time=float(self.clock.time()),kind=kind,message=kind.replace('_',' ').capitalize(),**extra)
        db.execute('INSERT INTO events VALUES(?,?,?)',(run,seq,encode(value)))

    @contextmanager
    def _legacy_snapshot(self):
        # Explicit installation precondition: the legacy HTTP/CLI service is
        # stopped. SQLite lock acquisition independently excludes an active DB
        # reader/writer; it cannot prove absence of a legacy handler between DB
        # transactions. The opt-in is trusted operator config, never HTTP input.
        if not self.legacy_quiescent:
            raise Error('STOP_LEGACY_SERVICE_AND_SET_LEGACY_QUIESCENT',409)
        path=self.legacy_database
        fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK)
        old=None
        try:
            first=os.fstat(fd)
            if not stat.S_ISREG(first.st_mode) or first.st_nlink!=1 or first.st_uid!=os.getuid() or first.st_size>16*1024*1024:
                raise Error('UNSAFE_LEGACY_DATABASE',409)
            # SQLite's pending/reserved/shared lock bytes. Nonblocking and held
            # through immutable reads/hash comparison; no checkpoint or writes.
            try:fcntl.lockf(fd,fcntl.LOCK_EX|fcntl.LOCK_NB,512,1073741824)
            except BlockingIOError:raise Error('LEGACY_DATABASE_NOT_QUIESCENT',409) from None
            def no_sidecars():
                if any(os.path.lexists(str(path)+suffix) for suffix in ('-wal','-shm','-journal')):
                    raise Error('LEGACY_SIDECAR_REQUIRES_OFFLINE_MIGRATION',409)
            def digest():
                h=hashlib.sha256();offset=0
                while offset<first.st_size:
                    data=os.pread(fd,min(65536,first.st_size-offset),offset)
                    if not data:raise Error('LEGACY_DATABASE_CHANGED',409)
                    h.update(data);offset+=len(data)
                return h.hexdigest()
            no_sidecars();before=digest()
            old=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
            old.row_factory=sqlite3.Row
            metadata={'path':str(path),'sha256':before,'bytes':first.st_size,
                'device':first.st_dev,'inode':first.st_ino,'mode':stat.S_IMODE(first.st_mode),
                'mtime_ns':first.st_mtime_ns,'read_mode':'immutable with no sidecars and SQLite byte locks',
                'operator_precondition':'legacy service stopped; explicitly attested by startup option'}
            yield old,metadata
            no_sidecars();last=os.fstat(fd)
            identity=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mode,x.st_mtime_ns,x.st_nlink)
            named=path.lstat()
            if identity(first)!=identity(last) or identity(first)!=identity(named) or digest()!=before:
                raise Error('LEGACY_DATABASE_CHANGED',409)
        finally:
            if old is not None:old.close()
            os.close(fd)

    def _import_legacy(self):
        with self.transaction() as db:
            if db.execute('SELECT 1 FROM metadata WHERE key="legacy_imported"').fetchone(): return
            if os.path.lexists(self.legacy_database):
                with self._legacy_snapshot() as (old,metadata):
                    tables={r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if {'runs','run_context','requests','events'}-tables: raise Error('UNKNOWN_LEGACY_SCHEMA',409)
                    for r in old.execute('SELECT * FROM runs'):
                        core.uid(r['id'])
                        c=old.execute('SELECT creation,preview FROM run_context WHERE run=?',(r['id'],)).fetchone()
                        db.execute('INSERT OR IGNORE INTO runs(id,workspace_id,batch,fixture,mode,creation_context,preview_context,state,stage,blocked,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                            (r['id'],r['id'],r['batch'],r['fixture'],r['mode'],c['creation'] if c else None,c['preview'] if c else None,'NEEDS_REFRESH','RETAINED_HISTORY',1,self.last_wall))
                        for e in old.execute('SELECT seq,payload FROM events WHERE run=? ORDER BY seq',(r['id'],)):
                            db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?)',(r['id'],e['seq'],e['payload']))
                    for r in old.execute('SELECT * FROM requests'):
                        db.execute('INSERT OR IGNORE INTO legacy_requests VALUES(?,?,?,?)',tuple(r))
                    db.execute('INSERT INTO metadata VALUES(?,?)',('legacy_source',encode(metadata)))
            db.execute('INSERT INTO metadata VALUES(?,?)',('legacy_imported','1'))

    def _restart(self):
        # No factory, movement, reconciliation or automatic queued replay on startup.
        with self.transaction() as db:
            previous=float(db.execute('SELECT value FROM metadata WHERE key="last_wall"').fetchone()[0])
            now=float(self.clock.time()); rollback=now<previous
            self.last_wall=max(now,previous)
            for job in db.execute("SELECT * FROM jobs WHERE state IN ('QUEUED','RUNNING')").fetchall():
                reason='CLOCK_ROLLBACK' if rollback else 'ERROR_INTERRUPTED'
                self._interrupt(db,job,reason,self.last_wall)
            for task in db.execute("SELECT * FROM tasks WHERE status IN ('ACTIVE','PAUSED')").fetchall():
                reason='CLOCK_ROLLBACK' if rollback else 'ERROR_INTERRUPTED'
                db.execute("UPDATE tasks SET status='FAILED',finished_at=?,reason=? WHERE id=?",(self.last_wall,reason,task['id']))
                db.execute("UPDATE runs SET state='ERROR_INTERRUPTED',blocked=1,export_current=NULL,error=?,stage=? WHERE id=?",(encode(failure(reason)['error']),reason,task['run_id']))
                self._event(db,task['run_id'],reason,task_id=task['id'])
            if rollback:
                db.execute("UPDATE tasks SET status='FAILED',finished_at=?,reason='CLOCK_ROLLBACK' WHERE status IN ('ACTIVE','PAUSED')",(self.last_wall,))
                db.execute("UPDATE runs SET blocked=1,export_current=NULL,error=?,stage='CLOCK_ROLLBACK' WHERE task_id IN (SELECT id FROM tasks WHERE reason='CLOCK_ROLLBACK')",(encode(failure('CLOCK_ROLLBACK')['error']),))
            db.execute('UPDATE metadata SET value=? WHERE key="last_wall"',(str(self.last_wall),))

    def _now(self, db):
        now=float(self.clock.time())
        recorded=float(db.execute('SELECT value FROM metadata WHERE key="last_wall"').fetchone()[0])
        if now<recorded or now<self.last_wall:
            raise Error('CLOCK_ROLLBACK',409)
        self.last_wall=now
        db.execute('UPDATE metadata SET value=? WHERE key="last_wall"',(str(now),))
        return now

    def _resource_guard(self, inode_reserve=32):
        # Retained group accounting, directories included; no recovery helper imports.
        def walk(base):
            if not base.exists(): return []
            values=[base]; pending=[base]
            while pending:
                for p in pending.pop().iterdir():
                    values.append(p)
                    if p.is_dir() and not p.is_symlink(): pending.append(p)
            return values
        def charge(paths):
            a=l=0
            for p in paths:
                try:s=p.lstat()
                except FileNotFoundError:continue
                a+=s.st_blocks*512;l+=s.st_size
            return max(a,l)
        roots=[]
        for root in (self.workspace_base.parent,self.root.parent):
            if not any(root==p or p in root.parents for p in roots):
                roots=[p for p in roots if root not in p.parents];roots.append(root)
        used=sum(charge(walk(p)) for p in roots)
        project=Path(core.__file__).resolve().parents[2]
        external=project/'judging-20261028'
        if external.exists() and not any(p==external or p in external.parents for p in roots):
            used+=charge(p for p in walk(external) if p!=external and ('folder' in str(p.relative_to(external)) or '/fourth/' in '/'+str(p.relative_to(external))+'/'))
        fs=os.statvfs(self.root)
        if self.bounded_live_runtime:
            # Conservative admission margin, not a completion guarantee.
            if fs.f_favail<inode_reserve:
                raise Error('RESOURCE_INODE_ADMISSION_CLOSED',429)
            # Trusted seven single-document runs, retaining earlier failed/interrupted runs. Host floor remains
            # unchanged; a separately bounded native filesystem is not the host.
            host=os.statvfs(Path(__file__).resolve().parents[1])
            native_reserve=8*1024*1024
            if (host.f_bavail*host.f_frsize<HOST_FLOOR
                    or fs.f_blocks*fs.f_frsize>16*1024*1024
                    or fs.f_bavail*fs.f_frsize<native_reserve
                    or used+native_reserve+RESERVE>=CAP):
                raise Error('RESOURCE_ADMISSION_CLOSED',429)
        elif used+JOB_RESERVE+RESERVE>=CAP or fs.f_bavail*fs.f_frsize-JOB_RESERVE<HOST_FLOOR:
            raise Error('RESOURCE_ADMISSION_CLOSED',429)
        return used

    def _new_task(self,db,run,kind,now,parent=None):
        task=str(uuid.uuid4())
        db.execute('INSERT INTO tasks(id,run_id,kind,parent_task_id,origin,original_deadline,timeout,status) VALUES(?,?,?,?,?,?,?,?)',
                   (task,run,kind,parent,now,now+self.task_timeout,self.task_timeout,'ACTIVE'))
        db.execute('UPDATE runs SET task_id=? WHERE id=?',(task,run))
        if kind=='FORWARD':db.execute('UPDATE runs SET forward_task_id=? WHERE id=?',(task,run))
        return dict(db.execute('SELECT * FROM tasks WHERE id=?',(task,)).fetchone())

    def _task(self,db,run,action,now):
        previous=db.execute('SELECT * FROM tasks WHERE id=?',(run['task_id'],)).fetchone() if run.get('task_id') else None
        if action in SAFETY:
            if previous and previous['status'] in ('ACTIVE','PAUSED'):
                db.execute("UPDATE tasks SET status='FAILED',finished_at=?,reason='EXPLICIT_SAFETY_TRANSITION' WHERE id=?",(now,previous['id']))
            return self._new_task(db,run['id'],'UNDO' if action=='undo' else 'SAFETY_RECOVERY',now,previous['id'] if previous else None)
        if action in FORWARD:
            forward=db.execute('SELECT * FROM tasks WHERE id=?',(run.get('forward_task_id'),)).fetchone()
            if forward and forward['status'] not in ('ACTIVE','PAUSED'):
                raise Error('FORWARD_TASK_FINISHED_OR_INTERRUPTED',409)
        if previous and previous['status'] in ('ACTIVE','PAUSED'):
            task=dict(previous)
            if task['pause_started'] is not None:
                if now<task['pause_started']:raise Error('CLOCK_ROLLBACK',409)
                task['paused_total']+=now-task['pause_started']
                db.execute("UPDATE tasks SET paused_total=?,pause_started=NULL,status='ACTIVE' WHERE id=?",(task['paused_total'],task['id']))
                task['pause_started']=None;task['status']='ACTIVE'
                self._event(db,run['id'],'HUMAN_WAIT_ENDED_AT_ADMISSION',task_id=task['id'],paused_total=task['paused_total'])
            return task
        # Legacy forward states get a newly identified explicit forward task only
        # after a current read; no interrupted task's origin is rewritten.
        if action in FORWARD:
            if run.get('forward_task_id') or run.get('blocked'):raise Error('REFRESH_OR_NEW_COPY_REQUIRED',409)
            return self._new_task(db,run['id'],'FORWARD',now)
        return self._new_task(db,run['id'],'OBSERVATION',now,previous['id'] if previous else None)

    def admit(self, action, run_id, body, key):
        core.uid(key)
        if action not in PHASES:raise Error('UNKNOWN_ACTION',404)
        if action not in ('create','create-preserving'):core.uid(run_id)
        signature=core.digest({'action':action,'run':run_id,'body':body})
        # Duplicates must remain replayable even when current disk admission closes.
        with self.connection() as db:
            existing=db.execute('SELECT signature FROM jobs WHERE id=? UNION ALL SELECT signature FROM legacy_requests WHERE key=? LIMIT 1',(key,key)).fetchone()
        if not existing:self._resource_guard(inode_reserve=128 if action in ('create','create-preserving') else 32)
        with self.condition:
            if not self.accepting or self.fatal:raise Error('COORDINATOR_CLOSED',503)
            with self.transaction() as db:
                previous=db.execute('SELECT * FROM jobs WHERE id=?',(key,)).fetchone()
                if previous:
                    if previous['signature']!=signature:raise Error('IDEMPOTENCY_CONFLICT',409)
                    return self._ack(previous)
                legacy=db.execute('SELECT * FROM legacy_requests WHERE key=?',(key,)).fetchone()
                if legacy:
                    if legacy['signature']!=signature:raise Error('IDEMPOTENCY_CONFLICT',409)
                    old=json.loads(legacy['result']);rid=run_id or old.get('run_id')
                    if not rid:raise Error('RETAINED_REQUEST_UNAVAILABLE',409)
                    db.execute('INSERT INTO jobs(id,run_id,action,signature,body,state,accepted_at,intent,codeidentity,http_status,result) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (key,rid,action,signature,encode(body),'RECORDED',self.last_wall,'{}',encode(self.codeidentity),legacy['status'],legacy['result']))
                    return self._ack(db.execute('SELECT * FROM jobs WHERE id=?',(key,)).fetchone())
                now=self._now(db)
                create=action in ('create','create-preserving')
                run=None if create else decode(db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone(),JSON_RUN)
                if not create and not run:raise Error('UNKNOWN_RUN',404)
                validate_request(action,run,body,self.live_config)
                if self.bounded_live_runtime and action in ('create','create-preserving'):
                    if body['mode']!='B1': raise Error('BOUNDED_LIVE_FIXTURE_ONLY',409)
                    if db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]>=7:
                        raise Error('BOUNDED_LIVE_RUN_LIMIT',409)
                # Every machine read/control command shares the same bounded RUN lane.
                active=db.execute("SELECT run_id FROM jobs WHERE state='RUNNING' ORDER BY seq LIMIT 1").fetchone()
                active_run=active['run_id'] if active else None
                waiting={r[0] for r in db.execute("SELECT DISTINCT run_id FROM jobs WHERE state='QUEUED'")} - ({active_run} if active_run else set())
                pending=[] if create else db.execute("SELECT * FROM jobs WHERE run_id=? AND state IN ('QUEUED','RUNNING') ORDER BY seq",(run_id,)).fetchall()
                if pending and action!='cancel':raise Error('RUN_BUSY',409)
                if action=='cancel' and any(j['action']=='cancel' for j in pending):raise Error('RUN_BUSY',409)
                if create or (run_id!=active_run and run_id not in waiting):
                    if len(waiting)>=10:raise Error('QUEUE_FULL',429)
                if create:
                    run_id=str(uuid.uuid4())
                    db.execute('INSERT INTO runs(id,fixture,mode,state,stage,created_at) VALUES(?,?,?,?,?,?)',
                        (run_id,body['fixture_id'],body['mode'],'CREATED','QUEUED',now))
                    task=self._new_task(db,run_id,'FORWARD',now)
                else:
                    if action in FORWARD and run.get('blocked'):raise Error('REFRESH_OR_RECOVERY_REQUIRED',409)
                    if pending and action=='cancel':
                        for old in pending:
                            if old['state']=='QUEUED':self._interrupt(db,old,'CANCELLED_BEFORE_EXECUTION',now,state='CANCELLED')
                    task=self._task(db,run,action,now)
                deadline=task['original_deadline']+task['paused_total']
                db.execute('INSERT INTO jobs(id,run_id,action,signature,body,state,task_id,accepted_at,deadline,intent,codeidentity) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (key,run_id,action,signature,encode(body),'QUEUED',task['id'],now,deadline,'{}',encode(self.codeidentity)))
                db.execute('UPDATE runs SET last_job_id=?,stage=?,export_current=NULL,error=NULL WHERE id=?',(key,'CANCEL_REQUESTED' if action=='cancel' and pending else 'QUEUED',run_id))
                self._event(db,run_id,'ADMITTED',request_id=key,action=action,task_id=task['id'],deadline=deadline)
                admitted=db.execute('SELECT * FROM jobs WHERE id=?',(key,)).fetchone()
                if deadline<=now:self._interrupt(db,admitted,'TASK_DEADLINE_EXHAUSTED',now)
                ack=self._ack(admitted)
            if action=='cancel' and self.active_worker and active_run==run_id:
                self.active_worker.request_stop('CANCELLED_ACTIVE_PHASE')
            self.condition.notify_all()
            return ack

    def _ack(self,job):
        return {'run_id':job['run_id'],'request_id':job['id'],'state':'CREATED' if job['action'] in ('create','create-preserving') else job['state'],
                'poll_url':'/runs/'+job['run_id']}

    def job(self,key):
        core.uid(key)
        with self.connection() as db:
            row=decode(db.execute('SELECT * FROM jobs WHERE id=?',(key,)).fetchone(),JSON_JOB)
            if not row:raise Error('UNKNOWN_REQUEST',404)
            return row

    def wait(self,key):
        with self.condition:
            while True:
                job=self.job(key)
                if job['state'] not in ('QUEUED','RUNNING'):
                    result=job['result'] or failure('ERROR_INTERRUPTED')
                    if job['http_status']==200 and job['action'] in ('apply','undo','resume','recover','cancel'):
                        with self.connection() as db:
                            run=decode(db.execute('SELECT * FROM runs WHERE id=?',(job['run_id'],)).fetchone(),JSON_RUN)
                            if run and not run['blocked'] and run['latest_result'] is not None:
                                result=run['latest_result']
                    return job['http_status'] or 409,result
                if self.closed or self.fatal:raise Error('COORDINATOR_CLOSED',503)
                self.condition.wait(0.1)

    def status(self,run_id,after=0):
        core.uid(run_id)
        if isinstance(after,bool) or not isinstance(after,int) or not 0<=after<=1000000000:raise Error('INVALID_QUERY',400)
        with self.connection() as db:
            run=decode(db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone(),JSON_RUN)
            if not run:raise Error('UNKNOWN_RUN',404)
            busy=bool(db.execute("SELECT 1 FROM jobs WHERE run_id=? AND state IN ('QUEUED','RUNNING')",(run_id,)).fetchone())
            events=[json.loads(r[0]) for r in db.execute('SELECT payload FROM events WHERE run=? AND seq>? ORDER BY seq LIMIT 200',(run_id,after))]
            tasks=[dict(r) for r in db.execute('SELECT * FROM tasks WHERE run_id=? ORDER BY origin,id',(run_id,))]
            clock_bad=float(self.clock.time())<float(db.execute('SELECT value FROM metadata WHERE key="last_wall"').fetchone()[0])
            current=bool(run['observed_at'] is not None and not busy and not run['blocked'] and not clock_bad)
            return dict(run_id=run_id,workspace_id=run['workspace_id'],fixture_id=run['fixture'],mode=run['mode'],provider='LIVE_BROKER' if run['mode']=='B1' else 'CLOSED',
                state=run['state'],stage=run['stage'],busy=busy,blocked=bool(run['blocked'] or clock_bad),result=run['latest_result'],
                observed_at=run['observed_at'],snapshot_current=current,scan_ready=bool(run['workspace_id'] and not busy),
                export_ready=bool(not busy and not clock_bad and run['export_current'] and (current or run['export_current'].get('kind')=='failure_receipt')),
                export_kind=run['export_current'].get('kind') if run['export_current'] else None,context={'creation':run['creation_context'],'preview':run['preview_context'],'provider_evidence':(run.get('last_intent') or {}).get('provider_evidence')},
                events=events,next_after=events[-1]['seq'] if events else after,error=run['error'] or (failure('CLOCK_ROLLBACK')['error'] if clock_bad else None),tasks=tasks)

    def session_view(self):
        with self.connection() as db:
            return {'registered_runs':[dict(r) for r in db.execute('SELECT id,fixture,mode FROM runs ORDER BY created_at DESC,id LIMIT 100')],
                    'provider':'LIVE_BROKER' if self.live_config else 'CLOSED','config_shas':{'reference':config_sha('reference'),'B0':config_sha('B0'), **({'B1':config_sha('B1')} if self.live_config else {})},'classification':classification_context()}

    def stored_export(self,run_id):
        status=self.status(run_id)
        if status['busy']:raise Error('RUN_BUSY',409)
        if not status['export_ready']:raise Error('EXPORT_NOT_READY',409)
        with self.connection() as db:
            row=db.execute('SELECT export_current FROM runs WHERE id=?',(run_id,)).fetchone()
            if not row or not row[0]:raise Error('EXPORT_NOT_READY',409)
            descriptor=json.loads(row[0])
        try:
            relative=descriptor['path'];core.valid(relative)
            raw=bounded_read(self.export_base/relative,8*1024*1024)
            if len(raw)!=descriptor['bytes'] or hashlib.sha256(raw).hexdigest()!=descriptor['sha256']:
                raise Error('ERROR_EVIDENCE',409)
            verify_archive(raw)
            return raw
        except BaseException as exc:
            # Artifact failure revokes only control metadata. No workspace reads,
            # journal reconciliation, archive rebuild or automatic worker enqueue.
            with self.transaction() as db:
                db.execute('UPDATE runs SET export_current=NULL,blocked=1,error=?,stage=? WHERE id=?',
                           (encode(failure('ERROR_EVIDENCE')['error']),'ERROR_EVIDENCE',run_id))
                self._event(db,run_id,'EXPORT_REVOKED')
            raise Error('ERROR_EVIDENCE',409) from exc

    def _interrupt(self,db,job,reason,now,state='INTERRUPTED'):
        reason=str(reason).split(':',1)[0].upper()
        result=failure(reason)
        db.execute('UPDATE jobs SET state=?,http_status=409,result=?,finished_at=? WHERE id=?',(state,encode(result),now,job['id']))
        if job['task_id']:
            db.execute("UPDATE tasks SET status='FAILED',finished_at=?,reason=? WHERE id=? AND status IN ('ACTIVE','PAUSED')",(now,reason,job['task_id']))
        db.execute('UPDATE runs SET state=?,blocked=1,export_current=NULL,error=?,stage=?,last_intent=? WHERE id=?',
            ('CANCELLED' if state=='CANCELLED' else 'ERROR_INTERRUPTED',encode(result['error']),reason,job['intent'],job['run_id']))
        self._event(db,job['run_id'],reason,request_id=job['id'],task_id=job['task_id'])

    def _expire(self,db,now):
        for job in db.execute("SELECT * FROM jobs WHERE state='QUEUED' AND deadline<=?",(now,)).fetchall():
            self._interrupt(db,job,'TASK_TIMEOUT_IN_QUEUE',now)

    def _select(self,db,now):
        self._expire(db,now)
        # A queued cancellation of the current run is its bounded control continuation,
        # not an eleventh waiting RUN. It precedes unrelated waiting runs after reap.
        if self.active:
            previous=db.execute('SELECT run_id FROM jobs WHERE id=?',(self.active,)).fetchone()
            if previous:
                row=db.execute("SELECT * FROM jobs WHERE run_id=? AND action='cancel' AND state='QUEUED' ORDER BY seq LIMIT 1",(previous['run_id'],)).fetchone()
                if row:return row
        return db.execute("SELECT * FROM jobs WHERE state='QUEUED' ORDER BY seq LIMIT 1").fetchone()

    def start(self):
        with self.condition:
            if self.closed:raise Error('COORDINATOR_CLOSED',503)
            if self.thread:return
            self.thread=threading.Thread(target=self._loop,name='folderundo-run-supervisor',daemon=False)
            self.thread.start()

    def wake(self):
        worker=self.active_worker
        if worker:worker.wake()
        with self.condition:self.condition.notify_all()

    def runtime_snapshot(self):
        with self.condition,self.connection() as db:
            queued=[dict(r) for r in db.execute("SELECT id,run_id FROM jobs WHERE state='QUEUED' ORDER BY seq")]
            active_run=db.execute('SELECT run_id FROM jobs WHERE id=?',(self.active,)).fetchone() if self.active else None
            waiting={j['run_id'] for j in queued}-({active_run[0]} if active_run else set())
            return {'active':self.active,'active_pid':self.active_worker.pid if self.active_worker else None,
                    'queued':[j['id'] for j in queued],'queue_waiting_runs':len(waiting),'accepting':self.accepting,'fatal':self.fatal}

    def _payload(self,job):
        with self.connection() as db:
            run=decode(db.execute('SELECT * FROM runs WHERE id=?',(job['run_id'],)).fetchone(),JSON_RUN)
        directory=self.jobs_base/job['id']
        fd=_directory(directory,create=True);os.close(fd)
        payload={'job':{**decode(job,JSON_JOB),'run':run,'live_config':self.live_config,'deadline_monotonic_ns':int((self.clock.monotonic()+job['deadline']-self.clock.time())*1e9)},'paths':{'database':str(self.database),'workspace_base':str(self.workspace_base),
            'staging_base':str(self.staging_base),'export_base':str(self.export_base),'job_dir':str(directory)},'codeidentity':self.codeidentity}
        path=directory/'job.json';digest=write_once(path,payload)
        return path,digest

    def _evidence(self,job,result=None,error=None):
        # Bookkeeping after quiescence, never workspace execution. Failed jobs
        # may publish only sanitized control receipts with preservation unknown.
        with self.connection() as db:
            current=db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone()
            if current['evidence_record']:return json.loads(current['evidence_record'])
            run=decode(db.execute('SELECT * FROM runs WHERE id=?',(job['run_id'],)).fetchone(),JSON_RUN)
            task=dict(db.execute('SELECT * FROM tasks WHERE id=?',(job['task_id'],)).fetchone()) if job['task_id'] else None
            events=[json.loads(r[0]) for r in db.execute('SELECT payload FROM events WHERE run=? ORDER BY seq DESC LIMIT 199',(job['run_id'],))][::-1]
        http_status=result['http_status'] if result is not None and not error else current['http_status'] or 409
        payload=result['result'] if result is not None and not error else failure(error) if error else json.loads(current['result'] or '{}')
        patch=result['run_patch'] if result is not None and not error else {}
        domain=patch.get('export_record');raw_domain=None
        if domain:
            core.valid(domain['path']);raw_domain=bounded_read(self.export_base/domain['path'],8*1024*1024)
            if len(raw_domain)!=domain['bytes'] or hashlib.sha256(raw_domain).hexdigest()!=domain['sha256']:
                raise Error('ERROR_EVIDENCE',409)
            verify_archive(raw_domain)
        # Successful nonterminal phases have no export link. Their events remain
        # durable until the complete machine task or a finalized failure receipt.
        if 200<=http_status<300 and not domain and patch.get('state')!='CANCELLED':return None
        context=run.get('creation_context') or {}
        observed=patch.get('latest_result') or run.get('latest_result') or {}
        state=patch.get('state',run['state']) if domain or patch.get('state')=='CANCELLED' else 'ERROR'
        events.append(dict(seq=(events[-1]['seq']+1 if events else 1),time=float(self.clock.time()),kind='RECEIPT_FINALIZED',message='Receipt finalized',request_id=job['id'],action=job['action'],task_id=job['task_id'],http_status=http_status))
        envelope=build_evidence(dict(run_id=job['run_id'],fixture_id=run['fixture'],config_sha=context.get('config_sha'),
            input_sha=observed.get('before_sha') if domain else None,oracle_sha=None,codeidentity=self.codeidentity,
            request_id=job['id'],action=job['action'],state=state,result=payload,events=events,task=task,http_status=http_status,
            workspace_id=patch.get('workspace_id',run.get('workspace_id')),domain_sha=hashlib.sha256(raw_domain).hexdigest() if raw_domain else None,
            preservation={'established':False,'scope':'Original fixture preservation is not independently established by this receipt.'},
            runtime_versions={'python':'.'.join(map(str,sys.version_info[:3])),'sqlite':sqlite3.sqlite_version,'implementation':sys.implementation.name}),raw_domain)
        if self.bounded_live_runtime and len(envelope)>1024*1024:
            raise Error('LIVE_EXPORT_LIMIT',409)
        verify_archive(envelope)
        digest=hashlib.sha256(envelope).hexdigest();relative=job['run_id']+'/'+digest+'.receipt.zip'
        root=safe_directory(self.export_base/job['run_id']);os.chmod(root,0o700)
        fd=core.open_directory_chain(root)
        try:
            target=root/(digest+'.receipt.zip')
            if target.exists():
                if bounded_read(target,8*1024*1024)!=envelope:raise Error('ERROR_EVIDENCE',409)
            else:
                name=digest+'.'+str(uuid.uuid4())+'.pending'
                handle=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                with os.fdopen(handle,'wb') as stream:stream.write(envelope);stream.flush();os.fsync(stream.fileno())
                core.noreplace(fd,name,fd,target.name);os.fsync(fd)
        finally:os.close(fd)
        return {'revision':digest,'path':relative,'sha256':digest,'bytes':len(envelope),
                'kind':'failure_receipt' if not 200<=http_status<300 or state in ('CONFLICT','CANCELLED') else 'domain_receipt'}

    def _register_evidence(self,db,job,descriptor):
        if descriptor is None:return
        values=(descriptor['path'],descriptor['sha256'],descriptor['bytes'])
        previous=db.execute('SELECT path,sha256,bytes FROM exports WHERE run=? AND revision=?',(job['run_id'],descriptor['revision'])).fetchone()
        if previous and tuple(previous)!=values:raise Error('EXPORT_REVISION_MISMATCH',409)
        db.execute('INSERT OR IGNORE INTO exports VALUES(?,?,?,?,?)',(job['run_id'],descriptor['revision'],*values))
        db.execute('UPDATE jobs SET evidence_record=? WHERE id=?',(encode(descriptor),job['id']))
        latest=db.execute('SELECT last_job_id FROM runs WHERE id=?',(job['run_id'],)).fetchone()[0]
        if latest==job['id']:db.execute('UPDATE runs SET export_current=? WHERE id=?',(encode(descriptor),job['run_id']))

    def _pending_failure_evidence(self):
        with self.connection() as db:
            pending=db.execute("SELECT j.* FROM jobs j JOIN runs r ON r.last_job_id=j.id WHERE j.state IN ('FAILED','INTERRUPTED','CANCELLED') AND j.evidence_record IS NULL ORDER BY j.seq LIMIT 1").fetchone()
        if pending:
            descriptor=self._evidence(pending)
            with self.transaction() as db:self._register_evidence(db,pending,descriptor)

    def _finish(self,job,worker,result,error,cleanup,evidence=None):
        with self.transaction() as db:
            now=self._now(db)
            current=db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone()
            if job['deadline'] is not None and now>job['deadline']:
                error=error or 'TASK_DEADLINE_EXCEEDED_DURING_COMPLETION';evidence=None
            if error:
                self._interrupt(db,current,error,now)
            else:
                patch=result['run_patch'];allowed={'workspace_id','batch','fixture','mode','creation_context','preview_context','latest_result','latest_scan','last_intent','state','blocked','export_current','export_record','snapshot_observed_at'}
                if set(patch)-allowed:raise Error('UNKNOWN_WORKER_PATCH',409)
                for name,value in patch.items():
                    if name=='export_record':continue
                    column='observed_at' if name=='snapshot_observed_at' else name
                    if name in JSON_RUN:value=encode(value) if value is not None else None
                    if name=='blocked':value=int(bool(value))
                    db.execute('UPDATE runs SET '+column+'=? WHERE id=?',(value,job['run_id']))
                descriptor=patch.get('export_record')
                if descriptor:
                    previous=db.execute('SELECT path,sha256,bytes FROM exports WHERE run=? AND revision=?',(job['run_id'],descriptor['revision'])).fetchone()
                    values=(descriptor['path'],descriptor['sha256'],descriptor['bytes'])
                    if previous and tuple(previous)!=values:raise Error('EXPORT_REVISION_MISMATCH',409)
                    db.execute('INSERT OR IGNORE INTO exports VALUES(?,?,?,?,?)',(job['run_id'],descriptor['revision'],*values))
                status=result['http_status'];ok=200<=status<300
                db.execute('UPDATE jobs SET state=?,http_status=?,result=?,finished_at=? WHERE id=?',('SUCCEEDED' if ok else 'FAILED',status,encode(result['result']),now,job['id']))
                pending=db.execute("SELECT 1 FROM jobs WHERE run_id=? AND state='QUEUED'",(job['run_id'],)).fetchone()
                db.execute('UPDATE runs SET stage=?,error=? WHERE id=?',('QUEUED' if pending else ('OBSERVED' if ok else 'FAILED'),None if ok else encode(result['result'].get('error',failure('WORKER_FAILURE')['error'])),job['run_id']))
                task=db.execute('SELECT * FROM tasks WHERE id=?',(job['task_id'],)).fetchone()
                state=patch.get('state')
                if not ok:
                    db.execute("UPDATE tasks SET status='FAILED',finished_at=?,reason=? WHERE id=?",(now,result['result'].get('error',{}).get('code','WORKER_FAILURE').split(':',1)[0].upper(),job['task_id']))
                elif task['status']=='ACTIVE':
                    if task['kind']=='FORWARD' and state in ('AWAITING_APPROVAL','APPROVED'):
                        db.execute("UPDATE tasks SET status='PAUSED',pause_started=? WHERE id=?",(now,job['task_id']))
                        self._event(db,job['run_id'],'HUMAN_APPROVAL_WAIT_STARTED',task_id=job['task_id'])
                    elif (task['kind']=='FORWARD' and state in TERMINAL and descriptor) or task['kind']!='FORWARD':
                        db.execute("UPDATE tasks SET status='COMPLETE',finished_at=? WHERE id=?",(now,job['task_id']))
                self._event(db,job['run_id'],'PHASE_COMPLETED' if ok else 'PHASE_FAILED',request_id=job['id'],action=job['action'],task_id=job['task_id'],http_status=status)
            self._register_evidence(db,job,evidence)
            db.execute('UPDATE jobs SET worker_exit=?,cleanup=? WHERE id=?',(worker.poll() if worker else None,encode(cleanup),job['id']))

    def _run(self,job):
        worker=None;result=None;error=None;cleanup={};evidence=None
        try:
            self._resource_guard()
            path,digest=self._payload(job)
            remaining=job['deadline']-float(self.clock.time())
            if remaining<=0:raise Error('TASK_TIMEOUT_BEFORE_EXECUTION',409)
            self.active_deadline=float(self.clock.monotonic())+remaining
            worker=WorkerProcess(path,path.parent,self.worker_command,owner_fd=self.owner_fd).start()
            worker.arm_deadline(remaining, clock=self.clock.monotonic)
            self.active_worker=worker
            with self.condition:
                self.active_worker=worker
                with self.transaction() as db:db.execute('UPDATE jobs SET pid=?,job_file_sha256=? WHERE id=?',(worker.pid,digest,job['id']))
                self.condition.notify_all()
            while worker.poll() is None:
                with self.condition:
                    wall=float(self.clock.time())
                    if wall<self.last_wall:error='CLOCK_ROLLBACK'
                    elif self.closing:error='SHUTDOWN_INTERRUPTED'
                    elif float(self.clock.monotonic())>=self.active_deadline or wall>=job['deadline']:error='TASK_TIMEOUT_ACTIVE'
                    else:
                        with self.connection() as db:
                            cancel=db.execute("SELECT 1 FROM jobs WHERE run_id=? AND action='cancel' AND state='QUEUED'",(job['run_id'],)).fetchone()
                        if cancel and job['action']!='cancel':error='CANCELLED_ACTIVE_PHASE'
                    if worker.failed:error=worker.failure or 'WORKER_DIAGNOSTIC_LIMIT'
                    if error:break
                    self.last_wall=max(self.last_wall,wall)
                    self.condition.wait(0.02)
            cleanup=worker.stop() if error else worker.finish()
            if not cleanup.get('quiescent'):
                self.fatal='OWNED_WORKER_NOT_QUIESCENT';self.accepting=False
                error=self.fatal
            if worker.failed:error=error or worker.failure
            if not error and (float(self.clock.monotonic())>self.active_deadline or float(self.clock.time())>job['deadline']):error='TASK_DEADLINE_EXCEEDED_DURING_COMPLETION'
            if not error:
                if worker.poll()!=0:raise Error('WORKER_EXIT_FAILED',409)
                raw=bounded_read(path.parent/'result.json',RESULT_LIMIT)
                result=json.loads(raw)
                if result.get('job_id')!=job['id'] or result.get('job_file_sha256')!=digest or result.get('codeidentity')!=self.codeidentity:
                    raise Error('WORKER_RESULT_BINDING',409)
                if type(result.get('http_status')) is not int or not 200<=result['http_status']<=599 or not isinstance(result.get('result'),dict) or not isinstance(result.get('run_patch'),dict):
                    raise Error('WORKER_RESULT_SCHEMA',409)
                self._resource_guard(inode_reserve=0)
        except BaseException as exc:
            error=exc.code if isinstance(exc,Error) else 'INTERNAL_COORDINATOR_ERROR'
            if worker:
                cleanup=worker.stop()
                if not cleanup.get('quiescent'):self.fatal='OWNED_WORKER_NOT_QUIESCENT';self.accepting=False
        try:
            failed=bool(error or (result is not None and result['http_status']>=400))
            if failed:
                # A failure receipt must observe the committed FAILED task, not
                # the earlier ACTIVE snapshot. Publish only after quiescence and
                # the outcome transaction. Holding this condition delays wait()
                # return until the receipt link is committed; no worker is live.
                with self.condition:
                    self._finish(job,worker,result,error,cleanup)
                    if cleanup.get('quiescent') or worker is None:
                        evidence=self._evidence(job)
                        with self.transaction() as db:self._register_evidence(db,job,evidence)
            else:
                if cleanup.get('quiescent') or worker is None:evidence=self._evidence(job,result,error)
                self._finish(job,worker,result,error,cleanup,evidence)
        except BaseException as exc:
            self.fatal='COORDINATOR_COMPLETION_UNCERTAIN';self.accepting=False
            # Do not rewrite a possibly committed core result as rollback/no movement.
            with self.transaction() as db:
                current=db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone()
                if current and current['state']=='RUNNING':self._interrupt(db,current,self.fatal,max(self.last_wall,float(self.clock.time())))
        finally:
            with self.condition:
                if cleanup.get('quiescent') or worker is None:self.active_worker=None
                self.condition.notify_all()

    def _loop(self):
        try:
            while True:
                self._pending_failure_evidence()
                with self.condition:
                    if self.closing or self.fatal:return
                    with self.transaction() as db:
                        now=self._now(db);job=self._select(db,now)
                        if job:
                            db.execute("UPDATE jobs SET state='RUNNING' WHERE id=?",(job['id'],))
                            db.execute("UPDATE runs SET stage='RUNNING' WHERE id=?",(job['run_id'],))
                            self._event(db,job['run_id'],'WORKER_CLAIMED',request_id=job['id'],task_id=job['task_id'])
                            self.active=job['id']
                    if not job:
                        self.active=None;self.condition.wait(0.05);continue
                self._run(job)
        except BaseException:
            with self.condition:
                self.fatal='SUPERVISOR_FAILED';self.accepting=False;self.condition.notify_all()

    def close(self):
        # Signal before taking any condition or SQLite lock.
        self.accepting=False;self.closing=True
        worker=self.active_worker
        if worker:worker.request_stop('SHUTDOWN_INTERRUPTED')
        with self.condition:
            if self.closed:return
            self.accepting=False;self.closing=True
            with self.transaction() as db:
                now=max(self.last_wall,float(self.clock.time()))
                for job in db.execute("SELECT * FROM jobs WHERE state='QUEUED'").fetchall():self._interrupt(db,job,'SHUTDOWN_BEFORE_EXECUTION',now)
            self.condition.notify_all()
        if self.thread:self.thread.join(timeout=8)
        if self.thread and self.thread.is_alive():
            raise Error('SHUTDOWN_NOT_QUIESCENT_OWNER_RETAINED',503)
        if self.active_worker:
            if not self.active_worker.stop().get('quiescent'):
                raise Error('SHUTDOWN_NOT_QUIESCENT_OWNER_RETAINED',503)
            self.active_worker=None
        with self.condition:
            self.closed=True
            if self.owner_fd is not None:os.close(self.owner_fd);self.owner_fd=None
            self.condition.notify_all()
