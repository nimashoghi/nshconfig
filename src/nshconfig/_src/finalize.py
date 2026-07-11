"""The non-destructive draft-to-final interpolation and validation boundary."""

from collections.abc import Mapping, Sequence, Set as AbstractSet
from dataclasses import is_dataclass
from types import UnionType
from typing import Any, Literal, TypeVar, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel
from pydantic_core import PydanticUndefined
from typing_extensions import NotRequired, ReadOnly, Required, is_typeddict

from .annotations import unwrap_annotation as _unwrap_annotated
from .config import Config
from .draft import (
    _DraftDict,
    _DraftList,
    _DraftSet,
    assert_tracked_integrity,
    has_user_input,
)
from .errors import DraftError
from .interp import Interp
from .provenance import (
    final_reuse_error,
    merge_draft_provenance,
    safe_repr,
    stored_path,
)
from .semantic import inert_dataclass_state
from .state import FinalState, draft_state, is_draft, state_of

__all__ = ["finalize"]

C = TypeVar("C", bound=Config)


def finalize(config: C) -> C:
    """Resolve and validate a draft, or revalidate an existing final by value."""
    if not isinstance(config, Config):
        raise TypeError("finalize() expects a Config instance")
    if is_draft(config):
        values = _collect_draft(config, type(config).__name__, set())
        output = type(config).model_validate(values, by_alias=True, by_name=True)
        merge_draft_provenance(config, output)
        return output

    if not isinstance(state_of(config), FinalState):
        raise DraftError(
            "finalize() cannot run while Config validation is still in progress"
        )
    return type(config).model_validate(config, by_alias=True, by_name=True)


def _collect_draft(config: Config, path: str, active: set[int]) -> dict[str, Any]:
    identity = id(config)
    _enter(identity, path, active)
    try:
        output: dict[str, Any] = {}
        data = object.__getattribute__(config, "__dict__")
        for name, value in data.items():
            if name in type(config).__pydantic_fields__:
                assert_tracked_integrity(value, f"{path}.{name}")
        explicit = config.__pydantic_fields_set__
        pending = draft_state(config).pending
        for name, model_field in type(config).__pydantic_fields__.items():
            field_path = f"{path}.{name}"
            if name in pending:
                output[name] = pending[name]
                continue
            if name in data and (name in explicit or has_user_input(data[name])):
                output[name] = _collect_value(
                    data[name],
                    model_field.annotation,
                    field_path,
                    active,
                    allow_interp=True,
                    discriminator=model_field.discriminator,
                )
                continue
            if (
                model_field.is_required()
                and (child_type := _concrete_config_type(model_field.annotation))
                is not None
            ):
                if name in data and is_draft(data[name]):
                    output[name] = _collect_draft(data[name], field_path, active)
                else:
                    output[name] = _required_spine(child_type, field_path, set())
        return output
    finally:
        active.remove(identity)


