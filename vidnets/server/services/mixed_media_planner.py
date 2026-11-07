from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import os
import re
from typing import Dict, List


# Source shares are retrieval preferences. Actual relevance owns the final edit.
AUTO_YOUTUBE_RATIO = 0.55
AUTHENTICATED_MIXED_YOUTUBE_RATIO = 0.60
AUTO_STOCK_RATIO = 0.20

# Niche-Specific Dynamic Mixed Media Weights
_GAMING_MIXED_MEDIA_WEIGHTS = {
    "Web Images": 0.65,
    "AI Images": 0.00,
    "Stock Media": 0.00,
    "YouTube Clips": 0.35,
}

_ACTION_SPORTS_MIXED_MEDIA_WEIGHTS = {
    "Web Images": 0.70,
    "AI Images": 0.00,
    "Stock Media": 0.00,
    "YouTube Clips": 0.30,
}

_FITNESS_LIFESTYLE_MIXED_MEDIA_WEIGHTS = {
    "Web Images": 0.80,
    "AI Images": 0.00,
    "Stock Media": 0.00,
    "YouTube Clips": 0.20,
}

_DOCUMENTARY_MIXED_MEDIA_WEIGHTS = {
    "Web Images": 0.80,
    "AI Images": 0.00,
    "Stock Media": 0.00,
    "YouTube Clips": 0.20,
}

_GENERAL_MIXED_MEDIA_WEIGHTS = {
    "Web Images": 0.80,
    "AI Images": 0.00,
    "Stock Media": 0.00,
    "YouTube Clips": 0.20,
}

DEFAULT_MIXED_MEDIA_WEIGHTS = dict(_GENERAL_MIXED_MEDIA_WEIGHTS)
_LIFESTYLE_MIXED_MEDIA_WEIGHTS = dict(_FITNESS_LIFESTYLE_MIXED_MEDIA_WEIGHTS)
_SIMPLE_MIXED_MEDIA_WEIGHTS = {
    "Web Images": 0.80,
    "AI Images": 0.00,
    "Stock Media": 0.00,
    "YouTube Clips": 0.20,
}
_AUTO_MOTION_MEDIA_WEIGHTS = dict(_ACTION_SPORTS_MIXED_MEDIA_WEIGHTS)

# Automatic source profiles. These are editorial defaults, not hard overrides
# for explicit UI percentages. Web imagery supplies the majority of scene
# coverage; verified YouTube excerpts add motion accents where they help.
_NICHE_MEDIA_PROFILES = {
    "royal": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "gaming": dict(_GAMING_MIXED_MEDIA_WEIGHTS),
    "sports": dict(_ACTION_SPORTS_MIXED_MEDIA_WEIGHTS),
    "action": dict(_ACTION_SPORTS_MIXED_MEDIA_WEIGHTS),
    "crime": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "history": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "documentary": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "disaster": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "technology": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "science": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "finance": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    # Lifestyle/food/health is mostly clean b-roll and product imagery. Keep
    # the established 80/20 web-to-video behavior for compatibility.
    "lifestyle": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "travel": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    # Global-topic profiles keep retrieval useful outside the original
    # gaming/documentary presets. They are discovery preferences; every
    # candidate still passes the shared identity/visual gates.
    # Stock is an emergency fallback, never a planned automatic mix share.
    "politics": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "education": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "entertainment": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "music": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "fashion": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "environment": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "culture": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "religion": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "law": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "health": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "food": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "business": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "economics": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "literature": {"Web Images": 0.80, "AI Images": 0.00, "Stock Media": 0.00, "YouTube Clips": 0.20},
    "general": dict(_GENERAL_MIXED_MEDIA_WEIGHTS),
}

# Adaptive starting points: archival stories need exact evidence; movement and
# mechanisms need footage. Stock is illustrative, never proof of a named event.
def _media_share(web, youtube, stock):
    return {"Web Images": web, "YouTube Clips": youtube, "Stock Media": stock, "AI Images": 0.0}

_NICHE_MEDIA_PROFILES.update({
    "crime": _media_share(.30, .55, .15), "history": _media_share(.45, .45, .10),
    "royal": _media_share(.30, .65, .05), "documentary": _media_share(.30, .60, .10),
    "sports": _media_share(.20, .75, .05), "action": _media_share(.20, .75, .05),
    "gaming": _media_share(.15, .80, .05), "travel": _media_share(.20, .55, .25),
    "technology": _media_share(.30, .60, .10), "science": _media_share(.35, .55, .10),
    "aviation": _media_share(.25, .65, .10), "maritime": _media_share(.30, .60, .10),
    "automotive": _media_share(.20, .70, .10), "engineering": _media_share(.30, .60, .10),
    "biography": _media_share(.40, .55, .05), "politics": _media_share(.30, .65, .05),
    "business": _media_share(.35, .55, .10), "finance": _media_share(.35, .55, .10),
    "food": _media_share(.30, .60, .10), "architecture": _media_share(.30, .60, .10),
    "education": _media_share(.35, .55, .10), "health": _media_share(.35, .55, .10),
    "lifestyle": _media_share(.30, .60, .10), "general": _media_share(.25, .55, .20),
    "disaster": _media_share(.35, .60, .05), "environment": _media_share(.30, .60, .10),
    "entertainment": _media_share(.30, .65, .05), "music": _media_share(.30, .65, .05),
})
_GAMING_MIXED_MEDIA_WEIGHTS = dict(_NICHE_MEDIA_PROFILES["gaming"])
_ACTION_SPORTS_MIXED_MEDIA_WEIGHTS = dict(_NICHE_MEDIA_PROFILES["sports"])
_DOCUMENTARY_MIXED_MEDIA_WEIGHTS = dict(_NICHE_MEDIA_PROFILES["documentary"])
_GENERAL_MIXED_MEDIA_WEIGHTS = dict(_NICHE_MEDIA_PROFILES["general"])
_FITNESS_LIFESTYLE_MIXED_MEDIA_WEIGHTS = dict(_NICHE_MEDIA_PROFILES["lifestyle"])
_LIFESTYLE_MIXED_MEDIA_WEIGHTS = dict(_FITNESS_LIFESTYLE_MIXED_MEDIA_WEIGHTS)
_AUTO_MOTION_MEDIA_WEIGHTS = dict(_ACTION_SPORTS_MIXED_MEDIA_WEIGHTS)
_SIMPLE_MIXED_MEDIA_WEIGHTS = dict(_FITNESS_LIFESTYLE_MIXED_MEDIA_WEIGHTS)
DEFAULT_MIXED_MEDIA_WEIGHTS = dict(_GENERAL_MIXED_MEDIA_WEIGHTS)

