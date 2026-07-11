"""Python interpolation markers and the read-only validation context."""

import difflib
import keyword
from collections.abc import (
    AsyncIterable,
    Awaitable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from contextvars import ContextVar
from dataclasses import dataclass, field, is_dataclass
from enum import Enum
from pathlib import Path
from threading import get_ident
from typing import TYPE_CHECKING, Any, Callable, TypeVar, cast, overload

from pydantic import BaseModel

from .state import is_path_part
from typing_extensions import override

from .errors import DraftError

if TYPE_CHECKING:
    from .config import Config

__all__ = ["Context", "interp"]

T = TypeVar("T")
if TYPE_CHECKING:
    M = TypeVar("M", bound=Config)
else:
    M = TypeVar("M", bound=BaseModel)
PathPart = str | int


@dataclass(frozen=True)
class _Read:
    """One temporary dependency edge, including its validation-path anchor."""

    path: tuple[PathPart, ...]
    value: str
    token: str | None
    anchor: tuple[PathPart, ...]
    relative: tuple[PathPart, ...]
    provisional: bool = False


_READS: ContextVar[list[_Read] | None] = ContextVar(
    "nshconfig_interpolation_reads", default=None
)


@dataclass(frozen=True)
class _ModelPath:
    path: tuple[PathPart, ...]
    anchor: tuple[PathPart, ...]
    relative: tuple[PathPart, ...]


_MODEL_PATHS: ContextVar[dict[int, _ModelPath] | None] = ContextVar(
    "nshconfig_interpolation_model_paths", default=None
)


@dataclass(slots=True)
class _InterpolationCapability:
    """Confine interpolation access to one live resolver on its owning thread."""

    owner_thread: int = field(default_factory=get_ident)
    active: bool = True

    def require(self) -> None:
        if not self.active:
            raise RuntimeError(
                "interpolation Context and views expire when their resolver returns"
            )
        if get_ident() != self.owner_thread:
            raise RuntimeError(
                "interpolation Context and views cannot cross thread boundaries"
            )


_CAPABILITY: ContextVar[_InterpolationCapability | None] = ContextVar(
    "nshconfig_interpolation_capability",
    default=None,
)


def _current_capability() -> _InterpolationCapability:
    capability = _CAPABILITY.get()
    if capability is None:
        raise RuntimeError("interpolation views exist only inside a live resolver")
    capability.require()
    return capability


def _require_view_capability(value: Any) -> None:
    capability = object.__getattribute__(value, "_capability")
    assert isinstance(capability, _InterpolationCapability)
    capability.require()


@dataclass(frozen=True, slots=True, eq=False)
class Interp:
    """A pending callable stored as a field value until Pydantic validates it."""

    fn: "Callable[[Context], Any]"
    site: str = field(init=False)

    def __post_init__(self) -> None:
        try:
            code = getattr(self.fn, "__code__", None)
        except Exception:
            code = None
        name: Any = None
        for attribute in ("__qualname__", "__name__"):
            try:
                name = getattr(self.fn, attribute)
            except Exception:
                continue
            if isinstance(name, str):
                break
        if not isinstance(name, str):
            name = type.__getattribute__(type(self.fn), "__name__")
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


def _record_read(
    path: tuple[PathPart, ...],
    value: Any,
    anchor: tuple[PathPart, ...],
    relative: tuple[PathPart, ...],
    *,
    provisional: bool = False,
) -> None:
    reads = _READS.get()
    if reads is None:
        return
    from .provenance import provenance_token, safe_repr

    reads.append(
        _Read(
            path,
            safe_repr(value, limit=80),
            provenance_token(value),
            anchor,
            relative,
            provisional,
        )
    )


def _publish(
    value: Any,
    path: tuple[PathPart, ...],
    anchor: tuple[PathPart, ...],
    relative: tuple[PathPart, ...],
    *,
    coarse: bool = False,
) -> Any:
    if isinstance(value, BaseModel):
        from .config import Config

        if isinstance(value, Config):
            if not coarse:
                _record_read(path, value, anchor, relative, provisional=True)
            _register_models(value, path, anchor, relative, set())
            return _ConfigView(value, path, anchor, relative, coarse=coarse)
        return _ObjectView(value, path, anchor, relative, coarse=coarse)
    if isinstance(value, Mapping):
        return _ContainerView(value, path, anchor, relative, coarse=coarse)
    if isinstance(value, (list, tuple)):
        return _ContainerView(value, path, anchor, relative, coarse=coarse)
    if not coarse:
        _record_read(path, value, anchor, relative)
    return value


def _register_models(
    value: Any,
    path: tuple[PathPart, ...],
    anchor: tuple[PathPart, ...],
    relative: tuple[PathPart, ...],
    active: set[int],
) -> None:
    """Associate concrete models with their canonical path for transparent reads."""
    model_paths = _MODEL_PATHS.get()
    if model_paths is None:
        return
    identity = id(value)
    if identity in active:
        return
    if isinstance(value, BaseModel):
        model_paths[identity] = _ModelPath(path, anchor, relative)
        return
    if not isinstance(value, (Mapping, list, tuple)):
        return
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if is_path_part(key):
                    part: PathPart = key
                else:
                    from .provenance import safe_repr

                    part = safe_repr(key)
                _register_models(
                    item,
                    (*path, part),
                    anchor,
                    (*relative, part),
                    active,
                )
        else:
            for index, item in enumerate(value):
                _register_models(
                    item,
                    (*path, index),
                    anchor,
                    (*relative, index),
                    active,
                )
    finally:
        active.remove(identity)


def record_model_field_read(model: BaseModel, name: str, value: Any) -> Any:
    """Record a declared field read on a concrete Config during interpolation."""
    model_paths = _MODEL_PATHS.get()
    if model_paths is None or (model_path := model_paths.get(id(model))) is None:
        return value
    path = (*model_path.path, name)
    relative = (*model_path.relative, name)
    return _publish(value, path, model_path.anchor, relative)


class _ObjectView:
    """Read-only declared-field access for ordinary Pydantic models."""

    __slots__ = ("_anchor", "_capability", "_coarse", "_path", "_relative", "_value")

    def __init__(
        self,
        value: BaseModel,
        path: tuple[PathPart, ...],
        anchor: tuple[PathPart, ...],
        relative: tuple[PathPart, ...],
        *,
        coarse: bool = False,
    ):
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_anchor", anchor)
        object.__setattr__(self, "_relative", relative)
        object.__setattr__(self, "_capability", _current_capability())
        object.__setattr__(self, "_coarse", coarse)

    @override
    def __getattribute__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(
                "interpolation views do not expose their backing runtime state"
            )
        _require_view_capability(self)
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str) -> Any:
        _require_view_capability(self)
        value = object.__getattribute__(self, "_value")
        if name.startswith("_"):
            raise AttributeError(
                f"{type(value).__name__} interpolation views expose declared fields only; "
                f"there is no declared field {name!r}"
            )
        fields = type(value).__pydantic_fields__
        if name not in fields:
            hint = difflib.get_close_matches(name, fields, n=1)
            suffix = f"; did you mean {hint[0]!r}?" if hint else ""
            raise AttributeError(
                f"{type(value).__name__} has no field {name!r}{suffix}"
            )
        coarse = object.__getattribute__(self, "_coarse")
        path = object.__getattribute__(self, "_path")
        relative = object.__getattribute__(self, "_relative")
        if not coarse:
            path = (*path, name)
            relative = (*relative, name)
        data = object.__getattribute__(value, "__dict__")
        if name not in data:
            raise AttributeError(f"{type(value).__name__} field {name!r} is not stored")
        return _publish(
            data[name],
            path,
            object.__getattribute__(self, "_anchor"),
            relative,
            coarse=coarse,
        )

    def _record_whole(self) -> None:
        _require_view_capability(self)
        if object.__getattribute__(self, "_coarse"):
            return
        _record_read(
            object.__getattribute__(self, "_path"),
            object.__getattribute__(self, "_value"),
            object.__getattribute__(self, "_anchor"),
            object.__getattribute__(self, "_relative"),
        )

    @staticmethod
    def _unwrap_other(value: Any) -> Any:
        if isinstance(value, _ObjectView):
            _require_view_capability(value)
            _ObjectView._record_whole(value)
            return object.__getattribute__(value, "_value")
        return value

    @override
    def __str__(self) -> str:
        _ObjectView._record_whole(self)
        return str(object.__getattribute__(self, "_value"))

    @override
    def __repr__(self) -> str:
        _require_view_capability(self)
        _ObjectView._record_whole(self)
        return repr(object.__getattribute__(self, "_value"))

    @override
    def __format__(self, format_spec: str) -> str:
        _ObjectView._record_whole(self)
        return format(object.__getattribute__(self, "_value"), format_spec)

    @override
    def __eq__(self, other: object) -> bool:
        _ObjectView._record_whole(self)
        return object.__getattribute__(self, "_value") == _ObjectView._unwrap_other(
            other
        )

    @override
    def __ne__(self, other: object) -> bool:
        return not self == other


