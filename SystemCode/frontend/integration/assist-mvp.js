/* Responsibilities: Connect original navigation/MCP, camera or file perception to one output owner.
 * Implementation: Default-enabled unified outputs, one backend bilingual voice preference,
 * synchronous user-gesture audio unlock, prioritized accepted assistant reply speech and
 * one-click product rear-camera acquisition,
 * canonical shared module URLs, independent source lifecycles, explicit controls and a diagnostic-only idle transport check,
 * original frame transports and passive maps snapshots; no navigation or algorithm calculations.
 * Related Modules: assist-panel.html/assist.css/assist-guidance.js; maps-navigation-bridge.js,
 * devices.js/video-replay.js/catalog.js/voice-preferences.js; original camera/navigation/speech/vibration singletons.
 * Declaration Index: navigationPort.begin; navigationPort.stop; node; productText; setStatusText; renderControls; renderResults; onSource; guidanceDiagnostic; onGuidance; applyVoice;
 * renderSpeechActivation; activateSpeech; resumeSpeech; enableDefaultOutputs; startProductCamera; cameraFailureMessage;
 * readVoices; readProductVoices; observeProductLanguage; readDevices; authorizeDevices; selectFile; startVideo; stopPerception; renderStopStatus; stopAll;
 * onBoundary; speakAssistantReply; initialize; guidanceManager; speechActivation; navigationBridge; videoReplay; assistReady.
 * Variable Index: riskOwner/sessionId identify the active perception generation; sourceStarting
 * locks user source choice during permission/admission; fileChoice/permissionChoice invalidate
 * late metadata/permission UI; boundaryStopped prevents repeated lifecycle cancellation;
 * fileReady/latest retain one source state/result only; stoppingAll makes two-source cleanup atomic.
 * Exported controllers are the original instances; navigationPort forwards the existing bridge API;
 * replay href uses its original integration/video base so ../../ws preserves the current application mount.
 * speechActivation gates automatic speech until the backend audio stream starts; guidanceManager queues accepted
 * replies behind risk alerts and ahead of navigation; activation-gated replies remain queued until
 * an explicit audio start; defaults do not acquire sensors.
 * testView retains localized manual diagnostic choices; voicePreference stores backend TTS availability;
 * languageObserver watches only the document language and never starts or cancels speech;
 * cameraFailure retains a finite native error type across the core's following stop event;
 * GUIDANCE_PHASE_LABELS provides bilingual lifecycle labels to guidanceDiagnostic.
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
import {interfaceSpeechLanguage} from "./voice-preferences.js";
export {cameraApp} from "../camera.js";
export {navigation} from "../navigation-ui.js";
export {speechOutput} from "../speech-output.js";

const doc = document;
const testView = doc.documentElement.dataset.assistView === "test";
/* Functionality: Map internal guidance lifecycle codes to fixed bilingual diagnostic labels.
 * Inputs: The finite phase values emitted by AssistGuidanceManager and configuration updates.
 * Outputs: Chinese/English label pairs consumed by guidanceDiagnostic.
 * Logic: Keep transport and engine identifiers out of user-facing status copy.
 * Constraints: Unknown phases use a generic label; this table does not alter event state. */
const GUIDANCE_PHASE_LABELS = {
  paused: ["输出已暂停", "Output paused"],
  suppressed: ["输出因优先级而暂缓", "Output deferred by priority"],
  resumed: ["输出已恢复", "Output resumed"],
  risk_enabled: ["风险提醒设置已更新", "Risk alert setting updated"],
  navigation_enabled: ["导航播报设置已更新", "Navigation announcement setting updated"],
  risk_session: ["风险检测会话已启动", "Risk detection session started"],
  risk_stopped: ["风险检测已停止", "Risk detection stopped"],
  navigation_run: ["导航播报已启动", "Navigation announcements started"],
  navigation_stopped: ["导航播报已停止", "Navigation announcements stopped"],
  output_cancel: ["输出已取消", "Output cancelled"],
  output_submitted: ["输出已提交", "Output submitted"],
  output_start: ["输出已开始", "Output started"],
  output_end: ["输出已结束", "Output ended"],
  output_error: ["输出失败", "Output failed"],
  pattern_elapsed: ["振动模式已完成", "Vibration pattern completed"],
  stopped: ["导航、风险与助手输出已停止", "Navigation, risk and assistant outputs stopped"],
  configuration_changed: ["语音配置已更新", "Voice configuration updated"],
  assistant_replies_cleared: ["待播报的助手回复已清除", "Queued assistant replies cleared"],
};
let voicePreference = {language: null, voiceId: "Cherry", reason: "backend_tts_unavailable"};
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

