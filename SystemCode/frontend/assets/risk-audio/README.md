# Prepared Risk Speech Resources

## Coverage and contract

Version 2 contains generated Chinese (`zh-CN`) and English (`en-US`) warning audio for `person`, `bicycle`, `car`, `motorcycle`, `chair`, and the presentation term `obstacle`. Each language covers `front`, `front_left`, and `front_right`; `medium`, `high`, and `critical`; and the existing approaching/non-approaching wording. The composition table contains 108 combinations per language. The pack does not classify targets, estimate motion, or change alert levels. The prepared demo uses named covered targets and says obstacle for other classes; the result list retains the model's actual label.

Audio uses the existing backend's Qwen3 realtime TTS model (`qwen3-tts-flash-realtime`), Cherry voice, and synthesis rate 2. WAV files contain mono 24 kHz signed 16-bit little-endian PCM. The rate is already encoded by synthesis; play at the declared sample rate rather than applying a second 2x speed change.

## Composition

`manifest.json` describes each clip, its text, language, role, relative path, hashes, sample count, duration, and byte length. Its `compositions` list gives the exact current warning text and ordered `clip_ids` for each covered combination. Resolve IDs through the clip table. Concatenate decoded PCM, not complete WAV containers. Do not alter words or infer a risk level from an available recording.

Chinese uses 57 clips: three comma-ended alert prefixes, 36 complete target clauses, and 18 intact medium/non-approaching notices. English uses three alert prefixes and 36 complete target clauses. Splits occur at the existing punctuation boundary; the unpunctuated Chinese notice remains one clip. This yields 96 source clips and 216 compositions without splitting individual words. No silence trimming, mixing, or resampling is applied. The original 66 clip files and two preview WAVs are preserved byte for byte; version 2 adds 30 clips for chairs and generic obstacles. The full source PCM size is 3,975,984 bytes.

## Reproduction and verification

From `SystemCode/frontend`, run `node tools/risk-audio-plan.mjs --output <new-plan.json>`. With the existing local TTS gateway running, use `SystemCode/backend/tools/prepare_risk_audio.py` with explicit plan, loopback WebSocket URL, new output directory, report path, and concurrency. The generator opens at most two requests, validates PCM/control identity and completion, preserves completed files on failure, and does not retry automatically. It does not read or export provider credentials.

Resource integrity is established by WAV parameters, exact text composition, PCM/file hashes, and the pack hash. Listening is still needed to assess pronunciation and joins. Preparation timing is recorded separately in an ignored structured report; it is not a detection-to-warning latency result.

## Integration boundary

The unified integration serves the manifest and source clips through `/assets/risk-audio/`, also under the mobile `/mvp/` mount. `RiskAudioResources` validates and preloads the pack; `RiskSpeechCache` resolves exact rate-2 warning text into concatenated PCM. The existing shared player and guidance manager retain current language/rate selection, original risk freshness, eligibility, priority, same-target completion, manual-output protection, and explicit stop behavior. Loading an asset does not activate browser audio or authorize an alert. Uncovered wording or rates use the existing synthesis path explicitly; pack failure is an error and does not silently fall back. Server deployment is a separate operation.
