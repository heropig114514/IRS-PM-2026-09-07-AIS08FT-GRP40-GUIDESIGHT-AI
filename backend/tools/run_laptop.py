"""Responsibilities: Start/stop GPU inference and SSH reverse tunnel as one explicit command.
Implementation: Validate configuration, select GPU/cache, load model, listen locally, activate SSH.
Related Modules: tools.laptop_config/laptop_platform/laptop_tunnel; core.gpu_detector.
Declaration Index: ensure_model; prepare_engine; check_cloud; run; main.
Variable Index: BASE_DIR — backend root; logger — safe lifecycle diagnostics.
"""

import argparse
import hashlib
import http.client
import importlib.metadata
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Thread
from urllib.parse import urlsplit

from dotenv import load_dotenv

from tools.laptop_config import LaptopConfig
from tools.laptop_platform import engine_identity, runtime_profile, select_gpu
from tools.laptop_tunnel import ReverseTunnel

BASE_DIR = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


def ensure_model(config, tunnel, digest):
    """Functionality: Obtain the fixed ONNX when absent. Inputs: config, SSH tunnel, pinned digest.
    Outputs: verified local model; Logic: explicit SFTP source, bounded read, hash before rename.
    Constraints: existing invalid files fail; no alternate weights, HTTP download or retry.
    """
    path = config.onnx_path
    if not path.exists():
        if not config.server_onnx_path:
            raise RuntimeError("ONNX missing; supply the pinned file or LAPTOP_SERVER_ONNX_PATH")
        logger.info("Fetching the pinned ONNX from the configured cloud artifact")
        with tunnel.client.open_sftp() as sftp:
            with sftp.open(config.server_onnx_path, "rb") as source:
                source.settimeout(30)
                data = source.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Cloud ONNX does not match the pinned bounded artifact")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".onnx.tmp")
        try:
            temporary.write_bytes(data)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Local ONNX is not a bounded regular model file")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("Local ONNX differs from the pinned model")


def prepare_engine(config, gpu, tunnel):
    """Functionality: Select/build hardware-specific cache. Inputs: config, GPU inventory, SSH.
    Outputs: absolute engine path; Logic: source/version/hardware identity, build only absent pair.
    Constraints: FP32/TF32-off/256MiB remain fixed; corrupt/partial cache never silently rebuilds.
    """
    import tensorrt as trt

    profile = runtime_profile(gpu)
    if (
        trt.__version__ != profile["trt"]
        or importlib.metadata.version("nvidia-cuda-runtime-cu12") != profile["cuda"]
    ):
        raise RuntimeError(
            "Installed TensorRT/CUDA profile differs from GPU; run --setup for this GPU"
        )
    manifest = json.loads((BASE_DIR / "models/yolo26n.json").read_text(encoding="utf-8"))
    ensure_model(config, tunnel, manifest["onnx_sha256"])
    identity, key = engine_identity(gpu, trt.__version__, manifest["onnx_sha256"])
    engine = BASE_DIR / "models/tensorrt" / f"yolo26n-fp32-{key}.engine"
    sidecar = engine.with_suffix(".json")
    if engine.exists() != sidecar.exists():
        raise RuntimeError(
            "Partial TensorRT cache; inspect/remove this cache pair before restarting"
        )
    if not engine.exists():
        logger.info("Compiling this GPU's FP32 engine; first startup may take several minutes")
        subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-u",
                "-m",
                "tools.build_tensorrt",
                "--onnx",
                str(config.onnx_path),
                "--engine",
                str(engine),
                "--gpu-index",
                str(gpu["index"]),
            ],
            cwd=BASE_DIR,
            check=True,
        )
    if json.loads(sidecar.read_text(encoding="utf-8")).get("hardware") != identity:
        raise ValueError("TensorRT cache hardware identity mismatch")
    return engine


