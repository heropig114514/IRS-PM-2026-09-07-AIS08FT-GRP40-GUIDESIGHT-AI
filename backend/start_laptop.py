"""Responsibilities: One portable command for isolated GPU setup or worker-plus-tunnel startup.
Implementation: Detect NVIDIA profile, optionally install pinned packages, use isolated venv Python.
Related Modules: tools.laptop_platform, tools.run_laptop, requirements-laptop*.txt.
Declaration Index: main.
Variable Index: BASE_DIR — backend path independent of caller's current directory.
"""

import argparse
import logging
import os
import subprocess
import sys
import venv
from pathlib import Path

from tools.laptop_platform import runtime_profile, select_gpu

BASE_DIR = Path(__file__).resolve().parent


def main():
    """Functionality: Bootstrap/launch node. Inputs: --setup and optional physical --gpu-index.
    Outputs: installation or combined startup/exit status; Logic: choose pinned hardware profile.
    Constraints: Python3.12 required; installs only with --setup, no global changes/CPU fallback.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", action="store_true", help="Install isolated GPU dependencies")
    parser.add_argument("--gpu-index", type=int, help="Explicit physical NVIDIA GPU index")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Use Python 3.12 to preserve the project's pinned dependency contract")
    environment = BASE_DIR / ".venv-laptop"
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if args.setup:
        index = args.gpu_index
        if index is None and os.environ.get("LAPTOP_GPU_INDEX", "").strip():
            index = int(os.environ["LAPTOP_GPU_INDEX"])
        gpu = select_gpu(index)
        profile = runtime_profile(gpu)
        logging.info(
            "Setup GPU index=%d name=%s sm=%s TensorRT=%s CUDA=%s",
            gpu["index"],
            gpu["name"],
            gpu["sm"],
            profile["trt"],
            profile["cuda"],
        )
        if not executable.exists():
            venv.EnvBuilder(with_pip=True).create(environment)
        subprocess.run(
            [str(executable), "-c", "import sys; assert sys.version_info[:2] == (3, 12)"],
            check=True,
        )
        requirements = (
            "requirements-laptop-blackwell.txt"
            if gpu["sm"] in {"10.0", "12.0"}
            else ("requirements-laptop.txt")
        )
        subprocess.run(
            [
                str(executable),
                "-X",
                "utf8",
                "-m",
                "pip",
                "install",
                "--index-url",
                "https://pypi.org/simple",
                "--extra-index-url",
                "https://pypi.nvidia.com",
                "-r",
                str(BASE_DIR / requirements),
            ],
            check=True,
        )
        subprocess.run([str(executable), "-m", "pip", "check"], check=True)
        logging.info("Setup complete; configure .env.laptop then run this command without --setup")
        return 0
    if not executable.exists():
        raise RuntimeError(
            "Isolated GPU environment missing; run python backend/start_laptop.py --setup"
        )
    arguments = [str(executable), "-X", "utf8", "-u", "-m", "tools.run_laptop"]
    if args.gpu_index is not None:
        arguments += ["--gpu-index", str(args.gpu_index)]
    return subprocess.call(arguments, cwd=BASE_DIR)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        logging.error("Laptop startup stopped: %s", error)
        sys.exit(1)
    except KeyboardInterrupt:
        logging.info("Laptop startup cancelled")
        sys.exit(130)
