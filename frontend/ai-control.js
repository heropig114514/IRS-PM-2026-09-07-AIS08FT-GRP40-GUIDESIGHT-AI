/* Responsibilities: Expose live PRS DOM state, actions, device output and walking navigation to MCP.
 * Implementation: One WebSocket per page; sessionStorage identity survives explicit navigation;
 * correlated commands execute once and return snapshots/device acknowledgements/errors;
 * no automatic reconnect.
 * Related Modules: core.ai_bridge/ai_mcp, index.html, maps.html, camera/maps controllers,
 * vibration.js, speech-output.js, navigation-ui.js, agent-device.js, agent-client.js.
 * Declaration Index: selectorFor — exact CSS paths; visible — rendered state;
 * uniqueElement — unambiguous selection; snapshot — live DOM description;
 * navigatePage — route or embedded-panel navigation;
 * waitForState — bounded observation; performVibration — shared structured vibration calls;
 * performSpeech — shared speech state/voices/speak/stop interface;
 * performNavigation — shared walking navigation start/stop/status;
 * performAction — native control/event dispatch;
 * connectControl — socket registration; connectControl.send — JSON writer;
 * connectControl.onMessage — correlated command handler;
 * connectControl.startHeartbeat — idle keepalive; connectControl.stopHeartbeat — timer cleanup.
 * Variable Index: SESSION_KEY — tab identity storage key; CONTROL_SELECTOR — discoverable controls;
 * connectControl sessionId/socket/heartbeat — tab transport state; base — application mount URL;
 * AGENT_TOKEN_KEY — tab-local ownership secret, never part of a snapshot.
 */
import {tester} from "./vibration.js";
import {speechOutput} from "./speech-output.js";
import {navigation} from "./navigation-ui.js";
import {performDevice} from "./agent-device.js";

const SESSION_KEY = "prs.ai.session";
const AGENT_TOKEN_KEY = "prs.agent.token";
const CONTROL_SELECTOR = "button,a[href],input,select,textarea,[role=button],[contenteditable=true]";

/* Functionality: Generate an exact CSS selector for a DOM element.
 * Inputs: element. Outputs: Escaped ID or ancestor/nth-of-type selector.
 * Logic: Prefer unique IDs; otherwise derive a positional path without changing the DOM.
 * Constraints: A selector may become stale when the application replaces its elements. */
export function selectorFor(element) {
  if (element.id && element.ownerDocument.querySelectorAll(`#${CSS.escape(element.id)}`).length === 1) {
    return `#${CSS.escape(element.id)}`;
  }
  const parts = [];
  for (let node = element; node?.nodeType === 1; node = node.parentElement) {
    const peers = node.parentElement ? [...node.parentElement.children]
      .filter(peer => peer.tagName === node.tagName) : [node];
    parts.unshift(`${node.tagName.toLowerCase()}:nth-of-type(${peers.indexOf(node) + 1})`);
  }
  return parts.join(" > ");
}

/* Functionality: Determine whether a control has visible layout.
 * Inputs: element or null. Outputs: Boolean. Logic: Check layout and computed visibility.
 * Constraints: Off-screen elements still count as visible and can be scrolled into view. */
export function visible(element) {
  return Boolean(element && element.getClientRects().length &&
    getComputedStyle(element).visibility !== "hidden");
}

/* Functionality: Resolve exactly one control to avoid unintended ambiguous operations.
 * Inputs: document and selector. Outputs: Element or Error.
 * Logic: CSS query followed by cardinality check; malformed selectors propagate their error.
 * Constraints: No implicit choice of the first matching control. */
export function uniqueElement(doc, selector) {
  const elements = doc.querySelectorAll(selector);
  if (elements.length !== 1) throw new Error(`Selector must match exactly one element; matched ${elements.length}`);
  return elements[0];
}

/* Functionality: Describe current UI controls, values, status and visible page text.
 * Inputs: Browser document. Outputs: JSON-compatible snapshot with explicit truncation flags.
 * Logic: Read live values/options/bounds and page surface capabilities; exclude assistant
 * dialogue/controls/status to avoid recursively feeding its own conversation back as evidence.
 * Constraints: Limit controls to 500 and text to 30000 characters; does not capture media pixels. */
