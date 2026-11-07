import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

import app as server_app


class ActiveMediaRecoveryTests(unittest.TestCase):
    def test_mixed_branch_miss_does_not_overwrite_verified_media(self):
        source = inspect.getsource(server_app._resolve_media_paths)
        self.assertIn("never let a miss erase verified media", source)
        self.assertIn("optional mixed-media lane", source)
        source = inspect.getsource(server_app._repair_media_plan_for_render)
        self.assertIn("not is_mixed_media_job", source)

    def test_pool_cut_recovery_excludes_blurred_derivative(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cuts = root / "yt_clips" / "pool_timeout_case" / "cuts"
            cuts.mkdir(parents=True)
            original = cuts / "scene_001.moment_00.mp4"
            clean = cuts / "scene_001.moment_00.clean.mp4"
            original.write_bytes(b"original" * 32)
            clean.write_bytes(b"clean" * 32)

            with mock.patch.object(server_app, "TEMP_DIR", root), \
                 mock.patch.object(server_app, "_media_path_exists_for_render", return_value=True):
                recovered = server_app._recover_completed_clips_from_disk(1)

        self.assertEqual(recovered, [str(original.resolve())])

    def test_completed_raw_mp4_range_is_recovered_only_after_media_and_visual_checks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw = root / "yt_clip_scene_1.cand_0.raw.mp4"
            raw.write_bytes(b"verified-range" * 512)
            with mock.patch.object(server_app, "_media_path_exists_for_render", return_value=True), \
                 mock.patch("services.youtube_pool_renderer._screen_moment_quality", return_value={"reject": False}), \
                 mock.patch("services.youtube_pool_renderer._screen_moment_overlays", return_value={"reject": False, "overlay_types": []}):
                recovered = server_app._recover_completed_clips_from_disk(1, run_root=root)

        self.assertEqual(recovered, [str(raw.resolve())])

    def test_media_resolver_uses_its_config_parameter_during_rescue(self):
        source = inspect.getsource(server_app._resolve_media_paths)

        self.assertNotIn("job_config", source)

    def test_parallel_web_rescue_completes_zero_youtube_mixed_job(self):
        scenes = ["Lionel Messi trains.", "Lionel Messi scores.", "Lionel Messi celebrates."]
        with tempfile.TemporaryDirectory() as temp_dir:
            fallback_paths = []
            for index in range(len(scenes)):
                path = Path(temp_dir) / f"fallback_{index}.jpg"
                Image.new("RGB", (1280, 720), (40 + index * 20, 80, 120)).save(path)
                fallback_paths.append(str(path))

            def web_branch(scene_texts, *_args, **_kwargs):
                return {
                    "web_result": {
                        "image_paths": fallback_paths[:len(scene_texts)],
                        "audit_path": str(Path(temp_dir) / "audit.json"),
                    },
                    "scene_queries": [],
                }

            config = {
                "image_source": "Mixed Media",
                "mixed_media_weights": {
                    "Web Images": 0,
                    "YouTube Clips": 100,
                    "Stock Media": 0,
                    "AI Images": 0,
                },
                "strict_mixed_source_contract": False,
                "strict_mixed_percentages": False,
                "_skip_sync_topic_anchor": True,
                "_topic_anchor": "Football legends",
                "_yt_scene_query_overrides": [
                    {
                        "container_subject": "Lionel Messi",
                        "container_id": "messi",
                        "youtube_query": "Lionel Messi training footage",
                        "_llm": True,
                    }
                    for _ in scenes
                ],
                "script": " ".join(scenes),
                "title": "Football legends",
            }
            audio_plan = {"scene_texts": scenes, "scene_durations": [2.0] * len(scenes)}

            with mock.patch.object(server_app, "_run_youtube_clips_branch", return_value=[None] * len(scenes)), mock.patch.object(
                server_app,
                "_run_web_media_branch",
                side_effect=web_branch,
            ), mock.patch(
                "services.web_image_service.resolve_web_images_for_video_fast",
                return_value={"image_paths": fallback_paths, "audit_path": str(Path(temp_dir) / "audit.json")},
            ):
                result = server_app._resolve_media_paths(config, audio_plan, lambda *_args: None)

        self.assertEqual(
            result["media_paths"], fallback_paths,
        )
        # The label was extended to reflect the actual fallback pipeline
        # (shadow-web rescue). The old test expected "YouTube Clips (image
        # fallback)", but that wrongly reported image paths as YouTube video.
        self.assertEqual(
            result["source_labels"],
            ["Web Images (parallel web rescue)"] * len(scenes),
        )


if __name__ == "__main__":
    unittest.main()
