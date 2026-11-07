from __future__ import annotations

import os
import json
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.research_ledger import _safe_url, build_research_ledger


CLAIM = "GPS requires corrections for relativity."
SOURCE = {"id": "nasa", "url": "https://www.nasa.gov/example", "title": "GPS clock science", "publisher": "NASA", "source_kind": "primary"}
QUOTE = "GPS requires corrections for relativity to keep satellite clocks synchronized."
REVIEW = {"claim_id": "scene:0:claim:0", "claim_text": CLAIM, "verdict": "supported", "reviewer_kind": "llm", "reviewer_id": "independent-fact-checker", "reason": "The primary reference explicitly describes relativity corrections in GPS clocks.", "source_id": "nasa", "evidence_quote": QUOTE}


def _fetch(url, deadline):
    return {"status": "retrieved", "text": QUOTE}


def test_no_sources_never_claims_narration_is_verified():
    result = build_research_ledger([CLAIM, "The ship departed in 1912."])
    assert result["status"] == "needs_review"
    assert result["supported_claim_count"] == 0
    assert result["unresolved_claim_ids"] == ["scene:0:claim:0", "scene:1:claim:0"]


def test_matching_fetched_text_alone_does_not_certify_claim():
    result = build_research_ledger([CLAIM], sources=[SOURCE], fetch_sources=True, fetcher=_fetch)
    assert result["sources"][0]["retrieval_status"] == "retrieved"
    assert result["claims"][0]["status"] == "unresolved"
    assert result["claims"][0]["reason"] == "no_independent_evidence_review"


def test_supplied_source_content_is_not_independently_retrieved():
    result = build_research_ledger([CLAIM], sources=[{**SOURCE, "text": QUOTE}], claim_reviews=[REVIEW])
    assert result["sources"][0]["retrieval_status"] == "supplied_unverified"
    assert result["claims"][0]["reason"] == "evidence_source_not_retrieved"


def test_explicit_independent_review_with_exact_fetched_evidence_retains_provenance():
    result = build_research_ledger([CLAIM], sources=[SOURCE], claim_reviews=[REVIEW], fetch_sources=True, fetcher=_fetch)
    assert result["status"] == "reviewed_source_support"
    assert result["supported_claim_count"] == 1
    assert result["claims"][0]["evidence"][0]["url"] == SOURCE["url"]
    assert result["claims"][0]["evidence"][0]["source_content_sha256"]
    assert "text" not in result["sources"][0]
    assert result["limitations"]


def test_fabricated_quote_stays_unresolved():
    result = build_research_ledger([CLAIM], sources=[SOURCE], claim_reviews=[{**REVIEW, "evidence_quote": "An invented quote claims the satellite moves impossibly fast."}], fetch_sources=True, fetcher=_fetch)
    assert result["claims"][0]["reason"] == "evidence_quote_not_anchored_in_source"


def test_review_of_old_script_cannot_apply_to_changed_narration():
    result = build_research_ledger(["GPS requires no corrections."], sources=[SOURCE], claim_reviews=[REVIEW], fetch_sources=True, fetcher=_fetch)
    assert result["claims"][0]["reason"] == "review_claim_text_mismatch"


def test_unidentified_source_authority_does_not_support_claim():
    result = build_research_ledger([CLAIM], sources=[{**SOURCE, "source_kind": "unknown"}], claim_reviews=[REVIEW], fetch_sources=True, fetcher=_fetch)
    assert result["claims"][0]["reason"] == "source_authority_not_identified"


def test_contested_claim_is_never_marked_supported():
    result = build_research_ledger([CLAIM], sources=[SOURCE], claim_reviews=[{**REVIEW, "verdict": "contradicted"}], fetch_sources=True, fetcher=_fetch)
    assert result["claims"][0]["status"] == "contested"
    assert result["status"] == "needs_review"


def test_slow_fetch_cannot_hold_ledger_past_budget():
    def slow(url, deadline):
        time.sleep(0.2)
        return _fetch(url, deadline)

    started = time.monotonic()
    result = build_research_ledger([CLAIM], sources=[SOURCE], claim_reviews=[REVIEW], fetch_sources=True, fetcher=slow, budget_seconds=0.01)
    assert time.monotonic() - started < 0.15
    assert result["sources"][0]["retrieval_status"] == "deadline_exceeded"
    assert result["status"] == "needs_review"


def test_local_private_and_credential_urls_are_rejected():
    for url in ("file:///secret", "https://127.0.0.1/path", "http://10.0.0.1/a", "http://localhost/a", "http://private.local/a", "https://username:password@example.com/a", "https://example.com:5000/a"):
        assert not _safe_url(url)
    assert _safe_url("https://science.nasa.gov/eclipses/")


def test_decimal_numbers_remain_in_exact_claim_span():
    result = build_research_ledger(["The delay is 1.5 seconds. The next beat arrives."])
    assert result["claim_count"] == 2
    assert result["claims"][0]["spoken_text"] == "The delay is 1.5 seconds."


def test_semantic_manifest_has_explicit_unresolved_research_ledger(tmp_path):
    from services.semantic_edit_engine import build_semantic_edit_manifest

    result = build_semantic_edit_manifest([CLAIM], [3], config={"director_treatment_enabled": False})
    assert result["research_ledger"]["status"] == "needs_review"
    assert result["metrics"]["unresolved_research_claim_count"] == 1


class _Reviewer:
    model = "independent-test-reviewer"

    def __init__(self):
        self.requests = []

    def chat(self, **kwargs):
        payload = json.loads(kwargs["user_prompt"])
        self.requests.append(payload)
        return json.dumps({"reviews": [{**REVIEW, "claim_id": row["claim_id"], "claim_text": row["claim_text"]} for row in payload["owned_claims"]]})


