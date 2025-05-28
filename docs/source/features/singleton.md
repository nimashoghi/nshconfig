# Singleton Pattern for Config Classes

## Overview

The `Singleton` utility in `nshconfig` provides a robust, type-safe, and thread-safe way to manage singleton instances of configuration classes. It is designed for scenarios where a single, globally accessible configuration instance is required throughout the lifetime of an application or process.

This feature is implemented as a generic descriptor, making it easy to attach to any config class and ensuring correct usage even in complex inheritance hierarchies.

## Key Features

- **Type-Safe:** Uses generics and type hints for safe, explicit usage.
- **Thread-Safe:** Initialization is protected by a reentrant lock.
- **Descriptor-Based:** Attaches to config classes as a class variable, automatically binding to the correct owner.
- **Inheritance-Aware:** Prevents accidental sharing of singletons between unrelated subclasses unless explicitly intended.
- **Explicit Reset:** Allows resetting the singleton for testability and re-initialization.

## Usage

### Defining a Singleton on a Config Class

```python
from typing import ClassVar, Self
from nshconfig import Config, Singleton

class MyConfig(Config):
    singleton: ClassVar[Singleton[Self]] = Singleton[Self]()
    value: str
    number: int = 0
```

### Initializing and Accessing the Singleton

```python
# Initialize with keyword arguments (creates a new instance)
config = MyConfig.singleton.initialize(value="foo", number=42)

# Or initialize with an existing instance
instance = MyConfig(value="bar")
config = MyConfig.singleton.initialize(instance)

# Access the singleton instance
same_config = MyConfig.singleton.instance()
assert same_config is config

# Try to get the instance if it exists (returns None if not initialized)
maybe_config = MyConfig.singleton.try_instance()
```

### Resetting the Singleton

```python
# Reset the singleton (useful for tests or re-initialization)
MyConfig.singleton.reset()
assert MyConfig.singleton.try_instance() is None
```

### Thread Safety

Initialization is thread-safe. If multiple threads attempt to initialize the singleton simultaneously, only one instance will be created; others will receive a warning and the original instance.

### Inheritance Behavior

- **Each subclass must define its own `singleton` if it wants a separate instance.**
- If a subclass does not define its own `singleton`, attempts to access it will raise a `TypeError`.
- To explicitly share a singleton with a base class, assign it directly:

    ```python
    class BaseConfig(Config):
        singleton: ClassVar[Singleton[Self]] = Singleton[Self]()

    class DerivedConfig(BaseConfig):
        singleton: ClassVar[Singleton[BaseConfig]] = BaseConfig.singleton  # Explicitly share
    ```

## API Reference

### `Singleton`

```python
class Singleton(Generic[T]):
    def initialize(self, instance: T) -> T
    def initialize(self, **kwargs: Any) -> T
    def instance(self) -> T
    def try_instance(self) -> T | None
    def reset(self) -> None
```

- **`initialize(...)`**: Initializes the singleton. Can be called with an existing instance or with keyword arguments to construct a new one. If already initialized, returns the existing instance and emits a warning.
- **`instance()`**: Returns the singleton instance. Raises `RuntimeError` if not initialized.
- **`try_instance()`**: Returns the singleton instance if initialized, else `None`.
- **`reset()`**: Resets the singleton, allowing re-initialization.

## Example: Multiple Singletons

```python
class ConfigA(Config):
    singleton: ClassVar[Singleton[Self]] = Singleton[Self]()
    foo: int

class ConfigB(Config):
    singleton: ClassVar[Singleton[Self]] = Singleton[Self]()
    bar: str

a = ConfigA.singleton.initialize(foo=1)
b = ConfigB.singleton.initialize(bar="hello")
assert a is not b
```

## Example: Inheritance

```python
class Parent(Config):
    singleton: ClassVar[Singleton[Self]] = Singleton[Self]()
    parent_value: str

class Child(Parent):
    singleton: ClassVar[Singleton[Self]] = Singleton[Self]()
    child_value: str

parent = Parent.singleton.initialize(parent_value="p")
child = Child.singleton.initialize(parent_value="c", child_value="c2")
assert Parent.singleton.instance() is not Child.singleton.instance()
```

## Error Handling

- Accessing `instance()` before initialization raises `RuntimeError`.
- Calling `initialize()` with no arguments raises `TypeError`.
- Accessing a singleton from a subclass that does not define or explicitly assign it raises `TypeError`.

## Testing

See test_singleton.py for comprehensive test coverage, including thread safety, inheritance, and reset behavior.
