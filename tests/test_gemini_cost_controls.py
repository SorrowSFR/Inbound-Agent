import unittest

import agent_backend


class _FakeGoogleTypes:
    class AudioTranscriptionConfig:
        pass

    class RealtimeInputConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class AutomaticActivityDetection:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class StartSensitivity:
        START_SENSITIVITY_HIGH = "high"

    class EndSensitivity:
        END_SENSITIVITY_LOW = "low"


class _FakeRealtime:
    class RealtimeModel(dict):
        def __init__(self, **kwargs):
            super().__init__(kwargs)


class _FakeGooglePlugin:
    realtime = _FakeRealtime


def _live_config(**overrides):
    config = {
        "google_api_key": "test-key",
        "gemini_live_model": "gemini-3.1-flash-live-preview",
    }
    config.update(overrides)
    return config


class GeminiCostControlsTest(unittest.TestCase):
    def setUp(self):
        self._google_plugin = agent_backend.google_plugin
        self._google_genai_types = agent_backend.google_genai_types
        agent_backend.google_plugin = _FakeGooglePlugin
        agent_backend.google_genai_types = _FakeGoogleTypes

    def tearDown(self):
        agent_backend.google_plugin = self._google_plugin
        agent_backend.google_genai_types = self._google_genai_types

    def test_transcription_flags_explicitly_disable_plugin_defaults(self):
        model = agent_backend.build_gemini_realtime_model(_live_config())

        self.assertIn("input_audio_transcription", model)
        self.assertIn("output_audio_transcription", model)
        self.assertIsNone(model["input_audio_transcription"])
        self.assertIsNone(model["output_audio_transcription"])

    def test_transcription_flags_opt_in_to_audio_transcription(self):
        model = agent_backend.build_gemini_realtime_model(
            _live_config(
                gemini_live_input_transcription_enabled=True,
                gemini_live_output_transcription_enabled=True,
            )
        )

        self.assertIsInstance(
            model["input_audio_transcription"],
            _FakeGoogleTypes.AudioTranscriptionConfig,
        )
        self.assertIsInstance(
            model["output_audio_transcription"],
            _FakeGoogleTypes.AudioTranscriptionConfig,
        )

    def test_gemini_tts_fallback_is_opt_in_for_3_1_live(self):
        self.assertFalse(agent_backend.should_use_gemini_tts_fallback(_live_config()))
        self.assertTrue(
            agent_backend.should_use_gemini_tts_fallback(
                _live_config(gemini_tts_fallback_enabled=True)
            )
        )
        self.assertFalse(
            agent_backend.should_use_gemini_tts_fallback(
                _live_config(
                    gemini_live_model="gemini-2.5-flash-native-audio-preview-12-2025",
                    gemini_tts_fallback_enabled=True,
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
