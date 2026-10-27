# ruff: noqa: E501, E731, B023  (offline Colab/Kaggle experiment script, not runtime code)
"""ImVisible (PTL) test split: image-level pedestrian-light detection rate + red/green accuracy.

Runs inside Colab after the backend is installed (/content/work/backend, weights present).
Labels (Annotations/testing_file.csv): one row per image, mode = state of the MAIN pedestrian
light (0 red, 1 green, 2 countdown green, 3 countdown blank, 4 no light); x1,y1 -> x2,y2 is the
zebra-crossing midline in 4032x3024 pixels (x1,y1 = far end). There are NO light boxes, so
"detected" only means "at least one traffic-light box in an image that has a light".
Colour: prototype HSV rule on the chosen light's crop (red/green/unknown); three ways to choose
the light: highest confidence, front_light flag, nearest to the far end of the crosswalk midline.
Usage: python imvisible_eval.py   (prints tables, writes /content/imv-results.zip)
"""

import csv
import glob
import json
import os
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict

from PIL import Image

ROOT = "/content/imv"
BACKEND = "/content/work/backend"
MODE = {0: "red", 1: "green", 2: "green", 3: "blank", 4: "none"}


def sh(cmd):
    print("$", cmd, flush=True)
    subprocess.run(cmd, shell=True, check=True)


def prepare():
    os.makedirs(ROOT, exist_ok=True)
    os.chdir(ROOT)
    if not os.path.exists("testing_file.csv"):
        sh(
            "wget -q https://raw.githubusercontent.com/samuelyu2002/ImVisible/master/Annotations/testing_file.csv"
        )
    if not glob.glob(f"{ROOT}/raw/**/*.*", recursive=True):
        sh(f"{sys.executable} -m pip install -q gdown")
        sh(
            "gdown -q --fuzzy 'https://drive.google.com/file/d/117HUGczZwXFRbuBl7y4cHakH3xQV2Omo/view' -O imv768.zip"
        )
        sh("mkdir -p raw && unzip -q -o imv768.zip -d raw")
    rows = list(csv.DictReader(open("testing_file.csv", encoding="utf-8-sig")))
    files = {
        os.path.basename(f).lower(): f
        for f in glob.glob(f"{ROOT}/raw/**/*", recursive=True)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    }
    os.makedirs("test", exist_ok=True)
    kept = []
    for r in rows:
        src = files.get(r["file"].lower())
        if src is None:
            continue
        dst = f"{ROOT}/test/{os.path.splitext(r['file'])[0]}.jpg"
        if not os.path.exists(dst):
            Image.open(src).convert("RGB").save(dst, quality=95)
        kept.append(r)
    print(f"test rows {len(rows)}, images found {len(kept)}, raw files {len(files)}")
    sizes = Counter(
        Image.open(f"{ROOT}/test/{os.path.splitext(r['file'])[0]}.jpg").size for r in kept[:50]
    )
    print("image sizes (first 50):", dict(sizes))
    return kept


def run_models():
    os.chdir(BACKEND)
    for name, extra in (("i01-base", ""), ("i04-roi", "--roi --max-edge 1280")):
        if not os.path.exists(f"/content/out/{name}/results.jsonl"):
            sh(
                f"{sys.executable} -X utf8 -m model_tests.run_depth --input {ROOT}/test "
                f"--output /content/out/{name} {extra} --label {name} 2>&1 | tail -1"
            )


def light_colour(img, box):
    """Prototype HSV rule: bright, saturated red vs green pixels inside the box."""
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    crop = img.crop((max(0, x0), max(0, y0), max(x0 + 1, x1), max(y0 + 1, y1))).convert("HSV")
    red = green = 0
    for h, s, v in crop.getdata():  # PIL HSV: all channels 0-255
        if s < 90 or v < 120:
            continue
        if h < 15 or h > 235:
            red += 1
        elif 70 <= h <= 150:  # green to cyan (pedestrian greens are often blue-green)
            green += 1
    if red >= 3 and red >= 2 * green:
        return "red"
    if green >= 3 and green >= 2 * red:
        return "green"
    return "unknown"


