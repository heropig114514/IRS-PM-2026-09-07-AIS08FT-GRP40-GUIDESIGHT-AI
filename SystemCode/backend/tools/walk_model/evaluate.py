# ruff: noqa: E501, E731, B023  (offline Kaggle experiment script, not runtime code)
"""Evaluate the fine-tuned 87-class model against the original yolo26n (+ light-crop classifier numbers).

1. Forgetting gate: COCO val2017 (5,000 images, never trained on) mAP50-95, original vs fine-tuned,
   same Ultralytics protocol for both.
2. Night: ExDark official test split, per class (person / bicycle / motorcycle / car / bus ...).
3. New classes: AP50 / recall on the held-out scene-grouped test splits of Mendeley, OOD, pedlight.
4. Pedestrian light colour on ImVisible TEST (never trained on), single frame:
   detection = 640 full frame + centre ROI (x 30-70 %, y 0-55 %), like the backend;
   the light is picked at the crosswalk far end (same rule as the classifier study) and also with the
   runtime rule (vertical light box nearest the image centre line). Decision: "green" only if the
   picked light's ped_green score >= tau and beats ped_red; "red" if ped_red is present; else unknown.
   tau is calibrated on ImVisible VALIDATION (red->green <= 0.5 %), plus a fixed-tau table.
Usage: python evaluate.py --weights /kaggle/working/ft/yolo26n-walk-r2.pt
Outputs: $FT_OUT/eval/results.json and results.md
"""

import argparse
import json
import os
import sys

import torch
from PIL import Image
from torchvision.ops import nms

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prepare_data as P  # noqa: E402

HALF = __import__("torch").cuda.is_available()
OUT = os.environ.get("FT_OUT", "/kaggle/working/ft")
TAUS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
ROI = (0.30, 0.0, 0.70, 0.55)
CLASSIFIER_BASELINE = {"tau": 0.95, "red_to_green": "4/226", "green_said_green": 0.61}


def val(model_path, yaml, names_of_interest=None):
    from ultralytics import YOLO

    m = YOLO(model_path).val(data=yaml, imgsz=640, batch=32, half=HALF, plots=False, verbose=False)
    names = m.names
    per = {}
    for i, c in enumerate(m.box.ap_class_index):
        n = names[int(c)]
        if names_of_interest is None or n in names_of_interest:
            per[n] = {
                "ap50_95": round(float(m.box.ap[i]), 4),
                "ap50": round(float(m.box.ap50[i]), 4),
                "recall": round(float(m.box.r[i]), 4),
                "precision": round(float(m.box.p[i]), 4),
            }
    return {
        "map50_95": round(float(m.box.map), 4),
        "map50": round(float(m.box.map50), 4),
        "per_class": per,
    }


def light_boxes(model, img):
    """Light boxes [x0, y0, x1, y1, conf, cls] in pixels from full frame + centre ROI."""
    W, H = img.size
    rx0, ry0, rx1, ry1 = int(ROI[0] * W), int(ROI[1] * H), int(ROI[2] * W), int(ROI[3] * H)
    light = {P.CID[n] for n in ("traffic light", "ped_red", "ped_green", "ped_off")}
    out = []
    for im, (ox, oy) in ((img, (0, 0)), (img.crop((rx0, ry0, rx1, ry1)), (rx0, ry0))):
        r = model.predict(im, imgsz=640, conf=0.05, verbose=False, half=HALF)[0]
        for b, s, c in zip(
            r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist(), strict=False
        ):
            if int(c) in light:
                out.append([b[0] + ox, b[1] + oy, b[2] + ox, b[3] + oy, s, int(c)])
    if not out:
        return []
    t = torch.tensor(out)
    keep = []
    for c in t[:, 5].unique():  # class-aware NMS among lights, as in the backend
        idx = (t[:, 5] == c).nonzero().flatten()
        keep += idx[nms(t[idx, :4], t[idx, 4], 0.5)].tolist()
    return [out[i] for i in keep]


def light_scores(boxes, target):
    """Max score per light class among boxes overlapping the picked location."""
    sc = {}
    for b in boxes:
        if P.iou(b[:4], target[:4]) >= 0.3:
            sc[b[5]] = max(sc.get(b[5], 0), b[4])
    return sc


def pick_crosswalk(boxes, fx, fy):
    above = [b for b in boxes if (b[1] + b[3]) / 2 < fy] or boxes
    return min(above, key=lambda b: abs((b[0] + b[2]) / 2 - fx)) if above else None


def pick_runtime(boxes, W):
    vertical = [b for b in boxes if (b[3] - b[1]) > (b[2] - b[0])]
    return min(vertical, key=lambda b: abs((b[0] + b[2]) / 2 - W / 2)) if vertical else None


def decide(sc, tau):
    g, r = sc.get(P.CID["ped_green"], 0), sc.get(P.CID["ped_red"], 0)
    if g >= tau and g > r:
        return "green"
    if r > 0:
        return "red"
    return "unknown"


