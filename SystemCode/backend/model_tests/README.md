# Local Model Testing

Mode A remains the default; use `--distance-mode geometry` for mode B and
`--distance-mode temporal_depth --track --depth-interval 3` for mode C. See
[DISTANCE-MODES.md](../DISTANCE-MODES.md) for full commands, camera parameters, distance caching,
and experiment statistics. The dual-model description below applies to default mode A.

Entry points, examples, datasets, and outputs are organized in this directory. The scripts call
the project's existing YOLO26 detection and depth implementations and retain detection size 640,
confidence 0.25, JPEG quality 80, FP32, and depth size 768. Each model is loaded once and reused.
The current tool exports predictions and timing only: it does not read ground truth or calculate
accuracy, and it does not download, train, sample, or retry automatically.

```text
SystemCode/backend/model_tests/
├── run_depth.py       # Single-image and directory dual-model test entry point
├── test_cli.py        # CLI automation checks, discovered by Django's test runner
├── test_tracking.py   # BoT-SORT, history/motion, and pipeline integration tests
├── README.md          # Usage and output guide
├── DATASETS.md        # Public dataset links, size suggestions, and comparison protocol
├── samples/           # Bundled bus.jpg and source information
├── datasets/          # User-provided or public datasets
└── outputs/           # JSON, annotated images, and status for each run
```

## Run Directly (PowerShell)

From the project root, enter this directory and use the separate GPU environment
`SystemCode/backend/.venv-inference`:

```powershell
cd SystemCode/backend/model_tests

# Use samples/bus.jpg by default; specify a new output directory
..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --output outputs/bus-run-3

# Process a directory after placing your RGB photos there
..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --input datasets/my-photos --output outputs/photos-run-1

# Explicitly repeat the same image without discarding the first frame
..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --output outputs/bus-repeat-1 --repeat 3

# Track one sequence; repeated images can check ID continuity and stationary state
..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --track --repeat 6 --output outputs/tracking-run-1

# Provide continuous, same-size RGB frames from your video, sorted by filenames such as 000001.jpg
..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --track --input datasets/sequence-frames --output outputs/sequence-run-1

..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --help
```

The module entry point can also be run from `backend`:

```powershell
\.venv-inference\Scripts\python.exe -X utf8 -m model_tests.run_depth --output model_tests/outputs/bus-run-3
```

Both entry points behave identically. Default sample, model, and manifest paths are resolved
relative to the script. Explicit relative paths such as `--input`, `--output`, and
`--depth-weights` are resolved from the current working directory. If the output directory already
exists, the command reports `Output already exists; choose a new run directory`; choose a new
directory name. Existing results are never overwritten.

## Organize Datasets

Example paths include `datasets/my-photos/`, `datasets/coco/val2017/`, and
`datasets/nyu-depth-png/images/val/`. Dataset source directories, detection labels, and depth
ground truth may be kept together. Point `--input` only to RGB images or an RGB subdirectory; do
not point it at a dataset root that also contains depth or label PNGs. Supported extensions are
jpg/jpeg/png/bmp/webp. All matching files are read recursively and sorted by path; there is no
automatic split or sampling.

The four MOT17 test videos and `datasets/street-video/SOURCES.json` source manifest are tracked in
version control. Prepare full datasets, annotations, and generated outputs separately; they are
not maintained alongside these test videos. MP4 files are for the separate `/integration/video/`
replay entry point; the current image script does not read video files directly. See
[DATASETS.md](DATASETS.md) for public download links and evaluation notes.

## Models and Environment

Image inference uses the separate GPU environment `SystemCode/backend/.venv-inference` and the shared files
`SystemCode/backend/models/yolo26n.pt`, `SystemCode/backend/models/yolo26n.json`, and
`SystemCode/backend/models/yolo26n-depth.pt`. Web and Django tests use `SystemCode/backend/.venv`; dependencies are
managed separately, and Torch is not installed in the web environment. See the
[local MVP guide](../LOCAL-MVP.md#environment-and-model-setup) for environment setup and pinned
dependencies.

The detection loader validates the existing YOLO26n weights and manifest; `--weights` is not a
generic interface for arbitrary models. Specify compatible depth weights explicitly with
`--depth-weights`. This script requires an NVIDIA CUDA GPU and does not download weights, switch
to CPU, or fall back to a single model. BoT-SORT uses the pinned dependency `lap==0.5.12`.

Image tests do not require the Django server. See [DEPTH.md](../DEPTH.md),
[TRACKING.md](../TRACKING.md), and [RISK.md](../RISK.md) for depth fusion, tracking, and risk
configuration, respectively.

Tracking is disabled by default for independent photos. With `--track`, all inputs and repeats
are treated as one continuous sequence; do not use it for unrelated COCO/NYU images or multiple
camera sequences. For original video, provide time-sorted RGB frames first. This tool does not
read video or capture FPS; speed uses local inference start timestamps, so a directory run cannot
establish the original video's real motion speed. Pass `--tracking-config <JSON path>` to override
the centralized configuration; `--track` is required. Enabling tracking also enables TTC/risk.
Pass `--risk-config <JSON path>` to override centralized risk rules; this also requires `--track`.
The complete effective rules are stored in `run.json.settings.risk`; the original motion
parameters are unchanged.

## Read the Output

Each `outputs/<run-name>/` directory contains `run.json` (status, settings, and hashes),
`results.jsonl` (predictions for each image/repeat), and
`annotations/<image-name>/<repeat-index>.jpg`. Inspect the annotated image, then review
`class_name/confidence/bbox/distance_m` in `metadata.boxes`. Distance is in meters; unavailable
distance is `null`. Box coordinates refer to the resized frame. The file records original and
frame dimensions for later alignment with ground truth.

When tracking is enabled, the output also includes
`track_id/smoothed_distance_m/relative_speed_mps/motion_state`. Speed is in m/s; negative means
approaching and positive means receding. After EMA smoothing, linear regression uses all historical
timestamps. Speed estimation starts with at least three valid samples; classification requires two
consecutive matching candidates and is confirmed no earlier than the fourth valid sample by
default. Labels include confirmed speed. A brief loss of distance retains the historical estimate;
it is cleared to `unknown`/`null` only after `motion_history_timeout_s` (two seconds by default).
The current raw `distance_m` remains `null` in that case.

The output also includes
`direction/ttc_sec/risk_score/risk_level/alert_priority/alert_required/risk_reasons`. TTC is the
smoothed distance divided by the absolute value of valid negative speed. If depth is missing in
the current frame, TTC is empty and risk is unknown while motion history is retained. Only
high/critical labels append a concise TTC/level annotation; lower-risk labels retain the original
annotation. `risk_ms` is timed separately and excludes inference. The first full `predict` duration
includes Graph/predictor initialization and is not a steady-state FPS measurement.

## Checks and Result Boundaries

Run the CLI and protocol automation checks from `backend`:

```powershell
\.venv\Scripts\python.exe manage.py test model_tests
```

The checks require the project's configured `DJANGO_SECRET_KEY`; see the
[backend guide](../README.md#checks) for the full development checks. Generated JSON, annotated
images, and timing are saved under the selected `outputs/<run-name>/`. The repository does not
include machine-specific run artifacts. Existing output directories cannot be overwritten, and
failures and cancellations should remain in their corresponding reports.

Single images, static repeats, and protocol doubles check interfaces, ID lifecycle, and output
fields; they do not establish real-video speed, TTC, risk, or metric-distance accuracy. Formal
comparison requires matching ground truth, a fixed data split, and an evaluation protocol. See
[DATASETS.md](DATASETS.md) for methodology.
