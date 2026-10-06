"""Responsibilities: Launch the sole ZeroGPU live API and its bounded WebSocket broker.
Implementation: One queued allocation and direct Python ASGI with LiveMiddleware.
Related Modules: zerogpu.app/live_transport; tools.deploy_space stages root app.py.
Declaration Index: None.
Variable Index: ssr_mode=False — avoid Gradio's Node proxy which omits custom WS upgrades;
default_concurrency_limit=1 — serialize GPU admission without automatic reallocation.
"""

from starlette.middleware import Middleware

from zerogpu.app import demo
from zerogpu.live_transport import LiveMiddleware

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=1).launch(
        show_error=False, ssr_mode=False,
        app_kwargs={"middleware": [Middleware(LiveMiddleware)]},
    )
