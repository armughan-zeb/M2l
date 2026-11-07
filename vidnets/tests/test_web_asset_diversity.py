import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services.media_resolution_coordinator import resolve_storyboard_media_v2


def test_web_allocator_uses_unused_alternate_before_reuse():
    scenes = [
        {
            "scene_id": f"scene-{index}",
            "scene_index": index,
            "container_id": "same-topic",
            "text": "Cristiano Ronaldo record",
            "positive_subjects": ["Cristiano Ronaldo"],
            "forbidden_subjects": [],
            "source_policy": {"visual_verification_required": False},
        }
        for index in range(2)
    ]

    primary = {
        "path": "snapshot-a.jpg",
        "source": "web",
        "provider": "web",
        "source_url": "https://example.test/a",
        "asset_fingerprint": "same-pixels-a",
        "score": 100,
        "container_id": "same-topic",
    }
    alternate = {
        "path": "snapshot-b.jpg",
        "source": "web",
        "provider": "web",
        "source_url": "https://example.test/b",
        "asset_fingerprint": "different-pixels-b",
        "score": 90,
        "container_id": "same-topic",
    }

    def web_lane(scene, _cancel_event):
        return [primary] if scene["scene_index"] == 0 else [primary, alternate]

    result = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=lambda _scene, _cancel_event: [],
        web_lane=web_lane,
        visual_verifier=lambda _scene, _candidate: {"accepted": True, "score": 90},
        neutral_factory=lambda _scene: None,
        deadline_seconds=2,
    )

    assert result[0].selected_path == "snapshot-a.jpg"
    assert result[1].selected_path == "snapshot-b.jpg"
    assert result[1].resolution_kind == "unique"
    assert "web_path_already_selected:web_asset_identity" in str(result[1].attempts)
