"""Scene Grammar & Pacing Validation Engine.

Enforces documentary editorial rules to prevent recognizable repetition,
visual monotony, and chaotic or nonsensical layout choices.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .contract import (
    ALLOWED_CAMERAS,
    ALLOWED_ENTRANCES,
    EditPlan,
    SceneDecision,
    VisualSceneType,
    SCENE_TYPE_VARIANTS,
)


class GrammarViolationError(Exception):
    """Raised when an edit plan violates hard editorial grammar rules."""
    pass


def enforce_scene_grammar(scenes: Sequence[SceneDecision]) -> List[SceneDecision]:
    """Validate and automatically adjust a sequence of scene decisions to satisfy documentary grammar."""
    if not scenes:
        return []

    validated: List[SceneDecision] = []
    consecutive_images = 0
    recent_layouts: List[str] = []
    recent_camera_moves: List[str] = []
    recent_graphic_entrances: List[str] = []
    last_dramatic_transition_time = -999.0
    last_graphic_time = 0.0
    last_quadrant = None

    dramatic_transitions = {"film_burn", "paper_tear", "light_leak", "zoom_cut"}
    info_graphic_types = {
        VisualSceneType.STAT_CARD.value,
        VisualSceneType.CHART.value,
        VisualSceneType.TIMELINE.value,
        VisualSceneType.DOCUMENT_HIGHLIGHT.value,
        VisualSceneType.HEADLINE.value,
        VisualSceneType.ANIMATED_MAP.value,
        VisualSceneType.DIAGRAM.value,
        VisualSceneType.COMPARISON.value,
        VisualSceneType.KINETIC_TEXT.value,
        VisualSceneType.QUOTE_CARD.value,
        VisualSceneType.PERSON_PROFILE.value,
        VisualSceneType.COLLAGE.value,
    }

    alternatives = [
        ("SmartImage", "full_bleed_cinematic"),
        ("SmartImage", "parallax_float"),
        ("SmartImage", "spotlight_reveal"),
    ]

    for idx, raw_scene in enumerate(scenes):
        s = copy.deepcopy(raw_scene)
        scene_type = s.primary_visual.type
        variant = s.primary_visual.variant
        layout_key = f"{scene_type}:{variant}"

        # Rule 0: The 3-Second Rule (Cold Open Hook)
        if idx == 0:
            s.pacing.energy = max(0.85, s.pacing.energy)
            s.pacing.importance = max(0.90, s.pacing.importance)
            if scene_type == VisualSceneType.SMART_IMAGE.value and variant == "full_bleed_cinematic":
                # Keep cold open clean full bleed with smooth push
                pass
            if s.motion.camera == "static":
                s.motion.camera = "slow_push"
            if s.motion.entrance in {"fade", "cut"}:
                s.motion.entrance = "mask_reveal"
            s.motion.speed = max(1.2, s.motion.speed)

        # Rule 1: Never show >2 consecutive static SmartImage scenes
        if scene_type == VisualSceneType.SMART_IMAGE.value and variant == "full_bleed_cinematic":
            consecutive_images += 1
            if consecutive_images > 2:
                # Modulate camera motion rather than applying letterboxes/vignettes over footage
                cam_moves = ["drift_right", "slow_push", "pan_left", "slow_pull"]
                s.motion.camera = cam_moves[(idx + consecutive_images) % len(cam_moves)]
                consecutive_images = 0
        else:
            consecutive_images = 0

        # Rule 2: Never repeat the exact same layout within previous 4 scenes
        if layout_key in recent_layouts[-4:]:
            variants = SCENE_TYPE_VARIANTS.get(scene_type, ())
            if variants:
                recent_vars = {
                    lk.split(":", 1)[1] for lk in recent_layouts[-4:] if lk.startswith(f"{scene_type}:")
                }
                candidates = [v for v in variants if v not in recent_vars and v != variant]
                if candidates:
                    s.primary_visual.variant = candidates[0]
                else:
                    alt = [v for v in variants if v != variant]
                    if alt:
                        s.primary_visual.variant = alt[idx % len(alt)]
            elif scene_type == VisualSceneType.STAT_CARD.value:
                s.primary_visual.variant = "kpi_metric_card" if variant == "hero_counter_large" else "hero_counter_large"
            elif scene_type == VisualSceneType.HEADLINE.value:
                s.primary_visual.variant = "digital_article_crop" if variant == "newspaper_front_page" else "newspaper_front_page"
            elif scene_type == VisualSceneType.QUOTE_CARD.value:
                s.primary_visual.variant = "quote_fragment_punch" if variant == "editorial_large_quote" else "editorial_large_quote"

            if isinstance(s.primary_visual.props, dict):
                s.primary_visual.props["variant"] = s.primary_visual.variant

        # Rule 2B: Disallow repeated adjacent graphic treatments unless explicitly continuing
        if validated:
            prev_s = validated[-1]
            prev_pv = prev_s.primary_visual
            prev_type = prev_pv.type
            prev_variant = prev_pv.variant
            is_non_media = scene_type not in {VisualSceneType.SMART_IMAGE.value, VisualSceneType.SMART_VIDEO.value, VisualSceneType.CHAPTER_TITLE.value}
            prev_non_media = prev_type not in {VisualSceneType.SMART_IMAGE.value, VisualSceneType.SMART_VIDEO.value, VisualSceneType.CHAPTER_TITLE.value}
            continuing = bool(
                getattr(s.pacing, "continuation", False)
                or getattr(s.pacing, "continuous_reveal", False)
                or getattr(prev_s.pacing, "continuation", False)
                or getattr(prev_s.pacing, "continuous_reveal", False)
            )
            if is_non_media and prev_non_media and not continuing:
                if scene_type == prev_type:
                    variants = SCENE_TYPE_VARIANTS.get(scene_type, ())
                    if variants:
                        alt_variants = [v for v in variants if v != prev_variant]
                        if alt_variants:
                            s.primary_visual.variant = alt_variants[(idx + 1) % len(alt_variants)]
                            if isinstance(s.primary_visual.props, dict):
                                s.primary_visual.props["variant"] = s.primary_visual.variant

                if scene_type == VisualSceneType.KINETIC_TEXT.value:
                    kinetic_templates = ("kinetic-statement", "keyword-reveal", "kinetic-text", "headline-center")
                    prev_tmpl = prev_s.template_id or ""
                    alt_tmpls = [t for t in kinetic_templates if t != prev_tmpl]
                    if alt_tmpls and (not s.template_id or s.template_id == prev_tmpl):
                        s.template_id = alt_tmpls[idx % len(alt_tmpls)]

        # Update layout tracking
        recent_layouts.append(f"{s.primary_visual.type}:{s.primary_visual.variant}")

        # Rule 3: Spacing dramatic transitions (prevent sensory fatigue, min 18 seconds apart)
        if s.transition_out in dramatic_transitions:
            if s.start - last_dramatic_transition_time < 18.0:
                s.transition_out = "hard_cut"
            else:
                last_dramatic_transition_time = s.start

        # Rule 4: Pacing engine adjustments based on beat metrics
        pacing = s.pacing
        if pacing.energy > 0.75:
            # High energy -> faster motion, tighter entrance
            s.motion.speed = 1.3
            s.motion.spring_stiffness = 160.0
            if s.motion.camera == "static":
                s.motion.camera = "slow_push"
        elif pacing.importance > 0.8:
            # High importance -> restrained, clean camera push
            s.motion.speed = 0.9
            s.motion.spring_stiffness = 100.0
            s.motion.camera = "slow_push"
        elif pacing.complexity > 0.75:
            # Complex concepts get slower camera drift to aid legibility
            s.motion.speed = 0.8
            s.motion.camera = "drift"

        # Rule 5: Do not promote ordinary media into generic cards. The LLM
        # director owns the frame grammar; media-only scenes are intentional
        # breathing room and must remain free of invented text.
        if scene_type in info_graphic_types:
            last_graphic_time = s.end

        # Give repeated scene types distinct movement. Pacing adjustments above
        # can otherwise collapse several energetic beats back to slow_push,
        # even when the director selected different frames and layouts.
        if scene_type != VisualSceneType.ANIMATED_MAP.value and s.motion.camera in recent_camera_moves[-2:]:
            camera_options = sorted(ALLOWED_CAMERAS - {"static"} - set(recent_camera_moves[-2:]))
            if camera_options:
                s.motion.camera = camera_options[idx % len(camera_options)]
        if scene_type in info_graphic_types and s.motion.entrance in recent_graphic_entrances[-2:]:
            entrance_options = [
                value for value in ("mask_reveal", "split_wipe", "slide_up", "scale_pop")
                if value in ALLOWED_ENTRANCES and value not in recent_graphic_entrances[-2:]
            ]
            if entrance_options:
                s.motion.entrance = entrance_options[idx % len(entrance_options)]
        recent_camera_moves.append(s.motion.camera)
        if scene_type in info_graphic_types:
            recent_graphic_entrances.append(s.motion.entrance)

        # Rule 6: Placement & variety governance for mass production
        quadrant_cycle = ["bottom_left", "top_right", "top_left", "bottom_right"]
        if scene_type == VisualSceneType.CHART.value:
            s.primary_visual.props["position"] = "center"
            s.primary_visual.props["background_blur"] = True
        elif scene_type in info_graphic_types or scene_type == VisualSceneType.CHAPTER_TITLE.value:
            available_quads = [q for q in quadrant_cycle if q != last_quadrant]
            assigned_pos = available_quads[idx % len(available_quads)]
            if not s.primary_visual.props.get("position"):
                s.primary_visual.props["position"] = assigned_pos
            last_quadrant = s.primary_visual.props.get("position")

        # Rule 7: There is intentionally no minimum graphic density. A
        # documentary can hold on a verified image or clip for several beats;
        # forcing a card here was the source of repetitive text treatment.

        validated.append(s)

    # Do not synthesize extra visual systems just to satisfy a type-count
    # target. Variety must come from the script and the director storyboard;
    # media-only holds remain valid editorial decisions.

    return validated


def enforce_scene_dict_grammar(scenes: Sequence[dict]) -> List[dict]:
    """Validate and automatically adjust a sequence of scene dictionaries for documentary grammar."""
    if not scenes:
        return []

    validated: List[dict] = []
    consecutive_images = 0
    recent_layouts: List[str] = []
    recent_cameras: List[str] = []
    last_quadrant = None

    image_variants = [
        "full_bleed_cinematic",
        "parallax_float",
        "spotlight_reveal",
    ]

    camera_rotations = [
        "slow_push",
        "pan_left",
        "drift",
        "pan_right",
        "focus_zoom",
        "cinematic_parallax",
    ]

    for idx, raw_scene in enumerate(scenes):
        s = copy.deepcopy(raw_scene)
        pv = s.get("primary_visual") or {}
        pv_type = str(pv.get("type") or "SmartImage")
        pv_variant = str(pv.get("variant") or "full_bleed_cinematic")

        # Camera anti-repetition: don't use identical camera as immediate predecessor
        cam = s.get("camera") or {}
        curr_motion = str(cam.get("motion") or "zoom_in").lower()
        if recent_cameras and curr_motion == recent_cameras[-1]:
            new_motion = camera_rotations[(idx + 1) % len(camera_rotations)]
            cam["motion"] = new_motion
            s["camera"] = cam
        recent_cameras.append(str(cam.get("motion") or "zoom_in").lower())

        # Rule 0: The 3-Second Rule (Cold Open Hook)
        if idx == 0:
            if pv_type.lower() == "smartimage" and pv_variant == "full_bleed_cinematic":
                pv["variant"] = "spotlight_reveal"
            if str(cam.get("motion") or "").lower() == "static":
                cam["motion"] = "slow_push"
            s["camera"] = cam

        # Rule 1: Never show >2 consecutive static SmartImage scenes with same variant
        if pv_type.lower() == "smartimage" and pv_variant == "full_bleed_cinematic":
            consecutive_images += 1
            if consecutive_images > 2:
                cam_moves = ["drift", "slow_push", "pan_left", "slow_pull"]
                cam["motion"] = cam_moves[(idx + consecutive_images) % len(cam_moves)]
                s["camera"] = cam
                consecutive_images = 0
        else:
            consecutive_images = 0

        # Rule 2: Never repeat the exact same layout within previous 3 scenes
        layout_key = f"{pv_type}:{pv.get('variant')}"
        if layout_key in recent_layouts[-3:]:
            variants = SCENE_TYPE_VARIANTS.get(pv_type, ())
            if not variants:
                for k, v in SCENE_TYPE_VARIANTS.items():
                    if k.lower() == pv_type.lower():
                        variants = v
                        break
            if variants:
                recent_vars = {
                    lk.split(":", 1)[1] for lk in recent_layouts[-3:] if lk.lower().startswith(f"{pv_type.lower()}:")
                }
                candidates = [v for v in variants if v not in recent_vars and v != pv_variant]
                if candidates:
                    pv["variant"] = candidates[0]
                else:
                    alt = [v for v in variants if v != pv_variant]
                    if alt:
                        pv["variant"] = alt[idx % len(alt)]
            elif pv_type == "SmartImage":
                pv["variant"] = image_variants[idx % len(image_variants)]
            elif pv_type == "StatCard":
                pv["variant"] = "kpi_metric_card" if pv_variant == "hero_counter_large" else "hero_counter_large"
            elif pv_type == "Headline":
                pv["variant"] = "digital_article_crop" if pv_variant == "newspaper_front_page" else "newspaper_front_page"
            elif pv_type == "QuoteCard":
                pv["variant"] = "quote_fragment_punch" if pv_variant == "editorial_large_quote" else "editorial_large_quote"
            elif pv_type == "Timeline":
                pv["variant"] = "date_flip_calendar" if pv_variant == "horizontal_milestone_trail" else "horizontal_milestone_trail"
            elif pv_type in {"TextCard", "text-card"}:
                text_styles = [
                    "vox-highlighter", "investigative-memo",
                    "broadsheet-callout", "frosted-glass", "brutalist-punch",
                ]
                pv["variant"] = text_styles[(idx + 1) % len(text_styles)]
                pv.setdefault("props", {})["style"] = pv["variant"]

            pv.setdefault("props", {})["variant"] = pv["variant"]

        # Rule 2B: Disallow repeated adjacent graphic treatments unless explicitly continuing
        if validated:
            prev_s = validated[-1]
            prev_pv = prev_s.get("primary_visual") or {}
            prev_type = str(prev_pv.get("type") or "SmartImage")
            prev_var = str(prev_pv.get("variant") or "")
            is_non_media = pv_type not in {"SmartImage", "SmartVideo", "ChapterTitle"}
            prev_non_media = prev_type not in {"SmartImage", "SmartVideo", "ChapterTitle"}
            prev_pacing = prev_s.get("pacing") or {}
            curr_pacing = s.get("pacing") or {}
            continuing = bool(
                curr_pacing.get("continuation")
                or curr_pacing.get("continuous_reveal")
                or prev_pacing.get("continuation")
                or prev_pacing.get("continuous_reveal")
            )
            if is_non_media and prev_non_media and not continuing:
                if pv_type.lower() == prev_type.lower():
                    variants = SCENE_TYPE_VARIANTS.get(pv_type, ())
                    if not variants:
                        for k, v in SCENE_TYPE_VARIANTS.items():
                            if k.lower() == pv_type.lower():
                                variants = v
                                break
                    if variants:
                        alt_variants = [v for v in variants if v.lower() != prev_var.lower()]
                        if alt_variants:
                            pv["variant"] = alt_variants[(idx + 1) % len(alt_variants)]
                            pv.setdefault("props", {})["variant"] = pv["variant"]

                if pv_type.lower() in {"kinetictext", "kinetic_text"}:
                    kinetic_templates = ("kinetic-statement", "keyword-reveal", "kinetic-text", "headline-center")
                    prev_tmpl = str(prev_s.get("template_id") or (prev_s.get("graphic") or {}).get("template_id") or "")
                    alt_tmpls = [t for t in kinetic_templates if t != prev_tmpl]
                    curr_tmpl = str(s.get("template_id") or (s.get("graphic") or {}).get("template_id") or "")
                    if alt_tmpls and (not curr_tmpl or curr_tmpl == prev_tmpl):
                        s["template_id"] = alt_tmpls[idx % len(alt_tmpls)]
                        if isinstance(s.get("graphic"), dict):
                            s["graphic"]["template_id"] = s["template_id"]

        # Rule 3: Placement variety for graphic scenes
        quadrant_cycle = ["bottom_left", "top_right", "top_left", "bottom_right"]
        pv_props = pv.setdefault("props", {})
        if pv_type == "Chart" or "chart" in str(s.get("graphic", {}).get("template_id", "")).lower():
            pv_props["position"] = "center"
            pv_props["background_blur"] = True
        elif pv_type in {"TextCard", "text-card"}:
            card_positions = ["bottom-left", "top-left", "bottom-right", "top-right", "center-left"]
            assigned_pos = card_positions[idx % len(card_positions)]
            if not pv_props.get("position") or pv_props.get("position") in {"bottom-left", "bottom_left"}:
                pv_props["position"] = assigned_pos
            last_quadrant = pv_props.get("position")
        elif pv_type not in {"SmartImage", "SmartVideo"}:
            available_quads = [q for q in quadrant_cycle if q != last_quadrant]
            assigned_pos = available_quads[idx % len(available_quads)]
            if not pv_props.get("position"):
                pv_props["position"] = assigned_pos
            last_quadrant = pv_props.get("position")

        s["primary_visual"] = pv
        recent_layouts.append(f"{pv.get('type')}:{pv.get('variant')}")
        validated.append(s)

    return validated
