"""Responsibilities: Verify MCP discovery, Django database effects and browser command lifecycle.
Implementation: Use the real SDK in-memory client, Django test database and ASGI queues.
Related Modules: core.ai_mcp/ai_bridge/ai_database; config.asgi; tools/check_mcp.py.
Declaration Index: DatabaseControlTests — real database contracts;
DatabaseControlTests.test_schema_and_crud — schema/CRUD;
DatabaseControlTests.test_invalid_update_rolls_back — rollback;
DatabaseControlTests.test_sql_parameters — binding/truncation;
DatabaseControlTests.test_mutation_contracts — validation;
BrowserControlTests — real ASGI queues; BrowserControlTests.open_tab — hello fixture;
BrowserControlTests.test_correlated_results — concurrent IDs;
BrowserControlTests.test_disconnect_fails_pending — disconnected command;
BrowserControlTests.test_timeout_does_not_repeat — no replay;
BrowserControlTests.test_navigation_generation — replacement registration;
BrowserControlTests.test_invalid_protocol — malformed messages;
MCPControlTests — official protocol client;
MCPControlTests.test_discovery_and_http — schemas and actual HTTP view;
MCPControlTests.test_tool_error — explicit MCP failure results.
MCPControlTests.test_vibration_tools — typed routing and structured browser refusals;
MCPControlTests.test_vibration_validation — reject invalid/missing arguments before dispatch.
MCPControlTests.test_speech_tools — typed speech routing and structured state;
MCPControlTests.test_speech_validation — bounds/whitespace validation before dispatch.
Variable Index: None; each test owns its hub or disposable Django database rows.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

from asgiref.testing import ApplicationCommunicator
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from mcp.client import Client

from . import ai_database
from .ai_bridge import BrowserHub
from .ai_mcp import mcp


class DatabaseControlTests(TestCase):
    """Functionality: Verify ORM validation, mutation counts and SQL against an actual database.
    Logic: Use Django-managed temporary SQLite tables and real auth.Group constraints.
    Constraints: No production rows, PostgreSQL validation or external network calls.
    """

    def test_schema_and_crud(self):
        """Functionality: Verify discovery and complete create/read/update/delete lifecycle.
        Inputs: Test database schema and a temporary group. Outputs: Exact values and counts.
        Logic: Read field names from actual schema; mutate using the same service as MCP tools.
        Constraints: No database mocks; Django cleans test rows after completion.
        """
        schema = ai_database.database_schema()
        self.assertIn("auth_group", [table["name"] for table in schema["tables"]])
        self.assertIn("auth.Group", [model["model"] for model in schema["models"]])
        created = ai_database.write_rows("create", "auth.Group", {"name": "mcp-test"}, None)
        filters = {"id": created["primary_key"]}
        read = ai_database.query_rows("auth.Group", filters, ["name"], [], 10, 0)
        self.assertEqual(read["rows"], [{"name": "mcp-test"}])
        self.assertEqual(
            ai_database.write_rows("update", "auth.Group", {"name": "mcp-changed"}, filters),
            {"updated": 1},
        )
        self.assertEqual(Group.objects.get(pk=created["primary_key"]).name, "mcp-changed")
        self.assertEqual(ai_database.write_rows("delete", "auth.Group", {}, filters)["deleted"], 1)

    def test_invalid_update_rolls_back(self):
        """Functionality: Verify a multi-row update rolls back earlier successful saves.
        Inputs: Two distinct groups and one conflicting replacement name.
        Outputs: ValidationError and both original names still present.
        Logic: The first save succeeds, second unique validation fails inside the same atomic block.
        Constraints: Actual uniqueness/transaction behavior; no simulated exception.
        """
        Group.objects.create(name="mcp-first")
        Group.objects.create(name="mcp-second")
        with self.assertRaises(ValidationError):
            ai_database.write_rows("update", "auth.Group", {"name": "mcp-shared"}, {})
        self.assertEqual(
            set(Group.objects.values_list("name", flat=True)), {"mcp-first", "mcp-second"}
        )

    def test_sql_parameters(self):
        """Functionality: Verify unrestricted writes, bound parameters and explicit truncation.
        Inputs: Test auth_group table, SQL with %s parameters and a one-row return cap.
        Outputs: Stored names, ordered result and truncated=True.
        Logic: Execute SQL inserts then read back through SQL and ORM.
        Constraints: Only disposable test tables are modified; cap does not constrain mutations.
        """
        for name in ["mcp-sql-a", "mcp-sql-b"]:
            ai_database.execute_sql("INSERT INTO auth_group (name) VALUES (%s)", [name], 10)
        result = ai_database.execute_sql("SELECT name FROM auth_group ORDER BY name", [], 1)
        self.assertEqual(result["rows"], [["mcp-sql-a"]])
        self.assertTrue(result["truncated"])
        self.assertEqual(Group.objects.count(), 2)

    def test_mutation_contracts(self):
        """Functionality: Reject omitted mutation filters and invalid identifiers/pagination.
        Inputs: Incomplete update, unknown model and invalid limits.
        Outputs: Explicit ValueErrors; no writes. Logic: Exercise actual validation boundaries.
        Constraints: Explicit {} filters remain supported rather than silently narrowing matches.
        """
        with self.assertRaises(ValueError):
            ai_database.write_rows("delete", "auth.Group", {}, None)
        with self.assertRaises(ValueError):
            ai_database.query_rows("missing.Model", {}, None, None, 10, 0)
        with self.assertRaises(ValueError):
            ai_database.query_rows("auth.Group", {}, None, None, 0, 0)


class BrowserControlTests(SimpleTestCase):
    """Functionality: Verify real ASGI bridge correlation, timeouts and navigation state.
    Logic: ApplicationCommunicator drives the implementation without mocking send/receive.
    Constraints: In-memory ASGI queues do not verify actual browser DOM or proxy behavior.
    """

    async def open_tab(self, hub, session_id="test-tab", url="http://localhost/live/"):
        """Functionality: Register a disposable tab transport with a valid hello handshake.
        Inputs: hub, session_id, url. Outputs: Accepted communicator after welcome.
        Logic: Send actual connect and JSON hello messages.
        Constraints: Caller must disconnect/await communicator cleanup.
        """
        socket = ApplicationCommunicator(hub.socket, {"type": "websocket"})
        await socket.send_input({"type": "websocket.connect"})
        self.assertEqual((await socket.receive_output())["type"], "websocket.accept")
        await socket.send_input(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "hello",
                        "session_id": session_id,
                        "page": {"url": url},
                    }
                ),
            }
        )
        self.assertEqual(json.loads((await socket.receive_output())["text"])["type"], "welcome")
        return socket

    async def test_correlated_results(self):
        """Functionality: Verify simultaneous commands resolve by ID rather than arrival order.
        Inputs: Two actual command futures and reversed browser result messages.
        Outputs: Each future receives its own result and registry clears after disconnect.
        Logic: Receive commands from the bridge queue; answer the second before the first.
        Constraints: Synthetic results test protocol correlation, not real DOM mutations.
        """
        hub = BrowserHub()
        socket = await self.open_tab(hub)
        first = asyncio.create_task(hub.command("test-tab", "snapshot"))
        second = asyncio.create_task(hub.command("test-tab", "click", {"selector": "#ping"}))
        commands = [json.loads((await socket.receive_output())["text"]) for _ in range(2)]
        for command in reversed(commands):
            await socket.send_input(
                {
                    "type": "websocket.receive",
                    "text": json.dumps(
                        {
                            "type": "result",
                            "id": command["id"],
                            "ok": True,
                            "result": {"action": command["action"]},
                        }
                    ),
                }
            )
        self.assertEqual(await first, {"action": "snapshot"})
        self.assertEqual(await second, {"action": "click"})
        await socket.send_input({"type": "websocket.disconnect"})
        await socket.wait()
        self.assertEqual(hub.sessions(), [])

    async def test_disconnect_fails_pending(self):
        """Functionality: Make incomplete actions fail explicitly on transport loss.
        Inputs: One sent command followed by disconnect. Outputs: ConnectionError and clean state.
        Logic: Disconnect before providing any acknowledgement.
        Constraints: Error reports unknown completion, without claiming the mutation did not run.
        """
        hub = BrowserHub()
        socket = await self.open_tab(hub)
        pending = asyncio.create_task(hub.command("test-tab", "click"))
        await socket.receive_output()
        await socket.send_input({"type": "websocket.disconnect"})
        with self.assertRaises(ConnectionError):
            await pending
        await socket.wait()
        self.assertEqual(hub.sessions(), [])

    async def test_timeout_does_not_repeat(self):
        """Functionality: Ensure timeout cleans the pending future without issuing another command.
        Inputs: Unanswered command with a short explicit test timeout.
        Outputs: TimeoutError and no second outgoing message.
        Logic: Let the actual wait_for deadline expire; inspect transport and pending state.
        Constraints: The short budget is test-only and does not alter production tool defaults.
        """
        hub = BrowserHub()
        socket = await self.open_tab(hub)
        pending = asyncio.create_task(hub.command("test-tab", "click", timeout=0.05))
        await socket.receive_output()
        with self.assertRaises(TimeoutError):
            await pending
        self.assertEqual(hub.connection("test-tab").pending, {})
        self.assertTrue(await socket.receive_nothing(timeout=0.05))
        await socket.send_input({"type": "websocket.disconnect"})
        await socket.wait()

    async def test_navigation_generation(self):
        """Functionality: Verify replacement registration survives cleanup of the previous page.
        Inputs: Two WebSockets with the same tab ID and a navigation observer.
        Outputs: New page metadata and retained current registration after old disconnect.
        Logic: Replace a connection, then run both connections' finally blocks in order.
        Constraints: Tests backend generation tracking; browser unload is covered by live checks.
        """
        hub = BrowserHub()
        old = await self.open_tab(hub)
        previous = hub.connection("test-tab")
        observer = asyncio.create_task(hub.wait_page("test-tab", previous, 1))
        new = await self.open_tab(hub, url="http://localhost/maps/")
        self.assertEqual((await old.receive_output())["type"], "websocket.close")
        self.assertEqual((await observer)["url"], "http://localhost/maps/")
        await old.send_input({"type": "websocket.disconnect"})
        await old.wait()
        self.assertEqual(len(hub.sessions()), 1)
        await new.send_input({"type": "websocket.disconnect"})
        await new.wait()

    async def test_invalid_protocol(self):
        """Functionality: Reject malformed initial control messages and unknown sessions.
        Inputs: Invalid hello JSON and nonexistent tab ID.
        Outputs: 1008 close and ValueError. Logic: Exercise actual protocol boundaries.
        Constraints: No browser, network or database access.
        """
        hub = BrowserHub()
        socket = ApplicationCommunicator(hub.socket, {"type": "websocket"})
        await socket.send_input({"type": "websocket.connect"})
        await socket.receive_output()
        await socket.send_input({"type": "websocket.receive", "text": "[]"})
        self.assertEqual((await socket.receive_output())["code"], 1008)
        await socket.wait()
        with self.assertRaises(ValueError):
            await hub.command("missing", "snapshot")


class MCPControlTests(TransactionTestCase):
    """Functionality: Verify actual MCP schemas and tools through the official client.
    Logic: In-memory protocol transport invokes real tools and the shared Django test database.
    Constraints: TransactionTestCase allows thread-owned ORM connections to observe committed rows;
    this does not claim public HTTP/TLS transport or external-service validation.
    """

    async def test_discovery_and_http(self):
        """Functionality: Verify tool discovery, live database status and existing health API.
        Inputs: Official in-memory MCP Client and current tool registry.
        Outputs: Required tool names, query success and HTTP 200/database ok.
        Logic: Run discovery and actual tool calls without mocking handlers or Django middleware.
        Constraints: Client/server run in this process; HTTP transport is checked separately.
        """
        async with Client(mcp) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            self.assertEqual(
                names,
                {
                    "system_status",
                    "system_routes",
                    "system_request",
                    "ui_sessions",
                    "ui_snapshot",
                    "ui_navigate",
                    "ui_action",
                    "vibration_status",
                    "vibration_test",
                    "vibration_stop",
                    "speech_status",
                    "speech_voices",
                    "speech_speak",
                    "speech_stop",
                    "database_schema",
                    "database_query",
                    "database_write",
                    "database_sql",
                },
            )
            result = await client.call_tool("system_status", {})
            self.assertFalse(result.is_error)
            health = await client.call_tool("system_request", {"path": "/api/health/"})
            self.assertFalse(health.is_error)
            self.assertEqual(health.structured_content["status"], 200)
            self.assertEqual(health.structured_content["body"]["status"], "ok")

    async def test_tool_error(self):
        """Functionality: Verify invalid calls appear as MCP errors instead of success-shaped data.
        Inputs: Unknown browser session and missing database mutation filters.
        Outputs: isError=True for both. Logic: Invoke actual protocol tools.
        Constraints: No mutations or simulated service failures.
        """
        async with Client(mcp) as client:
            missing = await client.call_tool("ui_snapshot", {"session_id": "missing"})
            self.assertTrue(missing.is_error)
            invalid = await client.call_tool(
                "database_write",
                {
                    "action": "delete",
                    "model": "auth.Group",
                },
            )
            self.assertTrue(invalid.is_error)

    async def test_vibration_tools(self):
        """Functionality: Verify vibration schemas and routing through the official MCP client.
        Inputs: Real SDK registry and one explicitly mocked browser transport result per command.
        Outputs: Exact session/action/arguments, one dispatch and preserved accepted/reason data.
        Logic: Status/stop use no selector; test carries only the fixed pattern. Browser refusals
        are normal explicit device outcomes rather than hardware-success claims.
        Constraints: No real tab, motor or public MCP transport; native/browser checks are separate.
        """
        cases = [
            ("vibration_status", {"session_id": "tab"}, None,
             {"available": True, "enabled": False, "hardware_verified": False}),
            ("vibration_test", {"session_id": "tab", "pattern": "double"},
             {"pattern": "double"}, {"accepted": False, "reason": "activation_required",
                                     "hardware_verified": False}),
            ("vibration_stop", {"session_id": "tab"}, None,
             {"accepted": True, "request_pending": False, "hardware_verified": False}),
        ]
        async with Client(mcp) as client:
            for tool, arguments, dispatched, response in cases:
                with self.subTest(tool=tool), \
                        patch("core.ai_mcp.hub.command", new_callable=AsyncMock) as command:
                    command.return_value = response
                    result = await client.call_tool(tool, arguments)
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.structured_content, response)
                    command.assert_awaited_once_with("tab", tool, dispatched, 10)
            with patch("core.ai_mcp.hub.command", new_callable=AsyncMock) as command:
                command.return_value = {"accepted": False, "reason": "disabled"}
                await client.call_tool("vibration_test", {"session_id": "tab"})
                command.assert_awaited_once_with("tab", "vibration_test", {"pattern": "short"}, 10)

    async def test_vibration_validation(self):
        """Functionality: Reject invalid tool arguments before issuing a browser side effect.
        Inputs: Invalid pattern or omitted required session, then an actual disconnected tab.
        Outputs: MCP isError and no mocked dispatch for invalid arguments; explicit session error.
        Logic: Exercise SDK validation and existing browser_call error conversion.
        Constraints: No native invocation, retry, browser creation or hardware verification.
        """
        async with Client(mcp) as client:
            with patch("core.ai_mcp.hub.command", new_callable=AsyncMock) as command:
                invalid = await client.call_tool(
                    "vibration_test", {"session_id": "tab", "pattern": "loop"}
                )
                missing = await client.call_tool("vibration_stop", {})
                self.assertTrue(invalid.is_error)
                self.assertTrue(missing.is_error)
                command.assert_not_awaited()
            disconnected = await client.call_tool("vibration_test", {"session_id": "missing"})
            self.assertTrue(disconnected.is_error)

    async def test_speech_tools(self):
        """Functionality: Verify speech tools route one command with exact text/configuration.
        Inputs: Real official MCP client/registry and explicit mocked browser transport only.
        Outputs: Typed discovery, precise session/action/options and preserved engine state.
        Logic: Queries/stop carry no selector; speak includes defaults or caller options once.
        Constraints: No browser, synthesis, public HTTP or actual audio validation in this test.
        """
        cases = [
            ("speech_status", {"session_id": "tab"}, None,
             {"state": "idle", "enabled": False, "audio_output_verified": False}),
            ("speech_voices", {"session_id": "tab"}, None,
             {"voices": [{"id": "explicit", "language": "en-US", "local_service": True}]}),
            ("speech_speak", {"session_id": "tab", "text": "Exact text", "language": "en-US",
                              "voice_id": "explicit", "rate": 1.5, "volume": 0.5},
             {"text": "Exact text", "language": "en-US", "voice_id": "explicit",
              "rate": 1.5, "volume": 0.5},
             {"accepted": True, "state": "queued", "audio_output_verified": False}),
            ("speech_stop", {"session_id": "tab"}, None,
             {"accepted": True, "state": "stopped", "audio_output_verified": False}),
        ]
        async with Client(mcp) as client:
            for tool, arguments, dispatched, response in cases:
                with self.subTest(tool=tool), \
                        patch("core.ai_mcp.hub.command", new_callable=AsyncMock) as command:
                    command.return_value = response
                    result = await client.call_tool(tool, arguments)
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.structured_content, response)
                    command.assert_awaited_once_with("tab", tool, dispatched, 10)
            with patch("core.ai_mcp.hub.command", new_callable=AsyncMock) as command:
                command.return_value = {"accepted": False, "reason": "disabled"}
                result = await client.call_tool(
                    "speech_speak", {"session_id": "tab", "text": "文本"}
                )
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content["reason"], "disabled")
                command.assert_awaited_once_with("tab", "speech_speak", {
                    "text": "文本", "language": "zh-CN", "voice_id": None,
                    "rate": 1.0, "volume": 1.0,
                }, 10)

    async def test_speech_validation(self):
        """Functionality: Reject invalid text/settings without browser side effects.
        Inputs: Empty/whitespace/overlength text, invalid language/ranges/bool/nonnumeric options;
        real MCP schemas and client. Outputs: isError and zero dispatch; valid Unicode boundary.
        Logic: SDK constraints and whitespace validation preserve the frontend contract.
        Constraints: No real browser/engine/speaker; passing Unicode validation is not synthesis.
        """
        cases = [{"text": ""}, {"text": "   "}, {"text": "x" * 1001},
                 {"text": "text", "language": "unknown"}, {"text": "text", "rate": 2.1},
                 {"text": "text", "volume": -0.1}, {"text": "text", "rate": True},
                 {"text": "text", "rate": "1"}, {"text": "text", "voice_id": "v" * 1025}]
        async with Client(mcp) as client:
            with patch("core.ai_mcp.hub.command", new_callable=AsyncMock) as command:
                for arguments in cases:
                    with self.subTest(arguments=list(arguments)):
                        result = await client.call_tool(
                            "speech_speak", {"session_id": "tab", **arguments}
                        )
                        self.assertTrue(result.is_error)
                command.assert_not_awaited()
                command.return_value = {"accepted": False, "reason": "disabled"}
                boundary = await client.call_tool("speech_speak", {
                    "session_id": "tab", "text": "😀" * 1000, "volume": 0,
                })
                self.assertFalse(boundary.is_error)
                self.assertEqual(command.await_count, 1)
            missing = await client.call_tool("speech_status", {"session_id": "missing"})
            self.assertTrue(missing.is_error)
