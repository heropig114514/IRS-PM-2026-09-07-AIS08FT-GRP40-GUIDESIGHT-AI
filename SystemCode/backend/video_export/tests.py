"""Responsibilities: Verify complete media export, original audio and failure publication.
Implementation: Real encoded synthetic clips with deterministic injected perception results.
Related Modules: video_export.services/media; tools.export_video.
Declaration Index: make_clip; FakeDetector; FakeDetector.__init__; FakeDetector.predict;
ExportTests; ExportTests.setUp; ExportTests.export; ExportTests.test_vfr_and_audio_roundtrip;
ExportTests.test_no_live_session_limit; ExportTests.test_failure_retains_partial_status;
ExportTests.test_input_and_overwrite_guards; ExportTests.test_rotation_and_odd_padding;
ExportTests.test_every_risk_level_is_visible; ExportTests.test_empty_detections;
ExportTests.test_requires_complete_pipeline; ExportTests.test_missing_video_fails;
ExportTests.test_cli_errors_and_success.
Declaration Index (continued): ExportTests.test_non_square_pixels.
Variable Index: None. Fixture inference does not establish model accuracy.
"""

import io
import json
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import av
import numpy as np
from PIL import Image, ImageDraw

from realtime.risk import RISK_MODEL
from tools.export_video import main
from video_export.media import audio_fingerprints
from video_export.services import annotate_frame, export_video


