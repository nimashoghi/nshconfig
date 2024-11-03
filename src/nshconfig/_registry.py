from __future__ import annotations

import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, cast

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema
from typing_extensions import TypeVar

import nshconfig as C

TConfig = TypeVar("TConfig", bound=C.Config, infer_variance=True)
TClass = TypeVar("TClass", bound=type[C.Config])


def _resolve_tag(cls: type[C.Config], discriminator_field_name: str) -> str:
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


@dataclass
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
        class AnimalBaseConfig(C.Config, ABC):
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
        class ProgramConfig(C.Config):
            animal: Annotated[AnimalBaseConfig, registry.RegistryResolution()]

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
    discriminator_field: str
    _elements: list[_RegistryEntry] = field(default_factory=lambda: [])
    _on_register_callbacks: list[Callable[[type[C.Config]], None]] = field(
        default_factory=lambda: []
    )

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
        tag = _resolve_tag(cls, self.discriminator_field)

        # Check if the tag is already registered
        if (
            registered_by_tag := next(
                (e.cls for e in self._elements if e.tag == tag), None
            )
        ) is not None and registered_by_tag != cls:
            raise ValueError(
                f"Tag `{tag}` is already registered by {registered_by_tag}."
            )

        # Add the cls to the registry
        self._elements.append(_RegistryEntry(tag=tag, cls=cls))

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
        class AnimalBaseConfig(C.Config, ABC): ...

        registry = Registry(AnimalBaseConfig, "type")

        @registry.register
        class DogConfig(AnimalBaseConfig): ...

        @registry.register
        class CatConfig(AnimalBaseConfig): ...

        class ProgramConfig(C.Config):
            animal: Annotated[AnimalBaseConfig, registry.RegistryResolution()]

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
        class AnimalBaseConfig(C.Config, ABC): ...

        registry = Registry(AnimalBaseConfig, "type")

        @registry.register
        class DogConfig(AnimalBaseConfig): ...

        @registry.register
        class CatConfig(AnimalBaseConfig): ...

        @registry.rebuild_on_registers
        class ProgramConfig(C.Config):
            animal: Annotated[AnimalBaseConfig, registry.RegistryResolution()]

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

        def _rebuild(_: type[C.Config]):
            cls.model_rebuild(force=True, raise_errors=False)

        self._on_register_callbacks.append(_rebuild)
        _rebuild(cls)
        return cls

    def pydantic_schema(self):
        """Generate a Pydantic schema for the union of all registered types.

        Returns:
            A CoreSchema representing the discriminated union

        Raises:
            ValueError: If no types are registered
        """

        # Make sure at least one element is registered
        if not self._elements:
            raise ValueError(
                "No elements registered in the registry. "
                "Please register at least one element."
            )

        # Construct the choices for the union schema
        choices: list[core_schema.CoreSchema | tuple[CoreSchema, str]] = []
        for e in self._elements:
            cls = cast(type[C.Config], e.cls)
            choices.append(
                core_schema.model_schema(cls, schema=cls.__pydantic_core_schema__)
            )
        return core_schema.union_schema(
            choices,
            auto_collapse=False,
        )

    def type_adapter(self):
        """Create a TypeAdapter for validating against the registry's types.

        Returns:
            A TypeAdapter for validating/serializing registry types
        """

        # Construct the annotated union type.
        t = typing.Union[tuple(e.cls for e in self._elements)]  # type: ignore
        t = typing.Annotated[t, C.Field(discriminator=self.discriminator_field)]

        # Create the TypeAdapter class for this type
        return C.TypeAdapter[TConfig](t)

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

    def RegistryResolution(self):
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
            class Config(C.Config):
                # This field will accept any type registered with 'registry'
                animal: Annotated[AnimalBase, registry.RegistryResolution()]

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
               (e.g., Annotated[AnimalBase, registry.RegistryResolution()])

            3. This can be used with any Pydantic field type that accepts a model, including
               nested within lists, dicts, Optional[], etc.

            4. The field will validate that the discriminator field matches one of the
               registered types and will raise a validation error if an unknown type
               is provided.

        This method is typically used in conjunction with rebuild_on_registers to create
        fully dynamic models that can handle new types as they are registered:
        ```python
        # In your base package:
        class AnimalBase(C.Config, ABC):
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
        class ProgramConfig(C.Config):
            animal: Annotated[AnimalBase, registry.RegistryResolution()]

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

        registry = self

        class _RegistryTypeAnnotation:
            @classmethod
            def __get_pydantic_core_schema__(
                cls,
                source_type: Any,
                handler: GetCoreSchemaHandler,
            ) -> core_schema.CoreSchema:
                nonlocal registry
                return core_schema.with_default_schema(registry.pydantic_schema())

        return _RegistryTypeAnnotation


@dataclass(frozen=True)
class _RegistryEntry:
    tag: str
    cls: type[C.Config]


def _unwrap_annotated_recurive(typ: Any):
    while typing.get_origin(typ) is typing.Annotated:
        typ = typing.get_args(typ)[0]
    return typ
