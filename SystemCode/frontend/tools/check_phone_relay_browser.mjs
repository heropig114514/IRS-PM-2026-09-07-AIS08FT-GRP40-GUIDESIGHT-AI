/* Responsibilities: Verify the public guest diagnostic relay with real Chrome/ORS/local GPU transport.
 * Implementation: Strict TLS, native fake-video capture and explicit
 * synthetic NUS watch callbacks; verify default-on controls without startup acquisition/output,
 * then a genuine start-click/backend-PCM activation before automatic guidance.
 * Related Modules: integration/assist-mvp.js, camera.js, navigation-ui.js, ai-control.js, speech-output.js;
 * SystemCode/backend/integration/assist.py and the separately deployed /mvp/ reverse proxy.
 * Declaration Index: requireGate — apply fixed acceptance failures; validateArguments — pin public target and outputs;
 * requireNew — prohibit artifact overwrite; safeFailure — redact exceptions; classifyRequest — constrain resources;
 * verifyModelEvidence — correlate genuine frames and model sockets; verifySpeechActivation — validate first PCM start;
 * verifyTtsStreamEvidence — correlate server stream IDs with playback; guardMedia — wrap native media and WebSocket boundaries;
 * guardMedia.checkedVideo — preserve native video while rejecting microphone access;
 * guardMedia.observedWebSocket — observe trusted speech gestures;
 * guardMedia.observedWebSocket.send — correlate synthesis UUIDs with playback IDs;
 * installGPS — inject labeled synthetic sensor callbacks; installGPS.emit — deliver one owned fix;
 * installGPS.describe — report fixture counters; installGPS.watchPosition — register one synthetic watch;
 * installGPS.clearWatch — release that watch; installGPS.getCurrentPosition — reject unexpected one-shot GPS;
 * observe — load canonical controllers and evidence listeners; observe.recordFrame — retain frame metadata;
 * observe.recordGuidance — correlate owned guidance with request policy; observe.recordAudio — retain output lifecycle;
 * observe.recordNavigation — retain navigation state; navigationEvidence — read controller state;
 * cleanupEvidence — read released resources; waitNode — poll one bounded predicate;
 * monitorSocket — observe approved public WebSocket metadata; monitorSocket.received — retain safe server events;
 * monitorSocket.sent — retain PRF2 headers; selectSpeech — validate configured backend voice;
 * planNavigation — exercise real public ORS; startCamera — select the configured fake scene source;
 * waitCameraFrames — await verified model results; waitModelAlert — await a correlated real-model alert utterance;
 * waitCapacity — verify drained sessions;
 * stopAll — release browser resources; syntheticSpeech — exercise isolated risk speech;
 * manualBilingualTts — test explicit Chinese and English backend playback;
 * run — execute public relay acceptance; main — validate CLI and write the evidence report.
 * Variable Index: PHONE_ORIGIN/PREFIX — explicitly approved deployment target; NUS — synthetic GPS,
 * not a phone measurement; report — body-free evidence without credentials, pixels or positions;
 * ttsSubmissions — request-ID correlation metadata without utterance text;
 * PRS_FAKE_CAMERA_VIDEO — optional caller-supplied local Y4M scene fixture; requireModelAlert — explicit strict-mode flag.
 * Constraints: Chrome with a phone viewport is not Safari/iPhone validation. Never disable TLS,
 * start services, download fixtures, use a physical camera/microphone, mock ORS/models, retry or commit.
 */
import {createRequire} from "node:module";
import {access, mkdir, stat, writeFile} from "node:fs/promises";
import {dirname, resolve} from "node:path";
import {pathToFileURL} from "node:url";

const PHONE_ORIGIN = "https://47.239.50.129", PREFIX = "/mvp/";
const NUS = {longitude: 103.7766, latitude: 1.2971, accuracy: 5};

/* Functionality: Fail one explicit acceptance gate without interpolating private values.
 * Inputs: Boolean and fixed code. Outputs: void or coded Error. Logic: fixed public diagnostic only.
 * Constraints: callers must not supply credential, body, location or browser error messages. */
export function requireGate(condition, code) {
  if (!condition) throw Object.assign(new Error(code), {code});
}

/* Functionality: Validate the exact public HTTPS relay and new JSON report arguments.
 * Inputs: CLI URL/report pair and runtime environment. Outputs: normalized URL and report/runtime paths.
 * Logic: pin origin/mount; no userinfo/query/fragment or credential-file input; accept only the documented
 * optional model-alert flag.
 * Constraints: no implicit local target, alternate server, TLS bypass or browser installation; an optional
 * camera scene must be an existing caller-supplied Y4M file. */
export function validateArguments(argv, environment) {
  const [url, output, ...extra] = argv;
  requireGate(Boolean(url && output && extra.length <= 1 &&
    (!extra.length || extra[0] === "--require-model-alert") && environment.PRS_PLAYWRIGHT_PATH), "INVALID_ARGUMENTS");
  let address;
  try { address = new URL(url); } catch { requireGate(false, "INVALID_PHONE_URL"); }
  requireGate(address.origin === PHONE_ORIGIN && address.pathname === `${PREFIX}maps/test` &&
    !address.username && !address.password && !address.search && !address.hash, "INVALID_PHONE_URL");
  const report = resolve(output);
  requireGate(report.toLowerCase().endsWith(".json"), "INVALID_REPORT_PATH");
  return {url: address.href, output: report,
    screenshot: report.slice(0, -5) + ".png", runtime: resolve(environment.PRS_PLAYWRIGHT_PATH),
    fakeCameraFile: environment.PRS_FAKE_CAMERA_VIDEO ? resolve(environment.PRS_FAKE_CAMERA_VIDEO) : null,
    requireModelAlert: extra[0] === "--require-model-alert"};
}

/* Functionality: Preserve existing evidence and private configuration.
 * Inputs: intended artifact path. Outputs: void when absent. Logic: distinguish ENOENT only.
 * Constraints: eventual writes also use exclusive creation; no deletion or overwrite. */
export async function requireNew(path) {
  try { await access(path); } catch (error) { if (error.code === "ENOENT") return; throw error; }
  requireGate(false, "REPORT_ALREADY_EXISTS");
}

/* Functionality: Redact browser/library failures by construction.
 * Inputs: Error and current fixed phase. Outputs: code/type/phase without message or stack.
 * Logic: whitelist diagnostic syntax; Constraints: never include exception bodies or credential paths. */
export function safeFailure(error, phase) {
  return {phase, code: /^[A-Z][A-Z_0-9]{0,79}$/.test(error?.code) ? error.code : "EXECUTION_FAILED",
    type: /^[A-Za-z][A-Za-z0-9]{0,60}$/.test(error?.name) ? error.name : "Error"};
}

/* Functionality: Classify real page dependencies and detect a mount escape.
 * Inputs: request URL and optional resource type. Outputs: safe origin/path/class/allowed.
 * Logic: own resources stay under /mvp/; only original pinned Leaflet and OSM may be external.
 * Constraints: never store headers, request bodies or URL queries, and never replace a dependency. */
export function classifyRequest(url, resourceType = null) {
  const value = new URL(url);
  if (["blob:", "data:"].includes(value.protocol)) return null;
  const own = value.origin === PHONE_ORIGIN;
  const canonical = !["script", "stylesheet"].includes(resourceType) || value.pathname.startsWith(`${PREFIX}assets/`);
  const allowed = value.protocol === "https:" && (own ? value.pathname.startsWith(PREFIX) && canonical :
    value.origin === "https://unpkg.com" && ["/leaflet@1.9.4/dist/leaflet.js", "/leaflet@1.9.4/dist/leaflet.css"].includes(value.pathname) ||
    value.origin === "https://tile.openstreetmap.org" && /^\/\d+\/\d+\/\d+\.png$/.test(value.pathname));
  return {origin: value.origin, path: value.pathname, scope: own ? "relay" : "dependency", allowed};
}

/* Functionality: Correlate real validated model frames with public WSS PRF2 capture headers.
 * Inputs: saved frames and actual model socket evidence. Outputs: per-session frame counts.
 * Logic: require two explicit sessions, >=3 frames each, original FP32/depth/tracker/risk identity.
 * Constraints: fake camera pixels test transport/pipeline only; no physical camera or detection-accuracy claim. */
