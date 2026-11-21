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
        "job_id": "62d7671a",                # the job that produced the verdict,
                                             # else None (legacy / unattributable)
        "verdict": {...},                    # the full raw late verdict dict
        "received_at": "2026-11-17T00:24:10+00:00",  # ISO-8601 UTC
        "winner": {...},                     # verdict already recorded, else None
    }

Job scoping (run 8, job 62d7671a): a record vetoes ONLY the job that
produced it. The ledger originally had no job identity and no expiry, so
every fatal verdict ever recorded — including adapter-fatal records from
an earlier job with the same script (same scene indices, same
requirement hashes) — vetoed the same (clip, scene) in every later job
before any fresh vision read. ``records_for_job`` is the filter the
coordinator seeds from: records with no ``job_id`` are legacy and
unattributable, so they are treated as expired and never seed a job.
Within a job the veto still holds exactly as before (including across
that job's resume attempts, which share its job id); a fresh job gets a
fresh vision read instead of a veto. A late verdict that contradicts an
earlier ACCEPT for the same (clip, scene) — job 62d7671a scene 0,
youtube:c5qhgwwLY50, accepted in job 6624244a at visual final 82.0 then
vetoed in 62d7671a by a scene-less late record filed during 6624244a —
is therefore confined to the job that filed it.

Content-key rule (the coordinator matches on these keys): a candidate is
identified by ``(scene_index, video_id)`` when the video id is known, else
``(scene_index, asset_sha256)``, else ``(scene_index, candidate_id)``. A
record with ``scene_index`` None applies to *any* scene only when its
``requirement_hash`` matches — see :func:`fatal_content_keys`.

Per-scene scoping (run 9, job a7f52c58): a fatal vetoes ONLY the scene it
was filed for. The lookup (:func:`fatal_content_keys` with a scene, and
the coordinator's ``_fatal_ledger_lookup``) matches a record only through
its scene index or through the queried scene's requirement hash. A
scene-less record with no requirement hash cannot be attributed to any
scene, so it vetoes nothing — the old ``(None, identifier)`` fallback
key, which matched EVERY scene, is gone. Job scoping is unchanged: a
record still vetoes only the job that filed it.

Reasoning-coherence gate (run 9, job a7f52c58, scene 4): a fatal verdict
whose reasoning is cross-contaminated must not be filed at all — the clip
is still rejected for that one scene on its own merits, but no ledger
entry is created. The stored verdict for ``youtube:_yl5ELcmugI:frame:1``
(final_score 40) reasons "the pixels are not the yacht from the earlier
clip review" — there is no yacht in that request; the yacht came from
the same clip's scene-10 review ("a speeding yacht"). See
:func:`verdict_reason_coherence_issue`. The check is deliberately
conservative: only a clear cross-review reference (or a concrete entity
explicitly attributed to another review and grounded in neither the
request text nor the candidate metadata) blocks filing, so genuine
fatals — e.g. ``youtube:AKoZY5TgpjI`` scene 12, a Germany travel
documentary ("Real-world drone footage of Dresden, Germany") — still
file. Identity notes ("same pixels as the earlier X review") assert the
frames ARE the same pixels and do not block filing.
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

# --- Reasoning-coherence gate (run 9) -------------------------------------
# A fatal verdict's REASONING can be cross-contaminated even when its
# conclusion is right: the scene-4 yacht verdict reasons about "the yacht
# from the earlier clip review", content from another review. Only a
# CLEAR cross-review reference blocks filing (conservative on purpose):
# the clip is still rejected for its own scene, only the ledger entry is
# skipped, so a false positive costs one re-read, not a lost veto.
#
# Identity notes are NOT contamination: "same pixels as the earlier X
# review" asserts these frames ARE the pixels reviewed before — the
# operative claims still describe this frame. They are stripped before
# matching so genuine fatals carrying them still file.
_CROSS_REVIEW_RE = re.compile(
    r"\b(earlier|previous|other|another|prior|different)\b[^.;]{0,60}"
    r"\b(review|clip|video|footage|frame)\b",
    re.IGNORECASE,
)
_IDENTITY_CLAUSE_RES = (
    re.compile(r"same pixels as[^.;]*", re.IGNORECASE),
    re.compile(r"identical pixels to[^.;]*", re.IGNORECASE),
    re.compile(
        r"the same [^.;]{0,80}? as (?:the scene \d+|frame \d+)[^.;]*?review",
        re.IGNORECASE,
    ),
)
# "the <entity> from the earlier clip review": a concrete entity the
# reason explicitly attributes to another review. The entity must be
# grounded in neither the request text nor the candidate metadata.
_FOREIGN_ENTITY_RE = re.compile(
    r"\b(?:the|a|an|that|this)\s+([A-Za-z][\w-]*)\s+from\s+the\s+"
    r"(?:earlier|previous|other|another|prior)\s+"
    r"(?:clip|video|frame|footage)\s+review\b",
    re.IGNORECASE,
)
# Distinctive concrete nouns only: generic descriptors ("woman",
# "nightclub", "game") are what a genuine wrong-content reject names,
# so they must never trigger on their own. A noun here triggers only
# inside the attribution construction above.
_FOREIGN_ENTITY_NOUNS = frozenset({
    "yacht", "boat", "ship", "sailboat", "speedboat", "ferry", "cruise",
    "submarine", "helicopter", "airplane", "aeroplane", "aircraft", "jet",
    "rocket", "satellite", "train", "locomotive", "tram", "bicycle",
    "motorcycle", "motorbike", "horse", "dog", "cat", "elephant", "lion",
    "tiger", "bear", "wolf", "shark", "whale", "dolphin", "castle",
    "palace", "cathedral", "church", "mosque", "temple", "pyramid",
    "lighthouse", "bridge", "tower", "skyscraper", "stadium", "arena",
    "beach", "island", "mountain", "volcano", "desert", "forest",
    "jungle", "river", "lake", "waterfall", "glacier", "canyon", "tank",
    "bus", "truck", "trailer", "caravan",
})

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


def _verdict_reason_text(verdict):
    """The verdict's reasoning text (reason + scene_description)."""
    if not isinstance(verdict, dict):
        return ""
    parts = []
    for field in ("reason", "scene_description"):
        text = verdict.get(field)
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return " ".join(parts)


def _strip_identity_clauses(text):
    """Remove pixel-identity notes ("same pixels as the earlier X review").

    Those clauses assert these frames ARE the pixels reviewed before;
    they are not reasoning imported from another review, so they must
    not trip the cross-review check.
    """
    for pattern in _IDENTITY_CLAUSE_RES:
        text = pattern.sub("", text)
    return text


def _context_words(context_text):
    """Lower-cased word set of the request / candidate-metadata text."""
    if not isinstance(context_text, str):
        return set()
    return set(re.findall(r"[a-z][a-z-]*", context_text.lower()))


def verdict_reason_coherence_issue(verdict, context_text=None):
    """Why a fatal verdict's reasoning must NOT be filed, or None.

    Returns a short tag (``"cross_review_reference:..."`` or
    ``"foreign_entity_from_other_review:<entity>"``) when the reasoning
    is clearly cross-contaminated, else None (coherent — file it).

    ``context_text`` is the request text (scene narration, visual
    requirement, film context, metadata hint) plus candidate metadata,
    used only by the foreign-entity signal: an entity attributed to
    another review blocks filing only when it appears in NEITHER the
    request text NOR the candidate metadata. With no context the
    foreign-entity signal is skipped and only the self-contained
    cross-review signal applies. A missing/empty reason is coherent:
    absence of reasoning is not evidence of contamination.
    """
    reason = _verdict_reason_text(verdict)
    if not reason:
        return None
    remainder = _strip_identity_clauses(reason)
    # Specific signal first: a concrete entity explicitly attributed to
    # another review ("the yacht from the earlier clip review") that is
    # grounded in neither the request text nor the candidate metadata.
    # With no context this signal is skipped and the general signal
    # below applies.
    if context_text is not None:
        words = _context_words(context_text)
        for entity_match in _FOREIGN_ENTITY_RE.finditer(remainder):
            entity = entity_match.group(1).lower()
            if entity in _FOREIGN_ENTITY_NOUNS and entity not in words:
                return "foreign_entity_from_other_review:" + entity
    # General signal: any clear cross-review reference in the reasoning.
    match = _CROSS_REVIEW_RE.search(remainder)
    if match:
        return "cross_review_reference:" + match.group(0)
    return None


def verdict_reason_coherent(verdict, context_text=None) -> bool:
    """True when a fatal verdict's reasoning is safe to file in the ledger."""
    return verdict_reason_coherence_issue(verdict, context_text) is None


def build_coherence_context(scene=None, candidate=None, extra_text=""):
    """Request/candidate text for the foreign-entity signal.

    Pulls the text-ish fields the callers have: scene narration and
    requirement terms, candidate title/description/tags, plus any extra
    text (e.g. a bridge request's message texts). Missing fields are
    skipped; the result is a plain string for
    :func:`verdict_reason_coherence_issue`.
    """
    parts = []
    if isinstance(extra_text, str) and extra_text.strip():
        parts.append(extra_text.strip())
    for row in (scene, candidate):
        if not isinstance(row, dict):
            continue
        for field in ("text", "narration", "title", "description",
                      "channel", "channel_title", "film_context",
                      "metadata_hint", "canonical_entity"):
            value = row.get(field)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
            elif isinstance(value, list):
                parts.extend(str(item).strip() for item in value
                             if isinstance(item, str) and item.strip())
        requirement = row.get("visual_requirement")
        if isinstance(requirement, dict):
            for field in ("canonical_entity", "subject_context"):
                value = requirement.get(field)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
            for field in ("positive_evidence", "required_terms", "aliases",
                          "action_terms"):
                values = requirement.get(field)
                if isinstance(values, list):
                    parts.extend(str(item).strip() for item in values
                                 if isinstance(item, str) and item.strip())
        metadata = row.get("metadata")
        if isinstance(metadata, dict):
            for field in ("title", "description", "channel_title"):
                value = metadata.get(field)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
    return " ".join(parts)


def request_context_text(row):
    """All prompt text of a bridge request row, for the coherence check.

    The scene narration, visual requirement, film context and metadata
    hint a verdict was asked about — the grounding text a foreign entity
    named in the reasoning is checked against.
    """
    return " ".join(_message_texts(row))


def _extract_job_id(row, verdict=None):
    """The job that produced a verdict, or None when unattributable.

    Bridge request rows carry ``job_id`` (job 4f61cc9e and every queue job
    since); adapter-fatal records are stamped by the adapter from the
    current job context. A verdict dict may also carry it.
    """
    for source in (row, verdict):
        if isinstance(source, dict):
            raw = source.get("job_id")
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return None


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
        "job_id": _extract_job_id(row, verdict),
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
    received_at, job_id) are filled from the record's verdict/candidate
    when the caller did not supply them. A record for the same request +
    candidate + job already in the ledger is not duplicated — the existing
    record is returned. The job is part of the dedupe key on purpose:
    adapter-fatal request ids are deterministic per (scene, content), so
    without it a re-record of the same content in a NEW job would return
    the OLD job's record (with its old ``received_at`` and no job) instead
    of filing a fresh, job-scoped one.
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
    if stored.get("job_id") is None:
        stored["job_id"] = _extract_job_id(stored.get("request") or {}, verdict)
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
                and (existing.get("job_id") or None) == (stored.get("job_id") or None)
            ):
                return existing
        records.append(stored)
        _atomic_json(_ledger_path(bridge_root), records)
    return stored


