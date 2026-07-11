"""Interpolation views never expose raw Config or validation-frame objects."""

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import PrivateAttr, ValidationError

import nshconfig as C


class _Child(C.Config):
    value: int
    _secret: int = PrivateAttr(default=91)


@pytest.mark.parametrize(
    "resolver",
    [
        lambda context: context.current().child._value._secret,
        lambda context: context.current()._frame.values["child"]._secret,
        lambda context: context._stack[-1].values["child"]._secret,
    ],
)
def test_context_and_object_backing_slots_are_inaccessible(
    resolver: Callable[[C.Context], Any],
) -> None:
    class Root(C.Config):
        child: _Child
        leaked: int = C.interp(resolver)

    with pytest.raises(
        ValidationError,
        match="does not expose|declared fields only|unavailable on",
    ):
        Root(child={"value": 7})


@pytest.mark.parametrize(
    "resolver",
    [
        lambda context: context.current().children._value[0]._secret,
        lambda context: context.current().children[:][0]._secret,
        lambda context: (context.current().children + [])[0]._secret,
        lambda context: (context.current().children * 2)[0]._secret,
        lambda context: next(iter(context.current().by_name.values()))._secret,
    ],
)
def test_container_operations_preserve_declared_field_views(
    resolver: Callable[[C.Context], Any],
) -> None:
    class Root(C.Config):
        children: list[_Child]
        by_name: dict[str, _Child]
        leaked: int = C.interp(resolver)

    with pytest.raises(
        ValidationError,
        match="does not expose|declared fields only|unavailable on",
    ):
        Root(
            children=[{"value": 7}],
            by_name={"first": {"value": 8}},
        )


def test_sliced_container_views_still_expose_declared_values() -> None:
    class Root(C.Config):
        children: list[_Child]
        copied: int = C.interp(lambda context: context.current().children[:][0].value)

    final = Root(children=[{"value": 7}])
    assert final.copied == 7
    assert C.explain(final, "copied").events[-1].reads == (
        ("children", "[_Child(value=7)]"),
        ("children[0].value", "7"),
    )
