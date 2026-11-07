"""No paid requests: exact-content vision savings measured with mocked calls."""
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from services.job_context import job_scope
from services.visual_intelligence import cache as cache_module
from services.visual_intelligence.cache import VisualDecisionCache, decision_cache_key
from services.visual_intelligence.vision_client import QwenVisionClient, VisionClientError


def verdict(**patch):
    return {"candidate_id": "candidate", "entity_conflict": False, "identity_score": 95,
        "action_score": 95, "context_score": 95, "quality": 95, "visible_overlays": [],
        "subject_box": {}, "safe_text_regions": [], "reason": "Matches the charging station.",
        "scene_description": "A charging station.", **patch}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    image = tmp_path / "image.png"
    Image.new("RGB", (100, 60), (12,34,56)).save(image)
    context = {"llm_cache_dir": str(tmp_path / "cache"), "usage_path": str(tmp_path / "logs" / "openrouter.jsonl")}
    calls = []
    guard = threading.Lock()
    def post(client, content, **kwargs):
        with guard: calls.append((content, kwargs))
        client.last_cost = .0005
        client.last_usage = {"prompt_tokens": 100, "completion_tokens": 10}
        time.sleep(.02)
        return json.dumps(verdict())
    monkeypatch.setattr(QwenVisionClient, "_post_json", post)
    return image, context, calls


def test_repeated_exact_scene_and_duplicate_copied_asset_uses_one_call(setup, tmp_path):
    image, context, calls = setup
    copied = tmp_path / "copy.png"; copied.write_bytes(image.read_bytes())
    with job_scope(context):
        client = QwenVisionClient("account-a", model="qwen/test")
        first = client.analyze_image(str(image), "Check charging station", "Verify pixels")
        second = client.analyze_image(str(copied), "Check charging station", "Verify pixels")
        assert second == first
        assert client.last_cost == 0 and client.last_usage == {}
    assert len(calls) == 1  # 2 requests -> 1 mocked provider call, no quality change.
    assert json.loads((tmp_path / "logs" / "cost-optimizations.jsonl").read_text())["kind"] == "vision"


@pytest.mark.parametrize("change", ["scene", "system", "pixels", "model", "account", "policy", "probe"])
def test_exact_cache_invalidates_any_effective_context_change(setup, monkeypatch, change):
    image, context, calls = setup
    with job_scope(context):
        client = QwenVisionClient("account-a", model="qwen/test")
        client.analyze_image(str(image), "Charging station", "Verify pixels")
        user, system = "Charging station", "Verify pixels"
        if change == "scene": user = "Home stationary battery"
        if change == "system": system = "New stricter event identity contract"
        if change == "pixels": Image.new("RGB", (100,60), (100,40,80)).save(image)
        if change == "model": client.model = "qwen/changed"
        if change == "account": client.api_key = "account-b"
        if change == "policy":
            previous = cache_module.vision_cache_policy
            monkeypatch.setattr(cache_module, "vision_cache_policy", lambda owner: {**previous(owner), "source_contract": "changed-schema-and-prompt-code"})
        if change == "probe": monkeypatch.setenv("VIDZEO_VISION_IMAGE_PROBE_EDGE", "576")
        client.analyze_image(str(image), user, system)
    assert len(calls) == 2


def test_cache_preserves_complete_negative_identity_verdict(setup, monkeypatch):
    image, context, calls = setup
    def post(*args, **kwargs):
        calls.append(1)
        return json.dumps(verdict(entity_conflict=True, identity_score=0, reason="Wrong subject"))
    monkeypatch.setattr(QwenVisionClient, "_post_json", post)
    with job_scope(context):
        client = QwenVisionClient("account-a", model="qwen/test")
        assert client.analyze_image(str(image), "Charging station", "Verify pixels")["entity_conflict"] is True
        assert client.analyze_image(str(image), "Charging station", "Verify pixels")["entity_conflict"] is True
    assert len(calls) == 1


def test_transient_error_and_incomplete_schema_are_never_cached(setup, monkeypatch):
    image, context, calls = setup
    state = ["error", "incomplete", "valid"]
    def post(*args, **kwargs):
        calls.append(1)
        mode = state.pop(0)
        if mode == "error": raise VisionClientError("HTTP503 provider unavailable")
        return "{}" if mode == "incomplete" else json.dumps(verdict())
    monkeypatch.setattr(QwenVisionClient, "_post_json", post)
    with job_scope(context):
        client = QwenVisionClient("account-a", model="qwen/test")
        with pytest.raises(VisionClientError): client.analyze_image(str(image), "Charging station", "Verify pixels")
        client.analyze_image(str(image), "Charging station", "Verify pixels")
        client.analyze_image(str(image), "Charging station", "Verify pixels")
        client.analyze_image(str(image), "Charging station", "Verify pixels")
    assert len(calls) == 3


