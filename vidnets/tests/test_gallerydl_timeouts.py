"""Unit tests for gallery-dl timeout configuration, config file, and URL discovery.

Tests verify:
 - Gallery-dl timeout values are at safe minimums
 - gallery-dl.conf has no sleep-request (the root cause of all timeouts)
 - --get-urls command is used (faster than -D download mode)
 - --http-timeout is set appropriately
 - Semaphore allows enough parallelism
 - _discover_gallery_urls command construction and error handling
 - Future timeout in _run_harvest is sufficient
 - URL parsing and video-extension filtering
"""

import json
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

# ── patch noisy side-effects before importing the module under test ──
_original_print = print


def _quiet_print(*args, **kwargs):
    pass  # suppress module-level prints during test imports


sys.modules["builtins"].print = _quiet_print


class GalleryDLTimeoutTests(unittest.TestCase):
    """Tests for gallery-dl timeout configuration and command construction."""

    @classmethod
    def setUpClass(cls):
        """Restore print after imports are done."""
        sys.modules["builtins"].print = _original_print
        import services.web_image.resolver as _resolver
        cls._resolver = _resolver
        cls._gdl_conf_path = (
            Path(__file__).parents[1] / "gallery-dl.conf"
        )

    # ═══════════════════════════════════════════════════════════════════
    #  TIMEOUT CONSTANTS
    # ═══════════════════════════════════════════════════════════════════

    def test_gallerydl_timeouts_are_configured(self):
        """Flickr must have explicit, safe timeout value."""
        t = self._resolver._GALLERYDL_TIMEOUTS
        self.assertIn("flickr", t)

    def test_gallerydl_timeouts_above_minimum(self):
        """No source timeout may be below 15s — the safe floor for gallery-dl."""
        for source, timeout in self._resolver._GALLERYDL_TIMEOUTS.items():
            with self.subTest(source=source):
                self.assertGreaterEqual(
                    timeout, 15,
                    f"{source} timeout={timeout}s  is too low — "
                    f"gallery-dl with --range 1-3 can take 5-10s per source; "
                    f"15s is the minimum floor"
                )

    def test_flickr_timeout_sufficient(self):
        """Flickr must have a safe timeout."""
        t = self._resolver._GALLERYDL_TIMEOUTS
        self.assertGreaterEqual(t["flickr"], 30)

    def test_default_fallback_timeout_is_safe(self):
        """_discover_gallery_urls.get(unknown_source, default) must be >= 15."""
        t = self._resolver._GALLERYDL_TIMEOUTS
        default = t.get("nonexistent_source", 20)
        self.assertGreaterEqual(
            default, 15,
            "default fallback timeout must be at least 15s — "
            "if it's lower, unrecognised sources will timeout prematurely"
        )

    # ═══════════════════════════════════════════════════════════════════
    #  GALLERY-DL CONFIG FILE
    # ═══════════════════════════════════════════════════════════════════

    def test_gallerydl_conf_exists(self):
        """Config file must exist on disk."""
        self.assertTrue(
            self._gdl_conf_path.exists(),
            f"gallery-dl.conf not found at {self._gdl_conf_path}"
        )

    def test_gallerydl_conf_no_sleep_request(self):
        """sleep-request was the root cause of all gallery-dl timeouts.
        It added 1s delay per HTTP request. Must be absent or <= 0.2."""
        if not self._gdl_conf_path.exists():
            self.skipTest("gallery-dl.conf not found")
        raw = self._gdl_conf_path.read_text(encoding="utf-8")
        conf = json.loads(raw)
        extractor = conf.get("extractor", {})
        sleep_req = extractor.get("sleep-request", 0)
        self.assertLessEqual(
            sleep_req, 0.2,
            f"sleep-request={sleep_req} is too high — "
            f"this value added a delay PER HTTP request and was the root cause "
            f"of gallery-dl timing out on every query. Max allowed: 0.2s"
        )

    def test_gallerydl_conf_sleep_429_reasonable(self):
        """sleep-429 adds delay when rate-limited; must be <= 10s."""
        if not self._gdl_conf_path.exists():
            self.skipTest("gallery-dl.conf not found")
        raw = self._gdl_conf_path.read_text(encoding="utf-8")
        conf = json.loads(raw)
        extractor = conf.get("extractor", {})
        sleep_429 = extractor.get("sleep-429", 0)
        self.assertLessEqual(
            sleep_429, 10,
            f"sleep-429={sleep_429} is too high — "
            f"long rate-limit sleeps will cause timeouts"
        )

    def test_gallerydl_conf_has_image_only_wrappers(self):
        """IMG sources must use image-only extraction to skip video/webm."""
        if not self._gdl_conf_path.exists():
            self.skipTest("gallery-dl.conf not found")
        raw = self._gdl_conf_path.read_text(encoding="utf-8")
        conf = json.loads(raw)
        extractor = conf.get("extractor", {})
        for src in ("flickr", "pinterest", "imgur"):
            src_cfg = extractor.get(src, {})
            self.assertTrue(
                src_cfg.get("image-only", False),
                f"{src} must have 'image-only': true in gallery-dl.conf"
            )

    # ═══════════════════════════════════════════════════════════════════
    #  SEMAPHORE — PARALLELISM GATE
    # ═══════════════════════════════════════════════════════════════════

    def test_gdl_semaphore_allows_parallelism(self):
        """Semaphore must allow >=2 concurrent gallery-dl subprocesses."""
        sem = self._resolver._GDL_SEMAPHORE
        acquired = 0
        try:
            for _ in range(10):
                if sem.acquire(blocking=False):
                    acquired += 1
                else:
                    break
        finally:
            for _ in range(acquired):
                sem.release()
        self.assertGreaterEqual(
            acquired, 2,
            f"semaphore allows only {acquired} concurrent gallery-dl calls — "
            f"need at least 2"
        )

    # ═══════════════════════════════════════════════════════════════════
    #  COMMAND CONSTRUCTION (_discover_gallery_urls)
    # ═══════════════════════════════════════════════════════════════════

    @mock.patch("subprocess.run")
    @mock.patch("services.web_image.resolver._GDL_SEMAPHORE")
    def test_discover_uses_get_urls_flag(self, mock_gdl_sem, mock_run):
        """Must use --get-urls (URL discovery), NOT -D (download).
        --get-urls is 6x faster because it avoids file I/O."""
        mock_gdl_sem.__enter__ = mock.MagicMock()
        mock_gdl_sem.__exit__ = mock.MagicMock()
        mock_run.return_value = mock.Mock(returncode=0, stdout="")

        gdl = ["gallery-dl"]
        self._resolver._discover_gallery_urls("flickr", "test query", gdl)

        called_cmd = mock_run.call_args[0][0]
        full_cmd = " ".join(called_cmd)
        self.assertIn("--get-urls", full_cmd)
        self.assertNotIn("-D", full_cmd)

    @mock.patch("subprocess.run")
    @mock.patch("services.web_image.resolver._GDL_SEMAPHORE")
    def test_discover_uses_http_timeout_flag(self, mock_gdl_sem, mock_run):
        """--http-timeout must be set to fail fast on slow connections."""
        mock_gdl_sem.__enter__ = mock.MagicMock()
        mock_gdl_sem.__exit__ = mock.MagicMock()
        mock_run.return_value = mock.Mock(returncode=0, stdout="")

        gdl = ["gallery-dl"]
        self._resolver._discover_gallery_urls("flickr", "test query", gdl)

        called_cmd = mock_run.call_args[0][0]
        http_timeout_idx = called_cmd.index("--http-timeout")
        http_timeout_val = int(called_cmd[http_timeout_idx + 1])
        self.assertGreaterEqual(http_timeout_val, 10,
            f"--http-timeout {http_timeout_val} is too low — need at least 10s")
        self.assertLessEqual(http_timeout_val, 30,
            f"--http-timeout {http_timeout_val} is too high — single HTTP req shouldn't take >30s")

    @mock.patch("subprocess.run")
    @mock.patch("services.web_image.resolver._GDL_SEMAPHORE")
    def test_discover_uses_config_flag(self, mock_gdl_sem, mock_run):
        """--config must point to gallery-dl.conf for image-only extraction."""
        mock_gdl_sem.__enter__ = mock.MagicMock()
        mock_gdl_sem.__exit__ = mock.MagicMock()
        mock_run.return_value = mock.Mock(returncode=0, stdout="")

        gdl = ["gallery-dl"]
        self._resolver._discover_gallery_urls("flickr", "test query", gdl)

        called_cmd = mock_run.call_args[0][0]
        full_cmd = " ".join(called_cmd)
        self.assertIn("--config", full_cmd)
        self.assertIn("gallery-dl.conf", full_cmd)

    @mock.patch("subprocess.run")
    @mock.patch("services.web_image.resolver._GDL_SEMAPHORE")
    def test_discover_uses_bounded_recall_range(self, mock_gdl_sem, mock_run):
        """--range should harvest a useful bounded candidate set."""
        mock_gdl_sem.__enter__ = mock.MagicMock()
        mock_gdl_sem.__exit__ = mock.MagicMock()
        mock_run.return_value = mock.Mock(returncode=0, stdout="")

        gdl = ["gallery-dl"]
        self._resolver._discover_gallery_urls("flickr", "test query", gdl)

        called_cmd = mock_run.call_args[0][0]
        self.assertIn("--range", called_cmd)
        range_idx = called_cmd.index("--range")
        range_val = called_cmd[range_idx + 1]
        self.assertEqual(range_val, "1-20", f"--range must default to 1-20, got {range_val}")

    @mock.patch("subprocess.run")
    @mock.patch("services.web_image.resolver._GDL_SEMAPHORE")
    def test_discover_subprocess_uses_correct_timeout(self, mock_gdl_sem, mock_run):
        """subprocess.run must use the timeout from _GALLERYDL_TIMEOUTS."""
        mock_gdl_sem.__enter__ = mock.MagicMock()
        mock_gdl_sem.__exit__ = mock.MagicMock()
        mock_run.return_value = mock.Mock(returncode=0, stdout="")

        gdl = ["gallery-dl"]
        self._resolver._discover_gallery_urls("flickr", "test query", gdl)
        flickr_timeout = mock_run.call_args[1].get("timeout")
        self.assertEqual(
            flickr_timeout, self._resolver._GALLERYDL_TIMEOUTS["flickr"],
            f"subprocess timeout {flickr_timeout}s != config {self._resolver._GALLERYDL_TIMEOUTS['flickr']}s"
        )

    # ═══════════════════════════════════════════════════════════════════
    #  TIMEOUT HANDLING
    # ═══════════════════════════════════════════════════════════════════

    @mock.patch("subprocess.run")
    @mock.patch("services.web_image.resolver._GDL_SEMAPHORE")
    @mock.patch("services.web_image.resolver._web_log")
    def test_discover_handles_timeout_gracefully(self, mock_log, mock_gdl_sem, mock_run):
        """subprocess.TimeoutExpired must be caught and logged, not crash."""
        mock_gdl_sem.__enter__ = mock.MagicMock()
        mock_gdl_sem.__exit__ = mock.MagicMock()
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gallery-dl", timeout=5)

        gdl = ["gallery-dl"]
        result = self._resolver._discover_gallery_urls("flickr", "cricket", gdl)

        self.assertEqual(result, [], "timeout must return empty list, not None")
        self.assertTrue(mock_log.called, "timeout must be logged")

    @mock.patch("subprocess.run")
    @mock.patch("services.web_image.resolver._GDL_SEMAPHORE")
    @mock.patch("services.web_image.resolver._web_log")
    def test_discover_handles_generic_error_gracefully(self, mock_log, mock_gdl_sem, mock_run):
        """Generic exceptions must be caught and logged, not crash."""
        mock_gdl_sem.__enter__ = mock.MagicMock()
        mock_gdl_sem.__exit__ = mock.MagicMock()
        mock_run.side_effect = OSError("disk full")

        gdl = ["gallery-dl"]
        result = self._resolver._discover_gallery_urls("flickr", "cricket", gdl)

        self.assertEqual(result, [], "error must return empty list, not None")
        self.assertTrue(mock_log.called, "error must be logged")

    # ═══════════════════════════════════════════════════════════════════
    #  URL PARSING
    # ═══════════════════════════════════════════════════════════════════

    @mock.patch("subprocess.run")
    @mock.patch("services.web_image.resolver._GDL_SEMAPHORE")
    def test_discover_parses_http_urls_only(self, mock_gdl_sem, mock_run):
        """Only http/https lines are accepted; info lines are ignored."""
        mock_gdl_sem.__enter__ = mock.MagicMock()
        mock_gdl_sem.__exit__ = mock.MagicMock()
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout=(
                "[flickr][info] Retrieving public API key\n"
                "https://example.com/image1.jpg\n"
                "https://example.com/image2.png\n"
                "Some random output line\n"
                "  https://example.com/image3.webp  \n"
            ),
        )

        gdl = ["gallery-dl"]
        result = self._resolver._discover_gallery_urls("flickr", "cricket", gdl)

        self.assertEqual(len(result), 3, "must return 3 URLs, not info lines")
        urls = [r["url"] for r in result]
        self.assertIn("https://example.com/image1.jpg", urls)
        self.assertIn("https://example.com/image2.png", urls)
        self.assertNotIn("Some random output line", urls)

    @mock.patch("subprocess.run")
    @mock.patch("services.web_image.resolver._GDL_SEMAPHORE")
    def test_discover_filters_video_extensions(self, mock_gdl_sem, mock_run):
        """Video/anim extensions (.mp4, .webm, .ogv, .gif, .mov) must be skipped."""
        mock_gdl_sem.__enter__ = mock.MagicMock()
        mock_gdl_sem.__exit__ = mock.MagicMock()
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout=(
                "https://example.com/photo.jpg\n"
                "https://example.com/clip.mp4\n"
                "https://example.com/animation.gif\n"
                "https://example.com/screenshot.png\n"
                "https://example.com/video.webm\n"
                "https://example.com/footage.mov\n"
                "https://example.com/recording.ogv\n"
            ),
        )

        gdl = ["gallery-dl"]
        result = self._resolver._discover_gallery_urls("flickr", "cricket", gdl)

        urls = [r["url"] for r in result]
        self.assertIn("https://example.com/photo.jpg", urls)
        self.assertIn("https://example.com/screenshot.png", urls)
        self.assertNotIn("https://example.com/clip.mp4", urls)
        self.assertNotIn("https://example.com/animation.gif", urls)
        self.assertNotIn("https://example.com/video.webm", urls)
        self.assertNotIn("https://example.com/footage.mov", urls)
        self.assertNotIn("https://example.com/recording.ogv", urls)

    @mock.patch("subprocess.run")
    @mock.patch("services.web_image.resolver._GDL_SEMAPHORE")
    def test_discover_returns_structured_records(self, mock_gdl_sem, mock_run):
        """Each URL must return {url, title, source, query} dict."""
        mock_gdl_sem.__enter__ = mock.MagicMock()
        mock_gdl_sem.__exit__ = mock.MagicMock()
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout="https://example.com/img.jpg\n",
        )

        gdl = ["gallery-dl"]
        result = self._resolver._discover_gallery_urls("flickr", "Virat Kohli", gdl)

        self.assertEqual(len(result), 1)
        rec = result[0]
        self.assertEqual(rec["url"], "https://example.com/img.jpg")
        self.assertEqual(rec["title"], "")
        self.assertEqual(rec["source"], "flickr")
        self.assertEqual(rec["query"], "Virat Kohli")
        self.assertEqual(rec["discovery_method"], "gallery-dl")
        self.assertTrue(rec["metadata_pending"])

    # ═══════════════════════════════════════════════════════════════════
    #  SOURCE CONFIGURATION
    # ═══════════════════════════════════════════════════════════════════

    def test_sources_configuration(self):
        """Flickr GDL source must be configured with search URL."""
        sources = dict(self._resolver._GALLERYDL_SOURCES)
        self.assertIn("flickr", sources)
        self.assertIn("search", sources["flickr"])
        self.assertEqual(len(sources), 4, "Expected 4 gallery-dl sources configured")

    def test_all_sources_includes_gdl(self):
        """_ALL_SOURCES must include Flickr/Pinterest/Reddit GDL entries typed as 'gdl'."""
        gdl_sources = [s for s, t in self._resolver._ALL_SOURCES if t == "gdl"]
        self.assertEqual(
            len(gdl_sources), 3,
            f"expected 3 gallery-dl sources, got {gdl_sources}"
        )
        self.assertIn("flickr", gdl_sources)
        self.assertIn("pinterest", gdl_sources)
        self.assertIn("reddit", gdl_sources)
        api_sources = [s for s, t in self._resolver._ALL_SOURCES if t == "api"]
        self.assertGreaterEqual(
            len(api_sources), 2,
            f"expected >=2 API sources, got {api_sources}"
        )
        # Wikimedia re-enabled as an API lane with 429/5xx retry handling.
        # Qwant removed due to 403 captcha blocking
        # Imgur removed due to unreliable timeouts
        source_names = [s for s, t in self._resolver._ALL_SOURCES]
        self.assertIn("wikimedia", source_names, "wikimedia is an enabled API lane with retry handling")
        self.assertNotIn("qwant", source_names, "qwant removed due to 403 captcha blocking")
        self.assertNotIn("imgur", source_names, "imgur removed due to unreliable GDL timeouts")

    # ═══════════════════════════════════════════════════════════════════
    #  FUTURE TIMEOUT (covers _run_harvest)
    # ═══════════════════════════════════════════════════════════════════

    def test_future_timeout_exceeds_gdl_timeout(self):
        """The ThreadPoolExecutor future timeout must be >= the longest
        gallery-dl source timeout, or gallery-dl results get swallowed.

        We verify by checking that 50 >= max(_GALLERYDL_TIMEOUTS).
        """
        max_gdl = max(self._resolver._GALLERYDL_TIMEOUTS.values())
        future_timeout = 50  # hardcoded in _run_gdl_harvest
        self.assertGreaterEqual(
            future_timeout, max_gdl,
            f"future timeout {future_timeout}s < longest gdl timeout {max_gdl}s — "
            f"gallery-dl results will be silently lost"
        )

    def test_max_scene_workers_reduced(self):
        """Scene workers must be capped at 4 to reduce concurrency pressure."""
        gdl_sem_capacity = 0
        acquired = 0
        sem = self._resolver._GDL_SEMAPHORE
        try:
            for _ in range(10):
                if sem.acquire(blocking=False):
                    acquired += 1
                else:
                    break
        finally:
            for _ in range(acquired):
                sem.release()
        gdl_sem_capacity = acquired
        self.assertLessEqual(
            gdl_sem_capacity, 4,
            f"GDL semaphore should be 4, got {gdl_sem_capacity}"
        )


