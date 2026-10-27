/** Responsibilities: Attach opt-in speech to the existing maps navigation singleton.
 * Implementation: Passive state bridge, explicit local voice settings and shared manual output UI.
 * Related Modules: ../maps.html, ../navigation-ui.js, ../speech-output.js, maps-navigation-bridge.js;
 * navigation-speech.js, maps-navigation-speech.css. Served under the canonical assets/integration/ directory.
 * Declaration Index: node; renderSpeech; applyVoice; readVoices; initialize;
 * navigationSpeech; navigationBridge; mapsNavigationSpeechReady.
 * Variable Index: doc owns only added controls; navigation/speechOutput are the original page singletons.
 * No new GPS watch, navigation controller, route request, video dependency or output enablement is created.
 */
import {navigation} from "../navigation-ui.js";
import {speechOutput} from "../speech-output.js";
import {NavigationSpeechAdapter} from "./navigation-speech.js";
import {MapsNavigationSpeechBridge} from "./maps-navigation-bridge.js";
export {navigation} from "../navigation-ui.js";

const doc = document;
export const navigationSpeech = new NavigationSpeechAdapter({speech: speechOutput});
export const navigationBridge = new MapsNavigationSpeechBridge({navigation, adapter: navigationSpeech});

/** Functionality: Resolve an added speech control. Inputs: Fixed DOM ID. Outputs: Element.
 * Logic: Page-owned IDs; Constraints: never reads navigation text as a substitute for its state contract. */
function node(id) { return doc.getElementById(id); }
/** Functionality: Show body-free speech status and explicit cleanup controls.
 * Inputs: Optional adapter lifecycle event. Outputs: Status and disabled controls.
 * Logic: Preserve a stop action whenever owned output remains, including arrival or failed cancellation.
 * Constraints: Does not alter original navigation buttons, pause GPS or imply actual audible output. */
function renderSpeech(event) {
  const state = navigationSpeech.describe();
  node("navigation-speech-resume").disabled = !state.paused;
  node("navigation-speech-stop").disabled = state.run_id === null && state.active_request_id === null;
  if (event) node("navigation-speech-status").textContent = `${event.phase}${event.reason ? ` · ${event.reason}` : ""}${event.request_id ? ` · #${event.request_id}` : ""}`;
}
/** Functionality: Apply explicit speech choices to the adapter and manual control selectors.
 * Inputs: Navigation language/voice controls. Outputs: Stored settings.
 * Logic: Native voice IDs are kept exact; Constraints: does not enable a channel, start navigation or resume failure. */
function applyVoice() {
  const voiceId = node("navigation-speech-voice").value, language = node("navigation-speech-language").value;
  navigationSpeech.configure({voiceId, language});
  if (voiceId) { speechOutput.voice.value = voiceId; speechOutput.language.value = language; }
}
/** Functionality: Read matching local voices while preserving an unavailable explicit selection.
 * Inputs: Original shared driver's inventory and selected language. Outputs: Bounded options.
 * Logic: Filter by native local_service and language; Constraints: no default, remote or replacement voice is selected. */
function readVoices() {
  const select = node("navigation-speech-voice"), selected = select.value, language = node("navigation-speech-language").value;
  select.replaceChildren();
  const empty = doc.createElement("option"); empty.value = ""; empty.textContent = "请选择本地声音"; select.append(empty);
  for (const voice of speechOutput.voiceList().voices.filter(voice => voice.local_service === true &&
      voice.language.split("-")[0].toLowerCase() === language.split("-")[0].toLowerCase())) {
    const option = doc.createElement("option"); option.value = voice.id;
    option.textContent = `${voice.name} (${voice.language}) [local]`; option.dataset.languageOriginal = ""; select.append(option);
  }
  if (selected && ![...select.options].some(option => option.value === selected)) {
    const missing = doc.createElement("option"); missing.value = selected; missing.disabled = true;
    missing.textContent = `${selected} [unavailable]`; missing.dataset.languageOriginal = ""; select.append(missing);
  }
  select.value = selected; applyVoice();
}
/** Functionality: Bind added controls to the passive bridge and shared speech driver.
 * Inputs: Existing maps page singletons. Outputs: Event listeners and initial disabled output state.
 * Logic: Capture the original stop action before its synchronous navigation notification.
 * Constraints: Existing UI/MCP owns start, stop, route replacement and GPS; no method patch, polling or autoplay. */
function initialize() {
  if (!navigation || !speechOutput) throw new Error("Maps navigation and shared speech controls are required.");
  navigationSpeech.subscribe(renderSpeech);
  node("navigation-speech-enabled").addEventListener("change", () => {
    navigationSpeech.setEnabled(node("navigation-speech-enabled").checked); renderSpeech();
  });
  node("navigation-speech-language").addEventListener("change", readVoices);
  node("navigation-speech-voice").addEventListener("change", applyVoice);
  node("navigation-voices-refresh").addEventListener("click", readVoices);
  node("navigation-speech-resume").addEventListener("click", () => { navigationSpeech.resume(); renderSpeech(); });
  node("navigation-speech-stop").addEventListener("click", () => { navigationBridge.stop("speech_stop"); renderSpeech(); });
  node("navigation-stop").addEventListener("click", () => navigationBridge.stop("navigation_stop"), true);
  readVoices(); renderSpeech();
}

export const mapsNavigationSpeechReady = Promise.resolve().then(initialize);
