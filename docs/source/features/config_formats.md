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
- A `__config__` variable containing the configuration
- A `__create_config__` function that returns the configuration

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

## Dictionary Support

You can also create configurations directly from Python dictionaries:

```python
class ModelConfig(C.Config):
    hidden_size: int
    num_layers: int

# Create from dictionary
config = ModelConfig.from_dict({
    "hidden_size": 256,
    "num_layers": 4
})

# Convert to dictionary
config_dict = config.to_dict()
```

## Schema References

When saving to JSON or YAML, you can include schema references that enable better IDE support:

```python
# Include schema reference in JSON
config.to_json_file("config.json", with_schema=True)

# Include schema reference in YAML
config.to_yaml_file("config.yaml", with_schema=True)
```

The schema references help IDEs provide autocompletion and validation when editing the configuration files.
