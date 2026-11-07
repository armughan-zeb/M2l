"""Preserve exact clip identity when a parent salvages a completed worker cut."""
import hashlib
import json
import uuid
from pathlib import Path


def write_clip_provenance(path, scene_index, attempt):
    clip = Path(path)
    record = dict(attempt)
    record.update(scene_index=int(scene_index), path=str(clip.resolve()),
                  asset_sha256=hashlib.sha256(clip.read_bytes()).hexdigest())
    dest = clip.with_suffix(clip.suffix + '.audit.json')
    temp = dest.with_name(dest.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(record, ensure_ascii=True), encoding='utf-8')
        temp.replace(dest)
    finally:
        temp.unlink(missing_ok=True)


def load_clip_provenance(path, scene_index):
    try:
        clip = Path(path)
        record = json.loads(clip.with_suffix(clip.suffix + '.audit.json').read_text(encoding='utf-8'))
        if record.get('scene_index') != int(scene_index) or record.get('status') != 'downloaded':
            return {}
        if record.get('asset_sha256') != hashlib.sha256(clip.read_bytes()).hexdigest():
            return {}
        return record
    except (OSError, ValueError, TypeError):
        return {}
