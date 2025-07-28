"""Nox configuration for testing against multiple dependency versions."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence

import nox
from packaging import version
from packaging.specifiers import SpecifierSet


def _get_pydantic_versions(version_constraint: str) -> list[str]:
    """Get available Pydantic versions that match the given constraint."""
    try:
        # Get all available versions from PyPI
        result = subprocess.run(
            ["pip", "index", "versions", "pydantic"],
            capture_output=True,
            text=True,
            check=True,
        )

        # Parse the output to extract versions
        lines = result.stdout.strip().split("\n")
        versions = []
        for line in lines:
            if "Available versions:" in line:
                # Extract versions from the line
                version_part = line.split("Available versions:")[1].strip()
                versions = [v.strip() for v in version_part.split(",")]
                break

        if not versions:
            raise ValueError("No versions found for Pydantic.")

        # Filter versions that match the constraint
        spec = SpecifierSet(version_constraint)
        filtered = []

        for v in versions:
            try:
                if version.parse(v) in spec:
                    filtered.append(v)
            except Exception:
                # Skip invalid versions
                continue

        # Sort by version and return
        return sorted(filtered, key=version.parse)

    except subprocess.CalledProcessError as e:
        # Fallback to hardcoded versions if subprocess fails
        raise ValueError(
            f"Failed to get Pydantic versions: {e}. Falling back to hardcoded versions."
        ) from e


PYTHON_VERSIONS = ["3.9", "3.10", "3.11", "3.12"]
PYDANTIC_VERSIONS = _get_pydantic_versions(">=2")


def _resolve_python_session(python: str | Sequence[str] | bool | None):
    """Return a parsed packaging.version.Version for the given nox python arg."""
    if isinstance(python, str):
        return version.parse(python)
    if python is False or python is None:
        # `False` and `None` both indicate the current interpreter.
        return version.parse(sys.version.split(maxsplit=1)[0])
    raise ValueError(f"Unsupported Python version specification: {python}")


@nox.session(python=PYTHON_VERSIONS)
@nox.parametrize("pydantic", PYDANTIC_VERSIONS)
def tests(session: nox.Session, pydantic: str) -> None:
    """Run tests against different Python and Pydantic versions."""
    # Install dependencies
    deps: list[str] = []
    # This package
    deps.append(".[extra]")
    # Base deps
    deps.extend(["pytest", "pytest-cov", "ruff", "basedpyright"])
    # Pydantic-specific dependencies
    deps.extend(["typing-extensions", "pydantic-settings"])
    # Install eval_type_backport for Python < 3.10
    if _resolve_python_session(session.python) < version.parse("3.10"):
        deps.append("eval_type_backport")
    # Pydantic itself
    deps.append(f"pydantic=={pydantic}")

    session.install(*deps)

    # Run linting and type checking
    session.run("ruff", "check", "src")
    session.run("basedpyright", "src")

    # Run tests
    session.run("pytest")


if __name__ == "__main__":
    nox.main()
