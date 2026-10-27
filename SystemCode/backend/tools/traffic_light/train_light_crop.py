# ruff: noqa: E501, E731, B023  (offline Colab/Kaggle experiment script, not runtime code)
"""Train a traffic-light CROP colour classifier (red / green / off) from auto-labelled ImVisible crops.

Why crops: the whole-image classifier learned "people crossing => green". A classifier that only
sees the light itself cannot use that shortcut.

Auto-labelling (no manual boxes needed), per ImVisible image whose main light is red or green:
  1. run the project detector (640 full frame + centre ROI, traffic light conf 0.12) on the image;
  2. pick the traffic-light box closest (horizontally) to the far end of the zebra-crossing
     midline (x1, y1), preferring boxes above that point;
  3. label = the image's light state; drop the crop if a simple HSV check clearly says the
     opposite colour (then the picked box was probably a vehicle light of the other colour).
  Countdown-blank images (mode 3) give "off" crops; images without a light give no crops.
Model: ImageNet MobileNetV3-Small on 96x96 crops (box expanded 25%, letterboxed). No hue jitter.
Safety rule: say green only if p(green) >= tau; tau = smallest value with red->green <= 0.5% on
the validation crops (never tuned on test). Single-frame here (min_frames = 1); the 3-frame rule
needs video and is tested later.
Tests: ImVisible test images (same picking rule; no detection => unknown), and the 31 Singapore
front pedestrian lights (crop from the matched detection, and from the hand-labelled box).
Usage (Colab, backend installed): python train_light_crop.py -> /content/light-crop-results.zip
"""

import csv
import glob
import json
import os
import subprocess
import sys
import time
import zipfile
from collections import Counter

import torch
import torch.nn as nn
from PIL import Image, ImageDraw, ImageOps
from torch.utils.data import DataLoader, Dataset
from torchvision import models, transforms

IMV = "/content/imv"
BACKEND = "/content/work/backend"
OUT = "/content/out"
CLASSES = ["red", "green", "off"]
MODE_TO_CLASS = {0: 0, 1: 1, 2: 1, 3: 2}
CROP = 96
torch.manual_seed(0)


def sh(cmd):
    print("$", cmd, flush=True)
    subprocess.run(cmd, shell=True, check=True)


# ---------------------------------------------------------------- data + detections
def ensure_data():
    os.makedirs(IMV, exist_ok=True)
    for split in ("training", "validation", "testing"):
        p = f"{IMV}/{split}_file.csv"
        if not os.path.exists(p):
            sh(
                f"wget -q -O {p} https://raw.githubusercontent.com/samuelyu2002/ImVisible/master/Annotations/{split}_file.csv"
            )
    if not glob.glob(f"{IMV}/raw/**/*.*", recursive=True):
        sh(f"{sys.executable} -m pip install -q gdown")
        sh(
            f"cd {IMV} && gdown -q --fuzzy 'https://drive.google.com/file/d/117HUGczZwXFRbuBl7y4cHakH3xQV2Omo/view' -O imv768.zip"
        )
        sh(f"cd {IMV} && mkdir -p raw && unzip -q -o imv768.zip -d raw")
    files = {}
    for dirpath, _, names in os.walk(f"{IMV}/raw"):
        for n in names:
            files[n.lower()] = os.path.join(dirpath, n)
    return files


def detect(split, rows, files):
    """Run the project detector (ROI on) over one split; returns {stem: (frame_w, frame_h, light boxes)}."""
    run = f"{OUT}/crop-{split}"
    src = f"{IMV}/img-{split}"
    if not os.path.exists(f"{run}/results.jsonl"):
        os.makedirs(src, exist_ok=True)
        for r in rows:
            f = files.get(r["file"].lower())
            dst = f"{src}/{os.path.splitext(r['file'])[0]}.jpg"
            if f and not os.path.exists(dst):
                Image.open(f).convert("RGB").save(dst, quality=95)
        sh(
            f"cd {BACKEND} && {sys.executable} -X utf8 -m model_tests.run_depth --input {src} --output {run} "
            f"--roi --max-edge 1280 --label crop-{split} 2>&1 | tail -1"
        )
    det = {}
    for line in open(f"{run}/results.jsonl", encoding="utf-8"):
        r = json.loads(line)
        fw, fh = r["frame_size"]
        det[os.path.basename(r["image"])[:-4]] = (
            fw,
            fh,
            [b for b in r["metadata"]["boxes"] if b["class_name"] == "traffic light"],
        )
    return src, det


def pick(boxes, fx, fy):
    """Traffic light nearest (horizontally) to the far end of the crosswalk, preferring lights above it."""
    above = [b for b in boxes if (b["bbox"][1] + b["bbox"][3]) / 2 < fy] or boxes
    return min(above, key=lambda b: abs((b["bbox"][0] + b["bbox"][2]) / 2 - fx)) if above else None


