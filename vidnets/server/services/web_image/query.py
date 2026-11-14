"""Web image query generation — scene-aware, LLM + mechanical fallback.

Architecture:
  1. Extract full subject name (prefer multi-word like "Virat Kohli")
  2. Extract scene context: years, opponents, match types, venues, proper nouns
  3. Classify scene type (person, sports, nature, city) with sport sub-classification
  4. Build SPECIFIC queries: "Virat Kohli vs Australia 2016 T20" not "Virat Kohli batting"
  5. Always include bare subject ("Virat Kohli") as guaranteed fallback
  6. LLM override when available; mechanical fallback when not
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from ..job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Set, Tuple

from .constants import BAD_QUERY_TERMS, STOPWORDS, VISUAL_ENRICHMENTS, NON_CRICKET_SPORTS_TERMS, GENERIC_TOPIC_WORDS
from .runtime import _web_log


def _expand_full_named_subject(value: str, scene_text: str = "", context_text: str = "") -> str:
    """Expand known short-name aliases before a query reaches a provider.

    Search engines treat a bare surname or first name as a broad topic.  The
    media pipeline already has a small, curated identity table in
    ``container_guard``; re-use that table here so the query planner and the
    candidate isolation layer agree on the same fully-qualified subject.
    This intentionally does not infer arbitrary capitalized nouns.
    """
    raw = str(value or "").strip()
    if not raw:
        return raw
    try:
        from services.container_guard import KNOWN_SUBJECT_ALIASES
    except Exception:
        KNOWN_SUBJECT_ALIASES = ()
    result = raw
    # ``Harry`` is a valid football alias in the shared sports table, but it
    # must never turn a royal-family scene into Harry Kane. The royal query
    # contract supplies the disambiguating context here, so keep the alias
    # untouched whenever that context is present.
    alias_context = " ".join((raw, str(scene_text or ""), str(context_text or ""))).casefold()
    royal_harry_context = bool(re.search(
        r"\b(?:prince\s+harry|duke\s+of\s+sussex|royal\s+family|british\s+royal|windsor|king\s+charles)\b",
        alias_context,
    ))
    for full_name, aliases in KNOWN_SUBJECT_ALIASES:
        # Replace an alias found in the actual query/anchor first. Context is
        # intentionally not used to switch a scene to a different identity.
        if full_name.casefold() == "harry kane" and royal_harry_context:
            continue
        matching_alias = next(
            (alias for alias in sorted(aliases, key=len, reverse=True)
             if re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", raw, re.IGNORECASE)),
            None,
        )
        if matching_alias:
            result = re.sub(
                rf"(?<![A-Za-z0-9]){re.escape(matching_alias)}(?![A-Za-z0-9])",
                full_name,
                result,
                flags=re.IGNORECASE,
            )
            continue
        # Context is deliberately not allowed to replace the subject with a
        # different identity. A two-person story must keep each scene's own
        # alias instead of inheriting the last name mentioned in the title.
    for full_name, aliases in KNOWN_SUBJECT_ALIASES:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(full_name)}(?![A-Za-z0-9])", result, re.IGNORECASE):
            for alias in sorted(aliases, key=len, reverse=True):
                if alias.lower() == full_name.lower():
                    continue
                if alias.lower() in {part.lower() for part in full_name.split()}:
                    result = re.sub(
                        rf"(?<![A-Za-z0-9])({re.escape(full_name)})(?:\s+{re.escape(alias)})+(?![A-Za-z0-9])",
                        r"\1",
                        result,
                        flags=re.IGNORECASE,
                    )
                    continue
                result = re.sub(
                    rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])",
                    "",
                    result,
                    flags=re.IGNORECASE,
                )
    return re.sub(r"\s+", " ", result).strip()


def _expand_full_names_in_query(query: str, scene_text: str = "", context_text: str = "") -> str:
    """Apply the curated full-name contract to every generated query."""
    return _expand_full_named_subject(query, scene_text, context_text)


# =============================================================================
#  SUBJECT EXTRACTION
# =============================================================================

# Royal-family names are unusually collision-prone in image search.  A bare
# ``Harry`` can resolve to Harry Styles, Harry Potter, a band, or an unrelated
# local person; ``Charles`` is similarly ambiguous.  Keep this detector
# deliberately narrow: it only activates when the script supplies royal
# context or names both Charles and Harry together.
_ROYAL_CONTEXT_TERMS = (
    "royal family", "british royal", "british monarchy", "the monarchy",
    "windsor", "duke of sussex", "prince harry", "king charles",
    "charles iii", "prince of wales", "buckingham palace", "royal duties",
)


def _detect_royal_family_anchor(title: str = "", script: str = "") -> str:
    """Return a canonical royal-family anchor when the story is about Charles/Harry.

    This is shared by title extraction, mechanical/LLM query repair, and the
    app topic-anchor resolver so every branch uses the same identity contract.
    """
    text = re.sub(r"\s+", " ", f"{title or ''} {script or ''}").strip().lower()
    if not text:
        return ""
    has_charles = bool(re.search(r"\bcharles(?:\s+iii)?\b|\bking\s+charles\b|\bprince\s+of\s+wales\b", text))
    has_harry = bool(re.search(r"\bharry\b|\bduke\s+of\s+sussex\b", text))
    has_royal_context = any(term in text for term in _ROYAL_CONTEXT_TERMS)
    # A Prince Charles story is an explicit single-subject biography.  Do not
    # broaden it to the Charles/Harry family bundle merely because the script
    # mentions royal context ("monarchy", "Prince of Wales", etc.).  That
    # broad anchor is only useful for genuinely comparative royal-family
    # stories and is a frequent source of unrelated Harry/celebrity results.
    if re.search(r"\bprince\s+charles\b", text) and not has_harry:
        return ""
    if not ((has_charles and has_harry) or (has_royal_context and (has_charles or has_harry))):
        return ""
    if has_charles and has_harry:
        return "King Charles III and Prince Harry British Royal Family"
    # Family context cannot introduce a second, unmentioned protagonist.
    return "British Royal Family"


def _royal_scene_subject(scene_text: str, royal_anchor: str) -> str:
    """Choose exact, search-safe royal entities in their narrated order.

    First names and surnames are unsafe search subjects in this niche.  Keep
    the mapping deliberately explicit and return at most two entities so a
    comparison scene remains accurate without becoming an overlong query.
    """
    text = re.sub(r"\b(?:St\.?|Saint)\s+[A-Za-z'’-]+\s+(?:Chapel|Church|Cathedral)\b", "", str(scene_text or ""), flags=re.I)
    identities = (
        ("Queen Elizabeth II", r"\b(?:queen\s+elizabeth\s+ii|elizabeth\s+ii|elizabeth\s+the\s+second)\b"),
        ("King Charles III", r"\b(?:king\s+charles(?:\s+iii)?|charles\s+iii|charles)\b"),
        ("Prince Harry", r"\b(?:prince\s+harry|duke\s+of\s+sussex|harry)\b"),
        ("Meghan Markle", r"\b(?:meghan\s+markle|duchess\s+of\s+sussex|meghan)\b"),
        ("Prince William", r"\b(?:prince\s+william|william,?\s+prince\s+of\s+wales|william)\b"),
        ("Kate Middleton", r"\b(?:kate\s+middleton|catherine,?\s+princess\s+of\s+wales|princess\s+catherine|kate)\b"),
        ("Prince Archie", r"\b(?:prince\s+archie|archie)\b"),
        ("Princess Lilibet", r"\b(?:princess\s+lilibet|lilibet)\b"),
        ("Prince George", r"\b(?:prince\s+george|george)\b"),
        ("Princess Charlotte", r"\b(?:princess\s+charlotte|charlotte)\b"),
        ("Prince Louis", r"\b(?:prince\s+louis|louis)\b"),
        ("Princess Eugenie", r"\b(?:princess\s+eugenie|eugenie)\b"),
        ("Jack Brooksbank", r"\b(?:jack\s+brooksbank|brooksbank)\b"),
    )
    matches: List[Tuple[int, str]] = []
    for full_name, pattern in identities:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            matches.append((match.start(), full_name))
    matches.sort(key=lambda item: item[0])
    names = list(dict.fromkeys(name for _offset, name in matches))[:2]
    return " and ".join(names) if names else ("British Royal Family" if royal_anchor else "")


def _enforce_royal_query_contract(rows: List[Dict], title: str, script: str,
                                   scene_texts: List[str]) -> List[Dict]:
    """Repair LLM/mechanical rows so royal queries cannot fall back to bare names."""
    royal_anchor = _detect_royal_family_anchor(title, script)
    if not royal_anchor:
        return rows
    repaired_rows: List[Dict] = []
    for idx, original in enumerate(rows or []):
        row = dict(original or {})
        scene = scene_texts[idx] if idx < len(scene_texts or []) else ""
        subject = _royal_scene_subject(scene, royal_anchor)
        # Resolve short pronoun-led follow-up scenes against the immediately
        # preceding named royal. This keeps ``He walks through the garden``
        # tied to Prince Harry instead of widening it to an anonymous family
        # query, while still allowing a genuinely new named scene to switch.
        if (
            subject == "British Royal Family"
            and idx > 0
            and re.search(r"\b(?:he|she|they|his|her|their)\b", scene, re.IGNORECASE)
        ):
            previous_subject = str(repaired_rows[-1].get("scene_subject") or "").strip()
            if previous_subject and previous_subject != "British Royal Family":
                subject = previous_subject
        if not subject:
            subject = "British Royal Family"
        # Royal names are highly ambiguous in general image indexes. Keep a
        # subject-specific disambiguator on every provider query so Prince
        # Harry cannot drift to the musician and King Charles cannot drift to
        # a generic town crier. The live vision gate still decides acceptance.
        royal_disambiguator = {
            "Prince Harry": "Duke of Sussex British royal",
            "Meghan Markle": "Duchess of Sussex British royal",
            "King Charles III": "British monarch official",
            "Prince William": "Prince of Wales British royal",
            "Kate Middleton": "Princess of Wales British royal",
            "Prince Archie": "British royal family",
            "Princess Lilibet": "British royal family",
            "Prince George": "British royal family",
            "Princess Charlotte": "British royal family",
            "Prince Louis": "British royal family",
            "Princess Eugenie": "British royal family",
            "Jack Brooksbank": "British royal family",
        }.get(subject, "British royal family")
        if "Prince Harry" in subject and "Meghan Markle" in subject:
            royal_disambiguator = "Duke of Sussex Duchess of Sussex British royal"
        elif "Prince Harry" in subject:
            royal_disambiguator = "Duke of Sussex British royal"
        elif "Meghan Markle" in subject:
            royal_disambiguator = "Duchess of Sussex British royal"
        row["topic_anchor"] = royal_anchor
        row["subject_query"] = subject
        row["section_subject"] = subject
        row["scene_subject"] = subject
        raw: List[str] = []
        for key in ("query_variants", "specific", "medium", "general", "fallback_1", "fallback_2",
                    "search_query", "supporting_search_query", "alt_search_query", "youtube_query",
                    "youtube_supporting_query", "youtube_fallback_query"):
            value = row.get(key)
            if isinstance(value, list):
                raw.extend(str(item) for item in value if str(item or "").strip())
            elif value:
                raw.append(str(value))
        queries: List[str] = []
        seen: Set[str] = set()
        for value in raw:
            cleaned = _clean_query(value)
            if not cleaned:
                continue
            if re.search(r"\b(?:band|concert|album|music|singer|guitarist|drummer|tour|stage)\b", cleaned, re.IGNORECASE):
                continue
            # Remove any pre-existing identity phrase, then add the exact
            # scene subject once. This prevents residual constructions such
            # as ``Prince Harry Prince interview`` after short-name repair.
            for full_name in (
                "King Charles III", "King Charles", "Prince Harry", "Meghan Markle",
                "Prince William", "Kate Middleton", "Prince Archie", "Princess Lilibet",
                "Prince George", "Princess Charlotte", "Prince Louis",
                "Princess Eugenie", "Jack Brooksbank", "British Royal Family",
            ):
                cleaned = re.sub(rf"\b{re.escape(full_name)}\b", "", cleaned, flags=re.IGNORECASE)
            for alias in ("harry", "charles", "william", "meghan", "kate", "archie", "lilibet", "george", "charlotte", "louis", "eugenie", "brooksbank"):
                # Remove possessive/plain-text variants too (``Harrys`` is a
                # common transcript artifact after punctuation is stripped).
                cleaned = re.sub(rf"\b{alias}(?:['’]?s)?\b", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\b(?:prince|princess|king|duke|duchess)\b", "", cleaned, flags=re.IGNORECASE)
            if royal_anchor:
                # Mechanical scene keywords can leak sports/entertainment
                # terms into a royal-family row (for example ``batting``).
                cleaned = re.sub(
                    r"\b(?:batting|bowling|cricket|football|soccer|stadium|match|album|concert|singer|music|band|stage|guitarist|drummer)\b",
                    "",
                    cleaned,
                    flags=re.IGNORECASE,
                )
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if subject.lower() not in cleaned.lower():
                cleaned = f"{subject} {cleaned}".strip()
            if royal_disambiguator.casefold() not in cleaned.casefold():
                cleaned = f"{cleaned} {royal_disambiguator}".strip()
            cleaned = _clean_query(cleaned)
            key = cleaned.lower()
            if cleaned and key not in seen:
                seen.add(key)
                queries.append(cleaned)
        # Stable, concrete fallbacks; never a bare ``Harry``/``Charles`` query.
        for fallback in (
            f"{subject} portrait {royal_disambiguator}",
            f"{subject} royal ceremony {royal_disambiguator}",
            f"{subject} official event {royal_disambiguator}",
        ):
            cleaned = _clean_query(fallback)
            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                queries.append(cleaned)
        queries = queries[:5]
        row["query_variants"] = queries
        row["search_query"] = queries[0] if queries else f"{subject} portrait"
        row["supporting_search_query"] = queries[1] if len(queries) > 1 else row["search_query"]
        row["alt_search_query"] = queries[2] if len(queries) > 2 else row["search_query"]
        # Keep the web contract's royal-family qualifier, but send YouTube a
        # short positive footage phrase.  The previous code mirrored the web
        # query pack verbatim, so a sentence-shaped plan (and later exclusion
        # operators) leaked into the motion search box.
        try:
            from services.youtube_query_normalizer import normalize_youtube_queries
            yt_queries = normalize_youtube_queries(
                queries,
                anchor=subject,
                category="person",
                fallback="archival event footage",
            )
        except Exception:
            yt_queries = queries
        row["youtube_query"] = yt_queries[0] if yt_queries else f"{subject} archival footage"
        row["youtube_supporting_query"] = yt_queries[1] if len(yt_queries) > 1 else row["youtube_query"]
        row["youtube_fallback_query"] = yt_queries[2] if len(yt_queries) > 2 else row["youtube_query"]
        row["specific"] = row["search_query"]
        row["medium"] = row["supporting_search_query"]
        row["general"] = row["alt_search_query"]
        repaired_rows.append(row)
    return repaired_rows

def _derive_effective_web_title(title: str, script: str) -> Tuple[str, List[str]]:
    """Extract a meaningful web search title from the video title + script."""
    clean_title = title.strip().strip("'\"").strip()
    royal_anchor = _detect_royal_family_anchor(clean_title, script)
    if royal_anchor:
        return royal_anchor, re.findall(r"[a-z0-9]{3,}", royal_anchor.lower())
    # Clean possessives: "Kohli's" → "Kohli", "Kohlis" → "Kohli"
    clean_title = re.sub(r"(\b[A-Z][a-z]+)'s\b", r'\1', clean_title)
    clean_title = re.split(
        r"\s*,\s*(?:born|birth|aged?|who\s+was|who\s+is|known\s+for)\b",
        clean_title,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" ,.;:-")
    title_subject = _extract_main_subject(clean_title)
    if title_subject and len(title_subject.split()) >= 2 and len(clean_title.split()) <= 5:
        return title_subject, title_subject.lower().split()
    from services.youtube_clips_service import _is_listicle_video
    if _is_listicle_video(clean_title, script):
        words = re.findall(r'[a-zA-Z]{3,}', clean_title)
        meaningful = [w.lower() for w in words if w.lower() not in STOPWORDS][:4]
        if meaningful:
            return " ".join(meaningful), meaningful
        return clean_title, clean_title.lower().split()

    if not clean_title or clean_title.lower().startswith(("since ", "because ", "after ")):
        return _derive_from_script(script)

    # Use _extract_main_subject for proper name detection from title first
    subject = _extract_main_subject(clean_title)
    if not (subject and len(subject.split()) >= 2):
        script_subject = _extract_main_subject(script)
        if script_subject:
            if len(script_subject.split()) >= 2:
                subject = script_subject
            elif not subject:
                subject = script_subject

    if subject and len(subject) > 2:
        return subject, subject.lower().split()

    # Fallback to title keywords
    words = re.findall(r'[a-zA-Z]{3,}', clean_title)
    meaningful = [w.lower() for w in words if w.lower() not in STOPWORDS][:4]
    if meaningful:
        return " ".join(meaningful), meaningful

    return clean_title, clean_title.lower().split()


def _derive_from_script(script: str) -> Tuple[str, List[str]]:
    """Fallback: extract subject from script."""
    subject = _extract_main_subject(script)
    if subject and len(subject) > 2:
        return subject, subject.lower().split()
    words = re.findall(r'[a-zA-Z]{4,}', script[:2000])
    meaningful = [w.lower() for w in words if w.lower() not in STOPWORDS][:5]
    if meaningful:
        return " ".join(meaningful), meaningful
    return "photo", ["photo"]


# =============================================================================
#  SCENE CONTEXT EXTRACTION — years, opponents, match types, venues
# =============================================================================

_KNOWN_CRICKET_TEAMS: Set[str] = {
    "india", "australia", "england", "south africa", "new zealand",
    "pakistan", "sri lanka", "west indies", "bangladesh", "afghanistan",
    "zimbabwe", "ireland", "netherlands", "scotland", "uae",
}

_KNOWN_CRICKET_TOURNAMENTS: Set[str] = {
    "world cup", "t20 world cup", "world t20", "champions trophy",
    "asia cup", "ipl", "indian premier league", "bbl", "big bash",
    "psl", "cpl", "county championship", "sher e bangla cup",
    "ranji trophy", "birmingham test", "lords test", "the ashes",
    "border gavaskar trophy", "bgt", "icc champions trophy",
}

_KNOWN_CRICKET_VENUES: Set[str] = {
    "lords", "the oval", "edgbaston", "old trafford", "headingley",
    "trent bridge", "southampton", "wankhede", "chinnaswamy",
    "eden gardens", "chepauk", "mohali", "kotla", "rajiv gandhi",
    "narendra modi", "motera", "dubai", "sharjah", "mcg",
    "sydney cricket ground", "scg", "waca", "perth stadium",
    "adelaide oval", "gabba", "basin reserve", "hagley oval",
    "sabina park", "queens park oval", "newlands", "wanderers",
    "super sport park", "centurion", "kingsmead", "pallekele",
    "r premadasa", "sher e bangla", "zahur ahmed chowdhury",
}


# Concrete identifiers worth appending to a non-cricket scene query: vehicle /
# unit numbers ("locomotive 160", "BNSF 1555") and the scene's named place
# ("Cajon Pass"). The old non-cricket path returned at most a year, and
# _extract_visual_keywords strips all digits, so a scene about a specific
# locomotive searched as if it were about trains in general. Numbers are
# only accepted with a vehicle/operator word attached — a bare number in
# narration ("82 not out", "10 minutes") is a score or a duration, not an
# identifier, and must never become a query term.
_VEHICLE_NUMBER_RE = re.compile(
    r'\b(?:locomotive|engine|unit|train)\s+(?:no\.?\s+|#\s*)?(\d{2,4})\b', re.I)
_OPERATOR_NUMBER_RE = re.compile(
    r'\b(BNSF|Amtrak|Union Pacific|CSX|Norfolk Southern|Canadian National|'
    r'Canadian Pacific|Southern Pacific|Santa Fe|Indian Railways)\s+(\d{2,4})\b')
_HASH_NUMBER_RE = re.compile(r'#(\d{2,4})\b')
_PLACE_PHRASE_RE = re.compile(r'\b([A-Z][a-z]{3,}(?:\s+[A-Z][a-z]{3,}){1,2})\b')
_PLACE_PHRASE_SKIP_FIRST = {
    'the', 'a', 'an', 'in', 'on', 'at', 'from', 'with', 'this', 'that',
    'it', 'they', 'he', 'she', 'when', 'where', 'after', 'before',
}


def _extract_scene_identifiers(text: str, subject: str = "") -> List[str]:
    """Return up to 2 concrete identifiers from scene text, most specific first."""
    if not text:
        return []
    found: List[str] = []

    def _add(value: str) -> None:
        cleaned = re.sub(r'\s+', ' ', str(value or '').strip())
        if cleaned and cleaned.lower() not in {v.lower() for v in found}:
            found.append(cleaned)

    has_vehicle_word = bool(re.search(
        r'\b(?:locomotive|engine|unit|train|railway|railroad)\b', text, re.I))
    for m in _OPERATOR_NUMBER_RE.finditer(text):
        _add(f"{m.group(1)} {m.group(2)}")
    for m in _VEHICLE_NUMBER_RE.finditer(text):
        _add(f"locomotive {m.group(1)}")
    if has_vehicle_word:
        for m in _HASH_NUMBER_RE.finditer(text):
            _add(f"locomotive {m.group(1)}")
    if len(found) < 2:
        subject_lower = (subject or '').casefold()
        for m in _PLACE_PHRASE_RE.finditer(text):
            phrase = m.group(1)
            words = phrase.split()
            if words[0].lower() in _PLACE_PHRASE_SKIP_FIRST:
                continue
            if phrase.casefold() == subject_lower:
                continue
            if all(w.lower() in subject_lower for w in words):
                continue
            _add(phrase)
            break
    return found[:2]


def _extract_scene_context(scene_text: str, full_script: str = "",
                             subject: str = "") -> Dict[str, List[str]]:
    """Extract years, match types, potential opponents, venues from scene text + script.

    Returns qualifiers that can be appended to search queries to make them
    more specific, e.g. "Virat Kohli vs Australia 2016 World T20".
    """
    # Qualifiers must stay scene-local.  Appending the complete script here
    # caused an opponent/year from scene 2 to leak into every scene's query
    # (for example scene 1 receiving ``vs Australia`` before Australia was
    # mentioned).  A full-script fallback is safe only when the scene has no
    # text at all, where there is no local context to preserve.
    combined = str(scene_text or "").strip() or str(full_script or "").strip()
    text_lower = combined.lower()
    cricketish = _is_cricket_content(subject, combined)

    ctx: Dict[str, List[str]] = {
        "years": [],
        "match_types": [],
        "opponents": [],
        "venues": [],
        "qualifiers": [],
        "identifiers": [],
    }

    # 1. Years (broad range 1900-2099 for general content)
    years = set(re.findall(r'\b(19[0-9]{2}|20[0-9]{2})\b', combined))
    ctx["years"] = sorted(y for y in years if 1900 <= int(y) <= 2099)

    if not cricketish:
        # Non-cricket scenes used to qualify with at most a year, so a scene
        # about locomotive 160 searched as "trains in general". Concrete
        # identifiers (vehicle/unit numbers, named places) outrank the year:
        # they are the terms that separate this scene's footage from any
        # other train/snow/bridge photo. Cap stays 2 qualifiers.
        identifiers = _extract_scene_identifiers(combined, subject)
        ctx["identifiers"] = identifiers
        qualifiers = list(identifiers)
        if ctx["years"]:
            qualifiers.append(ctx["years"][-1])
        ctx["qualifiers"] = qualifiers[:2]
        return ctx

    # 2. Match types / tournaments
    for mt in _KNOWN_CRICKET_TOURNAMENTS:
        if re.search(rf"(?<![a-z0-9]){re.escape(mt)}(?![a-z0-9])", text_lower):
            ctx["match_types"].append(mt.title())

    ctx["match_types"] = list(dict.fromkeys(ctx["match_types"]))

    # 3. Opponent teams — only from "vs/against" patterns (not plain text match)
    # This avoids treating location names like "India" as opponents
    vs_matches = re.findall(r'(?:\bvs\.?\s+|v/s\s+|versus\s+|against\s+)([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)', combined)
    for vm in vs_matches:
        vm_stripped = vm.strip()
        if not vm_stripped:
            continue
        if subject and vm_stripped.lower() == subject.lower():
            continue
        ctx["opponents"].append(vm_stripped)

    # Deduplicate (preserving order)
    seen_opps: Set[str] = set()
    deduped_opps = []
    for o in ctx["opponents"]:
        ol = o.lower()
        if ol not in seen_opps:
            seen_opps.add(ol)
            deduped_opps.append(o)
    ctx["opponents"] = deduped_opps

    # 4. Venues
    for venue in _KNOWN_CRICKET_VENUES:
        if venue in text_lower:
            ctx["venues"].append(venue.title() if " " in venue else venue.capitalize())

    # 5. Build short qualifier strings (max 3 qualifiers, sorted by specificity)
    qualifiers = []
    if ctx["years"]:
        qualifiers.append(ctx["years"][-1])  # Most recent year
    if ctx["match_types"]:
        qualifiers.append(ctx["match_types"][0])
    if ctx["opponents"]:
        qualifiers.append(ctx["opponents"][0])
    if len(qualifiers) < 2 and ctx["venues"]:
        qualifiers.append(ctx["venues"][0])

    ctx["qualifiers"] = qualifiers[:2]
    return ctx


_WEAK_SINGLE_SUBJECTS = {
    "iranian", "american", "canadian", "mexican", "argentinian", "qatari",
    "algerian", "senegalese", "tunisian", "south african", "fans", "supporters",
    "crowd", "team", "country", "nation", "empty", "travel", "football", "soccer",
    "iran", "usa", "us", "canada", "mexico", "qatar", "argentina", "senegal",
    "welcome", "message", "tel", "aviv", "ivory", "coast", "cape", "verde",
    "saudi", "arabia", "algeria", "tunisia",
}

_QUERY_LEAK_TERMS = {
    "analysis", "explained", "commentary", "debate", "podcast", "reaction",
    "journalist", "reporter", "interview", "opinion", "breaking", "headline",
    "latest", "update", "updates", "news", "report", "reports", "why", "what",
    "how", "because", "means", "didn", "doesn", "nearly", "quietly", "says",
    "said", "according", "revealed", "shocking", "insane",
}

_ABSTRACT_QUERY_TERMS = {
    "crisis", "impact", "effects", "consequences", "strategy", "policy",
    "system", "future", "legacy", "scrutiny", "achievement", "achievements",
    "pressure", "tension", "momentum", "reality", "decision", "failure",
    "success", "problem", "problems", "issue", "issues", "scenario",
}

_CROSS_NICHE_LEAK_TERMS = {
    "cricket", "batting", "bowling", "batsman", "innings", "wicket", "icc",
    "ipl", "psl", "t20", "odi",
}

_QUERY_OUTPUT_GARBAGE_TERMS = {
    "specific", "medium", "general", "fallback", "fallbacks", "search", "query", "queries",
    "scene", "scenes", "depict", "depicts", "depicted", "showing", "shows", "visual",
    "narration", "voiceover", "sentence", "content", "subject", "theme", "concept",
    "illustration", "provide", "please", "recommended", "suggested", "option", "options",
    "hailed", "carried", "embarked", "arrived", "rescuing", "seeking", "including",
    "featuring", "bound", "marvel", "engineering",
}

_GENERIC_VISUAL_FALLBACKS = {
    "gaming": ["official gameplay screenshot", "official trailer still", "game environment screenshot", "official key art"],
    "history": ["old archive photo", "historic city street", "historical photograph", "old newspaper"],
    "vehicle": ["photograph", "exterior photograph", "detail photograph", "vintage photograph"],
    "finance": ["grocery price tags", "empty wallet", "cash register", "house sale sign"],
    "science": ["laboratory equipment", "microscope closeup", "research lab", "scientist hands"],
    "psychology": ["brain scan image", "person using phone", "daily routine habit", "neural pathway"],
    "technology": ["server room", "computer chip closeup", "software developer laptop", "robotics lab"],
    "disaster": ["earthquake damage", "rescue workers", "collapsed building", "emergency response"],
    "nature": ["landscape wide shot", "forest trail", "mountain valley", "ocean waves"],
    "travel": ["airport terminal", "passport documents", "city street", "train station"],
    "politics": ["government building", "press podium", "parliament chamber", "protest crowd"],
    "sports": ["stadium crowd", "team supporters", "match field", "fans flags"],
}

# Query-only entity hints.  They keep the provider phrase visually typed even
# when an upstream LLM returns a bare subject (or when a title is ambiguous).
# These are intentionally small and conservative; the downloaded pixels still
# go through the resolver's metadata/safety/vision gates.
_QUERY_PERSON_ROLES = {
    "actor", "actress", "athlete", "footballer", "cricketer", "player",
    "president", "minister", "king", "queen", "prince", "princess", "duke",
    "duchess", "monarch", "royal", "celebrity", "singer", "musician", "rapper",
    "journalist", "presenter", "streamer", "coach", "founder", "scientist",
    "author", "director", "born", "biography", "portrait", "interview",
}
_QUERY_PLACE_MARKERS = {
    "city", "town", "village", "country", "capital", "landmark", "tower", "palace",
    "castle", "bridge", "monument", "skyline", "street", "travel", "tourism",
    "destination", "airport", "beach", "mountain", "river", "lake", "island", "park",
    "museum", "harbor", "harbour", "coast", "location", "architecture", "temple",
    "church", "downtown", "waterfall", "glacier", "canyon", "mount", "mt", "everest",
    "himalayas", "valley", "desert", "falls", "volcano", "forest", "ocean",
}
_QUERY_OBJECT_MARKERS = {
    "product", "device", "phone", "iphone", "ipad", "smartphone", "laptop", "macbook", "computer", "camera", "vehicle", "vehicles",
    "car", "cars", "truck", "trucks", "train", "trains", "locomotive", "locomotives", "railway", "railroad", "automobile", "automobiles",
    "engine", "machine", "robot", "chip", "rocket", "aircraft", "ship", "ships", "vessel", "vessels", "boat", "boats", "model",
    "prototype", "software", "app", "equipment", "tool", "gadget", "food", "fruit", "recipe",
    "nutrition", "meal", "kitchen", "dashboard", "interior", "exterior",
}
_QUERY_FOOD_MARKERS = {
    "food", "fruit", "fruits", "apple", "apples", "vegetable", "vegetables", "produce", "recipe", "ingredient", "nutrition",
    "meal", "cooking", "kitchen", "cuisine", "dish", "beverage", "drink",
}


def _infer_entity_visual_kind(subject: str, scene_text: str = "", scene_type: str = "") -> str:
    """Classify a query anchor as person/place/object without an LLM call."""
    subject_text = str(subject or "").strip()
    subject_lower = subject_text.casefold()
    context_lower = f"{subject_text} {scene_text or ''}".casefold()
    subject_tokens = re.findall(r"[a-z0-9]+", subject_lower)
    name_like = len(subject_tokens) >= 2 and not any(
        token in _QUERY_OBJECT_MARKERS or token in _QUERY_PLACE_MARKERS
        for token in subject_tokens
    )
    role_hit = any(
        re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", context_lower)
        for term in _QUERY_PERSON_ROLES
    )
    # Sports scenes with a named two-word anchor are almost always a person;
    # do this before action nouns such as ``field`` or ``match`` are treated as
    # places/objects.
    if scene_type == "sports" and name_like:
        return "person"
    if role_hit and (name_like or len(subject_tokens) == 1):
        return "person"
    # History/gaming/vehicle subjects are commonly named ships, cars, trains, or games.
    # Their multi-word names must not fall through to the generic person
    # heuristic (cars/ships were previously given portrait queries).
    if scene_type in {"history", "gaming", "vehicle"}:
        return "thing"
    # Food terms are common in title/scene context (for example ``Apple
    # nutrition``).  Classify them before the generic object markers so the
    # query receives produce/ingredient vocabulary instead of product or
    # portrait wording.
    if any(token in _QUERY_FOOD_MARKERS for token in subject_tokens) or any(
        re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", context_lower)
        for term in _QUERY_FOOD_MARKERS
    ):
        return "food"
    # A typed object/place in the anchor is stronger than a coincidental verb
    # in the surrounding narration (e.g. ``Kohli trains``).
    if any(token in _QUERY_OBJECT_MARKERS for token in subject_tokens):
        return "object"
    if any(token in _QUERY_PLACE_MARKERS for token in subject_tokens):
        return "place"
    if scene_type in {"city", "nature"}:
        return "place"
    if any(
        re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", context_lower)
        for term in _QUERY_PLACE_MARKERS
    ) and not name_like:
        return "place"
    if any(
        re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", context_lower)
        for term in _QUERY_OBJECT_MARKERS
    ) and (
        not name_like
        or any(char.isdigit() for char in subject_text)
        or scene_type in {"technology", "general"}
    ):
        return "object"
    if name_like:
        return "person"
    return "thing"


def _is_person_fallback_subject(subject: str) -> bool:
    words = [w.lower() for w in re.findall(r"[A-Za-z]+", subject or "")]
    if not words or len(words) > 3:
        return False
    if " ".join(words) in _WEAK_SINGLE_SUBJECTS or words[0] in _WEAK_SINGLE_SUBJECTS:
        return False
    # A known editorial subject family is visual subject matter, not a person.
    # Keep the classifier taxonomy-driven so new engines, places, animals and
    # industries do not require another hard-coded exception list.
    if _topic_family(subject) != 'general':
        return False
    topic_words = {
        "fans", "supporters", "crowd", "team", "world", "cup", "fifa", "football",
        "soccer", "cricket", "stadium", "tickets", "ticket", "protest", "boycott",
        "war", "battle", "history", "documentary", "truck", "trucks", "city",
        "travel", "empty", "visa", "flight", "flights", "country", "nation",
        "iran", "usa", "canada", "mexico", "qatar", "argentina", "senegal",
        "welcome", "message", "tel", "aviv", "ivory", "coast", "cape", "verde",
        "saudi", "arabia", "algeria", "tunisia",
        "brain", "brains", "shortcut", "shortcuts", "habit", "habits", "dopamine",
        "psychology", "neuroscience", "cognitive", "heuristic", "heuristics",
        "technology", "software", "server", "robot", "robots", "algorithm",
        "rms", "hms", "uss", "mv", "ss", "titanic", "ship", "ships", "liner", "vessel",
    }
    return not any(w in topic_words or w in GENERIC_TOPIC_WORDS for w in words)


def _topic_family(subject: str, scene_text: str = "") -> str:
    text = f"{subject} {scene_text}".lower()

    def has_any(terms: Tuple[str, ...]) -> bool:
        return any(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) for term in terms)

    if has_any(("gta", "grand theft auto", "game", "gameplay", "gaming", "playstation", "xbox", "rockstar games", "nintendo", "steam", "tetris", "tetromino")):
        return "gaming"
    if has_any(("football", "soccer", "fifa", "world cup", "cricket", "basketball", "baseball", "tennis", "rugby", "golf", "nfl", "nba", "championship", "tournament", "athlete", "olympics")):
        return "sports"
    if has_any(("money", "market", "inflation", "price", "finance", "economy", "cash", "bank", "compounding", "returns", "deposits", "contributions")):
        return "finance"
    if has_any(("brain", "dopamine", "habit", "habits", "heuristic", "cognitive", "psychology", "neuroscience")):
        return "psychology"
    if has_any(("ai", "software", "technology", "server", "robot", "chip", "app", "algorithm", "automotive", "engine", "turbocharger", "turbine", "compressor", "exhaust", "airflow")):
        return "technology"
    if has_any(("earthquake", "quake", "seismic", "flood", "hurricane", "tsunami", "wildfire", "landslide")):
        return "disaster"
    if has_any(("science", "laboratory", "research", "space", "medical", "climate", "eclipse", "sunlight", "moon", "shadow", "satellite", "gps", "clock", "gravity", "relativity", "orbit")):
        return "science"
    if has_any(("war", "ancient", "history", "historical", "empire", "battle", "revolution", "archive", "titanic", "shipwreck", "ocean liner")):
        return "history"
    if has_any(("forest", "ocean", "mountain", "animal", "nature", "wildlife", "octopus", "octopuses", "neurons", "tentacles", "reef", "coral")):
        return "nature"
    if has_any(("travel", "airport", "visa", "passport", "flight", "city", "gates", "torii", "shrine", "temple", "kyoto")):
        return "travel"
    if has_any(("government", "minister", "election", "policy", "protest", "parliament")):
        return "politics"
    return "general"


def _topic_fallback_queries(subject: str, scene_text: str = "", limit: int = 5) -> List[str]:
    base = re.sub(r"\s+", " ", str(subject or "")).strip()
    if not base:
        return []
    queries: List[str] = []

    def _add(q: str) -> None:
        cleaned = _clean_query(q)
        if cleaned and cleaned.lower() not in {x.lower() for x in queries}:
            queries.append(cleaned)

    family = _topic_family(base, scene_text)
    if family == "history":
        for visual_term in _history_visual_terms(scene_text):
            _add(f"{base} {visual_term}")
    if family in _GENERIC_VISUAL_FALLBACKS:
        for fallback in _GENERIC_VISUAL_FALLBACKS[family]:
            _add(f"{base} {fallback}")

    kws = _extract_visual_keywords(scene_text or "", max_kws=3)
    # A scene may repeat the subject's short name ("Harry arrives..."). Do
    # not append that token after the canonical multi-word identity; doing so
    # creates malformed queries such as ``Prince Harry harry arrives`` and can
    # re-open namesake collisions in providers.
    subject_tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", base)
    }
    kws = [
        keyword for keyword in kws
        if not all(
            part.casefold() in subject_tokens
            for part in re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", str(keyword or ""))
        )
    ]
    if kws:
        _add(f"{base} {' '.join(kws[:2])}")
    _add(base)
    return queries[:limit]


def _broaden_scene_queries(
    queries: List[str],
    main_subject: str = "",
    tried: Optional[List[str]] = None,
    limit: int = 6,
) -> List[str]:
    """Progressively broaden specific queries that returned nothing.

    Drop the most specific (trailing) terms one at a time, so a query like
    "Single wheel climbing rail diagram photograph" degrades through
    "Single wheel climbing rail diagram" to "Single wheel" before the bare
    subject is tried. Generic subject queries come last. Queries already
    tried (including the originals) are never repeated.
    """
    tried_lower = {str(q or "").strip().lower() for q in (tried or [])}
    tried_lower |= {str(q or "").strip().lower() for q in (queries or [])}
    subject = str(main_subject or "").strip()
    out: List[str] = []

    def _add(raw: str) -> None:
        cleaned = _clean_query(raw)
        if not cleaned:
            return
        # Single-word variants are only useful as the bare subject itself.
        if len(cleaned.split()) < 2 and cleaned.lower() != subject.lower():
            return
        if cleaned.lower() in tried_lower:
            return
        tried_lower.add(cleaned.lower())
        out.append(cleaned)

    for query in (queries or []):
        tokens = [token for token in str(query or "").split() if token]
        for end in range(len(tokens) - 1, 1, -1):
            _add(" ".join(tokens[:end]))
            if len(out) >= limit:
                return out
    if subject:
        _add(f"{subject} photo")
        _add(f"{subject} photograph")
        _add(subject)
    return out[:limit]


def _history_visual_terms(scene_text: str) -> List[str]:
    """Return concrete historical subjects/actions, never narration prose."""
    lower = str(scene_text or "").lower()
    patterns = (
        (r"iceberg|struck|collision|starboard", "iceberg collision"),
        (r"sink|sinking|broke in two|ocean floor|shipwreck", "ship sinking"),
        (r"lifeboat|survivor|rescu|carpathia", "lifeboat rescue"),
        (r"maiden voyage|embark|sailed|departed|southampton|new york", "ocean liner departure"),
        (r"shipyard|constructed|belfast|harland|wolff", "shipyard construction"),
        (r"captain|commanded|officer|lookout", "ship captain"),
        (r"radio|sos|distress|marconi|rocket", "distress signal"),
        (r"passenger|immigrant|aristocrat|crew", "ocean liner passengers"),
        (r"soldier|infantry|trench|frontline|battlefield|combat", "battlefield historical photo"),
        (r"tank|artillery|cannon|bomber|warplane|fighter plane", "military equipment photo"),
        (r"treaty|surrender|declaration|pact|armistice|signing", "treaty signing photograph"),
        (r"emperor|monarch|queen|king|coronation|crown", "historical monarch portrait"),
        (r"roman|gladiator|colosseum|legion|senate|centurion", "ancient roman historical depiction"),
        (r"castle|fortress|medieval|knight|siege|armor", "medieval fortress historical photograph"),
        (r"pyramid|pharaoh|tomb|hieroglyph|temple|ruins", "ancient historical ruins photograph"),
        (r"locomotive|steam engine|railway|railroad|train station", "vintage locomotive photograph"),
        (r"factory|industrial|worker|coal mine|mill|loom", "industrial revolution archival photograph"),
        (r"protest|march|demonstration|crowd|rally|speech", "historical demonstration crowd photo"),
        (r"fish and chips|chippy|shopfront|high street|retro|vintage", "vintage british high street photo"),
        (r"archive|document|newspaper|headline|press", "historical archive"),
        (r"photo|photograph|vintage|historical|history", "archival photograph"),
    )
    return [term for pattern, term in patterns if re.search(pattern, lower)]


def _ranked_section_hints(scene_texts: List[str]) -> List[str]:
    """Carry listicle section owners until the next explicit rank boundary."""
    hints: List[str] = []
    active = ""
    rank_words = (
        "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        "first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
    )
    boundary_re = re.compile(
        rf"(?:^|[.!?]\s*)"
        rf"(?:#\s*\d+|(?:at|in|coming\s+in\s+at)?\s*(?:number|no\.?|rank(?:ed)?)\s*(?:\d+|{rank_words})|"
        rf"(?:{rank_words})\s*(?:place|spot))"
        rf"\s*(?:is|goes\s+to|belongs\s+to|[:\-–—,])?\s+(.+?)(?:,|\.|\bwhose\b|\bwho\b|\bwith\b|$)",
        re.IGNORECASE,
    )
    stop = {"is", "a", "an", "whose", "who", "with", "and", "or", "rank", "number"}
    for scene in scene_texts or []:
        text = str(scene or "")
        match = boundary_re.search(text)
        if match:
            raw = re.sub(r"[^A-Za-z0-9\s\-']", " ", match.group(1))
            words = [w for w in raw.split() if w.lower() not in stop]
            candidate = " ".join(words[:5]).strip()
            if candidate:
                active = candidate
        hints.append(active)
    return hints


def _valid_semantic_query_rows(rows: Optional[List[Dict]], expected_count: int = 0) -> bool:
    if not isinstance(rows, list) or not rows:
        return False
    if expected_count and len(rows) < expected_count:
        return False
    useful_keys = {
        "specific", "medium", "general", "fallback_1", "fallback_2",
        "youtube_query", "search_query", "primary", "query",
    }
    for row in rows:
        if not isinstance(row, dict):
            return False
        if not any(str(row.get(key) or "").strip() for key in useful_keys):
            return False
    return True


def _query_has_leakage(query: str, subject: str = "", scene_text: str = "", context_text: str = "") -> bool:
    lower = (query or "").lower()
    words = set(re.findall(r"[a-z0-9]+", lower))
    subject_scene = f"{subject} {scene_text} {context_text}".lower()
    if words.intersection(_QUERY_LEAK_TERMS | _ABSTRACT_QUERY_TERMS):
        return True
    if "football" in subject_scene or "soccer" in subject_scene or "fifa" in subject_scene:
        if words.intersection(_CROSS_NICHE_LEAK_TERMS):
            return True
    properish = [
        w.lower()
        for w in re.findall(r"\b[A-Z][A-Za-z]{3,}\b", str(query or ""))
        if w.lower() not in STOPWORDS and w.lower() not in {"flickr", "photo", "image"}
    ]
    if properish:
        allowed = subject_scene
        for w in properish:
            if w not in allowed:
                return True
    return False


def _should_switch_topic_subject(candidate: str, main_subject: str, scene_text: str) -> bool:
    if not candidate or _is_weak_subject_phrase(candidate):
        return False
    if not main_subject:
        return True
    candidate_lower = str(candidate or "").lower().strip()
    _GENERIC_NON_SUBJECTS = {
        "fans", "fanbase", "supporters", "crowd", "audience", "people", "viewers",
        "excitement", "hype", "anticipation", "release", "launch", "sales", "records",
        "trailer", "gameplay", "graphics", "development", "history", "future", "success",
        "auction listings", "auction listing", "listings", "listing", "auction", "auctions",
        "show", "shows", "really", "happened", "truth", "secret", "secrets",
    }
    if candidate_lower in _GENERIC_NON_SUBJECTS:
        return False
    main_lower = str(main_subject or "").lower()
    _VESSEL_ANCHOR_TERMS = {
        "rms", "hms", "uss", "mv", "ss", "titanic", "ship", "ships",
        "liner", "vessel", "ferry", "steamship", "airship", "submarine",
    }
    main_tokens = set(re.findall(r"[a-z0-9]+", main_lower)) | {main_lower}
    if main_tokens & _VESSEL_ANCHOR_TERMS:
        return False
    main_words = [w for w in re.findall(r"[a-z0-9]+", main_lower) if w not in STOPWORDS]
    candidate_words = [w for w in re.findall(r"[a-z0-9]+", str(candidate).lower()) if w not in STOPWORDS]
    if len(main_words) >= 2 and len(candidate_words) < 2:
        return False
    cand_words = [w for w in re.findall(r"[a-z]{3,}", str(candidate).lower()) if w not in STOPWORDS]
    scene_lower = (scene_text or "").lower()
    return bool(cand_words and any(re.search(r"\b" + re.escape(w) + r"\b", scene_lower) for w in cand_words))


def _looks_like_full_line(query: str, max_words: int = 6) -> bool:
    raw = str(query or "").strip()
    words = re.findall(r"[A-Za-z0-9]+", raw)
    return (
        len(words) > max_words
        or any(ch in raw for ch in ".:;!?")
        or bool(re.search(r"\b(?:and|but|because|while|after|before|when|where|which|that|this)\b", raw, re.I))
    )


def _compact_web_query(query: str, subject: str = "", scene_text: str = "", context_text: str = "") -> str:
    """Keep image-search queries broad enough for Flickr/Openverse/Bing to work."""
    cleaned = _clean_query(query)
    if not cleaned:
        return ""
    if _query_has_leakage(cleaned, subject, scene_text, context_text):
        broad = _topic_fallback_queries(subject, scene_text, limit=3) if subject else []
        return broad[0] if broad else ""
    words = cleaned.split()
    subject_words = [w for w in re.findall(r"[a-z0-9]+", str(subject or "").lower()) if w not in STOPWORDS]
    max_query_words = max(6, len(subject_words) + 3)
    if len(words) <= max_query_words and not _looks_like_full_line(cleaned, max_words=max_query_words):
        return cleaned

    lower = f"{cleaned} {scene_text}".lower()
    broad = _topic_fallback_queries(subject, scene_text, limit=5) if subject else []
    if broad:
        if any(term in lower for term in ("empty", "seat", "turnout", "absent")):
            return broad[0]
        if any(term in lower for term in ("ticket", "visa", "travel", "flight", "bond", "cancel")):
            return broad[0]
        if any(term in lower for term in ("protest", "boycott", "banner", "sign")):
            return broad[0]
        return broad[min(2, len(broad) - 1)]

    content = [
        w for w in words
        if w.lower() not in STOPWORDS and w.lower() not in BAD_QUERY_TERMS
    ]
    return " ".join(content[:max_query_words] or words[:max_query_words])


def _query_is_weak_or_too_specific(query: str, subject: str = "", scene_text: str = "", context_text: str = "") -> bool:
    cleaned = _clean_query(query)
    if not cleaned:
        return True
    words = [w.lower() for w in re.findall(r"[a-z0-9]+", cleaned)]
    if not words:
        return True
    subject_words = [w for w in re.findall(r"[a-z0-9]+", str(subject or "").lower()) if w not in STOPWORDS]
    max_query_words = max(6, len(subject_words) + 3)
    if _looks_like_full_line(cleaned, max_words=max_query_words) or _query_has_leakage(cleaned, subject, scene_text, context_text):
        return True
    if len(words) > max_query_words:
        return True
    if any(w in {"didn", "doesn", "nearly", "quietly"} for w in words):
        return True
    if set(words).intersection(_QUERY_OUTPUT_GARBAGE_TERMS):
        return True
    useful = [
        w for w in words
        if w not in STOPWORDS
        and w not in BAD_QUERY_TERMS
        and w not in {"photo", "portrait", "documentary", "image", "images"}
    ]
    subject_words_set = set(subject_words)
    non_subject_useful = [w for w in useful if w not in subject_words_set]
    return len(useful) <= 1 or (subject_words_set and len(non_subject_useful) == 0)


def _build_multi_wave_documentary_mutations(subject: str, scene_text: str, context_text: str = "") -> List[str]:
    """Generate progressive multi-wave query mutations for documentary depth.
    Wave 1: Specific narrow query (already formed by caller)
    Wave 2: Era and geographic context mutation (broadened context)
    Wave 3: Archival, primary evidence, blueprint & historical press mutation
    """
    mutations: List[str] = []
    clean_subj = str(subject or "").strip()
    if not clean_subj:
        return mutations

    combined = f"{scene_text} {context_text}"
    # Wave 2: Historical era & geographical context mutation
    years = re.findall(r"\b(?:18|19|20)\d{2}s?\b", combined)
    locations = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", scene_text)
    loc = next((l for l in locations if l.lower() not in {"the", "and", "in", "at", "on", "from", "with"} and l.lower() not in clean_subj.lower()), "")

    if years and loc:
        mutations.append(f"{clean_subj} {loc} {years[0]}")
    elif years:
        mutations.append(f"{clean_subj} {years[0]} archival")
    elif loc:
        mutations.append(f"{clean_subj} {loc} history")

    # Wave 3: Archival, primary evidence & blueprint mutations
    mutations.append(f"{clean_subj} archival photograph")
    mutations.append(f"{clean_subj} newspaper clipping")
    mutations.append(f"{clean_subj} historical document")
    mutations.append(f"{clean_subj} blueprint schematic")
    return mutations


def _repair_query_pack(queries: List[str], subject: str, scene_text: str, limit: int = 5, context_text: str = "") -> List[str]:
    # Detect listicle format to prevent global/previous subject pollution
    if context_text and _is_listicle_script(context_text):
        subject = ""

    repaired: List[str] = []
    seen: Set[str] = set()
    is_topic_subject = subject and not _is_person_fallback_subject(subject)
    effective_limit = min(limit, 4) if is_topic_subject else limit
    subject_words = [w for w in re.findall(r"[a-z0-9]+", str(subject or "").lower()) if w not in STOPWORDS]
    max_query_words = max(6, len(subject_words) + 3)

    def _add(q: str) -> None:
        compact = _compact_web_query(q, subject, scene_text, context_text)
        if not compact:
            return
        compact_words = [w.lower() for w in re.findall(r"[a-z0-9]+", compact)]
        if (
            _query_has_leakage(compact, subject, scene_text, context_text)
            or _has_bad_terms(compact)
            or len(compact_words) > max_query_words
            or set(compact_words).intersection(_QUERY_OUTPUT_GARBAGE_TERMS)
        ):
            return
        # Keep an exact subject anchor only as a last-resort fallback. All
        # other output must contain at least one concrete visual term.
        if _query_is_weak_or_too_specific(compact, subject, scene_text, context_text):
            if compact.lower() != str(subject or "").strip().lower():
                return
        key = compact.lower()
        if key not in seen and not _has_bad_terms(compact):
            seen.add(key)
            repaired.append(compact)

    # LLM queries lead, but only if they are short, concrete, and context-safe.
    for query in queries or []:
        if _query_is_weak_or_too_specific(query, subject, scene_text, context_text):
            continue
        _add(query)

    # Then add broad family fallbacks so every scene still has a high-result path.
    for query in _topic_fallback_queries(subject, scene_text, limit=8):
        _add(query)

    # Progressive documentary mutations (Wave 2 & 3: era/context & archival/evidence)
    for q_mut in _build_multi_wave_documentary_mutations(subject, scene_text, context_text):
        _add(q_mut)

    if len(repaired) < 2:
        for query in queries or []:
            _add(query)

    if not repaired and subject:
        anchor = _clean_query(subject)
        if anchor and not _query_has_leakage(anchor, subject, scene_text, context_text):
            repaired.append(anchor)

    return repaired[:effective_limit]


def _build_specific_queries(subject: str, action_terms: List[str],
                             scene_text: str, full_script: str = "") -> List[str]:
    """Build several simple, general query variations.
    
    Produces simple queries like:
      - "{Subject} {action}" (base)
      - "{Subject} {action} action"
      - "{Subject} portrait"
      - "{Subject} photo"
    """
    queries: List[str] = []
    seen: Set[str] = set()

    def _add(q: str):
        qs = q.strip()
        if qs and qs not in seen and len(qs) > 3:
            seen.add(qs)
            queries.append(qs)

    for action in action_terms:
        if not action or action.lower() == subject.lower():
            continue

        _add(f"{subject} {action}")
        _add(f"{subject} {action} action")

    # Add general fallback categories for the subject. Portrait/photo only makes
    # sense for real people; event/group topics need visual context instead.
    if _is_person_fallback_subject(subject):
        _add(f"{subject} portrait")
        _add(f"{subject} photo")
        _add(f"{subject} press conference")
        _add(f"{subject} training")
        _add(subject)
    else:
        for q in _topic_fallback_queries(subject, scene_text, limit=5):
            _add(q)

    return queries[:8]


_NON_NAME_WORDS = {
    "as", "if", "or", "so", "up", "out", "down", "off",
    "country", "nation", "quietly", "pulled", "pulls", "pulling",
    "the", "this", "that", "what", "when", "where", "which", "who", "how", "why", "here", "there", "often", "then", "once",
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "players", "team", "crowd", "fans", "people", "hero", "villain", "official",
    "street", "streets", "road", "roads", "building", "buildings", "city", "cities", "nature", "sky", "water", "ground", "house", "houses",
    "scene", "moment", "event", "action", "shot", "view", "image", "photo",
    "multimedia", "cockpit", "dashboard", "display", "screen", "interface",
    "infotainment", "navigation", "mirrorcam", "instrument", "cluster",
    "opening", "final", "match", "game", "play", "score", "scored", "scoring", "result",
    "powerful", "sudden", "quick", "formal", "post", "wave", "bench",
    "opposition", "teammates", "scoreboard", "gesture", "signal", "ceremony",
    "movement", "acknowledgment", "reflection", "celebration",
    "world", "cup", "final", "league", "series", "show", "story",
    "rise", "fall", "journey", "legacy", "career", "era", "age", "time",
    "day", "night", "year", "season", "week", "month",
    "north", "south", "east", "west", "central", "local", "global",
    "new", "old", "fresh", "healthy", "raw", "prepared", "cooked", "green", "red",
    "great", "big", "small", "first", "last", "next", "same", "clean", "modern",
    "common", "simple", "key", "main", "early", "final",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "his", "her", "its", "our", "your", "their", "my",
    "will", "can", "could", "would", "should", "must", "shall", "may",
    "has", "have", "had", "was", "were", "been", "being", "is", "are",
    "does", "did", "done", "make", "made", "take", "took", "give", "gave",
    "get", "got", "come", "came", "go", "went", "see", "saw", "know", "knew",
    "think", "thought", "say", "said", "tell", "told", "ask", "asked",
    "walked", "walks", "walk", "constructing", "constructed", "identifies", "identified",
    "allows", "allow", "exploits", "exploited", "execute", "executed", "executing",
    "batted", "bats", "bowled", "bowls", "fielded", "fields", "catches", "training",
    "enter", "enters", "entering", "leave", "leaves", "leaving", "lift", "lifts", "lifting",
    "rise", "rises", "rising", "speak", "speaks", "speaking", "announce", "announces", "announced",
    "win", "wins", "winning", "change", "changes", "changed", "move", "moves", "moving",
    "scored", "scoring", "pressed", "pressing", "defined", "defining", "defines",
    "remains", "remained", "remain", "gives", "goes", "comes",
    "since", "after", "before", "because", "while", "during", "until",
    "about", "above", "below", "between", "through", "against", "among",
    "into", "onto", "upon", "within", "without", "across", "around",
    "over", "under", "behind", "beside", "beyond", "inside", "outside",
    "stadium", "arena", "field", "court", "ground", "pitch", "track",
    "test", "cricket", "masterclass", "innings", "statistics", "stats",
    "championship", "tournament", "competition", "matches",
    "leading", "leads", "lead", "average", "averages", "record", "records",
    "recordholder", "trophywinner", "trophy", "winner", "chase", "chasing",
    "batting", "bowling", "fielding", "captain", "captaincy", "fitness",
    "training", "practice", "decade", "decades", "century", "centuries",
    "runs", "wicket", "wickets", "milestone", "milestones", "legend",
    "legends", "biography", "portrait", "photos", "photo", "images",
    "image", "video", "videos", "clip", "clips", "highlights", "highlight",
    "news", "report", "reports", "update", "updates", "analysis",
    "analytics", "graph", "graphs", "chart", "charts", "rank", "ranks",
    "ranking", "rankings", "comparison", "comparisons", "vs", "versus",
    "born", "birth", "birthdate", "age", "years", "year",
    "lights", "clouds", "tone", "momentum", "shift", "erupts", "unexpected",
    "dramatic", "turn", "stunned", "tide", "triumph", "watching",
    "stands", "lose", "minds", "joy", "protective", "gear", "upward",
    "confirms", "extraordinary", "achievement", "positions", "pressure",
    "area", "explodes", "emotion", "excitement", "masses", "historic",
    "occasion", "reflections", "capture", "magnitude", "happened",
    "illuminate", "falls", "opponent", "dejected", "fist", "pump",
    "cheer", "wildly", "removes", "helmet", "looks", "sky",
    "umpire", "signals", "boundary", "runs", "wickets", "dugout",
    "erupts", "celebration", "acknowledges", "c", "wave",
    "referee", "presents", "award", "gives", "interview",
    "blistering", "cover", "drive", "side", "bat", "century",
    "hugs", "milestone", "shows", "bowlers", "look",
    "onto", "pitch", "dribbles", "past", "defenders", "scores",
    "together", "finger", "point", "hat", "trick",
    "debut", "records", "fallen", "formats", "across",
    "sheer", "dominate", "matters", "ties", "innings",
    "mysterious", "dark", "gathering", "changes", "everything",
    "crucial", "breakthrough", "reflects", "events", "completely",
    "powerful", "gesture", "signals", "triumph", "watching",
    "hero", "removes", "protective", "upward", "official",
    "quick", "movement", "between", "keeps", "bench",
    "explodes", "emotion", "excitement", "formal", "marks",
    "post", "reflections", "capture", "magnitude", "happened",
    "stadium", "lights", "illuminate", "night", "falls",
}

_WEAK_SUBJECT_PHRASES = {
    "quietly pulled", "country quietly", "nation quietly", "pulled fans",
    "country pulled", "fans quietly", "didn lose", "didn fail",
}


def _is_weak_subject_phrase(value: str) -> bool:
    terms = [term.lower() for term in re.findall(r"[A-Za-z]{2,}", value or "")]
    if not terms:
        return True
    phrase = " ".join(terms)
    if phrase in _WEAK_SUBJECT_PHRASES:
        return True
    weak = {"country", "nation", "quietly", "pulled", "pulls", "pulling", "fans", "didn", "doesn", "officially", "unofficially", "nearly", "money"} | _WEAK_SINGLE_SUBJECTS
    return len(terms) <= 3 and all(term in weak or term in STOPWORDS or term in _NON_NAME_WORDS for term in terms)


def _detect_fictional_franchise_subject(text: str) -> str:
    lower = (text or "").lower()
    simpsons_terms = (
        "the simpsons", "simpsons", "homer simpson", "bart simpson",
        "marge simpson", "lisa simpson", "maggie simpson",
        "springfield elementary", "moe tavern", "mr burns",
        "montgomery burns", "waylon smithers", "edna krabappel",
        "principal skinner", "comic book guy", "animated town of springfield",
    )
    if any(term in lower for term in simpsons_terms):
        return "The Simpsons"
    return ""


def _detect_documentary_topic_subject(text: str) -> str:
    sample = text or ""
    patterns = (
        r"\b((?:The\s+)?[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\s+War)\b",
        r"\b((?:World\s+War|Cold\s+War|Civil\s+War)(?:\s+(?:I{1,3}|IV|V|One|Two|Three|[A-Z][A-Za-z]+))?)\b",
        r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\s+(?:Revolution|Conflict|Battle|Invasion|Offensive|Accords))\b",
    )
    for pattern in patterns:
        match = re.search(pattern, sample)
        if not match:
            continue
        anchor = re.sub(r"^(The)\s+", "", match.group(1)).strip(" ,.-")
        if len(anchor.split()) >= 2:
            return anchor
    return ""


def _detect_news_sports_topic_subject(text: str) -> str:
    sample = text or ""
    lower = sample.lower()
    if "world cup" in lower or "fifa" in lower:
        if re.search(r"\b(football|soccer)\b", lower):
            return "FIFA World Cup football"
        return "FIFA World Cup"
    disaster_kw = r"\b(earthquakes?|hurricanes?|tornadoes?|floods?|tsunamis?|wildfires?|volcanic|landslides?|droughts?|pandemics?|epidemics?|outbreaks?|explosions?|collapsed|collapses?|landslides?)\b"
    if re.search(disaster_kw, lower):
        location = re.search(
            r"(?:in|near|off|at|struck|hit|devastated|affected)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})",
            sample,
        )
        if not location:
            location = re.search(
                r"([A-Z][A-Za-z]{2,}(?:\s+[A-Z][A-Za-z]+){0,2})\s+(?:had\s+just\s+been|was\s+just|was|were)\s+(?:struck|hit|devastated|affected|shaken|rocked)\b",
                sample,
            )
        if location:
            loc = location.group(1).strip()
            if loc and len(loc) >= 3:
                loc_pos = location.start()
                matches = list(re.finditer(disaster_kw, lower))
                after = [m for m in matches if m.start() > loc_pos]
                if after:
                    chosen = after[0]
                else:
                    chosen = matches[0] if matches else None
                if chosen:
                    return f"{loc} {chosen.group(1)}"
                return ""
        return ""
    return ""


def _extract_main_subject(text: str) -> str:
    from ..documentary_subject import title_hook_subject, collection_title_subject
    collection = collection_title_subject(text)
    if collection:
        return collection
    hooked_subject = title_hook_subject(text)
    if hooked_subject:
        return hooked_subject
    title_line = str(text or "").split("\n", 1)[0].strip()
    if ":" in title_line or " - " in title_line or " | " in title_line:
        prefix = re.split(r"[:|]|\s+-\s+", title_line, maxsplit=1)[0].strip()
        prefix = re.sub(r"^(?:the|a|an)\s+", "", prefix, flags=re.I)
        if prefix and 2 <= len(prefix.split()) <= 6:
            if not _is_weak_subject_phrase(prefix):
                return prefix
    """Extract the primary named entity from text, preferring full names.

    Strategy:
      1. Find most frequent multi-word capitalized name (e.g. "Virat Kohli")
      2. Case-insensitive full-name detection using word co-occurrence
      3. Find adjacent capitalized word pairs that form names
      4. Single name boost: if we find "Kohli", search for "virat kohli" case-insensitively
      5. Fall back to the most frequent single capitalized name
    """
    if not text:
        return ""

    sample = text[:8000]
    initialed_person = re.search(r"\b(?:[A-Z]\.\s*){1,4}[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b", sample)
    if initialed_person:
        return initialed_person.group(0).strip()
    # Clean possessives from text before processing (e.g. "Kohli's" → "Kohli", "Kohlis" → "Kohli")
    sample = re.sub(r"\b([A-Z][a-z]+)'s\b", r'\1', sample)
    # Also handle possessive without apostrophe at sentence start ("Kohlis captaincy" → "Kohli captaincy")
    # Only strip trailing 's' from capitalized words that are NOT known English words
    _COMMON_S_WORDS = {'this', 'his', 'its', 'was', 'has', 'does', 'goes', 'thus', 'plus', 'us', 'yes', 'series', 'always', 'across', 'perhaps', 'various', 'versus', 'status', 'focus', 'minus', 'famous', 'bonus', 'campus', 'virus', 'census', 'genius', 'nexus', 'radius', 'surplus', 'chorus', 'corpus', 'ruckus', 'circus', 'cactus', 'fungus', 'mucus', 'walrus', 'syllabus', 'stimulus', 'apparatus', 'consensus', 'exodus', 'lotus', 'octopus', 'platypus', 'terminus', 'thesaurus', 'reigns', 'james', 'jones', 'williams', 'davis', 'harris', 'evans', 'edwards', 'collins', 'brooks', 'rogers', 'stephens', 'morris', 'sports', 'news', 'physics', 'mathematics', 'ethics', 'politics', 'carlos', 'marcos', 'lucas', 'andres', 'jesus', 'moses', 'chris', 'dennis', 'lewis', 'douglas', 'nicolas', 'silas', 'jonas', 'elias', 'tomas', 'matias', 'marcus'}
    def _strip_possessive_s(m):
        word = m.group(1)
        if word.lower() in _COMMON_S_WORDS:
            return word
        base = word[:-1]
        if len(base) >= 3 and base[0].isupper():
            if re.search(rf'\b{re.escape(base.lower())}\b', sample.lower()):
                return base
            return word
        return word
    sample = re.sub(r'\b([A-Z][a-z]{3,}s)\b', _strip_possessive_s, sample)
    # Preserve named vessels and other initialed entities. The normal proper
    # noun regex intentionally excludes all-caps tokens, which caused titles
    # such as "The Sinking of RMS Titanic" to fall back to "Sinking".
    initialed_entity = re.search(
        r"\b(?:RMS|HMS|USS|SS|MV|MS|HMT)\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2}\b",
        sample,
    )
    if initialed_entity:
        entity = initialed_entity.group(0).strip()
        _web_log(f"subject detection: '{entity}' (initialed entity anchor)")
        return entity
    gta_match = re.search(r"\b(?:Grand\s+Theft\s+Auto\s*(?:VI|V|IV|6|5|4)?|GTA\s*(?:VI|V|IV|6|5|4)?)\b", sample, re.I)
    if gta_match:
        matched = gta_match.group(0).strip()
        matched_norm = "Grand Theft Auto VI" if re.search(r"(?:vi|6)", matched, re.I) else ("Grand Theft Auto V" if re.search(r"(?:v|5)", matched, re.I) else "Grand Theft Auto")
        _web_log(f"subject detection: '{matched_norm}' (gaming entity anchor)")
        return matched_norm
    franchise_subject = _detect_fictional_franchise_subject(sample)
    if franchise_subject:
        _web_log(f"subject detection: '{franchise_subject}' (fictional franchise anchor)")
        return franchise_subject
    documentary_subject = _detect_documentary_topic_subject(sample)
    if documentary_subject:
        _web_log(f"subject detection: '{documentary_subject}' (documentary topic anchor)")
        return documentary_subject
    news_sports_subject = _detect_news_sports_topic_subject(sample)
    if news_sports_subject:
        _web_log(f"subject detection: '{news_sports_subject}' (news/sports topic anchor)")
        return news_sports_subject

    def _valid_subject_phrase(value: str) -> bool:
        if _is_weak_subject_phrase(value):
            return False
        terms = [term.lower() for term in re.findall(r"[A-Za-z]{2,}", value or "")]
        if _is_weak_subject_phrase(value):
            return False
        if len(terms) < 2:
            return False
        if terms[0] in STOPWORDS or terms[0] in _NON_NAME_WORDS:
            return False
        if all(term in STOPWORDS or term in _NON_NAME_WORDS for term in terms):
            return False
        if terms[0] in {"for", "as", "but", "and", "or", "the", "this", "that"}:
            return False
        return True

    # Strategy 1: Find most frequent multi-word capitalized sequences
    cap_words = [(m.group(), m.start()) for m in re.finditer(r'\b([A-Z][a-z]{1,})\b', sample)]

    if cap_words:
        full_names = re.findall(r'\b([A-Z][a-z]{1,}(?:\s+[A-Z][a-z]{1,})+)\b', sample)
        name_pairs = Counter()
        for name in full_names:
            words = name.split()
            start = 0
            while start < len(words) and words[start].lower() in _NON_NAME_WORDS:
                start += 1
            end = len(words)
            while end > start and words[end - 1].lower() in _NON_NAME_WORDS:
                end -= 1
            cleaned_words = words[start:end]
            if len(cleaned_words) >= 2:
                cleaned_name = " ".join(cleaned_words)
                if _valid_subject_phrase(cleaned_name) and all(w.lower() not in _NON_NAME_WORDS for w in cleaned_words):
                    name_pairs[cleaned_name] += 1

        if name_pairs:
            best, count = name_pairs.most_common(1)[0]
            _web_log(f"subject detection: '{best}' (full name, {count} occurrences)")
            return best

        # Strategy 2: Find adjacent capitalized word pairs
        pair_freq = Counter()
        for i in range(len(cap_words) - 1):
            first, pos1 = cap_words[i]
            second, pos2 = cap_words[i + 1]
            between = sample[pos1 + len(first):pos2].strip()
            if between and between not in ("'s", "'"):
                continue
            f_low = first.lower()
            s_low = second.lower()
            pair = f"{first} {second}"
            if f_low not in _NON_NAME_WORDS and s_low not in _NON_NAME_WORDS and _valid_subject_phrase(pair):
                pair_freq[pair] += 1

        if pair_freq:
            best, count = pair_freq.most_common(1)[0]
            _web_log(f"subject detection: '{best}' (adjacent pair, {count} occurrences)")
            return best

    # Strategy 3: Single name + case-insensitive co-occurrence search
    # If we found a capitalized single name like "Kohli", search the full
    # lowercased text for "firstname kohli" or "kohli lastname" patterns
    if cap_words:
        _SENTENCE_STARTERS = {
            "the", "a", "an", "this", "that", "these", "those", "what", "when",
            "where", "which", "who", "how", "why", "since", "after", "before",
            "because", "while", "during", "about", "over", "under", "through",
            "against", "among", "into", "across", "around", "upon", "within",
            "without", "stadium", "arena", "field", "court", "ground", "players",
            "team", "crowd", "fans", "people", "hero", "scene", "moment", "event",
            "action", "opening", "final", "match", "game", "powerful", "sudden",
            "quick", "formal", "post", "wave", "bench", "opposition", "teammates",
            "scoreboard", "gesture", "signal", "ceremony", "movement",
            "acknowledgment", "reflection", "celebration", "world", "league",
            "series", "show", "story", "history", "rise", "fall", "journey",
            "legacy", "career", "north", "south", "east", "west", "central",
            "local", "global", "new", "old", "great", "big", "small", "first",
            "last", "next", "same", "his", "her", "its", "our", "your", "their",
            "will", "can", "could", "would", "should", "must", "has", "have",
            "had", "was", "were", "been", "being", "is", "are", "does", "did",
            "make", "made", "take", "took", "give", "gave", "get", "got", "come",
            "came", "go", "went", "see", "saw", "know", "knew", "think", "thought",
            "say", "said", "tell", "told", "ask", "asked",
        }

        single_freq = Counter()
        for name, pos in cap_words:
            n_low = name.lower()
            if n_low in STOPWORDS or n_low in _NON_NAME_WORDS:
                continue
            if pos == 0 and n_low in _SENTENCE_STARTERS:
                continue
            single_freq[name] += 1

        if single_freq:
            best_single, count = single_freq.most_common(1)[0]
            best_lower = best_single.lower()

            # Case-insensitive co-occurrence: find words that appear right before
            # the single name (firstname lastname pattern like "virat kohli")
            text_lower = sample.lower()
            cooccur = Counter()

            # ONLY look for "word best_lower" pattern (firstname lastname)
            # NOT "best_lower word" which catches verbs like "Kohli continues"
            for m in re.finditer(rf'\b([a-z]{{2,}})\s+{re.escape(best_lower)}\b', text_lower):
                preceding = m.group(1)
                if preceding not in STOPWORDS and preceding not in _NON_NAME_WORDS and len(preceding) >= 2:
                    cooccur[f"{preceding} {best_lower}"] += 1

            if cooccur:
                sorted_pairs = cooccur.most_common()
                top_name, top_count = sorted_pairs[0]

# Accept co-occurrence if:
                # 1. Partner appears 2+ times before surname (strong signal)
                # 2. OR partner is capitalized in original text (proper noun)
                # 3. OR partner appears once but looks like a plausible first name
                #    (short, filtered through stopwords/non-name-words)
                if top_count >= 2:
                    full_name = top_name.title()
                    _web_log(f"subject detection: '{full_name}' (co-occurrence boost from '{best_single}', {top_count} occurrences)")
                    return full_name

                for pair_name, pair_count in sorted_pairs:
                    pair_words = pair_name.split()
                    for word in pair_words:
                        if word.lower() == best_lower:
                            continue
                        # Is this word ever capitalized in the original text?
                        patr = rf'\b{re.escape(word)}\b'
                        for cm in re.finditer(patr, sample, re.IGNORECASE):
                            start = cm.start()
                            if start < len(sample) and sample[start].isupper():
                                if start == 0 or sample[start - 1] in (' ', '\n', '\t', '"', "'", '(', '[', '{'):
                                    full_name = pair_name.title()
                                    _web_log(f"subject detection: '{full_name}' (co-occurrence with capitalized partner, {pair_count}x)")
                                    return full_name

                # Single co-occurrence but partner looks like a plausible first name
                # (already filtered through stopwords/non-name-words, and short enough)
                if cooccur and len(cooccur.most_common(1)[0][0].split()[0]) <= 8:
                    full_name = cooccur.most_common(1)[0][0].title()
                    _web_log(f"subject detection: '{full_name}' (single co-occurrence, plausible first name)")
                    return full_name

                for pair_name, pair_count in sorted_pairs:
                    pair_words = pair_name.split()
                    for word in pair_words:
                        if word.lower() == best_lower:
                            continue
                        # Is this word ever capitalized in the original text?
                        patr = rf'\b{re.escape(word)}\b'
                        for cm in re.finditer(patr, sample, re.IGNORECASE):
                            start = cm.start()
                            if start < len(sample) and sample[start].isupper():
                                if start == 0 or sample[start - 1] in (' ', '\n', '\t', '"', "'", '('):
                                    full_name = pair_name.title()
                                    _web_log(f"subject detection: '{full_name}' (co-occurrence with capitalized partner, {pair_count}x)")
                                    return full_name

            _web_log(f"subject detection: '{best_single}' (single name, {count} occurrences)")
            return best_single

    # Strategy 4: Pure case-insensitive extraction from lowercase text
    text_lower = (text or "").lower()[:8000]
    ci_pair_counter = Counter()
    # Find two-word sequences where NEITHER word is a stopword/non-name-word
    for m in re.finditer(r'\b([a-z]{2,})\s+([a-z]{2,})\b', text_lower):
        w1, w2 = m.group(1), m.group(2)
        if (w1 not in STOPWORDS and w1 not in _NON_NAME_WORDS and
            w2 not in STOPWORDS and w2 not in _NON_NAME_WORDS):
            ci_pair_counter[f"{w1} {w2}"] += 1

    if ci_pair_counter:
        best, count = ci_pair_counter.most_common(1)[0]
        _web_log(f"subject detection: '{best.title()}' (case-insensitive pair, {count} occurrences)")
        return best.title()

    return ""


def _title_query_anchor(title: str) -> str:
    """Keep model numbers and named anchors that capitalization heuristics miss."""
    value = re.sub(r"\s+", " ", str(title or "")).strip(" \t\r\n\"'`.,:;!?()[]")
    value = re.sub(r"^(?:the|a|an)\s+", "", value, flags=re.I)
    value = re.sub(
        r"^(?:history|story|origins?|rise|fall|evolution|legacy|journey|inside)\s+of\s+",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\s+(?:travel|tourism|city|destination)\s+guide\s*$",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\s+(?:biography|bio|profile|documentary|documentaries|film|movie|guide|overview|explained|facts|timeline|history|story|legacy|career|life|journey|product|review|specifications)\s*$",
        "",
        value,
        flags=re.I,
    )
    if re.match(
        r"^(?:top|best|greatest|worst|most)\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
        value,
        flags=re.I,
    ):
        return ""
    return value


def _extract_all_potential_subjects(text: str) -> List[str]:
    """Extract all famous proper-noun entities (persons/franchises/topics) mentioned in the text."""
    if not text:
        return []
    
    # 1. Clean possessives exactly like _extract_main_subject
    sample = text[:8000]
    sample = re.sub(r"\b([A-Z][a-z]+)'s\b", r'\1', sample)
    
    _COMMON_S_WORDS = {'this', 'his', 'its', 'was', 'has', 'does', 'goes', 'thus', 'plus', 'us', 'yes', 'series', 'always', 'across', 'perhaps', 'various', 'versus', 'status', 'focus', 'minus', 'famous', 'bonus', 'campus', 'virus', 'census', 'genius', 'nexus', 'radius', 'surplus', 'chorus', 'corpus', 'ruckus', 'circus', 'cactus', 'fungus', 'mucus', 'walrus', 'syllabus', 'stimulus', 'apparatus', 'consensus', 'exodus', 'lotus', 'octopus', 'platypus', 'terminus', 'thesaurus', 'reigns', 'james', 'jones', 'williams', 'davis', 'harris', 'evans', 'edwards', 'collins', 'brooks', 'rogers', 'stephens', 'morris', 'sports', 'news', 'physics', 'mathematics', 'ethics', 'politics', 'carlos', 'marcos', 'lucas', 'andres', 'jesus', 'moses', 'chris', 'dennis', 'lewis', 'douglas', 'nicolas', 'silas', 'jonas', 'elias', 'tomas', 'matias', 'marcus'}
    def _strip_possessive_s(m):
        word = m.group(1)
        if word.lower() in _COMMON_S_WORDS:
            return word
        base = word[:-1]
        if len(base) >= 3 and base[0].isupper():
            if re.search(rf'\b{re.escape(base.lower())}\b', sample.lower()):
                return base
            return word
        return word
    sample = re.sub(r'\b([A-Z][a-z]{3,}s)\b', _strip_possessive_s, sample)
    
    # 2. Extract fictional franchise subject (if any)
    franchise = _detect_fictional_franchise_subject(sample)
    # 3. Extract documentary subject (if any)
    doc_subj = _detect_documentary_topic_subject(sample)
    
    # 4. Find all multi-word capitalized names
    full_names = re.findall(r'\b([A-Z][a-z]{1,}(?:\s+[A-Z][a-z]{1,})+)\b', sample)
    
    subjects = []
    seen = set()
    
    if franchise and franchise.lower() not in seen:
        seen.add(franchise.lower())
        subjects.append(franchise)
    if doc_subj and doc_subj.lower() not in seen:
        seen.add(doc_subj.lower())
        subjects.append(doc_subj)
        
    def _valid_subject_phrase(value: str) -> bool:
        terms = [term.lower() for term in re.findall(r"[A-Za-z]{2,}", value or "")]
        if len(terms) < 2:
            return False
        if terms[0] in STOPWORDS or terms[0] in _NON_NAME_WORDS:
            return False
        if all(term in STOPWORDS or term in _NON_NAME_WORDS for term in terms):
            return False
        if terms[0] in {"for", "as", "but", "and", "or", "the", "this", "that"}:
            return False
        return True

    for name in full_names:
        words = name.split()
        start = 0
        while start < len(words) and words[start].lower() in _NON_NAME_WORDS:
            start += 1
        end = len(words)
        while end > start and words[end - 1].lower() in _NON_NAME_WORDS:
            end -= 1
        cleaned_words = words[start:end]
        if len(cleaned_words) >= 2:
            cleaned_name = " ".join(cleaned_words)
            if _valid_subject_phrase(cleaned_name) and all(w.lower() not in _NON_NAME_WORDS for w in cleaned_words):
                if cleaned_name.lower() not in seen:
                    seen.add(cleaned_name.lower())
                    subjects.append(cleaned_name)
                    
    # Also find single capitalized names that are frequent and not in stopwords
    cap_words = [(m.group(), m.start()) for m in re.finditer(r'\b([A-Z][a-z]{1,})\b', sample)]
    _SENTENCE_STARTERS = {
        "the", "a", "an", "this", "that", "these", "those", "what", "when",
        "where", "which", "who", "how", "why", "since", "after", "before",
        "because", "while", "during", "about", "over", "under", "through",
        "against", "among", "into", "across", "around", "upon", "within",
        "without", "stadium", "arena", "field", "court", "ground", "players",
        "team", "crowd", "fans", "people", "hero", "scene", "moment", "event",
        "action", "opening", "final", "match", "game", "powerful", "sudden",
        "quick", "formal", "post", "wave", "bench", "opposition", "teammates",
        "scoreboard", "gesture", "signal", "ceremony", "movement",
        "acknowledgment", "reflection", "celebration", "world", "league",
        "series", "show", "story", "history", "rise", "fall", "journey",
        "legacy", "career", "north", "south", "east", "west", "central",
        "local", "global", "new", "old", "great", "big", "small", "first",
        "last", "next", "same", "his", "her", "its", "our", "your", "their",
        "will", "can", "could", "would", "should", "must", "has", "have",
        "had", "was", "were", "been", "being", "is", "are", "does", "did",
        "make", "made", "take", "took", "give", "gave", "get", "got", "come",
        "came", "go", "went", "see", "saw", "know", "knew", "think", "thought",
        "say", "said", "tell", "told", "ask", "asked",
    }
    
    single_counts = Counter()
    for w, pos in cap_words:
        w_low = w.lower()
        if w_low in STOPWORDS or w_low in _NON_NAME_WORDS:
            continue
        if pos == 0 and w_low in _SENTENCE_STARTERS:
            continue
        single_counts[w] += 1
        
    for w, count in single_counts.most_common(10):
        w_low = w.lower()
        if _is_weak_subject_phrase(w):
            continue
        part_of_full = False
        for full in subjects:
            if w_low in full.lower().split():
                part_of_full = True
                break
        if not part_of_full and w_low not in seen:
            seen.add(w_low)
            subjects.append(w)
            
    # Filter out generic topic/country terms
    subjects = [s for s in subjects if not any(w in s.lower() for w in GENERIC_TOPIC_WORDS)]
    
    # Sort subjects: multi-word first, then longest first
    subjects.sort(key=lambda s: (len(s.split()) >= 2, len(s)), reverse=True)
    return subjects


# =============================================================================
#  QUERY CLEANING AND VALIDATION
# =============================================================================

def _has_bad_terms(query: str) -> bool:
    """Check if a query contains abstract/non-visual terms among its core words."""
    words = set(query.lower().split())
    core = words - {"the", "a", "an", "in", "on", "at", "of", "and", "or", "for"}
    if "world" in core and any(w in core for w in ("cup", "t20", "championship", "series")):
        core.remove("world")
    if not core:
        return False
    bad_count = len(core & BAD_QUERY_TERMS)
    return bad_count >= len(core) * 0.7


def _clean_query(q: str) -> str:
    """Clean a query string — strip brackets, punctuation, and normalize."""
    q = re.sub(r"\[[^\]]*\]", "", q)
    q = re.sub(r"\([^)]*\)", "", q)
    q = re.sub(r"\{[^}]*\}", "", q)
    q = re.sub(r"'s\b", "", q)
    # Preserve search-engine exclusion operators (``-wildlife``) while still
    # normalizing ordinary punctuation/hyphens in names. Dropping the leading
    # minus turned automobile queries into positive wildlife terms and was a
    # direct source of irrelevant animal imagery.
    q = re.sub(r"(?<![A-Za-z0-9])-(?=[A-Za-z0-9])", "__NEG__", q)
    # A hyphen inside a name is a word boundary, not a join operation:
    # ``Mountbatten-Windsor`` must remain searchable as two name parts rather
    # than becoming the misspelled ``MountbattenWindsor``.
    q = re.sub(r"(?<=[A-Za-z0-9])-(?=[A-Za-z0-9])", " ", q)
    q = re.sub(r"['\"]", "", q)
    q = re.sub(r"[^\w\s]", " ", q)
    q = q.replace("__NEG__", "-")
    q = re.sub(r"\s+", " ", q).strip()
    return q


def _extract_visual_keywords(text: str, max_kws: int = 8) -> List[str]:
    """Extract visually-searchable keywords from text."""
    cleaned = text.lower()
    cleaned = re.sub(r"'s\b", "", cleaned)
    cleaned = re.sub(r"[^a-z\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return [w for w in re.findall(r'[a-z]{3,}', cleaned)
            if w not in STOPWORDS and w not in BAD_QUERY_TERMS and w not in _NON_NAME_WORDS][:max_kws]


def _classify_scene_type(scene_text: str, main_subject: str) -> str:
    """Classify a scene's visual type to pick appropriate enrichment words."""
    text_lower = (scene_text + " " + main_subject).lower()
    if re.search(r"\b(fifa|football|soccer|premier league|champions league|la liga|serie a|bundesliga)\b", text_lower):
        return "football"
    if re.search(r"\b(?:gta|grand theft auto|gameplay|video game|videogame|gaming|playstation|xbox|nintendo|esports|steam)\b", text_lower):
        return "gaming"
    sports_kw = {"batting", "bowling", "fielding", "wicket", "century", "six",
                 "four", "run", "catch", "boundary", "inning", "innings", "overs", "score",
                 "goal", "penalty", "dunk", "three", "point", "touchdown",
                 "serve", "ace", "volley", "header", "tackle", "dribble",
                 "swim", "sprint", "marathon", "race", "championship", "trophy",
                 "medal", "gold", "silver", "bronze", "victory", "defeat",
                 "match", "game", "tournament", "league", "cup",
                 "cricket", "cricketer", "bowler", "bowlers", "batsman", "batsmen",
                 "bat", "bats", "pitch", "stumps", "crease",
                 "pressure", "technique", "footwork", "wristwork", "balance",
                 "highlights", "career", "legacy", "innings", "runs", "records"}
    nature_kw = {"ocean", "mountain", "forest", "river", "sunset", "sunrise",
                 "landscape", "wildlife", "animal", "flower", "tree", "sky",
                 "clouds", "rain", "snow", "desert", "island", "beach"}
    city_kw = {"city", "building", "street", "bridge", "tower", "monument",
               "skyline", "architecture", "downtown", "traffic", "market"}
    history_kw = {"archive", "historical", "history", "shipwreck", "iceberg", "lifeboat", "lifeboats",
                  "sinking", "shipyard", "voyage", "captain", "distress", "war", "empire",
                  "titanic", "rms", "hms", "uss", "carpathia", "liner", "vessel",
                  "ancient", "century", "era", "dynasty", "reign", "monarchy", "monarch", "kingdom",
                  "revolution", "medieval", "battle", "soldier", "military", "vintage", "retro",
                  "heritage", "ruins", "archaeology", "temple", "palace", "castle", "emperor",
                  "president", "treaty", "industrial", "victorian", "edwardian", "railway", "wwi",
                  "wwii", "ww2", "ww1", "chronicle", "expedition", "discovery", "civilization"}
    vehicle_kw = {"car", "cars", "truck", "trucks", "automobile", "automobiles", "vehicle", "vehicles",
                  "train", "trains", "locomotive", "locomotives", "railway", "railroad", "engine", "engines",
                  "motor", "motors", "classic", "vintage", "auction", "sedan", "coupe", "convertible",
                  "diesel", "steam", "freight", "cabin", "wagon", "carriage", "track", "chassis"}

    text_words = set(text_lower.split())
    sp = len(text_words & sports_kw)
    na = len(text_words & nature_kw)
    ci = len(text_words & city_kw)
    hi = len(text_words & history_kw)
    ve = len(text_words & vehicle_kw)

    if hi > 0 and hi >= sp and hi >= na and hi >= ci and hi >= ve:
        return "history"
    if ve > 0 and ve >= sp and ve >= na and ve >= ci and ve >= hi:
        return "vehicle"
    if sp > 0 and sp >= na and sp >= ci and sp >= ve:
        return "sports"
    if na > 0 and na >= sp and na >= ci and na >= ve:
        return "nature"
    if ci > 0 and ci >= sp and ci >= na and ci >= ve:
        return "city"
    if main_subject:
        return "person"
    return "general"


