"""Explicit new-owner-only ledger initialization. Never reset/import an account."""
import argparse
import os
from pathlib import Path
import broker


def initialize(path,uid=None):
    owner=os.getuid() if uid is None else uid
    if owner==0: raise ValueError('NONROOT_OWNER_REQUIRED')
    path=broker.no_symlinks(path)
    broker.private_directory(path.parent,owner)
    value={'v':1,'spent':0,'reserved':0,'halted':False,'counts':{'accessreplay':0,'third':0,'fourth':0},'runs':{},'requests':{}}
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as output:
        os.fchmod(output.fileno(),0o600)
        output.write(broker.canonical(value));output.flush();os.fsync(output.fileno())
    folder=os.open(path.parent,os.O_DIRECTORY)
    try:os.fsync(folder)
    finally:os.close(folder)
    return path


def main():
    p=argparse.ArgumentParser(description='NEW OWNER ONLY: create a never-before-existing zero ledger; not for existing-account resets')
    p.add_argument('--ledger',required=True)
    p.add_argument('--new-owner-no-prior-ledger',action='store_true',required=True)
    args=p.parse_args();initialize(args.ledger)

if __name__=='__main__':main()
