"""Python interpolation markers and the read-only validation context."""

import difflib
import keyword
from collections.abc import Iterator, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar, cast, overload

from pydantic import BaseModel

from .state import is_path_part
from typing_extensions import override

from .errors import DraftError

__all__ = ["Context", "interp"]

T = TypeVar("T")
M = TypeVar("M", bound=BaseModel)
PathPart = str | int


@dataclass(frozen=True)
class _ModelPath:
    path: tuple[PathPart, ...]


_MODEL_PATHS: ContextVar[dict[int, _ModelPath] | None] = ContextVar(
    "nshconfig_interpolation_model_paths", default=None
)


@dataclass(frozen=True, slots=True, eq=False)
class Interp:
    """A pending callable stored as a field value until Pydantic validates it."""

    fn: "Callable[[Context], Any]"
    site: str = field(init=False)

    def __post_init__(self) -> None:
        code = getattr(self.fn, "__code__", None)
        name = getattr(
            self.fn,
            "__qualname__",
            getattr(self.fn, "__name__", type(self.fn).__name__),
        )
        object.__setattr__(
            self,
            "site",
            f"{name} @ {code.co_filename}:{code.co_firstlineno}"
            if code is not None
            else name,
        )

    @override
    def __repr__(self) -> str:
        name, separator, location = self.site.partition(" @ ")
        if not separator:
            return f"interp(<{name}>)"
        filename, _, line = location.rpartition(":")
        return f"interp(<{name} @ {Path(filename).name}:{line}>)"

    def __bool__(self) -> bool:
        raise DraftError(f"pending {self!r} cannot be used as a boolean")

    @override
    def __format__(self, format_spec: str) -> str:
        raise DraftError(f"pending {self!r} cannot be formatted")


def interp(fn: "Callable[[Context], T]") -> T:
    """Derive one complete Config field value from its validation context."""
    if not callable(fn):
        raise TypeError("interp() requires a callable")
    return cast(T, Interp(fn))


def _render_path(parts: Sequence[PathPart]) -> str:
    rendered = ""
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        elif part.isidentifier() and not keyword.iskeyword(part):
            rendered += ("." if rendered else "") + part
        else:
            rendered += f"[{part!r}]"
    return rendered or "<root>"


def _safe_repr(value: Any, *, limit: int = 80) -> str:
    try:
        rendered = repr(value)
    except Exception:
        rendered = f"<{type(value).__qualname__}>"
    if len(rendered) <= limit:
        return rendered
    return f"{rendered[: max(0, limit - 3)]}..."


def _path_part(value: Any) -> PathPart:
    return value if is_path_part(value) else _safe_repr(value)


def _publish(
    value: Any,
    path: tuple[PathPart, ...],
) -> Any:
    if isinstance(value, BaseModel):
        from .config import Config

        if isinstance(value, Config):
            _register_model(value, path)
        return value
    if type(value) in {dict, list, tuple, set, frozenset}:
        return _ContainerView(value, path)
    return value


def _register_model(value: BaseModel, path: tuple[PathPart, ...]) -> None:
    """Associate a concrete Config with its canonical path for transparent reads."""

    model_paths = _MODEL_PATHS.get()
    if model_paths is not None:
        model_paths[id(value)] = _ModelPath(path)


def record_model_field_read(model: BaseModel, name: str, value: Any) -> Any:
    """Apply read-only views to nested values read during interpolation."""
    model_paths = _MODEL_PATHS.get()
    if model_paths is None or (model_path := model_paths.get(id(model))) is None:
        return value
    path = (*model_path.path, name)
    return _publish(value, path)


