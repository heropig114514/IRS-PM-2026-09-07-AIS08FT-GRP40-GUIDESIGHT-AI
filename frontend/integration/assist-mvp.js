/* Responsibilities: Connect original navigation/MCP, camera or file perception to one output owner.
 * Implementation: Default-enabled unified outputs, interface-language local voice preference,
 * synchronous user-click speech activation and one-click product rear-camera acquisition,
 * canonical shared module URLs, independent source lifecycles, explicit controls,
 * original frame transports and passive maps snapshots; no navigation or algorithm calculations.
 * Related Modules: assist-panel.html/assist.css/assist-guidance.js; maps-navigation-bridge.js,
 * devices.js/video-replay.js/catalog.js/voice-preferences.js; original camera/navigation/speech/vibration singletons.
 * Declaration Index: navigationPort.begin; navigationPort.stop; node; productText; setStatusText; renderControls; renderResults; onSource; onGuidance; applyVoice;
 * renderSpeechActivation; activateSpeech; resumeSpeech; enableDefaultOutputs; startProductCamera; cameraFailureMessage;
 * readVoices; readProductVoices; observeProductLanguage; readDevices; authorizeDevices; selectFile; startVideo; stopPerception; renderStopStatus; stopAll;
 * onBoundary; initialize; guidanceManager; speechActivation; navigationBridge; videoReplay; assistReady.
 * Variable Index: riskOwner/sessionId identify the active perception generation; sourceStarting
 * locks user source choice during permission/admission; fileChoice/permissionChoice invalidate
 * late metadata/permission UI; boundaryStopped prevents repeated lifecycle cancellation;
 * fileReady/latest retain one source state/result only; stoppingAll makes two-source cleanup atomic.
 * Exported controllers are the original instances; navigationPort forwards the existing bridge API;
 * replay href uses its original integration/video base so ../../ws preserves the current application mount.
 * speechActivation gates automatic speech until a real native start; defaults do not acquire sensors.
 * testView retains manual diagnostic choices; voicePreference stores configuration availability;
 * languageObserver watches only the document language and never starts or cancels speech;
 * cameraFailure retains a finite native error type across the core's following stop event.
 */
import {cameraApp} from "../camera.js";
import {navigation} from "../navigation-ui.js";
import {speechOutput} from "../speech-output.js";
import {tester} from "../vibration.js";
import {LiveDetectionClient} from "../live-detection-client.js";
import {FramePipeline} from "../frame-pipeline.js";
import {AssistGuidanceManager} from "./assist-guidance.js";
import {SpeechActivation} from "./speech-activation.js";
import {MapsNavigationSpeechBridge} from "./maps-navigation-bridge.js";
import {VideoReplay} from "./video-replay.js";
import {authorizeCameraDevices, explicitDeviceId} from "./devices.js";
import {resultText} from "./catalog.js";
import {interfaceSpeechLanguage, selectLocalVoice} from "./voice-preferences.js";
export {cameraApp} from "../camera.js";
export {navigation} from "../navigation-ui.js";
export {speechOutput} from "../speech-output.js";

const doc = document;
const testView = doc.documentElement.dataset.assistView === "test";
let voicePreference = {language: null, voiceId: "", reason: "local_voice_unavailable"};
let languageObserver = null;
let cameraFailure = null;
let riskOwner = null, sessionId = null, sourceStarting = null, fileReady = false, latest = null;
let fileChoice = 0, permissionChoice = 0, boundaryStopped = false, stoppingAll = false;
export const guidanceManager = new AssistGuidanceManager({speech: speechOutput, vibration: tester});
export const speechActivation = new SpeechActivation({speech: speechOutput, manager: guidanceManager,
  config: () => ({voiceId: node("navigation-speech-voice").value,
    language: node("navigation-speech-language").value}), onChange: renderSpeechActivation});
const navigationPort = {...guidanceManager.navPort,
  /* Functionality: Forward an explicit navigation run and rearm page cleanup.
   * Inputs: Original bridge run ID. Outputs: Manager state. Logic: UI and MCP share this edge.
   * Constraints: No GPS action, auto navigation or output enablement. */
  begin(runId) { boundaryStopped = false; return guidanceManager.beginNavigation(runId); },
  /* Functionality: Forward navigation-only stops outside an atomic global stop.
   * Inputs: Reason. Outputs: Manager state. Logic: Global cleanup is performed once by manager.stop.
   * Constraints: Does not retry failed cancellation when the bridge consumes the same boundary. */
  stop(reason) { return stoppingAll ? guidanceManager.describe() : guidanceManager.stopNavigation(reason); }};