export function verifyModelEvidence(frames, sockets) {
  requireGate(sockets.length === 2 && sockets.every(socket => socket.secure && socket.hellos.length === 1), "MODEL_SESSION_COUNT");
  requireGate(frames.every(frame => sockets.some(socket => socket.hellos[0].session_id === frame.session_id)), "FOREIGN_MODEL_FRAME");
  const sessions = [];
  for (const socket of sockets) {
    const hello = socket.hellos[0], matched = frames.filter(frame => frame.session_id === hello.session_id);
    requireGate(hello.provider === "local" && hello.protocol_version === 2 && hello.session_seconds === null &&
      hello.model === "yolo26n" && hello.precision === "FP32" && hello.output_precision === "FP32" &&
      hello.backend === "PyTorch resident CUDA" && hello.classes === 80 && hello.confidence_threshold === 0.25 &&
      hello.max_edge === 640 && hello.jpeg_quality === 0.8 && /^[a-f0-9]{32}$/.test(hello.session_id) &&
      matched.length >= 3, "MODEL_SESSION_CONTRACT");
    requireGate(matched.every((frame, ordinal) => frame.index === ordinal && frame.provider === "local" &&
      frame.model === "yolo26n" && frame.precision === "FP32" && frame.output_precision === "FP32" &&
      frame.backend === "PyTorch resident CUDA" && /^[a-f0-9]{64}$/.test(frame.source_sha256) &&
      frame.source_sha256 === hello.source_sha256 &&
      frame.timestamp_source === "browser_capture" && Number.isFinite(frame.captured_at_ms) &&
      Math.abs(frame.capture_time_s * 1000 - frame.captured_at_ms) < 0.000001 &&
      Number.isSafeInteger(frame.tracking_epoch) && frame.tracking_epoch >= 0 &&
      frame.tracking_epoch === matched[0].tracking_epoch && frame.source_sha256 === matched[0].source_sha256 &&
      frame.tracking_status === "active" && frame.tracking_model === "botsort" &&
      frame.depth_model === "yolo26n-depth" && frame.depth_units === "m" && frame.risk_model === "rules-v1" &&
      Number.isFinite(frame.capture_to_decoded_ms) && frame.capture_to_decoded_ms >= 0 &&
      socket.inputs.some(input => input.index === frame.index && input.capture_time_s === frame.capture_time_s)), "MODEL_FRAME_IDENTITY");
    requireGate(socket.inputs.every((input, index) => input.index === index && Number.isFinite(input.capture_time_s) &&
      input.capture_time_s >= 0 && (index === 0 || input.capture_time_s > socket.inputs[index - 1].capture_time_s)), "PRF2_SOURCE_CLOCK");
    sessions.push({session_id: hello.session_id, frames: matched.length, first_frame_ms: matched[0].capture_to_decoded_ms});
  }
  requireGate(sessions[0].session_id !== sessions[1].session_id, "MODEL_SESSION_REUSED");
  requireGate(frames.find(frame => frame.session_id === sessions[1].session_id).tracking_epoch >
    frames.find(frame => frame.session_id === sessions[0].session_id).tracking_epoch, "TRACKER_NOT_RESET");
  return sessions;
}

/* Functionality: Verify first-click activation from an actual backend PCM playback start.
 * Inputs: body-free activation/call/audio lifecycle snapshot. Outputs: verified request ID.
 * Logic: require ready only after the real shared driver emits start for the owned request and the
 * same gesture creates its TTS socket. Constraints: no manual bypass, fabricated start or audibility claim. */
export function verifySpeechActivation(evidence) {
  const id = evidence.activation?.request_id;
  requireGate(evidence.activation?.ready === true && Number.isSafeInteger(id) && id > 0,
    "SPEECH_ACTIVATION_NOT_READY");
  requireGate(evidence.calls?.[0]?.user_gesture_active === true, "SPEECH_ACTIVATION_NOT_GESTURE");
  const audio = evidence.audio ?? [];
  requireGate(audio.filter(event => event.phase === "start" && event.request_id === id).length === 1,
    "SPEECH_ACTIVATION_NO_AUDIO_START");
  return {audio_start_verified: true, request_id: id, user_gesture_active: true,
    source: "original_user_gesture_backend_stream", physical_audibility_verified: false};
}

/* Functionality: Correlate real backend TTS streams with browser playback request identities.
 * Inputs: Secure WebSocket observations, audio lifecycle events and submitted synthesis IDs.
 * Outputs: Counts and fixed PCM format for completed, interrupted and pre-provider-canceled streams.
 * Logic: Validate each playback against one submitted UUID and one matching 24 kHz mono PCM
 * configuration; require completion after normal end and accept an incomplete provider stream only
 * when the matching browser request has an explicit cancel event.
 * Constraints: A browser start proves playback scheduling only, not physical speaker audibility. */
export function verifyTtsStreamEvidence(sockets, audioEvents, submissions) {
  const requestIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  requireGate(sockets.length >= 4 && sockets.every(socket => socket.secure), "BACKEND_TTS_STREAM_INVALID");
  const submittedByUuid = new Map(), submittedByAudioId = new Map();
  for (const submission of submissions) {
    requireGate(requestIdPattern.test(submission.request_id) && Number.isSafeInteger(submission.audio_request_id) &&
      submission.audio_request_id > 0 && !submittedByUuid.has(submission.request_id) &&
      !submittedByAudioId.has(submission.audio_request_id), "BACKEND_TTS_STREAM_INVALID");
    submittedByUuid.set(submission.request_id, submission);
    submittedByAudioId.set(submission.audio_request_id, submission);
  }

  const streams = new Map();
  for (const socket of sockets) {
    for (const event of socket.tts) {
      requireGate(["audio_config", "complete", "error"].includes(event.type) &&
        requestIdPattern.test(event.request_id) && submittedByUuid.has(event.request_id) &&
        event.type !== "error", "BACKEND_TTS_STREAM_INVALID");
      const stream = streams.get(event.request_id) ?? {configs: [], completions: []};
      if (event.type === "audio_config") stream.configs.push(event);
      if (event.type === "complete") stream.completions.push(event);
      streams.set(event.request_id, stream);
    }
  }
  const canceledIds = new Set(audioEvents.filter(event => event.phase === "cancel" &&
    Number.isSafeInteger(event.request_id)).map(event => event.request_id));
  const audioIdByUuid = new Map([...submittedByUuid].map(([id, submission]) => [id, submission.audio_request_id]));
  let completedStreams = 0, interruptedStreams = 0;
  for (const [id, stream] of streams) {
    const validConfig = stream.configs.length === 1 && stream.configs[0].sample_rate === 24000 &&
      stream.configs[0].format === "pcm_s16le_mono" && submittedByUuid.has(id);
    const completed = stream.completions.length === 1 &&
      Number.isSafeInteger(stream.completions[0].audio_bytes) && stream.completions[0].audio_bytes > 0;
    const interrupted = stream.completions.length === 0 && canceledIds.has(audioIdByUuid.get(id));
    requireGate(validConfig && (completed || interrupted), "BACKEND_TTS_STREAM_INVALID");
    if (completed) completedStreams++;
    else interruptedStreams++;
  }

  const started = audioEvents.filter(event => event.phase === "start");
  const startedIds = new Set();
  for (const event of started) {
    requireGate(Number.isSafeInteger(event.request_id) && event.request_id > 0 &&
      !startedIds.has(event.request_id), "BACKEND_TTS_STREAM_INVALID");
    startedIds.add(event.request_id);
    const submission = submittedByAudioId.get(event.request_id);
    requireGate(submission && streams.has(submission.request_id) &&
      (streams.get(submission.request_id).completions.length === 1 || canceledIds.has(event.request_id)),
    "BACKEND_TTS_STREAM_INVALID");
  }
  for (const event of audioEvents.filter(item => item.phase === "end")) {
    const submission = submittedByAudioId.get(event.request_id);
    requireGate(submission && streams.get(submission.request_id)?.completions.length === 1,
      "BACKEND_TTS_STREAM_INVALID");
  }
  requireGate(startedIds.size >= 4, "BACKEND_TTS_STREAM_INVALID");
  return {secure_sockets: sockets.length, started_streams: startedIds.size,
    completed_streams: completedStreams, interrupted_streams: interruptedStreams,
    cancelled_before_provider_response: sockets.filter(socket => socket.tts.length === 0).length,
    sample_rate: 24000, format: "pcm_s16le_mono", physical_audibility_verified: false};
}

