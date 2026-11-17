#!/usr/bin/env python3
"""Report bridge inbox health and expire overdue requests without racing answers."""

import glob
import hashlib
import json
import os
import statistics
import sys
import tempfile
import time


KNOWN_OPERATIONS = {
    'scriptit.completion', 'scriptit.research-synthesis',
    'vidzeo_editorial_beats_v1', 'vidzeo_editing_profile_v1',
    'vidnets_film_emphasis_v1', 'vidnets_scene_quality_revision_v1',
    'vidzeo_independent_claim_review_v1', 'vidzeo_scene_queries_v3',
    'vidnets_scene_direction_v1', 'vidnets_art_direction_v1',
    'vidnets_scene_composition_v1', 'vidnets_scene_composition_repair_v1',
    'vidnets_copy_repair_v1', 'vidzeo_pixel_redesign_v1',
    'vidnets_response', 'visual_review',
    'muse.inbox_probe',
}


def read_json(path):
    with open(path, encoding='utf-8') as stream:
        return json.load(stream)


def report_once(directory, key, dry_run):
    marker = os.path.join(directory, hashlib.sha256(key.encode('utf-8')).hexdigest())
    if os.path.exists(marker):
        return False
    if dry_run:
        return True
    os.makedirs(directory, exist_ok=True)
    try:
        descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    os.close(descriptor)
    return True


def _lock_request(lock_path):
    """Acquire the same sibling lock used by agent_bridge.complete_request."""
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == 'nt':
            import msvcrt
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b'0')
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except Exception:
        os.close(descriptor)
        return None


def _unlock_request(descriptor):
    if os.name == 'nt':
        import msvcrt
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


def mark_expired(path, dry_run):
    if dry_run:
        return read_json(path), 'WOULD mark expired'
    descriptor = _lock_request(path + '.lock')
    if descriptor is None:
        return read_json(path), 'could not acquire request lock; left unchanged'
    try:
        row = read_json(path)
        if row.get('state') != 'pending' or float(row.get('expires_at') or 0) > time.time():
            return row, 'state changed while checking; left unchanged'
        row['state'] = 'expired'
        temp_fd, temporary = tempfile.mkstemp(prefix='.request-', suffix='.tmp', dir=os.path.dirname(path))
        try:
            with os.fdopen(temp_fd, 'w', encoding='utf-8') as stream:
                json.dump(row, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return row, 'marked expired under the bridge request lock'
    finally:
        _unlock_request(descriptor)


def inspect_bridge(root, state_dir, dry_run=False, now=None):
    now = time.time() if now is None else float(now)
    observed = {}
    pending_rows = []
    expired_by_job = {}
    alerts = []
    warnings = []
    latencies = {}
    reported_dir = os.path.join(state_dir, 'reported-expired')
    reported_jobs_dir = os.path.join(state_dir, 'reported-expired-jobs')

    def metric(operation):
        return observed.setdefault(operation, {
            'pending': 0, 'answered': 0, 'expired': 0,
            'oldest_pending_age_s': 0, 'answered_last_60s': 0,
        })

    for path in glob.glob(os.path.join(root, '*', 'request.json')):
        try:
            row = read_json(path)
            operation = str(row.get('operation') or 'unknown')
            state = str(row.get('state') or 'unknown')
            request_id = str(row.get('id') or os.path.basename(os.path.dirname(path)))
            job_id = str(row.get('job_id') or '')
            created = float(row.get('created_at') or now)
            age = max(0, int(now - created))
            values = metric(operation)

            if state == 'pending' and float(row.get('expires_at') or 0) <= now:
                row, action = mark_expired(path, dry_run)
                state = str(row.get('state') or 'unknown')
                if state == 'expired' and report_once(reported_dir, request_id, dry_run):
                    alerts.append(
                        f'inbox: request {request_id} ({operation}, job {job_id or "none"}) expired; {action}'
                    )
                elif state == 'pending':
                    values['pending'] += 1
                    values['oldest_pending_age_s'] = max(values['oldest_pending_age_s'], age)
                    pending_rows.append((age, request_id, operation, job_id))
                    if action == 'WOULD mark expired':
                        if report_once(reported_dir, request_id, dry_run):
                            alerts.append(
                                f'inbox: request {request_id} ({operation}, job {job_id or "none"}) expired; {action}'
                            )
                    else:
                        warnings.append(f'inbox: request {request_id} expired but {action}')
            elif state == 'pending':
                values['pending'] += 1
                values['oldest_pending_age_s'] = max(values['oldest_pending_age_s'], age)
                pending_rows.append((age, request_id, operation, job_id))
                if age >= 60:
                    warnings.append(
                        f'inbox: request {request_id} ({operation}, job {job_id or "none"}) '
                        f'is pending {age}s; operator SLO is 60s'
                    )

            if state == 'expired':
                values['expired'] += 1
                if job_id:
                    expired_by_job.setdefault(job_id, []).append(request_id)
                if report_once(reported_dir, request_id, dry_run):
                    alerts.append(f'inbox: request {request_id} ({operation}, job {job_id or "none"}) is expired')
            elif state == 'answered':
                values['answered'] += 1
                response = os.path.join(os.path.dirname(path), 'response.json')
                if os.path.isfile(response):
                    response_time = os.path.getmtime(response)
                    latencies.setdefault(operation, []).append(max(0, int((response_time - created) * 1000)))
                    if response_time >= now - 60:
                        values['answered_last_60s'] += 1
        except Exception as error:  # keep monitoring independent of a malformed old request
            request_id = os.path.basename(os.path.dirname(path))
            warnings.append(f'inbox: cannot inspect {request_id}: {type(error).__name__}: {str(error)[:180]}')

    for job_id, request_ids in expired_by_job.items():
        if len(request_ids) >= 3 and report_once(reported_jobs_dir, job_id, dry_run):
            alerts.append(f'inbox: job {job_id} has {len(request_ids)} expired requests (3 or more is critical)')

    for operation, values in observed.items():
        samples = latencies.get(operation, [])
        if samples:
            ordered = sorted(samples)
            values['answer_latency_p50_ms'] = int(statistics.median(ordered))
            values['answer_latency_p95_ms'] = ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.95))]

    unknown_count = sum(
        values['pending'] + values['answered'] + values['expired']
        for operation, values in observed.items() if operation not in KNOWN_OPERATIONS
    )
    oldest = max(pending_rows) if pending_rows else None
    metrics = {
        'pending': sum(values['pending'] for values in observed.values()),
        'answered': sum(values['answered'] for values in observed.values()),
        'expired': sum(values['expired'] for values in observed.values()),
        'unknown_operation_count': unknown_count,
        'operations': observed,
        'oldest_pending': ({
            'request_id': oldest[1], 'operation': oldest[2], 'job_id': oldest[3], 'age_s': oldest[0],
        } if oldest else None),
    }
    return metrics, warnings, alerts


def main(argv):
    if len(argv) != 4:
        print('usage: bridge-health.py BRIDGE_DIR STATE_DIR DRY_RUN(0|1)', file=sys.stderr)
        return 64
    metrics, warnings, alerts = inspect_bridge(argv[1], argv[2], argv[3] == '1')
    print('METRICS_JSON:' + json.dumps(metrics, separators=(',', ':')))
    for line in warnings:
        print('WARN:' + line)
    for line in alerts:
        print('ALERT:' + line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