/* Functionality: Render one guidance event as a localized, readable test status.
 * Inputs: Manager event with phase and optional channel, enabled, reason, and request_id fields.
 * Outputs: A concise status string in the current interface language.
 * Logic: Resolve phase/channel labels, expose boolean setting state, and retain useful request context.
 * Constraints: Never display raw phase identifiers; reason details are diagnostic codes only. */
function guidanceDiagnostic(event) {
  const [chinesePhase, englishPhase] = GUIDANCE_PHASE_LABELS[event.phase] ||
    ["收到未知引导事件", "Unknown guidance event"];
  const fragments = [productText(chinesePhase, englishPhase)];
  if (event.channel) {
    const channel = event.channel === "speech" ? productText("语音", "Speech")
      : event.channel === "vibration" ? productText("振动", "Vibration") : event.channel;
    fragments.push(`${productText("通道", "Channel")}: ${channel}`);
  }
  if (typeof event.enabled === "boolean")
    fragments.push(productText(event.enabled ? "已启用" : "已停用", event.enabled ? "enabled" : "disabled"));
  if (event.reason)
    fragments.push(`${productText("原因", "Reason")}: ${String(event.reason).replaceAll("_", " ")}`);
  if (event.request_id) fragments.push(`#${event.request_id}`);
  return fragments.join(" · ");
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
 * Inputs: Retained error type. Outputs: Fixed localized action. Logic: Permission guidance tells
 * the user to start from the visible click target if no prompt appeared; device and busy states are
 * distinguished from other connection/start errors. Constraints: No permission change or retry. */
function cameraFailureMessage(reason) {
  if (reason === "NotAllowedError" || reason === "SecurityError")
    return productText("摄像头请求未获准。若没有出现权限弹窗，请在本页直接点击“开启环境感知”；若之前拒绝过，请在浏览器网站设置中允许摄像头。", "The browser did not grant camera access. If no prompt appeared, tap Start environment awareness on this page; if access was previously denied, allow camera access in the browser's site settings.");
  if (reason === "NotFoundError" || reason === "OverconstrainedError")
    return productText("未能打开后置摄像头。请使用带后置摄像头的手机，或进入测试页手动选择镜头。", "The rear camera is unavailable. Use a phone with a rear camera, or choose a camera manually on the test page.");
  if (reason === "NotReadableError" || reason === "AbortError")
    return productText("摄像头暂时无法使用。请关闭其他占用摄像头的应用后再次点击开始检测。", "The camera is unavailable. Close other apps using it, then tap Start detection again.");
  return productText("摄像头启动或检测连接失败，请查看提示后重新开始。", "The camera or detection connection failed. Check the message and start again.");
}

/* Functionality: Render validated frame metadata for the product and diagnostic views.
 * Inputs: One validated frame. Outputs: At most 50 object entries and a concise product status or
 * diagnostic capture-age line. Logic: Product users see current detections without transport metrics;
 * the test view retains frame number and age. Constraints: No repeated inference or safety claim. */
function renderResults(event) {
  const list = node("assist-results"), metadata = event.result.metadata;
  list.replaceChildren();
  for (const box of metadata.boxes.slice(0, 50)) {
    const item = doc.createElement("li");
    item.textContent = resultText(box, node("navigation-speech-language").value); list.append(item);
  }
  const age = performance.now() - event.capturedAt;
  const status = testView
    ? `${metadata.count} objects · frame ${metadata.index} · ${age.toFixed(0)} ms capture age · ${age >= 0 && age < 1000 ? "fresh" : "stale"}`
    : metadata.count
      ? productText(`检测到 ${metadata.count} 个目标，详情见下方。`, `${metadata.count} objects detected. See details below.`)
      : productText("本帧暂未识别到目标；这不代表道路安全。", "No objects detected in this frame; this does not establish road safety.");
  setStatusText("assist-result-status", status);
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
    node("assist-results").replaceChildren();
    setStatusText("assist-result-status", testView ? productText("等待本地检测结果。", "Waiting for local detection results.")
      : productText("正在等待首个识别结果。", "Waiting for the first detection result."));
    node("assist-status").textContent = source === "video" ? productText("本地视频检测中；视频位置不代表设备位置。", "Local video detection is running; video position does not represent device location.")
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
      node("assist-status").textContent = testView ? productText("感知已停止；导航状态保持独立。", "Perception stopped; navigation state remains independent.")
        : productText("环境检测已停止。", "Obstacle detection stopped.");
      setStatusText("assist-result-status", testView ? productText("感知已停止；最后画面仅供查看，不触发提醒。", "Perception stopped; the last frame is for viewing only and triggers no alerts.")
        : productText("检测已停止；保留最后画面和识别结果。", "Detection stopped. The last frame and results remain visible."));
    }
    if (source === "video") node("assist-video-status").textContent = event.type === "error"
      ? productText(`视频已停止：${event.message || "请检查本机服务。"}`, `Video stopped: ${event.message || "Check the local service."}`)
      : productText("视频会话已停止，需手动开始或重新选择视频。", "Video session stopped. Start it manually or choose a video again.");
    // CameraApp publishes stop before clearing mode; read its completed transition once.
    if (source === "camera") queueMicrotask(renderControls);
  }
  if (!testView && source === "camera" && ["stop", "ended", "error"].includes(event.type))
    setStatusText("assist-status", cameraFailure ? cameraFailureMessage(cameraFailure)
      : productText("环境检测已停止。", "Obstacle detection stopped."));
  if (source === "video") node("assist-stats").textContent = JSON.stringify(videoReplay.stats, null, 2);
  renderControls();
}