def make_clip(path, times=(0, 40, 120, 160), *, audio=False, rotation=0, rate=25, pixel_aspect=1):
    """Functionality: Create a real, short independently encoded source clip.
    Inputs: Path, millisecond PTS, audio/rotation/rate options. Outputs: Source MP4.
    Logic: Encode numbered-color RGB frames plus optional AAC tone; set source packet durations.
    Constraints: Fixtures only; audio uses mono 48 kHz and source geometry is 128x96.
    """
    with av.open(str(path), "w", options={"avoid_negative_ts": "disabled"}) as container:
        video = container.add_stream("libx264", rate=rate)
        video.width, video.height, video.pix_fmt = 128, 96, "yuv420p"
        video.codec_context.time_base = Fraction(1, 1000)
        video.codec_context.sample_aspect_ratio = Fraction(pixel_aspect)
        video.options = {"tune": "zerolatency", "bf": "0"}
        if rotation:
            video.set_display_rotation(rotation)
        if audio:
            sound = container.add_stream("aac", rate=48000)
            sound.layout = "mono"
        end = times[-1] + 1000 // rate
        gaps = [b - a for a, b in zip(times, times[1:], strict=False)] + [1000 // rate]
        durations = dict(zip(times, gaps, strict=True))
        packets = []
        for index, timestamp in enumerate(times):
            frame = av.VideoFrame.from_image(Image.new("RGB", (128, 96), (index % 255, 40, 80)))
            frame.pts, frame.time_base = timestamp, Fraction(1, 1000)
            for packet in video.encode(frame):
                packet.duration = durations[int(packet.pts * packet.time_base * 1000)]
                packets.append(packet)
        packets.extend(video.encode(None))
        if audio:
            for start in range(0, end * 48, 1024):
                values = np.sin(2 * np.pi * 440 * np.arange(start, start + 1024) / 48000)
                frame = av.AudioFrame.from_ndarray((values * .1).astype("float32")[None, :],
                                                   format="fltp", layout="mono")
                frame.sample_rate, frame.time_base, frame.pts = 48000, Fraction(1, 48000), start
                packets.extend(sound.encode(frame))
            packets.extend(sound.encode(None))
        for packet in sorted(packets, key=lambda packet: packet.dts * packet.time_base):
            container.mux(packet)


class FakeDetector:
    """Functionality: Supply deterministic frame metadata without loading GPU models.
    Logic: Record inference times and return one known box. Constraints: No accuracy claims.
    """

    def __init__(self):
        """Functionality: Initialize observations. Inputs: None. Outputs: Detector.
        Logic: Start empty timestamp/size lists. Constraints: Per-test state only.
        """
        self.times, self.sizes = [], []

    def predict(self, jpeg, timestamp=None):
        """Functionality: Record model inputs and return a complete display contract.
        Inputs: JPEG and source time. Outputs: Metadata/unused annotation.
        Logic: One tracked person with known distance/risk. Constraints: Test inference only.
        """
        self.times.append(timestamp)
        with Image.open(io.BytesIO(jpeg)) as image:
            self.sizes.append(image.size)
        return {"tracking_model": "botsort", "risk_model": RISK_MODEL,
                "refinement_profile": "video-stable-v6",
                "boxes": [{"xyxy": [10, 30, 50, 65], "label": "person", "distance_m": 2.5,
                           "track_id": 7, "risk_level": "high", "risk_score": 70,
                           "alert_required": True}]}, b""


class ExportTests(TestCase):
    """Functionality: Exercise real file lifecycle and video/audio encoding boundaries.
    Logic: Temporary fixtures, real PyAV and injected inference. Constraints: CPU tests only.
    """

    def setUp(self):
        """Functionality: Isolate each run. Inputs: None. Outputs: Temporary source/output paths.
        Logic: Register directory cleanup and clear deployment overrides. Constraints: No repo I/O.
        """
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.output = self.root / "source.mp4", self.root / "export"
        self.detector = FakeDetector()
        environment = patch.dict("os.environ", {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def export(self, **options):
        """Functionality: Call service with a test factory. Inputs: Export options. Outputs: Report.
        Logic: Reuse fixture detector, record a test identity. Constraints: No native model load.
        """
        return export_video(self.source, self.output,
                            detector_factory=lambda **kwargs: (self.detector, {"test": True}),
                            **options)

    def test_vfr_and_audio_roundtrip(self):
        """Functionality: Verify every VFR frame, nonzero origin, meters/risk and original audio.
        Inputs: Real source with AAC. Outputs: Assertions. Logic: Compare packet hashes, source
        offsets and decoded frame PTS. Constraints: Packet equality alone is not synchronization.
        """
        make_clip(self.source, (80, 120, 200, 240), audio=True)
        original = self.source.read_bytes()
        report = self.export()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(self.detector.times, [0, .04, .12, .16])
        self.assertEqual(report["verification"]["frames"], 4)
        self.assertAlmostEqual(report["verification"]["duration_s"], .2)
        self.assertEqual(audio_fingerprints(self.source),
                         audio_fingerprints(self.output / "annotated.mp4"))
        starts = []
        for path in (self.source, self.output / "annotated.mp4"):
            with av.open(str(path)) as container:
                stream = container.streams.audio[0]
                starts.append(next(p for p in container.demux(stream) if p.size).pts
                              * stream.time_base)
        self.assertLessEqual(abs(starts[1] - starts[0] + Fraction(2, 25)), Fraction(1, 48000))
        lines = (self.output / "results.jsonl").read_text().splitlines()
        rows = [json.loads(line) for line in lines]
        self.assertEqual(rows[-1]["metadata"]["boxes"][0]["distance_m"], 2.5)
        self.assertEqual(rows[-1]["metadata"]["boxes"][0]["risk_level"], "high")
        self.assertEqual(original, self.source.read_bytes())
        self.assertFalse(list(self.output.glob("*.partial.*")))
        with av.open(str(self.output / "annotated.mp4")) as container:
            image = next(container.decode(video=0)).to_image()
            self.assertGreater(image.getpixel((10, 50))[0], 150)

    def test_no_live_session_limit(self):
        """Functionality: Process beyond the live 55-second cap. Inputs: 60-second 2fps clip.
        Outputs: All frames recorded. Logic: Assert final timestamp. Constraints: CPU fixture.
        """
        make_clip(self.source, tuple(range(0, 60000, 500)), rate=2)
        report = self.export()
        self.assertEqual(report["frames"], 120)
        self.assertEqual(self.detector.times[-1], 59.5)
        self.assertEqual(report["verification"]["duration_s"], 60)

    def test_failure_retains_partial_status(self):
        """Functionality: Avoid publishing truncated exports. Inputs: Mid-video inference failure.
        Outputs: Failed manifest and retained partials. Logic: Fail after one frame.
        Constraints: Caller must choose a fresh directory for a new attempt.
        """
        make_clip(self.source)
        buffer = io.BytesIO()
        Image.new("RGB", (128, 96)).save(buffer, "JPEG")
        first = self.detector.predict(buffer.getvalue(), 0)
        with patch.object(self.detector, "predict",
                          side_effect=[first, RuntimeError("test failure")]):
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                self.export()
        report = json.loads((self.output / "run.json").read_text())
        self.assertEqual((report["status"], report["frames"]), ("failed", 1))
        self.assertFalse((self.output / "annotated.mp4").exists())
        self.assertTrue((self.output / "results.partial.jsonl").exists())

    def test_input_and_overwrite_guards(self):
        """Functionality: Protect sources and existing results. Inputs: Missing/existing paths.
        Outputs: Explicit errors. Logic: Check before processing. Constraints: No overwrite mode.
        """
        with self.assertRaisesRegex(ValueError, "existing local"):
            self.export()
        make_clip(self.source)
        self.output.mkdir()
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.export()
        self.assertEqual(sentinel.read_text(), "keep")
        with self.assertRaisesRegex(ValueError, "max-edge"):
            export_video(self.source, self.root / "bad", max_edge=2000)

    def test_rotation_and_odd_padding(self):
        """Functionality: Apply portrait rotation before inference and pad odd encoder dimensions.
        Inputs: Rotated clip, 65px cap. Outputs: Matching model/output geometry.
        Logic: Decode actual rotation metadata. Constraints: Only right angles supported.
        """
        make_clip(self.source, rotation=90)
        report = self.export(max_edge=65)
        self.assertEqual(report["inference_size"], [49, 65])
        self.assertEqual(report["output_size"], [50, 66])

    def test_every_risk_level_is_visible(self):
        """Functionality: Display low/unknown risk as well as urgent risk and unknown distance.
        Inputs: All risk levels. Outputs: Drawn label assertions. Logic: Observe real text calls.
        Constraints: Display must preserve metadata without rescoring.
        """
        buffer = io.BytesIO()
        Image.new("RGB", (640, 480)).save(buffer, "JPEG")
        metadata, _ = self.detector.predict(buffer.getvalue(), 0)
        original = ImageDraw.ImageDraw.text
        for risk in ("low", "medium", "high", "critical", "unknown"):
            metadata["boxes"][0].update(risk_level=risk, distance_m=None)
            with patch.object(ImageDraw.ImageDraw, "text", autospec=True,
                              side_effect=original) as draw:
                annotate_frame(buffer.getvalue(), metadata)
            self.assertIn(f"N/A | {risk.upper()}", draw.call_args.args[2])

    def test_non_square_pixels(self):
        """Functionality: Preserve the displayed shape of non-square-pixel input.
        Inputs: Source SAR 3/4. Outputs: Square-pixel 96x96 export.
        Logic: Bake pixel aspect before detection. Constraints: Coordinates use normalized pixels.
        """
        make_clip(self.source, pixel_aspect=Fraction(3, 4))
        report = self.export()
        self.assertEqual(report["source"]["pixel_aspect_ratio"], "3/4")
        self.assertEqual(report["inference_size"], [96, 96])

    def test_empty_detections(self):
        """Functionality: Preserve frames without detections. Inputs: Empty prediction boxes.
        Outputs: Complete video. Logic: Inject empty metadata. Constraints: No fabricated labels.
        """
        make_clip(self.source)
        with patch.object(self.detector, "predict", return_value=(
                {"boxes": [], "tracking_model": "botsort", "risk_model": RISK_MODEL,
                 "refinement_profile": "video-stable-v6"}, b"")):
            self.assertEqual(self.export()["frames"], 4)

    def test_requires_complete_pipeline(self):
        """Functionality: Reject detection-only adapters. Inputs: Missing risk/tracking identity.
        Outputs: Failure without published video. Logic: Check contract. Constraints: Strict.
        """
        make_clip(self.source)
        with patch.object(self.detector, "predict", return_value=({"boxes": []}, b"")):
            with self.assertRaisesRegex(ValueError, "complete tracking/risk"):
                self.export()
        self.assertFalse((self.output / "annotated.mp4").exists())

    def test_missing_video_fails(self):
        """Functionality: Reject invalid media explicitly. Inputs: Non-media file.
        Outputs: Failed manifest. Logic: Decoder rejects contents. Constraints: No fallback source.
        """
        self.source.write_bytes(b"not a video")
        with self.assertRaises(av.error.InvalidDataError):
            self.export()
        self.assertEqual(json.loads((self.output / "run.json").read_text())["status"], "failed")

    def test_cli_errors_and_success(self):
        """Functionality: Report actionable command exit codes. Inputs: Controlled service outcomes.
        Outputs: Success/failure/interruption codes. Logic: Run parser and adapter.
        Constraints: CLI does not start Django or network services.
        """
        args = ["--input", str(self.source), "--output", str(self.output)]
        with patch("video_export.services.export_video") as service, patch("builtins.print"):
            self.assertEqual(main(args), 0)
            service.side_effect = ValueError("bad input")
            self.assertEqual(main(args), 1)
            service.side_effect = KeyboardInterrupt
            self.assertEqual(main(args), 130)
