/* Responsibilities: Protect product checker boundaries and prevent optimistic UI acceptance.
 * Implementation: Pure body-free snapshots exercise negative acceptance gates without browsers.
 * Related Modules: tools/check_product_browser.mjs; phone-relay-browser.test.mjs.
 * Declaration Index: initial; surface; testURL; testStartup; testVoice; testDefaults; testDiagnostics;
 * testAccessibility; testCameraRequest; testCameraDisplay.
 * Variable Index: URL/ENV — synthetic CLI fixtures, never access credentials or a real runtime.
 */
import assert from "node:assert/strict";
import test from "node:test";
import {validateArguments, verifyInitial, verifyProductSurface, verifyCameraRequest, verifyCameraDisplay} from "../tools/check_product_browser.mjs";

const URL = "https://47.239.50.129/mvp/maps/", ENV = {PRS_PLAYWRIGHT_PATH: "synthetic-runtime"};

/* Functionality: Build a fresh startup snapshot. Inputs: None. Outputs: synthetic evidence.
 * Logic: valid defaults with unactivated native engine. Constraints: never represents browser success. */
function initial() {
  return {camera_calls: 0, speech_calls: 0, gps_watches: 0, gps_one_shots: 0, outputs: [true, true, true],
    source: "camera", rate: 2, volume: 1, activation: {ready: false}, gated: true,
    voice: {id: "synthetic-local", language: "zh-CN", local_service: true}};
}

/* Functionality: Build a fresh surface snapshot. Inputs: None. Outputs: synthetic layout evidence.
 * Logic: named mobile controls, hidden diagnostics. Constraints: never substitutes actual screenshots. */
function surface() {
  return {horizontal_overflow: 0, duplicate_ids: [], unnamed_controls: [], small_touch_controls: [], visible_test_controls: [],
    visible_test_text: [], voice_hold_label: "按住说话", diagnostic_link: null};
}

/* Functionality: Reject authentication scope expansion. Inputs: synthetic URL/paths.
 * Outputs: accepted or fixed rejection. Logic: exercise independent gates. Constraints: no file/network access. */
test("product checker pins credentials to exact HTTPS product entry and new report shape", function testURL() {
  assert.equal(validateArguments([URL, "private.env", "new.json"], ENV).url, URL);
  assert.equal(validateArguments([URL, "private.env", "new.json", "--boundaries"], ENV).boundaries, true);
  assert.equal(validateArguments([URL, "private.env", "new.json", "--camera-boundaries"], ENV).cameraBoundaries, true);
  assert.throws(() => validateArguments([URL, "private.env", "new.json", "--boundaries", "--camera-boundaries"], ENV), /INVALID_ARGUMENTS/);
  for (const value of [URL.replace("https:", "http:"), URL.replace("47.239.50.129", "example.org"),
    URL + "test", URL + "?secret=value", URL + "#fragment", URL.replace("https://", "https://phone:secret@")]) {
    assert.throws(() => validateArguments([value, "private.env", "new.json"], ENV), /INVALID_PRODUCT_URL/);
  }
  assert.throws(() => validateArguments([URL, "same.json", "same.json"], ENV), /INVALID_REPORT_PATH/);
  assert.throws(() => validateArguments([URL, "private.env", "new.json", "extra"], ENV), /INVALID_ARGUMENTS/);
});

/* Functionality: Reject implicit startup side effects. Inputs: one changed sensor/output count.
 * Outputs: fixed rejection. Logic: independently corrupt each counter. Constraints: no native APIs. */
test("enabled product outputs cannot excuse on-load permissions or playback", function testStartup() {
  assert.equal(verifyInitial(initial()).gated, true);
  for (const key of ["camera_calls", "speech_calls", "gps_watches", "gps_one_shots"]) {
    const value = initial(); value[key] = 1;
    assert.throws(() => verifyInitial(value), /AUTOMATIC_DEVICE_OR_SPEECH_START/);
  }
});

/* Functionality: Reject remote, mismatched and missing automatic voices. Inputs: inventory snapshots.
 * Outputs: rejection. Logic: local language and activation gates. Constraints: no playback or remote lookup. */
test("product voice must be local and follow the supported interface language", function testVoice() {
  for (const voice of [{id: "remote", language: "zh-CN", local_service: false},
    {id: "wrong-language", language: "en-US", local_service: true}, {}]) {
    const value = initial(); value.voice = voice;
    assert.throws(() => verifyInitial(value), /PRODUCT_AUTOMATIC_LOCAL_VOICE_MISSING/);
  }
  const ready = initial(); ready.activation.ready = true;
  assert.throws(() => verifyInitial(ready), /PRODUCT_ACTIVATION_GATE_MISSING/);
});

