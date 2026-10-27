# ruff: noqa: E501, E731, B023  (offline Colab/Kaggle experiment script, not runtime code)
"""Evaluate YOLO26n detections (results/out/01-sg-depth) against makesense YOLO-format ground truth (results/gt).

Matching: per image and class, predictions sorted by confidence, greedy one-to-one at IoU >= 0.5.
Ground-truth class ids follow the makesense export (verified visually): 0 person, 1 bicycle,
2 motorcycle, 3 car, 4 bus, 5 truck, 10 traffic light. Id 11 (one bollard pre-labelled as a
COCO class) and bollard/barrier/pma are not COCO targets and are excluded.
Near-only mode (--near D): ground truth farther than D metres is "ignore" -- missing it is not a
FN and a prediction on it is not a FP (like COCO crowd regions). Distance comes from the pinhole
model: an object of real height H is at most D metres away when its box height >= h_min = f*H/D,
with f = long image edge / (2*tan(HFOV/2)) in pixels of the cropped photo (HFOV assumed, --hfov).
Traffic lights are not filtered by size (the light across the road is far but needed); they are
reported as-is until front pedestrian-light labels exist.
Usage: python3 eval_detection.py [--pred out/04-sg-roi] [--near 10] [--hfov 70]  (run from results/)
"""

import argparse
import csv
import glob
import json
import math
import os
from collections import defaultdict

GT_CLASSES = {
    0: "person",
    1: "bicycle",
    2: "motorcycle",
    3: "car",
    4: "bus",
    5: "truck",
    10: "traffic light",
}
EVAL = list(GT_CLASSES.values())
IOU = 0.5
SMALL_PX = 32  # GT box height in the 640-edge frame below this counts as "small"
REAL_HEIGHT_M = {
    "person": 1.7,
    "bicycle": 1.1,
    "motorcycle": 1.1,
    "car": 1.5,
    "bus": 3.0,
    "truck": 3.0,
}

ap = argparse.ArgumentParser()
ap.add_argument("--near", type=float, help="ignore objects farther than this many metres")
ap.add_argument("--pred", default="out/01-sg-depth", help="run directory containing results.jsonl")
ap.add_argument(
    "--hfov",
    type=float,
    default=70.0,
    help="assumed camera field of view on the long edge, degrees",
)
args = ap.parse_args()
run_name = os.path.basename(args.pred.rstrip("/"))
suffix = ("" if run_name == "01-sg-depth" else f"_{run_name}") + (
    f"_near{args.near:g}m" if args.near else ""
)


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0


meta = {m["id"]: m for m in csv.DictReader(open("../Photos-clean/metadata.csv"))}
preds, frames = {}, {}
for line in open(f"{args.pred}/results.jsonl", encoding="utf-8"):
    r = json.loads(line)
    sid = os.path.basename(r["image"])[:-4]
    fw, fh = r["frame_size"]
    frames[sid] = (fw, fh)
    preds[sid] = [
        (
            b["class_name"],
            b["confidence"],
            [b["bbox"][0] / fw, b["bbox"][1] / fh, b["bbox"][2] / fw, b["bbox"][3] / fh],
        )
        for b in r["metadata"]["boxes"]
    ]

gts = {}
for f in sorted(glob.glob("gt/*.txt")):
    sid = os.path.basename(f)[:-4]
    boxes = []
    for line in open(f):
        c, x, y, w, h = line.split()
        if int(c) in GT_CLASSES:
            x, y, w, h = map(float, (x, y, w, h))
            boxes.append((GT_CLASSES[int(c)], [x - w / 2, y - h / 2, x + w / 2, y + h / 2]))
    gts[sid] = boxes


def h_min(sid, cls):
    """Minimum normalised box height for an object of class cls to be within --near metres."""
    if not args.near or cls not in REAL_HEIGHT_M:
        return 0.0
    w, h = int(meta[sid]["width"]), int(meta[sid]["height"])
    f = max(w, h) / (2 * math.tan(math.radians(args.hfov) / 2))
    return f * REAL_HEIGHT_M[cls] / args.near / h


