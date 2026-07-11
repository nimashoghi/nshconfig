"""Existing finals are transactionally isolated from validation hooks."""

from typing import Any, ClassVar

import pytest
from pydantic import ValidationError, model_validator

import nshconfig as C


class _MutatingBefore(C.Config):
    enabled: ClassVar[bool] = False
    fail: ClassVar[bool] = False
    items: list[int]

    @model_validator(mode="before")
    @classmethod
    def mutate_input(cls, value: Any) -> Any:
        if cls.enabled:
            value["items"].append(99)
        if cls.fail:
            raise ValueError("deliberate validator failure")
        return value


def test_root_revalidation_never_exposes_source_containers_to_model_before() -> None:
    source = _MutatingBefore(items=[1])
    _MutatingBefore.enabled = True
    try:
        with pytest.raises(ValidationError) as caught:
            C.finalize(source)
    finally:
        _MutatingBefore.enabled = False

    assert caught.value.errors()[0]["type"] == "nshconfig_revalidation_change"
    assert source.items == [1]


def test_failed_model_before_cannot_mutate_the_source_final() -> None:
    source = _MutatingBefore(items=[1])
    _MutatingBefore.enabled = True
    _MutatingBefore.fail = True
    try:
        with pytest.raises(ValidationError, match="deliberate validator failure"):
            C.finalize(source)
    finally:
        _MutatingBefore.enabled = False
        _MutatingBefore.fail = False

    assert source.items == [1]


def test_nested_reuse_is_isolated_and_must_be_idempotent() -> None:
    class Parent(C.Config):
        child: _MutatingBefore

    source = _MutatingBefore(items=[1])
    _MutatingBefore.enabled = True
    try:
        with pytest.raises(ValidationError) as caught:
            Parent(child=source)
    finally:
        _MutatingBefore.enabled = False

    assert caught.value.errors()[0]["type"] == "nshconfig_revalidation_change"
    assert source.items == [1]


def test_revalidation_preserves_fields_set_at_every_config_node() -> None:
    class MetadataChild(C.Config):
        mutate_metadata: ClassVar[bool] = False
        value: int = 1

        @model_validator(mode="after")
        def mutate_fields_set(self) -> "MetadataChild":
            if type(self).mutate_metadata:
                self.__pydantic_fields_set__.add("value")
            return self

    class MetadataRoot(C.Config):
        child: MetadataChild

    source = MetadataRoot(child=MetadataChild())
    assert source.child.model_fields_set == set()

    MetadataChild.mutate_metadata = True
    try:
        with pytest.raises(ValidationError) as caught:
            C.finalize(source)
    finally:
        MetadataChild.mutate_metadata = False

    assert caught.value.errors()[0]["type"] == "nshconfig_revalidation_change"
    assert source.child.model_fields_set == set()


def test_nested_non_idempotent_field_validation_is_rejected() -> None:
    from pydantic import field_validator

    class IncrementingChild(C.Config):
        value: int

        @field_validator("value")
        @classmethod
        def increment(cls, value: int) -> int:
            return value + 1

    class Parent(C.Config):
        child: IncrementingChild

    source = IncrementingChild(value=1)
    assert source.value == 2
    with pytest.raises(ValidationError) as caught:
        Parent(child=source)

    assert caught.value.errors()[0]["type"] == "nshconfig_revalidation_change"
    assert source.value == 2
