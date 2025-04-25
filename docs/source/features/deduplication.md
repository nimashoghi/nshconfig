# Deduplication Functionality

NSHConfig provides utility functions to deduplicate config instances based on equality comparison rather than hashing.

## Overview

The library includes dedicated functions for removing duplicate configs from collections:

- `deduplicate()`: A general utility function that removes duplicates from any iterable
- `deduplicate_configs()`: A specialized alias for deduplicating config instances

## Implementation

Unlike traditional Python deduplication with sets (which uses hashing), NSHConfig's deduplication functions use explicit equality comparison. This approach is particularly useful when:

- You're working with complex config objects
- You need to deduplicate objects that might not be hashable
- You want to control exactly how deduplication works

## How It Works

The deduplication process:

1. Iterates through each item in the input iterable
2. Compares it to previously seen items using equality comparison (`==`)
3. Only keeps the first occurrence of each unique item
4. Returns a new list with duplicates removed

## Example Usage

```python
from __future__ import annotations

import nshconfig as C
from nshconfig._src.utils import deduplicate_configs

class MyConfig(C.Config):
    name: str = "default"
    value: int = 0

# Creating configs
config1 = MyConfig(name="example", value=42)
config2 = MyConfig(name="example", value=42)  # Same values as config1
config3 = MyConfig(name="different", value=100)

# Deduplicating configs
configs = [config1, config2, config3, MyConfig(name="example", value=42)]
unique_configs = deduplicate_configs(configs)

assert len(unique_configs) == 2  # Only 2 unique configs
assert unique_configs[0] == config1  # First occurrence of this config
assert unique_configs[1] == config3  # First occurrence of this config
```

## Using With Custom Types

The `deduplicate` function is generic and can be used with any type that supports equality comparison:

```python
from nshconfig._src.utils import deduplicate

# Deduplicating strings
strings = ["apple", "banana", "apple", "cherry", "banana"]
unique_strings = deduplicate(strings)
assert unique_strings == ["apple", "banana", "cherry"]

# Deduplicating custom objects
class CustomObject:
    def __init__(self, id: int, name: str):
        self.id = id
        self.name = name

    def __eq__(self, other):
        if not isinstance(other, CustomObject):
            return False
        return self.id == other.id  # Equality based on ID only

objects = [
    CustomObject(1, "First"),
    CustomObject(2, "Second"),
    CustomObject(1, "Different name, same ID"),
    CustomObject(3, "Third")
]

unique_objects = deduplicate(objects)
assert len(unique_objects) == 3  # Only 3 unique objects by ID
```

## Relation to Hash Functionality

While NSHConfig also provides automatic hash functionality for `Config` classes (see [Hash Functionality](hash_functionality.md)), the deduplication utilities operate independently of hashing. This means:

1. You can deduplicate configs even if they don't have a `__hash__` method
2. Deduplication is based on equality comparison, not hash values
3. The order of items in the original collection is preserved

## Performance Considerations

The current implementation uses a linear search (`any(config == seen_config for seen_config in seen)`) which has O(n²) time complexity in the worst case. For very large collections, this might be less efficient than hash-based deduplication.

However, this approach:
- Does not require objects to be hashable
- Preserves the original order of items
- Uses explicit equality comparison which may be desirable in some cases
