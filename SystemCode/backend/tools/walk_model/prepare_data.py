# ruff: noqa: E501, E731, B023  (offline Kaggle experiment script, not runtime code)
"""Build the 87-class fine-tuning dataset for yolo26n (80 COCO classes + 7 walking-aid classes).

Classes: the 80 COCO classes in their original order (ids 0-79 unchanged, so existing code keyed by
class name / id keeps working), then 80 pole, 81 bollard, 82 traffic_cone, 83 stairs,
84 ped_red, 85 ped_green, 86 ped_off.

Sources (all fetched on the Kaggle/Colab machine, nothing is uploaded from the laptop):
  coco      COCO 2017: a class-balanced train2017 subset (keeps the 80 classes from being forgotten;
            bicycle / motorcycle / street classes boosted) + the full val2017 as the AP gate.
  mendeley  Navigation assistance for visually impaired (Mendeley m68g3h7p87, CC BY 4.0):
            Electric Pole / Building Pillar -> pole, Parking Cone -> traffic_cone, Stairs -> stairs.
  ood       Outdoor Obstacle Detection (Roboflow fpn/ood-pbnro v1, CC BY 4.0): warning_column /
            spherical_roadblock -> bollard, stairs -> stairs (its pole / street_light labels are dropped).
  pedlight  Pedestrian traffic lights (Roboflow imagelable-a/pedestrian-traffic-lights v16, CC BY 4.0):
            red / countdown_red -> ped_red, green / countdown_green -> ped_green, none -> ped_off.
  exdark    ExDark low-light images (academic use): 12 COCO classes, official train/val/test split.
  imvisible ImVisible train/val (Shanghai): the front pedestrian light is auto-boxed with the teacher
            detector + crosswalk-midline pick, labelled with the image's light state, kept only when an HSV
            check clearly agrees (images without such a box are dropped; no teacher traffic-light fills).
Known conflict: COCO labels pedestrian lights as "traffic light", the light sources use ped_*; the COCO
subset therefore does not boost traffic lights, and only ped_* boxes are used for the colour decision.
            The ImVisible TEST split is never used here (kept for the red->green evaluation).

Missing labels: every source only labels its own classes. A teacher (official yolo26x, COCO) adds
boxes for COCO classes a source does not label exhaustively, when they do not overlap a ground-truth
box. Test splits keep ground truth only. New classes on other sources are added in round 2
(train.py --round 2) from the round-1 model.
Leakage: splits are re-made by group, not by random image. A group joins visually near-identical
images (dHash, Hamming <= 10: consecutive frames, repeated screenshots, Roboflow augmented copies) and,
for pedlight, timestamp names within 30 s. File names are not trusted for de-duplication (Mendeley's
"frame_0s" repeats across videos). COCO-named images inside OOD are dropped (could be COCO val2017).

Roboflow needs the Kaggle secret ROBOFLOW_API_KEY (or env var). Usage: python prepare_data.py
Outputs under $FT_ROOT (default /tmp/ft): ds/ (YOLO dataset + yaml files), stats.json, sheets/*.jpg
"""

import csv
import glob
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw

HALF = __import__("torch").cuda.is_available()
ROOT = os.environ.get("FT_ROOT", "/tmp/ft")
RAW, DS = f"{ROOT}/raw", f"{ROOT}/ds"
MAX_EDGE = 800
COCO_TRAIN_IMAGES = int(os.environ.get("FT_COCO_IMAGES", 60000))
TEACHER_CONF = 0.4
TEACHER = os.environ.get("FT_TEACHER", "yolo26x.pt")
# FT_EVAL_ONLY=1: rebuild only the held-out test splits + COCO val2017 (same deterministic splits as training,
# no COCO train download, no teacher) for evaluating existing checkpoints.
EVAL_ONLY = os.environ.get("FT_EVAL_ONLY") == "1"
NEW = ["pole", "bollard", "traffic_cone", "stairs", "ped_red", "ped_green", "ped_off"]
random.seed(0)


def sh(cmd, tries=1):
    print("$", cmd, flush=True)
    for k in range(tries):
        if subprocess.run(cmd, shell=True).returncode == 0:
            return
        print(f"  failed (try {k + 1}/{tries})", flush=True)
        time.sleep(30)
    raise RuntimeError(f"command failed: {cmd}")


