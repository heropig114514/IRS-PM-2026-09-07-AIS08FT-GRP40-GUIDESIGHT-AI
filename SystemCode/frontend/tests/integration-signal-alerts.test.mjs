/* Responsibilities: Verify exact signal wording and algorithm-field admission boundaries.
 * Implementation: Pure source fixtures, including ambiguous and unknown observations.
 * Related Modules: integration/signal-alerts.js; backend core.detector.
 * Declaration Index: light.
 * Variable Index: None. No inference, media, speech synthesis or camera substitutes. */
import test from "node:test";
import assert from "node:assert/strict";
import {selectFrontSignal, signalSpeechText, SIGNAL_TEXTS} from "../integration/signal-alerts.js";

/* Functionality: Provide one merged algorithm signal schema. Inputs: Explicit overrides.
 * Outputs: Fresh box. Logic: No risk/track fields needed. Constraints: Not a detector substitute. */
function light(overrides = {}) {
  return {label: "pedestrian light", class_id: 84, front_light: true, light_state: "green", score: 0.8,
    light_scores: {red: 0.1, green: 0.8, off: 0}, ...overrides};
}

test("approved colour speech is exact in Chinese and English", () => {
  assert.equal(signalSpeechText("red", "zh-CN"), "前方是红灯，请等待。");
  assert.equal(signalSpeechText("green", "zh-CN"), "绿灯，请通行。");
  assert.equal(signalSpeechText("red", "en"), "Red light ahead. Please wait.");
  assert.equal(signalSpeechText("green", "en-US"), "Green light. Please proceed.");
  assert.throws(() => signalSpeechText("unknown", "en"), TypeError);
  assert.equal(Object.isFrozen(SIGNAL_TEXTS), true);
});

test("only one front pedestrian signal supplies authoritative colour without TTC or a track", () => {
  const chosen = light();
  assert.equal(selectFrontSignal([chosen, light({front_light: false, light_state: "red"})]), chosen);
  assert.equal(chosen.track_id, undefined); assert.equal(chosen.ttc_sec, undefined);
  const red = light({light_state: "red", score: 0.07, light_scores: {red: 0.07, green: 0, off: 0}});
  assert.equal(selectFrontSignal([red]), red);
});

test("unknown, generic, ambiguous or malformed front observations cannot authorize crossing speech", () => {
  for (const fields of [
    {light_state: "unknown"}, {light_state: "amber"}, {front_light: false}, {front_light: "true"},
    {label: "traffic light", class_id: 9}, {label: "ped_green", class_id: 85}, {class_id: 85},
    {score: NaN}, {score: 0}, {score: 1.1}, {light_scores: null}, {light_scores: []},
    {light_scores: {red: 0, green: 0}}, {light_scores: {red: 0, green: NaN, off: 0}},
    {light_scores: {red: 0, green: 1.1, off: 0}}, {light_scores: {red: 0, green: 0, off: 1}},
  ]) assert.equal(selectFrontSignal([light(fields)]), null);
  assert.equal(selectFrontSignal([light(), light({light_state: "red"})]), null);
  assert.equal(selectFrontSignal([light(), light({label: "traffic light", class_id: 9})]), null);
  assert.equal(selectFrontSignal(null), null); assert.equal(selectFrontSignal([null]), null);
});
