from __future__ import annotations

import importlib.util
import logging
import sys
from abc import ABC, abstractmethod
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from packaging import version
from typing_extensions import Self, TypeAliasType, TypeVar, override

if TYPE_CHECKING:
    from .config import Config

log = logging.getLogger(__name__)

# Use string literal type annotation to avoid circular import
TConfig = TypeVar("TConfig", bound="Config", infer_variance=True)

IncEx = TypeAliasType(
    "IncEx",
    # set[int] | set[str] | dict[int, IncEx] | dict[str, IncEx] | None,
    Union[set[int], set[str], dict[int, "IncEx"], dict[str, "IncEx"], None],
)


@contextmanager
def temporary_sys_path(path: str | Path):
    """Temporarily add a path to sys.path.

    Args:
        path: Path to add to sys.path

    Example:
        >>> with temporary_sys_path("/path/to/dir"):
        ...     # sys.path is modified here
        ...     pass
        ... # sys.path is restored here
    """
    path_str = str(Path(path))
    original_path = sys.path.copy()
    try:
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
        yield
    finally:
        sys.path[:] = original_path


def import_python_file(path: str | Path) -> Any:
    """Import a Python file as a module.

    Args:
        path: Path to the Python file

    Returns:
        The imported module

    Raises:
        FileNotFoundError: If the file does not exist
        ImportError: If the file cannot be imported
    """
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Python file not found: {path}")

    # Generate a unique module name to avoid conflicts
    module_name = f"_nshconfig_dynamic_module_{hash(str(path))}"

    # Import the module
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load Python file: {path}")

    module = importlib.util.module_from_spec(spec)

    # Use context manager to handle sys.path modification
    with temporary_sys_path(path.parent):
        spec.loader.exec_module(module)

    return module


def import_python_module(module_name: str) -> Any:
    """Import a Python module.

    Args:
        module_name: Name of the Python module

    Returns:
        The imported module

    Raises:
        ImportError: If the module cannot be imported
    """

    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError(f"Could not import module {module_name}: {e}")

    return module


def extract_config_from_module(module: Any) -> Any:
    """Extract a config from a module.

    Args:
        module: The module to extract the config from

    Returns:
        The extractd config

    Raises:
        ValueError: If the module does not have a `__config__` or `__create_config__` export
    """
    # First check for callable config
    if hasattr(module, "__create_config__"):
        config = module.__create_config__()
        return config

    # Then check for static config
    if not hasattr(module, "__config__"):
        raise ValueError(f"Module does not export `__config__` or `__create_config__`")

    config = module.__config__
    return config


def parse_config_from_module(module: Any, config_cls: type[TConfig]) -> TConfig:
    """Parse a config from a module.

    Args:
        module: The module to parse the config from
        config_cls: The config class to parse into

    Returns:
        The parsed config

    Raises:
        ValueError: If the module does not export a valid config
    """
    config = extract_config_from_module(module)
    if not isinstance(config, config_cls):
        raise ValueError(
            f"Module exports a config of type {type(config)}, "
            f"but expected an instance of {config_cls.__name__}"
        )

    return config


def import_and_parse_python_file(
    path: str | Path, config_cls: type[TConfig]
) -> TConfig:
    """Import and parse a config from a Python file.

    Args:
        path: Path to the Python file
        config_cls: The config class to parse into

    Returns:
        The parsed config

    Raises:
        FileNotFoundError: If the file does not exist
        ImportError: If the file cannot be imported
        ValueError: If the file does not export a valid config
    """
    module = import_python_file(path)
    return parse_config_from_module(module, config_cls)


def import_and_parse_python_module(
    module_name: str, config_cls: type[TConfig]
) -> TConfig:
    """Import and parse a config from a Python module.

    Args:
        module_name: Name of the Python module
        config_cls: The config class to parse into

    Returns:
        The parsed config

    Raises:
        ImportError: If the module cannot be imported
        ValueError: If the module does not export a valid config
    """
    module = import_python_module(module_name)
    return parse_config_from_module(module, config_cls)


T = TypeVar("T", infer_variance=True)


