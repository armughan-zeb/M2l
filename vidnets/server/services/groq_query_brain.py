"""Groq-powered semantic query brain — generates per-scene queries for YouTube, Web, and Stock pipelines."""

import json
import re
from typing import List, Dict, Optional

# Keep the section detection engine
from services.semantic_query_agent import (
    detect_list_sections,
    AGENT_SYSTEM_PROMPT,
    build_agent_user_prompt,
    parse_agent_response,
    normalize_agent_queries,
)

# Per-pipeline system prompts
GROQ_BRAIN_SYSTEM = """You are Vidzeo Query Brain — the central intelligence for video scene retrieval across YouTube, Flickr/web images, and Stock media.

Given a video topic, full script, and per-scene section subjects, generate the BEST search queries for each scene across ALL three pipelines.

CORE RULES:
1. Section subjects are HARD CONTAINERS. Every query for a section scene MUST lead with the section subject.
   - Cristiano Ronaldo section: "Cristiano Ronaldo bicycle kick goal" NOT "football legend highlights"
   - Kylian Mbappe section: "Kylian Mbappe speed dribbling" NOT "cristiano ronaldo Kylian Mbappe"
   
2. INTRO scenes (before first marker): use domain topic, not a section subject.
3. OUTRO scenes (after last item, "choosing only five", "these players stand above"): use domain topic.

4. Use FULL proper names. "Kylian Mbappe" not "Kylian". "Jude Bellingham" not "Bellingham".
   For non-person/common-name subjects, preserve the domain qualifier:
   "Apple fruit" for fruit/health, not "Apple"; "Python programming" for coding, "Python snake" for wildlife.

5. Queries must be concrete, visual, search-optimized: 3-6 words, NO abstract terms.
   - "legacy" → "career highlights trophy celebration"
   - "dominance" → "winning goal champion"

6. PER-PIPELINE FORMATS:
   - YouTube: "Erling Haaland hat trick real footage" (raw footage terms)
   - Web/Flickr: "Cristiano Ronaldo portrait celebration" (photo-friendly terms)  
   - Stock: "Jude Bellingham football action" (generic media terms)
   - Fruit/food YouTube: "Apple fruit close up b roll", "Banana fruit market footage", "Orange fruit juice b roll"
   - Gaming YouTube: "Grand Theft Auto VI official trailer", "Grand Theft Auto VI gameplay trailer", "Rockstar Games GTA VI trailer"
   - Gaming must avoid streamer/live stream/reaction/news/leak/mod/fanmade unless the scene explicitly asks for those.

7. Three query tiers per scene:
   - PRIMARY: Most specific visual query
   - SUPPORTING: Broader discovery query
   - FALLBACK: Broadest, ensures results

8. Use the available tiers to find originating footage, not three paraphrases.
   Include a direct publisher/owner query where the script identifies one: the
   manufacturer for a product, league/team for sports, agency for science, or
   the relevant court/police/archive for a real case. Preserve the exact model,
   person, place and event. For products, search driving, walkaround, cabin or
   manufacturing footage when those are what the scene actually explains.
   A historical generation/year must not be illustrated with the current model.
   Search visible objects/actions rather than a narration fragment or sales chart.

Return ONLY valid JSON array:
[{"scene": 1, "youtube": {"primary":"...","supporting":"...","fallback":"..."}, "web": {"primary":"...","supporting":"...","fallback":"..."}, "stock": {"primary":"...","supporting":"...","fallback":"..."}}, ...]"""


def build_brain_prompt(
    topic: str,
    script: str,
    scene_texts: List[str],
    section_subjects: List[str],
    is_listicle: bool = False,
) -> str:
    """Build the Groq brain prompt with full script context."""
    scene_blocks = []
    for i, text in enumerate(scene_texts):
        section = section_subjects[i] if i < len(section_subjects) else ""
        section_label = f" | SECTION: {section}" if section else " | INTRO/OUTRO"
        scene_blocks.append(f"Scene {i+1}: {text[:180]}{section_label}")

    listicle_header = ""
    if is_listicle:
        distinct = list(dict.fromkeys(s for s in section_subjects if s))
        if len(distinct) >= 2:
            listicle_header = f"""
THIS IS A LISTICLE with {len(distinct)} distinct sections: {', '.join(distinct)}
Section subjects are HARD CONTAINERS — NEVER cross-contaminate queries between sections.
"""

    script_sample = (script or "")[:3000]

    return f"""Topic: {topic}
{listicle_header}
Script context:
{script_sample}

Scene descriptions with section assignments:
{chr(10).join(scene_blocks)}

Generate ONE query set per scene with YouTube, Web, and Stock variants.
Return JSON array, one object per scene."""
