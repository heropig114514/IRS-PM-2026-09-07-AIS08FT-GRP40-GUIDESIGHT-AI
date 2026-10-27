# ruff: noqa: E501, E731, B023  (offline Kaggle experiment script, not runtime code)
"""Training-free options for the forgetting / new-class trade-off, evaluated with ONE metric code path.

Checkpoints (all 87-class models start from the same head-expanded COCO yolo26n):
  original  official COCO yolo26n (80 classes)
  v1        whole-model fine-tune (strong new classes + night, COCO mAP50-95 -0.044)
  v2r1/v2r2 frozen backbone + distillation (COCO kept, weaker new classes); backbone identical to original
Candidates:
  soup_a    weight interpolation  a * v1 + (1 - a) * v2r2  (one model, same latency)
  pick_*    two models, each class taken from one model (disjoint class sets, so no duplicate boxes):
            pick_new        original for the 80 COCO classes, v1 for the 7 new classes
            pick_new_street original, except person / bicycle / car / motorcycle and the new classes from v1
            pick_v2new      original for COCO, v2r2 for the new classes (v2 shares the original backbone, so this
                            could later run as one backbone + two heads)
Metrics (same matching as Ultralytics val: IoU 0.50:0.95, greedy by IoU, per-class AP, conf >= 0.001):
  COCO val2017 (never trained on), ExDark test (night), new-class tests (Mendeley / OOD / pedlight, GT classes only),
  a walking-relevant view, ImVisible red->green (single models only), and inference time (1 vs 2 models).
Usage: python soup_eval.py --v1 v1_r2.pt --v2r1 v2_r1.pt --v2r2 v2_r2.pt
"""

import argparse
import glob
import json
import os
import statistics
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evaluate as E  # noqa: E402
import prepare_data as P  # noqa: E402

OUT = os.environ.get("FT_OUT", "/kaggle/working/ft")
HALF = __import__("torch").cuda.is_available()
IOUV = np.linspace(0.5, 0.95, 10)
WALK = [
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "bus",
    "truck",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "bench",
    "dog",
]
STREET = ["person", "bicycle", "car", "motorcycle"]
SETS = {
    "coco_val": "coco_val",
    "exdark": "test_exdark",
    "mendeley": "test_mendeley",
    "ood": "test_ood",
    "pedlight": "test_pedlight",
}


def gt_of(folder):
    """{image path: (W, H, gt array [n, 5] = cls, x0, y0, x1, y1 in pixels)}"""
    out = {}
    for img in sorted(glob.glob(f"{P.DS}/{folder}/images/*.jpg")):
        W, H = Image.open(img).size
        lab = img.replace("/images/", "/labels/")[:-4] + ".txt"
        rows = (
            [list(map(float, ln.split())) for ln in open(lab).read().split("\n") if ln.strip()]
            if os.path.exists(lab)
            else []
        )
        g = np.array(
            [
                [c, (x - w / 2) * W, (y - h / 2) * H, (x + w / 2) * W, (y + h / 2) * H]
                for c, x, y, w, h in rows
            ]
        ).reshape(-1, 5)
        out[img] = (W, H, g)
    return out


def predict(model, imgs):
    """{image: [n, 6] = x0, y0, x1, y1, conf, cls}"""
    out = {}
    for i in range(0, len(imgs), 64):
        chunk = imgs[i : i + 64]
        for p, r in zip(
            chunk,
            model.predict(
                chunk, imgsz=640, conf=0.001, max_det=300, half=HALF, verbose=False, batch=32
            ),
            strict=False,
        ):
            b = r.boxes
            out[p] = np.concatenate(
                [b.xyxy.cpu().numpy(), b.conf.cpu().numpy()[:, None], b.cls.cpu().numpy()[:, None]],
                1,
            )
    return out


def box_iou(a, b):
    lt = np.maximum(a[:, None, :2], b[None, :, :2])
    rb = np.minimum(a[:, None, 2:], b[None, :, 2:])
    inter = np.clip(rb - lt, 0, None).prod(2)
    area = lambda x: (x[:, 2] - x[:, 0]) * (x[:, 3] - x[:, 1])
    return inter / (area(a)[:, None] + area(b)[None, :] - inter + 1e-9)


