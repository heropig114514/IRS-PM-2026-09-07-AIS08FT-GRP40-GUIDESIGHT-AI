"""Responsibilities: Generate an exclusive, fixed bilingual warning-audio resource pack.
Implementation: Validate an owned phrase plan; synthesize bounded clips through the existing
loopback TTS socket with at most two calls, write unchanged PCM in WAV containers, and assemble
two previews without silence removal, resampling, or additional provider calls.
Related Modules: frontend/tools risk-audio plan builder; core.agent.tts's existing wire contract.
Declaration Index: ResourceError; ResourceError.__init__; validate_url; safe_path; valid_text;
validate_plan; wav_bytes;
audio_record; collect_clip; build_manifest; generate; generate.worker; main.
Variable Index: CLASSES/DIRECTIONS/LANGUAGES/GRADES describe the explicit resource corpus;
MAX_PLAN_BYTES/MAX_CLIP_BYTES/MAX_PARTS bound inputs/audio; MODEL/VOICE/RATE/SAMPLE_RATE/FORMAT
retain the unchanged provider contract. IDENTIFIER/COMPONENT restrict resource identifiers/paths;
ERROR_CODES bounds body-free generation diagnostics.
"""

import argparse
import asyncio
import hashlib
import io
import itertools
import json
import re
import time
import uuid
import wave
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from websockets.asyncio.client import connect

CLASSES = ["person", "bicycle", "car", "motorcycle"]
DIRECTIONS = ["front", "front_left", "front_right"]
LANGUAGES = ["zh-CN", "en-US"]
GRADES = ["medium", "high", "critical"]
MODEL, VOICE, RATE = "qwen3-tts-flash-realtime", "Cherry", 2
SAMPLE_RATE, FORMAT = 24000, "pcm_s16le_mono"
MAX_PLAN_BYTES, MAX_CLIP_BYTES, MAX_PARTS = 262144, 524288, 8
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,95}\Z")
COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*(?:\.wav)?\Z")
ERROR_CODES = frozenset(
    {
        "invalid_plan",
        "invalid_url",
        "output_exists",
        "report_exists",
        "invalid_output",
        "file_write_failed",
        "tts_timeout",
        "tts_not_configured",
        "tts_upstream_error",
        "tts_upstream_unavailable",
        "tts_upstream_protocol",
        "tts_audio_limit",
        "invalid_message",
        "invalid_speech_request",
        "generation_cancelled",
        "resource_integrity_failed",
    }
)


class ResourceError(Exception):
    """Functionality: Expose one fixed preparation failure code.
    Logic: Exception text contains only the locally selected code.
    Constraints: Provider bodies, clip text, credentials and arbitrary paths are never included.
    """

    def __init__(self, code):
        """Functionality: Select a body-free code. Inputs: Fixed code. Outputs: Exception.
        Logic: Unknown codes become a safe resource failure. Constraints: No raw exception text.
        """
        self.code = code if code in ERROR_CODES else "resource_integrity_failed"
        super().__init__(self.code)


