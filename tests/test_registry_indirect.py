from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Annotated, Literal

import pydantic
import pytest

import nshconfig as C


def test_manual_rebuild_fail():
    class PropBase(C.Config, ABC):
        @abstractmethod
        def my_method(self) -> None: ...

    registry = C.Registry(
        PropBase, discriminator="type", config={"auto_rebuild": False}
    )

    @registry.rebuild_on_registers
    class Root(C.Config):
        a: int = 1
        my_prop: list[Annotated[PropBase, registry.DynamicResolution()]]

    @registry.register
    class Prop1(PropBase):
        type: Literal["prop1"] = "prop1"

        def my_method(self) -> None:
            pass

    Root(my_prop=[Prop1()]).model_dump()

    class Prop2(PropBase):
        type: Literal["prop2"] = "prop2"

        def my_method(self) -> None:
            pass

    with pytest.raises(pydantic.ValidationError):
        Root(my_prop=[Prop2()]).model_dump()


def test_manual_rebuild():
    class PropBase(C.Config, ABC):
        @abstractmethod
        def my_method(self) -> None: ...

    registry = C.Registry(
        PropBase, discriminator="type", config={"auto_rebuild": False}
    )

    @registry.rebuild_on_registers
    class Root(C.Config):
        a: int = 1
        my_prop: list[Annotated[PropBase, registry.DynamicResolution()]]

    @registry.register
    class Prop1(PropBase):
        type: Literal["prop1"] = "prop1"

        def my_method(self) -> None:
            pass

    Root(my_prop=[Prop1()]).model_dump()

    @registry.register
    class Prop2(PropBase):
        type: Literal["prop2"] = "prop2"

        def my_method(self) -> None:
            pass

    # No raise
    Root(my_prop=[Prop2()]).model_dump()


def test_auto_rebuild():
    class PropBase(C.Config, ABC):
        @abstractmethod
        def my_method(self) -> None: ...

    registry = C.Registry(PropBase, discriminator="type")

    class Root(C.Config):
        a: int = 1
        my_prop: list[Annotated[PropBase, registry.DynamicResolution()]]

    @registry.register
    class Prop1(PropBase):
        type: Literal["prop1"] = "prop1"

        def my_method(self) -> None:
            pass

    Root(my_prop=[Prop1()]).model_dump()

    @registry.register
    class Prop2(PropBase):
        type: Literal["prop2"] = "prop2"

        def my_method(self) -> None:
            pass

    # No raise
    Root(my_prop=[Prop2()]).model_dump()
