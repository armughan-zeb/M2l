"""Regression tests for the render gate (Area 3: starvation bugs).

The gate probes the same two signals the render itself holds — the TCP
lease port (47639) and the server render lock file — so these tests hold
each signal on an ephemeral port / tmp file and assert the gate sees it.
Nothing here touches the real lease port or the real lock file.
"""

from __future__ import annotations

import fcntl
import importlib.util
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_VIDNETS = Path(__file__).resolve().parents[1]
GATE_PATH = REPO_VIDNETS / "server" / "services" / "render_gate.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("render_gate_under_test", GATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def _ephemeral_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    # Backlog 16: gate probes connect and close without accept()ing, and
    # each un-accepted connection occupies a backlog slot — backlog 1
    # makes the second probe time out and look idle.
    sock.listen(16)
    return sock, sock.getsockname()[1]


def test_port_probe_idle_and_busy(tmp_path):
    lock_file = tmp_path / "render.lock"
    listener, port = _ephemeral_listener()
    try:
        assert gate.probe_port(port) is True
        status = gate.is_render_active(port=port, lock_file=str(lock_file))
        assert status["active"] is True
        assert status["portActive"] is True
        assert status["lockActive"] is False
        assert any(f"lease port {port}" in s for s in status["signals"])
    finally:
        listener.close()
    assert gate.probe_port(port) is False
    status = gate.is_render_active(port=port, lock_file=str(lock_file))
    assert status["active"] is False


def test_lock_probe_free_held_and_missing(tmp_path):
    lock_file = tmp_path / "render.lock"
    # Missing file: no server render, never an error.
    assert gate.probe_lock_file(str(lock_file)) is False
    lock_file.write_bytes(b"0")
    assert gate.probe_lock_file(str(lock_file)) is False
    with lock_file.open("rb") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = gate.is_render_active(port=59999, lock_file=str(lock_file))
        assert status["active"] is True
        assert status["lockActive"] is True
        assert status["portActive"] is False
        assert any("render lock" in s and "held" in s for s in status["signals"])
    assert gate.probe_lock_file(str(lock_file)) is False


def test_wait_for_render_idle_returns_at_once_when_idle(tmp_path):
    listener, port = _ephemeral_listener()
    listener.close()
    result = gate.wait_for_render_idle(
        timeout_seconds=5.0, poll_seconds=0.05,
        port=port, lock_file=str(tmp_path / "render.lock"),
    )
    assert result["idle"] is True
    assert result["waitedSeconds"] < 5.0


def test_wait_for_render_idle_times_out_while_busy(tmp_path):
    listener, port = _ephemeral_listener()
    try:
        started = time.monotonic()
        result = gate.wait_for_render_idle(
            timeout_seconds=0.3, poll_seconds=0.05,
            port=port, lock_file=str(tmp_path / "render.lock"),
        )
        assert result["idle"] is False
        assert time.monotonic() - started >= 0.25
        assert result["status"]["active"] is True
    finally:
        listener.close()


def test_env_parsing_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("VIDZEO_RENDER_LEASE_PORT", "not-a-port")
    assert gate.render_lease_port() == gate.DEFAULT_LEASE_PORT
    monkeypatch.setenv("VIDZEO_RENDER_LEASE_PORT", "70000")
    assert gate.render_lease_port() == gate.DEFAULT_LEASE_PORT
    monkeypatch.setenv("VIDZEO_RENDER_LEASE_PORT", "47639")
    assert gate.render_lease_port() == 47639
    monkeypatch.setenv("VIDZEO_RENDER_GATE_WAIT_SECONDS", "60")
    assert gate.render_gate_wait_seconds() == 60.0
    monkeypatch.setenv("VIDZEO_RENDER_GATE_WAIT_SECONDS", "junk")
    assert gate.render_gate_wait_seconds() == gate.DEFAULT_WAIT_SECONDS
    monkeypatch.setenv("VIDNETS_RENDER_LOCK_FILE", "/tmp/somewhere/render.lock")
    assert gate.render_lock_file() == "/tmp/somewhere/render.lock"


def test_is_render_active_never_raises_on_unprovable_signals(tmp_path):
    # Lock file in a directory that does not exist, port closed: idle,
    # no exception — a broken probe must fail open, never hang a caller.
    status = gate.is_render_active(
        port=59999, lock_file=str(tmp_path / "no-such-dir" / "render.lock")
    )
    assert status["active"] is False


def test_pytest_sessionstart_waits_for_render(tmp_path):
    """Replay of the Berlin Wall failure: a test run starting while a
    render holds the lease must wait for the render instead of piling
    heavy work on top of it. Holds the lease port, runs a one-test
    pytest collection (which triggers vidnets/conftest.py
    pytest_sessionstart), and asserts the run waited out the gate
    bound, printed the wait line, and still exited 0."""
    listener, port = _ephemeral_listener()
    try:
        env = dict(os.environ)
        env["VIDZEO_RENDER_LEASE_PORT"] = str(port)
        env["VIDNETS_RENDER_LOCK_FILE"] = str(tmp_path / "render.lock")
        env["VIDZEO_RENDER_GATE_WAIT_SECONDS"] = "3"
        env.pop("VIDZEO_RENDER_GATE", None)
        started = time.monotonic()
        proc = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                "tests/test_render_gate.py::test_port_probe_idle_and_busy",
                "-q", "-p", "no:cacheprovider", "--collect-only",
            ],
            cwd=str(REPO_VIDNETS),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        elapsed = time.monotonic() - started
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert elapsed >= 2.5, f"sessionstart did not wait (elapsed {elapsed:.1f}s)"
        assert "waiting up to 3s" in proc.stdout, proc.stdout
        assert "WARNING: render still active" in proc.stdout, proc.stdout
    finally:
        listener.close()