/* Functionality: Observe native video tracks while preventing any microphone request before it occurs.
 * Inputs: pre-module Chrome page. Outputs: body-free native calls and retained track cleanup handles.
 * Logic: delegate all video requests unchanged to Chrome's explicit fake-video launch device.
 * Constraints: no physical camera fixture, substituted media stream, audio permission or retry. */
function guardMedia() {
  const native = navigator.mediaDevices?.getUserMedia?.bind(navigator.mediaDevices);
  window.__phoneMedia = {calls: [], tracks: []};
  window.__phoneSpeechCalls = [];
  const NativeWebSocket = window.WebSocket;
  /* Functionality: Observe backend speech requests without retaining utterance bodies in reports.
   * Inputs: same-origin socket URL and optional protocols. Outputs: original WebSocket instance.
   * Logic: record the trusted gesture; keep command text transiently in memory until the lifecycle
   * observer compares it with expected guidance, and retain UUID-to-playback-ID metadata for TTS checks.
   * Constraints: never export request text. */
  function observedWebSocket(url, protocols) {
    const socket = protocols === undefined ? new NativeWebSocket(url) : new NativeWebSocket(url, protocols);
    const address = new URL(String(url), location.href);
    if (address.pathname === "/mvp/ws/tts/") {
      window.__phoneSpeechCalls.push({at: performance.now(), user_gesture_active: navigator.userActivation?.isActive === true,
        secure: address.protocol === "wss:"});
      const send = socket.send.bind(socket);
      /* Functionality: Correlate one transmitted synthesis request without changing its payload.
       * Inputs: Original WebSocket data. Outputs: Same data sent once.
       * Logic: Retain only UUID, current playback ID, language and rate; text stays in the existing
       * transient matcher for its policy comparison. Constraints: Never export utterance text. */
      socket.send = data => {
        if (typeof data === "string" && window.__phoneProbe) {
          try {
            const command = JSON.parse(data);
            if (command.type === "synthesize") {
              const audioRequestId = window.__phoneControllers?.driver.current?.id ?? null;
              window.__phoneProbe.synthesisRequests.push({request_id: command.request_id,
                text: command.text, language: command.language, rate: command.rate});
              window.__phoneProbe.ttsSubmissions.push({request_id: command.request_id,
                audio_request_id: audioRequestId, language: command.language, rate: command.rate});
            }
          } catch { window.__phoneProbe.synthesisRequests.push({invalid_command: true}); }
        }
        return send(data);
      };
    }
    return socket;
  }
  observedWebSocket.prototype = NativeWebSocket.prototype;
  Object.setPrototypeOf(observedWebSocket, NativeWebSocket);
  window.WebSocket = observedWebSocket;
  if (!native) return;
  /* Functionality: Reject microphone acquisition and observe the unmodified native video request.
   * Inputs: production media constraints. Outputs: original native stream.
   * Logic: check before invoking the native API; Constraints: never generate or replace video pixels. */
  async function checkedVideo(constraints) {
    if (constraints?.audio !== undefined && constraints.audio !== false) throw new Error("MICROPHONE_FORBIDDEN");
    window.__phoneMedia.calls.push({audio_false: constraints.audio === false,
      exact_device: typeof constraints.video?.deviceId?.exact === "string", video: Boolean(constraints.video)});
    const stream = await native(constraints);
    window.__phoneMedia.tracks.push(...stream.getTracks());
    return stream;
  }
  navigator.mediaDevices.getUserMedia = checkedVideo;
}

/* Functionality: Supply explicitly synthetic GPS callbacks at the native-sensor boundary.
 * Inputs: fixed NUS point before production modules initialize. Outputs: one owned watch and counters.
 * Logic: fresh Date.now timestamps at registration and every2s avoid CDP override's transient code2.
 * Constraints: not native/phone GPS; no controller, route, model or browser clock replacement. */
export function installGPS(point) {
  const nativeSupported = typeof navigator.geolocation?.watchPosition === "function";
  const watches = []; let fixes = 0, oneShots = 0, maxActive = 0;
  /* Functionality: Deliver one fresh explicit sensor fixture to its current owner.
   * Inputs: watch ID. Outputs: original success callback only while owned.
   * Logic: actual epoch time and fixed sensor fixture; Constraints: no production-state mutation. */
  function emit(id) {
    const watch = watches[id]; if (!watch?.active) return;
    fixes++; watch.success({coords: {...point}, timestamp: Date.now()});
  }
  /* Functionality: Describe fixture ownership without recording coordinates or callbacks.
   * Inputs: fixture state. Outputs: registration/fix/cleanup counts and native API presence.
   * Logic: explicit counters; Constraints: no physical positioning success claim. */
  function describe() {
    return {source: "explicit_synthetic_watchPosition_NUS_Date_now", native_api_present: nativeSupported,
      watch_count: watches.length, active_watch_count: watches.filter(item => item.active).length,
      max_active_watch_count: maxActive, fixes, one_shot_requests: oneShots,
      cleared_watch_count: watches.filter(item => !item.active).length,
      emitter_active: watches.some(item => item.timer !== null)};
  }
  /* Functionality: Register the original controller's one explicit location subscription.
   * Inputs: original success callback. Outputs: unique watch ID and fresh fixture callbacks.
   * Logic: defer first fix until native-style registration returns, then repeat every2s.
   * Constraints: reject a concurrent owner, no automatic controller restart or native GPS request. */
  function watchPosition(success) {
    if (watches.some(item => item.active)) throw new Error("CONCURRENT_GPS_OWNER");
    const id = watches.length, watch = {active: true, success, timer: null}; watches.push(watch);
    maxActive = Math.max(maxActive, watches.filter(item => item.active).length);
    watch.timer = setInterval(() => emit(id), 2000); queueMicrotask(() => emit(id)); return id;
  }
  /* Functionality: Release only the original controller's retained sensor fixture.
   * Inputs: active watch ID. Outputs: inactive callback/timer ownership.
   * Logic: reject unknown/double releases; Constraints: no forced production cleanup. */
  function clearWatch(id) {
    if (!watches[id]?.active) throw new Error("UNKNOWN_GPS_OWNER");
    watches[id].active = false; clearInterval(watches[id].timer); watches[id].timer = null;
  }
  /* Functionality: Reject unintended one-shot sensor acquisition in the explicit example route test.
   * Inputs: None. Outputs: fixed failure and counter. Logic: no replacement one-shot fix.
   * Constraints: no fallback position or undisclosed sensor use. */
  function getCurrentPosition() { oneShots++; throw new Error("UNEXPECTED_GPS_ONESHOT"); }
  window.__phoneGPS = {describe};
  Object.defineProperty(navigator, "geolocation", {configurable: true,
    value: {watchPosition, clearWatch, getCurrentPosition}});
}

/* Functionality: Observe canonical production singletons using only the current /mvp/ module base.
 * Inputs: initialized relay page. Outputs: identity/security fields and in-memory safe observations.
 * Logic: original model events and backend audio events are observed without changing controllers.
 * Constraints: no absolute /assets alias, mock server/model, route installation or captured pixels. */
