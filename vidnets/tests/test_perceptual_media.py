from __future__ import annotations

import io
import os
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.media_shot_plan import _repair_perceptual_reuse, build_media_shot_plan
from services.perceptual_media import PixelSampler, describe_frame, frames_match, samples_match


def _picture(kind=0):
    image = Image.new("RGB", (320, 180), (18, 42, 68))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20 + kind * 60, 20, 115 + kind * 60, 145), fill=(240, 185, 65))
    draw.ellipse((175 - kind * 100, 60, 285 - kind * 100, 170), fill=(80, 175, 140))
    draw.line((0, 100, 320, 20 + kind * 110), fill=(230, 230, 235), width=8)
    return image


def _save(tmp_path, name, kind=0):
    path = tmp_path / name
    _picture(kind).save(path)
    return str(path)


def _shot(path, name, **extra):
    return {"path": path, "type": "image", "media_type": "image", "candidate_id": name, "verified": True, "shot_start": 0.0, "shot_end": 2.0, "duration": 2.0, **extra}


def test_pixel_match_survives_jpeg_encoding_but_not_different_composition():
    buffer = io.BytesIO()
    _picture().save(buffer, format="JPEG", quality=65)
    original = describe_frame(_picture())
    encoded = describe_frame(Image.open(io.BytesIO(buffer.getvalue())))
    different = describe_frame(_picture(1))
    assert frames_match(original, encoded)
    assert not frames_match(original, different)
    assert describe_frame(Image.new("RGB", (320, 180), "black")) is None


def test_video_needs_two_matching_samples():
    first, second = describe_frame(_picture()), describe_frame(_picture(1))
    assert not samples_match([first], [first, second], both_video=True)
    assert samples_match([first, second], [first, second], both_video=True)
    assert samples_match([first], [first], both_video=False)


def test_distinct_files_replaced_with_verified_local_alternate_preserving_beat(tmp_path):
    left = _save(tmp_path, "source.png")
    duplicate = _save(tmp_path, "different-provider.jpg")
    alternate = _save(tmp_path, "different-view.png", 1)
    plans = [[_shot(left, "a")], [_shot(duplicate, "b", shot_start=3.0, shot_end=5.0, intro_shot=True)]]
    audit = _repair_perceptual_reuse(plans, {1: [_shot(duplicate, "b"), _shot(alternate, "c")]})
    assert audit["detected_repeat_count"] == 1
    assert audit["replaced_shot_count"] == 1
    assert audit["status"] == "pass"
    assert plans[1][0]["path"] == alternate
    assert plans[1][0]["shot_start"] == 3.0
    assert plans[1][0]["shot_end"] == 5.0
    assert plans[1][0]["intro_shot"] is True
    assert plans[1][0]["perceptual_replacement_for"] == "b"


def test_unverified_alternate_cannot_hide_unresolved_duplicate(tmp_path):
    left = _save(tmp_path, "a.png")
    right = _save(tmp_path, "b.png")
    alternate = _save(tmp_path, "unverified.png", 1)
    plans = [[_shot(left, "a")], [_shot(right, "b")]]
    audit = _repair_perceptual_reuse(plans, {1: [_shot(alternate, "c", verified=False)]})
    assert audit["status"] == "needs_revision"
    assert audit["unresolved_repeat_count"] == 1
    assert plans[1][0]["perceptual_review"]["status"] == "unresolved_repeat"
    assert plans[1][0]["path"] == right


