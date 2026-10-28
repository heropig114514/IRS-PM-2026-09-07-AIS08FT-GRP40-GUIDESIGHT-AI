/* Responsibilities: Define browser location, form-coordinate and same-origin ORS route contracts.
 * Implementation: Explicit validation/timeouts; no retries, substitute positions or browser-side keys.
 * Related Modules: maps-page.js, tests/maps-client.test.mjs, core.maps/services.
 * Declaration Index: getDevicePosition — locate device; parseCoordinateInputs — validate input;
 * validateRoute — response contract; requestWalkingRoute — route request; turnLabel — Chinese turn labels.
 * Variable Index: TURN_LABELS — official ORS instruction types 0–13; timeout defaults are function parameters.
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

/* Functionality: Obtain device location without uploading or persisting it.
 * Inputs: navigator, defaulting to the browser; Outputs: GeolocationPosition Promise and permission request.
 * Logic: One high-accuracy fix with a 10-second timeout and zero cache; expose browser errors explicitly.
 * Constraints: The unified page requests one fix on entry; retries and agent requests remain explicit;
 * no IP inference or sample coordinates.
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

/* Functionality: Convert two form values into [longitude,latitude].
 * Inputs: longitude/latitude text or numbers; Outputs: Two finite values, or an Error.
 * Logic: Reject empty, boolean, invalid-type, nonfinite and out-of-range values; longitude comes first.
 * Constraints: Supply no defaults and never interpret missing position as zero.
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

/* Functionality: Validate backend route fields before rendering maps/lists.
 * Inputs: route JSON; Outputs: Original route or Error.
 * Logic: Check fixed provider/profile, LineString, finite values and step types/indexes without altering routes.
 * Constraints: A valid response does not establish safe travel for visually impaired pedestrians.
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

/* Functionality: Request an ORS walking route from the backend, failing explicitly on timeout/HTTP errors.
 * Inputs: Numeric start/end coordinates; injectable fetchFn, url and timeoutMs; Outputs: Normalized route Promise.
 * Logic: POST JSON without cookies; 25-second AbortController boundary; safe error codes/request IDs aid diagnosis.
 * Constraints: Require explicit user/agent planning requests; no retries; PRS/ORS receive coordinates, keys stay server-side.
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

/* Functionality: Return the Chinese label for an ORS instruction type.
 * Inputs: Integer type 0–13; Outputs: Chinese label; invalid types throw Error.
 * Logic: Fixed mapping never parses or rewrites street names; Constraints: Preserve original English instructions separately.
 */
export function turnLabel(type) {
  if (!Number.isInteger(type) || type < 0 || type >= TURN_LABELS.length)
    throw new Error("未知 ORS 转向类型。");
  return TURN_LABELS[type];
}