def hsv_colour(img, box):
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    crop = img.crop((max(0, x0), max(0, y0), max(x0 + 1, x1), max(y0 + 1, y1))).convert("HSV")
    red = green = 0
    for h, s, v in crop.getdata():
        if s < 90 or v < 120:
            continue
        if h < 15 or h > 235:
            red += 1
        elif 70 <= h <= 150:
            green += 1
    if red >= 3 and red >= 2 * green:
        return "red"
    if green >= 3 and green >= 2 * red:
        return "green"
    return "unknown"


def crop_box(img, box, pad=0.25):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    c = img.crop((x0 - pad * w, y0 - pad * h, x1 + pad * w, y1 + pad * h))
    return ImageOps.pad(c, (CROP, CROP), color=(0, 0, 0))


def build_crops(split, rows, files, keep_conflicts=False):
    """Returns list of (crop image, class index, stem, picked flag) and stats."""
    src, det = detect(split, rows, files)
    items, stats = [], Counter()
    for r in rows:
        stem = os.path.splitext(r["file"])[0]
        mode = int(r["mode"])
        if mode not in MODE_TO_CLASS or stem not in det:
            continue
        fw, fh, boxes = det[stem]
        b = pick(boxes, float(r["x1"]) * fw / 4032, float(r["y1"]) * fh / 3024)
        if b is None:
            stats["no_detection"] += 1
            continue
        img = Image.open(f"{src}/{stem}.jpg").convert("RGB")
        sx, sy = img.width / fw, img.height / fh
        box = [b["bbox"][0] * sx, b["bbox"][1] * sy, b["bbox"][2] * sx, b["bbox"][3] * sy]
        label = MODE_TO_CLASS[mode]
        hsv = hsv_colour(img, box)
        conflict = (label == 0 and hsv == "green") or (label == 1 and hsv == "red")
        stats["conflict" if conflict else "kept"] += 1
        if conflict and not keep_conflicts:
            continue
        items.append((crop_box(img, box), label, stem))
    print(
        f"{split}: {dict(stats)} -> {len(items)} crops {dict(Counter(CLASSES[c] for _, c, _ in items))}",
        flush=True,
    )
    return items, stats


class Crops(Dataset):
    def __init__(self, items, train):
        self.items = items
        aug = [
            transforms.RandomAffine(degrees=5, translate=(0.08, 0.08), scale=(0.85, 1.15)),
            transforms.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.15, hue=0),
        ]
        self.tf = transforms.Compose(
            (aug if train else [])
            + [
                transforms.ToTensor(),
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
            ]
        )

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        return self.tf(self.items[i][0]), self.items[i][1]


@torch.no_grad()
def probs(model, imgs):
    model.eval()
    tf = Crops([], False).tf
    out = []
    for i in range(0, len(imgs), 256):
        x = torch.stack([tf(im) for im in imgs[i : i + 256]]).cuda()
        out.append(model(x).float().softmax(1).cpu())
    return torch.cat(out) if out else torch.zeros(0, len(CLASSES))


def decide(p, tau):
    if p[1] >= tau:
        return "green"
    return "red" if p.argmax().item() == 0 else "unknown"


