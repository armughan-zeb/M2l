"""Cookie write-back: rotated session tokens must survive the process.

Synthetic jars and fake cookie values only -- the real operator cookies
are never read here. Covers: rotation written back, fail-closed when auth
cookies are lost, format preservation (JSON + Netscape), one backup
rotation, 0600 perms, no cookie value in logs, no-op when unchanged, and
the download/search hooks in the pipeline.
"""
import http.cookiejar
import json
import logging
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import youtube_cookie_persistence as persist

FUTURE = 1893456000  # 2030-01-01, safely beyond any test run

# Fake values -- sentinels that must never appear in any log line.
SID_OLD = "FAKE_SID_OLD_111"
SID_NEW = "FAKE_SID_ROTATED_222"
SAPISID = "FAKE_SAPISID_333"
SENTINEL = "FAKE_SENTINEL_VALUE_9f3z7"


def _make_cookie(name, value, domain=".youtube.com", path="/", secure=True,
                 expires=FUTURE, http_only=False, same_site=None):
    rest = {}
    if http_only:
        rest["HttpOnly"] = None
    if same_site:
        rest["SameSite"] = same_site
    return http.cookiejar.Cookie(
        version=0, name=name, value=value, port=None, port_specified=False,
        domain=domain, domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path=path, path_specified=True, secure=secure, expires=expires,
        discard=False, comment=None, comment_url=None, rest=rest, rfc2109=False,
    )


def _make_jar(*cookies):
    jar = http.cookiejar.CookieJar()
    for cookie in cookies:
        jar.set_cookie(cookie)
    return jar


def _json_cookie_file(*items):
    return (json.dumps(list(items), indent=2) + "\n").encode("utf-8")


def _base_items(sid_value):
    return [
        {"name": "SID", "value": sid_value, "domain": ".youtube.com",
         "path": "/", "secure": True, "expirationDate": FUTURE,
         "httpOnly": False, "session": False},
        {"name": "SAPISID", "value": SAPISID, "domain": ".youtube.com",
         "path": "/", "secure": True, "expirationDate": FUTURE,
         "httpOnly": True, "session": False},
        {"name": "PREF", "value": "FAKE_PREF_444", "domain": ".youtube.com",
         "path": "/", "secure": False, "expirationDate": FUTURE,
         "httpOnly": False, "session": False},
        # Non-YouTube entry: write-back must keep it verbatim.
        {"name": "NID", "value": "FAKE_NID_555", "domain": ".google.com",
         "path": "/", "secure": True, "expirationDate": FUTURE,
         "httpOnly": True, "session": False},
    ]


