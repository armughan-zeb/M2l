import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from contextlib import contextmanager
from concurrent.futures import Future

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

import app as server_app
from services.pipeline_contracts import MediaContractError


@contextmanager
def _owned_youtube_environment():
    """Exercise child-local tuning without mutating the real backend env."""
    class InlineWorker:
        def __init__(self, _payload, **kwargs):
            self.environment = kwargs['environment']
        def submit(self, fn):
            future = Future()
            try:
                with mock.patch.dict(os.environ, self.environment, clear=True):
                    future.set_result(fn())
            except Exception as exc:
                future.set_exception(exc)
            return future
        def shutdown(self, **_kwargs):
            pass
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop('PYTEST_CURRENT_TEST', None)
        os.environ['VIDZEO_YT_HARD_STOP_PROCESS'] = '1'
        with mock.patch.object(server_app, '_YoutubeProcessExecutor', InlineWorker):
            yield


def test_generation_json_response_serializes_nested_native_paths():
    payload = {'success': True, 'video_path': Path('completed.mp4'),
               'render_metadata': {'quality_checks': {'frame_paths': [Path('frame.png')]}}}
    with server_app.app.test_request_context('/api/generate', method='POST', json={'response_mode': 'json'}):
        response, status = server_app._generation_response(payload)
    assert status == 200
    assert response.get_json()['video_path'] == 'completed.mp4'
    assert response.get_json()['render_metadata']['quality_checks']['frame_paths'] == ['frame.png']


def test_scene_brain_query_keeps_full_global_game_title_anchor():
    query = server_app._subject_safe_scene_brain_query(
        "Vice City as the threshold VI official trailer Vice City skyline waterfront",
        "Grand Theft Auto VI official trailer footage",
        "Vice City as the threshold",
        "Grand Theft Auto VI",
    )
    assert "Grand Theft Auto VI" in query
    assert "Vice City" in query


