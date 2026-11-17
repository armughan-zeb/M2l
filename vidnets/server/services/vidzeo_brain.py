"""Vidzeo Brain — unified LLM-powered scene planning for all pipelines.

Single entry point for:
- Scene section detection + subject extraction
- Character anchor generation per scene
- Abstract→visual metaphor conversion
- Per-pipeline query generation (YouTube, Web/Flickr, Stock)

Mechanical fallback only when OpenRouter is unavailable.
"""
import hashlib
import json
import os
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor, wait, as_completed
from pathlib import Path
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor, current_job
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
        # A named game title is gaming evidence on its own: "The game
        # returns to Vice City" in a Grand Theft Auto VI film is a gaming
        # scene even though no "video game" phrase appears.
        if game_keys_named_in_text(f"{subject} {scene_text} {topic}"):
            domain_category = "gaming"
        elif any(has_term(term) for term in ("video game", "gaming", "gameplay", "game trailer", "console game")):
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
    # Scene-relative game: when this scene's narration names a game, its
    # canonical title is a positive retrieval term, so mechanical rows
    # seek the scene's game (a GTA V-history scene retrieves GTA V),
    # not only the video title's game.
    scene_game = scene_subject_game_for_scene(scene_text, topic)
    if scene_game["key"]:
        positive = _term_list([subject, scene_game["title"], domain_anchor, domain_category])
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

# visual_subject_type values named in SCENE_QUERY_SYSTEM_PROMPT. The schema
# carries the same list as an enum so an invented type is caught by
# validation (and fixed by the worker) instead of flowing into
# visual_requirement, which switches on this value.
VISUAL_SUBJECT_TYPES = [
    "person", "organization", "product", "place", "event", "animal",
    "automobile", "ship", "rail_crew", "video_game", "component",
    "activity", "process", "named_entity", "concept",
]

SCENE_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scene_number": {"type": "integer", "minimum": 1},
        "container_subject": {"type": "string", "minLength": 1},
        "scene_subject": {"type": "string", "minLength": 1},
        "visual_intent": {"type": "string", "minLength": 1},
        "visual_subject": {"type": "string"},
        "visual_subject_type": {"type": "string", "enum": VISUAL_SUBJECT_TYPES},
        "subject_evidence": {"type": "string"},
        "visible_target": {"type": "string"},
        "identity_scope": {"type": "string", "enum": ["subject", "event", "context"]},
        # Retrieval contract (the prompt already demands these; the schema
        # used to forbid them via additionalProperties: false, so the LLM
        # dropped them and every row shipped with empty domain/intents).
        # They are required so an empty-field row fails validation and is
        # re-planned mechanically instead of poisoning retrieval.
        "domain_category": {"type": "string"},
        "scene_subject_type": {"type": "string", "enum": ["person", "object", "place", "event", "product", "topic", "animal", "food"]},
        # NOTE: scene_subject_game is deliberately NOT a schema property.
        # It is derived server-side from the scene's own narration
        # (scene_subject_game_for_scene) and stamped onto the row after
        # validation. Adding it here would make it required: the strict
        # normaliser promotes every property to required, so every plan
        # without it would fail validation — the same class of bug the
        # retrieval-contract comment above describes.
        "retrieval_positive_terms": {"type": "array", "items": {"type": "string"}},
        "retrieval_negative_terms": {"type": "array", "items": {"type": "string"}},
        "youtube_query_intents": {"type": "array", "items": {"type": "string"}},
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
        "domain_category", "scene_subject_type",
        "retrieval_positive_terms", "retrieval_negative_terms", "youtube_query_intents",
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

Scene-relative game rule: each scene's queries must name the game THAT scene narrates, not the video title's game. If a scene narrates an earlier game — its sales, the wait for the sequel, its legacy — query FOR that earlier game (e.g. a scene about Grand Theft Auto Five's two hundred million copies queries "Grand Theft Auto V", "GTA V Los Santos gameplay", never only "GTA VI"). A scene about the new game's trailers, world, or characters queries for the new game. Never put a game in a query that the scene does not narrate. Never query for the 2002 Vice City game ("Grand Theft Auto: Vice City", "GTA Vice City", "GTA III") under any circumstances; "Vice City" beside the new game's title is the new city and is fine.

Subject-type rule: scene_subject_type must match the subject itself, not the container default. A company or studio (Rockstar Games) is topic, never person. A place (Vice City, Leonida, Leonida Keys) is place, never person. A game title (Grand Theft Auto VI) is topic, never person. A named person is person. A wrong type fails validation and the whole chunk is re-planned.

Distinctness rule: no two scenes share a primary query. Every scene's primary YouTube and web queries must contain a word from that scene's own narration that no other scene shares — its distinctive entity, action, viewpoint, or moment (aerial, prison gates, delay announcement, 200 million copies). If two scenes genuinely narrate the same moment word for word, and only then, they may share a query.

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
                # Retrieval fields are required, not optional: the prompt
                # already demands them, and the merge used to fill an
                # omitted youtube_query_intents with nothing (no fallback),
                # so rows shipped with empty intents. Required lets the
                # worker fix a missing field instead of omitting it.
                "required": [
                    "scene_number",
                    "container_id",
                    "container_subject",
                    "scene_subject",
                    "container_boundary",
                    "domain_category",
                    "scene_subject_type",
                    "retrieval_positive_terms",
                    "retrieval_negative_terms",
                    "youtube_query_intents",
                ],
            },
        },
    },
    "required": ["is_listicle", "containers"],
}

