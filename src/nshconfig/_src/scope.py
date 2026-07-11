"""Pydantic integration for canonical, declaration-ordered interpolation."""

from collections import deque
from collections.abc import AsyncIterable, Awaitable, Collection, Iterable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, is_dataclass
from enum import Enum
from functools import partial
import keyword
from types import UnionType
from typing import Any, Iterator, Union, cast, get_args, get_origin, get_type_hints
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
from .interp import _MODEL_PATHS, _READS, Context, Interp, unwrap_view
from .provenance import (
    Event,
    attach_interpolations,
    copy_provenance,
    final_reuse_error,
    interpolation_event,
    merge_reused_final_provenance,
    safe_repr,
    safe_exception_text,
    stored_path as _stored_path,
)
from .semantic import (
    inert_dataclass_state as _dataclass_state_items,
    inert_object_state as _opaque_state_items,
    is_known_immutable_atom,
    semantic_snapshot,
)
from .state import (
    ORIGIN_PATH_KEY,
    RESERVED_PRIVATE_KEYS,
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
    snapshots: dict[str, Any] = field(default_factory=dict)
    interpolations: dict[str, Event] = field(default_factory=dict)
    pending_interpolations: dict[str, tuple[Interp, list[Any], bool]] = field(
        default_factory=dict
    )
    defaults_used: set[str] = field(default_factory=set)
    validation_context: Any = None
    active_name: str | None = None
    active_raw: Any = None
    active_child: "_Frame | None" = None
    active_child_index: int = 0
    built_instance: BaseModel | None = None
    reused_source: BaseModel | None = None
    reused_sources: list[
        tuple[BaseModel, BaseModel, Path, Any, dict[Path, frozenset[str]]]
    ] = field(default_factory=list)


@dataclass(frozen=True)
class _InputLookup:
    field_name: str
    path: Path
    literal: bool


@dataclass(frozen=True)
class _DefaultValue:
    value: Any


_LITERAL_DEFAULT_METADATA = "nshconfig_literal_default"


Stack = tuple[_Frame, ...]
_STACK: ContextVar[Stack] = ContextVar("nshconfig_validation_stack", default=())
_ISOLATED_SOURCES: ContextVar[dict[int, BaseModel]] = ContextVar(
    "nshconfig_isolated_sources",
    default={},
)
_RECORD_LOADING: ContextVar[bool] = ContextVar(
    "nshconfig_record_loading",
    default=False,
)


@contextmanager
def _record_load_scope() -> Iterator[None]:
    """Mark trusted record reconstruction outside user-visible validator context."""

    token = _RECORD_LOADING.set(True)
    try:
        yield
    finally:
        _RECORD_LOADING.reset(token)


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


def _type_namespace_state(value: type) -> tuple[tuple[str, Any], ...]:
    """Return user-visible class state without descending into interpreter metadata."""

    namespace = type.__getattribute__(value, "__dict__")
    return tuple(
        (name, item)
        for name, item in namespace.items()
        if isinstance(name, str) and not name.startswith("__")
    )


def _assert_no_concealed_lifecycle(
    value: Any,
    path: str,
    active: set[int],
) -> None:
    """Inspect atomic state only for values that violate lifecycle boundaries."""

    from .config import Config

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
    if isinstance(value, Config):
        raise PydanticCustomError(
            "nshconfig_opaque_config",
            "Config value at {path} is hidden inside an atomic object; move it into a "
            "directly annotated Config field",
            {"path": path},
        )
    if is_known_immutable_atom(value):
        return

    identity = id(value)
    if identity in active:
        # Internal cycles are implementation detail of an atomic value. They are
        # not part of Config's structural graph and need no lifecycle scope.
        return

    children: tuple[tuple[Any, Any], ...]
    if isinstance(value, type):
        children = _type_namespace_state(value)
    elif isinstance(value, BaseModel):
        data = object.__getattribute__(value, "__dict__")
        children = tuple((name, item) for name, item in data.items())
        extra = object.__getattribute__(value, "__pydantic_extra__") or {}
        private = object.__getattribute__(value, "__pydantic_private__") or {}
        children += tuple((f"<extra:{name}>", item) for name, item in extra.items())
        children += tuple((f"<private:{name}>", item) for name, item in private.items())
    elif is_dataclass(value) and not isinstance(value, type):
        children = tuple(_dataclass_state_items(cast(Any, value)).items())
    elif type(value) is dict:
        children = tuple(
            item
            for key, child in cast(dict[Any, Any], value).items()
            for item in (("<key>", key), (key, child))
        )
    elif type(value) in {list, tuple, set, frozenset, deque}:
        children = tuple(enumerate(cast(Any, value)))
    else:
        children = _opaque_state_items(value)
        if not children and callable(value):
            raise PydanticCustomError(
                "nshconfig_opaque_callable",
                "opaque callable {callable} at {path} cannot be proven free of concealed "
                "lifecycle state",
                {"callable": type(value).__qualname__, "path": path},
            )

    if not children:
        return
    active.add(identity)
    try:
        for name, item in children:
            _assert_no_concealed_lifecycle(
                item,
                _stored_path(path, name),
                active,
            )
    finally:
        active.remove(identity)


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
            from_default=False,
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
    if "default" in updated:
        default = updated.pop("default")
        if not isinstance(default, Interp):
            metadata = dict(updated.get("metadata") or {})
            metadata[_LITERAL_DEFAULT_METADATA] = default
            updated["metadata"] = metadata
        updated["default_factory"] = partial(_literal_value_default, name, default)
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


def _mark_default_used(name: str) -> _Frame:
    stack = _STACK.get()
    assert stack, "Config default ran without its validation scope"
    frame = stack[-1]
    frame.defaults_used.add(name)
    return frame


def _literal_value_default(name: str, default: Any) -> Any:
    frame = _mark_default_used(name)
    if isinstance(default, Interp):
        return _resolve_literal_marker(frame, name, default, from_default=True)
    return default


def _literal_factory_default(
    name: str,
    factory: Any,
    takes_data: bool,
    validated_data: dict[str, Any] | None = None,
) -> Any:
    frame = _mark_default_used(name)
    value = factory(validated_data) if takes_data else factory()
    if not isinstance(value, Interp):
        return value
    return _resolve_literal_marker(frame, name, value, from_default=True)


def _prepare_tracked_default(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    updated = dict(schema)
    if "default" in updated:
        updated["default"] = _DefaultValue(updated["default"])
        return updated
    factory = updated.get("default_factory")
    if factory is not None:
        takes_data = bool(updated.get("default_factory_takes_data"))
        updated["default_factory"] = partial(
            _tracked_default_factory,
            name,
            factory,
            takes_data,
        )
    return updated


def _tracked_default_factory(
    name: str,
    factory: Any,
    takes_data: bool,
    validated_data: dict[str, Any] | None = None,
) -> Any:
    _mark_default_used(name)
    return factory(validated_data) if takes_data else factory()


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
                    else _prepare_tracked_default(name, schema)
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
        return core_schema.no_info_after_validator_function(
            capture_built_instance,
            cast("core_schema.CoreSchema", updated),
        ), True

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


def capture_built_instance(value: Any) -> Any:
    """Remember Pydantic's constructed instance before outer model validators run."""
    stack = _STACK.get()
    assert stack, "Config model construction ran without its validation scope"
    assert isinstance(value, BaseModel), "Pydantic model schema returned a non-model"
    frame = stack[-1]
    _assert_published_unchanged(frame, model_boundary=True)
    frame.built_instance = value
    return value


def capture_model_input(
    literal_fields: frozenset[str],
    value: Any,
    info: Any,
) -> Any:
    """Remember and de-alias input after model-before validators have transformed it."""
    stack = _STACK.get()
    assert stack, "Config model fields ran without their validation scope"
    frame = stack[-1]
    original = frame.raw
    expanded = _expand_builtin_input(value, set(), (), mapping_envelope=True)
    if isinstance(expanded, Mapping):
        _assert_invariant_mapping_input(expanded, frame.cls)
        discarded = [
            name
            for name in frame.cls.__pydantic_fields__
            if _provided(original, frame.cls, name)
            and not _provided(expanded, frame.cls, name)
        ]
        if discarded:
            raise PydanticCustomError(
                "nshconfig_ignored_input",
                "validation policy ignored provided Config fields: {fields}",
                {"fields": ", ".join(discarded)},
            )
    frame.raw = expanded
    frame.validation_context = info.context
    if literal_fields and isinstance(expanded, Mapping):
        ordered = dict(expanded)
        frame.raw = ordered
        return _OrderedInput(ordered, frame, literal_fields)
    return expanded


def _expand_builtin_input(
    value: Any,
    active: set[int],
    path: Path,
    *,
    mapping_envelope: bool = False,
    reject_custom: bool = True,
) -> Any:
    """Copy each built-in container occurrence and reject input cycles."""
    if mapping_envelope and isinstance(value, Mapping) and type(value) is not dict:
        value = dict(value)
    if (
        reject_custom
        and isinstance(value, (Iterable, AsyncIterable, Awaitable))
        and type(value) not in {dict, frozenset, list, set, tuple}
        and type(value) not in {bytearray, bytes, memoryview, range, str}
        and not isinstance(value, (BaseModel, Enum))
        and not (is_dataclass(value) and not isinstance(value, type))
    ):
        raise PydanticCustomError(
            "nshconfig_unsupported_container",
            "unsupported or lazy container {container} is not legal input at {path}; "
            "materialize an exact built-in value first",
            {"container": type(value).__qualname__, "path": _render_path(path)},
        )
    if type(value) not in {dict, list, tuple, set, frozenset}:
        return value
    identity = id(value)
    if identity in active:
        raise PydanticCustomError(
            "nshconfig_cycle",
            "configuration input must be acyclic; cycle found at {path}",
            {"path": _render_path(path)},
        )
    active.add(identity)
    try:
        if type(value) is dict:
            return {
                _expand_builtin_input(
                    key,
                    active,
                    (*path, "<key>"),
                    reject_custom=reject_custom,
                ): _expand_builtin_input(
                    item,
                    active,
                    (
                        *path,
                        key if is_path_part(key) else safe_repr(key, limit=80),
                    ),
                    reject_custom=reject_custom,
                )
                for key, item in value.items()
            }
        if type(value) is list:
            return [
                _expand_builtin_input(
                    item,
                    active,
                    (*path, index),
                    reject_custom=reject_custom,
                )
                for index, item in enumerate(value)
            ]
        if type(value) is tuple:
            return tuple(
                _expand_builtin_input(
                    item,
                    active,
                    (*path, index),
                    reject_custom=reject_custom,
                )
                for index, item in enumerate(value)
            )
        if type(value) is set:
            return {
                _expand_builtin_input(
                    item,
                    active,
                    (*path, index),
                    reject_custom=reject_custom,
                )
                for index, item in enumerate(value)
            }
        frozen_value = cast(frozenset[Any], value)
        return frozenset(
            _expand_builtin_input(
                item,
                active,
                (*path, index),
                reject_custom=reject_custom,
            )
            for index, item in enumerate(frozen_value)
        )
    finally:
        active.remove(identity)


def build_config_schema(
    cls: type[BaseModel], source: Any, handler: Any
) -> core_schema.CoreSchema:
    """Wrap this Config's complete field pipelines and all serializer entry points."""
    from .config import _validate_config_class

    _validate_config_class(cls)
    schema = handler(source)
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
        metadata = value.get("metadata")
        literal_default = (
            metadata.get(_LITERAL_DEFAULT_METADATA, PydanticUndefined)
            if isinstance(metadata, dict)
            else PydanticUndefined
        )
        for key, item in value.items():
            if literal_default is not PydanticUndefined and key in {
                "default_factory",
                "default_factory_takes_data",
            }:
                changed = True
                continue
            if key == "metadata" and literal_default is not PydanticUndefined:
                changed = True
                cleaned_metadata = dict(item)
                cleaned_metadata.pop(_LITERAL_DEFAULT_METADATA, None)
                if cleaned_metadata:
                    output[key] = cleaned_metadata
                continue
            if key == "default" and isinstance(item, _DefaultValue):
                changed = True
                item = item.value
            if key == "default" and isinstance(item, Interp):
                changed = True
                continue
            if key in {"cls", "function", "metadata", "serialization"}:
                updated = item
            else:
                updated = _without_marker_defaults(item, active)
            changed = changed or updated is not item
            output[key] = updated
        if literal_default is not PydanticUndefined:
            output["default"] = literal_default
        return output if changed else value
    finally:
        active.remove(identity)


def serialize_config(value: Any, handler: Any, info: Any) -> Any:
    """Reject drafts even through TypeAdapter and nested Pydantic serializers."""
    if not isinstance(state_of(value), FinalState):
        raise PydanticSerializationError(
            "nshconfig values are serializable only after validation has completed"
        )
    return handler(value)


def _input_value(raw: Any, cls: type[BaseModel], name: str) -> Any:
    if isinstance(raw, cls) and name in raw.__pydantic_fields_set__:
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


def _assert_invariant_mapping_input(
    raw: Mapping[Any, Any], cls: type[BaseModel]
) -> None:
    accepted: set[Any] = set()
    for name in cls.__pydantic_fields__:
        candidates = _input_candidates(cls, name)
        matches = 0
        for candidate in candidates:
            path = candidate.path if isinstance(candidate, AliasPath) else [candidate]
            if path:
                accepted.add(path[0])
            if _lookup_input_candidate(raw, candidate) is not PydanticUndefined:
                matches += 1
        if matches > 1:
            raise PydanticCustomError(
                "nshconfig_duplicate_input",
                "field {field} was provided through multiple names or aliases",
                {"field": name},
            )
    unknown = [key for key in raw if key not in accepted]
    if unknown:
        raise PydanticCustomError(
            "nshconfig_extra_final",
            "Extra inputs are not permitted: {keys}",
            {"keys": ", ".join(safe_repr(key, limit=80) for key in unknown)},
        )


def _evaluate_marker(
    frame: _Frame,
    name: str,
    marker: Interp,
    validation_context: Any,
) -> tuple[Any, list[Any]]:
    del validation_context
    if _RECORD_LOADING.get():
        raise PydanticCustomError(
            "nshconfig_record_interpolation",
            "run-record loading cannot execute interpolation at {path}",
            {"path": _render_path((*frame.path, name))},
        )

    reads: list[Any] = []
    stack = _STACK.get()
    from .interp import _CAPABILITY, _InterpolationCapability

    capability = _InterpolationCapability()
    capability_token = _CAPABILITY.set(capability)
    read_token = _READS.set(reads)
    model_path_token = _MODEL_PATHS.set({})
    stack_token = _STACK.set(())
    try:
        value = unwrap_view(marker.fn(Context._from_stack(stack, capability)))
    except Exception as error:
        path = _render_path((*frame.path, name))
        raise PydanticCustomError(
            "nshconfig_interpolation",
            "cannot interpolate {path} using {marker}: {error}",
            {
                "path": path,
                "marker": repr(marker),
                "error": safe_exception_text(error),
            },
        ) from error
    finally:
        capability.active = False
        _STACK.reset(stack_token)
        _MODEL_PATHS.reset(model_path_token)
        _READS.reset(read_token)
        _CAPABILITY.reset(capability_token)
    return value, reads


def _resolve_literal_marker(
    frame: _Frame,
    name: str,
    marker: Interp,
    *,
    from_default: bool,
) -> Any:
    _assert_published_unchanged(frame)
    try:
        value, reads = _evaluate_marker(
            frame,
            name,
            marker,
            frame.validation_context,
        )
    except PydanticCustomError as error:
        raise CoreValidationError.from_exception_data(
            frame.cls.__name__,
            [{"type": error, "loc": (name,), "input": marker}],
        ) from error

    assert name not in frame.pending_interpolations, (
        f"Literal field {name!r} resolved twice"
    )
    frame.active_name = name
    frame.active_raw = value
    frame.active_child = None
    frame.active_child_index = 0
    frame.pending_interpolations[name] = (marker, reads, from_default)
    return value


def resolve_field(value: Any, info: Any) -> Any:
    """Resolve a marker before Pydantic's native field pipeline."""
    stack = _STACK.get()
    assert stack, "Config field validation ran without its model scope"
    frame = stack[-1]
    name = info.field_name
    assert isinstance(name, str), "Config field wrapper did not receive a field name"
    _assert_published_unchanged(frame)
    if isinstance(value, _DefaultValue):
        frame.defaults_used.add(name)
        value = value.value

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
    reads: list[Any] = []
    if marker is not None:
        value, reads = _evaluate_marker(frame, name, marker, info.context)

    frame.active_name = name
    frame.active_raw = value
    frame.active_child = None
    frame.active_child_index = 0
    if marker is not None:
        frame.pending_interpolations[name] = (
            marker,
            reads,
            not _provided(frame.raw, frame.cls, name),
        )
    return value


def capture_field(value: Any, info: Any) -> Any:
    """Publish a fully canonical value after Pydantic's complete field pipeline."""
    stack = _STACK.get()
    assert stack, "Config field validation ran without its model scope"
    frame = stack[-1]
    name = info.field_name
    assert isinstance(name, str), "Config field wrapper did not receive a field name"
    _capture_field(frame, name, value)
    pending = frame.pending_interpolations.pop(name, None)
    if pending is not None:
        marker, reads, from_default = pending
        frame.interpolations[name] = interpolation_event(
            marker,
            value,
            reads,
            from_default=from_default,
        )
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
    frame.snapshots[name] = semantic_snapshot(value)


def _assert_published_unchanged(frame: _Frame, *, model_boundary: bool = False) -> None:
    """Require canonical fields to remain fixed after they are published."""
    for name, snapshot in frame.snapshots.items():
        current = frame.values.get(name, PydanticUndefined)
        if semantic_snapshot(current) != snapshot:
            raise PydanticCustomError(
                "nshconfig_model_mutation"
                if model_boundary
                else "nshconfig_field_mutation",
                (
                    "model-level hooks changed {field}; normalize interpolation sources "
                    "in field validators"
                    if model_boundary
                    else "validation mutated already-published field {field}"
                ),
                {"field": _render_path((*frame.path, name))},
            )


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
        # validation ordinal as a temporary container location. Provenance is
        # rebased against the completed final graph after validation.
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


def _config_fields_set_snapshot(
    value: Any,
    path: Path = (),
    active: set[int] | None = None,
) -> dict[Path, frozenset[str]]:
    """Capture explicit-field metadata for every reachable Config node."""

    from .config import Config

    if active is None:
        active = set()
    output: dict[Path, frozenset[str]] = {}
    identity = id(value)
    if isinstance(value, Config):
        if identity in active:
            return output
        fields_set = object.__getattribute__(value, "__pydantic_fields_set__")
        if not isinstance(fields_set, set):
            raise PydanticCustomError(
                "nshconfig_invalid_metadata",
                "Config at {path} has invalid field-set metadata",
                {"path": _render_path(path)},
            )
        output[path] = frozenset(fields_set)
        active.add(identity)
        try:
            data = object.__getattribute__(value, "__dict__")
            for name in type(value).__pydantic_fields__:
                if name in data:
                    output.update(
                        _config_fields_set_snapshot(
                            data[name],
                            (*path, name),
                            active,
                        )
                    )
        finally:
            active.remove(identity)
        return output
    if type(value) is dict:
        if identity in active:
            return output
        active.add(identity)
        try:
            for key, item in value.items():
                part: PathPart = key if is_path_part(key) else safe_repr(key, limit=80)
                output.update(_config_fields_set_snapshot(item, (*path, part), active))
        finally:
            active.remove(identity)
        return output
    if type(value) in {list, tuple}:
        if identity in active:
            return output
        active.add(identity)
        try:
            for index, item in enumerate(value):
                output.update(_config_fields_set_snapshot(item, (*path, index), active))
        finally:
            active.remove(identity)
    return output


def _unsafe_revalidation_atom(value: Any, path: Path) -> PydanticCustomError:
    return PydanticCustomError(
        "nshconfig_revalidation_unsafe_atom",
        "cannot safely revalidate identity-bearing atomic value {atom} at {path}; "
        "rebuild from a draft or mapping instead",
        {"atom": type(value).__qualname__, "path": _render_path(path)},
    )


def _isolate_validation_value(
    value: Any,
    isolated: dict[int, BaseModel],
    active: set[int],
    path: Path,
    *,
    protect_source: bool,
) -> Any:
    """Clone reusable finals without invoking user copy or rendering hooks."""

    from .config import Config

    if isinstance(value, Config) and isinstance(state_of(value), FinalState):
        if id(value) in isolated:
            return value
        return _clone_reused_final(value, isolated, active, path)

    value_type = type(value)
    if value_type is bytearray:
        return bytearray(cast(bytearray, value)) if protect_source else value
    if value_type not in {dict, list, tuple, set, frozenset}:
        if protect_source and not is_known_immutable_atom(value):
            raise _unsafe_revalidation_atom(value, path)
        return value

    identity = id(value)
    if identity in active:
        raise PydanticCustomError(
            "nshconfig_cycle",
            "configuration input must be acyclic; cycle found at {path}",
            {"path": _render_path(path)},
        )
    active.add(identity)
    try:
        if value_type is dict:
            dictionary = cast(dict[Any, Any], value)
            return {
                _isolate_validation_value(
                    key,
                    isolated,
                    active,
                    (*path, "<key>"),
                    protect_source=protect_source,
                ): _isolate_validation_value(
                    item,
                    isolated,
                    active,
                    (
                        *path,
                        key if is_path_part(key) else safe_repr(key, limit=80),
                    ),
                    protect_source=protect_source,
                )
                for key, item in dictionary.items()
            }
        if value_type is list:
            sequence = cast(list[Any], value)
            return [
                _isolate_validation_value(
                    item,
                    isolated,
                    active,
                    (*path, index),
                    protect_source=protect_source,
                )
                for index, item in enumerate(sequence)
            ]
        if value_type is tuple:
            sequence_tuple = cast(tuple[Any, ...], value)
            return tuple(
                _isolate_validation_value(
                    item,
                    isolated,
                    active,
                    (*path, index),
                    protect_source=protect_source,
                )
                for index, item in enumerate(sequence_tuple)
            )
        if value_type is set:
            value_set = cast(set[Any], value)
            return {
                _isolate_validation_value(
                    item,
                    isolated,
                    active,
                    (*path, index),
                    protect_source=protect_source,
                )
                for index, item in enumerate(value_set)
            }
        frozen_value = cast(frozenset[Any], value)
        return frozenset(
            _isolate_validation_value(
                item,
                isolated,
                active,
                (*path, index),
                protect_source=protect_source,
            )
            for index, item in enumerate(frozen_value)
        )
    finally:
        active.remove(identity)


def _clone_reused_final(
    source: BaseModel,
    isolated: dict[int, BaseModel],
    active: set[int],
    path: Path,
) -> BaseModel:
    identity = id(source)
    if identity in active:
        raise PydanticCustomError(
            "nshconfig_cycle",
            "configuration input must be acyclic; cycle found at {path}",
            {"path": _render_path(path)},
        )
    active.add(identity)
    clone = object.__new__(type(source))
    isolated[id(clone)] = source
    try:
        data = object.__getattribute__(source, "__dict__")
        cloned_data = {
            name: _isolate_validation_value(
                item,
                isolated,
                active,
                (*path, name),
                protect_source=True,
            )
            for name, item in data.items()
        }
        fields_set = object.__getattribute__(source, "__pydantic_fields_set__")
        if not isinstance(fields_set, set):
            raise PydanticCustomError(
                "nshconfig_invalid_metadata",
                "Config at {path} has invalid field-set metadata",
                {"path": _render_path(path)},
            )
        extra = object.__getattribute__(source, "__pydantic_extra__")
        cloned_extra = (
            None
            if extra is None
            else _isolate_validation_value(
                extra,
                isolated,
                active,
                (*path, "<extra>"),
                protect_source=True,
            )
        )
        private = object.__getattribute__(source, "__pydantic_private__")
        cloned_private: dict[str, Any] | None
        if private is None:
            cloned_private = None
        elif not isinstance(private, dict):
            raise PydanticCustomError(
                "nshconfig_invalid_metadata",
                "Config at {path} has invalid private-attribute storage",
                {"path": _render_path(path)},
            )
        else:
            cloned_private = {}
            for name, item in private.items():
                if isinstance(item, FinalState):
                    cloned_private[name] = FinalState(dict(item.events))
                elif name in RESERVED_PRIVATE_KEYS:
                    cloned_private[name] = item
                else:
                    cloned_private[name] = _isolate_validation_value(
                        item,
                        isolated,
                        active,
                        (*path, name),
                        protect_source=True,
                    )

        object.__setattr__(clone, "__dict__", cloned_data)
        object.__setattr__(clone, "__pydantic_fields_set__", set(fields_set))
        object.__setattr__(clone, "__pydantic_extra__", cloned_extra)
        object.__setattr__(clone, "__pydantic_private__", cloned_private)
        return clone
    except Exception:
        isolated.pop(id(clone), None)
        raise
    finally:
        active.remove(identity)


def interpolation_scope(
    cls: type[BaseModel], value: Any, handler: Any, info: Any
) -> Any:
    """Create one isolation registry around an entire Config validation graph."""

    token = None
    if not _STACK.get():
        token = _ISOLATED_SOURCES.set({})
    try:
        return _interpolation_scope(cls, value, handler, info)
    finally:
        if token is not None:
            _ISOLATED_SOURCES.reset(token)


def _interpolation_scope(
    cls: type[BaseModel], value: Any, handler: Any, info: Any
) -> Any:
    """Maintain Config ancestry around Pydantic's native model pipeline."""
    if not isinstance(value, (Mapping, cls)):
        raise PydanticCustomError(
            "nshconfig_attribute_input",
            "Config validation accepts mappings and Config instances, not attribute-based "
            "objects",
        )
    if is_draft(value):
        raise PydanticCustomError(
            "nshconfig_draft_input",
            "a draft cannot be validated as a value; finalize the root draft",
        )
    if isinstance(value, Mapping):
        value = _expand_builtin_input(
            value,
            set(),
            (),
            mapping_envelope=True,
            reject_custom=False,
        )
    value = _isolate_validation_value(
        value,
        _ISOLATED_SOURCES.get(),
        set(),
        (),
        protect_source=False,
    )

    stack = _STACK.get()
    direct_child = False
    if stack and (child := _child_path(stack[-1], value, cls)) is not None:
        path, direct_child = child
        parent_stack = stack
    else:
        path = ()
        parent_stack = ()

    reused_source = None
    if isinstance(value, BaseModel) and isinstance(state_of(value), FinalState):
        reused_source = _ISOLATED_SOURCES.get().get(id(value), value)
    historical_root = any(
        ancestor.reused_source is not None for ancestor in parent_stack
    )
    if (
        parent_stack
        and reused_source is not None
        and not historical_root
        and (reason := final_reuse_error(reused_source)) is not None
    ):
        raise PydanticCustomError(
            "nshconfig_final_reuse",
            "cannot reuse provenance-bearing Config final at {path}: {reason}",
            {"path": _render_path(path), "reason": reason},
        )

    reused_source_snapshot = (
        semantic_snapshot(reused_source) if reused_source is not None else None
    )
    reused_fields_set = (
        _config_fields_set_snapshot(reused_source)
        if reused_source is not None
        else None
    )
    frame = _Frame(cls, value, path, reused_source=reused_source)
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

    if not isinstance(output, cls) or output is not frame.built_instance:
        raise PydanticCustomError(
            "nshconfig_model_replacement",
            "Config model validators must return the validated {model} instance",
            {"model": cls.__name__},
        )
    if reused_source is not None:
        private = object.__getattribute__(output, "__pydantic_private__")
        if isinstance(private, dict):
            for key in RESERVED_PRIVATE_KEYS:
                private.pop(key, None)
    data = object.__getattribute__(output, "__dict__")
    if not isinstance(data, dict):
        raise PydanticCustomError(
            "nshconfig_invalid_metadata",
            "validated Config {model} has invalid instance storage",
            {"model": cls.__name__},
        )
    field_names = set(cls.__pydantic_fields__)
    unexpected = set(data) - field_names
    if unexpected:
        raise PydanticCustomError(
            "nshconfig_undeclared_state",
            "Config hooks stored undeclared instance attributes on {model}: {names}",
            {"model": cls.__name__, "names": ", ".join(sorted(map(str, unexpected)))},
        )
    extra = object.__getattribute__(output, "__pydantic_extra__")
    if extra is not None and (not isinstance(extra, dict) or extra):
        raise PydanticCustomError(
            "nshconfig_extra_state",
            "Config hooks populated forbidden extra fields on {model}",
            {"model": cls.__name__},
        )
    fields_set = object.__getattribute__(output, "__pydantic_fields_set__")
    if not isinstance(fields_set, set) or not fields_set <= field_names:
        raise PydanticCustomError(
            "nshconfig_invalid_metadata",
            "Config hooks corrupted field-set metadata on {model}",
            {"model": cls.__name__},
        )
    if isinstance(value, Mapping):
        ignored = [
            name
            for name in cls.__pydantic_fields__
            if _provided(value, cls, name) and name not in fields_set
        ]
        if ignored:
            raise PydanticCustomError(
                "nshconfig_ignored_input",
                "validation policy ignored provided Config fields: {fields}",
                {"fields": ", ".join(ignored)},
            )
    substituted = [name for name in frame.defaults_used if _provided(value, cls, name)]
    if substituted:
        raise PydanticCustomError(
            "nshconfig_default_substitution",
            "validation replaced explicit Config input with defaults for fields: {fields}",
            {"fields": ", ".join(sorted(substituted))},
        )
    private = object.__getattribute__(output, "__pydantic_private__")
    if private is not None and not isinstance(private, dict):
        raise PydanticCustomError(
            "nshconfig_invalid_metadata",
            "Config hooks corrupted private-attribute storage on {model}",
            {"model": cls.__name__},
        )
    if isinstance(private, dict) and RESERVED_PRIVATE_KEYS & private.keys():
        raise PydanticCustomError(
            "nshconfig_reserved_state",
            "Config hooks populated reserved lifecycle state on {model}",
            {"model": cls.__name__},
        )
    missing = [name for name in cls.__pydantic_fields__ if name not in data]
    if missing:
        raise PydanticCustomError(
            "nshconfig_incomplete_final",
            "validated Config {model} omitted declared fields: {fields}",
            {"model": cls.__name__, "fields": ", ".join(missing)},
        )
    for name, snapshot in frame.snapshots.items():
        actual = object.__getattribute__(output, "__dict__").get(
            name, PydanticUndefined
        )
        if semantic_snapshot(actual) != snapshot:
            raise PydanticCustomError(
                "nshconfig_model_mutation",
                "model-level hooks changed {field}; normalize interpolation sources in field validators",
                {"field": _render_path((*path, name))},
            )
    if reused_source is not None:
        assert reused_source_snapshot is not None
        assert reused_fields_set is not None
        if semantic_snapshot(reused_source) != reused_source_snapshot or (
            _config_fields_set_snapshot(reused_source) != reused_fields_set
        ):
            raise PydanticCustomError(
                "nshconfig_revalidation_mutation",
                "revalidation mutated the existing {model} final",
                {"model": cls.__name__},
            )
        if semantic_snapshot(output) != reused_source_snapshot:
            raise PydanticCustomError(
                "nshconfig_revalidation_change",
                "revalidation changed the existing {model} final",
                {"model": cls.__name__},
            )
        if _config_fields_set_snapshot(output) != reused_fields_set:
            raise PydanticCustomError(
                "nshconfig_revalidation_change",
                "revalidation changed explicit-field metadata on the existing {model} final",
                {"model": cls.__name__},
            )
    if reused_source is not None:
        set_final_state(output, {})
    attach_interpolations(output, frame.interpolations, frame.path)
    if (
        parent_stack
        and reused_source is not None
        and not any(ancestor.reused_source is not None for ancestor in parent_stack)
    ):
        assert reused_source_snapshot is not None
        assert reused_fields_set is not None
        parent_stack[0].reused_sources.append(
            (
                reused_source,
                output,
                path,
                reused_source_snapshot,
                reused_fields_set,
            )
        )
    if not parent_stack:
        _assert_final_graph(output, cls.__name__, set(), {}, {}, cls)
        for (
            source,
            target,
            validation_path,
            source_snapshot,
            source_fields_set,
        ) in frame.reused_sources:
            if semantic_snapshot(target) != source_snapshot or (
                _config_fields_set_snapshot(target) != source_fields_set
            ):
                raise PydanticCustomError(
                    "nshconfig_revalidation_change",
                    "validation changed reused final at {path}",
                    {"path": _render_path(validation_path)},
                )
            merge_reused_final_provenance(source, output, validation_path)
        if reused_source is not None:
            copy_provenance(reused_source, output)
    return output


def _assert_final_graph(
    value: Any,
    path: str,
    active: set[int],
    seen_mutable: dict[int, str],
    seen_origins: dict[tuple[Any, ...], str],
    annotation: Any,
    discriminator: Any = None,
) -> None:
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
    if is_known_immutable_atom(value):
        return

    if (
        isinstance(value, (Iterable, AsyncIterable, Awaitable))
        and type(value) not in {dict, frozenset, list, set, tuple}
        and type(value) not in {bytearray, bytes, memoryview, range, str}
        and not isinstance(value, (BaseModel, Enum))
        and not (is_dataclass(value) and not isinstance(value, type))
    ):
        raise PydanticCustomError(
            "nshconfig_unsupported_container",
            "unsupported or lazy container {container} is not legal at {path}; "
            "materialize it as an exact built-in first",
            {"container": type(value).__qualname__, "path": path},
        )
    if (
        isinstance(value, Collection)
        and type(value) not in {dict, frozenset, list, set, tuple}
        and type(value) not in {bytearray, bytes, memoryview, range, str}
        and not isinstance(value, Enum)
        and not isinstance(value, BaseModel)
        and not (is_dataclass(value) and not isinstance(value, type))
    ):
        raise PydanticCustomError(
            "nshconfig_unsupported_container",
            "unsupported or lazy container {container} is not legal at {path}; use an exact "
            "built-in dict, list, tuple, set, or frozenset",
            {"container": type(value).__qualname__, "path": path},
        )

    from .config import Config

    if not isinstance(value, Config) and type(value) not in {
        dict,
        frozenset,
        list,
        set,
        tuple,
    }:
        _assert_no_concealed_lifecycle(value, path, set())
        return

    identity = id(value)
    if identity in active:
        raise PydanticCustomError(
            "nshconfig_cycle",
            "configuration values must be acyclic; cycle found at {path}",
            {"path": path},
        )
    structured = True
    from .finalize import (
        _concrete_config_type,
        _contains_config,
        _mapping_annotations,
        _mapping_value_annotation,
        _select_annotation,
        _sequence_annotations,
        _set_annotation,
    )

    try:
        selected_annotation, selected_discriminator = _select_annotation(
            value,
            annotation,
            path,
            discriminator=discriminator,
        )
    except (TypeError, ValueError) as error:
        raise PydanticCustomError(
            "nshconfig_graph_annotation",
            "configuration value at {path} does not match one unambiguous structural annotation: {error}",
            {"path": path, "error": safe_exception_text(error)},
        ) from error
    if isinstance(value, BaseModel):
        assert isinstance(value, Config)
        if _concrete_config_type(selected_annotation) is not type(value):
            raise PydanticCustomError(
                "nshconfig_opaque_config",
                "Config value at {path} is hidden under an opaque or incompatible annotation; "
                "declare its exact Config structure",
                {"path": path},
            )
        if not isinstance(state_of(value), FinalState):
            raise PydanticCustomError(
                "nshconfig_unvalidated_node",
                "Config value at {path} bypassed its validation scope",
                {"path": path},
            )
        extra = object.__getattribute__(value, "__pydantic_extra__") or {}
        if extra:
            raise PydanticCustomError(
                "nshconfig_extra_final",
                "Config value at {path} contains undeclared extra fields: {fields}",
                {"path": path, "fields": ", ".join(sorted(map(str, extra)))},
            )
        private = object.__getattribute__(value, "__pydantic_private__") or {}
        origin = private.get(ORIGIN_PATH_KEY)
        if isinstance(origin, tuple):
            if origin in seen_origins:
                raise PydanticCustomError(
                    "nshconfig_duplicate_lineage",
                    "Config value at {path} duplicates validation lineage from {first_path}",
                    {"path": path, "first_path": seen_origins[origin]},
                )
            seen_origins[origin] = path
    mutable_structure = isinstance(value, (BaseModel, dict, list, set))
    if mutable_structure and identity in seen_mutable:
        raise PydanticCustomError(
            "nshconfig_alias",
            "mutable configuration value at {path} is also referenced from {first_path}; "
            "return independent values from validators",
            {"path": path, "first_path": seen_mutable[identity]},
        )
    if mutable_structure:
        seen_mutable[identity] = path
    if structured:
        active.add(identity)
    try:
        if isinstance(value, BaseModel):
            data = object.__getattribute__(value, "__dict__")
            model_fields = type(value).__pydantic_fields__
            for name, model_field in model_fields.items():
                if name in data:
                    _assert_final_graph(
                        data[name],
                        f"{path}.{name}",
                        active,
                        seen_mutable,
                        seen_origins,
                        model_field.annotation,
                        model_field.discriminator,
                    )
            for name, item in data.items():
                if name not in model_fields:
                    _assert_final_graph(
                        item,
                        _stored_path(path, name),
                        active,
                        seen_mutable,
                        seen_origins,
                        Any,
                    )
            extra = object.__getattribute__(value, "__pydantic_extra__") or {}
            for name, item in extra.items():
                _assert_final_graph(
                    item,
                    f"{path}[{safe_repr(name, limit=80)}]",
                    active,
                    seen_mutable,
                    seen_origins,
                    Any,
                )
            private = object.__getattribute__(value, "__pydantic_private__") or {}
            internal_state = state_of(value) is not None
            for name, item in private.items():
                if internal_state and name in RESERVED_PRIVATE_KEYS:
                    continue
                _assert_final_graph(
                    item,
                    f"{path}.{name}",
                    active,
                    seen_mutable,
                    seen_origins,
                    Any,
                )
        elif isinstance(value, Mapping):
            key_annotation, item_annotation = _mapping_annotations(selected_annotation)
            for key, item in value.items():
                if type(key) not in {str, int} and _contains_config(item, set()):
                    raise PydanticCustomError(
                        "nshconfig_structural_mapping_key",
                        "mapping at {path} contains Config structure below unsupported key "
                        "{key}; use exact str or int keys",
                        {"path": path, "key": safe_repr(key, limit=80)},
                    )
                _assert_final_graph(
                    key,
                    f"{path}.<key>",
                    active,
                    seen_mutable,
                    seen_origins,
                    key_annotation,
                )
                _assert_final_graph(
                    item,
                    f"{path}[{safe_repr(key, limit=80)}]",
                    active,
                    seen_mutable,
                    seen_origins,
                    _mapping_value_annotation(
                        selected_annotation,
                        key,
                        item_annotation,
                    ),
                )
        elif isinstance(value, (list, tuple)):
            item_annotations = _sequence_annotations(selected_annotation, len(value))
            for index, item in enumerate(value):
                _assert_final_graph(
                    item,
                    f"{path}[{index}]",
                    active,
                    seen_mutable,
                    seen_origins,
                    item_annotations[index],
                )
        elif isinstance(value, (set, frozenset)):
            item_annotation = _set_annotation(selected_annotation)
            for index, item in enumerate(value):
                _assert_final_graph(
                    item,
                    f"{path}[{index}]",
                    active,
                    seen_mutable,
                    seen_origins,
                    item_annotation,
                )
    finally:
        active.remove(identity)
