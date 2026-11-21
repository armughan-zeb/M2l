"""Periodic YouTube cookie-pool health watchdog + early warning.

Problem: a revoked YouTube session is only discovered when a job's
YouTube lane fails (0 candidates). The pool probe in
``youtube_session.ConfiguredYouTubeSessions._check_account`` already
knows how to verify an account, but nothing calls it outside a job.

This module re-verifies the configured pool on a slow tick (default
once per pool verify TTL, 1800s) and, when an account flips to
signed_out, surfaces it both ways:

(a) a one-line server log with the account number + reason — never a
    cookie value (reasons come from the pool's redacted health
    snapshot only); and
(b) a needs-user signal the server can surface: a small redacted
    state file (``youtube-session-watchdog.json`` under the runtime
    root) plus ``attach_to_status()``, which ``/api/status`` merges
    into its ``youtube_session`` field.

Why a state file and not the job debugger's ``mark_needs_human``:
that mechanism flags one specific job (``needs_human`` on the queue
item) and makes the driver skip it. A dead pool with no job in flight
has no job to flag, and flagging an unrelated processing job would
misattribute the failure and block its recovery. The pool-level
signal therefore lives in the status file + ``/api/status``, where
the UI already shows ``youtube_session.pool_message`` — so the
operator is told to re-export fresh cookies BEFORE the next job
fails, not after it.

Cheap by design:

* at most one probe per account per verify TTL — the pool itself
  enforces that inside ``_check_account`` (verified jars are cached,
  signed-out accounts are re-probed at most once per TTL), and this
  module additionally skips whole checks inside its own interval;
* no new thread: the hook is one best-effort call from the job
  debugger's existing 120s ``tick()`` (see ``JobDebugger.tick``), plus
  a standalone ``python -m services.youtube_session_watchdog`` entry
  point for a manual check;
* never during an active render: ``job_debugger.has_live_render_child``
  is consulted first (cheap /proc scan); a skipped check leaves the
  previous state untouched so a long render cannot push the next
  check out.

Honest limit: this detects a server-side revocation fast (within one
interval after the TTL expires); it cannot prevent YouTube revoking
the session in the first place. Only a fresh cookie export from the
operator restores signed-in access.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

LOG_PREFIX = "[yt-session-watchdog]"

# Statuses that mean "this account cannot authenticate any more".
_DEAD_STATUSES = {"signed_out", "load_error"}

# Default check interval (seconds). Aligned with the pool's own verify
# TTL (VIDZEO_YT_POOL_VERIFY_TTL_SECONDS, also 1800s) so a verified
# account is re-probed just as its cached verdict expires.
_DEFAULT_INTERVAL_SECONDS = 1800.0


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def interval_seconds() -> float:
    """Seconds between watchdog checks (0 = check on every call)."""
    return _env_float("VIDZEO_YT_SESSION_WATCHDOG_INTERVAL_SECONDS", _DEFAULT_INTERVAL_SECONDS)


def enabled() -> bool:
    return _env_bool("VIDZEO_YT_SESSION_WATCHDOG_ENABLED", True)


def state_file() -> Path:
    """Path of the redacted watchdog state file (no cookie values)."""
    configured = str(os.environ.get("VIDZEO_YT_SESSION_WATCHDOG_STATE_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    try:
        from services.job_debugger import runtime_root

        return runtime_root() / "server" / "youtube-session-watchdog.json"
    except Exception:
        return Path.cwd() / "temp" / "youtube-session-watchdog.json"


def _log(message: str) -> None:
    print(f"{LOG_PREFIX} {message}", flush=True)


def _empty_state() -> Dict[str, Any]:
    return {
        "checked_at": 0.0,
        "needs_reexport": False,
        "healthy_accounts": 0,
        "total_accounts": 0,
        "accounts": [],
        "alerts": [],
        "message": "",
    }


def read_state(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read the last watchdog state (redacted); {}-safe when never run."""
    target = path or state_file()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_state()
        merged = _empty_state()
        merged.update(data)
        return merged
    except (OSError, ValueError, TypeError):
        return _empty_state()


