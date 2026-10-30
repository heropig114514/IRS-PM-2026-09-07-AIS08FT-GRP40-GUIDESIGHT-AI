/** Responsibilities: Request page-entry media permissions and adapt local-camera inventory.
 * Implementation: Entry checks request audio and video together and release their tracks; explicit
 * diagnostic authorization remains video-only. Device choice remains a separate user action.
 * Related Modules: local-mvp.js; camera.js starts inference only after a selected deviceId.
 * Declaration Index: acquireAndRelease; requestPageMediaPermissions; authorizeCameraDevices; explicitDeviceId.
 * Variable Index: None; temporary streams are not retained, displayed, encoded or transmitted.
 */

/** Functionality: Acquire one temporary media stream, release every track, and read device inventory.
 * Inputs: browser mediaDevices and explicit getUserMedia constraints. Outputs: enumerated devices.
 * Logic: Cleanup attempts continue after an individual stop error; enumeration occurs only after all
 * cleanup succeeds. Constraints: No stream is returned or retained, and failures are not retried. */
async function acquireAndRelease(mediaDevices, constraints) {
  const stream = await mediaDevices.getUserMedia(constraints);
  let cleanupError = null;
  for (const track of stream.getTracks()) {
    try { track.stop(); } catch (error) { cleanupError ??= error; }
  }
  if (cleanupError) throw cleanupError;
  return mediaDevices.enumerateDevices();
}

/** Functionality: Request camera and microphone permission when a unified page opens.
 * Inputs: Native browser mediaDevices. Outputs: Device inventory after temporary tracks stop.
 * Logic: One combined audio/video request lets the browser present its native permission UI once.
 * Constraints: No recording, preview, inference, device selection, stream retention or retry. */
export function requestPageMediaPermissions(mediaDevices) {
  return acquireAndRelease(mediaDevices, {audio: true, video: true});
}

/** Functionality: Authorize camera labels/IDs at the user's request. Inputs: browser mediaDevices.
 * Outputs: newly readable inventory or explicit permission/cleanup/inventory error.
 * Logic: getUserMedia(video true/audio false), stop every temporary track, then enumerate devices.
 * Constraints: no auto selection, facing fallback, microphone, inference, stream retention or retry;
 * each cleanup attempt runs once even if another track fails, and any cleanup failure is reported. */
export async function authorizeCameraDevices(mediaDevices) {
  return acquireAndRelease(mediaDevices, {video: true, audio: false});
}

/** Functionality: Preserve only a still-valid explicit device selection. Inputs: devices/requestedId.
 * Outputs: requested ID or empty string. Logic: match a video input exactly; blank/removed IDs stay blank.
 * Constraints: never choose the first/default camera or treat permission granting as selection. */
export function explicitDeviceId(devices, requestedId) {
  return typeof requestedId === "string" && requestedId &&
    devices.some(device => device.kind === "videoinput" && device.deviceId === requestedId) ? requestedId : "";
}
