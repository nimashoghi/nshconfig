"""Owned drafts, on-demand field resolution, and independent final snapshots."""

from __future__ import annotations

import ast
import copy
import enum
import inspect
import json
from collections.abc import (
    Callable,
    Iterable,
    Iterator,
    Mapping,
    MutableMapping,
    MutableSequence,
)
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import (
    Path,
    PosixPath,
    PurePath,
    PurePosixPath,
    PureWindowsPath,
    WindowsPath,
)
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar, cast
from uuid import UUID

from pydantic import Field, TypeAdapter, ValidationError
from pydantic_core import PydanticUndefined, core_schema
from typing_extensions import Self, dataclass_transform, override

from . import schema

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., None])


class ConfigError(ValueError):
    """A configuration lifecycle or resolution error."""


class MissingValueError(ConfigError):
    """A requested required field has not been assigned."""


class InterpolationError(ConfigError):
    """An interpolation has no context or has a dependency cycle."""


class OwnershipError(ConfigError):
    """A config node would have multiple owners or create a structural cycle."""


class FrozenError(ConfigError):
    """A completed or computed config value was mutated."""


@dataclass(frozen=True)
class _Interpolation:
    fn: Callable[[Context], Any]

    @override
    def __repr__(self) -> str:
        return f"interp({getattr(self.fn, '__qualname__', repr(self.fn))})"


def interp(fn: Callable[[Context], T]) -> T:
    """Declare a typed field computation, evaluated against its owning tree."""
    if not callable(fn):
        raise TypeError("interp requires a callable")
    return cast(T, _Interpolation(fn))


def check(fn: F) -> F:
    """Mark an instance method as a check-only finalization validator."""
    setattr(fn, "__nshconfig_check__", True)
    return fn


@dataclass
class _Resolution:
    roots: list[Config] = field(default_factory=list)
    active: list[tuple[Config, str]] = field(default_factory=list)
    values: dict[tuple[int, str], Any] = field(default_factory=dict)
    finalizing: bool = False
    token: object = field(default_factory=object)
    nodes: dict[int, Config] = field(default_factory=dict)
    inputs: dict[tuple[int, str], Any] = field(default_factory=dict)


_SESSION: ContextVar[_Resolution | None] = ContextVar(
    "nshconfig_resolution", default=None
)


class Context:
    """Typed selectors for the config containing an interpolation."""

    def __init__(self, node: Config):
        self._node = node

    def root(self, cls: type[T]) -> T:
        """Select the tree root, requiring the requested schema type."""
        root = _root(self._node)
        if not isinstance(root, cls):
            raise InterpolationError(
                f"{_path(self._node)}: expected {cls.__name__} root, found {type(root).__name__}"
            )
        return root

    def nearest(self, cls: type[T]) -> T:
        """Select the nearest enclosing Config of this type (excluding self)."""
        node = self._node._parent
        while node is not None:
            if isinstance(node, Config) and isinstance(node, cls):
                return node
            node = node._parent
        raise InterpolationError(f"{_path(self._node)}: no enclosing {cls.__name__}")

    def current(self, cls: type[T]) -> T:
        """Select the config containing this field, requiring its schema type."""
        if not isinstance(self._node, cls):
            raise InterpolationError(
                f"expected {cls.__name__}, found {type(self._node).__name__}"
            )
        return self._node


def _root(node: Any) -> Any:
    while node._parent is not None:
        node = node._parent
    return node


def _path(node: Any) -> str:
    parts: list[str] = []
    while node._parent is not None:
        parent = node._parent
        parts.append(
            f".{node._key}" if isinstance(parent, Config) else f"[{node._key!r}]"
        )
        node = parent
    return type(node).__name__ + "".join(reversed(parts))


def _mutable(node: Any) -> None:
    if node._frozen:
        raise FrozenError(f"{_path(node)} is read-only; copy it to edit")
    session = _SESSION.get()
    if session is not None and any(_root(node) is root for root in session.roots):
        raise FrozenError(
            "interpolation and validation may only read their config tree"
        )


def _field_key(key: Any) -> str:
    while isinstance(key, tuple):
        key = key[0]
    return key


