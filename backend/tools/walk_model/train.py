# ruff: noqa: E501, E731, B023  (offline Kaggle experiment script, not runtime code)
"""Fine-tune the whole yolo26n on the 87-class dataset built by prepare_data.py (two rounds).

Round 1: start from the head-expanded COCO checkpoint (expand_head.py: the 80 COCO outputs are
         exactly the original ones at step 0) and train on ground truth + teacher fills.
Round 2: the round-1 model labels the obstacle classes (pole, bollard, traffic_cone, stairs) on
         images whose source does not annotate them (COCO, ExDark, ImVisible, pedlight, and
         bollard / cone across OOD / Mendeley), conf >= 0.5, not overlapping existing boxes; then
         train again from the round-1 weights. Pedestrian-light colours are NOT pseudo-labelled
         (a wrong "green" label is a safety risk).
v2 (anti-forgetting): backbone frozen (freeze=10) + Ultralytics feature distillation from the original
yolo26n.pt (kept as teacher in both rounds, so COCO behaviour is anchored to the original model).
Optimiser: SGD, lr0 0.002 (round 2: 0.001), 1 warm-up epoch; Ultralytics' auto choice would use
0.01, too high for already-converged COCO weights (risk of losing COCO accuracy).
Augmentation: no hue jitter (hsv_h = 0) because light colour is the label.
Training time is capped with Ultralytics' `time` (hours), so a Kaggle 12-hour session never dies
mid-run; checkpoints go to $FT_OUT (default /kaggle/working/ft) so they survive.
Usage: python train.py --round 1 --hours 4.5 ; python train.py --round 2 --hours 3.5
"""

import argparse
import json
import os
import shutil
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prepare_data as P  # noqa: E402
from expand_head import expand, verify  # noqa: E402

HALF = __import__("torch").cuda.is_available()
OUT = os.environ.get("FT_OUT", "/kaggle/working/ft")
ANNOTATED_NEW = {
    "mendeley": {"pole", "traffic_cone", "stairs"},
    "ood": {"bollard", "stairs"},
    "pedlight": set(),
    "imvisible": set(),
    "exdark": set(),
    "coco": set(),
}


def fit(init, name, hours, batch, device, lr0, freeze=10, distill="yolo26n.pt"):
    from ultralytics import YOLO

    model = YOLO(init)
    model.train(
        data=f"{P.DS}/data.yaml",
        imgsz=640,
        time=hours,
        epochs=100,
        batch=batch,
        device=device,
        workers=4,
        cos_lr=True,
        close_mosaic=5,
        hsv_h=0.0,
        patience=100,
        plots=True,
        amp=True,
        optimizer="SGD",
        lr0=lr0,
        warmup_epochs=1,  # gentle: the COCO weights are already converged
        freeze=freeze,  # backbone (layers 0-9) frozen: COCO features stay as they are
        distill_model=distill or None,  # neck features pulled toward the ORIGINAL COCO yolo26n
        project=OUT,
        name=name,
        exist_ok=True,
        seed=0,
    )
    return f"{OUT}/{name}/weights/best.pt"


def pseudo_label(weights):
    from ultralytics import YOLO

    recs, teacher = P.load_records()
    model = YOLO(weights)
    extra, added = {}, Counter()
    todo = [r for r in recs if r["split"] in ("train", "val")]
    for i in range(0, len(todo), 64):
        chunk = todo[i : i + 64]
        for r, res in zip(
            chunk,
            model.predict(
                [x["image"] for x in chunk], imgsz=640, conf=0.5, half=HALF, verbose=False, batch=32
            ),
            strict=False,
        ):
            want = {
                P.CID[n]
                for n in ("pole", "bollard", "traffic_cone", "stairs")
                if n not in ANNOTATED_NEW[r["src"]]
            }
            boxes = [
                [int(c)] + [float(v) for v in b]
                for c, b in zip(res.boxes.cls, res.boxes.xyxyn, strict=False)
                if int(c) in want
            ]
            if boxes:
                extra[r["stem"]] = boxes
                added.update(f"{r['src']}:{P.NAMES[b[0]]}" for b in boxes)
    P.write_labels(recs, teacher, extra)
    for cache in ("train", "val"):  # labels changed: drop Ultralytics label caches
        p = f"{P.DS}/labels/{cache}.cache"
        if os.path.exists(p):
            os.remove(p)
    print("round-2 pseudo boxes:", dict(sorted(added.items())))
    json.dump(dict(added), open(f"{OUT}/pseudo_counts.json", "w"), indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--hours", type=float, default=4.5)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--device", default="0,1")
    a = ap.parse_args()
    device = [int(d) for d in a.device.split(",")] if "," in a.device else a.device
    os.makedirs(OUT, exist_ok=True)
    if a.round == 1:
        import torch

        old, new = expand()
        verify(old, new)
        init = f"{OUT}/yolo26n-87init.pt"
        torch.save(
            {"model": new.half(), "train_args": {}, "epoch": -1, "date": None, "version": None},
            init,
        )
        best = fit(init, "r1", a.hours, a.batch, device, lr0=0.002)
    else:
        r1 = f"{OUT}/r1/weights/best.pt"
        pseudo_label(r1)
        best = fit(r1, "r2", a.hours, a.batch, device, lr0=0.001)
    shutil.copy(best, f"{OUT}/yolo26n-walk-r{a.round}.pt")
    print("best weights:", f"{OUT}/yolo26n-walk-r{a.round}.pt")


if __name__ == "__main__":
    main()
