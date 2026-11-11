import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, wait
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from typing import Dict, List

_CATEGORY_HINTS = [
    "business", "news", "politics", "finance", "technology", "science",
    "sports", "travel", "nature", "lifestyle", "education", "entertainment",
    "other",
]

_SETTING_HINTS = ["indoor", "outdoor", "nature", "city", "abstract"]
_SUBJECT_HINTS = ["people", "nature", "object", "place", "abstract"]
_QUERY_CHUNK_SIZE = 5
_QUERY_CHUNK_WORKERS = 3
DEFAULT_TOOL_BRAIN_MODEL = "qwen/qwen3.5-flash-02-23"



def _stock_query_chunk_size() -> int:
    try:
        return max(2, min(8, int(os.environ.get("VIDZEO_STOCK_LLM_QUERY_CHUNK_SIZE", "5") or 5)))
    except Exception:
        return 5


def _stock_query_workers() -> int:
    try:
        return max(1, min(8, int(os.environ.get("VIDZEO_STOCK_LLM_QUERY_WORKERS", "4") or 4)))
    except Exception:
        return 4
_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "about", "after",
    "before", "while", "when", "what", "why", "how", "your", "their", "there",
    "then", "than", "have", "has", "had", "will", "would", "could", "should",
    "into", "onto", "over", "under", "between", "around", "through", "across",
    "video", "videos", "scene", "scenes", "clip", "clips", "footage", "story",
    "stories", "show", "shows", "showing", "make", "makes", "made", "using",
    "used", "still", "just", "very", "more", "most", "much", "many", "only",
    "like", "than", "they", "them", "those", "these", "because", "also", "every",
    "next", "phase", "analysts", "quietly", "expected", "faster", "focuses",
    "says", "saying", "reportedly", "nearly", "around", "you", "know", "think",
}
_SHORT_KEEPERS = {"ai", "vr", "ar", "ui", "ux", "3d", "4k", "uk", "us"}
_CATEGORY_KEYWORDS = {
    "business": {"office", "company", "corporate", "startup", "boardroom", "employee", "manager"},
    "news": {"news", "headline", "report", "breaking", "press", "statement", "briefing"},
    "politics": {"president", "minister", "election", "government", "parliament", "senate", "campaign", "summit"},
    "finance": {"bank", "money", "market", "stock", "trading", "crypto", "bitcoin", "finance", "investor"},
    "technology": {"technology", "tech", "ai", "software", "robot", "chip", "server", "data", "digital", "app"},
    "science": {"science", "research", "laboratory", "lab", "experiment", "space", "rocket", "satellite"},
    "sports": {"sport", "sports", "match", "game", "player", "stadium", "team", "trophy", "goal"},
    "travel": {"travel", "airport", "hotel", "tourist", "journey", "destination", "passport"},
    "nature": {"nature", "forest", "ocean", "wildlife", "mountain", "river", "beach", "reef", "desert"},
    "lifestyle": {"family", "home", "kitchen", "fitness", "fashion", "lifestyle", "wellness", "wedding"},
    "education": {"school", "classroom", "student", "teacher", "university", "campus", "lecture"},
    "entertainment": {"music", "concert", "movie", "gaming", "festival", "celebrity", "film"},
}
_SETTING_KEYWORDS = {
    "indoor": {"office", "room", "studio", "lab", "factory", "kitchen", "classroom", "warehouse", "hospital"},
    "outdoor": {"street", "road", "city", "bridge", "stadium", "airport", "beach", "harbor", "coast"},
    "nature": {"forest", "ocean", "mountain", "river", "reef", "desert", "wildlife", "lake"},
    "city": {"city", "urban", "downtown", "skyline", "traffic", "street", "station"},
}
_CATEGORY_VISUAL_NOUNS = {
    "business": ["office", "meeting", "workspace", "team"],
    "news": ["briefing", "crowd", "street", "podium"],
    "politics": ["podium", "flags", "summit", "government"],
    "finance": ["trading", "office", "chart", "bank"],
    "technology": ["server", "device", "robot", "factory"],
    "science": ["laboratory", "research", "rocket", "satellite"],
    "sports": ["stadium", "training", "match", "trophy"],
    "travel": ["airport", "street", "landmark", "coast"],
    "nature": ["ocean", "forest", "mountain", "wildlife"],
    "lifestyle": ["family", "home", "kitchen", "fitness"],
    "education": ["classroom", "campus", "student", "lecture"],
    "entertainment": ["concert", "stage", "festival", "crowd"],
    "other": ["people", "street", "landscape", "detail"],
}
_PLACE_TERMS = {
    "city", "street", "skyline", "airport", "beach", "mountain", "forest", "ocean",
    "office", "factory", "warehouse", "stadium", "campus", "lab", "hospital",
}
_PEOPLE_TERMS = {
    "person", "people", "man", "woman", "child", "family", "team", "leader", "worker",
    "student", "teacher", "player", "doctor", "scientist", "soldier", "founder",
    "president", "minister", "ceo", "artist",
}
_ABSTRACT_SCENE_TERMS = {
    "power", "future", "growth", "change", "strategy", "balance", "impact", "issue",
    "problem", "story", "stories", "question", "questions", "reason", "reasons",
}
_OBJECT_TERMS = {
    "carrier", "missile", "radar", "shipyard", "shipyards", "network", "networks",
    "factory", "robot", "server", "chip", "device", "rocket", "satellite",
    "document", "map", "chart", "vehicle", "car", "aircraft", "submarine",
    "backpack", "laptop", "computer", "keyboard", "screen", "app",
}

