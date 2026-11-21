"""Keep explicit pixel contradictions out of every media handoff."""
from collections.abc import Mapping
import re
from urllib.parse import unquote

VISUAL_REVIEW_POLICY_VERSION = 26


# ---------------------------------------------------------------------------
# Abstract-narration acceptance (run 9, GTA VI launch-day job a7f52c58).
#
# The coordinator's vision gate rejected nearly every YouTube clip for the
# launch-day video even though downloads were plentiful (12 clips): the
# narration on most scenes is ABSTRACT — platforms, sales, numbers, specs
# ("current-gen only", "16M pre-orders, $1.5B, 99 Metacritic", "212 GB",
# "30 fps") — and no frame can literally depict an abstract concept, so
# vision kept reporting "the scene's subject is entirely absent" with
# fatal_mismatch=True. For an abstract line, authentic same-game footage
# IS the right illustration. The checks below are deterministic
# (keyword/entity matching on the scene text and the stored verdict), never
# another model call.
#
# Hard keeps: the scene-relative game gate stays — wrong game / wrong
# domain / wrong era footage still rejects in full (a 2002 Vice City
# cutscene, a Germany travel doc, a DIY-painting clip, an Apollo rocket
# launch), as do unsafe, watermarked, subtitled, outro/end-card and
# talking-head verdicts. The bypass applies to the YouTube motion lane
# only; web-image verdicts keep their own gates.
# ---------------------------------------------------------------------------
#
# Subject-level acceptance (Nov 20, 2026 — generalises the run-9 fix to
# every niche, both lanes). The run-9 bug is the same bug in every niche:
# vision asks "does this clip literally show this narration sentence?"
# and the narration is abstract in every niche ("the canal changed world
# trade", "16M pre-orders"). The visual subject is the ENTITY the video
# is about, not the sentence. ``scene_subject`` generalises the per-scene
# game derivation to a per-scene subject (game / place / machine /
# person / phenomenon); ``subject_level_accepts_verdict`` accepts a
# candidate that authentically shows that subject, in the YouTube lane
# AND the web-image lane, and vision's score only ranks accepted
# candidates — a low score never rejects. Hard rejects remain ONLY:
# wrong subject, wrong entity, terrible quality, duplicate (enforced by
# the coordinator's reuse/dedup, not this gate), or a direct
# contradiction (wrong era / location / entity / game). The 2002 Vice
# City cutscene stays rejected everywhere; GTA V footage is only allowed
# for scenes narrating GTA V. No throttle or concurrency value is touched
# by this change: the mix improves through better acceptance, never by
# hammering the sources harder.
# ---------------------------------------------------------------------------

# A narration beat is abstract when it is affirmatively about a
# number/money/spec/platform/format/date/availability fact. "Launch day"
# alone is NOT in this list on purpose: "launch day has arrived" names no
# such fact, so it stays concrete-gated (its frames were rejected live on
# subtitles/overlays, not on subject absence).
#
# _ABSTRACT_TOPIC_GENERAL_RE is the niche-free half: money, scale and
# impact facts that are abstract in a documentary exactly as specs are
# abstract in gaming ("the canal changed world trade", "two thousand
# nine hundred kilometres of coastline"). "Decade" is deliberately NOT
# here: run-9 scene 1 ("after more than a decade of waiting") is a
# concrete beat, and "launch day has arrived" must stay concrete-gated.
_ABSTRACT_TOPIC_GENERAL_RE = re.compile(
    r"(?:"
    r"\b\d+\s*(?:percent|%)\b"
    r"|\brevenue\b"
    r"|\btrade\b"
    r"|\bchanged\b"
    r"|\btransformed\b"
    r"|\brevolutioni[sz]ed\b"
    r"|\blegacy\b"
    r"|\bimpact\b"
    r"|\blargest\b"
    r"|\bbiggest\b"
    r"|\b\d+\s*(?:kilomet(?:er|re)s?|miles|tonnes?|tons?)\b"
    r"|\b(?:19|20)\d{2}\b"
    r")",
    re.I,
)
_ABSTRACT_TOPIC_RE = re.compile(
    r"(?:"
    r"\$\s*\d"
    r"|\b\d+(?:\.\d+)?\s*(?:million|billion|thousand)\b"
    r"|\bmetacritic\b"
    r"|\bpre-?orders?\b"
    r"|\bsales\b"
    r"|\bbiggest launch\b"
    r"|\bfps\b"
    r"|\bframes per second\b"
    r"|\bgigabytes?\b"
    r"|\b\d+\s*(?:gb|mb)\b"
    r"|\bstorage\b"
    r"|\bclear some space\b"
    r"|\bpssr\b"
    r"|\bray-?traced\b"
    r"|\bray tracing\b"
    r"|\bupscaling\b"
    r"|\bsingle-player\b"
    r"|\bmultiplayer\b"
    r"|\bgta online\b"
    r"|\bgta\+"
    r"|\bpatches\b"
    r"|\bpre-?loads?\b"
    r"|\bdownloads?\b"
    r"|\bdownload code\b"
    r"|\bboxed copy\b"
    r"|\bdisc\b"
    r"|\bcurrent-gen\b"
    r"|\blast-gen\b"
    r"|\bno pc\b"
    r"|\bplaystation\s*5\b"
    r"|\bxbox series\b"
    r"|\bps5\b"
    r"|\bxbox\b"
    r"|\bplaystation\b"
    r"|\bmandatory\b"
    r"|\bupdates?\b"
    r"|\bnumbers?\b"
    r"|\bnovember\s+\d+(?:st|nd|rd|th)?\b"
    r")",
    re.I,
)

# A depictable visual subject in the narration. "Vice City Pack" is a
# product name, not the place, so it is stripped before this check; a
# console is a platform concept, not a depictable subject, so "console"
# is deliberately absent here. The second half is the niche-free set:
# places, structures and machines a documentary narrates ("ship", "lock",
# "canal", "bridge", "train"). "Ocean"/"sea" are deliberately absent:
# "two hours between oceans" names no single depictable subject, so a
# Panama trade scene stays abstract.
_CONCRETE_SUBJECT_RE = re.compile(
    r"(?:"
    r"\bvice city\b"
    r"|\bleonida\b"
    r"|\bcar\b"
    r"|\bboat\b"
    r"|\byacht\b"
    r"|\bbeach\b"
    r"|\bstreet\b"
    r"|\bnightclub\b"
    r"|\bclub\b"
    r"|\bhelicopter\b"
    r"|\bmotorcycle\b"
    r"|\bpolice\b"
    r"|\bheist\b"
    r"|\brobbery\b"
    r"|\bchase\b"
    r"|\bdriving\b"
    r"|\bracing\b"
    r"|\bdancing\b"
    r"|\bparty\b"
    r"|\bshootout\b"
    r"|\bshooting\b"
    r"|\bguns?\b"
    r"|\bskyline\b"
    r"|\bsunset\b"
    r"|\bman\b"
    r"|\bwoman\b"
    r"|\bmen\b"
    r"|\bwomen\b"
    r"|\bcharacters?\b"
    r"|\bship\b"
    r"|\blocks?\b"
    r"|\bcanal\b"
    r"|\bbridge\b"
    r"|\btrain\b"
    r"|\blocomotives?\b"
    r"|\brailway\b"
    r"|\brailroad\b"
    r"|\bwall\b"
    r"|\btower\b"
    r"|\bdam\b"
    r"|\btunnel\b"
    r"|\briver\b"
    r"|\bport\b"
    r"|\bharbo[u]?r\b"
    r"|\bcrane\b"
    r"|\bfactory\b"
    r"|\bplane\b"
    r"|\baircraft\b"
    r"|\brocket\b"
    r"|\bengine\b"
    r"|\bcastle\b"
    r"|\bpalace\b"
    r"|\bcathedral\b"
    r"|\bmountain\b"
    r"|\bforest\b"
    r"|\bcoast(?:line)?\b"
    r")",
    re.I,
)

