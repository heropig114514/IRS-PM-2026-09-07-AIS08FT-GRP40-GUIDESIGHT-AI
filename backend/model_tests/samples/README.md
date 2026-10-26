# Local Depth-Test Sample

`bus.jpg` is an official Ultralytics public example image: [bus.jpg](https://ultralytics.com/images/bus.jpg). Its original bytes are preserved. SHA-256: `c02019c4979c191eb739ddd944445ef408dad5679acab6fd520ef9d434bfbc63`.
The image is attributed to the official Ultralytics example. This repository does not claim new image copyright or dataset-wide licensing.

The image has no detection/depth ground-truth annotations. It is used only to verify dual-model inference, bounding boxes, distance labels, and result output. It is not a training set, accuracy-evaluation set, or real phone video. The program processes the original image according to the existing 640-pixel maximum edge/JPEG quality 80 contract and does not modify the saved source image.

Run from `backend/model_tests`:

```powershell
..\.venv\Scripts\python.exe -X utf8 .\run_depth.py --output outputs/bus-run-3
```

The output directory must be new. See [DATASETS.md](../DATASETS.md) for public dataset choices and model-comparison boundaries, [README.md](../README.md) for run instructions, and [DEPTH.md](../../DEPTH.md) for the fusion implementation.
