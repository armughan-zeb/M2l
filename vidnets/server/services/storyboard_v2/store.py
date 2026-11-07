"""Atomic Storyboard V2 snapshots and redacted append-only event ledgers."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Dict, Mapping, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import (
    AttemptLedgerEntryV1,
    ContractValidationError,
    StoryboardV2,
    utc_now_iso,
)


_SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "openaiapikey",
        "openrouterapikey",
        "authorization",
        "password",
        "passwd",
        "secret",
        "clientsecret",
        "cookie",
        "cookies",
        "accesstoken",
        "refreshtoken",
        "oauthtoken",
        "potoken",
        "sessiontoken",
    }
)

_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "key",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "auth",
        "authorization",
        "signature",
        "sig",
        "cookie",
    }
)


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _is_sensitive_key(value: str) -> bool:
    normalized = _normalized_key(value)
    if normalized in _SENSITIVE_KEYS:
        return True
    return any(
        normalized.endswith(suffix)
        for suffix in (
            "apikey",
            "password",
            "passwd",
            "secret",
            "accesstoken",
            "refreshtoken",
            "oauthtoken",
            "potoken",
            "sessiontoken",
            "authorization",
            "cookie",
            "cookies",
        )
    )


def _redact_string(value: str) -> str:
    text = str(value)
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/=]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{8,}", "[REDACTED]", text)
    text = re.sub(
        r"(?i)(api[_-]?key|access[_-]?token|po[_-]?token|password|secret)=([^&\s]+)",
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    if re.match(r"(?i)^https?://", text):
        try:
            split = urlsplit(text)
            query = [
                (key, "[REDACTED]" if key.casefold() in _SENSITIVE_QUERY_KEYS else item)
                for key, item in parse_qsl(split.query, keep_blank_values=True)
            ]
            text = urlunsplit(
                (split.scheme, split.netloc, split.path, urlencode(query), split.fragment)
            )
        except Exception:
            pass
    return text


def redact_sensitive(value: Any) -> Any:
    """Return a JSON-safe copy with credentials removed recursively."""

    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = redact_sensitive(item)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_string(str(value))


def storyboard_path_for_output(output_path: str | os.PathLike[str]) -> Path:
    path = Path(output_path)
    temp_root = Path(os.environ.get("VIDZEO_TEMP_DIR") or Path(__file__).resolve().parents[3] / "temp")
    storyboards_dir = temp_root / "storyboards"
    storyboards_dir.mkdir(parents=True, exist_ok=True)
    return storyboards_dir / f"{path.stem}.storyboard.json"



class StoryboardStore:
    """Job/run-scoped persistence; no global temp-directory discovery."""

    def __init__(self, snapshot_path: str | os.PathLike[str], *, job_id: str, run_id: str):
        self.snapshot_path = Path(snapshot_path).resolve()
        self.job_id = str(job_id)
        self.run_id = str(run_id)
        if not self.job_id or not self.run_id:
            raise ContractValidationError("StoryboardStore job_id and run_id are required")
        # Snapshots intentionally keep the human-readable output stem so the
        # renderer and delivery layer can discover them.  The event ledger must
        # be scoped to the job/run, though: reusing an output name for a later
        # generation must never inherit sequence numbers from an older run.
        scope_digest = hashlib.sha256(
            f"{self.job_id}\0{self.run_id}".encode("utf-8", errors="replace")
        ).hexdigest()[:16]
        self.event_path = self.snapshot_path.with_name(
            f"{self.snapshot_path.stem}.{scope_digest}.events.jsonl"
        )
        self._lock = threading.RLock()
        self._next_sequence: int | None = None

    @classmethod
    def for_output(
        cls,
        output_path: str | os.PathLike[str],
        *,
        job_id: str,
        run_id: str,
    ) -> "StoryboardStore":
        return cls(storyboard_path_for_output(output_path), job_id=job_id, run_id=run_id)

    def _validate_scope(self, storyboard: StoryboardV2) -> None:
        if storyboard.job_id != self.job_id or storyboard.run_id != self.run_id:
            raise ContractValidationError("Refusing to persist a storyboard from another job/run")

    def save_atomic(self, storyboard: StoryboardV2) -> Path:
        """Fsync and atomically replace the current snapshot in the same folder."""

        self._validate_scope(storyboard)
        payload = redact_sensitive(storyboard.to_dict())
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            # Persisting an empty ledger is the durable marker for a fresh
            # generation.  Reset a same-scope prior ledger at that boundary;
            # subsequent non-empty snapshots must retain their append history.
            if not storyboard.attempt_ledger:
                self.reset_event_ledger()
            temp_name = ""
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    newline="\n",
                    prefix=f".{self.snapshot_path.name}.",
                    suffix=".part",
                    dir=self.snapshot_path.parent,
                    delete=False,
                ) as handle:
                    temp_name = handle.name
                    json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, self.snapshot_path)
            finally:
                if temp_name and os.path.exists(temp_name):
                    try:
                        os.unlink(temp_name)
                    except OSError:
                        pass
        return self.snapshot_path

    def reset_event_ledger(self) -> None:
        """Start a fresh generation for this exact job/run scope.

        ``V2PipelineRuntime.create`` creates a new immutable storyboard rather
        than resuming an existing one.  If a user reuses an output name (or a
        queue retries with the same identifiers), an older ledger must not
        make the first append look stale.  Only this store's scoped ledger is
        touched; snapshots and other jobs remain intact.
        """

        with self._lock:
            try:
                self.event_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise ContractValidationError(
                    f"Unable to reset storyboard event ledger: {exc}"
                ) from exc
            self._next_sequence = None

    def load(self) -> StoryboardV2:
        with self.snapshot_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        storyboard = StoryboardV2.from_dict(raw)
        self._validate_scope(storyboard)
        return storyboard

    def load_compatible(self) -> StoryboardV2:
        """Read V2 or expose a legacy artifact as a V2 in-memory view.

        Compatibility loading is intentionally read-only: this method never
        replaces the legacy file.  If a caller explicitly saves the returned
        contract later, :meth:`save_atomic` writes the normal schema V2 shape.
        """

        from .compat import load_storyboard_compatible

        storyboard = load_storyboard_compatible(
            self.snapshot_path,
            job_id=self.job_id,
            run_id=self.run_id,
        )
        self._validate_scope(storyboard)
        return storyboard

    def _discover_next_sequence(self) -> int:
        if self._next_sequence is not None:
            return self._next_sequence
        last = 0
        if self.event_path.exists():
            stale = False
            try:
                with self.event_path.open("r", encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if not line.strip():
                            continue
                        try:
                            row = json.loads(line)
                            sequence = int(row["sequence"])
                        except Exception as exc:
                            raise ContractValidationError(
                                f"Malformed storyboard ledger line {line_number}"
                            ) from exc
                        if sequence != last + 1:
                            raise ContractValidationError("Storyboard ledger sequence is not contiguous")
                        if row.get("job_id") != self.job_id or row.get("run_id") != self.run_id:
                            # Stale ledger from a previous job/run targeting the
                            # same output path – clear it so the new job can proceed.
                            stale = True
                            break
                        last = sequence
            except ContractValidationError:
                # Malformed or non-contiguous ledger – treat as stale so a fresh
                # generation is not blocked by a corrupt leftover file.
                stale = True
            if stale:
                try:
                    self.event_path.unlink()
                except OSError:
                    pass
                last = 0
        self._next_sequence = last + 1
        return self._next_sequence

    def append_event(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        scene_id: str | None = None,
        container_id: str | None = None,
    ) -> AttemptLedgerEntryV1:
        if not str(event_type or "").strip():
            raise ContractValidationError("event_type is required")
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            sequence = self._discover_next_sequence()
            entry = AttemptLedgerEntryV1(
                sequence=sequence,
                timestamp=utc_now_iso(),
                event_type=str(event_type),
                job_id=self.job_id,
                run_id=self.run_id,
                scene_id=scene_id,
                container_id=container_id,
                payload=redact_sensitive(dict(payload or {})),
            )
            line = json.dumps(
                redact_sensitive(entry.to_dict()),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            with self.event_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._next_sequence = sequence + 1
            return entry

    def record_attempt(
        self,
        storyboard: StoryboardV2,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        scene_id: str | None = None,
        container_id: str | None = None,
    ) -> StoryboardV2:
        """Append the durable event, then return a snapshot-ready storyboard."""

        self._validate_scope(storyboard)
        with self._lock:
            next_sequence = self._discover_next_sequence()
            if next_sequence != len(storyboard.attempt_ledger) + 1:
                raise ContractValidationError(
                    "Refusing to append from a stale storyboard snapshot"
                )
            entry = self.append_event(
                event_type,
                payload,
                scene_id=scene_id,
                container_id=container_id,
            )
            updated = storyboard.with_attempt(entry)
            self.save_atomic(updated)
            return updated

    def read_events(self) -> Tuple[AttemptLedgerEntryV1, ...]:
        if not self.event_path.exists():
            return tuple()
        result = []
        with self.event_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    result.append(AttemptLedgerEntryV1.from_dict(json.loads(line)))
        return tuple(result)
