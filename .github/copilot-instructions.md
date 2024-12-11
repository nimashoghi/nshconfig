# Modern Python Style Guide

## Type Hints and Imports

### Future Imports
- Always include `from __future__ import annotations` at the top of your Python files
- This enables better type hint handling and forward references

### Type Hints
- Use type hints consistently for function parameters and return values
- Leverage modern syntax introduced in Python 3.10+:
  - Use `X | Y` instead of `Union[X, Y]`
  - Use `T | None` instead of `Optional[T]`
- Import type-related utilities from their canonical sources:
  - Use `collections.abc` for collection abstract base classes (not `typing`)
  - Use `typing_extensions` for newer typing features that aren't in the standard library yet

### Path Handling
- Always use `pathlib.Path` for file system operations
- Avoid using string paths or `os.path` functions directly

## Type Definitions

### Type Aliases
- Use `TypeAliasType` for creating proper type aliases that maintain better type checking
- Define complex types at the module level for reusability
- Document type aliases with clear docstrings when their purpose isn't immediately obvious

Example:
```python
from typing_extensions import TypeAliasType
from typing import Literal

LogLevel = TypeAliasType(
    "LogLevel",
    Literal["CRITICAL", "FATAL", "ERROR", "WARN", "WARNING", "INFO", "DEBUG"],
)
```

## Function Definitions

### Parameter Type Annotations
- Always annotate function parameters with appropriate types
- Use the most specific type that accurately represents the expected input
- For collections, prefer abstract base classes over concrete implementations when possible:
  ```python
  # Good
  def process_numbers(numbers: Sequence[int]) -> None:
      pass

  # Too specific
  def process_numbers(numbers: list[int]) -> None:
      pass
  ```

### Return Type Annotations
- Always include return type annotations
- Use `-> None` explicitly when a function doesn't return anything
- For multiple return types, use the union operator:
  ```python
  def get_value() -> int | str:
      pass
  ```

## Collections and Sequences

### Type Annotations
- Use `list[T]` for mutable sequences of type T
- Use `Sequence[T]` when read-only access is sufficient
- Import collection types from `collections.abc`:
  ```python
  from collections.abc import Sequence, Mapping, Set
  ```

## Optional Values

### Handling Optional Parameters
- Use `T | None` syntax for optional values
- Be explicit about which parameters can be None
- Consider providing default values when appropriate:
  ```python
  def process_data(value: int | None = None) -> str:
      if value is None:
          return "No value provided"
      return str(value)
  ```

## Code Organization

### Import Order
1. Future imports
2. Standard library imports
3. Third-party imports
4. Local application imports

Separate each import group with a blank line:
```python
from __future__ import annotations

import sys
from pathlib import Path

from third_party_library import something

from .local_module import local_function
```

### Type Definitions Location
- Place type definitions near the top of the file, after imports
- Group related type definitions together
- Add clear comments explaining complex type definitions

## General Best Practices

### Code Formatting
- Use a consistent code formatter (like `black`)
- Maintain consistent line length (typically 88 characters for black)
- Use meaningful variable and function names
- Add appropriate whitespace for readability

### Documentation
- Include docstrings for modules, classes, and functions
- Document complex type definitions
- Use type hints as part of your documentation strategy

### Error Handling
- Use type hints to prevent type-related errors
- Handle None values explicitly when working with optional types
- Use appropriate exception handling when working with paths and I/O

## Documentation Standards

### NumPy Docstring Format
All functions and classes should have docstrings following the NumPy format. Here's a comprehensive example:

```python
def process_data(
    input_data: list[float],
    threshold: float | None = None,
    log_level: LogLevel = "INFO",
) -> dict[str, float]:
    """Process numerical data with optional threshold filtering.

    Processes a list of floating point numbers by applying various
    statistical operations. If a threshold is provided, filters out
    values below it.

    Parameters
    ----------
    input_data : list[float]
        List of numerical values to process
    threshold : float | None, optional
        Minimum value to include in processing, by default None
    log_level : LogLevel, default="INFO"
        Logging level for the processing operation

    Returns
    -------
    dict[str, float]
        Dictionary containing the following keys:
            - mean: Mean of processed values
            - std: Standard deviation of processed values
            - count: Number of values after filtering

    Raises
    ------
    ValueError
        If input_data is empty or contains non-numeric values
    TypeError
        If threshold is provided but not a float

    Notes
    -----
    The function uses numpy for statistical calculations to ensure
    numerical stability.

    Examples
    --------
    >>> data = [1.0, 2.0, 3.0, 4.0]
    >>> process_data(data, threshold=2.5)
    {'mean': 3.5, 'std': 0.707, 'count': 2}

    See Also
    --------
    numpy.mean : Used for calculating mean values
    numpy.std : Used for calculating standard deviation
    """
```

### Key Docstring Sections

1. **Short Summary**
   - First line should be a brief summary of the function/class
   - Followed by a more detailed description if needed

2. **Parameters**
   - List all parameters with their types and descriptions
   - Include default values when applicable
   - Type hints should match the function signature

3. **Returns**
   - Clearly describe the return value and its type
   - For complex return types, detail the structure

4. **Raises**
   - Document all exceptions that might be raised
   - Explain the conditions that trigger each exception

5. **Notes**
   - Include implementation details, mathematical explanations, or other relevant information
   - Document any important side effects

6. **Examples**
   - Provide clear, runnable examples
   - Include expected output in doctest format
   - Cover common use cases and edge cases

7. **See Also**
   - Reference related functions, classes, or modules
   - Especially useful for complex APIs

### Additional Documentation Best Practices

1. **Module-Level Documentation**
```python
"""
Module for data processing and analysis.

This module provides utilities for processing numerical data
with support for various statistical operations and filtering.

Classes
-------
DataProcessor
    Main class for processing numerical data

Functions
---------
process_data
    Process numerical data with threshold filtering
validate_input
    Validate input data format and values

Notes
-----
This module requires numpy for numerical operations.
"""
```

2. **Class Documentation**
```python
class DataProcessor:
    """A class for processing numerical data.

    Provides methods for loading, processing, and analyzing
    numerical data with support for various statistical operations.

    Attributes
    ----------
    data : list[float]
        The input data to be processed
    threshold : float | None
        Current threshold value for filtering

    Methods
    -------
    load_data(filename: Path)
        Load data from a file
    process()
        Process the loaded data
    """
```

3. **Type Variable Documentation**
```python
from typing import TypeVar, Generic

T = TypeVar('T', int, float)
"""Type variable for numeric types.

Used to define generic functions that work with both
integers and floating point numbers.
"""

class NumericProcessor(Generic[T]):
    """Process numeric data of type T.

    Type Parameters
    --------------
    T : TypeVar
        Must be either int or float
    """
```

4. **Constants and Type Alias Documentation**
```python
MAX_RETRIES: Final[int] = 3
"""Maximum number of retries for network operations."""

ProcessingResult = TypeAliasType("ProcessingResult", dict[str, float | int | str])
"""Type alias for processing result dictionary.

Contains statistical results with various value types:
- float for calculations
- int for counts
- str for status messages
"""
```

These documentation standards help create self-documenting code that is easy to understand and maintain. The NumPy docstring format is particularly well-suited for scientific and data processing code, but its clarity and structure make it valuable for any Python project.

Remember that good documentation should:
- Be clear and concise
- Include all necessary information
- Be kept up-to-date with code changes
- Include examples for non-obvious usage
- Document exceptions and edge cases
- Maintain consistency across the project
