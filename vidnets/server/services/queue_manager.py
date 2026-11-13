# =============================================================================
# QUEUE MANAGER - Persistent Queue with Cancel/Resume Support
# =============================================================================
# This module manages the video generation queue with:
# - File-based persistence (queue.json)
# - Cancellation support via flags
# - Resume capability for interrupted jobs
# - Full configuration storage for each job

import os
import sys
import json
import uuid
import copy
import math
import re
import threading
from .job_context import JobThread
import time
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Callable, Any

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

# Add parent directories to path
SERVICES_DIR = Path(__file__).parent
SERVER_DIR = SERVICES_DIR.parent
BASE_DIR = SERVER_DIR.parent
sys.path.insert(0, str(SERVER_DIR))
from config import normalize_generation_config
from services.cancellation import CancellationContext, JobCancelled
from services.pipeline_contracts import MediaContractError
from services.delivery_budget import install_delivery_budget

# Queue file location
QUEUE_FILE = Path(
    os.environ.get("VIDZEO_QUEUE_FILE") or SERVER_DIR / "queue.json"
).expanduser().resolve()
MAX_FINISHED_HISTORY = 12
_RECOVERY_PAUSE_TEXT = {
    'delivery_deadline': 'Processing time limit reached. Click Retry to continue from saved work.',
    'preparation_stall': 'Preparation stalled. Click Retry to continue from saved work.',
}
_REDACTED_SECRET = ""
_SENSITIVE_CONFIG_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "auth_token",
    "authorization",
    "bearer",
    "secret",
    "password",
}


def _is_sensitive_key(key: Any) -> bool:
    lowered = str(key or "").strip().lower()
    return any(marker in lowered for marker in _SENSITIVE_CONFIG_KEYS)


def _redact_sensitive_data(value: Any, *, persistence: bool = False) -> Any:
    """Return a deep copy safe for queue persistence/API responses."""
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            hidden = _is_sensitive_key(key) or (key == '_export_usage_ticket' and not persistence)
            cleaned[key] = _REDACTED_SECRET if hidden else _redact_sensitive_data(item, persistence=persistence)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_redact_sensitive_data(item, persistence=persistence) for item in value]
    return value


def _contains_sensitive_value(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if _is_sensitive_key(key) and item:
                return True
            if _contains_sensitive_value(item):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_value(item) for item in value)
    return False


