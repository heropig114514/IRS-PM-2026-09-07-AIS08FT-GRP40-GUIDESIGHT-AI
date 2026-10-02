"""Responsibilities: Launch the staged Hugging Face Gradio application.
Implementation: Import the verified startup app, then serve its serialized inference events.
Related Modules: zerogpu.app; tools.deploy_space places this file at Space root app.py.
Declaration Index: None.
Variable Index: None.
"""

from zerogpu.app import demo

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=1).launch(show_error=False)
