"""Side-effect-free, exact runtime snapshots for lifecycle integrity checks.

These snapshots are deliberately process-local.  They are used to prove that
validation and serialization did not change an existing final and that a JSON
round trip preserved every supported value distinction.  Stable record bytes
are a separate concern handled by :mod:`nshconfig._src.records`.
"""

import re
import struct
from array import array
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from functools import partial, partialmethod
from hashlib import sha256
from operator import attrgetter, itemgetter, methodcaller
from pathlib import (
    Path,
    PosixPath,
    PurePath,
    PurePosixPath,
    PureWindowsPath,
    WindowsPath,
)
from types import (
    BuiltinFunctionType,
    BuiltinMethodType,
    ClassMethodDescriptorType,
    FunctionType,
    GetSetDescriptorType,
    MemberDescriptorType,
    MethodType,
    MethodDescriptorType,
    MethodWrapperType,
    ModuleType,
    WrapperDescriptorType,
)
from typing import Any, cast
from uuid import UUID
from weakref import ReferenceType, WeakMethod
from zoneinfo import ZoneInfo

from pydantic import BaseModel

__all__ = [
    "inert_dataclass_state",
    "inert_object_state",
    "is_known_immutable_atom",
    "semantic_snapshot",
    "stable_semantic_digest",
]


def _slot_storage_name(owner: type, name: str) -> str:
    if name.startswith("__") and not name.endswith("__"):
        class_name = type.__getattribute__(owner, "__name__").lstrip("_")
        if class_name:
            return f"_{class_name}{name}"
    return name


@dataclass(frozen=True, slots=True)
class _SnapshotNode:
    """Unambiguous node in the internal semantic-snapshot grammar.

    Raw tuples are also valid runtime payloads (for example ``Path.parts``),
    so dispatching on a tuple's first string would confuse user data with our
    structural tags.  Keeping nodes nominal until they are digested makes the
    encoding prefix-free at every intermediate stage.
    """

    tag: str
    payload: tuple[Any, ...]


_OPERATOR_CARRIER_TYPES = {
    type(attrgetter("name")),
    type(itemgetter(0)),
    type(methodcaller("method")),
}


