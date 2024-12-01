# Configuration Formats

nshconfig supports multiple formats for creating and serializing configurations, making it flexible for different use cases.

## Python File/Module Support

nshconfig supports loading configurations directly from Python files and modules:

```python
from mypackage import ModelConfig

# Load from Python file
config1 = ModelConfig.from_python_file("model_config.py")

# Load from Python module
config2 = ModelConfig.from_python_module("myapp.configs.model")
```

The Python file or module should export either:
- A `__config__` variable containing an instance of the configuration class
- A `__create_config__` function that returns an instance of the configuration class

Example configuration file:

```python
# model_config.py
from mypackage import ModelConfig

# Option 1: Using __config__ variable
__config__ = ModelConfig(
    hidden_size=256,
    num_layers=4
)

# Option 2: Using __create_config__ function
def __create_config__():
    return ModelConfig(
        hidden_size=256,
        num_layers=4
    )
```

## JSON Support

You can create and save configurations using JSON:

```python
import nshconfig as C

class ModelConfig(C.Config):
    hidden_size: int
    num_layers: int

# Create from JSON string
config1 = ModelConfig.from_json_str('{"hidden_size": 256, "num_layers": 4}')

# Load from JSON file
config2 = ModelConfig.from_json_file("model_config.json")

# Save to JSON string
json_str = config1.to_json_str()

# Save to JSON file
config1.to_json_file("model_config.json")
```

## YAML Support

YAML support requires installing the `pydantic-yaml` package:

```bash
pip install "nshconfig[extra]"  # Installs all extras
pip install "nshconfig[yaml]"   # Installs only the YAML extra
pip install pydantic-yaml       # Or install directly
```

Then you can work with YAML formats:

```python
class ModelConfig(C.Config):
    hidden_size: int
    num_layers: int

# Create from YAML string
config1 = ModelConfig.from_yaml_str("""
hidden_size: 256
num_layers: 4
""")

# Load from YAML file
config2 = ModelConfig.from_yaml("model_config.yaml")

# Save to YAML string
yaml_str = config1.to_yaml_str()

# Save to YAML file
config1.to_yaml_file("model_config.yaml")
```

## Type-Safe Adapters

For more fine-grained control over configuration conversion and validation, nshconfig provides a type-safe Adapter class that wraps Pydantic's [TypeAdapter](https://docs.pydantic.dev/latest/api/type_adapter/) functionality. While Config classes provide their own serialization methods, the Adapter class allows you to validate and serialize any type - including complex nested types and non-Config types:

```python
from nshconfig import Config
from nshconfig.adapter import Adapter

class ModelConfig(Config):
    hidden_size: int
    num_layers: int

# Create adapters for different types
model_adapter = Adapter(ModelConfig)
tuple_adapter = Adapter(tuple[ModelConfig, int, str])
dict_adapter = Adapter(dict[str, ModelConfig])

# Validate and convert different data structures
config = model_adapter.from_dict({
    "hidden_size": 256,
    "num_layers": 4
})

# Validate tuple of (ModelConfig, int, str)
validated = tuple_adapter.from_dict([
    {"hidden_size": 256, "num_layers": 4},
    42,
    "hello"
])

# Validate dictionary of ModelConfigs
configs = dict_adapter.from_dict({
    "model1": {"hidden_size": 256, "num_layers": 4},
    "model2": {"hidden_size": 512, "num_layers": 8}
})
```

The adapter provides type-safe conversion between different formats while maintaining all validation rules. This is particularly useful when you need to:
- Load configuration files in different formats
- Save configuration state to disk
- Convert between different representations of your data while ensuring type safety
- Validate external data against complex types
- Work with collections of configurations

Each conversion method accepts additional parameters to customize the serialization/deserialization process, such as excluding certain fields, handling defaults, or controlling validation strictness. See Pydantic's TypeAdapter documentation for more details on the underlying functionality.

## Schema References

When saving to JSON or YAML, you can include schema references that enable better IDE support:

```python
# Include schema reference in JSON
config.to_json_file("config.json", with_schema=True)

# Include schema reference in YAML
config.to_yaml_file("config.yaml", with_schema=True)
```

The schema references help IDEs provide autocompletion and validation when editing the configuration files.
