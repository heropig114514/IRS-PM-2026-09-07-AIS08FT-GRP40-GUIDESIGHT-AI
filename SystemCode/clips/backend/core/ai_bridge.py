"""Responsibilities: Connect MCP commands to the application's open browser tabs.
Implementation: One process owns live WebSockets and correlates commands with result futures.
Related Modules: core.ai_mcp, config.asgi, SystemCode/clips/frontend/ai-control.js.
Declaration Index: BrowserSession — tab transport state; BrowserSession.describe — page metadata;
BrowserHub — registry; BrowserHub.__init__ — empty state; BrowserHub.sessions — list tabs;
BrowserHub.connection — current generation; BrowserHub.command — dispatch/await once;
BrowserHub.wait_page — observe replacement page; BrowserHub.socket — ASGI transport lifecycle.
Variable Index: logger — lifecycle diagnostics; hub — process-local browser registry;
BrowserSession.id/page/send — tab identity, metadata and ASGI writer; pending — result futures;
lock — serialized command sends; BrowserHub._sessions — live connection generations by tab ID.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class BrowserSession:
    """Functionality: Hold one tab's transport, page identity and outstanding commands.
    Logic: The session object identifies a connection generation, even across page navigation.
    Constraints: Futures and the send lock belong to the ASGI event loop; no durable storage.
    """

    id: str
    page: dict
    send: object
    pending: dict = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def describe(self):
        """Functionality: Return the latest page identity without transport internals.
        Inputs: self.id/page. Outputs: JSON-compatible tab description.
        Logic: Copy the page metadata and attach the tab identifier.
        Constraints: Metadata describes the last browser report, not an unsolicited DOM refresh.
        """
        return {"session_id": self.id, **self.page}


class BrowserHub:
    """Functionality: Route tools to live tabs and resolve browser acknowledgements.
    Logic: Correlation IDs prevent late results from completing different commands.
    Constraints: Run one ASGI worker; disconnect and timeout never trigger automatic retries.
    """

    def __init__(self):
        """Functionality: Initialize empty process-local tab state.
        Inputs: None. Outputs: Registry instance. Logic: No loop-bound primitives until use.
        Constraints: Restart discards all sessions and outstanding work.
        """
        self._sessions = {}

    def sessions(self):
        """Functionality: List currently connected tabs.
        Inputs: self._sessions. Outputs: List of page descriptions.
        Logic: Snapshot the registry without choosing a default tab.
        Constraints: Call on the owning event loop; callers must select a session explicitly.
        """
        return [session.describe() for session in self._sessions.values()]

    def connection(self, session_id):
        """Functionality: Identify the current connection generation for navigation observation.
        Inputs: session_id. Outputs: BrowserSession or None. Logic: Read the owning registry.
        Constraints: Identity is meaningful only on the current ASGI process/event loop.
        """
        return self._sessions.get(session_id)

    async def command(self, session_id, action, arguments=None, timeout=10):
        """Functionality: Send exactly one browser command and await its result.
        Inputs: session_id, action, arguments dict and timeout seconds (0 < timeout <= 60).
        Outputs: Browser result; raises ValueError, ConnectionError or TimeoutError on failure.
        Logic: Register the future before sending; clean it in finally, including cancellation.
        Constraints: Timeout means completion is unknown; a dispatched mutation may have run.
        """
        if not 0 < timeout <= 60:
            raise ValueError("timeout must be greater than 0 and at most 60 seconds")
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError("Browser session is not connected; open or refresh a PRS page")
        command_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        session.pending[command_id] = future
        logger.info(
            "browser command sent session=%s id=%s action=%s", session_id, command_id, action
        )
        try:
            async with session.lock:
                await session.send(
                    {
                        "type": "websocket.send",
                        "text": json.dumps(
                            {
                                "type": "command",
                                "id": command_id,
                                "action": action,
                                "arguments": arguments or {},
                            }
                        ),
                    }
                )
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as error:
            logger.error(
                "browser command timeout session=%s id=%s action=%s", session_id, command_id, action
            )
            raise TimeoutError(
                "Browser command timed out; completion is unknown. "
                "Inspect the page before repeating a mutation."
            ) from error
        finally:
            session.pending.pop(command_id, None)

    async def wait_page(self, session_id, previous, timeout):
        """Functionality: Await registration of a new page after explicit navigation.
        Inputs: session_id, previous connection object, timeout seconds.
        Outputs: New tab description or TimeoutError.
        Logic: Observe connection generation; do not issue another navigation request.
        Constraints: This is bounded state observation, not a retry of a side effect.
        """
        async with asyncio.timeout(timeout):
            while True:
                current = self._sessions.get(session_id)
                if current is not None and current is not previous:
                    return current.describe()
                await asyncio.sleep(0.05)

    async def socket(self, scope, receive, send):
        """Functionality: Accept an unauthenticated browser-control WebSocket.
        Inputs: ASGI scope/receive/send; hello includes session_id and page metadata.
        Outputs: Welcome, command and pong messages; releases futures on disconnect.
        Logic: Validate message shape, replace duplicate IDs, resolve only matching results.
        Constraints: Invalid protocol closes with 1008; single worker; no image or DOM archive.
        """
        session = None
        try:
            if (await receive())["type"] != "websocket.connect":
                return
            await send({"type": "websocket.accept"})
            event = await asyncio.wait_for(receive(), 10)
            hello = json.loads(event.get("text", ""))
            if not isinstance(hello, dict) or hello.get("type") != "hello":
                raise ValueError("First message must be hello")
            session_id = hello.get("session_id")
            page = hello.get("page")
            if not isinstance(session_id, str) or not 1 <= len(session_id) <= 128:
                raise ValueError("Invalid session_id")
            if not isinstance(page, dict):
                raise ValueError("Invalid page metadata")
            session = BrowserSession(session_id, page, send)
            previous = self._sessions.get(session_id)
            self._sessions[session_id] = session
            if previous:
                await previous.send({"type": "websocket.close", "code": 1000})
            logger.info("browser connected session=%s", session_id)
            await send(
                {
                    "type": "websocket.send",
                    "text": json.dumps(
                        {
                            "type": "welcome",
                            "session_id": session_id,
                        }
                    ),
                }
            )
            while True:
                event = await receive()
                if event["type"] == "websocket.disconnect":
                    break
                message = json.loads(event.get("text", ""))
                if not isinstance(message, dict):
                    raise ValueError("Control message must be an object")
                if message.get("type") == "ping":
                    await send({"type": "websocket.send", "text": '{"type":"pong"}'})
                elif message.get("type") == "result":
                    future = session.pending.get(message.get("id"))
                    if future is None or future.done():
                        continue
                    if message.get("ok") is True:
                        future.set_result(message.get("result"))
                    else:
                        future.set_exception(ValueError(str(message.get("error", "Action failed"))))
                else:
                    raise ValueError("Unknown control message type")
        except (ValueError, TimeoutError, TypeError):
            logger.exception("browser protocol failed session=%s", session.id if session else "new")
            await send({"type": "websocket.close", "code": 1008})
        finally:
            if session is not None:
                if self._sessions.get(session.id) is session:
                    del self._sessions[session.id]
                for future in session.pending.values():
                    if not future.done():
                        future.set_exception(
                            ConnectionError(
                                "Browser disconnected; completion is unknown. "
                                "Inspect before retrying."
                            )
                        )
                logger.info(
                    "browser disconnected session=%s pending=%s", session.id, len(session.pending)
                )


hub = BrowserHub()
