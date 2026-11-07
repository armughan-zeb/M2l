import http.cookiejar
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


def _cookie(name="SAPISID", value="secret-value", domain=".youtube.com"):
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


def _authenticated_jar():
    jar = http.cookiejar.CookieJar()
    jar.set_cookie(_cookie())
    jar.set_cookie(_cookie("PREF", "preference", ".youtube.com"))
    jar.set_cookie(_cookie("UNRELATED", "discard-me", ".example.com"))
    return jar


class YouTubeSessionTests(unittest.TestCase):
    def setUp(self):
        self._environment = mock.patch.dict(
            os.environ,
            {
                "VIDZEO_YT_COOKIES_FROM_BROWSER": "firefox",
                "VIDZEO_YT_REQUIRE_PREFERRED_BROWSER": "0",
                "VIDZEO_YT_REQUIRE_AUTH_PROBE": "1",
            },
            clear=False,
        )
        self._environment.start()
        session.reset_for_tests()

    def tearDown(self):
        session.reset_for_tests()
        self._environment.stop()

    def test_auto_detect_persists_only_os_protected_container_and_redacts_status(self):
        profile = session.BrowserProfile("firefox", "profile-path", "default", time.time())
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {"VIDZEO_SECURE_DATA_DIR": temp_dir, "VIDZEO_YT_COOKIES_FILE": ""},
            clear=False,
        ), mock.patch.object(
            session, "detect_browser_profiles", return_value=[profile]
        ), mock.patch.object(
            session, "_extract_profile", return_value=_authenticated_jar()
        ), mock.patch.object(
            session, "_verify_youtube_session", return_value=(True, "youtube-confirmed")
        ), mock.patch.object(
            session, "_protect", side_effect=lambda value: b"dpapi:" + value
        ):
            jar, generation = session.ensure_session(force_refresh=True)
            status = session.session_status()
            container_path = Path(temp_dir) / "youtube-session.dpapi"
            container = json.loads(container_path.read_text(encoding="utf-8"))

        self.assertEqual(generation, 1)
        self.assertEqual(sum(1 for _ in jar), 2)
        self.assertTrue(status["ready"])
        self.assertTrue(status["verified_authenticated"])
        self.assertEqual(status["browser"], "firefox")
        self.assertNotIn("secret-value", json.dumps(status))
        self.assertNotIn("secret-value", json.dumps(container))
        self.assertEqual(container["protected"], "windows-dpapi-current-user")

    def test_persisted_session_restores_without_reopening_browser_database(self):
        profile = session.BrowserProfile("firefox", "profile-path", "default", time.time())
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {"VIDZEO_SECURE_DATA_DIR": temp_dir, "VIDZEO_YT_COOKIES_FILE": ""},
            clear=False,
        ), mock.patch.object(
            session, "detect_browser_profiles", return_value=[profile]
        ), mock.patch.object(
            session, "_protect", side_effect=lambda value: b"dpapi:" + value
        ), mock.patch.object(
            session, "_unprotect", side_effect=lambda value: value.removeprefix(b"dpapi:")
        ), mock.patch.object(
            session, "_extract_profile", return_value=_authenticated_jar()
        ) as extract, mock.patch.object(
            session, "_verify_youtube_session", return_value=(True, "youtube-confirmed")
        ) as verify:
            session.ensure_session(force_refresh=True)
            session.reset_for_tests()
            jar, generation = session.ensure_session()

        self.assertEqual(generation, 1)
        self.assertEqual(sum(1 for _ in jar), 2)
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(verify.call_count, 1)

    def test_stale_generation_refresh_is_single_flight(self):
        profile = session.BrowserProfile("firefox", "profile-path", "default", time.time())
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {"VIDZEO_SECURE_DATA_DIR": temp_dir, "VIDZEO_YT_COOKIES_FILE": ""},
            clear=False,
        ), mock.patch.object(
            session, "detect_browser_profiles", return_value=[profile]
        ), mock.patch.object(
            session, "_extract_profile", return_value=_authenticated_jar()
        ) as extract, mock.patch.object(
            session, "_verify_youtube_session", return_value=(True, "youtube-confirmed")
        ), mock.patch.object(
            session, "_persist", return_value=True
        ):
            _, first_generation = session.ensure_session(force_refresh=True)
            _, second_generation = session.ensure_session(
                force_refresh=True, stale_generation=first_generation, reason="403"
            )
            _, shared_generation = session.ensure_session(
                force_refresh=True, stale_generation=first_generation, reason="403"
            )

        self.assertEqual(second_generation, first_generation + 1)
        self.assertEqual(shared_generation, second_generation)
        self.assertEqual(extract.call_count, 2)

    def test_plaintext_legacy_snapshot_is_removed(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {
                "VIDZEO_TEMP_DIR": temp_dir,
                "VIDZEO_SECURE_DATA_DIR": str(Path(temp_dir) / "secure"),
                "VIDZEO_YT_COOKIES_FILE": "",
            },
            clear=False,
        ), mock.patch.object(session, "detect_browser_profiles", return_value=[]):
            legacy = Path(temp_dir) / "vidzeo_session_cookies.txt"
            legacy.write_text("sensitive", encoding="utf-8")
            with self.assertRaises(session.YouTubeSessionUnavailable):
                session.ensure_session(force_refresh=True)

        self.assertFalse(legacy.exists())

    def test_new_install_defaults_to_chrome(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {"VIDZEO_ROOT": temp_dir, "VIDZEO_YT_COOKIES_FROM_BROWSER": ""},
            clear=False,
        ):
            self.assertEqual(session._preferred_browser(), "chrome")

    def test_strict_chrome_does_not_silently_fallback_when_cookie_db_is_locked(self):
        chrome = session.BrowserProfile("chrome", "chrome-path", "Profile 4", time.time())
        firefox = session.BrowserProfile("firefox", "firefox-path", "default", time.time() + 10)
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {
                "VIDZEO_SECURE_DATA_DIR": temp_dir,
                "VIDZEO_YT_COOKIES_FILE": "",
                "VIDZEO_YT_COOKIES_FROM_BROWSER": "chrome",
                "VIDZEO_YT_REQUIRE_PREFERRED_BROWSER": "1",
                "VIDZEO_YT_COOKIE_EXTRACTION_ATTEMPTS": "1",
            },
            clear=False,
        ), mock.patch.object(
            session, "detect_browser_profiles", return_value=[chrome, firefox]
        ), mock.patch.object(
            session, "_extract_profile", side_effect=RuntimeError("Could not copy Chrome cookie database")
        ) as extract:
            with self.assertRaises(session.YouTubeSessionUnavailable):
                session.ensure_session(force_refresh=True)
            status = session.session_status()

        self.assertEqual(extract.call_count, 1)
        self.assertFalse(status["ready"])
        self.assertEqual(status["preferred_browser"], "chrome")
        self.assertEqual(status["extraction_issue"], "browser-open-cookie-lock")
        self.assertIn("Connect YouTube", status["message"])

    def test_cookie_names_are_not_enough_without_youtube_confirmation(self):
        chrome = session.BrowserProfile("chrome", "chrome-path", "Profile 4", time.time())
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {
                "VIDZEO_SECURE_DATA_DIR": temp_dir,
                "VIDZEO_YT_COOKIES_FILE": "",
                "VIDZEO_YT_COOKIES_FROM_BROWSER": "chrome",
                "VIDZEO_YT_REQUIRE_PREFERRED_BROWSER": "1",
                "VIDZEO_YT_REQUIRE_AUTH_PROBE": "1",
                "VIDZEO_YT_COOKIE_EXTRACTION_ATTEMPTS": "1",
            },
            clear=False,
        ), mock.patch.object(
            session, "detect_browser_profiles", return_value=[chrome]
        ), mock.patch.object(
            session, "_extract_profile", return_value=_authenticated_jar()
        ), mock.patch.object(
            session, "_verify_youtube_session", return_value=(False, "youtube-reported-signed-out")
        ):
            with self.assertRaises(session.YouTubeSessionUnavailable):
                session.ensure_session(force_refresh=True)
            status = session.session_status()

        self.assertFalse(status["ready"])
        self.assertFalse(status["verified_authenticated"])

    def test_import_cookie_file_verifies_filters_and_encrypts(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {
                "VIDZEO_SECURE_DATA_DIR": temp_dir,
                "VIDZEO_YT_COOKIES_FILE": "",
                "VIDZEO_YT_COOKIES_FROM_BROWSER": "chrome",
            },
            clear=False,
        ), mock.patch.object(
            session, "_verify_youtube_session", return_value=(True, "youtube-confirmed")
        ), mock.patch.object(
            session, "_persist", return_value=True
        ) as persist:
            cookie_path = Path(temp_dir) / "youtube-cookies.txt"
            export = http.cookiejar.MozillaCookieJar(str(cookie_path))
            for cookie in _authenticated_jar():
                export.set_cookie(cookie)
            export.save(ignore_discard=True, ignore_expires=True)

            jar, generation = session.import_cookie_file(cookie_path, browser="chrome")
            status = session.session_status()

        self.assertEqual(generation, 1)
        self.assertEqual(sum(1 for _ in jar), 2)
        self.assertTrue(status["ready"])
        self.assertTrue(status["verified_authenticated"])
        self.assertEqual(status["browser"], "chrome")
        self.assertEqual(status["source"], "manual browser export")
        self.assertEqual(status["verification"], "youtube-confirmed")
        persisted_jar = persist.call_args.args[0]
        self.assertNotIn("example.com", {cookie.domain.lstrip(".") for cookie in persisted_jar})

    def test_import_cookie_bytes_deletes_temporary_plaintext(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {"VIDZEO_SECURE_DATA_DIR": temp_dir, "VIDZEO_YT_COOKIES_FILE": ""},
            clear=False,
        ), mock.patch.object(
            session, "_verify_youtube_session", return_value=(True, "youtube-confirmed")
        ), mock.patch.object(
            session, "_persist", return_value=True
        ):
            source = Path(temp_dir) / "source.txt"
            export = http.cookiejar.MozillaCookieJar(str(source))
            for cookie in _authenticated_jar():
                export.set_cookie(cookie)
            export.save(ignore_discard=True, ignore_expires=True)
            payload = source.read_bytes()
            source.unlink()

            session.import_cookie_bytes(payload, browser="chrome")

            self.assertEqual(list(Path(temp_dir).glob("cookie-import-*.txt")), [])

    def test_persistent_chrome_records_verify_and_filter_without_plaintext_file(self):
        records = [
            {
                "name": "SAPISID",
                "value": "managed-secret",
                "domain": ".youtube.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "expirationDate": time.time() + 3600,
                "session": False,
            },
            {
                "name": "PREF",
                "value": "managed-pref",
                "domain": ".youtube.com",
                "path": "/",
                "secure": True,
                "expirationDate": time.time() + 3600,
            },
            {
                "name": "OTHER",
                "value": "must-not-enter-session",
                "domain": ".example.com",
                "path": "/",
                "expirationDate": time.time() + 3600,
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {"VIDZEO_SECURE_DATA_DIR": temp_dir, "VIDZEO_YT_COOKIES_FILE": ""},
            clear=False,
        ), mock.patch.object(
            session, "_verify_youtube_session", return_value=(True, "youtube-confirmed")
        ), mock.patch.object(
            session, "_persist", return_value=True
        ) as persist:
            jar, _generation = session.import_cookie_records(records, browser="chrome")
            status = session.session_status()

        self.assertEqual(sum(1 for _ in jar), 2)
        self.assertTrue(status["ready"])
        self.assertEqual(status["source"], "Vidzeo persistent Chrome connector")
        persisted_jar = persist.call_args.args[0]
        self.assertNotIn("example.com", {cookie.domain.lstrip(".") for cookie in persisted_jar})
        self.assertNotIn("managed-secret", json.dumps(status))

    def test_import_cookie_file_rejects_non_netscape_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cookie_path = Path(temp_dir) / "cookies.txt"
            cookie_path.write_text("not a browser cookie export", encoding="utf-8")
            with self.assertRaises(ValueError):
                session.import_cookie_file(cookie_path, browser="chrome")

    def test_managed_edge_import_keeps_session_cookies_and_updates_preference(self):
        records = [{
            "name": "SAPISID",
            "value": "managed-session-secret",
            "domain": ".youtube.com",
            "path": "/",
            "secure": True,
            # CDP uses -1 for a session cookie with no fixed expiry.
            "expires": -1,
            "session": True,
        }]
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {
                "VIDZEO_ROOT": temp_dir,
                "VIDZEO_SECURE_DATA_DIR": temp_dir,
                "VIDZEO_YT_COOKIES_FROM_BROWSER": "chrome",
            },
            clear=False,
        ), mock.patch.object(
            session, "_verify_youtube_session", return_value=(True, "youtube-confirmed")
        ), mock.patch.object(
            session, "_persist", return_value=True
        ):
            jar, _generation = session.import_cookie_records(records, browser="edge")
            status = session.session_status()

        self.assertEqual(sum(1 for _ in jar), 1)
        self.assertIsNone(next(iter(jar)).expires)
        self.assertTrue(status["ready"])
        self.assertEqual(status["browser"], "edge")
        self.assertEqual(status["preferred_browser"], "edge")
        self.assertTrue(status["using_preferred_browser"])

    def test_live_rejection_marks_session_for_cookie_update(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {
                "VIDZEO_SECURE_DATA_DIR": temp_dir,
                "VIDZEO_YT_COOKIES_FROM_BROWSER": "chrome",
            },
            clear=False,
        ), mock.patch.object(
            session, "_verify_youtube_session", return_value=(False, "youtube-reported-signed-out")
        ):
            session._install_memory_session(_authenticated_jar(), {
                "browser": "chrome",
                "preferred_browser": "chrome",
                "verified_authenticated": True,
            })
            with self.assertRaises(session.YouTubeSessionUnavailable) as raised:
                session.ensure_verified_session(reason="pipeline preflight")
            # The update-needed flag survives a broker/app restart without
            # retaining any cookie data in the redacted state file.
            session.reset_for_tests()
            status = session.session_status()
            with mock.patch.object(session, "detect_browser_profiles") as detect:
                with self.assertRaises(session.YouTubeSessionUnavailable):
                    session.ensure_session(reason="automatic UI detection")
                detect.assert_not_called()

        self.assertIn("Connect YouTube", str(raised.exception))
        self.assertFalse(status["ready"])
        self.assertTrue(status["cookie_update_required"])
        self.assertIn("Connect YouTube", status["message"])

    def test_rejected_import_marks_cookie_update_required(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {"VIDZEO_SECURE_DATA_DIR": temp_dir, "VIDZEO_YT_COOKIES_FROM_BROWSER": "chrome"},
            clear=False,
        ), mock.patch.object(
            session, "_verify_youtube_session", return_value=(False, "youtube-reported-signed-out")
        ):
            cookie_path = Path(temp_dir) / "cookies.txt"
            export = http.cookiejar.MozillaCookieJar(str(cookie_path))
            for cookie in _authenticated_jar():
                export.set_cookie(cookie)
            export.save(ignore_discard=True, ignore_expires=True)

            with self.assertRaises(session.YouTubeSessionUnavailable):
                session.import_cookie_file(cookie_path, browser="chrome")
            status = session.session_status()

    def test_netscape_cookies_written_and_read_back_on_install(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_cookie_file = Path(temp_dir) / "custom-cookies.txt"
            with mock.patch.dict(os.environ, {"VIDZEO_YT_COOKIES_FILE": str(custom_cookie_file)}, clear=False):
                jar = _authenticated_jar()
                session._install_memory_session(jar, {
                    "browser": "chrome",
                    "channel_title": "Test Channel",
                    "username": "Test Channel",
                    "verified_authenticated": True,
                })
                self.assertTrue(custom_cookie_file.is_file())
                # Verify that MozillaCookieJar can load it and sees the cookies
                loaded_jar = session._load_explicit_cookie_file(custom_cookie_file)
                self.assertTrue(session._jar_is_authenticated(loaded_jar))
                self.assertEqual(sum(1 for _ in loaded_jar), 2)

                # Test invalidate cleans it up
                session.invalidate_session("test")
                self.assertFalse(custom_cookie_file.is_file())

    def test_import_cookie_records_preserves_username_and_writes_netscape_cookies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_cookie_file = Path(temp_dir) / "imported-cookies.txt"
            with mock.patch.dict(
                os.environ,
                {"VIDZEO_SECURE_DATA_DIR": temp_dir, "VIDZEO_YT_COOKIES_FILE": str(custom_cookie_file)},
                clear=False,
            ), mock.patch.object(
                session, "_verify_youtube_session", return_value=(True, "youtube-confirmed")
            ), mock.patch.object(
                session, "_protect", side_effect=lambda value: b"dpapi:" + value
            ):
                records = [
                    {"name": "LOGIN_INFO", "value": "test-login-token", "domain": ".youtube.com", "path": "/", "secure": True},
                    {"name": "SAPISID", "value": "test-sapisid", "domain": ".youtube.com", "path": "/", "secure": True},
                    {"name": "SID", "value": "test-sid", "domain": ".youtube.com", "path": "/", "secure": True},
                ]
                installed, gen = session.import_cookie_records(records, browser="chrome", username="@TechDocumentary")
                status = session.session_status()

                self.assertTrue(status["ready"])
                self.assertTrue(status["verified_authenticated"])
                self.assertEqual(status["username"], "@TechDocumentary")
                self.assertEqual(status["channel_title"], "@TechDocumentary")
                self.assertIn("@TechDocumentary", status["message"])
                self.assertTrue(custom_cookie_file.is_file())
                content = custom_cookie_file.read_text(encoding="utf-8")
                self.assertIn("# Netscape HTTP Cookie File", content)
                self.assertIn("LOGIN_INFO", content)
                self.assertIn("SAPISID", content)


if __name__ == "__main__":
    unittest.main()

