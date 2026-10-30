# ruff: noqa: E501, E731, B023  (offline Colab/Kaggle experiment script, not runtime code)
"""Light-crop classifier, round 2: tau trade-off curves (1) and cleaner auto-labels (2).

(1) For the round-1 model (v1) and the new model (v2), print red->green and green->green
    rates at several fixed tau values, on validation crops and on ImVisible test images.
(2) v2 is trained on CLEANER crops: a red/green crop is kept only if the HSV check AGREES with
    the image label (v1 kept "agrees or unknown"); an "off" crop is kept only if HSV sees no
    lit red/green. Fewer but cleaner samples.
Reuses detection, picking, HSV and cropping from train_light_crop.py (same folder).
Usage: python train_light_crop_v2.py [--v1 /content/v1/light_crop.pt]
"""

import argparse
import csv
import json
import os
import sys
import time
import zipfile
from collections import Counter

import torch
import torch.nn as nn
from PIL import Image
from torch.utils.data import DataLoader

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import train_light_crop as T  # noqa: E402

TAUS = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95]


def crops_with_hsv(split, rows, files):
    """All picked crops of a split with label and HSV verdict (no filtering)."""
    src, det = T.detect(split, rows, files)
    out = []
    for r in rows:
        stem = os.path.splitext(r["file"])[0]
        mode = int(r["mode"])
        if mode not in T.MODE_TO_CLASS or stem not in det:
            continue
        fw, fh, boxes = det[stem]
        b = T.pick(boxes, float(r["x1"]) * fw / 4032, float(r["y1"]) * fh / 3024)
        if b is None:
            continue
        img = Image.open(f"{src}/{stem}.jpg").convert("RGB")
        sx, sy = img.width / fw, img.height / fh
        box = [b["bbox"][0] * sx, b["bbox"][1] * sy, b["bbox"][2] * sx, b["bbox"][3] * sy]
        out.append((T.crop_box(img, box), T.MODE_TO_CLASS[mode], stem, T.hsv_colour(img, box)))
    return out


def clean(items):
    keep = []
    for c, y, stem, hsv in items:
        if (
            (y == 0 and hsv == "red")
            or (y == 1 and hsv == "green")
            or (y == 2 and hsv == "unknown")
        ):
            keep.append((c, y, stem))
    return keep


def train(tr, va, path, epochs=25):
    model = T.models.mobilenet_v3_small(weights=T.models.MobileNet_V3_Small_Weights.IMAGENET1K_V1)
    model.classifier[-1] = nn.Linear(model.classifier[-1].in_features, len(T.CLASSES))
    model = model.cuda()
    counts = Counter(c for _, c, _ in tr)
    weight = torch.tensor(
        [len(tr) / (len(T.CLASSES) * max(counts[i], 1)) for i in range(len(T.CLASSES))]
    ).cuda()
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, epochs)
    loader = DataLoader(
        T.Crops(tr, True), batch_size=128, shuffle=True, num_workers=2, drop_last=True
    )
    vl = torch.tensor([y for _, y, _ in va])
    best = 0
    for ep in range(epochs):
        model.train()
        t0, tot = time.time(), 0.0
        for x, y in loader:
            loss = nn.functional.cross_entropy(model(x.cuda()), y.cuda(), weight=weight)
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += loss.item()
        sched.step()
        acc = (T.probs(model, [c for c, _, _ in va]).argmax(1) == vl).float().mean().item()
        print(
            f"  epoch {ep + 1}/{epochs} loss {tot / len(loader):.3f} val_acc {acc:.3f} ({time.time() - t0:.0f}s)",
            flush=True,
        )
        if acc > best:
            best = acc
            torch.save(model.state_dict(), path)
    model.load_state_dict(torch.load(path))
    return model


def load(path):
    model = T.models.mobilenet_v3_small()
    model.classifier[-1] = nn.Linear(model.classifier[-1].in_features, len(T.CLASSES))
    model.load_state_dict(torch.load(path, map_location="cpu"))
    return model.cuda().eval()


def auto_tau(model, va):
    vp = T.probs(model, [c for c, _, _ in va])
    vl = torch.tensor([y for _, y, _ in va])
    red = sorted(vp[vl == 0][:, 1].tolist(), reverse=True)
    k = int(0.005 * len(red))
    return max(0.5, red[k] + 1e-4) if len(red) > k else 0.5


