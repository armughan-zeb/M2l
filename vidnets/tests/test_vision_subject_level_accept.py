"""Subject-level acceptance for every niche, both lanes (plan §2/§3).

Generalises the run-9 GTA-only abstract rule: a candidate (YouTube clip
or web image) is ACCEPTED when it authentically shows the scene's
SUBJECT — the game, the canal, the train, the person — even when the
narration is abstract or vision rejected it only for literal mismatch.
Vision's score is preserved for ranking; a low score never rejects.
Hard rejects remain ONLY: wrong subject, wrong entity, terrible
quality, duplicate, or a direct contradiction (wrong era / location /
entity / game). The 2002 Vice City cutscene stays rejected everywhere;
GTA V footage is only allowed for scenes narrating GTA V. No throttle
or concurrency value is touched by this change.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.media_resolution_coordinator import (
    _candidate_hard_gate,
    _is_fatal_vision_verdict,
    _verification_result,
)
from services.pipeline_v2_media_adapter import _score_verdict
from services.visual_intelligence.semantic_guard import (
    candidate_proves_subject,
    is_abstract_narration,
    scene_subject,
    subject_level_accepts_verdict,
)

# ---------------------------------------------------------------------------
# Fixtures: Panama Canal documentary scenes (title verbatim from the
# launch-day plan) and verdicts modelled on the run-9 stored verdicts.
# ---------------------------------------------------------------------------

PANAMA_TITLE = "How the Panama Canal Changed World Trade"


def panama_scene(text, canonical="Panama Canal", subject_type="place",
                 film_subject="", strict_identity=False):
    requirement = {
        "canonical_entity": canonical,
        "subject_type": subject_type,
        "strict_identity": strict_identity,
        "aliases": ["Panama Canal"],
        "subject_context": {"film_subject": film_subject},
    }
    return {
        "text": text,
        "content_title": PANAMA_TITLE,
        "visual_requirement": requirement,
    }


PANAMA_TRADE_TEXT = (
    "That is the promise of this crossing and the question of this film "
    "is what two hours between oceans does to world trade."
)
PANAMA_CONCRETE_TEXT = (
    "Cross from the Pacific to the Caribbean in just two hours through "
    "the Panama Canal."
)


def subject_fatal_verdict(candidate_id, reason, presentation="photograph",
                         identity=50.0, final=40.0, context=80.0):
    """A run-9 style fatal: subject pixels, 'subject entirely absent'."""
    return {
        "candidate_id": candidate_id,
        "entity_conflict": False,
        "identity_score": identity,
        "action_score": 40.0,
        "context_score": context,
        "fatal_mismatch": True,
        "domain_match": True,
        "scene_match": False,
        "final_score": final,
        "quality": 70.0,
        "watermark": False,
        "source_presentation_type": presentation,
        "visual_checked": True,
        "reason": reason,
        "scene_description": (
            "Aerial photograph of the Panama Canal locks with a cargo "
            "ship transiting."
        ),
    }


CANAL_YT_REASON = (
    "Shows a container ship transiting the Panama Canal Miraflores "
    "Locks; no trade figures or economic impact are visible — the "
    "scene's subject is entirely absent."
)
CANAL_WEB_REASON = (
    "Aerial photograph of the Panama Canal locks with a ship; the "
    "narration about world trade is not literally depicted — the "
    "scene's subject is entirely absent."
)


def web_candidate(title, description="", path="/tmp/does-not-exist.jpg",
                  source="bing"):
    return {
        "candidate_id": f"web:{title[:12]}",
        "source": source,
        "provider": source,
        "title": title,
        "description": description,
        "path": path,
    }


def yt_candidate(video_id, title, description="", path="/tmp/does-not-exist.mp4"):
    return {
        "candidate_id": f"youtube:{video_id}",
        "source": "youtube",
        "provider": "youtube",
        "title": title,
        "description": description,
        "path": path,
    }


# ---------------------------------------------------------------------------
# Subject derivation
# ---------------------------------------------------------------------------

def test_scene_subject_panama_from_canonical():
    subject = scene_subject(panama_scene(PANAMA_TRADE_TEXT))
    assert subject == {"name": "Panama Canal", "type": "place"}


def test_scene_subject_prefers_film_subject():
    scene = panama_scene(PANAMA_TRADE_TEXT, film_subject="Panama Canal")
    assert scene_subject(scene) == {"name": "Panama Canal", "type": "place"}


def test_scene_subject_train_is_machine():
    scene = {
        "text": "and the numbers are already staggering.",
        "content_title": "The California Zephyr",
        "visual_requirement": {
            "canonical_entity": "California Zephyr",
            "subject_type": "train",
            "strict_identity": True,
            "aliases": [],
            "subject_context": {"film_subject": ""},
        },
    }
    assert scene_subject(scene) == {"name": "California Zephyr", "type": "machine"}


def test_scene_subject_abstract_concept_canonical_fails_closed():
    # The planner stored the abstract concept as the canonical entity;
    # that is not a subject, and the (question-form) title is not a
    # bare entity either — so the scene has NO subject and the rule
    # must not fire.
    scene = panama_scene(PANAMA_TRADE_TEXT, canonical="world trade",
                         subject_type="concept")
    assert scene_subject(scene) is None
    verdict = subject_fatal_verdict("web:canal1", CANAL_WEB_REASON)
    assert not subject_level_accepts_verdict(verdict, scene)


def test_scene_subject_single_word_concept_fails_closed():
    scene = panama_scene(PANAMA_TRADE_TEXT, canonical="Expect",
                         subject_type="concept")
    assert scene_subject(scene) is None


# ---------------------------------------------------------------------------
# Abstract detector, generalised
# ---------------------------------------------------------------------------

def test_panama_trade_scene_is_abstract():
    assert is_abstract_narration(panama_scene(PANAMA_TRADE_TEXT))


def test_panama_concrete_scene_not_abstract():
    assert not is_abstract_narration(panama_scene(PANAMA_CONCRETE_TEXT))


def test_run9_concrete_scene_still_not_abstract():
    # "After more than a decade of waiting" must stay concrete-gated:
    # "decade" is deliberately not an abstract signal.
    scene = {
        "text": "After more than a decade of waiting, launch day has arrived — ",
        "content_title": "Grand Theft Auto VI",
        "visual_requirement": {
            "canonical_entity": "Grand Theft Auto VI",
            "subject_type": "video_game",
            "strict_identity": True,
            "aliases": ["GTA 6", "GTA VI"],
            "subject_context": {"film_subject": "Grand Theft Auto VI"},
        },
    }
    assert not is_abstract_narration(scene)


# ---------------------------------------------------------------------------
# Gate: accept / reject matrix on stored-style verdicts
# ---------------------------------------------------------------------------

def test_panama_yt_clip_accepts_on_literal_rejection():
    scene = panama_scene(PANAMA_TRADE_TEXT)
    verdict = subject_fatal_verdict(
        "youtube:canalclip", CANAL_YT_REASON, presentation="youtube_frame",
    )
    candidate = yt_candidate(
        "canalclip", "Panama Canal: Ships Transiting the Locks",
        "Container ships transit the Panama Canal Miraflores Locks.",
    )
    assert subject_level_accepts_verdict(verdict, scene, candidate)


def test_panama_web_image_accepts_on_literal_rejection():
    scene = panama_scene(PANAMA_TRADE_TEXT)
    verdict = subject_fatal_verdict("web:canal1", CANAL_WEB_REASON)
    candidate = web_candidate(
        "Panama Canal locks aerial view",
        "Aerial view of ships transiting the Panama Canal locks.",
    )
    assert subject_level_accepts_verdict(verdict, scene, candidate)


def test_panama_concrete_scene_subject_footage_accepts():
    # Not abstract — but the verdict rejected only on literal mismatch
    # while the pixels ARE the subject, so the subject rule still fires.
    scene = panama_scene(PANAMA_CONCRETE_TEXT)
    verdict = subject_fatal_verdict(
        "youtube:canalclip",
        "Shows a container ship transiting the Panama Canal; the two-hour "
        "crossing itself is not literally depicted — the scene's subject "
        "is entirely absent.",
        presentation="youtube_frame",
    )
    candidate = yt_candidate(
        "canalclip", "Panama Canal: Ships Transiting the Locks",
        "Container ships transit the Panama Canal.",
    )
    assert subject_level_accepts_verdict(verdict, scene, candidate)


def test_suez_canal_still_rejects():
    scene = panama_scene(PANAMA_TRADE_TEXT)
    verdict = subject_fatal_verdict(
        "youtube:suezclip",
        "Shows the Suez Canal in Egypt — wrong location and wrong subject "
        "for the Panama Canal scene.",
        presentation="youtube_frame",
    )
    candidate = yt_candidate(
        "suezclip", "Suez Canal transit, Egypt",
        "A container ship transits the Suez Canal.",
    )
    assert not subject_level_accepts_verdict(verdict, scene, candidate)


def test_1914_scene_modern_photo_rejects_on_era():
    scene = panama_scene(
        "Construction crews dig the Culebra Cut in 1914.",
        canonical="Panama Canal",
    )
    verdict = subject_fatal_verdict(
        "web:canal2016",
        "Shows the modern canal with container ships in 2016, not the "
        "1914 construction.",
    )
    candidate = web_candidate(
        "Panama Canal 2016", "The modern Panama Canal with container ships.",
    )
    assert not subject_level_accepts_verdict(verdict, scene, candidate)


def test_2002_vice_city_still_rejects_on_web_too():
    scene = {
        "text": "current-gen only.",
        "content_title": "Grand Theft Auto VI",
        "visual_requirement": {
            "canonical_entity": "Grand Theft Auto VI",
            "subject_type": "video_game",
            "strict_identity": True,
            "aliases": ["GTA 6", "GTA VI"],
            "subject_context": {"film_subject": "Grand Theft Auto VI"},
        },
    }
    verdict = subject_fatal_verdict(
        "web:vc2002",
        "A 2002 Vice City cutscene — Tommy Vercetti in the original 2002 "
        "game, wrong era and wrong game for a GTA VI launch scene.",
    )
    candidate = web_candidate(
        "GTA Vice City 2002 gameplay screenshot",
        "Tommy Vercetti cutscene from the original 2002 game.",
    )
    assert not subject_level_accepts_verdict(verdict, scene, candidate)


def test_gta_v_footage_accepts_only_for_gta_v_scenes():
    gta_v_scene = {
        "text": "GTA V has sold over 200 million copies since 2013.",
        "content_title": "Grand Theft Auto V",
        "visual_requirement": {
            "canonical_entity": "Grand Theft Auto V",
            "subject_type": "video_game",
            "strict_identity": True,
            "aliases": ["GTA 5", "GTA V"],
            "subject_context": {"film_subject": "Grand Theft Auto V"},
        },
    }
    gta_vi_scene = {
        "text": "current-gen only.",
        "content_title": "Grand Theft Auto VI",
        "visual_requirement": {
            "canonical_entity": "Grand Theft Auto VI",
            "subject_type": "video_game",
            "strict_identity": True,
            "aliases": ["GTA 6", "GTA VI"],
            "subject_context": {"film_subject": "Grand Theft Auto VI"},
        },
    }
    verdict = subject_fatal_verdict(
        "youtube:gta5clip",
        "GTA V gameplay footage of Los Santos; no sales figures are "
        "visible — the scene's subject is entirely absent.",
        presentation="gameplay_footage",
    )
    candidate = yt_candidate(
        "gta5clip", "GTA V gameplay — Los Santos",
        "Grand Theft Auto V gameplay in Los Santos.",
    )
    assert subject_level_accepts_verdict(verdict, gta_v_scene, candidate)
    assert not subject_level_accepts_verdict(verdict, gta_vi_scene, candidate)


def test_watermarked_web_image_still_rejects():
    scene = panama_scene(PANAMA_TRADE_TEXT)
    verdict = subject_fatal_verdict("web:canal1", CANAL_WEB_REASON)
    verdict["watermark"] = True
    candidate = web_candidate("Panama Canal locks aerial view")
    assert not subject_level_accepts_verdict(verdict, scene, candidate)
    verdict2 = subject_fatal_verdict("web:canal1", CANAL_WEB_REASON)
    verdict2["rejection_reason"] = "watermark_detected"
    assert not subject_level_accepts_verdict(verdict2, scene, candidate)


def test_promotional_thumbnail_web_still_rejects():
    scene = panama_scene(PANAMA_TRADE_TEXT)
    verdict = subject_fatal_verdict("web:canal1", CANAL_WEB_REASON)
    candidate = web_candidate("Panama Canal locks aerial view")
    candidate["promotional_thumbnail"] = True
    assert not subject_level_accepts_verdict(verdict, scene, candidate)


def test_terrible_quality_still_rejects():
    scene = panama_scene(PANAMA_TRADE_TEXT)
    verdict = subject_fatal_verdict("web:canal1", CANAL_WEB_REASON)
    verdict["quality"] = 5.0
    candidate = web_candidate("Panama Canal locks aerial view")
    assert not subject_level_accepts_verdict(verdict, scene, candidate)


def test_deterministic_unbound_verdict_never_accepts():
    scene = panama_scene(PANAMA_TRADE_TEXT)
    verdict = subject_fatal_verdict("web:canal1", CANAL_WEB_REASON)
    verdict["visual_checked"] = False
    candidate = web_candidate("Panama Canal locks aerial view")
    assert not subject_level_accepts_verdict(verdict, scene, candidate)


def test_low_score_vague_verdict_without_subject_proof_rejects():
    # Same category, wrong instance, vague reason, low scores, and no
    # metadata proof: the presentation label alone must not pass it.
    scene = panama_scene(PANAMA_TRADE_TEXT)
    verdict = subject_fatal_verdict(
        "web:canalx", "Not a good match for the scene.", identity=20.0, final=10.0,
    )
    candidate = web_candidate("Canal aerial view", "An aerial view of a canal.")
    assert not subject_level_accepts_verdict(verdict, scene, candidate)


def test_candidate_metadata_proves_subject():
    scene = panama_scene(PANAMA_TRADE_TEXT)
    canal = web_candidate(
        "Panama Canal locks aerial view",
        "Ships transit the Panama Canal.",
    )
    assert candidate_proves_subject(canal, scene)
    suez = web_candidate("Suez Canal transit", "A ship in the Suez Canal, Egypt.")
    assert not candidate_proves_subject(suez, scene)


# ---------------------------------------------------------------------------
# Ledger: subject fatals must not file, wrong-subject fatals still file
# ---------------------------------------------------------------------------

def test_panama_subject_fatal_does_not_file():
    scene = panama_scene(PANAMA_TRADE_TEXT)
    verdict = subject_fatal_verdict("web:canal1", CANAL_WEB_REASON)
    assert not _is_fatal_vision_verdict(verdict, scene)


def test_suez_fatal_still_files():
    scene = panama_scene(PANAMA_TRADE_TEXT)
    verdict = subject_fatal_verdict(
        "youtube:suezclip",
        "Shows the Suez Canal in Egypt — wrong location and wrong subject "
        "for the Panama Canal scene.",
        presentation="youtube_frame",
    )
    assert _is_fatal_vision_verdict(verdict, scene)


# ---------------------------------------------------------------------------
# Hard gate: canonical_entity_mismatch on subject scenes, both lanes
# ---------------------------------------------------------------------------

def test_hard_gate_panama_web_subject_passes_to_vision(tmp_path):
    image = tmp_path / "canal.jpg"
    image.write_bytes(b"fake")
    # The canonical is the abstract concept ("world trade impact") while
    # the SUBJECT comes from the film subject; the requirement's aliases
    # are empty, so the metadata gate genuinely mismatches on the
    # canonical and only the subject handoff can pass the row to vision.
    scene = panama_scene(
        PANAMA_TRADE_TEXT, canonical="world trade impact", subject_type="concept",
        film_subject="Panama Canal", strict_identity=True,
    )
    scene["visual_requirement"]["aliases"] = []
    scene["container_id"] = "ctr_test"
    candidate = web_candidate(
        "Panama Canal locks aerial view",
        "Ships transit the Panama Canal.",
        path=str(image),
    )
    candidate["container_id"] = "ctr_test"
    passed, reason = _candidate_hard_gate(scene, candidate)
    assert passed, reason
    assert candidate.get("semantic_context_pending") is True
    assert candidate.get("match_reason") == "subject_level_identity_pending_visual_required"


def test_hard_gate_suez_still_fails(tmp_path):
    image = tmp_path / "suez.jpg"
    image.write_bytes(b"fake")
    scene = panama_scene(
        PANAMA_TRADE_TEXT, canonical="world trade impact", subject_type="concept",
        film_subject="Panama Canal", strict_identity=True,
    )
    scene["visual_requirement"]["aliases"] = []
    scene["container_id"] = "ctr_test"
    candidate = web_candidate(
        "Suez Canal transit", "A ship in the Suez Canal, Egypt.",
        path=str(image),
    )
    candidate["container_id"] = "ctr_test"
    passed, _reason = _candidate_hard_gate(scene, candidate)
    assert not passed


# ---------------------------------------------------------------------------
# Live gate end-to-end (fake verifier returns the stored verdict)
# ---------------------------------------------------------------------------

def _scene_with_container(text, **kwargs):
    scene = panama_scene(text, **kwargs)
    scene["container_id"] = "ctr_test"
    scene["scene_index"] = 3
    return scene


def test_verification_result_accepts_panama_web_image(tmp_path):
    image = tmp_path / "canal.jpg"
    image.write_bytes(b"fake")
    scene = _scene_with_container(PANAMA_TRADE_TEXT)
    candidate = web_candidate(
        "Panama Canal locks aerial view",
        "Ships transit the Panama Canal.",
        path=str(image),
    )
    candidate["container_id"] = "ctr_test"

    def verifier(_scene, _candidate):
        return dict(subject_fatal_verdict("web:canal1", CANAL_WEB_REASON))

    accepted, reason, score, result = _verification_result(
        scene, candidate, verifier, scene_index=3
    )
    assert accepted, reason
    assert reason == "subject_level_accept"
    assert result.get("subject_level_accepted") is True
    assert result.get("fatal_mismatch") is False
    # Vision's score is preserved for ranking: a low score never
    # rejects, and the best-scoring accepted candidate wins.
    assert score == 40.0
    assert result.get("final_score") == 40.0


def test_verification_result_accepts_panama_yt_clip(tmp_path):
    clip = tmp_path / "canal.mp4"
    clip.write_bytes(b"fake")
    scene = _scene_with_container(PANAMA_TRADE_TEXT)
    candidate = yt_candidate(
        "canalclip", "Panama Canal: Ships Transiting the Locks",
        "Container ships transit the Panama Canal Miraflores Locks.",
        path=str(clip),
    )
    candidate["container_id"] = "ctr_test"

    def verifier(_scene, _candidate):
        return dict(subject_fatal_verdict(
            "youtube:canalclip", CANAL_YT_REASON, presentation="youtube_frame",
        ))

    accepted, reason, _score, result = _verification_result(
        scene, candidate, verifier, scene_index=3
    )
    assert accepted, reason
    assert reason == "subject_level_accept"
    assert result.get("subject_level_accepted") is True


def test_verification_result_still_rejects_suez(tmp_path):
    clip = tmp_path / "suez.mp4"
    clip.write_bytes(b"fake")
    scene = _scene_with_container(PANAMA_TRADE_TEXT)
    candidate = yt_candidate(
        "suezclip", "Suez Canal transit, Egypt",
        "A container ship transits the Suez Canal.",
        path=str(clip),
    )
    candidate["container_id"] = "ctr_test"

    def verifier(_scene, _candidate):
        return dict(subject_fatal_verdict(
            "youtube:suezclip",
            "Shows the Suez Canal in Egypt — wrong location and wrong "
            "subject for the Panama Canal scene.",
            presentation="youtube_frame",
        ))

    accepted, reason, _score, _result = _verification_result(
        scene, candidate, verifier, scene_index=3
    )
    assert not accepted
    assert reason in {"explicit_scene_conflict", "visual_rejected", "insufficient_visible_subject_match"}


def test_verification_result_still_rejects_watermarked_web(tmp_path):
    image = tmp_path / "canal.jpg"
    image.write_bytes(b"fake")
    scene = _scene_with_container(PANAMA_TRADE_TEXT)
    candidate = web_candidate(
        "Panama Canal locks aerial view",
        "Ships transit the Panama Canal.",
        path=str(image),
    )
    candidate["container_id"] = "ctr_test"

    def verifier(_scene, _candidate):
        verdict = subject_fatal_verdict("web:canal1", CANAL_WEB_REASON)
        verdict["watermark"] = True
        return verdict

    accepted, _reason, _score, _result = _verification_result(
        scene, candidate, verifier, scene_index=3
    )
    assert not accepted


# ---------------------------------------------------------------------------
# Web resolver shortlist gate (_judge_candidate): subject rescue
# ---------------------------------------------------------------------------

def _write_test_jpeg(path):
    from PIL import Image

    # The resolver's safety screen rejects tiny images as unreadable.
    Image.new("RGB", (800, 600), "blue").save(str(path), format="JPEG")


class _FakeVisionClient:
    model = "test/vision"

    def __init__(self, row):
        self._row = row

    def analyze_image(self, path, _prompt, _system_prompt=None):
        return dict(self._row)


class _FakeVisualContext:
    model = "test/vision"

    def __init__(self, client):
        self._client = client

    def get_client(self):
        return self._client


def test_resolver_shortlist_rescues_subject_image(tmp_path):
    from services.web_image.resolver import _rank_deferred_scene_shortlist

    image = tmp_path / "canal.jpg"
    _write_test_jpeg(image)
    model_row = subject_fatal_verdict("web:canal1", CANAL_WEB_REASON)
    client = _FakeVisionClient(model_row)
    result = _rank_deferred_scene_shortlist(
        scene_index=2,
        scene_text=PANAMA_TRADE_TEXT,
        subject="Panama Canal",
        global_topic=PANAMA_TITLE,
        query="Panama Canal locks aerial",
        candidates=[{
            "path": str(image),
            "title": "Panama Canal locks aerial view",
            "description": "Ships transit the Panama Canal.",
            "domain": "bing",
            "provider": "bing",
        }],
        visual_context=_FakeVisualContext(client),
    )
    assert result is not None, "subject image must survive the shortlist gate"
    assert result.get("path") == str(image)


def test_resolver_shortlist_still_rejects_wrong_subject_image(tmp_path):
    from services.web_image.resolver import _rank_deferred_scene_shortlist

    image = tmp_path / "suez.jpg"
    _write_test_jpeg(image)
    model_row = subject_fatal_verdict(
        "web:suez1",
        "Shows the Suez Canal in Egypt — wrong location and wrong subject "
        "for the Panama Canal scene.",
        identity=20.0, final=25.0,
    )
    model_row["scene_description"] = (
        "Aerial photograph of the Suez Canal with a cargo ship."
    )
    client = _FakeVisionClient(model_row)
    result = _rank_deferred_scene_shortlist(
        scene_index=2,
        scene_text=PANAMA_TRADE_TEXT,
        subject="Panama Canal",
        global_topic=PANAMA_TITLE,
        query="Panama Canal locks aerial",
        candidates=[{
            "path": str(image),
            "title": "Suez Canal transit",
            "description": "A ship in the Suez Canal, Egypt.",
            "domain": "bing",
            "provider": "bing",
        }],
        visual_context=_FakeVisualContext(client),
    )
    # Wrong-subject image must not win the shortlist as a verified pick.
    assert result is None or str(result.get("path") or "") != str(image) or result.get("visual_rejected") is True


# ---------------------------------------------------------------------------
# Adapter web visual gate (_score_verdict): low-match + conflict rescue
# ---------------------------------------------------------------------------

def test_score_verdict_rescues_low_match_subject_image():
    scene = panama_scene(PANAMA_TRADE_TEXT)
    verdict = subject_fatal_verdict(
        "web:canal1",
        "Photograph of the Panama Canal locks with a cargo ship; the "
        "trade figures in the narration are not visible — the scene's "
        "subject is entirely absent.",
        identity=20.0, final=25.0,
    )
    result = _score_verdict(scene, verdict)
    assert result.get("accepted") is True
    assert result.get("subject_level_accepted") is True
    assert result.get("accept_reason") == "subject_level_accept"


def test_score_verdict_rescues_explicit_conflict_subject_image():
    scene = panama_scene(PANAMA_TRADE_TEXT, strict_identity=True)
    verdict = subject_fatal_verdict(
        "web:canal1",
        "Photograph of the Panama Canal locks with a cargo ship; the "
        "trade figures in the narration are not visible — the scene's "
        "subject is entirely absent.",
        identity=60.0, final=55.0,
    )
    result = _score_verdict(scene, verdict)
    assert result.get("accepted") is True
    assert result.get("subject_level_accepted") is True


def test_score_verdict_still_rejects_wrong_subject_image():
    scene = panama_scene(PANAMA_TRADE_TEXT)
    verdict = subject_fatal_verdict(
        "web:suez1",
        "Shows the Suez Canal in Egypt — wrong location and wrong subject "
        "for the Panama Canal scene.",
        identity=20.0, final=25.0,
    )
    verdict["scene_description"] = (
        "Aerial photograph of the Suez Canal with a cargo ship."
    )
    result = _score_verdict(scene, verdict)
    assert result.get("accepted") is not True