export const navigationBridge = new MapsNavigationSpeechBridge({navigation, adapter: navigationPort,
  document: null, window: null, events: doc});
export const videoReplay = new VideoReplay({video: doc.getElementById("video-source"),
  href: new URL("../integration/video/", location.href).href,
  canvas: doc.createElement("canvas"), image: doc.getElementById("video-result"),
  clientFactory: (url, failed, ended) => new LiveDetectionClient(url, failed, ended,
    {provider: "local", localSessionSeconds: null}),
  pipelineFactory: options => new FramePipeline(options)});

/* Functionality: Resolve one fixed integration control. Inputs: DOM ID. Outputs: HTMLElement.
 * Logic: All IDs originate in the fixed server-composed template. Constraints: no user selectors. */
function node(id) { return doc.getElementById(id); }

/* Functionality: Present integration product text in the current interface language.
 * Inputs: Fixed Chinese and English strings. Outputs: One plain string.
 * Logic: Existing UI lang remains authoritative. Constraints: No input text translation or speech. */
function productText(chinese, english) {
  return interfaceSpeechLanguage(doc.documentElement.lang) === "en-US" ? english : chinese;
}

/* Functionality: Update an accessible status only when its user-facing text changes.
 * Inputs: Fixed status ID and prepared string. Outputs: At most one textContent mutation.
 * Logic: Lifecycle events and inventory refreshes can describe the same visible state repeatedly.
 * Constraints: Does not suppress distinct errors, alter diagnostic text or trigger speech output. */
function setStatusText(id, text) {
  const target = node(id);
  if (target.textContent !== text) target.textContent = text;
}

/* Functionality: Reflect mutually exclusive perception ownership without affecting navigation.
 * Inputs: Camera/replay states and explicit source choice. Outputs: Disabled/hidden controls.
 * Logic: Lock starts through permission/admission; diagnostics require a selected device while the
 * product explicitly requests the rear-facing camera without device enumeration.
 * Constraints: Does not start/stop media, request permission, select a device or enable outputs. */
function renderControls() {
  const busy = sourceStarting !== null || cameraApp.mode !== "idle" || videoReplay.mode !== "idle";
  const camera = node("assist-source").value === "camera";
  node("assist-source").disabled = busy;
  node("assist-camera-panel").hidden = !camera;
  node("assist-video-panel").hidden = camera;
  node("assist-video-file").disabled = busy;
  node("assist-video-start").disabled = busy || !fileReady || videoReplay.video.ended || camera;
  node("assist-video-stop").disabled = videoReplay.mode === "idle";
  node("assist-device").disabled = busy;
  node("assist-devices-authorize").disabled = busy;
  node("assist-devices-refresh").disabled = busy;
  node("start").disabled = busy || !camera || testView && !cameraApp.localDeviceId;
  if (!testView) node("product-camera-start").disabled = node("start").disabled;
}

/* Functionality: Start environment perception from the product shortcut in the same user gesture.
 * Inputs: One explicit shortcut click. Outputs: One original start-button dispatch and visible views.
 * Logic: Reuse the capture guard, speech activation and existing CameraApp listener synchronously.
 * Constraints: No await before the forwarded click, duplicate acquisition, device fallback or retry. */
function startProductCamera() {
  if (node("start").disabled) return;
  node("assist-camera-panel").scrollIntoView({block: "start"});
  node("start").click();
}

/* Functionality: Explain native camera failures without hiding the controller's detailed message.
 * Inputs: Retained error type. Outputs: Fixed localized action. Logic: Known permission/device
 * failures are distinguished, with a generic message for other connection/start errors.
 * Constraints: No error-body logging, hardware assumption, permission change or automatic retry. */
function cameraFailureMessage(reason) {
  if (reason === "NotAllowedError" || reason === "SecurityError")
    return productText("摄像头权限被拒绝。请在 Safari 网站设置中允许摄像头，然后再次点击开始检测。", "Camera access was denied. Allow camera access in your browser's site settings, then tap Start detection again.");
  if (reason === "NotFoundError" || reason === "OverconstrainedError")
    return productText("未能打开后置摄像头。请使用带后置摄像头的手机，或进入测试页手动选择镜头。", "The rear camera is unavailable. Use a phone with a rear camera, or choose a camera manually on the test page.");
  if (reason === "NotReadableError" || reason === "AbortError")
    return productText("摄像头暂时无法使用。请关闭其他占用摄像头的应用后再次点击开始检测。", "The camera is unavailable. Close other apps using it, then tap Start detection again.");
  return productText("摄像头启动或检测连接失败，请查看提示后重新开始。", "The camera or detection connection failed. Check the message and start again.");
}

