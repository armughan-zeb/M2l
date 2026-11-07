"""Scoped localhost keys delegated by an authenticated Vidnets user."""
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import threading
import time
import requests
from .agent_bridge import atomic_json, _request_lock

PREFIX = 'vna_'
_LOCK = threading.RLock()


def credential_path():
    root = Path(os.environ.get('VIDZEO_SECURE_DATA_DIR') or
                Path(os.environ.get('VIDZEO_ROOT') or Path(__file__).resolve().parents[2]) / 'secure')
    return root / 'agent-access.dpapi'


def _read():
    from .youtube_session import _unprotect
    path = credential_path()
    if not path.is_file():
        raise PermissionError('Enable local agent access in Vidnets Settings first.')
    try:
        return json.loads(_unprotect(path.read_bytes()))
    except Exception:
        raise PermissionError('Local agent credentials could not be unlocked by this Windows user.') from None


def _write(value):
    from .youtube_session import _protect
    path = credential_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _protect(json.dumps(value).encode('utf-8'))
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with temporary.open('xb') as stream:
            os.chmod(temporary, 0o600)
            stream.write(data)
            stream.flush()
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def pair(access_token, user, device_context, refresh_token=''):
    owner = str(user.get('user_id') or user.get('id') or '')
    if not owner or user.get('access_state') != 'active':
        raise PermissionError('Sign in to an active Vidnets account before pairing an agent.')
    credential_path().parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, _request_lock(credential_path()):
        _write({'version': 1, 'key': PREFIX + secrets.token_urlsafe(40), 'owner_id': owner,
                'access_token': access_token, 'refresh_token': str(refresh_token or ''),
                'device_context': device_context, 'created_at': time.time()})
    register_installation()
    return status(owner)


def status(owner_id=None):
    try:
        value = _read()
        if owner_id is not None and value.get('owner_id') != owner_id:
            return {'enabled': False, 'provider': 'external_agent'}
        return {'enabled': True, 'credential_file': str(credential_path()),
                'created_at': value['created_at'], 'provider': 'external_agent'}
    except PermissionError:
        return {'enabled': False, 'provider': 'external_agent'}


def revoke(owner_id):
    credential_path().parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, _request_lock(credential_path()):
        if _read().get('owner_id') != owner_id:
            raise PermissionError('This agent connection belongs to another account.')
        credential_path().unlink(missing_ok=True)
    return {'enabled': False}


def authorized_credentials(key, method, path):
    allowed = ((method == 'POST' and path == '/api/generate') or
               (method == 'GET' and path in {'/api/agent/capabilities', '/api/agent/requests'}) or
               (method == 'GET' and re.fullmatch(r'/api/agent/requests/[0-9a-f]{32}', path)) or
               (method == 'POST' and re.fullmatch(r'/api/agent/requests/[0-9a-f]{32}/complete', path)) or
               (method == 'POST' and re.fullmatch(r'/api/agent/jobs/[A-Za-z0-9_-]{1,128}/retry', path)) or
               (method == 'GET' and re.fullmatch(r'/api/agent/jobs/[A-Za-z0-9_-]{1,128}', path)))
    if not allowed:
        raise PermissionError('This local agent key cannot call that function.')
    value = _read()
    if not secrets.compare_digest(str(value.get('key', '')), str(key)):
        raise PermissionError('Invalid or revoked local agent key.')
    return value


def installation_file(resource_root):
    key = hashlib.sha256(str(Path(resource_root).resolve()).casefold().encode()).hexdigest()[:24]
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'Vidnets Studio' / 'agent-installations'
    return base / (key + '.json')


def register_installation():
    resources = Path(os.environ.get('VIDZEO_RESOURCE_ROOT') or Path(__file__).resolve().parents[2]).resolve()
    root = Path(os.environ.get('VIDZEO_ROOT') or resources).resolve()
    atomic_json(installation_file(resources), {'resource_root': str(resources), 'runtime_root': str(root),
                'packaged': os.environ.get('VIDZEO_PACKAGED') == '1',
                'auth_mode': os.environ.get('VIDZEO_AUTH_MODE', 'supabase'),
                'allow_packaged_local_auth': os.environ.get('VIDZEO_ALLOW_PACKAGED_LOCAL_AUTH', '')})


def refresh_credentials(expected_key):
    # Serialize rotating session refreshes across desktop and headless worker
    # requests in this service. A desktop re-pair rotates the agent key too.
    with _LOCK:
        path = credential_path()
        with _request_lock(path):
            value = _read()
            if value.get('key') != expected_key:
                raise PermissionError('The local agent key was replaced.')
            if not value.get('refresh_token'):
                raise PermissionError('Sign in and pair the agent again in Vidnets Settings.')
            from .auth_service import AUTH_BASE, SUPABASE_ANON_KEY
            response = requests.post(AUTH_BASE + '/token?grant_type=refresh_token',
                headers={'apikey': SUPABASE_ANON_KEY, 'Content-Type': 'application/json'},
                json={'refresh_token': value['refresh_token']}, timeout=15)
            if response.status_code != 200:
                raise PermissionError('Vidnets session renewal failed. Sign in and pair again.')
            session = response.json()
            if str((session.get('user') or {}).get('id', '')) != value['owner_id']:
                raise PermissionError('Renewed Vidnets identity did not match the paired account.')
            value['access_token'] = session['access_token']
            value['refresh_token'] = session.get('refresh_token') or value['refresh_token']
            _write(value)
            return value
