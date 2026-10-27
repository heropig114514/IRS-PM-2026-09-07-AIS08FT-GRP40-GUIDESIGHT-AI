"""Responsibilities: Bound ordered binary JPEG requests and hash-checked JPEG responses.
Implementation: Versioned8-byte headers; bounded JSON metadata; unchanged JPEG bytes.
Related Modules: zerogpu.live_worker; tools.live_benchmark; core.detector JPEG size boundary.
Declaration Index: pack_request; unpack_request; pack_result; unpack_result.
Variable Index: HEADER — network-order magic/uint32; REQUEST_MAGIC/RESULT_MAGIC — version tags;
MAX_METADATA_BYTES —256KiB boundary for300 fused/tracked boxes including legacy fields;
MAX_PACKET_BYTES — response wire-size ceiling; JPEG limit is unchanged.
"""

import hashlib
import json
import struct

from core.detector import MAX_FRAME_BYTES

HEADER = struct.Struct("!4sI")
REQUEST_MAGIC = b"PRF1"
RESULT_MAGIC = b"PRS1"
MAX_METADATA_BYTES = 256 * 1024
MAX_PACKET_BYTES = HEADER.size + MAX_METADATA_BYTES + MAX_FRAME_BYTES


def pack_request(index, jpeg):
    """Functionality: Encode one sequence-numbered full JPEG without base64.
    Inputs: uint32 index and original JPEG bytes; Outputs: header plus unchanged bytes.
    Logic: enforce existing1MiB input boundary; Constraints: no encoding, skipping or persistence.
    """
    if type(index) is not int or not 0 <= index <= 0xFFFFFFFF:
        raise ValueError("Invalid frame sequence")
    if not isinstance(jpeg, bytes) or not 0 < len(jpeg) <= MAX_FRAME_BYTES:
        raise ValueError("Invalid JPEG request size")
    return HEADER.pack(REQUEST_MAGIC, index) + jpeg


def unpack_request(packet, expected_index):
    """Functionality: Reject text, oversized, reordered or foreign-protocol input.
    Inputs: received packet and expected sequence; Outputs: JPEG bytes.
    Logic: validate bounded header and exact next index; Constraints: codec checked by detector.
    """
    if not isinstance(packet, bytes) or not HEADER.size < len(packet) <= (
            HEADER.size + MAX_FRAME_BYTES):
        raise ValueError("Invalid binary request size")
    magic, index = HEADER.unpack_from(packet)
    if magic != REQUEST_MAGIC or index != expected_index:
        raise ValueError("Request version or sequence mismatch")
    return packet[HEADER.size:]


def pack_result(metadata, jpeg):
    """Functionality: Encode detector metadata and the complete unchanged annotated JPEG.
    Inputs: finite JSON metadata with sequence/hash and bounded JPEG bytes.
    Outputs: version/JSON-length prefix, UTF8 JSON and JPEG; Logic: strict finite serialization.
    Constraints: original1MiB JPEG boundary; no compression/base64 or partial-success message.
    """
    if not isinstance(jpeg, bytes) or not 0 < len(jpeg) <= MAX_FRAME_BYTES:
        raise ValueError("Invalid JPEG result size")
    raw = json.dumps(metadata, separators=(",", ":"), allow_nan=False).encode()
    if not 0 < len(raw) <= MAX_METADATA_BYTES:
        raise ValueError("Result metadata boundary exceeded")
    return HEADER.pack(RESULT_MAGIC, len(raw)) + raw + jpeg


def unpack_result(packet, expected_index):
    """Functionality: Verify response framing, order and complete JPEG fingerprint.
    Inputs: received binary packet and expected sequence; Outputs: metadata and JPEG bytes.
    Logic: bound total/header length, parse JSON, verify index/markers/SHA256.
    Constraints: model/box consistency is verified separately by the validation benchmark;
    checksum integrity alone does not authenticate a remote service or prove detection accuracy.
    """
    if not isinstance(packet, bytes) or not HEADER.size < len(packet) <= MAX_PACKET_BYTES:
        raise ValueError("Invalid binary result size")
    magic, length = HEADER.unpack_from(packet)
    if magic != RESULT_MAGIC or not 0 < length <= MAX_METADATA_BYTES:
        raise ValueError("Result version or metadata boundary mismatch")
    boundary = HEADER.size + length
    metadata = json.loads(packet[HEADER.size:boundary])
    jpeg = packet[boundary:]
    if (not isinstance(metadata, dict) or type(metadata.get("index")) is not int
            or metadata["index"] != expected_index or not 0 < len(jpeg) <= MAX_FRAME_BYTES
            or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9")
            or hashlib.sha256(jpeg).hexdigest() != metadata.get("jpeg_sha256")):
        raise ValueError("Result sequence or complete JPEG fingerprint mismatch")
    return metadata, jpeg
