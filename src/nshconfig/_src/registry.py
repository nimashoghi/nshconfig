from __future__ import annotations

import dataclasses
import logging
import typing
import warnings
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Generic, Literal, TypedDict, TypeVar, cast

from pydantic import GetCoreSchemaHandler, model_validator
from pydantic.fields import FieldInfo
from pydantic_core import core_schema
from typing_extensions import assert_never, deprecated, override

from .utils import PYDANTIC_VERSION

if TYPE_CHECKING:
    from .config import Config, ConfigDict

log = logging.getLogger(__name__)

TConfig = TypeVar("TConfig", bound="Config", covariant=True)
TClass = TypeVar("TClass", bound="type[Config]")


@dataclasses.dataclass(frozen=True)
class _RegistryEntry:
    tag: str
    cls: type[Config]


def _unwrap_annotated_recurive(typ: Any):
    while typing.get_origin(typ) is typing.Annotated:
        typ = typing.get_args(typ)[0]
    return typ


def _resolve_tag(cls: type[Config], discriminator_field_name: str) -> str:
    # Make sure cls has the discriminator attribute
    if (discriminator_field := cls.model_fields.get(discriminator_field_name)) is None:
        raise ValueError(f"{cls} does not have a field `{discriminator_field_name}`")

    # Make sure the discriminator field is a Literal
    if (
        typing.get_origin(
            type_ := _unwrap_annotated_recurive(discriminator_field.annotation)
        )
        is not typing.Literal
    ):
        raise ValueError(
            f"The discriminator field `{discriminator_field_name}` of {cls} "
            f"should be a Literal. Got {type_}. Please ensure the field is a Literal."
        )

    # Make sure the Literal has only one argument
    if len(typing.get_args(type_)) != 1:
        raise ValueError(
            f"The discriminator field `{discriminator_field_name}` of {cls} "
            f"should be a Literal with only one argument. Got {type_}. "
            "This should not happen. Please report a bug."
        )

    # Extract the tag
    return typing.get_args(type_)[0]


class RegistryConfig(TypedDict, total=False):
    """A typed dictionary defining configuration options for the registry.

    Attributes:
        duplicate_tag_policy (Literal["warn-and-ignore", "warn-and-replace", "error"]):
            Defines how to handle duplicate tags in the registry.
            - "warn-and-ignore": Warns about duplicates but keeps the original tag
            - "warn-and-replace": Warns about duplicates and replaces with new tag
            - "error": Raises an error when duplicate tags are found

        auto_rebuild (bool):
            If True, the registry will automatically rebuild Pydantic models
            that use the registry type annotation whenever a new class
            is registered. This ensures that the model schema is always up-to-date
            with the registered classes. If False, the user must manually wrap
            parent pydantic models with the `@registry.rebuild_on_registers` decorator
            to achieve the same behavior. Default is True.
    """

    duplicate_tag_policy: Literal["warn-and-ignore", "warn-and-replace", "error"]
    auto_rebuild: bool


def _no_configs_registered_invalid_config(registry: Registry) -> type[Config]:
    if registry._invalid_cls is None:
        from .config import Config

        _model_config: ConfigDict = {}
        if PYDANTIC_VERSION >= "2.1.0":
            _model_config = {"defer_build": True}  # pyright: ignore[reportAssignmentType]

        class NoConfigsRegisteredInvalidConfig(Config):
            model_config = {**_model_config}

            @model_validator(mode="before")
            @classmethod
            def invalidate(cls, data: Any) -> Any:
                raise ValueError(
                    f"No configs have been registered with registry {registry}."
                )

        registry._invalid_cls = NoConfigsRegisteredInvalidConfig

    return registry._invalid_cls


# Pydantic < 2.1.0 has a behavior where in some situations, it expects
# types to be hashable. This is a workaround to ensure that
# the Registry class is hashable.
_legacy_pydantic_class_kwargs: Any = {}
_legacy_pydantic_field_kwargs: Any = {}
if PYDANTIC_VERSION < "2.1.0":
    _legacy_pydantic_class_kwargs = {"unsafe_hash": True}
    _legacy_pydantic_field_kwargs = {"hash": False}


