/* Responsibilities: 浏览器定位、表单坐标和同源 ORS 路线请求契约。
 * Implementation: 显式验证与超时；不重试、不替代位置；key 不进入浏览器。
 * Related Modules: maps-page.js, tests/maps-client.test.mjs, core.maps/services。
 * Declaration Index: getDevicePosition — 设备定位；parseCoordinateInputs — 输入校验；
 * validateRoute — 返回契约；requestWalkingRoute — 路线调用；turnLabel — 转向类型中文说明。
 * Variable Index: TURN_LABELS — ORS 0–13 官方指令类型；超时默认值位于函数参数。
 */
const TURN_LABELS = [
  "左转",
  "右转",
  "急左转",
  "急右转",
  "稍向左",
  "稍向右",
  "直行",
  "进入环岛",
  "离开环岛",
  "掉头",
  "到达终点",
  "出发",
  "靠左",
  "靠右",
];

/* Functionality: 获取当前设备位置，不上传或保存。
 * Inputs: navigator 默认浏览器；Outputs: GeolocationPosition Promise 和权限请求。
 * Logic: 一次高精度定位，10 秒超时、零缓存；浏览器错误明确呈现。
 * Constraints: 只由用户点击调用；不推断 IP 位置或生成示例坐标。
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
          1: "定位权限被拒绝，请在网站权限中允许位置。",
          2: "设备无法获取位置，请检查定位服务。",
          3: "设备定位超时，请检查 GPS 或网络后手动再试。",
        };
        reject(new Error(messages[error.code] || "设备定位失败。"));
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 },
    );
  });
}

/* Functionality: 将两个表单值转为 [经度,纬度]。
 * Inputs: longitude/latitude 文本或数字；Outputs: 两个有限数值，错误抛 Error。
 * Logic: 拒绝空值、bool、非法类型、非有限和越界；保持经度在前。
 * Constraints: 不补默认值，不将缺失位置解释为零点。
 */
export function parseCoordinateInputs(longitude, latitude) {
  const values = [longitude, latitude];
  for (const [i, value] of values.entries()) {
    if (
      !["string", "number"].includes(typeof value) ||
      String(value).trim() === "" ||
      !Number.isFinite(Number(value)) ||
      Math.abs(Number(value)) > [180, 90][i]
    )
      throw new Error("请填写有效经纬度：经度 -180～180，纬度 -90～90。");
  }
  return values.map(Number);
}

/* Functionality: 验证后端路线字段，阻止错误内容进入地图和列表。
 * Inputs: route JSON；Outputs: 原 route 或 Error。
 * Logic: 固定 provider/profile、LineString、有限数值和步骤类型/索引；不更改路线。
 * Constraints: 服务返回正确不代表路线适合视障安全通行。
 */
export function validateRoute(route) {
  if (
    route?.provider !== "openrouteservice" ||
    route.profile !== "foot-walking" ||
    route.geometry?.type !== "LineString" ||
    !Array.isArray(route.geometry.coordinates) ||
    route.geometry.coordinates.length < 2 ||
    !Array.isArray(route.steps) ||
    !route.steps.length
  )
    throw new Error("服务端路线格式无效。");
  for (const point of route.geometry.coordinates) {
    if (
      !Array.isArray(point) ||
      point.length !== 2 ||
      point.some((v) => typeof v !== "number")
    )
      throw new Error("路线坐标格式无效。");
    parseCoordinateInputs(...point);
  }
  for (const step of route.steps) {
    turnLabel(step.type);
    if (
      typeof step.instruction !== "string" ||
      !step.instruction.trim() ||
      !Array.isArray(step.way_points) ||
      step.way_points.length !== 2 ||
      step.way_points.some((v) => !Number.isInteger(v)) ||
      step.way_points[0] < 0 ||
      step.way_points[0] > step.way_points[1] ||
      step.way_points[1] >= route.geometry.coordinates.length
    )
      throw new Error("转向指令格式无效。");
  }
  const values = [
    route.summary?.distance_m,
    route.summary?.duration_s,
    ...route.steps.flatMap((step) => [step.distance_m, step.duration_s]),
  ];
  if (values.some((v) => typeof v !== "number" || !Number.isFinite(v) || v < 0))
    throw new Error("路线距离或时长无效。");
  return route;
}

/* Functionality: 请求后端 ORS 步行路线，超时或 HTTP 失败明确终止。
 * Inputs: start/end 数字坐标；fetchFn、url、timeoutMs 可注入；Outputs: 规范路线 Promise。
 * Logic: POST JSON，无 Cookie；25 秒 AbortController 边界；安全错误码与请求标识支持诊断。
 * Constraints: 只在用户点击时调用；不自动重试；真实位置会发送 PRS 和 ORS，key 留在后端。
 */
export async function requestWalkingRoute(
  start,
  end,
  { fetchFn = globalThis.fetch, url = "./route/", timeoutMs = 25000 } = {},
) {
  parseCoordinateInputs(...start);
  parseCoordinateInputs(...end);
  if (start[0] === end[0] && start[1] === end[1])
    throw new Error("起点和终点不能相同。");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchFn(url, {
      method: "POST",
      mode: "same-origin",
      credentials: "omit",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start, end }),
      signal: controller.signal,
    });
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error(
        `路线接口返回不可解析的 JSON（HTTP ${response.status}）。`,
      );
    }
    if (!response.ok)
      throw new Error(
        `${data.detail || "路线请求失败。"}（${data.code || response.status}${data.request_id ? " / " + data.request_id : ""}）`,
      );
    return validateRoute(data);
  } catch (error) {
    if (controller.signal.aborted)
      throw new Error("路线请求超过 25 秒，请检查网络后手动再试。");
    if (error instanceof TypeError)
      throw new Error("路线网络请求失败，请检查连接。");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

/* Functionality: 给出 ORS 指令类型的中文名称。
 * Inputs: type 整数 0–13；Outputs: 中文标签，非法类型抛 Error。
 * Logic: 固定映射不解析或改写街道名称；Constraints: 原始英文 instruction 单独保留。
 */
export function turnLabel(type) {
  if (!Number.isInteger(type) || type < 0 || type >= TURN_LABELS.length)
    throw new Error("未知 ORS 转向类型。");
  return TURN_LABELS[type];
}