# Retrieval/editorial knobs stay separate from source percentages.  They make
# the same script-led policy behave differently for a sparse gaming search than
# for a well-covered history or travel topic, without letting the LLM invent
# unbounded concurrency or clip length.
_NICHE_RETRIEVAL_POLICIES = {
    "royal": {
        "web_max_candidate_downloads": 24,
        "yt_download_candidate_limit": 4,
        "yt_max_download_attempts_per_scene": 4,
        "mixed_yt_clip_max_seconds": 8.0,
        "max_still_hold_seconds": 3.2,
        "max_visual_beat_seconds": 3.1,
        "intro_media_max_shots": 7,
    },
    "gaming": {
        "web_max_candidate_downloads": 18,
        "yt_download_candidate_limit": 10,
        "yt_max_download_attempts_per_scene": 6,
        "mixed_yt_clip_max_seconds": 7.0,
        "max_still_hold_seconds": 2.8,
        "max_visual_beat_seconds": 2.6,
        "intro_media_max_shots": 8,
    },
    "sports": {
        "web_max_candidate_downloads": 24,
        "yt_download_candidate_limit": 6,
        "yt_max_download_attempts_per_scene": 5,
        "mixed_yt_clip_max_seconds": 7.0,
        "max_still_hold_seconds": 2.8,
        "max_visual_beat_seconds": 2.6,
        "intro_media_max_shots": 8,
    },
    "action": {
        "web_max_candidate_downloads": 24,
        "yt_download_candidate_limit": 5,
        "yt_max_download_attempts_per_scene": 5,
        "mixed_yt_clip_max_seconds": 7.0,
        "max_still_hold_seconds": 3.0,
        "max_visual_beat_seconds": 2.8,
        "intro_media_max_shots": 8,
    },
    "crime": {
        "web_max_candidate_downloads": 22,
        "yt_download_candidate_limit": 5,
        "yt_max_download_attempts_per_scene": 4,
        "mixed_yt_clip_max_seconds": 8.0,
        "max_still_hold_seconds": 3.0,
        "max_visual_beat_seconds": 3.0,
        "intro_media_max_shots": 7,
    },
    "history": {
        "web_max_candidate_downloads": 22,
        "yt_download_candidate_limit": 4,
        "yt_max_download_attempts_per_scene": 4,
        "mixed_yt_clip_max_seconds": 8.0,
        "max_still_hold_seconds": 3.5,
        "max_visual_beat_seconds": 3.4,
        "intro_media_max_shots": 6,
    },
    "politics": {
        "web_max_candidate_downloads": 22,
        "yt_download_candidate_limit": 4,
        "yt_max_download_attempts_per_scene": 4,
        "mixed_yt_clip_max_seconds": 8.0,
        "max_still_hold_seconds": 3.4,
        "max_visual_beat_seconds": 3.2,
        "intro_media_max_shots": 7,
    },
    "general": {
        "web_max_candidate_downloads": 20,
        "yt_download_candidate_limit": 4,
        "yt_max_download_attempts_per_scene": 4,
        "mixed_yt_clip_max_seconds": 8.0,
        "max_still_hold_seconds": 3.5,
        "max_visual_beat_seconds": 3.4,
        "intro_media_max_shots": 6,
    },
}
_NICHE_ALIASES = {
    "royalty": "royal", "royal family": "royal", "monarchy": "royal", "british monarchy": "royal",
    "game": "gaming", "games": "gaming", "video games": "gaming", "esports": "gaming",
    "sport": "sports", "football": "sports", "cricket": "sports", "cars": "automotive",
    "true crime": "crime", "criminal justice": "crime", "historical": "history",
    "serious": "documentary", "disaster documentary": "disaster",
    "news documentary": "documentary", "tech": "technology", "wellness": "lifestyle",
    "food": "food", "cooking": "food", "health": "health", "medicine": "health",
    "political": "politics", "government": "politics", "election": "politics", "elections": "politics",
    "school": "education", "learning": "education", "academic": "education",
    "showbiz": "entertainment", "film": "entertainment", "films": "entertainment", "movies": "entertainment",
    "song": "music", "songs": "music", "musician": "music", "concert": "music",
    "fashion design": "fashion", "climate": "environment", "ecology": "environment",
    "arts": "culture", "cultural": "culture", "heritage": "culture", "faith": "religion",
    "religious": "religion", "legal": "law", "corporate": "business", "markets": "economics",
    "economy": "economics", "books": "literature", "poetry": "literature",
}
_GTA_VI_POLICY_RE = re.compile(
    r"\b(?:gta\s*(?:vi|6)|gta\s+six|grand\s+theft\s+auto\s*(?:vi|6))\b",
    re.IGNORECASE,
)



_ZERO_WEIGHT_EPSILON = 0.0001
_SOURCE_ORDER = tuple(DEFAULT_MIXED_MEDIA_WEIGHTS.keys())

_FAMILY_PERSON = "person_primary"
_FAMILY_EVENT = "event_primary"
_FAMILY_PLACE = "place_primary"
_FAMILY_THEME = "object_or_theme_primary"

_TYPE_STANDARD = "standard"
_TYPE_EXPLANATION = "explanation"
_TYPE_DIAGRAM = "diagram"
_TYPE_INTERVIEW = "interview"
_TYPE_ABSTRACT_CONCEPT = "abstract_concept"

_EVENT_TERMS = {
    "attack", "fight", "fights", "clash", "riot", "protest", "explosion",
    "summit", "ceremony", "match", "final", "semifinal", "knockout",
    "moment", "earthquake", "flood", "wildfire", "crash", "9/11",
}
_PLACE_TERMS = {
    "city", "street", "streets", "skyline", "landmark", "drone", "aerial",
    "harbor", "harbour", "port", "downtown", "airport", "temple", "beach",
    "mountain", "river", "village", "town", "tour",
}
_PERSON_TERMS = {
    "president", "prime minister", "minister", "leader", "leaders",
    "trump", "biden", "obama", "putin", "xi", "netanyahu", "modi",
    "speech", "remarks", "meeting", "signing", "rally", "arrival",
    "arrives", "arriving", "walking", "campaign",
}
_THEME_TERMS = {
    "factory", "cargo", "ship", "ships", "warehouse", "grocery", "shelves",
    "chip", "chips", "car", "cars", "robot", "assembly line", "container",
    "containers", "store", "shopping", "industrial", "office", "product",
}
_EXPLANATION_TERMS = {
    "how it works", "how does", "mechanism", "process", "workflow",
    "explained", "overview", "introduction to", "background",
    "functions", "operation", "principles", "system", "structure",
    "method", "technique", "approach", "strategy", "theory",
    "concept", "framework", "foundation", "basis", "fundamentals",
    "what is", "meaning", "definition", "explanation", "purpose",
}
_DIAGRAM_TERMS = {
    "diagram", "schematic", "blueprint", "cross section", "anatomy",
    "architecture", "layout", "structure", "design", "parts",
    "components", "assembly", "mechanism", "internal", "cutaway",
    "exploded view", "3d model", "visualization", "wireframe",
    "flowchart", "circuit", "infrastructure",
}
_INTERVIEW_TERMS = {
    "interview", "interviewed", "speaks about", "discusses", "discussion",
    "conversation", "qa", "q&a", "fireside chat", "panel", "roundtable",
    "talks to", "talk with", "in conversation", "one-on-one",
}
_ABSTRACT_TERMS = {
    "concept", "idea", "philosophy", "theory", "abstract", "notion",
    "thought", "belief", "ideology", "principle", "ethics", "morality",
    "consciousness", "perception", "awareness", "imagination",
}

# Gaming and gameplay specific terms (web-led with a small verified motion lane):
_GAMING_TOPIC_TERMS = {
    "game", "games", "gameplay", "gaming", "gamer", "gamers", "gta", "gta vi", "gta 6", "grand theft auto",
    "fortnite", "minecraft", "roblox", "call of duty", "warzone", "cyberpunk", "ps5", "playstation",
    "xbox", "nintendo", "switch", "zelda", "pokemon", "elden ring", "dark souls", "valorant",
    "league of legends", "apex legends", "overwatch", "csgo", "counter strike", "fifa", "ea fc",
    "nba 2k", "esports", "speedrun", "boss fight", "walkthrough", "mod", "mods", "unreal engine",
    "assassin's creed", "god of war", "red dead redemption", "rdr2", "witcher", "skyrim",
}

# Action, sports, racing, automotive topics (web-led with bounded footage):
_ACTION_TOPIC_TERMS = {
    "car", "cars", "hypercar", "supercar", "bugatti", "ferrari", "lamborghini",
    "porsche", "bmw", "mercedes", "audi", "mclaren", "koenigsegg", "racing", "speed", "drift", "drifting",
    "motorcycle", "bike", "drag race", "f1", "formula 1", "nba", "football", "soccer", "cricket",
    "basketball", "tennis", "ufc", "boxing", "mma", "match", "tournament", "stunt",
    "acceleration", "horsepower", "top speed", "lap record", "rally",
}

