# Temporary public phone access

## Direct laptop tunnel for a phone camera latency test

The optional `integration.tunnel_asgi:application` entry accepts the full `/mvp/` path
from a local connector, requires the exact configured `MOBILE_PUBLIC_ORIGIN` Host,
and reuses the existing mobile path/Origin restrictions. Bind it only to loopback and
disable Uvicorn proxy-header trust. Do not point a public connector at the general
software or inference ASGI entry. The GPU worker remains a separate loopback service.

This path is phone → public HTTPS connector → laptop Web → laptop GPU. It does not
require the application server, phone certificate installation, or access to a LAN IP.
It does require an explicitly started connector and a continuously running laptop.
Keep the generated hostname exact; rebuilding a temporary tunnel requires updating
the Web process's `MOBILE_PUBLIC_ORIGIN`. Do not use wildcard Host/Origin acceptance.

For an explicit image latency session, set `PRS_LATENCY_PANEL=1` on this Web process.
The explicit latency page selects `low-latency-2` and retains `metadata-v1`, original
640-pixel capture bound and JPEG quality 0.8. At most two captured, transmitted or
awaiting-display frames are admitted; the first frame completes alone. Other product
pages retain `low-latency-1`; `standard` retains its original bound. The page adds a scalar JSON
export. First-frame timing is separate from subsequent median/P95/max and completion
FPS. Capture-to-decode includes encoding, transport, inference and result decode, but
excludes sensor buffering and physical display refresh. At most the first 3000 frames
per session are retained; no images, detection labels or positions are included.

A passing desktop fixture test confirms transport and software behavior, not physical
phone permissions, wireless latency or speaker playback. Record the actual connector
edge and phone export. A public tunnel is an access option, not a promised latency gain.

The explicit latency page uses one live camera view sharing the existing capture
stream. The source-bound result snapshot is hidden. Detection runs independently;
the optional local tracker reprojects validated boxes into subsequent image coordinates.
Its checkbox is on by default on this explicit test page. A dedicated Worker samples
the preview at a ceiling of 12 Hz, longest edge 320, with one outstanding local frame.
It retains at most 28 grayscale pyramids and 2.1 seconds of local history. When a GPU
result arrives, features from its exact retained source JPEG are replayed through that
history. Each subsequent frame advances at most six boxes with twenty features each.
These frames stay in browser memory and are discarded on stop; they are not uploaded.

Detection hints and local estimates are separate visual layers. Every valid, recent
returned box is initially eligible for a dashed historical hint lasting at most
150 ms. The tracker selects at most six candidates, ordered by risk priority; its
reliable estimates replace those hints with solid labeled boxes. Other hints remain
until their own deadline, even when tracking updates or fails. Source frame index,
tracking epoch and original box index prevent duplicate boxes and cross-frame mixing.
Untracked hints omit text labels to reduce dense-scene clutter and drawing overhead.
This changes visual admission only; it does not cap the detector output at six.

Forward/backward Lucas–Kanade checks and displacement consensus reject unreliable
tracks. Sampling gaps over 220 ms, insufficient texture, lost features or orientation
changes hide tracking estimates; any still-valid historical hints remain until their
original hint expiry. The original detection's two-second deadline applies to both
layers. No lost track is extrapolated indefinitely. Labels identify estimates and
retain original detection age. Each estimate expires after at most 150 ms unless
another recent local image supports an update; redraw never extends a source hint.
Turning tracking off leaves the 150 ms historical hints for direct comparison.
Worker failure visibly disables tracking while hints, inference and speech continue.
This is a translation-only visual
tracker; deforming objects, large movements and clutter require physical-phone checks.

Tracking never writes to risk assessment, distance, classification or speech; those
consume only original GPU metadata and original capture times. JSON reports distinguish
`completion_fps` from `local_tracking.visual_updates_fps` and include local sampling
and Worker timing counters. Visual-update FPS includes detection corrections and gaps
where no box was reliable; it is not model FPS. Native preview remains independent.

Exports identify the layered policy as `box_display_policy: tiered-v1`. Each source
row contains `detected_count`, `tracking_epoch`, `box_display` and
`tracking_diagnostics`. `detected_count` is the post-filter GPU output count, not a
ground-truth count. `box_display.displayed_unique_count` counts distinct source boxes
drawn at least once; `never_displayed_count` is the difference from the detected count.
`tracked_peak_count` is the largest simultaneously drawn tracked set for that source.
`hinted_unique_count` overlaps tracked boxes and must not be added to the tracked count.
Drawing observations do not guarantee physical screen paint or model recall.

`visible_count`, `hint_count`, `tracked_count`, `hidden_count` and `hidden_reasons`
describe the last drawing observation for that source, not missed detections. In
particular, `hint_expired` means an intentionally brief hint ended, even if every
target was previously drawn. `tracking_diagnostics.excluded` separates the six-slot
budget, small boxes, insufficient features, stale sources and unavailable tracking.
`lost` counts each seeded track's first failure, including unreliable flow, sampling
gaps, inconsistent motion, movement outside the image and original-capture expiry.
Superseded pending seeds and explicit stops have separate reason codes. Late worker
callbacks cannot redraw or overwrite diagnostics for a newer source/epoch. Count
fields store no images, class names, target identities or coordinates. Use them to
distinguish display policy from model output before proposing model changes.

