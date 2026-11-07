"""Prepare local, attributed satellite plates for compact Remotion map scenes.

The 2016 EOxCloudless WMS layer is CC BY 4.0. Downloading once per map view
keeps every Remotion frame deterministic and avoids tile requests during render.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping


LOGGER = logging.getLogger(__name__)
EOX_WMS = "https://tiles.maps.eox.at/map"
EOX_CREDIT = (
    "EOxCloudless https://cloudless.eox.at by EOX IT Services GmbH "
    "(Contains modified Copernicus Sentinel data 2016 & 2017)"
)


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
    """Return only validated coordinate payloads usable by the Remotion map."""
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


def _scene_view(props: Mapping[str, Any]) -> dict[str, float] | None:
    supplied_route = props.get("route_points")
    candidates = supplied_route if isinstance(supplied_route, list) and len(supplied_route) >= 2 else [props.get("p1"), props.get("p2")]
    points = [_coordinate(value) for value in candidates]
    if len(points) < 2 or any(value is None for value in points):
        return None
    lons = [value[0] for value in points if value]
    lats = [value[1] for value in points if value]
    lon_span = max(lons) - min(lons)
    lat_span = max(lats) - min(lats)
    # World and intercontinental shots still use the existing vector map.
    # Sentinel imagery is most useful where terrain resolves the subject.
    if lon_span > 3.5 or lat_span > 2.5 or min(lats) < -70 or max(lats) > 70:
        return None
    mid_lat = (min(lats) + max(lats)) / 2
    view_height = max(.24, lat_span * 1.45)
    view_width = max(.42, lon_span * 1.55, view_height * 16 / 9 / max(.36, math.cos(math.radians(mid_lat))))
    center_lon = (min(lons) + max(lons)) / 2
    center_lat = (min(lats) + max(lats)) / 2
    west, east = center_lon - view_width / 2, center_lon + view_width / 2
    south, north = center_lat - view_height / 2, center_lat + view_height / 2
    if west < -180 or east > 180 or south < -85 or north > 85:
        return None
    return {"west": round(west, 6), "south": round(south, 6), "east": round(east, 6), "north": round(north, 6)}


def _fetch_plate(bounds: Mapping[str, float], cache_dir: Path) -> Path | None:
    bbox = ",".join(str(bounds[key]) for key in ("west", "south", "east", "north"))
    digest = hashlib.sha256(f"eox-2016:{bbox}:3840x2160".encode("ascii")).hexdigest()[:20]
    target = cache_dir / f"eox-2016-{digest}.jpg"
    if target.is_file() and target.stat().st_size > 30_000:
        return target
    cache_dir.mkdir(parents=True, exist_ok=True)
    query = urllib.parse.urlencode({
        "service": "WMS", "request": "GetMap", "version": "1.1.1",
        "layers": "s2cloudless", "styles": "", "srs": "EPSG:4326",
        "bbox": bbox, "width": 3840, "height": 2160, "format": "image/jpeg",
    })
    request = urllib.request.Request(f"{EOX_WMS}?{query}", headers={"User-Agent": "Vidzeo/1.0 satellite documentary renderer"})
    temporary = target.with_suffix(".download")
    try:
        with urllib.request.urlopen(request, timeout=18) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            payload = response.read(16_000_000)
        if "image/jpeg" not in content_type or len(payload) < 30_000 or not payload.startswith(b"\xff\xd8"):
            raise ValueError("EOxCloudless returned a non-image map response")
        temporary.write_bytes(payload)
        os.replace(temporary, target)
        return target
    except (OSError, ValueError) as exc:
        LOGGER.warning("Satellite map unavailable for %s: %s", bbox, exc)
        temporary.unlink(missing_ok=True)
        return None


def hydrate_satellite_maps(manifest: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    """Attach imagery to compact, coordinate-grounded routes with a fixed deadline.

    Failed requests leave the original map scene intact. Images live beside the
    video output; the Remotion local asset server serves the absolute paths.
    """
    result = dict(manifest)
    scenes = [dict(scene) if isinstance(scene, Mapping) else scene for scene in (manifest.get("scenes") or [])]
    result["scenes"] = scenes
    groups: dict[tuple[float, float, float, float], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        primary = scene.get("primary_visual")
        if not isinstance(primary, Mapping) or str(primary.get("type")) != "AnimatedMap":
            continue
        props = primary.get("props")
        if not isinstance(props, Mapping):
            continue
        map_shot = props.get("map_shot") if isinstance(props.get("map_shot"), Mapping) else {}
        if str(map_shot.get("render_style") or props.get("map_render_style") or "").lower() == "vector_editorial":
            continue

        # The director can select satellite_local from verified media metadata,
        # while the map's visual props contain only the spoken place names.
        # Carry those exact coordinates into the renderer contract so both the
        # plate hydrator and SatelliteMap draw the same grounded route.
        editable_primary = dict(primary)
        editable_props = dict(props)
        route_payload = _route_payload(props)
        if route_payload is None:
            for source in (
                primary.get("asset"),
                scene.get("media"),
                scene.get("background"),
            ):
                route_payload = _route_payload(source)
                if route_payload is not None:
                    break
        if route_payload is not None:
            # Replace malformed/partial legacy route fields as a unit.
            editable_props.pop("route_points", None)
            editable_props.pop("p1", None)
            editable_props.pop("p2", None)
            editable_props.update(route_payload)

        # Existing plates still need their route geometry in component props;
        # the early return used to skip that copy and made SatelliteMap blank.
        if props.get("satellite_image"):
            if route_payload is not None and route_payload != {
                key: props.get(key) for key in route_payload
            }:
                editable_primary["props"] = editable_props
                scene["primary_visual"] = editable_primary
            continue

        bounds = _scene_view(editable_props)
        if bounds is None:
            continue
        editable_primary["props"] = editable_props
        scene["primary_visual"] = editable_primary
        key = tuple(bounds[name] for name in ("west", "south", "east", "north"))
        groups.setdefault(key, []).append((editable_props, bounds))
    if not groups:
        return result

    cache_dir = Path(output_dir).resolve() / "satellite_maps"
    pool = ThreadPoolExecutor(max_workers=min(3, len(groups)), thread_name_prefix="satellite-map")
    try:
        futures = {pool.submit(_fetch_plate, rows[0][1], cache_dir): rows for rows in groups.values()}
        done, pending = wait(futures, timeout=20)
        for future in pending:
            future.cancel()
        for future in done:
            try:
                plate = future.result()
            except Exception as exc:
                LOGGER.warning("Satellite map preparation failed: %s", exc)
                continue
            if plate is None:
                continue
            for props, bounds in futures[future]:
                props["satellite_image"] = str(plate)
                props["satellite_bounds"] = bounds
                props["satellite_attribution"] = EOX_CREDIT
                props["satellite_caption"] = "SATELLITE MOSAIC 2016–17 / SCHEMATIC ROUTE"
                props["map_style"] = "satellite_tactical"
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return result
