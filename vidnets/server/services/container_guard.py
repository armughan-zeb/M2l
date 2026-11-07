"""Hard container isolation for scene-level media retrieval.

The rest of the pipeline can be fast and permissive, but container identity must
be deterministic. A Messi scene may fall back to a Messi query or stay
unresolved; it must not borrow a Ronaldo query, candidate, or reused clip.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Set


KNOWN_SUBJECT_ALIASES = (
    ("Lionel Messi", ("lionel messi", "leo messi", "messi")),
    ("Cristiano Ronaldo", ("cristiano ronaldo", "cristiano", "ronaldo", "cr7")),
    ("Kylian Mbappe", ("kylian mbappe", "kylian mbapp\u00e9", "mbappe", "mbapp\u00e9", "mbapp", "kylian")),
    ("Erling Haaland", ("erling haaland", "haaland", "erling")),
    ("Jude Bellingham", ("jude bellingham", "bellingham", "jude")),
    ("Lamine Yamal", ("lamine yamal", "yamal", "lamine")),
    ("Harry Kane", ("harry kane", "kane", "harry")),
    ("Mohamed Salah", ("mohamed salah", "mo salah", "salah")),
    ("Neymar Jr", ("neymar jr", "neymar junior", "neymar")),
    ("Robert Lewandowski", ("robert lewandowski", "lewandowski")),
    ("Kevin De Bruyne", ("kevin de bruyne", "de bruyne", "debruyne")),
    ("Virat Kohli", ("virat kohli", "kohli", "virat")),
    ("Babar Azam", ("babar azam", "babar", "azam")),
    ("John Cena", ("john cena", "cena")),
    ("Roman Reigns", ("roman reigns", "reigns")),
    ("The Undertaker", ("the undertaker", "undertaker")),
    ("Donald Trump", ("donald trump", "trump")),
    ("Joe Biden", ("joe biden", "biden")),
    ("Vladimir Putin", ("vladimir putin", "putin")),
    ("Elon Musk", ("elon musk", "musk")),
    ("Grand Theft Auto VI", ("grand theft auto vi", "grand theft auto 6", "gta vi", "gta 6")),
    ("Rockstar Games", ("rockstar games", "rockstar")),
)

DOMAIN_SUBJECTS = {
    "fruit": {
        "domain_alias": "fruit nutrition",
        "keywords": {
            "fruit", "fruits", "nutrition", "nutrient", "vitamin", "vitamins",
            "diet", "healthy", "health", "fiber", "antioxidant", "antioxidants",
            "juice", "smoothie", "fresh produce", "produce",
        },
        "subjects": {
            "apple": "Apple fruit", "apples": "Apple fruit",
            "banana": "Banana fruit", "bananas": "Banana fruit",
            "orange": "Orange fruit", "oranges": "Orange fruit",
            "mango": "Mango fruit", "mangoes": "Mango fruit",
            "grape": "Grape fruit", "grapes": "Grape fruit",
            "strawberry": "Strawberry fruit", "strawberries": "Strawberry fruit",
            "blueberry": "Blueberry fruit", "blueberries": "Blueberry fruit",
            "pineapple": "Pineapple fruit", "watermelon": "Watermelon fruit",
            "kiwi": "Kiwi fruit", "papaya": "Papaya fruit", "avocado": "Avocado fruit",
            "pomegranate": "Pomegranate fruit", "peach": "Peach fruit",
            "pear": "Pear fruit", "lemon": "Lemon fruit", "lime": "Lime fruit",
            "cherry": "Cherry fruit", "cherries": "Cherry fruit",
        },
    },
    "vegetable": {
        "domain_alias": "vegetable nutrition",
        "keywords": {
            "vegetable", "vegetables", "nutrition", "healthy", "health",
            "diet", "salad", "produce", "greens",
        },
        "subjects": {
            "carrot": "Carrot vegetable", "carrots": "Carrot vegetable",
            "broccoli": "Broccoli vegetable", "spinach": "Spinach vegetable",
            "potato": "Potato vegetable", "potatoes": "Potato vegetable",
            "tomato": "Tomato vegetable", "tomatoes": "Tomato vegetable",
            "onion": "Onion vegetable", "onions": "Onion vegetable",
        },
    },
    "gaming": {
        "domain_alias": "video game official trailer gameplay",
        "keywords": {
            "video game", "gaming", "gameplay", "trailer", "official trailer",
            "publisher", "studio", "console", "playstation", "xbox", "rockstar games",
        },
        "subjects": {
            "gta vi": "Grand Theft Auto VI", "gta 6": "Grand Theft Auto VI",
            "grand theft auto vi": "Grand Theft Auto VI",
            "grand theft auto 6": "Grand Theft Auto VI",
            "rockstar": "Rockstar Games", "rockstar games": "Rockstar Games",
        },
    },
}


WEAK_CONTAINER_TERMS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "from",
    "with", "without", "this", "that", "these", "those", "there", "here", "today",
    "every", "its", "their", "our", "your",
    "tonight", "now", "next", "then", "when", "while", "whether", "because", "time",
    "before", "after", "later", "today", "meanwhile", "once", "here", "there",
    "he", "she", "his", "her", "they", "their", "them", "it", "its", "we", "you", "our", "my",
    "inside", "across", "during", "despite", "fame", "internet", "hollywood",
    "wwe", "wwf", "actor", "actress", "wrestling", "gym", "entrance", "character",
    "probably", "maybe", "some", "any", "all", "few", "many", "other", "another", "one", "two",
    "three", "four", "five", "first", "second", "third", "fourth", "fifth",
    "are", "is", "was", "were", "be", "being", "been", "has", "have", "had",
    "give", "gives", "gave", "given", "takes", "take", "taking", "took", "make", "makes",
    "made", "put", "puts", "see", "seen", "show", "shows",
    "number", "rank", "spot", "place", "top", "best", "worst", "greatest",
    "generation", "generations", "legend", "legends", "star", "stars", "player", "players",
    "football", "footballer", "footballers", "soccer", "sport", "sports",
    "fan", "fans", "simply", "many fans", "for many fans",
    "fifa", "world", "cup", "world cup", "league", "match", "matches",
    "goal", "goals", "score", "scoring", "chance", "attacker", "attackers",
    "striker", "strikers", "midfielder", "midfielders", "dominant", "talented",
    "era", "history", "current", "modern", "rewritten", "consistent",
    "consistently", "deliver", "delivers", "delivered", "finish", "finishing",
    "speed", "strength", "skill", "skills", "technical", "vision", "leadership",
    "legacy", "complete", "combination", "unstoppable", "produced", "seen",
    "choose", "choosing", "chosen", "pick", "picking", "selected", "selecting",
    "biggest", "highest", "stage", "stages", "night", "young", "incredible", "even",
    "footage", "video", "clip", "clips", "scene", "story", "section",
    "intro", "outro", "container", "subject", "topic",
    "what", "if", "just", "can", "you",
    "could", "improve", "health", "healthy", "boost", "help", "live", "longer",
    "add", "adds", "adding", "daily", "diet", "meal", "meals", "nutrition",
    "fruit", "fruits", "food", "foods", "vegetable", "vegetables",
    "universe", "cosmos",
    "here top five best", "football generation legends", "football generation legends talented",
    "today era produced some", "best football stars",
}


_ALIAS_TO_FULL: Dict[str, str] = {}
for _full, _aliases in KNOWN_SUBJECT_ALIASES:
    for _alias in _aliases:
        _ALIAS_TO_FULL[_alias] = _full

_SUBJECT_ALIASES_BY_FULL: Dict[str, tuple] = {
    full: tuple(dict.fromkeys((full, *aliases)))
    for full, aliases in KNOWN_SUBJECT_ALIASES
}


def _normalize_alias_seed(value: str) -> str:
    text = str(value or "")
    text = text.replace("Ã©", "e").replace("Ã‰", "e").replace("Ã¨", "e")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'").replace("`", "'").lower()
    text = re.sub(r"['’]s\b", "", text)
    text = re.sub(r"[^a-z0-9\s&'-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_NORMALIZED_ALIAS_ROWS = tuple(
    (full, tuple(dict.fromkeys(_normalize_alias_seed(alias) for alias in aliases if _normalize_alias_seed(alias))))
    for full, aliases in KNOWN_SUBJECT_ALIASES
)


@lru_cache(maxsize=8192)
def _ascii_fold(value: str) -> str:
    text = str(value or "")
    text = text.replace("Ã©", "e").replace("Ã‰", "e").replace("Ã¨", "e")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'").replace("`", "'")
    return text


@lru_cache(maxsize=8192)
def normalize_subject_key(value: str) -> str:
    text = _ascii_fold(value).lower()
    text = re.sub(r"['’]s\b", "", text)
    text = re.sub(r"[^a-z0-9\s&'-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


@lru_cache(maxsize=8192)
def _alias_hits_tuple(value: str) -> tuple:
    key = normalize_subject_key(value)
    if not key:
        return tuple()
    hits: List[str] = []
    for full, aliases in _NORMALIZED_ALIAS_ROWS:
        for alias_key in aliases:
            if alias_key and re.search(rf"(?<![a-z0-9]){re.escape(alias_key)}(?![a-z0-9])", key):
                hits.append(full)
                break
    return tuple(dict.fromkeys(hits))


def _alias_hits(value: str) -> List[str]:
    return list(_alias_hits_tuple(str(value or "")))


def _infer_domain_key(*values: str) -> str:
    text = normalize_subject_key(" ".join(str(value or "") for value in values))
    if not text:
        return ""
    scores: Dict[str, int] = {}
    for domain, meta in DOMAIN_SUBJECTS.items():
        keyword_score = 0
        subject_score = 0
        for keyword in meta.get("keywords", set()):
            key = normalize_subject_key(keyword)
            if key and re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", text):
                keyword_score += 2
        for token in meta.get("subjects", {}):
            if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text):
                subject_score += 1
        # A bare ambiguous item like "Apple" must not force a fruit domain.
        # Require domain evidence from the script/title, or multiple same-domain
        # items in the scene.
        if keyword_score or subject_score >= 2:
            scores[domain] = keyword_score + subject_score
    return max(scores.items(), key=lambda item: item[1])[0] if scores else ""


def _domain_subject_hits(value: str, domain: str) -> List[str]:
    key = normalize_subject_key(value)
    if not key or domain not in DOMAIN_SUBJECTS:
        return []
    hits: List[str] = []
    for token, full in DOMAIN_SUBJECTS[domain].get("subjects", {}).items():
        token_key = normalize_subject_key(token)
        if token_key and re.search(rf"(?<![a-z0-9]){re.escape(token_key)}(?![a-z0-9])", key):
            hits.append(full)
    return list(dict.fromkeys(hits))


def domain_context_for_subject(subject: str, scene_text: str = "", topic: str = "") -> str:
    key = _infer_domain_key(subject, scene_text, topic)
    if key:
        return key
    subject_key = normalize_subject_key(subject)
    for domain, meta in DOMAIN_SUBJECTS.items():
        if any(subject_key == normalize_subject_key(full) for full in meta.get("subjects", {}).values()):
            return domain
    return ""


def _first_alias_in_text(subjects: Iterable[str], text: str) -> str:
    key = normalize_subject_key(text)
    if not key:
        return ""
    best_full = ""
    best_pos: Optional[int] = None
    for full in subjects:
        aliases = _SUBJECT_ALIASES_BY_FULL.get(full, ())
        for alias in aliases:
            alias_key = normalize_subject_key(alias)
            if not alias_key:
                continue
            match = re.search(rf"(?<![a-z0-9]){re.escape(alias_key)}(?![a-z0-9])", key)
            if match and (best_pos is None or match.start() < best_pos):
                best_pos = match.start()
                best_full = full
    return best_full


def _clean_candidate_subject(value: str, max_words: int = 6) -> str:
    text = _ascii_fold(value)
    text = re.sub(r"[^A-Za-z0-9\s&'-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_'\"")
    words = text.split()[:max_words]
    while words and words[0].lower() in {"the", "a", "an", "and", "or", "is", "are"}:
        words.pop(0)
    while words and words[-1].lower() in {"the", "a", "an", "and", "or", "is", "are", "of", "in", "on", "for", "to"}:
        words.pop()
    while words and not re.search(r"[A-Za-z0-9]", words[-1]):
        words.pop()
    while words and not re.search(r"[A-Za-z0-9]", words[0]):
        words.pop(0)
    return " ".join(words).strip(" -_'\"")


def _leading_entity_from_clause(value: str) -> str:
    """Extract a proper-name lead from a title-like clause.

    Local/fallback planners frequently receive a title fragment instead of an
    entity, for example ``The Titanic was designed as``. Treating that whole
    clause as the pool key produces searches that no video title contains and
    pushes retrieval toward generic explainers. Keep a leading capitalized
    name (including qualified names such as ``RMS Titanic``) and discard the
    predicate. This is deliberately conservative: it only fires when the
    first meaningful token is capitalized and a lower-case clause follows.
    """
    text = _ascii_fold(value)
    text = re.sub(r"[^A-Za-z0-9\s&'-]+", " ", text)
    words = re.sub(r"\s+", " ", text).strip().split()
    while words and words[0].lower() in {"the", "a", "an", "number", "no"}:
        words.pop(0)
    if not words or not re.match(r"^[A-Z][A-Za-z0-9'&-]*$", words[0]):
        return ""

    clause_markers = {
        "is", "are", "was", "were", "be", "been", "being", "has", "have", "had",
        "contain", "contains", "provide", "provides", "boost", "boosts", "help", "helps",
        "feature", "features", "deliver", "delivers", "built", "designed", "sank", "sunk",
        "sink", "sinks", "fell", "fall", "falls", "collapsed", "collapses", "collapse",
        "exploded", "explodes", "explode", "crashed", "crashes", "crash",
        "failed", "fails", "fail", "disappeared", "disappears", "disappear",
        "died", "dies", "die", "lost", "loses", "lose", "destroyed", "destroys", "destroy",
        "vanished", "vanishes", "vanish", "struck", "strikes", "strike",
        "conquered", "conquers", "conquer", "ruled", "rules", "rule",
        "survived", "survives", "survive", "landed", "lands", "land",
        "carried", "broke", "changed", "remains", "became", "faced",
        "shaped", "made", "served", "opened", "stand", "stands", "ranked",
    }
    entity = [words[0]]
    for index, word in enumerate(words[1:6], start=1):
        if word.lower() in clause_markers:
            break
        if word.lower() in {"of", "the", "and", "&"}:
            if index + 1 < len(words) and words[index + 1][:1].isupper():
                entity.append(word)
                continue
            break
        if not word[:1].isupper() and not word.isdigit():
            break
        # An agency suffix followed by a descriptive clause is a retrieval
        # facet, not part of a person's/place's name. Keep Roman regnal numbers
        # and alphanumeric product models inside their actual identities.
        if (len(entity) >= 2 and word.isupper() and len(word) >= 2
                and not re.fullmatch(r"[IVXLCDM]+", word)
                and not any(char.isdigit() for char in word)
                and index + 1 < len(words) and words[index + 1][:1].islower()):
            break
        entity.append(word)

    res = " ".join(entity).strip()
    if len(entity) == 1 and words[0].isdigit():
        return ""
    return res



def is_weak_container_subject(subject: str) -> bool:
    cleaned = _clean_candidate_subject(subject)
    key = normalize_subject_key(cleaned)
    if not key:
        return True
    if _alias_hits(cleaned):
        return False
    if any(key == normalize_subject_key(meta.get("domain_alias", "")) for meta in DOMAIN_SUBJECTS.values()):
        return False
    if key in WEAK_CONTAINER_TERMS:
        return True
    tokens = [token for token in key.split() if token]
    meaningful = [token for token in tokens if token not in WEAK_CONTAINER_TERMS and len(token) >= 2]
    if not meaningful:
        return True
    if len(tokens) >= 4 and len(meaningful) <= 2:
        return True
    if len(meaningful) == 1 and meaningful[0] in WEAK_CONTAINER_TERMS:
        return True
    if len(meaningful) == 1 and len(meaningful[0]) < 3:
        return True
    return False


def canonical_container_subject(
    subject: str,
    scene_text: str = "",
    topic: str = "",
    known_subjects: Optional[Iterable[str]] = None,
) -> str:
    """Return a leak-safe container subject or an empty string if too weak.

    Known famous-name aliases are expanded to full names. Mixed labels such as
    "World Cup Messi" become "Lionel Messi"; junk labels such as "score" or
    "attackers" are rejected.
    """
    raw = _clean_candidate_subject(subject)
    if not raw:
        return ""

    # ``Harry`` is already a legacy alias for Harry Kane in the sports table.
    # In a royal-family story that mapping is dangerous: canonicalize the
    # title-qualified royal identity before consulting the broad alias map.
    royal_blob = normalize_subject_key(" ".join((raw, scene_text or "", topic or "")))
    raw_identity = normalize_subject_key(raw)
    scene_identity = normalize_subject_key(scene_text or "")
    royal_context = bool(
        re.search(r"\broyal\s+family\b|\bbritish\s+royal\b|\bwindsor\b|\bduke\s+of\s+sussex\b|\bking\s+charles\b|\bcharles\s+iii\b|\bprince\s+harry\b", royal_blob)
        or (re.search(r"\bcharles\b", royal_blob) and re.search(r"\bharry\b", royal_blob))
    )
    if royal_context:
        if re.search(r"\b(?:prince\s+harry|duke\s+of\s+sussex)\b", raw_identity):
            return "Prince Harry"
        if re.search(r"\b(?:king\s+charles|charles\s+iii|prince\s+of\s+wales)\b", raw_identity):
            return "King Charles III"
        if re.search(r"\bharry\b", scene_identity) and not re.search(r"\bcharles\b", scene_identity):
            return "Prince Harry"
        if re.search(r"\bcharles\b", scene_identity) and not re.search(r"\bharry\b", scene_identity):
            return "King Charles III"
        if re.fullmatch(r"harry", raw_identity):
            return "Prince Harry"
        if re.fullmatch(r"charles", raw_identity):
            return "King Charles III"
        if re.search(r"\b(?:royal\s+family|british\s+royal|windsor|monarchy)\b", raw_identity):
            return "British Royal Family"

    domain = _infer_domain_key(topic, scene_text, raw)
    raw_domain_hits = _domain_subject_hits(raw, domain)
    scene_domain_hits = _domain_subject_hits(scene_text, domain)
    subject_hits = _alias_hits(raw)
    context_hits = _alias_hits(scene_text)
    known_hits: List[str] = []
    for item in known_subjects or []:
        known_hits.extend(_alias_hits(str(item or "")))
    known_hits = list(dict.fromkeys(known_hits))

    if len(subject_hits) == 1:
        return subject_hits[0]
    if len(subject_hits) > 1:
        picked = _first_alias_in_text(subject_hits, scene_text) or subject_hits[0]
        return picked
    raw_tokens = raw.split()
    if (
        len(raw_tokens) == 1
        and raw_tokens[0][:1].isupper()
        and re.search(rf"(?<![A-Za-z0-9]){re.escape(raw_tokens[0])}(?![A-Za-z0-9])", str(scene_text or ""))
        and not is_weak_container_subject(raw)
    ):
        return raw
    raw_key = normalize_subject_key(raw)
    phrase_prefixes = (
        "stars like", "players like", "legends like", "names like",
        "including", "such as", "like",
    )
    has_phrase_prefix = any(raw_key.startswith(prefix) for prefix in phrase_prefixes)
    if context_hits and has_phrase_prefix:
        return _first_alias_in_text(context_hits, scene_text) or context_hits[0]
    if len(context_hits) == 1 and is_weak_container_subject(raw):
        return context_hits[0]
    if raw_domain_hits:
        return raw_domain_hits[0]
    if scene_domain_hits and (is_weak_container_subject(raw) or has_phrase_prefix or raw_key in {"scene", "intro", "outro"}):
        return scene_domain_hits[0]
    if known_hits:
        for full in known_hits:
            aliases = dict(KNOWN_SUBJECT_ALIASES).get(full, ())
            if any(re.search(rf"(?<![a-z0-9]){re.escape(normalize_subject_key(alias))}(?![a-z0-9])", normalize_subject_key(raw)) for alias in aliases):
                return full

    # Check if raw is a collection title or documentary hook clause before falling back
    try:
        from .documentary_subject import collection_title_subject, title_hook_subject
        hooked = collection_title_subject(raw) or title_hook_subject(raw, scene_text)
        if hooked and not is_weak_container_subject(hooked):
            return hooked
    except Exception:
        pass

    # A title-derived clause is not a usable pool identity. Preserve the
    # proper-name lead so ``The Titanic was designed as`` becomes ``Titanic``
    # while ordinary descriptive subjects still pass through unchanged.
    leading_entity = _leading_entity_from_clause(raw)
    if (leading_entity and not is_weak_container_subject(leading_entity)
            and re.search(r'\b(?:is|are|was|were|has|have|had|built|designed|sank|sunk|sink|fell|fall|collapsed|exploded|crashed|failed|disappeared|died|lost|destroyed|vanished|struck|conquered|ruled|survived|landed|carried|broke|changed|remains|became|faced|shaped|made|served|opened|ranked)\b', raw, re.I)):
        return leading_entity

    if is_weak_container_subject(raw):
        if domain:
            return str(DOMAIN_SUBJECTS[domain].get("domain_alias") or domain).strip()
        return ""
    return raw


def canonicalize_sections(
    sections: List[Dict],
    scene_texts: Optional[List[str]] = None,
    topic: str = "",
) -> List[Dict]:
    if not sections:
        return []
    known = [
        str(section.get("container_subject") or "")
        for section in sections
        if isinstance(section, dict) and section.get("container_subject")
    ]
    result: List[Dict] = []
    previous = ""
    for idx, section in enumerate(sections):
        row = dict(section or {})
        if row.get("_container_guard_canonical"):
            result.append(row)
            previous = str(row.get("container_subject") or previous or "").strip()
            continue
        scene_text = scene_texts[idx] if scene_texts and idx < len(scene_texts) else ""
        if row.get("is_intro") or str(row.get("container_id") or "").lower() == "outro":
            row["container_subject"] = ""
            result.append(row)
            continue
        raw_subject = str(row.get("container_subject") or "").strip()
        canonical = canonical_container_subject(raw_subject, scene_text, topic, known)
        if not canonical and previous and not row.get("has_explicit_marker"):
            canonical = previous
        row["container_subject"] = canonical
        row["_container_guard_canonical"] = True
        if canonical:
            previous = canonical
        result.append(row)
    return result


def build_forbidden_map(sections: List[Dict], scene_count: int) -> Dict[int, Set[str]]:
    """Build a per-scene map of forbidden canonical subject phrases."""
    canonical_sections = (
        list(sections or [])
        if sections and all(isinstance(sec, dict) and sec.get("_container_guard_canonical") for sec in sections)
        else canonicalize_sections(sections, topic="")
    )
    per_scene_subjects: Dict[int, str] = {}
    for idx, sec in enumerate(canonical_sections):
        subj = str(sec.get("container_subject") or "").strip()
        if subj and not sec.get("is_intro"):
            per_scene_subjects[idx] = subj

    all_subjects = set(per_scene_subjects.values())
    forbidden: Dict[int, Set[str]] = {}
    for i in range(scene_count):
        mine = per_scene_subjects.get(i, "")
        forbidden[i] = set(item for item in all_subjects if item and item != mine)
    return forbidden


def forbidden_subjects_for_index(index: int, section_subjects: List[str]) -> List[str]:
    canonical = [
        canonical_container_subject(subject)
        for subject in section_subjects or []
    ]
    mine = canonical[index] if index < len(canonical) else ""
    result = []
    seen = set()
    for subject in canonical:
        key = normalize_subject_key(subject)
        if not subject or not key or key == normalize_subject_key(mine) or key in seen:
            continue
        seen.add(key)
        result.append(subject)
    return result


def _subject_aliases(subject: str) -> List[str]:
    canonical = canonical_container_subject(subject) or _clean_candidate_subject(subject)
    aliases = [canonical]
    for full, full_aliases in KNOWN_SUBJECT_ALIASES:
        if normalize_subject_key(full) == normalize_subject_key(canonical):
            aliases.extend(full_aliases)
            break
    return [alias for alias in dict.fromkeys(aliases) if alias]


def text_mentions_subject(text: str, subject: str) -> bool:
    key = normalize_subject_key(text)
    if not key:
        return False
    for alias in _subject_aliases(subject):
        alias_key = normalize_subject_key(alias)
        if alias_key and re.search(rf"(?<![a-z0-9]){re.escape(alias_key)}(?![a-z0-9])", key):
            return True
    return False


def strip_forbidden_subjects(text: str, forbidden: Iterable[str]) -> str:
    cleaned = str(text or "")
    if not cleaned:
        return cleaned
    for subject in forbidden or []:
        for alias in sorted(_subject_aliases(subject), key=len, reverse=True):
            alias_key = normalize_subject_key(alias)
            if not alias_key:
                continue
            words = [re.escape(part) for part in alias_key.split()]
            pattern = r"(?<![A-Za-z0-9])" + r"\s+".join(words) + r"(?![A-Za-z0-9])"
            cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
            raw_words = [
                re.escape(part)
                for part in re.sub(r"\s+", " ", str(alias or "")).strip().split()
                if part
            ]
            if raw_words:
                raw_pattern = r"(?<![A-Za-z0-9])" + r"\s+".join(raw_words) + r"(?![A-Za-z0-9])"
                cleaned = re.sub(raw_pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[?]+", " ", cleaned)
    cleaned = re.sub(r"\b(?:vs|versus|and|with)\s*$", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+\b(?:vs|versus|and|with)\b\s+", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_,.:;")
    return cleaned


def _ensure_subject_prefix(query: str, subject: str) -> str:
    query = re.sub(r"\s+", " ", str(query or "")).strip()
    subject = canonical_container_subject(subject) or _clean_candidate_subject(subject)
    if not query or not subject:
        return query
    subject_key = normalize_subject_key(subject)
    query_key = normalize_subject_key(query)
    if subject_key and re.match(rf"^{re.escape(subject_key)}(?:\s|$)", query_key):
        return query
    query = strip_forbidden_subjects(query, [subject])
    return re.sub(r"\s+", " ", f"{subject} {query}").strip()


def enforce_container_isolation(
    rows: List[Dict],
    sections: List[Dict],
    scene_texts: Optional[List[str]] = None,
    topic: str = "",
) -> List[Dict]:
    """Sanitize rows in-place so each scene stays inside its own container."""
    if not rows or not sections:
        return rows

    canonical_sections = canonicalize_sections(sections, scene_texts=scene_texts, topic=topic)
    forbidden_map = build_forbidden_map(canonical_sections, len(canonical_sections))

    query_keys = {
        "search_query", "alt_search_query", "supporting_search_query", "fallback_query",
        "youtube_query", "youtube_supporting_query", "youtube_fallback_query",
        "web_query", "web_alt_query", "web_fallback",
        "primary_query", "specific", "medium", "general",
        "stock_query", "stock_alt_query", "stock_fallback_query", "stock_fallback",
    }

    all_subjects = [
        str(section.get("container_subject") or "").strip()
        for section in canonical_sections
        if str(section.get("container_subject") or "").strip()
    ]

    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        section = canonical_sections[i] if i < len(canonical_sections) else {}
        subject = str(section.get("container_subject") or "").strip()
        forbidden = forbidden_map.get(i, set())
        identity_prefix = str(row.get('scene_subject_type') or '').casefold() not in {'topic', 'category', 'setting', 'action'}

        if subject:
            row["section_subject"] = subject
            row["scene_subject"] = canonical_container_subject(
                row.get("scene_subject") or subject,
                scene_texts[i] if scene_texts and i < len(scene_texts) else "",
                topic,
                all_subjects,
            ) or subject
            row["subject_query"] = subject
        else:
            existing = canonical_container_subject(
                row.get("section_subject") or row.get("scene_subject") or "",
                scene_texts[i] if scene_texts and i < len(scene_texts) else "",
                topic,
                all_subjects,
            )
            row["section_subject"] = existing
            subject = existing
            forbidden = set(item for item in all_subjects if item != subject)

        row["forbidden_subjects"] = [
            item for item in sorted(forbidden)
            if item and normalize_subject_key(item) != normalize_subject_key(subject)
        ]

        for key in query_keys:
            value = str(row.get(key) or "").strip()
            if not value:
                continue
            cleaned = strip_forbidden_subjects(value, forbidden)
            if subject and identity_prefix:
                cleaned = _ensure_subject_prefix(cleaned, subject)
            row[key] = cleaned

        variants = row.get("query_variants")
        if isinstance(variants, list):
            fixed = []
            for value in variants:
                cleaned = strip_forbidden_subjects(str(value or ""), forbidden)
                if subject and identity_prefix:
                    cleaned = _ensure_subject_prefix(cleaned, subject)
                if cleaned:
                    fixed.append(cleaned)
            row["query_variants"] = list(dict.fromkeys(fixed))

    return rows
