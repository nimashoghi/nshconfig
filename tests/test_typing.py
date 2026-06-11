"""Golden basedpyright probes: the typing story is part of the contract.

V2_CORE.md section 7: the lambda's return type is checked at both slots, draft
writes are statically checked (the TYPE_CHECKING-gated dunders), and the ok-probe
must stay completely clean. A pyright release that breaks either half of this is a
canary firing, not a flake.

The repo-root pyproject.toml excludes tests/typing_probes so project-wide scans
(publish.sh, editors) skip probe_bad.py's deliberate errors -- and pyright honors
that exclude even for files named on the command line. So we check the probes
under their own pyrightconfig.json (same rules, no exclude) via ``-p``, pinning
import resolution to this venv's interpreter with ``--pythonpath``.
"""

import json
import subprocess
import sys
from pathlib import Path

PROBES = Path(__file__).parent / "typing_probes"
REPO = Path(__file__).parent.parent


def _basedpyright(*files: Path) -> list[dict]:
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
    return [
        d
        for d in json.loads(r.stdout)["generalDiagnostics"]
        if d["severity"] == "error"
    ]


def test_ok_probe_is_clean():
    assert _basedpyright(PROBES / "probe_ok.py") == []


def test_bad_probe_seeded_errors_all_fire():
    bad = PROBES / "probe_bad.py"
    expected_lines = {
        i + 1 for i, line in enumerate(bad.read_text().splitlines()) if "# BAD:" in line
    }
    diags = _basedpyright(bad)
    flagged_lines = {d["range"]["start"]["line"] + 1 for d in diags}
    assert flagged_lines == expected_lines, diags
