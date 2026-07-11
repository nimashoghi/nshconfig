"""Shared annotation normalization for runtime structural checks."""

from collections.abc import (
    AsyncIterable,
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Collection,
    Coroutine,
    Generator,
    Iterable,
    Iterator,
    Mapping,
    MutableMapping,
    MutableSequence,
    MutableSet,
    Sequence,
    Set,
)
from dataclasses import is_dataclass
from enum import Enum
from types import UnionType
import typing
from typing import (
    Annotated,
    Any,
    Literal,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from typing_extensions import (
    NotRequired,
    ReadOnly,
    Required,
    TypeAliasType as _ExtensionsTypeAliasType,
    is_typeddict,
)
from pydantic import BaseModel

_NativeTypeAliasType = getattr(typing, "TypeAliasType", None)

__all__ = [
    "unsupported_container_annotation",
    "unwrap_annotation",
    "unwrap_type_alias",
]

_ALLOWED_CONTAINER_ORIGINS = {
    dict,
    frozenset,
    list,
    Mapping,
    MutableMapping,
    MutableSequence,
    MutableSet,
    Sequence,
    set,
    Set,
    tuple,
}
_LAZY_CONTAINER_ORIGINS = {
    AsyncGenerator,
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Coroutine,
    Generator,
    Iterable,
    Iterator,
}
_SCALAR_COLLECTION_TYPES = {bytearray, bytes, memoryview, range, str}


def _is_type_alias(value: Any) -> bool:
    native = _NativeTypeAliasType
    return type(value) is _ExtensionsTypeAliasType or (
        isinstance(native, type) and type(value) is native
    )


def unwrap_type_alias(annotation: Any) -> Any:
    """Resolve consecutive PEP 695 named aliases without descending containers."""

    seen: set[int] = set()
    while _is_type_alias(annotation) or _is_type_alias(get_origin(annotation)):
        alias = annotation if _is_type_alias(annotation) else get_origin(annotation)
        identity = id(alias)
        if identity in seen:
            raise TypeError("recursive type alias cannot resolve to itself directly")
        seen.add(identity)
        parameters = tuple(getattr(alias, "__type_params__", ()))
        arguments = get_args(annotation) if annotation is not alias else ()
        substitutions = dict(zip(parameters, arguments))
        annotation = _substitute_type_parameters(alias.__value__, substitutions, set())
    return annotation


def _substitute_type_parameters(
    annotation: Any,
    substitutions: dict[Any, Any],
    active: set[int],
) -> Any:
    try:
        if annotation in substitutions:
            return substitutions[annotation]
    except TypeError:
        pass
    identity = id(annotation)
    if identity in active:
        return annotation
    arguments = get_args(annotation)
    if not arguments:
        return annotation
    active.add(identity)
    try:
        replaced = tuple(
            _substitute_type_parameters(argument, substitutions, active)
            for argument in arguments
        )
    finally:
        active.remove(identity)
    if replaced == arguments:
        return annotation
    copier = getattr(annotation, "copy_with", None)
    if callable(copier):
        try:
            return copier(replaced)
        except (AttributeError, TypeError, ValueError):
            pass
    origin = get_origin(annotation)
    if origin is UnionType:
        output = replaced[0]
        for argument in replaced[1:]:
            output = output | argument
        return output
    if origin is Annotated:
        return cast(Any, Annotated).__class_getitem__(replaced)
    if origin is not None:
        try:
            return origin[replaced[0] if len(replaced) == 1 else replaced]
        except (AttributeError, TypeError, ValueError):
            pass
    return annotation


def unwrap_annotation(annotation: Any, discriminator: Any) -> tuple[Any, Any]:
    """Unwrap named aliases and ``Annotated`` while retaining discriminators."""

    while True:
        normalized = unwrap_type_alias(annotation)
        if normalized is not annotation:
            annotation = normalized
            continue
        if get_origin(annotation) in {Required, NotRequired, ReadOnly}:
            arguments = get_args(annotation)
            annotation = arguments[0] if arguments else Any
            continue
        if get_origin(annotation) is not Annotated:
            return annotation, discriminator
        annotation, *metadata = get_args(annotation)
        for item in metadata:
            if (candidate := getattr(item, "discriminator", None)) is not None:
                discriminator = candidate


def unsupported_container_annotation(annotation: Any) -> str | None:
    """Name a container annotation outside the finite built-in value graph."""

    return _unsupported_container_annotation(annotation, set())


def _unsupported_container_annotation(annotation: Any, active: set[int]) -> str | None:
    identity = id(annotation)
    if identity in active:
        return None
    active.add(identity)
    annotation, _ = unwrap_annotation(annotation, None)
    origin = get_origin(annotation)
    candidate = origin or annotation

    if is_typeddict(annotation):
        try:
            arguments = tuple(get_type_hints(annotation, include_extras=True).values())
        except Exception:
            arguments = tuple(annotation.__annotations__.values())
        for argument in arguments:
            if (
                unsafe := _unsupported_container_annotation(argument, active)
            ) is not None:
                return unsafe
        return None
    if origin is Literal:
        return None
    if origin in {Union, UnionType}:
        for argument in get_args(annotation):
            if (
                unsafe := _unsupported_container_annotation(argument, active)
            ) is not None:
                return unsafe
        return None
    if isinstance(annotation, TypeVar):
        constraints = annotation.__constraints__
        bound = annotation.__bound__
        for argument in (*constraints, *((bound,) if bound is not None else ())):
            if (
                unsafe := _unsupported_container_annotation(argument, active)
            ) is not None:
                return unsafe
        return None
    if candidate in _ALLOWED_CONTAINER_ORIGINS:
        for argument in get_args(annotation):
            if argument is Ellipsis:
                continue
            if (
                unsafe := _unsupported_container_annotation(argument, active)
            ) is not None:
                return unsafe
        return None
    if candidate in _LAZY_CONTAINER_ORIGINS:
        return getattr(candidate, "__qualname__", str(candidate))
    if isinstance(candidate, type) and candidate not in _SCALAR_COLLECTION_TYPES:
        try:
            if issubclass(candidate, (BaseModel, Enum)) or is_dataclass(candidate):
                return None
            if issubclass(candidate, Collection):
                return candidate.__qualname__
            if issubclass(candidate, (Iterable, AsyncIterable, Awaitable)):
                return candidate.__qualname__
        except TypeError:
            pass
    # Do not inspect unrelated generic arguments such as Callable return types
    # or type[T]; only recognized structural containers recurse above.
    return None
