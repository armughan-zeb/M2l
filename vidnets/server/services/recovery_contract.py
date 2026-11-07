"""Turn unavailable media into supported explanations, never fake source footage."""
from collections.abc import Mapping
import math
import re


def is_recovery_media(media):
    if not isinstance(media, Mapping):
        return False
    path = str(media.get('src') or media.get('path') or '').replace('\\', '/').lower()
    return bool(media.get('is_graphic_fallback') or media.get('graphic_fallback')
                or re.search(r'(?:^|/)(?:graphic_fallback|emergency_render|placeholder)_', path)
                or str(media.get('source') or media.get('provider') or '') in {'graphic', 'neutral'})


def _align_explanation_ownership(scene, visual_type):
    """A recovered specialist owns its canvas; stale media flags cannot hide it."""
    frame_mode = {'StatCard': 'data', 'Chart': 'data', 'MetricGrid': 'data',
                  'Timeline': 'data', 'Comparison': 'data', 'Diagram': 'diagram',
                  'ProcessFlow': 'diagram', 'AnimatedMap': 'map', 'MapComparison': 'map',
                  'PersonProfile': 'portrait', 'DossierCard': 'portrait',
                  'DocumentHighlight': 'evidence', 'MediaFocusCard': 'evidence',
                  'KineticText': 'kinetic', 'ChapterTitle': 'chapter', 'QuoteCard': 'quote'}.get(visual_type)
    if not frame_mode:
        return
    text = dict(scene.get('text') or {})
    text['frame_mode'] = frame_mode
    text['frame_contract_validation'] = {'status': 'repaired', 'resolved_mode': frame_mode,
                                         'reason': 'grounded_recovery_specialist_ownership'}
    scene['text'] = text
    scene['visual_role'] = 'primary_explanation'
    scene['visual_role_source'] = 'system_grounded_mapping'
    scene['visual_role_version'] = 2
    scene['visual_role_contract_version'] = 2
    scene['director_treatment_enabled'] = True
    scene['media_composition'] = {'enabled': True, 'hero_layer': 'graphic',
                                  'render_mode': 'explanatory_canvas', 'allow_full_frame_graphic': True}
    scene['design'] = {**(scene.get('design') or {}), 'graphics_enabled': True,
                       'visual_structure': frame_mode, 'template_family': frame_mode}


def recover_manifest(manifest):
    report = {'recovered_scene_indices': [], 'unresolved_scene_indices': []}
    for index, scene in enumerate(manifest.get('scenes') or []):
        if not isinstance(scene, dict):
            continue
        media = scene.get('media') or {}
        if not is_recovery_media(media):
            continue
        scene['recovery'] = {'status': 'degraded', 'reason': 'real_media_unavailable', 'claims_source_footage': False}
        scene['media'] = {**media, 'is_graphic_fallback': True, 'verified': False, 'type': 'graphic'}
        primary = scene.get('primary_visual') or {}
        if primary.get('type') not in {'SmartImage', 'SmartVideo', 'image', 'video', None}:
            # Existing grounded specialist retains ownership of the explanation.
            _align_explanation_ownership(scene, primary.get('type'))
            report['recovered_scene_indices'].append(index)
            continue
        narration = str(scene.get('narration') or '')
        selected = None
        for match in re.finditer(r'(?<!\w)(?P<prefix>[$£€])?\s*(?P<value>-?\d[\d,]*(?:\.\d+)?)'
                                r'(?:\s*(?P<magnitude>thousand|million|billion))?'
                                r'(?:\s*(?P<unit>percent|%|seconds?|minutes?|hours?|dollars?|pounds?|euros?|'
                                r'miles?|kilometers?|metres?|meters?|feet|yards?|points?|suspects?|passengers?|parachutes?))?\b', narration, re.I):
            if not match.group('prefix') and not match.group('unit'):
                continue
            value = float(match.group('value').replace(',', ''))
            value *= {'thousand': 1e3, 'million': 1e6, 'billion': 1e9}.get((match.group('magnitude') or '').lower(), 1)
            if not math.isfinite(value):
                continue
            unit = match.group('unit') or ''
            selected = {'type': 'StatCard', 'variant': 'hero_counter_large', 'props': {
                'value': value, 'prefix': match.group('prefix') or '',
                'suffix': '%' if unit.lower() in {'percent', '%'} else unit,
                'source_span': match.group().strip(), 'label': '', 'decimals': 0 if value.is_integer() else 2}}
            break
        if selected is None:
            date = re.search(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)'
                             r'\s+\d{1,2}(?:,?\s+(?:18|19|20|21)\d{2})?\b', narration, re.I)
            if date:
                selected = {'type': 'Timeline', 'variant': 'date_flip_calendar', 'props': {'date': date.group(), 'source_span': date.group()}}
        if selected:
            scene['primary_visual'] = selected
            _align_explanation_ownership(scene, selected['type'])
            scene['graphic'] = {}
            scene['visual_role'] = 'primary_explanation'
            scene['visual_role_version'] = 2
            scene['visual_role_source'] = 'system_grounded_mapping'
            scene['director_treatment_enabled'] = True
            scene['motion_cues'] = [{'element': 'data' if selected['type'] == 'StatCard' else 'timeline',
                                    'action': 'count' if selected['type'] == 'StatCard' else 'reveal',
                                    'anchor_text': selected['props']['source_span'], 'time_local': 0,
                                    'preview_before_narration': True,
                                    'duration_seconds': .4}]
            scene['recovery']['timing_reason'] = 'Supported data preview while source media is unavailable.'
            scene['recovery']['status'] = 'grounded_explanation'
            report['recovered_scene_indices'].append(index)
        else:
            scene['recovery']['status'] = 'requires_verified_media_or_director_explanation'
            report['unresolved_scene_indices'].append(index)
    manifest['media_recovery'] = report
    return report
