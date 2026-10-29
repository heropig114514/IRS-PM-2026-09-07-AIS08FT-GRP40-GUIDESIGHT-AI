/* Responsibilities: Present one live preview, optional local box tracking and honest latency metrics.
 * Implementation: Exact-source motion seeds never enter guidance; collect inference timings first.
 * Related Modules: camera.js, local-motion.js, live-camera-overlay.js, assist-mvp.js.
 * Declaration Index: recordDisplay; recordTracking; motionStatus; renderHint; renderOverlay; mirrorSpeech;
 * attachPreview; clearPreview; finite; format; percentile; summary; render.
 * Variable Index: motion owns visual estimates; overlay owns the short visible lifetime;
 * samples/speechEvents are bounded scalar diagnostics; owner isolates inference sessions.
 */
import {cameraApp} from "../camera.js";
import {LiveCameraOverlay} from "./live-camera-overlay.js";
import {LocalMotion} from "./local-motion.js";
import {guidanceManager, speechActivation} from "./assist-mvp.js";

const output = document.getElementById("live-latency-values");
const download = document.getElementById("live-latency-export");
const preview = document.getElementById("live-camera-preview");
const previewFigure = document.getElementById("live-camera-figure");
const resultFigure = document.getElementById("echo")?.closest("figure");
if (previewFigure && resultFigure) resultFigure.before(previewFigure);
const singleView = document.documentElement.dataset.cameraDisplay === "live-overlay";
if (singleView && resultFigure) { resultFigure.hidden = true; resultFigure.setAttribute("aria-hidden", "true"); }
const sampleByIndex = new Map();
let latestDisplay = null;
/** Functionality: Attach scalar drawing evidence to its source row. Inputs: layer counts.
 * Outputs: bounded per-frame diagnostics. Logic: epoch/index guards. Constraints: no geometry retained. */
function recordDisplay(metrics) {
  latestDisplay = metrics;
  const row = sampleByIndex.get(metrics.frame_index);
  if (row && row.tracking_epoch === metrics.tracking_epoch) row.box_display = {...metrics};
}
/** Functionality: Explain tracking eligibility/loss separately from drawing. Inputs: worker counts.
 * Outputs: source-row diagnostic. Logic: latest matching epoch/index. Constraints: no target identities. */
function recordTracking(diagnostic) {
  const row = sampleByIndex.get(diagnostic.index);
  if (row && row.tracking_epoch === diagnostic.epoch) row.tracking_diagnostics = {...diagnostic};
}
const overlay = new LiveCameraOverlay({video: preview, canvas: document.getElementById("live-camera-overlay"),
  label: document.getElementById("live-camera-age"), onMetrics: recordDisplay,
  maxAgeMs: Number(document.documentElement.dataset.riskMaxAgeMs || 1000)});
const motionEnabled = document.getElementById("live-motion-enabled");
/** Functionality: Explain tracking state. Inputs: statistics. Outputs: visible status.
 * Logic: separate local refresh from inference. Constraints: never claims a fresh detection. */
function motionStatus(stats) {
  document.getElementById("live-motion-status").textContent = !motionEnabled.checked ? "本地跟踪已关闭；只闪现检测原位置。"
    : stats.state === "unavailable" ? "本地跟踪不可用；已识别目标仍短暂显示虚线框，检测与语音继续。"
    : stats.state === "running" ? `本地跟踪运行中 · 框更新 ${stats.visual_updates_fps?.toFixed(1) ?? "—"} 次/秒（含检测校正，不是识别帧率）`
    : "跟踪已停止。";
}
const motion = new LocalMotion({video: preview, sourceImage: cameraApp.image, onState: motionStatus, onDiagnostic: recordTracking,
  onResult: result => {
    if (!result) {renderOverlay(null); return;}
    renderOverlay({localTracking: true, capturedAt: result.capturedAt,
      result: {metadata: {width: result.width, height: result.height, boxes: result.boxes,
        index: result.index, tracking_epoch: result.epoch}}});
  }});