# Reason text that proves the pixels are NOT the scene's game, even when
# the structured domain flags are missing. Bare "vice city" is NOT here:
# GTA VI is set in Vice City, so a same-game frame may mention it
# positively; only the 2002 original (year / explicit marker) counts.
_WRONG_GAME_REASON_MARKERS = (
    "wrong game", "different game", "wrong domain", "different domain",
    "wrong place", "wrong subject", "wrong entity", "wrong era",
    "different era", "anachronis", "travel documentary",
    "travel-documentary", "no game", "unrelated",
    "previous game", "older game", "earlier game",
    "san andreas", "vice city (2002)",
)
# Other GTA titles as word-boundary patterns: a bare substring would
# false-fire ("gta v" is a prefix of "gta vi"; "grand theft auto v" is
# a prefix of "grand theft auto vi"), so these need the boundary.
_WRONG_GAME_TITLE_RE = re.compile(
    r"\b(?:gta\s*v|grand theft auto\s*v|gta\s*5"
    r"|gta\s*iv|grand theft auto\s*iv|gta\s*4"
    r"|gta\s*iii|grand theft auto\s*iii|gta\s*3"
    r"|2002)\b"
)

# Presentation types that are game footage (not a still, a photo, or an
# archival clip). "video_clip" is deliberately NOT in this set: it is the
# generic fallback label, so a video_clip verdict must also name the game
# in its reason or prove it through the candidate's metadata.
_GAME_FOOTAGE_PRESENTATIONS = {"gameplay_footage", "youtube_frame", "in_game_footage"}

# Candidate-side hard flags the abstract bypass must never override:
# the provider's own overlay/outro/caption/facecam screen. A watermark
# is handled separately (tolerated cleanups stay tolerated).
_ABSTRACT_BLOCKED_CANDIDATE_REJECTIONS = {
    "outro_endcard_rejected", "overlay_blur_failed",
    "severe_overlay_or_subtitle_detected", "caption_overlay_rejected",
    "large_or_central_logo_rejected", "facecam_inset_rejected",
}

_ROMAN_TO_ARABIC = {
    "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7",
    "viii": "8", "ix": "9", "x": "10",
}


def _scene_text(scene: Mapping | None) -> str:
    if not isinstance(scene, Mapping):
        return ""
    return " ".join(
        str(scene.get(key) or "")
        for key in ("text", "narration", "scene_description", "visual_intent")
    )


def is_abstract_narration(scene: Mapping | None) -> bool:
    """True when the narration is about an abstract fact, not a picture.

    Deterministic: the text must carry a strong abstract-topic signal
    (a number, price, spec, platform, format, patch or date fact — or,
    for any niche, a money / scale / impact fact) AND name no concrete
    visual subject. A named person in the requirement is a concrete
    subject and vetoes abstract. A beat with neither a signal nor a
    subject ("GTA 6 is finally here") is NOT abstract — fail closed, so
    the bypass only fires where the narration is affirmatively
    undepictable.
    """
    if not isinstance(scene, Mapping):
        return False
    text = _scene_text(scene)
    if not (_ABSTRACT_TOPIC_RE.search(text) or _ABSTRACT_TOPIC_GENERAL_RE.search(text)):
        return False
    requirement = scene.get("visual_requirement")
    if isinstance(requirement, Mapping):
        if str(requirement.get("subject_type") or "").strip().lower() == "person":
            canonical = str(requirement.get("canonical_entity") or "").strip()
            if len([token for token in re.split(r"\s+", canonical) if token]) >= 2:
                return False
    searchable = re.sub(r"vice city pack", " ", text, flags=re.I)
    if _CONCRETE_SUBJECT_RE.search(searchable):
        return False
    return True


def scene_game_identity(scene: Mapping | None) -> str:
    """The display name of the game this scene is about, or "".

    Only meaningful for a gaming scene (callers gate on
    ``is_gaming_scene`` first). Prefers the requirement's film subject,
    then a video-game canonical entity, then a gaming title.
    """
    if not isinstance(scene, Mapping) or not is_gaming_scene(scene):
        return ""
    requirement = scene.get("visual_requirement")
    if isinstance(requirement, Mapping):
        context = requirement.get("subject_context")
        if isinstance(context, Mapping):
            film = str(context.get("film_subject") or "").strip()
            if film:
                return film
        if str(requirement.get("subject_type") or "").strip().lower() in _GAMING_CATEGORIES:
            canonical = str(requirement.get("canonical_entity") or "").strip()
            if canonical:
                return canonical
    for key in ("content_title", "title"):
        title = str(scene.get(key) or "").strip()
        if title and _GAMING_TERMS_RE.search(title):
            return title
    return ""


def _subject_aliases(subject_name: str, scene: Mapping | None = None) -> list[str]:
    """Phrase aliases for a subject name (``Grand Theft Auto VI`` -> GTA 6...).

    Generic form of the old game-only helper: the name itself, the
    requirement's aliases, a trailing Roman numeral swapped for Arabic,
    and — only for a Grand Theft Auto title — the GTA short forms.
    """
    names = [str(subject_name or "").strip().lower()]
    if isinstance(scene, Mapping):
        requirement = scene.get("visual_requirement")
        if isinstance(requirement, Mapping):
            for alias in requirement.get("aliases") or []:
                if str(alias or "").strip():
                    names.append(str(alias).strip().lower())
    aliases: list[str] = []
    for name in names:
        if name and name not in aliases:
            aliases.append(name)
        match = re.search(r"\b([ivx]+)\s*$", name)
        if match:
            arabic = _ROMAN_TO_ARABIC.get(match.group(1))
            if arabic:
                swapped = name[: match.start()] + arabic
                if swapped not in aliases:
                    aliases.append(swapped)
        if name.startswith("grand theft auto"):
            short = "gta" + name[len("grand theft auto"):].strip()
            if short and short not in aliases:
                aliases.append(short)
            nospace = short.replace(" ", "")
            if nospace not in aliases:
                aliases.append(nospace)
    return aliases


