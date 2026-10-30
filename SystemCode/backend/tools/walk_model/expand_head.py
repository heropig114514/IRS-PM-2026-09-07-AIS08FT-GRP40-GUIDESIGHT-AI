# ruff: noqa: E501, E731, B023  (offline Kaggle experiment script, not runtime code)
"""Expand the COCO yolo26n checkpoint from 80 to 87 classes WITHOUT losing what it already knows.

Plain Ultralytics fine-tuning with a new class count re-initialises every classification-head
tensor whose shape changed (in yolo26n the hidden width of the class branch is also tied to the
class count), so the model would have to re-learn all 80 COCO classes. Instead:
  * every old tensor is copied into the leading slice of the new one;
  * weights from the NEW hidden channels into OLD outputs are zeroed;
so at step 0 the 80 COCO scores are exactly the original ones (checked by verify()), and only the
7 new class outputs start from the default initialisation (Ultralytics' prior bias).
Usage: python expand_head.py [out.pt]  (default yolo26n-87init.pt)
"""

import sys

import torch
from ultralytics import YOLO
from ultralytics.nn.tasks import DetectionModel

NEW = ["pole", "bollard", "traffic_cone", "stairs", "ped_red", "ped_green", "ped_off"]


def expand(src="yolo26n.pt", nc=87):
    old = YOLO(src).model.float()
    new = DetectionModel(dict(old.yaml), nc=nc, verbose=False)
    so, sn = old.state_dict(), new.state_dict()
    for k, t in sn.items():
        o = so[k]
        if o.shape == t.shape:
            sn[k] = o.clone()
            continue
        t = t.clone()
        t[tuple(slice(0, s) for s in o.shape)] = o
        if t.dim() >= 2 and t.shape[1] > o.shape[1] and o.shape[1] > 1:
            t[: o.shape[0], o.shape[1] :] = 0  # new hidden channels must not change old outputs
        sn[k] = t
    new.load_state_dict(sn)
    new.names = {i: n for i, n in enumerate([old.names[i] for i in range(80)] + NEW)}
    new.args = getattr(old, "args", {})
    return old.eval(), new.eval()


def verify(old, new):
    torch.manual_seed(0)
    x = torch.rand(2, 3, 640, 640)
    with torch.no_grad():
        a, b = old(x), new(x)
    a = a[0] if isinstance(a, (list, tuple)) else a
    b = b[0] if isinstance(b, (list, tuple)) else b
    print("output shapes", tuple(a.shape), tuple(b.shape))
    if a.shape[-1] == 6:  # end-to-end [B, 300, 6] = box, score, class
        box = (a[..., :4] - b[..., :4]).abs().max().item()
        cls = (a[..., 4:] - b[..., 4:]).abs().max().item()
    else:  # raw [B, 4 + nc, anchors]
        box = (a[:, :4] - b[:, :4]).abs().max().item()
        cls = (a[:, 4:84] - b[:, 4:84]).abs().max().item()
    print(f"max diff vs original: boxes {box:.2e}, COCO class scores {cls:.2e}")
    assert box < 1e-4 and cls < 1e-4, "expanded model changed the original COCO outputs"


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "yolo26n-87init.pt"
    old, new = expand()
    verify(old, new)
    torch.save(
        {"model": new.half(), "train_args": {}, "epoch": -1, "date": None, "version": None}, out
    )
    print("saved", out)
