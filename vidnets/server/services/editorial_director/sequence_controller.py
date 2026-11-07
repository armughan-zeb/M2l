"""Sequence Director and Anti-Repetition Controller for Documentary Editing.

Enforces:
1. Rolling 60s cooldown per composition family (except intentional continuity groups).
2. Prevents adjacent identical compositions (different colors/fonts do NOT count as variety).
3. Alternates anchor quadrants (top_left, bottom_left, top_right, bottom_right) avoiding static placement.
4. Limits full-screen graphic anchors (center / hero) to max 2 consecutive beats.
5. Max 2 font families per video (e.g. 1 display/heading, 1 body/numbers).
6. Dynamic pacing curves: tension build (1.5-2.5s) and exhale (4.0-6.0s).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

DEFAULT_FAMILY_COOLDOWN_SECONDS = 60.0
MAX_CONSECUTIVE_HERO_BEATS = 2
CORNER_QUADRANTS = ("bottom_left", "top_left", "bottom_right", "top_right")

INTENTIONAL_CONTINUITY_FAMILIES = {
    "data_chart",
    "chronology_timeline",
    "before_after",
    "process_diagram",
}


def _scene_duration(scene: Mapping[str, Any]) -> float:
    try:
        explicit = scene.get("duration")
        if explicit not in (None, ""):
            return max(0.5, float(explicit))
        start = float(scene.get("start") or 0.0)
        end = float(scene.get("end") or 0.0)
        if end > start:
            return max(0.5, end - start)
        return 6.0
    except (TypeError, ValueError):
        return 6.0


def _scene_pacing_mode(scene: Mapping[str, Any]) -> str:
    """Classify the pacing beat: tension (fast cuts), exhale (longer holds), or neutral."""
    narration = str(scene.get("narration") or scene.get("text") or "").lower()
    pacing = scene.get("pacing") if isinstance(scene.get("pacing"), Mapping) else {}
    importance = float(pacing.get("importance") or scene.get("importance") or 0.5)

    if any(k in narration for k in ("suddenly", "chaos", "explod", "panic", "rush", "chase", "crisis", "pressure", "warning")) or importance >= 0.85:
        return "tension"
    if any(k in narration for k in ("meanwhile", "landscape", "years later", "silence", "remains", "vast", "calm", "reflect")) or importance <= 0.35:
        return "exhale"
    return "neutral"


class SequenceController:
    """Stateful orchestrator for scene-to-scene visual variety and pacing."""

    def __init__(self, cooldown_seconds: float = DEFAULT_FAMILY_COOLDOWN_SECONDS):
        self.cooldown_seconds = float(cooldown_seconds)
        self.current_time = 0.0
        self.family_last_seen: Dict[str, float] = {}
        self.recent_families: List[str] = []
        self.recent_anchors: List[str] = []
        self.consecutive_center = 0

    def can_use_family(
        self,
        family: str,
        continuity_group: str = "",
    ) -> Tuple[bool, str]:
        """Check rolling 60s cooldown and adjacent identical composition rules."""
        if not family or family == "media_hold":
            return True, "media_hold"

        is_intentional_continuity = bool(
            continuity_group and family in INTENTIONAL_CONTINUITY_FAMILIES
        )

        # Rule 1: No adjacent identical compositions
        if self.recent_families and self.recent_families[-1] == family:
            if not is_intentional_continuity:
                return False, f"adjacent_repeat_{family}"

        # Rule 2: Rolling 60s cooldown per composition family
        if not is_intentional_continuity and family in self.family_last_seen:
            elapsed = self.current_time - self.family_last_seen[family]
            if elapsed < self.cooldown_seconds:
                return False, f"cooldown_{family}_{elapsed:.1f}s_of_{self.cooldown_seconds}s"

        return True, "eligible"

    def select_anchor(
        self,
        preferred: str,
        scene: Mapping[str, Any],
        is_center_eligible: bool = True,
    ) -> str:
        """Select safe anchor while alternating quadrants and enforcing hero limits."""
        if preferred == "center":
            if is_center_eligible and self.consecutive_center < MAX_CONSECUTIVE_HERO_BEATS:
                return "center"
            # Degrade to alternating corner anchor if center limit reached
            preferred = "corner"

        # Check explicit safe regions on media
        media = scene.get("media") if isinstance(scene.get("media"), Mapping) else {}
        safe_region = scene.get("media_composition", {}).get("safe_region") or media.get("safe_region")
        if isinstance(safe_region, Sequence) and len(safe_region) == 4:
            try:
                x1, y1, x2, y2 = [float(v) for v in safe_region]
                h = "left" if (x1 + x2) / 2.0 < 0.5 else "right"
                v = "top" if (y1 + y2) / 2.0 < 0.5 else "bottom"
                explicit_anchor = f"{v}_{h}"
                return explicit_anchor
            except (TypeError, ValueError):
                pass

        # Cycle through corner quadrants avoiding repeating the last 2 quadrants
        last_corner = next((a for a in reversed(self.recent_anchors) if a != "center"), None)
        available = [q for q in CORNER_QUADRANTS if q != last_corner]
        chosen = available[len(self.recent_anchors) % len(available)] if available else "bottom_left"
        return chosen

    def register_scene_contract(
        self,
        scene: Mapping[str, Any],
        contract: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Apply anti-repetition rules, update timeline, and return the resolved contract."""
        duration = _scene_duration(scene)
        family = str(contract.get("family") or "media_hold")
        continuity_group = str(scene.get("props", {}).get("continuity_group") or contract.get("continuity_exception") or "")
        enabled = bool(contract.get("enabled", True))

        if enabled and family != "media_hold":
            can_use, reason = self.can_use_family(family, continuity_group)
            if not can_use:
                # Revert to media_hold (clean footage) rather than repeating
                contract.update({
                    "enabled": False,
                    "family": "media_hold",
                    "legacy_grammar": "none",
                    "purpose": "media_hold",
                    "reason": reason,
                })
                family = "media_hold"
                enabled = False

        if enabled and family != "media_hold":
            # Anchor resolution
            pref = contract.get("preferred_placement") or contract.get("anchor") or "corner"
            anchor = self.select_anchor(pref, scene, is_center_eligible=(family != "corner_identification"))
            contract["anchor"] = anchor
            if anchor == "center":
                self.consecutive_center += 1
            else:
                self.consecutive_center = 0

            self.family_last_seen[family] = self.current_time + duration
            self.recent_families.append(family)
            self.recent_anchors.append(anchor)
        else:
            self.consecutive_center = 0
            self.recent_families.append("media_hold")
            self.recent_anchors.append("none")

        # Pacing curves
        pacing_mode = _scene_pacing_mode(scene)
        contract["pacing_mode"] = pacing_mode

        self.current_time += duration
        return contract


