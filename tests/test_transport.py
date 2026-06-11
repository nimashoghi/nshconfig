"""Transport: pickles are how work-in-progress travels (V2_CORE.md section 8).

The flagship path: a PENDING draft whose classes live in a notebook (``__main__``,
cloudpickled BY VALUE) ships to a process that has only the library, arrives as a
draft, finalizes there, and can still explain itself.
"""

from __future__ import annotations

import pickle
import subprocess
import sys
from pathlib import Path

import cloudpickle
import pytest

import nshconfig as C


class LNConfig(C.Config):
    dim: int = C.interp(lambda c: c.nearest(ModelConfig).dim)


class EncoderConfig(C.Config):
    ln: LNConfig


class ModelConfig(C.Config):
    dim: int = 768
    encoder: EncoderConfig


class TrainConfig(C.Config):
    width: int = 512
    model: ModelConfig


def test_cloudpickle_roundtrip_pending_draft_stays_live():
    cfg = TrainConfig.draft()
    cfg.width = 100
    cfg.model.dim = C.interp(lambda c: c.root.width * 2)
    cfg2 = pickle.loads(cloudpickle.dumps(cfg))
    assert C.is_draft(cfg2)
    cfg2.width = 200  # the round-tripped draft stays live
    f = C.finalize(cfg2)
    assert (f.model.dim, f.model.encoder.ln.dim) == (400, 400)


def test_plain_pickle_policy():
    # lambdas do not plain-pickle: loud, documented; cloudpickle is the channel.
    cfg = TrainConfig.draft()
    cfg.model.dim = C.interp(lambda c: c.root.width)
    with pytest.raises(Exception):
        pickle.dumps(cfg)
    # named module-level resolvers plain-pickle fine:
    cfg2 = TrainConfig.draft()
    cfg2.model.dim = C.interp(_double_width)
    assert C.finalize(pickle.loads(pickle.dumps(cfg2))).model.dim == 1024
    # markerless drafts and finals plain-pickle fine:
    cfg3 = TrainConfig.draft()
    cfg3.width = 7
    assert pickle.loads(pickle.dumps(cfg3)).width == 7
    f = C.finalize(TrainConfig.draft())
    assert pickle.loads(pickle.dumps(f)) == f
    assert pickle.loads(cloudpickle.dumps(f)) == f


def _double_width(c: C.Ctx) -> int:
    return c.root.width * 2


_NOTEBOOK_SIDE = """
import pathlib
import cloudpickle
import nshconfig as C

# Classes defined in __main__: cloudpickle ships them BY VALUE, including the
# pydantic validator/serializer objects (the _LazyValSer shim's reason to exist:
# the interp lambda's __globals__ forward-reference the sibling model classes).
class LNConfig(C.Config):
    dim: int = C.interp(lambda c: c.nearest(ModelConfig).dim)

class EncoderConfig(C.Config):
    ln: LNConfig

class ModelConfig(C.Config):
    dim: int = 768
    encoder: EncoderConfig

class TrainConfig(C.Config):
    width: int = 512
    model: ModelConfig

cfg = TrainConfig.draft()
cfg.model.dim = 2048
cfg.model.encoder.ln.dim = C.interp(lambda c: c.nearest(ModelConfig).dim)  # pending!
pathlib.Path({payload!r}).write_bytes(cloudpickle.dumps(cfg))
print("dumped")
"""

_CLUSTER_SIDE = """
import pathlib
import cloudpickle
import nshconfig as C

d = cloudpickle.loads(pathlib.Path({payload!r}).read_bytes())
assert C.is_draft(d)
f = C.finalize(d)
assert f.model.encoder.ln.dim == 2048, f.model.encoder.ln.dim
ex = str(C.explain(f, "model.encoder.ln.dim"))
assert "because" in ex and "interpolated to 2048" in ex
print("cluster OK", f.model.encoder.ln.dim)
"""


def test_notebook_to_cluster_by_value_canary(tmp_path: Path):
    payload = str(tmp_path / "payload.pkl")
    for name, script in [("notebook", _NOTEBOOK_SIDE), ("cluster", _CLUSTER_SIDE)]:
        r = subprocess.run(
            [sys.executable, "-c", script.format(payload=payload)],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, f"{name} side failed:\n{r.stderr}"
    # provenance (sites) survived: asserted inside the cluster-side script.