def imvisible(weights):
    from ultralytics import YOLO

    model = YOLO(weights)
    rows, files = P.imvisible_rows()
    res = {}
    for split in ("validation", "testing"):
        items = []
        for row in rows[split]:
            mode = int(row["mode"])
            f = files.get(row["file"].lower())
            if mode not in (0, 1, 2) or not f:
                continue
            img = Image.open(f).convert("RGB")
            W, H = img.size
            boxes = light_boxes(model, img)
            t1 = (
                pick_crosswalk(boxes, float(row["x1"]) / 4032 * W, float(row["y1"]) / 3024 * H)
                if boxes
                else None
            )
            t2 = pick_runtime(boxes, W) if boxes else None
            items.append(
                {
                    "truth": "red" if mode == 0 else "green",
                    "crosswalk": light_scores(boxes, t1) if t1 else {},
                    "runtime": light_scores(boxes, t2) if t2 else {},
                }
            )
        res[split] = items

    def rates(items, key, tau):
        red = [decide(i[key], tau) for i in items if i["truth"] == "red"]
        green = [decide(i[key], tau) for i in items if i["truth"] == "green"]
        return {
            "tau": tau,
            "red_to_green": f"{red.count('green')}/{len(red)}",
            "red_said_red": round(red.count("red") / max(1, len(red)), 3),
            "green_said_green": round(green.count("green") / max(1, len(green)), 3),
            "unknown": red.count("unknown") + green.count("unknown"),
        }

    out = {}
    for key in ("crosswalk", "runtime"):
        reds = sorted(
            (i[key].get(P.CID["ped_green"], 0) for i in res["validation"] if i["truth"] == "red"),
            reverse=True,
        )
        k = int(0.005 * len(reds))
        tau = round(min(0.99, max(0.3, reds[k] + 1e-3)) if len(reds) > k else 0.3, 3)
        out[key] = {
            "calibrated_on_validation": rates(res["testing"], key, tau),
            "fixed_tau": [rates(res["testing"], key, t) for t in TAUS],
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default=f"{OUT}/yolo26n-walk-r2.pt")
    ap.add_argument("--original", default="yolo26n.pt")
    ap.add_argument(
        "--coco-only",
        action="store_true",
        help="only COCO val2017 + ExDark (quick check of a checkpoint)",
    )
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    P.setup()
    os.makedirs(f"{OUT}/eval", exist_ok=True)
    key = [
        "person",
        "bicycle",
        "motorcycle",
        "car",
        "bus",
        "truck",
        "traffic light",
        "bench",
        "dog",
    ]
    r = {"weights": a.weights}
    r["coco_val"] = {
        "original": val(a.original, f"{P.DS}/coco_val80.yaml", key),
        "finetuned": val(a.weights, f"{P.DS}/coco_val87.yaml", key),
    }
    r["exdark_test"] = {
        "original": val(a.original, f"{P.DS}/test_exdark80.yaml"),
        "finetuned": val(a.weights, f"{P.DS}/test_exdark.yaml"),
    }
    if a.coco_only:
        json.dump(r, open(f"{OUT}/eval/results{a.tag}.json", "w"), indent=1)
        print(
            json.dumps(
                {
                    k: {
                        "original": v["original"]["map50_95"],
                        "finetuned": v["finetuned"]["map50_95"],
                    }
                    for k, v in r.items()
                    if k != "weights"
                }
            )
        )
        return
    r["new_classes"] = {
        s: val(a.weights, f"{P.DS}/test_{s}.yaml", set(P.NEW))
        for s in ("mendeley", "ood", "pedlight")
        if os.path.exists(f"{P.DS}/test_{s}.yaml")
    }
    r["imvisible_light"] = imvisible(a.weights)
    r["imvisible_light"]["classifier_baseline"] = CLASSIFIER_BASELINE
    json.dump(r, open(f"{OUT}/eval/results.json", "w"), indent=1)

    lines = [
        "# Fine-tune evaluation",
        "",
        f"weights: `{a.weights}`",
        "",
        "| COCO val2017 | mAP50-95 | " + " | ".join(key) + " |",
        "|---" * (len(key) + 2) + "|",
    ]
    for k in ("original", "finetuned"):
        v = r["coco_val"][k]
        lines.append(
            f"| {k} | {v['map50_95']} | "
            + " | ".join(str(v["per_class"].get(n, {}).get("ap50_95", "-")) for n in key)
            + " |"
        )
    ex = sorted(r["exdark_test"]["original"]["per_class"])
    lines += [
        "",
        "| ExDark test (night) | mAP50-95 | " + " | ".join(ex) + " |",
        "|---" * (len(ex) + 2) + "|",
    ]
    for k in ("original", "finetuned"):
        v = r["exdark_test"][k]
        lines.append(
            f"| {k} | {v['map50_95']} | "
            + " | ".join(str(v["per_class"].get(n, {}).get("ap50_95", "-")) for n in ex)
            + " |"
        )
    lines += ["", "| New-class test | class | AP50 | recall | precision |", "|---|---|---|---|---|"]
    for s, v in r["new_classes"].items():
        for n, m in v["per_class"].items():
            lines.append(f"| {s} | {n} | {m['ap50']} | {m['recall']} | {m['precision']} |")
    for k in ("crosswalk", "runtime"):
        lines += [
            "",
            f"ImVisible test, pick = {k}",
            "",
            "| tau | red->green | red said red | green said green | unknown |",
            "|---|---|---|---|---|",
        ]
        v = r["imvisible_light"][k]
        for cal, t in [(True, v["calibrated_on_validation"])] + [
            (False, t) for t in v["fixed_tau"]
        ]:
            lines.append(
                f"| {t['tau']}{' (calibrated on val)' if cal else ''} | {t['red_to_green']} | {t['red_said_red']} | {t['green_said_green']} | {t['unknown']} |"
            )
    lines += ["", f"Light-crop classifier baseline (crosswalk pick): {CLASSIFIER_BASELINE}"]
    open(f"{OUT}/eval/results.md", "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