def test_six_simultaneous_same_scene_requests_coalesce_without_paid_duplicates(setup):
    image, context, calls = setup
    def worker(index):
        with job_scope(context):
            return QwenVisionClient("account-a", model="qwen/test").analyze_image(str(image), "Charging station", "Verify pixels")
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(worker, range(6)))
    assert all(row == results[0] for row in results)
    assert len(calls) == 1  # 6 identical workers -> 1 mocked call, 5 actual call avoidances.


def test_failed_owner_is_shared_with_existing_waiters_but_new_call_retries(setup, monkeypatch):
    image, context, calls = setup
    arrived, release = threading.Event(), threading.Event()
    original_flight = cache_module._VisionFlight
    class CountingLock:
        def __init__(self):
            self.lock = threading.Lock()
            self.guard = threading.Lock()
            self.threads = set()
        def acquire(self, **kwargs):
            with self.guard:
                self.threads.add(threading.get_ident())
                if len(self.threads) == 6: arrived.set()
            return self.lock.acquire(**kwargs)
        def release(self): self.lock.release()
    class Flight(original_flight):
        def __init__(self):
            super().__init__()
            self.lock = CountingLock()
    monkeypatch.setattr(cache_module, "_VisionFlight", Flight)
    def post(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            assert release.wait(3)
            raise VisionClientError("One provider outage")
        return json.dumps(verdict())
    monkeypatch.setattr(QwenVisionClient, "_post_json", post)
    def worker(_index):
        with job_scope(context):
            return QwenVisionClient("account-a", model="qwen/test").analyze_image(str(image), "Charging station", "Verify pixels")
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(worker, index) for index in range(6)]
        assert arrived.wait(3)
        release.set()
        for pending in futures:
            with pytest.raises(VisionClientError, match="One provider outage"):
                pending.result()
    assert len(calls) == 1  # Existing waiters do not each bill a retry after owner failure.
    assert worker(7)["identity_score"] == 95
    assert len(calls) == 2  # A genuinely new explicit call can recover immediately.


@pytest.mark.parametrize("payload", [
    {"reason": "choose image"},
    {"selected_index": 9, "acceptable": True, "reason": "Matches", "confidence": 95},
    {"selected_index": "0", "acceptable": True, "reason": "Matches", "confidence": 95},
    {"selected_index": True, "acceptable": True, "reason": "Matches", "confidence": 95},
    {"selected_index": 0, "acceptable": True, "reason": "Matches", "confidence": True},
    {"selected_index": 0, "acceptable": True, "reason": "Matches", "confidence": "95"},
    {"selected_index": 0, "acceptable": True, "reason": [], "confidence": 95},
    {"selected_index": 0, "acceptable": "yes", "reason": "Matches", "confidence": 95},
])
def test_incomplete_or_invalid_comparison_never_becomes_cached_approval(setup, monkeypatch, payload):
    image, context, calls = setup
    def post(*args, **kwargs):
        calls.append(1)
        return json.dumps(payload)
    monkeypatch.setattr(QwenVisionClient, "_post_json", post)
    with job_scope(context):
        client = QwenVisionClient("account-a", model="qwen/test")
        first = client.compare_images([str(image)], "Select exact charging station")
        second = client.compare_images([str(image)], "Select exact charging station")
        assert first["_schema_errors"] and first["acceptable"] is False
        assert second["acceptable"] is False
    assert len(calls) == 2


def test_complete_comparison_reuses_valid_raw_schema(setup, monkeypatch):
    image, context, calls = setup
    def post(*args, **kwargs):
        calls.append(1)
        return json.dumps({"selected_index": 0, "acceptable": True, "reason": "Exact charging station", "confidence": 95})
    monkeypatch.setattr(QwenVisionClient, "_post_json", post)
    with job_scope(context):
        client = QwenVisionClient("account-a", model="qwen/test")
        first = client.compare_images([str(image)], "Select exact charging station")
        assert client.compare_images([str(image)], "Select exact charging station") == first
        assert first["acceptable"] and not first["_schema_errors"]
    assert len(calls) == 1


def test_malformed_review_status_does_not_break_cache_validation():
    assert not cache_module.valid_cached_decision("review_editorial_frames", {"status": {"bad": "shape"}})