_BROLL_CUE_MAP = [
    ({"arrive", "arrives", "arrival", "immigrant", "canada", "backpack", "journey", "move", "moved"}, ["traveler", "backpack", "airport"]),
    ({"code", "codes", "coding", "program", "programmer", "software", "computer"}, ["programmer", "laptop", "code"]),
    ({"rocket", "rockets", "spacex", "space", "launch"}, ["rocket", "launch", "smoke"]),
    ({"tesla", "electric", "vehicle", "car", "factory"}, ["electric", "car", "factory"]),
    ({"bank", "banking", "money", "payment", "paypal", "xcom", "finance"}, ["fintech", "app", "laptop"]),
    ({"startup", "founder", "company", "office", "grind"}, ["startup", "office", "laptop"]),
    ({"school", "student", "college", "university", "class"}, ["student", "campus", "classroom"]),
    ({"meeting", "deal", "sell", "sold", "investor", "pitch"}, ["business", "meeting", "office"]),
]


def _prefer_structured_model(provider: str, model: str) -> str:
    provider = normalize_provider(provider)
    selected = (model or "").strip()
    if provider == "openrouter":
        return selected or os.environ.get("VIDZEO_OPENROUTER_QUERY_MODEL") or "qwen/qwen3.5-flash-02-23"
    return selected or os.environ.get("VIDZEO_QUERY_LLM_MODEL") or DEFAULT_TOOL_BRAIN_MODEL


def normalize_provider(provider: str) -> str:
    provider = str(provider or "").strip().lower()
    return "external_agent" if provider == "external_agent" else "openrouter"


def _build_client(provider: str, api_key: str, model: str):
    provider = normalize_provider(provider)
    model = _prefer_structured_model(provider, model)
    from services.openrouter_llm import OpenRouterLLM
    return OpenRouterLLM(api_key=api_key, model=model or "qwen/qwen3.5-flash-02-23")


