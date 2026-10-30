/* Responsibilities: Integrate Leaflet/OSM, entry-time device location, walking routes and shared continuous navigation.
 * Implementation: Request one location fix as the page opens, initialize one map, serialize user actions,
 * report errors and tile state separately, and expose an explicit retry after location failure.
 * Related Modules: maps-client.js, navigation-ui.js, maps.html, i18n.js, core.maps/services.
 * Declaration Index: setCoordinates — fill coordinates; setBusy — action lock; clearRoute — discard stale routes;
 * showFailure — display errors; initialize — configuration/map; locate — device origin; useExample — explicit sample;
 * plan — request a route; describeMap — structured state; planDestination — agent inputs to existing route controls;
 * mapsReady — settled map/location initialization.
 * Variable Index: button/planButton/exampleButton/status/message — state DOM; map/tiles — Leaflet instances;
 * routeLayer/startMarker/endMarker — replaceable layers; busy — action lock; configured — backend configuration;
 * tileFailed — current tile failure; navigationMarker — live position; NUS_START/NUS_END — fixed test points;
 * lastFailure — latest error; startSource — origin provenance; startupPosition — one page-entry location result;
 * mapsControl — semantic MCP interface.
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
let lastFailure = null, startSource = null;

/* Functionality: Fill origin/destination without external calls.
 * Inputs: prefix (start/end), point [longitude,latitude]; Outputs: Input values.
 * Logic: Display six decimal places; Constraints: Callers must know the coordinate source.
 */
function setCoordinates(prefix, point) {
  document.querySelector(`#${prefix}-longitude`).value = point[0].toFixed(6);
  document.querySelector(`#${prefix}-latitude`).value = point[1].toFixed(6);
}

/* Functionality: Serialize actions to prevent location and route updates from overwriting each other.
 * Inputs: Boolean value and configured state; Outputs: Disabled state of buttons/fields.
 * Logic: Product pages omit the optional NUS sample button; diagnostic pages retain it. Configuration
 * restricts only the route button, while map selection also checks busy. Constraints: No queueing or retries.
 */
function setBusy(value) {
  busy = value;
  button.disabled = value;
  if (exampleButton) exampleButton.disabled = value;
  planButton.disabled = value || !configured;
  for (const input of document.querySelectorAll("#route-form input"))
    input.disabled = value;
}

/* Functionality: Stop navigation and remove stale route/origin/destination layers.
 * Inputs: Current layers/DOM; Outputs: true with cleared layers/list, or false with diagnostics.
 * Logic: Clear the shared navigation route before removing layers.
 * Constraints: Stop failure aborts the new operation without retrying or leaving the form locked.
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

/* Functionality: Report failures without logging positions or keys.
 * Inputs: error; Outputs: Status text and fixed diagnostic log.
 * Logic: Insert safe text through textContent; Constraints: Do not claim success or substitute sample data.
 */
function showFailure(error) {
  lastFailure = error.message;
  status.textContent = "未完成";
  message.textContent = error.message;
  console.error("[PRS routes] Operation failed; see page diagnostic.");
}

/* Functionality: Check backend configuration and initialize OSM tiles.
 * Inputs: Same-origin configuration and pinned/SRI Leaflet; Outputs: Map, configuration state and events.
 * Logic: Mark the NUS view as a temporary preview; use the page-entry location request after map setup;
 * report tile load/tileerror separately.
 * Constraints: No tile prefetch, cache overrides or hidden attribution; a failed location is never replaced
 * with the preview and can be retried explicitly.
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
      ? (exampleButton
        ? "页面打开时请求设备位置，点击地图选择终点；或填入 NUS 示例后测试路线。"
        : "页面打开时请求设备位置并设为起点，点击地图选择目的地。")
      : "服务器未配置 ORS_API_KEY，路线接口暂不可用。";
    setBusy(false);
    console.info(
      "[PRS routes] Map initialized; backend key stays server-side.",
    );
    await locate(startupPosition);
  } catch (error) {
    showFailure(error);
  }
}

/* Functionality: Use one device location fix as the route origin.
 * Inputs: Page-entry result, user retry or bound MCP request and browser permission; Outputs: Coordinates, accuracy,
 * provenance, origin layer and structured result.
 * Logic: Disable route controls during acquisition; reveal product retry only on failure and hide it after success;
 * failures never use cached/sample locations. Constraints: Location stays in page state until route planning;
 * MCP returns results to its agent; planning sends coordinates to PRS/ORS.
 */
