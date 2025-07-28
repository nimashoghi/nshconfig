# Singleton Pattern for Config Classes

## Overview

The `Singleton` utility in `nshconfig` provides a robust, type-safe, and thread-safe way to manage singleton instances of configuration classes. It is designed for scenarios where a single, globally accessible configuration instance is required throughout the lifetime of an application or process.

This feature is implemented as a generic descriptor that can be used in two ways:

1. **As a descriptor** attached to config classes as class variables
2. **As a global singleton** created using the `singleton()` factory function

## Key Features

- **Type-Safe:** Uses generics and type hints for safe, explicit usage.
- **Thread-Safe:** Initialization is protected by a reentrant lock.
- **Descriptor-Based:** Can attach to config classes as a class variable, automatically binding to the correct owner.
- **Global Support:** Can be created as standalone global singletons using `singleton(cls)`.
- **Inheritance-Aware:** Prevents accidental sharing of singletons between unrelated subclasses unless explicitly intended (descriptor mode).
- **Explicit Reset:** Allows resetting the singleton for testability and re-initialization.

## Usage

### Method 1: As a Class Descriptor (Original)

```python
from typing import ClassVar, Self
from nshconfig import Config, Singleton

class MyConfig(Config):
    singleton: ClassVar[Singleton[Self]] = Singleton[Self]()
    value: str
    number: int = 0
```

### Method 2: As a Global Singleton (New)

```python
from nshconfig import Config, singleton

class MyConfig(Config):
    value: str
    number: int = 0

# Create a global singleton
my_config_singleton = singleton(MyConfig)
```

### Initializing and Accessing the Singleton

Both methods use the same API for initialization and access:

```python
# Initialize with keyword arguments (creates a new instance)
config = my_config_singleton.initialize(value="foo", number=42)

# Or initialize with an existing instance
instance = MyConfig(value="bar")
config = my_config_singleton.initialize(instance)

# Access the singleton instance
same_config = my_config_singleton.instance()
assert same_config is config

# Try to get the instance if it exists (returns None if not initialized)
maybe_config = my_config_singleton.try_instance()
```

### Resetting the Singleton

```python
# Reset the singleton (useful for tests or re-initialization)
my_config_singleton.reset()
assert my_config_singleton.try_instance() is None
```

### Thread Safety

Initialization is thread-safe. If multiple threads attempt to initialize the singleton simultaneously, only one instance will be created; others will receive a warning and the original instance.

### Choosing Between Methods

**Use the descriptor method (Method 1) when:**

- You want the singleton to be directly accessible from the class (`MyConfig.singleton`)
- You're working with inheritance hierarchies and want strict separation between classes
- You prefer the more explicit class-based approach

**Use the global singleton method (Method 2) when:**

- You want more flexibility in where you define the singleton
- You're working with classes you can't modify
- You prefer a more functional approach
- You want to create multiple independent singletons for the same class

### Inheritance Behavior (Descriptor Mode Only)

- **Each subclass must define its own `singleton` if it wants a separate instance.**
- If a subclass does not define its own `singleton`, attempts to access it will raise a `TypeError`.
- To explicitly share a singleton with a base class, assign it directly:

    ```python
    class BaseConfig(Config):
        singleton: ClassVar[Singleton[Self]] = Singleton[Self]()

    class DerivedConfig(BaseConfig):
        singleton: ClassVar[Singleton[BaseConfig]] = BaseConfig.singleton  # Explicitly share
    ```

### Independence of Methods

Global singletons and descriptor singletons are completely independent, even for the same class:

```python
class MyConfig(Config):
    singleton: ClassVar[Singleton[Self]] = Singleton[Self]()
    value: str

# Global singleton for the same class
global_singleton = singleton(MyConfig)

# These are completely separate
config1 = MyConfig.singleton.initialize(value="descriptor")
config2 = global_singleton.initialize(value="global")
assert config1 is not config2
```

## API Reference

### `Singleton`

```python
class Singleton(Generic[T]):
    def __init__(self, cls: type[T] | None = None) -> None
    def initialize(self, instance: T) -> T
    def initialize(self, **kwargs: Any) -> T
    def instance(self) -> T
    def try_instance(self) -> T | None
    def reset(self) -> None
```

- **`__init__(cls=None)`**: Initialize a singleton. If `cls` is provided, creates a global singleton bound to that class.
- **`initialize(...)`**: Initializes the singleton. Can be called with an existing instance or with keyword arguments to construct a new one. If already initialized, returns the existing instance and emits a warning.
- **`instance()`**: Returns the singleton instance. Raises `RuntimeError` if not initialized.
- **`try_instance()`**: Returns the singleton instance if initialized, else `None`.
- **`reset()`**: Resets the singleton, allowing re-initialization.

### `singleton()`

```python
def singleton(cls: type[T]) -> Singleton[T]
```

Factory function to create a global singleton for a given class.

- **`cls`**: The class type to create a singleton for.
- **Returns**: A `Singleton` instance bound to the given class.

## Example: Multiple Singletons

### Descriptor Method

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

### Global Method

```python
class ConfigA(Config):
    foo: int

class ConfigB(Config):
    bar: str

singleton_a = singleton(ConfigA)
singleton_b = singleton(ConfigB)

a = singleton_a.initialize(foo=1)
b = singleton_b.initialize(bar="hello")
assert a is not b
```

## Example: Inheritance (Descriptor Method)

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

## Example: Global Singleton Usage

```python
from nshconfig import Config, singleton

class DatabaseConfig(Config):
    host: str
    port: int
    database: str

# Create global singleton
db_config = singleton(DatabaseConfig)

# Initialize once at application startup
config = db_config.initialize(
    host="localhost",
    port=5432,
    database="myapp"
)

# Access anywhere in your application
def get_database_connection():
    config = db_config.instance()
    return connect(config.host, config.port, config.database)
```

## Error Handling

- Accessing `instance()` before initialization raises `RuntimeError`.
- Calling `initialize()` with no arguments raises `TypeError`.
- Accessing a singleton from a subclass that does not define or explicitly assign it raises `TypeError`.

## Testing

See test_singleton.py for comprehensive test coverage, including thread safety, inheritance, and reset behavior.
