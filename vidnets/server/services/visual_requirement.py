"""Canonical, identity-first visual requirements for media retrieval.

The media providers are intentionally dumb adapters.  This module is the
single place where a script is turned into a visual identity contract and the
single place where cheap candidate metadata can prove (or disprove) that
identity before a file reaches the expensive vision gate.

It is deliberately conservative.  A generic word such as ``car`` may express
the category, but it can never prove a particular make, model, animal, year,
or era.  When the contract cannot be proven, callers receive a grounded
graphic fallback instead of an unrelated external asset.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from functools import lru_cache
import os
import re
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

import hashlib
import json


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "by", "for", "from", "how",
    "in", "is", "it", "of", "on", "or", "that", "the", "this", "to",
    "was", "were", "what", "when", "where", "which", "who", "why", "with",
    "we", "you", "they", "unlike", "changed", "begin", "begins", "today",
    "just", "then", "now", "here", "there", "after", "before", "during",
    "later", "earlier", "since", "still", "even", "also", "already", "soon",
    "one", "two", "three", "four", "five", "first", "second", "third",
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
}
_GENERIC_SUBJECTS = {
    "car", "cars", "automobile", "automobiles", "vehicle", "vehicles",
    "history", "story", "documentary", "technology", "person", "people",
    "thing", "things", "world", "event", "events", "subject", "topic",
}

# Search indexes use formal titles and birth names interchangeably for public
# figures. These are retrieval/identity aliases only: a candidate still needs
# to pass the scene-aware pixel verifier before it can render.
_KNOWN_PERSON_ALIASES = {
    "catherine princess of wales": ("Kate Middleton", "Princess Catherine", "Catherine, Duchess of Cambridge"),
    "kate middleton": ("Catherine, Princess of Wales", "Princess Catherine"),
    "prince archie": ("Archie Harrison Mountbatten-Windsor", "Archie Mountbatten-Windsor"),
    "princess lilibet": ("Lilibet Diana Mountbatten-Windsor", "Lilibet Mountbatten-Windsor"),
    "prince harry": ("Henry, Duke of Sussex",),
    "meghan markle": ("Meghan, Duchess of Sussex",),
    "prince william": ("William, Prince of Wales",),
    "king charles iii": ("Charles III",),
    "princess eugenie": ("Eugenie, Princess of York", "Eugenie of York"),
}
_AUTO_TERMS = {
    "car", "cars", "automobile", "automobiles", "vehicle", "vehicles",
    "sedan", "coupe", "convertible", "roadster", "sports car", "motorcar",
    "motor vehicle", "chassis", "engine", "bonnet", "hood", "wheelbase",
    # ``drive`` is common sports language (for example, an NFL scoring drive)
    # and must not make an unrelated scene look automotive. Concrete vehicle
    # terms such as ``driving`` and ``engine`` remain valid domain evidence.
    "dashboard", "driving", "racing", "motoring",
}
_AUTO_BRAND_TERMS = {
    "toyota", "honda", "nissan", "mazda", "subaru", "suzuki", "mitsubishi",
    "lexus", "acura", "infiniti", "ford", "chevrolet", "cadillac", "buick",
    "chrysler", "dodge", "jeep", "tesla", "bmw", "mercedes", "volkswagen",
    "audi", "porsche", "volvo", "saab", "fiat", "ferrari", "lamborghini",
    "maserati", "alfa romeo", "peugeot", "renault", "citroen", "hyundai",
    "kia", "genesis", "tata", "mahindra", "proton", "perodua", "daewoo",
    "isuzu", "daihatsu", "land rover", "range rover", "bentley", "rolls royce",
}
_ANIMAL_TERMS = {
    "animal", "wildlife", "mammal", "big cat", "predator", "zoo", "safari",
    "habitat", "jungle", "fur", "paw", "roar", "species", "wild",
    "cheetah", "leopard", "lion", "tiger",
}
_SHIP_TERMS = {
    "ship", "ships", "vessel", "liner", "boat", "maritime", "naval",
    "voyage", "sinking", "wreck", "shipyard", "ocean liner",
}
_RAIL_CREW_TERMS = {
    # Keep the singular noun.  ``trains`` is also the common verb in person
    # biographies ("Kohli trains every day") and must not promote a cricket
    # or athlete scene to the rail-crew contract.
    "train", "rail", "railway", "railroad", "locomotive",
    "conductor", "conductors",
    "dispatcher", "dispatchers", "station staff", "train crew", "rail crew",
}
_RAIL_CREW_ROLE_TERMS = {
    "conductor", "conductors", "locomotive engineer", "locomotive engineers",
    "train driver", "train drivers", "railway worker", "railway workers",
    "railroad worker", "railroad workers", "dispatcher", "dispatchers",
}
_RAIL_CREW_CONTEXT_TERMS = {
    "crew", "crews", "maintenance", "operations", "operator", "operators",
    "yard", "yardmaster", "yardmasters", "switchman", "switchmen", "cab",
    "railway worker", "railway workers", "railroad worker", "railroad workers",
}
_PERSON_TERMS = {
    "born", "actor", "actress", "president", "king", "queen", "player", "athlete",
    "prince", "princess", "duke", "duchess", "royal", "royalty", "monarch", "politician", "leader", "minister", "celebrity",
}
_PERSON_ROLE_TERMS = {
    "prince", "princess", "king", "queen", "duke", "duchess", "royal",
    "royalty", "monarch", "president", "prime minister", "politician",
    "athlete", "footballer", "cricketer", "actor", "actress", "singer",
    "musician", "rapper", "journalist", "presenter", "streamer",
}
# Explicit fictional/animal-object markers are strong evidence that a result
# is a character or prop rather than the requested real person.  These are
# deliberately narrow so ordinary context such as ``animal-rights`` does not
# become a hard rejection.
_PERSON_CONFUSABLE_TERMS = {
    "frog", "toad", "cartoon", "toy", "mascot", "anime",
    "fictional character", "fantasy character", "plush", "figurine",
    "action figure", "storybook character", "wax figure", "wax figures",
    "waxwork", "waxworks", "wax museum", "madame tussauds",
    "second life", "secondlife", "virtual world", "virtual avatar",
    "game avatar", "3d avatar", "avatar marketplace",
}
_PERSON_VIRTUAL_CATALOG_TERMS = {
    "skin", "hair", "eyes", "mesh", "outfit", "body", "head",
}
_GAME_DOMAIN_TERMS = {
    "gameplay", "game play", "trailer", "walkthrough", "playthrough",
    "cutscene", "video game", "video games", "gaming", "game footage",
    "official footage", "cinematic", "mission", "open world", "pc game",
    "playstation", "xbox", "nintendo", "rockstar games",
}
_MODERN_MARKERS = {
    "modern car", "modern vehicle", "latest model", "contemporary car", "2024", "2025", "2026",
    "2027", "next generation", "facelift", "type r", "mk4", "mk5",
}
_HISTORICAL_MARKERS = {
    "old", "older", "classic", "classics", "vintage", "antique", "heritage",
    "historical", "historic", "archival", "archive", "period",
}


def _historical_evidence(text: str, subject: str = "", subject_type: str = "") -> list[str]:
    """Return explicit historical-era evidence without over-reading prose.

    ``early`` is intentionally not a standalone historical marker. In a
    present-day occupational script, phrases such as ``early starts`` and
    ``early shift`` describe a schedule, not an era. Treat it as historical
    only when it is attached to an explicit date/century/period expression.
    """
    normalized = _norm(text)
    # ``old``, ``older``, and ``period`` are ambiguous in ordinary narration
    # (for example, ``older passengers`` or ``long periods away``). They need
    # a subject-local signal before they become an era constraint.
    ambiguous_markers = {"old", "older", "period"}
    markers = sorted(
        marker for marker in _HISTORICAL_MARKERS - ambiguous_markers
        if _contains_phrase(normalized, marker)
    )
    subject_terms = set(_tokens(subject))
    subject_terms.update({
        "car", "automobile", "vehicle", "train", "railway", "railroad",
        "locomotive", "ship", "boat", "vessel", "crew",
    })
    old_subject_window = False
    for marker in ("old", "older"):
        for match in re.finditer(r"(?<![a-z0-9])" + marker + r"(?![a-z0-9])", normalized):
            context = normalized[max(0, match.start() - 36):match.end() + 36]
            if any(_contains_phrase(context, term) for term in subject_terms):
                old_subject_window = True
                break
        if old_subject_window:
            break
    if old_subject_window:
        markers.append("old")
    if any(
        re.search(r"\bperiod\s+(?:piece|film|drama|costume|setting|train|car|vehicle)\b", sentence)
        for sentence in re.split(r"[.!?;:\n]+", normalized)
    ):
        markers.append("period")
    if re.search(
        r"\b(?:early|mid|late)\s+(?:(?:18|19|20)\d{2}s?|\d{1,2}(?:st|nd|rd|th)\s+century|century|period|era)\b",
        normalized,
    ):
        markers.append("early")
    return sorted(set(markers))

_SUBJECT_TYPE_ALIASES = {
    "car": "automobile",
    "cars": "automobile",
    "vehicle": "automobile",
    "vehicles": "automobile",
    "auto": "automobile",
    "automotive": "automobile",
    "automotive_history": "automobile",
    "automotive history": "automobile",
    "motor vehicle": "automobile",
    "product": "named_entity",
    "object": "named_entity",
    "wildlife": "animal",
    "game": "video_game",
    "video game": "video_game",
    "video_game": "video_game",
    "gaming": "video_game",
    "train crew": "rail_crew",
    "rail crew": "rail_crew",
    "railway crew": "rail_crew",
    "railroad crew": "rail_crew",
}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


class _NormalizedSearchText(str):
    """A lexical key which must not be normalized again per search term."""


@lru_cache(maxsize=512)
def _cached_search_key(value: str) -> str:
    from .documentary_subject import search_key
    return _NormalizedSearchText(search_key(value))


@lru_cache(maxsize=128)
def _cached_long_search_key(value: str) -> str:
    from .documentary_subject import search_key
    return _NormalizedSearchText(search_key(value))


def _norm(value: Any) -> str:
    if isinstance(value, _NormalizedSearchText):
        return value
    value = str(value or '')
    # Cache lexical normalization only, never a candidate verdict. Bound
    # both entry count and input size so long scripts cannot grow the cache.
    if len(value) <= 8192:
        return _cached_search_key(value)
    if len(value) <= 131072:
        return _cached_long_search_key(value)
    from .documentary_subject import search_key
    return _NormalizedSearchText(search_key(value))


def _tokens(value: Any) -> list[str]:
    return [token for token in _norm(value).split() if token]


def _contains_phrase(text: str, phrase: str) -> bool:
    if not phrase:
        return False
    normalized_text, normalized_phrase = _norm(text), _norm(phrase)
    if not normalized_phrase:
        return False
    # Keys contain Unicode letters/digits separated by spaces. Literal
    # lookup with word boundaries preserves the regex decision and avoids
    # regex-cache churn on thousands of candidate/scene combinations.
    start = 0
    while True:
        index = normalized_text.find(normalized_phrase, start)
        if index < 0:
            return False
        end = index + len(normalized_phrase)
        if (index == 0 or normalized_text[index - 1] == ' ') and (end == len(normalized_text) or normalized_text[end] == ' '):
            return True
        start = index + 1


def _person_confusable_hits(normalized_metadata: str) -> list[str]:
    hits = [
        term for term in _PERSON_CONFUSABLE_TERMS
        if _contains_phrase(normalized_metadata, term)
    ]
    # A real person's image can mention hair/skin in ordinary editorial copy.
    # Treat it as a synthetic marketplace listing only when explicit roleplay
    # context is paired with multiple catalog fields (e.g. "Harry skin",
    # "Prince hair", "eyes", as in virtual-world avatar listings).
    catalog_hits = [
        term for term in _PERSON_VIRTUAL_CATALOG_TERMS
        if _contains_phrase(normalized_metadata, term)
    ]
    if _contains_phrase(normalized_metadata, "roleplay") and len(catalog_hits) >= 2:
        hits.append("virtual_avatar_catalog")
    return hits


_ASSET_METADATA_FIELDS = (
    "title", "description", "tags", "subject", "filename", "caption",
    "alt", "channel", "author", "categories",
)

# Search providers commonly fill a missing card title with the retrieval
# query (sometimes adding ``result 3``/``image 3``).  The query is intent, not
# evidence about the pixels; keeping this marker here lets every downstream
# gate apply the same rule instead of trusting a provider-specific fallback.
_QUERY_ECHO_SUFFIX_RE = re.compile(
    r"(?:\s+(?:result|image|photo|picture|thumbnail|item|card|match)\s*#?\s*\d+)?\s*$",
    re.IGNORECASE,
)


def _is_query_echo_title(candidate: Mapping[str, Any], value: str) -> bool:
    """Return true when a title is only a search-intent echo."""
    metadata = candidate.get("metadata")
    if isinstance(metadata, Mapping) and (
        metadata.get("title_is_query") or metadata.get("query_echo")
    ):
        return True
    if candidate.get("title_is_query") or candidate.get("query_echo"):
        return True
    title_norm = _norm(value)
    if not title_norm:
        return False
    queries = (
        candidate.get("query"), candidate.get("query_used"),
        candidate.get("search_query"), candidate.get("retrieval_query"),
    )
    for query in queries:
        query_norm = _norm(query)
        if not query_norm:
            continue
        if title_norm == query_norm:
            return True
        # Only remove a generic numeric suffix.  A real provider caption such
        # as ``Prince Harry at the 2019 ceremony`` must remain evidence.
        stripped = _QUERY_ECHO_SUFFIX_RE.sub("", title_norm).strip()
        if stripped == query_norm:
            return True
    return False


def candidate_asset_metadata(candidate: Mapping[str, Any]) -> str:
    """Return asset-authored metadata without retrieval-intent echoes.

    Scrapers sometimes put their own boilerplate (for example, ``Yandex
    result for: <query>``) in a description field.  That text is useful for
    debugging a provider but it is not evidence about the downloaded asset.
    Centralizing this normalization prevents such echoes from satisfying the
    identity or context contract in one pipeline while being rejected in
    another.  ``query``, ``query_used`` and similar provenance fields are
    intentionally never read here.
    """
    values: list[str] = []
    for key in _ASSET_METADATA_FIELDS:
        value = str(candidate.get(key) or "").strip()
        if not value:
            continue
        if key == "title" and _is_query_echo_title(candidate, value):
            continue
        if key == "description" and re.match(
            r"^\s*(?:(?:yandex|duckduckgo)\s+result\s+for\s*:|yandex\s+visual\s+search\s+index|bing\s+visual\s+search\s+index)",
            value,
            flags=re.IGNORECASE,
        ):
            continue
        if key == "description" and isinstance(candidate.get("metadata"), Mapping):
            if candidate["metadata"].get("description_is_query"):
                continue
        values.append(value)
    return " ".join(values)


def _unique(values: Iterable[Any]) -> Tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _text(value)
        key = _norm(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)


def _title_subject(title: str) -> str:
    """Use the title's leading subject phrase, never a sentence opener."""
    from .documentary_subject import title_hook_subject, collection_title_subject
    collection = collection_title_subject(title)
    if collection:
        return collection
    hooked = title_hook_subject(title)
    if hooked:
        return hooked
    raw = _text(title)
    if not raw:
        return ""
    first = re.split(r"\s*(?:[:|]|\s[-–—]\s)\s*", raw, maxsplit=1)[0]
    # Parenthetical delivery/source labels belong to the UI title, not to the
    # subject identity (for example ``Top 4 Shipwrecks (Web + YT)``).
    first = re.sub(r"\s*\([^)]*\)\s*$", "", first).strip()
    first = re.sub(r"^(?:the|a|an)\s+", "", first, flags=re.I).strip(" ,.;!?()[]")
    # Editorial wrappers are useful for the human title but are not the
    # identity being searched.  Without this, titles such as "The History of
    # the Jaguar E-Type" turn the canonical subject into "History of Jaguar
    # E-Type", which weakens both provider search and candidate verification.
    first = re.sub(
        r"^(?:history|story|origins?|rise|fall|evolution|legacy|journey|inside)\s+of\s+",
        "",
        first,
        flags=re.I,
    )
    first = re.sub(
        r"^(?:how|why|what)\s+(?:the\s+)?(?:history|story|evolution|legacy)\s+of\s+",
        "",
        first,
        flags=re.I,
    )
    # Strip common editorial wrappers when they follow the named subject.
    # Keeping ``biography``/``travel guide`` in the canonical identity makes
    # provider queries search for a title phrase instead of the person/place
    # itself, while removing them from the end does not change the identity.
    first = re.sub(
        r"\s+(?:travel|tourism|city|destination)\s+guide\s*$",
        "",
        first,
        flags=re.I,
    )
    first = re.sub(
        r"\s+(?:biography|bio|profile|documentary|documentaries|film|movie|guide|overview|explained|facts|timeline|history|story|legacy|career|life|journey|product|review|specifications)\s*$",
        "",
        first,
        flags=re.I,
    )
    # Split before auxiliary/predicate verbs if the title contains a complete sentence clause
    verb_match = re.search(
        r"\s+(?:is|are|was|were|has|have|had|will|would|can|could|still|remains?|became|becomes?)\b",
        first,
        flags=re.I,
    )
    if verb_match and verb_match.start() >= 3:
        first = first[:verb_match.start()].strip(" ,.;!?()[]")
    words = first.split()
    while words and _norm(words[0]) in _STOPWORDS:
        words.pop(0)
    subject = " ".join(words[:8]).strip()
    if not subject or _norm(subject) in _STOPWORDS:
        return ""
    # A listicle shell is a container label.  Treating it as the identity of
    # every scene makes a later timeline/list item inherit the first named
    # entity from the full script.  Scene-local evidence is more trustworthy
    # for these titles and is selected by ``build_visual_requirement``.
    if re.match(
        r"^(?:top|best|greatest|worst|most)\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
        _norm(subject),
    ):
        return ""
    if re.match(
        r"^(?:(?:january|february|march|april|may|june|july|august|september|october|november|december|\d+|[,\s]|one|two|three|four|five|six|seven|eight|nine|ten|in|on|at|the|a|an|of)+)$",
        subject,
        flags=re.I,
    ):
        return ""
    return subject