def _write_quality_gate_samples(root):
    """Build small deterministic stills instead of relying on old output files."""
    try:
        import cv2
        import numpy as np
    except Exception as exc:
        raise unittest.SkipTest(f"OpenCV quality analyzer unavailable: {exc}")

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    clean = rng.integers(30, 230, (360, 640), dtype=np.uint8)
    flat = np.full((360, 640), 4, dtype=np.uint8)
    soft_band = rng.integers(30, 230, (360, 640), dtype=np.uint8)
    # Smooth lower-strip variation keeps luminance variance while removing
    # recoverable edge detail, as happens with baked social-video blur bands.
    smooth_wave = (120 + 75 * np.sin(np.linspace(0, 2 * np.pi, 640))).clip(0, 255).astype(np.uint8)
    for row in range(245, 360):
        soft_band[row, :] = smooth_wave
    clean_path = root / "clean-textured.jpg"
    flat_path = root / "flat-dark.jpg"
    soft_path = root / "soft-lower-band.jpg"
    cv2.imwrite(str(clean_path), clean, [cv2.IMWRITE_JPEG_QUALITY, 95])
    cv2.imwrite(str(flat_path), flat, [cv2.IMWRITE_JPEG_QUALITY, 95])
    cv2.imwrite(str(soft_path), soft_band, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return clean_path, flat_path, soft_path


class PipelineApiContractTests(unittest.TestCase):
    def setUp(self):
        self.client = server_app.app.test_client()
        self.auth_patcher = mock.patch("app._get_authenticated_state", return_value=("test-token", {"access_state": "active"}))
        self.auth_patcher.start()
        # Media contract tests isolate cloud admission; quotas have their own
        # real Postgres integration and account-access tests.
        quota_patcher=mock.patch('services.export_entitlements.reserve_generation_export', return_value={'allowed':True})
        quota_patcher.start()
        self.addCleanup(quota_patcher.stop)

    def tearDown(self):
        self.auth_patcher.stop()

    def test_authenticated_youtube_session_does_not_override_editorial_mix(self):
        base = {
            "niche": "serious",
            "weights": {"Web Images": 0.80, "AI Images": 0.0, "Stock Media": 0.0, "YouTube Clips": 0.20},
        }
        automatic = server_app._apply_authenticated_youtube_mix(
            base,
            {"image_source": "Mixed Media"},
            session_verified=True,
        )
        self.assertAlmostEqual(automatic["weights"]["YouTube Clips"], 0.20)
        self.assertNotIn("youtube_session_verified", automatic)
        self.assertNotIn("youtube_coverage_mode", automatic)

        explicit_authenticated_target = server_app._apply_authenticated_youtube_mix(
            base,
            {
                "image_source": "Mixed Media",
                "youtube_authenticated_coverage_target": 0.60,
            },
            session_verified=True,
        )
        self.assertAlmostEqual(explicit_authenticated_target["weights"]["YouTube Clips"], 0.60)
        self.assertTrue(explicit_authenticated_target["youtube_session_verified"])
        self.assertEqual(
            explicit_authenticated_target["youtube_coverage_mode"],
            "verified_cookie_video_majority",
        )

        explicit = server_app._apply_authenticated_youtube_mix(
            base,
            {
                "image_source": "Mixed Media",
                "mixed_media_weights": {"Web Images": 80, "YouTube Clips": 20},
            },
            session_verified=True,
        )
        self.assertAlmostEqual(explicit["weights"]["YouTube Clips"], 0.20)
        self.assertNotIn("youtube_coverage_mode", explicit)

        expired = server_app._apply_authenticated_youtube_mix(
            explicit_authenticated_target,
            {"image_source": "Mixed Media", "youtube_authenticated_coverage_target": 0.60},
            session_verified=False,
        )
        self.assertAlmostEqual(expired["weights"]["YouTube Clips"], 0.20)
        self.assertNotIn("youtube_coverage_mode", expired)

    def test_mixed_youtube_branch_wall_setting_is_a_hard_cap(self):
        with mock.patch.dict(os.environ, {"VIDZEO_MIXED_YT_BRANCH_WALL_TIMEOUT_SECONDS": "240"}):
            self.assertEqual(server_app._mixed_youtube_branch_wall_cap_seconds({}), 240.0)
        self.assertEqual(
            server_app._mixed_youtube_branch_wall_cap_seconds({"yt_branch_wall_timeout_seconds": 150}),
            150.0,
        )
        self.assertEqual(
            server_app._mixed_youtube_branch_wall_cap_seconds({"yt_branch_wall_timeout_seconds": 1800}),
            900.0,
        )

    def test_final_render_reuse_never_crosses_container_boundary(self):
        paths = ["ronaldo.mp4", None]
        labels = ["YouTube Clips", ""]
        with mock.patch("app._media_path_exists_for_render", side_effect=lambda path: bool(path)):
            filled = server_app._fill_final_bad_slots_from_existing_media(
                final_paths=paths,
                source_labels=labels,
                bad_indices=[1],
                scene_texts=["Cristiano Ronaldo scores.", "Lionel Messi scores."],
                render_resolution=(1280, 720),
                title="Football legends",
                prefer_video=True,
                config={"image_source": "YouTube Clips"},
                allow_emergency=False,
                section_subjects=["Cristiano Ronaldo", "Lionel Messi"],
            )

        self.assertEqual(filled, 0)
        self.assertIsNone(paths[1])

    def test_topic_anchor_prefers_documentary_topic_over_generic_country(self):
        anchor = server_app._resolve_topic_anchor(
            "The Vietnam War, a conflict that",
            "The Vietnam War drew the United States into a long conflict across Vietnam.",
        )

        self.assertEqual(anchor, "Vietnam War")

    def test_topic_anchor_rejects_date_hook_and_keeps_explicit_ship_entity(self):
        script = (
            "On April 15, 1912, the RMS Titanic became one of history most famous maritime disasters. "
            "Four days earlier, Titanic left Southampton on its maiden voyage to New York. "
            "At 11:40 PM, lookouts spotted an iceberg ahead. In 1985, Robert Ballard located the wreck."
        )

        anchor = server_app._resolve_topic_anchor_for_config(
            "On April 15, 1912, the RMS",
            script,
            {},
        )

        self.assertEqual(anchor, "RMS Titanic")

    def test_explicit_construction_title_outranks_related_event_anchor(self):
        anchor = server_app._resolve_topic_anchor_for_config(
            "How the Eiffel Tower Was Built",
            "In 1887, construction began on a tower for the 1889 Paris Exposition. Gustave Eiffel's company led the work.",
            {"_topic_anchor": "Paris Exposition"},
        )
        self.assertEqual(anchor, "Eiffel Tower")

    def test_single_word_brand_title_outranks_hook_phrase(self):
        anchor = server_app._resolve_topic_anchor_for_config(
            "Tesla: The Car Was Only the Beginning",
            "That sounds like the whole Tesla story. Tesla builds cars. Tesla also builds energy storage.",
            {},
        )
        self.assertEqual(anchor, "Tesla")

    def test_director_asset_is_hydrated_into_media_first_scene_contract(self):
        manifest = {
            "scenes": [
                {"primary_visual": {"type": "KineticText", "asset": {"src": "C:/media/hero.jpg", "type": "image"}}},
                {"media": {"src": "C:/media/existing.jpg"}, "primary_visual": {"asset": {"src": "C:/media/other.jpg"}}},
            ]
        }
        hydrated = server_app._hydrate_director_scene_media(manifest)
        self.assertEqual(hydrated["scenes"][0]["media"]["src"], "C:/media/hero.jpg")
        self.assertEqual(hydrated["scenes"][1]["media"]["src"], "C:/media/existing.jpg")

    def test_director_scene_media_uses_closed_media_list_when_parallel_asset_is_missing(self):
        manifest = {"scenes": [{"primary_visual": {"type": "Chart"}}]}
        media = [{"src": "C:/media/scene-0.mp4", "type": "video"}]

        hydrated = server_app._hydrate_director_scene_media(manifest, media)

        self.assertEqual(hydrated["scenes"][0]["media"]["src"], "C:/media/scene-0.mp4")
        self.assertEqual(hydrated["scenes"][0]["primary_visual"]["asset"]["type"], "video")

    def test_closed_media_list_overrides_stale_director_placeholder(self):
        manifest = {
            "scenes": [{
                "media": {"src": "C:/stale/director-placeholder.jpg"},
                "primary_visual": {"type": "Chart", "asset": {"src": "C:/stale/director-placeholder.jpg"}},
            }]
        }
        hydrated = server_app._hydrate_director_scene_media(
            manifest,
            [{"src": "C:/media/accepted-scene-0.mp4", "type": "video"}],
        )
        self.assertEqual(hydrated["scenes"][0]["media"]["src"], "C:/media/accepted-scene-0.mp4")
        self.assertEqual(hydrated["scenes"][0]["primary_visual"]["asset"]["type"], "video")

    def test_custom_media_quality_gate_replaces_near_flat_dark_still(self):
        from services.media_visual_quality import repair_media_sequence

        with tempfile.TemporaryDirectory() as temp_dir:
            clean, flat, _soft = _write_quality_gate_samples(temp_dir)
            items, repairs = repair_media_sequence([
                {"path": str(flat), "src": str(flat), "type": "image"},
                {"path": str(clean), "src": str(clean), "type": "image"},
            ])

            self.assertEqual(len(repairs), 1)
            self.assertEqual(Path(items[0]["path"]), clean)

    def test_custom_media_quality_gate_replaces_baked_blur_band(self):
        from services.media_visual_quality import inspect_still, repair_media_sequence

        with tempfile.TemporaryDirectory() as temp_dir:
            clean, _flat, blurred = _write_quality_gate_samples(temp_dir)
            inspection = inspect_still(str(blurred))
            self.assertTrue(inspection["baked_blur_band"])
            items, repairs = repair_media_sequence([
                {"path": str(blurred), "src": str(blurred), "type": "image"},
                {"path": str(clean), "src": str(clean), "type": "image"},
            ])
            self.assertEqual(len(repairs), 1)
            self.assertEqual(Path(items[0]["path"]), clean)

    def test_custom_media_quality_gate_rejects_large_soft_lower_duplicate_strip(self):
        from services.media_visual_quality import inspect_still

        with tempfile.TemporaryDirectory() as temp_dir:
            _clean, _flat, soft_band = _write_quality_gate_samples(temp_dir)
            inspection = inspect_still(str(soft_band))
            self.assertTrue(inspection["baked_blur_band"])
            self.assertTrue(inspection["needs_repair"])

    def test_custom_media_quality_gate_rejects_soft_bands_that_keep_luminance_variance(self):
        from services.media_visual_quality import inspect_still

        # These source frames retain plenty of luminance variation, but their
        # lower social-media blur has almost no recoverable edge detail.
        with tempfile.TemporaryDirectory() as temp_dir:
            _clean, _flat, soft_band = _write_quality_gate_samples(temp_dir)
            inspection = inspect_still(str(soft_band))
            self.assertTrue(inspection["baked_blur_band"])
            self.assertTrue(
                inspection["bottom_edge_collapse"]
                or inspection["lower_inner_edge_collapse"]
                or inspection["hard_matte_bars"],
            )

    def test_query_llm_uses_openrouter_even_for_legacy_groq_projects(self):
        provider, key, model = server_app._resolve_query_llm_settings({
            "groq_api_key": "groq-key",
            "openrouter_api_key": "openrouter-key",
            "llm_provider": "groq",
            "llm_model": "llama-3.3-70b-versatile",
            "query_llm_model": "qwen/qwen3.5-flash-02-23",
        })

        self.assertEqual(provider, "openrouter")
        self.assertEqual(key, "openrouter-key")
        self.assertEqual(model, "qwen/qwen3.5-flash-02-23")

    def test_runtime_logs_route_returns_bounded_redacted_tail(self):
        original = server_app.SERVER_ACTIVITY_LOG
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "backend-activity.log"
            log_path.write_text(
                "first line\nAuthorization: Bearer secret-token\nAPI_KEY=top-secret\nlast line\n",
                encoding="utf-8",
            )
            server_app.SERVER_ACTIVITY_LOG = log_path
            try:
                response = self.client.get('/api/runtime/logs?limit=2')
            finally:
                server_app.SERVER_ACTIVITY_LOG = original
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['count'], 2)
        self.assertEqual(payload['lines'][0], 'API_KEY=[redacted]')
        self.assertNotIn('top-secret', '\n'.join(payload['lines']))


    def test_shared_scene_brain_adapter_preserves_listicle_subjects_for_all_sources(self):
        scene_texts = [
            "Number five is Cristiano Ronaldo, still dangerous in the box.",
            "Number four is Kylian Mbappe, using speed to break defensive lines.",
        ]
        section_subjects = ["Cristiano Ronaldo", "Kylian Mbappe"]
        sections = [
            {"container_id": "section_5", "container_subject": "Cristiano Ronaldo", "has_explicit_marker": True, "is_intro": False},
            {"container_id": "section_4", "container_subject": "Kylian Mbappe", "has_explicit_marker": True, "is_intro": False},
        ]
        plans = [
            {
                "section_subject": "Cristiano Ronaldo",
                "scene_subject": "Cristiano Ronaldo",
                "domain_anchor": "Football players",
                "youtube": {"primary": "Cristiano Ronaldo goal real footage", "supporting": "Cristiano Ronaldo highlights", "fallback": "Cristiano Ronaldo footage"},
                "web": {"primary": "Cristiano Ronaldo celebration photo", "supporting": "Cristiano Ronaldo portrait", "fallback": "Cristiano Ronaldo"},
                "stock": {"primary_query": "football player goal", "alt_query": "stadium celebration", "fallback_query": "soccer action", "subject": "people", "category": "sports", "setting": "outdoor"},
            },
            {
                "section_subject": "Kylian Mbappe",
                "scene_subject": "Kylian Mbappe",
                "domain_anchor": "Football players",
                "youtube": {"primary": "Kylian Mbappe sprint real footage", "supporting": "Kylian Mbappe highlights", "fallback": "Kylian Mbappe footage"},
                "web": {"primary": "Kylian Mbappe action photo", "supporting": "Kylian Mbappe portrait", "fallback": "Kylian Mbappe"},
                "stock": {"primary_query": "football player sprint", "alt_query": "soccer speed", "fallback_query": "stadium action", "subject": "people", "category": "sports", "setting": "outdoor"},
            },
        ]

        bundle = server_app._adapt_scene_brain_plans(plans, scene_texts, "Football players", section_subjects, sections)

        for source in ("yt", "web", "stock"):
            self.assertEqual(len(bundle[source]), 2)
            self.assertEqual(bundle[source][0]["section_subject"], "Cristiano Ronaldo")
            self.assertEqual(bundle[source][1]["section_subject"], "Kylian Mbappe")
            self.assertEqual(bundle[source][0]["container_id"], "section_5")
            self.assertEqual(bundle[source][1]["container_id"], "section_4")
        self.assertIn("search_query", bundle["web"][0])
        self.assertIn("query_variants", bundle["web"][0])
        self.assertIn("youtube_query", bundle["yt"][0])
        self.assertIn("primary_query", bundle["stock"][0])
        self.assertIn("Kylian Mbappe", bundle["yt"][0]["forbidden_subjects"])


    def test_adapter_keeps_mechanical_fallback_marker_on_timed_out_chunk_rows(self):
        """A plan the brain filled mechanically for a timed-out chunk must
        stay marked as a fallback. Laundering it as directed is how
        word-salad queries got cached and reused for every re-run."""
        scene_texts = ["In February 2006, Amtrak ran 233 to 286 departures a day."]
        plans = [{
            "container_subject": "Amtrak",
            "scene_subject": "Amtrak departures",
            "youtube": {"primary": "Amtrak trains footage", "supporting": "Amtrak stations", "fallback": "Amtrak footage"},
            "web": {"primary": "Amtrak train photo", "supporting": "Amtrak station", "fallback": "Amtrak"},
            "stock": {"primary_query": "train station", "alt_query": "railway", "fallback_query": "train"},
            "_query_fallback": "incomplete_or_timed_out_chunk",
        }]
        bundle = server_app._adapt_scene_brain_plans(plans, scene_texts, "Amtrak", ["Amtrak"], [{}])
        for source in ("yt", "web", "stock"):
            self.assertEqual(bundle[source][0].get("_query_fallback"), "incomplete_or_timed_out_chunk")

    def test_shared_scene_brain_adapter_prefers_container_map_over_bad_scene_plan(self):
        scene_texts = [
            "Lionel Messi controls the tempo and waits for the final pass.",
            "For many fans he is simply the greatest footballer ever.",
        ]
        section_subjects = ["Lionel Messi", "Lionel Messi"]
        sections = [
            {
                "container_id": "messi",
                "container_subject": "Lionel Messi",
                "scene_subject": "Lionel Messi",
                "container_boundary": True,
                "youtube_primary": "Lionel Messi match footage",
                "youtube_supporting": "Lionel Messi goals highlights",
                "youtube_fallback": "Lionel Messi interview",
            },
            {
                "container_id": "messi",
                "container_subject": "Lionel Messi",
                "scene_subject": "Lionel Messi",
                "container_boundary": False,
                "youtube_primary": "Lionel Messi dribbling footage",
                "youtube_supporting": "Lionel Messi press conference",
                "youtube_fallback": "Lionel Messi football footage",
            },
        ]
        plans = [
            {
                "container_subject": "Cristiano Ronaldo",
                "scene_subject": "Cristiano Ronaldo",
                "domain_anchor": "Cristiano Ronaldo FIFA World Cup",
                "youtube": {"primary": "Cristiano Ronaldo highlights"},
            },
            {
                "container_subject": "For many fans he is simply",
                "scene_subject": "football stars",
                "domain_anchor": "Cristiano Ronaldo FIFA World Cup",
                "youtube": {"primary": "football stars universe"},
            },
        ]

        bundle = server_app._adapt_scene_brain_plans(
            plans,
            scene_texts,
            "Cristiano Ronaldo FIFA World Cup",
            section_subjects,
            sections,
        )

        self.assertEqual([row["section_subject"] for row in bundle["yt"]], ["Lionel Messi", "Lionel Messi"])
        self.assertEqual(bundle["yt"][0]["container_id"], "messi")
        self.assertIn("Lionel Messi", bundle["yt"][0]["youtube_query"])
        self.assertIn("Lionel Messi", bundle["yt"][1]["youtube_query"])
        self.assertNotIn("Cristiano Ronaldo", bundle["yt"][0]["youtube_query"])
        self.assertNotIn("universe", bundle["yt"][1]["youtube_query"].lower())

    @mock.patch("app._store_shared_plan_cache")
    @mock.patch("services.vidzeo_brain.VidzeoBrain")
    def test_shared_scene_brain_uses_directed_scene_subjects_without_container_stage(self, brain_cls, _store_cache):
        scene_texts = [
            "Taking the third spot is Kylian Mbappe, whose speed changes every match.",
            "Few players can match the French forward in open space.",
            "Finally Lionel Messi controls the tempo in tight spaces.",
        ]
        plans = [
            {
                "section_subject": "Kylian Mbappe",
                "container_subject": "Kylian Mbappe",
                "container_id": "mbappe",
                "container_boundary": True,
                "scene_subject": "Kylian Mbappe",
                "domain_anchor": "Football legends",
                "youtube": {"primary": "Kylian Mbappe speed highlights", "supporting": "Kylian Mbappe match footage", "fallback": "Kylian Mbappe football"},
            },
            {
                "section_subject": "Kylian Mbappe",
                "container_subject": "Kylian Mbappe",
                "container_id": "mbappe",
                "container_boundary": False,
                "scene_subject": "Kylian Mbappe",
                "domain_anchor": "Football legends",
                "youtube": {"primary": "Kylian Mbappe speed in open space", "supporting": "Kylian Mbappe football highlights", "fallback": "Kylian Mbappe match footage"},
            },
            {
                "section_subject": "Lionel Messi",
                "container_subject": "Lionel Messi",
                "container_id": "messi",
                "container_boundary": True,
                "scene_subject": "Lionel Messi",
                "domain_anchor": "Football legends",
                "youtube": {"primary": "Lionel Messi control", "supporting": "Lionel Messi highlights", "fallback": "Lionel Messi"},
            },
        ]
        brain = brain_cls.return_value
        brain.plan_scenes.return_value = plans

        bundle = server_app._generate_shared_scene_brain_bundle(
            "\n".join(scene_texts),
            "Football legends",
            scene_texts,
            {"openrouter_api_key": "openrouter-test-key", "llm_provider": "openrouter"},
        )

        self.assertIsNotNone(bundle)
        brain.plan_containers.assert_not_called()
        self.assertEqual([row["section_subject"] for row in bundle["yt"]], [
            "Kylian Mbappe",
            "Kylian Mbappe",
            "Lionel Messi",
        ])
        self.assertEqual(bundle["yt"][0]["container_id"], "mbappe")
        self.assertEqual(bundle["yt"][2]["container_id"], "messi")
        self.assertTrue(bundle["yt"][0]["container_boundary"])
        self.assertIn("Kylian Mbappe", bundle["yt"][0]["youtube_query"])
        self.assertNotIn("Taking", bundle["yt"][0]["youtube_query"])

    def test_shared_scene_brain_adapter_removes_wrong_global_person_from_local_queries(self):
        scene_texts = [
            "Erling Haaland rewrites scoring records.",
            "Lionel Messi lifts another trophy.",
        ]
        section_subjects = ["Erling Haaland", "Lionel Messi"]
        plans = [
            {
                "section_subject": "Erling Haaland",
                "scene_subject": "Erling Haaland",
                "domain_anchor": "Cristiano Ronaldo FIFA World Cup",
                "youtube": {
                    "primary": "cristiano Erling Haaland scoring records",
                    "supporting": "Cristiano Ronaldo Erling Haaland goal machine",
                    "fallback": "Erling Haaland football footage",
                },
            },
            {
                "section_subject": "Lionel Messi",
                "scene_subject": "Lionel Messi",
                "domain_anchor": "Cristiano Ronaldo FIFA World Cup",
                "youtube": {
                    "primary": "world cup messi Cristiano Ronaldo trophy collection",
                    "supporting": "lionel messi Cristiano Ronaldo trophy celebration",
                    "fallback": "Lionel Messi FIFA World Cup footage",
                },
            },
        ]

        bundle = server_app._adapt_scene_brain_plans(
            plans,
            scene_texts,
            "Cristiano Ronaldo FIFA World Cup",
            section_subjects,
            [],
        )

        haaland_query = bundle["yt"][0]["youtube_query"].lower()
        messi_query = bundle["yt"][1]["youtube_query"].lower()
        self.assertIn("erling haaland", haaland_query)
        self.assertNotIn("cristiano", haaland_query)
        self.assertNotIn("ronaldo", haaland_query)
        self.assertIn("lionel messi", messi_query)
        self.assertNotIn("cristiano", messi_query)
        self.assertNotIn("ronaldo", messi_query)
        self.assertEqual(bundle["yt"][0]["global_topic"], "Erling Haaland")
        self.assertEqual(bundle["yt"][1]["global_topic"], "Lionel Messi")

    @mock.patch("services.vidzeo_brain.VidzeoBrain")
    def test_shared_scene_brain_fails_closed_when_directed_scene_plan_is_incomplete(self, brain_cls):
        scene_texts = [
            "Kylian Mbappe breaks away with speed.",
            "The French forward finishes on the biggest stage.",
            "Lionel Messi controls the tempo.",
        ]
        brain = brain_cls.return_value
        brain.plan_scenes.return_value = []

        with self.assertRaises(server_app.OpenRouterScenePlanningError):
            server_app._generate_shared_scene_brain_bundle(
                "\n".join(scene_texts),
                "Football legends",
                scene_texts,
                {"openrouter_api_key": "openrouter-key", "llm_provider": "openrouter"},
            )
        brain.plan_containers.assert_not_called()

    @mock.patch("services.vidzeo_brain.VidzeoBrain")
    def test_direct_youtube_query_generation_fails_closed_on_incomplete_shared_brain(self, brain_cls):
        scene_texts = [
            "Kylian Mbappe breaks away with speed.",
            "The French forward finishes on the biggest stage.",
            "Lionel Messi controls the tempo.",
        ]
        brain = brain_cls.return_value
        brain.plan_scenes.return_value = []

        with self.assertRaises(server_app.OpenRouterScenePlanningError):
            server_app._generate_fast_youtube_scene_queries(
                "\n".join(scene_texts),
                "Football legends",
                scene_texts,
                {"openrouter_api_key": "openrouter-key", "llm_provider": "openrouter"},
                silent=True,
            )

        brain.plan_containers.assert_not_called()

    def test_youtube_branch_uses_neutral_title_for_multi_container_llm_queries(self):
        scene_texts = [
            "Erling Haaland rewrites scoring records.",
            "Lionel Messi lifts another trophy.",
        ]
        scene_durations = [2.0, 2.0]
        captured = {}

        def fake_get_youtube_clips(**kwargs):
            captured["title"] = kwargs.get("title")
            captured["overrides"] = kwargs.get("scene_query_overrides")
            return [None] * len(scene_texts)

        with mock.patch("services.youtube_clips_service.get_youtube_clips", side_effect=fake_get_youtube_clips):
            server_app._run_youtube_clips_branch(
                scene_texts,
                scene_durations,
                {
                    "image_source": "YouTube Clips",
                    "_topic_anchor": "Cristiano Ronaldo FIFA World Cup",
                    "_skip_sync_topic_anchor": True,
                    "_yt_scene_query_overrides": [
                        {
                            "_llm": True,
                            "section_subject": "Erling Haaland",
                            "scene_subject": "Erling Haaland",
                            "youtube_query": "Erling Haaland goals highlights",
                        },
                        {
                            "_llm": True,
                            "section_subject": "Lionel Messi",
                            "scene_subject": "Lionel Messi",
                            "youtube_query": "Lionel Messi goals highlights",
                        },
                    ],
                },
                "Every football generation has its legends",
                "\n".join(scene_texts),
            )

        self.assertEqual(captured["title"], "Every football generation has its legends")
        self.assertEqual(len(captured["overrides"]), 2)

    def test_youtube_branch_defaults_to_same_source_reuse(self):
        captured = {}

        def fake_get_youtube_clips(**kwargs):
            captured["reuse"] = kwargs.get("allow_same_source_reuse")
            return [None]

        with mock.patch("services.youtube_clips_service.get_youtube_clips", side_effect=fake_get_youtube_clips):
            server_app._run_youtube_clips_branch(
                ["RMS Titanic departed Southampton."],
                [4.0],
                {
                    "image_source": "YouTube Clips",
                    "_yt_scene_query_overrides": [{"youtube_query": "RMS Titanic Southampton archival footage"}],
                },
                "RMS Titanic",
                "RMS Titanic departed Southampton.",
            )

        self.assertTrue(captured["reuse"])

    def test_semantic_containers_detect_paragraph_entity_shifts_without_number_markers(self):
        from services.semantic_query_agent import detect_list_sections

        scene_texts = [
            "Every football generation has its legends and new stars.",
            "Erling Haaland rewrites scoring records with brutal power.",
            "The Norwegian striker keeps attacking space behind defenders.",
            "Jude Bellingham controls midfield with calm movement.",
            "Kylian Mbappe stretches every back line with speed.",
            "Lionel Messi still turns tight spaces into decisive moments.",
        ]

        sections = detect_list_sections(scene_texts, "Cristiano Ronaldo FIFA World Cup")
        subjects = [row.get("container_subject") for row in sections]

        self.assertIn("Erling Haaland", subjects[1])
        self.assertIn("Erling Haaland", subjects[2])
        self.assertIn("Jude Bellingham", subjects[3])
        self.assertIn("Kylian Mbappe", subjects[4])
        self.assertIn("Lionel Messi", subjects[5])
        self.assertNotIn("Cristiano Ronaldo", subjects[5])

    def test_youtube_query_repair_ignores_weak_mechanical_section_anchors(self):
        scene_texts = [
            "Every football generation has its legends.",
            "Today, the sport is filled with names fighting for the top.",
            "The best footballers are judged by what they do in pressure moments.",
            "Erling Haaland rewrites scoring records with brutal power.",
            "The Norwegian striker keeps attacking space behind defenders.",
            "Lionel Messi still turns tight spaces into decisive moments.",
        ]
        rows = [
            {"_mechanical": True, "section_subject": "generation", "youtube_query": "generation football raw footage"},
            {"_mechanical": True, "section_subject": "today", "youtube_query": "today drone skyline raw footage"},
            {"_mechanical": True, "section_subject": "sport", "youtube_query": "sport footballers raw footage"},
            {"_mechanical": True, "section_subject": "Erling Haaland", "youtube_query": "Erling Haaland scoring raw footage"},
            {"_mechanical": True, "section_subject": "Erling Haaland", "youtube_query": "Erling Haaland striker raw footage"},
            {"_mechanical": True, "section_subject": "Lionel Messi", "youtube_query": "Lionel Messi trophy raw footage"},
        ]
        repaired = server_app._repair_primary_anchor_query_rows(
            rows,
            "Cristiano Ronaldo FIFA World Cup",
            scene_texts,
            list_sections=None,
        )

        self.assertNotEqual(repaired[0]["section_subject"].lower(), "generation")
        self.assertNotEqual(repaired[1]["section_subject"].lower(), "today")
        self.assertNotEqual(repaired[2]["section_subject"].lower(), "sport")
        self.assertIn("Erling Haaland", repaired[3]["section_subject"])
        self.assertIn("Erling Haaland", repaired[4]["section_subject"])
        self.assertIn("Lionel Messi", repaired[5]["youtube_query"])
        self.assertNotIn("Cristiano Ronaldo", repaired[5]["youtube_query"])

    def test_youtube_query_repair_drops_weak_llm_section_anchors_from_football_listicle(self):
        scene_texts = [
            "Taking the third spot is Kylian Mbappe. Few players have that combination of speed and finishing.",
            "Whether it is a World Cup final or a Champions League night, Mbappe delivers.",
            "And at number one is Lionel Messi. His vision and dribbling decide matches.",
        ]
        rows = [
            {
                "_llm": True,
                "section_subject": "combination",
                "scene_subject": "combination",
                "youtube_query": "combination speed finishing footage",
                "youtube_supporting_query": "combination football highlights",
                "youtube_fallback_query": "combination archive footage",
            },
            {
                "_llm": True,
                "section_subject": "whether",
                "scene_subject": "Mbappe",
                "youtube_query": "whether world cup final footage",
                "youtube_supporting_query": "mbapp league night official footage",
                "youtube_fallback_query": "mbappe football footage",
            },
            {
                "_llm": True,
                "section_subject": "probably",
                "scene_subject": "Messi",
                "youtube_query": "probably trophy football footage",
                "youtube_supporting_query": "lionel messi dribbling footage",
                "youtube_fallback_query": "messi archive footage",
            },
        ]

        repaired = server_app._repair_primary_anchor_query_rows(
            rows,
            "Cristiano Ronaldo FIFA World Cup",
            scene_texts,
            list_sections=None,
        )

        self.assertIn("Kylian Mbappe", repaired[0]["section_subject"])
        self.assertIn("Kylian Mbappe", repaired[0]["youtube_query"])
        self.assertIn("Kylian Mbappe", repaired[1]["section_subject"])
        self.assertIn("Kylian Mbappe", repaired[1]["youtube_query"])
        self.assertIn("Lionel Messi", repaired[2]["section_subject"])
        self.assertIn("Lionel Messi", repaired[2]["youtube_query"])
        for row in repaired:
            joined = " ".join([
                row.get("section_subject", ""),
                row.get("youtube_query", ""),
                row.get("youtube_supporting_query", ""),
                row.get("youtube_fallback_query", ""),
            ]).lower()
            self.assertNotIn("cristiano ronaldo", joined)
            self.assertNotIn("probably", joined)
            self.assertNotIn("combination", row.get("section_subject", "").lower())

    def test_container_guard_expands_aliases_and_blocks_cross_section_subjects(self):
        from services.container_guard import canonical_container_subject, enforce_container_isolation

        rows = [
            {
                "section_subject": "cristiano",
                "youtube_query": "cristiano ronaldo goal footage messi",
                "web_query": "ronaldo celebration photo",
            },
            {
                "section_subject": "World Cup Messi",
                "youtube_query": "Cristiano Ronaldo trophy Messi archive footage",
                "web_query": "ronaldo messi trophy photo",
            },
        ]
        sections = [
            {"container_subject": "cristiano", "is_intro": False, "has_explicit_marker": True},
            {"container_subject": "World Cup Messi", "is_intro": False, "has_explicit_marker": True},
        ]
        scene_texts = [
            "Cristiano Ronaldo scores again.",
            "Lionel Messi turns tight spaces into decisive moments.",
        ]

        guarded = enforce_container_isolation(rows, sections, scene_texts=scene_texts, topic="Football legends")

        self.assertEqual(guarded[0]["section_subject"], "Cristiano Ronaldo")
        self.assertEqual(guarded[1]["section_subject"], "Lionel Messi")
        self.assertTrue(guarded[1]["youtube_query"].startswith("Lionel Messi"))
        self.assertNotIn("Cristiano", guarded[1]["youtube_query"])
        self.assertNotIn("Ronaldo", guarded[1]["youtube_query"])
        self.assertIn("Cristiano Ronaldo", guarded[1]["forbidden_subjects"])
        self.assertEqual(
            canonical_container_subject(
                "Few",
                "Few players can sprint like Kylian Mbappe in open space.",
                "Cristiano Ronaldo FIFA World Cup",
            ),
            "Kylian Mbappe",
        )
        self.assertEqual(
            canonical_container_subject(
                "Give",
                "Give Erling Haaland a chance and he scores.",
                "Cristiano Ronaldo FIFA World Cup",
            ),
            "Erling Haaland",
        )

    def test_rotation_rescue_refuses_wrong_container_clip(self):
        paths = ["C:/tmp/ronaldo_clip.mp4", None]
        labels = ["YouTube Clips", ""]

        filled = server_app._fill_unresolved_with_rotating_video_pool(
            final_paths=paths,
            source_labels=labels,
            unresolved_indices=[1],
            scene_texts=["Cristiano Ronaldo scores.", "Lionel Messi dribbles."],
            require_scene_overlap=False,
            section_subjects=["Cristiano Ronaldo", "Lionel Messi"],
        )

        self.assertEqual(filled, 0)
        self.assertIsNone(paths[1])

        paths = ["C:/tmp/messi_clip.mp4", None, None]
        labels = ["YouTube Clips", "", ""]
        filled = server_app._fill_unresolved_with_rotating_video_pool(
            final_paths=paths,
            source_labels=labels,
            unresolved_indices=[2],
            scene_texts=["Lionel Messi scores.", "A neutral bridge scene.", "Lionel Messi dribbles."],
            require_scene_overlap=False,
            max_reuse_override=2,
            section_subjects=["Lionel Messi", "", "Lionel Messi"],
        )

        self.assertEqual(filled, 0)
        self.assertIsNone(paths[2])

    def test_rotation_rescue_spreads_fill_across_available_video_pool(self):
        paths = [
            "C:/tmp/titanic_a.mp4",
            "C:/tmp/titanic_b.mp4",
            "C:/tmp/titanic_c.mp4",
            None,
            None,
            None,
        ]
        labels = ["YouTube Clips", "YouTube Clips", "YouTube Clips", "", "", ""]

        filled = server_app._fill_unresolved_with_rotating_video_pool(
            final_paths=paths,
            source_labels=labels,
            unresolved_indices=[3, 4, 5],
            scene_texts=["Titanic documentary"] * 6,
            require_scene_overlap=False,
            max_reuse_override=2,
            section_subjects=None,
        )

        self.assertEqual(filled, 0)
        self.assertEqual(paths[3:], [None, None, None])

    def test_youtube_rotation_rescue_never_silently_uses_stock_video(self):
        paths = ["C:/tmp/stock_only.mp4", None]
        labels = ["Stock Media", ""]

        filled = server_app._fill_unresolved_with_rotating_video_pool(
            final_paths=paths,
            source_labels=labels,
            unresolved_indices=[1],
            preferred_label="YouTube Clips",
            label_contains="YouTube",
            require_scene_overlap=False,
            allow_any_video_fallback=False,
        )

        self.assertEqual(filled, 0)
        self.assertIsNone(paths[1])

    def test_query_repair_expands_short_sports_names_to_full_names(self):
        scene_texts = [
            "Messi still owns the biggest stages with control and finishing.",
            "Mbappe stretches every back line with speed.",
        ]
        rows = [
            {
                "_llm": True,
                "section_subject": "Messi",
                "scene_subject": "Messi",
                "youtube_query": "messi trophy collection",
                "youtube_supporting_query": "world cup messi celebration",
                "youtube_fallback_query": "messi footage",
            },
            {
                "_llm": True,
                "section_subject": "Mbappe",
                "scene_subject": "Mbappe",
                "youtube_query": "mbappe sprint goal",
                "youtube_supporting_query": "mbappe speed highlights",
                "youtube_fallback_query": "mbappe footage",
            },
        ]

        repaired = server_app._repair_primary_anchor_query_rows(
            rows,
            "Cristiano Ronaldo FIFA World Cup",
            scene_texts,
            list_sections=None,
        )

        self.assertIn("Lionel Messi", repaired[0]["section_subject"])
        self.assertIn("Lionel Messi", repaired[0]["youtube_query"])
        self.assertIn("Lionel Messi", repaired[0]["youtube_supporting_query"])
        self.assertIn("Kylian Mbappe", repaired[1]["section_subject"])
        self.assertIn("Kylian Mbappe", repaired[1]["youtube_query"])
        self.assertIn("Kylian Mbappe", repaired[1]["youtube_supporting_query"])

    @mock.patch("app._generate_fast_stock_queries")
    @mock.patch("app._generate_fast_youtube_scene_queries")
    @mock.patch("app._generate_fast_scene_queries")
    @mock.patch("app._generate_shared_scene_brain_bundle")
    def test_source_query_bundle_prefers_shared_brain_over_separate_planners(
        self,
        mock_shared_brain,
        mock_web_planner,
        mock_yt_planner,
        mock_stock_planner,
    ):
        shared = {
            "web": [{"_llm": True, "scene_number": 1, "search_query": "Cristiano Ronaldo photo"}],
            "yt": [{"_llm": True, "scene_number": 1, "youtube_query": "Cristiano Ronaldo real footage"}],
            "stock": [{"_llm": True, "scene_number": 1, "primary_query": "football player goal"}],
            "_planner": {"status": "directed", "provider": "openrouter", "model": "openai/gpt-6-luna"},
        }
        mock_shared_brain.return_value = shared

        bundle = server_app._generate_source_query_bundle_parallel(
            "Number one is Cristiano Ronaldo.",
            "Football players",
            ["Number one is Cristiano Ronaldo."],
            {"openrouter_api_key": "openrouter-key", "llm_provider": "openrouter"},
            needs_web=True,
            needs_yt=True,
            needs_stock=True,
        )

        # The shared planner's source rows remain unchanged; the API adds
        # route/status metadata so downstream UI and saved jobs can report
        # whether queries came from the LLM or a fallback.
        for source in ("web", "yt", "stock"):
            self.assertEqual(bundle[source], shared[source])
        self.assertEqual(bundle["_planner"]["status"], "directed")
        self.assertEqual(bundle["_planner"]["provider"], "openrouter")
        mock_web_planner.assert_not_called()
        mock_yt_planner.assert_not_called()
        mock_stock_planner.assert_not_called()

    @mock.patch("app._fill_unresolved_with_general_web_fallback")
    def test_render_qa_repairs_invalid_and_duplicate_media_before_ffmpeg(self, mock_web_fallback):
        existing_path = server_app.TEMP_DIR / "render_qa_existing.jpg"
        fallback_path = server_app.TEMP_DIR / "render_qa_fallback.jpg"
        fallback_path_2 = server_app.TEMP_DIR / "render_qa_fallback_2.jpg"
        existing_path.write_bytes(b"x" * 128)
        fallback_path.write_bytes(b"y" * 128)
        fallback_path_2.write_bytes(b"z" * 128)

        def fake_web_fallback(final_paths, source_labels, unresolved_indices, **_kwargs):
            replacements = [fallback_path, fallback_path_2]
            for local_idx, idx in enumerate(unresolved_indices):
                final_paths[idx] = str(replacements[local_idx])
                source_labels[idx] = "Web Fallback Rescue"
            return len(unresolved_indices)

        mock_web_fallback.side_effect = fake_web_fallback

        media_plan = {
            "media_paths": [str(existing_path), str(existing_path), str(server_app.TEMP_DIR / "missing.jpg")],
            "source_labels": ["Web Images", "Web Images", "Web Images"],
            "prompt_bundle": {"beat_texts": ["one", "two", "three"]},
        }
        repaired = server_app._repair_media_plan_for_render(
            media_plan=media_plan,
            audio_plan={"scene_texts": ["one", "two", "three"]},
            job_config={},
            render_resolution=(1280, 720),
            title="QA",
            script="A valid script",
            target_scene_count=3,
        )

        keys = [server_app._media_duplicate_key(path) for path in repaired["media_paths"]]
        self.assertEqual(len(keys), 3)
        self.assertEqual(len(set(keys)), 3)
        self.assertTrue(all(server_app._media_path_exists_for_render(path) for path in repaired["media_paths"]))
        self.assertNotIn("Placeholder Rescue", repaired["source_labels"])
        self.assertEqual(repaired["source_labels"].count("Web Fallback Rescue"), 2)

    @mock.patch("app._fill_unresolved_with_general_web_fallback")
    def test_custom_media_contract_preserves_supplied_assets_without_web_rescue(self, mock_web_fallback):
        from PIL import Image

        first_path = server_app.TEMP_DIR / "custom_contract_first.jpg"
        second_path = server_app.TEMP_DIR / "custom_contract_second.jpg"
        Image.new("RGB", (160, 90), color=(30, 70, 120)).save(first_path, "JPEG")
        Image.new("RGB", (160, 90), color=(120, 70, 30)).save(second_path, "JPEG")

        repaired = server_app._repair_media_plan_for_render(
            media_plan={
                "media_paths": [str(first_path), str(second_path)],
                "source_labels": ["Custom Images", "Custom Images"],
                "prompt_bundle": {"beat_texts": ["opening", "turn"]},
            },
            audio_plan={
                "scene_texts": ["opening", "turn", "proof", "close"],
                "scene_durations": [2.0, 2.0, 2.0, 2.0],
            },
            job_config={"image_source": "Custom Images"},
            render_resolution=(1280, 720),
            title="GTA VI",
            script="A custom-media documentary.",
            target_scene_count=4,
        )

        self.assertEqual(
            repaired["media_paths"],
            [str(first_path), str(second_path), str(first_path), str(second_path)],
        )
        self.assertEqual(repaired["source_labels"], ["Custom Images"] * 4)
        self.assertEqual(
            repaired["metadata"]["custom_media_contract"]["mode"],
            "closed",
        )
        mock_web_fallback.assert_not_called()

    def test_mixed_media_keeps_real_reused_media_after_final_qa(self):
        from PIL import Image

        path = server_app.TEMP_DIR / "render_qa_mixed_reuse.jpg"
        Image.new("RGB", (96, 96), color=(120, 80, 40)).save(path)
        media_plan = {
            "media_paths": [str(path), str(path)],
            "source_labels": ["Web Images", "Web Images"],
            "prompt_bundle": {"beat_texts": ["one", "two"]},
        }

        repaired = server_app._repair_media_plan_for_render(
            media_plan=media_plan,
            audio_plan={"scene_texts": ["one", "two"], "scene_durations": [2.0, 2.0]},
            job_config={"image_source": "Mixed Media"},
            render_resolution=(1280, 720),
            title="Mixed QA",
            script="A valid mixed media script.",
            target_scene_count=2,
        )

        self.assertEqual(repaired["media_paths"], [str(path), str(path)])

    @mock.patch("app._run_youtube_render_repair", return_value=0)
    @mock.patch("app._fill_unresolved_with_general_web_fallback")
    def test_render_qa_repairs_same_visual_saved_under_different_names(self, mock_web_fallback, _mock_yt_repair):
        from PIL import Image

        first_path = server_app.TEMP_DIR / "render_qa_same_visual_a.jpg"
        second_path = server_app.TEMP_DIR / "render_qa_same_visual_b.jpg"
        image = Image.new("RGB", (96, 96), color=(120, 80, 40))
        image.save(first_path, "JPEG")
        image.save(second_path, "JPEG")

        def fake_web_fallback(final_paths, source_labels, unresolved_indices, **_kwargs):
            for idx in unresolved_indices:
                final_paths[idx] = str(first_path)
                source_labels[idx] = "Web Fallback Rescue"
            return len(unresolved_indices)

        mock_web_fallback.side_effect = fake_web_fallback

        media_plan = {
            "media_paths": [str(first_path), str(second_path)],
            "source_labels": ["Web Images", "Web Images"],
            "prompt_bundle": {"beat_texts": ["one", "two"]},
        }
        with self.assertRaises(MediaContractError) as raised:
            server_app._repair_media_plan_for_render(
                media_plan=media_plan,
                audio_plan={"scene_texts": ["one", "two"]},
                job_config={"strict_visual_dedupe": True},
                render_resolution=(1280, 720),
                title="QA",
                script="A valid script",
                target_scene_count=2,
            )

        self.assertEqual(
            raised.exception.detail.get("fallback_policy"),
            "no_blank_or_text_card_fallbacks",
        )

    @mock.patch("app._run_targeted_stock_rescue", return_value={})
    @mock.patch("app._run_youtube_render_repair", return_value=0)
    def test_youtube_render_qa_does_not_replay_a_clip_when_all_repairs_fail(self, _mock_yt_repair, _mock_stock_rescue):
        existing_path = server_app.TEMP_DIR / "render_qa_existing_youtube.mp4"
        missing_path = server_app.TEMP_DIR / "render_qa_missing_youtube.mp4"
        existing_path.write_bytes(b"x" * 256)

        media_plan = {
            "media_paths": [str(existing_path), str(missing_path)],
            "source_labels": ["YouTube Clips", "YouTube Clips"],
            "prompt_bundle": {"beat_texts": ["one", "two"]},
            "metadata": {},
        }

        with self.assertRaises(MediaContractError):
            server_app._repair_media_plan_for_render(
                media_plan=media_plan,
                audio_plan={"scene_texts": ["one", "two"], "scene_durations": [2.0, 2.0]},
                job_config={"image_source": "YouTube Clips"},
                render_resolution=(1280, 720),
                title="QA",
                script="A valid script",
                target_scene_count=2,
            )
        self.assertEqual(media_plan["media_paths"][1], str(missing_path))
        self.assertNotIn("real_media_render_reuse_slots", media_plan["metadata"])

    @mock.patch("app._fill_unresolved_with_general_web_fallback")
    @mock.patch("app._run_targeted_stock_rescue", return_value={})
    @mock.patch("app._run_youtube_render_repair", return_value=0)
    def test_youtube_render_qa_never_uses_web_when_youtube_only_fallback_disabled(
        self,
        _mock_yt_repair,
        _mock_stock_rescue,
        mock_web_fallback,
    ):
        existing_path = server_app.TEMP_DIR / "render_qa_existing_youtube_no_web.mp4"
        missing_path = server_app.TEMP_DIR / "render_qa_missing_youtube_no_web.mp4"
        existing_path.write_bytes(b"x" * 256)

        media_plan = {
            "media_paths": [str(existing_path), str(missing_path)],
            "source_labels": ["YouTube Clips", "YouTube Clips"],
            "prompt_bundle": {"beat_texts": ["John Cena entrance", "John Cena training"]},
            "metadata": {},
        }

        with self.assertRaises(MediaContractError):
            server_app._repair_media_plan_for_render(
                media_plan=media_plan,
                audio_plan={"scene_texts": ["John Cena entrance", "John Cena training"], "scene_durations": [2.0, 2.0]},
                job_config={"image_source": "YouTube Clips"},
                render_resolution=(1280, 720),
                title="John Cena",
                script="A valid John Cena wrestling script",
                target_scene_count=2,
            )

        mock_web_fallback.assert_not_called()
        self.assertEqual(media_plan["media_paths"][1], str(missing_path))

    @mock.patch("app._run_targeted_stock_rescue", return_value={})
    @mock.patch("app._run_youtube_render_repair", return_value=0)
    def test_youtube_render_qa_caps_exact_clip_reuse(self, _mock_yt_repair, _mock_stock_rescue):
        existing_path = server_app.TEMP_DIR / "render_qa_reused_youtube.mp4"
        existing_path.write_bytes(b"x" * 256)

        media_plan = {
            "media_paths": [str(existing_path)] * 6,
            "source_labels": ["YouTube Clips"] * 6,
            "prompt_bundle": {"beat_texts": [f"scene {idx}" for idx in range(6)]},
            "metadata": {},
        }

        self.assertEqual(
            server_app._final_video_reuse_cap({
                "image_source": "YouTube Clips",
                "yt_allow_same_source_reuse": True,
                "max_final_video_reuse_per_clip": 6,
            }),
            1,
        )
        with self.assertRaises(MediaContractError):
            server_app._repair_media_plan_for_render(
                media_plan=media_plan,
                audio_plan={
                    "scene_texts": [f"scene {idx}" for idx in range(6)],
                    "scene_durations": [2.0] * 6,
                },
                job_config={
                    "image_source": "YouTube Clips",
                    "yt_allow_same_source_reuse": True,
                    "max_final_video_reuse_per_clip": 6,
                },
                render_resolution=(1280, 720),
                title="Grand Theft Auto VI",
                script="A valid GTA VI script",
                target_scene_count=6,
            )

    @mock.patch("app._run_targeted_stock_rescue", return_value={})
    @mock.patch("app._run_youtube_render_repair", return_value=0)
    @mock.patch("app._fill_unresolved_with_general_web_fallback")
    @mock.patch("app._create_emergency_render_asset")
    def test_youtube_render_qa_rejects_web_fallback_when_no_real_clip_exists(
        self,
        mock_emergency_asset,
        mock_web_fallback,
        _mock_yt_repair,
        _mock_stock_rescue,
    ):
        missing_path = server_app.TEMP_DIR / "render_qa_missing_youtube_only.mp4"
        fallback_path = server_app.TEMP_DIR / "render_qa_youtube_web_fallback.jpg"
        fallback_path.write_bytes(b"web" * 128)

        def fake_web_fallback(final_paths, source_labels, unresolved_indices, **_kwargs):
            for idx in unresolved_indices:
                final_paths[idx] = str(fallback_path)
                source_labels[idx] = "Web Fallback Rescue"
            return len(unresolved_indices)

        mock_web_fallback.side_effect = fake_web_fallback
        media_plan = {
            "media_paths": [str(missing_path)],
            "source_labels": ["YouTube Clips"],
            "prompt_bundle": {"beat_texts": ["one"]},
            "metadata": {},
        }

        with self.assertRaises(MediaContractError):
            server_app._repair_media_plan_for_render(
                media_plan=media_plan,
                audio_plan={"scene_texts": ["one"], "scene_durations": [2.0]},
                job_config={"image_source": "YouTube Clips"},
                render_resolution=(1280, 720),
                title="QA",
                script="A valid script",
                target_scene_count=1,
            )

        mock_emergency_asset.assert_not_called()
        mock_web_fallback.assert_not_called()

    def test_one_scene_intro_placeholder_uses_script_anchor(self):
        config = {
            "_topic_anchor": "",
            "_yt_scene_query_overrides": [{
                "_llm": True,
                "container_id": "intro",
                "is_intro": True,
                "section_subject": "intro",
                "scene_subject": "intro",
                "youtube_query": "intro real footage",
            }],
        }
        captured = {}

        def fake_get_youtube_clips(**kwargs):
            rows = kwargs["scene_query_overrides"]
            captured["row"] = rows[0]
            return [None]

        with mock.patch("services.youtube_clips_service.get_youtube_clips", side_effect=fake_get_youtube_clips):
            result = server_app._run_youtube_clips_branch(
                ["The RMS Titanic sank after striking an iceberg."],
                [3.0],
                config,
                "Video",
                "The RMS Titanic sank after striking an iceberg.",
            )

        self.assertEqual(result, [None])
        self.assertEqual(captured["row"]["domain_anchor"], "RMS Titanic")
        self.assertEqual(captured["row"]["container_id"], "primary_topic")
        self.assertNotIn("intro", captured["row"]["youtube_query"].lower())

    @_owned_youtube_environment()
    def test_youtube_branch_omits_scene_first_shared_pool_and_scales_wall_timeout(self):
        scene_texts = [f"John Cena wrestling scene {idx}" for idx in range(20)]
        scene_durations = [2.0] * len(scene_texts)
        captured = {}

        def fake_get_youtube_clips(**kwargs):
            captured["pool_first"] = kwargs.get("pool_first")
            captured["shared_pool"] = os.environ.get("VIDZEO_YT_SCENE_FIRST_SHARED_POOL")
            captured["pool_queries"] = os.environ.get("VIDZEO_YT_SCENE_FIRST_POOL_QUERIES")
            captured["selection_budget"] = os.environ.get("VIDZEO_YT_SELECTION_BUDGET_SECONDS")
            captured["selection_concurrency"] = os.environ.get("VIDZEO_YT_SELECTION_CONCURRENCY")
            return [None] * len(scene_texts)

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VIDZEO_YT_SCENE_FIRST_SHARED_POOL", None)
            os.environ.pop("VIDZEO_YT_SCENE_FIRST_POOL_QUERIES", None)
            with mock.patch("services.youtube_clips_service.get_youtube_clips", side_effect=fake_get_youtube_clips):
                result = server_app._run_youtube_clips_branch(
                    scene_texts,
                    scene_durations,
                    {
                        "image_source": "YouTube Clips",
                        "_yt_scene_query_overrides": [{"youtube_query": "john cena wrestling footage"} for _ in scene_texts],
                        "yt_branch_wall_timeout_seconds": 999,
                    },
                    "John Cena",
                    "\n".join(scene_texts),
                )

        self.assertEqual(result, [None] * len(scene_texts))
        self.assertTrue(captured["pool_first"])
        self.assertIsNone(captured["shared_pool"])
        self.assertIsNone(captured["pool_queries"])
        self.assertGreaterEqual(int(captured["selection_concurrency"]), 8)
        self.assertLessEqual(int(captured["selection_concurrency"]), 18)
        self.assertLessEqual(float(captured["selection_budget"]), 180.0)

    @_owned_youtube_environment()
    def test_eleven_scene_youtube_job_uses_medium_download_budget(self):
        scene_texts = [f"RMS Titanic archival footage scene {idx}" for idx in range(11)]
        captured = {}

        def fake_get_youtube_clips(**kwargs):
            captured["batch_hard_timeout"] = os.environ.get("VIDZEO_YT_BATCH_HARD_TIMEOUT")
            captured["fast_soft_timeout"] = os.environ.get("VIDZEO_YT_FAST_SOFT_TIMEOUT")
            captured["active_download_grace"] = os.environ.get("VIDZEO_YT_ACTIVE_DOWNLOAD_GRACE_SECONDS")
            return [None] * len(scene_texts)

        with mock.patch("services.youtube_clips_service.get_youtube_clips", side_effect=fake_get_youtube_clips):
            result = server_app._run_youtube_clips_branch(
                scene_texts,
                [2.0] * len(scene_texts),
                {
                    "image_source": "YouTube Clips",
                    "_yt_scene_query_overrides": [{"youtube_query": "RMS Titanic archival footage"} for _ in scene_texts],
                    "yt_branch_wall_timeout_seconds": 60,
                },
                "RMS Titanic",
                "RMS Titanic archival footage.",
            )

        self.assertEqual(result, [None] * len(scene_texts))
        # Quality-first adaptive streams need a real transfer/merge budget;
        # the retired 180s deadline cancelled healthy 1080p sources.
        self.assertGreaterEqual(float(captured["batch_hard_timeout"]), 360.0)
        self.assertEqual(captured["fast_soft_timeout"], "180")
        self.assertGreaterEqual(float(captured["active_download_grace"]), 120.0)

    @_owned_youtube_environment()
    def test_long_youtube_branch_uses_scene_aware_source_pool_mode(self):
        scene_texts = [f"Erling Haaland football scene {idx}" for idx in range(51)]
        scene_durations = [2.0] * len(scene_texts)
        captured = {}

        def fake_get_youtube_clips(**kwargs):
            captured["scene_first_required"] = os.environ.get("VIDZEO_YT_SCENE_FIRST_REQUIRED")
            captured["quick_source_pool"] = os.environ.get("VIDZEO_YT_QUICK_SOURCE_POOL")
            captured["selection_budget"] = os.environ.get("VIDZEO_YT_SELECTION_BUDGET_SECONDS")
            captured["weak_network_proceed"] = os.environ.get("VIDZEO_YT_WEAK_NETWORK_PROCEED")
            captured["fast_clip_height"] = os.environ.get("VIDZEO_YT_FAST_CLIP_HEIGHT")
            captured["long_hard_cap"] = os.environ.get("VIDZEO_YT_LONG_JOB_HARD_CAP_SECONDS")
            captured["fast_soft_timeout"] = os.environ.get("VIDZEO_YT_FAST_SOFT_TIMEOUT")
            captured["active_download_grace"] = os.environ.get("VIDZEO_YT_ACTIVE_DOWNLOAD_GRACE_SECONDS")
            captured["keep_queue_below_target"] = os.environ.get("VIDZEO_YT_KEEP_QUEUE_BELOW_TARGET")
            captured["shared_source_cache"] = os.environ.get("VIDZEO_YT_SHARED_SOURCE_CACHE")
            captured["shared_source_min_reuse"] = os.environ.get("VIDZEO_YT_SHARED_SOURCE_MIN_REUSE")
            captured["shared_source_short_duration"] = os.environ.get("VIDZEO_YT_SHARED_SOURCE_ALWAYS_MAX_DURATION")
            captured["shared_source_max_duration"] = os.environ.get("VIDZEO_YT_SHARED_SOURCE_MAX_DURATION")
            captured["shared_source_max_mb"] = os.environ.get("VIDZEO_YT_SHARED_SOURCE_MAX_MB")
            captured["source_pool_prefill"] = os.environ.get("VIDZEO_YT_SOURCE_POOL_PREFILL_RATIO")
            captured["source_pool_min_fit"] = os.environ.get("VIDZEO_YT_SOURCE_POOL_MIN_FIT")
            captured["section_first_prefill"] = os.environ.get("VIDZEO_YT_SECTION_FIRST_PREFILL")
            captured["section_first_queries"] = os.environ.get("VIDZEO_YT_SECTION_FIRST_QUERIES_PER_SECTION")
            captured["section_first_budget"] = os.environ.get("VIDZEO_YT_SECTION_FIRST_BUDGET_SECONDS")
            captured["scene_scoped_rescue_queries"] = os.environ.get("VIDZEO_YT_SCENE_SCOPED_RESCUE_QUERIES")
            captured["rank_only_mode"] = os.environ.get("VIDZEO_YT_CONTAINER_RANK_ONLY_MODE")
            captured["search_concurrency"] = os.environ.get("VIDZEO_YT_SEARCH_CONCURRENCY")
            captured["download_concurrency"] = os.environ.get("VIDZEO_YT_DOWNLOAD_CONCURRENCY")
            captured["selection_concurrency"] = os.environ.get("VIDZEO_YT_SELECTION_CONCURRENCY")
            captured["batch_size"] = os.environ.get("VIDZEO_YT_BATCH_SIZE")
            captured["soft_coverage"] = kwargs.get("min_resolved_ratio_before_soft_stop")
            captured["allow_same_source_reuse"] = kwargs.get("allow_same_source_reuse")
            return [None] * len(scene_texts)

        with mock.patch("services.youtube_clips_service.get_youtube_clips", side_effect=fake_get_youtube_clips):
            result = server_app._run_youtube_clips_branch(
                scene_texts,
                scene_durations,
                {
                    "image_source": "YouTube Clips",
                    "yt_allow_same_source_reuse": True,
                    "_yt_scene_query_overrides": [{"youtube_query": "Erling Haaland football footage"} for _ in scene_texts],
                    "yt_branch_wall_timeout_seconds": 999,
                },
                "Football legends",
                "\n".join(scene_texts),
            )

        self.assertEqual(result, [None] * len(scene_texts))
        self.assertEqual(captured["scene_first_required"], "0")
        self.assertEqual(captured["quick_source_pool"], "1")
        self.assertEqual(captured["weak_network_proceed"], "1")
        self.assertEqual(captured["fast_clip_height"], "1080")
        self.assertGreaterEqual(float(captured["long_hard_cap"]), 390.0)
        self.assertGreaterEqual(float(captured["fast_soft_timeout"]), 120.0)
        self.assertGreaterEqual(float(captured["active_download_grace"]), 45.0)
        self.assertEqual(captured["keep_queue_below_target"], "1")
        self.assertEqual(captured["shared_source_cache"], "1")
        self.assertEqual(captured["shared_source_min_reuse"], "4")
        self.assertLessEqual(float(captured["shared_source_short_duration"]), 240.0)
        self.assertLessEqual(float(captured["shared_source_max_duration"]), 600.0)
        self.assertLessEqual(float(captured["shared_source_max_mb"]), 80.0)
        self.assertGreaterEqual(float(captured["source_pool_prefill"]), 0.85)
        self.assertLessEqual(float(captured["source_pool_min_fit"]), 20.0)
        self.assertEqual(captured["section_first_prefill"], "1")
        self.assertEqual(captured["section_first_queries"], "1")
        self.assertGreaterEqual(float(captured["section_first_budget"]), 10.0)
        self.assertLessEqual(float(captured["section_first_budget"]), 20.0)
        self.assertGreaterEqual(int(captured["scene_scoped_rescue_queries"]), 12)
        self.assertEqual(captured["rank_only_mode"], "1")
        self.assertGreaterEqual(int(captured["search_concurrency"]), 14)
        self.assertLessEqual(int(captured["download_concurrency"]), 6)
        self.assertGreaterEqual(int(captured["download_concurrency"]), 4)
        self.assertGreaterEqual(int(captured["selection_concurrency"]), 14)
        self.assertGreaterEqual(int(captured["batch_size"]), 48)
        self.assertGreaterEqual(float(captured["soft_coverage"]), 0.72)
        self.assertTrue(captured["allow_same_source_reuse"])
        self.assertLessEqual(float(captured["selection_budget"]), 180.0)

    @mock.patch("services.web_image_service.resolve_web_images_for_video_fast")
    @mock.patch("app._generate_fast_scene_queries")
    def test_web_fallback_generates_queries_only_for_unresolved_subset(self, mock_query_gen, mock_resolve_web):
        final_paths = [None, "C:\\temp\\existing.mp4", None, "C:\\temp\\existing2.mp4"]
        source_labels = ["", "YouTube Clips", "", "YouTube Clips"]
        scene_texts = ["scene one", "scene two", "scene three", "scene four"]
        mock_query_gen.return_value = [
            {"search_query": "scene one photo"},
            {"search_query": "scene three photo"},
        ]
        mock_resolve_web.return_value = {"image_paths": ["C:\\temp\\a.jpg", "C:\\temp\\b.jpg"]}

        with mock.patch("app._media_duplicate_key", side_effect=lambda path: str(path or "")):
            with mock.patch("app._render_image_phash", return_value=None):
                rescued = server_app._fill_unresolved_with_general_web_fallback(
                    final_paths,
                    source_labels,
                    [0, 2],
                    scene_texts,
                    (1280, 720),
                    "Subset Test",
                    "\n".join(scene_texts),
                    {},
                    force_web=True,
                )

        self.assertEqual(rescued, 2)
        args, _kwargs = mock_query_gen.call_args
        self.assertEqual(args[2], ["scene one", "scene three"])
        self.assertEqual(mock_resolve_web.call_args.kwargs["scene_texts"], ["scene one", "scene three"])

    @mock.patch("services.web_image_service.resolve_web_images_for_video_fast")
    @mock.patch("app._generate_fast_scene_queries")
    def test_web_fallback_repairs_weak_section_anchor_to_scene_person(self, mock_query_gen, mock_resolve_web):
        final_paths = [None]
        source_labels = [""]
        scene_texts = ["Few players can sprint like Kylian Mbappe in open space."]
        mock_query_gen.return_value = [{
            "section_subject": "Few",
            "scene_subject": "Few",
            "search_query": "Few speed portrait",
            "query_variants": ["Few speed portrait", "Few speed action"],
        }]
        mock_resolve_web.return_value = {"image_paths": [""]}

        server_app._fill_unresolved_with_general_web_fallback(
            final_paths,
            source_labels,
            [0],
            scene_texts,
            (1280, 720),
            "Cristiano Ronaldo FIFA World Cup",
            "\n".join(scene_texts),
            {
                "_topic_anchor": "Cristiano Ronaldo FIFA World Cup",
                "_yt_scene_query_overrides": [{
                    "section_subject": "Few",
                    "scene_subject": "Few",
                    "youtube_query": "Few speed footage",
                }],
            },
            force_web=True,
        )

        override = mock_resolve_web.call_args.kwargs["scene_queries_override"][0]
        joined = " ".join(override.get("query_variants") or []).lower()
        self.assertEqual(override["section_subject"], "Kylian Mbappe")
        self.assertIn("kylian mbappe", joined)
        self.assertNotIn("few", joined)

    @mock.patch("services.web_image_service.resolve_web_images_for_video_fast")
    @mock.patch("app._generate_fast_scene_queries")
    def test_terminal_web_fallback_preserves_product_requirement(self, mock_query_gen, mock_resolve_web):
        from services.visual_requirement import build_visual_requirement
        text = "The Tesla Model S followed in 2012."
        requirement = build_visual_requirement(title="Tesla", script=text, scene_text=text)
        mock_query_gen.return_value = [{
            "section_subject": "Tesla", "scene_subject": "Tesla",
            "search_query": "Tesla photograph", "query_variants": ["Tesla photograph"],
        }]
        mock_resolve_web.return_value = {"image_paths": [""]}
        server_app._fill_unresolved_with_general_web_fallback(
            [None], [""], [0], [text], (1280, 720), "Tesla", text,
            {"_topic_anchor": "Tesla", "_visual_requirements": [requirement]}, force_web=True,
        )
        row = mock_resolve_web.call_args.kwargs["scene_queries_override"][0]
        self.assertEqual(row["visual_requirement"], requirement)
        self.assertIn("model s", " ".join(row["query_variants"]).lower())
        self.assertNotIn("elon musk", " ".join(row["query_variants"]).lower())
        self.assertTrue(row["_semantic_locked"])

    def test_terminal_web_rescue_rechecks_original_scene_contract(self):
        from services.visual_requirement import build_visual_requirement
        from PIL import Image
        text = "The Tesla Model S followed in 2012."
        requirement = build_visual_requirement(title="Tesla", script=text, scene_text=text)
        contracts = [{'text': 'Tesla company'}, {'text': text, 'visual_requirement': requirement}]
        with tempfile.TemporaryDirectory() as td:
            asset = Path(td) / 'wrong-person.jpg'
            Image.new('RGB', (640, 360), '#345678').save(asset)
            preliminary = {'path': str(asset), 'visual_outcome': 'pass',
                           'visual_decision': {'status': 'accepted', 'model': 'qwen'}}
            strict_verifier = mock.Mock(return_value={'accepted': False, 'entity_conflict': True})
            with mock.patch('services.web_image_service.resolve_web_images_for_video_fast',
                            return_value={'image_paths': [str(asset)], 'scenes': [preliminary]}), \
                 mock.patch('app._generate_fast_scene_queries', return_value=[{'section_subject': 'Tesla'}]), \
                 mock.patch('app._resolve_query_llm_settings', return_value=('openrouter', 'unit-test-key', 'qwen')), \
                 mock.patch('services.web_image.safety.image_is_safe', return_value=(True, '')), \
                 mock.patch('services.pipeline_v2_media_adapter.build_qwen_verifier', return_value=(strict_verifier, None)):
                paths = ['existing.jpg', None]
                count = server_app._fill_unresolved_with_general_web_fallback(
                    paths, ['web', ''], [1], ['Tesla company', text], (640, 360), 'Tesla', text,
                    {'_topic_anchor': 'Tesla', '_scene_contracts_v2': contracts,
                     '_visual_requirements': [{}, requirement]}, force_web=True, require_verified_indices={1},
                )
            self.assertEqual(count, 0)
            self.assertIsNone(paths[1])
            verified_scene = strict_verifier.call_args.args[0]
            self.assertEqual(verified_scene['scene_index'], 1)
            self.assertEqual(verified_scene['text'], text)
            self.assertEqual(verified_scene['visual_requirement'], requirement)

    @mock.patch("app._fill_unresolved_with_general_web_fallback")
    @mock.patch("app._run_youtube_clips_branch", return_value=[None, None])
    def test_youtube_clips_source_rejects_web_fallback_after_youtube_exhausted(self, _mock_yt_branch, mock_web_fallback):
        fallback_a = server_app.TEMP_DIR / "yt_exhausted_web_a.jpg"
        fallback_b = server_app.TEMP_DIR / "yt_exhausted_web_b.jpg"
        fallback_a.write_bytes(b"a" * 128)
        fallback_b.write_bytes(b"b" * 128)

        def fake_web_fallback(final_paths, source_labels, unresolved_indices, **_kwargs):
            replacements = [fallback_a, fallback_b]
            for local_idx, idx in enumerate(unresolved_indices):
                final_paths[idx] = str(replacements[local_idx])
                source_labels[idx] = "Web Fallback Rescue"
            return len(unresolved_indices)

        mock_web_fallback.side_effect = fake_web_fallback
        with self.assertRaises(MediaContractError):
            server_app._resolve_media_paths(
                {
                    "script": "John Cena enters the arena.\nRoman Reigns appears in the ring.",
                    "title": "John Cena",
                    "image_source": "YouTube Clips",
                    "scene_count": 2,
                },
                {
                    "scene_texts": ["John Cena enters the arena.", "Roman Reigns appears in the ring."],
                    "scene_durations": [2.0, 2.0],
                    "render_resolution": (1280, 720),
                },
                lambda *_args, **_kwargs: None,
            )

        mock_web_fallback.assert_not_called()


    @mock.patch("app._resolve_query_llm_settings", return_value=("openrouter", "openrouter-key", "qwen/qwen3.5-flash-02-23"))
    @mock.patch("app._generate_shared_scene_brain_bundle")
    def test_fast_scene_queries_use_the_shared_openrouter_director(self, mock_shared_plan, _mock_settings):
        mock_shared_plan.return_value = {
            "web": [{
                "scene_number": 1,
                "search_query": "Titanic departure Southampton archival photograph",
                "supporting_search_query": "Southampton harbor 1912 archival image",
                "fallback_query": "RMS Titanic Southampton 1912",
                "_llm": True,
            }],
        }
        rows = server_app._generate_fast_scene_queries(
            script="A valid script for testing query routing.",
            title="Video",
            scene_texts=["Scene 1"],
            config={
                "llm_provider": "openrouter",
                "openrouter_api_key": "openrouter-key",
                "scene_brain_model": "qwen/qwen3.5-flash-02-23",
                "query_llm_model": "qwen/qwen3.5-flash-02-23",
                "query_generation_mode": "llm",
            },
        )

        mock_shared_plan.assert_called_once()
        args, kwargs = mock_shared_plan.call_args
        self.assertEqual(args[0], "A valid script for testing query routing.")
        self.assertEqual(args[1], "Valid Script")
        self.assertEqual(args[2], ["Scene 1"])
        self.assertEqual(args[3]["openrouter_api_key"], "openrouter-key")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["_llm"])

    @mock.patch("services.transcription_service.prewarm_whisper_workers")
    @mock.patch("services.transcription_service.is_available", side_effect=[False, True, True])
    @mock.patch("services.transcription_service.init_whisper")
    def test_whisper_warmup_prewarms_worker_pool(self, mock_init_whisper, _mock_available, mock_prewarm):
        ready = server_app._warm_local_whisper_if_needed({"whisper_mode": "local"})

        self.assertTrue(ready)
        mock_init_whisper.assert_called_once()
        mock_prewarm.assert_called_once()

    @mock.patch("app._run_video_pipeline", return_value={"success": True})
    def test_generate_accepts_mixed_media_without_weights(self, _mock_run):
        video = Path(tempfile.gettempdir()) / 'vidnets-mixed-validator-fixture.mp4'
        video.write_bytes(b'fixture-video')
        self.addCleanup(lambda: video.unlink(missing_ok=True))
        _mock_run.return_value = {'success': True, 'video_path': str(video)}
        response = self.client.post(
            "/api/generate",
            json={
                "script": "This is a valid script for testing the mixed media validator.",
                "image_source": "Mixed Media",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        _mock_run.assert_called_once()

    def test_queue_add_accepts_mixed_media_without_weights(self):
        response = self.client.post(
            "/api/queue/add",
            json={
                "script": "This is a valid script for testing queue validation.",
                "image_source": "Mixed Media",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])

    def test_explicit_archive_mix_youtube_misses_use_web_without_stock_rescue(self):
        scene_texts = [f"Titanic history scene {index}" for index in range(10)]
        scene_durations = [2.0] * len(scene_texts)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            web_paths = []
            for index in range(8):
                path = root / f"web_{index}.jpg"
                path.write_bytes(b"web")
                web_paths.append(str(path))
            fallback_path = root / "fallback.jpg"
            fallback_path.write_bytes(b"web fallback")

            def web_branch(scene_subset, *_args, **_kwargs):
                if len(scene_subset) == 2:
                    return {
                        "scene_queries": [],
                        "web_result": {"image_paths": [None, None]},
                    }
                return {
                    "scene_queries": [],
                    "web_result": {"image_paths": web_paths},
                }

            def fill_web_fallback(final_paths, source_labels, unresolved_indices, **_kwargs):
                for scene_index in unresolved_indices:
                    final_paths[scene_index] = str(fallback_path)
                    source_labels[scene_index] = "Web Images (YouTube fallback)"
                return len(unresolved_indices)

            config = {
                "image_source": "Mixed Media",
                "title": "Titanic History",
                "script": "A serious documentary about the history of the Titanic.",
                # Explicit source shares exclude illustrative stock. Automatic
                # history profiles now permit relevant stock for context.
                "mixed_media_weights": {"Web Images": 0.8, "YouTube Clips": 0.2, "Stock Media": 0.0},
                "media_sources": ["pexels"],
                "strict_mixed_source_contract": True,
            }
            audio_plan = {
                "scene_texts": scene_texts,
                "scene_durations": scene_durations,
            }
            self.assertFalse(server_app._stock_rescue_allowed(
                config,
                config["title"],
                config["script"],
                scene_texts,
            ))
            with mock.patch.object(server_app, "_run_web_media_branch", side_effect=web_branch) as web_mock, \
                    mock.patch.object(server_app, "_run_youtube_clips_branch", return_value=[None, None]), \
                    mock.patch.object(server_app, "_run_stock_media_branch") as stock_mock, \
                    mock.patch.object(server_app, "_fill_unresolved_with_general_web_fallback", side_effect=fill_web_fallback) as fallback_mock:
                result = server_app._resolve_media_paths(config, audio_plan, lambda *_args: None)

        self.assertEqual(stock_mock.call_count, 0)
        self.assertGreaterEqual(web_mock.call_count, 2)
        self.assertEqual(fallback_mock.call_count, 1)
        self.assertIn("Web Images (YouTube fallback)", result["source_labels"])

    def test_single_pool_youtube_audit_enables_fast_mixed_web_miss_rescue(self):
        scene_count = 40
        web_indices = list(range(10))
        yt_indices = list(range(10, scene_count))

        class Plan:
            requested_weights = {"Web Images": 0.25, "YouTube Clips": 0.75, "Stock Media": 0.0}
            target_counts = {"Web Images": 10, "YouTube Clips": 30, "Stock Media": 0}
            final_counts = target_counts
            grouped_indices = {
                "Web Images": web_indices,
                "YouTube Clips": yt_indices,
                "Stock Media": [],
            }

        class AuditedPaths(list):
            audit = {"pool_count": 1, "scene_assignments": 30}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            yt_paths = []
            for index in range(len(yt_indices)):
                path = root / f"titanic_{index}.mp4"
                path.write_bytes(b"video")
                yt_paths.append(str(path))

            config = {
                "image_source": "Mixed Media",
                "title": "RMS Titanic",
                "script": "A single-topic RMS Titanic documentary.",
                "_video_format": "single_subject",
                "strict_mixed_source_contract": False,
                "mixed_web_miss_use_yt_pool": 1,
            }
            audio_plan = {
                "scene_texts": [f"RMS Titanic documentary scene {index}" for index in range(scene_count)],
                "scene_durations": [2.0] * scene_count,
            }
            web_bundle = {"scene_queries": [], "web_result": {"image_paths": [None] * len(web_indices)}}

            with mock.patch("services.mixed_media_planner.build_adaptive_mixed_media_plan", return_value=Plan()), \
                    mock.patch.object(server_app, "_run_web_media_branch", return_value=web_bundle), \
                    mock.patch.object(server_app, "_run_youtube_clips_branch", return_value=AuditedPaths(yt_paths)), \
                    mock.patch.object(server_app, "_fill_unresolved_with_general_web_fallback") as fallback_mock:
                result = server_app._resolve_media_paths(config, audio_plan, lambda *_args: None)

        self.assertEqual(result["metadata"]["youtube_pool_audit"]["pool_count"], 1)
        self.assertEqual(sum(bool(path) for path in result["media_paths"]), len(yt_indices))
        self.assertTrue(all(not path for path in result["media_paths"][:len(web_indices)]))
        self.assertTrue(all("YouTube" in label for label in result["source_labels"][len(web_indices):]))
        # The missing Web-assigned slots should still receive the emergency
        # web-image rescue. YouTube clips remain one-use and are not replayed.
        fallback_mock.assert_called_once()
        self.assertTrue(fallback_mock.call_args.kwargs.get("force_web"))

    @mock.patch("app._run_video_pipeline")
    def test_generate_returns_media_contract_error_detail(self, mock_run_video_pipeline):
        mock_run_video_pipeline.side_effect = MediaContractError(
            "Mixed Media contract failed",
            {
                "requested_source": "Mixed Media",
                "requested_weights": {"Web Images": 0.65, "Stock Media": 0.35, "AI Images": 0.0},
                "target_counts": {"Web Images": 5, "Stock Media": 2, "AI Images": 0},
                "resolved_counts": {"Web Images": 3, "Stock Media": 2, "AI Images": 0},
                "unresolved_scene_indices": [2, 4],
                "source_failures": [
                    {
                        "source": "Web Images",
                        "requested_count": 5,
                        "resolved_count": 3,
                        "unresolved_scene_indices": [2, 4],
                        "error": "No safe web image selected for one or more scenes.",
                    }
                ],
            },
        )

        response = self.client.post(
            "/api/generate",
            json={
                "script": "This is a valid script for contract error testing.",
                "image_source": "Web Images",
            },
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error_detail"]["media_contract"]["requested_source"], "Mixed Media")
        self.assertEqual(payload["error_detail"]["media_contract"]["target_counts"]["Web Images"], 5)

    @mock.patch("app._run_stock_media_branch")
    @mock.patch("app._run_web_media_branch")
    def test_web_images_mode_does_not_launch_stock_backup(self, mock_web_branch, mock_stock_branch):
        mock_web_branch.return_value = {
            "scene_queries": [],
            "web_result": {"image_paths": ["C:\\temp\\web1.jpg", None], "audit_path": "C:\\temp\\audit.json"},
        }

        result = server_app._resolve_media_paths(
            {
                "image_source": "Web Images",
                "script": "A valid script for exact web-only mode.",
                "title": "Video",
            },
            {"scene_texts": ["Scene 1"], "scene_durations": [2.0]},
            lambda *_args: None,
        )

        self.assertEqual(result["media_paths"], ["C:\\temp\\web1.jpg"])
        mock_web_branch.assert_called_once()
        mock_stock_branch.assert_not_called()

    @mock.patch("app._run_stock_media_branch")
    @mock.patch("app._run_web_media_branch")
    def test_mixed_media_rescues_unresolved_slots_from_stock_media(self, mock_web_branch, mock_stock_branch):
        mock_web_branch.return_value = {
            "scene_queries": [],
            "web_result": {"image_paths": ["C:\\temp\\web1.jpg"], "audit_path": "C:\\temp\\audit.json"},
        }
        mock_stock_branch.side_effect = [
            {
                "scene_queries": [],
                "stock_paths": ["C:\\temp\\stock_assigned.mp4"],
            },
            {
                "scene_queries": [],
                "stock_paths": ["C:\\temp\\stock_rescue.mp4"],
            },
        ]

        result = server_app._resolve_media_paths(
            {
                "image_source": "Mixed Media",
                "mixed_media_weights": {"Web Images": 50, "Stock Media": 50, "AI Images": 0},
                "strict_mixed_source_contract": False,
                "script": "A valid script for exact mixed mode.",
                "title": "Video",
            },
            {"scene_texts": ["Scene 1", "Scene 2", "Scene 3"], "scene_durations": [2.0, 2.0, 2.0]},
            lambda *_args: None,
        )

        self.assertCountEqual(list(zip(result['media_paths'], result['source_labels'])), [
            ("C:\\temp\\web1.jpg", "Web Images"),
            ("C:\\temp\\stock_assigned.mp4", "Stock Media"),
            ("C:\\temp\\stock_rescue.mp4", "Stock Media (rescue search)"),
        ])
        self.assertEqual(mock_web_branch.call_count, 1)
        self.assertEqual(mock_stock_branch.call_count, 2)

    @mock.patch("app._run_stock_media_branch")
    def test_targeted_stock_rescue_reuses_cached_scene_results_and_misses(self, mock_stock_branch):
        mock_stock_branch.return_value = {
            "scene_queries": [],
            "stock_paths": ["C:\\temp\\stock_rescue_1.mp4", None],
        }
        config = {}
        final_paths = [None, None, None]

        first = server_app._run_targeted_stock_rescue(
            unresolved_indices=[0, 1],
            scene_texts=["Scene 1", "Scene 2", "Scene 3"],
            scene_durations=[2.0, 2.0, 2.0],
            final_paths=final_paths,
            config=config,
            title="Video",
            script="Script",
        )
        final_paths[0] = first[0]

        second = server_app._run_targeted_stock_rescue(
            unresolved_indices=[0, 1],
            scene_texts=["Scene 1", "Scene 2", "Scene 3"],
            scene_durations=[2.0, 2.0, 2.0],
            final_paths=final_paths,
            config=config,
            title="Video",
            script="Script",
        )

        self.assertEqual(first, {0: "C:\\temp\\stock_rescue_1.mp4"})
        self.assertEqual(second, {})
        self.assertEqual(mock_stock_branch.call_count, 1)

    @mock.patch("app._fill_unresolved_with_general_web_fallback")
    @mock.patch("app._run_youtube_clips_branch", return_value=[None])
    def test_youtube_clips_source_fails_when_zero_clips_resolve_without_explicit_web_fallback(self, _mock_yt_branch, mock_web_fallback):
        with self.assertRaises(MediaContractError) as raised:
            server_app._resolve_media_paths(
                {
                    "image_source": "YouTube Clips",
                    "script": "A valid script for exact youtube clips mode.",
                    "title": "Video",
                },
                {"scene_texts": ["Scene 1"], "scene_durations": [2.0]},
                lambda *_args: None,
            )

        self.assertEqual(raised.exception.detail["resolved_counts"]["YouTube Clips"], 0)
        mock_web_fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
