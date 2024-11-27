# Draft Configs

Draft configs allow for a nicer API when creating configurations. Instead of relying on JSON or YAML files, you can create your configs using pure Python.

## Basic Usage

```python
config = MyConfig.draft()

# Set some values
config.a = 10
config.b = "hello"

# Finalize the config
config = config.finalize()
```

## Motivation

The primary motivation behind draft configs is to provide a cleaner and more Pythonic way of creating configurations. By leveraging the power of Python, you can define your configs in a more readable and maintainable manner.

## Usage Guide

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

## Benefits

* More intuitive configuration creation
* Better IDE support with autocompletion
* Immediate type checking as you set values
* Ability to use Python expressions and logic
* Clear separation between configuration creation and validation