/* Functionality: Display existing object metadata without deriving risk or refreshing its age.
 * Inputs: One validated frame. Outputs: Bounded object list and measured capture age.
 * Logic: Display at most 50 objects; only source clocks label freshness.
 * Constraints: No repeated inference, hidden thresholds, metric accuracy or safety claims. */
function renderResults(event) {
  const list = node("assist-results"), metadata = event.result.metadata;
  list.replaceChildren();
  for (const box of metadata.boxes.slice(0, 50)) {
    const item = doc.createElement("li");
    item.textContent = resultText(box, node("navigation-speech-language").value); list.append(item);
  }
  const age = performance.now() - event.capturedAt;
  node("assist-result-status").textContent = `${metadata.count} objects · frame ${metadata.index} · ` +
    `${age.toFixed(0)} ms capture age · ${age >= 0 && age < 1000 ? "fresh" : "stale"}`;
}

/* Functionality: Connect original source events to the independent risk input only.
 * Inputs: Original event and camera/video source label. Outputs: Session/frame/terminal adaptation.
 * Logic: Match generation, live controller and exact session before ingesting a result.
 * Constraints: Perception stops never clear navigation; late or foreign source callbacks are ignored. */
function onSource(event, source) {
  if (event.type === "session") {
    if (event.provider !== "local" || source !== node("assist-source").value) return;
    riskOwner = source; sessionId = event.sessionId; sourceStarting = null; boundaryStopped = false;
    if (source === "camera") cameraFailure = null;
    guidanceManager.beginRisk(sessionId);
    node("assist-status").textContent = source === "video" ? "本地视频检测中；视频位置不代表设备位置。"
      : productText("正在留意周围障碍物。", "Monitoring nearby obstacles.");
  } else if (event.type === "frame") {
    const controller = source === "video" ? videoReplay : cameraApp;
    if (riskOwner !== source || sessionId !== event.sessionId || event.generation !== controller.generation ||
        source === "video" && !videoReplay.active() || source === "camera" && cameraApp.mode !== "camera") return;
    latest = event;
    guidanceManager.ingestRisk({sessionId, capturedAt: event.capturedAt, metadata: event.result.metadata});
    renderResults(event);
  } else if (["stop", "ended", "error"].includes(event.type)) {
    if (source === "camera" && event.type === "error") cameraFailure = event.errorType || "Error";
    if (sourceStarting === source) sourceStarting = null;
    if (riskOwner === source) {
      riskOwner = sessionId = null; latest = null;
      guidanceManager.stopRisk(event.reason || event.type);
      node("assist-status").textContent = testView ? "感知已停止；导航状态保持独立。"
        : productText("环境检测已停止。", "Obstacle detection stopped.");
      node("assist-result-status").textContent = "感知已停止；最后画面仅供查看，不触发提醒。";
    }
    if (source === "video") node("assist-video-status").textContent = event.type === "error"
      ? `视频已停止：${event.message || "请检查本机服务。"}` : "视频会话已停止，需手动开始或重新选择视频。";
    // CameraApp publishes stop before clearing mode; read its completed transition once.
    if (source === "camera") queueMicrotask(renderControls);
  }
  if (!testView && source === "camera" && ["stop", "ended", "error"].includes(event.type))
    setStatusText("assist-status", cameraFailure ? cameraFailureMessage(cameraFailure)
      : productText("环境检测已停止。", "Obstacle detection stopped."));
  if (source === "video") node("assist-stats").textContent = JSON.stringify(videoReplay.stats, null, 2);
  renderControls();
}

/* Functionality: Present product output availability or diagnostic lifecycle and recovery controls.
 * Inputs: Manager event. Outputs: Finite status without logging media, GPS or spoken text.
 * Logic: Product text describes available actions; only the test view exposes request identities.
 * The startup gate uses the activation button; recovery appears only for a separate channel pause.
 * Constraints: Engine acceptance/start/end are not physical speaker or motor verification. */
