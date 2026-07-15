"""The non-destructive draft-to-final interpolation and validation boundary."""

from collections.abc import Mapping, Sequence, Set as AbstractSet
from types import UnionType
from typing import Any, Literal, TypeVar, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel
from pydantic.aliases import AliasChoices, AliasPath
from pydantic_core import PydanticUndefined
from typing_extensions import NotRequired, ReadOnly, Required, is_typeddict

from .annotations import unwrap_annotation as _unwrap_annotated
from .config import Config
from .draft import field_was_edited
from .errors import DraftError, TemplateError
from .interp import Interp
from .state import (
    BINDING_KEY,
    copy_builtin_graph,
    draft_state,
    is_draft,
    is_template,
    replay_recipe,
    template_state,
)

__all__ = ["finalize"]

C = TypeVar("C", bound=Config)
_DELETE_PATH = object()


def finalize(config: C) -> C:
    """Resolve interpolation and validate one draft without consuming it."""
    if not isinstance(config, Config):
        raise TypeError("config_finalize() expects a Config instance")
    if is_template(config):
        raise TemplateError(
            "an unbound template cannot be finalized by itself; bind it in a "
            "concrete annotated Config position"
        )
    if not is_draft(config):
        raise DraftError(
            "config_finalize() expects a draft; this Config is already final. Keep and edit the "
            "original draft when values must change"
        )
    values = _collect_draft(config, type(config).__name__, set(), {})
    output = type(config).model_validate(values, by_alias=True, by_name=True)
    return output


