"""Responsibilities: Verify PRS MCP over real Streamable HTTP, optionally exercising UI/CRUD.
Implementation: Official client calls live tools; explicit flags enable reversible test mutations.
Related Modules: core.ai_mcp; SystemCode/frontend/tools/check_mcp_browser.mjs; MCP.md.
Declaration Index: call — protocol result assertion; check_database — disposable real CRUD;
check_browser — MCP UI workflow; run — client orchestration; main — CLI/evidence persistence.
Variable Index: None; report/args are local evidence/configuration, not module state.
"""

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from mcp.client import Client


async def call(client, name, **arguments):
    """Functionality: Call a real tool and assert protocol-level success.
    Inputs: client, tool name and arguments. Outputs: Structured content or AssertionError.
    Logic: Fail immediately on is_error; never automatically repeat failed operations.
    Constraints: Returned error text is printed only for explicitly requested verification.
    """
    result = await client.call_tool(name, arguments)
    if result.is_error:
        raise AssertionError(f"{name} failed: {result.content}")
    return result.structured_content


async def check_database(client):
    """Functionality: Verify complete ORM CRUD and bound SQL against a disposable group.
    Inputs: Live client. Outputs: Non-sensitive completion evidence.
    Logic: Create one uniquely named group; verify update through SQL; delete in finally.
    Constraints: Requires --exercise-db; writes the connected database, never existing groups.
    Cleanup is an explicit deletion of the test marker, not a retry of a failed mutation.
    """
    name = "mcp-check-" + uuid.uuid4().hex
    changed = name + "-updated"
    filters = {"name__in": [name, changed]}
    try:
        created = await call(
            client, "database_write", action="create", model="auth.Group", values={"name": name}
        )
        key = created["primary_key"]
        read = await call(
            client, "database_query", model="auth.Group", filters={"id": key}, fields=["name"]
        )
        assert read["rows"] == [{"name": name}]
        updated = await call(
            client,
            "database_write",
            action="update",
            model="auth.Group",
            filters={"id": key},
            values={"name": changed},
        )
        assert updated["updated"] == 1
        sql = await call(
            client,
            "database_sql",
            sql="SELECT name FROM auth_group WHERE id = %s",
            parameters=[key],
        )
        assert sql["rows"] == [[changed]]
    finally:
        deleted = await call(
            client, "database_write", action="delete", model="auth.Group", filters=filters
        )
    assert deleted["deleted"] == 1
    return {
        "create": True,
        "query": True,
        "update": True,
        "sql_parameters": True,
        "delete": True,
        "test_rows_remaining": 0,
    }


async def check_browser(client, session_id):
    """Functionality: Exercise camera buttons, page navigation and map inputs via MCP alone.
    Inputs: Client and dedicated camera-tab session ID. Outputs: Observed UI completion evidence.
    Logic: Select English/Echo, ping, start/stop camera, navigate to maps, fill and submit.
    Constraints: Intended for check_mcp_browser's fixture browser; route/GPS/media are synthetic.
    Changes tab language/page state; does not allocate a GPU or claim physical sensor validation.
    """
    await call(client, "ui_snapshot", session_id=session_id)
    await call(
        client,
        "ui_action",
        session_id=session_id,
        action="select",
        selector="#language",
        value="en",
    )
    await call(
        client,
        "ui_action",
        session_id=session_id,
        action="select",
        selector="#processing",
        value="echo",
    )
    await call(client, "ui_action", session_id=session_id, action="click", selector="#ping")
    await call(
        client,
        "ui_action",
        session_id=session_id,
        action="wait",
        selector="#message",
        text="Ping/pong identifiers match",
        timeout=8,
    )
    await call(client, "ui_action", session_id=session_id, action="click", selector="#start")
    camera = await call(
        client,
        "ui_action",
        session_id=session_id,
        action="wait",
        selector="#verified",
        state="visible",
        timeout=15,
    )
    assert (
        next(control for control in camera["controls"] if control["selector"] == "#stop")[
            "disabled"
        ]
        is False
    )
    await call(client, "ui_action", session_id=session_id, action="click", selector="#stop")
    await call(
        client,
        "ui_action",
        session_id=session_id,
        action="wait",
        selector="#start",
        state="enabled",
    )
    maps = await call(client, "ui_navigate", session_id=session_id, page="maps")
    assert "/maps/" in maps["url"]
    await call(
        client,
        "ui_action",
        session_id=session_id,
        action="wait",
        selector="#example",
        state="enabled",
        timeout=20,
    )
    await call(client, "ui_action", session_id=session_id, action="click", selector="#example")
    await call(
        client,
        "ui_action",
        session_id=session_id,
        action="fill",
        selector="#end-longitude",
        value="103.775",
    )
    await call(
        client, "ui_action", session_id=session_id, action="click", selector="#map", x=100, y=100
    )
    point = await call(client, "ui_snapshot", session_id=session_id)
    assert any(
        control["selector"] == "#end-longitude" and control["value"] != "103.775"
        for control in point["controls"]
    )
    await call(client, "ui_action", session_id=session_id, action="submit", selector="#route-form")
    await call(
        client,
        "ui_action",
        session_id=session_id,
        action="wait",
        selector="#route-summary",
        text="120 m",
        timeout=10,
    )
    # Functionality: Restore a camera page and verify the same tab ID survives both navigations.
    # Logic: Observe the new page snapshot; Constraints: Dedicated fixture browser is closed next.
    returned = await call(client, "ui_navigate", session_id=session_id, page="camera")
    assert "/live/" in returned["url"]
    return {
        "ping": True,
        "camera_echo": True,
        "stop": True,
        "language_select": True,
        "navigation_both_directions": True,
        "fill": True,
        "map_click": True,
        "form_submit": True,
        "async_result_wait": True,
    }