def _netscape_cookie_file(sid_value):
    lines = [
        "# Netscape HTTP Cookie File",
        "# http://curl.haxx.se/rfc/cookie_spec.html",
        "",
        f".youtube.com\tTRUE\t/\tTRUE\t{FUTURE}\tSID\t{sid_value}",
        f".youtube.com\tTRUE\t/\tTRUE\t{FUTURE}\tSAPISID\t{SAPISID}",
        f".youtube.com\tTRUE\t/\tFALSE\t{FUTURE}\tPREF\tFAKE_PREF_444",
        f".google.com\tTRUE\t/\tTRUE\t{FUTURE}\tNID\tFAKE_NID_555",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _rotated_jar(sid_value=SID_NEW):
    return _make_jar(
        _make_cookie("SID", sid_value),
        _make_cookie("SAPISID", SAPISID, http_only=True),
        _make_cookie("PREF", "FAKE_PREF_444", secure=False),
    )


class CookiePersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        os.environ.pop("VIDZEO_YT_COOKIE_WRITEBACK", None)

    def tearDown(self):
        os.environ.pop("VIDZEO_YT_COOKIE_WRITEBACK", None)
        self._tmp.cleanup()

    def _json_path(self, sid_value=SID_OLD):
        path = self.tmp / "cookies.json"
        path.write_bytes(_json_cookie_file(*_base_items(sid_value)))
        return path

    def _txt_path(self, sid_value=SID_OLD):
        path = self.tmp / "cookies.txt"
        path.write_bytes(_netscape_cookie_file(sid_value))
        return path

    def _read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def _value_of(self, items, name):
        return next(item["value"] for item in items if item["name"] == name)

    # (a) synthetic rotation is written back ---------------------------
    def test_json_rotation_writes_back_changed_value(self):
        path = self._json_path()
        self.assertTrue(persist.persist_cookie_jar(_rotated_jar(), path))
        items = self._read_json(path)
        self.assertEqual(self._value_of(items, "SID"), SID_NEW)
        self.assertEqual(self._value_of(items, "SAPISID"), SAPISID)
        # Non-YouTube entry survives verbatim.
        self.assertEqual(self._value_of(items, "NID"), "FAKE_NID_555")

    def test_netscape_rotation_writes_back_changed_value(self):
        path = self._txt_path()
        self.assertTrue(persist.persist_cookie_jar(_rotated_jar(), path))
        jar = http.cookiejar.MozillaCookieJar(str(path))
        jar.load(ignore_discard=True, ignore_expires=True)
        by_name = {cookie.name: cookie.value for cookie in jar}
        self.assertEqual(by_name["SID"], SID_NEW)
        self.assertEqual(by_name["SAPISID"], SAPISID)

    # (b) jar missing auth cookies leaves the file untouched ------------
    def test_jar_missing_auth_cookies_leaves_file_untouched(self):
        path = self._json_path()
        before = path.read_bytes()
        jar = _make_jar(
            _make_cookie("PREF", "FAKE_PREF_444", secure=False),
            _make_cookie("VISITOR_INFO1_LIVE", "FAKE_VISITOR_666", secure=False),
        )
        self.assertFalse(persist.persist_cookie_jar(jar, path))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.tmp / "cookies.json.bak").exists())

    def test_empty_jar_leaves_file_untouched(self):
        path = self._json_path()
        before = path.read_bytes()
        self.assertFalse(persist.persist_cookie_jar(_make_jar(), path))
        self.assertEqual(path.read_bytes(), before)

    # (c) format preserved ----------------------------------------------
    def test_json_format_preserved(self):
        path = self._json_path()
        self.assertTrue(persist.persist_cookie_jar(_rotated_jar(), path))
        items = self._read_json(path)
        self.assertIsInstance(items, list)
        self.assertEqual(
            {item["name"] for item in items},
            {"SID", "SAPISID", "PREF", "NID"})
        for item in items:
            self.assertIn("domain", item)
            self.assertIn("path", item)

    def test_netscape_format_preserved(self):
        path = self._txt_path()
        self.assertTrue(persist.persist_cookie_jar(_rotated_jar(), path))
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Netscape HTTP Cookie File"))
        # Cookie lines are the tab-separated ones; the #HttpOnly_ prefix
        # on httpOnly cookies also starts with '#', so count by tabs.
        lines = [ln for ln in text.splitlines() if "\t" in ln]
        self.assertEqual(len(lines), 4)
        for line in lines:
            self.assertEqual(len(line.split("\t")), 7)

    def test_written_json_reloads_through_pipeline_loader(self):
        # The file the pipeline loads next start must parse identically.
        from services import youtube_session as session

        path = self._json_path()
        self.assertTrue(persist.persist_cookie_jar(_rotated_jar(), path))
        jar = session._load_explicit_cookie_file(path)
        by_name = {cookie.name: cookie.value for cookie in jar}
        self.assertEqual(by_name["SID"], SID_NEW)
        self.assertEqual(by_name["SAPISID"], SAPISID)

    def test_written_netscape_reloads_through_pipeline_loader(self):
        from services import youtube_session as session

        path = self._txt_path()
        self.assertTrue(persist.persist_cookie_jar(_rotated_jar(), path))
        jar = session._load_explicit_cookie_file(path)
        by_name = {cookie.name: cookie.value for cookie in jar}
        self.assertEqual(by_name["SID"], SID_NEW)

    # (d) backup holds the previous content ------------------------------
    def test_backup_created_with_previous_content(self):
        path = self._json_path()
        self.assertTrue(persist.persist_cookie_jar(_rotated_jar(), path))
        backup = self.tmp / "cookies.json.bak"
        self.assertTrue(backup.is_file())
        backup_items = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual(self._value_of(backup_items, "SID"), SID_OLD)
        # Backup is itself a valid cookie file the loader accepts.
        self.assertEqual(self._value_of(backup_items, "SAPISID"), SAPISID)

    # (e) perms 0600 -------------------------------------------------------
    @unittest.skipIf(sys.platform == "win32", "POSIX file modes not supported on Windows")
    def test_written_file_and_backup_are_0600(self):
        path = self._json_path()
        self.assertTrue(persist.persist_cookie_jar(_rotated_jar(), path))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        backup = self.tmp / "cookies.json.bak"
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)

    # (f) no cookie value appears in logs ----------------------------------
    def test_no_cookie_value_appears_in_logs(self):
        path = self._json_path()
        jar = _rotated_jar(SENTINEL)
        with self.assertLogs("services.youtube_cookie_persistence",
                             level="DEBUG") as captured:
            self.assertTrue(persist.persist_cookie_jar(jar, path))
        output = "\n".join(captured.output)
        self.assertNotIn(SENTINEL, output)
        self.assertNotIn(SID_OLD, output)
        self.assertNotIn(SAPISID, output)
        # The write did happen (the log line exists, values do not).
        self.assertIn("wrote back", output)

    def test_refusal_logs_no_values_either(self):
        path = self._json_path()
        jar = _make_jar(_make_cookie("PREF", SENTINEL, secure=False))
        with self.assertLogs("services.youtube_cookie_persistence",
                             level="DEBUG") as captured:
            self.assertFalse(persist.persist_cookie_jar(jar, path))
        self.assertNotIn(SENTINEL, "\n".join(captured.output))

    # no-op when nothing changed -------------------------------------------
    def test_unchanged_jar_does_not_rewrite_file(self):
        path = self._json_path()
        jar = _make_jar(
            _make_cookie("SID", SID_OLD),
            _make_cookie("SAPISID", SAPISID, http_only=True),
            _make_cookie("PREF", "FAKE_PREF_444", secure=False),
        )
        before = path.read_bytes()
        mtime_before = path.stat().st_mtime_ns
        self.assertTrue(persist.persist_cookie_jar(jar, path))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, mtime_before)
        self.assertFalse((self.tmp / "cookies.json.bak").exists())

    # misc guards ------------------------------------------------------------
    def test_expired_cookies_are_not_written_back(self):
        path = self._json_path()
        jar = _make_jar(
            _make_cookie("SID", SID_NEW),
            _make_cookie("SAPISID", SAPISID, http_only=True),
            _make_cookie("DEAD_COOKIE", "FAKE_DEAD_888", expires=1000),
        )
        self.assertTrue(persist.persist_cookie_jar(jar, path))
        items = self._read_json(path)
        self.assertNotIn("DEAD_COOKIE", {item["name"] for item in items})

    def test_unknown_suffix_is_refused(self):
        path = self.tmp / "cookies.yaml"
        path.write_bytes(_json_cookie_file(*_base_items(SID_OLD)))
        self.assertFalse(persist.persist_cookie_jar(_rotated_jar(), path))
        self.assertEqual(path.read_bytes(),
                         _json_cookie_file(*_base_items(SID_OLD)))

    def test_missing_source_file_is_refused(self):
        self.assertFalse(persist.persist_cookie_jar(
            _rotated_jar(), self.tmp / "nope.json"))

    def test_writeback_env_kill_switch(self):
        path = self._json_path()
        before = path.read_bytes()
        with mock.patch.dict(os.environ, {"VIDZEO_YT_COOKIE_WRITEBACK": "0"}):
            self.assertFalse(persist.persist_cookie_jar(_rotated_jar(), path))
        self.assertEqual(path.read_bytes(), before)

    # pool resolution ---------------------------------------------------------
    def test_persist_session_jar_resolves_pool_path(self):
        path = self._json_path()
        pool = types.SimpleNamespace(paths=(path,))
        session_jar = _rotated_jar()
        session_jar._vidzeo_session_pool = pool
        session_jar._vidzeo_session_index = 0
        used_jar = _rotated_jar()
        self.assertTrue(persist.persist_session_jar(used_jar, session_jar))
        self.assertEqual(
            self._value_of(self._read_json(path), "SID"), SID_NEW)

    def test_persist_session_jar_refreshes_encrypted_snapshot(self):
        from services import youtube_session as session

        path = self._json_path()
        pool = types.SimpleNamespace(paths=(path,))
        session_jar = _rotated_jar()
        session_jar._vidzeo_session_pool = pool
        session_jar._vidzeo_session_index = 0
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ, {"VIDZEO_SECURE_DATA_DIR": tmp}, clear=False,
            ):
                self.assertTrue(
                    persist.persist_session_jar(_rotated_jar(), session_jar))
                loaded, _meta = session._load_persisted()
        self.assertIsNotNone(loaded)
        values = {c.name: c.value for c in loaded}
        self.assertEqual(values.get("SID"), SID_NEW)
        self.assertEqual(values.get("SAPISID"), SAPISID)

    def test_persist_session_jar_skips_unknown_source(self):
        path = self._json_path()
        before = path.read_bytes()
        used_jar = _rotated_jar()
        # No pool/index attributes anywhere: must not touch any file.
        self.assertFalse(persist.persist_session_jar(used_jar))
        self.assertEqual(path.read_bytes(), before)

    def test_persist_session_jar_never_raises(self):
        # A hostile pool object must not break the caller.
        bad_pool = types.SimpleNamespace(paths="not-a-tuple")
        self.assertFalse(persist.persist_session_jar(
            _rotated_jar(), pool=bad_pool, index=0))
        self.assertFalse(persist.persist_session_jar(None))


