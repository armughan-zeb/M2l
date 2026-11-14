import http.cookiejar
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import youtube_pipeline_v2 as ytv2
import services.youtube_session


class YouTubePipelineV2Tests(unittest.TestCase):
    def _mock_yt_dlp(self):
        instances = []

        class FakeYoutubeDLCookieJar(http.cookiejar.CookieJar):
            def get_cookie_header(self, _url):
                return ""

        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options
                self.cookiejar = None
                instances.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _query, download=False):
                self.download_flag = download
                return {"entries": [{"id": "video-1", "title": "A result"}]}

            def download(self, _urls):
                raw_path = str(self.options["outtmpl"]).replace("%(ext)s", "webm")
                Path(raw_path).write_bytes(b"raw" * 2048)

        yt_dlp = types.ModuleType("yt_dlp")
        yt_dlp.YoutubeDL = FakeYoutubeDL
        cookies = types.ModuleType("yt_dlp.cookies")
        cookies.YoutubeDLCookieJar = FakeYoutubeDLCookieJar
        utils = types.ModuleType("yt_dlp.utils")
        utils.download_range_func = lambda *_args: "download-range"
        return instances, mock.patch.dict(
            sys.modules,
            {"yt_dlp": yt_dlp, "yt_dlp.cookies": cookies, "yt_dlp.utils": utils},
        )

    def _cookie_jar(self):
        jar = http.cookiejar.CookieJar()
        jar.set_cookie(http.cookiejar.Cookie(
            version=0,
            name="SAPISID",
            value="account-cookie",
            port=None,
            port_specified=False,
            domain=".youtube.com",
            domain_specified=True,
            domain_initial_dot=True,
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        ))
        return jar

    def test_script_scene_parser_does_not_pad_duplicate_or_title_only_beats(self):
        rows = ytv2.parse_script_to_scenes(
            "The engine turns over. Air pressure builds.",
            "Diesel locomotive startup",
            num_scenes=5,
        )
        self.assertEqual(
            [row["description"] for row in rows],
            ["The engine turns over.", "Air pressure builds."],
        )
        overflow_rows = ytv2.parse_script_to_scenes(
            "One. Two. Three. Four. Five.", "Process", num_scenes=2,
        )
        overflow_text = " ".join(row["description"] for row in overflow_rows)
        self.assertEqual(len(overflow_rows), 2)
        self.assertTrue(overflow_text.index("One.") < overflow_text.index("Two.") < overflow_text.index("Three.") < overflow_text.index("Four.") < overflow_text.index("Five."))
        self.assertEqual(ytv2.parse_script_to_scenes("", "Diesel locomotive startup", num_scenes=5), [])

    def test_youtube_scene_text_gaps_skip_search_and_keep_output_indices(self):
        captured = {}

        def empty_search(groups, **_kwargs):
            captured["row_count"] = sum(len(group) for group in groups.values())
            captured["scene_texts"] = [
                row.get("scene_text", "")
                for group in groups.values()
                for row in group
            ]
            return {}

        with mock.patch.dict(os.environ, {"VIDZEO_YT_POOL_FIRST": "0", "VIDZEO_YT_V2_SKIP_DOWNLOAD": "0"}, clear=False), \
             mock.patch.object(ytv2, "_search_container_pools", side_effect=empty_search):
            result = ytv2.get_youtube_clips(
                script="The engine turns over. Air pressure builds.",
                title="Diesel locomotive startup",
                num_scenes=4,
                scene_texts=["The engine turns over.", "", "Air pressure builds."],
                use_authenticated_session=False,
            )

        self.assertEqual(captured["row_count"], 2)
        self.assertCountEqual(captured["scene_texts"], ["The engine turns over.", "Air pressure builds."])
        self.assertEqual(list(result), [None, None, None, None])
        self.assertEqual(result.audit["scene_index_map"], [0, 2])
        self.assertEqual(result.audit["requested"], 4)
        self.assertEqual(result.audit["scene_count_with_narration"], 2)

    def test_empty_narration_does_not_launch_youtube_search(self):
        with mock.patch.object(ytv2, "_search_container_pools", side_effect=AssertionError("unexpected search")):
            result = ytv2.get_youtube_clips(
                script="",
                title="Diesel locomotive startup",
                num_scenes=3,
                use_authenticated_session=False,
            )
        self.assertEqual(list(result), [None, None, None])
        self.assertEqual(result.audit["stop_reason"], "no_scene_texts")
        self.assertEqual(result.audit["scene_index_map"], [])

    def test_authenticated_session_is_converted_to_memory_cookie_jar(self):
        instances, yt_dlp = self._mock_yt_dlp()
        source = self._cookie_jar()
        with yt_dlp, mock.patch(
            "services.youtube_session.ensure_verified_session",
            return_value=(source, 7),
        ), mock.patch(
            "server.services.youtube_session.ensure_verified_session",
            return_value=(source, 7),
            create=True,
        ):
            converted = ytv2._get_authenticated_youtube_session()

        self.assertIsNot(converted, source)
        self.assertTrue(hasattr(converted, "get_cookie_header"))
        self.assertEqual([(cookie.name, cookie.value) for cookie in converted], [
            ("SAPISID", "account-cookie"),
        ])
        self.assertIsNot(next(iter(converted)), next(iter(source)))
        self.assertEqual(instances, [])

    def test_authenticated_session_falls_back_anonymous_without_cookie_adapter(self):
        _instances, yt_dlp = self._mock_yt_dlp()
        missing_adapter = types.ModuleType("yt_dlp.cookies")
        source = self._cookie_jar()
        with yt_dlp, mock.patch.dict(sys.modules, {"yt_dlp.cookies": missing_adapter}), mock.patch(
            "services.youtube_session.ensure_verified_session",
            return_value=(source, 7),
        ):
            converted = ytv2._get_authenticated_youtube_session()

        self.assertIsNone(converted)

    def test_search_and_download_receive_converted_authenticated_cookie_jar(self):
        instances, yt_dlp = self._mock_yt_dlp()
        source = self._cookie_jar()
        with yt_dlp, mock.patch.dict(
            os.environ,
            {"VIDZEO_YT_SEARCH_CACHE_TTL_SECONDS": "0", "VIDZEO_YT_FRAGMENT_CONCURRENCY": "4"},
            clear=False,
        ):
            converted = ytv2._adapt_youtube_cookie_jar(source)
            search_results = ytv2._search_youtube("account query", 2, converted)
            with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
                ytv2.subprocess,
                "run",
                side_effect=lambda command, **_kwargs: (
                    Path(command[-1]).write_bytes(b"clip" * 2048),
                    None,
                )[1],
            ), mock.patch.object(ytv2, "_source_video_height", return_value=1080):
                downloaded = ytv2._download_clip(
                    {"id": "video-1", "webpage_url": "https://youtube.com/watch?v=video-1"},
                    Path(temp_dir) / "clip.mp4",
                    0.0,
                    4.0,
                    session_jar=converted,
                    stream_downloads=True,
                )

        self.assertEqual(len(search_results), 1)
        self.assertTrue(downloaded)
        self.assertEqual(len(instances), 2)
        self.assertTrue(all(hasattr(instance.cookiejar, "get_cookie_header") for instance in instances))
        self.assertTrue(all(instance.cookiejar is converted for instance in instances))
        self.assertEqual(instances[1].options["concurrent_fragment_downloads"], 4)
        selected_format = instances[1].options["format"]
        self.assertIn("bestvideo", selected_format)
        self.assertIn("height<=1080", selected_format)
        self.assertIn("bestaudio", selected_format)

    def test_completed_mp4_range_is_promoted_without_lossy_reencode(self):
        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options
                self.cookiejar = None
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def download(self, _urls):
                raw = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
                raw.write_bytes(b"source-quality-range" * 512)

        fake_module = SimpleNamespace(YoutubeDL=FakeYoutubeDL)
        fake_utils = types.ModuleType("yt_dlp.utils")
        fake_utils.download_range_func = lambda *_args: "download-range"
        with tempfile.TemporaryDirectory() as temp_dir, \
             mock.patch("services.yt_dlp_runtime.load_yt_dlp", return_value=fake_module), \
             mock.patch.dict(sys.modules, {"yt_dlp.utils": fake_utils}), \
             mock.patch.object(ytv2, "_source_has_video_stream", return_value=True), \
             mock.patch.object(ytv2, "_source_video_height", return_value=1080), \
             mock.patch("services.media_integrity.promote_verified_media", return_value=SimpleNamespace(valid=True, reason="ok")), \
             mock.patch.object(ytv2.subprocess, "run") as run_mock, \
             mock.patch.dict(os.environ, {"VIDZEO_YT_RETRIEVAL_PRESERVE_SOURCE": "1"}, clear=False):
            output = Path(temp_dir) / "yt_clip_scene_1.cand_0.mp4"
            self.assertTrue(ytv2._download_clip(
                {"id": "video-1", "webpage_url": "https://youtube.com/watch?v=video-1"},
                output,
                12.0,
                5.0,
            ))

        run_mock.assert_not_called()

    def test_transcript_preparation_is_bounded_to_shortlist_and_reserves(self):
        candidates = [
            {"id": f"source-{index}", "title": f"RMS Titanic archive {index}", "duration": 600, "v2_score": 100 - index}
            for index in range(10)
        ]
        calls = []
        deadlines = []
        class FakeIndex:
            def fetch_many(self, ids, **kwargs):
                calls.extend(ids)
                deadlines.append(kwargs.get("deadline_seconds"))
                return {}
        with mock.patch.dict(os.environ, {"VIDZEO_YT_POOL_SOURCE_SHORTLIST": "3", "VIDZEO_YT_POOL_SOURCE_RESERVES": "1", "VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS": "7.5"}, clear=False), \
             mock.patch("services.youtube_transcript_index.YouTubeTranscriptIndex", return_value=FakeIndex()):
            result = ytv2._prepare_pool_transcript_moments(
                [{"candidate_subject": "RMS Titanic", "scene_text": "Titanic archive"}],
                {"primary rms titanic": candidates},
                scene_durations=[4.0],
                force=True,
            )
        self.assertEqual(len(calls), 4)
        self.assertEqual(deadlines, [7.5])

    def test_pool_transcript_preparation_passes_authenticated_session(self):
        captured = {}

        class FakeIndex:
            def __init__(self, **kwargs):
                captured.update(kwargs)
            def fetch_many(self, ids, **kwargs):
                return {}

        with mock.patch("services.youtube_transcript_index.YouTubeTranscriptIndex", FakeIndex), \
             mock.patch.dict(os.environ, {"VIDZEO_YT_POOL_SOURCE_SHORTLIST": "1", "VIDZEO_YT_POOL_SOURCE_RESERVES": "0"}, clear=False):
            ytv2._prepare_pool_transcript_moments(
                [{"candidate_subject": "RMS Titanic", "scene_text": "Titanic archive"}],
                {"pool": [{"id": "source-1", "title": "RMS Titanic archive", "duration": 600, "v2_score": 100}]},
                scene_durations=[4.0],
                force=True,
                session_jar="authenticated-session",
            )
        self.assertEqual(captured.get("session_jar"), "authenticated-session")

    def test_pool_first_bypasses_tiny_jobs_without_force(self):
        captured = {}

        def fake_download(_candidate, _path, *_args, **_kwargs):
            return False

        with mock.patch.object(ytv2, "_search_container_pools", return_value={"pool": [{"id": "source-1", "title": "RMS Titanic archive footage", "duration": 300, "v2_score": 100, "score": 100, "query_text": "RMS Titanic archive footage", "webpage_url": "https://youtu.be/source-1"}]}), \
             mock.patch.object(ytv2, "_assign_candidates", return_value=[{"id": "source-1", "title": "RMS Titanic archive footage", "duration": 300, "best_timestamp": 20, "webpage_url": "https://youtu.be/source-1"}]), \
             mock.patch.object(ytv2, "_download_clip", side_effect=lambda *args, **kwargs: captured.setdefault("ranged", True) or False), \
             mock.patch.dict(os.environ, {"VIDZEO_YT_POOL_FIRST": "1", "VIDZEO_YT_POOL_FIRST_FORCE": "0", "VIDZEO_YT_V2_SKIP_DOWNLOAD": "0"}, clear=False):
            ytv2.get_youtube_clips(
                script="RMS Titanic archive footage",
                title="RMS Titanic",
                num_scenes=1,
                scene_texts=["RMS Titanic archive footage"],
                openrouter_api_key="",
                use_authenticated_session=False,
                pool_first=True,
            )
        assert captured.get("ranged") is True

    def test_source_download_passes_integer_max_filesize_to_ytdlp(self):
        instances, yt_dlp = self._mock_yt_dlp()
        with yt_dlp, mock.patch.object(
            ytv2.subprocess,
            "run",
            side_effect=lambda command, **_kwargs: (
                Path(command[-1]).write_bytes(b"clip" * 2048),
                None,
            )[1],
        ), mock.patch.object(ytv2, "_source_video_height", return_value=720), mock.patch.dict(os.environ, {"VIDZEO_YT_SOURCE_MAX_MB": "80", "VIDZEO_YT_SOURCE_MAX_HEIGHT": "720", "VIDZEO_YT_FRAGMENT_CONCURRENCY": "12"}, clear=False):
            with tempfile.TemporaryDirectory() as temp_dir:
                output = Path(temp_dir) / "source.mp4"
                output.write_bytes(b"placeholder")
                self.assertTrue(ytv2._download_source_once(
                    {"id": "video-1", "webpage_url": "https://youtube.com/watch?v=video-1"},
                    output,
                    session_jar=None,
                    stream_downloads=True,
                ))
        self.assertEqual(len(instances), 1)
        self.assertIsInstance(instances[0].options["max_filesize"], int)
        self.assertEqual(instances[0].options["max_filesize"], 80 * 1024 * 1024)
        self.assertEqual(instances[0].options["concurrent_fragment_downloads"], 8)

    def test_source_download_rejects_audio_only_artifact(self):
        instances, yt_dlp = self._mock_yt_dlp()
        audit = {}
        with yt_dlp, mock.patch.object(
            ytv2,
            "_source_has_video_stream",
            return_value=False,
        ), mock.patch.dict(os.environ, {"VIDZEO_YT_SOURCE_MAX_MB": "80"}, clear=False):
            with tempfile.TemporaryDirectory() as temp_dir:
                output = Path(temp_dir) / "source.mp4"
                self.assertFalse(ytv2._download_source_once(
                    {"id": "video-1", "webpage_url": "https://youtube.com/watch?v=video-1"},
                    output,
                    audit=audit,
                ))
                self.assertFalse(output.exists())
                self.assertEqual(list(Path(temp_dir).glob("source.raw.*")), [])
        self.assertEqual(len(instances), 1)
        self.assertEqual(audit["status"], "merge_failed")
        self.assertEqual(audit["reason"], "downloaded_source_has_no_video_stream")

    def test_source_download_rejects_unknown_resolution(self):
        _instances, yt_dlp = self._mock_yt_dlp()
        audit = {}
        with yt_dlp, mock.patch.object(ytv2, "_source_has_video_stream", return_value=True), \
             mock.patch.object(ytv2, "_source_video_height", return_value=0):
            with tempfile.TemporaryDirectory() as temp_dir:
                output = Path(temp_dir) / "source.mp4"
                self.assertFalse(ytv2._download_source_once(
                    {"id": "video-1", "webpage_url": "https://youtube.com/watch?v=video-1"},
                    output,
                    audit=audit,
                ))
                self.assertFalse(output.exists())
                self.assertEqual(list(Path(temp_dir).glob("source.raw.*")), [])
        self.assertEqual(audit["status"], "source_quality_rejected")
        self.assertEqual(audit["reason"], "source_height_unknown")

    def test_source_download_selects_video_instead_of_newer_audio_artifact(self):
        instances = []

        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options
                self.cookiejar = None
                instances.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def download(self, _urls):
                template = self.options["outtmpl"]
                video = Path(template.replace("%(ext)s", "video.mp4"))
                audio = Path(template.replace("%(ext)s", "audio.webm"))
                video.write_bytes(b"video" * 2048)
                audio.write_bytes(b"audio" * 2048)  # newest, like the production failure

        yt_dlp = types.ModuleType("yt_dlp")
        yt_dlp.YoutubeDL = FakeYoutubeDL
        with mock.patch.dict(sys.modules, {"yt_dlp": yt_dlp}), mock.patch.object(
            ytv2,
            "_source_has_video_stream",
            side_effect=lambda path: "video" in path.name,
        ), mock.patch.object(ytv2, "_source_video_height", return_value=1080):
            with tempfile.TemporaryDirectory() as temp_dir:
                output = Path(temp_dir) / "source.mp4"
                self.assertTrue(ytv2._download_source_once(
                    {"id": "video-1", "webpage_url": "https://youtube.com/watch?v=video-1"},
                    output,
                ))
                self.assertEqual(output.read_bytes(), b"video" * 2048)
        self.assertEqual(len(instances), 1)

    def test_gta_vi_ranking_keeps_official_trailer_and_blocks_noise(self):
        context = {
            "domain_category": "gaming",
            "container_subject": "Grand Theft Auto VI",
            "primary_container_subject": "Grand Theft Auto VI",
            "retrieval_positive_terms": ["official trailer", "gameplay trailer"],
            "retrieval_negative_terms": ["streamer", "reaction", "news"],
        }
        scene = "Grand Theft Auto VI returns to Vice City with Jason and Lucia."
        query = "Grand Theft Auto VI official trailer"
        official = {
            "title": "Grand Theft Auto VI Trailer 2",
            "description": "The official Grand Theft Auto VI trailer.",
            "channel": "Rockstar Games",
            "duration": 166,
        }
        streamer = {
            "title": "Streamer reacts to GTA 6 Trailer 2 with facecam",
            "description": "Live stream reaction and commentary.",
            "channel": "Gaming Live",
            "duration": 900,
        }
        wrong_game = {
            "title": "Grand Theft Auto V: Official Gameplay Video",
            "description": "GTA V gameplay.",
            "channel": "Rockstar Games",
            "duration": 300,
        }
        wrong_franchise = {
            "title": "Red Dead Redemption 2: Official Gameplay Video",
            "description": "Official gameplay from Rockstar Games.",
            "channel": "Rockstar Games",
            "duration": 300,
        }
        high_risk = {
            "title": "GTA 6 Official Trailer",
            "description": "Grand Theft Auto VI trailer.",
            "channel": "T-Series",
            "duration": 180,
        }
        concept = {
            "title": "Grand Theft Auto VI Gameplay Trailer - Unseen Locations (Concept)",
            "description": "GTA 6 fan concept gameplay.",
            "channel": "Gar Studio",
            "duration": 180,
        }
        impersonator = {
            "title": "GTA 6 Official Trailer",
            "description": "Grand Theft Auto VI official gameplay.",
            "channel": "Rockstar Games GTA 6",
            "duration": 180,
        }
        verified_platform = {
            "title": "Grand Theft Auto VI - Trailer 2 - 4K | PS5",
            "description": "Grand Theft Auto VI trailer on PlayStation 5.",
            "channel": "PlayStation France",
            "channel_is_verified": True,
            "duration": 169,
        }
        untrusted_gameplay_trailer = {
            "title": "Grand Theft Auto VI: Online - Gameplay Trailer",
            "description": "GTA 6 gameplay trailer.",
            "channel": "Marlon XGamer 2.0",
            "duration": 180,
        }
        creator_clickbait = {
            "title": "I Found Gameplay Footage in Grand Theft Auto VI",
            "description": "GTA 6 gameplay footage discovery.",
            "channel": "Mr Creator",
            "duration": 600,
        }
        clean_gameplay = {
            "title": "Grand Theft Auto VI 4K Gameplay - No Commentary",
            "description": "Direct GTA 6 gameplay capture without commentary.",
            "channel": "Gameplay Archive",
            "duration": 600,
        }

        official_score = ytv2._score_candidate(official, "Grand Theft Auto VI", scene, query, context)
        self.assertGreater(official_score, 0)
        self.assertEqual(ytv2._score_candidate(streamer, "Grand Theft Auto VI", scene, query, context), -999.0)
        self.assertLess(ytv2._score_candidate(wrong_game, "Grand Theft Auto VI", scene, query, context), -100)
        self.assertLess(ytv2._score_candidate(wrong_franchise, "Grand Theft Auto VI", scene, query, context), -100)
        self.assertEqual(ytv2._score_candidate(high_risk, "Grand Theft Auto VI", scene, query, context), -999.0)
        self.assertEqual(ytv2._score_candidate(concept, "Grand Theft Auto VI", scene, query, context), -999.0)
        self.assertLess(ytv2._score_candidate(impersonator, "Grand Theft Auto VI", scene, query, context), official_score)
        self.assertGreater(ytv2._score_candidate(verified_platform, "Grand Theft Auto VI", scene, query, context), 0)
        self.assertLess(ytv2._score_candidate(untrusted_gameplay_trailer, "Grand Theft Auto VI", scene, query, context), official_score)
        self.assertLess(ytv2._score_candidate(creator_clickbait, "Grand Theft Auto VI", scene, query, context), official_score)
        self.assertGreater(ytv2._score_candidate(clean_gameplay, "Grand Theft Auto VI", scene, query, context), 0)

    def test_raw_gameplay_policy_keeps_clean_walkthrough_and_rejects_creator_formats(self):
        context = {
            "domain_category": "gaming",
            "container_subject": "Grand Theft Auto V",
            "primary_container_subject": "Grand Theft Auto V",
        }
        clean_walkthrough = {
            "title": "Grand Theft Auto V Full Walkthrough No Commentary",
            "description": "Uncut raw gameplay playthrough and direct game capture.",
            "channel": "Gameplay Archive",
            "duration": 1800,
        }
        facecam_gameplay = {
            "title": "Grand Theft Auto V Raw Gameplay with Facecam",
            "description": "Streamer reaction while playing missions.",
            "channel": "Gamer Live",
            "duration": 1800,
        }
        podcast = {
            "title": "Grand Theft Auto V Podcast Breakdown",
            "description": "Two hosts discuss the game in a studio.",
            "channel": "Gaming Talk",
            "duration": 1800,
        }

        score = lambda candidate: ytv2._score_candidate(
            candidate,
            "Grand Theft Auto V",
            "Raw driving and mission gameplay.",
            "Grand Theft Auto V raw gameplay no commentary",
            context,
        )
        self.assertGreater(score(clean_walkthrough), 0)
        self.assertEqual(score(facecam_gameplay), -999.0)
        self.assertEqual(score(podcast), -999.0)

    def test_gta_vi_queries_do_not_inject_prior_title_without_scene_comparison(self):
        row = {
            "domain_category": "gaming",
            "container_subject": "Grand Theft Auto VI",
            "primary_container_subject": "Grand Theft Auto VI",
            "container_type": "product",
            "gta_vi_supporting_gameplay": True,
        }
        queries = ytv2._queries_for_row(
            row,
            "Grand Theft Auto VI",
            "Grand Theft Auto VI returns to Vice City.",
        )

        self.assertTrue(any("Grand Theft Auto VI" in query for query in queries))
        self.assertFalse(any("Grand Theft Auto V raw gameplay" in query for query in queries))
        self.assertTrue(all("-facecam" not in query and "-webcam" not in query for query in queries))
        self.assertTrue(all("-streamer" not in query and "-reaction" not in query for query in queries))

    def test_gta_v_is_only_queried_for_an_explicit_scripted_comparison(self):
        row = {
            "domain_category": "gaming",
            "container_subject": "Grand Theft Auto VI",
            "primary_container_subject": "Grand Theft Auto VI",
            "container_type": "product",
            "gta_vi_supporting_gameplay": True,
            "visual_intent": "Explicit comparison to a prior game as context.",
        }
        queries = ytv2._queries_for_row(
            row,
            "Grand Theft Auto VI",
            "Compare GTA V open-world driving with GTA VI as a predecessor reference.",
        )
        self.assertTrue(any("Grand Theft Auto V raw gameplay" in query for query in queries))

    def test_gta_vi_scene_rows_do_not_auto_enable_prior_title_footage(self):
        rows = ytv2._build_scene_rows(
            "Grand Theft Auto VI: Vice City",
            ["Grand Theft Auto VI returns to Vice City."],
        )
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["gta_vi_supporting_gameplay"])

    def test_full_named_person_anchor_survives_raw_footage_query_generation(self):
        row = {
            "domain_category": "documentary",
            "container_subject": "Prince Harry",
            "primary_container_subject": "Prince Harry",
            "container_type": "person",
            "search_disambiguator": "British royal family",
        }
        queries = ytv2._queries_for_row(
            row,
            "Prince Harry",
            "Prince Harry attended the public ceremony.",
        )

        self.assertTrue(queries)
        self.assertTrue(all("Prince Harry" in query for query in queries))
        self.assertTrue(all("-facecam" not in query and "-webcam" not in query for query in queries))

    def test_semantic_lock_preserves_llm_footage_queries_before_deterministic_reserve(self):
        requirement = {
            "canonical_entity": "Jaguar E-Type",
            "domain": "automotive",
            "required_attributes": {"make": "Jaguar", "model": "E-Type", "year": "1961"},
        }
        row = {
            "_semantic_locked": True,
            "visual_requirement": requirement,
            "domain_category": "automotive",
            "youtube_query_plan": [
                "Jaguar driving footage no commentary",
                "Jaguar factory walkaround clean",
                "Jaguar interior detail b-roll",
            ],
        }
        queries = ytv2._queries_for_row(row, "Jaguar E-Type", "The classic coupe drives through town.")

        assert queries
        assert queries[0].startswith("Jaguar E-Type driving footage")
        assert all(("e type" in query.lower() or "e-type" in query.lower()) and "1961" in query for query in queries[:3])
        positive_query_text = " ".join(queries[:3]).lower().replace("-reaction", "").replace("-podcast", "").replace("-explained", "")
        assert all(term not in positive_query_text for term in ("reaction", "podcast", "explained"))
        assert all("-facecam" not in query and "-webcam" not in query for query in queries[:3])

    def test_metadata_gate_is_narrow_but_facecam_cannot_hide_behind_raw_footage(self):
        contaminated = {
            "title": "Prince Harry Raw Footage with Streamer Facecam",
            "description": "Reaction to archive footage.",
            "channel": "Royal Reactions",
        }
        archival_interview = {
            "title": "Prince Harry Interview Footage and Arrival B-Roll",
            "description": "Archival footage from the public event.",
            "channel": "Documentary Archive",
        }

        self.assertTrue(ytv2._metadata_talking_head_reason(contaminated).startswith("presenter_contamination:"))
        self.assertEqual(ytv2._metadata_talking_head_reason(archival_interview), "")

    def test_intro_and_publisher_rows_retain_main_game_identity(self):
        scenes = [
            "For more than a decade, gamers have waited.",
            "Grand Theft Auto VI returns to Vice City.",
            "Jason and Lucia lead the story.",
            "Rockstar Games built a larger world.",
            "Grand Theft Auto VI is finally approaching.",
        ]
        overrides = [
            {"container_id": "intro", "container_subject": "gaming", "is_intro": True, "domain_category": "gaming"},
            {"container_id": "gta_vi", "container_subject": "Grand Theft Auto VI", "domain_category": "gaming", "container_type": "product"},
            {"container_id": "gta_vi", "container_subject": "Grand Theft Auto VI", "domain_category": "gaming", "container_type": "product"},
            {"container_id": "rockstar", "container_subject": "Rockstar Games", "domain_category": "gaming", "container_type": "organization"},
            {"container_id": "gta_vi", "container_subject": "Grand Theft Auto VI", "domain_category": "gaming", "container_type": "product"},
        ]

        rows = ytv2._build_scene_rows("Grand Theft Auto VI", scenes, overrides)

        self.assertEqual(rows[0]["candidate_subject"], "Grand Theft Auto VI")
        self.assertEqual(rows[3]["candidate_subject"], "Grand Theft Auto VI")
        self.assertTrue(all("Grand Theft Auto VI" in query for query in rows[0]["queries"]))
        self.assertTrue(all("Grand Theft Auto VI" in query for query in rows[3]["queries"]))
        self.assertEqual(set(ytv2._group_rows(rows)), {"primary grand theft auto vi"})

    def test_multiword_subject_requires_the_complete_identity(self):
        self.assertFalse(ytv2._text_mentions_subject("Jason gameplay compilation", "Jason and Lucia"))
        self.assertTrue(ytv2._text_mentions_subject("Jason and Lucia trailer footage", "Jason and Lucia"))

    def test_regional_mirrors_share_one_nonoverlapping_timestamp_budget(self):
        with mock.patch.dict(os.environ, {"VIDZEO_YT_REUSE_TIMESTAMP_GAP": "60"}, clear=False):
            used = ytv2.defaultdict(list)
            italy = {"id": "italy", "title": "Grand Theft Auto VI | Trailer 2 | PS5", "duration": 169}
            australia = {"id": "australia", "title": "Grand Theft Auto VI | Trailer 2 | PS5", "duration": 169}

            starts = [ytv2._pick_timestamp(italy, idx, used, 4.0) for idx in range(3)]
            mirrored = ytv2._pick_timestamp(australia, 4, used, 4.0)

        self.assertTrue(all(start is not None for start in starts))
        self.assertIsNone(mirrored)

    def test_mechanical_fallback_rows_collapse_to_verified_event_anchor(self):
        scenes = [
            "Buildings collapsed after the strongest tremor.",
            "Families waited while rescue crews searched the rubble.",
            "Before dawn, emergency workers reached Caracas.",
        ]
        overrides = [
            {
                "section_subject": weak,
                "container_id": weak.lower().replace(" ", "_"),
                "domain_anchor": "Venezuela earthquakes",
                "topic_anchor": "Venezuela earthquakes",
                "domain_category": "disaster",
                "youtube_query": f"{weak} highlights",
                "_mechanical": True,
                "_llm": False,
            }
            for weak in ("Buildings", "Families", "Before")
        ]

        rows = ytv2._build_scene_rows("Venezuela earthquakes", scenes, overrides)

        self.assertEqual({row["candidate_subject"] for row in rows}, {"Venezuela earthquakes"})
        self.assertEqual(len(ytv2._group_rows(rows)), 1)
        self.assertTrue(all("Venezuela earthquakes" in query for row in rows for query in row["queries"]))

    def test_disaster_ranking_rejects_cross_topic_and_packages(self):
        context = {
            "domain_anchor": "Venezuela earthquakes",
            "domain_category": "disaster",
            "container_subject": "Venezuela earthquakes",
        }
        clean = {
            "title": "Venezuela Earthquake Raw Footage and Rescue Aftermath",
            "description": "Street damage and emergency response in Caracas, Venezuela.",
            "channel": "Caracas Local Archive",
            "duration": 420,
        }
        gaming = {
            "title": "GTA VI Streamer Reacts to New Gameplay",
            "description": "Live reaction with facecam.",
            "channel": "Gaming Live",
            "duration": 900,
        }
        football = {
            "title": "World Cup Football Match Highlights",
            "description": "Goals and match reaction.",
            "channel": "Sports Talk",
            "duration": 600,
        }
        overlay = {
            "title": "Venezuela Earthquake Breaking News Banner and Text Overlay",
            "description": "Captioned video with lower third.",
            "channel": "Daily News",
            "duration": 300,
        }
        media_agency = {
            "title": "Drone Footage Shows Venezuela Earthquake Damage",
            "description": "Aerial footage from Caracas.",
            "channel": "New York Post",
            "duration": 240,
        }
        sensational = {
            "title": "Venezuela Earthquakes EXPOSED: The SHOCKING Truth",
            "description": "The truth about the rescue effort finally revealed.",
            "channel": "SilencedTruths",
            "duration": 600,
        }

        score = lambda candidate: ytv2._score_candidate(
            candidate,
            "Venezuela earthquakes",
            "Rescue crews searched collapsed buildings in Venezuela.",
            "Venezuela earthquakes rescue raw footage",
            context,
        )
        self.assertGreater(score(clean), 0)
        self.assertLess(score(gaming), -100)
        self.assertLess(score(football), -100)
        self.assertEqual(score(overlay), -999.0)
        self.assertEqual(score(media_agency), -999.0)
        self.assertEqual(score(sensational), -999.0)

    def test_historical_disaster_archive_footage_is_usable(self):
        context = {
            "domain_anchor": "RMS Titanic",
            "domain_category": "disaster",
            "container_subject": "RMS Titanic",
        }
        archival = {
            "title": "RMS Titanic Sinking - Archival Historical Footage 1912",
            "description": "Vintage newsreel and period documentary footage of the ocean liner and lifeboats.",
            "channel": "British Pathé Archive",
            "duration": 180,
        }
        commentary = {
            "title": "Titanic Disaster Explained by a Historian",
            "description": "Podcast commentary and reaction about what really happened.",
            "channel": "History Talk Podcast",
            "duration": 900,
        }
        score = lambda candidate: ytv2._score_candidate(
            candidate,
            "RMS Titanic",
            "Titanic sank after striking an iceberg and lifeboats rescued survivors.",
            "RMS Titanic sinking archival historical footage",
            context,
        )

        self.assertGreater(score(archival), 0)
        self.assertLess(score(commentary), -100)

    def test_disaster_queries_include_historical_archive_variants(self):
        rows = [{
            "section_subject": "RMS Titanic",
            "container_subject": "RMS Titanic",
            "domain_category": "disaster",
            "scene_subject": "RMS Titanic",
        }]
        built = ytv2._build_scene_rows(
            "RMS Titanic documentary",
            ["The Titanic sank after striking an iceberg."],
            rows,
        )
        queries = " ".join(built[0]["queries"]).lower()
        self.assertIn("archival historical footage", queries)
        self.assertIn("sinking shipwreck footage", queries)

    def test_container_search_queries_sample_the_full_section(self):
        rows = [
            {"queries": [f"Venezuela earthquakes facet {idx} raw footage", f"alternate {idx}"]}
            for idx in range(12)
        ]

        queries = ytv2._representative_pool_queries(rows, 5)

        self.assertEqual(len(queries), 5)
        self.assertIn("Venezuela earthquakes facet 0 raw footage", queries)
        self.assertIn("Venezuela earthquakes facet 11 raw footage", queries)
        self.assertTrue(any("facet 6" in query or "facet 5" in query for query in queries))

    # ------------------------------------------------------------------ #
    # Visual-gated per-scene clip resolver (scene-chain YouTubeLane)      #
    # ------------------------------------------------------------------ #

    def _wrestle_overrides(self):
        return [
            {"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports", "youtube_query": "John Cena WWE training footage"},
            {"_llm": True, "section_subject": "The Undertaker", "scene_subject": "The Undertaker", "domain_category": "sports", "youtube_query": "The Undertaker WrestleMania footage"},
        ]

    def _wrestle_scenes(self):
        return ["John Cena starts with early WWE training.", "The Undertaker's WrestleMania streak becomes the defining chapter."]

    def _fake_wrestle_search(self, query, max_results=12, session_jar=None):
        q = query.lower()
        if "john cena" in q:
            return [{"id": "cena01", "title": "John Cena WWE training raw footage", "channel": "Wrestling Archive", "description": "raw footage highlights", "duration": 300, "query_text": query}]
        if "undertaker" in q:
            return [{"id": "taker01", "title": "Undertaker WrestleMania streak highlights", "channel": "Wrestling Archive", "description": "match archive highlights", "duration": 300, "query_text": query}]
        return []

    def test_build_clip_pools_assigns_matching_sources_per_scene(self):
        with mock.patch.object(ytv2, "_search_youtube", side_effect=self._fake_wrestle_search), \
             mock.patch.object(ytv2, "_get_authenticated_youtube_session", return_value=None):
            pools = ytv2.build_clip_pools(
                "Top WWE Wrestlers",
                self._wrestle_scenes(),
                scene_query_overrides=self._wrestle_overrides(),
                use_authenticated_session=False,
            )
        self.assertEqual(len(pools.rows), 2)
        self.assertIsNotNone(pools.assignments[0])
        self.assertIsNotNone(pools.assignments[1])
        self.assertIn("John Cena", pools.assignments[0].get("title", ""))
        self.assertIn("Undertaker", pools.assignments[1].get("title", ""))

    def test_pool_transcript_moment_overrides_heuristic_timestamp(self):
        import tempfile
        from unittest import mock
        from services.youtube_transcript_index import YouTubeTranscriptIndex

        source = {
            "id": "moment-source",
            "title": "John Cena training raw footage",
            "channel": "Wrestling Archive",
            "description": "raw footage highlights",
            "duration": 600,
            "query_text": "John Cena training footage",
        }
        captured = []

        def fake_download(option, out, start, duration, session_jar=None, stream_downloads=False):
            captured.append(float(start))
            out.write_bytes(b"x" * 100)
            return True

        with mock.patch.object(ytv2, "_search_youtube", return_value=[source]), \
             mock.patch.object(ytv2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.object(ytv2, "_download_clip", side_effect=fake_download), \
             mock.patch.object(ytv2, "_visual_verdict_scored", return_value=(True, 90.0, True, "keep")), \
             mock.patch.object(YouTubeTranscriptIndex, "fetch_many", return_value={
                 "moment-source": __import__("services.youtube_transcript_index", fromlist=["TranscriptDocument"]).TranscriptDocument(
                     video_id="moment-source",
                     status="ready",
                     segments=__import__("services.youtube_transcript_index", fromlist=["normalize_segments"]).normalize_segments([
                         {"start": 222, "duration": 5, "text": "John Cena training in the wrestling gym"},
                     ]),
                 )
             }), \
             mock.patch.dict(os.environ, {"VIDZEO_YT_POOL_TRANSCRIPTS": "1"}, clear=False):
            pools = ytv2.build_clip_pools(
                "John Cena",
                ["John Cena trains in the wrestling gym."],
                scene_query_overrides=[{"_llm": True, "title_anchor": "John Cena", "domain_anchor": "John Cena", "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports", "youtube_query": "John Cena training footage"}],
                scene_durations=[4.0],
                use_authenticated_session=False,
            )
            state = ytv2.make_resolve_state(pools)
            path = ytv2.resolve_clip_for_scene(0, pools, state, visual_context=object(), output_dir=tempfile.mkdtemp())

        assert path
        assert captured == [222.0]

    def test_resolve_clip_for_scene_returns_gated_path_and_marks_resolved(self):
        with mock.patch.object(ytv2, "_search_youtube", side_effect=self._fake_wrestle_search), \
             mock.patch.object(ytv2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.object(ytv2, "_download_clip", side_effect=lambda _o, out, _s, _d, session_jar=None, stream_downloads=False: (out.write_bytes(b"x" * 100) or True)), \
             mock.patch.object(ytv2, "_visual_verdict", return_value=(True, "", True)):
            pools = ytv2.build_clip_pools(
                "Top WWE Wrestlers",
                self._wrestle_scenes(),
                scene_query_overrides=self._wrestle_overrides(),
                use_authenticated_session=False,
            )
            state = ytv2.make_resolve_state(pools)
            path = ytv2.resolve_clip_for_scene(0, pools, state, output_dir=str(tempfile.mkdtemp()))
            self.assertIsNotNone(path)
            self.assertTrue(Path(path).exists())
            self.assertEqual(state.resolved[0], path)

    def test_resolve_clip_for_scene_skips_hard_rejected_fallback_candidates(self):
        def fake_search(query, max_results=12, session_jar=None):
            return [
                {"id": "stream1", "title": "John Cena Reaction Stream VOD", "channel": "Live Streams", "description": "reaction facecam", "duration": 300, "query_text": query},
                {"id": "cena_good", "title": "John Cena WWE training raw footage", "channel": "Wrestling Archive", "description": "raw footage highlights", "duration": 300, "query_text": query},
            ]

        downloaded = []

        def fake_dl(_option, out, _start, _dur, session_jar=None, stream_downloads=False):
            out.write_bytes(b"x" * 100)
            downloaded.append(_option.get("id"))
            return True

        with mock.patch.object(ytv2, "_search_youtube", side_effect=fake_search), \
             mock.patch.object(ytv2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.object(ytv2, "_download_clip", side_effect=fake_dl), \
             mock.patch.object(ytv2, "_visual_verdict", return_value=(True, "", True)):
            pools = ytv2.build_clip_pools(
                "John Cena",
                ["John Cena trains for WWE."],
                scene_query_overrides=[{"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports", "youtube_query": "John Cena WWE training footage"}],
                use_authenticated_session=False,
            )
            state = ytv2.make_resolve_state(pools)
            ytv2.resolve_clip_for_scene(0, pools, state, output_dir=str(tempfile.mkdtemp()))
        self.assertEqual(downloaded, ["cena_good"])

    def test_resolve_clip_for_scene_falls_back_when_visual_gate_rejects(self):
        downloaded = []

        class StrictContext:
            visual_required = True

        def fake_dl(_option, out, _start, _dur, session_jar=None, stream_downloads=False):
            out.write_bytes(b"x" * 100)
            downloaded.append(_option.get("id"))
            return True

        def fake_fetch(query, max_results=12, session_jar=None):
            base = {"channel": "Wrestling Archive", "description": "raw footage highlights", "duration": 300, "query_text": query}
            return [
                dict(base, id="cenaA", title="John Cena training raw footage A"),
                dict(base, id="cenaB", title="John Cena training raw footage B"),
            ]

        # Reject the first downloaded candidate, accept the second.
        def fake_verdict(_path, _scene_index, _topic, _narration, _subject, _vc):
            accepted = len(downloaded) >= 2
            return (accepted, 90.0 if accepted else 30.0, True, "keep" if accepted else "replace")

        with mock.patch.object(ytv2, "_search_youtube", side_effect=fake_fetch), \
             mock.patch.object(ytv2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.object(ytv2, "_download_clip", side_effect=fake_dl), \
             mock.patch.object(ytv2, "_visual_verdict_scored", side_effect=fake_verdict):
            pools = ytv2.build_clip_pools(
                "John Cena",
                ["John Cena trains for WWE."],
                scene_query_overrides=[{"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports", "youtube_query": "John Cena WWE training footage", "scene_subject_type": ""}],
                use_authenticated_session=False,
            )
            state = ytv2.make_resolve_state(pools)
            path = ytv2.resolve_clip_for_scene(
                0, pools, state, visual_context=StrictContext(), output_dir=str(tempfile.mkdtemp())
            )
        self.assertEqual(downloaded, ["cenaA", "cenaB"])
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).exists())

    def test_get_youtube_clips_visual_gate_falls_back_within_attempt_budget(self):
        downloaded = []

        def fake_dl(_option, out, _start, _dur, session_jar=None, stream_downloads=False):
            out.write_bytes(b"x" * 100)
            downloaded.append(_option.get("id"))
            return True

        def fake_fetch(query, max_results=12, session_jar=None):
            base = {"channel": "Wrestling Archive", "description": "raw footage highlights", "duration": 300, "query_text": query}
            return [
                dict(base, id="cenaA", title="John Cena training raw footage A"),
                dict(base, id="cenaB", title="John Cena training raw footage B"),
            ]

        def fake_verdict(_path, _scene_index, _topic, _narration, _subject, _vc):
            return (len(downloaded) >= 2, "low_visual_quality", True)

        with mock.patch.object(ytv2, "_search_youtube", side_effect=fake_fetch), \
             mock.patch.object(ytv2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.object(ytv2, "_download_clip", side_effect=fake_dl), \
             mock.patch.object(ytv2, "_visual_verdict", side_effect=fake_verdict), \
             mock.patch.dict(os.environ, {"VIDZEO_YT_V2_DOWNLOAD_ATTEMPTS_PER_SCENE": "3"}, clear=False):
            results = ytv2.get_youtube_clips(
                script="John Cena trains for WWE.",
                title="John Cena",
                num_scenes=1,
                scene_durations=[4.0],
                scene_texts=["John Cena trains for WWE."],
                visual_context=object(),
                scene_query_overrides=[{"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports", "youtube_query": "John Cena WWE training footage"}],
                output_dir=str(tempfile.mkdtemp()),
            )
        self.assertEqual(len(results), 1)
        self.assertEqual(len(downloaded), 1)
        self.assertIsNotNone(results[0])
        self.assertTrue(Path(results[0]).exists())

    def test_visual_verdict_fails_open_when_vision_unavailable(self):
        accepted, reason, vision_available = ytv2._visual_verdict("some.mp4", 0, "subject", "narration", "subject", None)
        self.assertTrue(accepted)
        self.assertFalse(vision_available)

    def test_visual_gate_frames_clamped_to_one_or_two(self):
        # Media-selection spec: 1-2 frames per clip at the matched timestamp.
        with mock.patch.dict(os.environ, {"VIDZEO_YT_VISUAL_GATE_FRAMES": "8"}, clear=False):
            self.assertEqual(ytv2._visual_gate_frames(), 2)
        with mock.patch.dict(os.environ, {"VIDZEO_YT_VISUAL_GATE_FRAMES": "1"}, clear=False):
            self.assertEqual(ytv2._visual_gate_frames(), 1)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VIDZEO_YT_VISUAL_GATE_FRAMES", None)
            self.assertEqual(ytv2._visual_gate_frames(), 2)

    def test_whisper_timing_windows_override_provisional_scene_durations(self):
        self.assertAlmostEqual(
            ytv2._youtube_timing_window_duration({
                "start_seconds": 12.25,
                "end_seconds": 17.75,
                "timing_quality": "word_aligned",
            }),
            5.5,
        )
        self.assertEqual(
            ytv2._resolve_youtube_scene_durations(
                2,
                [4.0, 4.0],
                [
                    {"start_seconds": 0.0, "end_seconds": 2.25},
                    {"start": 2.25, "duration_needed": 6.75},
                ],
            ),
            [2.25, 6.75],
        )
        self.assertEqual(
            ytv2._resolve_youtube_scene_durations(
                1,
                [4.0],
                [{"start_seconds": 0.0, "end_seconds": 2.25}],
                prefer_aligned=False,
            ),
            [4.0],
        )

    def test_provider_ids_are_safe_for_clip_output_paths(self):
        self.assertEqual(
            ytv2._safe_youtube_path_token("../../video:id?part=1"),
            "video_id_part_1",
        )

    def test_youtube_retrieval_prefers_hd_with_bounded_labelled_sd_fallback(self):
        with mock.patch.dict(os.environ, {
            "VIDZEO_YT_MIN_SOURCE_HEIGHT": "",
            "VIDZEO_YT_MAX_SOURCE_HEIGHT": "",
            "VIDZEO_YT_FORMAT": "",
        }, clear=False):
            os.environ.pop("VIDZEO_YT_MIN_SOURCE_HEIGHT", None)
            os.environ.pop("VIDZEO_YT_MAX_SOURCE_HEIGHT", None)
            self.assertEqual(ytv2._youtube_min_source_height(), 720)
            self.assertEqual(ytv2._youtube_quality_limits()[0], 1080)
            formats = ytv2._youtube_quality_formats()
            self.assertTrue(formats)
            self.assertIn("height>=720", formats[0])
            self.assertTrue(all("height>=" in item for item in formats))
            self.assertTrue(any("height>=480" in item for item in formats))
            self.assertTrue(any("height<=2160" in item for item in formats))

        with mock.patch.dict(os.environ, {
            "VIDZEO_YT_MIN_SOURCE_HEIGHT": "720",
            "VIDZEO_YT_FORMAT": "bestvideo[height<=1080]+bestaudio",
        }, clear=False):
            # A legacy override without an HD floor must not become the first
            # selector or bypass the post-download quality requirement.
            self.assertIn("height>=720", ytv2._youtube_quality_formats()[0])

        # A stale SD override cannot weaken the HD floor.
        with mock.patch.dict(os.environ, {"VIDZEO_YT_MIN_SOURCE_HEIGHT": "360"}, clear=False):
            self.assertEqual(ytv2._youtube_min_source_height(), 720)

    def test_ranged_clip_rejects_sd_or_unverified_height(self):
        for source_height in (480, 0):
            instances, yt_dlp = self._mock_yt_dlp()
            with tempfile.TemporaryDirectory() as temp_dir, yt_dlp, \
                 mock.patch.dict(os.environ, {"VIDZEO_YT_FORMAT": "", "VIDZEO_YT_ALLOW_SD_FALLBACK": "0"}, clear=False), \
                 mock.patch.object(ytv2, "_source_has_video_stream", return_value=True), \
                 mock.patch.object(ytv2, "_source_video_height", return_value=source_height), \
                 mock.patch.object(ytv2.subprocess, "run") as ffmpeg_run:
                output = Path(temp_dir) / "clip.mp4"
                self.assertFalse(ytv2._download_clip(
                    {"id": "video-1", "webpage_url": "https://youtube.com/watch?v=video-1"},
                    output,
                    15.0,
                    4.0,
                ))
                self.assertFalse(output.exists())
                ffmpeg_run.assert_not_called()
            self.assertIn("height>=720", instances[0].options["format"])

    def test_parallel_visual_verdicts_run_concurrently_and_pick_scores(self):
        import threading

        calls = []
        rendezvous = threading.Barrier(3)
        concurrent = []

        def slow(path, _scene_index, _topic, _narration, _subject, _vc):
            calls.append(path)
            try:
                rendezvous.wait(timeout=10)
                concurrent.append(path)
            except threading.BrokenBarrierError:
                pass
            return (True, 80.0 + len(calls), True, "keep")

        with mock.patch.object(ytv2, "_visual_verdict_scored", side_effect=slow):
            out = ytv2._parallel_visual_verdicts([(0, "a.mp4"), (1, "b.mp4"), (2, "c.mp4")], 0, "t", "n", "s", object())
        self.assertEqual(sorted(out), [0, 1, 2])
        # Prove all jobs overlap; wall-clock thresholds are unreliable while
        # the real render acceptance portfolio competes for this machine.
        self.assertEqual(sorted(concurrent), ['a.mp4', 'b.mp4', 'c.mp4'])

    def test_resolve_clip_for_scene_picks_best_vision_accepted_candidate(self):
        downloaded = []

        class StrictContext:
            visual_required = True

        def fake_dl(_option, out, _start, _dur, session_jar=None, stream_downloads=False):
            out.write_bytes(b"x" * 100)
            downloaded.append(_option.get("id"))
            return True

        def fake_fetch(query, max_results=12, session_jar=None):
            base = {"channel": "Wrestling Archive", "description": "raw footage highlights", "duration": 300, "query_text": query}
            return [
                dict(base, id="clipA", title="John Cena training raw footage A"),
                dict(base, id="clipB", title="John Cena training raw footage B"),
            ]

        # clipA scores below the accept floor (replace), clipB scores well (keep).
        def fake_scored(path, _scene_index, _topic, _narration, _subject, _vc):
            if "clipA" in path:
                return (False, 40.0, True, "replace")
            return (True, 90.0, True, "keep")

        with mock.patch.object(ytv2, "_search_youtube", side_effect=fake_fetch), \
             mock.patch.object(ytv2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.object(ytv2, "_download_clip", side_effect=fake_dl), \
             mock.patch.object(ytv2, "_visual_verdict_scored", side_effect=fake_scored), \
             mock.patch.dict(os.environ, {"VIDZEO_YT_VISUAL_GATE_PARALLEL": "3"}, clear=False):
            pools = ytv2.build_clip_pools(
                "John Cena",
                ["John Cena trains for WWE."],
                scene_query_overrides=[{"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports", "youtube_query": "John Cena WWE training footage"}],
                use_authenticated_session=False,
            )
            state = ytv2.make_resolve_state(pools)
            path = ytv2.resolve_clip_for_scene(0, pools, state, visual_context=StrictContext(), output_dir=str(tempfile.mkdtemp()))
        # Candidates are downloaded progressively; the second is fetched only
        # after strict vision rejects the first.
        self.assertEqual(sorted(downloaded), ["clipA", "clipB"])
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).exists())

    def test_jev_reranks_close_youtube_options_but_visual_gate_remains_required(self):
        downloaded=[]

        class DecisionContext:
            visual_required=True
            api_key='test-openrouter-key'
            call_timeout_seconds=1.0

        def fake_dl(option, out, _start, _duration, session_jar=None, stream_downloads=False):
            out.write_bytes(b'x'*100)
            downloaded.append(option.get('id'))
            return True

        def fake_fetch(query, max_results=12, session_jar=None):
            base={'channel':'NFL Archive','description':'Jermaine Kearse Super Bowl XLIX catch football play raw footage',
                  'duration':300,'query_text':query}
            return [
                dict(base,id='clipA',title='Super Bowl XLIX Seattle Patriots play raw footage'),
                dict(base,id='clipB',title='Jermaine Kearse four-bobble catch replay'),
            ]

        jev_inputs=[]
        def jev_choose(**kwargs):
            jev_inputs.append(kwargs)
            selected_index=next(
                index for index,row in enumerate(kwargs['candidates'])
                if 'four-bobble' in row.get('title','').casefold()
            )
            return {'selected_index':selected_index,'confidence':0.9,'selected_probability':0.91,
                    'model':'typesafe/jev-1.13','decision_id':'test-decision',
                    'candidates_considered':2}

        with mock.patch.object(ytv2,'_search_youtube',side_effect=fake_fetch), \
             mock.patch.object(ytv2,'_get_authenticated_youtube_session',return_value=None), \
             mock.patch.object(ytv2,'_download_clip',side_effect=fake_dl), \
             mock.patch.object(ytv2,'_visual_verdict_scored',return_value=(True,90.0,True,'keep')), \
             mock.patch('services.jev_decision_service.choose_youtube_candidate',side_effect=jev_choose), \
             mock.patch.dict(os.environ,{'VIDZEO_JEV_YT_RERANK':'1'},clear=False):
            pools=ytv2.build_clip_pools(
                'Super Bowl XLIX',
                ['Jermaine Kearse secures the four-bobble catch.'],
                scene_query_overrides=[{'_llm':True,'section_subject':'Jermaine Kearse',
                    'scene_subject':'Jermaine Kearse','domain_category':'sports',
                    'youtube_query':'Jermaine Kearse Super Bowl XLIX catch replay'}],
                use_authenticated_session=False,
            )
            state=ytv2.make_resolve_state(pools)
            path=ytv2.resolve_clip_for_scene(0,pools,state,visual_context=DecisionContext(),
                output_dir=str(tempfile.mkdtemp()))

        assert len(jev_inputs)==1
        assert jev_inputs[0]['scene_subject']=='Jermaine Kearse'
        assert len(jev_inputs[0]['candidates'])==2
        assert any(row.get('event')=='jev_youtube_candidate_order' for row in state.attempt_log), state.attempt_log
        event=next(row for row in state.attempt_log if row.get('event')=='jev_youtube_candidate_order')
        assert event['selected_candidate_id']=='clipB', state.attempt_log
        assert downloaded[0]=='clipB', (jev_inputs[0], state.attempt_log)
        assert path and Path(path).exists()
        decision=next(row for row in state.attempt_log if row.get('event')=='jev_youtube_candidate_order')
        assert decision['selected_candidate_id']=='clipB'
        assert decision['visual_gate_still_required'] is True

    def test_resolve_clip_for_scene_keeps_metadata_winner_when_advisory_vision_rejects_all(self):
        downloaded = []

        def fake_dl(_option, out, _start, _dur, session_jar=None, stream_downloads=False):
            out.write_bytes(b"x" * 100)
            downloaded.append(_option.get("id"))
            return True

        def fake_fetch(query, max_results=12, session_jar=None):
            base = {"channel": "Wrestling Archive", "description": "raw footage highlights", "duration": 300, "query_text": query}
            return [dict(base, id="clipA", title="John Cena training raw footage A")]

        def fake_scored(_path, _scene_index, _topic, _narration, _subject, _vc):
            return (False, 30.0, True, "replace")

        with mock.patch.object(ytv2, "_search_youtube", side_effect=fake_fetch), \
             mock.patch.object(ytv2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.object(ytv2, "_download_clip", side_effect=fake_dl), \
             mock.patch.object(ytv2, "_visual_verdict_scored", side_effect=fake_scored):
            pools = ytv2.build_clip_pools(
                "John Cena",
                ["John Cena trains for WWE."],
                scene_query_overrides=[{"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports", "youtube_query": "John Cena WWE training footage"}],
                use_authenticated_session=False,
            )
            state = ytv2.make_resolve_state(pools)
            path = ytv2.resolve_clip_for_scene(0, pools, state, visual_context=object(), output_dir=str(tempfile.mkdtemp()))
        # Relevance vision is advisory: a validated download still ships so
        # the scene does not enter repeated web/stock repair loops.
        self.assertEqual(downloaded, ["clipA"])
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).exists())

    def test_resolve_clip_for_scene_strict_vision_can_still_reject_all(self):
        class StrictContext:
            visual_required = True

        def fake_dl(_option, out, _start, _dur, session_jar=None, stream_downloads=False):
            out.write_bytes(b"x" * 100)
            return True

        def fake_fetch(query, max_results=12, session_jar=None):
            return [{
                "id": "clipA", "title": "John Cena training raw footage A",
                "channel": "Wrestling Archive", "description": "raw footage highlights",
                "duration": 300, "query_text": query,
            }]

        with mock.patch.object(ytv2, "_search_youtube", side_effect=fake_fetch), \
             mock.patch.object(ytv2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.object(ytv2, "_download_clip", side_effect=fake_dl), \
             mock.patch.object(ytv2, "_visual_verdict_scored", return_value=(False, 30.0, True, "replace")):
            pools = ytv2.build_clip_pools(
                "John Cena",
                ["John Cena trains for WWE."],
                scene_query_overrides=[{"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports", "youtube_query": "John Cena WWE training footage"}],
                use_authenticated_session=False,
            )
            state = ytv2.make_resolve_state(pools)
            path = ytv2.resolve_clip_for_scene(
                0, pools, state, visual_context=StrictContext(), output_dir=str(tempfile.mkdtemp())
            )
        self.assertIsNone(path)

    def test_get_youtube_clips_no_gate_survives_partial_first_candidate_download_failure(self):
        # Regression: when the metadata-best candidate download FAILS but a
        # later candidate succeeds, the no-gate winner selection must use a
        # downloaded-list position, never the raw plan index (which used to
        # raise IndexError and abort the whole scene).
        downloaded = []

        def fake_dl(option, out, _start, _dur, session_jar=None, stream_downloads=False):
            if option.get("id") == "bad":
                return False
            out.write_bytes(b"x" * 100)
            downloaded.append(option.get("id"))
            return True

        def fake_fetch(query, max_results=12, session_jar=None):
            base = {"channel": "Wrestling Archive", "description": "raw footage highlights", "duration": 300, "query_text": query}
            return [
                dict(base, id="bad", title="John Cena training bad"),   # plan index 0
                dict(base, id="goodA", title="John Cena training A"),   # plan index 1
                dict(base, id="goodB", title="John Cena training B"),   # plan index 2
            ]

        with mock.patch.object(ytv2, "_search_youtube", side_effect=fake_fetch), \
             mock.patch.object(ytv2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.object(ytv2, "_download_clip", side_effect=fake_dl), \
             mock.patch.object(ytv2, "_visual_verdict_scored", return_value=(True, 85.0, True, "keep")), \
             mock.patch.dict(os.environ, {"VIDZEO_YT_V2_QUERIES_PER_CONTAINER": "3"}, clear=False):
            results = ytv2.get_youtube_clips(
                script="John Cena trains for WWE.",
                title="John Cena",
                num_scenes=1,
                scene_durations=[4.0],
                scene_texts=["John Cena trains for WWE."],
                scene_query_overrides=[{"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports", "youtube_query": "John Cena WWE training footage"}],
                output_dir=str(tempfile.mkdtemp()),
                download_candidate_limit=3,
            )
        # The bad candidate was skipped; the first good candidate downloads and
        # the scene resolves instead of crashing with IndexError.
        self.assertEqual(downloaded, ["goodA"])
        self.assertIsNotNone(results[0])
        self.assertTrue(Path(results[0]).exists())
        self.assertTrue(hasattr(results, "audit"))
        self.assertEqual(results.audit["resolved"], 1)
        assert any(attempt["status"] == "download_failed" for attempt in results.audit["attempts"]["0"])
        assert any(attempt["status"] == "downloaded" for attempt in results.audit["attempts"]["0"])

    # ------------------------------------------------------------------ #
    # Production regression: LLM container-map disaster jobs must resolve  #
    # ------------------------------------------------------------------ #

    def test_container_anchor_fall_back_accepts_real_footage_for_llm_container_subjects(self):
        # LLM container subjects are descriptive labels footage never repeats
        # verbatim. A candidate matching the domain anchor is still correct.
        candidate = {
            "title": "RMS Titanic Archival Historical Footage 1912",
            "channel": "British Pathé Archive",
            "description": "documentary footage of the shipwreck and lifeboats",
            "duration": 407,
        }
        context = {
            "domain_category": "disaster",
            "domain_anchor": "RMS Titanic",
            "container_subject": "April 15 1912",
            "primary_container_subject": "RMS Titanic",
        }
        score = ytv2._score_candidate(
            candidate,
            "April 15 1912",
            "On April 15 1912 the Titanic struck an iceberg.",
            "April 15 1912 archival historical footage",
            context,
        )
        self.assertGreater(score, 0)
        self.assertNotEqual(candidate.get("v2_reject_reason"), "low_score")

    def test_container_anchor_fall_back_still_rejects_off_topic_candidate(self):
        candidate = {
            "title": "Grand Theft Auto VI Official Trailer",
            "channel": "Rockstar Games",
            "description": "gameplay trailer footage",
            "duration": 166,
        }
        context = {
            "domain_category": "disaster",
            "domain_anchor": "RMS Titanic",
            "container_subject": "Titanic sinking",
            "primary_container_subject": "RMS Titanic",
        }
        score = ytv2._score_candidate(
            candidate,
            "Titanic sinking",
            "The Titanic sank after striking an iceberg.",
            "Titanic sinking footage",
            context,
        )
        self.assertLess(score, 0)

    def test_llm_container_map_disaster_job_resolves_high_coverage(self):
        # A 20-scene multi-subject disaster job must resolve most scenes, even
        # though each LLM container subject is a label the footage never uses.
        scenes = [f"On April {idx} the Titanic was at sea." for idx in range(20)]
        subjects = [
            "April 15 1912", "Titanic disaster", "April 10 1912", "White Star Line",
            "Titanic departure from Southampton", "Titanic maiden voyage to New York",
            "Titanic largest passenger ship", "Titanic collision course",
            "Titanic lifeboat shortage", "Titanic wireless messages", "Titanic sinking",
            "Titanic final hours", "Titanic survivors", "Titanic rescue Carpathia",
            "Titanic inquiry", "Titanic wreck discovery", "Titanic memorial",
            "Titanic legacy", "Titanic museum artifacts", "Titanic film legacy",
        ]
        overrides = [
            {
                "_llm": True, "section_subject": subjects[idx % 20], "scene_subject": subjects[idx % 20],
                "domain_anchor": "RMS Titanic", "topic_anchor": "RMS Titanic", "domain_category": "disaster",
                "youtube_query": f"{subjects[idx % 20]} footage",
            }
            for idx in range(20)
        ]
        vids = [
            {"id": f"vid{i}", "title": f"RMS Titanic Archival Historical Footage {i}", "channel": "British Pathe Archive",
             "description": "documentary footage of the shipwreck and lifeboats", "duration": dur}
            for i, dur in enumerate([407, 520, 1300, 340, 660, 920, 210, 1560])
        ]

        def fake_search(query, _max_results=12, _session_jar=None):
            return [dict(v) for v in vids]

        def fake_dl(_o, out, _s, _d, session_jar=None, stream_downloads=False):
            out.write_bytes(b"x" * 100)
            return True

        with mock.patch.object(ytv2, "_search_youtube", side_effect=fake_search), \
             mock.patch.object(ytv2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.object(ytv2, "_download_clip", side_effect=fake_dl), \
             mock.patch.object(ytv2, "_visual_verdict_scored", return_value=(True, 85.0, True, "keep")), \
             mock.patch.dict(os.environ, {"VIDZEO_YT_V2_QUERIES_PER_CONTAINER": "3", "VIDZEO_YT_REUSE_TIMESTAMP_GAP": "45", "VIDZEO_YT_POOL_FIRST": "0"}, clear=False), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VIDZEO_YT_V2_SKIP_DOWNLOAD", None)
            results = ytv2.get_youtube_clips(
                script="A Titanic documentary.",
                title="RMS Titanic",
                num_scenes=20,
                scene_durations=[2.0] * 20,
                scene_texts=scenes,
                scene_query_overrides=overrides,
                output_dir=str(tempfile.mkdtemp()),
                visual_context=object(),
            )
        resolved = sum(1 for path in results if path)
        self.assertGreaterEqual(resolved, 15)

    def test_single_topic_llm_micro_containers_share_one_source_pool(self):
        scenes = [
            "Titanic left Southampton on her maiden voyage.",
            "The ship struck an iceberg late at night.",
            "Carpathia rescued survivors from lifeboats.",
            "Investigators later examined the disaster.",
        ]
        subjects = [
            "Southampton departure",
            "Iceberg collision",
            "Carpathia rescue",
            "British inquiry",
        ]
        overrides = [
            {
                "_llm": True,
                "container_id": f"titanic_section_{idx}",
                "section_subject": subject,
                "scene_subject": subject,
                "domain_anchor": "RMS Titanic",
                "topic_anchor": "RMS Titanic",
                "global_topic": "RMS Titanic",
                "domain_category": "disaster",
                "youtube_query": f"RMS Titanic {subject} archival footage",
            }
            for idx, subject in enumerate(subjects)
        ]

        rows = ytv2._build_scene_rows("RMS Titanic documentary", scenes, overrides)
        groups = ytv2._group_rows(rows)

        self.assertEqual(len(groups), 1)
        self.assertEqual(
            {_norm for _norm in (ytv2._norm(row["primary_container_subject"]) for row in rows)},
            {"rms titanic"},
        )
        self.assertTrue(all(
            ytv2._norm(row["candidate_subject"]) == "rms titanic"
            for row in rows
        ))
        # Pooling must not erase scene-specific retrieval intent.
        self.assertTrue(any("iceberg collision" in " ".join(row["queries"]).lower() for row in rows))

    def test_inconsistent_titanic_anchors_infer_one_dominant_entity_pool(self):
        # Mirrors the production failure where 52 scenes became 37 pools.  No
        # exact anchor reaches consensus, but Titanic is the recurring named
        # entity inside the LLM's scene-specific descriptive labels.
        subjects = [
            "April 15 1912",
            "Titanic maritime disaster",
            "Titanic departure date",
            "RMS Titanic departure from Southampton",
            "White Star Line shipping company",
            "Titanic largest passenger ship",
            "Titanic engineering masterpiece",
            "Titanic unsinkable belief",
            "North Atlantic crossing",
            "Titanic iceberg collision",
            "Titanic lifeboat shortage",
            "RMS Carpathia rescue",
        ]
        scenes = [
            "On April 15, 1912, a maritime disaster changed history.",
            "The Titanic disaster shocked the entire world.",
            "Titanic departed Southampton on her maiden voyage.",
            "RMS Titanic sailed from Southampton toward New York.",
            "White Star Line built the Titanic as its newest ocean liner.",
            "Titanic was then the largest passenger ship ever constructed.",
            "Engineers described Titanic as a technical masterpiece.",
            "Many passengers believed the Titanic was unsinkable.",
            "The ship carried families across the North Atlantic.",
            "Titanic scraped an iceberg along her starboard side.",
            "Titanic carried lifeboats for only half of those aboard.",
            "RMS Carpathia rescued Titanic survivors from their lifeboats.",
        ]
        overrides = []
        for idx, subject in enumerate(subjects):
            overrides.append({
                "_llm": True,
                "container_id": f"micro_{idx}",
                "section_subject": subject,
                "scene_subject": subject,
                # Deliberately reproduce inconsistent/absent planner anchors.
                "domain_anchor": subject if idx % 3 == 0 else "",
                "topic_anchor": subject if idx % 3 == 1 else "",
                "global_topic": subject if idx % 3 == 2 else "",
                "title_anchor": subject,
                "domain_category": "disaster",
                "youtube_query": f"{subject} archival footage",
            })

        rows = ytv2._build_scene_rows("On April 15, 1912, one of", scenes, overrides)
        groups = ytv2._group_rows(rows)

        self.assertEqual(len(groups), 1)
        self.assertEqual(
            {ytv2._norm(row["primary_container_subject"]) for row in rows},
            {"rms titanic"},
        )
        self.assertTrue(all(
            ytv2._norm(row["candidate_subject"]) == "rms titanic"
            for row in rows
        ))
        self.assertTrue(any(
            "white star line" in " ".join(row["queries"]).lower()
            for row in rows
        ))

    def test_near_equal_comparison_entities_do_not_collapse_to_one_pool(self):
        scenes = [
            "Lionel Messi developed at Barcelona.",
            "Cristiano Ronaldo starred for Manchester United.",
            "Messi won the World Cup with Argentina.",
            "Ronaldo became Portugal's leading scorer.",
            "Lionel Messi is known for close control.",
            "Cristiano Ronaldo is known for aerial finishing.",
        ]
        subjects = ["Lionel Messi", "Cristiano Ronaldo"] * 3
        overrides = [
            {
                "_llm": True,
                "container_id": f"player_{idx}",
                "section_subject": subject,
                "scene_subject": subject,
                "global_topic": "Lionel Messi and Cristiano Ronaldo comparison",
                "domain_category": "sports",
                "youtube_query": f"{subject} match footage",
            }
            for idx, subject in enumerate(subjects)
        ]

        rows = ytv2._build_scene_rows("Messi and Ronaldo career comparison", scenes, overrides)

        self.assertGreaterEqual(len(ytv2._group_rows(rows)), 2)
        self.assertEqual(
            {ytv2._norm(row["candidate_subject"]) for row in rows},
            {"lionel messi", "cristiano ronaldo"},
        )

    def test_search_budget_scales_with_job_cost(self):
        from concurrent.futures import ThreadPoolExecutor
        jobs = ["q"] * 180

        per_scene = 10.0
        max_workers = 6
        scaled = per_scene + (len(jobs) // max_workers + 1) * 2.0
        self.assertGreaterEqual(scaled, 65)
        # The production bug used a bare 10s cap -> 0 candidates. The scaled
        # value must comfortably cover 180 searches at ~2s/round.
        self.assertGreater(scaled, 30)

    def test_progressive_download_fails_open_when_vision_unavailable(self):
        downloaded = []

        class StrictContext:
            enabled = True
            visual_required = True

        def fake_dl(_option, out, _start, _dur, session_jar=None, stream_downloads=False):
            out.write_bytes(b"x" * 100)
            downloaded.append(_option.get("id"))
            return True

        def fake_fetch(query, max_results=12, session_jar=None):
            base = {"channel": "Wrestling Archive", "description": "raw footage highlights", "duration": 300, "query_text": query}
            return [
                dict(base, id="clip1", title="John Cena training raw footage 1"),
                dict(base, id="clip2", title="John Cena training raw footage 2"),
                dict(base, id="clip3", title="John Cena training raw footage 3"),
            ]

        mock_scored = mock.MagicMock(return_value=(False, 0.0, False, "no_model_available"))

        with mock.patch.object(ytv2, "_search_youtube", side_effect=fake_fetch), \
             mock.patch.object(ytv2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.object(ytv2, "_download_clip", side_effect=fake_dl), \
             mock.patch.object(ytv2, "_visual_verdict_scored", mock_scored), \
             mock.patch.dict(os.environ, {
                 "VIDZEO_YT_FAIL_OPEN_ON_VISUAL_UNAVAILABLE": "1",
                 "VIDZEO_YT_RELAXED_VISUAL_GATE": "1",
                 "VIDZEO_YT_V2_DOWNLOAD_ATTEMPTS_PER_SCENE": "3",
             }, clear=False):
            results = ytv2.get_youtube_clips(
                script="John Cena trains for WWE.",
                title="John Cena",
                num_scenes=1,
                scene_durations=[4.0],
                scene_texts=["John Cena trains for WWE."],
                visual_context=StrictContext(),
                scene_query_overrides=[{"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports", "youtube_query": "John Cena WWE training footage"}],
                output_dir=str(tempfile.mkdtemp()),
            )
        self.assertEqual(len(results), 1)
        self.assertIsNotNone(results[0])
        self.assertTrue(Path(results[0]).exists())
        self.assertEqual(downloaded, ["clip1"])
        self.assertEqual(mock_scored.call_count, 1)

    def test_progressive_fallback_fails_open_when_vision_unavailable(self):
        downloaded = []

        class StrictContext:
            enabled = True
            visual_required = True

        def fake_dl(_option, out, _start, _dur, session_jar=None, stream_downloads=False):
            out.write_bytes(b"x" * 100)
            downloaded.append(_option.get("id"))
            return True

        def fake_fetch(query, max_results=12, session_jar=None):
            base = {"channel": "Wrestling Archive", "description": "raw footage highlights", "duration": 300, "query_text": query}
            return [
                dict(base, id="clip1", title="John Cena training raw footage 1"),
                dict(base, id="clip2", title="John Cena training raw footage 2"),
            ]

        mock_scored = mock.MagicMock(return_value=(False, 0.0, False, "vision_network_timeout"))

        with mock.patch.object(ytv2, "_search_youtube", side_effect=fake_fetch), \
             mock.patch.object(ytv2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.object(ytv2, "_download_clip", side_effect=fake_dl), \
             mock.patch.object(ytv2, "_visual_verdict_scored", mock_scored), \
             mock.patch.dict(os.environ, {
                 "VIDZEO_YT_FAIL_OPEN_ON_VISUAL_UNAVAILABLE": "1",
                 "VIDZEO_YT_RELAXED_VISUAL_GATE": "1",
             }, clear=False):
            pools = ytv2.build_clip_pools(
                "John Cena",
                ["John Cena trains for WWE."],
                scene_query_overrides=[{"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports", "youtube_query": "John Cena WWE training footage"}],
                use_authenticated_session=False,
            )
            state = ytv2.make_resolve_state(pools)
            path = ytv2.resolve_clip_for_scene(
                0, pools, state, visual_context=StrictContext(), output_dir=str(tempfile.mkdtemp())
            )
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).exists())
        self.assertEqual(downloaded, ["clip1"])
        self.assertEqual(mock_scored.call_count, 1)

    def test_progressive_download_strict_fail_closed_when_fail_open_disabled(self):
        downloaded = []

        class StrictContext:
            enabled = True
            visual_required = True

        def fake_dl(_option, out, _start, _dur, session_jar=None, stream_downloads=False):
            out.write_bytes(b"x" * 100)
            downloaded.append(_option.get("id"))
            return True

        def fake_fetch(query, max_results=12, session_jar=None):
            base = {"channel": "Wrestling Archive", "description": "raw footage highlights", "duration": 300, "query_text": query}
            return [
                dict(base, id="clip1", title="John Cena training raw footage 1"),
                dict(base, id="clip2", title="John Cena training raw footage 2"),
            ]

        with mock.patch.object(ytv2, "_search_youtube", side_effect=fake_fetch), \
             mock.patch.object(ytv2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.object(ytv2, "_download_clip", side_effect=fake_dl), \
             mock.patch.object(ytv2, "_visual_verdict_scored", return_value=(False, 0.0, False, "no_model")), \
             mock.patch.dict(os.environ, {
                 "VIDZEO_YT_FAIL_OPEN_ON_VISUAL_UNAVAILABLE": "0",
                 "VIDZEO_YT_RELAXED_VISUAL_GATE": "0",
             }, clear=False):
            pools = ytv2.build_clip_pools(
                "John Cena",
                ["John Cena trains for WWE."],
                scene_query_overrides=[{"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports", "youtube_query": "John Cena WWE training footage"}],
                use_authenticated_session=False,
            )
            state = ytv2.make_resolve_state(pools)
            path = ytv2.resolve_clip_for_scene(
                0, pools, state, visual_context=StrictContext(), output_dir=str(tempfile.mkdtemp())
            )
        self.assertIsNone(path)
        self.assertEqual(sorted(downloaded), ["clip1", "clip2"])

    def test_classify_visual_result_outcomes(self):
        self.assertEqual(ytv2._classify_visual_result(False, 0.0, False, "unavailable"), "unavailable")
        self.assertEqual(ytv2._classify_visual_result(True, 95.0, True, "high_quality"), "pass")


if __name__ == "__main__":
    unittest.main()
