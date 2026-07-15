"""Pydantic integration for canonical, declaration-ordered interpolation."""

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import partial
import keyword
from types import UnionType
from typing import Any, Union, cast, get_args, get_origin, get_type_hints
import warnings

from pydantic import AliasChoices, AliasPath, BaseModel
from pydantic.json_schema import PydanticJsonSchemaWarning
from pydantic_core import (
    PydanticCustomError,
    PydanticSerializationError,
    PydanticUndefined,
    ValidationError as CoreValidationError,
    core_schema,
)
from typing_extensions import is_typeddict, override

from .annotations import unwrap_annotation
from .interp import _MODEL_PATHS, Context, Interp, unwrap_view
from .state import (
    FinalState,
    is_draft,
    is_path_part,
    set_final_state,
    state_of,
)

__all__ = ["build_config_json_schema", "build_config_schema", "interpolation_scope"]

PathPart = str | int
Path = tuple[PathPart, ...]


@dataclass
class _Frame:
    cls: type[BaseModel]
    raw: Any
    path: Path
    values: dict[str, Any] = field(default_factory=dict)
    active_name: str | None = None
    active_raw: Any = None
    active_child: "_Frame | None" = None
    active_child_index: int = 0


def safe_repr(value: Any, *, limit: int) -> str:
    """Return a bounded repr for validation paths and errors."""

    try:
        rendered = repr(value)
    except Exception:
        rendered = f"<{type(value).__qualname__}>"
    if len(rendered) <= limit:
        return rendered
    return f"{rendered[: max(0, limit - 3)]}..."


@dataclass(frozen=True)
class _InputLookup:
    field_name: str
    path: Path
    literal: bool


Stack = tuple[_Frame, ...]
_STACK: ContextVar[Stack] = ContextVar("nshconfig_validation_stack", default=())


def _render_path(parts: Path) -> str:
    rendered = ""
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        elif part.isidentifier() and not keyword.iskeyword(part):
            rendered += ("." if rendered else "") + part
        else:
            rendered += f"[{safe_repr(part, limit=80)}]"
    return rendered or "<root>"


class _OrderedInput(dict[Any, Any]):
    """Expose Pydantic's input while resolving Literal markers at field lookup time.

    Pydantic reads model inputs in declaration order through ``Mapping.get``.  A
    Literal cannot have a before/wrap validator when it may be used as an inferred
    discriminator, so this is the only point at which its marker can be resolved
    without replacing Pydantic's field-validation pipeline.
    """

    def __init__(self, raw: dict[Any, Any], frame: _Frame, literals: frozenset[str]):
        super().__init__(raw)
        self._frame = frame
        self._lookups = tuple(_input_lookups(frame.cls, literals))
        self._consumed: set[int] = set()
        self._completed: set[str] = set()

    @override
    def get(self, key: Any, default: Any = None) -> Any:
        value = super().get(key, default)
        selected = self._next_lookup(key)
        if selected is None:
            return value

        index, lookup = selected
        self._consumed.add(index)
        found = _search_path(self, lookup.path)
        if found is PydanticUndefined:
            return value
        self._completed.add(lookup.field_name)
        if not lookup.literal or not isinstance(found, Interp):
            return value

        resolved = _resolve_literal_marker(
            self._frame,
            lookup.field_name,
            found,
        )
        if len(lookup.path) == 1:
            return resolved
        return _replace_path(value, lookup.path[1:], resolved)

    def _next_lookup(self, key: Any) -> tuple[int, _InputLookup] | None:
        for index, lookup in enumerate(self._lookups):
            if index in self._consumed or lookup.field_name in self._completed:
                continue
            if lookup.path[0] == key:
                return index, lookup
        return None