motionEnabled.addEventListener("change", () => {
  if (motionEnabled.checked && cameraApp.stream && !ended) motion.start(); else motion.stop();
  motionStatus(motion.describe());
});
/** Functionality: Admit all detections as brief hints and optionally track selected boxes.
 * Inputs: validated camera event. Outputs: source layer and worker seed. Logic: draw source first.
 * Constraints: original counts/quality and risk decisions are unchanged. */
function renderHint(event) {
  renderOverlay(event);
  if (singleView && motionEnabled.checked) motion.ingest(event);
}
/** Functionality: Draw a bounded hint. Inputs: visual event. Outputs: overlay or counted error.
 * Logic: isolate visualization errors. Constraints: original telemetry already collected. */
function renderOverlay(event) {
  if (!singleView) return;
  try { if (event) overlay.show(event); else overlay.clearTracking(); }
  catch (error) {
    overlayErrors++;
    document.getElementById("live-camera-overlay").hidden = true;
    document.getElementById("live-camera-age").hidden = true;
    console.error("Live overlay failed", {error_type: error.name});
  }
}
const speechTest = document.getElementById("live-speech-test"), speechSource = document.getElementById("assist-speech-start");
/** Functionality: Mirror original audio controls. Inputs: DOM state. Outputs: test button/status.
 * Logic: same activation owner. Constraints: no extra speech backend. */
function mirrorSpeech() {
  speechTest.disabled = speechSource.disabled;
  document.getElementById("live-speech-feedback").textContent = document.getElementById("assist-speech-status").textContent;
}
speechTest.addEventListener("click", () => speechSource.click());
new MutationObserver(mirrorSpeech).observe(document.getElementById("assist-speech-status"), {childList: true, subtree: true, characterData: true});
new MutationObserver(mirrorSpeech).observe(speechSource, {attributes: true, attributeFilter: ["disabled"]});
mirrorSpeech();
/** Functionality: Bind original camera stream. Inputs: CameraApp.stream. Outputs: live preview.
 * Logic: shared media and optional worker. Constraints: no second camera acquisition. */
function attachPreview() {
  if (!preview || !cameraApp.stream) return;
  preview.muted = true;
  preview.srcObject = cameraApp.stream;
  previewFigure.hidden = false;
  void preview.play().catch(() => { previewFigure.hidden = true; });
  if (owner && !ended && motionEnabled.checked && !motion.worker && motion.stats.state !== "unavailable") motion.start();
}
/** Functionality: Release visual state. Inputs: motion and preview. Outputs: cleared bindings.
 * Logic: stop worker before clearing media. Constraints: CameraApp releases hardware. */
function clearPreview() {
  motion.stop();
  overlay.clear();
  if (!preview) return;
  preview.pause(); preview.srcObject = null; previewFigure.hidden = true;
}
cameraApp.video.addEventListener("playing", attachPreview);
let owner = null, startedAt = null, samples = [], ended = false, timer = null;
let speechEvents = [], previousCompletedAt = null, warningStarts = 0, overlayErrors = 0;
guidanceManager.subscribe(event => {
  if (!owner || ended || event.channel !== "speech" && event.phase !== "risk_speech_state") return;
  if (event.phase === "output_start" && event.kind === "risk") warningStarts++;
  const fields = ["phase", "reason", "kind", "at", "capture_to_start_ms", "confirmed_capture_to_start_ms"];
  speechEvents.push(Object.fromEntries(fields.filter(key => Object.hasOwn(event, key)).map(key => [key, event[key]])));
  if (speechEvents.length > 300) speechEvents.shift();
});
/** Functionality: Normalize a scalar. Inputs: value. Outputs: number/null.
 * Logic: finite nonnegative checks. Constraints: no latency clipping. */
const finite = value => Number.isFinite(value) && value >= 0 ? value : null;
/** Functionality: Format a timing. Inputs: number/null. Outputs: display string.
 * Logic: one decimal place. Constraints: source precision remains in export. */
const format = value => value === null ? "—" : value.toFixed(1);
/** Functionality: Summarize samples. Inputs: values/fraction. Outputs: interpolated percentile.
 * Logic: sort finite values. Constraints: null for no samples. */
