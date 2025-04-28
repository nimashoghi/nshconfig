# Python Style Guide

## 1. Introduction & Philosophy

This style guide aims to foster Python code that is:

* **Readable & Explicit:** Code should be easy to understand at a glance. Clarity trumps brevity where ambiguity might arise.
* **Maintainable:** Consistent style makes it easier for developers (including your future self) to modify and extend the codebase.
* **Robust & Type-Safe:** Extensive use of type hints helps catch errors early and improves code comprehension.
* **Simple:** Prefer straightforward solutions. Favor simple functions and data structures over complex class hierarchies and deep inheritance. Composition is strongly preferred over inheritance.

While inspired by specific projects, this guide provides general best practices for writing clean Python.

## 2. Code Formatting & Linting

Consistency is key. We use specific tools to enforce formatting and identify potential issues.

* **Formatter:** `ruff format` is the mandatory code formatter. All code must be formatted using `ruff` before committing. Configure `ruff` via `pyproject.toml` or `ruff.toml` for project-wide consistency.
* **Linter / Type Checker:** `basedpyright` (or `pyright`) is the mandatory linter and static type checker. Configure it via `pyproject.toml` or `pyrightconfig.json`. Aim for strict type checking settings.
    * Run `ruff check` (with appropriate plugins/settings) for additional linting rules beyond basic formatting and type errors.

## 3. Naming Conventions

Follow standard PEP 8 naming conventions:

* **Modules:** `lower_snake_case`.
* **Packages:** `lower_snake_case`.
* **Classes:** `PascalCase` (or `CapWords`).
* **Functions:** `lower_snake_case`.
* **Variables:** `lower_snake_case`.
* **Constants:** `UPPER_SNAKE_CASE`.
* **Methods:** `lower_snake_case`.
* **Function/Method Arguments:** `lower_snake_case`.
* **Type Variables:** `PascalCase` (e.g., `T`, `KT`, `VT`, `TConfig`).

**Clarity:** Choose descriptive names. Avoid single-letter variable names unless they are conventional loop counters (`i`, `j`, `k`) or type variables.

## 4. Typing

Static typing is crucial for code robustness and readability.

* **Mandatory Type Hints:** All function signatures (parameters and return types, with exceptions noted below), variables, and class attributes **must** have type hints. Use `from __future__ import annotations` (standard in Python 3.11+) to enable modern forward reference handling.
* **Modern Syntax:** Always prefer modern type hint syntax:
    * Use `|` for unions (e.g., `int | str` instead of `typing.Union[int, str]`).
    * Use built-in generics (e.g., `list[int]`, `dict[str, float]`, `tuple[int, ...]` instead of `typing.List[int]`, `typing.Dict[str, float]`, `typing.Tuple[int, ...]`).
    * Use `collections.abc` for abstract base classes when applicable (e.g., `collections.abc.Iterable[str]` instead of `typing.Iterable[str]`, `collections.abc.Mapping[K, V]` instead of `typing.Mapping[K, V]`).
    * Use `X | None` instead of `typing.Optional[X]`.
* **Function Signatures:**
    * **Parameters:** All function and method parameters **must** be type-annotated.
    * **Return Types:**
        * Return types **should generally be omitted** if the function **implicitly returns `None`** (i.e., has no `return` statement or just `return`).
        * Return types **should generally be omitted** for **simple, obvious types** like `-> bool` or `-> int` if the function's purpose and name make it unambiguous.
        * Return types **must be included** for **non-trivial types** (e.g., complex custom classes, complex generics, unions, types involving `Any`) or when the return type isn't immediately obvious from the function name and context.
        * Example:
            ```python
            # Good: Omitting simple/None return types
            def process_data(data: list[int]): # Implicitly returns None
                ...

            def is_valid(value: str) -> bool: # Clear from name, but adding doesn't hurt
                return value.startswith("valid")

            # Good: Including complex/non-obvious return types
            from .models import ComplexResult

            def parse_complex_data(raw: bytes) -> ComplexResult | None:
                ...

            def get_items(key: str) -> list[dict[str, Any]]:
                ...
            ```