def _input_lookups(
    cls: type[BaseModel], literals: frozenset[str]
) -> list[_InputLookup]:
    lookups: list[_InputLookup] = []
    for name, model_field in cls.__pydantic_fields__.items():
        paths: list[Path] = []
        alias = model_field.validation_alias
        choices = alias.choices if isinstance(alias, AliasChoices) else [alias]
        for choice in choices:
            if isinstance(choice, str):
                paths.append((choice,))
            elif isinstance(choice, AliasPath):
                paths.append(tuple(choice.path))
        paths.append((name,))
        for path in dict.fromkeys(paths):
            lookups.append(_InputLookup(name, path, name in literals))
    return lookups


def _search_path(value: Any, path: Path) -> Any:
    for part in path:
        if isinstance(value, str):
            return PydanticUndefined
        try:
            value = value[part]
        except (KeyError, IndexError, TypeError):
            return PydanticUndefined
    return value


def _replace_path(value: Any, path: Path, replacement: Any) -> Any:
    part, *remaining = path
    child = value[part]
    updated = (
        _replace_path(child, tuple(remaining), replacement)
        if remaining
        else replacement
    )
    if isinstance(value, Mapping):
        output = dict(value)
        output[part] = updated
        return output
    if isinstance(value, list):
        if not isinstance(part, int):
            raise TypeError("list AliasPath components must be integers")
        output = list(value)
        output[part] = updated
        return output
    if isinstance(value, tuple):
        if not isinstance(part, int):
            raise TypeError("tuple AliasPath components must be integers")
        output = list(value)
        output[part] = updated
        return tuple(output)
    raise TypeError(
        "interpolation through AliasPath requires mapping, list, or tuple input containers"
    )


def _literal_values(schema: dict[str, Any]) -> tuple[Any, ...] | None:
    current = schema
    while isinstance(current, dict):
        if current.get("type") == "literal":
            expected = current.get("expected")
            return tuple(expected) if isinstance(expected, list) else None
        child = current.get("schema")
        if not isinstance(child, dict):
            return None
        current = child
    return None


def _wrap_field_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if schema.get("type") in {"function-wrap", "function-after"} and schema.get(
        "metadata", {}
    ).get("nshconfig_field_wrapper"):
        return schema
    literal_values = _literal_values(schema)
    if literal_values is not None:
        wrapped = core_schema.with_info_after_validator_function(
            capture_literal_field,
            cast("core_schema.CoreSchema", schema),
            metadata={"nshconfig_field_wrapper": True},
        )
    else:
        before = core_schema.with_info_before_validator_function(
            resolve_field,
            cast("core_schema.CoreSchema", schema),
            metadata={"nshconfig_field_wrapper": True},
        )
        wrapped = core_schema.with_info_after_validator_function(
            capture_field,
            before,
            metadata={"nshconfig_field_wrapper": True},
        )
    return cast("dict[str, Any]", wrapped)


