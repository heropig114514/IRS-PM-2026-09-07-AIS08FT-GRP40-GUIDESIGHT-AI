/** Responsibilities: Bootstrap the independent local camera/integration/guidance UI.
 * Implementation: Explicit device/voice choices, event subscriptions, local-only operation gates,
 * latest result display and labeled demo navigation; no DOM polling or algorithm modification.
 * Related Modules: guidance/events/catalog/devices.js; existing camera/speech-output/vibration; integration.asgi assets.
 * Declaration Index: element; renderLabels; renderResults; populateDevices; readDevices; authorizeDevices; readVoices; onCamera; onGuidance;
 * applyVoice; initialize; localMvpReady; guidanceManager.
 * Variable Index: cameraApp/speechOutput/tester are existing singleton imports; doc/panel/nodes hold UI;
 * latest stores one result event, currentLanguage controls local catalog, labels maps fixed UI keys;
 * guidanceManager is default-disabled independent software policy, localMvpReady tracks read-only setup.
 */
import {cameraApp} from "../../live/assets/camera.js";
import {speechOutput} from "../../live/assets/speech-output.js";
import {tester} from "../../live/assets/vibration.js";
import {GuidanceManager} from "./guidance.js";
import {authorizeCameraDevices, explicitDeviceId} from "./devices.js";
import {languageOf, text, resultText} from "./catalog.js";

const doc = document;
const nodes = new Map();
let panel, latest = null, currentLanguage = languageOf(doc.documentElement.lang);
const labels = new Map();
export const guidanceManager = new GuidanceManager({speech: speechOutput, vibration: tester});

/** Functionality: Create a fixed UI node. Inputs: tag/id/key/parent. Outputs: HTMLElement.
 * Logic: catalog text is textContent, never executable markup; Constraints: no user data or permission request. */
function element(tag, id, key, parent) {
  const node = doc.createElement(tag);
  if (id) { node.id = id; nodes.set(id, node); }
  if (key) { labels.set(node, key); node.textContent = text(key, currentLanguage); }
  parent.append(node);
  return node;
}
/** Functionality: Translate this independent panel. Inputs: current document language. Outputs: labels/results.
 * Logic: catalog handles only local UI, leaves existing i18n and native voice names intact.
 * Constraints: language changes do not request new devices, inference or routes, or select another voice. */
function renderLabels() {
  currentLanguage = languageOf(doc.documentElement.lang);
  cameraApp.processing.querySelector('option[value="local"]').textContent = text("localMode", currentLanguage);
  for (const [node, key] of labels) {
    if (!node.isConnected) labels.delete(node);
    else node.textContent = text(key, currentLanguage);
  }
  nodes.get("local-guidance-language").value = currentLanguage;
  applyVoice();
  if (latest) renderResults(latest);
}
/** Functionality: Render device inventory without making a device choice. Inputs: devices/preserveSelection.
 * Outputs: option list and explicit selected/empty ID. Logic: IDs hidden by permission are not selectable.
 * Constraints: no default device selection, facing fallback or stream startup. */
function populateDevices(devices, preserveSelection = true) {
  const select = nodes.get("local-device"), selected = preserveSelection ? select.value : "";
  const cameras = devices.filter(item => item.kind === "videoinput");
  select.replaceChildren();
  const placeholder = element("option", null, null, select);
  placeholder.value = ""; placeholder.textContent = text("chooseCamera", currentLanguage);
  for (const [index, device] of cameras.filter(item => item.deviceId).entries()) {
    const option = element("option", null, null, select);
    option.value = device.deviceId; option.textContent = device.label || `Camera ${index + 1}`;
  }
  select.value = explicitDeviceId(cameras, selected);
  cameraApp.localDeviceId = select.value;
  if (select.options.length === 1) nodes.get("local-guidance-status").textContent =
    text(cameras.length ? "permissionNeeded" : "noCamera", currentLanguage);
}
/** Functionality: Read available cameras without opening them. Inputs: browser mediaDevices. Outputs: explicit choices.
 * Logic: enumerate only videoinput, preserve a still-present selection, otherwise keep blank.
 * Constraints: no implicit camera selection/fallback, getUserMedia or hidden permission request. */
async function readDevices() {
  try {
    populateDevices(await navigator.mediaDevices.enumerateDevices());
  } catch (error) {
    console.warn("[PRS local MVP] Camera inventory failed", {error_type: error.name});
    nodes.get("local-guidance-status").textContent = text("cameraError", currentLanguage);
  }
}
/** Functionality: Request video permission only after the explicit authorization click.
 * Inputs: idle camera controller and native mediaDevices. Outputs: released temporary stream and unselected readable devices.
 * Logic: adapter stops permission tracks before listing; authorization never starts cameraApp/inference.
 * Constraints: failure is diagnosed without retry/selection/facing fallback; active camera sessions are untouched. */
