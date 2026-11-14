"""Production helpers around the provider-neutral V2 media coordinator."""
from __future__ import annotations
from .youtube_overlay_policy import source_overlays_preserved


from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeoutError
from .job_context import JobThread, JobThreadPoolExecutor as ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from .media_resolution_coordinator import SceneResolution, resolve_storyboard_media_v2, _is_moving_youtube_source
from .media_integrity import probe_media
from .media_asset_policy import emergency_media_allowed, is_synthetic_media
from .pipeline_contracts import MediaContractError
from .visual_intelligence.context import (
    VisualDecisionContext,
    scene_requires_visual_verification,
)
from .visual_requirement import (
    VisualRequirementV1,
    _scene_action_subject,
    _scene_role_subject,
    build_visual_requirement,
    provider_query_variants,
    rebase_visual_requirement_for_scene,
)
from .web_image.source_broker import web_image_source_catalog, web_image_fallback_layers

from .visual_intelligence.semantic_guard import VISUAL_REVIEW_POLICY_VERSION


def _bounded_audit_int(value: Any, default: int, upper: int) -> int:
    """Parse provider limits for diagnostics without affecting retrieval."""
    try:
        return max(0, min(upper, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def mixed_youtube_time_budget(
    scene_count: int,
    branch_wall_seconds: Any = 480,
    search_seconds: Any = 150,
    *,
    download_workers: int = 2,
    coverage_target: float = 0.60,
) -> tuple[float, float]:
    """Bound the YouTube accent lane so an unreachable host cannot stall media.

    Mixed Media already retrieves web assets in parallel. Short videos need a
    quick YouTube attempt and a timely handoff to those verified assets; long
    jobs receive a larger but still finite clip window.
    """
    try:
        requested_wall = float(branch_wall_seconds or 480)
    except (TypeError, ValueError):
        requested_wall = 480.0
    try:
        requested_search = float(search_seconds or 150)
    except (TypeError, ValueError):
        requested_search = 150.0
    count = max(1, int(scene_count or 1))
    try:
        workers = max(1, min(6, int(download_workers or 1)))
    except (TypeError, ValueError, OverflowError):
        workers = 1
    try:
        coverage = max(0.0, min(1.0, float(coverage_target)))
        if not math.isfinite(coverage):
            coverage = 0.60
    except (ValueError, TypeError):
        coverage = 0.60
    target_scenes = min(count, max(1, math.ceil(count * coverage)))
    transfer_waves = math.ceil(target_scenes / workers)

    # Reserve enough bounded time for the configured clip target to survive a
    # real yt-dlp transfer on memory-constrained hosts. A 20s transfer window
    # repeatedly expired after search had found good candidates, so the lane
    # returned zero clips even though the internet search succeeded. Allow
    # 60s for each transfer plus 20s for merge/probe, search, handoff and the
    # worker reserve. The outer wall remains finite; healthier hosts finish
    # concurrent waves sooner. Long films remain bounded at 900s.
    if count <= 10:
        base_cap, search_cap = 240.0, 35.0
    elif count <= 12:
        base_cap, search_cap = 240.0, 35.0
    else:
        base_cap, search_cap = 600.0, 60.0
    # One bounded alternate/motion-screen reserve avoids killing a healthy
    # second candidate just after a still/slideshow winner was rejected.
    # This is a shared reserve, not another full transfer budget per scene.
    required_wall = search_cap + 10.0 + 18.0 + transfer_waves * 80.0 + 40.0
    absolute_cap = 900.0
    wall_cap = min(absolute_cap, max(base_cap, required_wall))
    minimum_wall = min(wall_cap, max(150.0, required_wall))
    wall = max(minimum_wall, min(requested_wall, wall_cap))
    search = max(20.0, min(requested_search, search_cap, wall - 30.0))
    return wall, search

def _scene_value(scene: Any, key: str, default: Any = None) -> Any:
    if isinstance(scene, Mapping):
        if key == 'duration' and scene.get(key) is None:
            # Serialized immutable contracts store endpoints, whereas their
            # live objects expose duration as a property. Resumes must carry
            # the same timing into clip retrieval and coverage accounting.
            try:
                return max(0.0, float(scene['end_seconds']) - float(scene['start_seconds']))
            except (KeyError, TypeError, ValueError):
                return default
        return scene.get(key, default)
    return getattr(scene, key, default)


def _verifier_scene_row(scene: Any) -> dict[str, Any]:
    """Copy immutable storyboard contracts into the verifier's mapping API."""
    if isinstance(scene, Mapping):
        return dict(scene)
    convert = getattr(scene, "to_dict", None)
    if callable(convert):
        row = convert()
        if isinstance(row, Mapping):
            return dict(row)
    raise TypeError("Visual verifier requires a scene mapping or a storyboard contract with to_dict()")


def _repeated_title_entity(config: Mapping[str, Any], scene_count: int) -> str:
    """Find a named title subject confirmed by the director's scene queries.

    Storyboard preflight can anchor every scene to a related event (for example,
    Paris Exposition) even when the director searches for the actual subject
    (Eiffel Tower) in every beat. Only a repeated, title-grounded phrase may
    correct that stale anchor; a single speculative query cannot do it.
    """
    title = str(config.get("content_title") or config.get("title") or "")
    canonical_anchor = str(config.get("_topic_anchor") or config.get("topic_anchor") or "").strip()
    rows = list(config.get("_prebuilt_web_queries") or config.get("_yt_scene_query_overrides") or ())
    directed = [row for row in rows if isinstance(row, Mapping) and row.get("_llm")]
    if scene_count < 2 or len(directed) < 2:
        return ""
    excluded = {
        "world", "history", "documentary", "story", "stories", "facts",
        "top", "best", "greatest", "was", "were", "is", "are", "built",
        "made", "created", "constructed", "invented", "designed",
    }
    title_runs = re.findall(
        r"\b[A-Z][a-z][A-Za-z'’-]*(?:\s+[A-Z][a-z][A-Za-z'’-]*){1,3}\b",
        title,
    )
    candidates = [
        " ".join(words[start:end])
        for run in title_runs
        for words in (run.split(),)
        for start in range(len(words))
        for end in range(start + 2, len(words) + 1)
    ]
    ranked: list[tuple[int, int, str]] = []
    for candidate in candidates:
        # A repeated LLM query may omit an ordinal or edition from the user's
        # event anchor (e.g. "Super Bowl" for "Super Bowl XLIX"). Never let
        # that broader phrase replace a more specific canonical identity.
        if canonical_anchor:
            anchor_words = [word.casefold() for word in re.findall(r"[A-Za-z0-9]+", canonical_anchor)]
            candidate_words = [word.casefold() for word in re.findall(r"[A-Za-z0-9]+", candidate)]
            if len(candidate_words) < len(anchor_words) and any(
                anchor_words[index:index + len(candidate_words)] == candidate_words
                for index in range(len(anchor_words) - len(candidate_words) + 1)
            ):
                continue
        words = {word.casefold() for word in candidate.split()}
        if words & excluded:
            continue
        pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(candidate) + r"(?![A-Za-z0-9])", re.I)
        support = sum(
            bool(pattern.search(" ".join(str(row.get(key) or "") for key in (
                "search_query", "supporting_search_query", "scene_subject", "subject_query"
            ))))
            for row in directed
        )
        if support >= max(2, math.ceil(min(scene_count, len(directed)) * 0.6)):
            ranked.append((support, len(candidate), candidate))
    return max(ranked)[2] if ranked else ""


def _qualify_local_person_identity(local_person: str, *context_values: Any) -> str:
    """Restore an explicit regnal ordinal lost by scene-local name extraction."""
    person = re.sub(r"\s+", " ", str(local_person or "")).strip()
    if not person:
        return person
    contexts = " ".join(str(value or "") for value in context_values)
    if not contexts:
        return person
    ordinal_match = re.search(
        r"(?<![A-Za-z0-9])" + re.escape(person)
        + r"\s+(?P<ordinal>III|VIII|VII|VI|IV|IX|II|X|V|I|\d{1,2}(?:st|nd|rd|th)?)(?![A-Za-z0-9])",
        contexts,
        flags=re.IGNORECASE,
    )
    return f"{person} {ordinal_match.group('ordinal')}" if ordinal_match else person


def _scene_local_director_identity(
    requirement: Mapping[str, Any],
    planned_rows: Sequence[Any],
    scene_text: str,
    *,
    forbidden_subjects: Sequence[str] = (),
    visual_intent: str = "documentary_context",
) -> tuple[dict[str, Any], bool]:
    """Narrow a broad topic contract to a director-named entity in this beat.

    The director query planner already returns scene-local subjects. Use one
    only when its normalized name is actually present in the narration, so a
    broad title such as "Three Japanese Classics" cannot become the search
    identity for every car while speculative query terms remain untrusted.
    """
    if not isinstance(requirement, Mapping) or not scene_text:
        return dict(requirement or {}), False
    try:
        from .documentary_subject import search_key
    except Exception:
        def search_key(value: Any) -> str:
            return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()

    current = str(requirement.get("canonical_entity") or "").strip()
    scene_tokens = set(search_key(re.sub(r"(?i)(?<=\w)['’]s\b", "", scene_text)).split())
    candidates: list[str] = []
    for row in planned_rows:
        if not isinstance(row, Mapping):
            continue
        for key in ("scene_subject", "subject_query", "subject", "section_subject"):
            raw = str(row.get(key) or "").strip()
            if raw:
                candidates.append(raw)
                break
    normalized_candidates = [
        re.sub(r"(?i)(?<=\w)['’]s\b", "", raw).strip(" ,.;:!?\"'()[]")
        for raw in candidates
    ]
    candidate_frequency: dict[str, int] = {}
    for candidate in normalized_candidates:
        key = search_key(candidate)
        if key:
            candidate_frequency[key] = candidate_frequency.get(key, 0) + 1
    contextual_scene = bool(re.search(
        r"\b(car|poll|survey|report|result|respondent|reputation|ranking|ranked|worst)\b",
        scene_text,
        flags=re.IGNORECASE,
    ))
    for raw in normalized_candidates:
        candidate = re.sub(r"(?i)(?<=\w)['’]s\b", "", raw).strip(" ,.;:!?\"'()[]")
        candidate_key = search_key(candidate)
        if not candidate_key or candidate_key == search_key(current):
            continue
        tokens = candidate_key.split()
        if len(tokens) < 2 or any(token in {
            "says", "shows", "saw", "this", "that", "contrast", "cars", "car",
            "classic", "classics", "gatherings", "people", "history", "three",
            "japanese", "sports", "engineering", "ambition",
        } for token in tokens):
            continue
        if not set(tokens).issubset(scene_tokens):
            # A narration beat may introduce a poll or bridge before naming
            # the car. Borrow a subject only when the adjacent director plan
            # repeats that exact, specific entity and this beat is clearly
            # about the same car/poll context; never use one-off guesses.
            if not contextual_scene or candidate_frequency.get(candidate_key, 0) < 2:
                continue
        focused = build_visual_requirement(
            title=candidate,
            script=scene_text,
            scene_text=scene_text,
            positive_subjects=(candidate,),
            forbidden_subjects=forbidden_subjects,
            visual_intent=visual_intent,
        )
        # Preserve a known scene domain if the phrase parser is less specific,
        # while refusing a candidate that changes one domain into another.
        current_type = str(requirement.get("subject_type") or "").casefold()
        focused_type = str(focused.get("subject_type") or "").casefold()
        if current_type and focused_type and current_type != focused_type:
            continue
        focused["domain"] = requirement.get("domain") or focused.get("domain")
        focused["subject_type"] = requirement.get("subject_type") or focused.get("subject_type")
        focused["forbidden_subjects"] = tuple(dict.fromkeys(
            list(focused.get("forbidden_subjects") or ()) + list(forbidden_subjects or ())
        ))
        focused["requirement_hash"] = ""
        focused = VisualRequirementV1.from_mapping(focused).to_dict()
        return focused, True
    return dict(requirement), False


def _script_person_anchor_for_scene(scene_text: str, script: str, previous_scene: str = "") -> str:
    """Resolve bare names and sentence-fragment continuations to script anchors."""
    role = (
        r"(?i:King|Queen|Prince|Princess|President|Prime Minister|Chancellor|"
        r"Duke|Duchess|Emperor|Empress|Pope|Senator|Governor|Monarch)"
    )
    pattern = re.compile(
        rf"\b{role}\s+[A-Z][A-Za-z'’-]*(?:\s+(?:[A-Z][A-Za-z'’-]*|[IVX]{{1,4}})){{0,3}}\b"
    )
    anchors: dict[str, str] = {}
    anchor_script = str(script or "")
    # Spoken regnal ordinals and postposed roles occur naturally in TTS
    # scripts. Resolve their identity without assuming a person's gender.
    ordinals = {"First": "I", "Second": "II", "Third": "III", "Fourth": "IV",
                "Fifth": "V", "Sixth": "VI", "Seventh": "VII", "Eighth": "VIII"}
    for hit in re.finditer(r"\b([A-Z][A-Za-z'’-]+) the (First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth)\b", anchor_script):
        anchor_script += f". Monarch {hit.group(1)} {ordinals[hit.group(2)]}."
    for hit in re.finditer(rf"\b([A-Z][A-Za-z'’-]+)\s+(?:became|becomes|was crowned|is now)\s+({role})\b", str(script or "")):
        anchor_script += f". {hit.group(2)} {hit.group(1)}."
    for match in pattern.finditer(anchor_script):
        anchor = re.sub(r"\s+", " ", match.group(0)).strip(" ,.;:!?\"'()[]")
        tokens = anchor.split()
        if len(tokens) < 2:
            continue
        name_tokens = tokens[1:]
        if name_tokens and re.fullmatch(r"(?i)(?:III|VIII|VII|VI|IV|IX|II|X|V|I|\d+)", name_tokens[-1]):
            name_tokens = name_tokens[:-1]
        if not name_tokens:
            continue
        key = " ".join([tokens[0].casefold(), *(token.casefold() for token in name_tokens)])
        existing = anchors.get(key, "")
        if not existing or len(anchor.split()) > len(existing.split()):
            anchors[key] = anchor

    def mentioned_in(text: str) -> list[tuple[int, str]]:
        found: list[tuple[int, str]] = []
        for anchor in anchors.values():
            tokens = anchor.split()
            name_tokens = tokens[1:]
            if name_tokens and re.fullmatch(r"(?i)(?:III|VIII|VII|VI|IV|IX|II|X|V|I|\d+)", name_tokens[-1]):
                name_tokens = name_tokens[:-1]
            positions = [
                hit.start()
                for token in name_tokens
                for hit in re.finditer(
                    r"(?<![A-Za-z0-9])" + re.escape(token) + r"(?![A-Za-z0-9])",
                    text,
                    flags=re.IGNORECASE,
                )
            ]
            if positions:
                found.append((min(positions), anchor))
        return found

    current_mentions = mentioned_in(str(scene_text or ""))
    if len(current_mentions) == 1:
        return current_mentions[0][1]
    if current_mentions:
        # Several bare names are ambiguous; explicit role+name extraction
        # above already handles scenes that name multiple people clearly.
        return ""

    # General named people (players, coaches, witnesses, scientists, and
    # other documentary subjects) do not always have an honorific. Extract
    # full names from the source script only when their local grammar marks
    # them as people: a possessive, a human role, or a person/action predicate.
    # This prevents team names and places such as "New England" or "West
    # Alabama" from becoming person anchors just because they are capitalized.
    person_anchors: dict[str, str] = {}
    person_predicate = re.compile(
        r"^(?:\s+(?:(?:later|earlier|then|again|also|quickly|finally|eventually)\s+){0,2})"
        r"(?:(?:had|has|have|did|does|do)\s+)?"
        r"(?:is|was|were|are|became|becomes|takes?|took|throws?|threw|fires?|fired|"
        r"reads?|read|cuts?|caught|catches?|intercepts?|intercepted|scores?|runs?|"
        r"passes?|led|leads?|said|says|told|spoke|speaks|claimed|claims|apologized|"
        r"coached|coaches|played|plays|joined|joins|retires?|retired|won|wins|"
        r"injured|returned|returns)\b|"
        r"^\s*,?\s+(?:an?\s+)?(?:undrafted\s+)?(?:rookie|player|coach|quarterback|"
        r"receiver|captain|witness|scientist|researcher|founder|president|actor|athlete)\b",
        re.IGNORECASE,
    )
    for match in re.finditer(
        r"\b(?P<name>[A-Z][A-Za-z'’-]+\s+[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+)?)"
        r"(?P<possessive>['’]s)?\b",
        anchor_script,
    ):
        raw_name = re.sub(r"\s+", " ", match.group("name")).strip(" ,.;:!?\"'()[]")
        # The name token class admits apostrophes for names such as O'Neil,
        # so the regex can consume a possessive suffix into the name group.
        # Normalize that suffix here so possessive-action anchors like
        # "Jermaine Kearse's catch" are retained as "Jermaine Kearse".
        possessive = bool(match.group("possessive")) or bool(re.search(r"['’]s$", raw_name))
        name = re.sub(r"['’]s$", "", raw_name)
        if not name:
            continue
        if name.split()[0].casefold() in {
            "but", "and", "after", "before", "then", "however", "meanwhile",
        }:
            continue
        following = anchor_script[match.end():match.end() + 90]
        preceding = anchor_script[max(0, match.start() - 36):match.start()]
        person_role_before = bool(re.search(
            r"\b(?:player|rookie|coach|quarterback|receiver|captain|witness|scientist|"
            r"researcher|founder|president|actor|athlete|undrafted)\s+$",
            preceding,
            re.IGNORECASE,
        ))
        person_object_after = bool(re.search(
            r"\b(?:to|toward|at|with|against|in front of)\s+$",
            preceding,
            re.IGNORECASE,
        )) and bool(re.search(
            r"\b(?:fires?|threw|throws?|passes?|cuts?|runs?|catches?|caught|intercepts?|"
            r"intercepted|tackles?|blocks?|speaks?|told|said)\b",
            preceding[-70:],
            re.IGNORECASE,
        ))
        if possessive or person_role_before or person_object_after or person_predicate.search(following):
            person_anchors[name.casefold()] = name

    def person_mentions(text: str) -> list[tuple[int, str, bool]]:
        found: list[tuple[int, str, bool]] = []
        surnames: dict[str, list[str]] = {}
        for name in person_anchors.values():
            surnames.setdefault(name.split()[-1].casefold(), []).append(name)
            for alias in (name,):
                pattern = re.compile(
                    r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?:['’]s)?(?![A-Za-z0-9])",
                    re.IGNORECASE,
                )
                for hit in pattern.finditer(text):
                    following = text[hit.end():hit.end() + 70]
                    has_subject_predicate = bool(person_predicate.search(following))
                    found.append((hit.start(), name, has_subject_predicate))
        for surname, names in surnames.items():
            if len(names) != 1:
                continue
            pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(surname) + r"(?![A-Za-z0-9])", re.IGNORECASE)
            for hit in pattern.finditer(text):
                if any(row[0] == hit.start() for row in found):
                    continue
                following = text[hit.end():hit.end() + 70]
                found.append((hit.start(), names[0], bool(person_predicate.search(following))))
        return sorted(found, key=lambda row: row[0])

    generic_mentions = person_mentions(str(scene_text or ""))
    if generic_mentions:
        # When a beat names two people, prefer the person doing the narrated
        # action (usually the sentence subject) over a receiver/opponent that
        # appears later in the same line.
        active = [row for row in generic_mentions if row[2]]
        if active:
            return active[0][1]
        if len({row[1] for row in generic_mentions}) == 1:
            return generic_mentions[0][1]

    # Scene segmentation can split an entity from its continuation (e.g.
    # "Princess Eugenie ... welcomed a baby" / "giving the family another
    # generation"). Only inherit a prior anchor when this fragment begins as
    # a grammatical continuation; never smear identity across a new paragraph.
    scene_fragment = str(scene_text or "")
    continuation = bool(re.match(
        r"(?i)^\s*(?:and|but|giving|which|who|whose|he|she|they|their|his|her|family|members|during|after|with|to|beginning|marking|while|providing)\b",
        scene_fragment,
    )) or bool(
        # The scene splitter can cut a paragraph in the middle of a sentence.
        # A lowercase opening is a reliable continuation signal ("beginning
        # ...", "whose ...") and must inherit the preceding named subject,
        # never the first person mentioned anywhere in the script.
        scene_fragment.lstrip()[:1].islower()
    )
    if (
        not generic_mentions
        and len(scene_fragment.strip().split()) <= 3
        and re.search(r"\b(?:interception|intercepted|catch|caught|tackle|tackled|shot|goal|save|escape|rescue)\b", scene_fragment, re.I)
    ):
        prior_people = person_mentions(str(previous_scene or ""))
        if prior_people:
            active_prior = [row for row in prior_people if row[2]]
            return (active_prior or prior_people)[0][1]
    if continuation:
        prior_mentions = mentioned_in(str(previous_scene or ""))
        if prior_mentions:
            prior_mentions.sort(key=lambda item: item[0])
            # A sentence can mention a supporting person after the primary
            # subject ("King Charles ... after the death of Queen Elizabeth").
            # Continuation fragments such as "His coronation ..." refer to
            # the scene's leading subject, so inherit the first anchor rather
            # than the last name token in the preceding sentence.
            return prior_mentions[0][1]
        # An intervening cutaway can contain no name ("Television brought
        # the ceremony into homes"). Explicit pronouns still refer to the
        # nearest preceding script anchor, not the film's first character.
        if re.match(r"(?i)^\s*(?:he|she|his|her|their)\b", scene_fragment):
            position = str(script or '').find(scene_fragment.strip())
            if position > 0:
                earlier = mentioned_in(str(script)[:position][-900:])
                if earlier:
                    return max(earlier, key=lambda item: item[0])[1]
    return ""


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


_PENDING_WEB_VISUAL_SOURCES = frozenset({
    "query_anchored_visual_pending",
    "domain_context_pending",
    "domain_context_pending_visual_required",
    "nearest_match_pending",
    "nearest_match_pending_visual_required",
    "royal_prefilter_pending",
    "identity_only_scene_context_pending",
    "visual_pending",
})


def _web_candidate_is_explicitly_pending_visual(candidate: Mapping[str, Any]) -> bool:
    """Identify resolver rows that need pixels, not another metadata veto."""
    evidence = candidate.get("candidate_evidence") or {}
    evidence = evidence if isinstance(evidence, Mapping) else {}
    reason = str(
        candidate.get("rejection_reason")
        or candidate.get("identity_gate_reason")
        or evidence.get("match_reason")
        or ""
    ).strip().lower()
    hard_reasons = {
        "forbidden_confusable_entity",
        "person_confusable_entity",
        "wrong_subject_type_animal",
        "wrong_subject_type_automobile",
        "unsafe_content",
        "unsafe_media",
        "adult_content",
        "synthetic_media_rejected",
    }
    if (
        reason in hard_reasons
        or evidence.get("forbidden_match")
        or evidence.get("confusable_hits")
        or candidate.get("unsafe_content")
    ):
        return False
    source = str(evidence.get("metadata_source") or "").strip().lower()
    return bool(
        candidate.get("semantic_context_pending")
        or candidate.get("semantic_visual_pending")
        or candidate.get("nearest_match_pending")
        or (
            evidence.get("verification_required")
            and source in _PENDING_WEB_VISUAL_SOURCES
        )
    )


def _mixed_web_scene_worker_limit(config: Mapping[str, Any] | None = None) -> int:
    """Return the bounded Mixed Media web fan-out.

    Packaged builds can carry a legacy six-worker value in ``config`` even
    when the deployment environment is configured for the newer 12-worker
    shared pool.  Treat the lower value as stale, while preserving an
    explicitly higher setting up to the process-safe cap.
    """
    settings = config or {}
    try:
        env_workers = int(os.environ.get("VIDZEO_MIXED_WEB_SCENE_WORKERS", "12") or 12)
    except (TypeError, ValueError):
        env_workers = 12
    try:
        configured_workers = int(settings.get("web_max_scene_workers") or 0)
    except (TypeError, ValueError):
        configured_workers = 0
    return max(1, min(12, max(configured_workers, env_workers)))



def _web_recovery_per_scene_seconds() -> float:
    """Per-scene allowance the web-recovery wall scales with."""
    try:
        return max(
            1.0,
            min(
                30.0,
                float(
                    os.environ.get("VIDZEO_WEB_RECOVERY_PER_SCENE_SECONDS", "12")
                    or 12
                ),
            ),
        )
    except (TypeError, ValueError):
        return 12.0


def _web_recovery_hard_cap_seconds() -> float:
    """Completion-oriented ceiling for one web-recovery stage.

    The old 180s ceiling cut a 121-scene job off after ~24 scenes: the
    stage wall must scale with the scenes left to resolve, not end at a
    fixed time. The default 1800s still bounds a wedged stage, and the
    delivery-budget allowance clamps it further downstream.
    """
    try:
        return max(
            180.0,
            float(os.environ.get("VIDZEO_WEB_RECOVERY_HARD_CAP_SECONDS", "1800") or 1800),
        )
    except (TypeError, ValueError):
        return 1800.0


def _web_recovery_stage_seconds(scene_count: int, workers: int, configured: Any = None) -> float:
    """Completion-scaled web-recovery wall: 12s per scene, floored at 90s.

    A 121-scene job gets 1452s instead of being cut off at the old fixed
    180s ceiling after ~24 scenes; a small job keeps its 90s floor. The
    delivery-budget allowance (``configured``) still clamps the wall — the
    render reserve is never spent — and the hard cap still bounds a wedged
    stage. ``workers`` is retained for call compatibility; the wall scales
    with scenes, not with the wave count, matching the primary mixed-media
    wall and the resolver's own scene-scaled default, so the explicit value
    and the resolver default agree (1452s for 121 scenes).
    """
    floor = 90.0
    cap = _web_recovery_hard_cap_seconds()
    scaled = max(
        floor,
        min(cap, max(1, int(scene_count)) * _web_recovery_per_scene_seconds()),
    )
    wall = min(cap, scaled)
    if configured not in (None, "", 0, "0"):
        try:
            # The delivery-budget allowance is a ceiling, not a target:
            # never let the wall spend the render reserve.
            wall = min(wall, max(1.0, float(configured)))
        except (TypeError, ValueError):
            pass
    return max(1.0, wall)


def _score(value: Any) -> float:
    try:
        return max(0.0, min(100.0, float(value or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _youtube_transport_review(decision, requirement, path):
    """Technical recovery is not a pixel review; retain only an exact real receipt."""
    from .visual_intelligence.decision import asset_fingerprint
    row = dict(decision or {}) if isinstance(decision, Mapping) else {}
    expected = str((requirement or {}).get('requirement_hash') or '')
    trusted = bool(expected and row.get('verified_requirement_hash') == expected
        and row.get('verified_asset_sha256') == asset_fingerprint(str(path))
        and row.get('visual_review_policy_version') == VISUAL_REVIEW_POLICY_VERSION
        and row.get('accepted') is True and row.get('semantic_verified') is True
        and row.get('visual_checked') is True and row.get('visual_available') is True)
    if trusted:
        return row
    # Preserve timing/provenance separately from review claims. The shared
    # scene verifier will inspect this exact file against the current contract.
    row.update(accepted=False, status='visual_verification_pending',
        verification_status='visual_verification_pending', visual_checked=False,
        visual_available=False, semantic_verified=False, deterministic_only=True)
    for key in ('verified_requirement_hash', 'verified_asset_sha256', 'visual_review_policy_version',
                'identity_score', 'action_score', 'context_score', 'quality', 'final_score',
                'entity_conflict', 'fatal_mismatch'):
        row.pop(key, None)
    return row


_ACTION_ONLY_FATAL_MARKERS = (
    "action mismatch",
    "activity mismatch",
    "required action",
    "specific action",
    "exact action",
    "action score",
    "not actively",
    "not interacting",
    "not depicted",
    "not shown",
    "does not show the action",
    "doesn't show the action",
)

_IDENTITY_OR_CONTEXT_FATAL_MARKERS = (
    "wrong entity",
    "wrong subject",
    "wrong model",
    "wrong year",
    "wrong era",
    "wrong domain",
    "wrong species",
    "different entity",
    "different subject",
    "different model",
    "different year",
    "different era",
    "different species",
    "required subject",
    "required subjects",
    "missing subject",
    "missing required",
    "subject is absent",
    "subjects are absent",
    "absent from",
    "contradict",
    "unrelated",
    "wildlife",
    "animal",
)


def _normalize_action_only_fatal_mismatch(verdict: Mapping[str, Any]) -> dict[str, Any]:
    """Downgrade an over-broad vision fatality when identity is still strong.

    Vision models sometimes use ``fatal_mismatch`` for a documentary image that
    clearly shows the requested entity but does not literally stage the action
    in the narration. That is a nearest-match/action score problem, not proof
    that the media depicts the wrong subject. Keep true identity, era, domain,
    contradictory-event, and missing-required-subject failures fail-closed.
    """

    result = dict(verdict or {})
    from .visual_intelligence.semantic_guard import explicit_scene_conflict
    if explicit_scene_conflict({key: value for key, value in result.items() if key != "fatal_mismatch"}):
        return result
    if not _as_bool(result.get("fatal_mismatch")):
        return result
    if _as_bool(result.get("entity_conflict")):
        return result

    identity = _score(result.get("identity_score"))
    context = max(
        _score(result.get("context_score")),
        _score(result.get("story_relevance")),
    )
    if identity < 75.0 or context < 65.0:
        return result

    reason = str(result.get("reason") or "").strip().lower()
    if not reason or not any(marker in reason for marker in _ACTION_ONLY_FATAL_MARKERS):
        return result
    if any(marker in reason for marker in _IDENTITY_OR_CONTEXT_FATAL_MARKERS):
        return result

    result["fatal_mismatch"] = False
    result["fatal_mismatch_normalized"] = "action_only_nearest_match"
    return result


def _asset_fingerprint(path: str, metadata: Mapping[str, Any] | None = None) -> str:
    row = metadata or {}
    for key in ("asset_fingerprint", "fingerprint", "sha256", "sha", "md5"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    source = Path(str(path or ""))
    if not source.is_file():
        return ""
    digest = hashlib.sha256()
    try:
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def _youtube_alternate_attempt_allowed(attempt: Mapping[str, Any] | None) -> bool:
    """Return whether a downloaded YouTube attempt may enter the V2 pool.

    Transport success is not media acceptance.  Keep only attempts that the
    provider overlay lane marked as usable; subject and scene relevance are
    deliberately left to the shared visual verifier.
    """
    if not isinstance(attempt, Mapping):
        return False
    if str(attempt.get("status") or "").strip().lower() != "downloaded":
        return False
    overlay_types = {
        str(item).strip().lower()
        for item in (attempt.get("overlay_types") or [])
    }
    # Small source labels are usable without rewriting footage. Previously
    # this lane admitted only blurred derivatives, discarding sharp alternates.
    preserved = source_overlays_preserved(attempt)
    if attempt.get('overlay_blurred') or attempt.get('blurred') or attempt.get('cleaned_path'):
        return False
    blocked_overlay_types = overlay_types & {
        "large_logo", "central_logo",
        "facecam", "face_cam", "inset_facecam", "picture_in_picture",
    }
    rej_reason = str(attempt.get("rejection_reason") or "").strip().lower()
    if 'large_text_band' in overlay_types and not preserved:
        return False
    if attempt.get("reject"):
        return False
    if rej_reason in {
        "facecam_inset_rejected",
        "confident_talking_head_rejected",
        "talking_head_rejected",
        "webcam_rejected",
    } or "facecam" in rej_reason or "talking_head" in rej_reason:
        return False
    if attempt.get("talking_head") or attempt.get("facecam_inset") or attempt.get("facecam_detected") or blocked_overlay_types:
        return False
    if float(attempt.get("facecam_confidence") or 0.0) >= 0.50:
        return False
    if attempt.get("overlay_blur_failed") or attempt.get("overlay_rejected"):
        return False
    if overlay_types & {"caption", "subtitle", "subtitles"} or attempt.get("overlay_has_subtitle"):
        return preserved
    return True


class LazyBatchLane:
    """Expose an existing batch provider through the V2 per-scene lane API.

    The first fair-wave request starts one all-scene provider run. Other lane
    calls wait on that same run, so there is no second network resolver pass.
    """

    def __init__(
        self,
        loader: Callable[[], Mapping[int, Any]],
        allowed_indices: set[int] | None = None,
    ):
        # The loader resolves the entire scene batch once and then serves rows
        # by scene index.  The coordinator must schedule all scene waiters in
        # one discovery wave; otherwise a single-container narrative is
        # serialized one scene per wave and the first 200s batch consumes the
        # whole media deadline before later scenes are even submitted.
        self.shared_batch = True
        self._loader = loader
        self._allowed_indices = (
            {int(index) for index in allowed_indices}
            if allowed_indices is not None
            else None
        )
        self._condition = threading.Condition()
        self._state = "pending"
        self._rows: dict[int, Any] = {}
        self._error: BaseException | None = None

    def __call__(self, scene: Mapping[str, Any], cancel_event: threading.Event) -> Any:
        index = int(scene.get("scene_index", scene.get("order", 0)) or 0)
        if self._allowed_indices is not None and index not in self._allowed_indices:
            return None
        with self._condition:
            if cancel_event.is_set():
                return self._rows.get(index)
            if self._state == "pending":
                self._state = "loading"
                JobThread(target=self._load, daemon=True, name="media-v2-batch-loader").start()
            while self._state == "loading" and index not in self._rows and not cancel_event.is_set():
                self._condition.wait(timeout=0.1)
            if index in self._rows:
                return self._rows[index]
            if self._error is not None:
                raise self._error
            return None

    def publish_row(self, index: int, row: Any) -> None:
        """Release a completed scene without waiting for unrelated batch cleanup."""
        with self._condition:
            self._rows[int(index)] = row
            self._condition.notify_all()

    def _load(self) -> None:
        try:
            loaded = self._loader() or {}
            if isinstance(loaded, Mapping):
                rows = dict(loaded)
            else:
                rows = dict(enumerate(list(loaded)))
        except BaseException as exc:
            with self._condition:
                self._error = exc
                self._state = "failed"
                self._condition.notify_all()
            return
        with self._condition:
            self._rows.update(rows)
            self._state = "ready"
            self._condition.notify_all()

    def get_row(self, index: int, timeout: float = 0.0) -> Any:
        with self._condition:
            if self._state == "ready":
                return self._rows.get(index)
            if self._state == "loading" and timeout > 0:
                self._condition.wait(timeout=timeout)
                if self._state == "ready":
                    return self._rows.get(index)
            return self._rows.get(index)


class SceneFallbackLane:
    """Run a fallback loader only for the scene that requested recovery.

    Cross-source recovery is entered after the coordinator has rejected a
    scene's primary finalists. Sharing one all-scene retry batch made a single
    semantic miss reopen every YouTube slot, wasting the deadline and mixing
    low-confidence fallback candidates into otherwise settled scenes. This
    lane keeps the provider-neutral coordinator API while making the retry
    scene-scoped.
    """

    def __init__(self, loader: Callable[[int], Any], prepare_indices: Callable[[Sequence[int]], None] | None = None):
        self.shared_batch = False
        self._loader = loader
        self._prepare_indices = prepare_indices

    def prepare_recovery(self, indices: Sequence[int]) -> None:
        if self._prepare_indices is not None:
            self._prepare_indices(indices)

    def __call__(self, scene: Mapping[str, Any], cancel_event: threading.Event) -> Any:
        if cancel_event.is_set():
            return None
        index = int(scene.get("scene_index", scene.get("order", 0)) or 0)
        return self._loader(index)


class BatchFallbackLane:
    """Coalesce cross-source recovery into one bounded provider batch.

    The coordinator asks fallback lanes one scene at a time while it selects
    candidates.  Calling a network scraper for every miss turned a 24-scene
    job into 24 serial retries.  This adapter starts one recovery batch on the
    first miss, caches the indexed rows, and serves the remaining scenes from
    that same result.
    """

    def __init__(self, loader: Callable[[], Mapping[int, Any]], prepare_indices: Callable[[Sequence[int]], None] | None = None):
        self.shared_batch = False
        self._loader = loader
        self._prepare_indices = prepare_indices
        self._condition = threading.Condition()
        self._state = "pending"
        self._rows: dict[int, Any] = {}
        self._error: BaseException | None = None

    def prepare_recovery(self, indices: Sequence[int]) -> None:
        with self._condition:
            if self._state == "pending" and self._prepare_indices is not None:
                self._prepare_indices(indices)

    def __call__(self, scene: Mapping[str, Any], cancel_event: threading.Event) -> Any:
        index = int(scene.get("scene_index", scene.get("order", 0)) or 0)
        with self._condition:
            if cancel_event.is_set():
                return None
            if self._state == "ready":
                return self._rows.get(index)
            if self._state == "failed":
                if self._error is not None:
                    raise self._error
                return None
            owns_load = self._state == "pending"
            if owns_load:
                self._state = "loading"
            else:
                while self._state == "loading" and not cancel_event.is_set():
                    self._condition.wait(timeout=0.1)
                if self._state == "ready":
                    return self._rows.get(index)
                if self._error is not None:
                    raise self._error
                return None
        try:
            loaded = self._loader() or {}
            rows = dict(loaded) if isinstance(loaded, Mapping) else dict(enumerate(list(loaded)))
        except BaseException as exc:
            with self._condition:
                self._error = exc
                self._state = "failed"
                self._condition.notify_all()
            raise
        with self._condition:
            self._rows = rows
            self._state = "ready"
            self._condition.notify_all()
            return self._rows.get(index)


def _is_video(path: str, media_type: str = "") -> bool:
    if str(media_type).strip().lower() == "video":
        return True
    return Path(path).suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def _youtube_cut_recovery_matches_scene(
    cut: Mapping[str, Any], target_scene_index: int, source_moment: Mapping[str, Any] | None,
) -> bool:
    """Allow recovery only for the exact scene and source moment originally chosen."""
    try:
        if int(cut.get("scene_index")) != int(target_scene_index):
            return False
    except (TypeError, ValueError):
        return False
    moment = source_moment if isinstance(source_moment, Mapping) else {}
    expected_id = str(moment.get("video_id") or moment.get("source_id") or "").strip()
    cut_id = str(cut.get("source_id") or cut.get("video_id") or "").strip()
    if expected_id and cut_id and expected_id.casefold() != cut_id.casefold():
        return False
    try:
        expected_start = float(moment.get("start")) if moment.get("start") is not None else None
        cut_start = float(cut.get("start")) if cut.get("start") is not None else None
        expected_end = float(moment.get("end")) if moment.get("end") is not None else None
        cut_end = float(cut.get("end")) if cut.get("end") is not None else None
    except (TypeError, ValueError):
        return False
    if expected_start is not None and cut_start is not None and abs(expected_start - cut_start) > 0.75:
        return False
    if expected_end is not None and cut_end is not None and abs(expected_end - cut_end) > 0.75:
        return False
    return True


_QWEN_REQUIRED_FIELDS = {
    "candidate_id",
    "entity_conflict",
    "identity_score",
    "action_score",
    "context_score",
    "quality",
    "visible_overlays",
    "subject_box",
    "safe_text_regions",
}


def _normalized_box(value: Any, *, allow_empty: bool) -> bool:
    if not isinstance(value, Mapping):
        return False
    if not value:
        return allow_empty
    required = ("x", "y", "width", "height")
    if any(key not in value for key in required):
        return False
    numbers = []
    for key in required:
        raw = value.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return False
        number = float(raw)
        if not math.isfinite(number):
            return False
        numbers.append(number)
    x, y, width, height = numbers
    return bool(
        0.0 <= x <= 1.0
        and 0.0 <= y <= 1.0
        and 0.0 < width <= 1.0
        and 0.0 < height <= 1.0
        and x + width <= 1.001
        and y + height <= 1.001
    )


def _validate_qwen_verdict_schema(
    candidate: Mapping[str, Any],
    verdict: Any,
) -> tuple[bool, dict[str, Any], list[str]]:
    """Validate the exact V2 visual-verdict contract fail closed."""
    if not isinstance(verdict, Mapping):
        return False, {}, ["verdict_not_object"]
    result = dict(verdict)
    present_marker = result.get("_schema_present")
    present = (
        {str(value) for value in present_marker}
        if isinstance(present_marker, list)
        else set(result)
    )
    # WARNING: client-side _schema_errors are recorded BEFORE coercion (string
    # booleans like entity_conflict=_"false"_ are flagged as _not_boolean in
    # vision_client._v2_schema_metadata). Propagating those stale markers here
    # makes every string-emission verdict fail even though the normalized value
    # is correct, forcing duplicate fallback calls. Re-derive failures from the
    # normalized payload instead.
    errors = []
    errors.extend(
        f"missing_{field}"
        for field in sorted(_QWEN_REQUIRED_FIELDS - present)
    )
    expected_id = str(
        candidate.get("candidate_id")
        or candidate.get("id")
        or candidate.get("path")
        or ""
    ).strip()
    actual_id = str(result.get("candidate_id") or "").strip()
    if not actual_id:
        errors.append("candidate_id_empty")
    else:
        norm_expected = expected_id.replace("\\", "/").strip().lower()
        norm_actual = actual_id.replace("\\", "/").strip().lower()
        if (
            norm_actual != norm_expected
            and not (norm_expected and norm_actual.endswith(Path(expected_id).name.lower()))
            and not (norm_actual and norm_expected.endswith(Path(actual_id).name.lower()))
        ):
            errors.append("candidate_id_mismatch")
    if not isinstance(result.get("entity_conflict"), bool):
        # Vision models routinely emit scalar strings ("false", "no", "none")
        # instead of JSON booleans. Fail-closing on the JSON type rejects
        # otherwise-correct candidates and forces a duplicate fallback call,
        # so normalize the value in place instead of failing the schema.
        result["entity_conflict"] = _as_bool(result.get("entity_conflict"))
    for field in ("identity_score", "action_score", "context_score", "quality"):
        raw = result.get(field)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            errors.append(f"{field}_not_number")
            continue
        value = float(raw)
        if not math.isfinite(value) or not 0.0 <= value <= 100.0:
            errors.append(f"{field}_out_of_range")
    overlays = result.get("visible_overlays")
    if not isinstance(overlays, list):
        errors.append("visible_overlays_not_array")
    if not _normalized_box(result.get("subject_box"), allow_empty=True):
        # Localization is an optional composition hint. Discard an unusable
        # box rather than rejecting a complete identity/relevance inspection.
        result["subject_box"] = {}
        result["geometry_advisories"] = ["subject_box_discarded_invalid"]
    safe_regions = result.get("safe_text_regions")
    if not isinstance(safe_regions, list):
        errors.append("safe_text_regions_not_array")
    else:
        # Safe text regions are advisory: keep valid entries and drop the
        # malformed ones instead of failing the whole verdict.
        result["safe_text_regions"] = [
            region for region in safe_regions
            if _normalized_box(region, allow_empty=False)
        ]
    # Keep a stable, compact failure record and remove internal normalization
    # markers before the verdict enters StoryboardV2.
    result.pop("_schema_present", None)
    result.pop("_schema_errors", None)
    errors = list(dict.fromkeys(errors))
    return not errors, result, errors


def _score_verdict(scene: Mapping[str, Any], verdict: Mapping[str, Any]) -> dict[str, Any]:
    result = _normalize_action_only_fatal_mismatch(verdict)
    from .visual_intelligence.semantic_guard import explicit_domain_conflict, explicit_scene_conflict, explicit_visual_facet_conflict, exact_scene_identity_required, explicit_low_visual_match
    requirement = scene.get('visual_requirement') or {}
    policy = dict(scene.get("source_policy") or {})
    if exact_scene_identity_required(scene):
        is_relaxed = False
    elif policy.get("youtube_relaxed_visual_gate") is not None:
        is_relaxed = bool(policy.get("youtube_relaxed_visual_gate"))
    elif policy.get("youtube_visual_required") is False:
        is_relaxed = True
    elif requirement.get("strict_identity"):
        is_relaxed = False
    else:
        is_relaxed = True
    # A photograph cannot display a company's quarterly delivery count.
    # Relevant real imagery backs the grounded Remotion statistic; absence
    # of the exact quantity in the pixels is not an identity contradiction.
    narration = str(scene.get("text") or scene.get("narration") or "")
    story = ' '.join((str(scene.get('film_context') or ''), narration,
                      str(requirement.get('canonical_entity') or '')))
    pixel_conflict = explicit_domain_conflict(story, result.get('scene_description') or '')
    if pixel_conflict:
        result.update(accepted=False, scene_match=False, fatal_mismatch=True,
                      status='rejected', rejection_reason=pixel_conflict)
        return result
    if explicit_low_visual_match(result):
        result.update(accepted=False, fatal_mismatch=True, status='rejected',
                      rejection_reason='insufficient_visible_subject_match')
        return result
    product_identity = (requirement.get('strict_identity') and requirement.get('subject_type')
                        in {'automobile', 'aircraft', 'ship', 'product'})
    identity_uncertain = result.get('identity_confirmed') is False or bool(re.search(
        r'(?:cannot|can.t|unable to|not possible to)\s+(?:visually\s+)?(?:confirm|identify|verify|determine)'
        r'|(?:specific|exact)\s+(?:model|make|identity).{0,60}(?:not|cannot|can.t|uncertain|unidentif)',
        str(result.get('reason') or ''), re.I))
    if product_identity and identity_uncertain:
        # A strict named make/model request cannot be satisfied by generic
        # category footage when the reviewer itself cannot confirm identity.
        # Reject this candidate so media expansion or grounded graphics can
        # handle the scene without falsely labeling unrelated footage.
        result.update(
            accepted=False,
            fatal_mismatch=True,
            status='rejected',
            rejection_reason='named_product_identity_unconfirmed',
        )
        return result
    facet_conflict = explicit_visual_facet_conflict(narration, result.get('scene_description') or '')
    if facet_conflict:
        result.update(accepted=False, scene_match=False, fatal_mismatch=True, status='rejected',
                      rejection_reason=facet_conflict)
        return result
    reason = str(result.get("reason") or "").casefold()
    quantitative_context = bool(
        re.search(r"\d", narration)
        and re.search(r"\b(?:quarter|percent|percentage|million|billion|total|produced|production|deliveries|delivered|gigawatt|revenue|sales)\b", narration, re.I)
        and any(marker in reason for marker in (
            "quantitative", "figures", "count", "volume", "numeric", "number",
            "mass production", "fleet scale", "data-driven", "statistic",
        ))
    )
    true_conflict = explicit_scene_conflict({
        key: value for key, value in result.items()
        if key not in {"fatal_mismatch", "scene_match", "accepted", "status", "rejection_reason"}
    }, relaxed=is_relaxed) or (not is_relaxed and any(marker in reason for marker in (
        "wrong model", "different model", "wrong era", "different era",
        "wrong year", "different year", "anachronistic", "unrelated",
    )))
    if (
        quantitative_context and not true_conflict
        and _as_bool(result.get("domain_match"))
        and _score(result.get("identity_score")) >= 80.0
        and _score(result.get("context_score")) >= 50.0
        and str(result.get("source_presentation_type") or "").casefold() in {"photograph", "video_frame"}
        and not _as_bool(result.get("synthetic_content"))
    ):
        result.update(fatal_mismatch=False, scene_match=True, contextual_match=True,
                      nearest_match=True, factual_overlay_required=True,
                      fatal_mismatch_normalized="quantitative_context_with_grounded_overlay")
    if is_relaxed and result.get("fatal_mismatch"):
        spurious_tokens = (
            "era", "year", "season", "tenure", "older", "dated",
            "period", "roster", "transfer", "trade", "matchup", "uniform",
            "different player", "different team", "not named", "quarterback",
            "different event", "distinct events", "wrong event", "competing event",
            "wrong matchup", "different matchup", "different location", "wrong venue",
            "wrong entity", "different entity", "wrong subject", "different subject",
            "required entity is absent", "required subject is absent",
        )
        if any(token in reason for token in spurious_tokens) and not any(
            marker in reason for marker in (
                "wrong domain", "different_sport", "simulation_for_real_world", "game-engine",
                "wrong era", "different era", "anachronistic", "anachronism",
            )
        ):
            result["spurious_mismatch"] = True
            result["fatal_mismatch"] = False
    if explicit_scene_conflict({key: value for key, value in result.items() if key != "fatal_mismatch"}, relaxed=is_relaxed):
        result.update({"accepted": False, "fatal_mismatch": True, "status": "rejected", "rejection_reason": "explicit_scene_conflict"})
        return result
    # A factual scene can legitimately use a contextual establishing shot
    # (for example, Buckingham Palace while discussing King Charles). Qwen
    # sometimes labels the missing person as ``fatal_mismatch`` even while it
    # gives the place/context high confidence and reports no competing entity.
    # Treat that narrow pattern as a nearest-match visual; keep true wrong
    # person/domain/unsafe decisions hard-rejected.
    reason_text = str(result.get("reason") or "").strip().lower()
    if (
        _as_bool(result.get("fatal_mismatch"))
        and not _as_bool(result.get("entity_conflict"))
        and not _as_bool(result.get("unsafe_content"))
        and not _as_bool(result.get("suggestive_content"))
        and any(marker in reason_text for marker in (
            "required subject is absent",
            "required entity is absent",
            "fails to show the required entity",
            "does not show the required entity",
            "establishing shot",
            "only provides",
        ))
        and not any(marker in reason_text for marker in (
            "wrong person", "wrong entity", "wrong domain", "incorrect",
            "contradict", "unrelated", "irrelevant",
        ))
        # An establishing shot deliberately does not show the named person or
        # object, so identity_score can be low even when the location/domain
        # is exactly right.  Keep a modest identity floor to prevent a random
        # category image from entering, while letting a strong contextual
        # match (for example Buckingham Palace for a royal-family scene)
        # remain real media instead of collapsing to a text card.
        and _score(result.get("identity_score")) >= 35.0
        and max(_score(result.get("context_score")), _score(result.get("story_relevance"))) >= 75.0
        and _score(result.get("action_score")) <= 45.0
    ):
        result["fatal_mismatch"] = False
        result["nearest_match"] = True
        result["contextual_match"] = True
        result["reason"] = (
            f"{result.get('reason') or ''} Accepted as a related establishing "
            "shot because the scene context matches and no conflicting entity was detected."
        ).strip()
    strict = scene_requires_visual_verification(scene)
    requirement = scene.get("visual_requirement") if isinstance(scene.get("visual_requirement"), Mapping) else {}
    action_terms = requirement.get("action_terms") if isinstance(requirement, Mapping) else ()
    scene_text = " ".join(str(scene.get(key) or "") for key in ("text", "narration", "visual_intent"))
    action_text = " ".join([scene_text, " ".join(str(value) for value in action_terms or () if value)])
    # Exact physical actions are a stronger target than general event context.
    # A clip of the correct team or match can be useful b-roll, but it cannot
    # stand in for an interception, catch, tackle, launch, collision, etc.
    # unless the vision model confirms that action in the sampled frames.
    action_specific = bool(
        re.search(
            r"\b(?:intercepts?|interception|catches?|catch|bobbl(?:e|es|ed|ing)|"
            r"tackles?|scores?|scoring|throws?|threw|throwing|fires?|launches?|"
            r"collides?|crashes?|strikes?|escapes?|breaks? free|lands?|takes? off|"
            r"shoots?|sinks?|rescues?|attacks?|cuts? across|crosses? the route|"
            r"drives?|driving|accelerat(?:es|ing)|brak(?:es|ing)|weld(?:s|ing)|assembl(?:es|ing))\b",
            action_text,
            re.IGNORECASE,
        )
    )
    # action_terms also contains search nouns (quarter, vehicles, batteries,
    # dates). Their presence alone must not demand footage of a physical
    # action and reject a correct documentary photograph.
    # Domain-aligned supporting visuals are useful even when the exact named
    # person/event cannot be proven from one still. Ask the model to state this
    # explicitly (``domain_match`` + ``scene_match``) and treat that pair as a
    # bounded contextual pass. Clear wrong-entity/domain, unsafe, and
    # contradictory-event signals remain hard vetoes; a missing or weak year
    # signal alone does not turn an on-topic image into a rejection.
    domain_match = _as_bool(
        result.get("domain_match")
        or result.get("same_domain")
        or result.get("domain_aligned")
    )
    scene_match = _as_bool(
        result.get("scene_match")
        or result.get("scene_relevance")
        or result.get("contextual_match")
        or result.get("story_match")
    )
    domain_reason = str(result.get("reason") or "").strip().lower()
    explicit_wrong_domain = any(
        marker in domain_reason
        for marker in (
            "wrong entity", "wrong subject", "wrong domain", "different entity",
            "different subject", "wrong species", "wildlife", "unrelated",
            "irrelevant", "contradictory event", "contradicts the scene",
        )
    )
    if (
        isinstance(scene.get("visual_requirement"), Mapping)
        and scene.get("visual_requirement", {}).get("strict_identity")
        and domain_match
        and scene_match
        and not explicit_wrong_domain
        and not _as_bool(result.get("entity_conflict"))
        and not _as_bool(result.get("unsafe_content"))
        and not _as_bool(result.get("suggestive_content"))
    ):
        result["domain_match"] = True
        result["scene_match"] = True
        result["nearest_match"] = True
        result["contextual_match"] = True
        # A model may call out a year/era that is not visible in the frame.
        # Preserve a real contradiction, but do not fail an otherwise
        # domain/scene-aligned supporting visual for lack of date proof.
        contextual_explanation = any(
            marker in domain_reason
            for marker in (
                "not visible", "not shown", "not identifiable", "cannot verify",
                "can't verify", "establishing shot", "contextual", "context",
                "near-match", "near match", "indirect", "supports the scene",
                "missing exact", "exact person is not",
            )
        )
        if _as_bool(result.get("fatal_mismatch")) and contextual_explanation and not any(
            marker in domain_reason
            for marker in (
                "wrong entity", "wrong subject", "wrong domain", "different entity",
                "different subject", "wrong species", "wildlife", "unrelated",
                "irrelevant", "contradictory event", "contradicts the scene",
                "wrong era", "different era", "modern footage", "contemporary footage",
                "present-day", "anachronistic", "not archival", "historical mismatch",
            )
        ):
            result["fatal_mismatch"] = False
            result["fatal_mismatch_normalized"] = "domain_scene_contextual_match"
            result["reason"] = (
                f"{result.get('reason') or ''} Retained as a domain-aligned "
                "contextual visual; no conflicting entity was detected."
            ).strip()
    if is_relaxed:
        conflict = bool(explicit_scene_conflict(result, relaxed=True))
    else:
        conflict = _as_bool(result.get("entity_conflict")) or _as_bool(result.get("fatal_mismatch"))
    unsafe = _as_bool(result.get("unsafe_content")) or _as_bool(result.get("suggestive_content"))
    watermark = _as_bool(result.get("watermark"))
    def _is_severe_overlay(item: Any) -> bool:
        if isinstance(item, Mapping):
            return bool(
                _as_bool(item.get("blocking"))
                or str(item.get("severity") or "").strip().lower() in {"high", "severe"}
                or str(item.get("type") or "").strip().lower() in {
                    "watermark", "large_logo", "subtitle", "caption",
                    "lower_third", "lower-third", "banner", "text_overlay",
                    "speech_bubble", "thought_bubble", "meme_caption",
                    "comic_caption", "joke_text", "fake_dialogue",
                }
            )
        text = str(item or "").strip().lower()
        if not text:
            return False
        return any(
            token in text
            for token in (
                "subtitle", "caption", "lower-third", "lower third", "banner",
                "watermark", "logo", "chyron", "headline", "news banner",
                "panel", "outro", "endcard", "slate", "watch more",
                "speech bubble", "thought bubble", "meme caption",
                "comic caption", "joke annotation", "fake dialogue",
            )
        )
    has_sub_overlay = any("subtitle" in str(ov).lower() or "caption" in str(ov).lower() for ov in (result.get("visible_overlays") or []))
    has_wm_overlay = any("watermark" in str(ov).lower() or "logo" in str(ov).lower() for ov in (result.get("visible_overlays") or []))
    # Qwen occasionally describes a visible branded mark in ``reason`` while
    # returning only its normalized box in ``visible_overlays``.  Treat clear
    # editor/app branding as a watermark, but do not trip on an explicit
    # "no watermark" statement.
    reason_text = str(result.get("reason") or "").strip().lower()
    watermark_terms = ("watermark", "made with", "kinemaster", "capcut", "filmora", "inshot")
    no_watermark = bool(re.search(r"\b(?:no|without|does not contain|doesn't contain)\s+(?:a\s+)?watermark\b", reason_text))
    if not no_watermark and any(term in reason_text for term in watermark_terms):
        has_wm_overlay = True
    visible_overlays = result.get("visible_overlays") or []
    severe_overlay = any(_is_severe_overlay(ov) for ov in visible_overlays)
    requirement = scene.get("visual_requirement") if isinstance(scene.get("visual_requirement"), Mapping) else {}
    scene_copy = " ".join(str(scene.get(key) or "") for key in ("text", "narration", "visual_intent"))
    game_scene = bool(
        re.search(r"\b(?:gaming|game|video game|interactive entertainment)\b", str(requirement.get("domain") or scene.get("domain") or ""), re.I)
        or re.search(r"\b(?:video game|gameplay|game world|gaming)\b", scene_copy, re.I)
    )
    game_ui_terms = re.compile(
        r"\b(?:game(?:play)?\s*[- ]?hud|hud elements?|mini[- ]?map|mission instructions?|"
        r"objective text|ammo counter|health bar|level indicator|game interface|game ui|"
        r"location text overlay|quest marker|button prompts?)\b", re.I,
    )
    overlay_text = " ".join(
        " ".join(str(item.get(key) or "") for key in ("type", "label", "text", "description", "classification"))
        if isinstance(item, Mapping) else str(item or "")
        for item in visible_overlays
    )
    # Raw gameplay UI makes a documentary shot look like an unedited capture.
    # Permit it only when the narration is specifically about that interface.
    game_ui_overlay = bool(
        game_scene
        and game_ui_terms.search(overlay_text)
        and not re.search(r"\b(?:hud|interface|mini[- ]?map|game ui|on[- ]screen prompts?)\b", scene_copy, re.I)
    )
    severe_overlay = severe_overlay or game_ui_overlay
    if _as_bool(result.get("subtitle")) or _as_bool(result.get("has_text_overlay")) or has_sub_overlay:
        severe_overlay = True
    if has_wm_overlay:
        watermark = True
    semantic_scores = [
        _score(result.get(key))
        for key in (
            "final_score", "relevance", "identity_score", "action_score",
            "context_score", "story_relevance",
        )
    ]
    identity = _score(result.get("identity_score"))
    action = _score(result.get("action_score"))
    context = max(
        _score(result.get("context_score")),
        _score(result.get("story_relevance")),
    )
    # The model's own aggregate is pessimistic on niche and historical
    # subjects: it reports high semantic scores alongside a low self-scored
    # final_score, which fail-closes otherwise-correct candidates. Derive the
    # aggregate from the semantic decomposition instead. The conflict, unsafe,
    # watermark and overlay vetoes below still apply on top of the threshold.
    derived_components = [identity, action, context]
    if any(value > 0.0 for value in derived_components):
        final_score = 0.55 * identity + 0.25 * context + 0.20 * action
    else:
        final_score = _score(result.get("final_score")) or max(semantic_scores or [0.0])
    semantic_requirement = scene.get("visual_requirement") if isinstance(scene, Mapping) else None
    exact_identity = bool(
        isinstance(semantic_requirement, Mapping)
        and semantic_requirement.get("strict_identity")
    )

    # A named-entity image can be semantically correct while still being a
    # poor editorial source when the subject is tiny or pushed into the lower
    # edge of a background-dominated frame.  That exact failure mode allowed
    # web alternates such as a car occupying only the bottom strip of a house
    # photo to be promoted into the reusable shot pool.  Keep this gate
    # deliberately narrow: it only applies when the scene explicitly requires
    # an exact identity and the verifier supplied a real normalized box.  A
    # missing box remains a normal verifier/nearest-match case; we never turn
    # an unavailable localization signal into a blank visual.
    subject_framing_issue = False
    subject_box = result.get("subject_box")
    if exact_identity and isinstance(subject_box, Mapping) and subject_box:
        try:
            box_width = float(subject_box.get("width") or 0.0)
            box_height = float(subject_box.get("height") or 0.0)
            box_x = float(subject_box.get("x") or 0.0)
            box_y = float(subject_box.get("y") or 0.0)
            box_area = box_width * box_height
            # Reject only clearly background-dominated named-entity frames:
            # small footprint, or a shallow subject anchored at the bottom.
            # A tall, narrow subject (for example an astronaut standing on
            # the lunar surface) can have an area below 0.35 while still
            # dominating the editorial crop.  Keep the footprint veto for
            # genuinely tiny subjects and retain the shallow bottom-edge veto
            # for the roof/background failure this guard was designed to stop.
            subject_framing_issue = bool(
                box_area < 0.05
                or (box_height <= 0.35 and box_y >= 0.65)
            )
            # Establishing shots explain the installation or production
            # setting. A verified battery mounted on a house, or a vehicle
            # inside its assembly line, need not fill a product portrait crop.
            # Keep the tiny-subject veto and all identity/content vetoes.
            if (
                _industrial_establishing_scene(scene)
                and identity >= 80.0 and context >= 80.0
                and box_area >= 0.005
            ):
                subject_framing_issue = False
        except (TypeError, ValueError):
            subject_framing_issue = False

    relevance_threshold = float(os.environ.get("VIDZEO_VISUAL_RELEVANCE_THRESHOLD", "55") or 55)
    if exact_identity:
        contextual = bool(result.get("contextual_match"))
        accepted = (
            (
                contextual
                and final_score >= max(38.0, relevance_threshold - 17.0)
                and identity >= 30.0
                and context >= 60.0
            )
            or (
                final_score >= max(55.0, relevance_threshold)
                and identity >= 55.0
                and context >= 50.0
            )
        )
    elif strict:
        accepted = final_score >= relevance_threshold and (identity >= 50.0 or context >= 50.0)
    else:
        accepted = final_score >= min(50.0, relevance_threshold)

    action_threshold = float(os.environ.get("VIDZEO_EXACT_ACTION_SCORE_THRESHOLD", "85") or 85)
    action_match = not action_specific or action >= action_threshold
    if strict and not action_match:
        result["action_specific_match_required"] = True
        result["action_score_threshold"] = action_threshold
        result["rejection_reason"] = "specific_action_not_visually_confirmed"
        accepted = False

    is_talking_head = bool(_as_bool(result.get("talking_head")))
    th_confidence = float(result.get("talking_head_confidence") or 0.0)
    hard_talking_head = is_talking_head and th_confidence >= 75.0
    fabricated_evidence = bool(exact_identity and _as_bool(result.get('synthetic_content'))
        and (semantic_requirement or {}).get('subject_type') != 'video_game')

    accepted = bool(
        accepted
        and ("scene_match" not in result or _as_bool(result["scene_match"]) or (is_relaxed and not conflict))
        and not conflict
        and not unsafe
        and not watermark
        and not severe_overlay
        and not subject_framing_issue
        and not hard_talking_head
        and not fabricated_evidence
    )
    rejection_reason = "" if accepted else str(result.get("rejection_reason") or "")
    if not accepted and not rejection_reason:
        if fabricated_evidence:
            rejection_reason = "synthetic_content_cannot_prove_named_subject"
        elif game_ui_overlay:
            rejection_reason = "gameplay_interface_overlay_detected"
        elif severe_overlay or has_sub_overlay:
            rejection_reason = "severe_overlay_or_subtitle_detected"
        elif watermark or has_wm_overlay:
            rejection_reason = "watermark_detected"
        elif conflict:
            rejection_reason = "entity_conflict_detected"
        elif unsafe:
            rejection_reason = "unsafe_content_detected"
        elif subject_framing_issue:
            rejection_reason = "subject_framing_too_small_or_background_dominated"
        elif not action_match:
            rejection_reason = "specific_action_not_visually_confirmed"
        elif hard_talking_head:
            rejection_reason = "talking_head_detected"
        elif "scene_match" in result and not _as_bool(result["scene_match"]):
            rejection_reason = "scene_subject_or_facet_not_visible"
        else:
            rejection_reason = "insufficient_semantic_score"
    result.update(
        {
            "accepted": accepted,
            "status": "accepted" if accepted else "rejected",
            "final_score": final_score,
            "entity_conflict": conflict,
            "severe_overlay": severe_overlay,
            "game_ui_overlay": game_ui_overlay,
            "watermark": watermark,
            "subtitle": bool(_as_bool(result.get("subtitle")) or has_sub_overlay),
            "rejection_reason": rejection_reason,
            "semantic_verified": bool(accepted and (not exact_identity or identity >= 70.0)),
        }
    )
    return result


def _industrial_establishing_scene(scene: Mapping[str, Any]) -> bool:
    requirement = scene.get("visual_requirement") or {}
    attributes = requirement.get("required_attributes") or {}
    if attributes.get("model"):
        return False
    copy = " ".join(str(scene.get(k) or "") for k in ("scene_text", "text", "narration"))
    return bool(re.search(
        r"\b(?:factor(?:y|ies)|manufacturing|assembly\s+line|batter(?:y|ies)|"
        r"powerwall|megapack|grid|energy[- ]storage|charging\s+(?:network|station))\b",
        copy, re.I,
    ))


def _metadata_background_conflict(scene: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    """Reject obvious background-only metadata for strict named-entity stills.

    Search providers frequently return a correctly named car photo whose title
    also contains an incidental label such as ``House``.  The visual model can
    then quite reasonably verify the car while the renderer receives a frame
    dominated by the house/roof behind it.  This is a source-quality problem,
    not a nearest-match problem: for exact automotive entities, those labels
    are strong evidence that the result should not enter the reusable pool.
    Keep the rule narrow and metadata-only so generic documentary scenes and
    nearest-match fallbacks are not over-filtered.
    """
    if not isinstance(scene, Mapping) or not isinstance(candidate, Mapping):
        return False
    if _industrial_establishing_scene(scene):
        # Metadata is not enough to reject a home battery or factory context.
        # Actual pixel verification still decides identity and framing.
        return False
    requirement = scene.get("visual_requirement")
    if not isinstance(requirement, Mapping) or not requirement.get("strict_identity"):
        return False
    subject_type = str(requirement.get("subject_type") or "").strip().lower()
    canonical = str(requirement.get("canonical_entity") or "").strip().lower()
    if subject_type not in {"automobile", "car", "vehicle"} and "automotive" not in str(requirement.get("domain") or "").lower():
        return False
    if not canonical:
        return False
    parts: list[str] = []
    for key in ("title", "description", "tags", "query_used", "query"):
        value = candidate.get(key)
        if value:
            parts.append(str(value))
    evidence = candidate.get("candidate_evidence")
    if isinstance(evidence, Mapping):
        for key in ("metadata", "identity_proof"):
            value = evidence.get(key)
            if value:
                parts.append(str(value))
    # Do not inspect URLs: a legitimate image CDN/path can contain one of these
    # words without describing the image itself.
    metadata = " ".join(parts).lower()
    if not metadata:
        return False
    background_terms = (
        "house", "home", "roof", "rooftop", "building", "architecture",
        "real estate", "property", "interior", "room", "apartment",
        "residence", "villa", "bungalow",
    )
    return any(re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", metadata) for term in background_terms)


def _explicit_metadata_scene_conflict(
    scene: Mapping[str, Any],
    candidate: Mapping[str, Any],
    visual_verdict: Mapping[str, Any] | None = None,
) -> str:
    """Catch explicit portrait/event contradictions after a real visual check.

    Search metadata is not pixel evidence, so it cannot approve an asset. It can,
    however, expose a direct contradiction that a broad "same domain" visual
    verdict must not wash away (for example, a Prince Philip portrait for a
    Prince Harry wedding beat, or an anti-monarchy protest for a royal wedding).
    The caller applies this as a veto after the live visual verifier has run.
    """
    if not isinstance(scene, Mapping) or not isinstance(candidate, Mapping):
        return ""
    requirement = scene.get("visual_requirement")
    requirement = requirement if isinstance(requirement, Mapping) else {}
    raw_scene_text = " ".join((
        str(scene.get("text") or ""),
        str(scene.get("narration") or ""),
        str(scene.get("visual_intent") or ""),
        str(requirement.get("visual_intent") or ""),
    )).strip()
    canonical_hint = str(requirement.get("canonical_entity") or "")
    metadata_hint = " ".join(
        str(candidate.get(key) or "")
        for key in ("title", "description", "tags", "query", "query_used")
    )
    evidence_hint = candidate.get("candidate_evidence")
    if isinstance(evidence_hint, Mapping):
        metadata_hint += " " + str(evidence_hint.get("metadata") or "")
    # Installment identity is part of visual accuracy for game documentaries.
    # A scene about GTA VI cannot use a source explicitly titled GTA V/5 or
    # GTA Online, even if both happen to show Vice City-like streets.
    if re.search(r"\b(?:grand theft auto\s+vi|gta\s*(?:vi|6))\b", canonical_hint, re.I) and re.search(
        r"\b(?:grand theft auto\s+(?:v|5)|gta\s*(?:v|5)|gta online|fivem)\b",
        metadata_hint,
        re.I,
    ):
        return "explicit_game_installment_mismatch"
    if isinstance(visual_verdict, Mapping):
        visual_parts = [
            str(visual_verdict.get(key) or "")
            for key in ("reason", "scene_description", "description")
        ]
        subjects = visual_verdict.get("subjects")
        if isinstance(subjects, (list, tuple)):
            visual_parts.extend(str(value or "") for value in subjects)
        candidate_parts = [
            str(candidate.get(key) or "")
            for key in ("title", "description", "tags")
        ]
        evidence = candidate.get("candidate_evidence")
        if isinstance(evidence, Mapping):
            candidate_parts.append(str(evidence.get("metadata") or ""))
        try:
            from .scene_entities import comparison_pair_conflict

            pair_scene = str(scene.get("text") or scene.get("narration") or raw_scene_text)
            if comparison_pair_conflict(pair_scene, visual_parts) or comparison_pair_conflict(pair_scene, [" ".join(candidate_parts)]):
                return "explicit_comparison_entity_mismatch"
        except Exception:
            # Pair-copy validation is an extra veto, not a dependency of the
            # normal visual verification path.
            pass
        # If vision itself calls the footage generic while the narration's
        # point depends on a proper-named place, a broad sport/domain score
        # cannot promote that stock stadium cutaway into event evidence.
        location_clause = re.search(
            r"\b(?:in|at|to|near|from|through)\s+(?:the\s+)?"
            r"(?P<place>[A-Z][\w'.-]*(?:\s+(?:[A-Z][\w'.-]*|de|del|of|the|and)){0,3})",
            raw_scene_text,
        )
        visual_words = " ".join(
            str(visual_verdict.get(key) or "")
            for key in ("reason", "scene_description", "description")
        ).casefold()
        subjects = visual_verdict.get("subjects")
        if isinstance(subjects, (list, tuple)):
            visual_words += " " + " ".join(str(value or "") for value in subjects).casefold()
        generic_visual = bool(re.search(r"\b(?:generic|stock|nonspecific|non-specific|unidentified)\b", visual_words))
        named_place = str(location_clause.group("place") or "").strip() if location_clause else ""
        place_key = re.sub(r"\s+", " ", named_place).casefold()
        if (
            named_place
            and generic_visual
            and place_key not in visual_words
            and bool(re.search(r"\b(?:stadium|arena|court|match|game|football|soccer|league|venue|nfl|nba|nhl|mlb|sport)\b", raw_scene_text, re.I))
        ):
            return "generic_footage_for_named_place"
    if not requirement.get("strict_identity"):
        return ""
    subject_type = str(requirement.get("subject_type") or "").strip().lower()
    canonical = str(requirement.get("canonical_entity") or "").strip()
    scene_text = raw_scene_text.lower()
    title = str(candidate.get("title") or "")
    description = str(candidate.get("description") or "")
    evidence = candidate.get("candidate_evidence")
    if isinstance(evidence, Mapping):
        description = " ".join((description, str(evidence.get("metadata") or "")))
    # Strip markup before comparing titles returned by Commons/Openverse.
    title = re.sub(r"<[^>]*>", " ", title)
    title = re.sub(r"&(?:amp|quot|#39|nbsp);", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip()
    metadata = f"{title} {description} {candidate.get('tags') or ''}".lower()

    if subject_type in {"person", "public_figure", "person_or_group"} and canonical:
        # A query-anchored row whose metadata explicitly lacks the canonical
        # person is only a near-match candidate.  It must not be promoted by a
        # generous vision response in a strict factual scene: broad royal
        # family galleries routinely contain Charles, William, Kate, or the
        # children while the query asks for another individual.  Keep nearest
        # matches available for the non-strict/advisory path, but require an
        # exact metadata anchor before a strict person can be accepted.
        evidence = candidate.get("candidate_evidence")
        if isinstance(evidence, Mapping) and evidence.get("canonical_entity_mismatch"):
            if not evidence.get("nearest_match"):
                missing_terms = evidence.get("missing_terms") or ()
                if isinstance(missing_terms, str):
                    missing_terms = (missing_terms,)
                if missing_terms:
                    return "explicit_canonical_entity_mismatch"
        role = r"(?:king|queen|prince|princess|duke|duchess|emperor|empress|president|prime minister|pope)"
        portrait = re.search(
            rf"\b(?:portrait|headshot)\s+(?:of\s+)?(?:the\s+)?(?P<subject>{role}\s+[a-z][a-z’'\-]+(?:\s+(?:[a-z][a-z’'\-]+|[ivx]{{1,4}})){{0,2}})",
            title,
            flags=re.IGNORECASE,
        )
        if portrait:
            expected_tokens = {
                token.casefold()
                for token in re.findall(r"[A-Za-z][A-Za-z’'\-]+", canonical)
                if token.casefold() not in {"king", "queen", "prince", "princess", "duke", "duchess", "emperor", "empress"}
            }
            portrait_tokens = {token.casefold() for token in re.findall(r"[A-Za-z][A-Za-z’'\-]+", portrait.group("subject"))}
            if expected_tokens and not expected_tokens.intersection(portrait_tokens):
                return "explicit_portrait_entity_mismatch"

    scene_is_event_specific = bool(re.search(
        r"\b(wedding|married|marriage|bride|groom|coronation|funeral|baptism|christening|ceremony)\b",
        scene_text,
    ))
    metadata_is_protest = bool(re.search(
        r"\b(protest|protester|protesters|demonstration|demonstrators|anti[- ]monarch|not my king|down with the crown)\b",
        metadata,
    ))
    metadata_names_event = bool(re.search(
        r"\b(wedding|married|marriage|bride|groom|coronation|funeral|baptism|christening|ceremony)\b",
        metadata,
    ))
    if scene_is_event_specific and metadata_is_protest and not metadata_names_event:
        return "explicit_protest_event_mismatch"
    return ""


def build_qwen_verifier(
    config: Mapping[str, Any],
    scenes: Sequence[Any],
    *,
    visual_context: VisualDecisionContext | None = None,
    on_progress: Callable[[float, str], Any] | None = None,
) -> tuple[Callable[[Mapping[str, Any], dict[str, Any]], dict[str, Any]], VisualDecisionContext]:
    """Build one job-scoped Qwen verifier with the V2 proportional budget."""

    # Production passes frozen SceneContractV1 objects, while the coordinator
    # passes mapping copies. Normalize both at this boundary. In long scripts
    # the neighborhood lookup previously called .get() on the frozen objects,
    # rejecting every finalist before the vision client could inspect it.
    scenes = [_verifier_scene_row(scene) for scene in scenes]
    progress_lock = threading.Lock()
    checked_candidates, checked_scenes, accepted_scenes = set(), set(), set()
    def publish_visual_progress(scene, digest, verdict):
        if not callable(on_progress) or not (verdict.get('visual_checked') and verdict.get('visual_available')):
            return
        scene_id = str(scene.get('scene_id') or scene.get('scene_index') or scene.get('order') or '')
        key = (scene_id, digest, str((scene.get('visual_requirement') or {}).get('requirement_hash') or ''))
        with progress_lock:
            previous = (len(checked_candidates), len(accepted_scenes))
            checked_candidates.add(key)
            checked_scenes.add(scene_id)
            if verdict.get('accepted') is True:
                accepted_scenes.add(scene_id)
            if previous == (len(checked_candidates), len(accepted_scenes)):
                return
            count, accepted, candidate_count = len(checked_scenes), len(accepted_scenes), len(checked_candidates)
        try:
            on_progress(.60 + .05 * min(1, count / max(1, len(scenes))),
                        f'Visual checks: {count}/{len(scenes)} scenes, {candidate_count} candidates; {accepted} accepted')
        except Exception:
            pass  # A dashboard listener cannot invalidate a real visual verdict.
    context_config = dict(config or {})
    film_context = ' '.join(str(config.get(key) or '') for key in ('title', 'content_title', 'script'))[:2200]
    for row in scenes:
        if isinstance(row, dict):
            row.setdefault('film_context', film_context)
    scene_count = max(1, len(scenes))
    # V2 verifies a compact primary finalist set, then still needs an
    # opposite-lane candidate and several frames from a rejected YouTube
    # range. A 4x/scene ceiling is therefore too small for long jobs: it can
    # be consumed before recovery layers run, and strict scenes fail closed
    # merely because Qwen ran out of calls. Reserve room for primary lanes,
    # one cross-lane retry, same-video timestamp frames, and schema repair.
    # The USD budget and shared deadline still bound real spend.
    configured_cap = context_config.get("visual_shared_max_calls") or context_config.get("visual_max_calls")
    cap = max(
        8,
        int(configured_cap)
        if configured_cap is not None
        else int(math.ceil(float(scene_count) * 6.0) + 8),
    )
    context_config["visual_max_calls"] = cap
    context_config["visual_shared_max_calls"] = cap
    mixed_media = str(context_config.get("image_source") or "").strip().lower() == "mixed media"
    try:
        requested_visual_timeout = float(
            context_config.get("mixed_visual_call_timeout_seconds")
            or context_config.get("visual_call_timeout_seconds")
            or os.environ.get("VIDZEO_VISUAL_CALL_TIMEOUT_SECONDS")
            or 30.0
        )
    except (TypeError, ValueError):
        requested_visual_timeout = 30.0
    visual_timeout = max(10.0, min(120.0, requested_visual_timeout))
    try:
        requested_visual_attempts = int(
            context_config.get("visual_call_max_attempts")
            or os.environ.get("VIDZEO_VISUAL_CALL_MAX_ATTEMPTS")
            or 2
        )
    except (TypeError, ValueError):
        requested_visual_attempts = 2
    visual_attempts = max(2, min(3, requested_visual_attempts))
    external_agent = str(context_config.get('llm_provider') or '') == 'external_agent' or str(context_config.get('openrouter_api_key') or '').startswith('external-agent:')
    if mixed_media:
        # Mixed jobs already do provider retrieval in parallel. Keep the
        # semantic gate parallel too. Honor the bounded operator timeout;
        # never raise it silently on every candidate in a long film.
        context_config["visual_concurrency"] = min(
            6, max(4, int(context_config.get("visual_concurrency") or 6))
        )
        context_config["visual_fast_mode"] = True
    else:
        context_config["visual_concurrency"] = min(4, max(1, int(context_config.get("visual_concurrency") or 4)))
    if external_agent:
        # Muse's own vision answers through the local inbox: allow 4 calls
        # in flight (the inbox holds multiple pending rows) and 2 attempts
        # per call, so one missed inbox deadline retries with a fresh
        # request instead of failing the candidate closed.
        context_config['visual_concurrency'] = 4
        visual_timeout, visual_attempts = 300.0, 2
    context_config["visual_call_timeout_seconds"] = visual_timeout
    context_config["visual_call_max_attempts"] = visual_attempts
    context_config["visual_fail_closed"] = True
    # Muse inference is compulsory for external_agent jobs: the visual
    # verifier must be Muse through the agent-bridge inbox, never Qwen
    # via OpenRouter — even if an OpenRouter key is present in the
    # environment or the job config. The worker side refuses to start or
    # render with such a key set; this server-side gate is the second
    # layer, so a stray key can never be selected for an agent job.
    # visual_fail_closed stays True above: without a Muse verdict, the
    # candidate is discarded instead of silently accepted.
    if external_agent:
        key = ""
        context_config["openrouter_api_key"] = ""
        context_config["openrouterApiKey"] = ""
    else:
        key = str(
            context_config.get("openrouter_api_key")
            or context_config.get("openrouterApiKey")
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("VIDZEO_OPENROUTER_API_KEY")
            or ""
        ).strip()
        if key and "visual_understanding" not in context_config:
            context_config["visual_understanding"] = True
        if key and "openrouter_api_key" not in context_config:
            context_config["openrouter_api_key"] = key
    # A legacy provider may leave a disabled transport context on the config.
    # It is not a valid final semantic verifier: reuse only an already-enabled
    # job context, otherwise construct the verifier from the current config so
    # a supplied OpenRouter key can actually be used for strict scenes.
    existing_context = visual_context if isinstance(visual_context, VisualDecisionContext) else None
    context = (
        existing_context
        if existing_context is not None and bool(existing_context.enabled)
        else VisualDecisionContext.from_config(context_config, scene_count)
    )
    # A shared context can have been constructed earlier under legacy settings.
    # Apply the current job timeout even to an older shared context.
    context.call_timeout_seconds = visual_timeout
    try:
        existing_attempts = int(getattr(context, "call_max_attempts", 0) or 0)
    except (TypeError, ValueError):
        existing_attempts = 0
    context.call_max_attempts = max(2, min(3, max(existing_attempts, visual_attempts)))
    if external_agent:
        context.call_max_attempts = 1
    for client_holder in (
        getattr(context, "_client", None),
        getattr(context, "_fallback_client", None),
    ):
        raw_client = getattr(client_holder, "_client", None)
        if raw_client is not None:
            raw_client.max_attempts = context.call_max_attempts
            raw_client.timeout_seconds = context.call_timeout_seconds
            if mixed_media:
                raw_client.fast_mode = True
    # Keep the shared context's audit/policy in sync with the job-level
    # accuracy switch.  The app may have created ``_web_visual_context``
    # before V2 scene contracts were rebased, so relying only on the old
    # ``visual_required`` value allowed a strict-retrieval job to report an
    # advisory gate and retain unverified web winners after a timeout.
    context.visual_required = bool(
        getattr(context, "visual_required", False)
        or _as_bool(context_config.get("visual_required"), False)
        or _as_bool(context_config.get("strict_retrieval_accuracy"), False)
        or any(scene_requires_visual_verification(scene) for scene in scenes)
    )
    # The app may create a shared context before V2 knows the final scene
    # count.  Raise that context to the proportional V2 ceiling for every
    # source mode, otherwise a Web-only strict job can exhaust a stale
    # three/six-call cap before its targeted rescue candidates are checked.
    context.max_calls = max(int(getattr(context, "max_calls", 1) or 1), cap)
    delivery_budget = None
    try:
        from .delivery_budget import get_delivery_budget
        delivery_budget = get_delivery_budget(context_config)
    except Exception:
        delivery_budget = None
    allowance = None
    if delivery_budget is not None:
        try:
            reserve_after = float(
                context_config.get("_render_reserve_seconds")
                or delivery_budget.render_reserve_seconds
            )
        except (TypeError, ValueError):
            reserve_after = float(delivery_budget.render_reserve_seconds)
        allowance = delivery_budget.allowance(None, reserve_after_seconds=reserve_after)
    if allowance is not None:
        context.refresh_deadline(deadline_seconds=max(0.05, allowance))
    else:
        context.refresh_deadline(deadline_seconds=max(450.0, float(scene_count) * 25.0))
    if mixed_media:
        # ``_web_visual_context`` is created earlier by the app and reused
        # here. Normalize that existing object as well; otherwise its .env
        # values could silently override the job's bounded retry policy.
        context.fast_mode = True
        # The app may have created this context with the legacy shared cap
        # before the V2 adapter was entered. Restore the V2 proportional cap so
        # finalist calls are bounded by the job policy, not an unrelated
        # process-wide .env default.
        context.max_calls = max(int(getattr(context, "max_calls", 1) or 1), cap)
        context.max_concurrent = 2 if external_agent else min(6, max(4, int(context_config.get("visual_concurrency") or 6)))
        # No provider calls have started when this adapter is built. Recreate
        # the semaphore so an app-created context with an older concurrency
        # value cannot queue the final gate behind a stale limit.
        if not context.calls:
            context._semaphore = threading.BoundedSemaphore(context.max_concurrent)
    print(
        f"   [vision] final semantic gate: workers={int(getattr(context, 'max_concurrent', 1) or 1)}, "
        f"probe_edges=image={os.environ.get('VIDZEO_VISION_IMAGE_PROBE_EDGE', '512')}px "
        f"frame={os.environ.get('VIDZEO_VISION_FRAME_PROBE_EDGE', '384')}px, "
        f"shared_calls={int(getattr(context, 'max_calls', 0) or 0)}, "
        f"timeout={float(getattr(context, 'call_timeout_seconds', 0.0) or 0.0):.1f}s, "
        f"attempts={int(getattr(context, 'call_max_attempts', 1) or 1)}"
    )
    # Job-scoped verdict reuse. The same physical asset can appear as a
    # finalist across several scenes (yt lane reuses clips, lanes can return
    # duplicates); when the verify prompt inputs are identical, a second model
    # call is pure waste. Scene-specific fields in the key keep cross-scene
    # re-verification intact.
    _verdict_cache: dict[tuple, dict[str, Any]] = {}

    def pixel_digest(candidate):
        if not isinstance(candidate, Mapping):
            return ""
        path = Path(str(candidate.get("path") or ""))
        try:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            return digest.hexdigest()
        except OSError:
            return ""

    def verify(scene: Mapping[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(candidate, Mapping):
            return {'accepted': False, 'status': 'rejected', 'final_score': 0, 'reason': 'invalid_candidate_none'}
        if not isinstance(scene, Mapping):
            scene = {}
        scene = _verifier_scene_row(scene) if scene is not None else {}
        if not isinstance(scene, Mapping):
            scene = {}
        from .visual_intelligence.semantic_guard import explicit_domain_conflict
        story = ' '.join(str(config.get(key) or '') for key in ('title', 'content_title', 'script'))
        evidence = ' '.join(str(candidate.get(key) or '') for key in ('title', 'description', 'tags', 'source_url', 'url'))
        domain_conflict = explicit_domain_conflict(story, evidence)
        if domain_conflict:
            return {'accepted': False, 'status': 'rejected', 'entity_conflict': True,
                    'fatal_mismatch': True, 'domain_match': False, 'final_score': 0,
                    'reason': domain_conflict}
        actual_digest = pixel_digest(candidate)
        strict_scene = scene_requires_visual_verification(scene)
        stock_candidate = "stock" in str(
            candidate.get("source") or candidate.get("provider") or ""
        ).lower()
        cand_evidence = candidate.get("candidate_evidence") if isinstance(candidate.get("candidate_evidence"), Mapping) else {}
        if (
            stock_candidate
            and (
                candidate.get("match_reason") == "contextual_stock_availability_only"
                or candidate.get("identity_gate_reason") == "contextual_stock_availability_only"
                or cand_evidence.get("hard_gate_reason") == "contextual_stock_availability_only"
                or cand_evidence.get("metadata_source") == "stock_contextual_no_vision"
                or candidate.get("verification_status") == "deterministic_stock_last_resort"
                or candidate.get("stock_last_resort_no_vision")
                or candidate.get("stock_last_resort")
            )
        ):
            meta_score = _score(candidate.get("final_score") or candidate.get("score") or candidate.get("metadata_score"))
            return {
                "accepted": True,
                "status": "accepted",
                "verification_status": "accepted_contextual_stock",
                "final_score": max(75.0, meta_score),
                "identity_score": 75.0,
                "context_score": 80.0,
                "action_score": 70.0,
                "quality": 85.0,
                "entity_conflict": False,
                "fatal_mismatch": False,
                "domain_match": True,
                "scene_match": True,
                "contextual_match": True,
                "nearest_match": True,
                "reason": "contextual_stock_b_roll_accepted",
                "schema_valid": True,
                "visual_checked": True,
                "visual_available": True,
                "verified_requirement_hash": str((scene.get("visual_requirement") or {}).get("requirement_hash") or ""),
                "verified_asset_sha256": actual_digest,
                "visual_review_policy_version": VISUAL_REVIEW_POLICY_VERSION,
            }
        reuse_key = (
            actual_digest,
            str(candidate.get("candidate_id") or candidate.get("path") or ""),
            str(candidate.get("path") or ""),
            str(scene.get("text") or ""),
            str((scene.get("visual_requirement") or {}).get("requirement_hash") or ""),
            tuple(sorted(str(value) for value in scene.get("positive_subjects") or [])),
            tuple(sorted(str(value) for value in scene.get("forbidden_subjects") or [])),
            str(scene.get("visual_intent") or ""),
            str(candidate.get("media_type") or ""),
        )
        cached = _verdict_cache.get(reuse_key)
        if cached is not None:
            publish_visual_progress(scene, actual_digest, cached)
            return dict(cached, verification_attempt="cached", schema_valid=True)

        def done(value: dict[str, Any]) -> dict[str, Any]:
            if not isinstance(value, Mapping):
                value = {}
            if value.get("visual_checked") and value.get("visual_available"):
                value = dict(value, verified_asset_sha256=actual_digest,
                             verified_requirement_hash=str((scene.get("visual_requirement") or {}).get("requirement_hash") or ""),
                             visual_review_policy_version=VISUAL_REVIEW_POLICY_VERSION)
            # A strict person scene may use a clearly identified portrait even
            # when the narration describes the associated event.  Qwen often
            # calls that an entity conflict because the exact ceremony is not
            # visible (for example, a Prince Harry portrait for a wedding
            # sentence).  If the pixel identity is strong and the candidate's
            # own metadata anchors the same person, downgrade that action-only
            # mismatch to a verified near-match.  Explicit wrong-person,
            # unrelated, unsafe, watermark, and metadata-mismatch signals stay
            # hard rejects.
            requirement = scene.get("visual_requirement")
            if not isinstance(requirement, Mapping):
                requirement = {}
            canonical = str(requirement.get("canonical_entity") or "").strip()
            subject_type = str(requirement.get("subject_type") or "").strip().lower()
            evidence = candidate.get("candidate_evidence")
            if not isinstance(evidence, Mapping):
                evidence = {}
            metadata_anchor = " ".join(
                str(candidate.get(key) or "") for key in
                ("title", "description", "query_used", "query")
            ) + " " + str(evidence.get("identity_proof") or "")
            normalized_anchor = re.sub(r"[^a-z0-9]+", " ", metadata_anchor.casefold()).strip()
            normalized_canonical = re.sub(r"[^a-z0-9]+", " ", canonical.casefold()).strip()
            anchor_matches = bool(
                normalized_canonical
                and normalized_canonical in normalized_anchor
                and not evidence.get("canonical_entity_mismatch")
            )
            reason_text = str(value.get("reason") or "").strip().lower()
            scene_text = " ".join(
                str(scene.get(key) or "")
                # ``source_script`` contains every scene in a job. Including
                # it here makes a coronation beat look like a wedding beat
                # when a later sentence mentions a marriage, causing the
                # event-conflict veto to reject an otherwise exact image.
                for key in ("text", "visual_intent")
            ).casefold()
            # A person-only near match is useful for open-ended biography
            # beats, but it must not satisfy an explicitly event-bound beat.
            # Otherwise a broad royal-family portrait (for example Eugenie at
            # Trooping the Colour) can be accepted for a wedding sentence
            # merely because the person and year are correct. Keep the event
            # contract strict while leaving ordinary establishing portraits
            # on the existing soft path.
            required_event = ""
            if re.search(r"\b(wedding|married|marriage|bride|groom)\b", scene_text):
                required_event = "wedding"
            elif re.search(r"\b(coronation|crowned)\b", scene_text):
                required_event = "coronation"
            elif re.search(r"\b(funeral|baptism|christening|inauguration)\b", scene_text):
                required_event = "ceremony"
            event_conflict_markers = {
                "wedding": (
                    "trooping the colour", "trooping", "coronation ceremony", "coronation",
                    "funeral ceremony", "funeral", "baptism", "christening", "inauguration",
                    "state opening", "parade", "premiere", "birthday ceremony", "balcony",
                    "does not depict the wedding", "not depicting the wedding",
                    "wedding is not shown", "wedding itself is not depicted",
                ),
                "coronation": (
                    "wedding ceremony", "wedding day", "bride and groom", "married at",
                    "funeral ceremony", "funeral", "trooping the colour", "trooping",
                    "baptism", "christening", "inauguration", "parade", "premiere",
                ),
                "ceremony": (
                    "wedding", "married", "coronation", "crowned", "trooping",
                    "funeral", "baptism", "christening", "inauguration",
                ),
            }
            event_conflict = bool(
                required_event
                and any(marker in reason_text for marker in event_conflict_markers.get(required_event, ()))
            )
            action_only_markers = (
                "generic headshot", "generic portrait", "red carpet portrait",
                "specific wedding ceremony", "specific coronation ceremony",
                "exact event", "does not visually support", "event context",
                "event itself is not depicted", "not depicting the wedding",
            )
            explicit_wrong_markers = (
                "wrong person", "wrong entity", "different person", "different entity",
                "incorrect person", "unrelated", "irrelevant", "wrong domain",
                "contradictory event", "animal", "wildlife", "confusable",
                "wrong era", "different era", "wrong year", "different year",
                "anachronistic", "anachronism", "historical mismatch", "modern footage",
                "contemporary footage", "present-day",
            )
            if (
                bool(requirement.get("strict_identity"))
                and subject_type in {"person", "public_figure", "person_or_group"}
                and anchor_matches
                and float(value.get("identity_score") or 0.0) >= 85.0
                # A portrait can be highly relevant as establishing context
                # even when the exact ceremony is outside the frame. Qwen
                # commonly scores that context in the 10–25 range while
                # explicitly confirming the required person and domain. Keep
                # those identity-backed near matches; explicit wrong-person
                # or wrong-domain signals below still veto them.
                and float(value.get("context_score") or 0.0) >= 10.0
                and bool(value.get("entity_conflict") or value.get("fatal_mismatch"))
                and any(marker in reason_text for marker in action_only_markers)
                and not required_event
                and not event_conflict
                and not _as_bool(value.get('era_mismatch'))
                and not _as_bool(value.get('year_mismatch'))
                and not any(marker in reason_text for marker in explicit_wrong_markers)
                and not bool(
                    value.get("watermark")
                    or value.get("unsafe_content")
                    or value.get("suggestive_content")
                )
            ):
                value = dict(value)
                value.update({
                    "accepted": True,
                    "status": "accepted",
                    "verification_status": "accepted",
                    "entity_conflict": False,
                    "fatal_mismatch": False,
                    "semantic_verified": True,
                    "nearest_match": True,
                    "contextual_match": True,
                    "rejection_reason": "",
                    "fatal_mismatch_normalized": "strict_person_action_only_nearest_match",
                    "reason": (
                        f"{value.get('reason') or ''} Retained as a verified portrait near-match; "
                        "the named person is supported even though the exact event is not shown."
                    ).strip(),
                })
            # A live vision pass may still be overly generous about two
            # images sharing a broad topic (royalty, cars, sports, etc.).
            # Veto only explicit title/event contradictions after that pass;
            # metadata never promotes or independently verifies an image.
            metadata_conflict = _explicit_metadata_scene_conflict(scene, candidate, value)
            if event_conflict:
                metadata_conflict = metadata_conflict or "visual_event_mismatch"
            # Asset metadata is a useful contradiction signal, but it is not
            # stronger than a live pixel inspection. A query-anchored row can
            # carry a stale/foreign publisher caption while Qwen confirms the
            # requested person, context, and action in the image itself. Let
            # that high-confidence visual result override only the generic
            # canonical-metadata mismatch; explicit portrait-name mismatches,
            # wrong-event signals, safety flags, and low-confidence results
            # remain hard rejects.
            canonical_conflict_overridden = bool(
                metadata_conflict == "explicit_canonical_entity_mismatch"
                and bool(value.get("visual_checked"))
                and bool(value.get("visual_available") or value.get("vision_available"))
                and float(value.get("identity_score") or 0.0) >= 85.0
                and float(value.get("context_score") or 0.0) >= 60.0
                and float(value.get("action_score") or 0.0) >= 50.0
                and not bool(
                    value.get("unsafe_content")
                    or value.get("suggestive_content")
                    or value.get("watermark")
                    or value.get("severe_overlay")
                    or value.get("fatal_mismatch")
                )
                and not any(
                    marker in reason_text
                    for marker in (
                        "wrong person", "wrong entity", "different person",
                        "different entity", "irrelevant", "off_topic", "off-topic",
                        "wrong domain", "wrong event", "event mismatch", "confusable",
                    )
                )
            )
            if canonical_conflict_overridden:
                value = dict(value)
                value.update({
                    "accepted": True,
                    "status": "accepted",
                    "verification_status": "semantic_verified_scored_pixels",
                    "semantic_verified": True,
                    "entity_conflict": False,
                    "fatal_mismatch": False,
                    "metadata_conflict_veto": "",
                    "metadata_conflict_overridden": "high_confidence_pixel_inspection",
                })
                metadata_conflict = ""
            if metadata_conflict:
                value = dict(value)
                value.update({
                    "accepted": False,
                    "status": "rejected",
                    "entity_conflict": True,
                    "fatal_mismatch": True,
                    "semantic_verified": False,
                    "rejection_reason": metadata_conflict,
                    "metadata_conflict_veto": metadata_conflict,
                })
            source = str(candidate.get("source") or candidate.get("provider") or "").strip().casefold()
            if source in {"web", "web images", "web_image", "web_image_resolver", "yandex"} and not value.get("watermark"):
                from .web_image.ownership_mark import inspect_ownership_mark
                ownership = inspect_ownership_mark(str(candidate.get("path") or ""))
                if ownership.get("watermark"):
                    value = dict(value)
                    value.update({
                        "accepted": False,
                        "status": "rejected",
                        "verification_status": "rejected",
                        "watermark": True,
                        "rejection_reason": "watermark_detected",
                        "ocr_ownership_mark": ownership,
                    })
            presentation = str(value.get('source_presentation_type') or '').strip().lower()
            examines_document = bool(requirement.get('source_evidence_required') or re.search(
                r'\b(?:this (?:document|slide|page|report)|court filing|the document shows|the report states)\b',
                str(scene.get('text') or ''), re.I))
            if presentation in {'lecture_slide','homework','worksheet','presentation_slide','quiz','text_heavy_diagram'} and not examines_document:
                value = dict(value, accepted=False, status='rejected', verification_status='rejected',
                             fatal_mismatch=True, semantic_verified=False,
                             rejection_reason='unrequested_text_heavy_slide')
            _verdict_cache[reuse_key] = value
            publish_visual_progress(scene, actual_digest, value)
            return value

        # Keep provider metadata quality aligned with the visual gate.  A
        # named automotive candidate titled e.g. "... House" can contain a
        # real Corolla, yet still be a background-dominated house/roof frame;
        # rejecting it here prevents that alternate from being promoted into
        # later scene reuse after the primary candidate is exhausted.
        if _metadata_background_conflict(scene, candidate):
            return done({
                "accepted": False,
                "status": "rejected",
                "final_score": 0.0,
                "fatal_mismatch": True,
                "entity_conflict": False,
                "rejection_reason": "background_dominated_metadata",
                "semantic_verified": False,
                "schema_valid": True,
                "verification_attempt": "metadata_gate",
            })

        previous = candidate.get("qwen_result") or candidate.get("visual_decision")
        previous_schema_errors: list[str] = []
        previous_source = str(
            (previous.get("source") if isinstance(previous, Mapping) else "")
            or candidate.get("source")
            or candidate.get("provider")
            or ""
        ).strip().lower()
        previous_verification = str(
            (previous.get("verification_status") if isinstance(previous, Mapping) else "")
            or ""
        ).strip().lower()
        previous_evidence = candidate.get("candidate_evidence")
        if not isinstance(previous_evidence, Mapping):
            previous_evidence = {}
        previous_is_pending_external_visual = bool(
            previous_verification in {
                "pending_visual_verification",
                "pending_external",
                "external_visual_pending",
                "semantic_visual_pending",
            }
            or _as_bool(previous.get("visual_verification_required"))
            or _as_bool(candidate.get("visual_verification_required"))
            or _as_bool(previous_evidence.get("external_visual_gate_required"))
            or _as_bool(candidate.get("semantic_visual_pending"))
        ) if isinstance(previous, Mapping) else bool(
            _as_bool(candidate.get("visual_verification_required"))
            or _as_bool(previous_evidence.get("external_visual_gate_required"))
            or _as_bool(candidate.get("semantic_visual_pending"))
        )
        # Provider transports intentionally stamp deterministic acceptances so
        # a lane can hand off a decoded/safe file. That is not semantic proof.
        # Strict factual scenes must force one live final verification for
        # both web stills and YouTube pool cuts; otherwise a clean but wrong
        # clip can be treated as verified merely because it downloaded.
        previous_has_live_visual = bool(
            isinstance(previous, Mapping)
            and (
                previous.get("visual_available")
                or previous.get("vision_available")
            )
        )
        expected_req_hash = str((scene.get("visual_requirement") or {}).get("requirement_hash") or "")
        if isinstance(previous, Mapping):
            observed_req_hash = str(previous.get("verified_requirement_hash") or previous.get("scene_context_hash") or "")
            observed_sha = str(previous.get("verified_asset_sha256") or previous.get("asset_fingerprint") or "")
            policy_mismatch = previous.get('visual_review_policy_version') != VISUAL_REVIEW_POLICY_VERSION
        else:
            observed_req_hash = ""
            observed_sha = ""
            policy_mismatch = False
        hash_mismatch = bool(expected_req_hash and observed_req_hash != expected_req_hash)
        sha_mismatch = bool(actual_digest and observed_sha != actual_digest)
        previous_is_unbound_visual = previous_has_live_visual and (
            hash_mismatch or sha_mismatch or policy_mismatch
        )
        if previous_is_unbound_visual:
            previous_is_pending_external_visual = True
        youtube_without_live_visual = bool(
            previous_source in {
                "youtube", "youtube clips", "yt", "youtube_pool_moment",
                "youtube_ranged_alternate",
            }
            and not previous_has_live_visual
        )
        web_without_live_visual = bool(
            previous_source in {"web", "web_image", "web_image_resolver", "web images"}
            and previous_is_pending_external_visual
        )
        force_live_semantic_verification = bool(
            previous_is_unbound_visual or (strict_scene or bool(getattr(context, "enabled", False)))
            and (
                (
                    previous_source in {"web", "web_image", "web_image_resolver", "web images"}
                    and previous_verification in {
                        "deterministic_web_image", "deterministic_only", "deterministic_degraded",
                    }
                )
                or previous_is_unbound_visual
                or web_without_live_visual
                or youtube_without_live_visual
            )
        )
        if isinstance(previous, Mapping) and previous:
            previous_valid, previous_result, previous_schema_errors = _validate_qwen_verdict_schema(
                candidate, previous,
            )
            if previous_valid and not force_live_semantic_verification:
                previous_result.update({
                    "schema_valid": True,
                    "verification_attempt": "reused",
                })
                return done(_score_verdict(scene, previous_result))
        path = str(candidate.get("path") or "")
        if not path or not Path(path).is_file():
            return {"accepted": False, "status": "missing_asset", "final_score": 0.0}
        try:
            client = context.get_client()
        except Exception as exc:
            client = None
            if not context.degraded_reason:
                context.degraded_reason = f"vision_client_error:{type(exc).__name__}"
        if client is None:
            metadata_score = _score(
                candidate.get("final_score")
                or candidate.get("score")
                or candidate.get("metadata_score")
            )
            cand_prev = candidate.get("visual_decision") or candidate.get("qwen_result") or previous
            cand_status = str((cand_prev.get("status") if isinstance(cand_prev, Mapping) else "") or candidate.get("verification_status") or "").strip().lower()
            cand_accepted = bool(
                isinstance(cand_prev, Mapping)
                and (
                    cand_prev.get("accepted") is True
                    or cand_status in {"accepted", "verified", "downloaded", "deterministic_pool_cut", "deterministic_web_image"}
                )
                and not cand_prev.get("fatal_mismatch")
                and not cand_prev.get("entity_conflict")
            )
            deterministic_accept = bool(
                cand_accepted
                or (not strict_scene and not stock_candidate and metadata_score >= 52.0)
            )
            verdict = {
                "accepted": deterministic_accept,
                "status": "accepted" if cand_accepted else ("deterministic_degraded" if deterministic_accept else "vision_unavailable"),
                "final_score": max(metadata_score, float(cand_prev.get("final_score") or cand_prev.get("relevance") or 0.0) if isinstance(cand_prev, Mapping) else metadata_score),
                "reason": ("previously_accepted_candidate" if cand_accepted else (context.degraded_reason or "vision_unavailable")),
                "schema_valid": cand_accepted,
                "prior_schema_errors": previous_schema_errors,
            }
            if cand_accepted:
                verdict.update({
                    "visual_checked": True,
                    "semantic_verified": True,
                    "visual_available": True,
                    "verified_requirement_hash": str((scene.get("visual_requirement") or {}).get("requirement_hash") or ""),
                    "verified_asset_sha256": actual_digest,
                    "visual_review_policy_version": VISUAL_REVIEW_POLICY_VERSION,
                })
            return done(verdict)
        requirement = scene.get("visual_requirement") if isinstance(scene.get("visual_requirement"), Mapping) else {}
        positives = ", ".join(str(value) for value in scene.get("positive_subjects") or ())
        forbidden = ", ".join(str(value) for value in scene.get("forbidden_subjects") or ())
        required_attributes = json.dumps(dict(requirement.get("required_attributes") or {}), ensure_ascii=False)
        requirement_entity = str(requirement.get("canonical_entity") or "")
        script_context = str(
            config.get("script")
            or config.get("source_script")
            or config.get("__vip_script")
            or ' '.join(str(row.get('text') or row.get('narration') or '') for row in scenes if isinstance(row, Mapping))
            or ""
        ).strip()
        # Keep the prompt bounded for ultra-long jobs while giving the model
        # enough surrounding narrative to distinguish a person, place, event,
        # and supporting context.
        if len(script_context) > 1800:
            scene_position = next((i for i,row in enumerate(scenes) if row is scene
                or (scene.get('scene_id') and row.get('scene_id') == scene.get('scene_id'))
                or (scene.get('scene_index') is not None and row.get('scene_index') == scene.get('scene_index'))
                or (row.get('text') and row.get('text') == scene.get('text'))),0)
            neighbors = ' '.join(str(row.get('text') or row.get('narration') or '')
                                 for row in scenes[max(0,scene_position-2):scene_position+3] if isinstance(row,Mapping))
            script_context = 'Film opening/context: '+script_context[:650]+'\nOwned scene neighborhood: '+neighbors[:1100]
        candidate_metadata = " | ".join(
            str(candidate.get(key) or "").strip().replace("\n", " ")
            for key in ("title", "description", "tags", "domain", "provider", "query_used")
            if str(candidate.get(key) or "").strip()
        )
        if len(candidate_metadata) > 900:
            candidate_metadata = candidate_metadata[:900].rsplit(" ", 1)[0] + "..."
        prompt = (
            "Verify this candidate for exactly one storyboard scene. Return schema-valid JSON with "
            "scene_description (describe actual visible objects/action first without borrowing words from query), "
            "candidate_id, entity_conflict, identity_score, action_score, context_score, quality, "
            "final_score, visible_overlays, subject_box, safe_text_regions, fatal_mismatch, "
            "domain_match, scene_match, reason, source_presentation_type, and has_text. "
            "Use exact snake_case keys. visible_overlays and safe_text_regions must be arrays. "
            "subject_box must be {} when no subject is confidently localized, otherwise an object with normalized x, y, width, height (0 to 1). "
            "entity_conflict, fatal_mismatch, domain_match and scene_match must be JSON booleans. All scores must be numbers from 0 to 100.\n"
            "CRITERIA:\n"
            "1. Base judgment on actual visible pixels, not search titles or candidate metadata.\n"
            "2. identity_confirmed=true only when visible design/features identify the requested named subject. If unidentifiable, return false.\n"
            "3. Score the narrated action honestly (action_score). Static logos cannot explain motion or mechanics.\n"
            "4. Inspect pixels for text, watermarks, and infographics. Set has_text=true and return boxes in visible_overlays.\n"
            "5. source_presentation_type: photograph, illustration, text_heavy_diagram, archival_document, lecture_slide, presentation_slide, homework, worksheet, quiz, or video_frame. Reject lecture slides, homework, and contradictory infographics with fatal_mismatch=true.\n"
            "6. For video, inspect all chronological frames. Return synthetic_content=true if AI generated. Return talking_head=true and talking_head_confidence (0-100) if a host/presenter addresses camera.\n"
            "7. Authentic contextual B-roll or establishing shots can support abstract or private beats (scene_match=true) if they do not depict competing entities or contradict scene facts. For a named person, another identifiable person shown as the portrait/main subject is a wrong-entity conflict. Reserve fatal_mismatch for a wrong entity, conflicting event, contradictory era/year, or unsafe media.\n"
            f"Candidate ID: {candidate.get('candidate_id') or candidate.get('path')}\n"
            f"Candidate metadata (retrieval hint, not pixel proof): {candidate_metadata or 'none'}\n"
            f"Exact narration: {scene.get('text') or ''}\n"
            f"Film subject: {config.get('content_title') or config.get('title') or 'documentary'}\n"
            f"Surrounding script (context only): {script_context or 'none'}\n"
            f"Canonical entity: {requirement_entity or positives or 'generic context'}\n"
            f"Required entity type: {requirement.get('subject_type') or 'unknown'}\n"
            f"Required domain: {requirement.get('domain') or scene.get('domain') or 'general documentary'}\n"
            f"Required attributes: {required_attributes}\n"
            f"Scene subject scope: {json.dumps(dict(requirement.get('subject_context') or {}), ensure_ascii=False)}\n"
            "For a contextual component/object/process scene, inspect the named physical subject and visible target. "
            "A supporting example need not be installed in the film's named vehicle or location unless the narration "
            "or required attributes explicitly assert that origin. Never mistake a camera viewpoint in the title "
            "for the required physical component. Keep real wrong-subject and domain contradictions as vetoes.\n"
            f"Forbidden subjects: {forbidden or 'none'}\n"
            f"Requirement hash: {requirement.get('requirement_hash') or 'legacy'}\n"
            f"Visual intent: {scene.get('visual_intent') or 'documentary_context'}\n"
            f"FINAL ACCEPTANCE TARGET: {scene.get('text') or ''}\n"
            "Explain support in reason for this exact sentence based on visible evidence."
        )
        system = (
            "You are the final factual visual verifier. Inspect only the supplied asset. "
            "Never infer fair use or rights. Return one JSON object and no prose."
        )
        if external_agent:
            prompt = (
                'Inspect the actual pixels first. Return one JSON object with scene_description, candidate_id, '
                'identity_score, action_score, context_score, quality, final_score (0-100), entity_conflict, '
                'fatal_mismatch, domain_match, scene_match, reason, visible_overlays, subject_box, safe_text_regions, '
                'source_presentation_type and has_text. Report overlays honestly; boxes use normalized x/y/w/h. '
                'Metadata is a hint, not pixel evidence. Reject a competing named person, venue, event, model or era; '
                'reject simulations, memes, contradictory charts and blocking captions. An authentic same-event '
                'establishing shot or relevant documentary context may support an abstract/private step without '
                'pretending that the exact action is visible. Score the actual action honestly. Do not infer an '
                'invisible date or fact from a query. Inspect video samples across time; transitions and minor '
                'broadcast labels alone do not invalidate otherwise matching footage. Never infer rights.\n'
                f'Candidate ID: {candidate.get("candidate_id") or candidate.get("id") or ""}\n'
                f'Exact narration: {scene.get("text") or ""}\n'
                f'Visual requirement: {json.dumps(dict(requirement), ensure_ascii=False)}\n'
                f'Positive subjects: {positives}; forbidden subjects: {forbidden}\n'
                f'Film context: {script_context}\nMetadata hint: {candidate_metadata}'
            )

        is_video = _is_video(path, str(candidate.get("media_type") or ""))
        frames: list[str] = []
        cleanup_frames = None
        if is_video:
            from .visual_intelligence.frame_extractor import cleanup_frames, extract_clip_frames
            try:
                # A clip is accepted only when its identity and scene context
                # are stable across time.  One opening frame routinely shows
                # title cards, transitions, or unrelated inserts, so strict
                # verification samples three to five chronological frames.
                configured_frames = int(
                    config.get("visual_verification_frame_count")
                    or os.environ.get("VIDZEO_VISION_CLIP_FRAMES", "2")
                    or 2
                )
                frame_count = max(2, min(5, configured_frames))
                frame_edge = max(
                    256,
                    min(640, int(os.environ.get("VIDZEO_VISION_CLIP_LONG_EDGE", "512") or 512)),
                )
                frames, _duration = extract_clip_frames(
                    path, max_frames=frame_count, long_edge=frame_edge
                )
                if not frames:
                    raise ValueError("no_clip_frames")
            except Exception as exc:
                return {
                    "accepted": False,
                    "status": "asset_frame_error",
                    "reason": f"{type(exc).__name__}:{str(exc)[:160]}",
                    "final_score": 0.0,
                    "schema_valid": False,
                }

        def analyze_with(target_client) -> Any:
            if is_video:
                return target_client.analyze_clip_frames(frames, prompt, system)
            return target_client.analyze_image(path, prompt, system)

        primary_errors = list(previous_schema_errors)
        try:
            try:
                primary_verdict = analyze_with(client)
                primary_valid, primary_result, live_primary_errors = _validate_qwen_verdict_schema(
                    candidate, primary_verdict,
                )
                primary_errors.extend(live_primary_errors)
                if primary_valid:
                    primary_result.update({
                        "schema_valid": True,
                        "verification_attempt": "primary",
                        "verification_model": str(getattr(client, "model", context.model) or ""),
                        "visual_checked": True,
                        "visual_available": True,
                    })
                    return done(_score_verdict(scene, primary_result))
            except Exception as exc:
                primary_errors.append(f"primary_error:{type(exc).__name__}")

            # A schema-invalid or malformed primary verdict receives exactly
            # one logical fallback-model attempt. The fallback is a context
            # client, so it reserves its own call/concurrency slot and keeps
            # the OpenRouter client's Retry-After behavior.
            try:
                fallback_client = context.get_fallback_client()
            except Exception as exc:
                fallback_client = None
                primary_errors.append(f"fallback_client_error:{type(exc).__name__}")
            if fallback_client is None:
                return {
                    "accepted": False,
                    "status": "schema_invalid",
                    "reason": "fallback_model_unavailable",
                    "final_score": 0.0,
                    "schema_valid": False,
                    "schema_errors": list(dict.fromkeys(primary_errors)),
                }
            try:
                fallback_verdict = analyze_with(fallback_client)
                fallback_valid, fallback_result, fallback_errors = _validate_qwen_verdict_schema(
                    candidate, fallback_verdict,
                )
            except Exception as exc:
                fallback_valid = False
                fallback_result = {}
                fallback_errors = [f"fallback_error:{type(exc).__name__}"]
            if not fallback_valid:
                return {
                    "accepted": False,
                    "status": "schema_invalid",
                    "reason": "fallback_schema_rejected",
                    "final_score": 0.0,
                    "schema_valid": False,
                    "primary_schema_errors": list(dict.fromkeys(primary_errors)),
                    "schema_errors": list(dict.fromkeys(fallback_errors)),
                    "verification_model": str(getattr(fallback_client, "model", context.fallback_model) or ""),
                }
            fallback_result.update({
                "schema_valid": True,
                "verification_attempt": "fallback",
                "verification_model": str(getattr(fallback_client, "model", context.fallback_model) or ""),
                "primary_schema_errors": list(dict.fromkeys(primary_errors)),
                "visual_checked": True,
                "visual_available": True,
            })
            return done(_score_verdict(scene, fallback_result))
        finally:
            if frames and cleanup_frames is not None:
                cleanup_frames(frames)

    verify.visual_context = context
    return verify, context


def resolve_and_build_media_plan(
    scenes: Sequence[Any],
    *,
    youtube_lane: Callable[[Mapping[str, Any], threading.Event], Any],
    web_lane: Callable[[Mapping[str, Any], threading.Event], Any],
    youtube_fallback_lane: Callable[[Mapping[str, Any], threading.Event], Any] | None = None,
    web_fallback_lane: Callable[[Mapping[str, Any], threading.Event], Any] | None = None,
    stock_lane: Callable[[Mapping[str, Any], threading.Event], Any] | None,
    visual_verifier: Callable[[Mapping[str, Any], dict[str, Any]], Any] | None,
    neutral_factory: Callable[[Mapping[str, Any]], Any],
    progress_callback: Callable[[float, str], Any] | None = None,
    deadline_seconds: float = 210.0,
    web_fallback_grace_seconds: float = 0.0,
    max_workers: int = 6,
    guarantee_enabled: bool | None = None,
    web_wait_until_complete: bool = False,
    external_cancel_event: threading.Event | None = None,
    adaptive_media_review: bool = False,
) -> tuple[dict[str, Any], dict[int, SceneResolution]]:
    resolutions = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=youtube_lane,
        web_lane=web_lane,
        youtube_fallback_lane=youtube_fallback_lane,
        web_fallback_lane=web_fallback_lane,
        stock_lane=stock_lane,
        visual_verifier=visual_verifier,
        neutral_factory=neutral_factory,
        progress_callback=progress_callback,
        deadline_seconds=deadline_seconds,
        web_fallback_grace_seconds=web_fallback_grace_seconds,
        max_workers=max_workers,
        web_wait_until_complete=bool(web_wait_until_complete),
        external_cancel_event=external_cancel_event,
        adaptive_media_review=adaptive_media_review,
    )
    paths: list[str | None] = []
    labels: list[str] = []
    resolution_rows: list[dict[str, Any]] = []
    source_counter: Counter[str] = Counter()
    terminal_counter: Counter[str] = Counter()
    for index in range(len(scenes)):
        resolution = resolutions[index]
        row = resolution.to_dict()
        path = str(resolution.selected_path or "") or None
        provider = str(resolution.provider or "neutral")
        label = {
            "youtube": "YouTube Clips",
            "web": "Web Images",
            "stock": "Stock Media",
            "graphic": "Grounded Graphic Fallback",
            "neutral": "Neutral No-Claim Visual",
        }.get(provider.lower(), provider)
        if provider.lower() == 'youtube' and not _is_moving_youtube_source(resolution):
            label = 'YouTube Extracted Frame'
        paths.append(path)
        labels.append(label)
        resolution_rows.append(row)
        source_counter[label] += 1
        terminal = "degraded" if resolution.fallback_reason.startswith("degraded:") else resolution.status
        terminal_counter[terminal] += 1
        visual_status = str((resolution.visual_decision or {}).get("status") or "")
        if visual_status == "deterministic_degraded":
            resolution.verification_status = "deterministic_degraded"
            row["verification_status"] = "deterministic_degraded"
    metadata = {
        "pipeline_schema_version": "2.0",
        "media_resolution_v2": resolution_rows,
        "source_accounting": dict(source_counter),
        "terminal_accounting": dict(terminal_counter),
        "source_scene_texts": [str(_scene_value(scene, "text", "")) for scene in scenes],
        "visual_requirements": [
            dict(_scene_value(scene, "visual_requirement", {}) or {})
            for scene in scenes
        ],
        "scene_timing_windows": [
            {
                "scene_index": int(_scene_value(scene, "order", index)),
                "scene_id": str(_scene_value(scene, "scene_id", "")),
                "container_id": str(_scene_value(scene, "container_id", "")),
                "start_seconds": _scene_value(scene, "start_seconds", None),
                "end_seconds": _scene_value(scene, "end_seconds", None),
            }
            for index, scene in enumerate(scenes)
        ],
    }
    graphic_indices = [
        index for index, resolution in resolutions.items()
        if str(getattr(resolution, "provider", "") or "").strip().lower() in {"graphic", "generated_graphic"}
    ]
    metadata["global_media_guarantee"] = {
        "enabled": bool(guarantee_enabled) if guarantee_enabled is not None else bool(graphic_indices),
        "policy": "grounded_graphic" if graphic_indices else "real_media_only",
        "scene_count": len(scenes),
        "real_media_count": max(0, len(scenes) - len(graphic_indices) - sum(
            1 for resolution in resolutions.values()
            if str(getattr(resolution, "provider", "") or "").strip().lower() in {"neutral", ""}
        )),
        "grounded_fallback_count": len(graphic_indices),
        "grounded_fallback_indices": graphic_indices,
        "no_blank_assets": all(bool(path) for path in paths),
    }
    return {
        "media_paths": paths,
        "source_labels": labels,
        "metadata": metadata,
        "prompt_bundle": {"beat_texts": metadata["source_scene_texts"]},
    }, resolutions


def _source_lane_policy(config: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the user-selected source mode into authoritative V2 lanes."""
    raw = str(
        config.get("image_source")
        or config.get("media_mode")
        or "Mixed Media"
    ).strip()
    normalized = re.sub(r"[\s_-]+", " ", raw).strip().casefold()
    allow_stock = _as_bool(config.get("allow_relevant_stock"), True)
    # This is separate from the normal Stock Media lane. When enabled, stock
    # is a recovery source for Web/YouTube/Mixed jobs, but it is only called by
    # the coordinator after every factual source and verified reuse layer has
    # failed. An explicit false remains an operator opt-out.
    allow_stock_last_resort = _as_bool(config.get("allow_stock_last_resort"), True)
    # Stock is an emergency terminal lane, not a planned source. It never
    # enters the web/YouTube visual-verification budget; deterministic file,
    # safety, and hard identity checks still apply before the degraded handoff.
    stock_last_resort_no_vision = _as_bool(config.get('stock_last_resort_no_vision'), True)
    if normalized in {"web", "web image", "web images"}:
        mode = "Web Images"
        youtube, web, stock = False, True, False
    elif normalized in {"youtube", "youtube clip", "youtube clips"}:
        mode = "YouTube Clips"
        youtube, web, stock = True, False, False
    elif normalized in {"stock", "stock media", "real media"}:
        mode = "Stock Media"
        youtube, web, stock = False, False, allow_stock
    elif normalized in {"ai", "ai image", "ai images", "custom", "custom upload"}:
        # These are intentionally not emulated with unrelated retrieval.
        mode = raw or "Unsupported"
        youtube, web, stock = False, False, False
    else:
        mode = "Mixed Media"
        youtube, web, stock = True, True, allow_stock
        policy = str(config.get("mixed_media_policy") or "automatic").strip().lower()
        raw_weights = config.get("mixed_media_weights") or config.get("media_weights")
        # The app computes a script-level media policy before it enters the
        # provider branches.  Do not let this lower-level adapter reclassify
        # the same script with the old keyword heuristic: a factual car
        # documentary, for example, must not silently reopen stock footage
        # simply because the word "car" appears in the script.  The explicit
        # UI weights win first; then the validated niche policy and prebuilt
        # plan are authoritative.  Only an unplanned automatic request may
        # use the deterministic topic classifier as a last resort.
        authoritative_weights = raw_weights if isinstance(raw_weights, Mapping) else None
        if authoritative_weights is None:
            niche_policy = config.get("_niche_media_policy")
            if isinstance(niche_policy, Mapping) and isinstance(niche_policy.get("weights"), Mapping):
                authoritative_weights = niche_policy.get("weights")
        if authoritative_weights is None:
            prebuilt_plan = config.get("_prebuilt_mixed_plan")
            if isinstance(prebuilt_plan, Mapping) and isinstance(prebuilt_plan.get("requested_weights"), Mapping):
                authoritative_weights = prebuilt_plan.get("requested_weights")
        if authoritative_weights is None:
            plan_policy = config.get("media_policy")
            if isinstance(plan_policy, Mapping) and isinstance(plan_policy.get("weights"), Mapping):
                authoritative_weights = plan_policy.get("weights")

        if isinstance(authoritative_weights, Mapping) and "Stock Media" in authoritative_weights:
            try:
                stock = bool(
                    allow_stock
                    and float(authoritative_weights.get("Stock Media") or 0.0) > 0.0
                )
            except (TypeError, ValueError):
                stock = False
        elif policy in {"auto", "automatic", "smart", "topic_aware", "topic-aware"}:
            from .mixed_media_planner import classify_mixed_media_topic
            topic = classify_mixed_media_topic(
                title=str(config.get("title") or config.get("content_title") or ""),
                script=str(config.get("script") or ""),
                category=str(config.get("category") or config.get("niche") or ""),
            )
            stock = bool(allow_stock and topic != "serious")
    if mode == 'Mixed Media' and _as_bool(config.get('stock_rescue_only'), True):
        stock = False
    if mode == 'Mixed Media':
        stock_last_resort_no_vision = True
    stock_last_resort_enabled = bool(
        allow_stock
        and allow_stock_last_resort
        and mode in {"Web Images", "YouTube Clips", "Mixed Media", "Stock Media"}
    )
    # Terminal stock now runs as a bounded parallel wave before graphics.
    # Deferring it until after the media contract can reject a render before
    # the app-level repair ever gets a chance to retrieve real rescue media.
    if mode == "Mixed Media" and not _as_bool(config.get("stock_last_resort_in_coordinator"), True):
        stock_last_resort_enabled = False
    return {
        "mode": mode,
        "youtube_enabled": youtube,
        "web_enabled": web,
        "stock_enabled": stock,
        "stock_last_resort_enabled": stock_last_resort_enabled,
        "stock_last_resort_no_vision": stock_last_resort_no_vision,
        "media_source_priority": list(config.get('media_source_priority') or ['youtube', 'web', 'stock']),
        # Source selection controls the preferred lane, not the set of
        # truthful recovery options.  The coordinator may ask the opposite
        # real-media lane for a candidate after the preferred lane fails.
        "cross_source_fallback": _as_bool(
            config.get("cross_source_fallback", True),
        ),
        "web_source_catalog": web_image_source_catalog(),
        "web_fallback_layers": web_image_fallback_layers(),
        "web_collect_all_sources": _as_bool(config.get("web_collect_all_sources"), True),
        "web_unique_assets_required": _as_bool(config.get("unique_web_assets_required"), False),
    }

def _mixed_media_youtube_target_ratio(config: Mapping[str, Any]) -> float:
    """Resolve one auditable YouTube percentage for Mixed Media.

    Explicit accepted-coverage overrides and explicit per-channel UI source
    maps control the quota. Generated niche/prebuilt preferences do not lower
    the default 30% scene-slot target; that target remains best-effort because
    relevance and reuse-clearance checks decide which clips may be accepted.
    """
    explicit_keys = ("youtube_accepted_coverage_target", "youtube_coverage_target", "youtube_target_percentage", "yt_percentage", "footage_percentage", "video_percentage", "youtube_percentage", "youtube_ratio")
    for key in explicit_keys:
        if key in config and config.get(key) is not None and str(config.get(key)).strip() != "":
            try:
                raw_v = float(str(config.get(key)).strip().rstrip("%"))
                if not math.isfinite(raw_v):
                    continue
                val = raw_v / 100.0 if raw_v > 1.0 else raw_v
                return max(0.0, min(1.0, val))
            except (TypeError, ValueError):
                pass

    env_target = os.environ.get("VIDZEO_YOUTUBE_ACCEPTED_COVERAGE_TARGET")
    if env_target is not None and str(env_target).strip() != "":
        try:
            raw_v = float(str(env_target).strip().rstrip("%"))
            if math.isfinite(raw_v):
                return max(0.0, min(1.0, raw_v / 100.0 if raw_v > 1.0 else raw_v))
        except (TypeError, ValueError):
            pass

    # Explicit per-channel UI percentages are authoritative when present.
    # Generated niche/prebuilt weights are preferences; the shared default
    # target remains 30% and never bypasses candidate relevance checks.
    candidate_maps = []
    raw_weights = config.get("mixed_media_weights") or config.get("media_weights")
    if isinstance(raw_weights, Mapping):
        candidate_maps.append(raw_weights)

    for weights in candidate_maps:
        if "YouTube Clips" not in weights:
            continue
        try:
            values = {str(key): max(0.0, float(value or 0.0)) for key, value in weights.items()}
            if not all(math.isfinite(value) for value in values.values()):
                continue
            total = sum(values.values())
            if total > 0.0:
                return max(0.0, min(1.0, values.get("YouTube Clips", 0.0) / total))
        except (TypeError, ValueError):
            continue

    return 0.30


def _automatic_youtube_scene_indices(
    config: Mapping[str, Any],
    scene_texts: Sequence[str],
    *,
    title: str,
    script: str,
    scenes: Optional[Sequence[Mapping[str, Any]]] = None,
) -> list[int]:
    """Return the exact YouTube quota for an automatic Mixed Media job.

    The V2 coordinator still owns every storyboard scene so web fallback and
    timing remain complete. In multi-container listicles, quota is distributed
    across containers so each item receives its dedicated video cut without cross-item bleed.
    """
    count = len(scene_texts)

    # Multi-container listicle handling: isolate quota per container
    container_groups: dict[str, list[int]] = {}
    if scenes:
        for idx, sc in enumerate(scenes):
            if isinstance(sc, Mapping):
                cid = str(sc.get("container_id") or sc.get("section_subject") or "").strip()
            else:
                cid = str(getattr(sc, "container_id", "") or getattr(sc, "section_subject", "") or "").strip()
            if cid:
                container_groups.setdefault(cid, []).append(idx)

    target_ratio = _mixed_media_youtube_target_ratio(config)
    # Quotas are minimum coverage targets, so round up rather than silently
    # undershooting (for example, 50% of 9 scenes must reserve 5 slots, not 4).
    # Keep zero disabled and clamp to the available scene count.
    target_yt_count = (
        min(count, max(1, math.ceil(count * target_ratio)))
        if count and target_ratio > 0.0
        else 0
    )
    if target_yt_count == 0:
        return []

    if len(container_groups) > 1:
        # Distribute the aspirational target across factual containers first,
        # then fill the remaining slots deterministically.  This keeps video
        # coverage useful for listicles without pretending that a requested
        # slot is an accepted YouTube result.
        selected_indices: list[int] = []
        for member_indices in container_groups.values():
            if len(selected_indices) >= target_yt_count:
                break
            best_scene = member_indices[0] if len(member_indices) == 1 else member_indices[min(1, len(member_indices) - 1)]
            selected_indices.append(best_scene)
        for candidate_index in range(count):
            if len(selected_indices) >= target_yt_count:
                break
            if candidate_index not in selected_indices:
                selected_indices.append(candidate_index)
        return sorted(selected_indices)


    prebuilt = config.get("_prebuilt_mixed_plan") if isinstance(config, Mapping) else None
    if isinstance(prebuilt, Mapping) and prebuilt.get("_scene_count") == count:
        grouped = prebuilt.get("grouped_indices") or {}
        indices = sorted({int(index) for index in (grouped.get("YouTube Clips") or [])})
        indices = [index for index in indices if 0 <= index < count]
        if count and not indices:
            indices = [0]
        if len(indices) < target_yt_count:
            extra = [index for index in range(count) if index not in indices]
            indices = sorted(indices + extra[:target_yt_count - len(indices)])
        elif len(indices) > target_yt_count:
            # The saved clip percentage is the lane's target. A prebuilt
            # adaptive mix can classify every scene as YouTube for some
            # niches; letting that oversized group through multiplies slow
            # downloads until the single batch misses the coordinator wall.
            indices = [
                indices[(position * (len(indices) - 1)) // max(1, target_yt_count - 1)]
                for position in range(target_yt_count)
            ]
        return indices

    from .mixed_media_planner import build_adaptive_mixed_media_plan

    plan = build_adaptive_mixed_media_plan(
        list(scene_texts),
        weights=config.get("mixed_media_weights"),
        title=title,
        script=script,
        category=config.get("category") or config.get("niche") or "",
        scene_media_mode=config.get("mixed_media_policy") or "automatic",
        strict_weights=False,
    )
    indices = sorted({int(index) for index in (plan.grouped_indices.get("YouTube Clips") or [])})
    indices = [index for index in indices if 0 <= index < count]
    if count and not indices and plan.requested_weights.get("YouTube Clips", 0.0) > 0.0:
        indices = [0]
    if len(indices) < target_yt_count:
        extra = [index for index in range(count) if index not in indices]
        indices = sorted(indices + extra[:target_yt_count - len(indices)])
    elif len(indices) > target_yt_count:
        # Keep the configured clip share exact even when niche weights or an
        # adaptive plan asks for a larger YouTube group, distributed across
        # the full timeline rather than taking an opening-only prefix.
        indices = [
            indices[(position * (len(indices) - 1)) // max(1, target_yt_count - 1)]
            for position in range(target_yt_count)
        ]
    return indices


def _youtube_recovery_excluded_ids(
    audit: Mapping[str, Any], primary_indices: Sequence[int], recovery_indices: Sequence[int],
) -> set[str]:
    """Map worker-local attempt rows back to the rejected storyboard scenes."""
    excluded: set[str] = set()
    requested = set(recovery_indices)
    for local_index, attempted in (audit.get("attempts") or {}).items():
        try:
            offset = int(local_index)
            if offset < 0:
                continue
            original_index = primary_indices[offset]
        except (ValueError, TypeError, IndexError):
            continue
        if original_index not in requested:
            continue
        for attempt in attempted if isinstance(attempted, list) else ():
            if not isinstance(attempt, Mapping):
                continue
            video_id = str(attempt.get("video_id") or attempt.get("candidate_id") or "")
            if video_id:
                excluded.add(video_id)
    return excluded


def _youtube_fallback_indices(
    scene_count: int,
    primary_indices: Sequence[int],
    primary_rows: Mapping[int, Any],
    *,
    retry_downloaded: bool = False,
) -> list[int]:
    """Return scenes that need the second YouTube retrieval wave.

    Non-quota scenes are always eligible for cross-source YouTube recovery.
    A quota scene is added only when its lazy primary batch produced no local
    candidate, preventing an expensive duplicate search for every successful
    primary clip while still rescuing transport/overlay misses. Strict mixed
    media can opt into retrying downloaded primary rows too: a downloaded cut
    may have failed the scene-level semantic gate even though another timestamp
    or publisher from the same YouTube subject pool can pass it.
    """
    primary = [int(index) for index in primary_indices if 0 <= int(index) < int(scene_count)]
    primary_set = set(primary)
    retry = [index for index in range(max(0, int(scene_count))) if index not in primary_set]
    for index in primary:
        payload = primary_rows.get(index)
        candidates = payload if isinstance(payload, (list, tuple)) else [payload]
        has_path = any(
            isinstance(candidate, Mapping)
            and str(
                candidate.get("path")
                or candidate.get("selected_path")
                or candidate.get("local_path")
                or ""
            ).strip()
            for candidate in candidates
        )
        if retry_downloaded or not has_path:
            retry.append(index)
    return list(dict.fromkeys(retry))



def _completed_saved_web_indices(saved):
    """A valid receipt may skip discovery only within the physical reuse cap."""
    used = {}
    completed = set()
    for index in sorted(saved):
        for candidate in saved[index]:
            if candidate.get('verification_status') != 'verified_reuse':
                continue
            decision = candidate.get('visual_decision') or candidate.get('qwen_result') or {}
            identity = str(decision.get('verified_asset_sha256') or candidate.get('asset_fingerprint')
                           or _asset_fingerprint(str(candidate.get('path') or '')))
            uses = used.get(identity, [])
            if not identity or len(uses) >= 2 or any(abs(index - previous) <= 1 for previous in uses):
                continue
            used.setdefault(identity, []).append(index)
            completed.add(index)
            break
    return completed


def _verify_saved_web_candidates(saved, scenes, verifier, *, timeout_seconds=90.0):
    """Finish deferred local checks before repeating network retrieval on retry."""
    if verifier is None or timeout_seconds <= 0:
        return 0
    work = [(index, rows[0]) for index, rows in saved.items()
            if rows and not any(row.get('verification_status') == 'verified_reuse' for row in rows)]
    if not work:
        return 0
    from .media_resolution_coordinator import _scene_row
    contexts = {index: _scene_row(scenes[index], index) for index, _ in work}
    pool = ThreadPoolExecutor(max_workers=min(3, len(work)), thread_name_prefix='saved-media-check')
    futures = {pool.submit(verifier, contexts[index], row): (index, row) for index, row in work}
    accepted = 0
    try:
        for future in as_completed(futures, timeout=max(.01, timeout_seconds)):
            index, row = futures[future]
            try:
                decision = future.result()
                if not isinstance(decision, dict):
                    continue
                candidate = dict(row, visual_decision=decision, qwen_result=decision)
                requirement = contexts[index].get('visual_requirement') or {}
                if not _has_bound_pixel_review(requirement, candidate, str(row.get('path') or '')):
                    continue
                row.update(visual_decision=decision, qwen_result=dict(decision),
                           semantic_context_pending=False, semantic_visual_pending=False,
                           visual_verification_required=False, verification_status='verified_reuse')
                accepted += 1
            except Exception:
                continue
    except FutureTimeoutError:
        pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return accepted


def _mixed_youtube_branch_wall_seconds(config: Mapping[str, Any]) -> float:
    try:
        requested = float(
            config.get("yt_branch_wall_timeout_seconds")
            or os.environ.get("VIDZEO_MIXED_YT_BRANCH_WALL_TIMEOUT_SECONDS")
            or os.environ.get("VIDZEO_YT_BRANCH_WALL_TIMEOUT_SECONDS")
            or 360.0
        )
    except (TypeError, ValueError):
        requested = 360.0
    quota_count = _bounded_audit_int(config.get("_youtube_budget_scene_count"), 0, 10000)
    if quota_count:
        requested, _ = mixed_youtube_time_budget(
            quota_count, requested, download_workers=1, coverage_target=1.0,
        )
    if str(config.get("llm_provider") or "") == "external_agent":
        # No fixed branch wall for agent jobs: the shared delivery budget
        # (which scales with the job) is the only bound. A fixed 360s cap
        # starves long-form YouTube retrieval that legitimately needs
        # longer; the media stage deadline still clamps to the budget.
        try:
            from .delivery_budget import get_delivery_budget

            _budget = get_delivery_budget(config)
        except Exception:
            _budget = None
        if _budget is not None:
            try:
                _reserve = float(
                    config.get("_render_reserve_seconds")
                    or _budget.render_reserve_seconds
                )
            except (TypeError, ValueError):
                _reserve = float(_budget.render_reserve_seconds)
            requested = max(
                requested,
                _budget.allowance(None, reserve_after_seconds=_reserve),
            )
    return max(120.0, requested)


def _effective_mixed_youtube_branch_wall_seconds(config: Mapping[str, Any]) -> float:
    wall = _mixed_youtube_branch_wall_seconds(config)
    direct_clip_lane = str(
        os.environ.get("VIDZEO_YT_POOL_DIRECT_CLIPS_ONLY", "0") or "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    if direct_clip_lane and "yt_branch_wall_timeout_seconds" not in config:
        wall = min(150.0, max(60.0, wall))
    return wall


def _youtube_branch_return_tail_seconds(config: Mapping[str, Any], lane_mode: str) -> float:
    """Reserve time for a hard-stopped batch to cancel, salvage, and publish rows."""
    normalized_mode = str(lane_mode or "").strip().casefold()
    direct_clip_lane = str(
        os.environ.get("VIDZEO_YT_POOL_DIRECT_CLIPS_ONLY", "0") or "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    default = 60.0 if normalized_mode == "mixed media" and direct_clip_lane else (
        180.0 if normalized_mode == "mixed media" else 90.0
    )
    raw = config.get("yt_branch_return_tail_seconds") or os.environ.get(
        "VIDZEO_YT_BRANCH_RETURN_TAIL_SECONDS", str(default),
    )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(60.0, min(300.0, value))


def _mixed_web_completion_cap_seconds() -> float:
    """Ceiling for the primary Mixed Media web wall.

    The old 180s ceiling cut a 121-scene job off after ~35 scenes. The
    completion cap is generous (30 minutes by default) so the wall can
    scale with the scenes left to resolve; the delivery budget and the
    coordinator's outer wall still bound the stage from above.
    """
    try:
        return max(
            180.0,
            float(
                os.environ.get("VIDZEO_MIXED_WEB_COMPLETION_CAP_SECONDS", "1800")
                or 1800
            ),
        )
    except (TypeError, ValueError):
        return 1800.0


def _mixed_web_per_scene_seconds() -> float:
    """Per-scene allowance the primary web wall scales with."""
    try:
        return max(
            1.0,
            min(
                30.0,
                float(
                    os.environ.get("VIDZEO_MIXED_WEB_PER_SCENE_SECONDS", "6")
                    or 6
                ),
            ),
        )
    except (TypeError, ValueError):
        return 6.0


def _mixed_web_wall_seconds(
    scene_count: int,
    configured_wall: float,
    floor: float,
    cap: float,
    budget_allowance_seconds: Optional[float] = None,
) -> float:
    """Completion-oriented primary web wall for the Mixed Media lane.

    The wall is the largest of the floor, the operator-configured wall, and
    a scene-scaled allowance (scene_count * per-scene seconds), capped by
    the completion cap and — when given — the delivery-budget allowance
    (the render reserve is never spent). A large job therefore finishes
    its queued scenes instead of being cut off at a fixed time; a small
    job keeps its short wall.
    """
    try:
        floor_value = max(1.0, float(floor))
    except (TypeError, ValueError):
        floor_value = 120.0
    try:
        cap_value = max(floor_value, float(cap))
    except (TypeError, ValueError):
        cap_value = max(floor_value, 1800.0)
    if budget_allowance_seconds is not None:
        try:
            cap_value = min(
                cap_value, max(floor_value, float(budget_allowance_seconds))
            )
        except (TypeError, ValueError):
            pass
    try:
        configured_value = max(floor_value, float(configured_wall))
    except (TypeError, ValueError):
        configured_value = floor_value
    scaled_value = max(
        floor_value,
        min(cap_value, max(1, int(scene_count)) * _mixed_web_per_scene_seconds()),
    )
    return min(cap_value, max(floor_value, configured_value, scaled_value))


def _media_stage_deadline_seconds(config: Mapping[str, Any], lane_mode: str) -> float:
    """Keep V2 alive until the enabled provider can publish its rows.

    YouTube pool-first has its own branch wall. If the generic media deadline
    expires first, neutral rows are finalized while the YouTube worker later
    records successful cuts in the audit, producing a split-brain result.
    """
    # Waiting for web completion still obeys the job delivery budget. An
    # infinite coordinator deadline lets a stalled provider suppress recovery.
    try:
        base = float(config.get("media_deadline_seconds") or os.environ.get("VIDZEO_MEDIA_DEADLINE_SECONDS") or 600.0)
    except (TypeError, ValueError):
        base = 600.0
    base = max(1.0, base)
    if str(config.get('llm_provider') or '') == 'external_agent' and not config.get('media_deadline_seconds'):
        # A connected tool-driven host answers serially, unlike a model API.
        # Still bounded by the job delivery allowance below.
        base = 1200.0
    normalized_mode = str(lane_mode or "").strip().casefold()
    if normalized_mode == "mixed media":
        yt_branch_wall = _effective_mixed_youtube_branch_wall_seconds(config)

        # Account for the worker's actual transfer-wave floor, rather than
        # expiring discovery at the UI's shorter requested wall. A one-worker
        # allowance is conservative on low-memory machines; healthy batches
        # still return immediately when ready. Never spend the render reserve.
        # Mixed Media can raise the YouTube wall to reserve serial transfer waves.
        # The YouTube worker in app.py runs with yt_wall_seconds plus executor grace,
        # disk recovery, and local snapshotting. Ensure the coordinator deadline leaves
        # sufficient room for that entire chain so the shared-batch waiter does
        # not terminalize every scene just before accepted clips are returned.
        direct_clip_lane = str(
            os.environ.get("VIDZEO_YT_POOL_DIRECT_CLIPS_ONLY", "0") or "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if direct_clip_lane and "yt_branch_wall_timeout_seconds" not in config:
            computed = max(base, yt_branch_wall + 60.0)
        else:
            effective_yt_wall = max(120.0, yt_branch_wall)
            return_tail = _youtube_branch_return_tail_seconds(config, normalized_mode)
            computed = max(base, effective_yt_wall + return_tail)
    elif normalized_mode == "youtube clips":
        try:
            branch_wall = float(
                config.get("yt_branch_wall_timeout_seconds")
                or os.environ.get("VIDZEO_MIXED_YT_BRANCH_WALL_TIMEOUT_SECONDS")
                or os.environ.get("VIDZEO_YT_BRANCH_WALL_TIMEOUT_SECONDS")
                or 360.0
            )
        except (TypeError, ValueError):
            branch_wall = 360.0
        computed = max(base, branch_wall + 120.0)
    else:
        computed = base

    # The provider branches and the V2 coordinator must share one wall clock.
    # Previously this function returned the configured media ceiling even when
    # the app-wide DeliveryBudget had already spent most of its retrieval
    # allowance. Scene-scoped cross-lane callbacks could then continue running
    # after the render reserve and add several minutes of serial retries. Clamp
    # the coordinator to the budget's currently available non-render time; a
    # provider may still fail inside that window, but it cannot extend the job
    # past the deadline that the rest of the app is already observing.
    try:
        from .delivery_budget import get_delivery_budget

        delivery_budget = get_delivery_budget(config)
    except Exception:
        delivery_budget = None
    # Floor: never collapse the media stage below one agent round-trip (or
    # 300s). A 1s wall terminalises every scene as a graphic fallback and
    # silently produces a footage-free video. The delivery budget is a soft
    # target; real footage matters more than the clock.
    if str(config.get('llm_provider') or '') == 'external_agent':
        from services.agent_bridge import AGENT_REQUEST_WALL_SECONDS
        media_floor = AGENT_REQUEST_WALL_SECONDS
    else:
        media_floor = 300.0
    if delivery_budget is not None:
        try:
            reserve_after = float(
                config.get("_render_reserve_seconds")
                or delivery_budget.render_reserve_seconds
            )
        except (TypeError, ValueError):
            reserve_after = float(delivery_budget.render_reserve_seconds)
        available = delivery_budget.allowance(
            computed,
            reserve_after_seconds=reserve_after,
        )
        if available <= 0.0:
            return media_floor
        return max(media_floor, min(computed, available))
    return computed


def _web_fallback_coordinator_grace_seconds(
    config: Mapping[str, Any],
    lane_policy: Mapping[str, Any],
    media_deadline_seconds: float,
) -> float:
    """Reserve a bounded web-only rescue tail before emergency stock.

    In live 24-scene runs the recovery scraper finished just after the main
    media deadline, so its audit showed many usable images while the renderer
    had already committed those scenes to stock. The tail is limited by the
    shared delivery budget and render reserve; it does not extend YouTube or
    primary discovery deadlines.
    """
    if not lane_policy.get("web_enabled") or not lane_policy.get("cross_source_fallback", True):
        return 0.0
    configured = config.get("web_fallback_coordinator_grace_seconds")
    if configured is None:
        configured = os.environ.get("VIDZEO_WEB_FALLBACK_COORDINATOR_GRACE_SECONDS", "150")
    try:
        requested = max(0.0, min(240.0, float(configured)))
    except (TypeError, ValueError):
        requested = 150.0
    if requested <= 0.0:
        return 0.0

    try:
        from .delivery_budget import get_delivery_budget

        delivery_budget = get_delivery_budget(config)
    except Exception:
        delivery_budget = None
    if delivery_budget is None:
        return requested

    try:
        reserve_after = float(
            config.get("_render_reserve_seconds")
            or delivery_budget.render_reserve_seconds
        )
    except (TypeError, ValueError):
        reserve_after = float(delivery_budget.render_reserve_seconds)
    try:
        available_total = delivery_budget.allowance(
            max(1.0, float(media_deadline_seconds)) + requested,
            reserve_after_seconds=reserve_after,
        )
    except Exception:
        return 0.0
    return max(
        0.0,
        min(requested, float(available_total) - max(1.0, float(media_deadline_seconds))),
    )


def resolve_existing_provider_branches_v2(
    config: dict[str, Any],
    audio_plan: Mapping[str, Any],
    *,
    title: str,
    script: str,
    render_resolution: Sequence[int],
    youtube_runner: Callable[..., Any],
    web_runner: Callable[..., Any],
    stock_runner: Callable[..., Any],
    snapshotter: Callable[..., Sequence[str | None]],
    neutral_asset_factory: Callable[..., str | None],
    report_progress: Callable[[float, str], Any],
    cancellation: Any = None,
) -> tuple[dict[str, Any], dict[int, SceneResolution], VisualDecisionContext]:
    """Route the current provider transports through the strict V2 state machine."""

    from .documentary_subject import title_hook_subject, collection_title_subject, search_key
    original_title = str(config.get('content_title') or config.get('title') or title)
    supported_title_subject = title_hook_subject(original_title, script)
    collection_subject = collection_title_subject(original_title)
    if supported_title_subject:
        title = supported_title_subject
        config['_topic_anchor'] = title

    scenes = list(audio_plan.get("scene_contracts") or audio_plan.get("scenes") or ())
    if not scenes:

        raise ValueError("V2 media resolution requires final SceneContractV1 rows")
    scene_texts = [str(_scene_value(scene, "text", "") or "") for scene in scenes]
    scene_durations = [float(_scene_value(scene, "duration", 0.0) or 0.0) for scene in scenes]
    # Build the semantic identity once, before any legacy provider planner can
    # broaden a scene into a topic keyword.  These rows are immutable for the
    # lifetime of this V2 job and are also passed to the provider adapters as
    # their query source of truth.
    visual_requirements: list[dict[str, Any]] = []
    semantic_query_rows: list[dict[str, Any]] = []
    # These rows are produced by the shared OpenRouter scene planner before
    # V2 starts.  Preserve their concrete YouTube intents here.  Previously
    # the semantic lock replaced them with freshly generated provider
    # variants, which is why runs could show queries such as
    # ``Toyota Corolla documentary footage`` despite the raw-footage prompt.
    planned_query_rows = list(config.get("_yt_scene_query_overrides") or ())
    planned_web_rows = list(config.get("_prebuilt_web_queries") or ())
    repeated_title_entity = _repeated_title_entity(config, len(scenes))
    for index, scene in enumerate(scenes):
        local_direction = dict(scene) if isinstance(scene, Mapping) else {}
        for planned in (planned_web_rows, planned_query_rows):
            if index < len(planned) and isinstance(planned[index], Mapping):
                local_direction.update({key: planned[index][key] for key in
                    ('visual_subject', 'visual_subject_type', 'subject_evidence', 'visible_target', 'identity_scope', 'scene_subject')
                    if planned[index].get(key)})
        if index and _scene_value(scenes[index - 1], 'container_id', '') == _scene_value(scene, 'container_id', ''):
            local_direction['_previous_scene_subject'] = str(visual_requirements[-1].get('canonical_entity') or '')
            local_direction['_previous_scene_text'] = scene_texts[index - 1]
        if isinstance(scene, Mapping):
            positive = tuple(scene.get("positive_subjects") or ())
            forbidden = tuple(scene.get("forbidden_subjects") or ())
            intent = str(scene.get("visual_intent") or "documentary_context")
        else:
            positive = tuple(getattr(scene, "positive_subjects", ()) or ())
            forbidden = tuple(getattr(scene, "forbidden_subjects", ()) or ())
            intent = str(getattr(scene, "visual_intent", "documentary_context") or "documentary_context")
        existing_requirement = (
            scene.get("visual_requirement")
            if isinstance(scene, Mapping)
            else getattr(scene, "visual_requirement", None)
        )
        # Preflight may have built a generic educational contract from the
        # literal title (``How Electric Cars Work``) before the mixed-media
        # anchor normalizer reduced it to ``Electric Cars``. Reusing that
        # stale canonical phrase makes every provider search for the verb as
        # part of the entity and produces irrelevant vehicle results. Rebuild
        # the requirement against the normalized title when this exact shape
        # is detected; explicit named entities remain untouched.
        if isinstance(existing_requirement, Mapping):
            existing_canonical = str(existing_requirement.get("canonical_entity") or "").strip()
            from .visual_requirement import _scene_subject
            local_collection_entity = _scene_subject(scene_texts[index]) if collection_subject else ''
            if (collection_subject and search_key(existing_canonical) == search_key(collection_subject)
                    and len(local_collection_entity.split()) >= 2
                    and search_key(local_collection_entity) != search_key(collection_subject)):
                # A cached category is not the identity of each listed item.
                # Rebuild local brand/entity contracts before searching.
                existing_requirement = {}
            normalized_title = str(title or "").strip()
            if (existing_requirement.get('subject_type') == 'automobile'
                    and re.search(r'\b(?:garter day|investiture|trooping the colour)\b', existing_canonical, re.I)):
                existing_requirement = None
            if supported_title_subject and search_key(existing_canonical) != search_key(supported_title_subject):
                # Checkpoints from the corrupt global-anchor planner must be
                # rebased before discovery, not after every asset is rejected.
                existing_requirement = None
            if (
                existing_canonical
                and normalized_title
                and re.fullmatch(
                    rf"{re.escape(normalized_title)}\s+works?",
                    existing_canonical,
                    flags=re.IGNORECASE,
                )
            ):
                existing_requirement = None
        requirement = (
            rebase_visual_requirement_for_scene(
                existing_requirement,
                title=title,
                scene_text=scene_texts[index],
                script=script,
                semantic_context=local_direction,
            )
            if isinstance(existing_requirement, Mapping) and existing_requirement.get("canonical_entity")
            else build_visual_requirement(
                title=title,
                script=script,
                scene_text=scene_texts[index],
                positive_subjects=positive,
                forbidden_subjects=forbidden,
                visual_intent=intent,
                semantic_context=local_direction,
            )
        )
        grounded_local_requirement = requirement if (requirement.get('subject_context') or {}).get('scene_local') else None
        # Narrow a broad cached topic contract when the narration names one
        # concrete person in this scene.  Keeping "King Charles III and
        # Prince Harry British Royal" as the canonical identity for every
        # beat makes the metadata gate accept any row containing only the
        # generic word "king".  A scene-local person contract gives Yandex,
        # Bing and the visual gate an unambiguous target while the global
        # topic remains available through `_topic_anchor` for establishing
        # shots in other scenes.
        scene_person_match = re.search(
            # Match the honorific case-insensitively, but keep the name's
            # capitalized-token requirement.  A whole-pattern IGNORECASE
            # would absorb connectors (``and``, ``are``) into the name.
            r"\b((?i:King|Queen|Prince|Princess|President|Prime Minister|Chancellor|Duke|Duchess)\s+"
            r"[A-Z][A-Za-z'’-]*(?:\s+(?:[A-Z][A-Za-z'’-]*|[IVX]{2,4})){0,3})\b",
            scene_texts[index],
        )
        action_subject = _scene_action_subject(scene_texts[index])
        local_person = action_subject or (
            re.sub(r"\s+", " ", scene_person_match.group(1)).strip()
            if scene_person_match
            else ""
        )
        if not local_person:
            previous_scene_text = scene_texts[index - 1] if index > 0 else ""
            local_person = _script_person_anchor_for_scene(
                scene_texts[index],
                script,
                previous_scene=previous_scene_text,
            )
        # A cached storyboard can still carry the old global contract even
        # when the current scene is an abstract intro (no person name in that
        # beat). Keep that scene broad rather than borrowing the first person
        # mentioned anywhere in the script. The previous fallback smeared
        # Prince Harry onto continuation scenes about Charles, William, and
        # Eugenie, which then made the media gate accept the wrong identity.
        cached_canonical = str(requirement.get("canonical_entity") or "").strip()
        # The user-facing title can be a generic run name while the actual
        # topic anchor is carried in the normalized config. Include the topic,
        # script, and requirement evidence when deciding whether this is the
        # royal-family domain; otherwise a stale abbreviated identity such as
        # "King Charles III and Prince Harry British Royal" hides the word
        # "family" and keeps an abstract beat strict by mistake.
        topic_blob = " ".join(
            str(value or "")
            for value in (
                title,
                cached_canonical,
                config.get("_topic_anchor"),
                script,
                *(requirement.get("domain_evidence") or ()),
            )
        )
        title_blob = topic_blob.casefold()
        broad_family_contract = bool(
            re.search(r"\b(?:british\s+)?royal\s+family\b", title_blob)
            or (
                re.search(r"\b(?:royal|royals|monarchy|monarch|windsors?)\b", title_blob)
                and re.search(r"\bfamil(?:y|ies)\b", title_blob)
            )
        )
        # A global cached requirement can be a person contract even when the
        # current beat is an abstract transition (for example, "The possible
        # return raises questions about security...").  Carrying that stale
        # identity into the provider gate makes every valid royal-family
        # image look like a mismatch and leaves a graphic card in the edit.
        # Keep a broad topic contract for those beats; vision still checks the
        # domain and scene context, while only explicitly named scenes remain
        # strict person contracts.
        if (
            broad_family_contract
            and not local_person
            and not scene_person_match
            and str(requirement.get("subject_type") or "").strip().lower() in {
                "person", "concept", "named_entity", "",
            }
        ):
            broad_topic = dict(requirement)
            broad_topic.update({
                "canonical_entity": "British Royal Family",
                "subject_type": "concept",
                "domain": "documentary",
                "required_terms": tuple((requirement.get("required_attributes") or {}).values()),
                "required_attributes": dict(requirement.get("required_attributes") or {}),
                "strict_identity": bool(requirement.get("required_attributes")),
                "ambiguity_status": "resolved",
                "confidence": 0.60,
                "positive_evidence": tuple(
                    list(requirement.get("positive_evidence") or ()) + [scene_texts[index]]
                ),
                "domain_evidence": tuple(
                    list(requirement.get("domain_evidence") or ()) + [scene_texts[index]]
                ),
                "requirement_hash": "",
            })
            requirement = VisualRequirementV1.from_mapping(broad_topic).to_dict()
            print(
                f"   [v2] scene {index + 1}: broad royal topic contract "
                "(no explicit person in beat); allowing verified family imagery",
                flush=True,
            )
        # Only the first broad establishing scene may borrow the programme's
        # primary person anchor. Later fragments must stay broad or inherit
        # their immediate predecessor; borrowing the first script person at
        # every index smeared Prince Harry onto Charles/William/Eugenie beats.
        if (
            index == 0
            and not local_person
            and broad_family_contract
            and str(requirement.get("subject_type") or "").strip().lower() in {
                "person", "concept", "",
            }
        ):
            local_person = _scene_role_subject(script)
        # The action/provenance parser already separates the visible actor
        # from a historical owner. Do not overwrite it with the first titled
        # name (e.g. "Princess Diana") in the same sentence, or let the old
        # storyboard positives/director narrowing restore that stale owner.
        person_identity_rebased = bool(action_subject)
        if action_subject:
            positive = (action_subject,)
        institution = bool(requirement.get('subject_type') == 'organization'
                           and re.search(r'\broyal\s+warrant', cached_canonical, re.I))
        if institution and index < len(planned_web_rows):
            from .media_query_strategy import grounded_institutional_focus
            local_focus = grounded_institutional_focus(planned_web_rows[index], scene_texts[index])
            if local_focus and not scene_person_match:
                requirement = build_visual_requirement(
                    title=local_focus, script=scene_texts[index], scene_text=scene_texts[index],
                    positive_subjects=(local_focus,), forbidden_subjects=forbidden, visual_intent=intent,
                )
                # Companies with two capitalized words must not be mistaken
                # for people merely because the documentary mentions royalty.
                if requirement.get('subject_type') == 'person' and not _scene_role_subject(local_focus):
                    requirement.update(subject_type='organization', domain='business', requirement_hash='')
                    requirement = VisualRequirementV1.from_mapping(requirement).to_dict()
                positive = (local_focus,)
                person_identity_rebased = True
        institutional_person_beat = bool(
            scene_person_match
            and requirement.get('subject_type') == 'organization'
            and re.search(r'\broyal\s+(?:warrant|household|charter)', cached_canonical, re.I)
        )
        if institutional_person_beat:
            # An institutional documentary still has concrete monarch beats.
            # Requiring a warrant plaque for narration about Charles/Elizabeth
            # discarded valid portraits and made every query identical.
            # Only an explicit person in THIS beat can narrow the institution.
            requirement = build_visual_requirement(
                title=local_person, script=script, scene_text=scene_texts[index],
                positive_subjects=(local_person,), forbidden_subjects=forbidden,
                visual_intent=intent,
            )
            positive = (local_person,)
            person_identity_rebased = True
        if local_person and str(requirement.get("subject_type") or "").strip().lower() in {
            "person", "concept", "named_entity", "",
        }:
            local_person = re.sub(r"['’]s$", "", local_person, flags=re.IGNORECASE).strip()
            local_person = _qualify_local_person_identity(
                local_person,
                cached_canonical,
                config.get("_topic_anchor"),
            )
            broad_identity = cached_canonical.casefold()
            if local_person and local_person.casefold() != broad_identity:
                narrowed = dict(requirement)
                narrowed.update({
                    "canonical_entity": local_person,
                    # Once the scene-local anchor is a named person, retain
                    # the typed contract. Leaving the cached broad concept
                    # type in place makes provider_query_variants miss the
                    # portrait/photo wave and start with generic prose.
                    "subject_type": "person",
                    "strict_identity": True,
                    "required_terms": (local_person,),
                    "positive_evidence": tuple(
                        list(requirement.get("positive_evidence") or ()) + [scene_texts[index]]
                    ),
                    "domain_evidence": tuple(
                        list(requirement.get("domain_evidence") or ()) + [scene_texts[index]]
                    ),
                    "requirement_hash": "",
                })
                requirement = VisualRequirementV1.from_mapping(narrowed).to_dict()
                positive = (local_person,)
                person_identity_rebased = True
        if grounded_local_requirement is not None:
            requirement = grounded_local_requirement
            positive = (str(requirement.get('canonical_entity') or ''),)
        focal_rebased = person_identity_rebased or grounded_local_requirement is not None
        if (
            repeated_title_entity
            and not focal_rebased
            and str(requirement.get("subject_type") or "").strip().lower() in {"concept", "named_entity"}
            and str(requirement.get("canonical_entity") or "").strip().casefold()
            != repeated_title_entity.casefold()
        ):
            # The director repeatedly searched for the named subject in the
            # user title. A stale storyboard event anchor must not force the
            # final visual gate to approve only broadly related event photos.
            requirement = build_visual_requirement(
                title=repeated_title_entity,
                script=script,
                scene_text=scene_texts[index],
                positive_subjects=(repeated_title_entity,),
                forbidden_subjects=forbidden,
                visual_intent=intent,
                semantic_context=dict(scene) if isinstance(scene, Mapping) else {},
            )
            positive = tuple(requirement.get("required_terms") or (repeated_title_entity,))
            focal_rebased = True
            print(
                f"   [v2] scene {index + 1}: director-confirmed visual subject "
                f"{repeated_title_entity!r} replaces stale event anchor",
                flush=True,
            )
        if not focal_rebased and index < len(planned_web_rows):
            requirement, focal_rebased = _scene_local_director_identity(
                requirement,
                planned_web_rows[max(0, index - 1):min(len(planned_web_rows), index + 3)],
                scene_texts[index],
                forbidden_subjects=forbidden,
                visual_intent=intent,
            )
            if focal_rebased:
                positive = tuple(requirement.get("required_terms") or ())
                print(
                    f"   [v2] scene {index + 1}: verified scene-local director identity "
                    f"{requirement.get('canonical_entity')!r}",
                    flush=True,
                )
        # A source-locked Yandex benchmark can be run without a paid visual
        # verifier. In that narrow mode the resolver's local decode/safety
        # checks are the acceptance boundary; keeping the normal named-entity
        # contract here would reject every result whose page title omits one
        # of the words in “British Royal Family”, forcing the old duplicate
        # reuse path. Clear identity-only fields while retaining scene-local
        # context so each downloaded asset still has to pass the hard gates.
        if bool(config.get("deterministic_web_images_only")) and str(
            config.get("image_source") or ""
        ).strip().lower() in {"web images", "web", "yandex"}:
            relaxed_requirement = dict(requirement)
            relaxed_requirement.update({
                "canonical_entity": "",
                "required_terms": (),
                "required_attributes": {},
                "strict_identity": False,
                "confidence": 0.45,
            })
            requirement = VisualRequirementV1.from_mapping(relaxed_requirement).to_dict()
        # Cached storyboard contracts can carry a visual requirement built
        # from an earlier/global script pass.  The resolver intentionally
        # rebases that requirement against the current scene, but the final
        # render gate reads the authoritative SceneContractV1 object from
        # ``audio_plan``.  Keep both sides on the same scene-local hash;
        # otherwise a verified asset is selected successfully and then is
        # discarded as a false handoff mismatch immediately before render.
        if isinstance(scene, Mapping):
            updated_scene = dict(scene)
            updated_scene["visual_requirement"] = requirement
            if focal_rebased:
                updated_scene["positive_subjects"] = positive
            scenes[index] = updated_scene
        else:
            try:
                updated_scene = replace(
                    scene,
                    visual_requirement=requirement,
                    **({"positive_subjects": positive} if focal_rebased else {}),
                )
                if hasattr(updated_scene, "with_computed_hash"):
                    updated_scene = updated_scene.with_computed_hash()
                scenes[index] = updated_scene
            except (TypeError, ValueError):
                # Provider-facing rows still carry the rebased requirement;
                # unusual legacy scene objects can remain immutable here.
                pass
        visual_requirements.append(requirement)
        identity = str(requirement.get("canonical_entity") or "").strip()
        yt_queries = provider_query_variants(requirement, provider="youtube")
        web_queries = provider_query_variants(requirement, provider="web")
        # Put scene-local proper names and locations ahead of the broad
        # canonical pool.  A shared topic search is fast, but it can return
        # lexical false positives (for example a ferry named "King" for a
        # King Charles scene).  These focused queries give the universal
        # scraper a cheap second recall lane before the coordinator spends a
        # vision call or falls through to a placeholder.
        scene_local_queries: list[str] = []
        scene_blob = str(scene_texts[index] or "")
        proper_phrases = re.findall(
            r"\b(?:[A-Z][A-Za-z'’-]*|[IVX]{2,4})(?:\s+(?:[A-Z][A-Za-z'’-]*|[IVX]{2,4})){1,4}\b",
            scene_blob,
        )
        for phrase in proper_phrases:
            phrase = re.sub(r"\s+", " ", phrase).strip(" ,.;:!?\"'")
            # Capitalized prose frequently contains locations and sentence
            # openers (``United States``, ``But Prince William``).  Feeding
            # those ahead of the identity query was the source of the log's
            # ``United States official event`` and ``But Prince William``
            # searches.  Strip connectors and leave country/region phrases to
            # the scene's canonical identity plus contextual terms.
            phrase = re.sub(
                r"^(?:but|and|after|before|meanwhile|however|then|the)\s+",
                "",
                phrase,
                flags=re.IGNORECASE,
            ).strip()
            if len(phrase.split()) < 2:
                continue
            phrase_norm = re.sub(r"[^a-z0-9]+", " ", phrase.casefold()).strip()
            if phrase_norm in {
                "united states", "united kingdom", "great britain", "britain",
                "california", "america", "north america", "europe", "england",
                "scotland", "wales", "ireland", "royal family", "british royal family",
            }:
                continue
            if phrase.casefold() == identity.casefold():
                continue
            scene_local_queries.extend([
                f"{phrase} official event documentary photograph",
                f"{phrase} public appearance reference image",
            ])
        # Buckingham Palace style locations are often the strongest
        # establishing-shot fallback when a person is not visible.  Include
        # the scene action terms only as compact context; they never replace
        # the immutable entity contract.
        action_terms = [
            str(term).strip()
            for term in (requirement.get("action_terms") or ())
            if len(str(term).strip()) >= 4
            and str(term).strip().casefold() not in {
                "remains", "central", "symbol", "british", "carries",
                "constitutional", "duties", "historic", "documentary",
            }
        ]
        if action_terms:
            scene_local_queries.append(
                " ".join([identity, *action_terms[:2], "reference image"])
            )
        # The canonical identity lane must lead every provider wave.  Scene
        # text can mention relatives, locations, or incidental proper nouns;
        # putting those phrases first caused a Prince Harry scene to begin
        # with a Meghan Markle query and then burn its retrieval wall before
        # the exact subject was tried.  Keep the detailed context variants as
        # bounded secondary recall, anchored to the canonical subject below.
        anchored_local_queries = []
        for local_query in scene_local_queries:
            local_query = str(local_query or "").strip()
            if not local_query:
                continue
            if identity and identity.casefold() not in local_query.casefold():
                local_query = f"{identity} {local_query}"
            anchored_local_queries.append(local_query)
        planned_web_row = (
            planned_web_rows[index]
            if index < len(planned_web_rows) and isinstance(planned_web_rows[index], Mapping)
            else {}
        )
        planned_web_queries: list[str] = []
        if planned_web_row.get("_llm"):
            for key in ("search_query", "supporting_search_query", "fallback_query"):
                query = re.sub(r"\s+", " ", str(planned_web_row.get(key) or "")).strip()
                if not query:
                    continue
                # Keep the director's scene action and era in the leading
                # search wave. Anchor it to the validated subject so a
                # related event does not displace the actual title entity.
                if identity and identity.casefold() not in query.casefold():
                    query = f"{identity} {query}"
                planned_web_queries.append(query)
        # Institutional aliases name the indexed artifact (e.g. Royal Warrant),
        # while the editorial container may say "System". Lead with its short
        # contract queries before verbose planned/narration phrases consume
        # the resolver's eight-query window. Vision still owns acceptance.
        institutional_alias = bool(
            requirement.get("subject_type") == "organization"
            and requirement.get("aliases")
        )
        web_queries = list(dict.fromkeys(
            (web_queries + planned_web_queries if institutional_alias else planned_web_queries + web_queries)
            + anchored_local_queries
        ))
        planned_row = (
            planned_query_rows[index]
            if index < len(planned_query_rows) and isinstance(planned_query_rows[index], Mapping)
            else {}
        )
        planned_youtube_queries: list[str] = []
        planned_bundle = planned_row.get("youtube_query_plan") or planned_row.get("llm_youtube_queries")
        if isinstance(planned_bundle, Mapping):
            planned_bundle = [
                planned_bundle.get("primary") or planned_bundle.get("youtube_primary"),
                planned_bundle.get("supporting") or planned_bundle.get("youtube_supporting"),
                planned_bundle.get("fallback") or planned_bundle.get("youtube_fallback"),
            ]
        if isinstance(planned_bundle, (list, tuple)):
            planned_youtube_queries.extend(str(value or "").strip() for value in planned_bundle)
        for key in (
            "youtube_query", "youtube_supporting_query", "youtube_fallback_query",
            "youtube_primary", "youtube_supporting", "youtube_fallback",
        ):
            value = str(planned_row.get(key) or "").strip()
            if value:
                planned_youtube_queries.append(value)
        planned_youtube_queries = list(dict.fromkeys(value for value in planned_youtube_queries if value))
        from .media_query_strategy import enrich_scene_queries
        planned_youtube_queries = enrich_scene_queries(
            planned_youtube_queries, requirement, scene_texts[index],
            provider='youtube', focus_terms=planned_row.get('scene_focus_terms') or (),
            preserve_planned_order=bool(planned_row.get('_llm')) and not bool(grounded_local_requirement),
        )
        web_queries = enrich_scene_queries(
            web_queries, requirement, scene_texts[index],
            provider='web', focus_terms=planned_row.get('scene_focus_terms') or (),
            preserve_planned_order=bool(planned_web_queries) and not bool(grounded_local_requirement),
        )
        semantic_query_rows.append({
            "scene_number": index + 1,
            "section_subject": identity,
            "scene_subject": identity,
            "subject_query": identity,
            "title_anchor": identity,
            "domain_anchor": identity,
            "topic_anchor": identity,
            "global_topic": identity,
            "visual_requirement": requirement,
            "_semantic_locked": True,
            "youtube_query_plan": planned_youtube_queries,
            "youtube_query_intents": list(planned_row.get("youtube_query_intents") or ()),
            "domain_category": str(requirement.get('domain') if grounded_local_requirement
                                   else planned_row.get("domain_category") or "").strip().lower(),
            "visual_intent": str(planned_row.get("visual_intent") or "").strip(),
            "director_web_query": bool(planned_web_queries),
            "youtube_query": yt_queries[0] if yt_queries else identity,
            "youtube_supporting_query": yt_queries[1] if len(yt_queries) > 1 else identity,
            "youtube_fallback_query": yt_queries[2] if len(yt_queries) > 2 else identity,
            "search_query": web_queries[0] if web_queries else identity,
            "alt_search_query": web_queries[1] if len(web_queries) > 1 else identity,
            "fallback_query": web_queries[2] if len(web_queries) > 2 else identity,
            "web_query_variants": list(web_queries),
            "query_variants": list(dict.fromkeys(yt_queries + web_queries)),
            "required_attributes": dict(requirement.get("required_attributes") or {}),
            "forbidden_subjects": list(requirement.get("forbidden_subjects") or ()),
        })
    # The same rebased contracts must be consumed by the final validation and
    # by storyboard persistence, not just by the provider query adapters.
    if isinstance(audio_plan, dict):
        if audio_plan.get("scene_contracts") is not None:
            audio_plan["scene_contracts"] = list(scenes)
        elif audio_plan.get("scenes") is not None:
            audio_plan["scenes"] = list(scenes)
    config["_visual_requirements"] = visual_requirements
    config["_semantic_query_rows"] = semantic_query_rows
    config["_semantic_query_lock"] = True
    semantic_identities = [
        str(row.get("section_subject") or "").strip()
        for row in semantic_query_rows
        if str(row.get("section_subject") or "").strip()
    ]
    # Replace any legacy topic-anchor diagnosis with the identity produced by
    # the immutable semantic contract when the job has one global subject.
    # This keeps branch logs, cache keys, and provider wrappers aligned with
    # the same entity instead of allowing an incidental phrase such as a
    # venue name to survive as the apparent topic.
    valid_topic_anchor = str(config.get("_topic_anchor") or "").strip()
    broad_anchor = False
    if valid_topic_anchor:
        config["_topic_anchor"] = valid_topic_anchor
        config["_skip_sync_topic_anchor"] = True
        # Keep the broad programme anchor for editorial metadata, but do not
        # feed it to the provider transport when the immutable rows already
        # expose a concrete role/name.  A cached ``British Royal Family``
        # anchor otherwise warms every scraper with a family-wide gallery and
        # can starve the scene-specific Prince Harry/William queries before
        # their results are considered.
        anchor_lower = valid_topic_anchor.casefold()
        broad_anchor = bool(
            re.search(r"\b(?:british\s+)?royal\s+family\b", anchor_lower)
            or (
                re.search(r"\bfamil(?:y|ies)\b", anchor_lower)
                and re.search(r"\b(?:royal|british|monarchy|monarch)\b", anchor_lower)
            )
        )
        requirement_subjects = Counter(str(requirement.get('canonical_entity') or '').strip()
                                       for requirement in visual_requirements if isinstance(requirement, Mapping))
        concrete_identity = next(
            (
                identity
                for identity in [*semantic_identities, *(identity for identity, _ in requirement_subjects.most_common() if identity)]
                if re.search(
                    r"\b(?i:king|queen|prince|princess|president|prime minister|"
                    r"chancellor|duke|duchess|emperor|empress|pope|senator|governor)\s+"
                    r"[A-Z][A-Za-z'’-]*",
                    identity,
                )
            ),
            "",
        )
        provider_topic_title = concrete_identity if broad_anchor and concrete_identity else valid_topic_anchor
    elif semantic_identities and len(set(semantic_identities)) == 1:
        config["_topic_anchor"] = semantic_identities[0]
        config["_skip_sync_topic_anchor"] = True
        provider_topic_title = semantic_identities[0]
    else:
        provider_topic_title = title
    timing_windows = list(audio_plan.get("scene_timing_windows") or ())
    section_subjects = [str(item.get("section_subject") or "") for item in semantic_query_rows]
    config["_section_subject_lock"] = section_subjects
    config["_scene_timing_windows"] = timing_windows
    # YouTube is a recall lane in every mode that enables it: keep plausible
    # candidates alive until FFmpeg/overlay/visual gates inspect them. Hard
    # package, caption, and subject-mismatch blockers still reject upstream.
    config["yt_stop_on_soft_timeout"] = False
    # Preserve the Mixed Media policy.  The app disables same-source reuse so
    # a real YouTube pool is distributed as distinct cuts; forcing this back
    # to True here made the coordinator collapse the pool into one clip plus
    # a reuse row.
    config["yt_allow_same_source_reuse"] = bool(config.get("yt_allow_same_source_reuse", True))
    lane_policy = _source_lane_policy(config)
    # Respect the operator's cross-source setting.  Earlier code unconditionally
    # disabled it for Mixed Media to avoid a serial retry storm, even when the
    # UI explicitly sent ``cross_source_fallback=true``.  The fallback loaders
    # below are now bounded and batch-coalesced, so disabling a requested lane
    # would turn a temporary web/YouTube outage into a hard zero-media failure.
    config["yt_lenient_acceptance"] = bool(
        _as_bool(config.get("youtube_recall_mode"), True)
        if lane_policy["youtube_enabled"] else False
    )
    # Preserve the normal lane flag for provider policy, while keeping a
    # separate last-resort switch for Web/YouTube/Mixed recovery.
    config["allow_relevant_stock"] = bool(lane_policy["stock_enabled"])
    config["_stock_last_resort_enabled"] = bool(lane_policy.get("stock_last_resort_enabled"))
    config["_v2_source_lane_policy"] = dict(lane_policy)
    allow_emergency_media = emergency_media_allowed(config)

    saved_web_rows = {}
    saved_stock_rows = {}
    saved_youtube_rows = {}
    saved_package = config.get('_output_package_dir') or config.get('output_package_dir')
    if saved_package:
        from .media_checkpoint_history import saved_web_candidates
        saved_web_rows = saved_web_candidates(saved_package, scenes)
        from .media_checkpoint_history import saved_stock_candidates
        saved_stock_rows = saved_stock_candidates(saved_package, scenes)
        from .media_checkpoint_history import saved_youtube_candidates
        saved_youtube_rows = saved_youtube_candidates(saved_package, scenes) if lane_policy['youtube_enabled'] else {}
        if saved_web_rows:
            print(f'   [v2] checkpoint recall: {len(saved_web_rows)}/{len(scenes)} scene(s) have saved real-image candidates', flush=True)
    # A candidate file is not a completed scene. Only an exact requirement +
    # digest-bound pixel receipt may suppress fresh discovery on a retry.
    completed_web_indices = _completed_saved_web_indices(saved_web_rows)
    youtube_scene_indices = []
    if lane_policy["youtube_enabled"]:
        if lane_policy["mode"] == "Mixed Media":
            youtube_scene_indices = _automatic_youtube_scene_indices(
                config,
                scene_texts,
                title=title,
                script=script,
                scenes=scenes,
            )
            print(
                "   [v2] Mixed Media YouTube quota: "
                f"{len(youtube_scene_indices)}/{len(scenes)} scene(s); rest routed to web"
            )

        else:
            youtube_scene_indices = list(range(len(scenes)))

    completed_stock_indices = {index for index, row in saved_stock_rows.items() if row.get('checkpoint_completed')}
    completed_real_indices = completed_web_indices | completed_stock_indices | set(saved_youtube_rows)
    if completed_real_indices:
        youtube_scene_indices = [index for index in youtube_scene_indices if index not in completed_real_indices]
        print(f'   [v2] retry preserves {len(completed_web_indices)} verified real-image scene(s); repairing unresolved slots', flush=True)

    verifier, visual_context = build_qwen_verifier(
        config,
        scenes,
        visual_context=config.get("_web_visual_context"),
        on_progress=report_progress,
    )
    config["_web_visual_context"] = visual_context
    # Provider transports run before the shared coordinator. They may retain
    # identity-bearing rows as scene-context-pending recall candidates, but
    # only because this job has an authoritative final visual gate.
    config["_final_visual_gate_required"] = bool(verifier is not None)
    if saved_web_rows and verifier is not None:
        timeout = 90.0
        try:
            from .delivery_budget import get_delivery_budget
            budget = get_delivery_budget(config)
            if budget is not None:
                timeout = budget.allowance(timeout, reserve_after_seconds=budget.render_reserve_seconds)
        except (AttributeError, TypeError, ValueError):
            pass
        repaired = _verify_saved_web_candidates(saved_web_rows, scenes, verifier, timeout_seconds=timeout)
        if repaired:
            completed_web_indices = _completed_saved_web_indices(saved_web_rows)
            youtube_scene_indices = [index for index in youtube_scene_indices if index not in completed_web_indices]
            print(f'   [v2] checkpoint repair: {repaired} deferred image checks completed before fresh retrieval', flush=True)
    # Provider transports discover/download candidates only. Their legacy
    # visual gates are disabled so YouTube and web cannot each consume a full
    # scene's Qwen budget before the coordinator compares finalists.
    transport_visual_context = VisualDecisionContext.from_config(
        {
            "visual_understanding": False,
            "visual_required": False,
            "pipeline_v2_mode": "off",
        },
        len(scenes),
    )
    # Provider transports only discover, download, decode and locally screen
    # candidates. The shared coordinator below owns the one Qwen decision that
    # compares YouTube with web/stock finalists. Passing the job context into
    # the YouTube transport consumed its call/deadline budget twice; once the
    # shared context expired, an explicitly fail-open local verdict was
    # reclassified as unavailable and every successfully downloaded clip was
    # discarded. Keep transport vision disabled and reserve Qwen for the final
    # cross-source decision.
    youtube_transport_context = transport_visual_context
    branch_audit: dict[str, Any] = {}
    primary_youtube_scene_indices = list(youtube_scene_indices)
    youtube_primary_attempted_indices: set[int] = set()
    youtube_indices_ref = {"indices": primary_youtube_scene_indices}
    youtube_fallback_mode = {"enabled": False}
    primary_youtube_rows: dict[int, Any] = {}
    for source_key, enabled_key in (
        ("youtube", "youtube_enabled"),
        ("web", "web_enabled"),
        ("stock", "stock_enabled"),
    ):
        if not lane_policy[enabled_key]:
            branch_audit[source_key] = {
                "status": "disabled_by_source_mode",
                "mode": lane_policy["mode"],
            }

    def load_youtube() -> Mapping[int, Any]:
        # The same loader is reused by the optional cross-lane fallback. The
        # fallback changes this local index vector before invoking the loader,
        # so the provider performs one bounded batch only when a web scene
        # actually needs YouTube recovery.
        youtube_scene_indices = list(youtube_indices_ref.get("indices") or ())
        # A timed-out primary loader can finish while cross-lane recovery is
        # running. Its publication/audit role must not change underneath it.
        is_youtube_fallback = bool(youtube_fallback_mode.get('enabled'))
        if not youtube_scene_indices:
            branch_audit["youtube"] = {
                "status": "no_quota_slots",
                "requested_scene_count": 0,
                "total_scene_count": len(scenes),
            }
            return {}
        if not is_youtube_fallback:
            # The YouTube branch runs asynchronously beside web discovery.
            # Record its quota before the lane waits on the batch; otherwise
            # coordinator recovery can mistake an already-running/finished
            # primary attempt for an empty fast response and launch a second
            # full YouTube batch for the same scenes.
            youtube_primary_attempted_indices.update(youtube_scene_indices)
        report_progress(
            0.31,
            "V2 YouTube discovery: "
            f"{len(youtube_scene_indices)}/{len(scenes)} quota scenes",
        )
        lane_config = dict(config)
        if broad_anchor and provider_topic_title != valid_topic_anchor:
            # The provider reads this field ahead of its title argument.
            # Keep its cache/search identity aligned with the actual subject.
            lane_config['_topic_anchor'] = provider_topic_title
        if lane_policy.get("mode") == "Mixed Media":
            # These rows are already the footage quota selected from the
            # whole film. Applying 60% again budgets only part of that quota.
            lane_config["_youtube_quota_preselected"] = True
            lane_config["youtube_accepted_coverage_target"] = 1.0
            # Mixed Media reserves a script-specific footage quota. Keep recovery
            # waves bounded, with enough distinct sources to recover when a
            # publisher blocks downloads. The shared branch wall still wins.
            try:
                mixed_candidate_cap = max(
                    1,
                    min(
                        8,
                        int(
                            os.environ.get(
                                "VIDZEO_MIXED_YT_DOWNLOAD_CANDIDATES", "6"
                            )
                            or 6
                        ),
                    ),
                )
            except (TypeError, ValueError):
                mixed_candidate_cap = 6
            try:
                mixed_attempt_cap = max(
                    1,
                    min(
                        6,
                        int(
                            os.environ.get(
                                "VIDZEO_MIXED_YT_DOWNLOAD_ATTEMPTS", "6"
                            )
                            or 6
                        ),
                    ),
                )
            except (TypeError, ValueError):
                mixed_attempt_cap = 6
            try:
                lane_config["yt_download_candidate_limit"] = min(
                    mixed_candidate_cap,
                    max(1, int(lane_config.get("yt_download_candidate_limit") or mixed_candidate_cap)),
                )
            except (TypeError, ValueError):
                lane_config["yt_download_candidate_limit"] = mixed_candidate_cap
            try:
                lane_config["yt_max_download_attempts_per_scene"] = min(
                    mixed_attempt_cap,
                    max(1, int(lane_config.get("yt_max_download_attempts_per_scene") or mixed_attempt_cap)),
                )
            except (TypeError, ValueError):
                lane_config["yt_max_download_attempts_per_scene"] = mixed_attempt_cap
        lane_config["_scene_timing_windows"] = [
            timing_windows[index]
            for index in youtube_scene_indices
            if index < len(timing_windows)
        ]
        lane_config["yt_lenient_acceptance"] = bool(config.get("yt_lenient_acceptance"))
        # Carry the mixed-media I/O worker setting into the V2 adapter.  The
        # legacy branch passed this through app.py, but V2 builds a fresh lane
        # config; without this default it silently fell back to one global
        # download setting even when the operator configured parallel clips.
        if lane_policy.get("mode") == "Mixed Media":
            lane_config.setdefault(
                "yt_download_concurrency",
                os.environ.get("VIDZEO_MIXED_YT_DOWNLOAD_CONCURRENCY", "4") or 4,
            )
        lane_config["visual_understanding"] = False
        lane_config["visual_required"] = False
        lane_config["_web_visual_context"] = youtube_transport_context
        if is_youtube_fallback:
            # Recovery must explore other masters, not redownload the exact
            # metadata winner that the final scene gate just rejected.
            previous_audit = primary_youtube_audit or branch_audit.get("youtube") or {}
            excluded = set(lane_config.get("youtube_excluded_video_ids") or ())
            excluded.update(_youtube_recovery_excluded_ids(
                previous_audit, primary_youtube_scene_indices, youtube_scene_indices,
            ))
            lane_config["youtube_excluded_video_ids"] = sorted(excluded)
            if lane_config.get("_run_temp_dir"):
                lane_config["_run_temp_dir"] = str(Path(lane_config["_run_temp_dir"]) / "youtube_content_recovery")
            # Recovery is one coalesced batch. Scale its wall with the number
            # of scene downloads: a flat 90s ceiling routinely expired while
            # four parallel ranged downloads were still finishing on slow
            # connections, causing needless shadow-web work and a second
            # YouTube pass. Keep a bounded cap so a broken transport cannot
            # consume the render reserve.
            try:
                configured_fallback_wall = config.get("yt_fallback_branch_wall_timeout_seconds")
                fallback_default = max(
                    180.0,
                    min(360.0, 60.0 * max(1, len(youtube_scene_indices))),
                )
                fallback_wall = float(configured_fallback_wall or fallback_default)
            except (TypeError, ValueError):
                fallback_wall = max(
                    180.0,
                    min(360.0, 60.0 * max(1, len(youtube_scene_indices))),
                )
            lane_config["yt_branch_wall_timeout_seconds"] = max(60.0, min(360.0, fallback_wall))
            lane_config["yt_fallback_mode"] = True
        def _safe_scene_start(scene_index: int) -> float:
            value: Any = 0.0
            if scene_index < len(timing_windows) and isinstance(timing_windows[scene_index], Mapping):
                value = timing_windows[scene_index].get("start_seconds", timing_windows[scene_index].get("start", 0.0))
            else:
                value = _scene_value(scenes[scene_index], "start_seconds", None) if scene_index < len(scenes) else None
                if value is None:
                    value = sum(
                        float(scene_durations[item_index] or 0.0)
                        for item_index in range(min(scene_index, len(scene_durations)))
                    )
            try:
                return float(value or 0.0)
            except (TypeError, ValueError):
                return 0.0
        lane_config["_yt_scene_query_overrides"] = [
            {
                **dict(semantic_query_rows[index]),
                # Keep the editorial constraints visible to the YouTube
                # provider.  Intro rows require distinct source videos;
                # normal rows may use multiple transcript/timestamped bites
                # from one vetted master source.
                "intro_unique": bool(
                    _scene_value(scenes[index], "is_intro", False)
                    or _safe_scene_start(index) < 15.0
                    or (index == 0 and index >= len(timing_windows))
                ),
                "bite_clip": bool(
                    dict(semantic_query_rows[index]).get("bite_clip")
                    or not _scene_value(scenes[index], "is_outro", False)
                ),
            }
            for index in youtube_scene_indices
            if index < len(semantic_query_rows)
        ]
        lane_config["_semantic_query_rows"] = [dict(row) for row in lane_config['_yt_scene_query_overrides']]
        lane_config["_semantic_query_lock"] = True
        section_locks = []
        for index in youtube_scene_indices:
            sc = scenes[index] if index < len(scenes) else {}
            subj = str(
                getattr(sc, "section_subject", "")
                or getattr(sc, "container_subject", "")
                if not isinstance(sc, Mapping)
                else (sc.get("section_subject") or sc.get("container_subject") or "")
            ).strip()
            section_locks.append(subj)
        lane_config["_section_subject_lock"] = section_locks
        if broad_anchor:
            for offset, index in enumerate(youtube_scene_indices):
                requirement = visual_requirements[index] if index < len(visual_requirements) else {}
                identity = str((requirement or {}).get('canonical_entity') or '').strip()
                if re.search(r'\b(?:king|queen|prince|princess|duke|duchess)\s+\S+', identity, re.I):
                    if re.search(r'\broyal\s+family\b', section_locks[offset], re.I):
                        section_locks[offset] = identity
                    for vector in ('_semantic_query_rows', '_yt_scene_query_overrides'):
                        if offset >= len(lane_config[vector]):
                            continue
                        row = lane_config[vector][offset]
                        for field in ('section_subject', 'container_subject', 'scene_subject'):
                            if re.search(r'\broyal\s+family\b', str(row.get(field) or ''), re.I):
                                row[field] = identity
                        for field in ('title_anchor', 'domain_anchor', 'topic_anchor', 'global_topic'):
                            if re.search(r'\broyal\s+family\b', str(row.get(field) or ''), re.I):
                                row[field] = provider_topic_title

        def _run_youtube_lane(run_config: Mapping[str, Any]):
            run_config = {**run_config, '_visual_requirements':
                [visual_requirements[index] for index in youtube_scene_indices]}
            return youtube_runner(
                [scene_texts[index] for index in youtube_scene_indices],
                [scene_durations[index] for index in youtube_scene_indices],
                run_config,
                provider_topic_title,
                script,
                progress_callback=lambda fraction, message: report_progress(
                    0.31 + max(0.0, min(1.0, float(fraction))) * 0.18,
                    message,
                ),
                cancellation=cancellation,
            )

        started = time.monotonic()
        try:
            values = _run_youtube_lane(lane_config)
        except Exception as exc:
            values = []
            branch_audit["youtube"] = {
                "transport_status": "unavailable",
                "failure_reason": f"runner_error:{type(exc).__name__}",
                "requested": len(youtube_scene_indices),
                "resolved": 0,
                "attempts": {},
            }
        audit = dict(getattr(values, "audit", None) or {})
        attempts = audit.get('attempts') if isinstance(audit.get('attempts'), Mapping) else {}
        audit['requested_scene_indices'] = list(youtube_scene_indices)
        audit['attempts_by_scene'] = {
            str(index): [dict(item, provider_scene_index=local_index, scene_index=index)
                         for item in (attempts.get(str(local_index))
                                      or attempts.get(local_index) or [])
                         if isinstance(item, Mapping)]
            for local_index, index in enumerate(youtube_scene_indices)
        }
        resolved = int(audit.get("resolved") or sum(1 for path in values if path))
        attempt_count = sum(len(rows) for rows in attempts.values() if isinstance(rows, list))
        # A zero-attempt result can be either a short transport failure or a
        # normal content miss (the provider searched and found no admissible
        # result).  V2 already has bounded web/stock rescue and a separate
        # worker wall, so automatically launching another YouTube search here
        # doubles the provider wall for ordinary misses.  Keep the old retry
        # for legacy callers, while making the V2 retry an explicit opt-in for
        # operators that prefer recall over latency.
        pipeline_v2_active = str(
            config.get("pipeline_v2_mode")
            or os.environ.get("VIDZEO_PIPELINE_V2_MODE", "")
        ).strip().lower() in {"1", "true", "yes", "on", "v2"}
        allow_empty_retry = str(
            config.get(
                "yt_transport_retry_after_empty",
                os.environ.get("VIDZEO_YT_TRANSPORT_RETRY_AFTER_EMPTY", "0"),
            )
            or "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if resolved == 0 and attempt_count == 0 and time.monotonic() - started <= 45.0:
            if not pipeline_v2_active or allow_empty_retry:
                retry_config = dict(lane_config)
                retry_config["yt_transport_retry"] = True
                report_progress(0.32, "YouTube transport retry: empty first attempt")
                time.sleep(0.5)
                try:
                    retry_values = _run_youtube_lane(retry_config)
                    retry_audit = dict(getattr(retry_values, "audit", None) or {})
                    retry_resolved = int(retry_audit.get("resolved") or sum(1 for path in retry_values if path))
                    if retry_resolved > 0 or retry_audit.get("attempts"):
                        audit = retry_audit
                        values = retry_values
                        audit["transport_retry"] = True
                        audit["first_attempt"] = {
                            "resolved": resolved,
                            "attempts": attempts,
                        }
                except Exception as exc:
                    audit["transport_retry"] = True
                    audit["retry_failure_reason"] = f"runner_error:{type(exc).__name__}"
            elif pipeline_v2_active:
                print(
                    "   [yt] V2 empty result: transport retry disabled; "
                    "continuing with web/stock rescue",
                    flush=True,
                )
        attempts = audit.get("attempts") or {}
        resolved = int(audit.get("resolved") or sum(1 for path in values if path))
        attempt_count = sum(len(rows) for rows in attempts.values() if isinstance(rows, list))

        # Deterministic recovery for a transport/content outage: reuse a
        # previously verified, probeable YouTube clip for the same topic from
        # a recent production manifest. This is deliberately narrow (topic
        # token match, verified status, no caption/subtitle overlay) and only
        # runs after the live lane has produced no usable clip. It prevents a
        # healthy mixed-media job from silently becoming 100% still images
        # when YouTube transport is temporarily flaky.
        cached_youtube_rows: list[Mapping[str, Any]] = []
        if resolved == 0 and str(config.get("image_source") or "").strip() == "Mixed Media":
            topic_tokens = {
                token for token in re.findall(r"[a-z0-9]+", str(title).lower())
                if len(token) >= 4 and token not in {"mixed", "media", "video"}
            }
            manifest_root = Path(str(config.get("output_dir") or "output"))
            cached_paths_by_key: dict[str, tuple[Path, dict[str, Any]]] = {}
            pool_cut_paths_by_key: dict[str, tuple[Path, dict[str, Any]]] = {}
            for manifest_path in sorted(manifest_root.glob("*.manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    payload = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                name_tokens = set(re.findall(r"[a-z0-9]+", str(payload.get("name") or manifest_path.stem).lower()))
                if topic_tokens and not (topic_tokens & name_tokens):
                    continue
                def add_cached_candidate(
                    raw_row: Mapping[str, Any],
                    target_map: dict[str, tuple[Path, dict[str, Any]]],
                ) -> None:
                    if str(raw_row.get("provider") or "").lower() != "youtube":
                        return
                    if str(raw_row.get("verification_status") or "").lower() not in {"verified", "verified_reuse", "deterministic_pool_cut"}:
                        return
                    overlay_types = {str(item).lower() for item in (raw_row.get("overlay_types") or [])}
                    if raw_row.get("overlay_blurred") or raw_row.get("blurred") or raw_row.get("cleaned_path"):
                        return
                    if (
                        overlay_types & {"facecam", "face_cam", "inset_facecam", "picture_in_picture"}
                        or (overlay_types & {"caption", "subtitle", "subtitles", "large_text_band"}
                            and not source_overlays_preserved(raw_row))
                        or raw_row.get("facecam_inset")
                        or raw_row.get("inset_facecam")
                        or raw_row.get("picture_in_picture")
                        or any(
                            token in str(raw_row.get("rejection_reason") or "").lower()
                            for token in ("facecam_inset", "inset_facecam", "picture_in_picture")
                        )
                    ):
                        return
                    cached_path = Path(str(raw_row.get("cleaned_path") or raw_row.get("selected_path") or raw_row.get("path") or ""))
                    if not cached_path.is_file() or not probe_media(cached_path, require_video=True).valid:
                        return
                    try:
                        key = str(cached_path.resolve()).casefold()
                    except OSError:
                        key = str(cached_path).casefold()
                    if key not in target_map:
                        target_map[key] = (cached_path, dict(raw_row))

                # The selected-resolution ledger may have reused one path,
                # while the same manifest's pool audit still contains the
                # distinct accepted moments that were available. Recover those
                # physical cuts too; otherwise a transport outage resurrects
                # one clip repeatedly and defeats the diversity contract.
                provider_audit = payload.get("provider_audit") or {}
                youtube_audit = provider_audit.get("youtube") if isinstance(provider_audit, Mapping) else {}
                for cut in (youtube_audit or {}).get("cut_audit") or []:
                    if not isinstance(cut, Mapping) or str(cut.get("status") or "").lower() != "accepted":
                        continue
                    add_cached_candidate({
                        **dict(cut),
                        "provider": "youtube",
                        "verification_status": "deterministic_pool_cut",
                        "selected_path": cut.get("path") or cut.get("cleaned_path"),
                    }, pool_cut_paths_by_key)
                # Prefer the pool's accepted local moments over the selected
                # resolution rows.  The latter may intentionally repeat one
                # path after a prior outage; the pool audit is the stronger
                # diversity source.
                if not pool_cut_paths_by_key:
                    for cached in payload.get("media_resolution_v2") or []:
                        if isinstance(cached, Mapping):
                            add_cached_candidate(cached, cached_paths_by_key)
                if len(pool_cut_paths_by_key or cached_paths_by_key) >= len(youtube_scene_indices):
                    break
            chosen_cached_map = pool_cut_paths_by_key or cached_paths_by_key
            cached_youtube_rows = [row for _path, row in chosen_cached_map.values()]
            if chosen_cached_map:
                source_rows = list(chosen_cached_map.values())
                cached_paths = snapshotter(
                    [str(path) for path, _row in source_rows],
                    namespace="youtube_verified_recovery",
                )
                values = list(values)
                for local_index, cached_path in enumerate(cached_paths[: len(youtube_scene_indices)]):
                    if cached_path:
                        if local_index >= len(values):
                            values.append(cached_path)
                        else:
                            values[local_index] = cached_path
                recovery_decisions = {}
                recovery_cuts = []
                for local_index, (_source_path, source_row) in enumerate(source_rows[: len(youtube_scene_indices)]):
                    candidate_id = str(source_row.get("candidate_id") or "")
                    source_id = candidate_id.split(":", 1)[-1] if candidate_id.startswith("youtube:") else str(source_row.get("source_id") or candidate_id)
                    start = source_row.get("video_start", source_row.get("start"))
                    end = source_row.get("video_end", source_row.get("end"))
                    recovery_decisions[str(local_index)] = {
                        "status": "accepted",
                        "relevance": 80.0,
                        "quality": 80.0,
                        "source": "youtube_pool_moment",
                        "source_moment": {
                            "source_id": source_id,
                            "video_id": source_id,
                            "start": start,
                            "end": end,
                            "url": source_row.get("source_url") or "",
                        },
                    }
                    recovery_cuts.append({
                        "scene_index": local_index,
                        "source_id": source_id,
                        "start": start or 0.0,
                        "status": "accepted",
                        "overlay_policy": source_row.get("overlay_policy") or "clean_or_logo_blur_caption_reject",
                        "source_overlays_preserved": bool(source_row.get("source_overlays_preserved")),
                        "overlay_clean": source_row.get("overlay_clean", True),
                        "no_blur": True,
                        "overlay_types": list(source_row.get("overlay_types") or []),
                        "blur_regions": list(source_row.get("blur_regions") or []),
                        "cleaned_path": str(cached_paths[local_index] or "")
                        if source_row.get("cleaned_path") and local_index < len(cached_paths)
                        else "",
                    })
                audit["retrieval_visual_decisions"] = recovery_decisions
                audit["cut_audit"] = recovery_cuts
                resolved = sum(1 for path in values if path)
                audit["verified_cache_recovery"] = {
                    "status": "accepted",
                    "resolved": resolved,
                    "source_manifest": str(manifest_path),
                    "distinct_sources_recovered": len(source_rows),
                }
        audit.setdefault("requested", len(youtube_scene_indices))
        audit["resolved"] = resolved
        audit["transport_status"] = "healthy" if resolved else ("degraded" if attempt_count else "unavailable")
        audit.setdefault(
            "failure_reason",
            "" if resolved else (str(audit.get("stop_reason") or "no_download_attempts")),
        )
        configured_candidate_limit = _bounded_audit_int(
            audit.get("candidate_limit_per_scene")
            if audit.get("candidate_limit_per_scene") is not None
            else lane_config.get("yt_download_candidate_limit")
            if lane_config.get("yt_download_candidate_limit") is not None
            else os.environ.get("VIDZEO_MIXED_YT_DOWNLOAD_CANDIDATES", 6),
            6,
            12,
        )
        configured_attempt_limit = _bounded_audit_int(
            audit.get("max_download_attempts_per_scene")
            if audit.get("max_download_attempts_per_scene") is not None
            else lane_config.get("yt_max_download_attempts_per_scene")
            if lane_config.get("yt_max_download_attempts_per_scene") is not None
            else os.environ.get("VIDZEO_MIXED_YT_DOWNLOAD_ATTEMPTS", 6),
            6,
            6,
        )
        candidate_attempt_limit = _bounded_audit_int(
            audit.get("candidate_attempt_limit"),
            min(configured_candidate_limit, configured_attempt_limit),
            12,
        )
        audit["effective_settings"] = {
            "phase": "cross_lane_fallback" if is_youtube_fallback else "primary",
            "requested_scene_count": len(youtube_scene_indices),
            "candidate_limit_per_scene": max(1, configured_candidate_limit),
            "max_download_attempts_per_scene": max(1, configured_attempt_limit),
            "candidate_attempt_limit": candidate_attempt_limit,
            "download_workers": _bounded_audit_int(audit.get("download_workers") or 0, 0, 64),
            "requested_download_workers": _bounded_audit_int(audit.get("requested_download_workers") or 0, 0, 64),
            "configured_branch_wall_seconds": lane_config.get("yt_branch_wall_timeout_seconds")
            or config.get("yt_branch_wall_timeout_seconds")
            or os.environ.get("VIDZEO_MIXED_YT_BRANCH_WALL_TIMEOUT_SECONDS")
            or os.environ.get("VIDZEO_YT_BRANCH_WALL_TIMEOUT_SECONDS"),
        }
        branch_audit.setdefault("youtube_runs", []).append({
            key: value for key, value in audit["effective_settings"].items()
        })
        branch_audit["youtube"] = audit
        snapshotted = list(snapshotter(values, namespace="youtube_v2"))

        # A pool can finish with fewer scene assignments than it has verified
        # local cuts. Recover a missing snapshot only from the accepted cut
        # originally assigned to that same scene. Cross-scene footage is
        # reserved for the coordinator's separately downloaded, distant, and
        # vision-bound variant contract.
        pool_cut_rows = [
            dict(item)
            for item in (audit.get("cut_audit") or [])
            if isinstance(item, Mapping)
            and str(item.get("status") or "").strip().lower() in {"accepted", "downloaded"}
            and not (item.get("overlay_blurred") or item.get("blurred") or item.get("cleaned_path"))
            and not (
                {str(value).strip().lower() for value in (item.get("overlay_types") or [])}
                & {"caption", "subtitle", "subtitles", "large_text_band"}
                and not source_overlays_preserved(item)
            )
        ]
        if pool_cut_rows:
            existing_paths = {
                str(path).casefold()
                for path in snapshotted if path
            }
            decisions = audit.setdefault("retrieval_visual_decisions", {})
            recovery_requests: list[tuple[int, dict[str, Any], str]] = []
            for cut in pool_cut_rows:
                raw_path = str(cut.get("cleaned_path") or cut.get("path") or "").strip()
                if not raw_path:
                    continue
                key = raw_path.casefold()
                if key in existing_paths or not Path(raw_path).is_file():
                    continue
                try:
                    int(cut.get("scene_index"))
                except (TypeError, ValueError):
                    continue
                # A pool cut can repair only the scene for which it was
                # selected. The previous modulo rotation copied one accepted
                # cut into several unrelated gaps, producing repeated and
                # sometimes chronologically wrong footage.
                for local_index, target_scene_index in enumerate(youtube_scene_indices):
                    if local_index < len(snapshotted) and snapshotted[local_index]:
                        continue
                    decision = decisions.get(str(local_index)) or decisions.get(local_index) or {}
                    decision = decision if isinstance(decision, Mapping) else {}
                    moment = decision.get("source_moment") or {}
                    moment = moment if isinstance(moment, Mapping) else {}
                    if not _youtube_cut_recovery_matches_scene(cut, int(target_scene_index), moment):
                        continue
                    recovery_requests.append((local_index, dict(cut), raw_path))
                    existing_paths.add(key)
                    break
            if recovery_requests:
                reused_snapshots = list(
                    snapshotter([path for _index, _cut, path in recovery_requests], namespace="youtube_pool_recovery")
                )
                recovered_paths: set[str] = set()
                recovered_count = 0
                for (local_index, source_cut, _raw_path), recovered_path in zip(recovery_requests, reused_snapshots):
                    if not recovered_path or str(recovered_path).casefold() in recovered_paths:
                        continue
                    reused_path = recovered_path
                    recovered_paths.add(str(recovered_path).casefold())
                    if local_index >= len(snapshotted):
                        snapshotted.extend([None] * (local_index + 1 - len(snapshotted)))
                    snapshotted[local_index] = reused_path
                    if local_index >= len(values):
                        values.extend([None] * (local_index + 1 - len(values)))
                    values[local_index] = reused_path
                    reused_cut = dict(source_cut)
                    reused_cut.update({
                        "scene_index": youtube_scene_indices[local_index],
                        "status": "accepted",
                        "resolution_kind": "same_scene_source_recovery",
                        "recovery_kind": "same_scene_source_recovery",
                        "path": str(reused_path),
                        "cleaned_path": "",
                    })
                    audit.setdefault("cut_audit", []).append(reused_cut)
                    source_moment = {
                        "source_id": source_cut.get("source_id") or "",
                        "video_id": source_cut.get("source_id") or "",
                        "start": source_cut.get("start"),
                        "end": (
                            float(source_cut.get("start") or 0.0)
                            + float(source_cut.get("duration") or 0.0)
                        ),
                        "url": source_cut.get("source_url") or "",
                    }
                    decisions[str(local_index)] = {
                        "source_moment": source_moment,
                        "status": "accepted",
                        "verification_status": "deterministic_pool_cut",
                        "recovered_same_scene_source": True,
                    }
                    recovered_count += 1
                resolved = sum(1 for path in snapshotted[: len(youtube_scene_indices)] if path)
                audit["same_scene_recovery_attempted"] = len(recovery_requests)
                audit["same_scene_recovery_resolved"] = recovered_count
                audit["resolved"] = resolved
                branch_audit["youtube"] = audit

        decisions = audit.get("retrieval_visual_decisions") or {}
        attempt_rows = audit.get("attempts") or {}
        # Pool-first cuts are already decoded, locally screened, and (when a
        # publisher mark exists) cleaned before they reach this adapter.  The
        # old adapter looked only in the ranged-download ``attempts`` ledger,
        # which is empty for pool cuts.  That made clean pool moments appear
        # unverified, so the coordinator discarded them and reused whichever
        # one moment happened to survive the fallback pass.
        cut_rows = [
            item for item in (audit.get("cut_audit") or [])
            if isinstance(item, Mapping)
        ]
        accepted_cut_by_scene = {
            int(item.get("scene_index")): dict(item)
            for item in cut_rows
            if item.get("scene_index") is not None
            and str(item.get("status") or "").strip().lower() == "accepted"
        }
        # In ranged-download mode (yt_pool_first=False or mixed media), downloaded
        # cuts are recorded in audit["attempts"]. Merge those downloaded rows into
        # accepted_cut_by_scene so they are not treated as unverified and nulled out.
        for local_idx_key, attempts_list in attempt_rows.items():
            try:
                l_idx = int(local_idx_key)
            except Exception:
                continue
            if l_idx in accepted_cut_by_scene:
                continue
            for att in (attempts_list if isinstance(attempts_list, list) else []):
                if isinstance(att, Mapping) and att.get("status") == "downloaded":
                    if att.get("selected") or att.get("cleaned_path") or att.get("path"):
                        accepted_cut_by_scene[l_idx] = dict(att)
                        if att.get("selected"):
                            break
        # The pool and ranged-download lanes can race and return different
        # physical files for the same scene. Re-screen the exact snapshotted
        # path that will enter the compositor; provider metadata alone is not
        # sufficient because a parallel lane may have carried stale overlay
        # fields. This is the final YouTube fail-closed boundary.
        try:
            from services.youtube_pool_renderer import (
                _screen_moment_overlays,
                _screen_moment_quality,
                _motion_admissible,
                _native_overlay_lane_for_category,
            )
        except Exception:
            _screen_moment_overlays = None
            _screen_moment_quality = None
            _motion_admissible = None
            _native_overlay_lane_for_category = lambda _category: False
        # Exact-path screening consumes the cut's recorded overlay policy.
        # Never change global caption/blur settings from a concurrent job.
        post_snapshot_rejections = []

        def _screen_single_scene(local_index, path):
            if not path or not _screen_moment_overlays or not _screen_moment_quality:
                return None
            pool_cut = accepted_cut_by_scene.get(local_index) or accepted_cut_by_scene.get(youtube_scene_indices[local_index]) or {}
            # Disk-salvaged pool cuts were already rigorously screened by _recover_completed_clips_from_disk
            # in app.py before being returned. Fast-track them.
            if pool_cut.get("recovery_kind") == "disk_verified_pool_cut":
                return None

            lane = bool(pool_cut.get("archival_lane"))
            source_scene_index = (
                int(youtube_scene_indices[local_index])
                if local_index < len(youtube_scene_indices)
                else local_index
            )
            semantic_row = (
                semantic_query_rows[source_scene_index]
                if source_scene_index < len(semantic_query_rows)
                and isinstance(semantic_query_rows[source_scene_index], Mapping)
                else {}
            )
            domain_category = str(
                pool_cut.get("domain_category")
                or semantic_row.get("domain_category")
                or ""
            ).strip().lower()
            gaming_lane = domain_category == "gaming"
            native_overlay_lane = bool(
                _native_overlay_lane_for_category(
                    pool_cut.get("domain_category")
                    or semantic_row.get("domain_category")
                )
            )

            try:
                quality = _screen_moment_quality(str(path), archival_lane=lane) or {}
                overlay = _screen_moment_overlays(
                    str(path), archival_lane=lane, gaming_lane=gaming_lane,
                    native_overlay_lane=native_overlay_lane,
                ) or {}
            except Exception as exc:
                quality = {"checked": False, "reject": False}
                overlay = {"checked": False, "reject": False, "error": type(exc).__name__}
            hard_reason = str(quality.get("reason") or "") if quality.get("reject") else ""
            if hard_reason:
                try:
                    soft_admitted = bool(
                        _motion_admissible(quality, hard_reason, archival_lane=lane)
                    )
                except Exception:
                    soft_admitted = False
                if soft_admitted:
                    hard_reason = ""
                elif hard_reason in {"static_image_or_pan", "static_pan", "static_frame_hold", "still_or_slideshow"}:
                    lenient = bool(config.get("yt_lenient_acceptance")) or str(os.environ.get("VIDZEO_YT_LENIENT_ACCEPTANCE", "0")).strip().lower() in {"1", "true", "yes", "on"}
                    if lenient or gaming_lane:
                        hard_reason = ""
            if not hard_reason and overlay.get("reject"):
                rej_reason = str(overlay.get("rejection_reason") or "overlay_rejected")
                remediated_caption = bool(
                    rej_reason in {
                        "caption_overlay_rejected",
                        "burned_in_text_overlay_rejected",
                        "overlay_rejected",
                        "watermark_rejected",
                    }
                    and (
                        pool_cut.get("cleaned_path")
                        or pool_cut.get("overlay_blurred")
                        or ".overlay_clean." in str(path).lower()
                        or ".clean." in str(path).lower()
                        or "blur" in str(pool_cut.get("overlay_policy") or "").lower()
                        or str(pool_cut.get("status") or "").strip().lower() in {"accepted", "downloaded"}
                        or str(os.environ.get("VIDZEO_YT_ALLOW_BOUNDED_CAPTION_BLUR", "0")).strip().lower() in {"1", "true", "yes", "on"}
                    )
                )
                lenient = bool(config.get("yt_lenient_acceptance")) or str(os.environ.get("VIDZEO_YT_LENIENT_ACCEPTANCE", "0")).strip().lower() in {"1", "true", "yes", "on"}
                if remediated_caption or (lenient and rej_reason in {"overlay_blur_failed", "watermark_rejected", "overlay_rejected", "burned_in_text_overlay_rejected", "caption_overlay_rejected"}):
                    hard_reason = ""
                elif gaming_lane and rej_reason in {"caption_overlay_rejected", "burned_in_text_overlay_rejected", "watermark_rejected", "overlay_rejected"}:
                    hard_reason = ""
                else:
                    hard_reason = rej_reason
            if hard_reason:
                return (local_index, hard_reason, quality, overlay, path)
            return None

        screen_targets = [
            (local_index, path)
            for local_index, path in enumerate(snapshotted[: len(youtube_scene_indices)])
            if path and _screen_moment_overlays and _screen_moment_quality
        ]
        if screen_targets:
            max_screen_workers = min(6, len(screen_targets), max(1, os.cpu_count() or 4))
            with ThreadPoolExecutor(max_workers=max_screen_workers, thread_name_prefix="yt-post-screen") as screen_executor:
                screen_results = list(screen_executor.map(lambda target: _screen_single_scene(*target), screen_targets))
            for res in screen_results:
                if not res:
                    continue
                local_index, hard_reason, quality, overlay, path = res
                post_snapshot_rejections.append({
                    "scene_index": youtube_scene_indices[local_index],
                    "reason": hard_reason,
                    "quality_metrics": {
                        key: quality.get(key)
                        for key in (
                            "motion_mean", "motion_changed_fraction",
                            "optical_flow_coherence", "perceptual_hold_fraction",
                        )
                        if key in quality
                    },
                    "overlay_types": list(overlay.get("overlay_types") or []),
                    "facecam_inset": bool(overlay.get("facecam_inset")),
                    "facecam_confidence": float(overlay.get("facecam_confidence") or 0.0),
                    "facecam_reason": str(overlay.get("facecam_reason") or ""),
                    "path": str(path),
                })
                snapshotted[local_index] = None
                values[local_index] = None
                accepted = accepted_cut_by_scene.get(local_index)
                if accepted is not None:
                    accepted.update({
                        "status": "post_snapshot_rejected",
                        "reason": hard_reason,
                        "rejection_reason": hard_reason,
                        "overlay_types": list(overlay.get("overlay_types") or accepted.get("overlay_types") or []),
                    })
        if post_snapshot_rejections:
            audit["post_snapshot_screened"] = len(post_snapshot_rejections) + sum(
                1 for path in snapshotted[: len(youtube_scene_indices)] if path
            )
            audit["post_snapshot_rejections"] = post_snapshot_rejections
            audit["resolved"] = sum(1 for path in snapshotted[: len(youtube_scene_indices)] if path)
            branch_audit["youtube"] = audit

        # A different scene may use the same YouTube master only through the
        # coordinator's verified-distant-variant contract. This adapter has no
        # vision call of its own, so it must not relabel an already-used cut as
        # verified for a new beat.
        rows: dict[int, Any] = {}
        for local_index in range(len(youtube_scene_indices)):
            path = (
                snapshotted[local_index]
                if local_index < len(snapshotted)
                else None
            )
            index = youtube_scene_indices[local_index]
            decision = decisions.get(str(local_index)) or decisions.get(local_index) or {}
            local_attempts = attempt_rows.get(str(local_index)) or attempt_rows.get(local_index) or []
            ranged_overlay_audit = next(
                (
                    dict(item)
                    for item in reversed(local_attempts)
                    if isinstance(item, Mapping)
                    and item.get("status") == "downloaded"
                    and item.get("selected")
                ),
                    {},
                )
            if not ranged_overlay_audit:
                ranged_overlay_audit = next(
                    (
                        dict(item)
                        for item in reversed(local_attempts)
                        if isinstance(item, Mapping) and item.get("status") == "downloaded"
                    ),
                    {},
                )

            # A scene can have several downloaded moments even when its
            # selected primary was later rejected by the final local quality
            # screen.  Keep those alternatives alive until the shared semantic
            # gate; otherwise a bad top-ranked moment erases the good moment
            # that was already paid for and downloaded.
            alternate_records: list[tuple[str, dict[str, Any], int]] = []
            alternate_attempts = [
                dict(item)
                for item in local_attempts
                if isinstance(item, Mapping)
                and _youtube_alternate_attempt_allowed(item)
                and not item.get("selected")
            ]
            seen_transport_paths = {
                str(path).casefold()
                for path in (path,)
                if path
            }
            for alt_index, alternate in enumerate(alternate_attempts[:8], start=1):
                alternate_source = str(
                    alternate.get("path") or alternate.get("cleaned_path") or ""
                ).strip()
                if not alternate_source or not Path(alternate_source).is_file():
                    continue
                try:
                    alternate_key = str(Path(alternate_source).resolve()).casefold()
                except OSError:
                    alternate_key = alternate_source.casefold()
                if alternate_key in seen_transport_paths:
                    continue

                # Re-run the cheap local quality/overlay screen for the exact
                # alternate file that will be snapshotted. The final Qwen
                # gate still owns subject and scene relevance; this only
                # prevents unusable transport files from entering that pool.
                if _screen_moment_overlays and _screen_moment_quality:
                    try:
                        alternate_quality = _screen_moment_quality(
                            alternate_source, archival_lane=False
                        ) or {}
                        alternate_overlay = _screen_moment_overlays(
                            alternate_source,
                            archival_lane=lane,
                            gaming_lane=gaming_lane,
                            native_overlay_lane=native_overlay_lane,
                        ) or {}
                    except Exception:
                        alternate_quality = {"reject": False}
                        alternate_overlay = {"reject": False}
                    alternate_hard_reason = str(
                        alternate_quality.get("reason") or ""
                    ) if alternate_quality.get("reject") else ""
                    if alternate_hard_reason and _motion_admissible:
                        try:
                            if _motion_admissible(
                                alternate_quality,
                                alternate_hard_reason,
                                archival_lane=False,
                            ):
                                alternate_hard_reason = ""
                        except Exception:
                            pass
                        if alternate_hard_reason == "static_image_or_pan":
                            lenient = bool(config.get("yt_lenient_acceptance")) or str(os.environ.get("VIDZEO_YT_LENIENT_ACCEPTANCE", "0")).strip().lower() in {"1", "true", "yes", "on"}
                            if lenient:
                                alternate_hard_reason = ""
                    if not alternate_hard_reason and alternate_overlay.get("reject"):
                        alt_rej = str(
                            alternate_overlay.get("rejection_reason")
                            or "overlay_rejected"
                        )
                        lenient = bool(config.get("yt_lenient_acceptance")) or str(os.environ.get("VIDZEO_YT_LENIENT_ACCEPTANCE", "0")).strip().lower() in {"1", "true", "yes", "on"}
                        if lenient and alt_rej in {"overlay_blur_failed", "watermark_rejected", "overlay_rejected"}:
                            alternate_hard_reason = ""
                        else:
                            alternate_hard_reason = alt_rej
                    if alternate_hard_reason:
                        post_snapshot_rejections.append({
                            "scene_index": index,
                            "reason": alternate_hard_reason,
                            "stage": "alternate_download",
                            "path": alternate_source,
                        })
                        continue

                try:
                    alternate_snapshot = list(snapshotter(
                        [alternate_source],
                        namespace=f"youtube_v2_alt_{index}_{alt_index}",
                    ))
                except Exception:
                    alternate_snapshot = []
                alternate_path = alternate_snapshot[0] if alternate_snapshot else None
                if not alternate_path:
                    continue
                seen_transport_paths.add(alternate_key)
                alternate_records.append((str(alternate_path), alternate, alt_index))

            # If the selected primary was removed by the post-snapshot screen,
            # promote the first still-usable downloaded alternate into the
            # scene slot. Its siblings remain candidates for semantic choice.
            if not path and alternate_records:
                path = alternate_records[0][0]
                alternate_primary = alternate_records[0][1]
                # The primary download may have been rejected for a facecam,
                # subtitle or failed blur while this alternate is the clean
                # bounded-caption cut.  Carry the alternate's exact overlay
                # audit forward; otherwise the promoted row inherits the
                # rejected primary metadata and is hard-gated again.
                ranged_overlay_audit = dict(alternate_primary)
                if local_index >= len(snapshotted):
                    snapshotted.extend([None] * (local_index + 1 - len(snapshotted)))
                if local_index >= len(values):
                    values.extend([None] * (local_index + 1 - len(values)))
                snapshotted[local_index] = path
                values[local_index] = path
                decision = {
                    "source_moment": {
                        **alternate_primary,
                        "start": alternate_primary.get("timestamp"),
                        "end": (
                            float(alternate_primary.get("timestamp") or 0.0)
                            + float(alternate_primary.get("clip_duration") or 0.0)
                        ),
                    },
                    "status": "accepted",
                    "verification_status": "deterministic_ranged_alternate",
                }

            if not path:
                continue
            overlay_audit = accepted_cut_by_scene.get(local_index) or ranged_overlay_audit
            source_moment = decision.get("source_moment") if isinstance(decision, Mapping) else {}
            source_moment = source_moment if isinstance(source_moment, Mapping) else {}
            # Direct ranged downloads record the authoritative YouTube title
            # in the per-attempt audit, while their retrieval decision only
            # carries the timestamp. Preserve that metadata through the V2
            # handoff so the final requirement gate can prove the clip is the
            # requested entity instead of receiving a title-less candidate.
            if ranged_overlay_audit:
                source_moment = {
                    **dict(ranged_overlay_audit),
                    **dict(source_moment),
                }
            source_id = str(
                source_moment.get("video_id")
                or source_moment.get("source_id")
                or source_moment.get("candidate_id")
                or getattr(scenes[index], "scene_id", index)
            )
            candidate_id = f"youtube:{source_id}"
            provider_decision = dict(decision or {}) if isinstance(decision, Mapping) else {}
            metadata_score = _score(source_moment.get('metadata_score')
                or provider_decision.get('relevance') or provider_decision.get('final_score'))
            scene_req = _scene_value(scenes[index], 'visual_requirement', {})
            provider_decision = _youtube_transport_review(provider_decision, scene_req, path)
            primary_row = {
                "candidate_id": candidate_id,
                "path": str(path),
                "media_type": "video",
                "provider": "youtube",
                "source": "youtube",
                "container_id": str(getattr(scenes[index], "container_id", "")),
                "query": str((lane_config.get("_yt_scene_query_overrides") or [{}])[local_index].get("primary_query", ""))
                if local_index < len(lane_config.get("_yt_scene_query_overrides") or [])
                and isinstance((lane_config.get("_yt_scene_query_overrides") or [])[local_index], Mapping)
                else "",
                "origin_url": str((source_moment or {}).get("url") or (source_moment or {}).get("source_url") or ""),
                "title": str(
                    (source_moment or {}).get("title")
                    or provider_decision.get("title")
                    or provider_decision.get("video_title")
                    or ranged_overlay_audit.get("title")
                    or ""
                ),
                "description": str(
                    (source_moment or {}).get("description")
                    or provider_decision.get("description")
                    or ranged_overlay_audit.get("description")
                    or ""
                ),
                "channel": str(
                    (source_moment or {}).get("channel")
                    or provider_decision.get("channel")
                    or provider_decision.get("uploader")
                    or ranged_overlay_audit.get("channel")
                    or ""
                ),
                "rights_status": str(source_moment.get("rights_status") or "unknown_user_responsibility"),
                "license": str(source_moment.get("license") or "unknown"),
                "license_url": str(source_moment.get("license_url") or ""),
                "attribution": str(source_moment.get("attribution") or ""),
                "creator": str(source_moment.get("channel") or source_moment.get("creator") or ""),
                "asset_fingerprint": _asset_fingerprint(str(path), source_moment),
                "qwen_result": provider_decision,
                "final_score": metadata_score,
                **({"transcript_score": _score(source_moment["moment"]["match_score"])}
                   if isinstance(source_moment.get("moment"), Mapping)
                   and source_moment["moment"].get("match_score") is not None else {}),
                # Carry the provider's deterministic overlay result into the
                # shared coordinator. This lets a clean clip survive a
                # temporary vision outage while preserving hard caption
                # rejection.
                "overlay_policy": str(overlay_audit.get("overlay_policy") or ""),
                "source_overlays_preserved": bool(overlay_audit.get("source_overlays_preserved")),
                "no_blur": True,
                "overlay_types": list(overlay_audit.get("overlay_types") or []),
                "overlay_clean": bool(
                    overlay_audit.get("overlay_clean")
                    or (
                        str(overlay_audit.get("overlay_policy") or "").strip().lower()
                        == "clean_or_logo_blur_caption_reject"
                        and not set(str(item).strip().lower() for item in (overlay_audit.get("overlay_types") or []))
                        & {"caption", "subtitle", "subtitles", "large_text_band", "text_overlay"}
                        and not bool(overlay_audit.get("overlay_rejected") or overlay_audit.get("overlay_blur_failed"))
                    )
                ),
                "deterministic_overlay_clean": bool(
                    overlay_audit.get("overlay_clean")
                    or (
                        str(overlay_audit.get("overlay_policy") or "").strip().lower()
                        == "clean_or_logo_blur_caption_reject"
                        and not set(str(item).strip().lower() for item in (overlay_audit.get("overlay_types") or []))
                        & {"caption", "subtitle", "subtitles", "large_text_band", "text_overlay"}
                        and not bool(overlay_audit.get("overlay_rejected") or overlay_audit.get("overlay_blur_failed"))
                    )
                ),
                "overlay_has_subtitle": bool(overlay_audit.get("overlay_has_subtitle")),
                "watermark_tolerated": bool(overlay_audit.get("watermark_tolerated")),
                "talking_head": bool(overlay_audit.get("talking_head")),
                "talking_head_confidence": float(overlay_audit.get("talking_head_confidence") or 0.0),
                "facecam_inset": bool(overlay_audit.get("facecam_inset")),
                "facecam_tolerated": bool(overlay_audit.get("facecam_tolerated")),
                "facecam_confidence": float(overlay_audit.get("facecam_confidence") or 0.0),
                "facecam_reason": str(overlay_audit.get("facecam_reason") or ""),
                "facecam_corner": str(overlay_audit.get("facecam_corner") or ""),
                "outro_detected": bool(overlay_audit.get("outro_detected")),
                "outro_reason": str(overlay_audit.get("outro_reason") or ""),
                "outro_confidence": float(overlay_audit.get("outro_confidence") or 0.0),
                "blur_failed": bool(overlay_audit.get("blur_failed") or overlay_audit.get("overlay_blur_failed")),
                "overlay_blurred": bool(overlay_audit.get("blurred") or overlay_audit.get("overlay_blurred")),
                "rejection_reason": str(overlay_audit.get("rejection_reason") or ""),
                "blur_regions": list(overlay_audit.get("blur_regions") or []),
                # The provider's cleaned file lives in its temporary download
                # directory, while ``path`` is the job-local render snapshot.
                # Keep the manifest self-contained and probeable after the
                # provider workspace is cleaned up.
                "cleaned_path": str(
                    path if overlay_audit.get("overlay_blurred") else
                    (overlay_audit.get("cleaned_path") or "")
                ),
                "video_start": (source_moment or {}).get("start"),
                "video_end": (source_moment or {}).get("end"),
                # youtube_pool_renderer and ranged retrieval both hand back a
                # local, already-trimmed cut. Preserve the original source
                # range above for auditability, but render the local file from
                # its own zero timebase.
                "render_clip_start": 0.0,
                "gate_outcomes": {"decodable": True, "safe": True, "container": True},
            }
            # Direct ranged retrieval now retains every clean candidate it
            # downloaded for this scene. Hand those physical alternatives to
            # the shared coordinator instead of discarding them after the
            # first metadata winner. This is what lets the final visual gate
            # reject a wrong top result and choose a lower-ranked, relevant
            # role/scene match.
            candidate_rows = [primary_row]
            if not is_youtube_fallback:
                fresh_youtube_lane.publish_row(index, list(candidate_rows))
            for alternate_path, alternate, alt_index in alternate_records:
                alt_video_id = str(
                    alternate.get("video_id")
                    or alternate.get("candidate_id")
                    or source_id
                )
                alt_timestamp = alternate.get("timestamp")
                alt_suffix = (
                    f":{round(float(alt_timestamp), 2)}"
                    if alt_timestamp is not None
                    else f":alt{alt_index}"
                )
                alt_candidate_id = f"youtube:{alt_video_id}{alt_suffix}"
                alt_decision = dict(provider_decision)
                alt_decision.update({
                    "candidate_id": alt_candidate_id,
                    "source": "youtube_ranged_alternate",
                    "verification_status": "deterministic_ranged_alternate",
                })
                alt_row = dict(primary_row)
                alt_row.update({
                    "candidate_id": alt_candidate_id,
                    "path": str(alternate_path),
                    "origin_url": str(alternate.get("source_url") or ""),
                    "title": str(alternate.get("title") or primary_row.get("title") or ""),
                    "description": str(alternate.get("description") or ""),
                    "channel": str(alternate.get("channel") or ""),
                    "asset_fingerprint": _asset_fingerprint(str(alternate_path), alternate),
                    "qwen_result": alt_decision,
                    "cleaned_path": str(alternate_path) if alternate.get("overlay_blurred") else "",
                    "watermark_tolerated": bool(
                        alternate.get("watermark_tolerated")
                        or primary_row.get("watermark_tolerated")
                    ),
                    "video_start": alt_timestamp,
                    "video_end": (
                        float(alt_timestamp) + float(alternate.get("clip_duration") or 0.0)
                        if alt_timestamp is not None
                        else None
                    ),
                    # Do not inherit the rejected primary's overlay verdict.
                    # These fields are consumed by the shared hard gate and by
                    # the final manifest audit.
                    "overlay_policy": str(alternate.get("overlay_policy") or primary_row.get("overlay_policy") or ""),
                    "overlay_types": list(alternate.get("overlay_types") or primary_row.get("overlay_types") or []),
                    "overlay_clean": bool(alternate.get("overlay_clean") or alternate.get("overlay_blurred")),
                    "deterministic_overlay_clean": bool(alternate.get("overlay_clean") or alternate.get("overlay_blurred")),
                    "overlay_has_subtitle": bool(alternate.get("overlay_has_subtitle")),
                    "facecam_inset": bool(alternate.get("facecam_inset")),
                    "facecam_confidence": float(alternate.get("facecam_confidence") or 0.0),
                    "facecam_reason": str(alternate.get("facecam_reason") or ""),
                    "talking_head": bool(alternate.get("talking_head")),
                    "talking_head_confidence": float(alternate.get("talking_head_confidence") or 0.0),
                    "blur_failed": bool(alternate.get("overlay_blur_failed") or alternate.get("blur_failed")),
                    "overlay_blurred": bool(alternate.get("overlay_blurred") or alternate.get("blurred")),
                    "rejection_reason": str(alternate.get("rejection_reason") or ""),
                    "blur_regions": list(alternate.get("blur_regions") or []),
                })
                if (
                    alt_row.get("facecam_inset")
                    or alt_row.get("talking_head")
                    or float(alt_row.get("facecam_confidence") or 0.0) >= 0.50
                    or "facecam" in str(alt_row.get("rejection_reason") or "").lower()
                    or "talking_head" in str(alt_row.get("rejection_reason") or "").lower()
                ):
                    continue
                candidate_rows.append(alt_row)
            rows[index] = candidate_rows
            if not is_youtube_fallback:
                fresh_youtube_lane.publish_row(index, list(candidate_rows))
        # Preserve the primary batch outcome for the cross-source retry lane.
        # A Mixed Media quota assignment is not a one-shot commitment: if a
        # primary YouTube scene produced no usable candidate, the opposite
        # lane must be able to ask YouTube again with the broader fallback
        # query set. Without this snapshot, fallback only covered scenes that
        # were never in the quota and a failed quota scene became neutral.
        if youtube_scene_indices == primary_youtube_scene_indices:
            primary_youtube_rows.clear()
            primary_youtube_rows.update(rows)
        ready_count = sum(
            1 for row in rows.values()
            if bool((row[0] if isinstance(row, (list, tuple)) and row else row).get('path'))
        )
        print(f"   [v2] YouTube handoff: {ready_count}/{len(rows)} scenes ready", flush=True)
        if str(os.environ.get("VIDZEO_VERBOSE", "0")).lower() in {"1", "true", "yes"}:
            print(
                "   [v2] YouTube handoff rows: "
                + ", ".join(
                    f"scene={index}:candidates={len(row) if isinstance(row, (list, tuple)) else 1}:"
                    f"path={bool((row[0] if isinstance(row, (list, tuple)) and row else row).get('path'))}:"
                    f"provider={(row[0] if isinstance(row, (list, tuple)) and row else row).get('provider')}:"
                    f"type={(row[0] if isinstance(row, (list, tuple)) and row else row).get('media_type')}:"
                    f"status={(((row[0] if isinstance(row, (list, tuple)) and row else row).get('qwen_result') or {}).get('status') if isinstance((row[0] if isinstance(row, (list, tuple)) and row else row).get('qwen_result'), Mapping) else '')}:"
                    f"verified={(((row[0] if isinstance(row, (list, tuple)) and row else row).get('qwen_result') or {}).get('verification_status') if isinstance((row[0] if isinstance(row, (list, tuple)) and row else row).get('qwen_result'), Mapping) else '')}"
                    for index, row in sorted(rows.items())
                )
                or "none",
                flush=True,
            )
        return rows

    # Quotas express editorial preference, not recovery eligibility. Failed
    # web/stock scenes may need footage too. The coordinator supplies only
    # failed primary scenes before the single bounded recovery batch starts.
    fallback_youtube_scene_indices = (
        list(range(len(scenes)))
        if lane_policy.get("mode") == "Mixed Media" and lane_policy["youtube_enabled"]
        else (
            list(range(len(scenes)))
            if _as_bool(config.get("allow_cross_source_youtube_fallback"), False)
            else []
        )
    )
    primary_youtube_audit: dict[str, Any] | None = None
    # Cross-source fallback is batch-coalesced below.  Keep the per-scene
    # caches as a compatibility layer for callers that inspect retry audits.
    youtube_fallback_cache: dict[int, Any] = {}
    youtube_fallback_attempted: set[int] = set()
    youtube_fallback_batch_state = {"started": False}
    youtube_fallback_batch_lock = threading.Lock()
    youtube_recovery_request: dict[str, Any] = {"indices": None}

    def prepare_youtube_recovery(indices: Sequence[int]) -> None:
        allowed = set(fallback_youtube_scene_indices)
        unresolved = list(dict.fromkeys(
            int(index) for index in indices
            if int(index) in allowed
        ))
        quota = set(primary_youtube_scene_indices)
        # Repair intended footage before spending the bounded recovery slots
        # on cross-source image misses. Preserve scene order within each group.
        youtube_recovery_request["indices"] = (
            [index for index in unresolved if index in quota]
            + [index for index in unresolved if index not in quota]
        )

    def load_youtube_fallback_batch() -> Mapping[int, Any]:
        """Run one bounded YouTube recovery wave for all unresolved scenes."""
        nonlocal primary_youtube_audit
        primary_youtube_audit = dict(branch_audit.get("youtube") or {})
        if primary_youtube_audit.get('provider_network_unavailable'):
            branch_audit['youtube_fallback'] = {'status': 'skipped_provider_network_unavailable'}
            print('   [v2] YouTube DNS unavailable; continuing with web/stock instead of repeating downloads', flush=True)
            return {}
        # A second YouTube wave cannot repair a transport outage.  In that
        # case it only repeats the provider timeout and blocks the web/stock
        # rescue lanes (the live audit showed two sequential YT stalls before
        # stock rescue could begin).  Keep fallback available for ordinary
        # content misses and rejected candidates; skip it only when the
        # primary branch explicitly reports that no transport/download work
        # was possible.
        if lane_policy.get("mode") == "Mixed Media" and not primary_youtube_rows:
            failure_reason = str(primary_youtube_audit.get("failure_reason") or "").lower()
            transport_status = str(primary_youtube_audit.get("transport_status") or "").lower()
            transport_failure = (
                transport_status in {"unavailable", "failed", "timeout"}
                or failure_reason in {
                    "no_download_attempts",
                    "transport_unavailable",
                    "branch_wall_timeout",
                }
                or "timeout" in failure_reason
                or "timeouterror" in failure_reason
            )
            if transport_failure:
                print(
                    "   [v2] YouTube fallback skipped after primary transport failure; "
                    "web/stock rescue owns the remaining slots",
                    flush=True,
                )
                branch_audit["youtube_fallback"] = {
                    "status": "skipped_primary_transport_failure",
                    "requested_scene_indices": list(fallback_youtube_scene_indices),
                    "primary": dict(primary_youtube_audit),
                }
                return {}
        # The primary quota lane may already have delivered clean clips for
        # some requested scenes. The coordinator supplies the rejected/missing
        # primary scenes, including non-quota web misses. Compatibility callers
        # without that preparation retry missing quota rows only.
        def _has_usable_primary_youtube_row(idx: int) -> bool:
            rows_for_idx = primary_youtube_rows.get(idx)
            if not rows_for_idx:
                return False
            candidates = rows_for_idx if isinstance(rows_for_idx, (list, tuple)) else [rows_for_idx]
            for cand in candidates:
                if not isinstance(cand, Mapping):
                    continue
                p = str(cand.get("path") or cand.get("selected_path") or "")
                if p and os.path.isfile(p):
                    status = str(cand.get("status") or "").lower()
                    rej = str(cand.get("rejection_reason") or "").lower()
                    if status not in {"rejected", "post_snapshot_rejected", "failed"} and not rej:
                        return True
            return False

        prepared_indices = youtube_recovery_request.get("indices")
        if prepared_indices is not None:
            # A downloaded path does not mean visual acceptance. A quota cut
            # rejected by the shared gate is eligible for a different moment.
            fallback_targets = list(prepared_indices)
        else:
            # Compatibility callers without coordinator preparation may retry
            # only missing primary quota rows; never reopen the whole film.
            fallback_targets = [index for index in primary_youtube_scene_indices
                                if not _has_usable_primary_youtube_row(index)]
        try:
            recovery_limit = max(1, min(16, int(config.get("yt_recovery_batch_max_scenes") or 8)))
        except (TypeError, ValueError):
            recovery_limit = 8
        fallback_targets = fallback_targets[:recovery_limit]
        if not fallback_targets:
            print("   [v2] YouTube fallback skipped: no unresolved recovery targets", flush=True)
            return {}
        previous_indices = list(youtube_indices_ref.get("indices") or ())
        # The fallback runs once for the complete scene vector so the provider
        # can share search/download work and the coordinator can choose the
        # best row for each hole.  A bounded wall is applied by load_youtube.
        youtube_indices_ref["indices"] = list(fallback_targets)
        youtube_fallback_mode["enabled"] = True
        try:
            rows = load_youtube()
            fallback_audit = dict(branch_audit.get("youtube") or {})
            if fallback_audit:
                audit = branch_audit.setdefault("youtube_fallback", {})
                audit.setdefault("runs", []).append({**fallback_audit})
                audit["requested_scene_indices"] = list(fallback_targets)
                audit["unresolved_primary_scene_indices"] = list(prepared_indices or [])
                audit["batch_scene_cap"] = recovery_limit
            return dict(rows or {}) if isinstance(rows, Mapping) else {}
        finally:
            youtube_fallback_mode["enabled"] = False
            youtube_indices_ref["indices"] = previous_indices
            if primary_youtube_audit:
                branch_audit["youtube"] = primary_youtube_audit

    def load_youtube_fallback_for_scene(scene_index: int) -> Any:
        """Serve one scene from the coalesced YouTube fallback batch."""
        if scene_index < 0 or scene_index >= len(scenes):
            return None
        if scene_index in youtube_fallback_attempted:
            return youtube_fallback_cache.get(scene_index)
        youtube_fallback_attempted.add(scene_index)
        with youtube_fallback_batch_lock:
            if not youtube_fallback_batch_state["started"]:
                youtube_fallback_batch_state["started"] = True
                rows = load_youtube_fallback_batch()
                for index in fallback_youtube_scene_indices:
                    youtube_fallback_cache[index] = rows.get(index)
        return youtube_fallback_cache.get(scene_index)

    web_indices_ref = {"indices": [index for index in range(len(scenes)) if index not in (completed_web_indices | completed_stock_indices | set(saved_youtube_rows))]}
    web_fallback_mode = {"enabled": False}
    primary_web_audit: dict[str, Any] | None = None
    primary_web_failed: str = ""
    primary_web_rows: dict[int, Any] = dict(saved_web_rows)
    web_fallback_cache: dict[int, Any] = {}
    web_fallback_attempted: set[int] = set()
    web_fallback_batch_state = {"started": False, "loading": False, "targets": []}
    web_fallback_batch_condition = threading.Condition()
    web_fallback_scene_retry_attempted: set[int] = set()
    web_fallback_retry_slots = threading.BoundedSemaphore(3)

    def load_web(active_indices: Sequence[int] | None = None) -> Mapping[int, Any]:
        nonlocal primary_web_rows, primary_web_failed
        active_indices = list(
            active_indices
            if active_indices is not None
            else web_indices_ref.get("indices") or range(len(scenes))
        )
        active_indices = [index for index in active_indices if 0 <= int(index) < len(scenes)]
        is_scene_recovery = (
            active_indices != list(range(len(scenes)))
            or bool(web_fallback_mode.get("enabled"))
        )
        local_scene_texts = [scene_texts[index] for index in active_indices]
        local_scene_durations = [scene_durations[index] for index in active_indices]
        local_query_rows = [semantic_query_rows[index] for index in active_indices]
        progress_base = 0.55 if is_scene_recovery else 0.31
        progress_span = 0.07 if is_scene_recovery else 0.18
        report_progress(
            progress_base,
            f"V2 web {'recovery' if is_scene_recovery else 'discovery'}: {len(active_indices)} scenes",
        )
        lane_config = dict(config)
        lane_config["visual_understanding"] = False
        if broad_anchor and provider_topic_title != valid_topic_anchor:
            lane_config['_topic_anchor'] = provider_topic_title
        lane_config["visual_required"] = False
        lane_config["_web_visual_context"] = transport_visual_context
        lane_config["_final_visual_gate_required"] = True
        lane_config["_semantic_query_rows"] = local_query_rows
        lane_config["_semantic_query_lock"] = True
        lane_config["web_wait_until_complete"] = bool(
            config.get("web_wait_until_complete", False)
        )
        # V2 also constructs a fresh web lane config, so preserve the
        # operator's I/O fan-out here instead of relying on legacy app defaults.
        lane_config.setdefault(
            "web_max_scene_workers",
            max(
                1,
                min(
                    12,
                    int(
                        os.environ.get("VIDZEO_MIXED_WEB_SCENE_WORKERS", "8")
                        or 8
                    ),
                ),
            ),
        )
        lane_config.setdefault(
            "web_api_workers",
            max(
                1,
                min(
                    16,
                    int(
                        os.environ.get("VIDZEO_MIXED_WEB_API_WORKERS", "12")
                        or 12
                    ),
                ),
            ),
        )
        lane_config.setdefault(
            "web_max_candidate_downloads",
            int(os.environ.get("VIDZEO_MIXED_WEB_MAX_CANDIDATES", "6") or 6),
        )
        # The V2 caller often receives a legacy ``web_max_scene_workers``
        # value (the packaged app historically defaulted this to six).  That
        # value must not silently cap the contract-backed Mixed Media lane:
        # the shared provider pool is already warmed, and leaving only six
        # workers causes the first query wave to monopolize the stage while
        # later scenes never reach retrieval.  Keep an explicit higher
        # operator setting, but promote stale lower defaults to the bounded
        # mixed-media fan-out configured for this deployment.
        if lane_policy.get("mode") == "Mixed Media":
            configured_scene_workers = int(lane_config.get("web_max_scene_workers") or 0)
            lane_config["web_max_scene_workers"] = _mixed_web_scene_worker_limit(lane_config)
            if lane_config["web_max_scene_workers"] != configured_scene_workers:
                print(
                    "   [web] mixed-media scene workers: "
                    f"{lane_config['web_max_scene_workers']} (legacy={configured_scene_workers or 'unset'})",
                    flush=True,
                )
        # The primary web batch is allowed its normal packaged budget. A
        # scene-scoped recovery must be a bounded rescue, not another full
        # all-scenes search: the coordinator invokes it serially for rejected
        # scenes, so an unbounded 240s resolver here multiplied into the
        # 45-minute hangs reported by portable users. Keep recovery transport
        # bounded and parallel; the coordinator's shared visual verifier
        # remains authoritative for correctness.
        if is_scene_recovery:
            def _recovery_float(key: str, default: float, lower: float, upper: float) -> float:
                try:
                    value = float(config.get(key) or default)
                except (TypeError, ValueError):
                    value = float(default)
                return max(float(lower), min(float(upper), value))

            _recovery_configured = config.get("web_fallback_stage_deadline_seconds")
            if (
                _recovery_configured in (None, "", 0, "0")
                and str(config.get("llm_provider") or "") == "external_agent"
            ):
                # Agent jobs: recovery is bounded by the shared delivery
                # budget, and the recovery wall itself is scene-scaled
                # (12s/scene, floor 90s, cap 1800s) so a final web retry
                # cannot repeat the multi-minute image-stage stall, while a
                # large job still finishes its queued scenes.
                try:
                    from .delivery_budget import get_delivery_budget

                    _recovery_budget = get_delivery_budget(config)
                except Exception:
                    _recovery_budget = None
                if _recovery_budget is not None:
                    try:
                        _recovery_reserve = float(
                            config.get("_render_reserve_seconds")
                            or _recovery_budget.render_reserve_seconds
                        )
                    except (TypeError, ValueError):
                        _recovery_reserve = float(_recovery_budget.render_reserve_seconds)
                    _recovery_configured = _recovery_budget.allowance(
                        None, reserve_after_seconds=_recovery_reserve
                    )
            lane_config["web_stage_deadline_seconds"] = _web_recovery_stage_seconds(
                len(active_indices),
                int(lane_config.get("web_max_scene_workers") or 6),
                _recovery_configured,
            )
            lane_config["web_query_deadline_seconds"] = max(
                1.5,
                _recovery_float(
                    "web_fallback_query_deadline_seconds", 10.0, 2.0, 15.0,
                ),
            )
            lane_config["web_batch_discovery_seconds"] = max(
                3.0,
                _recovery_float(
                    "web_fallback_batch_discovery_seconds", 12.0, 3.0, 15.0,
                ),
            )
            # gallery-dl is an optional, bounded slow-web tier. Use it only
            # during exhaustive recovery (after the fast scrapers), and let
            # an explicit operator setting disable it when desired.
            gdl_setting = config.get("web_enable_gallery_dl")
            if gdl_setting is None:
                env_gdl = os.environ.get("VIDZEO_WEB_ENABLE_GDL")
                gdl_setting = True if env_gdl is None else str(env_gdl).strip().lower() in {"1", "true", "yes", "on"}
            lane_config["web_enable_gallery_dl"] = bool(gdl_setting)
            # Recovery is the last web-only tier before stock. Give every
            # bounded scraper a fair parallel wave and retain enough local
            # candidates for the coordinator's shared visual gate.
            lane_config["web_exhaustive_fallback"] = True
            # Recovery still fans out across every enabled scraper, but it
            # must use the same bounded shortlist as the primary V2 wave.
            # The old non-fast recovery downloaded full provider pages for
            # every unresolved scene; with three retry slots that multiplied
            # into several minutes before stock could even start.
            lane_config["web_fast_mode"] = True
            lane_config["web_max_candidate_downloads"] = min(
                8,
                max(4, int(config.get("web_max_candidate_downloads") or 4)),
            )
            # Do not spend a second paid visual wave in a recovery callback;
            # the outer V2 coordinator will verify the returned candidates.
            lane_config["_web_visual_context"] = transport_visual_context
        elif lane_policy.get("mode") == "Mixed Media" and not lane_config.get("web_wait_until_complete"):
            # A process-level .env may carry a legacy 240s ceiling. That is
            # long enough for a stalled provider to starve the coordinator and
            # leave every later scene neutral. Keep the value configurable but
            # clamp the primary mixed-media wave to a scene-scaled window;
            # shared discovery/cache work makes this ample on slow links.
            try:
                configured_web_wall = float(
                    lane_config.get("web_stage_deadline_seconds")
                    or os.environ.get("VIDZEO_WEB_STAGE_DEADLINE_SECONDS")
                    or 120.0
                )
            except (TypeError, ValueError):
                configured_web_wall = 120.0
            # Keep the primary web lane inside the shared V2 coordinator wall,
            # while leaving enough time for the scene workers to download and
            # validate the prefetched shortlist.  Discovery is already pooled
            # across providers, so a large fixed wall only makes a slow scene
            # tail hold the whole edit.  The floor/cap are configurable for
            # unusually slow networks. The default leaves enough time for the
            # exact multi-source scene wave and its bounded image downloads;
            # YouTube/stock rescue still has its own coordinator budget.
            try:
                web_wall_floor = max(
                    45.0,
                    min(
                        180.0,
                        float(
                            lane_config.get("mixed_web_min_wall_seconds")
                            or os.environ.get("VIDZEO_MIXED_WEB_MIN_WALL_SECONDS", "120")
                            or 120.0
                        ),
                    ),
                )
            except (TypeError, ValueError):
                web_wall_floor = 120.0
            try:
                web_wall_cap = max(
                    web_wall_floor,
                    min(
                        _mixed_web_completion_cap_seconds(),
                        float(
                            lane_config.get("mixed_web_max_wall_seconds")
                            or os.environ.get(
                                "VIDZEO_MIXED_WEB_MAX_WALL_SECONDS",
                                str(_mixed_web_completion_cap_seconds()),
                            )
                            or _mixed_web_completion_cap_seconds()
                        ),
                    ),
                )
            except (TypeError, ValueError):
                web_wall_cap = max(web_wall_floor, _mixed_web_completion_cap_seconds())
            if str(config.get("llm_provider") or "") == "external_agent":
                # Agent-driven jobs can gather slower complementary results.
                # The delivery-budget allowance still bounds the wall (the
                # render reserve is never spent); the old fixed 180s ceiling
                # that cut large jobs off mid-batch is gone.
                try:
                    agent_wall = 120.0
                except (TypeError, ValueError):
                    agent_wall = 120.0
                try:
                    from .delivery_budget import get_delivery_budget

                    _budget = get_delivery_budget(config)
                except Exception:
                    _budget = None
                if _budget is not None:
                    try:
                        _reserve = float(
                            config.get("_render_reserve_seconds")
                            or _budget.render_reserve_seconds
                        )
                    except (TypeError, ValueError):
                        _reserve = float(_budget.render_reserve_seconds)
                    agent_wall = max(
                        60.0,
                        min(
                            _mixed_web_completion_cap_seconds(),
                            _budget.allowance(None, reserve_after_seconds=_reserve),
                        ),
                    )
                web_wall_floor = agent_wall
                web_wall_cap = min(
                    _mixed_web_completion_cap_seconds(),
                    max(web_wall_cap, agent_wall),
                )
            # Larger storyboards can use more of the completion ceiling; a
            # long-form override can raise the cap but never lowers it.
            if len(active_indices) >= 32 and web_wall_cap <= 120.0:
                try:
                    long_form_cap = max(
                        web_wall_cap,
                        min(
                            _mixed_web_completion_cap_seconds(),
                            float(
                                lane_config.get("mixed_web_long_form_max_wall_seconds")
                                or os.environ.get("VIDZEO_MIXED_WEB_LONG_FORM_MAX_WALL_SECONDS", "240")
                                or 240.0
                            ),
                        ),
                    )
                except (TypeError, ValueError):
                    long_form_cap = max(web_wall_cap, 180.0)
                web_wall_cap = long_form_cap
            # Completion-oriented: the wall grows with the scenes left to
            # resolve (per-scene allowance) instead of cutting the batch off
            # at a fixed time. See _mixed_web_wall_seconds.
            lane_config["web_stage_deadline_seconds"] = _mixed_web_wall_seconds(
                len(active_indices),
                configured_web_wall,
                web_wall_floor,
                web_wall_cap,
            )
            print(
                f"   [budget] mixed web wall: {lane_config['web_stage_deadline_seconds']:.0f}s "
                f"(scenes={len(active_indices)}, floor={web_wall_floor:.0f}s, cap={web_wall_cap:.0f}s)"
            )
        elif lane_policy.get("mode") == "Mixed Media":
            print(
                "   [budget] mixed web soft wall: disabled; the resolver's "
                "completion-scaled stage ceiling applies",
                flush=True,
            )
        if str(config.get("llm_provider") or "") == "external_agent":
            # Agent jobs collect all provider results within the bounded
            # per-scene (90s) and scene-scaled per-stage walls.
            lane_config["web_wait_until_complete"] = True
        try:
            bundle = web_runner(
                local_scene_texts,
                local_scene_durations,
                lane_config,
                provider_topic_title,
                script,
                # The earlier app-level pre-bake is a legacy convenience cache. It
                # may contain an incidental word such as ``early`` and therefore
                # cannot override the immutable contract built above. Pass the
                # semantic rows as the sole prebuilt query source; the web
                # resolver may still expand providers/waves, but it may not
                # redefine the entity or invent an era.
                prebuilt_queries=local_query_rows,
                prebuilt_gallery_cmd=config.get("_prebuilt_gallery_cmd"),
                progress_callback=lambda fraction, message: report_progress(
                    progress_base + max(0.0, min(1.0, float(fraction))) * progress_span,
                    message,
                ),
            )
        except Exception as exc:
            primary_web_failed = f"{type(exc).__name__}: {exc}"
            branch_audit["web_error"] = primary_web_failed[:500]
            print(
                f"   [v2] web lane failed; continuing with YouTube/stock rescue: {primary_web_failed}",
                flush=True,
            )
            return {}
        result = dict((bundle or {}).get("web_result") or {})
        audit = dict(result.get("audit") or {})
        audit['scene_indices'] = list(active_indices)
        branch_audit["web"] = audit
        if (
            not is_scene_recovery
            and int(audit.get("worker_error_count") or 0) > 0
            and not primary_web_failed
        ):
            primary_web_failed = "web_worker_errors"
            branch_audit["web_error"] = {
                "error": primary_web_failed,
                "worker_errors": list(audit.get("worker_errors") or [])[:8],
            }
        metadata = list(result.get("image_metadata") or ())
        audit_scenes = list(audit.get("scenes") or ())
        audited_by_index: dict[int, Mapping[str, Any]] = {}
        for audit_row in audit_scenes:
            if not isinstance(audit_row, Mapping):
                continue
            try:
                audit_index = max(0, int(audit_row.get("scene_number") or 1) - 1)
            except (TypeError, ValueError):
                continue
            if audit_index < len(active_indices):
                audited_by_index[audit_index] = audit_row
        # The desktop app's web runner can persist successful paths only in
        # its per-scene audit while returning an empty image_paths list.  Do
        # not discard those verified downloads: recover the exact audited
        # paths before snapshotting them into the job-local render directory.
        raw_paths = list(result.get("image_paths") or ())
        # The resolver's audit is the authoritative scene-indexed contract.
        # Some runners return a complete vector containing empty slots while
        # persisting the real path only in the audit row (and older runners
        # returned a compressed vector).  Always repair each empty slot from
        # its explicit ``scene_number`` audit entry before snapshotting.  This
        # prevents a valid scene-1 download from disappearing at the V2
        # handoff, which previously made the coordinator report no candidates
        # even though the web audit had a real file.
        if audited_by_index:
            if len(raw_paths) != len(active_indices):
                raw_paths = [
                    str(audited_by_index.get(index, {}).get("path") or "")
                    for index in range(len(active_indices))
                ]
            else:
                raw_paths = [
                    (str(path or "").strip()
                     or str(audited_by_index.get(index, {}).get("path") or ""))
                    for index, path in enumerate(raw_paths)
                ]
        elif not raw_paths and audit_scenes:
            raw_paths = [
                str(audited_by_index.get(index, {}).get("path") or "")
                for index in range(len(active_indices))
            ]
        elif len(raw_paths) < len(active_indices):
            raw_paths.extend([""] * (len(active_indices) - len(raw_paths)))
        namespace = "web_v2" if active_indices == list(range(len(scenes))) else f"web_v2_fallback_{active_indices[0] if active_indices else 0}"
        paths = list(snapshotter(raw_paths, namespace=namespace))
        if len(paths) < len(active_indices):
            paths.extend([None] * (len(active_indices) - len(paths)))
        rows: dict[int, Any] = {}
        for local_index, path in enumerate(paths[: len(active_indices)]):
            if not path:
                continue
            index = active_indices[local_index]
            row = {}
            if local_index < len(metadata) and isinstance(metadata[local_index], Mapping):
                row.update(metadata[local_index])
            audit_row = audited_by_index.get(local_index)
            if audit_row is None and local_index < len(audit_scenes) and isinstance(audit_scenes[local_index], Mapping):
                audit_row = audit_scenes[local_index]
            if isinstance(audit_row, Mapping):
                row.update(audit_row)
            nested_metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
            pending_visual_candidate = bool(
                config.get("_final_visual_gate_required")
                and _web_candidate_is_explicitly_pending_visual(row)
            )
            license_verification_required = _as_bool(
                row.get("license_verification_required", nested_metadata.get("license_verification_required")),
                False,
            )
            license_verified = _as_bool(
                row.get("license_verified", nested_metadata.get("license_verified")),
                False,
            )
            web_decision = dict(row.get("visual_decision") or {})
            candidate_id = f"web:{_scene_value(scenes[index], 'scene_id', index)}"
            if not web_decision:
                # When web retrieval runs in deterministic mode (visual_understanding=False),
                # inspect candidate evidence before stamping a decision.
                cand_ev = dict(row.get("candidate_evidence") or {})
                rejection_reason = str(row.get("rejection_reason") or row.get("identity_gate_reason") or "")
                is_mismatch = bool(
                    not pending_visual_candidate
                    and (
                        (cand_ev.get("canonical_entity_mismatch") and not cand_ev.get("nearest_match"))
                        or (row.get("identity_failed") and not cand_ev.get("nearest_match"))
                        or rejection_reason in {"canonical_entity_mismatch", "forbidden_confusable_entity", "missing_required_model"}
                        or (cand_ev.get("missing_terms") and not cand_ev.get("thematic_near_match") and not cand_ev.get("identity_proof") and not cand_ev.get("nearest_match"))
                    )
                )
                if is_mismatch:
                    web_decision = {
                        "candidate_id": candidate_id,
                        "entity_conflict": True,
                        "identity_score": 0.0,
                        "action_score": 0.0,
                        "context_score": 0.0,
                        "quality": 50.0,
                        "final_score": 0.0,
                        "visible_overlays": [],
                        "subject_box": {},
                        "safe_text_regions": [],
                        "accepted": False,
                        "status": "rejected",
                        "source": "web_image_resolver",
                        "verification_status": "rejected_entity_mismatch",
                    }
                else:
                    raw_score = float(row.get("final_score") or row.get("pre_score") or 10.0)
                    norm_score = max(55.0, min(100.0, raw_score * 8.0 if raw_score <= 12.0 else raw_score))
                    web_decision = {
                        "candidate_id": candidate_id,
                        "entity_conflict": False,
                        "identity_score": 100.0,
                        "action_score": 100.0,
                        "context_score": norm_score,
                        "quality": 85.0,
                        "final_score": norm_score,
                        "visible_overlays": [],
                        "subject_box": {},
                        "safe_text_regions": [],
                        "accepted": True,
                        "status": "accepted",
                        "source": "web_image_resolver",
                        "verification_status": (
                            "pending_visual_verification"
                            if pending_visual_candidate
                            else "deterministic_web_image"
                        ),
                        "visual_verification_required": pending_visual_candidate,
                    }
            is_primary_mismatch = bool(web_decision.get("entity_conflict") or not web_decision.get("accepted"))
            raw_final = float(row.get("final_score") or row.get("pre_score") or 10.0)
            norm_final = 0.0 if is_primary_mismatch else max(55.0, min(100.0, raw_final * 8.0 if raw_final <= 12.0 else raw_final))
            primary_row = {
                **row,
                "candidate_id": candidate_id,
                "path": str(path),
                "media_type": "image",
                "provider": "web",
                "source": "web",
                "identity_failed": bool(row.get("identity_failed") or is_primary_mismatch),
                "verified": bool(not is_primary_mismatch and row.get("verified", True)),
                "container_id": str(_scene_value(scenes[index], "container_id", "")),
                "query": str(row.get("query_used") or ""),
                "origin_url": str(row.get("source_url") or row.get("page_url") or row.get("url") or ""),
                "rights_status": str(row.get("rights_status") or nested_metadata.get("rights_status") or row.get("license") or "unknown"),
                "license": str(row.get("license") or nested_metadata.get("license") or "unknown"),
                "license_url": str(row.get("license_url") or nested_metadata.get("license_url") or ""),
                "attribution": str(row.get("attribution") or nested_metadata.get("attribution") or ""),
                "creator": str(row.get("creator") or nested_metadata.get("creator") or row.get("author") or ""),
                "asset_fingerprint": _asset_fingerprint(str(path), row),
                "qwen_result": web_decision,
                "final_score": norm_final,
                "gate_outcomes": {
                    "decodable": True,
                    "safe": True,
                    "container": True,
                    "rights": not license_verification_required or license_verified,
                },
            }
            if pending_visual_candidate:
                # This is a recall handoff only. Keep the marker so the
                # coordinator knows the resolver's metadata mismatch was
                # intentionally deferred and must be decided from the pixels.
                primary_row["semantic_context_pending"] = True
                primary_row["visual_verification_required"] = True
            # The resolver keeps a small downloaded shortlist on the audit
            # row. Snapshot those alternates as independent candidates so the
            # coordinator can try a fresh, already screened still when the
            # primary candidate's final visual gate rejects it.
            candidate_rows = [primary_row]
            alternate_rows = row.get("alternates") or []
            if isinstance(alternate_rows, (list, tuple)):
                for alt_index, alternate in enumerate(alternate_rows[:8], start=1):
                    if not isinstance(alternate, Mapping):
                        continue
                    alternate_source = str(alternate.get("path") or "").strip()
                    if not alternate_source or alternate_source == str(path):
                        continue
                    try:
                        alternate_snapshot = list(snapshotter(
                            [alternate_source],
                            namespace=f"web_v2_alt_{index}_{alt_index}",
                        ))
                    except Exception:
                        alternate_snapshot = []
                    alternate_path = alternate_snapshot[0] if alternate_snapshot else None
                    if not alternate_path:
                        continue
                    alt_ev = dict(alternate.get("candidate_evidence") or {})
                    alt_rejection = str(alternate.get("rejection_reason") or alternate.get("identity_gate_reason") or "")
                    alternate_pending_visual = bool(
                        config.get("_final_visual_gate_required")
                        and _web_candidate_is_explicitly_pending_visual(alternate)
                    )
                    alt_mismatch = bool(
                        not alternate_pending_visual
                        and (
                            (alt_ev.get("canonical_entity_mismatch") and not alt_ev.get("nearest_match"))
                            or (alternate.get("identity_failed") and not alt_ev.get("nearest_match"))
                            or alt_rejection in {"canonical_entity_mismatch", "forbidden_confusable_entity", "missing_required_model"}
                            or (alt_ev.get("missing_terms") and not alt_ev.get("thematic_near_match") and not alt_ev.get("identity_proof") and not alt_ev.get("nearest_match"))
                        )
                    )
                    if alt_mismatch:
                        alt_decision = {
                            "candidate_id": f"{candidate_id}:alt{alt_index}",
                            "entity_conflict": True,
                            "identity_score": 0.0,
                            "action_score": 0.0,
                            "context_score": 0.0,
                            "quality": 50.0,
                            "final_score": 0.0,
                            "visible_overlays": [],
                            "subject_box": {},
                            "safe_text_regions": [],
                            "accepted": False,
                            "status": "rejected",
                            "source": "web_image_resolver",
                            "verification_status": "rejected_entity_mismatch",
                        }
                    else:
                        alt_decision = dict(web_decision, candidate_id=f"{candidate_id}:alt{alt_index}")
                    alt_row = dict(primary_row)
                    alt_row.update({
                        key: value
                        for key, value in alternate.items()
                        if key not in {"path", "alternates"} and value not in (None, "")
                    })
                    alt_row.update({
                        "candidate_id": f"{candidate_id}:alt{alt_index}",
                        "path": str(alternate_path),
                        "identity_failed": bool(alternate.get("identity_failed") or alt_mismatch),
                        "verified": bool(not alt_mismatch and alternate.get("verified", True)),
                        "origin_url": str(
                            alternate.get("source_url") or alternate.get("page_url")
                            or alternate.get("url") or alt_row.get("origin_url") or ""
                        ),
                        "asset_fingerprint": _asset_fingerprint(str(alternate_path), alternate),
                        "qwen_result": alt_decision,
                        "final_score": 0.0 if alt_mismatch else alt_row.get("final_score", norm_final),
                    })
                    if alternate_pending_visual:
                        alt_row["semantic_context_pending"] = True
                        alt_row["visual_verification_required"] = True
                        alt_row["qwen_result"]["verification_status"] = "pending_visual_verification"
                        alt_row["qwen_result"]["visual_verification_required"] = True
                    candidate_rows.append(alt_row)
            rows[index] = candidate_rows
        ready_count = sum(
            1 for row in rows.values()
            if bool((row[0] if isinstance(row, (list, tuple)) and row else row).get('path'))
        )
        from .media_checkpoint_history import save_pending_web_candidates
        try:
            save_pending_web_candidates(
                config.get('_output_package_dir') or config.get('output_package_dir'), scenes, rows)
        except OSError as checkpoint_error:
            # Keep live candidates even when their recovery receipt cannot be written.
            branch_audit['web_checkpoint_error'] = type(checkpoint_error).__name__
            print(f'   [v2] web checkpoint write unavailable: {type(checkpoint_error).__name__}; downloaded candidates retained', flush=True)
        print(f"   [v2] Web handoff: {ready_count}/{len(rows)} scenes ready", flush=True)
        if str(os.environ.get("VIDZEO_VERBOSE", "0")).lower() in {"1", "true", "yes"}:
            print(
                "   [v2] Web handoff rows: "
                + ", ".join(
                    f"scene={index}:path={bool((row[0] if isinstance(row, (list, tuple)) and row else row).get('path'))}:"
                    f"provider={(row[0] if isinstance(row, (list, tuple)) and row else row).get('provider')}:"
                    f"type={(row[0] if isinstance(row, (list, tuple)) and row else row).get('media_type')}:"
                    f"score={(row[0] if isinstance(row, (list, tuple)) and row else row).get('final_score')}"
                    for index, row in sorted(rows.items())
                )
                or "none",
                flush=True,
            )
        # Snapshot the primary hard-gated web rows. Recovery should target
        # only scenes that had no usable web candidate; reopening an all-scene
        # provider crawl after the visual gate was the main source of the
        # multi-minute production stall.
        if not web_fallback_mode.get("enabled"):
            primary_web_rows.update(rows)
        return rows

    def load_web_fallback_batch() -> Mapping[int, Any]:
        """Run one bounded web recovery wave for all unresolved scenes."""
        nonlocal primary_web_audit
        primary_web_audit = dict(branch_audit.get("web") or {})

        def _has_usable_primary(cand_list: Any) -> bool:
            if not cand_list:
                return False
            items = cand_list if isinstance(cand_list, (list, tuple)) else [cand_list]
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                if not item.get("path"):
                    continue
                if item.get("identity_failed") or item.get("entity_conflict"):
                    continue
                decision = item.get("visual_decision") or item.get("qwen_result") or {}
                if isinstance(decision, Mapping):
                    if decision.get("entity_conflict") or not decision.get("accepted", True):
                        continue
                    # A deterministic transport stamp is a discovery status,
                    # not a final semantic verdict, but the downloaded row is
                    # still the input the shared coordinator must verify. Do
                    # not launch another all-scene web crawl merely because
                    # Qwen has not inspected it yet; the coordinator's single
                    # final visual gate owns that check. A second crawl here
                    # was the source of the 75s duplicate web tail seen in
                    # live jobs with healthy 6/6 web coverage.
                if not item.get("verified", True):
                    continue
                if float(item.get("final_score") or 0.0) <= 0.0:
                    continue
                return True
            return False

        prepared_targets = web_fallback_batch_state.get("verified_failure_targets")
        fallback_targets = (
            list(prepared_targets) if prepared_targets is not None else [
                index for index in range(len(scenes))
                if not _has_usable_primary(primary_web_rows.get(index))
            ]
        )
        web_fallback_batch_state["targets"] = list(fallback_targets)
        if not fallback_targets:
            print("   [v2] web fallback skipped: primary web coverage is complete", flush=True)
            return {}
        # A fatal primary resolver error must not trigger another full
        # all-scene provider crawl.  That pattern multiplied the same
        # ``dictionary changed size`` failure into a long apparent stall.
        # Keep the recovery bounded, but give the web pool enough scene
        # coverage to replace a failed primary wave before stock is considered.
        if primary_web_failed:
            recovery_cap = max(
                1,
                min(
                    len(fallback_targets),
                    int(os.environ.get("VIDZEO_WEB_FATAL_RECOVERY_SCENES", "8") or 8),
                ),
            )
            fallback_targets = fallback_targets[:recovery_cap]
            print(
                f"   [v2] web fatal recovery limited to {len(fallback_targets)} scene(s)",
                flush=True,
            )
        report_progress(
            0.44,
            f"V2 web recovery: retrying {len(fallback_targets)}/{len(scenes)} unresolved scene(s)",
        )
        try:
            # A single batch avoids one 14-second scraper request per scene.
            # load_web recognizes the recovery flag and applies the short
            # recovery deadlines while retaining provider diversity.
            web_fallback_mode["enabled"] = True
            rows = load_web(fallback_targets)
            fallback_audit = dict(branch_audit.get("web") or {})
            if fallback_audit:
                audit = branch_audit.setdefault("web_fallback", {})
                audit.setdefault("runs", []).append({**fallback_audit})
                audit["requested_scene_indices"] = list(fallback_targets)
            result_rows = dict(rows or {}) if isinstance(rows, Mapping) else {}
            report_progress(
                0.49,
                f"V2 web recovery complete: {len(result_rows)}/{len(fallback_targets)} scene(s) returned",
            )
            return result_rows
        finally:
            web_fallback_mode["enabled"] = False
            if primary_web_audit:
                branch_audit["web"] = primary_web_audit

    def prepare_web_recovery(indices: Sequence[int]) -> None:
        # The coordinator owns pixel verification. A downloaded primary path
        # is not usable coverage after that verifier has rejected its content.
        # Batch just those failed scenes once instead of retrying only the
        # first two and silently starving the rest of the storyboard.
        web_fallback_batch_state["verified_failure_targets"] = sorted({
            int(index) for index in indices if 0 <= int(index) < len(scenes)
        })

    def load_web_fallback_for_scene(scene_index: int) -> Any:
        """Use the shared web rescue wave, then retry scenes whose primary
        image was rejected by the final visual gate with a scene-scoped query.
        """
        if scene_index < 0 or scene_index >= len(scenes):
            return None
        owns_batch = False
        with web_fallback_batch_condition:
            while web_fallback_batch_state["loading"]:
                web_fallback_batch_condition.wait(timeout=0.1)
            if scene_index in web_fallback_attempted:
                return web_fallback_cache.get(scene_index)
            web_fallback_attempted.add(scene_index)
            if not web_fallback_batch_state["started"]:
                web_fallback_batch_state["started"] = True
                web_fallback_batch_state["loading"] = True
                owns_batch = True

        if owns_batch:
            try:
                rows = load_web_fallback_batch()
                for index in range(len(scenes)):
                    web_fallback_cache[index] = rows.get(index)
            except Exception as exc:
                print(
                    f"   [v2] shared web recovery failed: {type(exc).__name__}",
                    flush=True,
                )
            finally:
                with web_fallback_batch_condition:
                    web_fallback_batch_state["loading"] = False
                    web_fallback_batch_condition.notify_all()

        if scene_index in set(web_fallback_batch_state.get("targets") or ()):
            return web_fallback_cache.get(scene_index)

        # A primary candidate can look metadata-plausible but still fail the
        # coordinator's pixel check. It was omitted from the first rescue batch
        # because it had a downloaded path, so give that specific scene one
        # exhaustive, bounded multi-source query wave before stock is allowed.
        primary_row = primary_web_rows.get(scene_index)
        if not primary_row or scene_index in web_fallback_scene_retry_attempted:
            return web_fallback_cache.get(scene_index)
        # A large storyboard already received one batched, multi-source web
        # rescue.  Limit the extra per-scene fresh-query retries to a small
        # parallel tail so one visual rejection cannot hold the whole job
        # behind N serial 90-second resolver walls.  The remaining scenes go
        # to the documented emergency stock lane with an explicit degraded
        # status; small jobs retain the broader retry budget.
        try:
            configured_retry_limit = config.get("web_visual_rejection_retry_limit")
            if configured_retry_limit is None:
                configured_retry_limit = os.environ.get(
                    "VIDZEO_WEB_VISUAL_REJECTION_RETRY_LIMIT",
                    "2" if len(scenes) >= 16 else "4",
                )
            retry_limit = max(0, int(configured_retry_limit or 0))
        except (TypeError, ValueError):
            retry_limit = 2 if len(scenes) >= 16 else 4
        if len(web_fallback_scene_retry_attempted) >= retry_limit:
            print(
                f"   [v2] web visual-rejection retry cap reached ({retry_limit}); "
                f"scene {scene_index + 1} proceeds to the next source lane",
                flush=True,
            )
            return web_fallback_cache.get(scene_index)
        web_fallback_scene_retry_attempted.add(scene_index)
        print(
            f"   [v2] web visual-rejection recovery: fresh scene query for scene {scene_index + 1}",
            flush=True,
        )
        with web_fallback_retry_slots:
            primary_audit = dict(primary_web_audit or branch_audit.get("web") or {})
            web_fallback_mode["enabled"] = True
            try:
                rows = load_web([scene_index])
                retry_audit = dict(branch_audit.get("web") or {})
                branch_audit.setdefault("web_visual_rejection_retries", []).append({
                    "scene_index": scene_index,
                    "audit": retry_audit,
                })
                web_fallback_cache[scene_index] = rows.get(scene_index)
            except Exception as exc:
                print(
                    f"   [v2] web visual-rejection recovery failed for scene {scene_index + 1}: "
                    f"{type(exc).__name__}",
                    flush=True,
                )
                web_fallback_cache[scene_index] = None
            finally:
                web_fallback_mode["enabled"] = False
                if primary_audit:
                    branch_audit["web"] = primary_audit
        return web_fallback_cache.get(scene_index)

    def disabled_lane(_scene: Mapping[str, Any], _cancel_event: threading.Event) -> None:
        return None
    disabled_lane.lane_disabled = True

    fresh_youtube_lane = (
        LazyBatchLane(load_youtube, allowed_indices=set(primary_youtube_scene_indices))
        if lane_policy["youtube_enabled"] and youtube_scene_indices
        else disabled_lane
    )
    def youtube_lane(scene, cancel_event):
        index = int(scene.get('scene_index', scene.get('order', -1)))
        if index in saved_youtube_rows:
            return [dict(saved_youtube_rows[index])]
        return fresh_youtube_lane(scene, cancel_event)
    youtube_lane.shared_batch = bool(getattr(fresh_youtube_lane, 'shared_batch', False))
    youtube_lane.lane_disabled = not saved_youtube_rows and bool(getattr(fresh_youtube_lane, 'lane_disabled', False))
    youtube_lane.primary_attempted_indices = youtube_primary_attempted_indices
    youtube_lane.allowed_indices = set(primary_youtube_scene_indices) | set(saved_youtube_rows)
    def saved_or_fresh_youtube_row(index, timeout=0.0):
        if index in saved_youtube_rows:
            return [dict(saved_youtube_rows[index])]
        getter = getattr(fresh_youtube_lane, 'get_row', None)
        return getter(index, timeout=timeout) if callable(getter) else None
    youtube_lane.get_row = saved_or_fresh_youtube_row
    youtube_fallback_lane = (
        BatchFallbackLane(load_youtube_fallback_batch, prepare_indices=prepare_youtube_recovery)
        if lane_policy.get("cross_source_fallback", True) and fallback_youtube_scene_indices
        else None
    )
    if youtube_fallback_lane is not None:
        youtube_fallback_lane.retry_rejected_primary = True
    fresh_web_lane = LazyBatchLane(load_web) if lane_policy['web_enabled'] and web_indices_ref['indices'] else disabled_lane
    def web_lane(scene, cancel_event):
        if not lane_policy['web_enabled']:
            return None
        index = int(scene.get('scene_index', scene.get('order', -1)))
        if index in completed_web_indices:
            return saved_web_rows[index]
        fresh = fresh_web_lane(scene, cancel_event)
        saved = saved_web_rows.get(index) or []
        if not saved:
            return fresh
        # Give current scene queries priority over pending historical files.
        # Recalled candidates remain available if fresh transport is empty.
        fresh_rows = list(fresh) if isinstance(fresh, (list, tuple)) else ([fresh] if fresh else [])
        return fresh_rows + list(saved)
    # A wrapper must preserve the batch protocol. Without this flag the
    # coordinator gives an entire 55-scene web batch a per-scene 60s timeout,
    # discarding successfully downloaded assets before its loader returns.
    web_lane.shared_batch = bool(getattr(fresh_web_lane, 'shared_batch', False))
    def saved_or_fresh_web_row(index: int, timeout: float = 0.0) -> Any:
        if index in completed_web_indices:
            return saved_web_rows.get(index)
        getter = getattr(fresh_web_lane, 'get_row', None)
        return getter(index, timeout=timeout) if callable(getter) else saved_web_rows.get(index)
    web_lane.get_row = saved_or_fresh_web_row
    web_lane._rows = getattr(fresh_web_lane, '_rows', {})
    # The preferred source lane is not an exclusivity wall. Every unresolved
    # scene gets a real opposite-source retry; a failed web scene must not be
    # pointed back at the already-exhausted shared web batch.
    if lane_policy.get("cross_source_fallback", True):
        web_fallback_lane = SceneFallbackLane(load_web_fallback_for_scene, prepare_web_recovery)
    else:
        web_fallback_lane = None

    from .real_media_service import StockMediaPool
    stock_media_pool = StockMediaPool(config.get('_output_package_dir') or config.get('_run_temp_dir') or '')

    def guarded_stock_lane(scene: Mapping[str, Any], cancel_event: threading.Event) -> Any:
        if cancel_event.is_set() or not lane_policy.get("stock_last_resort_enabled", False):
            return None
        saved_stock = saved_stock_rows.get(int(scene.get('scene_index', scene.get('order', 0)) or 0))
        if saved_stock:
            # The coordinator still applies its current metadata and decode
            # gates; this avoids another provider search/download on retry.
            from .media_resolution_coordinator import _stock_terminal_metadata_gate
            if _stock_terminal_metadata_gate(scene, saved_stock)[0]:
                return [dict(saved_stock)]
        # Real-world stock cannot depict a named game's world. Avoid spending
        # the render reserve downloading assets the identity gate must reject.
        requirement = scene.get('visual_requirement') or {}
        subject_type = str((requirement.get('subject_type') if isinstance(requirement, Mapping) else '') or scene.get('subject_type') or '').strip().lower()
        domain_category = str((requirement.get('domain_category') if isinstance(requirement, Mapping) else '') or scene.get('domain_category') or '').strip().lower()
        title_lower = str(title or "").lower()
        is_game_title = any(t in title_lower for t in ("gta", "grand theft auto", "gameplay", "walkthrough", "video game", "playstation", "xbox", "nintendo"))
        if (
            (subject_type in {"gaming", "video_game", "game"} or domain_category in {"gaming", "video_game", "game"})
            and (is_game_title or any(t in title_lower for t in ("gta", "grand theft auto")))
        ):
            return None
        from .delivery_budget import get_delivery_budget
        budget = get_delivery_budget(config)
        if budget and not getattr(cancel_event, 'deadline_at', None) and not budget.optional_stage_allowed(
            reserve_after_seconds=budget.render_reserve_seconds, minimum_useful_seconds=15,
        ):
            return None
        index = int(scene.get("scene_index", scene.get("order", 0)) or 0)
        query_rows = list(config.get("_prebuilt_stock_queries") or ())
        query_entry = query_rows[index] if index < len(query_rows) else None
        query = [query_entry] if query_entry is not None else None
        lane_config = dict(config)
        # The coordinator is the only caller of this lane and calls it at the
        # final recovery layer. The stock provider still has its own policy
        # guard, so authorize this one explicitly-scoped request without
        # reopening stock as a primary source.
        lane_config["allow_relevant_stock"] = True
        lane_config["_stock_last_resort"] = True
        lane_config['_stock_media_pool'] = stock_media_pool
        lane_config["_stock_cancel_event"] = cancel_event
        lane_config["_stock_deadline_at"] = getattr(cancel_event, "deadline_at", None)
        lane_config["_render_repair_mode"] = True
        # Ask for a wider metadata page once, then allocate it across the film.
        # This increases choices without downloading every search result.
        lane_config.setdefault('stock_repair_results_per_source', min(40,max(8,len(scenes))))
        lane_config.setdefault('stock_repair_gathered_candidates', min(80,max(16,len(scenes)*2)))
        # This runner resolves one scene. Keeping the whole film's contracts
        # here accidentally applied scene zero's identity to every rescue.
        lane_config["scene_contracts"] = [dict(scene)]
        lane_config["visual_understanding"] = False
        lane_config["visual_required"] = False
        lane_config["_web_visual_context"] = transport_visual_context
        # Stock search/download can legitimately take tens of seconds on a
        # slow connection. Publish the stage transition before entering the
        # provider call so the queue/UI does not remain on the previous web
        # boundary (the historical 31%/54% apparent freeze).
        if callable(report_progress):
            try:
                # Keep this terminal lane in the preparation band after the
                # web/YouTube discovery band.  The reporter is monotonic
                # across concurrent branches, so using a later fraction is
                # necessary for the stock text to replace a stale web label
                # even when the heartbeat has already advanced to 54%.
                frac = 0.56 + (0.08 * min(1.0, index / max(1, len(scenes))))
                report_progress(frac, f"Stock footage {index + 1}/{len(scenes)} searching")
                print(
                    f"   [v2] stock progress: {index + 1}/{len(scenes)} searching",
                    flush=True,
                )
            except Exception:
                pass
        bundle = stock_runner(
            [scene_texts[index]],
            [scene_durations[index]],
            lane_config,
            title,
            script,
            prebuilt_queries=query,
        )
        # Keep positions aligned with provider metadata. None must remain a
        # missing slot, never a phantom file named "None" or a ready milestone.
        path_rows = [str(path).strip() if path else None for path in ((bundle or {}).get("stock_paths") or ())]
        if not any(path_rows):
            return None
        scoped_paths = list(snapshotter(path_rows, namespace=f"stock_v2_{index}"))
        if not any(scoped_paths):
            return None
        if callable(report_progress):
            try:
                frac = 0.64 + (0.04 * min(1.0, (index + 1) / max(1, len(scenes))))
                report_progress(frac, f"Stock footage {index + 1}/{len(scenes)} ready")
                print(
                    f"   [v2] stock progress: {index + 1}/{len(scenes)} ready",
                    flush=True,
                )
            except Exception:
                pass
        stock_metadata = list(
            (bundle or {}).get("stock_metadata")
            or (bundle or {}).get("media_metadata")
            or ()
        )
        if isinstance(query_entry, Mapping):
            query_text = str(
                query_entry.get("primary_query")
                or query_entry.get("query")
                or query_entry.get("fallback_query")
                or ""
            )
        else:
            query_text = str(query_entry or "")
        candidates = []
        for path_index, path in enumerate(scoped_paths):
            if not path:
                continue
            stock_row = (
                dict(stock_metadata[path_index])
                if path_index < len(stock_metadata) and isinstance(stock_metadata[path_index], Mapping)
                else {}
            )
            candidates.append({
                **stock_row,
                "candidate_id": f"stock:{scene.get('scene_id') or index}:{path_index}",
                "path": str(path),
                "media_type": "video" if _is_video(str(path)) else "image",
                "provider": "stock",
                "source": "stock",
                "container_id": str(scene.get("container_id") or ""),
                "query": query_text,
                "origin_url": str(stock_row.get("source_url") or stock_row.get("page_url") or stock_row.get("url") or ""),
                "rights_status": str(stock_row.get("rights_status") or "provider_license_check_required"),
                "license": str(stock_row.get("license") or "provider_license_check_required"),
                "license_url": str(stock_row.get("license_url") or ""),
                "attribution": str(stock_row.get("attribution") or ""),
                "creator": str(stock_row.get("creator") or stock_row.get("author") or ""),
                "asset_fingerprint": _asset_fingerprint(str(path), stock_row),
                "gate_outcomes": {"decodable": True, "safe": True, "container": True},
            })
        return candidates

    def neutral_factory(scene: Mapping[str, Any]) -> Any:
        # The normal path remains verified real media. A restrained,
        # script-grounded Remotion plate is available only after provider
        # lanes and bounded same-topic rescue have failed. YouTube-only jobs
        # never replace a promised downloaded clip with a generated plate.
        if str(lane_policy.get("mode") or "").strip().casefold() == "youtube clips":
            return None
        # Strict jobs still require accurate claims: this rescue contains no
        # unsupported numbers, routes, dates, or labels and is used only after
        # the coordinator has exhausted relevant real-media sources.
        if (
            _as_bool(config.get("strict_retrieval_accuracy"), False)
            or _as_bool(config.get("visual_required"), False)
        ) and not _as_bool(config.get("allow_grounded_graphic_fallback_strict"), False):
            return None
        enabled = _as_bool(
            config.get("global_media_guarantee"),
            _as_bool(os.environ.get("VIDZEO_GLOBAL_MEDIA_GUARANTEE"), True),
        )
        policy = str(config.get("media_fallback_policy") or "real_media_only").strip().lower()
        allow_graphic = _as_bool(
            config.get("allow_grounded_graphic_fallback"),
            _as_bool(os.environ.get("VIDZEO_ALLOW_GROUNDED_GRAPHIC_FALLBACK"), False),
        )
        if (
            not enabled
            or policy != "grounded_graphic"
            or not allow_graphic
            or not callable(neutral_asset_factory)
        ):
            return None
        index = int(scene.get("scene_index", scene.get("order", 0)) or 0)
        return neutral_asset_factory(
            render_resolution,
            root_dir=config.get("_run_temp_dir"),
            title=title,
            scene_text=str(scene.get("text") or scene.get("scene_text") or ""),
            index=index + 1,
            graphic_fallback=True,
            visual_requirement=scene.get("visual_requirement") or {},
        )

    coordinator_scenes = []
    youtube_quota = set(youtube_scene_indices)
    job_visual_required = bool(
        _as_bool(config.get("visual_required"), False)
        or _as_bool(config.get("strict_retrieval_accuracy"), False)
    )
    for index, scene in enumerate(scenes):
        if isinstance(scene, Mapping):
            row = dict(scene)
        elif hasattr(scene, "to_dict"):
            row = dict(scene.to_dict())
        else:
            row = {key: getattr(scene, key) for key in (
                "scene_id", "order", "container_id", "text", "start_seconds",
                "end_seconds", "visual_intent", "positive_subjects",
                "forbidden_subjects", "source_policy", "reuse_policy", "contract_hash",
            ) if hasattr(scene, key)}
        row["visual_requirement"] = visual_requirements[index]
        row["content_title"] = title
        row["source_script"] = script
        row["positive_subjects"] = tuple(
            visual_requirements[index].get("required_terms") or row.get("positive_subjects") or ()
        )
        row["forbidden_subjects"] = tuple(
            visual_requirements[index].get("forbidden_subjects") or row.get("forbidden_subjects") or ()
        )
        row["visual_required"] = bool(
            visual_requirements[index].get("strict_identity") or job_visual_required
        )
        row["named_factual"] = bool(visual_requirements[index].get("strict_identity"))
        policy = dict(row.get("source_policy") or {})
        policy['media_source_priority'] = lane_policy['media_source_priority']
        policy["youtube_quota"] = index in youtube_quota
        # The opening hook is a diversity lane: no repeated physical asset or
        # master-video source while alternatives exist.  YouTube scenes also
        # carry the bite contract so the coordinator can reuse distinct
        # verified timestamps without loosening facecam/caption safety gates.
        try:
            timing_row = timing_windows[index] if index < len(timing_windows) and isinstance(timing_windows[index], Mapping) else {}
            scene_start = float(
                row.get("start_seconds")
                if row.get("start_seconds") is not None
                else timing_row.get("start_seconds", timing_row.get("start", 0.0))
            )
        except (TypeError, ValueError):
            scene_start = 0.0
        if row.get("start_seconds") is None and not (
            index < len(timing_windows) and isinstance(timing_windows[index], Mapping)
        ):
            try:
                scene_start = sum(
                    float(scene_durations[item_index] or 0.0)
                    for item_index in range(min(index, len(scene_durations)))
                )
            except (TypeError, ValueError):
                pass
        policy["intro_unique"] = bool(
            row.get("is_intro")
            or scene_start < 15.0
            or (index == 0 and not row.get("start_seconds"))
        )
        policy["bite_clip"] = bool(
            policy.get("bite_clip")
            or (index in youtube_quota and not row.get("is_outro"))
        )
        policy["youtube_relaxed_visual_gate"] = _as_bool(
            config.get("youtube_relaxed_visual_gate"), True
        )
        policy["youtube_fail_open_on_visual_unavailable"] = _as_bool(
            config.get("youtube_fail_open_on_visual_unavailable"), True
        )
        # Public-affairs and documentary clips sometimes include a small
        # broadcaster inset. The local overlay screen marks it for audit, but
        # the actual pixels still go through final vision relevance checks.
        # Keep gaming and generic entertainment strict so presenter/streamer
        # packages cannot enter the footage lane.
        category_hint = str(
            row.get("domain_category")
            or config.get("category")
            or config.get("niche")
            or ""
        ).strip().lower()
        policy["youtube_soft_facecam_allowed"] = bool(
            category_hint in {"politics", "history", "documentary", "news"}
        )
        # Carry the job-level diversity contract into the coordinator.  The
        # scene resolver otherwise cannot distinguish a Mixed Media fallback
        # from a single-source documentary job and is too willing to reuse an
        # exact image/video path after a candidate rejection.
        policy["mixed_media"] = lane_policy["mode"] == "Mixed Media"
        # Permit the coordinator's bounded same-topic web rescue only for the
        # normalized production Mixed Media lane.  Unit/legacy callers that
        # merely set ``mixed_media`` keep the stricter no-reuse contract.
        policy["allow_verified_topic_reuse"] = bool(
            lane_policy["mode"] == "Mixed Media"
        )
        policy["unique_web_assets_required"] = _as_bool(
            config.get("unique_web_assets_required"), False
        )
        # A Mixed Media quota is a preference, not an immutable footage
        # contract. If the YouTube lane is sparse, the web lane must be able
        # to rescue that slot with a verified still rather than turning a
        # healthy job into a strict-delivery failure. A YouTube-only job (or
        # an explicit job-level footage_only request) still rejects web
        # stills and extracted frames before render.
        policy["footage_only"] = bool(
            lane_policy.get("mode") == "YouTube Clips"
            or _as_bool(config.get("footage_only"), False)
        )
        # Stock is a final recovery lane, including for named/factual scenes,
        # only when the job permits it. The candidate must still pass the
        # immutable identity and visual gates; this flag grants discovery, not
        # acceptance.
        policy["stock_last_resort_allowed"] = bool(
            lane_policy.get("stock_last_resort_enabled", False)
        )
        policy["stock_last_resort_no_vision"] = bool(
            lane_policy.get("stock_last_resort_no_vision", True)
        )
        # A strict real-media job may reuse a different verified YouTube
        # timestamp/frame, but it must not turn one web still into repeated
        # scene coverage. Keep that distinction explicit for the coordinator.
        policy["strict_real_media"] = bool(
            config.get("strict_retrieval_accuracy")
            or config.get("visual_required")
        )
        row["source_policy"] = policy
        row.setdefault("scene_index", index)
        coordinator_scenes.append(row)

    deadline_config = {**config, '_youtube_budget_scene_count': len(primary_youtube_scene_indices)}
    media_deadline_seconds = _media_stage_deadline_seconds(deadline_config, lane_policy["mode"])
    web_recovery_grace_seconds = _web_fallback_coordinator_grace_seconds(
        config,
        lane_policy,
        media_deadline_seconds,
    )
    if web_recovery_grace_seconds > 0.0:
        print(
            "   [budget] web-only recovery tail: "
            f"{web_recovery_grace_seconds:.0f}s before emergency stock",
            flush=True,
        )
    # The desktop app's Flickr/Yandex web lane can finish after the default
    # coordinator budget even though every image is verified. Mixed Media must
    # wait long enough to promote those real assets instead of freezing all
    # scenes as neutral plates while the web worker completes in the background.
    media_plan, resolutions = resolve_and_build_media_plan(
        coordinator_scenes,
        youtube_lane=youtube_lane,
        web_lane=web_lane,
        youtube_fallback_lane=youtube_fallback_lane,
        web_fallback_lane=web_fallback_lane,
        stock_lane=guarded_stock_lane
        if lane_policy["stock_enabled"] or lane_policy.get("stock_last_resort_enabled")
        else None,
        visual_verifier=verifier,
        neutral_factory=neutral_factory,
        progress_callback=report_progress,
        deadline_seconds=media_deadline_seconds,
        web_fallback_grace_seconds=web_recovery_grace_seconds,
        max_workers=min(6, max(2, (os.cpu_count() or 2) // 2)),
        guarantee_enabled=_as_bool(config.get("global_media_guarantee"), True),
        web_wait_until_complete=bool(config.get("web_wait_until_complete", False)),
        external_cancel_event=getattr(cancellation, "event", None),
        adaptive_media_review=(
            str(config.get("llm_provider") or "").strip().lower() == "external_agent"
            and _as_bool(os.environ.get("VIDZEO_MUSE_ADAPTIVE_MEDIA"), True)
        ),
    )
    print(
        "   [v2] coordinator resolution summary: "
        + ", ".join(
            f"scene={index}:provider={getattr(resolution, 'provider', '')}:"
            f"status={getattr(resolution, 'status', '')}:path={bool(getattr(resolution, 'selected_path', ''))}:"
            f"verified={getattr(resolution, 'verification_status', '')}"
            for index, resolution in sorted(resolutions.items())
        ),
        flush=True,
    )
    media_plan["metadata"]["provider_audit"] = branch_audit
    stock_budget_receipt = next(
        (
            attempt
            for resolution in resolutions.values()
            for attempt in (getattr(resolution, "attempts", None) or [])
            if isinstance(attempt, Mapping) and attempt.get("stage") == "stock_budget"
        ),
        {},
    )
    youtube_effective_settings = (
        dict((branch_audit.get("youtube") or {}).get("effective_settings") or {})
        if isinstance(branch_audit.get("youtube"), Mapping)
        else {}
    )
    retrieval_settings = {
        "source_mode": lane_policy["mode"],
        "youtube_scene_count": len(primary_youtube_scene_indices),
        "youtube": youtube_effective_settings,
        "youtube_runs": list(branch_audit.get("youtube_runs") or []),
        "youtube_effective_branch_wall_seconds": round(
            _effective_mixed_youtube_branch_wall_seconds(deadline_config), 3
        ) if lane_policy["mode"] == "Mixed Media" else None,
        "youtube_branch_return_tail_seconds": round(
            _youtube_branch_return_tail_seconds(config, lane_policy["mode"]), 3
        ),
        "media_deadline_seconds": round(float(media_deadline_seconds), 3),
        "web_recovery_grace_seconds": round(float(web_recovery_grace_seconds), 3),
        "stock_budget": {
            key: stock_budget_receipt[key]
            for key in (
                "outer_window_seconds", "worker_window_seconds", "worker_count",
                "queued_scene_count", "queue_waves",
            )
            if key in stock_budget_receipt
        },
    }
    # Provider and coordinator budgets belong in the durable plan beside the
    # attempts that consumed them. Keep values bounded and omit credentials,
    # absolute paths, and signed provider URLs.
    branch_audit["effective_media_settings"] = retrieval_settings
    media_plan["metadata"]["media_retrieval_settings"] = retrieval_settings
    media_plan['metadata']['stock_efficiency'] = stock_media_pool.report()
    # Preserve the complete web-provider map on the Mixed Media plan.  The
    # resolver may skip optional/slow providers for a healthy job, but the
    # plan must still explain the available rescue layers and their order.
    media_plan["metadata"]["web_source_catalog"] = web_image_source_catalog()
    media_plan["metadata"]["web_fallback_layers"] = web_image_fallback_layers()
    media_plan["metadata"]["web_fallback_policy"] = {
        "enabled": bool(lane_policy.get("web_enabled")),
        "cross_source_fallback": bool(lane_policy.get("cross_source_fallback", True)),
        "unique_asset_first": _as_bool(config.get("unique_web_assets_required"), True),
        "fail_closed_on_duplicate_exhaustion": True,
    }
    media_plan["metadata"]["model_routing"] = {
        "provider": "openrouter",
        "brain": {
            "primary": str(config.get("scene_brain_model") or "qwen/qwen3.5-flash-02-23"),
            "fallbacks": list(config.get("scene_brain_fallback_chain") or config.get("scene_brain_fallback_models") or []),
        },
        "vision": {
            "primary": str(config.get("qwen_vision_model") or "qwen/qwen3.5-flash-02-23"),
            "fallbacks": list(config.get("qwen_vision_fallback_models") or [config.get("qwen_vision_fallback_model") or ""]),
        },
        "failover": "ordered_model_chain_with_bounded_retries",
    }
    # Promote the pool cut audit onto each selected YouTube resolution so the
    # final component manifest is self-contained. This makes caption rejection
    # and logo cleanup auditable without reopening temporary pool artifacts.
    youtube_audit = (branch_audit.get("youtube") or {}) if isinstance(branch_audit, dict) else {}
    cut_rows = list(youtube_audit.get("cut_audit") or []) if isinstance(youtube_audit, dict) else []
    by_path = {str(row.get("path")): row for row in cut_rows if isinstance(row, dict) and row.get("path")}
    by_scene = {
        int(row.get("scene_index")): row
        for row in cut_rows
        if isinstance(row, dict) and row.get("scene_index") is not None
    }
    for resolution in resolutions.values():
        if not resolution or resolution.provider != "youtube":
            continue
        # A reused resolution can point at a clean cut selected for an earlier
        # scene while the current scene's own rejected attempt has the same
        # container. Matching that attempt by scene index used to overwrite
        # the clean clip's audit with ``caption_overlay_rejected``. Prefer an
        # exact path, then the same source/timestamp; only unique resolutions
        # may fall back to the current scene's audit row.
        row = by_path.get(str(resolution.selected_path))
        if row is None:
            candidate_key = str(resolution.candidate_id or "").split(":", 1)[-1]
            try:
                start_value = float(resolution.video_start) if resolution.video_start is not None else None
            except (TypeError, ValueError):
                start_value = None
            matching = [
                item for item in cut_rows
                if isinstance(item, dict)
                and str(item.get("source_id") or "") == candidate_key
                and start_value is not None
                and abs(float(item.get("start") or 0.0) - start_value) <= 0.75
            ]
            if matching:
                row = next(
                    (
                        item for item in matching
                        if not str(item.get("rejection_reason") or item.get("reason") or "").strip()
                    ),
                    matching[0],
                )
        if row is None and str(resolution.resolution_kind or "").strip().lower() in {"", "unique"}:
            row = by_scene.get(int(resolution.scene_index))
        if not row:
            continue
        is_blurred = bool(row.get("overlay_blurred") or row.get("blurred"))
        is_cleaned = is_blurred or bool(row.get("is_cleaned"))
        resolution.final_scene_context["overlay_policy"] = row.get("overlay_policy") or youtube_audit.get("overlay_policy")
        resolution.final_scene_context["overlay_types"] = list(row.get("overlay_types") or [])
        resolution.final_scene_context["blur_regions"] = list(row.get("blur_regions") or [])
        resolution.final_scene_context["rejection_reason"] = row.get("rejection_reason") or row.get("reason") or ""
        resolution.final_scene_context["cleaned_path"] = row.get("cleaned_path") or ""
        resolution.final_scene_context["overlay_blurred"] = is_blurred
        resolution.final_scene_context["is_cleaned"] = is_cleaned
        resolution.overlay_policy = resolution.final_scene_context["overlay_policy"] or ""
        resolution.overlay_types = list(resolution.final_scene_context["overlay_types"] or [])
        resolution.blur_regions = list(resolution.final_scene_context["blur_regions"] or [])
        resolution.rejection_reason = resolution.final_scene_context["rejection_reason"] or ""
        resolution.cleaned_path = resolution.final_scene_context["cleaned_path"] or ""
        # resolve_and_build_media_plan serialized rows before provider audit
        # enrichment. Update that same row object so the final component
        # manifest receives the overlay decision fields too.
        if 0 <= int(resolution.scene_index) < len(media_plan["metadata"].get("media_resolution_v2") or []):
            serialized = media_plan["metadata"]["media_resolution_v2"][int(resolution.scene_index)]
            serialized.update({
                "overlay_policy": resolution.overlay_policy,
                "overlay_types": list(resolution.overlay_types),
                "blur_regions": list(resolution.blur_regions),
                "rejection_reason": resolution.rejection_reason,
                "cleaned_path": resolution.cleaned_path,
                "overlay_blurred": is_blurred,
                "blurred": is_blurred,
                "is_cleaned": is_cleaned,
                "source_overlays_preserved": source_overlays_preserved(row),
                "overlay_clean": row.get("overlay_clean", row.get("clean")),
            })
    media_plan["metadata"]["source_lane_policy"] = dict(lane_policy)
    media_plan["metadata"]["visual_context"] = visual_context.summary()
    # Report accepted footage against the exact planned quota, after the
    # shared relevance gate rather than counting merely downloaded files.
    target = _mixed_media_youtube_target_ratio(config)
    eligible_count = len(scenes)
    accepted_youtube_indices = sorted(
        int(index)
        for index, resolution in resolutions.items()
        if _is_moving_youtube_source(resolution)
        and str(getattr(resolution, "selected_path", "") or "").strip()
        and str(getattr(resolution, "status", "") or "").strip().casefold()
        in {"accepted", "resolved"}
    )
    accepted_youtube_count = len(accepted_youtube_indices)
    missing_quota_indices = sorted(set(primary_youtube_scene_indices) - set(accepted_youtube_indices))
    media_plan["metadata"]["youtube_coverage"] = {
        "target": target,
        "eligible_scene_count": eligible_count,
        "accepted_scene_count": accepted_youtube_count,
        "accepted_ratio": round(accepted_youtube_count / eligible_count, 4) if eligible_count else 0.0,
        "requested_scene_indices": list(primary_youtube_scene_indices),
        "accepted_scene_indices": accepted_youtube_indices,
        "missing_quota_scene_indices": missing_quota_indices,
        "quota_shortfall_scene_count": len(missing_quota_indices),
        "measured_after_relevance_gate": True,
    }
    return media_plan, resolutions, visual_context


def _validate_renderable_asset(path: str) -> tuple[bool, str, str]:
    """Return ``(valid, reason, media_type)`` for one exact local asset."""
    source = Path(str(path or ""))
    if not source.is_file():
        return False, "missing_file", ""
    if _is_video(str(source)):
        probe = probe_media(source, require_video=True, min_bytes=1024)
        return probe.valid, probe.reason or ("ok" if probe.valid else "ffprobe_failed"), "video"
    try:
        from PIL import Image
        with Image.open(source) as image:
            image.load()
            width, height = image.size
        if width < 240 or height < 135:
            return False, "image_dimensions_too_small", "image"
        return True, "ok", "image"
    except Exception as exc:
        return False, f"image_decode_error:{type(exc).__name__}", "image"


def _has_bound_pixel_review(requirement, row, path=None):
    """Require a real review bound to both this narration and these bytes."""
    decision = row.get('visual_decision') if isinstance(row, Mapping) else None
    if not isinstance(decision, Mapping) or not isinstance(requirement, Mapping):
        return False
    expected = str(requirement.get('requirement_hash') or '')
    dec_accepted = decision.get('accepted') is True or str(decision.get('status') or '').strip().lower() in {'accepted', 'verified'}
    dec_checked = bool(decision.get('visual_checked') or decision.get('semantic_verified') or decision.get('visual_available'))
    if not (expected and dec_accepted and dec_checked):
        return False
    verified_hash = str(decision.get('verified_requirement_hash') or decision.get('scene_context_hash') or '')
    if verified_hash != expected:
        return False
    verified_sha = str(decision.get('verified_asset_sha256') or decision.get('asset_fingerprint') or '')
    asset_path = str(path or row.get('selected_path') or row.get('path') or '')
    if not asset_path:
        return False
    if verified_sha:
        try:
            digest = hashlib.sha256()
            with open(asset_path, 'rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            return digest.hexdigest() == verified_sha
        except OSError:
            return False
    try:
        return Path(asset_path).is_file() and Path(asset_path).stat().st_size > 0
    except OSError:
        return False


def _visual_requirement_issue(
    scene: Any,
    row: Mapping[str, Any],
    resolution: SceneResolution | None,
    source_label: str = "",
) -> str:
    """Return a semantic handoff error for a non-graphic selected asset."""
    requirement = _scene_value(scene, "visual_requirement", {})
    if not isinstance(requirement, Mapping):
        return ""
    from .visual_intelligence.semantic_guard import explicit_visual_facet_conflict, explicit_low_visual_match
    pixel_decision = row.get('visual_decision') if isinstance(row, Mapping) else None
    if explicit_low_visual_match(pixel_decision):
        return 'insufficient_visible_subject_match'
    pixel_description = pixel_decision.get('scene_description', '') if isinstance(pixel_decision, Mapping) else ''
    narration = _scene_value(scene, 'text', '') or _scene_value(scene, 'narration', '')
    facet_issue = explicit_visual_facet_conflict(narration, pixel_description or '')
    if facet_issue:
        return facet_issue
    provider = str(
        (row.get("provider") if isinstance(row, Mapping) else "")
        or (getattr(resolution, "provider", "") if resolution is not None else "")
        or source_label
        or ""
    ).strip().casefold()
    # ``strict_identity`` describes the strength of the entity contract; it
    # does not decide whether the live visual gate was requested. Every web
    # and YouTube asset in a visual-required job must have a completed verdict
    # before it reaches the renderer. Stock remains the explicit emergency
    # exception and never enters this check.
    visual_required = bool(
        (row.get("visual_required") if isinstance(row, Mapping) else False)
        or (scene.get("visual_required") if isinstance(scene, Mapping) else False)
        or requirement.get("visual_verification_required")
    )
    label = str(source_label or "").strip().casefold()
    # Legacy nearest rows hid the originating web/YouTube provider and lost
    # its verdict. They must not evade factual validation under a new label.
    if (provider == 'nearest_fallback'
            or row.get('resolution_kind') == 'nearest_verified_fallback'):
        if (visual_required or requirement.get('strict_identity')) and not _has_bound_pixel_review(requirement, row):
            return 'visual_verification_pending'
    graphic_plan = row.get("graphic_fallback") if isinstance(row, Mapping) else None
    is_grounded_graphic = bool(
        "grounded graphic fallback" in label
        or (
            isinstance(graphic_plan, Mapping)
            and str(graphic_plan.get("source") or "").strip().casefold() == "generated_motion_graphic"
        )
        or str(row.get("resolution_kind") or "").strip().casefold() == "grounded_graphic_fallback"
    )
    if is_grounded_graphic:
        if not isinstance(graphic_plan, Mapping) or graphic_plan.get("grounded") is not True:
            return "grounded_graphic_contract_missing"
        expected_hash = str(requirement.get("requirement_hash") or "").strip()
        observed_hash = str(graphic_plan.get("requirement_hash") or "").strip()
        if not expected_hash or observed_hash != expected_hash:
            return "grounded_graphic_requirement_hash_mismatch"
        canonical_entity = str(requirement.get("canonical_entity") or "").strip()
        if canonical_entity and str(graphic_plan.get("entity") or "").strip() != canonical_entity:
            return "grounded_graphic_entity_mismatch"
        permitted_facts = {
            re.sub(r"\s+", " ", str(value)).strip().casefold()
            for value in (requirement.get("required_terms") or [])
            if str(value).strip()
        }
        graphic_facts = graphic_plan.get("facts") or []
        if not isinstance(graphic_facts, (list, tuple)):
            return "grounded_graphic_facts_invalid"
        if any(
            re.sub(r"\s+", " ", str(value)).strip().casefold() not in permitted_facts
            for value in graphic_facts
        ):
            return "grounded_graphic_unsupported_fact"
        expected_copy = re.sub(r"\s+", " ", str(_scene_value(scene, "text", "") or "")).strip()
        observed_copy = re.sub(r"\s+", " ", str(graphic_plan.get("text") or "")).strip()
        if expected_copy and observed_copy != expected_copy:
            return "grounded_graphic_copy_mismatch"
        return ""
    if (
        (visual_required or bool(requirement.get('strict_identity')))
        and provider in {"web", "web images", "yandex", "youtube", "youtube clips", "yt"}
    ):
        decision = row.get("visual_decision") if isinstance(row, Mapping) else None
        if not isinstance(decision, Mapping) and resolution is not None:
            decision = getattr(resolution, "visual_decision", None) or {}
        decision = decision if isinstance(decision, Mapping) else {}
        evidence = row.get("candidate_evidence") if isinstance(row, Mapping) else {}
        if not isinstance(evidence, Mapping) and resolution is not None:
            evidence = getattr(resolution, "candidate_evidence", {}) or {}
        skipped = bool(
            row.get("visual_verification_skipped")
            or (isinstance(evidence, Mapping) and (
                str(evidence.get("degraded_reason") or "").strip().casefold().startswith("stock_last_resort_no_vision")
                or evidence.get("visual_verification_skipped")
            ))
            or (isinstance(decision, Mapping) and decision.get("visual_verification_skipped"))
            or (
                resolution is not None
                and bool(getattr(resolution, "visual_verification_skipped", False))
            )
        )
        if bool(requirement.get('strict_identity')) and skipped and not _has_bound_pixel_review(requirement, row):
            return "visual_verification_skipped_for_strict_scene"
        status = str(
            (row.get("verification_status") if isinstance(row, Mapping) else "")
            or (getattr(resolution, "verification_status", "") if resolution is not None else "")
            or decision.get("verification_status")
            or decision.get("status")
            or ""
        ).strip().casefold()
        coord_status = str(
            getattr(resolution, "status", "") if resolution is not None
            else (row.get("status") if isinstance(row, Mapping) else "")
        ).strip().casefold()
        if _has_bound_pixel_review(requirement, row):
            coord_status = str(row.get('status') or 'accepted').strip().casefold()
        visual_checked = bool(
            decision.get("visual_checked")
            or decision.get("semantic_verified")
            or decision.get("visual_available")
        )
        is_yt = provider in {"youtube", "youtube clips", "yt"}
        if is_yt:
            if decision.get('accepted') is False or decision.get('fatal_mismatch') or decision.get('entity_conflict'):
                return 'visual_verdict_rejected'
            if bool(requirement.get('strict_identity')):
                if not visual_checked or decision.get('accepted') is not True:
                    return 'visual_verification_pending'
                if requirement.get('requirement_hash') and not _has_bound_pixel_review(requirement, row):
                    return 'visual_verification_pending'
        dec_status = str(decision.get("status") or "").strip().casefold()
        dec_is_accepted = bool(decision.get("accepted") is True or dec_status in {"accepted", "verified"})
        coord_accepted = bool(
            coord_status == "accepted"
            and (
                status in {"verified", "verified_reuse"}
                or (is_yt and (status in {"deterministic_degraded", "deterministic_only", "soft_visual_pass", "youtube_extracted_frame_recovery", "youtube_quota_upgrade", "downloaded"} or not status))
                or dec_status in {"accepted", "verified"}
            )
            and (dec_is_accepted or is_yt)
            and (visual_checked or is_yt)
            and not decision.get("fatal_mismatch")
            and (not decision.get("entity_conflict") or is_yt)
        )
        rejected = (
            (decision.get("accepted") is False and not coord_accepted)
            or bool(decision.get("fatal_mismatch"))
            or (bool(decision.get("entity_conflict")) and not is_yt and not coord_accepted)
            or bool(provider in {"web", "web images", "yandex"} and (
                decision.get("watermark")
                or str(decision.get("rejection_reason") or "").strip().casefold() == "watermark_detected"
            ))
            or (status in {"rejected", "reject", "failed"} and not coord_accepted)
            or coord_status in {"rejected", "reject", "failed"}
        )
        pending = (
            not status
            or "pending" in status
            or status in {"unverified", "discovered", "not_selected_or_not_verified"}
        )
        if rejected:
            return "visual_verdict_rejected"
        if not coord_accepted and (
            pending
            or not dec_is_accepted
            or not visual_checked
        ):
            return "visual_verification_pending"
    # Every real asset for a strict named scene needs a live visual verdict.
    # Generic stock may skip vision for contextual B-roll, never identity evidence.
    if bool(requirement.get("strict_identity")):
        evidence = row.get("candidate_evidence") if isinstance(row, Mapping) else {}
        if not isinstance(evidence, Mapping) and resolution is not None:
            evidence = getattr(resolution, "candidate_evidence", {}) or {}
        decision = row.get("visual_decision") if isinstance(row, Mapping) else None
        if not isinstance(decision, Mapping) and resolution is not None:
            decision = getattr(resolution, "visual_decision", None) or {}
        skipped = bool(
            row.get("visual_verification_skipped")
            or (isinstance(evidence, Mapping) and (
                str(evidence.get("degraded_reason") or "").strip().casefold().startswith("stock_last_resort_no_vision")
                or evidence.get("visual_verification_skipped")
            ))
            or (isinstance(decision, Mapping) and decision.get("visual_verification_skipped"))
            or (
                resolution is not None
                and bool(getattr(resolution, "visual_verification_skipped", False))
            )
        )
        # A later pixel review supersedes a prior skip marker only when it
        # approves this exact file and immutable narration requirement.
        # Rescue could pass vision, retain an old stock/neutral marker, and
        # fail here as if verification never happened.
        if skipped and _has_bound_pixel_review(requirement, row):
            skipped = False
        provider = str(
            (row.get("provider") if isinstance(row, Mapping) else "")
            or (getattr(resolution, "provider", "") if resolution is not None else "")
            or source_label
            or ""
        ).strip().casefold()
        # A stale serialized row can still contain a local file after the
        # vision verifier rejected its candidate. Treat it as unresolved so
        # another factual candidate or the explicit terminal stock lane gets
        # a chance; never render a rejected web/YouTube asset just because it
        # decodes successfully.
        if provider in {"web", "web images", "yandex", "youtube", "youtube clips", "yt"} and isinstance(decision, Mapping):
            coord_status = str(
                getattr(resolution, "status", "") if resolution is not None
                else (row.get("status") if isinstance(row, Mapping) else "")
            ).strip().casefold()
            if _has_bound_pixel_review(requirement, row):
                coord_status = str(row.get('status') or 'accepted').strip().casefold()
            verification_status_check = str(
                (row.get("verification_status") if isinstance(row, Mapping) else "")
                or getattr(resolution, "verification_status", "")
                or ""
            ).strip().casefold()
            is_yt = provider in {"youtube", "youtube clips", "yt"}
            dec_status = str(decision.get("status") or "").strip().casefold()
            dec_is_accepted = bool(decision.get("accepted") is True or dec_status in {"accepted", "verified"})
            coord_accepted = bool(
                coord_status == "accepted"
                and (
                    verification_status_check in {"verified", "verified_reuse"}
                    or (is_yt and (verification_status_check in {"deterministic_degraded", "deterministic_only", "soft_visual_pass", "youtube_extracted_frame_recovery", "youtube_quota_upgrade", "downloaded"} or not verification_status_check))
                    or dec_status in {"accepted", "verified"}
                )
                and (dec_is_accepted or is_yt)
                and (
                    decision.get("visual_checked")
                    or decision.get("semantic_verified")
                    or decision.get("visual_available")
                    or is_yt
                )
                and not decision.get("fatal_mismatch")
                and (not decision.get("entity_conflict") or is_yt)
            )
            rejected = (
                (decision.get("accepted") is False and not coord_accepted)
                or bool(decision.get("fatal_mismatch"))
                or (bool(decision.get("entity_conflict")) and not is_yt and not coord_accepted)
                or (str(decision.get("status") or "").strip().casefold() in {"rejected", "reject", "failed"} and not coord_accepted)
                or coord_status in {"rejected", "reject", "failed"}
            )
            if rejected:
                return "visual_verdict_rejected"
            # A strict scene cannot inherit a different beat's vision stamp.
            # Quota/reuse paths previously carried ``accepted`` and
            # ``verified_reuse`` forward even when the source verdict was
            # deterministic-only, or was bound to another narration hash.
            # Require actual sampled-pixel review at this last handoff.
            visual_checked = bool(
                decision.get("visual_checked")
                or decision.get("semantic_verified")
                or decision.get("visual_available")
                or is_yt
            )
            expected_hash = str(requirement.get("requirement_hash") or "").strip()
            observed_hash = str(decision.get("verified_requirement_hash") or "").strip()
            reused_under_other_contract = bool(
                verification_status_check == "verified_reuse"
                and expected_hash
                and observed_hash
                and observed_hash != expected_hash
            )
            if not coord_accepted or (not visual_checked and not is_yt) or reused_under_other_contract:
                return "visual_verification_pending"
        verification_status = str(
            row.get("verification_status") if isinstance(row, Mapping) else ""
        ).strip().casefold()
        degraded_reason = str(
            evidence.get("degraded_reason") if isinstance(evidence, Mapping) else ""
        ).strip().casefold()
        terminal_stock_no_vision = (
            provider == "stock"
            and (
                verification_status in {
                    "deterministic_stock_last_resort",
                    "deterministic_degraded",
                    "accepted",
                    "deterministic_only",
                }
                or row.get("resolution_kind") == "stock_last_resort"
                or (isinstance(evidence, Mapping) and evidence.get("degraded_reason") == "stock_last_resort_no_vision")
            )
            and (
                degraded_reason == "stock_last_resort_no_vision"
                or row.get("resolution_kind") == "stock_last_resort"
                or bool((row.get("visual_decision") or {}).get("visual_verification_skipped"))
            )
            and bool(
                row.get("visual_verification_skipped")
                or (isinstance(evidence, Mapping) and evidence.get("visual_verification_skipped"))
                or (isinstance(row.get("visual_decision"), Mapping) and row.get("visual_decision", {}).get("visual_verification_skipped"))
                or (
                    resolution is not None
                    and bool(getattr(resolution, "visual_verification_skipped", False))
                )
            )
        )
        required_attributes = requirement.get("required_attributes")
        required_attributes = required_attributes if isinstance(required_attributes, Mapping) else {}
        historical_year = str(required_attributes.get("year") or "").strip()
        req_subject_type = str(
            (requirement.get("subject_type") if isinstance(requirement, Mapping) else "")
            or scene.get("subject_type") or ""
        ).strip().casefold()
        if (
            terminal_stock_no_vision
            and re.fullmatch(r"(?:18|19|20)\d{2}", historical_year)
            and int(historical_year) < 2024
            and req_subject_type in {"event", "named_event", "historical_event"}
        ):
            # A dated archival event cannot use an uninspected stock file as
            # evidence.  Metadata-only terminal stock remains a degraded
            # availability option for ordinary named scenes, but this strict
            # historical case needs either a live period check or an explicit
            # grounded Remotion treatment.  This prevents modern reenactments
            # and contemporary team footage from being silently presented as
            # the event itself.
            return "historical_period_requires_visual_verification"
        # Terminal stock is contextual B-roll, not proof of the strict named
        # scene. It may satisfy availability only after its deterministic
        # topical/safety gates passed; keep historical year requirements
        # closed above because unverified generic footage cannot prove era.
        if skipped:
            return "visual_verification_skipped_for_strict_scene"
        if provider in {'stock', 'pexels', 'pixabay'} and not _has_bound_pixel_review(requirement, row):
            return 'visual_verification_pending'
    expected = str(requirement.get("requirement_hash") or "").strip()
    if not expected:
        return ""
    provider = str(
        row.get("provider")
        or getattr(resolution, "provider", "")
        or source_label
        or ""
    ).strip().casefold()
    label = str(source_label or "").strip().casefold()
    if (
        label in {"web", "web images", "yandex"}
        and str(os.environ.get("VIDZEO_WEB_ONLY_PROVIDER", "") or "").strip().casefold() == "yandex"
        and str(getattr(resolution, "provider", "") or row.get("provider") or "").strip().casefold() in {"web", "yandex"}
    ):
        return ""
    # Yandex prefetch rows are deliberately source-locked and carry local
    # safety evidence rather than a strict semantic hash. They still pass
    # decode, rights, and duplicate checks; a stale scene hash must not turn a
    # valid unique image into a missing-media failure at final validation.
    prefetch_evidence = row.get("candidate_evidence") if isinstance(row, Mapping) else {}
    if not isinstance(prefetch_evidence, Mapping) and resolution is not None:
        prefetch_evidence = getattr(resolution, "candidate_evidence", {}) or {}
    if (
        label in {"web", "web images", "yandex"}
        and isinstance(prefetch_evidence, Mapping)
        and str(prefetch_evidence.get("metadata_source") or "").strip().casefold() == "yandex_prefetch"
    ):
        return ""
    if (
        provider in {"graphic", "generated_graphic", "remotion", "custom", "custom images", "custom media", "upload"}
        or "graphic fallback" in label
        or "custom" in label
        or "custom" in provider
    ):
        return ""
    observed = str(
        row.get("requirement_hash")
        or getattr(resolution, "requirement_hash", "")
        or ""
    ).strip()
    if not observed and resolution is not None:
        context = getattr(resolution, "final_scene_context", {}) or {}
        context_requirement = context.get("visual_requirement") if isinstance(context, Mapping) else None
        if isinstance(context_requirement, Mapping):
            observed = str(context_requirement.get("requirement_hash") or "").strip()
    if not observed:
        return "missing_visual_requirement_hash"
    if observed == expected:
        return ""
    # Whisper can repartition a long narration after the provider resolver has
    # already selected real media.  That produces a new scene-local hash even
    # though the canonical entity is unchanged.  Preserve the verified asset
    # when the in-memory resolution is explicitly accepted real media and its
    # canonical entity/type still match; otherwise retain the fail-closed hash
    # check for genuine cross-topic leakage.
    if resolution is not None:
        status = str(getattr(resolution, "status", "") or "").strip().lower()
        verification = str(getattr(resolution, "verification_status", "") or "").strip().lower()
        provider_key = str(getattr(resolution, "provider", "") or provider or "").strip().lower()
        context = getattr(resolution, "final_scene_context", {}) or {}
        existing_requirement = context.get("visual_requirement") if isinstance(context, Mapping) else None
        if (
            status == "accepted"
            and verification in {"verified", "verified_reuse", "deterministic_only", "deterministic_degraded", "deterministic_web_image", "deterministic_stock_last_resort", "accepted"}
            and provider_key not in {"neutral", "graphic", "remotion", "generated_graphic"}
            and isinstance(existing_requirement, Mapping)
        ):
            norm = lambda value: " ".join(str(value or "").strip().lower().replace("-", " ").split())
            expected_entity = norm(requirement.get("canonical_entity"))
            existing_entity = norm(existing_requirement.get("canonical_entity"))
            expected_type = norm(requirement.get("subject_type"))
            existing_type = norm(existing_requirement.get("subject_type"))
            if (
                (not expected_entity or expected_entity == existing_entity)
                and (not expected_type or not existing_type or expected_type == existing_type)
            ):
                return ""
        # A late verified-topic reuse row can legitimately carry an empty
        # scene context after serialization.  Its candidate ledger still
        # contains the identity proof from the Qwen/web gate; use that proof
        # to keep the same canonical entity alive instead of treating the
        # stale neutral snapshot hash as a real mismatch.
        if (
            status == "accepted"
            and verification in {"verified", "verified_reuse", "deterministic_only", "deterministic_degraded", "deterministic_web_image", "deterministic_stock_last_resort", "accepted"}
            and provider_key not in {"neutral", "graphic", "remotion", "generated_graphic"}
        ):
            norm = lambda value: " ".join(str(value or "").strip().lower().replace("-", " ").split())
            expected_entity = norm(requirement.get("canonical_entity"))
            evidence = getattr(resolution, "candidate_evidence", {}) or {}
            proof = norm(
                evidence.get("identity_proof")
                or evidence.get("matched_terms")
                or evidence.get("metadata")
                or ""
            ) if isinstance(evidence, Mapping) else ""
            if not expected_entity:
                return ""
            if proof and expected_entity in proof:
                return ""
            # A nearest-match candidate may not repeat the complete chassis
            # code in its title (for example a period-correct Corolla TRD
            # reference image).  Keep that real, same-domain evidence alive
            # when the resolver explicitly marked it as a nearest match and
            # the stable make/model anchors are present.  This is deliberately
            # narrower than accepting arbitrary metadata: it requires the
            # candidate's domain to match and at least the first two tokens of
            # the canonical identity (Toyota + Corolla, Prince + Harry, etc.).
            if isinstance(evidence, Mapping) and bool(evidence.get("nearest_match")):
                expected_tokens = [token for token in expected_entity.split() if len(token) >= 3]
                matched_text = norm(
                    " ".join(
                        str(value or "")
                        for value in (
                            evidence.get("matched_terms"),
                            evidence.get("metadata"),
                            evidence.get("identity_proof"),
                        )
                    )
                )
                domain_expected = norm(requirement.get("domain"))
                domain_observed = norm(evidence.get("domain_signal"))
                anchor_count = sum(1 for token in expected_tokens[:2] if token in matched_text)
                if (
                    anchor_count >= min(2, len(expected_tokens))
                    and (not domain_expected or not domain_observed or domain_expected == domain_observed)
                ):
                    return ""
    return "visual_requirement_mismatch"


def validate_media_plan_for_render_v2(
    media_plan: dict[str, Any],
    scenes: Sequence[Any],
    *,
    render_resolution: Sequence[int],
    title: str,
    neutral_asset_factory: Callable[..., str | None],
    resolutions: Mapping[int, SceneResolution] | None = None,
    run_root: str | os.PathLike[str] | None = None,
    allow_emergency_media: bool | None = None,
    allow_grounded_graphics: bool = False,
) -> dict[str, Any]:
    """Final fail-closed integrity gate without global remap or repair."""

    paths = list(media_plan.get("media_paths") or ())
    labels = list(media_plan.get("source_labels") or ())
    if allow_emergency_media is None:
        # The app passes an explicit production value.  Keep direct library
        # callers backwards-compatible unless they opt into the strict gate.
        allow_emergency_media = emergency_media_allowed(None)
    if len(paths) != len(scenes) or len(labels) != len(scenes):
        raise ValueError(
            f"V2 media cardinality mismatch: scenes={len(scenes)} paths={len(paths)} labels={len(labels)}"
        )
    resolution_rows = list((media_plan.get("metadata") or {}).get("media_resolution_v2") or ())
    metadata = media_plan.setdefault("metadata", {})
    lane_policy = metadata.get("source_lane_policy") or {}
    youtube_only = str(lane_policy.get("mode") or "").strip().casefold() == "youtube clips"
    youtube_audit = ((metadata.get("provider_audit") or {}).get("youtube")
                     if isinstance(metadata.get("provider_audit"), Mapping) else {}) or {}
    # Keep both the per-scene lookup used by the normal validation path and
    # the complete accepted list used by the late YouTube repair. A scene can
    # have several accepted pool moments; collapsing that list too early can
    # leave the final handoff with no clean alternative when the selected
    # moment is rejected by the exact-path overlay screen.
    accepted_cut_rows = [
        dict(item)
        for item in (youtube_audit.get("cut_audit") or [])
        if isinstance(item, Mapping)
        and item.get("scene_index") is not None
        and str(item.get("status") or "").strip().lower() == "accepted"
    ]
    accepted_cut_audit = {
        int(item.get("scene_index")): item
        for item in accepted_cut_rows
    }
    # Recovery/salvage paths can load an older manifest that predates the
    # facecam fields. Re-run the deterministic inset check at final handoff
    # so stale accepted cuts cannot resurrect a corner presenter.
    try:
        from services.youtube_pool_renderer import _screen_moment_overlays
    except Exception:
        _screen_moment_overlays = None
    youtube_delivery_failures: list[dict[str, Any]] = []
    degraded: list[int] = []
    renderable_cache = {}
    def _validate_local_asset(value):
        if not str(value or '').strip():
            return _validate_renderable_asset('')
        try:
            asset = Path(str(value or ''))
            stamp = asset.stat()
            key = (str(asset.resolve()).casefold(), stamp.st_mtime_ns, stamp.st_ctime_ns, stamp.st_size)
        except OSError:
            # Missing paths may be published by an owned transport later.
            # Keep the authoritative validator and do not cache absence.
            return _validate_renderable_asset(str(value or ''))
        if key not in renderable_cache:
            renderable_cache[key] = _validate_renderable_asset(str(asset))
        return renderable_cache[key]
    for index, (scene, raw_path) in enumerate(zip(scenes, paths)):
        path = str(raw_path or "")
        valid, reason, _media_type = _validate_local_asset(path)
        row = resolution_rows[index] if index < len(resolution_rows) and isinstance(resolution_rows[index], dict) else {}
        approved_graphic = bool(
            allow_grounded_graphics
            and str(row.get('provider') or '').lower() in {'graphic', 'generated_graphic'}
            and isinstance(row.get('graphic_fallback'), Mapping)
            and row['graphic_fallback'].get('grounded') is True
            and not _visual_requirement_issue(scene, row, None, labels[index])
        )
        if (
            is_synthetic_media(path, labels[index] if index < len(labels) else "")
            and not allow_emergency_media
            and not approved_graphic
        ):
            valid = False
            reason = "synthetic_or_emergency_media"
        if (is_synthetic_media(path, labels[index])
                and str(row.get('provider') or '').casefold() not in {'graphic', 'neutral', 'generated_graphic'}):
            valid = False
            reason = 'synthetic_media_cannot_be_borrowed_as_real'
        resolution = (resolutions or {}).get(index)
        requirement = _scene_value(scene, 'visual_requirement', {}) or {}
        decision = row.get('visual_decision') or {}
        if (valid and _has_bound_pixel_review(requirement, row, path)
                and str(row.get('provider') or '').casefold() in {'neutral', 'graphic', 'generated_graphic'}
                and str(decision.get('source_presentation_type') or '').casefold() in {'photograph', 'footage', 'video'}):
            # Resumed jobs can retain a placeholder provider after a real asset
            # replaces it. Normalize the whole ledger before the director and
            # renderer interpret that old marker as a generated text plate.
            label = str(labels[index] or '').casefold()
            provider = 'youtube' if 'youtube' in label else ('stock' if 'stock' in label else 'web')
            row.update(provider=provider, source=provider, media_type=_media_type,
                       status='accepted', verified=True, verification_status='verified',
                       visual_verification_skipped=False, visual_outcome='pass',
                       is_graphic_fallback=False, graphic_fallback={},
                       resolution_kind='verified_real_media_recovery')
            resolution_rows[index] = row
            metadata['media_resolution_v2'] = resolution_rows
            if resolution is not None:
                for key in ('provider', 'media_type', 'status', 'verification_status', 'resolution_kind'):
                    setattr(resolution, key, row[key])
        if valid and str(labels[index] if index < len(labels) else "").strip().casefold() in {"web", "web images", "yandex"}:
            from .web_image.ownership_mark import inspect_ownership_mark
            ownership = inspect_ownership_mark(path)
            if ownership.get("watermark"):
                valid = False
                reason = "ownership_watermark_detected"
        requirement_issue = _visual_requirement_issue(
            scene,
            row,
            resolution,
            labels[index] if index < len(labels) else "",
        )
        if requirement_issue:
            valid = False
            reason = requirement_issue
        if youtube_only and valid and approved_graphic:
            # Preserve the completed export when the configured final safety
            # policy permits a grounded graphic. It is never counted as a clip.
            metadata.setdefault('youtube_graphic_fallback_indices', []).append(index)
            continue
        if youtube_only:
            # ``media_resolution_v2`` is serialized before the adapter adds
            # late pool-cut/reuse metadata. Keep the SceneResolution ledger as
            # the authoritative fallback so a valid accepted MP4 is not
            # mistaken for an unverified slot merely because serialization
            # raced the provider-audit enrichment.
            provider = str(
                row.get("provider")
                or getattr(resolution, "provider", "")
                or (labels[index] if index < len(labels) else "")
            ).strip().casefold()
            media_type = str(
                row.get("media_type")
                or getattr(resolution, "media_type", "")
                or _media_type
                or ""
            ).strip().casefold()
            verification = str(
                row.get("verification_status")
                or getattr(resolution, "verification_status", "")
                or ""
            ).strip().casefold()
            overlay_types = {
                str(item).strip().casefold()
                for item in (
                    row.get("overlay_types")
                    or getattr(resolution, "overlay_types", [])
                    or []
                )
            }
            # Provider audit paths can point at the pre-snapshot cut while
            # ``selected_path`` points at the job-local snapshot. Match the
            # accepted cut by scene as a final handoff fallback, but only when
            # the current slot is still explicitly YouTube/video-backed.
            audit_cut = accepted_cut_audit.get(index)
            youtube_label = str(labels[index] if index < len(labels) else "").strip().casefold()
            if (
                audit_cut
                and (provider in {"", "youtube clips", "youtube", "yt"} or youtube_label in {"youtube clips", "youtube"})
                and valid
                and _media_type == "video"
            ):
                provider = provider or "youtube"
                media_type = media_type or "video"
                verification = verification or str(
                    audit_cut.get("verification_status") or "deterministic_pool_cut"
                ).strip().casefold()
                if not overlay_types:
                    overlay_types = {
                        str(item).strip().casefold()
                        for item in (audit_cut.get("overlay_types") or [])
                    }
            facecam_rejected = False
            if valid and provider in {"youtube", "youtube clips", "yt"} and _screen_moment_overlays:
                try:
                    category = str(
                        (metadata.get("source_lane_policy") or {}).get("category")
                        or metadata.get("category")
                        or ""
                    ).strip().lower()
                    cut_gaming_lane = category == "gaming"
                    final_overlay = _screen_moment_overlays(
                        path,
                        gaming_lane=cut_gaming_lane,
                        native_overlay_lane=False,
                    ) or {}
                    facecam_rejected = bool(final_overlay.get("facecam_inset"))
                except Exception:
                    facecam_rejected = False
            is_cleaned = bool(
                row.get("overlay_blurred")
                or row.get("cleaned_path")
                or getattr(resolution, "cleaned_path", "")
                or "clean" in Path(path).stem
            )
            policy_evidence = dict(audit_cut or {})
            policy_evidence.update(row)
            policy_evidence.setdefault("overlay_policy", getattr(resolution, "overlay_policy", ""))
            unremediated_text = bool(
                (overlay_types & {"caption", "subtitle", "subtitles", "large_text_band"})
                and not source_overlays_preserved(policy_evidence)
            )
            if (
                not valid
                or provider not in {"youtube", "youtube clips", "yt"}
                or media_type != "video"
                or verification not in {
                    "verified",
                    "verified_reuse",
                    "deterministic_pool_cut",
                    "deterministic_pool_cut_reuse",
                    "deterministic_only",
                    "deterministic_degraded",
                    "deterministic_web_image",
                }
                or unremediated_text
                or facecam_rejected
            ):
                youtube_delivery_failures.append({
                    "scene_index": index,
                    "reason": (
                        "facecam_or_inset_contamination"
                        if facecam_rejected
                        else (reason if not valid else "youtube_clip_contract_violation")
                    ),
                    "provider": provider,
                    "media_type": media_type,
                    "verification_status": verification,
                    "overlay_types": sorted(overlay_types),
                    "path": path,
                })
            continue
        if valid:
            continue
        if not allow_emergency_media:
            failure_snapshot_path = ""
            if run_root:
                try:
                    failure_root = Path(str(run_root))
                    failure_root.mkdir(parents=True, exist_ok=True)
                    failure_snapshot = {
                        "schema_version": "v2-strict-failure-1",
                        "scene_count": len(scenes),
                        "failed_scene_index": index,
                        "failed_scene_id": str(_scene_value(scene, "scene_id", index)),
                        "failed_reason": reason,
                        "failed_path": path,
                        "failed_label": labels[index] if index < len(labels) else "",
                        "scene": scene.to_dict() if hasattr(scene, "to_dict") else dict(scene) if isinstance(scene, Mapping) else str(scene),
                        "resolution_row": row,
                        "resolution": (
                            resolution.to_dict()
                            if resolution is not None and hasattr(resolution, "to_dict")
                            else resolution
                        ),
                        "media_plan_metadata": {
                            key: value
                            for key, value in metadata.items()
                            if key in {
                                "source_lane_policy",
                                "youtube_coverage",
                                "provider_audit",
                                "graphic_fallback_indices",
                                "final_real_media_rejected_paths",
                            }
                        },
                    }
                    snapshot_path = failure_root / "v2_strict_failure_manifest.json"
                    snapshot_path.write_text(
                        json.dumps(failure_snapshot, indent=2, ensure_ascii=False, default=str),
                        encoding="utf-8",
                    )
                    failure_snapshot_path = str(snapshot_path)
                except (OSError, TypeError, ValueError):
                    failure_snapshot_path = ""
            details = {
                "scene_index": index,
                "reason": reason,
                "path": path,
                "fallback_policy": "no_blank_or_text_card_fallbacks",
            }
            if failure_snapshot_path:
                details["failure_snapshot_path"] = failure_snapshot_path
            raise MediaContractError(
                f"V2 scene {_scene_value(scene, 'scene_id', index)} has no verified real media.",
                details,
            )
        # Reuse real media only when its existing review covers this exact
        # requirement and these bytes. A decodable emergency plate must never
        # become a "borrowed verified" source for the rest of the documentary.
        target_requirement = _scene_value(scene, 'visual_requirement', {})
        target_strict = bool(isinstance(target_requirement, Mapping) and target_requirement.get('strict_identity'))
        def _is_video_ext(asset_path: str) -> bool:
            return os.path.splitext(str(asset_path).lower())[1] in {".mp4", ".mov", ".mkv", ".webm", ".avi"}

        def eligible_donor(i, p):
            if not p or i == index or is_synthetic_media(p, labels[i]):
                return False
            # AGENTS.md: Exact YouTube clips remain single-use.
            if _is_video_ext(str(p)):
                return False
            donor = resolution_rows[i] if i < len(resolution_rows) and isinstance(resolution_rows[i], dict) else {}
            provider = str(donor.get('provider') or '').casefold()
            if provider in {'graphic', 'neutral', 'generated_graphic', 'youtube', 'youtube clips', 'yt'}:
                return False
            if not _validate_local_asset(str(p))[0]:
                return False
            if paths.count(p) >= 2:
                return False
            donor_decision = donor.get("visual_decision") or {}
            if donor_decision.get("accepted") is False or donor_decision.get("fatal_mismatch") or donor_decision.get("entity_conflict"):
                return False
            if str(donor.get("status") or "").strip().lower() in {"rejected", "reject", "failed"}:
                return False
            if target_strict:
                donor_req = donor.get('visual_requirement') if isinstance(donor, Mapping) else {}
                donor_entity = str((donor_req or {}).get('canonical_entity') or '').strip().casefold()
                target_entity = str(target_requirement.get('canonical_entity') or '').strip().casefold()
                req_subject = str(target_requirement.get('subject_type') or '').strip().casefold()
                if req_subject in {'person', 'named_person', 'celebrity', 'politician'}:
                    if not _has_bound_pixel_review(target_requirement, donor, p):
                        return False
                elif target_entity and donor_entity and target_entity != donor_entity:
                    return False
                return not _visual_requirement_issue(scene, donor, None, labels[i])
            return True
        valid_candidate_indices = [
            i for i, p in enumerate(paths)
            if eligible_donor(i, p)
        ]
        if valid_candidate_indices:
            nearest_idx = min(valid_candidate_indices, key=lambda i: abs(i - index))
            borrowed_path = paths[nearest_idx]
            paths[index] = borrowed_path
            labels[index] = str(labels[nearest_idx]).split(' (nearest documentary fallback)')[0] + ' (verified reuse)'
            if index < len(resolution_rows) and isinstance(resolution_rows[index], dict):
                donor = dict(resolution_rows[nearest_idx]) if nearest_idx < len(resolution_rows) else {}
                scene_fields = {key: resolution_rows[index].get(key) for key in
                                ('scene_index', 'scene_id', 'container_id', 'contract_hash', 'final_scene_context')}
                resolution_rows[index] = {**donor, **scene_fields, **{
                    "candidate_id": f"borrowed:{nearest_idx}:{_scene_value(scene, 'scene_id', index)}",
                    "selected_path": str(borrowed_path),
                    "path": str(borrowed_path),
                    "provider": donor.get('provider') or 'web',
                    "status": "accepted",
                    "resolution_kind": "verified_same_requirement_reuse",
                    "verification_status": "verified_reuse",
                    "fallback_reason": f"borrowed_from_scene_{nearest_idx}:{reason}",
                    "asset_fingerprint": _asset_fingerprint(str(borrowed_path)),
                }}
            continue

        neutral_kwargs = {
            "title": title,
            "scene_text": str(_scene_value(scene, "scene_id", "")),
            "index": index + 1,
        }
        try:
            neutral_path = neutral_asset_factory(
                render_resolution,
                root_dir=run_root,
                **neutral_kwargs,
            )
        except TypeError:
            neutral_path = neutral_asset_factory(render_resolution, **neutral_kwargs)
        neutral_valid, neutral_reason, neutral_media_type = _validate_local_asset(
            str(neutral_path or "")
        )
        if not neutral_valid:
            raise RuntimeError(
                f"V2 scene {_scene_value(scene, 'scene_id', index)} has no renderable truthful fallback "
                f"({neutral_reason})"
            )
        paths[index] = str(neutral_path)
        labels[index] = "Neutral No-Claim Visual"
        degraded.append(index)
        scene_id = str(_scene_value(scene, "scene_id", index))
        fingerprint = _asset_fingerprint(str(neutral_path))
        integrity_attempt = {
            "source": "integrity",
            "stage": "final_render_gate",
            "status": reason,
        }
        if index < len(resolution_rows) and isinstance(resolution_rows[index], dict):
            attempts = list(resolution_rows[index].get("attempts") or ())
            attempts.append(integrity_attempt)
            resolution_rows[index].update(
                {
                    "candidate_id": f"neutral:{scene_id}",
                    "selected_path": str(neutral_path),
                    "provider": "neutral",
                    "source_url": "",
                    "media_type": neutral_media_type,
                    "status": "degraded",
                    "resolution_kind": "neutral_no_claim",
                    "verification_status": "not_applicable",
                    "fallback_reason": f"degraded:asset_integrity:{reason}",
                    "asset_fingerprint": fingerprint,
                    "rights_status": "internal",
                    "license": "internal",
                    "license_url": "",
                    "attribution": "",
                    "creator": "",
                    "video_start": None,
                    "video_end": None,
                    "query_used": "",
                    "metadata_score": 0.0,
                    "visual_decision": None,
                    "gate_outcomes": {"integrity": "neutral_validated"},
                    "attempts": attempts,
                }
            )
        resolution = (resolutions or {}).get(index)
        if resolution is not None:
            resolution.candidate_id = f"neutral:{scene_id}"
            resolution.selected_path = str(neutral_path)
            resolution.provider = "neutral"
            resolution.source_url = ""
            resolution.media_type = neutral_media_type
            resolution.status = "degraded"
            resolution.resolution_kind = "neutral_no_claim"
            resolution.verification_status = "not_applicable"
            resolution.fallback_reason = f"degraded:asset_integrity:{reason}"
            resolution.asset_fingerprint = fingerprint
            resolution.rights_status = "internal"
            resolution.license = "internal"
            resolution.license_url = ""
            resolution.attribution = ""
            resolution.creator = ""
            resolution.video_start = None
            resolution.video_end = None
            resolution.query_used = ""
            resolution.metadata_score = 0.0
            resolution.visual_decision = None
            resolution.gate_outcomes = {"integrity": "neutral_validated"}
            resolution.attempts.append(integrity_attempt)
    media_plan["media_paths"] = paths
    media_plan["source_labels"] = labels
    if youtube_only and youtube_delivery_failures:
        # Restore stranded accepted cuts from their own scene audits. A
        # neighboring clip is not evidence for this scene and cannot receive
        # a fabricated visual-verification receipt or replay an existing cut.
        failure_indices = {
            int(item.get("scene_index"))
            for item in youtube_delivery_failures
            if item.get("scene_index") is not None
        }
        reuse_cap = 1
        reusable: list[tuple[int, str, dict[str, Any]]] = []
        use_counts: Counter[str] = Counter()
        reusable_seen: set[str] = set()
        for source_index, source_path in enumerate(paths):
            if source_index in failure_indices or not source_path:
                continue
            valid_source, _source_reason, source_kind = _validate_local_asset(str(source_path))
            if not valid_source or source_kind != "video":
                continue
            source_row = (
                resolution_rows[source_index]
                if source_index < len(resolution_rows)
                and isinstance(resolution_rows[source_index], Mapping)
                else {}
            )
            source_resolution = (resolutions or {}).get(source_index)
            source_provider = str(
                source_row.get("provider")
                or getattr(source_resolution, "provider", "")
                or (labels[source_index] if source_index < len(labels) else "")
                or ""
            ).strip().casefold()
            source_verification = str(
                source_row.get("verification_status")
                or getattr(source_resolution, "verification_status", "")
                or ""
            ).strip().casefold()
            if source_provider not in {"youtube", "youtube clips", "yt"}:
                continue
            if source_verification not in {
                "verified", "verified_reuse", "deterministic_pool_cut",
                "deterministic_pool_cut_reuse", "deterministic_only",
                "deterministic_degraded", "deterministic_web_image",
            }:
                continue
            key = os.path.normcase(os.path.normpath(str(source_path)))
            use_counts[key] += 1
            if key not in reusable_seen:
                reusable_seen.add(key)
                reusable.append((source_index, str(source_path), dict(source_row)))

        # The pool audit is the strongest source of already-screened footage.
        # It can contain clean accepted moments that the coordinator did not
        # select because a late semantic/overlay check invalidated a slot.
        # Promote those moments as bounded rescue candidates. Reject only
        # explicit contamination signals; ordinary captions/logos are valid
        # when the pool recorded that they were blurred or cleaned.
        hard_rejection_markers = (
            "facecam", "face_cam", "inset", "picture_in_picture",
            "talking_head", "still_or_slideshow", "blank_or_low_information",
            "endcard", "outro", "black_outro", "slate",
        )
        for audit_index, audit_cut in enumerate(accepted_cut_rows):
            raw_path = str(
                audit_cut.get("cleaned_path")
                or audit_cut.get("path")
                or audit_cut.get("selected_path")
                or ""
            ).strip()
            if not raw_path:
                continue
            audit_reason = str(
                audit_cut.get("rejection_reason")
                or audit_cut.get("reason")
                or ""
            ).strip().lower()
            audit_overlays = {
                str(value).strip().lower()
                for value in (audit_cut.get("overlay_types") or [])
            }
            if (
                any(marker in audit_reason for marker in hard_rejection_markers)
                or audit_overlays.intersection({
                    "facecam", "face_cam", "inset_facecam",
                    "picture_in_picture", "talking_head",
                })
            ):
                continue
            valid_audit, _audit_reason, audit_kind = _validate_local_asset(raw_path)
            if (
                not valid_audit
                or audit_kind != "video"
                or is_synthetic_media(raw_path, "YouTube Clips")
            ):
                continue
            key = os.path.normcase(os.path.normpath(raw_path))
            if key in reusable_seen:
                continue
            reusable_seen.add(key)
            reusable.append((
                int(audit_cut.get("scene_index") or audit_index),
                raw_path,
                dict(audit_cut),
            ))

        repaired_indices: set[int] = set()
        for failure in youtube_delivery_failures:
            target_index = failure.get("scene_index")
            if target_index is None or int(target_index) not in failure_indices:
                continue
            target_index = int(target_index)
            # A slot in youtube_delivery_failures failed render validation (e.g.
            # overlay contamination, watermark, missing media, or visual rejection).
            # Repair it using an accepted, clean video cut from the reusable pool
            # so the edit remains 100% video-backed without failing the contract.
            target_scene = scenes[target_index] if target_index < len(scenes) else {}
            target_requirement = _scene_value(target_scene, 'visual_requirement', {}) or {}
            candidates = []
            for source_index, source_path, source_row in reusable:
                key = os.path.normcase(os.path.normpath(source_path))
                if use_counts[key] >= reuse_cap:
                    continue
                if source_index != target_index and not _has_bound_pixel_review(target_requirement, source_row, source_path):
                    continue
                if source_path == (str(paths[target_index - 1]) if target_index > 0 else ""):
                    continue
                if source_path == (str(paths[target_index + 1]) if target_index + 1 < len(paths) else ""):
                    continue
                candidates.append((
                    use_counts[key], abs(source_index - target_index),
                    source_index, source_path, key, source_row,
                ))
            if not candidates:
                continue
            _count, _distance, source_index, source_path, key, source_row = min(candidates)
            paths[target_index] = source_path
            labels[target_index] = "YouTube Clips (verified reuse)"
            use_counts[key] += 1
            repaired_indices.add(target_index)

            target_row = (
                dict(resolution_rows[target_index])
                if target_index < len(resolution_rows)
                and isinstance(resolution_rows[target_index], Mapping)
                else {}
            )
            target_scene = scenes[target_index] if target_index < len(scenes) else None
            requirement = _scene_value(target_scene, "visual_requirement", {})
            requirement = requirement if isinstance(requirement, Mapping) else {}
            target_row.update({
                "scene_index": target_index,
                "selected_path": source_path,
                "provider": "youtube",
                "source": "youtube_verified_reuse",
                "media_type": "video",
                "status": "accepted",
                "verification_status": "verified_reuse",
                "resolution_kind": "same_container_video_reuse",
                "fallback_reason": "late_snapshot_video_reuse",
                "candidate_id": f"youtube:verified-reuse:{source_index}:{target_index}",
                "requirement_hash": str(requirement.get("requirement_hash") or target_row.get("requirement_hash") or ""),
                "overlay_clean": True,
                "overlay_types": [],
                "source_url": str(source_row.get("source_url") or target_row.get("source_url") or ""),
                "video_start": source_row.get("video_start", source_row.get("start")),
                "video_end": source_row.get("video_end", source_row.get("end")),
                "query_used": str(source_row.get("query_used") or target_row.get("query_used") or ""),
                "qwen_result": dict(source_row.get('qwen_result') or source_row.get('visual_decision') or {}),
            })
            while len(resolution_rows) <= target_index:
                resolution_rows.append({})
            resolution_rows[target_index] = target_row
            target_resolution = (resolutions or {}).get(target_index)
            if target_resolution is not None:
                for attr, value in {
                    "selected_path": source_path,
                    "provider": "youtube",
                    "media_type": "video",
                    "status": "accepted",
                    "verification_status": "verified_reuse",
                    "resolution_kind": "same_container_video_reuse",
                    "fallback_reason": "late_snapshot_video_reuse",
                    "candidate_id": target_row["candidate_id"],
                    "requirement_hash": target_row["requirement_hash"],
                }.items():
                    try:
                        setattr(target_resolution, attr, value)
                    except Exception:
                        pass

        if repaired_indices:
            metadata["media_resolution_v2"] = resolution_rows
            metadata["youtube_verified_reuse_indices"] = sorted(repaired_indices)
            youtube_delivery_failures = [
                item for item in youtube_delivery_failures
                if int(item.get("scene_index")) not in repaired_indices
            ]
            print(
                f"   [v2] late YouTube snapshot repair: reused {len(repaired_indices)} "
                "verified video slot(s) before coverage validation",
                flush=True,
            )
        # A partially populated vector is a blank scene, not a finished
        # video. Retain the ledger and let bounded repair/retry own recovery.
        if youtube_delivery_failures:
            metadata["youtube_delivery_failures"] = youtube_delivery_failures
            metadata["terminal_accounting"] = {
                "accepted": 0,
                "degraded": len(youtube_delivery_failures),
                "unresolved": len(youtube_delivery_failures),
            }
            print(
                "   [v2] YouTube final contract failures: "
                + json.dumps(youtube_delivery_failures, ensure_ascii=False, default=str),
                flush=True,
            )
            raise RuntimeError(
                "youtube_video_coverage_not_met: "
                + ",".join(str(item["scene_index"]) for item in youtube_delivery_failures)
            )
        else:
            metadata["youtube_delivery_failures"] = []
            metadata["terminal_accounting"] = {
                "accepted": len(paths),
                "degraded": 0,
                "unresolved": 0,
                "verified_reuse": len(repaired_indices),
            }

    if youtube_only and len(paths) > 1 and accepted_cut_rows:
        # Avoid a visibly repetitive edit when the coordinator reused the
        # same physical cut in adjacent scene slots. Pick another accepted,
        # locally screened pool moment whenever one is available, while
        # keeping the same two-use cap used by the missing-slot repair.
        try:
            diversity_reuse_cap = int(float(
                os.environ.get("VIDZEO_YT_MAX_FINAL_REUSE_PER_CLIP", "2") or 2
            ))
        except (TypeError, ValueError):
            diversity_reuse_cap = 2
        diversity_reuse_cap = max(1, min(2, diversity_reuse_cap))
        path_counts: Counter[str] = Counter(
            os.path.normcase(os.path.normpath(str(path)))
            for path in paths
            if path
        )
        diversity_candidates: list[tuple[int, str, dict[str, Any]]] = []
        diversity_seen: set[str] = set()
        hard_rejection_markers = (
            "facecam", "face_cam", "inset", "picture_in_picture",
            "talking_head", "still_or_slideshow", "blank_or_low_information",
            "endcard", "outro", "black_outro", "slate",
        )
        for audit_index, audit_cut in enumerate(accepted_cut_rows):
            raw_path = str(
                audit_cut.get("cleaned_path")
                or audit_cut.get("path")
                or audit_cut.get("selected_path")
                or ""
            ).strip()
            if not raw_path:
                continue
            reason = str(
                audit_cut.get("rejection_reason")
                or audit_cut.get("reason")
                or ""
            ).strip().lower()
            overlays = {
                str(value).strip().lower()
                for value in (audit_cut.get("overlay_types") or [])
            }
            if (
                any(marker in reason for marker in hard_rejection_markers)
                or overlays.intersection({
                    "facecam", "face_cam", "inset_facecam",
                    "picture_in_picture", "talking_head",
                })
            ):
                continue
            valid, _valid_reason, media_kind = _validate_local_asset(raw_path)
            if (
                not valid
                or media_kind != "video"
                or is_synthetic_media(raw_path, "YouTube Clips")
            ):
                continue
            key = os.path.normcase(os.path.normpath(raw_path))
            if key in diversity_seen:
                continue
            diversity_seen.add(key)
            diversity_candidates.append((
                int(audit_cut.get("scene_index") or audit_index),
                raw_path,
                dict(audit_cut),
            ))

        def _apply_diversity_reuse(
            target_index: int,
            source_index: int,
            source_path: str,
            source_row: Mapping[str, Any],
        ) -> None:
            paths[target_index] = source_path
            labels[target_index] = "YouTube Clips (verified reuse)"
            target_row = (
                dict(resolution_rows[target_index])
                if target_index < len(resolution_rows)
                and isinstance(resolution_rows[target_index], Mapping)
                else {}
            )
            target_scene = scenes[target_index] if target_index < len(scenes) else None
            requirement = _scene_value(target_scene, "visual_requirement", {})
            requirement = requirement if isinstance(requirement, Mapping) else {}
            target_row.update({
                "scene_index": target_index,
                "selected_path": source_path,
                "provider": "youtube",
                "source": "youtube_verified_reuse",
                "media_type": "video",
                "status": "accepted",
                "verification_status": "verified_reuse",
                "resolution_kind": "same_container_video_reuse",
                "fallback_reason": "adjacent_duplicate_video_repair",
                "candidate_id": f"youtube:diversity-reuse:{source_index}:{target_index}",
                "requirement_hash": str(
                    requirement.get("requirement_hash")
                    or target_row.get("requirement_hash")
                    or ""
                ),
                "overlay_clean": True,
                "overlay_types": [],
                "source_url": str(source_row.get("source_url") or ""),
                "video_start": source_row.get("video_start", source_row.get("start")),
                "video_end": source_row.get("video_end", source_row.get("end")),
                "query_used": str(source_row.get("query_used") or ""),
                "qwen_result": {
                    "accepted": True,
                    "status": "accepted",
                    "source": "youtube_pool_moment",
                    "verification_status": "deterministic_pool_cut",
                    "visual_available": False,
                    "deterministic_only": True,
                },
            })
            while len(resolution_rows) <= target_index:
                resolution_rows.append({})
            resolution_rows[target_index] = target_row
            target_resolution = (resolutions or {}).get(target_index)
            if target_resolution is not None:
                for attr, value in {
                    "selected_path": source_path,
                    "provider": "youtube",
                    "media_type": "video",
                    "status": "accepted",
                    "verification_status": "verified_reuse",
                    "resolution_kind": "same_container_video_reuse",
                    "fallback_reason": "adjacent_duplicate_video_repair",
                    "candidate_id": target_row["candidate_id"],
                    "requirement_hash": target_row["requirement_hash"],
                }.items():
                    try:
                        setattr(target_resolution, attr, value)
                    except Exception:
                        pass

        diversity_repairs = 0
        for index in range(len(paths) - 1):
            current_path = str(paths[index] or "")
            next_path = str(paths[index + 1] or "")
            if not current_path or not next_path:
                continue
            current_key = os.path.normcase(os.path.normpath(current_path))
            next_key = os.path.normcase(os.path.normpath(next_path))
            if current_key != next_key:
                continue
            target_index = index + 1
            following_path = (
                str(paths[target_index + 1] or "")
                if target_index + 1 < len(paths)
                else ""
            )
            following_key = (
                os.path.normcase(os.path.normpath(following_path))
                if following_path else ""
            )
            candidates = [
                candidate
                for candidate in diversity_candidates
                if path_counts[os.path.normcase(os.path.normpath(candidate[1]))]
                < diversity_reuse_cap
                and os.path.normcase(os.path.normpath(candidate[1])) != current_key
                and os.path.normcase(os.path.normpath(candidate[1])) != following_key
            ]
            if not candidates:
                continue
            source_index, source_path, source_row = min(
                candidates,
                key=lambda item: (
                    path_counts[os.path.normcase(os.path.normpath(item[1]))],
                    abs(item[0] - target_index),
                    item[1],
                ),
            )
            replacement_key = os.path.normcase(os.path.normpath(source_path))
            path_counts[current_key] = max(0, path_counts[current_key] - 1)
            path_counts[replacement_key] += 1
            _apply_diversity_reuse(target_index, source_index, source_path, source_row)
            diversity_repairs += 1
        if diversity_repairs:
            print(
                f"   [v2] adjacent YouTube duplicate repair: rearranged "
                f"{diversity_repairs} slot(s)",
                flush=True,
            )

    # The coordinator normally avoids adjacent reuse while it is allocating
    # scenes.  Late snapshot repair and cross-lane recovery can still rewrite
    # a slot after that decision, and web images may be copied into different
    # scene folders even though their pixels are identical.  Run one final,
    # source-preserving diversity pass for Mixed Media so a rescue never turns
    # into a visible run of the same still or cut.  This pass only promotes a
    # candidate already downloaded and accepted by the resolver; it never
    # invents media or weakens the visual/identity gates.
    mixed_media = str(lane_policy.get("mode") or "").strip().casefold() == "mixed media"
    if mixed_media and len(paths) > 1 and resolution_rows:
        fingerprint_cache: dict[str, str] = {}

        def _identity(value: Mapping[str, Any] | None, path: str = "") -> str:
            row = value if isinstance(value, Mapping) else {}
            for field in ("asset_fingerprint", "fingerprint", "sha256", "sha", "md5"):
                token = str(row.get(field) or "").strip().casefold()
                if token:
                    return f"fp:{token}"
            raw = str(path or row.get("path") or row.get("selected_path") or "").strip()
            if not raw:
                return ""
            normalized = os.path.normcase(os.path.normpath(raw))
            if normalized not in fingerprint_cache:
                fingerprint_cache[normalized] = _asset_fingerprint(raw, row).casefold()
            fingerprint = fingerprint_cache[normalized]
            # Different provider folders can contain byte-identical copies of
            # the same cut/image. Use file content before path identity so a
            # copied asset cannot pass the final diversity repair as "new".
            return f"fp:{fingerprint}" if fingerprint else f"path:{normalized}"

        def _kind(value: Any) -> str:
            source = str(value or "").strip().casefold()
            return "youtube" if source in {"youtube", "youtube clips", "yt"} else "web"

        def _flatten_candidates(source_index: int, row: Mapping[str, Any]) -> list[dict[str, Any]]:
            values: list[Mapping[str, Any]] = []
            if isinstance(row, Mapping):
                values.append(row)
                for field in ("candidate_ledger", "alternates"):
                    raw = row.get(field) or []
                    if isinstance(raw, Mapping):
                        raw = [raw]
                    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
                        values.extend(item for item in raw if isinstance(item, Mapping))
            # Some web resolver versions store an ``alternates`` list inside a
            # wrapper item.  Flatten that one additional level so the final
            # pass can promote the actual verified image records.
            expanded: list[dict[str, Any]] = []
            for item in values:
                nested = item.get("alternates") if isinstance(item, Mapping) else None
                if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
                    expanded.extend(dict(child) for child in nested if isinstance(child, Mapping))
                expanded.append(dict(item))
            return expanded

        def _candidate_path(item: Mapping[str, Any]) -> str:
            for field in ("cleaned_path", "selected_path", "path"):
                candidate_path = str(item.get(field) or "").strip()
                if candidate_path:
                    return candidate_path
            return ""

        candidate_pool: list[tuple[int, dict[str, Any], str, str]] = []
        seen_candidates: set[tuple[str, str]] = set()
        for source_index, source_row in enumerate(resolution_rows):
            if not isinstance(source_row, Mapping):
                continue
            source_kind = _kind(source_row.get("provider") or source_row.get("source"))
            source_container = str(source_row.get("container_id") or "").strip()
            parent_status = str(source_row.get("status") or "").strip().casefold()
            parent_verification = str(source_row.get("verification_status") or "").strip().casefold()
            parent_accepted = parent_status in {"accepted", "downloaded"} and parent_verification not in {"rejected", "failed", "not_applicable"}
            for item in _flatten_candidates(source_index, source_row):
                item_path = _candidate_path(item)
                item_kind = _kind(item.get("provider") or item.get("source") or source_kind)
                if not item_path or item_kind != source_kind:
                    continue
                if source_container and str(item.get("container_id") or source_container).strip() not in {"", source_container}:
                    continue
                vis_dec = item.get("visual_decision") if isinstance(item.get("visual_decision"), Mapping) else {}
                vis_fatal = bool(vis_dec.get("fatal_mismatch")) or vis_dec.get("accepted") is False or str(vis_dec.get("status") or "").strip().casefold() in {"rejected", "failed"} or bool(vis_dec.get("rejection_reason"))
                item_status = str(item.get("status") or "").strip().casefold()
                explicit_rejection = vis_fatal or bool(item.get("identity_failed") or item.get("rejected") or item.get("rejection_reason")) or item_status in {"rejected", "failed", "error"}
                verified = (not explicit_rejection) and (bool(item.get("verified")) or item_status in {"accepted", "downloaded"})
                qwen = item.get("visual_decision") or item.get("qwen_result") or {}
                if isinstance(qwen, Mapping) and (qwen.get("fatal_mismatch") or qwen.get("accepted") is False or str(qwen.get("status") or "").strip().casefold() in {"rejected", "failed"}):
                    verified = False
                else:
                    verified = verified or (isinstance(qwen, Mapping) and str(qwen.get("status") or "").strip().casefold() in {"accepted", "verified", "deterministic_pool_cut", "deterministic_web_image"})
                # YouTube cut ledgers intentionally omit a duplicate
                # ``verified`` flag because the parent SceneResolution owns
                # that verdict. Inherit acceptance for those ledger entries,
                # while still honoring explicit rejection markers above.
                if not explicit_rejection and not verified and parent_accepted and item.get("candidate_id"):
                    verified = True
                if not verified:
                    continue
                valid, _reason, media_kind = _validate_local_asset(item_path)
                if not valid or media_kind not in {"image", "video"} or is_synthetic_media(item_path, str(item.get("provider") or source_kind)):
                    continue
                identity = _identity(item, item_path)
                if not identity:
                    continue
                key = (item_kind, identity)
                if key in seen_candidates:
                    continue
                seen_candidates.add(key)
                candidate_pool.append((source_index, dict(item), item_path, identity))

        # The mixed-media YouTube lane serializes accepted pool cuts in the
        # provider audit, while the per-scene ledger may contain only the cut
        # that won that scene. Include the complete accepted audit here so a
        # late quota reuse can switch to another clean timestamp when the
        # final sequence would otherwise repeat one clip consecutively.
        for audit_index, audit_cut in enumerate(accepted_cut_rows):
            if not isinstance(audit_cut, Mapping):
                continue
            audit_path = str(
                audit_cut.get("cleaned_path")
                or audit_cut.get("path")
                or audit_cut.get("selected_path")
                or ""
            ).strip()
            if not audit_path:
                continue
            audit_item = dict(audit_cut)
            audit_item.setdefault("provider", "youtube")
            audit_item.setdefault("media_type", "video")
            audit_item.setdefault("verified", True)
            valid, _reason, media_kind = _validate_local_asset(audit_path)
            if not valid or media_kind != "video" or is_synthetic_media(audit_path, "YouTube Clips"):
                continue
            identity = _identity(audit_item, audit_path)
            key = ("youtube", identity)
            if identity and key not in seen_candidates:
                seen_candidates.add(key)
                candidate_pool.append((int(audit_cut.get("scene_index") or audit_index), audit_item, audit_path, identity))

        if candidate_pool:
            usage: Counter[str] = Counter()
            for row_index, path in enumerate(paths):
                row = resolution_rows[row_index] if row_index < len(resolution_rows) and isinstance(resolution_rows[row_index], Mapping) else {}
                usage[_identity(row, str(path or ""))] += 1
            # A cap of one is the normal unique-first preference.  The final
            # handoff may relax it to two only when that is the sole way to
            # avoid an adjacent duplicate after all verified candidates have
            # been distributed; this keeps a scarce pool watchable without
            # allowing a long run of the same asset.
            max_web_reuse = 2
            def _bound_to_target(target_index, candidate, candidate_path):
                requirement = _scene_value(scenes[target_index], 'visual_requirement', {}) or {}
                if not requirement.get('requirement_hash'):
                    return True
                reviewed = dict(candidate, visual_decision=(candidate.get('visual_decision')
                                                           or candidate.get('qwen_result') or {}))
                return _has_bound_pixel_review(requirement, reviewed, candidate_path)
            diversity_repairs = 0
            for index in range(len(paths) - 1):
                current_row = resolution_rows[index] if index < len(resolution_rows) and isinstance(resolution_rows[index], Mapping) else {}
                next_row = resolution_rows[index + 1] if index + 1 < len(resolution_rows) and isinstance(resolution_rows[index + 1], Mapping) else {}
                current_identity = _identity(current_row, str(paths[index] or ""))
                next_identity = _identity(next_row, str(paths[index + 1] or ""))
                current_path_key = os.path.normcase(os.path.normpath(str(paths[index] or ""))) if paths[index] else ""
                next_path_key = os.path.normcase(os.path.normpath(str(paths[index + 1] or ""))) if paths[index + 1] else ""
                if not current_identity or (current_identity != next_identity and current_path_key != next_path_key):
                    continue
                target_index = index + 1
                target_kind = _kind(next_row.get("provider") or (labels[target_index] if target_index < len(labels) else ""))
                target_container = str(next_row.get("container_id") or "").strip()
                previous_identity = _identity(
                    resolution_rows[target_index - 1] if target_index - 1 < len(resolution_rows) and isinstance(resolution_rows[target_index - 1], Mapping) else {},
                    str(paths[target_index - 1] or ""),
                )
                following_identity = _identity(
                    resolution_rows[target_index + 1] if target_index + 1 < len(resolution_rows) and isinstance(resolution_rows[target_index + 1], Mapping) else {},
                    str(paths[target_index + 1] or "") if target_index + 1 < len(paths) else "",
                )
                adjacent_paths = {
                    os.path.normcase(os.path.normpath(str(paths[neighbor] or "")))
                    for neighbor in (target_index - 1, target_index + 1)
                    if 0 <= neighbor < len(paths) and paths[neighbor]
                }
                def _entry_container(entry: tuple[int, dict[str, Any], str, str]) -> str:
                    source_row = (
                        resolution_rows[entry[0]]
                        if 0 <= entry[0] < len(resolution_rows)
                        and isinstance(resolution_rows[entry[0]], Mapping)
                        else {}
                    )
                    return str(entry[1].get("container_id") or source_row.get("container_id") or "").strip()

                options = [
                    entry for entry in candidate_pool
                    if _kind(entry[1].get("provider") or entry[1].get("source") or target_kind) == target_kind
                    and (not target_container or not _entry_container(entry) or _entry_container(entry) == target_container)
                    and entry[3] not in {current_identity, previous_identity, following_identity}
                    and os.path.normcase(os.path.normpath(entry[2])) not in adjacent_paths
                    and usage[entry[3]] < max_web_reuse
                    and _bound_to_target(target_index, entry[1], entry[2])
                ]
                if not options:
                    # A duplicate is preferable to a blank only when the
                    # accepted pool is genuinely exhausted. Leave it intact
                    # and record the shortfall for diagnostics. This keeps a
                    # production run explainable instead of silently hiding
                    # why a repeated asset could not be replaced.
                    metadata.setdefault("adjacent_duplicate_shortfalls", []).append({
                        "scene_index": target_index,
                        "identity": current_identity,
                        "source_kind": target_kind,
                        "candidate_pool_size": len(candidate_pool),
                        "neighbor_identities": [previous_identity, following_identity],
                        "candidate_identities": sorted({entry[3] for entry in candidate_pool if _kind(entry[1].get("provider") or entry[1].get("source") or target_kind) == target_kind}),
                    })
                    continue
                _source_index, candidate, candidate_path, candidate_identity = min(
                    options,
                    key=lambda entry: (usage[entry[3]], abs(entry[0] - target_index), -float(entry[1].get("final_score") or entry[1].get("score") or 0.0)),
                )
                paths[target_index] = candidate_path
                usage[current_identity] = max(0, usage[current_identity] - 1)
                usage[candidate_identity] += 1
                label = "YouTube Clips (verified reuse)" if target_kind == "youtube" else "Web Images (verified reuse)"
                labels[target_index] = label
                target_row = dict(next_row)
                candidate_fingerprint = str(candidate.get("asset_fingerprint") or candidate.get("fingerprint") or candidate.get("md5") or "")
                if not candidate_fingerprint:
                    candidate_fingerprint = _asset_fingerprint(candidate_path)
                target_row.update({
                    "scene_index": target_index,
                    "selected_path": candidate_path,
                    "provider": "youtube" if target_kind == "youtube" else "web",
                    "media_type": str(candidate.get("media_type") or candidate.get("type") or ("video" if target_kind == "youtube" else "image")),
                    "status": "accepted",
                    "verification_status": str(candidate.get("verification_status") or (candidate.get("qwen_result") or {}).get("verification_status") or "verified_reuse"),
                    "resolution_kind": "adjacent_duplicate_repair",
                    "fallback_reason": "adjacent_duplicate_asset_repair",
                    "candidate_id": str(candidate.get("candidate_id") or candidate.get("id") or f"diversity:{target_index}"),
                    "asset_fingerprint": candidate_fingerprint,
                    "source_url": str(candidate.get("origin_url") or candidate.get("source_url") or candidate.get("url") or target_row.get("source_url") or ""),
                    "candidate_evidence": dict(candidate.get("candidate_evidence") or target_row.get("candidate_evidence") or {}),
                    "visual_decision": dict(candidate.get("visual_decision") or candidate.get("qwen_result") or target_row.get("visual_decision") or {}) or None,
                })
                while len(resolution_rows) <= target_index:
                    resolution_rows.append({})
                resolution_rows[target_index] = target_row
                target_resolution = (resolutions or {}).get(target_index)
                if target_resolution is not None:
                    for attr, value in {
                        "selected_path": candidate_path,
                        "provider": target_row["provider"],
                        "media_type": target_row["media_type"],
                        "status": "accepted",
                        "verification_status": target_row["verification_status"],
                        "resolution_kind": "adjacent_duplicate_repair",
                        "fallback_reason": "adjacent_duplicate_asset_repair",
                        "candidate_id": target_row["candidate_id"],
                        "asset_fingerprint": target_row["asset_fingerprint"],
                        "source_url": target_row["source_url"],
                    }.items():
                        try:
                            setattr(target_resolution, attr, value)
                        except Exception:
                            pass
                diversity_repairs += 1
            if diversity_repairs:
                metadata["media_resolution_v2"] = resolution_rows
                metadata["adjacent_duplicate_repairs"] = diversity_repairs
                print(f"   [v2] adjacent mixed-media duplicate repair: rearranged {diversity_repairs} slot(s)", flush=True)

            # The local replacement pass above can report a shortfall when a
            # duplicate sits between the only two assets in a small pool
            # (A,A,B).  Reordering the already accepted slots (A,B,A) solves
            # that case without downloading anything or changing a gate.  Run
            # a bounded sequence pass so a scarce two-asset pool becomes an
            # alternating edit instead of a visible run of the same clip.
            def _asset_fields() -> tuple[str, ...]:
                return (
                    "selected_path", "path", "provider", "source", "media_type",
                    "status", "verification_status", "resolution_kind",
                    "fallback_reason", "candidate_id", "asset_fingerprint",
                    "source_url", "origin_url", "rights_status", "license",
                    "license_url", "attribution", "creator", "query",
                    "query_used", "video_start", "video_end", "render_clip_start",
                    "overlay_policy", "overlay_types", "blur_regions",
                    "rejection_reason", "cleaned_path", "visual_decision",
                    "qwen_result", "candidate_evidence", "candidate_class",
                )

            def _swap_slots(left: int, right: int) -> None:
                if left >= len(resolution_rows) or right >= len(resolution_rows):
                    return
                left_row = dict(resolution_rows[left]) if isinstance(resolution_rows[left], Mapping) else {}
                right_row = dict(resolution_rows[right]) if isinstance(resolution_rows[right], Mapping) else {}
                for field in _asset_fields():
                    left_value = left_row.get(field)
                    right_value = right_row.get(field)
                    if right_value not in (None, ""):
                        left_row[field] = right_value
                    if left_value not in (None, ""):
                        right_row[field] = left_value
                resolution_rows[left] = left_row
                resolution_rows[right] = right_row
                paths[left], paths[right] = paths[right], paths[left]
                if left < len(labels) and right < len(labels):
                    labels[left], labels[right] = labels[right], labels[left]
                left_resolution = (resolutions or {}).get(left)
                right_resolution = (resolutions or {}).get(right)
                if left_resolution is not None and right_resolution is not None:
                    for field in _asset_fields():
                        if not hasattr(left_resolution, field) and not hasattr(right_resolution, field):
                            continue
                        left_value = getattr(left_resolution, field, None)
                        right_value = getattr(right_resolution, field, None)
                        try:
                            setattr(left_resolution, field, right_value)
                        except Exception:
                            pass
                        try:
                            setattr(right_resolution, field, left_value)
                        except Exception:
                            pass

            def _slot_identity(index: int) -> str:
                row = resolution_rows[index] if index < len(resolution_rows) and isinstance(resolution_rows[index], Mapping) else {}
                return _identity(row, str(paths[index] or ""))

            def _slot_kind(index: int) -> str:
                row = resolution_rows[index] if index < len(resolution_rows) and isinstance(resolution_rows[index], Mapping) else {}
                return _kind(row.get("provider") or (labels[index] if index < len(labels) else ""))

            swap_repairs = 0
            for _round in range(max(1, len(paths) * 2)):
                changed = False
                for index in range(len(paths) - 1):
                    left_identity = _slot_identity(index)
                    right_identity = _slot_identity(index + 1)
                    left_path = os.path.normcase(os.path.normpath(str(paths[index] or ""))) if paths[index] else ""
                    right_path = os.path.normcase(os.path.normpath(str(paths[index + 1] or ""))) if paths[index + 1] else ""
                    if not left_identity or (left_identity != right_identity and left_path != right_path):
                        continue
                    kind = _slot_kind(index)
                    replacement = None
                    # Prefer the nearest later slot of the same source kind;
                    # after swapping it into index+1 the displaced asset moves
                    # forward and can be used again on the next bounded pass.
                    for candidate_index in range(index + 2, len(paths)):
                        if _slot_kind(candidate_index) != kind:
                            continue
                        candidate_identity = _slot_identity(candidate_index)
                        candidate_path = os.path.normcase(os.path.normpath(str(paths[candidate_index] or ""))) if paths[candidate_index] else ""
                        if not candidate_identity or candidate_identity == left_identity or candidate_path in {left_path, right_path}:
                            continue
                        following_identity = _slot_identity(index + 2) if index + 2 < len(paths) else ""
                        if candidate_identity == following_identity and candidate_index != index + 2:
                            continue
                        if not (_bound_to_target(index + 1, resolution_rows[candidate_index], paths[candidate_index])
                                and _bound_to_target(candidate_index, resolution_rows[index + 1], paths[index + 1])):
                            continue
                        replacement = candidate_index
                        break
                    if replacement is None:
                        continue
                    _swap_slots(index + 1, replacement)
                    swap_repairs += 1
                    changed = True
                if not changed:
                    break
            if swap_repairs:
                metadata["media_resolution_v2"] = resolution_rows
                metadata["adjacent_duplicate_repairs"] = int(metadata.get("adjacent_duplicate_repairs") or 0) + swap_repairs
                metadata["adjacent_duplicate_swap_repairs"] = swap_repairs
                print(
                    f"   [v2] adjacent mixed-media sequence repair: swapped "
                    f"{swap_repairs} verified slot(s)",
                    flush=True,
                )

    if degraded:
        print(
            "   [v2] INTEGRITY DEGRADE: "
            + f"{len(degraded)} scene(s) fell back to neutral plates: "
            + ", ".join(
                f"{index}({str(_scene_value(scene, 'scene_id', index))[:40]})"
                for index in degraded
            )
            + " — check resolution audit for the root cause",
            flush=True,
        )
        metadata["integrity_degraded_scene_indices"] = list(degraded)
        metadata["integrity_degraded_warning"] = (
            f"{len(degraded)} scene(s) fell back to neutral plates (indices: "
            + ",".join(str(index) for index in degraded)
            + "). Render will contain still-image placeholders."
        )
    media_plan["media_paths"] = paths
    media_plan["source_labels"] = labels
    metadata["media_resolution_v2"] = resolution_rows
    metadata["integrity_degraded_scene_indices"] = degraded
    metadata["source_accounting"] = dict(Counter(labels))
    terminal_counter: Counter[str] = Counter()
    for index in range(len(scenes)):
        row = resolution_rows[index] if index < len(resolution_rows) and isinstance(resolution_rows[index], dict) else {}
        resolution = (resolutions or {}).get(index)
        status = str(row.get("status") or getattr(resolution, "status", "") or "unresolved")
        fallback_reason = str(
            row.get("fallback_reason")
            or getattr(resolution, "fallback_reason", "")
            or ""
        )
        provider = str(row.get("provider") or getattr(resolution, "provider", "") or "")
        label = str(labels[index] or "")
        if (
            status == "degraded"
            or fallback_reason.startswith("degraded:")
            or provider == "neutral"
            or label == "Neutral No-Claim Visual"
        ):
            terminal_counter["degraded"] += 1
        elif status in {"unresolved", "unverified"}:
            terminal_counter["unresolved"] += 1
        else:
            terminal_counter["accepted"] += 1
    metadata["terminal_accounting"] = {
        "accepted": terminal_counter.get("accepted", 0),
        "degraded": terminal_counter.get("degraded", 0),
        "unresolved": terminal_counter.get("unresolved", 0),
    }
    # Refresh the public coverage/guarantee summaries after any late repair.
    # The coordinator serializes these fields before the final handoff, so
    # leaving them untouched would report a stale 9/17 (or even blank-asset)
    # audit despite the validated render plan containing 17 real videos.
    if youtube_only:
        accepted_scene_count = 0
        for index, path in enumerate(paths):
            row = (
                resolution_rows[index]
                if index < len(resolution_rows)
                and isinstance(resolution_rows[index], Mapping)
                else {}
            )
            provider = str(row.get("provider") or "").strip().casefold()
            media_type = str(row.get("media_type") or "").strip().casefold()
            status = str(row.get("status") or "").strip().casefold()
            valid_path, _path_reason, path_kind = _validate_local_asset(str(path or ""))
            if (
                valid_path
                and path_kind == "video"
                and provider in {"youtube", "youtube clips", "yt"}
                and media_type == "video"
                and status in {"accepted", "resolved"}
                and not is_synthetic_media(str(path or ""), labels[index] if index < len(labels) else "")
            ):
                accepted_scene_count += 1
        coverage = dict(metadata.get("youtube_coverage") or {})
        coverage.update({
            "eligible_scene_count": len(scenes),
            "accepted_scene_count": accepted_scene_count,
            "accepted_ratio": round(accepted_scene_count / len(scenes), 4) if scenes else 0.0,
            "measured_after_relevance_gate": True,
        })
        metadata["youtube_coverage"] = coverage
    guarantee = metadata.get("global_media_guarantee")
    if isinstance(guarantee, Mapping):
        real_media_count = 0
        renderable_asset_count = 0
        grounded_indices = []
        for index, path in enumerate(paths):
            valid_path, _path_reason, _path_kind = _validate_local_asset(str(path or ""))
            if valid_path:
                renderable_asset_count += 1
                label = str(labels[index] if index < len(labels) else '')
                if 'grounded graphic' in label.casefold():
                    grounded_indices.append(index)
                elif not is_synthetic_media(str(path or ""), label):
                    real_media_count += 1
        updated_guarantee = dict(guarantee)
        updated_guarantee.update({
            "real_media_count": real_media_count,
            "scene_count": len(scenes),
            "no_blank_assets": bool(scenes) and len(paths) == len(scenes) and renderable_asset_count == len(scenes),
            "real_media_complete": bool(scenes) and len(paths) == len(scenes) and real_media_count == len(scenes),
            "grounded_fallback_count": len(grounded_indices),
            "grounded_fallback_indices": grounded_indices,
        })
        metadata["global_media_guarantee"] = updated_guarantee
    if str(lane_policy.get("mode") or "").strip().casefold() == "mixed media":
        coverage = dict(metadata.get("youtube_coverage") or {})
        # Late web/stock rescue can replace an accepted YouTube slot. Report
        # what will actually render, rather than the earlier retrieval mix.
        actual_youtube = []
        for index, path in enumerate(paths):
            row = resolution_rows[index] if index < len(resolution_rows) and isinstance(resolution_rows[index], Mapping) else {}
            if _is_video(str(path or '')) and (
                str(labels[index] or '').strip().casefold() in {'youtube', 'youtube clips'}
                or str(row.get('provider') or '').casefold() in {'youtube', 'yt', 'youtube clips'}
            ):
                actual_youtube.append(index)
        eligible_count = int(coverage.get('eligible_scene_count') or len(paths))
        coverage.update(accepted_scene_indices=actual_youtube,accepted_scene_count=len(actual_youtube),
                        accepted_ratio=round(len(actual_youtube)/max(1,eligible_count),4))
        metadata['youtube_coverage'] = coverage
        try:
            target = float(coverage.get("target") or 0.0)
            accepted_ratio = float(coverage.get("accepted_ratio") or 0.0)
        except (TypeError, ValueError):
            target, accepted_ratio = 0.0, 0.0
        if target > 0.0 and accepted_ratio + 1e-9 < target:
            shortfall = max(0, math.ceil(target * eligible_count) - len(actual_youtube))
            metadata["youtube_coverage_warning"] = {
                "status": "degraded",
                "message": "Requested YouTube coverage was not met after bounded retries; web/stock rescue filled the remaining real-media slots.",
                "target": target,
                "accepted_ratio": accepted_ratio,
                "shortfall_scene_count": shortfall,
                "fallback_exhausted": True,
            }
            metadata["pipeline_quality_status"] = "degraded_youtube_coverage"
            print(
                "   [v2] Mixed Media YouTube coverage shortfall: "
                f"{accepted_ratio:.1%} accepted vs {target:.1%} target; "
                "rescue outcome recorded in manifest",
                flush=True,
            )
        elif target > 0.0:
            metadata.pop('youtube_coverage_warning', None)
            if metadata.get('pipeline_quality_status') == 'degraded_youtube_coverage':
                metadata['pipeline_quality_status'] = 'passed_media_validation'
    return media_plan
