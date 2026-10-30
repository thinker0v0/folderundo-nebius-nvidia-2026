"""Staged local launcher; existing Folder modules remain unchanged."""
import argparse
import os
from pathlib import Path
import stat
import re
import uuid


def parser():
    p=argparse.ArgumentParser(description='Owner-operated loopback Folder live launcher')
    p.add_argument('--broker-socket',required=True)
    p.add_argument('--storage-dir',required=True)
    p.add_argument('--port',required=True,type=int)
    p.add_argument('--database-name',default='journey.sqlite',help='Existing legacy database basename; current admitted store uses service.sqlite')
    p.add_argument('--bounded-live-runtime',action='store_true',help='Trusted operator opt-in; preserves native filesystem <=16 MiB and 8 MiB reserve guard')
    return p

def settings(args):
    if not 1<=args.port<=65535: raise ValueError('INVALID_PORT')
    paths=[Path(args.broker_socket),Path(args.storage_dir)]
    for p in paths:
        if not p.is_absolute() or '..' in p.parts or any(x.is_symlink() for x in (p,*p.parents)): raise ValueError('UNSAFE_PATH')
    sock,storage=paths
    st=sock.lstat();parent=sock.parent.lstat();directory=storage.lstat()
    if not stat.S_ISSOCK(st.st_mode) or stat.S_IMODE(st.st_mode)!=0o600 or st.st_uid!=os.getuid(): raise ValueError('UNSAFE_SOCKET')
    if not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode)!=0o700 or parent.st_uid!=os.getuid(): raise ValueError('UNSAFE_SOCKET_PARENT')
    if not stat.S_ISDIR(directory.st_mode) or stat.S_IMODE(directory.st_mode)!=0o700 or directory.st_uid!=os.getuid(): raise ValueError('UNSAFE_STORAGE')
    selected=select_database(storage,args.database_name)
    return {'database':selected,'workspace_base':storage/'workspaces','live_config':{'socket_path':str(sock)},'bounded_live_runtime':args.bounded_live_runtime}

def select_database(storage,name):
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.sqlite',name): raise ValueError('INVALID_DATABASE_NAME')
    storage=Path(storage);selected=storage/name
    if selected.is_symlink(): raise ValueError('UNSAFE_DATABASE')
    coordinator=storage/(name+'.coordinator-v1.sqlite')
    if coordinator.is_symlink(): raise ValueError('UNSAFE_DATABASE')
    other=list(storage.glob('*.sqlite.coordinator-v1.sqlite'))
    if not coordinator.exists() and any(p!=coordinator for p in other): raise ValueError('EXISTING_COORDINATOR_SELECT_EXPLICITLY')
    return selected

def fresh_bootstrap(storage):
    path=Path(storage)/('bootstrap-'+uuid.uuid4().hex)
    path.mkdir(mode=0o700,exist_ok=False)
    return path

def main():
    args=parser().parse_args();config=settings(args)
    from app.service import Service
    from app.server import Server
    os.umask(0o077)
    service=Service(**config)
    with Server(('127.0.0.1',args.port),service=service,bootstrap_directory=fresh_bootstrap(args.storage_dir)) as server:
        print(f'Owner bootstrap URL is in private file: {server.bootstrap_file}',flush=True)
        server.serve_forever()

if __name__=='__main__': main()