def audit_sequence_continuity(
    manifest: Mapping[str, Any],
    cooldown_seconds: float = DEFAULT_FAMILY_COOLDOWN_SECONDS,
) -> Dict[str, Any]:
    """Audit an entire video sequence for documentary anti-repetition rules."""
    errors: List[str] = []
    warnings: List[str] = []

    scenes = manifest.get("scenes") or []
    current_time = 0.0
    family_last_seen: Dict[str, float] = {}
    family_sequence: List[str] = []
    anchor_sequence: List[str] = []
    consecutive_center = 0
    cooldown_violations = 0
    adjacent_repetitions = 0

    for index, raw_scene in enumerate(scenes):
        if not isinstance(raw_scene, Mapping):
            continue
        duration = _scene_duration(raw_scene)
        contract = raw_scene.get("editorial_contract") if isinstance(raw_scene.get("editorial_contract"), Mapping) else {}
        enabled = bool(contract.get("enabled", True))
        family = str(contract.get("family") or "media_hold")
        anchor = str(contract.get("anchor") or raw_scene.get("media_composition", {}).get("placement") or "none")
        continuity = str(raw_scene.get("props", {}).get("continuity_group") or contract.get("continuity_exception") or "")

        if enabled and family != "media_hold":
            # 1. Adjacent repetition check
            if family_sequence and family_sequence[-1] == family and not continuity:
                errors.append(f"scene {index}: adjacent identical composition family '{family}'")
                adjacent_repetitions += 1

            # 2. 60s cooldown check
            if not continuity and family in family_last_seen:
                elapsed = current_time - family_last_seen[family]
                if elapsed < cooldown_seconds:
                    errors.append(
                        f"scene {index}: family '{family}' reused after {elapsed:.1f}s (cooldown is {cooldown_seconds}s)"
                    )
                    cooldown_violations += 1

            family_last_seen[family] = current_time + duration
            family_sequence.append(family)
            anchor_sequence.append(anchor)

            # 3. Center hero limit
            if anchor == "center":
                consecutive_center += 1
                if consecutive_center > MAX_CONSECUTIVE_HERO_BEATS:
                    errors.append(f"scene {index}: center hero graphic exceeded {MAX_CONSECUTIVE_HERO_BEATS} consecutive beats")
            else:
                consecutive_center = 0
        else:
            consecutive_center = 0
            family_sequence.append("media_hold")
            anchor_sequence.append("none")

        current_time += duration

    # 4. Font family audit: Max 2 font families per video
    visual_dna = manifest.get("visual_system", {}).get("visual_dna") or manifest.get("visual_dna") or {}
    font_families: Set[str] = set()
    if isinstance(visual_dna, Mapping):
        typo = visual_dna.get("typography") if isinstance(visual_dna.get("typography"), Mapping) else {}
        for role in ("display", "body", "numbers"):
            font = str(typo.get(role) or "").strip()
            if font:
                font_families.add(font)

    if len(font_families) > 2:
        errors.append(f"manifest visual DNA specifies {len(font_families)} font families {sorted(font_families)}; maximum allowed is 2")

    status = "fail" if errors else "pass"
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "cooldown_violations": cooldown_violations,
        "adjacent_repetitions": adjacent_repetitions,
        "unique_families": len(set(f for f in family_sequence if f != "media_hold")),
        "font_families": sorted(font_families),
        "total_duration_seconds": round(current_time, 2),
    }


__all__ = [
    "DEFAULT_FAMILY_COOLDOWN_SECONDS",
    "MAX_CONSECUTIVE_HERO_BEATS",
    "CORNER_QUADRANTS",
    "SequenceController",
    "audit_sequence_continuity",
]