async function locate(positionResult = null) {
  if (busy || !map) return {accepted: false, reason: busy ? "busy" : "map_not_ready"};
  lastFailure = null;
  if (!clearRoute()) return {accepted: false, reason: "stop_failed"};
  startSource = null;
  setBusy(true);
  status.textContent = "定位中";
  message.textContent = "请允许浏览器使用设备位置。";
  document.querySelector("#location-source").textContent = "等待设备定位";
  document.querySelector("#accuracy").textContent = "—";
  document.querySelector("#start-longitude").value = document.querySelector(
    "#start-latitude",
  ).value = "";
  try {
    const result = positionResult ? await positionResult : {position: await getDevicePosition()};
    if (result.error) throw result.error;
    const { coords } = result.position;
    startSource = "device";
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
    button.hidden = !exampleButton;
    console.info("[PRS routes] Device position acquired; not yet submitted.");
  } catch (error) {
    if (!exampleButton) button.hidden = false;
    showFailure(error);
  } finally {
    setBusy(false);
  }
  return {accepted: !lastFailure, ...describeMap()};
}

/* Functionality: Explicitly fill fixed NUS test coordinates.
 * Inputs: User click and NUS_START/NUS_END; Outputs: Form, provenance labels and map view.
 * Logic: No location permission or route request; Constraints: Never label the sample as the device location.
 */
function useExample() {
  if (!clearRoute()) return;
  startSource = "example";
  setCoordinates("start", NUS_START);
  setCoordinates("end", NUS_END);
  document.querySelector("#location-source").textContent =
    "NUS 固定示例（不是设备位置）";
  document.querySelector("#accuracy").textContent = "不适用";
  map.setView([NUS_START[1], NUS_START[0]], 16);
  status.textContent = "示例已填入";
  message.textContent = "点击规划步行路线，真实调用 ORS 验证这组示例起终点。";
}

/* Functionality: Render actual ORS routes/steps and pass them to the shared navigation controller.
 * Inputs: User submit event (absent for MCP) and four form values; Outputs: Route layer, summary,
 * instructions and result. Logic: Clear old data after explicit submission; convert longitude/latitude
 * to Leaflet latitude/longitude; preserve English instructions with DOM text and data-language-original.
 * Constraints: Successful planning does not start navigation; no speech scheduling, obstacle avoidance or retries.
 */
async function plan(event) {
  event?.preventDefault();
  if (busy || !map) return {accepted: false, reason: busy ? "busy" : "map_not_ready"};
  lastFailure = null;
  if (!clearRoute()) return {accepted: false, reason: "stop_failed"};
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
  return {accepted: !lastFailure, ...describeMap()};
}

/* Functionality: Expose form/shared navigation state for missing-input decisions.
 * Inputs: DOM/busy/configured/startSource; Outputs: Valid coordinates or null, provenance and route.
 * Logic: Do not convert empty fields to zero; Constraints: Read-only, no location request or default coordinates. */
export function describeMap() {
  const coordinates = {};
  for (const prefix of ["start", "end"]) {
    const longitude = document.querySelector(`#${prefix}-longitude`).value;
    const latitude = document.querySelector(`#${prefix}-latitude`).value;
    try { coordinates[prefix] = parseCoordinateInputs(longitude, latitude); }
    catch { coordinates[prefix] = null; }
  }
  return {available: true, ready: Boolean(map), busy, configured, origin: coordinates.start,
    destination: coordinates.end, origin_source: startSource, error: lastFailure,
    navigation: navigation.describe()};
}

/* Functionality: Fill an explicit destination and use the same planning workflow.
 * Inputs: Optional destination/origin [longitude,latitude]; Outputs: plan Promise.
 * Logic: Validate every argument before writing; omitted values retain current fields.
 * Constraints: No inferred coordinates or automatic navigation; busy/uninitialized states leave forms unchanged. */
export async function planDestination({destination = null, origin = null} = {}) {
  if (busy || !map) return {accepted: false, reason: busy ? "busy" : "map_not_ready"};
  for (const point of [destination, origin]) {
    if (point !== null) {
      if (!Array.isArray(point) || point.length !== 2) throw new Error("坐标须为经度、纬度。");
      parseCoordinateInputs(...point);
    }
  }
  if (destination) setCoordinates("end", destination);
  if (origin) {
    setCoordinates("start", origin);
    startSource = "user";
    document.querySelector("#location-source").textContent = "手动输入起点";
    document.querySelector("#accuracy").textContent = "不适用";
  }
  return plan();
}

export const mapsControl = {describe: describeMap, locate, plan: planDestination};

const startupPosition = getDevicePosition().then(
  position => ({position}),
  error => ({error}),
);

button.addEventListener("click", locate);
// Functionality: Render shared navigation position; Inputs: Controller events; Outputs: One position layer.
// Logic: Update while active/arrived, remove on stop/route clear; Constraints: No route edits or automatic panning.
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
if (exampleButton) exampleButton.addEventListener("click", useExample);
document.querySelector("#route-form").addEventListener("submit", plan);
for (const input of document.querySelectorAll("#route-form input"))
  input.addEventListener("input", () => {
    if (!clearRoute()) return;
    status.textContent = "待规划";
    if (input.id.startsWith("start-")) {
      startSource = "user";
      document.querySelector("#location-source").textContent = "手动输入起点";
      document.querySelector("#accuracy").textContent = "不适用";
    }
  });
export const mapsReady = initialize();
