"""The interpolation value: ``interp()`` markers, and the ``Ctx`` they resolve against.

An ``Interp`` marker is an ordinary VALUE (see V2_CORE.md section 3): nothing special
happens at class definition. It is legal anywhere a value sits (a draft assignment, a
``model_validate`` input dict, a class default, ``Field(default=...)``) and resolves
through one rule inside the validation pass (see ``scope.py``).
"""

from __future__ import annotations

import difflib
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, TypeVar, cast

from typing_extensions import override

from pydantic import BaseModel

__all__ = ["Ctx", "Interp", "interp"]

T = TypeVar("T")

# stack entry: (model class, in-progress input dict, key label in parent or None)
if TYPE_CHECKING:
    StackEntry = tuple[type[BaseModel], dict[str, Any], "str | None"]
    Stack = tuple[StackEntry, ...]

# Read-log recorder: while a marker's fn runs, _View reads append (dotted path, value
# repr) here so provenance can answer "derived BECAUSE x = v". Inactive (None) outside
# resolution; activated by the scope validator.
_READS: ContextVar[list[tuple[str, str]] | None] = ContextVar("nshconfig_reads", default=None)


class Interp:
    """Runtime marker: this value is interpolated from the config tree at validation.

    Stateless after construction (fn + captured source site), so one instance can
    occupy a class default slot and any number of instance slots concurrently.
    ``==`` is identity (raising would break container membership); ``bool()`` and
    f-string formatting raise so a pending marker cannot silently leak into data.
    """

    __slots__ = ("fn", "site")

    def __init__(self, fn: Callable[[Ctx], Any]):
        self.fn = fn
        code = getattr(fn, "__code__", None)
        name = getattr(fn, "__name__", type(fn).__name__)
        # Plain string captured NOW: survives pickling, citable on a cluster.
        self.site = f"{name} @ {code.co_filename}:{code.co_firstlineno}" if code else name

    @override
    def __repr__(self) -> str:
        name, _, loc = self.site.partition(" @ ")
        if loc:
            file, _, line = loc.rpartition(":")
            return f"interp(<{name} @ {Path(file).name}:{line}>)"
        return f"interp(<{name}>)"

    def __bool__(self) -> bool:
        from .errors import DraftError

        raise DraftError(f"refusing to use pending {self!r} in a boolean context")

    @override
    def __format__(self, spec: str) -> str:
        from .errors import DraftError

        raise DraftError(f"refusing to format pending {self!r} into a string")


def interp(fn: Callable[[Ctx], T]) -> T:
    """Mark a field value as interpolated from the surrounding config tree.

    Returns an ``Interp`` marker at runtime; typed as ``T`` (the same contained lie
    as pydantic's ``Field()``) so basedpyright checks the lambda's return type
    against the field at every use site.
    """
    return cast(T, Interp(fn))


class _View:
    """Read access to one stack level: resolved/provided input first, class defaults second."""

    __slots__ = ("_cls", "_data", "_path")

    def __init__(self, cls: type[BaseModel], data: dict[str, Any], path: str = ""):
        object.__setattr__(self, "_cls", cls)
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "_path", path)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        f = self._cls.__pydantic_fields__.get(name)
        if f is None:
            hint = difflib.get_close_matches(name, self._cls.__pydantic_fields__, n=1)
            raise AttributeError(
                f"{self._cls.__name__} has no field {name!r}"
                + (f"; did you mean {hint[0]!r}?" if hint else "")
            )
        if name in self._data:
            v = self._data[name]
            if isinstance(v, Interp):
                raise AttributeError(
                    f"{self._cls.__name__}.{name} is itself pending interpolation ({v!r}) "
                    "(possible cycle; set a concrete value, or point both at the same "
                    "concrete source)"
                )
        elif isinstance(f.default, Interp):
            raise AttributeError(
                f"{self._cls.__name__}.{name} is itself interpolated and not filled yet"
            )
        elif not f.is_required():
            data = {k: v for k, v in self._data.items() if not isinstance(v, Interp)}
            v = f.get_default(call_default_factory=True, validated_data=data)
        else:
            raise AttributeError(
                f"{self._cls.__name__}.{name}: not provided and no usable default"
            )
        dotted = f"{self._path}.{name}" if self._path else name
        ann = f.annotation
        if isinstance(v, dict) and isinstance(ann, type) and issubclass(ann, BaseModel):
            return _View(ann, v, dotted)
        if (reads := _READS.get()) is not None:
            reads.append((dotted, repr(v)[:60]))
        return v

    @override
    def __repr__(self) -> str:
        return f"<view {self._cls.__name__} {self._data!r}>"


class Ctx:
    """What an interp lambda sees. All navigations return attribute-access views."""

    __slots__ = ("_stack",)

    def __init__(self, stack: Stack):
        self._stack = stack

    @property
    def data(self) -> Any:
        """This model's own level (earlier-declared markers already resolved)."""
        cls, data, _ = self._stack[-1]
        return _View(cls, data, _dotted_prefix(self._stack))

    @property
    def parent(self) -> Any:
        """One level up (Hydra's ``${..x}``); ancestor frames are always resolved."""
        if len(self._stack) < 2:
            raise AttributeError("no parent: this model is the validation root")
        cls, data, _ = self._stack[-2]
        return _View(cls, data, _dotted_prefix(self._stack[:-1]))

    @property
    def root(self) -> Any:
        """The validation root (Hydra's ``${a.b}``); raw input plus class defaults."""
        cls, data, _ = self._stack[0]
        return _View(cls, data, "")

    def nearest(self, cls: type) -> Any:
        """The nearest enclosing instance of ``cls`` (ancestors only, nominal)."""
        for i in range(len(self._stack) - 2, -1, -1):
            c, d, _ = self._stack[i]
            if issubclass(c, cls):
                return _View(c, d, _dotted_prefix(self._stack[: i + 1]))
        chain = " > ".join(c.__name__ for c, _, _ in self._stack)
        raise AttributeError(f"no enclosing {cls.__name__} (ancestors here: {chain})")


def _dotted_prefix(stack: Stack) -> str:
    return ".".join(lbl for _, _, lbl in stack if lbl is not None)
