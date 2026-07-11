"""Diagnostics and notebook representations tolerate hostile user objects."""

from typing import Any

import pytest
from pydantic import Field, ValidationError

import nshconfig as C


class _HostileError(Exception):
    def __str__(self) -> str:
        raise RuntimeError("exception str exploded")

    def __repr__(self) -> str:
        raise RuntimeError("exception repr exploded")


class _HostileRepr:
    def __repr__(self) -> str:
        raise RuntimeError("value repr exploded")


def _raise_hostile_error(*args: Any, **kwargs: Any) -> Any:
    del args, kwargs
    raise _HostileError()


def test_interpolation_wraps_an_exception_with_hostile_string_methods() -> None:
    class Broken(C.Config):
        value: int = C.interp(_raise_hostile_error)

    with pytest.raises(ValidationError) as caught:
        Broken()
    error = caught.value.errors()[0]
    assert error["type"] == "nshconfig_interpolation"
    assert "repr unavailable" in error["msg"]


def test_draft_default_errors_have_guarded_diagnostics() -> None:
    class BrokenDefault(C.Config):
        value: int = Field(default_factory=_raise_hostile_error)

    work = C.draft(BrokenDefault)
    with pytest.raises(C.UnsetError, match="repr unavailable"):
        _ = work.value


def test_draft_repr_tolerates_hostile_values_and_direct_cycles() -> None:
    class Recipe(C.Config, arbitrary_types_allowed=True):
        value: Any
        other: Any = None

    hostile = C.draft(Recipe)
    hostile.value = _HostileRepr()
    assert "repr unavailable" in repr(hostile)

    cyclic = C.draft(Recipe)
    cyclic.value = cyclic
    cyclic.other = [cyclic]
    rendered = repr(cyclic)
    assert "recursive draft Recipe" in rendered
    assert len(rendered) <= 1200