def _game_aliases(game_name: str, scene: Mapping | None = None) -> list[str]:
    """Phrase aliases for a game name (``Grand Theft Auto VI`` -> GTA 6...)."""
    return _subject_aliases(game_name, scene)


def _contains_subject_phrase(text: str, subject_name: str, scene: Mapping | None = None) -> bool:
    """True when `text` names the subject as a whole phrase."""
    normalized = re.sub(r"[^a-z0-9+]+", " ", str(text or "").lower())
    normalized = f" {normalized.strip()} "
    for alias in _subject_aliases(subject_name, scene):
        phrase = re.sub(r"[^a-z0-9+]+", " ", alias).strip()
        if phrase and f" {phrase} " in normalized:
            return True
    return False


def _contains_game_phrase(text: str, game_name: str, scene: Mapping | None = None) -> bool:
    normalized = re.sub(r"[^a-z0-9+]+", " ", str(text or "").lower())
    normalized = f" {normalized.strip()} "
    for alias in _game_aliases(game_name, scene):
        phrase = re.sub(r"[^a-z0-9+]+", " ", alias).strip()
        if phrase and f" {phrase} " in normalized:
            return True
    return False


def candidate_proves_game(candidate: Mapping | None, scene: Mapping | None) -> bool:
    """True when the candidate's own metadata names the scene's game.

    Phrase match on title/description/tags/URL (never the search query):
    "Grand Theft Auto VI: An Extended Look" proves GTA VI; "More Than
    You Expect" (Germany travel) and "GTA Vice City (2002)" do not.
    """
    if not isinstance(candidate, Mapping):
        return False
    game = scene_game_identity(scene)
    if not game:
        return False
    metadata = " ".join(
        str(candidate.get(key) or "")
        for key in ("title", "description", "tags", "source_url", "url", "filename")
    )
    return _contains_game_phrase(metadata, game, scene)


def _verdict_is_same_game_footage(
    verdict: Mapping, scene: Mapping | None, candidate: Mapping | None = None
) -> bool:
    """True when the verdict's pixels are authentic footage of the scene's game.

    Structured flags first: domain_match False or entity_conflict True
    is a wrong-game/wrong-domain verdict (Dresden travel doc, Apollo
    rocket, DIY painting all fail here). Reason text is the second
    layer for verdicts without flags (wrong-game / wrong-era wording,
    other GTA titles, the 2002 original). Positive evidence is then
    required: a game-footage presentation type, the game named in the
    reason, or the candidate's metadata proving the game. Anything
    without positive evidence fails closed. Unsafe, watermarked,
    subtitled, outro/end-card and talking-head verdicts never pass.
    """
    if verdict.get("unsafe_content") or verdict.get("suggestive_content"):
        return False
    if verdict.get("watermark") is True:
        return False
    if (
        verdict.get("subtitle") is True
        or verdict.get("severe_overlay")
        or verdict.get("outro_detected")
        or verdict.get("talking_head")
    ):
        return False
    if verdict.get("domain_match") is False:
        return False
    if verdict.get("entity_conflict") is True:
        return False
    reason = " ".join(
        str(verdict.get(key) or "")
        for key in ("reason", "rejection_reason", "status")
    ).lower()
    if any(_has_non_negated_match(reason, marker) for marker in _WRONG_GAME_REASON_MARKERS):
        return False
    if _has_non_negated_other_game_match(reason, scene):
        return False
    presentation = re.sub(
        r"[\s\-]+", "_", str(verdict.get("source_presentation_type") or "").strip().lower()
    )
    if presentation in _GAME_FOOTAGE_PRESENTATIONS:
        return True
    game = scene_game_identity(scene)
    if game and _contains_game_phrase(reason, game, scene):
        return True
    if candidate is not None and candidate_proves_game(candidate, scene):
        return True
    return False


_NEGATED_MARKER_PREFIX_RE = re.compile(
    r"\b(?:no|not|without|never)\s+"
    r"(?:(?:a|an|any|the|evidence|indication|signs|of|showing|depicting|"
    r"shows|depicts|indicating|indicates|being|having)\s+){0,6}$",
)


def _has_non_negated_other_game_match(reason: str, scene: Mapping | None) -> bool:
    """True when the reason names a DIFFERENT GTA title (non-negated).

    The wrong-title regex also matches the scene's own game ("GTA V"
    in a GTA V scene's verdict) — that match must not veto, or GTA V
    footage could never pass for a GTA V scene. A match counts only
    when it is not one of the scene game's own aliases. "2002" and
    every other title still veto in full.
    """
    game = scene_game_identity(scene)
    own_aliases = set(_game_aliases(game, scene)) if game else set()
    for match in _WRONG_GAME_TITLE_RE.finditer(reason):
        prefix = re.split(r"[.!?;,\n]", reason[max(0, match.start() - 100):match.start()])[-1]
        if _NEGATED_MARKER_PREFIX_RE.search(prefix):
            continue
        matched = re.sub(r"\s+", " ", match.group(0).strip().lower())
        if matched in own_aliases:
            continue
        return True
    return False


def _has_non_negated_match(text: str, pattern) -> bool:
    """True when `pattern` occurs in `text` outside a negated clause.

    Verdict reasons routinely deny the mismatch ("no wrong subject",
    "not a different era") — a bare substring match would refuse authentic
    footage on the model's own denial. Same negation logic as
    ``explicit_scene_conflict``: the clause before the match (after the
    last sentence boundary) must not end in a negation. "no game" needs
    no exemption: the phrase itself is the wrong-game signal, and the
    text before it ("there is ...") does not end in a negation.
    """
    regex = pattern if hasattr(pattern, "finditer") else re.compile(re.escape(pattern))
    for match in regex.finditer(text):
        prefix = re.split(r"[.!?;,\n]", text[max(0, match.start() - 100):match.start()])[-1]
        if not _NEGATED_MARKER_PREFIX_RE.search(prefix):
            return True
    return False


def _candidate_has_abstract_block(candidate: Mapping | None) -> bool:
    """True when the provider's own overlay/outro screen vetoes the clip."""
    if not isinstance(candidate, Mapping):
        return False
    if candidate.get("outro_detected") or candidate.get("blur_failed"):
        return True
    if candidate.get("talking_head") or candidate.get("facecam_inset") or candidate.get("inset_facecam"):
        return True
    if str(candidate.get("rejection_reason") or "").strip().lower() in _ABSTRACT_BLOCKED_CANDIDATE_REJECTIONS:
        return True
    if candidate.get("watermark") and not candidate.get("watermark_tolerated"):
        return True
    # A promotional thumbnail / composite is not source media: the
    # subject rule must never resurrect one (web lane), and the same
    # holds for a YouTube candidate carrying the flag.
    if any(candidate.get(key) is True for key in (
        "promotional_thumbnail", "youtube_thumbnail", "is_thumbnail", "promotional_collage",
    )):
        return True
    return False


