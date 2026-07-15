"""Mutable, incomplete Config drafts and provisional default materialization."""

import difflib
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from pydantic import AliasChoices, AliasPath, BaseModel
from pydantic_core import PydanticUndefined

from .annotations import unwrap_annotation
from .errors import DraftError, UnsetError
from .interp import Interp
from .semantic import is_known_immutable_atom
from .state import (
    STATE_KEY,
    DraftState,
    Recipe,
    copy_builtin_graph,
    draft_state,
    is_draft,
    valid_recipe,
    value_token,
)

if TYPE_CHECKING:
    from .config import Config

C = TypeVar("C", bound="Config")


def create_draft(cls: type[C], *, base: Recipe | None = None) -> C:
    """Create an incomplete mutable instance without running Pydantic hooks."""

    from .config import Config

    if not isinstance(cls, type) or not issubclass(cls, Config):
        raise TypeError("config_draft() expects a Config subclass")
    model = object.__new__(cls)
    object.__setattr__(model, "__dict__", {})
    object.__setattr__(model, "__pydantic_fields_set__", set())
    object.__setattr__(model, "__pydantic_extra__", None)
    object.__setattr__(
        model,
        "__pydantic_private__",
        {STATE_KEY: DraftState(base=base)},
    )
    return model


def set_field(obj: BaseModel, name: str, value: Any) -> None:
    """Store one unvalidated draft assignment."""

    fields = type(obj).__pydantic_fields__
    if name not in fields:
        hint = difflib.get_close_matches(name, fields, n=1)
        suffix = f"; did you mean {hint[0]!r}?" if hint else ""
        raise AttributeError(f"{type(obj).__name__} has no field {name!r}{suffix}")

    state = draft_state(obj)
    data = object.__getattribute__(obj, "__dict__")
    if isinstance(value, Interp):
        data.pop(name, None)
        state.pending[name] = value
    else:
        data[name] = value
        state.pending.pop(name, None)
    state.deleted.discard(name)
    state.materialized.pop(name, None)
    obj.__pydantic_fields_set__.add(name)


def delete_field(obj: BaseModel, name: str) -> bool:
    """Delete one declared draft field and reactivate its schema default."""

    if name not in type(obj).__pydantic_fields__:
        return False
    data = object.__getattribute__(obj, "__dict__")
    state = draft_state(obj)
    data.pop(name, None)
    state.pending.pop(name, None)
    state.materialized.pop(name, None)
    state.deleted.add(name)
    obj.__pydantic_fields_set__.discard(name)
    return True


def read_field(obj: BaseModel, name: str) -> Any:
    """Materialize a readable draft default or a required Config child."""

    model_field = type(obj).__pydantic_fields__[name]
    state = draft_state(obj)
    if name in state.pending:
        raise UnsetError(
            f"{type(obj).__name__}.{name} is pending interpolation; "
            "read it after config_finalize()"
        )

    base_value = _base_input(obj, name)
    if base_value is not PydanticUndefined and name not in state.deleted:
        value = base_value
    elif isinstance(model_field.default, Interp):
        raise UnsetError(
            f"{type(obj).__name__}.{name} is pending interpolation; "
            "read it after config_finalize()"
        )
    elif model_field.is_required():
        child_type = _concrete_config_type(model_field.annotation)
        if child_type is None:
            raise UnsetError(f"{type(obj).__name__}.{name} is not set on this draft")
        value = create_draft(child_type)
    else:
        value = _provisional_default(obj, name)

    path = f"{type(obj).__name__}.{name}"
    value = project_default(value, model_field.annotation, path)
    if isinstance(value, Interp):
        raise UnsetError(
            f"{path} is pending interpolation from its default factory; "
            "read it after config_finalize()"
        )
    if not _safe_to_observe(value, set()):
        raise UnsetError(
            f"{path} has an opaque provisional default that cannot be observed safely; "
            "assign an explicit value or read it after config_finalize()"
        )

    object.__getattribute__(obj, "__dict__")[name] = value
    state.materialized[name] = _draft_token(value)
    return value


