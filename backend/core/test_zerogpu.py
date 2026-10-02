"""Responsibilities: Verify ZeroGPU media boundaries and safe deployment allowlist locally.
Implementation: Real Pillow/JSON/base64 with no model, GPU, HF network or authentication mocks.
Related Modules: zerogpu.protocol; tools.deploy_space; core.detector.
Declaration Index: ZeroGPUContractTests; ZeroGPUContractTests.test_round_trip;
ZeroGPUContractTests.test_invalid_inputs; ZeroGPUContractTests.test_invalid_timing;
ZeroGPUContractTests.test_allowlist.
Variable Index: None.
"""

import base64
from pathlib import Path

from django.test import SimpleTestCase

from tools.deploy_space import FILES
from zerogpu.protocol import MAX_BASE64_LENGTH, decode_input, encode_result

from .detector import DetectionError


class ZeroGPUContractTests(SimpleTestCase):
    """Functionality: Check JSON/media contracts and source exposure boundaries.
    Logic: existing public sample exercises real codec; malformed metadata exercises failure paths.
    Constraints: these tests do not prove CUDA availability, actual detections or HF deployment.
    """

    def test_round_trip(self):
        """Functionality: Verify memory-only serialization of a valid no-detection result.
        Inputs: public JPEG fixture, no mocks; Outputs: assertions on decoded size/metadata/JPEG.
        Logic: encode input, decode, render/serialize, decode output using the same contract.
        Constraints: empty boxes are intentional; no model inference or accuracy claim.
        """
        raw = (Path(__file__).parents[2] / "frontend/detection-sample.jpg").read_bytes()
        payload = {"schema_version": 1, "jpeg_base64": base64.b64encode(raw).decode("ascii")}
        image = decode_input(payload)
        result = encode_result(image, [], 12.3)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["backend"], "PyTorch ZeroGPU")
        self.assertEqual(result["precision"], "FP32")
        self.assertEqual(result["confidence_threshold"], 0.25)
        self.assertEqual(decode_input({
            "schema_version": 1, "jpeg_base64": result["jpeg_base64"],
        }).size, image.size)

    def test_invalid_inputs(self):
        """Functionality: Reject wrong schema, extra fields, bad base64 and excess text early.
        Inputs: malformed request cases; Outputs: expected DetectionError per case.
        Logic: each subtest calls the real validator; Constraints: no external services or mocks.
        """
        cases = [None, {}, {"schema_version": True, "jpeg_base64": "a"},
                 {"schema_version": 1, "jpeg_base64": "!!!"},
                 {"schema_version": 1, "jpeg_base64": "é"},
                 {"schema_version": 1, "jpeg_base64": "a", "url": "https://example.org"},
                 {"schema_version": 1, "jpeg_base64": "a" * (MAX_BASE64_LENGTH + 1)}]
        for payload in cases:
            with self.subTest(payload_type=type(payload).__name__):
                with self.assertRaises(DetectionError):
                    decode_input(payload)

    def test_invalid_timing(self):
        """Functionality: Reject nonfinite/negative timing before constructing a success.
        Inputs: invalid values, unused image None; Outputs: ValueError.
        Logic: timing check precedes image access; Constraints: no GPU or image renderer mocked.
        """
        for value in (float("nan"), float("inf"), -1):
            with self.assertRaises(ValueError):
                encode_result(None, [], value)

    def test_allowlist(self):
        """Functionality: Verify deployment excludes web configuration and credential files.
        Inputs: uploader FILES constant; Outputs: exact expected destination set assertion.
        Logic: forbid unnoticed scope growth; Constraints: not proof of an actual remote upload.
        """
        self.assertEqual(set(FILES.values()), {
            "app.py", "README.md", "requirements.txt", "packages.txt",
            "zerogpu/__init__.py", "zerogpu/app.py",
            "zerogpu/protocol.py", "zerogpu/runtime.py", "core/__init__.py", "core/detector.py",
            "models/yolo26n.json",
        })
