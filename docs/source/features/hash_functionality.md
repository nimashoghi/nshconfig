# Hash Functionality

NSHConfig provides automatic hash functionality for `Config` classes, making them usable as dictionary keys and in sets.

## Overview

By default, all `Config` classes are made hashable through a mechanism that automatically adds a `__hash__` method to the class. This allows you to:

1. Use `hash()` on a config instance
2. Use config instances as dictionary keys
3. Add config instances to sets
4. Deduplicate configs easily using `dict.fromkeys` or sets

This is particularly useful when you need to store and retrieve configs in dictionaries or sets.

## How It Works

The hash function is automatically added to your `Config` class during class initialization. It computes a hash based on the values of all fields in the config, ensuring that:

- Equal configs (those with identical field values) produce identical hash values
- Different configs produce different hash values (with high probability)

## Example Usage

```python
from typing import ClassVar
import nshconfig as C

class MyConfig(C.Config):
    name: str = "default"
    value: int = 0

# Creating configs
config1 = MyConfig(name="example", value=42)
config2 = MyConfig(name="example", value=42)
config3 = MyConfig(name="different", value=100)

# Configs with identical values have identical hashes
assert hash(config1) == hash(config2)
assert hash(config1) != hash(config3)

# Using configs as dictionary keys
config_map = {
    config1: "First config",
    config3: "Third config"
}

# Retrieving values
assert config_map[config1] == "First config"
# Since config2 is equal to config1, it has the same hash and can retrieve the same value
assert config_map[config2] == "First config"

# Deduplicating configs using sets
configs = [config1, config2, config3, MyConfig(name="example", value=42)]
unique_configs = list(set(configs))
assert len(unique_configs) == 2  # Only 2 unique configs (config1/config2 and config3)
```

## Controlling Hash Functionality

You can disable the automatic hash generation for a specific `Config` class by setting `set_default_hash` to `False` in the `model_config`:

```python
from typing import ClassVar
import nshconfig as C

class NonHashableConfig(C.Config):
    model_config: ClassVar[dict] = {"set_default_hash": False}

    name: str = "default"
    value: int = 0
```

With this configuration, attempting to hash an instance of `NonHashableConfig` will raise a `TypeError`.

## Limitations

1. **Mutability Warning**: Since `Config` objects are mutable, you should be careful when using them as dictionary keys. If you modify a config after using it as a key, you may not be able to retrieve its associated value anymore, as the hash will change.

2. **Nested Configs**: For configs containing other configs, all nested configs must also be hashable for the parent config to be hashable.

## Implementation Details

The hash function is implemented using Pydantic's internal `set_default_hash_func` mechanism. It computes a hash based on the model's representation, which includes all field values.