class _ConfigView(_ObjectView):
    """Declared-field-only access to a completed nested Config branch."""

    __slots__ = ()

    @override
    def __repr__(self) -> str:
        _ObjectView._record_whole(self)
        value = object.__getattribute__(self, "_value")
        return f"<declared-field view of {type(value).__name__}>"

    @override
    def __str__(self) -> str:
        return repr(self)

    @override
    def __format__(self, format_spec: str) -> str:
        return format(str(self), format_spec)


class _ContainerView:
    """Read-only container access with dependency-aware Python operations."""

    __slots__ = ("_anchor", "_capability", "_coarse", "_path", "_relative", "_value")

    def __init__(
        self,
        value: Mapping[Any, Any] | list[Any] | tuple[Any, ...],
        path: tuple[PathPart, ...],
        anchor: tuple[PathPart, ...],
        relative: tuple[PathPart, ...],
        *,
        coarse: bool = False,
    ):
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_anchor", anchor)
        object.__setattr__(self, "_relative", relative)
        object.__setattr__(self, "_capability", _current_capability())
        object.__setattr__(self, "_coarse", coarse)

    @override
    def __getattribute__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(
                "interpolation container views do not expose their backing runtime state"
            )
        _require_view_capability(self)
        return object.__getattribute__(self, name)

    def _record_whole(self) -> None:
        _require_view_capability(self)
        if object.__getattribute__(self, "_coarse"):
            return
        _record_read(
            object.__getattribute__(self, "_path"),
            object.__getattribute__(self, "_value"),
            object.__getattribute__(self, "_anchor"),
            object.__getattribute__(self, "_relative"),
        )

    @staticmethod
    def _unwrap_other(value: Any) -> Any:
        if isinstance(value, _ContainerView):
            _require_view_capability(value)
            _ContainerView._record_whole(value)
            return object.__getattribute__(value, "_value")
        return value

    def __getitem__(self, key: Any) -> Any:
        _require_view_capability(self)
        container = object.__getattribute__(self, "_value")
        value = container[key]
        if isinstance(key, slice):
            _ContainerView._record_whole(self)
            indices = range(*key.indices(len(container)))
            published = [
                _publish_operation_part(self, container[index], index)
                for index in indices
            ]
            return tuple(published) if type(container) is tuple else published
        if not is_path_part(key):
            _ContainerView._record_whole(self)
            return _ContainerView._publish_mapping_value(self, key, value)
        canonical_key = key
        if type(container) in {list, tuple} and type(key) is int and key < 0:
            # A negative index is relative to the current sequence length.  Its
            # value can become stale after an append even when the normalized
            # positive element path itself is unchanged.
            _ContainerView._record_whole(self)
            canonical_key = len(container) + key
        return _publish_container_part(self, value, canonical_key)

    def __iter__(self) -> Iterator[Any]:
        container = object.__getattribute__(self, "_value")
        if isinstance(container, Mapping):
            _ContainerView._record_whole(self)
            return (_noncanonical_mapping_value(key) for key in tuple(container.keys()))
        _ContainerView._record_whole(self)
        return (
            _publish(
                value,
                (*object.__getattribute__(self, "_path"), index),
                object.__getattribute__(self, "_anchor"),
                (*object.__getattribute__(self, "_relative"), index),
            )
            for index, value in enumerate(container)
        )

    def __contains__(self, item: Any) -> bool:
        _ContainerView._record_whole(self)
        return item in object.__getattribute__(self, "_value")

    def __len__(self) -> int:
        _ContainerView._record_whole(self)
        return len(object.__getattribute__(self, "_value"))

    def __bool__(self) -> bool:
        _ContainerView._record_whole(self)
        return bool(object.__getattribute__(self, "_value"))

    def get(self, key: Any, default: Any = None) -> Any:
        if not isinstance(object.__getattribute__(self, "_value"), Mapping):
            raise AttributeError("get")
        try:
            return self[key]
        except KeyError:
            _ContainerView._record_whole(self)
            return default

    def keys(self) -> Iterator[Any]:
        container = object.__getattribute__(self, "_value")
        if not isinstance(container, Mapping):
            raise AttributeError("keys")
        _ContainerView._record_whole(self)
        return (_noncanonical_mapping_value(key) for key in tuple(container.keys()))

    def values(self) -> Iterator[Any]:
        container = object.__getattribute__(self, "_value")
        if not isinstance(container, Mapping):
            raise AttributeError("values")
        _ContainerView._record_whole(self)
        return (
            _ContainerView._publish_mapping_value(self, key, value)
            for key, value in tuple(container.items())
        )

    def items(self) -> Iterator[tuple[Any, Any]]:
        container = object.__getattribute__(self, "_value")
        if not isinstance(container, Mapping):
            raise AttributeError("items")
        _ContainerView._record_whole(self)
        return (
            (
                _noncanonical_mapping_value(key),
                _ContainerView._publish_mapping_value(self, key, value),
            )
            for key, value in tuple(container.items())
        )

    def _publish_mapping_value(self, key: Any, value: Any) -> Any:
        if not is_path_part(key):
            return _publish(
                value,
                object.__getattribute__(self, "_path"),
                object.__getattribute__(self, "_anchor"),
                object.__getattribute__(self, "_relative"),
                coarse=True,
            )
        return _publish_container_part(self, value, key)

    def index(self, value: Any, start: int = 0, stop: int | None = None) -> int:
        container = object.__getattribute__(self, "_value")
        if isinstance(container, Mapping):
            raise AttributeError("index")
        if start < 0 or (stop is not None and stop < 0):
            _ContainerView._record_whole(self)
        length = len(container)
        normalized_start, normalized_stop, _ = slice(start, stop).indices(length)
        for index in range(normalized_start, normalized_stop):
            if self[index] == value:
                return index
        raise ValueError(f"{value!r} is not in {type(container).__name__}")

    def count(self, value: Any) -> int:
        if isinstance(object.__getattribute__(self, "_value"), Mapping):
            raise AttributeError("count")
        return sum(item == value for item in self)

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(
            f"{name!r} is unavailable on the read-only interpolation container view"
        )

    @override
    def __eq__(self, other: object) -> bool:
        _ContainerView._record_whole(self)
        return object.__getattribute__(self, "_value") == _ContainerView._unwrap_other(
            other
        )

    @override
    def __ne__(self, other: object) -> bool:
        return not self == other

    def __add__(self, other: Any) -> Any:
        return _container_operation_value(self) + _container_operation_operand(other)

    def __radd__(self, other: Any) -> Any:
        return _container_operation_operand(other) + _container_operation_value(self)

    def __mul__(self, other: Any) -> Any:
        return _container_operation_value(self) * other

    def __rmul__(self, other: Any) -> Any:
        return other * _container_operation_value(self)

    def __or__(self, other: Any) -> Any:
        return _container_operation_value(self) | _container_operation_operand(other)

    def __ror__(self, other: Any) -> Any:
        return _container_operation_operand(other) | _container_operation_value(self)

    @override
    def __repr__(self) -> str:
        _ContainerView._record_whole(self)
        return repr(object.__getattribute__(self, "_value"))

    @override
    def __str__(self) -> str:
        _ContainerView._record_whole(self)
        return str(object.__getattribute__(self, "_value"))

    @override
    def __format__(self, format_spec: str) -> str:
        _ContainerView._record_whole(self)
        return format(object.__getattribute__(self, "_value"), format_spec)