def fetch(url, dst, min_mb):
    """curl with HTTP-error detection, retries and a size check (an error page is not a zip)."""
    sh(f"curl -fsSL --retry 5 --retry-delay 10 -o '{dst}' '{url}'", tries=3)
    mb = os.path.getsize(dst) / 2**20
    assert mb >= min_mb, f"{url}: only {mb:.1f} MB downloaded"


def gdrive(file_id, dst, min_mb):
    """Google Drive via gdown, retried (Drive sometimes refuses with a quota error)."""
    sh(f"{sys.executable} -m pip install -q gdown")
    sh(f"gdown -q --fuzzy 'https://drive.google.com/file/d/{file_id}/view' -O '{dst}'", tries=4)
    mb = os.path.getsize(dst) / 2**20
    assert mb >= min_mb, f"Drive {file_id}: only {mb:.1f} MB downloaded"


def coco80():
    from ultralytics import YOLO

    m = YOLO("yolo26n.pt")
    return [m.names[i] for i in range(80)]


NAMES = None  # filled in main(): 80 COCO names + NEW
CID = {}


def group_key(stem):
    """Scene / video group: Roboflow suffix removed; the trailing frame number is bucketed by 30, so
    neighbouring frames (or seconds of a timestamp) share a group but one prefix never swallows a whole source."""
    s = stem.split(".rf.")[0]
    s = re.sub(r"_(jpe?g|png)$", "", s, flags=re.I)
    m = re.match(r"(.*?)(\d+)$", s)
    return f"{m.group(1)}#{int(m.group(2)) // 30}" if m else s


def split_of(group):
    h = int(hashlib.md5(group.encode()).hexdigest(), 16) % 10
    return "test" if h == 0 else "val" if h == 1 else "train"


def dhash(path):
    px = Image.open(path).convert("L").resize((9, 8)).tobytes()
    bits = [px[r * 9 + c + 1] > px[r * 9 + c] for r in range(8) for c in range(8)]
    return sum(1 << i for i, b in enumerate(bits) if b)


