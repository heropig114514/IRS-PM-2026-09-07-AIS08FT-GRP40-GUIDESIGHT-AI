# Phone speech output

## Responsibilities and operation

The public `/mvp/maps/` entry exposes speech state and **Test / restore voice** above
navigation and detection. This button plays the configured bilingual calibration through the
same backend/Web Audio path. No camera, GPS or GPU inference is needed for this test.
Successful scheduling does not verify the physical speaker. Check media volume, Bluetooth
output and, on older iPhones, silent mode. Only algorithm-required risks are announced.

## Playback and recovery

`SpeechOutput` observes suspended/interrupted/closed contexts, stops owned obsolete PCM and
invalidates the activation gate. A new trusted click resumes or replaces a closed context,
recalibrates and clears only the pause captured by that explicit action after PCM starts.
There is no automatic retry, provider substitution, stale alert replay or changed risk threshold.

Where available, Audio Session `playback` is selected only while an utterance is owned and
restored afterward. Entry permission checks and voice capture reserve microphone-compatible
routing before requesting access and release it after capture cleanup. An explicitly selected `play-and-record` session is preserved; a newer
session change by another owner is never overwritten. Unsupported browsers retain standard
Web Audio. Playback can pause other media. Platform behavior and iPhone audibility still
require physical device confirmation. See the [Audio Session specification](https://w3c.github.io/audio-session/)
and [AudioContext interruption documentation](https://developer.mozilla.org/en-US/docs/Web/API/BaseAudioContext/state).

## Delivery checks

Inspect the actual release and safe TTS lifecycle diagnostics, reproduce the playback/control
failure, test recovery ownership and visible controls, then deploy only matching source hashes
with rollback copies. Preserve provider credentials, the GPU route and prepared risk resources.
Record executions and unresolved device checks under `benchmarks/`; do not infer phone sound
from unit tests, returned PCM or desktop playback events.

## Browser regression command

From `SystemCode`, set `PRS_PLAYWRIGHT_PATH` to an installed Playwright package and run:

```text
node frontend/tools/check_phone_speech_browser.mjs https://47.239.50.129/mvp/maps/ NEW_REPORT.json
```

Installed Chrome is required. `PRS_BROWSER_PROXY` is optional and must be selected explicitly.
The script creates a separate browser context, plays fixed Chinese/English calibration,
checks explicit recovery after context suspension/closure, and verifies prepared risk phrases
without making inference requests. It grants no sensor permissions. Risk phrases in this test
are synthetic transport checks, not model detections. A PNG accompanies the JSON report.

## Deployment and rollback

Apply only source files whose old hashes match the inspected release. Frontend files and
HTML are read per request with `no-store`; this repair needs no Web or GPU restart. Existing
open tabs need a full reload. The deployment receipt records exact old/new hashes and the
server handoff directory. Its `backup/` holds the original seven files and `source/` the repair.
For rollback, verify the current release and each live hash against that receipt, then restore
only the corresponding backed-up files, preserving ownership. Do not overwrite a later release
or colleague edits. Credentials and model weights are outside this repair.