def _prepare_literal_default(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    updated = dict(schema)
    if isinstance(marker := updated.get("default"), Interp):
        updated.pop("default")
        updated["default_factory"] = partial(_literal_marker_default, name, marker)
        updated["default_factory_takes_data"] = False
        return updated

    factory = updated.get("default_factory")
    if factory is not None:
        takes_data = bool(updated.get("default_factory_takes_data"))
        updated["default_factory"] = partial(
            _literal_factory_default,
            name,
            factory,
            takes_data,
        )
    return updated


def _literal_marker_default(name: str, marker: Interp) -> Any:
    stack = _STACK.get()
    assert stack, "Literal default ran without its Config validation scope"
    return _resolve_literal_marker(stack[-1], name, marker)


def _literal_factory_default(
    name: str,
    factory: Any,
    takes_data: bool,
    validated_data: dict[str, Any] | None = None,
) -> Any:
    value = factory(validated_data) if takes_data else factory()
    if not isinstance(value, Interp):
        return value
    stack = _STACK.get()
    assert stack, "Literal default factory ran without its Config validation scope"
    return _resolve_literal_marker(stack[-1], name, value)


def _wrap_model_fields(node: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if node.get("type") == "model-fields":
        wrapped_fields: dict[str, Any] = {}
        literal_fields: list[str] = []
        for name, model_field in node["fields"].items():
            model_field = dict(model_field)
            schema = model_field["schema"]
            literal_schema = (
                schema.get("schema") if schema.get("type") == "default" else schema
            )
            literal_values = _literal_values(literal_schema)
            if literal_values is not None:
                literal_fields.append(name)
            if schema.get("type") == "default":
                schema = (
                    _prepare_literal_default(name, schema)
                    if literal_values is not None
                    else dict(schema)
                )
                schema["schema"] = _wrap_field_schema(schema["schema"])
            else:
                schema = _wrap_field_schema(schema)
            model_field["schema"] = schema
            wrapped_fields[name] = model_field
        updated = dict(node)
        updated["fields"] = wrapped_fields
        return cast(
            "dict[str, Any]",
            core_schema.with_info_before_validator_function(
                partial(capture_model_input, frozenset(literal_fields)),
                cast("core_schema.CoreSchema", updated),
                metadata={"nshconfig_model_input": True},
            ),
        ), True

    child = node.get("schema")
    if isinstance(child, dict):
        wrapped, found = _wrap_model_fields(child)
        if found:
            updated = dict(node)
            updated["schema"] = wrapped
            return updated, True
    return node, False


def _wrap_own_model(node: Any, cls: type[BaseModel]) -> tuple[Any, bool]:
    if not isinstance(node, dict):
        return node, False
    if node.get("type") == "model" and node.get("cls") is cls:
        updated, found = _wrap_model_fields(node)
        assert found, f"Pydantic schema for {cls.__name__} has no model-fields node"
        if updated.get("ref") is not None:
            updated = dict(updated)
            updated.pop("ref")
        return updated, True

    for key, child in node.items():
        if key in {"metadata", "serialization"}:
            continue
        if isinstance(child, dict):
            wrapped, found = _wrap_own_model(child, cls)
            if found:
                updated = dict(node)
                updated[key] = wrapped
                return updated, True
        elif isinstance(child, list):
            for index, item in enumerate(child):
                wrapped, found = _wrap_own_model(item, cls)
                if found:
                    updated = dict(node)
                    children = list(child)
                    children[index] = wrapped
                    updated[key] = children
                    return updated, True
    return node, False


def capture_model_input(
    literal_fields: frozenset[str],
    value: Any,
    info: Any,
) -> Any:
    """Remember input after model-before validators have transformed it."""
    stack = _STACK.get()
    assert stack, "Config model fields ran without their validation scope"
    _check_builtin_input(value, set(), set(), ())
    frame = stack[-1]
    frame.raw = value
    if literal_fields and isinstance(value, Mapping):
        ordered = dict(value)
        frame.raw = ordered
        return _OrderedInput(ordered, frame, literal_fields)
    return value


def _check_builtin_input(
    value: Any,
    active: set[int],
    checked: set[int],
    path: Path,
) -> None:
    """Reject cycles while leaving ordinary Pydantic input identity untouched."""
    if type(value) not in {dict, list, tuple, set, frozenset}:
        return
    identity = id(value)
    if identity in active:
        raise PydanticCustomError(
            "nshconfig_cycle",
            "configuration input must be acyclic; cycle found at {path}",
            {"path": _render_path(path)},
        )
    if identity in checked:
        return
    active.add(identity)
    try:
        if type(value) is dict:
            for key, item in value.items():
                _check_builtin_input(key, active, checked, (*path, "<key>"))
                _check_builtin_input(
                    item,
                    active,
                    checked,
                    (
                        *path,
                        key if is_path_part(key) else safe_repr(key, limit=80),
                    ),
                )
            return
        for index, item in enumerate(value):
            _check_builtin_input(item, active, checked, (*path, index))
    finally:
        active.remove(identity)
        checked.add(identity)


def build_config_schema(
    cls: type[BaseModel], source: Any, handler: Any
) -> core_schema.CoreSchema:
    """Wrap this Config's complete field pipelines and all serializer entry points."""
    schema = handler(source)
    from .config import _validate_config_class

    _validate_config_class(cls)
    if schema.get("type") == "definition-ref":
        resolved = handler.resolve_ref_schema(schema)
        if resolved.get("type") == "function-wrap" and resolved.get("metadata", {}).get(
            "nshconfig_scope"
        ):
            return schema
        wrapped = _build_scoped_schema(cls, resolved)
        resolved.clear()
        resolved.update(wrapped)
        return schema
    if schema.get("type") == "function-wrap" and schema.get("metadata", {}).get(
        "nshconfig_scope"
    ):
        return schema
    return _build_scoped_schema(cls, schema)


def _build_scoped_schema(
    cls: type[BaseModel], schema: dict[str, Any]
) -> core_schema.CoreSchema:
    outer_ref = _find_model_ref(schema, cls)
    wrapped, found = _wrap_own_model(schema, cls)
    if not found:
        raise TypeError(f"could not locate {cls.__name__}'s Pydantic model schema")
    return core_schema.with_info_wrap_validator_function(
        partial(interpolation_scope, cls),
        cast("core_schema.CoreSchema", wrapped),
        ref=outer_ref,
        metadata={"nshconfig_scope": True},
        serialization=core_schema.wrap_serializer_function_ser_schema(
            serialize_config,
            info_arg=True,
        ),
    )


def _find_model_ref(node: Any, cls: type[BaseModel]) -> str | None:
    if not isinstance(node, dict):
        return None
    if node.get("type") == "model" and node.get("cls") is cls:
        ref = node.get("ref")
        return ref if isinstance(ref, str) else None
    for key, child in node.items():
        if key in {"metadata", "serialization"}:
            continue
        if isinstance(child, dict):
            if (ref := _find_model_ref(child, cls)) is not None:
                return ref
        elif isinstance(child, list):
            for item in child:
                if (ref := _find_model_ref(item, cls)) is not None:
                    return ref
    return None


def build_config_json_schema(schema: core_schema.CoreSchema, handler: Any) -> Any:
    """Generate JSON Schema without trying to encode executable marker defaults."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            category=PydanticJsonSchemaWarning,
            message=r"Default value .* is not JSON serializable; excluding default.*",
        )
        return handler(_without_marker_defaults(schema, set()))


def _without_marker_defaults(value: Any, active: set[int]) -> Any:
    """Copy only schema branches that contain an executable marker default."""
    if not isinstance(value, (dict, list)):
        return value
    identity = id(value)
    if identity in active:
        return value
    active.add(identity)
    try:
        if isinstance(value, list):
            items = [_without_marker_defaults(item, active) for item in value]
            return (
                items
                if any(new is not old for new, old in zip(items, value))
                else value
            )

        changed = False
        output: dict[str, Any] = {}
        for key, item in value.items():
            if key == "default" and isinstance(item, Interp):
                changed = True
                continue
            if key in {"cls", "function", "metadata", "serialization"}:
                updated = item
            else:
                updated = _without_marker_defaults(item, active)
            changed = changed or updated is not item
            output[key] = updated
        return output if changed else value
    finally:
        active.remove(identity)


def serialize_config(value: Any, handler: Any, info: Any) -> Any:
    """Reject drafts even through TypeAdapter and nested Pydantic serializers."""
    if is_draft(value):
        raise PydanticSerializationError(
            "nshconfig drafts are not serializable; call config_finalize() first"
        )
    return handler(value)


def _input_value(raw: Any, cls: type[BaseModel], name: str) -> Any:
    if isinstance(raw, cls):
        return object.__getattribute__(raw, "__dict__").get(name, PydanticUndefined)

    candidates = _input_candidates(cls, name)

    for candidate in candidates:
        value = _lookup_input_candidate(raw, candidate)
        if value is not PydanticUndefined:
            return value
    return PydanticUndefined


def _input_candidates(cls: type[BaseModel], name: str) -> list[str | AliasPath]:
    model_field = cls.__pydantic_fields__[name]
    candidates: list[str | AliasPath] = []
    if model_field.alias is not None:
        candidates.append(model_field.alias)
    alias = model_field.validation_alias
    if alias is not None:
        candidates.extend(alias.choices if isinstance(alias, AliasChoices) else [alias])
    candidates.append(name)
    unique: list[str | AliasPath] = []
    seen: set[tuple[Any, ...]] = set()
    for candidate in candidates:
        token = (
            tuple(candidate.path) if isinstance(candidate, AliasPath) else (candidate,)
        )
        if token not in seen:
            seen.add(token)
            unique.append(candidate)
    return unique


def _lookup_input_candidate(raw: Any, candidate: str | AliasPath) -> Any:
    path = candidate.path if isinstance(candidate, AliasPath) else [candidate]
    value = raw
    for part in path:
        try:
            if isinstance(value, Mapping):
                value = value[part]
            elif isinstance(part, str):
                value = getattr(value, part)
            else:
                value = value[part]
        except (AttributeError, KeyError, IndexError, TypeError):
            return PydanticUndefined
    return value


def _provided(raw: Any, cls: type[BaseModel], name: str) -> bool:
    return _input_value(raw, cls, name) is not PydanticUndefined


def _evaluate_marker(
    frame: _Frame,
    name: str,
    marker: Interp,
) -> Any:
    stack = _STACK.get()
    model_path_token = _MODEL_PATHS.set({})
    stack_token = _STACK.set(())
    try:
        value = unwrap_view(marker.fn(Context(stack)))
    except Exception as error:
        path = _render_path((*frame.path, name))
        raise PydanticCustomError(
            "nshconfig_interpolation",
            "cannot interpolate {path} using {marker}: {error}",
            {"path": path, "marker": repr(marker), "error": str(error)},
        ) from error
    finally:
        _STACK.reset(stack_token)
        _MODEL_PATHS.reset(model_path_token)
    return value


def _resolve_literal_marker(
    frame: _Frame,
    name: str,
    marker: Interp,
) -> Any:
    try:
        value = _evaluate_marker(frame, name, marker)
    except PydanticCustomError as error:
        raise CoreValidationError.from_exception_data(
            frame.cls.__name__,
            [{"type": error, "loc": (name,), "input": marker}],
        ) from error

    frame.active_name = name
    frame.active_raw = value
    frame.active_child = None
    frame.active_child_index = 0
    return value


def resolve_field(value: Any, info: Any) -> Any:
    """Resolve a marker before Pydantic's native field pipeline."""
    stack = _STACK.get()
    assert stack, "Config field validation ran without its model scope"
    frame = stack[-1]
    name = info.field_name
    assert isinstance(name, str), "Config field wrapper did not receive a field name"

    if not _provided(frame.raw, frame.cls, name):
        from .finalize import default_input

        try:
            value = default_input(
                value,
                frame.cls.__pydantic_fields__[name].annotation,
                _render_path((*frame.path, name)),
                discriminator=frame.cls.__pydantic_fields__[name].discriminator,
            )
        except (TypeError, ValueError) as error:
            raise PydanticCustomError(
                "nshconfig_default",
                "cannot realize default at {path}: {error}",
                {
                    "path": _render_path((*frame.path, name)),
                    "error": str(error),
                },
            ) from error

    discriminator = frame.cls.__pydantic_fields__[name].discriminator
    if (
        isinstance(discriminator, str)
        and isinstance(value, Mapping)
        and isinstance(value.get(discriminator), Interp)
    ):
        raise PydanticCustomError(
            "nshconfig_discriminator_interpolation",
            "discriminator {path} must be concrete before union branch selection",
            {"path": _render_path((*frame.path, name, discriminator))},
        )

    marker = value if isinstance(value, Interp) else None
    if marker is not None:
        value = _evaluate_marker(frame, name, marker)

    frame.active_name = name
    frame.active_raw = value
    frame.active_child = None
    frame.active_child_index = 0
    return value


def capture_field(value: Any, info: Any) -> Any:
    """Publish a fully canonical value after Pydantic's complete field pipeline."""
    stack = _STACK.get()
    assert stack, "Config field validation ran without its model scope"
    frame = stack[-1]
    name = info.field_name
    assert isinstance(name, str), "Config field wrapper did not receive a field name"
    _capture_field(frame, name, value)
    frame.active_name = None
    frame.active_raw = None
    frame.active_child = None
    frame.active_child_index = 0
    return value


def capture_literal_field(value: Any, info: Any) -> Any:
    """Publish a canonical Literal without obscuring Pydantic discriminators."""
    return capture_field(value, info)


def _capture_field(frame: _Frame, name: str, value: Any) -> None:
    frame.values[name] = value


def _identity_path(container: Any, target: Any, active: set[int]) -> Path | None:
    if container is target:
        return ()
    identity = id(container)
    if identity in active:
        return None
    active.add(identity)
    try:
        if isinstance(container, Mapping):
            for key, value in container.items():
                found = _identity_path(value, target, active)
                if found is not None:
                    part: PathPart = (
                        key if is_path_part(key) else safe_repr(key, limit=80)
                    )
                    return (part, *found)
        elif isinstance(container, (list, tuple)):
            for index, value in enumerate(container):
                found = _identity_path(value, target, active)
                if found is not None:
                    return (index, *found)
    finally:
        active.remove(identity)
    return None


def _child_path(
    parent: _Frame, value: Any, child_cls: type[BaseModel]
) -> tuple[Path, bool] | None:
    if parent.active_name is None:
        return None
    relative = _identity_path(parent.active_raw, value, set())
    model_field = parent.cls.__pydantic_fields__[parent.active_name]
    direct_child = _direct_model_annotation_accepts(model_field.annotation, child_cls)
    if relative is None and not _annotation_contains_model(
        model_field.annotation, child_cls
    ):
        return None
    if relative is None:
        # A field-before validator may replace or clone its input before nested
        # model validation begins. Pydantic does not expose that transformed
        # location to child validators, so retain honest ancestry and use the
        # validation ordinal as a temporary container location.
        relative = () if direct_child else (parent.active_child_index,)
    parent.active_child_index += 1
    return ((*parent.path, parent.active_name, *relative), direct_child)


def _direct_model_annotation_accepts(annotation: Any, cls: type[BaseModel]) -> bool:
    annotation, _ = unwrap_annotation(annotation, None)
    origin = get_origin(annotation)
    if origin is None and isinstance(annotation, type) and not is_typeddict(annotation):
        try:
            if issubclass(cls, annotation):
                return True
        except TypeError:
            pass
    if origin in {Union, UnionType}:
        return any(
            _direct_model_annotation_accepts(argument, cls)
            for argument in get_args(annotation)
            if argument is not type(None)
        )
    return False


def _annotation_contains_model(annotation: Any, cls: type[BaseModel]) -> bool:
    if _direct_model_annotation_accepts(annotation, cls):
        return True
    arguments = get_args(annotation)
    if is_typeddict(annotation):
        try:
            arguments = tuple(get_type_hints(annotation, include_extras=True).values())
        except Exception:
            arguments = tuple(annotation.__annotations__.values())
    return any(_annotation_contains_model(argument, cls) for argument in arguments)


def interpolation_scope(
    cls: type[BaseModel], value: Any, handler: Any, info: Any
) -> Any:
    """Maintain Config ancestry around Pydantic's native model pipeline."""
    if is_draft(value):
        raise PydanticCustomError(
            "nshconfig_draft_input",
            "a draft cannot be validated as a value; call config_finalize() on the root draft",
        )

    stack = _STACK.get()
    direct_child = False
    if stack and (child := _child_path(stack[-1], value, cls)) is not None:
        path, direct_child = child
        parent_stack = stack
    else:
        path = ()
        parent_stack = ()

    frame = _Frame(cls, value, path)
    parent = parent_stack[-1] if parent_stack else None
    old_child = parent.active_child if parent is not None else None
    if parent is not None and direct_child:
        parent.active_child = frame
    token = _STACK.set((*parent_stack, frame))
    try:
        output = handler(value)
    finally:
        _STACK.reset(token)
        if parent is not None and direct_child:
            parent.active_child = old_child

    if isinstance(output, cls) and not is_draft(output):
        set_final_state(output)
    if not parent_stack:
        _assert_declared_graph(output, cls.__name__, set(), cls)
    return output


def _assert_declared_graph(
    value: Any,
    path: str,
    seen: set[int],
    annotation: Any,
    discriminator: Any = None,
) -> None:
    """Reject lifecycle values that survive in the declared Config graph."""

    if isinstance(value, Interp):
        raise PydanticCustomError(
            "nshconfig_pending",
            "unresolved interpolation is not legal at {path}",
            {"path": path},
        )
    if is_draft(value):
        raise PydanticCustomError(
            "nshconfig_pending_draft",
            "a nested draft survived validation at {path}",
            {"path": path},
        )

    from .config import Config

    if not isinstance(value, Config) and type(value) not in {
        dict,
        frozenset,
        list,
        set,
        tuple,
    }:
        return

    from .finalize import (
        _concrete_config_type,
        _mapping_annotations,
        _mapping_value_annotation,
        _select_annotation,
        _sequence_annotations,
        _set_annotation,
    )

    identity = id(value)
    entered = False
    try:
        selected, selected_discriminator = _select_annotation(
            value,
            annotation,
            path,
            discriminator=discriminator,
        )
        if identity in seen:
            return
        seen.add(identity)
        entered = True
        if isinstance(value, Config):
            if _concrete_config_type(selected) is not type(value):
                raise PydanticCustomError(
                    "nshconfig_opaque_config",
                    "Config value at {path} is hidden under an opaque or incompatible annotation",
                    {"path": path},
                )
            if not isinstance(state_of(value), FinalState):
                raise PydanticCustomError(
                    "nshconfig_unvalidated_node",
                    "Config value at {path} bypassed its validation scope",
                    {"path": path},
                )
            data = object.__getattribute__(value, "__dict__")
            for name, model_field in type(value).__pydantic_fields__.items():
                if name in data:
                    _assert_declared_graph(
                        data[name],
                        f"{path}.{name}",
                        seen,
                        model_field.annotation,
                        model_field.discriminator,
                    )
            return

        if type(value) is dict:
            key_annotation, item_annotation = _mapping_annotations(selected)
            for key, item in value.items():
                _assert_declared_graph(
                    key,
                    f"{path}.<key>",
                    seen,
                    key_annotation,
                )
                _assert_declared_graph(
                    item,
                    f"{path}[{safe_repr(key, limit=80)}]",
                    seen,
                    _mapping_value_annotation(selected, key, item_annotation),
                )
            return

        if type(value) in {list, tuple}:
            annotations = _sequence_annotations(selected, len(value))
            for index, item in enumerate(value):
                _assert_declared_graph(
                    item,
                    f"{path}[{index}]",
                    seen,
                    annotations[index],
                )
            return

        item_annotation = _set_annotation(selected)
        for index, item in enumerate(value):
            _assert_declared_graph(
                item,
                f"{path}[{index}]",
                seen,
                item_annotation,
            )
    except PydanticCustomError:
        raise
    except (TypeError, ValueError) as error:
        raise PydanticCustomError(
            "nshconfig_graph_annotation",
            "configuration value at {path} does not match its structural annotation: {error}",
            {"path": path, "error": str(error)},
        ) from error
    finally:
        if entered:
            seen.remove(identity)
