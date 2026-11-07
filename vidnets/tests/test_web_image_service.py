import os
import sys
import unittest
from unittest import mock
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add server directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import web_image_service as wis
from services.web_image import (
    generate_web_image_queries,
    _generate_queries_mechanical,
    _extract_main_subject,
    _ensure_query_diversity,
    _derive_effective_web_title,
    search_flickr_api,
    resolve_gallery_dl,
)


class WebImageModularTests(unittest.TestCase):
    def setUp(self):
        from services.web_image import flickr_api_source
        flickr_api_source._flickr_session = None

    def test_derive_effective_web_title(self):
        title, terms = _derive_effective_web_title(
            "Donald Trump tariffs on China",
            "Donald Trump announces new tariffs on Chinese goods."
        )
        self.assertIn("trump", title.lower())
        self.assertIn("donald", title.lower())

    def test_extract_main_subject_fictional_franchise(self):
        subject = _extract_main_subject(
            "In the animated town of Springfield, Homer Simpson and Bart Simpson get in trouble."
        )
        self.assertEqual(subject, "The Simpsons")

    def test_extract_main_subject_documentary(self):
        subject = _extract_main_subject(
            "The Vietnam War, a conflict in Southeast Asia, reshaped global politics."
        )
        self.assertEqual(subject, "Vietnam War")

    def test_extract_main_subject_prioritize_title(self):
        title = "Virat Kohli, born on November 5,"
        script = "Sri Lanka is a beautiful country. Sri Lanka won the match. Sri Lanka has great players."
        effective, _ = _derive_effective_web_title(title, script)
        self.assertEqual(effective, "Virat Kohli")

    def test_mechanical_queries_use_typed_visual_nouns(self):
        person = _generate_queries_mechanical(
            "Virat Kohli is an Indian cricketer.",
            "Virat Kohli biography",
            ["Virat Kohli bats in a cricket match."],
        )[0]["query_variants"]
        self.assertTrue(any("portrait" in query or "photo" in query for query in person))
        self.assertTrue(all("Virat Kohli" in query for query in person))

        place = _generate_queries_mechanical(
            "A guide to Paris and its landmarks.",
            "Paris travel guide",
            ["The Eiffel Tower defines the Paris skyline."],
        )[0]["query_variants"]
        self.assertTrue(any("landmark" in query or "location" in query for query in place))
        self.assertFalse(any("portrait" in query for query in place))

        product = _generate_queries_mechanical(
            "A look at the Tesla Model 3 electric car.",
            "Tesla Model 3",
            ["The Model 3 interior and dashboard."],
        )[0]["query_variants"]
        self.assertTrue(any("product" in query or "detail" in query for query in product))
        self.assertFalse(any("portrait" in query for query in product))

        food = _generate_queries_mechanical(
            "Apples provide fiber and vitamin C.",
            "Apple nutrition",
            ["Fresh apples at a market."],
        )[0]["query_variants"]
        self.assertTrue(any("produce" in query or "food" in query for query in food))
        self.assertFalse(any("portrait" in query for query in food))

    def test_scene_queries_keep_qualifiers_and_actions_local(self):
        scenes = [
            "Virat Kohli enters the stadium for a match.",
            "Virat Kohli bats against Australia in the final.",
            "Virat Kohli lifts the trophy after the win.",
        ]
        rows = generate_web_image_queries(
            " ".join(scenes), "Virat Kohli biography", scenes, api_key=""
        )
        self.assertNotIn("Australia", rows[0]["query_variants"][0])
        self.assertIn("batting", rows[1]["query_variants"][0])
        self.assertIn("Australia", rows[1]["query_variants"][0])
        self.assertIn("trophy", rows[2]["query_variants"][0])

    def test_web_image_rejects_cross_niche_candidate_from_taxonomy(self):
        from services.web_image.resolver import _pre_score_candidate, _topic_candidate_mismatch

        self.assertTrue(_topic_candidate_mismatch(
            title="FIFA football goal celebration",
            url="https://example.com/soccer-world-cup.jpg",
            query="Virat Kohli cricket batting",
            scene_text="Kohli scores a match-winning cricket century",
            main_subject="Virat Kohli",
        ))
        self.assertTrue(_topic_candidate_mismatch(
            title="Happy dog running through grass",
            url="https://example.com/dog-park.jpg",
            query="Kylian Mbappe goal",
            scene_text="Kylian Mbappe attacks the box and scores.",
            main_subject="Kylian Mbappe",
        ))
        self.assertLess(
            _pre_score_candidate(
                url="https://example.com/dog-park.jpg",
                title="Happy dog running through grass",
                source="flickr_api",
                query="Kylian Mbappe goal",
                scene_text="Kylian Mbappe attacks the box and scores.",
                identity_terms={"kylian", "mbappe"},
                main_subject="Kylian Mbappe",
            ),
            -40,
        )

    def test_web_image_relevance_floor_rejects_negative_unanchored_topic_hit(self):
        from services.web_image.resolver import _passes_web_relevance_floor

        self.assertFalse(_passes_web_relevance_floor(
            {
                "title": "Amazon oil absorbing pads",
                "url": "https://example.com/oil-pads.jpg",
                "query_used": "Grand Theft Auto revenue potentially",
            },
            -2.0,
            "Grand Theft Auto",
        ))
        self.assertFalse(_passes_web_relevance_floor(
            {
                "title": "Found this pastel practice sketch",
                "url": "https://example.com/practice-sketch.jpg",
                "query_used": "Grand Theft Auto trailer",
            },
            5.5,
            "Grand Theft Auto",
        ))
        self.assertTrue(_passes_web_relevance_floor(
            {
                "title": "Grand Theft Auto VI Rockstar Games trailer",
                "url": "https://example.com/gta-vi.jpg",
                "query_used": "Grand Theft Auto revenue",
            },
            -0.5,
            "Grand Theft Auto",
        ))

    def test_web_relevance_rejects_wrong_franchise_version(self):
        from services.web_image.resolver import _passes_web_relevance_floor

        self.assertFalse(_passes_web_relevance_floor(
            {
                "title": "GTA 5 Rockstar Games anniversary event",
                "url": "https://example.com/gta-5-grand-theft-auto-v.jpg",
                "query_used": "Grand Theft Auto VI game artwork",
            },
            9.0,
            "Grand Theft Auto VI",
        ))
        self.assertTrue(_passes_web_relevance_floor(
            {
                "title": "Grand Theft Auto VI official key artwork",
                "url": "https://example.com/gta-6-key-art.jpg",
                "query_used": "Grand Theft Auto VI game artwork",
            },
            9.0,
            "Grand Theft Auto VI",
        ))

    def test_web_relevance_rejects_grand_canyon_for_grand_theft_auto(self):
        from services.web_image.resolver import _passes_web_relevance_floor

        # Must reject Grand Canyon even with positive score
        self.assertFalse(_passes_web_relevance_floor(
            {
                "title": "Visiter Le Grand Canyon National Park",
                "url": "https://corkhousedenver.com/visiter-le-grand-canyon/",
                "query_used": "Grand Theft Auto VI documentary documentary photograph",
            },
            0.25,
            "Grand Theft Auto VI",
        ))
        self.assertFalse(_passes_web_relevance_floor(
            {
                "title": "Visiter Le Grand Canyon National Park",
                "url": "https://corkhousedenver.com/visiter-le-grand-canyon/",
                "query_used": "Grand Theft Auto documentary photograph",
            },
            8.0,
            "Grand Theft Auto",
        ))
        self.assertFalse(_passes_web_relevance_floor(
            {
                "title": "Grand-Place of Brussels Historic Architecture",
                "url": "https://example.com/brussels-grand-place.jpg",
                "query_used": "Grand Theft Auto trailer",
            },
            7.0,
            "Grand Theft Auto",
        ))
        # Must accept valid GTA content with acronym and version
        self.assertTrue(_passes_web_relevance_floor(
            {
                "title": "GTA 6 Vice City screenshot wallpaper",
                "url": "https://example.com/gta-6-vice-city.jpg",
                "query_used": "Grand Theft Auto VI gameplay",
            },
            0.5,
            "Grand Theft Auto VI",
        ))

    def test_topic_fallback_queries_keep_generic_terms_anchored(self):
        from services.web_image.query import _topic_fallback_queries

        queries = _topic_fallback_queries("Vietnam War", "old archive context", limit=5)

        self.assertTrue(queries)
        self.assertTrue(all("Vietnam War" in q for q in queries))

    def test_franchise_subject_does_not_drift_to_scene_character(self):
        from services.web_image.resolver import _should_switch_active_subject

        self.assertFalse(_should_switch_active_subject(
            "Jason",
            "Grand Theft Auto VI",
            "Jason and Lucia appear in the trailer.",
        ))
        self.assertTrue(_should_switch_active_subject(
            "Rockstar Games",
            "Grand Theft Auto VI",
            "Rockstar Games built the reveal around Vice City.",
        ))

    def test_game_query_repair_removes_sports_leaks(self):
        from app import _repair_primary_anchor_query_rows

        repaired = _repair_primary_anchor_query_rows(
            [{"youtube_query": "Grand Theft Auto VI fielding 2026"}],
            "Grand Theft Auto VI",
            ["For more than a decade gamers waited for Grand Theft Auto VI fielding 2026"],
        )

        self.assertIn("Grand Theft Auto VI", repaired[0]["youtube_query"])
        self.assertNotIn("fielding", repaired[0]["youtube_query"].lower())
        self.assertIn("gameplay trailer", repaired[0]["youtube_query"].lower())

    def test_resolve_topic_anchor_prioritize_title(self):
        from app import _resolve_topic_anchor
        title = "Virat Kohli, born on November 5,"
        script = "Sri Lanka is a beautiful country. Sri Lanka won the match. Sri Lanka has great players."
        anchor = _resolve_topic_anchor(title, script)
        self.assertEqual(anchor, "Virat Kohli")

    def test_semantic_query_repair_switches_subjects_in_ranked_list(self):
        from app import _repair_primary_anchor_query_rows

        scenes = [
            "Number five is John Cena, the sixteen-time champion.",
            "Number four is The Undertaker, whose WrestleMania streak became legendary.",
            "Number three is Stone Cold Steve Austin, the face of the Attitude Era.",
        ]
        rows = [
            {"section_subject": "John Cena", "scene_subject": "John Cena", "youtube_query": "John Cena WWE highlights"},
            {"section_subject": "The Undertaker", "scene_subject": "The Undertaker", "youtube_query": "Undertaker WrestleMania highlights"},
            {"section_subject": "Stone Cold Steve Austin", "scene_subject": "Stone Cold Steve Austin", "youtube_query": "Stone Cold Steve Austin interview"},
        ]

        repaired = _repair_primary_anchor_query_rows(rows, "Top five greatest WWE wrestlers", scenes)

        self.assertEqual([row["section_subject"] for row in repaired], [
            "John Cena",
            "The Undertaker",
            "Stone Cold Steve Austin",
        ])
        self.assertNotIn("John Cena", repaired[1]["youtube_query"])
        self.assertIn("Undertaker", repaired[1]["youtube_query"])

    def test_semantic_query_repair_combines_person_with_secondary_organization(self):
        from app import _repair_primary_anchor_query_rows

        repaired = _repair_primary_anchor_query_rows(
            [{
                "section_subject": "John Cena",
                "scene_subject": "Make-A-Wish Foundation",
                "scene_subject_type": "organization",
                "youtube_query": "Make A Wish record footage",
            }],
            "John Cena Make-A-Wish Foundation record",
            ["John Cena granted more wishes through the Make-A-Wish Foundation than any celebrity."],
        )

        self.assertEqual(repaired[0]["section_subject"], "John Cena")
        self.assertEqual(repaired[0]["scene_subject"], "Make-A-Wish Foundation")
        self.assertIn("John Cena", repaired[0]["youtube_query"])
        self.assertIn("Make-A-Wish Foundation", repaired[0]["youtube_query"])

    def test_semantic_query_repair_tracks_sections_without_llm_rows(self):
        from app import _repair_primary_anchor_query_rows

        scenes = [
            "Number three is John Cena.",
            "His Make-A-Wish work set a record.",
            "Number two is The Undertaker.",
            "His WrestleMania streak became legendary.",
        ]
        repaired = _repair_primary_anchor_query_rows(
            [{} for _ in scenes],
            "greatest professional wrestlers",
            scenes,
        )

        self.assertEqual(repaired[1]["section_subject"], "John Cena")
        self.assertIn("Make-A-Wish", repaired[1]["scene_subject"])
        self.assertEqual(repaired[2]["section_subject"], "The Undertaker")
        self.assertEqual(repaired[3]["section_subject"], "The Undertaker")

    def test_llm_semantic_plan_rejects_metadata_only_json(self):
        from services.web_image.query import _parse_llm_response, _valid_semantic_query_rows

        parsed = _parse_llm_response('{"encoding":"utf-8"}')

        self.assertFalse(_valid_semantic_query_rows(parsed, 3))

    def test_llm_semantic_parser_preserves_global_topic(self):
        from services.web_image.query import _parse_llm_response

        parsed = _parse_llm_response({
            "global_topic": "greatest professional wrestlers",
            "queries": [{
                "section_subject": "The Undertaker",
                "scene_subject": "The Undertaker",
                "specific": "Undertaker wrestling",
                "youtube_query": "Undertaker WWE highlights",
            }],
        })

        self.assertEqual(parsed[0]["global_topic"], "greatest professional wrestlers")
        self.assertEqual(parsed[0]["topic_anchor"], "greatest professional wrestlers")

    def test_ranked_section_owner_persists_until_next_rank_boundary(self):
        from app import _repair_primary_anchor_query_rows

        scenes = [
            "Number two is The Undertaker.",
            "His WrestleMania streak made him an icon.",
            "A rivalry with Shawn Michaels produced classic matches.",
            "WWE built major events around his supernatural character.",
            "Coming in at number one is Stone Cold Steve Austin.",
            "His battles with Vince McMahon defined the Attitude Era.",
        ]
        rows = [
            {},
            {},
            {"section_subject": "WrestleMania", "scene_subject": "WrestleMania"},
            {"section_subject": "WWE", "scene_subject": "WWE"},
            {},
            {"section_subject": "Vince McMahon", "scene_subject": "Vince McMahon"},
        ]

        repaired = _repair_primary_anchor_query_rows(
            rows,
            "top professional wrestlers",
            scenes,
        )

        self.assertEqual(
            [row["section_subject"] for row in repaired],
            [
                "The Undertaker",
                "The Undertaker",
                "The Undertaker",
                "The Undertaker",
                "Stone Cold Steve Austin",
                "Stone Cold Steve Austin",
            ],
        )
        self.assertIn("Shawn Michaels", repaired[2]["scene_subject"])
        self.assertIn("The Undertaker", repaired[2]["youtube_query"])
        self.assertIn("Vince McMahon", repaired[5]["scene_subject"])
        self.assertIn("Stone Cold Steve Austin", repaired[5]["youtube_query"])

    def test_ranked_section_hints_cross_parallel_chunk_boundaries(self):
        from services.web_image.query import _ranked_section_hints

        scenes = [
            "Number three is John Cena.",
            "His championship record made him famous.",
            "Make-A-Wish became part of his legacy.",
            "Hollywood expanded his audience.",
            "Number two is The Undertaker.",
            "His WrestleMania streak became legendary.",
            "Shawn Michaels challenged him twice.",
            "WWE presented him as a supernatural force.",
        ]

        hints = _ranked_section_hints(scenes)

        self.assertEqual(hints[:4], ["John Cena"] * 4)
        self.assertEqual(hints[4:], ["The Undertaker"] * 4)

    def test_flowing_paragraph_script_keeps_subject_until_real_transition(self):
        from app import _repair_primary_anchor_query_rows

        scenes = [
            "John Cena rebuilt his career through discipline and consistency.",
            "His work with Make-A-Wish brought hope to hundreds of children.",
            "The foundation recognized him for a record number of wishes.",
            "A partnership with WWE helped those visits reach more families.",
            "Meanwhile, The Undertaker created a completely different legacy.",
            "His WrestleMania streak became central to that character.",
            "Matches with Shawn Michaels elevated the story even further.",
            "The arena entrances made The Undertaker instantly recognizable.",
        ]
        rows = [
            {"section_id": "cena", "section_transition": True, "section_subject": "John Cena", "scene_subject": "John Cena"},
            {"section_id": "cena", "section_subject": "John Cena", "scene_subject": "Make-A-Wish"},
            {"section_id": "cena", "section_subject": "John Cena", "scene_subject": "Make-A-Wish Foundation"},
            {"section_id": "cena", "section_subject": "WWE", "scene_subject": "WWE"},
            {"section_id": "undertaker", "section_transition": True, "section_subject": "The Undertaker", "scene_subject": "The Undertaker"},
            {"section_id": "undertaker", "section_subject": "The Undertaker", "scene_subject": "WrestleMania"},
            {"section_id": "undertaker", "section_subject": "Shawn Michaels", "scene_subject": "Shawn Michaels"},
            {"section_id": "undertaker", "section_subject": "The Undertaker", "scene_subject": "The Undertaker"},
        ]

        repaired = _repair_primary_anchor_query_rows(
            rows,
            "professional wrestling legacies",
            scenes,
        )

        self.assertEqual(
            [row["section_subject"] for row in repaired],
            ["John Cena"] * 4 + ["The Undertaker"] * 4,
        )
        self.assertEqual(repaired[3]["scene_subject"], "WWE")
        self.assertIn("John Cena", repaired[3]["youtube_query"])
        self.assertEqual(repaired[6]["scene_subject"], "Shawn Michaels")
        self.assertIn("The Undertaker", repaired[6]["youtube_query"])

    def test_flowing_script_can_transition_between_non_person_subjects(self):
        from app import _repair_primary_anchor_query_rows

        scenes = [
            "The Amazon rainforest regulates rainfall across South America.",
            "Its rivers move enormous amounts of fresh water.",
            "Now, the Sahara Desert shapes weather in a different way.",
            "Its dust crosses the Atlantic and feeds distant ecosystems.",
        ]
        rows = [
            {"section_id": "amazon", "section_transition": True, "section_subject": "Amazon rainforest", "scene_subject": "Amazon rainforest"},
            {"section_id": "amazon", "section_subject": "Amazon rainforest", "scene_subject": "Amazon rivers"},
            {"section_id": "sahara", "section_transition": True, "section_subject": "Sahara Desert", "scene_subject": "Sahara Desert"},
            {"section_id": "sahara", "section_subject": "Sahara Desert", "scene_subject": "Sahara dust"},
        ]

        repaired = _repair_primary_anchor_query_rows(
            rows,
            "global climate systems",
            scenes,
        )

        self.assertEqual(
            [row["section_subject"] for row in repaired],
            ["Amazon rainforest", "Amazon rainforest", "Sahara Desert", "Sahara Desert"],
        )

    def test_semantic_repair_replaces_talking_head_youtube_queries(self):
        from app import _repair_primary_anchor_query_rows

        repaired = _repair_primary_anchor_query_rows(
            [{
                "section_subject": "John Cena",
                "scene_subject": "John Cena",
                "youtube_query": "John Cena podcast reaction",
                "youtube_supporting_query": "John Cena exclusive interview",
                "youtube_fallback_query": "John Cena streamer commentary",
            }],
            "John Cena wrestling career",
            ["John Cena trained relentlessly before reaching WWE."],
        )

        combined = " ".join([
            repaired[0]["youtube_query"],
            repaired[0]["youtube_supporting_query"],
            repaired[0]["youtube_fallback_query"],
        ]).lower()
        self.assertNotIn("podcast", combined)
        self.assertNotIn("interview", combined)
        self.assertNotIn("streamer", combined)
        self.assertNotIn("commentary", combined)

    def test_extract_main_subject_strip_garbage(self):
        subject = _extract_main_subject("Statistics Leading Virat Kohli played a great inning.")
        self.assertEqual(subject, "Virat Kohli")

    def test_path_resolution_with_subdirectories(self):
        from app import _path_to_url, _local_media_path_for_render, TEMP_DIR, OUTPUT_DIR
        
        # Test TEMP_DIR relative paths
        sub_temp_path = os.path.join(str(TEMP_DIR), "web_images_simple", "scene_1", "img_abc.jpg")
        url_temp = _path_to_url(sub_temp_path)
        self.assertEqual(url_temp, "/temp/web_images_simple/scene_1/img_abc.jpg")
        
        local_temp = _local_media_path_for_render(url_temp)
        self.assertEqual(os.path.normpath(local_temp), os.path.normpath(sub_temp_path))
        
        # Test OUTPUT_DIR relative paths
        sub_output_path = os.path.join(str(OUTPUT_DIR), "renders", "scene_2", "output_xyz.mp4")
        url_output = _path_to_url(sub_output_path)
        self.assertEqual(url_output, "/output/renders/scene_2/output_xyz.mp4")
        
        local_output = _local_media_path_for_render(url_output)
        self.assertEqual(os.path.normpath(local_output), os.path.normpath(sub_output_path))

        local_media = _local_media_path_for_render('local-media:///C:/Media/scene%20one.jpg')
        self.assertEqual(local_media, 'C:/Media/scene one.jpg')

    def test_generate_queries_mechanical(self):
        rows = _generate_queries_mechanical(
            script="Cristiano Ronaldo owns multiple hypercars including a Bugatti Veyron.",
            title="Cristiano Ronaldo Bugatti Veyron",
            scene_texts=["He also owns the Bugatti Veyron in his Monaco garage."]
        )
        self.assertEqual(len(rows), 1)
        self.assertIn("search_query", rows[0])
        self.assertTrue(len(rows[0]["search_query"]) > 0)

    @mock.patch("services.web_image.flickr_api_source.requests.Session")
    def test_flickr_api_search(self, mock_session_cls):
        mock_session = mock_session_cls.return_value
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "photos": {
                "photo": [
                    {
                        "id": "12345",
                        "owner": "owner1",
                        "secret": "abc",
                        "server": "srv1",
                        "title": "Steve Jobs",
                        "tags": "apple,computer,portrait",
                        "views": "1500",
                        "url_b": "https://live.staticflickr.com/srv1/12345_abc_b.jpg"
                    }
                ]
            }
        }
        mock_session.get.return_value = mock_response

        results = search_flickr_api("Steve Jobs Apple", limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Steve Jobs")
        self.assertEqual(results[0]["image_url"], "https://live.staticflickr.com/srv1/12345_abc_b.jpg")
        self.assertEqual(results[0]["domain"], "flickr.com")

    @mock.patch("services.web_image.resolver._resolve_scene")
    @mock.patch("services.web_image.query.generate_web_image_queries")
    def test_resolve_web_images_for_video_fast(self, mock_queries, mock_resolve):
        mock_queries.return_value = [
            {
                "search_query": "test query",
                "supporting_search_query": "",
                "alt_search_query": "",
                "scene_family": "theme",
                "topic_tag": "general",
                "topic_anchor": "Video",
                "subject_query": "subject",
            }
        ]
        mock_resolve.return_value = {
            "path": r"C:\temp\scene_1.jpg",
            "width": 1280,
            "height": 720,
            "url": "https://example.com/photo.jpg",
            "domain": "example.com",
            "reused_from_scene": False,
            "query_used": "test query",
            "title": "test title",
            "source_url": "https://example.com/photo",
            "pre_score": 5.0,
            "quality_score": 4.0,
            "final_score": 9.0,
            "identity_failed": False,
        }

        result = wis.resolve_web_images_for_video_fast(
            script="Scene 1",
            title="Video",
            scene_texts=["Scene 1"],
            scene_durations=[3.0],
            api_key="test-key",
            gallery_cmd=["gallery-dl"],
        )

        self.assertEqual(result["audit"]["freshness_mode"], "streaming-live-search")
        self.assertEqual(result["audit"]["scene_count"], 1)
        self.assertEqual(result["image_paths"], [r"C:\temp\scene_1.jpg"])
        self.assertTrue(result["audit"]["gallery_enabled"])

    @mock.patch("services.web_image.runtime.subprocess.run")
    def test_resolve_gallery_dl_validation(self, mock_run):
        mock_result = mock.Mock()
        mock_result.return_value = None
        mock_result.return_code = 0
        mock_result.stdout = "gallery-dl 1.25.0"
        mock_run.return_value = mock_result

        # Force a rescan to check subprocess calls
        cmd = resolve_gallery_dl(required=False, force_rescan=True)
        # Even if none found on system, it shouldn't raise exception when required=False
        self.assertTrue(cmd is None or isinstance(cmd, list))

    @mock.patch("services.web_image.resolver._discover_flickr_api_urls")
    def test_query_cache_prevents_duplicate_calls(self, mock_flickr_api):
        mock_flickr_api.return_value = [{"url": "https://example.com/1.jpg", "title": "A", "source": "flickr_api", "query": "test_query"}]
        
        from services.web_image.resolver import _SEARCH_QUERY_CACHE, _CACHE_LOCK
        with _CACHE_LOCK:
            _SEARCH_QUERY_CACHE.clear()

        from services.web_image.resolver import _resolve_scene
        import threading
        from pathlib import Path
        
        global_phashes = set()
        global_md5s = set()
        used_files = set()
        phash_lock = threading.Lock()
        md5_lock = threading.Lock()
        file_lock = threading.Lock()
        
        with mock.patch("services.web_image.resolver.download_bulk") as mock_dl:
            mock_dl.return_value = [("https://example.com/1.jpg", Path("C:/temp/1.jpg"), "")]
            with mock.patch("services.web_image.resolver._validate_image") as mock_val:
                mock_val.return_value = (800, 600)
                
                _resolve_scene(
                    idx=0, scene_text="test text", queries=["test_query"],
                    gdl=None, output_dir=Path("C:/temp"), main_subject="",
                    global_phashes=global_phashes, global_md5s=global_md5s, used_files=used_files,
                    phash_lock=phash_lock, md5_lock=md5_lock, file_lock=file_lock,
                    fast_mode=True
                )
                
                _resolve_scene(
                    idx=1, scene_text="test text 2", queries=["test_query"],
                    gdl=None, output_dir=Path("C:/temp"), main_subject="",
                    global_phashes=global_phashes, global_md5s=global_md5s, used_files=used_files,
                    phash_lock=phash_lock, md5_lock=md5_lock, file_lock=file_lock,
                    fast_mode=True
                )
        
        self.assertEqual(mock_flickr_api.call_count, 1)

    @mock.patch("services.web_image.resolver._discover_api_urls")
    @mock.patch("services.web_image.resolver._discover_hybrid_urls")
    @mock.patch("services.web_image.resolver._discover_flickr_api_urls")
    @mock.patch("services.web_image.resolver._pre_score_candidate")
    def test_adaptive_download_limit_excellent_match(self, mock_pre_score, mock_flickr_api, mock_hybrid, mock_api):
        mock_flickr_api.return_value = [
            {"url": "https://example.com/1.jpg", "title": "A", "source": "flickr_api", "query": "test"},
            {"url": "https://example.com/2.jpg", "title": "B", "source": "flickr_api", "query": "test"},
            {"url": "https://example.com/3.jpg", "title": "C", "source": "flickr_api", "query": "test"},
        ]
        mock_hybrid.return_value = []
        mock_api.return_value = []
        
        scores = [6.0, 4.0, 2.0]
        mock_pre_score.side_effect = lambda *args, **kwargs: scores.pop(0) if scores else 0.0
        
        from services.web_image.resolver import _resolve_scene
        import threading
        from pathlib import Path
        
        with mock.patch("services.web_image.resolver.download_bulk") as mock_dl:
            mock_dl.return_value = []
            
            _resolve_scene(
                idx=0, scene_text="test", queries=["test"],
                gdl=None, output_dir=Path("C:/temp"), main_subject="",
                global_phashes=set(), global_md5s=set(), used_files=set(),
                phash_lock=threading.Lock(), md5_lock=threading.Lock(), file_lock=threading.Lock(),
                fast_mode=True
            )
            
            called_urls = mock_dl.call_args[0][0]
            # Medium-confidence matches retain a bounded alternative page;
            # the resolver no longer collapses a healthy result set to two
            # downloads before the visual gate can compare them.
            self.assertEqual(len(called_urls), 3)
            self.assertEqual(
                called_urls,
                [
                    "https://example.com/1.jpg",
                    "https://example.com/2.jpg",
                    "https://example.com/3.jpg",
                ],
            )

    def test_extract_all_potential_subjects(self):
        from services.web_image.query import _extract_all_potential_subjects
        text = "Virat Kohli is an Indian cricketer. Meanwhile, Lionel Messi is a legendary footballer from Argentina."
        subjects = _extract_all_potential_subjects(text)
        self.assertIn("Virat Kohli", subjects)
        self.assertIn("Lionel Messi", subjects)
        # Verify countries are filtered out via GENERIC_TOPIC_WORDS
        self.assertNotIn("India", subjects)

    def test_multi_subject_mechanical_query_generation(self):
        rows = _generate_queries_mechanical(
            script="Virat Kohli walks to the crease. In Qatar, Lionel Messi prepares to kick the ball.",
            title="Kohli vs Messi",
            scene_texts=[
                "Virat Kohli gets ready at the crease.",
                "Lionel Messi preparing for the free kick."
            ]
        )
        self.assertEqual(len(rows), 2)
        # Scene 1 should map to active subject Virat Kohli
        self.assertEqual(rows[0]["topic_anchor"], "Virat Kohli")
        # Scene 2 should map to active subject Lionel Messi
        self.assertEqual(rows[1]["topic_anchor"], "Lionel Messi")
        # Check that scene 2 queries contain Lionel Messi and not Virat Kohli
        scene_2_queries = rows[1]["query_variants"]
        self.assertTrue(any("messi" in q.lower() for q in scene_2_queries))
        self.assertTrue(all("kohli" not in q.lower() for q in scene_2_queries))


if __name__ == "__main__":
    unittest.main()
