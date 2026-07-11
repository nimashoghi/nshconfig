"""Atomic values are scanned for lifecycle state without becoming graph nodes."""

from collections import deque
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

import nshconfig as C


class _Atom:
    def __init__(self, cache: Any) -> None:
        self.cache = cache


def test_atomic_internal_containers_do_not_inherit_structural_graph_policy() -> None:
    class Root(C.Config):
        payload: Any

    atom = _Atom(deque([1, 2]))
    final = Root(payload=atom)
    assert final.payload is atom
    assert final.payload.cache == deque([1, 2])


def test_atomic_internal_cycles_are_implementation_detail() -> None:
    class Root(C.Config):
        payload: Any

    atom = _Atom(None)
    atom.cache = atom
    assert Root(payload=atom).payload is atom


def test_atomic_state_still_cannot_conceal_a_draft() -> None:
    class Child(C.Config):
        value: int

    class Root(C.Config):
        payload: Any

    atom = _Atom([C.draft(Child)])
    with pytest.raises(ValidationError) as caught:
        Root(payload=atom)
    assert caught.value.errors()[0]["type"] == "nshconfig_pending_draft"


def test_ordinary_pydantic_models_are_identity_bearing_atomic_values() -> None:
    class Plain(BaseModel):
        values: list[int]

    class Root(C.Config):
        first: Plain
        second: Plain

    shared = Plain(values=[1])
    final = Root(first=shared, second=shared)
    assert final.first is shared
    assert final.second is shared


def test_builtin_class_objects_are_safe_atomic_values() -> None:
    class Root(C.Config):
        factory: Any

    assert Root(factory=int).factory is int