async function authorizeDevices() {
  if (cameraApp.mode !== "idle") return;
  const button = nodes.get("local-devices-authorize"); button.disabled = true;
  nodes.get("local-device").value = ""; cameraApp.localDeviceId = "";
  try { populateDevices(await authorizeCameraDevices(navigator.mediaDevices), false); }
  catch (error) {
    console.warn("[PRS local MVP] Camera authorization failed", {error_type: error.name});
    nodes.get("local-guidance-status").textContent = text("permissionFailed", currentLanguage);
  } finally { button.disabled = false; }
}
/** Functionality: Read local native voices. Inputs: shared speech inventory/language. Outputs: explicit voice choices.
 * Logic: filter local_service and matching primary language, preserve selection without replacing it.
 * Constraints: no remote/default selection, native speak, automatic channel recovery or network request. */
function readVoices() {
  const select = nodes.get("local-guidance-voice"), selected = select.value;
  const language = nodes.get("local-guidance-language").value;
  const inventory = speechOutput.voiceList();
  select.replaceChildren();
  const placeholder = element("option", null, null, select);
  placeholder.value = ""; placeholder.textContent = text("chooseVoice", currentLanguage);
  for (const voice of inventory.voices.filter(item => item.local_service === true &&
      item.language.split("-")[0].toLowerCase() === language.split("-")[0].toLowerCase())) {
    const option = element("option", null, null, select);
    option.value = voice.id;
    option.textContent = `${voice.name} (${voice.language}) [local]`;
  }
  if (selected && ![...select.options].some(option => option.value === selected)) {
    const missing = element("option", null, null, select);
    missing.value = selected; missing.textContent = `${selected} [unavailable]`; missing.disabled = true;
  }
  select.value = selected;
  if (select.options.length === 1) nodes.get("local-guidance-status").textContent = text("noVoice", currentLanguage);
  applyVoice();
}
/** Functionality: Display one result without re-publishing it. Inputs: camera frame event.
 * Outputs: latest result list/freshness. Logic: source capture age, display at most50 of the full count.
 * Constraints: language refresh never re-infers/replays guidance; stale frames remain visible. */
function renderResults(event) {
  const metadata = event.result.metadata, list = nodes.get("local-results"); list.replaceChildren();
  for (const box of metadata.boxes.slice(0, 50)) element("li", null, null, list).textContent = resultText(box, currentLanguage);
  if (!metadata.boxes.length) element("li", null, null, list).textContent = text("empty", currentLanguage);
  const age = performance.now() - event.capturedAt;
  nodes.get("local-result-status").textContent = `${metadata.count} objects · frame ${metadata.index} · ` +
    text(age >= 0 && age < 1000 ? "fresh" : "stale", currentLanguage);
}
/** Functionality: Route camera events into independent guidance and result rendering. Inputs: structured event.
 * Outputs: result list/state or lifecycle cleanup. Logic: only local provider frames are consumed;
 * capture TTL is carried unchanged, stale detections remain visible.
 * Constraints: no metadata invention, re-score, frame FIFO or replacement capture pipeline. */
function onCamera(event) {
  if (event.type === "session" && event.provider === "local") guidanceManager.begin(event.sessionId);
  else if (event.type === "frame" && event.provider === "local") {
    if (!cameraApp.sessionId || cameraApp.mode !== "camera" ||
        event.generation !== cameraApp.generation) return;
    if (!guidanceManager.sessionId) {
      guidanceManager.report("active_session_missing", {camera_session_id: event.sessionId, frame_index: event.result.metadata.index});
      return;
    }
    if (event.sessionId !== guidanceManager.sessionId) {
      guidanceManager.ingest({sessionId: event.sessionId, capturedAt: event.capturedAt, metadata: event.result.metadata});
      return;
    }
    latest = event;
    const metadata = event.result.metadata;
    guidanceManager.ingest({sessionId: event.sessionId, capturedAt: event.capturedAt, metadata});
    renderResults(event);
  } else if (["stop", "ended", "error"].includes(event.type)) {
    guidanceManager.stop(event.type);
    latest = null;
    nodes.get("local-guidance-status").textContent = text("stopped", currentLanguage);
    nodes.get("local-result-status").textContent = text("stopped", currentLanguage);
    nodes.get("local-device").disabled = false;
  }
}
/** Functionality: Present non-body scheduler diagnostics. Inputs: event. Outputs: local status and structured log.
 * Logic: pauses are visible per channel and request IDs remain inspectable.
 * Constraints: no spoken text, images, keys or hardware-success claims in logs. */