async def run(args, report):
    """Functionality: Verify live discovery/status and optionally database/UI behavior.
    Inputs: CLI args and mutable report. Outputs: Report with complete=True on success.
    Logic: Open one client session; require exactly one browser unless --session is explicit.
    Constraints: Selection ambiguity fails; no implicit browser creation, retries or fallback.
    """
    async with Client(args.url) as client:
        report["tools"] = [tool.name for tool in (await client.list_tools()).tools]
        status = await call(client, "system_status")
        report["database_probe"] = status["database"]["rows"]
        routes = await call(client, "system_routes")
        assert routes["pages"] == {"camera": "live/", "maps": "maps/"}
        report["route_discovery"] = True
        health = await call(client, "system_request", path="/api/health/")
        assert health["status"] == 200 and health["body"]["status"] == "ok"
        report["existing_health_api"] = True
        schema = await call(client, "database_schema")
        assert any(model["model"] == "auth.Group" for model in schema["models"])
        report["schema_introspection"] = True
        if args.exercise_db:
            report["database_crud"] = await check_database(client)
        if args.exercise_ui:
            sessions = (await call(client, "ui_sessions"))["sessions"]
            if args.session:
                session_id = args.session
            elif len(sessions) == 1:
                session_id = sessions[0]["session_id"]
            else:
                raise ValueError("UI verification needs one dedicated tab or an explicit --session")
            report["browser"] = await check_browser(client, session_id)
        report["complete"] = True


def main():
    """Functionality: Run bounded live MCP verification and persist evidence even on failure.
    Inputs: --url, --output, optional --exercise-db/--exercise-ui/--session/--browser-fixture.
    Outputs: JSON report and success/failure exit code. Logic: asyncio.run drives one live client.
    Constraints: Explicit mutation flags; failure is re-raised after report persistence.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8766/mcp")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exercise-db", action="store_true")
    parser.add_argument("--exercise-ui", action="store_true")
    parser.add_argument("--session")
    parser.add_argument(
        "--browser-fixture", choices=["chrome-fake-exact-facing", "chrome-fake-synthetic-facing"]
    )
    args = parser.parse_args()
    report = {
        "complete": False,
        "url": args.url,
        "scope": "Real MCP HTTP and Django database; optional dedicated fixture Chrome UI",
    }
    if args.browser_fixture:
        report["browser_conditions"] = {
            "camera": args.browser_fixture,
            "maps": "synthetic config/route; real Leaflet/CDN",
            "sensor_validation": False,
        }
    try:
        asyncio.run(run(args, report))
    except Exception as error:
        report["error_type"] = type(error).__name__
        raise
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report))


if __name__ == "__main__":
    main()
