from __future__ import annotations

import copy
import json
import pickle
from pathlib import Path
from typing import Annotated, ClassVar

import pytest

import nshconfig as C


class Importable(C.Config):
    value: int = 1
    items: list[int] = [1]


def test_constraints_factories_inheritance_and_exports():
    class Base(C.Config):
        tag: ClassVar[str] = "base"
        value: Annotated[int, C.Field(gt=0)] = 2
        path: Path = Path("weights.pt")
        items: list[int] = C.Field(default_factory=list)

    class Child(Base):
        value: int = 3
        extra: str = "x"

    a, b = Base(), Base()
    a.items.append(1)
    assert list(b.items) == []
    a.value = 0
    with pytest.raises(C.ConfigError):
        _ = a.value
    assert Child(value=0).value == 0
    f = Child().finalize()
    assert json.loads(f.to_json()) == {
        "value": 3,
        "path": "weights.pt",
        "items": [],
        "extra": "x",
    }
    exported = f.to_dict()
    exported["items"].append(4)
    assert list(f.items) == []
    with pytest.raises(C.ConfigError, match="finalize"):
        Child().to_dict()


def test_quoted_late_and_local_annotations():
    class Parent(C.Config):
        child: "Child"

    class Child(C.Config):
        value: int = 1

    Parent.rebuild(namespace={"Child": Child})
    assert Parent(child=Child()).finalize().child.value == 1
    Local = Annotated[int, C.AfterValidator(abs)]

    class UsesLocal(C.Config):
        value: Local

    assert UsesLocal(value=-3).value == 3


def test_pickle_and_copy_keep_ownership_and_readonly_state():
    for original in (Importable(), Importable().finalize()):
        restored = pickle.loads(pickle.dumps(original))
        assert C.is_draft(restored) == C.is_draft(original)
        assert restored.items == [1]
        copied = copy.deepcopy(restored)
        assert C.is_draft(copied)
        copied.items.append(2)
        assert list(restored.items) == [1]
        assert list(copied.items) == [1, 2]


def test_read_only_computation_runs_once_per_finalization():
    calls = []
    checks = []

    def compute(c: C.Context) -> int:
        calls.append(1)
        return c.current(Run).value * 2

    class Run(C.Config):
        a: int = C.interp(compute)
        b: int = C.interp(lambda c: c.current(Run).a + c.current(Run).a)
        value: int = 3

        @C.check
        def checked(self) -> None:
            checks.append(1)

    r = Run()
    f = r.finalize()
    assert (f.a, f.b) == (6, 12)
    assert len(calls) == len(checks) == 1
    assert r.a == 6 and len(calls) == 2


def test_ownership_failures_do_not_partially_attach_incoming_children():
    class Child(C.Config):
        value: int = 1

    class Parent(C.Config):
        children: list[Child] = []

    owned, unattached = Child(), Child()
    a = Parent(children=[owned])
    b = Parent()
    with pytest.raises(C.OwnershipError):
        b.children.extend([unattached, owned])
    assert list(b.children) == []
    b.children.append(unattached)
    assert b.children[0] is unattached
    a.children.clear()
    b.children.append(owned)
    assert b.children[1] is owned


def test_detached_container_alias_stays_independent():
    r = Importable()
    alias = r.items
    r.items = [3]
    alias.append(2)
    assert list(r.items) == [3]
    assert list(alias) == [1, 2]


def test_deletion_unsets_and_does_not_restore_default():
    r = Importable()
    del r.value
    with pytest.raises(C.MissingValueError):
        _ = r.value
    r.value = 4
    assert r.value == 4


def test_disallow_structural_cycles():
    class Recursive(C.Config):
        child: Recursive | None = None

    r = Recursive()
    with pytest.raises(C.OwnershipError):
        r.child = r
    child = Recursive()
    r.child = child
    with pytest.raises(C.OwnershipError):
        child.child = r
    raw = []
    raw.append(raw)
    with pytest.raises(C.OwnershipError):
        Importable(items=raw)


def test_computed_container_preserves_source_context_and_is_complete():
    class Leaf(C.Config):
        width: int = C.interp(lambda c: c.nearest(Group).width)

    class Group(C.Config):
        width: int = 3
        leaves: list[Leaf] = [Leaf()]

    class Root(C.Config):
        group: Group = Group()
        copied: list[Leaf] = C.interp(lambda c: c.current(Root).group.leaves)

    r = Root()
    assert r.copied[0].width == 3
    r.group.width = 4
    assert r.finalize().copied[0].width == 4


def test_interpolated_containers_read_normalized_source_values():
    class Run(C.Config):
        xs: list[Annotated[int, C.AfterValidator(lambda x: x + 1)]] = [1]
        copied: list[int] = C.interp(lambda c: c.current(Run).xs)
        nested: dict[str, list[int]] = C.interp(lambda c: {"x": c.current(Run).xs})

    r = Run()
    assert list(r.xs) == [2]
    assert list(r.copied) == [2]
    assert list(r.nested["x"]) == [2]
    assert r.finalize().to_dict() == {"xs": [2], "copied": [2], "nested": {"x": [2]}}


def test_failed_constructor_does_not_take_ownership_of_arguments():
    class Parent(C.Config):
        a: Importable
        b: Importable

    child = Importable()
    with pytest.raises(C.OwnershipError):
        Parent(a=child, b=child)
    second = Importable()
    c = Parent(a=child, b=second)
    assert c.a is child
    assert c.b is second
