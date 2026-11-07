"""One-call video-level editing direction with deterministic fallback.

The LLM selects an editorial language for the whole project.  It does not pick
arbitrary CSS values scene-by-scene: model output is normalized against the
curated registries, then the semantic scene planner derives reproducible
variations from the profile seed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from typing import Any, Dict, Mapping, Optional, Sequence

from .editing_libraries import (
    DOCUMENTARY_FONT_ROLES,
    FONT_LIBRARY,
    FONT_PAIRINGS,
    IMAGE_TREATMENT_LIBRARY,
    NICHE_PACKS,
    OVERLAY_LIBRARY,
    SFX_PACKS,
    TEXT_STYLE_LIBRARY,
    TRANSITION_PACKS,
    allowed_id,
)
from .documentary_template_system import art_direction_pack, catalog_manifest, direction_pack


PROFILE_VERSION = 1

_TEXT_ENTRY_MOTIONS = {
    "slide_left", "slide_right", "wipe", "rise", "fade", "pop", "stamp", "paper", "data",
}

_ROYAL_CONTEXT_RE = re.compile(
    r"\b(?:royal\s+family|british\s+royal|royalty|monarchy|monarch|windsor|"
    r"king\s+charles|queen\s+camilla|prince\s+william|prince\s+harry|"
    r"buckingham\s+palace|duke\s+of\s+sussex)\b",
    re.IGNORECASE,
)

_NICHE_EDITING_DEFAULTS = {
    "royal": {"graphic_density": 0.32, "sound_density": 0.46, "text_entry_motion": "slide_left"},
    # Gaming benefits from more cuts and stronger directional text because
    # the web lane often returns fewer clean gameplay clips than stills.
    "gaming": {"graphic_density": 0.32, "sound_density": 0.62, "text_entry_motion": "slide_left"},
    "crime": {"graphic_density": 0.32, "sound_density": 0.58, "text_entry_motion": "wipe"},
    "history": {"graphic_density": 0.32, "sound_density": 0.48, "text_entry_motion": "paper"},
    "documentary": {"graphic_density": 0.32, "sound_density": 0.48, "text_entry_motion": "fade"},
    "travel": {"graphic_density": 0.32, "sound_density": 0.50, "text_entry_motion": "slide_right"},
    "finance": {"graphic_density": 0.32, "sound_density": 0.56, "text_entry_motion": "data"},
    "technology": {"graphic_density": 0.32, "sound_density": 0.58, "text_entry_motion": "slide_left"},
    "sports": {"graphic_density": 0.32, "sound_density": 0.60, "text_entry_motion": "slide_right"},
    "science": {"graphic_density": 0.32, "sound_density": 0.44, "text_entry_motion": "data"},
    "politics": {"graphic_density": 0.32, "sound_density": 0.45, "text_entry_motion": "wipe"},
    "military": {"graphic_density": 0.32, "sound_density": 0.54, "text_entry_motion": "wipe"},
    "nature": {"graphic_density": 0.32, "sound_density": 0.46, "text_entry_motion": "fade"},
    "biography": {"graphic_density": 0.32, "sound_density": 0.44, "text_entry_motion": "paper"},
    "health": {"graphic_density": 0.32, "sound_density": 0.42, "text_entry_motion": "data"},
    "automotive": {"graphic_density": 0.32, "sound_density": 0.58, "text_entry_motion": "slide_left"},
    "culture": {"graphic_density": 0.32, "sound_density": 0.48, "text_entry_motion": "slide_right"},
    "horror": {"graphic_density": 0.32, "sound_density": 0.52, "text_entry_motion": "fade"},
    "general": {"graphic_density": 0.32, "sound_density": 0.48, "text_entry_motion": "slide_left"},
}

# These are editorial rules, not fixed templates. The LLM scene director uses
# them as a subject-specific vocabulary, then selects a different visual
# instrument for each script beat according to its evidence and story role.
NICHE_VISUAL_GRAMMAR = {
    "crime": {
        "editorial_logic": "Reconstruct the evidence chain and chronology; separate confirmed facts, allegations, and unknowns.",
        "use_when_grounded": ["verified source excerpts", "case chronology", "location maps", "evidence-to-consequence diagrams", "restrained witness or subject portraits"],
        "motion_language": ["trace", "reveal", "connect", "redact only verified source text", "hold on evidence"],
        "avoid": ["invented case-file labels", "fabricated evidence boards", "unsupported routes or timestamps", "sensational crime-scene reenactments"],
    },
    "sports": {
        "editorial_logic": "Follow the contest: athlete action, turning points, tactical relationships, and sourced performance measures.",
        "use_when_grounded": ["relevant match footage", "verified athlete portraits", "play or formation diagrams when the play is described", "score and stat graphics from supplied sources", "rivalry comparisons"],
        "motion_language": ["track", "split", "sequence", "count", "land the turning point"],
        "avoid": ["wrong teams or matchups", "generic stadium filler for a named event", "video-game footage for real sport", "invented scores or performance statistics"],
    },
    "gaming": {
        "editorial_logic": "Explain the game as a designed interactive world: mechanics, systems, level scale, and verified release context.",
        "use_when_grounded": ["authentic gameplay", "world maps", "mechanism diagrams", "version or release chronology", "sourced performance comparisons"],
        "motion_language": ["navigate", "reveal", "trace", "scale", "compare"],
        "avoid": ["fake HUD telemetry", "invented gameplay footage", "unsupported benchmarks", "sports clips standing in for game footage"],
    },
    "history": {
        "editorial_logic": "Make chronology, archival evidence, place, and human consequence legible without implying modern footage is historical evidence.",
        "use_when_grounded": ["dated archival images", "maps and boundary changes", "primary documents", "chronologies", "before-and-after geography"],
        "motion_language": ["unfold", "trace", "layer", "compare eras", "settle on the source"],
        "avoid": ["fabricated archive documents", "anachronistic labels", "unsupported borders or dates", "decorative faux-aged treatments"],
    },
    "royal": {
        "editorial_logic": "Connect people, institutions, succession, and public events with sourced portraits and exact chronology.",
        "use_when_grounded": ["identified portraits", "official event footage", "succession timelines", "institutional context", "verified headlines"],
        "motion_language": ["introduce", "connect", "advance chronology", "reveal source"],
        "avoid": ["confusable family members", "fabricated lineage", "unsupported private-life claims", "decorative heraldry"],
    },
    "travel": {
        "editorial_logic": "Orient the viewer in real geography, then move from place to lived detail and atmosphere.",
        "use_when_grounded": ["world-to-region maps", "verified routes", "distinct place imagery", "architecture and landscape details", "cultural context"],
        "motion_language": ["zoom with a settled camera", "trace a narrated route", "pan across a verified vista", "reveal local detail"],
        "avoid": ["unsupported routes", "generic destination footage", "rapid oscillating maps", "invented place labels"],
    },
    "finance": {
        "editorial_logic": "Explain scale, incentives, ownership, and change with comparable sourced values.",
        "use_when_grounded": ["accurate time-series charts", "ownership or cash-flow diagrams", "sourced comparisons", "company and market imagery", "document evidence"],
        "motion_language": ["count", "grow", "compare", "trace a flow", "mark a turning point"],
        "avoid": ["invented prices or tickers", "misleading truncated axes", "unsupported causal arrows", "decorative terminal screens"],
    },
    "technology": {
        "editorial_logic": "Show how a product or system works, what changed, and which claims are measured or sourced.",
        "use_when_grounded": ["product details", "process diagrams", "system relationships", "release chronology", "sourced benchmark charts"],
        "motion_language": ["assemble", "trace a signal", "reveal layers", "compare versions", "hold on real product detail"],
        "avoid": ["fake telemetry", "invented interface screens", "unsupported benchmarks", "generic futuristic HUDs"],
    },
    "science": {
        "editorial_logic": "Move from question to method to evidence to interpretation, preserving uncertainty and scale.",
        "use_when_grounded": ["mechanism diagrams", "scale transitions", "sourced measurements", "experiment footage", "time-series or distribution charts"],
        "motion_language": ["zoom across scale", "assemble a mechanism", "trace a measured change", "compare hypotheses"],
        "avoid": ["invented measurements", "unlabeled illustrative models presented as data", "false precision", "unsupported causal claims"],
    },
    "politics": {
        "editorial_logic": "Distinguish claims, policy text, process, and measured outcomes; explain institutions without partisan decoration.",
        "use_when_grounded": ["verified documents", "institution and process diagrams", "district or policy maps", "sourced election data", "date-led chronology"],
        "motion_language": ["quote the source", "trace the process", "compare stated outcomes", "reveal the timeline"],
        "avoid": ["invented polling", "unsupported territorial claims", "fake document excerpts", "decorative party-color coding that implies judgment"],
    },
    "military": {
        "editorial_logic": "Explain documented movement, capability, and consequence with verified geography and chronology.",
        "use_when_grounded": ["verified operational maps", "documented equipment imagery", "chronology", "force or logistics diagrams", "source-led comparisons"],
        "motion_language": ["trace only documented movement", "reveal positions", "sequence events", "compare scale"],
        "avoid": ["invented tactical overlays", "unsupported routes or unit positions", "game footage as evidence", "fake command-center HUDs"],
    },
    "nature": {
        "editorial_logic": "Show habitat, organism, and ecological relationship at the scale supported by the story.",
        "use_when_grounded": ["identified species imagery", "habitat maps", "food-web or process diagrams", "sourced population trends", "seasonal timelines"],
        "motion_language": ["move across scale", "reveal habitat layers", "trace a cycle", "let behavior play"],
        "avoid": ["wrong species", "invented population data", "overstated geographic ranges", "busy overlays on animal behavior"],
    },
    "biography": {
        "editorial_logic": "Build a person’s arc through dated moments, choices, relationships, and documented work.",
        "use_when_grounded": ["identified portraits", "chronology", "verified letters or documents", "work and place imagery", "before-and-after comparisons"],
        "motion_language": ["introduce", "advance time", "connect influence", "reveal the source"],
        "avoid": ["invented quotations", "unsupported motives", "misidentified portraits", "generic profile cards"],
    },
    "health": {
        "editorial_logic": "Explain the body, treatment, and evidence carefully; distinguish established findings from uncertainty.",
        "use_when_grounded": ["verified mechanism illustrations", "sourced clinical measurements", "evidence comparisons", "timeline of treatment or disease", "expert or source excerpts"],
        "motion_language": ["trace a mechanism", "compare measured outcomes", "reveal evidence strength", "hold on uncertainty"],
        "avoid": ["invented medical data", "diagnosis from decorative imagery", "misleading certainty", "unsupported body mechanics"],
    },
    "automotive": {
        "editorial_logic": "Show design, engineering, performance, and context through real vehicle detail and sourced specifications.",
        "use_when_grounded": ["vehicle detail footage", "exploded or process diagrams", "sourced specifications", "era comparisons", "road or factory footage"],
        "motion_language": ["track a vehicle", "reveal components", "compare generations", "count measured performance"],
        "avoid": ["sports league assumptions from the phrase sports car", "invented specifications", "generic racing footage", "unsafe driving presented as endorsement"],
    },
    "culture": {
        "editorial_logic": "Explain the people, artifacts, practice, and context behind cultural change without flattening differences.",
        "use_when_grounded": ["identified artifacts", "event footage", "chronology", "place context", "sourced quotations"],
        "motion_language": ["reveal detail", "connect influence", "compare periods", "hold on the artifact"],
        "avoid": ["invented symbols", "unsupported cultural labels", "generic collage filler", "decorative text"],
    },
    "horror": {
        "editorial_logic": "Build uncertainty through evidence, place, and controlled revelation; the edit should not claim more than the sources do.",
        "use_when_grounded": ["verified testimony", "source chronology", "place atmosphere", "document excerpts", "restrained negative space"],
        "motion_language": ["hold", "reveal", "trace", "cut to silence", "withhold then clarify"],
        "avoid": ["fabricated proof", "fake paranormal evidence", "constant jump scares", "unearned glitch effects"],
    },
    "general": {
        "editorial_logic": "Choose the clearest visual argument for each script beat; vary evidence, scale, and pacing without forcing a template quota.",
        "use_when_grounded": ["relevant media", "maps", "chronology", "source excerpts", "diagrams", "comparisons", "charts"],
        "motion_language": ["reveal", "trace", "compare", "assemble", "hold"],
        "avoid": ["invented facts", "decorative overlays", "repeated layout without narrative reason", "fake data"],
    },
}

_CATEGORY_ALIASES = {
    "royal": {"royal", "royalty", "royal family", "royal history", "monarchy"},
    "gaming": {"gaming", "game", "gaming blockbuster", "video games", "esports"},
    "crime": {"crime", "true crime", "true crime documentary", "criminal justice", "investigation"},
    "sports": {"sports", "sport", "sports documentary", "football", "soccer", "cricket", "basketball", "nfl", "nba"},
    "history": {"history", "historical", "war history"},
    "travel": {"travel", "travelogue", "tourism", "destination"},
    "finance": {"finance", "finance explainer", "economics", "markets"},
    "technology": {"technology", "tech", "software", "ai"},
    "science": {"science", "science nature", "physics", "biology", "space science"},
    "politics": {"politics", "political", "politics explainer", "government", "public policy"},
    "military": {"military", "military tactical", "defense", "warfare", "conflict"},
    "nature": {"nature", "wildlife", "environment", "ecology"},
    "biography": {"biography", "biographical", "biographical essay", "person profile"},
    "health": {"health", "health science", "medicine", "medical"},
    "automotive": {"automotive", "automotive documentary", "cars", "car history", "motorsport engineering"},
    "culture": {"culture", "cultural chronicle", "arts", "music", "entertainment"},
    "horror": {"horror", "paranormal"},
}


NICHE_VISUAL_GRAMMAR.update({'aviation': {'editorial_logic': 'Explain flight, aircraft systems, operating constraints and verified incidents.', 'use_when_grounded': ['aircraft and airport footage', 'grounded flight routes', 'system cutaways', 'sourced specification comparisons'], 'motion_language': ['trace a stated route', 'assemble a mechanism', 'compare'], 'avoid': ['invented flight telemetry', 'wrong aircraft model', 'animated crash reconstruction presented as evidence']}, 'maritime': {'editorial_logic': 'Explain vessels, ports, routes and the engineering behind a stated voyage.', 'use_when_grounded': ['ship and port footage', 'verified sea routes', 'lock and propulsion diagrams', 'sourced vessel dimensions'], 'motion_language': ['trace', 'section reveal', 'scale comparison'], 'avoid': ['invented routes', 'wrong vessel', 'decorative radar']}, 'engineering': {'editorial_logic': 'Reveal a mechanism through its parts, constraints and consequence.', 'use_when_grounded': ['component assembly', 'cutaway diagrams', 'grounded measurements', 'real machinery footage'], 'motion_language': ['assemble', 'connect named parts', 'trace energy flow'], 'avoid': ['invented dimensions', 'unlabeled decorative schematics']}, 'business': {'editorial_logic': 'Follow the decisions, market context and documented consequences.', 'use_when_grounded': ['company footage', 'verified revenue comparisons', 'product evolution', 'sourced documents'], 'motion_language': ['compare', 'sequence', 'trace cause and effect'], 'avoid': ['invented financial data', 'generic office imagery used as evidence']}, 'food': {'editorial_logic': 'Show ingredients, technique, origin and the people behind the dish.', 'use_when_grounded': ['real preparation footage', 'origin maps', 'ingredient-to-result diagrams', 'identified people'], 'motion_language': ['match action', 'assemble', 'locate'], 'avoid': ['unrelated dishes', 'unsupported health claims']}, 'architecture': {'editorial_logic': 'Connect place, structure, human scale and the documented design decisions.', 'use_when_grounded': ['building footage', 'site locators', 'grounded structural diagrams', 'before and after imagery'], 'motion_language': ['section reveal', 'scale', 'compare'], 'avoid': ['invented blueprints', 'wrong building']}, 'education': {'editorial_logic': 'Explain one question at a time using evidence, mechanisms and useful comparisons.', 'use_when_grounded': ['grounded diagrams', 'verified charts', 'relevant real-world footage', 'document excerpts'], 'motion_language': ['assemble', 'compare', 'reveal the answer'], 'avoid': ['decorative node clouds', 'unsupported data']}})
_CATEGORY_ALIASES.update({'aviation': {'planes', 'air travel', 'aviation', 'aircraft'}, 'maritime': {'ships', 'shipping', 'naval engineering', 'maritime'}, 'engineering': {'mechanical engineering', 'engineering'}, 'business': {'entrepreneurship', 'business'}, 'food': {'cooking', 'food', 'culinary'}, 'architecture': {'buildings', 'architecture'}, 'education': {'education', 'educational', 'explainer'}})


def _explicit_category_niche(category: str) -> str:
    normalized = re.sub(r"[_-]+", " ", str(category or "").strip().lower())
    return next((niche for niche, aliases in _CATEGORY_ALIASES.items() if normalized in aliases), "")


def niche_visual_grammar(niche: str) -> Dict[str, Any]:
    """Return the editorial vocabulary for a niche without forcing a layout."""
    return deepcopy(NICHE_VISUAL_GRAMMAR.get(str(niche or "general").strip().lower(), NICHE_VISUAL_GRAMMAR["general"]))


def _seed(*parts: Any) -> int:
    payload = "|".join(str(part or "") for part in parts).encode("utf-8", errors="ignore")
    return int(hashlib.sha256(payload).hexdigest()[:12], 16)


def classify_niche(topic: str = "", category: str = "", script: str = "") -> str:
    explicit = _explicit_category_niche(category)
    if explicit:
        return explicit
    text = f"{topic} {category} {script[:5000]}".lower()
    text = re.sub(r"\bsports?\s+cars?\b", "performance vehicle", text)
    if _ROYAL_CONTEXT_RE.search(text):
        return "royal"
    patterns = {
        "crime": (r"\b(?:murder|homicide|serial killer|cold case|true crime|forensic|suspect|victim|investigation|criminal trial|missing person|crime scene|hijack(?:er|ing)?|ransom|robbery|heist|burglary|kidnapp(?:er|ing)?|fbi|law enforcement|criminal|fraud|theft)\b",),
        "aviation": (r"\b(?:aviation|aircraft|airliner|airplane|aeroplane|boeing|airbus|cockpit|runway|commercial flight)\b",),
        "maritime": (r"\b(?:maritime|container ship|cargo ship|shipbuilding|shipping canal|panama canal|suez canal|ocean liner|port authority)\b",),
        "engineering": (r"\b(?:engineering|mechanical design|turbine|hydraulic|gearbox|structural design)\b",),
        "business": (r"\b(?:entrepreneur|business model|company strategy|corporate merger|retail chain)\b",),
        "food": (r"\b(?:cooking|culinary|recipe|chef|cuisine|restaurant|ingredients)\b",),
        "architecture": (r"\b(?:architecture|architect|skyscraper|building design|urban design)\b",),
        "education": (r"\b(?:education|classroom|curriculum|teaching|learning method)\b",),
        "gaming": (r"\b(?:gaming|video game|gameplay|esports|playstation|xbox|nintendo|steam|gta|rockstar|open-world rpg|fps game)\b",),
        "automotive": (r"\b(?:automotive|car|cars|automobile|engine|horsepower|vehicle|racing car|car factory)\b",),
        # Some major sports stories are named for the event or clubs without
        # using the generic word "football" (for example, "Super Bowl XLIX"
        # and "Seahawks–Patriots"). Recognize those unambiguous anchors so
        # they receive the sports visual grammar before the director runs.
        "sports": (r"\b(?:nfl|nba|nhl|mlb|super\s*bowl|seahawks|patriots|football|soccer|cricket|basketball|athlete|team|league|season|match|playoffs|championship|tournament|touchdown|quarterback|second[-\s]+and[-\s]+goal|goalkeeper|scoreboard|grand slam)\b",),
        "finance": (r"\b(?:finance|stock market|share price|investor|revenue|profit|economy|inflation|billionaire|banking|corporate earnings)\b",),
        "politics": (r"\b(?:politic|election|parliament|congress|government|policy|legislation|president|prime minister|senate|campaign)\b",),
        "science": (r"\b(?:science|scientific|physics|biology|chemistry|experiment|research paper|laboratory|quantum|climate model|astronomy)\b",),
        "health": (r"\b(?:health|medical|medicine|disease|diagnosis|treatment|clinical trial|hospital|public health|vaccine)\b",),
        "military": (r"\b(?:military|armed forces|battle|warfare|weapon|military operation|naval fleet|air force|defense ministry)\b",),
        "nature": (r"\b(?:wildlife|ecosystem|species|habitat|conservation|biodiversity|animal migration|rainforest|ocean life|environment)\b",),
        "travel": (r"\b(?:travel|tourism|destination|journey|itinerary|city guide|national park|visit to|road trip)\b",),
        "technology": (r"\b(?:technology|software|computer|artificial intelligence|startup|semiconductor|smartphone|robotics|spacecraft)\b",),
        "horror": (r"\b(?:horror|haunted|paranormal|ghost|monster|supernatural)\b",),
        # Named space-race subjects often contain no generic "history" or
        # "archive" wording. Classify them as history so a documentary about
        # Apollo gets chronology and evidence grammar, not a generic/science
        # treatment merely because it mentions spacecraft and astronauts.
        "history": (r"\b(?:history|historical|archive|empire|titanic|ancient|century|historian|revolution|dynasty|apollo\s*11|moon landing|space race|lunar module|armstrong|aldrin|collins|saturn\s*v)\b",),
        "biography": (r"\b(?:biography|life of|career of|born in|legacy of|personal history)\b",),
        "culture": (r"\b(?:culture|cultural|music scene|film industry|art movement|fashion|tradition|popular culture)\b",),
    }
    scores = {
        niche: sum(2 if index == 0 else 1 for index, _ in enumerate(re.finditer(pattern, text)))
        for niche, niche_patterns in patterns.items()
        for pattern in niche_patterns
    }
    if re.search(r"\b(?:hijack(?:er|ing)?|ransom|heist|serial killer|homicide|murder|kidnapping)\b", text):
        return "crime"
    best = max(scores, key=scores.get) if scores else "general"
    return best if scores.get(best, 0) else "general"


def _palette(niche: str, seed: int) -> Dict[str, str]:
    """Generate a unique palette deterministically from the seed.
    
    Produces distinct branding according to niche:
    - gaming: electric cyan / neon magenta / amber gold accents, sleek obsidian surfaces
    - crime: red / crimson accents, dark noir surfaces and pitch obsidian background
    - travel: lush emerald green / tropical teal accents, forest slate surfaces
    - finance: currency green / gold accents, terminal slate surfaces
    - technology: electric cyan / neon blue accents, deep space navy surfaces
    - history: antique gold / amber accents, archival sepia surfaces
    """
    rng = _pseudo_rng(seed + _seed("palette", niche))

    if niche == "gaming":
        accents = ["#00f0ff", "#ff007f", "#ffd700", "#8b5cf6", "#00ff9f", "#38bdf8"]
        surfaces = ["#0f111a", "#141726", "#161b2e"]
        backgrounds = ["#08090f", "#0a0b12", "#090a14"]
        return {
            "background": backgrounds[seed % len(backgrounds)],
            "surface": surfaces[seed % len(surfaces)],
            "primary": "#ffffff",
            "accent": accents[seed % len(accents)],
            "secondary": "#38bdf8",
            "danger": "#ff3366",
            "accents": accents,
            "niche": "gaming",
        }
    elif niche == "crime":
        accents = ["#D32F2F", "#B72A31", "#C0392B", "#CF343A"]
        surfaces = ["#181216", "#1c1417", "#161218", "#1a1014"]
        backgrounds = ["#0c0b0e", "#0a090c", "#0e080b", "#0d0a0d"]
        return {
            "background": backgrounds[seed % len(backgrounds)],
            "surface": surfaces[seed % len(surfaces)],
            "primary": "#ffffff",
            "accent": accents[seed % len(accents)],
            "secondary": "#8a9ba8",
            "danger": "#ff1744",
            "accents": accents,
            "niche": "crime",
        }
    elif niche == "travel":
        accents = ["#10b981", "#00a86b", "#059669", "#14b8a6"]
        surfaces = ["#10261e", "#0f2b23", "#122a28", "#112e20"]
        backgrounds = ["#081712", "#061510", "#071917", "#061811"]
        return {
            "background": backgrounds[seed % len(backgrounds)],
            "surface": surfaces[seed % len(surfaces)],
            "primary": "#ffffff",
            "accent": accents[seed % len(accents)],
            "secondary": "#fbbf24",
            "danger": "#f59e0b",
            "accents": accents,
            "niche": "travel",
        }
    elif niche == "finance":
        accents = ["#27ae60", "#2ecc71", "#10b981", "#d4af37"]
        surfaces = ["#101c18", "#0f221b", "#14201d"]
        backgrounds = ["#0b120f", "#07110d", "#0a1310"]
        return {
            "background": backgrounds[seed % len(backgrounds)],
            "surface": surfaces[seed % len(surfaces)],
            "primary": "#edf8f2",
            "accent": accents[seed % len(accents)],
            "secondary": "#75a99a",
            "danger": "#ee6b6b",
            "accents": accents,
            "niche": "finance",
        }
    elif niche == "technology":
        accents = ["#00e5ff", "#0070f3", "#38bdf8", "#06b6d4"]
        surfaces = ["#0d1b2a", "#0f172a", "#111c30"]
        backgrounds = ["#060d17", "#080e1a", "#050b14"]
        return {
            "background": backgrounds[seed % len(backgrounds)],
            "surface": surfaces[seed % len(surfaces)],
            "primary": "#f0f9ff",
            "accent": accents[seed % len(accents)],
            "secondary": "#64748b",
            "danger": "#f43f5e",
            "accents": accents,
            "niche": "technology",
        }
    elif niche == "history":
        accents = ["#d4af37", "#c28b43", "#e67e22", "#cd7f32"]
        surfaces = ["#231d18", "#2a221b", "#1f1914"]
        backgrounds = ["#14100c", "#18130e", "#110d0a"]
        return {
            "background": backgrounds[seed % len(backgrounds)],
            "surface": surfaces[seed % len(surfaces)],
            "primary": "#f5ede0",
            "accent": accents[seed % len(accents)],
            "secondary": "#8b9b82",
            "danger": "#b9553f",
            "accents": accents,
            "niche": "history",
        }

    # Restrained, coherent palettes for the newer specialist grammars. These
    # are palette families, not scene layouts; the director still chooses how
    # each beat uses color, footage, maps, or evidence.
    niche_palette = {
        "aviation": ("#10161D", "#1B2630", "#F4F4F1", ["#80B7CE", "#DBAD57"], "#BCC9CF", "#D45B4C"),
        "maritime": ("#0C151C", "#142631", "#F4F4F1", ["#76AEB8", "#CEAC64"], "#B4C4CD", "#CF5C50"),
        "engineering": ("#131619", "#20272C", "#F4F4F1", ["#D1A957", "#8DB5BC"], "#BDC6CA", "#CE5B51"),
        "business": ("#111714", "#1C2721", "#F4F4F1", ["#7BB393", "#D5B165"], "#BECBC1", "#CE5B51"),
        "food": ("#1B1510", "#2B2118", "#FAF4E9", ["#D4A561", "#86A477"], "#D3C4AD", "#C96950"),
        "architecture": ("#171717", "#262523", "#F8F5EF", ["#C2A478", "#9EAFAE"], "#C6C1B8", "#C96950"),
        "general": ("#111418", "#202429", "#F5F3ED", ["#C8A362", "#83B0B5", "#ACB9C0"], "#C1C4C7", "#C45B51"),
        "education": ("#11191C", "#202B30", "#F4F4F1", ["#78ACBB", "#D7AC62"], "#BCC7CA", "#C96950"),
        "sports": ("#111B17", "#1C2A23", "#F3F0E7", ["#D6A84F", "#C88F42", "#79A18B"], "#B5C2B7", "#D16B56"),
        "politics": ("#11171A", "#1C2428", "#F2EFE7", ["#D5A548", "#C99545", "#7595A1"], "#B8B7AD", "#B85A50"),
        "science": ("#0C1718", "#142525", "#F1F5EE", ["#5FAE9B", "#D59F4C", "#4D9AA1"], "#A8C3BA", "#D16B56"),
        "health": ("#0C1717", "#142322", "#F0F6F3", ["#55A18E", "#78AFA0", "#D4B56C"], "#AAC1BA", "#C56D61"),
        "military": ("#111511", "#1C2119", "#F0EEE4", ["#B99A57", "#C4773F", "#748773"], "#B3B7A7", "#B85245"),
        "nature": ("#0C1711", "#14251B", "#F2F4E9", ["#6C9B65", "#8CA36A", "#C1A766"], "#A8B8A1", "#C87356"),
        "automotive": ("#111416", "#1B2023", "#F4F4F1", ["#C88D59", "#AEB8BE", "#8C9DA5"], "#ADB4B8", "#BD5B4C"),
        "biography": ("#171512", "#24201A", "#F4EFE5", ["#BE9C5B", "#A68452", "#997964"], "#C9BDAA", "#A84F43"),
        "culture": ("#151615", "#222522", "#F4F0E9", ["#C87555", "#AE694F", "#77958C"], "#BDB5AA", "#BC5448"),
        "royal": ("#140E11", "#23181D", "#F7F1E7", ["#D4AF59", "#C29B55", "#9C5961"], "#D5C5B5", "#B84E4A"),
    }.get(niche)
    if niche_palette:
        background, surface, primary, accents, secondary, danger = niche_palette
        accent = accents[seed % len(accents)]
        return {
            "background": background,
            "surface": surface,
            "primary": primary,
            "accent": accent,
            "secondary": secondary,
            "danger": danger,
            "accents": accents,
            "niche": niche,
        }

    # General / horror / fallback color harmony
    hue_ranges = {
        "horror":     [(0, 15), (340, 360)],            # blood reds
        "general":    [(25, 60), (190, 230)],           # warm gold / cool blue
    }
    ranges = hue_ranges.get(niche, hue_ranges["general"])
    chosen = ranges[seed % len(ranges)]
    base_hue = _lerp(chosen[0], chosen[1], rng())

    accent_offset = 30 if (seed // 7) % 2 == 0 else 165
    accent_hue = (base_hue + accent_offset + rng() * 20 - 10) % 360
    secondary_offset = 150 if seed % 3 == 0 else 210
    secondary_hue = (base_hue + secondary_offset + rng() * 15 - 7) % 360
    danger_hue = (355 + rng() * 15) % 360

    bg_light = .04 + rng() * .04
    surf_light = .08 + rng() * .06
    bg_sat = 0.12 + rng() * 0.18

    main_accent = _hsl_hex(accent_hue, .62 + rng() * .3, .48 + rng() * .18)
    sec_accent = _hsl_hex(secondary_hue, .35 + rng() * .25, .38 + rng() * .22)
    alt_accent = _hsl_hex((accent_hue + 60) % 360, .65, .52)

    return {
        "background": _hsl_hex(base_hue, bg_sat, bg_light),
        "surface": _hsl_hex(base_hue, bg_sat * 1.4, surf_light),
        "primary": _hsl_hex(base_hue, .06 + rng() * .12, .87 + rng() * .1),
        "accent": main_accent,
        "secondary": sec_accent,
        "danger": _hsl_hex(danger_hue, .72 + rng() * .18, .42 + rng() * .16),
        "accents": [main_accent, sec_accent, alt_accent],
        "niche": niche,
    }


def _pseudo_rng(seed: int):
    """Return a closure that produces 0..1 floats deterministically from seed."""
    state = (seed & 0xFFFFFFFF)
    def next_float():
        nonlocal state
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF
    return next_float


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _hsl_hex(h: float, s: float, l: float) -> str:
    """HSL → #rrggbb hex string."""
    h = h % 360 / 360
    s = max(0, min(1, s))
    l = max(0, min(1, l))
    if s == 0:
        r = g = b = int(l * 255)
    else:
        q = l * (1 + s) if l < .5 else l + s - l * s
        p = 2 * l - q
        r = int(_hue_to_rgb(p, q, h + 1/3) * 255)
        g = int(_hue_to_rgb(p, q, h) * 255)
        b = int(_hue_to_rgb(p, q, h - 1/3) * 255)
    return f"#{r:02x}{g:02x}{b:02x}"


