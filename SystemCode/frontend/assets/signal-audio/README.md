# Prepared pedestrian-signal speech

This pack contains four complete Cherry utterances: red and green signal guidance in Chinese
and English. The sentences are synthesized at rate 2 and stored as mono 24 kHz signed 16-bit
little-endian PCM inside WAV containers. Playback must not multiply the speed again.

The Chinese wording is “前方是红灯，请等待。” and “绿灯，请通行。”. English uses
“Red light ahead. Please wait.” and “Green light. Please proceed.”. The manifest fixes each
sentence, relative path, container hash, PCM hash, byte count and duration. The resource loader
accepts only these four identities and checks every WAV before making the pack available.

Use `tools/prepare_signal_audio.py` from `SystemCode/backend` with an explicit existing TTS
WebSocket URL and a new output directory to reproduce resources. The tool uses the original
five-field TTS request and writes each phrase once without retries or overwrites. Remote
destinations require verified WSS; plain WS is restricted to loopback. Provider keys remain
on the configured server.

Loading resources never establishes the current signal color or permission to announce it.
The integration scheduler must confirm fresh algorithm output for the selected front pedestrian
light before playback, deduplicate unchanged states and cancel obsolete green guidance.
Physical pronunciation and phone speaker output require listening acceptance.