function onGuidance(event) {
  const state = guidanceManager.describe();
  const diagnostic = `${event.phase}${event.reason ? ` · ${event.reason}` : ""}${event.request_id ? ` · #${event.request_id}` : ""}`;
  const status = testView ? diagnostic : !speechOutput.enabled.checked
    ? productText("语音提醒已关闭。", "Voice alerts are off.")
    : voicePreference.reason
      ? productText("当前语言的语音提醒暂不可用。", "Voice alerts are unavailable for this language.")
    : state.paused?.speech && state.reasons.speech !== "activation_required"
      ? productText("语音提醒已暂停，请点击恢复语音。", "Voice alerts are paused. Tap Resume voice.")
      : !speechActivation.describe().ready
        ? productText("开始导航或环境检测时，将启用语音提醒。", "Starting navigation or detection will activate voice alerts.")
        : productText("语音提醒已就绪。", "Voice alerts are ready.");
  setStatusText("assist-output-status", status);
  setStatusText("navigation-speech-status", status);
  node("assist-resume-speech").disabled = !state.paused?.speech ||
    !testView && state.reasons.speech === "activation_required";
  node("navigation-speech-resume").disabled = !state.paused?.speech;
  node("assist-resume-vibration").disabled = !state.paused?.vibration;
  renderSpeechActivation(speechActivation.describe());
}

/* Functionality: Display genuine speech activation independently of enabled output preferences.
 * Inputs: Activation metadata. Outputs: Accessible status and explicit startup control state.
 * Logic: Only native start marks ready; pending submission never claims sound or sensor access.
 * Constraints: No native calls, automatic retry, voice substitution or hardware verification. */
function renderSpeechActivation(state) {
  const enabled = speechOutput.enabled.checked;
  const output = guidanceManager.describe();
  const gated = output.paused.speech && output.reasons.speech === "activation_required";
  node("assist-speech-start").disabled = !speechOutput.supported || !enabled || state.ready && !gated || state.state === "queued" ||
    !testView && Boolean(voicePreference.reason);
  if (!testView) {
    setStatusText("assist-speech-status", !speechOutput.supported
      ? productText("此浏览器不支持语音提醒，请使用支持语音的浏览器。", "This browser cannot provide voice alerts. Use a browser with speech support.")
      : !enabled ? productText("语音提醒已关闭。", "Voice alerts are off.")
      : voicePreference.reason === "voice_query_failed"
        ? productText("暂时无法读取设备声音，请刷新页面后重试。", "Cannot read device voices. Refresh this page and try again.")
      : voicePreference.reason === "unsupported_interface_language"
        ? productText("当前语言暂不支持语音提醒，请切换中文或英文。", "Voice alerts do not support this language. Choose Chinese or English.")
      : voicePreference.reason
        ? productText("未找到当前语言的本地声音。请在设备设置中安装中文声音，或切换界面语言后再试。", "No local voice matches this language. Install an English voice in device settings, or change the interface language and try again.")
      : state.ready
        ? gated ? productText("点击启动语音，或重新开始导航、检测。", "Tap Activate voice, or start navigation or detection again.")
          : output.paused.speech ? productText("语音提醒已暂停，请点击恢复语音。", "Voice alerts are paused. Tap Resume voice.")
          : productText("语音提醒已就绪。", "Voice alerts are ready.")
      : state.state === "queued" ? productText("正在启用语音提醒。", "Activating voice alerts.")
      : state.state === "error"
        ? productText("语音尚未启动，请保持页面在前台并再次点击启动语音。", "Voice has not started. Keep this page open and tap Activate voice again.")
        : productText("语音跟随界面语言。点击启动语音，或开始导航、检测。", "Voice follows the interface language. Tap Activate voice, or start navigation or detection."));
    return;
  }
  node("assist-speech-status").textContent = !enabled ? "语音输出已关闭。" : state.ready
    ? gated ? "语音已停止；点击“启动语音”或开始导航、检测后恢复。"
      : output.paused.speech ? `语音引擎已启动，但自动播报已暂停（${output.reasons.speech}），请手动恢复输出。`
      : "语音引擎已启动，导航和预警可自动播报；请确认设备实际声音。"
    : state.state === "queued" ? "正在启动语音，请等待提示音。"
    : `语音提醒已开启，尚未启动（${state.reason || "activation_required"}）。选择本地声音后，点击“启动语音”或开始导航、检测。`;
}

/* Functionality: Start the first utterance directly within the current real click stack.
 * Inputs: Explicit startup, navigation or perception click. Outputs: Activation acknowledgement.
 * Logic: Delegate synchronously before any permission promise or inference callback.
 * Constraints: No await, task deferral, automatic enabling, device selection or playback retry. */
