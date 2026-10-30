"""Genuine process-kill probes; cases and raw failures retained in workspaces."""
import json,os,subprocess,sys,time,hashlib,uuid,selectors,base64,sqlite3,stat,errno,tempfile,gzip,zlib,re
from unittest.mock import patch
from app import core
from datetime import datetime,timezone
from pathlib import Path
from app.core import Store,FOLDERS,Refusal,inspect,parent,noreplace,digest
BOUNDARIES=('before_prepare','after_prepare','after_rename','after_fsync','after_verify','after_commit')
class ProcessFailure(AssertionError):
    def __init__(self,event):
        self.event=event
        super().__init__(f'Probe process failed; event {event["event_id"]}, returncode {event["returncode"]}, overflow={event["output_overflow"]}; retained in {event["workspace_id"]}/probe-events.jsonl')
def retain_failure(record,error):
    record.update(passed=False,error=repr(error))
    if isinstance(error,ProcessFailure):
        event=error.event
        record['failed_process_event_id']=event['event_id']
        if event['kind']=='kill':record.update(kill_event_id=event['event_id'],kill_pid=event['pid'],kill_returncode=event['returncode'],post_kill_snapshot=event['post_kill_snapshot'])
def setup(count, selected=None):
    s=Store.create(count);scan=s.scan()
    proposals=[dict(file_id=e['file_id'],destination_folder=FOLDERS[i%4],new_basename=e['relative_path'],reason='Synthetic deterministic probe',confidence=1) for i,e in enumerate(scan['files'][:selected])]
    plan=s.preview(proposals,scan['manifest_sha'])
    body={k:plan[k] for k in ('plan_sha','source_manifest_sha','policy_version','approval_nonce')};body['operation_ids']=[o['op_id'] for o in plan['operations']]
    approval=s.approve(plan['batch_id'],body)
    return s,plan['batch_id'],approval['approval_id']