_SCENE_COMMON_CAPITALS = {
    "a", "an", "the", "in", "on", "at", "by", "from", "for", "today",
    "this", "these", "those", "some", "one", "when", "after", "before",
    "german", "british", "american", "french", "italian", "spanish", "russian",
    "chinese", "japanese", "irish", "european", "world", "u", "boat", "liner",
    "ship", "ships", "vessel", "vessels", "boat", "boats", "car", "cars",
    "automobile", "vehicle", "vehicles", "official", "today",
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
}

_TITLE_CONTAINER_WORDS = {
    "famous", "popular", "notable", "great", "greatest", "best", "worst",
    "history", "story", "stories", "documentary", "documentaries", "guide",
    "overview", "explained", "shipwreck", "shipwrecks", "wreck", "wrecks",
    "ships", "events", "moments", "facts", "examples", "types", "kinds",
    # Broad editorial containers.  A title such as ``British Royal Family``
    # describes the programme, not the person in every beat; scene-local
    # names must be allowed to own the identity contract below.
    "family", "families", "royal", "british", "monarchy", "monarch",
}


def _scene_action_subject(scene_text: str) -> str:
    """Keep the visible actor distinct from an object's previous owner.

    Only explicit action-plus-provenance clauses qualify. A historical owner
    mentioned after 'belonged to' must not acquire the current actor's date.
    """
    text = _text(scene_text)
    role = r"(?i:king|queen|prince|princess|duke|duchess|president)"
    name = r"[A-Z][\w’'-]*(?:\s+[A-Z][\w’'-]*){0,2}"
    pattern = (rf"\b(?P<actor>(?:{role}\s+)?{name})"
               rf"(?P<apposition>,\s*{role}(?:\s+of\s+{name})?,?)?\s+"
               r"(?i:(?:has |had |was |is )?(?:wore|wears|wearing|carried|carries|used|uses|drove|drives))\b")
    for match in re.finditer(pattern, text):
        if not re.search(r"\b(?:belonged to|(?:once |formerly |previously |originally )?owned by|inherited from|once worn by)\b",
                         text[match.end():], re.I):
            continue
        actor = match.group('actor').strip()
        apposition = (match.group('apposition') or '').strip(' ,')
        if apposition:
            return f'{actor}, {apposition}'
        if len(actor.split()) >= 2:
            return actor
    return ''


def _scene_role_subject(scene_text: str) -> str:
    """Return the first explicit title-plus-name person in one scene.

    The role prefix is matched case-insensitively, while the name remains
    capitalized.  Applying ``IGNORECASE`` to the entire expression would let
    connectors such as ``and`` and ``are`` become part of the name and produce
    unusable queries (``Prince Harry and Meghan Markle are``).
    """
    text = _text(scene_text)
    if not text:
        return ""
    role = (
        r"(?i:king|queen|prince|princess|president|prime minister|"
        r"chancellor|duke|duchess|emperor|empress|pope|senator|governor)"
    )
    pattern = rf"\b(?P<role>{role})\s+(?P<name>[A-Z][A-Za-z'’-]*(?:\s+(?:[A-Z][A-Za-z'’-]*|[IVX]{{2,4}})){{0,3}})\b"
    match = re.search(pattern, text)
    if not match:
        return ""
    # Preserve the source's normal capitalization but normalize whitespace and
    # remove punctuation that can leak into provider query variants.
    value = re.sub(r"\s+", " ", match.group(0)).strip(" ,.;:!?\"'()[]")
    # Narration commonly uses a possessive immediately after a person's
    # surname (``Prince William's position``).  The possessive is grammar,
    # not part of the provider identity/query.
    return re.sub(r"['’]s$", "", value, flags=re.IGNORECASE).strip()


def _scene_subject(scene_text: str) -> str:
    """Extract the strongest named entity from one scene only.

    Timeline/listicle scenes often carry only a year as their container label,
    while the actual entity appears in the narration (``Lusitania`` or
    ``Costa Concordia``).  Looking at the scene before the full script avoids
    borrowing the first entity from a different scene.  The extractor is
    intentionally small and conservative: explicit ship prefixes and names
    following a typed label score highest, while sentence openers, nationalities
    and locations introduced by ``in/off/near`` are ignored.
    """
    text = _text(scene_text)
    if not text:
        return ""
    candidates: list[tuple[int, int, str]] = []

    # Possessive names in religious buildings identify the place, not its
    # namesake (St George's Chapel is not a portrait of Saint/Prince George).
    for match in re.finditer(
        r"\b(?:St\.?|Saint)\s+[A-Z][A-Za-z'-]*(?:['’]s)?\s+(?:Chapel|Church|Cathedral)\b", text,
    ):
        candidates.append((95, -match.start(), match.group(0)))

    # Prefix-qualified names are unambiguous and should win even when the
    # sentence starts with a date or a nationality adjective.
    for match in re.finditer(
        r"\b(?:RMS|SS|HMS|USS|MV|MS|USNS|KMS)\s+[A-Z][A-Za-z0-9'-]*(?:\s+[A-Z][A-Za-z0-9'-]*){0,3}",
        text,
    ):
        candidates.append((100, -match.start(), match.group(0).strip(" ,.;:()[]")))

    # A named entity following a domain label: ``liner Lusitania``,
    # ``ship Costa Concordia``, ``actor Sean Connery``.
    typed_labels = (
        "liner", "ship", "vessel", "boat", "car", "automobile", "vehicle",
        "aircraft", "actor", "actress", "president", "king", "queen", "prince",
        "princess", "duke", "duchess", "footballer", "singer", "author",
    )
    # Keep the role alternatives case-insensitive while preserving the
    # capitalized-name guard below.  A global IGNORECASE flag would turn
    # sentence connectors into false name tokens.
    label_pattern = "|".join(re.escape(value) for value in typed_labels)
    for match in re.finditer(
        rf"\b(?i:{label_pattern})\s+(?P<name>[A-Z][A-Za-z0-9'-]*(?:\s+[A-Z][A-Za-z0-9'-]*){{0,3}})",
        text,
    ):
        name = match.group("name").strip(" ,.;:()[]")
        if name:
            candidates.append((85, -match.start(), name))

    # General capitalized runs catch names such as ``Costa Concordia`` and
    # single-token names such as ``Lusitania``.  Score them below the typed
    # forms and penalize a location clause introduced by a preposition.
    for match in re.finditer(
        r"\b(?P<name>[A-Z][A-Za-z0-9'-]*(?:\s+[A-Z][A-Za-z0-9'-]*){0,3})\b",
        text,
    ):
        name = match.group("name").strip(" ,.;:()[]")
        parts = name.split()
        from .documentary_subject import is_source_attribution_subject
        if is_source_attribution_subject(name, text):
            continue
        if not name or all(part.casefold() in _SCENE_COMMON_CAPITALS for part in parts):
            continue
        if len(parts) == 1 and parts[0].casefold() in _SCENE_COMMON_CAPITALS:
            continue
        prefix = text[max(0, match.start() - 12):match.start()].casefold()
        score = 50 + min(18, (len(parts) - 1) * 8)
        if re.search(r"\b(?:in|off|near|at|from|on)\s+$", prefix):
            score -= 18
        candidates.append((score, -match.start(), name))

    if not candidates:
        return ""
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def _script_subject(script: str) -> str:
    """Find a defensible named phrase without promoting ``We``/``Unlike``."""
    text = _text(script)
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*){0,4})\b", text):
        value = re.sub(r'^(?:the|a|an)\s+', '', match.group(1).strip(), flags=re.I)
        if value.casefold() in {'its', 'it', 'this', 'that', 'these', 'those', 'he', 'she', 'they', 'here', 'there'}:
            continue
        if _norm(value) in _STOPWORDS or any(_norm(word) in _STOPWORDS for word in value.split()[:1]):
            continue
        if len(value) <= 2 or value.casefold() in {"I", "We".casefold()}:
            continue
        return value
    return ""