def match(pred, gt):
    """Ultralytics DetectionValidator.match_predictions (non-scipy branch)."""
    correct = np.zeros((len(pred), len(IOUV)), bool)
    if not len(pred) or not len(gt):
        return correct
    iou = box_iou(gt[:, 1:], pred[:, :4]) * (gt[:, :1] == pred[None, :, 5])
    for i, t in enumerate(IOUV):
        m = np.nonzero(iou >= t)
        if m[0].size:
            mt = np.array(m).T
            if m[0].size > 1:
                mt = mt[iou[mt[:, 0], mt[:, 1]].argsort()[::-1]]
                mt = mt[np.unique(mt[:, 1], return_index=True)[1]]
                mt = mt[np.unique(mt[:, 0], return_index=True)[1]]
            correct[mt[:, 1].astype(int), i] = True
    return correct


def score(preds, gts, classes=None):
    """mAP50-95 over GT classes (optionally restricted) + per-class {ap50_95, ap50}."""
    from ultralytics.utils.metrics import ap_per_class

    tp, conf, pc, tc = [], [], [], []
    for img, (_, _, g) in gts.items():
        p = preds.get(img, np.zeros((0, 6)))
        if classes is not None:
            p, g = p[np.isin(p[:, 5], classes)], g[np.isin(g[:, 0], classes)]
        tp.append(match(p, g))
        conf.append(p[:, 4])
        pc.append(p[:, 5])
        tc.append(g[:, 0])
    tc = np.concatenate(tc)
    if not len(tc):
        return {"map50_95": None, "per_class": {}}
    res = ap_per_class(np.concatenate(tp), np.concatenate(conf), np.concatenate(pc), tc)
    ap, cls = res[5], res[6]
    per = {
        P.NAMES[int(c)]: {
            "ap50_95": round(float(ap[i].mean()), 4),
            "ap50": round(float(ap[i, 0]), 4),
        }
        for i, c in enumerate(cls)
    }
    return {"map50_95": round(float(ap.mean()), 4), "per_class": per}


def combine(a, b, b_classes):
    """Boxes of b_classes from b, all other classes from a."""
    out = {}
    for img in a:
        pa, pb = a[img], b.get(img, np.zeros((0, 6)))
        out[img] = np.concatenate(
            [pa[~np.isin(pa[:, 5], b_classes)], pb[np.isin(pb[:, 5], b_classes)]]
        )
    return out


def make_soup(a_path, b_path, alpha, out_path):
    from ultralytics import YOLO

    m = YOLO(a_path)
    sa, sb = m.model.float().state_dict(), YOLO(b_path).model.float().state_dict()
    m.model.load_state_dict(
        {k: alpha * sa[k] + (1 - alpha) * sb[k] if sa[k].is_floating_point() else sa[k] for k in sa}
    )
    m.save(out_path)
    return out_path


def latency(paths, n=200):
    """Median Ultralytics inference ms per 640 frame for each model (batch 1)."""
    from ultralytics import YOLO

    img = sorted(glob.glob(f"{P.DS}/coco_val/images/*.jpg"))[0]
    out = {}
    for name, p in paths.items():
        m = YOLO(p)
        for _ in range(20):
            m.predict(img, imgsz=640, half=HALF, verbose=False)
        out[name] = round(
            statistics.median(
                m.predict(img, imgsz=640, half=HALF, verbose=False)[0].speed["inference"]
                for _ in range(n)
            ),
            2,
        )
    return out


