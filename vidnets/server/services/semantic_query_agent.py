"""
Semantic Query Agent — replaces raw LLM prompt injection with multi-step
context-aware reasoning for both YouTube and web image queries.

The agent understands:
- Listicle containers (each section is a hard boundary)
- Intro/transition scenes (use domain anchor, not section subject)
- Named entity extraction (proper full names, not fragments)
- Query tiering (primary = specific, supporting = broader, fallback = discovery)
"""

import re
import json
from typing import List, Dict, Optional, Tuple


# ── Core: scene-level subject extraction ──

RANK_WORDS_RE = (
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|"
    r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
)

SECTION_MARKER_PATTERNS = [
    # "Number 5: Banana" / "Number five is..."
    re.compile(
        rf"(?:^|[.!?\n])\s*(?:number|no\.?)\s*(\d{{1,2}}|{RANK_WORDS_RE})"
        r"\s*[.:,)\-–—]?\s*",
        re.IGNORECASE,
    ),
    # "#5" or "#5: item" at start
    re.compile(r"(?:^|\.\s+)#\s*(\d{1,2})\b", re.IGNORECASE),
    # "Fifth place" / "first spot"
    re.compile(rf"\b({RANK_WORDS_RE})\s+(?:place|spot|is)\b", re.IGNORECASE),
    # "at number 5" / "coming in at number 5"
    re.compile(
        rf"(?:at|in|coming\s+in\s+at|landing\s+at)\s+(?:the\s+)?(?:number|no\.?|spot)\s+"
        rf"(\d{{1,2}}|{RANK_WORDS_RE})",
        re.IGNORECASE,
    ),
    # "#5 - item"
    re.compile(r"#\s*(\d{1,2})\s*[-:–—]\s*([^.!?\n]+)", re.IGNORECASE),
]

# Words that should NEVER be section subjects
GENERIC_NOUNS = {
    "health", "benefits", "benefit", "nutrition", "vitamin", "vitamins",
    "mineral", "minerals", "antioxidant", "antioxidants", "fruit", "fruits",
    "vegetable", "vegetables", "food", "foods", "diet", "healthy", "wellness",
    "nutrient", "nutrients", "property", "properties", "effect", "effects",
    "impact", "advantage", "advantages", "superfood", "superfoods",
    "player", "players", "football", "soccer", "sport", "sports",
    "match", "goal", "team", "league", "champion", "champions",
    "stadium", "pitch", "season", "tournament", "fifa", "world", "cup",
    "game", "games", "generation", "legend", "legends",
}

ENTITY_NOISE = {
    "But", "And", "This", "That", "These", "Those", "When", "While",
    "Because", "Next", "Finally", "First", "Second", "Third", "Fourth", "Fifth",
    "Top", "Number", "Scene", "Video", "Imagine", "Think", "Watch", "Look",
    "He", "She", "His", "Her", "Hers", "Their", "Theirs", "They", "Him",
    "Starting", "Moving", "Transitioning", "Shifting", "Every",
    # Sentence-opening/narrative words are not named entities. Without this
    # guard a timeout fallback turns rows such as “Before fame…” into a
    # container named “Before”, contaminating every provider query.
    "Before", "After", "Later", "Today", "Now", "Meanwhile", "Once",
    "Then", "Here", "There", "Inside", "Across", "During", "Despite",
}

SCENE_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "into", "about",
    "they", "them", "their", "your", "you", "was", "were", "has", "have",
    "had", "will", "would", "could", "should", "when", "while", "because",
    "just", "still", "more", "most", "very", "every", "next", "story",
    "scene", "video", "clip", "clips", "thing", "things", "became", "become",
    "made", "make", "like", "part", "year", "years",
}

WORD_TO_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}

# A listicle marker is also allowed to be written in words.  The planner used
# to recognise only ``top 5``/``number 5`` in a few paths; a perfectly normal
# script such as ``top five footballers`` then looked like a single-topic
# documentary and its named players were flattened into the global anchor.
LISTICLE_HINT_RE = re.compile(
    rf"\b(?:top|best|greatest|worst)\s*(?:\d{{1,2}}|{RANK_WORDS_RE})\b"
    rf"|\b(?:number|no\.?|ranked|ranking)\s*(?:\d{{1,2}}|{RANK_WORDS_RE})\b"
    rf"|\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+(?:place|spot|is)\b",
    re.IGNORECASE,
)


