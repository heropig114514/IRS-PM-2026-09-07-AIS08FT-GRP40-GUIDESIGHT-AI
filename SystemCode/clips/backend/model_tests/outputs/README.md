# Test Outputs

Specify a new subdirectory for each run, such as `outputs/bus-run-3` or `outputs/photos-run-1`. It contains `run.json`, `results.jsonl`, and `annotations/`; generated results are not committed to Git. All files previously under `SystemCode/clips/backend/test-results/depth/` were moved here byte-for-byte, including `bus-run-1` and `bus-run-2`. Historical results retain their original absolute input paths; new runs record their current sample paths.

In `run.json`, `complete` indicates success and `failed` preserves the error code and partial results. The interrupted historical `bus-run-1` remains in its original `running` state with empty results; it was not rewritten as successful.

Existing directories are never overwritten, even after failure or interruption. Choose a new run name. See [README.md](../README.md) for details.
