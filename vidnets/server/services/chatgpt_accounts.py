"""Official ChatGPT plan OAuth; credentials stay in the Windows user vault.

Private clients require OpenAI approval. Dynamic registration is only enabled
for an explicitly configured open-source distribution. No ChatGPT web scraping,
cookie reuse, API-key substitution, or automatic account/credit rotation.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

ISSUER = 'https://auth.openai.com'
RESOURCE = 'https://api.openai.com/v1'
AUTHORIZE = ISSUER + '/api/accounts/authorize'
TOKEN = ISSUER + '/api/accounts/oauth/token'
PLAN_SCOPE = 'chatgpt.tokens.use.direct'
SCOPES = 'openid profile email offline_access resource.invoke ' + PLAN_SCOPE
USAGE_URL = 'https://chatgpt.com/settings/usage'
_REFRESH_TERMINAL = {'invalid_grant', 'invalid_refresh_token', 'token_expired',
                     'refresh_token_expired', 'refresh_token_invalidated', 'refresh_token_reused'}


class ChatGPTError(RuntimeError):
    def __init__(self, message, code='chatgpt_unavailable', status=503, request_id=''):
        super().__init__(message)
        self.code, self.status, self.request_id = code, status, request_id
        self.retryable = status >= 500
        self.stage = 'chatgpt_provider'


def integration_status():
    approved = os.environ.get('VIDNETS_CHATGPT_PLAN_ACCESS_APPROVED') == '1'
    client = os.environ.get('VIDNETS_CHATGPT_CLIENT_ID', '').strip()
    oss = os.environ.get('VIDNETS_CHATGPT_OPEN_SOURCE_CLIENT') == '1'
    enabled = oss or (approved and client.startswith('oaiapp_'))
    return {'enabled': enabled, 'mode': 'open_source' if oss else 'private',
            'message': ('Sign in with your own eligible ChatGPT plan.' if enabled else
                        'ChatGPT connection is prepared. Vidnets needs OpenAI approval for plan usage before sign-in can be enabled.')}


def _provider_error(response):
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = {}
    error = payload.get('error') if isinstance(payload, dict) else None
    code = error.get('code', '') if isinstance(error, dict) else error if isinstance(error, str) else ''
    # Surface actionable codes without logging arbitrary bodies or tokens.
    return ChatGPTError('ChatGPT request could not complete. ' + str(code or f'HTTP {response.status_code}'),
                        str(code or 'chatgpt_http_error'), response.status_code,
                        response.headers.get('x-request-id', ''))


class ChatGPTAccounts:
    def __init__(self, path=None):
        root = Path(os.environ.get('VIDZEO_SECURE_DATA_DIR') or
                    Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Vidnets Studio' / 'secure')
        self.path = Path(path) if path else root / 'chatgpt-accounts.dpapi'
        self._mutex = threading.RLock()
        self._flows = {}
        self._flow_guard = threading.Lock()

    @contextmanager
    def _locked(self):
        """Serialize rotating refreshes and atomic storage across worker processes."""
        with self._mutex:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lockfile = self.path.with_suffix('.lock')
            with lockfile.open('a+b') as stream:
                stream.seek(0, 2)
                if stream.tell() == 0:
                    stream.write(b'0')
                    stream.flush()
                deadline = time.monotonic() + 40
                while True:
                    try:
                        stream.seek(0)
                        if os.name == 'nt':
                            import msvcrt
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise ChatGPTError('Another account operation is pending. Try again shortly.')
                        time.sleep(.05)
                try:
                    yield
                finally:
                    stream.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _read(self):
        if not self.path.exists():
            return {'version': 1, 'host_id': secrets.token_hex(24), 'active_account_id': '', 'accounts': {}}
        from .youtube_session import _unprotect
        try:
            data = json.loads(_unprotect(self.path.read_bytes()))
            if data.get('version') != 1 or not isinstance(data.get('accounts'), dict):
                raise ValueError('Invalid vault')
            return data
        except Exception:
            raise ChatGPTError('Saved ChatGPT credentials could not be unlocked. Use the original Windows user account.',
                               'credential_storage_unavailable', 503) from None

    def _write(self, data):
        from .youtube_session import _protect
        try:
            encrypted = _protect(json.dumps(data).encode('utf-8'))
        except Exception:
            raise ChatGPTError('Secure account storage is unavailable. No tokens were saved.',
                               'credential_storage_unavailable', 503) from None
        temporary = self.path.with_name(self.path.name + '.' + secrets.token_hex(8) + '.tmp')
        try:
            with temporary.open('xb') as stream:
                os.chmod(temporary, 0o600)
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def active_account_id(self):
        with self._locked():
            return self._read().get('active_account_id', '')

    @staticmethod
    def _public(account_id, record):
        enabled = PLAN_SCOPE in record.get('scopes', [])
        connected = bool(record.get('access_token'))
        state = record.get('state') or ('ready' if enabled and connected else 'permission_needed' if connected else 'signed_out')
        messages = {'ready': 'Connected to your ChatGPT plan.', 'signed_out': 'Sign in again to use this account.',
                    'sign_in_required': 'ChatGPT did not accept this connection. Sign in again to continue.',
                    'permission_needed': 'Sign in again and allow ChatGPT plan usage.',
                    'limit_reached': 'This app or account has reached a usage limit. Check ChatGPT Usage, wait, or choose another saved account.',
                    'ineligible': 'This account or workspace is not eligible for ChatGPT plan usage.'}
        return {'id': account_id, 'label': record.get('label', 'ChatGPT account'),
                'email': record.get('email', ''), 'state': state,
                'connected': connected, 'plan_enabled': enabled,
                'message': messages.get(state, 'ChatGPT needs attention.'),
                'last_error_code': record.get('last_error_code', ''),
                'last_request_id': record.get('last_request_id', '')}

    def status(self):
        with self._locked():
            data = self._read()
            rows = [self._public(key, value) for key, value in data['accounts'].items()]
            return {'integration': integration_status(), 'active_account_id': data.get('active_account_id', ''),
                    'accounts': rows, 'usage_url': USAGE_URL}

    def select(self, account_id, retry=False):
        with self._locked():
            data = self._read()
            row = data['accounts'].get(account_id)
            if not row or not row.get('access_token') or PLAN_SCOPE not in row.get('scopes', []):
                raise ChatGPTError('Sign in and enable plan usage before selecting this account.', 'account_not_ready', 400)
            if retry and row.get('state') == 'limit_reached':
                row['state'] = 'ready'
                row.pop('last_error_code', None)
            data['active_account_id'] = account_id
            self._write(data)
        return self.status()

    def record_error(self, account_id, error):
        with self._locked():
            data = self._read()
            row = data['accounts'].get(account_id)
            if not row:
                return
            row['last_error_code'], row['last_request_id'] = error.code, error.request_id
            if error.code == 'subscription_sharing_usage_limit_exceeded' or error.status == 429:
                row['state'] = 'limit_reached'
            elif error.code == 'subscription_sharing_user_not_eligible':
                row['state'] = 'ineligible'
            elif error.status == 401:
                row['state'] = 'sign_in_required'
            self._write(data)

    @staticmethod
    def _validate_id_token(token, client_id, nonce=None):
        try:
            import jwt
        except ImportError:
            raise ChatGPTError('Install the bundled ChatGPT authentication dependency before connecting.',
                               'auth_dependency_missing', 503) from None
        # Fixed trusted issuer/JWKS, with an algorithm allowlist. Never follow
        # token-controlled jku/x5u URLs or accept unsigned identity claims.
        jwks = jwt.PyJWKClient(ISSUER + '/.well-known/jwks.json', timeout=15)
        key = jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=['RS256'], audience=client_id,
                            issuer=ISSUER, options={'require': ['exp', 'iat', 'iss', 'aud', 'sub']})
        if not isinstance(claims.get('sub'), str) or not claims['sub']:
            raise ChatGPTError('ChatGPT returned an invalid account identity.', 'invalid_identity', 401)
        if nonce is not None and not secrets.compare_digest(str(claims.get('nonce', '')), nonce):
            raise ChatGPTError('ChatGPT sign-in could not be verified.', 'invalid_nonce', 401)
        return claims

    @staticmethod
    def _apply_tokens(row, tokens, scopes=None):
        if not tokens.get('access_token') or str(tokens.get('token_type', '')).lower() != 'bearer':
            raise ChatGPTError('ChatGPT returned an invalid token response.', 'invalid_token_response', 401)
        row['access_token'] = tokens['access_token']
        if tokens.get('refresh_token'):
            row['refresh_token'] = tokens['refresh_token']
        if tokens.get('id_token'):
            row['id_token'] = tokens['id_token']
        row['scopes'] = str(tokens['scope']).split() if 'scope' in tokens else list(scopes or row.get('scopes', []))
        row['expires_at'] = time.time() + float(tokens.get('expires_in', 3600))
        row['earliest_refresh_at'] = tokens.get('earliest_refresh_at', 0)
        row['saved_at'] = time.time()
        row['state'] = 'ready' if PLAN_SCOPE in row['scopes'] else 'permission_needed'
        row.pop('last_error_code', None)
        row.pop('last_request_id', None)

    def access_token(self, account_id):
        if not integration_status()['enabled']:
            raise ChatGPTError(integration_status()['message'], 'integration_not_enabled', 403)
        with self._locked():
            data = self._read()
            row = data['accounts'].get(account_id)
            if not row or not row.get('access_token'):
                raise ChatGPTError('Sign in to the selected ChatGPT account.', 'sign_in_required', 401)
            if PLAN_SCOPE not in row.get('scopes', []):
                raise ChatGPTError('Allow ChatGPT plan usage during sign-in.', 'permission_needed', 403)
            if row.get('state') == 'sign_in_required':
                raise ChatGPTError('Sign in again to renew your ChatGPT connection.', 'sign_in_required', 401)
            if row.get('state') in {'limit_reached', 'ineligible'}:
                code = 'subscription_sharing_usage_limit_exceeded' if row['state'] == 'limit_reached' else 'subscription_sharing_user_not_eligible'
                raise ChatGPTError(self._public(account_id, row)['message'], code, 429 if row['state'] == 'limit_reached' else 403)
            if float(row.get('expires_at', 0)) > time.time() + 120:
                return row['access_token']
            earliest = row.get('earliest_refresh_at', 0)
            try:
                earliest = float(earliest or 0)
            except (TypeError, ValueError):
                earliest = 0
            if time.time() < earliest and time.time() < float(row.get('expires_at', 0)):
                return row['access_token']
            if not row.get('refresh_token'):
                raise ChatGPTError('Sign in again to renew your ChatGPT connection.', 'sign_in_required', 401)
            response = requests.post(TOKEN, data={'grant_type': 'refresh_token', 'client_id': row['client_id'],
                         'refresh_token': row['refresh_token'], 'resource': RESOURCE}, timeout=20)
            if response.status_code != 200:
                error = _provider_error(response)
                if error.code in _REFRESH_TERMINAL:
                    for name in ('access_token', 'refresh_token', 'id_token'):
                        row.pop(name, None)
                    row['state'] = 'signed_out'
                    self._write(data)
                raise error
            tokens = response.json()
            if tokens.get('id_token'):
                claims = self._validate_id_token(tokens['id_token'], row['client_id'])
                if claims['sub'] != row['subject'] or claims['iss'] != row['issuer']:
                    raise ChatGPTError('Renewed account identity did not match.', 'identity_mismatch', 401)
            self._apply_tokens(row, tokens)
            self._write(data)
            return row['access_token']

    def model_catalog(self, account_id):
        token = self.access_token(account_id)
        response = requests.get(RESOURCE + '/models', headers={'Authorization': 'Bearer ' + token}, timeout=20)
        if response.status_code != 200:
            error = _provider_error(response)
            self.record_error(account_id, error)
            raise error
        # Only expose the inexpensive requested default. An account lacking
        # Luna needs an actionable error, never an automatic expensive upgrade.
        catalog = response.json().get('models', [])
        rows = [{'id': row['slug'], 'label': row.get('display_name') or row['slug']}
                for row in catalog if row.get('visibility') == 'list' and row.get('slug') == 'gpt-6-luna']
        with self._locked():
            data = self._read()
            if account_id in data['accounts']:
                data['accounts'][account_id]['models'] = rows
                data['accounts'][account_id]['models_checked_at'] = time.time()
                self._write(data)
        if not rows:
            raise ChatGPTError('GPT-6 Luna is not available to this ChatGPT account. Select another account or use OpenRouter.',
                               'model_unavailable', 400)
        return rows

    def ensure_model(self, account_id, model):
        if model != 'gpt-6-luna':
            raise ChatGPTError('ChatGPT projects use GPT-6 Luna; paid model upgrades are disabled.', 'model_not_allowed', 400)
        with self._locked():
            row = self._read()['accounts'].get(account_id, {})
            checked = row.get('models_checked_at', 0)
            available = any(item.get('id') == model for item in row.get('models', []))
        if not available or time.time() - checked > 300:
            self.model_catalog(account_id)

    def disconnect(self, account_id):
        confirmed = False
        with self._locked():
            data = self._read()
            row = data['accounts'].get(account_id)
            if not row:
                raise ChatGPTError('Account was not found.', 'account_not_found', 404)
            if row.get('refresh_token'):
                try:
                    discovery = requests.get(ISSUER + '/.well-known/openid-configuration', timeout=10)
                    discovery.raise_for_status()
                    endpoint = discovery.json().get('revocation_endpoint', '')
                    parsed = urlparse(endpoint)
                    if parsed.scheme != 'https' or parsed.hostname != 'auth.openai.com':
                        raise ValueError('Untrusted revocation endpoint')
                    for attempt in range(2):
                        response = requests.post(endpoint, data={'token': row['refresh_token'],
                            'token_type_hint': 'refresh_token', 'client_id': row['client_id']}, timeout=10)
                        confirmed = response.status_code == 200
                        if confirmed or response.status_code < 500:
                            break
                        time.sleep(.5)
                except (requests.RequestException, ValueError):
                    pass
            for name in ('access_token', 'refresh_token', 'id_token', 'models', 'models_checked_at'):
                row.pop(name, None)
            row['state'] = 'signed_out'
            if data.get('active_account_id') == account_id:
                data['active_account_id'] = ''
            self._write(data)
        return {**self.status(), 'revocation_confirmed': confirmed,
                'message': 'Signed out.' if confirmed else 'Signed out locally. Remote revocation was not confirmed; disconnect Vidnets in ChatGPT Settings.'}

    def start_sign_in(self, account_id=''):
        integration = integration_status()
        if not integration['enabled']:
            raise ChatGPTError(integration['message'], 'integration_not_enabled', 403)
        with self._flow_guard:
            now = time.time()
            self._flows = {key: row for key, row in self._flows.items() if row['expires_at'] > now}
            if any(row['status'] in {'pending', 'exchanging'} for row in self._flows.values()):
                raise ChatGPTError('Finish or cancel the current ChatGPT sign-in first.', 'sign_in_pending', 409)
        with self._locked():
            data = self._read()
            existing = data['accounts'].get(account_id) if account_id else None
            if account_id and not existing:
                raise ChatGPTError('Saved account was not found.', 'account_not_found', 404)
            self._write(data)  # Persist stable host ID before starting OAuth.
        client_id = existing['client_id'] if existing else (
            'dynamic_agent_client' if integration['mode'] == 'open_source' else os.environ['VIDNETS_CHATGPT_CLIENT_ID'])
        state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        flow_id = secrets.token_urlsafe(24)
        service = self

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Callback queries contain authorization codes.

            def do_GET(self):
                if urlparse(self.path).path != '/auth/callback':
                    self.send_error(404)
                    return
                parameters = parse_qs(urlparse(self.path).query)
                try:
                    service.complete_sign_in(flow_id, parameters)
                    message = 'ChatGPT connection saved. You can return to Vidnets.'
                    status = 200
                except Exception:
                    message = 'ChatGPT connection could not be completed. Return to Vidnets for details.'
                    status = 400
                self.send_response(status)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.end_headers()
                self.wfile.write(message.encode('utf-8'))

        listener = HTTPServer(('127.0.0.1', 0), Callback)
        listener.timeout = .5
        redirect = f'http://127.0.0.1:{listener.server_port}/auth/callback'
        flow = {'status': 'pending', 'expires_at': time.time() + 600, 'state': state,
                'nonce': nonce, 'verifier': verifier, 'redirect_uri': redirect,
                'client_id': client_id, 'account_id': account_id, 'host_id': data['host_id']}
        with self._flow_guard:
            self._flows[flow_id] = flow
        params = {'client_id': client_id, 'ext_agent_host_id': data['host_id'],
                  'response_type': 'code', 'redirect_uri': redirect, 'scope': SCOPES,
                  'resource': RESOURCE, 'state': state, 'nonce': nonce,
                  'code_challenge_method': 'S256',
                  'code_challenge': __import__('base64').urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')}
        if client_id == 'dynamic_agent_client':
            params['agent_name_hint'] = 'Vidnets Studio'
        if existing:
            if existing.get('id_token'):
                params['id_token_hint'] = existing['id_token']
            if existing.get('email'):
                params['login_hint'] = existing['email']
            if PLAN_SCOPE not in existing.get('scopes', []):
                params['prompt'] = 'consent'

        def serve():
            try:
                while time.time() < flow['expires_at'] and flow['status'] == 'pending':
                    listener.handle_request()
                with self._flow_guard:
                    if flow['status'] == 'pending':
                        flow['status'], flow['message'] = 'expired', 'Sign-in expired. Try again.'
            finally:
                listener.server_close()
        threading.Thread(target=serve, name='vidnets-chatgpt-oauth', daemon=True).start()
        # Never return authorization URLs (which may include an ID-token hint)
        # to renderer state, diagnostics, or local storage.
        try:
            opened = webbrowser.open(AUTHORIZE + '?' + urlencode(params))
        except Exception:
            opened = False
        if not opened:
            flow['status'], flow['message'] = 'failed', 'Could not open your browser. Try again.'
        return {'flow_id': flow_id, 'status': flow['status']}

    def flow_status(self, flow_id):
        with self._flow_guard:
            row = self._flows.get(flow_id)
            if not row:
                raise ChatGPTError('Sign-in attempt was not found.', 'flow_not_found', 404)
            return {key: row[key] for key in ('status', 'message', 'account_id', 'error_code') if key in row}

    def cancel_sign_in(self, flow_id):
        with self._flow_guard:
            row = self._flows.get(flow_id)
            if row and row['status'] == 'pending':
                row['status'], row['message'] = 'cancelled', 'Sign-in cancelled.'
        return {'status': 'cancelled'}

    def complete_sign_in(self, flow_id, parameters):
        with self._flow_guard:
            flow = self._flows.get(flow_id)
            if not flow or flow['status'] != 'pending' or flow['expires_at'] <= time.time():
                raise ChatGPTError('Sign-in expired or already used.', 'invalid_state', 400)
            returned = parameters.get('state', [''])[0]
            if not secrets.compare_digest(returned, flow['state']):
                # Do not let an unrelated loopback request consume a valid flow.
                raise ChatGPTError('Sign-in state did not match.', 'invalid_state', 400)
            flow['status'] = 'exchanging'  # One-time consumption, before network I/O.
        try:
            if parameters.get('error'):
                raise ChatGPTError('ChatGPT sign-in was cancelled or denied.', 'access_denied', 400)
            code = parameters.get('code', [''])[0]
            if not code:
                raise ChatGPTError('No authorization code was returned.', 'invalid_callback', 400)
            issued = parameters.get('client_id', [''])[0]
            if flow['client_id'] == 'dynamic_agent_client':
                if not issued.startswith('oaiapp_'):
                    raise ChatGPTError('An issued client registration was not returned.', 'invalid_client', 400)
            else:
                if issued and issued != flow['client_id']:
                    raise ChatGPTError('The client registration changed unexpectedly.', 'invalid_client', 400)
                issued = flow['client_id']
            response = requests.post(TOKEN, data={'grant_type': 'authorization_code', 'client_id': issued,
                  'code': code, 'code_verifier': flow['verifier'], 'redirect_uri': flow['redirect_uri'],
                  'resource': RESOURCE}, timeout=20)
            if response.status_code != 200:
                raise _provider_error(response)
            tokens = response.json()
            claims = self._validate_id_token(tokens.get('id_token', ''), issued, flow['nonce'])
            with self._locked():
                data = self._read()
                account_id = flow['account_id'] or secrets.token_hex(16)
                previous = data['accounts'].get(account_id, {})
                if previous and (previous['subject'] != claims['sub'] or previous['issuer'] != claims['iss'] or previous['client_id'] != issued):
                    raise ChatGPTError('The signed-in account did not match the selected saved account.', 'identity_mismatch', 401)
                email = str(claims.get('email', ''))
                row = {**previous, 'client_id': issued, 'host_id': flow['host_id'],
                       'subject': claims['sub'], 'issuer': claims['iss'], 'email': email,
                       'label': previous.get('label') or f'{email or "ChatGPT account"} · {account_id[:6]}'}
                self._apply_tokens(row, tokens)
                data['accounts'][account_id] = row
                if PLAN_SCOPE in row['scopes']:
                    data['active_account_id'] = account_id
                self._write(data)
            flow.update(status='completed', account_id=account_id,
                        message='Connected.' if PLAN_SCOPE in row['scopes'] else 'Signed in. Enable ChatGPT plan usage to generate videos.')
        except Exception as error:
            flow.update(status='failed', message=str(error) if isinstance(error, ChatGPTError) else 'ChatGPT sign-in validation failed. Try again.',
                        error_code=getattr(error, 'code', 'sign_in_failed'))
            raise
        finally:
            # PKCE/state/nonce are no longer needed after the terminal callback.
            for key in ('verifier', 'state', 'nonce'):
                flow.pop(key, None)


accounts = ChatGPTAccounts()
