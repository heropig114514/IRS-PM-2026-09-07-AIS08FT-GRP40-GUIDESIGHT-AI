"""Responsibilities: Measure real YOLO inference with Django under deployment limits.
Implementation: Five serial predictions on an explicit public test JPEG; Linux RSS measurement.
Related Modules: core.detector, deploy/README.md; invoke in a temporary limited systemd unit.
Declaration Index: main — initialize, measure and report the bounded sample.
Variable Index: None; CLI model/image/output paths are explicit and contain no camera data.
"""

import argparse
import json
import resource
import sys
import time
from pathlib import Path


def main():
    """Functionality: Benchmark the actual CPU pipeline and report sample limitations.
    Inputs: --model/--image, optional --output for the public fixture; existing Django environment.
    Outputs: JSON timing/count/RSS, optional fixture annotation; failure terminates without retry.
    Logic: load Django and Detector together, run five frames, measure total time and peak RSS.
    Constraints: Linux only; not a camera/concurrency benchmark or general accuracy evaluation.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).parents[1]))

    import django

    django.setup()
    from core.detector import Detector, decode_frame

    model = Detector(str(arguments.model.resolve()))
    image = decode_frame(arguments.image.read_bytes())
    times = []
    for _ in range(5):
        started = time.monotonic()
        jpeg, boxes, inference_ms = model.predict(image)
        times.append((time.monotonic() - started) * 1000)
    if arguments.output:
        arguments.output.write_bytes(jpeg)
    print(
        json.dumps(
            {
                "frames": len(times),
                "total_ms_mean": sum(times) / len(times),
                "total_ms_max": max(times),
                "last_inference_ms": inference_ms,
                "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                "detections": len(boxes),
                "labels": [box["label"] for box in boxes],
            }
        )
    )


if __name__ == "__main__":
    main()
