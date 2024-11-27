# MISSING Constant

The `MISSING` constant is similar to `None`, but with a key difference. While `None` has the type `NoneType` and can only be assigned to fields of type `T | None`, the `MISSING` constant has the type `Any` and can be assigned to fields of any type.

## Motivation

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
# ^ The above line is valid code, but the type-checker will complain
# because `age_str` could be `None`.
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