# Fitness, exercises, workouts, lifestyle, wellness, routines (80% pristine stock media):
_SIMPLE_TOPIC_TERMS = {
    "food", "foods", "superfood", "superfoods", "fruit", "fruits", "apple", "apples",
    "banana", "bananas", "orange", "oranges", "berry", "berries", "avocado", "salmon",
    "spinach", "tea", "green tea", "coffee", "vegetable", "vegetables", "recipe", "recipes",
    "cooking", "kitchen", "meal", "meals", "nutrition", "diet", "healthy", "health",
    "exercise", "exercises", "workout", "workouts", "fitness", "gym", "bodybuilding", "yoga", "pilates",
    "cardio", "stretching", "calisthenics", "weight loss", "abs", "muscle", "wellness", "meditation",
    "habit", "habits", "productivity", "study", "travel", "vacation", "tourism", "tourist", "hotel", "beach",
    "lifestyle", "routine", "everyday", "daily life", "generic", "fashion", "beauty", "self care", "skincare",
    "home", "shopping", "money", "investing", "finance", "business", "tips", "tricks",
    "life hacks", "nature", "wildlife", "animal", "pets", "dog", "dogs", "cat", "cats",
}

# Strict historical / documentary / crime / disaster topics (authentic web images first):
_SERIOUS_TOPIC_TERMS = {
    "documentary", "documentaries", "history", "historical", "historian", "titanic", "crime", "criminal",
    "murder", "killing", "investigation", "police", "court", "trial",
    "prison", "forensic", "evidence", "war", "battle", "conflict",
    "disaster", "tragedy", "earthquake", "earthquakes", "flood", "wildfire", "tsunami", "hurricane", "tornado", "explosion",
    "archaeology", "archaeological", "pharaoh", "ancient", "pyramid", "pyramids",
    "bermuda triangle", "genocide", "terrorism", "assassination", "cold war",
    "apollo", "sputnik", "moon landing", "biography", "venezuela",
}
_ROYAL_TOPIC_TERMS = {
    "royal family", "british royal", "royalty", "monarchy", "monarch", "windsor",
    "king charles", "queen camilla", "prince william", "prince harry",
    "buckingham palace", "duke of sussex",
}
_CRIME_TOPIC_TERMS = {
    "crime", "criminal", "murder", "killing", "investigation", "police",
    "court", "trial", "prison", "forensic", "evidence", "detective",
}

# Broad deterministic safety-floor groups. These intentionally use distinctive
# words rather than generic nouns so an arbitrary international title can still
# select an appropriate retrieval mix when the LLM is unavailable.
_GLOBAL_TOPIC_GROUPS = {
    "politics": {"politics", "political", "election", "elections", "president", "parliament", "government", "senate", "congress", "minister", "policy", "diplomacy", "geopolitics"},
    "education": {"education", "school", "university", "college", "student", "students", "teacher", "teaching", "lecture", "learning", "curriculum", "exam", "academic"},
    "music": {"music", "song", "songs", "singer", "musician", "album", "concert", "band", "rapper", "orchestra", "composer"},
    "entertainment": {"entertainment", "actor", "actress", "film", "films", "movie", "movies", "cinema", "television", "tv series", "celebrity", "hollywood", "bollywood"},
    "fashion": {"fashion", "designer", "runway", "couture", "clothing", "dress", "modeling", "outfit", "sneakers", "textile"},
    "environment": {"environment", "climate", "climate change", "ecology", "conservation", "carbon", "emissions", "renewable", "sustainability", "deforestation", "biodiversity"},
    "culture": {"culture", "cultural", "heritage", "tradition", "festival", "museum", "art history", "folklore", "architecture", "anthropology"},
    "religion": {"religion", "religious", "faith", "church", "mosque", "temple", "bible", "quran", "hinduism", "christianity", "islam", "judaism", "buddhism"},
    "law": {"law", "legal", "lawsuit", "court ruling", "legislation", "attorney", "lawyer", "judge", "constitution", "regulation", "rights"},
    "health": {"health", "medicine", "medical", "doctor", "disease", "diagnosis", "treatment", "hospital", "symptom", "public health", "mental health", "vaccine"},
    "food": {"food", "cooking", "recipe", "cuisine", "restaurant", "chef", "ingredient", "nutrition", "baking", "dish", "meal"},
    "business": {"business", "company", "startup", "corporate", "entrepreneur", "ceo", " ಉದ್ಯోగ", "marketing", "product launch", "industry"},
    "economics": {"economics", "economy", "economic", "inflation", "interest rates", "gdp", "unemployment", "markets", "stock market", "trade", "recession"},
    "literature": {"literature", "book", "books", "novel", "novelist", "poetry", "poem", "author", "writer", "fiction", "literary"},
    "science": {"science", "scientist", "research", "experiment", "physics", "chemistry", "biology", "astronomy", "space", "laboratory", "discovery"},
    "technology": {"technology", "software", "hardware", "artificial intelligence", "ai", "robot", "computer", "smartphone", "internet", "cybersecurity", "programming"},
}



@dataclass
class SourceScore:
    source: str
    quality_score: float
    latency_score: float
    total_score: float
    notes: List[str] = field(default_factory=list)


@dataclass
class SceneMediaDecision:
    scene_index: int
    scene_text: str
    scene_family: str
    requested_source: str
    assigned_source: str
    fallback_sources: List[str] = field(default_factory=list)
    content_type: str = _TYPE_STANDARD
    source_scores: List[SourceScore] = field(default_factory=list)
    quality_score: float = 0.0
    latency_score: float = 0.0
    router_score: float = 0.0
    attempted_sources: List[str] = field(default_factory=list)
    rebalanced_early: bool = False
    fallback_reason: str = ""
    resolved_source: str = ""
    final_source: str = ""
    # V2 automatic mode treats these as ordered discovery preferences rather
    # than a hard percentage allocation. ``assigned_source`` remains for v1
    # accounting compatibility until the orchestrator consumes this field.
    preferred_sources: List[str] = field(default_factory=list)
    primary_discovery_sources: List[str] = field(default_factory=list)
    stock_allowed: bool = False
    source_strategy: str = "quota"


@dataclass
class AdaptiveMixedMediaPlan:
    requested_weights: Dict[str, float]
    target_counts: Dict[str, int]
    final_counts: Dict[str, int]
    grouped_indices: Dict[str, List[int]]
    decisions: List[SceneMediaDecision]
    topic_policy: str = "general"
    discovery_indices: Dict[str, List[int]] = field(default_factory=dict)
    source_strategy: str = "quota"
    media_policy: Dict[str, object] = field(default_factory=dict)


def normalize_niche_label(value: str) -> str:
    raw = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    raw = _NICHE_ALIASES.get(raw, raw)
    return raw if raw in _NICHE_MEDIA_PROFILES else "general"


def _gta_vi_story(title: str, script: str, scene_texts: List[str] | None = None) -> bool:
    return bool(_GTA_VI_POLICY_RE.search(" ".join(
        [str(title or ""), str(script or "")]
        + [str(scene or "") for scene in (scene_texts or [])]
    )))


def _parse_niche_llm_payload(raw: str) -> Dict[str, object]:
    import json
    try:
        payload = json.loads(str(raw or ""))
    except Exception:
        text = str(raw or "")
        start, end = text.find("{"), text.rfind("}")
        try:
            payload = json.loads(text[start:end + 1]) if start >= 0 and end > start else {}
        except Exception:
            payload = {}
    return dict(payload) if isinstance(payload, dict) else {}


