"""Notebook-defined schemas and editable/final state survive a fresh process."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("future", ["", "from __future__ import annotations"])
def test_notebook_schema_cross_process(tmp_path: Path, future: str):
    payload = tmp_path / "config.pkl"
    env = {**os.environ, "NSHCONFIG_PAYLOAD": str(payload)}
    sender = (
        future
        + """
import os
from pathlib import Path
from typing import Annotated
import cloudpickle
import nshconfig as C

class Leaf(C.Config):
    width: int = C.interp(lambda c: c.root(Run).width)

class Run(C.Config):
    width: Annotated[int, C.AfterValidator(abs)]
    leaves: list[Leaf] = [Leaf()]
    values: dict[str, list[int]] = {'x': [1]}

    @C.check
    def positive(self) -> None:
        assert self.width > 0

r = Run(width=-4)
f = r.finalize()
assert r.leaves[0].width == 4
Path(os.environ['NSHCONFIG_PAYLOAD']).write_bytes(cloudpickle.dumps((r, f, Run)))
"""
    )
    receiver = """
import os
from pathlib import Path
import cloudpickle
import nshconfig as C
r, f, Run = cloudpickle.loads(Path(os.environ['NSHCONFIG_PAYLOAD']).read_bytes())
assert C.is_draft(r) and not C.is_draft(f)
r.width = -8
r.values['x'].append(2)
assert r.leaves[0].width == 8
assert r.finalize().to_dict() == {'width': 8, 'leaves': [{'width': 8}], 'values': {'x': [1, 2]}}
assert f.to_dict() == {'width': 4, 'leaves': [{'width': 4}], 'values': {'x': [1]}}
assert Run(width=2).leaves[0].width == 2
try:
    f.values['x'].append(3)
except C.FrozenError:
    pass
else:
    raise AssertionError('restored snapshot is mutable')
"""
    for script in (sender, receiver):
        result = subprocess.run(
            [sys.executable, "-c", script], env=env, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stdout + result.stderr
