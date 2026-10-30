"""Single-owner bounded live broker. No network or key reads at import time."""
import argparse
import base64
import fcntl
import hashlib
import http.client
import json
import multiprocessing
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import struct
import threading
import time

MODEL='nvidia/Nemotron-3.5-Super-VL'
SOCKET=None
CAP=25_000_000_000
RESERVE=9_000_000
MAX_COMPLETION=2000
CAPS={'accessreplay':0,'third':0,'fourth':0}
ID=re.compile(r'[A-Za-z0-9_-]{1,64}\Z')

def peer_allowed(uid,app):
    return uid==os.getuid() and uid!=0 and app=='fourth'

def canonical(v):
    return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def sha(v): return hashlib.sha256(v).hexdigest()
def usd(n): return f'{n//1_000_000_000}.{n%1_000_000_000:09d}'
def strict(raw):
    def pairs(items):
        result={}
        for k,v in items:
            if k in result: raise ValueError('DUPLICATE_JSON_KEY')
            result[k]=v
        return result
    def invalid(v): raise ValueError('NONFINITE_JSON')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)
def atomic(path,value):
    data=canonical(value)
    if len(data)>3_000_000: raise ValueError('LEDGER_LIMIT')
    tmp=path.with_suffix('.tmp')
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as f: f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
        d=os.open(path.parent,os.O_DIRECTORY)
        try: os.fsync(d)
        finally: os.close(d)
    finally:
        if tmp.exists(): tmp.unlink()

