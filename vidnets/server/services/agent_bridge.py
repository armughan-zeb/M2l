"""Local inference inbox for an external agent's own text/vision reasoning.

The host agent answers requests; this service never invokes its model provider
or borrows a ChatGPT login. All writes are correlated, bounded and atomic.
"""
from contextlib import contextmanager
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
import requests

from .job_context import current_job, check_job_active, remaining_seconds

AGENT_HANDLE = 'external-agent:'
AGENT_MODEL = 'external-agent'
AGENT_REQUEST_WALL_SECONDS = 120.0


class AgentBridgeError(RuntimeError):
    stage = 'external_agent'
    retryable = True


def bridge_root():
    runtime = Path(os.environ.get('VIDZEO_RUNTIME_ROOT') or Path.home() / 'workspace' / 'vidnets-runtime')
    temp = Path(os.environ.get('VIDZEO_TEMP_DIR') or runtime / 'temp')
    return Path(os.environ.get('VIDNETS_AGENT_BRIDGE_DIR') or os.environ.get('SCRIPTIT_AGENT_BRIDGE_DIR') or
                os.environ.get('VIDZEO_BRIDGE_ROOT') or os.environ.get('MUSE_BRIDGE_ROOT') or
                os.environ.get('SCRIPTIT_BRIDGE_ROOT') or temp / 'agent-bridge')


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _request_path(request_id):
    if len(str(request_id)) != 32 or any(c not in '0123456789abcdef' for c in request_id):
        raise ValueError('Invalid agent request ID.')
    return bridge_root() / request_id / 'request.json'


@contextmanager
def _request_lock(path):
    """Cross-process single completion/cancellation, with bounded lock wait."""
    if os.name == 'nt':
        import msvcrt
    lock_path = path.with_name(path.name + '.lock')
    with lock_path.open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        deadline = time.monotonic() + 5
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise AgentBridgeError('An agent response is currently being updated.')
                time.sleep(.02)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read_request(request_id, owner_id, *, locked=False):
    path = _request_path(request_id)
    if not path.is_file():
        raise ValueError('Agent request was not found.')
    row = json.loads(path.read_text(encoding='utf-8'))
    if not secrets.compare_digest(str(row.get('owner_id', '')), str(owner_id)):
        raise PermissionError('This agent request belongs to another account.')
    # Deadline is authoritative, even if the generation process exited.
    if row['state'] == 'pending' and row['expires_at'] <= time.time():
        if not locked:
            with _request_lock(path):
                return read_request(request_id, owner_id, locked=True)
        if path.with_name('response.json').exists():
            row['state'] = 'answered'
        else:
            row['state'] = 'expired'
        atomic_json(path, row)
    return row


def pending_requests(owner_id):
    rows = []
    if not bridge_root().exists():
        return rows
    for path in bridge_root().glob('*/request.json'):
        try:
            row = read_request(path.parent.name, owner_id)
            if row['state'] == 'pending':
                rows.append({key: row[key] for key in ('id', 'job_id', 'operation', 'kind', 'created_at', 'expires_at')})
        except (OSError, ValueError, PermissionError, KeyError):
            continue
    return sorted(rows, key=lambda row: row['created_at'])


def complete_request(request_id, owner_id, content, error=''):
    path = _request_path(request_id)
    if not path.exists():
        raise ValueError('Agent request was not found.')
    with _request_lock(path):
        row = read_request(request_id, owner_id, locked=True)
        if row['state'] != 'pending' or path.with_name('response.json').exists():
            raise ValueError('This request expired or was already answered.')
        if error:
            reply = {'error': str(error)[:500]}
        else:
            if isinstance(content, (dict, list)):
                content = json.dumps(content, ensure_ascii=False)
            if not isinstance(content, str) or not content.strip() or len(content.encode('utf-8')) > 2_000_000:
                raise ValueError('Agent response must be nonempty text or JSON under 2 MB.')
            contract = (row.get('response_format') or {}).get('json_schema')
            if contract:
                from .openrouter_llm import _json_schema_error
                try:
                    value = json.loads(content)
                except ValueError:
                    raise ValueError('This request requires valid JSON matching its schema.') from None
                issue = _json_schema_error(value, contract['schema'])
                if issue:
                    raise ValueError('Agent response does not match the schema: ' + str(issue)[:300])
            elif (row.get('response_format') or {}).get('type') == 'json_object':
                try:
                    if not isinstance(json.loads(content), dict):
                        raise ValueError()
                except ValueError:
                    raise ValueError('This request requires a JSON object.') from None
            reply = {'content': content}
        atomic_json(path.with_name('response.json'), reply)
        row['state'] = 'answered'
        atomic_json(path, row)
    return {'success': True, 'request_id': request_id}


