from pathlib import Path

import pytest

from server.services import satellite_map_service


@pytest.mark.parametrize("source_key", ["media", "asset"])
def test_satellite_hydration_carries_grounded_media_coordinates_into_map_props(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_key: str,
) -> None:
    plate = tmp_path / "satellite.jpg"
    plate.write_bytes(b"verified test plate")
    monkeypatch.setattr(satellite_map_service, "_fetch_plate", lambda bounds, cache_dir: plate)

    coordinate_source = {"route_points": [[2.3522, 48.8566], [4.3517, 50.8503]]}
    scene = {
        "primary_visual": {
            "type": "AnimatedMap",
            "props": {
                "variant": "route_flight_arc",
                "from": "PARIS",
                "to": "BRUSSELS",
                "map_shot": {"render_style": "satellite_local"},
            },
            **({"asset": coordinate_source} if source_key == "asset" else {}),
        },
    }
    if source_key == "media":
        scene["media"] = coordinate_source
    manifest = {"scenes": [scene]}

    hydrated = satellite_map_service.hydrate_satellite_maps(manifest, tmp_path)
    props = hydrated["scenes"][0]["primary_visual"]["props"]

    assert props["route_points"] == [
        {"lon": 2.3522, "lat": 48.8566},
        {"lon": 4.3517, "lat": 50.8503},
    ]
    assert props["satellite_image"] == str(plate)
    bounds = props["satellite_bounds"]
    assert bounds["west"] <= 2.3522 <= bounds["east"]
    assert bounds["west"] <= 4.3517 <= bounds["east"]
    assert bounds["south"] <= 48.8566 <= bounds["north"]
    assert bounds["south"] <= 50.8503 <= bounds["north"]


def test_existing_satellite_plate_still_receives_route_from_media_metadata(tmp_path: Path) -> None:
    scene = {
        "primary_visual": {
            "type": "AnimatedMap",
            "props": {
                "variant": "route_flight_arc",
                "from": "PARIS",
                "to": "BRUSSELS",
                "satellite_image": "plate.jpg",
                "satellite_bounds": {"west": -2, "south": 47, "east": 4, "north": 53},
                "map_shot": {"render_style": "satellite_local"},
            },
        },
        "media": {"p1": {"lon": 2.3522, "lat": 48.8566}, "p2": {"lon": 4.3517, "lat": 50.8503}},
    }

    hydrated = satellite_map_service.hydrate_satellite_maps({"scenes": [scene]}, tmp_path)
    props = hydrated["scenes"][0]["primary_visual"]["props"]

    assert props["p1"] == {"lon": 2.3522, "lat": 48.8566}
    assert props["p2"] == {"lon": 4.3517, "lat": 50.8503}

