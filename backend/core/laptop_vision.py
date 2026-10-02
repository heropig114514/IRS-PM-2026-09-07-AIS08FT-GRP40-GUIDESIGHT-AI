"""Responsibilities: Select the explicit laptop GPU provider for same-origin camera routes.
Implementation: Shared guards delegate once to the fixed SSH-loopback transport.
Related Modules: core.inference_relay/laptop_services; config.urls; CPU core.vision unchanged.
Declaration Index: laptop_config; laptop_frame.
Variable Index: slot — original one-call non-queued laptop semaphore.
"""

from threading import BoundedSemaphore

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from .inference_relay import relay_config, relay_frame
from .laptop_services import laptop_request

slot = BoundedSemaphore(1)


@require_safe
def laptop_config(request):
    """Functionality: Check actual laptop readiness. Inputs: request and fixed tunnel settings.
    Outputs: readiness JSON or original failure; Logic: shared guard, selected laptop provider.
    Constraints: unchanged semaphore/error codes; no host from browser or CPU substitution.
    """
    return relay_config(request, laptop_request, slot, "laptop", "Laptop")


@csrf_exempt
@require_POST
def laptop_frame(request):
    """Functionality: Return laptop-annotated JPEG. Inputs: same-origin request and JPEG body.
    Outputs: JPEG and original metadata/error codes; Logic: shared guards then laptop_request.
    Constraints: established 8-second upstream and 10-second client budgets remain unchanged.
    """
    return relay_frame(request, laptop_request, slot, "laptop", "Laptop", "TensorRT")