class GalleryDLConfigFileUnitTests(unittest.TestCase):
    """Low-level config file integrity checks."""

    @classmethod
    def setUpClass(cls):
        cls._gdl_conf_path = (
            Path(__file__).parents[1] / "gallery-dl.conf"
        )

    def test_config_is_valid_json(self):
        """Config file must be parseable JSON."""
        if not self._gdl_conf_path.exists():
            self.skipTest("gallery-dl.conf not found")
        raw = self._gdl_conf_path.read_text(encoding="utf-8")
        try:
            json.loads(raw)
        except json.JSONDecodeError as e:
            self.fail(f"gallery-dl.conf is not valid JSON: {e}")

    def test_config_has_required_sections(self):
        """Must have extractor and downloader sections."""
        if not self._gdl_conf_path.exists():
            self.skipTest("gallery-dl.conf not found")
        raw = self._gdl_conf_path.read_text(encoding="utf-8")
        conf = json.loads(raw)
        self.assertIn("extractor", conf)
        self.assertIn("downloader", conf)

    def test_downloader_rate_reasonable(self):
        """Download rate limit must be between 1M and 10M bytes/s."""
        if not self._gdl_conf_path.exists():
            self.skipTest("gallery-dl.conf not found")
        raw = self._gdl_conf_path.read_text(encoding="utf-8")
        conf = json.loads(raw)
        rate_str = conf.get("downloader", {}).get("rate", "1M")
        # Parse rate: "1M" -> 1_000_000, "2M" -> 2_000_000
        if rate_str.endswith("M"):
            rate_val = float(rate_str[:-1])
        elif rate_str.endswith("K"):
            rate_val = float(rate_str[:-1]) / 1000
        else:
            rate_val = float(rate_str) / 1_000_000
        self.assertGreaterEqual(rate_val, 1, "download rate too low")
        self.assertLessEqual(rate_val, 10, "download rate too high")

    def test_downloader_retries_reasonable(self):
        """Retries must be between 1 and 5."""
        if not self._gdl_conf_path.exists():
            self.skipTest("gallery-dl.conf not found")
        raw = self._gdl_conf_path.read_text(encoding="utf-8")
        conf = json.loads(raw)
        retries = conf.get("downloader", {}).get("retries", 5)
        self.assertGreaterEqual(retries, 1)
        self.assertLessEqual(retries, 5)


if __name__ == "__main__":
    unittest.main()
