/* Responsibilities: Adapt existing map/camera controllers to conversational tools.
 * Implementation: Conditional imports and bounded read-only observation waits.
 * Related Modules: ai-control.js, camera.js, maps-page.js, navigation-ui.js.
 * Declaration Index: cameraState; observe; performDevice.
 * Variable Index: None; controller/status/deadline are call-local state. */

/* Functionality: Describe camera resources. Inputs: CameraApp; Outputs: JSON state.
 * Logic: Separate live track, pending startup and decoded frames.
 * Constraints: No device request; historical counters do not imply an active camera. */
export function cameraState(app) {
  const active = Boolean(app.stream?.getVideoTracks().some(track => track.readyState === "live"));
  const frames = app.frameTimings ? app.frameTimings.steadyFrames + 1 : 0;
  return {available: true, active, processing: app.processing.value, phase: app.cameraPhase,
    state: app.message.dataset.kind === "error" ? "error" : app.mode === "camera" ?
      (frames > 0 && active ? "streaming" : "starting") : "stopped",
    frames_received: frames, detail: app.message.textContent};
}

/* Functionality: Await startup observations. Inputs: read/pending functions and seconds.
 * Outputs: latest state; Logic: read-only local polling every 100ms.
 * Constraints: 0..12s, never repeats a device mutation. */
export async function observe(read, pending, seconds = 0) {
  if (!Number.isFinite(seconds) || seconds < 0 || seconds > 12) throw new Error("Invalid observation timeout");
  const deadline = performance.now() + seconds * 1000;
  let state = read();
  while (pending(state) && performance.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 100));
    state = read();
  }
  return state;
}

/* Functionality: Execute one semantic command via shared controllers.
 * Inputs: action/args/current document; Outputs: state, acknowledgement or error.
 * Logic: Page checks precede imports; start returns promptly, status observes startup and
 * the existing optional navigation speech adapter without creating sensors or enabling output.
 * Constraints: No provider fallback, permission bypass or processing parameter changes. */
export async function performDevice(action, args = {}, doc = document) {
  if (action.startsWith("maps_") || action === "agent_navigation_status") {
    if (!doc.querySelector("#navigation-panel")) return {available: false, accepted: false, reason: "page_unavailable"};
    if (action === "agent_navigation_status") {
      const {navigation} = await import("./navigation-ui.js");
      let speech = null;
      if (doc.querySelector("#maps-navigation-speech-panel")) {
        const {navigationSpeech, mapsNavigationSpeechReady} = await import("./integration/maps-navigation-speech.js");
        await mapsNavigationSpeechReady;
        speech = navigationSpeech;
      }
      return observe(() => ({...navigation.describe(),
        navigation_speech: speech ? {available: true, ...speech.describe()} : {available: false}}),
      state => state.state === "locating", args.wait_seconds ?? 0);
    }
    const {mapsControl} = await import("./maps-page.js");
    if (action === "maps_status") return mapsControl.describe();
    if (action === "maps_locate") return mapsControl.locate();
    if (action === "maps_plan") return mapsControl.plan(args);
  } else if (action.startsWith("camera_")) {
    if (!doc.querySelector("#preview")) return {available: false, accepted: false, reason: "page_unavailable"};
    const {cameraApp: app} = await import("./camera.js");
    if (action === "camera_status") return observe(() => cameraState(app), state => state.state === "starting", args.wait_seconds ?? 0);
    if (action === "camera_stop") { app.stop(); return {accepted: true, ...cameraState(app)}; }
    if (action === "camera_start") {
      if (doc.visibilityState === "hidden") return {accepted: false, reason: "page_hidden", ...cameraState(app)};
      if (app.mode !== "idle") return {accepted: false, reason: "already_active", ...cameraState(app)};
      if (args.processing != null) {
        if (!["echo", "hf"].includes(args.processing)) throw new Error("Invalid processing mode");
        app.processing.value = args.processing;
        app.handleProcessingChange();
      }
      // Functionality: Start the existing long-lived loop; Logic: CameraApp owns async
      // failures/cleanup; Constraints: do not await the entire video session or restart it.
      void app.start();
      return {accepted: true, ...cameraState(app)};
    }
  }
  throw new Error("Unknown semantic device action");
}
