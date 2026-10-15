/** Responsibilities: Explicit local-camera permission and inventory adaptation.
 * Implementation: User-requested video-only permission, immediate track cleanup, then inventory;
 * device choice remains a separate explicit UI action.
 * Related Modules: local-mvp.js; camera.js starts inference only after a selected deviceId.
 * Declaration Index: authorizeCameraDevices; explicitDeviceId.
 * Variable Index: None; temporary streams are not retained, displayed, encoded or transmitted.
 */

/** Functionality: Authorize camera labels/IDs at the user's request. Inputs: browser mediaDevices.
 * Outputs: newly readable inventory or explicit permission/cleanup/inventory error.
 * Logic: getUserMedia(video true/audio false), stop every temporary track, then enumerate devices.
 * Constraints: no auto selection, facing fallback, microphone, inference, stream retention or retry;
 * each cleanup attempt runs once even if another track fails, and any cleanup failure is reported. */
export async function authorizeCameraDevices(mediaDevices) {
  const stream = await mediaDevices.getUserMedia({video: true, audio: false});
  let cleanupError = null;
  for (const track of stream.getTracks()) {
    try { track.stop(); } catch (error) { cleanupError ??= error; }
  }
  if (cleanupError) throw cleanupError;
  return mediaDevices.enumerateDevices();
}

/** Functionality: Preserve only a still-valid explicit device selection. Inputs: devices/requestedId.
 * Outputs: requested ID or empty string. Logic: match a video input exactly; blank/removed IDs stay blank.
 * Constraints: never choose the first/default camera or treat permission granting as selection. */
export function explicitDeviceId(devices, requestedId) {
  return typeof requestedId === "string" && requestedId &&
    devices.some(device => device.kind === "videoinput" && device.deviceId === requestedId) ? requestedId : "";
}