class _ContainerView:
    """Read-only container access with ordinary Python operations."""

    __slots__ = ("_path", "_value")

    def __init__(
        self,
        value: dict[Any, Any] | list[Any] | tuple[Any, ...] | set[Any] | frozenset[Any],
        path: tuple[PathPart, ...],
    ):
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_path", path)

    @staticmethod
    def _unwrap_other(value: Any) -> Any:
        return value._value if isinstance(value, _ContainerView) else value

    def _publish_result(self, value: Any) -> Any:
        return _publish(value, self._path)

    def __getitem__(self, key: Any) -> Any:
        value = self._value[key]
        return _publish(
            value,
            (*self._path, _path_part(key)),
        )

    def __iter__(self) -> Iterator[Any]:
        if type(self._value) is dict:
            return iter(self._value)
        return (
            _publish(
                value,
                (*self._path, index),
            )
            for index, value in enumerate(self._value)
        )

    def __contains__(self, item: Any) -> bool:
        return item in self._value

    def __len__(self) -> int:
        return len(self._value)

    def __bool__(self) -> bool:
        return bool(self._value)

    def get(self, key: Any, default: Any = None) -> Any:
        if type(self._value) is not dict:
            raise AttributeError("get")
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self) -> Iterator[Any]:
        if type(self._value) is not dict:
            raise AttributeError("keys")
        return iter(tuple(self._value.keys()))

    def values(self) -> Iterator[Any]:
        if type(self._value) is not dict:
            raise AttributeError("values")
        return (
            self._publish_mapping_value(key, value)
            for key, value in tuple(self._value.items())
        )

    def items(self) -> Iterator[tuple[Any, Any]]:
        if type(self._value) is not dict:
            raise AttributeError("items")
        return (
            (key, self._publish_mapping_value(key, value))
            for key, value in tuple(self._value.items())
        )

    def _publish_mapping_value(self, key: Any, value: Any) -> Any:
        return _publish(
            value,
            (*self._path, _path_part(key)),
        )

    def index(self, value: Any, start: int = 0, stop: int | None = None) -> int:
        if type(self._value) is dict:
            raise AttributeError("index")
        length = len(self._value)
        normalized_start, normalized_stop, _ = slice(start, stop).indices(length)
        for index in range(normalized_start, normalized_stop):
            if self[index] == value:
                return index
        raise ValueError(f"{value!r} is not in {type(self._value).__name__}")

    def count(self, value: Any) -> int:
        if type(self._value) is dict:
            raise AttributeError("count")
        return sum(item == value for item in self)

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(
            f"{name!r} is unavailable on the read-only interpolation container view"
        )

    @override
    def __eq__(self, other: object) -> bool:
        return self._value == self._unwrap_other(other)

    @override
    def __ne__(self, other: object) -> bool:
        return not self == other

    def __add__(self, other: Any) -> Any:
        return self._publish_result(self._value + self._unwrap_other(other))

    def __radd__(self, other: Any) -> Any:
        return self._publish_result(self._unwrap_other(other) + self._value)

    def __mul__(self, other: Any) -> Any:
        return self._publish_result(self._value * other)

    def __rmul__(self, other: Any) -> Any:
        return self._publish_result(other * self._value)

    def __or__(self, other: Any) -> Any:
        return self._publish_result(self._value | self._unwrap_other(other))

    def __ror__(self, other: Any) -> Any:
        return self._publish_result(self._unwrap_other(other) | self._value)

    @override
    def __repr__(self) -> str:
        return repr(self._value)

    @override
    def __str__(self) -> str:
        return str(self._value)

    @override
    def __format__(self, format_spec: str) -> str:
        return format(self._value, format_spec)


class _FrameView:
    __slots__ = ("_frame",)

    def __init__(self, frame: Any):
        object.__setattr__(self, "_frame", frame)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        frame = object.__getattribute__(self, "_frame")
        fields = frame.cls.__pydantic_fields__
        if name not in fields:
            hint = difflib.get_close_matches(name, fields, n=1)
            suffix = f"; did you mean {hint[0]!r}?" if hint else ""
            raise AttributeError(f"{frame.cls.__name__} has no field {name!r}{suffix}")
        if name not in frame.values:
            child = frame.active_child
            if child is not None and child.path == (*frame.path, name):
                return _FrameView(child)
            path = _render_path((*frame.path, name))
            raise AttributeError(
                f"{path} has not been validated yet; declare the source field or "
                "completed branch before the field that reads it"
            )
        return _publish(
            frame.values[name],
            (*frame.path, name),
        )

    @override
    def __repr__(self) -> str:
        frame = object.__getattribute__(self, "_frame")
        return f"<validation view of {frame.cls.__name__}>"