def test_editorial_review_cache_binds_frames_order_and_full_context(setup, monkeypatch, tmp_path):
    image, context, calls = setup
    second = tmp_path / "second.png"; Image.new("RGB", (100,60), (90,30,5)).save(second)
    def post(*args, **kwargs):
        calls.append(1)
        return json.dumps({"status": "pass", "confidence": 90, "repair_kind": "none", "issues": [], "patch": None})
    monkeypatch.setattr(QwenVisionClient, "_post", post)
    with job_scope(context):
        client = QwenVisionClient("account-a", model="qwen/test")
        frames = [str(image),str(second)]
        client.review_editorial_frames(frames, '{"narration":"Charging"}')
        client.review_editorial_frames(frames, '{"narration":"Charging"}')
        assert len(calls) == 1
        client.review_editorial_frames(list(reversed(frames)), '{"narration":"Charging"}')
        client.review_editorial_frames(frames, '{"narration":"Storage"}')
    assert len(calls) == 3


def test_live_probe_always_reaches_provider_and_is_not_cached(setup):
    _image, context, calls = setup
    with job_scope(context):
        client = QwenVisionClient("account-a", model="qwen/test", fallback_models=[])
        assert client.probe()[0]
        assert client.probe()[0]
    assert len(calls) == 2


def test_missing_source_cannot_reuse_filename_based_approval(tmp_path):
    with pytest.raises(ValueError):
        decision_cache_key(operation="image", model="qwen/test", prompt="same", paths=[str(tmp_path/"missing.png")])


def test_expired_cache_is_miss_and_write_renews_creation(tmp_path, monkeypatch):
    store = VisualDecisionCache(str(tmp_path / "cache.sqlite3"))
    monkeypatch.setattr(cache_module.time, "time", lambda: 1.)
    store.put("key", "image", "qwen/test", {"reason": "verified"})
    monkeypatch.setattr(cache_module.time, "time", lambda: 8 * 86400.)
    assert store.get("key") is None
    store.put("key", "image", "qwen/test", {"reason": "fresh"})
    assert store.get("key")["reason"] == "fresh"


def test_original_probe_resolution_unchanged(setup):
    image, context, calls = setup
    from services.visual_intelligence.vision_client import _vision_image_probe_edge, _vision_frame_probe_edge
    assert _vision_image_probe_edge() == 512
    assert _vision_frame_probe_edge() == 384


def test_waiter_deadline_never_starts_another_billed_call(setup, monkeypatch):
    image, context, calls = setup
    started, release = threading.Event(), threading.Event()
    def post(*args, **kwargs):
        calls.append(1); started.set()
        release.wait(2)
        return json.dumps(verdict())
    monkeypatch.setattr(QwenVisionClient, "_post_json", post)
    def owner():
        with job_scope({**context, "deadline_at": time.monotonic()+3}):
            return QwenVisionClient("account-a", model="qwen/test").analyze_image(str(image), "Charging station", "Verify pixels")
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending=executor.submit(owner)
        assert started.wait(1)
        with job_scope({**context, "deadline_at": time.monotonic()+.02}):
            with pytest.raises(TimeoutError):
                QwenVisionClient("account-a", model="qwen/test").analyze_image(str(image), "Charging station", "Verify pixels")
        assert len(calls) == 1
        release.set(); assert pending.result()["identity_score"] == 95


def test_context_cache_includes_account_and_suppresses_recursive_failed_waiters(setup):
    image, _context, calls = setup
    from services.visual_intelligence.context import VisualDecisionContext, _ContextClient
    class CustomClient:
        model="qwen/test"
        last_cost=0.
        def __init__(self,key): self.api_key=key
    store = VisualDecisionCache(str(image.parent / "context.sqlite3"))
    def wrap(key):
        ctx=VisualDecisionContext(enabled=True,api_key=key,model="qwen/test",scene_count=1,max_calls=4,
            budget_usd=1,deadline_at=time.monotonic()+3,cache=store)
        return _ContextClient(ctx,CustomClient(key))
    def callback():
        calls.append(1)
        return verdict()
    # Custom result includes schema evidence only after the real normalizer;
    # include complete legacy reason/scene/quality for the fake verifier.
    first=wrap("account-a")
    first._run("image",[str(image)],"Charging",callback)
    first._run("image",[str(image)],"Charging",callback)
    second=wrap("account-b")
    second._run("image",[str(image)],"Charging",callback)
    assert len(calls)==2
    failed=wrap("account-c")
    started,release=threading.Event(),threading.Event()
    def failure():
        calls.append(1);started.set();release.wait(1)
        raise VisionClientError("Provider unavailable")
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending=executor.submit(failed._run,"image",[str(image)],"Charging",failure)
        assert started.wait(1)
        failed._context.deadline_at=time.monotonic()+.02
        with pytest.raises(VisionClientError):
            failed._run("image",[str(image)],"Charging",callback)
        assert len(calls)==3
        release.set()
        with pytest.raises(VisionClientError): pending.result()


