"""Class declarations and field-local Pydantic validation."""

from __future__ import annotations

import ast
import inspect
import sys
from dataclasses import dataclass
from typing import Any, ClassVar, ForwardRef, cast, get_args, get_origin, get_type_hints
from collections.abc import Callable, Iterable, Iterator
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

        return annotationlib.get_annotations(
            cls, format=annotationlib.Format.FORWARDREF
        )
    return inspect.get_annotations(cls, eval_str=False)


def annotation_names(annotations: Iterable[Any]) -> Iterator[str]:
    """Find names needed to retain function-local forward annotation scopes."""
    pending = list(annotations)
    while pending:
        annotation = pending.pop()
        if isinstance(annotation, ForwardRef):
            annotation = annotation.__forward_arg__
        if not isinstance(annotation, str):
            pending.extend(get_args(annotation))
            continue
        try:
            expression = ast.parse(annotation, mode="eval")
        except SyntaxError:
            # Literal string metadata need not be a Python expression.
            continue
        for node in ast.walk(expression):
            if isinstance(node, ast.Name):
                yield node.id
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                pending.append(node.value)


def namespaces(owner: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    module = sys.modules.get(owner.__module__)
    globalns = dict(vars(module)) if module is not None else {}
    # Functions created while evaluating a postponed annotation must also see
    # captured function locals, rather than only the defining module's globals.
    globalns.update(owner._namespace)
    globalns.update(owner._namespace_overrides)
    localns = {**globalns, **vars(owner), **owner._namespace_overrides}
    localns[owner.__name__] = owner
    return globalns, localns


def is_classvar(annotation: Any, owner: Any) -> bool:
    if isinstance(annotation, str):
        expression = ast.parse(annotation, mode="eval").body
        while isinstance(expression, ast.Constant) and isinstance(
            expression.value, str
        ):
            expression = ast.parse(expression.value, mode="eval").body
        # Only resolve the outer type: ClassVar["DefinedLater"] is still a
        # class variable even when its parameter cannot yet be resolved.
        if isinstance(expression, ast.Subscript):
            expression = expression.value
        globalns, localns = namespaces(owner)
        try:
            annotation = eval(
                compile(ast.Expression(expression), "<annotation>", "eval"),
                globalns,
                localns,
            )
        except (NameError, AttributeError):
            return False
    return annotation is ClassVar or get_origin(annotation) is ClassVar


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
        globalns, localns = namespaces(owner)
        field_hint = type(
            "_FieldHint", (), {"__annotations__": {"value": declaration.annotation}}
        )
        resolved = get_type_hints(
            field_hint, globalns=globalns, localns=localns, include_extras=True
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