def test_repeated_video_can_recover_to_a_verified_image(tmp_path, monkeypatch):
    first, second = tmp_path/'first.mp4', tmp_path/'second.mp4'
    first.write_bytes(b'first'); second.write_bytes(b'second')
    image=io.BytesIO(); _picture().save(image,format='PNG')
    monkeypatch.setattr('services.perceptual_media.subprocess.run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=image.getvalue()))
    alternate=_save(tmp_path,'distinct.png',1)
    plans,audit=build_media_shot_plan([
        {'path':str(first),'type':'video','candidate_id':'first'},
        {'path':str(second),'type':'video','candidate_id':'second','alternates':[{'path':alternate,'type':'image','verified':True}]},
    ],[2,2],config={'intro_media_min_shots':20})
    assert audit['perceptual_repetition_audit']['status']=='pass'
    assert audit['perceptual_repetition_audit']['replaced_shot_count']==1
    assert plans[1][0]['path']==alternate


def test_same_subject_colors_do_not_trigger_repeat(tmp_path):
    plans = [[_shot(_save(tmp_path, "a.png"), "a")], [_shot(_save(tmp_path, "b.png", 1), "b")]]
    audit = _repair_perceptual_reuse(plans, {})
    assert audit["status"] == "pass"
    assert audit["detected_repeat_count"] == 0


def test_exhausted_sampling_budget_reports_incomplete(tmp_path):
    plans = [[_shot(_save(tmp_path, "a.png"), "a")]]
    audit = _repair_perceptual_reuse(plans, {}, budget_seconds=0)
    assert audit["status"] == "incomplete"
    assert audit["unavailable_shot_count"] == 1
    assert audit["unavailable_shots"][0]["reason"] == "sampling_budget_exhausted"


def test_actual_video_window_uses_local_not_original_source_timestamp(tmp_path, monkeypatch):
    path = tmp_path / "cut.mp4"
    path.write_bytes(b"fixture")
    image = io.BytesIO()
    _picture().save(image, format="PNG")
    seeks = []

    def decode(command, **kwargs):
        seeks.append(float(command[command.index("-ss") + 1]))
        return SimpleNamespace(returncode=0, stdout=image.getvalue())

    monkeypatch.setattr("services.perceptual_media.subprocess.run", decode)
    sampler = PixelSampler()
    shot = _shot(str(path), "cut", type="video", media_type="video", clip_start=140.0, clip_end=144.0, render_clip_start=0.5)
    frames, status = sampler.sample(shot)
    assert status == "sampled" and len(frames) == 3
    assert seeks == [0.74, 1.5, 2.2]
    sampler.sample(shot)
    assert len(seeks) == 3


def test_distant_video_timestamps_with_same_pixels_are_detected(tmp_path, monkeypatch):
    first = tmp_path / "first-cut.mp4"
    second = tmp_path / "distant-cut.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"different")
    image = io.BytesIO()
    _picture().save(image, format="PNG")
    monkeypatch.setattr("services.perceptual_media.subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=image.getvalue()))
    plans = [[_shot(str(first), "a", type="video", media_type="video", clip_start=10.0, clip_end=12.0)], [_shot(str(second), "b", type="video", media_type="video", clip_start=120.0, clip_end=122.0)]]
    audit = _repair_perceptual_reuse(plans, {})
    assert audit["detected_repeat_count"] == 1
    assert audit["unresolved_repeats"][0]["matches_candidate_id"] == "a"


def test_distant_film_repeats_are_checked_beyond_three_scene_cooldown(tmp_path):
    repeated = _save(tmp_path, "repeat-a.png")
    plans = [[_shot(repeated, "first")]]
    plans.extend([[_shot(_save(tmp_path, f"unique-{i}.png", i + 1), f"unique-{i}")] for i in range(3)])
    plans.append([_shot(_save(tmp_path, "repeat-b.png"), "last")])
    audit = _repair_perceptual_reuse(plans, {})
    assert audit["cooldown_scenes"] == 4
    assert audit["detected_repeat_count"] == 1
    assert audit["unresolved_repeat_count"] == 1
    assert audit["unresolved_repeats"][0]["matches_candidate_id"] == "first"


def test_same_scene_duplicate_still_becomes_one_sustained_shot(tmp_path):
    path = _save(tmp_path, "same.png")
    plans = [[_shot(path, "first", shot_start=0, shot_end=2, duration=2),
              _shot(path, "same-again", shot_start=2, shot_end=4, duration=2)]]
    audit = _repair_perceptual_reuse(plans, {})
    assert audit["status"] == "pass"
    assert audit["removed_same_scene_duplicate_shot_count"] == 1
    assert len(plans[0]) == 1
    assert plans[0][0]["duration"] == 4
    assert plans[0][0]["shot_end"] == 4


def test_main_shot_builder_reports_pixel_duplicates_across_source_urls(tmp_path):
    first, second = _save(tmp_path, "a.png"), _save(tmp_path, "b.jpg")
    plans, summary = build_media_shot_plan([
        {**_shot(first, "a"), "source_url": "https://one.example/a"},
        {**_shot(second, "b"), "source_url": "https://two.example/b"},
    ], [2.0, 2.0], config={"intro_media_duration_seconds": 0})
    assert summary["perceptual_repetition_audit"]["unresolved_repeat_count"] == 1
    assert plans[1][0]["perceptual_review"]["status"] == "unresolved_repeat"


def test_corrupt_file_does_not_get_a_distinctness_pass(tmp_path):
    path = tmp_path / "invalid.jpg"
    path.write_bytes(b"not an image")
    audit = _repair_perceptual_reuse([[_shot(str(path), "broken")]], {})
    assert audit["status"] == "incomplete"
    assert audit["unavailable_shots"][0]["reason"] == "image_decode_failed"
