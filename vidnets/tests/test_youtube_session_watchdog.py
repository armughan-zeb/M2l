"""YouTube session watchdog tests: flip alerts, TTL gating, status wiring.

All fixtures use synthetic temp-dir cookie files with dummy values;
nothing here touches the operator's real cookies.json. Network
verification is mocked in every test.
"""
import http.cookiejar
import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services import youtube_session as session
from services import youtube_session_watchdog as watchdog


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    # Never touch the operator's real session state or cookies: redirect
    # the secure store + cookie dir, point the watchdog state at tmp,
    # and make both TTLs instant so a flip is observable in one test.
    monkeypatch.setenv("VIDZEO_SECURE_DATA_DIR", str(tmp_path / "secure"))
    monkeypatch.setenv("VIDZEO_YT_COOKIE_DIR", str(tmp_path / "cookies.d"))
    monkeypatch.setenv(
        "VIDZEO_YT_SESSION_WATCHDOG_STATE_FILE", str(tmp_path / "watchdog.json")
    )
    monkeypatch.setenv("VIDZEO_YT_POOL_VERIFY_TTL_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_YT_SESSION_WATCHDOG_INTERVAL_SECONDS", "0")
    session.reset_for_tests()
    yield
    session.reset_for_tests()


def _cookie(name="SAPISID", value="dummy-value", domain=".youtube.com"):
    return http.cookiejar.Cookie(
        version=0, name=name, value=value, port=None, port_specified=False,
        domain=domain, domain_specified=True, domain_initial_dot=True,
        path="/", path_specified=True, secure=True,
        expires=int(time.time()) + 3600, discard=False,
        comment=None, comment_url=None, rest={"HttpOnly": None}, rfc2109=False,
    )


def _write_json(path):
    payload = [
        {"name": "SAPISID", "value": "dummy", "domain": ".youtube.com",
         "path": "/", "secure": True, "expirationDate": int(time.time()) + 3600},
        {"name": "LOGIN_INFO", "value": "dummy", "domain": ".youtube.com",
         "path": "/", "secure": True, "expirationDate": int(time.time()) + 3600},
    ]
    Path(path).write_text(json.dumps(payload), encoding="utf-8")
    return path


def _pool(tmp_path):
    return session.ConfiguredYouTubeSessions(str(_write_json(tmp_path / "a.json")))


def _state(tmp_path):
    return json.loads((tmp_path / "watchdog.json").read_text(encoding="utf-8"))


class TestWatchdogFlip:
    def test_flip_to_signed_out_fires_alert_and_status(self, tmp_path, capsys):
        pool = _pool(tmp_path)
        state_path = tmp_path / "watchdog.json"
        with mock.patch.object(
            session, "_verify_youtube_session", return_value=(True, "youtube-confirmed")
        ):
            result = watchdog.check(pool=pool, state_path=state_path, render_active=False)
        assert result["checked"] is True
        assert result["alerts"] == []
        assert result["needs_reexport"] is False

        with mock.patch.object(
            session, "_verify_youtube_session",
            return_value=(False, "youtube-reported-signed-out"),
        ):
            result = watchdog.check(pool=pool, state_path=state_path, render_active=False)
        assert result["checked"] is True
        assert len(result["alerts"]) == 1
        alert = result["alerts"][0]
        assert alert["account"] == 1
        assert alert["status"] == "signed_out"
        assert result["needs_reexport"] is True
        assert "re-export" in result["message"]

        # (a) the log line names the account + reason, never a cookie value.
        out = capsys.readouterr().out
        assert "[yt-session-watchdog]" in out
        assert "account 1 signed_out" in out
        assert "dummy" not in out
        # (b) the needs-user signal is recorded in the state file.
        saved = _state(tmp_path)
        assert saved["needs_reexport"] is True
        assert saved["accounts"][0]["status"] == "signed_out"
        assert saved["alerts"][0]["account"] == 1
        assert "dummy" not in json.dumps(saved)

        # A repeat check on the still-dead account stays silent: one
        # alert per flip, not one per tick.
        with mock.patch.object(
            session, "_verify_youtube_session",
            return_value=(False, "youtube-reported-signed-out"),
        ):
            again = watchdog.check(pool=pool, state_path=state_path, render_active=False)
        assert again["alerts"] == []
        assert again["needs_reexport"] is True
        assert "account 1 signed_out" not in capsys.readouterr().out

    def test_healthy_account_produces_no_alert(self, tmp_path, capsys):
        pool = _pool(tmp_path)
        with mock.patch.object(
            session, "_verify_youtube_session", return_value=(True, "youtube-confirmed")
        ):
            result = watchdog.check(
                pool=pool, state_path=tmp_path / "watchdog.json", render_active=False
            )
        assert result["checked"] is True
        assert result["alerts"] == []
        assert result["needs_reexport"] is False
        assert result["healthy_accounts"] == 1
        assert "signed_out" not in capsys.readouterr().out

    def test_recovery_clears_alert(self, tmp_path, capsys):
        pool = _pool(tmp_path)
        state_path = tmp_path / "watchdog.json"
        with mock.patch.object(
            session, "_verify_youtube_session",
            return_value=(False, "youtube-reported-signed-out"),
        ):
            dead = watchdog.check(pool=pool, state_path=state_path, render_active=False)
        assert dead["needs_reexport"] is True
        with mock.patch.object(
            session, "_verify_youtube_session", return_value=(True, "youtube-confirmed")
        ):
            back = watchdog.check(pool=pool, state_path=state_path, render_active=False)
        assert back["alerts"] == []
        assert back["needs_reexport"] is False
        assert back["healthy_accounts"] == 1
        assert _state(tmp_path)["alerts"] == []
        assert "recovered" in capsys.readouterr().out

    def test_inconclusive_probe_does_not_clear_death_alert(self, tmp_path, capsys):
        pool = _pool(tmp_path)
        state_path = tmp_path / "watchdog.json"
        with mock.patch.object(
            session, "_verify_youtube_session",
            return_value=(False, "youtube-reported-signed-out"),
        ):
            dead = watchdog.check(pool=pool, state_path=state_path, render_active=False)
        assert dead["needs_reexport"] is True
        # A later probe that times out (inconclusive) is not a recovery:
        # the death alert and needs_reexport survive the network blip.
        with mock.patch.object(
            session, "_verify_youtube_session",
            return_value=(None, "youtube-probe-ReadTimeout"),
        ):
            blip = watchdog.check(pool=pool, state_path=state_path, render_active=False)
        assert blip["alerts"] == []  # no new flip logged
        assert blip["needs_reexport"] is True
        assert _state(tmp_path)["alerts"][0]["account"] == 1
        assert "recovered" not in capsys.readouterr().out

    def test_watchdog_does_not_reprobe_within_ttl(self, tmp_path, monkeypatch):
        # Watchdog interval 3600s: the second check must not touch the
        # pool at all, so the probe runs exactly once.
        monkeypatch.setenv("VIDZEO_YT_SESSION_WATCHDOG_INTERVAL_SECONDS", "3600")
        monkeypatch.setenv("VIDZEO_YT_POOL_VERIFY_TTL_SECONDS", "3600")
        pool = _pool(tmp_path)
        probe = mock.Mock(return_value=(True, "youtube-confirmed"))
        with mock.patch.object(session, "_verify_youtube_session", probe):
            first = watchdog.check(
                pool=pool, state_path=tmp_path / "watchdog.json", render_active=False
            )
            second = watchdog.check(
                pool=pool, state_path=tmp_path / "watchdog.json", render_active=False
            )
        assert first["checked"] is True
        assert second["checked"] is False
        assert second.get("skipped_ttl") is True
        assert probe.call_count == 1

    def test_render_active_skips_probe_without_moving_last_check(self, tmp_path):
        pool = _pool(tmp_path)
        probe = mock.Mock(return_value=(True, "youtube-confirmed"))
        with mock.patch.object(session, "_verify_youtube_session", probe):
            result = watchdog.check(
                pool=pool, state_path=tmp_path / "watchdog.json", render_active=True
            )
        assert result["checked"] is False
        assert result.get("skipped_render") is True
        assert probe.call_count == 0
        # No state file written on a skipped check: last_checked stays 0.
        assert not (tmp_path / "watchdog.json").exists()