def _save_state(state: Mapping[str, Any], path: Optional[Path] = None) -> None:
    target = path or state_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(dict(state), indent=2), encoding="utf-8")
        os.replace(tmp_path, target)
    except OSError as exc:
        _log(f"state save failed: {exc}")


def _render_active() -> bool:
    """True when a remotion/ffmpeg render child is alive (cheap check).

    Returns False when the signal is unavailable, so a missing render
    gate never blocks the watchdog — the probe itself is one cheap
    HTTP GET per account at most per TTL.
    """
    try:
        from services.job_debugger import has_live_render_child

        return bool(has_live_render_child())
    except Exception:
        return False


def _carried_alert(old: Mapping[str, Any], now: float, reason: str) -> Dict[str, Any]:
    """An earlier death alert kept alive by a still-dead / inconclusive probe."""
    kept = dict(old)
    kept["last_seen_at"] = now
    kept["reason"] = reason
    return kept


def _alert_message(account: int, status: str, reason: str) -> str:
    return (
        f"YouTube account {account} {status} ({reason or 'youtube-reported-signed-out'}): "
        "re-export fresh YouTube cookies from the operator browser before the next job — "
        "signed-in access is gone until then."
    )


def check(
    now: Optional[float] = None,
    pool: Any = None,
    state_path: Optional[Path] = None,
    *,
    force: bool = False,
    render_active: Optional[bool] = None,
) -> Dict[str, Any]:
    """Re-verify the pool once (TTL-gated) and record flips to signed_out.

    Returns a summary dict: ``checked`` (whether a probe ran),
    ``skipped_render`` / ``skipped_ttl`` / ``disabled`` reasons,
    ``alerts`` (newly dead accounts this check), plus the full state
    written to the state file. Never raises for a bad pool or a bad
    state file; a failed check leaves the previous state in place.
    """
    now = time.time() if now is None else float(now)
    target = state_path or state_file()
    previous = read_state(target)
    if not enabled() and not force:
        return {**previous, "checked": False, "disabled": True, "alerts": []}
    if not force and previous.get("checked_at"):
        if now - float(previous.get("checked_at") or 0) < interval_seconds():
            return {**previous, "checked": False, "skipped_ttl": True, "alerts": []}
    active = _render_active() if render_active is None else bool(render_active)
    if active:
        # Do not touch last_checked: a long render must not push the
        # next check out. The previous state stays authoritative.
        return {**previous, "checked": False, "skipped_render": True, "alerts": []}

    if pool is None:
        try:
            from services import youtube_session

            pool = youtube_session.get_configured_pool()
        except Exception as exc:
            _log(f"pool unavailable: {exc}")
            return {**previous, "checked": False, "error": str(exc), "alerts": []}

    # Probe every account through the pool's own verify path. The pool
    # caches verified verdicts and re-probes a signed-out account at
    # most once per verify TTL, so this loop never burns the search
    # wall even if the watchdog tick runs hot.
    try:
        paths = list(getattr(pool, "paths", []) or [])
        for index in range(len(paths)):
            try:
                pool._check_account(index)
            except Exception as exc:  # one bad account never kills the sweep
                _log(f"account {index + 1} probe failed: {type(exc).__name__}")
        snapshot = list(pool.health_snapshot())
    except Exception as exc:
        _log(f"pool probe failed: {exc}")
        return {**previous, "checked": False, "error": str(exc), "alerts": []}

    prev_by_account = {
        int(item.get("account") or 0): str(item.get("status") or "")
        for item in (previous.get("accounts") or [])
        if isinstance(item, dict)
    }
    prev_alerts = {
        int(item.get("account") or 0)
        for item in (previous.get("alerts") or [])
        if isinstance(item, dict)
    }
    prev_alert_by_account = {
        int(item.get("account") or 0): item
        for item in (previous.get("alerts") or [])
        if isinstance(item, dict)
    }
    healthy = sum(1 for item in snapshot if str(item.get("status") or "") == "verified")
    alerts: List[Dict[str, Any]] = []
    # Alerts carried over: accounts still dead, plus accounts whose
    # last conclusive verdict was death and whose latest probe was
    # inconclusive (unverified) — a network blip is not a recovery;
    # only a verified verdict clears a death alert.
    live_alerts: List[Dict[str, Any]] = []
    still_dead: List[str] = []
    for item in snapshot:
        account = int(item.get("account") or 0)
        status = str(item.get("status") or "")
        reason = str(item.get("reason") or "")[:120]
        was_alerted = account in prev_alerts
        if status in _DEAD_STATUSES:
            still_dead.append(str(account))
            if prev_by_account.get(account) != status or not was_alerted:
                # Flip into dead (or first sighting of a dead account):
                # log once, record once. Repeat ticks stay silent.
                message = _alert_message(account, status, reason)
                _log(message)
                alerts.append({
                    "account": account,
                    "status": status,
                    "reason": reason,
                    "message": message,
                    "first_seen_at": now,
                    "last_seen_at": now,
                })
            else:
                live_alerts.append(_carried_alert(prev_alert_by_account[account], now, reason))
        elif status == "verified":
            if was_alerted:
                _log(
                    f"account {account} recovered ({status}) — "
                    "fresh cookies are working again"
                )
        elif was_alerted:
            still_dead.append(str(account))
            live_alerts.append(_carried_alert(prev_alert_by_account[account], now, reason))
    # Alerts raised this check join the carried-over live ones.
    all_alerts = live_alerts + alerts
    needs_reexport = healthy == 0 and bool(snapshot) and bool(still_dead)
    message = ""
    if needs_reexport:
        message = (
            "YouTube cookie pool signed out"
            + (f" (account {', '.join(still_dead)})" if still_dead else "")
            + " — re-export fresh cookies for signed-in access; "
            "downloads run anonymous until then"
        )
    state: Dict[str, Any] = {
        "checked_at": now,
        "needs_reexport": needs_reexport,
        "healthy_accounts": healthy,
        "total_accounts": len(snapshot),
        "accounts": [
            {
                "account": int(item.get("account") or 0),
                "file": str(item.get("file") or ""),
                "status": str(item.get("status") or ""),
                "reason": str(item.get("reason") or "")[:120],
                "transient": bool(item.get("transient")),
            }
            for item in snapshot
        ],
        "alerts": all_alerts,
        "message": message,
    }
    _save_state(state, target)
    return {**state, "checked": True, "skipped_render": False, "alerts": alerts}


