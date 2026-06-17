"""Golden typing probe: every line here must typecheck cleanly under basedpyright."""

from typing import Annotated

import nshconfig as C


class LNConfig(C.Config):
    dim: int = 32


class ModelConfig(C.Config):
    dim: int = 768
    ln: LNConfig
    head_dim: int = C.interp(lambda c: c.nearest(ModelConfig).dim)  # class default slot


class TrainConfig(C.Config):
    scale: int = 2
    model: ModelConfig


class ReexportConfig(C.Config):
    model_config = C.ConfigDict(str_strip_whitespace=True)

    x: Annotated[int, C.Field(gt=0)]
    y: C.PositiveInt = 1

    @C.field_validator("x")
    @classmethod
    def validate_x(cls, value: int, info: C.ValidationInfo) -> int:
        assert info.field_name == "x"
        return value


def helper(cfg: ModelConfig) -> None:
    cfg.dim = 1024  # draft writes typecheck against declared fields
    cfg.ln.dim = C.interp(lambda c: c.nearest(ModelConfig).dim)  # instance slot
    cfg.ln.dim = C.interp(lambda c: c.self(LNConfig).dim)
    cfg.ln.dim = C.interp(lambda c: c.parent(ModelConfig).dim)
    cfg.ln.dim = C.interp(lambda c: c.parent(1, ModelConfig).dim)
    cfg.ln.dim = C.interp(lambda c: c.root(TrainConfig).scale)
    cfg.ln.dim = C.interp(lambda c: c.root().dynamic.path)  # untyped selector stays dynamic


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
adapter: C.TypeAdapter[int] = C.TypeAdapter(int)
adapted: int = adapter.validate_python(1)
reexported = ReexportConfig(x=2)
positive: int = reexported.y
