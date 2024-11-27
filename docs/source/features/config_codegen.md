# Configuration Codegen

The configuration codegen feature provides tools to generate clean, importable interfaces and type definitions for your configurations. This is particularly useful for:

1. Creating type-safe client libraries from your configuration definitions
2. Generating TypeScript-like type definitions for better IDE support
3. Building plugin systems with strong type guarantees
4. Generating JSON schemas for configuration validation

## Basic Usage

You can use the configuration codegen feature via the command line:

```bash
nshconfig-export my_module -o exported_configs
```

This will:
1. Find all configuration classes in `my_module`
2. Generate a clean export hierarchy in the `exported_configs` directory
3. Optionally generate TypedDict definitions and JSON schemas

## Features

### Type-Safe Exports

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

### TypedDict Generation

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

# Generated creator functions
def CreateModelConfig(
    dict: ModelConfigTypedDict, /  # Positional only dict argument
) -> ModelConfig: ...

def CreateModelConfig(
    **dict: Unpack[ModelConfigTypedDict]  # Keyword arguments
) -> ModelConfig: ...
```

You can use these definitions in several ways:

```python
from exported_configs.configs.model import (
    ModelConfig, ModelConfigTypedDict, CreateModelConfig
)

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

### JSON Schema Generation

With the `--generate-json-schema` flag, nshconfig generates JSON schemas for your configurations:

```bash
nshconfig-export my_module -o exported_configs --generate-json-schema
```

This creates `.schema.json` files that can be used for:
- Configuration validation in any language
- API documentation
- IDE support for JSON/YAML files
- Integration with other tools

## Command Line Options

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

## Use Cases

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
