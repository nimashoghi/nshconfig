"""Config-local lazy reconstruction of Pydantic core validators and serializers."""

from threading import Lock
from typing import Any

from pydantic_core import SchemaSerializer, SchemaValidator
from typing_extensions import override

__all__ = ["defer_config_val_sers"]


class _DeferredValSer:
    """Delay core-schema construction until a restored class is complete.

    Cloudpickle restores a notebook-defined class incrementally.  A Config core
    schema contains callbacks that refer back to that class, so constructing its
    validator while the class dictionary is only partially restored can observe
    an incomplete schema.  Keeping the live core object behind this class is
    transparent during normal use; its pickle reduction carries the constructor
    inputs but deliberately postpones the constructor call.
    """

    __slots__ = ("_args", "_factory", "_lock", "_value")

    def __init__(
        self,
        value: SchemaValidator | SchemaSerializer | None,
        factory: Any = None,
        args: tuple[Any, ...] | None = None,
    ) -> None:
        self._value = value
        self._factory = factory
        self._args = args
        self._lock = Lock()

    def _materialize(self) -> SchemaValidator | SchemaSerializer:
        value = self._value
        if value is not None:
            return value
        with self._lock:
            value = self._value
            if value is None:
                assert self._factory is not None, "deferred core factory is missing"
                assert self._args is not None, "deferred core arguments are missing"
                value = self._factory(*self._args)
                self._value = value
                self._factory = None
                self._args = None
        return value

    def __getattr__(self, name: str) -> Any:
        return getattr(self._materialize(), name)

    @override
    def __reduce__(self) -> tuple[Any, tuple[Any, tuple[Any, ...]]]:
        value = self._value
        if value is None:
            factory = self._factory
            args = self._args
        else:
            reduced = value.__reduce__()
            factory, args = reduced[:2]
        assert factory is not None, "core pickle factory is missing"
        assert isinstance(args, tuple), "core pickle arguments must be a tuple"
        return _restore_deferred_val_ser, (factory, args)


def _restore_deferred_val_ser(
    factory: Any,
    args: tuple[Any, ...],
) -> _DeferredValSer:
    return _DeferredValSer(None, factory, args)


def defer_config_val_sers(cls: type[Any]) -> None:
    """Wrap one completed Config class without changing global pickle behavior."""

    validator = cls.__dict__.get("__pydantic_validator__")
    if isinstance(validator, SchemaValidator):
        cls.__pydantic_validator__ = _DeferredValSer(validator)

    serializer = cls.__dict__.get("__pydantic_serializer__")
    if isinstance(serializer, SchemaSerializer):
        cls.__pydantic_serializer__ = _DeferredValSer(serializer)
