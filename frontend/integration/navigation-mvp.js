/** Responsibilities: Connect explicit local route/GPS replay to existing navigation and independent speech.
 * Implementation: File/sample loading, offline sketch, explicit run identities and native local-output controls.
 * Related Modules: navigation.html/navigation.css/navigation-replay.js/navigation-speech.js;
 * fixed /integration/navigation.js/maps-client.js aliases and /live/assets/speech-output.js.
 * Declaration Index: node; renderControls; renderStats; sketchPoint; renderSource; renderNavigation;
 * onReplay; onSpeech; applyVoice; readVoices; stopReplay; loadSource; loadSample; loadFile; startReplay;
 * initialize; replayGeolocation; navigation; navigationSpeech; navigationMvpReady.
 * Variable Index: doc owns this page; bundle is one explicit local source; loading/sourceGeneration isolate async loads;
 * runSequence isolates restarts; sketchBounds serves display only; STATE_LABELS/REASON_LABELS describe existing states.
 * prs-navigation-change/prs-local-navigation-replay expose original snapshots and timing metadata to local observers.
 * No real GPS, camera, video, external route planner, persistence or automatic restart is used.
 */
import {WalkingNavigation} from "../navigation.js";
import {turnLabel} from "../maps-client.js";
import {speechOutput} from "/live/assets/speech-output.js";
import {ReplayGeolocation, validateNavigationReplay} from "./navigation-replay.js";
import {NavigationSpeechAdapter} from "./navigation-speech.js";

const doc = document;
const STATE_LABELS = {idle: "尚未加载路线", ready: "路线已准备", locating: "等待可靠回放位置",
  navigating: "导航回放进行中", off_route: "回放位置已偏离路线", arrived: "已到达路线终点", stopped: "回放已停止", error: "导航回放失败"};
const REASON_LABELS = {accuracy_insufficient: "定位精度不足，暂停转向。", off_route: "偏离路线，请确认位置后手动重新规划。",
  position_stale: "定位超过原有效期，已停止。", position_timeout: "定位超时，已停止。",
  position_unavailable: "定位回放不可用。", invalid_position: "定位数据无效。", watch_failed: "无法开始定位回放。",
  stop_failed: "停止定位失败，请关闭页面后重新打开。", page_hidden: "页面已隐藏，回放已停止。", page_left: "页面已离开。"};
let bundle = null, loading = false, sourceGeneration = 0, runSequence = 0, sketchBounds = null;
export const replayGeolocation = new ReplayGeolocation({onEvent: onReplay});
export const navigationSpeech = new NavigationSpeechAdapter({speech: speechOutput});
export const navigation = new WalkingNavigation({geo: replayGeolocation, onChange: renderNavigation});

/** Functionality: Resolve a fixed page control. Inputs: DOM ID. Outputs: HTML or SVG Element.
 * Logic: local stable IDs; Constraints: no user-provided selector or duplicate shared driver. */
function node(id) { return doc.getElementById(id); }
/** Functionality: Reflect source/run and owned speech admission. Inputs: navigation, output and source state.
 * Outputs: disabled controls. Logic: source selection stays locked during active replay; stop also remains available
 * for arrival speech or an unconfirmed owned cancellation. Constraints: no implicit start or sensor activation. */
function renderControls() {
  node("navigation-load-sample").disabled = node("navigation-file").disabled = loading || navigation.active;
  node("navigation-replay-start").disabled = loading || !bundle || navigation.active || navigation.reason === "stop_failed";
  node("navigation-replay-stop").disabled = !loading && !navigation.active && navigation.reason !== "stop_failed" &&
    navigationSpeech.describe().active_request_id === null;
}
/** Functionality: Show bounded lifecycle diagnostics. Inputs: original controller snapshots. Outputs: JSON status.
 * Logic: include no coordinates or instruction bodies; Constraints: engine status is not audible proof. */