def _touch(node: Any) -> None:
    while node._parent is not None:
        parent = node._parent
        if isinstance(parent, Config):
            parent._canonical.pop(_field_key(node._key), None)
        node = parent


def _node(value: Any) -> bool:
    return isinstance(value, (Config, _List, _Dict))


def _edges(value: Any) -> Iterator[Any]:
    if _node(value):
        yield value
    elif isinstance(value, tuple):
        for item in value:
            yield from _edges(item)


def _install(parent: Any, pairs: list[tuple[Any, Any]], old: Iterable[Any]) -> None:
    """Check the entire attachment before changing any ownership metadata."""
    old_nodes = {id(node): node for value in old for node in _edges(value)}
    planned: list[tuple[Any, Any, Any]] = []
    seen: set[int] = set()
    ancestors: set[int] = set()
    ancestor = parent
    while ancestor is not None:
        ancestors.add(id(ancestor))
        ancestor = ancestor._parent

    def visit(value: Any, owner: Any, key: Any) -> None:
        if isinstance(value, tuple):
            for index, item in enumerate(value):
                visit(item, owner, (key, index))
            return
        if not _node(value):
            return
        if id(value) in seen or id(value) in ancestors:
            raise OwnershipError(
                "a config/container may have one owner; use .copy() for reuse"
            )
        seen.add(id(value))
        if value._parent is not None and not (
            owner is parent and value._parent is parent and id(value) in old_nodes
        ):
            raise OwnershipError(
                f"{_path(value)} already has an owner; use .copy() for reuse"
            )
        planned.append((value, owner, key))
        # Already assembled children keep their own ownership. Only unbound
        # container imports have descendants that still need attachment.
        if isinstance(value, (_List, _Dict)) and value._unbound:
            for child_key, item in value._pairs():
                visit(item, value, child_key)

    for key, value in pairs:
        visit(value, parent, key)
    for identity, node in old_nodes.items():
        if identity not in seen:
            node._parent = None
            node._key = None
    for node, owner, key in planned:
        node._parent = owner
        node._key = key
        if isinstance(node, (_List, _Dict)):
            node._unbound = False


def _prepare(value: Any, active: set[int] | None = None) -> Any:
    if _node(value) or isinstance(value, _Interpolation):
        return value
    if type(value) not in (list, dict, tuple):
        return value
    active = set() if active is None else active
    if id(value) in active:
        raise OwnershipError("cyclic containers are not config values")
    active.add(id(value))
    try:
        if type(value) is list:
            return _List([_prepare(item, active) for item in value])
        if type(value) is dict:
            return _Dict({key: _prepare(item, active) for key, item in value.items()})
        return tuple(_prepare(item, active) for item in value)
    finally:
        active.remove(id(value))


class _Container:
    _parent: Any = None
    _key: Any = None
    _frozen: bool = False
    _unbound: bool = True

    def _ensure(self) -> None:
        node: Any = self
        while node._parent is not None:
            parent = node._parent
            if isinstance(parent, Config):
                parent._read(_field_key(node._key))
                return
            node = parent

    def _pairs(self) -> list[tuple[Any, Any]]:
        raise NotImplementedError


