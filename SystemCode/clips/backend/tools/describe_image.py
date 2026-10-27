"""Responsibilities: Evaluate the VLM scene description on local images from the terminal.
Implementation: In-process core.vlm.describe_sync (no HTTP/Origin/server);
SystemCode/clips/backend/.env supplies DASHSCOPE_*; Pillow shrinks each image in memory to the
live-stream contract (long edge 640, RGB,
JPEG quality 80); optional run.json + results.jsonl in a new directory.
Related Modules: core.vlm; VLM.md; model_tests.run_depth (CLI style).
Persistent errors unrelated to the image (STOP_CODES) stop the batch immediately.
Declaration Index: load_environment; collect_images; prepare_jpeg; describe_one; write_json_line;
run; build_parser; main.
Variable Index: STOP_CODES — persistent non-image errors that end the run;
ROOT — backend directory; IMAGE_SUFFIXES — accepted extensions;
MAX_EDGE/JPEG_QUALITY — live-stream contract; DEFAULT_INTERVAL — 6.5 s keeps under 10 req/min;
logger — codes/counts only, never keys, questions or answers.
"""

import argparse
import io
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from core import vlm

ROOT = Path(__file__).resolve().parents[1]
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp"})
MAX_EDGE = 640
JPEG_QUALITY = 80
DEFAULT_INTERVAL = 6.5
STOP_CODES = frozenset({"vlm_not_configured", "vlm_authentication_failed", "vlm_quota_exceeded"})
logger = logging.getLogger(__name__)


def load_environment():
    """Functionality: Load SystemCode/clips/backend/.env like config.base without starting Django.
    Inputs: none. Outputs: none; fills os.environ.
    Logic: override=False so real process variables win. Constraints: never prints values.
    """
    load_dotenv(ROOT / ".env", override=False)


