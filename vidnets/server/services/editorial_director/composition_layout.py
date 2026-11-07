"""Lay out approved semantic actors without asking the LLM to solve geometry.

The model still owns actors, contours, relationships, palette roles and reveal
order. This module only fits those authored objects into a readable safe area.
It never creates a relationship, substitutes a subject or invents measurements.
"""
from __future__ import annotations

from copy import deepcopy
import math


def fit_actor_objects(draft, approved_nodes, approved_links):
    labels = [str(node['label']) for node in approved_nodes]
    objects = [deepcopy(obj) for obj in draft.get('objects', []) if isinstance(obj, dict)]
    if not 2 <= len(labels) <= 4 or not objects:
        raise ValueError('composition_missing_authored_objects')
    by_actor = {label: [] for label in labels}
    for obj in objects:
        if obj.get('actor_label') not in by_actor:
            raise ValueError('composition_object_actor_ungrounded')
        if obj.get('kind') not in {'sphere', 'circle', 'cone', 'rect', 'path', 'arrow', 'line'}:
            raise ValueError('composition_object_kind_unsupported')
        by_actor[obj['actor_label']].append(obj)
    # Connectors alone are not a subject. Missing objects must be redesigned,
    # rather than silently converted to a circle or a generic card.
    subjects = {'sphere', 'circle', 'cone', 'rect', 'path'}
    if any(not any(obj.get('kind') in subjects for obj in group) for group in by_actor.values()):
        raise ValueError('composition_missing_actor_geometry')
    layout = str(draft.get('layout') or 'flow')
    if len(labels) == 2:
        slots = [(8, 23, 36, 46), (56, 23, 36, 46)]
        if layout == 'vertical':
            slots = [(29, 18, 42, 22), (29, 50, 42, 22)]
    elif len(labels) == 3:
        slots = [(6, 24, 26, 44), (37, 24, 26, 44), (68, 24, 26, 44)]
        if layout in {'branch', 'network'}:
            slots = [(35, 18, 30, 22), (8, 50, 36, 22), (56, 50, 36, 22)]
    else:
        slots = [(8, 20, 36, 21), (56, 20, 36, 21), (8, 52, 36, 21), (56, 52, 36, 21)]
    for label, slot in zip(labels, slots):
        group = by_actor[label]
        # Geometry-free drafts describe one actor; multiple authored pieces
        # retain their relative positions as one uniformly scaled illustration.
        for obj in group:
            for key, default in [('x', 0), ('y', 0), ('width', 30), ('height', 40)]:
                value = float(obj[key] if obj.get(key) is not None else default)
                if not math.isfinite(value):
                    raise ValueError('composition_nonfinite_geometry')
                obj[key] = value
            if obj['width'] <= 0 or obj['height'] <= 0:
                raise ValueError('composition_empty_geometry')
            if obj['kind'] in {'sphere', 'circle'}:
                obj['height'] = obj['width'] * 16 / 9
            obj.setdefault('motion', 'fade')
            obj.setdefault('start_seconds', 0)
        x0, y0 = min(o['x'] for o in group), min(o['y'] for o in group)
        x1 = max(o['x'] + o['width'] for o in group)
        y1 = max(o['y'] + o['height'] for o in group)
        sx, sy, sw, sh = slot
        scale = min(sw / (x1 - x0), sh / (y1 - y0))
        dx, dy = sx + (sw - (x1-x0)*scale)/2, sy + (sh - (y1-y0)*scale)/2
        for obj in group:
            obj.update(x=round(dx+(obj['x']-x0)*scale, 4),
                       y=round(dy+(obj['y']-y0)*scale, 4),
                       width=round(obj['width']*scale, 4), height=round(obj['height']*scale, 4))
        # All shapes for an actor share a label above the complete actor box.
        group[0]['label_x'] = sx + sw/2
        group[0]['label_y'] = sy - 5
    result = deepcopy(draft)
    result['objects'] = objects
    result['_local_layout'] = layout
    return result
