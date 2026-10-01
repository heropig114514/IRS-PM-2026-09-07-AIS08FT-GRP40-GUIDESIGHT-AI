/* Responsibilities: Google SDK 加载、设备定位和地图瓦片就绪的独立异步契约。
 * Implementation: 显式失败和超时；不重试、不替换位置；依赖注入仅用于测试。
 * Related Modules: maps-page.js, tests/maps-client.test.mjs, core.maps。
 * Declaration Index: loadGoogleMaps — 加载 SDK；getDevicePosition — 请求位置；waitForTiles — 验证瓦片事件。
 * Variable Index: None；超时默认值在各函数参数声明中。
 */

/* Functionality: 以浏览器 key 加载官方 SDK，并处理认证/网络/超时失败。
 * Inputs: apiKey；document/window DOM 环境；nonce CSP 值；onAuthFailure 后续认证失败通知；timeoutMs 毫秒。
 * Outputs: Promise<google.maps>；全局 SDK 回调和 script 元素副作用；不记录 key。
 * Logic: SDK 回调仅证明库已加载；认证错误仍可在此之后发生，持续通知 UI。
 * Constraints: 每页只调用一次；失败不自动重试；必须传网站限制型 key。
 */
export function loadGoogleMaps(
  apiKey,
  {
    document = globalThis.document,
    window = globalThis.window,
    nonce = "",
    onAuthFailure = () => {},
    timeoutMs = 10000,
  } = {},
) {
  if (!apiKey) return Promise.reject(new Error("Google Maps key 未配置。"));
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    const timer = setTimeout(() => {
      script.remove();
      delete window.prsMapsReady;
      reject(new Error("Google Maps SDK 加载超时，请检查网络。"));
    }, timeoutMs);
    window.gm_authFailure = () => {
      clearTimeout(timer);
      script.remove();
      delete window.prsMapsReady;
      onAuthFailure();
      reject(
        new Error(
          "Google Maps 认证失败，请检查 key、网站限制、API 启用和配额。",
        ),
      );
    };
    window.prsMapsReady = () => {
      clearTimeout(timer);
      delete window.prsMapsReady;
      if (!window.google?.maps?.Map) {
        reject(new Error("Google Maps SDK 未提供地图接口。"));
        return;
      }
      resolve(window.google.maps);
    };
    script.onerror = () => {
      clearTimeout(timer);
      delete window.prsMapsReady;
      script.remove();
      reject(new Error("无法连接 Google Maps，请检查设备网络或 CSP 错误。"));
    };
    const url = new URL("https://maps.googleapis.com/maps/api/js");
    url.search = new URLSearchParams({
      key: apiKey,
      loading: "async",
      callback: "prsMapsReady",
      v: "weekly",
    });
    script.src = url.href;
    script.async = true;
    script.nonce = nonce;
    document.head.append(script);
  });
}

/* Functionality: 获取当前设备经纬度和精度，保留浏览器失败语义。
 * Inputs: navigator（默认浏览器）；隐式读取安全上下文；只由用户点击调用。
 * Outputs: Promise<GeolocationPosition>；权限对话框；不发给 PRS 后端、不记录坐标。
 * Logic: 一次高精度定位、10 秒超时、不使用缓存；错误码映射成可操作信息。
 * Constraints: 不推断 IP 位置，不生成测试坐标，不自动重试。
 */
export function getDevicePosition(navigator = globalThis.navigator) {
  if (!navigator.geolocation)
    return Promise.reject(
      new Error("此浏览器不支持定位，请使用 HTTPS 系统浏览器。"),
    );
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      resolve,
      (error) => {
        const messages = {
          1: "定位权限被拒绝，请在浏览器的网站权限中允许位置。",
          2: "设备无法获取位置，请检查定位服务。",
          3: "设备定位超时，请检查 GPS 或网络后手动再试。",
        };
        reject(new Error(messages[error.code] || "设备定位失败。"));
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 },
    );
  });
}

/* Functionality: 区分地图对象创建和首次实际瓦片加载事件。
 * Inputs: maps SDK、map 实例、timeoutMs 毫秒。
 * Outputs: Promise<void>；临时事件监听器在成功或超时后释放。
 * Logic: 监听一次 tilesloaded；没有事件则明确失败，不能只凭 script HTTP 200 宣称地图可用。
 * Constraints: 不证明设备坐标准确或所有地图服务可用；不重试瓦片。
 */
export function waitForTiles(maps, map, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    const listener = maps.event.addListenerOnce(map, "tilesloaded", () => {
      clearTimeout(timer);
      resolve();
    });
    const timer = setTimeout(() => {
      maps.event.removeListener(listener);
      reject(
        new Error(
          "位置已获取，但地图瓦片加载超时，请检查 Google Maps 网络、key 或配额。",
        ),
      );
    }, timeoutMs);
  });
}
