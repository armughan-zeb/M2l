import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services import projects_service


def test_list_projects_returns_every_nested_video_with_unique_ids(tmp_path, monkeypatch):
    first = tmp_path / "exports" / "first"
    second = tmp_path / "exports" / "second"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    # Same names/sizes/timestamps are intentional: projects in different
    # folders must still be selectable independently in the library.
    left = first / "same-name.mp4"
    right = second / "same-name.mp4"
    left.write_bytes(b"video")
    right.write_bytes(b"video")
    nested = first / "nested" / "another.webm"
    nested.parent.mkdir()
    nested.write_bytes(b"webm")

    monkeypatch.setattr(
        projects_service,
        "ensure_project_thumbnail",
        lambda path: f"/temp/thumbnails/{path.stem}.jpg",
    )

    projects = projects_service.list_projects(tmp_path / "exports")

    assert {item["path"] for item in projects} == {
        str(left),
        str(right),
        str(nested),
    }
    assert len({item["id"] for item in projects}) == 3
    assert all(item["url"].startswith("/output/") for item in projects)
    assert all(item["root"] == str((tmp_path / "exports").resolve()) for item in projects)

