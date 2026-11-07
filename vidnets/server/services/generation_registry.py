"""Durable, account-scoped deduplication for explicit API request IDs."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

_GUARD = threading.RLock()
_SAFE_RESULT = {'success', 'status', 'video', 'video_url', 'video_path', 'duration',
                'job_id', 'attempt_id', 'name', 'content_title', 'manifest_path',
                'output_package_dir', 'checkpoint_dir', 'cost_receipt_path',
                'usage_ledger_path', 'error', 'retryable', 'stage', 'message'}


@contextmanager
def _file_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with _GUARD, path.open('a+b') as stream:
        if path.stat().st_size == 0:
            stream.write(b'0')
            stream.flush()
        deadline = time.monotonic() + 3
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError('This request is being updated; retry with the same request ID.')
                time.sleep(.025)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _process_identity():
    import psutil
    return {'pid': os.getpid(), 'created': psutil.Process().create_time()}


def _owner_alive(owner):
    try:
        import psutil
        return abs(psutil.Process(owner['pid']).create_time() - owner['created']) < .01
    except Exception:
        return False


def _fingerprint(payload):
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in {'request_id', 'execution_mode', 'response_mode'}
                    and not any(word in str(k).lower() for word in ('api_key', 'token', 'password', 'cookie'))}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    return hashlib.sha256(json.dumps(clean(payload), sort_keys=True,
                        ensure_ascii=False, default=str).encode()).hexdigest()


class GenerationRegistry:
    def __init__(self, directory):
        self.directory = Path(directory)

    def reserve(self, user_id, request_id, payload):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id):
            raise ValueError('request_id must contain 1–128 letters, digits, dots, colons, underscores or hyphens.')
        identity = hashlib.sha256((str(user_id) + '\0' + request_id).encode()).hexdigest()
        path = self.directory / (identity + '.json')
        fingerprint = _fingerprint(payload)
        with _file_lock(path.with_suffix('.lock')):
            entry = self._read(path)
            if entry and entry.get('fingerprint') != fingerprint:
                return {'action': 'conflict'}
            if entry.get('state') in {'completed', 'queued'}:
                return {'action': 'replay', 'entry': entry}
            if entry.get('state') == 'running' and _owner_alive(entry.get('owner') or {}):
                return {'action': 'busy', 'entry': entry}
            ticket = uuid.uuid4().hex
            updated = {'fingerprint': fingerprint, 'state': 'running', 'owner': _process_identity(),
                       'ticket': ticket, 'updated_at': time.time(),
                       'output_package_dir': entry.get('output_package_dir', '')}
            self._write(path, updated)
            return {'action': 'run', 'path': str(path), 'ticket': ticket,
                    'output_package_dir': updated['output_package_dir']}

    def set_package(self, reservation, directory):
        self._update(reservation, output_package_dir=str(directory))

    def finish(self, reservation, payload, status_code=200):
        queued_id = (payload.get('item') or {}).get('id')
        result = {key: value for key, value in payload.items() if key in _SAFE_RESULT}
        state = 'queued' if queued_id else ('completed' if payload.get('success') else 'failed')
        self._update(reservation, state=state, response=result, status_code=status_code,
                     queued_id=queued_id, output_package_dir=payload.get('output_package_dir')
                     or reservation.get('output_package_dir', ''))

    def _update(self, reservation, **updates):
        if not reservation or reservation.get('action') != 'run':
            return
        path = Path(reservation['path'])
        with _file_lock(path.with_suffix('.lock')):
            entry = self._read(path)
            if entry.get('ticket') == reservation.get('ticket'):
                # Keep the prepared package when a queued response has no path.
                if not updates.get('output_package_dir'):
                    updates.pop('output_package_dir', None)
                self._write(path, {**entry, **updates, 'updated_at': time.time()})

    @staticmethod
    def _read(path):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError('Invalid request record')
            return value
        except FileNotFoundError:
            return {}
        except (ValueError, OSError):
            # Corrupt deduplication data must not silently charge a second job.
            raise ValueError('Saved request record is unreadable; use a new request ID or restore the record.') from None

    @staticmethod
    def _write(path, value):
        temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
