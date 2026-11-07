"""Vidzeo Brain — unified LLM-powered scene planning for all pipelines.

Single entry point for:
- Scene section detection + subject extraction
- Character anchor generation per scene
- Abstract→visual metaphor conversion
- Per-pipeline query generation (YouTube, Web/Flickr, Stock)

Mechanical fallback only when OpenRouter is unavailable.
"""
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait, as_completed
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from typing import List, Dict, Optional, Any

from .openrouter_llm import (
    OpenRouterLLM,
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_SCENE_BRAIN_MODEL,
    DEFAULT_SCENE_BRAIN_FALLBACK_MODELS,
)
from .youtube_query_normalizer import normalize_youtube_query

DEFAULT_BRAIN_MODEL = DEFAULT_SCENE_BRAIN_MODEL


def _scene_brain_fallback_models() -> tuple[str, ...]:
    """Read a bounded, user-overridable fallback chain.

    The default intentionally ends at Qwen Flash. Ling free models are
    domain-specialized (finance and health) and are therefore opt-in via the
    environment instead of being inserted ahead of a general scene planner.
    """
    raw = os.environ.get("VIDZEO_SCENE_BRAIN_FALLBACK_MODELS")
    if raw:
        values = [item.strip() for item in re.split(r"[,;|]+", raw) if item.strip()]
    else:
        values = list(DEFAULT_SCENE_BRAIN_FALLBACK_MODELS)
    result = []
    seen = set()
    for value in values[:4]:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result or DEFAULT_SCENE_BRAIN_FALLBACK_MODELS)


def _loads_brain_json(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("OpenRouter returned empty JSON payload")
    try:
        return json.loads(text)
    except Exception:
        extracted = OpenRouterLLM._extract_json_text(None, text)
        if extracted:
            return json.loads(extracted)
        raise


def _term_list(value: Any, limit: int = 10) -> List[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = re.split(r"[,;|]+", str(value or ""))
    result: List[str] = []
    seen = set()
    for item in raw_items:
        cleaned = re.sub(r"\s+", " ", str(item or "").strip())
        key = cleaned.lower()
        if cleaned and key not in seen:
            result.append(cleaned[:48])
            seen.add(key)
        if len(result) >= limit:
            break
    return result


def _fallback_retrieval_context(subject: str, scene_text: str, topic: str, category: str = "") -> Dict[str, Any]:
    text = f"{subject} {scene_text} {topic}".lower()
    def has_term(term: str) -> bool:
        # Substring checks turn ``apples`` into the technology token ``app``.
        # Retrieval category errors at this stage poison every downstream
        # provider query, so use token boundaries for short/common words.
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(term.strip())}(?![a-z0-9])", text))

    domain_anchor = topic or subject or "general topic"
    domain_category = (category or "").strip().lower()
    if not domain_category:
        if any(has_term(term) for term in ("video game", "gaming", "gameplay", "game trailer", "console game")):
            domain_category = "gaming"
        elif any(has_term(term) for term in ("iphone", "android", "software", "app", "laptop", "ai", "gadget", "smartphone")):
            domain_category = "technology"
        elif any(has_term(term) for term in ("fruit", "nutrition", "vitamin", "diet", "healthy", "recipe", "food")):
            domain_category = "food"
        elif any(has_term(term) for term in ("earthquake", "flood", "wildfire", "hurricane", "cyclone", "tsunami", "landslide", "disaster", "aftermath", "rescue")):
            domain_category = "disaster"
        elif any(has_term(term) for term in ("murder", "crime", "case", "police", "detective", "court", "trial")):
            domain_category = "crime"
        elif any(has_term(term) for term in ("travel", "city", "hotel", "beach", "tour", "flight", "airport")):
            domain_category = "travel"
        elif any(has_term(term) for term in ("football", "soccer", "nba", "cricket", "goal", "match", "tournament")):
            domain_category = "sports"
        else:
            domain_category = "other"
    positive = _term_list([subject, domain_anchor, domain_category])
    negative: List[str] = []
    if domain_category == "technology" and "apple" in text:
        positive.extend(_term_list(["iphone", "smartphone", "ios", "apple event"]))
        negative.extend(["fruit", "recipe", "orchard", "nutrition"])
    elif domain_category == "gaming":
        positive.extend(_term_list(["official trailer", "gameplay trailer", "official gameplay", "no commentary", "publisher channel"]))
        negative.extend(["streamer", "live stream", "reaction", "news", "leak", "fanmade", "mod", "explained", "podcast"])
    elif domain_category in {"food", "health"} and "apple" in text:
        positive.extend(_term_list(["fruit", "fresh produce", "nutrition", "healthy food"]))
        negative.extend(["iphone", "macbook", "ios", "apple event"])
    return {
        "domain_anchor": domain_anchor,
        "domain_category": domain_category,
        "search_disambiguator": subject or domain_anchor,
        "retrieval_positive_terms": _term_list(positive),
        "retrieval_negative_terms": _term_list(negative),
    }

_SCENE_QUERY_STOPWORDS = set(
    "the a an of on in to and or with at by for from was were is are had have has its it their they this that these those over under after before during between into about around across aboard along among behind below beneath beside beyond despite until up upon within without night day morning evening hour hours minute minutes second seconds against would could should must might still then when where which who whom whose what why how not no very more most".split()
)


def _scene_terms_for_query(scene_text, subject, topic):
    excluded = set(re.split(r"[^a-z0-9]+", f"{subject} {topic}".lower())) | {
        "rm", "rms", "titanic", "ship", "footage", "documentary",
        "sank", "sinking", "disaster", "ocean", "vessel", "liner",
    }
    terms = []
    for token in re.findall(r"[a-z][a-z0-9]{3,}", str(scene_text or "").lower()):
        if token in _SCENE_QUERY_STOPWORDS or token in excluded:
            continue
        if token not in terms:
            terms.append(token)
        if len(terms) >= 3:
            break
    return terms


def _scene_youtube_query(subject, scene_text, topic, tier=0):
    terms = _scene_terms_for_query(scene_text, subject, topic)
    t1 = terms[0] if terms else ""
    t2 = terms[1] if len(terms) > 1 else t1
    if tier == 0:
        raw = " ".join(part for part in (subject, t1, t2, "raw footage") if part)
    elif tier == 1:
        raw = " ".join(part for part in (subject, t1, "b-roll footage 4k") if part)
    else:
        raw = " ".join(part for part in (subject, "footage no commentary", t2) if part)
    return normalize_youtube_query(raw, anchor=subject, fallback="documentary footage")
# ── JSON Schemas ──────────────────────────────────────────────────────

_SCENE_SEARCH_TIERS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "primary": {"type": "string"},
        "supporting": {"type": "string"},
        "fallback": {"type": "string"},
    },
    "required": ["primary"],
}

SCENE_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scene_number": {"type": "integer"},
        "container_subject": {"type": "string"},
        "scene_subject": {"type": "string"},
        "visual_intent": {"type": "string"},
        "visual_subject": {"type": "string"},
        "visual_subject_type": {"type": "string"},
        "subject_evidence": {"type": "string"},
        "visible_target": {"type": "string"},
        "identity_scope": {"type": "string", "enum": ["subject", "event", "context"]},
        "youtube": _SCENE_SEARCH_TIERS_SCHEMA,
        "web": _SCENE_SEARCH_TIERS_SCHEMA,
        "stock": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "primary_query": {"type": "string"},
                "alt_query": {"type": "string"},
                "fallback_query": {"type": "string"},
            },
            "required": ["primary_query"],
        },
    },
    "required": [
        "scene_number", "container_subject", "scene_subject", "visual_intent",
        "visual_subject", "visual_subject_type", "subject_evidence", "visible_target", "identity_scope",
        "youtube", "web", "stock",
    ],
}

