---
name: camera
description: Open/close the camera, select existing echo or HF detection mode, inspect frames and request scene descriptions.
---

1. Inspect ui_snapshot. On an `assist-product` or `assist-test` surface, the camera is embedded
   in the current page; use ui_navigate(page="camera") to focus its panel without changing the
   route. On a standard PRS surface, use the same tool to navigate to the camera page. A request
   only to show the page/panel ends after verifying it; do not start capture.
2. For an explicit request to turn on/start the camera, inspect camera_status first. If it is
   already active, report the observed state. On `assist-product`, do not call camera_start: the
   browser permission request must come from a direct page tap, so ask the user to tap the visible
   "Start environment awareness" control and wait for its status. Never use ui_action to click
   `#product-camera-start` on that surface; a synthetic click cannot satisfy browser user activation.
   On other surfaces, call
   camera_start with no mode override to preserve the UI choice. Explicit "object detection"
   requests may use processing="hf"; "echo" requests may use processing="echo". Never silently
   allocate GPU for a generic request.
3. Verify with camera_status(wait_seconds=12). active means a live media track;
   frames_received proves decoded frames reached this page. A permission wait or first
   frame wait is not success. If the browser reports a missing camera, denied permission,
   unavailable selection, or another failure, report that observed reason in the user's language.
   Do not retry errors or replace the chosen camera/mode.
4. To close, call camera_stop and verify inactive with camera_status. Do not navigate away
   as a substitute for a supported stop action. If already stopped, report that state.
5. To change camera/mode while running, stop first, then start with the requested settings.
   Only change facing via observed #camera options and ui_action when explicitly requested.
6. For scene description, inspect ui_snapshot for #scene-question, #scene-describe and
   #scene-result. If the camera is inactive, start and verify it first. Fill an optional
   question, click #scene-describe, wait for that button to become enabled, then inspect
   #scene-result and #scene-message via ui_snapshot. Empty result/error is failure; never
   fabricate a scene. This existing VLM has its own service configuration.
7. Connection testing uses observed #ping and status, only when camera is idle. On the diagnostic
   MVP page, this verifies the mounted WSS echo/ping-pong path only; it does not send an inference
   frame or verify model readiness. To validate inference without camera permission, use the
   diagnostic page's explicit local-video replay, then verify a completed returned frame. For all
   device actions preserve real browser permissions and the existing processing parameters.
