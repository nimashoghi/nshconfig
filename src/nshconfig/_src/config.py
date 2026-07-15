"""The Config base class: ordinary validated finals plus explicit mutable drafts."""

import operator
from collections.abc import Generator, Mapping
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Generic,
    cast,
    get_args,
    get_origin,
)

from pydantic import BaseModel, ConfigDict, GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic_core import CoreSchema, PydanticUndefined, ValidationError
from typing_extensions import Self, override

from .annotations import (
    unsupported_container_annotation,
    unwrap_annotation,
    unwrap_type_alias,
)
from .draft import delete_field, draft_repr, ensure_final, read_field, set_field
from .errors import DraftError
from .scope import build_config_json_schema, build_config_schema
from .state import (
    FinalState,
    Recipe,
    RESERVED_PRIVATE_KEYS,
    STATE_KEY,
    is_draft,
    make_recipe,
    set_final_state,
    state_of,
    valid_recipe,
    value_token,
)

__all__ = ["Config"]

_LIFECYCLE_CONFIG: dict[str, Any] = {
    "extra": "forbid",
    "frozen": True,
    "validate_default": True,
    "revalidate_instances": "always",
    "validate_by_alias": True,
    "validate_by_name": True,
    "from_attributes": False,
}
_MISSING_HASH_VALUE = object()
_RESERVED_METHODS = frozenset(
    {
        "__copy__",
        "__deepcopy__",
        "__delattr__",
        "__eq__",
        "__get_pydantic_core_schema__",
        "__get_pydantic_json_schema__",
        "__getattr__",
        "__getattribute__",
        "__hash__",
        "__fields_set__",
        "__init__",
        "__init_subclass__",
        "__iter__",
        "__ne__",
        "__new__",
        "__pydantic_init_subclass__",
        "__setattr__",
        "copy",
        "config_draft",
        "config_finalize",
        "model_construct",
        "model_copy",
        "model_dump",
        "model_dump_json",
        "model_fields_set",
        "model_rebuild",
        "model_validate",
        "model_validate_json",
        "model_validate_strings",
    }
)


def _unsafe_validation_metadata(
    annotation: Any,
    metadata: tuple[Any, ...] = (),
    active: set[int] | None = None,
) -> str | None:
    if active is None:
        active = set()
    identity = id(annotation)
    if identity in active:
        return None
    active.add(identity)
    annotation = unwrap_type_alias(annotation)
    for item in metadata:
        item_type = item if isinstance(item, type) else type(item)
        qualified = f"{item_type.__module__}.{item_type.__qualname__}"
        if qualified in {
            "pydantic.functional_validators.SkipValidation",
            "pydantic.functional_validators.InstanceOf",
            "pydantic.types._OnErrorOmit",
        }:
            return item_type.__qualname__.lstrip("_")

    arguments = get_args(annotation)
    if get_origin(annotation) is Annotated:
        nested = _unsafe_validation_metadata(arguments[0], tuple(arguments[1:]), active)
        if nested is not None:
            return nested
        return None
    for argument in arguments:
        nested = _unsafe_validation_metadata(argument, active=active)
        if nested is not None:
            return nested
    return None


def _uses_callable_discriminator(
    annotation: Any,
    metadata: tuple[Any, ...] = (),
    active: set[int] | None = None,
) -> bool:
    if active is None:
        active = set()
    identity = id(annotation)
    if identity in active:
        return False
    active.add(identity)
    annotation = unwrap_type_alias(annotation)
    if any(
        (candidate := getattr(item, "discriminator", None)) is not None
        and not isinstance(candidate, str)
        and callable(candidate)
        for item in metadata
    ):
        return True
    arguments = get_args(annotation)
    if get_origin(annotation) is Annotated:
        return _uses_callable_discriminator(
            arguments[0],
            tuple(arguments[1:]),
            active,
        )
    return any(
        _uses_callable_discriminator(argument, active=active) for argument in arguments
    )


def _raise_frozen(instance: BaseModel, name: str, value: Any) -> None:
    raise ValidationError.from_exception_data(
        type(instance).__name__,
        [{"type": "frozen_instance", "loc": (name,), "input": value}],
    )