YT_WINNER_SCHEMA = {
    "type": "object",
    "properties": {
        "winner_index": {"type": "integer", "minimum": 0},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string"},
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    # Per-candidate scores are 0-10 each (see
                    # YT_WINNER_SYSTEM_PROMPT); total is their sum, 0-30.
                    # Bounds are enforced here so the worker fixes an
                    # out-of-range value instead of the consumer silently
                    # clamping it to candidate 0.
                    "relevance": {"type": "number", "minimum": 0, "maximum": 10},
                    "footage_signal": {"type": "number", "minimum": 0, "maximum": 10},
                    "visual_match": {"type": "number", "minimum": 0, "maximum": 10},
                    "total": {"type": "number", "minimum": 0, "maximum": 30},
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
  It must match the subject itself: a company/studio is topic, never person; a place is place, never person; a game title is topic, never person. A format phrase ("video game official trailer gameplay") is never a subject.
- Scene-relative game rule: a scene's subject is the game THAT scene narrates. A scene about an earlier game's sales or legacy takes that earlier game as its subject, not the video title's game. Never seek the 2002 Vice City game ("Grand Theft Auto: Vice City", "GTA Vice City", "GTA III").
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
    # A section label is not a searchable subject: "In February" is a
    # calendar marker, not the thing on screen. Fall back to the film topic
    # (compacted) instead of building queries around the label, which is how
    # scenes ended up searching "In February February Amtrak departures".
    _SECTION_LABEL_MONTHS = (
        "january", "february", "march", "april", "may", "june", "july",
        "august", "september", "october", "november", "december",
    )
    subject = section_subject or topic or "topic"
    if any(month in subject.lower() for month in _SECTION_LABEL_MONTHS):
        subject = topic or subject
    words = re.findall(r'[a-zA-Z]{4,}', scene_text or "")
    subject_words = {w.lower() for w in re.findall(r'[a-zA-Z]+', subject or "")}
    visual_words = [w for w in words if w.lower() not in {
        "the", "this", "that", "with", "from", "they", "have", "been", "were",
        "what", "when", "where", "which", "their", "them", "about", "into",
        "could", "would", "should", "there", "these", "those", "because",
        "just", "also", "very", "more", "than", "then", "some", "made",
        "make", "like", "than", "even", "still", "after", "before",
    } and w.lower() not in subject_words][:3]
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
            # Never "entity": it is outside the schema enum and the
            # validator treats it as a wrong type. Infer from the subject
            # (studio -> topic, place -> place) or fall back to "topic".
            "scene_subject_type": _infer_scene_subject_type(query_subject, "", scene_text),
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

# --- Persisted scene plan (script-time pre-compute, resume reuse) --------

# Scene direction + web/YT/stock queries are computed once, when the script
# is made, by VidzeoBrain.plan_scenes — each plan row carries the direction
# (container_subject / scene_subject / visual_intent) and the queries for
# all three sources. Until now the rows lived only in memory (and in the
# app-level temp cache, which only stores FULLY directed plans): a resumed
# job re-filed every direction/query bridge request, even for scenes the
# first attempt had already directed. The Hindenburg job is the proof: its
# checkpoint source_query_plan.json holds only planner provenance (status
# "partial", 32 directed / 25 fallback of 57 scenes) — the query rows
# themselves were never persisted, and the temp cache refused the partial
# plan, so a resume would re-file all 57 scenes.
#
# The plan is therefore persisted on the job record, beside the other
# checkpoints (package/checkpoints/scene_plan.json), on EVERY plan_scenes
# completion — including partial ones. On load, only DIRECTED rows are
# reused (same validation as the fresh path: non-empty direction + a usable
# query for every source, no _query_fallback); mechanical/fallback rows are
# re-planned. A resumed job files zero new direction/query requests for its
# already-directed scenes. The media branches consume the plan through the
# existing prebuilt-queries path: they all funnel through plan_scenes, so
# no branch changes are needed.
_SCENE_PLAN_STORE_SCHEMA = "vidzeo_scene_plan_v1"
_SCENE_PLAN_STORE_FILENAME = "scene_plan.json"


def _scene_plan_digest(topic: str, script: str, scene_texts: List[str]) -> str:
    material = "\x1f".join([str(topic or ""), str(script or ""), *[str(t or "") for t in (scene_texts or [])]])
    return hashlib.sha1(material.encode("utf-8")).hexdigest()


def _scene_plan_store_path() -> Optional[Path]:
    """Job-checkpoint path for the persisted scene plan, or None outside a job.

    Derived from the job context's llm_cache_dir (package/checkpoints/
    model-cache). Without a job there is no job record to persist on, so
    persistence is skipped entirely — never write to a shared temp dir.
    """
    try:
        cache_dir = current_job().get("llm_cache_dir")
    except Exception:
        return None
    if not cache_dir:
        return None
    return Path(str(cache_dir)).expanduser().parent / _SCENE_PLAN_STORE_FILENAME


def _is_directed_scene_row(row: Any) -> bool:
    """True when a stored plan row was LLM-directed (reusable on resume).

    Mirrors the fresh-path validation in _plan_scenes_impl: non-empty
    direction fields, a usable query for every source, and no
    _query_fallback marker. Mechanical rows are never served as directed —
    that is how word-salad queries were once cached and reused forever.
    Also enforces the retrieval contract (see _directed_scene_row_error):
    a row with empty domain_category/intents, a format-phrase subject, a
    wrong subject type, or queries that carry no entity/viewpoint words is
    not directed — it is re-planned mechanically on resume.
    """
    if not isinstance(row, dict) or row.get("_query_fallback"):
        return False
    if any(not str(row.get(field) or "").strip()
           for field in ("container_subject", "scene_subject", "visual_intent")):
        return False
    query_fields = {
        "youtube": ("primary", "supporting", "fallback"),
        "web": ("primary", "supporting", "fallback"),
        "stock": ("primary_query", "alt_query", "fallback_query"),
    }
    for source, fields in query_fields.items():
        queries = row.get(source)
        if not isinstance(queries, dict):
            return False
        if not any(str(queries.get(field) or "").strip() for field in fields):
            return False
    return _directed_scene_row_error(row) is None


# Query-shape contract (GTA VI fix). The prompts already demand the right
# thing, but the schema used to forbid these fields, so the LLM dropped
# them and every row shipped with empty domain_category, empty
# retrieval_positive_terms and empty youtube_query_intents — and a format
# phrase ("video game official trailer gameplay") as the container
# subject, typed "person" for Vice City, Leonida Keys and Rockstar Games.
_QUERY_SHAPE_STOPWORDS = set(
    "the a an of on in to and or with at by for from was were is are had have has its it their they "
    "this that these those over under after before during between into about around across aboard along "
    "among behind below beneath beside beyond despite until up upon within without".split()
)
_FORMAT_PHRASE_WORDS = {
    "video", "videos", "game", "games", "gameplay", "trailer", "trailers",
    "official", "footage", "documentary", "clip", "clips", "4k", "hd",
    "preview", "teaser", "reveal", "episode", "series", "show", "movie",
    "film", "streaming", "live", "stream", "cinematic", "montage",
}
# visual_subject_type -> scene_subject_types that may describe it. Only
# the definite families are mapped; vague types (named_entity, concept,
# activity, process, component) carry no verdict here.
_VISUAL_TO_SCENE_TYPES = {
    "person": {"person"},
    "place": {"place"},
    "video_game": {"topic"},
    "organization": {"topic"},
    "product": {"product", "object"},
    "event": {"event"},
    "animal": {"animal"},
    "ship": {"topic", "object"},
    "automobile": {"object", "product"},
    "food": {"food"},
    "rail_crew": {"person"},
}
_ORG_SUFFIX_RE = re.compile(r"\b(?:games|studios|entertainment|interactive|company|corporation|pictures)\s*$", re.I)
_PLACE_WORD_RE = re.compile(
    r"\b(?:city|streets?|island|keys|beach|state|county|country|neighborhood|district|town|village|province|coast|peninsula|canal)\b", re.I)
_GAME_TITLE_RE = re.compile(r"\b(?:grand theft auto|gta)\s*(?:vi|6|v|5|iv|4|iii|3|six|five|four|three)?\b", re.I)

# Scene-relative game identity (GTA VI fix, user correction Nov 16).
# Relevance is scene-relative, not title-absolute: a scene whose narration
# is ABOUT an earlier game (GTA V sales / the decade-long wait / its
# legacy) must query FOR that game, not the video title's game. The game
# is derived from the scene's OWN narration, never assumed from the title.
_GAME_ROMAN_TO_NUMBER = {
    "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8",
    "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
}
_GAME_NUMERAL_RE = re.compile(
    r"\b(?:grand theft auto|gta)\s+(iii|iv|v|vi|vii|viii|3|4|5|6|7|8|three|four|five|six|seven|eight)\b", re.I)


def _game_key_for_numeral(numeral: str) -> str:
    number = _GAME_ROMAN_TO_NUMBER.get(str(numeral or "").strip().lower(), str(numeral or "").strip())
    return f"gta_{number}" if number else ""


def game_canonical_title(game_key: str) -> str:
    """Canonical title for a scene-subject game key (``gta_v``)."""
    key = str(game_key or "").strip().lower()
    if not key.startswith("gta_"):
        return ""
    numeral = key[4:]
    roman = {"3": "III", "4": "IV", "5": "V", "6": "VI", "7": "VII", "8": "VIII"}.get(numeral, numeral.upper())
    return f"Grand Theft Auto {roman}"


def game_aliases(game_key: str) -> List[str]:
    """Searchable aliases for a scene-subject game key."""
    title = game_canonical_title(game_key)
    if not title:
        return []
    numeral = str(game_key or "").strip().lower()[4:]
    roman = title.rsplit(" ", 1)[-1]
    return [title, f"GTA {roman}", f"GTA {numeral}", f"Grand Theft Auto {numeral}"]


def game_keys_named_in_text(text: Any) -> List[str]:
    """Game keys explicitly named in this text, in order of first mention.

    Token-boundary matching only: the garbled LLM token "GTA VI V" names
    GTA VI, not GTA V (``auto v`` inside ``auto vi v`` is not a match).
    A bare "Grand Theft Auto" with no numeral names no game — it cannot
    tell which instalment the scene means.
    """
    found: List[str] = []
    for match in _GAME_NUMERAL_RE.finditer(str(text or "")):
        key = _game_key_for_numeral(match.group(1))
        if key and key not in found:
            found.append(key)
    return found


def title_game_key(title: Any) -> str:
    """The game named in the video title, or "" when the title names none."""
    named = game_keys_named_in_text(title)
    return named[0] if named else ""


def scene_subject_game_for_scene(scene_text: Any, title: Any = "") -> Dict[str, Any]:
    """The game a scene's queries must seek, derived from ITS narration.

    - The scene names a game other than the title's game -> that game is
      the subject (a GTA V-history scene queries for GTA V, even in a
      GTA VI video). First-named non-title game wins.
    - The scene names only the title's game -> the title's game.
    - The scene names no game -> the title's game, implicitly (``from_title``).
    - Nothing names a game -> no subject game (``key`` is "").
    """
    named = game_keys_named_in_text(scene_text)
    title_game = title_game_key(title)
    if named:
        non_title = [key for key in named if key != title_game]
        key = non_title[0] if non_title else named[0]
        return {"key": key, "title": game_canonical_title(key),
                "aliases": game_aliases(key), "named_in_scene": True, "from_title": False}
    if title_game:
        return {"key": title_game, "title": game_canonical_title(title_game),
                "aliases": game_aliases(title_game), "named_in_scene": False, "from_title": True}
    return {"key": "", "title": "", "aliases": [], "named_in_scene": False, "from_title": False}


# The 2002 Vice City game is never a retrieval target: no query may seek
# it. "Grand Theft Auto: Vice City" / "GTA Vice City" is that game's title,
# not the new Vice City — but "Vice City" next to "GTA VI" is the new city
# and stays allowed. Year/III markers are forbidden in any combination.
_FORBIDDEN_2002_RE = re.compile(
    r"\b(?:gta\s+iii|grand theft auto\s+iii|grand theft auto\s+vice city|"
    r"gta\s+vice city|vice city\s+2002|2002\s+vice city|vice city\s+(?:ps2|original)\b)", re.I)


def query_seeks_forbidden_2002(query: Any) -> bool:
    """True when a query seeks the 2002 Vice City game (GTA III-era)."""
    return bool(_FORBIDDEN_2002_RE.search(str(query or "")))


def _query_game_keys(query: Any) -> set:
    """Game keys a query names, via the same token-boundary match."""
    return set(game_keys_named_in_text(query))


# visual_subject_type -> the scene_subject_type it may be described as.
# (Single-value inference default for the same mapping.)
_VISUAL_TO_SCENE_TYPE_DEFAULT = {
    "person": "person",
    "place": "place",
    "video_game": "topic",
    "organization": "topic",
    "product": "product",
    "event": "event",
    "animal": "animal",
    "ship": "object",
    "automobile": "object",
    "food": "food",
    "rail_crew": "person",
}


def _infer_scene_subject_type(subject: Any, visual_subject_type: Any = "", scene_text: Any = "") -> str:
    """Infer scene_subject_type from evidence, never defaulting to "person".

    The container-map default used to stamp "person" on everything, which
    is how Rockstar Games (organization), Vice City / Leonida (places) and
    Grand Theft Auto VI (video game) all shipped typed as a person. Order:
    the brain's visual type when present, then the subject text itself
    (game title / company suffix / place word), then a name-like
    multi-word capitalized name -> "person" (the same heuristic the
    mechanical bundle uses: "Erling Haaland", "Lucia Caminos and Jason
    Duval"), then the neutral "topic". "person" requires person evidence;
    a bare capitalized name with no org/place/game signal is that
    evidence — without it the adapter's wrong-global-person stripping,
    which only runs for person subjects, stops firing.
    """
    visual = str(visual_subject_type or "").strip().lower()
    if visual in _VISUAL_TO_SCENE_TYPE_DEFAULT:
        return _VISUAL_TO_SCENE_TYPE_DEFAULT[visual]
    text = str(subject or "").strip()
    if _GAME_TITLE_RE.search(text) and game_keys_named_in_text(text):
        return "topic"
    if _ORG_SUFFIX_RE.search(text):
        return "topic"
    if _PLACE_WORD_RE.search(text):
        return "place"
    words = [w for w in re.split(r"\s+", text) if w]
    capitals = [w for w in words if w[:1].isupper()]
    connectors = {"and", "&", "of", "the", "de", "van", "von"}
    if (len(capitals) >= 2
            and all(w[:1].isupper() or w.lower().strip(",") in connectors for w in words)):
        return "person"
    return "topic"


# Fallback YouTube intent suffixes per domain, mirroring the intent map in
# CONTAINER_SYSTEM_PROMPT. The container merge used to leave
# youtube_query_intents empty when the LLM omitted them; the fresh-path
# validator then failed the whole chunk. An omitted field is filled from
# the category instead of shipping empty.
_YOUTUBE_INTENTS_BY_CATEGORY = {
    "gaming": ["raw gameplay no commentary", "direct gameplay capture 4k",
               "official gameplay trailer", "uncut gameplay 60fps", "cinematic trailer 4k"],
    "person": ["public appearance footage", "ceremony arrival footage",
               "archival event footage", "documentary archive footage", "daily life b-roll"],
    "sports": ["match action clean broadcast", "skills goals 4k no commentary",
               "training raw footage", "stadium broadcast 4k", "slow motion highlights 1080p"],
    "disaster": ["raw footage", "aftermath uncut", "rescue raw footage",
                 "aerial location clean footage", "archival broadcast footage", "newsreel archive uncut"],
    "travel": ["4k walking tour no talking", "drone footage clean 4k",
               "street b roll footage", "city location 4k b-roll", "landmark raw footage"],
    "technology": ["hands on demo b-roll 4k", "product close up clean",
                   "official launch clean footage", "technology 4k b roll", "unboxing no talking b roll"],
    "automotive": ["car driving raw footage 4k", "exterior b-roll clean",
                   "interior detail 4k", "track testing raw footage", "official promo b-roll"],
    "history": ["raw archival film footage", "newsreel moving footage clean",
                "historic location b roll", "ship at sea archival clean"],
    "science": ["laboratory experiment 4k b roll", "microscope raw footage",
                "research facility clean footage", "scientific demonstration clean"],
    "crime": ["case location raw b-roll", "archive newsreel uncut",
              "police investigation archival footage", "aerial scene location 4k"],
    "food": ["close up food b roll 4k", "cooking preparation raw footage",
             "fresh produce market b roll", "harvesting farm clean footage"],
    "animals": ["4k wildlife b roll", "animal habitat raw footage",
                "nature close up clean footage", "safari animal behavior 4k"],
    "finance": ["trading floor raw b roll", "factory manufacturing 4k",
                "shipping logistics clean footage", "office corporate b roll 4k"],
}


def _default_youtube_intents(domain_category: Any) -> List[str]:
    return list(_YOUTUBE_INTENTS_BY_CATEGORY.get(
        str(domain_category or "").strip().lower(),
        ["raw footage", "archival footage", "documentary archive footage", "location b-roll"],
    ))


def _is_format_phrase_subject(subject: Any) -> bool:
    """True when a subject is only format words, not a real subject.

    "video game official trailer gameplay" is a format phrase; "Grand
    Theft Auto VI" is not (grand/theft/auto/vi are not format words).
    Stopwords are ignored, so "Game of Thrones" survives via "thrones".
    """
    words = [word for word in re.findall(r"[a-z0-9]+", str(subject or "").lower())
             if word not in _QUERY_SHAPE_STOPWORDS]
    return bool(words) and all(word in _FORMAT_PHRASE_WORDS for word in words)


_SCENE_SUBJECT_TYPE_ENUM = {"person", "object", "place", "event", "product", "topic", "animal", "food"}


def _subject_type_mismatch(row: Dict[str, Any]) -> bool:
    """True when scene_subject_type contradicts the subject itself.

    The container-map default stamps "person" on everything; a game title,
    a studio, or a place typed "person" is a wrong row, not a person beat.
    The text checks apply to ANY claimed type, not just "person": a studio
    typed "place" and a place typed "product" are wrong too. A value
    outside the schema enum (e.g. the mechanical "entity") is a mismatch
    by itself. One direction is deliberately NOT enforced: a personal
    name typed as a place cannot be told apart from a place name by
    capitalization alone ("Berlin Wall"), so it is left to the visual
    type mapping.
    """
    subject = str(row.get("scene_subject") or row.get("container_subject") or "").strip()
    scene_type = str(row.get("scene_subject_type") or "").strip().lower()
    if not subject or not scene_type:
        return False
    if scene_type not in _SCENE_SUBJECT_TYPE_ENUM:
        return True
    visual_type = str(row.get("visual_subject_type") or "").strip().lower()
    allowed = _VISUAL_TO_SCENE_TYPES.get(visual_type)
    if allowed is not None and scene_type not in allowed:
        return True
    if game_keys_named_in_text(subject) and scene_type in {"person", "place"}:
        return True
    if _ORG_SUFFIX_RE.search(subject) and scene_type in {"person", "place"}:
        return True
    if _PLACE_WORD_RE.search(subject) and scene_type in {"person", "product"}:
        return True
    return False


def _stem(word: str) -> str:
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _visual_intent_action_words(visual_intent: str, entity_words: set) -> set:
    """Viewpoint/action words mined from the scene's visual intent.

    Viewpoint phrases (aerial, street-level, from the cab, prison gates)
    plus visible/action words: -ing verbs and content words that are not
    the entity, not stopwords, and not format words. Empty when the intent
    carries no usable shot words — then the shape check cannot enforce the
    viewpoint half and skips it.
    """
    intent = re.sub(r"\s+", " ", str(visual_intent or "")).strip()
    if not intent:
        return set()
    words: set = set()
    try:
        from .media_query_strategy import _viewpoint_phrases, _VISIBLE
        for phrase in _viewpoint_phrases(intent):
            words.add(phrase.lower())
        for visible in _VISIBLE:
            if re.search(r"\b" + re.escape(visible) + r"\b", intent, re.I):
                words.add(visible.lower())
    except Exception:
        pass
    for word in re.findall(r"[a-z0-9]+", intent.lower()):
        if (len(word) >= 4 and word not in _QUERY_SHAPE_STOPWORDS
                and word not in _FORMAT_PHRASE_WORDS and word not in entity_words
                and word not in {"context", "shows", "show", "depicts", "features",
                                 "featuring", "shot", "shots", "view", "scene"}):
            words.add(word)
    return words


def _primary_query_shape_ok(primary: Any, entity_words: set, viewpoint_words: set) -> bool:
    """One primary query must carry the entity + a viewpoint/action word.

    Shared by the plan-row shape check and the flat adapted-row check
    (cache loader), which only ever sees one source's primary at a time.
    Either half is skipped when the row gives it nothing to check against.
    """
    text = str(primary or "").strip().lower()
    if not text:
        return False
    if not entity_words:
        return True
    primary_words = {_stem(word) for word in re.findall(r"[a-z0-9]+", text)}
    if not any(_stem(word) in primary_words for word in entity_words):
        return False
    if viewpoint_words:
        for item in viewpoint_words:
            parts = [_stem(word) for word in re.findall(r"[a-z0-9]+", item.lower())
                     if word not in _QUERY_SHAPE_STOPWORDS]
            if parts and all(part in primary_words for part in parts):
                return True
        return False
    return True


def _scene_query_shape_ok(row: Dict[str, Any]) -> bool:
    """Primary YT + web queries must carry entity + viewpoint/action words.

    Entity: a distinctive token of the scene/container subject. Viewpoint/
    action: a word or phrase mined from the visual intent (aerial,
    street-level, prison gates, driving, ...). Either half is skipped when
    the row gives it nothing to check against.
    """
    entity_words = set()
    for field in ("scene_subject", "container_subject"):
        for word in re.findall(r"[a-z0-9]+", str(row.get(field) or "").lower()):
            if (len(word) >= 3 and word not in _QUERY_SHAPE_STOPWORDS
                    and word not in _FORMAT_PHRASE_WORDS):
                entity_words.add(word)
    if not entity_words:
        return True
    viewpoint_words = _visual_intent_action_words(row.get("visual_intent") or "", entity_words)
    for source in ("youtube", "web"):
        queries = row.get(source)
        if not isinstance(queries, dict):
            return False
        if not _primary_query_shape_ok(queries.get("primary"), entity_words, viewpoint_words):
            return False
    return True


def _scene_game_query_error(primary_queries: List[str], scene_text: Any, title: Any = "") -> Optional[str]:
    """Scene-relative game consistency for a scene's primary queries.

    The queries must seek the game THIS scene narrates: a GTA V-history
    scene must name GTA V in at least one primary (a query set that only
    ever says GTA VI for such a scene is wrong), and no primary may name
    a game the scene does not narrate (plus the title's game, which a
    scene may always use implicitly). The 2002 Vice City game is
    forbidden in every query, whatever the scene narrates.
    """
    if not scene_text:
        return None
    primaries = [str(query or "").strip() for query in (primary_queries or []) if str(query or "").strip()]
    if not primaries:
        return None
    for query in primaries:
        if query_seeks_forbidden_2002(query):
            return "query seeks the 2002 Vice City game"
    named = game_keys_named_in_text(scene_text)
    title_game = title_game_key(title)
    allowed = set(named)
    if title_game:
        allowed.add(title_game)
    if not named and not title_game:
        return None
    for query in primaries:
        foreign = _query_game_keys(query) - allowed
        if foreign:
            return f"query names {game_canonical_title(sorted(foreign)[0])}, which this scene does not narrate"
    subject_game = scene_subject_game_for_scene(scene_text, title)
    if subject_game["key"]:
        # Token-boundary game detection only: the garbled LLM token
        # "Grand Theft Auto VI V" must NOT count as naming GTA V — a
        # substring check would pass it ("grand theft auto v" is a prefix
        # of "grand theft auto vi v").
        if not any(subject_game["key"] in _query_game_keys(query) for query in primaries):
            return (f"no primary query names the scene's subject game "
                    f"{subject_game['title']}")
    return None


def _directed_scene_row_error(row: Any, scene_text: Any = None, title: Any = "") -> Optional[str]:
    """First retrieval-contract violation in a plan row, or None.

    Shared by _is_directed_scene_row (resume reuse) and the fresh-path
    validation in _plan_scenes_impl (LLM output). A row failing any of
    these is re-planned mechanically, never shipped with empty fields.
    Pass the scene's narration text (and the video title) when available:
    without it the scene-relative game check cannot run and is skipped.
    """
    if not isinstance(row, dict):
        return "not a scene row"
    if not str(row.get("domain_category") or "").strip():
        return "empty domain_category"
    positive = row.get("retrieval_positive_terms")
    if not isinstance(positive, list) or len([term for term in positive if str(term or "").strip()]) < 3:
        return "fewer than 3 retrieval_positive_terms"
    intents = row.get("youtube_query_intents")
    if not isinstance(intents, list) or not any(str(term or "").strip() for term in intents):
        return "empty youtube_query_intents"
    for field in ("container_subject", "scene_subject"):
        if _is_format_phrase_subject(row.get(field)):
            return f"{field} is a format phrase, not a subject"
    if _subject_type_mismatch(row):
        return "scene_subject_type does not match the subject"
    # The game check runs before the shape check: a query seeking the
    # 2002 Vice City game, or naming a game this scene does not narrate,
    # is wrong as a game query whatever its shape.
    primaries = []
    for source in ("youtube", "web"):
        queries = row.get(source)
        if isinstance(queries, dict):
            primaries.append(str(queries.get("primary") or ""))
    game_error = _scene_game_query_error(primaries, scene_text, title)
    if game_error:
        return game_error
    if not _scene_query_shape_ok(row):
        return "primary youtube/web query lacks entity or viewpoint/action words"
    return None


def _normalized_query_tokens(query: Any) -> List[str]:
    return [word for word in re.findall(r"[a-z0-9]+", str(query or "").lower())
            if word not in _QUERY_SHAPE_STOPWORDS]


def _scene_distinctive_tokens(scene_text: Any, sibling_texts: List[str]) -> set:
    """Content tokens this scene owns and no sibling scene shares.

    Narration only — never the visual intent. The intent is the LLM's
    own paraphrase, so counting its words would let the LLM grade its
    own homework (a generic intent word would rescue a generic query).
    A scene's query must carry at least one of these narration tokens
    (or the scene has none, and the check is skipped — two scenes with
    near-identical narration cannot be told apart by tokens).
    """
    own = set()
    for word in re.findall(r"[a-z0-9]+", str(scene_text or "").lower()):
        if (len(word) >= 4 and word not in _QUERY_SHAPE_STOPWORDS
                and word not in _FORMAT_PHRASE_WORDS):
            own.add(word)
    sibling_words = set()
    for sibling in (sibling_texts or []):
        sibling_words.update(re.findall(r"[a-z0-9]+", str(sibling or "").lower()))
    return {word for word in own if word not in sibling_words}


def validate_scene_query_plan(rows: List[Dict[str, Any]], scene_texts: List[str],
                              title: Any = "") -> List[Dict[str, Any]]:
    """Plan-level query errors: duplicates + scene-distinctiveness.

    Returns one {"scene_number", "error"} per failing scene (empty = valid).
    Per-row contract errors are reported by _directed_scene_row_error;
    this adds what a single row cannot see:
    - two scenes share a primary query (exact, or identical once the
      shared title anchor is stripped), unless their narrations are
      genuinely identical — proven by comparing the actual texts.
    - a scene's primary queries carry no token distinctive to its own
      narration (the query could belong to any sibling scene).
    """
    rows = list(rows or [])
    scene_texts = list(scene_texts or [])
    errors: List[Dict[str, Any]] = []
    seen: Dict[str, Any] = {}

    def _norm_text(text: Any) -> str:
        return re.sub(r"\s+", " ", str(text or "")).strip().lower()

    anchor_words = set(_normalized_query_tokens(title))
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        scene_number = row.get("scene_number", index + 1)
        text = scene_texts[index] if index < len(scene_texts) else ""
        siblings = [t for j, t in enumerate(scene_texts) if j != index]
        # Scene-distinctiveness: at least one primary must carry a token
        # this scene owns and no sibling shares.
        distinctive = _scene_distinctive_tokens(text, siblings)
        if distinctive:
            primaries = []
            for source in ("youtube", "web"):
                queries = row.get(source)
                if isinstance(queries, dict):
                    primaries.append(str(queries.get("primary") or ""))
            primary_words = set()
            for query in primaries:
                primary_words.update(_stem(word) for word in re.findall(r"[a-z0-9]+", query.lower()))
            if not any(_stem(word) in primary_words for word in distinctive):
                errors.append({"scene_number": scene_number,
                               "error": "primary queries carry no token distinctive to this scene's narration"})
                continue
        # Cross-scene duplicates, per source.
        for source in ("youtube", "web"):
            queries = row.get(source)
            primary = str(queries.get("primary") or "").strip() if isinstance(queries, dict) else ""
            if not primary:
                continue
            tokens = _normalized_query_tokens(primary)
            key = " ".join(tokens)
            stripped = " ".join(word for word in tokens if word not in anchor_words)
            for seen_key, seen_info in list(seen.get(source, {}).items()):
                seen_index, seen_stripped = seen_info
                if _norm_text(text) and _norm_text(text) == _norm_text(scene_texts[seen_index] if seen_index < len(scene_texts) else ""):
                    continue  # genuinely identical narration: same query allowed
                if key == seen_key or (stripped and stripped == seen_stripped):
                    errors.append({"scene_number": scene_number,
                                   "error": f"{source} primary query duplicates scene {seen_index + 1}"})
                    break
            seen.setdefault(source, {})[key] = (index, stripped)
    return errors


def _adapted_row_primaries(row: Dict[str, Any]) -> List[str]:
    """Primary query strings carried by one flat adapted row."""
    primaries = []
    for field in ("search_query", "youtube_query", "web_query", "primary_query"):
        value = str(row.get(field) or "").strip()
        if value:
            primaries.append(value)
    return primaries


def _adapted_scene_row_error(row: Any, scene_text: Any = None, title: Any = "") -> Optional[str]:
    """Retrieval-contract error for one FLAT adapted row, or None.

    Adapted rows (app.py _adapt_scene_brain_plans / queries.json) carry
    one source's queries as flat strings, not nested youtube/web dicts —
    the plan-row validator cannot run on them as-is. The contract checks
    (fields, format phrase, subject type, game) run on the flat fields;
    the shape check runs on this row's own primary only.
    """
    if not isinstance(row, dict):
        return "not a scene row"
    if not str(row.get("domain_category") or "").strip():
        return "empty domain_category"
    positive = row.get("retrieval_positive_terms")
    if not isinstance(positive, list) or len([term for term in positive if str(term or "").strip()]) < 3:
        return "fewer than 3 retrieval_positive_terms"
    intents = row.get("youtube_query_intents")
    if not isinstance(intents, list) or not any(str(term or "").strip() for term in intents):
        return "empty youtube_query_intents"
    for field in ("section_subject", "container_subject", "scene_subject"):
        if _is_format_phrase_subject(row.get(field)):
            return f"{field} is a format phrase, not a subject"
    if _subject_type_mismatch(row):
        return "scene_subject_type does not match the subject"
    primaries = _adapted_row_primaries(row)
    if not primaries:
        return "no primary query"
    for query in primaries:
        if query_seeks_forbidden_2002(query):
            return "query seeks the 2002 Vice City game"
    if scene_text:
        named = game_keys_named_in_text(scene_text)
        title_game = title_game_key(title)
        allowed = set(named)
        if title_game:
            allowed.add(title_game)
        if named or title_game:
            for query in primaries:
                foreign = _query_game_keys(query) - allowed
                if foreign:
                    return f"query names {game_canonical_title(sorted(foreign)[0])}, which this scene does not narrate"
    entity_words = set()
    for field in ("scene_subject", "container_subject"):
        for word in re.findall(r"[a-z0-9]+", str(row.get(field) or "").lower()):
            if (len(word) >= 3 and word not in _QUERY_SHAPE_STOPWORDS
                    and word not in _FORMAT_PHRASE_WORDS):
                entity_words.add(word)
    # Stock queries are generic by design ("generic visible subjects and
    # actions suitable for stock libraries"): the entity + visual-intent
    # viewpoint shape check does not apply to them. Contract fields,
    # subject type, and the game checks above still do.
    if str(row.get("query_source") or "").strip().lower() == "stock":
        return None
    viewpoint_words = _visual_intent_action_words(row.get("visual_intent") or "", entity_words)
    if not any(_primary_query_shape_ok(primary, entity_words, viewpoint_words) for primary in primaries):
        return "primary query lacks entity or viewpoint/action words"
    return None


def validate_adapted_scene_plan(bundle: Dict[str, Any], scene_texts: List[str],
                                title: Any = "") -> Optional[str]:
    """First error in a flat adapted bundle (web/yt/stock rows), or None.

    What the shared-plan cache loader must run before serving a stored
    bundle: per-row contract errors on every source, the scene-level
    subject-game requirement (at least one primary across web+yt names
    the scene's subject game), and cross-scene primary duplicates.
    """
    if not isinstance(bundle, dict):
        return "not a query bundle"
    scene_texts = list(scene_texts or [])
    for source in ("web", "yt", "stock"):
        rows = bundle.get(source)
        if not isinstance(rows, list):
            return f"missing {source} rows"
        for index, row in enumerate(rows):
            text = scene_texts[index] if index < len(scene_texts) else ""
            error = _adapted_scene_row_error(row, scene_text=text, title=title)
            if error:
                return f"scene {index + 1} {source}: {error}"
    # Scene-level: at least one primary across web+yt names the subject game.
    for index, text in enumerate(scene_texts):
        subject_game = scene_subject_game_for_scene(text, title)
        if not subject_game["key"]:
            continue
        primaries = []
        for source in ("web", "yt"):
            rows = bundle.get(source) or []
            if index < len(rows) and isinstance(rows[index], dict):
                primaries.extend(_adapted_row_primaries(rows[index]))
        game_error = _scene_game_query_error(primaries, text, title)
        if game_error:
            return f"scene {index + 1}: {game_error}"
    # Cross-scene duplicates on flat primaries (same rule as plan rows).
    flat_rows: List[Dict[str, Any]] = []
    for index, text in enumerate(scene_texts):
        web_rows = bundle.get("web") or []
        yt_rows = bundle.get("yt") or []
        web_primary = ""
        yt_primary = ""
        if index < len(web_rows) and isinstance(web_rows[index], dict):
            web_primary = str(web_rows[index].get("search_query") or web_rows[index].get("primary_query") or "")
        if index < len(yt_rows) and isinstance(yt_rows[index], dict):
            yt_primary = str(yt_rows[index].get("search_query") or yt_rows[index].get("youtube_query") or "")
        flat_rows.append({"scene_number": index + 1,
                          "youtube": {"primary": yt_primary},
                          "web": {"primary": web_primary},
                          "visual_intent": (web_rows[index].get("visual_intent") if index < len(web_rows) and isinstance(web_rows[index], dict) else "")})
    plan_errors = validate_scene_query_plan(flat_rows, scene_texts, title)
    if plan_errors:
        first = plan_errors[0]
        return f"scene {first['scene_number']}: {first['error']}"
    return None


def validate_cached_scene_bundle(bundle: Any, scene_texts: List[str], title: Any = "") -> Optional[str]:
    """Cache-loader gate: first error in a stored bundle, or None if servable."""
    return validate_adapted_scene_plan(bundle, scene_texts, title)


def load_persisted_scene_plan(topic: str, script: str, scene_texts: List[str]) -> Optional[List[Dict]]:
    """Return the persisted plan rows for these exact inputs, or None.

    The digest covers topic + script + every scene text, so a changed
    script or re-cut scene list never reuses a stale plan. Returns the
    stored rows as-is (directed and fallback alike); the caller filters
    with _is_directed_scene_row.
    """
    path = _scene_plan_store_path()
    if path is None or not path.is_file():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(stored, dict) or stored.get("_schema") != _SCENE_PLAN_STORE_SCHEMA:
        return None
    if stored.get("digest") != _scene_plan_digest(topic, script, scene_texts):
        return None
    rows = stored.get("scenes")
    if not isinstance(rows, list) or len(rows) != len(scene_texts or []):
        return None
    return [dict(row) if isinstance(row, dict) else {} for row in rows]


def store_persisted_scene_plan(topic: str, script: str, scene_texts: List[str], rows: List[Dict]) -> bool:
    """Atomically persist plan rows on the job record. Best-effort: a
    persistence failure must never break planning. Returns True on success."""
    path = _scene_plan_store_path()
    if path is None or not isinstance(rows, list):
        return False
    payload = {
        "_schema": _SCENE_PLAN_STORE_SCHEMA,
        "digest": _scene_plan_digest(topic, script, scene_texts),
        "topic": str(topic or ""),
        "scene_count": len(scene_texts or []),
        "scenes": [dict(row) if isinstance(row, dict) else {} for row in rows],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.part")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return True
    except Exception:
        return False


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
        """Generate full scene plans for all scenes.

        Scene direction + queries are computed once and persisted on the job
        record (see load/store_persisted_scene_plan): a resumed call reuses
        its already-directed rows and files new bridge requests only for
        the scenes the persisted plan never directed.
        """
        self._last_scene_model_routes = []
        persisted = load_persisted_scene_plan(topic, script, scene_texts)
        if persisted is not None:
            reusable = {index: row for index, row in enumerate(persisted)
                        if _is_directed_scene_row(row)}
            missing = [index for index in range(len(scene_texts)) if index not in reusable]
            if not missing:
                print(f"   [brain] persisted scene plan reused: {len(reusable)} directed scene(s), 0 new requests")
                self._last_scene_model_routes = [self._route_snapshot(
                    status="directed", reason="persisted_scene_plan")]
                return [dict(reusable[index]) for index in range(len(scene_texts))]
            sub_texts = [scene_texts[index] for index in missing]
            sub_subjects = [
                section_subjects[index]
                if section_subjects and index < len(section_subjects) else ""
                for index in missing
            ]
            fresh = self._plan_scenes_impl(
                topic, script, sub_texts, sub_subjects, is_listicle, pipeline_type, extra_hint="",
            )
            fresh_by_index: Dict[int, Dict] = {}
            if isinstance(fresh, list):
                for offset, index in enumerate(missing):
                    plan = fresh[offset] if offset < len(fresh) else None
                    if isinstance(plan, dict) and plan:
                        plan = dict(plan)
                        plan["scene_number"] = index + 1
                        fresh_by_index[index] = plan
            merged: List[Dict] = []
            for index, text in enumerate(scene_texts):
                if index in reusable:
                    merged.append(dict(reusable[index]))
                elif index in fresh_by_index:
                    merged.append(fresh_by_index[index])
                else:
                    subject = section_subjects[index] if section_subjects and index < len(section_subjects) else ""
                    plan = _mechanical_scene_plan(text, index + 1, subject, topic)
                    plan['_query_fallback'] = 'persisted_plan_missing_chunk'
                    merged.append(plan)
            if not self._last_scene_model_routes:
                self._last_scene_model_routes = [self._route_snapshot(
                    status="directed" if reusable or fresh_by_index else "failed",
                    reason="persisted_scene_plan_partial" if missing else "",
                )]
            store_persisted_scene_plan(topic, script, scene_texts, merged)
            return merged
        rows = self._plan_scenes_impl(
            topic, script, scene_texts, section_subjects, is_listicle, pipeline_type, extra_hint="",
        )
        if isinstance(rows, list) and rows:
            store_persisted_scene_plan(topic, script, scene_texts, rows)
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
                # The LLM may omit intents even though the schema now
                # requires them; fill from the domain instead of shipping
                # empty (an empty intents row fails the fresh-path check).
                youtube_query_intents = _term_list(
                    row.get("youtube_query_intents") or _default_youtube_intents(domain_category), limit=8)
                cleaned = {
                    "scene_number": idx + 1,
                    "container_id": container_id,
                    "container_subject": subject if not is_intro and not is_outro else (subject or topic or ""),
                    "subcontainer_subject": re.sub(r"\s+", " ", str(row.get("subcontainer_subject") or "").strip()),
                    "scene_subject": scene_subject or subject or topic or "",
                    "scene_subject_type": str(row.get("scene_subject_type") or "").strip().lower(),
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
                # Never ship a contradicted or missing subject type: the
                # old default stamped "person" on studios, places and
                # game titles. A missing/invalid/wrong value is replaced
                # with the inferred type (visual type, then subject text,
                # then neutral "topic") instead of the "person" default.
                if not cleaned.get("scene_subject_type") or _subject_type_mismatch(cleaned):
                    cleaned["scene_subject_type"] = _infer_scene_subject_type(
                        cleaned.get("scene_subject") or cleaned.get("container_subject"),
                        row.get("visual_subject_type") or "",
                        scene_text,
                    )
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
            chunk_size = int(os.environ.get("VIDZEO_BRAIN_SCENE_CHUNK_SIZE", "32") or 32)
        except Exception:
            chunk_size = 32
        # BULK queries: one request carries up to 32 scenes (a whole 2-min
        # job in a single request) instead of 12-scene slices. The schema
        # has no scenes-per-request cap; the binding limits are compose
        # time (~20s/scene measured — 32 scenes ≈ 11 min, inside the
        # 15-min request wall) and output tokens (~260/scene — 32 scenes
        # ≈ 8k tokens, inside the 12000 max_tokens cap below). Longer jobs
        # split into 32-scene chunks that the worker pool answers in
        # parallel, so wall time stays ~one chunk. A failed chunk falls
        # back to mechanical rows and the resume re-plans only those.
        chunk_size = max(3, min(32, chunk_size))
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
                # Queries are the foundation of the whole video: they get a
                # full agent round-trip per wave, not a 120s cut-off. An agent
                # answer needs the hook wake (~4.5 min) plus composition, so
                # a 120s wave expired before the first answer arrived and the
                # chunk was filled with mechanical word-salad queries.
                per_wave = None
                if getattr(getattr(self, "_client", None), "provider", "") == "external_agent":
                    try:
                        per_wave = max(60.0, float(os.environ.get("VIDZEO_AGENT_MIN_WALL_SECONDS", "3600") or 3600))
                    except (TypeError, ValueError):
                        per_wave = 3600.0
                deadline_monotonic = time.monotonic() + query_planning_budget(len(chunks), worker_limit, per_wave_seconds=per_wave)

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
                    # An empty plan is not a plan: the whole chunk would
                    # fall back to mechanical rows. minItems lets the
                    # worker fix an empty answer instead of losing the chunk.
                    "minItems": 1,
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
                # Bulk output budget: ~260 tokens per scene plan, so a
                # 32-scene chunk needs ~8k tokens; the old 7000 cap would
                # truncate a full bulk plan (and a truncated plan fails
                # validation, losing the whole chunk to mechanical rows).
                max_tokens=max(2400, min(12000, 900 + (len(scene_texts) * 550))),
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
                    # Retrieval contract: a row with empty domain/intents,
                    # a format-phrase subject, a wrong subject type, or
                    # queries carrying no entity/viewpoint words fails the
                    # whole chunk to mechanical re-plan — it is never
                    # shipped with empty fields. The game check is
                    # scene-relative: the scene's own narration (not the
                    # video title) decides which game the queries must name.
                    contract_error = _directed_scene_row_error(
                        scene,
                        scene_text=scene_texts[index - 1] if index - 1 < len(scene_texts) else "",
                        title=topic,
                    )
                    if contract_error:
                        raise ValueError(
                            f"OpenRouter returned an invalid scene plan at row {index}: {contract_error}")
                # Stamp the scene-relative subject game, derived from each
                # scene's own narration (never the LLM's say-so alone), so
                # the adapter, the cache, and the relevance gate all see
                # which game this scene's queries must seek. Stamped only
                # when a game is actually derived: stamping an empty
                # "scene_subject_game": "" onto every row would change the
                # returned rows' shape for scenes with no game at all.
                for index, scene in enumerate(scenes):
                    if isinstance(scene, dict):
                        game = scene_subject_game_for_scene(
                            scene_texts[index] if index < len(scene_texts) else "", topic)
                        if game["key"]:
                            scene["scene_subject_game"] = game["key"]
                            scene["scene_subject_games"] = game_keys_named_in_text(
                                scene_texts[index] if index < len(scene_texts) else "")
                # Plan-level contract: no two scenes share a primary query
                # and every scene's primaries carry a token distinctive to
                # its own narration. A duplicate/generic plan fails the
                # whole chunk, like a bad row.
                plan_errors = validate_scene_query_plan(scenes, scene_texts, topic)
                if plan_errors:
                    first = plan_errors[0]
                    raise ValueError(
                        f"OpenRouter returned an invalid scene plan at row {first['scene_number']}: {first['error']}")
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
