# ruff: noqa: E501, E731, B023  (offline Colab/Kaggle experiment script, not runtime code)
"""Follow-up checks for the light-state classifier (no retraining).

1. Show red images predicted green (val + test) as an image grid, to see whether they are label
   errors or model errors.
2. Re-calibrate the green threshold tau on the VALIDATION split: smallest tau with
   red->green <= 0.5% of red val images (the old rule capped tau at 0.99, which label smoothing
   made unreachable). Report the test split at that tau.
3. Cross-city check on the Singapore photos: /content/Photos-clean/*.jpg and
   /content/front_lights.csv (front_ped = 1 rows give the colour of the front pedestrian light).
Usage: python light_cls_check.py   (writes /content/light-cls-check.zip)
"""

import csv
import json
import os
import zipfile

import torch
import torch.nn as nn
from PIL import Image, ImageDraw, ImageOps
from torchvision import models, transforms

ROOT = "/content/imv"
CLASSES = ["red", "green", "blank", "none"]
MODE_TO_CLASS = {0: 0, 1: 1, 2: 1, 3: 2, 4: 3}
SIZE = (432, 576)
TF = transforms.Compose(
    [
        transforms.Resize(SIZE),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ]
)

model = models.mobilenet_v3_large()
model.classifier[-1] = nn.Linear(model.classifier[-1].in_features, len(CLASSES))
model.load_state_dict(torch.load("/content/light_cls.pt", map_location="cpu"))
model = model.cuda().eval()

files = {}
for dirpath, _, names in os.walk(f"{ROOT}/raw"):
    for n in names:
        files[n.lower()] = os.path.join(dirpath, n)


def to_4x3(img):
    """Pad portrait or odd-aspect frames to 4:3 instead of stretching them."""
    w, h = img.size
    if abs(w / h - 4 / 3) < 0.05:
        return img
    tw, th = (w, round(w * 3 / 4)) if w / h > 4 / 3 else (round(h * 4 / 3), h)
    return ImageOps.pad(img, (tw, th), color=(0, 0, 0))


@torch.no_grad()
def probs_for(paths):
    out = []
    for i in range(0, len(paths), 64):
        x = torch.stack(
            [TF(to_4x3(Image.open(p).convert("RGB"))) for p in paths[i : i + 64]]
        ).cuda()
        out.append(model(x).float().softmax(1).cpu())
    return torch.cat(out)


def split(name):
    rows = [
        r
        for r in csv.DictReader(open(f"{ROOT}/{name}_file.csv", encoding="utf-8-sig"))
        if r["file"].lower() in files
    ]
    paths = [files[r["file"].lower()] for r in rows]
    labels = torch.tensor([MODE_TO_CLASS[int(r["mode"])] for r in rows])
    return rows, paths, labels, probs_for(paths)


def decide(p, tau):
    if p[1] >= tau:
        return "green"
    return "red" if p.argmax().item() == 0 else "unknown"