class _List(_Container, MutableSequence[Any]):
    def __init__(self, items: list[Any]):
        self._items = items
        self._view = items

    @override
    def _pairs(self) -> list[tuple[Any, Any]]:
        return list(enumerate(self._items))

    @override
    def __len__(self) -> int:
        self._ensure()
        return len(self._view)

    @override
    def __getitem__(self, index: Any) -> Any:
        self._ensure()
        return self._view[index]

    def _replace(self, items: list[Any]) -> None:
        _mutable(self)
        _install(self, list(enumerate(items)), self._items)
        self._items = items
        self._view = items
        _touch(self)

    @override
    def __setitem__(self, index: Any, value: Any) -> None:
        items = self._items.copy()
        items[index] = (
            [_prepare(v) for v in value]
            if isinstance(index, slice)
            else _prepare(value)
        )
        self._replace(items)

    @override
    def __delitem__(self, index: Any) -> None:
        items = self._items.copy()
        del items[index]
        self._replace(items)

    @override
    def insert(self, index: int, value: Any) -> None:
        items = self._items.copy()
        items.insert(index, _prepare(value))
        self._replace(items)

    @override
    def append(self, value: Any) -> None:
        self._replace([*self._items, _prepare(value)])

    @override
    def clear(self) -> None:
        self._replace([])

    @override
    def extend(self, values: Iterable[Any]) -> None:
        self._replace([*self._items, *(_prepare(v) for v in values)])

    @override
    def reverse(self) -> None:
        self._replace(list(reversed(self._items)))

    def sort(self, *, key: Any = None, reverse: bool = False) -> None:
        self._ensure()
        order = sorted(
            range(len(self._items)),
            key=lambda i: key(self._view[i]) if key else self._view[i],
            reverse=reverse,
        )
        self._replace([self._items[i] for i in order])

    def copy(self) -> list[Any]:
        return list(self)

    @override
    def __iter__(self) -> Iterator[Any]:
        self._ensure()
        return iter(self._view)

    def __add__(self, other: Any) -> list[Any]:
        return list(self) + list(other)

    def __radd__(self, other: Any) -> list[Any]:
        return list(other) + list(self)

    def __mul__(self, count: int) -> list[Any]:
        return list(self) * count

    __rmul__ = __mul__

    def __imul__(self, count: int) -> Self:
        self._replace(self._items * count)
        return self

    @override
    def __eq__(self, other: object) -> bool:
        return list(self) == (list(other) if isinstance(other, _List) else other)

    @override
    def __repr__(self) -> str:
        return repr(self._items)


class _Dict(_Container, MutableMapping[Any, Any]):
    def __init__(self, items: dict[Any, Any]):
        self._items = items
        self._view = items

    @override
    def _pairs(self) -> list[tuple[Any, Any]]:
        return list(self._items.items())

    @override
    def __getitem__(self, key: Any) -> Any:
        self._ensure()
        return self._view[key]

    def _replace(self, items: dict[Any, Any]) -> None:
        _mutable(self)
        _install(self, list(items.items()), self._items.values())
        self._items = items
        self._view = items
        _touch(self)

    @override
    def __setitem__(self, key: Any, value: Any) -> None:
        self._replace({**self._items, key: _prepare(value)})

    @override
    def __delitem__(self, key: Any) -> None:
        items = self._items.copy()
        del items[key]
        self._replace(items)

    @override
    def __iter__(self) -> Iterator[Any]:
        self._ensure()
        return iter(self._view)

    @override
    def __len__(self) -> int:
        self._ensure()
        return len(self._view)

    @override
    def update(self, *args: Any, **kwargs: Any) -> None:
        incoming = dict(*args, **kwargs)
        self._replace({**self._items, **{k: _prepare(v) for k, v in incoming.items()}})

    @override
    def clear(self) -> None:
        self._replace({})

    def copy(self) -> dict[Any, Any]:
        return dict(self.items())

    def __or__(self, other: Any) -> dict[Any, Any]:
        return self.copy() | dict(other)

    def __ror__(self, other: Any) -> dict[Any, Any]:
        return dict(other) | self.copy()

    def __ior__(self, other: Any) -> Self:
        self.update(other)
        return self

    @override
    def __repr__(self) -> str:
        return repr(self._items)


def _input(value: Any, *, canonical: bool = False) -> Any:
    if isinstance(value, (_List, _Dict)) and canonical:
        value._ensure()
    if isinstance(value, _List):
        items = value._view if canonical else value._items
        return [_input(item, canonical=canonical) for item in items]
    if isinstance(value, _Dict):
        items = value._view if canonical else value._items
        return {key: _input(item, canonical=canonical) for key, item in items.items()}
    if isinstance(value, (list, tuple)):
        items = (_input(item, canonical=canonical) for item in value)
        return tuple(items) if isinstance(value, tuple) else list(items)
    if isinstance(value, dict):
        return {key: _input(item, canonical=canonical) for key, item in value.items()}
    return value


