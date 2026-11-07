"""Conversational AI Director Tweak Engine.

Enables iterative, conversational modifications to an existing EditPlan
without regenerating audio, re-scraping media, or hallucinating code:
- "Make the first minute faster and punchier"
- "Switch visual style to dark archival"
- "Add more data charts to the financial section"
- "Reduce captions"
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Mapping, Optional

from .contract import EditPlan, VisualSceneType
from .scene_grammar import enforce_scene_grammar
from .visual_dna import generate_visual_dna


def tweak_edit_plan(edit_plan: EditPlan, instruction: str) -> EditPlan:
    """Modify an existing EditPlan in place according to a natural language directorial instruction."""
    inst = str(instruction or "").lower().strip()
    plan = copy.deepcopy(edit_plan)

    # 1. Pacing & Energy adjustments
    if any(k in inst for k in ["faster", "punchier", "speed up", "high energy", "more cuts"]):
        for s in plan.scenes:
            s.motion.speed = 1.3
            s.motion.spring_stiffness = 150.0
            if s.transition_out == "cross_dissolve":
                s.transition_out = "hard_cut"
            if s.primary_visual.type == VisualSceneType.KINETIC_TEXT.value:
                s.primary_visual.variant = "word_by_word_punch"
            s.pacing.energy = min(1.0, s.pacing.energy + 0.3)
        plan.visual_dna.motion_style = "punchy"

    elif any(k in inst for k in ["slower", "cinematic", "calm", "more emotional", "restrained"]):
        for s in plan.scenes:
            s.motion.speed = 0.85
            s.motion.spring_stiffness = 100.0
            s.motion.camera = "slow_push"
            s.transition_out = "cross_dissolve"
            s.pacing.energy = max(0.2, s.pacing.energy - 0.25)
            s.pacing.emotion = min(1.0, s.pacing.emotion + 0.25)
        plan.visual_dna.motion_style = "cinematic_slow"

    # 2. Visual DNA & Aesthetic Genre shifts
    if any(k in inst for k in ["king", "queen", "monarchy", "royal", "imperial"]):
        plan.visual_dna.genre = "royal-history"
        plan.visual_dna.texture = "paper"
        plan.visual_dna.image_treatment = "warm_archival"
        plan.visual_dna.palette["accent"] = "#D4AF37"
        plan.visual_dna.palette["background"] = "#140A10"
        plan.visual_dna.map_style = "archival_sepia"
    elif any(k in inst for k in ["archival", "vintage", "retro", "paper", "historical"]):
        plan.visual_dna.genre = "historical-documentary"
        plan.visual_dna.texture = "paper"
        plan.visual_dna.image_treatment = "warm_archival"
        plan.visual_dna.palette["accent"] = "#C17A3A"
        plan.visual_dna.palette["background"] = "#111312"
        plan.visual_dna.map_style = "archival_sepia"
    elif any(k in inst for k in ["tech", "technology", "cyber", "digital", "modern"]):
        plan.visual_dna.genre = "technology"
        plan.visual_dna.texture = "scanlines"
        plan.visual_dna.image_treatment = "cool_modern"
        plan.visual_dna.palette["accent"] = "#00F0FF"
        plan.visual_dna.palette["background"] = "#05070E"
        plan.visual_dna.map_style = "blueprint_slate"
    elif any(k in inst for k in ["crime", "investigative", "noir", "gritty"]):
        plan.visual_dna.genre = "true-crime"
        plan.visual_dna.texture = "film_grain"
        plan.visual_dna.image_treatment = "desaturated_contrast"
        plan.visual_dna.palette["accent"] = "#DC2626"

    # 3. Data & Chart density
    if any(k in inst for k in ["more data", "add charts", "show stats", "more numbers"]):
        for s in plan.scenes:
            if s.primary_visual.type == VisualSceneType.SMART_IMAGE.value and (s.text.get("headline") or any(ch.isdigit() for ch in s.narration)):
                s.primary_visual.type = VisualSceneType.STAT_CARD.value
                s.primary_visual.variant = "kpi_metric_card"

    # 4. Caption adjustments
    if any(k in inst for k in ["reduce captions", "no captions", "less text", "hide captions"]):
        for s in plan.scenes:
            # Only keep captions on quotes
            if s.primary_visual.type != VisualSceneType.QUOTE_CARD.value:
                s.captions.enabled = False

    # Re-enforce grammar rules so adjustments maintain documentary polish
    plan.scenes = enforce_scene_grammar(plan.scenes)
    plan.metrics["last_tweak_instruction"] = instruction

    return plan
