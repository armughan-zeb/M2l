"""Local inference inbox for an external agent's own text/vision reasoning.

The host agent answers requests; this service never invokes its model provider
or borrows a ChatGPT login. All writes are correlated, bounded and atomic.
"""
from contextlib import contextmanager
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import threading
import time
import requests

from .job_context import current_job, check_job_active, remaining_seconds
from .pipeline_activity import record_activity
from .verdict_conflicts import (
    build_coherence_context,
    build_late_fatal_record,
    is_fatal_reject,
    record_late_fatal_reject,
    request_context_text,
    verdict_reason_coherence_issue,
)

logger = logging.getLogger(__name__)

AGENT_HANDLE = 'external-agent:'
AGENT_MODEL = 'external-agent'


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
    # A claim whose lease lapsed (owner died mid-work) releases the request
    # back to pending so another answerer can take it.
    if row.get('state') == 'claimed' and not _claim_is_live(row):
        if not locked:
            with _request_lock(path):
                return read_request(request_id, owner_id, locked=True)
        row['state'] = 'pending'
        row.pop('claimed_by', None)
        row.pop('claimed_at', None)
        row.pop('claim_expires_at', None)
        atomic_json(path, row)
    # Deadline is authoritative, even if the generation process exited.
    if row['state'] in ('pending', 'claimed') and row['expires_at'] <= time.time():
        if not locked:
            with _request_lock(path):
                return read_request(request_id, owner_id, locked=True)
        if path.with_name('response.json').exists():
            row['state'] = 'answered'
        else:
            row['state'] = 'expired'
        atomic_json(path, row)
    return row


# --- Request claims (duplicate-answer race) ---
# 2026-11-21, job a42a1b7b: the daemon's resident inbox, the pool workers
# (via the spool transport) and hook-woken agents all picked up the same
# visual_review requests at once. The bridge had no ownership state, so
# every answerer composed in parallel and complete_request's
# first-write-wins refused all but the fastest with "already answered" —
# 6 answers lost in 2.5 min (07:57:24-07:59:46 UTC), plus "Could not
# record" noise when the daemon tried to file each failure. A claim marks
# a pending request as owned by one answerer for a lease; every picker
# skips live claims, and complete_request refuses a completion from
# anyone but the claim owner while the lease is live.
_CLAIM_LEASE_SECONDS = 900


def _claim_is_live(row, now=None):
    """True when row is claimed and its lease has not lapsed."""
    if row.get('state') != 'claimed':
        return False
    try:
        return float(row.get('claim_expires_at') or 0) > (time.time() if now is None else now)
    except (TypeError, ValueError):
        return False


def _claim_lease_seconds(row, lease_seconds, now):
    """Lease length in seconds, clamped to the request's remaining wall."""
    try:
        requested = float(lease_seconds or _CLAIM_LEASE_SECONDS)
    except (TypeError, ValueError):
        requested = float(_CLAIM_LEASE_SECONDS)
    remaining = float(row.get('expires_at') or 0) - now
    return max(1.0, min(requested, remaining)) if remaining > 0 else 1.0


def claim_request(request_id, owner_id, claimant, lease_seconds=_CLAIM_LEASE_SECONDS):
    """Claim a pending request for one answerer.

    Returns True when the claim was taken (or refreshed by the same
    claimant), False when the request is already claimed under a live
    lease, answered, or expired. Atomic under the request lock. The lease
    is capped by the request's own expiry so a claim can never outlive
    the request.
    """
    path = _request_path(request_id)
    if not path.is_file():
        raise ValueError('Agent request was not found.')
    with _request_lock(path):
        row = json.loads(path.read_text(encoding='utf-8'))
        if not secrets.compare_digest(str(row.get('owner_id', '')), str(owner_id)):
            raise PermissionError('This agent request belongs to another account.')
        now = time.time()
        if row.get('state') == 'pending' and row.get('expires_at', 0) <= now:
            row['state'] = 'expired'
            atomic_json(path, row)
            return False
        if row.get('state') == 'claimed' and _claim_is_live(row, now):
            # Same claimant re-claiming refreshes its own lease.
            if str(row.get('claimed_by') or '') == str(claimant):
                row['claim_expires_at'] = now + _claim_lease_seconds(row, lease_seconds, now)
                atomic_json(path, row)
                return True
            return False
        if row.get('state') not in ('pending', 'claimed'):
            return False
        row['state'] = 'claimed'
        row['claimed_by'] = str(claimant)
        row['claimed_at'] = now
        row['claim_expires_at'] = now + _claim_lease_seconds(row, lease_seconds, now)
        atomic_json(path, row)
        return True