# =============================================================================
#  MECHANICAL QUERY GENERATION
# =============================================================================

def _generate_queries_mechanical(script: str, title: str, scene_texts: List[str]) -> List[Dict]:
    """Generate diverse, SPECIFIC search queries per scene without LLM.

    Each query is enhanced with context from the scene text — years, opponents,
    match types, venues — to produce search terms like:
      "Virat Kohli cover drive 2012"
      "Virat Kohli vs Australia century 2016"
      "Virat Kohli Mohali T20 2016"
    instead of generic "Virat Kohli batting".
    """
    royal_anchor = _detect_royal_family_anchor(title, script)
    title_anchor = _title_query_anchor(title)
    main_subject = royal_anchor or _extract_main_subject(title) or title_anchor
    # Preserve model/generation numbers that the proper-noun extractor drops
    # (for example ``Tesla Model 3`` -> ``Tesla Model``).  Do not append broad
    # title shells such as ``FIFA World Cup`` to a person's identity.
    if (
        main_subject
        and title_anchor
        and re.search(r"\d", title_anchor)
        and not re.search(r"\d", main_subject)
        and len(title_anchor.split()) <= 6
    ):
        main_subject = title_anchor
    if not (main_subject and len(main_subject.split()) >= 2):
        script_subject = _extract_main_subject(script)
        if script_subject:
            main_subject = script_subject
    combined_text = ". ".join(filter(None, [title, script] + scene_texts))
    potential_subjects = _extract_all_potential_subjects(combined_text)
    if royal_anchor:
        # Replace loose first names discovered by capitalization heuristics.
        # These are the only scene subjects allowed for this story family.
        potential_subjects = [
            "King Charles III", "Prince Harry", "British Royal Family",
        ]
    rows = []

    for idx, scene_text in enumerate(scene_texts):
        # Context-aware text expansion for short/generic scenes
        effective_scene_text = scene_text
        if scene_text:
            cleaned_text = re.sub(r'[^a-zA-Z\s]', '', scene_text).strip()
            if len(cleaned_text.split()) < 4 or not any(c.isalpha() for c in scene_text):
                context_parts = []
                if idx > 0 and scene_texts[idx - 1]:
                    context_parts.append(scene_texts[idx - 1])
                context_parts.append(scene_text)
                if idx + 1 < len(scene_texts) and scene_texts[idx + 1]:
                    context_parts.append(scene_texts[idx + 1])
                effective_scene_text = " ".join(context_parts)

        active_subject = main_subject
        if royal_anchor:
            active_subject = _royal_scene_subject(effective_scene_text, royal_anchor)
        if len(potential_subjects) > 1 and effective_scene_text:
            scene_text_lower = effective_scene_text.lower()
            for subj in potential_subjects:
                if not _should_switch_topic_subject(subj, main_subject, effective_scene_text):
                    continue
                # A one-word capitalized token extracted from a scene must not
                # replace a stronger title anchor (``iPhone 15`` -> ``iPhone``
                # or ``Tesla Model 3`` -> ``Tesla``).
                if (
                    main_subject
                    and len(main_subject.split()) > len(str(subj).split())
                    and all(
                        token.casefold() in {part.casefold() for part in main_subject.split()}
                        for token in str(subj).split()
                    )
                ):
                    continue
                subj_lower = subj.lower()
                subj_words = [w for w in subj_lower.split() if len(w) >= 3]
                if subj_words and any(re.search(r'\b' + re.escape(w) + r'\b', scene_text_lower) for w in subj_words):
                    active_subject = subj
                    break
        if _is_weak_subject_phrase(active_subject) and main_subject:
            active_subject = main_subject
        # Never send a bare surname/first name to an image provider.  The
        # canonical identity is kept in every scene row and then repeated in
        # each query variant so fallbacks cannot drift back to a broad topic.
        active_subject = _expand_full_named_subject(
            active_subject,
            effective_scene_text,
            f"{title} {script[:3000]}",
        )

        scene_kws = _extract_visual_keywords(effective_scene_text, max_kws=8)
        scene_type = _classify_scene_type(effective_scene_text, active_subject)
        entity_kind = _infer_entity_visual_kind(
            active_subject,
            effective_scene_text,
            scene_type,
        )
        # Keep the existing domain classifier for history/sports/nature/city,
        # but correct the generic-person default for named products and places.
        query_scene_type = scene_type
        if entity_kind in {"place", "object", "food", "thing"} and scene_type == "person":
            query_scene_type = entity_kind
        queries: List[str] = []

        if active_subject:
            # 1. Extract context qualifiers
            ctx = _extract_scene_context(effective_scene_text, script, active_subject)
            qualifiers = ctx.get("qualifiers", [])

            # 2. Get enrichments (action terms) for this scene
            enrichments = _pick_enrichments(active_subject, query_scene_type, scene_kws, idx, scene_text=effective_scene_text)
            action = enrichments[0] if enrichments else _default_scene_term(query_scene_type, active_subject, effective_scene_text)

            # Specific Query: subject + action + context qualifiers
            specific_parts = [active_subject, action]
            for q in qualifiers:
                if q.lower() in _KNOWN_CRICKET_TEAMS and q.lower() != "india":
                    specific_parts.append(f"vs {q}")
                else:
                    specific_parts.append(q)
            specific_query = " ".join(specific_parts)

            # Medium Query: subject + action + top visual keywords
            subj_words = set(active_subject.lower().split())
            extra_kws = [
                kw for kw in scene_kws[:1]
                if scene_type != "history"
                and kw.lower() not in subj_words
                and kw.lower() not in BAD_QUERY_TERMS
                and kw.lower() not in _NON_NAME_WORDS
            ]
            medium_query = " ".join([active_subject, action] + extra_kws)

            # General Query: subject + category/sport
            category_mapping = {
                "gaming": "gameplay screenshot",
                "sports": "cricket" if _is_cricket_content(active_subject, effective_scene_text) else "sports action",
                "person": "portrait",
                "history": "historical archive",
                "nature": "nature",
                "city": "city",
                "place": "landmark photograph",
                "object": "product detail photograph",
                "food": "food photograph",
                "vehicle": "photograph",
                "thing": "photograph",
            }
            cat_term = category_mapping.get(
                query_scene_type,
                _default_scene_term(query_scene_type, active_subject, effective_scene_text),
            )
            general_query = f"{active_subject} {cat_term}"

            # Fallbacks
            if entity_kind == "person":
                fallback_queries = [
                    f"{active_subject} portrait",
                    f"{active_subject} photo",
                    f"{active_subject} official event photograph",
                ]
            elif entity_kind == "place":
                fallback_queries = [
                    f"{active_subject} landmark photograph",
                    f"{active_subject} location photo",
                    f"{active_subject} travel photograph",
                ]
            elif entity_kind == "object":
                fallback_queries = [
                    f"{active_subject} product photograph",
                    f"{active_subject} detail photo",
                    f"{active_subject} reference image",
                ]
            elif entity_kind == "food":
                fallback_queries = [
                    f"{active_subject} food photograph",
                    f"{active_subject} fresh produce photo",
                    f"{active_subject} recipe ingredient photograph",
                ]
            elif query_scene_type == "history":
                fallback_queries = [
                    f"{active_subject} historical archive",
                    f"{active_subject} old archive photo",
                    f"{active_subject} historical photograph",
                ]
            elif query_scene_type == "vehicle":
                fallback_queries = [
                    f"{active_subject} photograph",
                    f"{active_subject} detail photo",
                    f"{active_subject} vintage photo",
                ]
            else:
                fallback_queries = _topic_fallback_queries(active_subject, effective_scene_text, limit=3) or [active_subject]
            fb1 = fallback_queries[0]
            fb2 = fallback_queries[1] if len(fallback_queries) > 1 else active_subject

            raw_queries = [specific_query, medium_query, general_query, fb1, fb2]
            raw_queries.extend(_build_multi_wave_documentary_mutations(active_subject, effective_scene_text, title))
            
            # Clean and deduplicate queries
            seen_q = set()
            for q in raw_queries:
                cleaned = _clean_query(q)
                if cleaned and len(cleaned) > 2 and not _has_bad_terms(cleaned):
                    if cleaned.lower() not in seen_q:
                        seen_q.add(cleaned.lower())
                        queries.append(cleaned)
            
            # Ensure at least 3 queries
            if len(queries) < 3:
                if entity_kind == "person":
                    extra_fallbacks = [
                        f"{active_subject} photo",
                        f"{active_subject} portrait",
                        f"{active_subject} official event photograph",
                    ]
                elif entity_kind == "place":
                    extra_fallbacks = [
                        f"{active_subject} location photo",
                        f"{active_subject} landmark photograph",
                        f"{active_subject} skyline landscape photo",
                    ]
                elif entity_kind == "object":
                    extra_fallbacks = [
                        f"{active_subject} detail photo",
                        f"{active_subject} product photograph",
                        f"{active_subject} official reference image",
                    ]
                elif entity_kind == "food":
                    extra_fallbacks = [
                        f"{active_subject} fresh produce photo",
                        f"{active_subject} food photograph",
                        f"{active_subject} recipe ingredient photograph",
                    ]
                elif query_scene_type == "history":
                    extra_fallbacks = [
                        f"{active_subject} historical archive",
                        f"{active_subject} old archive photo",
                        f"{active_subject} historical photograph",
                    ]
                elif query_scene_type == "vehicle":
                    extra_fallbacks = [
                        f"{active_subject} photograph",
                        f"{active_subject} detail photo",
                        f"{active_subject} vintage photo",
                    ]
                else:
                    extra_fallbacks = _topic_fallback_queries(active_subject, effective_scene_text, limit=5)
                for fallback in extra_fallbacks:
                    cleaned = _clean_query(fallback)
                    if cleaned.lower() not in seen_q:
                        seen_q.add(cleaned.lower())
                        queries.append(cleaned)
        else:
            scene_queries = []
            seen_q = set()
            q_spec = " ".join(scene_kws[:3]) if len(scene_kws) >= 3 else " ".join(scene_kws)
            q_med = " ".join(scene_kws[:2]) if len(scene_kws) >= 2 else (scene_kws[0] if scene_kws else "")
            q_gen = scene_kws[0] if scene_kws else "photo"
            fb1 = f"{q_gen} photo" if q_gen != "photo" else "photo"
            fb2 = "photo"
            
            for q in [q_spec, q_med, q_gen, fb1, fb2]:
                cleaned = _clean_query(q)
                if cleaned and len(cleaned) > 2:
                    if cleaned.lower() not in seen_q:
                        seen_q.add(cleaned.lower())
                        scene_queries.append(cleaned)
            queries = scene_queries

        queries = list(dict.fromkeys(q for q in queries if q and len(q) > 2))[:5]
        if not queries:
            if active_subject:
                queries = (
                    [f"{active_subject} portrait", f"{active_subject} photo", f"{active_subject} official event photograph"]
                    if entity_kind == "person"
                    else (
                        [f"{active_subject} landmark photograph", f"{active_subject} location photo", f"{active_subject} travel photograph"]
                        if entity_kind == "place"
                        else (
                            [f"{active_subject} product photograph", f"{active_subject} detail photo", f"{active_subject} reference image"]
                            if entity_kind == "object"
                            else (
                                [f"{active_subject} food photograph", f"{active_subject} fresh produce photo", f"{active_subject} recipe ingredient photograph"]
                                if entity_kind == "food"
                                else (
                                    [f"{active_subject} historical archive", f"{active_subject} old archive photo", f"{active_subject} historical photograph"]
                                    if query_scene_type == "history"
                                    else (
                                        [f"{active_subject} photograph", f"{active_subject} detail photo", f"{active_subject} vintage photo"]
                                        if query_scene_type == "vehicle"
                                        else _topic_fallback_queries(active_subject, scene_text, limit=5)
                                    )
                                )
                            )
                        )
                    )
                )
            else:
                queries = ["photo", "portrait"]

        rows.append(_make_row(idx + 1, queries, active_subject or title, active_subject or ""))

    return _enforce_royal_query_contract(rows, title, script, scene_texts)


