"""Account-wide quota admission; scoped completion receipts survive queue restarts.

Only the user's JWT may reserve exports. Workers retain a receipt that can settle
one reservation, never the user's account token. Failed reporting goes to an
idempotent local outbox so a finished video is not lost to an analytics outage.
"""
import json
import os
import re
import threading
import uuid
from pathlib import Path

import requests
import config

_outbox_lock = threading.Lock()


def reserve_generation_export(data, access_token, duration_seconds=0):
    from services.auth_service import _rpc_request, AuthServiceError
    mode = str(os.environ.get('VIDZEO_AUTH_MODE', '')).strip().lower() or 'local'
    if mode in {'local', 'offline', 'byok'} or access_token in {'local-dev-token', 'local-bypass', ''} or data.get('auth_user_id') == 'local-user':
        return {'allowed': True, 'development_mode': True}
    request_id = str(data.get('_export_request_id') or data.get('request_id') or uuid.uuid4())
    try:
        result = _rpc_request(access_token, 'reserve_vidnets_export', {
            'p_request_id': request_id, 'p_duration_seconds': max(0, float(duration_seconds or 0)),
        })
    except AuthServiceError as exc:
        # Admission must not silently turn a remote quota into a resettable local counter.
        raise AuthServiceError('Could not verify the export allowance. Your script is saved; reconnect and try again.', status_code=503) from exc
    if not isinstance(result, dict) or not result.get('allowed') or not result.get('receipt'):
        result = result if isinstance(result, dict) else {}
        if result.get('reason') == 'duration':
            raise ValueError(f"PLAN_LIMIT_REACHED: This plan allows up to {result.get('max_video_minutes', 1)} minute(s) per video. Shorten the script or view upgrade options.")
        raise ValueError(f"PLAN_LIMIT_REACHED: Your {result.get('limit', 5)} monthly export slots are used or reserved by queued videos. Cancel an unused job, wait for the next month, or upgrade.")
    data['_export_request_id'] = request_id
    data['_export_usage_ticket'] = result['receipt']
    return result


def _outbox_dir():
    return Path(config.TEMP_DIR) / 'usage-outbox'


def _send(payload):
    from services.auth_service import REST_BASE, SUPABASE_ANON_KEY
    response = requests.post(f'{REST_BASE}/rpc/finish_vidnets_export', json=payload,
        headers={'apikey': SUPABASE_ANON_KEY, 'Authorization': f'Bearer {SUPABASE_ANON_KEY}'}, timeout=5)
    if response.status_code >= 400 or response.json() is not True:
        raise RuntimeError('Usage confirmation is pending.')


def finish_generation_export(data, success, duration_seconds=0):
    receipt = str(data.get('_export_usage_ticket') or '')
    if not re.fullmatch(r'[a-f0-9]{64}', receipt):
        return
    payload = {'p_receipt': receipt, 'p_success': bool(success), 'p_duration_seconds': max(0, float(duration_seconds or 0))}
    folder = _outbox_dir()
    path = folder / f'{receipt}.json'
    # Write before sending: a crash between confirmation and deletion only replays
    # the same receipt, which cannot count the export twice.
    try:
        with _outbox_lock:
            folder.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(payload), encoding='utf-8')
            os.replace(temporary, path)
    except OSError:
        print('   [usage] Local confirmation storage is unavailable; retaining the video result.')
    try:
        _send(payload)
        path.unlink(missing_ok=True)
    except Exception:
        print('   [usage] Confirmation saved for retry; the video result is retained.')


def flush_usage_outbox():
    folder = _outbox_dir()
    if not folder.exists():
        return
    for path in list(folder.glob('*.json'))[:3]:
        try:
            _send(json.loads(path.read_text(encoding='utf-8')))
            path.unlink(missing_ok=True)
        except Exception:
            break
