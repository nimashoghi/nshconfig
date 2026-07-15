"""Golden typing probe: this file must remain clean under basedpyright."""

from __future__ import annotations

from typing import Annotated

from typing_extensions import assert_type

import nshconfig as C


class LayerNorm(C.Config):
    dim: int = 32


class Model(C.Config):
    dim: int = 768
    norm: LayerNorm
    head_dim: int = C.interp(lambda context: context.current(Model).dim)


class Run(C.Config):
    scale: int = 2
    model: Model


class ProjectConfig(C.Config):
    model_config = C.ConfigDict(strict=False)

    count: Annotated[int, C.Field(gt=0)]

    @C.field_validator("count")
    @classmethod
    def validate_count(cls, value: int, info: C.ValidationInfo) -> int:
        assert info.field_name == "count"
        return value


def compose(work: Model) -> None:
    work.dim = 1024
    work.norm.dim = C.interp(lambda context: context.current(LayerNorm).dim)
    work.norm.dim = C.interp(lambda context: context.parent(Model).dim)
    work.norm.dim = C.interp(lambda context: context.parent(1, Model).dim)
    work.norm.dim = C.interp(lambda context: context.nearest(Model).dim)
    work.norm.dim = C.interp(lambda context: context.root(Run).scale)
    work.norm.dim = C.interp(lambda context: context.root().dynamic.path)


work = Model.config_draft()
assert_type(work, Model)
compose(work)
final = work.config_finalize()
assert_type(final, Model)
value: int = final.norm.dim
draft_flag: bool = C.is_draft(work)
work.dim = 2048
relaxed = ProjectConfig(count=2)
positive: int = relaxed.count
