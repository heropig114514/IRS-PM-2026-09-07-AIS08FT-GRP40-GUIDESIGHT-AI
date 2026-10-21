"""Responsibilities: Expose the explicitly enabled combined maps and local perception entry.
Implementation: Wrap the existing local ASGI application without changing software or algorithms.
Related Modules: integration.assist/asgi; tools.run_local_mvp; config.asgi.
Declaration Index: None.
Variable Index: application — combined entry around the imported local wrapper instance.
"""

from .asgi import application as local_application
from .assist import AssistApplication

application = AssistApplication(local_application.software, local=local_application)