def _publish_container_part(
    view: _ContainerView,
    value: Any,
    part: PathPart,
) -> Any:
    coarse = object.__getattribute__(view, "_coarse")
    path = object.__getattribute__(view, "_path")
    relative = object.__getattribute__(view, "_relative")
    if not coarse:
        path = (*path, part)
        relative = (*relative, part)
    return _publish(
        value,
        path,
        object.__getattribute__(view, "_anchor"),
        relative,
        coarse=coarse,
    )


def _noncanonical_mapping_value(value: Any) -> Any:
    return value


def _container_operation_value(view: _ContainerView) -> Any:
    """Copy a container as origin-preserving element proxies."""

    _ContainerView._record_whole(view)
    value = object.__getattribute__(view, "_value")
    if isinstance(value, Mapping):
        return {
            _noncanonical_mapping_value(key): (
                _publish_operation_part(view, item, key)
                if is_path_part(key)
                else _ContainerView._publish_mapping_value(view, key, item)
            )
            for key, item in value.items()
        }
    published = [
        _publish_operation_part(view, item, index) for index, item in enumerate(value)
    ]
    return tuple(published) if type(value) is tuple else published


def _container_operation_operand(value: Any) -> Any:
    if isinstance(value, _ContainerView):
        return _container_operation_value(value)
    return value


