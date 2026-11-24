"""Render gate: let a render in progress own the machine.

Python mirror of vidnets/scripts/render-gate.js — same two signals, same
env vars, same exit codes. Stdlib only, no imports from ``services`` (or
any other package), so pytest's conftest can load it by file path and the
CLI works from any directory.

Signals (either firing means a render is active):
  1. TCP lease port (default 47639, VIDZEO_RENDER_LEASE_PORT) — held by
     vidnets/scripts/render-remotion-manifest.js main() for the whole
     Node render. A connect that succeeds means a render owns the
     machine right now.
  2. Render lock file (VIDNETS_RENDER_LOCK_FILE, else
     ~/Vidnets Studio/render.lock — the same rule as
     services/remotion_render_gate.py) — held with flock by the server
     while its render call runs. Probed with a non-blocking flock:
     free means idle, held means a render is active. A missing file
     means no server render.

Both signals are held by the render itself and released by the OS on
crash/reboot (the socket closes, the flock dies with the fd), so there
is no stale status file to clean up. Probes never raise: an unprovable
signal is reported as unknown and treated as inactive, so a broken
probe degrades to "rely on the other signal", never to a hung caller.

CLI:
  python3 render_gate.py status [--json]   exit 0 idle, 2 render active
  python3 render_gate.py wait [--wait-seconds N] [--strict] [--json]
      exit 0 when idle; on timeout prints a warning and exits 0,
      unless --strict (exit 3).

Env:
  VIDZEO_RENDER_LEASE_PORT, VIDNETS_RENDER_LOCK_FILE,
  VIDZEO_RENDER_GATE_WAIT_SECONDS (default 1200 = 20 min, matches the
  Node lease max wait), VIDZEO_RENDER_GATE=off (``wait`` and the test
  gates skip waiting; ``status`` still reports the truth).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

DEFAULT_LEASE_PORT = 47639
DEFAULT_WAIT_SECONDS = 1200.0
PORT_PROBE_TIMEOUT_SECONDS = 0.5
WAIT_POLL_SECONDS = 2.0


def render_lease_port(env: Optional[os._Environ] = None) -> int:
    env = env if env is not None else os.environ
    try:
        raw = int(str(env.get("VIDZEO_RENDER_LEASE_PORT") or "").strip())
    except (TypeError, ValueError):
        return DEFAULT_LEASE_PORT
    return raw if 0 < raw < 65536 else DEFAULT_LEASE_PORT


def render_lock_file(env: Optional[os._Environ] = None) -> str:
    env = env if env is not None else os.environ
    explicit = str(env.get("VIDNETS_RENDER_LOCK_FILE") or "").strip()
    if explicit:
        return explicit
    base = env.get("LOCALAPPDATA") or str(Path.home())
    return str(Path(base) / "Vidnets Studio" / "render.lock")


def render_gate_wait_seconds(env: Optional[os._Environ] = None) -> float:
    env = env if env is not None else os.environ
    try:
        raw = float(str(env.get("VIDZEO_RENDER_GATE_WAIT_SECONDS") or "").strip())
    except (TypeError, ValueError):
        return DEFAULT_WAIT_SECONDS
    return raw if raw > 0 else DEFAULT_WAIT_SECONDS


def render_gate_enabled(env: Optional[os._Environ] = None) -> bool:
    env = env if env is not None else os.environ
    return str(env.get("VIDZEO_RENDER_GATE") or "").strip().lower() != "off"


def probe_port(port: int, timeout_seconds: float = PORT_PROBE_TIMEOUT_SECONDS) -> Optional[bool]:
    """True = something listens (render active), False = refused (idle),
    None = could not tell."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout_seconds)
        sock.connect(("127.0.0.1", port))
        return True
    except ConnectionRefusedError:
        return False
    except socket.timeout:
        # A localhost connect that times out is not "idle" — the machine
        # is thrashing or the listener backlog is full. Unknown, so the
        # other signal decides; never report a timeout as idle.
        return None
    except OSError:
        return False
    except Exception:
        return None
    finally:
        try:
            sock.close()
        except Exception:
            pass


def probe_lock_file(lock_file: str) -> Optional[bool]:
    """True = lock held (render active), False = free or no lock file,
    None = could not tell (no flock available, non-POSIX without msvcrt)."""
    path = Path(lock_file)
    if not path.exists():
        return False
    try:
        stream = path.open("rb")
    except OSError:
        return None
    try:
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            return False
        import fcntl

        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return None
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    except ImportError:
        return None
    finally:
        try:
            stream.close()
        except Exception:
            pass