def test_unavailable_cache_runs_normal_verifier_without_lowering_quality(setup, monkeypatch):
    image, context, calls = setup
    def unavailable(*args, **kwargs):
        raise OSError("Cache directory is temporarily unavailable")
    monkeypatch.setattr(cache_module, "VisualDecisionCache", unavailable)
    with job_scope(context):
        client=QwenVisionClient("account-a", model="qwen/test")
        assert client.analyze_image(str(image), "Charging station", "Verify pixels")["identity_score"] == 95
        assert client.analyze_image(str(image), "Charging station", "Verify pixels")["identity_score"] == 95
    assert len(calls)==2


def test_invalid_image_schema_is_not_reused(setup, monkeypatch):
    image,context,calls=setup
    def post(*args,**kwargs):
        calls.append(1)
        return json.dumps(verdict(identity_score="high"))
    monkeypatch.setattr(QwenVisionClient,"_post_json",post)
    with job_scope(context):
        client=QwenVisionClient("account-a",model="qwen/test")
        first=client.analyze_image(str(image),"Charging station","Verify pixels")
        second=client.analyze_image(str(image),"Charging station","Verify pixels")
        assert first["_schema_errors"] == second["_schema_errors"] == ["identity_score_not_number"]
    assert len(calls)==2


def test_asset_changed_during_provider_call_cannot_cache_old_content_key(setup, monkeypatch):
    image,context,calls=setup
    original=image.read_bytes()
    def post(*args,**kwargs):
        calls.append(1)
        Image.new("RGB",(100,60),(250,240,230)).save(image)
        return json.dumps(verdict())
    monkeypatch.setattr(QwenVisionClient,"_post_json",post)
    with job_scope(context):
        client=QwenVisionClient("account-a",model="qwen/test")
        client.analyze_image(str(image),"Charging station","Verify pixels")
        image.write_bytes(original)
        client.analyze_image(str(image),"Charging station","Verify pixels")
    assert len(calls)==2


def test_context_keeps_system_user_roles_distinct(setup, monkeypatch):
    image,_context,calls=setup
    from services.visual_intelligence.context import VisualDecisionContext,_ContextClient
    class CustomClient:
        model="qwen/test";api_key="account-a";last_cost=0.
        def analyze_image(self,*args):
            calls.append(1);return verdict()
    context=VisualDecisionContext(enabled=True,api_key="account-a",model="qwen/test",scene_count=1,max_calls=4,
        budget_usd=1,deadline_at=time.monotonic()+3,cache=VisualDecisionCache(str(image.parent/"roles.sqlite3")))
    client=_ContextClient(context,CustomClient())
    client.analyze_image(str(image),"c","a\nb")
    client.analyze_image(str(image),"b\nc","a")
    assert len(calls)==2


def test_raw_cache_hit_does_not_consume_context_visual_quota_or_duplicate_savings(setup, tmp_path):
    image,scope,calls=setup
    from services.visual_intelligence.context import VisualDecisionContext,_ContextClient
    with job_scope(scope):
        raw=QwenVisionClient("account-a",model="qwen/test")
        first=raw.analyze_image(str(image),"Charging station","Verify pixels")
        context=VisualDecisionContext(enabled=True,api_key="account-a",model="qwen/test",scene_count=1,max_calls=0,
            budget_usd=1,deadline_at=time.monotonic()+3,cache=VisualDecisionCache(str(tmp_path/"separate-context.sqlite3")))
        wrapped=_ContextClient(context,raw)
        assert wrapped.analyze_image(str(image),"Charging station","Verify pixels") == first
        assert context.calls==0 and context.cache_hits==1 and context.cost_usd==0
        assert context.decisions[-1]["cache_hit"] is True
    assert len(calls)==1
    savings=(tmp_path/"logs"/"cost-optimizations.jsonl").read_text().splitlines()
    assert len(savings)==1  # Inner reuse emitted one event; wrapper doesn't duplicate.


def test_probe_health_is_isolated_by_account_and_model(monkeypatch):
    from services.visual_intelligence import context as module
    module._PROBE_DONE.clear();module._PROBE_LAST_FAILURE_AT.clear()
    monkeypatch.setenv("VIDZEO_VISION_PROBE","1")
    calls=[]
    class Probe:
        provider="openrouter"
        def __init__(self,key,model="qwen/test"): self.api_key=key;self.model=model
        def probe(self):
            calls.append((self.api_key,self.model))
            return (True,"healthy") if self.api_key=="account-a" else (False,"401 invalid account")
    assert module._maybe_probe_vision(Probe("account-a"))[0]
    assert not module._maybe_probe_vision(Probe("account-b"))[0]
    assert module._maybe_probe_vision(Probe("account-a"))[0]
    assert module._maybe_probe_vision(Probe("account-a","qwen/other"))[0]
    assert len(calls)==3
    assert all("account-a" not in key and "account-b" not in key for key in module._PROBE_DONE)