def _publish_operation_part(
    view: _ContainerView,
    value: Any,
    part: PathPart,
) -> Any:
    """Protect structured elements; the whole-container read covers scalar ones."""

    if isinstance(value, (BaseModel, Mapping, list, tuple)):
        return _publish_container_part(view, value, part)
    return value


class _FrameView:
    __slots__ = ("_capability", "_frame")

    def __init__(self, frame: Any):
        object.__setattr__(self, "_frame", frame)
        object.__setattr__(self, "_capability", _current_capability())

    @override
    def __getattribute__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(
                "validation views do not expose their backing runtime state"
            )
        _require_view_capability(self)
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str) -> Any:
        _require_view_capability(self)
        frame = object.__getattribute__(self, "_frame")
        if name.startswith("_"):
            raise AttributeError(
                f"{frame.cls.__name__} interpolation views expose declared fields only; "
                f"there is no declared field {name!r}"
            )
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
            frame.path,
            (name,),
        )

    @override
    def __repr__(self) -> str:
        _require_view_capability(self)
        frame = object.__getattribute__(self, "_frame")
        return f"<validation view of {frame.cls.__name__}>"


class Context:
    """Read-only access to canonical values already validated in this config tree."""

    __slots__ = ("_capability", "_stack")

    def __init__(self) -> None:
        raise TypeError("Context instances are created only while interp() is running")

    @override
    def __getattribute__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(
                "interpolation Context does not expose its validation stack"
            )
        _require_view_capability(self)
        return object.__getattribute__(self, name)

    @classmethod
    def _from_stack(
        cls,
        stack: tuple[Any, ...],
        capability: _InterpolationCapability,
    ) -> "Context":
        instance = object.__new__(cls)
        object.__setattr__(instance, "_stack", stack)
        object.__setattr__(instance, "_capability", capability)
        return instance

    @overload
    def current(self) -> Any: ...

    @overload
    def current(self, cls: type[M]) -> M: ...

    def current(self, cls: type[M] | None = None) -> Any:
        """Return the model whose field is currently being validated."""
        _require_view_capability(self)
        stack = object.__getattribute__(self, "_stack")
        return Context._view_at(self, len(stack) - 1, cls)

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
        _require_view_capability(self)
        if type(levels_or_cls) is int:
            levels = cast(int, levels_or_cls)
            expected = cls
        else:
            if cls is not None:
                raise AttributeError("parent(Model) does not accept a second class")
            levels = 1
            expected = cast(type[M], Context._config_class(levels_or_cls, "parent"))
        if levels < 1:
            raise AttributeError("parent() levels must be at least 1")
        stack = object.__getattribute__(self, "_stack")
        index = len(stack) - 1 - levels
        if index < 0:
            raise AttributeError(
                f"no ancestor exists {levels} level(s) above this model"
            )
        return Context._view_at(self, index, expected)

    @overload
    def root(self) -> Any: ...

    @overload
    def root(self, cls: type[M]) -> M: ...

    def root(self, cls: type[M] | None = None) -> Any:
        """Return the root model of this validation session."""
        _require_view_capability(self)
        return Context._view_at(self, 0, cls)

    def nearest(self, cls: type[M]) -> M:
        """Return the nearest enclosing ancestor compatible with ``cls``."""
        _require_view_capability(self)
        expected = Context._config_class(cls, "nearest")
        stack = object.__getattribute__(self, "_stack")
        for frame in reversed(stack[:-1]):
            if issubclass(frame.cls, expected):
                return cast(M, _FrameView(frame))
        chain = " > ".join(frame.cls.__name__ for frame in stack)
        raise AttributeError(
            f"no enclosing {expected.__name__}; active models: {chain}"
        )

    def _view_at(self, index: int, cls: type[M] | None) -> Any:
        _require_view_capability(self)
        frame = object.__getattribute__(self, "_stack")[index]
        expected = (
            None if cls is None else Context._config_class(cls, "context selector")
        )
        if expected is not None and not issubclass(frame.cls, expected):
            raise AttributeError(
                f"expected {expected.__name__}, found {frame.cls.__name__} at that context level"
            )
        return _FrameView(frame)

    @staticmethod
    def _config_class(value: Any, selector: str) -> type[BaseModel]:
        from .config import Config

        if not isinstance(value, type) or not issubclass(value, Config):
            raise TypeError(f"{selector}() expects a Config class")
        return value


