"""Internet resilience and automatic reconnection utility for Vidnets.

When network dropouts or DNS issues occur, this module pauses requests,
waits for connectivity to be restored (probing DNS/socket signals),
and resumes execution immediately without manual intervention.
"""
from __future__ import annotations

import socket
import time
import logging
from typing import Callable, Any, Optional

logger = logging.getLogger("vidnets.internet_resilience")

NETWORK_ERROR_PATTERNS = (
    "connection refused",
    "connection reset",
    "network unreachable",
    "network is down",
    "network error",
    "fetch failed",
    "getaddrinfo",
    "socket hang up",
    "internet disconnected",
    "name or service not known",
    "temporary failure in name resolution",
    "failed to establish a new connection",
    "max retries exceeded with url",
    "timed out",
    "connect timeout",
    "econnrefused",
    "econnreset",
    "enotfound",
    "eai_again",
    "enetunreach",
    "ehostunreach",
    "enetdown",
    "ehostdown",
)


def is_network_error(exc: Any) -> bool:
    """Check if an exception is caused by a network outage, socket drop, or DNS error."""
    if exc is None:
        return False
    if isinstance(exc, (socket.gaierror, socket.timeout, ConnectionError, BrokenPipeError)):
        return True
    try:
        import requests
        if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return True
    except Exception:
        pass
    try:
        import urllib3
        if isinstance(exc, (urllib3.exceptions.ProtocolError, urllib3.exceptions.MaxRetryError)):
            return True
    except Exception:
        pass

    msg = str(exc).lower()
    return any(pattern in msg for pattern in NETWORK_ERROR_PATTERNS)


def is_internet_reachable(timeout: float = 2.0) -> bool:
    """Probe global reliable DNS servers to determine whether internet is reachable."""
    probe_hosts = [
        ("1.1.1.1", 53),
        ("8.8.8.8", 53),
        ("8.8.4.4", 53),
        ("1.0.0.1", 53),
    ]
    for host, port in probe_hosts:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((host, port))
            sock.close()
            return True
        except Exception:
            try:
                sock.close()
            except Exception:
                pass
    return False


def wait_for_internet_connectivity(
    stop_event: Optional[Any] = None,
    poll_interval: float = 3.0,
    max_wait_seconds: float = 86400.0,
    on_waiting: Optional[Callable[[str], None]] = None,
    on_restored: Optional[Callable[[str], None]] = None,
) -> bool:
    """Wait for network connectivity to return.

    Loops until is_internet_reachable returns True or stop_event is set.
    """
    if is_internet_reachable(timeout=1.5):
        return True

    msg = "[net] Internet connection lost. Waiting for network connectivity to restore before continuing..."
    print(msg, flush=True)
    if on_waiting:
        try:
            on_waiting(msg)
        except Exception:
            pass

    started = time.monotonic()
    while time.monotonic() - started < max_wait_seconds:
        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            return False
        time.sleep(poll_interval)
        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            return False

        if is_internet_reachable(timeout=2.0):
            restored_msg = "[net] Internet connection restored. Resuming immediately..."
            print(restored_msg, flush=True)
            if on_restored:
                try:
                    on_restored(restored_msg)
                except Exception:
                    pass
            return True

    return False


def with_internet_resilience(
    fn: Callable[[], Any],
    max_retries: int = 3,
    stop_event: Optional[Any] = None,
) -> Any:
    """Execute a network callable, automatically waiting for internet if disconnected."""
    attempts = 0
    while True:
        attempts += 1
        try:
            return fn()
        except BaseException as exc:
            if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                raise
            if is_network_error(exc) or not is_internet_reachable(timeout=1.0):
                wait_for_internet_connectivity(stop_event=stop_event)
                # Once internet is restored, retry the network operation
                continue
            if attempts >= max_retries:
                raise
            raise
