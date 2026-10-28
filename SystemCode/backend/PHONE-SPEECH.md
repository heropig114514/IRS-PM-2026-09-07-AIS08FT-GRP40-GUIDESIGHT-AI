# Phone speech output

## Responsibilities and operation

The public `/mvp/maps/` entry exposes speech state and **Test / restore voice** above
navigation and detection. This button plays the configured bilingual calibration through the
same backend/Web Audio path. No camera, GPS or GPU inference is needed for this test.
Successful scheduling does not verify the physical speaker. Check media volume, Bluetooth
output and, on older iPhones, silent mode. Only algorithm-required risks are announced.

## Detection speech eligibility

The detection result area explains the actual owner's current speech state. Object detection and
risk alerts are separate: a medium grade alone is insufficient when the server's `alert_required`
flag is false. Only required alerts with positive priority are considered. Results expire one second
after original capture, including transport and decode time; displaying an old annotated image never
refreshes that deadline. The status distinguishes stale input, no required alert, explicit pause,
preparation, playback, foreign speech and the existing three-second per-target repeat interval.
Frame age and alert counts stay outside the live announcement region. A started short risk sentence
may finish, while new speech still requires fresh confirmation. No threshold or timing policy changes.

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

## Camera latency profile

The product template explicitly selects `data-camera-profile="low-latency-1"`. It samples the
next camera frame only after the previous frame has returned and decoded. The original `standard`
profile (30 FPS ceiling, 12 admitted jobs) remains available on the diagnostic page and in controlled
comparisons. Both profiles retain the same 640-pixel maximum edge, JPEG quality 0.8, model contract,
tracking/risk rules and one-second speech deadline. Video-file replay and HF are unchanged.
Profile validation occurs before permission; an invalid name fails instead of silently substituting.

One in-flight frame prevents client-generated queues from growing on slow links. It trades throughput
for recency and cannot make a network round trip below one second. A stale status after this change
means the current route still cannot support real-time risk speech. Do not reset capture timestamps
or extend the deadline to make delayed hazards speak. Compare original and one-frame profiles on the
same route and fixture; record all frames, including first frames and failures. Physical-phone results
must be reported separately from desktop browser measurements.

## End-to-end capture comparison

With the remote worker idle, from `SystemCode` run:

```text
node frontend/tools/check_phone_risk_browser.mjs HTTPS_MAPS_URL NEW_REPORT.json deployed
node frontend/tools/check_phone_risk_browser.mjs HTTPS_MAPS_URL STANDARD_REPORT.json standard
node frontend/tools/check_phone_risk_browser.mjs HTTPS_MAPS_URL SINGLE_REPORT.json low-latency-1
```

Use the same explicit `PRS_PLAYWRIGHT_PATH` runtime as the speech regression command. Each run
opens an isolated browser, uses the checked-in public image as a synthetic camera and sends exactly
eight frames through the real remote model. It does not record personal media or fabricate risk
results. A busy worker aborts without retry. An optional final camera-source path overrides only the
test tab and records its SHA-256. The report keeps every completed frame, failures and unchanged
model/session contracts. Close phone detection during this comparison; resume testing afterward.

The speech regression additionally feeds clearly labeled synthetic required/non-required/expired
metadata into the real output owner. This isolates playback from actual model classification and
network latency; it is not proof that a real scene produced an audible warning on a phone.

## Compact phone results

`data-camera-result-transport="metadata-v1"` explicitly requests WebSocket subprotocol
`prs.metadata.v1` for the local camera route. An absent selector retains `annotated-jpeg`.
The relay verifies the worker's full PRS1 response, including its JPEG hash and exact source time,
then returns a bounded PRM1 envelope containing the unchanged model metadata, original annotation
hash, original response size and SHA-256 of the corresponding input JPEG. It never changes the
worker's request, model, thresholds or input quality. The browser verifies the retained input hash,
source time and complete model/risk contract before drawing boxes on that exact frame. The rendered
labels are client-side presentation; the image is not claimed to match the omitted server annotation
pixel for pixel. No metadata mode is silently selected or retried, and an incompatible hello fails.

The phone's one-frame profile limits retained input memory and prioritizes fresh results. Other
profiles remain bounded by the original 12-frame capacity. Legacy PRS1 clients retain their exact
wire format. Compact mode only removes the returned JPEG on the relay-to-browser leg; original
camera uploads and the relay-to-GPU link remain unchanged. Measure whether the remaining uplink
fits the one-second deadline before claiming real-time risk speech is available.
