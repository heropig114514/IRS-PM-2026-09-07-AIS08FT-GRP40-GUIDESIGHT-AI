# Prepared Risk Speech Resources

## Coverage and contract

This pack contains generated Chinese (`zh-CN`) and English (`en-US`) warning audio for `person`, `bicycle`, `car`, and `motorcycle`. Each language covers `front`, `front_left`, and `front_right`; `medium`, `high`, and `critical`; and the existing approaching/non-approaching wording. The composition table contains 72 combinations per language. The pack does not classify targets, estimate motion, or change alert levels.

Audio uses the existing backend's Qwen3 realtime TTS model (`qwen3-tts-flash-realtime`), Cherry voice, and synthesis rate 2. WAV files contain mono 24 kHz signed 16-bit little-endian PCM. The rate is already encoded by synthesis; play at the declared sample rate rather than applying a second 2x speed change.

## Composition

`manifest.json` describes each clip, its text, language, role, relative path, hashes, sample count, duration, and byte length. Its `compositions` list gives the exact current warning text and ordered `clip_ids` for each covered combination. Resolve IDs through the clip table. Concatenate decoded PCM, not complete WAV containers. Do not alter words or infer a risk level from an available recording.

Chinese uses 39 clips: three comma-ended alert prefixes, 24 complete target clauses, and 12 intact medium/non-approaching notices. English uses three alert prefixes and 24 complete target clauses. Splits occur at the existing punctuation boundary; the unpunctuated Chinese notice remains one clip. This yields 66 source clips and 144 compositions without splitting individual words. No silence trimming, mixing, or resampling is applied. Two assembled preview WAVs demonstrate the same parts without adding synthesis calls.

## Reproduction and verification

From `SystemCode/frontend`, run `node tools/risk-audio-plan.mjs --output <new-plan.json>`. With the existing local TTS gateway running, use `SystemCode/backend/tools/prepare_risk_audio.py` with explicit plan, loopback WebSocket URL, new output directory, report path, and concurrency. The generator opens at most two requests, validates PCM/control identity and completion, preserves completed files on failure, and does not retry automatically. It does not read or export provider credentials.

Resource integrity is established by WAV parameters, exact text composition, PCM/file hashes, and the pack hash. Listening is still needed to assess pronunciation and joins. Preparation timing is recorded separately in an ignored structured report; it is not a detection-to-warning latency result.

## Integration boundary

The unified integration serves the manifest and source clips through `/assets/risk-audio/`, also under the mobile `/mvp/` mount. `RiskAudioResources` validates and preloads the pack; `RiskSpeechCache` resolves exact rate-2 warning text into concatenated PCM. The existing shared player and guidance manager retain current language/rate selection, original risk freshness, eligibility, priority, same-target completion, manual-output protection, and explicit stop behavior. Loading an asset does not activate browser audio or authorize an alert. Uncovered wording or rates use the existing synthesis path explicitly; pack failure is an error and does not silently fall back. Server deployment is a separate operation.
