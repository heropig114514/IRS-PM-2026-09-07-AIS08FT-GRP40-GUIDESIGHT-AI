/* Responsibilities: Adapt shared diagnostic presentation to the everyday assist product.
 * Implementation: Fixed bilingual labels and passive DOM mirrors; original controllers retain
 * route, GPS, NUS endpoint filling, camera and output ownership. Only invalid coordinate fields reveal their details.
 * Related Modules: assist-product.html/css, assist-mvp.js; i18n.js, maps-page.js, camera.js.
 * Declaration Index: productMessage; coordinateSummary; coordinateSummary.valid; mountProduct; mountProduct.node; mountProduct.write;
 * mountProduct.render; mountProduct.revealInvalid; mountProduct.stop;
 * mountProduct.refresh; mountProduct.changed; mountProduct.onInput; mountProduct.onPageHide.
 * Variable Index: COPY — fixed product labels; MESSAGES — presentation-only diagnostics mapping;
 * stateObserver/languageObserver observe current DOM, never poll sensors or infer route/risk.
 */
import {translate} from "../i18n.js";

const COPY = {
  title: ["PRS · 出行助手", "PRS · Walking companion"],
  skip: ["跳到主要操作", "Skip to main controls"], brand: ["出行助手", "Walking companion"],
  language: ["界面语言", "Language"], eyebrow: ["步行导航 · 环境提醒", "WALKING DIRECTIONS · AWARENESS"],
  heading: ["出行，从这里开始。", "Your walk starts here."],
  intro: ["选择目的地，开启摄像头，沿途听取导航与风险提醒。", "Choose a destination and turn on your camera for directions and risk alerts along the way."],
  speechHeading: ["语音提醒", "Voice guidance"], activate: ["启动语音", "Activate voice"],
  recoverSpeech: ["恢复语音", "Resume voice"], recoverVibration: ["恢复振动", "Resume vibration"],
  navigation: ["步行导航", "Walking directions"], camera: ["环境感知", "Surroundings"], preferences: ["提醒设置", "Alert preferences"],
  routeHint: ["获取当前位置后，点选地图上的目的地。", "Get your current location, then tap your destination on the map."],
  locate: ["使用当前位置", "Use my location"], mapHint: ["地图初始位置仅供浏览；可使用当前位置或 NUS 示例。", "The initial map is a preview. Use your location or the NUS example."],
  example: ["填入 NUS 示例", "Fill NUS example"],
  origin: ["起点", "From"], destination: ["目的地", "To"], coordinates: ["手动输入坐标", "Enter coordinates manually"],
  plan: ["规划步行路线", "Find walking route"], startNavigation: ["开始导航", "Start navigation"], stopNavigation: ["结束导航", "End navigation"],
  routeDetails: ["查看路线详情", "Route details"],
  cameraHint: ["点击开始检测，自动使用手机后置摄像头。首次使用请允许摄像头权限。", "Tap Start detection to use your phone's rear camera. Allow camera access when prompted."],
  startCameraShortcut: ["开启环境感知", "Start surroundings"],
  liveCamera: ["实时画面", "Live camera"], annotatedCamera: ["识别结果", "Detection results"],
  resultPending: ["开始检测后，显示带标记框的返回画面", "Start detection to see the returned image with object boxes"],
  resultAlt: ["带目标标记框的检测画面", "Detection image with object boxes"],
  cameraOff: ["摄像头尚未开启", "Camera is off"], startDetection: ["开始检测", "Start detection"], stopDetection: ["停止检测", "Stop detection"],
  settingsHint: ["声音跟随界面语言，音量通过手机按键调节。", "Voice follows the interface language. Adjust volume with your device buttons."],
  speechToggle: ["语音提醒", "Voice guidance"], navigationToggle: ["导航播报", "Spoken directions"],
  riskToggle: ["风险预警", "Risk alerts"], vibrationToggle: ["振动提醒", "Vibration alerts"],
  foreground: ["使用时请保持页面在前台。离开或锁屏后，需要重新开始导航和检测。", "Keep this page in the foreground. Restart navigation and detection after leaving the page or locking the screen."],
  diagnostics: ["测试与诊断", "Testing & diagnostics"], stopAll: ["停止导航与检测", "Stop navigation & detection"],
  routeLoading: ["正在准备路线服务。", "Preparing route service."],
  unselected: ["尚未选择", "Not selected"], chooseDestination: ["请在地图上点选", "Tap the map to choose"],
  currentOrigin: ["设备当前位置", "Current device location"], chosenOrigin: ["已输入起点", "Start point entered"], chosenDestination: ["已选择目的地", "Destination selected"],
  vibrationUnavailable: ["此设备浏览器不支持振动提醒。", "This browser does not support vibration alerts."],
  mainNavigation: ["主要功能", "Main features"], preview: ["摄像头预览", "Camera preview"],
};
const MESSAGES = new Map([
  ["ORS 已配置", ["路线服务已就绪", "Routes ready"]],
  ["ORS 未配置", ["路线服务不可用", "Routes unavailable"]],
  ["检查配置中", ["正在准备", "Preparing"]],
  ["正在检查 ORS 配置。", COPY.routeLoading],
  ["可获取设备起点，点击地图选择终点；或填入 NUS 示例后测试路线。", COPY.routeHint],
  ["点击规划步行路线，真实调用 ORS 验证这组示例起终点。", ["已填入 NUS 示例起终点，不代表当前位置。点击规划步行路线继续。", "NUS example endpoints are filled, not your current location. Tap Find walking route to continue."]],
  ["服务器未配置 ORS_API_KEY，路线接口暂不可用。", ["路线服务暂不可用，请稍后再试。", "Route service is unavailable. Please try again later."]],
  ["Leaflet 未加载，请检查 unpkg 网络后刷新页面。", ["地图未能加载，请检查网络后刷新页面。", "The map could not load. Check your connection and refresh the page."]],
  ["路线配置接口失败。", ["路线服务连接失败，请检查网络后刷新页面。", "Could not connect to route service. Check your connection and refresh."]],
  ["规划路线后，可由此处或 agent 开始导航。", ["规划路线后，即可开始导航。", "Find a route to start navigation."]],
  ["起终点将发送 PRS 服务端和 ORS，等待步行路线。", ["正在规划步行路线，请稍候。", "Finding your walking route. Please wait."]],
  ["ORS 网络 I/O 超时，请检查网络后手动尝试。", ["路线请求超时，请检查网络后重新规划。", "The route request timed out. Check your connection and try again."]],
  ["尚未连接", ["未开启", "Off"]],
  ["摄像头和连接已释放。再次点击开始即可重新采集。", ["检测已停止，摄像头已关闭。", "Detection stopped. The camera is off."]],
  ["本地感知已连接；首帧可能需要加载模型，请等待。", ["正在准备检测，请稍候。", "Preparing detection. Please wait."]],
  ["YOLO26n 本地 CUDA 检测 80 类；图像仅发送本机服务。", ["正在准备检测，请稍候。", "Preparing detection. Please wait."]],
  ["本次视频会话已结束", ["检测已结束", "Detection ended"]],
  ["本地视频会话已结束并释放资源。再次点击开始可开启新会话。", ["检测已结束，点击开始检测可重新开启。", "Detection ended. Tap Start detection to begin again."]],
]);