def _compat_monkey_patch_type_pr_8526():
    from pydantic._internal import _core_utils

    def handle_definitions_schema(
        self,
        schema: core_schema.DefinitionsSchema,
        f: _core_utils.Walk,  # pyright: ignore[reportAttributeAccessIssue]
    ) -> core_schema.CoreSchema:
        new_definitions: list[core_schema.CoreSchema] = []
        for definition in schema["definitions"]:
            if "schema_ref" and "ref" in definition:
                # This indicates a purposely indirect reference
                # We want to keep such references around for implications related to JSON schema, etc.:
                new_definitions.append(definition)
                # However, we still need to walk the referenced definition:
                self.walk(definition, f)
                continue

            updated_definition = self.walk(definition, f)
            if "ref" in updated_definition:
                # If the updated definition schema doesn't have a 'ref', it shouldn't go in the definitions
                # This is most likely to happen due to replacing something with a definition reference, in
                # which case it should certainly not go in the definitions list
                new_definitions.append(updated_definition)
        new_inner_schema = self.walk(schema["schema"], f)

        if not new_definitions and len(schema) == 3:
            # This means we'd be returning a "trivial" definitions schema that just wrapped the inner schema
            return new_inner_schema

        new_schema = schema.copy()
        new_schema["schema"] = new_inner_schema
        new_schema["definitions"] = new_definitions
        return new_schema

    _core_utils._WalkCoreSchema.handle_definitions_schema = handle_definitions_schema  # type: ignore
    _core_utils._dispatch = _core_utils._WalkCoreSchema().walk  # type: ignore


