"""Run-record reconstruction cannot be downgraded through validator context."""

from collections.abc import Mapping
from typing import Any, ClassVar

import pytest
from pydantic import Field, ValidationInfo, model_validator

import nshconfig as C


class _ContextAttack(C.Config):
    attack: ClassVar[bool] = False
    resolver_calls: ClassVar[int] = 0
    value: int

    @classmethod
    def _resolve(cls, context: C.Context) -> int:
        del context
        cls.resolver_calls += 1
        return 7

    @model_validator(mode="before")
    @classmethod
    def replace_record_value(cls, value: Any, info: ValidationInfo) -> Any:
        if not cls.attack:
            return value
        if isinstance(info.context, Mapping):
            # This was the old, spoofable capability. A record validation must
            # never expose a mutable flag that user code can downgrade.
            info.context["nshconfig_record_load"] = False
        value["value"] = C.interp(cls._resolve)
        return value


def test_record_round_trip_rejects_injected_interpolation_without_executing_it() -> (
    None
):
    final = _ContextAttack(value=7)
    _ContextAttack.attack = True
    _ContextAttack.resolver_calls = 0
    try:
        with pytest.raises(C.FingerprintError, match="cannot reconstruct"):
            C.record(final)
    finally:
        _ContextAttack.attack = False

    assert _ContextAttack.resolver_calls == 0


def test_load_record_uses_a_private_non_spoofable_capability() -> None:
    final = _ContextAttack(value=7)
    run_record = C.record(final)

    _ContextAttack.attack = True
    _ContextAttack.resolver_calls = 0
    try:
        with pytest.raises(C.RecordError, match="interpolation|reconstruct"):
            C.load_record(_ContextAttack, run_record)
    finally:
        _ContextAttack.attack = False

    assert _ContextAttack.resolver_calls == 0


class _DroppedStoredField(C.Config):
    attack: ClassVar[bool] = False
    factory_calls: ClassVar[int] = 0
    value: int = Field(default_factory=lambda: _DroppedStoredField._factory())

    @classmethod
    def _factory(cls) -> int:
        cls.factory_calls += 1
        return 11

    @model_validator(mode="before")
    @classmethod
    def drop_value(cls, value: Any) -> Any:
        if cls.attack:
            value = dict(value)
            value.pop("value", None)
        return value


def test_record_field_removal_fails_before_default_factory_side_effects() -> None:
    final = _DroppedStoredField(value=7)
    run_record = C.record(final)
    _DroppedStoredField.attack = True
    _DroppedStoredField.factory_calls = 0
    try:
        with pytest.raises(C.RecordError, match="substituted defaults"):
            C.load_record(_DroppedStoredField, run_record)
    finally:
        _DroppedStoredField.attack = False

    assert _DroppedStoredField.factory_calls == 0