def _messages_with_local_images(messages, directory):
    result = []
    count = 0
    for message in messages:
        copied = dict(message)
        if isinstance(message.get('content'), list):
            content = []
            for part in message['content']:
                copied_part = dict(part)
                if part.get('type') == 'image_url':
                    source = part.get('image_url', {})
                    url = source.get('url', '') if isinstance(source, dict) else str(source)
                    if url.startswith('data:image/') and ';base64,' in url:
                        prefix, encoded = url.split(';base64,', 1)
                        data = base64.b64decode(encoded, validate=True)
                        if len(data) > 5 * 1024 * 1024:
                            raise ValueError('Agent image exceeds the 5 MB limit.')
                        extension = '.png' if prefix == 'data:image/png' else '.jpg'
                        count += 1
                        image_path = directory / f'frame-{count}{extension}'
                        image_path.write_bytes(data)
                        copied_part = {'type': 'local_image', 'path': str(image_path.resolve()),
                                       'detail': source.get('detail', 'auto') if isinstance(source, dict) else 'auto'}
                content.append(copied_part)
            copied['content'] = content
        result.append(copied)
    return result


def agent_post(payload, timeout, *, operation='', kind='text', handle=''):
    context = current_job()
    owner = str(context.get('agent_owner_id') or (handle[len(AGENT_HANDLE):] if handle.startswith(AGENT_HANDLE) else ''))
    if not owner:
        raise AgentBridgeError('Pair a local agent before starting an agent-powered job.')
    # The resident answerer targets 60s. Preserve the enclosing job budget.
    wall = max(0.0, min(AGENT_REQUEST_WALL_SECONDS, remaining_seconds(AGENT_REQUEST_WALL_SECONDS)))
    if wall <= 0:
        raise AgentBridgeError(f'Job budget exhausted before posting Muse request ({operation or kind}).')
    request_id = secrets.token_hex(16)
    path = _request_path(request_id)
    path.parent.mkdir(parents=True)
    deadline = time.monotonic() + wall
    created_at = time.time()
    row = {'id': request_id, 'owner_id': owner, 'job_id': context.get('job_id', ''),
           'attempt_id': context.get('attempt_id', ''), 'kind': kind, 'operation': operation,
           'state': 'pending', 'created_at': created_at, 'expires_at': created_at + wall,
           'messages': _messages_with_local_images(payload.get('messages', []), path.parent),
           'response_format': payload.get('response_format') or {},
           'max_tokens_hint': payload.get('max_tokens'),
           'instructions': 'Use your own reasoning and vision tools. Ground facts in the supplied context. Return the requested schema. Never call OpenRouter for this request.'}
    atomic_json(path, row)
    reply_path = path.with_name('response.json')
    try:
        while time.monotonic() < deadline:
            check_job_active()
            if reply_path.exists():
                reply = json.loads(reply_path.read_text(encoding='utf-8'))
                if reply.get('error'):
                    raise AgentBridgeError(f'Muse request {request_id} ({operation or kind}) failed: ' + str(reply['error']))
                value = {'id': 'agent-' + request_id, 'model': AGENT_MODEL,
                         'provider': 'external_agent', 'billing': 'external_agent_runtime',
                         'choices': [{'message': {'content': reply['content']}, 'finish_reason': 'stop'}], 'usage': {}}
                response = requests.Response()
                response.status_code = 200
                response._content = json.dumps(value).encode('utf-8')
                return response
            time.sleep(min(.1, max(.001, deadline - time.monotonic())))
        raise AgentBridgeError(f'Muse request {request_id} ({operation or kind}) expired after {wall:.1f}s.')
    finally:
        with _request_lock(path):
            saved = json.loads(path.read_text(encoding='utf-8'))
            if saved['state'] == 'pending':
                saved['state'] = 'expired'
                atomic_json(path, saved)