* **`typing.Any`:** Avoid `Any` whenever possible. If used, consider adding a comment explaining why a more specific type isn't feasible.
* **Array Typing (NumPy/PyTorch/JAX/TensorFlow/MLX):**
    * Use `nshutils.typecheck` (based on `jaxtyping`) for annotating array-like objects (NumPy `ndarray`, `torch.Tensor`, `jax.Array`, `tf.Tensor`, `mx.array`). This enables runtime shape and dtype checking.
    * **Syntax:** `tc.Dtype[ArrayType, "shape_spec"]`
        * `tc`: Alias for `nshutils.typecheck`.
        * `Dtype`: Specifies the data type (e.g., `tc.Float`, `tc.Int`, `tc.Bool`, `tc.Complex`, `tc.Num`, `tc.Shaped`). Prefer general types like `Float` or `Int` over specific precision (e.g., `Float32`) unless precision is critical.
        * `ArrayType`: The specific array class (e.g., `np.ndarray`, `torch.Tensor`, `jax.Array`).
        * `shape_spec`: A string describing the shape using space-separated symbols (e.g., `"batch height width channels"`, `"num_items latent_dim"`, `"28 28"`).
            * Use names for variable dimensions (e.g., `"batch"`).
            * Use integers for fixed dimensions (e.g., `"3"`).
            * Use symbolic expressions (e.g., `"dim-1"`, `"batch*2"`).
            * Use modifiers: `*` (multiple axes), `#` (broadcastable), `_` (unchecked), `name=` (documentation), `?` (variable in PyTree).
            * Use `""` for scalars.
            * Use `"..."` for arbitrary shapes (dtype check only).
    * **Example:**
        ```python
        import nshutils.typecheck as tc
        import numpy as np
        import torch

        def process_image(img: tc.Float[np.ndarray, "height width 3"]) -> tc.Int[np.ndarray, "height width"]:
            # Process a float image (H, W, 3) -> Int mask (H, W)
            ...

        def attention(
            query: tc.Float[torch.Tensor, "*batch seq_len dim"],
            key: tc.Float[torch.Tensor, "*batch kv_seq_len dim"],
            value: tc.Float[torch.Tensor, "*batch kv_seq_len v_dim"]
        ) -> tc.Float[torch.Tensor, "*batch seq_len v_dim"]:
            # Calculate attention output
            ...

        def add_broadcast(
            x: tc.Num[np.ndarray, "#rows #cols"],
            y: tc.Num[np.ndarray, "#rows #cols"]
        ) -> tc.Num[np.ndarray, "#rows #cols"]:
            # Add with broadcasting support
            return x + y
        ```

## 5. Code Structure

* **Project Layout:** Prefer a `src`-layout:
    ```
    project_root/
    ├── pyproject.toml
    ├── README.md
    ├── src/
    │   └── my_package/
    │       ├── __init__.py
    │       ├── module_a.py
    │       └── subpackage/
    │           ├── __init__.py
    │           └── module_b.py
    └── tests/
        ├── test_module_a.py
        └── subpackage/
            └── test_module_b.py
    ```
* **Modules & Packages:** Keep modules focused on a single responsibility. Use packages (`subpackage/`) to group related modules.
* **`__init__.py`:** Use `__init__.py` to define the public API of a package. Explicitly import the symbols you want to expose. Avoid putting implementation logic directly in `__init__.py`.
* **Imports:**
    * Follow PEP 8 import order (standard library, third-party, local application).
    * Use absolute imports (`from my_package.subpackage import module_b`) within your project's `src` directory whenever possible. Relative imports (`from . import module_b`) are acceptable within the same package.
    * Avoid wildcard imports (`from module import *`), except potentially in `__init__.py` files if carefully managed, or for specific frameworks designed around them (like the `pydantic` import in the example `__init__.py`). Be mindful of namespace pollution.

## 6. Functions vs. Classes (OOP Approach)

* **Functions First:** Prefer simple, standalone functions for operations and logic. Functions are easier to test and reason about.
* **Classes for State/Structure:** Use classes when you need to:
    * Bundle data and the methods that operate *specifically* on that data (and the state is complex enough to warrant it).
    * Define structured data containers (see Section 7).
    * Manage resources that require setup/teardown (often better handled by context managers or generators).
* **Avoid Deep Hierarchies:** Do not build complex class inheritance trees. They often lead to fragile and hard-to-understand code (fragile base class problem).
* **Composition Over Inheritance:** If you need to reuse functionality or combine behaviors, strongly prefer composition (passing instances of other classes or functions) over inheriting from multiple classes or deep chains.

## 7. Data Structures

* **Structured Data:** When dealing with structured data (especially configurations, API responses, or data passed between components), use:
    * **`dataclasses`:** Standard library solution for simple data containers. Provides boilerplate `__init__`, `__repr__`, `__eq__`, etc.
    * **`Pydantic`:** Excellent for data validation, serialization/deserialization (JSON, YAML), and creating more complex data models with type enforcement at runtime. Highly recommended for configuration management and API interactions.
* **Immutability:** Prefer immutable data structures where possible (e.g., `tuple` over `list` for fixed sequences, `frozenset` over `set`). Pydantic models can also be configured for immutability (`frozen=True`).

## 8. Control Flow & Operators

Control flow should be clear and easy to follow.

### 8.1 Walrus Operator (`:=`)

Use the walrus operator when it genuinely simplifies code and reduces redundancy, typically within `if`, `while`, or list/dict comprehensions. The primary goal is improved readability.

* **Good:** `if (match := pattern.search(line)): print(match.group(1))`
* **Good:** `while chunk := file.read(8192): process(chunk)`
* **Avoid:** Using it in ways that harm readability just to save a line.

### 8.2 Structural Pattern Matching (`match`)

