/* Responsibilities: Verify the offline bilingual warning plan and exclusive JSON-only CLI.
 * Implementation: Independent Cartesian coverage, exact production wording, punctuation seams,
 * safe deterministic resource identities, isolated builder state and real local CLI invocations.
 * Related Modules: ../tools/risk-audio-plan.mjs; ../integration/video-alerts.js.
 * Declaration Index: inTempDirectory; expectedText; testPlanSchema; testPlanCoverage;
 * testNaturalFragments; testPlanIsolation; testPlanCli.
 * Variable Index: TOOL_PATH locates the repository CLI; all plans and temporary files are test-owned.
 * Constraints: No TTS service, audio generation, playback, browser, upload or runtime policy change.
 */
import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {basename, dirname, join} from "node:path";
import {fileURLToPath} from "node:url";
import {mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import test from "node:test";
import {buildRiskAudioPlan} from "../tools/risk-audio-plan.mjs";
import {videoAlertText} from "../integration/video-alerts.js";

const TOOL_PATH = fileURLToPath(new URL("../tools/risk-audio-plan.mjs", import.meta.url));

/* Functionality: Isolate CLI evidence in one disposable test-owned temporary directory.
 * Inputs: Synchronous callback. Outputs: Callback result and checked cleanup.
 * Logic: Resolve the created target and verify its parent and generated prefix before recursion.
 * Constraints: Never remove any path outside the exact temporary child created by this helper. */
function inTempDirectory(callback) {
  const parent = realpathSync(tmpdir()), directory = mkdtempSync(join(parent, "prs-risk-audio-plan-"));
  try { return callback(directory); }
  finally {
    const target = realpathSync(directory);
    assert.equal(dirname(target), parent);
    assert.ok(basename(target).startsWith("prs-risk-audio-plan-"));
    rmSync(target, {recursive: true, force: true});
  }
}

/* Functionality: Obtain a normal warning for comparison with an offline composition.
 * Inputs: One requested variant. Outputs: Production wording from an independent backend-shaped object.
 * Logic: Use different valid motion values from the builder's templates.
 * Constraints: No source data is submitted to inference or an output owner. */
function expectedText(variant) {
  return videoAlertText({label: variant.label, direction: variant.direction, risk_level: variant.risk_level,
    track_id: 27, alert_required: true, alert_priority: {medium: 2, high: 3, critical: 4}[variant.risk_level],
    distance_m: 4, ttc_sec: 2, motion_state: variant.approaching ? "approaching" : "moving_away",
    relative_speed_mps: variant.approaching ? -3 : 0.5,
    risk_reasons: variant.approaching ? ["approaching", "closing_mps_ge_2"] : ["moving_away"]}, variant.language);
}

/* Functionality: Lock the authorized resource scope and intended synthesis profile.
 * Inputs: Built plan. Outputs: Exact schema metadata and fragment/composition counts.
 * Logic: Check all approved classes, directions and role totals independently.
 * Constraints: Profile metadata does not claim that any WAV or audible speech already exists. */
test("offline risk plan contains the exact approved bilingual profile and 96 natural fragments", function testPlanSchema() {
  const plan = buildRiskAudioPlan();
  assert.deepEqual(Object.keys(plan), ["schema_version", "voice", "rate", "sample_rate", "format",
    "classes", "directions", "clips", "compositions"]);
  assert.equal(plan.schema_version, 2); assert.equal(plan.voice, "Cherry");
  assert.equal(plan.rate, 2); assert.equal(plan.sample_rate, 24000); assert.equal(plan.format, "pcm_s16le_mono");
  assert.deepEqual(plan.classes, ["person", "bicycle", "car", "motorcycle", "chair", "obstacle"]);
  assert.deepEqual(plan.directions, ["front", "front_left", "front_right"]);
  assert.equal(plan.clips.length, 96); assert.equal(plan.compositions.length, 216);
  for (const [language, counts] of [["zh-CN", {prefix: 3, clause: 36, sentence: 18}],
    ["en-US", {prefix: 3, clause: 36, sentence: 0}]]) {
    assert.equal(plan.compositions.filter(item => item.language === language).length, 108);
    assert.equal(plan.clips.filter(item => item.language === language).length,
      Object.values(counts).reduce((sum, count) => sum + count, 0));
    for (const [kind, count] of Object.entries(counts))
      assert.equal(plan.clips.filter(item => item.language === language && item.kind === kind).length, count);
  }
  assert.deepEqual(JSON.parse(JSON.stringify(plan)), plan);
});

/* Functionality: Cover every class/direction/grade/motion combination without changing its spoken text.
 * Inputs: All 216 plan compositions and an independent Cartesian expectation.
 * Outputs: Exact production wording, exact transcript concatenation and full unique coverage.
 * Logic: Resolve every referenced clip and compare the complete tuple set.
 * Constraints: No risk grade, approach marker, category or Chinese medium wording is substituted. */
test("every offline composition exactly reconstructs the existing warning wording", function testPlanCoverage() {
  const plan = buildRiskAudioPlan(), clips = new Map(plan.clips.map(clip => [clip.id, clip]));
  const expected = new Set(), actual = new Set(), used = new Set();
  for (const language of ["zh-CN", "en-US"])
    for (const label of ["person", "bicycle", "car", "motorcycle", "chair", "obstacle"])
      for (const direction of ["front_left", "front", "front_right"])
        for (const risk_level of ["medium", "high", "critical"])
          for (const approaching of [true, false])
            expected.add(JSON.stringify([language, label, direction, risk_level, approaching]));
  assert.equal(new Set(plan.compositions.map(item => item.id)).size, 216);
  for (const composition of plan.compositions) {
    actual.add(JSON.stringify([composition.language, composition.label, composition.direction,
      composition.risk_level, composition.approaching]));
    assert.equal(typeof composition.approaching, "boolean");
    assert.equal(composition.text, expectedText(composition));
    const parts = composition.clip_ids.map(id => {
      assert.ok(clips.has(id)); used.add(id);
      const clip = clips.get(id); assert.equal(clip.language, composition.language); return clip.text;
    });
    assert.equal(parts.join(""), composition.text);
    assert.doesNotMatch(composition.text, /track_id|captured_at|\d/);
  }
  assert.deepEqual(actual, expected); assert.equal(used.size, 96);
  assert.ok(plan.compositions.some(item => item.text === "注意左前方的行人。"));
  assert.ok(plan.compositions.some(item => item.text === "注意，左前方有行人靠近。"));
  assert.ok(plan.compositions.some(item => item.text === "Warning. Person ahead left is approaching."));
});

/* Functionality: Keep composition boundaries natural and resource paths deterministic and safe.
 * Inputs: Plan clips and compositions. Outputs: No single-character pieces or arbitrary file paths.
 * Logic: Permit a complete Chinese medium-slow sentence or a warning prefix plus complete clause;
 * assert the exact punctuation seam and unambiguous ASCII language/hash resource identities.
 * Constraints: Text-identity hashes are not treated as audio integrity or listening evidence. */
test("offline fragments split only after warning punctuation and use safe relative WAV paths", function testNaturalFragments() {
  const plan = buildRiskAudioPlan(), clips = new Map(plan.clips.map(clip => [clip.id, clip]));
  assert.equal(clips.size, 96); assert.equal(new Set(plan.clips.map(clip => clip.path)).size, 96);
  for (const clip of plan.clips) {
    assert.match(clip.id, /^(zh-CN|en-US)-(prefix|clause|sentence)-[a-f0-9]{64}$/);
    assert.equal(clip.path, `${clip.language}/${clip.id}.wav`);
    assert.ok(clip.text.length >= 3);
    assert.ok(!clip.path.includes("..") && !clip.path.includes("\\") && !clip.path.includes(":"));
    assert.deepEqual(Object.keys(clip), ["id", "language", "text", "kind", "path"]);
  }
  for (const composition of plan.compositions) {
    const parts = composition.clip_ids.map(id => clips.get(id));
    if (composition.language === "zh-CN" && composition.risk_level === "medium" && !composition.approaching) {
      assert.equal(parts.length, 1); assert.equal(parts[0].kind, "sentence");
      assert.ok(parts[0].text.startsWith("注意")); assert.ok(parts[0].text.endsWith("。"));
    } else {
      assert.deepEqual(parts.map(part => part.kind), ["prefix", "clause"]);
      assert.ok(parts[0].text.endsWith(composition.language === "zh-CN" ? "，" : ". "));
      assert.ok(parts[1].text.endsWith(composition.language === "zh-CN" ? "。" : "."));
    }
  }
  assert.ok(plan.compositions.slice(0, 6).every(item => item.language === "zh-CN" && item.label === "person" && item.risk_level === "critical"));
  assert.ok(plan.compositions.slice(6, 12).every(item => item.label === "person" && item.risk_level === "high"));
  assert.deepEqual(plan.compositions.slice(0, 6).map(item => [item.direction, item.approaching]),
    [["front", false], ["front", true], ["front_left", false], ["front_left", true], ["front_right", false], ["front_right", true]]);
});

/* Functionality: Keep plan construction deterministic and free of shared mutable resource state.
 * Inputs: Independent builds plus nested caller mutations. Outputs: Unchanged later plans.
 * Logic: Compare complete JSON-compatible values before and after mutations at each collection level.
 * Constraints: Modifying a generated plan cannot change production warning wording or later resources. */
test("offline plan builders return deterministic independent collections", function testPlanIsolation() {
  const first = buildRiskAudioPlan(), second = buildRiskAudioPlan();
  assert.deepEqual(first, second); assert.notEqual(first.clips, second.clips);
  assert.notEqual(first.compositions[0].clip_ids, second.compositions[0].clip_ids);
  first.classes.push("bus"); first.directions[0] = "behind";
  first.clips[0].text = "changed"; first.compositions[0].clip_ids.pop();
  first.compositions[0].approaching = true;
  assert.deepEqual(buildRiskAudioPlan(), second);
});

/* Functionality: Exercise the real resource-plan CLI without overwriting existing files.
 * Inputs: Missing args, explicit new JSON target and an existing sentinel file in an owned temp dir.
 * Outputs: Usage failure, successful deterministic plan, or exclusive-creation refusal.
 * Logic: Run the actual Node entry with bounded subprocess time and verify filesystem contents.
 * Constraints: No parent creation, default output, audio synthesis, upload or persistent test artifact. */
test("offline plan CLI requires an explicit new output file and preserves existing content", function testPlanCli() {
  inTempDirectory(directory => {
    const options = {cwd: directory, encoding: "utf8", timeout: 10000, windowsHide: true};
    const missing = spawnSync(process.execPath, [TOOL_PATH], options);
    assert.equal(missing.error, undefined); assert.equal(missing.status, 2);
    assert.match(missing.stderr, /Usage:/); assert.deepEqual(readdirSync(directory), []);
    const output = join(directory, "plan.json");
    const created = spawnSync(process.execPath, [TOOL_PATH, "--output", output], options);
    assert.equal(created.error, undefined); assert.equal(created.status, 0, created.stderr);
    assert.deepEqual(JSON.parse(created.stdout), {created: true, clips: 96, compositions: 216});
    assert.equal(readFileSync(output, "utf8"), JSON.stringify(buildRiskAudioPlan(), null, 2) + "\n");
    const existing = join(directory, "existing.json"), sentinel = '{"keep":"original"}\n';
    writeFileSync(existing, sentinel, "utf8");
    const refused = spawnSync(process.execPath, [TOOL_PATH, "--output", existing], options);
    assert.equal(refused.error, undefined); assert.equal(refused.status, 1);
    assert.match(refused.stderr, /EEXIST/); assert.equal(readFileSync(existing, "utf8"), sentinel);
    assert.deepEqual(readdirSync(directory).sort(), ["existing.json", "plan.json"]);
  });
});
