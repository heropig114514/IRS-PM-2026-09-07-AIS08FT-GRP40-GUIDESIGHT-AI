---
name: outputs
description: Speak or stop speech, inspect voices, request or stop vibration, and switch interface language. Verify prerequisites and actually invoke the requested operation.
---

Follow the workflow for the current request. Reading status does not execute an action.
Once prerequisites are met, invoke the operation and report returned evidence. Do not end
the turn merely promising to execute or speak.

1. Inspect ui_snapshot. Speech output is available on maps and camera pages; vibration controls
   are on the camera page. Use ui_navigate(page="camera") only if the current page lacks the
   required controller. Leaving maps stops navigation. Do not navigate unnecessarily when the
   current page is already known.
2. For vibration, inspect supported/enabled/visible/user_activated with vibration_status.
   If prerequisites are missing, explain the exact reason and ask the user to enable the relevant
   switch/click the page personally; do not enable output switches on their behalf.
   Once ready, **immediately call vibration_test(pattern="short"|"long"|"double")**.
   Use short, long or double for the requested pattern. Report the request as accepted only when
   this tool returns accepted=true. hardware_verified=false does not prove physical motor output;
   do not automatically substitute sound.
3. To stop vibration, call vibration_stop and report its result; the switch need not remain enabled.
4. For speech, inspect prerequisites with speech_status; the user must enable the speech switch
   if needed. Once ready, **immediately call speech_speak(text=requested_text, language=matching_language)**.
   Use speech_voices only for a requested voice or voice listing; do not replace an unavailable
   specified voice. Then verify engine events with speech_status; queued/speaking/completed
   does not guarantee the user heard sound.
5. To stop speech, call speech_stop and report the returned state.
6. To change interface language, inspect #language options through ui_snapshot, then call
   ui_action(action="select",selector="#language",value=observed_option_value) and verify the
   returned state. Do not alter original route instructions or user messages.
7. For other existing page controls, observe, execute the user's requested action and verify
   results. Explain unavailable capabilities explicitly. Do not access the assistant's own controls,
   databases or administration features, or resubmit failed actions.
