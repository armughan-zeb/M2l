import os
import sys
import threading
import time
import unittest
from unittest import mock


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))


class SourceBrokerTests(unittest.TestCase):
    def test_repairs_cross_job_sports_leak_and_duplicate_terms(self):
        from services.web_image.source_broker import sanitize_source_query

        repaired = sanitize_source_query(
            "RMS Titanic lifeboats lifeboats football portrait",
            subject="Sea maritime",
            scene_text="Passengers entered lifeboats after the collision.",
        )

        self.assertEqual(repaired, "RMS Titanic lifeboats")

    def test_preserves_real_sports_queries(self):
        from services.web_image.source_broker import sanitize_source_query

        query = "FIFA World Cup football match stadium"
        repaired = sanitize_source_query(
            query,
            subject="FIFA World Cup football",
            scene_text="Supporters enter the stadium before the match.",
        )

        self.assertEqual(repaired, query)

    def test_cache_reuses_results_and_exposes_funnel(self):
        from services.web_image.source_broker import DiscoveryCache, SourceTelemetry

        cache = DiscoveryCache(success_ttl=60, empty_ttl=5)
        telemetry = SourceTelemetry()
        calls = []

        def fetch():
            calls.append(1)
            return [{"url": "https://example.com/image.jpg"}]

        first = cache.get_or_fetch("wikimedia", "RMS Titanic", fetch, telemetry)
        second = cache.get_or_fetch("wikimedia", " rms   titanic ", fetch, telemetry)
        snapshot = telemetry.snapshot()

        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)
        self.assertEqual(snapshot["providers"]["wikimedia"]["provider_requests"], 1)
        self.assertEqual(snapshot["providers"]["wikimedia"]["cache_hits"], 1)
        self.assertEqual(snapshot["providers"]["wikimedia"]["discovered"], 1)

    def test_cache_coalesces_concurrent_identical_requests(self):
        from services.web_image.source_broker import DiscoveryCache, SourceTelemetry

        cache = DiscoveryCache(success_ttl=60)
        telemetry = SourceTelemetry()
        calls = []
        gate = threading.Event()

        def fetch():
            calls.append(1)
            gate.wait(1)
            return [{"url": "https://example.com/one.jpg"}]

        results = []
        threads = [
            threading.Thread(
                target=lambda: results.append(
                    cache.get_or_fetch("flickr_api", "Titanic", fetch, telemetry)
                )
            )
            for _ in range(3)
        ]
        for thread in threads:
            thread.start()
        time.sleep(0.05)
        gate.set()
        for thread in threads:
            thread.join(2)

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(result for result in results))
        self.assertEqual(
            telemetry.snapshot()["providers"]["flickr_api"]["coalesced_hits"],
            2,
        )

    def test_cache_hard_caps_unique_provider_requests(self):
        from services.web_image.source_broker import DiscoveryCache, SourceTelemetry

        cache = DiscoveryCache(success_ttl=60, max_provider_requests=30)
        telemetry = SourceTelemetry()
        calls = []

        for index in range(60):
            cache.get_or_fetch(
                "flickr_api",
                f"RMS Titanic facet {index}",
                lambda index=index: calls.append(index) or [],
                telemetry,
            )

        budget = cache.request_budget_snapshot()
        snapshot = telemetry.snapshot()
        self.assertEqual(len(calls), 30)
        self.assertEqual(budget, {"cap": 30, "started": 30, "rejected": 30})
        self.assertEqual(
            snapshot["providers"]["flickr_api"]["provider_requests"],
            30,
        )
        self.assertEqual(
            snapshot["providers"]["flickr_api"]["request_budget_exhausted"],
            30,
        )

    def test_telemetry_calculates_per_source_acceptance(self):
        from services.web_image.source_broker import SourceTelemetry

        telemetry = SourceTelemetry()
        telemetry.record("openverse", "discovered", 20)
        telemetry.record("openverse", "relevance_rejected", 15)
        telemetry.record("openverse", "accepted", 2)
        telemetry.record_query_repairs(
            4,
            [{"original": "Titanic football", "repaired": "Titanic"}],
        )

        snapshot = telemetry.snapshot()

        self.assertEqual(snapshot["providers"]["openverse"]["acceptance_rate"], 0.1)
        self.assertEqual(snapshot["totals"]["relevance_rejected"], 15)
        self.assertEqual(snapshot["query_repairs"][0]["scene_number"], 4)

    def test_legacy_search_lanes_are_opt_in(self):
        from services.web_image import resolver

        with mock.patch.dict(
            os.environ,
            {"VIDZEO_ENABLE_LEGACY_WEB_SEARCH": "0"},
            clear=False,
        ):
            default_names = [name for name, _kind in resolver._enabled_fast_sources()]
        with mock.patch.dict(
            os.environ,
            {"VIDZEO_ENABLE_LEGACY_WEB_SEARCH": "1"},
            clear=False,
        ):
            legacy_names = [name for name, _kind in resolver._enabled_fast_sources()]

        # Direct Bing is now a bounded default fast lane. Only the old hybrid
        # DDG lane remains behind the legacy switch.
        self.assertIn("bing_hybrid", default_names)
        self.assertNotIn("ddg_hybrid", default_names)
        self.assertIn("bing_hybrid", legacy_names)
        self.assertIn("ddg_hybrid", legacy_names)

    def test_bing_thumbnail_is_preserved_for_blocked_original_retry(self):
        from services.web_image import resolver

        with mock.patch.object(
            resolver,
            "search_bing",
            return_value=[{
                "image_url": "https://publisher.example/original.jpg",
                "thumbnail_url": "https://bing.example/thumb.jpg",
                "title": "Titanic lifeboats",
                "source_url": "https://publisher.example/story",
            }],
        ):
            rows = resolver._discover_hybrid_urls(
                "bing_hybrid", "RMS Titanic lifeboats"
            )

        assert rows[0]["url"] == "https://publisher.example/original.jpg"
        assert rows[0]["preview_url"] == "https://bing.example/thumb.jpg"
        assert rows[0]["source_url"] == "https://publisher.example/story"

    def test_bing_parser_accepts_regional_single_quote_markup(self):
        from services.web_image.hybrid_search import _parse_bing_html

        payload = (
            '{"murl":"https://images.example/titanic.jpg",'
            '"turl":"https://bing.example/titanic-thumb.jpg",'
            '"purl":"https://archive.example/titanic",'
            '"t":"Titanic lifeboats","w":1200,"h":800}'
        )
        html = "<a class='iusc extra' m='" + payload + "'></a>"
        rows = _parse_bing_html(html, 5)

        assert rows[0]["image_url"] == "https://images.example/titanic.jpg"
        assert rows[0]["thumbnail_url"] == "https://bing.example/titanic-thumb.jpg"
        assert rows[0]["width"] == 1200

    def test_provider_download_headers_use_publisher_for_originals(self):
        from services.web_image.resolver import _provider_download_headers

        item = {
            "provider": "yandex",
            "url": "https://publisher.example/original.jpg",
            "preview_url": "https://yandex.example/preview.jpg",
            "source_url": "https://publisher.example/article",
        }
        headers = _provider_download_headers([item])
        assert headers[item["url"]]["Referer"] == item["source_url"]
        assert headers[item["preview_url"]]["Referer"].startswith("https://yandex.com/images")

    def test_resolver_audit_contains_sanitized_queries_and_broker_funnel(self):
        from services.web_image import resolver

        selected = {
            "path": r"C:\temp\titanic.jpg",
            "width": 1280,
            "height": 720,
            "url": "https://example.com/titanic.jpg",
            "domain": "wikimedia",
            "reused_from_scene": False,
            "query_used": "RMS Titanic lifeboats",
            "title": "RMS Titanic lifeboats",
            "source_url": "https://commons.wikimedia.org/wiki/File:Titanic.jpg",
            "license": "Public domain",
            "rights_complete": True,
        }
        with mock.patch.object(resolver, "_resolve_scene", return_value=selected):
            result = resolver.resolve_web_images_for_video_fast(
                script="Passengers entered lifeboats after the collision.",
                title="RMS Titanic",
                scene_texts=["Passengers entered lifeboats after the collision."],
                scene_durations=[4.0],
                scene_queries_override=[{
                    "query_variants": [
                        "RMS Titanic lifeboats lifeboats football portrait",
                        "RMS Titanic match",
                    ],
                }],
                subject_override="RMS Titanic",
                output_namespace="source_broker_audit_test",
                fast_mode=True,
                max_scene_workers=1,
            )

        queries = result["audit"]["scenes"][0]["queries"]
        self.assertTrue(queries)
        self.assertNotIn("football", " ".join(queries).lower())
        self.assertNotIn(" match", " ".join(queries).lower())
        self.assertIn("source_broker", result["audit"])
        self.assertTrue(result["audit"]["source_broker"]["query_repairs"])


