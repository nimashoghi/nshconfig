# nshconfig

Fully typed configuration management, powered by Pydantic

## Table of Contents <!-- omit in toc -->
- [nshconfig](#nshconfig)
    - [Motivation](#motivation)
    - [Installation](#installation)
    - [Usage](#usage)
    - [Features](#features)
        - [Draft Configs](#draft-configs)
            - [Motivation](#motivation-1)
            - [Usage Guide](#usage-guide)
        - [MISSING Constant](#missing-constant)
            - [Motivation](#motivation-2)
        - [Seamless Integration with PyTorch Lightning](#seamless-integration-with-pytorch-lightning)
    - [Credit](#credit)
    - [Contributing](#contributing)
    - [License](#license)


## Motivation

As a machine learning researcher, I often found myself running numerous training jobs with various hyperparameters for the models I was working on. Keeping track of these parameters in a fully typed manner became increasingly important. While the excellent `pydantic` library provided most of the functionality I needed, I wanted to add a few extra features to streamline my workflow. This led to the creation of `nshconfig`.


## Installation

You can install `nshconfig` via pip:

```bash
pip install nshconfig
```

## Usage

While the primary use case for `nshconfig` is in machine learning projects, it can be used in any Python project where you need to store configurations in a fully typed manner.

Here's a basic example of how to use `nshconfig`:

```python
import nshconfig as C

class MyConfig(C.Config):
    field1: int
    field2: str
    field3: C.AllowMissing[float] = C.MISSING

config = MyConfig.draft()
config.field1 = 42
config.field2 = "hello"
final_config = config.finalize()

print(final_config)
```

For more advanced usage and examples, please refer to the documentation.

## Features

### Draft Configs

Draft configs allow for a nicer API when creating configurations. Instead of relying on JSON or YAML files, you can create your configs using pure Python:

```python
config = MyConfig.draft()

# Set some values
config.a = 10
config.b = "hello"

# Finalize the config
config = config.finalize()
```

This approach enables a more intuitive and expressive way of defining your configurations.

#### Motivation

The primary motivation behind draft configs is to provide a cleaner and more Pythonic way of creating configurations. By leveraging the power of Python, you can define your configs in a more readable and maintainable manner.

#### Usage Guide

1. Create a draft config using the `draft()` class method:
   ```python
   config = MyConfig.draft()
   ```

2. Set the desired values on the draft config:
   ```python
   config.field1 = value1
   config.field2 = value2
   ```

3. Finalize the draft config to obtain the validated configuration:
   ```python
   final_config = config.finalize()
   ```

### MISSING Constant

The `MISSING` constant is similar to `None`, but with a key difference. While `None` has the type `NoneType` and can only be assigned to fields of type `T | None`, the `MISSING` constant has the type `Any` and can be assigned to fields of any type.

#### Motivation

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
# ^ The above line is valid code, but the type-checker will complain because `age_str` could be `None`.
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

### Seamless Integration with PyTorch Lightning

`nshconfig` seamlessly integrates with PyTorch Lightning by implementing the `Mapping` interface. This allows you to use your configs directly as the `hparams` argument in your Lightning modules without any additional effort.

## Credit

`nshconfig` is built on top of the incredible `pydantic` library. Massive credit goes to the `pydantic` team for creating such a powerful and flexible tool for data validation and settings management.

## Contributing

Contributions are welcome! If you find any issues or have suggestions for improvement, please open an issue or submit a pull request on the GitHub repository.

## License

`nshconfig` is open-source software licensed under the [MIT License](LICENSE).
