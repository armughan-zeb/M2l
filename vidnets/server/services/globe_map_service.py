"""Render deterministic orthographic Earth plates for Remotion map scenes.

The texture is NASA Earth Observatory's Blue Marble: Next Generation July
global base map. Each pixel is projected onto a lit sphere, so geographic
markers and great-circle connections can share the same camera coordinates.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .geographic_regions import region_center


LOGGER = logging.getLogger(__name__)
NASA_CREDIT = "EARTH IMAGERY: NASA EARTH OBSERVATORY / BLUE MARBLE: NEXT GENERATION"
TEXTURE_NAME = "nasa-blue-marble-july-5400.jpg"
FRAME_COUNT = 30
FRAME_SIZE = 900
GLOBE_MOTION_VERSION = "world-sweep-v2"
GLOBE_START_OFFSET = (34.0, 8.0)


def _coordinate(value: Any) -> tuple[float, float] | None:
    if isinstance(value, Mapping):
        raw_lon, raw_lat = value.get("lon"), value.get("lat")
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        raw_lon, raw_lat = value[0], value[1]
    else:
        return None
    try:
        lon, lat = float(raw_lon), float(raw_lat)
    except (TypeError, ValueError):
        return None
    return (lon, lat) if math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90 else None


def _route_payload(source: Any) -> dict[str, Any] | None:
    """Return a fully validated route from scene or retrieved-media metadata."""
    if not isinstance(source, Mapping):
        return None
    route = source.get("route_points")
    if isinstance(route, list) and len(route) >= 2:
        points = [_coordinate(value) for value in route]
        if all(point is not None for point in points):
            return {"route_points": [{"lon": point[0], "lat": point[1]} for point in points if point]}
    first, second = _coordinate(source.get("p1")), _coordinate(source.get("p2"))
    if first and second:
        return {
            "p1": {"lon": first[0], "lat": first[1]},
            "p2": {"lon": second[0], "lat": second[1]},
        }
    return None


def _center_from_props(props: Mapping[str, Any]) -> tuple[float, float] | None:
    explicit = _coordinate(props.get("globe_center") or props.get("satellite_focus"))
    if explicit:
        return explicit
    route = props.get("route_points")
    raw_points = route if isinstance(route, list) and len(route) >= 2 else [props.get("p1"), props.get("p2")]
    points = [_coordinate(value) for value in raw_points]
    points = [value for value in points if value is not None]
    if not points:
        return _catalog_center(props)
    longitude = math.degrees(math.atan2(
        sum(math.sin(math.radians(value[0])) for value in points),
        sum(math.cos(math.radians(value[0])) for value in points),
    ))
    latitude = sum(value[1] for value in points) / len(points)
    return round(longitude, 5), round(latitude, 5)


@lru_cache(maxsize=1)
def _geographic_centers() -> tuple[dict[str, tuple[float, float]], dict[str, tuple[float, float]]]:
    """Use bundled Natural Earth labels only; never invent a camera location."""
    map_dir = Path(__file__).resolve().parents[2] / "src" / "remotion" / "maps"
    countries: dict[str, tuple[float, float]] = {}
    cities: dict[str, tuple[float, float]] = {}
    try:
        features = json.loads((map_dir / "ne_110m_admin_0_countries.json").read_text(encoding="utf-8"))["features"]
        for feature in features:
            fields = feature.get("properties") or {}
            point = _coordinate({"lon": fields.get("LABEL_X"), "lat": fields.get("LABEL_Y")})
            if point:
                for key in ("ISO_A3_EH", "ISO_A3", "ADM0_A3", "ADMIN", "NAME_EN", "NAME"):
                    if fields.get(key):
                        countries[str(fields[key]).strip().casefold()] = point
    except (OSError, ValueError, KeyError, TypeError) as exc:
        LOGGER.warning("Bundled country centers unavailable: %s", exc)
    try:
        features = json.loads((map_dir / "ne_110m_populated_places.json").read_text(encoding="utf-8"))["features"]
        for feature in features:
            fields = feature.get("properties") or {}
            point = _coordinate((feature.get("geometry") or {}).get("coordinates"))
            if point:
                for key in ("NAME", "NAMEASCII", "NAME_EN"):
                    if fields.get(key):
                        cities[str(fields[key]).strip().casefold()] = point
    except (OSError, ValueError, KeyError, TypeError) as exc:
        LOGGER.warning("Bundled city centers unavailable: %s", exc)
    return countries, cities


def _catalog_center(props: Mapping[str, Any]) -> tuple[float, float] | None:
    countries, cities = _geographic_centers()
    map_shot = props.get("map_shot") if isinstance(props.get("map_shot"), Mapping) else {}
    names = [props.get("country_code"), props.get("country"), props.get("location")]
    names.extend(map_shot.get("location_names") or [])
    for name in names:
        key = str(name or "").strip().casefold()
        region = region_center(name)
        if region:
            return region
        if key in countries:
            return countries[key]
        if key in cities:
            return cities[key]
    return None


def _catalog_route(props: Mapping[str, Any]) -> dict[str, Any] | None:
    """Resolve exact bundled endpoints only when the director selected a route variant."""
    variant = str(props.get("variant") or "").strip().lower().replace("-", "_")
    if variant not in {"route_flight_arc", "city_to_city_transit"}:
        return None
    start = str(props.get("from") or props.get("start") or props.get("startCity") or "").strip().casefold()
    end = str(props.get("to") or props.get("end") or props.get("endCity") or "").strip().casefold()
    if not start or not end:
        return None
    countries, cities = _geographic_centers()
    first = cities.get(start) or countries.get(start)
    second = cities.get(end) or countries.get(end)
    if not first or not second or first == second:
        return None
    return {"route_points": [
        {"lon": first[0], "lat": first[1]},
        {"lon": second[0], "lat": second[1]},
    ]}


def _render_frames(texture_file: Path, target_dir: Path, center: tuple[float, float]) -> list[Path] | None:
    expected = [target_dir / f"frame_{index:03d}.webp" for index in range(FRAME_COUNT)]
    if all(frame.is_file() and frame.stat().st_size > 10_000 for frame in expected):
        return expected
    try:
        import cv2
        import numpy as np

        texture = cv2.imread(str(texture_file), cv2.IMREAD_COLOR)
        if texture is None or texture.shape[1] != 5400 or texture.shape[0] != 2700:
            raise ValueError("NASA globe texture is missing or not equirectangular")
        target_dir.mkdir(parents=True, exist_ok=True)
        axis = (np.arange(FRAME_SIZE, dtype=np.float32) + .5 - FRAME_SIZE / 2) / (FRAME_SIZE / 2)
        px, py_down = np.meshgrid(axis, axis)
        py = -py_down
        radius_squared = px * px + py * py
        inside = radius_squared <= 1.0
        pz = np.sqrt(np.maximum(0.0, 1.0 - radius_squared))
        light = np.clip(-.32 * px + .43 * py + .84 * pz, 0, 1)
        shade = (.38 + .69 * light).astype(np.float32)
        atmosphere = np.clip((1.0 - pz) / .29, 0, 1) ** 2
        alpha = np.where(inside, np.clip((1.0 - radius_squared) * FRAME_SIZE / 5, 0, 1) * 255, 0).astype(np.uint8)
        center_lon, center_lat = center
        for index, destination in enumerate(expected):
            if destination.is_file() and destination.stat().st_size > 10_000:
                continue
            phase = index / max(1, FRAME_COUNT - 1)
            eased = phase * phase * (3 - 2 * phase)
            longitude = math.radians(center_lon - GLOBE_START_OFFSET[0] * (1.0 - eased))
            latitude = math.radians(center_lat + GLOBE_START_OFFSET[1] * (1.0 - eased))
            lat = np.arcsin(np.clip(py * math.cos(latitude) + pz * math.sin(latitude), -1, 1))
            lon = longitude + np.arctan2(px, pz * math.cos(latitude) - py * math.sin(latitude))
            sample_x = np.mod((lon + math.pi) / (2 * math.pi) * texture.shape[1], texture.shape[1]).astype(np.float32)
            sample_y = np.clip((math.pi / 2 - lat) / math.pi * texture.shape[0], 0, texture.shape[0] - 1).astype(np.float32)
            pixels = cv2.remap(texture, sample_x, sample_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP).astype(np.float32)
            pixels *= shade[..., None]
            pixels += atmosphere[..., None] * np.array([78, 50, 16], dtype=np.float32) * .32
            pixels[~inside] = 0
            bgra = np.dstack((np.clip(pixels, 0, 255).astype(np.uint8), alpha))
            temporary = destination.with_suffix(".tmp.webp")
            if not cv2.imwrite(str(temporary), bgra, [cv2.IMWRITE_WEBP_QUALITY, 91]):
                raise OSError("OpenCV could not encode globe frame")
            temporary.replace(destination)
        return expected
    except Exception as exc:
        LOGGER.warning("3D globe rendering unavailable: %s", exc)
        return None


def hydrate_globe_maps(manifest: Mapping[str, Any], output_dir: Path, resource_root: Path) -> dict[str, Any]:
    """Attach local 3D globe frames to scenes selected by the director."""
    result = dict(manifest)
    scenes = [dict(scene) if isinstance(scene, Mapping) else scene for scene in (manifest.get("scenes") or [])]
    result["scenes"] = scenes
    texture = Path(resource_root) / "public" / "map-assets" / TEXTURE_NAME
    if not texture.is_file():
        return result
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        primary = scene.get("primary_visual")
        if not isinstance(primary, Mapping) or str(primary.get("type")) != "AnimatedMap":
            continue
        props = primary.get("props")
        if not isinstance(props, Mapping):
            continue
        map_shot = props.get("map_shot") if isinstance(props.get("map_shot"), Mapping) else scene.get("map_shot") or {}
        style = str(map_shot.get("render_style") or props.get("map_render_style") or "").lower()
        if style != "globe_3d":
            continue

        editable_primary = dict(primary)
        editable_props = dict(props)
        variant = str(props.get("variant") or primary.get("variant") or "").strip().lower().replace("-", "_")
        if variant and not editable_props.get("variant"):
            editable_props["variant"] = variant
        explicit_route = variant in {"route_flight_arc", "city_to_city_transit"}
        route_payload = _route_payload(props) if explicit_route else None
        if explicit_route and route_payload is None:
            for source in (primary.get("asset"), scene.get("media"), scene.get("background")):
                route_payload = _route_payload(source)
                if route_payload is not None:
                    break
        if explicit_route and route_payload is None:
            route_payload = _catalog_route(editable_props)
        if explicit_route and route_payload is not None:
            editable_props.pop("route_points", None)
            editable_props.pop("p1", None)
            editable_props.pop("p2", None)
            editable_props.update(route_payload)
        elif not explicit_route:
            # Coordinate metadata alone is not permission to draw a route.
            editable_props.pop("route_points", None)
            editable_props.pop("p1", None)
            editable_props.pop("p2", None)

        if props.get("globe_frames"):
            service_owned_frames = str(props.get("globe_attribution") or "") == NASA_CREDIT
            current_motion = str(props.get("globe_animation_version") or "") == GLOBE_MOTION_VERSION
            if not service_owned_frames or current_motion:
                if editable_props != dict(props):
                    editable_primary["props"] = editable_props
                    scene["primary_visual"] = editable_primary
                continue
            # Refresh old service-rendered frame sets once to pick up the
            # smoother world sweep. User-provided globe frames stay intact.
        center = _center_from_props(editable_props)
        if center is None:
            continue
        digest = hashlib.sha256(
            f"nasa-july:{center[0]:.5f},{center[1]:.5f}:{GLOBE_START_OFFSET[0]:.1f},{GLOBE_START_OFFSET[1]:.1f}:{GLOBE_MOTION_VERSION}".encode("ascii")
        ).hexdigest()[:20]
        frames = _render_frames(texture, Path(output_dir).resolve() / "globe_maps" / digest, center)
        if not frames:
            continue
        editable_props["globe_frames"] = [str(frame.resolve()) for frame in frames]
        editable_props["globe_center"] = {"lon": center[0], "lat": center[1]}
        editable_props["globe_start_center"] = {
            "lon": center[0] - GLOBE_START_OFFSET[0],
            "lat": center[1] + GLOBE_START_OFFSET[1],
        }
        editable_props["globe_animation_version"] = GLOBE_MOTION_VERSION
        editable_props["globe_attribution"] = NASA_CREDIT
        if not editable_props.get("p1") and not editable_props.get("p2") and not editable_props.get("route_points"):
            map_shot = props.get("map_shot") if isinstance(props.get("map_shot"), Mapping) else {}
            names = map_shot.get("location_names") if isinstance(map_shot.get("location_names"), list) else []
            location = str(props.get("location") or (names[0] if names else "") or "").strip()
            if location:
                editable_props["globe_pin_label"] = location[:32]
        editable_primary["props"] = editable_props
        scene["primary_visual"] = editable_primary
    return result