def transport(body,deadline,key):
    """One TLS request, no proxy, redirects or retry; watchdog closes socket."""
    remaining=(deadline-time.monotonic_ns())/1e9
    if remaining<=0: raise TimeoutError()
    c=http.client.HTTPSConnection('api.tokenfactory.nebius.com',443,timeout=remaining,context=ssl.create_default_context())
    def abort():
        if c.sock:
            try: c.sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        c.close()
    timer=threading.Timer(remaining,abort);timer.daemon=True;timer.start()
    try:
        c.request('POST','/v1/chat/completions',body=body,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','Connection':'close'})
        response=c.getresponse();raw=response.read(60001)
        if len(raw)>60000 or time.monotonic_ns()>=deadline: raise ValueError('UPSTREAM_LIMIT')
        return response.status,raw
    finally: timer.cancel();c.close()

def bounded_transport(body,deadline,key):
    # Forked one-shot worker bounds DNS, TLS and reads by one original deadline.
    context=multiprocessing.get_context('fork')
    receiver,sender=context.Pipe(duplex=False)
    def worker():
        receiver.close()
        try:
            status,raw=transport(body,deadline,key)
            sender.send_bytes(struct.pack('!I',status)+raw)
        except BaseException:
            pass
        finally: sender.close()
    process=context.Process(target=worker,daemon=True)
    process.start();sender.close()
    try:
        remaining=(deadline-time.monotonic_ns())/1e9
        if remaining<=0 or not receiver.poll(remaining): raise TimeoutError()
        data=receiver.recv_bytes(60004)
        if len(data)<4 or time.monotonic_ns()>=deadline: raise TimeoutError()
        return struct.unpack('!I',data[:4])[0],data[4:]
    finally:
        receiver.close()
        if process.is_alive(): process.terminate()
        process.join(timeout=0.2)
        if process.is_alive(): process.kill();process.join(timeout=0.2)

class Broker:
    def __init__(self,path,send,secret=None):
        self.path=Path(path)
        if not self.path.is_file(): raise FileNotFoundError('EXISTING_LEDGER_REQUIRED')
        self.send=send;self.secret=secret;self.failed=False
        self.lock=open(self.path.parent/'broker.lock','a+b');os.chmod(self.lock.name,0o600)
        fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.ledger=strict(self.path.read_bytes()) # Existing owner ledger is mandatory; never initialize/reset.
        validate_ledger(self.ledger)
        if any(v['state']=='pending' for v in self.ledger['requests'].values()):
            self.ledger['halted']=True
        self.save()
    def save(self):
        try: atomic(self.path,self.ledger)
        except Exception: self.failed=True;raise
    def validate(self,q):
        if type(q) is not dict or set(q)!={'v','op','app','run_id','request_id','deadline_monotonic_ns','messages'}: raise ValueError('REQUEST_SCHEMA')
        if type(q['v']) is not int or q['v']!=1 or q['op']!='chat' or q['app']!='fourth': raise ValueError('REQUEST_SCHEMA')
        if any(type(q[k]) is not str or not ID.fullmatch(q[k]) for k in ('run_id','request_id')): raise ValueError('REQUEST_ID')
        if type(q['deadline_monotonic_ns']) is not int: raise ValueError('DEADLINE')
        m=q['messages']
        if type(m) is not list or not 1<=len(m)<=8: raise ValueError('MESSAGES')
        for x in m:
            if type(x) is not dict or set(x)!={'role','content'} or x['role'] not in ('system','user','assistant') or type(x['content']) is not str: raise ValueError('MESSAGES')
    def budget_snapshot(self,stage):
        calls=self.ledger['counts']['fourth'];limit=CAPS['fourth']
        committed=self.ledger['spent']+self.ledger['reserved']
        return {'stage':stage,'calls':calls,'call_cap':limit,'committed_nusd':committed,'cap_nusd':CAP,
                'alert_80':calls*5>=limit*4 or committed*5>=CAP*4}
    def handle(self,raw):
        if len(raw)>11000: raise ValueError('FRAME_LIMIT')
        q=strict(raw);self.validate(q);start=time.monotonic_ns();h=sha(raw)
        receipt={'schema':'nebius-shared-broker-receipt/v1','phase_id':'live-nvidia-20261029','app':q['app'],'run_id':q['run_id'],'request_id':q['request_id'],'model_requested':MODEL,'model_returned':None,'request_sha256':h,'provider_request_sha256':None,'response_sha256':None,'dispatched':False,'http_status':None,'usage_status':'not_dispatched','usage':None,'latency_ms':0,'reserved_usd':usd(0),'cost_usd':None,'halted':False}
        receipt['budget_snapshot']=self.budget_snapshot('admission')
        answer={'v':1,'request_id':q['request_id'],'status':'rejected','error_code':None,'provider':None,'receipt':receipt}
        def reject(code):
            answer['error_code']=code;receipt['halted']=self.failed or self.ledger['halted'];return answer
        ident=q['app']+':'+q['request_id'];run=q['app']+':'+q['run_id']
        old=self.ledger['requests'].get(ident)
        if old:
            if old['hash']!=h: return reject('REQUEST_ID_CONFLICT')
            if old['state']=='terminal': return old['answer']
            return reject('PENDING_UNKNOWN')
        if self.failed or self.ledger['halted']: return reject('BROKER_HALTED')
        deadline=q['deadline_monotonic_ns'];first=self.ledger['runs'].get(run)
        if first is None: first={'first_ns':start,'deadline_ns':min(deadline,start+180_000_000_000)}
        if deadline>first['deadline_ns']: return reject('DEADLINE_EXTENSION')
        if deadline<=start: return reject('DEADLINE_EXPIRED')
        first['deadline_ns']=deadline
        if self.ledger['counts'][q['app']]>=CAPS[q['app']]: return reject('CALL_CAP')
        if self.ledger['spent']+self.ledger['reserved']+RESERVE>CAP: return reject('BUDGET_CAP')
        upstream={'model':MODEL,'messages':q['messages'],'max_completion_tokens':MAX_COMPLETION,'n':1,'stream':False}
        body=canonical(upstream)
        if self.secret and self.secret.encode() in raw: return reject('SECRET_INPUT')
        receipt['provider_request_sha256']=sha(body)
        self.ledger['runs'][run]=first;self.ledger['counts'][q['app']]+=1;self.ledger['reserved']+=RESERVE
        entry={'state':'pending','hash':h,'request':q,'request_raw':raw.decode('utf-8'),'provider_request':strict(body),'answer':None}
        self.ledger['requests'][ident]=entry
        self.save() # Durable reservation MUST precede the first possible request byte.
        receipt['budget_snapshot']=self.budget_snapshot('reserved')
        receipt['dispatched']=True;receipt['usage_status']='unknown';receipt['reserved_usd']=usd(RESERVE)
        try:
            status,returned=self.send(body,min(deadline,start+60_000_000_000))
            receipt['http_status']=status
            if len(returned)>60000: raise ValueError('UPSTREAM_LIMIT')
            entry['provider_response_raw_sha256']=sha(returned)
            entry['provider_response_raw_size']=len(returned)
            entry['provider_response_redacted']=bool(self.secret and self.secret.encode() in returned)
            if entry['provider_response_redacted']: raise ValueError('SECRET_RESPONSE')
            entry['provider_response_base64']=base64.b64encode(returned).decode('ascii')
            entry['provider_response_raw']=returned.decode('utf-8')
            payload=strict(returned)
            if type(payload) is not dict: raise ValueError('PROVIDER_SCHEMA')
            usage=payload.get('usage');names=('prompt_tokens','completion_tokens','total_tokens')
            if type(usage) is not dict or any(type(usage.get(k)) is not int or usage[k]<0 for k in names): raise ValueError('USAGE_UNKNOWN')
            if usage['total_tokens']!=usage['prompt_tokens']+usage['completion_tokens'] or usage['prompt_tokens']>12000 or usage['completion_tokens']>MAX_COMPLETION: raise ValueError('USAGE_LIMIT')
            cost=usage['prompt_tokens']*300+usage['completion_tokens']*900
            receipt.update(usage_status='authoritative',usage={k:usage[k] for k in names},model_returned=payload.get('model'),response_sha256=sha(canonical(payload)))
            answer['provider']=payload
            self.ledger['reserved']-=RESERVE;self.ledger['spent']+=cost
            receipt['cost_usd']=usd(cost)
            if payload.get('model')!=MODEL: self.ledger['halted']=True
            answer['status']='ok' if status==200 and payload.get('model')==MODEL else 'provider_error'
            answer['error_code']=None if answer['status']=='ok' else 'PROVIDER_ERROR'
            entry['provider_response_raw']=returned.decode('utf-8')
        except Exception:
            self.ledger['halted']=True;answer.update(status='unknown',error_code='USAGE_UNKNOWN')
        receipt['latency_ms']=(time.monotonic_ns()-start)/1e6
        receipt['halted']=self.ledger['halted']
        if len(canonical(answer))>65536:
            answer['provider']=None
            answer['status']='provider_error' if receipt['usage_status']=='authoritative' else 'unknown'
            answer['error_code']='RESPONSE_LIMIT'
            if receipt['usage_status']!='authoritative':
                self.ledger['halted']=True;receipt['halted']=True
        entry['state']='terminal';entry['answer']=answer
        self.save()
        return answer

def recv_frame(conn,deadline):
    def receive(n):
        out=bytearray()
        while n:
            remaining=(deadline-time.monotonic_ns())/1e9
            if remaining<=0: raise TimeoutError()
            conn.settimeout(remaining);part=conn.recv(n)
            if not part: raise ValueError('FRAME_TRUNCATED')
            out.extend(part);n-=len(part)
        return bytes(out)
    n=struct.unpack('!I',receive(4))[0]
    if not 0<n<=11000: raise ValueError('FRAME_LIMIT')
    return receive(n)

def no_symlinks(path):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts: raise ValueError('ABSOLUTE_PATH_REQUIRED')
    for part in (path,*path.parents):
        if part.is_symlink(): raise ValueError('SYMLINK_FORBIDDEN')
    return path

def private_directory(path,uid):
    path=no_symlinks(path);st=path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid!=uid or stat.S_IMODE(st.st_mode)!=0o700:
        raise ValueError('PRIVATE_OWNER_DIRECTORY_REQUIRED')
    return path

def private_file(path,uid):
    path=no_symlinks(path);st=path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=uid or stat.S_IMODE(st.st_mode)!=0o600:
        raise ValueError('PRIVATE_OWNER_FILE_REQUIRED')
    return path

def validate_ledger(v):
    if type(v) is not dict or v.get('v')!=1 or type(v.get('halted')) is not bool: raise ValueError('INVALID_LEDGER')
    for k in ('spent','reserved'):
        if type(v.get(k)) is not int or v[k]<0: raise ValueError('INVALID_LEDGER')
    for k in ('counts','runs','requests'):
        if type(v.get(k)) is not dict: raise ValueError('INVALID_LEDGER')
    for app in CAPS:
        if type(v['counts'].get(app)) is not int or v['counts'][app]<0: raise ValueError('INVALID_LEDGER')
    for entry in v['requests'].values():
        if type(entry) is not dict or entry.get('state') not in ('pending','terminal'): raise ValueError('INVALID_LEDGER')

def budget(value):
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,9})?',value): raise argparse.ArgumentTypeError('Use positive USD with at most nine decimals')
    whole,_,fraction=value.partition('.')
    n=int(whole)*1_000_000_000+int(fraction.ljust(9,'0'))
    if not 0<n<=25_000_000_000: raise argparse.ArgumentTypeError('Budget must be positive and at most 25 USD')
    return n