export function snapshot(doc = document) {
  const embeddedCameraPanel = doc.querySelector("#assist-camera-panel");
  const cameraVideo = doc.querySelector("#preview");
  const panelText = doc.querySelector("#agent-panel")?.innerText;
  const text = panelText ? doc.body.innerText.replace(panelText, "") : doc.body.innerText;
  const controls = [...doc.querySelectorAll(CONTROL_SELECTOR)].filter(element => !element.closest("#agent-panel"));
  return {
    url: doc.location.href, title: doc.title, language: doc.documentElement.lang,
    surface: doc.documentElement.dataset.assist === "true" ?
      `assist-${doc.documentElement.dataset.assistView || "unknown"}` : "standard",
    capabilities: {
      camera_panel: Boolean(doc.querySelector("#assist-camera-panel, #preview")),
      camera_panel_visible: embeddedCameraPanel ? !embeddedCameraPanel.hidden :
        Boolean(cameraVideo && !cameraVideo.hidden),
      maps_panel: Boolean(doc.querySelector("#navigation-panel, #maps-form")),
    },
    text: text.slice(0, 30000), text_truncated: text.length > 30000,
    controls_truncated: controls.length > 500,
    controls: controls.slice(0, 500).map(element => {
      const bounds = element.getBoundingClientRect();
      return {
        selector: selectorFor(element), tag: element.tagName.toLowerCase(),
        type: element.type || null, text: element.innerText?.trim() || "",
        label: element.getAttribute("aria-label") ||
          [...(element.labels || [])].map(label => label.innerText.trim()).join(" "),
        value: "value" in element ? element.value : null,
        checked: "checked" in element ? element.checked : null,
        disabled: element.matches(":disabled"), readonly: Boolean(element.readOnly),
        visible: visible(element), href: element.getAttribute("href"),
        options: element.tagName === "SELECT" ? [...element.options].map(option => ({
          value: option.value, text: option.text, disabled: option.disabled,
        })) : undefined,
        bounds: {x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height},
      };
    }),
    status: [...doc.querySelectorAll("[role=status],[aria-live],.status")].filter(element => !element.closest("#agent-panel")).map(element => ({
      selector: selectorFor(element), text: element.innerText, visible: visible(element),
    })),
  };
}

/* Functionality: Resolve one semantic page request against the current application surface.
 * Inputs: page alias, browser document and configured application base URL.
 * Outputs: Same-page focus result or an explicit destination URL.
 * Logic: The unified assist surface focuses its embedded camera panel; standard PRS pages keep
 * their established camera/maps routes. A hidden or missing embedded panel is reported directly.
 * Constraints: Does not start camera/GPS work, reveal hidden panels, or change route allowlists. */
export function navigatePage(page, doc, base) {
  if (!["camera", "maps"].includes(page)) throw new Error("Unknown page destination");
  if (page === "camera" && doc.documentElement.dataset.assist === "true") {
    const panel = doc.querySelector("#assist-camera-panel");
    if (!panel) return {same_page: true, available: false, reason: "page_unavailable"};
    if (panel.hidden) return {same_page: true, available: false, reason: "camera_panel_hidden"};
    panel.scrollIntoView({block: "start"});
    return {same_page: true, available: true, page: "camera", section: "#assist-camera-panel"};
  }
  const destination = {camera: "live/", maps: "maps/"}[page];
  return {same_page: false, available: true, navigating_to: new URL(destination, base).href};
}

/* Functionality: Observe an asynchronous application's DOM completion without reissuing work.
 * Inputs: document, selector, text/state and timeout_ms. Outputs: Completion or timeout error.
 * Logic: Poll state every 50 ms; optional text must be contained in rendered text or input value.
 * Constraints: Hidden may match an absent element; other states require one unambiguous element. */
export async function waitForState(doc, args) {
  const deadline = performance.now() + args.timeout_ms;
  while (true) {
    const matches = doc.querySelectorAll(args.selector);
    if (matches.length > 1) throw new Error("Wait selector is ambiguous");
    const element = matches[0];
    const ready = args.state === "hidden" ? !visible(element) :
      visible(element) && (args.state !== "enabled" || !element.matches(":disabled"));
    const textMatches = args.text == null || (element &&
      String(element.innerText || element.value || "").includes(args.text));
    if (ready && textMatches) return;
    if (performance.now() >= deadline) throw new Error("DOM condition timed out; inspect current state");
    await new Promise(resolve => setTimeout(resolve, 50));
  }
}