function renderStats() {
  node("navigation-replay-stats").textContent = JSON.stringify({replay: replayGeolocation.describe(), speech: navigationSpeech.describe()}, null, 2);
}
/** Functionality: Project coordinates for the offline illustration only. Inputs: longitude/latitude.
 * Outputs: SVG coordinate pair. Logic: fit selected route/replay bounds with padding.
 * Constraints: this display projection never feeds navigation geometry or arrival decisions. */
function sketchPoint(longitude, latitude) {
  const {west, scale, left, top, north} = sketchBounds;
  return [left + (longitude - west) * scale, top + (north - latitude) * scale];
}
/** Functionality: Describe the explicitly selected source. Inputs: validated bundle. Outputs: route list and sketch.
 * Logic: render textContent and finite geometry only; Constraints: not a map, real GPS or ORS attribution claim. */
function renderSource() {
  const points = [...bundle.route.geometry.coordinates, ...bundle.fixes.map(fix => [fix.longitude, fix.latitude])];
  const longitudes = points.map(point => point[0]), latitudes = points.map(point => point[1]);
  const west = Math.min(...longitudes), east = Math.max(...longitudes), south = Math.min(...latitudes), north = Math.max(...latitudes);
  const scale = Math.min(320 / Math.max(east - west, 1e-9), 180 / Math.max(north - south, 1e-9));
  sketchBounds = {west, south, north, scale, left: (360 - (east - west) * scale) / 2, top: (220 - (north - south) * scale) / 2};
  node("navigation-route-line").setAttribute("points", bundle.route.geometry.coordinates.map(point => sketchPoint(...point).join(",")).join(" "));
  node("navigation-position-dot").toggleAttribute("hidden", true);
  const list = node("navigation-route-steps"); list.replaceChildren();
  for (const step of bundle.route.steps) {
    const item = doc.createElement("li"); item.textContent = `${turnLabel(step.type)} · ${step.instruction}`; list.append(item);
  }
  node("navigation-source-status").textContent = `${bundle.label} · 合成来源 · ${bundle.fixes.length}个定位点 · ${bundle.description}`;
  node("navigation-source-status").dataset.kind = "ready";
}
/** Functionality: Consume the original navigation snapshot. Inputs: WalkingNavigation state. Outputs: speech input/UI.
 * Logic: speech sees the same current/next and source timestamps as the progress panel.
 * Constraints: no reclassification of arrival, route projection, position substitution or raw-coordinate logging. */
function renderNavigation(state) {
  navigationSpeech.ingest(state);
  node("navigation-replay-status").textContent = `${STATE_LABELS[state.state]}${REASON_LABELS[state.reason] ? ` · ${REASON_LABELS[state.reason]}` : ""}`;
  const progress = state.progress;
  node("navigation-current").textContent = progress?.current_step ? `当前：${turnLabel(progress.current_step.type)} · ${progress.current_step.instruction}` : "暂无可靠当前指令。";
  node("navigation-next").textContent = progress?.next_step ? `下一步：${turnLabel(progress.next_step.type)} · ${progress.next_step.instruction}` : "暂无下一指令。";
  node("navigation-progress").textContent = progress?.remaining_distance_m != null ?
    `剩余约${Math.round(progress.remaining_distance_m)}米 · 距下一动作约${Math.round(progress.distance_to_next_m)}米` : "尚无可靠进度。";
  node("navigation-position").textContent = state.position ?
    `合成定位：经度${state.position.longitude.toFixed(6)}，纬度${state.position.latitude.toFixed(6)}；精度约${Math.round(state.position.accuracy_m)}米。` : "尚无回放位置。";
  if (state.position && sketchBounds) {
    const [x, y] = sketchPoint(state.position.longitude, state.position.latitude), dot = node("navigation-position-dot");
    dot.setAttribute("cx", x); dot.setAttribute("cy", y); dot.dataset.state = state.state; dot.toggleAttribute("hidden", false);
  }
  renderControls(); renderStats();
  doc.dispatchEvent(new CustomEvent("prs-navigation-change", {detail: state}));
}
/** Functionality: Observe replay lifecycle without adding a second navigation rule. Inputs: metadata event.
 * Outputs: bounded status. Logic: original controller owns fixes/failures; Constraints: no coordinate/event-body logging. */