def _canonical(raw: Any, value: Any, updates: list[tuple[Any, Any]]) -> Any:
    """Publish normalized leaves without destroying editable container identity."""
    if isinstance(raw, _List):
        if not isinstance(value, list) or len(raw._items) != len(value):
            raise ConfigError(
                "container normalization must preserve shape; use interp to compute a different container"
            )
        view = [_canonical(a, b, updates) for a, b in zip(raw._items, value)]
        updates.append((raw, view))
        return raw
    if isinstance(raw, _Dict):
        if not isinstance(value, dict) or raw._items.keys() != value.keys():
            raise ConfigError(
                "container normalization must preserve keys; use interp to compute a different mapping"
            )
        view = {
            key: _canonical(item, value[key], updates)
            for key, item in raw._items.items()
        }
        updates.append((raw, view))
        return raw
    if isinstance(raw, Config) and raw is not value:
        raise ConfigError("field normalization cannot replace Config nodes; use interp")
    if isinstance(raw, tuple):
        if not isinstance(value, tuple) or len(raw) != len(value):
            raise ConfigError("tuple normalization must preserve shape")
        return tuple(_canonical(a, b, updates) for a, b in zip(raw, value))
    if isinstance(value, (Config, list, dict)) and value is not raw:
        raise ConfigError(
            "normalizers cannot introduce config/container nodes; assign them directly or use interp"
        )
    return value


def _safe_leaf(value: Any) -> Any:
    immutable = (
        str,
        bytes,
        int,
        float,
        complex,
        bool,
        PurePath,
        Path,
        PosixPath,
        WindowsPath,
        PurePosixPath,
        PureWindowsPath,
        date,
        datetime,
        time,
        timedelta,
        Decimal,
        UUID,
        range,
    )
    if value is None or type(value) in immutable or isinstance(value, type):
        return value
    if isinstance(value, enum.Enum):
        _safe_leaf(value.value)
        return value
    if type(value) is tuple:
        return tuple(_safe_leaf(item) for item in value)
    if type(value) is frozenset:
        return frozenset(_safe_leaf(item) for item in value)
    raise ConfigError(
        f"{type(value).__name__} is not a supported immutable leaf; "
        "describe mutable state with Config, list, dict, or tuple"
    )