def _extract_scene_entities(text: str, subject: str) -> Dict:
    """Extract year, opponent, tournament, score numbers from scene text."""
    entities = {
        "years": [],
        "numbers": [],
        "opponents": [],
        "tournaments": [],
        "shots": [],
    }
    if not text:
        return entities

    text_lower = text.lower()

    # Extract years (4-digit numbers 1900-2099)
    for m in re.finditer(r'\b(19\d{2}|20\d{2})\b', text):
        entities["years"].append(m.group())

    # Extract notable numbers (scores, ball counts)
    # Score patterns: "82 not out", "82*", "113 not out", "runs"
    score_matches = re.findall(r'(\d+)\s*(?:not out|\*|runs?|balls?|wickets?|fours?|sixes?)', text_lower)
    entities["numbers"] = [m for m in score_matches if len(m) <= 5]

    # Known cricket opponents
    opponents = {
        "australia", "australian", "england", "english", "india", "indian",
        "pakistan", "pakistani", "sri lanka", "sri lankan", "lanka",
        "west indies", "windies", "south africa", "south african",
        "new zealand", "new zealand", "bangladesh", "bangladeshi",
        "zimbabwe", "zimbabwean", "afghanistan", "afghan",
        "ireland", "scotland", "netherlands", "netherlands",
    }
    for opp in sorted(opponents, key=len, reverse=True):
        if opp in text_lower:
            entities["opponents"].append(opp.title())

    # Tournament names
    tournaments = {
        "world cup": "World Cup", "t20 world cup": "T20 World Cup",
        "asia cup": "Asia Cup", "ipl": "IPL", "champions trophy": "Champions Trophy",
        "test match": "Test Match", "ashes": "Ashes", "bgt": "BGT",
        "border gavaskar": "Border Gavaskar Trophy",
        "champions league": "Champions League", "pcl": "PCL",
        "psl": "PSL", "bbl": "BBL", "cpl": "CPL", "the hundred": "The Hundred",
        "county": "County", "sheffield shield": "Sheffield Shield",
        "ranji trophy": "Ranji Trophy", "duleep trophy": "Duleep Trophy",
        "world test": "World Test Championship", "wtc": "WTC",
        "icc": "ICC", "odi world cup": "ODI World Cup",
    }
    for kw, name in tournaments.items():
        if kw in text_lower:
            entities["tournaments"].append(name)

    # Remove duplicates while preserving order
    for k in entities:
        seen = set()
        deduped = []
        for v in entities[k]:
            if v not in seen:
                seen.add(v)
                deduped.append(v)
        entities[k] = deduped

    return entities


