/* Responsibilities: Leaflet/OSM 地图、设备位置、步行路线和共享持续导航接入。
 * Implementation: 初始化一次地图；串行用户操作；错误和底图状态分别呈现；无隐式重试。
 * Related Modules: maps-client.js, navigation-ui.js, maps.html, i18n.js, core.maps/services。
 * Declaration Index: setCoordinates — 填坐标；setBusy — 操作锁；clearRoute — 清旧路线；
 * showFailure — 错误展示；initialize — 配置/地图；locate — 设备起点；useExample — 显式示例；plan — 请求路线。
 * Variable Index: button/planButton/exampleButton/status/message — 状态 DOM；map/tiles — Leaflet 实例；
 * routeLayer/startMarker/endMarker — 唯一可替换图层；busy — 操作锁；configured — 后端配置状态；
 * tileFailed — 本轮底图失败；navigationMarker — 实时位置图层；NUS_START/NUS_END — 固定测试点。
 */
import {
  getDevicePosition,
  parseCoordinateInputs,
  requestWalkingRoute,
  turnLabel,
} from "./maps-client.js";
import {navigation} from "./navigation-ui.js";
const button = document.querySelector("#locate");
const planButton = document.querySelector("#plan");
const exampleButton = document.querySelector("#example");
const status = document.querySelector("#maps-status");
const message = document.querySelector("#maps-message");
const NUS_START = [103.7766, 1.2971];
const NUS_END = [103.7735, 1.2991];
let map, tiles, routeLayer, startMarker, endMarker, navigationMarker;
let busy = false,
  configured = false,
  tileFailed = false;

/* Functionality: 填写起点或终点，不调用外部服务。
 * Inputs: prefix(start/end)、point [经度,纬度]；Outputs: input 值。
 * Logic: 显示六位小数；Constraints: 调用者必须明确坐标来源。
 */
function setCoordinates(prefix, point) {
  document.querySelector(`#${prefix}-longitude`).value = point[0].toFixed(6);
  document.querySelector(`#${prefix}-latitude`).value = point[1].toFixed(6);
}

/* Functionality: 串行操作，防止异步定位和路线相互覆盖。
 * Inputs: value 布尔值、configured；Outputs: 按钮和字段 disabled。
 * Logic: 配置只限制路线按钮；地图点选也读取 busy；Constraints: 不排队或重试。
 */
function setBusy(value) {
  busy = value;
  button.disabled = exampleButton.disabled = value;
  planButton.disabled = value || !configured;
  for (const input of document.querySelectorAll("#route-form input"))
    input.disabled = value;
}

/* Functionality: 停止导航并清除旧路线和起终点图层，避免展示过期结果。
 * Inputs: 当前图层/DOM；Outputs: 成功返回 true 并清图层/列表；失败返回 false 并显示诊断。
 * Logic: 先清除共享导航路线，再移除图层；Constraints: 停止失败中止新操作，不重试，不锁死表单。
 */
function clearRoute() {
  try { navigation.setRoute(null); }
  catch (error) { showFailure(error); return false; }
  for (const layer of [routeLayer, startMarker, endMarker]) layer?.remove();
  routeLayer = startMarker = endMarker = undefined;
  document.querySelector("#steps").replaceChildren();
  document.querySelector("#route-summary").textContent = "尚未请求路线";
  return true;
}

/* Functionality: 报告失败，不输出位置或密钥到日志。
 * Inputs: error；Outputs: 状态文本和固定诊断日志。
 * Logic: textContent 插入安全文本；Constraints: 不宣称成功、不使用示例降级。
 */
function showFailure(error) {
  status.textContent = "未完成";
  message.textContent = error.message;
  console.error("[PRS routes] Operation failed; see page diagnostic.");
}

/* Functionality: 检查后端配置并初始化 OSM 底图。
 * Inputs: 同源配置、固定 SRI 的 Leaflet；Outputs: 地图/配置状态/事件。
 * Logic: 初始 NUS 视图标为区域预览；真实位置须用户点击；底图 load 与 tileerror 独立报告。
 * Constraints: 不预下载、不覆盖瓦片缓存、不隐藏署名；加载失败需用户手动刷新。
 */
