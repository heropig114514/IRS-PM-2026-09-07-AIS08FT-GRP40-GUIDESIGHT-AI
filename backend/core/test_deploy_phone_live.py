"""Responsibilities: Prevent deployment from omitting the maps page or its local dependencies.
Implementation: Traverse real maps HTML asset references and static relative JS imports,
then compare the closure with the publisher's literal allowlist parsed without importing it.
Related Modules: tools.deploy_phone_live; frontend/maps.html, maps-page.js and i18n.js.
Declaration Index: DeploymentAssetTests; DeploymentAssetTests.test_maps_dependency_closure.
Variable Index: None; required/pending are test-local file-name sets, with no credentials.
"""

import ast
import re
from pathlib import Path

from django.test import SimpleTestCase


class DeploymentAssetTests(SimpleTestCase):
    """Functionality: Verify source/deployment integration for the bilingual map page.
    Logic: Inspect actual local source and the production publisher's allowlist.
    Constraints: No SSH, uploads, deployment-only imports or external-service assertions.
    """

    def test_maps_dependency_closure(self):
        """Functionality: Require every locally referenced map asset in an atomic release.
        Inputs: Real maps HTML, static relative imports and publisher FRONTEND declaration.
        Outputs: Missing dependency assertion with file names; local source reads only.
        Logic: Read the literal allowlist via AST, seed from HTML, traverse static JS once,
        reject path escapes. No publisher import, so no Paramiko credential/runtime requirement.
        Constraints: Covers static imports only; not dynamic imports or CDN availability.
        """
        backend = Path(__file__).resolve().parents[1]
        publisher = ast.parse((backend / "tools/deploy_phone_live.py").read_text(encoding="utf-8"))
        declarations = [
            ast.literal_eval(node.value) for node in publisher.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "FRONTEND"
                    for target in node.targets)
        ]
        self.assertEqual(len(declarations), 1, "Publisher must have one literal FRONTEND allowlist")
        published = set(declarations[0])
        frontend = backend.parent / "frontend"
        html = (frontend / "maps.html").read_text(encoding="utf-8")
        required = {"maps.html"}
        pending = re.findall(r'(?:src|href)="\.\./assets/([^"/]+)"', html)
        while pending:
            name = pending.pop()
            if name in required:
                continue
            path = (frontend / name).resolve()
            self.assertTrue(path.is_relative_to(frontend.resolve()), name)
            required.add(name)
            if path.suffix == ".js":
                source = path.read_text(encoding="utf-8")
                imports = re.findall(r"(?:\bfrom\s*|\bimport\s*)['\"](\./[^'\"]+)['\"]", source)
                pending.extend(
                    str(path.parent.joinpath(item).relative_to(frontend)).replace("\\", "/")
                    for item in imports
                )
        self.assertFalse(
            required - published, f"Unpublished maps assets: {required - published}"
        )
