"""Arbitrary mapping keys use truthful coarse interpolation dependencies."""

from dataclasses import dataclass

from pydantic import BaseModel

import nshconfig as C


@dataclass(frozen=True)
class _Key:
    name: str


_KEY = _Key("selected")


def test_structured_value_below_arbitrary_key_uses_whole_mapping_dependency() -> None:
    class Values(C.Config, arbitrary_types_allowed=True):
        values: dict[_Key, list[int]]
        first: int = C.interp(lambda context: context.current().values[_KEY][0])

    final = Values(values={_KEY: [4, 5]})
    assert final.first == 4
    assert C.provenance(final)["first"][-1].reads == (
        ("values", "{_Key(name='selected'): [4, 5]}"),
    )


def test_atomic_model_below_arbitrary_key_remains_a_declared_field_view() -> None:
    class Plain(BaseModel):
        value: int

    class Values(C.Config, arbitrary_types_allowed=True):
        values: dict[_Key, Plain]
        selected: int = C.interp(lambda context: context.current().values[_KEY].value)

    final = Values(values={_KEY: Plain(value=7)})
    assert final.selected == 7
    assert C.provenance(final)["selected"][-1].reads[0][0] == "values"
