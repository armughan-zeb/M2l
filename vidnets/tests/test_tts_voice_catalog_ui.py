from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tts_selectors_render_model_advertised_voice_catalogs_and_categories():
    renderer = (ROOT / "src" / "renderer.js").read_text(encoding="utf-8")

    assert "ttsVoices: Object.fromEntries(" in renderer
    assert ".map(model => [model.id, Array.isArray(model.voices) ? model.voices : []])" in renderer
    assert "const remoteModels = (AppState.dropdownData.ttsModels || [])" in renderer
    assert "const catalog = getVoiceCatalogForTtsModel(ttsModel);" in renderer
    assert "['character', 'Character styles']" in renderer
    assert "group.label = 'Other available voices';" in renderer
    assert "populateVoiceDropdown(context.voiceId, catalog, preferred);" in renderer