def _default_scene_term(scene_type: str, subject: str, scene_text: str) -> str:
    """Return a context-aware default visual term instead of generic 'stadium crowd'."""
    is_cricket = _is_cricket_content(subject, scene_text) if scene_text else False
    if scene_type == "sports":
        if is_cricket:
            return "cricket match"
        if re.search(r"\b(football|soccer|fifa|world cup)\b", f"{subject} {scene_text}".lower()):
            return "football match"
        return "sports action"
    if scene_type == "nature":
        return "nature landscape"
    if scene_type == "city":
        return "city skyline"
    if scene_type == "place":
        return "landmark location"
    if scene_type == "object":
        return "product detail"
    if scene_type == "person":
        return "portrait"
    return subject if subject else "stock photo"


def _is_cricket_content(subject: str, scene_text: str) -> bool:
    """Detect if the subject or scene text is about cricket."""
    combined = f"{subject} {scene_text}".lower()
    if re.search(r"\b(fifa|football|soccer|fanbase|supporters|world cup ticket|world cup fans|2026 world cup|iranian football)\b", combined):
        return False
    cricket_keywords = {
        "cricket", "cricketer", "batsman", "bowler", "fielder", "wicket",
        "stumps", "crease", "innings", "batting", "bowling", "fielding",
        "century", "boundary",
        "bat", "ball", "pitch", "test match", "t20", "odi", "ipl",
        "cover drive", "pull shot", "straight drive", "square cut",
        "leg glance", "on drive", "sweep", "reverse sweep",
        "kohli", "sachin", "dhoni", "rohit", "sharma", "bumrah",
        "ashwin", "jadeja", "gill", "iyer", "rahul", "pant",
        "dravid", "laxman", "sehwag", "gambhir", "ganguly", "yuvraj",
        "harbhajan", "zaheer", "kumble", "shastri", "kapil",
        "imran", "wasim", "akram", "waqar", "younis", "miandad", "afridi",
        "babar", "azam", "rizwan", "shaheen", "shoaib", "akhtar", "saeed",
        "anwar", "yousuf", "younus", "inzamam", "inzemam", "sarfaraz", "shadab",
        "fakhar", "naseem", "psl", "pcb",
    }
    return any(re.search(rf"\b{re.escape(kw)}\b", combined) for kw in cricket_keywords)