def release_claim(request_id, owner_id, claimant=None):
    """Release a claim back to pending. Returns True when released.

    A claimant may only release its own claim; with no claimant given,
    any live claim on the request is released (used when the holder is
    known to be gone, e.g. the daemon's transport died).
    """
    path = _request_path(request_id)
    if not path.is_file():
        raise ValueError('Agent request was not found.')
    with _request_lock(path):
        row = json.loads(path.read_text(encoding='utf-8'))
        if not secrets.compare_digest(str(row.get('owner_id', '')), str(owner_id)):
            raise PermissionError('This agent request belongs to another account.')
        if row.get('state') != 'claimed':
            return False
        if claimant is not None and str(row.get('claimed_by') or '') != str(claimant):
            return False
        row['state'] = 'pending'
        row.pop('claimed_by', None)
        row.pop('claimed_at', None)
        row.pop('claim_expires_at', None)
        atomic_json(path, row)
        return True


# --- Answer cache + bridge prune (inbox reliability) ---

# A re-issued request carries the same payload under a new id (verified by
# fingerprint 2026-11-14: identical messages, operation and response
# format). Caching a composed answer under its payload fingerprint lets a
# re-issue be answered immediately instead of being composed again against
# the answer wall. The cache lives in one file at the bridge root so it is
# never scanned by pending_requests() and never pruned with the dirs.
_ANSWER_CACHE_FILE = '_answer_cache.json'
_ANSWER_CACHE_MAX_ENTRIES = 200
# How often a waiting agent_post records inference activity and re-checks
# the answer cache for a twin's answer (seconds).
_ACTIVITY_RECORD_SECONDS = 30.0
_PRUNE_GRACE_SECONDS = 24 * 60 * 60
_PRUNE_INTERVAL_SECONDS = 3600
_last_prune_monotonic = 0.0
_prune_lock = threading.Lock()


def _fingerprint_image_part(part):
    """Canonical form of one message part for fingerprinting.

    A vision payload is filed with inline data-URI images but STORED with
    ``local_image`` parts pointing at per-request files, so the raw
    messages never matched a stored twin (and the answer cache never hit
    for vision). Normalizing both forms to the image bytes' hash makes a
    filed payload and its stored twin fingerprint identically. A part
    whose bytes cannot be read falls back to its path/URI text — today's
    behavior — so fingerprinting never breaks a filing.
    """
    if not isinstance(part, dict):
        return part
    part_type = part.get('type')
    if part_type == 'image_url':
        source = part.get('image_url', {})
        url = source.get('url', '') if isinstance(source, dict) else str(source)
        if isinstance(url, str) and url.startswith('data:image/') and ';base64,' in url:
            try:
                data = base64.b64decode(url.split(';base64,', 1)[1], validate=True)
                return {'type': 'image', 'image_sha256': hashlib.sha256(data).hexdigest(),
                        'detail': source.get('detail', 'auto') if isinstance(source, dict) else 'auto'}
            except Exception:
                pass
        return {'type': 'image_url', 'image_url': url,
                'detail': source.get('detail', 'auto') if isinstance(source, dict) else 'auto'}
    if part_type == 'local_image':
        image_path = str(part.get('path') or '')
        try:
            data = open(image_path, 'rb').read()
            return {'type': 'image', 'image_sha256': hashlib.sha256(data).hexdigest(),
                    'detail': part.get('detail', 'auto')}
        except Exception:
            return {'type': 'local_image', 'path': image_path,
                    'detail': part.get('detail', 'auto')}
    return part


