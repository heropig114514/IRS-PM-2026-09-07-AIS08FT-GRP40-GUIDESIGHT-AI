# ruff: noqa: E501, E731, B023  (offline Kaggle experiment script, not runtime code)
"""Build finetune-kaggle.ipynb: scripts embedded with %%writefile, then prepare -> round 1 -> round 2 -> eval."""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
md = lambda s: {"cell_type": "markdown", "metadata": {}, "source": s}
code = lambda s: {
    "cell_type": "code",
    "metadata": {},
    "execution_count": None,
    "outputs": [],
    "source": s,
}
cells = [
    md(
        "# yolo26n walking-aid fine-tune (87 classes)\n"
        "Needs: GPU T4 x2, Internet on, Kaggle secret `ROBOFLOW_API_KEY`. Run with **Save Version -> Save & Run All** "
        "(background). Total time is capped at about 11 hours (round 1: up to 5 h, round 2: up to 3.5 h).\n"
    ),
    code(
        "!pip install -q ultralytics==8.4.171 roboflow gdown 2>&1 | tail -2\n!nvidia-smi -L\n!nproc; free -g | head -2\n!df -h /tmp /kaggle/working | tail -2"
    ),
    code("import os\nos.makedirs('/kaggle/working/code', exist_ok=True)"),
]
for f in ("imv_helpers.py", "expand_head.py", "prepare_data.py", "train.py", "evaluate.py"):
    cells.append(code(f"%%writefile /kaggle/working/code/{f}\n" + open(f"{HERE}/{f}").read()))
RUN = """import subprocess, time, os
T0 = time.time()
def run(cmd, log):
    \"\"\"Run a step with full output in a log file; stop Run All on failure (no exit code swallowed by a pipe).\"\"\"
    t = time.time()
    p = subprocess.run(f"{cmd} > {log} 2>&1", shell=True, cwd="/kaggle/working/code")
    print(open(log).read()[-6000:])
    print(f"[{cmd}] exit {p.returncode} in {(time.time() - t) / 3600:.2f} h; session {(time.time() - T0) / 3600:.2f} h")
    if p.returncode:
        raise RuntimeError(f"step failed: {cmd} (see {log})")
def hours_left():
    return 11.3 - (time.time() - T0) / 3600   # Kaggle stops at 12 h; keep margin for eval + saving
"""
cells += [
    code(
        RUN + "\nfrom kaggle_secrets import UserSecretsClient\n"
        "os.environ['ROBOFLOW_API_KEY'] = UserSecretsClient().get_secret('ROBOFLOW_API_KEY')\n"
        "subprocess.run('python -c \"import ultralytics, roboflow, cv2, torch; print(ultralytics.__version__, cv2.__version__, torch.__version__)\"', shell=True, check=True)\n"
        "os.makedirs('/kaggle/working/logs', exist_ok=True)\n"
        "run('python prepare_data.py', '/kaggle/working/logs/prepare.log')"
    ),
    code(
        "h1 = max(1.0, min(5.0, hours_left() - 3.5 - 0.9))\nrun(f'python train.py --round 1 --hours {h1:.2f}', '/kaggle/working/logs/round1.log')"
    ),
    code(
        "h2 = max(0.5, min(3.5, hours_left() - 0.9))\nrun(f'python train.py --round 2 --hours {h2:.2f}', '/kaggle/working/logs/round2.log')"
    ),
    code(
        "run('python evaluate.py --weights /kaggle/working/ft/yolo26n-walk-r1.pt --coco-only --tag _r1', '/kaggle/working/logs/eval_r1.log')\n"
        "run('python evaluate.py --weights /kaggle/working/ft/yolo26n-walk-r2.pt', '/kaggle/working/logs/eval.log')"
    ),
    code(
        "!cp -r /tmp/ft/sheets /tmp/ft/stats.json /kaggle/working/ft/ 2>/dev/null; "
        "cd /kaggle/working/ft && zip -qr /kaggle/working/finetune-results.zip *.pt eval sheets stats.json pseudo_counts.json "
        "r1/results.csv r2/results.csv r1/*.png r2/*.png ../logs && ls -la /kaggle/working"
    ),
]
nb = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
        "language_info": {"name": "python"},
    },
    "nbformat": 4,
    "nbformat_minor": 4,
}
json.dump(nb, open(f"{HERE}/finetune-kaggle.ipynb", "w"), indent=1)
print([c["source"][:60].split("\n")[0] for c in cells])