def resolve_media_policy(
    title: str = "",
    script: str = "",
    scene_texts: List[str] | None = None,
    category: str = "",
    *,
    openrouter_api_key: str = "",
    llm_model: str = "",
    llm_analysis: Dict[str, object] | None = None,
) -> Dict[str, object]:
    """Resolve a bounded, auditable niche policy for automatic Mixed Media.

    OpenRouter supplies a classification/evidence suggestion when available;
    deterministic terms remain the safety floor. Explicit UI weights are
    handled by the caller and are never overridden by this policy.
    """
    combined = " ".join([str(category or ""), str(title or ""), str(script or "")] + [str(s or "") for s in (scene_texts or [])])
    raw_heuristic = classify_mixed_media_topic(title, script, scene_texts, category)
    heuristic = {
        "serious": "documentary",
        "simple": "lifestyle",
    }.get(raw_heuristic, normalize_niche_label(raw_heuristic))
    analysis = dict(llm_analysis or {})
    llm_used = bool(analysis)
    if not analysis and str(openrouter_api_key or "").strip():
        try:
            from services.openrouter_llm import OpenRouterLLM
            prompt = f"""TITLE:\n{title}\n\nSCRIPT:\n{str(script or '')[:12000]}\n\nClassify the script for media planning. Return JSON only:\n{{\"niche\":\"royal|gaming|sports|crime|history|documentary|disaster|technology|science|finance|lifestyle|travel|politics|education|entertainment|music|fashion|environment|culture|religion|law|health|food|business|economics|literature|aviation|maritime|automotive|biography|engineering|architecture|general\",\"confidence\":0.0,\"evidence_terms\":[\"exact words copied from script\"],\"visual_strategy\":\"one short sentence\"}}\nRules: choose royal for a royal family, monarchy, Windsor, King Charles, Queen Camilla, Prince William, or Prince Harry story, even when the script includes historical dates. Choose gaming when the story is about a video game, gameplay, game trailer, or esports. Choose crime/history/documentary for evidence-led real-world stories. Choose the most specific global niche supported by multiple script terms. Never classify from the title alone when the script contradicts it. Also return media_weights with numeric Web Images, YouTube Clips and Stock Media shares totaling 1, and media_reason explaining scene needs. Favor exact archival evidence for named people/events; favor moving footage for actions and mechanisms. Stock may illustrate context only, never impersonate a named person/event. Documentary sources can contribute multiple verified distant timestamps; do not require one download per scene."""
            prompt += "\nTreat the whole film as a media edit: allocate YouTube footage to visible actions, mechanisms, places and archival events, rather than defaulting to 80% stills. Sports/gaming/action often need 65-80% footage; a well-covered documentary or crime investigation often benefits from 45-65%. These are starting points, not quotas: retain exact still documents, maps and portraits where they carry evidence. Preserve images for unfilmed events or unidentified people, and use clearly illustrative stock for atmosphere. Name the reason if footage should be sparse. A clip is usable only after its exact cut passes identity and frame checks; a topical title is not enough."
            llm = OpenRouterLLM(api_key=str(openrouter_api_key).strip(), model=llm_model or "qwen/qwen3.5-flash-02-23")
            raw = llm.chat(
                prompt,
                system_prompt="You are a senior documentary media allocator. Be conservative and evidence-grounded.",
                temperature=0.0,
                max_tokens=480,
                json_mode=True,
                timeout_seconds=20.0,
                max_attempts=1,
                total_timeout_seconds=25.0,
            )
            analysis = _parse_niche_llm_payload(raw)
            llm_used = bool(analysis)
        except Exception as exc:
            analysis = {"error": f"llm_unavailable:{type(exc).__name__}"}
    proposed = normalize_niche_label(str(analysis.get("niche") or heuristic))
    try:
        confidence = float(analysis.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    evidence = analysis.get("evidence_terms") or []
    if isinstance(evidence, str):
        evidence = [evidence]
    evidence = [str(item).strip() for item in evidence if str(item).strip()]
    evidence_hits = sum(1 for term in evidence if len(term) >= 3 and term.lower() in combined.lower())
    llm_is_supported = bool(llm_used and confidence >= 0.45 and (evidence_hits or proposed == heuristic))
    # Preserve the deterministic royal guard even when the model sees dates,
    # succession, or the word "history" and proposes an archive niche.
    royal_context = raw_heuristic == "royal" or _contains_topic_term(combined, _ROYAL_TOPIC_TERMS)
    niche = "royal" if royal_context else (proposed if llm_is_supported else heuristic)
    gta_vi = _gta_vi_story(title, script, scene_texts)
    if gta_vi:
        niche = "gaming"
    weights = _raise_automatic_youtube_floor(
        dict(_NICHE_MEDIA_PROFILES.get(niche, _NICHE_MEDIA_PROFILES["general"])),
        niche=niche,
    )
    suggested_weights = analysis.get("media_weights")
    allocation_used = False
    if llm_is_supported and isinstance(suggested_weights, dict):
        try:
            proposed_weights = {source: float(suggested_weights.get(source, 0)) for source in _SOURCE_ORDER}
            if all(0 <= value <= 1 for value in proposed_weights.values()) and .95 <= sum(proposed_weights.values()) <= 1.05 and proposed_weights.get("AI Images", 0) == 0:
                weights = _raise_automatic_youtube_floor(proposed_weights, niche=niche)
                allocation_used = True
        except (TypeError, ValueError):
            pass
    retrieval_policy = dict(
        _NICHE_RETRIEVAL_POLICIES.get(
            niche,
            _NICHE_RETRIEVAL_POLICIES["general"],
        )
    )
    # Keep the decision auditable and prevent a reused job config from silently
    # applying yesterday's niche to a new script/title.
    import hashlib
    policy_context = "\n".join(
        [str(category or ""), str(title or ""), str(script or "")]
        + [str(scene or "") for scene in (scene_texts or [])]
    )
    return {
        "version": "niche-policy-v3",
        "llm_allocation_used": allocation_used,
        "media_reason": str(analysis.get("media_reason") or "script-grounded niche preferences")[:400],
        "niche": niche,
        "heuristic_niche": heuristic,
        "weights": weights,
        "gta_vi": gta_vi,
        "gta_policy": "gta_vi_exact_title_official_first; prior_title_only_for_scripted_scene_comparison" if gta_vi else "standard_game_or_topic_sources",
        "llm_used": llm_used,
        "llm_supported": llm_is_supported,
        "confidence": round(confidence if llm_is_supported else 0.5, 3),
        "evidence_terms": evidence[:8],
        "visual_strategy": str(analysis.get("visual_strategy") or "")[:240],
        "retrieval_policy": retrieval_policy,
        "context_hash": hashlib.sha256(policy_context.encode("utf-8", errors="ignore")).hexdigest()[:24],
    }


def normalize_mixed_media_weights(raw_weights=None, strict: bool = False) -> Dict[str, float]:
    weights = {}
    has_explicit_weights = isinstance(raw_weights, dict) and any(
        source in raw_weights for source in _SOURCE_ORDER
    )
    if has_explicit_weights:
        for source in _SOURCE_ORDER:
            try:
                value = float(raw_weights.get(source, 0))
            except (TypeError, ValueError):
                value = 0.0
            weights[source] = max(0.0, value)
    else:
        weights = dict(DEFAULT_MIXED_MEDIA_WEIGHTS)

    weights = {
        source: (0.0 if value <= _ZERO_WEIGHT_EPSILON else value)
        for source, value in weights.items()
    }
    total = sum(weights.values())
    if total <= 0:
        if strict and has_explicit_weights:
            raise ValueError("Mixed Media requires at least one enabled media source.")
        return dict(DEFAULT_MIXED_MEDIA_WEIGHTS)
    return {source: value / total for source, value in weights.items()}


def _contains_topic_term(text: str, terms) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    if not normalized:
        return False
    return any(
        term in normalized
        if " " in term
        else bool(re.search(rf"\b{re.escape(term)}\b", normalized))
        for term in terms
    )


def classify_mixed_media_topic(
    title: str = "",
    script: str = "",
    scene_texts: List[str] | None = None,
    category: str = "",
) -> str:
    """Classify the broad source policy without another network call."""
    # An explicit category from the user/scene brain is authoritative. This is
    # important for ambiguous titles such as "Apple" (food vs. technology)
    # and "Harry" (a person vs. a fictional character).
    explicit = normalize_niche_label(category)
    if explicit != "general":
        return explicit
    from .editing_profile import classify_niche
    shared_niche = classify_niche(title, category, script or " ".join(scene_texts or []))
    if shared_niche in _NICHE_MEDIA_PROFILES and shared_niche != "general":
        return shared_niche
    context = " ".join(
        [str(category or ""), str(title or ""), str(script or "")]
        + [str(scene or "") for scene in (scene_texts or [])]
    )
    if _contains_topic_term(context, _GAMING_TOPIC_TERMS):
        return "gaming"
    if _contains_topic_term(context, _ACTION_TOPIC_TERMS):
        return "action"
    if _contains_topic_term(context, _CRIME_TOPIC_TERMS):
        return "crime"
    # Royal-family stories often mention dates, succession, and history. Keep
    # those supporting words from downgrading the primary niche to history.
    if _contains_topic_term(context, _ROYAL_TOPIC_TERMS):
        return "royal"
    if _contains_topic_term(context, _SERIOUS_TOPIC_TERMS):
        return "serious"
    for niche, terms in _GLOBAL_TOPIC_GROUPS.items():
        if _contains_topic_term(context, terms):
            return niche
    if _contains_topic_term(context, _SIMPLE_TOPIC_TERMS):
        return "simple"
    return "general"


def automatic_mixed_media_weights(
    title: str = "",
    script: str = "",
    scene_texts: List[str] | None = None,
    category: str = "",
) -> Dict[str, float]:
    """Return the automatic, topic-aware Mixed Media source policy."""
    topic = classify_mixed_media_topic(
        title=title,
        script=script,
        scene_texts=scene_texts,
        category=category,
    )
    if topic == "gaming":
        return _raise_automatic_youtube_floor(dict(_GAMING_MIXED_MEDIA_WEIGHTS), niche="gaming")
    if topic == "action":
        return _raise_automatic_youtube_floor(dict(_ACTION_SPORTS_MIXED_MEDIA_WEIGHTS), niche="action")
    if topic == "serious":
        return _raise_automatic_youtube_floor(dict(_DOCUMENTARY_MIXED_MEDIA_WEIGHTS), niche="serious")
    if topic == "simple":
        return _raise_automatic_youtube_floor(dict(_SIMPLE_MIXED_MEDIA_WEIGHTS), niche="simple")
    if topic == "lifestyle":
        return _raise_automatic_youtube_floor(dict(_FITNESS_LIFESTYLE_MIXED_MEDIA_WEIGHTS), niche="lifestyle")
    if topic in _NICHE_MEDIA_PROFILES:
        return _raise_automatic_youtube_floor(dict(_NICHE_MEDIA_PROFILES[topic]), niche=topic)
    return _raise_automatic_youtube_floor(dict(_GENERAL_MIXED_MEDIA_WEIGHTS))


def _raise_automatic_youtube_floor(
    weights: Dict[str, float],
    niche: str = "",
    *,
    authenticated_youtube: bool = False,
) -> Dict[str, float]:
    """Cap automatic YouTube at the niche-appropriate motion limit.

    For gaming, gameplay, and video-heavy niches where web images are sparse,
    YouTube clips provide majority coverage (up to 75-80%). Factual, history,
    documentary, and royal niches can use substantial archival footage with
    scene-specific still evidence and contextual stock where appropriate.
    """
    result = {
        source: max(0.0, float(weights.get(source, 0.0) or 0.0))
        for source in _SOURCE_ORDER
    }
    current = result.get("YouTube Clips", 0.0)

    niche_lower = str(niche or "").strip().lower()
    if authenticated_youtube:
        yt_ceiling = 0.85
    elif niche_lower in {"gaming", "game", "esports"}:
        yt_ceiling = 0.85
    elif niche_lower in {"action", "sports"}:
        yt_ceiling = 0.80
    else:
        yt_ceiling = 0.75

    if current > yt_ceiling:
        excess = current - yt_ceiling
        result["YouTube Clips"] = yt_ceiling
        # Web Images is the primary fallback lane.
        result["Web Images"] = result.get("Web Images", 0.0) + excess
    total = sum(result.values())
    if total <= _ZERO_WEIGHT_EPSILON:
        return dict(DEFAULT_MIXED_MEDIA_WEIGHTS)
    return {source: value / total for source, value in result.items()}


def authenticated_youtube_mix(
    weights: Dict[str, float],
    target_ratio: float = AUTHENTICATED_MIXED_YOUTUBE_RATIO,
) -> Dict[str, float]:
    """Raise an automatic mixed-media plan to the authenticated video target.

    This only expresses a retrieval preference. Candidates still have to pass
    the visual relevance gate, and unresolved scenes remain eligible for real
    web-image rescue. Explicit user weights are filtered by the caller and do
    not reach this helper.
    """
    result = normalize_mixed_media_weights(weights, strict=False)
    try:
        target = max(0.0, min(0.85, float(target_ratio)))
    except (TypeError, ValueError):
        target = AUTHENTICATED_MIXED_YOUTUBE_RATIO
    current = float(result.get("YouTube Clips", 0.0) or 0.0)
    target = max(current, target)
    if target <= current + 1e-9:
        return result

    other_total = sum(
        max(0.0, float(value or 0.0))
        for source, value in result.items()
        if source != "YouTube Clips"
    )
    remaining = max(0.0, 1.0 - target)
    if other_total <= _ZERO_WEIGHT_EPSILON:
        result["Web Images"] = remaining
        for source in result:
            if source not in {"Web Images", "YouTube Clips"}:
                result[source] = 0.0
    else:
        for source in result:
            if source != "YouTube Clips":
                result[source] = (
                    max(0.0, float(result.get(source, 0.0) or 0.0))
                    / other_total
                    * remaining
                )
    result["YouTube Clips"] = target
    return result


def _automatic_mode_weights(topic_policy: str = "general") -> Dict[str, float]:
    """Use the current niche policy; explicit percentages are handled separately."""
    niche = {"action": "sports", "serious": "documentary", "simple": "lifestyle"}.get(topic_policy, topic_policy)
    return _raise_automatic_youtube_floor(dict(_NICHE_MEDIA_PROFILES.get(niche, DEFAULT_MIXED_MEDIA_WEIGHTS)), niche=niche)


def _target_counts(num_scenes: int, weights: Dict[str, float]) -> Dict[str, int]:
    enabled_sources = [source for source, weight in weights.items() if weight > _ZERO_WEIGHT_EPSILON]
    if not enabled_sources:
        return {source: 0 for source in _SOURCE_ORDER}
    if len(enabled_sources) == 1:
        only_source = enabled_sources[0]
        return {
            source: (num_scenes if source == only_source else 0)
            for source in _SOURCE_ORDER
        }

    raw_counts = {source: weights[source] * num_scenes for source in _SOURCE_ORDER}
    counts = {source: int(math.floor(value)) for source, value in raw_counts.items()}

    assigned = sum(counts.values())
    remaining = max(0, num_scenes - assigned)
    remainder_order = sorted(
        enabled_sources,
        key=lambda source: (
            -(raw_counts[source] - counts[source]),
            -weights[source],
            _SOURCE_ORDER.index(source),
        ),
    )
    for source in remainder_order[:remaining]:
        counts[source] += 1
    for source in _SOURCE_ORDER:
        if weights.get(source, 0.0) <= _ZERO_WEIGHT_EPSILON:
            counts[source] = 0
    return counts


def _apply_runtime_caps(num_scenes: int, counts: Dict[str, int], weights: Dict[str, float]) -> Dict[str, int]:
    """
    Keep the deterministic plan aligned with runtime branch limits.

    The web resolver is intentionally capped in mixed mode for speed. If the
    planner assigns 100+ scenes to web while the runtime only fetches 16, the
    pipeline fills the rest by nearest-media reuse, which looks duplicated and
    inaccurate. Rebalance that excess before launching branches.
    """
    if num_scenes <= 0:
        return {source: 0 for source in _SOURCE_ORDER}

    adjusted = {source: max(0, int(counts.get(source, 0) or 0)) for source in _SOURCE_ORDER}
    def _redistribute_excess(source: str, excess: int, receivers: List[str]) -> None:
        if excess <= 0:
            return
        receivers = [
            receiver for receiver in receivers
            if receiver != source and (weights.get(receiver, 0.0) > _ZERO_WEIGHT_EPSILON or adjusted.get(receiver, 0) > 0)
        ]
        if not receivers:
            receivers = [receiver for receiver in _SOURCE_ORDER if receiver != source]
        receiver_weight_total = sum(max(weights.get(receiver, 0.0), 0.0) for receiver in receivers)
        if receiver_weight_total <= _ZERO_WEIGHT_EPSILON:
            raw_additions = {receiver: excess / max(1, len(receivers)) for receiver in receivers}
        else:
            raw_additions = {
                receiver: (max(weights.get(receiver, 0.0), 0.0) / receiver_weight_total) * excess
                for receiver in receivers
            }
        additions = {receiver: int(math.floor(raw_additions[receiver])) for receiver in receivers}
        remainder = excess - sum(additions.values())
        order = sorted(
            receivers,
            key=lambda receiver: (
                -(raw_additions[receiver] - additions[receiver]),
                -weights.get(receiver, 0.0),
                _SOURCE_ORDER.index(receiver),
            ),
        )
        for receiver in order[:remainder]:
            additions[receiver] += 1
        for receiver, addition in additions.items():
            adjusted[receiver] = adjusted.get(receiver, 0) + addition

    enabled_sources = [source for source, weight in weights.items() if weight > _ZERO_WEIGHT_EPSILON]
    yt_cap = max(0, int(os.environ.get("VIDZEO_MIXED_YT_SCENE_CAP", "0") or 0))
    if (
        yt_cap
        and adjusted.get("YouTube Clips", 0) > yt_cap
        and len(enabled_sources) >= 2
    ):
        excess = adjusted["YouTube Clips"] - yt_cap
        adjusted["YouTube Clips"] = yt_cap
        # Keep the UI intent, but do not assign more YouTube scenes than the
        # resolver can reliably prove. Extra scenes go to enabled non-YT
        # branches before launch, instead of failing the job after minutes.
        _redistribute_excess(
            "YouTube Clips",
            excess,
            [source for source in ("Stock Media", "Web Images") if weights.get(source, 0.0) > _ZERO_WEIGHT_EPSILON],
        )

    respect_ui_percentages = os.environ.get("VIDZEO_MIXED_RESPECT_UI_PERCENTAGES", "1").strip().lower() in {"1", "true", "yes", "on"}
    if respect_ui_percentages:
        return adjusted

    web_cap = max(0, int(os.environ.get("VIDZEO_MIXED_WEB_SCENE_CAP", "16") or 16))
    if web_cap and adjusted.get("Web Images", 0) > web_cap:
        receivers = [
            source for source in ("Stock Media",)
            if weights.get(source, 0.0) > _ZERO_WEIGHT_EPSILON
        ]
        # Never manufacture Stock Media assignments merely because an
        # optional web cap was configured. Serious topics may have no stock
        # branch at all.
        if receivers:
            excess = adjusted["Web Images"] - web_cap
            adjusted["Web Images"] = web_cap
            _redistribute_excess("Web Images", excess, receivers)

    stock_cap = max(0, int(os.environ.get("VIDZEO_MIXED_STOCK_SCENE_CAP", "64") or 64))
    if stock_cap and adjusted.get("Stock Media", 0) > stock_cap:
        excess = adjusted["Stock Media"] - stock_cap
        adjusted["Stock Media"] = stock_cap
        adjusted["Web Images"] = adjusted.get("Web Images", 0) + excess

    # Mixed mode should not collapse into a one-source plan unless the user
    # explicitly enabled only one source and no cap forced redistribution.
    enabled_sources = [source for source, weight in weights.items() if weight > _ZERO_WEIGHT_EPSILON]
    if len(enabled_sources) >= 2:
        for source in enabled_sources:
            if adjusted.get(source, 0) <= 0 and num_scenes >= len(enabled_sources):
                donor = max(enabled_sources, key=lambda item: adjusted.get(item, 0))
                if adjusted.get(donor, 0) > 1:
                    adjusted[donor] -= 1
                    adjusted[source] = 1

    assigned = sum(adjusted.values())
    if assigned < num_scenes:
        receiver = max(_SOURCE_ORDER, key=lambda source: (weights.get(source, 0.0), adjusted.get(source, 0)))
        adjusted[receiver] += num_scenes - assigned
    elif assigned > num_scenes:
        overflow = assigned - num_scenes
        for source in sorted(_SOURCE_ORDER, key=lambda item: adjusted.get(item, 0), reverse=True):
            if overflow <= 0:
                break
            removable = min(overflow, adjusted.get(source, 0))
            adjusted[source] -= removable
            overflow -= removable

    return adjusted


def _classify_scene_family(scene_text: str, title: str = "", script: str = "") -> str:
    lower = f"{title} {scene_text}".lower()
    if re.search(r"\b(?:19|20)\d{2}\b", lower) or any(term in lower for term in _EVENT_TERMS):
        return _FAMILY_EVENT
    if any(term in lower for term in _PLACE_TERMS):
        return _FAMILY_PLACE
    if any(term in lower for term in _PERSON_TERMS):
        return _FAMILY_PERSON
    if any(term in lower for term in _THEME_TERMS):
        return _FAMILY_THEME
    script_lower = (script or "").lower()
    if any(term in script_lower for term in ("president", "prime minister", "leader", "speech", "rally")):
        return _FAMILY_PERSON
    return _FAMILY_THEME


def _classify_scene_content_type(scene_text: str, title: str = "", script: str = "") -> str:
    lower = f"{title} {scene_text}".lower()
    if any(term in lower for term in _DIAGRAM_TERMS):
        return _TYPE_DIAGRAM
    if any(term in lower for term in _INTERVIEW_TERMS):
        return _TYPE_INTERVIEW
    if any(term in lower for term in _EXPLANATION_TERMS):
        return _TYPE_EXPLANATION
    if any(term in lower for term in _ABSTRACT_TERMS):
        return _TYPE_ABSTRACT_CONCEPT
    return _TYPE_STANDARD


def _base_source_quality(scene_family: str, content_type: str = _TYPE_STANDARD) -> Dict[str, float]:
    base = {"Web Images": 0.78, "AI Images": 0.72, "Stock Media": 0.96}
    if scene_family == _FAMILY_PERSON:
        base = {"Web Images": 0.78, "AI Images": 0.58, "Stock Media": 0.42}
    elif scene_family == _FAMILY_EVENT:
        base = {"Web Images": 0.82, "AI Images": 0.50, "Stock Media": 0.32}
    elif scene_family == _FAMILY_PLACE:
        base = {"Web Images": 0.95, "AI Images": 0.62, "Stock Media": 0.84}
    if content_type == _TYPE_DIAGRAM:
        base["Stock Media"] = max(base.get("Stock Media", 0.0), 0.90)
        base["Web Images"] = max(base.get("Web Images", 0.0), 0.85)
        base["AI Images"] = max(base.get("AI Images", 0.0), 0.88)
    elif content_type == _TYPE_INTERVIEW:
        base["Stock Media"] = min(base.get("Stock Media", 0.0), 0.20)
        base["AI Images"] = min(base.get("AI Images", 0.0), 0.35)
    elif content_type == _TYPE_EXPLANATION:
        base["Web Images"] = max(base.get("Web Images", 0.0), 0.88)
        base["AI Images"] = max(base.get("AI Images", 0.0), 0.82)
    elif content_type == _TYPE_ABSTRACT_CONCEPT:
        base["Stock Media"] = max(base.get("Stock Media", 0.0), 0.85)
        base["Web Images"] = max(base.get("Web Images", 0.0), 0.90)
        base["AI Images"] = max(base.get("AI Images", 0.0), 0.90)
    return base


def _source_latency_score(source: str) -> float:
    return {
        "Web Images": 0.92,
        "AI Images": 0.55,
        "YouTube Clips": 0.62,
        "Stock Media": 0.82,
    }.get(source, 0.50)


def _is_video_source(source: str) -> bool:
    return source in {"YouTube Clips", "Stock Media"}


_MOTION_PREFERENCE_TERMS = {
    "walk", "walking", "run", "running", "drive", "driving", "fly", "flying",
    "launch", "arrive", "arrival", "celebrate", "crowd", "fight", "match",
    "game", "protest", "explosion", "storm", "factory", "assembly", "dance",
    "perform", "speech", "interview", "demonstration", "rescue", "race",
}
_STATIC_EVIDENCE_TERMS = {
    "portrait", "photograph", "photo", "document", "map", "diagram", "chart",
    "graph", "headline", "newspaper", "letter", "location", "building", "logo",
    "screenshot", "date", "identity", "archive photo", "historical photo",
}


def automatic_source_preferences(
    scene_text: str,
    *,
    title: str = "",
    script: str = "",
) -> List[str]:
    """Return relevance-oriented primary lanes for one automatic scene.

    This function intentionally does not look at requested percentages. Both
    primary lanes may discover candidates; the order is only a tie-break when
    both candidates pass the same verification contract.
    """
    scene_family = _classify_scene_family(scene_text, title=title, script=script)
    content_type = _classify_scene_content_type(scene_text, title=title, script=script)
    normalized = re.sub(r"\s+", " ", str(scene_text or "").lower())
    has_motion = any(re.search(rf"\b{re.escape(term)}\w*\b", normalized) for term in _MOTION_PREFERENCE_TERMS)
    has_static_evidence = any(term in normalized for term in _STATIC_EVIDENCE_TERMS)
    identity_intro = bool(re.search(r"\b(?:name (?:was|used|on)|later called|was born|born in|portrait of|known as|who was)\b", normalized))
    if identity_intro and not has_motion:
        return ["Web Images", "YouTube Clips"]
    if content_type in {_TYPE_DIAGRAM, _TYPE_EXPLANATION} or has_static_evidence:
        return ["Web Images", "YouTube Clips"]
    if has_motion or scene_family in {_FAMILY_EVENT, _FAMILY_PERSON} or content_type == _TYPE_INTERVIEW:
        return ["YouTube Clips", "Web Images"]
    return ["Stock Media", "YouTube Clips", "Web Images"] if scene_family in {_FAMILY_THEME, _FAMILY_PLACE} else ["Web Images", "YouTube Clips"]


def _semantic_source_sequence(scene_texts, weights, target_counts, title, script):
    """Place discovery work by beat meaning, with soft film-wide allocation.

    A quota must not send a named portrait or dated incident to anonymous stock.
    Sources still pass identity/vision checks; these are search assignments.
    """
    counts = {source: 0 for source in _SOURCE_ORDER}
    result = []
    niche = classify_mixed_media_topic(title, script)
    first_20_slots = max(1, int(round(len(scene_texts) * 0.20)))
    mid_end = max(first_20_slots + 1, int(round(len(scene_texts) * 0.70)))
    for index, text in enumerate(scene_texts):
        preferences = automatic_source_preferences(text, title=title, script=script)
        if niche in {"gaming", "sports", "royal", "biography", "politics"}:
            preferences = [source for source in preferences if source != "Stock Media"]
        if niche in {"crime", "history", "biography", "politics", "royal"} and re.search(r"\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b", text):
            preferences = [source for source in preferences if source != "Stock Media"]
        enabled = [source for source in preferences if weights.get(source, 0) > _ZERO_WEIGHT_EPSILON]
        if not enabled:
            enabled = [source for source in ("Web Images", "YouTube Clips") if weights.get(source, 0) > _ZERO_WEIGHT_EPSILON]
        if not enabled:
            enabled = [max(weights, key=weights.get)]
        def score(source):
            semantic = (len(preferences) - preferences.index(source)) * .35 if source in preferences else 0
            remaining = (target_counts.get(source, 0) - counts[source]) / max(1, len(scene_texts) - index)
            repeat = .20 if len(result) >= 2 and result[-1] == result[-2] == source else 0
            pacing_bonus = 0.0
            if source == "YouTube Clips":
                if index < first_20_slots:
                    pacing_bonus = 0.45
                elif index < mid_end:
                    mid_yt = sum(1 for s in result[first_20_slots:] if s == "YouTube Clips")
                    mid_scenes = max(1, index - first_20_slots)
                    if mid_yt / mid_scenes < 0.50:
                        pacing_bonus = 0.25
            return semantic + remaining - repeat + pacing_bonus
        chosen = max(enabled, key=score)
        result.append(chosen)
        counts[chosen] += 1
    return result


def _polish_mixed_sequence(sequence: List[str], target_counts: Dict[str, int]) -> List[str]:
    """
    Make mixed media feel edited, not randomly allocated.

    Front-loads YouTube clips in the first 20% of the video (aiming for 80% to 90%
    YouTube clips), paces the mid section with approximately 50% YouTube clips,
    ensures early video hook coverage, and breaks long consecutive runs of the
    same source while strictly preserving target_counts.
    """
    if len(sequence) < 2:
        return sequence

    polished = list(sequence)
    total_len = len(polished)
    available_yt = int(target_counts.get("YouTube Clips", 0) or 0)
    video_sources = [source for source in ("YouTube Clips", "Stock Media") if target_counts.get(source, 0) > 0]
    video_count = sum(int(target_counts.get(source, 0) or 0) for source in video_sources)

    # 1. Video hook and YouTube front-load:
    # If video sources are enabled, ensure early hook slots are video, alternating sources.
    if video_sources:
        video_total = video_count
        non_video_total = max(0, total_len - video_total)
        min_video_after_intro = max(0, (non_video_total - 1) // 2)
        intro_ratio = float(os.environ.get("VIDZEO_MIXED_INTRO_VIDEO_RATIO", "0.45") or 0.45)
        intro_ratio = min(0.75, max(0.20, intro_ratio))
        early_video_slots = max(
            1 if video_total > 0 else 0,
            min(
                video_total,
                max(1, video_total - min_video_after_intro),
                total_len,
                max(3, int(math.ceil(total_len * intro_ratio))),
            ) if video_total >= 3 else video_total,
        )
        preferred = sorted(video_sources, key=lambda s: (0 if s == "YouTube Clips" else 1, -target_counts.get(s, 0)))
        for slot in range(early_video_slots):
            desired = preferred[slot % len(preferred)]
            if polished[slot] == desired:
                continue
            swap_index = next(
                (idx for idx in range(slot + 1, total_len) if polished[idx] == desired),
                None,
            )
            if swap_index is None:
                swap_index = next(
                    (idx for idx in range(slot + 1, total_len) if polished[idx] in video_sources),
                    None,
                )
            if swap_index is not None:
                polished[slot], polished[swap_index] = polished[swap_index], polished[slot]

    # 2. Mid-section pacing: mid section (~20% to ~70%) contains ~50% YouTube clips
    first_20_slots = max(1, int(round(total_len * 0.20)))
    mid_start = first_20_slots
    mid_end = max(mid_start + 1, int(round(total_len * 0.70)))
    if total_len >= 5 and mid_end > mid_start:
        mid_slots = list(range(mid_start, min(mid_end, total_len)))
        current_mid_yt = sum(1 for idx in mid_slots if polished[idx] == "YouTube Clips")
        target_mid_yt = int(round(len(mid_slots) * 0.50))
        unused_yt = [idx for idx in range(min(mid_end, total_len), total_len) if polished[idx] == "YouTube Clips"]
        non_video_post_mid = sum(1 for idx in range(min(mid_end, total_len), total_len) if polished[idx] not in video_sources)
        min_yt_to_keep = (non_video_post_mid - 1) // 2 if "Stock Media" not in video_sources else 0
        safe_unused = max(0, len(unused_yt) - min_yt_to_keep)
        needed = min(target_mid_yt - current_mid_yt, safe_unused)
        if needed > 0 and unused_yt:
            for idx in mid_slots:
                if needed <= 0 or not unused_yt:
                    break
                if polished[idx] != "YouTube Clips":
                    prev1 = polished[idx - 1] if idx > 0 else None
                    prev2 = polished[idx - 2] if idx > 1 else None
                    next1 = polished[idx + 1] if idx + 1 < total_len else None
                    if prev1 == "YouTube Clips" and prev2 == "YouTube Clips":
                        continue
                    if prev1 == "YouTube Clips" and next1 == "YouTube Clips":
                        continue
                    swap_from = unused_yt.pop()
                    polished[idx], polished[swap_from] = polished[swap_from], polished[idx]
                    needed -= 1

    # 3. Break 3+ runs by swapping with a later different source.
    for idx in range(2, len(polished)):
        if polished[idx] != polished[idx - 1] or polished[idx] != polished[idx - 2]:
            continue
        swap_index = next(
            (
                candidate for candidate in range(idx + 1, len(polished))
                if polished[candidate] != polished[idx]
            ),
            None,
        )
        if swap_index is not None:
            polished[idx], polished[swap_index] = polished[swap_index], polished[idx]

    return polished



def _build_exact_source_sequence(num_scenes: int, weights: Dict[str, float], target_counts: Dict[str, int]) -> List[str]:
    enabled_sources = [source for source, count in target_counts.items() if count > 0]
    if not enabled_sources:
        return []

    assigned_counts = {source: 0 for source in _SOURCE_ORDER}
    remaining_counts = dict(target_counts)
    sequence: List[str] = []

    for scene_index in range(num_scenes):
        best_source = None
        best_score = -10**9
        progress_step = scene_index + 1

        for source in enabled_sources:
            if remaining_counts.get(source, 0) <= 0:
                continue

            expected_progress = weights.get(source, 0.0) * progress_step
            deficit = expected_progress - assigned_counts[source]
            remaining_bias = remaining_counts[source] / max(1, num_scenes - scene_index)
            continuity_penalty = 0.04 if sequence and sequence[-1] == source else 0.0
            score = deficit + remaining_bias - continuity_penalty

            if score > best_score:
                best_score = score
                best_source = source

        if best_source is None:
            best_source = max(enabled_sources, key=lambda source: remaining_counts.get(source, 0))

        sequence.append(best_source)
        assigned_counts[best_source] += 1
        remaining_counts[best_source] = max(0, remaining_counts[best_source] - 1)

    return _polish_mixed_sequence(sequence, target_counts)


def build_adaptive_mixed_media_plan(
    scene_texts: List[str],
    weights=None,
    title: str = "",
    script: str = "",
    strict_weights: bool = False,
    category: str = "",
    scene_media_mode: str = "",
    media_policy: Dict[str, object] | None = None,
) -> AdaptiveMixedMediaPlan:
    automatic_mode = str(scene_media_mode or "").strip().lower() in {
        "auto", "automatic", "smart", "topic_aware", "topic-aware",
    }
    # Explicit caller-supplied weights always win, even when a request also set
    # scene_media_mode="auto": the user asked for a concrete split.
    explicit_weights = isinstance(weights, dict) and any(
        source in weights for source in _SOURCE_ORDER
    )
    topic_policy = classify_mixed_media_topic(
        title=title,
        script=script,
        scene_texts=scene_texts,
        category=category,
    )
    policy = dict(media_policy or {})
    policy_niche = normalize_niche_label(str(policy.get("niche") or "")) if policy else "general"
    if policy and policy_niche != "general":
        topic_policy = policy_niche
    if explicit_weights:
        weights = normalize_mixed_media_weights(weights, strict=strict_weights)

    else:
        policy_weights = policy.get("weights") if isinstance(policy.get("weights"), dict) else None
        if policy_weights:
            # The app's niche agent has already validated this profile. Keep
            # the planner's normalization/caps, but do not apply the legacy
            # global YT ratio env override on top of it.
            weights = _raise_automatic_youtube_floor(
                normalize_mixed_media_weights(policy_weights, strict=False),
                niche=topic_policy,
                authenticated_youtube=policy.get("youtube_session_verified") is True,
            )
        elif automatic_mode:
            weights = _automatic_mode_weights(topic_policy=topic_policy)
        else:
            weights = automatic_mixed_media_weights(

                title=title,
                script=script,
                scene_texts=scene_texts,
                category=category,
            )
    num_scenes = len(scene_texts)

    yt_first = (
        explicit_weights
        and str(os.environ.get("VIDZEO_YT_FIRST_MIXED", "0")).strip().lower()
        in {"1", "true", "yes", "on"}
    )
    if yt_first and weights.get("YouTube Clips", 0) > 0:
        target_counts = {source: 0 for source in _SOURCE_ORDER}
        target_counts["YouTube Clips"] = num_scenes
        source_sequence = ["YouTube Clips"] * num_scenes
    else:
        target_counts = _target_counts(num_scenes, weights)
        target_counts = _apply_runtime_caps(num_scenes, target_counts, weights)
        source_sequence = _build_exact_source_sequence(num_scenes, weights, target_counts)
    grouped_indices = {source: [] for source in _SOURCE_ORDER}
    source_strategy = "preference" if automatic_mode and not explicit_weights else "quota"
    if source_strategy == "preference":
        source_sequence = _semantic_source_sequence(scene_texts, weights, target_counts, title, script)
        source_sequence = _polish_mixed_sequence(source_sequence, target_counts)
    discovery_indices = {source: [] for source in _SOURCE_ORDER}
    decisions: List[SceneMediaDecision] = []

    for scene_index, scene_text in enumerate(scene_texts):
        assigned_source = source_sequence[scene_index] if scene_index < len(source_sequence) else "Web Images"
        scene_family = _classify_scene_family(scene_text, title=title, script=script)
        content_type = _classify_scene_content_type(scene_text, title=title, script=script)
        grouped_indices[assigned_source].append(scene_index)
        preferred_sources = (
            automatic_source_preferences(scene_text, title=title, script=script)
            if source_strategy == "preference"
            else [assigned_source]
        )
        if source_strategy == "preference":
            for primary_source in preferred_sources:
                discovery_indices[primary_source].append(scene_index)
        else:
            discovery_indices[assigned_source].append(scene_index)
        stock_allowed = bool(
            weights.get("Stock Media", 0.0) > _ZERO_WEIGHT_EPSILON
            and scene_family not in {_FAMILY_PERSON, _FAMILY_EVENT}
        )

        source_scores = [
            SourceScore(
                source=source,
                quality_score=round(weights.get(source, 0.0) * 100.0, 2),
                latency_score=round(_source_latency_score(source) * 100.0, 2),
                total_score=round(weights.get(source, 0.0) * 100.0, 2),
                notes=[
                    "explicit_weight_routed" if explicit_weights else "automatic_topic_policy",
                    f"topic={topic_policy}",
                    f"target={target_counts.get(source, 0)}",
                ],
            )
            for source in _SOURCE_ORDER
            if target_counts.get(source, 0) > 0
        ]

        chosen_score = next((score for score in source_scores if score.source == assigned_source), None)
        fallback_sources = [score.source for score in source_scores if score.source != assigned_source]
        if source_strategy == "preference":
            fallback_sources = [source for source in preferred_sources if source != assigned_source]
            if stock_allowed:
                fallback_sources.append("Stock Media")
        elif assigned_source == "YouTube Clips" and target_counts.get("Web Images", 0) > 0:
            fallback_sources = ["Web Images"]

        decisions.append(
            SceneMediaDecision(
                scene_index=scene_index,
                scene_text=scene_text,
                scene_family=scene_family,
                content_type=content_type,
                requested_source=assigned_source,
                assigned_source=assigned_source,
                fallback_sources=fallback_sources,
                source_scores=source_scores,
                quality_score=(chosen_score.quality_score if chosen_score else 0.0),
                latency_score=(chosen_score.latency_score if chosen_score else 0.0),
                router_score=(chosen_score.total_score if chosen_score else 0.0),
                attempted_sources=[assigned_source],
                rebalanced_early=False,
                fallback_reason="",
                resolved_source=assigned_source,
                final_source=assigned_source,
                preferred_sources=preferred_sources,
                primary_discovery_sources=preferred_sources if source_strategy == "preference" else [assigned_source],
                stock_allowed=stock_allowed,
                source_strategy=source_strategy,
            )
        )

    return AdaptiveMixedMediaPlan(
        requested_weights=weights,
        target_counts=target_counts,
        final_counts={source: len(indices) for source, indices in grouped_indices.items()},
        grouped_indices=grouped_indices,
        decisions=decisions,
        topic_policy=topic_policy,
        discovery_indices=discovery_indices,
        source_strategy=source_strategy,
        media_policy=policy,
    )


def plan_to_dict(plan: AdaptiveMixedMediaPlan) -> Dict:
    return {
        "requested_weights": plan.requested_weights,
        "target_counts": plan.target_counts,
        "final_counts": plan.final_counts,
        "grouped_indices": plan.grouped_indices,
        "topic_policy": plan.topic_policy,
        "discovery_indices": plan.discovery_indices,
        "source_strategy": plan.source_strategy,
        "media_policy": plan.media_policy,
        "decisions": [asdict(decision) for decision in plan.decisions],
    }