def readonly_state(s,b):
    # mode=ro sees committed WAL rows; immutable=1 would incorrectly ignore WAL.
    db=sqlite3.connect((s.home/'journal.sqlite').as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        row=db.execute('SELECT * FROM batch WHERE id=?',(b,)).fetchone()
        if row is None:raise Refusal('UNKNOWN_BATCH')
        result=dict(row)
        result['operations']=[dict(r) for r in db.execute('SELECT * FROM op WHERE batch=? ORDER BY direction,seq,id',(b,))]
        return result
    finally:db.close()
def readonly_manifest(s):
    fd=os.open(s.home/'files',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:return s.manifest(fd)
    finally:os.close(fd)
def mapping(s):
    return [(e['relative_path'],e['file_id'],e['sha256'],e['size_bytes']) for e in readonly_manifest(s)]
def write_evidence(path,value):
    with path.open('x') as f:
        json.dump(value,f,sort_keys=True);f.flush();os.fsync(f.fileno())
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
SNAPSHOT_RAW_LIMIT=1024*1024
SNAPSHOT_ENCODED_LIMIT=SNAPSHOT_RAW_LIMIT+65536
SNAPSHOT_ENCODING='gzip-canonical-json-v1'
def snapshot_shape(value):
    if (type(value) is not dict or type(value.get('manifest')) is not list or len(value['manifest'])>100
            or type(value.get('journal')) is not dict or type(value['journal'].get('operations')) is not list
            or len(value['journal']['operations'])>200):raise Refusal('PROBE_SNAPSHOT_SCHEMA')
    def depth(v,n=0):
        if n>16:raise Refusal('PROBE_SNAPSHOT_DEPTH')
        if isinstance(v,dict):
            for item in v.values():depth(item,n+1)
        elif isinstance(v,list):
            for item in v:depth(item,n+1)
    depth(value)
def publish_snapshot(home,value):
    """First publication is lossless gzip; never rewrites existing evidence."""
    snapshot_shape(value)
    raw=core.canonical(value).encode('utf-8')
    if len(raw)>SNAPSHOT_RAW_LIMIT:raise Refusal('PROBE_SNAPSHOT_LIMIT')
    encoded=gzip.compress(raw,compresslevel=9,mtime=0)
    if len(encoded)>SNAPSHOT_ENCODED_LIMIT:raise Refusal('PROBE_SNAPSHOT_LIMIT')
    name='probe-snapshot-'+str(uuid.uuid4())+'.json.gz'
    with (home/name).open('xb') as f:f.write(encoded);f.flush();os.fsync(f.fileno())
    fd=os.open(home,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    sha=hashlib.sha256(encoded).hexdigest()
    return dict(path=name,encoding=SNAPSHOT_ENCODING,sha256=sha,compressed_sha256=sha,
                compressed_bytes=len(encoded),uncompressed_sha256=hashlib.sha256(raw).hexdigest(),uncompressed_bytes=len(raw))
def read_snapshot(s,description):
    """Verify bounded encoded bytes then bounded single-member decoded JSON.

    Legacy .json is read in place with its original SHA; no migration or rewrite.
    """
    try:
        name=description['path']
        if type(name) is not str or not re.fullmatch(r'probe-snapshot-[0-9a-f-]{36}\.json(?:\.gz)?',name):raise Refusal('PROBE_SNAPSHOT_PATH')
        compressed=name.endswith('.gz')
        if compressed:
            if description.get('encoding')!=SNAPSHOT_ENCODING:raise Refusal('PROBE_SNAPSHOT_ENCODING')
            for key,limit in [('compressed_bytes',SNAPSHOT_ENCODED_LIMIT),('uncompressed_bytes',SNAPSHOT_RAW_LIMIT)]:
                if type(description.get(key)) is not int or not 0<description[key]<=limit:raise Refusal('PROBE_SNAPSHOT_LIMIT')
        elif description.get('encoding') not in (None,'json'):raise Refusal('PROBE_SNAPSHOT_ENCODING')
        fd=os.open(s.home/name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as f:
            info=os.fstat(f.fileno());limit=SNAPSHOT_ENCODED_LIMIT if compressed else SNAPSHOT_RAW_LIMIT
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>limit:raise Refusal('PROBE_SNAPSHOT_LIMIT')
            encoded=f.read(limit+1)
        if len(encoded)>limit or hashlib.sha256(encoded).hexdigest()!=description['sha256']:raise Refusal('PROBE_SNAPSHOT_HASH')
        raw=encoded
        if compressed:
            if len(encoded)!=description['compressed_bytes'] or description['compressed_sha256']!=description['sha256']:raise Refusal('PROBE_SNAPSHOT_HASH')
            decoder=zlib.decompressobj(31)
            raw=decoder.decompress(encoded,SNAPSHOT_RAW_LIMIT+1)
            if len(raw)>SNAPSHOT_RAW_LIMIT or decoder.unconsumed_tail:raise Refusal('PROBE_SNAPSHOT_LIMIT')
            if not decoder.eof or decoder.unused_data:raise Refusal('PROBE_SNAPSHOT_GZIP')
            if len(raw)!=description['uncompressed_bytes'] or hashlib.sha256(raw).hexdigest()!=description['uncompressed_sha256']:raise Refusal('PROBE_SNAPSHOT_HASH')
        def pairs(items):
            obj={}
            for k,v in items:
                if k in obj:raise Refusal('PROBE_SNAPSHOT_DUPLICATE_KEY')
                obj[k]=v
            return obj
        def constant(_):raise Refusal('PROBE_SNAPSHOT_NONFINITE')
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=constant)
        snapshot_shape(value)
        if compressed and core.canonical(value).encode('utf-8')!=raw:raise Refusal('PROBE_SNAPSHOT_NONCANONICAL')
        if digest(value['manifest'])!=description['manifest_sha'] or digest(value['journal'])!=description['journal_sha']:raise Refusal('PROBE_SNAPSHOT_HASH')
        return value
    except (OSError,ValueError,TypeError,KeyError,RecursionError,zlib.error) as e:
        raise Refusal('PROBE_SNAPSHOT_INVALID:'+type(e).__name__) from e

def snapshot(s,b,phase,process=None):
    # No Store.status/locked/recover: parent inspection must not reconcile rows.
    before=readonly_state(s,b);manifest=readonly_manifest(s);after=readonly_state(s,b)
    if before!=after:raise Refusal('PROBE_SNAPSHOT_JOURNAL_CHANGED')
    value=dict(version=2,phase=phase,workspace_id=s.id,batch_id=b,
               captured_utc=datetime.now(timezone.utc).isoformat(),process=process,
               manifest=manifest,journal=before,journal_read_unchanged=True)
    description=publish_snapshot(s.home,value)
    description.update(manifest_sha=digest(manifest),journal_sha=digest(before),
                       state=before['state'],mode=before['mode'],captured_utc=value['captured_utc'])
    if phase=='POST_KILL':
        description['op_state_vector']=[{k:o[k] for k in ('id','seq','direction','state')} for o in before['operations']]
    return description
def boundary_check(s,description,boundary,seq):
    value=read_snapshot(s,description);row=value['journal'];direction=row['mode']
    ops=[o for o in row['operations'] if o['direction']==direction]
    done='COMMITTED' if direction=='FORWARD' else 'UNDONE'
    if boundary=='before_batch_marker':
        return dict(passed=bool(ops) and all(o['state']==done for o in ops),expected_state=done,observed_states=[o['state'] for o in ops])
    selected=[o for o in ops if o['seq']==seq]
    if len(selected)!=1:return dict(passed=False,reason='Missing or duplicate selected operation')
    op=selected[0];prepared='PREPARED' if direction=='FORWARD' else 'UNDO_PREPARED'
    wanted='PENDING' if boundary=='before_prepare' else (done if boundary=='after_commit' else prepared)
    moved=boundary in ('after_rename','after_fsync','after_verify','after_commit')
    expected_path=op['to_path'] if moved else op['from_path'];absent_path=op['from_path'] if moved else op['to_path']
    manifest={e['relative_path']:e for e in value['manifest']};original=json.loads(op['expected']);actual=manifest.get(expected_path)
    identity_ok=actual is not None and all(actual.get(k)==v for k,v in original.items() if k!='relative_path')
    return dict(passed=op['state']==wanted and identity_ok and absent_path not in manifest,
                expected_state=wanted,observed_state=op['state'],expected_path=expected_path,absent_path=absent_path,identity_matches=identity_ok)
OUTPUT_LIMIT=1024*1024
def source_binding():
    root=Path(__file__).resolve().parents[1]
    hashes={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ('app/core.py','app/cli.py','tools/probes.py')}
    return dict(code_sha=digest(hashes),source_sha256=hashes)
def persist_event(s,event):
    path=s.home/'probe-events.jsonl'
    with path.open('a') as f:
        f.write(json.dumps(event)+'\n');f.flush();os.fsync(f.fileno())
    fd=os.open(s.home,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
def observed_process(s,b,args,env,kind,boundary=None,seq=None):
    started=datetime.now(timezone.utc).isoformat();clock=time.monotonic()
    child=subprocess.Popen(args,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    common=dict(event_id=str(uuid.uuid4()),workspace_id=s.id,batch_id=b,pid=child.pid,args=args,kind=kind,
                configured_boundary=boundary,seq=seq,started_utc=started,**source_binding())
    persist_event(s,dict(common,phase='STARTED'))
    data={'stdout':bytearray(),'stderr':bytearray()};sizes=dict(stdout=0,stderr=0)
    hashes={k:hashlib.sha256() for k in data};overflow=False;retained=0
    with selectors.DefaultSelector() as selector:
        selector.register(child.stdout,selectors.EVENT_READ,'stdout');selector.register(child.stderr,selectors.EVENT_READ,'stderr')
        while selector.get_map():
            for key,_ in selector.select():
                chunk=os.read(key.fileobj.fileno(),65536)
                if not chunk:selector.unregister(key.fileobj);key.fileobj.close();continue
                name=key.data;sizes[name]+=len(chunk);hashes[name].update(chunk)
                keep=chunk[:max(0,OUTPUT_LIMIT-retained)];data[name].extend(keep);retained+=len(keep)
                if len(keep)!=len(chunk) and not overflow:
                    overflow=True;child.kill()
    child.wait()
    post_kill=None;snapshot_error=None
    if kind=='kill':
        try:
            post_kill=snapshot(s,b,'POST_KILL',{'pid':child.pid,'returncode':child.returncode,'event_id':common['event_id']})
            post_kill['boundary_check']=boundary_check(s,post_kill,boundary,seq)
        except Exception as e:snapshot_error=repr(e)
    stdout=bytes(data['stdout']);stderr=bytes(data['stderr']);parse_error=None;result=None
    if kind=='request' and child.returncode==0 and not overflow:
        try:
            result=json.loads(stdout)
            if not isinstance(result,dict) or not all(isinstance(result.get(k),str) for k in ('state','mode')):raise ValueError('Missing state/mode')
        except (ValueError,TypeError) as e:parse_error=repr(e)
    success=not overflow and snapshot_error is None and (child.returncode==-9 and post_kill['boundary_check']['passed'] if kind=='kill' else child.returncode==0 and parse_error is None)
    event=dict(common,phase='FINISHED',ended_utc=datetime.now(timezone.utc).isoformat(),elapsed_seconds=time.monotonic()-clock,
               returncode=child.returncode,passed=success,post_kill_snapshot=post_kill,snapshot_error=snapshot_error,output_limit_bytes=OUTPUT_LIMIT,output_overflow=overflow,
               output_observed_bytes=sizes,stdout_sha256=hashes['stdout'].hexdigest(),stderr_sha256=hashes['stderr'].hexdigest(),
               stderr_base64=base64.b64encode(stderr).decode(),stderr_retained_bytes=len(stderr),parse_error=parse_error)
    if result is not None:
        event.update(state=result.get('state'),mode=result.get('mode'),after_sha=result.get('after_sha'),before_sha=result.get('before_sha'))
    if not success:
        event.update(stdout_base64=base64.b64encode(stdout).decode(),stdout_retained_bytes=len(stdout),
                     raw_retention='Complete stdout/stderr within combined 1MiB; overflow explicitly fails and terminates child')
    else:event['stdout_retention']='Digest and verified key fields only; not full response retention'
    persist_event(s,event)  # Durable outcome precedes any assertion/exception.
    if not success:raise ProcessFailure(event)
    return event
def crash(s,b,a,direction,boundary,seq):
    args=[sys.executable,'-m','app.cli', 'apply' if direction=='FORWARD' else 'undo','--workspace',s.id,'--batch',b]
    if direction=='FORWARD':args+=['--approval-id',a]
    else:args+=['--expected-after-sha',readonly_state(s,b)['after_sha']]
    env=dict(os.environ,FOLDERUNDO_CRASH=f'{boundary}:{seq}')
    return observed_process(s,b,args,env,'kill',boundary,seq)
def fresh_request(s,b,command,a=None,expected_after_sha=None):
    args=[sys.executable,'-m','app.cli',command,'--workspace',s.id,'--batch',b]
    if a is not None: args+=['--approval-id',a]
    if expected_after_sha is not None: args+=['--expected-after-sha',expected_after_sha]
    env=dict(os.environ);env.pop('FOLDERUNDO_CRASH',None)
    event=observed_process(s,b,args,env,'request')
    return dict(event_id=event['event_id'],pid=event['pid'],command=command,args=args,returncode=event['returncode'],
                state=event['state'],mode=event['mode'],stdout_sha256=event['stdout_sha256'])
def finish(s,b,a):
    processes=[]
    for _ in range(3):processes.append(fresh_request(s,b,'recover'))
    if readonly_state(s,b)['mode']=='FORWARD':
        for _ in range(3):processes.append(fresh_request(s,b,'apply',a=a))
    for _ in range(3):
        row=readonly_state(s,b);processes.append(fresh_request(s,b,'undo',expected_after_sha=row['after_sha']))
    for _ in range(3):
        processes.append(fresh_request(s,b,'recover'))
        processes.append(fresh_request(s,b,'apply',a=a))
        processes.append(fresh_request(s,b,'undo'))
    return processes
def actual_exdev(s,source_fd):
    """Root-authorized tiny scratch on existing /dev/shm; never mount/chown."""
    facts={'status':'UNPROVEN','source_name':'document-000.txt','target_name':'cross-device.txt',
           'source_device':os.fstat(source_fd).st_dev,'expected_errno':errno.EXDEV}
    scratch=None;target_fd=None
    try:
        scratch=Path(tempfile.mkdtemp(prefix='folderundo-exdev-',dir='/dev/shm'))
        facts['scratch_path']=str(scratch)
        target_fd=os.open(scratch,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        facts['destination_device']=os.fstat(target_fd).st_dev
        facts['source_before']=inspect(source_fd,facts['source_name'])
        facts['destination_entries_before']=sorted(os.listdir(target_fd))
        if facts['destination_device']!=facts['source_device']:
            try:noreplace(source_fd,facts['source_name'],target_fd,facts['target_name'])
            except Refusal as e:facts['observed_refusal']=str(e)
            facts['source_after']=inspect(source_fd,facts['source_name'])
            facts['destination_entries_after']=sorted(os.listdir(target_fd))
            facts['passed']=(facts.get('observed_refusal')==f'NO_GO_RENAMEAT2_{errno.EXDEV}'
                             and facts['source_before']==facts['source_after']
                             and facts['destination_entries_before']==facts['destination_entries_after']==[])
            facts['status']='PASS' if facts['passed'] else 'FAIL'
        else:facts['reason']='Existing /dev/shm is on the same device; real EXDEV remains unproven.'
    except (OSError,Refusal) as e:facts['reason']=repr(e)
    finally:
        # Retain terminal proof before any cleanup; unexpected target content is preserved.
        write_evidence(s.home/'primitive-exdev-proof.json',facts)
        if target_fd is not None:os.close(target_fd)
        if scratch is not None:
            try:
                if not any(scratch.iterdir()):scratch.rmdir();facts['known_empty_scratch_removed']=True
                else:facts['known_empty_scratch_removed']=False
            except OSError:facts['known_empty_scratch_removed']=False
    return facts
def primitive():
    resource_admission()
    s=Store.create(2);success={};details={}
    info=s.home.stat();success['private_0700']=stat.S_IMODE(info.st_mode)==0o700
    success['owned_by_app_uid']=info.st_uid==os.getuid()
    os.chmod(s.home,0o755)
    try:
        try:
            with s.locked():pass
        except Refusal as e:success['unsafe_mode_refused']=str(e).startswith('EXECUTION_NO_GO')
    finally:os.chmod(s.home,0o700)
    with patch.object(core.os,'getuid',return_value=info.st_uid+1):
        try:
            with s.locked():pass
        except Refusal as e:success['owner_mismatch_refused']=str(e).startswith('EXECUTION_NO_GO')
    details['owner_mismatch']='Injected reported app UID; no chown, extra UID or cross-user process created.'
    with s.locked() as (fd,db):
        before=s.manifest(fd)
        try:noreplace(fd,'document-000.txt',fd,'document-001.txt')
        except Refusal as e:success['eexist']=str(e)=='CONFLICT_DESTINATION'
        success['preserved']=s.manifest(fd)==before
        os.fsync(fd);success['dir_fsync']=True
        with parent(fd,'work/probe.txt') as (d,n):
            success['same_device']=os.fstat(fd).st_dev==os.fstat(d).st_dev
            noreplace(fd,'document-000.txt',d,n);noreplace(d,n,fd,'document-000.txt')
        # Deterministic wrapper negatives, explicitly not a real cross-mount probe.
        class MissingSymbol:pass
        class Failure:
            def __init__(self,code):self.code=code
            def renameat2(self,*args):core.ctypes.set_errno(self.code);return -1
        for name,library,expected in [('unsupported_symbol',MissingSymbol(),'NO_GO_RENAMEAT2'),
                                      ('unsupported_syscall',Failure(errno.ENOSYS),f'NO_GO_RENAMEAT2_{errno.ENOSYS}'),
                                      ('exdev',Failure(errno.EXDEV),f'NO_GO_RENAMEAT2_{errno.EXDEV}')]:
            check=s.manifest(fd)
            with patch.object(core.ctypes,'CDLL',return_value=library):
                try:noreplace(fd,'document-000.txt',fd,'work/negative.txt')
                except Refusal as e:success[name+'_no_fallback']=str(e)==expected and s.manifest(fd)==check
            details[name]='Injected libc result; real synthetic files verified unchanged; no mounts or fallback.'
        details['actual_exdev']=actual_exdev(s,fd)
        success['actual_exdev_no_fallback']=details['actual_exdev'].get('passed') is True
        os.symlink('work',s.home/'files'/'unsafe')
        try:
            with parent(fd,'unsafe/probe.txt'):pass
        except OSError:success['symlink_refused']=True
    required={'private_0700','owned_by_app_uid','unsafe_mode_refused','owner_mismatch_refused','eexist','preserved',
              'dir_fsync','same_device','unsupported_symbol_no_fallback','unsupported_syscall_no_fallback','exdev_no_fallback','actual_exdev_no_fallback','symlink_refused'}
    result={'scope':'Local Linux filesystem plus labeled injected wrapper negatives; actual existing-/dev/shm EXDEV where available; not physical power loss',
            'workspace_id':s.id,'checks':success,'details':details,'passed':set(success)==required and all(success.values()),**source_binding()}
    write_evidence(s.home/'primitive-probe.json',result)
    return result
# Owner approval 2026-10-28: FolderUndo1.875GiB, full original matrix.
# Admission accounting only; case inventory, executor and recovery assertions unchanged.
ARTIFACT_CAP_BYTES=2013265920
HOST_FLOOR_BYTES=1073741824
CASE_RESERVE_BYTES=8*1024*1024
AGGREGATE_RESERVE_BYTES=149344160
SAFETY_RESERVE_BYTES=64*1024*1024
PACKAGING_RESERVE_BYTES=32*1024*1024
def resource_usage():
    root=Path(__file__).resolve().parents[1]
    artifacts=root/'artifacts'
    review=root.parent/'judging-20261028'
    def measure(paths):
        allocated=logical=0
        for p in paths:
            s=p.lstat();allocated+=s.st_blocks*512;logical+=s.st_size
        return dict(allocated_bytes=allocated,logical_bytes=logical,charged_bytes=max(allocated,logical))
    local=measure([artifacts,*artifacts.rglob('*')])
    external=measure(p for p in review.rglob('*') if 'folder' in str(p.relative_to(review)) or '/fourth/' in '/'+str(p.relative_to(review))+'/') if review.exists() else dict(allocated_bytes=0,logical_bytes=0,charged_bytes=0)
    fs=os.statvfs(artifacts)
    return dict(local=local,external=external,charged_bytes=local['charged_bytes']+external['charged_bytes'],free_bytes=fs.f_bavail*fs.f_frsize)
def resource_admission():
    usage=resource_usage()
    reserve=CASE_RESERVE_BYTES+AGGREGATE_RESERVE_BYTES+SAFETY_RESERVE_BYTES+PACKAGING_RESERVE_BYTES
    if usage['charged_bytes']+reserve>=ARTIFACT_CAP_BYTES or usage['free_bytes']-reserve<HOST_FLOOR_BYTES:
        raise Refusal('RESOURCE_ADMISSION_CLOSED')
    return dict(artifact_allocated_bytes=usage['local']['allocated_bytes'],free_bytes=usage['free_bytes'],
                resource_usage=usage,approved_cap_bytes=ARTIFACT_CAP_BYTES,reserved_bytes=reserve,
                packaging_reserve_bytes=PACKAGING_RESERVE_BYTES)
def case_inventory(scope):
    if scope not in ('early','full'):raise Refusal('INVALID_PROBE_SCOPE')
    count=4 if scope=='early' else 100
    cases=[dict(direction=d,boundary=k,seq=i) for d in ('FORWARD','UNDO') for k in BOUNDARIES for i in range(count)]
    cases += [dict(direction=d,boundary='before_batch_marker',seq=0) for d in ('FORWARD','UNDO')]
    cases += [dict(case=c) for c in ('partial_cancel','partial_undo_conflict')]
    return cases
def append_chronology(path,event):
    with path.open('a') as f:
        f.write(json.dumps(dict(event,utc=datetime.now(timezone.utc).isoformat()))+'\n');f.flush();os.fsync(f.fileno())
def register_inventory(progress,scope,code_sha,resources,resume=False):
    inventory_path=progress.with_suffix('.inventory.json');chronology=progress.with_suffix('.chronology.jsonl')
    expected=case_inventory(scope)
    if resume:
        if inventory_path.is_symlink() or chronology.is_symlink():raise Refusal('INVALID_PROGRESS_PATH')
        try:inventory=json.loads(inventory_path.read_text())
        except (OSError,ValueError) as e:raise Refusal('PREREGISTRATION_REQUIRED_NO_LEGACY_RETROCLAIM') from e
        unsigned=dict(inventory);claimed=unsigned.pop('inventory_sha256',None)
        if digest(unsigned)!=claimed or inventory['code_sha']!=code_sha or inventory['scope']!=scope or inventory['cases']!=expected:
            raise Refusal('INVENTORY_SOURCE_MISMATCH')
        if not chronology.is_file():raise Refusal('CHRONOLOGY_REQUIRED')
        append_chronology(chronology,dict(phase='RESUMED',inventory_sha256=claimed))
    else:
        inventory=dict(version=1,scope=scope,code_sha=code_sha,cases=expected,case_count=len(expected),resources=resources,
                       registered_utc=datetime.now(timezone.utc).isoformat(),method='SIGKILL; full before/post-kill/final snapshots; three fresh requests per recovery sequence')
        inventory['inventory_sha256']=digest(inventory);write_evidence(inventory_path,inventory)
        with chronology.open('x') as f:f.flush();os.fsync(f.fileno())
        append_chronology(chronology,dict(phase='PREREGISTERED',inventory_sha256=inventory['inventory_sha256'],case_count=len(expected)))
    return inventory_path,chronology,inventory['inventory_sha256']
def recovery(scope='early', resume=None):

    if scope not in ('early','full'): raise Refusal('INVALID_PROBE_SCOPE')
    count=4 if scope=='early' else 100; results=[]; started=time.monotonic()
    resources=resource_admission()
    root=Path(__file__).resolve().parents[1]
    code_sha=digest({str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [root/'app/core.py',root/'app/cli.py',root/'tools/probes.py']})
    progress=root/'artifacts'/('recovery-progress-'+str(uuid.uuid4())+'.jsonl')
    if resume is not None:
        candidate=Path(resume)
        if candidate.is_absolute() or len(candidate.parts)!=2 or candidate.parts[0]!='artifacts' or candidate.is_symlink(): raise Refusal('INVALID_PROGRESS_PATH')
        progress=root/candidate
        results=[json.loads(line) for line in progress.read_text().splitlines()]
        if any(r.get('code_sha')!=code_sha or r.get('scope')!=scope for r in results): raise Refusal('PROGRESS_SOURCE_MISMATCH')
    else:
        with progress.open('x') as f: f.flush();os.fsync(f.fileno())
        fd=os.open(progress.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    inventory_path,chronology,inventory_sha=register_inventory(progress,scope,code_sha,resources,resume is not None)
    def record_progress(record):
        record.update(code_sha=code_sha,scope=scope,inventory_sha256=inventory_sha,elapsed_seconds=time.monotonic()-started)
        with progress.open('a') as f:
            f.write(json.dumps(record)+'\n');f.flush();os.fsync(f.fileno())
        results.append(record)
        append_chronology(chronology,dict(phase='CASE_FINISHED',case_key=key(record),record_sha256=digest(record),passed=record['passed']))
    def key(record):
        return record.get('case') or (record.get('direction'),record.get('boundary'),record.get('seq'))

    cases=[(d,k,i) for d in ('FORWARD','UNDO') for k in BOUNDARIES for i in range(count)]
    cases += [(d,'before_batch_marker',0) for d in ('FORWARD','UNDO')]
    expected=set(cases)|{'partial_cancel','partial_undo_conflict'}
    completed={key(r) for r in results}
    if len(completed)!=len(results) or not completed<=expected: raise Refusal('INVALID_PROGRESS_CASES')
    def begin_case(case_key):
        try:
            if source_binding()['code_sha']!=code_sha:raise Refusal('PROBE_SOURCE_CHANGED')
            resource_admission()
        except Exception as e:
            append_chronology(chronology,dict(phase='ADMISSION_STOP',case_key=case_key,error=repr(e)))
            raise
        append_chronology(chronology,dict(phase='CASE_STARTED',case_key=case_key))
        try:return setup(count)
        except Exception as e:
            append_chronology(chronology,dict(phase='SETUP_FAILED',case_key=case_key,error=repr(e)))
            raise
    for direction,boundary,seq in cases:
        if (direction,boundary,seq) in completed: continue
        s,b,a=begin_case([direction,boundary,seq])
        record=dict(workspace_id=s.id,direction=direction,boundary=boundary,seq=seq,event_path=str((s.home/'probe-events.jsonl').relative_to(root)))
        try:
            before=mapping(s);record['before_snapshot']=snapshot(s,b,'BEFORE')
            if direction=='UNDO':s.apply(b,a)
            killed=crash(s,b,a,direction,boundary,seq);record.update(kill_event_id=killed['event_id'],kill_pid=killed['pid'],kill_returncode=killed['returncode'],post_kill_snapshot=killed['post_kill_snapshot']);record['fresh_processes']=finish(s,b,a)
            assert mapping(s)==before;assert readonly_state(s,b)['state']=='UNDONE';record['passed']=True
        except Exception as e:retain_failure(record,e)
        try:record['final_snapshot']=snapshot(s,b,'FINAL')
        except Exception as e:record.update(passed=False,final_snapshot_error=repr(e))
        record_progress(record)
    for case in ('partial_cancel','partial_undo_conflict'):
        if case in completed: continue
        s,b,a=begin_case(case);record=dict(case=case,workspace_id=s.id,event_path=str((s.home/'probe-events.jsonl').relative_to(root)))
        try:
            record['before_snapshot']=snapshot(s,b,'BEFORE');original=mapping(s)
            if case=='partial_cancel':
                before=mapping(s);killed=crash(s,b,a,'FORWARD','after_commit',0);record.update(kill_event_id=killed['event_id'],kill_pid=killed['pid'],kill_returncode=killed['returncode'],post_kill_snapshot=killed['post_kill_snapshot'])
                record['fresh_processes']=[fresh_request(s,b,'cancel')]
                for _ in range(3):
                    record['fresh_processes'] += [fresh_request(s,b,'recover'),fresh_request(s,b,'apply',a=a),fresh_request(s,b,'undo')]
                assert mapping(s)==before
            else:
                s.apply(b,a);killed=crash(s,b,a,'UNDO','after_commit',0);record.update(kill_event_id=killed['event_id'],kill_pid=killed['pid'],kill_returncode=killed['returncode'],post_kill_snapshot=killed['post_kill_snapshot'])
                row=readonly_state(s,b);pending=next(o for o in row['operations'] if o['direction']=='UNDO' and o['state']=='PENDING')
                path=s.home/'files'/pending['from_path'];path.write_text('Synthetic conflicting edit\n');before=mapping(s)
                record['fresh_processes']=[]
                for _ in range(3):
                    record['fresh_processes'] += [fresh_request(s,b,'recover'),fresh_request(s,b,'undo'),fresh_request(s,b,'apply',a=a)]
                changed_id=json.loads(pending['expected'])['file_id']
                expected={e[1]:e for e in original}
                expected[changed_id]=next(e for e in before if e[1]==changed_id)
                assert {e[1]:e for e in mapping(s)}==expected and readonly_state(s,b)['state']=='CONFLICT'
            record['passed']=True
        except Exception as e:retain_failure(record,e)
        try:record['final_snapshot']=snapshot(s,b,'FINAL')
        except Exception as e:record.update(passed=False,final_snapshot_error=repr(e))
        record_progress(record)
    assert len(results)==12*count+4
    return dict(inventory_path=str(inventory_path.relative_to(root)),inventory_sha256=inventory_sha,chronology_path=str(chronology.relative_to(root)),code_sha=code_sha,progress_path=str(progress.relative_to(root)),resources=resources,scope=scope,method='Real SIGKILL subprocesses, local filesystem; not physical power loss',count=len(results),passed=all(r['passed'] for r in results),elapsed_seconds=time.monotonic()-started,cases=results)