def scene_subject_contract(title: str, scene_text: str, context: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """Resolve a grounded local subject independently of the film's niche.

    Structured direction is accepted only with local textual support. The
    conservative parser rescues explicit actors, never a cited publisher or
    an incidental former owner. Pronouns retain the established film identity.
    """
    context = context if isinstance(context, Mapping) else {}
    text = _text(scene_text)
    normalized = _norm(text)
    film = _title_subject(title)
    subject = _text(context.get('visual_subject') or context.get('scene_subject'))
    evidence = _text(context.get('subject_evidence'))
    from .documentary_subject import is_source_attribution_subject
    # Physical/process direction can combine inflected words from the quoted
    # evidence ("clutch wear" / "clutches would wear out"). Named identities
    # still require the exact name; this is not a fuzzy alias rule for people.
    def word_stem(word):
        if len(word) > 5 and word.endswith('ies'):
            return word[:-3] + 'y'
        if len(word) > 5 and word.endswith(('ches', 'shes', 'xes', 'zes')):
            return word[:-2]
        return word[:-1] if len(word) > 4 and word.endswith('s') and not word.endswith('ss') else word
    evidence_valid = bool(not evidence or _contains_phrase(normalized, _norm(evidence)))
    directed_type = _norm(context.get('visual_subject_type'))
    subject_words = {word_stem(word) for word in _tokens(subject) if word not in _STOPWORDS}
    evidence_words = {word_stem(word) for word in _tokens(evidence or text)}
    compositional = bool(directed_type in {'component', 'object', 'process', 'activity', 'mechanism', 'concept'}
                         and subject_words and subject_words.issubset(evidence_words))
    grounded = bool(subject and (_contains_phrase(normalized, _norm(subject)) or compositional)
                    and evidence_valid
                    and not is_source_attribution_subject(subject, text))
    origin = 'grounded_director' if grounded else ''
    previous_subject = _text(context.get('_previous_scene_subject'))
    previous_text = _text(context.get('_previous_scene_text'))
    if (not grounded and subject and _norm(subject) == _norm(previous_subject)
            and _contains_phrase(previous_text, subject)
            and re.search(r'\b(?:he|she|his|her|they|their|it|its)\b', text, re.I)
            and (not evidence or _contains_phrase(normalized, _norm(evidence)))):
        grounded, origin = True, 'grounded_antecedent'
    if not grounded:
        subject = _scene_action_subject(text) or _scene_role_subject(text) or _scene_subject(text)
        if subject and not is_source_attribution_subject(subject, text):
            escaped = re.escape(subject)
            # Focus/agency, rather than capitalization, authorizes a local
            # override in a single-topic film. This applies across domains.
            actor = re.search(rf'(?<!\w){escaped}(?!\w)(?:,?\s+who)?\s+(?:was |is |has |had )?'
                r'(?:invented|patented|discovered|founded|developed|designed|created|built|introduced|'
                r'won|scored|painted|wrote|performed|announced|launched|opened|died|ruled|reigned)\b', text, re.I)
            typed = re.search(rf'\b(?:inventor|scientist|engineer|doctor|physician|author|artist|'
                rf'composer|founder|astronaut|athlete|actor|actress|singer)\s+{escaped}(?!\w)', text, re.I)
            focused = re.search(rf'\b(?:begins? with|focus(?:es)? on|turn(?:s)? to|known as|called)\s+{escaped}(?!\w)', text, re.I)
            grounded = bool(actor or typed or focused or _scene_action_subject(text))
            origin = 'explicit_scene_focus' if grounded else ''
    explicit_scope = origin in {'grounded_director', 'grounded_antecedent'} and bool(context.get('identity_scope'))
    if not grounded or not subject or (_norm(subject) == _norm(film) and not explicit_scope):
        return {'film_subject': film, 'scene_local': False, 'origin': 'film_context'}
    # Do not let a comparison's subordinate object become its actor.
    prefix = text[:text.casefold().find(subject.casefold())].rstrip() if subject.casefold() in text.casefold() else ''
    if re.search(r'\b(?:according to|reported by|belonged to|owned by|unlike|compared with|compared to)\s*$', prefix, re.I):
        return {'film_subject': film, 'scene_local': False, 'origin': 'incidental_entity'}
    kind = _norm(context.get('visual_subject_type')) if origin in {'grounded_director', 'grounded_antecedent'} else ''
    kind = _SUBJECT_TYPE_ALIASES.get(kind, kind)
    allowed = {'person', 'organization', 'product', 'place', 'event', 'concept', 'named_entity',
               'automobile', 'ship', 'animal', 'rail_crew', 'video_game'}
    if kind and kind not in allowed and origin in {'grounded_director', 'grounded_antecedent'}:
        # New niches and scientific/component types need no new whitelist.
        # Their grounded identity remains searchable with a neutral type.
        kind = 'named_entity'
    if kind not in allowed:
        human = bool(re.search(rf'{re.escape(subject)}(?:,?\s+who)?\s+(?:was |is |has |had )?'
            r'(?:invented|patented|discovered|founded|won|scored|painted|wrote|performed|died|ruled|reigned)\b', text, re.I)
            or re.search(rf'\b(?:inventor|scientist|doctor|author|artist|composer|astronaut|athlete|actor|actress|singer)\s+{re.escape(subject)}', text, re.I))
        kind = 'person' if human and len(_tokens(subject)) >= 2 else ''
    return {'film_subject': film, 'scene_subject': subject, 'scene_local': True,
            'subject_type': kind, 'subject_evidence': evidence or text,
            'directed_type': str(context.get('visual_subject_type') or ''),
            'identity_scope': str(context.get('identity_scope') or ('event' if kind == 'event' else 'subject')),
            'visible_target': _text(context.get('visible_target') or context.get('visual_intent') or text),
            'origin': origin}


def _is_royal_institution(subject: str) -> bool:
    # A role adjective is not a person's identity. Keep this anchored to
    # institutional noun phrases so named monarchs still require portraits
    # of the correct person, even when their narration discusses warrants.
    return bool(re.fullmatch(
        r"(?:the )?royal (?:warrants?(?: (?:system|holders?|association))?|charters?(?: system)?|household|mint|mail|society)",
        _norm(subject),
    ))


def _is_generic_process(subject: str) -> bool:
    """A title-cased physical/economic process is not a named product."""
    return bool(re.fullmatch(
        r'(?:how )?(?:anti lock braking(?: systems?)?|wheel speed sensors?|hydraulic pressure|'
        r'energy conversion|signal processing|compound interest|supply and demand|photosynthesis)'
        r'(?: works?| explained)?', _norm(subject)))


_ORGANIZATION_AND_LEAGUE_TERMS = {
    "nfl", "the nfl", "nba", "the nba", "mlb", "the mlb", "nhl", "the nhl",
    "fifa", "uefa", "ioc", "ncaa", "f1", "formula 1", "nascar", "mls", "pga",
    "ufc", "wwe", "premier league", "champions league", "super bowl", "world cup",
    "nasa", "fbi", "cia", "un", "nato", "who", "cdc", "fda", "sec", "irs", "doj",
    "apple", "google", "microsoft", "amazon", "meta", "tesla", "spacex", "netflix",
    "cowboys", "ravens", "chiefs", "dolphins", "chargers", "bills", "rams", "broncos",
    "patriots", "packers", "49ers", "steelers", "eagles", "lakers", "celtics", "warriors",
    "bulls", "yankees", "dodgers", "red sox", "cubs", "manchester united", "real madrid",
    "barcelona", "bayern munich", "liverpool", "arsenal", "chelsea", "manchester city",
}

_ORGANIZATION_SUFFIX_WORDS = {
    "league", "association", "federation", "committee", "union", "organization", "organisation",
    "conference", "franchise", "team", "club", "corporation", "corp", "inc", "ltd", "company",
    "agency", "department", "ministry", "parliament", "congress", "senate", "foundation",
    "institute", "university", "college", "board", "council",
}


def _league_topic_anchor(name: str) -> str:
    """Separate a league's identity from a generic film/topic suffix."""
    norm = _norm(name).removeprefix('the ')
    leagues = {'nfl': 'NFL', 'nba': 'NBA', 'mlb': 'MLB', 'nhl': 'NHL', 'fifa': 'FIFA', 'ufc': 'UFC'}
    words = norm.split()
    if not words or words[0] not in leagues:
        return ''
    topic_words = {'four', 'downs', 'down', 'ten', 'yards', 'rules', 'rule', 'explained', 'formations', 'formation',
                   'strategy', 'strategies', 'history', 'season', 'highlights', 'football', 'basketball', 'baseball',
                   'how', 'works', 'the', 'of', 'and', 'a', 'game', 'games', 'league', 'draft', 'playoffs', 'playoff',
                   'championship', 'championships'}
    return leagues[words[0]] if all(word in topic_words or word.isdigit() for word in words[1:]) else ''


def _is_organization_entity(name: str) -> bool:
    norm = _norm(name)
    if not norm:
        return False
    tokens = norm.split()
    # Brand tokens inside a product/food/person name are not the entity itself.
    # "Tesla Model 3" and "Apple nutrition" must retain their own domains.
    ambiguous_teams = {"cowboys", "ravens", "chiefs", "dolphins", "chargers", "bills", "rams", "broncos",
                       "patriots", "packers", "steelers", "eagles", "lakers", "celtics", "warriors",
                       "bulls", "yankees", "dodgers", "cubs"}
    if norm in _ORGANIZATION_AND_LEAGUE_TERMS - ambiguous_teams or _league_topic_anchor(name):
        return True
    cities = {"dallas", "baltimore", "kansas city", "miami", "los angeles", "buffalo", "denver",
              "new england", "green bay", "pittsburgh", "philadelphia", "boston", "golden state",
              "chicago", "new york", "san francisco", "san diego", "oakland"}
    if any(norm == f"{city} {team}" for city in cities for team in ambiguous_teams):
        return True
    if any(token in _ORGANIZATION_SUFFIX_WORDS for token in tokens):
        return True
    return False


def _organization_domain(name: str) -> str:
    norm = _norm(name)
    sports = {"nfl", "nba", "mlb", "nhl", "fifa", "uefa", "ioc", "ncaa", "f1", "formula 1",
              "nascar", "mls", "pga", "ufc", "wwe", "premier league", "champions league", "super bowl", "world cup"}
    if _league_topic_anchor(name) or norm.removeprefix('the ') in sports or re.search(r'\b(?:football|basketball|baseball|hockey|soccer)\b', norm):
        return 'sports'
    if _is_organization_entity(name) and norm.split()[-1:] and norm.split()[-1] in {
            'cowboys', 'ravens', 'chiefs', 'dolphins', 'chargers', 'bills', 'rams', 'broncos', 'patriots',
            'packers', 'steelers', 'eagles', 'lakers', 'celtics', 'warriors', 'bulls', 'yankees', 'dodgers', 'cubs'}:
        return 'sports'
    return 'business'


def _is_rail_vehicle_subject(subject: str) -> bool:
    normalized = _norm(subject)
    if any(_contains_phrase(normalized, role) for role in (*_RAIL_CREW_ROLE_TERMS, 'train crew', 'rail crew', 'station staff')):
        return False
    return any(_contains_phrase(normalized, term) for term in ('locomotive', 'locomotives', 'train', 'trains', 'railcar', 'railcars', 'rolling stock'))


def _repair_institutional_requirement(existing: Mapping[str, Any]) -> Dict[str, Any]:
    values = dict(existing)
    canonical = str(values.get("canonical_entity") or "").strip()
    if _is_rail_vehicle_subject(canonical) and values.get('subject_type') in {'automobile', 'named_entity', 'concept'}:
        # Engine, cab and transmission vocabulary is shared with road vehicles.
        # Repair cached train contracts before their automotive gate rejects footage.
        values.update(subject_type='named_entity', domain='rail_transport', requirement_hash='')
        values['required_terms'] = tuple(term for term in values.get('required_terms') or ()
                                         if _norm(term) not in {'automobile', 'car', 'vehicle'})
        values['aliases'] = tuple(alias for alias in values.get('aliases') or ()
                                 if _norm(alias) not in {_norm(canonical + ' car'), _norm(canonical + ' automobile')})
    if values.get("subject_type") == "person" and (_is_royal_institution(canonical) or _is_organization_entity(canonical)):
        values.update(subject_type="organization", domain=_organization_domain(canonical), requirement_hash="")
    if values.get('subject_type') == 'organization' and _organization_domain(canonical) == 'sports' and values.get('domain') != 'sports':
        values.update(domain='sports', requirement_hash='')
    if _is_royal_institution(canonical) and "warrant" in _norm(canonical):
        aliases = _unique(list(values.get("aliases") or ()) + ["Royal Warrant", "Royal Warrants"])
        if list(values.get("aliases") or ()) != aliases:
            values.update(aliases=tuple(aliases), requirement_hash="")
    return values


def _subject_type(
    text: str,
    subject: str,
    *,
    title: str = "",
    scene_text: str = "",
    semantic_context: Mapping[str, Any] | None = None,
) -> str:
    """Infer a domain without letting an incidental full-script word win."""
    semantic_context = semantic_context if isinstance(semantic_context, Mapping) else {}
    if _is_generic_process(subject):
        return 'concept'
    if _is_royal_institution(subject) or _is_organization_entity(subject):
        return "organization"
    if _is_rail_vehicle_subject(subject):
        return 'named_entity'
    # Product histories can mention brands such as King & Barnes. Incidental
    # royal words or a stale global "person" hint cannot turn a product into
    # a human identity and prohibit legitimate brewery/production B-roll.
    if re.search(r'\b(?:beers?|brewer(?:y|ies)|brewing|wines?|whisk(?:e)?ys?|soft drinks?|food brands?|consumer products?)\b', _norm(title)):
        return 'product'
    event_subject = re.search(r"\b(?:ceremony|procession|coronation|investiture|garter day|trooping (?:the )?colour)\b", _norm(subject))
    vehicle_subject = any(_contains_phrase(_norm(subject), term) for term in (_AUTO_BRAND_TERMS | _AUTO_TERMS))
    venue_subject = re.search(r'\b(?:chapel|church|cathedral|castle|palace)$', _norm(subject))
    if (event_subject and not vehicle_subject) or venue_subject:
        # Incidental transport in an event film does not turn the event's
        # identity into a car model or require automobile proof for every shot.
        return "named_entity"
    if re.search(r"\b(?:was|were|company|business)\s+(?:incorporated|founded|established)\b", _norm(scene_text)):
        # A company's founding is an organizational beat, even in a car film.
        # Requiring an automobile here made pre-product years unresolvable.
        return "organization"
    if re.search(r"\b(?:stationary batter(?:y|ies)|battery storage|energy storage|batter(?:y|ies) (?:store|stores|storing) electricity|storage installations?|power grid|electricity for homes|manufacturing plants?|factory assembly lines?|(?:supercharger|charging) stations?)\b", _norm(scene_text)):
        # A manufacturer's non-vehicle systems are not automobiles. Keeping
        # the film's car domain here wrongly rejected real storage/factory
        # footage while accepting car exteriors for the same sentences.
        return "organization"
    # Some upstream contracts expose a generic container type (``product``)
    # alongside a more useful domain category (``automotive``).  Do not let
    # the generic field mask the authoritative domain hint.
    for hint_value in (
        semantic_context.get("subject_type"),
        semantic_context.get("container_type"),
        semantic_context.get("domain"),
        semantic_context.get("domain_category"),
    ):
        hinted = _norm(hint_value)
        hinted_type = _SUBJECT_TYPE_ALIASES.get(hinted, hinted)
        if hinted_type in {"automobile", "animal", "ship", "person", "rail_crew", "video_game"}:
            if hinted_type == "person" and _is_organization_entity(subject):
                return "organization"
            return hinted_type

    subject_normalized = _norm(subject)
    title_text = _norm(title or subject)
    scene_normalized = _norm(scene_text)
    script_normalized = _norm(text)

    def count_terms(value: str, terms: Iterable[str]) -> int:
        return sum(1 for term in terms if _contains_phrase(value, term))

    def model_evidence(value: str) -> int:
        typed_model = bool(re.search(
            r"\b[a-z0-9]+[- ](?:type|series|class)(?:\s+(?:r|s|gt|rs|ii|iii|iv))?\b",
            value,
        ))
        # Manufacturer + chassis/generation identifiers (AE101, E46, W124,
        # R34, etc.) are strong automotive evidence even when prose omits the
        # generic word "car".  Requiring a make or an automotive term avoids
        # treating arbitrary gadget model numbers as vehicles.
        coded_model = bool(re.search(r"\b[a-z]{1,4}[- ]?\d{2,4}[a-z]?\b", value))
        vehicle_context = any(_contains_phrase(value, term) for term in (_AUTO_TERMS | _AUTO_BRAND_TERMS))
        return 2 if typed_model or (coded_model and vehicle_context) else 0

    # Title/container evidence is stronger than scene evidence, which is
    # stronger than incidental words elsewhere in the script.
    auto_score = (
        count_terms(subject_normalized, _AUTO_TERMS) * 8
        + count_terms(subject_normalized, _AUTO_BRAND_TERMS) * 8
        + count_terms(title_text, _AUTO_TERMS) * 6
        + count_terms(title_text, _AUTO_BRAND_TERMS) * 6
        + count_terms(scene_normalized, _AUTO_TERMS) * 6
        + count_terms(scene_normalized, _AUTO_BRAND_TERMS) * 4
        + count_terms(script_normalized, _AUTO_TERMS)
        + count_terms(script_normalized, _AUTO_BRAND_TERMS)
        + model_evidence(subject_normalized) * 8
        + model_evidence(title_text) * 6
        + model_evidence(scene_normalized) * 4
    )
    gaming_signals = {
        "video game", "video games", "gaming", "gameplay", "rockstar", "gta",
        "playstation", "xbox", "nintendo", "steam game", "steam store",
        "valve steam", "steam deck", "on steam", "pc game", "esports",
        "grand theft auto",
    }
    gaming_score = (
        count_terms(subject_normalized, gaming_signals) * 8
        + count_terms(title_text, gaming_signals) * 6
        + count_terms(scene_normalized, gaming_signals) * 6
        + count_terms(script_normalized, gaming_signals) * 2
    )
    # A railway, train, or locomotive documentary must never be misclassified as a
    # video game merely because words like steam appear in the narration.
    if any(term in title_text or term in subject_normalized for term in ("locomotive", "train", "railway", "railroad")):
        gaming_score = 0
    if gaming_score:
        auto_score = 0
    animal_score = (
        count_terms(subject_normalized, _ANIMAL_TERMS) * 8
        + count_terms(title_text, _ANIMAL_TERMS) * 6
        + count_terms(scene_normalized, _ANIMAL_TERMS) * 6
        + count_terms(script_normalized, _ANIMAL_TERMS)
    )
    ship_score = (
        count_terms(subject_normalized, _SHIP_TERMS) * 8
        + count_terms(title_text, _SHIP_TERMS) * 6
        + count_terms(scene_normalized, _SHIP_TERMS) * 6
        + count_terms(script_normalized, _SHIP_TERMS)
    )
    rail_score = (
        count_terms(subject_normalized, _RAIL_CREW_TERMS) * 8
        + count_terms(title_text, _RAIL_CREW_TERMS) * 6
        + count_terms(scene_normalized, _RAIL_CREW_TERMS) * 6
        + count_terms(script_normalized, _RAIL_CREW_TERMS)
    )
    # A role/domain identity is often expressed by a role title in source
    # metadata. Do not require every publisher to repeat the exact phrase
    # ``train crew`` when ``conductor`` or ``locomotive engineer`` is the
    # stronger asset evidence.
    if _contains_phrase(subject_normalized, "train crew") or _contains_phrase(subject_normalized, "rail crew"):
        rail_score += 12
    if any(_contains_phrase(title_text, role) for role in _RAIL_CREW_ROLE_TERMS):
        rail_score += 4
    has_rail_role = any(
        _contains_phrase(value, role)
        for value in (subject_normalized, title_text, scene_normalized)
        for role in _RAIL_CREW_ROLE_TERMS
    ) or any(
        _contains_phrase(value, role)
        for value in (subject_normalized, title_text, scene_normalized)
        for role in ("train crew", "rail crew", "station staff")
    )
    if rail_score and not has_rail_role:
        # A mention of a rail route or a train in a broader historical story
        # does not make the documentary about railway staff.
        rail_score = 0
    person_score = (
        count_terms(subject_normalized, _PERSON_TERMS) * 8
        + count_terms(subject_normalized, _PERSON_ROLE_TERMS) * 8
        + count_terms(title_text, _PERSON_TERMS) * 6
        + count_terms(title_text, _PERSON_ROLE_TERMS) * 6
        + count_terms(scene_normalized, _PERSON_TERMS) * 6
        + count_terms(scene_normalized, _PERSON_ROLE_TERMS) * 6
    )
    if len(_tokens(subject)) >= 2 and re.search(r'\b(?:biography|biographical|life of)\b', title_text):
        person_score += 8

    subject_pattern = re.escape(subject_normalized).replace(r"\ ", r"\s+")
    if subject_pattern:
        if re.search(
            rf"\b{subject_pattern}\s+(?:is|are|means)\s+(?:an?\s+)?(?:animal|species|wildlife|predator)\b",
            script_normalized,
        ):
            animal_score += 12
        if re.search(
            rf"\b{subject_pattern}\s+(?:is|are|means)\s+(?:an?\s+)?(?:car|automobile|vehicle|sports\s+car)\b",
            script_normalized,
        ):
            auto_score += 12

    # Documentary scripts frequently explain an animal-name origin while
    # explicitly saying the story is about a vehicle. Do not count that
    # contrast as positive wildlife evidence.
    if auto_score and re.search(
        r"\b(?:not|isn t|is not|rather than|instead of|but|however)\s+(?:the\s+)?(?:animal|wildlife|big cat|jaguar)\b",
        script_normalized,
    ):
        animal_score = max(0, animal_score - 3)
    if animal_score and re.search(
        r"\b(?:not|isn t|is not|rather than|instead of|but|however)\s+(?:the\s+)?(?:car|automobile|vehicle)\b",
        script_normalized,
    ):
        auto_score = max(0, auto_score - 3)

    scores = {
        "automobile": auto_score,
        "animal": animal_score,
        "ship": ship_score,
        "rail_crew": rail_score,
        "person": person_score,
        "video_game": gaming_score,
    }
    best_type, best_score = max(scores.items(), key=lambda item: item[1])
    second_score = sorted(scores.values(), reverse=True)[1]
    # Ambiguous names stay ambiguous. Searching an unknown “Jaguar” as an
    # animal is worse than surfacing an unresolved contract for clarification.
    if best_score <= 0 or best_score == second_score or best_score < 6:
        return "named_entity" if subject and len(_tokens(subject)) >= 2 else "concept"
    return best_type


def _is_generic_category_phrase(subject: str) -> bool:
    words = [word for word in _tokens(subject) if word not in _STOPWORDS]
    meaningful = [word for word in words if word not in _HISTORICAL_MARKERS]
    return bool(words) and all(word in _GENERIC_SUBJECTS for word in meaningful)


def beverage_category_aliases(subject: str) -> tuple[str, ...]:
    # A collection's beer category is not an exact brand/product name.
    # Keep named brands strict; allow topic footage such as a British pub or
    # brewery to reach the pixel/era gate for broad establishing scenes.
    if re.fullmatch(r'(?:(?:british|english|historic|historical|old|vintage)\s+)*beers?(?:\s+from\s+the\s+past)?', _norm(subject)):
        return ('British beer', 'English beer', 'beer', 'ale', 'brewery', 'brewing', 'pub')
    return ()


def _canonical_entity(subject: str, subject_type: str) -> str:
    """Remove a trailing category label while preserving the named identity.

    Provider titles commonly say ``<make> cars`` or ``<model> automobile``.
    The category word is useful for type classification, but it should not
    become part of the exact identity proof.  This is intentionally generic;
    it does not contain a catalog of brands or special cases.
    """
    value = _text(subject)
    if subject_type != 'person' and _league_topic_anchor(value):
        return _league_topic_anchor(value)
    if subject_type == 'person':
        # Search-plan prose must not become part of a person's exact identity.
        suffix = re.search(r'\s+(?:FBI|CIA|police)\s+(?:investigation|case|report)\b|\s+(?:investigation into|documentary about|biography of)\b', value, re.I)
        if suffix and len(value[:suffix.start()].split()) >= 2:
            value = value[:suffix.start()].strip()
    if subject_type == "video_game":
        game = re.search(r"\b(?:gta|grand\s+theft\s+auto)\s*(vi|v|6|5)\b", value, re.I)
        if game:
            return "Grand Theft Auto " + ("VI" if game.group(1).lower() in {"vi", "6"} else "V")
    if subject_type != "automobile":
        return value
    value = re.sub(r'^(?:the|a|an)\s+', '', value, flags=re.I)
    words = value.split()
    category_words = {"car", "cars", "automobile", "automobiles", "vehicle", "vehicles"}
    # Category modifiers are part of the searchable concept rather than a
    # make/model suffix. Dropping ``cars`` from ``Electric Cars`` leaves the
    # ambiguous one-word subject ``Electric`` and sends image providers into
    # generic electrical imagery. Preserve these common technology/category
    # phrases while continuing to canonicalize named makes such as ``Tesla
    # Cars`` to ``Tesla``.
    category_modifiers = {
        "electric", "hybrid", "autonomous", "self-driving", "self driving",
        "fuel cell", "solar", "luxury", "sports", "family", "commercial",
    }
    if len(words) >= 2:
        prefix = " ".join(words[:-1]).strip().casefold()
        if _norm(words[-1]) in category_words and prefix in category_modifiers:
            return value
    while len(words) > 1 and _norm(words[-1]) in category_words:
        words.pop()
    candidate = " ".join(words).strip()
    if candidate and not _is_generic_category_phrase(candidate):
        return candidate
    return value


def _attributes(text: str, subject: str, subject_type: str) -> Dict[str, str]:
    positive_text = re.sub(r"\b(?:not|never)\s+(?:a|an|the)\s+[^.;:!?]+?(?=[.;:!?]|\s+but\b|$)", "", str(text), flags=re.I)
    normalized = _norm(positive_text)
    # A negated category is not the requested subject: "not a family sedan"
    # must not constrain retrieval to sedans. Keep the original narration for
    # the vision model; remove only locally negated noun phrases here.
    attributes: Dict[str, str] = {}
    years = re.findall(r"\b(?:18|19|20)\d{2}\b", normalized)
    if years:
        attributes["year"] = years[0]
    decade = re.search(r"\b(?:early|mid|late)?\s*((?:18|19|20)\d0s)\b", normalized)
    if decade:
        attributes["decade"] = decade.group(1)
    historical = _historical_evidence(normalized, subject, subject_type)
    if historical:
        attributes["era"] = "classic_historical" if subject_type == "automobile" else "historical"
    if subject_type == "automobile":
        if "sports car" in normalized:
            attributes["vehicle_class"] = "sports car"
        elif "sedan" in normalized:
            attributes["vehicle_class"] = "sedan"
    # Preserve a model token such as E-Type without inventing a catalog.
    model_match = re.search(
        r"\b(model\s+[a-z0-9]+|[a-z0-9]+[- ](?:type|series|class)(?:\s+(?:r|s|gt|rs|ii|iii|iv))?)\b",
        normalized,
    )
    if model_match:
        attributes["model"] = model_match.group(1).strip()
    if subject_type == "automobile" and subject:
        subject_norm = _norm(subject)
        if not _is_generic_category_phrase(subject) and subject_norm not in _GENERIC_SUBJECTS and _tokens(subject):
            attributes.setdefault("model_or_make", subject_norm)
    return attributes


def _forbidden(subject: str, subject_type: str, attributes: Mapping[str, Any]) -> Tuple[str, ...]:
    values: list[str] = []
    subject_norm = _norm(subject)
    if "prince harry" in subject_norm:
        values.extend(["harry potter", "potter", "half-blood prince", "half blood prince", "hogwarts", "voldemort", "quidditch", "dumbledore", "gryffindor", "slytherin", "order of the phoenix"])
        # A royal-person contract must never silently accept an animal/toy
        # result whose card happens to contain the word ``prince``.
        values.extend(_PERSON_CONFUSABLE_TERMS)
    if subject_type == "automobile":
        values.extend(["animal", "big cat", "cheetah", "leopard", "lion", "tiger", "wildlife"])
        if attributes.get("era") == "classic_historical":
            values.extend(_MODERN_MARKERS)
    elif subject_type == "animal":
        values.extend(["automobile", "car review", "vehicle", "driving footage"])
    elif subject_type == "rail_crew":
        values.extend(["wildlife", "animal", "zoo", "safari", "car review"])
    elif subject_type == "person":
        values.extend(["gameplay", "walkthrough", "playthrough", "xbox 360", "speedrun", "b-roll: harry potter"])
    return _unique(values)


@dataclass(frozen=True)
class VisualRequirementV1:
    schema_version: str = "visual_requirement.v2"
    canonical_entity: str = ""
    subject_type: str = "concept"
    domain: str = ""
    ambiguity_status: str = "resolved"
    domain_evidence: Tuple[str, ...] = field(default_factory=tuple)
    confusable_entities: Tuple[str, ...] = field(default_factory=tuple)
    aliases: Tuple[str, ...] = field(default_factory=tuple)
    required_attributes: Mapping[str, str] = field(default_factory=dict)
    required_terms: Tuple[str, ...] = field(default_factory=tuple)
    positive_evidence: Tuple[str, ...] = field(default_factory=tuple)
    forbidden_subjects: Tuple[str, ...] = field(default_factory=tuple)
    visual_intent: str = "documentary_context"
    action_terms: Tuple[str, ...] = field(default_factory=tuple)
    strict_identity: bool = True
    source_preference: str = "verified_external_or_graphic"
    fallback_template: str = "kinetic_statement"
    confidence: float = 0.0
    requirement_hash: str = ""
    subject_context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        canonical = _canonical_entity(self.canonical_entity, self.subject_type)
        if self.subject_type == "video_game" and canonical in {"Grand Theft Auto VI", "Grand Theft Auto V"}:
            number = "6" if canonical.endswith("VI") else "5"
            roman = "VI" if number == "6" else "V"
            object.__setattr__(self, "canonical_entity", canonical)
            object.__setattr__(self, "aliases", tuple(self.aliases) + (f"GTA {number}", f"GTA {roman}", f"GTA{number}", f"Grand Theft Auto {number}"))
            object.__setattr__(self, "requirement_hash", "")
        object.__setattr__(self, "aliases", _unique(self.aliases))
        object.__setattr__(self, "domain_evidence", _unique(self.domain_evidence))
        object.__setattr__(self, "confusable_entities", _unique(self.confusable_entities))
        object.__setattr__(self, "required_terms", _unique(self.required_terms))
        object.__setattr__(self, "positive_evidence", _unique(self.positive_evidence))
        object.__setattr__(self, "forbidden_subjects", _unique(self.forbidden_subjects))
        object.__setattr__(self, "action_terms", _unique(self.action_terms))
        object.__setattr__(self, "required_attributes", {
            str(key): _text(value) for key, value in dict(self.required_attributes or {}).items() if _text(value)
        })
        if not self.requirement_hash:
            object.__setattr__(self, "requirement_hash", _stable_hash(self.hash_payload()))

    def hash_payload(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "canonical_entity": self.canonical_entity,
            "subject_type": self.subject_type,
            "domain": self.domain,
            "ambiguity_status": self.ambiguity_status,
            "domain_evidence": list(self.domain_evidence),
            "confusable_entities": list(self.confusable_entities),
            "aliases": list(self.aliases),
            "required_attributes": dict(self.required_attributes),
            "required_terms": sorted({_norm(term) for term in self.required_terms}),
            "positive_evidence": list(self.positive_evidence),
            "forbidden_subjects": list(self.forbidden_subjects),
            "visual_intent": self.visual_intent,
            "action_terms": list(self.action_terms),
            "strict_identity": self.strict_identity,
            "fallback_template": self.fallback_template,
            "subject_context": dict(self.subject_context),
        }

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "VisualRequirementV1":
        values = dict(raw or {})
        return cls(
            schema_version=str(values.get("schema_version") or "visual_requirement.v2"),
            canonical_entity=_text(values.get("canonical_entity") or values.get("entity") or values.get("subject")),
            subject_type=_text(values.get("subject_type") or "concept"),
            domain=_text(values.get("domain") or ""),
            ambiguity_status=_text(values.get("ambiguity_status") or "resolved"),
            domain_evidence=tuple(values.get("domain_evidence") or ()),
            confusable_entities=tuple(values.get("confusable_entities") or ()),
            aliases=tuple(values.get("aliases") or ()),
            required_attributes=dict(values.get("required_attributes") or {}),
            required_terms=tuple(values.get("required_terms") or ()),
            positive_evidence=tuple(values.get("positive_evidence") or ()),
            forbidden_subjects=tuple(values.get("forbidden_subjects") or ()),
            visual_intent=_text(values.get("visual_intent") or "documentary_context"),
            action_terms=tuple(values.get("action_terms") or ()),
            strict_identity=bool(values.get("strict_identity", True)),
            source_preference=_text(values.get("source_preference") or "verified_external_or_graphic"),
            fallback_template=_text(values.get("fallback_template") or "kinetic_statement"),
            confidence=float(values.get("confidence") or 0.0),
            requirement_hash=_text(values.get("requirement_hash")),
            subject_context=dict(values.get('subject_context') or {}),
        )


_DATED_EVENT_TERMS = re.compile(
    r"\b(?:bowl|championship|cup final|world cup|playoff|final|battle|war|siege|"
    r"disaster|shipwreck|sinking|collision|crash|assassination|election|launch|"
    r"expedition|historic event|historical event|the event)\b"
)


def _single_film_event_year(title: str, script: str, semantic_context: Mapping[str, Any] | None = None) -> str:
    context = semantic_context if isinstance(semantic_context, Mapping) else {}
    explicit = str(context.get("historical_event_year") or context.get("required_event_year") or "").strip()
    if re.fullmatch(r"(?:17|18|19|20)\d{2}", explicit):
        return explicit
    years = set(re.findall(r"\b(?:17|18|19|20)\d{2}\b", _text(script)))
    event_context = " ".join((_text(title), _text(script))).casefold()
    return next(iter(years)) if len(years) == 1 and _DATED_EVENT_TERMS.search(event_context) else ""


def build_visual_requirement(
    *,
    title: str = "",
    script: str = "",
    scene_text: str = "",
    positive_subjects: Sequence[str] = (),
    forbidden_subjects: Sequence[str] = (),
    visual_intent: str = "documentary_context",
    semantic_context: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    semantic_context = semantic_context if isinstance(semantic_context, Mapping) else {}
    full_text = " ".join(part for part in (_text(title), _text(script), _text(scene_text)) if part)
    title_subject = _title_subject(title)
    scene_subject = _scene_subject(scene_text)
    positive_subject = next(
        (item for item in positive_subjects if _text(item) and _norm(item) not in _GENERIC_SUBJECTS),
        "",
    )
    # Scene-local named evidence outranks a numeric/timeline container label
    # and a listicle shell.  A meaningful editorial title still owns ordinary
    # single-topic narratives, so only replace it when the title is empty or
    # clearly a container/category label.
    title_tokens = set(_tokens(title_subject))
    title_is_broad_category = bool(
        title_tokens
        and len(title_tokens) <= 5
        and any(token in _TITLE_CONTAINER_WORDS for token in title_tokens)
        and len(title_tokens & _TITLE_CONTAINER_WORDS) >= max(1, len(title_tokens) - 1)
    )
    from .documentary_subject import collection_title_subject
    title_is_container = bool(
        collection_title_subject(title) or
        re.match(
            r"^(?:(?:top|best|greatest|worst|most)\s+)?(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
            _norm(title_subject),
        )
        or re.fullmatch(r"(?:\d{3,4}|\d{1,2}(?:st|nd|rd|th)?)", _norm(title_subject or title))
        or title_is_broad_category
        or not title_subject
    )
    role_subject = _scene_role_subject(scene_text)
    # Intro/transition beats often contain only a pronoun (``Harry and
    # Meghan are back``) or an abstract line about the family.  When the
    # title is a broad container, carry the script's first explicit
    # role/name anchor into those beats so they still receive a concrete
    # searchable identity instead of a generic family portrait query.
    if not role_subject and title_is_container:
        role_subject = _scene_role_subject(script)
    # Explicit role/name evidence is stronger than a broad programme title.
    # This is what turns the second beat of a Royal Family script into
    # ``Prince Harry portrait`` instead of searching for the whole family.
    action_subject = _scene_action_subject(scene_text)
    subject = action_subject or (
        role_subject if role_subject and title_is_container
        else scene_subject if scene_subject and title_is_container
        else title_subject
        or scene_subject
        or positive_subject
        or _script_subject(script)
    )
    if not subject:
        subject = next((item for item in positive_subjects if _text(item)), "")
    subject_context = scene_subject_contract(title, scene_text, semantic_context)
    if subject_context.get('scene_local'):
        subject = subject_context['scene_subject']
        # A local actor's type is independent of a global container hint.
        semantic_context = {key: value for key, value in semantic_context.items()
                            if key not in {'subject_type', 'container_type', 'domain', 'domain_category'}}
    subject_type = _subject_type(
        scene_text if subject_context.get('scene_local') else full_text,
        subject,
        title=subject if subject_context.get('scene_local') else title,
        scene_text=scene_text,
        semantic_context=semantic_context,
    )
    if subject_context.get('scene_local') and subject_context.get('subject_type'):
        subject_type = subject_context['subject_type']
    canonical = _canonical_entity(subject, subject_type) or ("automobile" if subject_type == "automobile" else "")
    if subject_type == 'automobile':
        # Titles contain editorial hooks. Prefer an explicit narrated model
        # over an appended phrase such as "on the Road" or "practical design".
        for candidate in (_scene_subject(scene_text), _script_subject(script)):
            anchor = _canonical_entity(candidate, subject_type)
            if anchor and _norm(canonical).startswith(_norm(anchor) + ' '):
                suffix = _norm(canonical)[len(_norm(anchor)):].strip()
                if re.match(r'^(?:on|in|at|for|road|everyday|reliability|practical|engineering|driving|history|story|explained)\b', suffix):
                    canonical = anchor
                    break
    # The full script is valid evidence for the global subject and domain,
    # but it is not scene-local evidence for a year, model, era, or vehicle
    # class. Using the first year in the full narration used to stamp (for
    # example) 1966 onto every Corolla scene and reject valid later footage.
    scene_attribute_text = (_text(scene_text) if subject_context.get('scene_local') else
                            " ".join(part for part in (_text(title), _text(scene_text)) if part))
    attrs = _attributes(scene_attribute_text, canonical, subject_type)
    subject_only = bool(subject_context.get('scene_local') and subject_context.get('identity_scope') != 'event')
    if subject_only:
        # An invention's date is not the date on which a supporting portrait
        # must have been photographed. Keep it as narration evidence.
        if attrs.get('year'):
            subject_context['narrated_year'] = attrs.pop('year')
        if attrs.get('decade'):
            subject_context['narrated_decade'] = attrs.pop('decade')
    # Carry one unambiguous event year through a single-event documentary.
    # Local-only extraction left cold opens and aftermath beats unconstrained,
    # allowing modern same-team footage into films about named archival events.
    if not attrs.get("year") and subject_type != "video_game" and not subject_only:
        inherited_year = _single_film_event_year(title, script, semantic_context)
        if inherited_year:
            attrs["year"] = inherited_year
    domain = {
        "automobile": "automotive",
        "animal": "wildlife",
        "ship": "maritime",
        "rail_crew": "rail_transport",
        "person": "person",
        "organization": _organization_domain(canonical),
        "product": "consumer_products",
    }.get(subject_type, _text(semantic_context.get("domain_category") or "documentary"))
    if subject_type == 'named_entity' and _is_rail_vehicle_subject(canonical):
        domain = 'rail_transport'
    domain_evidence = _unique(
        list(semantic_context.get("retrieval_positive_terms") or ())
        + [title, scene_text]
    )
    ambiguity_status = "resolved"
    if subject_type in {"concept", "named_entity"}:
        ambiguity_status = "ambiguous" if subject else "unresolved"
    confusable_entities = list(semantic_context.get("confusable_entities") or ())
    if subject_type == "automobile":
        confusable_entities.extend(("animal", "wildlife", "big cat"))
    elif subject_type == "animal":
        confusable_entities.extend(("automobile", "car", "vehicle"))
    aliases: list[str] = []
    aliases.extend(beverage_category_aliases(canonical))
    if _is_royal_institution(canonical) and "warrant" in _norm(canonical):
        aliases.extend(("Royal Warrant", "Royal Warrants"))
    if re.search(r'\bgarter day\b', _norm(canonical)):
        aliases.extend(('Garter Day', 'Order of the Garter', 'Garter ceremony', 'Garter service'))
    if canonical and subject_type == "person":
        aliases.extend(_KNOWN_PERSON_ALIASES.get(_norm(canonical).casefold(), ()))
    if subject_type == "video_game" and canonical in {"Grand Theft Auto VI", "Grand Theft Auto V"}:
        number = "6" if canonical.endswith("VI") else "5"
        roman = "VI" if number == "6" else "V"
        aliases.extend((f"GTA {number}", f"GTA {roman}", f"GTA{number}", f"Grand Theft Auto {number}"))
    if canonical and subject_type == "automobile":
        aliases.extend([f"{canonical} car", f"{canonical} automobile"])
    elif canonical and subject_type == "rail_crew":
        aliases.extend(
            (
                "railway crew",
                "railroad crew",
                "train conductor",
                "locomotive engineer",
                "train driver",
                "railway worker",
                "railroad worker",
                "dispatcher",
            )
        )
    required_terms = [canonical]
    if subject_type == "automobile":
        required_terms.append("automobile")
    elif subject_type == "animal":
        required_terms.append("animal")
    elif subject_type == "rail_crew":
        required_terms.extend(("railway crew", "conductor", "locomotive engineer"))
    required_terms.extend(value for key, value in attrs.items() if key in {"year", "decade", "model", "model_or_make", "vehicle_class"})
    actions = [
        token for token in _unique(_tokens(scene_text))
        if token not in _STOPWORDS and token not in _GENERIC_SUBJECTS and len(token) >= 4
    ][:6]
    strict = bool(canonical or subject_type in {"automobile", "animal", "ship", "rail_crew", "person"})
    if subject_type == 'concept' and _is_generic_process(canonical):
        strict = False
    if attrs.get("year") or attrs.get("model") or attrs.get("era"):
        strict = True
    fallback_template = "kinetic_statement"
    if attrs.get("year") or attrs.get("decade"):
        fallback_template = "date_card"
    elif subject_type == "ship":
        fallback_template = "timeline"
    elif subject_type == "automobile":
        fallback_template = "blueprint"
    requirement = VisualRequirementV1(
        canonical_entity=canonical,
        subject_type=subject_type,
        domain=domain,
        ambiguity_status=ambiguity_status,
        domain_evidence=domain_evidence,
        confusable_entities=tuple(confusable_entities),
        aliases=tuple(aliases),
        required_attributes=attrs,
        required_terms=tuple(required_terms),
        positive_evidence=tuple(value for value in (_text(title), _text(scene_text)) if value),
        forbidden_subjects=tuple(forbidden_subjects) + _forbidden(canonical, subject_type, attrs),
        visual_intent=_text(visual_intent) or "documentary_context",
        action_terms=tuple(actions),
        strict_identity=strict,
        fallback_template=fallback_template,
        confidence=0.85 if subject else 0.45,
        subject_context=subject_context,
    )
    return requirement.to_dict()


def rebase_visual_requirement_for_scene(
    existing: Mapping[str, Any],
    *,
    title: str = "",
    scene_text: str = "",
    script: str = "",
    semantic_context: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Refresh scene-local attributes on a cached semantic requirement.

    Shared planning caches can contain a contract created before the
    scene-local attribute rule was applied. Preserve its canonical identity,
    repair institutional nouns incorrectly typed as people, and recompute
    year/model/era/class from this scene and the title only. This prevents a script's first historical year from
    poisoning every later scene.
    """
    requirement = VisualRequirementV1.from_mapping(_repair_institutional_requirement(existing))
    local = scene_subject_contract(title, scene_text, semantic_context)
    if local.get('scene_local'):
        return build_visual_requirement(title=title, script=script, scene_text=scene_text,
            visual_intent=requirement.visual_intent, forbidden_subjects=requirement.forbidden_subjects,
            semantic_context=semantic_context)
    from .documentary_subject import is_source_attribution_subject
    if is_source_attribution_subject(requirement.canonical_entity, scene_text):
        # Old checkpoints can bind the cited article/publisher as an exact
        # product or game identity. Rebuild before search, which also invalidates
        # reviews tied to that obsolete requirement hash.
        return build_visual_requirement(title=title, script=script, scene_text=scene_text,
            visual_intent=requirement.visual_intent)
    action_subject = _scene_action_subject(scene_text)
    if action_subject and _norm(action_subject) != _norm(requirement.canonical_entity):
        # A changed identity invalidates old pixel verdicts by generating a
        # new requirement hash. Never relabel the prior owner's review.
        return build_visual_requirement(title=title, script=script, scene_text=scene_text,
            visual_intent=requirement.visual_intent)
    place = re.search(r"\b(?:St\.?|Saint)\s+[A-Z][\w’-]*(?:['’]s)?\s+(?:Chapel|Church|Cathedral)\b", _text(scene_text))
    if place and _norm(requirement.canonical_entity) == _norm(re.sub(r"(?:['’]s)?\s+(?:Chapel|Church|Cathedral)$", '', place.group(0))):
        # Repair old cached saint-name truncation before source discovery.
        values = requirement.to_dict()
        values.update(canonical_entity=place.group(0), subject_type='named_entity',
            required_terms=(place.group(0),), aliases=(), requirement_hash='',
            positive_evidence=(_text(title), _text(scene_text)))
        return VisualRequirementV1.from_mapping(values).to_dict()
    values = requirement.to_dict()
    old_attributes = dict(requirement.required_attributes or {})
    values['aliases'] = tuple(_unique(list(requirement.aliases) + list(beverage_category_aliases(requirement.canonical_entity))))
    scene_text_value = " ".join(part for part in (_text(title), _text(scene_text)) if part)
    local_attributes = _attributes(
        scene_text_value,
        requirement.canonical_entity,
        requirement.subject_type,
    )
    if not local_attributes.get("year") and requirement.subject_type != "video_game":
        inherited_year = _single_film_event_year(title, script)
        if inherited_year:
            local_attributes["year"] = inherited_year
    stale_attribute_values = {
        _norm(value) for value in old_attributes.values() if _text(value)
    }
    retained_terms = [
        str(term) for term in requirement.required_terms
        if _norm(term) not in stale_attribute_values
    ]
    values["required_attributes"] = local_attributes
    values["required_terms"] = tuple(_unique(retained_terms + list(local_attributes.values())))
    values["positive_evidence"] = tuple(
        part for part in (_text(title), _text(scene_text)) if part
    )
    values["domain_evidence"] = tuple(_unique(
        list(requirement.domain_evidence) + [title, scene_text]
    ))
    values["requirement_hash"] = ""
    return VisualRequirementV1.from_mapping(values).to_dict()


def requirement_from_scene(scene: Mapping[str, Any], *, title: str = "", script: str = "") -> Dict[str, Any]:
    existing = scene.get("visual_requirement") if isinstance(scene, Mapping) else None
    if isinstance(existing, Mapping) and (existing.get("canonical_entity") or existing.get("subject_type")):
        return VisualRequirementV1.from_mapping(_repair_institutional_requirement(existing)).to_dict()
    return build_visual_requirement(
        title=title or str(scene.get("content_title") or ""),
        script=script or str(scene.get("source_script") or ""),
        scene_text=str(scene.get("text") or scene.get("scene_text") or ""),
        positive_subjects=tuple(scene.get("positive_subjects") or ()),
        forbidden_subjects=tuple(scene.get("forbidden_subjects") or ()),
        visual_intent=str(scene.get("visual_intent") or "documentary_context"),
        semantic_context=scene,
    )


def provider_query_variants(requirement: Mapping[str, Any], *, provider: str) -> list[str]:
    req = VisualRequirementV1.from_mapping(requirement)
    identity = req.canonical_entity or "documentary subject"
    attrs = req.required_attributes

    def domain_query_qualifier() -> str:
        """Return a compact domain anchor without replacing the full identity.

        Global titles frequently contain short/ambiguous names (Apple, Harry,
        Java, etc.). Keeping the canonical entity first and adding a domain
        anchor prevents provider search from drifting into a similarly named
        but unrelated topic.
        """
        domain = _norm(req.domain)
        mapping = (
            ("food", "food cuisine"),
            ("health", "health medicine"),
            ("technology", "technology"),
            ("science", "science research"),
            ("politic", "political"),
            ("education", "education"),
            ("music", "music"),
            ("entertainment", "entertainment"),
            ("fashion", "fashion"),
            ("environment", "environment"),
            ("culture", "culture heritage"),
            ("religion", "religion"),
            ("law", "legal"),
            ("business", "business"),
            ("economics", "economics finance"),
            ("literature", "literature books"),
            ("travel", "travel"),
            ("documentary", "documentary"),
        )
        for needle, value in mapping:
            if needle in domain:
                return value
        return ""

    domain_qualifier = domain_query_qualifier()
    qualifiers = []
    for key, value in attrs.items():
        if key not in {"model", "year", "decade", "era", "vehicle_class"}:
            continue
        qualifiers.append(str(value).replace("_", " "))
    type_term = {
        "automobile": "automobile",
        "animal": "wildlife",
        "ship": "ship",
        "rail_crew": "railway crew",
        "person": "",
        "video_game": "gameplay",
    }.get(req.subject_type, "")
    historical_suffix = bool(attrs.get("era") or attrs.get("year") or attrs.get("decade"))
    if (req.subject_type == "automobile" or attrs.get("model") or attrs.get("vehicle_class")) and not attrs.get("era") and not attrs.get("decade"):
        try:
            historical_suffix = int(attrs.get("year") or 0) < 1990 and bool(attrs.get("year"))
        except (ValueError, TypeError):
            pass
    suffix = (
        "trailer 4k" if req.subject_type == "video_game" and provider.casefold() in {"youtube", "yt", "video"}
        else "screenshot wallpaper" if req.subject_type == "video_game"
        else "archival footage" if historical_suffix and provider.casefold() in {"youtube", "yt", "video"}
        else "archival photograph" if historical_suffix
        else "documentary footage" if provider.casefold() in {"youtube", "yt", "video"}
        else "documentary photograph"
    )
    if req.subject_type == "automobile" and attrs.get("era") == "classic_historical":
        suffix = "classic automobile historical footage" if provider.casefold() in {"youtube", "yt", "video"} else "classic automobile historical photograph"
    identity_tokens = set(_tokens(identity))
    confusable_terms = _ANIMAL_TERMS if req.subject_type == "automobile" else (
        _AUTO_TERMS if req.subject_type == "animal" else set()
    )
    negative_terms = []
    for forbidden in req.forbidden_subjects:
        for token in _tokens(forbidden):
            if (
                token in confusable_terms
                and token not in identity_tokens
                and token not in {"car", "cars"}
            ):
                negative_terms.append(f"-{token}")
    negative_terms = list(dict.fromkeys(negative_terms))[:8]
    # YouTube ranking is much more reliable when it receives a short positive
    # phrase.  Keep forbidden-subject terms for web image providers (where a
    # small disambiguation operator can help), but enforce YouTube exclusions
    # after download in its metadata/vision gates.  Passing ``-animal
    # -wildlife`` and similar operators through every motion query was causing
    # the exact noisy rows seen in the UI and suppressing valid footage.
    provider_is_youtube = provider.casefold() in {"youtube", "yt", "video"}
    query_negative_terms = [] if provider_is_youtube else negative_terms

    # Provider queries are still subordinate to the contract, but they must
    # carry the scene's concrete visual context.  Previously this function
    # emitted the same identity/type pack for every scene, which made a
    # scraper return a large pool of broadly related assets without any
    # scene-level retrieval signal.  Keep the extraction conservative: use
    # contract action/context terms only, discard prose/abstract filler, and
    # never allow these terms to replace or redefine the canonical identity.
    # Scene text is narration, not a search query.  Pronouns, interrogatives,
    # and editorial glue are especially dangerous here: a phrase such as
    # ``they what makes the work meaningful`` can become a search instruction
    # that displaces the canonical subject.  Keep this list domain-neutral;
    # concrete nouns/actions remain eligible as scene context.
    query_context_stop = {
        "over", "under", "eight", "hundred", "thousand", "million", "billion",
        "one", "two", "three", "four", "five", "six", "seven", "nine", "ten",
        "archival", "archive", "documentary", "footage", "photo", "photograph",
        "image", "images", "reference", "real", "historical", "classic", "modern",
        "scene", "visual", "context", "makes", "meaningful", "meaning", "balance",
        "precision", "service", "life", "work", "journey", "story", "stories",
        "moment", "moments", "part", "thing", "things", "people", "person",
        "subject", "topic", "shown", "showing", "shows", "look", "looks",
        # Narrative/function words are not visual evidence and create noisy
        # provider queries when extracted from complete sentences.
        "a", "an", "and", "are", "as", "at", "be", "been", "being", "but",
        "can", "could", "did", "do", "does", "doing", "for", "from", "has",
        "have", "having", "how", "if", "in", "into", "is", "it", "its", "just",
        "may", "might", "must", "nor", "not", "of", "on", "or", "our", "out",
        "should", "so", "than", "that", "the", "their", "them", "then", "there",
        "these", "they", "this", "those", "through", "to", "too", "under", "until",
        "upon", "was", "were", "what", "when", "where", "which", "who", "why",
        "will", "with", "would", "you", "your", "every", "each", "all", "both",
        "often", "usually", "together", "behind", "around", "built", "depends",
        "depend", "remember", "remembering", "begins", "begin", "began", "becomes",
        "worth", "looking", "closely", "visible", "whole", "idea", "challenge",
        "changed", "changes", "explain", "explains", "practical", "experience",
    }
    identity_tokens = set(_tokens(identity))
    attribute_tokens = {
        token
        for value in attrs.values()
        for token in _tokens(value)
    }
    type_tokens = set(_tokens(type_term))
    context_values = list(req.action_terms)
    if req.visual_intent and req.visual_intent != "documentary_context":
        context_values.append(req.visual_intent)
    if req.positive_evidence:
        # Positive evidence is ordered as title/context by the contract
        # builder; the final item is the most scene-local evidence.
        context_values.append(req.positive_evidence[-1])
    context_terms: list[str] = []
    seen_context: set[str] = set()
    for value in context_values:
        for token in _tokens(value):
            if (
                len(token) < 4
                or token in query_context_stop
                or token in identity_tokens
                or token in attribute_tokens
                or token in type_tokens
                or token in seen_context
            ):
                continue
            seen_context.add(token)
            context_terms.append(token)
            if len(context_terms) >= 3:
                break
        if len(context_terms) >= 3:
            break
    context = context_terms[:2]
    # Always retain a canonical same-entity pool query as a late retrieval
    # wave.  It is intentionally context-light, not a relevance bypass: each
    # returned asset still passes the metadata gate, pixel verification, and
    # the shared scene-context verifier.  This gives providers a chance to
    # return several unique assets when a very specific scene phrase has no
    # indexed results.
    canonical_pool = [identity, *qualifiers, domain_qualifier, type_term, suffix, *query_negative_terms]
    if req.subject_type == "rail_crew":
        canonical_pool = [identity, *qualifiers, "railway crew conductor locomotive engineer", suffix, *query_negative_terms]
    queries = [
        " ".join([part for part in [identity, *qualifiers, domain_qualifier, type_term, *context, suffix, *query_negative_terms] if part]),
        " ".join([part for part in [
            identity,
            *qualifiers,
            domain_qualifier,
            *context,
            (
                "conductor locomotive engineer railway operations"
                if req.subject_type == "rail_crew"
                else "gameplay official"
                if req.subject_type == "video_game"
                else "documentary"
            ),
            "real footage" if provider.casefold() in {"youtube", "yt", "video"} else "reference image",
            *query_negative_terms,
        ] if part]),
        " ".join([part for part in [identity, *qualifiers, domain_qualifier, type_term, *context, *query_negative_terms] if part]),
        " ".join([part for part in canonical_pool if part]),
    ]
    if _is_royal_institution(identity) and "warrant" in _norm(identity):
        # Search the indexed name of the artifact instead of turning the
        # editorial word "System" into part of every query. These are recall
        # candidates only: the scene contract and pixel gate still own acceptance.
        queries = (["Royal Warrant holders footage", "Royal Warrant documentary"]
                   if provider_is_youtube else ["Royal Warrant certificate", "Royal Warrant seal", "Royal Warrant holders"]) + queries
    # A generic ``documentary photograph`` suffix is too weak for people and
    # places: image indexes contain many unrelated documentaries and stock
    # portraits.  Add typed identity waves so the first requests carry the
    # visual noun users actually mean (``Virat Kohli portrait`` / ``Paris
    # landmark photograph``).  These remain retrieval hints; metadata and
    # vision still decide whether the downloaded pixels are acceptable.
    evidence_text = _norm(" ".join((req.domain, req.visual_intent, *req.positive_evidence, *req.domain_evidence)))
    query_kind = req.subject_type
    if query_kind in {"named_entity", "concept"}:
        person_markers = (
            "actor", "actress", "athlete", "footballer", "cricketer", "player",
            "president", "minister", "king", "queen", "prince", "princess",
            "duke", "celebrity", "singer", "musician", "journalist", "born",
            "portrait", "biography", "profile",
        )
        place_markers = (
            "city", "town", "village", "country", "capital", "landmark", "tower",
            "palace", "castle", "bridge", "monument", "skyline", "street", "travel",
            "tourism", "destination", "airport", "beach", "mountain", "river", "lake",
            "island", "park", "museum", "harbor", "harbour", "coast", "location",
            "architecture", "temple", "church",
        )
        object_markers = (
            "product", "device", "phone", "laptop", "computer", "camera", "vehicle",
            "car", "engine", "machine", "robot", "chip", "rocket", "aircraft", "ship",
            "model", "prototype", "software", "app", "equipment", "tool", "gadget",
        )
        food_markers = (
            "food", "fruit", "fruits", "apple", "apples", "vegetable", "vegetables", "produce", "recipe", "ingredient", "nutrition",
            "meal", "cooking", "kitchen", "cuisine", "dish", "beverage", "drink",
        )
        if any(_contains_phrase(evidence_text, marker) for marker in person_markers):
            query_kind = "person"
        elif any(_contains_phrase(evidence_text, marker) for marker in place_markers):
            query_kind = "place"
        elif any(_contains_phrase(evidence_text, marker) for marker in food_markers):
            query_kind = "food"
        elif any(_contains_phrase(evidence_text, marker) for marker in object_markers):
            query_kind = "object"
    if not provider_is_youtube:
        typed_queries: list[str] = []
        # Typed retrieval waves lead the search, so they must retain the
        # scene's date/model/era contract just like the later canonical waves.
        # An unqualified portrait query can otherwise crowd out period evidence.
        typed_identity = " ".join([identity, *qualifiers]).strip()
        if query_kind == "person":
            typed_queries = [f"{typed_identity} portrait", f"{typed_identity} photo"]
            if req.aliases:
                # Indexes often use a public figure's formal/birth name rather
                # than the title the narrator uses. Put the strongest alias in
                # the first retrieval wave; later gates still verify pixels.
                best_alias = str(req.aliases[0]).strip()
                if best_alias:
                    dated_alias = " ".join([best_alias, *qualifiers]).strip()
                    typed_queries.extend((f"{dated_alias} portrait", f"{dated_alias} photo"))
            else:
                typed_queries.extend((
                    f"{typed_identity} official event photograph",
                    f"{typed_identity} press conference photograph",
                ))
            if _contains_phrase(evidence_text, "cricket"):
                typed_queries.insert(2, f"{typed_identity} cricket action photograph")
            elif any(_contains_phrase(evidence_text, marker) for marker in ("football", "soccer")):
                typed_queries.insert(2, f"{typed_identity} football match photograph")
        elif query_kind == "place":
            typed_queries = [
                f"{typed_identity} landmark photograph",
                f"{typed_identity} location photo",
                f"{typed_identity} skyline landscape photograph",
                f"{typed_identity} travel documentary photograph",
            ]
        elif query_kind == "object":
            typed_queries = [
                f"{typed_identity} product photograph",
                f"{typed_identity} detail photo",
                f"{typed_identity} official reference image",
                f"{typed_identity} in use photograph",
            ]
        elif query_kind == "food":
            typed_queries = [
                f"{identity} food photograph",
                f"{identity} fresh produce photo",
                f"{identity} recipe ingredient photograph",
                f"{identity} nutrition reference image",
            ]
        if typed_queries:
            queries = typed_queries + queries
    # YouTube is a motion archive, not a single-result image search. Give it
    # several evidence-oriented retrieval waves so a strong entity can expose
    # distinct usable moments even when one query is dominated by a presenter,
    # title card, or a single modern review. These are intentionally generic
    # subject-type waves; they never add a topic-specific keyword patch and the
    # returned asset still has to pass the candidate and visual gates.
    if provider.casefold() in {"youtube", "yt", "video"}:
        if req.subject_type == "automobile":
            queries.extend([
                " ".join([identity, *qualifiers, "walkaround", "exterior detail", *query_negative_terms]),
                " ".join([identity, *qualifiers, "driving road test", *query_negative_terms]),
                " ".join([identity, *qualifiers, "engine startup mechanical", *query_negative_terms]),
                " ".join([identity, *qualifiers, "restoration workshop", *query_negative_terms]),
                " ".join([identity, *qualifiers, "race circuit historical archive", *query_negative_terms]),
                " ".join([identity, *qualifiers, "car show event", *query_negative_terms]),
            ])
        elif req.subject_type == "video_game":
            queries.extend([
                " ".join([identity, *qualifiers, "gameplay walkthrough 4k", *query_negative_terms]),
                " ".join([identity, *qualifiers, "official gameplay trailer", *query_negative_terms]),
                " ".join([identity, *qualifiers, "cinematic trailer 4k", *query_negative_terms]),
                " ".join([identity, *qualifiers, "gameplay cutscene", *query_negative_terms]),
            ])
        elif req.subject_type == "rail_crew":
            queries.extend([
                " ".join([identity, *qualifiers, "conductor locomotive engineer cab operations", *query_negative_terms]),
                " ".join([identity, *qualifiers, "railway crew day in the life", *query_negative_terms]),
                " ".join([identity, *qualifiers, "train departure platform operations", *query_negative_terms]),
                " ".join([identity, *qualifiers, "railway cab working footage", *query_negative_terms]),
            ])
        elif req.subject_type == "person":
            queries.extend([
                " ".join([identity, *qualifiers, "public appearance b roll archival footage", *query_negative_terms]),
                " ".join([identity, *qualifiers, "event ceremony b roll footage", *query_negative_terms]),
                " ".join([identity, *qualifiers, "documentary archive footage", *query_negative_terms]),
            ])
        elif req.subject_type == "ship":
            queries.extend([
                " ".join([identity, *qualifiers, "at sea deck footage", *query_negative_terms]),
                " ".join([identity, *qualifiers, "port departure archival footage", *query_negative_terms]),
                " ".join([identity, *qualifiers, "engine room maritime footage", *query_negative_terms]),
            ])
        else:
            queries.extend([
                " ".join([identity, *qualifiers, "field footage", *query_negative_terms]),
                " ".join([identity, *qualifiers, "archive footage", *query_negative_terms]),
                " ".join([identity, *qualifiers, "documentary scene footage", *query_negative_terms]),
            ])
    result: list[str] = []
    seen: set[str] = set()
    for query in queries:
        cleaned_tokens = []
        token_seen = set()
        for token in query.split():
            t_low = token.lower()
            if t_low in token_seen and t_low in {"documentary", "gameplay", "archival", "photograph", "footage", "photo", "reference"}:
                continue
            token_seen.add(t_low)
            cleaned_tokens.append(token)
        cleaned = re.sub(r"\s+", " ", " ".join(cleaned_tokens)).strip()
        if provider.casefold() in {"youtube", "yt", "video"}:
            try:
                from .youtube_query_normalizer import normalize_youtube_query
                cleaned = normalize_youtube_query(
                    cleaned,
                    anchor=identity,
                    category=req.domain,
                    fallback=suffix,
                )
            except Exception:
                # Provider query cleanup is best-effort; the immutable visual
                # requirement and downstream gates remain authoritative.
                pass
        key = _norm(cleaned)
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    if provider.casefold() in {"youtube", "yt", "video"}:
        # Keep enough waves for recall. Search fan-out applies its own bounded
        # per-job cap, so this does not turn into an unbounded provider burst.
        return result[:10]
    return result


def candidate_requirement_match(requirement: Mapping[str, Any], candidate: Mapping[str, Any]) -> tuple[bool, str, Dict[str, Any]]:
    req = VisualRequirementV1.from_mapping(requirement)
    source_metadata = candidate_asset_metadata(candidate)
    # Query text is retrieval intent, not evidence about the returned asset.
    # It must never make a Honda result look like a Jaguar just because the
    # query that found it contained the word Jaguar.
    # Search terms are an instruction to the provider, never evidence about
    # what it returned.  Even lenient/non-strict callers must not approve a
    # candidate whose only identity is the query string.
    metadata = source_metadata
    normalized = _norm(metadata)
    evidence: Dict[str, Any] = {
        "metadata": metadata[:500],
        "metadata_source": "asset" if source_metadata else "query_only",
        "matched_terms": [],
        "missing_terms": [],
    }
    if req.strict_identity and not source_metadata:
        return False, "missing_asset_identity_metadata", evidence
    # A regnal ordinal is part of a monarch's identity, not a soft visual
    # attribute. Vision models can confidently confuse similarly styled
    # portraits, so an explicit metadata contradiction must stop before the
    # visual handoff (e.g. King Charles I is not King Charles III).
    if req.subject_type == "person" and source_metadata:
        regnal_match = re.search(
            r"\b(?P<role>King|Queen|Pope|Emperor|Empress|Tsar|Czar|Pharaoh|Sultan)\s+"
            r"(?P<name>[A-Z][a-z][A-Za-z'’-]*(?:\s+[A-Z][a-z][A-Za-z'’-]*){0,2})\s+"
            r"(?P<ordinal>III|VIII|VII|VI|IV|IX|II|X|V|I|\d{1,2}(?:st|nd|rd|th)?)\b",
            req.canonical_entity,
            flags=re.IGNORECASE,
        )
        if regnal_match:
            role = regnal_match.group("role")
            name = regnal_match.group("name")
            required_ordinal = regnal_match.group("ordinal").casefold()
            candidate_regnal = re.compile(
                r"\b(?:(?:King|Queen|Pope|Emperor|Empress|Tsar|Czar|Pharaoh|Sultan)\s+)?"
                + re.escape(name)
                + r"\s+(?P<ordinal>III|VIII|VII|VI|IV|IX|II|X|V|I|\d{1,2}(?:st|nd|rd|th)?)\b",
                flags=re.IGNORECASE,
            )
            explicit_ordinals = [
                match.group("ordinal").casefold()
                for match in candidate_regnal.finditer(source_metadata)
            ]
            if explicit_ordinals and required_ordinal not in explicit_ordinals:
                return False, "conflicting_person_ordinal", {
                    **evidence,
                    "canonical_entity_mismatch": True,
                    "conflicting_entity": True,
                    "required_person_role": role,
                    "required_person_name": name,
                    "required_person_ordinal": regnal_match.group("ordinal"),
                    "candidate_person_ordinals": explicit_ordinals,
                }
    identity_tokens = [
        token for token in _tokens(req.canonical_entity)
        if token not in _GENERIC_SUBJECTS
        and token not in _HISTORICAL_MARKERS
        # Honorifics/roles are domain labels, not person identity.  Treating
        # ``prince`` as proof for ``Prince William`` was allowing a Prince
        # Harry image to pass the nearest-match handoff.
        and not (req.subject_type == "person" and token in _PERSON_ROLE_TERMS)
        and (len(token) > 2 or token in {"ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"} or token.isdigit())
    ]
    identity_proof = bool(
        identity_tokens
        and all(
            re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", normalized)
            for token in identity_tokens
        )
    ) or any(_contains_phrase(normalized, alias) for alias in req.aliases)
    forbidden_hits = [
        forbidden for forbidden in req.forbidden_subjects
        if _contains_phrase(normalized, forbidden)
    ]
    if req.subject_type == "person":
        confusable_hits = _person_confusable_hits(normalized)
        if confusable_hits:
            return False, "person_confusable_entity", {
                **evidence,
                "confusable_hits": confusable_hits,
            }
    model_proof = any(
        value and _contains_phrase(normalized, value)
        for key, value in req.required_attributes.items()
        if key in {"model", "model_or_make"}
    )
    if req.subject_type == "automobile":
        auto_hits = [term for term in _AUTO_TERMS if _contains_phrase(normalized, term)]
        auto_hits.extend(term for term in _AUTO_BRAND_TERMS if _contains_phrase(normalized, term))
        # Model-shaped names (E-Type, Civic Type R, 300 Series) are automotive
        # evidence even when a provider title omits the generic word “car”.
        if re.search(r"\b[a-z0-9]+[- ](?:type|series|class)(?:\s+(?:r|s|gt|rs|ii|iii|iv))?\b", normalized):
            auto_hits.append("model_pattern")
        if re.search(r"\b[a-z]{1,4}[- ]?\d{2,4}[a-z]?\b", normalized) and auto_hits:
            auto_hits.append("chassis_model_pattern")
        animal_hits = [term for term in _ANIMAL_TERMS if _contains_phrase(normalized, term)]
        if forbidden_hits and (not identity_proof or (animal_hits and not auto_hits)):
            return False, "forbidden_confusable_entity", {**evidence, "forbidden_match": forbidden_hits[0]}
        if animal_hits and not auto_hits:
            return False, "wrong_subject_type_animal", {**evidence, "animal_hits": animal_hits}
        if not auto_hits and not model_proof and req.strict_identity:
            return False, "missing_automobile_evidence", evidence
    elif req.subject_type == "animal":
        animal_hits = [term for term in _ANIMAL_TERMS if _contains_phrase(normalized, term)]
        auto_hits = [term for term in _AUTO_TERMS if _contains_phrase(normalized, term)]
        if forbidden_hits and not identity_proof:
            return False, "forbidden_confusable_entity", {**evidence, "forbidden_match": forbidden_hits[0]}
        if auto_hits and not animal_hits:
            return False, "wrong_subject_type_automobile", {**evidence, "auto_hits": auto_hits}
        if not animal_hits and req.strict_identity:
            return False, "missing_animal_evidence", evidence
    elif req.subject_type == "rail_crew":
        rail_hits = [term for term in _RAIL_CREW_TERMS if _contains_phrase(normalized, term)]
        role_hits = [term for term in _RAIL_CREW_ROLE_TERMS if _contains_phrase(normalized, term)]
        animal_hits = [term for term in _ANIMAL_TERMS if _contains_phrase(normalized, term)]
        if forbidden_hits and not identity_proof:
            return False, "forbidden_confusable_entity", {**evidence, "forbidden_match": forbidden_hits[0]}
        if animal_hits and not rail_hits:
            return False, "wrong_subject_type_animal", {**evidence, "animal_hits": animal_hits}
        # ``train crew`` is the canonical identity, while role metadata is a
        # valid semantic alias. Require a rail-domain signal plus either the
        # canonical phrase or an unambiguous crew role; query text alone is
        # never included in this evidence set.
        if not rail_hits or not (identity_proof or role_hits):
            return False, "missing_rail_crew_evidence", {
                **evidence,
                "rail_hits": rail_hits,
                "role_hits": role_hits,
            }
    elif forbidden_hits:
        return False, "forbidden_confusable_entity", {**evidence, "forbidden_match": forbidden_hits[0]}
    canonical_tokens = [
        token for token in _tokens(req.canonical_entity)
        if token not in _GENERIC_SUBJECTS
        and token not in _HISTORICAL_MARKERS
        and not (req.subject_type == "person" and token in _PERSON_ROLE_TERMS)
        and (len(token) > 2 or token in {"ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"} or token.isdigit())
        and not _is_generic_category_phrase(req.canonical_entity)
    ]
    is_yt = str(
        candidate.get("provider") or candidate.get("source") or ""
    ).strip().casefold() in {"youtube", "yt", "youtube_v2"}
    if canonical_tokens:
        matched = [token for token in canonical_tokens if re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", normalized)]
        evidence["matched_terms"] = matched
        evidence["missing_terms"] = [token for token in canonical_tokens if token not in matched]
        alias_match = any(_contains_phrase(normalized, alias) for alias in req.aliases)
        entity_norm = _norm(req.canonical_entity)
        phrase_match = _contains_phrase(normalized, entity_norm) or alias_match
        if not phrase_match and len(canonical_tokens) >= 2:
            core_entities = [
                part.strip()
                for part in re.split(r"\b(?:and|with|family|story|history|adventures|life)\b", entity_norm)
                if len(_tokens(part)) >= 2
            ]
            if core_entities and any(_contains_phrase(normalized, core) for core in core_entities):
                phrase_match = True

        if len(canonical_tokens) >= 2 and not phrase_match:
            min_matched = max(2, (len(canonical_tokens) * 2 + 1) // 3)
            thematic_pass = (len(matched) >= min_matched) or alias_match
            if not thematic_pass and req.subject_type not in {"rail_crew"}:
                evidence["canonical_entity_mismatch"] = True
                return False, "canonical_entity_mismatch", evidence
            if thematic_pass:
                evidence["thematic_near_match"] = True
        elif all(token in matched for token in canonical_tokens):
            evidence["identity_proof"] = req.canonical_entity
        if alias_match or phrase_match:
            evidence["identity_proof"] = req.canonical_entity
    for key, value in req.required_attributes.items():
        if key in {"year", "decade", "model", "model_or_make", "vehicle_class"}:
            if value and _contains_phrase(normalized, value):
                continue
            if is_yt and key == "year" and value and re.search(r"\b(?:18|19|20)\d{2}\b", normalized):
                try:
                    required_year = int(re.search(r"\b(?:18|19|20)\d{2}\b", str(value)).group(0))
                except (AttributeError, TypeError, ValueError):
                    required_year = 0
                candidate_years = [
                    int(match.group(0))
                    for match in re.finditer(r"\b(?:18|19|20)\d{2}\b", normalized)
                ]
                try:
                    tolerance = max(
                        1,
                        min(
                            15,
                            int(os.environ.get("VIDZEO_YT_NEAREST_YEAR_TOLERANCE", "10") or 10),
                        ),
                    )
                except (TypeError, ValueError):
                    tolerance = 10
                nearest_year = min(
                    candidate_years,
                    key=lambda year: abs(year - required_year),
                    default=0,
                )
                model_evidence = bool(model_proof or identity_proof or evidence.get("thematic_near_match"))
                if (
                    required_year
                    and nearest_year
                    and model_evidence
                    and abs(nearest_year - required_year) <= tolerance
                ):
                    evidence["year_match_mode"] = "youtube_nearest_same_model_year"
                    evidence["required_year"] = required_year
                    evidence["matched_year"] = nearest_year
                    evidence["year_delta"] = abs(nearest_year - required_year)
                    continue
            if key == "year" and value and re.search(r"\b(?:18|19|20)\d{2}\b", normalized):
                return False, "wrong_required_year", {**evidence, "required_year": value}
            if identity_proof or evidence.get("thematic_near_match"):
                # Thematic near-match for real media: assets accurately featuring the subject
                # or topic are resolved even when micro-details (like exact year or model sub-trim)
                # are not repeated verbatim in short titles.
                evidence["thematic_attribute_pass"] = key
                continue
            if key == "vehicle_class" and (identity_proof or model_proof):
                # A model name is stronger identity evidence than whether a
                # provider happened to include the class label in its title.
                # Vision still checks the actual body style; metadata must not
                # reject a valid archival result for omitting "sports car".
                continue
            if key in {"model", "model_or_make", "vehicle_class"} and req.strict_identity:
                return False, f"missing_required_{key}", {**evidence, "required": value}
    if req.required_attributes.get("era") == "classic_historical":
        modern_hits = [term for term in _MODERN_MARKERS if _contains_phrase(normalized, term)]
        if modern_hits and not any(_contains_phrase(normalized, term) for term in _HISTORICAL_MARKERS):
            return False, "wrong_modern_era", {**evidence, "modern_hits": modern_hits}
    return True, "identity_evidence_passed", {**evidence, "requirement_hash": req.requirement_hash}


def candidate_nearest_match(
    requirement: Mapping[str, Any], candidate: Mapping[str, Any]
) -> tuple[bool, str, Dict[str, Any]]:
    """Return a bounded visual-verification handoff for a near match.

    Provider metadata is a recall signal, not the final scene decision.  The
    old flow treated every missing title token as a hard rejection, which
    meant a useful archival frame (or GTA V footage supporting a GTA VI scene)
    never reached the pixel-level verifier.  This helper admits only a
    *same-domain* candidate with positive asset-authored evidence.  It never
    rescues an explicit confusable entity, unsafe subject, presenter/PiP flag,
    or a candidate with no usable metadata.  The returned evidence is marked
    pending so downstream code must still run the live visual gate.
    """
    req = VisualRequirementV1.from_mapping(requirement)
    source_metadata = candidate_asset_metadata(candidate)
    normalized = _norm(source_metadata)
    base: Dict[str, Any] = {
        "metadata": source_metadata[:500],
        "metadata_source": "asset" if source_metadata else "query_only",
        "matched_terms": [],
        "missing_terms": [],
        "nearest_match": True,
        "thematic_near_match": True,
        "verification_required": True,
        "requirement_hash": req.requirement_hash,
    }
    if not normalized:
        return False, "missing_asset_identity_metadata", {**base, "nearest_match": False}

    canonical_tokens = [
        token for token in _tokens(req.canonical_entity)
        if token not in _GENERIC_SUBJECTS
        and token not in _HISTORICAL_MARKERS
        # Honorifics are shared by many people and cannot establish identity
        # for a nearest-match handoff (``Prince`` alone is insufficient).
        and not (req.subject_type == "person" and token in _PERSON_ROLE_TERMS)
        and (len(token) > 2 or token in {"ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"} or token.isdigit())
        and not _is_generic_category_phrase(req.canonical_entity)
    ]
    matched = [
        token for token in canonical_tokens
        if re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", normalized)
    ]
    alias_match = any(_contains_phrase(normalized, alias) for alias in req.aliases)
    base["matched_terms"] = matched
    base["missing_terms"] = [token for token in canonical_tokens if token not in matched]

    # Never turn a known forbidden/confusable result into a nearest match.
    forbidden_hits = [
        forbidden for forbidden in req.forbidden_subjects
        if _contains_phrase(normalized, forbidden)
    ]
    identity_overlap = bool(alias_match or matched)
    if forbidden_hits and not identity_overlap:
        return False, "forbidden_confusable_entity", {**base, "forbidden_match": forbidden_hits[0], "nearest_match": False}

    subject_type = str(req.subject_type or "concept").strip().lower()
    auto_hits = [term for term in _AUTO_TERMS if _contains_phrase(normalized, term)]
    animal_hits = [term for term in _ANIMAL_TERMS if _contains_phrase(normalized, term)]
    ship_hits = [term for term in _SHIP_TERMS if _contains_phrase(normalized, term)]
    rail_hits = [term for term in _RAIL_CREW_TERMS if _contains_phrase(normalized, term)]
    role_hits = [term for term in _RAIL_CREW_ROLE_TERMS if _contains_phrase(normalized, term)]
    if subject_type == "automobile":
        if animal_hits and not auto_hits:
            return False, "wrong_subject_type_animal", {**base, "animal_hits": animal_hits, "nearest_match": False}
        # A classic requirement must not fall through to a clearly modern
        # vehicle when the candidate does not carry the requested identity.
        modern_hits = [term for term in _MODERN_MARKERS if _contains_phrase(normalized, term)]
        if modern_hits and "classic_historical" == req.required_attributes.get("era") and not identity_overlap:
            return False, "wrong_modern_era", {**base, "modern_hits": modern_hits, "nearest_match": False}
        if not auto_hits:
            return False, "missing_automobile_evidence", {**base, "nearest_match": False}
        base.update({"domain_signal": "automotive", "auto_hits": auto_hits[:8]})
    elif subject_type == "animal":
        if auto_hits and not animal_hits:
            return False, "wrong_subject_type_automobile", {**base, "auto_hits": auto_hits, "nearest_match": False}
        if not animal_hits:
            return False, "missing_animal_evidence", {**base, "nearest_match": False}
        base.update({"domain_signal": "wildlife", "animal_hits": animal_hits[:8]})
    elif subject_type == "ship":
        if not ship_hits:
            return False, "missing_ship_evidence", {**base, "nearest_match": False}
        base.update({"domain_signal": "maritime", "ship_hits": ship_hits[:8]})
    elif subject_type == "rail_crew":
        # Keep the existing, stricter rail-role handoff. It already requires
        # an occupational/operations signal and explicitly rejects wildlife.
        if animal_hits or not rail_hits or not (role_hits or any(_contains_phrase(normalized, term) for term in _RAIL_CREW_CONTEXT_TERMS)):
            return False, "missing_rail_crew_evidence", {**base, "rail_hits": rail_hits, "role_hits": role_hits, "nearest_match": False}
        base.update({"domain_signal": "rail_transport", "rail_hits": rail_hits[:8], "role_hits": role_hits[:8]})
    elif subject_type == "video_game":
        game_hits = [term for term in _GAME_DOMAIN_TERMS if _contains_phrase(normalized, term)]
        if not game_hits:
            return False, "missing_video_game_evidence", {**base, "nearest_match": False}
        canonical_game = _norm(req.canonical_entity)
        gta_req = canonical_game in {"grand theft auto vi", "grand theft auto v"}
        gta_candidate = bool(
            _contains_phrase(normalized, "grand theft auto")
            or re.search(r"(?<![a-z0-9])gta\s*(?:iv|v|vi|4|5|6)(?![a-z0-9])", normalized)
        )
        if gta_req and not gta_candidate:
            return False, "canonical_entity_mismatch", {**base, "nearest_match": False}
        gta_vi_requested = canonical_game == "grand theft auto vi"
        gta_v_candidate = bool(
            re.search(r"\bgrand theft auto\s*v\b", normalized)
            or re.search(r"(?<![a-z0-9])gta\s*(?:v|5)(?![a-z0-9])", normalized)
        )
        if gta_vi_requested and gta_v_candidate:
            authored_scene_context = _norm(" ".join(req.positive_evidence))
            explicit_comparison = bool(
                re.search(r"\b(?:gta\s*(?:v|5)|grand theft auto\s*v)\b", authored_scene_context)
                and re.search(
                    r"\b(?:compare|comparison|compared|previous|predecessor|prior|earlier|legacy|side by side|baseline|reference|supporting example)\b",
                    authored_scene_context,
                )
                and not re.search(r"\b(?:avoid|exclude|reject|no|not|never|without|instead of|do not use)\b", authored_scene_context)
            )
            if not explicit_comparison:
                return False, "game_installment_mismatch", {
                    **base,
                    "conflicting_entity": True,
                    "required_game": "Grand Theft Auto VI",
                    "candidate_game": "Grand Theft Auto V",
                    "nearest_match": False,
                }
        if not gta_req and not identity_overlap:
            return False, "canonical_entity_mismatch", {**base, "nearest_match": False}
        base.update({"domain_signal": "gaming", "game_hits": game_hits[:8]})
        if gta_req and gta_candidate and not alias_match:
            base["nearest_basis"] = "same_franchise_version"
    elif subject_type == "person":
        # A partial name is useful only when the role/domain agrees. This is
        # what keeps Prince Harry from falling through to Harry Styles or a
        # Harry Potter result while still allowing a role-labelled archive.
        confusable_hits = _person_confusable_hits(normalized)
        if confusable_hits:
            return False, "person_confusable_entity", {
                **base,
                "confusable_hits": confusable_hits,
                "nearest_match": False,
            }
        req_text = _norm(" ".join((req.canonical_entity, *req.positive_evidence, *req.domain_evidence)))
        req_roles = {term for term in _PERSON_ROLE_TERMS if _contains_phrase(req_text, term)}
        cand_roles = {term for term in _PERSON_ROLE_TERMS if _contains_phrase(normalized, term)}
        if not identity_overlap:
            return False, "canonical_entity_mismatch", {**base, "nearest_match": False}
        if req_roles and not (req_roles & cand_roles):
            return False, "person_role_mismatch", {**base, "required_roles": sorted(req_roles), "candidate_roles": sorted(cand_roles), "nearest_match": False}
        base.update({"domain_signal": "person", "role_hits": sorted(cand_roles)})
    else:
        # Named/concept scenes still need either a canonical token or a
        # scene-domain/action signal. Query text is deliberately excluded.
        context_terms = set(_tokens(" ".join(req.action_terms)))
        context_hits = [term for term in context_terms if _contains_phrase(normalized, term)]
        if not identity_overlap and not context_hits:
            return False, "canonical_entity_mismatch", {**base, "nearest_match": False}
        base.update({"domain_signal": req.domain or "documentary", "context_hits": context_hits[:8]})

    # Required attributes may be omitted from a short provider title; an
    # explicit conflicting year/model remains a hard mismatch. Same-domain
    # candidates with no conflict are allowed to reach visual verification.
    required_year = str(req.required_attributes.get("year") or "").strip()
    if required_year:
        candidate_years = [match.group(0) for match in re.finditer(r"\b(?:18|19|20)\d{2}\b", normalized)]
        if candidate_years and required_year not in candidate_years:
            try:
                tolerance = max(1, min(15, int(os.environ.get("VIDZEO_NEAREST_YEAR_TOLERANCE", "10") or 10)))
                delta = min(abs(int(year) - int(required_year)) for year in candidate_years)
            except (TypeError, ValueError):
                delta = 999
                tolerance = 0
            if delta > tolerance:
                return False, "wrong_required_year", {**base, "required_year": required_year, "candidate_years": candidate_years, "nearest_match": False}
            base.update({"nearest_year": True, "year_delta": delta, "required_year": required_year})
    base["match_distance"] = max(0, len(base.get("missing_terms") or []))
    base["nearest_basis"] = base.get("nearest_basis") or "same_domain_context"
    return True, "nearest_context_pending_visual_required", base


def candidate_domain_context_pending(
    requirement: Mapping[str, Any], candidate: Mapping[str, Any]
) -> tuple[bool, Dict[str, Any]]:
    """Escalate plausible domain metadata to vision without accepting it.

    A source such as ``Rail Maintenance Crew`` is useful recall for a Train
    Crew scene even though it does not repeat the exact contract phrase. This
    function is intentionally only a pending transition: it never reads the
    retrieval query, never grants identity proof, and still requires the live
    visual verifier before selection.
    """
    req = VisualRequirementV1.from_mapping(requirement)
    supported_types = {"rail_crew", "automobile", "car", "person", "royal", "celebrity", "history", "documentary"}
    if req.subject_type not in supported_types:
        return False, {}
    source_metadata = candidate_asset_metadata(candidate)
    normalized = _norm(source_metadata)
    if not normalized:
        return False, {}

    if req.subject_type in {"automobile", "car"}:
        auto_hits = [t for t in _AUTO_TERMS if _contains_phrase(normalized, t)]
        brand_hits = [t for t in _AUTO_BRAND_TERMS if _contains_phrase(normalized, t)]
        if (auto_hits or brand_hits) and not any(_contains_phrase(normalized, t) for t in _ANIMAL_TERMS):
            return True, {
                "metadata": source_metadata[:500],
                "metadata_source": "asset",
                "auto_hits": auto_hits,
                "brand_hits": brand_hits,
                "pending": True,
            }
        return False, {}

    if req.subject_type in {"person", "royal", "celebrity", "history", "documentary"}:
        person_hits = [t for t in _PERSON_TERMS | _PERSON_ROLE_TERMS if _contains_phrase(normalized, t)]
        history_hits = [t for t in _HISTORICAL_MARKERS if _contains_phrase(normalized, t)]
        if person_hits or history_hits:
            return True, {
                "metadata": source_metadata[:500],
                "metadata_source": "asset",
                "person_hits": person_hits,
                "history_hits": history_hits,
                "pending": True,
            }
        return False, {}

    rail_hits = [
        term for term in _RAIL_CREW_TERMS
        if _contains_phrase(normalized, term)
    ]
    context_hits = [
        term for term in _RAIL_CREW_CONTEXT_TERMS
        if _contains_phrase(normalized, term)
    ]
    role_hits = [
        term for term in _RAIL_CREW_ROLE_TERMS
        if _contains_phrase(normalized, term)
    ]
    animal_hits = [
        term for term in _ANIMAL_TERMS
        if _contains_phrase(normalized, term)
    ]
    # ``train`` alone is too broad (for example, model trains or a robbery
    # story). Require a rail signal plus an occupational/operations signal.
    strong_rail_hits = [
        term for term in rail_hits
        if term not in {"train", "trains", "driver", "drivers"}
    ]
    evidence = {
        "metadata": source_metadata[:500],
        "metadata_source": "asset",
        "rail_hits": rail_hits,
        "role_hits": role_hits,
        "context_hits": context_hits,
        "animal_hits": animal_hits,
        "pending": False,
    }
    if animal_hits or not strong_rail_hits or not (role_hits or context_hits):
        return False, evidence
    evidence["animal_hits"] = []
    evidence["pending"] = True
    return True, evidence


def graphic_fallback_spec(requirement: Mapping[str, Any], scene_text: str, *, scene_index: int = 0) -> Dict[str, Any]:
    req = VisualRequirementV1.from_mapping(requirement)
    facts = [value for value in (req.canonical_entity, req.required_attributes.get("year"), req.required_attributes.get("decade")) if value]
    return {
        "schema_version": "graphic_fallback.v1",
        "scene_index": int(scene_index),
        "template_id": req.fallback_template,
        "entity": req.canonical_entity,
        "subject_type": req.subject_type,
        "facts": facts,
        "text": _text(scene_text)[:240],
        "requirement_hash": req.requirement_hash,
        "grounded": True,
        "source": "generated_motion_graphic",
    }
