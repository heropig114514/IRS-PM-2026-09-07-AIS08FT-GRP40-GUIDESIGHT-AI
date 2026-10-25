"""Responsibilities: Django management-command entry point for migrations, checks, tests, and user
administration.
Implementation: Select local settings by default, then delegate the command line to Django; startup
errors propagate.
Related Modules: config.settings; production uses DJANGO_SETTINGS_MODULE=config.production.
Declaration Index: main — Select default configuration and execute a management command.
Variable Index: None。
"""

import os
import sys

from django.core.management import execute_from_command_line


def main():
    """Functionality: Execute a Django management command.
    Inputs: sys.argv and process environment, including required DJANGO_SECRET_KEY.
    Outputs: None; the command writes to the console and preserves errors or exit status.
    Logic: Select development settings only when the environment has not selected a settings module,
    then delegate to Django.
    Constraints: Run in a Python environment with dependencies installed; do not generate secrets
    implicitly.
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
