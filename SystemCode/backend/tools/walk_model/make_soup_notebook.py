# ruff: noqa: E501, E731, B023  (offline Kaggle experiment script, not runtime code)
"""Build soup-eval-kaggle.ipynb: eval-only data (test splits + COCO val) then soup_eval.py on uploaded weights."""

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
        "# Training-free options: weight interpolation vs per-class two-model pick\n"
        "Needs: GPU T4 x2, Internet on, secret `ROBOFLOW_API_KEY`, input dataset with v1_r2.pt / v2_r1.pt / v2_r2.pt. "
        "About 2 hours. Run with Save Version -> Save & Run All.\n"
    ),
    code(
        "!pip install -q ultralytics==8.4.171 roboflow gdown 2>&1 | tail -2\n!nvidia-smi -L\n"
        "import os\nos.makedirs('/kaggle/working/code', exist_ok=True)"
    ),
]
for f in ("imv_helpers.py", "prepare_data.py", "evaluate.py", "soup_eval.py"):
    cells.append(code(f"%%writefile /kaggle/working/code/{f}\n" + open(f"{HERE}/{f}").read()))
cells += [
    code("""import subprocess, glob, time
from kaggle_secrets import UserSecretsClient
os.environ['ROBOFLOW_API_KEY'] = UserSecretsClient().get_secret('ROBOFLOW_API_KEY')
os.environ['FT_EVAL_ONLY'] = '1'
os.makedirs('/kaggle/working/logs', exist_ok=True)
def run(cmd, log):
    t = time.time()
    p = subprocess.run(f"{cmd} > {log} 2>&1", shell=True, cwd="/kaggle/working/code")
    print(open(log).read()[-8000:])
    print(f"[{cmd}] exit {p.returncode} in {(time.time() - t) / 3600:.2f} h")
    if p.returncode:
        raise RuntimeError(f"step failed: {cmd}")
w = {os.path.basename(p): p for p in glob.glob('/kaggle/input/**/*.pt', recursive=True)}
print(w)
assert {'v1_r2.pt', 'v2_r1.pt', 'v2_r2.pt'} <= set(w), 'attach the weights dataset'
run('python prepare_data.py', '/kaggle/working/logs/prepare_eval.log')"""),
    code(
        "run(f\"python soup_eval.py --v1 {w['v1_r2.pt']} --v2r1 {w['v2_r1.pt']} --v2r2 {w['v2_r2.pt']}\", "
        "'/kaggle/working/logs/soup_eval.log')"
    ),
    code(
        "!cd /kaggle/working && zip -qr soup-results.zip ft/soup/results.md ft/soup/results.json logs && ls -la"
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
json.dump(nb, open(f"{HERE}/soup-eval-kaggle.ipynb", "w"), indent=1)
print(len(cells), "cells")
