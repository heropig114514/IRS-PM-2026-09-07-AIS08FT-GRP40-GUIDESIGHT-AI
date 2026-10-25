# SANPO diversity subset

## Responsibility and scope

`sanpo_subset.py` prepares an additional class-agnostic E dataset from the existing
official SANPO-Real discovery inventory. It does not train a model, modify the
Distance Head, overwrite previous datasets, or alter A/B/C inference.

The selected camera is `camera_head/left`. Labels retain the existing
`sanpo_visible_surface_median_m` definition and instance taxonomy. The extractor
uses finite positive metric depth inside each nonzero instance mask, resized by
nearest-neighbor sampling to the native depth grid. No distance quotas, new
distance cutoffs, synthetic examples, or instance oversampling are introduced.

## Artifacts and interfaces

All experiment reports live under
`model_tests/datasets/sanpo/expansion-diversity-01/`:

- `candidates.json`, `probe-summary.json`, and `contact-*.jpg`: candidate metadata
  and five widely spaced human-reference frames per session.
- `candidate-review.json` and `selection.json`: explicit decisions, official split
  origins, and conservative visual location groups.
- `*-download-manifest.json`, `new-files.json`, `logs/`, and `checksums.jsonl`:
  exact object whitelist, cumulative added payload, CLI logs, and remote-checksum
  validation. The ledger includes screening samples that were not finally selected.
- `summary.json` and `distance-distribution.csv`: decoded sample counts, distance
  bins, class counts, exclusions, and final split hashes.
- `preserved-sha256.json`: fingerprints of original configurations, manifests,
  split lists and existing KITTI/SANPO checkpoints.
- `config.sanpo-agnostic-diversity.json`: separate training configuration with the
  new processed path; existing architecture and hyperparameters are preserved.

Original cloud objects are added only where absent under `sanpo/raw/`; existing
files must have the expected length and remote checksum. No second copy of the
previously downloaded sessions is made. Camera descriptions and annotation
provenance are copied from the cached official metadata into the new report tree.

The compatible E manifests are separate:
`model_tests/datasets/sanpo/processed-agnostic-diversity-01/{train,val,test}.jsonl`.
They use the same loader contract and SHA256 guards as the previous processed set.

## Operation

Run from `backend` with `.venv-evaluation/Scripts/python.exe -X utf8`.
The module has explicit `screen`, `analyse`, and `build` phases. The initial
screen selects 40 candidates. This experiment additionally screened ten low-blur
urban sessions, with their reasons recorded in `candidates.json`, for 50 total.
Do not rerun screening over an existing reviewed list. `analyse` inspects the
fixed candidates; `build` consumes the reviewed selection and validates files.
Completed processed outputs are never overwritten.

Downloads use the installed `D:\gcloud cli\google-cloud-sdk\bin\gcloud.cmd`,
with explicit URLs, no-clobber behavior, and a cumulative 10 GB payload ceiling.
Free space must exceed the pending payload by at least 3 GB. No application-level
automatic retry is implemented. An interrupted transfer requires log review;
the size and checksum guards do not silently accept partial files.

Training is intentionally a separate user action. Use the new configuration
only after `status.json` reports `verified`, with a fresh experiment output path.
This preparation never executes `object_distance.train`.

## Sampling and limits

All downloaded selected frames are human-reference frames. Training uses a base
interval of 0.8 seconds and additionally retains intervening frames for previously
unseen 2-10 m instance IDs or near-distance changes of at least `max(0.75 m, 20%)`
since the last retained view. All valid objects in each retained frame, including
distant objects, remain supervised. Empty training frames are omitted. Validation and test retain all
human-reference frames, including empty frames; their natural distributions are
not balanced by distance. IDs are session-local annotation identifiers, not
verified physical-object identities. Remaining frames are still correlated video
observations, not independent photographs.

The public metadata inspected here has no GPS or explicit site identifiers.
Visual location groups use common landmarks and suspected overlapping routes.
They never cross the new splits, and official test sessions never enter training
or validation. This is not proof of strict geographic isolation; the report must
retain that limitation. A session is not automatically a distinct place.

The sparse selected frames are suitable for E training and frame-level evaluation.
They are **not a full-rate temporal benchmark for C**: no unannotated intermediate
RGB frames are downloaded in this compact expansion. A later C evaluation must
state its actual sampling frequency or separately provision continuous RGB.

## Validation

Unit checks cover the payload ceiling, preservation of existing raw files,
official-test isolation, and novelty-aware temporal selection. Actual preparation
also checks remote MD5, PNG decoding, calibration dimensions, mask/RGB agreement,
depth shape/aspect ratio, finite distances, and split manifest hashes. These checks
establish format integrity, not surveyed accuracy of CREstereo depth or pixel-level
registration accuracy for every object. Review previews alongside numeric reports.