def _recipe_from_copy(obj: BaseModel, *, source_was_intact: bool) -> Recipe | None:
    """Recover copied raw inputs and refresh identity-bearing integrity tokens."""

    state = state_of(obj)
    if not source_was_intact or not isinstance(state, FinalState):
        return None
    recipe = state.recipe
    if recipe is None:
        return None
    return Recipe(inputs=recipe.inputs, input_token=value_token(recipe.inputs))


def _validate_class_shape(cls: type[BaseModel]) -> None:
    is_base = cls.__module__ == __name__ and cls.__name__ == "Config"
    if cls.__pydantic_root_model__:
        raise TypeError("Config does not support Pydantic RootModel subclasses")
    reserved_private = RESERVED_PRIVATE_KEYS & cls.__private_attributes__.keys()
    if reserved_private:
        name = sorted(reserved_private)[0]
        raise TypeError(
            f"{cls.__name__} declares reserved private attribute {name!r}; "
            "that name stores nshconfig lifecycle state"
        )

    if is_base:
        return
    config_base = globals()["Config"]
    allowed_owners = set(config_base.__mro__)
    overridden = []
    for method in _RESERVED_METHODS:
        owner = next((base for base in cls.__mro__ if method in base.__dict__), None)
        generic_lifecycle = method == "__init_subclass__" and owner is Generic
        if owner not in allowed_owners and not generic_lifecycle:
            overridden.append(method)
    if overridden:
        name = min(overridden)
        raise TypeError(
            f"{cls.__name__} overrides reserved Config lifecycle method {name!r}"
        )


def _validate_lifecycle_settings(cls: type[BaseModel]) -> None:
    broken = {
        name: (cls.model_config.get(name), required)
        for name, required in _LIFECYCLE_CONFIG.items()
        if cls.model_config.get(name) != required
    }
    if broken:
        details = ", ".join(
            f"{name}={actual!r} (required {required!r})"
            for name, (actual, required) in broken.items()
        )
        raise TypeError(
            f"{cls.__name__} overrides nshconfig lifecycle settings: {details}"
        )


def _validate_config_field(cls: type[BaseModel], name: str, model_field: Any) -> None:
    metadata = tuple(model_field.metadata)
    unsafe = _unsafe_validation_metadata(model_field.annotation, metadata)
    if unsafe is not None:
        raise TypeError(
            f"{cls.__name__}.{name} uses {unsafe}, which can bypass or omit "
            "Config field validation"
        )
    if _uses_callable_discriminator(model_field.annotation, metadata):
        raise TypeError(
            f"{cls.__name__}.{name} uses a callable union discriminator; incomplete "
            "Config drafts cannot execute an opaque branch-selection protocol. Use a "
            "string discriminator or an ordinary union of concrete Config types"
        )
    if (
        unsupported := unsupported_container_annotation(model_field.annotation)
    ) is not None:
        raise TypeError(
            f"{cls.__name__}.{name} uses unsupported container annotation {unsupported}; "
            "Config values use finite built-in dict, list, tuple, set, and frozenset "
            "containers"
        )
    if model_field.validate_default is False:
        raise TypeError(
            f"{cls.__name__}.{name} disables default validation; every Config field "
            "must publish one canonical value for declaration-ordered interpolation"
        )

    direct_annotation, _ = unwrap_annotation(model_field.annotation, None)
    config_base = globals()["Config"]
    direct_config = isinstance(direct_annotation, type) and issubclass(
        direct_annotation, config_base
    )
    from .interp import Interp

    if (
        direct_config
        and model_field.default is not PydanticUndefined
        and not isinstance(model_field.default, Interp)
        and (
            not isinstance(model_field.default, config_base)
            or valid_recipe(model_field.default) is None
        )
    ):
        raise TypeError(
            f"{cls.__name__}.{name} has a Config default without an intact "
            "constructor recipe; use a normally constructed Config value or a "
            "default factory returning a fresh draft"
        )


def _validate_config_class(cls: type[BaseModel]) -> None:
    """Recheck class policy after creation and every forward-ref schema rebuild."""

    _validate_class_shape(cls)
    _validate_lifecycle_settings(cls)
    for name, model_field in cls.__pydantic_fields__.items():
        _validate_config_field(cls, name, model_field)