function activateSpeech() {
  boundaryStopped = false;
  if (!testView) readProductVoices();
  const result = speechActivation.activate();
  renderSpeechActivation(speechActivation.describe());
  return result;
}

/* Functionality: Respect first-speech activation when manually resuming automatic output.
 * Inputs: Explicit recovery click. Outputs: Startup or manager recovery result.
 * Logic: An unstarted engine must first receive synchronous calibration, not asynchronous risk.
 * Constraints: Existing unrelated output errors still require the manager's explicit recovery. */
function resumeSpeech() {
  return speechActivation.describe().ready ? guidanceManager.resume("speech") : activateSpeech();
}

/* Functionality: Apply the user's default-on policy only to this unified MVP document.
 * Inputs: Original shared controller instances and existing switches. Outputs: Enabled intentions.
 * Logic: Existing render handlers preserve unsupported states; activation gate precedes scheduling.
 * Constraints: No speech, vibration, camera, GPS or planning request is made by initialization. */
function enableDefaultOutputs() {
  speechOutput.enabled.checked = speechOutput.supported;
  speechOutput.handleEnable();
  node("navigation-speech-enabled").checked = true;
  node("assist-risk-enabled").checked = true;
  if (tester) { tester.enabled.checked = tester.supported; tester.handleEnable(); }
  guidanceManager.setNavigationEnabled(true);
  guidanceManager.setRiskEnabled(true);
  renderSpeechActivation(speechActivation.describe());
}

/* Functionality: Apply the selected manual or product voice to both automatic sources.
 * Inputs: Shared integration voice/language. Outputs: Manager/manual UI configuration.
 * Logic: Preserve exact native IDs, including empty unavailable selection.
 * Constraints: No output, enablement, readiness reset or current-utterance cancellation. */
function applyVoice() {
  const voiceId = node("navigation-speech-voice").value, language = node("navigation-speech-language").value;
  guidanceManager.configure({voiceId, language});
  speechOutput.voice.value = voiceId; speechOutput.language.value = language;
}

/* Functionality: Configure automatic product speech for the existing interface language.
 * Inputs: Native inventory, document lang and current integration selection. Outputs: Future settings.
 * Logic: Rank matching local voices; a queued calibration retains its exact selection until native
 * start so a concurrent inventory/language change cannot invalidate its startup confirmation.
 * Constraints: No engine output, cancellation, readiness reset, remote voice or automatic retry. */
function readProductVoices() {
  if (["感知尚未开始。", "Obstacle detection has not started."].includes(node("assist-status").textContent))
    setStatusText("assist-status", productText("感知尚未开始。", "Obstacle detection has not started."));
  if (speechActivation.describe().state === "queued") return;
  const language = interfaceSpeechLanguage(doc.documentElement.lang);
  const inventory = speechOutput.voiceList();
  const voiceId = inventory.accepted && language
    ? selectLocalVoice(inventory.voices, language, node("navigation-speech-voice").value) : "";
  voicePreference = {language, voiceId, reason: !language ? "unsupported_interface_language"
    : !inventory.accepted ? inventory.reason || "voice_query_failed" : !voiceId ? "local_voice_unavailable" : null};
  const select = node("navigation-speech-voice");
  const option = doc.createElement("option"); option.value = voiceId;
  option.textContent = voiceId ? productText("跟随界面语言", "Follow interface language")
    : productText("本地声音不可用", "Local voice unavailable");
  select.replaceChildren(option); select.value = voiceId;
  node("navigation-speech-language").value = language || "";
  speechOutput.rate.value = "2"; speechOutput.volume.value = "1";
  applyVoice(); speechOutput.render(); onGuidance({phase: "configuration_changed"});
  if (latest) renderResults(latest);
}

/* Functionality: Follow UI/system language changes and delayed native voice availability.
 * Inputs: Product document and original driver lifecycle. Outputs: Future selection refreshes.
 * Logic: Observe only lang; a native start releases a deferred calibration selection update.
 * Constraints: Voice inventory events never speak, resume the manager, cancel or reset readiness. */
function observeProductLanguage() {
  languageObserver = new MutationObserver(() => readProductVoices());
  languageObserver.observe(doc.documentElement, {attributes: true, attributeFilter: ["lang"]});
  speechOutput.subscribe(event => {
    if (["start", "end", "cancel", "error"].includes(event.phase) && speechActivation.describe().state !== "queued")
      readProductVoices();
  });
}