def check_cloud(config):
    """Functionality: Verify configured public relay once. Inputs: optional HTTPS public URL.
    Outputs: None or failure; Logic: bounded GET to same-origin readiness endpoint.
    Constraints: no redirects, certificate bypass, credentials, retry or inference frame.
    """
    if not config.public_url:
        return
    parsed = urlsplit(config.public_url)
    connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=8)
    try:
        connection.request("GET", parsed.path + "vision/laptop/config/")
        response = connection.getresponse()
        data = response.read(4097)
        if response.status != 200 or len(data) > 4096:
            raise RuntimeError("Cloud readiness failed; check relay port, token and HTTPS URL")
        data = json.loads(data)
        if not (
            data.get("ready") is True
            and data.get("backend") == "TensorRT"
            and data.get("precision") == "FP32"
        ):
            raise ValueError("Cloud readiness returned an unexpected inference contract")
        logger.info("Public cloud relay verified ready=true backend=TensorRT precision=FP32")
    finally:
        connection.close()


def run(config, gpu):
    """Functionality: Own combined runtime. Inputs: validated config and selected physical GPU.
    Outputs: serving lifetime or error; Logic: SSH/model/worker/tunnel/readiness in order.
    Constraints: finally closes owned components on Ctrl+C/start failure/disconnect; no restart.
    """
    import django
    import uvicorn

    tunnel = ReverseTunnel(config)
    server = None
    worker = None
    forwarding = None
    listener = None
    stop = Event()
    try:
        # Reserve the owned port throughout compilation to reject concurrent startup races.
        listener = socket.socket()
        listener.bind(("127.0.0.1", config.local_port))
        tunnel.connect()
        os.environ["LAPTOP_ENGINE_PATH"] = str(prepare_engine(config, gpu, tunnel))
        os.environ["DJANGO_SETTINGS_MODULE"] = "config.laptop_worker"
        django.setup()
        from core.gpu_detector import get_gpu_detector

        get_gpu_detector()
        server = uvicorn.Server(
            uvicorn.Config(
                "config.asgi:application",
                host="127.0.0.1",
                port=config.local_port,
                workers=1,
                lifespan="off",
                access_log=False,
                timeout_graceful_shutdown=8,
            )
        )
        worker = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        deadline = time.monotonic() + 20
        while not server.started:
            if not worker.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("Local GPU worker did not become ready")
            stop.wait(0.05)
        tunnel.activate()
        forwarding = Thread(target=tunnel.serve, args=(stop,), daemon=True)
        forwarding.start()
        check_cloud(config)
        logger.info("Laptop node ready: GPU worker + SSH tunnel active; Ctrl+C stops both")
        while not stop.wait(0.5):
            if not worker.is_alive():
                raise RuntimeError("GPU worker stopped; restart combined command manually")
            if not forwarding.is_alive():
                raise RuntimeError("SSH tunnel stopped; restart combined command manually") from (
                    tunnel.error
                )
    finally:
        stop.set()
        tunnel.close()
        if server is not None:
            server.should_exit = True
        for thread in (forwarding, worker):
            if thread is not None:
                thread.join(timeout=10)
        if listener is not None:
            listener.close()
        logger.info("Owned GPU worker and SSH tunnel shut down")


def main():
    """Functionality: Parse/load and start one GPU node. Inputs: private env, optional GPU CLI.
    Outputs: run lifetime; Logic: validate and mask GPU before importing CUDA/TensorRT.
    Constraints: no implicit CPU fallback or stored SSH password; established precision unchanged.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu-index", type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    load_dotenv(BASE_DIR / ".env.laptop", override=False)
    if args.gpu_index is not None:
        os.environ["LAPTOP_GPU_INDEX"] = str(args.gpu_index)
    config = LaptopConfig.load()
    if len(os.environ.get("DJANGO_SECRET_KEY", "")) < 32:
        raise ValueError("Set a private DJANGO_SECRET_KEY of at least 32 characters")
    gpu = select_gpu(config.gpu_index)
    mask = os.environ.get("CUDA_VISIBLE_DEVICES")
    if mask is not None and mask != gpu["uuid"]:
        raise ValueError("CUDA_VISIBLE_DEVICES conflicts with selected physical GPU")
    os.environ["CUDA_VISIBLE_DEVICES"] = gpu["uuid"]
    os.environ["LAPTOP_GPU_INDEX"] = str(gpu["index"])
    logger.info("Selected NVIDIA GPU index=%d name=%s sm=%s", gpu["index"], gpu["name"], gpu["sm"])
    run(config, gpu)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Laptop node stopped by user")
        sys.exit(130)
    except Exception as error:
        logger.error("Laptop node failed exception_type=%s reason=%s", type(error).__name__, error)
        sys.exit(1)
