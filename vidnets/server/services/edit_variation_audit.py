"""Structural anti-repetition audit for one edit and optional channel history.

This is a quality signal, not a promise about platform monetization.  It catches
mechanical edit fingerprints before render while preserving deliberate hard
cuts and full-bleed documentary footage.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Mapping, Sequence


def _ratio(counter: Counter, total: int) -> tuple[str, float]:
    if not counter or total <= 0:
        return "", 0.0
    key, count = counter.most_common(1)[0]
    return str(key), round(count / total, 4)


def audit_edit_variation(
    scenes: Sequence[Mapping[str, Any]],
    *,
    design_fingerprint: str = "",
    history_fingerprints: Sequence[str] = (),
    grounded_cue_mode: bool = False,
) -> Dict[str, Any]:
    rows = list(scenes or [])
    template_sequence = [
        str((scene.get("graphic") or {}).get("template_id") or "")
        for scene in rows
    ]
    designed = [
        template_id
        for template_id in template_sequence
        if template_id not in {"", "full-bleed", "lower-third"}
    ]
    transitions = [str((scene.get("transition") or {}).get("template_id") or "cut") for scene in rows]
    non_cut = [item for item in transitions if item != "cut"]
    treatments = [str((scene.get("design") or {}).get("image_treatment") or "hold") for scene in rows]
    structures = [
        str((scene.get("design") or {}).get("visual_structure") or "")
        for scene in rows
    ]
    overlays = [layer for scene in rows for layer in ((scene.get("atmosphere") or {}).get("layers") or [])]
    sounds = [str(cue.get("kind") or "") for scene in rows for cue in (scene.get("sound_cues") or []) if cue.get("kind")]
    graphic_count = sum(
        str((scene.get("graphic") or {}).get("template_id") or "") != "full-bleed"
        for scene in rows
    )
    atmosphere_count = sum(bool((scene.get("atmosphere") or {}).get("layers")) for scene in rows)
    template_id, template_ratio = _ratio(Counter(designed), len(designed))
    transition_id, transition_ratio = _ratio(Counter(non_cut), len(non_cut))
    treatment_id, treatment_ratio = _ratio(Counter(treatments), len(treatments))
    sound_id, sound_ratio = _ratio(Counter(sounds), len(sounds))
    # Full-bleed breathing shots separate two designed graphics. Do not remove
    # them before checking adjacency or the audit reports false repetitions.
    adjacent_template_repeats = sum(
        left == right and left not in {"", "full-bleed", "lower-third"}
        for left, right in zip(template_sequence, template_sequence[1:])
    )
    adjacent_transition_repeats = sum(left == right and left != "cut" for left, right in zip(transitions, transitions[1:]))
    adjacent_structure_repeats = sum(
        left_structure == right_structure
        and left_structure not in {"", "media", "annotation"}
        and left_template == right_template
        for left_structure, right_structure, left_template, right_template in zip(
            structures,
            structures[1:],
            template_sequence,
            template_sequence[1:],
        )
    )
    quantitative_chart_count = sum(item == "quantitative_chart" for item in structures)
    diagram_count = sum(item == "concept_diagram" for item in structures)
    warnings: list[str] = []
    # Short projects can legitimately have four uses of a supporting grammar;
    # adjacency and the hard per-template scheduler cap still prevent loops.
    if template_ratio > 0.50 and len(designed) >= 8:
        warnings.append(f"template_concentration:{template_id}")
    if transition_ratio > 0.42 and len(non_cut) >= 6:
        warnings.append(f"transition_concentration:{transition_id}")
    if treatment_id != "hold" and treatment_ratio > 0.48 and len(treatments) >= 8:
        warnings.append(f"image_treatment_concentration:{treatment_id}")
    if len(rows) >= 8 and len(set(treatments)) < 3:
        warnings.append("image_treatment_variety")
    if len(rows) >= 8 and len(non_cut) < max(2, len(rows) // 8):
        warnings.append("transition_scarcity")
    if sound_ratio > 0.24 and len(sounds) >= 10:
        warnings.append(f"sound_concentration:{sound_id}")
    if adjacent_template_repeats:
        warnings.append("adjacent_template_repeat")
    if adjacent_transition_repeats > max(1, len(rows) // 12):
        warnings.append("adjacent_transition_repeat")
    if adjacent_structure_repeats:
        warnings.append("adjacent_visual_structure_repeat")
    if len(rows) >= 10 and quantitative_chart_count / len(rows) > 0.12:
        warnings.append("quantitative_chart_overuse")
    if len(rows) >= 10 and diagram_count / len(rows) > 0.16:
        warnings.append("concept_diagram_overuse")
    if len(rows) >= 9:
        graphic_ratio = graphic_count / len(rows)
        # Coverage default is now 0.55 (clamp 0.30-0.68): the band tracks the
        # higher default instead of the old 0.40 target so long-form jobs do
        # not trip the variation gate for producing enough text cards.
        # Grounded Cue V2 deliberately emits text only when an exact script
        # span and Whisper window exist.  Its density is a fact of the script,
        # not an editing failure; forcing the legacy 47% floor would require
        # invented or subtitle-like cards.
        if not grounded_cue_mode and not 0.47 <= graphic_ratio <= 0.63:
            warnings.append(f"graphic_coverage:{graphic_ratio:.2f}")
        if atmosphere_count != len(rows):
            warnings.append("missing_scene_atmosphere")
    channel_collision = bool(design_fingerprint and design_fingerprint in set(history_fingerprints or []))
    if channel_collision:
        warnings.append("channel_fingerprint_collision")
    unique_visual_grammars = len({
        (
            str((scene.get("graphic") or {}).get("template_id") or ""),
            int((scene.get("graphic") or {}).get("variant") or 0),
            str((scene.get("design") or {}).get("image_treatment") or ""),
            tuple((scene.get("atmosphere") or {}).get("layers") or []),
        )
        for scene in rows
    })
    return {
        "status": "needs_regeneration" if warnings else "pass",
        "warnings": warnings,
        "scene_count": len(rows),
        "unique_visual_grammars": unique_visual_grammars,
        "designed_template_unique": len(set(designed)),
        "transition_unique": len(set(transitions)),
        "image_treatment_unique": len(set(treatments)),
        "visual_structure_unique": len({item for item in structures if item}),
        "overlay_layer_unique": len(set(overlays)),
        "sound_unique": len(set(sounds)),
        "graphic_scene_count": graphic_count,
        "graphic_scene_ratio": round(graphic_count / max(1, len(rows)), 4),
        "graphic_density_policy": "grounded_cues" if grounded_cue_mode else "coverage_band",
        "atmosphere_scene_count": atmosphere_count,
        "quantitative_chart_count": quantitative_chart_count,
        "concept_diagram_count": diagram_count,
        "concentration": {
            "template": {"id": template_id, "ratio": template_ratio},
            "transition_non_cut": {"id": transition_id, "ratio": transition_ratio},
            "image_treatment": {"id": treatment_id, "ratio": treatment_ratio},
            "sound": {"id": sound_id, "ratio": sound_ratio},
        },
        "channel_fingerprint_collision": channel_collision,
    }