async function observe() {
  const asset = new URL("../assets/", location.href);
  const glue = await import(new URL("integration/assist-mvp.js", asset)); await glue.assistReady;
  const {cameraApp} = await import(new URL("camera.js", asset));
  const {navigation} = await import(new URL("navigation-ui.js", asset));
  const {speechOutput: driver} = await import(new URL("speech-output.js", asset));
  const ai = await import(new URL("ai-control.js", asset));
  const {navigationSpeechText} = await import(new URL("integration/navigation-speech.js", asset));
  const {videoAlertText} = await import(new URL("integration/video-alerts.js", asset));
  window.__phoneControllers = {glue, cameraApp, navigation, driver, ai};
  window.__phoneProbe = {frames: [], sources: [], guidance: [], audio: [], utterances: [],
    synthesisRequests: [], ttsSubmissions: [], navigation: []};
  /* Functionality: Observe validated camera frames and original source identity.
   * Inputs: production source event. Outputs: bounded public metadata.
   * Logic: select actual metadata fields only on genuine frame events; Constraints: no image retention. */
  function recordFrame(event) {
    if (event.type === "frame") {
      const metadata = event.result.metadata;
      window.__phoneProbe.frames.push({session_id: metadata.session_id, index: metadata.index, model: metadata.model,
        provider: metadata.provider, precision: metadata.precision, output_precision: metadata.output_precision,
        backend: metadata.backend, source_sha256: metadata.source_sha256, captured_at_ms: event.capturedAt,
        capture_time_s: metadata.capture_time_s, timestamp_source: metadata.timestamp_source,
        tracking_epoch: metadata.tracking_epoch, tracking_status: metadata.tracking_status,
        tracking_model: metadata.tracking_model, depth_model: metadata.depth_model, depth_units: metadata.depth_units,
        risk_model: metadata.risk_model, count: metadata.count,
        required_alert_count: metadata.boxes.filter(box => box.alert_required === true).length,
        alert_track_ids: metadata.boxes.filter(box => box.alert_required === true).map(box => box.track_id),
        inference_ms: metadata.inference_ms, server_ms: metadata.server_ms,
        capture_to_result_ms: event.timing?.captureToResultMs ?? null,
        capture_to_decoded_ms: event.timing?.captureToDecodedMs ?? null});
    } else window.__phoneProbe.sources.push({type: event.type, session_id: event.sessionId ?? null,
      session_seconds: event.contract?.session_seconds, protocol_version: event.contract?.protocol_version});
  }
  /* Functionality: Bind backend playback starts to original guidance and output policy.
   * Inputs: manager lifecycle and transiently observed synthesis metadata. Outputs: ownership/rate/voice and provenance booleans.
   * Logic: match only at genuine output_start using request identity and precomputed text-policy status.
   * Constraints: synthetic risk remains separate from model-result evidence; request text is not exported. */
  function recordGuidance(event) {
    window.__phoneProbe.guidance.push({phase: event.phase, channel: event.channel, kind: event.kind,
      request_id: event.request_id, at: event.at, session_id: event.session_id, frame_index: event.frame_index});
    if (event.phase !== "output_start" || event.channel !== "speech") return;
    const manager = glue.guidanceManager, active = manager.active.speech, last = driver.lastRequest, request = driver.current;
    const owned = active?.requestId === event.request_id && last?.id === event.request_id;
    const commandIndex = window.__phoneProbe.synthesisRequests.findIndex(item => item.request_id === request?.requestId);
    const command = commandIndex < 0 ? null : window.__phoneProbe.synthesisRequests[commandIndex];
    const expected = !owned ? null : active?.kind === "risk" ? videoAlertText(active.box, last.language)
      : navigationSpeechText(active?.snapshot, last.language);
    window.__phoneProbe.utterances.push({kind: active?.kind, request_id: event.request_id,
      session_id: active?.source?.session_id ?? null, frame_index: active?.source?.frame_index ?? null,
      captured_at_ms: active?.source?.captured_at_ms ?? null, track_id: active?.box?.track_id ?? null,
      capture_to_start_ms: event.capture_to_start_ms ?? null, request_owned: owned,
      text_matches_policy: Boolean(owned && active?.kind !== "assistant" && command?.text && command.text === expected),
      backend_voice: Boolean(owned && last?.service === "backend" && last?.voice_id === "Cherry" && command &&
        command.language === last.language),
      configured_rate: last?.rate ?? null, server_rate: command?.rate ?? null,
      synthetic_risk: active?.source?.session_id === "synthetic-phone-tts-only"});
    if (commandIndex >= 0) window.__phoneProbe.synthesisRequests.splice(commandIndex, 1);
  }
  /* Functionality: Preserve genuine backend audio lifecycle without the speech body.
   * Inputs: driver event. Outputs: safe identity/phase/error class. Logic: field allowlist only.
   * Constraints: never fabricate audio starts, ends or audibility success. */
  function recordAudio(event) {
    window.__phoneProbe.audio.push({phase: event.phase, request_id: event.request_id,
      id: event.id, at: event.at, reason: event.reason, state: event.state});
  }
  /* Functionality: Record original navigation failures and timing before source-stop assertions.
   * Inputs: production semantic event. Outputs: safe state/reason/time without location data.
   * Logic: retain original reason and active ownership; Constraints: never infer causal coupling. */
  function recordNavigation(event) {
    const value = event.detail;
    window.__phoneProbe.navigation.push({state: value.state, reason: value.reason, active: value.active,
      at: performance.now(), source_timestamp: value.position?.timestamp ?? null});
  }
  cameraApp.subscribe(recordFrame); glue.guidanceManager.subscribe(recordGuidance); driver.subscribe(recordAudio);
  document.addEventListener("prs-navigation-change", recordNavigation);
  return {secure_context: isSecureContext, media_devices: Boolean(navigator.mediaDevices?.getUserMedia),
    geolocation_api: Boolean(navigator.geolocation?.watchPosition),
    native_geolocation_api: window.__phoneGPS.describe().native_api_present,
    canonical_camera: glue.cameraApp === cameraApp, canonical_navigation: glue.navigation === navigation,
    canonical_speech: glue.speechOutput === driver && glue.guidanceManager.speech === driver,
    control_navigation_state_matches: ai.performNavigation("navigation_status").state === navigation.describe().state,
    local_session_seconds: cameraApp.localSessionSeconds, asset_prefix: asset.pathname,
    control_session_id: sessionStorage.getItem("prs.ai.session")};
}

/* Functionality: Capture the original navigation state immediately before and after camera cleanup.
 * Inputs: canonical page controller. Outputs: reason/ownership/source-time evidence without position.
 * Logic: select original snapshot fields; Constraints: no route, coordinates or controller mutation. */
function navigationEvidence() {
  const {navigation} = window.__phoneControllers, value = navigation.describe();
  return {state: value.state, reason: value.reason, active: value.active,
    watch_active: navigation.watchId !== null, source_timestamp: value.position?.timestamp ?? null,
    source_age_ms: value.position?.age_ms ?? null, at: performance.now()};
}

/* Functionality: Inspect cleanup of the actual camera/GPS/model socket and automatic outputs.
 * Inputs: original page state. Outputs: body-free lifecycle plus native track termination.
 * Logic: retain stopped native track references from the guard; Constraints: no coordinates/pixels/text. */
function cleanupEvidence() {
  const {cameraApp, navigation, driver, glue} = window.__phoneControllers;
  return {camera_mode: cameraApp.mode, camera_stream_cleared: cameraApp.stream === null,
    tracks: window.__phoneMedia.tracks.map(track => ({kind: track.kind, ready_state: track.readyState})),
    media_calls: structuredClone(window.__phoneMedia.calls), navigation_active: navigation.active,
    navigation_state: navigation.describe().state, navigation_reason: navigation.describe().reason,
    gps_fixture: window.__phoneGPS.describe(), gps_watch_cleared: navigation.watchId === null,
    gps_deadline_cleared: navigation.timer === null, guidance: glue.guidanceManager.describe(),
    scheduler_cleared: glue.guidanceManager.schedulerTimer === null, speech_request_cleared: driver.current === null,
    camera_socket_closed: !cameraApp.client?.socket || cameraApp.client.socket.readyState === 3,
    speech_activation: glue.speechActivation.describe()};
}

/* Functionality: Await body-free event evidence with a finite observer deadline.
 * Inputs: synchronous predicate, timeout and fixed failure code. Outputs: void or coded failure.
 * Logic: observe without retrying operations. Constraints: no hidden network/source submissions. */
async function waitNode(predicate, milliseconds, code) {
  const deadline = Date.now() + milliseconds;
  while (!predicate()) {
    requireGate(Date.now() < deadline, code);
    await new Promise(done => setTimeout(done, 50));
  }
}

/* Functionality: Observe real public WSS handshakes and inference clocks without media/command bodies.
 * Inputs: Playwright socket and report. Outputs: public endpoint, lifecycle, inference and bounded TTS response metadata.
 * Logic: permit exact mounted control, assistant, local-inference and synthesis paths; no authorization-header capture.
 * Constraints: read-only observation, never a replacement client or access-control workaround. */