def default_input(
    value: Any,
    annotation: Any,
    path: str,
    active: set[int] | None = None,
    memo: dict[int, Any] | None = None,
    *,
    discriminator: Any = None,
) -> Any:
    """Turn a default-origin Config graph into replayable validation input."""

    if active is None:
        active = set()
    if memo is None:
        memo = {}
    if isinstance(value, Interp):
        return value

    is_container = type(value) in {dict, list, tuple, set, frozenset}
    identity = id(value)
    if is_container:
        _enter(identity, path, active)
    try:
        selected, discriminator = _select_annotation(
            value, annotation, path, discriminator=discriminator
        )
        if isinstance(value, Config):
            return _config_default_input(
                value,
                selected,
                discriminator,
                path,
                active,
                memo,
            )
        if type(value) is dict:
            _, item_annotation = _mapping_annotations(selected)
            preserve_alias = not _contains_config_in_builtins(value, set())
            if identity in memo and preserve_alias:
                return memo[identity]
            output: dict[Any, Any] = {}
            if preserve_alias:
                memo[identity] = output
            for key, item in value.items():
                copied_key = copy_builtin_graph(key, memo)
                copied_item = default_input(
                    item,
                    _mapping_value_annotation(selected, key, item_annotation),
                    f"{path}[{_safe_repr(key, limit=80)}]",
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
                default_input(
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
                default_input(
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


def _config_default_input(
    value: Config,
    selected: Any,
    discriminator: Any,
    path: str,
    active: set[int],
    memo: dict[int, Any],
) -> Config:
    if _concrete_config_type(selected) is not type(value):
        raise ValueError(
            f"{path} contains a Config default but its annotation does not "
            "describe that Config structure: this position must name the exact "
            f"{type(value).__name__} type"
        )
    if is_draft(value):
        values = _collect_draft(value, path, set())
        return _validation_carrier(value, values, discriminator)

    recipe = replay_recipe(value)
    if recipe is None:
        raise ValueError(
            f"{path} uses a Config default without an intact constructor recipe"
        )
    inputs = copy_builtin_graph(recipe.inputs)
    assert isinstance(inputs, dict)
    for name, model_field in type(value).__pydantic_fields__.items():
        current = _field_value(inputs, type(value), name)
        if current is PydanticUndefined:
            continue
        replacement = default_input(
            current,
            model_field.annotation,
            f"{path}.{name}",
            active,
            memo,
            discriminator=model_field.discriminator,
        )
        _replace_field_input(inputs, type(value), name, replacement)
    return _validation_carrier(
        value,
        inputs,
        discriminator,
        replay_recipe=True,
    )


def _collect_draft(
    config: Config,
    path: str,
    active: set[int],
    memo: dict[int, Any] | None = None,
) -> dict[str, Any]:
    if memo is None:
        memo = {}
    identity = id(config)
    _enter(identity, path, active)
    try:
        state = draft_state(config)
        output = copy_builtin_graph(state.base.inputs) if state.base is not None else {}
        assert isinstance(output, dict)
        data = object.__getattribute__(config, "__dict__")
        explicit = config.__pydantic_fields_set__
        pending = state.pending
        for name, model_field in type(config).__pydantic_fields__.items():
            field_path = f"{path}.{name}"
            if name in state.deleted:
                _remove_field_input(output, type(config), name)
                continue
            if name in pending:
                _replace_field_input(output, type(config), name, pending[name])
                continue
            if name in data and (
                name in explicit or field_was_edited(config, name, data[name])
            ):
                replacement = _collect_value(
                    data[name],
                    model_field.annotation,
                    field_path,
                    active,
                    memo,
                    allow_interp=True,
                    discriminator=model_field.discriminator,
                )
                _replace_field_input(output, type(config), name, replacement)
                continue
            if (
                not _field_provided(output, type(config), name)
                and model_field.is_required()
                and (child_type := _concrete_config_type(model_field.annotation))
                is not None
            ):
                if name in data and is_draft(data[name]):
                    output[name] = _collect_draft(data[name], field_path, active, memo)
                else:
                    output[name] = _required_spine(child_type, field_path, set())
        return output
    finally:
        active.remove(identity)


def _collect_final(
    config: Config,
    path: str,
    active: set[int],
    memo: dict[int, Any],
) -> dict[str, Any]:
    identity = id(config)
    _enter(identity, path, active)
    try:
        data = object.__getattribute__(config, "__dict__")
        return {
            name: _collect_value(
                data[name],
                model_field.annotation,
                f"{path}.{name}",
                active,
                memo,
                allow_interp=False,
                discriminator=model_field.discriminator,
            )
            for name, model_field in type(config).__pydantic_fields__.items()
            if name in data
        }
    finally:
        active.remove(identity)


def _collect_value(
    value: Any,
    annotation: Any,
    path: str,
    active: set[int],
    memo: dict[int, Any],
    *,
    allow_interp: bool,
    discriminator: Any = None,
) -> Any:
    if isinstance(value, Interp):
        if allow_interp:
            return value
        raise ValueError(
            f"interp() is only legal as a complete Config field value: {path}"
        )

    annotation, discriminator = _select_annotation(
        value, annotation, path, discriminator=discriminator
    )
    if isinstance(value, Config):
        if _concrete_config_type(annotation) is not type(value):
            lifecycle = (
                "draft"
                if is_draft(value)
                else "template"
                if is_template(value)
                else "value"
            )
            raise ValueError(
                f"{path} contains a Config {lifecycle} but its annotation does not "
                "describe that Config structure: this position must name the exact "
                f"{type(value).__name__} type"
            )
        if is_template(value):
            return _config_default_input(
                value,
                annotation,
                discriminator,
                path,
                active,
                memo,
            )
        values = (
            _collect_draft(value, path, active, memo)
            if is_draft(value)
            else _collect_final(value, path, active, memo)
        )
        return _validation_carrier(value, values, discriminator)

    identity = id(value)
    if type(value) is dict:
        key_annotation, item_annotation = _mapping_annotations(annotation)
        preserve_alias = not _contains_config_in_builtins(value, set())
        _enter(identity, path, active)
        try:
            if identity in memo and preserve_alias:
                return memo[identity]
            output: dict[Any, Any] = {}
            if preserve_alias:
                memo[identity] = output
            for key, item in value.items():
                collected_key = _collect_value(
                    key,
                    key_annotation,
                    f"{path}.<key>",
                    active,
                    memo,
                    allow_interp=False,
                )
                collected_item = _collect_value(
                    item,
                    _mapping_value_annotation(annotation, key, item_annotation),
                    f"{path}[{_safe_repr(key, limit=80)}]",
                    active,
                    memo,
                    allow_interp=False,
                )
                output[collected_key] = collected_item
            return output
        finally:
            active.remove(identity)
    if type(value) is list:
        item_annotations = _sequence_annotations(annotation, len(value))
        preserve_alias = not _contains_config_in_builtins(value, set())
        _enter(identity, path, active)
        try:
            if identity in memo and preserve_alias:
                return memo[identity]
            output_list: list[Any] = []
            if preserve_alias:
                memo[identity] = output_list
            output_list.extend(
                _collect_value(
                    item,
                    item_annotations[index],
                    f"{path}[{index}]",
                    active,
                    memo,
                    allow_interp=False,
                )
                for index, item in enumerate(value)
            )
            return output_list
        finally:
            active.remove(identity)
    if type(value) is tuple:
        item_annotations = _sequence_annotations(annotation, len(value))
        preserve_alias = not _contains_config_in_builtins(value, set())
        _enter(identity, path, active)
        try:
            if identity in memo and preserve_alias:
                return memo[identity]
            output_tuple = tuple(
                _collect_value(
                    item,
                    item_annotations[index],
                    f"{path}[{index}]",
                    active,
                    memo,
                    allow_interp=False,
                )
                for index, item in enumerate(value)
            )
            if preserve_alias:
                memo[identity] = output_tuple
            return output_tuple
        finally:
            active.remove(identity)
    if type(value) is set:
        item_annotation = _set_annotation(annotation)
        preserve_alias = not _contains_config_in_builtins(value, set())
        _enter(identity, path, active)
        try:
            if identity in memo and preserve_alias:
                return memo[identity]
            output_set: set[Any] = set()
            if preserve_alias:
                memo[identity] = output_set
            output_set.update(
                _collect_value(
                    item,
                    item_annotation,
                    f"{path}[{index}]",
                    active,
                    memo,
                    allow_interp=False,
                )
                for index, item in enumerate(value)
            )
            return output_set
        finally:
            active.remove(identity)
    if type(value) is frozenset:
        item_annotation = _set_annotation(annotation)
        preserve_alias = not _contains_config_in_builtins(value, set())
        _enter(identity, path, active)
        try:
            if identity in memo and preserve_alias:
                return memo[identity]
            output_frozen = frozenset(
                _collect_value(
                    item,
                    item_annotation,
                    f"{path}[{index}]",
                    active,
                    memo,
                    allow_interp=False,
                )
                for index, item in enumerate(value)
            )
            if preserve_alias:
                memo[identity] = output_frozen
            return output_frozen
        finally:
            active.remove(identity)
    return value


def _required_spine(
    cls: type[Config], path: str, active_classes: set[type[Config]]
) -> dict[str, Any]:
    if cls in active_classes:
        raise ValueError(
            f"required Config fields form an unbounded recursive spine at {path}"
        )
    active_classes.add(cls)
    try:
        output: dict[str, Any] = {}
        for name, model_field in cls.__pydantic_fields__.items():
            if (
                model_field.is_required()
                and (child_type := _concrete_config_type(model_field.annotation))
                is not None
            ):
                output[name] = _required_spine(
                    child_type, f"{path}.{name}", active_classes
                )
        return output
    finally:
        active_classes.remove(cls)


def _concrete_config_type(annotation: Any) -> type[Config] | None:
    annotation, _ = _unwrap_annotated(annotation, None)
    if not isinstance(annotation, type) or is_typeddict(annotation):
        return None
    try:
        return annotation if issubclass(annotation, Config) else None
    except TypeError:
        return None


def _select_annotation(
    value: Any, annotation: Any, path: str, *, discriminator: Any
) -> tuple[Any, Any]:
    annotation, discriminator = _unwrap_annotated(annotation, discriminator)
    if get_origin(annotation) not in {Union, UnionType}:
        return annotation, discriminator

    arguments = [
        argument for argument in get_args(annotation) if argument is not type(None)
    ]
    if value is None:
        return type(None), discriminator

    matches = [
        argument
        for argument in arguments
        if _annotation_accepts_structure(value, argument, set())
    ]
    if len(matches) == 1:
        selected, selected_discriminator = _unwrap_annotated(matches[0], discriminator)
        return selected, selected_discriminator
    if len(matches) > 1 and _contains_config(value, set()):
        choices = ", ".join(repr(match) for match in matches)
        raise ValueError(
            f"{path} has an ambiguous non-discriminated annotation for Config values "
            f"({choices}); use one concrete branch or a Pydantic discriminated union"
        )
    if matches:
        selected, selected_discriminator = _unwrap_annotated(matches[0], discriminator)
        return selected, selected_discriminator

    # Select by outer runtime shape so a pending value under an invalid inner
    # position is rejected at that exact position rather than at the whole field.
    shallow = [
        argument for argument in arguments if _annotation_accepts_outer(value, argument)
    ]
    selected = shallow[0] if shallow else arguments[0]
    selected, selected_discriminator = _unwrap_annotated(selected, discriminator)
    return selected, selected_discriminator


def _annotation_accepts_structure(
    value: Any, annotation: Any, active: set[int]
) -> bool:
    annotation, _ = _unwrap_annotated(annotation, None)
    origin = get_origin(annotation)
    if origin in {Union, UnionType}:
        return any(
            _annotation_accepts_structure(value, argument, active)
            for argument in get_args(annotation)
        )
    if annotation in {Any, object}:
        return not _contains_config_in_builtins(value, set())
    if isinstance(value, Config):
        return _concrete_config_type(annotation) is type(value)
    if isinstance(value, Interp):
        return True
    if not _annotation_accepts_outer(value, annotation):
        return False

    identity = id(value)
    if isinstance(value, Mapping):
        if identity in active:
            return True
        active.add(identity)
        try:
            key_annotation, item_annotation = _mapping_annotations(annotation)
            return all(
                _annotation_accepts_structure(key, key_annotation, active)
                and _annotation_accepts_structure(
                    item,
                    _mapping_value_annotation(annotation, key, item_annotation),
                    active,
                )
                for key, item in value.items()
            )
        finally:
            active.remove(identity)
    if isinstance(value, (list, tuple)):
        if identity in active:
            return True
        active.add(identity)
        try:
            annotations = _sequence_annotations(annotation, len(value))
            return all(
                _annotation_accepts_structure(item, annotations[index], active)
                for index, item in enumerate(value)
            )
        finally:
            active.remove(identity)
    if isinstance(value, (set, frozenset)):
        if identity in active:
            return True
        active.add(identity)
        try:
            item_annotation = _set_annotation(annotation)
            return all(
                _annotation_accepts_structure(item, item_annotation, active)
                for item in value
            )
        finally:
            active.remove(identity)
    return True


def _annotation_accepts_outer(value: Any, annotation: Any) -> bool:
    annotation, _ = _unwrap_annotated(annotation, None)
    origin = get_origin(annotation)
    if origin in {Union, UnionType}:
        return any(
            _annotation_accepts_outer(value, argument)
            for argument in get_args(annotation)
        )
    if annotation in {Any, object}:
        return True
    if is_typeddict(annotation):
        return isinstance(value, Mapping)
    if annotation is type(None):
        return value is None
    if _concrete_config_type(annotation) is not None:
        return isinstance(value, Config) and type(value) is annotation
    if origin is Literal:
        return value in get_args(annotation)
    if origin is dict:
        return isinstance(value, dict)
    if origin is list:
        return isinstance(value, list)
    if origin is tuple:
        return isinstance(value, tuple)
    if origin is set:
        return isinstance(value, set)
    if origin is frozenset:
        return isinstance(value, frozenset)
    if _is_mapping_annotation(annotation):
        return isinstance(value, Mapping)
    if _is_sequence_annotation(annotation):
        return isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        )
    if _is_set_annotation(annotation):
        return isinstance(value, AbstractSet)
    return isinstance(annotation, type) and isinstance(value, annotation)


def _mapping_annotations(annotation: Any) -> tuple[Any, Any]:
    if is_typeddict(annotation):
        return str, Any
    if not _is_mapping_annotation(annotation):
        return Any, Any
    arguments = get_args(annotation)
    return (arguments[0], arguments[1]) if len(arguments) == 2 else (Any, Any)


def _mapping_value_annotation(annotation: Any, key: Any, fallback: Any) -> Any:
    if not is_typeddict(annotation) or not isinstance(key, str):
        return fallback
    try:
        annotations = get_type_hints(annotation, include_extras=True)
    except Exception:
        annotations = annotation.__annotations__
    value_annotation = annotations.get(key, Any)
    origin = get_origin(value_annotation)
    while origin in {Required, NotRequired, ReadOnly}:
        arguments = get_args(value_annotation)
        value_annotation = arguments[0] if arguments else Any
        origin = get_origin(value_annotation)
    return value_annotation


def _sequence_annotations(annotation: Any, length: int) -> tuple[Any, ...]:
    if _is_tuple_annotation(annotation):
        arguments = get_args(annotation)
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return (arguments[0],) * length
        return tuple(
            arguments[index] if index < len(arguments) else Any
            for index in range(length)
        )
    if _is_sequence_annotation(annotation):
        arguments = get_args(annotation)
        item_annotation = arguments[0] if len(arguments) == 1 else Any
        return (item_annotation,) * length
    return (Any,) * length


def _set_annotation(annotation: Any) -> Any:
    if not _is_set_annotation(annotation):
        return Any
    arguments = get_args(annotation)
    return arguments[0] if len(arguments) == 1 else Any


def _is_mapping_annotation(annotation: Any) -> bool:
    if is_typeddict(annotation):
        return True
    origin = get_origin(annotation)
    return isinstance(origin, type) and issubclass(origin, Mapping)


def _is_tuple_annotation(annotation: Any) -> bool:
    return get_origin(annotation) is tuple


def _is_sequence_annotation(annotation: Any) -> bool:
    origin = get_origin(annotation)
    return (
        isinstance(origin, type)
        and issubclass(origin, Sequence)
        and not issubclass(origin, (str, bytes, bytearray))
    )


def _is_set_annotation(annotation: Any) -> bool:
    origin = get_origin(annotation)
    return isinstance(origin, type) and issubclass(origin, AbstractSet)


def _contains_config(value: Any, active: set[int]) -> bool:
    if isinstance(value, Config):
        return True
    identity = id(value)
    if identity in active:
        return False
    if isinstance(value, Mapping):
        active.add(identity)
        try:
            return any(
                _contains_config(key, active) or _contains_config(item, active)
                for key, item in value.items()
            )
        finally:
            active.remove(identity)
    if isinstance(value, (list, tuple, set, frozenset)):
        active.add(identity)
        try:
            return any(_contains_config(item, active) for item in value)
        finally:
            active.remove(identity)
    return False


def _contains_config_in_builtins(value: Any, active: set[int]) -> bool:
    """Inspect only the exact built-in graph; arbitrary objects stay opaque."""

    if isinstance(value, Config):
        return True
    if type(value) not in {dict, list, tuple, set, frozenset}:
        return False
    identity = id(value)
    if identity in active:
        return False
    active.add(identity)
    try:
        items = (
            value.items() if type(value) is dict else ((None, item) for item in value)
        )
        return any(
            (key is not None and _contains_config_in_builtins(key, active))
            or _contains_config_in_builtins(item, active)
            for key, item in items
        )
    finally:
        active.remove(identity)


def _validation_carrier(
    source: Config,
    values: dict[str, Any],
    discriminator: Any,
    *,
    replay_recipe: bool = False,
) -> Config:
    if not replay_recipe and not is_draft(source):
        return source
    assert not (replay_recipe and is_draft(source)), (
        "only a final Config or unbound template can replay a constructor recipe"
    )
    values = dict(values)
    injected: set[str] = set()
    if (
        isinstance(discriminator, str)
        and _field_value(values, type(source), discriminator) is PydanticUndefined
    ):
        model_field = type(source).__pydantic_fields__.get(discriminator)
        if model_field is not None:
            if replay_recipe:
                if is_template(source):
                    tag = model_field.get_default(call_default_factory=False)
                else:
                    data = object.__getattribute__(source, "__dict__")
                    tag = data.get(discriminator, PydanticUndefined)
            else:
                tag = model_field.get_default(call_default_factory=False)
            if tag is not PydanticUndefined and not isinstance(tag, Interp):
                values[discriminator] = tag
                injected.add(discriminator)

    carrier = object.__new__(type(source))
    object.__setattr__(carrier, "__dict__", values)
    object.__setattr__(
        carrier,
        "__pydantic_fields_set__",
        set(source.__pydantic_fields_set__) - injected,
    )
    object.__setattr__(carrier, "__pydantic_extra__", None)
    private = (
        {BINDING_KEY: template_state(source).issues}
        if replay_recipe and is_template(source)
        else {}
    )
    object.__setattr__(carrier, "__pydantic_private__", private)
    return carrier


def _field_paths(cls: type[BaseModel], name: str) -> tuple[tuple[Any, ...], ...]:
    model_field = cls.__pydantic_fields__[name]
    alias = model_field.validation_alias
    choices = alias.choices if isinstance(alias, AliasChoices) else [alias]
    paths: list[tuple[Any, ...]] = []
    for choice in choices:
        if isinstance(choice, str):
            paths.append((choice,))
        elif isinstance(choice, AliasPath):
            paths.append(tuple(choice.path))
    paths.append((name,))
    return tuple(dict.fromkeys(paths))


def _field_value(values: Mapping[str, Any], cls: type[BaseModel], name: str) -> Any:
    for path in _field_paths(cls, name):
        current = _value_at_path(values, path)
        if current is not PydanticUndefined:
            return current
    return PydanticUndefined


def _field_provided(values: Mapping[str, Any], cls: type[BaseModel], name: str) -> bool:
    return _field_value(values, cls, name) is not PydanticUndefined


def _remove_field_input(
    values: dict[str, Any], cls: type[BaseModel], name: str
) -> None:
    for path in _field_paths(cls, name):
        if len(path) > 1 and not _another_field_uses_alias_root(
            values, cls, name, path[0]
        ):
            _delete_path(values, path[:1])
        else:
            _delete_path(values, path)


def _another_field_uses_alias_root(
    values: Mapping[str, Any],
    cls: type[BaseModel],
    excluded_name: str,
    root: Any,
) -> bool:
    return any(
        path
        and path[0] == root
        and _value_at_path(values, path) is not PydanticUndefined
        for name in cls.__pydantic_fields__
        if name != excluded_name
        for path in _field_paths(cls, name)
    )


def _value_at_path(values: Any, path: tuple[Any, ...]) -> Any:
    current = values
    for part in path:
        if isinstance(current, str):
            return PydanticUndefined
        try:
            current = current[part]
        except (KeyError, IndexError, TypeError):
            return PydanticUndefined
    return current


def _delete_path(values: Any, path: tuple[Any, ...]) -> None:
    if not path:
        return
    updated, found = _rewrite_path(values, path, _DELETE_PATH)
    if found:
        assert isinstance(values, dict) and isinstance(updated, dict)
        values.clear()
        values.update(updated)


def _replace_field_input(
    values: dict[str, Any], cls: type[BaseModel], name: str, replacement: Any
) -> None:
    for path in _field_paths(cls, name):
        updated, found = _rewrite_path(values, path, replacement)
        if found:
            assert isinstance(updated, dict)
            values.clear()
            values.update(updated)
            return
    values[name] = replacement


def _rewrite_path(
    value: Any,
    path: tuple[Any, ...],
    replacement: Any,
) -> tuple[Any, bool]:
    """Functionally replace or delete one mapping/list/tuple path."""

    part, *remaining = path
    child, found = _path_item(value, part)
    if not found:
        return value, False

    if remaining:
        updated_child, found = _rewrite_path(child, tuple(remaining), replacement)
        if not found:
            return value, False
        delete_child = replacement is _DELETE_PATH and _empty_alias_branch(
            updated_child
        )
        return _write_path_item(
            value,
            part,
            _DELETE_PATH if delete_child else updated_child,
        )
    return _write_path_item(value, part, replacement)


def _path_item(value: Any, part: Any) -> tuple[Any, bool]:
    if isinstance(value, Mapping):
        try:
            return value[part], True
        except (IndexError, KeyError, TypeError):
            return None, False
    if isinstance(value, (list, tuple)) and isinstance(part, int):
        if -len(value) <= part < len(value):
            return value[part], True
    return None, False


def _write_path_item(value: Any, part: Any, replacement: Any) -> tuple[Any, bool]:
    if isinstance(value, Mapping):
        output = dict(value)
        if replacement is _DELETE_PATH:
            output.pop(part, None)
        else:
            output[part] = replacement
        return output, True
    if isinstance(value, (list, tuple)) and isinstance(part, int):
        if not -len(value) <= part < len(value):
            return value, False
        output_items = list(value)
        output_items[part] = (
            PydanticUndefined if replacement is _DELETE_PATH else replacement
        )
        output = output_items if isinstance(value, list) else tuple(output_items)
        return output, True
    return value, False


def _empty_alias_branch(value: Any) -> bool:
    if isinstance(value, Mapping):
        return not value
    if isinstance(value, (list, tuple)):
        return not value or all(item is PydanticUndefined for item in value)
    return False


def _safe_repr(value: Any, *, limit: int) -> str:
    try:
        rendered = repr(value)
    except Exception:
        rendered = f"<{type(value).__name__}>"
    return rendered if len(rendered) <= limit else f"{rendered[: limit - 3]}..."


def _enter(identity: int, path: str, active: set[int]) -> None:
    if identity in active:
        raise ValueError(f"configuration values must be acyclic; cycle found at {path}")
    active.add(identity)
