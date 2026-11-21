"""General video pool for the two-track YouTube lane (Track B).

Track B builds a pool of vision-verified clips about the whole video's
topic, in parallel with the scene-by-scene Track A, so a scene whose own
search fails or runs out of discovery budget can still get a real YouTube
clip that is on-topic instead of dropping to a web image.

This module is pure: it builds broad topic queries and holds verified pool
entries with a reuse cap. It performs no provider calls itself. The caller
verifies pool candidates through the existing YouTube pipeline
(search -> metadata rank -> transcript moment -> probe -> vision) and
adds only vision-verified entries via :meth:`YouTubeGeneralPool.add`.

Reuse cap: one pool clip may cover up to 3 scenes (config key
``yt_general_pool_reuse_cap``, env ``VIDZEO_YT_GENERAL_POOL_REUSE_CAP``,
default 3). The cap limits visible repetition while still letting the
pool lift the clip count for scenes Track A could not resolve.
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Mapping, Optional, Sequence

DEFAULT_REUSE_CAP = 3
DEFAULT_MAX_QUERIES = 6
ENV_REUSE_CAP = "VIDZEO_YT_GENERAL_POOL_REUSE_CAP"
CONFIG_REUSE_CAP = "yt_general_pool_reuse_cap"

# Track B enable switch (Nov 21, 2026): the general pool is DISABLED by
# default. It verified 0 clips in GTA test 18 AND test 19 — the parallel
# search finds good candidates, but the pool's fresh downloads time out
# (transport_timeout ~285s, yt-dlp errors), so nothing ever banks.
# Scene-by-scene Track A + the web fallback deliver instead, so the pool
# is extra complexity (daemon thread, separate download queue, more
# vision calls) for zero payoff. The code stays intact: re-enable with
# config ``yt_general_pool_enabled`` / env
# ``VIDZEO_YT_GENERAL_POOL_ENABLED`` if download transport becomes
# reliable enough for fresh pool clips to land.
DEFAULT_GENERAL_POOL_ENABLED = False
ENV_GENERAL_POOL_ENABLED = "VIDZEO_YT_GENERAL_POOL_ENABLED"
CONFIG_GENERAL_POOL_ENABLED = "yt_general_pool_enabled"


def general_pool_enabled(config: Optional[Mapping[str, Any]] = None) -> bool:
    """Whether the Track B general pool (parallel topic-clip build) runs.

    Disabled by default (see above): 0 verified clips in tests 18 and
    19. Config key ``yt_general_pool_enabled`` wins over env
    ``VIDZEO_YT_GENERAL_POOL_ENABLED``; both fall back to disabled.
    """
    settings = config or {}
    raw = settings.get(CONFIG_GENERAL_POOL_ENABLED)
    if raw is None:
        raw = os.environ.get(ENV_GENERAL_POOL_ENABLED)
    if raw is None:
        return DEFAULT_GENERAL_POOL_ENABLED
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")

# Words that carry no topic meaning when they appear in a title. Stripped
# when deriving a bare anchor from a raw title (the adapter's _topic_anchor
# is preferred when available and is used verbatim).
_TITLE_FILLER = {
    "the", "a", "an", "and", "or", "of", "in", "on", "to", "for", "with",
    "what", "how", "why", "when", "is", "are", "was", "were", "part",
    "episode", "documentary", "explained", "story", "history", "inside",
    "behind", "return", "full",
}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _anchor_from_title(title: str) -> str:
    """Best-effort bare topic anchor from a raw title.

    Takes the text before a subtitle separator (``:``, ``—``, ``-``) when
    that head looks like an entity, else the whole title with filler words
    dropped. This is only a fallback: callers should pass the adapter's
    ``_topic_anchor`` when they have it.
    """
    text = _clean(title)
    if not text:
        return ""
    for sep in (":", "—", "–"):
        if sep in text:
            head = _clean(text.split(sep, 1)[0])
            if len(head) >= 3:
                return head
    words = [w for w in re.split(r"[^A-Za-z0-9]+", text) if w]
    kept = [w for w in words if w.lower() not in _TITLE_FILLER]
    return " ".join(kept[:6]) if kept else text


def build_general_pool_queries(
    title: str,
    *,
    topic_anchor: str = "",
    scene_subjects: Sequence[str] = (),
    max_queries: int = DEFAULT_MAX_QUERIES,
) -> List[str]:
    """Broad topic queries for the general pool (Track B).

    One query per template, all anchored on the video's topic — never on a
    single scene's narration. Subject-derived queries use only the most
    common scene subjects so the pool stays general, not scene-specific.
    """
    anchor = _clean(topic_anchor) or _anchor_from_title(title)
    if not anchor:
        return []
    queries: List[str] = []
    for template in (
        "{anchor} gameplay",
        "{anchor} official gameplay",
        "{anchor} trailer",
        "{anchor}",
    ):
        query = _clean(template.format(anchor=anchor))
        if query and query not in queries:
            queries.append(query)
    seen_subjects: List[str] = []
    for subject in scene_subjects or ():
        subject = _clean(subject)
        if not subject or subject.lower() == anchor.lower():
            continue
        if subject not in seen_subjects:
            seen_subjects.append(subject)
        if len(seen_subjects) >= 2:
            break
    for subject in seen_subjects:
        query = _clean(f"{subject} {anchor}")
        if query and query not in queries:
            queries.append(query)
    return queries[: max(1, int(max_queries or DEFAULT_MAX_QUERIES))]


def general_pool_reuse_cap(config: Optional[Mapping[str, Any]] = None) -> int:
    """How many scenes one general-pool clip may cover (default 3)."""
    settings = config or {}
    raw = settings.get(CONFIG_REUSE_CAP)
    if raw is None:
        raw = os.environ.get(ENV_REUSE_CAP)
    try:
        cap = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_REUSE_CAP
    return max(1, cap)


def _tokens(text: str) -> set:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", str(text or "").lower())
        if len(token) >= 3 and token not in _TITLE_FILLER
    }


def _subject_matches(entry_text: str, scene_subject: str, scene_text: str) -> bool:
    """Cheap subject match for a pool claim.

    True when the scene's subject shares a content token with the pool
    entry's title/subject, or when the entry text mentions the subject
    outright. Vision verification is the real gate (only verified entries
    are in the pool); this only picks the best-fitting verified clip.
    """
    entry_tokens = _tokens(entry_text)
    if not entry_tokens:
        return False
    subject_tokens = _tokens(scene_subject)
    if subject_tokens and subject_tokens & entry_tokens:
        return True
    scene_tokens = _tokens(scene_text)
    if len(scene_tokens & entry_tokens) >= 2:
        return True
    subject = _clean(scene_subject).lower()
    return bool(subject) and subject in str(entry_text or "").lower()


@dataclass
class GeneralPoolEntry:
    video_id: str
    title: str = ""
    channel: str = ""
    url: str = ""
    best_start: float = 0.0
    best_end: float = 0.0
    vision_score: float = 0.0
    subjects: List[str] = field(default_factory=list)
    use_count: int = 0
    candidate_id: str = ""
    source_path: str = ""

    def entry_text(self) -> str:
        return " ".join([self.title, self.channel, " ".join(self.subjects)])

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "channel": self.channel,
            "url": self.url,
            "best_start": round(float(self.best_start), 3),
            "best_end": round(float(self.best_end), 3),
            "vision_score": round(float(self.vision_score), 3),
            "subjects": list(self.subjects),
            "use_count": int(self.use_count),
            "candidate_id": self.candidate_id,
            "source_path": self.source_path,
        }


class YouTubeGeneralPool:
    """Verified general-pool clips, with a per-clip reuse cap.

    Entries are added only after vision verification (the caller owns that
    step). ``claim`` picks the best-fitting verified clip for a scene:
    subject-matched first, then highest vision score, then least used.
    A clip that already covers ``reuse_cap`` scenes is never claimed again.

    Thread-safe: the pool is filled by the background Track B builder
    thread while the coordinator claims from it, so ``add`` and ``claim``
    hold a lock.
    """

    def __init__(self, *, reuse_cap: int = DEFAULT_REUSE_CAP) -> None:
        self.reuse_cap = max(1, int(reuse_cap))
        self._entries: List[GeneralPoolEntry] = []
        self._by_video: dict = {}
        self._lock = threading.Lock()

    def add(self, entry: GeneralPoolEntry) -> bool:
        if not entry or not entry.video_id:
            return False
        with self._lock:
            return self._add_locked(entry)

    def _add_locked(self, entry: GeneralPoolEntry) -> bool:
        existing = self._by_video.get(entry.video_id)
        if existing is not None:
            if float(entry.vision_score) > float(existing.vision_score):
                existing.vision_score = float(entry.vision_score)
                existing.best_start = float(entry.best_start)
                existing.best_end = float(entry.best_end)
                existing.title = entry.title or existing.title
                existing.candidate_id = entry.candidate_id or existing.candidate_id
                existing.source_path = entry.source_path or existing.source_path
            for subject in entry.subjects:
                if subject not in existing.subjects:
                    existing.subjects.append(subject)
            return False
        self._entries.append(entry)
        self._by_video[entry.video_id] = entry
        return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def entries(self) -> List[GeneralPoolEntry]:
        with self._lock:
            return list(self._entries)

    def claim(
        self,
        scene_subject: str,
        scene_text: str = "",
        *,
        exclude_video_ids: Iterable[str] = (),
    ) -> Optional[GeneralPoolEntry]:
        with self._lock:
            excluded = {str(v) for v in exclude_video_ids or ()}
            candidates = [
                entry
                for entry in self._entries
                if entry.video_id not in excluded and entry.use_count < self.reuse_cap
            ]
            if not candidates:
                return None
            matched = [
                entry
                for entry in candidates
                if _subject_matches(entry.entry_text(), scene_subject, scene_text)
            ]
            ranked = sorted(
                matched or candidates,
                key=lambda e: (1 if e in matched else 0, float(e.vision_score), -int(e.use_count)),
                reverse=True,
            )
            chosen = ranked[0]
            chosen.use_count += 1
            return chosen

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "reuse_cap": self.reuse_cap,
                "size": len(self._entries),
                "entries": [entry.to_dict() for entry in self._entries],
            }


_POOL_PATH_VIDEO_RE = re.compile(r"cand([A-Za-z0-9_-]{6,})_ts\d+")


def _video_id_from_pool_path(path: str) -> str:
    match = _POOL_PATH_VIDEO_RE.search(str(path or ""))
    return match.group(1) if match else ""


def _accepted_attempt_row(rows: Any) -> Optional[Mapping[str, Any]]:
    """The winning attempt row for one pool pseudo-scene, if any.

    First preference: a row carrying a live vision ``accepted`` verdict
    (``visual_status == "accepted"``) — the stamp the branch's batched
    probe-vision path writes. Second: a row the branch itself verified
    through its download verification (``status == "downloaded"`` with a
    real cut path). The second tier is the normal Mixed Media outcome,
    not a weakening: the child worker forces
    ``visual_context.youtube_contact_only = True``
    (``services/youtube_branch_worker.py`` — "The parent coordinator
    owns final per-scene verification"), so transport vision is
    intentionally disabled in ``_download_scene``
    (``services/youtube_pipeline_v2.py``) and a ranged-download row
    NEVER carries ``visual_status`` — requiring it banked 0 clips even
    on a perfect 4/4 branch run (live repro, Nov 21 2026). The adapter
    accepts exactly these downloaded rows as Track A cuts
    (``pipeline_v2_media_adapter.py`` merge into
    ``accepted_cut_by_scene``) and re-screens the exact path as its
    fail-closed boundary; the coordinator still applies its hard gate
    and fatal-ledger veto to a claimed pool clip. A downloaded row is
    therefore branch-verified (metadata rank, transcript moment, probe
    quality/overlay/motion screens, overlay screen — the worker log's
    "verification audit"), not LLM-vision-verified, and the entry's
    ``vision_score`` falls back to the row's ``metadata_score`` when no
    vision score exists. Anything else (rejected, motion/quality
    rejected, no path) is dropped, never banked as verified.
    """
    if not isinstance(rows, (list, tuple)):
        return None
    accepted = [
        row for row in rows
        if isinstance(row, Mapping)
        and str(row.get("visual_status") or "").strip().lower() == "accepted"
    ]
    if accepted:
        return accepted[-1]
    downloaded = [
        row for row in rows
        if isinstance(row, Mapping)
        and str(row.get("status") or "").strip().lower() == "downloaded"
        and (row.get("selected") or row.get("cleaned_path") or row.get("path"))
    ]
    if downloaded:
        return downloaded[-1]
    return None


def build_general_pool(
    runner,
    *,
    queries: Sequence[str],
    title: str,
    script: str,
    run_config: Mapping[str, Any],
    output_dir: str,
    reuse_cap: int = DEFAULT_REUSE_CAP,
    cancellation=None,
    progress_callback=None,
) -> YouTubeGeneralPool:
    """Run the YouTube branch once per broad topic query and fill the pool.

    ``runner`` is the adapter's YouTube branch runner
    (``app._run_youtube_clips_branch``): it takes ``(scene_texts,
    scene_durations, config, title, script, ...)``. Each broad query is
    passed as one pseudo-scene, so the branch does its normal
    search -> rank -> transcript moment -> probe -> vision -> ranged
    download for that query. Only clips whose attempt row carries a live
    vision ``accepted`` verdict — or, failing that, a branch-verified
    download (``status == "downloaded"`` with a real cut path; see
    :func:`_accepted_attempt_row` for why the second tier exists) —
    become pool entries; anything else is dropped, never banked as
    verified.

    The caller owns the run config (timeouts, output dir). This function
    never raises for a branch failure: a failed pool build returns an
    empty pool and Track A proceeds alone.
    """
    pool = YouTubeGeneralPool(reuse_cap=reuse_cap)
    query_list = [_clean(q) for q in (queries or []) if _clean(q)]
    if not query_list or runner is None:
        return pool
    pool_config = dict(run_config or {})
    pool_config["_run_temp_dir"] = str(output_dir or "")
    pool_config["_yt_scene_query_overrides"] = []
    pool_config["_scene_timing_windows"] = None
    # Marker for callers/tests: this is a Track B general-pool build, not
    # the primary scene-by-scene lane. It carries no prep manifest title
    # by design (fresh topic search only — prep cuts belong to Track A).
    pool_config["_youtube_general_pool_build"] = True
    try:
        values = runner(
            list(query_list),
            [5.0] * len(query_list),
            pool_config,
            str(title or ""),
            str(script or ""),
            progress_callback=progress_callback,
            cancellation=cancellation,
            prep_manifest_title="",
        )
    except Exception as exc:
        # Test 18 failed silently here: the pool reported "0 verified"
        # with no clue whether search, vision, or the wall killed it.
        # Name the failure; Track A still proceeds alone.
        print(
            "   [v2] general YouTube pool build failed: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return pool
    audit = dict(getattr(values, "audit", None) or {})
    attempts = audit.get("attempts") or {}
    # Per-query diagnostics (test 18 gap: the log showed only "0 verified
    # clip(s)" — no candidate counts, no vision verdicts, no hint that the
    # branch wall had killed the worker before any clip resolved). The
    # audit's "attempts" ledger is the only place a live vision verdict
    # lives; a timeout/salvage result carries no "attempts" key at all, so
    # say so explicitly instead of silently banking zero.
    if not isinstance(attempts, Mapping):
        attempts = {}
    if "attempts" not in audit:
        print(
            "   [v2] general YouTube pool audit has no 'attempts' ledger "
            f"(keys: {sorted(str(k) for k in audit.keys()) or ['<none>']}); "
            "branch likely hit its wall before returning a result",
            flush=True,
        )
    for local_index, path in enumerate(list(values or [])):
        if not path:
            continue
        rows = attempts.get(str(local_index))
        if rows is None:
            rows = attempts.get(local_index)
        row = _accepted_attempt_row(rows)
        if row is None:
            verdicts = sorted({
                str(item.get("visual_status") or item.get("status") or "unknown")
                for item in (rows or [])
                if isinstance(item, Mapping)
            })
            print(
                "   [v2] general YouTube pool query "
                f"{local_index + 1}/{len(query_list)} "
                f"({query_list[local_index]!r}): no accepted clip "
                f"({len(rows or [])} attempt row(s); "
                f"verdicts: {verdicts or ['<no rows>']})",
                flush=True,
            )
            continue
        video_id = str(row.get("video_id") or row.get("candidate_id") or "") or _video_id_from_pool_path(str(path))
        if not video_id:
            continue
        try:
            start = float(row.get("timestamp") or 0.0)
        except (TypeError, ValueError):
            start = 0.0
        try:
            duration = float(row.get("clip_duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        try:
            vision_score = float(row.get("visual_score") or row.get("metadata_score") or 0.0)
        except (TypeError, ValueError):
            vision_score = 0.0
        pool.add(GeneralPoolEntry(
            video_id=video_id,
            title=str(row.get("title") or "")[:180],
            channel=str(row.get("channel") or "")[:120],
            url=str(row.get("source_url") or "")[:500],
            best_start=start,
            best_end=start + duration if duration > 0 else start,
            vision_score=vision_score,
            subjects=[query_list[local_index]],
            candidate_id=video_id,
            source_path=str(path),
        ))
    return pool
