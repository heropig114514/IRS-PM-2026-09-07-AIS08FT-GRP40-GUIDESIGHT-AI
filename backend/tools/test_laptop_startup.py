"""Responsibilities: Verify portable configuration, GPU selection/cache and combined cleanup.
Implementation: Real temporary files/configuration, mocked GPU/SSH/Uvicorn and no external requests.
Related Modules: tools.laptop_config/laptop_platform/laptop_tunnel/run_laptop; core.laptop_services.
Declaration Index: StartupTests; StartupTests.config; StartupTests.test_configuration;
StartupTests.test_gpu_selection; StartupTests.test_inventory; StartupTests.test_cache_identity;
StartupTests.test_model_integrity; StartupTests.test_partial_cache; StartupTests.test_tunnel_bind;
StartupTests.test_disconnect; StartupTests.test_cleanup; StartupTests.test_cloud_contract;
StartupTests.test_relay_ports; StartupTests.test_runtime_library.
Variable Index: GPU — synthetic NVIDIA inventory; TOKEN — synthetic shared credential.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from django.test import SimpleTestCase

from core import laptop_services
from core.detector import DetectionError
from tools import laptop_platform, run_laptop
from tools.laptop_config import LaptopConfig
from tools.laptop_tunnel import ReverseTunnel

GPU = {
    "index": 1,
    "name": "Test NVIDIA",
    "uuid": "GPU-test-only",
    "sm": "8.9",
    "driver": "566.36",
    "free_mib": 6000,
    "cuda_capability": (12, 7),
}
TOKEN = "test-only-shared-token-of-32-characters"


class StartupTests(SimpleTestCase):
    """Functionality: Validate startup orchestration and machine configuration boundaries.
    Logic: replace external GPU/SSH/runtime interfaces, exercise actual control flow.
    Constraints: passing mocks does not establish Linux/Blackwell hardware or SSH compatibility.
    """

    def config(self, **values):
        """Functionality: Create explicit test configuration. Inputs: self, field overrides.
        Outputs: LaptopConfig; Logic: synthetic credentials/paths with existing defaults.
        Constraints: no real key, token or cloud endpoint used.
        """
        return LaptopConfig(
            **{
                "host": "cloud.test",
                "user": "test",
                "ssh_port": 22,
                "local_port": 18767,
                "remote_port": 18767,
                "known_hosts": Path("unused"),
                "key_path": None,
                "onnx_path": Path("unused"),
                "server_onnx_path": "",
                "public_url": "",
                "gpu_index": None,
                "token": TOKEN,
                **values,
            }
        )

    def test_configuration(self):
        """Functionality: Check portable config validation. Inputs: temporary trust file/test env.
        Outputs: defaults/overrides and rejection assertions; Logic: real env parser and paths.
        Constraints: no host trust or actual authentication verified.
        """
        with tempfile.TemporaryDirectory() as directory:
            trust = Path(directory) / "known_hosts"
            trust.touch()
            environment = {
                "LAPTOP_SSH_HOST": "cloud.test",
                "LAPTOP_SSH_USER": "test",
                "LAPTOP_INFERENCE_TOKEN": TOKEN,
                "LAPTOP_SSH_KNOWN_HOSTS": str(trust),
                "LAPTOP_PUBLIC_URL": "https://cloud.test/prs",
            }
            with patch.dict(os.environ, environment, clear=True):
                config = LaptopConfig.load()
                self.assertEqual(config.local_port, 18767)
                self.assertIsNone(config.gpu_index)
                self.assertEqual(config.public_url, "https://cloud.test/prs/")
                self.assertTrue(config.onnx_path.is_absolute())
                self.assertNotIn(TOKEN, repr(config))
                for name, value in (
                    ("LAPTOP_WORKER_PORT", "80"),
                    ("LAPTOP_REMOTE_PORT", "65536"),
                    ("LAPTOP_SSH_PORT", "0"),
                    ("LAPTOP_GPU_INDEX", "-1"),
                    ("LAPTOP_PUBLIC_URL", "https://secret@cloud.test/prs/"),
                    ("LAPTOP_SERVER_ONNX_PATH", "relative/model.onnx"),
                    ("LAPTOP_SSH_HOST", ""),
                ):
                    with self.subTest(name=name), patch.dict(os.environ, {name: value}):
                        with self.assertRaises(ValueError):
                            LaptopConfig.load()
                with patch.dict(os.environ, LAPTOP_WORKER_PORT="23456", LAPTOP_GPU_INDEX="1"):
                    self.assertEqual(LaptopConfig.load().local_port, 23456)
                    self.assertEqual(LaptopConfig.load().gpu_index, 1)

    def test_gpu_selection(self):
        """Functionality: Check automatic/explicit GPU choice and pinned profiles.
        Inputs: mock inventory with unsupported/compatible GPUs and old driver/Blackwell.
        Outputs: selection/rejection assertions; Logic: real profile policy.
        Constraints: validates policy only, no CUDA/TensorRT package or hardware execution.
        """
        unsupported = {**GPU, "index": 0, "sm": "6.1"}
        with patch.object(laptop_platform, "discover_gpus", return_value=[GPU, unsupported]):
            self.assertEqual(laptop_platform.select_gpu()["index"], 1)
            with self.assertRaises(RuntimeError):
                laptop_platform.select_gpu(0)
            with self.assertRaises(RuntimeError):
                laptop_platform.select_gpu(9)
        with self.assertRaises(RuntimeError):
            laptop_platform.runtime_profile({**GPU, "cuda_capability": (12, 5)})
        profile = laptop_platform.runtime_profile({**GPU, "sm": "12.0", "cuda_capability": (12, 8)})
        self.assertEqual(profile["trt"], "10.8.0.43")

    def test_inventory(self):
        """Functionality: Check discovery parsing on supported OS. Inputs: mocked nvidia-smi CSV.
        Outputs: physical index/capability; Logic: real parser with bounded subprocess calls.
        Constraints: both OS branches are simulated, including localized banner replacement.
        """
        for system, machine in (("Windows", "AMD64"), ("Linux", "x86_64")):
            with (
                self.subTest(system=system),
                patch.object(laptop_platform.platform, "system", return_value=system),
                patch.object(laptop_platform.platform, "machine", return_value=machine),
                patch.object(
                    laptop_platform.subprocess,
                    "run",
                    side_effect=[
                        SimpleNamespace(stdout="1, Test NVIDIA, GPU-test-only, 8.9, 566.36, 6000"),
                        SimpleNamespace(stdout="CUDA Version: 12.7"),
                    ],
                ) as command,
            ):
                self.assertEqual(laptop_platform.discover_gpus(), [GPU])
                self.assertEqual(command.call_args.kwargs["errors"], "replace")

    def test_cache_identity(self):
        """Functionality: Prevent cross-machine/version cache reuse. Inputs: synthetic identities.
        Outputs: stable and distinct keys; Logic: vary GPU/driver/OS/TRT/source independently.
        Constraints: hash stability is not proof of engine numerical accuracy.
        """
        identity, key = laptop_platform.engine_identity(GPU, "10.7.0.post1", "source")
        self.assertEqual(key, laptop_platform.engine_identity(GPU, "10.7.0.post1", "source")[1])
        self.assertEqual(identity["precision"], "FP32")
        self.assertFalse(identity["tf32"])
        for field, value in (("uuid", "GPU-other"), ("driver", "other"), ("sm", "8.6")):
            self.assertNotEqual(
                key,
                laptop_platform.engine_identity({**GPU, field: value}, "10.7.0.post1", "source")[1],
            )
        with patch.object(laptop_platform.platform, "system", return_value="Different"):
            self.assertNotEqual(
                key, laptop_platform.engine_identity(GPU, "10.7.0.post1", "source")[1]
            )
        self.assertNotEqual(key, laptop_platform.engine_identity(GPU, "other", "source")[1])

    def test_model_integrity(self):
        """Functionality: Verify bounded SFTP download/hash and rejection of existing wrong models.
        Inputs: temp model and mocked explicit SFTP source; Outputs: verified bytes or ValueError.
        Logic: only missing artifacts are fetched; Constraints: no actual SSH or weights involved.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.onnx"
            config = self.config(onnx_path=path, server_onnx_path="/models/pinned.onnx")
            tunnel = MagicMock()
            source = tunnel.client.open_sftp.return_value.__enter__.return_value.open
            source.return_value.__enter__.return_value.read.return_value = b"test-artifact"
            digest = hashlib.sha256(b"test-artifact").hexdigest()
            run_laptop.ensure_model(config, tunnel, digest)
            self.assertEqual(path.read_bytes(), b"test-artifact")
            path.write_bytes(b"wrong")
            with self.assertRaises(ValueError):
                run_laptop.ensure_model(config, tunnel, digest)
            self.assertEqual(tunnel.client.open_sftp.call_count, 1)

    def test_partial_cache(self):
        """Functionality: Reject half-written cache without build/retry.
        Inputs: temp manifest/engine, fake TRT version, mocked already verified ONNX.
        Outputs: error and no subprocess; Logic: real cache state check.
        Constraints: no real GPU import or model compilation.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "models").mkdir()
            (root / "models/yolo26n.json").write_text('{"onnx_sha256":"source"}')
            _, key = laptop_platform.engine_identity(GPU, "10.7.0.post1", "source")
            engine = root / "models/tensorrt" / f"yolo26n-fp32-{key}.engine"
            engine.parent.mkdir()
            engine.touch()
            with (
                patch.dict("sys.modules", tensorrt=SimpleNamespace(__version__="10.7.0.post1")),
                patch.object(run_laptop.importlib.metadata, "version", return_value="12.6.77"),
                patch.object(run_laptop, "BASE_DIR", root),
                patch.object(run_laptop, "ensure_model"),
                patch.object(run_laptop.subprocess, "run") as build,
            ):
                with self.assertRaisesRegex(RuntimeError, "Partial"):
                    run_laptop.prepare_engine(self.config(), GPU, Mock())
                build.assert_not_called()

    def test_tunnel_bind(self):
        """Functionality: Require exact loopback bind. Inputs: mocked SSH transport/ss rows.
        Outputs: accepted private listener/rejected wildcard; Logic: real bind validation.
        Constraints: no SSH server, permissions or firewall verification.
        """
        with patch("tools.laptop_tunnel.paramiko.SSHClient"):
            tunnel = ReverseTunnel(self.config())
        tunnel.transport = Mock()
        for address, accepted in (("127.0.0.1", True), ("0.0.0.0", False), ("[::]", False)):
            with patch.object(
                tunnel, "listener", return_value=[f"LISTEN 0 128 {address}:18767 0.0.0.0:*"]
            ):
                if accepted:
                    tunnel.activate()
                else:
                    with self.assertRaises(RuntimeError):
                        tunnel.activate()

    def test_disconnect(self):
        """Functionality: Record fatal SSH disconnect without reconnect.
        Inputs: mock inactive transport and unset stop event; Outputs: error/log, no connect.
        Logic: real serving loop; Constraints: no active channels or actual network.
        """
        with patch("tools.laptop_tunnel.paramiko.SSHClient"):
            tunnel = ReverseTunnel(self.config())
        tunnel.transport = Mock()
        tunnel.transport.is_active.return_value = False
        stop = Mock()
        stop.is_set.return_value = False
        with self.assertLogs("tools.laptop_tunnel", "ERROR"):
            tunnel.serve(stop)
        self.assertIsInstance(tunnel.error, RuntimeError)
        tunnel.client.connect.assert_not_called()

    def test_cleanup(self):
        """Functionality: Close worker/listener/tunnel on fatal exit and user interruption.
        Inputs: mocked GPU/Django/Uvicorn/threads/socket and failure at specified startup stage.
        Outputs: unconditional owned cleanup; Logic: actual run finally, no external services.
        Constraints: mock lifecycle assertions do not prove OS process/port release.
        """
        for failure in (RuntimeError("cloud unavailable"), KeyboardInterrupt()):
            with (
                self.subTest(failure=type(failure).__name__),
                patch.object(run_laptop, "ReverseTunnel") as tunnel_factory,
                patch.object(run_laptop.socket, "socket") as socket_factory,
                patch.object(run_laptop, "prepare_engine", return_value=Path("test.engine")),
                patch("django.setup"),
                patch("core.gpu_detector.get_gpu_detector"),
                patch("uvicorn.Server") as server_factory,
                patch.object(run_laptop, "Thread") as thread_factory,
                patch.object(run_laptop, "check_cloud", side_effect=failure),
                patch.dict(os.environ, clear=False),
            ):
                server = server_factory.return_value
                server.started = True
                with self.assertRaises(type(failure)):
                    run_laptop.run(self.config(), GPU)
                self.assertTrue(server.should_exit)
                tunnel_factory.return_value.close.assert_called_once()
                socket_factory.return_value.close.assert_called_once()
                self.assertEqual(thread_factory.call_count, 2)
                self.assertEqual(thread_factory.return_value.join.call_count, 2)
                tunnel_factory.return_value.activate.assert_called_once()
        with (
            patch.object(run_laptop, "ReverseTunnel") as factory,
            patch.object(run_laptop.socket, "socket") as sockets,
        ):
            factory.return_value.connect.side_effect = RuntimeError("occupied")
            with self.assertRaises(RuntimeError):
                run_laptop.run(self.config(), GPU)
            factory.return_value.close.assert_called_once()
            sockets.return_value.close.assert_called_once()

    def test_cloud_contract(self):
        """Functionality: Reject redirects/nonready cloud results. Inputs: fake HTTPS responses.
        Outputs: verified ready or error, closed connection; Logic: one bounded GET.
        Constraints: no actual TLS or cloud connectivity verified.
        """
        for status, payload, accepted in (
            (200, {"ready": True, "backend": "TensorRT", "precision": "FP32"}, True),
            (302, {}, False),
            (200, {"ready": False}, False),
        ):
            with patch.object(run_laptop.http.client, "HTTPSConnection") as connection:
                response = connection.return_value.getresponse.return_value
                response.status = status
                response.read.return_value = json.dumps(payload).encode()
                if accepted:
                    run_laptop.check_cloud(self.config(public_url="https://cloud.test/prs/"))
                else:
                    with self.assertRaises((ValueError, RuntimeError)):
                        run_laptop.check_cloud(self.config(public_url="https://cloud.test/prs/"))
                connection.return_value.request.assert_called_once_with(
                    "GET", "/prs/vision/laptop/config/"
                )
                connection.return_value.close.assert_called_once()

    def test_relay_ports(self):
        """Functionality: Check configured remote loopback port and invalid-port rejection.
        Inputs: mock failed HTTP connection and explicit env ports; Outputs: destination/error.
        Logic: real relay configuration parsing; Constraints: no actual server-to-worker request.
        """
        with (
            patch.dict(os.environ, LAPTOP_INFERENCE_TOKEN=TOKEN, LAPTOP_INFERENCE_PORT="23456"),
            patch.object(laptop_services.http.client, "HTTPConnection") as connection,
        ):
            connection.return_value.request.side_effect = OSError("test-only")
            with self.assertRaises(DetectionError):
                laptop_services.laptop_request()
            connection.assert_called_once_with("127.0.0.1", 23456, timeout=8)
        for value in ("80", "65536", "not-a-port"):
            with (
                patch.dict(os.environ, LAPTOP_INFERENCE_TOKEN=TOKEN, LAPTOP_INFERENCE_PORT=value),
                patch.object(laptop_services.http.client, "HTTPConnection") as connection,
                self.assertLogs("core.laptop_services", "ERROR"),
            ):
                with self.assertRaises(DetectionError):
                    laptop_services.laptop_request()
                connection.assert_not_called()

    def test_runtime_library(self):
        """Functionality: Check CUDA library discovery without fixed installation paths.
        Inputs: temporary package directories and simulated Windows/Linux platform names.
        Outputs: absolute DLL/SO path or missing-library error; Logic: real existence validation.
        Constraints: no native library is loaded; this is not a Linux CUDA execution test.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for system, filename in (
                ("Windows", "bin/cudart64_12.dll"),
                ("Linux", "lib/libcudart.so.12"),
            ):
                library = root / filename
                library.parent.mkdir(exist_ok=True)
                library.touch()
                with (
                    patch.object(laptop_platform.platform, "system", return_value=system),
                    patch.object(
                        laptop_platform.importlib.util,
                        "find_spec",
                        return_value=SimpleNamespace(submodule_search_locations=[directory]),
                    ),
                ):
                    self.assertEqual(laptop_platform.cuda_library(), library)
                    library.unlink()
                    with self.assertRaises(RuntimeError):
                        laptop_platform.cuda_library()
