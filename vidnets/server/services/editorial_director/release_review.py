"""Separate render correctness from creative and factual acceptance."""
from collections.abc import Mapping
from .production_contract import visible_perceptual_repeats


def release_review(manifest, technical=None):
    technical = technical if isinstance(technical,Mapping) else {}
    creative = manifest.get('creative_review') or {}
    research = manifest.get('research_ledger') or {}
    director = manifest.get('director') or {}
    repeats = (manifest.get('media_shot_plan') or {}).get('perceptual_repetition_audit') or {}
    blockers, pending, advisories = [], [], []
    if technical.get('status') != 'pass':
        pending.append('Technical render acceptance not recorded')
    # A completed export remains available while acceptance is pending.
    # Stream/codec checks alone do not certify its factual or visual content.
    if not creative and not research:
        pending.append('Creative and factual acceptance has not been reviewed')
    if creative.get('status') != 'pass':
        if creative:
            advisories.append('Optional creative review did not complete')
        rounds = creative.get('rounds') or []
        last_round = rounds[-1] if rounds and isinstance(rounds[-1], Mapping) else {}
        unresolved_media = sorted({
            row['scene_index'] for row in last_round.get('scenes') or []
            if isinstance(row, Mapping) and isinstance(row.get('scene_index'), int)
            and row.get('status') == 'needs_revision' and row.get('media_repair_required')
        })
        if unresolved_media:
            pending.append('Previously flagged media accuracy issues await final verification in scenes: '
                           + ', '.join(str(index) for index in unresolved_media))
    if director.get('creative_coverage') == 'partial' or director.get('local_fallback_scene_indices'):
        advisories.append('Some scenes use local direction')
    visible_repeats, _hidden_repeats = visible_perceptual_repeats(manifest)
    if visible_repeats:
        blockers.append('Visually repeated media remains')
    if creative.get('status') == 'needs_revision':
        blockers.append('Rendered scenes need redesign')
    if any(row.get('status') == 'contested' for row in research.get('claims') or []):
        blockers.append('A narrated claim conflicts with reviewed source evidence')
    return {'status':'needs_revision' if blockers else ('needs_review' if pending else 'accepted'),
            'ready_to_publish':not blockers and not pending,
            'technical_status':technical.get('status','unreviewed'),
            'creative_status':creative.get('status','unreviewed'),
            'research_status':research.get('status','unreviewed'),
            'review_performed': creative.get('llm_review_certified') is True or bool(creative.get('rounds') or creative.get('scenes')),
            'blockers':blockers,'pending':pending,'advisories':advisories}