function onReplay(event) {
  renderStats(); doc.dispatchEvent(new CustomEvent("prs-local-navigation-replay", {detail: event}));
}
/** Functionality: Reflect independent speech lifecycle. Inputs: body-free adapter event. Outputs: status/recovery controls.
 * Logic: errors require manual recovery; Constraints: native starts/ends do not prove physical audio output. */
function onSpeech(event) {
  const status = navigationSpeech.describe();
  node("navigation-speech-status").textContent = `${event.phase}${event.reason ? ` · ${event.reason}` : ""}${event.request_id ? ` · #${event.request_id}` : ""}`;
  node("navigation-speech-resume").disabled = !status.paused;
  if (!["snapshot", "suppressed"].includes(event.phase)) console.info("[PRS navigation speech] State", {phase: event.phase, request_id: event.request_id ?? null, reason: event.reason ?? null});
  renderControls(); renderStats();
}
/** Functionality: Apply the user's explicit voice/language. Inputs: page controls. Outputs: adapter and manual driver choices.
 * Logic: exact native voice identity; Constraints: no voice selection, channel enablement or recovery is synthesized. */
function applyVoice() {
  const voiceId = node("navigation-speech-voice").value, language = node("navigation-speech-language").value;
  navigationSpeech.configure({voiceId, language});
  if (voiceId) { speechOutput.voice.value = voiceId; speechOutput.language.value = language; }
}
/** Functionality: Read matching local voices without choosing one. Inputs: live voice inventory and explicit language.
 * Outputs: choices with preserved selection. Logic: reject remote voices and language mismatches.
 * Constraints: disappeared voices remain visibly unavailable; no replacement or automatic playback. */
function readVoices() {
  const select = node("navigation-speech-voice"), selected = select.value, language = node("navigation-speech-language").value;
  select.replaceChildren();
  const empty = doc.createElement("option"); empty.value = ""; empty.textContent = "请选择本地声音"; select.append(empty);
  for (const voice of speechOutput.voiceList().voices.filter(voice => voice.local_service === true && voice.language.split("-")[0].toLowerCase() === language.split("-")[0].toLowerCase())) {
    const option = doc.createElement("option"); option.value = voice.id; option.textContent = `${voice.name} (${voice.language}) [local]`; select.append(option);
  }
  if (selected && ![...select.options].some(option => option.value === selected)) {
    const missing = doc.createElement("option"); missing.value = selected; missing.disabled = true; missing.textContent = `${selected} [unavailable]`; select.append(missing);
  }
  select.value = selected; applyVoice();
}
/** Functionality: Stop pending source loading, replay navigation and its owned speech.
 * Inputs: explicit lifecycle reason. Outputs: invalidated load and cleaned run.
 * Logic: invalidate async reads and the speech run before clearing the watch, whose synchronous state publication
 * must not submit or cancel speech again; Constraints: manual speech remains independent,
 * no automatic restart, and an uncancellable file read cannot install a route after stopping or hiding. */
function stopReplay(reason = "user") {
  if (loading) {
    sourceGeneration++; loading = false;
    node("navigation-source-status").textContent = "回放加载已取消，请重新选择。";
    node("navigation-source-status").dataset.kind = "idle";
  }
  navigationSpeech.stop(reason); navigation.stop(reason); renderControls(); renderStats();
}
/** Functionality: Load one explicitly requested local source without retaining an old route on failure.
 * Inputs: async text reader. Outputs: validated replay/route or visible failure.
 * Logic: invalidate old run, bound source bytes, parse and validate before setting a new source.
 * Constraints: no fallback source, upload, race overwrite, automatic playback or silently repaired geometry. */