def _collect_final(
    config: Config,
    path: str,
    active: set[int],
    *,
    allow_historical_finals: bool = False,
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
                allow_interp=False,
                discriminator=model_field.discriminator,
                allow_historical_finals=allow_historical_finals,
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
    *,
    allow_interp: bool,
    discriminator: Any = None,
    allow_historical_finals: bool = False,
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
            lifecycle = "draft" if is_draft(value) else "value"
            raise ValueError(
                f"{path} contains a Config {lifecycle} but its annotation does not "
                "describe that Config structure: this position must name the exact "
                f"{type(value).__name__} type"
            )
        values = (
            _collect_draft(value, path, active)
            if is_draft(value)
            else _collect_final(
                value,
                path,
                active,
                allow_historical_finals=allow_historical_finals,
            )
        )
        return _validation_carrier(
            value,
            values,
            discriminator,
            path,
            allow_historical=allow_historical_finals,
        )

    identity = id(value)
    if type(value) is dict or isinstance(value, _DraftDict):
        key_annotation, item_annotation = _mapping_annotations(annotation)
        for key, item in value.items():
            if type(key) not in {str, int} and _contains_config(item, set()):
                raise ValueError(
                    f"{path} uses unsupported structural mapping key "
                    f"{safe_repr(key, limit=80)}: mappings containing Config values "
                    "require exact str or int keys"
                )
        _enter(identity, path, active)
        try:
            return {
                _collect_value(
                    key,
                    key_annotation,
                    f"{path}.<key>",
                    active,
                    allow_interp=False,
                    allow_historical_finals=allow_historical_finals,
                ): _collect_value(
                    item,
                    _mapping_value_annotation(annotation, key, item_annotation),
                    f"{path}[{safe_repr(key, limit=80)}]",
                    active,
                    allow_interp=False,
                    allow_historical_finals=allow_historical_finals,
                )
                for key, item in value.items()
            }
        finally:
            active.remove(identity)
    if type(value) is list or isinstance(value, _DraftList):
        item_annotations = _sequence_annotations(annotation, len(value))
        _enter(identity, path, active)
        try:
            return [
                _collect_value(
                    item,
                    item_annotations[index],
                    f"{path}[{index}]",
                    active,
                    allow_interp=False,
                    allow_historical_finals=allow_historical_finals,
                )
                for index, item in enumerate(value)
            ]
        finally:
            active.remove(identity)
    if type(value) is tuple:
        item_annotations = _sequence_annotations(annotation, len(value))
        _enter(identity, path, active)
        try:
            return tuple(
                _collect_value(
                    item,
                    item_annotations[index],
                    f"{path}[{index}]",
                    active,
                    allow_interp=False,
                    allow_historical_finals=allow_historical_finals,
                )
                for index, item in enumerate(value)
            )
        finally:
            active.remove(identity)
    if type(value) is set or isinstance(value, _DraftSet):
        item_annotation = _set_annotation(annotation)
        _enter(identity, path, active)
        try:
            return {
                _collect_value(
                    item,
                    item_annotation,
                    f"{path}[{index}]",
                    active,
                    allow_interp=False,
                    allow_historical_finals=allow_historical_finals,
                )
                for index, item in enumerate(value)
            }
        finally:
            active.remove(identity)
    if type(value) is frozenset:
        item_annotation = _set_annotation(annotation)
        _enter(identity, path, active)
        try:
            return frozenset(
                _collect_value(
                    item,
                    item_annotation,
                    f"{path}[{index}]",
                    active,
                    allow_interp=False,
                    allow_historical_finals=allow_historical_finals,
                )
                for index, item in enumerate(value)
            )
        finally:
            active.remove(identity)
    if isinstance(value, BaseModel):
        _assert_opaque_model_clean(value, path, active)
    elif is_dataclass(value) and not isinstance(value, type):
        _assert_dataclass_clean(value, path, active)
    return value


def _required_spine(
    cls: type[Config], path: str, active_classes: set[type[Config]]
) -> Config:
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
        # These values only make the required Config structure available to
        # Pydantic.  They are not user input, so preserve that distinction at
        # every generated node instead of passing a mapping whose keys would be
        # recorded in ``model_fields_set``.
        return _detached_validation_carrier(cls, output, set())
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
        return not _contains_config(value, set())
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


def _validation_carrier(
    source: Config,
    values: dict[str, Any],
    discriminator: Any,
    path: str,
    *,
    allow_historical: bool,
) -> Config:
    if (
        not allow_historical
        and not is_draft(source)
        and (reason := final_reuse_error(source)) is not None
    ):
        raise ValueError(
            f"{path} cannot reuse a provenance-bearing Config final: {reason}"
        )
    # Preserve the actual source for ordinary final reuse.  Pydantic's required
    # ``revalidate_instances='always'`` creates the new validated node, while
    # the validation scope can still identify and transfer its provenance.
    # ``finalize(existing_final)`` uses detached carriers because the root-level
    # provenance copy handles the entire historical graph in one pass.
    if not is_draft(source) and not allow_historical:
        return source
    values = dict(values)
    injected: set[str] = set()
    if isinstance(discriminator, str) and discriminator not in values:
        model_field = type(source).__pydantic_fields__.get(discriminator)
        if model_field is not None:
            default = model_field.get_default(call_default_factory=False)
            if default is not PydanticUndefined and not isinstance(default, Interp):
                values[discriminator] = default
                injected.add(discriminator)

    return _detached_validation_carrier(
        type(source),
        values,
        (source.__pydantic_fields_set__ & values.keys()) - injected,
    )


def _detached_validation_carrier(
    cls: type[Config], values: dict[str, Any], fields_set: set[str]
) -> Config:
    carrier = object.__new__(cls)
    object.__setattr__(carrier, "__dict__", values)
    object.__setattr__(carrier, "__pydantic_fields_set__", fields_set)
    object.__setattr__(carrier, "__pydantic_extra__", None)
    object.__setattr__(carrier, "__pydantic_private__", {})
    return carrier


def _enter(identity: int, path: str, active: set[int]) -> None:
    if identity in active:
        raise ValueError(f"configuration values must be acyclic; cycle found at {path}")
    active.add(identity)


def _assert_opaque_model_clean(value: BaseModel, path: str, active: set[int]) -> None:
    identity = id(value)
    _enter(identity, path, active)
    try:
        data = object.__getattribute__(value, "__dict__")
        for name in type(value).__pydantic_fields__:
            if name in data:
                _assert_no_pending(data[name], f"{path}.{name}", active)
        extra = object.__getattribute__(value, "__pydantic_extra__") or {}
        for name, item in extra.items():
            _assert_no_pending(
                item,
                f"{path}[{safe_repr(name, limit=80)}]",
                active,
            )
        private = object.__getattribute__(value, "__pydantic_private__") or {}
        for name, item in private.items():
            _assert_no_pending(item, f"{path}.{name}", active)
    finally:
        active.remove(identity)


def _assert_dataclass_clean(value: Any, path: str, active: set[int]) -> None:
    identity = id(value)
    _enter(identity, path, active)
    try:
        state = inert_dataclass_state(value)
        for name, item in state.items():
            _assert_no_pending(item, stored_path(path, name), active)
    finally:
        active.remove(identity)


def _assert_no_pending(value: Any, path: str, active: set[int]) -> None:
    if isinstance(value, Interp) or is_draft(value):
        raise ValueError(
            f"pending nshconfig value is hidden in an opaque object at {path}"
        )
    if isinstance(value, Mapping):
        identity = id(value)
        _enter(identity, path, active)
        try:
            for key, item in value.items():
                _assert_no_pending(key, f"{path}.<key>", active)
                _assert_no_pending(
                    item,
                    f"{path}[{safe_repr(key, limit=80)}]",
                    active,
                )
        finally:
            active.remove(identity)
    elif isinstance(value, (list, tuple, set, frozenset)):
        identity = id(value)
        _enter(identity, path, active)
        try:
            for index, item in enumerate(value):
                _assert_no_pending(item, f"{path}[{index}]", active)
        finally:
            active.remove(identity)
    elif isinstance(value, BaseModel):
        _assert_opaque_model_clean(value, path, active)
    elif is_dataclass(value) and not isinstance(value, type):
        _assert_dataclass_clean(value, path, active)
