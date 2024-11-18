# nshconfig <!-- omit in toc -->

Fully typed configuration management, powered by [Pydantic](https://github.com/pydantic/pydantic/)

## Table of Contents <!-- omit in toc -->
- [Motivation](#motivation)
- [Installation](#installation)
- [Usage](#usage)
- [Features](#features)
    - [Draft Configs](#draft-configs)
        - [Motivation](#motivation-1)
        - [Usage Guide](#usage-guide)
    - [Dynamic Type Registry](#dynamic-type-registry)
        - [Basic Usage](#basic-usage)
        - [Plugin System Support](#plugin-system-support)
        - [Key Features](#key-features)
        - [When to Use](#when-to-use)
    - [Configuration Codegen](#configuration-codegen)
        - [Basic Usage](#basic-usage-1)
        - [Features](#features-1)
            - [Type-Safe Exports](#type-safe-exports)
            - [TypedDict Generation](#typeddict-generation)
            - [JSON Schema Generation](#json-schema-generation)
        - [Command Line Options](#command-line-options)
        - [Use Cases](#use-cases)
    - [MISSING Constant](#missing-constant)
        - [Motivation](#motivation-2)
    - [Seamless Integration with PyTorch Lightning](#seamless-integration-with-pytorch-lightning)
- [Credit](#credit)
- [Contributing](#contributing)
- [License](#license)


## Motivation

As a machine learning researcher, I often found myself running numerous training jobs with various hyperparameters for the models I was working on. Keeping track of these parameters in a fully typed manner became increasingly important. While the excellent `pydantic` library provided most of the functionality I needed, I wanted to add a few extra features to streamline my workflow. This led to the creation of `nshconfig`.


## Installation

You can install `nshconfig` via pip:

```bash
pip install nshconfig
```

## Usage

While the primary use case for `nshconfig` is in machine learning projects, it can be used in any Python project where you need to store configurations in a fully typed manner.

Here's a basic example of how to use `nshconfig`:

```python
import nshconfig as C

class MyConfig(C.Config):
    field1: int
    field2: str
    field3: C.AllowMissing[float] = C.MISSING

config = MyConfig.draft()
config.field1 = 42
config.field2 = "hello"
final_config = config.finalize()

print(final_config)
```

For more advanced usage and examples, please refer to the documentation.

## Features

- Draft configs for a more Pythonic configuration creation experience
- Dynamic type registry for building extensible, plugin-based systems
- MISSING constant for better handling of optional fields
- Seamless integration with PyTorch Lightning


### Draft Configs

Draft configs allow for a nicer API when creating configurations. Instead of relying on JSON or YAML files, you can create your configs using pure Python:

```python
config = MyConfig.draft()

# Set some values
config.a = 10
config.b = "hello"

# Finalize the config
config = config.finalize()
```

This approach enables a more intuitive and expressive way of defining your configurations.

#### Motivation

The primary motivation behind draft configs is to provide a cleaner and more Pythonic way of creating configurations. By leveraging the power of Python, you can define your configs in a more readable and maintainable manner.

#### Usage Guide

1. Create a draft config using the `draft()` class method:
   ```python
   config = MyConfig.draft()
   ```

2. Set the desired values on the draft config:
   ```python
   config.field1 = value1
   config.field2 = value2
   ```

3. Finalize the draft config to obtain the validated configuration:
   ```python
   final_config = config.finalize()
   ```

Based on your code and its functionality, I'll write a new section for the README that showcases the Registry feature. Here's my suggested addition:

### Dynamic Type Registry

The Registry system enables dynamic registration of subtypes, allowing you to create extensible configurations that can be enhanced at runtime. This is particularly useful for plugin systems or any scenario where you want to allow users to add new types to your configuration schema.

#### Basic Usage

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
    discriminator="type"  # Discriminator field to determine the type of the config
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

#### Plugin System Support

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

# This works automatically, even though BirdConfig was registered after ProgramConfig was defined
main(ProgramConfig(animal=BirdConfig(name="Tweety", wingspan=1.2)))  # Output: Tweet!
```

#### Key Features

1. **Type Safety**: Full type checking support with discriminated unions
2. **Runtime Extensibility**: Register new types even after config classes are defined
3. **Validation**: Automatic validation of discriminator fields and type matching
4. **Plugin Support**: Perfect for building extensible applications
5. **Pydantic Integration**: Seamless integration with Pydantic's validation system

#### When to Use

The Registry system is particularly useful when:
- Building plugin systems that need configuration support
- Creating extensible applications where users can add new types
- Working with configurations that need to handle different variants of a base type
- Implementing pattern matching or strategy patterns with configuration support


### Configuration Codegen

The configuration codegen feature provides tools to generate clean, importable interfaces and type definitions for your configurations. This is particularly useful for:

1. Creating type-safe client libraries from your configuration definitions
2. Generating TypeScript-like type definitions for better IDE support
3. Building plugin systems with strong type guarantees
4. Generating JSON schemas for configuration validation

#### Basic Usage

You can use the configuration codegen feature via the command line:

```bash
nshconfig-export my_module -o exported_configs
```

This will:
1. Find all configuration classes in `my_module`
2. Generate a clean export hierarchy in the `exported_configs` directory
3. Optionally generate TypedDict definitions and JSON schemas

#### Features

##### Type-Safe Exports

The codegen tool automatically creates a clean export hierarchy that maintains your module structure:

```python
# Original: my_module/configs/model.py
class ModelConfig(Config):
    hidden_size: int
    num_layers: int

# Generated: exported_configs/configs/model.py
from my_module.configs.model import ModelConfig

# Your code can now import from the generated interface:
from exported_configs.configs.model import ModelConfig
```

##### TypedDict Generation

With the `--generate-typed-dicts` flag, nshconfig generates TypedDict versions of your configurations along with type-safe creator functions:

```bash
nshconfig-export my_module -o exported_configs --generate-typed-dicts
```

This creates TypedDict definitions that mirror your Config classes:

```python
# Original Config
class ModelConfig(Config):
    hidden_size: int
    num_layers: int

# Generated TypedDict
class ModelConfigTypedDict(TypedDict):
    hidden_size: int
    num_layers: int

# Generated creator function
def CreateModelConfig(
    dict: ModelConfigTypedDict, /  # Positional only dict argument
) -> ModelConfig: ...

def CreateModelConfig(
    **dict: Unpack[ModelConfigTypedDict]  # Keyword arguments
) -> ModelConfig: ...
```

You can use these definitions in several ways:

```python
from exported_configs.configs.model import ModelConfig, ModelConfigTypedDict, CreateModelConfig

# Use the TypedDict for type-safe dictionaries
config_dict: ModelConfigTypedDict = {
    "hidden_size": 256,
    "num_layers": 4
}

# Create configs from dictionaries
config1 = CreateModelConfig(config_dict)
config2 = CreateModelConfig(hidden_size=256, num_layers=4)

# Both are equivalent to:
config3 = ModelConfig(hidden_size=256, num_layers=4)
```

##### JSON Schema Generation

With the `--generate-json-schema` flag, nshconfig generates JSON schemas for your configurations:

```bash
nshconfig-export my_module -o exported_configs --generate-json-schema
```

This creates `.schema.json` files that can be used for:
- Configuration validation in any language
- API documentation
- IDE support for JSON/YAML files
- Integration with other tools

#### Command Line Options

```bash
nshconfig-export [-h] -o OUTPUT [--remove-existing | --no-remove-existing]
                [--recursive | --no-recursive] [--verbose | --no-verbose]
                [--ignore-module IGNORE_MODULE] [--ignore-abc | --no-ignore-abc]
                [--export-generics | --no-export-generics]
                [--generate-typed-dicts | --no-generate-typed-dicts]
                [--generate-json-schema | --no-generate-json-schema]
                module
```

Key options:
- `--recursive`: Recursively process all submodules (default: True)
- `--ignore-abc`: Skip abstract base classes
- `--ignore-module`: Ignore specific modules
- `--export-generics`: Include generic type definitions
- `--generate-typed-dicts`: Generate TypedDict definitions
- `--generate-json-schema`: Generate JSON schemas

#### Use Cases

1. **Client Libraries**: Generate clean, minimal interfaces for your configurations that clients can depend on without pulling in your entire codebase.

2. **Plugin Systems**: Use TypedDict definitions to allow plugins to work with your configurations without depending on your core library:
```python
# Plugin code can use TypedDicts instead of importing your Config classes
from my_library_export import ModelConfigTypedDict

def process_config(config_dict: ModelConfigTypedDict) -> None:
    print(f"Processing model with {config_dict['num_layers']} layers")
```

3. **IDE Support**: Get better IDE completion and type checking when working with configuration dictionaries:
```python
from my_library_export import ModelConfigTypedDict

def create_model_config() -> ModelConfigTypedDict:
    return {
        "hidden_size": 256,  # IDE knows this needs to be an int
        "num_layers": 4      # IDE provides completion for field names
    }
```

4. **Schema Validation**: Use generated JSON schemas to validate configurations in any environment:
```python
import json
from jsonschema import validate

# Load the generated schema
with open("exported_configs/model/ModelConfig.schema.json") as f:
    schema = json.load(f)

# Validate a configuration
config = {"hidden_size": 256, "num_layers": 4}
validate(instance=config, schema=schema)
```

### MISSING Constant

The `MISSING` constant is similar to `None`, but with a key difference. While `None` has the type `NoneType` and can only be assigned to fields of type `T | None`, the `MISSING` constant has the type `Any` and can be assigned to fields of any type.

#### Motivation

The `MISSING` constant addresses a common issue when working with optional fields in configurations. Consider the following example:

```python
import nshconfig as C

# Without MISSING:
class MyConfigWithoutMissing(C.Config):
    age: int
    age_str: str | None = None

    def __post_init__(self):
        if self.age_str is None:
            self.age_str = str(self.age)

config = MyConfigWithoutMissing(age=10)
age_str_lower = config.age_str.lower()
# ^ The above line is valid code, but the type-checker will complain because `age_str` could be `None`.
```

In the above code, the type-checker will raise a complaint because `age_str` could be `None`. This is where the `MISSING` constant comes in handy:

```python
# With MISSING:
class MyConfigWithMissing(C.Config):
    age: int
    age_str: C.AllowMissing[str] = C.MISSING

    def __post_init__(self):
        if self.age_str is C.MISSING:
            self.age_str = str(self.age)

config = MyConfigWithMissing(age=10)
age_str_lower = config.age_str.lower()
# ^ No more type-checker complaints!
```

By using the `MISSING` constant, you can indicate that a field is not set during construction, and the type-checker will not raise any complaints.

### Seamless Integration with PyTorch Lightning

`nshconfig` seamlessly integrates with PyTorch Lightning by implementing the `Mapping` interface. This allows you to use your configs directly as the `hparams` argument in your Lightning modules without any additional effort.

## Credit

`nshconfig` is built on top of the incredible [`pydantic`](https://github.com/pydantic/pydantic/) library. Massive credit goes to the [`pydantic`](https://github.com/pydantic/pydantic/) team for creating such a powerful and flexible tool for data validation and settings management.

## Contributing

Contributions are welcome! If you find any issues or have suggestions for improvement, please open an issue or submit a pull request on the GitHub repository.

## License

`nshconfig` is open-source software licensed under the [MIT License](LICENSE).
