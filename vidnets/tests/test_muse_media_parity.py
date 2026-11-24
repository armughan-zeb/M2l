import os
from types import SimpleNamespace

import pytest

from services import youtube_pipeline_v2 as youtube, youtube_session
from services.editorial_director.production_contract import _remove_incidental_maps
from services.mixed_media_planner import build_adaptive_mixed_media_plan
from services.pipeline_v2_media_adapter import _source_lane_policy
from services.web_image import resolver


def test_keyless_plan_reallocates_stock_slots_to_web():
    plan = build_adaptive_mixed_media_plan(
        ["A scene about transport."] * 10,
        weights={"Web Images": 30, "YouTube Clips": 40, "Stock Media": 30},
        allow_stock=False,
    )
    assert plan.target_counts["Web Images"] == 6
    assert plan.target_counts["YouTube Clips"] == 4
    assert plan.grouped_indices["Stock Media"] == []
    assert all(not decision.stock_allowed for decision in plan.decisions)
    policy = _source_lane_policy({"image_source": "Mixed Media"})
    assert policy["youtube_enabled"] and policy["web_enabled"]
    assert not policy["stock_enabled"] and not policy["stock_last_resort_enabled"]


def test_gta_stills_use_publisher_and_narrated_edition():
    assert resolver._publisher_screenshot_query("Grand Theft Auto VI", "Lucia visits Vice City.") == (
        "Grand Theft Auto VI lucia vice city official screenshot site:rockstargames.com"
    )
    assert resolver._publisher_screenshot_query("Grand Theft Auto VI", "GTA V sold millions.").startswith(
        "Grand Theft Auto V official screenshot"
    )
    assert not resolver._publisher_screenshot_query("Miami", "The real city.")


@pytest.mark.parametrize("complete, initial_wait, late_wait", [(False, 8, 0), (True, 30, 120)])
def test_web_harvests_available_results_without_stalling_fast_pass(monkeypatch, complete, initial_wait, late_wait):
    monkeypatch.delenv("VIDZEO_UNIVERSAL_SCRAPER_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_UNIVERSAL_LATE_WAIT_SECONDS", raising=False)
    first = SimpleNamespace(image_url="https://images.example/first.jpg")
    late = SimpleNamespace(image_url="https://images.example/late.jpg")
    timed_out = SimpleNamespace(engine="slow", count=0, duration_ms=1000, success=False, error="timeout")

    def search(query, **options):
        assert options["timeout_seconds"] == initial_wait
        assert options["wait_until_complete"] == complete
        return resolver.UniversalSearchResponse(query, 1, [first], [timed_out])

    def collect(_query, *, wait_seconds):
        assert wait_seconds == late_wait
        return [first, late]

    monkeypatch.setattr(resolver, "_UNIVERSAL_SCRAPER", SimpleNamespace(search=search, collect_late_results=collect))
    monkeypatch.setattr(resolver, "universal_result_to_candidate", lambda item, **_: {"url": item.image_url})
    monkeypatch.setattr(resolver, "candidate_is_safe", lambda _candidate: (True, "fixture"))
    monkeypatch.setattr(resolver, "_report_provider_success", lambda _source: None)
    rows = resolver._discover_universal_urls("GTA VI", wait_until_complete=complete)
    assert [row["url"] for row in rows] == [first.image_url, late.image_url]


def _map_scene(narration, places):
    return {
        "narration": narration,
        "template_id": "map-route",
        "primary_visual": {"type": "AnimatedMap", "props": {}},
        "map_shot": {"enabled": True, "location_names": places},
        "media": {"type": "video", "src": "verified.mp4"},
        "motion_cues": [{"element": "map"}, {"element": "data"}],
        "text": {"frame_mode": "map"},
    }


def test_incidental_and_wrong_maps_restore_media_and_keep_data():
    incidental = _map_scene("The GTA trailer shows Lucia driving near Miami.", ["Miami"])
    wrong = _map_scene("See Paris on the map.", ["London"])
    explicit = _map_scene("See Paris on the map.", ["Paris"])
    route = _map_scene("The train travelled from London to Paris.", ["London", "Paris"])
    _remove_incidental_maps([incidental, wrong, explicit, route])
    for scene in (incidental, wrong):
        assert scene["primary_visual"]["type"] == "SmartVideo"
        assert not scene["map_shot"]["enabled"]
        assert scene["media"]["src"] == "verified.mp4"
        assert scene["motion_cues"] == [{"element": "data"}]
    assert explicit["map_shot"]["enabled"]
    assert route["map_shot"]["enabled"]


@pytest.mark.parametrize("failure, uses_backup", [
    ("Sign in to confirm your age", True),
    ("Sign in to confirm you're not a bot", False),
    ("HTTP Error 429: Too Many Requests", False),
])
def test_verified_cookie_files_fail_over_only_for_account_access(tmp_path, monkeypatch, failure, uses_backup):
    paths = []
    for label in ("first", "second"):
        path = tmp_path / f"{label}.txt"
        path.write_text(
            f"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\tfixture-{label}\n",
            encoding="utf-8",
        )
        paths.append(str(path))
    monkeypatch.setenv("VIDZEO_YT_COOKIE_FILES", os.pathsep.join(paths))
    checked = []

    def verify(jar, *, explicit_sign_in_only):
        assert explicit_sign_in_only
        checked.append(next(iter(jar)).value)
        return True, "verified"

    monkeypatch.setattr(youtube_session, "_verify_youtube_session", verify)
    primary = youtube._get_authenticated_youtube_session()
    used = []

    class FakeYoutubeDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def download(self, _urls):
            used.append(next(iter(self.cookiejar)).value)
            if used[-1] == "fixture-first":
                raise RuntimeError(failure)

    args = (SimpleNamespace(YoutubeDL=FakeYoutubeDL), {}, "https://www.youtube.com/",
            primary, None, tmp_path, "fixture.raw.")
    if uses_backup:
        youtube._download_with_youtube_session(*args)
        youtube._download_with_youtube_session(*args)
        assert checked == ["fixture-first", "fixture-second"]
        assert used == ["fixture-first", "fixture-second"] * 2
    else:
        with pytest.raises(RuntimeError, match=failure):
            youtube._download_with_youtube_session(*args)
        assert checked == used == ["fixture-first"]