/* Functionality: Reject unintended default-policy changes. Inputs: changed output/source/rate/volume.
 * Outputs: rejection. Logic: one policy field each. Constraints: assertions describe product policy only. */
test("default output controls, camera source and original speech parameters remain explicit", function testDefaults() {
  for (const changed of [{outputs: [true, false, true]}, {source: "video"}, {rate: 1}, {volume: 0}])
    assert.throws(() => verifyInitial({...initial(), ...changed}), /PRODUCT_DEFAULTS_INVALID/);
});

/* Functionality: Require press-to-talk and ensure diagnostic links/text are absent. Inputs: visible surface evidence.
 * Outputs: rejection. Logic: one independent exposure each. Constraints: synthetic DOM evidence only. */
test("product surface requires press-to-talk and hides diagnostic links and text", function testDiagnostics() {
  assert.equal(verifyProductSurface(surface()).diagnostic_link, null);
  for (const changed of [{visible_test_controls: ["speech-play"]}, {visible_test_controls: ["example"]},
    {visible_test_text: ["PRF2"]}, {visible_test_text: ["Live camera"]},
    {visible_test_text: ["Navigation not started"]}, {visible_test_text: ["No reliable navigation progress"]},
    {visible_test_text: ["Waiting for turn guidance"]}, {visible_test_text: ["Find a route to start navigation."]}])
    assert.throws(() => verifyProductSurface({...surface(), ...changed}), /PRODUCT_DIAGNOSTICS_EXPOSED/);
  assert.throws(() => verifyProductSurface({...surface(), voice_hold_label: null}), /PRODUCT_HOLD_CONTROL_MISSING/);
  assert.throws(() => verifyProductSurface({...surface(), diagnostic_link: "/mvp/maps/test"}), /PRODUCT_DIAGNOSTIC_LINK_EXPOSED/);
});

/* Functionality: Reject inaccessible or ambiguous product surfaces. Inputs: broken layout, binding, name or PTT control.
 * Outputs: rejection. Logic: distinct accessibility gates. Constraints: actual layout still requires browser. */
test("mobile overflow, missing labels, duplicate bindings and absent voice control fail", function testAccessibility() {
  assert.throws(() => verifyProductSurface({...surface(), horizontal_overflow: 20}), /PRODUCT_LAYOUT_INVALID/);
  assert.throws(() => verifyProductSurface({...surface(), duplicate_ids: ["start"]}), /PRODUCT_LAYOUT_INVALID/);
  assert.throws(() => verifyProductSurface({...surface(), unnamed_controls: ["language"]}), /PRODUCT_CONTROL_LABEL_MISSING/);
  assert.throws(() => verifyProductSurface({...surface(), small_touch_controls: ["start"]}), /PRODUCT_TOUCH_TARGET_TOO_SMALL/);
  assert.throws(() => verifyProductSurface({...surface(), voice_hold_label: ""}), /PRODUCT_HOLD_CONTROL_MISSING/);
});

/* Functionality: Reject duplicate acquisition and relaxed or microphone-enabled camera requests.
 * Inputs: body-free request fixtures. Outputs: acceptance/rejection. Logic: independently break gates.
 * Constraints: no browser device acquisition or claim of physical rear-camera verification. */
test("product camera requires one real-click exact rear request without audio or selected device", function testCameraRequest() {
  const request = {audio_false: true, exact_rear: true, exact_device: false, user_gesture_active: true};
  assert.equal(verifyCameraRequest([request]), request);
  for (const value of [[], [request, request], [{...request, audio_false: false}], [{...request, exact_rear: false}],
    [{...request, exact_device: true}], [{...request, user_gesture_active: false}]])
    assert.throws(() => verifyCameraRequest(value), /PRODUCT_REAR_CAMERA_REQUEST_INVALID/);
});

/* Functionality: Require a hidden capture source and visible decoded detection result.
 * Inputs: synthetic media evidence. Outputs: rejection per missing signal. Logic: capture state plus decode.
 * Constraints: a valid fixture is not a claim about model accuracy or actual device frames. */
test("product shows detection results while the playing capture source stays hidden", function testCameraDisplay() {
  const value = {preview_hidden: true, preview_playing: true, preview_width: 640, preview_height: 480,
    result_visible: true, result_decoded: true, result_width: 640, result_height: 480, result_blob: true, frames: 1};
  assert.equal(verifyCameraDisplay(value), value);
  for (const field of ["preview_hidden", "preview_playing", "result_visible", "result_decoded", "result_blob"])
    assert.throws(() => verifyCameraDisplay({...value, [field]: false}), /PRODUCT_CAMERA_DISPLAY_INVALID/);
  for (const field of ["preview_width", "preview_height", "result_width", "result_height", "frames"])
    assert.throws(() => verifyCameraDisplay({...value, [field]: 0}), /PRODUCT_CAMERA_DISPLAY_INVALID/);
});
