import os
import sys
import unittest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from config import normalize_generation_config


class TtsProviderNormalizationTests(unittest.TestCase):
    def test_legacy_audio_settings_are_normalized(self):
        normalized = normalize_generation_config({
            "vo_mode": "Use Previous VO",
            "tts_model": "legacy-local",
            "whisper_mode": "web",
            "llm_provider": "legacy-provider",
            "groq_api_key": "gsk_test",
        })

        self.assertEqual(normalized["vo_mode"], "Use Audiogram")
        self.assertEqual(normalized["tts_model"], "audiogram")
        self.assertEqual(normalized["whisper_mode"], "local")
        self.assertEqual(normalized["llm_provider"], "openrouter")
        self.assertNotIn("groq_api_key", normalized)

    def test_missing_audio_settings_get_audiogram_defaults(self):
        normalized = normalize_generation_config({"script": "A valid script."})

        self.assertEqual(normalized["vo_mode"], "Use Audiogram")
        self.assertEqual(normalized["tts_model"], "audiogram")
        self.assertEqual(normalized["whisper_mode"], "local")


if __name__ == "__main__":
    unittest.main()