def main():
    files = ensure_data()
    rows = {
        s: list(csv.DictReader(open(f"{IMV}/{s}_file.csv", encoding="utf-8-sig")))
        for s in ("training", "validation", "testing")
    }
    tr, _ = build_crops("training", rows["training"], files)
    va, _ = build_crops("validation", rows["validation"], files)

    model = models.mobilenet_v3_small(weights=models.MobileNet_V3_Small_Weights.IMAGENET1K_V1)
    model.classifier[-1] = nn.Linear(model.classifier[-1].in_features, len(CLASSES))
    model = model.cuda()
    counts = Counter(c for _, c, _ in tr)
    weight = torch.tensor(
        [len(tr) / (len(CLASSES) * max(counts[i], 1)) for i in range(len(CLASSES))]
    ).cuda()
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    epochs = 25
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, epochs)
    loader = DataLoader(
        Crops(tr, True), batch_size=128, shuffle=True, num_workers=2, drop_last=True
    )
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
        vp = probs(model, [c for c, _, _ in va])
        vl = torch.tensor([y for _, y, _ in va])
        acc = (vp.argmax(1) == vl).float().mean().item()
        print(
            f"epoch {ep + 1}/{epochs} loss {tot / len(loader):.3f} val_acc {acc:.3f} ({time.time() - t0:.0f}s)",
            flush=True,
        )
        if acc > best:
            best = acc
            torch.save(model.state_dict(), "/content/light_crop.pt")
    model.load_state_dict(torch.load("/content/light_crop.pt"))

    # calibrate tau on validation crops: red -> green <= 0.5 %
    vp = probs(model, [c for c, _, _ in va])
    vl = torch.tensor([y for _, y, _ in va])
    red_pg = sorted(vp[vl == 0][:, 1].tolist(), reverse=True)
    allowed = int(0.005 * len(red_pg))
    tau = max(0.5, red_pg[allowed] + 1e-4) if len(red_pg) > allowed else 0.5
    results = {"tau": tau, "val_top1": (vp.argmax(1) == vl).float().mean().item()}
    print(f"\ntau = {tau:.3f} (validation top-1 {results['val_top1']:.3f})")

    def summarise(name, pairs):
        """pairs: list of (truth in red/green, decision)"""
        c = Counter(pairs)
        nr = sum(v for (t, _), v in c.items() if t == "red")
        ng = sum(v for (t, _), v in c.items() if t == "green")
        r = {
            "red_n": nr,
            "red_said_green": c[("red", "green")],
            "red_said_red": c[("red", "red")],
            "green_n": ng,
            "green_said_green": c[("green", "green")],
            "green_said_red": c[("green", "red")],
            "unknown": c[("red", "unknown")] + c[("green", "unknown")],
        }
        results[name] = r
        print(
            f"[{name}] red {nr}: said green {r['red_said_green']}, said red {r['red_said_red']} | "
            f"green {ng}: said green {r['green_said_green']} ({r['green_said_green'] / max(ng, 1):.2f}), "
            f"said red {r['green_said_red']} | unknown {r['unknown']}"
        )

    # validation (crop level)
    summarise(
        "validation_crops",
        [
            (CLASSES[t], decide(p, tau))
            for t, p in zip(vl.tolist(), vp, strict=False)
            if CLASSES[t] in ("red", "green")
        ],
    )

    # ImVisible test, image level: every red/green image counts; no detection => unknown
    te, _ = build_crops("testing", rows["testing"], files, keep_conflicts=True)
    tp = probs(model, [c for c, _, _ in te])
    dec = {stem: decide(p, tau) for (_, _, stem), p in zip(te, tp, strict=False)}
    pairs = []
    for r in rows["testing"]:
        m = int(r["mode"])
        if m in (0, 1, 2):
            pairs.append(
                ("red" if m == 0 else "green", dec.get(os.path.splitext(r["file"])[0], "unknown"))
            )
    summarise("imvisible_test_images", pairs)

    # Singapore front pedestrian lights (31): crop from matched detection and from the labelled box
    sg_rows = []
    if os.path.exists("/content/sg_front_lights.json") and os.path.isdir("/content/Photos-clean"):
        sg = json.load(open("/content/sg_front_lights.json"))
        for kind in ("det_box", "gt_box"):
            pairs, imgs, idx = [], [], []
            for i, s in enumerate(sg):
                if s["color"] not in ("red", "green"):
                    continue
                if s[kind] is None:
                    pairs.append((s["color"], "unknown"))
                    continue
                imgs.append(
                    crop_box(
                        Image.open(f"/content/Photos-clean/{s['image']}.jpg").convert("RGB"),
                        s[kind],
                    )
                )
                idx.append(i)
            for i, p in zip(idx, probs(model, imgs), strict=False):
                d = decide(p, tau)
                pairs.append((sg[i]["color"], d))
                sg_rows.append(
                    {
                        "kind": kind,
                        "light_id": sg[i]["light_id"],
                        "truth": sg[i]["color"],
                        "decision": d,
                        **{f"p_{c}": round(v, 3) for c, v in zip(CLASSES, p.tolist(), strict=False)},
                    }
                )
            summarise(f"singapore_{kind}", pairs)
        sheet = Image.new("RGB", (8 * (CROP + 4), ((len(sg_rows) + 7) // 8) * (CROP + 18)), "white")
        d = ImageDraw.Draw(sheet)
        crops_by = {}
        for s in sg:
            if s["gt_box"]:
                crops_by[s["light_id"]] = crop_box(
                    Image.open(f"/content/Photos-clean/{s['image']}.jpg").convert("RGB"),
                    s["gt_box"],
                )
        for i, row in enumerate([r for r in sg_rows if r["kind"] == "gt_box"]):
            x, y = (i % 8) * (CROP + 4), (i // 8) * (CROP + 18)
            sheet.paste(crops_by[row["light_id"]], (x, y))
            d.text(
                (x, y + CROP + 2),
                f"{row['truth'][0]}->{row['decision'][:3]} {row['p_green']:.2f}",
                fill="black",
            )
        sheet.save("/content/sg_crops.jpg")
        with open("/content/sg_light_crop.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(sg_rows[0]))
            w.writeheader()
            w.writerows(sg_rows)

    json.dump(results, open("/content/light_crop_results.json", "w"), indent=1)
    with zipfile.ZipFile("/content/light-crop-results.zip", "w") as z:
        for f in ("light_crop.pt", "light_crop_results.json", "sg_light_crop.csv", "sg_crops.jpg"):
            if os.path.exists(f"/content/{f}"):
                z.write(f"/content/{f}", f)
    print("\nwrote /content/light-crop-results.zip")


if __name__ == "__main__":
    main()
