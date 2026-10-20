/* Responsibilities: Bind the maps navigation panel to the shared GPS controller.
 * Implementation: One conditional page singleton; safe text, original ORS instructions and
 * state events for the map; cancel on hide/unload. No device calls on import.
 * Related Modules: navigation.js, maps-page.js, maps.html, ai-control.js, translations.js.
 * Declaration Index: renderNavigation — panel/state event rendering.
 * Variable Index: doc — browser document or undefined in Node; navigation — maps-only controller;
 * STATE_LABELS/REASON_LABELS — bilingual-source diagnostics without sensor values in logs.
 */
import {WalkingNavigation} from "./navigation.js";
import {turnLabel} from "./maps-client.js";

const doc = globalThis.document;
const STATE_LABELS = {idle: "尚未开始导航", ready: "路线可导航", locating: "等待可靠定位",
  navigating: "导航进行中", off_route: "已偏离路线", arrived: "已到达终点", stopped: "导航已停止", error: "导航失败"};
const REASON_LABELS = {
  route_required: "请先规划步行路线。", unsupported: "此浏览器不支持持续定位。",
  already_active: "导航已启动，请先停止再重新开始。", page_hidden: "页面已隐藏，导航已停止。",
  page_left: "页面已离开，导航已停止。", permission_denied: "定位权限被拒绝，请在网站权限中允许位置。",
  position_unavailable: "设备无法获取位置，请检查定位服务。",
  position_timeout: "设备定位超时，请检查 GPS 或网络后手动再试。",
  position_stale: "定位超过15秒未更新，导航已停止，请手动重新开始。",
  accuracy_insufficient: "定位精度不足，暂不提供转向和到达判断。",
  off_route: "已偏离规划路线，请确认位置后手动重新规划。",
  invalid_position: "定位数据无效，导航已停止。", position_error: "设备定位失败。",
  watch_failed: "无法启动持续定位，请检查浏览器权限。",
  stop_failed: "停止定位失败，请关闭页面后重新打开。",
  user: "导航已停止。", mcp: "导航已停止。",
};

/* Functionality: Render the exact controller snapshot shared by human controls and MCP.
 * Inputs: state JSON. Outputs: Panel text, disabled buttons and prs-navigation-change event.
 * Logic: Separate translatable labels from original instruction text; clear unavailable guidance.
 * Constraints: No HTML injection, speech, device permission requests or route requests. */
function renderNavigation(state) {
  doc.querySelector("#navigation-status").textContent = STATE_LABELS[state.state];
  doc.querySelector("#navigation-start").disabled = !state.route || state.active || state.reason === "stop_failed";
  doc.querySelector("#navigation-stop").disabled = !state.active && state.reason !== "stop_failed";
  doc.querySelector("#navigation-message").textContent = REASON_LABELS[state.reason] ||
    (state.state === "arrived" ? "已到达终点，持续定位已关闭。" : state.active ?
      "导航使用设备实时位置，请保持页面在前台。" : "规划路线后，可由此处或 agent 开始导航。");
  doc.querySelector("#navigation-position").textContent = state.position ?
    `纬度 ${state.position.latitude.toFixed(6)} · 经度 ${state.position.longitude.toFixed(6)} · 精度约 ${Math.round(state.position.accuracy_m)} 米` : "尚未取得导航位置";
  const progress = state.progress;
  doc.querySelector("#navigation-progress").textContent = progress?.remaining_distance_m != null ?
    `剩余 ${Math.round(progress.remaining_distance_m)} 米 · 预计 ${Math.ceil(progress.remaining_duration_s / 60)} 分钟` : "暂无可靠导航进度";
  doc.querySelector("#navigation-turn").textContent = progress?.current_step ?
    `${turnLabel(progress.current_step.type)} · 距下一指令 ${Math.round(progress.distance_to_next_m)} 米` : "等待转向指令";
  doc.querySelector("#navigation-instruction").textContent = progress?.current_step?.instruction || "";
  doc.querySelector("#navigation-next").textContent = progress?.next_step?.instruction || "";
  doc.querySelector("#navigation-deviation").textContent = progress ?
    `距路线 ${Math.round(progress.cross_track_m)} 米` : "—";
  doc.dispatchEvent(new CustomEvent("prs-navigation-change", {detail: state}));
}

export const navigation = doc?.querySelector("#navigation-panel") ?
  new WalkingNavigation({onChange: renderNavigation}) : null;

if (navigation) {
  doc.querySelector("#navigation-start").addEventListener("click", () => {
    const result = navigation.start();
    if (!result.accepted) doc.querySelector("#navigation-message").textContent =
      REASON_LABELS[result.reason] || "设备定位失败。";
  });
  doc.querySelector("#navigation-stop").addEventListener("click", () => navigation.stop());
  doc.addEventListener("visibilitychange", () => {
    if (doc.visibilityState === "hidden" && navigation.active) navigation.stop("page_hidden");
  });
  globalThis.addEventListener("pagehide", () => {
    if (navigation.active) navigation.stop("page_left");
  });
  renderNavigation(navigation.describe());
}