def _clean_text(value, max_words=9) -> str:
    text = re.sub(r"[^A-Za-z0-9\s\-']", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return " ".join(text.split()[:max_words])


def _extract_proper_noun(text: str) -> str:
    """Extract the most significant proper noun / named entity from text."""
    candidates = []
    for match in re.finditer(
        r"\b((?:The\s+)?[A-Z][A-Za-z0-9'’-]*(?:\s+(?:of|the|and|de|da|dos|&|[A-Z][A-Za-z0-9'’-]*)){0,4})\b",
        str(text or ""),
    ):
        candidate = _clean_text(match.group(1), max_words=6)
        words = candidate.split()
        while words and words[0] in ENTITY_NOISE:
            words.pop(0)
        candidate = " ".join(words).strip()
        if not candidate:
            continue
        # Must have at least one meaningful word
        meaningful = [w for w in re.findall(r"[a-z0-9][a-z0-9'-]*", candidate.lower())
                      if len(w) >= 3 and w not in SCENE_STOP]
        if not meaningful:
            continue
        candidates.append(candidate)
    if not candidates:
        return ""
    # Prefer longer, more specific names
    candidates.sort(key=lambda v: (len(v.split()), len(v)), reverse=True)
    return candidates[0]


def _subject_key(subject: str) -> str:
    tokens = [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9'-]*", _clean_text(subject, max_words=8).lower())
        if token not in SCENE_STOP and token not in GENERIC_NOUNS
    ]
    return " ".join(tokens[-3:])


def _subject_slug(subject: str) -> str:
    key = _subject_key(subject) or _clean_text(subject, max_words=5).lower()
    slug = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    return slug or "subject"


def _is_valid_semantic_subject(subject: str) -> bool:
    cleaned = _clean_text(subject, max_words=6)
    if not cleaned:
        return False
    if cleaned in ENTITY_NOISE or cleaned.lower() in GENERIC_NOUNS:
        return False
    meaningful = [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9'-]*", cleaned.lower())
        if len(token) >= 3 and token not in SCENE_STOP and token not in GENERIC_NOUNS
    ]
    if not meaningful:
        return False
    if cleaned.lower().startswith("the ") and len(meaningful) == 1:
        return False
    # One-word famous/person/product anchors are valid, but generic capitalized
    # narration starts such as "Every" or "Moving" are not.
    if len(meaningful) == 1 and meaningful[0] in {
        "video", "scene", "story", "generation", "players", "football",
        "soccer", "world", "cup", "sport", "sports", "number",
    }:
        return False
    return True


def _semantic_scene_subject(text: str) -> str:
    subject = _extract_proper_noun(text)
    return subject if _is_valid_semantic_subject(subject) else ""


def _repair_with_semantic_entity_containers(
    rows: List[Dict[str, any]],
    scene_texts: List[str],
    topic: str = "",
) -> List[Dict[str, any]]:
    """
    Listicle markers are useful, but many scripts shift subjects paragraph by
    paragraph without "Number X" labels. This pass treats strong named entities
    as hard low-level containers and propagates them until the next entity.
    """
    if not rows:
        return rows

    subjects = [_semantic_scene_subject(text) for text in scene_texts or []]

    # A non-listicle documentary has one authoritative topic. Pronoun-led
    # beats often contain incidental proper-looking words (WWE, Hollywood,
    # Before, etc.); they are facets, not new retrieval containers. Only split
    # semantic entities when the topic is generic/ambiguous or explicit
    # numbered boundaries establish a real listicle.
    try:
        from services.container_guard import (
            canonical_container_subject,
            is_weak_container_subject,
            text_mentions_subject,
        )
        topic_anchor = canonical_container_subject(topic, "", topic) if topic else ""
        explicit_boundaries = any(bool(row.get("has_explicit_marker")) for row in rows)
        # Do not let a global topic mention suppress entity containers when the
        # script is explicitly a ranking/listicle.  The topic often appears in
        # one beat (e.g. “Ronaldo”) while the remaining beats are pronoun-led
        # and belong to different named players.
        listicle_hint = bool(
            LISTICLE_HINT_RE.search(" ".join(str(text or "") for text in scene_texts or []))
        )
        if (
            topic_anchor
            and not is_weak_container_subject(topic_anchor)
            and not explicit_boundaries
            and not listicle_hint
            and any(text_mentions_subject(text, topic_anchor) for text in scene_texts or [])
        ):
            return rows
    except Exception:
        pass
    distinct_subject_keys = {
        _subject_key(subject)
        for subject in subjects
        if subject and _subject_key(subject)
    }
    if len(distinct_subject_keys) < 2:
        return rows

    current_subject = ""
    current_id = ""
    repaired: List[Dict[str, any]] = []
    for index, original in enumerate(rows):
        row = dict(original or {})
        subject = subjects[index] if index < len(subjects) else ""
        if subject:
            try:
                from services.container_guard import canonical_container_subject
                subject = canonical_container_subject(subject, scene_texts[index] if index < len(scene_texts or []) else "", topic)
            except Exception:
                pass
        has_marker = bool(row.get("has_explicit_marker"))

        if subject:
            current_subject = subject
            current_id = f"entity_{_subject_slug(subject)}"

        if current_subject and not has_marker:
            existing_subject = str(row.get("container_subject") or "").strip()
            existing_key = _subject_key(existing_subject)
            current_key = _subject_key(current_subject)
            if not existing_key or existing_key != current_key:
                row.update({
                    "container_id": current_id,
                    "container_subject": current_subject,
                    "marker_number": 0,
                    "has_explicit_marker": False,
                    "is_intro": False,
                    "semantic_container": True,
                })
        repaired.append(row)

    return repaired


def _extract_subject_after_marker(text: str, marker_end: int) -> str:
    """Extract list-item subject from text after a numbered marker."""
    tail = text[marker_end:].strip()
    if not tail:
        return ""
    tail = re.sub(r"^[.:,;\-–—)\]]+\s*", "", tail).strip()

    # Strategy 1: Noun phrase before main verb or punctuation (e.g. "Fresh spinach is loaded...")
    clause_split = re.split(
        r"\s+(?:is|are|was|were|contains?|provides?|boosts?|helps?|features?|delivers?|stands?|has|have|ranked)\b|[.,:;–—\-]",
        tail,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    if clause_split and clause_split[0].strip():
        lead_phrase = clause_split[0].strip()
        words = lead_phrase.split()
        if 1 <= len(words) <= 5:
            cleaned = re.sub(r"^(?:The|A|An)\s+", "", lead_phrase, flags=re.IGNORECASE).strip()
            if cleaned and cleaned.lower() not in GENERIC_NOUNS and len(cleaned) >= 3:
                return cleaned

    # Strategy 2: Proper noun phrase with title casing
    proper = re.match(
        r"^((?:The\s+)?[A-Z][A-Za-z0-9'’]+(?:\s+(?:of|the|and|de|da|dos|&|[A-Za-z0-9'’]+)){0,3})\b",
        tail,
    )
    if proper:
        candidate = proper.group(1).strip()
        if candidate.lower() not in GENERIC_NOUNS:
            cleaned = re.sub(r"^(?:The|A|An)\s+", "", candidate).strip()
            if len(cleaned) >= 2:
                return cleaned

    # Strategy 3: First significant capitalized word
    words = re.findall(r"[A-Za-z]{3,}", tail[:200])
    for w in words:
        if w.lower() not in GENERIC_NOUNS and w[0].isupper():
            return w

    return ""



def detect_list_sections(scene_texts: List[str], topic: str = "") -> List[Dict[str, any]]:
    """
    Preemptive numbered-section detection.
    Returns list of section markers, one per scene.
    """
    n = len(scene_texts or [])
    if n < 3:
        rows = [
            {"container_id": "intro", "container_subject": topic, "marker_number": 0, "has_explicit_marker": False, "is_intro": True}
            for _ in range(n)
        ]
        try:
            from services.container_guard import canonicalize_sections
            rows = canonicalize_sections(rows, scene_texts=scene_texts, topic=topic)
        except Exception:
            pass
        return _repair_with_semantic_entity_containers(rows, scene_texts, topic)

    markers: Dict[int, dict] = {}
    for i, scene_text in enumerate(scene_texts):
        if not scene_text:
            continue
        best_match = None
        best_number = 0
        for pattern in SECTION_MARKER_PATTERNS:
            match = pattern.search(scene_text)
            if not match:
                continue
            number_str = match.group(1).lower()
            number = WORD_TO_NUM.get(number_str) or (int(number_str) if number_str.isdigit() else 0)
            if number < 1 or number > 100:
                continue
            if number > best_number:
                best_number = number
                best_match = match
        if best_match and best_number > 0:
            subject = _extract_subject_after_marker(scene_text, best_match.end())
            if subject:
                try:
                    from services.container_guard import canonical_container_subject
                    subject = canonical_container_subject(subject, scene_text, topic)
                except Exception:
                    pass
            if subject:
                markers[i] = {"container_subject": subject, "marker_number": best_number, "has_explicit_marker": True}

    # Propagate subjects forward
    marker_indices = sorted(markers.keys())
    first_marker = marker_indices[0] if marker_indices else -1
    last_marker = marker_indices[-1] if marker_indices else -1
    current_container = -1
    current_subject = ""
    sorted_markers = sorted(markers.items(), key=lambda x: x[0])
    marker_iter = iter(sorted_markers)
    next_marker = next(marker_iter, None)

    # Detect outro transition: scenes after the last marked section that 
    # contain "choosing", "stars like", "other", "conclusion", "these X stand"
    _OUTRO_PATTERNS = [
        re.compile(r"\b(?:choosing|picking|selecting)\s+(?:only|just)\b", re.IGNORECASE),
        re.compile(r"\b(?:stars|players)\s+(?:like|such\s+as|including)\b", re.IGNORECASE),
        re.compile(r"\b(?:these|those)\s+\w+\s+(?:stand|remain|define)\b", re.IGNORECASE),
        re.compile(r"\b(?:but|however)\s+(?:based\s+on|these)\b", re.IGNORECASE),
    ]
    outro_start = None
    for i in range(n):
        if i > last_marker and last_marker >= 0:
            if any(p.search(scene_texts[i]) for p in _OUTRO_PATTERNS):
                outro_start = i
                break

    result = []
    for i in range(n):
        if outro_start is not None and i >= outro_start:
            # Outro scenes: fall back to using the topic as subject
            result.append({
                "index": i,
                "container_id": "outro",
                "container_subject": topic or "",
                "marker_number": 0,
                "has_explicit_marker": False,
                "is_intro": False,
            })
        elif next_marker and i == next_marker[0]:
            info = next_marker[1]
            current_container = info["marker_number"]
            current_subject = info["container_subject"].strip()
            # Clean trailing noise words from subject
            noise_suffixes = {"the", "a", "an", "is", "and", "or", "of", "in", "on", "at", "to", "for"}
            words = current_subject.split()
            while words and words[-1].lower() in noise_suffixes:
                words.pop()
            current_subject = " ".join(words)
            result.append({
                "index": i,
                "container_id": f"section_{current_container}",
                "container_subject": current_subject,
                "marker_number": current_container,
                "has_explicit_marker": True,
                "is_intro": False,
            })
            next_marker = next(marker_iter, None)
        elif current_container > 0:
            result.append({
                "index": i,
                "container_id": f"section_{current_container}",
                "container_subject": current_subject,
                "marker_number": current_container,
                "has_explicit_marker": False,
                "is_intro": False,
            })
        else:
            is_intro = first_marker < 0 or i < first_marker
            result.append({
                "index": i,
                "container_id": "intro" if is_intro else f"section_unknown",
                "container_subject": topic or "",
                "marker_number": 0 if is_intro else -1,
                "has_explicit_marker": False,
                "is_intro": is_intro,
            })
    try:
        from services.container_guard import canonicalize_sections
        result = canonicalize_sections(result, scene_texts=scene_texts, topic=topic)
    except Exception:
        pass
    return _repair_with_semantic_entity_containers(result, scene_texts, topic)


# ── LLM Query Agent ──

AGENT_SYSTEM_PROMPT = """You are Vidzeo Semantic Query Agent — a specialized system for generating 
YouTube search queries and web image search queries for video scene retrieval.

Your job: given a video topic, scene texts, and per-scene section subjects, 
generate the BEST 3 search queries per scene (primary, supporting, fallback).

CORE RULES:
1. EVERY query for a section scene MUST LEAD with the section subject as the FIRST word.
   - Banana section: "banana fruit tree real footage" NOT "fruits health banana"
   - Cristiano Ronaldo section: "Cristiano Ronaldo dribbling skills" NOT "football legend highlights"
   
2. Section subjects are HARD CONTAINERS. Never mix subjects across sections.
   - Wrong: apple section getting "banana health" query

3. INTRO scenes (before first marker): use the video's domain topic, not a section subject.
   - "football legends all time greatest" NOT a specific player name

4. Use proper FULL names — never fragments. "Kylian Mbappé" not "Kylian".
   "Jude Bellingham" not "Bellingham". "Cristiano Ronaldo" not "Ronaldo" alone.

5. Queries must be search-engine optimized: 3-6 concrete words, visual/searchable terms.
   - Good: "Cristiano Ronaldo bicycle kick goal real footage"
   - Bad: "the incredible legacy of one of football's greatest players ever"

6. YouTube prefers: "real footage", "highlights", "skills", "goals", "match", "interview"
   Web images prefer: "portrait", "action", "stadium", "celebration", "training"

7. NO abstract/conceptual terms. Translate to visual:
   - "legacy" → "trophy", "celebration", "career highlights"
   - "talent" → "skills", "dribbling", "goal"
   - "dominance" → "winning", "champion", "lifting trophy"

8. Three query tiers per scene:
   - PRIMARY: Most specific, contains section subject + 2-3 visual terms
   - SUPPORTING: Slightly broader, good for discovery
   - FALLBACK: Broadest, ensures at least some results

Output ONLY valid JSON array with this exact structure per scene:
{"primary": "...", "supporting": "...", "fallback": "..."}"""


def build_agent_user_prompt(
    topic: str,
    scene_texts: List[str],
    section_subjects: List[str],
    is_listicle: bool = False,
) -> str:
    """Build the user prompt for the semantic query agent."""
    scene_blocks = []
    for i, text in enumerate(scene_texts):
        section = section_subjects[i] if i < len(section_subjects) else ""
        section_label = f" | SECTION SUBJECT: {section}" if section else " | INTRO (no section)"
        scene_blocks.append(f"Scene {i+1}: {text[:200]}{section_label}")

    listicle_header = ""
    if is_listicle:
        distinct = list(dict.fromkeys(s for s in section_subjects if s))
        if len(distinct) >= 2:
            listicle_header = f"""
LISTICLE FORMAT DETECTED — {len(distinct)} distinct sections: {', '.join(distinct)}
Each scene belongs to ONE section. Queries MUST use the correct section subject as first word.
DO NOT cross-contaminate. Banana scenes get banana queries, apple scenes get apple queries.
"""

    return f"""Video Topic: {topic}
{listicle_header}
Scenes:
{chr(10).join(scene_blocks)}

Generate 3 search queries per scene. Remember:
- Section scenes: section subject MUST be first word
- Intro scenes: use domain topic
- Use proper full names (Kylian Mbappé not Kylian)
- 3-6 concrete visual words per query
- Primary = exact narrated action/place/era; Supporting = another supported angle/detail; Fallback = broader recall retaining the subject
- Vary neighboring beats: do not return the same generic topic query or portrait for each scene
- Seek distinct photographs and authentic event footage. Small source labels, captions and logos alone are not a reason to discard relevant footage

Return JSON array with one object per scene:
[{{"primary": "...", "supporting": "...", "fallback": "..."}}, ...]"""


def parse_agent_response(raw: str) -> Optional[List[Dict]]:
    """Parse LLM agent output, handling malformed JSON."""
    if not raw:
        return None
    raw = raw.strip()
    # Try direct parse
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass
    # Try extracting JSON array
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    # Try extracting individual objects
    objects = re.findall(r'\{[^{}]*\}', raw)
    if objects:
        try:
            return [json.loads(obj) for obj in objects]
        except json.JSONDecodeError:
            pass
    return None


def _query_tokens(value: str) -> List[str]:
    return [token.lower() for token in re.findall(r"[a-z0-9][a-z0-9'’-]*", str(value or ""))]


def _starts_with_phrase(value: str, phrase: str) -> bool:
    value_tokens = _query_tokens(value)
    phrase_tokens = _query_tokens(phrase)
    return bool(phrase_tokens) and value_tokens[:len(phrase_tokens)] == phrase_tokens


def _query_contract(value: str, section: str, topic_anchor: str, removed: List[str]) -> Dict[str, object]:
    tokens = _query_tokens(value)
    concrete = [token for token in tokens if token not in {"the", "a", "an", "and", "for", "with", "of", "to"}]
    return {
        "subject": section or topic_anchor or "",
        "anchored": _starts_with_phrase(value, section) if section else bool(tokens),
        "word_count": len(tokens),
        "concrete_terms": concrete[:6],
        "removed_domain_terms": list(dict.fromkeys(removed)),
        "quality": "pass" if 3 <= len(tokens) <= 7 and concrete else "weak",
    }


def normalize_agent_queries(
    queries: List[Dict],
    scene_count: int,
    section_subjects: List[str],
    topic_anchor: str = "",
) -> List[Dict]:
    """Normalize agent queries without cross-section anchor contamination."""
    result = []
    domain_tokens = _query_tokens(topic_anchor)

    for i in range(scene_count):
        source = queries[i] if i < len(queries) and isinstance(queries[i], dict) else {}
        row = {
            "primary": str(source.get("primary") or source.get("query") or "").strip(),
            "supporting": str(source.get("supporting") or source.get("alt") or "").strip(),
            "fallback": str(source.get("fallback") or source.get("fallback_query") or "").strip(),
        }
        section = " ".join(str(section_subjects[i] if i < len(section_subjects) else "").split())
        section_tokens = set(_query_tokens(section))
        audits = {}

        for qkey in ("primary", "supporting", "fallback"):
            original = row[qkey]
            if not original:
                audits[qkey] = _query_contract("", section, topic_anchor, [])
                continue
            tokens = original.split()
            removed: List[str] = []
            if section:
                # Remove a leading article and any existing subject phrase before
                # rebuilding the query. This avoids duplicated anchors such as
                # "Kylian Mbappe Kylian Mbappe ...".
                if tokens and tokens[0].lower() in {"the", "a", "an"}:
                    tokens = tokens[1:]
                rebuilt = " ".join(tokens)
                if _starts_with_phrase(rebuilt, section):
                    section_len = len(_query_tokens(section))
                    tokens = rebuilt.split()[section_len:]
                # A conflicting global topic is noise inside a hard section
                # container. Remove only its tokens that are not part of the
                # section subject, preserving visual terms and full names.
                filtered = []
                for token in tokens:
                    normalized = _query_tokens(token)
                    token_key = normalized[0] if normalized else ""
                    if token_key in domain_tokens and token_key not in section_tokens:
                        removed.append(token)
                        continue
                    filtered.append(token)
                tokens = filtered
                row[qkey] = " ".join([section, *tokens])
            else:
                row[qkey] = " ".join(tokens)
            row[qkey] = " ".join(row[qkey].split()[:7])
            audits[qkey] = _query_contract(row[qkey], section, topic_anchor, removed)

        row["section_subject"] = section
        row["topic_anchor"] = topic_anchor
        row["query_contract"] = audits
        result.append(row)

    return result
