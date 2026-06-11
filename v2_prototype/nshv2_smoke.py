from __future__ import annotations
from pydantic import ValidationError
from nshv2 import Config, derive, finalize, DraftError

class LNConfig(Config):
    dim: int = 32
    eps: float = 1e-5

class EncoderConfig(Config):
    ln: LNConfig

class HeadConfig(Config):
    dim: int = derive(lambda c: c.nearest(ModelConfig).dim)   # class-default slot

class ModelConfig(Config):
    dim: int = 768
    encoder: EncoderConfig
    head: HeadConfig

class TrainConfig(Config):
    batch: int = 8
    model: ModelConfig

# instance-level + class-level through ONE rule
cfg = TrainConfig.draft()
cfg.model.dim = 1024
cfg.model.encoder.ln.dim = derive(lambda c: c.nearest(ModelConfig).dim)
f = finalize(cfg)
assert f.model.encoder.ln.dim == 1024 and f.model.head.dim == 1024

# fields_set scrub: injected class-default marker must NOT appear user-set
assert "dim" not in f.model.head.__pydantic_fields_set__, f.model.head.__pydantic_fields_set__

# override + del re-arm
cfg.model.encoder.ln.dim = 17
assert finalize(cfg).model.encoder.ln.dim == 17
del cfg.model.encoder.ln.dim
assert finalize(cfg).model.encoder.ln.dim == 32

# hygiene: f-string/bool raise at compose time
m = derive(lambda c: c.root.batch)
try:
    s = f"{m}"; raise SystemExit("format did not raise")
except DraftError: pass
try:
    bool(m); raise SystemExit("bool did not raise")
except DraftError: pass

# orphan: dotted path + site + ancestors
lonely = EncoderConfig.draft()
lonely.ln.dim = derive(lambda c: c.nearest(ModelConfig).dim)
try:
    finalize(lonely); raise SystemExit("orphan did not raise")
except ValidationError as e:
    msg = str(e)
    assert "cannot derive ln.dim" in msg and "ancestors here" in msg and "nshv2_smoke.py" in msg, msg

# mutual instance cycle: both ends named
class A(Config):
    x: int = 1
class B(Config):
    y: int = 1
class Pair(Config):
    a: A
    b: B
p = Pair.draft()
p.a.x = derive(lambda c: c.root.b.y)
p.b.y = derive(lambda c: c.root.a.x)
try:
    finalize(p); raise SystemExit("cycle did not raise")
except ValidationError as e:
    assert "pending derivation" in str(e), str(e)

# Any-field smuggle caught by root sweep
from typing import Any as _Any
class Meta(Config):
    meta: _Any = None
mt = Meta.draft()
mt.meta = [derive(lambda c: c.root)]
try:
    finalize(mt); raise SystemExit("sweep did not catch")
except ValidationError as e:
    assert "leaked into the final" in str(e) and "meta[0]" in str(e), str(e)

# re-entrancy guard: nested model_validate inside a resolver is a fresh root
class Inner(Config):
    v: int = derive(lambda c: c.root.q)   # would wrongly resolve against OUTER root without the guard
class Outer(Config):
    q: int = 5
    z: int = derive(lambda c: 0 * 0 or _nested())
def _nested():
    try:
        Inner.model_validate({})
        return -1   # resolved against the wrong tree: guard FAILED
    except ValidationError:
        return 42   # fresh root -> orphan error -> guard works
assert finalize(Outer.draft()).z == 42

# pending draft repr
d2 = TrainConfig.draft()
d2.model.encoder.ln.dim = derive(lambda c: c.nearest(ModelConfig).dim)
r = repr(d2.model.encoder.ln)
assert "pending: instance" in r, r
print("ALL SMOKE CHECKS PASSED")
print("repr sample:", r)