def records_for_job(records, job_id):
    """Only the ledger records produced by ``job_id``.

    Records with no ``job_id`` predate job scoping (every record filed
    before this fix, including the 133 live ones) and cannot be attributed
    to any job, so they are treated as expired: they never veto a later
    job. A record vetoes only the job that filed it — the coordinator
    seeds from this, and the in-memory ledger still vetoes re-presentation
    within the job itself. ``job_id`` None returns []: without a job
    identity there is nothing safe to seed.
    """
    if not job_id:
        return []
    wanted = str(job_id).strip()
    return [
        record
        for record in (records or [])
        if isinstance(record, dict) and str(record.get("job_id") or "").strip() == wanted
    ]


def content_key_for(record):
    """The single content key identifying a ledger record's candidate.

    ``(scene_index, video_id)`` when the video id is known, else
    ``(scene_index, asset_sha256)``, else ``(scene_index, candidate_id)``.
    A record with no scene index is keyed by its requirement hash —
    ``("requirement_hash", hash, identifier)`` — so it only matches a
    scene whose requirement hash is the same; with neither a scene nor a
    hash it falls back to ``(None, identifier)``. None when unidentifiable.
    Note: the ``(None, identifier)`` key is inspection-only — the
    per-scene lookup never matches it, so a hash-less scene-less record
    vetoes no scene.
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


def records_for_scene(records, scene_index, requirement_hash=None):
    """Only the ledger records filed for one scene.

    A record belongs to the scene when its ``scene_index`` matches, or —
    for scene-less records — when its ``requirement_hash`` matches the
    scene's hash. A scene-less record with no requirement hash cannot be
    attributed to any scene and never belongs to one. ``scene_index``
    None returns []: without a scene there is nothing safe to attribute.
    """
    wanted_scene = _coerce_scene_index(scene_index) if scene_index is not None else None
    if wanted_scene is None:
        return []
    wanted_hash = requirement_hash if isinstance(requirement_hash, str) and requirement_hash.strip() else None
    kept = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        scene = _coerce_scene_index(record.get("scene_index"))
        if scene is not None:
            if scene == wanted_scene:
                kept.append(record)
            continue
        record_hash = record.get("requirement_hash")
        record_hash = record_hash if isinstance(record_hash, str) and record_hash.strip() else None
        if record_hash and wanted_hash and record_hash == wanted_hash:
            kept.append(record)
    return kept


def fatal_content_keys(records, scene_index=None, requirement_hash=None, job_id=None):
    """Content keys of the candidates fatally rejected in ``records``.

    ``scene_index`` narrows to one scene (None returns every scene's keys).
    Per-scene scoping (run 9): a fatal vetoes only the scene it was filed
    for. A record with a scene index contributes its ``(scene,
    identifier)`` key only for that scene. A scene-less record contributes
    only through its requirement hash: when ``requirement_hash`` is given
    and matches, the key is ``(scene_index, identifier)`` for the queried
    scene (or ``(None, identifier)`` when no scene was asked for); when
    it is not given, the key is ``("requirement_hash", hash, identifier)``
    so the caller can match the hash itself — the hash is that record's
    only scene attribution. A scene-less record with NO requirement hash
    contributes NOTHING when a scene is queried: it cannot be attributed
    to any scene, so it vetoes none (the old ``(None, identifier)`` key,
    which matched every scene, is gone). With no scene queried the
    whole-ledger behaviour is preserved for inspection, including the
    ``(None, identifier)`` key for hash-less scene-less records.

    ``job_id`` narrows to one job: only records filed by that job
    contribute. Records with no ``job_id`` (pre-scoping legacy) contribute
    nothing when a job is given — they are expired, not attributable.
    With ``job_id`` None the old unfiltered behaviour is preserved for
    callers that inspect the whole ledger.
    """
    if job_id is not None:
        records = records_for_job(records, job_id)
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
        elif wanted_scene is None:
            keys.add((None, identifier))
    return keys