def unwrap_view(value: Any) -> Any:
    """Materialize an interpolation result without leaking read proxies or aliases."""
    return _materialize_result(value, "<result>", set())


def _materialize_result(value: Any, path: str, active: set[int]) -> Any:
    if isinstance(value, _ObjectView):
        _require_view_capability(value)
        view_path = object.__getattribute__(value, "_path")
        view_value = object.__getattribute__(value, "_value")
        view_anchor = object.__getattribute__(value, "_anchor")
        view_relative = object.__getattribute__(value, "_relative")
        if not object.__getattribute__(value, "_coarse"):
            _record_read(view_path, view_value, view_anchor, view_relative)
        return view_value.model_copy(deep=True)
    if isinstance(value, _ContainerView):
        _require_view_capability(value)
        view_path = object.__getattribute__(value, "_path")
        view_value = object.__getattribute__(value, "_value")
        view_anchor = object.__getattribute__(value, "_anchor")
        view_relative = object.__getattribute__(value, "_relative")
        if not object.__getattribute__(value, "_coarse"):
            _record_read(view_path, view_value, view_anchor, view_relative)
        return _materialize_container(view_value, path, active)
    if isinstance(value, _FrameView):
        raise AttributeError(
            "an active Config branch is not a completed value while it is still "
            "being validated; select one of its already validated fields instead"
        )
    if isinstance(value, BaseModel):
        model_paths = _MODEL_PATHS.get()
        if (
            model_paths is not None
            and (model_path := model_paths.get(id(value))) is not None
        ):
            _record_read(
                model_path.path,
                value,
                model_path.anchor,
                model_path.relative,
            )
        return value
    if (
        isinstance(value, (Iterable, AsyncIterable, Awaitable))
        and type(value) not in {dict, frozenset, list, set, tuple}
        and type(value) not in {bytearray, bytes, memoryview, range, str}
        and not isinstance(value, (BaseModel, Enum))
        and not (is_dataclass(value) and not isinstance(value, type))
    ):
        raise AttributeError(
            f"interpolation returned unsupported or lazy container "
            f"{type(value).__qualname__}; materialize an exact built-in value in the callable"
        )
    if type(value) in {dict, list, tuple, set, frozenset}:
        return _materialize_container(value, path, active)
    return value


def _materialize_container(value: Any, path: str, active: set[int]) -> Any:
    identity = id(value)
    if identity in active:
        raise AttributeError(f"interpolation result contains a cycle at {path}")
    active.add(identity)
    try:
        if type(value) is dict:
            from .provenance import safe_repr

            return {
                _materialize_result(key, f"{path}.<key>", active): _materialize_result(
                    item, f"{path}[{safe_repr(key)}]", active
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                _materialize_result(item, f"{path}[{index}]", active)
                for index, item in enumerate(value)
            ]
        if isinstance(value, tuple):
            return tuple(
                _materialize_result(item, f"{path}[{index}]", active)
                for index, item in enumerate(value)
            )
        if isinstance(value, set):
            return {
                _materialize_result(item, f"{path}[{index}]", active)
                for index, item in enumerate(value)
            }
        if isinstance(value, frozenset):
            return frozenset(
                _materialize_result(item, f"{path}[{index}]", active)
                for index, item in enumerate(value)
            )
        raise AssertionError(
            "interpolation container materialization received a custom type"
        )
    finally:
        active.remove(identity)
