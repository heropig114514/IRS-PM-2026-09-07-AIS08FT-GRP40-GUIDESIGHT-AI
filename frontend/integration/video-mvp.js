/** Responsibilities: Bind independent local-video replay, results and shared native outputs to a simple UI.
 * Implementation: Explicit File/start/stop, lifecycle subscriptions, short class/motion alert speech with started-sentence completion,
 * local voice choices and existing demo controls.
 * Related Modules: video.html/video.css/video-replay.js/guidance.js/catalog.js/video-alerts.js;
 * existing /live/assets FramePipeline/LiveDetectionClient/SpeechOutput/VibrationTester single module URLs.
 * Declaration Index: node; renderControls; renderStats; renderResults; onReplay; onGuidance;
 * applyVoice; readVoices; selectFile; startVideo; initialize; videoReplay; guidanceManager; videoMvpReady.
 * Variable Index: doc is the current page; latest is one result, fileReady tracks explicit metadata success;
 * fileChoiceGeneration prevents late cancelled metadata promises from changing a newer user selection;
 * exported controllers provide event/stats inspection without DOM polling; no camera singleton is imported.
 */
import {LiveDetectionClient} from "../../live/assets/live-detection-client.js";
import {FramePipeline} from "../../live/assets/frame-pipeline.js";
import {speechOutput} from "../../live/assets/speech-output.js";
import {tester} from "../../live/assets/vibration.js";
import {VideoReplay} from "./video-replay.js";
import {GuidanceManager} from "./guidance.js";
import {selectVideoAlert, videoAlertText} from "./video-alerts.js";
import {text, resultText} from "./catalog.js";
const doc = document;
let latest = null, fileReady = false, fileChoiceGeneration = 0;
export const guidanceManager = new GuidanceManager({speech: speechOutput, vibration: tester,
  selectAlert: selectVideoAlert, alertText: videoAlertText, finishStartedRiskSpeech: true});
export const videoReplay = new VideoReplay({video: doc.getElementById("video-source"), canvas: doc.createElement("canvas"),
  image: doc.getElementById("video-result"),
  clientFactory: (url, failed, ended) => new LiveDetectionClient(url, failed, ended, {provider: "local"}),
  pipelineFactory: options => new FramePipeline(options)});

/** Functionality: Resolve a fixed control. Inputs: ID. Outputs: current HTMLElement.
 * Logic: page owns stable IDs; Constraints: no user text as selector or module singleton duplication. */
function node(id) { return doc.getElementById(id); }
/** Functionality: Reflect explicit source lifecycle. Inputs: controller mode/file readiness. Outputs: disabled states.
 * Logic: file/start stay locked through EOF drain; explicit stop remains available.
 * Constraints: no button click, playback, seek, permissions or provider choice is synthesized. */
function renderControls() {
  const idle = videoReplay.mode === "idle";
  node("video-file").disabled = !idle;
  node("video-start").disabled = !idle || !fileReady || videoReplay.video.ended;
  node("video-stop").disabled = idle;
}
/** Functionality: Present source and pipeline counts separately. Inputs: controller stats. Outputs: bounded diagnostic JSON.
 * Logic: source callbacks/PTS skips are distinct from encoded/sent/delivered; Constraints: no media pixels or local file paths. */
function renderStats() { node("video-stats").textContent = JSON.stringify(videoReplay.stats, null, 2); }
/** Functionality: Display original risk/distance/track fields without generating alerts. Inputs: one completed event.
 * Outputs: latest object list and source/capture timing. Logic: at most50 UI rows, full metadata remains in event.
 * Constraints: stale frames remain visible; sourceMediaTime is never a capture timestamp. */