def _merge_generation_config(current: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a partial queue edit without conflating content and file names.

    ``normalize_generation_config`` intentionally fills compatibility aliases.
    Normalizing a partial edit before merging would therefore turn an
    ``output_name``-only edit into a synthetic ``content_title`` edit.  Merge
    first, while explicitly mirroring only aliases supplied by the caller,
    and normalize the complete result once.
    """
    merged = copy.deepcopy(dict(current or {}))
    incoming = copy.deepcopy(dict(updates or {}))
    merged.update(incoming)

    if 'content_title' in incoming:
        merged['title'] = incoming['content_title']
    elif 'title' in incoming:
        merged['content_title'] = incoming['title']

    if 'output_name' in incoming:
        merged['name'] = incoming['output_name']
    elif 'name' in incoming:
        merged['output_name'] = incoming['name']

    return normalize_generation_config(merged)


def _callback_config_for_run(
    stored_config: Dict[str, Any],
    *,
    job_id: str,
    run_id: str,
    attempt: int,
) -> Dict[str, Any]:
    """Build an isolated callback payload for one queue attempt.

    The queue owns these identifiers.  They override any stale IDs present in
    an imported config and are deliberately not written into the persisted
    user config, so retrying a job creates a fresh run without changing its
    stable queue/job identity.
    """
    callback_config = copy.deepcopy(normalize_generation_config(stored_config))
    callback_config['_queue_job_id'] = str(job_id)
    callback_config['_queue_run_id'] = str(run_id)
    callback_config['_queue_attempt'] = int(attempt)
    # Compatibility aliases consumed by the Storyboard V2 runtime and older
    # stages.  Queue-owned values must win over imported/stale config values.
    callback_config['queue_job_id'] = str(job_id)
    callback_config['job_id'] = str(job_id)
    callback_config['run_id'] = str(run_id)
    return callback_config

# =============================================================================
# QUEUE ITEM STATUS
# =============================================================================
class JobStatus:
    QUEUED = "queued"
    PROCESSING = "processing"
    CANCELLATION_REQUESTED = "cancellation_requested"
    PAUSED = "paused"      # Interrupted (server restart)
    CANCELLED = "cancelled"
    # A cancellation acknowledgement can time out while a child process is
    # still unwinding.  This is an internal transitional value; it must never
    # survive a backend restart as an active/stopping job.
    SHUTDOWN_TIMEOUT = "shutdown_timeout"
    COMPLETED = "completed"
    ERROR = "error"


# =============================================================================
# QUEUE MANAGER CLASS
# =============================================================================
class QueueManager:
    """
    Singleton Queue Manager that handles:
    - Persistent storage of queue items
    - Background worker for processing
    - Cancellation flags for stopping jobs
    - Progress tracking
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self.queue: List[Dict] = []
        self.active_jobs: Dict[str, Dict] = {}  # job_id -> cancellation context/state
        self.worker_thread: Optional[threading.Thread] = None
        self.worker_threads: List[threading.Thread] = []
        try:
            self.worker_count = max(1, min(2, int(os.environ.get('VIDZEO_QUEUE_PREP_WORKERS', '2') or 2)))
        except (TypeError, ValueError):
            self.worker_count = 2
        self.worker_running = False
        self._queue_lock = threading.Lock()
        self._persistence_lock = threading.Lock()
        self._queue_condition = threading.Condition(self._queue_lock)
        self._queue_needs_secret_scrub = False
        self._queue_needs_config_migration = False
        self._save_tick = 0
        self._full_backup_interval = 15
        # Adjustable in fast queue tests; these remain internal timing values.
        self._progress_heartbeat_poll_seconds = 5.0
        self._progress_heartbeat_idle_seconds = 7.0
        self._progress_heartbeat_emit_seconds = 7.0
        
        # Load queue from disk
        self._load_queue()
        
        # Mark any 'processing' jobs as 'paused' (server was restarted)
        self._recover_interrupted_jobs()
        self._compact_history()
        if self._queue_needs_secret_scrub or self._queue_needs_config_migration:
            if self._queue_needs_secret_scrub:
                print("Sensitive queue config fields scrubbed from disk")
            if self._queue_needs_config_migration:
                print("Legacy queue provider settings normalized to Audiogram")
            self._save_queue()

        print(f"📋 Queue Manager initialized with {len(self.queue)} items")
    
    # =========================================================================
    # PERSISTENCE
    # =========================================================================
    
    def _load_queue(self):
        """Load queue from JSON file"""
        if QUEUE_FILE.exists():
            last_error = None
            for attempt in range(3):
                try:
                    with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    self.queue = data.get('items', [])
                    self._scrub_loaded_queue_secrets()
                    print(f"Loaded {len(self.queue)} items from queue.json")
                    return
                except Exception as e:
                    last_error = e
                    time.sleep(0.05 * (attempt + 1))

            backup_file = QUEUE_FILE.with_suffix('.json.bak')
            if backup_file.exists():
                try:
                    with open(backup_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    self.queue = data.get('items', [])
                    self._scrub_loaded_queue_secrets()
                    print(f"Recovered {len(self.queue)} items from queue backup after load error: {last_error}")
                    return
                except Exception as backup_error:
                    print(f"Failed to load queue backup: {backup_error}")

        try:
            if QUEUE_FILE.exists():
                with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.queue = data.get('items', [])
                    self._scrub_loaded_queue_secrets()
                    print(f"📂 Loaded {len(self.queue)} items from queue.json")
            else:
                self.queue = []
        except Exception as e:
            print(f"⚠️ Failed to load queue: {e}")
            self.queue = []

    def _scrub_loaded_queue_secrets(self):
        """Remove persisted secrets from loaded queue items."""
        if _contains_sensitive_value(self.queue):
            self.queue = _redact_sensitive_data(self.queue, persistence=True)
            self._queue_needs_secret_scrub = True

        for item in self.queue:
            if not isinstance(item, dict) or not isinstance(item.get('config'), dict):
                continue
            original = item['config']
            normalized = normalize_generation_config(original)
            if normalized != original:
                item['config'] = normalized
                self._queue_needs_config_migration = True
    
    def _save_queue(self):
        """Save queue to JSON file.

        Snapshot queue state briefly under its state lock, then redact and
        serialize outside it so status polling is not held behind large job
        manifests. Progress writes are separately rate-limited by the worker.
        """
        tmp_file = None
        try:
            with self._persistence_lock:
                self._save_tick += 1
                do_full_backup = (self._save_tick % self._full_backup_interval) == 0
                with self._queue_lock:
                    queue_snapshot = copy.deepcopy(self.queue)
                data = {
                    'items': _redact_sensitive_data(queue_snapshot, persistence=True),
                    'last_updated': datetime.now().isoformat()
                }
                # History can contain large completed manifests. Serialize,
                # flush and replace outside the state lock so UI polling and
                # cancellation remain responsive during slow disk writes.
                QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
                backup_file = QUEUE_FILE.with_suffix('.json.bak')
                # A fresh install has no writable server/ directory yet. Use
                # a unique sibling file so concurrent processes cannot steal
                # each other's pending rename.
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                        dir=QUEUE_FILE.parent, prefix='.queue-', suffix='.tmp', delete=False) as f:
                    tmp_file = Path(f.name)
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    if do_full_backup:
                        os.fsync(f.fileno())
                if do_full_backup and QUEUE_FILE.exists():
                    try:
                        with open(backup_file, 'w', encoding='utf-8') as backup:
                            json.dump(data, backup, indent=2, ensure_ascii=False)
                            backup.flush()
                            os.fsync(backup.fileno())
                    except OSError:
                        pass
                # On Windows, os.replace can fail with WinError 5 if the file
                # is briefly locked by another process. Retry with backoff.
                _replace_done = False
                for _attempt in range(5):
                    try:
                        os.replace(tmp_file, QUEUE_FILE)
                        _replace_done = True
                        break
                    except OSError:
                        import time as _t
                        _t.sleep(0.05 * (2 ** _attempt))
                if not _replace_done:
                    # Keep the last valid queue if Windows still holds it.
                    raise OSError('queue save failed after replace retries; previous queue preserved')
        except Exception as e:
            print(f"⚠️ Failed to save queue: {e}")
        finally:
            if tmp_file is not None:
                try:
                    tmp_file.unlink(missing_ok=True)
                except OSError:
                    pass
    
    def _recover_interrupted_jobs(self):
        """Handle jobs that were processing when the server last stopped.

        Default to pausing them so a large interrupted job cannot hijack the
        next short run on startup. Set VIDZEO_AUTO_REQUEUE_INTERRUPTED=1 to
        restore the old immediate-resume behavior.
        """
        auto_requeue = str(os.environ.get("VIDZEO_AUTO_REQUEUE_INTERRUPTED", "0")).strip().lower() in {
            "1", "true", "yes", "on"
        }
        recovered = 0
        recovered_processing = 0
        recovered_cancellations = 0
        recovered_timeouts = 0

        def record_recovery_event(item, text):
            clean_text = str(text or '').strip() or 'Recovered after backend restart'
            events = list(item.get('progress_events') or [])
            try:
                seq = max(
                    int(item.get('progress_seq') or 0),
                    max((int(event.get('seq') or 0) for event in events if isinstance(event, dict)), default=0),
                ) + 1
            except (TypeError, ValueError):
                seq = len(events) + 1
            try:
                progress = max(0, min(100, int(item.get('progress') or 0)))
            except (TypeError, ValueError):
                progress = 0
            item['progress_seq'] = seq
            events.append({'seq': seq, 'progress': progress, 'text': clean_text})
            item['progress_events'] = events[-12:]

        for item in self.queue:
            status = str(item.get('status') or '').strip().lower()
            if status == JobStatus.PROCESSING:
                if auto_requeue:
                    item['status'] = JobStatus.QUEUED
                    item['progress'] = 0
                    item['progress_text'] = 'Queued (auto-resumed after restart)'
                    item.pop('error', None)
                    item.pop('error_detail', None)
                else:
                    item['status'] = JobStatus.PAUSED
                    item['progress_text'] = 'Paused after restart - click Resume to continue'
                    item['paused_reason'] = 'backend_restart'
                record_recovery_event(item, item['progress_text'])
                recovered += 1
                recovered_processing += 1
            elif status == JobStatus.CANCELLATION_REQUESTED:
                pause_text = _RECOVERY_PAUSE_TEXT.get(item.get('paused_reason'))
                if pause_text and not item.get('cancel_requested'):
                    item['status'] = JobStatus.PAUSED
                    item['retryable'] = True
                    item['progress_text'] = pause_text
                    record_recovery_event(item, pause_text)
                    recovered += 1
                    recovered_processing += 1
                    continue
                # A cancellation request is persisted before the worker has
                # acknowledged it.  If the process starts with no active
                # worker state, the old request can otherwise remain in this
                # status forever and the UI will keep polling it as if work
                # were still running.  The intent was explicit, so finalize
                # it as a durable cancelled history item.
                item['status'] = JobStatus.CANCELLED
                item['progress_text'] = 'Cancelled after backend restart'
                item['cancelled_at'] = datetime.now().isoformat()
                item['cancel_recovered'] = True
                record_recovery_event(item, item['progress_text'])
                recovered += 1
                recovered_cancellations += 1
            elif status == JobStatus.SHUTDOWN_TIMEOUT:
                # No child process can still be tracked after a restart. Keep
                # the job resumable, but make the interruption visible rather
                # than presenting an indefinitely stopping card.
                item['status'] = JobStatus.PAUSED
                item['progress_text'] = 'Paused after cancellation cleanup timed out - click Resume to continue'
                item['paused_reason'] = 'cancellation_cleanup_timeout'
                item['cancel_recovered'] = True
                record_recovery_event(item, item['progress_text'])
                recovered += 1
                recovered_timeouts += 1

        if recovered > 0:
            action = "auto-requeued" if auto_requeue else "paused"
            print(
                f"[queue] startup recovery: {recovered} stale job(s) reconciled "
                f"(processing={recovered_processing} {action}, "
                f"cancellation={recovered_cancellations} cancelled, "
                f"shutdown_timeout={recovered_timeouts} paused)"
            )
            self._save_queue()

    def _compact_history(self):
        """
        Keep active jobs plus a small recent history so startup does not keep
        dragging along huge finished queues forever.
        """
        finished_statuses = {
            JobStatus.COMPLETED,
            JobStatus.CANCELLED,
            JobStatus.ERROR,
        }
        active_items = [item for item in self.queue if item.get('status') not in finished_statuses]
        finished_items = [item for item in self.queue if item.get('status') in finished_statuses]

        if len(finished_items) <= MAX_FINISHED_HISTORY:
            return

        def _finished_key(item):
            return (
                item.get('completed_at')
                or item.get('started_at')
                or item.get('created_at')
                or ''
            )

        finished_items = sorted(finished_items, key=_finished_key, reverse=True)
        compacted = active_items + finished_items[:MAX_FINISHED_HISTORY]
        removed = len(self.queue) - len(compacted)
        if removed <= 0:
            return

        self.queue = compacted
        print(f"🧹 Compacted queue history: removed {removed} old finished items")
        self._save_queue()
    
    # =========================================================================
    # QUEUE OPERATIONS
    # =========================================================================
    
    def add_item(self, config: Dict) -> Dict:
        """
        Add a new item to the queue.
        
        Args:
            config: Full job configuration (title, voice, settings, etc.)
        
        Returns:
            The created queue item with ID
        """
        config = normalize_generation_config(config)
        item = {
            'id': str(uuid.uuid4())[:8],  # Short unique ID
            'created_at': datetime.now().isoformat(),
            'status': JobStatus.QUEUED,
            'progress': 0,
            'progress_text': 'Queued',
            'progress_seq': 1,
            'progress_events': [
                {'seq': 1, 'progress': 0, 'text': 'Queued'}
            ],
            'type': config.get('type', 'long'),  # 'long' or 'short'
            'config': config  # Store full configuration
        }
        
        with self._queue_lock:
            self.queue.append(item)
        with self._queue_condition:
            self._queue_condition.notify_all()
        
        self._save_queue()
        print(f"➕ Added job {item['id']}: {config.get('title', 'Script-based')}")
        
        return _redact_sensitive_data(item)
    
    def get_item(self, job_id: str) -> Optional[Dict]:
        """Get a queue item by ID"""
        for item in self.queue:
            if item['id'] == job_id:
                return item
        return None
    
    def update_item(self, job_id: str, updates: Dict) -> bool:
        """
        Update a queue item's configuration.
        Only works for 'queued' or 'paused' items.
        """
        if not isinstance(updates, dict) or ('config' in updates and not isinstance(updates['config'], dict)):
            return False
        # The worker claims queued items under this same lock. Check and edit
        # together so the accepted config is the one the worker snapshots.
        updated = False
        with self._queue_lock:
            for item in self.queue:
                if item['id'] != job_id:
                    continue
                if item['status'] not in [JobStatus.QUEUED, JobStatus.PAUSED]:
                    print(f"⚠️ Cannot edit {job_id}: status is {item['status']}")
                    return False
                if 'config' in updates:
                    item['config'] = _merge_generation_config(item['config'], updates['config'])
                for key in ['status', 'progress', 'progress_text']:
                    if key in updates:
                        item[key] = updates[key]
                updated = True
                break
        if updated:
            # Persistence snapshots acquire the queue lock independently.
            self._save_queue()
            with self._queue_condition:
                self._queue_condition.notify_all()
            print(f"✏️ Updated job {job_id}")
        return updated
    
    def _request_cancellation(self, job_id: str, *, remove_after: bool = True) -> Dict[str, Any]:
        context = None
        removed_config = None
        with self._queue_lock:
            active = self.active_jobs.get(job_id)
            item = next((row for row in self.queue if row.get('id') == job_id), None)
            if active is not None:
                context = active['context']
                active['remove_after_cancel'] = remove_after
                active['requested_at'] = datetime.now().isoformat()
                if item is not None:
                    item['status'] = JobStatus.CANCELLATION_REQUESTED
                    item['cancel_requested'] = True
                    item['progress_text'] = 'Stopping…'
                result = {'found': True, 'active': True, 'pending': True, 'removed': False}
            elif item is None:
                cancelled_item = next(
                    (row for row in self.queue if row.get('id') == job_id),
                    None,
                )
                return {
                    'found': False,
                    'active': False,
                    'pending': False,
                    'removed': False,
                    'already_cancelled': bool(cancelled_item),
                }
            else:
                removed_config = dict(item.get('config') or {})
                self.queue = [row for row in self.queue if row.get('id') != job_id]
                result = {'found': True, 'active': False, 'pending': False, 'removed': True}
        if context is not None:
            self._save_queue()
            with self._queue_condition:
                self._queue_condition.notify_all()
            context.cancel('Job cancelled by user')
            print(f"🛑 Cancellation requested for job {job_id}")
        else:
            self._save_queue()
            with self._queue_condition:
                self._queue_condition.notify_all()
        if removed_config:
            from services.export_entitlements import finish_generation_export
            finish_generation_export(removed_config, False)
        return result

    def delete_item(self, job_id: str) -> Dict[str, Any]:
        """Request cancellation, deferring active-job removal until cleanup."""
        return self._request_cancellation(job_id, remove_after=True)
    
    def resume_item(self, job_id: str, config: Optional[Dict] = None) -> bool:
        """
        Resume a paused or error job by setting status back to queued.
        """
        updated_config = normalize_generation_config(config) if config is not None else None
        with self._queue_lock:
            item = self.get_item(job_id)
            if not item or item['status'] not in {JobStatus.PAUSED, JobStatus.ERROR, JobStatus.SHUTDOWN_TIMEOUT}:
                return False
            # A timed-out cleanup can be released only after the callback
            # returned and every registered worker stopped.
            active = self.active_jobs.get(job_id)
            if active is not None:
                if (active.get('cleanup_status') != 'shutdown_timeout'
                        or active.get('requested_at')
                        or not active['context'].wait_for_quiescence(0)):
                    return False
                active['context'].clear_registries()
                self.active_jobs.pop(job_id, None)
            if updated_config is not None:
                item['config'] = updated_config
            item.update(status=JobStatus.QUEUED, progress=0, progress_text='Queued (Resumed)', progress_seq=1,
                        progress_events=[{'seq': 1, 'progress': 0, 'text': 'Queued (Resumed)'}])
            for key in ('error', 'error_detail', 'paused_reason', 'cancel_recovered', 'retryable', 'memory_warning'):
                item.pop(key, None)
        self._save_queue()
        with self._queue_condition:
            self._queue_condition.notify_all()
        print(f"Resumed job {job_id}")
        return True

    def record_debugger_event(self, job_id: str, text: str) -> bool:
        """Append a stuck-job debugger diagnosis/action line to a job's
        progress history. Same event shape as the startup-recovery events:
        next seq, capped at the last 12. Best-effort visibility -- never
        raises, never touches the worker's own progress values."""
        clean_text = str(text or "").strip()
        if not clean_text:
            return False
        try:
            with self._queue_lock:
                item = next(
                    (row for row in self.queue if row.get("id") == job_id),
                    None,
                )
                if item is None:
                    return False
                events = list(item.get("progress_events") or [])
                try:
                    seq = max(
                        int(item.get("progress_seq") or 0),
                        max(
                            (int(event.get("seq") or 0) for event in events
                             if isinstance(event, dict)),
                            default=0,
                        ),
                    ) + 1
                except (TypeError, ValueError):
                    seq = len(events) + 1
                try:
                    progress = max(0, min(100, int(item.get("progress") or 0)))
                except (TypeError, ValueError):
                    progress = 0
                item["progress_seq"] = seq
                events.append(
                    {"seq": seq, "progress": progress, "text": clean_text}
                )
                item["progress_events"] = events[-12:]
            self._save_queue()
            return True
        except Exception as exc:
            print(f"⚠️ Debugger event record failed for {job_id}: {exc}")
            return False

    def request_scene_advance(
        self, job_id: str, scene_number: int, reason: str = ""
    ) -> bool:
        """Record a debugger scene advance on the queue item and register it
        with the in-process advance registry the media coordinator checks.
        The scene number is the 1-based number from the progress label
        (``Preparing media N/M``). Returns False when the job is unknown."""
        try:
            from services.job_debugger import set_scene_advance
        except Exception:
            set_scene_advance = None  # type: ignore[assignment]
        with self._queue_lock:
            item = next(
                (row for row in self.queue if row.get("id") == job_id),
                None,
            )
            if item is None:
                return False
            item["debugger_advance"] = {
                "scene_number": int(scene_number),
                "reason": str(reason or ""),
                "at": datetime.now().isoformat(),
            }
        self._save_queue()
        if set_scene_advance is not None:
            try:
                set_scene_advance(job_id, int(scene_number), str(reason or ""))
            except Exception as exc:
                print(f"⚠️ Debugger advance registry failed: {exc}")
        print(
            f"⏭️ Debugger advance requested for job {job_id} "
            f"scene {scene_number}: {reason}"
        )
        return True

    def clear_completed(self) -> int:
        """Remove all completed/cancelled/error items"""
        with self._queue_lock:
            original_len = len(self.queue)
            self.queue = [
                item for item in self.queue 
                if item['status'] not in [JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.ERROR]
            ]
            removed = original_len - len(self.queue)
        
        if removed > 0:
            self._save_queue()
        
        return removed
    
    def clear_all(self) -> Dict[str, Any]:
        """Clear queued history and request cancellation for active jobs."""
        contexts = []
        active_ids = []
        with self._queue_lock:
            for job_id, active in self.active_jobs.items():
                active['remove_after_cancel'] = True
                active['requested_at'] = datetime.now().isoformat()
                active_ids.append(job_id)
                contexts.append(active['context'])
                for item in self.queue:
                    if item.get('id') == job_id:
                        item['status'] = JobStatus.CANCELLATION_REQUESTED
                        item['cancel_requested'] = True
                        item['progress_text'] = 'Stopping…'
            queued_removed = sum(1 for item in self.queue if item.get('id') not in active_ids)
            self.queue = [item for item in self.queue if item.get('id') in active_ids]
        self._save_queue()
        for context in contexts:
            context.cancel('Queue cleared by user')
        with self._queue_condition:
            self._queue_condition.notify_all()
        print(f"🧹 Cleared {queued_removed} queued items; stopping {len(active_ids)} active job(s)")
        return {
            'removed': queued_removed,
            'cancel_requested': active_ids,
            'pending': active_ids,
        }
    
    @staticmethod
    def _compact_status_item(item: Dict[str, Any]) -> Dict[str, Any]:
        """Trim completed manifests for fast progress polling."""
        compact = dict(item)
        result = compact.get("result")
        if not isinstance(result, dict):
            return compact
        result_summary = {
            key: result.get(key)
            for key in (
                "success", "video", "video_url", "video_path", "video_path_disk",
                "manifest_path", "duration", "name", "content_title",
                "output_package_dir", "checkpoint_dir",
            )
            if key in result
        }
        manifest = result.get("component_manifest")
        if isinstance(manifest, dict):
            result_summary["component_manifest"] = {
                key: manifest.get(key)
                for key in (
                    "name", "duration", "fps", "source_accounting",
                    "terminal_accounting", "pipeline_quality_status",
                    "youtube_coverage", "youtube_coverage_warning",
                    "semantic_manifest_metrics", "media_shot_plan",
                )
                if key in manifest
            }
        compact["result"] = result_summary
        return compact

    def get_status(self, compact: bool = False, owner_id: Optional[str] = None) -> Dict:
        """Get queue status for API; compact mode keeps polling responsive."""
        with self._queue_lock:
            if compact:
                # Do not deep-copy the full historical manifests before
                # trimming them. That was the remaining source of latency on
                # the polling endpoint even after the response was compacted.
                status_fields = (
                    'id', 'created_at', 'started_at', 'completed_at', 'status',
                    'progress', 'progress_text', 'progress_seq', 'progress_events',
                    'type', 'error', 'error_detail', 'paused_reason',
                    'cancel_requested', 'retryable', 'memory_warning',
                    'checkpoint_dir', 'edit_plan_path', 'last_run_id', 'config', 'result',
                )
                items = []
                for raw_item in self.queue:
                    if not isinstance(raw_item, dict):
                        continue
                    if owner_id is not None and (raw_item.get('config') or {}).get('auth_user_id') != owner_id:
                        continue
                    item = {
                        key: raw_item.get(key)
                        for key in status_fields
                        if key in raw_item
                    }
                    if isinstance(item.get('config'), dict):
                        item['config'] = _redact_sensitive_data(item['config'])
                    items.append(_redact_sensitive_data(self._compact_status_item(item)))
            else:
                items = _redact_sensitive_data([item for item in self.queue if owner_id is None or
                    (item.get('config') or {}).get('auth_user_id') == owner_id])
            visible_ids = {item['id'] for item in items}
            active = {
                job_id: {
                    'run_id': state.get('run_id'),
                    'attempt': state.get('attempt'),
                    'cancellation_requested': bool(state.get('context') and state['context'].cancelled()),
                    'requested_at': state.get('requested_at'),
                    'active_processes': state.get('context').active_process_count() if state.get('context') else 0,
                    'active_futures': state.get('context').active_future_count() if state.get('context') else 0,
                    'cancellation_acknowledged': bool(state.get('cancellation_acknowledged', False)),
                    'cleanup_status': state.get('cleanup_status') or ('stopping' if state.get('context') and state['context'].cancelled() else 'running'),
                    'quiesced_at': state.get('quiesced_at'),
                }
                for job_id, state in self.active_jobs.items()
                if owner_id is None or job_id in visible_ids
            }
            return {
                'items': items,
                'worker_running': self.worker_running,
                'worker_count': int(self.worker_count or 1),
                'active_jobs': list(active),
                'active_job_state': active,
            }

    def get_item_snapshot(self, job_id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Return one redacted queue item, including its full manifest."""
        with self._queue_lock:
            item = next((row for row in self.queue if row.get('id') == job_id), None)
            if item and owner_id is not None and (item.get('config') or {}).get('auth_user_id') != owner_id:
                return None
            return _redact_sensitive_data(dict(item)) if isinstance(item, dict) else None

    # =========================================================================
    # WORKER MANAGEMENT
    # =========================================================================

    def start_worker(self, generation_callback: Callable):
        """
        Start the background worker thread.
        
        Args:
            generation_callback: Function to call for each job.
                                 Signature: callback(job_config, progress_callback, check_cancelled)
        """
        # /queue/add starts the worker and older clients also call /queue/start
        # immediately afterwards. Serialize this check and the state change so
        # two near-simultaneous requests can never create two workers that race
        # the same queue item.
        with self._queue_condition:
            if generation_callback is not None:
                self.generation_callback = generation_callback
            try:
                self.worker_count = max(1, min(2, int(os.environ.get('VIDZEO_QUEUE_PREP_WORKERS', '2') or 2)))
            except (TypeError, ValueError):
                self.worker_count = 2

            alive_threads = [t for t in self.worker_threads if t and t.is_alive()]
            if self.worker_running and len(alive_threads) >= self.worker_count:
                self._queue_condition.notify_all()
                return False

            self.worker_running = True
            new_threads = list(alive_threads)
            while len(new_threads) < self.worker_count:
                idx = len(new_threads) + 1
                t = JobThread(
                    target=self._worker_loop,
                    daemon=True,
                    name=f"QueueWorker-{idx}",
                )
                t.start()
                new_threads.append(t)
            self.worker_threads = new_threads
            self.worker_thread = self.worker_threads[0] if self.worker_threads else None
            self._queue_condition.notify_all()
        print(f"🚀 Queue workers started ({len(self.worker_threads)}; media preparation may overlap, Remotion render remains gated)")
        return True
    
    def stop_worker(self):
        """Stop the background worker"""
        with self._queue_condition:
            self.worker_running = False
            self._queue_condition.notify_all()
        for thread in list(self.worker_threads or ([self.worker_thread] if self.worker_thread else [])):
            if thread:
                thread.join(timeout=5)
        self.worker_threads = []
        print("⏹️ Queue workers stopped")
    
    def _finalize_cancelled_job(self, job_id: str, item: Dict, cancellation: CancellationContext) -> None:
        timeout = float(os.environ.get('VIDZEO_QUEUE_CANCEL_ACK_SECONDS', '10') or 10)
        acknowledged = cancellation.wait_for_quiescence(timeout)
        with self._queue_lock:
            state = self.active_jobs.get(job_id)
            if state is not None:
                state['cancellation_acknowledged'] = acknowledged
                state['quiesced_at'] = datetime.now().isoformat() if acknowledged else None
                state['cleanup_status'] = 'acknowledged' if acknowledged else 'shutdown_timeout'
                if not acknowledged:
                    item['status'] = JobStatus.SHUTDOWN_TIMEOUT
                    item['progress_text'] = 'Cancellation cleanup timed out; background work is still stopping.'
                    seq = int(item.get('progress_seq') or 0) + 1
                    item['progress_seq'] = seq
                    events = list(item.get('progress_events') or [])
                    events.append({
                        'seq': seq,
                        'progress': max(0, min(100, int(item.get('progress') or 0))),
                        'text': item['progress_text'],
                    })
                    item['progress_events'] = events[-12:]
                else:
                    # Keep the request visible until the worker has actually
                    # acknowledged cancellation and all registered children
                    # have quiesced.  This prevents a fast callback from
                    # racing the DELETE response and makes the UI's
                    # cancellation_requested state reliable to pollers.
                    pause_text = _RECOVERY_PAUSE_TEXT.get(item.get('paused_reason'))
                    if not state.get('requested_at') and pause_text:
                        item['status'] = JobStatus.PAUSED
                        item['progress_text'] = pause_text
                        item['retryable'] = True
                    else:
                        item['status'] = JobStatus.CANCELLED
                        item['progress_text'] = 'Cancelled'
                        item['cancelled_at'] = datetime.now().isoformat()
                self.active_jobs.pop(job_id, None) if acknowledged else None
                if acknowledged and state.get('remove_after_cancel'):
                    self.queue = [row for row in self.queue if row.get('id') != job_id]
        cancellation.clear_registries() if acknowledged else None
        self._save_queue()
        with self._queue_condition:
            self._queue_condition.notify_all()

    def _worker_loop(self):
        """Main worker loop - processes queue items sequentially"""
        while self.worker_running:
            with self._queue_condition:
                next_item = None
                while self.worker_running and next_item is None:
                    for item in self.queue:
                        if isinstance(item, dict) and item.get('status') == JobStatus.QUEUED:
                            next_item = item
                            break
                    if next_item is None:
                        self._queue_condition.wait()

                if not self.worker_running:
                    break

                job_id = next_item['id']
                attempt = max(0, int(next_item.get('attempt') or 0)) + 1
                run_id = f"{job_id}-run-{attempt:04d}-{uuid.uuid4().hex[:12]}"
                callback_config = _callback_config_for_run(
                    next_item['config'],
                    job_id=job_id,
                    run_id=run_id,
                    attempt=attempt,
                )
                cancellation = CancellationContext(job_id=job_id)
                self.active_jobs[job_id] = {
                    'context': cancellation,
                    'thread': threading.current_thread(),
                    'run_id': run_id,
                    'attempt': attempt,
                    'remove_after_cancel': False,
                    'requested_at': None,
                }
                next_item['attempt'] = attempt
                next_item['active_run_id'] = run_id
                next_item['status'] = JobStatus.PROCESSING
                next_item['progress'] = 0
                next_item['progress_text'] = 'Starting...'
                next_item['progress_seq'] = 1
                next_item['progress_events'] = [
                    {'seq': 1, 'progress': 0, 'text': 'Starting...'}
                ]
                next_item['started_at'] = datetime.now().isoformat()
                next_item.pop('error', None)
                next_item.pop('error_detail', None)
                next_item.pop('result', None)
                next_item.pop('paused_reason', None)
                next_item.pop('retryable', None)

            self._save_queue()
            
            print(f"\n{'='*50}")
            print(f"🎬 Processing job {job_id}: {next_item['config'].get('title', 'Script')}")
            print(f"{'='*50}")

            # Provider calls can be healthy while they wait on a network
            # response and emit no normal progress callback.  A durable
            # heartbeat keeps the UI from appearing frozen at a branch
            # boundary (often 31% while web and YouTube run in parallel).
            heartbeat_stop = threading.Event()
            heartbeat_lock = threading.Lock()
            heartbeat_last_activity = time.monotonic()
            heartbeat_last_emit = 0.0
            heartbeat_job_started = heartbeat_last_activity
            heartbeat_thread = None
            recovery_stop_text = ''
            delivery_budget = None
            stop_for_recovery = None
            
            try:
                # Install before invoking any generation callback so the queue
                # and all pipeline stages share one fresh clock per attempt.
                delivery_budget = install_delivery_budget(callback_config)
                try:
                    preparation_stall_seconds = float(callback_config.get('preparation_stall_seconds') or 0)
                    if not math.isfinite(preparation_stall_seconds):
                        preparation_stall_seconds = 0
                except (TypeError, ValueError):
                    preparation_stall_seconds = 0
                try:
                    from services.web_image_service import reset_web_runtime_state
                    reset_web_runtime_state(f"queue job {job_id} start")
                except Exception as reset_exc:
                    print(f"⚠️ Web runtime reset skipped: {reset_exc}")

                # Define progress callback
                _last_progress_save = 0.0
                last_work_progress = None
                try:
                    progress_save_interval = max(
                        1.0,
                        float(os.environ.get('VIDZEO_QUEUE_PROGRESS_SAVE_INTERVAL_SECONDS', '3.0') or 3.0),
                    )
                except (TypeError, ValueError):
                    progress_save_interval = 3.0

                def update_progress(progress: int, text: str, force_save: bool = False):
                    nonlocal _last_progress_save, heartbeat_last_activity, last_work_progress
                    cancellation.raise_if_cancelled()
                    try:
                        numeric_progress = max(0, min(100, int(round(float(progress)))))
                    except (TypeError, ValueError):
                        numeric_progress = int(next_item.get('progress') or 0)
                    clean_text = str(text or '').strip() or 'Processing'
                    previous_progress = int(next_item.get('progress') or 0)
                    previous_text = str(next_item.get('progress_text') or '')
                    # A branch may finish after another branch has advanced.
                    # Keep progress monotonic while still recording meaningful
                    # stage changes in one ordered history for the UI.
                    # Background retrieval can finish after rendering begins.
                    # Do not relabel an active render as "Preparing media".
                    if previous_progress >= 70 and numeric_progress <= previous_progress and clean_text.lower().startswith((
                            'finding ', 'searching ', 'preparing media', 'checking media relevance',
                            'checking youtube', 'downloading youtube', 'generating captions', 'planning visual')):
                        return
                    numeric_progress = max(previous_progress, numeric_progress)
                    work_progress = (numeric_progress, clean_text)
                    with heartbeat_lock:
                        if work_progress != last_work_progress and not re.search(
                            r'\bstill working\b', clean_text, flags=re.I
                        ):
                            last_work_progress = work_progress
                            heartbeat_last_activity = time.monotonic()
                    next_item['progress'] = numeric_progress
                    next_item['progress_text'] = clean_text
                    if numeric_progress != previous_progress or clean_text != previous_text:
                        seq = int(next_item.get('progress_seq') or 0) + 1
                        next_item['progress_seq'] = seq
                        events = list(next_item.get('progress_events') or [])
                        events.append({'seq': seq, 'progress': numeric_progress, 'text': clean_text})
                        next_item['progress_events'] = events[-12:]
                    now = time.monotonic()
                    if force_save or (now - _last_progress_save) >= progress_save_interval:
                        _last_progress_save = now
                        self._save_queue()

                def stop_for_recovery(reason, text):
                    """Stop owned work before the same queue item can be retried."""
                    nonlocal recovery_stop_text
                    with self._queue_lock:
                        if recovery_stop_text or cancellation.cancelled() or next_item.get('status') != JobStatus.PROCESSING:
                            return
                        recovery_stop_text = text
                        next_item['paused_reason'] = reason
                        next_item['retryable'] = True
                        next_item['error'] = text
                        next_item['status'] = JobStatus.CANCELLATION_REQUESTED
                    try:
                        record_terminal_event(next_item.get('progress', 0), text)
                    finally:
                        cancellation.cancel(text)

                def _progress_heartbeat_loop():
                    """Publish truthful waiting activity during quiet stages."""
                    nonlocal heartbeat_last_emit
                    poll_seconds = max(0.02, float(self._progress_heartbeat_poll_seconds))
                    idle_seconds = max(0.02, float(self._progress_heartbeat_idle_seconds))
                    emit_seconds = max(0.02, float(self._progress_heartbeat_emit_seconds))
                    while not heartbeat_stop.wait(poll_seconds):
                        try:
                            if not cancellation.cancelled() and delivery_budget.remaining_seconds() <= 0:
                                stop_for_recovery('delivery_deadline', 'Processing time limit reached; stopping background work.')
                                return
                            if cancellation.cancelled() or next_item.get('status') != JobStatus.PROCESSING:
                                continue
                            now = time.monotonic()
                            with heartbeat_lock:
                                idle_for = now - heartbeat_last_activity
                            if (preparation_stall_seconds > 0 and not delivery_budget.render_started
                                    and idle_for >= preparation_stall_seconds):
                                stop_for_recovery('preparation_stall', 'Preparation stopped advancing; saving work for retry.')
                                return
                            with heartbeat_lock:
                                if idle_for < idle_seconds or (now - heartbeat_last_emit) < emit_seconds:
                                    continue
                                heartbeat_last_emit = now
                            try:
                                current = max(0, min(100, int(next_item.get('progress') or 0)))
                            except (TypeError, ValueError):
                                current = 0
                            # Keep the percentage truthful. The previous
                            # synthetic +1 heartbeat stopped at 69% before
                            # render, which looked exactly like a hung job.
                            # The UI animates the bar and elapsed wait text.
                            heartbeat_progress = current
                            elapsed = max(1, int(now - heartbeat_job_started))
                            base = str(next_item.get('progress_text') or 'Processing').strip()
                            base = re.sub(r'\s*[—-]\s*still working.*$', '', base, flags=re.I).strip()
                            heartbeat_text = f'{base} — still working ({elapsed}s); please wait'
                            next_item['progress'] = heartbeat_progress
                            next_item['progress_text'] = heartbeat_text
                            seq = int(next_item.get('progress_seq') or 0) + 1
                            next_item['progress_seq'] = seq
                            events = list(next_item.get('progress_events') or [])
                            events.append({
                                'seq': seq,
                                'progress': heartbeat_progress,
                                'text': heartbeat_text,
                            })
                            next_item['progress_events'] = events[-12:]
                            self._save_queue()
                            print(f'[queue] job {job_id} heartbeat: {heartbeat_text}')
                        except Exception as heartbeat_exc:
                            # Heartbeats are observability only; never let one
                            # failure interrupt the generation job.
                            print(f'[queue] heartbeat skipped for {job_id}: {heartbeat_exc}')

                def check_cancelled() -> bool:
                    return cancellation.cancelled()

                def record_terminal_event(progress: int, text: str):
                    """Persist terminal state in the UI-visible event stream.

                    Progress events are the only durable activity stream the
                    renderer receives while polling.  Previously a late
                    exception/cancellation changed only ``error`` and
                    ``progress_text``, so the Runtime diagnostics panel could
                    stop on an old stage and make a failed job look hung.
                    """
                    clean_text = str(text or '').strip() or 'Processing stopped'
                    try:
                        numeric_progress = max(0, min(100, int(round(float(progress)))))
                    except (TypeError, ValueError):
                        numeric_progress = int(next_item.get('progress') or 0)
                    previous = str(next_item.get('progress_text') or '').strip()
                    if previous == clean_text and next_item.get('progress') == numeric_progress:
                        return
                    next_item['progress'] = numeric_progress
                    next_item['progress_text'] = clean_text
                    seq = int(next_item.get('progress_seq') or 0) + 1
                    next_item['progress_seq'] = seq
                    events = list(next_item.get('progress_events') or [])
                    events.append({'seq': seq, 'progress': numeric_progress, 'text': clean_text})
                    next_item['progress_events'] = events[-12:]
                    self._save_queue()

                heartbeat_thread = JobThread(
                    target=_progress_heartbeat_loop,
                    name=f'queue-heartbeat-{job_id}',
                    daemon=True,
                )
                heartbeat_thread.start()

                cancellation.raise_if_cancelled()
                try:
                    import inspect
                    parameter_count = len(inspect.signature(self.generation_callback).parameters)
                except (TypeError, ValueError):
                    parameter_count = 4
                if parameter_count >= 4:
                    result = self.generation_callback(
                        callback_config,
                        update_progress,
                        check_cancelled,
                        cancellation,
                    )
                else:
                    result = self.generation_callback(
                        callback_config,
                        update_progress,
                        check_cancelled,
                    )
                # Close the boundary race if a callback returns just after
                # expiry but before the watchdog's next poll.
                if delivery_budget.remaining_seconds() <= 0:
                    stop_for_recovery('delivery_deadline', 'Processing time limit reached; stopping background work.')
                with self._queue_lock:
                    cancellation.raise_if_cancelled()
                    if recovery_stop_text or next_item.get('status') == JobStatus.CANCELLATION_REQUESTED:
                        raise JobCancelled(recovery_stop_text or 'Cancellation requested')
                    next_item['status'] = JobStatus.COMPLETED
                    next_item['progress'] = 100
                    next_item['progress_text'] = 'Completed'
                    next_item['progress_seq'] = int(next_item.get('progress_seq') or 0) + 1
                    next_item.setdefault('progress_events', []).append({
                        'seq': next_item['progress_seq'], 'progress': 100, 'text': 'Completed'
                    })
                    next_item['completed_at'] = datetime.now().isoformat()
                    next_item['result'] = result
                print(f"✅ Job {job_id} completed successfully")
                
            except InterruptedError:
                if delivery_budget is not None and delivery_budget.remaining_seconds() <= 0 and callable(stop_for_recovery):
                    stop_for_recovery('delivery_deadline', 'Processing time limit reached; stopping background work.')
                next_item['status'] = JobStatus.CANCELLATION_REQUESTED
                record_terminal_event(next_item.get('progress', 0),
                    recovery_stop_text or 'Cancellation requested')
                print(f"🛑 Job {job_id} was cancelled")
                
            except Exception as e:
                if delivery_budget is not None and delivery_budget.remaining_seconds() <= 0 and callable(stop_for_recovery):
                    stop_for_recovery('delivery_deadline', 'Processing time limit reached; stopping background work.')
                memory_warning = None
                if cancellation.cancelled() or recovery_stop_text:
                    next_item['status'] = JobStatus.CANCELLATION_REQUESTED
                    record_terminal_event(next_item.get('progress', 0),
                        recovery_stop_text or 'Cancellation requested')
                    if not recovery_stop_text:
                        next_item.pop('error', None)
                    next_item.pop('error_detail', None)
                    print(f"🛑 Job {job_id} was cancelled")
                else:
                    memory_warning = getattr(e, 'memory_warning', None)
                    if memory_warning is None:
                        memory_warning = getattr(e, 'memory_snapshot', None)
                    if memory_warning is None:
                        memory_warning = getattr(getattr(e, '__cause__', None), 'memory_snapshot', None)
                    planning_retry = bool(
                        getattr(e, 'retryable', False)
                        and getattr(e, 'stage', None) == 'openrouter_scene_plan'
                    )
                    edit_plan_retry = bool(
                        getattr(e, 'retryable', False)
                        and getattr(e, 'stage', None) == 'remotion_edit_plan'
                    )
                    render_retry = bool(
                        getattr(e, 'retryable', False)
                        and getattr(e, 'stage', None) == 'remotion_render'
                    )
                    media_retry = isinstance(e, MediaContractError)
                    if memory_warning:
                        available_mb = int((memory_warning or {}).get('available_mb') or 0)
                        next_item['status'] = JobStatus.PAUSED
                        next_item['paused_reason'] = 'low_memory'
                        next_item['retryable'] = True
                        next_item['memory_warning'] = dict(memory_warning)
                        next_item['checkpoint_dir'] = (
                            callback_config.get('output_package_dir')
                            or (next_item.get('config') or {}).get('output_package_dir')
                            or (next_item.get('config') or {}).get('_output_package_dir')
                            or next_item.get('checkpoint_dir')
                        )
                        next_item['progress_text'] = (
                            f'Paused: {available_mb} MB free RAM. Close other apps, then click Retry.'
                        )
                        record_terminal_event(next_item.get('progress', 0), next_item['progress_text'])
                        next_item['error'] = str(e)
                        print(f"⏸️ Job {job_id} paused for low memory ({available_mb} MB free); retry is available")
                    elif planning_retry:
                        next_item['status'] = JobStatus.PAUSED
                        next_item['paused_reason'] = 'openrouter_scene_plan'
                        next_item['retryable'] = True
                        package_dir = (
                            callback_config.get('output_package_dir')
                            or (next_item.get('config') or {}).get('output_package_dir')
                            or (next_item.get('config') or {}).get('_output_package_dir')
                        )
                        if package_dir:
                            next_item['checkpoint_dir'] = str(Path(package_dir) / 'checkpoints')
                        next_item['progress_text'] = 'Waiting for OpenRouter scene planning. Click Retry to continue.'
                        record_terminal_event(next_item.get('progress', 0), next_item['progress_text'])
                        next_item['error'] = str(e)
                        print(f"⏸️ Job {job_id} paused for OpenRouter scene planning; retry is available")
                    elif edit_plan_retry:
                        next_item['status'] = JobStatus.PAUSED
                        next_item['paused_reason'] = 'remotion_edit_plan'
                        next_item['retryable'] = True
                        package_dir = callback_config.get('output_package_dir') or (next_item.get('config') or {}).get('output_package_dir')
                        if package_dir:
                            next_item['checkpoint_dir'] = str(Path(package_dir) / 'checkpoints')
                        next_item['edit_plan_path'] = getattr(e, 'edit_plan_path', None)
                        next_item['progress_text'] = 'Remotion edit plan needs correction. Review the saved plan, then click Retry.'
                        record_terminal_event(next_item.get('progress', 0), next_item['progress_text'])
                        next_item['error'] = str(e)
                        print(f"⏸️ Job {job_id} paused for Remotion edit plan correction")
                    elif media_retry:
                        next_item['status'] = JobStatus.PAUSED
                        next_item['paused_reason'] = 'media_contract'
                        next_item['retryable'] = True
                        package_dir = callback_config.get('output_package_dir') or (next_item.get('config') or {}).get('output_package_dir')
                        if package_dir:
                            next_item['checkpoint_dir'] = str(Path(package_dir) / 'checkpoints')
                        next_item['progress_text'] = 'No verified media for one or more scenes. Click Retry to search again.'
                        record_terminal_event(next_item.get('progress', 0), next_item['progress_text'])
                        next_item['error'] = str(e)
                        print(f"⏸️ Job {job_id} paused after media validation; retry is available")
                    elif render_retry:
                        next_item['status'] = JobStatus.PAUSED
                        next_item['paused_reason'] = 'remotion_render'
                        next_item['retryable'] = True
                        package_dir = callback_config.get('output_package_dir') or (next_item.get('config') or {}).get('output_package_dir')
                        if package_dir:
                            next_item['checkpoint_dir'] = str(Path(package_dir) / 'checkpoints')
                        next_item['progress_text'] = 'Remotion render or final check failed. Click Retry to use the saved narration, media, and edit plan.'
                        record_terminal_event(next_item.get('progress', 0), next_item['progress_text'])
                        next_item['error'] = str(e)
                    else:
                        next_item['status'] = JobStatus.ERROR
                        record_terminal_event(next_item.get('progress', 0), f'Error: {str(e)[:160]}')
                        next_item['error'] = str(e)
                detail = getattr(e, 'detail', None)
                if detail:
                    next_item['error_detail'] = {'media_contract': detail}
                else:
                    if not memory_warning:
                        next_item.pop('error_detail', None)
                print(f"❌ Job {job_id} failed: {e}")
                import traceback
                traceback.print_exc()
            
            finally:
                heartbeat_stop.set()
                if heartbeat_thread and heartbeat_thread.is_alive():
                    heartbeat_thread.join(timeout=1.5)
                package_dir = callback_config.get('output_package_dir') or callback_config.get('_output_package_dir')
                if package_dir:
                    next_item.setdefault('config', {})['output_package_dir'] = str(package_dir)
                    next_item['config']['_output_package_dir'] = str(package_dir)
                    next_item['checkpoint_dir'] = str(Path(package_dir) / 'checkpoints')
                next_item['last_run_id'] = run_id
                next_item.pop('active_run_id', None)
                try:
                    from services.web_image_service import reset_web_runtime_state
                    reset_web_runtime_state(f"queue job {job_id} cleanup")
                except Exception:
                    pass
                was_cancelled = cancellation.cancelled() or bool(recovery_stop_text)
                if was_cancelled:
                    self._finalize_cancelled_job(job_id, next_item, cancellation)
                else:
                    cancellation.clear_registries()
                    with self._queue_lock:
                        self.active_jobs.pop(job_id, None)
                    self._save_queue()
                    with self._queue_condition:
                        self._queue_condition.notify_all()
        
        print("👋 Worker loop exited")


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================
queue_manager: Optional[QueueManager] = None


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================
def get_queue_manager() -> QueueManager:
    """Get the singleton queue manager instance"""
    global queue_manager
    if queue_manager is None:
        queue_manager = QueueManager()
    return queue_manager
