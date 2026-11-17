"""Stock last-resort policy regressions (Area 7, Nov 16 2026).

Standing facts: no Pexels/Pixabay keys were ever supplied and none will
be — direct retrieval only. Stock must be a LAST resort, never a silent
replacement for real retrieved media (YouTube clips / web images), and
never block a job when stock is unavailable. These tests replay the real
failures found in the audit:

* FallbackManager._try_stock searched generic scene-type B-roll queries
  ("abstract", "cinematic", "old photograph") with a score>=20 bar and
  returned the top hit as the scene's media — unrelated generic footage
  silently standing in for the scene.
* FallbackManager._try_hybrid returned the first result for a generic
  query regardless of relevance (and invited AI-generated media).
* The Pexels/Pixabay HTML-scraper lanes dropped the provider candidate's
  license/source/attribution/retrieval-method, so a stock image reached
  the manifest/ledger indistinguishable from a retrieved web image.
* _download_stock_fallback buffered r.content unbounded and saved
  whatever a 200 returned as a scene image.
* summarize_manifest() folded stock into real_count with no stock count
  at all, so an all-stock video read as all retrieved real media.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.real_media_service import (
    FallbackManager,
    MediaConfig,
    MediaItem,
    MultiSourceSearchEngine,
    SceneMedia,
    _download_stock_fallback,
    get_real_media_for_video,
)
from services.sources.base_source import ImageCandidate


def _item(score):
    return MediaItem(
        id="pexels_1", source="pexels", type="image", title="generic clip",
        description="generic clip", tags=[], download_url="https://media.test/1.jpg",
        preview_url="https://media.test/1.jpg", width=1280, height=720, score=score,
    )


class _StubEngine:
    """Records every query; always returns the same stub item."""

    def __init__(self, score):
        self.score = score
        self.queries = []

    def search_all_sources(self, query, media_type="both", priorities=None, context=None):
        self.queries.append(query)
        return [_item(self.score)]


class _StubScorer:
    def score_all(self, results, keywords, scene_context):
        return list(results)


class FallbackManagerStockTests(unittest.TestCase):
    def test_try_stock_rejects_generic_broll_below_threshold(self):
        # Real failure: Wright Brothers scene, generic "old photograph" /
        # "abstract" B-roll at score 25 used to be returned (bar was 20).
        engine = _StubEngine(score=25.0)
        manager = FallbackManager(MediaConfig(cache_dir=tempfile.mkdtemp()), engine)
        item, reason = manager._try_stock(
            ["Wright", "brothers", "first", "flight"],
            {"scene_type": "historical"},
            _StubScorer(),
        )
        self.assertIsNone(item)
        # And the search must have used the scene's own keywords, never a
        # generic scene-type B-roll query.
        self.assertEqual(engine.queries, ["Wright brothers first flight"])
        self.assertNotIn("abstract", " ".join(engine.queries))
        self.assertNotIn("old photograph", " ".join(engine.queries))

    def test_try_stock_accepts_keyword_matched_candidate_above_threshold(self):
        engine = _StubEngine(score=55.0)
        manager = FallbackManager(MediaConfig(cache_dir=tempfile.mkdtemp()), engine)
        item, reason = manager._try_stock(
            ["Wright", "brothers", "first", "flight"],
            {"scene_type": "historical"},
            _StubScorer(),
        )
        self.assertIsNotNone(item)
        self.assertIn("Wright brothers first flight", reason)

    def test_try_stock_with_no_keywords_is_a_clean_miss(self):
        engine = _StubEngine(score=99.0)
        manager = FallbackManager(MediaConfig(cache_dir=tempfile.mkdtemp()), engine)
        item, _reason = manager._try_stock([], {"scene_type": "generic"}, _StubScorer())
        self.assertIsNone(item)
        self.assertEqual(engine.queries, [])

    def test_try_hybrid_never_returns_generic_or_generated_media(self):
        engine = _StubEngine(score=99.0)
        manager = FallbackManager(MediaConfig(cache_dir=tempfile.mkdtemp()), engine)
        item, reason = manager._try_hybrid(
            ["Wright", "brothers"], {"scene_type": "historical"}, _StubScorer()
        )
        self.assertIsNone(item)
        self.assertEqual(engine.queries, [])


class ScraperProvenanceTests(unittest.TestCase):
    def _engine(self):
        return MultiSourceSearchEngine(MediaConfig(cache_dir=tempfile.mkdtemp()))

    def test_pexels_scraper_images_keep_license_and_retrieval_method(self):
        candidate = ImageCandidate(
            id="pexels_abc", source="pexels", title="Biplane", description="Biplane",
            download_url="https://images.pexels.com/1.jpg",
            preview_url="https://images.pexels.com/1.jpg", width=1280, height=720,
            license="Pexels License (provider page; verify current terms)",
            metadata={
                "source_url": "https://www.pexels.com/photo/1/",
                "page_url": "https://www.pexels.com/photo/1/",
                "license": "Pexels License (provider page; verify current terms)",
                "license_url": "https://www.pexels.com/license/",
                "rights_status": "provider_license_check_required",
                "attribution": "Pexels contributor: Jane",
                "creator": "Jane",
            },
        )
        source = Mock()
        source.search.return_value = [candidate]
        with patch(
            "services.sources.pexels_source.PexelsSource", return_value=source
        ):
            items = self._engine()._pexels_scraper_images("wright brothers flight")
        self.assertEqual(len(items), 1)
        breakdown = items[0].score_breakdown
        self.assertEqual(breakdown["source_url"], "https://www.pexels.com/photo/1/")
        self.assertEqual(
            breakdown["license"], "Pexels License (provider page; verify current terms)"
        )
        self.assertEqual(breakdown["retrieval_method"], "bounded_public_html_last_resort")
        self.assertTrue(breakdown["license_verification_required"])
        self.assertEqual(breakdown["attribution"], "Pexels contributor: Jane")

    def test_pixabay_scraper_images_keep_license_and_retrieval_method(self):
        candidate = ImageCandidate(
            id="pixabay_7", source="pixabay", title="Biplane", description="Biplane",
            download_url="https://cdn.pixabay.com/1.jpg",
            preview_url="https://cdn.pixabay.com/1.jpg", width=1280, height=720,
            metadata={"foreign_url": "https://pixabay.com/photos/7/", "user": "John"},
        )
        source = Mock()
        source.search.return_value = [candidate]
        with patch(
            "services.sources.pixabay_source.PixabaySource", return_value=source
        ):
            items = self._engine()._pixabay_scraper_images("wright brothers flight")
        self.assertEqual(len(items), 1)
        breakdown = items[0].score_breakdown
        self.assertEqual(breakdown["source_url"], "https://pixabay.com/photos/7/")
        self.assertIn("Pixabay Content License", breakdown["license"])
        self.assertEqual(breakdown["retrieval_method"], "bounded_public_html_last_resort")
        self.assertTrue(breakdown["license_verification_required"])

    def test_scraper_fallback_metadata_labels_retrieval_method(self):
        # get_real_media_for_video's keyless scraper fallback used to emit a
        # metadata row with no retrieval_method / licence at all.
        empty = SceneMedia(
            scene_index=0, scene_text="Wright brothers first flight",
            scene_type="historical", duration_needed=5.0, primary_media=None,
            fallback_level=4, fallback_reason="No stock media candidate passed",
        )
        image = {
            "download_url": "https://images.pexels.com/1.jpg",
            "source": "pexels", "id": "pexels_1", "title": "Biplane",
        }
        with (
            patch(
                "services.real_media_service._find_stock_media_for_scenes",
                return_value=[empty],
            ),
            patch(
                "services.real_media_service._scrape_stock_image", return_value=image
            ),
            patch(
                "services.real_media_service._download_stock_fallback",
                return_value="/tmp/fake-stock.jpg",
            ),
        ):
            result = get_real_media_for_video(
                ["Wright brothers first flight"], [5.0], title="Wright",
                runtime_options={"_return_metadata": True},
            )
        self.assertEqual(result["paths"], ["/tmp/fake-stock.jpg"])
        row = result["metadata"][0]
        self.assertEqual(row["provider"], "pexels")
        self.assertEqual(row["retrieval_method"], "bounded_public_html_last_resort")
        self.assertEqual(row["rights_status"], "provider_license_check_required")
        self.assertTrue(row["license_verification_required"])


class StockFallbackDownloadTests(unittest.TestCase):
    def _resp(self, chunks, content_type="image/jpeg"):
        resp = Mock(status_code=200)
        resp.headers = {"Content-Type": content_type}
        resp.iter_content.return_value = iter(chunks)
        return resp

    def _cleanup(self, img_id):
        from services.real_media_service import TEMP_DIR

        path = Path(TEMP_DIR) / "stock_fallback" / f"pexels_{img_id}.jpg"
        if path.exists():
            path.unlink()
        return path

    def test_valid_small_image_downloads(self):
        img_id = "dl-ok-1"
        path = self._cleanup(img_id)
        with patch(
            "services.real_media_service.requests.get",
            return_value=self._resp([b"\xff\xd8\xff\xe0" + b"x" * 100]),
        ):
            out = _download_stock_fallback(
                {"download_url": "https://images.pexels.com/1.jpg",
                 "source": "pexels", "id": img_id},
                "biplane",
            )
        try:
            self.assertEqual(out, str(path))
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 0)
        finally:
            self._cleanup(img_id)

    def test_oversized_image_is_rejected_without_a_partial_file(self):
        # Real failure: r.content was buffered unbounded and saved as-is.
        import services.real_media_service as rms

        img_id = "dl-big-1"
        path = self._cleanup(img_id)
        big = [b"x" * 600_000, b"x" * 600_000]  # > 1 MB cap in this test
        with (
            patch.object(rms, "_STOCK_MAX_DOWNLOAD_MB", 1),
            patch(
                "services.real_media_service.requests.get",
                return_value=self._resp(big),
            ),
        ):
            out = _download_stock_fallback(
                {"download_url": "https://images.pexels.com/1.jpg",
                 "source": "pexels", "id": img_id},
                "biplane",
            )
        self.assertIsNone(out)
        self.assertFalse(path.exists())

    def test_non_image_payload_is_rejected(self):
        img_id = "dl-html-1"
        path = self._cleanup(img_id)
        with patch(
            "services.real_media_service.requests.get",
            return_value=self._resp([b"<html>error</html>"], content_type="text/html"),
        ):
            out = _download_stock_fallback(
                {"download_url": "https://images.pexels.com/1.jpg",
                 "source": "pexels", "id": img_id},
                "biplane",
            )
        self.assertIsNone(out)
        self.assertFalse(path.exists())


class ManifestStockLabellingTests(unittest.TestCase):
    def test_all_stock_manifest_is_labelled_stock_not_retrieved(self):
        from services.media_manifest import summarize_manifest

        summary = summarize_manifest(
            {
                "asset_count": 3,
                "media_paths": ["/tmp/a.jpg", "/tmp/b.mp4", "/tmp/c.jpg"],
                "source_labels": ["Stock Media", "Stock Media", "Stock Media"],
                "metadata": {
                    "media_resolution_v2": [
                        {"provider": "pexels", "status": "accepted"},
                        {"provider": "pixabay", "status": "accepted"},
                        {"provider": "stock", "status": "accepted"},
                    ]
                },
            }
        )
        self.assertEqual(summary["asset_count"], 3)
        self.assertEqual(summary["stock_count"], 3)
        self.assertEqual(summary["stock_indices"], [0, 1, 2])
        self.assertEqual(summary["retrieved_count"], 0)
        self.assertEqual(summary["graphic_count"], 0)
        # Stock is a real file, so it stays in real_count — but it is no
        # longer silently presented as retrieved media.
        self.assertEqual(summary["real_count"], 3)

    def test_mixed_manifest_separates_stock_retrieved_and_graphic(self):
        from services.media_manifest import summarize_manifest

        summary = summarize_manifest(
            {
                "asset_count": 3,
                "media_paths": ["/tmp/a.jpg", "/tmp/b.jpg", "/tmp/c.png"],
                "source_labels": [
                    "Web Images", "Stock Media", "Grounded Graphic Fallback",
                ],
                "metadata": {
                    "media_resolution_v2": [
                        {"provider": "web", "status": "accepted"},
                        {"provider": "pexels", "status": "accepted"},
                        {"provider": "graphic",
                         "resolution_kind": "grounded_graphic_fallback"},
                    ]
                },
            }
        )
        self.assertEqual(summary["stock_count"], 1)
        self.assertEqual(summary["stock_indices"], [1])
        self.assertEqual(summary["retrieved_count"], 1)
        self.assertEqual(summary["graphic_count"], 1)
        self.assertEqual(summary["real_count"], 2)

    def test_labels_alone_label_stock_when_no_rows(self):
        from services.media_manifest import summarize_manifest

        summary = summarize_manifest(
            {
                "asset_count": 2,
                "media_paths": ["/tmp/a.jpg", "/tmp/b.jpg"],
                "source_labels": ["Stock Media", "YouTube Clips"],
            }
        )
        self.assertEqual(summary["stock_count"], 1)
        self.assertEqual(summary["retrieved_count"], 1)
        self.assertEqual(summary["real_count"], 2)


if __name__ == "__main__":
    unittest.main()
