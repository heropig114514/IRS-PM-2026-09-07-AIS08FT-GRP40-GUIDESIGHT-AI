# Fixed speech activation clips

These two clips say only “语音提醒已开启。” / “Voice alerts are enabled.”.
They were synthesized on 2026-10-29 through the project's existing Qwen TTS endpoint,
using Cherry at rate 2, and stored as 24 kHz mono signed 16-bit PCM WAV files.
No provider credentials are included.

`manifest.json` records exact text, language, rate, voice, WAV SHA-256, PCM SHA-256
and PCM byte count. `integration.prepared_speech.load_clips` validates all of these
before serving the original speech WebSocket protocol. It rejects altered clips;
it never substitutes these words for an arbitrary warning. The browser still needs
a user gesture and successful audio activation.

To regenerate, submit each exact manifest text/language with rate 2 to the existing
configured `/ws/tts/` endpoint, collect its `audio_config`, PCM chunks and `complete`
message, validate the 24 kHz mono PCM16 format and final byte count, and wrap the PCM
in WAV. Update both hashes and the PCM byte count together, then run
`integration.test_prepared_speech`. Do not commit keys or temporary deployment URLs.