def _dedupe_words(words: List[str]) -> List[str]:
    seen = set()
    result = []
    for word in words:
        cleaned = re.sub(r"[^a-zA-Z0-9'-]", "", (word or "").strip()).lower()
        if cleaned.endswith("'s"):
            cleaned = cleaned[:-2]
        cleaned = cleaned.strip("-'")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _dedupe_phrases(values: List[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        normalized = re.sub(r"\s+", " ", str(value or "")).strip(" ,.;:!?-").lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _extract_terms(text: str) -> List[str]:
    words = re.findall(r"[A-Za-z0-9'-]+", text or "")
    terms = []
    for word in words:
        cleaned = word.strip("-'").replace("'", "").lower()
        if len(cleaned) < 3 and cleaned not in _SHORT_KEEPERS:
            continue
        if cleaned in _STOPWORDS:
            continue
        terms.append(cleaned)
    return _dedupe_words(terms)


def _compact_phrase(text: str, limit: int = 4) -> str:
    terms = []
    for word in _extract_terms(text):
        # Strip apostrophes (contractions like "they're" -> "theyre")
        clean = word.replace("'", "")
        if len(clean) <= 2:
            continue
        if clean in {"the", "and", "for", "with", "that", "this", "from", "into", "about", "they", "you", "are", "was", "were", "been", "have", "has", "had", "not", "but", "its", "all", "can", "just", "also", "very", "much", "more", "some", "them", "than", "then", "each", "what", "when", "where", "which", "will", "would", "could", "should", "been", "being", "does", "done", "made", "make", "take", "get", "got", "see", "went", "come", "came", "say", "said", "know", "need", "like", "want", "use", "used", "think", "look", "find", "give", "tell", "help", "show", "try", "keep", "let", "begin", "start"}:
            continue
        terms.append(clean)
    return " ".join(_dedupe_words(terms)[:limit]).strip()


def _extract_named_phrases(text: str) -> List[str]:
    phrases = []
    for match in re.findall(r"\b([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){0,2})\b", text or ""):
        cleaned = " ".join(_extract_terms(match))
        if cleaned:
            phrases.append(cleaned)
    return _dedupe_phrases(phrases)


def _infer_category(terms: List[str]) -> str:
    term_set = set(terms)
    best_category = "other"
    best_score = 0
    for category, keywords in _CATEGORY_KEYWORDS.items():
        score = len(term_set & keywords)
        if score > best_score:
            best_category = category
            best_score = score
    return best_category


def _infer_setting(terms: List[str], category: str) -> str:
    term_set = set(terms)
    best_setting = "abstract"
    best_score = 0
    for setting, keywords in _SETTING_KEYWORDS.items():
        score = len(term_set & keywords)
        if score > best_score:
            best_setting = setting
            best_score = score
    if best_setting != "abstract":
        return best_setting
    if category in {"travel", "nature", "sports"}:
        return "outdoor"
    if category in {"business", "technology", "finance", "education"}:
        return "indoor"
    return "abstract"


def _infer_subject(terms: List[str], category: str, setting: str) -> str:
    term_set = set(terms)
    if term_set & _PEOPLE_TERMS:
        return "people"
    if term_set & _OBJECT_TERMS:
        return "object"
    if term_set & _PLACE_TERMS or setting in {"city", "nature", "outdoor"}:
        return "place"
    if category in {"nature", "travel"}:
        return "nature"
    if category in {"technology", "science", "finance"}:
        return "object"
    return "people" if category in {"business", "politics", "education", "entertainment"} else "object"


def _visual_nouns_for_scene(category: str, subject: str, setting: str) -> List[str]:
    nouns = list(_CATEGORY_VISUAL_NOUNS.get(category, _CATEGORY_VISUAL_NOUNS["other"]))
    if subject == "people":
        nouns.extend(["people", "portrait", "team"])
    elif subject == "place":
        nouns.extend(["location", "street", "landscape"])
    elif subject == "nature":
        nouns.extend(["landscape", "wildlife", "environment"])
    else:
        nouns.extend(["detail", "object", "equipment"])
    if setting == "city":
        nouns.extend(["city", "skyline"])
    elif setting == "nature":
        nouns.extend(["nature", "outdoors"])
    elif setting == "indoor":
        nouns.extend(["interior"])
    return _dedupe_words(nouns)


def _compose_query(parts: List[str], limit: int = 5) -> str:
    tokens: List[str] = []
    for part in parts:
        if not part:
            continue
        if " " in part:
            normalized = " ".join(_extract_terms(part))
            if normalized:
                tokens.append(normalized)
            continue
        normalized = part.strip().lower()
        if normalized and normalized not in _STOPWORDS:
            tokens.append(normalized)
    return " ".join(_dedupe_phrases(tokens)[:limit]).strip()


def _prefer_visual_terms(terms: List[str]) -> List[str]:
    selected = []
    for term in terms:
        if term in _ABSTRACT_SCENE_TERMS:
            continue
        if term.endswith(("ly", "ing", "ed")) and term not in {"building", "trading"}:
            continue
        selected.append(term)
    return selected or terms


def _cue_visual_terms(scene_text: str) -> List[str]:
    scene_terms = set(_extract_terms(scene_text))
    cues: List[str] = []
    for trigger_terms, visual_terms in _BROLL_CUE_MAP:
        if scene_terms & trigger_terms:
            cues.extend(visual_terms)
    return _dedupe_phrases(cues)


def _select_query_terms(scene_terms: List[str], title_terms: List[str], named_terms: List[str]) -> List[str]:
    ordered = _dedupe_phrases(named_terms + scene_terms + title_terms)
    ranked = []
    for term in ordered:
        if term in _ABSTRACT_SCENE_TERMS:
            continue
        score = 0
        if term in named_terms:
            score += 5
        if term in scene_terms:
            score += 4
        if term in _OBJECT_TERMS or term in _PLACE_TERMS:
            score += 4
        if term in _PEOPLE_TERMS:
            score += 3
        if len(term) >= 6:
            score += 1
        ranked.append((score, term))
    ranked.sort(key=lambda item: (-item[0], ordered.index(item[1])))
    return [term for _, term in ranked]


def _scene_seed_row(scene_text: str, title: str, scene_index: int) -> Dict:
    title_terms = _extract_terms(title)
    scene_terms = _extract_terms(scene_text)
    title_named = _extract_named_phrases(title)
    scene_named = _extract_named_phrases(scene_text)
    title_name_words = set(_extract_terms(" ".join(title_named)))
    scene_named_for_search = [
        phrase for phrase in scene_named
        if not (set(_extract_terms(phrase)) & title_name_words)
    ]
    scene_visual_terms = _prefer_visual_terms(scene_terms)
    cue_terms = _cue_visual_terms(scene_text)
    scene_visual_terms = [
        term for term in scene_visual_terms
        if term not in title_name_words
    ] or scene_visual_terms
    joined = _dedupe_phrases(scene_named + title_named + scene_visual_terms + title_terms)

    category = _infer_category(joined)
    setting = _infer_setting(joined, category)
    subject = _infer_subject(joined, category, setting)
    visual_nouns = _visual_nouns_for_scene(category, subject, setting)
    stock_nouns = cue_terms[:3] or visual_nouns

    focus_terms = _select_query_terms(
        cue_terms[:4] + scene_visual_terms[:8] + stock_nouns[:3],
        [],
        scene_named_for_search[:2],
    )
    if not focus_terms:
        focus_terms = _dedupe_phrases(stock_nouns[:3] + [setting, category, subject])

    primary = _compose_query(focus_terms[:4], limit=6)
    if not primary:
        primary = _compose_query([subject, category, visual_nouns[0] if visual_nouns else setting], limit=4)

    alt = _compose_query(focus_terms[1:5] + stock_nouns[:1], limit=6)
    if not alt:
        alt = _compose_query([focus_terms[0] if focus_terms else subject, setting, stock_nouns[0] if stock_nouns else category], limit=4)

    fallback = _compose_query(
        focus_terms[:2] + ([focus_terms[2]] if len(focus_terms) > 2 else stock_nouns[:1]),
        limit=5,
    )
    if not fallback:
        fallback = _compose_query([subject, category, setting], limit=4)

    # Build a keyword-based query from concrete scene phrases for stock search
    raw_terms = scene_named[:3] + scene_visual_terms[:6] + stock_nouns[:2]
    raw_query = _compose_query(raw_terms, limit=6)

    query_variants = _dedupe_phrases([
        primary,
        alt,
        fallback,
        raw_query,
        _compose_query(focus_terms[:1] + stock_nouns[:2], limit=5),
        _compose_query(cue_terms[:3], limit=5),
    ])

    return {
        "scene_number": scene_index + 1,
        "query": primary,
        "alt": alt or primary,
        "primary_query": primary,
        "alt_query": alt or primary,
        "fallback_query": fallback or subject,
        "subject": subject,
        "category": category,
        "setting": setting,
        "mood": "neutral",
        "preferred_source": "pexels" if category in {"business", "technology", "travel", "sports", "lifestyle"} or subject in {"people", "place"} else "pixabay",
        "scene_focus_terms": focus_terms[:5],
        "support_terms": title_terms[:4],
        "query_variants": [variant for variant in query_variants if variant][:5],
    }


def _mechanical_queries(scene_texts: List[str], title: str) -> List[Dict]:
    return [_scene_seed_row(scene_text, title, idx) for idx, scene_text in enumerate(scene_texts)]


def _chunk_scene_texts(scene_texts: List[str], chunk_size: int = _QUERY_CHUNK_SIZE) -> List[tuple[int, List[str]]]:
    chunk_size = _stock_query_chunk_size()
    chunks = []
    for start in range(0, len(scene_texts), max(1, chunk_size)):
        chunks.append((start, scene_texts[start:start + chunk_size]))
    return chunks


def _extract_json_text(raw: str) -> str | None:
    candidate = str(raw or "").strip()
    if not candidate:
        return None
    if "```" in candidate:
        for part in candidate.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{") or part.startswith("["):
                candidate = part
                break
    possible = []
    object_start = candidate.find("{")
    object_end = candidate.rfind("}")
    array_start = candidate.find("[")
    array_end = candidate.rfind("]")
    if object_start != -1 and object_end > object_start:
        possible.append(candidate[object_start:object_end + 1])
    if array_start != -1 and array_end > array_start:
        possible.append(candidate[array_start:array_end + 1])
    possible.append(candidate)
    for item in possible:
        cleaned = re.sub(r",\s*([}\]])", r"\1", item)
        cleaned = re.sub(r"[\x00-\x1f]+", " ", cleaned)
        try:
            json.loads(cleaned)
            return cleaned
        except Exception:
            continue
    return None


def _load_json_payload(raw: str) -> Dict:
    text = _extract_json_text(raw) or str(raw or "").strip()
    if not text:
        raise ValueError("empty LLM response")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("LLM payload was not a JSON object")
    return payload


def generate_stock_media_queries(
    script: str,
    title: str,
    scene_texts: List[str],
    api_key: str = "",
    provider: str = "openrouter",
    model: str = "",
    total_duration: float = 60.0,
    video_format: str = "",
    domain_anchor_category: str = "",
) -> List[Dict]:
    """
    Convert scene text into stock-media-friendly query rows.
    """
    if not scene_texts:
        return []

    provider = normalize_provider(provider)
    selected_model = _prefer_structured_model(provider, model)

    stock_query_mode = os.environ.get("VIDZEO_STOCK_QUERY_MODE") or os.environ.get("VIDZEO_QUERY_GENERATION_MODE") or "llm"
    use_stock_llm = str(stock_query_mode).strip().lower() == "llm"
    if api_key and use_stock_llm:
        try:
            print(
                f"   Stock query planning: {len(scene_texts)} scenes, "
                f"{len(_chunk_scene_texts(scene_texts))} chunk(s), "
                f"{min(_stock_query_workers(), max(1, len(_chunk_scene_texts(scene_texts))))} worker(s), "
                f"{provider}/{selected_model}"
            )
            rows = _generate_with_llm(
                script=script,
                title=title,
                scene_texts=scene_texts,
                api_key=api_key,
                provider=provider,
                model=selected_model,
                total_duration=total_duration,
                video_format=video_format,
                domain_anchor_category=domain_anchor_category,
            )
            if rows and len(rows) >= len(scene_texts):
                print(f"   Stock queries ready: {len(rows)} scene rows via {provider}")
                return rows[:len(scene_texts)]
        except Exception as exc:
            print(f"   Stock query LLM failed: {exc}")

    fallback = _mechanical_queries(scene_texts, title)
    print(f"   Stock queries ready: {len(fallback)} scene rows (mechanical fallback)")
    return fallback


def _generate_with_llm(
    script: str,
    title: str,
    scene_texts: List[str],
    api_key: str,
    provider: str,
    model: str,
    total_duration: float,
    video_format: str = "",
    domain_anchor_category: str = "",
) -> List[Dict]:
    chunks = _chunk_scene_texts(scene_texts)
    if len(chunks) == 1:
        return _generate_with_llm_chunk(
            script=script,
            title=title,
            scene_texts=scene_texts,
            api_key=api_key,
            provider=provider,
            model=model,
            total_duration=total_duration,
            scene_offset=0,
            video_format=video_format,
            domain_anchor_category=domain_anchor_category,
        )

    ordered_rows: Dict[int, List[Dict]] = {}
    failures: List[str] = []
    worker_count = min(_stock_query_workers(), max(1, len(chunks)))
    executor = ThreadPoolExecutor(max_workers=worker_count)
    try:
        future_map = {
            executor.submit(
                _generate_with_llm_chunk,
                script,
                title,
                chunk_scene_texts,
                api_key,
                provider,
                model,
                total_duration,
                start_index,
                video_format,
                domain_anchor_category,
            ): start_index
            for start_index, chunk_scene_texts in chunks
        }
        from .media_query_strategy import query_planning_budget
        # Queries are the foundation: an agent answer needs a full
        # round-trip per wave, not a 120s cut-off that replaces real queries
        # with mechanical ones.
        per_wave = None
        if str(provider or "").lower() == "external_agent":
            try:
                per_wave = max(60.0, float(os.environ.get("VIDZEO_AGENT_MIN_WALL_SECONDS", "600") or 600))
            except (TypeError, ValueError):
                per_wave = 600.0
        completed, pending = wait(future_map, timeout=query_planning_budget(len(chunks), worker_count, per_wave_seconds=per_wave))
        for future in completed:
            start_index = future_map[future]
            try:
                ordered_rows[start_index] = future.result()
            except Exception as exc:
                failures.append(f"chunk {start_index + 1}: {exc}")
                chunk_scene_texts = next(
                    (chunk_rows for chunk_start, chunk_rows in chunks if chunk_start == start_index),
                    [],
                )
                ordered_rows[start_index] = _mechanical_queries(chunk_scene_texts, title)
        for future in pending:
            future.cancel()
            failures.append(f"chunk {future_map[future] + 1}: planning deadline handoff")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    if failures:
        print(f"   Stock query partial fallback: {'; '.join(failures)}")

    merged: List[Dict] = []
    for start_index, chunk_scene_rows in chunks:
        chunk_rows = ordered_rows.get(start_index) or []
        fallback_rows = None
        for offset in range(len(chunk_scene_rows)):
            row = chunk_rows[offset] if offset < len(chunk_rows) else None
            if not isinstance(row, dict) or not row:
                if fallback_rows is None:
                    fallback_rows = _mechanical_queries(chunk_scene_rows, title)
                row = dict(fallback_rows[offset])
                row['_query_fallback'] = 'incomplete_or_timed_out_chunk'
            merged.append(row)
    return merged[:len(scene_texts)]


def _generate_with_llm_chunk(
    script: str,
    title: str,
    scene_texts: List[str],
    api_key: str,
    provider: str,
    model: str,
    total_duration: float,
    scene_offset: int = 0,
    video_format: str = "",
    domain_anchor_category: str = "",
) -> List[Dict]:
    client = _build_client(provider, api_key, model)

    scene_rows = []
    for idx, scene_text in enumerate(scene_texts):
        prev_text = scene_texts[idx - 1][:120] if idx > 0 else ""
        next_text = scene_texts[idx + 1][:120] if idx + 1 < len(scene_texts) else ""
        scene_rows.append(
            {
                "scene_number": scene_offset + idx + 1,
                "scene": scene_text[:350],
                "prev": prev_text,
                "next": next_text,
            }
        )

    script_sample = re.sub(r"\s+", " ", str(script or "")).strip()[:700]
    compact_script_context = "\n".join(
        part for part in [
            f"Title/topic: {title}",
            "Current chunk scenes: " + " ".join(str(item or "")[:170] for item in scene_texts),
            f"Script sample: {script_sample}",
        ]
        if part.strip()
    )

    prompt = f"""You are a stock media B-roll query director.

Convert each scene into concrete stock-video search metadata for Pexels and Pixabay.
These libraries usually do not contain exact celebrity, brand, or news-event footage.
Your job is to translate the narration into the best visual B-roll substitute.

Video title: "{title}"
Duration: {total_duration:.1f}s"""

    if video_format or domain_anchor_category:
        prompt += f"""
Video format: {video_format or 'script_like'}
Domain: {domain_anchor_category or title}"""

    prompt += f"""

Compact script context:
{compact_script_context}

Scenes:
{json.dumps(scene_rows, ensure_ascii=True)}

Return JSON object only in this exact shape:
{{
  "scenes": [
    {{
      "scene_number": 1,
      "query": "3 to 4 word noun phrase",
      "alt": "broader 2 to 4 word noun phrase",
      "primary_query": "3 to 4 word noun phrase",
      "alt_query": "broader 2 to 4 word noun phrase",
      "fallback_query": "subject category setting phrase",
      "subject": "one of [people, nature, object, place, abstract]",
      "category": "one of [business, news, politics, finance, technology, science, sports, travel, nature, lifestyle, education, entertainment, other]",
      "setting": "one of [indoor, outdoor, nature, city, abstract]",
      "mood": "short mood label",
      "preferred_source": "pexels or pixabay",
      "scene_focus_terms": ["specific visual noun", "specific visual noun"],
      "support_terms": ["context term", "context term"],
      "query_variants": ["specific b-roll phrase", "broader b-roll phrase", "fallback b-roll phrase"]
    }}
  ]
}}

Rules:
- Use noun phrases only, no sentences.
- No verbs.
- No abstract concepts unless the scene is truly abstract.
- Preserve the video's global context. If the title/script is about football,
  FIFA World Cup, a stadium, fans, or supporters, mood words like "silence",
  "tension", or "waiting" must become sports B-roll such as "stadium crowd",
  "football fans", "supporters waiting", or "match day crowd" - never nature,
  lakes, meditation, weather, generic travel, or unrelated country footage.
- Prefer visible B-roll subjects: people, office, laptop, code screen, airport, backpack, factory, rocket launch, electric car, meeting, skyline, document, crowd, map, classroom, lab, server room.
- Do not put celebrity/person names in primary_query unless the stock library could reasonably have that exact object/event. Use them only in support_terms.
- Replace named-person narration with visual substitutes, for example:
  "Elon Musk arrives in Canada" -> "young traveler backpack airport"
  "startup grind" -> "startup office laptop"
  "online bank" -> "fintech app laptop"
  "rocket ambition" -> "rocket launch smoke"
- Make primary_query the most specific visual B-roll phrase and alt_query a broader visual fallback.
- Keep the actual visible subject and action together. A diesel locomotive starter is not a road-car starter; preserve the railway context through every fallback.
- Make query_variants meaningfully different views or supported synonyms, not punctuation changes or the same title repeated. Prefer component, operator, and environment views that support this scene.
- For an invisible process, choose a real visible component or contextual operation. Do not demand a photograph of an abstract causal claim or invent a model/year/event to make it searchable.
- Require an exact named identity only when the narration requires it. Contextual stock cannot prove a specific historical event or a particular named person.
- Keep each query short and natural.
- For MULTI_SUBJECT videos: Each scene covers an independent subject within the
  shared domain. ALWAYS include domain context in every query while using
  scene-specific visual terms. Example: for "Top 10 Fruits", every query must
  contain "fruit" or the specific fruit name together with scene visuals.
- For SCRIPT_LIKE videos: Scenes flow naturally within a common domain.
  Preserve the domain's global context throughout all scenes. Scene-specific
  terms add variety but must NOT replace the domain anchor.
- For SUBJECTS_TOGETHER videos: Multiple subjects co-exist in shared scenes.
  Include all relevant subjects in queries; prefer comparison-friendly visual
  terms (side-by-side, comparison, versus, together).
- For an explicitly narrated matchup or comparison, preserve each exact pair
  in its own query and keep the same left/right subjects. Never blend a list of
  pairings into one query or silently substitute another team, person, product,
  place, or era. The query adapter may split a narrated list into separate
  exact searches.
- When a beat names a venue, route, object, or specific event, search that
  visible evidence first. Use broad domain footage only for an atmospheric
  beat; sports footage means real-world sport, never video-game footage unless
  the script itself is about gaming.
- If the scene is ambiguous, use the nearest concrete setting/object from the scene, not random narration words.
- Do not drift into visa, weather forecast, travel vlog, politics, reporter,
  news desk, or talking-head concepts unless the script scene is actually about that.
- query_variants must be visually different enough to avoid repeated generic clips.
- Do not add commentary outside the JSON.
"""

    schema = {
        "type": "object",
        "properties": {
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "scene_number": {"type": "integer"},
                        "primary_query": {"type": "string"},
                        "alt_query": {"type": "string"},
                        "fallback_query": {"type": "string"},
                        "subject": {"type": "string"},
                        "category": {"type": "string"},
                        "setting": {"type": "string"},
                        "mood": {"type": "string"},
                        "preferred_source": {"type": "string"},
                        "scene_focus_terms": {"type": "array", "items": {"type": "string"}},
                        "support_terms": {"type": "array", "items": {"type": "string"}},
                        "query_variants": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": [
                        "scene_number", "primary_query", "alt_query", "fallback_query",
                        "subject", "category", "setting", "mood", "preferred_source",
                        "scene_focus_terms", "support_terms", "query_variants"
                    ],
                    "additionalProperties": False
                }
            }
        },
        "required": ["scenes"],
        "additionalProperties": False
    }

    raw = client.chat(
        system_prompt="Return valid JSON only.",
        user_prompt=prompt,
        temperature=0.15,
        max_tokens=3500,
        json_mode=True,
        json_schema=schema,
        timeout_seconds=120.0,
        total_timeout_seconds=120.0,
    )

    payload = _load_json_payload(raw)
    rows = payload.get("scenes", [])
    if not isinstance(rows, list):
        return []

    cleaned = []
    for idx, scene_text in enumerate(scene_texts):
        expected_scene_number = scene_offset + idx + 1
        row = next(
            (
                item for item in rows
                if isinstance(item, dict) and int(item.get("scene_number", -1)) == expected_scene_number
            ),
            rows[idx] if idx < len(rows) and isinstance(rows[idx], dict) else {},
        )
        seed = _scene_seed_row(scene_text, title, idx)
        primary = _compact_phrase(str(row.get("primary_query", "")).strip(), limit=4) or seed["primary_query"]
        alt = _compact_phrase(str(row.get("alt_query", "")).strip(), limit=4) or seed["alt_query"]
        fallback = _compact_phrase(str(row.get("fallback_query", "")).strip(), limit=4) or seed["fallback_query"]

        subject = str(row.get("subject", seed["subject"])).strip().lower()
        category = str(row.get("category", seed["category"])).strip().lower()
        setting = str(row.get("setting", seed["setting"])).strip().lower()
        mood = str(row.get("mood", seed["mood"])).strip().lower()
        preferred_source = str(row.get("preferred_source", seed["preferred_source"])).strip().lower()
        row_focus_terms = [
            _compact_phrase(str(item), limit=3)
            for item in (row.get("scene_focus_terms") or [])
            if str(item or "").strip()
        ] if isinstance(row.get("scene_focus_terms"), list) else []
        row_support_terms = [
            _compact_phrase(str(item), limit=3)
            for item in (row.get("support_terms") or [])
            if str(item or "").strip()
        ] if isinstance(row.get("support_terms"), list) else []
        row_query_variants = [
            _compact_phrase(str(item), limit=5)
            for item in (row.get("query_variants") or [])
            if str(item or "").strip()
        ] if isinstance(row.get("query_variants"), list) else []

        if subject not in _SUBJECT_HINTS:
            subject = seed["subject"]
        if category not in _CATEGORY_HINTS:
            category = seed["category"]
        if setting not in _SETTING_HINTS:
            setting = seed["setting"]
        if preferred_source not in {"pexels", "pixabay"}:
            preferred_source = seed["preferred_source"]

        cleaned.append(
            {
                "scene_number": expected_scene_number,
                "query": primary,
                "alt": alt,
                "primary_query": primary,
                "alt_query": alt,
                "fallback_query": fallback,
                "subject": subject,
                "category": category,
                "setting": setting,
                "mood": mood or "neutral",
                "preferred_source": preferred_source,
                "scene_focus_terms": _dedupe_phrases(
                    row_focus_terms + _extract_terms(scene_text)[:6] + seed.get("scene_focus_terms", [])[:4]
                )[:6],
                "support_terms": _dedupe_phrases(row_support_terms + seed.get("support_terms", [])[:4])[:5],
                "query_variants": _dedupe_phrases(
                    [primary, alt, fallback] + row_query_variants + seed.get("query_variants", [])
                )[:5],
            }
        )

    return cleaned
