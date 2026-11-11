"""Media manifest provenance (Nov 10, 2026 — BNSF incident).

Root cause: the CMS job r_mw85awoskks8lh was delivered the WRONG video.
Two Vidnets jobs shared the title "A BNSF Whiteout Crossing, Seen From
the Driver's Cab":

* 876f0e84 (local-queue job, Nov 9, 25 min, director failed with
  AgentBridgeError -> local director fallback): 10 scenes, 10/10
  "Grounded Graphic Fallback", 18 MB master in
  output/..._20261109_090133_cb5e09d2/.
* f04c6b9f (the CMS job's claim, Nov 10, 2h37m): 15 scenes, 15/15 real
  media (14 web + 1 YouTube clip), 277 MB master in the daemon work dir.

The deliver step picked the stale output-package file by TITLE instead of
the claiming job's own queue result. The delivered manifest even said so:
its 10 media_paths all point at temp/jobs/876f0e84/... — another job's
temp dir — while the claiming job was f04c6b9f.

This module makes that mix-up impossible to miss:

* build_media_manifest() stamps the producing job id + run id into every
  manifest, so a manifest self-identifies its job.
* foreign_job_references() extracts distinct Vidnets job ids from
  temp/jobs/<id>/ path segments that do NOT belong to the claiming job.
* summarize_manifest() counts real vs graphic-fallback assets, so a
  delivery guard can refuse an all-graphic master.

The daemon-side guard lives in lib/masterManifestGuard.js and uses these
same rules (reimplemented in JS — the daemon cannot import Python).
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

_GRAPHIC_LABEL_TOKENS = ("graphic fallback", "grounded graphic", "generated graphic")

# temp/jobs/<vidnets-job-id>/... — the per-job render temp dir. A manifest
# whose media_paths point under another job's id belongs to that job, not
# to whoever is trying to deliver it.
_JOB_DIR_RE = re.compile(r"temp/jobs/([A-Za-z0-9._-]+)/")


def _job_id_from_config(job_config: Optional[Mapping[str, Any]]) -> str:
    if not isinstance(job_config, Mapping):
        return ""
    for key in ("_queue_job_id", "job_id", "queued_id"):
        value = str(job_config.get(key) or "").strip()
        if value:
            return value
    return ""


def _run_id_from_config(job_config: Optional[Mapping[str, Any]]) -> str:
    if not isinstance(job_config, Mapping):
        return ""
    for key in ("_pipeline_run_id", "_queue_run_id", "run_id"):
        value = str(job_config.get(key) or "").strip()
        if value:
            return value
    return ""


def build_media_manifest(
    media_plan: Optional[Mapping[str, Any]],
    job_config: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build the media/manifest.json payload, stamped with the job identity.

    The job_id/run_id stamp is the provenance anchor: a delivery guard can
    compare it against the claiming job instead of trusting a title match.
    """
    plan = media_plan if isinstance(media_plan, Mapping) else {}
    metadata = plan.get("metadata") if isinstance(plan.get("metadata"), Mapping) else {}
    return {
        "schema_version": 1,
        "job_id": _job_id_from_config(job_config),
        "run_id": _run_id_from_config(job_config),
        "asset_count": len(plan.get("media_paths") or []),
        "media_paths": list(plan.get("media_paths") or []),
        "source_labels": list(plan.get("source_labels") or []),
        "metadata": dict(metadata),
        "note": "Source media remains in the shared media cache; paths and provenance are preserved here for retry and audit.",
    }


def foreign_job_references(
    media_paths: Sequence[str],
    job_id: str,
) -> list[str]:
    """Distinct Vidnets job ids referenced by media paths, excluding job_id.

    A media path under temp/jobs/<other-id>/ belongs to another job's temp
    dir. The claiming job's own id is expected and never reported.
    """
    own = str(job_id or "").strip()
    found: list[str] = []
    for raw in media_paths or []:
        for match in _JOB_DIR_RE.finditer(str(raw or "")):
            other = match.group(1).strip()
            if other and other != own and other not in found:
                found.append(other)
    return found


def is_graphic_label(label: str) -> bool:
    text = str(label or "").strip().casefold()
    return any(token in text for token in _GRAPHIC_LABEL_TOKENS)


def _resolution_is_graphic(row: Mapping[str, Any]) -> bool:
    kind = str(row.get("resolution_kind") or "").strip().casefold()
    if kind in {"graphic_fallback", "grounded_graphic_fallback"}:
        return True
    return str(row.get("provider") or "").strip().casefold() == "graphic"


def summarize_manifest(manifest: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Count real vs graphic-fallback assets in a manifest.

    A scene counts as real media when its source label is not a graphic
    label, or when its media_resolution_v2 row is a non-graphic provider.
    """
    if not isinstance(manifest, Mapping):
        return {"asset_count": 0, "real_count": 0, "graphic_count": 0, "foreign_jobs": []}
    paths = list(manifest.get("media_paths") or [])
    labels = list(manifest.get("source_labels") or [])
    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), Mapping) else {}
    rows = metadata.get("media_resolution_v2") or []
    asset_count = int(manifest.get("asset_count") or len(paths) or len(labels) or 0)
    graphic_count = 0
    for index in range(max(len(paths), len(labels), len(rows) if isinstance(rows, Sequence) else 0)):
        row = rows[index] if isinstance(rows, Sequence) and index < len(rows) and isinstance(rows[index], Mapping) else {}
        label = labels[index] if index < len(labels) else ""
        if row:
            graphic = _resolution_is_graphic(row)
        else:
            graphic = is_graphic_label(label)
        if graphic:
            graphic_count += 1
    real_count = max(0, asset_count - graphic_count) if asset_count else 0
    return {
        "asset_count": asset_count,
        "real_count": real_count,
        "graphic_count": graphic_count,
        "foreign_jobs": foreign_job_references(paths, str(manifest.get("job_id") or "")),
    }


def manifest_matches_job(manifest: Optional[Mapping[str, Any]], job_id: str) -> bool:
    """True when the manifest's stamped job id agrees with job_id (or is absent).

    Manifests written before the stamp existed carry no job_id and cannot
    contradict — they are checked by path instead (foreign_job_references).
    """
    if not isinstance(manifest, Mapping):
        return True
    stamped = str(manifest.get("job_id") or "").strip()
    if not stamped:
        return True
    return stamped == str(job_id or "").strip()