class OfficialStockSourceTests(unittest.TestCase):
    def test_library_of_congress_retains_item_rights_and_provenance(self):
        from services.sources.loc_source import LibraryOfCongressSource

        source = LibraryOfCongressSource()
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [{
                "id": "https://www.loc.gov/item/2018670001/",
                "url": "https://www.loc.gov/pictures/item/2018670001/",
                "title": "Titanic lifeboats approach Carpathia",
                "description": ["Survivors in a lifeboat"],
                "image_url": [
                    "https://tile.loc.gov/small.jpg",
                    "https://tile.loc.gov/large.jpg",
                ],
                "rights": ["No known restrictions on publication."],
                "contributors": ["Bain News Service"],
                "date": "1912",
            }],
        }
        source.session.get = mock.Mock(return_value=response)

        results = source.search("RMS Titanic lifeboats", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].download_url, "https://tile.loc.gov/large.jpg")
        self.assertIn("No known restrictions", results[0].license)
        self.assertEqual(
            results[0].metadata["foreign_url"],
            "https://www.loc.gov/pictures/item/2018670001/",
        )
        self.assertIn("Bain News Service", results[0].metadata["attribution"])
        params = source.session.get.call_args.kwargs["params"]
        self.assertEqual(params["fo"], "json")
        self.assertEqual(params["q"], "rms titanic lifeboats")

    def test_pexels_uses_official_api_and_retains_attribution(self):
        from services.sources.pexels_source import PexelsSource

        source = PexelsSource(api_key="test-key")
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "photos": [{
                "id": 42,
                "width": 2400,
                "height": 1600,
                "url": "https://www.pexels.com/photo/42/",
                "photographer": "Jane Doe",
                "photographer_url": "https://www.pexels.com/@jane",
                "alt": "Ocean liner at sea",
                "src": {
                    "large2x": "https://images.pexels.com/photos/42/large.jpeg",
                    "medium": "https://images.pexels.com/photos/42/medium.jpeg",
                },
            }],
        }
        source.session.get = mock.Mock(return_value=response)

        results = source.search("ocean liner", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].license, "Pexels License")
        self.assertIn("Jane Doe", results[0].metadata["attribution"])
        self.assertEqual(results[0].metadata["foreign_url"], "https://www.pexels.com/photo/42/")
        call = source.session.get.call_args
        self.assertEqual(call.args[0], "https://api.pexels.com/v1/search")
        self.assertEqual(call.kwargs["headers"]["Authorization"], "test-key")
        self.assertNotIn("size", call.kwargs["params"])

    def test_pixabay_uses_official_api_and_caches_for_24_hours(self):
        from services.sources.pixabay_source import PixabaySource

        source = PixabaySource(api_key="test-key")
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "hits": [{
                "id": 7,
                "pageURL": "https://pixabay.com/photos/id-7/",
                "largeImageURL": "https://cdn.pixabay.com/photo/7_1280.jpg",
                "previewURL": "https://cdn.pixabay.com/photo/7_150.jpg",
                "imageWidth": 1920,
                "imageHeight": 1080,
                "tags": "iceberg, ocean",
                "user": "Historian",
                "user_id": 12,
            }],
        }
        source.session.get = mock.Mock(return_value=response)

        first = source.search("iceberg ocean", limit=5)
        second = source.search("iceberg ocean", limit=5)

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].license, "Pixabay Content License")
        self.assertEqual(first[0].download_url, second[0].download_url)
        self.assertEqual(source.session.get.call_count, 1)
        params = source.session.get.call_args.kwargs["params"]
        self.assertEqual(params["safesearch"], "true")
        self.assertEqual(params["orientation"], "horizontal")
        self.assertEqual(source.CACHE_SECONDS, 24 * 60 * 60)

    def test_wikimedia_enriches_only_bounded_shortlist(self):
        from services.sources.wikimedia_source import WikimediaSource

        source = WikimediaSource()
        discovery = mock.Mock()
        discovery.raise_for_status.return_value = None
        discovery.json.return_value = {
            "query": {
                "pages": [{
                    "pageid": 101,
                    "title": "File:RMS Titanic.jpg",
                    "imageinfo": [{
                        "mime": "image/jpeg",
                        "url": "https://upload.wikimedia.org/titanic.jpg",
                        "thumburl": "https://upload.wikimedia.org/titanic-1280.jpg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:RMS_Titanic.jpg",
                        "thumbwidth": 1280,
                        "thumbheight": 720,
                    }],
                }],
            },
        }
        enrichment = mock.Mock()
        enrichment.raise_for_status.return_value = None
        enrichment.json.return_value = {
            "query": {
                "pages": [{
                    "pageid": 101,
                    "imageinfo": [{
                        "extmetadata": {
                            "Artist": {"value": "<b>Archive</b>"},
                            "Credit": {"value": "National archive"},
                            "LicenseShortName": {"value": "Public domain"},
                            "LicenseUrl": {"value": "https://creativecommons.org/publicdomain/mark/1.0/"},
                            "ImageDescription": {"value": "<i>RMS Titanic</i> at sea"},
                        },
                    }],
                }],
            },
        }
        source.session.get = mock.Mock(side_effect=[discovery, enrichment])

        candidates = source.search("RMS Titanic", limit=12)
        enriched = source.enrich_rights(candidates, limit=6)

        self.assertEqual(len(enriched), 1)
        self.assertEqual(enriched[0].license, "Public domain")
        self.assertEqual(enriched[0].metadata["creator"], "Archive")
        self.assertEqual(enriched[0].description, "RMS Titanic at sea")
        first_params = source.session.get.call_args_list[0].kwargs["params"]
        second_params = source.session.get.call_args_list[1].kwargs["params"]
        self.assertNotIn("extmetadata", first_params["iiprop"])
        self.assertEqual(second_params["iiprop"], "extmetadata")
        self.assertEqual(second_params["pageids"], "101")


if __name__ == "__main__":
    unittest.main()