def evaluate(rows):
    labels = {os.path.splitext(r["file"])[0]: r for r in rows}
    out = {}
    for name in ("i01-base", "i04-roi"):
        res = {}
        for line in open(f"/content/out/{name}/results.jsonl", encoding="utf-8"):
            r = json.loads(line)
            sid = os.path.basename(r["image"])[:-4]
            fw, fh = r["frame_size"]
            res[sid] = (
                fw,
                fh,
                [b for b in r["metadata"]["boxes"] if b["class_name"] == "traffic light"],
            )
        out[name] = res

    print("\n=== Image-level detection: at least one traffic-light box ===")
    print(
        f"{'run':10} {'red':>11} {'green':>11} {'blank':>11} {'ALL light':>11} {'no-light FP':>12}"
    )
    for name, res in out.items():
        c = defaultdict(lambda: [0, 0])
        for sid, lab in labels.items():
            st = MODE[int(lab["mode"])]
            hit = bool(res.get(sid, (0, 0, []))[2])
            c[st][0] += 1
            c[st][1] += hit
            if st != "none":
                c["all"][0] += 1
                c["all"][1] += hit
        f = lambda k: f"{c[k][1]}/{c[k][0]}={c[k][1] / c[k][0]:.2f}" if c[k][0] else "-"
        print(
            f"{name:10} {f('red'):>11} {f('green'):>11} {f('blank'):>11} {f('all'):>11} {f('none'):>12}"
        )

    print(
        "\n=== Red/green accuracy (images whose main light is red or green, and a light was detected) ==="
    )
    pickers = {
        "highest_conf": lambda boxes, *_: max(boxes, key=lambda b: b["confidence"]),
        "front_light": lambda boxes, *_: next((b for b in boxes if b.get("front_light")), None),
        "near_crosswalk": lambda boxes, fx, fy: min(
            boxes, key=lambda b: abs((b["bbox"][0] + b["bbox"][2]) / 2 - fx)
        ),
    }
    rows_out = []
    for name, res in out.items():
        for pname, pick in pickers.items():
            conf = Counter()
            for sid, lab in labels.items():
                truth = MODE[int(lab["mode"])]
                if truth not in ("red", "green"):
                    continue
                fw, fh, boxes = res.get(sid, (0, 0, []))
                if not boxes:
                    conf[(truth, "not_detected")] += 1
                    continue
                fx = float(lab["x1"]) * fw / 4032  # far end of the crosswalk midline, frame pixels
                b = pick(boxes, fx, float(lab["y1"]) * fh / 3024)
                if b is None:
                    conf[(truth, "no_pick")] += 1
                    continue
                img = Image.open(f"{ROOT}/test/{sid}.jpg").convert("RGB")
                sx, sy = img.width / fw, img.height / fh
                bx = [b["bbox"][0] * sx, b["bbox"][1] * sy, b["bbox"][2] * sx, b["bbox"][3] * sy]
                pred = light_colour(img, bx)
                conf[(truth, pred)] += 1
                rows_out.append(
                    {
                        "run": name,
                        "picker": pname,
                        "image": sid,
                        "truth": truth,
                        "pred": pred,
                        "conf": round(b["confidence"], 3),
                    }
                )
            n = sum(v for (t, p), v in conf.items())
            correct = conf[("red", "red")] + conf[("green", "green")]
            print(
                f"{name:9} {pname:15} n={n} correct={correct} ({correct / n:.2f}) | "
                f"red->green={conf[('red', 'green')]} green->red={conf[('green', 'red')]} | "
                f"unknown={conf[('red', 'unknown')] + conf[('green', 'unknown')]} | "
                f"not_detected={conf[('red', 'not_detected')] + conf[('green', 'not_detected')]} "
                f"no_pick={conf[('red', 'no_pick')] + conf[('green', 'no_pick')]}"
            )
    with open("/content/imv_colour.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows_out[0]))
        w.writeheader()
        w.writerows(rows_out)
    with zipfile.ZipFile("/content/imv-results.zip", "w") as z:
        for name in out:
            z.write(f"/content/out/{name}/results.jsonl", f"{name}/results.jsonl")
            z.write(f"/content/out/{name}/run.json", f"{name}/run.json")
        z.write("/content/imv_colour.csv", "imv_colour.csv")
        z.write(f"{ROOT}/testing_file.csv", "testing_file.csv")
    print("\nwrote /content/imv-results.zip")


if __name__ == "__main__":
    kept = prepare()
    run_models()
    evaluate(kept)
