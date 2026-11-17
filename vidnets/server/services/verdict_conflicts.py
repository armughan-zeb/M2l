"""File-backed ledger of late fatal vision rejections.

Background (job 4f61cc9e, scene 1, 2026-11-17): one visual_review request
(8fc1a5f71d11747927affcc0b7e8ad59) received TWO verdicts. The resident pool
answered first with an ACCEPT, so ``agent_bridge.complete_request`` stored
it and the server selected the clip. A second agent then reviewed the same
frames and reached a REJECT (``fatal_mismatch`` true, final_score 20) — but
``complete_request`` refused the late answer with
``ValueError('This request expired or was already answered.')`` and the
verdict was discarded entirely. First-write-wins for the *response* is
correct; silently dropping a conflicting fatal verdict is not.

This module preserves the late fatal verdict in ``_verdict_conflicts.json``
at the bridge root (next to the request dirs and ``_answer_cache.json``) so
the media coordinator can veto the contested content on a later pass, even
though the original request is already answered. It is stdlib-only and
importable without the server running — ``agent_bridge`` imports it, so it
must not import ``agent_bridge`` back (its atomic write below mirrors
``agent_bridge.atomic_json`` for exactly that reason).

Record shape (one dict per late fatal verdict)::

    {
        "request_id": "8fc1a5f7...",          # the already-answered request
        "candidate_id": "youtube:F4yOVIKCZpw",
        "video_id": "F4yOVIKCZpw",           # parsed from "youtube:<id>", else None
        "asset_sha256": None,                # from the verdict, if present
        "scene_index": 1,                    # from the request payload, else None
        "requirement_hash": "dfb2a7...",     # from the request payload, else None
        "verdict": {...},                    # the full raw late verdict dict
        "received_at": "2026-11-17T00:24:10+00:00",  # ISO-8601 UTC
        "winner": {...},                     # verdict already recorded, else None
    }

Content-key rule (the coordinator matches on these keys): a candidate is
identified by ``(scene_index, video_id)`` when the video id is known, else
``(scene_index, asset_sha256)``, else ``(scene_index, candidate_id)``. A
record with ``scene_index`` None applies to *any* scene only when its
``requirement_hash`` matches — see :func:`fatal_content_keys`.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

LEDGER_FILENAME = "_verdict_conflicts.json"

_TRUE_STRINGS = {"true", "1", "yes", "on"}
_REQUIREMENT_HASH_RE = re.compile(r'"requirement_hash"\s*:\s*"([^"]+)"')
_SCENE_INDEX_RE = re.compile(r'"scene_index"\s*:\s*(\d+)')
_YOUTUBE_PREFIXES = ("youtube:", "yt:")

# In-process guard for the read-modify-write below; the file lock in
# _ledger_lock covers the cross-process (Flask vs daemon) case.
_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def _coerce_bool(value, default: bool = False) -> bool:
    """Same coercion as vision_client._normalise_clip_response's _coerce_bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    return default


