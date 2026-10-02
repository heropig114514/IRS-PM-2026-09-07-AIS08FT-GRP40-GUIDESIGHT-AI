"""Responsibilities: Explicit loopback GPU worker launch with verified model before listening.
Implementation: Isolated settings, mandatory token, model validation then one Uvicorn process.
Related Modules: config.laptop_worker; core.gpu_detector; tools.laptop_tunnel separate lifetime.
Declaration Index: main.
Variable Index: None; explicit settings and dependencies read by main.
"""

import os


def main():
    """Functionality: Launch the private worker. Inputs: ignored .env.laptop and engine artifacts.
    Outputs: 127.0.0.1:18767 HTTP service or startup failure; Logic: Django setup then GPU load.
    Constraints: no public bind/auto reload/restart, token required before GPU or listener access.
    """
    import django
    import uvicorn

    os.environ["DJANGO_SETTINGS_MODULE"] = "config.laptop_worker"
    django.setup()
    if len(os.environ.get("LAPTOP_INFERENCE_TOKEN", "")) < 32:
        raise ValueError("Explicit laptop inference token is required")
    from core.gpu_detector import get_gpu_detector

    get_gpu_detector()
    uvicorn.run(
        "config.asgi:application",
        host="127.0.0.1",
        port=18767,
        workers=1,
        lifespan="off",
        access_log=False,
    )


if __name__ == "__main__":
    main()
