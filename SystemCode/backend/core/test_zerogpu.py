"""Responsibilities: Verify a single HF allocation endpoint and safe live-only source staging.
Implementation: Inspect actual AST/allowlist and stage actual files without importing CUDA.
Related Modules: zerogpu.app; tools.deploy_space; real HF configuration checked after publication.
Declaration Index: ZeroGPUContractTests; ZeroGPUContractTests.test_single_api;
ZeroGPUContractTests.test_allowlist.
Variable Index: None. No HF token, model initialization or GPU quota needed.
"""

import ast
from pathlib import Path

from django.test import SimpleTestCase

from tools.deploy_space import FILES, RETIRED, stage_bundle


class ZeroGPUContractTests(SimpleTestCase):
    """Functionality: Detect accidental reintroduction of extra APIs or deployable private assets.
    Logic: inspect maintained source and real staged files; Constraints: no live network proof.
    """

    def test_single_api(self):
        """Functionality: Assert the application registers exactly one named inference API.
        Inputs: maintained app AST; Outputs: singleton name/decorator assertions.
        Logic: inspect API names and both GPU decorators; compilation is startup-only with an
        explicit1500s ceiling, while the sole live API retains its original default60s decorator.
        Constraints: runtime HF API discovery is verified independently after deployment.
        """
        tree = ast.parse((Path(__file__).parents[1] / "zerogpu/app.py").read_text(encoding="utf-8"))
        names = [
            keyword.value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg == "api_name"
        ]
        self.assertEqual(names, ["detect_live"])
        gpu_functions = {
            node.name: node.decorator_list[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and any((isinstance(d, ast.Attribute) and d.attr == "GPU")
                    or (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                        and d.func.attr == "GPU") for d in node.decorator_list)
        }
        self.assertEqual(set(gpu_functions), {"compile_startup", "gpu_live"})
        self.assertIsInstance(gpu_functions["gpu_live"], ast.Attribute)
        startup = gpu_functions["compile_startup"]
        self.assertEqual([(item.arg, item.value.value) for item in startup.keywords],
                         [("duration", 1500)])

    def test_allowlist(self):
        """Functionality: Ensure staged files exactly match live source and exclude retired assets.
        Inputs: actual source staging; Outputs: names/bytes/exposure assertions.
        Logic: compare every staged file to its maintained source and deployment destinations.
        Constraints: local writes only; HF commit and source hashes checked separately.
        """
        bundle, names = stage_bundle()
        self.assertEqual(set(names), set(FILES.values()))
        self.assertFalse(set(names).intersection(RETIRED))
        self.assertFalse(any(".env" in name or name.endswith((".pt", ".jpg")) for name in names))
        for source, destination in FILES.items():
            self.assertEqual(
                (bundle / destination).read_bytes(),
                (Path(__file__).parents[1] / source).read_bytes(),
            )
