"""Retain immutable media ledgers before an attempt replaces its checkpoint."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import copy
import threading

_PENDING_WEB_LOCK = threading.Lock()


def _scene_text_vector(contracts):
    return [' '.join(str(row.get('text') or '' if isinstance(row, dict) else getattr(row, 'text', '')).split())
            for row in contracts]


def save_pending_web_candidates(package_dir, scene_contracts, candidates):
    """Checkpoint downloaded rows before the other source batch finishes."""
    if not package_dir:
        return
    from .media_asset_policy import is_synthetic_media
    target = Path(package_dir) / 'checkpoints' / 'provider_web_candidates.json'
    texts = _scene_text_vector(scene_contracts)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _PENDING_WEB_LOCK:
        payload = {'version': 1, 'scene_texts': texts, 'candidates': {}}
        try:
            previous = json.loads(target.read_text(encoding='utf-8'))
            if (isinstance(previous, dict) and previous.get('scene_texts') == texts
                    and isinstance(previous.get('candidates'), dict)):
                payload = previous
        except (OSError, ValueError):
            pass
        for index, values in candidates.items():
            if not 0 <= int(index) < len(texts):
                continue
            values = values if isinstance(values, (tuple, list)) else [values]
            valid = [dict(row) for row in values if isinstance(row, dict)
                     and Path(str(row.get('path') or row.get('selected_path') or '')).is_file()
                     and not is_synthetic_media(str(row.get('path') or row.get('selected_path') or ''))]
            if valid:
                payload['candidates'][str(index)] = valid[:4]
        temporary = target.with_name('.' + target.name + '.' + secrets.token_hex(4) + '.part')
        try:
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(payload, stream, ensure_ascii=False, default=str)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def _pending_web_candidates(package_dir, contracts):
    try:
        payload = json.loads((Path(package_dir) / 'checkpoints' / 'provider_web_candidates.json').read_text(encoding='utf-8'))
        if not isinstance(payload, dict) or payload.get('scene_texts') != _scene_text_vector(contracts):
            return {}
        if not isinstance(payload.get('candidates'), dict):
            return {}
        return {int(index): rows for index, rows in payload.get('candidates', {}).items()
                if 0 <= int(index) < len(contracts) and isinstance(rows, list)}
    except (OSError, ValueError, TypeError):
        return {}


def archive_media_checkpoint(target):
    target = Path(target)
    if not target.is_file():
        return None
    data = target.read_bytes()
    # Do not archive malformed checkpoints as authoritative recovery inputs.
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise ValueError('Saved media checkpoint must be a JSON object')
    history = target.parent / 'media_history'
    history.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    destination = history / ('media_plan-' + digest + '.json')
    if destination.is_file():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise OSError('Media checkpoint history digest mismatch')
        return destination
    temporary = destination.with_name('.' + destination.name + '.' + secrets.token_hex(4) + '.part')
    try:
        with temporary.open('wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def reconcile_saved_media_order(media_plan, scene_contracts):
    """Undo old ledger swaps only with a complete, text-proven permutation."""
    plan = copy.deepcopy(media_plan)
    rows = list((plan.get('metadata') or {}).get('media_resolution_v2') or [])
    paths, labels = list(plan.get('media_paths') or []), list(plan.get('source_labels') or [])
    count = len(scene_contracts)
    if not count or any(len(value) != count for value in (rows, paths, labels)):
        raise ValueError('Saved media cardinality does not match the narration')
    orders = []
    for row in rows:
        context = row.get('final_scene_context') or {}
        order = context.get('order')
        if not isinstance(order, int) or isinstance(order, bool) or not 0 <= order < count:
            raise ValueError('Saved media has no proven original scene order')
        contract = scene_contracts[order]
        narration = contract.get('text', '') if isinstance(contract, dict) else getattr(contract, 'text', '')
        if ' '.join(str(context.get('text') or '').split()) != ' '.join(str(narration or '').split()):
            raise ValueError('Saved media narration differs; retrieval must repair this scene')
        orders.append(order)
    if len(set(orders)) != count:
        raise ValueError('Saved media scene order is ambiguous')
    indices = sorted(range(count), key=lambda index: orders[index])
    plan['media_paths'] = [paths[index] for index in indices]
    plan['source_labels'] = [labels[index] for index in indices]
    plan['metadata']['media_resolution_v2'] = [rows[index] for index in indices]
    plan['metadata']['checkpoint_order_repair'] = {
        'moved_slots': sum(index != order for index, order in enumerate(orders)),
        'proof': 'unique_original_order_and_matching_narration',
    }
    return plan


def saved_web_candidates(package_dir, scene_contracts, limit=3):
    """Recall exact saved files; current live verifier still owns acceptance."""
    from .media_asset_policy import is_synthetic_media
    from .web_image.safety import candidate_is_safe
    from .pipeline_v2_media_adapter import _has_bound_pixel_review, VISUAL_REVIEW_POLICY_VERSION
    from .visual_intelligence.semantic_guard import explicit_domain_conflict
    from .visual_requirement import candidate_asset_metadata
    story = ' '.join(str(row.get('text') or '') if isinstance(row, dict) else str(getattr(row, 'text', ''))
                     for row in scene_contracts)
    checkpoint = Path(package_dir) / 'checkpoints' / 'media_plan.json'
    pending = _pending_web_candidates(package_dir, scene_contracts)
    try:
        plan = reconcile_saved_media_order(json.loads(checkpoint.read_text(encoding='utf-8')), scene_contracts)
    except (OSError, ValueError, TypeError):
        if not pending:
            return {}
        plan = {'metadata': {'media_resolution_v2': [{} for _ in scene_contracts]}}
    result = {}
    # A shared provider can finish downloads after an older scene waiter timed
    # out. Those files are recorded in its scene-indexed audit, not necessarily
    # in the coordinator's finalist ledger. Recall them as pending candidates;
    # never promote their old search metadata into a current visual approval.
    audited = {}
    web_audit = plan.get('metadata', {}).get('provider_audit', {}).get('web', {})
    audit_rows = web_audit.get('scenes') or []
    index_map = web_audit.get('scene_indices')
    if index_map is None and len(audit_rows) == len(scene_contracts):
        index_map = list(range(len(scene_contracts)))
    # A compressed recovery batch needs its explicit original-index mapping.
    # Never guess that local scene 1 belongs to documentary scene 1.
    for candidate in audit_rows if isinstance(index_map, list) else []:
        if not isinstance(candidate, dict):
            continue
        try:
            local_index = int(candidate.get('scene_number', 0)) - 1
            if not 0 <= local_index < len(index_map):
                continue
            index = int(index_map[local_index])
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(scene_contracts):
            audited[index] = [dict(candidate, provider='web', source='web'),
                              *(candidate.get('alternates') or [])]
    for index, row in enumerate(plan['metadata']['media_resolution_v2']):
        candidates = [row, *(row.get('alternates') or []), *(row.get('candidate_ledger') or []), *audited.get(index, []), *pending.get(index, [])]
        requirement = scene_contracts[index].get('visual_requirement') if isinstance(scene_contracts[index], dict) else getattr(scene_contracts[index], 'visual_requirement', {})
        target_hash = str((requirement or {}).get('requirement_hash') or '')
        kept, seen = [], set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            path = str(candidate.get('selected_path') or candidate.get('path') or '')
            provider = str(candidate.get('source') or candidate.get('provider') or '').casefold()
            if not path or path in seen or not Path(path).is_file() or is_synthetic_media(path):
                continue
            if provider not in {'web', 'web images', 'borrowed'} and not provider.startswith('universal_'):
                continue
            if not candidate_is_safe(candidate)[0]:
                continue
            if explicit_domain_conflict(story, candidate_asset_metadata(candidate)):
                continue
            decision = candidate.get('visual_decision') or candidate.get('qwen_result') or {}
            if (isinstance(decision, dict) and decision.get('verified_requirement_hash') == target_hash
                    and decision.get('visual_review_policy_version') == VISUAL_REVIEW_POLICY_VERSION
                    and decision.get('accepted') is False):
                continue
            recall = dict(candidate, path=path, selected_path=path, provider='web', source='web',
                          media_type='image', semantic_context_pending=True,
                          visual_verification_required=True, verification_status='pending_visual_verification',
                          checkpoint_recalled=True)
            # A receipt for a previous requirement cannot approve the current
            # scene. Let the verifier reuse only its own exact-context cache.
            recall.pop('visual_decision', None)
            recall.pop('qwen_result', None)
            recall.pop('identity_failed', None)
            recall.pop('entity_conflict', None)
            recall['requirement_hash'] = target_hash
            recall['candidate_evidence'] = {'metadata_source': 'saved_asset', 'verification_required': True,
                                          'requirement_hash': target_hash}
            recall['gate_outcomes'] = {'checkpoint_file_exists': True}
            recall['candidate_id'] = 'saved-web:' + hashlib.sha256(path.encode('utf-8')).hexdigest()[:20]
            if _has_bound_pixel_review(requirement, candidate, path):
                receipt = dict(candidate['visual_decision'], candidate_id=recall['candidate_id'])
                recall.update(visual_decision=receipt, qwen_result=dict(receipt),
                              semantic_context_pending=False, semantic_visual_pending=False,
                              visual_verification_required=False, verification_status='verified_reuse')
            else:
                recall['rejection_reason'] = ''
                recall['identity_gate_reason'] = ''
            seen.add(path)
            kept.append(recall)
            if len(kept) >= max(1, min(4, int(limit))):
                break
        if kept:
            result[index] = kept
    return result


def saved_youtube_candidates(package_dir, scene_contracts):
    """Retain completed moving clips only with exact current review/byte proof."""
    from .media_asset_policy import is_synthetic_media
    from .pipeline_v2_media_adapter import _has_bound_pixel_review, VISUAL_REVIEW_POLICY_VERSION
    try:
        target = Path(package_dir) / 'checkpoints' / 'media_plan.json'
        plan = reconcile_saved_media_order(json.loads(target.read_text(encoding='utf-8')), scene_contracts)
    except (OSError, ValueError, TypeError):
        return {}
    result, used = {}, set()
    for index, row in enumerate(plan['metadata']['media_resolution_v2']):
        if row.get('provider') != 'youtube' or row.get('media_type') != 'video' or row.get('status') != 'accepted':
            continue
        path = str(row.get('selected_path') or '')
        requirement = (scene_contracts[index].get('visual_requirement') if isinstance(scene_contracts[index], dict)
                       else getattr(scene_contracts[index], 'visual_requirement', {})) or {}
        review = row.get('visual_decision') or {}
        identity = review.get('verified_asset_sha256')
        if (not path or is_synthetic_media(path) or not row.get('source_url') or identity in used
                or review.get('visual_review_policy_version') != VISUAL_REVIEW_POLICY_VERSION
                or not _has_bound_pixel_review(requirement, row, path)):
            continue
        result[index] = dict(row, path=path, source='youtube', checkpoint_completed=True,
                             checkpoint_recalled=True, qwen_result=dict(review))
        used.add(identity)
    return result


def saved_stock_candidates(package_dir, scene_contracts):
    """Recall licensed stock only with exact narration/order and byte proof."""
    from .media_asset_policy import is_synthetic_media
    try:
        target = Path(package_dir) / 'checkpoints' / 'media_plan.json'
        plan = reconcile_saved_media_order(json.loads(target.read_text(encoding='utf-8')), scene_contracts)
    except (OSError, ValueError, TypeError):
        return {}
    result = {}
    for index, row in enumerate(plan['metadata']['media_resolution_v2']):
        if str(row.get('provider') or '').casefold() not in {'stock', 'pexels', 'pixabay'}:
            continue
        path = str(row.get('selected_path') or '')
        fingerprint = str(row.get('asset_fingerprint') or '')
        if not path or not Path(path).is_file() or is_synthetic_media(path) or len(fingerprint) != 64:
            continue
        digest = hashlib.sha256()
        try:
            with open(path, 'rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
        except OSError:
            continue
        if digest.hexdigest() != fingerprint:
            continue
        original = next((candidate for candidate in row.get('candidate_ledger') or []
                         if isinstance(candidate, dict) and str(candidate.get('path') or '') == path), None)
        if not original or not original.get('source_url') or not original.get('license'):
            continue
        requirement = (scene_contracts[index].get('visual_requirement') if isinstance(scene_contracts[index], dict)
                       else getattr(scene_contracts[index], 'visual_requirement', {})) or {}
        result[index] = dict(original, path=path, provider='stock', source='stock',
                             checkpoint_recalled=True, stock_last_resort=True,
                             checkpoint_completed=bool(row.get('status') == 'accepted'
                                and requirement.get('requirement_hash')
                                and row.get('requirement_hash') == requirement['requirement_hash']))
    return result