class TestStatusWiring:
    def test_api_status_youtube_session_reflects_pool_state(
        self, tmp_path, monkeypatch
    ):
        """Function-level: session_status() + attach_to_status().

        A full Flask app boot is too heavy here, so this tests the two
        functions /api/status actually calls: the pool state read and
        the watchdog merge in the status function.
        """
        cookie_file = _write_json(tmp_path / "a.json")
        monkeypatch.setenv("VIDZEO_YT_COOKIE_FILES", str(cookie_file))
        monkeypatch.setenv("VIDZEO_YT_COOKIE_DIR", str(tmp_path / "cookies.d"))
        session.reset_configured_pool()
        with mock.patch.object(
            session, "_verify_youtube_session",
            return_value=(False, "youtube-reported-signed-out"),
        ):
            # Drive a real probe through the pool the status fn reads.
            pool = session.get_configured_pool()
            assert pool.next_healthy() == (-1, None)
            status = session.session_status()
            # Keep the mock live for this call too: without it the
            # watchdog would do a real network probe here.
            watchdog.check(
                pool=pool, state_path=tmp_path / "watchdog.json",
                render_active=False, force=True,
            )
        assert status["pool"][0]["status"] == "signed_out"
        assert status["pool_healthy_accounts"] == 0
        assert "re-export" in status["pool_message"]

        # The watchdog state the status function merges in agrees.
        merged = watchdog.attach_to_status({
            "ready": status["ready"],
            "verified_authenticated": status["verified_authenticated"],
            "pool": status["pool"],
            "pool_healthy_accounts": status["pool_healthy_accounts"],
            "pool_message": status["pool_message"],
        })
        assert merged["watchdog"]["needs_reexport"] is True
        assert merged["watchdog"]["accounts"][0]["status"] == "signed_out"
        assert "re-export" in merged["pool_message"]

    def test_attach_fills_empty_pool_message_from_watchdog(self, tmp_path):
        # The stale-field case: the pool was never probed by a job, so
        # session_status() says nothing, but the watchdog already knows.
        pool = _pool(tmp_path)
        with mock.patch.object(
            session, "_verify_youtube_session",
            return_value=(False, "youtube-reported-signed-out"),
        ):
            watchdog.check(
                pool=pool, state_path=tmp_path / "watchdog.json",
                render_active=False, force=True,
            )
        merged = watchdog.attach_to_status({
            "ready": False, "verified_authenticated": False,
            "pool": [], "pool_healthy_accounts": 0, "pool_message": "",
        })
        assert merged["watchdog"]["needs_reexport"] is True
        assert "re-export" in merged["pool_message"]

    def test_attach_is_safe_with_no_state_file(self, tmp_path):
        merged = watchdog.attach_to_status({
            "ready": False, "verified_authenticated": False,
            "pool": [], "pool_healthy_accounts": 0, "pool_message": "",
        })
        assert merged["watchdog"]["needs_reexport"] is False
        assert merged["watchdog"]["checked_at"] == 0
        assert merged["pool_message"] == ""