def validate_url(value):
    """Functionality: Admit the explicitly supplied loopback TTS endpoint.
    Inputs: WebSocket URL. Outputs: Exact URL and matching HTTP Origin.
    Logic: Permit only canonical localhost/127.0.0.1 authorities with an explicit port and the
    original or mobile-mounted TTS path. Constraints: No TLS bypass, remote hosts, credentials,
    redirect, query, fragment, default provider or secret configuration.
    """
    try:
        parts = urlsplit(value)
        if (
            not isinstance(value, str)
            or not value.isascii()
            or any(c.isspace() for c in value)
            or parts.scheme != "ws"
            or parts.hostname not in {"127.0.0.1", "localhost"}
            or not parts.port
            or parts.netloc != f"{parts.hostname}:{parts.port}"
            or parts.path not in {"/ws/tts/", "/mvp/ws/tts/"}
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError
        return value, "http://" + parts.netloc
    except (ValueError, TypeError, AttributeError):
        raise ResourceError("invalid_url") from None


def safe_path(value):
    """Functionality: Validate a portable, relative WAV resource path.
    Inputs: Plan path. Outputs: Original path. Logic: Reject traversal, absolute/drive paths,
    duplicate separators and Windows device names before filesystem access.
    Constraints: No path normalization that silently changes the requested resource identity.
    """
    reserved = {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
    if (
        not isinstance(value, str)
        or len(value) > 180
        or "\\" in value
        or not value.endswith(".wav")
        or str(PurePosixPath(value)) != value
        or PurePosixPath(value).is_absolute()
        or any(
            not COMPONENT.fullmatch(part) or part.split(".")[0].casefold() in reserved
            for part in value.split("/")
        )
    ):
        raise ResourceError("invalid_plan")
    return value


def valid_text(value, limit):
    """Functionality: Check bounded nonblank Unicode without changing punctuation or spacing.
    Inputs: Text and maximum code points. Outputs: Original text. Logic: Validate UTF-8 strictly.
    Constraints: Leading/trailing spaces may be intentional composition boundaries.
    """
    try:
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError
        value.encode("utf-8")
    except (ValueError, UnicodeEncodeError):
        raise ResourceError("invalid_plan") from None
    return value


def validate_plan(value):
    """Functionality: Validate the complete fixed resource plan before starting any synthesis.
    Inputs: Decoded plan JSON. Outputs: Detached validated plan.
    Logic: Require 66 unique clips (39 Chinese/27 English), all 144 explicit four-class/front
    direction/grade/motion/language compositions, and exact concatenated text/part identities.
    Constraints: No inferred phrase, class removal, fallback language, traversal or
    unused synthesis.
    """
    try:
        fields = {
            "schema_version",
            "voice",
            "rate",
            "sample_rate",
            "format",
            "classes",
            "directions",
            "clips",
            "compositions",
        }
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["voice"] != VOICE
            or isinstance(value["rate"], bool)
            or value["rate"] != RATE
            or type(value["sample_rate"]) is not int
            or value["sample_rate"] != SAMPLE_RATE
            or value["format"] != FORMAT
            or value["classes"] != CLASSES
            or value["directions"] != DIRECTIONS
            or not isinstance(value["clips"], list)
            or len(value["clips"]) != 66
            or not isinstance(value["compositions"], list)
            or len(value["compositions"]) != 144
        ):
            raise ValueError
        clips, paths, counts = {}, set(), {language: 0 for language in LANGUAGES}
        for clip in value["clips"]:
            if (
                not isinstance(clip, dict)
                or set(clip) != {"id", "language", "text", "kind", "path"}
                or not isinstance(clip["id"], str)
                or not IDENTIFIER.fullmatch(clip["id"])
                or clip["id"] in clips
                or clip["language"] not in LANGUAGES
                or not isinstance(clip["kind"], str)
                or not IDENTIFIER.fullmatch(clip["kind"])
            ):
                raise ValueError
            path = safe_path(clip["path"])
            if path.casefold() in paths or path.startswith("demos/"):
                raise ValueError
            valid_text(clip["text"], 120)
            paths.add(path.casefold())
            clips[clip["id"]] = clip
            counts[clip["language"]] += 1
        if counts != {"zh-CN": 39, "en-US": 27}:
            raise ValueError
        identities, ids, used = set(), set(), set()
        for composition in value["compositions"]:
            if (
                not isinstance(composition, dict)
                or set(composition)
                != {
                    "id",
                    "language",
                    "label",
                    "direction",
                    "risk_level",
                    "approaching",
                    "text",
                    "clip_ids",
                }
                or not isinstance(composition["id"], str)
                or not IDENTIFIER.fullmatch(composition["id"])
                or composition["id"] in ids
                or composition["language"] not in LANGUAGES
                or composition["label"] not in CLASSES
                or composition["direction"] not in DIRECTIONS
                or composition["risk_level"] not in GRADES
                or type(composition["approaching"]) is not bool
                or not isinstance(composition["clip_ids"], list)
                or not 1 <= len(composition["clip_ids"]) <= MAX_PARTS
            ):
                raise ValueError
            text = valid_text(composition["text"], 240)
            parts = [clips[part] for part in composition["clip_ids"]]
            if (
                any(part["language"] != composition["language"] for part in parts)
                or "".join(part["text"] for part in parts) != text
            ):
                raise ValueError
            identity = tuple(
                composition[key]
                for key in ("language", "label", "direction", "risk_level", "approaching")
            )
            if identity in identities:
                raise ValueError
            identities.add(identity)
            ids.add(composition["id"])
            used.update(composition["clip_ids"])
        expected = set(itertools.product(LANGUAGES, CLASSES, DIRECTIONS, GRADES, [False, True]))
        if identities != expected or used != set(clips):
            raise ValueError
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (KeyError, ValueError, TypeError, OverflowError):
        raise ResourceError("invalid_plan") from None


def wav_bytes(pcm):
    """Functionality: Wrap unchanged little-endian PCM samples in a standard WAV container.
    Inputs: Nonempty even PCM bytes. Outputs: Mono 24 kHz/16-bit WAV bytes.
    Logic: The wave module adds headers only. Constraints: No trimming, silence insertion,
    normalization, resampling, rate alteration or codec conversion.
    """
    if (
        not isinstance(pcm, bytes)
        or not pcm
        or len(pcm) % 2
        or len(pcm) > MAX_CLIP_BYTES * MAX_PARTS
    ):
        raise ResourceError("resource_integrity_failed")
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(pcm)
    return output.getvalue()


def audio_record(identity, path, pcm):
    """Functionality: Describe one lossless clip/assembled preview.
    Inputs: Public resource ID/path and PCM. Outputs: WAV bytes and reproducible metadata.
    Logic: Hash container and sample bytes separately, deriving duration from sample count.
    Constraints: No runtime timestamps, provider request IDs or measured alert-latency claim.
    """
    audio = wav_bytes(pcm)
    return audio, {
        "id": identity,
        "path": path,
        "sha256": hashlib.sha256(audio).hexdigest(),
        "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
        "byte_length": len(audio),
        "pcm_byte_length": len(pcm),
        "samples": len(pcm) // 2,
        "duration_ms": len(pcm) / 2 / SAMPLE_RATE * 1000,
    }


async def collect_clip(url, clip, *, opener=connect):
    """Functionality: Synthesize one public fragment through the existing local server.
    Inputs: Local URL, validated clip and optional test connector. Outputs: Complete PCM bytes.
    Logic: One UUID-bound five-field command; require fixed audio config and exact completion bytes.
    Constraints: At most 512 KiB, 60 seconds total, no retries or direct provider/secrets access;
    cancellation closes the owned WebSocket context and preserves no partial audio as a resource.
    """
    address, origin = validate_url(url)
    request_id = str(uuid.uuid4())
    chunks, count, configured = [], 0, False
    try:
        async with asyncio.timeout(60):
            async with opener(
                address,
                origin=origin,
                proxy=None,
                compression=None,
                ping_interval=None,
                max_size=MAX_CLIP_BYTES + 8192,
                max_queue=1,
                open_timeout=10,
                close_timeout=3,
            ) as peer:
                await peer.send(
                    json.dumps(
                        {
                            "type": "synthesize",
                            "request_id": request_id,
                            "text": clip["text"],
                            "language": clip["language"],
                            "rate": RATE,
                        },
                        ensure_ascii=False,
                    )
                )
                while True:
                    packet = await asyncio.wait_for(peer.recv(), 55 if not count else 35)
                    if isinstance(packet, bytes):
                        if not configured or not packet or len(packet) % 2:
                            raise ResourceError("tts_upstream_protocol")
                        count += len(packet)
                        if count > MAX_CLIP_BYTES:
                            raise ResourceError("tts_audio_limit")
                        chunks.append(packet)
                        continue
                    if not isinstance(packet, str) or len(packet.encode("utf-8")) > 8192:
                        raise ResourceError("tts_upstream_protocol")
                    try:
                        message = json.loads(packet)
                    except (ValueError, TypeError):
                        raise ResourceError("tts_upstream_protocol") from None
                    if not isinstance(message, dict) or message.get("request_id") != request_id:
                        raise ResourceError("tts_upstream_protocol")
                    if message.get("type") == "error":
                        raise ResourceError(
                            message.get("code")
                            if message.get("code") in ERROR_CODES
                            else "tts_upstream_error"
                        )
                    if (
                        message.get("type") == "audio_config"
                        and not configured
                        and not count
                        and message.get("sample_rate") == SAMPLE_RATE
                        and message.get("format") == FORMAT
                    ):
                        configured = True
                        continue
                    if (
                        message.get("type") == "complete"
                        and configured
                        and count
                        and type(message.get("audio_bytes")) is int
                        and message["audio_bytes"] == count
                    ):
                        return b"".join(chunks)
                    raise ResourceError("tts_upstream_protocol")
    except asyncio.CancelledError:
        raise
    except ResourceError:
        raise
    except TimeoutError:
        raise ResourceError("tts_timeout") from None
    except Exception:
        raise ResourceError("tts_upstream_unavailable") from None


def build_manifest(plan, records, demos):
    """Functionality: Build deterministic complete-pack integrity metadata.
    Inputs: Validated plan, generated clip records and two preview records. Outputs: Manifest.
    Logic: Preserve composition text/part order, enrich each clip and hash canonical metadata.
    Constraints: The self hash excludes only itself; no runtime report, secret or diary is included.
    """
    indexed = {record["id"]: record for record in records}
    if len(records) != len(plan["clips"]) or set(indexed) != {clip["id"] for clip in plan["clips"]}:
        raise ResourceError("resource_integrity_failed")
    manifest = {
        **plan,
        "model": MODEL,
        "clips": [{**clip, **indexed[clip["id"]]} for clip in plan["clips"]],
        "demos": demos,
    }
    canonical = json.dumps(
        manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    manifest["pack_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return manifest


async def generate(plan, url, output, report_path, *, concurrency=2, opener=connect):
    """Functionality: Prepare an exclusive resource folder and separate runtime evidence.
    Inputs: Validated plan, local URL, new output/report Paths and one/two worker slots.
    Outputs: Structured report; completed resources remain on failure for inspection.
    Logic: Claim outputs before synthesis, stop unstarted clips after first failure, then assemble
    Chinese/English previews by byte concatenation and write the complete manifest last.
    Constraints: No overwrite, retry, deployment, algorithm edit or hidden partial success.
    """
    plan = validate_plan(plan)
    validate_url(url)
    output, report_path = Path(output), Path(report_path)
    if concurrency not in {1, 2} or isinstance(concurrency, bool):
        raise ResourceError("invalid_output")
    if output.exists():
        raise ResourceError("output_exists")
    if report_path.exists():
        raise ResourceError("report_exists")
    if report_path.resolve().is_relative_to(output.resolve()):
        raise ResourceError("invalid_output")
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        report_file = report_path.open("x", encoding="utf-8")
    except FileExistsError:
        raise ResourceError("report_exists") from None
    try:
        output.mkdir(exist_ok=False)
    except FileExistsError:
        report_file.close()
        raise ResourceError("output_exists") from None
    root = output.resolve()
    failed = asyncio.Event()
    position = 0
    records, pcm_by_id = [], {}
    report = {
        "kind": "risk_audio_resource_generation",
        "complete": False,
        "voice": VOICE,
        "model": MODEL,
        "rate": RATE,
        "sample_rate": SAMPLE_RATE,
        "format": FORMAT,
        "requested": len(plan["clips"]),
        "completed": 0,
        "concurrency": concurrency,
        "failures": [],
        "clips": records,
        "physical_audio_verified": False,
    }
    started = time.monotonic()

    async def worker():
        """Functionality: Consume admitted plan clips once within the shared worker budget.
        Inputs: Shared next index/failure flag. Outputs: Complete exclusive WAV files/records.
        Logic: Check failure before admission; a running second clip may finish after a peer fails.
        Constraints: No retry; failed/partial PCM is never installed as a complete resource.
        """
        nonlocal position
        while not failed.is_set() and position < len(plan["clips"]):
            clip = plan["clips"][position]
            position += 1
            try:
                pcm = await collect_clip(url, clip, opener=opener)
                audio, record = audio_record(clip["id"], clip["path"], pcm)
                target = output / clip["path"]
                if not target.resolve().is_relative_to(root):
                    raise ResourceError("invalid_output")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(audio)
                pcm_by_id[clip["id"]] = pcm
                records.append(record)
                report["completed"] = len(records)
                print(
                    json.dumps({"completed": len(records), "total": len(plan["clips"])}), flush=True
                )
            except ResourceError as error:
                failed.set()
                report["failures"].append({"clip_id": clip["id"], "code": error.code})
            except (OSError, ValueError):
                failed.set()
                report["failures"].append({"clip_id": clip["id"], "code": "file_write_failed"})

    try:
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        if not failed.is_set():
            demos = []
            for language in LANGUAGES:
                composition = next(
                    item
                    for item in plan["compositions"]
                    if item["language"] == language
                    and item["label"] == "person"
                    and item["direction"] == "front_left"
                    and item["risk_level"] == "critical"
                    and item["approaching"]
                )
                pcm = b"".join(pcm_by_id[part] for part in composition["clip_ids"])
                path = f"demos/{language}-person-front-left-approaching.wav"
                audio, record = audio_record("demo_" + language.lower(), path, pcm)
                target = output / path
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(audio)
                demos.append(
                    {
                        **record,
                        "language": language,
                        "text": composition["text"],
                        "composition_id": composition["id"],
                        "clip_ids": composition["clip_ids"],
                    }
                )
            manifest = build_manifest(plan, records, demos)
            with (output / "manifest.json").open("x", encoding="utf-8") as stream:
                json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
            report.update(complete=True, pack_sha256=manifest["pack_sha256"], demo_count=len(demos))
    except asyncio.CancelledError:
        report["failures"].append({"code": "generation_cancelled"})
        raise
    except (OSError, ResourceError, ValueError):
        report["failures"].append({"code": "resource_integrity_failed"})
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["unstarted"] = len(plan["clips"]) - position
        json.dump(report, report_file, ensure_ascii=False, indent=2, allow_nan=False)
        report_file.write("\n")
        report_file.close()
    return report


def main(argv=None):
    """Functionality: Execute one explicit local-only resource build.
    Inputs: Plan, URL, new output/report and concurrency. Outputs: Safe JSON summary/exit code.
    Logic: Bound and validate plan before any transport; delegate one generation without retry.
    Constraints: No environment secret lookup, service installation/startup, upload or deployment.
    """
    parser = argparse.ArgumentParser(description="Prepare fixed bilingual risk-audio resources")
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--concurrency", type=int, choices=(1, 2), default=2)
    args = parser.parse_args(argv)
    try:
        with args.plan.open("rb") as stream:
            raw = stream.read(MAX_PLAN_BYTES + 1)
        if len(raw) > MAX_PLAN_BYTES:
            raise ResourceError("invalid_plan")
        plan = validate_plan(json.loads(raw))
        report = asyncio.run(
            generate(plan, args.url, args.output, args.report, concurrency=args.concurrency)
        )
    except (OSError, ValueError, ResourceError) as error:
        print(
            json.dumps(
                {
                    "complete": False,
                    "code": error.code if isinstance(error, ResourceError) else "invalid_plan",
                }
            )
        )
        return 1
    print(
        json.dumps(
            {
                "complete": report["complete"],
                "completed": report["completed"],
                "failure_codes": [item["code"] for item in report["failures"]],
            }
        )
    )
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
