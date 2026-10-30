"""Linux-only synthetic-workspace engine. No external path registration API."""
import contextlib, ctypes, errno, fcntl, hashlib, json, os, re, signal, sqlite3, stat, uuid
from pathlib import Path
BASE = Path(__file__).resolve().parents[1] / 'artifacts' / 'workspaces'
REGISTERED_DEV_ROOT = Path(__file__).resolve().parents[1] / 'fixtures' / 'classification'
REGISTERED_DEV_SPEC_SHA = '99a53393376a72b9f1628df7d29e4e8384ea8e5bf80de51871b9467bf622d9d7'
FOLDERS = ('work', 'receipt', 'reference', 'unsorted')
POLICY = 'synthetic-v1'
class Refusal(Exception): pass
def canonical(x): return json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False)
def digest(x): return hashlib.sha256(canonical(x).encode()).hexdigest()
def valid(path):
    if not isinstance(path, str) or len(path.split('/')) > 4: raise Refusal('INVALID_PATH')
    for p in path.split('/'):
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}', p): raise Refusal('INVALID_PATH')
    return path
def uid(value):
    if str(uuid.UUID(value)) != value: raise Refusal('INVALID_ID')
    return value
def killpoint(name, seq):
    if os.environ.get('FOLDERUNDO_CRASH') == f'{name}:{seq}': os.kill(os.getpid(), signal.SIGKILL)
def noreplace(a, src, b, dst):
    libc = ctypes.CDLL(None, use_errno=True)
    try: fn = libc.renameat2
    except AttributeError: raise Refusal('NO_GO_RENAMEAT2')
    if fn(a, os.fsencode(src), b, os.fsencode(dst), 1):
        e = ctypes.get_errno()
        raise Refusal('CONFLICT_DESTINATION' if e == errno.EEXIST else f'NO_GO_RENAMEAT2_{e}')
@contextlib.contextmanager
def parent(rootfd, path):
    parts = valid(path).split('/'); fd = os.dup(rootfd)
    try:
        for p in parts[:-1]:
            nxt = os.open(p, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = nxt
        yield fd, parts[-1]
    finally: os.close(fd)
def inspect(rootfd, path):
    try:
        with parent(rootfd, path) as (pfd, name):
            try: fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd)
            except FileNotFoundError: return None
            try:
                s = os.fstat(fd)
                if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1: raise Refusal('UNSAFE_FILE')
                if s.st_size > 65536: raise Refusal('FILE_LIMIT')
                data = os.read(fd, 65537)
                if len(data) != s.st_size: raise Refusal('CHANGED_FILE')
                return dict(relative_path=path, sha256=hashlib.sha256(data).hexdigest(), size_bytes=s.st_size,
                            device=s.st_dev, inode=s.st_ino, mode=s.st_mode, mtime_ns=s.st_mtime_ns,
                            link_count=s.st_nlink, file_id=str(uuid.uuid5(uuid.NAMESPACE_OID, f'{s.st_dev}:{s.st_ino}')))
            finally: os.close(fd)
    except FileNotFoundError:return None
def identity(entry): return tuple(entry[k] for k in ('sha256','size_bytes','device','inode','mode','mtime_ns','link_count')) if entry else None
def open_directory_chain(path, create=False):
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for segment in Path(path).parts[1:]:
            if create:
                try: os.mkdir(segment,mode=0o700,dir_fd=fd)
                except FileExistsError: pass
            nxt=os.open(segment,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=nxt
        return fd
    except BaseException:
        os.close(fd);raise
def _registered_bytes(rootfd,path,limit=65536):
    with parent(rootfd,path) as (pfd,name):
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=pfd)
        try:
            before=os.fstat(fd);mode=stat.S_IMODE(before.st_mode)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_uid!=os.getuid(): raise Refusal('UNSAFE_REGISTERED_SOURCE')
            if mode & 0o7133 or not mode & 0o400 or before.st_size>limit: raise Refusal('UNSAFE_REGISTERED_SOURCE')
            raw=os.read(fd,limit+1);after=os.fstat(fd)
            if len(raw)!=before.st_size or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_mode,before.st_nlink)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_mode,after.st_nlink): raise Refusal('CHANGED_REGISTERED_SOURCE')
            return raw
        finally:os.close(fd)