/* Functionality: Perform one DOM operation using the same controls as a human user.
 * Inputs: action name, args DOM/device/navigation parameters, browser document.
 * Outputs: DOM snapshot, structured device result or explicit error.
 * Logic: Vibration/speech commands use the page's shared controllers; DOM commands use native value
 * setters plus input/change events, and clicks/forms call native DOM methods.
 * Constraints: Async work may still be running; synthetic keyboard events do not type characters
 * or grant browser permissions. Disabled/read-only/hidden controls fail instead of being bypassed. */
export async function performAction(action, args, doc = document) {
  if (["maps_status", "maps_locate", "maps_plan", "agent_navigation_status",
    "camera_status", "camera_start", "camera_stop"].includes(action)) return performDevice(action, args, doc);
  if (action === "agent_ui_action") {
    const element = uniqueElement(doc, args.selector);
    if (element.closest("#agent-panel") ||
        ["speech-enabled", "vibration-enabled", "navigation-speech-enabled"].includes(element.id))
      throw new Error("This control requires direct user interaction");
    return performAction(args.action, args, doc);
  }
  if (["navigation_status", "navigation_start", "navigation_stop"].includes(action))
    return performNavigation(action, doc === globalThis.document ? navigation : null);
  if (["speech_status", "speech_voices", "speech_speak", "speech_stop"].includes(action))
    return performSpeech(action, args, speechOutput?.doc === doc ? speechOutput : null);
  if (["vibration_status", "vibration_test", "vibration_stop"].includes(action))
    return performVibration(action, args, tester?.doc === doc ? tester : null);
  if (action === "snapshot") return snapshot(doc);
  if (action === "wait") {
    await waitForState(doc, args);
    return snapshot(doc);
  }
  const element = uniqueElement(doc, args.selector);
  if (!visible(element) || element.matches(":disabled")) throw new Error("Control is hidden or disabled");
  if (["fill", "select", "check"].includes(action) && element.readOnly) throw new Error("Control is read-only");
  element.scrollIntoView({block: "center", inline: "nearest"});
  if (action === "click") {
    if (args.x == null && args.y == null) element.click();
    else {
      if (!Number.isFinite(args.x) || !Number.isFinite(args.y)) throw new Error("Both x and y offsets are required");
      const rect = element.getBoundingClientRect();
      if (args.x < 0 || args.x > rect.width || args.y < 0 || args.y > rect.height) throw new Error("Click offsets are outside the control");
      element.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true,
        clientX: rect.left + args.x, clientY: rect.top + args.y}));
    }
  } else if (action === "fill" || action === "select") {
    if (typeof args.value !== "string") throw new Error("fill/select require a string value");
    if (action === "select" && element.tagName !== "SELECT") throw new Error("select requires a select element");
    if (action === "fill" && !["INPUT", "TEXTAREA"].includes(element.tagName) && !element.isContentEditable) throw new Error("fill requires a text control");
    if (element.type === "file") throw new Error("File selection requires browser-native automation");
    if (action === "select" && ![...element.options].some(option =>
      option.value === args.value && !option.disabled)) throw new Error("Option is absent or disabled");
    if (element.isContentEditable) element.textContent = args.value;
    else {
      const prototype = element.tagName === "SELECT" ? HTMLSelectElement.prototype :
        element.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, args.value);
      if (element.value !== args.value) throw new Error("Browser rejected the supplied control value");
    }
    element.dispatchEvent(new Event("input", {bubbles: true}));
    element.dispatchEvent(new Event("change", {bubbles: true}));
  } else if (action === "check") {
    if (!["checkbox", "radio"].includes(element.type) || typeof args.value !== "boolean") throw new Error("check requires checkbox/radio and boolean value");
    element.checked = args.value;
    element.dispatchEvent(new Event("input", {bubbles: true}));
    element.dispatchEvent(new Event("change", {bubbles: true}));
  } else if (action === "key") {
    if (typeof args.value !== "string" || !args.value) throw new Error("key requires a key name");
    element.focus();
    for (const type of ["keydown", "keyup"]) element.dispatchEvent(new KeyboardEvent(type,
      {key: args.value, bubbles: true, cancelable: true}));
  } else if (action === "submit") {
    const form = element.tagName === "FORM" ? element : element.form;
    if (!form) throw new Error("submit requires a form or its associated control");
    form.requestSubmit(element.type === "submit" ? element : undefined);
  } else if (action !== "scroll") throw new Error(`Unknown action: ${action}`);
  return snapshot(doc);
}