def attach_to_status(youtube_session_status: Mapping[str, Any]) -> Dict[str, Any]:
    """Merge watchdog state into an ``/api/status`` youtube_session dict.

    Adds a ``watchdog`` sub-dict (last check, needs_reexport, alerts)
    and, when the pool is dead but the pool message is empty (the pool
    has never been probed by a job, so ``session_status()`` says
    nothing), fills ``pool_message`` from the watchdog alert. Pure
    function over the passed dict; never raises, never probes.
    """
    status = dict(youtube_session_status or {})
    try:
        watchdog = read_state()
    except Exception:
        watchdog = _empty_state()
    status["watchdog"] = {
        "checked_at": float(watchdog.get("checked_at") or 0),
        "needs_reexport": bool(watchdog.get("needs_reexport")),
        "healthy_accounts": int(watchdog.get("healthy_accounts") or 0),
        "total_accounts": int(watchdog.get("total_accounts") or 0),
        "accounts": list(watchdog.get("accounts") or []),
        "alerts": list(watchdog.get("alerts") or []),
        "message": str(watchdog.get("message") or ""),
    }
    if status["watchdog"]["needs_reexport"] and not str(status.get("pool_message") or ""):
        status["pool_message"] = status["watchdog"]["message"] or (
            "YouTube cookie pool signed out — re-export fresh cookies for signed-in access"
        )
    return status


def main() -> Dict[str, Any]:
    """Standalone entry point: one check, JSON summary on stdout."""
    result = check(force=True)
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    main()