SCENE_QUERY_SYSTEM_PROMPT = """You direct factual documentary media retrieval.
For each numbered narration scene, return exactly one scene object in the same order.
Use the exact full name of the person, place, event, or product central to its section as container_subject. Keep continuity when a scene uses a pronoun. Set scene_subject to the specific thing that should appear in this scene, and visual_intent to a short, concrete shot description.

For EVERY niche, distinguish film context from the scene's visible subject.
Also return visual_subject (a subject explicitly supported by this scene),
visual_subject_type (person, organization, product, place, event, animal,
automobile, ship, rail_crew, video_game, component, activity, process,
named_entity, or concept),
subject_evidence (an exact short quotation from this scene supporting that
subject), visible_target (the concrete view/action), and identity_scope:
subject for a named subject supported by portrait/object/context imagery;
event only when the shot must show the exact narrated event/time;
context for a supported component/activity without an exact named identity.
Keep Unicode names intact. Never invent an entity, model, year or alias.
An inventor beat may show the inventor or patent, a component beat the
component, and a venue beat the venue, even within a single-topic film.
For components/activities retain the film's physical context in source
queries: a locomotive engine is different from a road-car engine.
Keep pronoun references within their actual section. Incidental cited
publishers, previous owners and comparison names are not the focal subject.
An invention's date does not require a supporting portrait taken that year.

Source attribution is not the scene's subject. When narration says an article, account, reader, study or report describes something, illustrate the underlying object/activity and retain the film's topic; do not search the cited publisher, the act of reading, or prose such as 'useful starting point'. Category/transition beats need recognizable topic footage or photos. A beer-history film uses real beer labels, pub interiors, brewing equipment and pouring shots; named-brand beats keep the correct brand. Stock queries use two to five visible subject/action words, without narration, 'documentary', 'archival photograph' or factual brand claims.

Every scene needs three distinct search tiers for each source:
- YouTube: authentic event footage, archival footage, direct capture, or location b-roll. Search for a visible action or setting with a footage intent, not a narrated history package. For category beats use physical activity (beer brewing, pub beer pouring, brewery bottling) rather than lists of labels or styles. Search different authentic source families across tiers, such as brewing operations footage, pub interiors archive film and beer pouring b-roll. Avoid commentary-led essays, reactions, podcasts, and subtitle-led compilations. Small source captions, scoreboards and broadcaster logos are acceptable; do not lose useful footage for cosmetic reasons.
- Web: credible photos of the exact subject and visible moment. Use photo-friendly terms.
  Conceptual stories need photographed physical actors from the full script, not literal abstract topic prefixes. Keep canonical names for people, events, places and products. For science/technology choose supported hardware, instruments, satellites, clocks, receivers or experiments. Remotion designs explanations: do not search lecture slides, homework, quizzes, generic infographics or foreign-language diagrams unless this scene explicitly studies that source. Each beat should vary its supported subject or shot scale.
- Stock: generic visible subjects and actions suitable for stock libraries. Avoid protected names when a generic substitute is appropriate.

Plan diversity across the complete script, including neighboring chunks. Primary queries identify the exact beat's action/place/era, supporting queries seek another grounded angle or detail, and fallback queries broaden recall while retaining the subject. Do not repeat the same topic-only query or portrait for successive beats. Prefer a distinct event photograph, document, location, actor or physical detail when the narration supports it. Match photo queries to photographs and footage queries to visible motion. Several distant moments from the same authentic long source can work; the same short cut repeated cannot. Never label illustrative stock or a related person's image as evidence of a named subject.

Keep each query concise, specific, and grounded in the supplied narration. Preserve names, dates, and places from the script. Never invent facts, routes, quotes, or visual evidence. Do not borrow another section's subject. All required strings must be nonempty. Return only the supplied JSON schema."""

CONTAINER_MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "is_listicle": {"type": "boolean"},
        "containers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene_number": {"type": "integer"},
                    "container_id": {"type": "string"},
                    "container_subject": {"type": "string"},
                    "subcontainer_subject": {"type": "string"},
                    "scene_subject": {"type": "string"},
                    "scene_subject_type": {"type": "string"},
                    "container_type": {"type": "string"},
                    "domain_anchor": {"type": "string"},
                    "domain_category": {"type": "string"},
                    "search_disambiguator": {"type": "string"},
                    "retrieval_positive_terms": {"type": "array", "items": {"type": "string"}},
                    "retrieval_negative_terms": {"type": "array", "items": {"type": "string"}},
                    "youtube_query_intents": {"type": "array", "items": {"type": "string"}},
                    "container_boundary": {"type": "boolean"},
                    "is_intro": {"type": "boolean"},
                    "is_outro": {"type": "boolean"},
                    "youtube_primary": {"type": "string"},
                    "youtube_supporting": {"type": "string"},
                    "youtube_fallback": {"type": "string"},
                },
                "required": [
                    "scene_number",
                    "container_id",
                    "container_subject",
                    "scene_subject",
                    "container_boundary",
                ],
            },
        },
    },
    "required": ["is_listicle", "containers"],
}

YT_WINNER_SCHEMA = {
    "type": "object",
    "properties": {
        "winner_index": {"type": "integer"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "relevance": {"type": "number"},
                    "footage_signal": {"type": "number"},
                    "visual_match": {"type": "number"},
                    "total": {"type": "number"},
                },
                "required": ["index", "relevance", "footage_signal", "visual_match", "total"],
            },
        },
    },
    "required": ["winner_index", "confidence", "reasoning", "scores"],
}


# ── System Prompts ────────────────────────────────────────────────────

BRAIN_SYSTEM_PROMPT = """You are Vidzeo Brain — the central intelligence for video scene planning across YouTube clips, web images, and stock media.

ROLE:
Given a video topic, a full script context, and per-scene text, you generate the BEST possible plan for each scene including:
1. Section identification (intro, numbered section, outro)
2. Character anchor (gender, age, appearance)
3. Visual metaphor (abstract→concrete conversion for image gen)
4. Search queries for YouTube, web images, and stock media

CORE RULES:
1. Section subjects are HARD CONTAINERS for entity identity, not literal abstract-topic search prefixes. A concrete named person, event, place or product stays exact. For a conceptual topic, search visible physical actors actually mentioned in this film rather than pasting its explanation title into every query.
   - Queries for a named-subject section MUST start with that full subject name.
   - Single-subject intro/outro scenes: use the exact main entity; conceptual stories use a supported visible actor
   - True listicle intro/outro scenes: use the video's domain topic
   - CRITICAL: NEVER include subject words from OTHER sections in any query.
     Each section is a sealed container — content from one section must NEVER leak into another.

2. Use FULL proper names and exact event/product names, never fragments or surnames alone.

3. Queries must be concrete, visual, search-optimized: 4-8 words, NO abstract terms.
   - "legacy" → "career highlights trophy celebration 4k"
   - "dominance" → "winning goal champion clean broadcast"

4. PER-PIPELINE FORMATS & RAW FOOTAGE MANDATE:
   - YouTube: ALWAYS format as: [Exact Subject] + [Concrete Visual Action/Setting] + [Raw Footage Intent: "raw footage", "b-roll 4k", "no commentary", "clean broadcast", "direct capture"].
     * STRICTLY AVOID terms that attract talking heads, podcasts, or captioned videos: "explained", "documentary", "essay", "reaction", "review", "podcast", "debate", "history of", "analysis".
   - Web/Flickr: exact subject + visible action + photo-friendly terms.
     * Science/technology: search real hardware, instruments, experiments, devices or locations supported by the script. Satellite timing corrections is context; a GPS satellite, atomic clock or navigation receiver is a visible subject.
     * Never retrieve lecture slides, homework, quizzes, stock infographics or foreign-language diagram cards as substitute explanations. Remotion creates the explanatory graphics. Source documents are appropriate only when this scene explicitly examines that document.
     * Distinct beats need distinct supported physical subjects or shot scales, not repeated wide overview images. Use full-film context to identify supporting actors.
   - Stock: generic visible subject and action, without protected names in primary.

5. Three query tiers per scene:
   - PRIMARY: Most specific raw/b-roll visual query (4-8 words)
   - SUPPORTING: Broader footage discovery query (4-8 words)
   - FALLBACK: Broadest clean footage query (3-6 words)

6. Character anchor: Determine the most appropriate character for THIS scene based on the scene text. Consider gender, age, appearance, clothing style appropriate for the content.

7. Visual metaphor: If the scene describes abstract concepts (emotions, psychology, success, failure), convert to concrete visual descriptions (body language, posture, environment). If already visual, keep as-is.

8. Stock queries: Use noun phrases only. No abstract concepts. Prefer visible subjects: people, objects, settings. For celebrity names in scene text, use visual substitutes in stock queries.

9. Container intelligence:
   - Detect sections and sub-sections from the full script, not only the current scene.
   - A section may span multiple scenes or one paragraph.
   - If a ranking sentence introduces a named entity, use that full entity, not the ranking phrase.
   - If paragraphs shift between entities, create separate containers even without numbered markers.
   - If a paragraph briefly mentions another person inside the same section, keep section_subject as the current container and set scene_subject to the local mention only when visually important.
   - Always use full proper names.

Return valid JSON only — one scene plan object per scene requested."""