def abstract_scene_accepts_verdict(
    verdict: Mapping | None, scene: Mapping | None, candidate: Mapping | None = None
) -> bool:
    """True when this verdict must PASS under the abstract-narration rule.

    All four must hold: the scene's narration is abstract, the scene is
    a gaming scene, the verdict's pixels are authentic footage of the
    scene's game, and neither the verdict nor the candidate carries a
    hard overlay/safety veto. A YouTube candidate is required when a
    candidate dict is supplied; without one, the verdict's candidate_id
    must name the YouTube lane (a missing id fails closed).
    """
    if not isinstance(verdict, Mapping) or not is_abstract_narration(scene):
        return False
    if not is_gaming_scene(scene):
        return False
    # A real vision verdict is required: a deterministic / compare-only
    # verdict (visual_checked False — e.g. the proof job a57cb5c7 Rockstar
    # -logo frame, "youtube_frame_recovery_soft_visual_warning") never
    # looked at the pixels, so there is no same-game footage to excuse.
    # The bound-pixel-review contract still applies: an unbound acceptance
    # must fall through to real media, never commit through this rule.
    # Same standard as _is_fatal_vision_verdict.
    if verdict.get("visual_checked") is not True:
        return False
    if isinstance(candidate, Mapping):
        source = str(candidate.get("source") or candidate.get("provider") or "").strip().lower()
        if source and source not in {"youtube", "youtube clips", "yt"}:
            return False
        if _candidate_has_abstract_block(candidate):
            return False
    else:
        candidate_id = str(verdict.get("candidate_id") or "").strip().lower()
        if not candidate_id.startswith("youtube:"):
            return False
    return _verdict_is_same_game_footage(verdict, scene, candidate)


# ---------------------------------------------------------------------------
# Subject-level acceptance (generalises the run-9 abstract rule).
# ---------------------------------------------------------------------------

# Web-image lane sources, mirroring the coordinator's ``_is_web_source``
# (same set, so the two gates cannot drift apart). DuckDuckGo stays
# skipped upstream — no DDG candidate reaches this gate — but the set
# keeps the coordinator's spelling so a stray DDG row is judged as web
# media, never as an unknown lane.
_WEB_LANE_SOURCES = frozenset({
    "web", "web images", "web_image", "web_image_resolver",
    "flickr", "yandex", "bing", "bing_hybrid", "ddg", "ddg_hybrid",
    "google", "google_images", "getty_bing", "pinterest", "openverse",
    "wikimedia",
})
_YOUTUBE_LANE_SOURCES = frozenset({"youtube", "youtube clips", "yt"})

# Planner subject_type -> subject type (plan §2). Anything unlisted is
# a generic "topic" subject: the name still has to match as a phrase.
_SUBJECT_TYPE_BY_REQUIREMENT = {
    "gaming": "game", "video_game": "game", "game": "game",
    "person": "person", "public_figure": "person", "person_or_group": "person",
    "named_person": "person", "celebrity": "person", "politician": "person",
    "train": "machine", "locomotive": "machine", "ship": "machine",
    "aircraft": "machine", "automobile": "machine", "vehicle": "machine",
    "machine": "machine",
    "place": "place", "location": "place", "city": "place", "country": "place",
    "canal": "place", "bridge": "place", "structure": "place",
    "building": "place", "landmark": "place", "dam": "place",
    "tunnel": "place", "river": "place", "mountain": "place",
    "event": "event", "war": "event", "battle": "event", "disaster": "event",
    "animal": "phenomenon", "species": "phenomenon",
    "phenomenon": "phenomenon", "nature": "phenomenon", "science": "phenomenon",
}

# Reason text that proves the pixels are NOT the scene's subject, in any
# niche. The game-only markers (wrong game, other GTA titles, 2002)
# apply only to gaming scenes — a Panama Canal documentary IS a travel
# documentary in some framings, so those words must not veto it.
_WRONG_SUBJECT_REASON_MARKERS = (
    "wrong subject", "wrong entity", "wrong place", "wrong location",
    "different location", "wrong era", "different era", "anachronis",
    "wrong domain", "different domain", "unrelated",
    "wrong person", "different person",
)

# Presentation types that are subject footage (not a still of something
# else, a photo of a different place, or an archival clip of another
# era). "video_clip" is deliberately NOT in this set — same reasoning as
# the game-only set: the generic label must also name the subject in
# its reason or prove it through the candidate's metadata.
_SUBJECT_FOOTAGE_PRESENTATIONS = {
    "gameplay_footage", "youtube_frame", "in_game_footage",
    "photograph", "video_frame",
}

# A verdict rejection that the subject rule must never override, even
# when the pixels show the subject: an ownership mark, or a thumbnail
# composite passed off as source media (web lane).
_SUBJECT_BLOCKED_REJECTION_REASONS = frozenset({
    "watermark_detected", "promotional_thumbnail_not_source_media",
})


def _canonical_is_entity(canonical: str, subject_type: str, scene: Mapping | None) -> bool:
    """True when the planner's canonical entity names a real subject.

    The planner sometimes stores the abstract concept itself as the
    canonical entity ("fps mode at launch", "Expect", "Standard") — that
    is not a subject and must not become one, or every clip proving the
    concept's words would pass. A concrete subject_type vouches for the
    canonical; a "concept" never does; anything else needs 2+ tokens
    and no abstract-topic signal.
    """
    tokens = [token for token in re.split(r"\s+", str(canonical or "").strip()) if token]
    if not tokens:
        return False
    if subject_type == "concept":
        return False
    if subject_type in _SUBJECT_TYPE_BY_REQUIREMENT:
        return True
    if len(tokens) < 2:
        return False
    if _ABSTRACT_TOPIC_RE.search(canonical) or _ABSTRACT_TOPIC_GENERAL_RE.search(canonical):
        return False
    return True


