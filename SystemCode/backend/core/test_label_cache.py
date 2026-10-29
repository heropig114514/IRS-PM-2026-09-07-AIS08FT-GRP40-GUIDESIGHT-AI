"""Responsibilities: Protect exact renderer output, bounded retention and explicit failures.
Implementation: Real Pillow pre-JPEG/JPEG comparisons across layout/semantic edge cases; bounded
LRU reuse on different backgrounds, new fonts and actual fractional coordinates.
Related Modules: core.detector; tools.label_cache_probe/validate_label_cache.
Declaration Index: LabelCacheTests; LabelCacheTests.test_pixels_and_labels;
LabelCacheTests.test_fractional_text; LabelCacheTests.test_identity_and_background;
LabelCacheTests.test_lru_limits; LabelCacheTests.test_explicit_rejection;
LabelCacheTests.test_failure_propagates; LabelCacheTests.test_failure_propagates.failed;
LabelCacheTests.test_probe_exclusion.
Variable Index: None; synthetic rendering cases are not model-accuracy data.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from PIL import Image, ImageDraw, ImageFont

from core.detector import annotate
from tools.benchmark_inference import run
from tools.label_cache_probe import LabelRasterCache
from tools.validate_label_cache import compare_render


class LabelCacheTests(SimpleTestCase):
    """Functionality: Verify output equality and cache ownership under realistic label changes.
    Logic: compare original real rendering with misses, hits and evictions before JPEG as well.
    Constraints: no mock text rendering or inference-equivalence/performance claims.
    """

    def test_pixels_and_labels(self):
        """Functionality: Preserve clipping, overlap, long labels and distance/motion/risk variants.
        Inputs: varied frames, fractional edge boxes and real label fields. Outputs: exact RGB/JPEG.
        Logic: repeat each case to exercise cache hits; retain input pixels for ownership checks.
        Constraints: uses bundled font, including its original unsupported-Chinese glyph behavior.
        """
        variants = [{}, {"distance_m": None}, {"distance_m": 3.25},
                    {"distance_m": 3.25, "track_id": 1, "motion_state": "unknown"},
                    {"distance_m": 3.25, "track_id": 1, "motion_state": "approaching",
                     "relative_speed_mps": -0.83},
                    {"distance_m": 3.25, "track_id": 1, "motion_state": "receding",
                     "relative_speed_mps": float("inf")},
                    {"distance_m": 1.25, "track_id": 4, "motion_state": "approaching",
                     "risk_level": "critical", "ttc_sec": 1.8},
                    {"distance_m": 1.25, "track_id": 4, "motion_state": "unknown",
                     "risk_level": "high", "ttc_sec": None}]
        cache = LabelRasterCache()
        for size in ((640, 360), (51, 19), (320, 640)):
            image = Image.new("RGB", size, (71, 129, 213))
            unchanged = image.tobytes()
            for label in ("person", "行人", "jQ" * 30):
                for extra in variants:
                    boxes = [{"xyxy": coords, "label": label, "score": 0.837, **extra}
                             for coords in ((0, 0, 25, 18), (12.125, 44.875, 48.25, 90),
                                            (size[0] - 8.75, size[1] - 2.25,
                                             size[0] - 1, size[1] - 1))]
                    for _ in range(2):
                        _, result = compare_render(image, boxes, cache)
                        self.assertTrue(result["rgb_exact"])
                        self.assertTrue(result["jpeg_exact"])
                    self.assertEqual(image.tobytes(), unchanged)
        _, empty = compare_render(image, [], cache)
        self.assertTrue(empty["rgb_exact"] and empty["jpeg_exact"])
        self.assertGreater(cache.hits, 0)

    def test_fractional_text(self):
        """Functionality: Preserve text raster phases including negative/near-integer coordinates.
        Inputs: direct original/cached draw operations. Outputs: identical pre-JPEG bytes.
        Logic: 64 raster fractions plus near-boundary/negative positions; repeat cached call.
        Constraints: phases remain exact floats and are never rounded for higher hit rates.
        """
        cache, font = LabelRasterCache(), ImageFont.load_default(size=14)
        for fraction in (*[i / 64 for i in range(64)], 0.999999999, -0.125, -3.875):
            for xy in ((3 + fraction, 5.5), (1.125, fraction)):
                original = Image.new("RGB", (120, 35), (39, 40, 41))
                ImageDraw.Draw(original).text(xy, "jQ Å 行人", font=font, fill="white")
                for _ in range(2):
                    candidate = Image.new("RGB", original.size, (39, 40, 41))
                    cache(ImageDraw.Draw(candidate), xy, "jQ Å 行人", font=font, fill="white")
                    self.assertEqual(original.tobytes(), candidate.tobytes())

    def test_identity_and_background(self):
        """Functionality: Avoid reusing another font/color raster or prior frame background.
        Inputs: changed font identity, size, fill and background. Outputs: exact fresh pixels.
        Logic: use a shared cache over independent images. Constraints: fonts remain immutable.
        """
        cache = LabelRasterCache()
        for font in (ImageFont.load_default(size=14), ImageFont.load_default(size=21)):
            for color in ("white", (1, 200, 90)):
                for background in ("black", "red", "blue"):
                    original = Image.new("RGB", (300, 50), background)
                    candidate = original.copy()
                    ImageDraw.Draw(original).text((2.375, 2.125), "person 0.90",
                                                  font=font, fill=color)
                    cache(ImageDraw.Draw(candidate), (2.375, 2.125), "person 0.90",
                          font=font, fill=color)
                    self.assertEqual(candidate.tobytes(), original.tobytes())
        self.assertEqual((cache.misses, cache.hits), (4, 8))

    def test_lru_limits(self):
        """Functionality: Bound retained raster memory and evict least-recently-used entries.
        Inputs: two-entry cache and alternating labels, then a byte-constrained cache.
        Outputs: predictable misses/hits/evictions, limits respected and exact redrawn pixels.
        Logic: touch first label before adding third; require second to miss; test byte pressure.
        Constraints: byte bound covers retained masks, not transient rasterization or Python heap.
        """
        font, cache = ImageFont.load_default(size=14), LabelRasterCache(max_entries=2)
        for text in ("A", "B", "A", "C", "B"):
            cache(ImageDraw.Draw(Image.new("RGB", (50, 40))), (0, 0), text, font=font, fill="white")
        self.assertEqual((cache.hits, cache.misses, cache.evictions), (1, 4, 2))
        cache = LabelRasterCache(max_raster_bytes=250)
        for text in ("A", "B", "C", "D", "A"):
            original = Image.new("RGB", (50, 40), "blue")
            candidate = original.copy()
            ImageDraw.Draw(original).text((0.25, 0.5), text, font=font, fill="white")
            cache(ImageDraw.Draw(candidate), (0.25, 0.5), text, font=font, fill="white")
            self.assertEqual(candidate.tobytes(), original.tobytes())
            self.assertLessEqual(cache.raster_bytes, 250)
        self.assertGreater(cache.evictions, 0)

    def test_explicit_rejection(self):
        """Functionality: Reject unsupported/oversized input without hidden normal-render fallback.
        Inputs: invalid capacities, multiline/oversized text and too-small raster capacity.
        Outputs: ValueError with no cached content. Logic: exercise real admission boundary.
        Constraints: experimental API only; default production rendering is unaffected.
        """
        for value in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                LabelRasterCache(max_entries=value)
        image, font = Image.new("RGB", (50, 40)), ImageFont.load_default(size=14)
        for cache, text in ((LabelRasterCache(), "a\nb"), (LabelRasterCache(), "x" * 4097),
                            (LabelRasterCache(max_raster_bytes=1), "person")):
            with self.assertRaises(ValueError):
                cache(ImageDraw.Draw(image), (0, 0), text, font=font, fill="white")
            self.assertEqual(cache.snapshot()["entries"], 0)

    def test_failure_propagates(self):
        """Functionality: Preserve diagnostic renderer failure without an implicit fallback/retry.
        Inputs: a deliberately failing renderer. Outputs: original exception and unchanged image.
        Logic: inject through annotation's explicit callback. Constraints: not a rendering oracle.
        """
        def failed(*args, **kwargs):
            """Functionality: Raise the original test error. Inputs: callback args. Outputs: error.
            Logic: unconditional raise. Constraints: no retries or external side effects.
            """
            raise RuntimeError("original render failure")

        image = Image.new("RGB", (120, 80))
        with self.assertRaisesMessage(RuntimeError, "original render failure"):
            annotate(image, [{"xyxy": (1, 2, 20, 30), "label": "person", "score": .9}],
                     text_renderer=failed)
        self.assertEqual(image.tobytes(), bytes(120 * 80 * 3))

    def test_probe_exclusion(self):
        """Functionality: Prevent stacking independent optimization candidates in the benchmark.
        Inputs: label cache plus depth Graph flag. Outputs: error before model construction.
        Logic: benchmark preflight only. Constraints: no GPU allocation or output creation.
        """
        with (self.assertRaises(ValueError),
              patch("tools.benchmark_inference.build_detector") as build):
            run(SimpleNamespace(frames=31, mode="full", label_cache=True, depth_graph=True,
                                output=Path("unused")))
        build.assert_not_called()
