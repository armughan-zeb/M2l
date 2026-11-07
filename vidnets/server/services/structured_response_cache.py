"""Reuse only successful schema-valid text responses inside the same project.

Cache prompts as hashes, never plaintext or credentials. New projects retain
independent direction; changed prompts, models, schemas or controls invalidate.
"""
import functools
from collections import OrderedDict
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid
import weakref
from contextlib import contextmanager

_LOCKS = weakref.WeakValueDictionary()
_GUARD = threading.Lock()
_VERSION = 2
_MEMORY = OrderedDict()
_MEMORY_BYTES = 0
_MAX_MEMORY_BYTES = 4 * 1024 * 1024
_MAX_MEMORY_ENTRIES = 128
_TTL = 7 * 86400


class _Flight:
    def __init__(self):
        self.lock = threading.Lock()
        self.generation = 0
        self.failure = None


def _memory_get(key):
    with _GUARD:
        value = _MEMORY.get(key)
        if value is not None:
            _MEMORY.move_to_end(key)
            return dict(value)
    return None


def _memory_put(key, value):
    global _MEMORY_BYTES
    size = len(value['content'].encode('utf-8'))
    if size > _MAX_MEMORY_BYTES:
        return
    with _GUARD:
        previous = _MEMORY.pop(key, None)
        if previous:
            _MEMORY_BYTES -= previous['_bytes']
        _MEMORY[key] = {**value, '_bytes': size}
        _MEMORY_BYTES += size
        while len(_MEMORY) > _MAX_MEMORY_ENTRIES or _MEMORY_BYTES > _MAX_MEMORY_BYTES:
            _, removed = _MEMORY.popitem(last=False)
            _MEMORY_BYTES -= removed['_bytes']


@contextmanager
def _bounded_cache_lock(flight, seconds=360):
    from .job_context import check_job_active, remaining_seconds
    deadline = time.monotonic() + min(seconds, remaining_seconds(seconds))
    observed_generation = flight.generation
    while not flight.lock.acquire(timeout=.1):
        check_job_active()
        if time.monotonic() >= deadline:
            raise TimeoutError('An identical model request is still pending; retry from saved work.')
    try:
        if flight.failure is not None and observed_generation < flight.generation:
            error_type, arguments = flight.failure
            raise error_type(*arguments)
        try:
            yield
            flight.failure = None
        except BaseException as exc:
            # Share a failed owner's outcome only with existing waiters.
            # A genuinely new request observes the new generation and retries.
            # Store no traceback, avoiding a cycle retaining request contents.
            flight.failure = (type(exc), exc.args)
            flight.generation += 1
            raise
    finally:
        flight.lock.release()


def cache_structured_response(function):
    signature = inspect.signature(function)

    @functools.wraps(function)
    def wrapped(self, *args, **kwargs):
        from .job_context import current_job, check_job_active
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        values = dict(bound.arguments)
        schema = values.get('json_schema')
        context = current_job()
        directory = context.get('llm_cache_dir')
        if (not directory or not isinstance(schema, dict) or not values.get('json_mode')
                or str(os.environ.get('VIDZEO_RESPONSE_CACHE', '1')).lower() in {'0', 'false', 'off'}):
            return function(self, *args, **kwargs)
        check_job_active()
        wait_seconds = values.get('total_timeout_seconds') or values.get('timeout_seconds') or 360
        try:
            wait_seconds = float(wait_seconds)
            wait_seconds = max(.01, min(1800., wait_seconds)) if math.isfinite(wait_seconds) else 360.
        except (ValueError, TypeError):
            wait_seconds = 360.
        # Scheduling/retry budgets change on resume, but do not change content.
        for key in ('self', 'timeout_seconds', 'total_timeout_seconds',
                    'max_attempts', 'primary_timeout_share'):
            values.pop(key, None)
        identity = {'version': _VERSION, 'arguments': values,
                    'models': self.model_chain,
                    'provider': getattr(self, 'provider', 'openrouter'),
                    'agent_owner_id': context.get('agent_owner_id', '') if getattr(self, 'provider', '') == 'external_agent' else '',
                    'account_id': (context.get('chatgpt_account_id') or getattr(self, 'api_key', '')) if getattr(self, 'provider', '') == 'chatgpt' else '',
                    'credential_scope': hashlib.sha256(str(getattr(self, 'api_key', '')).encode()).hexdigest(),
                    'cost_profile': context.get('cost_profile'),
                    'thinking': os.environ.get('VIDZEO_LLM_THINKING', '0')}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False,
                                           default=str).encode()).hexdigest()
        path = Path(directory) / (digest + '.json')
        with _GUARD:
            lock = _LOCKS.get(str(path))
            if lock is None:
                lock = _Flight()
                _LOCKS[str(path)] = lock
        with _bounded_cache_lock(lock, wait_seconds):
            check_job_active()
            from .openrouter_llm import _json_schema_error, _normalise_strict_json_schema
            effective_schema = _normalise_strict_json_schema(schema)
            try:
                cached = _memory_get(str(path))
                if cached is None:
                    cached = json.loads(path.read_text(encoding='utf-8'))
                content = cached['content']
                if (cached.get('version') == _VERSION
                        and 0 <= time.time() - float(cached['created_at']) < _TTL
                        and not _json_schema_error(json.loads(content), effective_schema)):
                    self.last_model = cached.get('model') or self.model
                    self.attempt_history = [{'cache_hit': True, 'model': self.last_model,
                                             'operation': values.get('json_schema_name')}]
                    _memory_put(str(path), cached)
                    from .openrouter_usage import record_cache_hit
                    record_cache_hit(self.last_model, values.get('json_schema_name'))
                    return content
            except (OSError, ValueError, KeyError, TypeError):
                pass
            content = function(self, *args, **kwargs)
            check_job_active()
            try:
                if _json_schema_error(json.loads(content), effective_schema):
                    return content
                cached = {'version': _VERSION, 'created_at': time.time(),
                          'model': self.last_model, 'content': content}
                # Disk-cache trouble must not cause concurrent duplicate paid
                # requests. The bounded process cache can still reuse success.
                _memory_put(str(path), cached)
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
                try:
                    temporary.write_text(json.dumps(cached, ensure_ascii=False), encoding='utf-8')
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            except (OSError, ValueError, TypeError):
                pass
            return content
    return wrapped