def collect_images(source):
    """Functionality: Discover one image or every supported image under a directory.
    Inputs: source Path. Outputs: list of (display_name, Path) sorted by relative path.
    Logic: recursive, no sampling or limit; the display name is the path relative to the source.
    Constraints: ValueError for missing, unsupported or empty sources.
    """
    source = source.resolve()
    if source.is_file() and source.suffix.lower() in IMAGE_SUFFIXES:
        return [(source.name, source)]
    if not source.is_dir():
        raise ValueError("Input must be an existing image or image directory")
    found = sorted(
        (path.relative_to(source).as_posix(), path)
        for path in source.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if not found:
        raise ValueError("Input directory contains no supported images")
    return found


def prepare_jpeg(path):
    """Functionality: Produce the JPEG bytes sent to the model, matching the live-stream contract.
    Inputs: image Path. Outputs: JPEG bytes (long edge <= 640, RGB, quality 80, <= 1 MiB).
    Logic: honour EXIF orientation, shrink only (never enlarge), flatten to RGB, encode in memory.
    Constraints: nothing is written to disk; undecodable input raises OSError/ValueError and
    oversize output raises ValueError (caller records it as a per-file failure).
    """
    from PIL import Image, ImageOps

    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((MAX_EDGE, MAX_EDGE))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    data = buffer.getvalue()
    if len(data) > vlm.MAX_IMAGE_BYTES:
        raise ValueError("Encoded image exceeds 1 MiB")
    return data


def describe_one(name, path, question, language):
    """Functionality: Describe one image and return a result record.
    Inputs: display name, Path, question, language. Outputs: dict with file, request_id, model,
    elapsed_ms, usage, text or error/detail.
    Logic: failures become records (code only) so the batch continues; this is logging per item,
    not a retry. Constraints: only SceneError and image-decoding errors are caught.
    """
    request_id = vlm.new_request_id()
    started = time.monotonic()
    record = {"file": name, "request_id": request_id}
    try:
        result = vlm.describe_sync(
            prepare_jpeg(path), None, question, language, request_id=request_id
        )
    except vlm.SceneError as error:
        record.update(error=error.code, detail=error.detail)
    except (OSError, ValueError) as error:
        record.update(error="image_unreadable", detail=type(error).__name__)
    else:
        record.update(model=result["model"], usage=result["usage"], text=result["text"])
    record["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return record


def write_json_line(handle, record):
    """Functionality: Append one JSONL record. Inputs: open text handle, dict. Outputs: none.
    Logic: ensure_ascii=False and flush so an interrupted run keeps finished rows.
    """
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    handle.flush()


def run(args, images, model):
    """Functionality: Process images in order with a fixed delay and report the outcome.
    Inputs: parsed args, [(name, Path)], configured model name. Outputs: exit status 0 or 1.
    Logic: sleep args.interval between requests (not before the first or after the last);
    print one line per image; with --output stream results.jsonl and write run.json at the end
    (also on Ctrl-C). A STOP_CODES error (config/auth/quota: it would hit every remaining image)
    ends the run at once: no sleep, no further requests, remaining images get no results.jsonl
    row; run.json records stopped_reason and skipped. This is not a retry.
    Constraints: exit 0 only when every image succeeded.
    """
    started_at = datetime.now(UTC)
    ok = failed = 0
    results = None
    if args.output:
        args.output.mkdir(parents=True)
        results = (args.output / "results.jsonl").open("w", encoding="utf-8")
    complete = False
    stopped_reason = None
    skipped = 0
    try:
        for index, (name, path) in enumerate(images, 1):
            if index > 1 and args.interval > 0:
                time.sleep(args.interval)
            record = describe_one(name, path, args.question, args.language)
            if "error" in record:
                failed += 1
                print(f"[{index}/{len(images)}] {name}  {record['elapsed_ms']}ms  "
                      f"FAILED {record['error']}")
            else:
                ok += 1
                print(f"[{index}/{len(images)}] {name}  {record['elapsed_ms']}ms  "
                      f"{record['text']}")
            if results:
                write_json_line(results, record)
            if record.get("error") in STOP_CODES:
                stopped_reason = record["error"]
                skipped = len(images) - index
                print(f"Stopped: {stopped_reason} will affect every image; "
                      f"{skipped} remaining image(s) skipped")
                break
        complete = stopped_reason is None
    finally:
        if results:
            results.close()
            run_info = {
                "settings": {
                    "source": str(args.source.resolve()),
                    "question": args.question,
                    "language": args.language,
                    "interval_seconds": args.interval,
                    "max_edge": MAX_EDGE,
                    "jpeg_quality": JPEG_QUALITY,
                },
                "model": model,
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "images": len(images),
                "succeeded": ok,
                "failed": failed,
                "complete": complete,
                "stopped_reason": stopped_reason,
                "skipped": skipped,
            }
            (args.output / "run.json").write_text(
                json.dumps(run_info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    print(f"Done: {ok} succeeded, {failed} failed")
    return 0 if failed == 0 and complete else 1


def build_parser():
    """Functionality: Define the command-line interface. Inputs: none. Outputs: ArgumentParser.
    Logic: explicit options only; defaults mirror the live contract. Constraints: no key option;
    the key is read from the environment/.env only.
    """
    parser = argparse.ArgumentParser(
        prog="python -m tools.describe_image",
        description=(
            "Describe local images with the VLM (in-process, uses "
            "SystemCode/clips/backend/.env)"
        ),
    )
    parser.add_argument("source", type=Path, help="Image file or directory (recursive)")
    parser.add_argument("--question", help="Optional question, 1..200 characters")
    parser.add_argument("--language", choices=sorted(vlm.LANGUAGES), default="zh-CN")
    parser.add_argument("--output", type=Path, help="New directory for run.json and results.jsonl")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help="Seconds between requests; default 6.5 (<= 10 requests/minute)")
    return parser


def main(argv=None):
    """Functionality: Terminal entry point. Inputs: optional argv. Outputs: exit status 0/1
    (argument errors exit 2 via argparse).
    Logic: validate arguments, discover images, check VLM configuration, then run.
    Constraints: errors show stable codes/messages only, never key or request content.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.interval >= 0 or args.interval == float("inf"):
        parser.error("--interval must be a finite number >= 0")
    if args.question is not None:
        args.question = args.question.strip()
        if not 1 <= len(args.question) <= vlm.MAX_QUESTION_CHARS:
            parser.error(f"--question must be 1..{vlm.MAX_QUESTION_CHARS} characters")
    if args.output is not None and args.output.exists():
        parser.error("--output must be a new directory that does not exist yet")
    try:
        images = collect_images(args.source)
    except ValueError as error:
        parser.error(str(error))
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("Pillow is required (pip install -r requirements-dev.txt)")
        return 1
    logging.basicConfig(level=logging.WARNING)
    load_environment()
    try:
        _, _, model = vlm.vlm_settings()
    except vlm.SceneError as error:
        print(f"Not configured [{error.code}]: {error.detail}")
        return 1
    return run(args, images, model)


if __name__ == "__main__":
    sys.exit(main())
