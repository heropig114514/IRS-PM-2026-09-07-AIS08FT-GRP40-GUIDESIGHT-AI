"""Responsibilities: Publish only verified fixed pedestrian-signal speech resources.
Implementation: Four exact bilingual utterances, bounded manifest/WAV reads and immutable hashes.
Related Modules: integration.assist; frontend/integration/signal-audio-resources.js.
Declaration Index: read_signal_resource; serve_signal_audio.
Variable Index: SIGNAL_TEXTS fixes approved wording; FILES is the exact public asset allowlist;
MAX_CLIP_BYTES/MAX_PACK_BYTES bound resource memory; no synthesis or credentials are used.
"""

import asyncio
import hashlib
import io
import json
import logging
import math
import re
import wave

SIGNAL_TEXTS = {
    "zh-CN": {"red": "前方是红灯，请等待。", "green": "绿灯，请通行。"},
    "en-US": {"red": "Red light ahead. Please wait.", "green": "Green light. Please proceed."},
}
FILES = frozenset(f"{language}/{state}.wav" for language in SIGNAL_TEXTS
                  for state in ("red", "green"))
MAX_CLIP_BYTES, MAX_PACK_BYTES = 512 * 1024, 1024 * 1024
logger = logging.getLogger(__name__)


def read_signal_resource(root, name):
    """Functionality: Read one fixed resource only after its manifest identity is validated.
    Inputs: caller-owned asset root and exact public name. Outputs: original bytes and MIME type.
    Logic: validate four descriptors; for WAVs verify lossless PCM format and both checksums.
    Constraints: manifest paths never control disk access;
    no retries, synthesis or file writes.
    """
    root = root.resolve()
    manifest_path = (root / "manifest.json").resolve()
    if not manifest_path.is_relative_to(root) or manifest_path.stat().st_size > 16384:
        raise ValueError("Invalid signal speech manifest")
    with manifest_path.open("rb") as source:
        raw_manifest = source.read(16385)
    if len(raw_manifest) > 16384:
        raise ValueError("Invalid signal speech manifest size")
    manifest = json.loads(raw_manifest)
    if (set(manifest) != {"schema_version", "voice", "rate", "sample_rate", "format", "model",
                          "clips"}
            or manifest["schema_version"] != 1 or manifest["voice"] != "Cherry"
            or manifest["rate"] != 2 or manifest["sample_rate"] != 24000
            or manifest["format"] != "pcm_s16le_mono"
            or manifest["model"] != "qwen3-tts-flash-realtime"
            or not isinstance(manifest["clips"], list) or len(manifest["clips"]) != 4):
        raise ValueError("Invalid signal speech identity")
    records, total = {}, 0
    for record in manifest["clips"]:
        if not isinstance(record, dict) or set(record) != {
            "language", "state", "text", "path", "sha256", "pcm_sha256", "byte_length",
            "pcm_byte_length", "samples", "duration_ms",
        }:
            raise ValueError("Invalid signal speech descriptor")
        language, state, path = record["language"], record["state"], record["path"]
        if (language not in SIGNAL_TEXTS or state not in {"red", "green"}
                or record["text"] != SIGNAL_TEXTS[language][state]
                or path != f"{language}/{state}.wav" or path in records
                or any(not isinstance(record[key], str)
                       or not re.fullmatch(r"[0-9a-f]{64}", record[key])
                       for key in ("sha256", "pcm_sha256"))
                or any(type(record[key]) is not int for key in
                       ("byte_length", "pcm_byte_length", "samples"))
                or not 2 <= record["pcm_byte_length"] <= MAX_CLIP_BYTES
                or record["pcm_byte_length"] % 2
                or record["byte_length"] != record["pcm_byte_length"] + 44
                or record["samples"] != record["pcm_byte_length"] // 2
                or not isinstance(record["duration_ms"], int | float)
                or not math.isfinite(record["duration_ms"])
                or abs(record["duration_ms"] - record["samples"] / 24) > 1e-8):
            raise ValueError("Invalid signal speech clip")
        records[path] = record
        total += record["byte_length"]
    if set(records) != FILES or total > MAX_PACK_BYTES:
        raise ValueError("Invalid signal speech pack")
    if name == "manifest.json":
        return raw_manifest, "application/json"
    record = records[name]
    target = (root / name).resolve()
    if not target.is_relative_to(root) or target.stat().st_size != record["byte_length"]:
        raise ValueError("Invalid signal speech file")
    with target.open("rb") as source:
        raw = source.read(record["byte_length"] + 1)
    if len(raw) != record["byte_length"] or hashlib.sha256(raw).hexdigest() != record["sha256"]:
        raise ValueError("Invalid signal speech checksum")
    with wave.open(io.BytesIO(raw), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 24000):
            raise ValueError("Invalid signal speech PCM format")
        pcm = audio.readframes(audio.getnframes())
    if (len(pcm) != record["pcm_byte_length"] or not any(pcm)
            or hashlib.sha256(pcm).hexdigest() != record["pcm_sha256"]):
        raise ValueError("Invalid signal speech PCM checksum")
    return raw, "audio/wav"


async def serve_signal_audio(scope, send, root, respond):
    """Functionality: Serve the exact signal speech pack under the existing mobile asset mount.
    Inputs: ASGI request, fixed asset root and caller-owned response function. Outputs: HTTP reply.
    Logic: reject methods, queries and unknown paths before disk access; expose verified bytes only.
    Constraints: no directory listing, range aliases, provider calls, private data or fallback.
    """
    head = scope.get("method") == "HEAD"
    if scope.get("method") not in {"GET", "HEAD"}:
        return await respond(send, 405, {"code": "method_not_allowed"})
    name = scope["path"].removeprefix("/assets/signal-audio/")
    if scope.get("query_string") or name not in FILES | {"manifest.json"}:
        return await respond(send, 404, {"code": "not_found"}, head=head)
    try:
        raw, mime = await asyncio.to_thread(read_signal_resource, root, name)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, wave.Error) as error:
        logger.error("Signal speech resource unavailable type=%s", type(error).__name__)
        return await respond(send, 503, {"code": "risk_audio_unavailable"}, head=head)
    return await respond(send, 200, raw, mime, head=head)