def scene_subject(scene: Mapping | None) -> dict | None:
    """The per-scene subject: ``{"name": ..., "type": ...}``, or None.

    Generalises the per-scene game derivation (plan §2): for a gaming
    scene the subject IS the game (same derivation, not rebuilt); for
    every other scene it is the entity / event / place the scene is
    about, from the planner's film subject, then its canonical entity,
    then the video's topic / a bare-entity title. Fails closed: when
    the planner only supplies an abstract concept ("Expect", "world
    trade") the scene has NO subject and the rule does not fire.
    """
    if not isinstance(scene, Mapping):
        return None
    if is_gaming_scene(scene):
        game = scene_game_identity(scene)
        return {"name": game, "type": "game"} if game else None
    requirement = scene.get("visual_requirement")
    requirement = requirement if isinstance(requirement, Mapping) else {}
    subject_type = str(requirement.get("subject_type") or "").strip().lower()
    context = requirement.get("subject_context")
    context = context if isinstance(context, Mapping) else {}
    film = str(context.get("film_subject") or "").strip()
    if film:
        return {"name": film, "type": _SUBJECT_TYPE_BY_REQUIREMENT.get(subject_type, "topic")}
    canonical = str(requirement.get("canonical_entity") or "").strip()
    if canonical and _canonical_is_entity(canonical, subject_type, scene):
        return {"name": canonical, "type": _SUBJECT_TYPE_BY_REQUIREMENT.get(subject_type, "topic")}
    for key in ("subject", "global_topic", "topic"):
        topic = str(scene.get(key) or "").strip()
        if (
            topic
            and len([token for token in re.split(r"\s+", topic) if token]) >= 2
            and not _ABSTRACT_TOPIC_RE.search(topic)
            and not _ABSTRACT_TOPIC_GENERAL_RE.search(topic)
        ):
            return {"name": topic, "type": "topic"}
    for key in ("content_title", "title"):
        title = str(scene.get(key) or "").strip()
        if (
            title
            and len([token for token in re.split(r"\s+", title) if token]) <= 5
            and not _ABSTRACT_TOPIC_RE.search(title)
            and not _ABSTRACT_TOPIC_GENERAL_RE.search(title)
            and not re.search(r"[.?!]|^(?:how|what|why|when|where|who)\b", title, re.I)
        ):
            return {"name": title, "type": _SUBJECT_TYPE_BY_REQUIREMENT.get(subject_type, "topic")}
    return None


def candidate_proves_subject(candidate: Mapping | None, scene: Mapping | None) -> bool:
    """True when the candidate's own metadata names the scene's subject.

    Phrase match on title/description/tags/URL/filename (never the
    search query), plus the web lane's alt text / caption. For a gaming
    scene this is exactly ``candidate_proves_game``.
    """
    if not isinstance(candidate, Mapping):
        return False
    subject = scene_subject(scene)
    if not subject:
        return False
    if is_gaming_scene(scene):
        return candidate_proves_game(candidate, scene)
    metadata = " ".join(
        str(candidate.get(key) or "")
        for key in ("title", "description", "tags", "source_url", "url", "filename", "alt_text", "caption")
    )
    return _contains_subject_phrase(metadata, subject["name"], scene)


def _verdict_reason(verdict: Mapping) -> str:
    return " ".join(
        str(verdict.get(key) or "")
        for key in ("reason", "rejection_reason", "status")
    ).lower()


def _has_era_contradiction(verdict: Mapping, scene: Mapping | None) -> bool:
    """True when the verdict's pixels contradict the scene's era.

    The scene names a year and the verdict's reason names a different
    year ("1914 construction" illustrated by a 2016 photo), or the
    scene names a historical year and the reason says the pixels are
    modern / present-day. Era wording without a year in the scene is
    left to the wrong-era markers — a scene about the canal in general
    may legitimately use a modern photo.
    """
    if not isinstance(scene, Mapping):
        return False
    scene_years = set(re.findall(r"\b(?:18|19|20)\d{2}\b", _scene_text(scene)))
    if not scene_years:
        return False
    reason = _verdict_reason(verdict)
    reason_years = set(re.findall(r"\b(?:18|19|20)\d{2}\b", reason))
    if reason_years and reason_years.isdisjoint(scene_years):
        return True
    if any(int(year) < 2000 for year in scene_years):
        if any(marker in reason for marker in (
            "modern", "present-day", "present day", "contemporary",
        )):
            return True
    return False


def _quality_is_terrible(verdict: Mapping) -> bool:
    """True only for an explicitly terrible quality signal.

    A numeric quality below 20 (a 0–1 float is scaled to 0–100 first;
    0 / missing means unknown, never terrible) or a "terrible" /
    "unusable" label. Dark / blurry alone is NOT terrible: run 9's
    _yl5ELcmugI frame:1 was dark and blurry and the GTA fix still
    accepts it — quality ranks candidates, it does not veto subject
    footage.
    """
    quality = verdict.get("quality")
    if isinstance(quality, (int, float)) and not isinstance(quality, bool):
        value = float(quality)
        if 0.0 < value <= 1.0:
            value *= 100.0
        return value < 20.0
    if isinstance(quality, str):
        return quality.strip().lower() in {"terrible", "unusable", "very poor", "very_poor"}
    return False


def _verdict_is_same_subject_footage(
    verdict: Mapping, scene: Mapping | None, candidate: Mapping | None = None
) -> bool:
    """True when the verdict's pixels authentically show the scene's subject.

    The niche-free form of ``_verdict_is_same_game_footage`` (gaming
    scenes delegate to it unchanged, so the run-9 behaviour is byte-for-
    byte preserved there). Structured flags first: domain_match False
    or entity_conflict True is a wrong-subject verdict. Reason text is
    the second layer (wrong-subject / wrong-era wording, era
    contradictions). Positive evidence is then required: a subject-
    footage presentation type, the subject named in the reason, or the
    candidate's metadata proving the subject. For a non-gaming verdict
    a bare presentation type only counts when the vision scores show at
    least a partial match (identity or final >= 35) — otherwise a vague
    low-score verdict on a same-category-but-wrong-instance photo (the
    Suez Canal for a Panama scene) could pass on the label alone; below
    that floor the reason or the metadata must name the subject.
    Unsafe, watermarked, subtitled, outro/end-card and talking-head
    verdicts never pass.
    """
    if is_gaming_scene(scene):
        return _verdict_is_same_game_footage(verdict, scene, candidate)
    if verdict.get("unsafe_content") or verdict.get("suggestive_content"):
        return False
    if verdict.get("watermark") is True:
        return False
    if (
        verdict.get("subtitle") is True
        or verdict.get("severe_overlay")
        or verdict.get("outro_detected")
        or verdict.get("talking_head")
    ):
        return False
    if verdict.get("domain_match") is False:
        return False
    if verdict.get("entity_conflict") is True:
        return False
    reason = _verdict_reason(verdict)
    if any(_has_non_negated_match(reason, marker) for marker in _WRONG_SUBJECT_REASON_MARKERS):
        return False
    if _has_era_contradiction(verdict, scene):
        return False
    subject = scene_subject(scene)
    if not subject:
        return False
    presentation = re.sub(
        r"[\s\-]+", "_", str(verdict.get("source_presentation_type") or "").strip().lower()
    )
    if presentation in _SUBJECT_FOOTAGE_PRESENTATIONS:
        try:
            identity = float(verdict.get("identity_score") or 0.0)
        except (TypeError, ValueError):
            identity = 0.0
        try:
            final = float(verdict.get("final_score") or 0.0)
        except (TypeError, ValueError):
            final = 0.0
        if identity >= 35.0 or final >= 35.0:
            return True
    if _contains_subject_phrase(reason, subject["name"], scene):
        return True
    if candidate is not None and candidate_proves_subject(candidate, scene):
        return True
    return False