function monitorSocket(socket, report) {
  const address = new URL(socket.url());
  const item = {path: address.pathname, secure: address.protocol === "wss:", closed: false,
    hellos: [], inputs: [], welcomes: [], tts: [], pongs: 0};
  report.sockets.push(item);
  if (address.origin !== PHONE_ORIGIN.replace("https:", "wss:") ||
      ![`${PREFIX}ws/control/`, `${PREFIX}ws/agent/`, `${PREFIX}ws/detect/local/`, `${PREFIX}ws/tts/`].includes(address.pathname))
    report.failures.push({phase: report.phase, code: "UNEXPECTED_SOCKET", type: "GateError"});
  socket.on("close", () => { item.closed = true; });
  /* Functionality: Read allowlisted handshake, heartbeat and TTS lifecycle metadata.
   * Inputs: received frame. Outputs: model hello, control welcome/pong and body-free PCM events.
   * Logic: Parse text control messages only; binary model results are ignored.
   * Constraints: No utterance text, binary results, control command or private header retention. */
  function received(frame) {
    if (typeof frame.payload !== "string") return;
    let value; try { value = JSON.parse(frame.payload); } catch { return; }
    if (value.kind === "session") item.hellos.push({session_id: value.session_id, model: value.model,
      provider: value.provider, protocol_version: value.protocol_version, session_seconds: value.session_seconds,
      source_sha256: value.source_sha256, precision: value.precision, output_precision: value.output_precision,
      backend: value.backend, classes: value.classes, confidence_threshold: value.confidence_threshold,
      max_edge: value.max_edge, jpeg_quality: value.jpeg_quality});
    if (value.type === "welcome") item.welcomes.push(value.session_id);
    if (value.type === "pong") item.pongs++;
    if (address.pathname === `${PREFIX}ws/tts/`)
      item.tts.push({type: value.type, request_id: value.request_id ?? null, code: value.code ?? null,
        sample_rate: value.sample_rate ?? null,
        format: value.format ?? null, audio_bytes: value.audio_bytes ?? null});
  }
  /* Functionality: Retain PRF2 wire header identity while discarding JPEG bytes immediately.
   * Inputs: original outbound frame. Outputs: index/capture seconds/length or safe protocol failure.
   * Logic: inspect only 16-byte envelope. Constraints: no timestamp substitution or media archive. */
  function sent(frame) {
    if (!Buffer.isBuffer(frame.payload) || address.pathname !== `${PREFIX}ws/detect/local/`) return;
    const bytes = frame.payload;
    if (bytes.length < 16 || bytes.toString("ascii", 0, 4) !== "PRF2") {
      report.failures.push({phase: report.phase, code: "INVALID_PRF2", type: "GateError"}); return;
    }
    item.inputs.push({index: bytes.readUInt32BE(4), capture_time_s: bytes.readDoubleBE(8), bytes: bytes.length});
  }
  socket.on("framereceived", received); socket.on("framesent", sent);
}

/* Functionality: Verify default-on controls and the fixed bilingual backend voice.
 * Inputs: real page voice controls. Outputs: exact 2x configuration without utterance text.
 * Logic: check fixed server metadata and actual default settings. Constraints: metadata alone does not prove synthesis. */
async function selectSpeech(page) {
  for (const id of ["speech-enabled", "navigation-speech-enabled", "assist-risk-enabled"])
    requireGate(await page.isChecked(`#${id}`) === true, "OUTPUT_NOT_DEFAULT_ON");
  const vibration = await page.locator("#vibration-enabled").evaluate(node => ({supported: !node.disabled, checked: node.checked}));
  requireGate(vibration.checked === vibration.supported, "VIBRATION_DEFAULT_MISMATCH");
  requireGate(await page.inputValue("#navigation-speech-voice") === "Cherry", "BACKEND_BILINGUAL_VOICE_MISSING");
  requireGate(Number(await page.inputValue("#speech-rate")) === 2, "SPEECH_RATE_CHANGED");
  return {language: "zh-CN", rate: 2, backend_voice: "Cherry", backend_only: true, default_switches_on: true,
    vibration_supported: vibration.supported, vibration_default_on: vibration.checked, physical_audibility_verified: false};
}

/* Functionality: Obtain one real ORS route through original NUS example controls and start navigation.
 * Inputs: explicit synthetic GPS fixture and actual page. Outputs: route metadata without coordinates.
 * Logic: no request interception or core route injection. Constraints: synthetic GPS is never real-phone GPS. */
async function planNavigation(page, url) {
  await page.waitForFunction(() => !document.getElementById("example").disabled, null, {timeout: 20000});
  await page.click("#example");
  await page.waitForFunction(() => !document.getElementById("plan").disabled, null, {timeout: 10000});
  const responsePromise = page.waitForResponse(response => response.url() === new URL("route/", url).href &&
    response.request().method() === "POST", {timeout: 30000});
  await page.click("#plan");
  const response = await responsePromise;
  requireGate(response.status() === 200, "REAL_ORS_ROUTE_FAILED");
  const route = await response.json();
  requireGate(route.provider === "openrouteservice" && route.profile === "foot-walking" &&
    route.geometry?.type === "LineString" && route.geometry.coordinates?.length >= 2 && route.steps?.length > 0, "REAL_ORS_ROUTE_INVALID");
  await page.waitForFunction(() => !document.getElementById("navigation-start").disabled);
  await page.click("#navigation-start");
  await page.waitForFunction(() => window.__phoneControllers.navigation.active && window.__phoneControllers.navigation.watchId !== null);
  return {status: response.status(), provider: route.provider, profile: route.profile,
    points: route.geometry.coordinates.length, steps: route.steps.length, source: "real_public_ORS",
    position_source: "explicit_synthetic_watchPosition_NUS_Date_now", real_phone_gps_verified: false};
}

/* Functionality: Select a Chrome fake-video source and start the actual CameraApp pipeline.
 * Inputs: public page, optional permission grant, and whether a named Y4M file is the source.
 * Outputs: one actual detection session. Logic: select the sole fake device or exact supplied-file device.
 * Constraints: launch always enables Chrome's fake-device flag; no physical camera is accessible. */
async function startCamera(page, grantCamera = false, fileFixture = false) {
  await page.selectOption("#assist-source", "camera");
  if (grantCamera) {
    await page.click("#assist-devices-authorize");
    await page.waitForFunction(() => !document.getElementById("assist-devices-authorize").disabled &&
      [...document.querySelectorAll("#assist-device option")].some(option => option.value));
    const device = await page.locator("#assist-device option").evaluateAll(options =>
      options.filter(option => option.value).map(option => ({id: option.value, fake: /fake/i.test(option.textContent)})));
    requireGate(device.length > 0 && (device.every(item => item.fake) ||
      fileFixture && device.length === 1), "FAKE_VIDEO_DEVICE_REQUIRED");
    await page.selectOption("#assist-device", device[0].id);
  }
  await page.click("#start");
}

/* Functionality: Wait for genuinely decoded/validated frames from the current CameraApp generation.
 * Inputs: page and minimum result count. Outputs: current session ID.
 * Logic: 70s observer budget covers original 55s cold plus network/display; no frame generation/retry.
 * Constraints: no metadata mutation, fake model output or fallback on failed inference. */
async function waitCameraFrames(page, minimum = 3) {
  await page.waitForFunction(count => {
    const camera = window.__phoneControllers.cameraApp;
    return camera.mode === "camera" && camera.sessionId &&
      window.__phoneProbe.frames.filter(frame => frame.session_id === camera.sessionId).length >= count;
  }, minimum, {timeout: 70000});
  return page.evaluate(() => window.__phoneControllers.cameraApp.sessionId);
}

/* Functionality: Wait for one genuine risk-alert synthesis and completed backend playback.
 * Inputs: Active real-model camera session and bounded browser page. Outputs: Matching alert identity and latency.
 * Logic: Require a non-synthetic risk utterance linked by session/frame/track to a server-required alert,
 * then require its original audio request to end before camera teardown.
 * Constraints: No generated risk event, manual speech, threshold change or physical-audibility claim. */
