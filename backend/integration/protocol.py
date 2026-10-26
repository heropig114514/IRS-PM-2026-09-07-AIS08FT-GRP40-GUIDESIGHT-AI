"""Responsibilities: Define local capture-time packets and immutable inference identity.
Implementation: PRF2 network-order uint32/float64 envelope; unchanged PRS1 response encoding.
Related Modules: realtime.protocol; core.detector; integration.services/inference.services.
Declaration Index: pack_request; unpack_request; read_manifest; model_contract;
validate_contract; validate_result; local_session_seconds; web_origins; inference_url;
is_loopback; is_stop.
Variable Index: ROOT — backend source root; REQUEST_HEADER/REQUEST_MAGIC — PRF2 framing;
MAX_INFLIGHT/SESSION_SECONDS/COLD_SECONDS/STEADY_SECONDS/IDLE_SECONDS — transport budgets;
HASH_PATTERN — model fingerprint syntax; MAX_REQUEST_BYTES — header plus existing JPEG bound.
distance_mode/depth_interval — additive explicit B/C identity; absent mode retains legacy A.
LOCAL_MVP_MODE — explicit offline/assist transport policy, never an algorithm setting.
"""

import json
import math
import os
import re
import struct
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from core.detector import MAX_FRAME_BYTES
from realtime.distance_config import DISTANCE_MODES

from .errors import IntegrationError

ROOT = Path(__file__).resolve().parents[1]
REQUEST_HEADER = struct.Struct("!4sId")
REQUEST_MAGIC = b"PRF2"
MAX_REQUEST_BYTES = REQUEST_HEADER.size + MAX_FRAME_BYTES
MAX_INFLIGHT = 12
SESSION_SECONDS = COLD_SECONDS = 55
STEADY_SECONDS = 10
IDLE_SECONDS = 5
HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def pack_request(index, timestamp, jpeg):
    """Functionality: Encode one timestamped source frame without re-encoding JPEG.
    Inputs: uint32 index, finite nonnegative seconds, JPEG bytes. Outputs: PRF2 packet.
    Logic: validate via the receiver after packing. Constraints: no guessed source times.
    """
    if type(index) is not int or not 0 <= index <= 0xFFFFFFFF:
        raise IntegrationError("invalid_frame", "The frame sequence number is invalid.", 400)
    if isinstance(timestamp, bool) or not isinstance(timestamp, int | float):
        raise IntegrationError(
            "invalid_timestamp", "Capture timestamps must be finite, increasing seconds.", 400
        )
    if not isinstance(jpeg, bytes):
        raise IntegrationError("invalid_frame", "The image must be JPEG bytes.", 400)
    packet = REQUEST_HEADER.pack(REQUEST_MAGIC, index, timestamp) + jpeg
    unpack_request(packet, index)
    return packet


def unpack_request(packet, expected_index, previous_timestamp=None):
    """Functionality: Reject foreign, oversized, reordered or invalid-time source frames.
    Inputs: packet, next uint32 index, prior capture time. Outputs: seconds and JPEG bytes.
    Logic: enforce PRF2, complete JPEG markers and strictly increasing source clock.
    Constraints: full codec/geometry validation belongs to the worker; no sorting or repair.
    """
    if not isinstance(packet, bytes) or not REQUEST_HEADER.size < len(packet) <= MAX_REQUEST_BYTES:
        raise IntegrationError("invalid_frame", "The image is empty or exceeds 1 MiB.", 400)
    magic, index, timestamp = REQUEST_HEADER.unpack_from(packet)
    if magic != REQUEST_MAGIC or index != expected_index:
        raise IntegrationError(
            "invalid_sequence", "The frame protocol or sequence number does not match.", 400
        )
    if (
        not math.isfinite(timestamp)
        or timestamp < 0
        or (previous_timestamp is not None and timestamp <= previous_timestamp)
    ):
        raise IntegrationError(
            "invalid_timestamp", "Capture timestamps must be finite, increasing seconds.", 400
        )
    jpeg = packet[REQUEST_HEADER.size :]
    if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
        raise IntegrationError("invalid_frame", "Only complete JPEG images are accepted.", 400)
    return timestamp, jpeg


def read_manifest(path=None):
    """Functionality: Read detector identity without loading Torch or executing GPU work.
    Inputs: optional explicit manifest Path. Outputs: validated original model manifest.
    Logic: require model/class/hash/build fields. Constraints: identity is not COCO qualification.
    """
    try:
        manifest = json.loads(
            (ROOT / "models/yolo26n.json" if path is None else path).read_text(encoding="utf-8")
        )
        names = manifest.get("names")
        if (
            manifest.get("model") != "yolo26n"
            or not isinstance(names, list)
            or len(names) != 80
            or any(not isinstance(name, str) or not name for name in names)
            or not HASH_PATTERN.fullmatch(manifest.get("source_sha256", ""))
            or not isinstance(manifest.get("build", {}).get("ultralytics"), str)
        ):
            raise ValueError("Invalid manifest")
        return manifest
    except (OSError, ValueError, TypeError, AttributeError):
        raise IntegrationError(
            "local_manifest_invalid", "The local detection-model manifest is missing or invalid."
        ) from None


