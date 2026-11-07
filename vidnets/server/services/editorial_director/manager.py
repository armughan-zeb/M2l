"""Manager agent for the documentary visual pipeline.

The manager is intentionally deterministic at render time: the LLM director
may propose a plan, but the manager owns the non-negotiable production rules
and turns critic findings into concrete revision instructions.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Dict, Mapping

from .visual_critic import audit_manifest
from .media_director import audit_media_compositions


MANAGER_RULES = (
    "Every semantic visual must have a grounded payload.",
    "Use advanced compositional grammars instead of generic centered cards.",
    "Do not repeat a grammar or motion verb in adjacent scenes.",
    "Keep the sequence media-led enough to breathe; graphics are emphasis, not wallpaper.",
    "Primary explanatory maps, charts, diagrams, timelines, comparisons and documents own the canvas; brief supporting graphics protect source media.",
    "Never let a graphic cover a verified subject or exceed the overlay area/opacity budget.",
    "Use modern, readable typography and keep text within the safe area.",
    "The documentary swarm must pass its grammar, motion, media, and collision audit before a long render.",
)


def _text_free_fallback(scene: Dict[str, Any], primary: Mapping[str, Any]) -> str:
    media = scene.get("media") or primary.get("asset") or {}
    kind = "SmartVideo" if str(media.get("type") or "").startswith("video") or media.get("is_video") else "SmartImage"
    scene["primary_visual"] = {"type": kind, "variant": "full_bleed_cinematic", "props": {}, "asset": primary.get("asset")}
    scene["text"] = {**(scene.get("text") or {}), "overlay_policy": "none", "display_text": "", "frame_mode": "media"}
    scene["editorial_contract"] = {"version": 4, "approved": False, "enabled": False, "display_text": "", "emphasis_spans": []}
    scene["display_text"] = ""
    scene["emphasis_spans"] = []
    scene["template_recipe"] = None
    return f"{kind}:full_bleed_cinematic"


def apply_manager_corrections(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a corrected copy for safe semantic failures.

    Corrections are deliberately conservative: when a factual payload is
    missing, use grounded typography instead of inventing data; when an
    identical signature repeats, select a different closed-vocabulary variant.
    Rendered-media failures still require an asset replacement or a new plan.
    """
    corrected = deepcopy(dict(manifest))
    scenes = corrected.get("scenes") if isinstance(corrected.get("scenes"), list) else []
    corrections = []
    alternate_variants = {
        "Chart:editorial_bar": "ranked_horizontal_bars",
        "Timeline:date_flip_calendar": "horizontal_milestone_trail",
        "TextCard:field-caption": "broadsheet-callout",
        "KineticText:multi_line_build": "staggered_lines",
    }
    previous_signature = ""
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        primary = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), dict) else {}
        props = primary.get("props") if isinstance(primary.get("props"), dict) else {}
        kind = str(primary.get("type") or "")
        variant = str(primary.get("variant") or "")
        signature = f"{kind}:{variant}"
        factual_keys = ("value", "stat", "number", "data", "values", "currentValue", "before", "after")
        payload_present = any(
            (isinstance(props.get(key), (int, float)) and not isinstance(props.get(key), bool) and math.isfinite(props[key]))
            or (isinstance(props.get(key), (str, list, dict)) and bool(props[key]))
            for key in factual_keys
        )
        if kind in {"Chart", "StatCard"} and not payload_present:
            replacement = _text_free_fallback(scene, primary)
            corrections.append({"scene_index": index, "from": signature, "to": replacement, "reason": "missing_factual_payload"})
            signature = replacement
        elif kind == "Timeline" and not any(props.get(key) for key in ("date", "year", "events", "eraSpan")):
            replacement = _text_free_fallback(scene, primary)
            corrections.append({"scene_index": index, "from": signature, "to": replacement, "reason": "missing_chronology_payload"})
            signature = replacement
        elif signature == previous_signature:
            next_variant = alternate_variants.get(signature)
            if next_variant:
                scene["primary_visual"]["variant"] = next_variant
                corrections.append({"scene_index": index, "from": signature, "to": f"{kind}:{next_variant}", "reason": "adjacent_signature_repeat"})
                signature = f"{kind}:{next_variant}"
        previous_signature = signature
    corrected["manager_corrections"] = corrections
    return corrected


