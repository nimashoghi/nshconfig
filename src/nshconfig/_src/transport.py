"""Pickle hardening for the notebook-to-cluster transport (V2_CORE.md section 8).

pydantic-core's ``SchemaValidator``/``SchemaSerializer`` ``__reduce__`` passes the
LIVE, cross-class-shared core schema dict as a constructor argument. Notebook-defined
``Config`` subclasses are cloudpickled BY VALUE, and an interp lambda's
``__globals__`` typically forward-references sibling model classes; with such
reference cycles, pickle can invoke that constructor while a shared sub-dict is
still partially materialized (observed: ``SchemaError ... KeyError: 'function'``).
Fix: never construct during unpickling; build the real object lazily on first
attribute access. Properly an upstream pydantic fix; shipped here as a contained shim.
"""

import copyreg
from typing import Any

from pydantic_core import SchemaSerializer, SchemaValidator
from typing_extensions import override

__all__ = ["install"]


class _LazyValSer:
    def __init__(self, factory: Any, args: tuple[Any, ...]):
        self._factory = factory
        self._args = args
        self._obj: Any = None

    def __getattr__(self, name: str) -> Any:  # only fires for missing attrs
        if self.__dict__.get("_obj") is None:
            self.__dict__["_obj"] = self._factory(*self._args)
        return getattr(self.__dict__["_obj"], name)

    @override
    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return (_LazyValSer, (self._factory, self._args))


def _reduce_valser(obj: Any) -> tuple[Any, tuple[Any, ...]]:
    factory, args, *_ = obj.__reduce__()
    return (_LazyValSer, (factory, args))


def install() -> None:
    copyreg.pickle(SchemaValidator, _reduce_valser)
    copyreg.pickle(SchemaSerializer, _reduce_valser)


install()
