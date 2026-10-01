/* Responsibilities: 地图页面状态、配置获取和用户发起的定位显示。
 * Implementation: SDK 初始化一次；点击后设备定位，等待瓦片；错误明确呈现且无隐式重试。
 * Related Modules: maps-client.js, maps.html, core.maps。
 * Declaration Index: showFailure — 错误状态；initialize — 初始化配置；locate — 定位并创建地图。
 * Variable Index: button/status/message — 状态 DOM；maps — SDK；authenticationFailed — 认证失败锁。
 */
import {
  getDevicePosition,
  loadGoogleMaps,
  waitForTiles,
} from "./maps-client.js";

const button = document.querySelector("#locate");
const status = document.querySelector("#maps-status");
const message = document.querySelector("#maps-message");
let maps;
let authenticationFailed = false;

/* Functionality: 呈现诊断错误而不输出敏感值。
 * Inputs: Error、当前 DOM；Outputs: 状态文本与固定控制台错误标识。
 * Logic: 文本通过 textContent 插入；调用方管理按钮及认证锁。
 * Constraints: 不向控制台写入 key、坐标或异常 URL。
 */
function showFailure(error) {
  status.textContent = "未完成";
  message.textContent = error.message;
  console.error("[PRS maps] Operation failed; see page diagnostic message.");
}

/* Functionality: 检查配置并连接 SDK，使定位按钮可用。
 * Inputs: 同源 config/ 接口、脚本 nonce、当前页面；Outputs: DOM/SDK 状态。
 * Logic: 未配置时不访问 Google；库加载与后续地图瓦片就绪分别报告。
 * Constraints: SDK 失败后只通过手动刷新重新初始化；无自动重试。
 */
async function initialize() {
  try {
    const response = await fetch("./config/", { cache: "no-store" });
    if (response.status === 503)
      throw new Error(
        "尚未配置 GOOGLE_MAPS_API_KEY。请提供 Maps JavaScript API key 或官方 Demo Key 后再测试。",
      );
    if (!response.ok)
      throw new Error(`地图配置接口失败（HTTP ${response.status}）。`);
    const config = await response.json();
    status.textContent = "连接 Google 中";
    maps = await loadGoogleMaps(config.apiKey, {
      nonce: document.querySelector("script[nonce]").nonce,
      onAuthFailure: () => {
        authenticationFailed = true;
        button.disabled = true;
        showFailure(
          new Error(
            "Google Maps 认证失败：检查 key、网站限制、API 启用和配额，然后刷新页面。",
          ),
        );
      },
    });
    if (authenticationFailed) return;
    status.textContent = "SDK 已加载";
    message.textContent =
      "点击显示我的位置。地图瓦片加载完成后才会报告测试成功。";
    button.disabled = false;
    console.info("[PRS maps] SDK loaded; waiting for user location request.");
  } catch (error) {
    showFailure(error);
  }
}

/* Functionality: 显示真实设备坐标、精度和 Google 地图位置气泡。
 * Inputs: 用户点击、SDK、浏览器位置权限；Outputs: 坐标 DOM、地图、状态日志。
 * Logic: 单次定位后创建中心地图及 InfoWindow，等待 tilesloaded；最终恢复手动按钮。
 * Constraints: 坐标不发给 PRS；认证失败锁优先于异步成功；不回退示例位置。
 */
async function locate() {
  button.disabled = true;
  status.textContent = "定位中";
  message.textContent = "请允许浏览器使用设备位置。";
  for (const id of ["latitude", "longitude", "accuracy"]) {
    document.querySelector(`#${id}`).textContent = "—";
  }
  console.info("[PRS maps] User requested device location.");
  try {
    const { coords } = await getDevicePosition();
    if (authenticationFailed) return;
    const position = { lat: coords.latitude, lng: coords.longitude };
    document.querySelector("#latitude").textContent =
      coords.latitude.toFixed(6);
    document.querySelector("#longitude").textContent =
      coords.longitude.toFixed(6);
    document.querySelector("#accuracy").textContent =
      `约 ${Math.round(coords.accuracy)} 米`;
    status.textContent = "加载地图中";
    message.textContent = "设备位置已获取，正在验证地图瓦片。";
    const map = new maps.Map(document.querySelector("#map"), {
      center: position,
      zoom: 17,
    });
    const label = document.createElement("div");
    label.textContent = "这台设备的位置";
    new maps.InfoWindow({ position, content: label }).open({ map });
    await waitForTiles(maps, map);
    if (authenticationFailed) return;
    status.textContent = "地图已显示";
    message.textContent =
      "Google Maps 瓦片已加载。位置精度由设备报告；再次点击可重新定位。";
    console.info("[PRS maps] Device location and map tiles displayed.");
  } catch (error) {
    if (!authenticationFailed) showFailure(error);
  } finally {
    button.disabled = authenticationFailed;
  }
}

button.addEventListener("click", locate);
initialize();