/* Functionality: Refresh product preferences or the diagnostic page's manual voice inventory.
 * Inputs: Native voice inventory and explicit view marker. Outputs: Options or unavailable state.
 * Logic: Diagnostic mode preserves its empty initial choice; product mode follows interface language.
 * Constraints: No remote/default speech, core setting change, playback or automatic error recovery. */
function readVoices() {
  if (!testView) { readProductVoices(); return; }
  const select = node("navigation-speech-voice"), selected = select.value;
  const language = node("navigation-speech-language").value;
  select.replaceChildren();
  const empty = doc.createElement("option"); empty.value = ""; empty.textContent = "请选择本地声音"; select.append(empty);
  for (const voice of speechOutput.voiceList().voices.filter(item => item.local_service === true &&
      item.language.split("-")[0].toLowerCase() === language.split("-")[0].toLowerCase())) {
    const option = doc.createElement("option"); option.value = voice.id;
    option.textContent = `${voice.name} (${voice.language}) [local]`; option.dataset.languageOriginal = ""; select.append(option);
  }
  if (selected && ![...select.options].some(option => option.value === selected)) {
    const missing = doc.createElement("option"); missing.value = selected; missing.disabled = true;
    missing.textContent = `${selected} [unavailable]`; select.append(missing);
  }
  select.value = selected; applyVoice();
  if (latest) renderResults(latest);
}

/* Functionality: Enumerate explicit camera choices without requesting permission.
 * Inputs: Optional inventory after an authorization action. Outputs: Preserved valid selection.
 * Logic: IDs absent before permission remain unselectable.
 * Constraints: No first-camera selection, facing fallback, media start or inventory logging. */
async function readDevices(devices) {
  const select = node("assist-device"), selected = select.value, choice = permissionChoice;
  try {
    const inventory = Array.isArray(devices) ? devices : await navigator.mediaDevices.enumerateDevices();
    if (choice !== permissionChoice) return;
    select.replaceChildren();
    const empty = doc.createElement("option"); empty.value = ""; empty.textContent = "请明确选择摄像头"; select.append(empty);
    for (const [index, device] of inventory.filter(item => item.kind === "videoinput" && item.deviceId).entries()) {
      const option = doc.createElement("option"); option.value = device.deviceId;
      option.textContent = device.label || `Camera ${index + 1}`; select.append(option);
    }
    select.value = explicitDeviceId(inventory, selected); cameraApp.localDeviceId = select.value;
  } catch (error) {
    if (choice === permissionChoice) node("assist-status").textContent = testView
      ? `读取摄像头失败（${error.name}），请检查浏览器权限。`
      : productText("无法读取摄像头，请检查浏览器的摄像头权限。", "Cannot list cameras. Check the browser's camera permission.");
  }
  renderControls();
}

/* Functionality: Request only camera authorization after a user click.
 * Inputs: Native mediaDevices. Outputs: Released temporary tracks and readable inventory.
 * Logic: Generation guards reject late permission UI after stop/hide.
 * Constraints: Does not choose a camera or start inference; no microphone or retry. */
async function authorizeDevices() {
  const choice = ++permissionChoice;
  boundaryStopped = false; sourceStarting = "permission"; renderControls();
  try {
    const devices = await authorizeCameraDevices(navigator.mediaDevices);
    if (choice !== permissionChoice) return;
    await readDevices(devices);
  } catch (error) {
    if (choice === permissionChoice) node("assist-status").textContent = testView ? `摄像头授权失败（${error.name}）。`
      : productText("无法访问摄像头，请检查设备和浏览器权限。", "Cannot access the camera. Check the device and browser permissions.");
  }
  finally { if (choice === permissionChoice) { sourceStarting = null; renderControls(); } }
}

/* Functionality: Load an explicitly selected local video without playback or upload.
 * Inputs: File chooser. Outputs: Metadata-ready source or diagnosed failure.
 * Logic: Preserve newer user choices when an older read settles late.
 * Constraints: No default sample, decode fallback, seek, loop or automatic inference. */
async function selectFile() {
  const file = node("assist-video-file").files[0]; if (!file) return;
  const choice = ++fileChoice; boundaryStopped = false; fileReady = false;
  try {
    const operation = videoReplay.loadFile(file); renderControls(); await operation;
    if (choice !== fileChoice) return;
    fileReady = true; node("assist-video-file").value = "";
    node("assist-video-status").textContent = `${file.name} · ${videoReplay.video.duration.toFixed(2)} s；点击开始视频检测。`;
  } catch (error) { if (choice === fileChoice) node("assist-video-status").textContent = `视频未就绪：${error.message}`; }
  renderControls();
}