async function loadSource(read) {
  stopReplay("source_changed");
  const generation = ++sourceGeneration; loading = true; bundle = null; sketchBounds = null; navigation.setRoute(null);
  node("navigation-source-status").textContent = "正在读取本地回放…";
  node("navigation-source-status").dataset.kind = "loading";
  node("navigation-route-line").setAttribute("points", ""); node("navigation-position-dot").toggleAttribute("hidden", true); node("navigation-route-steps").replaceChildren();
  renderControls();
  try {
    const text = await read();
    if (generation !== sourceGeneration) return;
    if (new TextEncoder().encode(text).length > 1048576) throw new Error("回放文件超过1MiB。");
    const source = validateNavigationReplay(JSON.parse(text));
    replayGeolocation.setReplay(source.fixes); navigation.setRoute(source.route); bundle = source; renderSource();
  } catch (error) {
    if (generation === sourceGeneration) {
      bundle = null; node("navigation-source-status").textContent = `回放加载失败：${error.message}`; node("navigation-source-status").dataset.kind = "error";
      node("navigation-route-line").setAttribute("points", ""); node("navigation-position-dot").toggleAttribute("hidden", true); node("navigation-route-steps").replaceChildren();
    }
  } finally { if (generation === sourceGeneration) { loading = false; renderControls(); renderStats(); } }
}
/** Functionality: Read the fixed local fixture at a user click. Inputs: local asset response. Outputs: source load.
 * Logic: one same-origin no-store request; Constraints: no online routing, retry or synthetic device-position claim. */
function loadSample() {
  return loadSource(async () => {
    const response = await fetch("/integration/assets/navigation-sample.json", {cache: "no-store", credentials: "omit"});
    if (!response.ok) throw new Error(`本地示例不可用（HTTP ${response.status}）。`);
    return response.text();
  });
}
/** Functionality: Read an explicitly chosen local JSON file in memory. Inputs: file picker. Outputs: source load.
 * Logic: reject oversized files before reading, then clear picker; Constraints: cancelled selection leaves the current source. */
function loadFile() {
  const file = node("navigation-file").files[0]; if (!file) return;
  node("navigation-file").value = "";
  return loadSource(() => { if (file.size > 1048576) throw new Error("回放文件超过1MiB。"); return file.text(); });
}
/** Functionality: Begin a new explicit replay and speech generation. Inputs: loaded bundle and user click.
 * Outputs: original navigation watch/start acknowledgement. Logic: source restarts only through this action.
 * Constraints: no GPS device, silent restart or inferred route identity. */
function startReplay() {
  if (!bundle || loading || navigation.active) return;
  navigationSpeech.begin(`local-navigation-${++runSequence}`);
  const result = navigation.start();
  if (!result.accepted) node("navigation-source-status").textContent = REASON_LABELS[result.reason] || "无法开始导航回放，请检查状态后手动操作。";
  renderControls(); renderStats();
}
/** Functionality: Wire explicit replay and local-output controls. Inputs: marked isolated page. Outputs: event subscriptions.
 * Logic: capture lifecycle events before the shared driver so intentional owned stops are not output failures;
 * Constraints: no initial fixture load, watch, audio or remote request; hidden/pagehide never resumes a run. */
function initialize() {
  if (doc.documentElement.dataset.localMvp !== "true") throw new Error("Navigation replay requires the local entry marker.");
  navigationSpeech.subscribe(onSpeech);
  node("navigation-load-sample").addEventListener("click", loadSample);
  node("navigation-file").addEventListener("change", loadFile);
  node("navigation-replay-start").addEventListener("click", startReplay);
  node("navigation-replay-stop").addEventListener("click", () => stopReplay());
  node("navigation-speech-enabled").addEventListener("change", () => navigationSpeech.setEnabled(node("navigation-speech-enabled").checked));
  node("navigation-speech-language").addEventListener("change", readVoices);
  node("navigation-speech-voice").addEventListener("change", applyVoice);
  node("navigation-voices-refresh").addEventListener("click", readVoices);
  node("navigation-speech-resume").addEventListener("click", () => navigationSpeech.resume());
  doc.addEventListener("visibilitychange", () => { if (doc.visibilityState !== "visible") stopReplay("page_hidden"); }, true);
  globalThis.addEventListener("pagehide", () => stopReplay("page_left"), true);
  renderNavigation(navigation.describe()); readVoices();
}

export const navigationMvpReady = Promise.resolve().then(initialize);
