"""Area B pool tests: multi-account cookies, health, rotation, graceful fallback.

All fixtures use synthetic cookies with dummy values; nothing here
touches the operator's real cookie files. Network verification is
mocked except for the explicit stale-file behaviour (verify=False).
"""
import http.cookiejar
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services import youtube_session as session


@pytest.fixture(autouse=True)
def _reset_session(tmp_path, monkeypatch):
    # Never let pool tests touch the operator's real session state or
    # encrypted snapshot: redirect the secure store and reset globals.
    monkeypatch.setenv("VIDZEO_SECURE_DATA_DIR", str(tmp_path / "secure"))
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


def _write_netscape(path, names=("SAPISID", "LOGIN_INFO")):
    jar = http.cookiejar.MozillaCookieJar(str(path))
    for name in names:
        jar.set_cookie(_cookie(name))
    jar.save(ignore_discard=True, ignore_expires=True)
    return path


def _write_json(path):
    import json

    payload = [
        {"name": "SAPISID", "value": "dummy", "domain": ".youtube.com",
         "path": "/", "secure": True, "expirationDate": int(time.time()) + 3600},
        {"name": "LOGIN_INFO", "value": "dummy", "domain": ".youtube.com",
         "path": "/", "secure": True, "expirationDate": int(time.time()) + 3600},
    ]
    Path(path).write_text(json.dumps(payload), encoding="utf-8")
    return path


def _verify_true(jar, explicit_sign_in_only=False):
    return True, "youtube-confirmed"


def _verify_false(jar, explicit_sign_in_only=False):
    return False, "youtube-reported-signed-out"


def _verify_none(jar, explicit_sign_in_only=False):
    return None, "youtube-probe-inconclusive"


def _pool_env(tmp_dir):
    return mock.patch.dict(
        os.environ,
        {"VIDZEO_YT_COOKIE_FILES": "", "VIDZEO_YT_COOKIE_DIR": ""},
        clear=False,
    )


def test_pool_parses_list_and_dedupes(tmp_path):
    first = _write_netscape(tmp_path / "a.txt")
    second = _write_json(tmp_path / "b.json")
    setting = os.pathsep.join([str(first), str(second), str(first)])
    with _pool_env(tmp_path):
        pool = session.ConfiguredYouTubeSessions(setting)
    assert [str(p) for p in pool.paths] == [str(first), str(second)]


def test_pool_discovers_cookie_directory(tmp_path):
    cookie_dir = tmp_path / "cookies.d"
    cookie_dir.mkdir()
    _write_netscape(cookie_dir / "acct1.txt")
    _write_json(cookie_dir / "acct2.json")
    (cookie_dir / "notes.md").write_text("not a cookie file", encoding="utf-8")
    with mock.patch.dict(
        os.environ,
        {"VIDZEO_YT_COOKIE_FILES": "", "VIDZEO_YT_COOKIE_DIR": str(cookie_dir)},
        clear=False,
    ):
        pool = session.ConfiguredYouTubeSessions("")
    assert [p.name for p in pool.paths] == ["acct1.txt", "acct2.json"]


