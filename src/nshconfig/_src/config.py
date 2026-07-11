"""The Config base class: ordinary validated finals plus explicit mutable drafts."""

from collections.abc import Generator, Mapping
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
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
from .interp import Interp
from .scope import build_config_json_schema, build_config_schema
from .state import RESERVED_PRIVATE_KEYS, is_draft

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
        "__fields_set__",
        "__init__",
        "__init_subclass__",
        "__iter__",
        "__ne__",
        "__new__",
        "__pydantic_init_subclass__",
        "__setattr__",
        "copy",
        "model_construct",
        "model_copy",
        "model_dump",
        "model_dump_json",
        "model_fields_set",
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
            "pydantic.functional_validators.PlainValidator",
            "pydantic.functional_validators.WrapValidator",
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


def _uses_callable_discriminator(metadata: tuple[Any, ...]) -> bool:
    return any(
        (candidate := getattr(item, "discriminator", None)) is not None
        and not isinstance(candidate, str)
        and callable(candidate)
        for item in metadata
    )


def _raise_frozen(instance: BaseModel, name: str, value: Any) -> None:
    raise ValidationError.from_exception_data(
        type(instance).__name__,
        [{"type": "frozen_instance", "loc": (name,), "input": value}],
    )


def _validate_config_class(cls: type[BaseModel]) -> None:
    """Recheck class policy after creation and every forward-ref schema rebuild."""
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
    if not is_base:
        config_base = globals()["Config"]
        allowed_owners = set(config_base.__mro__)
        overridden = []
        for method in _RESERVED_METHODS:
            owner = next(
                (base for base in cls.__mro__ if method in base.__dict__), None
            )
            generic_lifecycle = method == "__init_subclass__" and owner is Generic
            if owner not in allowed_owners and not generic_lifecycle:
                overridden.append(method)
        overridden.sort()
        if overridden:
            raise TypeError(
                f"{cls.__name__} overrides reserved Config lifecycle method "
                f"{overridden[0]!r}"
            )
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
    decorators = cls.__pydantic_decorators__
    for name, decorator in decorators.model_validators.items():
        if decorator.info.mode == "wrap":
            raise TypeError(
                f"{cls.__name__}.{name} uses a wrap model validator, which can execute "
                "the Config validation pipeline zero or multiple times"
            )
    for name, decorator in decorators.field_validators.items():
        if decorator.info.mode in {"plain", "wrap"}:
            raise TypeError(
                f"{cls.__name__}.{name} uses a {decorator.info.mode} field validator, "
                "which can bypass or repeat canonical field validation"
            )
    if decorators.validators or decorators.root_validators:
        raise TypeError(
            f"{cls.__name__} uses deprecated validator decorators that cannot preserve "
            "Config lifecycle guarantees"
        )
    for name, model_field in cls.__pydantic_fields__.items():
        if name in _RESERVED_METHODS:
            raise TypeError(
                f"{cls.__name__}.{name} shadows a reserved Config lifecycle API"
            )
        metadata = tuple(model_field.metadata)
        unsafe = _unsafe_validation_metadata(model_field.annotation, metadata)
        if unsafe is not None:
            raise TypeError(
                f"{cls.__name__}.{name} uses {unsafe}, which can bypass or omit "
                "Config field validation"
            )
        if _uses_callable_discriminator(metadata):
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
        if (
            direct_config
            and not model_field.is_required()
            and not isinstance(model_field.default, Interp)
        ):
            raise TypeError(
                f"{cls.__name__}.{name} gives a direct Config field a concrete default; "
                "declare it as required or use interp() so parent validation context is explicit"
            )


class Config(BaseModel):
    """Base class for field-frozen, provenance-aware Pydantic configuration."""

    record_schema_id: ClassVar[str | None] = None

    model_config: ClassVar[ConfigDict] = ConfigDict(
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
        super().__init__(**data)

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
        type.__setattr__(cls, "__hash__", None)

    @classmethod
    @override
    def model_construct(
        cls, _fields_set: set[str] | None = None, **values: Any
    ) -> Self:
        raise TypeError(
            "Config.model_construct() is unsafe; use draft(ConfigType) or validation"
        )

    @override
    def model_copy(
        self, *, update: Mapping[str, Any] | None = None, deep: bool = False
    ) -> Self:
        ensure_final(self, "model_copy()")
        if update is not None:
            raise TypeError(
                "Config.model_copy(update=...) is ambiguous for interpolation and provenance; "
                "edit and finalize the original draft, or construct a new final explicitly"
            )
        return super().model_copy(deep=deep)

    @override
    def copy(self, *args: Any, **kwargs: Any) -> Self:
        raise TypeError(
            "Config.copy() is unsafe and unsupported; use model_copy() without updates"
        )

    @override
    def __copy__(self) -> Self:
        ensure_final(self, "copy.copy()")
        return cast(Self, BaseModel.__copy__(self))

    @override
    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> Self:
        ensure_final(self, "copy.deepcopy()")
        return cast(Self, BaseModel.__deepcopy__(self, memo))

    @override
    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if type(other) is not type(self):
            return False
        assert isinstance(other, Config)
        if is_draft(self) or is_draft(other):
            return False
        # Provenance and lifecycle metadata are deliberately not part of value
        # equality.  Delegate value semantics to Python while making equality
        # total for hostile or array-like ``__eq__`` implementations.
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

    __hash__: ClassVar[None] = None

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
        ensure_final(self, "iteration")
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
            if name.startswith("_") and name not in type(self).__private_attributes__:
                raise AttributeError(
                    f"{type(self).__name__} private or undeclared attribute {name!r} "
                    "cannot be assigned"
                )
            if is_draft(self):
                if name.startswith("_"):
                    raise DraftError(
                        "drafts compose declared fields only; private attributes are "
                        "initialized when the final is validated"
                    )
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
            if name.startswith("_") and name not in type(self).__private_attributes__:
                raise AttributeError(
                    f"{type(self).__name__} private or undeclared attribute {name!r} "
                    "cannot be deleted"
                )
            if is_draft(self):
                if name.startswith("_"):
                    raise DraftError(
                        "drafts compose declared fields only; private attributes cannot be "
                        "deleted"
                    )
                if delete_field(self, name):
                    return
            if name in type(self).__pydantic_fields__:
                data = object.__getattribute__(self, "__dict__")
                _raise_frozen(self, name, data.get(name, PydanticUndefined))
            BaseModel.__delattr__(self, name)

        def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            ensure_final(self, "model_dump()")
            return BaseModel.model_dump(self, *args, **kwargs)

        def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
            ensure_final(self, "model_dump_json()")
            return BaseModel.model_dump_json(self, *args, **kwargs)