def local_session_seconds():
    """Functionality: Resolve the explicitly selected local transport session policy.
    Inputs: LOCAL_MVP_MODE environment, default offline. Outputs: 55 seconds or None for assist.
    Logic: only the assist entry permits continuous local input; unknown modes fail closed.
    Constraints: no automatic reconnect or changes to capture clocks and inference/idle budgets.
    """
    mode = os.environ.get("LOCAL_MVP_MODE", "offline")
    if mode not in {"offline", "assist"}:
        raise IntegrationError(
            "local_configuration_invalid", "The local runtime-mode configuration is invalid."
        )
    return None if mode == "assist" else SESSION_SECONDS


def model_contract(session_id, manifest, loaded=False, *, distance_mode="depth", depth_interval=3,
                   session_seconds=SESSION_SECONDS):
    """Functionality: Declare the complete selected local pipeline without foreign validation.
    Inputs: session ID, manifest, loaded flag, mode/interval and session policy. Outputs: hello.
    Logic: use existing native FP32 control/settings; explicit geometry replaces only depth.
    Constraints: capability means selected support; loaded separately describes initialization;
    None is an explicitly selected continuous local session, not an inference timeout exemption.
    """
    contract = {
        "kind": "session",
        "provider": "local",
        "protocol_version": 2,
        "session_id": session_id,
        "model": "yolo26n",
        "precision": "FP32",
        "backend": "PyTorch resident CUDA",
        "output_precision": "FP32",
        "classes": 80,
        "source_sha256": manifest["source_sha256"],
        "names": manifest["names"],
        "confidence_threshold": 0.25,
        "max_edge": 640,
        "jpeg_quality": 0.8,
        "max_inflight": MAX_INFLIGHT,
        "session_seconds": session_seconds,
        "cold_seconds": COLD_SECONDS,
        "steady_seconds": STEADY_SECONDS,
        "depth_model": "yolo26n-depth",
        "depth_units": "m",
        "tracking_model": "botsort",
        "risk_model": "rules-v1",
        "loaded": bool(loaded),
        "capabilities": {"detection": True, "depth": True, "tracking": True, "risk": True},
    }
    if distance_mode not in DISTANCE_MODES or type(depth_interval) is not int or depth_interval < 1:
        raise IntegrationError(
            "local_contract_invalid", "The distance mode or depth interval is invalid."
        )
    if session_seconds is not None and (
            type(session_seconds) is not int or session_seconds != SESSION_SECONDS):
        raise IntegrationError(
            "local_contract_invalid", "The local session timing configuration is invalid."
        )
    if distance_mode != "depth":
        contract.update(distance_mode=distance_mode, distance_units="m",
                        depth_interval=depth_interval if distance_mode == "temporal_depth" else 0)
    if distance_mode == "geometry":
        contract.pop("depth_model")
        contract.pop("depth_units")
        contract["distance_estimator"] = "pinhole-height-v1"
        contract["capabilities"].update(depth=False, distance=True)
    return contract


def validate_contract(message):
    """Functionality: Admit only the local native FP32 timestamped pipeline.
    Inputs: provider hello. Outputs: same dictionary or safe error.
    Logic: compare fixed fields and check session/class/hash identity.
    Constraints: A stays unchanged; B/C require an explicit supported distance identity;
    no accepting HF validation, alternative precision, or missing tracking/risk.
    """
    if not isinstance(message, dict):
        raise IntegrationError(
            "local_contract_invalid", "The local inference-session configuration is invalid."
        )
    names = message.get("names")
    mode = message.get("distance_mode", "depth")
    expected = model_contract("fixture", {"names": [], "source_sha256": ""},
                              distance_mode=mode, depth_interval=message.get("depth_interval", 3)
                              if mode == "temporal_depth" else 3,
                              session_seconds=local_session_seconds())
    fixed = set(expected) - {"session_id", "names", "source_sha256", "loaded"}
    if (
        any(message.get(key) != expected[key] for key in fixed)
        or not re.fullmatch(r"[0-9a-f]{32}", message.get("session_id", ""))
        or not isinstance(names, list)
        or len(names) != 80
        or any(not isinstance(name, str) or not name for name in names)
        or not HASH_PATTERN.fullmatch(message.get("source_sha256", ""))
        or type(message.get("loaded")) is not bool
        or "validation" in message
        or (mode == "geometry" and ("depth_model" in message or "depth_units" in message))
    ):
        raise IntegrationError(
            "local_contract_invalid", "The local inference-session configuration is invalid."
        )
    return message