function percentile(values, fraction) {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!sorted.length) return null;
  const position = (sorted.length - 1) * fraction;
  const low = Math.floor(position), high = Math.ceil(position);
  return sorted[low] + (sorted[high] - sorted[low]) * (position - low);
}
/** Functionality: Separate startup from steady inference. Inputs: scalar rows. Outputs: aggregates.
 * Logic: completion timestamps define inference FPS. Constraints: excludes visual tracking updates. */
function summary() {
  const steady = samples.slice(1);
  const total = steady.map(row => row.capture_to_decoded_ms);
  const elapsed = samples.length > 1 ? samples.at(-1).completed_at_ms - samples[0].completed_at_ms : 0;
  return {
    frames: samples.length,
    first_frame_ms: samples[0]?.capture_to_decoded_ms ?? null,
    steady_p50_ms: percentile(total, .5), steady_p95_ms: percentile(total, .95),
    steady_max_ms: total.length ? Math.max(...total) : null,
    server_p50_ms: percentile(steady.map(row => row.server_ms), .5),
    completion_fps: elapsed > 0 ? (samples.length - 1) * 1000 / elapsed : null,
    capture_gap_max_ms: samples.length ? Math.max(0, ...samples.map(row => row.capture_gap_ms ?? 0)) : null,
    warning_starts: warningStarts,
    overlay_errors: overlayErrors,
    frames_over_six_detections: samples.filter(row => row.detected_count > 6).length,
    frames_with_undisplayed_boxes: samples.filter(row => row.box_display?.never_displayed_count > 0).length,
  };
}
/** Functionality: Render measured timings. Inputs: summary/motion state. Outputs: UI.
 * Logic: bounded update timer. Constraints: preserves all original frame measurements. */