/* Functionality: Present an existing controller message without diagnostic identifiers.
 * Inputs: Original message and current interface language. Outputs: Plain user-facing text.
 * Logic: Translate fixed technical labels; keep meaningful unknown errors without request IDs.
 * Constraints: Presentation only; does not suppress failures, substitute providers or claim safety. */
export function productMessage(value, language = "zh-CN") {
  const text = String(value || "").trim(), english = language === "en";
  const base = text.replace(/（[a-z][a-z0-9_]*\s*\/\s*[a-f0-9]+）$/i, "");
  if (MESSAGES.has(base)) return MESSAGES.get(base)[english ? 1 : 0];
  if (base.startsWith("ORS 步行路线验证成功。请求编号："))
    return english ? "Your route is ready. Tap Start navigation." : "路线已规划，点击开始导航。";
  if (/^本帧 \d+ 个目标 ·/.test(base) || /^本地检测中/.test(base))
    return english ? "Detection is running. Risk alerts will be announced." : "正在检测，发现风险时会播报提醒。";
  if (/^已连接.*(?:CUDA|PRF2)/.test(base))
    return english ? "Detection is connected." : "检测已连接。";
  return translate(base, english ? "en" : "zh-CN");
}

/* Functionality: Describe explicit route fields without implying they are live GPS fixes.
 * Inputs: Four coordinate field values, original source label, interface language.
 * Outputs: Two short position selection labels. Logic: Only complete bounded numeric pairs qualify.
 * Constraints: Does not geocode, change values or label manually changed coordinates as device GPS. */
