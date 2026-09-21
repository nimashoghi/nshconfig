"""Verify the public API with each supported checker, including expected failures."""

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest


@pytest.mark.parametrize("checker", ["ty", "pyright", "mypy"])
def test_public_typing(checker: str):
    if importlib.util.find_spec(checker) is None:
        pytest.skip(f"{checker} is checked in the full development environment")
    directory = Path(__file__).parent / "v3_typing"
    for fixture in ("positive", "negative"):
        path = directory / f"{fixture}.py"
        command = [sys.executable, "-m", checker]
        if checker == "ty":
            command += ["check", str(path), "--output-format", "concise"]
        elif checker == "pyright":
            command += [str(path), "--outputjson"]
        else:
            command += [str(path), "--follow-imports=silent", "--no-error-summary"]
        result = subprocess.run(command, capture_output=True, text=True)
        output = result.stdout + result.stderr
        if fixture == "positive":
            assert result.returncode == 0, output
            continue
        expected = {
            i
            for i, line in enumerate(path.read_text().splitlines(), 1)
            if "# error:" in line
        }
        assert result.returncode == 1, output
        if checker == "pyright":
            diagnostics = json.loads(result.stdout)["generalDiagnostics"]
            actual = {
                d["range"]["start"]["line"] + 1
                for d in diagnostics
                if d["severity"] == "error"
            }
        else:
            actual = {
                int(n)
                for n in re.findall(r"negative\.py:(\d+)(?::\d+)?: error", output)
            }
        assert actual == expected, output
