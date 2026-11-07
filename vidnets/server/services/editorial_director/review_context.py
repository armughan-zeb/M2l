"""Compact, scene-specific editorial briefs for the rendered-frame reviewer.

These are review hints, never permission to invent copy, facts or source media.
"""
from __future__ import annotations

from collections.abc import Mapping
import re


def visual_summary(scene, index):
    primary = scene.get('primary_visual') or {}
    text = scene.get('text') if isinstance(scene.get('text'), Mapping) else {}
    recipe = scene.get('template_recipe') or {}
    return {
        'scene_index': index,
        'narration': str(scene.get('narration') or '')[:600],
        'component': primary.get('type'),
        'variant': primary.get('variant'),
        'visual_role': scene.get('visual_role'),
        'visual_intent': str(scene.get('visual_intent') or '')[:400],
        'frame_mode': text.get('frame_mode') or scene.get('frame_mode'),
        'copy': text.get('display_text') or scene.get('display_text') or '',
        'position': text.get('position'),
        'panel_style': text.get('panel_style'),
        'recipe_shapes': [layer.get('kind') for layer in recipe.get('layers') or []
                          if isinstance(layer, Mapping)],
        'recipe_geometry': [{key: layer.get(key) for key in ('kind', 'x', 'y', 'width', 'height', 'motion', 'start_frame')}
                            for layer in (recipe.get('layers') or [])[:16] if isinstance(layer, Mapping)],
    }


def narration_opportunities(narration):
    """Identify exact source spans worth considering, without fabricating data."""
    month = r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'
    patterns = {
        'date': rf'\b\d{{1,2}}(?:st|nd|rd|th)?\s+{month}(?:,?\s+\d{{4}})?\b|\b{month}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b|\b(?:18|19|20|21)\d{{2}}\b',
        'measurement': r'(?<!\w)(?:[$£€]\s*)?\d+(?:,\d{3})*(?:\.\d+)?(?:\s*(?:%|percent|million|billion|seconds?|minutes?|hours?|miles?|kilometers?|metres?|meters?|feet|yards?|dollars?|points?))?',
        'relationship': r'\b(?:because|causes?|blocks?|between|compared with|versus|before|after|leads? to|flows? through)\b',
    }
    result = []
    date_regions = [(match.start(), match.end()) for match in re.finditer(patterns['date'], narration, re.I)]
    for kind, pattern in patterns.items():
        for match in re.finditer(pattern, narration, re.I):
            if kind == 'measurement' and (re.fullmatch(r'(?:18|19|20|21)\d{2}', match.group())
                    or any(start <= match.start() and match.end() <= end for start, end in date_regions)):
                continue
            result.append({'kind': kind, 'source_span': match.group(),
                           'start': match.start(), 'end': match.end()})
    return sorted(result, key=lambda row: row['start'])[:16]


def editorial_review_brief(manifest, scenes, index):
    profile = manifest.get('editing_profile') or {}
    dna = scenes[index].get('visual_dna') or manifest.get('visual_dna') or {}
    niche = profile.get('niche') or (dna.get('palette') or {}).get('niche') or manifest.get('category') or 'general'
    grammar = profile.get('visual_grammar')
    if not isinstance(grammar, Mapping):
        from ..editing_profile import niche_visual_grammar
        grammar = niche_visual_grammar(niche)
    current = visual_summary(scenes[index], index)
    neighbors = [visual_summary(scenes[i], i)
                 for i in range(max(0, index - 2), min(len(scenes), index + 3)) if i != index]
    signature_keys = ('component', 'variant', 'visual_role', 'frame_mode', 'position', 'panel_style', 'recipe_shapes', 'recipe_geometry')
    repeated = [row['scene_index'] for row in neighbors
                if abs(row['scene_index'] - index) == 1
                and all(current[key] == row[key] for key in signature_keys)
                and current['component'] not in {None, '', 'SmartVideo', 'SmartImage'}]
    return {
        'niche': niche, 'visual_grammar': dict(grammar),
        'neighboring_edits': neighbors,
        'adjacent_matching_treatments': repeated,
        'narration_opportunities': narration_opportunities(str(scenes[index].get('narration') or '')),
        'instructions': [
            'Judge whether this treatment explains this beat in the film\'s niche and emotional arc.',
            'Check important named people, places and numerical units in the narration; do not replace specific evidence with generic imagery.',
            'Opportunity spans are review hints, not approved overlay copy or a quota: prefer a useful grounded explanation, and leave breathing shots clean.',
            'Compare neighboring edits for repetition. Matching signatures are a risk, not a failure; a continuous explanation may intentionally retain its layout.',
            'Use measured rendered bounds and actual pixels for placement/contrast; intended props do not prove correct rendering.',
            'Return one targeted correction for observed weaknesses, preserving healthy scenes, exact approved copy, timing and evidence.',
        ],
    }


def review_budget(config, scene_count, frame_count):
    """Bound the whole preview/revision phase and reserve its final assessment."""
    import math
    waves = max(1, math.ceil(scene_count / 4))
    automatic = max(120.0, min(300.0, 45.0 + frame_count * 1.5 + waves * 25.0 * 3))
    try:
        seconds = float(config.get('creative_review_budget_seconds', automatic))
        if not math.isfinite(seconds):
            seconds = automatic
    except (ValueError, TypeError):
        seconds = automatic
    try:
        rounds = int(config.get('creative_revision_rounds', 2))
    except (ValueError, TypeError, OverflowError):
        rounds = 2
    return {'total_seconds': max(30.0, min(300.0, seconds)),
            'revision_rounds': max(0, min(2, rounds)),
            'assessment_reserve_seconds': min(45.0, max(15.0, waves * 10.0))}
