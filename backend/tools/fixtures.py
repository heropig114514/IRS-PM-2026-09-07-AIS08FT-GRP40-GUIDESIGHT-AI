"""Responsibilities: Reproduce original public benchmark JPEG inputs.
Implementation: Established RGB/max640/bilinear/JPEG80 transformation, without network or inference.
Related Modules: tools.benchmark_live_remote/benchmark_zerogpu_live/kaggle_acceleration.
Declaration Index: fixture_bytes.
Variable Index: None.
"""

import io

from PIL import Image


def fixture_bytes(path, max_edge=640):
    """Functionality: Reproduce the existing benchmark's camera-format bytes exactly once.
    Inputs: public source JPEG path, long-edge cap (default 640, the established contract);
    Outputs: JPEG80 bytes and resized dimensions.
    Logic: preserve the established RGB/max-edge640/bilinear/round/JPEG80 fixture contract.
    Constraints: preserves evaluation inputs; do not re-encode decoded prepared images again.
    """
    with Image.open(path) as original:
        image = original.convert("RGB")
        scale = min(1, max_edge / max(image.size))
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)), Image.Resampling.BILINEAR
        )
        output = io.BytesIO()
        image.save(output, "JPEG", quality=80)
        return output.getvalue(), image.size
