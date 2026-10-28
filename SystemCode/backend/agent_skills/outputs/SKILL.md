---
name: outputs
description: Speak or stop speech, manage environmental voice alerts, inspect voices, request or stop vibration, and switch interface language. Verify prerequisites and invoke requested operations.
---

Follow the workflow for the current request. Reading status does not execute an action.
Once prerequisites are met, invoke the operation and report returned evidence. Do not end
the turn merely promising to execute or speak.

1. Inspect ui_snapshot. Speech output is available on maps and camera pages; vibration controls
   are on the camera page. Use ui_navigate(page="camera") only if the current page lacks the
   required controller. Leaving maps stops navigation. Do not navigate unnecessarily when the
   current page is already known.
2. For vibration, inspect supported/enabled/visible/user_activated with vibration_status.
   When the user requested vibration and the app switch is off, enable the observed
   `#vibration-enabled` control with ui_action(action="check", value=true), then verify with
   vibration_status. The user does not need to operate an application switch for the agent.
   Once supported, visible and enabled, **call vibration_test(pattern="short"|"long"|"double")**.
   Use short, long or double for the requested pattern. If the browser reports activation_required,
   explain that the page needs a real user interaction; do not imitate a gesture. Report the request
   as accepted only when this tool returns accepted=true. hardware_verified=false does not prove
   physical motor output; do not automatically substitute sound.
3. To stop vibration, call vibration_stop and report its result; the switch need not remain enabled.
4. For speech, inspect prerequisites with speech_status. When the user requested speech and the app
   switch is off, enable the observed `#speech-enabled` control with ui_action(action="check",
   value=true), then verify with speech_status. Once supported, visible and enabled,
   **call speech_speak(text=requested_text, language=matching_language)**.
   Use speech_voices only for a requested voice or voice listing; do not replace an unavailable
   specified voice. If browser activation or playback is blocked, explain the returned reason and
   request only the required real page interaction. Then verify engine events with speech_status;
   queued/speaking/completed does not guarantee the user heard sound.
5. To stop speech, call speech_stop and report the returned state.
6. For an explicit request to enable or disable environmental voice alerts, inspect
   `#assist-risk-enabled` in ui_snapshot and set its requested state with ui_action(action="check").
   When enabling alerts, also enable `#speech-enabled` if it is off. Verify both returned `checked`
   states. If the alert control is absent, report that it is unavailable on the current page; do
   not start camera capture or detection to create it.
7. To change interface language, inspect #language options through ui_snapshot, then call
   ui_action(action="select",selector="#language",value=observed_option_value) and verify the
   returned state. Do not alter original route instructions or user messages.
8. For other existing page controls, observe, execute the user's requested action and verify
   results. Explain unavailable capabilities explicitly. Do not access the assistant's own controls,
   databases or administration features, or resubmit failed actions.
