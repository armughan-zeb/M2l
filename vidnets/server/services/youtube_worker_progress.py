"""Relay bounded, credential-free work counters from the worker's file sink."""
import re
import threading
from pathlib import Path


class YouTubeWorkerProgress:
    def __init__(self, path, worker_id, emit=print, progress_callback=None):
        self.path = Path(path)
        self.worker_id = str(worker_id)
        self.emit = emit
        self.progress_callback = progress_callback
        self.offset = 0
        self.remainder = b''
        self.transfers = 0
        self.seen = set()
        self.lock = threading.Lock()

    def poll(self, final=False):
        # A file sink keeps provider stdout from filling a pipe. Only mirror
        # known work counters; URLs, local paths and credentials stay local.
        with self.lock:
            try:
                with self.path.open('rb') as source:
                    source.seek(self.offset)
                    chunk = source.read(65536)
                    self.offset += len(chunk)
            except OSError:
                return
            lines = (self.remainder + chunk).split(b'\n')
            self.remainder = lines.pop()[-8192:]
            if final and self.remainder:
                lines.append(self.remainder)
                self.remainder = b''
            for raw in lines:
                line = raw.decode('utf-8', errors='replace')
                match = re.search(r'search complete: (\d+) unique candidate', line)
                if match and int(match[1]) > 0:
                    self._send(f'phase=search candidates={int(match[1])}')
                elif re.search(r'source-quality range ready:', line):
                    self.transfers += 1
                    self._send(f'phase=transfer completed={self.transfers}')
                else:
                    match = re.search(r'downloads: (\d+)/(\d+) ready \((\d+)/(\d+) scenes attempted, (\d+) transfers\)', line)
                    if match and int(match[3]) > 0:
                        ready, requested, checked, assigned, transfers = map(int, match.groups())
                        self._send(f'phase=verification ready={ready} requested={requested} checked={checked} assigned={assigned} transfers={transfers}')
                    else:
                        match = re.search(r'\[yt-worker\] child complete: (\d+)/(\d+)', line)
                        if match:
                            self._send(f'phase=complete ready={int(match[1])} requested={int(match[2])}')

    def _send(self, counters):
        if counters not in self.seen:
            self.seen.add(counters)
            self.emit(f'[yt-worker-progress] worker={self.worker_id} {counters}', flush=True)
            if callable(self.progress_callback):
                values = dict(re.findall(r'(\w+)=(\w+)', counters))
                phase = values.get('phase')
                if phase == 'transfer':
                    message = f"Downloading YouTube footage ({values.get('completed', '0')} clips received)"
                elif phase in {'verification', 'complete'}:
                    message = f"Checking YouTube footage {values.get('ready', '0')}/{values.get('requested', '0')}"
                else:
                    message = f"Searching YouTube ({values.get('candidates', '0')} candidates found)"
                try:
                    self.progress_callback(.45, message)
                except Exception:
                    pass
