---
title: Prs Yolo26
emoji: 🚀
colorFrom: green
colorTo: pink
sdk: gradio
sdk_version: 6.29.0
python_version: '3.12.12'
app_file: app.py
pinned: false
license: mit
short_description: prs-yolo26
---

YOLO26n COCO 80-class inference on Hugging Face ZeroGPU. Click **Test public sample** to verify an annotated bus image. Original weights are SHA256 verified before loading.

This optional backend uses FP32 PyTorch, input `[1,3,640,640]`, confidence `0.25`, the existing Pillow bilinear letterbox and JPEG quality80 renderer. No additional NMS, quantization, CPU fallback or retries. PyTorch2.8 is selected for ZeroGPU compatibility; numerical equivalence must be checked against the existing ONNX baseline.

The Gradio API `/detect` receives one JSON value:

```json
{"schema_version": 1, "jpeg_base64": "<base64 JPEG>"}
```

Passing `null` selects the bundled public sample. JPEGs must be complete, at most1MiB, and have maximum edge640. The JSON output contains annotated `jpeg_base64`, boxes, count, image size, model, precision and `inference_ms`. Timing covers GPU input transfer/forward/output transfer, excluding queue/network/render. `/status` reports startup configuration without allocating GPU; it does not prove actual GPU availability.

The application does not write submitted frames or annotations to disk. Hugging Face operates the hosting infrastructure; only send images you are willing to process there. Public source contains no camera frames, SSH credentials or account tokens. The API uses Gradio JSON rather than file-upload components.

ZeroGPU is allocated per request, may queue, and has daily quotas. It is suitable for experiments and demonstrations; uninterrupted real-time streaming is not guaranteed. Free account quota is currently5GPUminutes/day; anonymous quota2minutes/day. Authentication affects quota and priority. See [official ZeroGPU documentation](https://huggingface.co/docs/hub/spaces-zerogpu).

The pre-existing Space code metadata remains MIT. **Ultralytics software and YOLO26n weights are AGPL-3.0 licensed** (or separately commercially licensed); MIT metadata does not relicense those dependencies or weights. See [Ultralytics licensing](https://docs.ultralytics.com/help/FAQ/#what-are-the-licensing-options-for-ultralytics-yolo).

Deployment source is maintained under `backend/zerogpu` in the PRS project. `tools/deploy_space.py` stages only an explicit allowlist, reusing `core/detector.py` without maintaining a second image-processing implementation.

`packages.txt` is a plain Debian package-name list consumed directly by the host's `xargs`; it has no comment syntax or code declarations. `libgl1` and `libglib2.0-0` supply the native libraries required by the pinned OpenCV wheel. These packages are installed only inside the Space container.
