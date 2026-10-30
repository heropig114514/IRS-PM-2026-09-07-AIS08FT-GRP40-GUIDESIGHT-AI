"""Responsibilities: Select the explicitly configured local multi-phone inference pool.
Implementation: Validate fixed loopback ports and expose one process-local ASGI dispatcher.
Related Modules: integration.inference.pool; tools.run_phone_mvp.
Declaration Index: None.
Variable Index: application owns session leases; PRS_INFERENCE_POOL_PORTS is required strict CSV.
"""

import os

from .pool import InferencePool, pool_ports

application = InferencePool(pool_ports(os.environ.get("PRS_INFERENCE_POOL_PORTS")))
