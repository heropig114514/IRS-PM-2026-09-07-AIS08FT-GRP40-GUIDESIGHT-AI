"""Responsibilities: Freeze an explicit native depth backend and artifact manifest selection.
Implementation: Lightweight environment parsing; no Torch, model loading or automatic fallback.
Related Modules: realtime.services/depth; integration.inference.services; tools.run_local_mvp.
Declaration Index: DepthBackendConfig; DepthBackendConfig.__post_init__; read_depth_backend.
Variable Index: DepthBackendConfig.backend/manifest — immutable startup selection and artifact path.
"""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DepthBackendConfig:
    """Functionality: Identify the chosen depth implementation without touching CUDA.
    Logic: Torch is portable; TensorRT requires an explicitly provisioned local manifest.
    Constraints: no engine discovery, backend guessing, implicit compile or fallback.
    """

    backend: str = "torch"
    manifest: Path | None = None

    def __post_init__(self):
        """Functionality: Reject ambiguous or unsupported startup selections.
        Inputs: backend name and optional manifest path. Outputs: valid immutable configuration.
        Logic: enforce the two supported backends and TensorRT-only manifest ownership.
        Constraints: file contents and runtime identity are validated on the GPU worker.
        """
        if self.backend not in {"torch", "tensorrt"}:
            raise ValueError("PRS_DEPTH_BACKEND must be torch or tensorrt")
        if (self.backend == "tensorrt") != (self.manifest is not None):
            raise ValueError("TensorRT requires PRS_DEPTH_ENGINE_MANIFEST; Torch must omit it")
        if self.manifest is not None and (
                not isinstance(self.manifest, Path) or self.manifest.suffix != ".json"):
            raise ValueError("Depth engine manifest must be a local JSON path")


def read_depth_backend(root, environment=None):
    """Functionality: Resolve native depth settings once against the backend workspace.
    Inputs: backend root and optional environment mapping. Outputs: frozen DepthBackendConfig.
    Logic: parse only the two explicit settings and resolve relative paths against root.
    Constraints: no dotenv reads or GPU/package imports; paths are trusted operator configuration.
    """
    environment = os.environ if environment is None else environment
    name = environment.get("PRS_DEPTH_BACKEND", "torch")
    path = environment.get("PRS_DEPTH_ENGINE_MANIFEST", "")
    return DepthBackendConfig(name, (Path(root) / path).resolve() if path else None)
