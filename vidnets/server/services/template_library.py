"""Curated VOX-inspired Remotion template catalog and safe recipe storage."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping


RECIPE_VERSION = 2
MAX_RECIPE_LAYERS = 16
LAYER_KINDS = {
    "text", "line", "rect", "circle", "image", "chart", "map",
    "diagram", "timeline", "comparison", "counter", "media_montage", "sphere", "cone", "arrow", "group", "path",
}
MOTIONS = {
    "none", "fade", "rise", "slide_left", "slide_right", "scale", "draw",
    "wipe", "pulse", "parallax",
}
LAYER_SHAPES = {"rect", "rounded", "arch", "circle", "hexagon"}
ORIENTATIONS = {"horizontal", "vertical"}
SAFE_ASSET_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$")
SAFE_TEMPLATE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SAFE_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def builtin_template_dir() -> Path:
    configured = str(os.environ.get("VIDZEO_TEMPLATE_LIBRARY_DIR") or "").strip()
    return Path(configured) / "builtin" if configured else _project_root() / "resources" / "template-library" / "builtin"


def custom_template_dir() -> Path:
    configured = str(os.environ.get("VIDZEO_TEMPLATE_CUSTOM_DIR") or "").strip()
    if configured:
        return Path(configured)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(local) / "Vidzeo" / "VidzeoData" / "template-library" / "custom"


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def list_template_catalog(include_drafts: bool = False) -> Dict[str, Any]:
    """Return built-ins and validated custom templates from the shared catalog."""
    manifest = _read_json(builtin_template_dir() / "manifest.json")
    builtins = [
        item for item in manifest.get("templates", [])
        if isinstance(item, dict) and item.get("id")
        and (include_drafts or item.get("status") == "approved")
    ]
    for item in builtins:
        item.setdefault("purpose", item.get("roles", ["editorial treatment"])[0])
        item.setdefault("allowed_props", [])
        item.setdefault("fonts", ["Inter", "Plus Jakarta Sans", "DM Sans", "Archivo Black", "Barlow Condensed"])
        item.setdefault("motion_limits", {"layers": 12, "max_duration_frames": 1800})
        item.setdefault("required_assets", [])
        item.setdefault("version", int(manifest.get("version") or 1))
        item.setdefault("preview", "")
        item.setdefault("review_status", item.get("status", "draft"))
    custom = []
    directory = custom_template_dir()
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            raw = _read_json(path)
            try:
                recipe = validate_template_recipe(raw)
            except ValueError:
                continue
            custom.append({
                "id": recipe["id"],
                "label": recipe["name"],
                "family": "recipe",
                "component": "generated-recipe",
                "roles": ["custom"],
                "status": "approved" if raw.get("review_status") == "approved" else "draft_only",
                "default_panel": False,
                "recipe": recipe,
            })
    return {
        "version": int(manifest.get("version") or 1),
        "style": str(manifest.get("style") or "vox-inspired-editorial"),
        "templates": builtins + (custom if include_drafts else [item for item in custom if item["status"] == "approved"]),
        "rejected_for_auto_selection": list(manifest.get("rejected_for_auto_selection") or []),
    }


def validate_template_recipe(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize a declarative composition; reject executable or remote content."""
    if not isinstance(raw, Mapping):
        raise ValueError("Template recipe must be an object")
    template_id = str(raw.get("id") or "").strip().lower()
    name = str(raw.get("name") or "").strip()
    if not SAFE_TEMPLATE_ID.fullmatch(template_id):
        raise ValueError("Template recipe id must be lowercase letters, digits, and hyphens")
    if not name or len(name) > 80:
        raise ValueError("Template recipe needs a name of at most 80 characters")
    try:
        version = int(raw.get("version") or 1)
    except (TypeError, ValueError):
        raise ValueError("Template recipe version must be an integer")
    if version not in {1, RECIPE_VERSION}:
        raise ValueError("Unsupported template recipe version")
    layers_raw = raw.get("layers") or []
    if not isinstance(layers_raw, list) or len(layers_raw) > MAX_RECIPE_LAYERS:
        raise ValueError(f"Template recipe must contain 0 to {MAX_RECIPE_LAYERS} layers")
    # Some models emit a consistently normalized 0..1 canvas despite the
    # percent schema. Recover that unambiguous whole-recipe convention once;
    # mixed coordinates still go through the ordinary percent validation.
    fraction_canvas = bool(layers_raw) and all(
        isinstance(layer, Mapping)
        and all(isinstance(layer.get(key), (int, float)) and 0 <= layer[key] <= 1
                for key in ("x", "y", "width", "height"))
        for layer in layers_raw
    ) and any(
        layer.get("kind") in {"chart", "timeline", "diagram", "comparison", "image", "media_montage", "counter"}
        for layer in layers_raw if isinstance(layer, Mapping)
    )
    layers = []
    for index, layer in enumerate(layers_raw):
        if not isinstance(layer, Mapping):
            raise ValueError(f"Layer {index} must be an object")
        kind = str(layer.get("kind") or "").strip().lower()
        if kind not in LAYER_KINDS:
            raise ValueError(f"Layer {index} has an unsupported kind")
        normalized = {"kind": kind}
        layer_id = str(layer.get("id") or f"layer-{index}")
        parent_id = str(layer.get("parent_id") or "")
        if not SAFE_TEMPLATE_ID.fullmatch(layer_id) or (parent_id and not SAFE_TEMPLATE_ID.fullmatch(parent_id)):
            raise ValueError(f"Layer {index} has an invalid id or parent")
        normalized.update(id=layer_id, parent_id=parent_id, clip=bool(layer.get("clip", kind == "group")))
        if kind=='path':
            normalized['closed']=layer.get('closed') is True
            if layer.get('fill_color'):
                paint=str(layer['fill_color'])
                if not re.fullmatch(r'#[0-9a-fA-F]{6}',paint):
                    raise ValueError(f'Layer {index} path fill must be a hex color')
                normalized['fill_color']=paint
        if layer.get('semantic_actor'):
            actor=str(layer['semantic_actor']).strip()
            if len(actor)>48 or any(ord(char)<32 for char in actor):
                raise ValueError(f'Layer {index} semantic actor is invalid')
            normalized['semantic_actor']=actor
        for key in ("x", "y", "width", "height"):
            value = float(layer.get(key, 0 if key in {"x", "y"} else 10))
            if fraction_canvas:
                value *= 100
            if not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError(f"Layer {index} {key} must be between 0 and 100")
            normalized[key] = round(value, 3)
        if kind in {"chart", "timeline", "diagram", "comparison", "image", "media_montage", "counter"} and (
            normalized["width"] < 15 or normalized["height"] < 12
        ):
            raise ValueError(f"Layer {index} is too small for readable explanatory content")
        if normalized["x"] + normalized["width"] > 100.001 or normalized["y"] + normalized["height"] > 100.001:
            raise ValueError(f"Layer {index} extends outside the canvas")
        text = str(layer.get("text") or "")
        if len(text) > 180 or any(ord(char) < 32 and char not in "\t\n" for char in text):
            raise ValueError(f"Layer {index} text is invalid or too long")
        normalized["text"] = text
        color = str(layer.get("color") or "#f4f0e6")
        if not SAFE_COLOR.fullmatch(color):
            raise ValueError(f"Layer {index} color must be a hex RGB value")
        normalized["color"] = color.lower()
        asset_id = str(layer.get("asset_id") or "")
        if asset_id and (not SAFE_ASSET_ID.fullmatch(asset_id) or "://" in asset_id or ".." in Path(asset_id).parts):
            raise ValueError(f"Layer {index} asset reference must be a local catalog id")
        normalized["asset_id"] = asset_id
        if layer.get('image_index') is not None:
            image_index = layer['image_index']
            if isinstance(image_index, bool) or not isinstance(image_index, int) or not 0 <= image_index < 16:
                raise ValueError(f'Layer {index} image_index must be an integer from 0 to 15')
            normalized['image_index'] = image_index
        motion = str(layer.get("motion") or "none")
        if motion not in MOTIONS:
            raise ValueError(f"Layer {index} has an unsupported motion")
        normalized["motion"] = motion
        shape = str(layer.get("shape") or ('circle' if kind in {'circle', 'sphere'} else 'rounded'))
        if shape not in LAYER_SHAPES:
            raise ValueError(f"Layer {index} has an unsupported frame shape")
        normalized["shape"] = shape
        if kind == "path":
            points = layer.get("points") or []
            if not isinstance(points, list) or not 2 <= len(points) <= 32:
                raise ValueError(f"Layer {index} path requires 2 to 32 points")
            normalized["points"] = []
            for point in points:
                if not isinstance(point, Mapping):
                    raise ValueError(f"Layer {index} path point must be an object")
                x, y = float(point.get("x", -1)), float(point.get("y", -1))
                if not all(math.isfinite(v) and 0 <= v <= 100 for v in (x, y)):
                    raise ValueError(f"Layer {index} path point is outside its bounds")
                normalized["points"].append({"x": x, "y": y})
        orientation = str(layer.get("orientation") or "horizontal")
        if orientation not in ORIENTATIONS:
            raise ValueError(f"Layer {index} has an unsupported orientation")
        normalized["orientation"] = orientation
        for key in ("focal_x", "focal_y"):
            value = float(layer.get(key, 50))
            if not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError(f"Layer {index} {key} must be between 0 and 100")
            normalized[key] = round(value, 3)
        start_frame = int(layer.get("start_frame") or 0)
        duration_frames = int(layer.get("duration_frames") or 30)
        if not 0 <= start_frame <= 100000 or not 1 <= duration_frames <= 1800:
            raise ValueError(f"Layer {index} frame timing is out of range")
        normalized["start_frame"] = start_frame
        normalized["duration_frames"] = duration_frames
        data_rows = layer.get("data") or []
        if not isinstance(data_rows, list) or len(data_rows) > 8:
            raise ValueError(f"Layer {index} data must contain at most 8 rows")
        normalized_data = []
        for row in data_rows:
            if not isinstance(row, Mapping):
                raise ValueError(f"Layer {index} data rows must be objects")
            label = str(row.get("label") or "").strip()
            value = None
            if row.get("value") is not None:
                try:
                    value = float(row.get("value"))
                except (TypeError, ValueError):
                    raise ValueError(f"Layer {index} data values must be numeric")
            if not label or len(label) > 48 or (value is not None and not math.isfinite(value)):
                raise ValueError(f"Layer {index} data row is invalid")
            normalized_row = {"label": label}
            if kind == 'diagram':
                for key in ['x', 'y', 'label_y']:
                    if row.get(key) is not None:
                        coordinate = float(row[key])
                        if not math.isfinite(coordinate) or not 0 <= coordinate <= 100:
                            raise ValueError(f'Layer {index} actor coordinate is outside the canvas')
                        normalized_row[key] = coordinate
                if row.get('hide_marker') is True:
                    normalized_row['hide_marker'] = True
            if value is not None:
                normalized_row["value"] = value
            normalized_data.append(normalized_row)
        normalized["data"] = normalized_data
        links_raw = layer.get("links") or []
        if not isinstance(links_raw, list) or len(links_raw) > 8:
            raise ValueError(f"Layer {index} links must contain at most 8 relationships")
        labels = {row["label"] for row in normalized_data}
        links = []
        for link in links_raw:
            if not isinstance(link, Mapping):
                raise ValueError(f"Layer {index} links must be objects")
            source, target = str(link.get("from") or "").strip(), str(link.get("to") or "").strip()
            if not source or not target or source == target or source not in labels or target not in labels:
                raise ValueError(f"Layer {index} relationships must connect two declared diagram nodes")
            evidence = str(link.get("evidence") or "").strip()
            if version >= 2 and (not evidence or len(evidence) > 180):
                raise ValueError(f"Layer {index} diagram links need an exact narration evidence phrase")
            links.append({"from": source, "to": target, **({"evidence": evidence} if evidence else {})})
        normalized["links"] = links
        if kind == "comparison" and len(normalized_data) != 2:
            raise ValueError(f"Layer {index} comparison needs exactly two rows")
        if kind in {"chart", "timeline", "comparison", "diagram"} and len(normalized_data) < 2:
            raise ValueError(f"Layer {index} {kind} needs at least two grounded rows")
        if kind in {"chart", "comparison"} and any(row.get("value") is None for row in normalized_data):
            raise ValueError(f"Layer {index} {kind} requires numeric values for every row")
        if kind == "counter" and (len(normalized_data) != 1 or normalized_data[0].get("value") is None):
            raise ValueError(f"Layer {index} counter needs one grounded numeric value")
        if links and kind != "diagram":
            raise ValueError(f"Layer {index} relationship links are only valid on diagrams")
        layers.append(normalized)

    by_id = {layer["id"]: layer for layer in layers}
    if len(by_id) != len(layers):
        raise ValueError("Recipe layer ids must be unique")
    for layer in layers:
        chain, current = set(), layer
        while current.get("parent_id"):
            parent = by_id.get(current["parent_id"])
            if parent is None or parent["kind"] != "group":
                raise ValueError("Recipe parent must reference a group")
            if current["id"] in chain or parent["id"] == layer["id"]:
                raise ValueError("Recipe groups must not contain cycles")
            chain.add(current["id"])
            if len(chain) > 4:
                raise ValueError("Recipe group nesting exceeds four levels")
            current = parent
    return {
        "version": version,
        "id": template_id,
        "name": name,
        "layers": layers,
        "review_status": str(raw.get("review_status") or "draft"),
    }


def save_custom_template(raw: Mapping[str, Any], *, review_status: str = "draft") -> Path:
    """Atomically store a validated custom recipe without evaluating code."""
    recipe = validate_template_recipe(raw)
    if review_status not in {"draft", "approved"}:
        raise ValueError("review_status must be draft or approved")
    recipe["review_status"] = review_status
    directory = custom_template_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{recipe['id']}.json"
    fd, temp_name = tempfile.mkstemp(prefix=f".{recipe['id']}.", suffix=".tmp", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(recipe, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return target


def template_cache_key(template_id: str, props: Mapping[str, Any], asset_digests: Mapping[str, str], font_version: str, fps: int, width: int, height: int) -> str:
    canonical = json.dumps({
        "template_id": template_id,
        "props": props,
        "assets": asset_digests,
        "font_version": font_version,
        "fps": int(fps),
        "width": int(width),
        "height": int(height),
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "builtin_template_dir", "custom_template_dir", "list_template_catalog",
    "save_custom_template", "template_cache_key", "validate_template_recipe",
]
