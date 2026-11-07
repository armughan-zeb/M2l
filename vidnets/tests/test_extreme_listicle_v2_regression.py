"""Permanent regression for the 63-scene listicle source-mix failure."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services.media_resolution_coordinator import resolve_storyboard_media_v2  # noqa: E402
from services.storyboard_v2 import build_script_plan  # noqa: E402


def _extreme_jobs_script() -> str:
    return "\n".join(
        (
            f"Number {item}: Job Subject {item}. "
            f"Job Subject {item} opens the task carefully. "
            f"Job Subject {item} performs the central action."
        )
        for item in range(1, 22)
    )


def test_63_scene_listicle_keeps_items_isolated_and_resolves_the_planned_primary_mix():
    plan = build_script_plan(
        _extreme_jobs_script(),
        content_title="Twenty One Extreme Jobs",
        format_hint="listicle",
        scene_mode="Every 2 Beats",
    )
    assert len(plan.scenes) == 63
    assert len(plan.leaf_containers) == 21

    scenes = [scene.to_dict() for scene in plan.scenes]
    youtube_indexes = set(range(25))
    stock_calls: list[str] = []

    def candidates(scene, source):
        index = int(scene["scene_index"])
        wanted = (source == "youtube" and index in youtube_indexes) or (
            source == "web" and index not in youtube_indexes
        )
        if not wanted:
            return []
        return [
            {
                "candidate_id": f"decoy:{source}:{index}",
                "path": f"decoy-{source}-{index}",
                "source": source,
                "container_id": "a-different-list-item",
                "score": 99,
            },
            {
                "candidate_id": f"correct:{source}:{index}",
                "path": f"correct-{source}-{index}",
                "source": source,
                "container_id": scene["container_id"],
                "score": 80,
            },
        ]

    resolutions = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=lambda scene, _cancel: candidates(scene, "youtube"),
        web_lane=lambda scene, _cancel: candidates(scene, "web"),
        stock_lane=lambda scene, _cancel: stock_calls.append(scene["scene_id"]),
        visual_verifier=lambda _scene, candidate: {
            "accepted": True,
            "candidate_id": candidate["candidate_id"],
            "identity_score": 95,
            "action_score": 90,
            "context_score": 95,
            "quality": 90,
            "final_score": 93,
            "entity_conflict": False,
        },
        neutral_factory=lambda _scene: None,
        max_workers=6,
    )

    assert len(resolutions) == 63
    assert all(row.status == "accepted" for row in resolutions.values())
    assert Counter(row.provider for row in resolutions.values()) == {
        "youtube": 25,
        "web": 38,
    }
    assert stock_calls == []
    for index, row in resolutions.items():
        assert row.container_id == scenes[index]["container_id"]
        assert row.candidate_id == f"correct:{row.provider}:{index}"
        assert any(
            attempt.get("status") == "container_mismatch"
            for attempt in row.attempts
        )