def test_pool_skips_bad_file_and_uses_good_one(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("this is not a cookie file", encoding="utf-8")
    good = _write_netscape(tmp_path / "good.txt")
    with _pool_env(tmp_path), mock.patch.object(
        session, "_verify_youtube_session", side_effect=_verify_true
    ):
        pool = session.ConfiguredYouTubeSessions(os.pathsep.join([str(bad), str(good)]))
        index, jar = pool.next_verified()
    assert index == 1
    assert jar is not None
    assert sum(1 for _ in jar) == 2
    snapshot = {row["file"]: row["status"] for row in pool.health_snapshot()}
    assert snapshot["bad.txt"] == "load_error"
    assert snapshot["good.txt"] == "verified"


def test_pool_marks_stale_account_signed_out_and_falls_back(tmp_path):
    stale = _write_netscape(tmp_path / "stale.txt")
    with _pool_env(tmp_path), mock.patch.object(
        session, "_verify_youtube_session", side_effect=_verify_false
    ) as verify:
        pool = session.ConfiguredYouTubeSessions(str(stale))
        index, jar = pool.next_verified()
        assert (index, jar) == (-1, None)
        assert pool.health_snapshot()[0]["status"] == "signed_out"
        assert pool.health_snapshot()[0]["auth_cookie_count"] == 2
        # Second call does not re-probe a signed-out export every time.
        index, jar = pool.next_healthy()
        assert (index, jar) == (-1, None)
        assert verify.call_count == 1


def test_pool_rotates_across_healthy_accounts(tmp_path):
    first = _write_netscape(tmp_path / "a.txt")
    second = _write_netscape(tmp_path / "b.txt")
    with _pool_env(tmp_path), mock.patch.object(
        session, "_verify_youtube_session", side_effect=_verify_true
    ):
        pool = session.ConfiguredYouTubeSessions(os.pathsep.join([str(first), str(second)]))
        seen = [pool.next_healthy()[0] for _ in range(4)]
    assert seen == [0, 1, 0, 1]


def test_pool_skips_throttled_account_until_cooldown(tmp_path):
    first = _write_netscape(tmp_path / "a.txt")
    second = _write_netscape(tmp_path / "b.txt")
    with _pool_env(tmp_path), mock.patch.object(
        session, "_verify_youtube_session", side_effect=_verify_true
    ), mock.patch.dict(
        os.environ, {"VIDZEO_YT_POOL_THROTTLE_COOLDOWN_SECONDS": "3600"}, clear=False
    ):
        pool = session.ConfiguredYouTubeSessions(os.pathsep.join([str(first), str(second)]))
        assert pool.next_healthy()[0] == 0
        pool.mark_throttled(0, reason="youtube-throttled")
        assert pool.next_healthy()[0] == 1
        assert pool.next_healthy()[0] == 1  # still skipped while cooling down
        assert pool.health_snapshot()[0]["status"] == "throttled"


def test_pool_zero_healthy_accounts_returns_none(tmp_path):
    only = _write_netscape(tmp_path / "only.txt")
    with _pool_env(tmp_path), mock.patch.object(
        session, "_verify_youtube_session", side_effect=_verify_none
    ):
        pool = session.ConfiguredYouTubeSessions(str(only))
        assert pool.next_healthy() == (-1, None)
        assert pool.health_snapshot()[0]["status"] == "unverified"


def test_pool_health_snapshot_is_redacted(tmp_path):
    good = _write_netscape(tmp_path / "good.txt")
    with _pool_env(tmp_path), mock.patch.object(
        session, "_verify_youtube_session", side_effect=_verify_true
    ):
        pool = session.ConfiguredYouTubeSessions(str(good))
        pool.next_verified()
        import json

        assert "dummy-value" not in json.dumps(pool.health_snapshot())


def test_shared_pool_rebuilds_when_files_change(tmp_path):
    cookie_dir = tmp_path / "cookies.d"
    cookie_dir.mkdir()
    _write_netscape(cookie_dir / "a.txt")
    env = {
        "VIDZEO_YT_COOKIE_FILES": "",
        "VIDZEO_YT_COOKIE_DIR": str(cookie_dir),
    }
    try:
        with mock.patch.dict(os.environ, env, clear=False):
            session.reset_configured_pool()
            first = session.get_configured_pool()
            assert len(first.paths) == 1
            _write_netscape(cookie_dir / "b.txt")
            second = session.get_configured_pool()
            assert second is not first
            assert len(second.paths) == 2
    finally:
        session.reset_configured_pool()


def test_ensure_session_tries_configured_pool_files_first(tmp_path):
    cookie_file = _write_netscape(tmp_path / "pool-acct.txt")
    with tempfile.TemporaryDirectory() as secure_dir, mock.patch.dict(
        os.environ,
        {
            "VIDZEO_SECURE_DATA_DIR": secure_dir,
            "VIDZEO_YT_COOKIE_FILES": str(cookie_file),
            "VIDZEO_YT_COOKIES_FILE": "",
        },
        clear=False,
    ), mock.patch.object(
        session, "_verify_youtube_session", side_effect=_verify_true
    ), mock.patch.object(
        session, "_protect", side_effect=lambda value: b"dpapi:" + value
    ), mock.patch.object(
        session, "_write_netscape_cookies", return_value=[]
    ):
        jar, _generation = session.ensure_session()
    assert session._jar_is_authenticated(jar)


def test_write_netscape_never_downgrades_existing_file(tmp_path):
    big = http.cookiejar.CookieJar()
    for name in ("SAPISID", "LOGIN_INFO", "SID", "HSID"):
        big.set_cookie(_cookie(name))
    target = tmp_path / "cookies.txt"
    with mock.patch.dict(
        os.environ, {"VIDZEO_YT_COOKIES_FILE": str(target)}, clear=False
    ), mock.patch.object(
        session, "_write_netscape_cookies", wraps=session._write_netscape_cookies
    ):
        session._install_memory_session(big, {"browser": "chrome"})
    assert target.is_file()
    before = target.read_text(encoding="utf-8")
    small = http.cookiejar.CookieJar()
    small.set_cookie(_cookie("SAPISID"))
    small.set_cookie(_cookie("PREF", value="pref"))
    with mock.patch.dict(
        os.environ, {"VIDZEO_YT_COOKIES_FILE": str(target)}, clear=False
    ):
        session._install_memory_session(small, {"browser": "chrome"})
    # The 2-cookie test-style jar must not overwrite the 4-cookie file.
    assert target.read_text(encoding="utf-8") == before
