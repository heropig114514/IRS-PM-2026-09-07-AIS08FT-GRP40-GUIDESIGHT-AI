/* Responsibilities: Guard product status presentation and honest coordinate selection summaries.
 * Implementation: Real pure adapter functions with explicit source strings and boundary values.
 * Related Modules: ../integration/product-ui.js; existing i18n dictionary.
 * Declaration Index: None.
 * Variable Index: None. */
import test from "node:test";
import assert from "node:assert/strict";
import {productMessage, coordinateSummary} from "../integration/product-ui.js";

test("product errors retain actionable failure while diagnostic request IDs stay off the surface", () => {
  assert.equal(productMessage("ORS 网络 I/O 超时，请检查网络后手动尝试。（ors_timeout / 92f680f86338）"), "路线请求超时，请检查网络后重新规划。");
  assert.match(productMessage("ORS 网络 I/O 超时，请检查网络后手动尝试。（ors_timeout / 92f680f86338）", "en"), /timed out/);
  assert.equal(productMessage("无法连接当前设备。"), "无法连接当前设备。");
  assert.equal(productMessage("ORS 步行路线验证成功。请求编号：fixture-123。底图状态见地图下方。"), "路线已规划，点击开始导航。");
  assert.equal(productMessage("YOLO26n 本地 CUDA 检测 80 类；图像仅发送本机服务。", "en"), "Preparing detection. Please wait.");
});

test("model result presentation makes no clear-road inference from zero detections", () => {
  assert.equal(productMessage("本帧 0 个目标 · 模型推理 5 ms。未检出目标不代表道路安全。"), "正在检测，发现风险时会播报提醒。");
});

test("incomplete or invalid coordinates never appear selected", () => {
  for (const values of [["", "", "", ""], [" ", "0", "NaN", "2"], ["91", "0", "0", "181"], ["Infinity", "0", "0", "Infinity"]]) {
    assert.deepEqual(coordinateSummary(values), {origin: "尚未选择", destination: "请在地图上点选"});
  }
});

test("manual coordinates remain distinct from an original device fix including valid zero", () => {
  assert.deepEqual(coordinateSummary(["0", "0", "-90", "180"]), {origin: "已输入起点", destination: "已选择目的地"});
  assert.equal(coordinateSummary(["1", "103", "1", "103"], "设备当前位置", "en").origin, "Current device location");
  assert.equal(coordinateSummary(["1", "103", "1", "103"], "", "en").origin, "Start point entered");
});
