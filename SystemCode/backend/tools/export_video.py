"""Responsibilities: Provide a backend command for complete annotated-video export.
Implementation: Validate CLI choices and delegate media/inference work to video_export.services.
Related Modules: VIDEO-EXPORT.md; requirements-video.txt; video_export.services.
Declaration Index: main.
Variable Index: None.
"""

import argparse
import logging
from pathlib import Path

from core.detection_model import PROFILES


def main(argv=None):
    """Functionality: Export a local video with boxes, meters, risk and its existing audio.
    Inputs: Optional CLI argument list. Outputs: Exit code and output path or actionable error.
    Logic: Set explicit offline model-library behavior; call one application export service.
    Constraints: No web service, original-file upload, source overwrite or synthesized speech.
    """
    import os

    parser = argparse.ArgumentParser(description="Export an entire annotated detection video")
    parser.add_argument("--input", required=True, type=Path, help="Existing local video file")
    parser.add_argument("--output", required=True, type=Path, help="New output directory")
    parser.add_argument("--model", choices=sorted(PROFILES),
                        help="Detection model; defaults to PRS_DETECTION_MODEL or yolo26n-walk")
    parser.add_argument("--max-edge", type=int, default=640,
                        help="Inference/output longest edge, 64..1280 (default: 640)")
    parser.add_argument("--profile", choices=("stable", "baseline"), default="stable",
                        help="Versioned video refinement or original perception (default: stable)")
    parser.add_argument("--show-ids", action="store_true", help="Include debug track IDs in labels")
    args = parser.parse_args(argv)
    os.environ["YOLO_OFFLINE"], os.environ["YOLO_AUTOINSTALL"] = "1", "0"
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    try:
        from video_export.services import export_video

        export_video(args.input, args.output, detection_model=args.model, max_edge=args.max_edge,
                     profile=args.profile, show_ids=args.show_ids)
    except ImportError as error:
        print(f"Missing video/inference dependency: {error}. Install requirements-video.txt.")
        return 1
    except KeyboardInterrupt:
        print("Video export cancelled; inspect run.json in the output directory.")
        return 130
    except Exception as error:
        print(f"Video export failed ({type(error).__name__}): {error}")
        return 1
    print(f"Video: {(args.output / 'annotated.mp4').resolve()}")
    print(f"Per-frame results: {(args.output / 'results.jsonl').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
