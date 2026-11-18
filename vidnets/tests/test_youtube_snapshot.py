"""Encrypted YouTube session snapshot: round-trip, perms, fail-closed.

The snapshot at ``<secure dir>/youtube-session.dpapi`` is the durable
store for the live cookie jar (see ``refresh_encrypted_snapshot`` in
``services/youtube_session.py``).  All cookie values here are synthetic
fakes; tests point the snapshot at a temp dir via
``VIDZEO_SECURE_DATA_DIR`` and never touch the real secure directory
or the real ``cookies.json``.
"""

import http.cookiejar
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import youtube_session as session


def _cookie(name, value, domain=".youtube.com"):
    return http.cookiejar.Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=True,
        path="/",
        path_specified=True,
        secure=True,
        expires=int(time.time()) + 3600,
        discard=False,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": None},
        rfc2109=False,
    )


def _synthetic_jar():
    jar = http.cookiejar.CookieJar()
    jar.set_cookie(_cookie("SID", "fake-sid-value"))
    jar.set_cookie(_cookie("SAPISID", "fake-sapisid-value"))
    jar.set_cookie(_cookie("__SECURE-3PSID", "fake-3psid-value"))
    return jar


def _jar_map(jar):
    return {cookie.name: cookie.value for cookie in jar}


class YouTubeSnapshotTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(
            os.environ,
            {"VIDZEO_SECURE_DATA_DIR": self._tmp.name},
            clear=False,
        )
        self._env.start()
        self.addCleanup(self._tmp.cleanup)
        session.reset_for_tests()

    def tearDown(self):
        session.reset_for_tests()
        self._env.stop()

    @property
    def _snapshot_path(self):
        return Path(self._tmp.name) / "youtube-session.dpapi"

    def test_round_trip_same_names_and_values(self):
        jar = _synthetic_jar()
        self.assertTrue(session.refresh_encrypted_snapshot(jar))
        loaded, meta = session._load_persisted()
        self.assertIsNotNone(loaded)
        self.assertEqual(_jar_map(loaded), _jar_map(jar))
        self.assertEqual(meta.get("source"), "cookie-write-back")
        self.assertTrue(meta.get("persisted"))

    def test_missing_snapshot_reads_empty_without_crash(self):
        self.assertFalse(self._snapshot_path.is_file())
        loaded, meta = session._load_persisted()
        self.assertIsNone(loaded)
        self.assertEqual(meta, {})

    def test_snapshot_file_600_and_directory_0700(self):
        self.assertTrue(session.refresh_encrypted_snapshot(_synthetic_jar()))
        file_mode = stat.S_IMODE(self._snapshot_path.stat().st_mode)
        dir_mode = stat.S_IMODE(self._snapshot_path.parent.stat().st_mode)
        self.assertEqual(file_mode, 0o600)
        self.assertEqual(dir_mode, 0o700)

    def test_corrupted_snapshot_fails_closed(self):
        self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        for garbage in (
            b"not json at all",
            json.dumps({"version": 1, "data": "!!!not-base64!!!"}).encode(),
            json.dumps({"version": 1, "data": "aGVsbG8="}).encode(),
        ):
            with self.subTest(garbage=garbage[:20]):
                self._snapshot_path.write_bytes(garbage)
                loaded, meta = session._load_persisted()
                self.assertIsNone(loaded)
                self.assertEqual(meta, {})

    def test_refresh_refuses_empty_or_unauthenticated_jar(self):
        empty = http.cookiejar.CookieJar()
        self.assertFalse(session.refresh_encrypted_snapshot(empty))
        self.assertFalse(self._snapshot_path.is_file())
        no_auth = http.cookiejar.CookieJar()
        no_auth.set_cookie(_cookie("PREF", "fake-pref-value"))
        self.assertFalse(session.refresh_encrypted_snapshot(no_auth))
        self.assertFalse(self._snapshot_path.is_file())

    def test_refresh_does_not_overwrite_good_snapshot_with_junk(self):
        good = _synthetic_jar()
        self.assertTrue(session.refresh_encrypted_snapshot(good))
        self.assertFalse(session.refresh_encrypted_snapshot(http.cookiejar.CookieJar()))
        loaded, _meta = session._load_persisted()
        self.assertEqual(_jar_map(loaded), _jar_map(good))

    def test_refresh_creates_missing_directory(self):
        nested = Path(self._tmp.name) / "deep" / "secure"
        with mock.patch.dict(os.environ, {"VIDZEO_SECURE_DATA_DIR": str(nested)}, clear=False):
            self.assertTrue(session.refresh_encrypted_snapshot(_synthetic_jar()))
            target = nested / "youtube-session.dpapi"
            self.assertTrue(target.is_file())
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(nested.stat().st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()