def _is_literal_match_rejection(verdict: Mapping) -> bool:
    """True when the verdict rejected on literal-match grounds.

    fatal_mismatch, an explicit-conflict / insufficient-match /
    visual-rejected outcome, a reason saying the subject is absent, or
    a low-score pair (identity and final both < 35). These are exactly
    the rejections the subject rule must stop firing on authentic
    subject footage (plan §3); a clean accept carries none of them.
    """
    if verdict.get("fatal_mismatch") is True:
        return True
    if str(verdict.get("rejection_reason") or "").strip().lower() in {
        "explicit_scene_conflict", "insufficient_visible_subject_match",
        "visual_rejected", "canonical_entity_mismatch",
    }:
        return True
    if str(verdict.get("status") or "").strip().lower() == "visual_rejected":
        return True
    reason = _verdict_reason(verdict)
    if any(phrase in reason for phrase in (
        "subject is entirely absent", "required subject is absent",
        "required entity is absent", "does not show the required",
        "fails to show the required", "no visible subject",
    )):
        return True
    try:
        if float(verdict.get("identity_score")) < 35 and float(verdict.get("final_score")) < 35:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _is_subject_lane_source(source: str) -> bool:
    """True for a YouTube or web-image lane source (or a universal_ engine)."""
    normalized = str(source or "").strip().lower()
    if normalized.startswith("universal_"):
        return True
    return normalized in _YOUTUBE_LANE_SOURCES or normalized in _WEB_LANE_SOURCES


def subject_level_accepts_verdict(
    verdict: Mapping | None, scene: Mapping | None, candidate: Mapping | None = None
) -> bool:
    """True when this verdict must PASS under the subject-level rule.

    All of these must hold: the scene has a derivable subject; the
    verdict is a REAL vision verdict (visual_checked — the a57cb5c7
    bound-pixel-review guard, same as the abstract rule); the quality
    is not explicitly terrible; the verdict is not a watermark /
    promotional-thumbnail rejection; the candidate (when supplied) is
    a YouTube or web-image candidate with no hard overlay/safety veto;
    the scene's narration is abstract OR the verdict rejected on
    literal-match grounds; and the verdict's pixels authentically show
    the scene's subject. Vision's score is preserved by the caller for
    ranking — a low score never rejects here. For a gaming scene this
    is a superset of ``abstract_scene_accepts_verdict``: abstract
    same-game footage still passes, and so does same-game footage for
    a concrete scene when vision rejected it only for literal mismatch.
    """
    if not isinstance(verdict, Mapping):
        return False
    if verdict.get("visual_checked") is not True:
        return False
    subject = scene_subject(scene)
    if not subject:
        return False
    if _quality_is_terrible(verdict):
        return False
    if str(verdict.get("rejection_reason") or "").strip().lower() in _SUBJECT_BLOCKED_REJECTION_REASONS:
        return False
    if isinstance(candidate, Mapping):
        source = str(candidate.get("source") or candidate.get("provider") or "").strip().lower()
        if source and not _is_subject_lane_source(source):
            return False
        if _candidate_has_abstract_block(candidate):
            return False
    else:
        candidate_id = str(verdict.get("candidate_id") or "").strip().lower()
        if candidate_id and not (
            candidate_id.startswith("youtube:") or candidate_id.startswith("web:")
        ):
            presentation = re.sub(
                r"[\s\-]+", "_",
                str(verdict.get("source_presentation_type") or "").strip().lower(),
            )
            reason = _verdict_reason(verdict)
            if (
                presentation not in _SUBJECT_FOOTAGE_PRESENTATIONS
                and not _contains_subject_phrase(reason, subject["name"], scene)
            ):
                return False
    if not (is_abstract_narration(scene) or _is_literal_match_rejection(verdict)):
        return False
    return _verdict_is_same_subject_footage(verdict, scene, candidate)


_GAMING_CATEGORIES = {"gaming", "video_game", "game"}
_GAMING_TERMS_RE = re.compile(
    r"\b(?:gaming|gameplay|video games?|gta|grand theft auto|playstation|xbox|nintendo)\b",
    re.I,
)


def is_gaming_scene(scene: Mapping | None) -> bool:
    """True when the scene's own topic is a video game.

    Reads the scene's ``domain_category`` / gaming flag first (authoritative
    when the planner set it); falls back to the visual requirement's
    domain/subject_type and to gaming terms in the scene text/title. A
    gaming scene's footage is game-engine footage by definition, so the
    "game-engine" / "video-game simulation" markers are advisory there,
    never a veto.
    """
    if not isinstance(scene, Mapping):
        return False
    for key in ("domain_category", "subject_type"):
        if str(scene.get(key) or "").strip().lower() in _GAMING_CATEGORIES:
            return True
    requirement = scene.get("visual_requirement")
    if isinstance(requirement, Mapping):
        for key in ("domain_category", "domain", "subject_type"):
            if str(requirement.get(key) or "").strip().lower() in _GAMING_CATEGORIES:
                return True
    if scene.get("is_gaming") or scene.get("gaming_mode"):
        return True
    text = " ".join(
        str(scene.get(key) or "")
        for key in ("text", "visual_intent", "content_title", "title")
    )
    return bool(_GAMING_TERMS_RE.search(text))


def exact_scene_identity_required(scene: Mapping | None) -> bool:
    """A grounded named subject keeps its identity even in the moving-clip lane."""
    if not isinstance(scene, Mapping):
        return False
    requirement = scene.get('visual_requirement') or {}
    if not isinstance(requirement, Mapping) or not requirement.get('strict_identity'):
        return False
    context = requirement.get('subject_context') or {}
    if isinstance(context, Mapping) and context.get('scene_local'):
        return context.get('identity_scope') != 'context'
    return str(requirement.get('subject_type') or '').casefold() in {
        'person', 'public_figure', 'person_or_group', 'named_person', 'celebrity', 'politician',
    }


