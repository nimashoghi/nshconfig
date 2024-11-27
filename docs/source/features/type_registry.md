# Dynamic Type Registry

The Registry system enables dynamic registration of subtypes, allowing you to create extensible configurations that can be enhanced at runtime. This is particularly useful for plugin systems or any scenario where you want to allow users to add new types to your configuration schema.

## Basic Usage

Here's a simple example of using the Registry system:

```python
import nshconfig as C
from abc import ABC, abstractmethod
from typing import Literal, Annotated

# Define your base configuration
class AnimalConfig(C.Config, ABC):
    @abstractmethod
    def make_sound(self) -> str: ...

# Create a registry for animal types
animal_registry = C.Registry(
    AnimalConfig,
    discriminator="type"  # Discriminator field to determine the type
)

# Register some implementations
@animal_registry.register
class DogConfig(AnimalConfig):
    type: Literal["dog"] = "dog"
    name: str

    def make_sound(self) -> str:
        return "Woof!"

@animal_registry.register
class CatConfig(AnimalConfig):
    type: Literal["cat"] = "cat"
    name: str

    def make_sound(self) -> str:
        return "Meow!"

# Create a config that uses the registry
@animal_registry.rebuild_on_registers
class ProgramConfig(C.Config):
    animal: Annotated[AnimalConfig, animal_registry.DynamicResolution()]

# Use it!
def main(program_config: ProgramConfig):
    print(program_config.animal.make_sound())

main(ProgramConfig(animal=DogConfig(name="Buddy")))  # Output: Woof!
main(ProgramConfig(animal=CatConfig(name="Whiskers")))  # Output: Meow!
```

## Plugin System Support

The real power of the Registry system comes when building extensible applications. Other packages can register new types with your registry:

```python
# In a separate plugin package:
@animal_registry.register
class BirdConfig(AnimalConfig):
    type: Literal["bird"] = "bird"
    name: str
    wingspan: float

    def make_sound(self) -> str:
        return "Tweet!"

# This works automatically, even though BirdConfig was registered after
# ProgramConfig was defined
main(ProgramConfig(
    animal=BirdConfig(name="Tweety", wingspan=1.2)
))  # Output: Tweet!
```

## Key Features

1. **Type Safety**: Full type checking support with discriminated unions
2. **Runtime Extensibility**: Register new types even after config classes are defined
3. **Validation**: Automatic validation of discriminator fields and type matching
4. **Plugin Support**: Perfect for building extensible applications
5. **Pydantic Integration**: Seamless integration with Pydantic's validation system

## When to Use

The Registry system is particularly useful when:
- Building plugin systems that need configuration support
- Creating extensible applications where users can add new types
- Working with configurations that need to handle different variants of a base type
- Implementing pattern matching or strategy patterns with configuration support
