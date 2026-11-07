import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))


class WebImageSafetyTests(unittest.TestCase):
    def test_metadata_blocks_explicit_terms_and_adult_domains(self):
        from services.web_image.safety import candidate_is_safe, metadata_safety_reason

        self.assertEqual(
            metadata_safety_reason(title="Celebrity nude gallery", url="https://example.com/photo.jpg"),
            "adult_metadata",
        )
        self.assertEqual(
            metadata_safety_reason(title="Portrait", url="https://cdn.onlyfans.example/photo.jpg"),
            "adult_domain",
        )
        self.assertEqual(metadata_safety_reason(title="Lionel Messi press conference"), "")
        self.assertEqual(candidate_is_safe({"title": "Bikini model", "url": "https://example.com/a.jpg"})[0], False)
        self.assertEqual(
            metadata_safety_reason(
                title="Image not found",
                url="https://provider.example/openverse-placeholder.jpg",
            ),
            "placeholder_metadata",
        )

    def test_visual_gate_blocks_valid_jpeg_provider_error_card(self):
        from services.web_image.safety import image_is_safe

        with tempfile.TemporaryDirectory() as temp_dir:
            placeholder = Path(temp_dir) / "openverse-result.jpg"
            image = Image.new("RGB", (1000, 600), "white")
            ImageDraw.Draw(image).text((420, 280), "IMAGE NOT FOUND", fill="black")
            image.save(placeholder, quality=92)

            safe, reason = image_is_safe(
                placeholder,
                title="Historical archive photograph",
                source="openverse",
            )

        self.assertFalse(safe)
        self.assertEqual(reason, "placeholder_text_card")

    def test_visual_gate_blocks_high_exposure_and_keeps_safe_landscape(self):
        from services.web_image.safety import image_is_safe

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {"VIDZEO_WEB_DISABLE_VISUAL_SAFETY": "0"},
            clear=False,
        ):
            exposed = Path(temp_dir) / "exposed.jpg"
            landscape = Path(temp_dir) / "landscape.jpg"
            Image.new("RGB", (800, 600), (205, 145, 105)).save(exposed, quality=90)
            Image.new("RGB", (800, 600), (30, 100, 180)).save(landscape, quality=90)

            self.assertFalse(image_is_safe(exposed, title="woman portrait")[0])
            self.assertTrue(image_is_safe(landscape, title="mountain lake")[0])

    @mock.patch("services.web_image.resolver.download_bulk")
    def test_single_candidate_metadata_gate_runs_before_download(self, mock_download):
        from services.web_image.resolver import _download_and_accept_single_candidate

        result = _download_and_accept_single_candidate(
            {"url": "https://example.com/nsfw-nude.jpg", "title": "portrait", "source": "bing_hybrid"},
            Path(tempfile.gettempdir()),
            0,
            "A documentary portrait",
            set(),
            "",
            set(),
            set(),
            set(),
            threading.Lock(),
            threading.Lock(),
            threading.Lock(),
        )

        self.assertIsNone(result)
        mock_download.assert_not_called()

    @mock.patch("services.web_image.hybrid_search._request_get_with_retries", return_value=None)
    def test_bing_always_requests_strict_safe_search(self, mock_get):
        from services.web_image.hybrid_search import search_bing

        search_bing("Venezuela earthquake rescue", limit=3)

        self.assertTrue(mock_get.call_args_list)
        self.assertTrue(all(call.kwargs["params"].get("adlt") == "strict" for call in mock_get.call_args_list))

    def test_openverse_excludes_mature_results(self):
        from services.sources.openverse_source import OpenverseSource

        source = OpenverseSource()
        response = mock.Mock(status_code=200)
        response.json.return_value = {"results": []}
        source.session.get = mock.Mock(return_value=response)

        source.search("earthquake rescue", limit=3)

        self.assertEqual(source.session.get.call_args.kwargs["params"].get("mature"), "false")

    def test_reddit_rejects_over_18_posts(self):
        from services.sources.reddit_source import RedditSource

        source = RedditSource()
        result = source._post_to_candidate(
            {
                "id": "unsafe",
                "title": "ordinary title",
                "url": "https://i.redd.it/test.jpg",
                "domain": "i.redd.it",
                "score": 100,
                "over_18": True,
            },
            "pics",
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
