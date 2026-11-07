"""Bounded, job-local reuse of passed reviews of identical pixels and briefs."""
import json
import os
import time
from pathlib import Path

VERSION = 1
MAX_ENTRIES = 2000
MAX_AGE_SECONDS = 7 * 86400
MAX_BYTES = 8 * 1024 * 1024


def load_pass_cache(path):
    try:
        path = Path(path)
        if path.stat().st_size > MAX_BYTES:
            return {}
        payload = json.loads(path.read_text(encoding='utf-8'))
        if payload.get('version') != VERSION or time.time() - float(payload['saved_at']) > MAX_AGE_SECONDS:
            return {}
        entries = payload.get('entries')
        if not isinstance(entries, dict):
            return {}
        return {key: value for key, value in list(entries.items())[-MAX_ENTRIES:]
                if isinstance(key, str) and len(key) == 64 and isinstance(value, dict)
                and value.get('status') == 'pass'}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def save_pass_cache(path, entries):
    """Cache I/O is optional and must never interrupt a render."""
    temporary = None
    try:
        path = Path(path)
        valid = {key: value for key, value in list(entries.items())[-MAX_ENTRIES:]
                 if isinstance(key, str) and len(key) == 64 and isinstance(value, dict)
                 and value.get('status') == 'pass'}
        content = json.dumps({'version': VERSION, 'saved_at': time.time(), 'entries': valid})
        if len(content.encode('utf-8')) > MAX_BYTES:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
        temporary.write_text(content, encoding='utf-8')
        os.replace(temporary, path)
    except (OSError, ValueError, TypeError):
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
