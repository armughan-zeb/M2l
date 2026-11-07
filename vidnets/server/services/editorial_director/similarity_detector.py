"""Similarity Detector & Anti-Repetition Fingerprinting Engine.

Stores and compares edit fingerprints against a creator's past projects.
Guarantees videos do not look mass-produced, identical, or templated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
import hashlib
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .contract import EditPlan, VisualSceneType


@dataclass
class EditFingerprint:
    """Distinctive visual signature of a documentary edit."""
    project_id: str
    scene_sequence: List[str]  # e.g. ["ChapterTitle", "SmartImage", "Headline", "AnimatedMap"]
    layout_ids: List[str]      # e.g. ["full_bleed_cinematic", "newspaper_front_page"]
    transition_distribution: Dict[str, int]
    font_pair: List[str]       # [display, body, numbers]
    palette_signature: str    # Hex signature of primary/accent/bg
    motion_profile: List[str]  # Camera motions per scene
    asset_hashes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def compute_edit_fingerprint(edit_plan: EditPlan) -> EditFingerprint:
    """Derive the structural fingerprint of an EditPlan."""
    scene_sequence = []
    layout_ids = []
    motion_profile = []
    transition_dist: Dict[str, int] = {}
    asset_hashes = []

    for s in edit_plan.scenes:
        pv_type = s.primary_visual.type
        pv_variant = s.primary_visual.variant
        scene_sequence.append(pv_type)
        layout_ids.append(f"{pv_type}:{pv_variant}")
        motion_profile.append(s.motion.camera)

        t = s.transition_out
        transition_dist[t] = transition_dist.get(t, 0) + 1

        if s.primary_visual.asset and s.primary_visual.asset.get("src"):
            raw_src = str(s.primary_visual.asset["src"])
            asset_hashes.append(hashlib.md5(raw_src.encode("utf-8", errors="ignore")).hexdigest()[:8])

    palette = edit_plan.visual_dna.palette
    palette_sig = f"{palette.get('primary','')}:{palette.get('accent','')}:{palette.get('background','')}"
    typography = edit_plan.visual_dna.typography
    font_pair = [typography.get("display", ""), typography.get("body", ""), typography.get("numbers", "")]

    return EditFingerprint(
        project_id=str(edit_plan.title),
        scene_sequence=scene_sequence,
        layout_ids=layout_ids,
        transition_distribution=transition_dist,
        font_pair=font_pair,
        palette_signature=palette_sig,
        motion_profile=motion_profile,
        asset_hashes=asset_hashes,
    )


def sequence_similarity(seq_a: Sequence[str], seq_b: Sequence[str]) -> float:
    """Compute string-sequence alignment ratio between two sequences."""
    if not seq_a and not seq_b:
        return 1.0
    if not seq_a or not seq_b:
        return 0.0
    return SequenceMatcher(None, list(seq_a), list(seq_b)).ratio()


def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Compute Jaccard overlap between two discrete sets."""
    if not set_a and not set_b:
        return 1.0
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return len(set_a & set_b) / union


def check_similarity(
    current: EditFingerprint,
    past_fingerprints: Sequence[EditFingerprint],
    threshold: float = 0.70,
) -> Dict[str, Any]:
    """Compare current edit against history. Returns flagged warnings and max score."""
    if not past_fingerprints:
        return {"passed": True, "max_similarity": 0.0, "flagged_project": None, "reasons": []}

    max_sim = 0.0
    flagged_project = None
    reasons = []

    for past in past_fingerprints:
        seq_sim = sequence_similarity(current.scene_sequence, past.scene_sequence)
        layout_sim = sequence_similarity(current.layout_ids, past.layout_ids)
        camera_sim = sequence_similarity(current.motion_profile, past.motion_profile)
        has_assets = bool(current.asset_hashes or past.asset_hashes)
        if has_assets:
            asset_overlap = jaccard_similarity(set(current.asset_hashes), set(past.asset_hashes))
            composite = (seq_sim * 0.35) + (layout_sim * 0.35) + (camera_sim * 0.15) + (asset_overlap * 0.15)
        else:
            composite = (seq_sim * 0.425) + (layout_sim * 0.425) + (camera_sim * 0.15)

        if composite > max_sim:
            max_sim = composite
            flagged_project = past.project_id

        if composite >= threshold:
            reasons.append(
                f"Project '{past.project_id}' has {composite*100:.1f}% structural similarity "
                f"(scenes: {seq_sim*100:.0f}%, layouts: {layout_sim*100:.0f}%)"
            )

    return {
        "passed": max_sim < threshold,
        "max_similarity": round(max_sim, 3),
        "flagged_project": flagged_project,
        "reasons": reasons,
    }


def reroll_variations_if_similar(
    edit_plan: EditPlan,
    past_fingerprints: Sequence[EditFingerprint],
    threshold: float = 0.70,
    max_attempts: int = 5,
) -> EditPlan:
    """Iteratively adjusts scene layouts, camera paths, and transitions if edit is too similar to past projects."""
    if not past_fingerprints:
        return edit_plan

    fp = compute_edit_fingerprint(edit_plan)
    check = check_similarity(fp, past_fingerprints, threshold)
    if check["passed"]:
        return edit_plan

    import copy
    import random

    plan_copy = copy.deepcopy(edit_plan)
    seed = plan_copy.seed

    for attempt in range(1, max_attempts + 1):
        rng = random.Random(seed + attempt * 1007)

        # Reroll camera motions and transitions on alternate scenes
        for idx, s in enumerate(plan_copy.scenes):
            if idx % 2 == attempt % 2:
                cameras = ["slow_push", "pan_left", "pan_right", "pull_out", "focus_zoom", "drift"]
                s.motion.camera = rng.choice(cameras)

                transitions = ["hard_cut", "cross_dissolve", "directional_slide_left", "wipe_left"]
                s.transition_out = rng.choice(transitions)

        new_fp = compute_edit_fingerprint(plan_copy)
        new_check = check_similarity(new_fp, past_fingerprints, threshold)
        if new_check["passed"]:
            plan_copy.metrics["similarity_reroll_attempts"] = attempt
            plan_copy.metrics["original_max_similarity"] = check["max_similarity"]
            plan_copy.metrics["resolved_similarity"] = new_check["max_similarity"]
            return plan_copy

    return plan_copy