def _registered_dev_seed():
    fd=None
    try:
        fd=open_directory_chain(REGISTERED_DEV_ROOT)
        raw=_registered_bytes(fd,'spec.json')
        if hashlib.sha256(raw).hexdigest()!=REGISTERED_DEV_SPEC_SHA: raise Refusal('REGISTERED_SPEC_MISMATCH')
        spec=json.loads(raw);entries=[e for e in spec['entries'] if e['split']=='dev']
        if len(entries)!=20: raise Refusal('REGISTERED_DEV_COUNT')
        seed=[]
        for entry in entries:
            path=valid(entry['path']);parts=path.split('/')
            if len(parts)!=2 or parts[0]!='dev': raise Refusal('REGISTERED_DEV_PATH')
            content=_registered_bytes(fd,path)
            if type(entry['size_bytes']) is not int or len(content)!=entry['size_bytes'] or hashlib.sha256(content).hexdigest()!=entry['sha256']: raise Refusal('REGISTERED_SOURCE_MISMATCH')
            seed.append((parts[1],content))
        return seed
    except (OSError,ValueError,KeyError,TypeError) as e:raise Refusal('INVALID_REGISTERED_DEV') from e
    finally:
        if fd is not None:os.close(fd)
class Store:
    def __init__(self, workspace_id):
        self.id = uid(workspace_id); self.home = BASE / self.id
        if not self.home.is_dir(): raise Refusal('UNREGISTERED_WORKSPACE')
    @classmethod
    def create(cls, count=4):
        if not 1 <= count <= 100: raise Refusal('FILE_COUNT_LIMIT')
        seed=[(f'document-{i:03}.txt',f'Synthetic document {i//2}. Project illustration only.\n'.encode()) for i in range(count)]
        return cls._create_seed(seed)
    @classmethod
    def create_registered_dev(cls):
        return cls._create_seed(_registered_dev_seed())
    @classmethod
    def _create_seed(cls,seed):
        if not 1<=len(seed)<=100: raise Refusal('FILE_COUNT_LIMIT')
        names=set();total=0
        for filename,content in seed:
            valid(filename)
            if '/' in filename or Path(filename).suffix.lower() not in ('.txt','.md') or filename.casefold() in names: raise Refusal('INVALID_REGISTERED_BASENAME')
            if not isinstance(content,bytes) or len(content)>65536: raise Refusal('FILE_LIMIT')
            try:text=content.decode('utf-8')
            except UnicodeDecodeError as e:raise Refusal('INVALID_REGISTERED_UTF8') from e
            if '\x00' in text: raise Refusal('INVALID_REGISTERED_UTF8')
            names.add(filename.casefold());total+=len(content)
        if total>4*1024*1024: raise Refusal('SCAN_LIMIT')
        basefd=homefd=filesfd=None
        try:
            # Resolve every existing ancestor without following links before writes.
            basefd=open_directory_chain(BASE,create=True)
            baseinfo=os.fstat(basefd)
            if baseinfo.st_uid!=os.getuid() or stat.S_IMODE(baseinfo.st_mode)&0o022: raise Refusal('EXECUTION_NO_GO_BASE_PERMISSIONS')
            name=str(uuid.uuid4())
            os.mkdir(name,mode=0o700,dir_fd=basefd)
            homefd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=basefd)
            os.mkdir('files',mode=0o700,dir_fd=homefd)
            filesfd=os.open('files',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=homefd)
            for folder in FOLDERS: os.mkdir(folder,mode=0o700,dir_fd=filesfd)
            for filename,content in seed:
                fd=os.open(filename,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=filesfd)
                with os.fdopen(fd,'wb') as f: f.write(content)
            fd=os.open('registered.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=homefd)
            with os.fdopen(fd,'w') as f: f.write(canonical({'workspace_id':name,'synthetic':True}))
            # Linux procfd keeps SQLite creation anchored to the verified directory.
            db=sqlite3.connect(f'/proc/self/fd/{homefd}/journal.sqlite')
            try:
                db.executescript("""
                PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
                CREATE TABLE batch(id TEXT PRIMARY KEY, mode TEXT, state TEXT, plan TEXT, approval TEXT, before_sha TEXT, after_sha TEXT);
                CREATE TABLE op(id TEXT PRIMARY KEY, batch TEXT, seq INTEGER, direction TEXT, from_path TEXT, to_path TEXT, expected TEXT, state TEXT, parent_forward_op_id TEXT);
                """)
            finally: db.close()
        except OSError as e: raise Refusal('UNSAFE_WORKSPACE_ROOT') from e
        finally:
            for fd in (filesfd,homefd,basefd):
                if fd is not None: os.close(fd)
        store = cls(name)
        with store.locked() as (fd, db): store.write_once('baseline.json', store.manifest(fd))
        return store
    @contextlib.contextmanager
    def locked(self):
        hfd = open_directory_chain(self.home)
        fd = lock = db = None
        try:
            s=os.fstat(hfd)
            if s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700: raise Refusal('EXECUTION_NO_GO_PERMISSIONS')
            lock=os.open('lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600, dir_fd=hfd); fcntl.flock(lock, fcntl.LOCK_EX)
            for control in ('registered.json','journal.sqlite','journal.sqlite-wal','journal.sqlite-shm','lock'):
                try: info=os.stat(control,dir_fd=hfd,follow_symlinks=False)
                except FileNotFoundError:
                    if control in ('registered.json','journal.sqlite','lock'): raise Refusal('RECOVERY_REQUIRED_CONTROL')
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid(): raise Refusal('RECOVERY_REQUIRED_CONTROL')
            marker=json.loads((self.home/'registered.json').read_text())
            if marker != {'workspace_id':self.id,'synthetic':True}: raise Refusal('UNREGISTERED_WORKSPACE')
            fd=os.open('files', os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=hfd)
            db=sqlite3.connect(self.home/'journal.sqlite'); db.row_factory=sqlite3.Row
            db.execute('PRAGMA synchronous=FULL'); db.execute('PRAGMA journal_mode=WAL')
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok': raise Refusal('RECOVERY_REQUIRED')
            yield fd, db
        except (OSError, sqlite3.Error) as e: raise Refusal(f'RECOVERY_REQUIRED:{type(e).__name__}') from e
        finally:
            if db is not None: db.close()
            if fd is not None: os.close(fd)
            if lock is not None: os.close(lock)
            os.close(hfd)
    def write_once(self, name, value):
        hfd=open_directory_chain(self.home);temp=name+'.'+str(uuid.uuid4())+'.pending'
        try:
            fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=hfd)
            with os.fdopen(fd,'w') as f:f.write(canonical(value));f.flush();os.fsync(f.fileno())
            noreplace(hfd,temp,hfd,name);os.fsync(hfd)
        finally:os.close(hfd)
    def manifest(self, fd, occupied=None):
        out=[]; seen=set(); total=0
        def walk(dfd, prefix=''):
            nonlocal total
            for name in sorted(os.listdir(dfd)):
                path=valid(prefix+name); key=path.casefold()
                if key in seen: raise Refusal('CASEFOLD_COLLISION')
                seen.add(key); s=os.stat(name,dir_fd=dfd,follow_symlinks=False)
                if stat.S_ISDIR(s.st_mode):
                    child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=dfd)
                    try: walk(child,path+'/')
                    finally: os.close(child)
                else:
                    item=inspect(fd,path); out.append(item); total+=item['size_bytes']
                    if len(out)>100 or total>4*1024*1024: raise Refusal('SCAN_LIMIT')
        walk(fd)
        if occupied is not None: occupied.update(seen)
        return out
    def scan(self):
        with self.locked() as (fd,db):
            entries=self.manifest(fd); snippets=[]
            for e in entries:
                status='SKIPPED'; snippet=''
                if Path(e['relative_path']).suffix.lower() in ('.txt','.md'):
                    with parent(fd,e['relative_path']) as (p,n):
                        f=os.open(n,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=p)
                        try:
                            raw=os.read(f,65537)
                            try:
                                text=raw.decode('utf-8'); status='READY' if '\x00' not in text else 'SKIPPED'; snippet=text[:2048] if status=='READY' else ''
                            except UnicodeDecodeError: pass
                        finally: os.close(f)
                snippets.append(dict(**e,status=status,snippet=snippet))
            return dict(workspace_id=self.id,manifest_sha=digest(entries),policy_version=POLICY,files=snippets,provider='CLOSED')
    def preview(self, proposals, manifest_sha, policy_version=POLICY):
        with self.locked() as (fd,db):
            existing=set();entries=self.manifest(fd,existing)
            if digest(entries)!=manifest_sha or policy_version!=POLICY: raise Refusal('REAPPROVAL_REQUIRED')
            if db.execute("SELECT 1 FROM batch WHERE state NOT IN ('UNDONE','REJECTED')").fetchone(): raise Refusal('ACTIVE_BATCH')
            index={e['file_id']:e for e in entries}; ops=[]; ids=set(); destinations=set()
            for p in proposals:
                if set(p)!={'file_id','destination_folder','new_basename','reason','confidence'}: raise Refusal('INVALID_PROPOSAL')
                if p['file_id'] in ids or p['file_id'] not in index or p['destination_folder'] not in FOLDERS: raise Refusal('INVALID_PROPOSAL')
                if not isinstance(p['reason'],str) or len(p['reason'])>300 or type(p['confidence']) not in (int,float) or not 0<=p['confidence']<=1: raise Refusal('INVALID_PROPOSAL')
                valid(p['new_basename'])
                if '/' in p['new_basename']: raise Refusal('INVALID_BASENAME')
                e=index[p['file_id']]; dst=p['destination_folder']+'/'+p['new_basename']; src=e['relative_path']
                if dst.casefold() in destinations or (dst.casefold() in existing and dst!=src): raise Refusal('CONFLICT_DESTINATION')
                if Path(src).suffix.lower() not in ('.txt','.md'): raise Refusal('SKIPPED_FILE')
                with parent(fd,src) as (pd,n):
                    sf=os.open(n,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=pd)
                    try: content=os.read(sf,65537).decode('utf-8')
                    finally: os.close(sf)
                if '\x00' in content: raise Refusal('SKIPPED_FILE')
                ids.add(p['file_id']); destinations.add(dst.casefold())
                ops.append(dict(op_id=str(uuid.uuid4()),src=src,dst=dst,expected=e,proposal=p))
            if not 1<=len(ops)<=100: raise Refusal('INVALID_PROPOSAL_COUNT')
            batch=str(uuid.uuid4()); plan=dict(batch_id=batch,operations=ops,source_manifest_sha=manifest_sha,policy_version=POLICY,approval_nonce=str(uuid.uuid4()))
            plan['plan_sha']=digest(plan)
            db.execute('INSERT INTO batch VALUES (?,?,?,?,?,?,?)',(batch,'FORWARD','AWAITING_APPROVAL',canonical(plan),None,manifest_sha,None))
            for seq,o in enumerate(ops): db.execute('INSERT INTO op VALUES (?,?,?,?,?,?,?,?,?)',(o['op_id'],batch,seq,'FORWARD',o['src'],o['dst'],canonical(o['expected']),'PENDING',None))
            self.write_once(batch+'-before.json',entries); db.commit(); return plan
    def approve(self,batch,body):
        with self.locked() as (fd,db):
            row=self.batch(db,batch); plan=json.loads(row['plan'])
            wanted={k:plan[k] for k in ('plan_sha','source_manifest_sha','policy_version','approval_nonce')}; wanted['operation_ids']=[o['op_id'] for o in plan['operations']]
            if body!=wanted: raise Refusal('REAPPROVAL_REQUIRED')
            if row['approval']:
                old=json.loads(row['approval'])
                if old['body']!=body: raise Refusal('REAPPROVAL_REQUIRED')
                return old
            if row['state']!='AWAITING_APPROVAL' or digest(self.manifest(fd))!=plan['source_manifest_sha']: raise Refusal('REAPPROVAL_REQUIRED')
            approval=dict(approval_id=str(uuid.uuid4()),body=body)
            db.execute('UPDATE batch SET approval=?,state=? WHERE id=?',(canonical(approval),'APPROVED',batch)); db.commit(); return approval
    def batch(self,db,batch):
        uid(batch); row=db.execute('SELECT * FROM batch WHERE id=?',(batch,)).fetchone()
        if row is None: raise Refusal('UNKNOWN_BATCH')
        if row['mode'] not in ('FORWARD','UNDO'): raise Refusal('RECOVERY_REQUIRED')
        try:
            if row['state'] not in ('AWAITING_APPROVAL','APPROVED','APPLYING','APPLIED','PARTIALLY_APPLIED','UNDO_REQUESTED','UNDOING','UNDONE','CONFLICT','RECOVERY_REQUIRED','REJECTED'): raise ValueError('unknown state')
            for operation in db.execute('SELECT direction,state FROM op WHERE batch=?',(batch,)):
                if operation['direction'] not in ('FORWARD','UNDO') or operation['state'] not in ('PENDING','PREPARED','UNDO_PREPARED','COMMITTED','UNDONE','CANCELLED','CONFLICT'): raise ValueError('unknown operation')
            plan=json.loads(row['plan']); unsigned=dict(plan); claimed=unsigned.pop('plan_sha')
            if digest(unsigned)!=claimed or plan['batch_id']!=batch or plan['policy_version']!=POLICY or plan['source_manifest_sha']!=row['before_sha']: raise ValueError('plan')
            forward=db.execute("SELECT * FROM op WHERE batch=? AND direction='FORWARD' ORDER BY seq",(batch,)).fetchall()
            if len(forward)!=len(plan['operations']): raise ValueError('operation count')
            for i,(op,approved) in enumerate(zip(forward,plan['operations'])):
                if (op['id'],op['seq'],op['from_path'],op['to_path'],json.loads(op['expected']))!=(approved['op_id'],i,approved['src'],approved['dst'],approved['expected']): raise ValueError('operation')
            by_id={op['id']:op for op in forward}
            reverse=db.execute("SELECT * FROM op WHERE batch=? AND direction='UNDO' ORDER BY seq",(batch,)).fetchall()
            if row['mode']=='UNDO':
                committed=[op for op in reversed(forward) if op['state']=='COMMITTED']
                if len(reverse)!=len(committed): raise ValueError('reverse coverage')
                for seq,(undo,original) in enumerate(zip(reverse,committed)):
                    if undo['seq']!=seq or undo['parent_forward_op_id']!=original['id']: raise ValueError('reverse sequence')
            elif reverse: raise ValueError('unexpected reverse operations')
            if row['state']=='UNDONE' and row['after_sha']!=row['before_sha']: raise ValueError('restoration mismatch')
            for op in db.execute("SELECT * FROM op WHERE batch=? AND direction='UNDO'",(batch,)):
                f=by_id[op['parent_forward_op_id']]
                if (op['from_path'],op['to_path'],op['expected'])!=(f['to_path'],f['from_path'],f['expected']): raise ValueError('reverse operation')
            before_path=self.home/(batch+'-before.json')
            if before_path.is_symlink() or digest(json.loads(before_path.read_text()))!=row['before_sha']: raise ValueError('before evidence')
            if row['state'] in ('APPLIED','UNDONE'):
                after_path=self.home/(batch+('-after.json' if row['state']=='APPLIED' else '-undone.json'))
                if after_path.is_symlink() or digest(json.loads(after_path.read_text()))!=row['after_sha']: raise ValueError('terminal evidence')
            if row['approval']:
                approval=json.loads(row['approval']); uid(approval['approval_id'])
                wanted={k:plan[k] for k in ('plan_sha','source_manifest_sha','policy_version','approval_nonce')}
                wanted['operation_ids']=[o['op_id'] for o in plan['operations']]
                if approval['body']!=wanted: raise ValueError('approval')
            elif row['state'] not in ('AWAITING_APPROVAL','PARTIALLY_APPLIED','UNDO_REQUESTED','UNDOING','UNDONE'): raise ValueError('missing approval')
        except (ValueError,KeyError,TypeError,OSError): raise Refusal('RECOVERY_REQUIRED_JOURNAL')
        return row
    def result(self,db,batch,fd=None):
        row=dict(self.batch(db,batch));row['operations']=[dict(o) for o in db.execute('SELECT * FROM op WHERE batch=? ORDER BY direction,seq',(batch,))]
        if fd is not None:
            try:
                current=self.manifest(fd);expected=self.projection(db,batch)
                old={e['relative_path']:e for e in expected};now={e['relative_path']:e for e in current}
                row['conflicts']=[dict(path=path,expected=old.get(path),current=now.get(path),reason='CURRENT_PATH_DIFFERS') for path in sorted(old.keys()|now.keys()) if old.get(path)!=now.get(path)]
                if row['state'] in ('APPLIED','UNDONE'):
                    actual=digest(current);row['current_drift']=dict(detected=actual!=row['after_sha'],expected_sha=row['after_sha'],manifest_sha=actual)
            except (Refusal,OSError) as e:
                row['conflicts']=[dict(path=None,expected=None,current=None,reason=str(e))]
                row['current_drift']=dict(detected=True,expected_sha=row['after_sha'],manifest_sha=None,error=str(e))
        return row
    def terminal_check(self,fd,db,batch):
        # Compatibility entry point: immutable historical terminal state.
        return self.batch(db,batch)
    def status(self,batch):
        with self.locked() as (fd,db):return self.result(db,batch,fd)
    def reconcile(self,fd,db,batch):
        row=self.batch(db,batch); direction=row['mode']; done='COMMITTED' if direction=='FORWARD' else 'UNDONE';observed_conflict=False
        for o in db.execute('SELECT * FROM op WHERE batch=? AND direction=? ORDER BY seq',(batch,direction)).fetchall():
            if o['state']=='CANCELLED': continue
            a=inspect(fd,o['from_path']); b=inspect(fd,o['to_path']); expected=json.loads(o['expected'])
            if o['from_path']==o['to_path']:
                state=done if identity(a)==identity(expected) else 'CONFLICT'
            elif a is None and identity(b)==identity(expected):
                if o['state'] in ('PENDING','CONFLICT'): state='CONFLICT'
                else:
                    with parent(fd,o['from_path']) as (p,n),parent(fd,o['to_path']) as (q,m): os.fsync(p); os.fsync(q)
                    state=done
            elif identity(a)==identity(expected) and b is None:
                state='PENDING' if o['state'] in ('PENDING','PREPARED','UNDO_PREPARED') else 'CONFLICT'
            else: state='CONFLICT'
            if direction=='FORWARD' and o['state']=='COMMITTED' and state=='CONFLICT':
                observed_conflict=True  # Durable commitment still requires a reverse row.
            else:
                db.execute('UPDATE op SET state=? WHERE id=?',(state,o['id']));db.commit()
        states=[r[0] for r in db.execute('SELECT state FROM op WHERE batch=? AND direction=?',(batch,direction))]
        if observed_conflict:states.append('CONFLICT')
        if 'CONFLICT' in states: db.execute("UPDATE batch SET state='CONFLICT' WHERE id=?",(batch,)); db.commit()
        return states
    def recover(self,batch):
        with self.locked() as (fd,db):
            self.terminal_check(fd,db,batch)
            row=self.batch(db,batch)
            if row['state'] in ('APPLIED','UNDONE','AWAITING_APPROVAL','APPROVED'): return self.result(db,batch,fd)
            self.reconcile(fd,db,batch); return self.result(db,batch,fd)
    def projection(self,db,batch):
        expected=json.loads((self.home/(batch+'-before.json')).read_text())
        positions={entry['relative_path']:dict(entry) for entry in expected}
        for direction,state in (('FORWARD','COMMITTED'),('UNDO','UNDONE')):
            for op in db.execute('SELECT * FROM op WHERE batch=? AND direction=? AND state=? ORDER BY seq',(batch,direction,state)):
                entry=positions.pop(op['from_path']);entry['relative_path']=op['to_path'];positions[op['to_path']]=entry
        return sorted(positions.values(),key=lambda e:e['relative_path'])
    def projection_matches(self,fd,db,batch):
        return self.projection(db,batch)==sorted(self.manifest(fd),key=lambda e:e['relative_path'])
    def parent_available(self,fd,path):
        try:
            with parent(fd,path):return True
        except (OSError,Refusal):return False
    def execute(self,fd,db,batch):
        row=self.batch(db,batch); direction=row['mode']; done='COMMITTED' if direction=='FORWARD' else 'UNDONE'
        states=self.reconcile(fd,db,batch)
        if direction=='FORWARD':
            if 'CONFLICT' in states:return self.result(db,batch,fd)
            if not self.projection_matches(fd,db,batch):raise Refusal('REAPPROVAL_REQUIRED')
        occupied=set();self.manifest(fd,occupied)
        pending=db.execute("SELECT * FROM op WHERE batch=? AND direction=? AND state='PENDING'",(batch,direction)).fetchall()
        for o in pending:
            if (o['to_path'].casefold() in occupied and o['to_path']!=o['from_path']) or not self.parent_available(fd,o['from_path']) or not self.parent_available(fd,o['to_path']):
                if direction=='FORWARD':raise Refusal('CONFLICT_DESTINATION')
                db.execute("UPDATE op SET state='CONFLICT' WHERE id=?",(o['id'],));db.commit()
        db.execute('UPDATE batch SET state=? WHERE id=?',('APPLYING' if direction=='FORWARD' else 'UNDOING',batch)); db.commit()
        for o in db.execute("SELECT * FROM op WHERE batch=? AND direction=? AND state='PENDING' ORDER BY seq",(batch,direction)).fetchall():
            seq=o['seq']; killpoint('before_prepare',seq)
            expected=json.loads(o['expected'])
            if identity(inspect(fd,o['from_path']))!=identity(expected) or inspect(fd,o['to_path']) is not None:
                if direction=='FORWARD':raise Refusal('CONFLICT_PRECONDITION')
                db.execute("UPDATE op SET state='CONFLICT' WHERE id=?",(o['id'],));db.commit();continue
            db.execute('UPDATE op SET state=? WHERE id=?',('PREPARED' if direction=='FORWARD' else 'UNDO_PREPARED',o['id'])); db.commit(); killpoint('after_prepare',seq)
            with parent(fd,o['from_path']) as (a,src),parent(fd,o['to_path']) as (b,dst):
                if os.fstat(a).st_dev!=os.fstat(b).st_dev: raise Refusal('NO_GO_CROSS_DEVICE')
                noreplace(a,src,b,dst); killpoint('after_rename',seq)
                os.fsync(a); os.fsync(b); killpoint('after_fsync',seq)
            if identity(inspect(fd,o['to_path']))!=identity(expected):
                db.execute("UPDATE op SET state='CONFLICT' WHERE id=?",(o['id'],)); db.execute("UPDATE batch SET state='CONFLICT' WHERE id=?",(batch,)); db.commit(); return self.result(db,batch,fd)
            killpoint('after_verify',seq)
            db.execute('UPDATE op SET state=? WHERE id=?',(done,o['id'])); db.commit(); killpoint('after_commit',seq)
        killpoint('before_batch_marker',0)
        entries=self.manifest(fd); after=digest(entries)
        if direction=='UNDO' and (after!=row['before_sha'] or db.execute("SELECT 1 FROM op WHERE batch=? AND state='CONFLICT'",(batch,)).fetchone()):
            db.execute("UPDATE batch SET state='CONFLICT' WHERE id=?",(batch,));db.commit();return self.result(db,batch,fd)
        name=batch+('-after.json' if direction=='FORWARD' else '-undone.json')
        if not (self.home/name).exists(): self.write_once(name,entries)
        else:
            try:
                path=self.home/name
                if path.is_symlink() or json.loads(path.read_text())!=entries:raise Refusal('ERROR_EVIDENCE')
            except (OSError,ValueError,TypeError) as e:raise Refusal('ERROR_EVIDENCE') from e
        db.execute('UPDATE batch SET state=?,after_sha=? WHERE id=?',('APPLIED' if direction=='FORWARD' else 'UNDONE',after,batch)); db.commit()
        return self.result(db,batch,fd)
    def apply(self,batch,approval_id):
        with self.locked() as (fd,db):
            self.terminal_check(fd,db,batch)
            row=self.batch(db,batch)
            if not row['approval'] or json.loads(row['approval'])['approval_id']!=approval_id: raise Refusal('REAPPROVAL_REQUIRED')
            if row['mode']=='UNDO' or row['state'] in ('APPLIED','UNDONE','CONFLICT','PARTIALLY_APPLIED'): return self.result(db,batch,fd)
            if row['state']=='APPROVED' and digest(self.manifest(fd))!=row['before_sha']: raise Refusal('REAPPROVAL_REQUIRED')
            return self.execute(fd,db,batch)
    def undo(self,batch,expected_after_sha=None,cancel=False):
        with self.locked() as (fd,db):
            self.terminal_check(fd,db,batch)
            row=self.batch(db,batch)
            if row['state']=='UNDONE':return self.result(db,batch,fd)
            if row['mode']=='FORWARD':
                if row['state']=='APPLIED':
                    if expected_after_sha!=row['after_sha']: raise Refusal('REAPPROVAL_REQUIRED')
                elif not cancel: raise Refusal('CANCEL_REQUIRED')
                self.reconcile(fd,db,batch)
                db.execute("UPDATE op SET state='CANCELLED' WHERE batch=? AND direction='FORWARD' AND state='PENDING'",(batch,))
                db.execute("UPDATE batch SET state='PARTIALLY_APPLIED' WHERE id=?",(batch,)); db.commit()
                forward=db.execute("SELECT * FROM op WHERE batch=? AND direction='FORWARD' AND state='COMMITTED' ORDER BY seq DESC",(batch,)).fetchall()
                for seq,o in enumerate(forward): db.execute('INSERT INTO op VALUES (?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),batch,seq,'UNDO',o['to_path'],o['from_path'],o['expected'],'PENDING',o['id']))
                db.execute("UPDATE batch SET mode='UNDO',state='UNDO_REQUESTED' WHERE id=?",(batch,)); db.commit()
            return self.execute(fd,db,batch)