async function waitModelAlert(page, sessionId) {
  await page.waitForFunction(id => {
    const item = window.__phoneProbe.utterances.find(value => value.kind === "risk" &&
      !value.synthetic_risk && value.session_id === id && Number.isFinite(value.capture_to_start_ms) &&
      value.capture_to_start_ms >= 0 && value.capture_to_start_ms < 1000 &&
      window.__phoneProbe.frames.some(frame => frame.session_id === id && frame.index === value.frame_index &&
        frame.captured_at_ms === value.captured_at_ms && frame.alert_track_ids.includes(value.track_id)));
    return Boolean(item);
  }, sessionId, {timeout: 30000});
  await page.waitForFunction(id => {
    const item = window.__phoneProbe.utterances.find(value => value.kind === "risk" &&
      !value.synthetic_risk && value.session_id === id && Number.isFinite(value.capture_to_start_ms) &&
      value.capture_to_start_ms >= 0 && value.capture_to_start_ms < 1000 &&
      window.__phoneProbe.frames.some(frame => frame.session_id === id && frame.index === value.frame_index &&
        frame.captured_at_ms === value.captured_at_ms && frame.alert_track_ids.includes(value.track_id)));
    return item && window.__phoneProbe.audio.some(event => event.phase === "end" &&
      event.request_id === item.request_id);
  }, sessionId, {timeout: 30000});
  return page.evaluate(id => {
    const item = window.__phoneProbe.utterances.find(value => value.kind === "risk" &&
      !value.synthetic_risk && value.session_id === id && Number.isFinite(value.capture_to_start_ms) &&
      value.capture_to_start_ms >= 0 && value.capture_to_start_ms < 1000 &&
      window.__phoneProbe.frames.some(frame => frame.session_id === id && frame.index === value.frame_index &&
        frame.captured_at_ms === value.captured_at_ms && frame.alert_track_ids.includes(value.track_id)));
    return {model_alert: true, frame_index: item.frame_index, track_id: item.track_id,
      capture_to_start_ms: item.capture_to_start_ms, backend_audio_ended: true,
      physical_audibility_verified: false};
  }, sessionId);
}

/* Functionality: Observe real worker drain before the next separately requested camera start.
 * Inputs: public guest context and approved page URL. Outputs: bounded stable-idle health evidence.
 * Logic: read the original health endpoint until loaded/ready and not busy for250ms, at most15s.
 * Constraints: never repeat a start/inference operation, change capacity or fake GPU completion. */
async function waitCapacity(context, url) {
  const deadline = Date.now() + 15000; let idleAt = null;
  while (Date.now() < deadline) {
    const response = await context.request.get(new URL("../integration/status/", url).href,
      {maxRedirects: 0, timeout: 5000});
    requireGate(response.status() === 200, "WORKER_STATUS_UNAVAILABLE");
    const body = await response.json();
    requireGate(body.provider === "local" && body.session_seconds === null, "WORKER_STATUS_CONTRACT");
    if (body.busy === false && body.loaded === true && body.ready === true) {
      idleAt ??= Date.now();
      if (Date.now() - idleAt >= 250) return {loaded: true, ready: true, busy: false, stable_idle_observed: true};
    } else idleAt = null;
    await new Promise(done => setTimeout(done, 250));
  }
  requireGate(false, "WORKER_DRAIN_TIMEOUT");
}

/* Functionality: Use the existing one-click cleanup and inspect actual native ownership release.
 * Inputs: page. Outputs: safe cleanup evidence. Logic: no controller replacement or forced thread kill.
 * Constraints: control hub stays connected while page remains open; only model socket must close here. */
async function stopAll(page) {
  await page.click("#assist-stop-all");
  await page.waitForFunction(() => {
    const {cameraApp, navigation, driver, glue} = window.__phoneControllers;
    return cameraApp.mode === "idle" && !navigation.active && navigation.watchId === null &&
      navigation.timer === null && driver.current === null && glue.guidanceManager.schedulerTimer === null &&
      window.__phoneMedia.tracks.every(track => track.readyState === "ended");
  }, null, {timeout: 15000});
  return page.evaluate(cleanupEvidence);
}

/* Functionality: Test backend risk speech only in an explicitly synthetic non-model partition.
 * Inputs: idle original page with opted-in speech. Outputs: genuine backend PCM lifecycle metadata.
 * Logic: generated risk is sent directly to the existing scheduler after actual model sessions end.
 * Constraints: never counted as a model frame, inferred risk, real camera alert or physical audibility. */
async function syntheticSpeech(page) {
  await page.evaluate(() => {
    const manager = window.__phoneControllers.glue.guidanceManager;
    manager.beginRisk("synthetic-phone-tts-only");
    manager.ingestRisk({sessionId: "synthetic-phone-tts-only", capturedAt: performance.now(), metadata: {
      index: 0, tracking_epoch: 0, boxes: [{track_id: 900001, label: "car", class_name: "car", direction: "front",
        distance_m: 1.2, smoothed_distance_m: 1.2, relative_speed_mps: -3, motion_state: "approaching",
        ttc_sec: 0.4, risk_score: 90, risk_level: "critical", alert_required: true, alert_priority: 4}]}});
  });
  await page.waitForFunction(() => window.__phoneProbe.utterances.some(item => item.synthetic_risk), null, {timeout: 10000});
  await page.waitForFunction(() => {
    const utterance = window.__phoneProbe.utterances.find(item => item.synthetic_risk);
    return window.__phoneProbe.audio.some(event => event.phase === "end" && event.request_id === utterance.request_id);
  }, null, {timeout: 15000});
  await page.evaluate(() => window.__phoneControllers.glue.guidanceManager.stopRisk("synthetic_test_complete"));
  return {source: "explicit_generated_scheduler_input", model_alert_verified: false,
    physical_audibility_verified: false, backend_tts_stream_verified: true};
}

/* Functionality: Exercise manual English and Chinese synthesis on the public diagnostic page.
 * Inputs: page with enabled speech controls. Outputs: body-free request/playback evidence for both languages.
 * Logic: use real user clicks, wait for actual backend PCM start/end, and compare request metadata only.
 * Constraints: short fixed sample phrases; no text appears in report/logs and no audibility claim is made. */
async function manualBilingualTts(page) {
  const cases = [{language: "en-US", text: "Turn left at the next street."},
    {language: "zh-CN", text: "前方路口左转。"}], results = [];
  for (const item of cases) {
    const previous = await page.evaluate(() => window.__phoneControllers.driver.lastRequest?.id ?? 0);
    await page.fill("#speech-text", item.text);
    await page.selectOption("#speech-language", item.language);
    await page.click("#speech-play");
    await page.waitForFunction(previousId => {
      const driver = window.__phoneControllers.driver;
      return driver.lastRequest?.id > previousId && driver.phase === "completed" && driver.current === null;
    }, previous, {timeout: 30000});
    const evidence = await page.evaluate(({language, textLength, previousId}) => {
      const driver = window.__phoneControllers.driver, request = driver.lastRequest;
      const events = window.__phoneProbe.audio.filter(event => event.request_id === request.id);
      const socketCall = window.__phoneSpeechCalls.at(-1);
      return {language: request.language, voice_id: request.voice_id, service: request.service,
        text_length: request.text_length, expected_text_length: textLength, rate: request.rate,
        started: events.filter(event => event.phase === "start").length === 1,
        ended: events.filter(event => event.phase === "end").length === 1,
        secure_connection: socketCall?.secure === true, user_gesture: socketCall?.user_gesture_active === true,
        request_id_advanced: request.id > previousId};
    }, {language: item.language, textLength: [...item.text].length, previousId: previous});
    requireGate(evidence.language === item.language && evidence.voice_id === "Cherry" && evidence.service === "backend" &&
      evidence.text_length === evidence.expected_text_length && evidence.rate === 2 && evidence.started && evidence.ended &&
      evidence.secure_connection && evidence.user_gesture && evidence.request_id_advanced, "MANUAL_BILINGUAL_TTS_FAILED");
    results.push(evidence);
  }
  return {cases: results, physical_audibility_verified: false};
}

/* Functionality: Execute one public-relay acceptance and preserve safe success/failure evidence.
 * Inputs: validated options and existing deployed relay. Outputs: report with body-free source/output events;
 * no credential, utterance, pixel or coordinate values. Logic: guest-access boundary, camera-first/no-acquisition
 * DOM gate, real ORS/WSS/model, two lifecycles and TTS; strict model-alert mode retains the first camera session
 * until the correlated inference alert has completed backend playback.
 * Constraints: no service launch/deployment, TLS override, HTTP/model mocks, automatic retry or phone access. */
