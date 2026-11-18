"""Hardcoded operator cookies: loaded with zero config, attached best-effort.

The operator shares cookies at <repo>/cookies.json. The pipeline must
use them even when no VIDZEO_YT_COOKIE_FILES env var is set, and must
attach the jar to YouTube search/downloads even when YouTube's probe
reports the account signed_out (a signed-out jar still carries the
operator's identity and is strictly better than fully anonymous
access). Tests that need the real shared file skip when it is absent,
so they never fail on a machine without it.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import youtube_session as session

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_COOKIES = REPO_ROOT / "cookies.json"


class HardcodedCookieTests(unittest.TestCase):
    def setUp(self):
        session.reset_for_tests()
        session.reset_configured_pool()

    def tearDown(self):
        session.reset_for_tests()
        session.reset_configured_pool()

    def test_hardcoded_default_loads_repo_cookies_json_without_env(self):
        if not SHARED_COOKIES.is_file():
            self.skipTest("operator-shared cookies.json not present in this tree")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VIDZEO_YT_COOKIE_FILES", None)
            os.environ.pop("VIDZEO_YT_COOKIE_DIR", None)
            pool = session.ConfiguredYouTubeSessions("")
        self.assertIn(str(SHARED_COOKIES), {str(p) for p in pool.paths})

    def test_next_attachable_returns_jar_for_signed_out_account(self):
        if not SHARED_COOKIES.is_file():
            self.skipTest("operator-shared cookies.json not present in this tree")
        pool = session.ConfiguredYouTubeSessions(str(SHARED_COOKIES))
        # The probe reports signed_out (mocked: no network in this test).
        with mock.patch.object(
            session, "_verify_youtube_session",
            return_value=(False, "youtube-reported-signed-out"),
        ):
            # The old path: no verified account, so the pipeline went anonymous.
            self.assertEqual(pool.next_healthy(), (-1, None))
            # The new path: the loadable jar is attached best-effort.
            index, jar = pool.next_attachable()
        self.assertGreaterEqual(index, 0)
        self.assertIsNotNone(jar)
        self.assertGreaterEqual(sum(1 for _ in jar), 1)

    def test_next_attachable_skips_throttled_and_unreadable(self):
        if not SHARED_COOKIES.is_file():
            self.skipTest("operator-shared cookies.json not present in this tree")
        with tempfile.TemporaryDirectory() as tmp:
            garbage = Path(tmp) / "bad.txt"
            garbage.write_text("this is not a cookie file\n")
            pool = session.ConfiguredYouTubeSessions(
                f"{SHARED_COOKIES}{os.pathsep}{garbage}"
            )
            # Mark the good account throttled: only the garbage file is left.
            pool.mark_throttled(0, "test-throttle")
            self.assertEqual(pool.next_attachable(), (-1, None))

    def test_accessor_returns_jar_for_stale_shared_cookies(self):
        if not SHARED_COOKIES.is_file():
            self.skipTest("operator-shared cookies.json not present in this tree")
        from services import youtube_pipeline_v2 as pipeline

        with mock.patch.object(
            session, "_verify_youtube_session",
            return_value=(False, "youtube-reported-signed-out"),
        ), mock.patch.dict(
            os.environ, {"VIDZEO_YT_COOKIE_FILES": str(SHARED_COOKIES)}
        ):
            session.reset_configured_pool()
            adapted = pipeline._get_authenticated_youtube_session()
        self.assertIsNotNone(adapted)
        self.assertGreaterEqual(sum(1 for _ in adapted), 1)


if __name__ == "__main__":
    unittest.main()
