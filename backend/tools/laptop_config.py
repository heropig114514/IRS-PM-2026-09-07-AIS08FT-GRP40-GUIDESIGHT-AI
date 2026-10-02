"""Responsibilities: Validate portable private laptop/SSH configuration before startup side effects.
Implementation: Immutable configuration, safe numeric/path/origin validation, environment overrides.
Related Modules: tools.run_laptop, tools.laptop_tunnel, .env.laptop.example.
Declaration Index: port; LaptopConfig/LaptopConfig.load.
Variable Index: BASE_DIR — portable backend location; LaptopConfig fields — validated
host/user/ssh_port endpoint, local_port/remote_port mapping, known_hosts/key_path trust/auth,
onnx_path/server_onnx_path artifacts, public_url readiness probe, gpu_index device selection,
token private bearer credential excluded from representation.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

BASE_DIR = Path(__file__).resolve().parents[1]


def port(name, default):
    """Functionality: Validate TCP port. Inputs: environment name and established default.
    Outputs: integer; Logic: require range 1024..65535 for inference, 1..65535 for SSH.
    Constraints: reject malformed values before socket creation or SSH command interpolation.
    """
    value = int(os.environ.get(name, str(default)))
    minimum = 1 if name == "LAPTOP_SSH_PORT" else 1024
    if not minimum <= value <= 65535:
        raise ValueError(f"{name} must be in {minimum}..65535")
    return value


@dataclass(frozen=True)
class LaptopConfig:
    """Functionality: Describe one private GPU node and its cloud tunnel.
    Logic: load validated explicit server settings; token excluded from representation.
    Constraints: fixed loopback binding, one worker, no password persistence or inferred SSH host.
    """

    host: str
    user: str
    ssh_port: int
    local_port: int
    remote_port: int
    known_hosts: Path
    key_path: Path | None
    onnx_path: Path
    server_onnx_path: str
    public_url: str
    gpu_index: int | None
    token: str = field(repr=False)

    @classmethod
    def load(cls):
        """Functionality: Parse startup configuration. Inputs: cls and loaded environment.
        Outputs: LaptopConfig; Logic: require host/user/token/key trust, validate paths and indices.
        Constraints: secrets never appear in errors, host is not an SSH command/URL.
        """
        host = os.environ.get("LAPTOP_SSH_HOST", "").strip()
        user = os.environ.get("LAPTOP_SSH_USER", "").strip()
        token = os.environ.get("LAPTOP_INFERENCE_TOKEN", "")
        if not host or any(character.isspace() for character in host) or "/" in host:
            raise ValueError("Set LAPTOP_SSH_HOST to your cloud server hostname/IP")
        if not user or any(character.isspace() for character in user):
            raise ValueError("Set LAPTOP_SSH_USER to your SSH account")
        if len(token) < 32 or any(character in token for character in "\r\n"):
            raise ValueError("Set LAPTOP_INFERENCE_TOKEN to the cloud's shared private token")
        known_hosts = Path(
            os.environ.get("LAPTOP_SSH_KNOWN_HOSTS", "~/.ssh/known_hosts")
        ).expanduser()
        if not known_hosts.is_file():
            raise ValueError("SSH known_hosts missing; verify and trust the server before startup")
        key = os.environ.get("LAPTOP_SSH_KEY_PATH", "").strip()
        key_path = Path(key).expanduser().resolve() if key else None
        if key_path is not None and not key_path.is_file():
            raise ValueError("Configured SSH private key file does not exist")
        onnx = Path(os.environ.get("LAPTOP_ONNX_PATH", "models/yolo26n.onnx")).expanduser()
        onnx = (BASE_DIR / onnx).resolve() if not onnx.is_absolute() else onnx.resolve()
        remote_model = os.environ.get("LAPTOP_SERVER_ONNX_PATH", "").strip()
        if remote_model and not remote_model.startswith("/"):
            raise ValueError("LAPTOP_SERVER_ONNX_PATH must be an absolute cloud file path")
        public_url = os.environ.get("LAPTOP_PUBLIC_URL", "").strip()
        if public_url:
            parsed = urlsplit(public_url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("LAPTOP_PUBLIC_URL must be a credential-free HTTPS PRS page URL")
            public_url = public_url.rstrip("/") + "/"
        configured_gpu = os.environ.get("LAPTOP_GPU_INDEX", "").strip()
        gpu_index = int(configured_gpu) if configured_gpu else None
        if gpu_index is not None and gpu_index < 0:
            raise ValueError("LAPTOP_GPU_INDEX must be a nonnegative physical device index")
        return cls(
            host,
            user,
            port("LAPTOP_SSH_PORT", 22),
            port("LAPTOP_WORKER_PORT", 18767),
            port("LAPTOP_REMOTE_PORT", 18767),
            known_hosts,
            key_path,
            onnx,
            remote_model,
            public_url,
            gpu_index,
            token,
        )