function renderResults(event) {
  const language = node("video-guidance-language").value, metadata = event.result.metadata;
  const list = node("video-results"); list.replaceChildren();
  for (const box of metadata.boxes.slice(0, 50)) {
    const item = doc.createElement("li"); item.textContent = resultText(box, language); list.append(item);
  }
  if (!metadata.boxes.length) { const item = doc.createElement("li"); item.textContent = text("empty", language); list.append(item); }
  const age = performance.now() - event.capturedAt;
  node("video-result-status").textContent = `frame ${metadata.index} · source PTS ${event.sourceMediaTime.toFixed(3)} s · ` +
    `${event.timing.captureToDecodedMs.toFixed(1)} ms capture→decode · ${text(age >= 0 && age < 1000 ? "fresh" : "stale", language)}`;
}
/** Functionality: Connect replay lifecycle to independent guidance. Inputs: typed source event.
 * Outputs: session/risk ingestion, UI state, terminal owned-output cleanup.
 * Logic: generation/session guard survives late native callbacks; session begins even while guidance is disabled.
 * Constraints: errors are diagnosed rather than disguised as inactive frames; no new source or automatic output enablement. */
function onReplay(event) {
  if (event.type === "loaded") {
    latest = null; node("video-results").replaceChildren(); node("video-result").hidden = true;
    node("video-result-status").textContent = "已选择新的来源，等待检测结果。";
  } else if (event.type === "session") { guidanceManager.begin(event.sessionId); node("video-status").textContent = "本地CUDA会话已建立，视频以1倍速播放。"; }
  else if (event.type === "frame" && videoReplay.active() && event.generation === videoReplay.generation) {
    if (!guidanceManager.sessionId) { guidanceManager.report("active_session_missing", {video_session_id: event.sessionId}); return; }
    latest = event; guidanceManager.ingest({sessionId: event.sessionId, capturedAt: event.capturedAt, metadata: event.result.metadata});
    renderResults(event);
  } else if (["stop", "ended", "error"].includes(event.type)) {
    guidanceManager.stop(event.reason || event.type);
    node("video-result-status").textContent = "会话已停止；最后结果仅保留显示，不触发提醒。";
    node("video-status").textContent = event.type === "error" ? `已停止：${event.message}` :
      event.type === "ended" ? `会话结束（${event.reason}），已发送/交付 ${event.stats.sent}/${event.stats.delivered}；需手动开始。` : `已停止（${event.reason}）。`;
    node("video-status").dataset.kind = event.type === "error" ? "error" : "idle";
  }
  renderControls(); renderStats();
}
/** Functionality: Show non-body guidance lifecycle. Inputs: diagnosed scheduler event. Outputs: status/recovery/log.
 * Logic: pause is per channel; Constraints: speech submission/start and vibration API acceptance do not imply hardware success. */
function onGuidance(event) {
  node("video-guidance-status").textContent = `${event.phase}${event.channel ? ` · ${event.channel}` : ""}${event.reason ? ` · ${event.reason}` : ""}${event.request_id ? ` · #${event.request_id}` : ""}`;
  for (const channel of ["speech", "vibration"]) node(`video-resume-${channel}`).disabled = !event.paused[channel];
  if (event.phase !== "frame") console.info("[PRS video guidance] State", event);
}
/** Functionality: Apply explicit matching local voice. Inputs: voice/language controls. Outputs: guidance and manual speech choice.
 * Logic: reuse native singleton exact identity; Constraints: no default/remote choice, enablement, retry or error recovery. */
function applyVoice() {
  const voiceId = node("video-guidance-voice").value, language = node("video-guidance-language").value;
  guidanceManager.setVoice(voiceId, language);
  if (voiceId) { speechOutput.voice.value = voiceId; speechOutput.language.value = language; }
}
/** Functionality: Read local voices while preserving explicit choice. Inputs: native inventory and requested language.
 * Outputs: filtered choices and unavailable selected placeholder. Logic: primary language must match.
 * Constraints: unavailable choices are not replaced or auto-enabled; this does not speak or fetch an external voice. */