class Config(BaseModel):
    """Base class for field-frozen Pydantic configuration with explicit drafts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        use_attribute_docstrings=True,
        validate_default=True,
        revalidate_instances="always",
        validate_by_alias=True,
        validate_by_name=True,
        from_attributes=False,
        serialize_by_alias=False,
    )

    def __init__(self, /, **data: Any) -> None:
        try:
            object.__getattribute__(self, "__pydantic_fields_set__")
        except AttributeError:
            pass
        else:
            raise TypeError(
                "Config instances cannot be reinitialized; construct a new final or edit a draft"
            )
        recipe = make_recipe(data)
        super().__init__(**data)
        set_final_state(self, recipe)

    # This guard is behaviorally equivalent to BaseModel.__init__ for fresh
    # objects.  Tell Pydantic not to insert a second custom-init validation
    # pass into the generated schema.
    setattr(__init__, "__pydantic_base_init__", True)

    @classmethod
    @override
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return build_config_schema(cls, source, handler)

    @classmethod
    @override
    def __get_pydantic_json_schema__(
        cls, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> dict[str, Any]:
        return build_config_json_schema(schema, handler)

    @classmethod
    @override
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        _validate_config_class(cls)
        from .transport import defer_config_val_sers

        defer_config_val_sers(cls)

    @classmethod
    @override
    def model_rebuild(
        cls,
        *,
        force: bool = False,
        raise_errors: bool = True,
        _parent_namespace_depth: int = 2,
        _types_namespace: Mapping[str, Any] | None = None,
    ) -> bool | None:
        result = super().model_rebuild(
            force=force,
            raise_errors=raise_errors,
            _parent_namespace_depth=_parent_namespace_depth,
            _types_namespace=_types_namespace,
        )
        if cls.__pydantic_complete__:
            from .transport import defer_config_val_sers

            defer_config_val_sers(cls)
        return result

    @classmethod
    def config_draft(cls) -> Self:
        """Create an incomplete mutable draft without running Pydantic hooks."""

        from .draft import create_draft

        return create_draft(cls)

    def config_finalize(self) -> Self:
        """Validate this draft into a fresh final without consuming it."""

        from .finalize import finalize

        return finalize(self)

    @classmethod
    @override
    def model_construct(
        cls, _fields_set: set[str] | None = None, **values: Any
    ) -> Self:
        raise TypeError(
            "Config.model_construct() is unsafe; use ConfigType.config_draft() or validation"
        )

    @override
    def model_copy(
        self, *, update: Mapping[str, Any] | None = None, deep: bool = False
    ) -> Self:
        if is_draft(self):
            raise DraftError(
                "drafts cannot be copied through model_copy(); keep the original draft"
            )
        copied = super().model_copy(update=update, deep=deep)
        recipe = valid_recipe(copied) if not update else None
        set_final_state(copied, recipe)
        return copied

    @override
    def copy(self, *args: Any, **kwargs: Any) -> Self:
        raise TypeError("Config.copy() is unsafe and unsupported; use model_copy()")

    @override
    def __copy__(self) -> Self:
        if is_draft(self):
            raise DraftError(
                "drafts cannot be shallow-copied; keep the original draft recipe"
            )
        source_was_intact = valid_recipe(self) is not None
        copied = cast(Self, BaseModel.__copy__(self))
        set_final_state(
            copied,
            _recipe_from_copy(copied, source_was_intact=source_was_intact),
        )
        return copied

    @override
    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> Self:
        if is_draft(self):
            raise DraftError(
                "drafts cannot be deep-copied; keep the original draft recipe"
            )
        source_was_intact = valid_recipe(self) is not None
        if memo is None:
            memo = {}
        copied = cast(Self, BaseModel.__deepcopy__(self, memo))
        set_final_state(
            copied,
            _recipe_from_copy(copied, source_was_intact=source_was_intact),
        )
        return copied

    @override
    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if type(other) is not type(self):
            return False
        assert isinstance(other, Config)
        if is_draft(self) or is_draft(other):
            return False
        # Lifecycle metadata is not part of value equality. Delegate field
        # semantics to Python while making equality total for hostile or
        # array-like ``__eq__`` implementations.
        left = object.__getattribute__(self, "__dict__")
        right = object.__getattribute__(other, "__dict__")
        missing = object()
        for name in type(self).__pydantic_fields__:
            left_value = left.get(name, missing)
            right_value = right.get(name, missing)
            if left_value is missing or right_value is missing:
                if left_value is not right_value:
                    return False
                continue
            if left_value is right_value:
                continue
            try:
                equal = left_value == right_value
                if not (equal if isinstance(equal, bool) else bool(equal)):
                    return False
            except Exception:
                return False
        return True

    @override
    def __hash__(self) -> int:
        if is_draft(self):
            raise TypeError(f"unhashable type: '{type(self).__name__}' draft")
        fields = tuple(type(self).__pydantic_fields__)
        if not fields:
            return hash(0)
        getter = operator.itemgetter(*fields)
        data = object.__getattribute__(self, "__dict__")
        try:
            values = getter(data)
        except KeyError:
            values = tuple(data.get(name, _MISSING_HASH_VALUE) for name in fields)
            if len(fields) == 1:
                values = values[0]
        return hash(values)

    @property
    @override
    def model_fields_set(self) -> set[str]:
        """Return a detached view so callers cannot mutate lifecycle bookkeeping."""
        return set(self.__pydantic_fields_set__)

    @property
    @override
    def __fields_set__(self) -> set[str]:
        """Detached compatibility view for Pydantic's deprecated public alias."""

        return set(self.__pydantic_fields_set__)

    @override
    def __repr__(self) -> str:
        return draft_repr(self) if is_draft(self) else BaseModel.__repr__(self)

    @override
    def __str__(self) -> str:
        return draft_repr(self) if is_draft(self) else BaseModel.__str__(self)

    def __treescope_repr__(self, path: str | None, subtree_renderer: Any) -> Any:
        from .treescope import render_config

        return render_config(self, path, subtree_renderer)

    @override
    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
        ensure_final(self)
        yield from BaseModel.__iter__(self)

    if not TYPE_CHECKING:

        def __getattribute__(self, name: str) -> Any:
            declared_field = (
                not name.startswith("_") and name in type(self).__pydantic_fields__
            )
            if declared_field:
                try:
                    draft_instance = is_draft(self)
                except AttributeError:
                    draft_instance = False
                if draft_instance:
                    data = object.__getattribute__(self, "__dict__")
                    if name in data:
                        from .interp import Interp

                        if isinstance(data[name], Interp):
                            return read_field(self, name)
            value = BaseModel.__getattribute__(self, name)
            if declared_field:
                from .interp import record_model_field_read

                value = record_model_field_read(self, name, value)
            return value

        def __setattr__(self, name: str, value: Any) -> None:
            if name.startswith("_"):
                if name == STATE_KEY or is_draft(self):
                    raise AttributeError(
                        f"{type(self).__name__} private or undeclared attribute {name!r} "
                        "cannot be assigned on composition state"
                    )
                BaseModel.__setattr__(self, name, value)
                return
            if is_draft(self) and not name.startswith("_"):
                set_field(self, name, value)
                return
            if name in type(self).__pydantic_fields__:
                _raise_frozen(self, name, value)
            BaseModel.__setattr__(self, name, value)

        def __getattr__(self, name: str) -> Any:
            if is_draft(self) and name in type(self).__pydantic_fields__:
                return read_field(self, name)
            return BaseModel.__getattr__(self, name)

        def __delattr__(self, name: str) -> None:
            if name.startswith("_"):
                if name == STATE_KEY or is_draft(self):
                    raise AttributeError(
                        f"{type(self).__name__} private or undeclared attribute {name!r} "
                        "cannot be deleted from composition state"
                    )
                BaseModel.__delattr__(self, name)
                return
            if is_draft(self) and delete_field(self, name):
                return
            if name in type(self).__pydantic_fields__:
                data = object.__getattribute__(self, "__dict__")
                _raise_frozen(self, name, data.get(name, PydanticUndefined))
            BaseModel.__delattr__(self, name)

        def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            ensure_final(self)
            return BaseModel.model_dump(self, *args, **kwargs)

        def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
            ensure_final(self)
            return BaseModel.model_dump_json(self, *args, **kwargs)
