from pathlib import Path

import pytest

from server.services import globe_map_service


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _map_scene(props: dict) -> dict:
    return {
        "primary_visual": {"type": "AnimatedMap", "props": {
            **props,
            "map_shot": {"render_style": "globe_3d", **(props.get("map_shot") or {})},
        }},
    }


def _stub_globe_frames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[float, float]]:
    centers: list[tuple[float, float]] = []

    def render(_texture: Path, _target: Path, center: tuple[float, float]) -> list[Path]:
        centers.append(center)
        return [tmp_path / "globe-frame.webp"]

    monkeypatch.setattr(globe_map_service, "_render_frames", render)
    return centers


def test_globe_hydration_resolves_exact_named_route_endpoints_from_bundled_geography(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    centers = _stub_globe_frames(tmp_path, monkeypatch)
    manifest = {"scenes": [_map_scene({
        "variant": "route_flight_arc",
        "from": "PARIS",
        "to": "BRUSSELS",
        "location": "PARIS",
    })]}

    hydrated = globe_map_service.hydrate_globe_maps(manifest, tmp_path, PROJECT_ROOT)
    props = hydrated["scenes"][0]["primary_visual"]["props"]
    _countries, cities = globe_map_service._geographic_centers()

    assert props["route_points"] == [
        {"lon": cities["paris"][0], "lat": cities["paris"][1]},
        {"lon": cities["brussels"][0], "lat": cities["brussels"][1]},
    ]
    assert props["globe_center"] == {"lon": centers[0][0], "lat": centers[0][1]}
    assert len(props["globe_frames"]) == 1


def test_globe_hydration_prefers_valid_media_route_coordinates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    centers = _stub_globe_frames(tmp_path, monkeypatch)
    media_route = [[2.3, 48.8], [4.3, 50.8]]
    scene = _map_scene({
        "variant": "city_to_city_transit",
        "from": "PARIS",
        "to": "BRUSSELS",
        "location": "PARIS",
    })
    scene["media"] = {"route_points": media_route}

    hydrated = globe_map_service.hydrate_globe_maps({"scenes": [scene]}, tmp_path, PROJECT_ROOT)
    props = hydrated["scenes"][0]["primary_visual"]["props"]

    assert props["route_points"] == [
        {"lon": 2.3, "lat": 48.8},
        {"lon": 4.3, "lat": 50.8},
    ]
    assert props["globe_center"] == {"lon": centers[0][0], "lat": centers[0][1]}


def test_globe_hydration_resolves_a_narrated_ocean_region_without_inventing_a_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    centers = _stub_globe_frames(tmp_path, monkeypatch)
    scene = _map_scene({
        "variant": "country_spotlight",
        "location": "NORTH ATLANTIC",
        "map_shot": {"location_names": ["North Atlantic"]},
    })

    hydrated = globe_map_service.hydrate_globe_maps({"scenes": [scene]}, tmp_path, PROJECT_ROOT)
    props = hydrated["scenes"][0]["primary_visual"]["props"]

    assert centers[0] == (-40.0, 50.0)
    assert props["globe_center"] == {"lon": -40.0, "lat": 50.0}
    assert props["globe_pin_label"] == "NORTH ATLANTIC"
    assert "route_points" not in props


def test_globe_uses_primary_visual_variant_when_props_omit_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_globe_frames(tmp_path, monkeypatch)
    scene = _map_scene({"from": "PARIS", "to": "BRUSSELS"})
    scene["primary_visual"]["variant"] = "route_flight_arc"

    hydrated = globe_map_service.hydrate_globe_maps({"scenes": [scene]}, tmp_path, PROJECT_ROOT)
    props = hydrated["scenes"][0]["primary_visual"]["props"]

    assert props["variant"] == "route_flight_arc"
    assert len(props["route_points"]) == 2


def test_globe_does_not_draw_media_route_without_an_explicit_route_variant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_globe_frames(tmp_path, monkeypatch)
    scene = _map_scene({"variant": "country_spotlight", "location": "PARIS"})
    scene["media"] = {"route_points": [[2.3, 48.8], [4.3, 50.8]]}

    hydrated = globe_map_service.hydrate_globe_maps({"scenes": [scene]}, tmp_path, PROJECT_ROOT)
    props = hydrated["scenes"][0]["primary_visual"]["props"]

    assert "route_points" not in props
    assert props["globe_center"] == {"lon": globe_map_service._geographic_centers()[1]["paris"][0], "lat": globe_map_service._geographic_centers()[1]["paris"][1]}


def test_old_service_globe_frames_refresh_to_a_smoother_global_camera_sweep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    centers = _stub_globe_frames(tmp_path, monkeypatch)
    scene = _map_scene({
        "variant":"country_spotlight",
        "globe_frames":[str(tmp_path/'old-frame.webp')],
        "globe_center":{"lon":0,"lat":0},
        "globe_start_center":{"lon":-11,"lat":3.5},
        "globe_attribution":globe_map_service.NASA_CREDIT,
        "map_shot":{"route_status":"not_established_by_script","motion_profile":"world_overview"},
    })

    hydrated = globe_map_service.hydrate_globe_maps({"scenes":[scene]},tmp_path,PROJECT_ROOT)
    props = hydrated["scenes"][0]["primary_visual"]["props"]

    assert centers == [(0.0,0.0)]
    assert props["globe_start_center"] == {"lon":-34.0,"lat":8.0}
    assert props["globe_animation_version"] == globe_map_service.GLOBE_MOTION_VERSION


def test_custom_globe_frames_are_not_replaced_by_the_service(
    tmp_path: Path,
) -> None:
    scene = _map_scene({"globe_frames":["custom-frame.webp"],"globe_center":{"lon":20,"lat":5}})
    hydrated = globe_map_service.hydrate_globe_maps({"scenes":[scene]},tmp_path,PROJECT_ROOT)
    assert hydrated["scenes"][0]["primary_visual"]["props"]["globe_frames"] == ["custom-frame.webp"]


def test_globe_leaves_unresolved_route_endpoints_without_a_claimed_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_globe_frames(tmp_path, monkeypatch)
    manifest = {"scenes": [_map_scene({
        "variant": "route_flight_arc",
        "from": "UNKNOWN ORIGIN",
        "to": "UNKNOWN DESTINATION",
        "location": "PARIS",
    })]}

    hydrated = globe_map_service.hydrate_globe_maps(manifest, tmp_path, PROJECT_ROOT)
    props = hydrated["scenes"][0]["primary_visual"]["props"]

    assert "route_points" not in props
    assert props["globe_center"]["lon"] == pytest.approx(globe_map_service._geographic_centers()[1]["paris"][0])
