"""Responsibilities: Explicit ZeroGPU camera API routes with shared relay boundaries.
Implementation: Dedicated one-call semaphore selects only the fixed Hugging Face transport.
Related Modules: core.inference_relay/hf_services; config.urls; frontend.camera.
Declaration Index: hf_config; hf_frame.
Variable Index: slot — one ZeroGPU relay call; no local queue or automatic backend switch.
"""

from threading import BoundedSemaphore

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from .hf_services import hf_request
from .inference_relay import relay_config, relay_frame

slot = BoundedSemaphore(1)


@require_safe
def hf_config(request):
    """Functionality: Query initialized Space metadata without invoking the GPU.
    Inputs: request and fixed provider; Outputs: JSON or explicit upstream failure.
    Logic: shared status guard and one call; Constraints: does not prove queue availability.
    """
    return relay_config(request, hf_request, slot, "hf", "ZeroGPU")


@csrf_exempt
@require_POST
def hf_frame(request):
    """Functionality: Return a ZeroGPU-annotated camera JPEG through the cloud.
    Inputs: same-origin JPEG; Outputs: JPEG/metadata or safe diagnosed failure.
    Logic: shared guards call only hf_request; Constraints: approved 60-second client/55 upstream.
    """
    return relay_frame(request, hf_request, slot, "hf", "ZeroGPU", "PyTorch ZeroGPU")
