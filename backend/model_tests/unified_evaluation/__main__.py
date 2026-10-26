"""Responsibilities: Invoke the independent external evaluation CLI.
Implementation: Delegate parsing/execution and preserve nonzero failure status.
Related Modules: model_tests.unified_evaluation.runner.
Declaration Index: None.
Variable Index: None.
"""

from .runner import main

if __name__ == "__main__":
    raise SystemExit(main())
