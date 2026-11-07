"""Base delivery on objective checks; retain optional review facts honestly."""
from collections.abc import Mapping


def delivery_quality_summary(render_metadata, remotion_manifest=None):
    metadata = render_metadata if isinstance(render_metadata, Mapping) else {}
    manifest = remotion_manifest if isinstance(remotion_manifest, Mapping) else {}
    review = metadata.get('creative_review') or manifest.get('creative_review') or {}
    review = review if isinstance(review, Mapping) else {}
    review_status = str(review.get('status') or 'not_requested')
    certified = review_status == 'pass' and review.get('llm_review_certified') is True
    technical = metadata.get('quality_checks') or {}
    technical = technical if isinstance(technical, Mapping) else {}
    release = metadata.get('release_review') or manifest.get('release_review') or {}
    release = release if isinstance(release, Mapping) else {}
    blockers = list(release.get('blockers') or []) + list(release.get('pending') or [])
    if technical.get('status') != 'pass':
        blockers.append('Objective export validation has not passed')
    if review_status == 'needs_revision':
        blockers.append('Rendered scenes have unresolved review findings')
    warnings = []
    timing = metadata.get('scene_timing') or {}
    if metadata.get('captions_enabled') and str(timing.get('timing_source') or '').startswith('tts_script_timing'):
        blockers.append('Caption timing is estimated; spoken word synchronization needs review')
    narration = metadata.get('narration_quality') or {}
    if narration and narration.get('status') != 'pass':
        blockers.append('Narration audibility validation has not passed')
    if review_status not in {'pass', 'pass_with_advisories'}:
        warnings.append('Optional visual review did not complete; no model approval is claimed.')
    metrics = manifest.get('metrics') or {}
    graphic_audit = metadata.get('rendered_graphics_audit') or (metrics.get('rendered_graphics_audit') if isinstance(metrics, Mapping) else None)
    if isinstance(graphic_audit, Mapping):
        audit_warnings = set(graphic_audit.get('warnings') or [])
        if 'explanatory_graphic_coverage_below_target' in audit_warnings:
            warnings.append('Explanatory graphics are below the preferred share.')
        if audit_warnings.intersection({'same_visible_graphic_treatment', 'adjacent_visible_graphic_repeat', 'same_rendered_graphic_layout'}):
            warnings.append('Graphic layouts repeat visibly.')
        if audit_warnings.intersection({'planned_graphics_not_visible_in_review_samples', 'graphic_visibility_samples_stale', 'graphic_visibility_samples_incomplete', 'graphic_audit_unavailable', 'graphic_visibility_not_sampled'}):
            warnings.append('Final graphic visibility was not fully sampled; no graphic certification is claimed.')
    elif isinstance(metrics, Mapping):
        try:
            coverage = float(metrics['graphic_scene_duration_ratio'])
            target = float(metrics.get('graphic_coverage_target', .3))
            if coverage + .05 < target:
                warnings.append('Explanatory graphics are below the preferred share.')
        except (KeyError, ValueError, TypeError):
            pass
    # Graphic coverage/style targets are advisories, not evidence of corrupt,
    # missing or blank media. Objective failures and actual unresolved findings
    # remain blockers regardless of optional model certification.
    # Export completion remains successful. Publish readiness also requires
    # content review: playable bytes alone previously approved a wrong-car film.
    review_approved = certified or review_status in {'pass', 'pass_with_advisories'}
    ready = not blockers and review_approved and not warnings
    return {
        'ready_to_publish': ready,
        'quality_status': 'validated' if ready else 'review_needed',
        'technical_status': str(technical.get('status') or 'not_completed'),
        'review_status': review_status,
        'review_performed': certified or bool(review.get('rounds') or review.get('scenes')),
        'llm_review_certified': certified,
        'blockers': list(dict.fromkeys(blockers)),
        'warnings': warnings,
    }
