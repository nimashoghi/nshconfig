"""Class declarations and field-local Pydantic validation."""

from __future__ import annotations

import inspect
import sys
from dataclasses import dataclass
from typing import Any, ClassVar, cast, get_origin, get_type_hints
from collections.abc import Callable
from weakref import WeakKeyDictionary

from pydantic import ConfigDict, TypeAdapter
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined


@dataclass
class Declaration:
    annotation: Any
    default: Any = PydanticUndefined
    owner: Any = None


# Core validators are process-local compiled artifacts, never class pickle state.
_ADAPTERS: WeakKeyDictionary[type, dict[str, TypeAdapter[Any]]] = WeakKeyDictionary()


def class_annotations(cls: type) -> dict[str, Any]:
    if sys.version_info >= (3, 14):
        import annotationlib

        return annotationlib.get_annotations(cls, format=annotationlib.Format.STRING)
    return inspect.get_annotations(cls, eval_str=False)


def is_classvar(annotation: Any) -> bool:
    return get_origin(annotation) is ClassVar or (
        isinstance(annotation, str)
        and annotation.replace("typing.", "").startswith("ClassVar[")
    )


def adapter(cls: Any, name: str) -> TypeAdapter[Any]:
    cached = _ADAPTERS.setdefault(cls, {})
    if name not in cached:
        hints = resolve(cls)
        declaration = cls._declarations[name]
        info = FieldInfo.from_annotated_attribute(hints[name], declaration.default)
        if info.alias or info.validation_alias or info.serialization_alias:
            raise TypeError("Config fields use Python names; aliases are unsupported")
        cached[name] = TypeAdapter(
            info.rebuild_annotation(), config=ConfigDict(strict=True)
        )
    return cached[name]


def default(declaration: Declaration) -> Any:
    value = declaration.default
    if isinstance(value, FieldInfo):
        if value.default_factory is not None:
            if value.default_factory_takes_validated_data:
                raise TypeError(
                    "default_factory must take no arguments; use interp for dependencies"
                )
            return cast(Callable[[], Any], value.default_factory)()
        return value.default
    return value


def resolve(cls: Any) -> dict[str, Any]:
    hints: dict[str, Any] = {}
    for name, declaration in cls._declarations.items():
        owner = declaration.owner
        namespace = dict(owner._namespace)
        namespace[owner.__name__] = owner
        module = sys.modules.get(owner.__module__)
        globalns = vars(module) if module is not None else {}

        def field_hint() -> None:
            pass

        field_hint.__annotations__ = {"value": declaration.annotation}
        resolved = get_type_hints(
            field_hint, globalns=globalns, localns=namespace, include_extras=True
        )["value"]
        declaration.annotation = resolved
        hints[name] = resolved
    own = class_annotations(cls)
    cls.__annotations__ = {
        name: hints.get(name, annotation) for name, annotation in own.items()
    }
    return hints


def clear_cache(cls: type) -> None:
    _ADAPTERS.pop(cls, None)