def _provisional_default(obj: BaseModel, name: str) -> Any:
    data = object.__getattribute__(obj, "__dict__")
    state = draft_state(obj)
    validated_data: dict[str, Any] = {}
    for field_name, earlier_field in type(obj).__pydantic_fields__.items():
        if field_name == name:
            break
        if field_name in state.pending:
            continue
        if field_name in data and not isinstance(data[field_name], Interp):
            validated_data[field_name] = data[field_name]
            continue
        if field_name not in state.deleted:
            earlier_base = _base_input(obj, field_name)
            if earlier_base is not PydanticUndefined:
                validated_data[field_name] = earlier_base
                continue
        if earlier_field.is_required() or isinstance(earlier_field.default, Interp):
            continue
        earlier_default = earlier_field.get_default(
            call_default_factory=True,
            validated_data=validated_data,
        )
        if earlier_default is not PydanticUndefined and not isinstance(
            earlier_default, Interp
        ):
            validated_data[field_name] = earlier_default

    model_field = type(obj).__pydantic_fields__[name]
    try:
        value = model_field.get_default(
            call_default_factory=True,
            validated_data=validated_data,
        )
    except Exception as error:
        raise UnsetError(
            f"cannot materialize provisional default for "
            f"{type(obj).__name__}.{name}: {error}"
        ) from error
    if value is PydanticUndefined:
        raise UnsetError(f"{type(obj).__name__}.{name} has no usable draft default")
    return value


def _base_input(obj: BaseModel, name: str) -> Any:
    state = draft_state(obj)
    if state.base is None:
        return PydanticUndefined
    return _input_value(state.base.inputs, type(obj), name)


def _input_value(raw: Mapping[str, Any], cls: type[BaseModel], name: str) -> Any:
    model_field = cls.__pydantic_fields__[name]
    alias = model_field.validation_alias
    choices = alias.choices if isinstance(alias, AliasChoices) else [alias]
    candidates: list[str | AliasPath] = [
        choice for choice in choices if isinstance(choice, (str, AliasPath))
    ]
    candidates.append(name)
    for candidate in candidates:
        path = candidate.path if isinstance(candidate, AliasPath) else [candidate]
        value: Any = raw
        for part in path:
            try:
                value = value[part]
            except (KeyError, IndexError, TypeError):
                value = PydanticUndefined
                break
        if value is not PydanticUndefined:
            return value
    return PydanticUndefined


def project_default(
    value: Any,
    annotation: Any,
    path: str,
    active: set[int] | None = None,
    memo: dict[int, Any] | None = None,
) -> Any:
    """Copy one default-origin graph and turn Config values into fresh drafts."""

    from .config import Config
    from .finalize import (
        _mapping_annotations,
        _mapping_value_annotation,
        _contains_config_in_builtins,
        _select_annotation,
        _sequence_annotations,
    )

    if active is None:
        active = set()
    if memo is None:
        memo = {}
    is_container = type(value) in {dict, list, tuple, set, frozenset}
    identity = id(value)
    if is_container:
        if identity in active:
            raise UnsetError(f"{path} contains a cyclic provisional default")
        active.add(identity)
    try:
        selected, _ = _select_annotation(value, annotation, path, discriminator=None)
        if isinstance(value, Config):
            return _project_config_default(value, selected, path)
        if type(value) is dict:
            key_annotation, item_annotation = _mapping_annotations(selected)
            preserve_alias = not _contains_config_in_builtins(value, set())
            if identity in memo and preserve_alias:
                return memo[identity]
            output: dict[Any, Any] = {}
            if preserve_alias:
                memo[identity] = output
            for key, item in value.items():
                copied_key = copy_builtin_graph(key, memo)
                copied_item = project_default(
                    item,
                    _mapping_value_annotation(selected, key, item_annotation),
                    f"{path}[{key!r}]",
                    active,
                    memo,
                )
                output[copied_key] = copied_item
            return output
        if type(value) is list:
            annotations = _sequence_annotations(selected, len(value))
            preserve_alias = not _contains_config_in_builtins(value, set())
            if identity in memo and preserve_alias:
                return memo[identity]
            output_list: list[Any] = []
            if preserve_alias:
                memo[identity] = output_list
            output_list.extend(
                project_default(
                    item,
                    annotations[index],
                    f"{path}[{index}]",
                    active,
                    memo,
                )
                for index, item in enumerate(value)
            )
            return output_list
        if type(value) is tuple:
            annotations = _sequence_annotations(selected, len(value))
            preserve_alias = not _contains_config_in_builtins(value, set())
            if identity in memo and preserve_alias:
                return memo[identity]
            output_tuple = tuple(
                project_default(
                    item,
                    annotations[index],
                    f"{path}[{index}]",
                    active,
                    memo,
                )
                for index, item in enumerate(value)
            )
            if preserve_alias:
                memo[identity] = output_tuple
            return output_tuple
        if type(value) in {set, frozenset}:
            return copy_builtin_graph(value, memo)
        return value
    finally:
        if is_container:
            active.remove(identity)


def _project_config_default(value: "Config", selected: Any, path: str) -> "Config":
    from .finalize import _concrete_config_type

    if _concrete_config_type(selected) is not type(value):
        raise UnsetError(
            f"{path} contains a Config default but its annotation does not "
            "describe that Config structure: this position must name the exact "
            f"{type(value).__name__} type"
        )
    if is_draft(value):
        return value
    recipe = valid_recipe(value)
    if recipe is None:
        raise UnsetError(
            f"{path} uses a Config default without an intact constructor recipe"
        )
    return create_draft(type(value), base=recipe)