@dataclass_transform(kw_only_default=True, field_specifiers=(Field,))
class Config:
    """An annotated schema whose instances are editable drafts until finalized."""

    _declarations: ClassVar[dict[str, schema.Declaration]] = {}
    _namespace: ClassVar[dict[str, Any]] = {}
    _checks: ClassVar[tuple[str, ...]] = ()
    _parent: Any
    _key: Any
    _frozen: bool
    _values: dict[str, Any]
    _canonical: dict[str, Any]
    _copy_inputs: dict[str, Any]
    _creation_token: object | None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        declarations: dict[str, schema.Declaration] = {}
        checks: dict[str, None] = {}
        namespace: dict[str, Any] = {}
        for base in reversed(cls.__mro__[1:]):
            declarations.update(getattr(base, "_declarations", {}))
            checks.update(dict.fromkeys(getattr(base, "_checks", ())))
            # Each annotation retains its declaring namespace, not a subclass namespace.
        frame = inspect.currentframe()
        if frame is not None and frame.f_back is not None:
            # Capture annotation names, not the frame or unrelated notebook locals.
            for annotation in schema.class_annotations(cls).values():
                if isinstance(annotation, str):
                    expressions = [annotation]
                    while expressions:
                        expression = ast.parse(expressions.pop(), mode="eval")
                        for node in ast.walk(expression):
                            if isinstance(node, ast.Name):
                                if node.id in frame.f_back.f_locals:
                                    namespace[node.id] = frame.f_back.f_locals[node.id]
                                elif node.id in frame.f_back.f_globals:
                                    namespace[node.id] = frame.f_back.f_globals[node.id]
                            elif isinstance(node, ast.Constant) and isinstance(
                                node.value, str
                            ):
                                try:
                                    ast.parse(node.value, mode="eval")
                                except SyntaxError:
                                    pass
                                else:
                                    expressions.append(node.value)
        del frame
        for name, annotation in schema.class_annotations(cls).items():
            if name.startswith("_") or schema.is_classvar(annotation):
                continue
            if hasattr(Config, name):
                raise TypeError(f"{name!r} is reserved by Config")
            declarations[name] = schema.Declaration(
                annotation, cls.__dict__.get(name, PydanticUndefined), cls
            )
            if name in cls.__dict__:
                delattr(cls, name)
        for name, value in cls.__dict__.items():
            if name in checks:
                del checks[name]
            if getattr(value, "__nshconfig_check__", False):
                checks[name] = None
        cls._declarations = declarations
        cls._namespace = namespace
        cls._checks = tuple(
            name
            for name in checks
            if getattr(getattr(cls, name), "__nshconfig_check__", False)
        )

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
        # A Config-typed field checks the node type without completing its tree.
        return core_schema.is_instance_schema(cls)

    def __init__(self, **values: Any) -> None:
        unknown = values.keys() - type(self)._declarations.keys()
        if unknown:
            raise TypeError(f"unknown fields: {', '.join(sorted(unknown))}")
        self._initialize()
        prepared: list[tuple[str, Any]] = []
        for name, declaration in type(self)._declarations.items():
            if name in values:
                value = values[name]
            else:
                value = _clone(schema.default(declaration))
            if value is not PydanticUndefined:
                prepared.append((name, _prepare(value)))
        _install(self, prepared, [])
        self._values.update(prepared)

    def _initialize(self) -> None:
        object.__setattr__(self, "_values", {})
        object.__setattr__(self, "_canonical", {})
        object.__setattr__(self, "_copy_inputs", {})
        session = _SESSION.get()
        object.__setattr__(self, "_creation_token", session.token if session else None)
        object.__setattr__(self, "_parent", None)
        object.__setattr__(self, "_key", None)
        object.__setattr__(self, "_frozen", False)

    @classmethod
    def draft(cls) -> Self:
        """Create an editable config, permitting every required field to be unset."""
        return cls()

    # Checkers see closed annotated fields, not runtime attribute interception.
    if not TYPE_CHECKING:

        @override
        def __getattribute__(self, name: str) -> Any:
            if not name.startswith("_") and name in type(self)._declarations:
                return self._read(name)
            return object.__getattribute__(self, name)

        @override
        def __setattr__(self, name: str, value: Any) -> None:
            if name.startswith("_"):
                object.__setattr__(self, name, value)
                return
            if name not in type(self)._declarations:
                raise AttributeError(f"{type(self).__name__} has no field {name!r}")
            self._assign(name, value)

        @override
        def __delattr__(self, name: str) -> None:
            if name not in type(self)._declarations:
                raise AttributeError(name)
            _mutable(self)
            old = self._values.get(name, PydanticUndefined)
            _install(self, [], [old])
            self._values.pop(name, None)
            self._canonical.pop(name, None)
            _touch(self)

    def _assign(self, name: str, value: Any) -> None:
        _mutable(self)
        old = self._values.get(name, PydanticUndefined)
        if value is old:
            return
        value = _prepare(value)
        _install(self, [(name, value)], [old])
        self._values[name] = value
        self._canonical.pop(name, None)
        _touch(self)

    def _read(self, name: str) -> Any:
        if self._frozen:
            return self._values[name]
        session = _SESSION.get()
        if session is not None:
            return self._resolve(name, session)
        session = _Resolution(roots=[_root(self)])
        token = _SESSION.set(session)
        try:
            return self._resolve(name, session)
        finally:
            _SESSION.reset(token)

    def _resolve(self, name: str, session: _Resolution) -> Any:
        session.nodes[id(self)] = self
        root = _root(self)
        if not any(root is known for known in session.roots):
            session.roots.append(root)
        key = (id(self), name)
        if key in session.values:
            return session.values[key]
        if name in self._canonical:
            return self._canonical[name]
        for node, active_name in session.active:
            if node is self and active_name == name:
                chain = " -> ".join(
                    f"{_path(n)}.{f}" for n, f in [*session.active, (self, name)]
                )
                raise InterpolationError(f"interpolation dependency cycle: {chain}")
        raw = self._values.get(name, PydanticUndefined)
        if raw is PydanticUndefined:
            raise MissingValueError(f"{_path(self)}.{name} is required but unset")
        session.active.append((self, name))
        try:
            computed = isinstance(raw, _Interpolation)
            value = raw.fn(Context(self)) if computed else _input(raw)
            validation_input = _input(value, canonical=computed)
            session.inputs[key] = _input(validation_input)
            value = schema.adapter(type(self), name).validate_python(validation_input)
            if computed:
                value = _computed(value, self, name, session)
            else:
                updates: list[tuple[Any, Any]] = []
                value = _canonical(raw, value, updates)
                for container, view in updates:
                    container._view = view
                self._canonical[name] = value
            session.values[key] = value
            return value
        except ValidationError as exc:
            raise ConfigError(f"{_path(self)}.{name}: {exc}") from exc
        finally:
            session.active.pop()

    def copy(self) -> Self:
        """Copy this subtree to an unattached draft, preserving unresolved rules."""
        return cast(Self, _clone(self))

    @classmethod
    def rebuild(cls, *, namespace: Mapping[str, Any] | None = None) -> None:
        """Resolve late annotations; supply locals for function-local forward types."""
        if namespace:
            cls._namespace.update(namespace)
        schema.resolve(cls)
        schema.clear_cache(cls)

    @override
    def __getstate__(self) -> dict[str, Any]:
        schema.resolve(type(self))
        return {**self.__dict__, "_canonical": {}}

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)

    def __copy__(self) -> Self:
        return self.copy()

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        result = self.copy()
        memo[id(self)] = result
        return result

    def finalize(self) -> Self:
        """Resolve and check an independent recursively read-only snapshot."""
        if _SESSION.get() is not None:
            raise ConfigError("finalize cannot run inside interpolation or validation")
        session = _Resolution(roots=[_root(self)], finalizing=True)
        token = _SESSION.set(session)
        try:
            result = cast(Self, _snapshot(self, session))
            _run_checks(result)
            return result
        finally:
            _SESSION.reset(token)

    def to_dict(self) -> dict[str, Any]:
        """Export a final snapshot as ordinary Python containers."""
        if not self._frozen:
            raise ConfigError("finalize before exporting a config")
        return {name: _plain(value) for name, value in self._values.items()}

    def to_json(self, *, indent: int | None = None) -> str:
        """Export a final snapshot as JSON using Pydantic scalar encodings."""
        data = TypeAdapter(dict[str, Any]).dump_python(self.to_dict(), mode="json")
        return json.dumps(data, indent=indent)

    @override
    def __repr__(self) -> str:
        state = "final" if self._frozen else "draft"
        values = ", ".join(f"{name}={value!r}" for name, value in self._values.items())
        return f"{type(self).__name__}.{state}({values})"

    __hash__: Any = None