/* Functionality: Present product output availability or localized diagnostic lifecycle and recovery controls.
 * Inputs: Manager event. Outputs: Finite status without logging media, GPS or spoken text.
 * Logic: Product text describes available actions; test diagnostics use finite phase labels and request identities.
 * The startup gate uses the activation button; recovery appears only for a separate channel pause.
 * Constraints: Backend stream events are not physical speaker verification. */
function onGuidance(event) {
  const state = guidanceManager.describe();
  const diagnostic = guidanceDiagnostic(event);
  const status = testView ? diagnostic : !speechOutput.enabled.checked
    ? productText("语音提醒已关闭。", "Voice alerts are off.")
    : voicePreference.reason
      ? productText("当前语言的语音提醒暂不可用。", "Voice alerts are unavailable for this language.")
    : state.paused?.speech && state.reasons.speech !== "activation_required"
      ? productText("语音提醒已暂停，请点击恢复语音。", "Voice alerts are paused. Tap Resume voice.")
      : !speechActivation.describe().ready
        ? productText("发送助手指令、开始导航或环境检测时，将启用语音提醒。", "Sending an assistant request, starting navigation or detection activates voice alerts.")
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
 * Logic: First backend PCM playback marks ready; pending submission never claims sound or sensor access.
 * Constraints: No browser voice lookup, automatic retry, voice substitution or hardware verification. */
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
      : voicePreference.reason === "backend_tts_unavailable"
        ? productText("后端语音服务暂不可用，请检查网络连接。", "The backend speech service is unavailable. Check the network connection.")
      : voicePreference.reason === "unsupported_interface_language"
        ? productText("当前语言暂不支持语音提醒，请切换中文或英文。", "Voice alerts do not support this language. Choose Chinese or English.")
      : voicePreference.reason
        ? productText("后端中英文语音暂不可用，请检查语音服务配置。", "Backend Chinese and English speech is unavailable. Check the speech service configuration.")
      : state.ready
        ? gated ? productText("点击启动语音、发送助手指令，或重新开始导航、检测。", "Tap Activate voice, send an assistant request, or start navigation or detection again.")
          : output.paused.speech ? productText("语音提醒已暂停，请点击恢复语音。", "Voice alerts are paused. Tap Resume voice.")
          : productText("语音提醒已就绪。", "Voice alerts are ready.")
      : state.state === "queued" ? productText("正在启用语音提醒。", "Activating voice alerts.")
      : state.state === "error"
        ? productText("语音尚未启动，请保持页面在前台，再次点击启动语音或发送助手指令。", "Voice has not started. Keep this page open, then tap Activate voice again or send an assistant request.")
        : productText("语音跟随界面语言。点击启动语音、发送助手指令，或开始导航、检测。", "Voice follows the interface language. Tap Activate voice, send an assistant request, or start navigation or detection."));
    return;
  }
  node("assist-speech-status").textContent = !enabled ? productText("语音输出已关闭。", "Speech output is off.") : state.ready
    ? gated ? productText("语音已停止；点击“启动语音”、发送助手指令或开始导航、检测后恢复。", "Voice has stopped. Tap Activate voice, send an assistant request, or start navigation or detection to resume.")
      : output.paused.speech ? productText(`流式音频已启动，但自动播报已暂停（${output.reasons.speech}），请手动恢复输出。`, `Streamed audio has started, but automatic announcements are paused (${String(output.reasons.speech).replaceAll("_", " ")}). Resume output manually.`)
        : productText("后端语音已开始播放，助手回复、导航和风险提醒可自动播报；请确认设备实际声音。", "Backend speech playback has started and can announce assistant replies, navigation and risk alerts. Confirm that sound is audible on the device.")
    : state.state === "queued" ? productText("正在启动语音，请等待提示音。", "Starting voice. Wait for the prompt.")
      : productText(`语音提醒已开启，尚未启动（${state.reason || "activation_required"}）。点击“启动语音”、发送助手指令或开始导航、检测。`, `Voice alerts are enabled but have not started (${String(state.reason || "activation_required").replaceAll("_", " ")}). Tap Activate voice, send an assistant request, or start navigation or detection.`);
}