def curve(name, model, va, test_items, test_rows):
    """Rates at fixed tau on validation crops and on ImVisible test images (no detection => unknown)."""
    vp = T.probs(model, [c for c, _, _ in va])
    vl = [y for _, y, _ in va]
    tp = T.probs(model, [c for c, _, _, _ in test_items])
    by_stem = {stem: p for (_, _, stem, _), p in zip(test_items, tp, strict=False)}
    truth = {
        os.path.splitext(r["file"])[0]: ("red" if int(r["mode"]) == 0 else "green")
        for r in test_rows
        if int(r["mode"]) in (0, 1, 2)
    }
    rows = []
    print(
        f"\n[{name}] tau | val: red->green  green->green | test images: red->green  green->green  unknown"
    )
    for tau in TAUS + [auto_tau(model, va)]:
        vd = [T.decide(p, tau) for p in vp]
        vr = [d for d, y in zip(vd, vl, strict=False) if y == 0]
        vg = [d for d, y in zip(vd, vl, strict=False) if y == 1]
        td = {s: (T.decide(by_stem[s], tau) if s in by_stem else "unknown") for s in truth}
        tr_ = [d for s, d in td.items() if truth[s] == "red"]
        tg = [d for s, d in td.items() if truth[s] == "green"]
        r = {
            "model": name,
            "tau": round(tau, 3),
            "val_red_to_green": f"{vr.count('green')}/{len(vr)}",
            "val_green_said_green": round(vg.count("green") / len(vg), 3),
            "test_red_to_green": f"{tr_.count('green')}/{len(tr_)}",
            "test_green_said_green": round(tg.count("green") / len(tg), 3),
            "test_unknown": tr_.count("unknown") + tg.count("unknown"),
        }
        rows.append(r)
        print(
            f"  {tau:.3f} | {r['val_red_to_green']:>7}  {r['val_green_said_green']:.2f} | "
            f"{r['test_red_to_green']:>7}  {r['test_green_said_green']:.2f}  {r['test_unknown']}"
        )
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", default="/content/v1/light_crop.pt")
    args = ap.parse_args()
    files = T.ensure_data()
    rows = {
        s: list(csv.DictReader(open(f"{T.IMV}/{s}_file.csv", encoding="utf-8-sig")))
        for s in ("training", "validation", "testing")
    }
    tr_all = crops_with_hsv("training", rows["training"], files)
    va_all = crops_with_hsv("validation", rows["validation"], files)
    te_all = crops_with_hsv("testing", rows["testing"], files)
    for name, items in (("training", tr_all), ("validation", va_all)):
        print(
            f"{name}: all {len(items)}  hsv agreement by label:",
            dict(Counter((T.CLASSES[y], h) for _, y, _, h in items)),
        )

    # v1 validation set = round-1 rule (drop only clear conflicts)
    va_v1 = [
        (c, y, s)
        for c, y, s, h in va_all
        if not ((y == 0 and h == "green") or (y == 1 and h == "red"))
    ]
    tr_v2, va_v2 = clean(tr_all), clean(va_all)
    print(
        f"v2 clean crops: training {len(tr_v2)} {dict(Counter(T.CLASSES[y] for _, y, _ in tr_v2))} | "
        f"validation {len(va_v2)}",
        flush=True,
    )

    results = []
    if os.path.exists(args.v1):
        results += curve("v1", load(args.v1), va_v1, te_all, rows["testing"])
    print("\ntraining v2 on clean crops", flush=True)
    v2 = train(tr_v2, va_v2, "/content/light_crop_v2.pt")
    results += curve("v2", v2, va_v2, te_all, rows["testing"])

    with open("/content/light_crop_curves.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0]))
        w.writeheader()
        w.writerows(results)
    with zipfile.ZipFile("/content/light-crop-v2-results.zip", "w") as z:
        for f in ("light_crop_v2.pt", "light_crop_curves.csv", "light_crop_bench.txt"):
            if os.path.exists(f"/content/{f}"):
                z.write(f"/content/{f}", f)
    json.dump(results, open("/content/light_crop_curves.json", "w"), indent=1)
    print("\nwrote /content/light-crop-v2-results.zip")


if __name__ == "__main__":
    main()
