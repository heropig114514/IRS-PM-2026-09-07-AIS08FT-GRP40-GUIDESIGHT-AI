"""Responsibilities: Prepare four fixed bilingual pedestrian-signal speech resources.
Implementation: One explicit TLS/local TTS request per sentence, unchanged PCM in exclusive WAVs.
Related Modules: core.agent.tts; frontend/integration/signal-alerts/signal-audio-resources.js.
Declaration Index: SignalAudioError; endpoint; collect; describe_audio; generate; main.
Variable Index: TEXTS fixes the approved corpus; MAX_CLIP_BYTES bounds each 24kHz mono PCM clip;
PROJECT confines output/report files to this checkout. No provider credentials are read.
"""

import argparse
import asyncio
import hashlib
import io
import json
import re
import sys
import uuid
import wave
from pathlib import Path
from urllib.parse import urlsplit

from websockets.asyncio.client import connect

PROJECT = Path(__file__).resolve().parents[2]
MAX_CLIP_BYTES = 512 * 1024
TEXTS = {
    "zh-CN": {"red": "前方是红灯，请等待。", "green": "绿灯，请通行。"},
    "en-US": {
        "red": "Red light ahead. Please wait.",
        "green": "Green light. Please proceed.",
    },
}


class SignalAudioError(Exception):
    """Functionality: Expose authored, body-free generation errors.
    Logic: callers select fixed codes. Constraints: no credentials, transcript or remote body.
    """


def endpoint(value):
    """Functionality: Validate the operator's explicit canonical TTS socket and Origin.
    Inputs: WSS URL or WS loopback URL. Outputs: exact URL and matching HTTP(S) Origin.
    Logic: require the existing TTS path and clean authority; TLS is mandatory for remote hosts.
    Constraints: no proxy discovery, redirects, credentials, query, fragment or automatic fallback.
    """
    try:
        parts = urlsplit(value)
        if (not isinstance(value, str) or not value.isascii()
                or any(char.isspace() for char in value)
                or parts.scheme not in {"ws", "wss"}
                or not parts.hostname or not re.fullmatch(r"[a-z0-9.-]+", parts.hostname)
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.path not in {"/ws/tts/", "/mvp/ws/tts/"}
                or parts.scheme == "ws" and parts.hostname not in {"127.0.0.1", "localhost"}
                or parts.port is not None and not 1 <= parts.port <= 65535
                or parts.netloc != parts.hostname + (f":{parts.port}" if parts.port else "")):
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise SignalAudioError("invalid_tts_endpoint") from None
    return value, ("https://" if parts.scheme == "wss" else "http://") + parts.netloc


async def collect(url, language, text, *, opener=connect):
    """Functionality: Obtain one fixed sentence through the existing five-field TTS contract.
    Inputs: explicit endpoint, language, approved sentence and injectable connector. Outputs: PCM.
    Logic: validate UUID/config/completion identity, cap bytes and total wall time, close on cancel.
    Constraints: one request only, no provider credentials, playback, trimming or rate alteration.
    """
    address, origin = endpoint(url)
    request_id = str(uuid.uuid4())
    if text not in TEXTS.get(language, {}).values():
        raise SignalAudioError("invalid_signal_phrase")
    configured, chunks, total = False, [], 0
    try:
        async with asyncio.timeout(60):
            async with opener(address, origin=origin, proxy=None, compression=None,
                              open_timeout=15, close_timeout=3, max_size=MAX_CLIP_BYTES + 8192,
                              max_queue=1, ping_interval=None) as peer:
                await peer.send(json.dumps({"type": "synthesize", "request_id": request_id,
                                           "text": text, "language": language, "rate": 2}))
                while True:
                    raw = await peer.recv()
                    if isinstance(raw, bytes):
                        if not configured or not raw or len(raw) % 2:
                            raise SignalAudioError("invalid_tts_audio")
                        total += len(raw)
                        if total > MAX_CLIP_BYTES:
                            raise SignalAudioError("signal_audio_limit")
                        chunks.append(raw)
                        continue
                    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 8192:
                        raise SignalAudioError("invalid_tts_protocol")
                    try:
                        item = json.loads(raw)
                    except (ValueError, TypeError):
                        raise SignalAudioError("invalid_tts_protocol") from None
                    if not isinstance(item, dict) or item.get("request_id") != request_id:
                        raise SignalAudioError("invalid_tts_protocol")
                    if item.get("type") == "error":
                        code = item.get("code")
                        permitted = {"tts_not_configured", "tts_timeout", "tts_upstream_error",
                                     "tts_upstream_unavailable", "tts_upstream_protocol"}
                        raise SignalAudioError(code if code in permitted else "tts_failed")
                    if (item.get("type") == "audio_config" and not configured and total == 0
                            and item.get("sample_rate") == 24000
                            and item.get("format") == "pcm_s16le_mono"):
                        configured = True
                    elif (item.get("type") == "complete" and configured and total > 0
                          and type(item.get("audio_bytes")) is int
                          and item["audio_bytes"] == total):
                        pcm = b"".join(chunks)
                        if not any(pcm):
                            raise SignalAudioError("empty_signal_audio")
                        return pcm
                    else:
                        raise SignalAudioError("invalid_tts_protocol")
    except asyncio.CancelledError:
        raise
    except SignalAudioError:
        raise
    except TimeoutError:
        raise SignalAudioError("tts_timeout") from None
    except Exception:
        raise SignalAudioError("tts_connection_failed") from None


