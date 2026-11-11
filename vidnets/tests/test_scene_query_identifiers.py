"""Regression tests for the research/scraper hardening sweep (Nov 9 2026).

Covers:
- scene queries carry concrete script entities (locomotive/unit numbers,
  operator + number, named places) instead of generic topic words;
- bare numbers that are scores/durations never become query terms;
- hybrid search: one dead engine keeps the other engine's rows, and the
  same image under different signed URLs is deduped before vision.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from services.web_image import hybrid_search  # noqa: E402
from services.web_image.query import (  # noqa: E402
    _extract_scene_context,
    _generate_queries_mechanical,
)


def test_scene_context_keeps_locomotive_number():
    ctx = _extract_scene_context(
        "Locomotive 160 pulls out of Chicago Union Station in a whiteout",
        "",
        "Amtrak",
    )
    assert "locomotive 160" in ctx["qualifiers"]


def test_scene_context_keeps_operator_number_and_place():
    ctx = _extract_scene_context(
        "BNSF 1555 crosses Cajon Pass in heavy snow",
        "",
        "BNSF",
    )
    assert "BNSF 1555" in ctx["qualifiers"]
    assert "Cajon Pass" in ctx["qualifiers"]


def test_scene_context_ignores_bare_numbers():
    # "10 minutes" and "3 stops" are a duration and a count, not identifiers.
    ctx = _extract_scene_context(
        "The train ran 10 minutes late after 3 stops in the snow",
        "",
        "Amtrak",
    )
    assert ctx["qualifiers"] == []
    assert ctx["identifiers"] == []


def test_mechanical_queries_carry_loco_number():
    script = (
        "Locomotive 160 pulls out of Chicago Union Station in a whiteout. "
        "Passengers board a vintage car as snow piles on the platform."
    )
    rows = _generate_queries_mechanical(
        script,
        "Amtrak locomotive 160 in a whiteout",
        ["Locomotive 160 pulls out of Chicago Union Station in a whiteout."],
    )
    assert rows
    assert "160" in rows[0]["search_query"]
    assert "160" in " ".join(rows[0]["query_variants"])


def test_hybrid_search_ddg_failure_keeps_bing_rows(monkeypatch):
    bing_rows = [{
        "title": "Amtrak 160",
        "image_url": "https://images.example.com/amtrak-160.jpg",
        "source": "bing_hybrid",
    }]

    def _bing(_query, _limit=20):
        return list(bing_rows)

    def _ddg(_query, _limit=20):
        raise RuntimeError("DDG vqd token unavailable (bot check or markup change)")

    monkeypatch.setattr(hybrid_search, "search_bing", _bing)
    monkeypatch.setattr(hybrid_search, "search_ddg", _ddg)
    out = hybrid_search.search_hybrid(["Amtrak locomotive 160"], limit_per_query=5)
    assert out["Amtrak locomotive 160"] == bing_rows


def test_hybrid_search_dedups_same_path_signed_urls(monkeypatch):
    def _bing(_query, _limit=20):
        return [{
            "title": "one",
            "image_url": "https://cdn.example.com/img/amtrak.jpg?sig=aaa&size=large",
            "source": "bing_hybrid",
        }]

    def _ddg(_query, _limit=20):
        return [{
            "title": "same image, other signature",
            "image_url": "https://cdn.example.com/img/amtrak.jpg?sig=bbb&size=thumb",
            "source": "ddg_hybrid",
        }]

    monkeypatch.setattr(hybrid_search, "search_bing", _bing)
    monkeypatch.setattr(hybrid_search, "search_ddg", _ddg)
    out = hybrid_search.search_hybrid(["Amtrak locomotive 160"], limit_per_query=5)
    assert len(out["Amtrak locomotive 160"]) == 1


def test_search_ddg_raises_without_vqd_token(monkeypatch):
    class _FakeResponse:
        text = "<html><body>no token here</body></html>"

    monkeypatch.setattr(
        hybrid_search, "_request_get_with_retries", lambda *a, **k: _FakeResponse()
    )
    with pytest.raises(RuntimeError, match="vqd token"):
        hybrid_search.search_ddg("Amtrak locomotive 160", limit=5)