def _coerce_scene_index(value):
    """An int scene index, or None when the value is absent/unusable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer():
        return int(value) if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def parse_video_id(candidate_id, verdict=None):
    """YouTube id for a candidate, or None.

    ``"youtube:<id>"`` (the form vision verdicts carry) yields ``<id>``;
    an explicit ``video_id`` field on the verdict wins when present.
    """
    if isinstance(verdict, dict):
        raw_video = verdict.get("video_id")
        if isinstance(raw_video, str) and raw_video.strip():
            return raw_video.strip()
    if not isinstance(candidate_id, str):
        return None
    candidate_id = candidate_id.strip()
    for prefix in _YOUTUBE_PREFIXES:
        if candidate_id.startswith(prefix):
            video_id = candidate_id[len(prefix):].strip()
            return video_id or None
    return None


def _message_texts(row):
    """Every text blob in a bridge request row, for requirement extraction."""
    messages = row.get("messages") if isinstance(row, dict) else None
    if not isinstance(messages, list):
        return
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            yield content
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text")
                    if isinstance(text, str):
                        yield text


def _extract_scene_and_requirement(row, verdict=None):
    """(scene_index, requirement_hash) identifiable from a request row.

    The real visual_review rows carry no top-level scene field: the scene
    is identified by the ``requirement_hash`` inside the ``Visual
    requirement: {...}`` JSON embedded in the prompt text (job 4f61cc9e:
    ``dfb2a785...``). Structured fields win when present; the prompt text
    is the fallback. Returns ``(None, None)`` when nothing is identifiable
    — the record is still stored, keyed on its requirement hash if any.
    """
    scene_index = None
    requirement_hash = None
    if isinstance(row, dict):
        for key in ("scene_index", "scene"):
            scene_index = _coerce_scene_index(row.get(key))
            if scene_index is not None:
                break
        if isinstance(row.get("requirement_hash"), str) and row["requirement_hash"].strip():
            requirement_hash = row["requirement_hash"].strip()
        elif isinstance(row.get("requirement"), dict):
            raw = row["requirement"].get("requirement_hash")
            if isinstance(raw, str) and raw.strip():
                requirement_hash = raw.strip()
    if isinstance(verdict, dict):
        if scene_index is None:
            scene_index = _coerce_scene_index(verdict.get("scene_index"))
        if requirement_hash is None:
            raw = verdict.get("requirement_hash")
            if isinstance(raw, str) and raw.strip():
                requirement_hash = raw.strip()
    if scene_index is None or requirement_hash is None:
        for text in _message_texts(row):
            if requirement_hash is None:
                match = _REQUIREMENT_HASH_RE.search(text)
                if match:
                    requirement_hash = match.group(1)
            if scene_index is None:
                match = _SCENE_INDEX_RE.search(text)
                if match:
                    scene_index = int(match.group(1))
            if scene_index is not None and requirement_hash is not None:
                break
    return scene_index, requirement_hash


def _asset_sha256(verdict):
    """Content hash from a verdict, if it carries one."""
    if not isinstance(verdict, dict):
        return None
    for key in ("asset_sha256", "sha256", "content_sha256", "asset_hash"):
        raw = verdict.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def is_fatal_reject(verdict) -> bool:
    """True when a verdict dict is a fatal rejection of the candidate.

    Either ``fatal_mismatch`` coerced true (same coercion as
    ``vision_client._normalise_clip_response``), or ``entity_conflict``
    true with ``scene_match`` *explicitly* false — a missing ``scene_match``
    does not count.
    """
    if not isinstance(verdict, dict):
        return False
    if _coerce_bool(verdict.get("fatal_mismatch")):
        return True
    return bool(
        _coerce_bool(verdict.get("entity_conflict"))
        and "scene_match" in verdict
        and not _coerce_bool(verdict.get("scene_match"))
    )


def build_late_fatal_record(request_id, row, verdict, winner=None):
    """Assemble a ledger record from a late fatal verdict and its request."""
    candidate_id = None
    if isinstance(verdict, dict):
        raw = verdict.get("candidate_id")
        if isinstance(raw, str) and raw.strip():
            candidate_id = raw.strip()
    if candidate_id is None and isinstance(row, dict):
        # Last resort: the prompt names the candidate ("Candidate ID: ...").
        for text in _message_texts(row):
            match = re.search(r"Candidate ID:\s*(\S+)", text)
            if match:
                candidate_id = match.group(1).strip()
                break
    scene_index, requirement_hash = _extract_scene_and_requirement(row, verdict)
    return {
        "request_id": str(request_id),
        "candidate_id": candidate_id,
        "video_id": parse_video_id(candidate_id, verdict),
        "asset_sha256": _asset_sha256(verdict),
        "scene_index": scene_index,
        "requirement_hash": requirement_hash,
        "verdict": verdict if isinstance(verdict, dict) else {},
        "received_at": datetime.now(timezone.utc).isoformat(),
        "winner": winner if isinstance(winner, dict) else None,
    }


def _ledger_path(bridge_root):
    return Path(bridge_root) / LEDGER_FILENAME


def _atomic_json(path, value):
    """Atomic JSON write (same semantics as agent_bridge.atomic_json).

    Kept local — not imported from agent_bridge — because agent_bridge
    imports this module; importing it back would be a circular import.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _ledger_lock(bridge_root):
    """Cross-process lock around the ledger read-modify-write (5s bound)."""
    root = str(Path(bridge_root))
    with _thread_locks_guard:
        lock = _thread_locks.setdefault(root, threading.Lock())
    lock.acquire()
    try:
        lock_path = _ledger_path(bridge_root).with_name(LEDGER_FILENAME + ".lock")
        if os.name != "nt":
            import fcntl

            with lock_path.open("a+b") as stream:
                deadline = time.monotonic() + 5
                while True:
                    try:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("verdict-conflict ledger is locked") from None
                        time.sleep(0.02)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        else:
            yield
    finally:
        lock.release()


