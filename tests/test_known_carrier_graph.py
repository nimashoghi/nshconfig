"""Known Python carrier objects cannot conceal pending lifecycle values."""

import functools
import operator
import weakref
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

import nshconfig as C


class _Child(C.Config):
    value: int


class _MethodCarrier:
    def __init__(self, hidden: Any) -> None:
        self.hidden = hidden

    def method(self) -> Any:
        return self.hidden


class _PartialSubclass(functools.partial[Any]):
    pass


class _WeakrefSubclass(weakref.ref[Any]):
    pass


def _closure_carrier(hidden: Any) -> Callable[[], Any]:
    def carry() -> Any:
        return hidden

    return carry


def _default_carrier(hidden: Any) -> Callable[..., Any]:
    def carry(value: Any = hidden) -> Any:
        return value

    return carry


@pytest.mark.parametrize(
    "make_carrier",
    [
        _closure_carrier,
        _default_carrier,
        lambda hidden: _MethodCarrier(hidden).method,
        lambda hidden: functools.partial(lambda value: value, hidden),
        lambda hidden: _PartialSubclass(lambda value: value, hidden),
        lambda hidden: weakref.ref(hidden),
        lambda hidden: _WeakrefSubclass(hidden),
        lambda hidden: weakref.WeakMethod(hidden.model_dump),
        lambda hidden: [hidden].append,
        lambda hidden: property(_closure_carrier(hidden)),
        lambda hidden: staticmethod(_closure_carrier(hidden)),
        lambda hidden: classmethod(_closure_carrier(hidden)),
        lambda hidden: functools.partialmethod(lambda value: value, hidden),
        lambda hidden: operator.itemgetter(hidden),
        lambda hidden: operator.methodcaller("consume", hidden),
        lambda hidden: type("ClassCarrier", (), {"hidden": hidden}),
    ],
)
def test_known_carriers_cannot_hide_a_draft(
    make_carrier: Callable[[Any], Any],
) -> None:
    class Root(C.Config, arbitrary_types_allowed=True):
        payload: Any

    pending = C.draft(_Child)
    carrier = make_carrier(pending)
    with pytest.raises(ValidationError) as caught:
        Root(payload=carrier)
    assert caught.value.errors()[0]["type"] == "nshconfig_pending_draft"


def test_function_carriers_without_pending_state_are_still_valid_runtime_values() -> (
    None
):
    class Root(C.Config, arbitrary_types_allowed=True):
        payload: Any

    carrier = _closure_carrier(7)
    final = Root(payload=carrier)
    assert final.payload() == 7
    with pytest.raises(
        C.FingerprintError,
        match="cannot be serialized deterministically|durable semantic representation",
    ):
        C.fingerprint(final)


def test_truly_opaque_callable_is_rejected_conservatively() -> None:
    class OpaqueCallable:
        __slots__ = ()

        def __call__(self) -> int:
            return 1

    class Root(C.Config, arbitrary_types_allowed=True):
        payload: Any

    with pytest.raises(ValidationError) as caught:
        Root(payload=OpaqueCallable())
    assert caught.value.errors()[0]["type"] == "nshconfig_opaque_callable"
