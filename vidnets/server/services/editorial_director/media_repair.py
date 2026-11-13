"""Bounded post-retrieval direction: repair failed shots with grounded explanations."""
import json
import os
import re
import time
from concurrent.futures import wait

from ..job_context import JobThreadPoolExecutor, current_job, phase_scope, remaining_seconds
from ..recovery_contract import is_recovery_media, _align_explanation_ownership


def _clean(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def apply_repair(scene, proposal):
    """Accept exact narrated actors, never invented relationships or source marks."""
    narration = _clean(scene.get('narration'))
    nodes = proposal.get('nodes')
    if not isinstance(nodes, list) or not 2 <= len(nodes) <= 4:
        return False
    checked = []
    for node in nodes:
        if not isinstance(node, dict):
            return False
        label, span = _clean(node.get('label')), _clean(node.get('source_span'))
        if (not label or len(label.split()) > 7 or not span
                or span.lower() not in narration.lower() or label.lower() not in span.lower()
                or any(label.lower() == old['label'].lower() for old in checked)):
            return False
        checked.append({'label': label, 'source_span': span})
    # No implied causal graph: sequential/hierarchical edges require exact
    # relationship evidence as well as the actors themselves.
    relation = _clean(proposal.get('relationship_span'))
    if not relation or relation.lower() not in narration.lower():
        return False
    if not all(node['label'].lower() in relation.lower() for node in checked):
        return False
    if (scene.get('primary_visual') or {}).get('type') == 'PersonProfile' and not is_recovery_media(scene.get('media')):
        existing = _clean((scene['primary_visual'].get('props') or {}).get('name')).lower()
        # A group profile must retain the director's named subject and only
        # add exact, explicitly named people, not locations or role labels.
        if (existing not in {node['label'].lower() for node in checked}
                or any(not re.fullmatch(r'[A-Z][\w.\u2019\'-]+(?:\s+[A-Z][\w.\u2019\'-]+)+', node['label']) for node in checked)):
            return False
        props = dict(scene['primary_visual'].get('props') or {})
        props['people'] = checked
        props['people_copy_contract'] = {'approved': True, 'source': 'llm_scene_repair', 'nodes': checked}
        scene['primary_visual']['props'] = props
        return True
    variant = proposal.get('variant')
    if variant not in {'step_by_step_process', 'cause_and_effect_flow', 'hierarchical_system_tree', 'network_node_connect'}:
        variant = 'step_by_step_process'
    # A list of independent facts does not establish a directed relationship.
    links = []
    if proposal.get('relationship') == 'sequence' and re.search(r'\b(then|before|after|first|next|finally|followed|from|to)\b', relation, re.I):
        links = [{'from': a['label'], 'to': b['label']} for a, b in zip(checked, checked[1:])]
    elif proposal.get('relationship') == 'cause' and re.search(r'\b(because|caused|causes|led to|result|enabled|allows|allowing)\b', relation, re.I):
        links = [{'from': a['label'], 'to': b['label']} for a, b in zip(checked, checked[1:])]
    props = {'steps': checked, 'links': links, 'transparent_canvas': True,
             'node_copy_contract': {'approved': True, 'source': 'llm_scene_repair', 'nodes': checked}}
    scene['primary_visual'] = {'type': 'Diagram', 'variant': variant, 'props': props}
    _align_explanation_ownership(scene, 'Diagram')
    scene['graphic'] = {}
    scene['template_id'] = 'full-bleed'
    scene['template_recipe'] = {}
    # Preserve only the already approved heading, never synthesize one here.
    duration = max(.1, float(scene.get('duration') or 5))
    words = narration.split()
    scene['motion_cues'] = [
        {'element': 'steps', 'action': 'reveal', 'anchor_text': node['label'],
         'time_local': min(duration - .1, len(narration[:narration.lower().index(node['label'].lower())].split()) / max(1, len(words)) * duration),
         'duration_seconds': .38}
        for node in checked[:3]
    ]
    scene['recovery'] = {'status': 'grounded_explanation', 'reason': 'llm_post_media_repair',
                         'claims_source_footage': False, 'relationship_span': relation}
    scene['media_plan'] = {**(scene.get('media_plan') or {}), 'repair_source': 'llm_scene_repair'}
    return True


def repair_failed_shots(manifest, config=None, client=None):
    config = config or {}
    scenes = manifest.get('scenes') or []
    def needs_repair(scene):
        primary = scene.get('primary_visual') or {}
        if is_recovery_media(scene.get('media')) and primary.get('type') in {None, 'SmartImage', 'SmartVideo'}:
            return True
        return primary.get('type') == 'PersonProfile' and len(re.findall(r'\b[A-Z][a-z]+\s+[A-Z][a-z]+\b', str(scene.get('narration') or ''))) >= 2 and not (primary.get('props') or {}).get('people_copy_contract')
    targets = [i for i, scene in enumerate(scenes) if isinstance(scene, dict) and needs_repair(scene)]
    report = {'requested': targets, 'repaired': [], 'failed': [], 'status': 'not_needed'}
    manifest['post_media_direction'] = report
    if not targets:
        return report
    reserve = float(config.get('_render_reserve_seconds') or config.get('render_reserve_seconds') or 150)
    repair_seconds = min(35.0, float(config.get('post_media_direction_seconds') or 30))
    if current_job().get('llm_provider') == 'external_agent':
        # The repair call answers through the polling inbox, so it needs the
        # full agent answer floor — a 30s budget expires before the answer
        # arrives (the phase scope below caps the request wall at it).
        from services.agent_bridge import AGENT_REQUEST_WALL_SECONDS
        agent_wall = AGENT_REQUEST_WALL_SECONDS
        repair_seconds = max(repair_seconds, agent_wall)
    budget = max(0, min(repair_seconds, remaining_seconds(900) - reserve))
    if budget < .01:
        report['status'] = 'render_reserve_preserved'
        return report
    if client is None:
        key = config.get('openrouter_api_key') or config.get('openrouterApiKey') or os.environ.get('OPENROUTER_API_KEY') or os.environ.get('VIDZEO_OPENROUTER_API_KEY')
        if not key:
            report['status'] = 'provider_unavailable'
            return report
        from ..openrouter_llm import OpenRouterLLM
        from .director import DIRECTOR_DEFAULT_MODEL
        client = OpenRouterLLM(api_key=key, model=config.get('director_model') or os.environ.get('VIDZEO_EDITORIAL_DIRECTOR_MODEL') or DIRECTOR_DEFAULT_MODEL,
                               fallback_models=[], use_server_fallbacks=False)
    deadline = time.monotonic() + budget
    arc = [{'index': i, 'narration': _clean(scene.get('narration'))} for i, scene in enumerate(scenes)]
    def plan(indices):
        context = {'visual_dna': manifest.get('visual_dna'),
                   'scenes': [{'index': i, 'narration': arc[i]['narration'],
                               'previous': arc[i-1] if i else None,
                               'next': arc[i+1] if i+1 < len(arc) else None,
                               'media_status': 'unavailable; do not claim source imagery' if is_recovery_media(scenes[i].get('media')) else 'verified group profile; preserve media and list every named person',
                               'current_profile_name': (scenes[i].get('primary_visual', {}).get('props') or {}).get('name')} for i in indices]}
        with phase_scope(max(.01, deadline - time.monotonic())):
            response = client.chat(user_prompt=json.dumps(context),
                system_prompt='You are the documentary repair director. For failed media, design a readable explanatory Diagram using 2-4 short, exact narration phrases as nodes. For verified GROUP PROFILES, preserve the media: nodes must list every explicitly named person including current_profile_name, with original capitalization; never replace the profile with a diagram. Each label must occur verbatim inside its source_span. Supply relationship_span containing every label, verbatim from THIS scene. Only draw sequence or cause edges when the words explicitly establish that relationship; otherwise use relationship=independent with no edges. No invented numbers, dates, routes, evidence, headings, decorative rings, or remote assets. Keep adjacent scene context and vary the layout within its visual grammar. Return JSON {scenes:[{index,nodes:[{label,source_span}],relationship_span,relationship,variant}]}. variants: step_by_step_process, cause_and_effect_flow, hierarchical_system_tree, network_node_connect. Return no entry if a grounded explanation is impossible.',
                temperature=.3, max_tokens=1800, json_mode=True, max_attempts=1,
                total_timeout_seconds=max(.01, deadline - time.monotonic()))
        from .director import _decode_director_json
        return indices, _decode_director_json(response)
    pool = JobThreadPoolExecutor(max_workers=2, thread_name_prefix='post-media-director')
    futures = {pool.submit(plan, targets[start:start+8]): targets[start:start+8]
               for start in range(0, len(targets), 8)}
    try:
        done, pending = wait(futures, timeout=budget)
        for future in done:
            indices = futures[future]
            try:
                _, response = future.result()
                proposals = response.get('scenes') if isinstance(response, dict) else []
                for row in proposals or []:
                    if not isinstance(row, dict):
                        continue
                    index = row.get('index')
                    if isinstance(index, int) and not isinstance(index, bool) and index in indices and index not in report['repaired']:
                        if apply_repair(scenes[index], row):
                            report['repaired'].append(index)
            except Exception as exc:
                report['failed'].append({'indices': indices, 'reason': type(exc).__name__})
        for future in pending:
            future.cancel()
            report['failed'].append({'indices': futures[future], 'reason': 'deadline'})
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    report['status'] = 'complete' if len(report['repaired']) == len(targets) else 'partial'
    report['unresolved'] = [i for i in targets if i not in report['repaired']]
    return report