def describe_audio(language, state, pcm):
    """Functionality: Wrap unchanged PCM and describe exact public clip identity.
    Inputs: approved language/state and nonzero PCM. Outputs: canonical WAV bytes and descriptor.
    Logic: add a 44-byte PCM WAV container and compute separate file/sample SHA256 hashes.
    Constraints: mono 24kHz signed16LE, at most512KiB, no resampling, silence edits or replay speed.
    """
    if (language not in TEXTS or state not in TEXTS[language] or not isinstance(pcm, bytes)
            or not pcm or len(pcm) % 2 or len(pcm) > MAX_CLIP_BYTES or not any(pcm)):
        raise SignalAudioError("invalid_signal_audio")
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(24000)
        writer.writeframes(pcm)
    audio = output.getvalue()
    return audio, {
        "language": language, "state": state, "text": TEXTS[language][state],
        "path": f"{language}/{state}.wav", "sha256": hashlib.sha256(audio).hexdigest(),
        "pcm_sha256": hashlib.sha256(pcm).hexdigest(), "byte_length": len(audio),
        "pcm_byte_length": len(pcm), "samples": len(pcm) // 2,
        "duration_ms": len(pcm) / 48,
    }


async def generate(url, output, *, collector=collect):
    """Functionality: Generate an exclusive fixed four-clip public pack.
    Inputs: explicit TTS URL, new owned output directory and optional test collector.
    Outputs: complete fixed manifest.
    Logic: serialize four requests; publish manifest only after all complete files exist.
    Constraints: no overwrite, retry, provider secret or partial manifest;
    failed files remain reviewable.
    """
    endpoint(url)
    target = output.resolve()
    if output.is_symlink() or not target.is_relative_to(PROJECT) or not target.parent.is_dir():
        raise SignalAudioError("invalid_output_directory")
    target.mkdir(exist_ok=False)
    clips = []
    for language, phrases in TEXTS.items():
        (target / language).mkdir()
        for state, text in phrases.items():
            pcm = await collector(url, language, text)
            audio, record = describe_audio(language, state, pcm)
            with (target / record["path"]).open("xb") as destination:
                destination.write(audio)
            clips.append(record)
    if sum(item["byte_length"] for item in clips) > 1024 * 1024:
        raise SignalAudioError("signal_pack_limit")
    manifest = {"schema_version": 1, "voice": "Cherry", "rate": 2, "sample_rate": 24000,
                "format": "pcm_s16le_mono", "model": "qwen3-tts-flash-realtime", "clips": clips}
    with (target / "manifest.json").open("x", encoding="utf-8", newline="\n") as destination:
        destination.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main(argv=None):
    """Functionality: Run explicit fixed-speech preparation and emit a body-free summary.
    Inputs: --tts-url and --output. Outputs: process exit status and counts/error code.
    Logic: validate before I/O; reject existing resources.
    Constraints: never saves endpoint credentials.
    """
    parser = argparse.ArgumentParser(description="Prepare fixed bilingual traffic-signal speech.")
    parser.add_argument("--tts-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = asyncio.run(generate(args.tts_url, args.output))
        print(json.dumps({"created": True, "clips": len(manifest["clips"]),
                          "audio_bytes": sum(item["byte_length"] for item in manifest["clips"])}))
    except (SignalAudioError, OSError) as error:
        code = str(error) if isinstance(error, SignalAudioError) else "signal_file_error"
        print(json.dumps({"created": False, "error": code}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
