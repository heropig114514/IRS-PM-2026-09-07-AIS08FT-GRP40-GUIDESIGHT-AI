"""Responsibilities: Verify explicit full/outputs MCP registration and invalid-profile failure.
Implementation: Fresh Python processes import the real SDK registry under isolated configuration;
no browser, network, database mutation or synthetic success for missing tools.
Related Modules: core.ai_mcp; deploy/prs-camera.service; tools.deploy_phone_live.
Declaration Index: MCPProfileTests; MCPProfileTests.test_outputs_allowlist;
MCPProfileTests.test_invalid_profile.
Variable Index: REGISTRY_SCRIPT — fixed read-only child registry query; no credentials in output.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

REGISTRY_SCRIPT = (
    "import asyncio,json,django; django.setup(); "
    "from core.ai_mcp import mcp,MCP_PROFILE; "
    "print(json.dumps({'profile':MCP_PROFILE,"
    "'tools':sorted(tool.name for tool in asyncio.run(mcp.list_tools()))}))"
)


class MCPProfileTests(SimpleTestCase):
    """Functionality: Verify publication scope without modifying the caller's tool registry.
    Logic: Fresh process avoids import caching and preserves local full-profile behavior.
    Constraints: Tool discovery does not prove browser execution or public TLS/service readiness.
    """

    def test_outputs_allowlist(self):
        """Functionality: Require session discovery, seven output and three navigation tools.
        Inputs: Explicit outputs profile and fixed child registry script.
        Outputs: Eleven exact names and selected profile; no production database/API calls.
        Logic: Use real SDK registration with fixed test configuration and a bounded subprocess.
        Constraints: No profile fallback, credential output or changes to parent environment.
        """
        environment = dict(os.environ, PRS_MCP_PROFILE="outputs",
                           DJANGO_SETTINGS_MODULE="config.settings",
                           DJANGO_SECRET_KEY="mcp-profile-test-only")
        result = subprocess.run(
            [sys.executable, "-c", REGISTRY_SCRIPT], env=environment,
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            encoding="utf-8", timeout=20, check=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["profile"], "outputs")
        self.assertEqual(set(payload["tools"]), {
            "ui_sessions", "vibration_status", "vibration_test", "vibration_stop",
            "speech_status", "speech_voices", "speech_speak", "speech_stop",
            "navigation_status", "navigation_start", "navigation_stop",
        })

    def test_invalid_profile(self):
        """Functionality: Reject invalid configuration before exposing any registry.
        Inputs: Invalid non-secret profile string and isolated child configuration.
        Outputs: Failing exit and actionable parameter-name diagnostic.
        Logic: The same import path must fail, rather than choosing full or outputs implicitly.
        Constraints: No service startup, remote requests or database effects.
        """
        environment = dict(os.environ, PRS_MCP_PROFILE="invalid-profile",
                           DJANGO_SETTINGS_MODULE="config.settings",
                           DJANGO_SECRET_KEY="mcp-profile-test-only")
        result = subprocess.run(
            [sys.executable, "-c", REGISTRY_SCRIPT], env=environment,
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            encoding="utf-8", timeout=20, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PRS_MCP_PROFILE must be full or outputs", result.stderr)
        self.assertFalse(result.stdout.strip())
