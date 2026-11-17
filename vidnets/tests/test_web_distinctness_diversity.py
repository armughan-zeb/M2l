"""Web-lane distinctness + source-diversity regressions.

Fixtures are the GTA VI 1-min proof job a57cb5c7's own records
(Nov 16, 2026):
* ``web_image_audit_a57cb5c7_…_60d802f2.json`` — scene 0 kept 1
  candidate out of 8 downloads (pool=1, alternates=[]); scene 6
  listed the same key art as winner AND alternate; 5/8 scenes hit
  ``retrieval_deadline`` with 0 downloads.
* ``provider_web_candidates.json`` — only 3 distinct web images for
  5 web scenes; all 5 from Pinterest.
* ``web_image_audit_a57cb5c7_…_ccdf1e22.json`` — shared-batch retry
  with 83 rows from diverse publishers (Baltimore Sun, Rectify
  Gaming, Flickr, …), of which only 2 pass scene 0's old relevance
  floor.
* Real file hashes (verified against the surviving downloads):
  key art md5 f8afedec2bf23c74dfc240d075b8318e /
  sha256 63a2158ab7c26285c542f8c70ab259b4ea9f6a4e04dcd845cf2b7bc289b9a73e;
  Vice City shot md5 7d05b5fd0332… /
  sha256 ebd04eb9c5cb48742e225cf90a6c8c81dbac12b02f73ff1c6c25f722ac806f19.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

# --- Real records from proof job a57cb5c7 ---------------------------------
KEY_ART_MD5 = "f8afedec2bf23c74dfc240d075b8318e"
KEY_ART_SHA256 = (
    "63a2158ab7c26285c542f8c70ab259b4ea9f6a4e04dcd845cf2b7bc289b9a73e"
)
KEY_ART_URL = (
    "https://i.pinimg.com/736x/51/73/8d/51738d6d4a176c22e630831246220953.jpg"
)
KEY_ART_PIN = "https://br.pinterest.com/pin/959196420643250674/"
VICE_CITY_MD5 = "7d05b5fd0332"
VICE_CITY_SHA256 = (
    "ebd04eb9c5cb48742e225cf90a6c8c81dbac12b02f73ff1c6c25f722ac806f19"
)
VICE_CITY_URL = (
    "https://i.pinimg.com/736x/4f/61/82/4f6182bed8a2288fa0d6d1e818f754de.jpg"
)

# Verbatim scene texts from provider_web_candidates.json.
SCENE_0_TEXT = (
    "Grand Theft Auto Six is the most anticipated video game of the decade, "
    "and Rockstar Games has kept fans waiting for more than ten years since "
    "Grand Theft Auto Five, a game that has sold more than two hundred "
    "million copies."
)
SCENE_3_TEXT = (
    "At the center of the story are Lucia Caminos and Jason Duval, two "
    "partners in crime whose story begins with Lucia walking out of prison "
    "and reuniting with Jason."
)


def key_art_row(**overrides):
    row = {
        "title": "Grand Theft Auto VI - Rockstar Games (Poster 1-DevilCat) em 2025",
        "url": KEY_ART_URL,
        "source_url": KEY_ART_PIN,
        "page_url": KEY_ART_PIN,
        "provider": "web",
        "domain": "br.pinterest.com",
        "asset_fingerprint": KEY_ART_SHA256,
        "md5": KEY_ART_MD5,
    }
    row.update(overrides)
    return row


def vice_city_row(**overrides):
    row = {
        "title": "Vice City Miami South Beach neon night photo",
        "url": VICE_CITY_URL,
        "source_url": VICE_CITY_URL,
        "page_url": VICE_CITY_URL,
        "provider": "web",
        "domain": "i.pinimg.com",
        "asset_fingerprint": VICE_CITY_SHA256,
        "md5": VICE_CITY_MD5,
    }
    row.update(overrides)
    return row


def baltimore_sun_row():
    # Real row from the 83-row shared batch (audit ccdf1e22).
    return {
        "title": "'Grand Theft Auto VI' is almost here. What to know about the characters, music and billions at ...",
        "url": "https://www.baltimoresun.com/wp-content/uploads/2026/11/Grand_Theft_Auto_639_8-1.jpg?w=810",
        "source_url": "https://www.baltimoresun.com/2026/11/13/grand-theft-auto-vi-is-almost-here-what-to-know-about-the-characters-music-and-billions-at-stake/",
        "provider": "ddg_hybrid",
        "source": "ddg_hybrid",
        "domain": "baltimoresun.com",
    }


def rectify_gaming_row():
    # Real row from the 83-row shared batch (audit ccdf1e22).
    return {
        "title": "Rockstar Games To Host 'Grand Theft Auto VI: The Festival' Music Event On November 21st ...",
        "url": "https://www.rectifygaming.com/wp-content/uploads/2026/11/grand-theft-auto-vi-the-festival.jpg",
        "source_url": "https://www.rectifygaming.com/rockstar-games-to-host-grand-theft-auto-vi-the-festival-music-event-on-november-21st/",
        "provider": "ddg_hybrid",
        "source": "ddg_hybrid",
        "domain": "rectifygaming.com",
    }


# --- Distinctness: content hash, not URL/path ------------------------------

def test_same_bytes_different_pins_are_one_image():
    """The key art under another pin/CDN size is still the same image."""
    from services.media_distinctness import content_identity

    first = key_art_row()
    # Same bytes, different pin page and a resized CDN variant URL.
    second = key_art_row(
        url="https://i.pinimg.com/originals/51/73/8d/51738d6d4a176c22e630831246220953.jpg",
        source_url="https://www.pinterest.com/pin/111222333444555666/",
        page_url="https://www.pinterest.com/pin/111222333444555666/",
    )
    assert content_identity(first) == content_identity(second)
    assert content_identity(first) != content_identity(vice_city_row())


def test_same_file_bytes_two_paths_are_one_image(tmp_path):
    """Copied scene files (same bytes, different folders) are one image."""
    from services.media_distinctness import content_identity

    payload = b"fake-jpeg-bytes-for-distinctness-test"
    first = tmp_path / "scene_1" / "scene_0001.jpg"
    second = tmp_path / "scene_4" / "scene_0001.jpg"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(payload)
    second.write_bytes(payload)
    assert content_identity({"path": str(first)}) == content_identity({"path": str(second)})
    other = tmp_path / "scene_2" / "scene_0002.jpg"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"different-bytes")
    assert content_identity({"path": str(first)}) != content_identity({"path": str(other)})


def test_repeated_key_art_fails_distinctness_while_unused_candidate_exists():
    """Proof job scenes 0+4 shared md5 f8afedec2…; with an unused
    verified candidate available, the repeat must lose."""
    from services.media_distinctness import (
        UsageTracker,
        content_identity,
        partition_unused_first,
    )

    tracker = UsageTracker()
    key_art = key_art_row()
    unused_alt = vice_city_row()
    # Scene 0 claims the key art.
    tracker.claim(content_identity(key_art))
    assert tracker.is_used(content_identity(key_art))
    # Scene 4's choice: the used key art must sort behind the unused alt.
    unused, already_used = partition_unused_first(
        [key_art, unused_alt], tracker
    )
    assert unused == [unused_alt]
    assert already_used == [key_art]


def test_reuse_allowed_only_when_no_unused_candidate_exists():
    """Reuse is the last resort, not the default: with nothing unused,
    the used image is still offered (cap permitting)."""
    from services.media_distinctness import UsageTracker, partition_unused_first

    tracker = UsageTracker()
    from services.media_distinctness import content_identity

    tracker.claim(content_identity(key_art_row()))
    unused, already_used = partition_unused_first([key_art_row()], tracker)
    assert unused == []
    assert len(already_used) == 1


def test_dedupe_by_content_collapses_scene_6_duplicate():
    """Audit scene 6 listed the key art twice (pool=1, 2 entries, one
    fingerprint). The pool must collapse to one distinct image."""
    from services.media_distinctness import dedupe_by_content

    rows = [
        key_art_row(path="/tmp/run/web_v2/scene_0007.jpg"),
        key_art_row(path="/tmp/run/web_v2_alt_6_1/scene_0001.jpg"),
        vice_city_row(path="/tmp/run/web_v2_alt_1_1/scene_0001.jpg"),
    ]
    kept = dedupe_by_content(rows)
    assert len(kept) == 2
    assert kept[0]["path"] == "/tmp/run/web_v2/scene_0007.jpg"
    assert kept[1]["path"] == "/tmp/run/web_v2_alt_1_1/scene_0001.jpg"


# --- Source diversity ------------------------------------------------------

def test_pinterest_variants_are_one_source():
    """i.pinimg.com image URLs behind br.pinterest.com pin pages are
    one source, not diverse hosts."""
    from services.media_distinctness import needs_more_sources, source_host

    rows = [
        key_art_row(),
        vice_city_row(),
        vice_city_row(url="https://i.pinimg.com/originals/a8/64/66/a864662522185fdb6ad7b56bac6cf0e2.jpg?nii=t"),
        key_art_row(url="https://i.pinimg.com/originals/ec/24/09/ec2409c6c4ae0d2242691dbe99512ff3.jpg?nii=t"),
    ]
    assert {source_host(row) for row in rows} == {"pinterest.com"}
    assert needs_more_sources(rows) is True


def test_mixed_publishers_do_not_need_more_sources():
    from services.media_distinctness import needs_more_sources

    rows = [key_art_row(), vice_city_row(), baltimore_sun_row(), rectify_gaming_row()]
    # 2 Pinterest / 4 rows = 50% share, 3 hosts -> diverse enough.
    assert needs_more_sources(rows) is False


def test_scraper_reports_single_source_batch():
    """The universal batch for the proof job's queries was all
    Pinterest; the response must say so instead of looking healthy."""
    from services.web_image.universal_scraper import (
        UniversalImageResult,
        universal_needs_more_sources,
        universal_source_diversity,
        universal_source_host_share,
    )

    pinterest_rows = [
        UniversalImageResult(
            title="GTA VI key art",
            image_url=f"https://i.pinimg.com/736x/51/73/8d/51738d6d4a176c22e63083124622095{i}.jpg",
            source_url="https://br.pinterest.com/pin/959196420643250674/",
            source_engine="bing",
        )
        for i in range(5)
    ]
    assert universal_source_host_share(pinterest_rows) == {"pinterest.com": 5}
    assert universal_needs_more_sources(pinterest_rows) is True
    summary = universal_source_diversity(pinterest_rows)
    assert summary["dominant_host"] == "pinterest.com"
    assert summary["dominant_share"] == 1.0
    assert summary["needs_more_sources"] is True

    mixed = pinterest_rows[:2] + [
        UniversalImageResult(
            title="GTA VI Baltimore Sun",
            image_url="https://www.baltimoresun.com/wp-content/uploads/2026/11/Grand_Theft_Auto_639_8-1.jpg?w=810",
            source_url="https://www.baltimoresun.com/2026/11/13/grand-theft-auto-vi-is-almost-here/",
            source_engine="bing",
        ),
        UniversalImageResult(
            title="GTA VI Rectify Gaming",
            image_url="https://www.rectifygaming.com/wp-content/uploads/2026/11/grand-theft-auto-vi-the-festival.jpg",
            source_url="https://www.rectifygaming.com/rockstar-games-to-host-grand-theft-auto-vi-the-festival-music-event-on-november-21st/",
            source_engine="bing",
        ),
    ]
    assert universal_needs_more_sources(mixed) is False


def test_diverse_download_keeps_non_pinterest_reserve():
    """A Pinterest-dominated eligible set (8 pins scored ~3.3) must
    still download a non-Pinterest row that passed the metadata floor,
    or the scene can never pick a distinct non-Pinterest image."""
    from services.web_image.resolver import _diverse_download_candidates

    pins = [
        (3.3 - i * 0.05, key_art_row(url=f"https://i.pinimg.com/736x/51/73/8d/pin{i}.jpg"))
        for i in range(8)
    ]
    others = [
        (0.5, baltimore_sun_row()),
        (0.4, rectify_gaming_row()),
    ]
    selected = _diverse_download_candidates(pins + others, 8)
    assert len(selected) == 8
    hosts = {
        ("pinterest.com" if "pinimg" in item.get("url", "") or "pinterest" in item.get("source_url", "") else "other")
        for _score, item in selected
    }
    assert "other" in hosts
    # The best pin still wins its slot; the reserve replaces the worst.
    assert selected[0][1]["url"] == pins[0][1]["url"]


# --- Scene-subject awareness ------------------------------------------------

def test_gta_v_image_correct_for_gta_v_history_scene():
    """Proof-job scene 0 narrates GTA V (sales, decade-long wait): a
    GTA V image must pass the relevance floor there."""
    from services.web_image.resolver import (
        _passes_web_relevance_floor,
        _topic_candidate_mismatch,
    )

    gta_v_row = {
        "title": "GTA V gameplay Los Santos city",
        "url": "https://i.pinimg.com/736x/aa/bb/cc/gta-v-los-santos.jpg",
        "source_url": "https://www.pinterest.com/pin/123/",
        "query": "Rockstar Games Grand Theft Auto Six photograph",
        "query_used": "Rockstar Games Grand Theft Auto Six photograph",
    }
    assert _topic_candidate_mismatch(
        title=gta_v_row["title"],
        url=f"{gta_v_row['url']} {gta_v_row['source_url']}",
        query=gta_v_row["query"],
        scene_text=SCENE_0_TEXT,
        main_subject="Rockstar Games",
    ) is False
    assert _passes_web_relevance_floor(
        gta_v_row, 3.0, "Rockstar Games", SCENE_0_TEXT
    ) is True


def test_gta_v_image_wrong_for_gta_vi_content_scene():
    """The same GTA V image is wrong for scene 3 (Lucia/Jason, GTA VI
    content) and must still fail there."""
    from services.web_image.resolver import (
        _passes_web_relevance_floor,
        _topic_candidate_mismatch,
    )

    gta_v_row = {
        "title": "GTA V gameplay Los Santos city",
        "url": "https://i.pinimg.com/736x/aa/bb/cc/gta-v-los-santos.jpg",
        "source_url": "https://www.pinterest.com/pin/123/",
        "query": "Lucia Caminos Jason Duval Grand Theft Auto VI",
        "query_used": "Lucia Caminos Jason Duval Grand Theft Auto VI",
    }
    assert _topic_candidate_mismatch(
        title=gta_v_row["title"],
        url=f"{gta_v_row['url']} {gta_v_row['source_url']}",
        query=gta_v_row["query"],
        scene_text=SCENE_3_TEXT,
        main_subject="Lucia Caminos and Jason Duval",
    ) is True
    assert _passes_web_relevance_floor(
        gta_v_row, 3.0, "Lucia Caminos and Jason Duval", SCENE_3_TEXT
    ) is False


def test_2002_vice_city_stays_out_everywhere():
    """The 2002 Vice City game is out for every scene, including the
    GTA V-history scene — no anchor ever admits it."""
    from services.media_distinctness import (
        is_legacy_installment_image,
        narrated_installment_anchor,
        alternate_anchor_allows,
    )
    from services.web_image.resolver import _passes_web_relevance_floor

    legacy = {
        "title": "Grand Theft Auto Vice City 2002 gameplay cutscene",
        "url": "https://i.pinimg.com/736x/4f/61/82/4f6182bed8a2288fa0d6d1e818f754de.jpg",
        "source_url": "https://www.pinterest.com/pin/456/",
        "query": "Vice City neon streets",
        "query_used": "Vice City neon streets",
    }
    metadata = f"{legacy['title']} {legacy['url']}"
    assert is_legacy_installment_image(metadata) is True
    anchor = narrated_installment_anchor(SCENE_0_TEXT, "Rockstar Games")
    assert anchor is not None  # the GTA V anchor exists for scene 0…
    assert alternate_anchor_allows(anchor, metadata) is False  # …but never admits 2002
    assert _passes_web_relevance_floor(legacy, 3.0, "Rockstar Games", SCENE_0_TEXT) is False
    assert _passes_web_relevance_floor(legacy, 3.0, "Vice City neon streets", SCENE_3_TEXT) is False


def test_narrated_anchor_only_fires_for_another_installment():
    from services.media_distinctness import narrated_installment_anchor

    anchor = narrated_installment_anchor(SCENE_0_TEXT, "Rockstar Games")
    assert anchor is not None
    assert anchor["version"] == "5"
    # Scene 3 narrates no earlier installment: no anchor, GTA V stays wrong.
    assert narrated_installment_anchor(SCENE_3_TEXT, "Lucia Caminos and Jason Duval") is None
    # A scene narrating the same installment as its subject needs none.
    assert narrated_installment_anchor(SCENE_3_TEXT, "Grand Theft Auto VI") is None


# --- Deferred-pool retention -------------------------------------------------

def test_promote_deferred_keeps_vision_pending_alternates():
    """Floor-failed downloads are promoted (best first, capped, legacy
    excluded, content-deduped) when the verified pool is empty."""
    from services.media_distinctness import promote_deferred

    good = vice_city_row()
    legacy = {
        "title": "Grand Theft Auto Vice City 2002 gameplay",
        "url": "https://i.pinimg.com/736x/00/11/22/legacy-2002.jpg",
        "source_url": "https://www.pinterest.com/pin/789/",
    }
    duplicate = vice_city_row(path="/tmp/other/scene_0001.jpg")
    promoted = promote_deferred(
        [(-1.5, good), (-0.5, legacy), (-2.0, duplicate)], limit=3
    )
    assert [row["url"] for _score, row in promoted] == [VICE_CITY_URL]
    assert promoted[0][1]["semantic_visual_pending"] is True
    assert promoted[0][1]["relevance_deferred"] is True


# --- Coordinator: reuse is the last resort ----------------------------------

def test_coordinator_rescue_prefers_unused_verified_alternate(tmp_path):
    """Proof-job scenes 3/4/7 were handed neighbours' stills by the
    pre-stock rescue. With an unused verified alternate available, the
    rescue must take it instead of the already-used key art."""
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    key_art_path = tmp_path / "snapshot-key-art.jpg"
    alt_path = tmp_path / "snapshot-vice-city.jpg"
    key_art_path.write_bytes(b"key-art-bytes")
    alt_path.write_bytes(b"vice-city-bytes")
    key_art = {
        "path": str(key_art_path),
        "source": "web",
        "provider": "web",
        "source_url": KEY_ART_PIN,
        "asset_fingerprint": KEY_ART_SHA256,
        "score": 100,
        "final_score": 100,
        "container_id": "same-topic",
    }
    unused_alt = {
        "path": str(alt_path),
        "source": "web",
        "provider": "web",
        "source_url": VICE_CITY_URL,
        "asset_fingerprint": VICE_CITY_SHA256,
        "score": 90,
        "final_score": 90,
        "container_id": "same-topic",
    }
    scenes = [
        {
            "scene_id": f"scene-{index}",
            "scene_index": index,
            "container_id": "same-topic",
            "text": "Grand Theft Auto Six trailer",
            "positive_subjects": ["Grand Theft Auto VI"],
            "forbidden_subjects": [],
            "source_policy": {"visual_verification_required": False},
        }
        for index in range(2)
    ]

    def web_lane(scene, _cancel_event):
        if scene["scene_index"] == 0:
            return [dict(key_art)]
        return []

    def web_fallback_lane(scene, _cancel_event):
        if scene["scene_index"] == 1:
            # Both the already-used key art and an unused verified alt.
            return [dict(key_art), dict(unused_alt)]
        return []

    result = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=lambda _scene, _cancel_event: [],
        web_lane=web_lane,
        web_fallback_lane=web_fallback_lane,
        visual_verifier=lambda _scene, _candidate: {"accepted": True, "score": 90},
        neutral_factory=lambda _scene: None,
        deadline_seconds=2,
    )
    assert result[0].selected_path == str(key_art_path)
    assert result[1].selected_path == str(alt_path)


def test_pool_recovery_identity_matches_primary_identity():
    """The pool-recovery cap compares identities: a discovered row and
    a resolution for the same bytes must yield the SAME identity, or
    the cap silently passes a third use. (This is the hole the
    coordinator fix closes: the loop used to derive the identity from
    a fresh fingerprint-less resolution and fell back to the path.)"""
    from services.media_resolution_coordinator import (
        _resolution_asset_identity,
        _web_asset_identity,
    )
    from services.media_resolution_coordinator import SceneResolution

    discovered = key_art_row()
    resolution = SceneResolution(
        scene_index=0,
        provider="web",
        selected_path="/tmp/run/web_v2/scene_0001.jpg",
        asset_fingerprint=KEY_ART_SHA256,
    )
    assert _web_asset_identity(discovered) == _resolution_asset_identity(resolution)
    assert _web_asset_identity(discovered) == f"web:fingerprint:{KEY_ART_SHA256}"


def test_thin_pool_tops_up_with_deferred_alternates(monkeypatch, tmp_path):
    """Proof job scene 0 shape: pool=1, alternates=[], 7 downloads erased.

    One strong row verifies; the other seven pass the pre-download
    score (on-topic GTA metadata) but fail the relevance floor's
    version block — GTA V imagery for a scene about GTA VI content
    (the trailer, Leonida). The resolver must keep the best deferred
    rows as alternates only: the winner stays the verified row, the
    pool grows to 3, and the two alternates still carry
    semantic_visual_pending for vision.
    """
    import threading
    import time

    from PIL import Image

    from services.web_image import resolver

    scene_text = "The trailer for Grand Theft Auto VI shows Leonida"
    rows = [
        {
            "url": "https://images.example/gta-vi-trailer.jpg",
            "image_url": "https://images.example/gta-vi-trailer.jpg",
            "title": "Grand Theft Auto VI trailer Leonida",
            "source": "wikimedia",
            "provider": "wikimedia",
            "source_url": "https://commons.wikimedia.org/wiki/File:GTA_VI_trailer",
            "query": "Grand Theft Auto VI trailer",
            "tags": "Grand Theft Auto VI trailer Leonida",
            "description": "Grand Theft Auto VI trailer Leonida",
        }
    ]
    # Word numbers, not digits: a bare "6" in the title would match
    # the floor's version alias {"vi", "6"} and wrongly pass the row.
    for word in ("one", "two", "three", "four", "five", "six", "seven"):
        rows.append(
            {
                "url": f"https://images.example/gta-v-{word}.jpg",
                "image_url": f"https://images.example/gta-v-{word}.jpg",
                "title": f"Grand Theft Auto V gameplay {word}",
                "source": "wikimedia",
                "provider": "wikimedia",
                "source_url": f"https://commons.wikimedia.org/wiki/File:GTA_V_{word}",
                "query": "Grand Theft Auto VI trailer",
                "tags": f"Grand Theft Auto V gameplay {word}",
                "description": f"Grand Theft Auto V gameplay {word}",
            }
        )

    monkeypatch.setattr(resolver, "_enabled_fast_sources", lambda: [("wikimedia", "api")])
    monkeypatch.setattr(
        resolver,
        "_discover_source_query",
        lambda _source, _kind, _query, _gdl=None, max_timeout=None: list(rows),
    )

    def fake_download(urls, output_dir, max_concurrent=50, timeout_seconds=15.0, **kwargs):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        result = []
        for index, url in enumerate(urls):
            path = output_dir / f"candidate-{index}.jpg"
            Image.effect_noise((800, 450), 64 + index).convert("RGB").save(
                path, format="JPEG", quality=92
            )
            result.append((url, path, ""))
        return result

    monkeypatch.setattr(resolver, "download_bulk", fake_download)

    result = resolver._resolve_scene(
        0,
        scene_text,
        ["Grand Theft Auto VI trailer"],
        None,
        tmp_path,
        "Grand Theft Auto VI",
        {},
        {},
        set(),
        threading.Lock(),
        threading.Lock(),
        threading.Lock(),
        fast_mode=True,
        max_candidate_downloads=20,
        deadline_at=time.monotonic() + 30,
    )

    assert result and result.get("path")
    assert result["downloaded_candidate_count"] == 8
    # 1 verified winner + 2 deferred alternates (was 1 + 0 in the proof job).
    assert result["candidate_pool_size"] == 3
    shortlist = result["_vision_shortlist"]
    assert len(shortlist) == 3
    assert shortlist[0]["url"] == "https://images.example/gta-vi-trailer.jpg"
    alternates = shortlist[1:]
    assert all(cand.get("semantic_visual_pending") for cand in alternates)
    assert {cand["url"] for cand in alternates} <= {
        f"https://images.example/gta-v-{w}.jpg"
        for w in ("one", "two", "three", "four", "five", "six", "seven")
    }
