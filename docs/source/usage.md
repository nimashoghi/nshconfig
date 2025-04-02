# Basic Usage

While the primary use case for `nshconfig` is in machine learning projects, it can be used in any Python project where you need to store configurations in a fully typed manner.

Here's a basic example:

```python
import nshconfig as C

class MyConfig(C.Config):
    field1: int
    field2: str
    field3: Annotated[float, C.AllowMissing()] = C.MISSING

config = MyConfig.draft()
config.field1 = 42
config.field2 = "hello"
final_config = config.finalize()

print(final_config)
```

This example demonstrates:

1. Creating a configuration class
2. Using draft mode for configuration creation
3. Setting configuration values
4. Finalizing the configuration

For more advanced features, check out the [Features](features/index) section.
