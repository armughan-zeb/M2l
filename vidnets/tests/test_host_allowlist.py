"""Host allowlist: persistent seen-host store + scraper annotation + demotions.

Every new image host costs one per-host network approval card. The store in
~/workspace/vidnets-runtime/seen_hosts.json (VIDZEO_SEEN_HOSTS_PATH override)
remembers hosts already fetched from, so later jobs reuse them. Tests use a
tmp store via monkeypatch and never touch the live store.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


def _seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDZEO_SEEN_HOSTS_PATH", str(tmp_path / "seen_hosts.json"))
    from services.web_image import host_allowlist

    return host_allowlist


def test_seed_contains_bnsf_pixel_hosts(tmp_path, monkeypatch):
    mod = _seeded(tmp_path, monkeypatch)
    seen = mod.load_seen_hosts()
    # Pixel (image_url) hosts from the BNSF pool's first 51 candidates
    # (work/r_mw85awoskks8lh, 2026-11-10, 51/51 downloaded).
    for host in (
        "i.ytimg.com",
        "i.pinimg.com",
        "live.staticflickr.com",
        "upload.wikimedia.org",
        "s3.amazonaws.com",
    ):
        assert host in seen
    assert len(seen) >= 15


def test_store_round_trip_and_fetch_count(tmp_path, monkeypatch):
    mod = _seeded(tmp_path, monkeypatch)
    path = tmp_path / "seen_hosts.json"
    mod.record_hosts(["new.example.com", "i.pinimg.com"], source="job", path=path)
    store = mod.load_store(path)
    hosts = store["hosts"]
    assert hosts["new.example.com"]["fetches"] == 1
    assert hosts["new.example.com"]["source"] == "job"
    assert hosts["i.pinimg.com"]["fetches"] == 1  # seed had 0
    mod.record_hosts(["new.example.com"], source="job", path=path)
    assert mod.load_store(path)["hosts"]["new.example.com"]["fetches"] == 2
    # Seeds survive a reload of a store written before they existed.
    assert "upload.wikimedia.org" in mod.load_store(path)["hosts"]


def test_corrupt_store_never_raises(tmp_path, monkeypatch):
    mod = _seeded(tmp_path, monkeypatch)
    path = tmp_path / "seen_hosts.json"
    path.write_text("{not json", encoding="utf-8")
    assert "upload.wikimedia.org" in mod.load_seen_hosts(path)
    assert mod.save_store(mod.load_store(path), path) is True


def test_unwritable_store_never_raises(tmp_path, monkeypatch):
    mod = _seeded(tmp_path, monkeypatch)
    # /proc is not writable: save must return False, record must not raise.
    assert mod.save_store(mod.load_store(), Path("/proc/1/seen_hosts.json")) is False
    store = mod.record_hosts(["new.example.com"], path=Path("/proc/1/seen_hosts.json"))
    assert "new.example.com" in store["hosts"]


def test_is_seen_host_matches_subdomains(tmp_path, monkeypatch):
    mod = _seeded(tmp_path, monkeypatch)
    seen = {"upload.wikimedia.org", "i.pinimg.com"}
    assert mod.is_seen_host("upload.wikimedia.org", seen)
    assert mod.is_seen_host("cdn.upload.wikimedia.org", seen)
    assert mod.is_seen_host("i.pinimg.com", seen)
    assert not mod.is_seen_host("notwikimedia.org", seen)
    assert not mod.is_seen_host("wikimedia.org.evil.test", seen)
    assert not mod.is_seen_host("", seen)


def test_preview_rescue_hosts_are_unseen_by_default(tmp_path, monkeypatch):
    # The thumbnail-CDN wave (Bing tse*/ts3, Yandex avatars) is exactly the
    # hosts the preview-rescue gate must skip: none is in the seeded store,
    # while a real pixel host passes. This is the gate's decision function.
    mod = _seeded(tmp_path, monkeypatch)
    seen = mod.load_seen_hosts()
    for host in (
        "avatars.mds.yandex.net",
        "tse1.mm.bing.net",
        "tse2.mm.bing.net",
        "tse3.mm.bing.net",
        "ts3.mm.bing.net",
    ):
        assert not mod.is_seen_host(host, seen)
    assert mod.is_seen_host("upload.wikimedia.org", seen)


def test_fast_sources_drop_openverse_and_getty_by_default(monkeypatch):
    from services.web_image.resolver import _enabled_fast_sources

    monkeypatch.delenv("VIDZEO_ENABLE_LEGACY_WEB_SEARCH", raising=False)
    monkeypatch.delenv("VIDZEO_WEB_ONLY_PROVIDER", raising=False)
    names = {name for name, _kind in _enabled_fast_sources()}
    assert "openverse" not in names  # 504s through the egress proxy
    assert "getty_bing" not in names  # 1/51 BNSF rows, watermarked
    assert "getty_ddg" not in names
    assert "wikimedia" in names  # rights-friendly lane stays
    assert "universal_scraper" in names
    # Rescue path still exists: legacy switch re-enables every lane.
    monkeypatch.setenv("VIDZEO_ENABLE_LEGACY_WEB_SEARCH", "1")
    names = {name for name, _kind in _enabled_fast_sources()}
    assert "openverse" in names and "getty_bing" in names


def test_broker_catalog_matches_demotions():
    from services.web_image.source_broker import web_image_source_catalog

    by_name = {row["name"]: row for row in web_image_source_catalog()}
    assert by_name["openverse"]["default"] is False
    assert by_name["getty_bing"]["default"] is False
    assert by_name["getty_ddg"]["default"] is False
    assert by_name["wikimedia"]["default"] is True


def test_universal_scraper_tags_host_seen(tmp_path, monkeypatch):
    mod = _seeded(tmp_path, monkeypatch)
    from services.web_image.universal_scraper import (
        UniversalImageResult,
        UniversalScraperManager,
        universal_result_to_candidate,
    )

    class StubEngine:
        name = "stub"

        def search(self, query, limit, page, safe_search):
            return [
                UniversalImageResult("t", "https://upload.wikimedia.org/a.jpg"),
                UniversalImageResult("t", "https://brand-new-cdn.example/x.jpg"),
            ]

    mgr = UniversalScraperManager(max_workers=2)
    mgr._engines = {"stub": StubEngine()}
    resp = mgr.search("BNSF snow train", engine_ids=["stub"], limit_per_engine=10)
    assert resp.total == 2
    assert resp.results[0].metadata.get("host_seen") is True
    assert resp.results[1].metadata.get("host_seen") is False
    cand = universal_result_to_candidate(resp.results[0], "BNSF snow train")
    assert cand["host_seen"] is True
    cand = universal_result_to_candidate(resp.results[1], "BNSF snow train")
    assert cand["host_seen"] is False
