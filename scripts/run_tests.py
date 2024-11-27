#!/usr/bin/env python
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_tests() -> int:
    """Run all tests using pytest with coverage reporting.

    Returns:
        Exit code from pytest (0 for success, non-zero for failure)
    """
    # Get the project root directory (2 levels up from this script)
    project_root = Path(__file__).parent.parent.resolve()

    # Build the pytest command with all the options from pyproject.toml
    cmd = [
        "pytest",
        "tests",
        "--cov=nshconfig",
        "--cov-report=term-missing",
    ]

    # Run pytest and return its exit code
    try:
        result = subprocess.run(cmd, cwd=project_root, check=False)
        return result.returncode
    except FileNotFoundError:
        print("Error: pytest not found. Please install test dependencies first.")
        return 1


if __name__ == "__main__":
    sys.exit(run_tests())