function render() {
  timer = null;
  motionStatus(motion.describe());
  const stats = summary();
  output.textContent = `${ended ? "已停止" : "检测中"} · 已统计 ${stats.frames} 帧\n`
    + `最近一帧：${format(samples.at(-1)?.capture_to_decoded_ms ?? null)} ms\n`
    + `首帧：${format(stats.first_frame_ms)} ms\n`
    + `后续帧中位数：${format(stats.steady_p50_ms)} ms · P95：${format(stats.steady_p95_ms)} ms\n`
    + `后续帧最大值：${format(stats.steady_max_ms)} ms\n`
    + `服务端处理中位数：${format(stats.server_p50_ms)} ms\n`
    + `采帧间空档最大值：${format(stats.capture_gap_max_ms)} ms · 已启动风险播报：${stats.warning_starts} 次\n`
    + `检测结果刷新（不含本地取景）：${format(stats.completion_fps)} FPS`;
  download.disabled = !samples.length;
  const counts = latestDisplay;
  const diagnostic = counts ? sampleByIndex.get(counts.frame_index)?.tracking_diagnostics : null;
  const reasons = {...diagnostic?.excluded};
  for (const [reason, count] of Object.entries(diagnostic?.lost ?? {})) reasons[reason] = (reasons[reason] || 0) + count;
  const names = {tracking_budget: "超过6个跟踪名额", small_target: "目标较小", insufficient_features: "纹理不足",
    flow_unreliable: "跟踪丢失", inconsistent_motion: "运动不一致", outside_frame: "移出画面",
    sampling_gap: "本地采样中断", detection_expired: "检测过期", aspect_mismatch: "画面方向变化",
    preview_unavailable: "取景未就绪", source_mismatch: "原图尺寸不匹配", tracking_unavailable: "跟踪不可用",
    invalid_geometry: "框坐标异常", worker_error: "跟踪出错", superseded_detection: "更新检测已到达",
    tracking_stopped: "跟踪已停止"};
  document.getElementById("live-box-counts").textContent = counts
    ? `最近结果：检出 ${counts.detected_count} 个 · 至少绘制过 ${counts.displayed_unique_count} 个 · 持续跟踪峰值 ${counts.tracked_peak_count} 个 · 完全未绘制 ${counts.never_displayed_count} 个。`
      + (Object.keys(reasons).length ? ` 未持续跟踪原因：${Object.entries(reasons).map(([reason,count])=>`${names[reason] || reason} ${count}`).join("；")}。` : "")
    : "等待检测目标统计。";
}
cameraApp.subscribe(event => {
  if (event.type === "session") {
    clearTimeout(timer); timer = null;
    motion.stop();
    owner = event.sessionId; startedAt = new Date().toISOString(); samples = []; ended = false;
    sampleByIndex.clear(); latestDisplay = null; overlay.clear();
    speechEvents = []; previousCompletedAt = null; warningStarts = 0; overlayErrors = 0;
    attachPreview();
    render();
  } else if (event.type === "frame" && event.sessionId === owner && !ended) {
    if (samples.length >= 3000) { renderHint(event); return; }
    const timing = event.timing, meta = event.result?.metadata ?? {};
    if (!timing || finite(timing.captureToDecodedMs) === null || finite(timing.completedAt) === null) return;
    const speech = guidanceManager.riskSpeechStatus();
    samples.push({
      capture_to_decoded_ms: finite(timing.captureToDecodedMs),
      capture_to_result_ms: finite(timing.captureToResultMs),
      capture_ms: finite(timing.captureMs), transport_ms: finite(timing.transportMs),
      render_queue_ms: finite(timing.renderQueueMs), decode_ms: finite(timing.decodeMs),
      completed_at_ms: finite(timing.completedAt), input_bytes: finite(timing.inputBytes),
      frame_index: meta.index ?? null,
      tracking_epoch: meta.tracking_epoch, detected_count: meta.boxes?.length ?? 0,
      box_display: null, tracking_diagnostics: {status: motionEnabled.checked ? "pending" : "disabled"},
      server_ms: finite(meta.server_ms), inference_ms: finite(meta.inference_ms), depth_ms: finite(meta.depth_ms),
      capture_gap_ms: previousCompletedAt === null ? null : finite(timing.capturedAt - previousCompletedAt),
      pacing_wait_ms: finite(timing.pacingWaitMs), pacing_late_ms: finite(timing.pacingLateMs),
      credit_wait_ms: finite(timing.creditWaitMs), result_listener_delay_ms: finite(performance.now() - timing.completedAt),
      previous_frame_callback_ms: finite(timing.previousFrameCallbackMs),
      risk_state: speech.state, alert_count: speech.alert_count, pause_reason: speech.pause_reason,
      speech_ready: speechActivation.describe().ready, page_visible: document.visibilityState === "visible",
    });
    sampleByIndex.set(meta.index, samples.at(-1));
    previousCompletedAt = timing.completedAt;
    if (timer === null) timer = setTimeout(render, 500);
    renderHint(event);
  } else if (["stop", "ended", "error"].includes(event.type)) {
    clearPreview();
    ended = true; clearTimeout(timer); render();
  }
});
download.addEventListener("click", () => {
  if (!samples.length) return;
  const report = {
    schema: "prs-live-latency-v1", started_at: startedAt, stopped: ended,
    measurement: "capture through result decode; excludes sensor buffering and display refresh",
    transport: document.documentElement.dataset.cameraResultTransport,
    profile: document.documentElement.dataset.cameraProfile,
    warning_max_age_ms: Number(document.documentElement.dataset.riskMaxAgeMs || 1000),
    risk_speech_style: document.documentElement.dataset.preparedSpeech === "true" ? "prepared" : "detailed", overlay_max_ms: 150,
    display: document.documentElement.dataset.cameraDisplay || "annotated",
    box_display_policy: "tiered-v1",
    local_tracking: {...motion.describe(), enabled: motionEnabled.checked, sample_fps_limit: 12, max_edge: 320},
    sample_limit: 3000, summary: summary(), samples, speech_events: speechEvents,
  };
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type: "application/json"}));
  const link = document.createElement("a");
  link.href = url; link.download = `prs-live-latency-${new Date().toISOString().replaceAll(":", "-")}.json`;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
});
