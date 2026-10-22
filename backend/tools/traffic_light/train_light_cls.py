# ruff: noqa: E501, E731, B023  (offline Colab/Kaggle experiment script, not runtime code)
"""Train a pedestrian-light state classifier on ImVisible (PTL) whole images, with a safety rule.

Data: /content/imv/raw (768x576 images, downloaded by imvisible_eval.py) + the three ImVisible
annotation CSVs. Label = state of the MAIN pedestrian light in the image:
  mode 0 red -> red | 1 green, 2 countdown green -> green | 3 countdown blank -> blank | 4 -> none
Model: ImageNet-pretrained MobileNetV3-Large, fine-tuned on whole images at 576x432.
No hue augmentation (it would turn red into green); horizontal flip is safe.
Safety rule (calibrated on the validation split, never on test):
  say "green" only if p(green) >= tau, where tau is above every p(green) seen on red val images;
  otherwise say "red" if red is the top class, else "unknown".
Usage: python train_light_cls.py  (writes /content/light_cls.pt and /content/light-cls-results.zip)
"""

import csv
import json
import os
import subprocess
import time
import zipfile
from collections import Counter

import torch
import torch.nn as nn
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from torchvision import models, transforms

ROOT = "/content/imv"
CLASSES = ["red", "green", "blank", "none"]
MODE_TO_CLASS = {0: 0, 1: 1, 2: 1, 3: 2, 4: 3}
SIZE = (432, 576)  # height, width (keeps the 4:3 frame)
EPOCHS, BATCH, LR = 12, 32, 3e-4
torch.manual_seed(0)


def rows(split):
    path = f"{ROOT}/{split}_file.csv"
    if not os.path.exists(path):
        subprocess.run(
            [
                "wget",
                "-q",
                "-O",
                path,
                "https://raw.githubusercontent.com/samuelyu2002/ImVisible/"
                f"master/Annotations/{split}_file.csv",
            ],
            check=True,
        )
    return list(csv.DictReader(open(path, encoding="utf-8-sig")))


files = {}
for dirpath, _, names in os.walk(f"{ROOT}/raw"):
    for n in names:
        files[n.lower()] = os.path.join(dirpath, n)


class PTL(Dataset):
    def __init__(self, split, train):
        self.items = [
            (files[r["file"].lower()], MODE_TO_CLASS[int(r["mode"])], r["file"])
            for r in rows(split)
            if r["file"].lower() in files
        ]
        aug = [
            transforms.RandomResizedCrop(SIZE, scale=(0.8, 1.0), ratio=(1.25, 1.42)),
            transforms.RandomHorizontalFlip(),
            transforms.ColorJitter(brightness=0.25, contrast=0.25, saturation=0.1, hue=0),
        ]
        self.tf = transforms.Compose(
            (aug if train else [transforms.Resize(SIZE)])
            + [
                transforms.ToTensor(),
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
            ]
        )
        print(split, len(self.items), dict(Counter(CLASSES[c] for _, c, _ in self.items)))

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        path, label, _ = self.items[i]
        return self.tf(Image.open(path).convert("RGB")), label


def predict(model, ds):
    model.eval()
    probs, labels = [], []
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
        for x, y in DataLoader(ds, batch_size=64, num_workers=2):
            probs.append(model(x.cuda()).float().softmax(1).cpu())
            labels.append(y)
    return torch.cat(probs), torch.cat(labels)


def safety_decide(p, tau):
    """green only above tau; else red if red is top; else unknown."""
    if p[1] >= tau:
        return "green"
    return "red" if p.argmax().item() == 0 else "unknown"


def report(name, probs, labels, tau):
    top = Counter(
        (CLASSES[t], CLASSES[p]) for t, p in zip(labels.tolist(), probs.argmax(1).tolist(), strict=False)
    )
    acc = sum(v for (t, p), v in top.items() if t == p) / len(labels)
    safe = Counter((CLASSES[t], safety_decide(p, tau)) for t, p in zip(labels.tolist(), probs, strict=False))
    n_red, n_green = (labels == 0).sum().item(), (labels == 1).sum().item()
    print(f"\n[{name}] top-1 accuracy (4 classes) = {acc:.3f}")
    print(
        "  confusion (truth -> predicted):", {f"{t}->{p}": v for (t, p), v in sorted(top.items())}
    )
    print(
        f"  safety rule tau={tau:.3f}: red images {n_red}: said green {safe[('red', 'green')]}, "
        f"said red {safe[('red', 'red')]}, unknown {safe[('red', 'unknown')]}"
    )
    print(
        f"                       green images {n_green}: said green {safe[('green', 'green')]} "
        f"({safe[('green', 'green')] / max(n_green, 1):.2f}), said red {safe[('green', 'red')]}, "
        f"unknown {safe[('green', 'unknown')]}"
    )
    return {
        "top1": acc,
        "red_to_green": safe[("red", "green")],
        "green_said_green": safe[("green", "green")],
        "n_red": n_red,
        "n_green": n_green,
    }


def main():
    train, val, test = PTL("training", True), PTL("validation", False), PTL("testing", False)
    model = models.mobilenet_v3_large(weights=models.MobileNet_V3_Large_Weights.IMAGENET1K_V2)
    model.classifier[-1] = nn.Linear(model.classifier[-1].in_features, len(CLASSES))
    model = model.cuda()
    opt = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, EPOCHS)
    scaler = torch.amp.GradScaler()
    loader = DataLoader(train, batch_size=BATCH, shuffle=True, num_workers=2, drop_last=True)
    best = 0
    for epoch in range(EPOCHS):
        model.train()
        t0, total = time.time(), 0.0
        for x, y in loader:
            with torch.autocast("cuda", dtype=torch.float16):
                loss = nn.functional.cross_entropy(model(x.cuda()), y.cuda(), label_smoothing=0.05)
            opt.zero_grad()
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            total += loss.item()
        sched.step()
        probs, labels = predict(model, val)
        acc = (probs.argmax(1) == labels).float().mean().item()
        print(
            f"epoch {epoch + 1}/{EPOCHS} loss {total / len(loader):.3f} val_acc {acc:.3f} ({time.time() - t0:.0f}s)",
            flush=True,
        )
        if acc > best:
            best = acc
            torch.save(model.state_dict(), "/content/light_cls.pt")
    model.load_state_dict(torch.load("/content/light_cls.pt"))
    vp, vl = predict(model, val)
    red_green = vp[vl == 0][:, 1]
    tau = min(0.99, (red_green.max().item() if len(red_green) else 0.5) + 0.02)
    results = {"tau": tau, "val": report("validation", vp, vl, tau)}
    tp, tl = predict(model, test)
    results["test"] = report("test", tp, tl, tau)
    with open("/content/light_cls_test_predictions.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "truth"] + [f"p_{c}" for c in CLASSES] + ["safety_decision"])
        for (_, label, name), p in zip(test.items, tp, strict=False):
            w.writerow(
                [name, CLASSES[label]] + [f"{v:.4f}" for v in p.tolist()] + [safety_decide(p, tau)]
            )
    json.dump(results, open("/content/light_cls_results.json", "w"), indent=1)
    with zipfile.ZipFile("/content/light-cls-results.zip", "w") as z:
        for f in ("light_cls.pt", "light_cls_results.json", "light_cls_test_predictions.csv"):
            z.write(f"/content/{f}", f)
    print("\nwrote /content/light-cls-results.zip")


if __name__ == "__main__":
    main()
