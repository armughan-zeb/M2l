"""Linear-time, network-free source query rows for planning deadline fallback."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

from .mechanical_scene_planner import content_words, game_for_scene, plan_mechanical_scene_rows
from .vidzeo_brain import (
    _default_youtube_intents,
    _fallback_retrieval_context,
    _is_format_phrase_subject,
    _mechanical_scene_plan,
)
from .youtube_query_normalizer import normalize_youtube_query


def _clean_query(value: Any, fallback: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value or fallback or "")).strip(" \t\r\n\"'`.,:;")
    return " ".join(text.split()[:14]).strip()


# Filler words that carry no search value in a title anchor. A title such as
# "Amtrak's Long Run Through the Crew's Day" is a made-up phrase: used
# verbatim it produces queries no search engine can match ("Amtrak Long Run
# Through documentary crew history photograph"). Stripping the filler leaves
# the searchable subject ("Amtrak Crew").
_COMPACT_SUBJECT_FILLER = {
    "a", "an", "the", "of", "and", "in", "on", "to", "for", "with", "from",
    "into", "over", "under", "after", "before", "during", "through", "across",
    "between", "about", "than", "then", "there", "here", "while", "since",
    "until", "per", "via", "at", "by", "as", "or", "but", "not", "no", "is",
    "are", "was", "were", "be", "been", "how", "what", "when", "where", "why",
    "who", "which", "that", "this", "these", "those", "very", "just", "also",
    "still", "yet", "more", "most", "other", "another", "first", "second",
    "new", "old", "great", "big", "small", "real", "true", "full", "complete",
    "entire", "long", "run", "runs", "day", "days", "history", "story",
    "stories", "look", "looks", "closely", "behind", "familiar", "primary",
}


def _compact_search_subject(anchor: str) -> str:
    """Shorten a title anchor to its searchable content words (max 4)."""
    text = re.sub(r"'s\b", "", str(anchor or ""))
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", text)
    kept = [word for word in words if word.casefold() not in _COMPACT_SUBJECT_FILLER]
    return " ".join(kept[:4]).strip()


def _positive_terms(context_terms: Any, mech_terms: Any, active: str,
                    extra_terms: Any = ()) -> List[str]:
    """Positive retrieval terms: the context's, padded to >= 3 from the
    mechanical row's own terms (the contract validator rejects fewer).

    The padding must not depend on the mechanical plan row: boundary
    (intro/outro) scenes take the old topic-anchored plan and carry no
    mechanical row, so with only the context terms they stall at two —
    subject == domain anchor dedupes to one term, plus the domain
    category (Panama: ["Panama Canal", "other"] on all 13 rows, which
    failed every row in job f66daa53). Pad from the scene's own content
    words next, then from the words of the active subject, so the >= 3
    invariant holds for every row regardless of its plan path.
    """
    terms = [str(term).strip() for term in (context_terms or []) if str(term or "").strip()]
    if not terms:
        terms = [str(active).strip()] if str(active or "").strip() else []
    for source in ((mech_terms or []), (extra_terms or []), str(active or "").split()):
        for term in source:
            if len(terms) >= 3:
                break
            clean = str(term or "").strip()
            if clean and clean not in terms:
                terms.append(clean)
        if len(terms) >= 3:
            break
    return terms[:10]


def _variants(*values: str) -> List[str]:
    return list(dict.fromkeys(value for value in values if value))


_FACET_STOPWORDS = {
    "a", "an", "and", "as", "at", "actually", "already", "also", "another", "because", "biggest", "but", "could",
    "currently", "days", "even", "every", "fans", "featured", "giving",
    "going", "greatest", "here", "including", "instead", "just", "killed",
    "left", "made", "major", "months", "more", "most", "much", "multiple",
    "for", "from", "how", "i", "in", "is", "it", "its", "now", "of", "on", "or", "our", "people", "pieces", "question", "recent", "reportedly", "seeing",
    "against", "after", "before", "above", "below", "behind", "beside", "along", "among", "during", "within", "without", "into", "onto", "upon", "since", "until", "while", "because", "across", "leave", "leaves", "leaving",
    "shown", "shows", "some", "something", "still", "taken", "that", "the", "their", "them", "over",
    "there", "these", "they", "thing", "this", "through", "to", "very", "was", "we", "what",
    "when", "where", "which", "while", "who", "will", "with", "world", "would", "you", "years",
    "train", "trains", "bat", "bats", "prepares", "prepare", "shows", "shown", "uses", "using",
    "owns", "rises", "gets", "ready", "works", "working", "checks", "check", "follows", "explains",
    "explore", "explores", "becomes", "became", "defines", "define",
    "shapes", "shape", "represents", "represent", "marks", "mark",
    "stands", "stand", "sits", "sit", "lies", "lie", "contains",
    "contain", "features", "feature", "includes", "include", "offers",
    "offer", "covers", "cover", "takes", "take", "brings", "bring",
    "carries", "carry", "sails", "sailed", "sank", "sinks", "crosses",
    "crossed", "travels", "travelled", "looks", "look",
}

_DOMAIN_VISUAL_TERMS = {
    "gaming": (
        "gameplay", "driving", "vehicle", "vehicles", "police", "plane", "city",
        "mission", "missions", "character", "characters", "npc", "npcs", "interior",
        "interiors", "animation", "animations", "physics", "trailer",
        "rockstar", "take-two", "netflix", "microsoft", "discord", "court", "subpoena",
        "radio", "music", "jason", "lucia", "vice city", "release",
    ),
    "automotive": (
        "driving", "engine", "interior", "exterior", "dashboard", "road", "track",
        "factory", "design", "wheel", "wheels", "headlight", "launch", "race",
    ),
    "crime": (
        "police", "court", "trial", "detective", "evidence", "investigation",
        "suspect", "arrest", "crime scene", "location", "archive", "hearing",
    ),
    "sports": (
        "match", "goal", "training", "stadium", "trophy", "celebration", "race",
        "interview", "practice", "team", "fans", "final",
    ),
    "technology": (
        "phone", "laptop", "device", "screen", "chip", "camera", "factory", "launch",
        "prototype", "software", "robot", "laboratory", "demo",
    ),
    "history": (
        "archive", "portrait", "palace", "ceremony", "war", "crowd", "speech",
        "newspaper", "building", "map", "photograph", "royal",
    ),
    "disaster": (
        "aftermath", "rescue", "flood", "fire", "storm", "damage", "evacuation",
        "aerial", "debris", "emergency", "location",
    ),
}


def _scene_visual_facet(scene_text: str, subject: str, domain: str) -> str:
    """Return a short, concrete scene facet for network-free query fallback.

    The old fallback used the first three long words in a sentence.  That made
    queries such as ``GTA VI giving fans unexpected portrait`` and forced every
    scene down to the same broad title query.  Prefer visible domain terms,
    named entities and actions while keeping the title as the identity anchor.
    """
    text = re.sub(r"\s+", " ", str(scene_text or "")).strip()
    lowered = text.casefold()
    selected: List[str] = []

    def add(value: str) -> None:
        value = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;!?\"'").casefold()
        parts = value.split()
        while parts and parts[0] in _FACET_STOPWORDS:
            parts.pop(0)
        while parts and parts[-1] in _FACET_STOPWORDS:
            parts.pop()
        value = " ".join(parts)
        if value and value not in selected and value not in _FACET_STOPWORDS:
            selected.append(value)

    for term in _DOMAIN_VISUAL_TERMS.get(str(domain or "").casefold(), ()):
        if re.search(rf"\b{re.escape(term)}(?:s|ed|ing)?\b", lowered):
            add(term)
        if len(selected) >= 3:
            break

    subject_words = {word.casefold() for word in re.findall(r"[A-Za-z0-9]+", subject or "")}
    for phrase in re.findall(r"\b(?:[A-Z][A-Za-z0-9-]+(?:\s+[A-Z][A-Za-z0-9-]+){0,2})\b", text):
        words = {word.casefold() for word in re.findall(r"[A-Za-z0-9]+", phrase)}
        # Skip a capitalized subject fragment such as ``Model`` from
        # ``The Model 3 interior``.  The subject is already the identity
        # anchor; repeating one of its tokens as a scene facet adds noise.
        meaningful_words = words - _FACET_STOPWORDS
        if meaningful_words and not meaningful_words.issubset(subject_words):
            add(phrase)
        if len(selected) >= 3:
            break

    if len(selected) < 2:
        for token in re.findall(r"[a-z][a-z0-9-]{3,}", lowered):
            if token in _FACET_STOPWORDS or token in subject_words:
                continue
            if any(token == part for phrase in selected for part in phrase.split()):
                continue
            add(token)
            if len(selected) >= 3:
                break
    return " ".join(selected[:3]).strip()


def _entity_visual_kind(subject: str, scene_text: str, domain: str, section: Dict[str, Any]) -> str:
    """Pick a visual query vocabulary for the no-network planning fast path."""
    explicit = str(
        section.get("scene_subject_type")
        or section.get("container_type")
        or ""
    ).casefold().strip()
    aliases = {
        "people": "person", "celebrity": "person", "athlete": "person",
        "location": "place", "landmark": "place", "product": "object",
        "vehicle": "object", "automobile": "object", "thing": "object",
        "food": "food",
    }
    explicit = aliases.get(explicit, explicit)
    if explicit in {"person", "place", "object", "food", "ship", "animal", "event"}:
        return explicit
    subject_text = str(subject or "").strip()
    subject_lower = subject_text.casefold()
    context = f"{subject_text} {scene_text or ''} {domain or ''}".casefold()
    # A studio, a game title, or a place is never a person, however
    # name-like its capitalization ("Rockstar Games", "Grand Theft Auto
    # VI", "Vice City" all match the two-capitalized-words pattern below).
    # Check those first: the old order returned "person" for all of them.
    if re.search(r"\b(?:grand theft auto|gta)\s+(?:vi|v|iv|iii|6|5|4|3|six|five|four|three)\b", subject_lower):
        return "topic"
    if re.search(r"\b(?:games|studios|entertainment|interactive|company|corporation|pictures)\s*$", subject_lower):
        return "topic"
    if re.search(r"\b(?:city|streets?|island|keys|beach|state|county|country|neighborhood|district|town|village|province|coast|peninsula)\b", subject_lower):
        return "place"
    if re.match(r"^(?:rms|hms|uss|ss|mv|ms|hmt)\b", subject_lower):
        # Initialed vessels are named maritime subjects (RMS Titanic, HMS
        # Victory), not retail products.  Give them an archive/ship query lane
        # so a history scene cannot drift into ``product photograph``.
        return "ship"
    if any(term in context for term in ("fruit", "recipe", "nutrition", "meal", "cooking", "food")):
        return "food"
    if str(domain or "").casefold() in {"travel", "nature", "geography"}:
        return "place"
    object_terms = {
        "car", "vehicle", "model", "product", "phone", "iphone", "smartphone",
        "laptop", "camera", "device", "engine", "robot", "chip", "machine",
        "ship", "aircraft", "rocket", "software", "app", "dashboard", "interior",
    }
    if re.search(r"\b(?:ship|vessel|liner|steamship|submarine|boat)\b", context):
        return "ship"
    if any(re.search(rf"\b{re.escape(term)}\b", context) for term in object_terms):
        return "object"
    role_terms = {
        "actor", "actress", "athlete", "footballer", "cricketer", "player", "president",
        "minister", "king", "queen", "prince", "princess", "duke", "duchess", "royal",
        "singer", "musician", "journalist", "coach", "founder", "scientist", "born",
        "biography", "portrait", "interview",
    }
    name_like = bool(re.fullmatch(r"[A-Z][A-Za-z0-9'’-]*(?:\s+[A-Z][A-Za-z0-9'’-]*){1,3}", subject_text))
    if not name_like:
        # Multi-person subjects joined by "and"/"&" ("Lucia Caminos and
        # Jason Duval"): each part is a capitalized name on its own.
        name_parts = [part.strip() for part in re.split(r"\s+(?:and|&)\s+", subject_text) if part.strip()]
        name_like = len(name_parts) >= 2 and all(
            re.fullmatch(r"[A-Z][A-Za-z0-9'’-]*(\s+[A-Z][A-Za-z0-9'’-]*)*", part)
            for part in name_parts)
    if any(re.search(rf"\b{re.escape(term)}\b", context) for term in role_terms) or name_like:
        return "person"
    return "thing"


def _documentary_anchor(title: str, scene_texts: Sequence[str]) -> str:
    """Promote a strong named vessel over a truncated/date-led UI title."""
    sample = "\n".join([str(title or ""), *(str(text or "") for text in scene_texts)])
    initialed = re.search(
        r"\b(?:RMS|HMS|USS|SS|MV|MS|HMT)\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2}\b",
        sample,
    )
    if initialed:
        return re.sub(r"\s+", " ", initialed.group(0)).strip()
    return str(title or "").strip()


def build_mechanical_query_bundle(
    *,
    title: str,
    scene_texts: Sequence[str],
    section_subjects: Sequence[str],
    sections: Sequence[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Build web/YouTube/stock rows without cross-container regex passes.

    Every generated query starts from only its active subject, so the expensive
    all-subject stripping in the full LLM adapter is unnecessary here.
    """
    scene_texts = list(scene_texts or [])
    section_subjects = list(section_subjects or [])
    sections = list(sections or [])
    global_anchor = _documentary_anchor(title, scene_texts) or str(title or "").strip() or "footage"
    all_subjects = list(dict.fromkeys(
        str(subject or "").strip()
        for subject in section_subjects
        if str(subject or "").strip()
    ))
    global_context = _fallback_retrieval_context(
        global_anchor,
        " ".join(scene_texts),
        global_anchor,
    )
    # The scene plans also take the title as their topic; give them the
    # compact subject too, so YouTube/stock queries don't repeat the full
    # made-up title phrase before the scene words.
    plan_topic = _compact_search_subject(global_anchor) or global_anchor
    bundle: Dict[str, List[Dict[str, Any]]] = {"web": [], "yt": [], "stock": []}
    # Mechanical-first plan rows (deterministic prototype, promoted from
    # scratch): per-scene subjects, distinctive-token facets, and
    # per-lane queries derived from the scene's own narration. The bundle
    # below still anchors retrieval on the section subject where the
    # prototype row gives no scene subject, and intro/outro scenes keep
    # the old topic-anchored path.
    try:
        mech_rows = plan_mechanical_scene_rows(
            scene_texts, title, plan_topic, section_subjects)
    except Exception:
        mech_rows = []

    for idx, scene_text in enumerate(scene_texts):
        section = sections[idx] if idx < len(sections) and isinstance(sections[idx], dict) else {}
        subject = str(section_subjects[idx] if idx < len(section_subjects) else "").strip()
        is_boundary = bool(section.get("is_intro") or str(section.get("container_id") or "").lower() == "outro")
        if is_boundary:
            subject = ""
        mrow = mech_rows[idx] if (not is_boundary and idx < len(mech_rows) and isinstance(mech_rows[idx], dict)) else {}
        # Padding source for the >= 3 positive-terms contract. Boundary
        # scenes take the old plan above and carry no mechanical row, so
        # mrow is empty for them; the mech row's own terms are still the
        # right pad (scene words), and the scene's content words cover a
        # missing or failed mechanical build.
        pad_terms = (
            mech_rows[idx].get("retrieval_positive_terms")
            if idx < len(mech_rows) and isinstance(mech_rows[idx], dict)
            else []
        )
        # The scene's own subject (first entity / event framing) is the
        # query anchor; the section subject stays the container identity
        # (grouping, container isolation) and the fallback anchor.
        mech_subject = str(mrow.get("scene_subject") or "").strip()
        # The section detector can emit a format phrase as a container
        # subject ("video game official trailer gameplay" for GTA scene 7):
        # it fails the contract validator as a section_subject, so replace
        # it with the scene's own subject before it reaches any row.
        if subject and _is_format_phrase_subject(subject):
            subject = mech_subject or _compact_search_subject(global_anchor) or global_anchor
        active = mech_subject or subject or _compact_search_subject(global_anchor) or global_anchor
        context = _fallback_retrieval_context(active, scene_text, global_anchor)
        domain_category = str(mrow.get("domain_category") or "").strip() or context.get("domain_category") or "other"
        if domain_category == "other":
            domain_category = global_context.get("domain_category") or "other"
        mech_intent = str(mrow.get("visual_intent") or "")
        facet = mech_intent.split(": ", 1)[1].strip() if ": " in mech_intent else ""
        if not facet:
            # Boundary scenes carry no mechanical row (mrow is empty), so
            # take the facet from the mechanical plan row itself: it is
            # built from tokens distinctive to this scene's narration,
            # which the plan-level validator requires. The generic scene
            # facet (_scene_visual_facet) picks shared words instead
            # ("caribbean pacific" also names scenes 1/7/8/13) and fails
            # distinctiveness.
            mech_row = mech_rows[idx] if idx < len(mech_rows) and isinstance(mech_rows[idx], dict) else {}
            mech_visual = str(mech_row.get("visual_intent") or "")
            facet = mech_visual.split(": ", 1)[1].strip() if ": " in mech_visual else ""
        if not facet:
            facet = _scene_visual_facet(scene_text, active, domain_category)
        entity_kind = _entity_visual_kind(active, scene_text, domain_category, section)
        # The prototype types subjects from the scene's own words (a
        # studio/game/place is never a person); trust it over the
        # name-shape heuristic wherever they disagree on person/place/
        # event, and never let a name-like topic become a person
        # ("PlayStation 5"). Ship/food/object detections the prototype
        # lacks are kept when it says topic.
        proto_type = str(mrow.get("scene_subject_type") or "").strip().lower()
        if proto_type in {"person", "place", "event"}:
            entity_kind = proto_type
        elif proto_type == "topic" and entity_kind not in {"ship", "food", "object"}:
            entity_kind = "topic"
        if is_boundary:
            plan = _mechanical_scene_plan(scene_text, idx + 1, section_subject=subject, topic=plan_topic)
            entity_kind = _entity_visual_kind(active, scene_text, domain_category, section)
        else:
            plan = {
                "youtube": mrow.get("youtube") if isinstance(mrow.get("youtube"), dict) else {},
                "web": mrow.get("web") if isinstance(mrow.get("web"), dict) else {},
                "stock": mrow.get("stock") if isinstance(mrow.get("stock"), dict) else {},
                "visual_metaphor": mech_intent,
                "section_label": f"section_{idx + 1}" if subject else "intro",
            }
        container_id = str(
            section.get("container_id")
            or plan.get("section_label")
            or (f"section_{idx + 1}" if subject else "intro")
        ).strip()
        boundary = bool(
            idx == 0
            or section.get("container_boundary")
            or section.get("has_explicit_marker")
        )
        common = {
            "scene_number": idx + 1,
            "section_subject": subject,
            "scene_subject": str(mech_subject or section.get("scene_subject") or subject or active).strip(),
            # "thing" means unknown: the neutral type is "topic", never the
            # schema-invalid "entity" the old mapping emitted here.
            "scene_subject_type": entity_kind if entity_kind in {"person", "place", "object", "food", "ship", "animal", "event", "topic", "product"} else "topic",
            "container_type": entity_kind if entity_kind in {"person", "place", "object", "food", "ship", "animal", "event", "topic", "product"} else "topic",
            "subject_query": active,
            "topic_anchor": active,
            "global_topic": global_anchor,
            "domain_anchor": global_anchor,
            "domain_category": domain_category,
            "search_disambiguator": context.get("search_disambiguator") or active,
            # The contract requires >= 3 positive terms; a short subject
            # ("Food") can leave the context with only two, so pad from
            # the mechanical row's own terms (scene words, already >= 3).
            # Boundary scenes have no mechanical row: pad from the mech
            # row's terms anyway (see pad_terms above) plus the scene's
            # own content words, so the invariant holds on every path.
            "retrieval_positive_terms": _positive_terms(
                context.get("retrieval_positive_terms"), pad_terms, active,
                extra_terms=content_words(scene_text)),
            "retrieval_negative_terms": list(context.get("retrieval_negative_terms") or [])[:10],
            "youtube_query_intents": _default_youtube_intents(domain_category),
            "container_id": container_id,
            "section_id": container_id,
            "container_boundary": boundary,
            "section_transition": boundary or idx == 0,
            "visual_intent": _clean_query(facet or plan.get("visual_metaphor") or scene_text, scene_text),
            "forbidden_subjects": [item for item in all_subjects if item != subject][:12],
            "_llm": False,
            "_brain": False,
            "_shared_scene_brain": True,
            "_mechanical_query_plan": True,
        }

        yt = plan.get("youtube") if isinstance(plan.get("youtube"), dict) else {}
        # Scene-relative game: the validator requires at least one primary
        # across web+yt to name the game THIS scene narrates. The gaming
        # overrides below replace the prototype row's queries (which
        # already carry the suffix), so re-attach it here.
        _scene_game = game_for_scene(scene_text, title)
        game_suffix = (
            f" {_scene_game}"
            if domain_category == "gaming" and _scene_game and _scene_game.lower() not in active.lower()
            else ""
        )
        if domain_category == "gaming":
            yt = {
                "primary": f"{active} {facet}{game_suffix} gameplay no commentary" if facet else f"{active}{game_suffix} official gameplay",
                "supporting": f"{active} {facet}{game_suffix} official trailer" if facet else f"{active}{game_suffix} official trailer",
                "fallback": f"{active}{game_suffix} gameplay footage",
            }
        elif is_boundary and facet:
            # Boundary scenes take the old topic-anchored plan, whose
            # YouTube primary is built from a different facet than the
            # recomputed scene facet the flat visual_intent carries
            # (Panama scene 4: plan "Panam narrow strip" vs visual
            # intent "caribbean pacific"). The shape validator requires
            # the primary to carry a viewpoint word from the visual
            # intent, so rebuild it from the same facet the web lane
            # already uses. Non-boundary scenes keep the mechanical
            # row's primary, which already shares its facet.
            yt = {
                "primary": f"{active} {facet} documentary footage",
                "supporting": f"{active} {facet} footage",
                "fallback": f"{active} footage",
            }
        yt_primary = normalize_youtube_query(
            _clean_query(yt.get("primary"), f"{active} real footage"),
            anchor=active,
            category=context.get("domain_category") or "",
            fallback="documentary footage",
        )
        yt_supporting = normalize_youtube_query(
            _clean_query(yt.get("supporting"), f"{active} documentary footage"),
            anchor=active,
            category=context.get("domain_category") or "",
            fallback="archival footage",
        )
        yt_fallback = normalize_youtube_query(
            _clean_query(yt.get("fallback"), f"{active} footage"),
            anchor=active,
            category=context.get("domain_category") or "",
            fallback="documentary footage",
        )
        bundle["yt"].append({
            **common,
            "title_anchor": global_anchor,
            "query_source": "youtube",
            "search_query": yt_primary,
            "alt_search_query": yt_supporting,
            "fallback_query": yt_fallback,
            "youtube_query": yt_primary,
            "youtube_supporting_query": yt_supporting,
            "youtube_fallback_query": yt_fallback,
            "query_variants": _variants(yt_primary, yt_supporting, yt_fallback),
        })

        web = plan.get("web") if isinstance(plan.get("web"), dict) else {}
        if domain_category == "gaming":
            web = {
                "primary": f"{active} {facet}{game_suffix} official screenshot" if facet else f"{active}{game_suffix} official screenshot",
                "supporting": f"{active} {facet}{game_suffix} gameplay still" if facet else f"{active}{game_suffix} gameplay still",
                "fallback": f"{active}{game_suffix} official image",
            }
        elif entity_kind == "person":
            # Person anchors need explicit portrait/photo nouns.  A generic
            # ``documentary image`` query routinely returns logos, objects,
            # or unrelated people with the same surname.
            web = {
                "primary": f"{active} {facet} action photograph" if facet else f"{active} portrait",
                "supporting": f"{active} photo",
                "fallback": f"{active} official event photograph",
            }
        elif entity_kind == "place":
            web = {
                "primary": f"{active} {facet} landmark photograph" if facet else f"{active} landmark photograph",
                "supporting": f"{active} location photo",
                "fallback": f"{active} skyline landscape photograph",
            }
        elif entity_kind == "food":
            web = {
                "primary": f"{active} {facet} food photograph" if facet else f"{active} food photograph",
                "supporting": f"{active} fresh produce photo",
                "fallback": f"{active} recipe ingredient photograph",
            }
        elif entity_kind == "object":
            web = {
                "primary": f"{active} {facet} product photograph" if facet else f"{active} product photograph",
                "supporting": f"{active} detail photo",
                "fallback": f"{active} official reference image",
            }
        elif entity_kind == "ship":
            web = {
                "primary": f"{active} {facet} archival photograph" if facet else f"{active} ship archival photograph",
                "supporting": f"{active} ship exterior photo",
                "fallback": f"{active} historical archive photograph",
            }
        elif facet:
            web = {
                "primary": f"{active} {facet} photo",
                "supporting": f"{active} {facet} documentary image",
                "fallback": f"{active} {facet}",
            }
        web_primary = _clean_query(web.get("primary"), f"{active} photo")
        web_supporting = _clean_query(web.get("supporting"), f"{active} image")
        web_fallback = _clean_query(web.get("fallback"), active)
        bundle["web"].append({
            **common,
            "query_source": "web",
            "search_query": web_primary,
            "supporting_search_query": web_supporting,
            "alt_search_query": web_supporting,
            "fallback_query": web_fallback,
            "primary_query": web_primary,
            "specific": web_primary,
            "medium": web_supporting,
            "general": web_fallback,
            "web_query": web_primary,
            "web_alt_query": web_supporting,
            "web_fallback": web_fallback,
            "query_variants": _variants(web_primary, web_supporting, web_fallback),
        })

        stock = plan.get("stock") if isinstance(plan.get("stock"), dict) else {}
        if facet:
            stock = {
                **stock,
                "primary_query": facet,
                "alt_query": f"{facet} close up",
                "fallback_query": f"{facet} footage",
            }
        stock_primary = _clean_query(stock.get("primary_query"), f"{active} action")
        stock_alt = _clean_query(stock.get("alt_query"), f"{active} scene")
        stock_fallback = _clean_query(stock.get("fallback_query"), f"{active} footage")
        bundle["stock"].append({
            **common,
            "query_source": "stock",
            "primary_query": stock_primary,
            "alt_query": stock_alt,
            "fallback_query": stock_fallback,
            "stock_query": stock_primary,
            "stock_alt_query": stock_alt,
            "stock_fallback_query": stock_fallback,
            "subject": stock.get("subject") or "people",
            "category": stock.get("category") or context.get("domain_category") or "other",
            "setting": stock.get("setting") or "outdoor",
            "preferred_source": stock.get("preferred_source") or "pexels",
            "scene_focus_terms": [stock_primary],
            "support_terms": [active, global_anchor],
            "query_variants": _variants(stock_primary, stock_alt, stock_fallback),
        })

    return bundle