images = sorted(set(gts) & set(preds))
ignored = defaultdict(int)
stats = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})  # key: (group, class)
misses, confusions, false_pos = [], defaultdict(int), []
for sid in images:
    light = meta[sid]["lighting"]
    fh = frames[sid][1]
    for cls in EVAL:
        hm = h_min(sid, cls)
        g_all = [b for c, b in gts[sid] if c == cls]
        g = [b for b in g_all if b[3] - b[1] >= hm]
        far = [b for b in g_all if b[3] - b[1] < hm]
        ignored[cls] += len(far)
        p = sorted([(conf, b) for c, conf, b in preds[sid] if c == cls], key=lambda t: -t[0])
        used = set()
        for conf, pb in p:
            best, bi = 0, None
            for i, gb in enumerate(g):
                if i not in used and (v := iou(pb, gb)) > best:
                    best, bi = v, i
            if best >= IOU:
                used.add(bi)
                outcome = "tp"
            elif pb[3] - pb[1] < hm or any(iou(pb, fb) >= IOU for fb in far):
                continue  # prediction on a far object: ignored, neither TP nor FP
            else:
                outcome = "fp"
                false_pos.append((sid, cls, round(conf, 2)))
            for key in (("all", cls), (light, cls), ("all", "*"), (light, "*")):
                stats[key][outcome] += 1
        for i, gb in enumerate(g):
            if i in used:
                size = "small" if (gb[3] - gb[1]) * fh < SMALL_PX else "normal"
                for key in ((size, cls), (size, "*")):
                    stats[key]["tp"] += 1
                continue
            size = "small" if (gb[3] - gb[1]) * fh < SMALL_PX else "normal"
            for key in (
                ("all", cls),
                (light, cls),
                ("all", "*"),
                (light, "*"),
                (size, cls),
                (size, "*"),
            ):
                stats[key]["fn"] += 1
            # Was the missed object detected under another class?
            other = max(
                ((iou(pb, gb), c) for c, _, pb in preds[sid] if c != cls), default=(0, None)
            )
            if other[0] >= IOU:
                confusions[(cls, other[1])] += 1
            misses.append((sid, cls, size, light))


def pr(s):
    tp, fp, fn = s["tp"], s["fp"], s["fn"]
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p and r else None
    return tp, fp, fn, p, r, f1


fmt = lambda v: "-" if v is None else f"{v:.2f}"
rows = []
print(
    f"images evaluated: {len(images)}  (gt files {len(gts)}, missing gt: {sorted(set(preds) - set(gts))})"
)
if args.near:
    print(
        f"near-only: D={args.near:g} m, HFOV={args.hfov:g} deg; far GT ignored per class: {dict(ignored)}"
    )
print(f"\n{'class':14} {'GT':>4} {'TP':>4} {'FP':>4} {'FN':>4} {'P':>5} {'R':>5} {'F1':>5}")
for cls in EVAL + ["*"]:
    tp, fp, fn, p, r, f1 = pr(stats[("all", cls)])
    name = "ALL (micro)" if cls == "*" else cls
    print(f"{name:14} {tp + fn:4} {tp:4} {fp:4} {fn:4} {fmt(p):>5} {fmt(r):>5} {fmt(f1):>5}")
    rows.append(
        {
            "group": "all",
            "class": name,
            "gt": tp + fn,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": fmt(p),
            "recall": fmt(r),
            "f1": fmt(f1),
        }
    )
for group in ("day", "dusk", "night", "normal", "small"):
    print(f"\n[{group}]")
    for cls in ["person", "car", "motorcycle", "bicycle", "*"]:
        s = stats[(group, cls)]
        tp, fp, fn, p, r, f1 = pr(s)
        if tp + fn == 0:
            continue
        name = "ALL (micro)" if cls == "*" else cls
        extra = "" if group in ("normal", "small") else f" P={fmt(p)}"
        print(f"  {name:14} GT={tp + fn:4} R={fmt(r)}{extra}")
        rows.append(
            {
                "group": group,
                "class": name,
                "gt": tp + fn,
                "tp": tp,
                "fp": "" if group in ("normal", "small") else fp,
                "fn": fn,
                "precision": "" if group in ("normal", "small") else fmt(p),
                "recall": fmt(r),
                "f1": "",
            }
        )
print("\nmissed objects detected as another class (IoU>=0.5):", dict(confusions))
with open(f"detection_metrics{suffix}.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
with open(f"detection_misses{suffix}.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["id", "class", "size", "lighting"])
    w.writerows(misses)