class Context:
    """Read-only access to canonical values already validated in this config tree."""

    __slots__ = ("_stack",)

    def __init__(self, stack: tuple[Any, ...]):
        self._stack = stack

    @overload
    def current(self) -> Any: ...

    @overload
    def current(self, cls: type[M]) -> M: ...

    def current(self, cls: type[M] | None = None) -> Any:
        """Return the model whose field is currently being validated."""
        return self._view_at(len(self._stack) - 1, cls)

    @overload
    def parent(self) -> Any: ...

    @overload
    def parent(self, levels_or_cls: type[M]) -> M: ...

    @overload
    def parent(self, levels_or_cls: int) -> Any: ...

    @overload
    def parent(self, levels_or_cls: int, cls: type[M]) -> M: ...

    def parent(
        self, levels_or_cls: int | type[M] = 1, cls: type[M] | None = None
    ) -> Any:
        """Return an ancestor by exact hop count, optionally checking its class."""
        if isinstance(levels_or_cls, int):
            levels = levels_or_cls
            expected = cls
        else:
            if cls is not None:
                raise AttributeError("parent(Model) does not accept a second class")
            levels = 1
            expected = levels_or_cls
        if levels < 1:
            raise AttributeError("parent() levels must be at least 1")
        index = len(self._stack) - 1 - levels
        if index < 0:
            raise AttributeError(
                f"no ancestor exists {levels} level(s) above this model"
            )
        return self._view_at(index, expected)

    @overload
    def root(self) -> Any: ...

    @overload
    def root(self, cls: type[M]) -> M: ...

    def root(self, cls: type[M] | None = None) -> Any:
        """Return the root model of this validation session."""
        return self._view_at(0, cls)

    def nearest(self, cls: type[M]) -> M:
        """Return the nearest enclosing ancestor compatible with ``cls``."""
        for frame in reversed(self._stack[:-1]):
            if issubclass(frame.cls, cls):
                return cast(M, _FrameView(frame))
        chain = " > ".join(frame.cls.__name__ for frame in self._stack)
        raise AttributeError(f"no enclosing {cls.__name__}; active models: {chain}")

    def _view_at(self, index: int, cls: type[M] | None) -> Any:
        frame = self._stack[index]
        if cls is not None and not issubclass(frame.cls, cls):
            raise AttributeError(
                f"expected {cls.__name__}, found {frame.cls.__name__} at that context level"
            )
        return _FrameView(frame)


def unwrap_view(value: Any) -> Any:
    """Materialize a result without leaking proxies or source-owned containers."""
    return _materialize_result(value, "<result>", set(), {})


def _materialize_result(
    value: Any,
    path: str,
    active: set[int],
    memo: dict[int, Any],
) -> Any:
    if isinstance(value, _ContainerView):
        return _materialize_container(value._value, path, active, memo)
    if isinstance(value, _FrameView):
        raise AttributeError(
            "an active Config branch is not a completed value while it is still "
            "being validated; select one of its already validated fields instead"
        )
    if isinstance(value, BaseModel):
        return value
    if type(value) in {dict, list, tuple, set, frozenset}:
        return _materialize_container(value, path, active, memo)
    return value


def _materialize_container(
    value: Any,
    path: str,
    active: set[int],
    memo: dict[int, Any],
) -> Any:
    identity = id(value)
    if identity in active:
        raise AttributeError(f"interpolation result contains a cycle at {path}")
    if identity in memo:
        return memo[identity]
    active.add(identity)
    try:
        if type(value) is dict:
            output: dict[Any, Any] = {}
            memo[identity] = output
            for key, item in value.items():
                copied_key = _materialize_result(key, f"{path}.<key>", active, memo)
                copied_item = _materialize_result(
                    item, f"{path}[{_safe_repr(key)}]", active, memo
                )
                output[copied_key] = copied_item
            return output
        if type(value) is list:
            output_list: list[Any] = []
            memo[identity] = output_list
            output_list.extend(
                _materialize_result(item, f"{path}[{index}]", active, memo)
                for index, item in enumerate(value)
            )
            return output_list
        if type(value) is tuple:
            output_tuple = tuple(
                _materialize_result(item, f"{path}[{index}]", active, memo)
                for index, item in enumerate(value)
            )
            memo[identity] = output_tuple
            return output_tuple
        if type(value) is set:
            output_set: set[Any] = set()
            memo[identity] = output_set
            output_set.update(
                _materialize_result(item, f"{path}[{index}]", active, memo)
                for index, item in enumerate(value)
            )
            return output_set
        assert type(value) is frozenset
        output_frozen = frozenset(
            _materialize_result(item, f"{path}[{index}]", active, memo)
            for index, item in enumerate(value)
        )
        memo[identity] = output_frozen
        return output_frozen
    finally:
        active.remove(identity)
