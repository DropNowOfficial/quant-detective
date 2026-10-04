"""Durable local log acceptance is not delivery to a person."""
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import time
from .contracts import canonical

@dataclass(frozen=True)
class Receipt:
    accepted: bool
    delivered: bool|None = None
    read: bool|None = None
    destination: str = 'local_log'

class LocalLogSink:
    def __init__(self,path,*,max_bytes=1048576):
        self.path=Path(path); self.max_bytes=max_bytes
        if type(max_bytes) is not int or max_bytes<=0: raise ValueError('invalid rotation size')
    def send(self,event):
        if not isinstance(event.get('event_id'),str): raise ValueError('missing event id')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        lock=self.path.parent/('.'+self.path.name+'.lock')
        with lock.open('a') as guard:
            fcntl.flock(guard,fcntl.LOCK_EX)
            files=sorted(p for p in self.path.parent.glob(self.path.name+'.part-*') if '.corrupt-' not in p.name)
            if self.path.exists(): files.append(self.path)
            found=False
            for path in files:
                raw=path.read_bytes(); lines=raw.splitlines(keepends=True)
                valid=[]
                for line in lines:
                    try:
                        if not line.endswith(b'\n'): raise ValueError('incomplete final line')
                        item=json.loads(line); item['event_id']
                    except (ValueError,KeyError,TypeError) as e:
                        # Preserve original bytes and valid prefix, report corruption before accepting.
                        quarantine=path.with_name(path.name+'.corrupt-'+str(time.time_ns()))
                        os.replace(path,quarantine)
                        with path.open('wb') as out:
                            out.write(b''.join(valid)); out.flush(); os.fsync(out.fileno())
                        self._sync_dir()
                        raise ValueError('quarantined corrupt notification log') from e
                    valid.append(line)
                    found |= item['event_id']==event['event_id']
            if found:
                # A previous append may have failed its durability barrier.
                for path in files:
                    with path.open('rb') as durable: os.fsync(durable.fileno())
                self._sync_dir()
                return Receipt(True)
            if self.path.exists() and self.path.stat().st_size and self.path.stat().st_size+len(canonical(event).encode())+1>self.max_bytes:
                os.replace(self.path,self.path.with_name(self.path.name+'.part-'+str(time.time_ns())))
            with self.path.open('ab') as out:
                out.write((canonical(event)+'\n').encode()); out.flush(); os.fsync(out.fileno())
            self._sync_dir()
            return Receipt(True)
    def _sync_dir(self):
        fd=os.open(self.path.parent,os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