The vendored JSFeat build comes from the official repository at commit
`4c7b336bbeeb26e6cd4cdf3c7d414abe273846f3`:
[JSFeat source](https://github.com/inspirit/jsfeat/tree/4c7b336bbeeb26e6cd4cdf3c7d414abe273846f3).
`frontend/integration/jsfeat.min.js` SHA-256 is
`974327515d68c91258efd3befa03622c41bbed8e3172771994604325799977a1`.
Its original MIT license is retained alongside it as `jsfeat-LICENSE.txt`, SHA-256
`20c20cb25e05181e3b6c7d68814a3484858422d6fb3745a3a75787256d30cc72`.
Both are vendored, without a runtime CDN dependency. CSP permits only same-origin workers.

Use the original single-slot profile and disable local tracking when reproducing old
measurements. Concurrent requests can raise refresh frequency without reducing any
individual network round trip. Never present local reprojection as a measured network
speedup or a new model prediction. Keep comparison fixtures, transport, model and image
quality constant; lower JPEG quality and three-frame feedback pacing were investigated
but are not active in this version.

For the phone DEMO approved on 2026-10-29, set these variables on the Web process:

```text
PRS_LATENCY_PANEL=1
PRS_RISK_TTL_MS=2000
PRS_PREPARED_SPEECH=1
```

`PRS_RISK_TTL_MS` accepts only 1000 (default) or 2000. It controls freshness from
original capture to the start of a warning, including audio preparation; it is not
a promise that every result arrives within two seconds. New warnings from older
results are suppressed. Already-started speech may finish its sentence. Track/session
ownership, latest semantic confirmation, priority and cooldown checks remain active.

Prepared speech mode serves the two verified startup clips locally and preloads the
96 bilingual risk clips before enabling detection (about 4 MB). The visible voice
test button preserves the original user-gesture audio activation. With the explicit
live-overlay prepared-speech page, risks speak the grade, direction, named target
(person, bicycle, car, motorcycle or chair) and confirmed approach where applicable.
Other classes are described as obstacles with their original direction and grade;
their exact labels and distances remain in the result list. This bounded vocabulary
avoids cloud synthesis on the current model's three forward camera directions.
Model alert flags, priorities, freshness, target cooldowns and activation requirements stay intact.
Other pages retain detailed speech. Their common person, bicycle, car and motorcycle
alerts use prepared compositions; uncovered detailed labels/rates use the existing
TTS path and bounded in-memory phrase cache.
If this laptop has no provider configuration, an operator may explicitly set
`PRS_TTS_UPSTREAM_ORIGIN=https://<project-speech-host>` to use the project's existing
guest `/mvp/ws/tts/` endpoint. This sends uncovered speech text to that service; it
does not send camera frames or copy provider credentials. The origin must be exact
and must differ from this local entry to avoid a relay loop. A temporary upstream
hostname must be refreshed when that service is recreated. Without this variable,
the original local TTS backend is used. A first uncached phrase may need preparation
and another fresh detection before it can play; this is not a two-second TTS SLA.

Desktop Chrome with a synthetic camera verified real GPU results, one advancing
preview, camera release on stop, real prepared PCM scheduling for a 1.5-second-old
risk without a synthesis request, and suppression of a 2.1-second-old risk. Synthetic
risk fixtures verify software paths only; physical phone audibility remains a
separate check.

One-slot capture waits for the pending result before arming any remaining cadence
timer. With a network round trip longer than one frame interval, the next capture
can proceed immediately after completion. This avoids an independently throttled
short timer blocking an otherwise free slot. Faster routes still obey the cadence
ceiling; the standard multi-frame profile is unchanged. This does not prove that
all observed phone idle gaps are caused by browser timer throttling.

Overlay timers must call the Window timer with its global receiver, not as a method
of the overlay instance. An unbound native timer caused `Illegal invocation` after
drawing, leaving boxes visible and aborting telemetry for those frames. Exports made
before this fix may omit frames with boxes: their completion FPS and inter-sample
gaps cannot establish true capture stalls. The current collector records each frame
before the optional overlay, keeps original frame indices, and counts overlay errors.
Browser verification must check real timer expiry, observer errors and equality
between received and exported frame counts; a screenshot alone is insufficient.

Latency exports include capture-to-capture idle gaps, admission/pacing observations,
frame-listener delay, previous frame's complete synchronous callback cost, page
visibility, per-frame alert counts and speech eligibility,
and the latest 300 content-free speech transitions. Warning starts have a separate
session counter. These fields distinguish stale/no-alert/paused/preparing/cooldown
states without saving object labels, media or location. Timing and warning eligibility
must be checked on the physical phone; desktop playback does not prove audibility.

Read-only route inspection found a Windows system proxy at 127.0.0.1:7897 and an
active aTrust virtual adapter. The connector's observed TCP socket was directly from
192.168.10.8 to Cloudflare port 7844 using the Wi-Fi route; proxy environment variables
were removed from its launch environment. The observed LAX edge alone cannot establish
that the system proxy caused the detour. Transparent VPN interception remains possible;
no VPN settings were changed and no VPN-off comparison was performed.

### Phone measurement on 2026-10-29

The completed phone export (`prs-live-latency-v1`, `low-latency-1`, `metadata-v1`)
contained 108 frames over 167.845 seconds from first to last result: 0.6375 completed
results/s. Subsequent-frame capture-to-decode median was 1433 ms, P95 2355.2 ms,
maximum 3482 ms. Worker processing median was 46.25 ms, P95 202.11 ms; capture/JPEG
encoding median was 11 ms and result decoding median 21 ms. Worker time represented
4.78% of total steady-state elapsed frame work, while transport minus worker time
represented 93.10%. That residual includes network, connector and Web overhead; it
is not a one-way network measurement. The exported summary was independently
recomputed and matched, and the timing decomposition error was zero.

105 of 108 results were already over one second old when received. The connector
registered at `lax01`; a temporary QUIC trial registered at `lax08`. A 12-pair
desktop echo comparison at 70,190 bytes measured median 2021 ms for the existing
HTTP/2 route and 2994 ms for the QUIC trial. The QUIC trial was stopped and was not
deployed. Echo returns the entire payload, unlike compact detection results, and
the two connector endpoints also differ, so this does not isolate protocol effects.
The camera preview improvement changes usability, not these measured inference
latencies. Faster detection requires improving the route/deployment, then measuring
again; these results do not establish a low-latency public demo.

## Existing application-server tunnel: plan and boundaries

1. Verify original direct TCP reachability separately from proxy reachability, server listeners,
   certificate validity, host firewall and current Web/GPU health.
2. Stage a checksum-pinned Cloudflare client on the existing application server. Use an explicit
   temporary HTTPS tunnel to a dedicated loopback-only guest bridge; preserve the original entry,
   credentials, backend processes and colleague GPU connection.
3. Admit one exact tunnel Host and matching Origin, retain the original public path allowlist,
   strip credentials and translate only trusted authority/CSP edges. Test forbidden paths,
   foreign origins, WebSockets and speech before publishing the new URL.
4. Verify direct access without the local proxy and request a phone confirmation. Record the
   temporary hostname, checks and teardown procedure in a structured deployment report.

## Operation and constraints

This is an explicitly selected demonstration route through Cloudflare, not an automatic fallback.
The tunnel runs on the application server; the user's laptop is not part of its runtime. Only the
existing guest page, its approved assets/APIs and five public WebSocket routes are reachable.
Provider credentials and GPU/MCP/admin listeners remain private. Browser traffic traverses
Cloudflare; the extra hop may change latency and must not be presented as a measured speed gain.

Quick Tunnels have no uptime guarantee, support at most 200 in-flight requests and change hostname
when recreated. See [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
For a lasting service, use an account-owned stable domain/tunnel and resolve the original public
IP connectivity with the cloud/network operator. The original IP fault is not repaired by a new URL.

The dedicated systemd unit has no automatic restart and the connector disables automatic update
and connection retries. Stop only `prs-phone-access.service` to close the temporary entrance.
Remove only `/etc/nginx/conf.d/prs-phone-access.conf` after verifying ownership, then test and reload
Nginx. Do not stop the existing Web service or colleague GPU tunnel. An archived binary may remain
for reproducibility; no tunnel credentials or SSH passwords belong in repository files.

## Review and activation

Render an approved hostname without writing system configuration:

```text
python tools/render_phone_access.py generated-phone-example.trycloudflare.com
```

The pinned connector for this delivery is `cloudflared 2026.10.5`, official Linux amd64 SHA-256
`e1bc0cf084fd9e590554fe769a86b1db46701c3f847a43d902f7b974a4e16298`.
Verify against the [official release](https://github.com/cloudflare/cloudflared/releases/tag/2026.10.5)
before execution. The connector uses one HTTP/2 connection to the edge, an explicit loopback
origin `http://127.0.0.1:18770`, `--retries 0` and `--no-autoupdate`. Its systemd service uses a
private dynamic user, read-only system protection, a private temporary directory, a 192 MiB
memory cap and no automatic restart. Metrics listen only on `127.0.0.1:18772`.

Install the rendered configuration only at the named dedicated Nginx path after reviewing the
exact generated hostname. Run `nginx -t` before reloading Nginx. On validation failure, remove
only the newly owned candidate; do not alter global map-hash settings or the existing virtual host.
Test allowed pages, CSP, wrong Host/Origin, forbidden MCP/admin paths, direct TLS, WebSocket echo
and real speech. The original application and GPU services need no restart. Deployment receipts
under `benchmarks/2026-10-28/phone-public-access/` retain the concrete instance and results.