def _fingerprint_messages(messages):
    """Messages with image parts normalized to content hashes.

    Text parts pass through unchanged. Image parts become their canonical
    form (see _fingerprint_image_part), so the filed and stored shapes of
    the same vision payload fingerprint identically.
    """
    normalized = []
    for message in messages or []:
        if not isinstance(message, dict):
            normalized.append(message)
            continue
        copied = dict(message)
        if isinstance(message.get('content'), list):
            copied['content'] = [
                _fingerprint_image_part(part)
                if isinstance(part, dict) and part.get('type') in ('image_url', 'local_image')
                else part
                for part in message['content']
            ]
        normalized.append(copied)
    return normalized


def payload_fingerprint(row):
    """Stable identity of what an answer must contain.

    Covers everything the answer depends on — job, operation, kind,
    messages, response format — and nothing that changes on re-issue
    (id, created/expires times). Image parts are normalized to content
    hashes, so a filed payload matches its stored twin.
    """
    canonical = {
        'job_id': row.get('job_id', ''),
        'operation': row.get('operation', ''),
        'kind': row.get('kind', ''),
        'messages': _fingerprint_messages(row.get('messages', [])),
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
    for path in bridge_root().glob('*/request.json'):
        try:
            row = read_request(path.parent.name, owner_id)
            if row['state'] == 'pending':
                rows.append({key: row[key] for key in ('id', 'job_id', 'operation', 'kind', 'created_at', 'expires_at')})
        except (OSError, ValueError, PermissionError, KeyError):
            continue
    return sorted(rows, key=lambda row: row['created_at'])


def _record_late_fatal_reject(request_id, row, path, content):
    """Preserve a late fatal vision verdict in the conflict ledger.

    First-write-wins for the response itself is unchanged — the late answer
    is still refused. But a late verdict that fatally rejects the candidate
    (job 4f61cc9e: a REJECT arrived after an ACCEPT had already been stored
    and was silently dropped) is now recorded in _verdict_conflicts.json so
    the coordinator can veto the contested content later. Best-effort: any
    failure here is logged and swallowed, never breaking answer submission.
    """
    try:
        if isinstance(content, dict):
            verdict = content
        elif isinstance(content, str):
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                return
            verdict = parsed
        else:
            return
        if not is_fatal_reject(verdict):
            return
        # Reasoning-coherence gate (run 9): a fatal verdict whose
        # reasoning is cross-contaminated (e.g. "the yacht from the
        # earlier clip review") is NOT filed — the late answer is still
        # refused as before, but no ledger entry is created, so a later
        # attempt re-reads the candidate live instead of vetoing it on
        # contaminated reasoning.
        try:
            context = build_coherence_context(
                extra_text=request_context_text(row))
            issue = verdict_reason_coherence_issue(verdict, context)
        except Exception:
            issue = None
        if issue is not None:
            logger.info(
                'verdict-conflict ledger filing skipped for %s: incoherent '
                'reasoning (%s)', request_id, issue)
            return
        winner = None
        reply_path = path.with_name('response.json')
        if reply_path.exists():
            try:
                reply = json.loads(reply_path.read_text(encoding='utf-8'))
                raw_winner = reply.get('content')
                winner = json.loads(raw_winner) if isinstance(raw_winner, str) else raw_winner
                if not isinstance(winner, dict):
                    winner = None
            except (OSError, ValueError):
                winner = None
        record = build_late_fatal_record(request_id, row, verdict, winner=winner)
        record_late_fatal_reject(bridge_root(), record)
    except Exception:
        logger.warning('verdict-conflict ledger write failed for %s', request_id, exc_info=True)


def complete_request(request_id, owner_id, content, error='', claimant=None):
    path = _request_path(request_id)
    if not path.exists():
        raise ValueError('Agent request was not found.')
    with _request_lock(path):
        row = read_request(request_id, owner_id, locked=True)
        claimant = str(claimant) if claimant is not None else str(owner_id)
        if row.get('state') == 'claimed' and _claim_is_live(row) and str(row.get('claimed_by') or '') != claimant:
            # Owned by another live answerer: refuse with a distinct error
            # so the loser can tell a race loss from an expiry. Preserve a
            # late fatal verdict in the conflict ledger (same as the
            # already-answered path) but do NOT cache the loser's answer:
            # the winner's completion already cached (or will cache) its
            # own verdict under this payload fingerprint.
            if not error and content is not None:
                _record_late_fatal_reject(request_id, row, path, content)
            raise ValueError('This request is claimed by another worker.')
        if row['state'] not in ('pending', 'claimed') or path.with_name('response.json').exists():
            # A late fatal vision verdict is preserved in the conflict
            # ledger before the refusal below — first-write-wins for the
            # response is preserved, only the discarded verdict is saved.
            # This runs for both already-answered and expired requests.
            if not error and content is not None:
                _record_late_fatal_reject(request_id, row, path, content)
            # A late answer is not wasted: if it is valid content for an
            # expired (not already-answered) request, cache it so the next
            # re-issue of the same payload reuses it immediately instead of
            # waiting for a fresh composition that may expire again. The
            # caller still gets the expired error — this only saves the work.
            # Reuse re-validates the cached content against the new
            # request's schema, so caching here needs only basic sanity.
            if (row['state'] != 'pending'
                    and not path.with_name('response.json').exists()
                    and not error and content is not None):
                try:
                    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                    if text.strip() and len(text.encode('utf-8')) <= 2_000_000:
                        _cache_answer(payload_fingerprint(row), text,
                                      operation=str(row.get('operation', '')))
                except Exception:
                    pass
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
        row.pop('claimed_by', None)
        row.pop('claimed_at', None)
        row.pop('claim_expires_at', None)
        atomic_json(path, row)
        if not error:
            # Cache the composed answer under its payload fingerprint so a
            # re-issued request (same payload, new id) can reuse it
            # immediately instead of being composed again against the wall.
            # Best-effort: _cache_answer swallows its own failures.
            _cache_answer(payload_fingerprint(row), content,
                          operation=str(row.get('operation', '')))
    return {'success': True, 'request_id': request_id}


def _agent_wall_seconds():
    """Floor for one agent inbox round-trip, in seconds.

    The hook polls every 60s and a grounded answer takes minutes to compose,
    so a short wall expires before the answer can arrive. Default 3600s
    (one hour): the wall is a deadline, not a delay — an answer ends the
    wait early. VIDZEO_AGENT_MIN_WALL_SECONDS overrides it.
    """
    try:
        return max(0.0, float(os.environ.get('VIDZEO_AGENT_MIN_WALL_SECONDS', '3600') or 3600))
    except (TypeError, ValueError):
        return 3600.0


# Serializes the scan-then-mint in agent_post within this process, so two
# threads filing the same payload at once cannot both miss the twin and
# mint duplicates. Cross-process races can still mint a duplicate (the
# twin scan below then joins it); that is today's behavior, not a new one.
_mint_lock = threading.Lock()


def find_pending_twin(*, owner, job_id, operation, kind, messages, response_format):
    """Return the id of an unexpired PENDING twin for the same payload, else None.

    A twin is a request for the same job + operation + payload fingerprint
    that is still pending: a resume/refile of the same payload must wait on
    it instead of minting a duplicate the operator has to answer twice.
    Picks the twin with the latest expiry when several exist.

    The fingerprint is computed on the payload as filed (before local-image
    copies): the copy rewrites data-URI images to per-request file paths,
    which would make two identical vision filings look different. The
    fingerprint normalizes image parts to content hashes, so a filed
    vision payload matches its stored twin exactly as text payloads do.
    For text payloads (direction, queries, beats) the filed and stored
    messages are identical, so twins match.

    Old dirs are skipped by mtime without parsing: a request file untouched
    for longer than the longest possible wall cannot still be pending, so
    its expiry is necessarily past.
    """
    fingerprint = payload_fingerprint({
        'job_id': job_id,
        'operation': operation,
        'kind': kind,
        'messages': messages,
        'response_format': response_format or {},
    })
    root = bridge_root()
    if not root.exists():
        return None
    now = time.time()
    try:
        lookback = max(7200.0, _agent_wall_seconds()) + 300.0
    except Exception:
        lookback = 7500.0
    best_id = None
    best_expires_at = 0.0
    for path in root.glob('*/request.json'):
        try:
            if path.stat().st_mtime < now - lookback:
                continue
            row = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if row.get('state') not in ('pending', 'claimed'):
            continue
        # A claimed twin is still joinable: its answer is coming, and
        # minting a duplicate would set up the same race the claim exists
        # to prevent. (An expired claim is released to pending on read.)
        try:
            expires_at = float(row.get('expires_at') or 0)
        except (TypeError, ValueError):
            continue
        if expires_at <= now or expires_at <= best_expires_at:
            continue
        if str(row.get('owner_id', '')) != str(owner):
            continue
        if payload_fingerprint(row) != fingerprint:
            continue
        best_id = str(row.get('id') or path.parent.name)
        best_expires_at = expires_at
    return best_id


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


def _wait_for_bridge_response(request_id, reply_path, deadline, *, operation='', kind=''):
    """Wait until ``deadline`` for ``reply_path``; return the response or None on expiry.

    Shared by the own-request wait and the pending-twin wait in agent_post.
    Records inference activity while waiting (so a live brain is never
    mistaken for a stall) and picks up a twin's cached answer mid-wait.
    Raises AgentBridgeError on an operator error reply; job cancellation
    and deadline expiry propagate as today (expiry returns None).
    """
    last_activity_at = 0.0
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
        now = time.monotonic()
        if now - last_activity_at >= _ACTIVITY_RECORD_SECONDS:
            record_activity("inference")
            last_activity_at = now
            # A twin request for the same payload may have been answered
            # while this one waited (the director and the query planner
            # file identical payloads concurrently). Reuse it instead
            # of waiting out the wall for a second copy of the answer.
            try:
                reuse_cached_answer(request_id)
            except Exception:
                pass
        time.sleep(min(.1, max(.001, deadline - time.monotonic())))
    return None


def agent_post(payload, timeout, *, operation='', kind='text', handle='', lane=None):
    context = current_job()
    owner = str(context.get('agent_owner_id') or (handle[len(AGENT_HANDLE):] if handle.startswith(AGENT_HANDLE) else ''))
    if not owner:
        raise AgentBridgeError('Pair a local agent before starting an agent-powered job.')
    # An external agent answers through a polling inbox: the watcher polls
    # every 60s and reading the prompt plus composing a schema-valid answer
    # takes minutes, so the fast-API timeouts callers pass (20-300s) expire
    # before the answer can arrive. Floor the wall for agent requests; the
    # job's remaining budget still caps it. VIDZEO_AGENT_MIN_WALL_SECONDS
    # tunes the floor (default 3600s).
    min_wall = _agent_wall_seconds()
    effective_timeout = max(float(timeout), min_wall)
    wall = min(effective_timeout, remaining_seconds(effective_timeout))
    deadline = time.monotonic() + wall
    messages = payload.get('messages', [])
    response_format = payload.get('response_format') or {}

    def _mint_fresh_request():
        """Create a new pending request for this payload; return (id, reply_path)."""
        request_id = secrets.token_hex(16)
        path = _request_path(request_id)
        path.parent.mkdir(parents=True)
        created_at = time.time()
        row = {'id': request_id, 'owner_id': owner, 'job_id': context.get('job_id', ''),
               'attempt_id': context.get('attempt_id', ''), 'kind': kind, 'operation': operation,
               # Vision lanes (C8): observability only — the answering worker
               # reads messages/response_format and ignores extra row keys
               # (the inbox listing whitelists its keys; twin/cache
               # fingerprints use operation+messages+format only).
               'lane': str(lane or '').strip().lower() or None,
               'state': 'pending', 'created_at': created_at, 'expires_at': created_at + wall,
               'messages': _messages_with_local_images(messages, path.parent),
               'response_format': response_format,
               'max_tokens_hint': payload.get('max_tokens'),
               'instructions': 'Use your own reasoning and vision tools. Ground facts in the supplied context. Return the requested schema. Never call OpenRouter for this request.'}
        atomic_json(path, row)
        # A re-issued request (same payload, new id — the pipeline refiles the
        # same beats/queries/direction on every attempt) can reuse a previously
        # composed answer immediately instead of waiting for a fresh one that
        # may expire again. Reuse re-validates against this request's schema;
        # a stale or mismatched cache entry must never break the wait.
        try:
            reuse_cached_answer(request_id)
        except Exception:
            pass
        return request_id, path.with_name('response.json'), path

    # Pending-twin dedupe: a resume/refile of the same payload waits on the
    # twin already pending instead of minting a duplicate the operator must
    # answer twice. The twin is not ours to expire: if it expires
    # unanswered while our wall remains, re-scan for a replacement twin
    # (another waiter may have minted one) or file a fresh request below.
    # The twin scan AND the mint happen under one _mint_lock hold, so
    # several waiters queued on one expired twin cannot each mint a fresh
    # request when it expires. At most two expired twins are joined; after
    # that a fresh request is minted even if a twin is still pending.
    request_id = reply_path = path = None
    for _twin_join in range(3):
        with _mint_lock:
            twin_id = find_pending_twin(
                owner=owner, job_id=context.get('job_id', ''), operation=operation,
                kind=kind, messages=messages, response_format=response_format,
            )
            if twin_id is None or _twin_join >= 2:
                request_id, reply_path, path = _mint_fresh_request()
                twin_id = None
        if twin_id is None:
            break
        twin_path = _request_path(twin_id)
        twin_reply_path = twin_path.with_name('response.json')
        try:
            reuse_cached_answer(twin_id)
        except Exception:
            pass
        twin_deadline = deadline
        try:
            twin_row = json.loads(twin_path.read_text(encoding='utf-8'))
            twin_deadline = min(
                deadline,
                time.monotonic() + max(0.0, float(twin_row.get('expires_at') or 0) - time.time()),
            )
        except (OSError, ValueError, TypeError):
            pass
        response = _wait_for_bridge_response(
            twin_id, twin_reply_path, twin_deadline, operation=operation, kind=kind)
        if response is not None:
            return response
        if time.monotonic() >= deadline:
            raise AgentBridgeError(f'Muse request {twin_id} ({operation or kind}) expired after {wall:.1f}s.')
    # Preparation-stall detector (queue_manager heartbeat) only sees lane
    # activity from web/vision/youtube. A bridge wait is bounded by the
    # request deadline (floor 3600s) but shows no display progress, so an
    # unanswered wait longer than preparation_stall_seconds (300s) looked
    # like a dead job and got it paused mid-preparation — four times on
    # one 5-min test job. Record inference activity while waiting so a
    # live brain is never mistaken for a stall (see _wait_for_bridge_response).
    try:
        response = _wait_for_bridge_response(
            request_id, reply_path, deadline, operation=operation, kind=kind)
        if response is not None:
            return response
        raise AgentBridgeError(f'Muse request {request_id} ({operation or kind}) expired after {wall:.1f}s.')
    finally:
        with _request_lock(path):
            saved = json.loads(path.read_text(encoding='utf-8'))
            if saved['state'] == 'pending':
                saved['state'] = 'expired'
                atomic_json(path, saved)
