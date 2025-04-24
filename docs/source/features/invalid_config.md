# Invalid Config Type

The `Invalid` class is a special configuration type that represents an invalid or impossible configuration state, similar to the `Never` type in type theory.

## Motivation

In type systems, the `Never` type represents a value that can never occur. The `Invalid` class in `nshconfig` fulfills a similar role for configuration objects. It's particularly useful in scenarios where:

1. You need a type placeholder for configurations that should never be instantiated
2. You want to create a union type that can be narrowed down at runtime
3. You need to represent failure states in a type-safe way

## Usage

The `Invalid` class is designed to immediately raise a `ValueError` when instantiated, making it impossible to create a valid instance:

```python
from nshconfig import Invalid

# This will always raise a ValueError:
try:
    config = Invalid()
except ValueError as e:
    print(e)  # Output: This is an invalid configuration.
```

## Implementation Details

The `Invalid` class is implemented using Pydantic's validator system to ensure that any attempt to create an instance results in an error:

```python
class Invalid(Config):
    """
    A class representing an invalid configuration.

    This is like the Never type (which isn't supported by Pydantic).
    """

    @model_validator(mode="before")
    @classmethod
    def invalidate(cls, data: Any) -> Any:
        raise ValueError("This is an invalid configuration.")
```

This implementation ensures that the `Invalid` class can never be instantiated, making it useful for type-level constraints and representing impossible states in your configuration system.