async function initialize() {
  try {
    if (!globalThis.L)
      throw new Error("Leaflet 未加载，请检查 unpkg 网络后刷新页面。");
    const response = await fetch("./config/", {
      cache: "no-store",
      credentials: "omit",
    });
    const config = await response.json();
    if (response.status !== 503 && !response.ok)
      throw new Error("路线配置接口失败。");
    if (
      config.provider !== "openrouteservice" ||
      config.profile !== "foot-walking"
    )
      throw new Error("路线配置格式无效。");
    configured = config.configured === true;
    map = L.map("map").setView([NUS_START[1], NUS_START[0]], 16);
    tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    });
    tiles.on("loading", () => {
      tileFailed = false;
      document.querySelector("#tile-status").textContent = "底图加载中";
    });
    tiles.on("tileerror", () => {
      tileFailed = true;
      document.querySelector("#tile-status").textContent =
        "部分底图加载失败，请检查设备网络";
      console.error("[PRS maps] OSM tile failed.");
    });
    tiles.on("load", () => {
      if (!tileFailed)
        document.querySelector("#tile-status").textContent = "OSM 底图已加载";
    });
    tiles.addTo(map);
    map.on("click", (event) => {
      if (busy) return;
      if (!clearRoute()) return;
      setCoordinates("end", [event.latlng.lng, event.latlng.lat]);
      endMarker = L.circleMarker(event.latlng, { color: "#147d82", radius: 8 })
        .addTo(map)
        .bindTooltip("所选终点");
      message.textContent = "终点已选定。确认起点后点击规划步行路线。";
      status.textContent = "待规划";
    });
    status.textContent = configured ? "ORS 已配置" : "ORS 未配置";
    message.textContent = configured
      ? "可获取设备起点，点击地图选择终点；或填入 NUS 示例后测试路线。"
      : "服务器未配置 ORS_API_KEY，路线接口暂不可用。";
    setBusy(false);
    console.info(
      "[PRS routes] Map initialized; backend key stays server-side.",
    );
  } catch (error) {
    showFailure(error);
  }
}

/* Functionality: 将一次设备定位作为路线起点。
 * Inputs: 用户点击、浏览器权限；Outputs: 坐标/精度/来源和起点图层。
 * Logic: 操作期间禁用输入；失败不采用缓存位置或示例。
 * Constraints: 此动作不上传位置；只有后续规划请求才发送 PRS/ORS。
 */
async function locate() {
  if (!clearRoute()) return;
  setBusy(true);
  status.textContent = "定位中";
  message.textContent = "请允许浏览器使用设备位置。";
  document.querySelector("#location-source").textContent = "等待设备定位";
  document.querySelector("#accuracy").textContent = "—";
  document.querySelector("#start-longitude").value = document.querySelector(
    "#start-latitude",
  ).value = "";
  try {
    const { coords } = await getDevicePosition();
    setCoordinates("start", [coords.longitude, coords.latitude]);
    document.querySelector("#location-source").textContent = "设备当前位置";
    document.querySelector("#accuracy").textContent =
      `约 ${Math.round(coords.accuracy)} 米`;
    map.setView([coords.latitude, coords.longitude], 17);
    startMarker = L.circleMarker([coords.latitude, coords.longitude], {
      color: "#183047",
      radius: 8,
    })
      .addTo(map)
      .bindTooltip("设备起点");
    status.textContent = "起点已定位";
    message.textContent = "点击地图选择终点，然后规划步行路线。";
    console.info("[PRS routes] Device position acquired; not yet submitted.");
  } catch (error) {
    showFailure(error);
  } finally {
    setBusy(false);
  }
}

/* Functionality: 明确填入固定 NUS 测试坐标。
 * Inputs: 用户点击和 NUS_START/NUS_END；Outputs: 表单、来源标签、地图视图。
 * Logic: 不申请定位、不请求路线；Constraints: 绝不将示例标为设备当前位置。
 */
function useExample() {
  if (!clearRoute()) return;
  setCoordinates("start", NUS_START);
  setCoordinates("end", NUS_END);
  document.querySelector("#location-source").textContent =
    "NUS 固定示例（不是设备位置）";
  document.querySelector("#accuracy").textContent = "不适用";
  map.setView([NUS_START[1], NUS_START[0]], 16);
  status.textContent = "示例已填入";
  message.textContent = "点击规划步行路线，真实调用 ORS 验证这组示例起终点。";
}