export async function run(options) {
  const report = {complete: false, phase: "public_guest_access", target: options.url,
    model_alert_gate_requested: options.requireModelAlert === true,
    browser: "Chrome with phone viewport; not Safari or a real iPhone", tls_validation: "enabled",
    fixtures: {camera: options.fakeCameraFile ? "explicit caller-supplied Y4M frame source" :
      "Chrome native fake-video device; no physical camera or microphone",
      gps: "Explicit synthetic NUS watchPosition callbacks with Date.now timestamps; not native/phone GPS"},
    actual_model_verified: false, model_alert_verified: false, real_ors_verified: false,
    real_iphone_verified: false, hardware_outputs_verified: false,
    requests: [], responses: [], sockets: [], frames: [], failures: [], checks: {}};
  let browser, context, page, navigationVerified = false;
  try {
    const {chromium, request} = createRequire(import.meta.url)(options.runtime);
    if (options.fakeCameraFile)
      requireGate(options.fakeCameraFile.toLowerCase().endsWith(".y4m") &&
        (await stat(options.fakeCameraFile)).isFile(), "INVALID_FAKE_CAMERA_FILE");
    report.phase = "public_guest_boundary";
    const guest = await request.newContext();
    try {
      const response = await guest.get(options.url, {maxRedirects: 0, timeout: 15000});
      requireGate(response.status() === 200 && !response.headers()["www-authenticate"], "PUBLIC_GUEST_ENTRY_FAILED");
      report.checks.guest_http_status = response.status();
    } finally { await guest.dispose(); }
    report.phase = "browser_and_tls";
    browser = await chromium.launch({channel: "chrome", headless: true,
      args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
        ...(options.fakeCameraFile ? [`--use-file-for-fake-video-capture=${options.fakeCameraFile}`] : [])]});
    context = await browser.newContext({viewport: {width: 390, height: 844},
      isMobile: true, hasTouch: true, locale: "zh-CN"});
    await context.grantPermissions(["geolocation"], {origin: PHONE_ORIGIN});
    await context.addInitScript(guardMedia);
    await context.addInitScript(installGPS, NUS);
    page = await context.newPage();
    page.on("request", item => {
      const evidence = classifyRequest(item.url(), item.resourceType()); if (!evidence) return;
      report.requests.push({...evidence, method: item.method()});
      if (!evidence.allowed) report.failures.push({phase: report.phase, code: "RESOURCE_ESCAPED_MOUNT", type: "GateError"});
    });
    page.on("response", response => {
      const item = classifyRequest(response.url(), response.request().resourceType()); if (!item) return;
      report.responses.push({...item, status: response.status()});
      if (item.scope === "relay" && item.path.includes("/assets/") && response.status() >= 400)
        report.failures.push({phase: report.phase, code: "MODULE_HTTP_FAILURE", type: "GateError"});
    });
    page.on("pageerror", error => report.failures.push(safeFailure(error, report.phase)));
    page.on("requestfailed", request => {
      const item = classifyRequest(request.url()); if (item) report.failures.push({phase: report.phase,
        code: "RESOURCE_REQUEST_FAILED", type: "NetworkError", path: item.path, origin: item.origin});
    });
    page.on("websocket", socket => monitorSocket(socket, report));
    const response = await page.goto(options.url, {waitUntil: "domcontentloaded", timeout: 30000});
    requireGate(response.status() === 200, "PUBLIC_PAGE_FAILED");
    report.checks.permissions_policy = response.headers()["permissions-policy"];
    requireGate(/camera=\(self\)/.test(report.checks.permissions_policy) &&
      /geolocation=\(self\)/.test(report.checks.permissions_policy), "PHONE_PERMISSIONS_POLICY");
    report.phase = "canonical_modules"; report.identity = await page.evaluate(observe);
    requireGate(report.identity.secure_context && report.identity.media_devices && report.identity.geolocation_api &&
      report.identity.native_geolocation_api &&
      report.identity.canonical_camera && report.identity.canonical_navigation && report.identity.canonical_speech &&
      report.identity.control_navigation_state_matches && report.identity.asset_prefix === `${PREFIX}assets/` &&
      report.identity.local_session_seconds === null, "CANONICAL_PHONE_ENTRY");
    report.phase = "initial_camera_entry";
    report.checks.initial_camera_entry = {
      source: await page.inputValue("#assist-source"),
      camera_panel_visible: await page.locator("#assist-camera-panel").isVisible(),
      video_panel_hidden: await page.locator("#assist-video-panel").isHidden(),
      get_user_media_calls: await page.evaluate(() => window.__phoneMedia.calls.length),
      startup_outputs: await page.evaluate(() => ({speech_calls: window.__phoneSpeechCalls.length,
        gps_watches: window.__phoneGPS.describe().watch_count,
        gps_one_shot_requests: window.__phoneGPS.describe().one_shot_requests,
        voice_id: document.getElementById("navigation-speech-voice").value,
        activation: window.__phoneControllers.glue.speechActivation.describe(),
        guidance: window.__phoneControllers.glue.guidanceManager.describe()}))};
    const initialCamera = report.checks.initial_camera_entry;
    requireGate(initialCamera.source === "camera" && initialCamera.camera_panel_visible &&
      initialCamera.video_panel_hidden && initialCamera.get_user_media_calls === 0, "INITIAL_CAMERA_ENTRY_INVALID");
    const initialOutput = initialCamera.startup_outputs;
    requireGate(initialOutput.speech_calls === 0 && initialOutput.gps_watches === 0 && initialOutput.gps_one_shot_requests === 0 &&
      initialOutput.voice_id === "Cherry" &&
      initialOutput.activation.ready === false && initialOutput.guidance.paused.speech === true &&
      initialOutput.guidance.reasons.speech === "activation_required", "STARTUP_OUTPUT_NOT_GATED");
    report.phase = "private_endpoint_boundaries"; report.checks.private_endpoints = [];
    for (const path of ["mcp", "mcp/", "admin", "admin/"]) {
      const result = await context.request.get(new URL(path, PHONE_ORIGIN + PREFIX).href, {maxRedirects: 0});
      report.checks.private_endpoints.push({path: PREFIX + path, status: result.status()});
      requireGate(result.status() === 404, "PRIVATE_ENDPOINT_EXPOSED");
    }
    report.phase = "explicit_speech_configuration"; report.speech_configuration = await selectSpeech(page);
    report.phase = "real_ors_and_synthetic_gps";
    try {
      requireGate(report.responses.some(item => item.path === `${PREFIX}maps/config/` && item.status === 200) &&
        !report.responses.some(item => item.path === `${PREFIX}maps/config/` && item.status >= 400),
      "PUBLIC_MAP_CONFIG_FAILED");
      report.route = await planNavigation(page, options.url);
      report.real_ors_verified = navigationVerified = true;
      await page.waitForFunction(() => window.__phoneControllers.glue.speechActivation.describe().ready, null, {timeout: 10000});
      report.speech_activation_evidence = await page.evaluate(() => ({
        activation: window.__phoneControllers.glue.speechActivation.describe(),
        calls: structuredClone(window.__phoneSpeechCalls), audio: structuredClone(window.__phoneProbe.audio)}));
      report.speech_activation = verifySpeechActivation(report.speech_activation_evidence);
    } catch (error) {
      report.failures.push(safeFailure(error, report.phase));
      report.navigation_incomplete = {route_post_observed: report.requests.some(item =>
        item.path === `${PREFIX}maps/route/` && item.method === "POST"), model_test_continues_independently: true};
    }
    if (navigationVerified) {
      try {
        await page.waitForFunction(() => window.__phoneProbe.utterances.some(item => item.kind === "navigation"), null, {timeout: 15000});
      } catch (error) { report.failures.push(safeFailure(error, "navigation_backend_output")); }
    }
    report.phase = "first_real_model_session";
    await startCamera(page, true, Boolean(options.fakeCameraFile)); const firstSession = await waitCameraFrames(page);
    if (options.requireModelAlert) {
      report.phase = "first_model_risk_alert";
      report.model_alert_evidence = await waitModelAlert(page, firstSession);
    }
    report.checks.navigation_before_camera_stop = await page.evaluate(navigationEvidence);
    if (navigationVerified && !report.checks.navigation_before_camera_stop.active) {
      report.failures.push({phase: report.phase, code: "NAVIGATION_FAILED_BEFORE_CAMERA_STOP", type: "GateError"});
      navigationVerified = false;
    }
    await page.click("#stop");
    await page.waitForFunction(() => window.__phoneControllers.cameraApp.mode === "idle" &&
      window.__phoneMedia.tracks.every(track => track.readyState === "ended"));
    report.checks.navigation_after_camera_stop = await page.evaluate(navigationEvidence);
    report.checks.camera_stop_preserves_navigation = navigationVerified ?
      report.checks.navigation_after_camera_stop.active && report.checks.navigation_after_camera_stop.watch_active : null;
    if (navigationVerified && !report.checks.camera_stop_preserves_navigation) {
      report.failures.push({phase: report.phase, code: "CAMERA_STOPPED_NAVIGATION", type: "GateError"});
      navigationVerified = false;
    }
    report.checks.first_session_drained = await waitCapacity(context, options.url);
    report.phase = "second_real_model_session";
    await startCamera(page); const secondSession = await waitCameraFrames(page);
    requireGate(firstSession !== secondSession, "MODEL_SESSION_REUSED");
    report.checks.navigation_before_navigation_stop = await page.evaluate(navigationEvidence);
    if (navigationVerified && !report.checks.navigation_before_navigation_stop.active) {
      report.failures.push({phase: report.phase, code: "NAVIGATION_FAILED_BEFORE_OWN_STOP", type: "GateError"});
      navigationVerified = false;
    }
    if (navigationVerified) {
      await page.click("#navigation-stop");
      await page.waitForFunction(() => !window.__phoneControllers.navigation.active && window.__phoneControllers.navigation.watchId === null);
      report.checks.navigation_stop_preserves_camera = await page.evaluate(() => window.__phoneControllers.cameraApp.mode === "camera");
      requireGate(report.checks.navigation_stop_preserves_camera, "NAVIGATION_STOPPED_CAMERA");
    } else report.checks.navigation_stop_preserves_camera = null;
    await waitCameraFrames(page, 4);
    if (navigationVerified) {
      await page.click("#navigation-start");
      await page.waitForFunction(() => window.__phoneControllers.navigation.active && window.__phoneControllers.navigation.watchId !== null);
    }
    report.phase = "one_click_cleanup"; report.cleanup = await stopAll(page);
    report.checks.second_session_drained = await waitCapacity(context, options.url);
    requireGate(report.cleanup.media_calls.every(call => call.audio_false && call.video) &&
      report.cleanup.tracks.every(track => track.kind === "video" && track.ready_state === "ended") &&
      report.cleanup.camera_stream_cleared && report.cleanup.gps_watch_cleared && report.cleanup.gps_deadline_cleared &&
      report.cleanup.scheduler_cleared && report.cleanup.speech_request_cleared && report.cleanup.camera_socket_closed, "RESOURCE_CLEANUP_FAILED");
    await waitNode(() => report.sockets.filter(item => item.path === `${PREFIX}ws/detect/local/`).every(item => item.closed),
      15000, "MODEL_SOCKET_NOT_CLOSED");
    report.frames = await page.evaluate(() => structuredClone(window.__phoneProbe.frames));
    report.model_sessions = verifyModelEvidence(report.frames, report.sockets.filter(item => item.path === `${PREFIX}ws/detect/local/`));
    report.actual_model_verified = true;
    report.phase = "separate_synthetic_risk_tts"; report.synthetic_tts = await syntheticSpeech(page);
    report.phase = "manual_bilingual_tts"; report.checks.manual_bilingual_tts = await manualBilingualTts(page);
    await waitNode(() => report.sockets.filter(item => item.path === `${PREFIX}ws/tts/`).every(item => item.closed),
      15000, "TTS_SOCKET_NOT_CLOSED");
    const ttsSockets = report.sockets.filter(item => item.path === `${PREFIX}ws/tts/`);
    const ttsEvidence = await page.evaluate(() => ({audio: structuredClone(window.__phoneProbe.audio),
      submissions: structuredClone(window.__phoneProbe.ttsSubmissions)}));
    report.checks.backend_tts_streams = verifyTtsStreamEvidence(ttsSockets, ttsEvidence.audio,
      ttsEvidence.submissions);
    report.phase = "control_hub_and_backend_audio";
    await waitNode(() => report.sockets.some(item => item.path === `${PREFIX}ws/control/` && item.pongs > 0),
      20000, "CONTROL_HEARTBEAT_MISSING");
    const controls = report.sockets.filter(item => item.path === `${PREFIX}ws/control/`);
    requireGate(controls.length === 1 && controls[0].welcomes.length === 1 &&
      controls[0].welcomes[0] === report.identity.control_session_id, "CONTROL_HUB_IDENTITY");
    report.output_evidence = await page.evaluate(() => ({guidance: structuredClone(window.__phoneProbe.guidance),
      utterances: structuredClone(window.__phoneProbe.utterances),
      audio: structuredClone(window.__phoneProbe.audio),
      tts_submissions: structuredClone(window.__phoneProbe.ttsSubmissions)}));
    requireGate(report.output_evidence.utterances.every(item => item.request_owned && item.text_matches_policy &&
      item.backend_voice && item.configured_rate === 2 && item.server_rate === 2), "BACKEND_SPEECH_IDENTITY");
    report.model_alert_verified = report.output_evidence.utterances.some(item => item.kind === "risk" &&
      !item.synthetic_risk && Number.isFinite(item.capture_to_start_ms) && item.capture_to_start_ms >= 0 &&
      item.capture_to_start_ms < 1000 && report.frames.some(frame => frame.session_id === item.session_id &&
        frame.index === item.frame_index && frame.captured_at_ms === item.captured_at_ms &&
        frame.alert_track_ids.includes(item.track_id)));
    if (options.requireModelAlert)
      requireGate(report.model_alert_verified && report.model_alert_evidence?.backend_audio_ended === true,
        "MODEL_RISK_ALERT_NOT_VERIFIED");
    requireGate(report.failures.length === 0, "OBSERVED_BROWSER_FAILURES");
    report.phase = "complete"; report.complete = true;
  } catch (error) {
    report.failures.push(safeFailure(error, report.phase));
    if (page) {
      try { report.partial_probe = await page.evaluate(() => {
        const probe = structuredClone(window.__phoneProbe ?? null);
        if (probe?.synthesisRequests) probe.synthesisRequests = probe.synthesisRequests.map(item => ({
          request_id: item.request_id ?? null, language: item.language ?? null, rate: item.rate ?? null}));
        if (probe?.ttsSubmissions) probe.ttsSubmissions = probe.ttsSubmissions.map(item => ({
          request_id: item.request_id ?? null, audio_request_id: item.audio_request_id ?? null,
          language: item.language ?? null, rate: item.rate ?? null}));
        return probe;
      }); } catch {}
      try { report.partial_cleanup = await page.evaluate(cleanupEvidence); } catch {}
      try {
        await requireNew(options.screenshot);
        await writeFile(options.screenshot, await page.screenshot({fullPage: true, mask: [page.locator(
          "#map, #route-form, #maps-message, #navigation-position, #navigation-instruction, #navigation-next, #steps, #speech-text, #preview, #echo, #video-source, #video-result, #assist-results")]}), {flag: "wx"});
        report.screenshot = options.screenshot;
      } catch {}
    }
  } finally {
    if (page) {
      try {
        const evidence = await page.evaluate(() => ({frames: structuredClone(window.__phoneProbe?.frames ?? []),
          navigation: structuredClone(window.__phoneProbe?.navigation ?? []), gps: window.__phoneGPS?.describe()}));
        report.frames = evidence.frames; report.navigation_evidence = evidence.navigation; report.gps_fixture = evidence.gps;
      } catch {}
    }
    if (browser) {
      try { await browser.close(); }
      catch (error) { report.failures.push(safeFailure(error, "browser_cleanup")); }
    }
    if (report.complete && report.failures.length) { report.complete = false; report.phase = "cleanup_failure"; }
  }
  return report;
}

/* Functionality: Save one exclusive success/failure artifact with credential-free console output.
 * Inputs: PHONE_URL NEW_REPORT.json plus PRS_PLAYWRIGHT_PATH.
 * Outputs: new JSON and optional masked failure screenshot. Logic: validate paths before browser work.
 * Constraints: no overwrite, secret logging, deployment/service commands, TLS bypass or automatic retry. */
export async function main(argv = process.argv.slice(2), environment = process.env) {
  const options = validateArguments(argv, environment);
  await requireNew(options.output); await requireNew(options.screenshot);
  await mkdir(dirname(options.output), {recursive: true});
  const report = await run(options);
  await writeFile(options.output, JSON.stringify(report, null, 2) + "\n", {flag: "wx"});
  process.stdout.write(JSON.stringify({complete: report.complete, actual_model_verified: report.actual_model_verified,
    real_ors_verified: report.real_ors_verified, real_iphone_verified: false, phase: report.phase,
    frames: report.frames.length, failures: report.failures}) + "\n");
  if (!report.complete) process.exitCode = 1;
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(error => { process.stderr.write(JSON.stringify(safeFailure(error, "preflight")) + "\n"); process.exitCode = 1; });
}