def positive(value):
    n=int(value)
    if n<=0: raise argparse.ArgumentTypeError('Must be positive')
    return n

def parser():
    p=argparse.ArgumentParser(description='Local owner-only Folder broker; existing durable ledger required')
    p.add_argument('--key-file',required=True)
    p.add_argument('--socket',required=True)
    p.add_argument('--ledger',required=True)
    p.add_argument('--budget-usd',required=True,type=budget)
    p.add_argument('--max-calls',required=True,type=positive)
    return p

def validate_paths(args,uid,repo):
    if uid==0: raise ValueError('NONROOT_OWNER_REQUIRED')
    repo=Path(repo).resolve()
    key=private_file(args.key_file,uid)
    if key==repo or repo in key.parents: raise ValueError('KEY_MUST_BE_OUTSIDE_REPOSITORY')
    path=no_symlinks(args.socket)
    if len(os.fsencode(path))>107: raise ValueError('SOCKET_PATH_LIMIT')
    private_directory(path.parent,uid)
    if path.exists():
        st=path.lstat()
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid!=uid or stat.S_IMODE(st.st_mode)!=0o600: raise ValueError('UNSAFE_SOCKET')
    ledger=private_file(args.ledger,uid);private_directory(ledger.parent,uid)
    if ledger.stat().st_size>3_000_000: raise ValueError('LEDGER_LIMIT')
    for name in ('broker.lock',ledger.with_suffix('.tmp').name):
        p=ledger.parent/name
        if p.exists() or p.is_symlink(): private_file(p,uid)
    if key==ledger or key==path or ledger==path: raise ValueError('PATH_COLLISION')
    return key,path,ledger