/* Functionality: Begin one user-requested video perception run.
 * Inputs: Explicit source choice and loaded file. Outputs: Existing PRF2 replay session.
 * Logic: Lock other sources before awaiting admission. Constraints: No camera permission or auto reconnect. */
async function startVideo() {
  if (node("assist-source").value !== "video" || cameraApp.mode !== "idle" || sourceStarting) return;
  activateSpeech();
  sourceStarting = "video"; boundaryStopped = false; renderControls();
  try { await videoReplay.start(); }
  catch (error) { node("assist-video-status").textContent = `未开始：${error.message}`; }
  finally { if (sourceStarting === "video") sourceStarting = null; renderControls(); }
}

/* Functionality: Stop perception and its owned output while preserving navigation.
 * Inputs: Explicit reason and stopOutput flag (false only inside atomic global cleanup).
 * Outputs: Invalidated source callbacks and stopped media.
 * Logic: Detach session ownership before controller stop events are published.
 * Constraints: Each explicit stop attempts cancellation once; source events cannot repeat it. */
function stopPerception(reason = "user", stopOutput = true) {
  permissionChoice++; fileChoice++; riskOwner = sessionId = sourceStarting = null; latest = null;
  cameraFailure = null;
  if (stopOutput) guidanceManager.stopRisk(reason);
  videoReplay.stop(reason);
  if (cameraApp.mode !== "idle") cameraApp.stop();
  node("assist-status").textContent = testView ? "感知已停止；导航状态保持独立。"
    : productText("环境检测已停止。", "Obstacle detection stopped.");
  node("assist-result-status").textContent = "感知已停止；最后画面不触发提醒。";
  renderControls();
}

/* Functionality: Report whether the existing controllers have completed local cleanup.
 * Inputs: Whether the explicit cleanup calls returned without throwing, plus controller snapshots.
 * Outputs: Confirmed stopped state or an actionable uncertainty message.
 * Logic: Check inputs, native request records and retained output ownership; historical errors alone
 * cannot imply an active resource. Constraints: No cancellation retry or physical-silence claim. */
function renderStopStatus(cleanupCompleted) {
  const navigationState = navigation.describe(), speech = speechOutput.describe();
  const output = guidanceManager.describe(), activation = speechActivation.describe();
  const stopped = cleanupCompleted && cameraApp.mode === "idle" && cameraApp.stream === null &&
    videoReplay.mode === "idle" && !navigationState.active && navigationState.reason !== "stop_failed" &&
    !speech.active_request && !speech.engine_speaking && !speech.engine_pending &&
    !output.active.speech && !output.active.vibration && !activation.owned_request_id &&
    !activation.cancel_failed && !tester?.describe().request_pending;
  setStatusText("assist-status", stopped
    ? productText("导航与环境检测已停止。", "Navigation and obstacle detection stopped.")
    : productText("尚未确认全部停止。请再次点击停止；若仍未停止，请关闭页面。", "Cannot confirm everything has stopped. Tap Stop again; if it persists, close this page."));
}

/* Functionality: Stop the two inputs and owned outputs on an explicit all-stop action.
 * Inputs: Reason and whether original navigation should also be stopped.
 * Outputs: Invalidated speech bridge, perception and optionally original GPS watch.
 * Logic: Clear bridge ownership before core stop notifications; preserve original stop-failure diagnostics.
 * Constraints: No global native speech cancellation, automatic cleanup retry or automatic restart. */
function stopAll(reason = "user", stopNavigation = true) {
  let cleanupCompleted = false;
  try {
    stoppingAll = true;
    try {
      navigationBridge.stop(reason);
      guidanceManager.stop(reason);
      speechActivation.stop();
      stopPerception(reason, false);
    } finally { stoppingAll = false; }
    if (stopNavigation && (navigation.active || navigation.describe().reason === "stop_failed")) navigation.stop("user");
    cleanupCompleted = true;
  } finally {
    if (testView) node("assist-status").textContent = "已请求停止全部；请查看导航和输出状态确认清理结果。";
    else {
      renderStopStatus(cleanupCompleted);
      // Native page lifecycle handlers stop original GPS after this capture listener returns.
      // Re-read only; a new explicit session clears boundaryStopped and keeps its newer status.
      if (!stopNavigation && cleanupCompleted) queueMicrotask(() => {
        if (boundaryStopped) renderStopStatus(true);
      });
    }
  }
}

