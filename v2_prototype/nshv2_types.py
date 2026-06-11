from __future__ import annotations
from nshv2 import Config, derive, finalize

class LNConfig(Config):
    dim: int = 32

class ModelConfig(Config):
    dim: int = 768
    ln: LNConfig

ok_class_default: int = derive(lambda c: c.nearest(ModelConfig).dim)
cfg = ModelConfig.draft()
cfg.ln.dim = derive(lambda c: c.nearest(ModelConfig).dim)   # OK
f = finalize(cfg)
good: int = f.ln.dim                                        # OK: finalize is C -> C

bad1: int = derive(lambda c: "oops")                        # E: str not assignable to int
cfg.ln.dim = derive(lambda c: "oops")                       # E: str not assignable to int
cfg.ln.dmi = 3                                              # E: unknown attribute (gated dunders)
