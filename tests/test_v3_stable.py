"""Regressions from the stable v3 release review."""

from __future__ import annotations

import subprocess
import sys
from typing import Annotated, Any

import pytest

import nshconfig as C


@pytest.mark.parametrize("kind", ["list", "config", "nested"])
def test_normalizers_cannot_introduce_nodes_through_tuples(kind: str):
    class Child(C.Config):
        value: int = 1

    child = Child()
    values = {"list": ([1],), "config": (child,), "nested": (([1],),)}
    Normalized = Annotated[Any, C.BeforeValidator(lambda value: values[kind])]

    class Run(C.Config):
        value: Normalized

    draft = Run(value=1)
    with pytest.raises(C.ConfigError, match="cannot introduce"):
        _ = draft.value
    with pytest.raises(C.ConfigError, match="cannot introduce"):
        draft.finalize()
    child.value = 2


@pytest.mark.parametrize("future", ["", "from __future__ import annotations"])
def test_class_annotations_and_inline_validators(future: str):
    script = (
        future
        + """
from typing import Annotated, ClassVar, ClassVar as CV
import __main__
import typing_extensions
import nshconfig as C

Value = str

class Outer(C.Config):
    Value: ClassVar[type] = int
    alias: CV[str] = "alias"
    quoted: "ClassVar[str]" = "quoted"
    extension: typing_extensions.ClassVar[str] = "extension"
    unresolved: CV["DefinedLater"] = None

    class Child(C.Config):
        value: int = 3

    child: Child = Child()
    value: Value = 1
    increment: Annotated[int, C.AfterValidator(lambda value: value + 1)] = 1

assert Outer.alias == "alias"
assert Outer.quoted == "quoted"
assert Outer.extension == "extension"
assert Outer.unresolved is None
draft = Outer()
assert draft.alias == "alias"
assert draft.finalize().to_dict() == {
    "child": {"value": 3}, "value": 1, "increment": 2,
}

class Child(Outer):
    pass

assert Child().finalize().to_dict() == draft.finalize().to_dict()

class LateRun(C.Config):
    child: "__main__.Later"

class Later(C.Config):
    value: int = 4

assert LateRun(child=Later()).finalize().child.value == 4
"""
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_explicit_rebuild_namespace_overrides_class_names():
    class Run(C.Config):
        Value = str
        value: "Value"

    Run.rebuild(namespace={"Value": int})
    assert Run(value=1).finalize().value == 1


def test_postponed_validator_retains_function_locals():
    increment = 2

    class Run(C.Config):
        value: Annotated[int, C.AfterValidator(lambda value: value + increment)] = 1

    assert Run().finalize().value == 3