CONTAINER_SYSTEM_PROMPT = """You are Vidzeo Container Brain.

Your job is to read the FULL script and scene list, then create the authoritative scene-to-container map used by YouTube, web/Flickr, and stock retrieval.

PRIMARY OUTPUT GOAL:
- Identify the video's domain/topic, intro/outro domain containers, and every semantic container or sub-container.
- A container may be a listicle item, one paragraph about one entity/object/place/event, or a mini-section inside a longer story.
- Container identity is more important than fancy wording. A bad container poisons the whole video.

Rules:
- Each scene must have exactly one container.
- container_subject must be a concrete searchable thing, not a sentence fragment.
- Use full proper names for people and exact object names for non-people.
- Include a short domain qualifier for ambiguous common names:
  - Apple in a fruit/health script => "Apple fruit", never "Apple".
  - Orange in a fruit/health script => "Orange fruit", never telecom/company.
  - Python in a programming script => "Python programming"; in wildlife => "Python snake".
- Intro scenes: container_id="intro", is_intro=true. For a single-subject video,
  container_subject must be the exact main anchor;
  only a true multi-item/listicle intro should use a broad domain topic.
- Outro scenes follow the same main-anchor rule as the intro.
- A publisher, studio, team, company, or related person mentioned inside a
  single-subject story is a subcontainer, not a new top-level container, unless
  the paragraph genuinely changes its subject to that entity. Keep the primary
  product/game/person as container_subject and put the local entity in
  subcontainer_subject or scene_subject.
- Do not use weak words as subjects: Taking, Few, Even, Today, Players, Goal, Speed, Legacy, Scene, Section, Intro, What if, health boost, live longer.
- If a ranking sentence introduces a named entity, container_subject is that full named entity.
- If a sentence says "Apples are packed with fiber", container_subject = "Apple fruit".
- If a paragraph briefly mentions another item inside the same section, keep container_subject as the current section item and set scene_subject to the local mention only when visually important.

CRITICAL YOUTUBE RAW FOOTAGE & CLEAN B-ROLL MANDATE:
- We reject talking heads, host commentary, video essays, podcasts, and subtitle-led slideshows during candidate metadata and visual verification. Preserve relevant raw footage with small source labels, captions or broadcaster logos.
- YouTube search strings must be short positive noun phrases (usually 3-8 words): full named entity + one concrete action, setting, or shot type. Never copy a narration sentence into a query.
- Do not emit negative search operators or long exclusion lists (for example `-facecam -webcam -streamer`). Those are local retrieval-policy fields and are enforced after download; putting them in the provider query suppresses valid footage.
- Build a niche-aware YouTube intent map for every container. Return 4-8 short `youtube_query_intents` suffixes covering wide niches:
  * GAMING: "raw gameplay no commentary", "direct gameplay capture 4k", "official gameplay trailer", "uncut gameplay 60fps", "cinematic trailer 4k". (NEVER streamer, facecam, review, reaction, leak, mod).
  * PERSON/CELEBRITY: "public appearance footage", "ceremony arrival footage", "archival event footage", "documentary archive footage", "daily life b-roll". (NEVER podcast, essay, interview anchor, drama).
  * SPORTS/ATHLETE: "match action clean broadcast", "skills goals 4k no commentary", "training raw footage", "stadium broadcast 4k", "slow motion highlights 1080p". (NEVER debate, fan talk, analysis).
  * EVENT/DISASTER: "raw footage", "aftermath uncut", "rescue raw footage", "aerial location clean footage", "archival broadcast footage", "newsreel archive uncut".
  * PLACE/TRAVEL: "4k walking tour no talking", "drone footage clean 4k", "street b roll footage", "city location 4k b-roll", "landmark raw footage". (NEVER vlog talking, travel guide host).
  * PRODUCT/TECHNOLOGY: "hands on demo b-roll 4k", "product close up clean", "official launch clean footage", "technology 4k b roll", "unboxing no talking b roll".
  * AUTOMOTIVE/VEHICLES: "car driving raw footage 4k", "exterior b-roll clean", "interior detail 4k", "track testing raw footage", "official promo b-roll".
  * HISTORY: "raw archival film footage", "newsreel moving footage clean", "historic location b roll", "ship at sea archival clean".
  * SCIENCE/MEDICINE: "laboratory experiment 4k b roll", "microscope raw footage", "research facility clean footage", "scientific demonstration clean".
  * CRIME/DOCUMENTARY: "case location raw b-roll", "archive newsreel uncut", "police investigation archival footage", "aerial scene location 4k".
  * FOOD/COOKING: "close up food b roll 4k", "cooking preparation raw footage", "fresh produce market b roll", "harvesting farm clean footage".
  * ANIMALS/WILDLIFE: "4k wildlife b roll", "animal habitat raw footage", "nature close up clean footage", "safari animal behavior 4k".
  * FINANCE/BUSINESS: "trading floor raw b roll", "factory manufacturing 4k", "shipping logistics clean footage", "office corporate b roll 4k".
- Never produce a query intent for subtitles, captions, lyric videos, slideshows, photo montages, reaction packages, podcasts, or broadcaster news packages.
- Keep format exclusions out of the positive query; use observable words such as arrival, ceremony, driving, factory line, onboard, walkaround, close-up, official feed, archive/newsreel.
- Query each visual moment, not just the topic: include the exact make/model/person/event plus an observable action, setting, or shot type. Prefer searches that can return source footage (driving, arrival, ceremony, factory line, onboard, walkaround, close-up, official feed, archive/newsreel) over searches that describe an explanation.
- A small channel watermark/logo is acceptable and must not be used as a rejection signal. Small source subtitles and captions are acceptable. Obstructive creator text cards, reaction layouts and dominant presenter/facecam overlays are not acceptable.
- For gaming scripts, every query must include the exact game title followed by raw gameplay or official trailer terms.

- scene_subject_type should be one of: person, object, place, event, product, topic, animal, food.
- Also output retrieval context for each row:
  - domain_anchor: the video/container world, e.g. "consumer technology", "fruit nutrition", "true crime documentary", "Paris travel".
  - domain_category: sports, gaming, technology, food, travel, disaster, crime, documentary, finance, history, entertainment, health, education, science, politics, lifestyle, automotive, other.
  - container_type: person, product, food, place, event, brand, topic, organization, animal, object.
  - search_disambiguator: the short phrase that prevents wrong meanings, e.g. "iPhone smartphone", "fruit nutrition", "crime case", "travel vlog".
  - retrieval_positive_terms: 4-10 words/phrases expected in good candidates for this container.
  - retrieval_negative_terms: 4-10 words/phrases that indicate wrong-domain candidates. Example for Apple fruit: iphone, macbook, ios, apple event. Example for Apple iPhone: fruit, recipe, orchard, nutrition.

Return valid JSON only."""