class DownloadHookTests(unittest.TestCase):
    """The pipeline writes back tokens yt-dlp rotated during a download."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        os.environ.pop("VIDZEO_YT_COOKIE_WRITEBACK", None)

    def tearDown(self):
        os.environ.pop("VIDZEO_YT_COOKIE_WRITEBACK", None)
        self._tmp.cleanup()

    def test_download_with_youtube_session_persists_rotated_sid(self):
        from services import youtube_pipeline_v2 as pipeline

        path = self.tmp / "cookies.json"
        path.write_bytes(_json_cookie_file(*_base_items(SID_OLD)))
        pool = types.SimpleNamespace(paths=(path,))
        session_jar = _rotated_jar(SID_OLD)
        session_jar._vidzeo_session_pool = pool
        session_jar._vidzeo_session_index = 0

        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def download(self, urls):
                # Simulate YouTube rotating SID on this request.
                jar = self.__dict__.get("cookiejar")
                assert jar is not None
                jar.set_cookie(_make_cookie("SID", SID_NEW))

        fake_yt_dlp = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
        pipeline._download_with_youtube_session(
            fake_yt_dlp, {}, "https://www.youtube.com/watch?v=FAKE1234567",
            session_jar, None, self.tmp, "raw_")
        items = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            next(i["value"] for i in items if i["name"] == "SID"), SID_NEW)
        backup = self.tmp / "cookies.json.bak"
        self.assertTrue(backup.is_file())
        backup_items = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual(
            next(i["value"] for i in backup_items if i["name"] == "SID"),
            SID_OLD)

    def test_search_uncached_persists_rotated_sid(self):
        from services import youtube_pipeline_v2 as pipeline
        from services import yt_dlp_runtime

        path = self.tmp / "cookies.json"
        path.write_bytes(_json_cookie_file(*_base_items(SID_OLD)))
        pool = types.SimpleNamespace(paths=(path,))
        session_jar = _rotated_jar(SID_OLD)
        session_jar._vidzeo_session_pool = pool
        session_jar._vidzeo_session_index = 0

        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download=False):
                jar = self.__dict__.get("cookiejar")
                assert jar is not None
                jar.set_cookie(_make_cookie("SID", SID_NEW))
                return {"entries": [{"id": "FAKE1234567", "title": "t",
                                     "duration": 60}]}

        fake_yt_dlp = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
        cache_dir = self.tmp / "yt_search_cache"
        with mock.patch.dict(os.environ,
                            {"VIDZEO_YT_SEARCH_CACHE_DIR": str(cache_dir)}):
            with mock.patch.object(yt_dlp_runtime, "load_yt_dlp",
                                   return_value=fake_yt_dlp):
                entries = pipeline._search_youtube_uncached(
                    "fake query", 1, session_jar=session_jar)
        self.assertEqual(len(entries), 1)
        items = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            next(i["value"] for i in items if i["name"] == "SID"), SID_NEW)


if __name__ == "__main__":
    unittest.main()