@dataclasses.dataclass(**_legacy_pydantic_class_kwargs)
class Registry(Generic[TConfig]):
    """A registry system for creating dynamic discriminated unions with Pydantic models.

    This registry allows dynamic registration of subclasses that share a common
    discriminator field. It's particularly useful when you want to:
    1. Allow runtime registration of new subtypes
    2. Use a discriminator field to differentiate between subtypes
    3. Have Pydantic handle validation/serialization of the union type

    Args:
        base_cls: The base class that all registered classes must inherit from
        discriminator_field: The field name used to distinguish between different types

    Example:
        ```python
        class AnimalBaseConfig(Config, ABC):
            type: str

            @abstractmethod
            def make_sound(self) -> str: ...

        registry = Registry(AnimalBaseConfig, "type")

        @registry.register
        class DogConfig(AnimalBaseConfig):
            type: Literal["dog"]
            name: str

            @override
            def make_sound(self) -> str:
                return "Woof!"

        @registry.register
        class CatConfig(AnimalBaseConfig):
            type: Literal["cat"]
            name: str

            @override
            def make_sound(self) -> str:
                return "Meow!"
        ```

        @registry.rebuild_on_registers
        class ProgramConfig(Config):
            animal: Annotated[AnimalBaseConfig, registry]

        # ^ With the above code, the `ProgramConfig` class will have a field `animal`
        # that can be any of the registered classes in the registry. Our implementation
        # above implements Dog and Cat classes, and so the following code will work:

        def main(config: ProgramConfig):
            print(config.animal.make_sound())

        main(ProgramConfig(animal=DogConfig(type="dog", name="Buddy")))
        main(ProgramConfig(animal=CatConfig(type="cat", name="Whiskers")))

        # Now, let's say a user wants to add their own animal class and
        # use it in the `ProgramConfig` class. They can do so by simply
        # registering their class with the registry:

        @registry.register
        class CowConfig(AnimalBaseConfig):
            type: Literal["cow"]
            name: str

            @override
            def make_sound(self) -> str:
                return "Moo!"

        # The following code will now work (note that this would not have worked
        # if we didn't use the `@registry.register` decorator):
        main(ProgramConfig(animal=CowConfig(type="cow", name="Bessie")))
    """

    base_cls: type[TConfig]
    discriminator: str
    config: RegistryConfig = dataclasses.field(
        default_factory=lambda: {
            "duplicate_tag_policy": "error",
            "auto_rebuild": True,
        },
        **_legacy_pydantic_field_kwargs,
    )
    _elements: list[_RegistryEntry] = dataclasses.field(
        default_factory=lambda: [],
        **_legacy_pydantic_field_kwargs,
    )
    _on_register_callbacks: list[Callable[[type[Config]], None]] = dataclasses.field(
        default_factory=lambda: [],
        **_legacy_pydantic_field_kwargs,
    )
    _invalid_cls: type[Config] | None = None

    def __post_init__(self):
        # Pydantic versions between 2.4.2 and 2.5.3 have a bug in the way
        # they handle TypeAliasType (see issue: https://github.com/pydantic/pydantic/pull/8526/)
        # Here, we will just monkey-patch the code to apply the fix from the
        # Pydantic PR to ensure that TypeAliasType works correctly.
        if PYDANTIC_VERSION >= "2.4.2" and PYDANTIC_VERSION < "2.6.0":
            warnings.warn(
                "Pydantic versions 2.4.2 to 2.5.3 have a bug with TypeAliasType. "
                "This registry will monkey-patch the code to fix this issue. "
                "Please upgrade to Pydantic 2.6.0 or later when available.",
            )
            _compat_monkey_patch_type_pr_8526()

        # There is a bug in Pydantic > 2.6.0 (but not 2.6.0 itself) and <= 2.7.0
        # which makes complex, recursive types not work properly. We should
        # log a warning about this and suggest users to upgrade to 2.7.1 or later,
        # but there is nothing more we can do about it.
        if PYDANTIC_VERSION > "2.6.0" and PYDANTIC_VERSION <= "2.7.0":
            warnings.warn(
                "Pydantic versions > 2.6.0 and <= 2.7.0 have a bug with complex, recursive types. "
                "The registry will not work properly if you use complex types with it. "
                "Please upgrade to Pydantic 2.7.1 or later.",
            )

    def _ref(
        self,
        prefix: Iterable[str] | None = None,
        suffix: Iterable[str] | None = None,
    ):
        """Get the reference name for the registry.

        This is used for Pydantic schema generation and should be unique
        for each registry instance.
        """
        if prefix is None:
            prefix = ()
        if suffix is None:
            suffix = ()
        lhs = [
            *prefix,
            f"{self.base_cls.__module__}.{self.base_cls.__name__}Registry",
            *suffix,
        ]
        lhs = "_".join(lhs)
        rhs = [str(id(self))]
        rhs = "_".join(rhs)
        return f"{lhs}:{rhs}"

    def register(self, cls: TClass, /) -> TClass:
        """Register a new type with the registry.

        The class must:
        1. Inherit from the registry's base class
        2. Have a discriminator field with a single-value Literal type
        3. Not already be registered
        4. Use a unique tag value

        Args:
            cls: The class to register

        Returns:
            The registered class (allows use as decorator)

        Raises:
            ValueError: If registration requirements aren't met
        """

        # Check if the cls is a subclass of the base_cls
        if not issubclass(cls, self.base_cls):
            raise ValueError(f"{cls} should be a subclass of {self.base_cls}.")

        # Check if the cls is already registered
        if cls in set(e.cls for e in self._elements):
            raise ValueError(f"{cls} is already registered.")

        # Extract the tag from the cls
        tag = _resolve_tag(cls, self.discriminator)

        # Check if the tag is already registered
        if (
            registered_by_tag := next(
                (e.cls for e in self._elements if e.tag == tag), None
            )
        ) is not None and registered_by_tag != cls:
            if (
                policy := self.config.get("duplicate_tag_policy", "error")
            ) == "warn-and-ignore":
                log.warning(
                    f"Tag `{tag}` is already registered by {registered_by_tag}. Ignoring {cls}."
                )
                return cast(TClass, registered_by_tag)
            elif policy == "warn-and-replace":
                log.warning(
                    f"Tag `{tag}` is already registered by {registered_by_tag}. Replacing with {cls}."
                )
                self._elements = [e for e in self._elements if e.tag != tag]
            elif policy == "error":
                raise ValueError(
                    f"Tag `{tag}` is already registered by {registered_by_tag}."
                )
            else:
                assert_never(policy)

        # Add the cls to the registry
        self._elements.append(_RegistryEntry(tag=tag, cls=cls))
        log.info(f"Registered {cls} with tag '{tag}'.")

        # Call the on_register callbacks
        for callback in self._on_register_callbacks:
            callback(cls)

        return cast(TClass, cls)

    def rebuild_on_registers(self, cls: TClass, /) -> TClass:
        """Rebuild a class's schema whenever new types are registered.

        Since Pydantic caches model schemas, fields using the registry's union type
        won't automatically update when new types are registered. This decorator
        ensures the schema is rebuilt whenever the registry changes.

        For example, the code below will work for the first print (since `DogConfig`
        is registered before the `ProgramConfig` schema is built), but will fail
        for the second print (since `MooseConfig` is registered after the schema is built):

        ```python
        class AnimalBaseConfig(Config, ABC): ...

        registry = Registry(AnimalBaseConfig, "type")

        @registry.register
        class DogConfig(AnimalBaseConfig): ...

        @registry.register
        class CatConfig(AnimalBaseConfig): ...

        class ProgramConfig(Config):
            animal: Annotated[AnimalBaseConfig, registry]

        # This will work, since `DogConfig` is registered before the schema is built.
        print(ProgramConfig(animal=DogConfig(type="dog")))
        # Same with `CatConfig`
        print(ProgramConfig(animal=CatConfig(type="cat")))

        @registry.register
        class MooseConfig(AnimalBaseConfig): ...

        # This will fail, since the schema for `ProgramConfig` is already built
        # and does not include `MooseConfig`. I.e., the schema for
        # `ProgramConfig.animal` is that of `Union[DogConfig, CatConfig]`.
        print(ProgramConfig(animal=MooseConfig(type="moose")))

        # If we rebuild the `ProgramConfig` schema, then the second print will work.
        ProgramConfig.model_rebuild(force=True)

        # Now this will work
        print(ProgramConfig(animal=MooseConfig(type="moose")))
        ```

        This decorator is a convenience method that automatically rebuilds the class
        whenever a new class is registered with the registry. This way, the schema
        for the class will always be up-to-date with the registered classes. For
        example, the code below will work as expected:

        ```python
        class AnimalBaseConfig(Config, ABC): ...

        registry = Registry(AnimalBaseConfig, "type")

        @registry.register
        class DogConfig(AnimalBaseConfig): ...

        @registry.register
        class CatConfig(AnimalBaseConfig): ...

        @registry.rebuild_on_registers
        class ProgramConfig(Config):
            animal: Annotated[AnimalBaseConfig, registry]

        # This will work, since `DogConfig` is registered before the schema is built.
        print(ProgramConfig(animal=DogConfig(type="dog")))
        # Same with `CatConfig`
        print(ProgramConfig(animal=CatConfig(type="cat")))

        @registry.register
        class MooseConfig(AnimalBaseConfig): ...

        # Now, this will also work, since the schema for `ProgramConfig` is
        # automatically rebuilt whenever a new class is registered with the registry.
        print(ProgramConfig(animal=MooseConfig(type="moose")))

        # The schema for `ProgramConfig` is always up-to-date with the registered classes.
        ```

        Args:
            cls: The class to rebuild whenever a new class is registered with the registry. This
                class must be a `nshconfig.Config` subclass.

        Returns:
            The decorated class
        """

        def _rebuild(_: type[Config]):
            # For older Pydantic versions, the `model_rebuild` method
            # does not work properly, so we need to do some additional work.
            if PYDANTIC_VERSION < "2.1.0":
                if "__pydantic_core_schema__" in cls.__dict__:
                    delattr(
                        cls, "__pydantic_core_schema__"
                    )  # delete cached value to ensure full rebuild happens

            cls.model_rebuild(force=True, raise_errors=False)
            log.debug(f"Rebuilt {cls} schema due to registry changes.")

        log.debug(f"Registering {cls} for auto-rebuild on registry changes.")
        self._on_register_callbacks.append(_rebuild)
        _rebuild(cls)
        return cls

    def _rebuild_on_registers_if_auto_rebuild(
        self, cls: type[Config], /, *args: Any, **kwargs: Any
    ) -> type[Config]:
        """Rebuild the class schema if auto_rebuild is enabled.

        This is a helper method that checks the registry's config for auto_rebuild
        and rebuilds the class schema if it is enabled. It is used internally by
        the `rebuild_on_registers` decorator.
        """
        if self.config.get("auto_rebuild", True):
            log.debug(f"Auto-rebuilding {cls} schema due to registry changes.")
            return self.rebuild_on_registers(cls, *args, **kwargs)
        return cls

    def type_hint(self):
        """Create a type hint for the union of all registered types."""
        from pydantic import Field

        if not self._elements:
            # If no elements are registered, just use the Invalid type.
            t = _no_configs_registered_invalid_config(self)
        else:
            # Construct the annotated union type.
            t = typing.Union[tuple(e.cls for e in self._elements)]
            # Pydantic < 2.1.0 does not support discriminated Unions with 1 element,
            # so we need to handle that case separately.
            if PYDANTIC_VERSION < "2.1.0" and len(self._elements) == 1:
                field_info = Field()
            else:
                field_info = Field(discriminator=self.discriminator)
            field_info.annotation = t
            t = typing.Annotated[t, field_info]

        return t

    def type_adapter(self):
        """Create a TypeAdapter for validating against the registry's types.

        Returns:
            A TypeAdapter for validating/serializing registry types
        """
        from pydantic import TypeAdapter

        # Earlier versions of TypeAdapter do not have the proper overload
        # for TypeAdapter.__init__ that handles `Any` types (which is needed
        # in cases we are trying to pass an `Annotated` type), so we explicitly
        # cast this to Any to avoid type errors.
        type_ = cast(Any, self.type_hint())
        return TypeAdapter[TConfig](type_)

    def construct(self, config: Any) -> TConfig:
        """Construct a registered type instance from configuration data.

        Args:
            config: Configuration data to validate/construct from

        Returns:
            An instance of the appropriate registered type
        """

        return self.type_adapter().validate_python(config)

    def print_registered_tags(self):
        """Pretty prints all registered tags in the registry."""
        if not self._elements:
            output = "No tags registered."
        else:
            output = "Registered tags:\n"
            for entry in self._elements:
                output += f"  - {entry.tag}: {entry.cls.__name__}\n"

        print(output)

    @override
    def __repr__(self) -> str:
        """Return a string representation of the registry.

        Provides a concise overview of the registry configuration and its registered types.
        The representation includes the base class, discriminator field, and a summary
        of registered types.

        Returns
        -------
        str
            A formatted string representation of the registry
        """
        base_name = f"{self.base_cls.__module__}.{self.base_cls.__name__}"
        registered_types = len(self._elements)

        # Start with the basic info
        parts = [
            f"Registry[{base_name}](",
            f"  discriminator='{self.discriminator}',",
            f"  registered_types={registered_types},",
        ]

        # Add policy info
        policy = self.config.get("duplicate_tag_policy", "error")
        parts.append(f"  duplicate_tag_policy='{policy}',")

        auto_rebuild = self.config.get("auto_rebuild", True)
        parts.append(f"  auto_rebuild={auto_rebuild},")

        # Add registered tags if there are any
        if self._elements:
            parts.append("  tags=[")
            # Sort by tag for consistent output
            for entry in sorted(self._elements, key=lambda e: e.tag):
                class_name = entry.cls.__name__
                parts.append(f"    '{entry.tag}': {class_name},")
            parts.append("  ]")
        else:
            parts.append("  tags=[]")

        parts.append(")")

        return "\n".join(parts)

    @deprecated(
        "This method is deprecated. You can directly use the registry instance instead (e.g., Annotated[AnimalBase, registry]).",
    )
    def DynamicResolution(self):
        """Create a type annotation that enables dynamic type resolution using this registry.

        This method is a core part of using the registry system with Pydantic models. It creates
        a special type annotation that tells Pydantic to dynamically resolve field types using
        the registry's registered classes. This enables powerful dynamic behavior where fields
        can accept any type registered with the registry, even types registered after the model
        is first defined.

        Returns:
            A class that can be used with typing.Annotated to indicate a field should
            accept any registered type.

        Example:
            Basic usage with a single field:
            ```python
            @registry.rebuild_on_registers
            class Config(Config):
                # This field will accept any type registered with 'registry'
                animal: Annotated[AnimalBase, registry.DynamicResolution()]

            # These will all work:
            Config(animal=DogConfig(type="dog", name="Rover"))
            Config(animal=CatConfig(type="cat", name="Whiskers"))

            # Even types registered after Config is defined will work:
            @registry.register
            class BirdConfig(AnimalBase):
                type: Literal["bird"]
                name: str

            Config(animal=BirdConfig(type="bird", name="Tweety"))  # Works!
            ```

        Important Notes:
            1. Always use this with @registry.rebuild_on_registers if you want the model
               to automatically support newly registered types.

            2. The Annotated type should use the registry's base class as its first argument
               (e.g., Annotated[AnimalBase, registry.DynamicResolution()])

            3. This can be used with any Pydantic field type that accepts a model, including
               nested within lists, dicts, Optional[], etc.

            4. The field will validate that the discriminator field matches one of the
               registered types and will raise a validation error if an unknown type
               is provided.

        This method is typically used in conjunction with rebuild_on_registers to create
        fully dynamic models that can handle new types as they are registered:
        ```python
        # In your base package:
        class AnimalBase(Config, ABC):
            type: str
            @abstractmethod
            def make_sound(self) -> str: ...

        registry = Registry(AnimalBase, "type")

        @registry.register
        class DogConfig(AnimalBase):
            type: Literal["dog"]
            name: str
            def make_sound(self) -> str: return "Woof!"

        @registry.rebuild_on_registers
        class ProgramConfig(Config):
            animal: Annotated[AnimalBase, registry.DynamicResolution()]

        # In a separate plugin package:
        @registry.register
        class CatConfig(AnimalBase):
            type: Literal["cat"]
            name: str
            def make_sound(self) -> str: return "Meow!"

        # Both of these will work:
        ProgramConfig(animal=DogConfig(type="dog", name="Rover"))
        ProgramConfig(animal=CatConfig(type="cat", name="Whiskers"))
        ```
        """

        return self

    def __get_pydantic_core_schema__(
        self,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        return handler.generate_schema(self.type_hint())


def _extract_registry_from_annotation(annotation: Any):
    if not isinstance(annotation, Registry):
        return None
    return annotation


def _recursively_find_registry_annotations(typ: Any) -> list[Registry]:
    """Recursively find all registry annotations in a type, including in nested types.

    For example, this will find registry annotations in:
    - Annotated[T, registry]
    - list[Annotated[T, registry]]
    - Dict[str, Annotated[T, registry]]
    - etc.

    Args:
        typ: The type to check

    Returns:
        List of found registry instances
    """
    registries = []

    # Handle Annotated types
    if typing.get_origin(typ) is typing.Annotated:
        # Check annotations
        (base_type, *annotations) = typing.get_args(typ)
        for annotation in annotations:
            if (registry := _extract_registry_from_annotation(annotation)) is not None:
                registries.append(registry)

        # Recurse into base type
        registries.extend(_recursively_find_registry_annotations(base_type))

    # Handle generic types (list, dict, etc)
    elif origin := typing.get_origin(typ):
        for arg in typing.get_args(typ):
            registries.extend(_recursively_find_registry_annotations(arg))

    return registries


def extract_registries_from_field_info(field: FieldInfo):
    """Extract all registries from a Pydantic FieldInfo object.

    This function will extract all registries from the field's type and any nested types.

    Args:
        field: The field to extract registries from

    Returns:
        List of found registry instances
    """
    registries: list[Registry] = []

    # Pydantic parses the first level of annotations, so we need to use those.
    registries.extend(
        registry
        for metadata in field.metadata
        if (registry := _extract_registry_from_annotation(metadata)) is not None
    )

    # Recursively find registry annotations
    registries.extend(_recursively_find_registry_annotations(field.annotation))

    return registries