YT_WINNER_SYSTEM_PROMPT = """You are Vidzeo YouTube Candidate Scorer — an expert at selecting the best YouTube video for a given scene.

Given a scene description and a list of YouTube candidate videos, score each candidate on:
1. RELEVANCE (0-10): How well the title/topic matches the scene subject
2. FOOTAGE SIGNAL (0-10): Is this actual event footage or commentary/analysis?
3. VISUAL MATCH (0-10): Would the visuals match what the scene describes?

Return the index of the best candidate, your confidence (0.0-1.0), and per-candidate scores.

Rules:
- Prefer actual moving footage, clean b-roll, official feeds, direct captures, and raw event footage over analysis/commentary
- Prefer videos that match the specific scene subject
- Prefer shorter, focused content over broad documentaries
- For news events: prefer raw footage, press conferences, official broadcasts
- For people: prefer public-appearance footage, arrivals, ceremonies, and action footage; reject presenter-led interviews, reaction layouts, and facecams unless the scene explicitly requires a speech
- Watermarks or channel logos are acceptable cosmetic defects; reject obstructive text cards and creator overlays, not small source captions or labels
- Penalize clickbait titles, opinion pieces, and compilation channels"""


# ── Mechanical Fallback ───────────────────────────────────────────────

_GENERIC_ANCHOR = {
    "gender": "neutral",
    "age_range": "25-35",
    "ethnicity": "diverse/unspecified",
    "hair": "natural hair",
    "clothing": "casual modern clothes",
    "body_type": "average build",
}


def _mechanical_scene_plan(scene_text: str, scene_number: int, section_subject: str = "",
                            topic: str = "") -> Dict:
    """Ultra-minimal mechanical fallback when Brain completely fails."""
    subject = section_subject or topic or "topic"
    words = re.findall(r'[a-zA-Z]{4,}', scene_text or "")
    visual_words = [w for w in words if w.lower() not in {
        "the", "this", "that", "with", "from", "they", "have", "been", "were",
        "what", "when", "where", "which", "their", "them", "about", "into",
        "could", "would", "should", "there", "these", "those", "because",
        "just", "also", "very", "more", "than", "then", "some", "made",
        "make", "like", "than", "even", "still", "after", "before",
    }][:3]
    base = " ".join(visual_words) if visual_words else subject

    return {
        "scene_number": scene_number,
        "section_label": f"section_{scene_number}" if section_subject else "intro",
        "section_subject": section_subject or topic or "",
        "anchor": dict(_GENERIC_ANCHOR),
        "visual_metaphor": base or "person in natural setting",
        "youtube": {
            "primary": f"{subject} {base}" if base != subject else f"{subject} footage",
            "supporting": f"{subject} highlights" if subject else "highlights",
            "fallback": subject or "footage",
        },
        "web": {
            "primary": f"{subject} {base} photo" if base != subject else f"{subject} photo",
            "supporting": f"{subject} photography",
            "fallback": subject or "photo",
        },
        "stock": {
            "primary_query": f"{subject} {base}" if base != subject and subject else (base or subject or "lifestyle"),
            "alt_query": f"{subject} close up" if subject else "nature",
            "fallback_query": subject or "video",
            "subject": subject or "lifestyle",
            "category": "lifestyle",
            "setting": "natural",
            "preferred_source": "pexels",
        },
    }



def _mechanical_container_map(topic: str, scene_texts: List[str]) -> Dict[str, Any]:
    """Build a complete, deterministic container map without a network call.

    The LLM map improves query wording, but media retrieval must never wait on
    it indefinitely.  The semantic section detector already preserves explicit
    listicle/entity boundaries, so it is a useful production fallback instead
    of returning ``None`` and launching another round of LLM planners.
    """
    scene_texts = list(scene_texts or [])
    if not scene_texts:
        return {"is_listicle": False, "containers": []}
    try:
        from .semantic_query_agent import detect_list_sections
        sections = list(detect_list_sections(scene_texts, topic or "") or [])
    except Exception:
        sections = []
    try:
        from .container_guard import canonicalize_sections
        if sections:
            sections = list(canonicalize_sections(sections, scene_texts=scene_texts, topic=topic or "") or sections)
    except Exception:
        pass

    # When the semantic detector sees no explicit numbered boundaries, a
    # strong title/topic is the safer hard container. This prevents sentence
    # starters and facets ("Before", "WWE", "Hollywood") from becoming fake
    # subjects after an OpenRouter timeout.
    explicit_listicle = any(
        bool(section.get("has_explicit_marker"))
        for section in sections
        if isinstance(section, dict)
    )
    try:
        from .container_guard import is_weak_container_subject
        topic_anchor = str(topic or "").strip()
        topic_is_strong = bool(topic_anchor) and not is_weak_container_subject(topic_anchor)
    except Exception:
        topic_anchor = str(topic or "").strip()
        topic_is_strong = bool(topic_anchor)
    single_topic_fallback = bool(topic_is_strong and not explicit_listicle)

    rows: List[Dict[str, Any]] = []
    previous_id = ""
    for idx, scene_text in enumerate(scene_texts):
        section = sections[idx] if idx < len(sections) and isinstance(sections[idx], dict) else {}
        is_intro = bool(section.get("is_intro"))
        is_outro = str(section.get("container_id") or "").strip().lower() == "outro"
        subject = re.sub(
            r"\s+",
            " ",
            str(section.get("container_subject") or section.get("section_subject") or "").strip(),
        )
        if (is_intro or is_outro) and not subject:
            subject = str(topic or "").strip()
        if single_topic_fallback:
            subject = topic_anchor
        query_subject = subject or topic_anchor or "footage"
        container_id = re.sub(
            r"[^a-z0-9]+",
            "_",
            str(section.get("container_id") or query_subject).lower(),
        ).strip("_")[:40] or f"section_{idx + 1}"
        context = _fallback_retrieval_context(query_subject, scene_text, topic or "")
        boundary = bool(
            idx == 0
            or section.get("container_boundary")
            or section.get("has_explicit_marker")
            or container_id != previous_id
        )
        rows.append({
            "scene_number": idx + 1,
            "container_id": container_id,
            "container_subject": query_subject,
            "subcontainer_subject": "",
            "scene_subject": query_subject,
            "scene_subject_type": "topic" if is_intro or is_outro else "entity",
            "container_type": "topic" if is_intro or is_outro else "entity",
            "domain_anchor": context.get("domain_anchor") or topic or query_subject,
            "domain_category": context.get("domain_category") or "other",
            "search_disambiguator": context.get("search_disambiguator") or query_subject,
            "retrieval_positive_terms": context.get("retrieval_positive_terms") or [query_subject],
            "retrieval_negative_terms": context.get("retrieval_negative_terms") or [],
            "container_boundary": boundary,
            "has_explicit_marker": bool(section.get("has_explicit_marker")),
            "is_intro": is_intro,
            "is_outro": is_outro,
            "youtube_primary": _scene_youtube_query(query_subject, scene_text, topic, 0),
            "youtube_supporting": _scene_youtube_query(query_subject, scene_text, topic, 1),
            "youtube_fallback": _scene_youtube_query(query_subject, scene_text, topic, 2),
            "_mechanical_container": True,
        })
        previous_id = container_id

    distinct = {
        str(row.get("container_subject") or "").strip().lower()
        for row in rows
        if row.get("container_subject") and not row.get("is_intro") and not row.get("is_outro")
    }
    explicit_subjects = {
        str(row.get("container_subject") or "").strip().lower()
        for row in rows
        if row.get("has_explicit_marker") and row.get("container_subject")
    }
    return {
        "is_listicle": (not single_topic_fallback) and (
            len(explicit_subjects) >= 2 or (
                len(distinct) >= 2 and any(row.get("has_explicit_marker") for row in rows)
            )
        ),
        "containers": rows,
    }


