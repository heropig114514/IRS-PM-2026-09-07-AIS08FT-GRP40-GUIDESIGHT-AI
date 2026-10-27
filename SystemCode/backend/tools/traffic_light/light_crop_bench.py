# ruff: noqa: E501, E731, B023  (offline Colab/Kaggle experiment script, not runtime code)
"""Latency benchmark for the light-crop colour classifier (measured, not estimated).

Measures, per frame with k traffic lights (k = 1, 3, 5), on the current GPU:
  crop_ms  : CPU crop + letterbox to 96x96 + normalise (PIL/torchvision, as in train_light_crop.py)
  infer_ms : host->GPU copy + MobileNetV3-Small forward (batch = k) + softmax + copy back
  total_ms : both
Protocol: 20 warm-up runs, then 200 timed runs; torch.cuda.synchronize() around the GPU part;
reports median and p95. Also times FP16 autocast for comparison.
Inputs: light_crop.pt (anywhere under /kaggle/input or /content), Singapore photos + labelled
front-light boxes (sg_front_lights.json) as realistic frames/boxes.
Usage: python light_crop_bench.py
"""

import glob
import json
import statistics
import time

import torch
import torch.nn as nn
from PIL import Image, ImageOps
from torchvision import models, transforms


def find(name):
    hits = glob.glob(f"/content/**/{name}", recursive=True) + glob.glob(
        f"/kaggle/**/{name}", recursive=True
    )
    if not hits:
        raise FileNotFoundError(name)
    return hits[0]


CROP = 96
TF = transforms.Compose(
    [transforms.ToTensor(), transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])]
)
model = models.mobilenet_v3_small()
model.classifier[-1] = nn.Linear(model.classifier[-1].in_features, 3)
model.load_state_dict(torch.load(find("light_crop.pt"), map_location="cpu"))
model = model.cuda().eval()
print("GPU:", torch.cuda.get_device_name(0), "| torch", torch.__version__)

sg = json.load(open(find("sg_front_lights.json")))
photo_dir = find("sg-002.jpg").rsplit("/", 1)[0]
frames = [
    (Image.open(f"{photo_dir}/{s['image']}.jpg").convert("RGB"), s["gt_box"]) for s in sg[:10]
]


def crop(img, box, pad=0.25):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    return ImageOps.pad(
        img.crop((x0 - pad * w, y0 - pad * h, x1 + pad * w, y1 + pad * h)), (CROP, CROP)
    )


def run(k, fp16, n_warm=20, n=200):
    crop_t, infer_t = [], []
    for i in range(n_warm + n):
        img, box = frames[i % len(frames)]
        t0 = time.perf_counter()
        x = torch.stack([TF(crop(img, box)) for _ in range(k)])
        t1 = time.perf_counter()
        torch.cuda.synchronize()
        t2 = time.perf_counter()
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16, enabled=fp16):
            p = model(x.cuda(non_blocking=False)).float().softmax(1).cpu()
        torch.cuda.synchronize()
        t3 = time.perf_counter()
        if i >= n_warm:
            crop_t.append((t1 - t0) * 1000)
            infer_t.append((t3 - t2) * 1000)
    q = lambda v: (statistics.median(v), sorted(v)[int(0.95 * len(v)) - 1])
    tot = [a + b for a, b in zip(crop_t, infer_t, strict=False)]
    (cm, cp), (im, ip), (tm, tp) = q(crop_t), q(infer_t), q(tot)
    print(
        f"k={k} {'FP16' if fp16 else 'FP32'}: crop {cm:.2f}/{cp:.2f} ms | infer {im:.2f}/{ip:.2f} ms | "
        f"total {tm:.2f}/{tp:.2f} ms  (median/p95)"
    )
    return p


print("per frame with k traffic lights; median / p95 over 200 runs after 20 warm-up runs")
for fp16 in (False, True):
    for k in (1, 3, 5):
        run(k, fp16)
