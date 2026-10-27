# YOLO26n Model Artifacts

The initial release uses the official Ultralytics YOLO26n pretrained weights for 80 COCO classes and requires no additional training. This is not zero-shot recognition of arbitrary classes. The detector does not guarantee stair, pothole, or road-safety judgments.

`yolo26n.json` tracks the model, class order, original weight source, build version, and SHA-256. Weights and ONNX files are not committed to Git. Official references: [YOLO26](https://docs.ultralytics.com/models/yolo26/) and [export mode](https://docs.ultralytics.com/modes/export/). The model and Ultralytics build tools are subject to their AGPL-3.0 terms; this does not change the licensing of other repository files.

Install `requirements-vision-build.txt` in an isolated build virtual environment, then run from `backend`:

```powershell
python tools/export_yolo26.py --output models
```

The script downloads the original weights from the official HTTPS release in the manifest. It fixes CPU FP32, image size 640, batch size 1, opset 17, static shapes, `nms=False`, `simplify=False`, and `max_det=300`. It uses one-to-one end-to-end output `[1,300,6]`; the server performs no additional NMS. Re-exporting may change artifact hashes, so publish the new artifact and manifest together. The server never downloads, converts, or replaces a model automatically.

The current camera uses only the original PyTorch YOLO26n weights hosted on HF and verified with SHA-256. The CPU ONNX service and `YOLO_MODEL_PATH` setting have been removed. The export script above is for offline artifact reproduction only; it is not a selectable processing mode. Inputs use centered RGB letterboxing, Pillow bilinear interpolation, padding value 114, FP32/255, size 640, and confidence threshold 0.25. Annotations use original-size JPEG quality 80. GPU tasks reuse CUDA Graphs; weights and backends are never switched automatically.

`inference-plan.json` is a required deployment artifact. It records actual compute precision, fusion/buffering/Graph/AOT/JIT configuration, source-report SHA for the same Notebook version, and three complete 5,000-image COCO results. Each AP decrease must be at most 0.5 percentage points. Input normalization remains FP32, values are converted only at GPU entry according to explicit configuration, and returned arrays remain FP32. Startup fails if plan loading, configuration, or quality checks fail; there is no default plan. AOT compilation runs at startup on the actual HF GPU and preserves original/compatible packages and each shared-library SHA. Cached JIT preserves official compiler artifacts and the Inductor/Triton directories; a new allocation must hit FX, and backend code generation and later recompilation are prohibited. Both methods validate hardware, versions, and configuration. Kaggle binaries are not published across GPUs.

`SystemCode/clips/frontend/tools/fixtures/detection-sample.jpg` is the 480×640 JPEG quality 80 version of `bus.jpg` from Ultralytics 8.4.171. It is used only in development browser tests and is no longer a public deployment asset. The original image bytes and source license are preserved.