function onGuidance(event) {
  nodes.get("local-guidance-status").textContent = event.phase === "disabled" ? text("disabled", currentLanguage)
    : event.phase === "enabled" ? text("enabled", currentLanguage)
      : `${event.phase}${event.channel ? ` · ${event.channel}` : ""}${event.reason ? ` · ${event.reason}` : ""}${event.request_id ? ` · #${event.request_id}` : ""}`;
  if (event.phase === "stale") nodes.get("local-result-status").textContent = text("stale", currentLanguage);
  for (const channel of ["speech", "vibration"]) nodes.get(`local-resume-${channel}`).disabled = !event.paused[channel];
  if (event.phase !== "frame") console.info("[PRS guidance] State", event);
}
/** Functionality: Apply the user's explicit local voice/language. Inputs: controls. Outputs: manager configuration.
 * Logic: also reflect the exact voice in the manual shared speech UI; Constraints: no implicit enable, replacement or resume. */
function applyVoice() {
  const voiceId = nodes.get("local-guidance-voice").value;
  const language = nodes.get("local-guidance-language").value;
  guidanceManager.setVoice(voiceId, language);
  if (voiceId) { speechOutput.voice.value = voiceId; speechOutput.language.value = language; }
}
/** Functionality: Build local-only controls and subscribe to existing controllers. Inputs: wrapper HTML marker.
 * Outputs: panel and read-only camera/voice setup. Logic: local endpoint explicit, HF/scene/maps actions disabled.
 * Constraints: no automatic media permissions, outputs, API descriptions, real route claims or remote voice. */
async function initialize() {
  if (doc.documentElement.dataset.localMvp !== "true") throw new Error("Local MVP entry requires explicit wrapper marker");
  panel = element("section", "local-mvp", null, doc.querySelector("main"));
  panel.className = "workspace local-mvp"; panel.dataset.languageOriginal = "";
  element("h2", null, "title", panel); element("p", null, "intro", panel);
  const cameraLabel = element("label", null, null, panel);
  element("span", null, "camera", cameraLabel);
  const device = element("select", "local-device", null, cameraLabel);
  element("button", "local-devices-refresh", "refresh", panel).addEventListener("click", readDevices);
  element("button", "local-devices-authorize", "authorizeCamera", panel).addEventListener("click", authorizeDevices);
  device.addEventListener("change", () => { cameraApp.localDeviceId = device.value; });
  const enableLabel = element("label", null, null, panel);
  const enable = element("input", "local-guidance-enabled", null, enableLabel); enable.type = "checkbox";
  element("span", null, "enable", enableLabel);
  enable.addEventListener("change", () => guidanceManager.setEnabled(enable.checked));
  const language = element("select", "local-guidance-language", null, panel);
  for (const [value, label] of [["zh-CN", "中文"], ["en-US", "English"]]) {
    const option = element("option", null, null, language); option.value = value; option.textContent = label;
  }
  language.value = currentLanguage; language.addEventListener("change", readVoices);
  const voiceLabel = element("label", null, null, panel);
  element("span", null, "voice", voiceLabel);
  const voice = element("select", "local-guidance-voice", null, voiceLabel);
  voice.addEventListener("change", applyVoice);
  element("button", "local-voices-refresh", "readVoices", panel).addEventListener("click", readVoices);
  for (const channel of ["speech", "vibration"]) {
    const button = element("button", `local-resume-${channel}`, channel === "speech" ? "resumeSpeech" : "resumeVibration", panel);
    button.disabled = true; button.addEventListener("click", () => guidanceManager.resume(channel));
  }
  element("p", null, "outputNote", panel);
  element("button", "local-navigation-demo", "demo", panel).addEventListener("click", () => {
    const result = guidanceManager.navigationEvent({text: text("demoText", guidanceManager.language),
      language: guidanceManager.language, source: "demo"});
    if (!result.accepted) nodes.get("local-guidance-status").textContent = result.reason;
  });
  element("p", null, "demoNote", panel);
  element("p", "local-guidance-status", "disabled", panel).setAttribute("role", "status");
  element("p", "local-result-status", "waiting", panel).setAttribute("role", "status");
  element("ol", "local-results", null, panel);
  cameraApp.processing.querySelector('option[value="local"]').disabled = false;
  cameraApp.processing.querySelector('option[value="local"]').textContent = text("localMode", currentLanguage);
  cameraApp.processing.querySelector('option[value="hf"]').disabled = true;
  cameraApp.processing.value = "local"; cameraApp.handleProcessingChange();
  doc.getElementById("hf-note").hidden = true;
  doc.getElementById("scene-describe").disabled = true;
  const maps = doc.querySelector('a[href="../maps/"]');
  maps?.setAttribute("aria-disabled", "true"); maps?.addEventListener("click", event => event.preventDefault());
  cameraApp.subscribe(onCamera); guidanceManager.subscribe(onGuidance);
  cameraApp.startButton.addEventListener("click", () => { device.disabled = cameraApp.mode !== "idle"; });
  doc.getElementById("language").addEventListener("change", renderLabels);
  doc.addEventListener("visibilitychange", () => { if (doc.visibilityState !== "visible") guidanceManager.stop("hidden"); });
  window.addEventListener("pagehide", () => guidanceManager.stop("pagehide"));
  readVoices();
  await readDevices();
}
export const localMvpReady = initialize();