Introduced in Python 3.10, the `match` statement provides a powerful way to implement complex conditional logic based on the *structure*, *type*, and *value* of an object.

* **When to Use:** Prefer `match` over complex, nested `if/elif/else` chains when your logic branches based on:
    * The type of an object (`case str():`, `case dict():`).
    * The presence and values of attributes in an object (`case Point(x=0, y=y):`).
    * Specific literal values (`case 200:`, `case "ERROR":`).
    * The structure and elements of sequences (`case [x, y]:`, `case [op, *args]:`).
    * The structure and key/value pairs of mappings (`case {"status": 200, "data": data}:`).
    * Combinations of the above.
* **Readability:** The primary benefit of `match` is often improved readability for these scenarios. If a `match` statement becomes overly complex or difficult to follow, consider refactoring the logic (e.g., breaking it into smaller functions) or reverting to `if/elif` if that is clearer for the specific case.
* **Wildcard:** Use the wildcard pattern (`case _:`) for handling cases that don't match any other specific pattern.

## 9. Strings

* **f-Strings:** Always use f-strings for string formatting. They are more readable and generally faster than older methods (`%` formatting or `.format()`).
    * **Example:** `message = f"Processing item {item_id} for user {user_name}."`

## 10. Comments and Docstrings

* **Docstrings:**
    * Write comprehensive docstrings for all public modules, classes, functions, and methods.
    * Use a standard format like Google style or reStructuredText (Sphinx style). Google style is often more readable in plain text.
    * Docstrings should explain *what* the code does, its parameters (`Args:`), what it returns (`Returns:`), and any exceptions it might raise (`Raises:`).
    * **Example (Google Style):**
        ```python
        def process_data(data: list[int], threshold: float = 0.5) -> list[float]:
            """Processes raw integer data into normalized floats.

            Filters items below the threshold and normalizes the rest.

            Args:
                data: A list of integers to process.
                threshold: The minimum value for an item to be included (inclusive).

            Returns:
                A list of normalized float values greater than or equal to the threshold.

            Raises:
                ValueError: If the input data list is empty.
            """
            if not data:
                raise ValueError("Input data cannot be empty")
            # ... implementation ...
        ```
* **Inline Comments:**
    * Use inline comments (`#`) sparingly.
    * **Use for:** Explaining *why* something is done in a non-obvious way, clarifying complex logic, or marking `TODO`s or `FIXME`s.
    * **Avoid:** Comments that simply restate what the code clearly says (e.g., `# Increment i` above `i += 1`). Comments should add information the code itself cannot convey easily.

## 11. Error Handling & Logging

* **Exceptions:**
    * Use built-in exceptions when appropriate (`ValueError`, `TypeError`, `KeyError`, etc.).
    * Define custom exception classes inheriting from `Exception` for application-specific error conditions. This allows for more granular error handling.
    * Avoid catching bare `Exception` unless you are at the top level of an application or thread and intend to log/report the error before exiting or continuing. Be specific about the exceptions you catch.
* **Logging:**
    * Use the standard `logging` module for diagnostic messages, debugging, and event tracking.
    * **Do not use `print()`** for logging or debugging purposes in library or application code. `print()` goes to `stdout`, is hard to configure (level, destination), and lacks context.
    * Configure loggers appropriately (level, formatters, handlers) at the application entry point. Libraries should generally not configure logging handlers themselves but should get loggers (`logging.getLogger(__name__)`) and use them.

## 12. Resource Management

* **Context Managers (`with`):** Always use the `with` statement for managing resources that need cleanup (files, network connections, locks, database sessions, etc.). This ensures resources are properly released even if errors occur.
    * **Example:**
        ```python
        # Good
        with open("my_file.txt", "r") as f:
            content = f.read()

        # Bad
        f = open("my_file.txt", "r")
        content = f.read()
        f.close() # <-- May not run if errors occur before this line
        ```

## 13. Testing

* **Framework:** Use `pytest` as the standard testing framework.
* **Coverage:** Aim for high test coverage. Tests should cover normal operation, edge cases, and error conditions.
* **Types of Tests:** Write a mix of unit tests (testing individual functions/methods in isolation) and integration tests (testing interactions between components).
* **Fixtures:** Use `pytest` fixtures effectively to manage test setup, teardown, and share resources/data.
* **Assertions:** Use descriptive assertion messages where helpful (`assert result == expected, "Unexpected processing outcome"`).

## 14. Handling Optional / Missing Data

* **Clarity:** Be explicit about how optional or potentially missing data is handled.
* **`None`:** The most common way to represent optional data. Use `| None` in type hints.
* **Default Values:** Use default arguments in functions or default values in `dataclasses`/`Pydantic` models for values that have sensible defaults.
* **Sentinels:** In specific cases, a custom sentinel object (like the `MISSING` object in the provided code) can be useful to distinguish between "value is `None`" and "value was not provided". Use this judiciously, as `None` is often sufficient and better understood. Ensure custom sentinels are properly handled during validation and serialization if using Pydantic.