def is_draft(value: object) -> bool:
    """Whether this value is an editable Config."""
    return isinstance(value, Config) and not value._frozen


def _clone(value: Any) -> Any:
    if isinstance(value, Config):
        output = object.__new__(type(value))
        output._initialize()
        inputs = value._copy_inputs if value._frozen else value._values
        for name, item in inputs.items():
            output._assign(name, _clone(item))
        return output
    if isinstance(value, (_List, list)):
        items = value._items if isinstance(value, _List) else value
        return [_clone(item) for item in items]
    if isinstance(value, (_Dict, dict)):
        items = value._items if isinstance(value, _Dict) else value
        return {key: _clone(item) for key, item in items.items()}
    if isinstance(value, tuple):
        return tuple(_clone(item) for item in value)
    if isinstance(value, _Interpolation) or value is PydanticUndefined:
        return value
    return copy.deepcopy(value)


def _capture_input(raw: Any, completed: Any) -> Any:
    """Keep concrete validation inputs so copying a final cannot normalize twice.

    Config references point into the independent snapshot, never back to the
    authoring tree. Interpolation callbacks have already become concrete inputs.
    """
    if isinstance(completed, (_List, _Dict)):
        completed = _input(completed)
    if isinstance(raw, Config):
        if not isinstance(completed, Config):
            raise ConfigError("normalization cannot replace Config nodes")
        return completed
    if isinstance(raw, (list, tuple, _List)):
        if not isinstance(completed, (list, tuple, _List)) or len(raw) != len(
            completed
        ):
            raise ConfigError("container normalization must preserve shape")
        values = [_capture_input(a, b) for a, b in zip(raw, completed)]
        return tuple(values) if isinstance(raw, tuple) else values
    if isinstance(raw, (dict, _Dict)):
        if not isinstance(completed, (dict, _Dict)) or raw.keys() != completed.keys():
            raise ConfigError("container normalization must preserve keys")
        return {
            copy.deepcopy(key): _capture_input(item, completed[key])
            for key, item in raw.items()
        }
    return copy.deepcopy(raw)


