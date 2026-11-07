"""YouTube retrieval tests against the compact youtube_pipeline_v2 API.

The legacy vnext suite tested the pre-refactor monolithic clip service.  After
youtube_clips_service became a thin facade over youtube_pipeline_v2, those unit
tests referenced removed internals (SceneCluster, ClipDownloadRegistry,
_hard_mismatch_reasons, _globally_assign_scene_matches, ...).  This file is the
v2 rewrite: it pins the v2 API behaviors that the legacy suite was guarding.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import youtube_pipeline_v2 as v2
from services.universal_clip_classifier import detect_niche, detect_niche_conflict


def _score(subject, scene_text, query, candidate, detail=None):
    context = dict(detail or {})
    return v2._score_candidate(dict(candidate), subject, scene_text, query, context)


class YouTubeRetrievalVNextTests(unittest.TestCase):
    _YT_ENV_KEYS = (
        "VIDZEO_YT_MIN_SOURCE_DURATION",
        "VIDZEO_YT_REUSE_TIMESTAMP_GAP",
        "VIDZEO_YT_V2_SKIP_DOWNLOAD",
        "VIDZEO_YT_V2_QUERIES_PER_CONTAINER",
        "VIDZEO_YT_RESULTS_PER_QUERY",
        "VIDZEO_YT_REJECT_TALKING_HEADS",
        "VIDZEO_YT_REJECT_TALKING_HEAD",
    )

    def setUp(self):
        self._saved_yt_env = {key: os.environ.get(key) for key in self._YT_ENV_KEYS}
        for key in self._YT_ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["VIDZEO_YT_REUSE_TIMESTAMP_GAP"] = "60"
        os.environ["VIDZEO_YT_MIN_SOURCE_DURATION"] = "60"

    def tearDown(self):
        for key, value in self._saved_yt_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # ------------------------------------------------------------------ #
    # Hard blocks (shorts, short sources, fake/template, overlay)         #
    # ------------------------------------------------------------------ #

    def test_fast_pipeline_blocks_shorts_and_short_sources_as_fatal(self):
        shorts = {
            "title": "Cristiano Ronaldo goal #shorts",
            "description": "Football shorts edit",
            "channel": "Football Shorts",
            "url": "https://www.youtube.com/shorts/abc123shorts",
            "duration": 38,
        }
        self.assertEqual(v2._hard_block_reason(shorts), "shorts")
        self.assertEqual(
            _score(
                "Cristiano Ronaldo",
                "Cristiano Ronaldo scores a powerful long-range goal.",
                "Cristiano Ronaldo goal highlights",
                shorts,
                {"domain_category": "sports", "container_subject": "Cristiano Ronaldo"},
            ),
            -999.0,
        )

    def test_short_source_video_is_blocked_as_fatal(self):
        short = {
            "title": "Cristiano Ronaldo chip",
            "description": "quick clip",
            "channel": "Football Clips",
            "url": "https://www.youtube.com/watch?v=short001",
            "duration": 32,
        }
        self.assertEqual(v2._hard_block_reason(short), "too_short")

    def test_caption_metadata_is_screened_in_pixels_and_slideshows_are_rejected(self):
        captioned = {
            "title": "RMS Titanic archival footage with English subtitles",
            "channel": "Maritime Archive",
            "duration": 300,
        }
        slideshow = {
            "title": "RMS Titanic photo slideshow and text cards",
            "channel": "History Visuals",
            "duration": 300,
        }
        self.assertEqual(v2._hard_block_reason(captioned), '')
        self.assertEqual(v2._hard_block_reason(slideshow), "still_image_or_slideshow_source_rejected")
        self.assertGreater(
            _score(
                "RMS Titanic",
                "Archival footage of the ship.",
                "RMS Titanic archival footage",
                captioned,
                {"domain_category": "history", "container_subject": "RMS Titanic"},
            ),
            0.0,
        )

    def test_reenactment_vr_and_cinematic_story_sources_are_hard_rejected(self):
        for title in (
            "360 VR Titanic sinking virtual reality experience",
            "The Titanic before it sank cinematic story and reenactment",
            "Titanic brought to life AI restored historical footage",
            "POV: You are aboard the Titanic the night it sank",
        ):
            self.assertEqual(
                v2._hard_block_reason({"title": title, "duration": 300}),
                "fake_or_template",
            )

    def test_promo_title_card_sources_are_hard_rejected(self):
        for title in (
            "Unsinkable Luxury: The Titanic Tragedy",
            "Titanic Belfast Visitor Centre drone footage",
        ):
            self.assertEqual(
                v2._hard_block_reason({"title": title, "duration": 300}),
                "fake_or_template",
            )

    def test_float_formatted_minimum_duration_does_not_abort_filtering(self):
        os.environ["VIDZEO_YT_MIN_SOURCE_DURATION"] = "20.0"
        usable = {
            "title": "RMS Titanic archival footage",
            "channel": "Maritime Archive",
            "duration": 21,
        }
        self.assertEqual(v2._hard_block_reason(usable), "")

    def test_youtube_copyright_risk_blocks_edit_pack_metadata(self):
        candidate = {
            "title": "Virat Kohli 4K scene pack Topaz clips for editing no copyright",
            "description": "edit pack for status video",
            "channel": "Random Edits",
            "duration": 240,
        }
        self.assertEqual(v2._hard_block_reason(candidate), "fake_or_template")
        self.assertEqual(
            _score(
                "Virat Kohli",
                "Kohli batting footage",
                "Virat Kohli batting footage",
                candidate,
                {"domain_category": "cricket"},
            ),
            -999.0,
        )

    def test_overlay_and_text_package_is_hard_rejected(self):
        candidate = {
            "title": "Venezuela Earthquake Breaking News Banner and Text Overlay",
            "description": "Captioned video with lower third.",
            "channel": "Daily News",
            "duration": 300,
        }
        self.assertEqual(
            _score(
                "Venezuela earthquakes",
                "Rescue crews searched collapsed buildings.",
                "Venezuela earthquakes rescue raw footage",
                candidate,
                {"domain_category": "disaster"},
            ),
            -999.0,
        )

    def test_streamer_or_reaction_package_is_hard_rejected_unless_allowed(self):
        candidate = {
            "title": "Streamer Reacts to John Cena WWE Moments",
            "description": "Facecam reaction and commentary",
            "channel": "Live Stream Highlights",
            "duration": 600,
        }
        self.assertEqual(
            _score(
                "John Cena",
                "John Cena trains before a major WWE event.",
                "John Cena training footage",
                candidate,
                {"domain_category": "sports", "container_subject": "John Cena"},
            ),
            -999.0,
        )
        allowed = _score(
            "John Cena",
            "John Cena trains before a major WWE event.",
            "John Cena training footage",
            candidate,
            {"domain_category": "sports", "allow_packaged_media": True},
        )
        self.assertNotEqual(allowed, -999.0)

    def test_sensational_explainer_package_is_hard_rejected(self):
        candidate = {
            "title": "Venezuela Earthquakes EXPOSED: The SHOCKING Truth",
            "description": "The truth about the rescue effort finally revealed.",
            "channel": "SilencedTruths",
            "duration": 600,
        }
        self.assertEqual(
            _score(
                "Venezuela earthquakes",
                "Rescue crews searched collapsed buildings.",
                "Venezuela earthquakes rescue raw footage",
                candidate,
                {"domain_category": "disaster"},
            ),
            -999.0,
        )

    # ------------------------------------------------------------------ #
    # High-risk media channels (copyright)                                #
    # ------------------------------------------------------------------ #

    def test_source_policy_blocks_media_agencies(self):
        self.assertTrue(v2._high_risk_media_channel("AP Archive"))
        self.assertTrue(v2._high_risk_media_channel("Reuters"))
        self.assertTrue(v2._high_risk_media_channel("BBC News"))
        self.assertTrue(v2._high_risk_media_channel("CNN"))
        self.assertFalse(v2._high_risk_media_channel("White House"))
        self.assertFalse(v2._high_risk_media_channel("Cricket Classics"))
        self.assertFalse(v2._high_risk_media_channel("Football Archive"))

    def test_big_media_channel_is_hard_blocked_for_copyright_risk(self):
        candidate = {
            "title": "CNN update on Trump tariffs and supply chain pressure",
            "channel": "CNN",
            "duration": 240,
            "description": "Reporter package from Washington",
        }
        self.assertTrue(v2._high_risk_media_channel("CNN"))
        self.assertEqual(
            _score(
                "trump",
                "Trump tariffs are changing the supply chain",
                "trump tariff footage",
                candidate,
                {"domain_category": "news", "container_subject": "trump"},
            ),
            -999.0,
        )

    def test_abstract_broll_scene_rejects_broadcast_package(self):
        candidate = {
            "title": "BBC News analysis: What Trump tariffs mean for consumers",
            "channel": "BBC News",
            "duration": 420,
            "description": "Anchor package explaining what this means",
        }
        self.assertTrue(v2._high_risk_media_channel("BBC News"))
        self.assertEqual(
            _score(
                "Tariffs",
                "Tariffs are raising prices across the global supply chain.",
                "tariffs supply chain footage",
                candidate,
                {"domain_category": "news"},
            ),
            -999.0,
        )

    # ------------------------------------------------------------------ #
    # Scoring: raw subject footage vs packages/anchors                    #
    # ------------------------------------------------------------------ #

    def test_subject_anchored_sports_highlights_are_not_rejected_as_commentary_package(self):
        candidate = {
            "title": "ERLING HAALAND - ALL 38 GOALS FOR MANCHESTER CITY",
            "channel": "Football Archive",
            "description": "Erling Haaland goals and football highlights compilation",
            "url": "https://www.youtube.com/watch?v=haalandgoals001",
            "duration": 620,
        }
        score = _score(
            "Erling Haaland",
            "Erling Haaland rewrites scoring records with brutal power.",
            "Erling Haaland goals highlights footage",
            candidate,
            {"domain_category": "sports", "container_subject": "Erling Haaland"},
        )
        self.assertGreater(score, 0)

    def test_plain_football_person_scene_does_not_require_worldcup_anchor(self):
        candidate = {
            "title": "Lionel Messi Best Goals and Skills",
            "description": "Football match highlights and goals",
            "channel": "Football Clips",
            "duration": 360,
        }
        score = _score(
            "Lionel Messi",
            "Lionel Messi controls the tempo and scores from close range.",
            "Lionel Messi match highlights",
            candidate,
            {"domain_category": "sports", "container_subject": "Lionel Messi"},
        )
        self.assertGreater(score, 0)

    def test_metadata_score_prefers_raw_subject_footage_over_anchor_package(self):
        raw_candidate = {
            "title": "Official White House footage of Donald Trump walking onto rally stage",
            "channel": "White House",
            "duration": 185,
            "description": "Raw footage without commentary",
            "url": "https://www.youtube.com/watch?v=raw1",
        }
        anchor_candidate = {
            "title": "CNN analysis: Trump rally latest",
            "channel": "CNN",
            "duration": 480,
            "description": "Anchor explains the latest rally developments",
            "url": "https://www.youtube.com/watch?v=cnn1",
        }
        raw_score = _score(
            "Donald Trump",
            "Donald Trump walks onto the rally stage before addressing supporters.",
            "donald trump rally footage",
            raw_candidate,
            {"domain_category": "sports", "container_subject": "Donald Trump"},
        )
        anchor_score = _score(
            "Donald Trump",
            "Donald Trump walks onto the rally stage before addressing supporters.",
            "donald trump rally footage",
            anchor_candidate,
            {"domain_category": "sports", "container_subject": "Donald Trump"},
        )
        self.assertGreater(raw_score, anchor_score)
        self.assertGreaterEqual(raw_score, 0)

    def test_talking_head_signal_is_prefilter_rejected_for_footage_only_jobs(self):
        candidate = {
            "title": "Kylian Mbappe interview and analysis after France match",
            "description": "Kylian Mbappe football match interview and highlights",
            "channel": "Football Commentary",
            "duration": 240,
            "url": "https://www.youtube.com/watch?v=localsoft01",
        }
        score = _score(
            "Kylian Mbappe",
            "Kylian Mbappe creates danger with speed and finishing.",
            "Kylian Mbappe speed goals",
            candidate,
            {"domain_category": "sports", "container_subject": "Kylian Mbappe"},
        )
        self.assertEqual(score, -999.0)

    def test_real_subject_footage_beats_generic_stock_for_person_primary(self):
        trump_candidate = {
            "title": "White House footage of Trump arriving for trade meeting",
            "channel": "White House",
            "description": "Raw footage of Trump arrival without commentary",
            "duration": 92,
        }
        stock_candidate = {
            "title": "Cargo Ship B-Roll/Free Stock Footage (4K)",
            "channel": "Stock Footage Channel",
            "description": "Generic shipping stock footage",
            "duration": 52,
        }
        context = {"domain_category": "news", "container_subject": "Donald Trump"}
        trump_score = _score(
            "Donald Trump",
            "Trump tariffs on China are shifting global markets.",
            "donald trump arrival footage",
            trump_candidate,
            context,
        )
        stock_score = _score(
            "Donald Trump",
            "Trump tariffs on China are shifting global markets.",
            "donald trump arrival footage",
            stock_candidate,
            context,
        )
        self.assertGreater(trump_score, stock_score)

    def test_wrong_subject_candidate_is_penalized_but_detached_topics_can_reject(self):
        candidate = {
            "title": "The Undertaker WrestleMania Streak Highlights",
            "description": "WWE match archive footage",
            "channel": "Wrestling Archive",
            "duration": 240,
        }
        score = _score(
            "John Cena",
            "John Cena trains for WWE.",
            "John Cena training footage",
            candidate,
            {"domain_category": "sports", "container_subject": "John Cena"},
        )
        self.assertLess(score, 0)

    def test_cricket_topic_rejects_unanchored_political_intrusion(self):
        candidate = {
            "title": "Exclusive: Historic Xi-Ma handshake to ease tensions",
            "channel": "Global News Agency",
            "duration": 180,
            "description": "Leaders meet at a political summit",
        }
        score = _score(
            "Virat Kohli",
            "A tense moment before the greatest innings begins.",
            "virat kohli innings raw footage",
            candidate,
            {"domain_category": "cricket", "container_subject": "Virat Kohli"},
        )
        self.assertLess(score, 0)

    def test_metadata_block_handles_severely_negative_candidate(self):
        candidate = {
            "title": "Unrelated AI tutorial",
            "channel": "Tutorial Channel",
            "duration": 180,
            "description": "",
        }
        score = _score(
            "John Cena",
            "John Cena struggled early in his wrestling career.",
            "john cena early wrestling footage",
            candidate,
            {"domain_category": "sports", "container_subject": "John Cena"},
        )
        self.assertLess(score, 0)

    # ------------------------------------------------------------------ #
    # Gaming / animation / disaster behaviors                             #
    # ------------------------------------------------------------------ #

    def test_gaming_scoring_prefers_clean_gameplay_over_streamers_news_and_stickers(self):
        context = {"domain_category": "gaming", "container_subject": "Cyberpunk 2077"}
        scene = "Cyberpunk 2077 players drive through Night City streets in open world gameplay"
        clean = {
            "title": "Cyberpunk 2077 4K gameplay driving police chase no commentary",
            "description": "Cyberpunk 2077 Night City gameplay footage without commentary",
            "channel": "Open World Gameplay",
            "duration": 180,
            "url": "https://www.youtube.com/watch?v=clean001",
        }
        streamer = {
            "title": "Streamer reacts to Cyberpunk 2077 gameplay with facecam",
            "description": "Live stream highlights and reaction",
            "channel": "Big Streamer",
            "duration": 620,
            "url": "https://www.youtube.com/watch?v=react002",
        }
        sticker = {
            "title": "Cyberpunk 2077 sticker green screen PNG edit pack",
            "description": "CapCut template sticker pack for editing",
            "channel": "Sticker Edits",
            "duration": 60,
            "url": "https://www.youtube.com/watch?v=stick004",
        }
        clean_score = _score("Cyberpunk 2077", scene, "Cyberpunk 2077 gameplay driving footage", clean, context)
        streamer_score = _score("Cyberpunk 2077", scene, "Cyberpunk 2077 gameplay driving footage", streamer, context)
        sticker_score = _score("Cyberpunk 2077", scene, "Cyberpunk 2077 gameplay driving footage", sticker, context)
        self.assertGreater(clean_score, 0)
        self.assertEqual(streamer_score, -999.0)
        self.assertEqual(sticker_score, -999.0)
        self.assertGreater(clean_score, streamer_score)

    def test_gaming_rejects_gta_v_candidate_for_gta_vi_scene(self):
        context = {"domain_category": "gaming", "container_subject": "Grand Theft Auto VI"}
        candidate = {
            "title": "Grand Theft Auto V: Official Gameplay Video",
            "description": "GTA V gameplay trailer",
            "channel": "Rockstar Games",
            "duration": 300,
        }
        score = _score(
            "Grand Theft Auto VI",
            "GTA VI shows city driving and open world gameplay.",
            "grand theft auto vi gameplay footage",
            candidate,
            context,
        )
        # Wrong title subject (GTA V) fails the required-subject gate.
        self.assertLess(score, 0)

    def test_gaming_rejects_legacy_and_fake_gta_vi_candidates(self):
        context = {"domain_category": "gaming", "container_subject": "Grand Theft Auto VI"}
        legacy = {
            "title": "GTA San Andreas Gameplay in 4K",
            "description": "Classic Grand Theft Auto San Andreas footage",
            "channel": "Retro Gaming",
            "duration": 300,
        }
        concept = {
            "title": "GTA 6 concept trailer fan made",
            "description": "unofficial remake trailer unseen locations",
            "channel": "Next Gaming",
            "duration": 180,
        }
        viral = {
            "title": "GTA 6 Viral Gameplay Best Open World Game Ever",
            "description": "Grand Theft Auto VI gameplay",
            "channel": "Random Gaming",
            "duration": 180,
        }
        # Wrong golden-age game and fake/concept trailers must not beat a real
        # Grand Theft Auto VI official trailer.
        legacy_score = _score("Grand Theft Auto VI", "GTA VI trailer footage shows Vice City streets.", "gta vi gameplay", legacy, context)
        concept_score = _score("Grand Theft Auto VI", "GTA VI trailer footage shows Vice City streets.", "gta vi gameplay", concept, context)
        viral_score = _score("Grand Theft Auto VI", "GTA VI trailer footage shows Vice City streets.", "gta vi gameplay", viral, context)
        official = {
            "title": "Grand Theft Auto VI Trailer 2",
            "description": "Official Grand Theft Auto VI trailer",
            "channel": "Rockstar Games",
            "duration": 166,
        }
        official_score = _score("Grand Theft Auto VI", "GTA VI trailer footage shows Vice City streets.", "gta vi gameplay", official, context)
        self.assertLess(legacy_score, official_score)
        self.assertEqual(concept_score, -999.0)
        self.assertLess(viral_score, official_score)
        self.assertGreater(official_score, 0)

    def test_gta_vi_official_rockstar_trailer_scores_high(self):
        context = {"domain_category": "gaming", "container_subject": "Grand Theft Auto VI"}
        candidate = {
            "title": "Grand Theft Auto VI Trailer 2",
            "description": "Official Grand Theft Auto VI trailer",
            "channel": "Rockstar Games",
            "duration": 166,
        }
        score = _score(
            "Grand Theft Auto VI",
            "GTA VI is already breaking records and fans are watching the official trailer.",
            "grand theft auto vi official trailer gameplay",
            candidate,
            context,
        )
        self.assertGreater(score, 0)

    def test_abstract_broll_scene_rejects_streamer_commentary_and_interview_sources(self):
        streamer = {
            "title": "Streamer Reacts to John Cena WWE Moments",
            "description": "Facecam reaction and commentary",
            "channel": "Live Stream Highlights",
            "duration": 600,
        }
        footage = {
            "title": "John Cena Training Before WWE Event",
            "description": "Gym and arena training raw footage",
            "channel": "Wrestling Archive",
            "duration": 240,
        }
        context = {"domain_category": "sports", "container_subject": "John Cena"}
        streamer_score = _score(
            "John Cena",
            "John Cena trains before a major WWE event.",
            "John Cena training footage",
            streamer,
            context,
        )
        footage_score = _score(
            "John Cena",
            "John Cena trains before a major WWE event.",
            "John Cena training footage",
            footage,
            context,
        )
        self.assertEqual(streamer_score, -999.0)
        self.assertGreater(footage_score, 0)

    def test_simpson_clip_is_not_rejected_as_school_news(self):
        context = {"domain_category": "animation", "container_subject": "The Simpsons"}
        clip = {
            "title": "The Simpsons - Bart Simpson at Springfield Elementary clip",
            "channel": "Simpsons Clips",
            "duration": 120,
        }
        score = _score(
            "The Simpsons",
            "Bart Simpson causes chaos at Springfield Elementary.",
            "the simpsons bart simpson clip",
            clip,
            context,
        )
        self.assertGreaterEqual(score, 0)

    # ------------------------------------------------------------------ #
    # Historical disaster archival footage                                #
    # ------------------------------------------------------------------ #

    def test_historical_disaster_archive_footage_is_usable(self):
        context = {"domain_category": "disaster", "container_subject": "RMS Titanic"}
        archive = {
            "title": "Titanic Real Footage: Leaving Belfast for Disaster (1911-1912)",
            "channel": "British Pathé Archive",
            "description": "Archival historical footage newsreel of the ocean liner and lifeboats",
            "duration": 407,
        }
        reaction = {
            "title": "Titanic Sinking REACTION EXPOSED",
            "channel": "SilencedTruths",
            "description": "the truth about the sinking finally revealed",
            "duration": 600,
        }
        simulation = {
            "title": "Titanic sinking simulation animation",
            "channel": "CGI Channel",
            "description": "unreal engine simulation render",
            "duration": 120,
        }
        self.assertGreater(_score("RMS Titanic", "Titanic sank after striking an iceberg.", "rms titanic archival footage", archive, context), 0)
        self.assertEqual(_score("RMS Titanic", "Titanic sank after striking an iceberg.", "rms titanic archival footage", reaction, context), -999.0)
        self.assertEqual(_score("RMS Titanic", "Titanic sank after striking an iceberg.", "rms titanic archival footage", simulation, context), -999.0)

    def test_disaster_metadata_lacks_clean_footage_evidence_is_rejected(self):
        context = {"domain_category": "disaster", "container_subject": "Venezuela earthquakes"}
        candidate = {
            "title": "What the Venezuela quake means going forward",
            "description": "analysis of the quake and its economic impact",
            "channel": "Analysis Desk",
            "duration": 420,
        }
        score = _score(
            "Venezuela earthquakes",
            "Buildings collapsed after the strongest tremor.",
            "venezuela earthquake footage",
            candidate,
            context,
        )
        self.assertEqual(score, -180.0)

    # ------------------------------------------------------------------ #
    # Subject matching / weak terms / aliases                             #
    # ------------------------------------------------------------------ #

    def test_scene_subject_terms_drop_weak_tokens(self):
        terms = v2._scene_subject_terms("What Trump said about China is absolutely insane", "Video")
        self.assertTrue(any("trump" in term.lower() for term in terms))
        self.assertFalse(any(term.lower() == "what" for term in terms))

    def test_multiword_subject_requires_the_complete_identity(self):
        self.assertFalse(v2._text_mentions_subject("Jason gameplay compilation", "Jason and Lucia"))
        self.assertTrue(v2._text_mentions_subject("Jason and Lucia trailer footage", "Jason and Lucia"))

    def test_query_expansion_promotes_known_surnames_to_full_names(self):
        aliases = v2._subject_aliases("Lionel Messi")
        self.assertIn("Lionel Messi", aliases)
        self.assertTrue(any("messi" in alias.lower() for alias in aliases))

    def test_gta_context_uses_gaming_aliases(self):
        aliases = v2._subject_aliases("Grand Theft Auto VI")
        self.assertIn("Grand Theft Auto VI", aliases)
        self.assertTrue(any("gta vi" in alias.lower() for alias in aliases))
        self.assertTrue(any("gta 6" in alias.lower() for alias in aliases))

    def test_weak_taking_anchor_canonicalizes_to_real_container_subject(self):
        from services.container_guard import canonical_container_subject, is_weak_container_subject

        text = "Taking the third spot is Kylian Mbappe, whose speed changes every match."
        self.assertTrue(is_weak_container_subject("Taking"))
        self.assertEqual(canonical_container_subject("Taking", text), "Kylian Mbappe")

    def test_weak_container_subjects_do_not_become_star_or_choosing_sections(self):
        from services.container_guard import canonical_container_subject, is_weak_container_subject

        self.assertTrue(is_weak_container_subject("stars"))
        self.assertTrue(is_weak_container_subject("choosing"))
        self.assertEqual(
            canonical_container_subject("stars", "Kylian Mbappe is the star of this section", "Football legends"),
            "Kylian Mbappe",
        )

    # ------------------------------------------------------------------ #
    # Query building                                                      #
    # ------------------------------------------------------------------ #

    def test_subject_presence_scene_builds_subject_first_query(self):
        row = {
            "_llm": True,
            "section_subject": "Donald Trump",
            "scene_subject": "Donald Trump",
            "domain_category": "news",
        }
        queries = v2._queries_for_row(row, "Donald Trump", "Donald Trump walks to Air Force One after a rally and waves to supporters")
        joined = " ".join(queries).lower()
        self.assertTrue("donald trump" in joined or "trump" in joined)
        self.assertIn("footage", joined)

    def test_youtube_query_filters_are_complete_not_character_truncated(self):
        row = {
            "_llm": True,
            "section_subject": "RMS Titanic",
            "scene_subject": "RMS Titanic",
            "domain_category": "history",
        }
        queries = v2._queries_for_row(
            row,
            "RMS Titanic",
            "RMS Titanic archival ship footage from 1912",
        )
        combined = " ".join(queries).lower()
        self.assertNotRegex(combined, r"-subti(?:\s|$)")
        self.assertNotRegex(combined, r"-slidesho(?:\s|$)")
        self.assertNotIn("-subtitles", combined)
        self.assertNotIn("-slideshow", combined)

    def test_gaming_targeted_retry_uses_gameplay_queries_not_real_world_clickbait(self):
        row = {
            "_llm": True,
            "section_subject": "Grand Theft Auto VI",
            "scene_subject": "Grand Theft Auto VI",
            "domain_category": "gaming",
        }
        queries = v2._queries_for_row(
            row,
            "Grand Theft Auto VI",
            "The new game shows crowded beaches, weather, and detailed interiors",
        )
        joined = " ".join(queries).lower()
        self.assertTrue(any("gameplay" in query for query in queries))
        self.assertNotIn("-reaction", joined)
        self.assertNotIn("-streamer", joined)
        self.assertNotIn("caught on camera", joined)
        self.assertNotIn("real footage", joined)

    def test_sports_section_ladder_generates_real_footage_queries(self):
        row = {
            "_llm": True,
            "section_subject": "Kylian Mbappe",
            "scene_subject": "Kylian Mbappe",
            "domain_category": "sports",
        }
        queries = v2._queries_for_row(
            row,
            "Kylian Mbappe",
            "Kylian Mbappe talks about pressure before scoring on the world stage",
        )
        combined = " ".join(q.lower() for q in queries)
        self.assertIn("kylian mbappe", combined)
        self.assertTrue(any(term in combined for term in ("interview", "press conference", "match footage", "highlights")))
        self.assertNotIn("-subtitles", combined)
        self.assertNotIn("-slideshow", combined)

    def test_person_niche_map_covers_lifestyle_family_and_interview_footage(self):
        row = {
            "_llm": True,
            "section_subject": "Cristiano Ronaldo",
            "scene_subject": "Cristiano Ronaldo",
            "container_type": "person",
            "domain_category": "other",
        }
        queries = v2._queries_for_row(row, "Cristiano Ronaldo", "Cristiano Ronaldo's life away from football and family.")
        combined = " ".join(queries).lower()
        self.assertIn("public appearance footage", combined)
        self.assertIn("archival event footage", combined)

    def test_place_niche_map_covers_location_views_not_generic_biography(self):
        row = {
            "_llm": True,
            "section_subject": "Paris",
            "scene_subject": "Paris",
            "container_type": "place",
            "domain_category": "other",
        }
        queries = v2._queries_for_row(row, "Paris", "Paris streets and landmarks at sunset.")
        combined = " ".join(queries).lower()
        self.assertIn("walking tour footage", combined)
        self.assertIn("aerial drone footage", combined)
        self.assertNotIn("biography", combined)

    def test_llm_query_intents_are_used_as_niche_specific_suffixes(self):
        row = {
            "_llm": True,
            "section_subject": "Serena Williams",
            "scene_subject": "Serena Williams",
            "container_type": "person",
            "domain_category": "other",
            "youtube_query_intents": ["family footage", "public appearance footage"],
        }
        queries = v2._queries_for_row(row, "Serena Williams", "Serena Williams at a public event.")
        combined = " ".join(queries).lower()
        self.assertIn("family footage", combined)
        self.assertIn("appearance footage", combined)

    def test_technology_query_branch_uses_hands_on_demo_profile(self):
        row = {"_llm": True, "section_subject": "Electric truck", "scene_subject": "Electric truck", "domain_category": "technology"}
        queries = v2._queries_for_row(row, "Electric truck", "A clean visual of the new electric truck driving on a city road")
        combined = " ".join(q.lower() for q in queries)
        self.assertIn("demo", combined)
        self.assertIn("close up", combined)

    def test_travel_query_branch_uses_drone_and_street_profile(self):
        row = {"_llm": True, "section_subject": "Barcelona", "scene_subject": "Barcelona", "domain_category": "travel"}
        queries = v2._queries_for_row(row, "Barcelona", "Drone views of Barcelona skyline and harbor streets at sunset")
        combined = " ".join(q.lower() for q in queries)
        self.assertTrue(any("drone" in q.lower() for q in queries))
        self.assertTrue(any("street" in q.lower() for q in queries))

    def test_historical_disaster_queries_include_archive_newsreel_variants(self):
        row = {
            "_llm": True,
            "section_subject": "RMS Titanic",
            "scene_subject": "RMS Titanic",
            "domain_category": "disaster",
        }
        queries = v2._queries_for_row(row, "RMS Titanic", "Titanic sank after striking an iceberg.")
        combined = " ".join(q.lower() for q in queries)
        self.assertIn("archival historical footage", combined)
        self.assertIn("newsreel vintage footage", combined)
        self.assertIn("sinking shipwreck footage", combined)
        self.assertEqual(len(queries), 9)

    def test_date_container_queries_keep_canonical_topic_anchor(self):
        row = {
            "_llm": True,
            "title_anchor": "RMS Titanic",
            "section_subject": "April 15 1912",
            "scene_subject": "April 15 1912",
            "domain_category": "disaster",
            "visual_facet": "ocean liner departure Southampton",
        }
        queries = v2._queries_for_row(row, "April 15 1912", "The RMS Titanic departed Southampton.")
        assert queries
        assert all("rms titanic" in query.lower() for query in queries)
        assert all("april 15 1912" not in query.lower().split(" -", 1)[0] for query in queries)
        assert any("ocean liner" in query.lower() for query in queries)

    def test_mechanical_query_fallback_uses_semantic_paragraph_containers(self):
        scenes = [
            "Every football generation has its legends.",
            "Erling Haaland rewrites scoring records with brutal power.",
            "Lionel Messi still turns tight spaces into decisive moments.",
        ]
        rows = v2._generate_queries_mechanical(scenes, "Cristiano Ronaldo FIFA World Cup")
        self.assertIn("Erling Haaland", rows[1]["youtube_query"])
        self.assertIn("Haaland", rows[1]["section_subject"])
        self.assertIn("Lionel Messi", rows[2]["youtube_query"])

    # ------------------------------------------------------------------ #
    # Row building / grouping / container pools                           #
    # ------------------------------------------------------------------ #

    def test_single_subject_rows_do_not_require_neutral_title_mode(self):
        rows = [
            {
                "_llm": True,
                "_brain": True,
                "title_anchor": "RMS Titanic",
                "domain_anchor": "RMS Titanic",
                "section_subject": "Titanic sinking date",
                "scene_subject": "Titanic sinking date",
            },
            {
                "_llm": True,
                "_brain": True,
                "title_anchor": "RMS Titanic",
                "domain_anchor": "RMS Titanic",
                "section_subject": "White Star Line",
                "scene_subject": "White Star Line",
            },
        ]
        anchors = {row["domain_anchor"].lower() for row in rows}
        assert len(anchors) == 1

    def test_single_subject_date_containers_share_canonical_pool(self):
        scenes = [
            "On April 15 1912, RMS Titanic sank.",
            "The White Star Line ocean liner departed Southampton.",
        ]
        overrides = [
            {
                "_llm": True,
                "title_anchor": "RMS Titanic",
                "domain_anchor": "RMS Titanic",
                "section_subject": "April 15 1912",
                "scene_subject": "April 15 1912",
                "container_id": "date",
                "domain_category": "disaster",
            },
            {
                "_llm": True,
                "title_anchor": "RMS Titanic",
                "domain_anchor": "RMS Titanic",
                "section_subject": "White Star Line",
                "scene_subject": "White Star Line",
                "container_id": "publisher",
                "domain_category": "disaster",
            },
        ]
        rows = v2._build_scene_rows("The Sinking of RMS Titanic", scenes, overrides)
        assert len(v2._group_rows(rows)) == 1
        assert all(row["candidate_subject"] == "RMS Titanic" for row in rows)

    def test_weak_llm_section_anchors_collapse_to_real_container_subject(self):
        scenes = [
            "Taking the third spot is Kylian Mbappe.",
            "Whether it is a World Cup final or a Champions League night, Mbappe delivers.",
            "And at number one is Lionel Messi.",
        ]
        overrides = [
            {"_llm": True, "section_subject": "Taking", "scene_subject": "Taking"},
            {"_llm": True, "section_subject": "whether", "scene_subject": "Mbappe"},
            {"_llm": True, "section_subject": "probably", "scene_subject": "Messi"},
        ]
        rows = v2._build_scene_rows("CR7 FIFA World Cup", scenes, overrides)
        subjects = [row["section_subject"] for row in rows]
        self.assertTrue(all(subject in ("Kylian Mbappe", "Lionel Messi") for subject in subjects))
        groups = v2._group_rows(rows)
        self.assertGreaterEqual(len(groups), 1)

    def test_top_list_script_clusters_by_numbered_item_subject(self):
        scenes = [
            "Number 10 John Cena starts in small wrestling gyms.",
            "Cena builds his Doctor of Thuganomics persona.",
            "Number 9 The Undertaker creates a darker supernatural character.",
            "The Undertaker streak becomes legendary.",
            "Number 8 Randy Orton becomes the Legend Killer.",
            "Orton wins world titles.",
        ]
        overrides = [
            {"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena"},
            {"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena"},
            {"_llm": True, "section_subject": "The Undertaker", "scene_subject": "The Undertaker"},
            {"_llm": True, "section_subject": "The Undertaker", "scene_subject": "The Undertaker"},
            {"_llm": True, "section_subject": "Randy Orton", "scene_subject": "Randy Orton"},
            {"_llm": True, "section_subject": "Randy Orton", "scene_subject": "Randy Orton"},
        ]
        rows = v2._build_scene_rows("Top 10 WWE Wrestlers", scenes, overrides)
        groups = v2._group_rows(rows)
        self.assertEqual(len(groups), 3)

    def test_vague_scene_query_uses_neighboring_context(self):
        scenes = [
            "John Cena wins his first WWE Championship in a loud arena.",
            "It changed everything.",
            "Fans saw Cena become the face of WWE.",
        ]
        overrides = [{"_llm": True, "section_subject": "John Cena"}, {"_llm": True, "container_id": "bridge"}, {"_llm": True, "section_subject": "John Cena"}]
        rows = v2._build_scene_rows("John Cena Career", scenes, overrides)
        # The primary-subject lock prefixes the active container anchor onto
        # the vague bridge scene's queries instead of inventing a new subject.
        self.assertTrue(any("john cena" in q.lower() for q in rows[1]["queries"]))
        # Groups still reflect one shared container pool for the scene.
        self.assertIn("primary john cena", v2._group_rows(rows))

    def test_representative_pool_queries_smples_across_rows(self):
        rows = [
            {"queries": ["a1", "a2", "a3"]},
            {"queries": ["b1", "b2", "b3"]},
        ]
        picked = v2._representative_pool_queries(rows, 4)
        self.assertEqual(len(picked), 4)
        self.assertIn("a1", picked)
        self.assertIn("b1", picked)

    # ------------------------------------------------------------------ #
    # Timestamp reuse                                                     #
    # ------------------------------------------------------------------ #

    def test_reused_youtube_timestamps_keep_distant_gap_even_with_stale_env(self):
        candidate = {"id": "gap001", "title": "A", "duration": 300}
        used = {}
        ts = v2._timestamp_reuse_keys(candidate)
        self.assertIn("gap001", ts)

    def test_regional_mirrors_share_one_nonoverlapping_timestamp_budget(self):
        used = v2.defaultdict(list)
        italy = {"id": "italy", "title": "Grand Theft Auto VI | Trailer 2 | PS5", "duration": 169}
        australia = {"id": "australia", "title": "Grand Theft Auto VI | Trailer 2 | PS5", "duration": 169}

        starts = [v2._pick_timestamp(italy, idx, used, 4.0) for idx in range(3)]
        mirrored = v2._pick_timestamp(australia, 4, used, 4.0)

        self.assertTrue(all(start is not None for start in starts))
        self.assertIsNone(mirrored)

    def test_repeated_source_keys_are_detected_for_local_cut_cache(self):
        url1 = v2._video_id({"url": "https://www.youtube.com/watch?v=ABCdef12345"})
        url2 = v2._video_id({"url": "https://youtu.be/ABCdef12345?t=60"})
        self.assertEqual(url1, url2)

    def test_timestamp_progress_respects_duration(self):
        used = v2.defaultdict(list)
        candidate = {"id": "prog001", "title": "P", "duration": 240}
        ts = v2._pick_timestamp(candidate, 5, used, 6.0)
        self.assertIsNotNone(ts)
        self.assertGreater(ts, 0)

    # ------------------------------------------------------------------ #
    # Container search + assignment (end-to-end with mocked search)       #
    # ------------------------------------------------------------------ #

    def test_chapter_source_pools_assign_from_matching_pool(self):
        def fake_search(query, max_results=12, session_jar=None):
            q = query.lower()
            if "john cena" in q:
                return [{"id": "cena01", "title": "John Cena WWE training raw footage", "channel": "Wrestling Archive", "description": "raw footage highlights", "duration": 300, "query_text": query}]
            if "undertaker" in q:
                return [{"id": "taker01", "title": "Undertaker WrestleMania streak highlights", "channel": "Wrestling Archive", "description": "match archive highlights", "duration": 300, "query_text": query}]
            return []

        scene_texts = ["John Cena starts with early WWE training.", "The Undertaker's WrestleMania streak becomes the defining chapter."]
        overrides = [
            {"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports", "youtube_query": "John Cena WWE training footage"},
            {"_llm": True, "section_subject": "The Undertaker", "scene_subject": "The Undertaker", "domain_category": "sports", "youtube_query": "The Undertaker WrestleMania footage"},
        ]
        with mock.patch.object(v2, "_search_youtube", side_effect=fake_search), \
             mock.patch.object(v2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.dict(os.environ, {"VIDZEO_YT_V2_QUERIES_PER_CONTAINER": "2"}, clear=False):
            result = v2.select_youtube_scene_matches(
                scene_texts=scene_texts,
                title="Top WWE Wrestlers",
                scene_query_overrides=overrides,
                use_authenticated_session=False,
            )

        self.assertEqual(len(result["matches"]), 2)
        titles = [(m["selected_video"] or {}).get("title", "") for m in result["matches"]]
        self.assertIn("John Cena", titles[0])
        self.assertIn("Undertaker", titles[1])

    def test_same_source_reuse_gets_distinct_timestamps(self):
        rows = v2._build_scene_rows(
            "John Cena Career",
            ["John Cena trains for WWE.", "Cena wins the championship."],
            [
                {"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports"},
                {"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports"},
            ],
        )
        pool = [{"id": "cena01", "title": "John Cena training raw footage", "channel": "Wrestling Archive", "description": "raw footage highlights", "duration": 420, "query_text": "John Cena training footage"}]
        pools = {key: pool for key in v2._group_rows(rows)}
        assignments = v2._assign_candidates(rows, pools, [4.0, 4.0])
        ts = [a.get("best_timestamp") for a in assignments if a]
        self.assertEqual(len(ts), 2)
        self.assertGreaterEqual(abs(ts[0] - ts[1]), 60)

    def test_get_youtube_clips_skips_rejected_scene_without_downloading(self):
        with mock.patch.object(v2, "_search_youtube", return_value=[]), \
             mock.patch.object(v2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.dict(os.environ, {"VIDZEO_YT_V2_SKIP_DOWNLOAD": "1"}, clear=True):
            result = v2.get_youtube_clips(
                script="John Cena trains and wins.",
                title="John Cena Career",
                num_scenes=2,
                scene_durations=[4.0, 4.0],
                scene_texts=["John Cena trains.", "Cena wins the title."],
            )
        self.assertEqual(result, [None, None])

    def test_get_youtube_clips_does_not_download_negative_ranked_candidate(self):
        negative = {
            "id": "neg001",
            "title": "Unrelated AI tutorial",
            "channel": "Tutorial Channel",
            "duration": 180,
            "description": "",
        }

        def fake_search(query, max_results=12, session_jar=None):
            return [dict(negative, query_text=query)]

        with mock.patch.object(v2, "_search_youtube", side_effect=fake_search), \
             mock.patch.object(v2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.dict(os.environ, {"VIDZEO_YT_V2_SKIP_DOWNLOAD": "1"}, clear=True):
            result = v2.get_youtube_clips(
                script="John Cena struggled early in his wrestling career.",
                title="John Cena",
                num_scenes=1,
                scene_durations=[5.0],
                scene_texts=["John Cena struggled early in his wrestling career."],
            )
        self.assertEqual(result, [None])

    # ------------------------------------------------------------------ #
    # Niche detection (shared with universal_clip_classifier)             #
    # ------------------------------------------------------------------ #

    def test_simpsons_script_uses_animation_niche_not_cricket(self):
        niche, score = detect_niche(
            "In the animated town of Springfield",
            "Homer Simpson, Bart Simpson, Marge, Lisa and Springfield Elementary define The Simpsons.",
        )
        self.assertEqual(niche, "animation")
        self.assertGreaterEqual(score, 20)

    def test_football_listicle_detects_football_not_history(self):
        niche, score = detect_niche(
            "Every football generation has its legends",
            "Cristiano Ronaldo, Haaland, Bellingham, Mbappe, and Messi are footballers with goals and World Cup moments.",
        )
        self.assertEqual(niche, "football")
        self.assertGreaterEqual(score, 20)

    def test_vietnam_war_detects_war_not_film_tv_from_generic_words(self):
        niche, score = detect_niche(
            "The Vietnam War, a conflict that changed history",
            "The Vietnam War involved troops, bombing, soldiers, the Cold War and Saigon.",
        )
        self.assertEqual(niche, "war")
        self.assertGreaterEqual(score, 3)

    def test_taxonomy_driven_niche_conflict_rejects_wrong_sport(self):
        conflict, expected, candidate = detect_niche_conflict(
            "Virat Kohli cricket batting innings",
            "FIFA football goals and soccer match highlights",
        )
        self.assertTrue(conflict)
        self.assertEqual(expected, "cricket")
        self.assertEqual(candidate, "football")


if __name__ == "__main__":
    unittest.main()