/* Functionality: Consume one hidden/leave boundary before ordinary source callbacks.
 * Inputs: Native lifecycle event. Outputs: One integrated cleanup request.
 * Logic: Original navigation UI remains responsible for GPS lifecycle cleanup.
 * Constraints: Returning to the foreground never resumes sources or replays speech. */
function onBoundary(event) {
  if (event.type === "visibilitychange" && doc.visibilityState === "visible" || boundaryStopped) return;
  boundaryStopped = true; stopAll(event.type === "pagehide" ? "page_left" : "page_hidden", false);
}

/* Functionality: Bind the dedicated local integration surface to original controllers.
 * Inputs: Explicit assist document marker. Outputs: Read-only setup and user action handlers.
 * Logic: Select only local PRF2, reuse shared drivers; product speech follows the interface language.
 * Constraints: No automatic permissions, route requests, inference or first playback. */
function initialize() {
  if (doc.documentElement.dataset.assist !== "true" || !navigation)
    throw new Error("Unified assist entry requires its explicit maps integration template.");
  cameraApp.localSessionSeconds = null;
  if (!testView) {
    cameraApp.localDeviceId = "";
    cameraApp.localFacingMode = "environment";
    node("product-camera-start").addEventListener("click", startProductCamera);
  }
  cameraApp.processing.value = "local"; cameraApp.handleProcessingChange();
  node("maps-navigation-speech-panel").querySelector("h2").textContent = "导航与风险播报声音";
  guidanceManager.subscribe(onGuidance);
  cameraApp.subscribe(event => onSource(event, "camera"));
  videoReplay.subscribe(event => onSource(event, "video"));
  node("navigation-speech-enabled").addEventListener("change", () => guidanceManager.setNavigationEnabled(node("navigation-speech-enabled").checked));
  node("assist-risk-enabled").addEventListener("change", () => guidanceManager.setRiskEnabled(node("assist-risk-enabled").checked));
  node("navigation-speech-language").addEventListener("change", readVoices);
  node("navigation-speech-voice").addEventListener("change", applyVoice);
  node("navigation-voices-refresh").addEventListener("click", readVoices);
  node("navigation-speech-stop").disabled = false;
  node("navigation-speech-stop").addEventListener("click", () => navigationBridge.stop("speech_stop"));
  node("navigation-stop").addEventListener("click", () => navigationBridge.stop("navigation_stop"), true);
  node("navigation-start").addEventListener("click", activateSpeech, true);
  for (const id of ["navigation-speech-resume", "assist-resume-speech"])
    node(id).addEventListener("click", resumeSpeech);
  node("assist-speech-start").addEventListener("click", activateSpeech);
  speechOutput.enabled.addEventListener("change", () => renderSpeechActivation(speechActivation.describe()));
  node("assist-resume-vibration").addEventListener("click", () => guidanceManager.resume("vibration"));
  node("assist-source").addEventListener("change", renderControls);
  node("assist-device").addEventListener("change", () => { cameraApp.localDeviceId = node("assist-device").value; renderControls(); });
  node("assist-devices-refresh").addEventListener("click", () => {
    boundaryStopped = false; permissionChoice++; void readDevices();
  });
  node("assist-devices-authorize").addEventListener("click", authorizeDevices);
  node("assist-video-file").addEventListener("change", selectFile);
  node("assist-video-start").addEventListener("click", startVideo);
  node("assist-video-stop").addEventListener("click", () => stopPerception("user"));
  node("start").addEventListener("click", event => {
    if (node("assist-source").value !== "camera" || videoReplay.mode !== "idle" || sourceStarting) {
      event.preventDefault(); event.stopImmediatePropagation(); return;
    }
    activateSpeech();
    cameraFailure = null;
    if (!testView) {
      setStatusText("assist-status", productText("正在开启后置摄像头，请允许摄像头权限。", "Opening the rear camera. Please allow camera access."));
      node("message").textContent = "";
    }
    sourceStarting = "camera"; boundaryStopped = false; renderControls();
  }, true);
  node("assist-stop-all").addEventListener("click", () => stopAll());
  doc.addEventListener("visibilitychange", onBoundary, true);
  window.addEventListener("pagehide", onBoundary, true);
  window.speechSynthesis?.addEventListener("voiceschanged", readVoices);
  if (!testView) observeProductLanguage();
  enableDefaultOutputs(); readVoices(); renderControls();
}

export const assistReady = Promise.resolve().then(initialize);