def validate_result(metadata, contract, index, timestamp):
    """Functionality: Correlate a hash-checked result with its admitted source frame.
    Inputs: parsed metadata, hello, index and exact capture seconds. Outputs: None or safe error.
    Logic: require identity, complete pipeline and matching clock/session/sequence.
    Constraints: preserve all algorithm values; semantic object validation stays in the client.
    """
    keys = (
        "provider",
        "session_id",
        "model",
        "precision",
        "backend",
        "output_precision",
        "classes",
        "source_sha256",
        "confidence_threshold",
        "depth_model",
        "depth_units",
        "tracking_model",
        "risk_model",
    )
    if contract.get("distance_mode") == "geometry":
        keys = tuple(key for key in keys if key not in {"depth_model", "depth_units"})
    if contract.get("distance_mode") in {"geometry", "temporal_depth"}:
        keys += ("distance_mode", "distance_units", "depth_interval")
    if contract.get("distance_mode") == "geometry":
        keys += ("distance_estimator",)
        if "depth_model" in metadata:
            raise IntegrationError(
                "local_result_invalid", "Geometric distance mode must not declare a depth model."
            )
    if (
        any(metadata.get(key) != contract[key] for key in keys)
        or metadata.get("index") != index
        or metadata.get("capture_time_s") != timestamp
        or metadata.get("timestamp_source") != "browser_capture"
    ):
        raise IntegrationError(
            "local_result_invalid",
            "The local inference-result identity or capture time does not match.",
        )


def is_loopback(host):
    """Functionality: Recognize explicit numeric loopback hosts without DNS resolution.
    Inputs: host text. Outputs: bool. Logic: IP parsing plus localhost alias.
    Constraints: arbitrary DNS names are never treated as local.
    """
    try:
        return host == "localhost" or ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def web_origins():
    """Functionality: Read an explicit local UI origin and its localhost alias.
    Inputs: LOCAL_WEB_ORIGIN environment. Outputs: canonical allowed origin tuple.
    Logic: require local HTTP/HTTPS host and no credentials/path/query/fragment.
    Constraints: no wildcard or external browser origin in this local MVP.
    """
    raw = os.environ.get("LOCAL_WEB_ORIGIN", "http://127.0.0.1:8766")
    try:
        parsed = urlsplit(raw)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if (
            parsed.scheme not in {"http", "https"}
            or not is_loopback(parsed.hostname)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Invalid origin")
        suffix = "" if port == (443 if parsed.scheme == "https" else 80) else f":{port}"
        return tuple(
            dict.fromkeys(
                (
                    raw,
                    f"{parsed.scheme}://127.0.0.1{suffix}",
                    f"{parsed.scheme}://localhost{suffix}",
                )
            )
        )
    except ValueError:
        raise IntegrationError(
            "local_configuration_invalid", "The local-page Origin configuration is invalid."
        ) from None


def inference_url():
    """Functionality: Validate one explicit local or future TLS cloud worker destination.
    Inputs: LOCAL_INFERENCE_URL environment. Outputs: URL and health URL.
    Logic: exact endpoint, no URL credentials/query/fragment; cleartext only for numeric loopback.
    Constraints: endpoint selection is explicit, never fallback or browser-controlled.
    """
    raw = os.environ.get("LOCAL_INFERENCE_URL", "ws://127.0.0.1:8767/ws/infer/")
    try:
        parsed = urlsplit(raw)
        if (
            parsed.scheme not in {"ws", "wss"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != "/ws/infer/"
            or parsed.query
            or parsed.fragment
            or (parsed.scheme == "ws" and not is_loopback(parsed.hostname))
        ):
            raise ValueError("Invalid worker URL")
        _ = parsed.port
        return raw, f"{'https' if parsed.scheme == 'wss' else 'http'}://{parsed.netloc}/health/"
    except ValueError:
        raise IntegrationError(
            "local_configuration_invalid", "The local inference-service address is invalid."
        ) from None


def is_stop(text):
    """Functionality: Recognize only a bounded explicit stop control.
    Inputs: ASGI text or None. Outputs: bool. Logic: exact JSON object semantics.
    Constraints: other text is invalid frame input, not a command or inferred disconnect.
    """
    if not isinstance(text, str) or len(text) > 1024:
        return False
    try:
        return json.loads(text) == {"kind": "stop"}
    except ValueError:
        return False
