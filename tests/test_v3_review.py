"""Public regressions found by the independent pre-merge review."""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Annotated, Any

import pytest

import nshconfig as C


def test_computed_children_keep_distinct_resolution_identities():
    class Child(C.Config):
        x: int

    class Root(C.Config):
        children: list[Child] = C.interp(lambda c: [Child(x=i) for i in range(2000)])

    assert [child.x for child in Root().children] == list(range(2000))
    assert [child.x for child in Root().finalize().children] == list(range(2000))


def test_final_copy_preserves_normalization_inputs_after_container_edits():
    Increment = Annotated[int, C.AfterValidator(lambda x: x + 1)]

    class Child(C.Config):
        x: Increment = 1

    class Root(C.Config):
        x: Increment = 1
        xs: list[Increment] = [1]
        ys: Annotated[list[int], C.AfterValidator(lambda xs: [x + 1 for x in xs])] = [1]
        child: Child = Child()
        derived: Increment = C.interp(lambda c: c.current(Root).x)

    draft = Root()
    final = draft.finalize()
    copied = final.copy()
    assert copied.x == final.x == 2
    assert copied.child.x == final.child.x == 2
    assert list(copied.xs) == list(copied.ys) == [2]
    assert copied.derived == final.derived == 3
    copied.xs.append(5)
    copied.ys.append(5)
    copied.x = 10
    assert copied.x == 11
    assert copied.derived == 3
    assert list(copied.xs) == list(copied.ys) == [2, 6]
    assert copied.finalize().copy().xs == [2, 6]
    assert final.copy().xs == [2]
    assert final.finalize().copy().child.x == 2


def test_captured_standalone_configs_retain_their_source_context():
    class Independent(C.Config):
        base: int = 4
        value: int = C.interp(lambda c: c.root(Independent).base)

    source = Independent()

    class Outer(C.Config):
        child: Independent = C.interp(lambda c: source)

    r = Outer()
    assert r.child.value == 4
    source.base = 8
    assert r.finalize().child.value == 8
    assert C.is_draft(source)


def test_undecorated_check_override_stays_suppressed_in_grandchildren():
    calls = []

    class Base(C.Config):
        @C.check
        def validate(self) -> None:
            calls.append("base")

    class Mid(Base):
        def validate(self) -> None:
            calls.append("mid")

    class Leaf(Mid):
        pass

    Base().finalize()
    Mid().finalize()
    Leaf().finalize()
    assert calls == ["base"]


def test_inherited_forward_annotations_use_the_declaring_module(monkeypatch):
    a, b = ModuleType("nshconfig_test_module_a"), ModuleType("nshconfig_test_module_b")
    monkeypatch.setitem(sys.modules, a.__name__, a)
    monkeypatch.setitem(sys.modules, b.__name__, b)
    exec(
        """
import nshconfig as C
class Base(C.Config):
    child: 'Child'
class Child(C.Config):
    x: int = 1
""",
        a.__dict__,
    )
    b.__dict__["Base"] = a.Base
    exec(
        """
import nshconfig as C
class Child(C.Config):
    x: str = 'wrong module'
class Sub(Base):
    own: Child = Child()
""",
        b.__dict__,
    )
    config = b.Sub(child=a.Child()).finalize()
    assert config.child.x == 1
    assert config.own.x == "wrong module"


def test_mutable_primitive_subclasses_cannot_enter_a_snapshot():
    class MutableInt(int):
        pass

    class Root(C.Config):
        value: Any

    value = MutableInt(1)
    value.items = []
    with pytest.raises(C.ConfigError, match="immutable leaf"):
        Root(value=value).finalize()


def test_managed_list_writes_can_repair_invalid_contents_without_reads():
    class Root(C.Config):
        xs: list[int] = [1]

    r = Root()
    alias = r.xs
    alias[0] = "invalid"
    alias.append(2)
    alias.extend([3])
    alias.insert(0, 4)
    with pytest.raises(C.ConfigError):
        _ = r.xs
    alias.clear()
    alias.append(5)
    assert list(r.xs) == [5]


def test_final_copy_keeps_inputs_when_container_normalizer_mutates_its_input():
    def increment(values: list[int]) -> list[int]:
        values[:] = [value + 1 for value in values]
        return values

    class Root(C.Config):
        xs: Annotated[list[int], C.AfterValidator(increment)] = [1]
        before: Annotated[list[int], C.BeforeValidator(increment)] = [1]
        derived: Annotated[list[int], C.BeforeValidator(increment)] = C.interp(
            lambda c: [1]
        )

    final = Root().finalize()
    assert list(final.xs) == list(final.derived) == [2]
    copied = final.copy()
    assert list(copied.xs) == list(copied.derived) == list(copied.before) == [2]
    copied.xs.append(4)
    assert list(copied.xs) == [2, 5]
