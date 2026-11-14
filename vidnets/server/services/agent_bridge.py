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
import shutil
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


# --- Answer cache + bridge prune (inbox reliability) ---

# A re-issued request carries the same payload under a new id (verified by
# fingerprint 2026-11-14: identical messages, operation and response
# format). Caching a composed answer under its payload fingerprint lets a
# re-issue be answered immediately instead of being composed again against
# the 120s wall. The cache lives in one file at the bridge root so it is
# never scanned by pending_requests() and never pruned with the dirs.
_ANSWER_CACHE_FILE = '_answer_cache.json'
_ANSWER_CACHE_MAX_ENTRIES = 200
_PRUNE_GRACE_SECONDS = 24 * 60 * 60
_PRUNE_INTERVAL_SECONDS = 3600
_last_prune_monotonic = 0.0
_prune_lock = threading.Lock()


def payload_fingerprint(row):
    """Stable identity of what an answer must contain.

    Covers everything the answer depends on — job, operation, kind,
    messages, response format — and nothing that changes on re-issue
    (id, created/expires times).
    """
    canonical = {
        'job_id': row.get('job_id', ''),
        'operation': row.get('operation', ''),
        'kind': row.get('kind', ''),
        'messages': row.get('messages', []),
        'response_format': row.get('response_format', {}),
    }
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def _cache_path():
    return bridge_root() / _ANSWER_CACHE_FILE


def _read_answer_cache():
    try:
        data = json.loads(_cache_path().read_text(encoding='utf-8'))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _cache_answer(fingerprint, content, operation=''):
    """Best-effort: a cache failure must never break a completed answer."""
    try:
        cache = _read_answer_cache()
        cache[fingerprint] = {
            'content': content,
            'operation': operation,
            'saved_at': time.time(),
        }
        while len(cache) > _ANSWER_CACHE_MAX_ENTRIES:
            oldest = min(cache, key=lambda key: cache[key].get('saved_at', 0))
            del cache[oldest]
        atomic_json(_cache_path(), cache)
    except Exception:
        pass


def cached_answer_for(request_id, owner_id):
    """Return the previously composed answer for this request's payload, if any.

    Returns None when the request is not pending or its payload has never
    been answered. The caller still has to submit the content through
    complete_request, which re-validates it against this request's schema.
    """
    row = read_request(request_id, owner_id)
    if row['state'] != 'pending':
        return None
    entry = _read_answer_cache().get(payload_fingerprint(row))
    return entry.get('content') if isinstance(entry, dict) else None


def reuse_cached_answer(request_id):
    """Complete a re-issued request from a previously composed answer.

    Returns {'reused': True, ...} when the payload matches a cached answer
    and it was submitted, {'reused': False, ...} when there is nothing to
    reuse (including an expired request). The cached content is
    re-validated by complete_request against this request's schema, so a
    stale or mismatched cache entry cannot be submitted.
    """
    path = _request_path(request_id)
    if not path.is_file():
        raise ValueError('Agent request was not found.')
    row = json.loads(path.read_text(encoding='utf-8'))
    content = cached_answer_for(request_id, str(row.get('owner_id', '')))
    if content is None:
        return {'reused': False, 'request_id': request_id}
    complete_request(request_id, str(row.get('owner_id', '')), content)
    return {'reused': True, 'request_id': request_id}


def prune_bridge(*, grace_seconds=_PRUNE_GRACE_SECONDS):
    """Delete request directories that are terminal and long past expiry.

    A request whose expiry is more than ``grace_seconds`` in the past can
    never be answered or re-issued against (a re-issue gets a new id), so
    its payload is dead history. Only directories are removed; the answer
    cache file lives at the bridge root and is untouched. Returns the number
    of directories removed. A file written within the grace window is kept
    without parsing: its mtime is its creation time for a fresh request and
    its answer time for a completed one, so a recent file cannot be
    terminal and old.
    """
    root = bridge_root()
    if not root.exists():
        return 0
    now = time.time()
    removed = 0
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        request_file = directory / 'request.json'
        if not request_file.is_file():
            continue
        try:
            if request_file.stat().st_mtime > now - grace_seconds:
                continue
            row = json.loads(request_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        try:
            expires_at = float(row.get('expires_at') or 0)
        except (TypeError, ValueError):
            continue
        if expires_at and expires_at < now - grace_seconds:
            shutil.rmtree(directory, ignore_errors=True)
            removed += 1
    return removed


def _maybe_prune_bridge():
    """Run prune_bridge at most once per hour, from whichever caller arrives first."""
    global _last_prune_monotonic
    now = time.monotonic()
    if now - _last_prune_monotonic < _PRUNE_INTERVAL_SECONDS:
        return 0
    if not _prune_lock.acquire(False):
        return 0
    try:
        if time.monotonic() - _last_prune_monotonic < _PRUNE_INTERVAL_SECONDS:
            return 0
        _last_prune_monotonic = time.monotonic()
        return prune_bridge()
    finally:
        _prune_lock.release()


def pending_requests(owner_id):
    rows = []
    if not bridge_root().exists():
        return rows
    _maybe_prune_bridge()
    now = time.time()
    # Cheap filter: a request file last written more than one wall plus
    # grace ago cannot be live — its wall is 120s from creation, and an
    # answer rewrites the file at answer time. Skip the parse entirely.
    cutoff = now - (AGENT_REQUEST_WALL_SECONDS + 60.0)
    for path in bridge_root().glob('*/request.json'):
        try:
            if path.stat().st_mtime < cutoff:
                continue
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
        if not error:
            # Cache the composed answer under its payload fingerprint so a
            # re-issued request (same payload, new id) can reuse it
            # immediately instead of being composed again against the wall.
            # Best-effort: _cache_answer swallows its own failures.
            _cache_answer(payload_fingerprint(row), content,
                          operation=str(row.get('operation', '')))
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
