# Dataset Storage

Place personally captured images or downloaded public datasets here, preserving their original directory structure and ground-truth annotations.
Examples: `my-photos/`, `coco/val2017/`, and `nyu-depth-png/images/val/`.
The four MOT17 test videos in `street-video/mot17/` and `street-video/SOURCES.json` are versioned, including the original previews and H.264 browser-playback copies for MOT17-07 and MOT17-10. The full datasets, annotations, extracted frames, and local run reports are ignored by Git; prepare them separately when needed. Nothing is downloaded or partitioned automatically.

Use `/integration/video/` to replay MP4 files and select `*-browser-h264.mp4`. The image scripts below do not read videos directly. See [Local MVP](../../LOCAL-MVP.md#continuous-local-video-entry) for startup and operation.

Run from `SystemCode/backend/model_tests`:

```powershell
..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --input datasets/my-photos --output outputs/photos-run-1
```

Point `--input` to an RGB image subdirectory; avoid PNG directories containing depth maps or masks. See [DATASETS.md](../DATASETS.md) for suggested quantities, public download sources, and ground-truth notes.