def field_was_edited(obj: BaseModel, name: str, value: Any) -> bool:
    """Return whether a materialized field must cross the validation boundary."""

    state = draft_state(obj)
    if name in obj.__pydantic_fields_set__ or name in state.deleted:
        return True
    baseline = state.materialized.get(name, PydanticUndefined)
    if baseline is not PydanticUndefined and _draft_token(value) != baseline:
        return True
    return has_user_input(value)


def has_user_input(value: Any, active: set[int] | None = None) -> bool:
    """Return whether a materialized draft subtree contains an edit."""

    if active is None:
        active = set()
    identity = id(value)
    if identity in active:
        return False
    if is_draft(value):
        state = draft_state(value)
        if value.__pydantic_fields_set__ or state.deleted or state.pending:
            return True
        active.add(identity)
        try:
            data = object.__getattribute__(value, "__dict__")
            return any(
                field_was_edited(value, name, data[name])
                for name in type(value).__pydantic_fields__
                if name in data
            )
        finally:
            active.remove(identity)
    if type(value) is dict:
        active.add(identity)
        try:
            return any(has_user_input(item, active) for item in value.values())
        finally:
            active.remove(identity)
    if type(value) in {list, tuple}:
        active.add(identity)
        try:
            return any(has_user_input(item, active) for item in value)
        finally:
            active.remove(identity)
    return False


def _draft_token(value: Any, active: set[int] | None = None) -> Any:
    """Snapshot ordinary structure while treating an unedited child draft as stable."""

    if active is None:
        active = set()
    if is_draft(value):
        return "draft", type(value), id(value)
    identity = id(value)
    if identity in active:
        return "cycle", identity
    if type(value) is dict:
        active.add(identity)
        try:
            return (
                dict,
                tuple(
                    (_draft_token(key, active), _draft_token(item, active))
                    for key, item in value.items()
                ),
            )
        finally:
            active.remove(identity)
    if type(value) in {list, tuple}:
        active.add(identity)
        try:
            return type(value), tuple(_draft_token(item, active) for item in value)
        finally:
            active.remove(identity)
    if type(value) in {set, frozenset}:
        active.add(identity)
        try:
            tokens = [_draft_token(item, active) for item in value]
            return type(value), tuple(sorted(tokens, key=repr))
        finally:
            active.remove(identity)
    return value_token(value)


def _safe_to_observe(value: Any, active: set[int]) -> bool:
    from .config import Config

    if is_known_immutable_atom(value) or isinstance(value, Config):
        return True
    identity = id(value)
    if identity in active:
        return False
    if type(value) is dict:
        active.add(identity)
        try:
            return all(
                _safe_to_observe(key, active) and _safe_to_observe(item, active)
                for key, item in value.items()
            )
        finally:
            active.remove(identity)
    if type(value) in {list, tuple, set, frozenset}:
        active.add(identity)
        try:
            return all(_safe_to_observe(item, active) for item in value)
        finally:
            active.remove(identity)
    return False


def _concrete_config_type(annotation: Any) -> type["Config"] | None:
    from .config import Config

    annotation, _ = unwrap_annotation(annotation, None)
    return (
        cast(type[Config], annotation)
        if isinstance(annotation, type) and issubclass(annotation, Config)
        else None
    )


def draft_repr(obj: BaseModel) -> str:
    """Render meaningful composition state, pending rules, and missing fields."""

    bits: list[str] = []
    data = object.__getattribute__(obj, "__dict__")
    state = draft_state(obj)
    for name, model_field in type(obj).__pydantic_fields__.items():
        if name in state.pending:
            bits.append(f"{name}=[pending {state.pending[name]!r}]")
        elif name in data and (
            is_draft(data[name]) or field_was_edited(obj, name, data[name])
        ):
            bits.append(f"{name}={data[name]!r}")
        elif isinstance(model_field.default, Interp):
            bits.append(f"{name}=[pending {model_field.default!r}]")
        elif model_field.is_required():
            child_type = _concrete_config_type(model_field.annotation)
            label = f"<untouched {child_type.__name__}>" if child_type else "[UNSET]"
            bits.append(f"{name}={label}")
    return f"<draft {type(obj).__name__}({', '.join(bits)})>"


def ensure_final(obj: BaseModel) -> None:
    """Shared guard for direct model serialization methods."""

    if is_draft(obj):
        raise DraftError(
            "nshconfig drafts are not serializable; call config_finalize() first"
        )
