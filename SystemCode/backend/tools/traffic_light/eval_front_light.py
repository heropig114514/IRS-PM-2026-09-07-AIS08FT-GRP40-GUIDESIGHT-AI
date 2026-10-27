# ruff: noqa: E501, E731, B023  (offline Colab/Kaggle experiment script, not runtime code)
"""Evaluate detection of the front pedestrian light (the one the walker is about to cross towards).

Ground truth: traffic-light boxes (makesense id 10) in results/gt, the k-th one in each file is
light_id "<image>-<k>"; light_labeling/front_lights.csv marks which are the front pedestrian light
(front_ped = 1) and their colour. Only front lights count; every other traffic light is ignored.
Lights are tiny, so recall is reported at IoU >= 0.5 and >= 0.3.
- recall: a front light is found if any traffic-light prediction overlaps it.
- front pick: in images with a front light, did the prediction flagged front_light=True hit it?
Usage: python3 eval_front_light.py [--pred out/04-sg-roi]  (run from results/)
"""

import argparse
import csv
import json
import os


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0


ap = argparse.ArgumentParser()
ap.add_argument("--pred", default="out/01-sg-depth", help="run directory containing results.jsonl")
args = ap.parse_args()

labels = {
    r["light_id"]: r
    for r in csv.DictReader(open("light_labeling/front_lights.csv", encoding="utf-8-sig"))
}
front = {}  # image -> list of (light_id, box)
for lid, r in labels.items():
    if r["front_ped"] != "1":
        continue
    sid, k = lid.rsplit("-", 1)
    lights = [line.split() for line in open(f"gt/{sid}.txt") if line.split()[0] == "10"]
    _, x, y, w, h = map(float, lights[int(k) - 1])
    front.setdefault(sid, []).append((lid, [x - w / 2, y - h / 2, x + w / 2, y + h / 2]))

preds = {}
for line in open(f"{args.pred}/results.jsonl", encoding="utf-8"):
    r = json.loads(line)
    if r.get("repeat_index", 0):
        continue
    sid = os.path.basename(r["image"])[:-4]
    fw, fh = r["frame_size"]
    preds[sid] = [
        (
            b["confidence"],
            b.get("front_light", False),
            [b["bbox"][0] / fw, b["bbox"][1] / fh, b["bbox"][2] / fw, b["bbox"][3] / fh],
        )
        for b in r["metadata"]["boxes"]
        if b["class_name"] == "traffic light"
    ]

rows = []
for thr in (0.5, 0.3):
    found = picked = total = 0
    for sid, gts in front.items():
        p = preds.get(sid, [])
        for lid, gb in gts:
            total += 1
            hit = any(iou(pb, gb) >= thr for _, _, pb in p)
            found += hit
            if thr == 0.5:
                rows.append(
                    {
                        "light_id": lid,
                        "found": int(hit),
                        "color": labels[lid]["color"],
                        "best_iou": round(max((iou(pb, gb) for _, _, pb in p), default=0), 2),
                    }
                )
        flagged = [pb for _, f, pb in p if f]
        picked += any(iou(pb, gb) >= thr for pb in flagged for _, gb in gts)
    print(
        f"IoU>={thr}: front lights {total}, found {found}, recall {found / total:.2f}; "
        f"front_light pick correct in {picked}/{len(front)} images"
    )
with open(f"front_light_{os.path.basename(args.pred.rstrip('/'))}.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