def main():
    from ultralytics import YOLO

    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", required=True)
    ap.add_argument("--v2r1", required=True)
    ap.add_argument("--v2r2", required=True)
    ap.add_argument("--alphas", default="0.3,0.5,0.7")
    a = ap.parse_args()
    P.setup()
    os.makedirs(f"{OUT}/soup", exist_ok=True)
    models = {"original": "yolo26n.pt", "v1": a.v1, "v2r1": a.v2r1, "v2r2": a.v2r2}
    for al in map(float, a.alphas.split(",")):
        models[f"soup_{al}"] = make_soup(a.v1, a.v2r2, al, f"{OUT}/soup/soup_{al}.pt")
    gts = {k: gt_of(f) for k, f in SETS.items() if os.path.isdir(f"{P.DS}/{f}/images")}
    print({k: len(v) for k, v in gts.items()}, flush=True)

    preds = {}
    for name, path in models.items():
        m = YOLO(path)
        preds[name] = {k: predict(m, list(g)) for k, g in gts.items()}
        print("predicted", name, flush=True)
    cid = P.CID
    new = [cid[n] for n in P.NEW]
    street = [cid[n] for n in STREET]
    for k in gts:
        preds.setdefault("pick_new", {})[k] = combine(preds["original"][k], preds["v1"][k], new)
        preds.setdefault("pick_new_street", {})[k] = combine(
            preds["original"][k], preds["v1"][k], new + street
        )
        preds.setdefault("pick_v2new", {})[k] = combine(preds["original"][k], preds["v2r2"][k], new)

    res = {}
    for name, pr in preds.items():
        r = {
            "coco_val": score(pr["coco_val"], gts["coco_val"]),
            "coco_walk": score(pr["coco_val"], gts["coco_val"], [cid[n] for n in WALK]),
            "exdark": score(pr["exdark"], gts["exdark"]),
            "new": {
                s: score(pr[s], gts[s], new)["per_class"]
                for s in ("mendeley", "ood", "pedlight")
                if s in gts
            },
        }
        res[name] = r
        print(
            name,
            "COCO",
            r["coco_val"]["map50_95"],
            "walk",
            r["coco_walk"]["map50_95"],
            "ExDark",
            r["exdark"]["map50_95"],
            flush=True,
        )
    lights = {}
    for name in [n for n in models if n != "original"]:
        lights[name] = E.imvisible(models[name])
        print("imvisible", name, lights[name]["crosswalk"]["calibrated_on_validation"], flush=True)
    lat = latency({"original": "yolo26n.pt", "v1": a.v1})
    json.dump(
        {"results": res, "imvisible": lights, "latency_ms": lat},
        open(f"{OUT}/soup/results.json", "w"),
        indent=1,
    )

    # ------------------------------------------------------------------ markdown summary
    L = [
        "# Training-free options",
        "",
        f"inference ms per frame (T4, Ultralytics, batch 1): {lat}; "
        "two-model candidates cost original + v1",
        "",
    ]
    hdr = ["candidate", "models", "COCO mAP50-95", "COCO walk-classes", "ExDark (night)"]
    newc = [
        "pole(mendeley)",
        "stairs(mendeley)",
        "stairs(ood)",
        "bollard(ood)",
        "ped_red",
        "ped_green",
    ]
    L += [
        "| " + " | ".join(hdr + [f"{c} AP50" for c in newc]) + " |",
        "|---" * (len(hdr) + len(newc)) + "|",
    ]
    key = [
        ("mendeley", "pole"),
        ("mendeley", "stairs"),
        ("ood", "stairs"),
        ("ood", "bollard"),
        ("pedlight", "ped_red"),
        ("pedlight", "ped_green"),
    ]
    for name, r in res.items():
        nm = 2 if name.startswith("pick_") else 1
        cells = [
            name,
            str(nm),
            str(r["coco_val"]["map50_95"]),
            str(r["coco_walk"]["map50_95"]),
            str(r["exdark"]["map50_95"]),
        ]
        cells += [str(r["new"].get(s, {}).get(c, {}).get("ap50", "-")) for s, c in key]
        L.append("| " + " | ".join(cells) + " |")
    L += [
        "",
        "| COCO per class (AP50-95) | " + " | ".join(WALK) + " |",
        "|---" * (len(WALK) + 1) + "|",
    ]
    for name, r in res.items():
        L.append(
            f"| {name} | "
            + " | ".join(
                str(r["coco_val"]["per_class"].get(n, {}).get("ap50_95", "-")) for n in WALK
            )
            + " |"
        )
    L += [
        "",
        "| ExDark per class (AP50-95) | " + " | ".join(STREET) + " |",
        "|---" * (len(STREET) + 1) + "|",
    ]
    for name, r in res.items():
        L.append(
            f"| {name} | "
            + " | ".join(
                str(r["exdark"]["per_class"].get(n, {}).get("ap50_95", "-")) for n in STREET
            )
            + " |"
        )
    L += [
        "",
        "| ImVisible test (crosswalk pick) | tau (val-calibrated) | red->green | green said green | "
        "red->green @0.5 | green said green @0.5 |",
        "|---|---|---|---|---|---|",
    ]
    for name, v in lights.items():
        c = v["crosswalk"]["calibrated_on_validation"]
        f5 = [t for t in v["crosswalk"]["fixed_tau"] if t["tau"] == 0.5][0]
        L.append(
            f"| {name} | {c['tau']} | {c['red_to_green']} | {c['green_said_green']} | {f5['red_to_green']} | "
            f"{f5['green_said_green']} |"
        )
    L += [
        "",
        "pick_* candidates take pedestrian-light colours from v1, so their ImVisible numbers equal v1's.",
        "Light-crop classifier baseline: tau 0.95, red->green 4/226, green said green 0.61.",
    ]
    open(f"{OUT}/soup/results.md", "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