def explicit_low_visual_match(verdict: Mapping | None, *, scene: Mapping | None = None) -> bool:
    """Do not promote a completed pixel review that found almost no subject match.

    For a gaming scene, a low score whose reason is only that the pixels are
    game-engine / video-game-simulation footage is not a genuine mismatch:
    the footage is a game because the topic is a game. It still rejects
    when the reason carries a wrong-entity/event/domain signal.
    """
    if not isinstance(verdict, Mapping) or not (verdict.get('visual_available') or verdict.get('visual_checked')):
        return False
    if verdict.get('identity_score') is None or verdict.get('final_score') is None:
        return False
    if is_gaming_scene(scene):
        reason = " ".join(str(verdict.get(key) or "") for key in (
            "reason", "rejection_reason", "status",
        )).lower()
        if any(marker in reason for marker in ("game-engine", "video-game simulation")) and not any(
            token in reason for token in (
                "wrong entity", "wrong event", "wrong domain", "different entity",
                "different event", "wrong game", "different game", "wrong subject",
            )
        ):
            return False
    try:
        return float(verdict['identity_score']) < 35 and float(verdict['final_score']) < 35
    except (TypeError, ValueError):
        return False


def explicit_domain_conflict(story: str, evidence: str) -> str:
    """Veto clearly different domains, without treating imperfect shots as failures.

    Search queries are never evidence. Callers supply source metadata or a
    description of the pixels; the story supplies the intended meaning.
    """
    expected = str(story or '').casefold()
    observed = re.sub(r'[-_]+', ' ', unquote(str(evidence or '')).casefold())
    business_warrants = bool(re.search(r'\broyal warrants?\b', expected)
                             and re.search(r'\b(?:warrant holders?|suppliers?|brands?|commercial)\b', expected))
    pension_document = re.search(r'\b(?:ministry of pensions|retired pay|military pensions|pensions royal warrant)\b', observed)
    if business_warrants and pension_document and not re.search(r'\b(?:ministry of pensions|retired pay|military pensions)\b', expected):
        return 'military_pension_warrant_for_commercial_appointment'
    gaming_story = re.search(r'\b(?:gaming|gameplay|video games?|gta|grand theft auto|farming simulator|train simulator|train sim world|railworks|madden|nba 2k\d*|game mods?)\b', expected)
    simulation_source = re.search(r'\b(?:kingmods|farming simulator|train simulator|train sim world|railworks|fs(?:19|22|25)\s*(?:mods?)?|madden(?: nfl)?|nba 2k\d*|ea sports fc|game mods?|video.game simulation|simulated (?:diesel )?(?:locomotive|train|cab|environment)|computer generated (?:footage|scene|environment)|3d rendered (?:cab|train|locomotive))\b', observed)
    if simulation_source and not gaming_story:
        return 'simulation_for_real_world'
    american = bool(re.search(r'\b(?:nfl|american football|quarterback|touchdown|super bowl)\b', expected))
    association = r'\b(?:soccer|uefa|champions league|premier league|la liga|penalty kick|fc barcelona|real madrid)\b'
    if american and not re.search(association, expected) and re.search(association, observed):
        return 'different_sport'
    sport = american or bool(re.search(r'\b(?:basketball|baseball|tennis|cricket|hockey|soccer)\b', expected))
    hardware = r'\b(?:tilt and turn|tilt.{0,4}turn|double glazed|quadruple pane|window modes|ventilation|air tight seal)\b'
    if sport and not re.search(hardware, expected) and re.search(hardware, observed):
        return 'literal_hardware_for_sports_metaphor'
    return ''


def calculate_youtube_weighted_score(
    *,
    vision_score: float = 0.0,
    vision_available: bool = False,
    transcript_score: float = 0.0,
    transcript_available: bool = False,
    metadata_score: float = 0.0,
    is_relaxed: bool = True,
) -> float:
    """Calculate a balanced composite score across vision, transcript, and metadata.

    Weights:
    - Vision verification: 0.40
    - Transcript match: 0.35
    - Metadata / Title match: 0.25

    Dynamically rebalances when vision or transcript is unavailable.
    """
    v_norm = min(100.0, max(0.0, float(vision_score or 0.0)))
    t_norm = min(100.0, max(0.0, float(transcript_score or 0.0)))
    m_norm = min(100.0, max(0.0, float(metadata_score or 0.0)))

    if vision_available and transcript_available:
        w_v, w_t, w_m = 0.40, 0.35, 0.25
    elif vision_available and not transcript_available:
        w_v, w_t, w_m = 0.55, 0.0, 0.45
    elif not vision_available and transcript_available:
        w_v, w_t, w_m = 0.0, 0.55, 0.45
    else:
        w_v, w_t, w_m = 0.0, 0.0, 1.0

    score = v_norm * w_v + t_norm * w_t + m_norm * w_m
    if is_relaxed and vision_available and v_norm >= 35.0:
        score = min(100.0, score + 5.0)
    return round(score, 2)