/* Functionality: Read/start/stop the same walking controller as the maps panel.
 * Inputs: Fixed action name and optional maps controller; null on other pages.
 * Outputs: Structured state, operation and mutation accepted/reason acknowledgement.
 * Logic: Status is read-only; start requests one watch; stop releases it once.
 * Constraints: Requires a planned route and browser location permission; no navigation to a
 * different page, permission bypass, route request, retry or synthetic location. */
export function performNavigation(action, controller = navigation) {
  if (!["navigation_status", "navigation_start", "navigation_stop"].includes(action))
    throw new Error("Unknown navigation command");
  const operation = action.slice("navigation_".length);
  if (!controller) return {available: false, active: false, operation,
    accepted: false, reason: "page_unavailable"};
  if (operation === "status") return {...controller.describe(), operation};
  return operation === "start" ? controller.start() : controller.stop("mcp");
}

/* Functionality: Call the page's existing vibration controller with structured acknowledgement.
 * Inputs: action(status/test/stop protocol names), args.pattern for test, optional controller;
 * default controller is the camera page's exported tester, null on pages without vibration UI.
 * Outputs: Machine-readable state and accepted/reason for mutations; invalid actions throw.
 * Logic: Test calls testPattern once; stop calls stop once; status performs no native invocation.
 * Constraints: Never enable the switch, synthesize trusted activation, navigate, retry or infer
 * physical hardware success. A missing controller returns page_unavailable without side effects. */
export function performVibration(action, args = {}, controller = tester) {
  if (!["vibration_status", "vibration_test", "vibration_stop"].includes(action))
    throw new Error("Unknown vibration command");
  if (!controller) return {available: false, supported: false, enabled: false, visible: false,
    user_activated: null, request_pending: false, hardware_verified: false,
    operation: action.slice("vibration_".length), accepted: false, reason: "page_unavailable"};
  if (action === "vibration_status") return {...controller.describe(), operation: "status"};
  if (action === "vibration_test") return controller.testPattern(args.pattern);
  const accepted = controller.stop("mcp");
  return {...controller.describe(), operation: "stop", accepted,
    reason: accepted ? null : controller.nativeOutcome};
}

/* Functionality: Expose the page's speech output controller without duplicating playback logic.
 * Inputs: fixed speech action, args.text/language/voice_id/rate/volume, optional controller.
 * Outputs: State, voice inventory or one submission/cancellation acknowledgement.
 * Logic: Queries do not speak; mutating actions delegate once to the same UI interface.
 * Constraints: No automatic enabling, trusted activation, queue, voice substitution, sensor
 * authorization or retry. Missing speech UI returns page_unavailable without side effects. */
export function performSpeech(action, args = {}, controller = speechOutput) {
  if (!["speech_status", "speech_voices", "speech_speak", "speech_stop"].includes(action))
    throw new Error("Unknown speech command");
  if (!controller) return {available: false, supported: false, enabled: false,
    operation: action.slice("speech_".length), accepted: false, reason: "page_unavailable",
    audio_output_verified: false};
  if (action === "speech_status") return {...controller.describe(), operation: "status"};
  if (action === "speech_voices") return controller.voiceList();
  if (action === "speech_stop") return controller.stop();
  const {language = "zh-CN", voice_id = null, rate = 1, volume = 1} = args;
  return controller.speak(args.text, {language, voice_id, rate, volume});
}

