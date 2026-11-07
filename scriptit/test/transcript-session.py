"""Offline proof of authenticated reference retrieval and subtitle provenance."""
import contextlib
import http.cookiejar
import importlib.util
import io
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('transcript_helper', Path(__file__).parents[1] / 'scripts/yt_transcript.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class TranscriptSessionTests(unittest.TestCase):
    def test_broker_cookies_stay_in_memory(self):
        jar = http.cookiejar.CookieJar()
        jar.set_cookie(http.cookiejar.Cookie(0, 'SID', 'isolated-fixture', None, False, '.youtube.com', True, True, '/', True, True, None, True, None, None, {}, False))
        broker = types.SimpleNamespace(ensure_session=lambda: (jar, 1))
        ydl = types.SimpleNamespace(_opener=object())
        with patch.dict('sys.modules', {'services.youtube_session': broker}):
            helper.attach_saved_session(ydl)
        self.assertEqual([c.name for c in ydl.cookiejar], ['SID'])
        self.assertIsNone(ydl._opener)
        self.assertIsNot(next(iter(ydl.cookiejar)), next(iter(jar)))

    def test_session_failure_stops_before_extraction(self):
        def unavailable():
            raise RuntimeError('Reconnect the selected YouTube session')
        with patch.dict('sys.modules', {'services.youtube_session': types.SimpleNamespace(ensure_session=unavailable)}):
            with self.assertRaisesRegex(RuntimeError, 'Reconnect'):
                helper.attach_saved_session(types.SimpleNamespace())

    def test_auto_captions_are_not_reported_as_manual(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'sub.en.vtt').write_text('WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nReal caption\nReal caption\n', encoding='utf-8')
            self.assertEqual(helper.pick_transcript(directory, set()), ('Real caption', 'auto'))
            self.assertEqual(helper.pick_transcript(directory, {'en'}), ('Real caption', 'manual'))

    def test_main_attaches_session_and_has_bounded_caption_only_options(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'sub.en.vtt').write_text('WEBVTT\n\nObserved narration', encoding='utf-8')
            captured = {}
            class Downloader:
                def __init__(self, options): captured.update(options)
                def __enter__(self): return self
                def __exit__(self, *_): pass
                def extract_info(self, url, download):
                    self_test.assertTrue(getattr(self, 'session_attached', False))
                    return {'title': 'Fixture', 'subtitles': {}, 'automatic_captions': {'en': []}}
            self_test = self
            output = io.StringIO()
            with patch.object(helper.yt_dlp, 'YoutubeDL', Downloader), patch.object(helper, 'attach_saved_session', lambda y: setattr(y, 'session_attached', True)), patch.object(helper.sys, 'argv', ['helper', 'https://youtube.com/watch?v=fixture', directory]), contextlib.redirect_stdout(output):
                helper.main()
            self.assertEqual(json.loads(output.getvalue())['subtitleKind'], 'auto')
            self.assertTrue(captured['skip_download'])
            self.assertFalse(captured['ignoreerrors'])
            self.assertEqual(captured['socket_timeout'], 8)
            self.assertNotIn('cookiefile', captured)


if __name__ == '__main__':
    unittest.main()
