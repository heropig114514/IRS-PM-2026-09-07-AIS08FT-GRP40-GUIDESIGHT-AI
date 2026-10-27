"""Responsibilities: Test the describe_image CLI: arguments, discovery, resizing, failures, output.
Implementation: Real Pillow on tiny synthetic images in a temp dir; http.client and time.sleep are
mocked, load_environment is patched so SystemCode/clips/backend/.env (real key) is never read;
no network.
Related Modules: tools.describe_image, core.vlm, core.test_scene (fake_connection helpers).
Declaration Index: write_image — synthetic image; DescribeImageCliTests;
DescribeImageCliTests.setUp/invoke/test_argument_validation/test_directory_order_and_filters/
test_resize_contract/test_failures_continue_and_exit_code/test_output_files/
test_interval_and_not_configured/test_persistent_error_stops_run.
Variable Index: KEY — fake key; bodies — captured upstream request bodies per test.
"""

import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from PIL import Image

from core import vlm
from core.test_scene import fake_connection
from tools import describe_image

KEY = "fake-dashscope-key-0123"


def write_image(path, size=(1280, 960), mode="RGB", fmt=None):
    """Functionality: Write a synthetic image. Inputs: path, size, mode, format.
    Outputs: none. Constraints: noise-free gradient; only Pillow, no downloads.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, (200, 100, 50, 128)[: len(mode)] if mode != "L" else 90).save(path, fmt)


@patch.dict("os.environ", {"DASHSCOPE_API_KEY": KEY})
class DescribeImageCliTests(SimpleTestCase):
    """Functionality: CLI behaviour with mocked upstream; Constraints: no real sleeping/network."""

    def setUp(self):
        """Functionality: Temp dir + patched env loader/sleep; Outputs: self.tmp, self.sleep."""
        vlm.request_times.clear()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name).resolve()
        patcher = patch("tools.describe_image.load_environment")
        patcher.start()
        self.addCleanup(patcher.stop)
        sleeper = patch("tools.describe_image.time.sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)

    def invoke(self, *argv, connection=None):
        """Functionality: Run main with mocked HTTPS. Inputs: CLI args, optional constructor mock.
        Outputs: (exit code, stdout, constructor). Logic: default constructor returns 200 text.
        """
        constructor = connection or fake_connection()[0]
        out = io.StringIO()
        with patch("core.vlm.http.client.HTTPSConnection", constructor), redirect_stdout(out):
            code = describe_image.main([str(a) for a in argv])
        return code, out.getvalue(), constructor

    def bodies(self, constructor):
        """Functionality: Decode every upstream request body sent through the mock."""
        return [json.loads(call.kwargs["body"])
                for call in constructor.return_value.request.call_args_list]

    def test_argument_validation(self):
        """Functionality: Bad arguments stop before any request. Inputs: missing source, empty dir,
        negative interval, blank/long question, bad language, existing output.
        Outputs: SystemExit(2); Constraints: no upstream call, no output dir created.
        """
        image = self.tmp / "a.png"
        write_image(image)
        empty = self.tmp / "empty"
        empty.mkdir()
        existing = self.tmp / "exists"
        existing.mkdir()
        cases = [
            [self.tmp / "missing.png"], [empty], [image, "--interval", "-1"],
            [image, "--interval", "nan"], [image, "--question", "   "],
            [image, "--question", "问" * 201], [image, "--language", "fr-FR"],
            [image, "--output", existing], [self.tmp / "notes.txt"],
        ]
        (self.tmp / "notes.txt").write_text("x")
        constructor = fake_connection()[0]
        for argv in cases:
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as caught:
                self.invoke(*argv, connection=constructor)
            self.assertEqual(caught.exception.code, 2)
        constructor.assert_not_called()

    def test_directory_order_and_filters(self):
        """Functionality: Recursive, sorted, extension-filtered discovery without sampling.
        Inputs: nested dirs, mixed case suffixes, a txt file. Outputs: ordered names.
        """
        for name in ["b.jpg", "a/z.PNG", "a/y.webp", "c.bmp", "a.jpeg"]:
            write_image(self.tmp / "in" / name, (32, 32))
        (self.tmp / "in" / "skip.txt").write_text("x")
        found = [name for name, _ in describe_image.collect_images(self.tmp / "in")]
        self.assertEqual(found, ["a.jpeg", "a/y.webp", "a/z.PNG", "b.jpg", "c.bmp"])
        code, out, constructor = self.invoke(self.tmp / "in", "--interval", 0)
        self.assertEqual(code, 0)
        self.assertEqual(constructor.return_value.request.call_count, 5)
        self.assertEqual([line.split()[1] for line in out.splitlines()[:5]], found)
        self.assertIn("Done: 5 succeeded, 0 failed", out)

    def test_resize_contract(self):
        """Functionality: Uploaded image is a JPEG, long edge <= 640, <= 1 MiB, RGB, in memory.
        Inputs: large RGBA PNG, small gray BMP. Outputs: decoded upstream image properties.
        Constraints: small images are not enlarged; the source file is unchanged.
        """
        big, small = self.tmp / "big.png", self.tmp / "small.bmp"
        write_image(big, (2000, 1000), "RGBA")
        write_image(small, (100, 50), "L", "BMP")
        before = big.read_bytes()
        for path, expected in [(big, (640, 320)), (small, (100, 50))]:
            data = describe_image.prepare_jpeg(path)
            self.assertLessEqual(len(data), vlm.MAX_IMAGE_BYTES)
            image = Image.open(io.BytesIO(data))
            self.assertEqual((image.format, image.mode, image.size), ("JPEG", "RGB", expected))
        self.assertEqual(big.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["big.png", "small.bmp"])
        code, _, constructor = self.invoke(big, "--interval", 0)
        url = self.bodies(constructor)[0]["messages"][1]["content"][0]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/jpeg;base64,"))

    def test_failures_continue_and_exit_code(self):
        """Functionality: A failing file is recorded and the batch goes on; exit code 1.
        Inputs: ok image, corrupt image, ok image with upstream 500 on the second request.
        Outputs: 3 lines, exit 1, exactly one upstream call per decodable image (no retry).
        """
        write_image(self.tmp / "in" / "a.png", (32, 32))
        (self.tmp / "in" / "b.png").write_bytes(b"not an image")
        write_image(self.tmp / "in" / "c.png", (32, 32))
        constructor, connection = fake_connection()
        statuses = iter([200, 500])
        response = connection.getresponse.return_value

        def next_status(*_):
            response.status = next(statuses)
            return response

        connection.getresponse.side_effect = next_status
        code, out, _ = self.invoke(self.tmp / "in", "--interval", 0, connection=constructor)
        self.assertEqual(code, 1)
        self.assertEqual(connection.request.call_count, 2)
        lines = out.splitlines()
        self.assertIn("a.png", lines[0])
        self.assertIn("FAILED image_unreadable", lines[1])
        self.assertIn("FAILED vlm_upstream_error", lines[2])
        self.assertIn("Done: 1 succeeded, 2 failed", out)
        self.assertNotIn(KEY, out)

    def test_output_files(self):
        """Functionality: run.json and results.jsonl content. Inputs: one ok + one corrupt image.
        Outputs: counts, model, timestamps, per-image rows; Constraints: no key anywhere.
        """
        write_image(self.tmp / "in" / "a.png", (32, 32))
        (self.tmp / "in" / "b.png").write_bytes(b"bad")
        output = self.tmp / "out"
        code, _, _ = self.invoke(
            self.tmp / "in", "--question", "前面有什么？", "--language", "zh-CN",
            "--output", output, "--interval", 0,
        )
        self.assertEqual(code, 1)
        run_info = json.loads((output / "run.json").read_text(encoding="utf-8"))
        self.assertEqual((run_info["images"], run_info["succeeded"], run_info["failed"]), (2, 1, 1))
        self.assertEqual((run_info["model"], run_info["complete"]), (vlm.DEFAULT_MODEL, True))
        self.assertEqual(run_info["settings"]["question"], "前面有什么？")
        self.assertEqual(run_info["settings"]["interval_seconds"], 0)
        self.assertLessEqual(run_info["started_at"], run_info["finished_at"])
        rows = [json.loads(line) for line in
                (output / "results.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["file"] for row in rows], ["a.png", "b.png"])
        self.assertEqual(rows[0]["text"], "前方有一辆车。")
        self.assertEqual(rows[0]["model"], vlm.DEFAULT_MODEL)
        self.assertEqual(rows[0]["usage"], {"input_tokens": 321, "output_tokens": 18})
        self.assertRegex(rows[0]["request_id"], r"^[0-9a-f]{12}$")
        self.assertEqual(rows[1]["error"], "image_unreadable")
        self.assertNotIn("text", rows[1])
        self.assertTrue(all(isinstance(row["elapsed_ms"], int) for row in rows))
        for path in output.iterdir():
            self.assertNotIn(KEY, path.read_text(encoding="utf-8"))
        # A second run must reject an output directory that already exists.
        with self.assertRaises(SystemExit):
            self.invoke(self.tmp / "in", "--output", output)

    def test_interval_and_not_configured(self):
        """Functionality: Default 6.5 s gaps between (not before/after) requests; missing key fails
        fast with exit 1, no request and no output dir. Constraints: sleep is mocked.
        """
        for name in "abc":
            write_image(self.tmp / "in" / f"{name}.png", (32, 32))
        code, _, _ = self.invoke(self.tmp / "in")
        self.assertEqual(code, 0)
        self.assertEqual([call.args for call in self.sleep.call_args_list], [(6.5,), (6.5,)])
        self.sleep.reset_mock()
        self.invoke(self.tmp / "in", "--interval", 0)
        self.sleep.assert_not_called()
        constructor = MagicMock()
        output = self.tmp / "unconfigured"
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": ""}):
            code, out, _ = self.invoke(
                self.tmp / "in", "--output", output, connection=constructor
            )
        self.assertEqual(code, 1)
        self.assertIn("vlm_not_configured", out)
        constructor.assert_not_called()
        self.assertFalse(output.exists())

    def test_persistent_error_stops_run(self):
        """Functionality: Config/auth/quota errors end the batch at once. Inputs: 3 images, upstream
        401 / 429 on the first request vs 500. Outputs: one request, no sleep, one results row,
        run.json stopped_reason/skipped, exit 1; a 500 still continues. Constraints: no retry.
        """
        for name in "abc":
            write_image(self.tmp / "in" / f"{name}.png", (32, 32))
        for status, code in [(401, "vlm_authentication_failed"), (429, "vlm_quota_exceeded")]:
            vlm.request_times.clear()
            self.sleep.reset_mock()
            constructor, connection = fake_connection(status=status)
            output = self.tmp / f"out{status}"
            exit_code, out, _ = self.invoke(
                self.tmp / "in", "--output", output, connection=constructor
            )
            self.assertEqual(exit_code, 1)
            self.assertEqual(connection.request.call_count, 1)
            self.sleep.assert_not_called()
            self.assertIn(f"Stopped: {code}", out)
            self.assertIn("2 remaining", out)
            rows = (output / "results.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual([json.loads(r)["error"] for r in rows], [code])
            info = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual((info["stopped_reason"], info["skipped"], info["complete"]),
                             (code, 2, False))
        vlm.request_times.clear()
        constructor, connection = fake_connection(status=500)
        exit_code, _, _ = self.invoke(self.tmp / "in", "--interval", 0, connection=constructor)
        self.assertEqual((exit_code, connection.request.call_count), (1, 3))