def explicit_scene_conflict(verdict: Mapping | None, *, relaxed: bool = False, scene: Mapping | None = None) -> bool:
    if not isinstance(verdict, Mapping):
        return False
    # For a gaming scene, "game-engine" / "video-game simulation" describe
    # the required footage, not a contradiction: they are advisory, never
    # fatal, in either mode. Every other veto (wrong game/event, unsafe,
    # domain mismatch) still applies in full.
    gaming = is_gaming_scene(scene)
    game_markers = ("game-engine", "video-game simulation")
    def yes(value):
        return value is True or str(value).strip().lower() in {"true", "1", "yes"}

    # Absolute non-negotiable safety vetoes apply everywhere
    if any(yes(verdict.get(key)) for key in ("unsafe_content", "suggestive_content")):
        return True

    # Domain contradiction is always fatal
    if verdict.get("domain_match") is False or str(verdict.get("domain_match")).strip().lower() == "false":
        return True

    reason = " ".join(str(verdict.get(key) or "") for key in (
        "reason", "rejection_reason", "status",
    )).lower()

    if relaxed:
        # In relaxed mode (YouTube motion lane / B-roll), minor matchup/era/event
        # differences within the same domain are advisory, not fatal rejections.
        spurious_tokens = (
            "era", "year", "season", "tenure", "older", "dated",
            "period", "roster", "transfer", "trade", "matchup", "uniform",
            "different player", "different team", "not named", "quarterback",
            "different event", "distinct events", "wrong event", "competing event",
            "wrong matchup", "different matchup", "different location", "wrong venue",
            "wrong entity", "different entity", "wrong subject", "different subject",
            "required entity is absent", "required subject is absent",
        )
        if yes(verdict.get("fatal_mismatch")):
            # Gaming scene, reason only "game-engine" / "video-game
            # simulation": the flag describes required footage, not a
            # mismatch. Any wrong-entity/event/domain wording keeps it fatal.
            game_only = gaming and any(marker in reason for marker in game_markers) and not any(
                token in reason for token in (
                    "wrong entity", "wrong event", "wrong domain", "wrong game",
                    "different entity", "different event", "different game",
                    "wrong subject",
                )
            )
            # If fatal_mismatch was triggered solely by a spurious token, do not veto
            if not game_only and not any(token in reason for token in spurious_tokens):
                return True
        if yes(verdict.get("entity_conflict")):
            # An entity conflict in relaxed mode without domain match failure is advisory
            # unless an explicit wrong domain or different sport marker is present
            if any(marker in reason for marker in ("wrong domain", "different_sport")):
                return True

        # Check fatal relaxed markers (strict domain/simulation contradictions and era conflicts)
        fatal_relaxed_markers = (
            "simulation_for_real_world",
            "wrong domain", "different_sport", "literal_hardware_for_sports_metaphor",
            "wrong era", "different era", "anachronistic", "anachronism", "era mismatch",
        ) if gaming else (
            "game-engine", "video-game simulation", "simulation_for_real_world",
            "wrong domain", "different_sport", "literal_hardware_for_sports_metaphor",
            "wrong era", "different era", "anachronistic", "anachronism", "era mismatch",
        )
        for marker in fatal_relaxed_markers:
            for match in re.finditer(re.escape(marker), reason):
                prefix = re.split(r"[.!?;,\n]", reason[max(0, match.start() - 100):match.start()])[-1]
                negated = re.search(
                    r"\b(?:no|not|without|never)\s+"
                    r"(?:(?:a|an|any|the|evidence|indication|signs|of|showing|depicting|"
                    r"shows|depicts|indicating|indicates|being|having)\s+){0,6}$",
                    prefix,
                )
                if not negated:
                    return True
        return False

    # Strict mode checks. For a gaming scene a fatal_mismatch / entity_conflict
    # flag whose reason is only "game-engine" / "video-game simulation" is
    # advisory: the model is describing required game footage, not a wrong
    # game. Any wrong-entity/event/domain wording keeps the flag fatal.
    if gaming and any(marker in reason for marker in game_markers) and not any(
        token in reason for token in (
            "wrong entity", "wrong event", "wrong domain", "wrong game",
            "different entity", "different event", "different game",
            "wrong subject",
        )
    ):
        flag_keys = ("event_mismatch", "location_mismatch", "era_mismatch", "year_mismatch")
    else:
        flag_keys = (
            "fatal_mismatch", "entity_conflict", "event_mismatch", "location_mismatch",
            "era_mismatch", "year_mismatch",
        )
    if any(yes(verdict.get(key)) for key in flag_keys):
        return True
    if any(key in verdict and (
        verdict[key] is False or str(verdict[key]).strip().lower() == "false"
    ) for key in ("scene_match", "domain_match")):
        return True

    markers = (
        "different event", "distinct events", "wrong event", "competing event",
        "contradictory event", "wrong location", "different location",
        "wrong venue", "different matchup", "wrong matchup",
        "wrong subject", "wrong entity", "wrong domain",
        "wrong era", "different era", "wrong year", "different year",
        "anachronistic", "anachronism", "historical mismatch",
    ) if gaming else (
        "different event", "distinct events", "wrong event", "competing event",
        "contradictory event", "wrong location", "different location",
        "wrong venue", "different matchup", "wrong matchup", "game-engine",
        "video-game simulation", "wrong subject", "wrong entity", "wrong domain",
        "wrong era", "different era", "wrong year", "different year",
        "anachronistic", "anachronism", "historical mismatch",
    )
    for marker in markers:
        for match in re.finditer(re.escape(marker), reason):
            prefix = re.split(r"[.!?;,\n]", reason[max(0, match.start() - 100):match.start()])[-1]
            negated = re.search(
                r"\b(?:no|not|without|never)\s+"
                r"(?:(?:a|an|any|the|evidence|indication|signs|of|showing|depicting|"
                r"shows|depicts|indicating|indicates|being|having)\s+){0,6}$",
                prefix,
            )
            if not negated:
                return True
    return False


def explicit_visual_facet_conflict(narration: str, description: str) -> str:
    """Reject a described different physical subsystem, not imperfect framing.

    Unknown descriptions stay unknown. Multiple subjects may show any one.
    This guard compares actual pixel descriptions, never search metadata.
    """
    story = str(narration or '').casefold()
    pixels = str(description or '').casefold()
    branding = bool(re.search(r'\b(?:logo|logos|emblem|badge|wordmark)\b', pixels))
    branding_requested = bool(re.search(r'\b(?:logo|logos|emblem|badge|branding|wordmark)\b', story))
    action = r'\b(?:throws?|throwing|passes?|passing|formations?|snaps?|running play|tackles?|tackling|accelerat\w*|brak\w*|taking off|takeoff|landing)\b'
    visible_action = r'\b(?:players?|quarterbacks?|tackl\w*|throw\w*|running|driving|moving|taking off|landing|formation|diagram|illustration)\b'
    if branding and not branding_requested and re.search(action, story) and not re.search(visible_action, pixels):
        return 'static_branding_for_action_scene'
    families = {
        'charging': r'\b(?:superchargers?|charging stations?|chargers?|charge ports?|charging sockets?|ev charging|recharging|plugged[- ]in|charging cables?)\b',
        'storage': r'\b(?:powerwalls?|megapacks?|stationary batter(?:y|ies)|battery storage|energy storage|home batter(?:y|ies)|battery units?|battery containers?)\b',
        'solar': r'\b(?:solar panels?|photovoltaic panels?|solar roofs?|solar installation)\b',
        'interface': r'\b(?:touchscreens?|dashboard displays?|infotainment|central screens?|control screens?)\b',
        'cockpit': r'\b(?:cockpits?|flight decks?|pilot instrument panels?)\b',
        'factory': r'\b(?:assembly lines?|manufacturing lines?|production lines?|factory floors?)\b',
        'engine': r'\b(?:engine bays?|engine interiors?|pistons?|cylinders?|combustion chambers?)\b',
        'court': r'\b(?:courtrooms?|court hearings?|witness stands?|judges? bench)\b',
    }
    expected = {name for name, pattern in families.items() if re.search(pattern, str(narration or ''), re.I)}
    # A montage that explicitly discusses vehicles as well as energy systems
    # may show a vehicle interior. Do not mistake that for a different system.
    vehicle_pattern = r'\b(?:vehicles?|cars?|sedans?|suvs?|roadsters?)\b'
    mentions_vehicle = bool(re.search(vehicle_pattern, str(narration or ''), re.I))
    vehicle_list = bool(re.search(r'\b(?:vehicles?|cars?)\s*,\s*charging', str(narration or ''), re.I))
    if mentions_vehicle and (not expected or vehicle_list):
        expected.update(('vehicle', 'interface', 'engine'))
    visible = {name for name, pattern in families.items() if re.search(pattern, str(description or ''), re.I)}
    if re.search(vehicle_pattern, str(description or ''), re.I):
        visible.add('vehicle')
    if expected and visible and not expected.intersection(visible):
        return 'different_visible_subsystem'
    return ''