export function coordinateSummary(values, source = "", language = "zh-CN") {
  const index = language === "en" ? 1 : 0;
  /* Functionality: Check a complete coordinate pair for presentation. Inputs: lat/lon field strings.
   * Outputs: Boolean. Logic: reject blanks, nonfinite and out-of-range numbers.
   * Constraints: Does not validate or submit routes; original controller retains validation. */
  const valid = (lat, lon) => String(lat).trim() !== "" && String(lon).trim() !== "" &&
    Number.isFinite(Number(lat)) && Number.isFinite(Number(lon)) && Math.abs(Number(lat)) <= 90 && Math.abs(Number(lon)) <= 180;
  return {
    origin: COPY[valid(values[0], values[1]) ? source === "设备当前位置" ? "currentOrigin" : "chosenOrigin" : "unselected"][index],
    destination: COPY[valid(values[2], values[3]) ? "chosenDestination" : "chooseDestination"][index],
  };
}

/* Functionality: Install passive product presentation around the shared page controllers.
 * Inputs: Product document/window. Outputs: Cleanup callback. Logic: Observe only original status
 * sources and language changes; reflect user-facing errors, settings support and selection state.
 * Constraints: No native speech, camera/GPS request, route request, retries or business calculation. */
export function mountProduct(doc = document, win = window) {
  /* Functionality: Resolve a fixed product binding. Inputs: ID. Outputs: DOM element.
   * Logic: Fixed template contract. Constraints: No dynamic selectors or external content. */
  const node = id => doc.getElementById(id);
  let manuallyEditedOrigin = false;
  const sources = ["maps-status", "maps-message", "tile-status", "location-source", "navigation-message", "status", "message"];
  /* Functionality: Update one text node only when changed. Inputs: ID/text. Outputs: DOM text.
   * Logic: Equality guard avoids repeated screen-reader announcements. Constraints: Text only. */
  function write(id, value) { if (node(id).textContent !== value) node(id).textContent = value; }
  /* Functionality: Reflect current source messages and capabilities. Inputs: Current DOM values.
   * Outputs: Product labels, notices and recovery visibility. Logic: No controller mutation.
   * Constraints: Unsupported vibration remains visibly unavailable; no event is reported as hardware proof. */
  function render() {
    const language = doc.documentElement.lang === "en" ? "en" : "zh-CN", index = language === "en" ? 1 : 0;
    write("product-map-state", productMessage(node("maps-status").textContent, language) || COPY.routeLoading[index]);
    write("product-route-message", productMessage(node("maps-message").textContent, language) || COPY.routeLoading[index]);
    write("product-navigation-message", productMessage(node("navigation-message").textContent, language));
    write("product-camera-state", productMessage(node("status").textContent, language) || (index ? "Off" : "未开启"));
    write("product-camera-message", productMessage(node("message").textContent, language));
    const tiles = node("tile-status").textContent;
    write("product-tile-message", tiles.includes("失败") ? productMessage(tiles, language) : "");
    const locationSource = manuallyEditedOrigin ? "" : node("location-source").textContent;
    const coordinates = coordinateSummary(["start-latitude", "start-longitude", "end-latitude", "end-longitude"].map(id => node(id).value), locationSource, language);
    write("product-origin", coordinates.origin); write("product-destination", coordinates.destination);
    write("product-vibration-note", typeof win.navigator.vibrate === "function" ? "" : COPY.vibrationUnavailable[index]);
    if (node("echo").alt !== COPY.resultAlt[index]) node("echo").alt = COPY.resultAlt[index];
    for (const id of ["assist-resume-speech", "assist-resume-vibration"]) node(id).hidden = node(id).disabled;
  }
  /* Functionality: Reveal invalid manual fields before native form validation focuses them.
   * Inputs: Captured invalid event. Outputs: Open coordinate details. Logic: Only route inputs qualify.
   * Constraints: Does not submit, fill coordinates or bypass native validation. */
  function revealInvalid(event) { if (event.target.closest(".product-coordinates")) event.target.closest("details").open = true; }
  /* Functionality: Refresh fixed copy for interface-language changes. Inputs: Current document lang.
   * Outputs: Fixed text, title and current notices. Logic: Local labels opt out of the shared dictionary.
   * Constraints: No user text or external instructions translated; no input/voice changes here. */
  function refresh() {
    const index = doc.documentElement.lang === "en" ? 1 : 0;
    doc.title = COPY.title[index];
    for (const element of doc.querySelectorAll("[data-product-copy]")) {
      const copy = COPY[element.dataset.productCopy];
      if (copy) { element.setAttribute("data-language-original", ""); if (element.textContent !== copy[index]) element.textContent = copy[index]; }
    }
    for (const element of doc.querySelectorAll("[data-product-aria]")) {
      const copy = COPY[element.dataset.productAria];
      if (copy) element.setAttribute("aria-label", copy[index]);
    }
    render();
  }
  /* Functionality: Render bounded original status mutations and track new real-origin selection.
   * Inputs: Mutation records. Outputs: Current summary. Logic: An explicit locate source clears the
   * manual-origin marker; direct edits set it before a later render. Constraints: No location inference. */
  function changed(records) {
    if (records.some(record => record.target === node("location-source"))) manuallyEditedOrigin = false;
    render();
  }
  /* Functionality: Mark manually edited origins before refreshing summaries. Inputs: Form input event.
   * Outputs: Presentation source marker. Logic: Start fields cease to claim current GPS origin.
   * Constraints: Never changes the original fields, route source or navigation state. */
  const onInput = event => { if (event.target.id.startsWith("start-")) manuallyEditedOrigin = true; render(); };
  const stateObserver = new win.MutationObserver(changed), languageObserver = new win.MutationObserver(refresh);
  for (const id of sources) stateObserver.observe(node(id), {childList: true, subtree: true, characterData: true});
  for (const id of ["assist-resume-speech", "assist-resume-vibration"]) stateObserver.observe(node(id), {attributes: true, attributeFilter: ["disabled"]});
  languageObserver.observe(doc.documentElement, {attributes: true, attributeFilter: ["lang"]});
  node("route-form").addEventListener("input", onInput);
  node("route-form").addEventListener("invalid", revealInvalid, true);
  refresh();
  /* Functionality: Release only the presentation observers and listeners. Inputs: None.
   * Outputs: No future product mirror updates. Logic: Explicit symmetric teardown.
   * Constraints: Does not stop or restart shared controllers, permissions or sessions. */
  function stop() {
    stateObserver.disconnect(); languageObserver.disconnect();
    node("route-form").removeEventListener("input", onInput);
    node("route-form").removeEventListener("invalid", revealInvalid, true);
    win.removeEventListener("pagehide", onPageHide);
  }
  /* Functionality: Keep presentation alive across back-forward cache restores. Inputs: Page event.
   * Outputs: Cleanup only on a noncached departure. Logic: Cached pages reuse their module instances.
   * Constraints: DOM observers neither retain a sensor nor restart navigation, detection or speech. */
  function onPageHide(event) { if (!event.persisted) stop(); }
  win.addEventListener("pagehide", onPageHide);
  return stop;
}

if (typeof document !== "undefined" && document.documentElement.dataset.assistView === "product") mountProduct();
