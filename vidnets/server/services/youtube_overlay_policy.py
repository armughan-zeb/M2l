"""Keep YouTube source pixels sharp; incidental source labels are not failures."""
from collections.abc import Mapping

_TEXT = {'caption', 'subtitle', 'subtitles', 'text_overlay', 'large_text_band'}
_HARD = {'title_card', 'central_logo', 'large_logo', 'full_screen_text', 'creator_cta'}


def preserve_source_overlays(screen):
    result = dict(screen or {})
    regions = result.get('regions') or result.get('blur_regions') or result.get('detected_regions') or []
    types = {str(value).lower() for value in result.get('overlay_types') or []}
    reason = str(result.get('rejection_reason') or '').lower()
    obstructive = bool(types & _HARD or result.get('outro_detected') or result.get('talking_head'))
    for region in regions:
        if not isinstance(region, Mapping):
            continue
        kind = str(region.get('type') or '').lower()
        types.add(kind)
        if kind in _HARD:
            obstructive = True
        box = region.get('bbox') or []
        if len(box) != 4:
            continue
        try:
            x1, y1, x2, y2 = [float(value) for value in box]
            area = max(0, x2 - x1) * max(0, y2 - y1)
        except (TypeError, ValueError):
            continue
        # Bounding boxes supplied by the screen are normalized coordinates.
        if kind in _TEXT and (area > .24 or (area > .12 and y1 < .45 and y2 > .35)):
            obstructive = True
        if kind in {'watermark', 'logo'} and area > .12:
            obstructive = True
    cosmetic = bool(types & (_TEXT | {'watermark', 'logo'}) or result.get('has_subtitle'))
    hard_reason = any(term in reason for term in ('title_card', 'outro', 'presenter', 'talking_head', 'facecam', 'creator_cta', 'unsafe', 'mismatch', 'low_quality', 'black_frame'))
    if result.get('facecam_inset') and not result.get('facecam_tolerated'):
        obstructive = True
    cosmetic_reason = (not reason or reason == 'overlay_rejected'
                       or any(term in reason for term in ('caption', 'subtitle', 'watermark', 'text_overlay', 'burned_in_text', 'logo_overlay')))
    if cosmetic and cosmetic_reason and not obstructive and not hard_reason:
        result['reject'] = False
        result['rejection_reason'] = ''
    # Never remediate source footage with Gaussian/box blur, even if an older
    # project requests it. Keep detection/provenance separately from usability.
    result.update(no_blur=True, blurred=False, blur_failed=False,
                  blur_regions=[], regions=[], cleaned_path='',
                  detected_regions=list(regions), overlay_types=sorted(types),
                  source_overlays_preserved=not obstructive and not result.get('reject', False),
                  overlay_policy='preserve_source_no_blur')
    result['clean'] = not result.get('reject', False) and not obstructive
    if obstructive:
        result['reject'] = True
        result['rejection_reason'] = reason or 'obstructive_source_overlay'
    if cosmetic and result['clean']:
        result['watermark_tolerated'] = bool(types & {'watermark', 'logo'} or result.get('watermark_tolerated'))
    return result


def source_overlays_preserved(candidate):
    if (candidate.get('reject') or candidate.get('overlay_clean') is False
            or candidate.get('overlay_rejected') or candidate.get('overlay_blurred')
            or candidate.get('blurred')):
        return False
    return bool(candidate.get('source_overlays_preserved')
                or (candidate.get('overlay_policy') == 'preserve_source_no_blur'
                    and candidate.get('clean', candidate.get('overlay_clean')) is True))