def manager_review(manifest: Mapping[str, Any], frame_report: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """Audit a director plan and return pass/fail plus revision instructions."""
    report = audit_manifest(manifest)
    media_report = audit_media_compositions(manifest)
    from .production_contract import audit_production_requirements
    production_report = audit_production_requirements(manifest)
    correction_preview = apply_manager_corrections(manifest)
    instructions = []
    for finding in report.get("findings", []):
        code = finding.get("code")
        if code == "data_without_payload":
            instructions.append("Director: provide a grounded payload with source-backed values or use a text-free media treatment; never invent a chart or rebuild a narration card.")
        elif code == "chronology_without_date":
            instructions.append("Director: provide a source-backed date/event or choose cinematic_type.")
        elif code == "adjacent_grammar_repeat":
            instructions.append("Grammar selector: choose a different visual grammar and motion verb for adjacent scenes.")
        elif code == "graphic_wallpaper_risk":
            instructions.append("Pacing editor: restore a media-led or quiet beat before the full render.")
        elif code == "spatial_visual_is_abstract":
            instructions.append("Spatial designer: keep the scene as a truthful locator/relationship field; do not draw a fake map.")
        elif code == "director_grammar_underdiversity":
            instructions.append("Director: revise the grounded date/process/data/place opportunities into distinct specialist Remotion scenes; keep atmospheric beats media-led and preserve the full story context.")
        elif code == "uncomposed_graphic_fallback":
            instructions.append("Director: this slot has no verified source media. Replace the generic fallback plate with a content-earned Remotion explanation when the narration supports one; otherwise flag the degraded media state clearly.")
    if media_report.get("status") == "fail":
        instructions.append("Media director: primary explanations own their canvas; repair supporting-overlay safe regions and preserve verified subjects.")
    swarm_report = manifest.get("swarm_audit") if isinstance(manifest.get("swarm_audit"), Mapping) else {}
    swarm_sequence = swarm_report.get("sequence") if isinstance(swarm_report.get("sequence"), Mapping) else {}
    swarm_investigators = swarm_report.get("investigators") if isinstance(swarm_report.get("investigators"), Mapping) else {}
    if swarm_sequence.get("status") == "fail":
        instructions.append("Swarm manager: resolve adjacent high-salience repeats before approving the long render.")
    if any("variety" in str(item).lower() or "cooldown" in str(item).lower() for item in swarm_sequence.get("warnings", []) if isinstance(swarm_sequence, Mapping)):
        instructions.append("Swarm manager: widen the graphic grammar palette and enforce a three-scene cooldown before reusing a motion family or visual grammar.")
    if swarm_investigators.get("status") == "fail":
        instructions.append("Investigative agents: reject the director plan until every graphic is script-earned, every map has real endpoints, and every chart has grounded data.")
    if isinstance(frame_report, Mapping) and frame_report.get("status") == "fail":
        instructions.append("Vision critic: reject the long render until low-information frames are replaced or the scene is converted to a grounded graphic.")
    if isinstance(frame_report, Mapping) and frame_report.get("dark_frames", 0):
        instructions.append("Vision critic: lift the grade and remove generic black/blurred stages; keep darkness only where the script earns a transition or investigative beat.")
    if isinstance(frame_report, Mapping) and frame_report.get("soft_frames", 0):
        instructions.append("Vision critic: keep source media sharp; allow blur only behind a centered chart or diagram that needs legibility.")
    ready = (
        report.get("status") == "pass"
        and media_report.get("status") == "pass"
        and production_report.get("status") == "pass"
        and swarm_report.get("status", "pass") == "pass"
        and not (isinstance(frame_report, Mapping) and frame_report.get("status") == "fail")
    )
    return {
        "status": "fail" if not ready else "pass",
        "rules": list(MANAGER_RULES),
        "critic": report,
        "media_critic": media_report,
        "production_critic": production_report,
        "revision_instructions": list(dict.fromkeys(instructions)),
        "correction_count": len(correction_preview.get("manager_corrections") or []),
        "corrections": correction_preview.get("manager_corrections") or [],
        "ready_for_long_render": ready,
    }


def execute_reviewer_council(
    manifest: Mapping[str, Any],
    frame_paths: Any = None,
) -> Dict[str, Any]:
    """Execute the 5-role Accountable Reviewer Council before approving final render."""
    scenes = list(manifest.get("scenes") or [])

    # 1. Lead Editor: narrative arc, duration, dynamic pacing curves
    pacing_modes = []
    hold_violations = []
    for idx, s in enumerate(scenes):
        contract = s.get("editorial_contract") if isinstance(s.get("editorial_contract"), Mapping) else {}
        pacing_modes.append(contract.get("pacing_mode") or "neutral")
        timing = contract.get("timing") or {}
        if timing.get("enabled"):
            hold = float(timing.get("hold_seconds") or 0.0)
            if hold < 1.3:
                hold_violations.append(f"scene {idx}: hold time {hold:.2f}s is below minimum 1.3s floor")
    lead_editor_status = "pass" if not hold_violations else "fail"
    lead_editor = {
        "role": "lead_editor",
        "status": lead_editor_status,
        "scene_count": len(scenes),
        "pacing_distribution": {mode: pacing_modes.count(mode) for mode in set(pacing_modes)},
        "hold_violations": hold_violations,
    }

    # 2. Cinematographer: media dominance, subject framing, safe regions
    media_report = audit_media_compositions(manifest)
    cinematographer_status = "pass" if media_report.get("status") == "pass" else "fail"
    cinematographer = {
        "role": "cinematographer",
        "status": cinematographer_status,
        "media_led_ratio": media_report.get("media_first_ratio"),
        "safe_region_coverage": media_report.get("safe_region_compliance"),
        "measurement": "automated manifest/layout check; no human or model review",
        "findings": media_report.get("findings", []),
    }

    # 3. Graphics Director: 16 composition families, anti-repetition, 60s cooldowns, max 2 fonts
    from .sequence_controller import audit_sequence_continuity
    seq_audit = audit_sequence_continuity(manifest)
    graphics_director_status = "pass" if seq_audit.get("status") == "pass" else "fail"
    graphics_director = {
        "role": "graphics_director",
        "status": graphics_director_status,
        "unique_families": seq_audit.get("unique_families", 0),
        "font_families": seq_audit.get("font_families", []),
        "cooldown_violations": seq_audit.get("cooldown_violations", 0),
        "adjacent_repetitions": seq_audit.get("adjacent_repetitions", 0),
        "errors": seq_audit.get("errors", []),
    }

    # 4. Evidence Reviewer: factual grounding, no invented numbers/dates/locations
    evidence_errors = []
    for idx, s in enumerate(scenes):
        contract = s.get("editorial_contract") if isinstance(s.get("editorial_contract"), Mapping) else {}
        if contract.get("enabled"):
            family = contract.get("family")
            text = contract.get("display_text") or ""
            if family == "numeric_emphasis":
                if not any(ch.isdigit() for ch in text):
                    evidence_errors.append(f"scene {idx}: numeric emphasis graphic has no numbers")
            elif family == "locator_route":
                loc = contract.get("location") or {}
                if not (loc.get("from") or loc.get("location") or loc.get("points")):
                    evidence_errors.append(f"scene {idx}: locator graphic missing grounded location payload")
    evidence_status = "pass" if not evidence_errors else "fail"
    evidence_reviewer = {
        "role": "evidence_reviewer",
        "status": evidence_status,
        "errors": evidence_errors,
    }

    # 5. Visual Critic: frame inspection
    from .visual_critic import audit_frames, audit_manifest
    manifest_audit = audit_manifest(manifest)
    from .production_contract import audit_production_requirements
    production_audit = audit_production_requirements(manifest)
    frame_audit = audit_frames(frame_paths) if frame_paths else None
    critic_errors = manifest_audit.get("errors", 0)
    critic_errors += len(production_audit.get("errors") or [])
    if frame_audit and frame_audit.get("status") == "fail":
        critic_errors += 1
    visual_critic = {
        "role": "visual_critic",
        "status": "pass" if critic_errors == 0 else "fail",
        "manifest_audit": manifest_audit,
        "production_audit": production_audit,
        "frame_audit": frame_audit,
    }

    all_pass = all(
        role["status"] == "pass"
        for role in (lead_editor, cinematographer, graphics_director, evidence_reviewer, visual_critic)
    )

    return {
        "status": "pass" if all_pass else "fail",
        "council": {
            "lead_editor": lead_editor,
            "cinematographer": cinematographer,
            "graphics_director": graphics_director,
            "evidence_reviewer": evidence_reviewer,
            "visual_critic": visual_critic,
        },
        "ready_for_long_render": all_pass,
    }


__all__ = [
    "MANAGER_RULES",
    "apply_manager_corrections",
    "manager_review",
    "execute_reviewer_council",
]