def _hue_to_rgb(p: float, q: float, t: float) -> float:
    if t < 0: t += 1
    if t > 1: t -= 1
    if t < 1/6: return p + (q - p) * 6 * t
    if t < 1/2: return q
    if t < 2/3: return p + (q - p) * (2/3 - t) * 6
    return p


def deterministic_profile(
    topic: str,
    category: str = "",
    script: str = "",
    *,
    variation_seed: Any = 0,
) -> Dict[str, Any]:
    niche = classify_niche(topic, category, script)
    locked_niche = bool(_explicit_category_niche(category))
    seed = _seed(topic, category, script, variation_seed, "editing-profile")
    pack = NICHE_PACKS.get(niche, NICHE_PACKS["general"])
    pairings = FONT_PAIRINGS.get(niche, FONT_PAIRINGS["general"])

    # Keep automatic documentary typography inside the restrained editorial
    # family. Variation changes the pairing, not the visual language.
    font_idx = seed % len(pairings)
    typography = deepcopy(pairings[font_idx % len(pairings)])
    art_direction = art_direction_pack(niche, seed, mood="")
    # The art direction is selected once per video.  Typography remains a
    # coherent display/body/accent relationship while the seed still changes
    # the visual language between renders.
    typography = {
        "display": art_direction["display"],
        "body": art_direction["body"],
        "accent": art_direction["accent"],
    }

    # ── varied pace & mood ──
    pace_options = ["urgent", "rapid", "precise", "measured", "deliberate", "slow_burn"]
    mood_options = {
        'travel':['curious','inspiring','reflective'], 'nature':['curious','reflective','inspiring'],
        'sports':['energetic','focused','inspiring'], 'science':['curious','precise','authoritative'],
        'technology':['curious','precise','authoritative'], 'culture':['curious','reflective','inspiring'],
        'crime':['tense','somber','authoritative'], 'horror':['tense','somber'],
    }.get(niche,['authoritative','curious','reflective'])
    pace = pace_options[seed % len(pace_options)]
    mood = mood_options[(seed // 2) % len(mood_options)]

    # Graphic and sound coverage are niche-aware.  These are inputs to the
    # semantic scene budget, not a promise that every scene receives a card or
    # a sound effect.  The seed still changes the accent choices while the
    # niche keeps the edit's cadence coherent.
    niche_defaults = _NICHE_EDITING_DEFAULTS.get(niche, _NICHE_EDITING_DEFAULTS["general"])
    graphic_density = float(niche_defaults["graphic_density"])
    sound_density = float(niche_defaults["sound_density"])
    text_entry_motion = str(niche_defaults["text_entry_motion"])

    archetypes = {
        "royal": ["royal_profile", "institution_and_person", "public_life", "succession_story", "modern_monarchy"],
        "history": ["archive_investigation", "chronological_reveal", "human_scale", "artifact_story", "era_contrast"],
        "crime": ["case_reconstruction", "evidence_chain", "countdown_investigation", "witness_testimony", "detective_procedure"],
        "travel": ["route_discovery", "field_journal", "place_portrait", "culture_clash", "hidden_gem"],
        "finance": ["market_explainer", "rise_and_fall", "decision_analysis", "wealth_narrative", "system_exposed"],
        "technology": ["system_explainer", "breakthrough_story", "future_tension", "build_process", "inside_look"],
        "sports": ["competitive_rise", "turning_point", "record_chase", "underdog_story", "rivalry_escalation"],
        "gaming": ["next_gen_reveal", "world_showcase", "record_breaker", "franchise_legacy", "gameplay_deep_dive"],
        "horror": ["slow_dread", "evidence_reveal", "unreliable_archive", "paranormal_case", "descent"],
        "general": ["clarity_explainer", "question_to_answer", "human_scale", "deep_dive", "counter_narrative"],
    }
    arch_list = archetypes.get(niche, archetypes["general"])
    archetype = arch_list[seed % len(arch_list)]

    return {
        "version": PROFILE_VERSION,
        "profile_id": hashlib.sha256(f"{topic}|{category}|{variation_seed}|{seed}".encode()).hexdigest()[:16],
        "source": "deterministic",
        "confidence": 0.72,
        "niche": niche,
        "niche_locked": locked_niche,
        "niche_source": "explicit_category" if locked_niche else "script_classification",
        "visual_grammar": niche_visual_grammar(niche),
        "mood": mood,
        "pace": pace,
        "editorial_archetype": archetype,
        "palette": _palette(niche, seed),
        "typography": typography,
        "art_direction": art_direction,
        "overlay_pack": pack["overlay"],
        "transition_pack": pack["transition"],
        "image_treatment_pack": pack["image"],
        "text_style_pack": pack["text"],
        "graph_pack": "editorial_data",
        "sfx_pack": pack["sfx"],
        "hook_strategy": {
            "duration_seconds": 18,
            "pattern": "question_evidence_payoff" if niche not in {"sports", "travel"} else "impact_promise_motion",
            "graphic_density": graphic_density + 0.1,
            "sound_density": sound_density + 0.05,
        },
        "graphic_density": graphic_density,
        "sound_density": sound_density,
        "text_entry_motion": text_entry_motion,
        "avoid": ["decorative_text_without_narrative_grounding", "adjacent_layout_repeat", "fake_multi_image_collage", "continuous_zoom_pan"],
        "seed": seed,
        "template_catalog_version": catalog_manifest()["version"],
    }


def normalize_profile(raw: Optional[Mapping[str, Any]], fallback: Mapping[str, Any]) -> Dict[str, Any]:
    result = deepcopy(dict(fallback))
    value = dict(raw or {})
    fallback_niche = str(result.get("niche") or "general").lower()
    # Keep royal-family videos in the royal design lane even if the LLM sees
    # dates, succession, or the word "history" and proposes an archive niche.
    raw_niche = str(value.get("niche") or result["niche"]).lower()
    raw_is_inferred_general = (
        raw_niche == "general"
        and not bool(value.get("niche_locked"))
        and fallback_niche != "general"
    )
    niche = (
        fallback_niche
        if fallback_niche == "royal" or bool(result.get("niche_locked")) or raw_is_inferred_general
        else raw_niche
    )
    if niche not in NICHE_PACKS:
        niche = result["niche"]
    result["niche"] = niche
    result["visual_grammar"] = niche_visual_grammar(niche)
    result["mood"] = str(value.get("mood") or result["mood"])[:40]
    result["pace"] = str(value.get("pace") or result["pace"])[:32]
    result["editorial_archetype"] = re.sub(r"[^a-z0-9_]+", "_", str(value.get("editorial_archetype") or result["editorial_archetype"]).lower())[:48]
    result["overlay_pack"] = allowed_id(value.get("overlay_pack"), OVERLAY_LIBRARY, result["overlay_pack"])
    result["transition_pack"] = allowed_id(value.get("transition_pack"), TRANSITION_PACKS, result["transition_pack"])
    result["image_treatment_pack"] = allowed_id(value.get("image_treatment_pack"), IMAGE_TREATMENT_LIBRARY, result["image_treatment_pack"])
    result["text_style_pack"] = allowed_id(value.get("text_style_pack"), TEXT_STYLE_LIBRARY, result["text_style_pack"])
    result["sfx_pack"] = allowed_id(value.get("sfx_pack"), SFX_PACKS, result["sfx_pack"])
    typography = dict(value.get("typography") or {})
    for role in ("display", "body", "accent"):
        candidate = str(typography.get(role) or result["typography"][role]).lower()
        if candidate in DOCUMENTARY_FONT_ROLES.get(role, ()):
            result["typography"][role] = candidate
        else:
            fallback_font = result["typography"].get(role)
            result["typography"][role] = (
                fallback_font
                if fallback_font in DOCUMENTARY_FONT_ROLES.get(role, ())
                else next(
                    (font for font in DOCUMENTARY_FONT_ROLES.get(role, ()) if font in FONT_LIBRARY),
                    fallback_font,
                )
            )
    supplied_direction = value.get("art_direction") if isinstance(value.get("art_direction"), Mapping) else {}
    direction = art_direction_pack(niche, result.get("seed", 0), result.get("mood", ""))
    # LLM output may choose a direction id, but its font files are always
    # resolved through the verified local catalog.
    requested_direction = str(supplied_direction.get("id") or "").strip().lower()
    if requested_direction and requested_direction in {"editorial", "archive", "investigative", "cinematic", "data", "field", "horror"}:
        direction = direction_pack(requested_direction, result.get("seed", 0))
    direction["display"] = result["typography"].get("display", direction["display"])
    direction["body"] = result["typography"].get("body", direction["body"])
    direction["accent"] = result["typography"].get("accent", direction["accent"])
    result["art_direction"] = direction
    result["template_catalog_version"] = catalog_manifest()["version"]
    palette = dict(value.get("palette") or {})
    for key in ("background", "surface", "primary", "accent", "secondary", "danger"):
        candidate = str(palette.get(key) or "")
        if re.fullmatch(r"#[0-9a-fA-F]{6}", candidate):
            result["palette"][key] = candidate
    for key in ("graphic_density", "sound_density", "confidence"):
        try:
            result[key] = round(max(0.0, min(1.0, float(value.get(key, result.get(key, 0.5))))), 3)
        except (TypeError, ValueError):
            pass
    result["source"] = str(value.get("source") or result.get("source") or "deterministic")
    requested_motion = str(value.get("text_entry_motion") or result.get("text_entry_motion") or "").strip().lower()
    result["text_entry_motion"] = requested_motion if requested_motion in _TEXT_ENTRY_MOTIONS else str(
        result.get("text_entry_motion") or "slide_left"
    )
    if niche == "crime":
        result["palette"] = deepcopy(fallback["palette"] if fallback_niche == "crime" else _palette("crime", result.get("seed", 0)))
    if fallback_niche == "royal":
        royal_pack = NICHE_PACKS["royal"]
        result["niche"] = "royal"
        result["overlay_pack"] = royal_pack["overlay"]
        result["transition_pack"] = royal_pack["transition"]
        result["image_treatment_pack"] = royal_pack["image"]
        result["text_style_pack"] = royal_pack["text"]
        result["sfx_pack"] = royal_pack["sfx"]
        result["text_entry_motion"] = "slide_left"
        result["palette"] = deepcopy(fallback.get("palette") or result.get("palette") or {})
        result["typography"] = deepcopy(fallback.get("typography") or result.get("typography") or {})
        result["art_direction"] = deepcopy(fallback.get("art_direction") or result.get("art_direction") or {})
    return result


def plan_editing_profile(
    topic: str,
    category: str = "",
    script: str = "",
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Plan once per video; use deterministic output if the brain is unavailable."""
    config = dict(config or {})
    fallback = deterministic_profile(topic, category, script, variation_seed=config.get("variation_seed", 0))
    supplied = config.get("editing_profile")
    if isinstance(supplied, Mapping):
        return normalize_profile(supplied, fallback)
    api_key = str(config.get("openrouter_api_key") or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("VIDZEO_OPENROUTER_API_KEY") or "").strip()
    llm_enabled = config.get("editing_profile_llm")
    if llm_enabled is None:
        llm_enabled = os.environ.get("VIDZEO_EDITING_PROFILE_LLM", "1")
    if not api_key or str(llm_enabled).lower() in {"0", "false", "off"}:
        return fallback
    try:
        from .openrouter_llm import (
            DEFAULT_SCENE_BRAIN_FALLBACK_MODELS,
            OpenRouterLLM,
        )

        schema = {
            "type": "object",
            "required": ["niche", "mood", "pace", "editorial_archetype", "overlay_pack", "transition_pack", "image_treatment_pack", "text_style_pack", "sfx_pack", "typography"],
            "properties": {
                "niche": {"type": "string"}, "mood": {"type": "string"}, "pace": {"type": "string"},
                "editorial_archetype": {"type": "string"}, "overlay_pack": {"type": "string"},
                "transition_pack": {"type": "string"}, "image_treatment_pack": {"type": "string"},
                "text_style_pack": {"type": "string"}, "sfx_pack": {"type": "string"},
                "typography": {"type": "object"}, "palette": {"type": "object"},
                "graphic_density": {"type": "number"}, "sound_density": {"type": "number"},
                "text_entry_motion": {"type": "string"},
            },
        }
        prompt = {
            "topic": topic,
            "category": category,
            "script_excerpt": script[:5000],
            "allowed": {
                "niches": list(NICHE_PACKS), "overlay_packs": list(OVERLAY_LIBRARY),
                "transition_packs": list(TRANSITION_PACKS), "image_treatments": list(IMAGE_TREATMENT_LIBRARY),
                "text_styles": list(TEXT_STYLE_LIBRARY), "sfx_packs": list(SFX_PACKS),
                "fonts": list(FONT_LIBRARY),
            },
            "instruction": (
                "Direct a premium, human-edited-feeling documentary. Build a distinct visual language from the script's actual subject, "
                "period, evidence, and emotional arc; do not reuse one default look across unrelated videos. Choose a restrained, modern "
                "palette and verified contemporary sans-serif font pairing, with clean hierarchy and open negative space. Keep text close "
                "to the relevant image or map and leave important faces, artifacts, and source passages unobstructed. Prefer fine rules, "
                "editorial typography, authentic source crops, and geographic linework over cards. Do not use childish/cartoon graphics, "
                "emoji, dashboard panels, boxed labels, fake status readouts, military HUDs, or decorative text unrelated to the script. "
                "Every on-screen phrase and map annotation must be supported by the supplied script or verified media/source metadata."
            ),
        }
        fallback_models = config.get("editing_profile_fallback_models")
        if fallback_models is None:
            fallback_models = config.get("scene_brain_fallback_chain")
        if fallback_models is None:
            fallback_models = os.environ.get("VIDZEO_EDITING_PROFILE_FALLBACK_MODELS")
        if isinstance(fallback_models, str):
            fallback_models = [item.strip() for item in fallback_models.replace(";", ",").split(",") if item.strip()]
        fallback_models = list(fallback_models or DEFAULT_SCENE_BRAIN_FALLBACK_MODELS)
        client = OpenRouterLLM(
            api_key=api_key,
            model=str(config.get("llm_model") or "qwen/qwen3.5-flash-02-23"),
            fallback_models=fallback_models,
        )

        try:
            request_timeout = max(
                120.0,
                min(240.0, float(config.get("editing_profile_timeout", os.environ.get("VIDZEO_EDITING_PROFILE_TIMEOUT_SECONDS", 180)) or 180)),
            )
        except (TypeError, ValueError):
            request_timeout = 180.0
        try:
            total_timeout = max(
                240.0,
                min(600.0, float(config.get("editing_profile_total_timeout", os.environ.get("VIDZEO_EDITING_PROFILE_TOTAL_TIMEOUT_SECONDS", 360)) or 360)),
            )
        except (TypeError, ValueError):
            total_timeout = 360.0
        content = client.chat(
            json.dumps(prompt, ensure_ascii=True),
            system_prompt=(
                "You are Vidzeo's senior documentary creative director. Return a distinctive, script-specific, polished video-level "
                "editing profile as JSON. Art-direct like a skilled documentary editor: modern typography, purposeful pacing, "
                "clean spatial composition, and no generic dashboard boxes or childish graphics. Keep every factual visual grounded."
            ),
            temperature=0.35,
            max_tokens=2200,
            json_mode=True,
            json_schema=schema,
            timeout_seconds=min(request_timeout, total_timeout),
            total_timeout_seconds=total_timeout,
            max_attempts=3,
            json_schema_name="vidzeo_editing_profile_v1",
        )
        raw = json.loads(content)
        raw["source"] = "openrouter"
        raw["confidence"] = raw.get("confidence", 0.82)
        return normalize_profile(raw, fallback)
    except Exception:
        return fallback
