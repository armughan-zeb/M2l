"""Identify the Python sources present when this backend booted."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import time

_ROOT = Path(__file__).resolve().parents[1]
_LOCK = threading.Lock()
_CACHE = {'checked_at': 0.0, 'signature': None, 'fingerprint': None}


def _fingerprint():
    files = sorted([_ROOT / 'app.py', _ROOT / 'config.py', *(_ROOT / 'services').rglob('*.py')])
    signature = tuple((str(path.relative_to(_ROOT)), path.stat().st_mtime_ns, path.stat().st_size)
                      for path in files if path.is_file())
    if signature == _CACHE['signature']:
        return _CACHE['fingerprint']
    digest = hashlib.sha256()
    for path in files:
        if path.is_file():
            digest.update(str(path.relative_to(_ROOT)).replace('\\', '/').encode())
            digest.update(path.read_bytes())
    _CACHE.update(signature=signature, fingerprint=digest.hexdigest())
    return _CACHE['fingerprint']


_LOADED_FINGERPRINT = _fingerprint()
_LOADED_AT = datetime.now(timezone.utc).isoformat()
try:
    _RESOURCE_ROOT = Path(os.environ.get('VIDZEO_RESOURCE_ROOT') or _ROOT.parent)
    _RELEASE_BUILD_ID = str(json.loads((_RESOURCE_ROOT / 'public/update-build.json').read_text(encoding='utf-8-sig')).get('buildId') or '')
except (OSError, ValueError, TypeError):
    _RELEASE_BUILD_ID = ''


def runtime_build_info():
    with _LOCK:
        if time.monotonic() - _CACHE['checked_at'] > 5:
            current = _fingerprint()
            _CACHE['checked_at'] = time.monotonic()
        else:
            current = _CACHE['fingerprint']
    return {'loaded_at_utc': _LOADED_AT, 'release_build_id': _RELEASE_BUILD_ID,
            'source_fingerprint': _LOADED_FINGERPRINT,
            'current_source_fingerprint': current, 'restart_required': current != _LOADED_FINGERPRINT}
