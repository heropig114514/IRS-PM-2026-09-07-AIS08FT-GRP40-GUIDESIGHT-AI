"""Responsibilities: Experiment with bounded reuse of complete label glyph rasters.
Implementation: Explicit single-worker LRU; preserve fractional FreeType starts, glyph offsets,
original backgrounds and clipping. Production entry points do not import this module.
Related Modules: core.detector; tools.benchmark_inference/validate_label_cache.
Declaration Index: LabelRasterCache; LabelRasterCache.__init__; LabelRasterCache.__call__;
LabelRasterCache.annotate; LabelRasterCache.snapshot.
Variable Index: None; per-instance limits bound raster bytes, key length and entry count,
not transient glyph allocation or the complete Python/Pillow heap. Fonts must remain immutable.
"""

import logging
import math
import time
from collections import OrderedDict

from PIL import Image, ImageFont

from core.detector import annotate


class LabelRasterCache:
    """Functionality: Reuse exact full-text masks in an explicit rendering experiment.
    Logic: cache by immutable font identity/size/layout, complete text, color, mode and fractional
    start; paste via the same Pillow bitmap operation used by ImageDraw.text.
    Constraints: single worker, FreeType L-mode single-line labels only; no font mutation,
    quantization, retry or implicit fallback. Oversized entries fail before allocation/admission.
    """

    def __init__(self, *, max_entries=256, max_raster_bytes=4 * 1024 * 1024):
        """Functionality: Create an empty bounded candidate with visible accounting.
        Inputs: positive entry/raster limits. Outputs: independent cache instance.
        Logic: allocate no glyphs until requested. Constraints: limits are not process RSS bounds.
        """
        if type(max_entries) is not int or type(max_raster_bytes) is not int:
            raise ValueError("Cache limits must be positive integers")
        if max_entries < 1 or max_raster_bytes < 1:
            raise ValueError("Cache limits must be positive integers")
        self.max_entries, self.max_raster_bytes = max_entries, max_raster_bytes
        self._entries = OrderedDict()
        self.hits = self.misses = self.evictions = self.raster_bytes = self.peak_raster_bytes = 0
        self.hit_ms = self.miss_ms = 0.0
        logging.getLogger(__name__).info("Label raster probe limits entries=%d raster_bytes=%d",
                                         max_entries, max_raster_bytes)

    def __call__(self, draw, xy, text, *, font, fill):
        """Functionality: Draw a full label with exact fractional glyph positioning.
        Inputs: original draw target, text coordinates, complete string, immutable font and color.
        Outputs: original bitmap blend; counters include raster lookup/build and draw cost.
        Logic: retain FreeType offset, use int/modf as Pillow does, evict oldest masks to fit.
        Constraints: key text at most 4096 UTF-8 bytes; only bundled-style FreeType L rendering;
        allocation/admission failures propagate, and cached masks are never exposed or modified.
        """
        started = time.perf_counter()
        if (not isinstance(font, ImageFont.FreeTypeFont) or draw.fontmode != "L"
                or "\n" in text or len(text.encode("utf-8")) > 4096):
            logging.getLogger(__name__).warning("Label raster probe rejected unsupported input")
            raise ValueError("Label raster probe requires bounded single-line FreeType L text")
        phase = tuple(math.modf(value)[0] for value in xy)
        key = (font, font.size, font.layout_engine, text, fill, draw.fontmode, phase)
        hit = key in self._entries
        if hit:
            mask, offset = self._entries.pop(key)
            self.hits += 1
        else:
            self.misses += 1
            # Bbox includes bearings; margin covers the fractional raster expansion.
            bounds = font.getbbox(text)
            if (bounds[2] - bounds[0] + 2) * (bounds[3] - bounds[1] + 2) > self.max_raster_bytes:
                logging.getLogger(__name__).warning("Label raster probe rejected oversized entry")
                raise ValueError("Label raster exceeds cache capacity")
            raster, offset = font.getmask2(text, mode=draw.fontmode, start=phase)
            size = raster.size[0] * raster.size[1]
            if size > self.max_raster_bytes:
                raise ValueError("Label raster exceeds cache capacity")
            mask = Image.frombytes("L", raster.size, bytes(raster))
            while (len(self._entries) >= self.max_entries
                   or self.raster_bytes + size > self.max_raster_bytes):
                _, (evicted, _) = self._entries.popitem(last=False)
                self.raster_bytes -= evicted.width * evicted.height
                self.evictions += 1
            self.raster_bytes += size
            self.peak_raster_bytes = max(self.peak_raster_bytes, self.raster_bytes)
        self._entries[key] = (mask, offset)
        draw.bitmap((int(xy[0]) + offset[0], int(xy[1]) + offset[1]), mask, fill=fill)
        elapsed = (time.perf_counter() - started) * 1000
        if hit:
            self.hit_ms += elapsed
        else:
            self.miss_ms += elapsed

    def annotate(self, image, boxes):
        """Functionality: Substitute only text drawing in the unchanged annotation contract.
        Inputs: original RGB image and actual boxes. Outputs: original-format JPEG bytes.
        Logic: shared formatter/layout/background/encoder invokes this explicit renderer.
        Constraints: no model, tracking, source-image mutation or production activation.
        """
        return annotate(image, boxes, text_renderer=self)

    def snapshot(self):
        """Functionality: Export counters without retaining image or text contents.
        Inputs: current state. Outputs: JSON-compatible accounting and limits.
        Logic: compute hit ratio from lookups. Constraints: times are nested within render.
        """
        requests = self.hits + self.misses
        return {"hits": self.hits, "misses": self.misses, "evictions": self.evictions,
                "hit_ratio": self.hits / requests if requests else None,
                "entries": len(self._entries), "raster_bytes": self.raster_bytes,
                "peak_raster_bytes": self.peak_raster_bytes, "hit_ms": self.hit_ms,
                "miss_ms": self.miss_ms, "max_entries": self.max_entries,
                "max_raster_bytes": self.max_raster_bytes, "max_key_utf8_bytes": 4096}
