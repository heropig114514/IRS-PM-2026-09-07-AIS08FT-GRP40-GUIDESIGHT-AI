# ruff: noqa: E501, E731, B023  (offline Kaggle experiment script, not runtime code)
"""Per-class COCO AP on LARGE objects only (area > 96^2 px, i.e. near objects for a walking user).

Official pycocotools COCOeval (bbox, areaRng 'large', maxDets 100) on COCO val2017, for the original yolo26n,
v1 and v2r2 (87-class models: only the 80 COCO outputs are scored). Images are scored at their original size.
Usage: python large_eval.py --v1 v1_r2.pt --v2r2 v2_r2.pt  -> $FT_OUT/large/results.{json,md}
"""

import argparse
import glob
import json
import os
import subprocess

import numpy as np

OUT = os.environ.get("FT_OUT", "/kaggle/working/ft")
COCO = "/tmp/coco"
HALF = __import__("torch").cuda.is_available()
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
    "parking meter",
    "bench",
    "dog",
    "cat",
    "suitcase",
    "chair",
    "potted plant",
    "dining table",
    "umbrella",
    "backpack",
    "handbag",
    "skateboard",
]


def sh(cmd):
    print("$", cmd, flush=True)
    subprocess.run(cmd, shell=True, check=True)


def data():
    os.makedirs(COCO, exist_ok=True)
    if not os.path.exists(f"{COCO}/annotations/instances_val2017.json"):
        sh(
            f"cd {COCO} && curl -fsSL --retry 5 -O http://images.cocodataset.org/annotations/annotations_trainval2017.zip "
            "&& unzip -q -o annotations_trainval2017.zip && rm annotations_trainval2017.zip"
        )
    if not os.path.isdir(f"{COCO}/val2017"):
        sh(
            f"cd {COCO} && curl -fsSL --retry 5 -O http://images.cocodataset.org/zips/val2017.zip "
            "&& unzip -q -o val2017.zip && rm val2017.zip"
        )
    imgs = sorted(glob.glob(f"{COCO}/val2017/*.jpg"))
    assert len(imgs) == 5000, len(imgs)
    return imgs


def detections(weights, imgs, cat_ids):
    from ultralytics import YOLO

    m, out = YOLO(weights), []
    for i in range(0, len(imgs), 64):
        chunk = imgs[i : i + 64]
        for p, r in zip(
            chunk,
            m.predict(
                chunk, imgsz=640, conf=0.001, max_det=300, half=HALF, verbose=False, batch=32
            ),
            strict=False,
        ):
            iid = int(os.path.basename(p)[:-4])
            for (x0, y0, x1, y1), s, c in zip(
                r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist(), strict=False
            ):
                if int(c) < 80:
                    out.append(
                        {
                            "image_id": iid,
                            "category_id": cat_ids[int(c)],
                            "bbox": [x0, y0, x1 - x0, y1 - y0],
                            "score": round(s, 5),
                        }
                    )
    return out


def evaluate(gt, dets):
    """{class name: {"all": AP, "large": AP}} + overall, from COCOeval precision [T, R, K, A, M]."""
    from pycocotools.cocoeval import COCOeval

    ev = COCOeval(gt, gt.loadRes(dets), "bbox")
    ev.evaluate()
    ev.accumulate()
    ev.summarize()
    prec = ev.eval[
        "precision"
    ]  # IoU x recall x class x area(all, small, medium, large) x maxDets(1, 10, 100)

    def ap(k, a):
        p = prec[:, :, k, a, 2]
        p = p[p > -1]
        return round(float(p.mean()), 4) if p.size else None

    per = {
        gt.cats[int(c)]["name"]: {"all": ap(k, 0), "large": ap(k, 3)}
        for k, c in enumerate(ev.params.catIds)
    }
    return {
        "map_all": round(float(ev.stats[0]), 4),
        "map_large": round(float(ev.stats[5]), 4),
        "per_class": per,
    }


def main():
    from pycocotools.coco import COCO as COCOapi

    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--v1", required=True)
    ap_.add_argument("--v2r2", required=True)
    a = ap_.parse_args()
    imgs = data()
    gt = COCOapi(f"{COCO}/annotations/instances_val2017.json")
    cat_ids = sorted(gt.getCatIds())
    os.makedirs(f"{OUT}/large", exist_ok=True)
    res = {}
    for name, w in (("original", "yolo26n.pt"), ("v1", a.v1), ("v2r2", a.v2r2)):
        res[name] = evaluate(gt, detections(w, imgs, cat_ids))
        print(name, res[name]["map_all"], res[name]["map_large"], flush=True)
    json.dump(res, open(f"{OUT}/large/results.json", "w"), indent=1)

    L = [
        "# COCO val2017, pycocotools: all objects vs LARGE objects (area > 96^2)",
        "",
        "| model | mAP all | mAP large |",
        "|---|---|---|",
    ]
    L += [f"| {n} | {r['map_all']} | {r['map_large']} |" for n, r in res.items()]
    L += [
        "",
        "| class | orig all | orig large | v1 large (change) | v2 large (change) | v1 all (change) |",
        "|---|---|---|---|---|---|",
    ]
    o, v1, v2 = res["original"]["per_class"], res["v1"]["per_class"], res["v2r2"]["per_class"]
    d = lambda x, y: "-" if x is None or y is None else f"{x - y:+.3f}"
    for n in WALK:
        L.append(
            f"| {n} | {o[n]['all']} | {o[n]['large']} | {v1[n]['large']} ({d(v1[n]['large'], o[n]['large'])}) | "
            f"{v2[n]['large']} ({d(v2[n]['large'], o[n]['large'])}) | {d(v1[n]['all'], o[n]['all'])} |"
        )
    walk_l = [
        (v1[n]["large"] - o[n]["large"], v2[n]["large"] - o[n]["large"])
        for n in WALK
        if o[n]["large"] is not None and v1[n]["large"] is not None
    ]
    L += [
        "",
        f"mean change on the {len(walk_l)} walking classes, LARGE: v1 {np.mean([x for x, _ in walk_l]):+.4f}, "
        f"v2 {np.mean([y for _, y in walk_l]):+.4f}",
    ]
    open(f"{OUT}/large/results.md", "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
