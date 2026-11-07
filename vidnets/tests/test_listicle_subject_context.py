import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app import _apply_listicle_section_lock, _infer_listicle_subjects  # noqa: E402


def test_listicle_subjects_propagate_across_split_headings():
    subjects = _infer_listicle_subjects([
        "Some careers push the human body to its limits.",
        "Number 10",
        "Deep-Sea Welder. Far below the ocean surface.",
        "Deep-sea welders repair underwater pipelines.",
        "Number 9",
        "High-Rise Window",
        "Cleaner. Suspended by cables above city streets.",
        "Number 8",
        "Alaskan Crab Fisherman. In the freezing Bering Sea.",
    ])

    assert subjects[1:4] == ["Deep-Sea Welder"] * 3
    assert subjects[4:7] == ["High-Rise Window Cleaner"] * 3
    assert subjects[7:9] == ["Alaskan Crab Fisherman"] * 2


def test_listicle_lock_rewrites_all_source_query_profiles():
    config = {
        "_prebuilt_web_queries": [{} for _ in range(2)],
        "_yt_scene_query_overrides": [{} for _ in range(2)],
        "_prebuilt_stock_queries": [{} for _ in range(2)],
    }
    _apply_listicle_section_lock(config, ["Volcano Researcher", "Ice Road Truck Driver"])

    for key in ("_prebuilt_web_queries", "_yt_scene_query_overrides", "_prebuilt_stock_queries"):
        assert [row["section_subject"] for row in config[key]] == [
            "Volcano Researcher",
            "Ice Road Truck Driver",
        ]
        assert config[key][1]["stock_query"] == "Ice Road Truck Driver at work"
        assert config[key][1]["youtube_query"] == "Ice Road Truck Driver at work"


def test_embedded_next_number_stays_locked_to_current_scene():
    subjects = _infer_listicle_subjects([
        "Number 2",
        "Helicopter Logger. In Oregon and British Columbia.",
        "Helicopter loggers dangle beneath rotor blades. Number 1 Oil Rig Roughneck.",
        "On towering platforms in the open ocean roughnecks wrestle heavy pipes.",
    ])

    assert subjects[2] == "Helicopter Logger"
    assert subjects[3] == "Oil Rig Roughneck"
