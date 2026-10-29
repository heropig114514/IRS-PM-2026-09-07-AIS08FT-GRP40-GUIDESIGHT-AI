/* Responsibilities: Describe fixed bilingual warning resources without generating or playing audio.
 * Implementation: Reuse the production text formatter, split only at natural warning punctuation,
 * deduplicate deterministic fragment identities, and optionally write one exclusive JSON plan.
 * Related Modules: ../integration/video-alerts.js; tests/risk-audio-plan.test.mjs;
 * the separate offline audio-generation tool consumes this data-only resource plan.
 * Declaration Index: templateWarning; splitWarning; buildRiskAudioPlan;
 * buildRiskAudioPlan.addClip; main.
 * Variable Index: All plan collections and template descriptors belong to each builder call;
 * fragment IDs hash language/kind/text identity, not audio bytes. No model/source state is retained.
 */
import {createHash} from "node:crypto";
import {writeFileSync} from "node:fs";
import {resolve} from "node:path";
import {fileURLToPath} from "node:url";
import {videoAlertText} from "../integration/video-alerts.js";

/* Functionality: Obtain exact production wording for one offline grammar combination.
 * Inputs: Fixed label/direction/grade/approach template and supported language. Outputs: Text.
 * Logic: Exercise the existing formatter's confirmed-approach branch with template-only fields.
 * Constraints: No classification, observation, source time, target ID, alert flag or risk input is
 * produced; the signed template speed only selects existing wording and is not an estimate. */
function templateWarning({label, direction, risk_level, approaching}, language) {
  return videoAlertText({label, direction, risk_level,
    motion_state: approaching ? "approaching" : "unknown",
    relative_speed_mps: approaching ? -2 : null,
    risk_reasons: approaching ? ["approaching", "closing_mps_ge_2"] : []}, language);
}

/* Functionality: Preserve complete clauses and the medium-slow Chinese attention sentence.
 * Inputs: Exact production text, language and grammar variant. Outputs: Ordered kind/text pairs.
 * Logic: Split Chinese after its warning comma and English after its first period plus space;
 * retain Chinese medium-slow wording as a whole sentence because it has no natural prefix seam.
 * Constraints: Fail on a changed punctuation contract; never split characters, alter wording,
 * insert silence, synthesize audio or claim that joined clips have been listened to. */
function splitWarning(text, language, {risk_level, approaching}) {
  if (language === "zh-CN" && risk_level === "medium" && !approaching)
    return [{kind: "sentence", text}];
  const separator = language === "zh-CN" ? "，" : ". ";
  const boundary = text.indexOf(separator);
  if (boundary <= 0 || boundary + separator.length >= text.length)
    throw new Error("The warning punctuation contract cannot be split safely");
  const end = boundary + separator.length;
  return [{kind: "prefix", text: text.slice(0, end)}, {kind: "clause", text: text.slice(end)}];
}

/* Functionality: Build the approved data-only bilingual warning resource plan.
 * Inputs: None. Outputs: Fresh schema-version-2 plan with 96 fragments and 216 compositions.
 * Logic: Cover six presentation labels, three forward directions, three grades and two approach wordings
 * in Chinese then English. Prioritize critical/high pedestrian text, then remaining critical,
 * high and medium groups. Deduplicate natural units by language, role and exact transcript.
 * Constraints: PCM metadata describes the intended mono WAV resource content, not existing audio.
 * No service, upload, runtime cache/manager wiring, algorithm change or audio generation occurs. */
export function buildRiskAudioPlan() {
  const classes = ["person", "bicycle", "car", "motorcycle", "chair", "obstacle"];
  const directions = ["front", "front_left", "front_right"];
  const order = [{label: "person", risk_level: "critical"}, {label: "person", risk_level: "high"}];
  for (const risk_level of ["critical", "high", "medium"])
    for (const label of classes)
      if (label !== "person" || risk_level === "medium") order.push({label, risk_level});
  const clips = [], compositions = [], fragments = new Map();

  /* Functionality: Register a deterministic fragment once within this plan.
   * Inputs: Exact language/text/role. Outputs: Globally unique ASCII clip ID.
   * Logic: Hash transcript identity and retain a safe relative WAV path under its language.
   * Constraints: The digest is not an audio checksum; returned IDs never derive from user paths. */
  function addClip(language, text, kind) {
    const identity = `${language}\0${kind}\0${text}`;
    if (fragments.has(identity)) return fragments.get(identity);
    const digest = createHash("sha256").update(identity, "utf8").digest("hex");
    const id = `${language}-${kind}-${digest}`;
    clips.push({id, language, text, kind, path: `${language}/${id}.wav`});
    fragments.set(identity, id); return id;
  }

  for (const language of ["zh-CN", "en-US"])
    for (const {label, risk_level} of order)
      for (const direction of directions)
        for (const approaching of [false, true]) {
          const variant = {label, direction, risk_level, approaching};
          const text = templateWarning(variant, language);
          const clip_ids = splitWarning(text, language, variant)
            .map(fragment => addClip(language, fragment.text, fragment.kind));
          compositions.push({id: `${language}-${label}-${direction}-${risk_level}-${approaching ? "approaching" : "present"}`,
            language, ...variant, text, clip_ids});
        }
  return {schema_version: 2, voice: "Cherry", rate: 2, sample_rate: 24000,
    format: "pcm_s16le_mono", classes, directions, clips, compositions};
}

/* Functionality: Write one explicitly selected new JSON resource plan.
 * Inputs: --output followed by a file path whose parent already exists. Outputs: Exit code and counts.
 * Logic: Serialize deterministically and use exclusive creation; importing this module performs no I/O.
 * Constraints: No default output path, overwritten file, parent creation, network or audio generation. */
function main(argv) {
  if (argv.length !== 2 || argv[0] !== "--output" || !argv[1]) {
    process.stderr.write("Usage: node tools/risk-audio-plan.mjs --output <new-plan.json>\n");
    return 2;
  }
  try {
    const plan = buildRiskAudioPlan();
    writeFileSync(resolve(argv[1]), JSON.stringify(plan, null, 2) + "\n", {encoding: "utf8", flag: "wx"});
    process.stdout.write(JSON.stringify({created: true, clips: plan.clips.length, compositions: plan.compositions.length}) + "\n");
    return 0;
  } catch (error) {
    process.stderr.write(`Risk audio plan was not created: ${error.code || "plan_error"}.\n`);
    return 1;
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  process.exitCode = main(process.argv.slice(2));