def repository_root():
    # Running staged or copied into the app: nearest Git root, else current tree.
    for parent in Path(__file__).resolve().parents:
        if (parent/'app'/'core.py').is_file() or (parent/'.git').exists() or (parent/'fourth'/'app'/'core.py').is_file(): return parent
    raise ValueError('REPOSITORY_ROOT_UNRESOLVED')

def main():
    global CAP,CAPS
    args=parser().parse_args();uid=os.getuid();os.umask(0o077)
    keypath,path,ledger=validate_paths(args,uid,repository_root())
    CAP=args.budget_usd;CAPS={'accessreplay':0,'third':0,'fourth':args.max_calls}
    fd=os.open(keypath,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd) as f:
        st=os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=uid or stat.S_IMODE(st.st_mode)!=0o600: raise SystemExit('Unsafe key permissions')
        key=f.read(8193).strip()
    if not key or len(key)>8192 or '\n' in key or '\r' in key: raise SystemExit('Invalid credential format')
    broker=Broker(ledger,lambda body,deadline:bounded_transport(body,deadline,key),key)
    if broker.ledger['halted']: raise SystemExit('Ledger halted; no automatic restart')
    if path.exists(): path.unlink()
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
        server.bind(str(path));os.chmod(path,0o600);server.listen(8)
        try:
            while not broker.failed and not broker.ledger['halted']:
                conn,_=server.accept()
                with conn:
                    try:
                        _,peer,_=struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                        if peer!=uid: continue
                        raw=recv_frame(conn,time.monotonic_ns()+2_000_000_000)
                        if not peer_allowed(peer,strict(raw).get('app')): continue
                        encoded=canonical(broker.handle(raw))
                        if len(encoded)>65536: raise ValueError('RESPONSE_LIMIT')
                        conn.settimeout(1);conn.sendall(struct.pack('!I',len(encoded))+encoded)
                    except Exception: pass
        finally: path.unlink(missing_ok=True)
if __name__=='__main__': main()