def _pick_enrichments(subject: str, scene_type: str,
                       scene_visual: List[str], scene_idx: int,
                       scene_text: str = "") -> List[str]:
    """Pick 2-3 visual enrichment words for a scene's queries.
    
    Extracts scene-specific action phrases from scene text first,
    then falls back to VISUAL_ENRICHMENTS pool.
    When cricket content is detected, cross-sport terms are filtered out.
    """
    is_cricket = _is_cricket_content(subject, scene_text) if scene_text else False
    is_football_world_cup = bool(re.search(
        r"\b(fifa|football|soccer|world cup|premier league|champions league)\b",
        f"{subject} {scene_text}".lower(),
    )) and not is_cricket
    
    picked = []
    subj_words = set(subject.lower().split()) if subject else set()
    seen_lower = set(w.lower() for w in subj_words)

    if scene_type == "history":
        for term in _history_visual_terms(scene_text):
            if term.lower() not in seen_lower:
                picked.append(term)
            if len(picked) >= 2:
                break
        if len(picked) >= 2:
            return picked[:3]

    # Phase 1: Extract scene-specific multi-word action phrases from scene text.
    # Only extract if at least one word in the pair expresses a concrete visual action.
    _VISUAL_ACTION_WORDS = {
        "cover", "drive", "pull", "shot", "cut", "sweep", "hook", "glance",
        "bowl", "bowling", "spin", "pace", "throw", "catch", "field",
        "dive", "six", "four", "boundary", "century", "wicket", "stump",
        "hit", "bat", "swing", "smash", "batting",
        "celebrate", "celebration", "fist", "pump", "hug", "wave",
        "practice", "training", "warmup", "stretch",
        "helmet", "pad", "glove", "kit", "uniform", "jersey",
        "trophy", "medal", "award", "ceremony", "handshake",
        "conference", "interview", "press", "speech", "talk",
        "walk", "run", "running", "sprint", "jump", "leap",
        "score", "runs", "over", "innings", "match", "cup", "test",
        "ground", "stadium", "pitch", "field", "net", "session",
        "fans", "supporters", "tickets", "visa", "border", "flags",
        "chants", "crowd", "seats", "empty", "travel", "airport",
    }
    
    if scene_text:
        text_lower = scene_text.lower()
        # Words that when NOT visual, make the whole phrase bad for search
        _CONNECTOR_WORDS = {
            "against", "during", "through", "between", "without", "within",
            "about", "above", "below", "behind", "across", "around",
            "after", "before", "since", "until", "while", "because",
            "into", "onto", "upon", "under", "over", "along", "among",
            "with", "from", "that", "this", "these", "those", "which",
            "what", "when", "where", "their", "them", "they", "then",
            "very", "just", "also", "been", "being", "having",
            "made", "make", "makes", "making", "took", "take", "takes",
            "showed", "shows", "show", "showing", "given", "gives",
            "came", "come", "goes", "gone", "went",
        }
        # Find all individual words first, then generate OVERLAPPING adjacent pairs
        all_words = re.findall(r'[a-z]{3,}', text_lower)
        # Prefer an action explicitly present in this scene over the rotating
        # enrichment pool.  Without this guard a scene saying ``bats against
        # Australia`` could receive the next pool item ``bowling`` simply
        # because it was scene number two.
        action_aliases = {
            "bats": "batting", "bat": "batting", "batted": "batting",
            "bowls": "bowling", "bowled": "bowling", "fields": "fielding",
            "fielded": "fielding", "catches": "catch", "caught": "catch",
            "scores": "scoring", "scored": "scoring", "celebrates": "celebration",
            "celebrated": "celebration", "trains": "training", "trained": "training",
            "trophy": "trophy", "trophies": "trophy", "medal": "medal", "medals": "medal",
            "award": "award", "awards": "award", "stadium": "stadium", "match": "match",
        }
        for word in all_words:
            alias = action_aliases.get(word)
            if alias and alias not in {item.casefold() for item in picked}:
                if not (is_cricket and alias in {term.casefold() for term in NON_CRICKET_SPORTS_TERMS}):
                    picked.append(alias)
            if len(picked) >= 2:
                break
        candidate_phrases = []
        for i in range(len(all_words) - 1):
            w1, w2 = all_words[i], all_words[i + 1]
            if w1 in STOPWORDS or w2 in STOPWORDS:
                continue
            if w1 in BAD_QUERY_TERMS or w2 in BAD_QUERY_TERMS:
                continue
            # If the non-visual word is a connector, the whole phrase is bad
            if w1 not in _VISUAL_ACTION_WORDS and w2 not in _VISUAL_ACTION_WORDS:
                continue
            if w1 not in _VISUAL_ACTION_WORDS and w1 in _CONNECTOR_WORDS:
                continue
            if w2 not in _VISUAL_ACTION_WORDS and w2 in _CONNECTOR_WORDS:
                continue
            phrase = f"{w1} {w2}"
            pl = phrase.lower()
            is_subj = any(s in pl for s in subj_words)
            if is_subj or pl in [p.lower() for p in picked]:
                continue
            if is_cricket:
                ws = set(pl.split())
                if ws & set(w.lower() for w in NON_CRICKET_SPORTS_TERMS):
                    continue
            # Score: +2 if both words are visual, +1 if one is visual
            visual_score = (2 if w1 in _VISUAL_ACTION_WORDS and w2 in _VISUAL_ACTION_WORDS
                           else 1)
            length_bonus = 0.5 if len(w1) >= 5 or len(w2) >= 5 else 0
            candidate_phrases.append((phrase, visual_score + length_bonus))
        # Sort by score (best first) then take top 2
        candidate_phrases.sort(key=lambda x: x[1], reverse=True)
        for action, _ in candidate_phrases:
            al = action.lower()
            if al in [p.lower() for p in picked]:
                continue
            picked.append(action)
            if len(picked) >= 2:
                break

    # Phase 2: Fill from enrichment pool
    if is_football_world_cup:
        enrichments_pool = [
            "fans stadium", "supporters flags", "empty seats", "stadium crowd",
            "press conference", "stadium exterior",
        ]
    elif scene_type == "gaming":
        enrichments_pool = [
            "gameplay screenshot", "official key art", "trailer still",
            "in-game environment", "character artwork", "official reveal",
        ]
    elif scene_type == "history":
        enrichments_pool = [
            "historical archive", "old photograph", "historic harbor", "documentary photograph",
            "archival newspaper", "ship exterior", "documentary still",
        ]
    elif scene_type == "vehicle":
        enrichments_pool = [
            "exterior photograph", "interior detail", "side profile view",
            "vintage photograph", "road action shot", "dashboard detail",
        ]
    elif scene_type == "place":
        enrichments_pool = [
            "landmark view", "location photograph", "street view", "skyline landscape",
            "architecture detail", "travel establishing shot",
        ]
    elif scene_type == "object":
        enrichments_pool = [
            "product detail", "close up photograph", "official product image",
            "in use photograph", "design detail", "reference photograph",
        ]
    elif scene_type == "food":
        enrichments_pool = [
            "fresh produce", "food close up", "market photograph",
            "recipe ingredient", "prepared dish", "nutrition reference",
        ]
    else:
        enrichments_pool = list(VISUAL_ENRICHMENTS.get(scene_type, VISUAL_ENRICHMENTS["person"]))
    general_pool = list(VISUAL_ENRICHMENTS.get("general", []))
    
    # Remove terms already used or overlapping with subject
    enrichments_pool = [
        w for w in enrichments_pool
        if w.lower() not in subj_words and w.lower() not in seen_lower
    ]
    
    # Filter out non-cricket sports terms for cricket subjects
    if is_cricket:
        enrichments_pool = [w for w in enrichments_pool if w.lower() not in set(w.lower() for w in NON_CRICKET_SPORTS_TERMS)]

    if enrichments_pool:
        offset = (scene_idx + len(picked)) % max(len(enrichments_pool), 1)
        for i in range(len(enrichments_pool)):
            if len(picked) >= 3:
                break
            candidate = enrichments_pool[(i + offset) % len(enrichments_pool)]
            cl = candidate.lower()
            if cl in seen_lower:
                continue
            if is_cricket and cl in NON_CRICKET_SPORTS_TERMS:
                continue
            picked.append(candidate)
            seen_lower.update(candidate.lower().split())

    # Phase 3: Fallback to general pool
    if len(picked) < 2:
        for g in general_pool:
            gl = g.lower()
            if gl in subj_words or gl in seen_lower or g in picked:
                continue
            if is_cricket and gl in NON_CRICKET_SPORTS_TERMS:
                continue
            picked.append(g)
            if len(picked) >= 3:
                break

    return picked[:3]


def _make_row(scene_num: int, queries: List[str], topic_anchor: str, section_subject: str = "") -> Dict:
    """Build a query row dict from a list of query strings."""
    return {
        "scene_number": scene_num,
        "search_query": queries[0] if queries else "photo",
        "supporting_search_query": queries[1] if len(queries) > 1 else (queries[0] if queries else "photo"),
        "alt_search_query": queries[2] if len(queries) > 2 else (queries[0] if queries else "photo"),
        "topic_anchor": topic_anchor,
        "topic_tag": "general",
        "video_topic_tag": "general",
        "subject_query": topic_anchor,
        "scene_family": "person_editorial",
        "disambiguation_terms": [],
        "source_strategy": "engine-first",
        "query_variants": queries,
        "section_subject": section_subject,
    }


def _dedupe_terms(values: List[str]) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for value in values or []:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _query_contract_terms(topic: str, script: str, scene_texts: List[str]) -> Dict[str, Any]:
    """Build lightweight validation hints so LLM output stays inside the script topic."""
    context = " ".join([str(topic or ""), str(script or ""), " ".join(scene_texts or [])])
    lower = context.lower()
    anchor = _extract_main_subject(f"{topic}\n{script}") or str(topic or "").strip()
    disaster = _detect_news_sports_topic_subject(context)
    if disaster and re.search(r"\b(earthquakes?|quake|floods?|hurricane|tsunami|wildfire|landslide|volcanic)\b", disaster.lower()):
        anchor = disaster
    anchor_terms = [
        term for term in re.findall(r"[a-z0-9]{3,}", anchor.lower())
        if term not in STOPWORDS and term not in BAD_QUERY_TERMS and term not in _NON_NAME_WORDS
    ]
    niche = _topic_family(anchor, context)
    if re.search(r"\b(earthquakes?|quake|seismic|tremors?|aftershocks?|floods?|hurricanes?|tornadoes?|tsunamis?|wildfires?|landslides?|volcanic|eruption)\b", lower):
        niche = "disaster"
    if re.search(r"\b(psychology|brain|dopamine|habit|habits|heuristic|cognitive|neural)\b", lower):
        niche = "psychology"
    if re.search(r"\b(ai|software|robot|server|chip|app|technology|startup)\b", lower):
        niche = "technology"
    anchor_is_named = bool(re.search(r"\b[A-Z][A-Za-z]{2,}(?:\s+[A-Z][A-Za-z]{2,}){0,3}\b", anchor))
    concept_niches = {"finance", "science", "psychology", "technology", "nature", "general"}
    is_listicle = _is_listicle_script(script, topic, scene_texts)
    preserve_anchor = bool(
        not is_listicle and (
            niche in {"disaster", "sports", "politics", "history", "travel"}
            or (niche not in concept_niches and anchor_is_named)
            or _is_person_fallback_subject(anchor)
        )
    )
    return {
        "anchor": anchor,
        "anchor_terms": _dedupe_terms(anchor_terms)[:6],
        "niche": niche,
        "preserve_anchor": preserve_anchor,
    }


def _query_preserves_contract(query: str, contract: Dict[str, Any]) -> bool:
    cleaned = str(query or "").lower()
    if not cleaned:
        return False
    niche = str(contract.get("niche") or "").lower()
    anchor_terms = [term for term in contract.get("anchor_terms", []) if term]
    if niche == "disaster":
        has_disaster = bool(re.search(
            r"\b(earthquakes?|quake|seismic|tremors?|aftershocks?|floods?|hurricanes?|tornadoes?|tsunamis?|wildfires?|landslides?|volcanic|eruption|disaster)\b",
            cleaned,
        ))
        location_terms = [
            term for term in anchor_terms
            if term not in {"earthquake", "earthquakes", "quake", "seismic", "tremor", "tremors", "disaster", "damage", "aftermath"}
        ]
        has_location = not location_terms or any(term in cleaned for term in location_terms)
        if not (has_disaster and has_location):
            return False
    if contract.get("preserve_anchor") and anchor_terms and len(anchor_terms) <= 4:
        strong_terms = [term for term in anchor_terms if term not in GENERIC_TOPIC_WORDS]
        if strong_terms and not any(term in cleaned for term in strong_terms):
            return False
    return True


def _normalize_llm_query_rows(raw_rows: Optional[List[Dict]], scene_texts: List[str],
                              topic: str, script: str, main_subject: str,
                              per_scene_subjects: Optional[List[str]] = None) -> Tuple[List[Dict], List[str]]:
    contract = _query_contract_terms(topic, script, scene_texts)
    subject = main_subject or contract.get("anchor") or topic
    rows: List[Dict] = []
    errors: List[str] = []
    if not isinstance(raw_rows, list) or not raw_rows:
        return [], ["LLM returned no query rows"]

    is_listicle = _is_listicle_script(script, topic, scene_texts)
    for i, scene_text in enumerate(scene_texts):
        qdata = raw_rows[i] if i < len(raw_rows) else {}
        if not isinstance(qdata, dict):
            qdata = {}
            errors.append(f"scene {i + 1}: row is not an object")

        raw_queries: List[str] = []
        for key in ("specific", "medium", "general", "fallback_1", "fallback_2", "primary", "supporting", "fallback", "alt", "query"):
            value = qdata.get(key)
            if isinstance(value, list):
                raw_queries.extend(str(item) for item in value if item)
            elif value:
                raw_queries.append(str(value))
        if isinstance(qdata.get("query_variants"), list):
            raw_queries.extend(str(item) for item in qdata.get("query_variants") if item)

        qlist: List[str] = []
        seen_q: Set[str] = set()
        for raw_query in raw_queries:
            cleaned = _clean_query(raw_query)
            if not cleaned or len(cleaned) <= 2 or _has_bad_terms(cleaned):
                continue
            if not _query_preserves_contract(cleaned, contract):
                continue
            key = cleaned.lower()
            if key not in seen_q:
                seen_q.add(key)
                qlist.append(cleaned)

        # Use per-scene subject when available (stronger signal than global subject)
        effective_subject = subject
        if per_scene_subjects and i < len(per_scene_subjects) and per_scene_subjects[i] and len(per_scene_subjects[i]) >= 2:
            effective_subject = per_scene_subjects[i]

        repaired = _repair_query_pack(qlist, effective_subject, scene_text, limit=5, context_text=f"{topic} {script[:3000]}")
        if len(repaired) < 3:
            errors.append(f"scene {i + 1}: only {len(repaired)} contract-safe query/query variants")
        row = _make_row(i + 1, repaired[:5], effective_subject)
        row["_llm"] = True
        row["llm_niche"] = contract.get("niche", "general")
        # Inject per-scene subject into the row for downstream anchor checking
        if per_scene_subjects and i < len(per_scene_subjects) and per_scene_subjects[i]:
            row["scene_subject"] = per_scene_subjects[i]
            row["subject_query"] = per_scene_subjects[i]
        rows.append(row)

    return rows, errors


def _chat_query_llm(llm, user_prompt: str, system_prompt: str, *,
                    temperature: float, max_tokens: int,
                    timeout_seconds: float, max_attempts: int) -> str:
    try:
        return llm.chat(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
        )
    except TypeError:
        return llm.chat(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )


# =============================================================================
#  PER-SCENE ENTITY ANALYSIS (universal — for ALL video types)
# =============================================================================

# Discourse transition markers that signal subject changes between scenes
_DISCOURSE_TRANSITIONS = {
    "next", "another", "also", "plus", "meanwhile", "mean while",
    "elsewhere", "however", "however", "meanwhile",
    "moving", "turning", "switching",
    "instead", "rather", "compared",
    "first", "firstly", "second", "secondly", "third", "thirdly",
    "finally", "lastly", "last", "additionally", "furthermore",
    "moreover", "besides", "then", "afterwards", "subsequently",
    "consequently", "accordingly", "regarding",
    "consider", "imagine", "picture",
    "taking", "coming", "entering", "ranking", "landing", "placing",
    "number", "spot", "position", "place", "rank",
    "top", "list", "listicle", "rundown", "roundup",
}

# Words that are too generic to be scene entities
_GENERIC_NOUNS = {
    "health", "healthy", "benefit", "benefits", "nutrition",
    "nutritional", "vitamin", "vitamins", "mineral", "minerals",
    "antioxidant", "antioxidants", "fruit", "fruits", "vegetable",
    "vegetables", "food", "foods", "diet", "wellness", "nutrient",
    "nutrients", "property", "properties", "effect", "effects",
    "impact", "advantage", "advantages", "superfood", "superfoods",
    "people", "person", "human", "life", "world", "thing", "things",
    "way", "ways", "time", "times", "day", "days", "year", "years",
    "idea", "ideas", "concept", "concepts", "topic", "topics",
    "subject", "subjects", "item", "items", "factor", "factors",
    "aspect", "aspects", "element", "elements", "part", "parts",
    "type", "types", "kind", "kinds", "sort", "sorts",
    "example", "examples", "reason", "reasons", "result", "results",
    "hand", "hands", "point", "points", "case", "cases",
    "number", "numbers", "spot", "spots", "position", "positions",
    "place", "places", "rank", "ranks", "list", "lists",
}

