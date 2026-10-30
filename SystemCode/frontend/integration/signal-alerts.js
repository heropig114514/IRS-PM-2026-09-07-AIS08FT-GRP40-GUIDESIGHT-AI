/* Responsibilities: Select only algorithm-confirmed front pedestrian signals and fixed speech.
 * Implementation: Validate the existing signal fields without reclassifying colours or risk.
 * Related Modules: assist-guidance.js; signal-audio-resources.js; backend core.detector.
 * Declaration Index: selectFrontSignal; signalSpeechText.
 * Variable Index: SIGNAL_TEXTS contains the approved bilingual red/green utterances. */
import {languageOf} from "./catalog.js";

export const SIGNAL_TEXTS = Object.freeze({
  "zh-CN": Object.freeze({red: "前方是红灯，请等待。", green: "绿灯，请通行。"}),
  "en-US": Object.freeze({red: "Red light ahead. Please wait.", green: "Green light. Please proceed."}),
});

/* Functionality: Select one unambiguous algorithm-confirmed pedestrian signal ahead.
 * Inputs: Validated frame boxes. Outputs: The original red/green signal box or null.
 * Logic: Require the backend's sole front marker, merged walk-model identity and bounded scores.
 * Constraints: Generic vehicle lights, unknown colours and malformed/contradictory markers never
 * authorize a signal utterance; no copied confidence threshold, tracking ID or distance is required. */
export function selectFrontSignal(boxes) {
  if (!Array.isArray(boxes)) return null;
  const fronts = boxes.filter(box => box?.front_light === true &&
    ["pedestrian light", "traffic light"].includes(box.label));
  if (fronts.length !== 1) return null;
  const box = fronts[0], scores = box.light_scores;
  if (box.label !== "pedestrian light" || box.class_id !== 84 ||
      !["red", "green"].includes(box.light_state) ||
      !Number.isFinite(box.score) || box.score <= 0 || box.score > 1 ||
      !scores || typeof scores !== "object" || Array.isArray(scores) ||
      Object.keys(scores).sort().join() !== "green,off,red" ||
      !Object.values(scores).every(value => Number.isFinite(value) && value >= 0 && value <= 1) ||
      scores[box.light_state] <= 0) return null;
  return box;
}

/* Functionality: Return the exact pre-generated sentence for an existing signal colour.
 * Inputs: Red/green state and explicit interface language. Outputs: Fixed bilingual text.
 * Logic: Reuse the page's existing language resolver. Constraints: Unknown state has no wording. */
export function signalSpeechText(state, language) {
  if (!["red", "green"].includes(state)) throw new TypeError("A confirmed signal colour is required");
  return SIGNAL_TEXTS[languageOf(language)][state];
}
