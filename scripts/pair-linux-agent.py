#!/usr/bin/env python3
"""Headless Linux pairing for the Vidnets local agent (Muse worker).

Vidnets sign-in is Google-only, so there is no password to use headlessly.
Instead this uses Supabase email OTP (the same GoTrue API the app uses):

  1. send-otp   -- emails a 6-digit code to the Vidnets account email
  2. pair <code> -- verifies the code, checks the account's real
                   access_state via the app's own auth service, and writes
                   the same agent credential file the desktop app writes
                   (secure/agent-access.dpapi, Linux LNX1 cipher), plus
                   registers the installation so later agent starts reuse it.

Needs SUPABASE_URL + SUPABASE_ANON_KEY in the environment (or in
~/workspace/vidnets-runtime/.env, which the server loads on boot).
The refresh token is stored with the credential, so the session renews
itself on Linux afterwards -- this is a one-time pairing.

Run with the Vidnets venv python from the repo root:
    vidnets/.venv/bin/python scripts/pair-linux-agent.py send-otp you@example.com
    vidnets/.venv/bin/python scripts/pair-linux-agent.py pair you@example.com 123456
"""
import json
import os
import sys

VIDNETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'vidnets')
RUNTIME_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', 'vidnets-runtime')
RUNTIME_ROOT = os.path.abspath(RUNTIME_ROOT)

os.environ.setdefault('VIDZEO_ROOT', RUNTIME_ROOT)
os.environ.setdefault('VIDZEO_RESOURCE_ROOT', VIDNETS_DIR)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(RUNTIME_ROOT, '.env'))
except ImportError:
    pass
sys.path.insert(0, os.path.join(VIDNETS_DIR, 'server'))

import requests  # noqa: E402

DEVICE_CONTEXT = {
    'device_fingerprint': 'muse-linux-worker',
    'device_info': {'host': 'muse-linux-vm', 'os': 'linux'},
    'last_login_ip': '127.0.0.1',
    'user_agent': 'muse-linux-worker/1.0',
}


def _supabase():
    url = os.environ.get('SUPABASE_URL', '').rstrip('/')
    key = os.environ.get('SUPABASE_ANON_KEY', '').strip()
    if not url or not key:
        raise SystemExit('Set SUPABASE_URL and SUPABASE_ANON_KEY first (env or vidnets-runtime/.env).')
    return url, key


def send_otp(email):
    url, key = _supabase()
    resp = requests.post(
        f'{url}/auth/v1/otp',
        headers={'apikey': key, 'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        json={'email': email, 'create_user': False},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise SystemExit(f'OTP send failed ({resp.status_code}): {resp.text[:300]}')
    print(f'Code sent to {email}. It expires in 60 minutes; run: pair {email} <6-digit code>')


def pair(email, code):
    from services import agent_access
    from services.auth_service import sync_authenticated_user

    url, key = _supabase()
    resp = requests.post(
        f'{url}/auth/v1/verify',
        headers={'apikey': key, 'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        json={'email': email, 'token': code.strip(), 'type': 'email'},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise SystemExit(f'Code verification failed ({resp.status_code}): {resp.text[:300]}')
    session = resp.json()
    access_token = session.get('access_token')
    refresh_token = session.get('refresh_token', '')
    if not access_token:
        raise SystemExit('Supabase returned no session token.')
    # The app's own check: verifies the token and returns the real
    # access_state (active requires a paid plan or admin grant).
    state = sync_authenticated_user(access_token, **DEVICE_CONTEXT)
    print('account:', json.dumps({k: state.get(k) for k in ('user_id', 'email', 'access_state', 'plan', 'tier')}, indent=2))
    if state.get('access_state') != 'active':
        raise SystemExit('Account access_state is not active; pairing refused by the app\'s own guard.')
    result = agent_access.pair(access_token, state, DEVICE_CONTEXT, refresh_token)
    print('paired:', json.dumps(result, indent=2))


if __name__ == '__main__':
    if len(sys.argv) < 3 or sys.argv[1] not in ('send-otp', 'pair'):
        raise SystemExit('usage: pair-linux-agent.py send-otp <email> | pair <email> <code>')
    if sys.argv[1] == 'send-otp':
        send_otp(sys.argv[2])
    else:
        if len(sys.argv) < 4:
            raise SystemExit('usage: pair-linux-agent.py pair <email> <code>')
        pair(sys.argv[2], sys.argv[3])
