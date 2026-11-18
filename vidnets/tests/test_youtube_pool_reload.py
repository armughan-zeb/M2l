"""Pool notices a cookie file overwritten in place (same path, new content).

The pool rebuild key is the file LIST only, so without content
signatures a fresh cookies.json dropped over the old one could sit
behind a stale signed_out verdict until the verify TTL (1800s)
expired. The pool tracks per-file (mtime_ns, size) signatures: a
changed file resets that account to unknown so the next call
re-probes it, with no restart and no extra probes for unchanged
files. All cookie values here are synthetic fakes in temp dirs --
never the real operator cookies.json.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import youtube_session as session


def _fake_cookie_json(tag):
    """Synthetic Cookie-Editor export: auth cookies, live expiry, fake values."""
    expires = time.time() + 3600
    return json.dumps([
        {"name": "SAPISID", "value": f"fake-sapisid-{tag}", "domain": ".youtube.com",
         "path": "/", "secure": True, "httpOnly": True, "expirationDate": expires},
        {"name": "SID", "value": f"fake-sid-{tag}", "domain": ".youtube.com",
         "path": "/", "secure": True, "httpOnly": True, "expirationDate": expires},
        {"name": "PREF", "value": f"fake-pref-{tag}", "domain": ".youtube.com",
         "path": "/", "secure": False, "expirationDate": expires},
    ])


def _overwrite_in_place(path, tag):
    """Replace a cookie file's content at the same path, forcing a new signature."""
    path.write_text(_fake_cookie_json(tag), encoding="utf-8")
    # Distinct mtime even if the new payload happened to be the same size.
    new_ns = time.time_ns() + 1_000_000_000
    os.utime(path, ns=(new_ns, new_ns))


class PoolReloadTests(unittest.TestCase):
    def setUp(self):
        session.reset_for_tests()
        session.reset_configured_pool()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.cookie_dir = self.tmp / "cookies.d"
        self.cookie_dir.mkdir()
        self.env = {
            "VIDZEO_YT_COOKIE_DIR": str(self.cookie_dir),
            "VIDZEO_YT_COOKIE_FILES": "",
        }

    def tearDown(self):
        session.reset_for_tests()
        session.reset_configured_pool()

    def _make_file(self, name, tag):
        path = self.tmp / name
        path.write_text(_fake_cookie_json(tag), encoding="utf-8")
        return path

    def _env_for(self, *paths):
        env = dict(self.env)
        env["VIDZEO_YT_COOKIE_FILES"] = os.pathsep.join(str(p) for p in paths)
        return env

    def test_overwritten_file_is_reverified_via_get_configured_pool(self):
        cookie = self._make_file("a.json", "v1")
        with mock.patch.dict(os.environ, self._env_for(cookie), clear=False), \
                mock.patch.object(
                    session, "_verify_youtube_session",
                    return_value=(False, "youtube-reported-signed-out"),
                ) as probe:
            pool = session.get_configured_pool()
            self.assertEqual(pool.next_healthy(), (-1, None))
            self.assertEqual(probe.call_count, 1)
            self.assertEqual(pool.health_snapshot()[0]["status"], "signed_out")

            _overwrite_in_place(cookie, "v2-fresh-export")
            same_pool = session.get_configured_pool()
            self.assertIs(same_pool, pool)
            # The stale signed_out verdict is gone before any new probe.
            self.assertEqual(pool.health_snapshot()[0]["status"], "unknown")
            self.assertEqual(pool.next_healthy(), (-1, None))
            self.assertEqual(probe.call_count, 2)

    def test_unchanged_file_is_not_reprobed(self):
        cookie = self._make_file("a.json", "v1")
        with mock.patch.dict(os.environ, self._env_for(cookie), clear=False), \
                mock.patch.object(
                    session, "_verify_youtube_session",
                    return_value=(False, "youtube-reported-signed-out"),
                ) as probe:
            pool = session.get_configured_pool()
            self.assertEqual(pool.next_healthy(), (-1, None))
            self.assertEqual(probe.call_count, 1)
            # Repeated pool fetches + refreshes must not re-probe an unchanged file.
            session.get_configured_pool()
            pool.refresh()
            self.assertEqual(pool.next_healthy(), (-1, None))
            self.assertEqual(probe.call_count, 1)
            self.assertEqual(pool.health_snapshot()[0]["status"], "signed_out")

    def test_changed_file_flips_to_verified(self):
        cookie = self._make_file("a.json", "v1")
        with mock.patch.dict(os.environ, self._env_for(cookie), clear=False), \
                mock.patch.object(
                    session, "_verify_youtube_session",
                    return_value=(False, "youtube-reported-signed-out"),
                ) as probe:
            pool = session.get_configured_pool()
            self.assertEqual(pool.next_healthy(), (-1, None))
            self.assertEqual(probe.call_count, 1)

            _overwrite_in_place(cookie, "v2-fresh-export")
            session.get_configured_pool()
            probe.return_value = (True, "youtube-confirmed")
            index, jar = pool.next_healthy()
            self.assertEqual(index, 0)
            self.assertIsNotNone(jar)
            self.assertEqual(pool.health_snapshot()[0]["status"], "verified")
            self.assertEqual(probe.call_count, 2)

    def test_refresh_directly_detects_in_place_overwrite(self):
        cookie = self._make_file("a.json", "v1")
        with mock.patch.dict(os.environ, self.env, clear=False), \
                mock.patch.object(
                    session, "_verify_youtube_session",
                    return_value=(False, "youtube-reported-signed-out"),
                ) as probe:
            pool = session.ConfiguredYouTubeSessions(str(cookie))
            self.assertEqual(pool.next_healthy(), (-1, None))
            self.assertEqual(probe.call_count, 1)
            _overwrite_in_place(cookie, "v2-fresh-export")
            pool.refresh()
            self.assertEqual(pool.health_snapshot()[0]["status"], "unknown")
            self.assertEqual(pool.next_healthy(), (-1, None))
            self.assertEqual(probe.call_count, 2)

    def test_rotation_and_throttled_cooldown_survive_file_change(self):
        first = self._make_file("a.json", "v1")
        second = self._make_file("b.json", "v1")
        with mock.patch.dict(os.environ, self._env_for(first, second), clear=False), \
                mock.patch.object(
                    session, "_verify_youtube_session",
                    return_value=(True, "youtube-confirmed"),
                ) as probe:
            pool = session.get_configured_pool()
            # Rotation: successive calls alternate verified accounts.
            self.assertEqual(pool.next_healthy()[0], 0)
            self.assertEqual(pool.next_healthy()[0], 1)
            self.assertEqual(pool.next_healthy()[0], 0)
            self.assertEqual(probe.call_count, 2)

            # Throttled account 0 is skipped; rotation serves account 1.
            pool.mark_throttled(0, "test-throttle")
            self.assertEqual(pool.next_healthy()[0], 1)
            self.assertEqual(probe.call_count, 2)

            # Overwrite throttled account 0 in place: still skipped until cooldown.
            _overwrite_in_place(first, "v2-fresh-export")
            session.get_configured_pool()
            self.assertEqual(pool.health_snapshot()[0]["status"], "throttled")
            self.assertEqual(pool.next_healthy()[0], 1)
            self.assertEqual(probe.call_count, 2)

            # Once the cooldown expires, the fresh content is probed, not the old verdict.
            pool._health[0]["last_checked"] = 0.0
            self.assertEqual(pool.next_healthy()[0], 0)
            self.assertEqual(probe.call_count, 3)
            self.assertEqual(pool.health_snapshot()[0]["status"], "verified")


if __name__ == "__main__":
    unittest.main()