def _snapshot(value: Any, session: _Resolution) -> Any:
    if isinstance(value, Config):
        result = object.__new__(type(value))
        result._initialize()
        for name in type(value)._declarations:
            resolved = (
                value._values[name] if value._frozen else value._resolve(name, session)
            )
            result._assign(name, _snapshot(resolved, session))
            raw_input = (
                value._copy_inputs[name]
                if value._frozen
                else session.inputs.get((id(value), name), _input(value._values[name]))
            )
            result._copy_inputs[name] = _capture_input(raw_input, result._values[name])
        result._frozen = True
        _freeze_containers(result)
        return result
    if isinstance(value, (_List, list)):
        items = value._view if isinstance(value, _List) else value
        return [_snapshot(item, session) for item in items]
    if isinstance(value, (_Dict, dict)):
        items = value._view if isinstance(value, _Dict) else value
        return {
            _safe_leaf(key): _snapshot(item, session) for key, item in items.items()
        }
    if isinstance(value, tuple):
        return tuple(_snapshot(item, session) for item in value)
    return _safe_leaf(value)


def _freeze_containers(node: Any) -> None:
    values = (
        node._values.values()
        if isinstance(node, Config)
        else node._items.values()
        if isinstance(node, _Dict)
        else node._items
    )
    for value in values:
        for child in _edges(value):
            if isinstance(child, (_List, _Dict)):
                child._frozen = True
                _freeze_containers(child)


def _computed_snapshot(
    value: Any, owner: Config, name: str, session: _Resolution
) -> Any:
    if isinstance(value, Config):
        if value._parent is not None or value._creation_token is not session.token:
            return _snapshot(value, session)
        temporary = _clone(value)
        temporary._parent, temporary._key = owner, name
        try:
            return _snapshot(temporary, session)
        finally:
            temporary._parent, temporary._key = None, None
    if isinstance(value, (list, _List)):
        return [_computed_snapshot(item, owner, name, session) for item in value]
    if isinstance(value, (dict, _Dict)):
        return {
            _safe_leaf(key): _computed_snapshot(item, owner, name, session)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_computed_snapshot(item, owner, name, session) for item in value)
    return _safe_leaf(value)


def _computed(value: Any, owner: Config, name: str, session: _Resolution) -> Any:
    # Existing nodes retain their source context; newly produced nodes bind to
    # the destination. Only the resolved copies become the computed value.
    result = _prepare(_computed_snapshot(value, owner, name, session))
    _install(owner, [(name, result)], [])
    for node in _edges(result):
        node._parent, node._key = None, None
        node._frozen = True
        _freeze_containers(node)
    if not session.finalizing:
        _run_checks(result)
    return result


def _run_checks(value: Any) -> None:
    if isinstance(value, Config):
        for item in value._values.values():
            _run_checks(item)
        for name in type(value)._checks:
            result = getattr(value, name)()
            if result is not None:
                raise ConfigError(
                    f"{type(value).__name__}.{name} must return None; checks cannot rewrite values"
                )
    elif isinstance(value, (_List, list, tuple)):
        for item in value:
            _run_checks(item)
    elif isinstance(value, (_Dict, dict)):
        for item in value.values():
            _run_checks(item)


def _plain(value: Any) -> Any:
    if isinstance(value, Config):
        return value.to_dict()
    if isinstance(value, (_List, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (_Dict, dict)):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_plain(item) for item in value)
    return value