def deduplicate(values: Iterable[T]) -> list[T]:
    """Deduplicate a list.

    Args:
        values: List to deduplicate

    Returns:
        A new list with duplicates removed
    """
    seen: list[T] = []
    unique_configs: list[T] = []
    num_configs = 0
    for config in values:
        num_configs += 1
        if any(config == seen_config for seen_config in seen):
            continue

        seen.append(config)
        unique_configs.append(config)

    if len(unique_configs) != num_configs:
        log.critical(f"Removed {num_configs - len(unique_configs)} duplicates")

    return unique_configs


def deduplicate_configs(configs: Iterable[T]) -> list[T]:
    """Deduplicate a list of configs.

    Args:
        configs: List of configs to deduplicate

    Returns:
        A new list with duplicates removed
    """
    return deduplicate(configs)


def resolve_version(
    version_input: str | version.Version | _PackageVersion,
    /,
) -> version.Version:
    """Resolve a version string or Version object to a Version object."""
    match version_input:
        case str():
            return version.parse(version_input)
        case version.Version():
            return version_input
        case _PackageVersion():
            return version_input()
        case _:
            raise TypeError(
                f"Expected str or Version, got {type(version_input).__name__}"
            )


class _PackageVersion(ABC):
    @abstractmethod
    def __call__(self) -> version.Version:
        """Return the version of the package."""

    def gt(self, other: str | version.Version | Self) -> bool:
        """Check if the current version is greater than the other version."""
        return self() > resolve_version(other)

    def ge(self, other: str | version.Version | Self) -> bool:
        """Check if the current version is greater than or equal to the other version."""
        return self() >= resolve_version(other)

    def lt(self, other: str | version.Version | Self) -> bool:
        """Check if the current version is less than the other version."""
        return self() < resolve_version(other)

    def le(self, other: str | version.Version | Self) -> bool:
        """Check if the current version is less than or equal to the other version."""
        return self() <= resolve_version(other)

    def eq(self, other: str | version.Version | Self) -> bool:
        """Check if the current version is equal to the other version."""
        return self() == resolve_version(other)

    def ne(self, other: str | version.Version | Self) -> bool:
        """Check if the current version is not equal to the other version."""
        return self() != resolve_version(other)

    # Operator overloads
    def __gt__(self, other: str | version.Version | Self) -> bool:
        """Check if the current version is greater than the other version."""
        return self.gt(other)

    def __ge__(self, other: str | version.Version | Self) -> bool:
        """Check if the current version is greater than or equal to the other version."""
        return self.ge(other)

    def __lt__(self, other: str | version.Version | Self) -> bool:
        """Check if the current version is less than the other version."""
        return self.lt(other)

    def __le__(self, other: str | version.Version | Self) -> bool:
        """Check if the current version is less than or equal to the other version."""
        return self.le(other)

    @override
    def __eq__(self, other: object) -> bool:
        """Check if the current version is equal to the other version."""
        if not isinstance(other, (type(self), str, version.Version)):
            return NotImplemented
        return self.eq(other)

    @override
    def __ne__(self, other: object) -> bool:
        """Check if the current version is not equal to the other version."""
        if not isinstance(other, (type(self), str, version.Version)):
            return NotImplemented
        return self.ne(other)

    @override
    def __str__(self) -> str:
        """Return the version as a string."""
        return str(self())


class _CommonPackageVersion(_PackageVersion):
    """A common base class for package versions that provides a default implementation."""

    _module_name: str

    def __init__(self, module_name: str):
        """Initialize with the module name."""
        self._module_name = module_name

    @override
    def __call__(self) -> version.Version:
        """Return the version of the package."""
        if importlib.util.find_spec(self._module_name) is None:
            raise ImportError(f"Module {self._module_name} not found")

        module = importlib.import_module(self._module_name)
        if (version := getattr(module, "__version__", None)) is None:
            raise ImportError(
                f"Module {self._module_name} does not have a __version__ attribute"
            )
        return resolve_version(version)


PYDANTIC_VERSION = _CommonPackageVersion("pydantic")