def test_independent_llm_reviews_retrieved_primary_source_with_full_context():
    client = _Reviewer()
    result = build_research_ledger([CLAIM, CLAIM], sources=[SOURCE], fetch_sources=True, fetcher=_fetch, llm_client=client)
    assert result["supported_claim_count"] == 2
    assert result["independent_llm_review"]["status"] == "complete"
    assert len(client.requests[0]["film_context"]) == 2
    assert client.requests[0]["sources"][0]["source_text"] == QUOTE
    assert result["claims"][0]["reviewer"]["id"] == client.model


def test_llm_invented_quote_is_rejected_after_generation():
    class FabricatingReviewer(_Reviewer):
        def chat(self, **kwargs):
            return json.dumps({"reviews": [{**REVIEW, "evidence_quote": "This quote did not appear in the primary source."}]})

    result = build_research_ledger([CLAIM], sources=[SOURCE], fetch_sources=True, fetcher=_fetch, llm_client=FabricatingReviewer())
    assert result["supported_claim_count"] == 0
    assert result["claims"][0]["reason"] == "evidence_quote_not_anchored_in_source"


def test_parallel_review_preserves_fast_chunk_when_other_chunk_is_slow():
    class SlowOneChunk(_Reviewer):
        def chat(self, **kwargs):
            payload = json.loads(kwargs["user_prompt"])
            if payload["owned_claims"][0]["scene_index"] == 6:
                time.sleep(0.2)
            return super().chat(**kwargs)

    started = time.monotonic()
    result = build_research_ledger([CLAIM] * 8, sources=[SOURCE], fetch_sources=True, fetcher=_fetch, llm_client=SlowOneChunk(), budget_seconds=0.08)
    assert time.monotonic() - started < 0.17
    # Budget <0.2s intentionally cannot launch LLM requests; return promptly.
    assert result["status"] == "needs_review"
    assert result["independent_llm_review"]["status"] == "partial"


def test_fast_chunks_survive_slow_provider_with_realistic_minimum_budget():
    class SlowOneChunk(_Reviewer):
        def chat(self, **kwargs):
            payload = json.loads(kwargs["user_prompt"])
            if payload["owned_claims"][0]["scene_index"] == 6:
                time.sleep(0.6)
            return super().chat(**kwargs)

    started = time.monotonic()
    result = build_research_ledger([CLAIM] * 8, sources=[SOURCE], fetch_sources=True, fetcher=_fetch, llm_client=SlowOneChunk(), budget_seconds=0.3)
    assert time.monotonic() - started < 0.45
    assert result["supported_claim_count"] == 6
    assert result["independent_llm_review"]["successful_chunk_count"] == 1
    assert result["independent_llm_review"]["status"] == "partial"


def test_discovered_primary_sources_use_same_fetch_and_review_contract(monkeypatch):
    monkeypatch.setattr("services.research_ledger._discover_sources", lambda texts, topic, deadline: ([SOURCE], {"status": "sources_found", "provider": "existing_ddgs"}))
    result = build_research_ledger([CLAIM], discover_sources=True, fetcher=_fetch, llm_client=_Reviewer(), topic="GPS")
    assert result["supported_claim_count"] == 1
    assert result["source_discovery"]["provider"] == "existing_ddgs"


def test_discovery_provider_failure_keeps_claims_unresolved(monkeypatch):
    monkeypatch.setattr("services.research_ledger._discover_sources", lambda texts, topic, deadline: ([], {"status": "unavailable", "reason": "provider_unavailable"}))
    result = build_research_ledger([CLAIM], discover_sources=True, llm_client=_Reviewer())
    assert result["status"] == "needs_review"
    assert result["supported_claim_count"] == 0


def test_unreviewed_factual_source_marks_omitted_without_removing_media_attribution():
    from services.research_ledger import reconcile_research_source_marks

    manifest = {"scenes": [{"primary_visual": {"type": "Chart", "props": {"source": "NASA", "citation_url": "https://example.com/fake"}}, "media": {"attribution": "Photo: NASA"}}, {"primary_visual": {"type": "SatelliteMap", "props": {"source": "Map attribution"}}}], "research_ledger": {"claims": [], "sources": []}}
    result = reconcile_research_source_marks(manifest)
    assert result["omitted_source_mark_count"] == 2
    assert manifest["scenes"][0]["primary_visual"]["props"] == {}
    assert manifest["scenes"][0]["media"]["attribution"] == "Photo: NASA"
    assert manifest["scenes"][1]["primary_visual"]["props"]["source"] == "Map attribution"


def test_reviewed_source_marks_are_bound_to_the_claims_scene():
    from services.research_ledger import reconcile_research_source_marks

    ledger = build_research_ledger([CLAIM, CLAIM], sources=[SOURCE], claim_reviews=[REVIEW], fetch_sources=True, fetcher=_fetch)
    manifest = {"research_ledger": ledger, "scenes": [{"primary_visual": {"type": "Chart", "props": {"source_label": "NASA", "citation_url": SOURCE["url"]}}}, {"primary_visual": {"type": "Chart", "props": {"citation_url": SOURCE["url"]}}}]}
    # Source record explicitly names NASA as its publisher for this fixture.
    assert reconcile_research_source_marks(manifest)["omitted_source_mark_count"] == 1
    assert manifest["scenes"][0]["primary_visual"]["props"]["citation_url"] == SOURCE["url"]
    assert manifest["scenes"][1]["primary_visual"]["props"] == {}
