/* Responsibilities: Verify product voice selection without changing shared-driver defaults.
 * Implementation: Pure inventory fixtures exercise language preference, late voices and removals.
 * Related Modules: ../integration/voice-preferences.js; original SpeechOutput inventory contract.
 * Declaration Index: voice.
 * Variable Index: None.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {interfaceSpeechLanguage, selectLocalVoice} from "../integration/voice-preferences.js";

/* Functionality: Build a native-inventory-shaped test record.
 * Inputs: Unique ID, language and optional local/default overrides. Outputs: Detached fixture.
 * Logic: Local Chinese is the ordinary case. Constraints: No engine or permission fixture. */
function voice(id, language = "zh-CN", options = {}) {
  return {id, language, local_service: true, default: false, ...options};
}

/* Functionality: Resolve the existing UI's language values.
 * Inputs: Supported/unsupported tags. Outputs: Canonical tags or null.
 * Logic: The UI's English tag is en. Constraints: Unknown input does not select English implicitly. */
test("interface language maps Chinese and English without cross-language substitution", () => {
  assert.equal(interfaceSpeechLanguage("zh-CN"), "zh-CN");
  assert.equal(interfaceSpeechLanguage("ZH-hant"), "zh-CN");
  assert.equal(interfaceSpeechLanguage("en"), "en-US");
  assert.equal(interfaceSpeechLanguage("en-GB"), "en-US");
  for (const value of ["fr", "", null, {}, 1]) assert.equal(interfaceSpeechLanguage(value), null);
});

/* Functionality: Protect exact-locale precedence over a different regional system default.
 * Inputs: Chinese and English regional voices. Outputs: Exact locale IDs.
 * Logic: Locale rank precedes the default flag. Constraints: Does not mutate source ordering. */
test("exact locale wins before regional default and inventory remains unchanged", () => {
  const voices = [voice("tw", "zh-TW", {default: true}), voice("cn"), voice("uk", "en-GB", {default: true}), voice("us", "en-US")];
  const before = structuredClone(voices);
  assert.equal(selectLocalVoice(voices, "zh-CN"), "cn");
  assert.equal(selectLocalVoice(voices, "en-US"), "us");
  assert.deepEqual(voices, before);
});

/* Functionality: Follow the device's preferred local voice within the matching group.
 * Inputs: Two exact-locale voices. Outputs: Default ID.
 * Logic: System preference precedes the previous choice. Constraints: Still rejects remote default. */
test("system default wins within local matching voices", () => {
  assert.equal(selectLocalVoice([voice("current"), voice("preferred", "zh-CN", {default: true})], "zh-CN", "current"), "preferred");
  assert.equal(selectLocalVoice([voice("remote", "zh-CN", {local_service: false, default: true}), voice("local")], "zh-CN"), "local");
});

/* Functionality: Keep an equally preferred selected voice stable during inventory reordering.
 * Inputs: Current ID and reordered equivalent voices. Outputs: Same ID.
 * Logic: Current selection breaks only equal locale/default ranks. Constraints: No readiness reset. */
test("equally ranked inventory updates preserve the current voice", () => {
  assert.equal(selectLocalVoice([voice("new"), voice("current")], "zh-CN", "current"), "current");
  assert.equal(selectLocalVoice([voice("current"), voice("new")], "zh-CN", "current"), "current");
});

/* Functionality: Permit a same-primary local regional voice when the exact locale is absent.
 * Inputs: Regional native inventory. Outputs: Matching regional ID.
 * Logic: Primary-language rank is the user-authorized second choice. Constraints: No other language. */
test("regional matches stay inside the requested primary language", () => {
  assert.equal(selectLocalVoice([voice("uk", "en-GB"), voice("cn")], "en-US"), "uk");
  assert.equal(selectLocalVoice([voice("tw", "zh-TW")], "zh-CN"), "tw");
  assert.equal(selectLocalVoice([voice("uk", "en-GB")], "zh-CN"), "");
});

/* Functionality: Recover configuration from a delayed inventory and clear a disappeared match.
 * Inputs: Empty, populated and subsequently removed inventory snapshots. Outputs: Honest IDs.
 * Logic: Selection has no retained stale inventory. Constraints: No automatic playback or retry. */
test("late native voices can become configured and removed voices become unavailable", () => {
  assert.equal(selectLocalVoice([], "zh-CN"), "");
  assert.equal(selectLocalVoice([voice("late")], "zh-CN"), "late");
  assert.equal(selectLocalVoice([], "zh-CN", "late"), "");
});

/* Functionality: Reject ambiguous or unusable identities before they reach shared speak validation.
 * Inputs: Duplicate, missing, remote or invalid records. Outputs: Empty selection.
 * Logic: Exact IDs must be unique across the original native inventory. Constraints: No coercion. */
test("invalid and duplicate voice IDs never become an implicit browser default", () => {
  const voices = [voice("duplicate"), voice("duplicate", "en-US"), voice(""), voice("remote", "zh-CN", {local_service: false}), null];
  assert.equal(selectLocalVoice(voices, "zh-CN"), "");
  assert.equal(selectLocalVoice([voice("x".repeat(1025))], "zh-CN"), "");
  assert.equal(selectLocalVoice([voice("cn")], "fr-FR"), "");
  assert.equal(selectLocalVoice(null, "zh-CN"), "");
});