def load_fatal_rejects(bridge_root):
    """All ledger records, oldest first; [] when the ledger is absent/bad."""
    try:
        data = json.loads(_ledger_path(bridge_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [record for record in data if isinstance(record, dict)]


def record_late_fatal_reject(bridge_root, record):
    """Append ``record`` to the ledger atomically; return the stored record.

    Derived fields (video_id, asset_sha256, scene_index, requirement_hash,
    received_at) are filled from the record's verdict/candidate when the
    caller did not supply them. A record for the same request + candidate
    already in the ledger is not duplicated — the existing record is
    returned. Raises ValueError when the record has no request_id.
    """
    if not isinstance(record, dict) or not str(record.get("request_id") or "").strip():
        raise ValueError("verdict-conflict record needs a request_id")
    stored = dict(record)
    stored["request_id"] = str(stored["request_id"])
    verdict = stored.get("verdict") if isinstance(stored.get("verdict"), dict) else {}
    stored["verdict"] = verdict
    if stored.get("candidate_id") is None and isinstance(verdict, dict):
        raw = verdict.get("candidate_id")
        if isinstance(raw, str) and raw.strip():
            stored["candidate_id"] = raw.strip()
    if stored.get("video_id") is None:
        stored["video_id"] = parse_video_id(stored.get("candidate_id"), verdict)
    if stored.get("asset_sha256") is None:
        stored["asset_sha256"] = _asset_sha256(verdict)
    if stored.get("scene_index") is None and stored.get("requirement_hash") is None:
        scene_index, requirement_hash = _extract_scene_and_requirement(
            stored.get("request") or {}, verdict
        )
        stored["scene_index"] = scene_index
        stored["requirement_hash"] = requirement_hash
    else:
        if stored.get("scene_index") is not None:
            stored["scene_index"] = _coerce_scene_index(stored["scene_index"])
    if not stored.get("received_at"):
        stored["received_at"] = datetime.now(timezone.utc).isoformat()
    if "winner" not in stored or not isinstance(stored.get("winner"), dict):
        stored["winner"] = stored.get("winner") if isinstance(stored.get("winner"), dict) else None
    with _ledger_lock(bridge_root):
        records = load_fatal_rejects(bridge_root)
        for existing in records:
            if (
                existing.get("request_id") == stored["request_id"]
                and existing.get("candidate_id") == stored.get("candidate_id")
            ):
                return existing
        records.append(stored)
        _atomic_json(_ledger_path(bridge_root), records)
    return stored


def content_key_for(record):
    """The single content key identifying a ledger record's candidate.

    ``(scene_index, video_id)`` when the video id is known, else
    ``(scene_index, asset_sha256)``, else ``(scene_index, candidate_id)``.
    A record with no scene index is keyed by its requirement hash —
    ``("requirement_hash", hash, identifier)`` — so it only matches a
    scene whose requirement hash is the same; with neither a scene nor a
    hash it falls back to ``(None, identifier)``. None when unidentifiable.
    """
    if not isinstance(record, dict):
        return None
    identifier = record.get("video_id") or record.get("asset_sha256") or record.get("candidate_id")
    if not identifier:
        return None
    scene_index = record.get("scene_index")
    if scene_index is not None:
        return (_coerce_scene_index(scene_index), identifier)
    requirement_hash = record.get("requirement_hash")
    if isinstance(requirement_hash, str) and requirement_hash.strip():
        return ("requirement_hash", requirement_hash, identifier)
    return (None, identifier)


def fatal_content_keys(records, scene_index=None, requirement_hash=None):
    """Content keys of the candidates fatally rejected in ``records``.

    ``scene_index`` narrows to one scene (None returns every scene's keys).
    A record with no scene index contributes a key only through its
    requirement hash: when ``requirement_hash`` is given and matches, the
    key is ``(scene_index, identifier)`` for the queried scene (or
    ``(None, identifier)`` when no scene was asked for); when it is not
    given, the key is ``("requirement_hash", hash, identifier)`` so the
    caller can match the hash itself. A hash-less, scene-less record keys
    as ``(None, identifier)``. A record whose requirement hash is given
    and does not match contributes nothing.
    """
    keys = set()
    wanted_scene = _coerce_scene_index(scene_index) if scene_index is not None else None
    for record in records:
        if not isinstance(record, dict):
            continue
        identifier = record.get("video_id") or record.get("asset_sha256") or record.get("candidate_id")
        if not identifier:
            continue
        scene = _coerce_scene_index(record.get("scene_index"))
        if scene is not None:
            if wanted_scene is None or scene == wanted_scene:
                keys.add((scene, identifier))
            continue
        record_hash = record.get("requirement_hash")
        record_hash = record_hash if isinstance(record_hash, str) and record_hash.strip() else None
        if requirement_hash is not None:
            if record_hash and record_hash == requirement_hash:
                keys.add((wanted_scene, identifier))
        elif record_hash:
            keys.add(("requirement_hash", record_hash, identifier))
        else:
            keys.add((None, identifier))
    return keys
