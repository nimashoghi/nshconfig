# MISSING Constant

The `MISSING` constant is similar to `None`, but with a key difference. While `None` has the type `NoneType` and can only be assigned to fields of type `T | None`, the `MISSING` constant has the type `Any` and can be assigned to fields of any type.

## Motivation

The `MISSING` constant addresses a common issue when working with optional fields in configurations. Consider the following example:

```python
import nshconfig as C
from typing import Annotated

# Without MISSING:
class MyConfigWithoutMissing(C.Config):
    age: int
    age_str: str | None = None

    def __post_init__(self):
        if self.age_str is None:
            self.age_str = str(self.age)

config = MyConfigWithoutMissing(age=10)
age_str_lower = config.age_str.lower()
# ^ The above line is valid code, but the type-checker will complain
# because `age_str` could be `None`.
```

In the above code, the type-checker will raise a complaint because `age_str` could be `None`. This is where the `MISSING` constant comes in handy:

## Using MISSING with AllowMissing

There are two syntaxes for using the `MISSING` constant with the `AllowMissing` feature:

### Syntax 1: Using Annotated

```python
# With MISSING (Annotated syntax):
class MyConfigWithMissing(C.Config):
    age: int
    age_str: Annotated[str, C.AllowMissing] = C.MISSING

    def __post_init__(self):
        if self.age_str is C.MISSING:
            self.age_str = str(self.age)

config = MyConfigWithMissing(age=10)
age_str_lower = config.age_str.lower()
# ^ No more type-checker complaints!
```

### Syntax 2: Direct AllowMissing Type

A more concise syntax is available using `AllowMissing` directly as a generic type:

```python
# With MISSING (direct AllowMissing syntax):
class MyConfigWithMissing(C.Config):
    age: int
    age_str: C.AllowMissing[str] = C.MISSING

    def __post_init__(self):
        if self.age_str is C.MISSING:
            self.age_str = str(self.age)

config = MyConfigWithMissing(age=10)
age_str_lower = config.age_str.lower()
# ^ No type-checker complaints with this syntax either!
```

This second syntax is more concise and follows modern Python typing patterns.

By using either syntax with the `MISSING` constant, you can indicate that a field is not set during construction, and the type-checker will not raise any complaints.

## Validating No Missing Values

If you want to ensure that your configuration doesn't contain any `MISSING` values after initialization, you can call the `model_validate_no_missing()` method:

```python
class MyConfig(C.Config):
    required_field: int
    optional_field: C.AllowMissing[str] = C.MISSING  # Using the direct syntax

    def __post_init__(self):
        # Set default values, etc.
        if self.optional_field is C.MISSING:
            self.optional_field = "default value"

        # Ensure no MISSING values remain
        self.model_validate_no_missing()

# This will work fine:
config1 = MyConfig(required_field=10)

# This would raise a validation error if model_validate_no_missing()
# wasn't setting a default value in __post_init__:
config1.optional_field  # "default value"
```

The `model_validate_no_missing()` method is useful when you want to ensure all fields have proper values before using your configuration.

## Using AllowMissing with Complex Types

The `AllowMissing` feature works seamlessly with complex types like lists, dictionaries, and nested configurations:

```python
class NestedConfig(C.Config):
    value: C.AllowMissing[str] = C.MISSING

class ParentConfig(C.Config):
    name: str
    nested: C.AllowMissing[NestedConfig] = C.MISSING
    items: C.AllowMissing[list[int]] = C.MISSING
    settings: C.AllowMissing[dict[str, float]] = C.MISSING

# Create with MISSING values
config = ParentConfig(name="test")
assert config.nested is C.MISSING
assert config.items is C.MISSING
assert config.settings is C.MISSING

# Assign values later
config.items = [1, 2, 3]
config.settings = {"scale": 1.5, "offset": 0.1}
```

This flexibility allows for dynamic configuration patterns where some values might not be available at initialization time.
