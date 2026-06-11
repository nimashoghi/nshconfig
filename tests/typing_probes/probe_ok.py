"""Golden typing probe: every line here must typecheck cleanly under basedpyright."""

import nshconfig as C


class LNConfig(C.Config):
    dim: int = 32


class ModelConfig(C.Config):
    dim: int = 768
    ln: LNConfig
    head_dim: int = C.interp(lambda c: c.nearest(ModelConfig).dim)  # class default slot


def helper(cfg: ModelConfig) -> None:
    cfg.dim = 1024  # draft writes typecheck against declared fields
    cfg.ln.dim = C.interp(lambda c: c.nearest(ModelConfig).dim)  # instance slot


cfg = ModelConfig.config_draft()
helper(cfg)
final = C.finalize(cfg)  # typed (C) -> C
value: int = final.ln.dim
explained: C.Explanation = C.explain(final, "ln.dim")
with C.source("sweep:lr"):
    cfg.dim = 2048

# the config_* verb family is fully typed as methods
final2: ModelConfig = cfg.config_finalize()
thawed: ModelConfig = final2.config_thaw()
exp2: C.Explanation = final2.config_explain("ln.dim")
table: dict[str, list[C.Event]] = final2.config_provenance()
flag: bool = cfg.config_is_draft
