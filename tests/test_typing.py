"""Golden basedpyright probes: the typing story is part of the contract.

The lambda's return type is checked at both slots, typed context selectors expose
field names to basedpyright, draft writes are statically checked (the
TYPE_CHECKING-gated dunders), and the ok-probe must stay completely clean. A
pyright release that breaks any part of this is a canary firing, not a flake.

The repo-root pyproject.toml excludes tests/typing_probes so project-wide scans
(publish.sh, editors) skip probe_bad.py's deliberate errors -- and pyright honors
that exclude even for files named on the command line. So we check the probes
under their own pyrightconfig.json (same rules, no exclude) via ``-p``, pinning
import resolution to this venv's interpreter with ``--pythonpath``.
"""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

PROBES = Path(__file__).parent / "typing_probes"
REPO = Path(__file__).parent.parent


def _basedpyright(*files: Path) -> list[dict[str, Any]]:
    exe = Path(sys.executable).parent / "basedpyright"
    r = subprocess.run(
        [
            str(exe),
            "--outputjson",
            "-p",
            str(PROBES / "pyrightconfig.json"),
            "--pythonpath",
            sys.executable,
            *map(str, files),
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert r.stdout, r.stderr
    diagnostics = json.loads(r.stdout)["generalDiagnostics"]
    return [d for d in diagnostics if d["severity"] == "error"]


def test_ok_probe_is_clean():
    assert _basedpyright(PROBES / "probe_ok.py") == []


def test_bad_probe_seeded_errors_all_fire():
    bad = PROBES / "probe_bad.py"
    marker = re.compile(r"# BAD\[(?P<rule>[^]]+)]")
    expected = {
        (line_number, match.group("rule"))
        for line_number, line in enumerate(bad.read_text().splitlines(), start=1)
        if (match := marker.search(line))
    }
    diags = _basedpyright(bad)
    actual = {
        (diagnostic["range"]["start"]["line"] + 1, diagnostic.get("rule"))
        for diagnostic in diags
    }
    assert actual == expected, diags
    assert len(diags) == len(expected), diags