def is_render_active(
    *,
    port: Optional[int] = None,
    lock_file: Optional[str] = None,
    env: Optional[os._Environ] = None,
) -> Dict[str, Any]:
    """Never raises: probe failures are reported in ``errors`` and the
    affected signal is treated as unknown (not active)."""
    env = env if env is not None else os.environ
    port = port if port is not None else render_lease_port(env)
    lock_file = lock_file if lock_file is not None else render_lock_file(env)
    errors = []
    try:
        port_active = probe_port(port)
    except Exception as exc:  # pragma: no cover - defensive
        errors.append(f"port probe: {exc}")
        port_active = None
    try:
        lock_active = probe_lock_file(lock_file)
    except Exception as exc:  # pragma: no cover - defensive
        errors.append(f"lock probe: {exc}")
        lock_active = None
    signals = []
    if port_active is True:
        signals.append(f"lease port {port} listening")
    if lock_active is True:
        signals.append(f"render lock {lock_file} held")
    return {
        "active": port_active is True or lock_active is True,
        "portActive": port_active,
        "lockActive": lock_active,
        "port": port,
        "lockFile": lock_file,
        "signals": signals,
        "errors": errors,
    }


def wait_for_render_idle(
    *,
    timeout_seconds: Optional[float] = None,
    poll_seconds: float = WAIT_POLL_SECONDS,
    on_wait: Optional[Callable[[Dict[str, Any], float], Any]] = None,
    port: Optional[int] = None,
    lock_file: Optional[str] = None,
    env: Optional[os._Environ] = None,
) -> Dict[str, Any]:
    """Wait until no render is active, or the bound expires. Never raises
    for probe failures; returns ``idle: False`` on timeout."""
    env = env if env is not None else os.environ
    timeout = timeout_seconds if timeout_seconds is not None else render_gate_wait_seconds(env)
    started = time.monotonic()
    status = is_render_active(port=port, lock_file=lock_file, env=env)
    while status["active"]:
        waited = time.monotonic() - started
        if waited >= timeout:
            return {"idle": False, "waitedSeconds": waited, "waitSeconds": timeout, "status": status}
        if on_wait is not None:
            try:
                on_wait(status, waited)
            except Exception:
                pass
        time.sleep(min(poll_seconds, max(0.0, timeout - waited)))
        status = is_render_active(port=port, lock_file=lock_file, env=env)
    return {"idle": True, "waitedSeconds": time.monotonic() - started, "waitSeconds": timeout, "status": status}


def _print_status_human(status: Dict[str, Any]) -> None:
    if status["active"]:
        print(
            "[RenderGate] render ACTIVE: "
            + "; ".join(status["signals"])
            + " — background work must yield or wait",
            flush=True,
        )
    else:
        print("[RenderGate] no render active", flush=True)
    for error in status["errors"]:
        print(f"[RenderGate] probe note: {error}", flush=True)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="render_gate.py")
    sub = parser.add_subparsers(dest="command")
    p_status = sub.add_parser("status")
    p_status.add_argument("--json", action="store_true")
    p_wait = sub.add_parser("wait")
    p_wait.add_argument("--json", action="store_true")
    p_wait.add_argument("--strict", action="store_true")
    p_wait.add_argument("--wait-seconds", type=float, default=None)
    args = parser.parse_args(argv)
    if args.command == "status":
        status = is_render_active()
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            _print_status_human(status)
        return 2 if status["active"] else 0
    if args.command == "wait":
        if not render_gate_enabled():
            print("[RenderGate] gate disabled (VIDZEO_RENDER_GATE=off); not waiting", flush=True)
            return 0
        wait_seconds = args.wait_seconds
        if wait_seconds is None or wait_seconds <= 0:
            wait_seconds = render_gate_wait_seconds()
        last_notice = 0.0
        started = time.monotonic()

        def _on_wait(status: Dict[str, Any], waited: float) -> None:
            nonlocal last_notice
            if time.monotonic() - last_notice >= 30.0:
                last_notice = time.monotonic()
                print(
                    "[RenderGate] render active ("
                    + "; ".join(status["signals"])
                    + f") — waiting {waited:.0f}s of {wait_seconds:.0f}s",
                    flush=True,
                )

        result = wait_for_render_idle(timeout_seconds=wait_seconds, on_wait=_on_wait)
        if args.json:
            print(json.dumps(result, indent=2))
        if result["idle"]:
            print(
                f"[RenderGate] render finished after {result['waitedSeconds']:.0f}s; machine is free",
                flush=True,
            )
            return 0
        print(
            "[RenderGate] WARNING: render still active after "
            f"{result['waitedSeconds']:.0f}s ({'; '.join(result['status']['signals'])})"
            " — proceeding anyway; the render will stay starved, re-check before heavy work",
            flush=True,
        )
        return 3 if args.strict else 0
    parser.print_usage(sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
