from __future__ import annotations

from typing import Annotated, ClassVar
from typing_extensions import assert_type
import nshconfig as C


class Pair(C.Config):
    c_z: int = C.interp(lambda c: c.root(Run).c_z)


class Run(C.Config):
    name: str
    c_z: int = 128
    pair: Pair = Pair()
    weights: list[float] = C.Field(default_factory=list)
    tag: ClassVar[str] = "run"
    positive: Annotated[int, C.Field(gt=0)] = 1


r = Run.draft()
assert_type(r, Run)
r.name = "x"
r.c_z = 256
r.pair.c_z = C.interp(lambda c: c.root(Run).c_z * 2)
r.weights.append(1.0)
assert_type(r.pair.c_z, int)
assert_type(r.finalize(), Run)
assert_type(r.copy(), Run)
assert_type(Run(name="x"), Run)
assert_type(C.interp(lambda c: c.root(Run).c_z), int)


class More(Run):
    count: int = 1


assert_type(More.draft(), More)
assert_type(More(name="a", count=2), More)