def grid(items, path, title):
    """items: (image path, caption)"""
    if not items:
        return
    cols, w, h = 4, 384, 288
    sheet = Image.new("RGB", (cols * w, ((len(items) + cols - 1) // cols) * (h + 20) + 24), "white")
    d = ImageDraw.Draw(sheet)
    d.text((4, 4), title, fill="black")
    for i, (p, cap) in enumerate(items):
        im = to_4x3(Image.open(p).convert("RGB")).resize((w, h))
        x, y = (i % cols) * w, 24 + (i // cols) * (h + 20)
        sheet.paste(im, (x, y))
        d.text((x + 4, y + h + 3), cap, fill="black")
    sheet.save(path, quality=88)


results = {}
val_rows, val_paths, val_lab, val_p = split("validation")
test_rows, test_paths, test_lab, test_p = split("testing")

# 1. red predicted green (argmax) or with high p_green
bad = []
for nm, rows, paths, lab, pr in (
    ("val", val_rows, val_paths, val_lab, val_p),
    ("test", test_rows, test_paths, test_lab, test_p),
):
    for r, p, y, q in zip(rows, paths, lab, pr, strict=False):
        if y == 0 and q[1] >= 0.3:
            bad.append((p, f"{nm} {r['file']} p_green={q[1]:.2f} block={r['block']}"))
grid(bad, "/content/red_as_green.jpg", "Labelled RED, model p(green) >= 0.3")
print(f"red images with p_green >= 0.3: {len(bad)}")
for _, cap in bad:
    print("  ", cap)

# 2. calibrate tau on validation: red->green <= 0.5%
red_val = sorted((val_p[val_lab == 0][:, 1]).tolist(), reverse=True)
allowed = int(0.005 * len(red_val))  # e.g. 1 of 235
tau = red_val[allowed] + 1e-4 if len(red_val) > allowed else 0.5
tau = max(tau, 0.5)
results["tau"] = tau
for nm, lab, pr in (("validation", val_lab, val_p), ("test", test_lab, test_p)):
    dec = [decide(q, tau) for q in pr]
    red = [d for d, y in zip(dec, lab.tolist(), strict=False) if y == 0]
    green = [d for d, y in zip(dec, lab.tolist(), strict=False) if y == 1]
    r = {
        "red_n": len(red),
        "red_said_green": red.count("green"),
        "red_said_red": red.count("red"),
        "green_n": len(green),
        "green_said_green": green.count("green"),
        "green_said_red": green.count("red"),
    }
    results[nm] = r
    print(
        f"[{nm}] tau={tau:.3f} red {r['red_n']}: said green {r['red_said_green']}, said red {r['red_said_red']} | "
        f"green {r['green_n']}: said green {r['green_said_green']} ({r['green_said_green'] / r['green_n']:.2f}), "
        f"said red {r['green_said_red']}"
    )

# 3. Singapore photos (front pedestrian light colour)
sg = {}
if os.path.exists("/content/front_lights.csv"):
    for r in csv.DictReader(open("/content/front_lights.csv", encoding="utf-8-sig")):
        if r["front_ped"] == "1":
            sg.setdefault(r["image"], set()).add(r["color"])
    imgs = sorted(f for f in os.listdir("/content/Photos-clean") if f.endswith(".jpg"))
    paths = [f"/content/Photos-clean/{f}" for f in imgs]
    pr = probs_for(paths)
    rows, items, cnt = [], [], {}
    for f, p, q in zip(imgs, paths, pr, strict=False):
        sid = f[:-4]
        truth = "/".join(sorted(sg.get(sid, {"none"})))
        top = CLASSES[q.argmax().item()]
        d = decide(q, tau)
        rows.append(
            {
                "image": sid,
                "truth": truth,
                "top1": top,
                "decision": d,
                **{f"p_{c}": round(v, 3) for c, v in zip(CLASSES, q.tolist(), strict=False)},
            }
        )
        cnt[(truth, d)] = cnt.get((truth, d), 0) + 1
        items.append((p, f"{sid} truth={truth} top={top} say={d} pg={q[1]:.2f}"))
    with open("/content/sg_light_cls.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    grid(items, "/content/sg_light_cls.jpg", "Singapore photos: truth = front pedestrian light")
    results["singapore"] = {f"{t}->{d}": n for (t, d), n in sorted(cnt.items())}
    print("[singapore] truth -> decision:", results["singapore"])

json.dump(results, open("/content/light_cls_check.json", "w"), indent=1)
with zipfile.ZipFile("/content/light-cls-check.zip", "w") as z:
    for f in ("light_cls_check.json", "red_as_green.jpg", "sg_light_cls.csv", "sg_light_cls.jpg"):
        if os.path.exists(f"/content/{f}"):
            z.write(f"/content/{f}", f)
print("wrote /content/light-cls-check.zip")