def analyze_script_entities(
    script: str,
    topic: str,
    scene_texts: List[str],
    list_sections: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict]:
    """Analyze script to determine per-scene focal entities for ALL video types.

    Returns a list of dicts, one per scene:
      - primary_entity: str — the focal entity for this scene
      - secondary_entities: List[str] — other entities mentioned
      - scene_role: str — intro | entity_focus | comparison | transition | outro
      - confidence: float — 0.0-1.0 how confident we are in primary_entity
      - is_distinct: bool — whether this entity differs from the previous scene
      - visual_keywords: List[str] — concrete visual terms from the scene

    Uses a four-strategy approach:
      0. list_sections markers (preemptive detection) — strongest signal (if provided)
      1. Proper noun phrases (capitalized words) — strong signal
      2. Pattern-based detection ("is the X", "at number X is")
      3. Noun frequency analysis — picks entity-like nouns across scenes
    """
    if not scene_texts:
        return []

    _GENERIC = {
        "the", "a", "an", "this", "that", "these", "those", "it", "its",
        "they", "them", "their", "he", "she", "his", "her", "our", "we",
        "you", "your", "and", "or", "but", "for", "with", "without",
        "from", "into", "onto", "upon", "during", "before", "after",
        "above", "below", "over", "under", "again", "once", "then",
        "than", "there", "here", "what", "when", "where", "why", "how",
        "which", "who", "whom", "very", "just", "also", "not", "no",
        "yes", "maybe", "still", "yet", "already", "ever", "never",
        "now", "always", "often", "usually", "generally", "typically",
        "actually", "basically", "literally", "really", "quite", "rather",
        "pretty", "fairly", "totally", "simply", "more", "most", "less",
        "least", "some", "any", "every", "each", "all", "both", "few",
        "several", "many", "much", "other", "another", "top", "best",
        "worst", "great", "better", "worse",
    } | _GENERIC_NOUNS

    n_scenes = len(scene_texts)

    # Step 1: Extract words from each scene (both raw and filtered)
    scene_words: List[List[str]] = []
    scene_all_words: List[List[str]] = []
    for st in scene_texts:
        words = re.findall(r"[a-zA-Z][a-zA-Z']*", st.lower())
        raw_words = [w.strip("'") for w in words if len(w.strip("'")) >= 3]
        cleaned = [w for w in raw_words if w not in _GENERIC]
        scene_words.append(cleaned)
        scene_all_words.append(raw_words)

    # Step 2: Document frequency
    doc_freq: Dict[str, int] = {}
    for words in scene_words:
        for w in set(words):
            doc_freq[w] = doc_freq.get(w, 0) + 1

    # Step 3: Per-scene entity selection using multi-strategy
    results: List[Dict] = []
    for i, (st_orig, words) in enumerate(zip(scene_texts, scene_words)):
        st_lower = st_orig.lower()
        candidates = []

        # Strategy 0: Preemptive section markers (highest priority — confidence 10.0)
        if list_sections and i < len(list_sections):
            section_marker = list_sections[i]
            container_subject = section_marker.get("container_subject", "")
            if container_subject and not section_marker.get("is_intro", False):
                candidates.append((container_subject, 10.0))

        # Strategy A: Proper noun phrases (capitalized multi-word entities)
        proper_phrases = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b", st_orig)
        for phrase in proper_phrases:
            lower = phrase.lower()
            if lower not in _GENERIC and len(phrase) >= 5 and phrase.split()[0].lower() not in _GENERIC:
                candidates.append((phrase, 5.0))

        # Strategy B: "is the X" / "are the X" / "is X" / "are X" patterns
        intro_match = re.search(r"\b(?:is|are)\s+(?:the\s+|a\s+|an\s+)?([A-Za-z]{3,})", st_orig, re.IGNORECASE)
        if intro_match:
            candidate = intro_match.group(1).lower()
            # Check that it doesn't end with verb-like suffix
            if candidate not in _GENERIC and not candidate.endswith(("ed", "ing", "ly", "ment", "tion", "sion")):
                # Look up original capitalization
                orig = intro_match.group(1)
                score = 5.0 + (1.0 if orig[0].isupper() else 0.0)
                candidates.append((orig, score))

        # Strategy C: Single capitalized words at start of scene
        first_cap = re.match(r"^[^a-z]*([A-Z][a-z]{2,})\b", st_orig)
        if first_cap:
            c = first_cap.group(1)
            if c.lower() not in _GENERIC:
                candidates.append((c, 3.5))

        # Strategy E: Word BEFORE "is/are/was/were/has/have" — entity in copular/possessive pattern.
        # Works for "android is more open", "ios has a walled garden".
        entity_before = re.search(r"\b([A-Za-z]{3,})\s+(?:is|are|was|were|has|have|became)\b", st_orig, re.IGNORECASE)
        if entity_before:
            candidate = entity_before.group(1)
            lower = candidate.lower()
            if lower not in _GENERIC and not lower.endswith(("ed", "ing", "ly", "ment", "tion", "sion")):
                score = 3.5 + (1.0 if candidate[0].isupper() else 0.0)
                candidates.append((candidate, score))

        # Strategy F: If scene starts with pronoun (he/she/they), inherit previous scene's entity
        starts_with_pronoun = bool(re.match(r"^\s*(?:he|she|they|it|this|that|these|those)\b", st_orig, re.IGNORECASE))
        if starts_with_pronoun and results:
            prev_entity = results[-1].get("primary_entity", "")
            if prev_entity:
                candidates.append((prev_entity, 3.0))

        # Strategy D: Entity detection via position + cross-scene frequency.
        # Works for both lowercase ASR and capitalized LLM text.
        # Real entities tend to appear near the start of each scene AND across
        # multiple scenes (they're the subject being discussed).
        # Description words (criminal, international, customizable) are usually
        # unique to one scene and appear later in the text.
        for w in set(words):
            df = doc_freq.get(w, n_scenes)
            in_all_scenes = df >= n_scenes

            # Skip verb/adjective suffixes
            if w.endswith(("ed", "ing", "ly", "ment", "tion", "sion", "ness", "ity", "able", "ible", "ive")):
                continue

            # Position within scene: words near the start are more likely to be entities
            try:
                first_pos = [j for j, w2 in enumerate(words) if w2 == w][0]
            except IndexError:
                first_pos = 99
            if first_pos < 3:
                pos_bonus = 3.0
            elif first_pos < 6:
                pos_bonus = 2.0
            elif first_pos < 10:
                pos_bonus = 1.3
            else:
                pos_bonus = 1.0

            # Frequency bonus: words appearing in multiple scenes are likely entities;
            # words in ALL scenes are global topic noise;
            # words in ONE scene are often scene-specific descriptions.
            if in_all_scenes:
                df_bonus = 0.1  # Global topic noise
            elif df >= max(3, n_scenes // 2):
                df_bonus = 2.0  # Appears in most scenes — entity
            elif df >= 2:
                df_bonus = 1.5  # Appears in several scenes — entity candidate
            else:
                df_bonus = 0.5  # Unique — weak signal (might be a description)

            # Capitalization indicates proper noun
            cap_match = re.search(rf"\b{w[0].upper() + w[1:]}\b", st_orig)
            cap_bonus = 2.0 if cap_match else 1.0
            cap_form = cap_match.group(0) if cap_match else w

            # Words after "is a", "was a", "became" are likely descriptions
            desc_penalty = 0.5 if re.search(rf"\b(?:is|was|are|were|became)\s+(?:a|an|the\s+)?{re.escape(w)}\b", st_lower) else 1.0

            score = pos_bonus * df_bonus * cap_bonus * desc_penalty
            candidates.append((cap_form, score))

        if not candidates:
            results.append({
                "primary_entity": "",
                "secondary_entities": [],
                "scene_role": "entity_focus",
                "confidence": 0.0,
                "is_distinct": False,
                "visual_keywords": [],
            })
            continue

        # Pick best candidate (highest score)
        candidates.sort(key=lambda x: -x[1])
        best_word = candidates[0][0]
        best_score = candidates[0][1]

        # Determine if entity is distinct from previous scene
        prev_best = results[-1].get("primary_entity", "").lower() if results else ""
        curr_best = best_word.lower()
        raw_words = scene_all_words[i]
        has_transition = any(w in _DISCOURSE_TRANSITIONS for w in raw_words[:8])
        is_distinct = bool(
            curr_best != prev_best
            and curr_best not in _GENERIC_NOUNS
            and (has_transition or doc_freq.get(curr_best, n_scenes) <= 2)
        )

        # Calculate confidence before using it to classify a transition.  A
        # low-confidence scene used to hit the ``confidence < 0.5`` branch
        # before assignment, raising UnboundLocalError and aborting the web
        # rescue lane for the entire job.
        confidence = min(1.0, best_score / 4.0)
        if best_word and len(best_word) >= 3:
            confidence = max(confidence, 0.3)

        # Determine scene role
        role = "entity_focus"
        if i == 0 and n_scenes > 1:
            role = "intro"
        elif i == n_scenes - 1 and n_scenes > 1:
            role = "outro"
        elif is_distinct and has_transition and confidence < 0.5:
            role = "transition"

        # Extract visual keywords (concrete, non-entity words unique to this scene)
        visual_keywords = [
            w for w in words[:12]
            if w != curr_best
            and doc_freq.get(w, n_scenes) <= 2
            and not w.endswith(("ed", "ing", "ly"))
        ][:4]

        results.append({
            "primary_entity": best_word,
            "secondary_entities": [w for w, _ in candidates[1:4] if w.lower() != curr_best],
            "scene_role": role,
            "confidence": confidence,
            "is_distinct": is_distinct,
            "visual_keywords": visual_keywords,
        })

    return results

_LISTICLE_PATTERNS = [
    r"\btop\s+\d+",
    r"\b\d+\s+(?:best|worst|greatest|biggest|smallest|most|least|top|highest|lowest)",
    r"\b(?:countdown|ranking|ranked)\b",
    r"\b(?:number|no\.)\s*\d+",
    # An ordinal on its own is common documentary prose ("first officer",
    # "first voyage") and must not turn the whole script into a listicle.
    # Require a list marker/context so query repair keeps scene-specific
    # subjects for ordinary narratives.
    r"\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s*(?:[:\-]|(?:place|spot|item|entry|reason|way|thing)\b)",
    r"\b(?:#|number)\s*\d+\b",
    r"\b(?:list|listicle|rundown|roundup)\b",
]

# Additional patterns for implicit listicles (no explicit "top 10" markers)
_IMPLICIT_LISTICLE_PATTERNS = [
    r"\b(?:another\s+(?:great|key|important|essential|popular|must-try|nutritious|delicious|fantastic|incredible|beneficial))\b",
    r"\b(?:next\s+(?:up|on|is|comes?|we['']ve\s+got))\b",
    r"\b(?:moving\s+(?:on\s+)?to|turning\s+to|switching\s+(?:gears|topics|to))\b",
    r"\b(?:let['']s\s+(?:not\s+)?(?:forget|talk\s+about|discuss|explore|look\s+at|dive\s+into))\b",
    r"\b(?:when\s+it\s+comes\s+to)\b",
    r"\b(?:meanwhile|elsewhere)\b",
    r"\bwhat\s+if\b",
    r"\b(?:instead\s+of|rather\s+than|compared\s+to)\b",
    # "from Southampton to New York" is ordinary documentary geography;
    # it is not evidence of a numbered list. Explicit list markers above and
    # the remaining discourse transitions provide safer implicit detection.
]

def _detect_entity_sequence(scene_texts: List[str]) -> bool:
    """Detect if scene texts mention different distinct entities in sequence.
    This catches implicit listicles where each scene covers a different item
    without explicit 'top 10' language (e.g. 'bananas...', 'apples...', 'oranges...').
    Handles lowercase ASR text and sentences with discourse transitions.
    """
    if len(scene_texts) < 3:
        return False

    _DETECT_STOP = {
        "the", "this", "that", "these", "those", "and", "but", "for", "with",
        "from", "they", "them", "their", "you", "your", "its", "was", "were",
        "has", "have", "had", "not", "are", "can", "will", "would", "could",
        "should", "when", "while", "just", "like", "very", "also", "now", "then",
        "next", "first", "second", "third", "fourth", "fifth", "another", "other",
        "many", "much", "some", "more", "most", "every", "each", "both",
        "health", "healthy", "benefit", "benefits", "fruit", "fruits", "food",
        "diet", "better", "great", "good", "best", "top", "real",
        "instead", "meanwhile", "elsewhere", "later", "earlier", "however",
        "therefore", "furthermore", "otherwise", "nevertheless", "nonetheless",
        "moreover", "besides", "consequently", "accordingly", "additionally",
        "after", "before", "during", "within", "without", "beyond", "until",
    }

    def _scene_candidates(text: str) -> List[str]:
        words = re.findall(r"[a-zA-Z][a-zA-Z']*", text)
        return [
            w.lower().strip("'") for w in words
            if len(w) >= 3 and w.lower().strip("'") not in _DETECT_STOP
        ]

    all_candidates = []
    for st in scene_texts:
        all_candidates.append(_scene_candidates(st))

    # Build frequency map of candidate words across ALL scenes
    freq: Dict[str, int] = {}
    for candidates in all_candidates:
        seen_in_scene = set()
        for c in candidates:
            if c not in seen_in_scene:
                freq[c] = freq.get(c, 0) + 1
                seen_in_scene.add(c)

    # For each scene, pick the candidate with the LOWEST global frequency
    # (the most scene-specific entity, avoiding the global topic word that
    #  appears in every scene's transition)
    scene_entities = []
    for candidates in all_candidates:
        if not candidates:
            continue
        # Sort by frequency ascending (prefer unique per-scene words)
        candidates.sort(key=lambda c: freq.get(c, 999))
        scene_entities.append(candidates[0])

    if len(scene_entities) < 3:
        return False
    unique_entities = set(scene_entities)
    return len(unique_entities) >= 3


def _is_listicle_script(script: str, topic: str = "", scene_texts: Optional[List[str]] = None) -> bool:
    """Detect if script follows a listicle/top-N format.
    Checks explicit patterns (top 10, first/second) and implicit patterns
    (distinct entity sequences across scenes).
    """
    text = f"{topic} {script}".lower()
    # Check explicit listicle patterns
    if any(re.search(p, text) for p in _LISTICLE_PATTERNS):
        return True
    # Check implicit listicle patterns (transition language)
    if any(re.search(p, text) for p in _IMPLICIT_LISTICLE_PATTERNS):
        return True
    # Check for distinct entity sequences across scenes (implicit listicle)
    if scene_texts and _detect_entity_sequence(scene_texts):
        return True
    return False


def _detect_list_sections(
    scene_texts: List[str],
    topic: str = "",
    script: str = "",
) -> List[Dict[str, Any]]:
    """Preemptive numbered-section detection that runs BEFORE query/contract generation.

    Returns a list of section markers, one per scene (same length as scene_texts).
    Each marker has:
      - index: int — scene index
      - container_id: str — "intro" | "section_1" | "section_2" | ...
      - container_subject: str — the extracted list item name (e.g. "Banana", "Apple")
      - marker_number: int — the list position (0 for intro)
      - has_explicit_marker: bool — whether this scene contains an explicit numbered marker
      - is_intro: bool — True if this scene is before the first numbered marker
    """
    n = len(scene_texts or [])
    if n == 0:
        return []

    # ---- Stage 1: Find all explicit numbered-markers and their subjects ----
    rank_words = (
        "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        "thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
        "first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
    )
    _word_to_num = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
        "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
        "nineteen": 19, "twenty": 20,
        "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
        "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    }

    _GENERIC_NOUNS_LIST = {
        "health", "benefits", "benefit", "nutrition", "vitamin", "vitamins",
        "mineral", "minerals", "antioxidant", "antioxidants", "fruit", "fruits",
        "vegetable", "vegetables", "food", "foods", "diet", "healthy", "wellness",
        "nutrient", "nutrients", "property", "properties", "effect", "effects",
        "impact", "advantage", "advantages", "superfood", "superfoods",
    }

    def _extract_subject_after_marker(text: str, marker_end: int) -> str:
        """Extract the list-item subject from text after the numbered marker."""
        tail = text[marker_end:].strip()
        if not tail:
            return ""

        # Strip leading punctuation/separators
        tail = re.sub(r"^[.:,;\-–—)\]]+\s*", "", tail).strip()

        # Strategy A: Proper noun phrase (capitalized)
        proper = re.match(r"^((?:The\s+)?[A-Z][A-Za-z0-9'’]+(?:\s+(?:of|the|and|&|[A-Z][A-Za-z0-9'’]+)){0,3})\b", tail)
        if proper:
            candidate = proper.group(1).strip()
            if candidate.lower() not in _GENERIC_NOUNS_LIST and not re.match(r"^(19|20)\d{2}$", candidate):
                # Clean article prefix
                cleaned = re.sub(r"^(?:The|A|An)\s+", "", candidate).strip()
                if len(cleaned) >= 3:
                    return cleaned

        # Strategy B: "is the X" / "is X" / "are the X" pattern
        intro_match = re.search(
            r"\b(?:is|are)\s+(?:the\s+|a\s+|an\s+)?([A-Za-z]{3,})",
            tail[:120],
            re.IGNORECASE,
        )
        if intro_match:
            candidate = intro_match.group(1)
            lower = candidate.lower()
            if lower not in _GENERIC_NOUNS_LIST and not lower.endswith(("ed", "ing", "ly", "ment", "tion", "sion")):
                if candidate[0].isupper():
                    return candidate
                # Capitalize first letter for normalized output
                return candidate[0].upper() + candidate[1:] if len(candidate) > 1 else candidate

        # Strategy C: First significant noun in tail
        words = re.findall(r"[A-Za-z]{3,}", tail[:200])
        for w in words:
            lower = w.lower()
            if lower not in _GENERIC_NOUNS_LIST and lower not in {
                "the", "and", "for", "with", "that", "this", "from", "into",
                "they", "them", "their", "your", "just", "like", "very", "also",
                "first", "second", "third", "fourth", "fifth", "number", "rank",
            }:
                if w[0].isupper():
                    return w
                return w[0].upper() + w[1:] if len(w) > 1 else w

        return ""

    # Pattern to detect numbered markers
    marker_patterns = [
        # "5. Banana..." or "5) Banana..." or "#5 Banana..."
        re.compile(
            r"(?:^|[.!?\n]\s*)"
            r"(?:#\s*|number\s+|no\.?\s*)"
            r"(\d{1,2})"
            r"\s*[.):,\-–—\]]?\s*",
            re.IGNORECASE,
        ),
        # "Number 5: Banana", "No. 5 is Banana"
        re.compile(
            r"\b(?:number|no\.?)\s*(\d{1,2})\s*(?:[.:,)\-–—]?\s+(?:is|are|goes|belongs|we\s*have|let['’]s\s*talk|coming|landing|ranking|placing|taking|sitting)\b)?",
            re.IGNORECASE,
        ),
        # "#5" or "# 5" at start of scene
        re.compile(r"^#\s*(\d{1,2})\b", re.IGNORECASE),
        # Word numbers: "First", "Second", etc.
        re.compile(
            rf"\b({rank_words})\s+(?:place|spot|is|on\s+the\s+list|on\s+our\s+list)\b",
            re.IGNORECASE,
        ),
        # "at number five" / "coming in at number 5" / "in at number 5"
        re.compile(
            r"(?:at|in|coming\s+in\s+at|landing\s+at|sitting\s+at)\s+(?:the\s+)?(?:number|no\.?|spot)\s+"
            rf"(\d{{1,2}}|{rank_words})",
            re.IGNORECASE,
        ),
        # "#5: item" or "#5 - item"
        re.compile(r"#\s*(\d{1,2})\s*[-:–—]\s*([^.!?\n]+)", re.IGNORECASE),
    ]

    markers: Dict[int, dict] = {}

    for i, scene_text in enumerate(scene_texts):
        if not scene_text:
            continue

        best_match = None
        best_number = 0

        for pattern in marker_patterns:
            match = pattern.search(scene_text)
            if not match:
                continue

            number_str = match.group(1).lower()
            number = _word_to_num.get(number_str) or int(number_str)
            if number < 1 or number > 100:
                continue

            if number > best_number:
                best_number = number
                best_match = match

        if best_match and best_number > 0:
            subject = _extract_subject_after_marker(scene_text, best_match.end())
            if subject:
                markers[i] = {
                    "index": i,
                    "container_subject": subject,
                    "marker_number": best_number,
                    "has_explicit_marker": True,
                }

    # ---- Stage 2: Detect the intro boundary (scenes before first marker) ----
    marker_indices = sorted(markers.keys())
    first_marker = marker_indices[0] if marker_indices else -1

    # ---- Stage 3: Fill gaps — propagate subjects forward from each marker ----
    # Also try to resolve missing markers (e.g., transition phrases like "next up is X")
    _transition_patterns = [
        re.compile(r"\b(?:next\s+(?:up|on|is|comes?)|moving\s+(?:on\s+)?to|turning\s+to|"
                    r"another\s+(?:great|key|important|essential|popular|must-try|nutritious|delicious|fantastic))"
                    r"\s*(?:is\s+)?(?:the\s+)?([A-Za-z]{3,})", re.IGNORECASE),
        re.compile(r"\b(?:let['’]s\s+(?:talk\s+about|discuss|explore|dive\s+into|look\s+at))\s+([A-Za-z]{3,})",
                    re.IGNORECASE),
        re.compile(r"\b(?:let['’]s\s+not\s+(?:forget|overlook))\s+([A-Za-z]{3,})", re.IGNORECASE),
    ]

    def _quick_subject_extract(text: str) -> str:
        """Quick subject extraction from transition phrases."""
        for pattern in _transition_patterns:
            match = pattern.search(text)
            if match:
                candidate = match.group(1).strip()
                lower = candidate.lower()
                if lower not in _GENERIC_NOUNS_LIST and len(candidate) >= 3:
                    if candidate[0].isupper():
                        return candidate
                    return candidate[0].upper() + candidate[1:] if len(candidate) > 1 else candidate
        return ""

    # ---- Stage 4: Build the output list ----
    result = []
    current_container = -1
    current_subject = ""
    # Sort markers by index
    sorted_markers = sorted(markers.items(), key=lambda x: x[0])

    for i in range(n):
        if i in markers:
            info = markers[i]
            current_container = info["marker_number"]
            current_subject = info["container_subject"]
            result.append({
                "index": i,
                "container_id": f"section_{current_container}",
                "container_subject": current_subject,
                "marker_number": current_container,
                "has_explicit_marker": True,
                "is_intro": False,
            })
        elif current_container > 0:
            # Inside a known section
            result.append({
                "index": i,
                "container_id": f"section_{current_container}",
                "container_subject": current_subject,
                "marker_number": current_container,
                "has_explicit_marker": False,
                "is_intro": False,
            })
        else:
            # Before the first numbered marker — check if it's an intro
            is_intro = (first_marker < 0 or i < first_marker)
            # Try to extract subject from transition phrasing
            transition_subject = _quick_subject_extract(scene_texts[i]) if scene_texts[i] else ""

            container_subject = current_subject or transition_subject or topic or ""
            result.append({
                "index": i,
                "container_id": "intro" if is_intro else f"section_unknown",
                "container_subject": container_subject,
                "marker_number": 0 if is_intro else -1,
                "has_explicit_marker": False,
                "is_intro": is_intro,
            })

    return result
    """Extract the primary subject/entity for each scene from the script text.
    Returns list of subjects, one per scene. Falls back to topic anchor if extraction fails.
    Handles both capitalized (LLM) and lowercase (ASR) scene text.
    """
    # Use the main subject as default fallback
    main_subject = _extract_main_subject(f"{topic}\n{script}") or topic
    main_subject = main_subject.strip()
    
    # Common nouns that are likely scene subjects (specific items in a listicle)
    # These are words that start a scene or are prominent: typically the first noun
    # that isn't a generic concept word
    _GENERIC_WORDS = {"the", "a", "an", "this", "that", "these", "those", "it", "its", "they", "them",
                      "their", "he", "she", "his", "her", "our", "we", "you", "your",
                      "and", "or", "but", "for", "with", "without", "from", "into", "onto", "upon",
                      "during", "before", "after", "above", "below", "over", "under", "again",
                      "once", "then", "than", "there", "here", "what", "when", "where", "why", "how",
                      "which", "who", "whom", "very", "just", "also", "not", "no", "yes", "maybe",
                      "still", "yet", "already", "ever", "never", "now", "always", "often", "usually",
                      "generally", "typically", "actually", "basically", "literally", "really",
                      "quite", "rather", "pretty", "fairly", "totally", "simply",
                      "health", "healthy", "benefit", "benefits", "nutrition", "nutritional",
                      "vitamin", "vitamins", "mineral", "minerals", "antioxidant", "antioxidants",
                      "fruit", "fruits", "vegetable", "vegetables", "food", "foods", "diet",
                      "wellness", "nutrient", "nutrients",
                      "property", "properties", "effect", "effects", "impact",
                      "advantage", "advantages", "superfood", "superfoods",
                      "first", "second", "third", "fourth", "fifth", "sixth", "next", "last", "final",
                      "another", "other", "many", "much", "some", "any", "every", "each",
                      "all", "both", "few", "several", "most", "least", "more", "less",
                      "top", "best", "worst", "greatest", "biggest", "smallest", "largest", "highest",
                      "lowest", "fastest", "slowest", "oldest", "newest", "earliest", "latest",
                      "number", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                      "instead", "meanwhile", "elsewhere", "however", "therefore", "furthermore",
                      "otherwise", "nevertheless", "nonetheless", "moreover", "besides",
                      "consequently", "accordingly", "additionally", "later", "earlier",
                       "moving", "turning", "regarding", "consider", "imagine", "picture",
                       "taking", "coming", "entering", "ranking", "landing", "placing"}
    
    subjects = []
    for st in scene_texts:
        # Strategy 1: Extract capitalized proper nouns (works for LLM-generated text)
        proper_phrases = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b", st)
        filtered = [
            p for p in proper_phrases
            if p.lower() not in _GENERIC_WORDS
            and len(p) >= 3
            and not re.match(r'^(19|20)\d{2}$', p)
        ]
        if filtered:
            subject = max(filtered, key=len)
            subjects.append(subject)
            continue
        
        # Strategy 2: Look at the first noun-like word in the scene text (works for ASR lowercase)
        # Scene text typically starts with the item name in listicles.
        # First check for "is the [subject]" or "is [subject]" patterns which
        # are common in listicle / ranking scripts (e.g. "is the apple", "is banana").
        words = re.findall(r"[a-zA-Z]+", st)
        significant = [w for w in words if w.lower() not in _GENERIC_WORDS and len(w) >= 3]
        if significant:
            first = significant[0]
            prev_subject = subjects[-1].lower() if subjects else ""
            first_lower = first.lower()

            # Try to extract subject from "is the X" / "are the X" / "is X" / "are X" patterns
            # This handles: "at number five is the apple", "taking third spot is the orange"
            intro_match = re.search(
                r"\b(?:is|are)\s+(?:the\s+|a\s+|an\s+)?([A-Za-z]{3,})",
                st,
                re.IGNORECASE,
            )
            if intro_match:
                candidate = intro_match.group(1).lower()
                if candidate in [w.lower() for w in significant] and candidate not in {
                    "health", "benefit", "benefits", "nutrition", "vitamin", "vitamins",
                    "antioxidant", "antioxidants", "fruit", "fruits", "food", "foods",
                    "diet", "wellness", "nutrient", "nutrients",
                }:
                    first = candidate

            # If the first significant word is a transition/reference to the
            # previous subject, look deeper for the actual subject
            prev_mentioned = prev_subject and (first_lower == prev_subject or first_lower in prev_subject)
            if first_lower in {"health", "benefit", "benefits", "nutrition", "vitamin", "vitamins", "antioxidant", "antioxidants", "fruit", "fruits", "food", "foods", "diet", "wellness"} or prev_mentioned:
                # Try to find a more specific noun later in the text
                for w in significant[1:]:
                    lower = w.lower()
                    if lower not in {"health", "benefit", "benefits", "nutrition", "vitamin", "vitamins", "antioxidant", "antioxidants", "fruit", "fruits", "food", "foods", "diet", "wellness", "nutrient", "nutrients", "property", "properties", "effect", "effects", "impact", "advantage", "advantages"} and lower != prev_subject:
                        first = w
                        break
            subjects.append(first)
            continue
        
        # Strategy 3: Use _extract_visual_keywords as fallback (broad keyword extraction)
        try:
            keywords = _extract_visual_keywords(st)
            if keywords:
                nouns = [k for k in keywords if len(k) > 4]
                if nouns:
                    subjects.append(nouns[0])
                else:
                    subjects.append(keywords[0])
            else:
                subjects.append(main_subject)
        except Exception:
            subjects.append(main_subject)
    
    return subjects


def _is_specific_subject(subject: str, main_subject: str) -> bool:
    """Check if subject is a specific instance (e.g. 'Apple', 'Blueberry') vs generic (e.g. 'Fruits', 'Health Benefits')."""
    if not subject or subject.lower() == main_subject.lower():
        return False
    # Generic/concept terms that indicate non-specific subject
    generic_terms = {"health", "benefits", "benefit", "nutrition", "nutritional", "vitamin", "vitamins", 
                     "mineral", "minerals", "antioxidant", "antioxidants", "fruit", "fruits", "vegetable", 
                     "vegetables", "food", "foods", "diet", "healthy", "health", "wellness", "benefit",
                     "advantage", "advantages", "property", "properties", "effect", "effects", "impact",
                     "benefits", "advantages", "superfood", "superfoods", "nutrient", "nutrients"}
    subject_lower = subject.lower()
    words = set(subject_lower.split())
    # If subject contains only generic terms, it's not specific
    if words and words.issubset(generic_terms):
        return False
    # If subject is a specific named entity (capitalized, not generic), it's specific
    if subject[0].isupper() and subject_lower not in generic_terms:
        return True
    # Lowercase concrete nouns (from ASR) — if not in generic terms and has >= 2 chars, it's specific
    if len(subject.strip()) >= 2 and subject_lower not in generic_terms:
        return True
    return False


# =============================================================================
#  LLM-POWERED QUERY GENERATION
# =============================================================================

_QUERY_SYSTEM_PROMPT = (
    "You are a senior visual query planner for long-form documentary video generation.\n"
    "Your job is to turn a full script plus scene snippets into short, semantic, Flickr-friendly photo search queries.\n"
    "Generate EXACTLY 5 simple, highly minimal, visually searchable queries per scene.\n\n"
    "GLOBAL CONTEXT CONTRACT:\n"
    "- First infer the video's central semantic anchor from the FULL SCRIPT, not from one isolated scene.\n"
    "- Every query must stay inside that anchor's world: same topic, sport, country/entity, era, product, or niche.\n"
    "- A scene may add one visible detail, but it must not drift to unrelated people, countries, sports, brands, professions, or generic portraits.\n"
    "- For news/sports/politics topics, prefer contextual crowd/location/object imagery over random individual portraits.\n\n"
    "DISASTER / BREAKING NEWS CONTRACT:\n"
    "- If the script is about an earthquake, flood, hurricane, tsunami, wildfire, landslide, or other disaster, every query must preserve the location/country plus the disaster word.\n"
    "- Never replace a recent disaster with historical, cultural, dance, festival, tourist, or generic archive footage.\n"
    "- Good: 'Venezuela earthquake damage', 'Venezuela earthquake aftermath', 'Venezuela quake rescue'. Bad: 'historic Venezuela dance', 'traditional festival', 'earthquakes devastation'.\n\n"
    "FLICKR PHOTO RETRIEVAL PRINCIPLES:\n"
    "- Flickr is a photography community, NOT a text search engine or encyclopedia. Photos are tagged with physical, visible entities, concrete actions, and real-world settings.\n"
    "- Short broad queries often return hundreds of useful images; over-specific sentence-like queries return almost nothing.\n"
    "- NEVER generate queries containing abstract, non-visual concepts. Instead, translate abstract ideas into highly concrete visual scenes. Choose the correct paradigm based on context:\n\n"
    "PARADIGM 1: Biographical / Entity-Centric Niches (e.g. Person, Brand, Team)\n"
    "  Pattern: [Subject Full Name] [Concrete Visual Action / Noun / Detail]\n"
    "  Translations:\n"
    "    * 'legacy' or 'achievements' -> '[Name] trophy', '[Name] team celebration', '[Name] podium'\n"
    "    * 'scrutiny' or 'challenges' or 'criticism' -> '[Name] press conference', '[Name] serious face', '[Name] media interview'\n"
    "    * 'hardwork' or 'practice' -> '[Name] practice', '[Name] training', '[Name] nets'\n"
    "    * 'leadership' or 'career' -> '[Name] leading team', '[Name] captain', '[Name] portrait'\n"
    "  Examples:\n"
    "    - \"Virat Kohli cover drive\" (NOT \"coverdrive virat\")\n"
    "    - \"Steve Jobs keynote Apple\" (NOT \"keynote steve\")\n"
    "    - \"Elon Musk rocket launch\"\n\n"
    "PARADIGM 2: Topic / Concept-Centric Niches (e.g. Finance, Science, Nature, Cooking, Travel, History)\n"
    "  Pattern: [Core Concrete Object / Setting] [Simple Visual Action or Physical Detail]\n"
    "  Translations:\n"
    "    * 'inflation' or 'economic crisis' -> 'house for sale sign', 'empty wallet', 'grocery store price tag'\n"
    "    * 'climate change' or 'global warming' -> 'dried cracked earth', 'melting glacier ice', 'smokestack factory smoke'\n"
    "    * 'ancient trade routes' or 'history' -> 'coffee beans burlap sack', 'wooden sailing ship ocean', 'ancient paper map'\n"
    "    * 'pressure' or 'abyssal zone' -> 'anglerfish deep sea', 'underwater deep ocean', 'submersible submarine'\n"
    "    * 'health' or 'diet' -> 'fresh green salad bowl', 'running shoes park trail', 'glass of water lemon'\n"
    "  Examples:\n"
    "    - \"coffee beans burlap sack\" (NOT \"ancient history of coffee trading\")\n"
    "    - \"house for sale sign\" (NOT \"real estate finance market inflation rate\")\n\n"
    "RULES:\n"
    "- Keep queries broad and minimal (2-5 words max, ideally 3-4). Long, full-sentence, newsy, or abstract queries will fail on search engines.\n"
    "- Put the semantic anchor first when useful: 'GTA 6 gameplay', 'Mercedes Actros truck', 'coffee beans', 'SpaceX rocket launch', 'Virat Kohli batting'.\n"
    "- Do not overfit tiny scene details. Use one concrete visual detail max: game environment, key art, truck, dashboard, trophy, market, glacier.\n"
    "- Prefer reusable visual searches over exact scene narration: '[Topic] key art', '[Topic] launch event', '[Subject] celebration'.\n"
    "- Do NOT use names of unrelated countries/places/people from side context unless they are the main visible subject of that scene and are present in the script.\n"
    "- Avoid generic 'portrait' for topic/group/news/gaming stories unless the video is clearly about one named person.\n"
    "- ALWAYS start with the main subject/object, followed by the concrete action/noun. NEVER invert the order.\n"
    "- If biographical: ALWAYS expand short surnames (like 'Kohli', 'Messi', 'Ronaldo') to full natural proper names (e.g. 'Virat Kohli').\n"
    "- NEVER mash multi-word visual phrases into a single word (e.g. use \"cover drive\" as two words, NOT \"coverdrive\").\n"
    "- Output ONLY valid JSON: {\"queries\": [{\"specific\": \"...\", \"medium\": \"...\", \"general\": \"...\", \"fallback_1\": \"...\", \"fallback_2\": \"...\"}]}\n"
    "LISTICLE CONTAINER CONTRACT:\n"
    "- Numbered items define HARD CONTAINERS — queries for scenes inside a container MUST start with that container's item.\n"
    "- A query for scene in the 'banana' section that doesn't contain 'banana' as the first word is INVALID.\n"
    "- A query for scene in the 'apple' section that uses 'banana' is INVALID.\n"
    "- Each section's item is a MANDATORY first-word of every query generated for scenes in that section.\n"
)

_QUERY_USER_PROMPT = """Video topic: {topic}

Full script context (first 2500 chars):
{script_context}

Scene descriptions:
{scenes}

For EACH scene, generate 5 broad visual queries.

Think silently:
1. What is the global semantic anchor of the video?
2. What visible scene family best represents this scene while staying inside that anchor?
3. Which short query would produce many real photos on Flickr?

Do not write full lines from the script. Do not generate isolated adjectives like "Iranian portrait". Do not switch sports, countries, or niches.

EXAMPLE OUTPUT (Biographical - Virat Kohli):
{{
  "queries": [
    {{
      "specific": "Virat Kohli cover drive",
      "medium": "Virat Kohli batting",
      "general": "Virat Kohli cricket",
      "fallback_1": "Virat Kohli portrait",
      "fallback_2": "Virat Kohli"
    }}
  ]
}}

EXAMPLE OUTPUT (Topic-Centric - History of Coffee / Economics):
{{
  "queries": [
    {{
      "specific": "coffee beans burlap sack",
      "medium": "coffee roasting pan",
      "general": "coffee plantation trees",
      "fallback_1": "coffee cup steaming",
      "fallback_2": "coffee beans"
    }},
    {{
      "specific": "house for sale sign",
      "medium": "empty leather wallet",
      "general": "grocery store prices",
      "fallback_1": "dollar bills hands",
      "fallback_2": "money cash"
    }}
  ]
}}

EXAMPLE OUTPUT (Listicle / Top-N - Fruit Health Benefits):
{{
  "queries": [
    {{
      "specific": "banana bunch tree",
      "medium": "banana close up",
      "general": "banana fruit slice",
      "fallback_1": "banana hand holding",
      "fallback_2": "banana smoothie"
    }},
    {{
      "specific": "apple red tree orchard",
      "medium": "apple close up",
      "general": "apple fruit basket",
      "fallback_1": "apple bite person",
      "fallback_2": "apple slice"
    }},
    {{
      "specific": "orange tree citrus",
      "medium": "orange segments fruit",
      "general": "orange fruit close",
      "fallback_1": "orange juice glass",
      "fallback_2": "orange wedge"
    }}
  ]
}}
CRITICAL FOR LISTICLES: Each scene's queries MUST start with its OWN unique item (banana, apple, orange) — never reuse the first item across scenes.
HARD RULE: If Scene 3's Entity is "banana", ALL 3 queries (specific, medium, general) MUST contain "banana" as the FIRST word. Never use "fruit" or "health" for a banana scene. Never use a different item's name in a different item's scene. Each numbered list item defines a HARD CONTAINER — queries MUST NOT cross containers.

NEVER use abstract words (e.g. scrutiny, achievements, legacy, inflation, crisis). Translate all abstract ideas into concrete visible photo scenes. Keep queries broad, minimal (2-5 words max), semantic, and Flickr-friendly.
Output as JSON only."""


def _generate_queries_llm(script: str, topic: str, scene_texts: List[str],
                           api_key: str, model: str = "qwen/qwen3.5-flash-02-23",
                           provider: str = "openrouter", strict: bool = False,
                           per_scene_subjects: Optional[List[str]] = None,
                           full_script: Optional[str] = None) -> List[Dict]:
    """Generate search queries using LLM with scene context.
    per_scene_subjects: list of scene-specific subjects, one per scene_texts entry.
    full_script: optional full script text (used when `script` is a truncated chunk context).
    """
    from services.openrouter_llm import OpenRouterLLM as LLMClass
    resolved_model = model or "qwen/qwen3.5-flash-02-23"


    llm = LLMClass(api_key=api_key, model=resolved_model)
    is_listicle = _is_listicle_script(full_script or script, topic, scene_texts)

    main_subject = ""
    if not is_listicle:
        main_subject = _extract_main_subject(topic)
        if not (main_subject and len(main_subject.split()) >= 2):
            script_subject = _extract_main_subject(script)
            if script_subject:
                main_subject = script_subject

        # Context-aware LLM Entity Expansion:
        if api_key and (not main_subject or len(main_subject.split()) < 2):
            try:
                context_sample = (script[:1500] if script else topic)[:1500]
                disambig_prompt = (
                    "You are an expert entity disambiguation system.\n"
                    "Identify the primary famous proper-noun subject (person, place, brand, organization) in the following text.\n"
                    "Always return their full, natural, globally recognized proper name (e.g. 'Virat Kohli' instead of 'Kohli', 'Steve Jobs' instead of 'Jobs').\n"
                    "If there is no famous proper-noun subject (e.g. it is a general topic like fruit, cooking, or general health), return 'NONE'. Do not guess or hallucinate.\n"
                    "Return ONLY the plain full name or 'NONE' (no punctuation, no explanation)."
                )
                disambig_response = llm.chat(
                    user_prompt=f"Text:\n{context_sample}\n\nIdentify and expand the primary subject to their full famous proper name, or return 'NONE':",
                    system_prompt=disambig_prompt,
                    temperature=0.1,
                    max_tokens=20
                ).strip()
                disambig_response = re.sub(r"[^\w\s\-]", "", disambig_response).strip()
                if disambig_response and disambig_response.upper() != "NONE" and len(disambig_response.split()) >= 2 and len(disambig_response) < 50:
                    _web_log(f"subject detection: expanded '{main_subject}' to full proper name '{disambig_response}' using LLM world knowledge")
                    main_subject = disambig_response
            except Exception as e:
                _web_log(f"LLM subject expansion failed: {e}")

    # ALWAYS run entity analysis for ALL video types — this determines
    # the focal entity per scene, whether it's a single-subject doc,
    # a multi-subject listicle, a comparison, or a narrative.
    # Preemptively detect list sections so entity analysis has the strongest signal.
    list_sections = _detect_list_sections(scene_texts, topic, full_script or script) if is_listicle else None
    entity_analysis = analyze_script_entities(full_script or script, topic, scene_texts, list_sections=list_sections)
    per_scene_subjects_from_analysis = [ea["primary_entity"] for ea in entity_analysis]

    # Merge with any externally provided per_scene_subjects (higher priority)
    merged_scene_subjects: List[str] = []
    for i in range(len(scene_texts)):
        external = (per_scene_subjects[i] if per_scene_subjects and i < len(per_scene_subjects) and per_scene_subjects[i] else "")
        if external and len(external) >= 3:
            merged_scene_subjects.append(external)
        elif i < len(per_scene_subjects_from_analysis) and per_scene_subjects_from_analysis[i]:
            merged_scene_subjects.append(per_scene_subjects_from_analysis[i])
        else:
            merged_scene_subjects.append("")

    # Build per-scene prompt blocks: NO neighboring context, NO script sample
    scene_blocks = []
    for i, st in enumerate(scene_texts):
        entity = merged_scene_subjects[i] if i < len(merged_scene_subjects) else ""
        role = entity_analysis[i]["scene_role"] if i < len(entity_analysis) else "entity_focus"
        if entity:
            scene_blocks.append(f'Scene {i + 1}: "{st[:400]}"\n  Entity: "{entity}"  Role: {role}')
        else:
            scene_blocks.append(f'Scene {i + 1}: "{st[:400]}"\n  Role: {role}')

    scenes_text = "\n\n".join(scene_blocks)
    script_context = ""  # NO global script context — it biases the LLM

    user_prompt = (
        f"You generate web image and Flickr photo search queries for each scene of a video.\n\n"
        f"Topic: \"{topic}\"\n\n"
        f"Scene descriptions with their focal entity:\n{scenes_text}\n\n"
        f"RULES:\n"
        f"- The FIRST word of EVERY query MUST be the scene's Entity (if provided).\n"
        f"- 2-5 words, use concrete visual nouns, no abstract concepts.\n"
        f"- Prefer photo-friendly words like portrait, action, celebration, stadium, event, scene, location.\n"
        f"- Search for the actual physical subjects and environments. Do not use lecture slides, homework, quizzes, worksheets or PowerPoint pages as documentary imagery. For a scientific mechanism, retrieve clean subject photographs or purposeful illustrations; the editing director builds the explanatory graphic. Search for source documents only when the scene explicitly examines that document.\n"
        f"- Do NOT force 'footage' or 'video' endings for web/Flickr image queries.\n"
        f"- Generate EXACTLY 3 queries per scene: specific, medium, broad.\n"
        f"- Output valid JSON: {{\"queries\": [{{\"specific\": \"...\", \"medium\": \"...\", \"general\": \"...\"}}]}}"
    )

    try:
        contract = _query_contract_terms(topic, script, scene_texts)
        contract_prompt = (
            f"\n\nSTRICT OUTPUT CONTRACT:\n"
            f"- Return EXACTLY {len(scene_texts)} rows in queries, one per scene, same order.\n"
            f"- Topic anchor: {contract.get('anchor') or topic}\n"
            f"- Detected niche: {contract.get('niche') or 'general'}\n"
            f"- Every row must contain specific, medium, general.\n"
            f"- Each query first word MUST be the scene's Entity.\n"
        )
        attempt_specs = [
            (0.12, user_prompt + contract_prompt, "initial"),
            (
                0.0,
                user_prompt + contract_prompt + "\nPrevious output failed validation. Repair it: exact row count, valid JSON only, no prose, no abstract or off-topic queries.",
                "repair",
            ),
        ]
        last_errors: List[str] = []
        token_budget = max(1800, min(6000, 420 + (len(scene_texts) * 260)))
        try:
            llm_timeout = float(os.environ.get("VIDZEO_QUERY_LLM_CALL_TIMEOUT", "120") or 120)
        except Exception:
            llm_timeout = 120.0
        llm_timeout = max(90.0, min(240.0, llm_timeout))
        try:
            llm_attempts = int(os.environ.get("VIDZEO_QUERY_LLM_MAX_ATTEMPTS", "2") or 2)
        except Exception:
            llm_attempts = 2
        llm_attempts = max(2, min(3, llm_attempts))
        for temperature, prompt, attempt_label in attempt_specs:
            response = _chat_query_llm(
                llm,
                prompt,
                _QUERY_SYSTEM_PROMPT,
                temperature=temperature,
                max_tokens=token_budget,
                timeout_seconds=llm_timeout,
                max_attempts=llm_attempts,
            )
            queries_data = _parse_llm_response(response)
            rows, validation_errors = _normalize_llm_query_rows(
                queries_data,
                scene_texts,
                topic,
                script,
                main_subject or topic,
                per_scene_subjects=merged_scene_subjects,
            )
            # Post-processing: force the first word of every query to match
            # the scene's primary entity. This is a HARD constraint, not a suggestion.
            if rows:
                for row_idx, row in enumerate(rows):
                    if isinstance(row, dict) and row_idx < len(merged_scene_subjects):
                        entity = merged_scene_subjects[row_idx]
                        if entity and len(entity) >= 2:
                            entity_lower = entity.lower().strip()
                            for qkey in ("search_query", "supporting_search_query", "alt_search_query"):
                                qval = str(row.get(qkey) or "").strip()
                                if qval and not qval.lower().startswith(entity_lower):
                                    # Prepend entity
                                    row[qkey] = f"{entity} {qval}"
                            # Also fix query_variants
                            variants = row.get("query_variants")
                            if isinstance(variants, list):
                                fixed = []
                                for v in variants:
                                    v = str(v or "").strip()
                                    if v and not v.lower().startswith(entity_lower):
                                        v = f"{entity} {v}"
                                    fixed.append(v)
                                row["query_variants"] = fixed
            last_errors = validation_errors
            if rows and len(rows) >= len(scene_texts):
                weak_rows = sum(1 for row in rows if len(row.get("query_variants") or []) < 3)
                if weak_rows == 0 or attempt_label == "repair":
                    _web_log(
                        f"LLM generated {len(rows)}/{len(scene_texts)} contract-safe scene queries "
                        f"via {provider}/{resolved_model} ({attempt_label})"
                    )
                    return rows
            _web_log(
                f"LLM query validation requested repair via {provider}/{resolved_model}: "
                + "; ".join(last_errors[:4])
            )
    except Exception as e:
        _web_log(f"LLM query generation failed via {provider}/{resolved_model}: {e}")
        if strict:
            raise

    return []


def _parse_llm_response(response: Any) -> Optional[List[Dict]]:
    """Parse LLM response into list of query dicts. Handles various formats."""
    if response is None:
        return None

    if isinstance(response, (int, float, bool)):
        return None

    if isinstance(response, list):
        return response if response else None

    if isinstance(response, dict):
        if "queries" in response:
            val = response["queries"]
            if not isinstance(val, list) or not val:
                return None
            global_topic = str(response.get("global_topic") or response.get("topic_anchor") or "").strip()
            if global_topic:
                for row in val:
                    if isinstance(row, dict):
                        row.setdefault("global_topic", global_topic)
                        row.setdefault("topic_anchor", global_topic)
            return val
        return [response]

    if isinstance(response, str):
        text = response.strip()
        if not text:
            return None
        for parse_fn in [_try_parse_json, _try_extract_json_block]:
            result = parse_fn(text)
            if result is not None:
                if isinstance(result, dict) and "queries" in result:
                    val = result["queries"]
                    if not isinstance(val, list) or not val:
                        return None
                    global_topic = str(result.get("global_topic") or result.get("topic_anchor") or "").strip()
                    if global_topic:
                        for row in val:
                            if isinstance(row, dict):
                                row.setdefault("global_topic", global_topic)
                                row.setdefault("topic_anchor", global_topic)
                    return val
                if isinstance(result, list) and result:
                    return result
                if isinstance(result, dict):
                    return [result]

    return None


def _try_parse_json(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except Exception:
        return None


def _try_extract_json_block(text: str) -> Optional[Any]:
    for brace_start, brace_end in [("{", "}"), ("[", "]")]:
        si = text.find(brace_start)
        ei = text.rfind(brace_end)
        if si != -1 and ei > si:
            try:
                return json.loads(text[si:ei + 1])
            except Exception:
                continue
    return None


# =============================================================================
#  CHUNKED LLM GENERATION
# =============================================================================

def _query_chunk_size() -> int:
    try:
        return max(3, min(8, int(os.environ.get("VIDZEO_QUERY_LLM_CHUNK_SIZE", "6") or 6)))
    except Exception:
        return 6


def _query_chunk_workers(chunk_count: int) -> int:
    try:
        requested = int(os.environ.get("VIDZEO_QUERY_LLM_CHUNK_WORKERS", "6") or 6)
    except Exception:
        requested = 6
    return max(1, min(max(1, chunk_count), max(1, min(8, requested))))


def _script_context_for_query_chunk(script: str, topic: str, chunk: List[str],
                                    previous_tail: str = "", next_head: str = "") -> str:
    """Build a compact, UNBIASED context for the LLM chunk.
    NO script sample — the first 700 chars always bias toward the first entity."""
    parts = []
    topic = re.sub(r"\s+", " ", str(topic or "")).strip()
    if topic:
        parts.append(f"Topic: {topic}")
    if previous_tail:
        parts.append(f"Previous context: {previous_tail[:220]}")
    parts.append("Current scenes: " + " ".join(str(item or "")[:180] for item in chunk))
    if next_head:
        parts.append(f"Next context: {next_head[:220]}")
    return "\n".join(parts)


def _chunk_query_generation(script: str, topic: str, scene_texts: List[str],
                             api_key: str, model: str,
                             chunk_size: int = 10, provider: str = "openrouter",
                             strict: bool = False,
                             per_scene_subjects: Optional[List[str]] = None) -> List[Dict]:
    """Split scene texts into chunks, call LLM per chunk, merge results.

    The provider call has its own retry budget, but ``as_completed`` without a
    wall deadline meant one wedged socket could hold the entire media-query
    stage forever. Keep the successful chunks, stop waiting at one shared
    deadline, and build deterministic subject-grounded queries only for the
    chunks that did not finish. The detached provider workers cannot block the
    caller or the next scene-resolution stage.
    """
    chunk_size = max(1, int(chunk_size or _query_chunk_size()))
    chunks: List[Tuple[int, List[str]]] = []
    for i in range(0, len(scene_texts), chunk_size):
        chunks.append((i, scene_texts[i:i + chunk_size]))

    all_rows = []
    try:
        chunk_wall = float(os.environ.get("VIDZEO_QUERY_LLM_CHUNK_WALL_SECONDS", "240") or 240)
    except (TypeError, ValueError):
        chunk_wall = 240.0
    # A chunk may contain several scene queries, so allow a useful model
    # response window while keeping the whole media-query phase bounded.
    chunk_wall = max(0.05, min(360.0, chunk_wall))
    _web_log(
        f"LLM query chunks: {len(scene_texts)} scenes -> {len(chunks)} chunk(s) "
        f"x up to {chunk_size} scenes, workers={_query_chunk_workers(len(chunks))}, model={model}"
    )
    pool = ThreadPoolExecutor(max_workers=_query_chunk_workers(len(chunks)), thread_name_prefix="query-chunk")
    futures = {}
    for ci, (start, chunk) in enumerate(chunks):
        previous_tail = scene_texts[start - 1] if start > 0 else ""
        next_head = scene_texts[start + len(chunk)] if start + len(chunk) < len(scene_texts) else ""
        chunk_context = _script_context_for_query_chunk(script, topic, chunk, previous_tail, next_head)
        # Slice per-scene subjects for this chunk
        chunk_subjects = None
        if per_scene_subjects:
            chunk_subjects = per_scene_subjects[start:start + len(chunk)]
        f = pool.submit(
            _generate_queries_llm, chunk_context, topic, chunk, api_key, model, provider, strict,
            per_scene_subjects=chunk_subjects,
            full_script=script,
        )
        futures[f] = (ci, start)
    chunk_results = {}
    failed_chunks = set()
    pending = set(futures)
    deadline = time.monotonic() + chunk_wall
    try:
        while pending:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                break
            done, pending = wait(pending, timeout=min(0.25, remaining), return_when=FIRST_COMPLETED)
            for future in done:
                ci, _start = futures[future]
                try:
                    chunk_results[ci] = future.result()
                except Exception as exc:
                    failed_chunks.add(ci)
                    _web_log(f"LLM query chunk {ci + 1}/{len(chunks)} failed via {provider}/{model}: {exc}")
    finally:
        for future in pending:
            future.cancel()
        # A running network request cannot be force-killed safely, but it must
        # not make this stage wait beyond its wall. Provider-level timeouts and
        # the job context still govern the detached worker itself.
        pool.shutdown(wait=False, cancel_futures=True)

    missing_chunks = {futures[future][0] for future in pending}
    failed_chunks.update(missing_chunks)
    for ci in sorted(missing_chunks):
        _web_log(
            f"LLM query chunk {ci + 1}/{len(chunks)} exceeded shared wall "
            f"({chunk_wall:.0f}s); keeping completed chunks and resolving this chunk locally"
        )

    for ci, (start, chunk) in enumerate(chunks):
        rows = list(chunk_results.get(ci, []))
        if ci in failed_chunks and not rows:
            rows = _generate_queries_mechanical(
                _script_context_for_query_chunk(
                    script,
                    topic,
                    chunk,
                    scene_texts[start - 1] if start > 0 else "",
                    scene_texts[start + len(chunk)] if start + len(chunk) < len(scene_texts) else "",
                ),
                topic,
                chunk,
            )
            for row in rows:
                if isinstance(row, dict):
                    row["_llm_timeout_fallback"] = True
                    row["_llm"] = False
        if len(rows) < len(chunk):
            emergency_rows = _generate_queries_mechanical(
                _script_context_for_query_chunk(script, topic, chunk),
                topic,
                chunk,
            )
            for offset in range(len(rows), len(chunk)):
                row = dict(emergency_rows[offset] if offset < len(emergency_rows) else {})
                row["_llm_timeout_fallback"] = True
                row["_llm"] = False
                rows.append(row)
        for offset, row in enumerate(rows[:len(chunk)]):
            if isinstance(row, dict):
                row["scene_number"] = start + offset + 1
                all_rows.append(row)

    return all_rows


def _generate_queries_with_candidates(script: str, topic: str, scene_texts: List[str],
                                      candidates: List[Dict], chunk_size: int = 10) -> List[Dict]:
    errors: List[str] = []
    valid_candidates = [
        c for c in candidates
        if isinstance(c, dict) and str(c.get("provider") or "").strip() and str(c.get("api_key") or "").strip()
    ]
    if not valid_candidates:
        raise RuntimeError("LLM query generation requires at least one configured LLM API key")

    def _candidate_rows(candidate: Dict) -> Tuple[str, List[Dict]]:
        provider = str(candidate.get("provider") or "").strip().lower()
        model = str(candidate.get("model") or "").strip()
        api_key = str(candidate.get("api_key") or "").strip()
        label = f"{provider}/{model or 'default'}"
        if not _llm_key_looks_usable(provider, api_key):
            raise RuntimeError(f"{label}: key failed local validation")
        if provider == "openrouter":
            try:
                from services.openrouter_llm import openrouter_model_available
                if model and not openrouter_model_available(model):
                    raise RuntimeError(f"{label}: model is not listed by OpenRouter")
            except Exception:
                # Availability probes are helpful, but never let a transient
                # model-list failure block the actual fast race.
                pass
        if len(scene_texts) > chunk_size:
            rows = _chunk_query_generation(
                script, topic, scene_texts, api_key, model,
                chunk_size=chunk_size, provider=provider, strict=True,
            )
        else:
            compact_context = _script_context_for_query_chunk(script, topic, scene_texts)
            rows = _generate_queries_llm(
                compact_context, topic, scene_texts, api_key, model,
                provider=provider, strict=True, full_script=script,
            )
        for idx, row in enumerate(rows or []):
            row["scene_number"] = idx + 1
        if rows and len(rows) >= len(scene_texts):
            return label, rows
        raise RuntimeError(f"{label}: returned {len(rows or [])}/{len(scene_texts)} query rows")

    try:
        race_seconds = float(os.environ.get("VIDZEO_QUERY_LLM_CANDIDATE_RACE_SECONDS", "300") or 300)
    except Exception:
        race_seconds = 300.0
    # Each configured provider races in parallel. The former 90-second hard
    # ceiling could abandon all routes while they were still inside their
    # legitimate OpenRouter retry window.
    race_seconds = max(60.0, min(480.0, race_seconds))
    try:
        race_workers_requested = int(os.environ.get("VIDZEO_QUERY_LLM_CANDIDATE_WORKERS", "3") or 3)
    except Exception:
        race_workers_requested = 3
    race_workers = max(1, min(len(valid_candidates), race_workers_requested))
    pool = ThreadPoolExecutor(max_workers=race_workers, thread_name_prefix="query-llm-race")
    futures = {pool.submit(_candidate_rows, candidate): candidate for candidate in valid_candidates}
    pending = set(futures.keys())
    try:
        deadline = time.time() + race_seconds
        while pending:
            remaining = max(0.0, deadline - time.time())
            if remaining <= 0:
                break
            done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                break
            for future in done:
                try:
                    label, rows = future.result()
                    if rows and len(rows) >= len(scene_texts):
                        for other in pending:
                            other.cancel()
                        pool.shutdown(wait=False, cancel_futures=True)
                        _web_log(f"LLM provider selected for web queries: {label}")
                        return rows
                except Exception as exc:
                    errors.append(str(exc))
                    _web_log(f"LLM candidate failed: {errors[-1]}")
        for future in pending:
            future.cancel()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if pending:
        errors.append(f"candidate race exceeded {race_seconds:.0f}s")

    raise RuntimeError("All configured LLM query providers failed: " + " | ".join(errors[-5:]))


# =============================================================================
#  MAIN ENTRY POINT
# =============================================================================

_WEB_LANE_FACETS = (
    "editorial photograph",
    "archive photograph",
    "news photograph",
    "field photograph",
    "reference photograph",
    "wide establishing photograph",
    "close detail photograph",
    "event moment photograph",
)
_YT_LANE_FACETS = (
    "short b-roll footage",
    "wide shot footage",
    "field footage",
    "archival film",
    "event footage",
    "close detail footage",
    "moving b-roll",
    "distant establishing footage",
)


def _youtube_lane_fallback(scene_text: str = "", subject: str = "",
                           title: str = "", category: str = "") -> str:
    """Choose a positive motion-search suffix when a scene has no YT wave.

    A web query is still useful as an identity seed, but its photo-oriented
    wording is a poor fallback for the motion lane.  Keep this deterministic
    and cheap: the normalizer still performs the final length/format cleanup,
    while this helper supplies the camera vocabulary that best matches the
    niche.  It is intentionally permissive so a near-peer clip can survive
    the downstream relevance check.
    """
    context = " ".join((str(category or ""), str(title or ""),
                        str(subject or ""), str(scene_text or ""))).casefold()
    category_key = re.sub(r"[^a-z0-9]+", " ", str(category or "").casefold()).strip()
    rules = (
        (("gaming", "game", "games", "gameplay", "video game", "grand theft auto", "gta ",
          "playstation", "xbox", "nintendo", "esports", "game footage"),
         "gameplay footage"),
        (("sports", "football", "soccer", "cricket", "basketball", "tennis",
          "match", "tournament", "championship", "athlete"),
         "match footage"),
        (("crime", "criminal", "murder", "detective", "forensic", "police",
          "investigation", "court case", "true crime"),
         "investigation footage"),
        (("history", "historical", "ancient", "museum", "war", "battle",
          "monarchy", "royal", "disaster", "earthquake", "flood", "archive"),
         "archival footage"),
        (("travel", "tourism", "destination", "landmark", "city break"),
         "travel footage"),
        (("nature", "wildlife", "animal", "ocean", "forest", "climate"),
         "nature footage"),
        (("automotive", "car", "vehicle", "racing", "motorsport"),
         "driving footage"),
        (("technology", "tech", "software", "smartphone", "robot", "ai ",
          "artificial intelligence", "product"),
         "product demo footage"),
        (("music", "singer", "band", "concert", "performance"),
         "live performance footage"),
    )
    for terms, fallback in rules:
        if category_key == terms[0] or any(term in context for term in terms):
            return fallback
    return "documentary footage"


def _scene_query_facet(scene_text: str, subject: str = "") -> str:
    """Extract two concrete scene terms for provider-specific query lanes.

    Query planning already carries the immutable identity. This helper adds a
    small, deterministic visual facet so neighbouring scenes do not all send
    the same broad ``subject documentary`` query to every provider. It never
    copies a complete narration sentence into a search box.
    """
    try:
        words = _extract_visual_keywords(str(scene_text or ""), max_kws=5)
    except Exception:
        words = []
    subject_tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", str(subject or ""))
    }
    chosen: list[str] = []
    for word in words:
        token = str(word or "").strip()
        if not token or token.casefold() in subject_tokens:
            continue
        if token.casefold() not in {item.casefold() for item in chosen}:
            chosen.append(token)
        if len(chosen) >= 2:
            break
    return " ".join(chosen)


def _append_query_terms(query: str, terms: str, *, max_words: int = 12) -> str:
    base = _clean_query(query)
    additions = [item for item in _clean_query(terms).split() if item]
    if not base or not additions:
        return base
    existing = {item.casefold() for item in base.split()}
    words = base.split()
    for item in additions:
        if item.casefold() in existing:
            continue
        if len(words) >= max_words:
            break
        words.append(item)
        existing.add(item.casefold())
    return " ".join(words[:max_words])


def _ensure_query_diversity(rows: List[Dict], title: str, script: str,
                            scene_texts: List[str]) -> List[Dict]:
    """Ensure queries are diverse across scenes and broad enough to resolve fast."""
    if _detect_royal_family_anchor(title, script):
        rows = _enforce_royal_query_contract(rows or [], title, script, scene_texts)
    for idx, row in enumerate(rows):
        row["scene_number"] = idx + 1
        scene_text = scene_texts[idx] if idx < len(scene_texts) else ""
        context_text = f"{title} {script[:3000]}"
        row["topic_anchor"] = _expand_full_named_subject(
            row.get("topic_anchor", title), scene_text, context_text
        )
        subject = _expand_full_named_subject(
            row.get("subject_query") or row.get("topic_anchor") or title,
            scene_text,
            context_text,
        )
        row["subject_query"] = subject
        row["section_subject"] = _expand_full_named_subject(
            row.get("section_subject") or subject, scene_text, context_text
        )
        row["scene_subject"] = _expand_full_named_subject(
            row.get("scene_subject") or subject, scene_text, context_text
        )
        raw_queries = []
        raw_youtube_queries = []
        for key in (
            "query_variants", "specific", "medium", "general", "fallback_1", "fallback_2",
            "search_query", "supporting_search_query", "alt_search_query",
            "youtube_query", "youtube_supporting_query", "youtube_fallback_query",
        ):
            value = row.get(key)
            if isinstance(value, list):
                raw_queries.extend(str(v) for v in value if v)
            elif value:
                raw_queries.append(str(value))
        for key in ("youtube_query", "youtube_supporting_query", "youtube_fallback_query"):
            value = row.get(key)
            if isinstance(value, list):
                raw_youtube_queries.extend(str(v) for v in value if v)
            elif value:
                raw_youtube_queries.append(str(value))
        repaired = _repair_query_pack(
            [_expand_full_names_in_query(query, scene_text, context_text) for query in raw_queries],
            subject,
            scene_text,
            limit=5,
            context_text=context_text,
        )
        repaired = [
            _expand_full_names_in_query(query, scene_text, context_text)
            for query in repaired
        ]
        if repaired:
            # Web and YouTube have different ranking vocabularies. Keep the
            # LLM's scene intent, then add a bounded lane facet and a concrete
            # scene term to prevent one generic winner from being reused for
            # every scene in a long video.
            scene_facet = _scene_query_facet(scene_text, subject)
            web_variants = []
            for variant_index, query in enumerate(repaired[:5]):
                lane_facet = _WEB_LANE_FACETS[(idx + variant_index) % len(_WEB_LANE_FACETS)]
                extra = " ".join(item for item in (scene_facet, lane_facet) if item)
                web_variants.append(_append_query_terms(query, extra, max_words=12))
            repaired = list(dict.fromkeys(web_variants)) or repaired
            row["query_variants"] = repaired
            row["specific"] = repaired[0]
            row["medium"] = repaired[1] if len(repaired) > 1 else repaired[0]
            row["general"] = repaired[2] if len(repaired) > 2 else repaired[-1]
            row["fallback_1"] = repaired[3] if len(repaired) > 3 else repaired[-1]
            row["fallback_2"] = repaired[4] if len(repaired) > 4 else repaired[-1]
            # Keep every provider-facing alias synchronized. A prebuilt Brain
            # row may have only search_query/youtube_query populated; leaving
            # those stale lets one branch reintroduce a bare surname.
            row["search_query"] = repaired[0]
            row["supporting_search_query"] = repaired[1] if len(repaired) > 1 else repaired[0]
            row["alt_search_query"] = repaired[2] if len(repaired) > 2 else repaired[-1]
            # Do not mirror the web/photo query back into the motion lane.
            # They have different retrieval vocabularies and the old
            # synchronization was another source of sentence-shaped YouTube
            # searches.  Preserve existing YouTube waves, then normalize them
            # as positive full-name phrases.
            category = str(row.get("domain_category") or "")
            yt_fallback = _youtube_lane_fallback(
                scene_text=scene_text,
                subject=subject,
                title=title,
                category=category,
            )
            # Mechanical/legacy rows often contain only web fields. Seed the
            # YT lane from the scene facet in that case instead of normalizing
            # the full photo query into a generic documentary search.
            yt_seed_queries = raw_youtube_queries
            if not yt_seed_queries:
                yt_seed = " ".join(item for item in (subject, scene_facet, yt_fallback) if item)
                yt_seed_queries = [yt_seed] if yt_seed else [yt_fallback]
            try:
                from services.youtube_query_normalizer import normalize_youtube_queries
                yt_repaired = normalize_youtube_queries(
                    yt_seed_queries,
                    anchor=subject,
                    category=category,
                    fallback=yt_fallback,
                )
            except Exception:
                yt_repaired = yt_seed_queries
            yt_variants = []
            for variant_index, query in enumerate(yt_repaired[:8]):
                lane_facet = _YT_LANE_FACETS[(idx + variant_index) % len(_YT_LANE_FACETS)]
                extra = " ".join(item for item in (scene_facet, lane_facet) if item)
                try:
                    from services.youtube_query_normalizer import normalize_youtube_query
                    enriched = normalize_youtube_query(
                        _append_query_terms(query, extra, max_words=12),
                        anchor=subject,
                        category=category,
                        fallback=yt_fallback,
                    )
                except Exception:
                    enriched = _append_query_terms(query, extra, max_words=12)
                if enriched:
                    yt_variants.append(enriched)
            yt_repaired = list(dict.fromkeys(yt_variants)) or yt_repaired
            row["youtube_query"] = yt_repaired[0] if yt_repaired else repaired[0]
            row["youtube_supporting_query"] = yt_repaired[1] if len(yt_repaired) > 1 else row["youtube_query"]
            row["youtube_fallback_query"] = yt_repaired[2] if len(yt_repaired) > 2 else row["youtube_query"]
    return rows


def _llm_key_looks_usable(provider: str, key: str) -> bool:
    key = (key or "").strip()
    if not key:
        return False
    provider = (provider or "").strip().lower()
    if provider == "external_agent":
        from services.agent_bridge import AGENT_HANDLE
        return key.startswith(AGENT_HANDLE) and len(key) > len(AGENT_HANDLE)
    if provider == "chatgpt":
        from services.provider_selection import account_from_handle
        return bool(account_from_handle(key))
    if provider == "openrouter":
        return key.startswith(("sk-or-", "sk-")) and len(key) >= 30
    return len(key) >= 30 and "invalid" not in key.lower()


def generate_web_image_queries(script: str, title: str, scene_texts: List[str],
                                api_key: str = "", llm_provider: str = "openrouter",
                                llm_model: str = "",
                                llm_candidates: Optional[List[Dict]] = None) -> List[Dict]:
    """Generate per-scene search queries.

    Uses OpenRouter if an API key is provided, then falls back mechanically.
    LLM queries understand scene context and generate visually searchable terms.
    """
    effective_title, _ = _derive_effective_web_title(title, script)
    _web_log(f"topic anchor: {effective_title}")

    real_provider = llm_provider if llm_provider in {"openrouter", "external_agent"} else "openrouter"

    if llm_model:
        model = llm_model.strip()
    else:
        model = "qwen/qwen3.5-flash-02-23"

    key = api_key.strip()
    force_mechanical = str(
        os.environ.get("VIDZEO_WEB_QUERY_MODE", "llm")
    ).strip().lower() in {"mechanical", "fast", "simple", "off"}
    if key and force_mechanical:
        _web_log("query planning: mechanical web query mode requested; skipping LLM")
        key = ""

    candidates = list(llm_candidates or [])
    if key and not candidates:
        candidates = [{"provider": real_provider, "api_key": key, "model": model}]
    allow_mechanical_fallback = str(
        os.environ.get("VIDZEO_WEB_ALLOW_MECHANICAL_FALLBACK", "1")
    ).strip().lower() in {"1", "true", "yes", "on"}

    rows = []
    if candidates and not force_mechanical:
        _web_log(f"query planning: {len(scene_texts)} scenes, provider={real_provider}, model={model}")
        _web_log(
            "query planning LLM candidates: "
            + ", ".join(
                f"{str(c.get('provider') or '').strip().lower()}/{str(c.get('model') or 'default').strip()}"
                for c in candidates if isinstance(c, dict)
            )
        )
        rows = _generate_queries_with_candidates(
            script, effective_title, scene_texts, candidates, chunk_size=_query_chunk_size()
        )

        if rows and len(rows) >= len(scene_texts):
            rows = _ensure_query_diversity(rows, effective_title, script, scene_texts)
            _web_log(f"{len(rows)} context-aware queries generated via LLM")
            return rows

        if not allow_mechanical_fallback:
            raise RuntimeError("LLM query generation incomplete and mechanical fallback is disabled")

        if rows:
            if not allow_mechanical_fallback:
                raise RuntimeError(
                    f"LLM query generation returned only {len(rows)}/{len(scene_texts)} rows; "
                    "mechanical fallback is disabled"
                )
            _web_log(f"LLM partial: {len(rows)}/{len(scene_texts)} — filling gaps mechanically")
            mech_rows = _generate_queries_mechanical(script, effective_title, scene_texts)
            merged = [None] * len(scene_texts)
            for r in rows:
                sn = r.get("scene_number", 1) - 1
                if 0 <= sn < len(scene_texts):
                    merged[sn] = r
            for i in range(len(scene_texts)):
                if merged[i] is None:
                    merged[i] = mech_rows[i] if i < len(mech_rows) else mech_rows[-1]
            rows = _ensure_query_diversity(merged, effective_title, script, scene_texts)
            llm_count = sum(1 for r in rows if r.get("_llm"))
            mech_count = len(rows) - llm_count
            _web_log(f"{len(rows)} hybrid queries ({llm_count} LLM, {mech_count} mechanical)")
            return rows

        _web_log("LLM query generation incomplete — falling back to mechanical")

    if not allow_mechanical_fallback and not force_mechanical:
        raise RuntimeError("LLM query generation requires a valid configured LLM provider/key")

    rows = _generate_queries_mechanical(script, effective_title, scene_texts)
    rows = _ensure_query_diversity(rows, effective_title, script, scene_texts)
    _web_log(f"{len(rows)} web image queries generated (mechanical)")
    return rows