function readVoices() {
  const select = node("video-guidance-voice"), selected = select.value, language = node("video-guidance-language").value;
  select.replaceChildren();
  const empty = doc.createElement("option"); empty.value = ""; empty.textContent = text("chooseVoice", language); select.append(empty);
  for (const voice of speechOutput.voiceList().voices.filter(voice => voice.local_service === true &&
      voice.language.split("-")[0].toLowerCase() === language.split("-")[0].toLowerCase())) {
    const option = doc.createElement("option"); option.value = voice.id; option.textContent = `${voice.name} (${voice.language}) [local]`; select.append(option);
  }
  if (selected && ![...select.options].some(option => option.value === selected)) {
    const missing = doc.createElement("option"); missing.value = selected; missing.textContent = `${selected} [unavailable]`; missing.disabled = true; select.append(missing);
  }
  select.value = selected; applyVoice();
  if (select.options.length === 1) node("video-guidance-status").textContent = text("noVoice", language);
  if (latest) renderResults(latest);
}
/** Functionality: Load the user's selected local File. Inputs: change event. Outputs: metadata or explicit failure UI.
 * Logic: successful choice clears picker value so the same file may be selected again; cancel keeps current source.
 * Constraints: no automatic playback/rewind/inference; original video stays in browser Blob storage. */
async function selectFile() {
  const file = node("video-file").files[0]; if (!file) return;
  const choice = ++fileChoiceGeneration;
  fileReady = false; renderControls();
  node("video-status").textContent = "正在读取本机视频元数据。";
  try {
    const operation = videoReplay.loadFile(file); renderControls(); await operation;
    if (choice !== fileChoiceGeneration) return;
    fileReady = true; latest = null;
    node("video-file").value = "";
    node("video-status").textContent = `已选择 ${file.name} · ${videoReplay.video.duration.toFixed(2)} s；点击开始检测。`;
    node("video-status").dataset.kind = "idle";
  } catch { if (choice === fileChoiceGeneration) fileReady = false; }
  renderControls();
}
/** Functionality: Begin one explicit replay session. Inputs: click. Outputs: connect/play or validation message.
 * Logic: early asynchronous state locks controls, faults keep their original diagnostic.
 * Constraints: no retry, media permissions or source seek when restarting a partially played file. */
async function startVideo() {
  try { const operation = videoReplay.start(); renderControls(); await operation; }
  catch (error) { node("video-status").textContent = `未开始：${error.message}`; node("video-status").dataset.kind = "error"; }
  renderControls();
}
/** Functionality: Wire the independent page. Inputs: explicit local-MVP marker. Outputs: event handlers and native inventory.
 * Logic: stop/hidden/pagehide cancels source and guidance-owned work; no automatic resume.
 * Constraints: no cameraApp/scene/maps/AI-control import, media permission request, original-file upload or external API operation. */
function initialize() {
  if (doc.documentElement.dataset.localMvp !== "true") throw new Error("Video MVP requires the local entry marker.");
  videoReplay.subscribe(onReplay); guidanceManager.subscribe(onGuidance);
  node("video-file").addEventListener("change", selectFile);
  node("video-start").addEventListener("click", startVideo);
  node("video-stop").addEventListener("click", () => videoReplay.stop());
  node("video-guidance-enabled").addEventListener("change", () => guidanceManager.setEnabled(node("video-guidance-enabled").checked));
  node("video-guidance-language").addEventListener("change", readVoices);
  node("video-guidance-voice").addEventListener("change", applyVoice);
  node("video-voices-refresh").addEventListener("click", readVoices);
  for (const channel of ["speech", "vibration"]) node(`video-resume-${channel}`).addEventListener("click", () => guidanceManager.resume(channel));
  node("video-navigation-demo").addEventListener("click", () => {
    const result = guidanceManager.navigationEvent({text: text("demoText", guidanceManager.language), language: guidanceManager.language, source: "demo"});
    if (!result.accepted) node("video-guidance-status").textContent = result.reason;
  });
  doc.addEventListener("visibilitychange", () => { if (doc.visibilityState !== "visible") videoReplay.stop("hidden"); });
  window.addEventListener("pagehide", () => { videoReplay.dispose(); guidanceManager.dispose(); });
  readVoices(); renderControls();
}
export const videoMvpReady = Promise.resolve(initialize());