def inert_object_state(value: Any) -> tuple[tuple[Any, Any], ...]:
    """Enumerate physically distinct Python instance storage without user hooks."""
    if isinstance(value, (type, ModuleType)):
        return ()
    items: list[tuple[Any, Any]] = []
    if isinstance(value, FunctionType):
        items.append((("function", "code"), value.__code__))
        if value.__defaults__ is not None:
            items.append((("function", "defaults"), value.__defaults__))
        if value.__kwdefaults__ is not None:
            items.append((("function", "kwdefaults"), value.__kwdefaults__))
        if value.__closure__ is not None:
            closure: list[Any] = []
            for cell in value.__closure__:
                try:
                    closure.append(cell.cell_contents)
                except ValueError:
                    closure.append(("empty-cell",))
            items.append((("function", "closure"), tuple(closure)))
    elif isinstance(value, MethodType):
        items.extend(
            [
                (("method", "function"), value.__func__),
                (("method", "self"), value.__self__),
            ]
        )
    elif isinstance(value, partial):
        items.extend(
            [
                (("partial", "function"), value.func),
                (("partial", "args"), value.args),
                (("partial", "keywords"), value.keywords or {}),
            ]
        )
    elif isinstance(value, (ReferenceType, WeakMethod)):
        items.append((("weakref", "referent"), value()))
    elif isinstance(value, (BuiltinFunctionType, BuiltinMethodType, MethodWrapperType)):
        referent = object.__getattribute__(value, "__self__")
        if referent is not None and not isinstance(referent, ModuleType):
            items.append((("builtin-method", "self"), referent))
        else:
            items.append(
                (
                    ("builtin-callable", "identity"),
                    (
                        object.__getattribute__(value, "__module__"),
                        object.__getattribute__(value, "__qualname__"),
                    ),
                )
            )
    elif isinstance(
        value,
        (
            ClassMethodDescriptorType,
            GetSetDescriptorType,
            MemberDescriptorType,
            MethodDescriptorType,
            WrapperDescriptorType,
        ),
    ):
        items.append(
            (
                ("builtin-descriptor", "identity"),
                (
                    object.__getattribute__(value, "__objclass__"),
                    object.__getattribute__(value, "__name__"),
                ),
            )
        )
    elif type(value) in _OPERATOR_CARRIER_TYPES:
        items.append((("operator-carrier", "reduction"), value.__reduce__()))
    elif isinstance(value, property):
        items.extend(
            [
                (("property", "getter"), object.__getattribute__(value, "fget")),
                (("property", "setter"), object.__getattribute__(value, "fset")),
                (("property", "deleter"), object.__getattribute__(value, "fdel")),
            ]
        )
    elif isinstance(value, (classmethod, staticmethod)):
        items.append(
            (("descriptor", "function"), object.__getattribute__(value, "__func__"))
        )
    elif isinstance(value, partialmethod):
        items.extend(
            [
                (("partialmethod", "function"), value.func),
                (("partialmethod", "args"), value.args),
                (("partialmethod", "keywords"), value.keywords),
            ]
        )
    try:
        namespace = object.__getattribute__(value, "__dict__")
    except Exception:
        namespace = None
    if isinstance(namespace, dict):
        enum_internal = (
            {"_value_", "_name_", "__objclass__", "_sort_order_"}
            if isinstance(value, Enum)
            else set()
        )
        items.extend(
            (("dict", name), item)
            for name, item in namespace.items()
            if name not in enum_internal
        )

    for owner in type(value).__mro__:
        slots = type.__getattribute__(owner, "__dict__").get("__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        for name in slots:
            if not isinstance(name, str) or name in {"__dict__", "__weakref__"}:
                continue
            storage_name = _slot_storage_name(owner, name)
            try:
                item = object.__getattribute__(value, storage_name)
            except Exception:
                continue
            items.append(
                (
                    (
                        "slot",
                        type.__getattribute__(owner, "__module__"),
                        type.__getattribute__(owner, "__qualname__"),
                        storage_name,
                    ),
                    item,
                )
            )
    return tuple(items)


def inert_dataclass_state(value: Any) -> dict[Any, Any]:
    """Enumerate dataclass fields plus every distinct residual storage location."""
    output = dict(inert_object_state(value))
    for dataclass_field in fields(value):
        name = dataclass_field.name
        slot_key = next(
            (
                key
                for key in output
                if isinstance(key, tuple)
                and len(key) == 4
                and key[0] == "slot"
                and key[3] == name
            ),
            None,
        )
        dict_key = ("dict", name)
        physical_key = slot_key if slot_key is not None else dict_key
        if physical_key in output:
            field_value = output.pop(physical_key)
        else:
            field_value = object.__getattribute__(value, name)
        output[("field", name)] = field_value
    return output


def is_known_immutable_atom(value: Any) -> bool:
    """Recognize scalar values whose public value has no mutable children."""
    value_type = type(value)
    if value is None or value_type in {
        bool,
        bytes,
        complex,
        date,
        Decimal,
        float,
        Fraction,
        int,
        range,
        str,
        timedelta,
        timezone,
        UUID,
    }:
        return True
    if value_type in {datetime, time}:
        return value.tzinfo is None or is_known_immutable_atom(value.tzinfo)
    if value_type is slice:
        return all(
            item is None or is_known_immutable_atom(item)
            for item in (value.start, value.stop, value.step)
        )
    if _is_stdlib_path(value) or value_type in {ZoneInfo, re.Pattern}:
        return True
    return (
        value_type.__module__ == "pydantic_core._pydantic_core"
        and value_type.__name__
        in {
            "Url",
            "MultiHostUrl",
        }
    )


def semantic_snapshot(value: Any) -> Any:
    """Return an immutable token that avoids user equality and rendering hooks.

    The token preserves exact runtime types, container kinds and alias topology.
    Known scalar types receive value snapshots.  Python objects with visible
    ``__dict__`` or slot state receive structural snapshots.  Truly opaque
    extension objects fall back to identity, which is intentionally
    conservative: independently reconstructed opaque values never compare as
    proven-equivalent merely because their ``__eq__`` method says so.
    """

    return _Snapshotter().snapshot(value)


class _UnstableSemanticValue(Exception):
    """An exact value has no process-independent inert representation."""


_TYPE_TOKEN_TAGS = frozenset(
    {
        "atom",
        "dataclass",
        "enum",
        "mapping",
        "model",
        "object",
        "path",
        "pattern",
        "pydantic-url",
        "zoneinfo",
    }
)

_REFERENCE_INDEX = {
    "array": 0,
    "bytearray": 0,
    "dataclass": 1,
    "defaultdict": 0,
    "deque": 0,
    "dict": 0,
    "enum": 1,
    "frozenset": 0,
    "list": 0,
    "mapping": 1,
    "memoryview": 0,
    "model": 1,
    "object": 1,
    "set": 0,
    "tuple": 0,
}


def stable_semantic_digest(value: Any) -> str | None:
    """Hash inspectable runtime semantics without identity or user rendering hooks.

    ``None`` means that some reachable value is truly opaque and therefore has
    no durable integrity token.  The encoding is prefix-free and explicitly
    versioned; changing it requires a run-record format revision.
    """

    try:
        normalized = _canonicalize_references(
            _stable_snapshot(semantic_snapshot(value))
        )
        digest = sha256()
        _digest_write(digest, b"nshconfig.semantic-token.v2")
        _digest_snapshot(digest, normalized)
    except Exception:
        return None
    return f"sha256:{digest.hexdigest()}"


def _stable_snapshot(value: Any) -> Any:
    if type(value) is not _SnapshotNode:
        if isinstance(value, tuple):
            return tuple(_stable_snapshot(item) for item in value)
        if isinstance(value, list):
            return [_stable_snapshot(item) for item in value]
        return value

    tag = value.tag
    if tag == "opaque-identity":
        raise _UnstableSemanticValue
    if tag == "type":
        return _SnapshotNode(tag, value.payload[:2])

    items = list(value.payload)
    if tag in _TYPE_TOKEN_TAGS:
        type_token = items[0]
        if (
            not isinstance(type_token, tuple)
            or len(type_token) != 3
            or not isinstance(type_token[0], str)
            or not isinstance(type_token[1], str)
        ):
            raise _UnstableSemanticValue
        items = [type_token[:2], *items[1:]]
        items[1:] = [_stable_snapshot(item) for item in items[1:]]
    else:
        items = [_stable_snapshot(item) for item in items]
    if tag in {"set", "frozenset"}:
        children = items[1]
        if not isinstance(children, tuple):
            raise _UnstableSemanticValue
        keyed_children = [(_snapshot_sort_key(child), child) for child in children]
        keyed_children.sort(key=lambda item: item[0])
        if any(
            first[0] == second[0]
            for first, second in zip(keyed_children, keyed_children[1:])
        ):
            # Structurally indistinguishable identity-hashed objects have no
            # canonical order inside an unordered container. Reject that rare
            # graph instead of smuggling traversal order into a durable token.
            raise _UnstableSemanticValue
        items[1] = tuple(child for _, child in keyed_children)
    return _SnapshotNode(tag, tuple(items))


def _snapshot_sort_key(value: Any) -> bytes:
    digest = sha256()
    _digest_write(digest, b"nshconfig.semantic-token.sort.v2")
    _digest_snapshot(digest, _without_reference_numbers(value))
    return digest.digest()


def _without_reference_numbers(value: Any) -> Any:
    """Remove traversal-order numbers while deriving an unordered sort key."""

    if type(value) is not _SnapshotNode:
        if isinstance(value, tuple):
            return tuple(_without_reference_numbers(item) for item in value)
        if isinstance(value, list):
            return [_without_reference_numbers(item) for item in value]
        return value
    items = list(value.payload)
    tag = value.tag
    if tag == "ref" and len(items) == 1:
        items[0] = 0
    elif tag in _REFERENCE_INDEX:
        index = _REFERENCE_INDEX[tag]
        if len(items) <= index or type(items[index]) is not int:
            raise _UnstableSemanticValue
        items[index] = 0
    return _SnapshotNode(
        tag,
        tuple(_without_reference_numbers(item) for item in items),
    )


def _canonicalize_references(value: Any) -> Any:
    """Renumber graph references by deterministic normalized traversal order."""

    references: dict[int, int] = {}

    def canonicalize(item: Any) -> Any:
        if type(item) is not _SnapshotNode:
            if isinstance(item, tuple):
                return tuple(canonicalize(value) for value in item)
            if isinstance(item, list):
                return [canonicalize(value) for value in item]
            return item
        values = list(item.payload)
        tag = item.tag
        reference_index: int | None = None
        if tag == "ref" and len(values) == 1:
            reference_index = 0
        elif tag in _REFERENCE_INDEX:
            reference_index = _REFERENCE_INDEX[tag]
        if reference_index is not None:
            if (
                len(values) <= reference_index
                or type(values[reference_index]) is not int
            ):
                raise _UnstableSemanticValue
            old_reference = values[reference_index]
            canonical_reference = references.setdefault(
                old_reference,
                len(references),
            )
            values[reference_index] = canonical_reference
        return _SnapshotNode(tag, tuple(canonicalize(value) for value in values))

    return canonicalize(value)


def _digest_write(digest: Any, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _digest_snapshot(digest: Any, value: Any) -> None:
    value_type = type(value)
    if value is None:
        _digest_write(digest, b"none")
        return
    if value_type is bool:
        _digest_write(digest, b"bool")
        _digest_write(digest, b"1" if value else b"0")
        return
    if value_type is int:
        _digest_write(digest, b"int")
        _digest_write(digest, str(value).encode("ascii"))
        return
    if value_type is float:
        _digest_write(digest, b"float")
        _digest_write(digest, struct.pack(">d", value))
        return
    if value_type is str:
        _digest_write(digest, b"str")
        _digest_write(digest, value.encode("utf-8", "surrogatepass"))
        return
    if value_type is bytes:
        _digest_write(digest, b"bytes")
        _digest_write(digest, value)
        return
    if value_type is _SnapshotNode:
        _digest_write(digest, b"node")
        _digest_write(digest, value.tag.encode("utf-8", "surrogatepass"))
        _digest_write(digest, len(value.payload).to_bytes(8, "big"))
        for item in value.payload:
            _digest_snapshot(digest, item)
        return
    if isinstance(value, tuple):
        _digest_write(digest, b"tuple")
        _digest_write(digest, len(value).to_bytes(8, "big"))
        for item in value:
            _digest_snapshot(digest, item)
        return
    if isinstance(value, list):
        _digest_write(digest, b"list")
        _digest_write(digest, len(value).to_bytes(8, "big"))
        for item in value:
            _digest_snapshot(digest, item)
        return
    raise _UnstableSemanticValue


def _type_token(value: Any) -> tuple[str, str, int]:
    cls = type(value)
    try:
        module = type.__getattribute__(cls, "__module__")
        qualname = type.__getattribute__(cls, "__qualname__")
    except Exception:
        module = "<opaque>"
        qualname = "<opaque>"
    return module, qualname, id(cls)


def _has_inspectable_storage(value: Any) -> bool:
    if isinstance(value, (type, FunctionType, MethodType, ModuleType)):
        return False
    try:
        if isinstance(object.__getattribute__(value, "__dict__"), dict):
            return True
    except (AttributeError, TypeError):
        pass
    return any(
        bool(owner.__dict__.get("__slots__", ())) for owner in type(value).__mro__
    )


def _is_stdlib_path(value: Any) -> bool:
    return type(value) in {
        Path,
        PosixPath,
        PurePath,
        PurePosixPath,
        PureWindowsPath,
        WindowsPath,
    }


class _Snapshotter:
    def __init__(self) -> None:
        self._memo: dict[int, int] = {}

    def snapshot(self, value: Any) -> Any:
        raw = self._snapshot(value)
        if not raw or not isinstance(raw[0], str):
            raise AssertionError("invalid semantic snapshot node")
        return _SnapshotNode(raw[0], tuple(raw[1:]))

    def _snapshot(self, value: Any) -> tuple[Any, ...]:
        value_type = type(value)
        if value is None:
            return ("none",)
        if value_type in {bool, int, str, bytes}:
            return ("atom", _type_token(value), value)
        if value_type is float:
            return ("float", struct.pack(">d", value))
        if value_type is complex:
            return (
                "complex",
                struct.pack(">d", value.real),
                struct.pack(">d", value.imag),
            )
        if isinstance(value, Enum):
            identity = id(value)
            if identity in self._memo:
                return ("ref", self._memo[identity])
            reference = len(self._memo)
            self._memo[identity] = reference
            namespace = object.__getattribute__(value, "__dict__")
            extras = tuple(
                (self.snapshot(key), self.snapshot(item))
                for key, item in namespace.items()
                if key not in {"_value_", "_name_", "__objclass__", "_sort_order_"}
            )
            return (
                "enum",
                _type_token(value),
                reference,
                object.__getattribute__(value, "_name_"),
                self.snapshot(object.__getattribute__(value, "_value_")),
                extras,
            )
        if isinstance(value, type):
            return ("type", value.__module__, value.__qualname__, id(value))
        if value_type is Decimal:
            return ("decimal", value.as_tuple())
        if value_type is Fraction:
            return ("fraction", value.numerator, value.denominator)
        if value_type is UUID:
            return ("uuid", value.bytes, value.is_safe.name)
        if value_type is range:
            return ("range", value.start, value.stop, value.step)
        if value_type is slice:
            return (
                "slice",
                self.snapshot(value.start),
                self.snapshot(value.stop),
                self.snapshot(value.step),
            )
        if value_type is ZoneInfo:
            return ("zoneinfo", _type_token(value), value.key)
        if value_type is timezone:
            return (
                "timezone",
                self.snapshot(value.utcoffset(None)),
                value.tzname(None),
            )
        if value_type is datetime:
            return (
                "datetime",
                value.year,
                value.month,
                value.day,
                value.hour,
                value.minute,
                value.second,
                value.microsecond,
                value.fold,
                self.snapshot(value.tzinfo),
            )
        if value_type is date:
            return ("date", value.year, value.month, value.day)
        if value_type is time:
            return (
                "time",
                value.hour,
                value.minute,
                value.second,
                value.microsecond,
                value.fold,
                self.snapshot(value.tzinfo),
            )
        if value_type is timedelta:
            return ("timedelta", value.days, value.seconds, value.microseconds)
        if _is_stdlib_path(value):
            path = cast(PurePath, value)
            return ("path", _type_token(path), path.parts)
        if isinstance(value, re.Pattern):
            return ("pattern", _type_token(value), value.pattern, value.flags)

        identity = id(value)
        if identity in self._memo:
            return ("ref", self._memo[identity])
        reference = len(self._memo)
        self._memo[identity] = reference

        if value_type is bytearray:
            return ("bytearray", reference, bytes(value))
        if value_type is memoryview:
            return (
                "memoryview",
                reference,
                value.format,
                value.itemsize,
                value.ndim,
                value.shape,
                value.strides,
                value.readonly,
                value.tobytes(),
            )
        if value_type is array:
            return ("array", reference, value.typecode, value.tobytes())
        if isinstance(value, BaseModel):
            return self._model(value, reference)
        if value_type is defaultdict:
            return (
                "defaultdict",
                reference,
                self.snapshot(value.default_factory),
                self._mapping_items(value),
            )
        if value_type is dict:
            return ("dict", reference, self._mapping_items(value))
        if isinstance(value, Mapping):
            return (
                "mapping",
                _type_token(value),
                reference,
                self._mapping_items(value),
                self._object_state(value),
            )
        if value_type is deque:
            return (
                "deque",
                reference,
                value.maxlen,
                tuple(self.snapshot(item) for item in value),
            )
        if value_type is list:
            return ("list", reference, tuple(self.snapshot(item) for item in value))
        if value_type is tuple:
            return ("tuple", reference, tuple(self.snapshot(item) for item in value))
        if value_type is set:
            return ("set", reference, self._unordered_items(value))
        if value_type is frozenset:
            return ("frozenset", reference, self._unordered_items(value))
        if is_dataclass(value) and not isinstance(value, type):
            return (
                "dataclass",
                _type_token(value),
                reference,
                tuple(
                    (
                        field.name,
                        self.snapshot(object.__getattribute__(value, field.name)),
                    )
                    for field in fields(value)
                ),
                self._object_state(value),
            )

        state = self._object_state(value)
        if state or _has_inspectable_storage(value):
            return ("object", _type_token(value), reference, state)

        # Pydantic's Rust-backed URL values are immutable and expose no Python
        # state.  Their ASCII rendering is their documented canonical value.
        if (
            value_type.__module__ == "pydantic_core._pydantic_core"
            and value_type.__name__
            in {
                "Url",
                "MultiHostUrl",
            }
        ):
            return ("pydantic-url", _type_token(value), str(value))

        return ("opaque-identity", _type_token(value), identity)

    def _mapping_items(self, value: Mapping[Any, Any]) -> tuple[Any, ...]:
        # Iteration order is observable runtime state and can change an ML run.
        # Keep one memo table across entries so alias topology and cycles are
        # represented rather than recursively resnapshotted.
        return tuple(
            (self.snapshot(key), self.snapshot(item)) for key, item in value.items()
        )

    def _unordered_items(self, value: Any) -> tuple[Any, ...]:
        # Keep this snapshotter's memo so hostile hashable objects that point
        # back into the surrounding graph remain cycle-safe. Stable record
        # tokens sort the completed element snapshots independently below.
        items = [self.snapshot(item) for item in value]
        return tuple(sorted(items, key=repr))

    def _model(self, value: BaseModel, reference: int) -> Any:
        data = object.__getattribute__(value, "__dict__")
        field_names = set(type(value).__pydantic_fields__)
        declared = tuple(
            (
                name,
                self.snapshot(data[name]) if name in data else ("missing",),
            )
            for name in type(value).__pydantic_fields__
        )
        extra = object.__getattribute__(value, "__pydantic_extra__") or {}
        stored = tuple(
            (self.snapshot(name), self.snapshot(item))
            for name, item in data.items()
            if name not in field_names
        )

        # Config's private state is lifecycle/provenance metadata rather than
        # user value.  Avoid a module-level import cycle while keeping ordinary
        # BaseModel private attributes in their semantic snapshot.
        from .config import Config

        private_data = object.__getattribute__(value, "__pydantic_private__") or {}
        if isinstance(value, Config):
            from .state import RESERVED_PRIVATE_KEYS

            private_data = {
                name: item
                for name, item in private_data.items()
                if name not in RESERVED_PRIVATE_KEYS
            }
        private = tuple(
            (self.snapshot(name), self.snapshot(item))
            for name, item in private_data.items()
        )
        return (
            "model",
            _type_token(value),
            reference,
            declared,
            stored,
            self._mapping_items(extra),
            private,
        )

    def _object_state(self, value: Any) -> tuple[Any, ...]:
        return tuple(
            (self.snapshot(name), self.snapshot(item))
            for name, item in inert_object_state(value)
        )