/* Functionality: 显示真实 ORS 路线和步骤，并将其交给共享导航控制器。
 * Inputs: 用户 submit event、四个输入值；Outputs: 路线图层、汇总、指令列表。
 * Logic: 明确提交后清旧数据；经纬度顺序转换为 Leaflet 纬经度；用 DOM 文本和 data-language-original 标记保留英文原指令。
 * Constraints: 规划成功不自动开始导航；未包含语音调度或避障；失败无自动重试。
 */
async function plan(event) {
  event.preventDefault();
  if (!clearRoute()) return;
  setBusy(true);
  status.textContent = "请求路线中";
  message.textContent = "起终点将发送 PRS 服务端和 ORS，等待步行路线。";
  try {
    const start = parseCoordinateInputs(
      document.querySelector("#start-longitude").value,
      document.querySelector("#start-latitude").value,
    );
    const end = parseCoordinateInputs(
      document.querySelector("#end-longitude").value,
      document.querySelector("#end-latitude").value,
    );
    const route = await requestWalkingRoute(start, end);
    navigation.setRoute(route);
    const points = route.geometry.coordinates.map(([lng, lat]) => [lat, lng]);
    routeLayer = L.polyline(points, { color: "#147d82", weight: 5 }).addTo(map);
    startMarker = L.circleMarker(points[0], { color: "#183047", radius: 8 })
      .addTo(map)
      .bindTooltip("路线起点");
    endMarker = L.circleMarker(points.at(-1), { color: "#147d82", radius: 8 })
      .addTo(map)
      .bindTooltip("路线终点");
    map.fitBounds(routeLayer.getBounds(), { padding: [24, 24] });
    document.querySelector("#route-summary").textContent =
      `${Math.round(route.summary.distance_m)} 米 · 约 ${Math.ceil(route.summary.duration_s / 60)} 分钟 · ${route.steps.length} 条指令`;
    for (const step of route.steps) {
      const li = document.createElement("li");
      const label = document.createElement("strong");
      label.textContent = `${turnLabel(step.type)} · ${Math.round(step.distance_m)} 米`;
      const detail = document.createElement("p");
      detail.setAttribute("data-language-original", "");
      detail.textContent = step.instruction;
      li.append(label, detail);
      document.querySelector("#steps").append(li);
    }
    document.querySelector("#route-attribution").textContent =
      route.attribution;
    status.textContent = "路线已返回";
    message.textContent = `ORS 步行路线验证成功。请求编号：${route.request_id}。底图状态见地图下方。`;
    console.info("[PRS routes] Walking route and instructions rendered.");
  } catch (error) {
    clearRoute();
    showFailure(error);
  } finally {
    setBusy(false);
  }
}

button.addEventListener("click", locate);
// Functionality: 显示共享导航位置；Inputs: 控制器事件；Outputs: 单一位置图层。
// Logic: 活跃/到达时更新标记，停止/清路线时移除；Constraints: 不改路线，不自动移动视图。
document.addEventListener("prs-navigation-change", ({detail}) => {
  if (!map) return;
  if (!detail.position || (!detail.active && detail.state !== "arrived")) {
    navigationMarker?.remove();
    navigationMarker = undefined;
    return;
  }
  const point = [detail.position.latitude, detail.position.longitude];
  if (navigationMarker) navigationMarker.setLatLng(point);
  else navigationMarker = L.circleMarker(point, {color: "#b45309", radius: 9})
    .addTo(map).bindTooltip("导航设备位置");
});
exampleButton.addEventListener("click", useExample);
document.querySelector("#route-form").addEventListener("submit", plan);
for (const input of document.querySelectorAll("#route-form input"))
  input.addEventListener("input", () => {
    if (!clearRoute()) return;
    status.textContent = "待规划";
    if (input.id.startsWith("start-")) {
      document.querySelector("#location-source").textContent = "手动输入起点";
      document.querySelector("#accuracy").textContent = "不适用";
    }
  });
initialize();
