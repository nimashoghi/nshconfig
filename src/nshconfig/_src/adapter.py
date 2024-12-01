from __future__ import annotations

from pathlib import Path
from typing import Any, Generic, Literal, overload

from pydantic import ConfigDict, TypeAdapter
from pydantic.main import IncEx
from typing_extensions import TypedDict, TypeVar, Unpack

T = TypeVar("T", infer_variance=True)


class DumpJsonParams(TypedDict, total=False):
    include: IncEx | None
    """Fields to include in the output."""

    exclude: IncEx | None
    """Fields to exclude from the output."""

    by_alias: bool
    """Whether to use alias names for field names."""

    exclude_unset: bool
    """Whether to exclude unset fields."""

    exclude_defaults: bool
    """Whether to exclude fields with default values."""

    exclude_none: bool
    """Whether to exclude fields with None values."""

    round_trip: bool
    """Whether to output the serialized data in a way that is compatible with deserialization."""

    warnings: bool | Literal["none", "warn", "error"]
    """How to handle serialization errors. False/"none" ignores them, True/"warn" logs errors,
    "error" raises a PydanticSerializationError."""

    serialize_as_any: bool
    """Whether to serialize fields with duck-typing serialization behavior."""

    context: dict[str, Any] | None
    """Additional context to pass to the serializer."""


class DumpPythonParams(DumpJsonParams, total=False):
    mode: Literal["json", "python"]
    """The output format. Can be 'json' or 'python'."""


class ValidateJsonParams(TypedDict, total=False):
    strict: bool | None
    """Whether to strictly check types."""

    context: dict[str, Any] | None
    """Additional context to pass to the validator."""

    experimental_allow_partial: bool | Literal["off", "on", "trailing-strings"]
    """**Experimental** whether to enable partial validation, e.g. to process streams.
    * False / 'off': Default behavior, no partial validation.
    * True / 'on': Enable partial validation.
    * 'trailing-strings': Enable partial validation and allow trailing strings in the input."""


class ValidatePythonParams(ValidateJsonParams, total=False):
    from_attributes: bool | None
    """Whether to extract data from object attributes."""


class Adapter(Generic[T]):
    @overload
    def __init__(
        self,
        type: type[T],
        *,
        config: ConfigDict | None = ...,
        _parent_depth: int = ...,
        module: str | None = ...,
    ) -> None: ...

    # This second overload is for unsupported special forms (such as Annotated, Union, etc.)
    # Currently there is no way to type this correctly
    # See https://github.com/python/typing/pull/1618
    @overload
    def __init__(
        self,
        type: Any,
        *,
        config: ConfigDict | None = ...,
        _parent_depth: int = ...,
        module: str | None = ...,
    ) -> None: ...

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.adapter = TypeAdapter[T](*args, **kwargs)

    # Python methods
    def to_python(self, instance: T, /, **kwargs: Unpack[DumpPythonParams]) -> Any:
        """Convert data to a Python object.

        Args:
            instance: The instance to convert
            **kwargs: Additional parameters to pass to the Pydantic TypeAdapter's
                dump_python method

        Returns:
            Python representation of the data
        """
        return self.adapter.dump_python(instance, **kwargs)

    def from_python(
        self,
        data: Any,
        /,
        **kwargs: Unpack[ValidatePythonParams],
    ) -> T:
        """Create instance from a Python object.

        Args:
            data: Dictionary containing data
            **kwargs: Additional parameters to pass to the Pydantic TypeAdapter's
                validate_python method

        Returns:
            Validated instance
        """
        return self.adapter.validate_python(data, **kwargs)

    def to_json_str(
        self,
        instance: T,
        /,
        **kwargs: Unpack[DumpJsonParams],
    ) -> str:
        """Dump configuration to a JSON string.

        Args:
            with_schema: Whether to include the schema reference in the JSON file
            kwargs: Additional keyword arguments to pass to the JSON dumper,
                these are parameters directly passed to TypeAdapter.dump_json().
        """
        json_bytes = self.adapter.dump_json(instance, **kwargs)
        return json_bytes.decode("utf-8")

    def to_json_file(
        self,
        instance: T,
        /,
        path: str | Path,
        **kwargs: Unpack[DumpJsonParams],
    ) -> None:
        """Save configuration to a JSON file.

        Args:
            path: Path where the JSON file will be saved
            kwargs: Additional keyword arguments to pass to the JSON dumper,
                these are parameters directly passed to TypeAdapter.dump_json().
        """
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json_str(instance, **kwargs))

    def from_json_str(
        self,
        json: str | bytes | bytearray,
        /,
        **kwargs: Unpack[ValidateJsonParams],
    ):
        """Create configuration from a JSON string.

        Args:
            json: JSON string, bytes or bytearray
            kwargs: Additional keyword arguments to pass to the JSON loader,
                these are parameters directly passed to TypeAdapter.validate_json().

        Returns:
            A validated configuration instance
        """
        return self.adapter.validate_json(json, **kwargs)

    def from_json_file(
        self,
        path: str | Path,
        /,
        **kwargs: Unpack[ValidateJsonParams],
    ):
        """Create configuration from a JSON file.

        Args:
            path: Path to the JSON file
            kwargs: Additional keyword arguments to pass to the JSON loader,
                these are parameters directly passed to TypeAdapter.validate_json().

        Returns:
            A validated configuration instance
        """
        with open(path, "r", encoding="utf-8") as f:
            return self.from_json_str(f.read(), **kwargs)

    def from_python_file(
        self,
        path: str | Path,
        /,
        **kwargs: Unpack[ValidatePythonParams],
    ):
        """Create configuration from a Python file.

        The Python file should export a `__config__` variable that contains the configuration,
        or a `__create_config__` function that returns a configuration.

        Args:
            path: Path to the Python file
            kwargs: Additional keyword arguments to pass to the Python loader,
                these are parameters directly passed to TypeAdapter.validate_python().

        Returns:
            A validated configuration instance

        Raises:
            FileNotFoundError: If the Python file does not exist
            ImportError: If the Python file cannot be imported
            ValueError: If the Python file does not export a valid `__config__` variable
        """
        from .utils import extract_config_from_module, import_python_file

        module = import_python_file(path)
        config = extract_config_from_module(module)

        return self.adapter.validate_python(config, **kwargs)

    def from_python_module(
        self,
        module_name: str,
        /,
        **kwargs: Unpack[ValidatePythonParams],
    ):
        """Create configuration from a Python module.

        The Python module should export a `__config__` variable that contains the configuration,
        or a `__create_config__` function that returns a configuration.

        Args:
            module_name: Name of the Python module
            kwargs: Additional keyword arguments to pass to the Python loader,
                these are parameters directly passed to TypeAdapter.validate_python().

        Returns:
            A validated configuration instance

        Raises:
            moduleNotFoundError: If the Python module does not exist
            ImportError: If the Python module cannot be imported
            ValueError: If the Python module does not export a valid `__config__` variable
        """
        from .utils import extract_config_from_module, import_python_module

        module = import_python_module(module_name)
        config = extract_config_from_module(module)

        return self.adapter.validate_python(config, **kwargs)