def near_duplicate_groups(recs, max_dist=10):
    """Union name groups with visually near-identical images (64-bit dHash, Hamming <= max_dist):
    consecutive video frames / repeated screenshots must not straddle train and test."""
    import numpy as np

    with ThreadPoolExecutor(8) as ex:
        h = np.array(list(ex.map(lambda r: dhash(r["orig"]), recs)), dtype=np.uint64)
    parent = list(range(len(recs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_name = {}
    for i, r in enumerate(recs):
        j = by_name.setdefault(r["group"], i)
        parent[find(i)] = find(j)
    pop = np.array([bin(v).count("1") for v in range(256)], dtype=np.uint8)
    hb = h.view(np.uint8).reshape(-1, 8)
    for a in range(0, len(recs), 256):
        x = np.bitwise_xor(hb[a : a + 256, None, :], hb[None, :, :])
        d = pop[x].sum(-1, dtype=np.int32)
        for i, j in zip(*np.nonzero(d <= max_dist), strict=False):
            if a + i < j:
                parent[find(a + i)] = find(j)
    roots = {}
    for i, r in enumerate(recs):
        roots.setdefault(find(i), r["stem"])
    for i, r in enumerate(recs):
        r["group"] = roots[find(i)]


def assign_splits(recs):
    """Group split (file-name group + near-duplicate images); if the groups are too coarse for a usable
    80/10/10 split, fall back to contiguous blocks of 25 name-sorted images."""
    src = recs[0]["src"] if recs else "?"
    near_duplicate_groups(recs)

    def ok():
        n = Counter(r["split"] for r in recs)
        return all(0.05 <= n[k] / len(recs) <= 0.2 for k in ("val", "test"))

    for r in recs:
        r["split"] = split_of(f"{src}/{r['group']}")
    how = "group"
    if recs and not ok():
        for i, r in enumerate(sorted(recs, key=lambda r: r["stem"])):
            r["group"] = f"block{i // 25}"
            r["split"] = split_of(f"{src}/{r['group']}")
        how = "name-block fallback"
    n = Counter(r["split"] for r in recs)
    big = Counter(r["group"] for r in recs).most_common(1)
    print(
        f"  {src} split ({how}): {dict(n)} from {len({r['group'] for r in recs})} groups, largest {big[0][1] if big else 0}"
    )
    assert len(recs) < 200 or ok(), (
        f"{src}: unusable split {dict(n)}"
    )  # tiny sets: smoke tests only
    return recs


def save_image(src, dst):
    if os.path.exists(dst):
        return Image.open(dst).size
    img = Image.open(src).convert("RGB")  # no EXIF rotation: annotations use the stored orientation
    img.thumbnail((MAX_EDGE, MAX_EDGE))
    img.save(dst, quality=90)
    return img.size


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter + 1e-9)


# ------------------------------------------------------------------ sources -> records
# record: {src, split, stem, image (resized copy), boxes [[cls, x0, y0, x1, y1] normalised],
#          exhaustive [COCO ids the source labels completely; teacher does not add these]}


def yolo_folder_records(src, root, mapping, exhaustive=(), drop_names=(), name_groups=False):
    """Roboflow-style export (train/valid/test with images/ + labels/); boxes or polygons."""
    import yaml

    ys = glob.glob(f"{root}/**/data.yaml", recursive=True)
    assert len(ys) == 1, f"{src}: expected exactly one data.yaml, found {ys}"
    names = yaml.safe_load(open(ys[0]))["names"]
    names = names if isinstance(names, list) else [names[k] for k in sorted(names)]
    print(f"{src} classes: {names}")
    recs, unmapped, dropped = [], Counter(), Counter()
    for img in sorted(glob.glob(f"{os.path.dirname(ys[0])}/**/images/*.*", recursive=True)):
        stem = os.path.splitext(os.path.basename(img))[0]
        base = stem.split(".rf.")[0]
        # Roboflow augmented copies are NOT removed by name: names like "frame_0s" repeat across different
        # videos (Mendeley). Copies are grouped with their original by the near-duplicate check instead.
        if any(re.fullmatch(p, re.sub(r"_(jpe?g|png)$", "", base, flags=re.I)) for p in drop_names):
            dropped[src] += 1  # e.g. COCO images inside OOD: could be COCO val2017 (the AP gate)
            continue
        lab = img.replace("/images/", "/labels/").rsplit(".", 1)[0] + ".txt"
        boxes = []
        for line in open(lab).read().split("\n") if os.path.exists(lab) else []:
            v = line.split()
            if len(v) < 5:
                continue
            name = names[int(v[0])]
            if name not in mapping:
                unmapped[name] += 1
                continue
            c = list(map(float, v[1:]))
            if len(c) == 4:
                x, y, w, h = c
                xyxy = [x - w / 2, y - h / 2, x + w / 2, y + h / 2]
            else:
                xs, ys = c[0::2], c[1::2]
                xyxy = [min(xs), min(ys), max(xs), max(ys)]
            boxes.append([CID[mapping[name]]] + [min(1, max(0, t)) for t in xyxy])
        g = group_key(base) if name_groups else img  # name groups only where names are timestamps
        uid = hashlib.md5(img.encode()).hexdigest()[
            :8
        ]  # file names are not unique (augmented copies)
        recs.append(
            {
                "src": src,
                "split": "",
                "group": g,
                "stem": f"{src}__{base[:100]}_{uid}",
                "orig": img,
                "boxes": boxes,
                "exhaustive": [CID[n] for n in exhaustive],
            }
        )
    print(
        f"{src}: {len(recs)} images, dropped images {dict(dropped)}, dropped labels {dict(unmapped)}"
    )
    return assign_splits(recs)


def roboflow(workspace, project, version, dst):
    if glob.glob(f"{dst}/**/data.yaml", recursive=True):
        return dst
    key = os.environ.get("ROBOFLOW_API_KEY")
    if not key:
        from kaggle_secrets import UserSecretsClient

        key = UserSecretsClient().get_secret("ROBOFLOW_API_KEY")
    from roboflow import Roboflow

    Roboflow(api_key=key).workspace(workspace).project(project).version(version).download(
        "yolov11", location=dst
    )
    return dst


def mendeley():
    d = f"{RAW}/mendeley"
    if not glob.glob(f"{d}/**/data.yaml", recursive=True):
        os.makedirs(d, exist_ok=True)
        fetch("https://data.mendeley.com/public-api/zip/m68g3h7p87/download/1", f"{d}/m.zip", 500)
        sh(f"cd {d} && unzip -q -o m.zip && rm m.zip")
        for z in glob.glob(f"{d}/**/*.zip", recursive=True):  # inner archives, if any
            sh(f"cd '{os.path.dirname(z)}' && unzip -q -o '{z}' && rm '{z}'")
    m = {
        "Bus": "bus",
        "Car": "car",
        "Van": "car",
        "Cycle": "bicycle",
        "Motorcycle": "motorcycle",
        "Person": "person",
        "Truck": "truck",
        "Pickup": "truck",
        "Electric Pole": "pole",
        "Building Pillar": "pole",
        "Parking Cone": "traffic_cone",
        "Stairs": "stairs",
    }
    return yolo_folder_records("mendeley", d, m)


def ood():
    d = roboflow("fpn", "ood-pbnro", 1, f"{RAW}/ood")
    m = {
        n: n
        for n in ("car", "truck", "bus", "dog", "bicycle", "motorcycle", "train", "bench", "person")
    }
    # v2: OOD "pole" / "street_light" are NOT used (lamp heads, tilted poles and glare boxes; AP50 0.18 and
    # inconsistent with Mendeley). Poles come from Mendeley, plus round-2 pseudo-labels on OOD images.
    m.update(
        {
            "fire_hydrant": "fire hydrant",
            "stop_sign": "stop sign",
            "traffic_light": "traffic light",
            "warning_column": "bollard",
            "spherical_roadblock": "bollard",
            "stairs": "stairs",
        }
    )
    return yolo_folder_records("ood", d, m, drop_names=[r"\d{12}"])


def pedlight():
    d = roboflow("imagelable-a", "pedestrian-traffic-lights", 16, f"{RAW}/pedlight")
    m = {
        "red": "ped_red",
        "countdown_red": "ped_red",
        "green": "ped_green",
        "countdown_green": "ped_green",
        "none": "ped_off",
    }
    return yolo_folder_records("pedlight", d, m, name_groups=True)


def exdark():
    d = f"{RAW}/exdark"
    if not os.path.exists(f"{d}/imageclasslist.txt"):
        os.makedirs(d, exist_ok=True)
        gdrive("1BHmPgu8EsHoFDDkMGLVoXIlCth2dW6Yx", f"{d}/img.zip", 1000)
        sh(f"cd {d} && unzip -q -o img.zip && rm img.zip")
        gdrive("1P3iO3UYn7KoBi5jiUkogJq96N6maZS1i", f"{d}/ann.zip", 1)
        sh(f"cd {d} && unzip -q -o ann.zip && rm ann.zip")
        fetch(
            "https://raw.githubusercontent.com/cs-chan/Exclusively-Dark-Image-Dataset/master/Groundtruth/imageclasslist.txt",
            f"{d}/imageclasslist.txt",
            0.1,
        )
    m = {
        "Bicycle": "bicycle",
        "Boat": "boat",
        "Bottle": "bottle",
        "Bus": "bus",
        "Car": "car",
        "Cat": "cat",
        "Chair": "chair",
        "Cup": "cup",
        "Dog": "dog",
        "Motorbike": "motorcycle",
        "People": "person",
        "Table": "dining table",
    }
    split = {}
    for line in open(f"{d}/imageclasslist.txt"):
        v = line.split()
        if len(v) == 5 and v[4].isdigit():
            split[v[0].lower()] = {"1": "train", "2": "val", "3": "test"}[v[4]]
    imgs = {
        os.path.basename(p).lower(): p
        for p in glob.glob(f"{d}/**/*.*", recursive=True)
        if p.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")) and "annno" not in p.lower()
    }
    recs = []
    for ann in glob.glob(f"{d}/**/*.txt", recursive=True):
        name = os.path.basename(ann)[:-4]
        if name.lower() not in imgs or name.lower() not in split:
            continue
        img = imgs[name.lower()]
        W, H = Image.open(img).size
        boxes = []
        for line in open(ann).read().split("\n")[1:]:
            v = line.split()
            if len(v) >= 5 and v[0] in m:
                left, t, w, h = map(float, v[1:5])
                boxes.append(
                    [
                        CID[m[v[0]]],
                        max(0, left / W),
                        max(0, t / H),
                        min(1, (left + w) / W),
                        min(1, (t + h) / H),
                    ]
                )
        stem = os.path.splitext(name)[0]
        recs.append(
            {
                "src": "exdark",
                "split": split[name.lower()],
                "group": stem,
                "stem": f"exdark__{stem}",
                "orig": img,
                "boxes": boxes,
                "exhaustive": [CID[n] for n in m.values()],
            }
        )
    print(f"exdark: {len(recs)} images {Counter(r['split'] for r in recs)}")
    return recs


IMV = f"{RAW}/imvisible"
MODE_TO_NEW = {0: "ped_red", 1: "ped_green", 2: "ped_green", 3: "ped_off"}


def imvisible_rows():
    os.makedirs(IMV, exist_ok=True)
    for split in ("training", "validation", "testing"):
        p = f"{IMV}/{split}_file.csv"
        if not os.path.exists(p):
            fetch(
                f"https://raw.githubusercontent.com/samuelyu2002/ImVisible/master/Annotations/{split}_file.csv",
                p,
                0.01,
            )
    if not glob.glob(f"{IMV}/raw/**/*.*", recursive=True):
        gdrive("117HUGczZwXFRbuBl7y4cHakH3xQV2Omo", f"{IMV}/imv768.zip", 50)
        sh(f"cd {IMV} && mkdir -p raw && unzip -q -o imv768.zip -d raw && rm imv768.zip")
    files = {n.lower(): os.path.join(dp, n) for dp, _, ns in os.walk(f"{IMV}/raw") for n in ns}
    rows = {
        s: list(csv.DictReader(open(f"{IMV}/{s}_file.csv", encoding="utf-8-sig")))
        for s in ("training", "validation", "testing")
    }
    return rows, files


def imvisible():
    """Train/val records; the front light box is added after the teacher runs (needs detections)."""
    rows, files = imvisible_rows()
    recs = []
    for split, ours in (("training", "train"), ("validation", "val")):
        for r in rows[split]:
            f = files.get(r["file"].lower())
            if f:
                stem = os.path.splitext(r["file"])[0]
                # other pedestrian lights in the image are unlabelled: no teacher "traffic light" fills here
                recs.append(
                    {
                        "src": "imvisible",
                        "split": ours,
                        "group": stem,
                        "stem": f"imvisible__{stem}",
                        "orig": f,
                        "boxes": [],
                        "exhaustive": [CID["traffic light"]],
                        "mode": int(r["mode"]),
                        "far": [float(r["x1"]) / 4032, float(r["y1"]) / 3024],
                    }
                )
    print(f"imvisible: {len(recs)} train/val images")
    return recs


def coco():
    d = f"{RAW}/coco"
    os.makedirs(f"{d}/train2017", exist_ok=True)
    if not os.path.exists(f"{d}/annotations/instances_train2017.json"):
        fetch(
            "http://images.cocodataset.org/annotations/annotations_trainval2017.zip",
            f"{d}/ann.zip",
            200,
        )
        sh(f"cd {d} && unzip -q -o ann.zip && rm ann.zip")
    if not os.path.isdir(f"{d}/val2017"):
        fetch("http://images.cocodataset.org/zips/val2017.zip", f"{d}/val2017.zip", 700)
        sh(f"cd {d} && unzip -q -o val2017.zip && rm val2017.zip")
    recs = []
    for part in ("val", "train"):
        js = json.load(open(f"{d}/annotations/instances_{part}2017.json"))
        cat = {c["id"]: i for i, c in enumerate(sorted(js["categories"], key=lambda c: c["id"]))}
        imgs = {i["id"]: i for i in js["images"]}
        boxes = defaultdict(list)
        for a in js["annotations"]:
            if a["iscrowd"]:
                continue
            im = imgs[a["image_id"]]
            x, y, w, h = a["bbox"]
            if w > 1 and h > 1:
                boxes[a["image_id"]].append(
                    [
                        cat[a["category_id"]],
                        x / im["width"],
                        y / im["height"],
                        (x + w) / im["width"],
                        (y + h) / im["height"],
                    ]
                )
        if part == "val":
            for iid, im in imgs.items():
                recs.append(
                    {
                        "src": "coco_val",
                        "split": "coco_val",
                        "group": str(iid),
                        "stem": f"coco__{iid}",
                        "orig": f"{d}/val2017/{im['file_name']}",
                        "boxes": boxes[iid],
                        "exhaustive": list(range(80)),
                    }
                )
            if EVAL_ONLY:
                break
            continue
        # class-balanced subset: rarest classes first, then boosted street classes, then random fill
        by_cls = defaultdict(list)
        for iid, bs in boxes.items():
            for c in {b[0] for b in bs}:
                by_cls[c].append(iid)
        quota = defaultdict(lambda: 300)
        for n, q in {
            "bicycle": 2000,
            "motorcycle": 2000,
            "person": 3000,
            "car": 2000,
            "bus": 1000,
            "truck": 1000,
            "fire hydrant": 2000,
            "stop sign": 2000,
            "parking meter": 2000,
            "bench": 1500,
            "dog": 800,
            "umbrella": 800,
        }.items():
            quota[CID[n]] = q
        chosen = set()
        for c in sorted(by_cls, key=lambda c: len(by_cls[c])):
            ids = by_cls[c][:]
            random.shuffle(ids)
            have = sum(1 for i in ids if i in chosen)
            for i in ids:
                if have >= quota[c]:
                    break
                if i not in chosen:
                    chosen.add(i)
                    have += 1
        rest = [i for i in boxes if i not in chosen]
        random.shuffle(rest)
        chosen |= set(rest[: max(0, COCO_TRAIN_IMAGES - len(chosen))])
        chosen = sorted(chosen)
        random.shuffle(chosen)
        for k, iid in enumerate(chosen):
            im = imgs[iid]
            recs.append(
                {
                    "src": "coco",
                    "split": "val" if k < 1000 else "train",
                    "group": str(iid),
                    "stem": f"coco__{iid}",
                    "orig": f"{d}/train2017/{im['file_name']}",
                    "url": im["coco_url"],
                    "boxes": boxes[iid],
                    "exhaustive": list(range(80)),
                }
            )
    need = [r for r in recs if r.get("url") and not os.path.exists(r["orig"])]
    print(f"coco: {len(recs)} images ({len(need)} to download)", flush=True)

    def get(r):
        for _ in range(3):
            if (
                subprocess.run(
                    ["curl", "-sfL", "--max-time", "60", "-o", r["orig"], r["url"]]
                ).returncode
                == 0
            ):
                return
            if os.path.exists(r["orig"]):
                os.remove(r["orig"])

    with ThreadPoolExecutor(64) as ex:
        list(ex.map(get, need))
    recs = [r for r in recs if os.path.exists(r["orig"])]
    n_val = sum(r["src"] == "coco_val" for r in recs)
    assert n_val == 5000 or COCO_TRAIN_IMAGES < 1000, (
        f"COCO val2017 has {n_val} images, expected 5000"
    )
    return recs


# ------------------------------------------------------------------ teacher + writing


def run_teacher(recs):
    """Official yolo26x on every non-COCO image; cached in teacher.jsonl. Boxes normalised xyxy."""
    path = f"{ROOT}/teacher.jsonl"
    done = {}
    if os.path.exists(path):
        for line in open(path):
            r = json.loads(line)
            done[r["stem"]] = r["det"]
    todo = [r for r in recs if not r["src"].startswith("coco") and r["stem"] not in done]
    if todo:
        from ultralytics import YOLO

        t = YOLO(TEACHER)
        with open(path, "a") as fh:
            for i in range(0, len(todo), 64):
                chunk = todo[i : i + 64]
                for r, res in zip(
                    chunk,
                    t.predict(
                        [x["image"] for x in chunk],
                        imgsz=960,
                        conf=0.1,
                        half=HALF,
                        verbose=False,
                        batch=16,
                    ),
                    strict=False,
                ):
                    det = [
                        [int(c), float(s)] + [round(float(v), 5) for v in b]
                        for c, s, b in zip(
                            res.boxes.cls, res.boxes.conf, res.boxes.xyxyn, strict=False
                        )
                    ]
                    done[r["stem"]] = det
                    fh.write(json.dumps({"stem": r["stem"], "det": det}) + "\n")
                print(f"  teacher {min(i + 64, len(todo))}/{len(todo)}", flush=True)
    return done


def imvisible_front_light(r, det):
    """ImVisible: pick the teacher's traffic light nearest the crosswalk far end; drop on HSV conflict."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from imv_helpers import hsv_colour

    if r["mode"] not in MODE_TO_NEW:
        return None
    lights = [d for d in det if d[0] == CID["traffic light"] and d[1] >= 0.15]
    fx, fy = r["far"]
    above = [d for d in lights if (d[3] + d[5]) / 2 < fy] or lights
    if not above:
        return None
    d = min(above, key=lambda d: abs((d[2] + d[4]) / 2 - fx))
    img = Image.open(r["image"])
    W, H = img.size
    hsv = hsv_colour(img, [d[2] * W, d[3] * H, d[4] * W, d[5] * H])
    want = MODE_TO_NEW[r["mode"]]
    if {"ped_red": "red", "ped_green": "green", "ped_off": "unknown"}[want] != hsv:
        return None  # keep only boxes whose colour clearly agrees with the image label
    return [CID[want]] + d[2:6]


def label_lines(r, det, extra=()):
    """Ground truth + teacher fills (train/val only) + optional round-2 extras."""
    boxes = [b[:] for b in r["boxes"]]
    if r["split"] in ("train", "val") and det is not None:
        ped = [b for b in boxes if b[0] >= CID["ped_red"]]
        for d in det:
            c, s, xyxy = d[0], d[1], d[2:6]
            if s < TEACHER_CONF or c in r["exhaustive"]:
                continue
            if any(iou(xyxy, b[1:]) >= 0.5 for b in boxes):
                continue
            if c == CID["traffic light"] and any(iou(xyxy, b[1:]) >= 0.2 for b in ped):
                continue
            boxes.append([c] + xyxy)
    for b in extra:
        if not any(iou(b[1:], x[1:]) >= 0.5 for x in boxes):
            boxes.append(b)
    out = []
    for c, x0, y0, x1, y1 in boxes:
        w, h = x1 - x0, y1 - y0
        if w > 0.002 and h > 0.002:
            out.append(f"{c} {(x0 + x1) / 2:.5f} {(y0 + y1) / 2:.5f} {w:.5f} {h:.5f}")
    return out


def folder(r):
    if r["split"] in ("train", "val"):
        return f"{DS}/images/{r['split']}", f"{DS}/labels/{r['split']}"
    name = "coco_val" if r["split"] == "coco_val" else f"test_{r['src']}"
    return f"{DS}/{name}/images", f"{DS}/{name}/labels"


def write_labels(recs, teacher, extra=None):
    counts = defaultdict(Counter)
    for r in recs:
        _, ld = folder(r)
        os.makedirs(ld, exist_ok=True)
        lines = label_lines(r, teacher.get(r["stem"]), (extra or {}).get(r["stem"], ()))
        open(f"{ld}/{r['stem']}.txt", "w").write("\n".join(lines) + ("\n" if lines else ""))
        for ln in lines:
            counts[f"{r['src']}/{r['split']}"][NAMES[int(ln.split()[0])]] += 1
    return counts


def write_yaml(names):
    def y(path, val, nms):
        with open(path, "w") as fh:
            fh.write(f"path: {DS}\ntrain: images/train\nval: {val}\nnames:\n")
            fh.writelines(f"  {i}: {n}\n" for i, n in enumerate(nms))

    y(f"{DS}/data.yaml", "images/val", names)
    y(f"{DS}/coco_val87.yaml", "coco_val/images", names)
    y(f"{DS}/coco_val80.yaml", "coco_val/images", names[:80])
    for t in glob.glob(f"{DS}/test_*"):
        y(f"{DS}/{os.path.basename(t)}.yaml", f"{os.path.basename(t)}/images", names)
    y(f"{DS}/test_exdark80.yaml", "test_exdark/images", names[:80])


def contact_sheets(recs):
    """24 random crops per new class (box drawn) for a visual label check."""
    os.makedirs(f"{ROOT}/sheets", exist_ok=True)
    for k in range(80, 87):
        items = [(r, b) for r in recs if r["split"] == "train" for b in r["boxes"] if b[0] == k]
        random.shuffle(items)
        sheet = Image.new("RGB", (6 * 160, 4 * 180), "white")
        for i, (r, b) in enumerate(items[:24]):
            img = Image.open(r["image"])
            W, H = img.size
            x0, y0, x1, y1 = b[1] * W, b[2] * H, b[3] * W, b[4] * H
            pw, ph = (x1 - x0) * 0.6 + 10, (y1 - y0) * 0.3 + 10
            crop = img.crop((x0 - pw, y0 - ph, x1 + pw, y1 + ph))
            ImageDraw.Draw(crop).rectangle(
                (pw, ph, pw + x1 - x0, ph + y1 - y0), outline="red", width=2
            )
            crop.thumbnail((156, 156))
            sheet.paste(crop, ((i % 6) * 160 + 2, (i // 6) * 180 + 2))
            ImageDraw.Draw(sheet).text(
                ((i % 6) * 160 + 2, (i // 6) * 180 + 160), r["src"], fill="black"
            )
        sheet.save(f"{ROOT}/sheets/{k}_{NAMES[k]}.jpg")


def setup():
    global NAMES
    NAMES = coco80() + NEW
    CID.update({n: i for i, n in enumerate(NAMES)})
    return NAMES


def load_records():
    """Records saved by main() (image paths already point at the resized copies)."""
    setup()
    return json.load(open(f"{ROOT}/records.json")), run_teacher([])


def main():
    setup()
    os.makedirs(RAW, exist_ok=True)
    recs = []
    for fn in (
        (mendeley, ood, pedlight, exdark, coco)
        if EVAL_ONLY
        else (mendeley, ood, pedlight, exdark, imvisible, coco)
    ):
        recs += fn()
    if EVAL_ONLY:  # splits were assigned on the full sources above, so they match the training run
        recs = [r for r in recs if r["split"] in ("test", "coco_val")]
    print("resizing / copying images", flush=True)

    def put(r):
        idir, _ = folder(r)
        os.makedirs(idir, exist_ok=True)
        r["image"] = f"{idir}/{r['stem']}.jpg"
        try:
            save_image(r["orig"], r["image"])
            return r
        except Exception as e:  # unreadable image
            print("skip", r["orig"], e)

    with ThreadPoolExecutor(8) as ex:
        recs = [r for r in ex.map(put, recs) if r]
    teacher = {} if EVAL_ONLY else run_teacher(recs)
    for r in recs:  # ImVisible front pedestrian light becomes a ground-truth-like box
        if r["src"] == "imvisible" and (fl := imvisible_front_light(r, teacher[r["stem"]])):
            r["boxes"].append(fl)
    # ImVisible images without a confident pick would only add unlabelled pedestrian lights (as background)
    recs = [r for r in recs if r["src"] != "imvisible" or r["boxes"]]
    counts = write_labels(recs, teacher)
    write_yaml(NAMES)
    json.dump(
        [{k: v for k, v in r.items() if k != "orig"} for r in recs],
        open(f"{ROOT}/records.json", "w"),
    )
    stats = {
        "images": dict(Counter(f"{r['src']}/{r['split']}" for r in recs)),
        "boxes": {k: dict(v) for k, v in sorted(counts.items())},
    }
    json.dump(stats, open(f"{ROOT}/stats.json", "w"), indent=1)
    print(json.dumps(stats["images"], indent=1))
    for k in ("train", "val"):
        tot = Counter()
        for s, c in counts.items():
            if s.endswith("/" + k):
                tot.update(c)
        print(k, "new-class boxes:", {n: tot[n] for n in NEW})
    for src in ("mendeley", "ood", "pedlight"):  # the new-class tests must not be empty
        got = {n: counts[f"{src}/test"][n] for n in NEW if counts[f"{src}/test"][n]}
        print(f"{src} test new-class boxes: {got}")
        assert got or len(recs) < 1000, f"{src}: no new-class boxes in its test split"
    if not EVAL_ONLY:
        contact_sheets(recs)
    print("done:", DS)


if __name__ == "__main__":
    main()
