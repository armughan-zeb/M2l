import os
import sys
from pathlib import Path
from unittest import mock
import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from config import normalize_generation_config  # noqa: E402
from services import audio_first_pipeline  # noqa: E402
from services import openrouter_tts_service as tts  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_payload_tests_from_audio_signal_validation(monkeypatch):
    # These tests exercise provider payloads with stub ID3 bytes. Real audio
    # decode/signal validation is covered by narration-quality regression tests.
    monkeypatch.setattr('services.narration_quality.validate_narration_audio', lambda _path: None)
    monkeypatch.setattr(tts, '_duration_seconds', lambda *_args: 3.0)


def test_normalization_preserves_supported_openrouter_tts_model():
    normalized = normalize_generation_config({
        "tts_model": "fish-audio/s2.1-pro-free:free",
        "vo_mode": "Use Audiogram",
        "whisper_mode": "web",
    })

    assert normalized["tts_model"] == "fish-audio/s2.1-pro-free:free"
    assert normalized["vo_mode"] == "Use OpenRouter TTS"
    assert normalized["whisper_mode"] == "local"


def test_catalog_is_english_only_and_represents_fish_default_and_style_profiles():
    remote_payload = {
        "data": [
            {
                "id": "deepgram/flux-tts:free",
                "name": "Deepgram: Flux TTS (free)",
                "supported_voices": ["flux-alexis-en", "flux-francois-fr"],
            },
            {
                "id": "fish-audio/s2.1-pro-free:free",
                "name": "Fish Audio: S2.1 Pro Free (free)",
                "supported_voices": None,
            },
        ]
    }
    response = mock.Mock(status_code=200)
    response.json.return_value = remote_payload

    with mock.patch.object(tts.requests, "get", return_value=response):
        catalog = tts.get_openrouter_tts_catalog(force_refresh=True)

    assert not any(model["id"].startswith("deepgram/") for model in catalog)
    fish = next(model for model in catalog if model["id"] == "fish-audio/s2.1-pro-free:free")
    assert fish["voices"][0]["id"] == ""
    assert fish["voices"][0]["category"] == "provider"
    assert fish["voices"][0]["is_default"] is True
    assert {voice["id"] for voice in fish["voices"][1:]} >= {
        "fish-style-documentary",
        "fish-style-warm-explainer",
        "fish-style-news-anchor",
        "fish-style-energetic",
        "fish-style-cinematic",
        "fish-character-heroic",
        "fish-character-mysterious",
        "fish-character-playful",
    }
    assert fish["voice_catalog_note"]


def test_catalog_keeps_fish_provider_voices_and_vidnets_character_profiles_together():
    remote_payload = {
        "data": [
            {
                "id": "fish-audio/s2.1-pro-free:free",
                "supported_voices": [
                    {"id": "fish-en-sage", "label": "Sage", "language": "en"},
                    {"id": "fish-en-aria", "label": "Aria", "language": "en-US"},
                    {"id": "fish-fr-zoe", "label": "Zoé", "language": "fr"},
                ],
            },
        ]
    }
    catalog = tts._build_catalog(tts._model_rows_from_response(remote_payload))

    fish = next(model for model in catalog if model["id"] == "fish-audio/s2.1-pro-free:free")
    voices_by_id = {voice["id"]: voice for voice in fish["voices"]}
    assert {"fish-en-sage", "fish-en-aria", ""} <= set(voices_by_id)
    assert "fish-fr-zoe" not in voices_by_id
    assert voices_by_id["fish-en-sage"]["category"] == "provider"
    assert {"fish-character-heroic", "fish-character-mysterious", "fish-character-playful"} <= set(voices_by_id)


def test_removed_flux_model_cannot_make_a_paid_speech_request(monkeypatch):
    post = mock.Mock()
    monkeypatch.setattr(tts.requests, 'post', post)
    with pytest.raises(ValueError, match='Unsupported'):
        tts.generate_audio('Preview', model='deepgram/flux-tts:free', api_key='test')
    post.assert_not_called()


def test_fish_request_omits_voice_and_uses_provider_default(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(
        tts,
        "get_voice_catalog_for_model",
        lambda *_args, **_kwargs: tts._builtin_voices("fish-audio/s2.1-pro-free:free"),
    )
    response = mock.Mock(
        status_code=200,
        content=b"ID3-fish-audio",
        headers={"content-type": "audio/mpeg"},
    )
    with mock.patch.object(tts.requests, "post", return_value=response) as post:
        path, _ = tts.generate_audio(
            "This is an English narration.",
            voice="some-invalid-edge-voice",
            model="fish-audio/s2.1-pro-free:free",
            api_key="test-openrouter-key",
        )

    payload = post.call_args.kwargs["json"]
    assert payload["model"] == "fish-audio/s2.1-pro-free:free"
    assert payload["input"] == "This is an English narration."
    assert "voice" not in payload
    assert Path(path).read_bytes() == b"ID3-fish-audio"


def test_fish_provider_voice_is_forwarded_as_a_real_voice_option(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "TEMP_DIR", tmp_path)
    provider_voice = {"id": "fish-en-sage", "label": "Sage", "category": "provider"}
    monkeypatch.setattr(
        tts,
        "get_voice_catalog_for_model",
        lambda *_args, **_kwargs: [*tts._builtin_voices("fish-audio/s2.1-pro-free:free"), provider_voice],
    )
    response = mock.Mock(
        status_code=200,
        content=b"ID3-fish-provider-voice",
        headers={"content-type": "audio/mpeg"},
    )
    with mock.patch.object(tts.requests, "post", return_value=response) as post:
        tts.generate_audio(
            "The story begins in Vice City.",
            voice="fish-en-sage",
            model="fish-audio/s2.1-pro-free:free",
            api_key="test-openrouter-key",
        )

    payload = post.call_args.kwargs["json"]
    assert payload["voice"] == "fish-en-sage"
    assert payload["input"] == "The story begins in Vice City."


def test_fish_style_profile_is_sent_as_bracket_instruction_not_provider_voice(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "TEMP_DIR", tmp_path)
    response = mock.Mock(
        status_code=200,
        content=b"ID3-fish-style-audio",
        headers={"content-type": "audio/mpeg"},
    )
    with mock.patch.object(tts.requests, "post", return_value=response) as post:
        path, _ = tts.generate_audio(
            "The story begins in Vice City.",
            voice="fish-style-documentary",
            model="fish-audio/s2.1-pro-free:free",
            api_key="test-openrouter-key",
        )

    payload = post.call_args.kwargs["json"]
    assert payload["input"].startswith("[calm, authoritative documentary narrator]")
    assert "voice" not in payload
    assert Path(path).read_bytes() == b"ID3-fish-style-audio"


def test_audio_pipeline_dispatches_openrouter_model_without_edge(monkeypatch):
    with mock.patch.object(tts, "generate_audio", return_value=("openrouter.mp3", 2.0)) as generate, \
         mock.patch("services.edge_tts_service.generate_audio") as edge_generate:
        result = audio_first_pipeline._run_tts(
            "A short narration script.",
            "fish-style-documentary",
            tts_model="fish-audio/s2.1-pro-free:free",
            openrouter_api_key="test-openrouter-key",
        )

    assert result == ("openrouter.mp3", 2.0)
    generate.assert_called_once_with(
        "A short narration script.",
        "fish-style-documentary",
        model="fish-audio/s2.1-pro-free:free",
        api_key="test-openrouter-key",
        options={},
    )
    edge_generate.assert_not_called()