/* Functionality: Start the first utterance directly within the current real user-gesture stack.
 * Inputs: Explicit startup, navigation, perception or assistant-submit gesture. Outputs: Activation acknowledgement.
 * Logic: Unlock the audio context synchronously before any permission promise or inference callback.
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
 * Logic: An unstarted audio context must first receive explicit calibration, not asynchronous risk.
 * Constraints: Existing unrelated output errors still require the manager's explicit recovery. */
function resumeSpeech() {
  return speechActivation.describe().ready ? guidanceManager.resume("speech") : activateSpeech();
}

/* Functionality: Submit a live or matching in-flight replay reply for automatic backend speech.
 * Inputs: CustomEvent carrying reply text. Outputs: Manager queue entry or no output.
 * Logic: Require visible page and a valid backend voice. When activation is incomplete, retain the
 * reply while the manager's activation gate is paused; explicit activation later resumes the queue.
 * Constraints: Disabled/unavailable output and unrelated paused speech are never queued. */
function speakAssistantReply(event) {
  const text = event.detail?.text;
  const activation = speechActivation.describe(), output = guidanceManager.describe();
  const selection = speechActivation.selection();
  const awaitingActivation = !activation.ready && output.paused.speech &&
    output.reasons.speech === "activation_required";
  if (typeof text !== "string" || !text.trim() || doc.visibilityState !== "visible" ||
      !speechOutput.enabled.checked || (!activation.ready && !awaitingActivation) ||
      selection.reason || (output.paused.speech && !awaitingActivation)) return;
  guidanceManager.enqueueAssistantReply(text);
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

/* Functionality: Apply the fixed backend voice and selected language to automatic sources.
 * Inputs: Shared backend voice/language. Outputs: Manager/manual UI configuration.
 * Logic: Preserve the exact server voice ID while applying explicit Chinese/English language.
 * Constraints: No output, enablement, readiness reset or current-utterance cancellation. */
function applyVoice() {
  const voiceId = "Cherry", language = node("navigation-speech-language").value;
  node("navigation-speech-voice").value = voiceId;
  guidanceManager.configure({voiceId, language});
  speechOutput.voice.value = voiceId; speechOutput.language.value = language;
}

/* Functionality: Configure automatic product speech for the interface language and backend voice.
 * Inputs: Document language and controller availability. Outputs: Fixed server voice and future settings.
 * Logic: Native voice inventory is not queried; a queued calibration retains its language until
 * first backend playback so a concurrent UI language change cannot relabel that request.
 * Constraints: No synthesis request, cancellation, readiness reset, voice fallback or retry. */
function readProductVoices() {
  if (["感知尚未开始。", "Obstacle detection has not started."].includes(node("assist-status").textContent))
    setStatusText("assist-status", productText("感知尚未开始。", "Obstacle detection has not started."));
  if (speechActivation.describe().state === "queued") return;
  const language = interfaceSpeechLanguage(doc.documentElement.lang);
  const voiceId = "Cherry";
  voicePreference = {language, voiceId, reason: !speechOutput.supported ? "backend_tts_unavailable"
    : !language ? "unsupported_interface_language" : null};
  const select = node("navigation-speech-voice");
  const option = doc.createElement("option"); option.value = voiceId;
  option.textContent = productText("后端语音 · Cherry（中文 / English）", "Backend voice · Cherry (中文 / English)");
  select.replaceChildren(option); select.value = voiceId;
  node("navigation-speech-language").value = language || "";
  speechOutput.rate.value = "2"; speechOutput.volume.value = "1";
  applyVoice(); speechOutput.render(); onGuidance({phase: "configuration_changed"});
  if (latest) renderResults(latest);
}

/* Functionality: Follow UI language changes after the current calibration completes.
 * Inputs: Product document and shared driver lifecycle. Outputs: Future language settings.
 * Logic: Observe only lang; first backend playback releases a deferred calibration setting update.
 * Constraints: No browser voice inventory, output, manager resume, cancellation or readiness reset. */
function observeProductLanguage() {
  languageObserver = new MutationObserver(() => readProductVoices());
  languageObserver.observe(doc.documentElement, {attributes: true, attributeFilter: ["lang"]});
  speechOutput.subscribe(event => {
    if (["start", "end", "cancel", "error"].includes(event.phase) && speechActivation.describe().state !== "queued")
      readProductVoices();
  });
}

/* Functionality: Refresh the single backend voice selection on both assist views.
 * Inputs: Explicit view marker and selected language. Outputs: Fixed Cherry option and manager config.
 * Logic: Diagnostic mode retains its language choice; product mode follows document language.
 * Constraints: Does not enumerate device voices, synthesize, change core settings or retry errors. */
function readVoices() {
  if (!testView) { readProductVoices(); return; }
  const select = node("navigation-speech-voice");
  const language = node("navigation-speech-language").value;
  select.replaceChildren();
  const option = doc.createElement("option"); option.value = "Cherry";
  option.textContent = productText("后端语音 · Cherry（中文 / English）", "Backend voice · Cherry (中文 / English)");
  option.dataset.languageOriginal = ""; select.append(option); select.value = "Cherry"; applyVoice();
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
    const empty = doc.createElement("option"); empty.value = ""; empty.textContent = productText("请明确选择摄像头", "Select a camera explicitly"); select.append(empty);
    for (const [index, device] of inventory.filter(item => item.kind === "videoinput" && item.deviceId).entries()) {
      const option = doc.createElement("option"); option.value = device.deviceId;
      option.textContent = device.label || `Camera ${index + 1}`; select.append(option);
    }
    select.value = explicitDeviceId(inventory, selected); cameraApp.localDeviceId = select.value;
  } catch (error) {
    if (choice === permissionChoice) node("assist-status").textContent = testView
      ? productText(`读取摄像头失败（${error.name}），请检查浏览器权限。`, `Camera list failed (${error.name}). Check browser permissions.`)
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
    if (choice === permissionChoice) node("assist-status").textContent = testView ? productText(`摄像头授权失败（${error.name}）。`, `Camera permission failed (${error.name}).`)
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
    node("assist-video-status").textContent = productText(`${file.name} · ${videoReplay.video.duration.toFixed(2)} s；点击开始视频检测。`, `${file.name} · ${videoReplay.video.duration.toFixed(2)} s. Select Start video detection to begin.`);
  } catch (error) { if (choice === fileChoice) node("assist-video-status").textContent = productText(`视频未就绪：${error.message}`, `Video is not ready: ${error.message}`); }
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
  catch (error) { node("assist-video-status").textContent = productText(`未开始：${error.message}`, `Not started: ${error.message}`); }
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
  node("assist-status").textContent = testView ? productText("感知已停止；导航状态保持独立。", "Perception stopped; navigation state remains independent.")
    : productText("环境检测已停止。", "Obstacle detection stopped.");
  setStatusText("assist-result-status", testView ? productText("感知已停止；最后画面不触发提醒。", "Perception stopped; the last frame does not trigger alerts.")
    : productText("检测已停止；保留最后画面和识别结果。", "Detection stopped. The last frame and results remain visible."));
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
    if (testView) node("assist-status").textContent = productText("已请求停止全部；请查看导航和输出状态确认清理结果。", "Stop all was requested. Check navigation and output statuses to confirm cleanup.");
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
 * Logic: Select only local PRF2, reuse shared drivers; expose the existing ping button only on the
 * diagnostic page; product speech uses backend bilingual TTS and accepts a voice-release or
 * text-submit gesture as synchronous audio activation.
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
  node("ping").hidden = !testView;
  node("ping").textContent = productText("测试页面连接", "Test page connection");
  cameraApp.processing.value = "local"; cameraApp.handleProcessingChange();
  node("maps-navigation-speech-panel").querySelector("h2").textContent = productText("自动语音播报", "Automatic speech output");
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
  if (!testView) window.addEventListener("prs-assist-voice-gesture", activateSpeech);
  speechOutput.enabled.addEventListener("change", () => {
    if (!speechOutput.enabled.checked) guidanceManager.clearAssistantReplies("speech_disabled");
    renderSpeechActivation(speechActivation.describe());
  });
  window.addEventListener("prs-agent-reply", speakAssistantReply);
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
  if (!testView) observeProductLanguage();
  enableDefaultOutputs(); readVoices(); renderControls();
}

export const assistReady = Promise.resolve().then(initialize);