# ── Brain Orchestrator ────────────────────────────────────────────────

class VidzeoBrain:
    """Central brain orchestrator for scene planning.

    Args:
        api_key: OpenRouter API key
        model: OpenRouter model name
    """

    def __init__(self, api_key: str = "", model: str = None, fallback_models=None, client=None):
        if client is not None:
            self._client = client
            self._model = getattr(client, "model", model or DEFAULT_BRAIN_MODEL)
            self._fallback_models = tuple(getattr(client, "fallback_models", ()) or ())
            self._last_scene_model_routes = []
            return
        selected_model = str(model or os.environ.get("VIDZEO_SCENE_BRAIN_MODEL") or DEFAULT_BRAIN_MODEL).strip()
        selected_fallbacks = fallback_models if fallback_models is not None else _scene_brain_fallback_models()
        self._client = OpenRouterLLM(api_key, selected_model, fallback_models=selected_fallbacks)
        self._model = self._client.model
        self._fallback_models = tuple(self._client.fallback_models)
        self._last_scene_model_routes = []
        print(
            f"   [brain] {self._client.provider} scene model chain: "
            f"{' -> '.join(self._client.model_chain)}"
        )

    def _route_snapshot(self, status="directed", reason=""):
        attempts = [dict(row) for row in (self._client.attempt_history or []) if isinstance(row, dict)]
        attempted_models = list(dict.fromkeys(
            str(row.get("model") or "").strip()
            for row in attempts
            if str(row.get("model") or "").strip()
        ))
        attempted_models = list(dict.fromkeys(
            [
                *attempted_models,
                *(
                    str(row.get("resolved_model") or "").strip()
                    for row in attempts
                    if str(row.get("resolved_model") or "").strip()
                ),
            ]
        ))
        return {
            "provider": getattr(self._client, "provider", "openrouter"),
            "status": str(status or "unknown"),
            "requested_model": self._model,
            "selected_model": str(self._client.last_model or self._model),
            "configured_chain": list(self._client.model_chain),
            "attempted_models": attempted_models,
            "attempts": attempts,
            "reason": str(reason or ""),
        }

    def plan_scenes(self, topic: str, script: str, scene_texts: List[str],
                    section_subjects: List[str] = None, is_listicle: bool = False,
                    pipeline_type: str = "mixed") -> List[Dict]:
        """Generate full scene plans for all scenes."""
        self._last_scene_model_routes = []
        rows = self._plan_scenes_impl(
            topic, script, scene_texts, section_subjects, is_listicle, pipeline_type, extra_hint="",
        )
        if not self._last_scene_model_routes:
            self._last_scene_model_routes = [self._route_snapshot(
                status="directed" if rows else "failed",
                reason="" if rows else "scene_plan_incomplete_or_all_routes_failed",
            )]
        return rows

    def plan_containers(self, topic: str, script: str, scene_texts: List[str],
                        deadline_monotonic: float = None) -> Optional[Dict[str, Any]]:
        """Generate the authoritative container map within one hard stage wall.

        Every missing/late LLM chunk is filled mechanically.  This intentionally
        returns a complete map so callers do not start a second, slower family of
        per-source LLM planners after a transient provider delay.
        """
        if not scene_texts:
            return None
        stage_started = time.monotonic()
        if deadline_monotonic is None:
            try:
                # Give the scene brain enough room for one short primary
                # request plus a bounded provider/model retry.  The media
                # pipeline already has a deterministic map ready immediately,
                # so this is a ceiling, never a reason to block the job.
                stage_seconds = float(os.environ.get("VIDZEO_BRAIN_CONTAINER_STAGE_SECONDS", "1500") or 1500)
            except Exception:
                stage_seconds = 1500.0
            deadline_monotonic = stage_started + max(300.0, min(1800.0, stage_seconds))
        mechanical_map = _mechanical_container_map(topic, scene_texts)

        def mechanical_fallback(reason):
            return {
                **mechanical_map,
                "_model_route": self._route_snapshot(status="mechanical_fallback", reason=reason),
            }
        chunk_size = max(8, min(24, int(os.environ.get("VIDZEO_BRAIN_CONTAINER_CHUNK_SIZE", "18") or 18)))
        if len(scene_texts) > chunk_size:
            chunks = [
                (start, scene_texts[start:start + chunk_size])
                for start in range(0, len(scene_texts), chunk_size)
            ]
            chunk_results: Dict[int, Dict[str, Any]] = {}
            try:
                worker_limit = int(os.environ.get("VIDZEO_BRAIN_CONTAINER_WORKERS", "4") or 4)
            except Exception:
                worker_limit = 6
            workers = min(max(1, worker_limit), len(chunks))
            stage_remaining = max(0.0, deadline_monotonic - time.monotonic())
            print(
                f"   [brain] container map: {len(scene_texts)} scenes in {len(chunks)} parallel chunk(s) "
                f"(workers={workers}, stage_remaining={stage_remaining:.1f}s)"
            )
            executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="brain-container")
            def _plan_container_chunk(start, chunk):
                # Each parallel request owns its OpenRouter client. Sharing one
                # client's attempt history across workers made the reported
                # model route nondeterministic and could interleave diagnostics.
                is_mocked = getattr(getattr(self._client, "chat", None), "mock_calls", None) is not None
                worker = VidzeoBrain(
                    api_key=getattr(self._client, "api_key", ""),
                    model=self._model,
                    fallback_models=self._fallback_models,
                    client=self._client if is_mocked else None,
                )
                result = worker.plan_containers(topic, script, chunk, deadline_monotonic)
                if isinstance(result, dict):
                    result = dict(result)
                    result.setdefault("_model_route", worker._route_snapshot())
                return result

            future_map = {
                executor.submit(_plan_container_chunk, start, chunk): (start, chunk)
                for start, chunk in chunks
            }
            try:
                done, pending = wait(
                    future_map,
                    timeout=max(0.0, deadline_monotonic - time.monotonic()),
                )
                for future in done:
                    start, chunk = future_map[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        print(f"   Brain container chunk at scene {start + 1} failed: {exc}")
                        result = None
                    result_rows = list((result or {}).get("containers") or [])
                    # A failed child request returns a complete mechanical map
                    # by design. Do not count that fallback as an LLM chunk.
                    if len(result_rows) == len(chunk) and any(
                        bool(row.get("_llm_container")) for row in result_rows if isinstance(row, dict)
                    ):
                        chunk_results[start] = result
                for future in pending:
                    future.cancel()
            finally:
                # Never let a slow provider thread hold the media launch path.
                executor.shutdown(wait=False, cancel_futures=True)
            merged: List[Dict[str, Any]] = []
            listicle = bool(mechanical_map.get("is_listicle"))
            mechanical_rows = list(mechanical_map.get("containers") or [])
            fallback_rows = 0
            route_rows = []
            for start, chunk in chunks:
                result = chunk_results.get(start)
                if result:
                    rows = list(result.get("containers") or [])
                    listicle = listicle or bool(result.get("is_listicle"))
                    route = result.get("_model_route")
                    if isinstance(route, dict):
                        route_rows.append(route)
                else:
                    rows = mechanical_rows[start:start + len(chunk)]
                for offset, row in enumerate(rows):
                    clean = dict(row or {})
                    if not bool(clean.get("_llm_container")):
                        fallback_rows += 1
                    clean["scene_number"] = start + offset + 1
                    merged.append(clean)
            distinct = {
                str(row.get("container_subject") or "").strip().lower()
                for row in merged
                if row.get("container_subject") and not row.get("is_intro") and not row.get("is_outro")
            }
            elapsed = time.monotonic() - stage_started
            print(
                f"   [brain] container map stage complete in {elapsed:.1f}s: "
                f"llm_chunks={len(chunk_results)}/{len(chunks)}, mechanical_rows={fallback_rows}"
            )
            attempted_models = list(dict.fromkeys(
                model
                for route in route_rows
                for model in (route.get("attempted_models") or [route.get("selected_model")])
                if model
            ))
            route_status = "partial_fallback" if fallback_rows else "directed"
            return {
                "is_listicle": listicle or len(distinct) >= 2,
                "containers": merged,
                "_model_route": {
                    "provider": getattr(self._client, "provider", "openrouter"),
                    "status": route_status,
                    "requested_model": self._model,
                    "selected_model": attempted_models[-1] if attempted_models else self._model,
                    "configured_chain": list(self._client.model_chain),
                    "attempted_models": attempted_models,
                    "attempts": [attempt for route in route_rows for attempt in route.get("attempts", [])],
                    "directed_scenes": sum(1 for row in merged if row.get("_llm_container")),
                    "fallback_scenes": fallback_rows,
                    "reason": "one_or_more_container_chunks_used_mechanical_fallback" if fallback_rows else "",
                },
            }
        remaining = max(0.0, deadline_monotonic - time.monotonic())
        if remaining <= 0.0:
            print(f"   [brain] container request skipped: stage deadline exhausted ({len(scene_texts)} mechanical row(s))")
            return mechanical_fallback("stage_deadline_exhausted")
        scene_blocks = [
            f"Scene {idx + 1}: {str(text or '')[:450]}"
            for idx, text in enumerate(scene_texts)
        ]
        user = f"""Video topic: {topic}

Full script:
{str(script or '')[:6000]}

Scenes:
{chr(10).join(scene_blocks)}

Return one container row for every scene in order."""
        try:
            try:
                request_budget = float(os.environ.get("VIDZEO_BRAIN_CONTAINER_REQUEST_BUDGET_SECONDS", "1500") or 1500)
            except Exception:
                request_budget = 1500.0
            request_budget = max(180.0, min(1800.0, request_budget))
            request_budget = min(request_budget, remaining)
            try:
                # Keep each HTTP attempt shorter than the request budget so a
                # timed-out calls still leave time for one bounded retry.
                socket_timeout = float(os.environ.get("VIDZEO_BRAIN_CONTAINER_TIMEOUT", "360") or 360)
            except Exception:
                socket_timeout = 360.0
            socket_timeout = max(90.0, min(900.0, socket_timeout))
            socket_timeout = min(socket_timeout, request_budget)
            try:
                max_attempts = int(os.environ.get("VIDZEO_BRAIN_CONTAINER_MAX_ATTEMPTS", "3") or 3)
            except Exception:
                max_attempts = 1
            raw = self._client.chat(
                user_prompt=user,
                system_prompt=CONTAINER_SYSTEM_PROMPT,
                temperature=0.1,
                max_tokens=max(2400, min(10000, 800 + (len(scene_texts) * 260))),
                json_mode=True,
                json_schema=CONTAINER_MAP_SCHEMA,
                timeout_seconds=socket_timeout,
                total_timeout_seconds=request_budget,
                max_attempts=max(1, min(3, max_attempts)),
            )
            payload = _loads_brain_json(raw)
            rows = payload.get("containers") or []
            if not isinstance(rows, list):
                print(f"   Brain container map incomplete: {len(rows) if isinstance(rows, list) else 0}/{len(scene_texts)}")
                return mechanical_fallback("invalid_container_rows")
            if len(rows) < len(scene_texts):
                print(
                    f"   Brain container map incomplete: {len(rows)}/{len(scene_texts)}; "
                    "salvaging partial LLM container map"
                )
            if not rows:
                return mechanical_fallback("empty_container_rows")
            try:
                from .container_guard import canonical_container_subject
            except Exception:
                canonical_container_subject = None
            known_subjects = []
            for row in rows:
                if isinstance(row, dict):
                    known_subjects.extend([
                        str(row.get("container_subject") or "").strip(),
                        str(row.get("scene_subject") or "").strip(),
                    ])
            known_subjects = [item for item in dict.fromkeys(known_subjects) if item]
            cleaned_rows: List[Dict[str, Any]] = []
            previous_id = ""
            previous_subject = ""
            mechanical_rows = list(mechanical_map.get("containers") or [])
            for idx in range(len(scene_texts)):
                raw_row = rows[idx] if idx < len(rows) and isinstance(rows[idx], dict) else None
                row_is_llm = isinstance(raw_row, dict) and bool(raw_row)
                row = dict(raw_row) if row_is_llm else (
                    dict(mechanical_rows[idx]) if idx < len(mechanical_rows) else {}
                )
                scene_text = str(scene_texts[idx] or "")
                raw_subject = re.sub(r"\s+", " ", str(row.get("container_subject") or "").strip())
                raw_scene_subject = re.sub(r"\s+", " ", str(row.get("scene_subject") or raw_subject).strip())
                if canonical_container_subject:
                    subject = canonical_container_subject(raw_subject, scene_text, topic, known_subjects)
                    if not subject and raw_scene_subject:
                        subject = canonical_container_subject(raw_scene_subject, scene_text, topic, known_subjects)
                    if not subject:
                        subject = canonical_container_subject("scene", scene_text, topic, known_subjects)
                else:
                    subject = raw_subject
                container_id = re.sub(
                    r"[^a-zA-Z0-9_-]+",
                    "_",
                    str(row.get("container_id") or "").strip(),
                ).strip("_").lower()
                is_intro = bool(row.get("is_intro")) or container_id == "intro"
                is_outro = bool(row.get("is_outro")) or container_id == "outro"
                if not subject and previous_subject and not bool(row.get("container_boundary")) and not is_intro and not is_outro:
                    subject = previous_subject
                if canonical_container_subject:
                    scene_subject = canonical_container_subject(raw_scene_subject or subject or "scene", scene_text, topic, known_subjects) or subject
                else:
                    scene_subject = raw_scene_subject or subject
                if not container_id:
                    if is_intro:
                        container_id = "intro"
                    elif is_outro:
                        container_id = "outro"
                    else:
                        slug = re.sub(r"[^a-z0-9]+", "_", subject.lower()).strip("_")[:36]
                        container_id = slug or f"section_{idx + 1}"
                boundary = bool(row.get("container_boundary")) or idx == 0 or container_id != previous_id
                if subject and previous_subject and subject.lower() != previous_subject.lower():
                    boundary = True
                fallback_context = _fallback_retrieval_context(
                    subject or raw_subject or raw_scene_subject,
                    scene_text,
                    topic,
                    str(row.get("domain_category") or ""),
                )
                domain_anchor = re.sub(
                    r"\s+", " ",
                    str(row.get("domain_anchor") or fallback_context.get("domain_anchor") or topic or "").strip(),
                )
                domain_category = re.sub(
                    r"\s+", " ",
                    str(row.get("domain_category") or fallback_context.get("domain_category") or "other").strip().lower(),
                )
                search_disambiguator = re.sub(
                    r"\s+", " ",
                    str(row.get("search_disambiguator") or fallback_context.get("search_disambiguator") or subject or domain_anchor).strip(),
                )
                positive_terms = _term_list(row.get("retrieval_positive_terms") or fallback_context.get("retrieval_positive_terms"))
                negative_terms = _term_list(row.get("retrieval_negative_terms") or fallback_context.get("retrieval_negative_terms"))
                youtube_query_intents = _term_list(row.get("youtube_query_intents"), limit=8)
                cleaned = {
                    "scene_number": idx + 1,
                    "container_id": container_id,
                    "container_subject": subject if not is_intro and not is_outro else (subject or topic or ""),
                    "subcontainer_subject": re.sub(r"\s+", " ", str(row.get("subcontainer_subject") or "").strip()),
                    "scene_subject": scene_subject or subject or topic or "",
                    "scene_subject_type": str(row.get("scene_subject_type") or ("topic" if is_intro or is_outro else "person")).strip().lower(),
                    "container_type": str(row.get("container_type") or row.get("scene_subject_type") or ("topic" if is_intro or is_outro else "entity")).strip().lower(),
                    "domain_anchor": domain_anchor,
                    "domain_category": domain_category,
                    "search_disambiguator": search_disambiguator,
                    "retrieval_positive_terms": positive_terms,
                    "retrieval_negative_terms": negative_terms,
                    "youtube_query_intents": youtube_query_intents,
                    "container_boundary": boundary,
                    "has_explicit_marker": boundary,
                    "is_intro": is_intro,
                    "is_outro": is_outro,
                    "youtube_primary": str(row.get("youtube_primary") or (f"{subject} highlights" if subject else "")).strip(),
                    "youtube_supporting": str(row.get("youtube_supporting") or (f"{subject} public appearance b roll" if subject else "")).strip(),
                    "youtube_fallback": str(row.get("youtube_fallback") or (f"{subject} raw footage clean" if subject else "")).strip(),
                    "_llm_container": row_is_llm,
                    "_mechanical_container": not row_is_llm,
                }
                cleaned_rows.append(cleaned)
                previous_id = container_id
                if subject and not is_intro and not is_outro:
                    previous_subject = subject
            distinct = {
                row.get("container_subject", "").strip().lower()
                for row in cleaned_rows
                if row.get("container_subject") and not row.get("is_intro") and not row.get("is_outro")
            }
            directed_count = sum(1 for row in cleaned_rows if row.get("_llm_container"))
            fallback_count = len(cleaned_rows) - directed_count
            return {
                "is_listicle": bool(payload.get("is_listicle")) or len(distinct) >= 2,
                "containers": cleaned_rows,
                "_model_route": self._route_snapshot(
                    status="partial_fallback" if fallback_count else "directed",
                    reason="partial_container_response" if fallback_count else "",
                ) | {
                    "directed_scenes": directed_count,
                    "fallback_scenes": fallback_count,
                },
            }
        except Exception as e:
            print(f"   Brain container mapping failed: {e}; using {len(scene_texts)} mechanical row(s)")
            return mechanical_fallback(f"{type(e).__name__}")

    def _plan_scenes_impl(self, topic: str, script: str, scene_texts: List[str],
                          section_subjects: List[str] = None, is_listicle: bool = False,
                          pipeline_type: str = "mixed",
                          extra_hint: str = "", deadline_monotonic: float = None) -> List[Dict]:
        """Internal scene planning implementation with optional extra forbidden context."""
        if not scene_texts:
            return []
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            return None

        section_subjects = section_subjects or []
        is_listicle = is_listicle and len([s for s in section_subjects if s]) >= 2

        # Build global forbidden context — subjects from OTHER sections that must never leak
        _all_section_words: set = set()
        for s in section_subjects:
            if s:
                _all_section_words.update(s.lower().split())
        _global_forbidden_hint = ""
        if _all_section_words:
            _global_forbidden_hint = f"\nGLOBAL FORBIDDEN SUBJECTS across all sections: {', '.join(sorted(_all_section_words))}. NEVER include any of these words in queries for scenes from OTHER sections."

        try:
            chunk_size = int(os.environ.get("VIDZEO_BRAIN_SCENE_CHUNK_SIZE", "8") or 8)
        except Exception:
            chunk_size = 8
        chunk_size = max(3, min(12, chunk_size))
        if len(scene_texts) > chunk_size:
            chunks = [
                (start, scene_texts[start:start + chunk_size], section_subjects[start:start + chunk_size])
                for start in range(0, len(scene_texts), chunk_size)
            ]
            try:
                worker_limit = max(1, min(8, int(os.environ.get("VIDZEO_BRAIN_SCENE_WORKERS", "4") or 4)))
            except (TypeError, ValueError):
                worker_limit = 4
            from .media_query_strategy import query_planning_budget
            if deadline_monotonic is None:
                deadline_monotonic = time.monotonic() + query_planning_budget(len(chunks), worker_limit)

            def _plan_scene_chunk(chunk):
                start, chunk_texts, chunk_subjects = chunk
                worker = VidzeoBrain(
                    api_key=self._client.api_key,
                    model=self._model,
                    fallback_models=self._fallback_models,
                )
                rows = worker._plan_scenes_impl(
                    topic=topic,
                    script=script,
                    scene_texts=chunk_texts,
                    section_subjects=chunk_subjects,
                    is_listicle=is_listicle,
                    pipeline_type=pipeline_type,
                    extra_hint=_global_forbidden_hint,
                    deadline_monotonic=deadline_monotonic,
                )
                return start, chunk_texts, chunk_subjects, rows, worker._route_snapshot()

            merged: List[Dict] = []
            results: Dict[int, tuple] = {}
            executor = ThreadPoolExecutor(
                max_workers=min(worker_limit, len(chunks)),
                thread_name_prefix="brain-scene-plan",
            )
            try:
                futures = [executor.submit(_plan_scene_chunk, chunk) for chunk in chunks]
                # Retain successful chunks when another worker stalls. The
                # outer planner budget scales with the number of parallel waves.
                completed, pending = wait(futures, timeout=max(0.01, deadline_monotonic - time.monotonic()))
                for future in completed:
                    try:
                        start, chunk_texts, chunk_subjects, rows, route = future.result()
                    except Exception as exc:
                        print(f"   Brain scene-plan worker failed: {type(exc).__name__}: {str(exc)[:180]}")
                        continue
                    results[start] = (chunk_texts, chunk_subjects, rows, route)
                    if not rows or len(rows) < len(chunk_texts):
                        print(
                            f"   Brain chunk returned {len(rows or [])}/{len(chunk_texts)} "
                            f"scenes for rows {start + 1}-{start + len(chunk_texts)}"
                        )
                for future in pending:
                    future.cancel()
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            self._last_scene_model_routes = [
                dict(results[start][3])
                for start, _chunk_texts, _chunk_subjects in chunks
                if start in results and isinstance(results[start][3], dict)
            ]
            for start, _chunk_texts, _chunk_subjects in chunks:
                chunk_texts, chunk_subjects, rows, _route = results.get(
                    start, (_chunk_texts, _chunk_subjects, [], {})
                )
                rows = rows if isinstance(rows, list) else []
                for offset, text in enumerate(chunk_texts):
                    plan = rows[offset] if offset < len(rows) else None
                    if not isinstance(plan, dict) or not plan:
                        plan = _mechanical_scene_plan(
                            text, start + offset + 1,
                            chunk_subjects[offset] if offset < len(chunk_subjects) else "", topic,
                        )
                        plan['_query_fallback'] = 'incomplete_or_timed_out_chunk'
                    if isinstance(plan, dict):
                        plan = dict(plan)
                        plan["scene_number"] = start + offset + 1
                        if chunk_subjects and offset < len(chunk_subjects) and chunk_subjects[offset]:
                            plan.setdefault("section_subject", chunk_subjects[offset])
                    merged.append(plan)
            return merged[:len(scene_texts)]

        # Build per-scene prompt blocks with forbidden-subject context
        from services.container_guard import build_forbidden_map
        _forbidden_map = build_forbidden_map(
            [{"container_subject": s, "is_intro": not s} for s in section_subjects],
            len(scene_texts),
        )
        scene_blocks = []
        for i, text in enumerate(scene_texts):
            subj = section_subjects[i] if i < len(section_subjects) else ""
            label = f" | SECTION: {subj}" if subj else " | INTRO/OUTRO"
            forbid = _forbidden_map.get(i, set())
            forbid_hint = f" | FORBIDDEN subjects: {', '.join(sorted(forbid))}" if forbid else ""
            scene_blocks.append(f"Scene {i+1}: {text[:300]}{label}{forbid_hint}")

        # Build the system prompt
        listicle_hint = ""
        if is_listicle:
            distinct = list(dict.fromkeys(s for s in section_subjects if s))
            listicle_hint = f"\nLISTICLE with sections: {', '.join(distinct)}. Section subjects are HARD CONTAINERS."

        system = f"""{SCENE_QUERY_SYSTEM_PROMPT}

Video topic: {topic}
Pipeline: {pipeline_type}
{listicle_hint}
Script context (first 1200 chars):
{(script or '')[:1200]}"""

        user = f"""Plan the following {len(scene_texts)} scenes. Generate ONE scene plan per scene.

Scenes:
{chr(10).join(scene_blocks)}

Return a JSON object with key "scenes" containing an array of scene plan objects.
Each scene plan must match the schema exactly."""

        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "scenes": {
                    "type": "array",
                    "items": SCENE_PLAN_SCHEMA,
                }
            },
            "required": ["scenes"],
        }

        try:
            try:
                scene_budget = float(os.environ.get("VIDZEO_BRAIN_SCENE_BUDGET_SECONDS", "1500") or 1500)
            except Exception:
                scene_budget = 1500.0
            scene_budget = max(300.0, min(1800.0, scene_budget))
            try:
                scene_timeout = float(os.environ.get("VIDZEO_BRAIN_SCENE_TIMEOUT", "360") or 360)
            except Exception:
                scene_timeout = 360.0
            scene_timeout = max(120.0, min(900.0, scene_timeout, scene_budget))
            from .media_query_strategy import query_planning_budget
            remaining = (deadline_monotonic - time.monotonic()) if deadline_monotonic is not None else query_planning_budget(1, 1)
            if remaining <= 0:
                return None
            scene_budget = min(scene_budget, remaining)
            scene_timeout = min(scene_timeout, scene_budget)
            try:
                scene_attempts = int(os.environ.get("VIDZEO_BRAIN_SCENE_MAX_ATTEMPTS", "3") or 3)
            except Exception:
                scene_attempts = 1
            raw = self._client.chat(
                user_prompt=user,
                system_prompt=system,
                temperature=0.2,
                max_tokens=max(2400, min(7000, 900 + (len(scene_texts) * 550))),
                json_mode=True,
                json_schema=schema,
                json_schema_name="vidzeo_scene_queries_v3",
                timeout_seconds=scene_timeout,
                total_timeout_seconds=scene_budget,
                max_attempts=max(1, min(3, scene_attempts)),
            )
            payload = _loads_brain_json(raw)
            scenes = payload.get("scenes", [])
            if isinstance(scenes, list) and len(scenes) == len(scene_texts):
                query_fields = {
                    "youtube": ("primary", "supporting", "fallback"),
                    "web": ("primary", "supporting", "fallback"),
                    "stock": ("primary_query", "alt_query", "fallback_query"),
                }
                for index, scene in enumerate(scenes, start=1):
                    if not isinstance(scene, dict) or scene.get("scene_number") != index:
                        raise ValueError(f"OpenRouter returned an invalid scene number at row {index}")
                    if any(not str(scene.get(field) or "").strip() for field in (
                        "container_subject", "scene_subject", "visual_intent"
                    )):
                        raise ValueError(f"OpenRouter returned an empty subject or visual intent at row {index}")
                    for source, fields in query_fields.items():
                        queries = scene.get(source)
                        if not isinstance(queries, dict):
                            queries = {}
                            scene[source] = queries
                        first_field = fields[0]
                        first_val = str(queries.get(first_field) or "").strip()
                        if not first_val:
                            first_val = next((str(queries.get(field) or '').strip() for field in fields
                                              if str(queries.get(field) or '').strip()), '')
                            if not first_val:
                                raise ValueError(f'OpenRouter returned no usable {source} queries at row {index}')
                            queries[first_field] = first_val
                        for field in fields[1:]:
                            if not str(queries.get(field) or "").strip():
                                queries[field] = first_val
                return scenes
            print(f"   Brain returned {len(scenes) if isinstance(scenes, list) else 0} scenes, expected {len(scene_texts)}")
        except Exception as e:
            print(f"   Brain scene planning failed: {e}")

        # Signal failure — caller should try next LLM provider before mechanical
        return None

    def select_yt_winner(self, scene_text: str, title: str,
                         candidates: List[Dict]) -> Dict:
        """Score YouTube candidates and select the best winner.

        Args:
            scene_text: Scene narration text
            title: Video title
            candidates: List of candidate dicts with keys:
                title, channel, duration, url, description

        Returns:
            Dict with winner_index, confidence, reasoning, scores[]
        """
        if not candidates:
            return {"winner_index": 0, "confidence": 0.0, "reasoning": "no candidates", "scores": []}

        candidate_lines = []
        for i, c in enumerate(candidates):
            title_str = str(c.get("title", "") or "").strip()
            channel = str(c.get("channel", "") or "").strip()
            desc = str(c.get("description", "") or "")[:200]
            candidate_lines.append(
                f"Candidate {i}: title='{title_str}' channel='{channel}' desc='{desc}'"
            )

        system = YT_WINNER_SYSTEM_PROMPT
        user = f"""Scene: {scene_text[:500]}
Video topic: {title}

Candidates:
{chr(10).join(candidate_lines)}

Score each candidate and select the best winner.
Return JSON with winner_index, confidence, reasoning, and scores array."""

        try:
            raw = self._client.chat(
                user_prompt=user,
                system_prompt=system,
                temperature=0.1,
                max_tokens=2000,
                json_mode=True,
                json_schema=YT_WINNER_SCHEMA,
                timeout_seconds=float(os.environ.get("VIDZEO_BRAIN_WINNER_TIMEOUT", "180") or 180),
            )
            result = _loads_brain_json(raw)
            winner_idx = int(result.get("winner_index", 0))
            if 0 <= winner_idx < len(candidates):
                return result
            result["winner_index"] = 0
            return result
        except Exception as e:
            print(f"   Brain YT winner selection failed: {e}")
            return {
                "winner_index": 0,
                "confidence": 0.0,
                "reasoning": "fallback: first candidate",
                "scores": [
                    {"index": i, "relevance": 5.0, "footage_signal": 5.0,
                     "visual_match": 5.0, "total": 15.0}
                    for i in range(len(candidates))
                ],
            }
