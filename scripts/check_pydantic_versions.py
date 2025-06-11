#!/usr/bin/env python3
"""Script to check available Pydantic versions that match our constraints."""

from __future__ import annotations

import json
import subprocess
import sys

from packaging import version
from packaging.specifiers import SpecifierSet


def get_available_versions(package_name: str) -> list[str]:
    """Get all available versions of a package from PyPI."""
    try:
        result = subprocess.run(
            ["pip", "index", "versions", package_name],
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

        return versions
    except subprocess.CalledProcessError:
        print(f"Failed to get versions for {package_name}")
        return []


def filter_versions_by_constraint(versions: list[str], constraint: str) -> list[str]:
    """Filter versions that match the given constraint."""
    spec = SpecifierSet(constraint)
    filtered = []

    for v in versions:
        try:
            if version.parse(v) in spec:
                filtered.append(v)
        except Exception:
            # Skip invalid versions
            continue

    return sorted(filtered, key=version.parse)


def main() -> None:
    """Main function."""
    constraint = ">=2.10"  # From your pyproject.toml

    print(f"Checking Pydantic versions matching constraint: {constraint}")
    print("=" * 60)

    versions = get_available_versions("pydantic")
    if not versions:
        print("Could not retrieve available versions")
        sys.exit(1)

    matching_versions = filter_versions_by_constraint(versions, constraint)

    print(f"Found {len(matching_versions)} matching versions:")
    for v in matching_versions:
        print(f"  - {v}")

    print("\nRecommended test matrix:")
    print("  - Minimum: 2.10.0")
    print("  - Current: 2.11.x (latest patch)")
    print("  - Latest: latest available")

    # Find latest in each minor version
    by_minor = {}
    for v in matching_versions:
        parsed = version.parse(v)
        minor_key = f"{parsed.major}.{parsed.minor}"
        if minor_key not in by_minor or version.parse(v) > version.parse(
            by_minor[minor_key]
        ):
            by_minor[minor_key] = v

    print(f"\nLatest patch for each minor version:")
    for minor_version, latest_patch in sorted(by_minor.items()):
        print(f"  - {minor_version}.x: {latest_patch}")


if __name__ == "__main__":
    main()
