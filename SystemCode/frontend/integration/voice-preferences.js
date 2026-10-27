/* Responsibilities: Select a local speech voice for the unified product's interface language.
 * Implementation: Exact language before primary-language matches, system default within each
 * group, and stable current selection among equally preferred candidates; no output side effects.
 * Related Modules: assist-mvp.js; ../speech-output.js inventory contract; voice-preferences tests.
 * Declaration Index: interfaceSpeechLanguage; selectLocalVoice.
 * Variable Index: None.
 */

/* Functionality: Map the existing interface language to a supported speech language.
 * Inputs: Document language. Outputs: zh-CN, en-US or null.
 * Logic: The interface supports Chinese and English; regional tags preserve their primary language.
 * Constraints: An unknown language never silently selects another language or a remote voice. */
export function interfaceSpeechLanguage(language) {
  if (typeof language !== "string") return null;
  const primary = language.toLowerCase().split("-")[0];
  return primary === "zh" ? "zh-CN" : primary === "en" ? "en-US" : null;
}

/* Functionality: Choose a usable local voice under the product's explicit automatic-selection policy.
 * Inputs: Original structured voice inventory, supported speech language and optional current ID.
 * Outputs: Selected voice ID or empty string when no valid match exists.
 * Logic: Require a unique native ID; rank exact tag, system default, then stable current choice.
 * Constraints: No network/default-voice/cross-language fallback, mutation, engine call or playback. */
export function selectLocalVoice(voices, language, currentId = "") {
  if (!Array.isArray(voices) || !["zh-CN", "en-US"].includes(language)) return "";
  const target = language.toLowerCase(), primary = target.split("-")[0];
  const ids = new Map();
  for (const voice of voices) ids.set(voice?.id, (ids.get(voice?.id) || 0) + 1);
  const candidates = voices.filter(voice => voice?.local_service === true &&
    typeof voice.id === "string" && voice.id.length > 0 && voice.id.length <= 1024 && ids.get(voice.id) === 1 &&
    typeof voice.language === "string" && voice.language.toLowerCase().split("-")[0] === primary);
  candidates.sort((left, right) =>
    Number(right.language.toLowerCase() === target) - Number(left.language.toLowerCase() === target) ||
    Number(right.default === true) - Number(left.default === true) ||
    Number(right.id === currentId) - Number(left.id === currentId));
  return candidates[0]?.id || "";
}
