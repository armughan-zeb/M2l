import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

import app as server_app


class ProductionPipelineWiringTests(unittest.TestCase):
    def test_explicit_subject_before_subtitle_is_preserved(self):
        cfg = {'_topic_anchor': 'Larger Megapack Tesla three connected systems'}
        self.assertEqual(server_app._resolve_topic_anchor_for_config('Tesla: Three Connected Systems',
            'Tesla makes electric cars. Larger Megapack installations store energy.', cfg), 'Tesla')

    def test_backend_status_matches_electron_pipeline_contract(self):
        with mock.patch.object(server_app, "public_auth_config", return_value={}), \
             mock.patch(
                 "services.transcription_service.whisper_runtime_status",
                 return_value={"available": True, "loaded": False},
             ):
            response = server_app.app.test_client().get("/api/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["pipeline_build_id"], server_app.PIPELINE_BUILD_ID)
        electron_source = Path(__file__).resolve().parents[1].joinpath("main.js").read_text(encoding="utf-8")
        self.assertIn(
            f"const EXPECTED_PIPELINE_BUILD_ID = '{server_app.PIPELINE_BUILD_ID}'",
            electron_source,
        )

    def test_strict_scene_cannot_inherit_neighbor_image_after_web_repair_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "accepted-other-scene.jpg"
            asset.write_bytes(b"existing media")
            paths = [str(asset), None]
            labels = ["Web Images", ""]
            config = {
                "image_source": "Mixed Media",
                "script": "Elizabeth was crowned. Charles was crowned decades later.",
                "_prebuilt_web_queries": [{}, {"scene_subject": "King Charles III"}],
            }
            with mock.patch.object(server_app, "_resolve_topic_anchor_for_config", return_value="British royal family"), \
                 mock.patch.object(server_app, "_resolve_query_llm_settings", return_value=("openrouter", "key", "model")), \
                 mock.patch("services.web_image_service.resolve_web_images_for_video_fast", return_value={"image_paths": [None]}), \
                 mock.patch.object(server_app, "_render_image_phash", return_value=None):
                rescued = server_app._fill_unresolved_with_general_web_fallback(
                    paths, labels, [1], ["Elizabeth was crowned.", "Charles was crowned decades later."],
                    (1280, 720), "British royal family", config["script"], config,
                    require_verified_indices={1},
                )
            self.assertEqual(rescued, 0)
            self.assertIsNone(paths[1])
            self.assertEqual(labels[1], "")

    @staticmethod
    def _visual_context(*, calls=0, max_calls=6, degraded_reason=''):
        context = mock.Mock()
        context.calls = calls
        context.max_calls = max_calls
        context.deadline_at = 9999999999.0
        context.degraded_reason = degraded_reason
        context.summary.return_value = {
            'vision_available': not bool(degraded_reason),
            'calls': calls,
            'max_calls': max_calls,
            'degraded_reason': degraded_reason,
        }
        return context

    def test_named_scene_contract_forces_visual_requirement(self):
        self.assertTrue(
            server_app._visual_required_for_job({
                "visual_understanding": False,
                "visual_required": False,
                "_visual_requirements": [
                    {
                        "strict_identity": True,
                        "canonical_entity": "British Royal Family",
                    }
                ],
            })
        )

    def test_strict_accuracy_forces_visual_requirement(self):
        self.assertTrue(
            server_app._visual_required_for_job({
                "visual_understanding": True,
                "visual_required": False,
                "strict_retrieval_accuracy": True,
            })
        )

    def test_delivery_first_long_job_makes_zero_new_final_visual_calls(self):
        context = self._visual_context(calls=6)
        paths = [f"resolved-{index}.mp4" for index in range(32)]
        plan = {
            'media_paths': list(paths),
            'source_labels': ['YouTube Clips'] * len(paths),
            'metadata': {
                'retrieval_visual_decisions': {
                    str(index): {'status': 'accepted'} for index in range(6)
                },
            },
        }
        config = {
            'type': 'long',
            'delivery_first_media': True,
            'visual_understanding': True,
            'visual_required': False,
            '_web_visual_context': context,
        }

        with mock.patch(
            'services.visual_intelligence.run_visual_intelligence_pass',
            side_effect=AssertionError('long delivery-first final gate must not call vision'),
        ) as run_pass, mock.patch.object(
            server_app,
            '_build_web_visual_context',
            side_effect=AssertionError('shared context must not be replaced'),
        ):
            result, rejected = server_app._run_final_visual_media_gate(
                plan, {'scene_texts': ['scene'] * len(paths)}, config,
                'Documentary', 'Script',
            )

        run_pass.assert_not_called()
        self.assertEqual(result['media_paths'], paths)
        self.assertEqual(rejected, [])
        summary = result['metadata']['final_visual_intelligence']
        self.assertTrue(summary['skipped'])
        self.assertEqual(summary['skip_reason'], 'delivery_first_long_job')
        self.assertEqual(summary['new_final_calls'], 0)
        self.assertEqual(summary['calls'], 6)
        self.assertEqual(summary['branch_decisions_reused'], 6)

    def test_exhausted_shared_context_is_never_replaced_for_final_gate(self):
        context = self._visual_context(
            calls=6,
            max_calls=6,
            degraded_reason='visual_call_budget_exceeded',
        )
        plan = {
            'media_paths': ['resolved.jpg'],
            'source_labels': ['Web Images'],
            'metadata': {},
        }
        config = {
            'type': 'short',
            'delivery_first_media': False,
            'visual_understanding': True,
            'visual_required': True,
            '_web_visual_context': context,
        }

        with mock.patch.object(
            server_app,
            '_build_web_visual_context',
            side_effect=AssertionError('final gate must not reset a spent context'),
        ), mock.patch(
            'services.visual_intelligence.run_visual_intelligence_pass',
            side_effect=AssertionError('spent context must fail open without new work'),
        ) as run_pass:
            result, rejected = server_app._run_final_visual_media_gate(
                plan, {'scene_texts': ['scene']}, config, 'Title', 'Script',
            )

        run_pass.assert_not_called()
        self.assertEqual(result['media_paths'], ['resolved.jpg'])
        self.assertEqual(rejected, [])
        self.assertEqual(
            result['metadata']['final_visual_intelligence']['skip_reason'],
            'visual_call_budget_exceeded',
        )

    def test_strict_final_gate_reuses_live_shared_context(self):
        context = self._visual_context(calls=2, max_calls=6)
        plan = {
            'media_paths': ['resolved.jpg'],
            'source_labels': ['Web Images'],
            'metadata': {},
        }
        config = {
            'type': 'long',
            'delivery_first_media': True,
            'visual_understanding': True,
            'visual_required': True,
            '_web_visual_context': context,
        }
        final_summary = {'vision_available': True, 'calls': 3}

        with mock.patch(
            'services.visual_intelligence.run_visual_intelligence_pass',
            return_value=(plan, '', final_summary),
        ) as run_pass, mock.patch.object(
            server_app,
            '_build_web_visual_context',
            side_effect=AssertionError('live shared context must be reused'),
        ):
            result, rejected = server_app._run_final_visual_media_gate(
                plan, {'scene_texts': ['scene']}, config, 'Title', 'Script',
            )

        self.assertEqual(rejected, [])
        self.assertEqual(result['metadata']['final_visual_intelligence'], final_summary)
        self.assertIs(run_pass.call_args.kwargs['visual_context'], context)

    def test_delivery_first_restores_render_safe_visual_rejection(self):
        media_plan = {
            "media_paths": [None],
            "source_labels": ["YouTube Clips (visual replace)"],
            "metadata": {},
        }
        with mock.patch.object(server_app, "_media_path_exists_for_render", return_value=True), \
             mock.patch.object(server_app, "_local_media_path_for_render", side_effect=lambda path: path):
            restored, remaining = server_app._restore_advisory_visual_rejections(
                media_plan,
                ["safe-clip.mp4"],
                ["YouTube Clips"],
                [0],
                {},
            )
        self.assertEqual(remaining, [])
        self.assertEqual(restored["media_paths"], ["safe-clip.mp4"])
        self.assertEqual(restored["source_labels"], ["YouTube Clips"])
        self.assertEqual(restored["metadata"]["visual_advisory_rejections_kept"], [1])

    def test_strict_visual_delivery_fallback_never_restores_rejected_asset(self):
        media_plan = {
            "media_paths": [None, "accepted.jpg"],
            "source_labels": ["", "Web Images"],
            "metadata": {},
        }
        audio_plan = {"scene_texts": ["Titanic lifeboats", "Titanic at sea"]}
        with mock.patch.object(
            server_app,
            "_create_emergency_render_asset",
            return_value="neutral-scene.jpg",
        ), mock.patch.object(
            server_app,
            "_media_path_exists_for_render",
            return_value=True,
        ):
            updated, remaining = server_app._fill_strict_visual_delivery_fallback(
                media_plan,
                audio_plan,
                [0],
                (1280, 720),
                "RMS Titanic",
                {"delivery_first_media": True, "allow_emergency_media": True},
            )
        self.assertEqual(remaining, [])
        self.assertEqual(updated["media_paths"], ["neutral-scene.jpg", "accepted.jpg"])
        self.assertTrue(updated["metadata"]["visual_accuracy_degraded"])
        self.assertEqual(updated["metadata"]["strict_visual_delivery_fallback_slots"], [1])

    def test_late_web_rescue_keeps_exact_source_and_never_fakes_vision_pass(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp_dir:
            asset = Path(temp_dir) / "berlin-airlift.jpg"
            Image.new("RGB", (640, 480), (80, 90, 100)).save(asset, format="JPEG")
            source_url = "https://www.businessinsider.com/berlin-airlift-history"
            audit_row = {
                "path": str(asset),
                "provider": "yandex",
                "domain": "yandex",
                "source_url": source_url,
                "page_url": source_url,
                "preview_url": "https://images.example.test/berlin-airlift.jpg",
                "title": "Berlin Airlift archive photograph",
                "license": "Unknown",
                "license_url": "",
                "attribution": "",
                "creator": "",
                "query_used": "Berlin Airlift photograph",
                "visual_outcome": "unavailable",
                "visual_reason": "OpenRouter vision timeout",
                "visual_decision": None,
            }
            web_result = {
                "image_paths": [str(asset)],
                "audit": {"scenes": [audit_row]},
            }
            config = {
                "image_source": "Mixed Media",
                "script": "The Berlin Airlift supplied the city by air.",
                "_prebuilt_web_queries": [{
                    "scene_subject": "Berlin Airlift",
                    "search_query": "Berlin Airlift archive photograph",
                }],
            }
            media_plan = {
                "media_paths": [None],
                "source_labels": [""],
                "metadata": {"media_resolution_v2": [{
                    "provider": "youtube",
                    "selected_path": "old-youtube.mp4",
                    "source_url": "https://www.youtube.com/watch?v=stale",
                    "candidate_id": "youtube:stale-candidate",
                    "verified": True,
                }]},
            }
            audio_plan = {"scene_texts": ["The Berlin Airlift supplied the city by air."]}

            with mock.patch("services.delivery_budget.get_delivery_budget", return_value=None), \
                 mock.patch.object(server_app, "_resolve_topic_anchor_for_config", return_value="Berlin Airlift"), \
                 mock.patch.object(server_app, "_resolve_query_llm_settings", return_value=("openrouter", "key", "model")), \
                 mock.patch("services.web_image_service.resolve_web_images_for_video_fast", return_value=web_result), \
                 mock.patch("services.web_image.safety.image_is_safe", return_value=(True, "ok")), \
                 mock.patch.object(server_app, "_render_image_phash", return_value=None), \
                 mock.patch.object(server_app, "_fill_unresolved_with_rotating_video_pool", return_value=0):
                updated, remaining = server_app._fill_strict_visual_delivery_fallback(
                    media_plan,
                    audio_plan,
                    [0],
                    (1280, 720),
                    "Berlin Airlift",
                    config,
                )

                self.assertEqual(remaining, [])
                row = updated["metadata"]["media_resolution_v2"][0]
                self.assertEqual(updated["media_paths"], [str(asset)])
                self.assertEqual(row["provider"], "web")
                self.assertEqual(row["retrieval_provider"], "yandex")
                self.assertEqual(row["source_url"], source_url)
                self.assertEqual(row["page_url"], source_url)
                self.assertEqual(row["rights_status"], "unknown")
                self.assertFalse(row["verified"])
                self.assertFalse(row["visual_decision"]["visual_checked"])
                self.assertNotEqual(row["candidate_id"], "youtube:stale-candidate")

                # The same unavailable candidate must remain unusable for a
                # strict named scene, even though it is safe and downloadable.
                strict_paths = [None]
                strict_labels = [""]
                strict_config = dict(config)
                strict_config.pop("_late_media_provenance_by_scene", None)
                rescued = server_app._fill_unresolved_with_general_web_fallback(
                    strict_paths,
                    strict_labels,
                    [0],
                    ["Berlin Airlift people"],
                    (1280, 720),
                    "Berlin Airlift",
                    "Berlin Airlift",
                    strict_config,
                    require_verified_indices={0},
                )

                self.assertEqual(rescued, 0)
                self.assertEqual(strict_paths, [None])
                self.assertEqual(strict_config.get("_late_media_provenance_by_scene"), {})

                audit_row["visual_outcome"] = "pass"
                audit_row["visual_decision"] = {
                    "status": "accepted",
                    "model": "qwen/qwen3.5-flash-02-23",
                    "vision_call_id": "vision-call-1",
                }
                strict_paths = [None]
                strict_labels = [""]
                rescued = server_app._fill_unresolved_with_general_web_fallback(
                    strict_paths,
                    strict_labels,
                    [0],
                    ["Berlin Airlift people"],
                    (1280, 720),
                    "Berlin Airlift",
                    "Berlin Airlift",
                    strict_config,
                    require_verified_indices={0},
                )

                self.assertEqual(rescued, 1)
                self.assertEqual(strict_paths, [str(asset)])

    def test_strict_fallback_uses_grounded_graphic_instead_of_unverified_stock(self):
        requirement = {
            "strict_identity": True,
            "visual_verification_required": True,
            "canonical_entity": "British Royal Family",
            "subject_type": "person",
            "requirement_hash": "royal-hash",
        }
        media_plan = {
            "media_paths": [None],
            "source_labels": [""],
            "metadata": {
                "media_resolution_v2": [{
                    "scene_index": 0,
                    "selected_path": "rejected.jpg",
                    "provider": "web",
                    "status": "accepted",
                    "verification_status": "visual_unverified",
                    "requirement_hash": "royal-hash",
                    "visual_decision": {"accepted": False, "status": "unavailable", "visual_checked": False},
                }],
            },
        }
        rejected_resolution = SimpleNamespace(
            provider="web",
            status="accepted",
            verification_status="visual_unverified",
            requirement_hash="royal-hash",
            selected_path="rejected.jpg",
            source_url="https://example.invalid/rejected",
            visual_decision={"accepted": False, "status": "unavailable", "visual_checked": False},
            final_scene_context={"visual_requirement": requirement},
            candidate_evidence={},
            attempts=[],
        )
        audio_plan = {
            "scene_texts": ["King Charles III addresses the nation"],
            "scene_contracts": [{"visual_requirement": requirement}],
        }
        with mock.patch.object(
            server_app,
            "_create_emergency_render_asset",
            return_value="render_media/graphics/graphic_fallback_1.png",
        ) as create_graphic, mock.patch.object(
            server_app,
            "_run_stock_media_branch",
            side_effect=AssertionError("strict unresolved slots must not use unverified stock"),
        ), mock.patch.object(
            server_app,
            "_media_path_exists_for_render",
            return_value=True,
        ):
            updated, remaining = server_app._fill_strict_visual_delivery_fallback(
                media_plan,
                audio_plan,
                [0],
                (1280, 720),
                "The British Royal Family",
                {
                    "delivery_first_media": True,
                    "strict_retrieval_accuracy": True,
                    "visual_required": True,
                    "allow_grounded_graphic_fallback": True,
                    "allow_grounded_graphic_fallback_strict": True,
                    "allow_emergency_media": False,
                    "allow_relevant_stock": True,
                    "_prebuilt_stock_queries": [{"primary_query": "royal family"}],
                    "_v2_media_resolutions": {0: rejected_resolution},
                },
            )
        assert remaining == []
        assert updated["media_paths"] == ["render_media/graphics/graphic_fallback_1.png"]
        assert updated["source_labels"] == ["Grounded Graphic Fallback"]
        row = updated["metadata"]["media_resolution_v2"][0]
        assert row["provider"] == "graphic"
        assert row["verification_status"] == "graphic_fallback"
        assert row["requirement_hash"] == "royal-hash"
        assert row["visual_decision"] is None
        assert row["graphic_fallback"]["grounded"] is True
        assert rejected_resolution.provider == "graphic"
        assert rejected_resolution.graphic_fallback["requirement_hash"] == "royal-hash"
        from services.pipeline_v2_media_adapter import _visual_requirement_issue
        assert _visual_requirement_issue(
            audio_plan["scene_contracts"][0], row, rejected_resolution,
            "Grounded Graphic Fallback",
        ) == ""
        create_graphic.assert_called_once()

    def test_late_provenance_stamp_keeps_grounded_graphic_out_of_web_lane(self):
        requirement = {
            "strict_identity": True,
            "visual_verification_required": True,
            "canonical_entity": "Super Bowl XLIX final play Malcolm Butler",
            "subject_type": "named_entity",
            "required_terms": ["Super Bowl XLIX final play Malcolm Butler", "2015"],
            "fallback_template": "date_card",
            "requirement_hash": "nfl-scene-hash",
        }
        scene_text = "A championship balanced on one snap."
        media_plan = {
            "media_paths": [None],
            "source_labels": [""],
            "metadata": {
                "media_resolution_v2": [{
                    "scene_index": 0,
                    "selected_path": "rejected.jpg",
                    "provider": "web",
                    "status": "accepted",
                    "verification_status": "visual_unverified",
                    "requirement_hash": "nfl-scene-hash",
                    "visual_decision": {"accepted": False, "status": "unavailable", "visual_checked": False},
                }],
            },
        }

        def fill_with_graphic(*, final_paths, source_labels, unresolved_indices, **_kwargs):
            assert unresolved_indices == [0]
            final_paths[0] = "render_media/graphics/nfl-grounded.png"
            source_labels[0] = "Grounded Graphic Fallback"
            return 1

        with mock.patch.object(
            server_app,
            "_fill_unresolved_with_general_web_fallback",
            side_effect=fill_with_graphic,
        ), mock.patch.object(
            server_app,
            "_media_path_exists_for_render",
            return_value=True,
        ), mock.patch.object(
            server_app,
            "_run_stock_media_branch",
            side_effect=AssertionError("strict grounded graphics must not be mislabeled as stock"),
        ):
            updated, remaining = server_app._fill_strict_visual_delivery_fallback(
                media_plan,
                {
                    "script": scene_text,
                    "scene_texts": [scene_text],
                    "scene_contracts": [{"text": scene_text, "visual_requirement": requirement}],
                },
                [0],
                (1280, 720),
                "Super Bowl XLIX",
                {
                    "script": scene_text,
                    "delivery_first_media": True,
                    "strict_retrieval_accuracy": True,
                    "visual_required": True,
                    "allow_grounded_graphic_fallback": True,
                    "allow_grounded_graphic_fallback_strict": True,
                    "allow_emergency_media": False,
                    "allow_relevant_stock": True,
                    "cross_source_fallback": True,
                },
            )

        assert remaining == []
        row = updated["metadata"]["media_resolution_v2"][0]
        assert row["provider"] == "graphic"
        assert row["resolution_kind"] == "grounded_graphic_fallback"
        assert row["verification_status"] == "grounded_graphic_fallback"
        assert row["visual_verification_skipped"] is False
        assert row["graphic_fallback"]["requirement_hash"] == "nfl-scene-hash"
        from services.pipeline_v2_media_adapter import _visual_requirement_issue
        scene = {"text": scene_text, "visual_requirement": requirement}
        assert _visual_requirement_issue(scene, row, None, "Grounded Graphic Fallback") == ""
        unsupported = dict(row)
        unsupported["graphic_fallback"] = dict(row["graphic_fallback"], facts=["2026 championship result"])
        assert _visual_requirement_issue(scene, unsupported, None, "Grounded Graphic Fallback") == (
            "grounded_graphic_unsupported_fact"
        )

    def test_v2_media_requirement_sync_prevents_stale_audio_contract_failure(self):
        broad = {
            "canonical_entity": "King Charles III and Prince Harry British Royal",
            "required_terms": ("King Charles III and Prince Harry British Royal",),
            "requirement_hash": "broad-hash",
        }
        narrowed = {
            "canonical_entity": "King Charles III",
            "required_terms": ("King Charles III",),
            "requirement_hash": "charles-hash",
        }
        audio_plan = {
            "scene_contracts": [
                {"visual_requirement": broad, "positive_subjects": broad["required_terms"]},
                {"visual_requirement": broad, "positive_subjects": broad["required_terms"]},
            ]
        }
        media_plan = {
            "metadata": {
                "media_resolution_v2": [
                    {"final_scene_context": {"visual_requirement": broad}},
                    {"final_scene_context": {"visual_requirement": narrowed}},
                ]
            }
        }
        changed = server_app._sync_v2_audio_contracts_from_media(audio_plan, media_plan, {})
        self.assertEqual(changed, 1)
        self.assertEqual(
            audio_plan["scene_contracts"][1]["visual_requirement"]["canonical_entity"],
            "King Charles III",
        )
        self.assertEqual(audio_plan["scene_contracts"][1]["positive_subjects"], ("King Charles III",))
        self.assertEqual(
            audio_plan["scene_contracts"][0]["visual_requirement"]["canonical_entity"],
            "King Charles III and Prince Harry British Royal",
        )

    def test_strict_stock_terminal_does_not_bypass_named_scene_verification(self):
        requirement = {
            "strict_identity": True,
            "canonical_entity": "King Charles III",
            "subject_type": "person",
            "requirement_hash": "charles-hash",
        }
        media_plan = {
            "media_paths": [None],
            "source_labels": [""],
            "metadata": {"media_resolution_v2": [{}]},
        }
        audio_plan = {
            "scene_texts": ["King Charles III addresses the nation"],
            "scene_contracts": [{"visual_requirement": requirement}],
        }
        stock_calls = []

        def stock_branch(*_args, **_kwargs):
            stock_calls.append(True)
            return {"stock_paths": ["terminal-stock.jpg"]}

        with mock.patch.object(server_app, "_fill_unresolved_with_rotating_video_pool"), \
             mock.patch.object(server_app, "_run_stock_media_branch", side_effect=stock_branch), \
             mock.patch.object(server_app, "_media_path_exists_for_render", return_value=True):
            updated, remaining = server_app._fill_strict_visual_delivery_fallback(
                media_plan,
                audio_plan,
                [0],
                (1280, 720),
                "Royal Family",
                {
                    "delivery_first_media": True,
                    "strict_retrieval_accuracy": True,
                    "visual_required": True,
                    "allow_stock_last_resort": True,
                    "stock_last_resort_no_vision": True,
                    "_stock_last_resort_enabled": True,
                    "_prebuilt_stock_queries": [{"primary_query": "King Charles III"}],
                },
            )
        # A strict named scene must not display anonymous stock when graphics
        # were explicitly disabled. Normal generation enables grounded recovery.
        self.assertEqual(remaining, [0])
        self.assertEqual(stock_calls, [])
        self.assertEqual(updated["media_paths"][0], None)

    def test_youtube_contract_error_uses_emergency_plan_in_delivery_mode(self):
        error = server_app.MediaContractError("no clips", {})
        audio_plan = {"scene_texts": ["Scene one"], "scene_durations": [4.0]}
        with mock.patch.object(
            server_app,
            "_fill_final_bad_slots_from_existing_media",
            side_effect=lambda **kwargs: kwargs["final_paths"].__setitem__(0, "emergency.jpg") or 1,
        ), mock.patch.object(
            server_app,
            "_media_path_exists_for_render",
            return_value=True,
        ):
            plan = server_app._build_emergency_media_plan_from_contract_error(
                error,
                audio_plan,
                {"image_source": "YouTube Clips", "delivery_first_media": True, "allow_emergency_media": True},
                "Topic",
                "Script",
                (1280, 720),
            )
        self.assertEqual(plan["media_paths"], ["emergency.jpg"])

    @mock.patch('services.media_delivery_gate.validate_documentary_coverage', new=lambda *_a, **_k: {'passed': True})
    def test_mixed_media_resolves_once_against_final_whisper_scenes(self):
        expected = {
            "scene_texts": ["provisional one", "provisional two"],
            "scene_durations": [4.0, 4.0],
            "duration": 8.0,
        }
        final_audio = {
            "scene_texts": ["final one", "final two", "final three"],
            "scene_durations": [3.0, 3.0, 3.0],
            "scene_timing_windows": [
                {"start": 0.0, "end": 3.0},
                {"start": 3.0, "end": 6.0},
                {"start": 6.0, "end": 9.0},
            ],
            "duration": 9.0,
            "audio_path": "voice.wav",
        }
        observed = []

        def resolve_media(config, audio_plan, _progress, cancellation=None):
            observed.append(list(audio_plan.get("scene_texts") or []))
            self.assertEqual(config["_scene_timing_windows"], final_audio["scene_timing_windows"])
            return {
                "media_paths": ["one.jpg", "two.mp4", "three.jpg"],
                "source_labels": ["Web Images", "YouTube Clips", "Web Images"],
                "metadata": {},
                "prompt_bundle": {"beat_texts": list(audio_plan["scene_texts"])},
            }

        config = {
            "script": "A sufficiently long script for a production pipeline wiring test.",
            "title": "Production wiring",
            "name": "production_wiring",
            "image_source": "Mixed Media",
            "pipeline_v2_mode": "off",
            "mixed_media_weights": {
                "Web Images": 40,
                "YouTube Clips": 60,
                "Stock Media": 0,
                "AI Images": 0,
            },
            "semantic_render_enabled": False,
            "visual_understanding": False,
            "captions_enabled": False,
        }

        env = {
            "VIDZEO_MIXED_WAIT_FOR_FINAL_AUDIO_SCENES": "1",
            "VIDZEO_STRICT_MIXED_SOURCE_CONTRACT": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
            server_app, "_start_whisper_warmup", return_value=None
        ), mock.patch.object(
            server_app, "_build_expected_scene_plan", return_value=expected
        ), mock.patch.object(
            server_app, "_build_audio_scene_plan", return_value=final_audio
        ), mock.patch.object(
            server_app, "_generate_source_query_bundle_parallel",
            return_value={"web": [{}, {}, {}], "yt": [{}, {}, {}], "stock": []},
        ), mock.patch.object(
            server_app, "_resolve_media_paths", side_effect=resolve_media
        ), mock.patch.object(
            server_app, "_remap_media_plan_to_audio_scenes", side_effect=lambda media, _audio: media
        ), mock.patch.object(
            server_app, "_repair_media_plan_for_render", side_effect=lambda **kwargs: kwargs["media_plan"]
        ), mock.patch.object(
            server_app, "_break_back_to_back_media_repeats",
            side_effect=lambda paths, labels, _config: (paths, labels, 0),
        ), mock.patch.object(
            server_app, "_build_component_manifest", return_value={"ok": True}
        ), mock.patch.object(
            server_app,
            "_render_video_with_semantic_editing",
            return_value="output/production_wiring.mp4",
        ) as render_remotion, mock.patch(
            "services.video_service.generate_video", return_value="output/production_wiring.mp4"
        ), mock.patch.object(
            server_app, "_resolve_mixed_media_niche_policy", return_value=None
        ), mock.patch.object(
            server_app, "_run_editing_profile", return_value=None
        ), mock.patch.object(
            server_app, "_run_editorial_brain", return_value=None
        ), mock.patch.object(
            server_app, "_run_director_plan", return_value=None
        ), mock.patch.object(
            server_app,
            "_run_final_visual_media_gate",
            side_effect=lambda media_plan, *_args, **_kwargs: (media_plan, []),
        ), mock.patch(
            "services.transcription_service.unload_whisper_model"
        ):
            result = server_app._run_video_pipeline(config)

        self.assertTrue(result["success"])
        self.assertEqual(observed, [["final one", "final two", "final three"]])
        render_remotion.assert_called_once()
        self.assertEqual(
            render_remotion.call_args.kwargs["audio_plan"]["scene_texts"],
            final_audio["scene_texts"],
        )

    def test_media_resolver_contains_no_outer_job_config_reference(self):
        import inspect

        source = inspect.getsource(server_app._resolve_media_paths)
        self.assertNotIn("job_config", source)

    def test_v2_adjacent_still_repair_moves_resolution_rows_with_the_asset(self):
        with tempfile.TemporaryDirectory() as root:
            first = Path(root) / "scene-1.jpg"
            duplicate_copy = Path(root) / "scene-2.jpg"
            alternate = Path(root) / "scene-3.jpg"
            first.write_bytes(b"same-pixels")
            duplicate_copy.write_bytes(b"same-pixels")
            alternate.write_bytes(b"different-pixels")
            rows = [
                {"scene_index": 0, "candidate_id": "a", "selected_path": str(first)},
                {"scene_index": 1, "candidate_id": "b", "selected_path": str(duplicate_copy)},
                {"scene_index": 2, "candidate_id": "c", "selected_path": str(alternate)},
            ]
            paths, labels, fixed = server_app._break_back_to_back_media_repeats(
                [str(first), str(duplicate_copy), str(alternate)],
                ["Web Images", "Web Images", "Web Images"],
                {},
                resolution_rows=rows,
            )

            self.assertEqual(fixed, 1)
            self.assertEqual(paths, [str(first), str(alternate), str(duplicate_copy)])
            self.assertEqual(labels, ["Web Images", "Web Images", "Web Images"])
            self.assertEqual([row["candidate_id"] for row in rows], ["a", "c", "b"])

    def test_v2_adjacent_tail_repeat_can_swap_with_earlier_same_container(self):
        with tempfile.TemporaryDirectory() as root:
            paths = []
            for name, payload in (
                ("a.jpg", b"a"),
                ("b.jpg", b"b"),
                ("c.jpg", b"c"),
                ("tail-1.jpg", b"tail"),
                ("tail-2.jpg", b"tail"),
            ):
                path = Path(root) / name
                path.write_bytes(payload)
                paths.append(str(path))
            rows = [
                {"scene_index": index, "candidate_id": name, "container_id": "story"}
                for index, name in enumerate(("a", "b", "c", "tail-1", "tail-2"))
            ]

            repaired, _labels, fixed = server_app._break_back_to_back_media_repeats(
                paths,
                ["Web Images"] * len(paths),
                {},
                resolution_rows=rows,
            )

            self.assertEqual(fixed, 1)
            self.assertTrue(all(
                not server_app._media_paths_are_repetitive(left, right, {})
                for left, right in zip(repaired, repaired[1:])
            ))
            self.assertEqual(
                [row["candidate_id"] for row in rows],
                [Path(path).stem for path in repaired],
            )

    @mock.patch('services.media_delivery_gate.validate_documentary_coverage', new=lambda *_a, **_k: {'passed': True})
    def test_v2_entrypoint_resolves_immutable_audio_scenes_without_legacy_remap(self):
        from services.storyboard_v2 import build_script_plan

        script = (
            "Number 1: Cave Diver. Divers inspect submerged tunnels.\n"
            "Number 2: Tower Climber. Climbers maintain antennas."
        )
        plan = build_script_plan(
            script,
            content_title="Extreme Work",
            format_hint="listicle",
            scene_mode="Every Beat",
        )
        timed_scenes = []
        cursor = 0.0
        for scene in plan.scenes:
            timed_scenes.append(
                scene.with_timing(
                    word_start_index=None,
                    word_end_index=None,
                    start_seconds=cursor,
                    end_seconds=cursor + 3.0,
                    timing_quality="estimated",
                    alignment_confidence=0.0,
                )
            )
            cursor += 3.0
        final_audio = {
            "scene_texts": [scene.text for scene in timed_scenes],
            "scene_durations": [3.0] * len(timed_scenes),
            "scene_timing_windows": [
                {
                    "scene_id": scene.scene_id,
                    "container_id": scene.container_id,
                    "start_seconds": scene.start_seconds,
                    "end_seconds": scene.end_seconds,
                }
                for scene in timed_scenes
            ],
            "scene_contracts": timed_scenes,
            "containers": list(plan.containers),
            "duration": cursor,
            "audio_path": "voice.wav",
            "whisper_words": [],
        }
        observed = []

        def resolve_v2(config, audio_plan, _progress, cancellation=None):
            del cancellation
            observed.append([scene.scene_id for scene in audio_plan["scene_contracts"]])
            config["_v2_media_resolutions"] = {}
            return {
                "media_paths": [f"asset-{index}.jpg" for index in range(len(timed_scenes))],
                "source_labels": ["Web Images"] * len(timed_scenes),
                "metadata": {"media_resolution_v2": []},
                "prompt_bundle": {"beat_texts": list(audio_plan["scene_texts"])},
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            final_video = Path(temp_dir) / "v2-entry.mp4"
            final_video.write_bytes(b"rendered-video")
            config = {
                "script": script,
                "content_title": "Extreme Work",
                "output_name": "v2-entry",
                "format_hint": "listicle",
                "scene_mode": "Every Beat",
                "media_mode": "automatic",
                "image_source": "Mixed Media",
                "pipeline_v2_mode": "on",
                "output_dir": temp_dir,
                "captions_enabled": False,
                "semantic_render_enabled": False,
            }
            with mock.patch.object(
                server_app, "_start_whisper_warmup", return_value=None
            ), mock.patch.object(
                server_app, "_resolve_mixed_media_niche_policy", return_value=None
            ), mock.patch.object(
                server_app, "_build_audio_scene_plan", return_value=final_audio
            ), mock.patch.object(
                server_app,
                "_generate_source_query_bundle_parallel",
                return_value={"web": [], "yt": [], "stock": []},
            ), mock.patch.object(
                server_app, "_resolve_media_paths_v2", side_effect=resolve_v2
            ), mock.patch.object(
                server_app,
                "_remap_media_plan_to_audio_scenes",
                side_effect=AssertionError("V2 must never remap immutable scenes"),
            ), mock.patch.object(
                server_app,
                "_repair_media_plan_for_render",
                side_effect=AssertionError("V2 must never enter legacy repair"),
            ), mock.patch(
                "services.pipeline_v2_media_adapter.validate_media_plan_for_render_v2",
                side_effect=lambda media_plan, *_args, **_kwargs: media_plan,
            ), mock.patch(
                "services.pipeline_v2_media_adapter._validate_renderable_asset",
                return_value=(True, "", "image"),
            ), mock.patch(
                "services.pipeline_v2_media_adapter._visual_requirement_issue",
                return_value="",
            ), mock.patch.object(
                server_app, "_media_path_exists_for_render", return_value=True
            ), mock.patch.object(
                server_app, "_render_video_with_semantic_editing", return_value=str(final_video)
            ), mock.patch.object(
                server_app, "_run_editing_profile", return_value=None
            ), mock.patch.object(
                server_app, "_run_editorial_brain", return_value=None
            ), mock.patch.object(
                server_app, "_run_director_plan", return_value=None
            ), mock.patch.object(
                server_app,
                "_run_final_visual_media_gate",
                side_effect=lambda media_plan, *_args, **_kwargs: (media_plan, []),
            ), mock.patch(
                "services.transcription_service.unload_whisper_model"
            ):
                result = server_app._run_video_pipeline(config)

        self.assertTrue(result["success"])
        self.assertEqual(observed, [[scene.scene_id for scene in timed_scenes]])
        self.assertTrue(result["storyboard"].endswith("v2-entry.storyboard.json"))
        self.assertTrue(result["manifest_path"].endswith("v2-entry.manifest.json"))

    def test_caption_burn_uses_the_canonical_ffmpeg_binary(self):
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(list(command))
            Path(command[-1]).write_bytes(b"v" * 4096)
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "master.mp4"
            subtitle = root / "captions.srt"
            output = root / "final.mp4"
            source.write_bytes(b"source")
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            canonical = str(root / "ffmpeg-canonical.exe")
            with mock.patch.dict(os.environ, {"VIDZEO_FFMPEG_PATH": canonical}), mock.patch(
                "subprocess.run", side_effect=fake_run,
            ):
                result = server_app._burn_subtitles_into_master(source, subtitle, output)

        self.assertEqual(result, str(output))
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], canonical)


if __name__ == "__main__":
    unittest.main()
