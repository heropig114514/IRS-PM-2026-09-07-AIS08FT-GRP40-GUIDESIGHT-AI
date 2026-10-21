---
name: camera
description: Open/close the camera, select existing echo or HF detection mode, inspect frames and request scene descriptions.
---

1. Inspect ui_snapshot. Camera tools require the camera page. Use ui_navigate(page="camera")
   when needed; leaving maps stops navigation. The conversation survives this page change.
   A request only to open the page ends after verifying the page; do not start capture.
2. Inspect camera_status. For opening, call camera_start with no mode override to preserve
   the UI choice. Explicit "object detection" requests may use processing="hf"; "echo"
   requests may use processing="echo". Never silently allocate GPU for a generic open request.
3. Verify with camera_status(wait_seconds=12). active means a live media track;
   frames_received proves decoded frames reached this page. A permission wait or first
   frame wait is not success. Do not retry errors or replace the chosen camera/mode.
4. To close, call camera_stop and verify inactive with camera_status. Do not navigate away
   as a substitute for a supported stop action. If already stopped, report that state.
5. To change camera/mode while running, stop first, then start with the requested settings.
   Only change facing via observed #camera options and ui_action when explicitly requested.
6. For scene description, inspect ui_snapshot for #scene-question, #scene-describe and
   #scene-result. If the camera is inactive, start and verify it first. Fill an optional
   question, click #scene-describe, wait for that button to become enabled, then inspect
   #scene-result and #scene-message via ui_snapshot. Empty result/error is failure; never
   fabricate a scene. This existing VLM has its own service configuration.
7. Connection testing uses observed #ping and status, only when camera is idle. For all
   device actions preserve real browser permissions and the existing processing parameters.