/* Functionality: Register this open page with the application's MCP control service.
 * Inputs: window, document, sessionStorage and optional data-api-base attribute (default ../).
 * Outputs: Live WebSocket; logs transport/command errors without dumping input values.
 * Logic: Retain tab ID across navigation; send heartbeats; reply once to each correlated command.
 * Constraints: No automatic reconnect; refresh after loss. Camera/GPS access remains browser-owned.
 * Navigation acknowledgement precedes unload; backend subsequently verifies a new registration. */
export function connectControl(win = window, doc = document) {
  let sessionId = win.sessionStorage.getItem(SESSION_KEY);
  if (!sessionId) {
    sessionId = win.crypto.randomUUID();
    win.sessionStorage.setItem(SESSION_KEY, sessionId);
  }
  const base = new URL(doc.documentElement.dataset.apiBase || "../", win.location.href);
  if (!win.sessionStorage.getItem(AGENT_TOKEN_KEY))
    win.sessionStorage.setItem(AGENT_TOKEN_KEY, win.crypto.randomUUID());
  const address = new URL("ws/control/", base);
  address.protocol = address.protocol === "https:" ? "wss:" : "ws:";
  const socket = new win.WebSocket(address);
  let heartbeat;

  /* Functionality: Send a JSON message once. Inputs: message. Outputs: WS send or exception.
   * Logic: Native socket send; Constraints: No queue/retry after a closed connection. */
  function send(message) { socket.send(JSON.stringify(message)); }

  /* Functionality: Dispatch correlated commands and acknowledge their exact result.
   * Inputs: WebSocket event. Outputs: Result message or diagnostic browser event.
   * Logic: Navigation either focuses an embedded panel or acknowledges and schedules page
   * replacement; other actions run once.
   * Constraints: Errors preserve id; no command contents or entered values are logged. */
  async function onMessage(event) {
    let command;
    try {
      command = JSON.parse(event.data);
      if (command.type === "welcome") {
        doc.documentElement.dataset.aiConnected = "true";
        win.dispatchEvent(new CustomEvent("prs-ai-ready"));
        return;
      }
      if (command.type !== "command") return;
      if (command.action === "agent_identity") {
        send({type: "result", id: command.id, ok: true,
          result: {token: win.sessionStorage.getItem(AGENT_TOKEN_KEY)}});
        return;
      }
      if (command.action === "navigate") {
        const navigation = navigatePage(command.arguments.page, doc, base);
        send({type: "result", id: command.id, ok: true, result: navigation});
        if (!navigation.same_page && navigation.available)
          setTimeout(() => win.location.assign(navigation.navigating_to), 0);
      } else {
        const result = await performAction(command.action, command.arguments, doc);
        send({type: "result", id: command.id, ok: true, result});
      }
    } catch (error) {
      console.error("PRS AI action failed", {id: command?.id, action: command?.action,
        error_type: error.name});
      if (command?.id && socket.readyState === win.WebSocket.OPEN) send({
        type: "result", id: command.id, ok: false, error: error.message,
      });
    }
  }

  /* Functionality: Keep idle proxy connections alive. Inputs: None. Outputs: Interval handle.
   * Logic: Ping every 15s only while open; Constraints: Does not reconnect or repeat commands. */
  function startHeartbeat() { heartbeat = setInterval(() => {
    if (socket.readyState === win.WebSocket.OPEN) send({type: "ping"});
  }, 15000); }

  /* Functionality: Release transport timer on disconnect/unload. Inputs: None. Outputs: None.
   * Logic: Clear the interval; Constraints: Outstanding server commands report disconnection. */
  function stopHeartbeat() { clearInterval(heartbeat); }

  socket.addEventListener("open", () => {
    send({type: "hello", session_id: sessionId,
      page: {url: win.location.href, title: doc.title, language: doc.documentElement.lang}});
    startHeartbeat();
  });
  socket.addEventListener("message", onMessage);
  socket.addEventListener("error", () => console.error("PRS AI control transport failed; refresh to reconnect"));
  socket.addEventListener("close", () => {
    doc.documentElement.dataset.aiConnected = "false";
    stopHeartbeat();
    win.dispatchEvent(new CustomEvent("prs-ai-disconnected"));
  });
  win.addEventListener("pagehide", () => { stopHeartbeat(); socket.close(); }, {once: true});
  return socket;
}

if (typeof window !== "undefined" && typeof document !== "undefined") connectControl();
