from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from .config import Config

# Use string literal type annotation to avoid circular import
T = TypeVar("T", bound="Config")


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


def parse_config_from_module(module: Any, config_cls: type[T]) -> T:
    """Parse a config from a module.

    Args:
        module: The module to parse the config from
        config_cls: The config class to parse into

    Returns:
        The parsed config

    Raises:
        ValueError: If the module does not export a valid config
    """
    # First check for callable config
    if hasattr(module, "__create_config__"):
        config = module.__create_config__()
        if isinstance(config, dict):
            return config_cls.from_dict(config)
        elif isinstance(config, config_cls):
            return config
        else:
            raise ValueError(
                f"Module's __create_config__() returned type {type(config)}, "
                f"but expected a dictionary or an instance of {config_cls.__name__}"
            )

    # Then check for static config
    if not hasattr(module, "__config__"):
        raise ValueError(f"Module does not export `__config__` or `__create_config__`")

    config = module.__config__
    if isinstance(config, dict):
        return config_cls.from_dict(config)
    elif isinstance(config, config_cls):
        return config
    else:
        raise ValueError(
            f"Module exports a `__config__` variable of type {type(config)}, "
            f"but expected a dictionary or an instance of {config_cls.__name__}"
        )


def parse_configs_from_module(module: Any, config_cls: type[T]) -> list[T]:
    """Parse multiple configs from a module.

    Args:
        module: The module to parse the configs from
        config_cls: The config class to parse into

    Returns:
        List of parsed configs

    Raises:
        ValueError: If the module does not export valid configs
    """
    # First check for callable configs
    if hasattr(module, "__create_configs__"):
        configs = module.__create_configs__()
        result = []
        for config in configs:
            if isinstance(config, dict):
                result.append(config_cls.from_dict(config))
            elif isinstance(config, config_cls):
                result.append(config)
            else:
                raise ValueError(
                    f"Module's __create_configs__() returned item of type {type(config)}, "
                    f"but expected a dictionary or an instance of {config_cls.__name__}"
                )
        return result

    # Then check for static configs
    if not hasattr(module, "__configs__"):
        raise ValueError(
            f"Module does not export `__configs__` or `__create_configs__`"
        )

    configs = module.__configs__
    if not isinstance(configs, (list, tuple)):
        raise ValueError(
            f"Module's __configs__ must be a list or tuple, got {type(configs)}"
        )

    result = []
    for config in configs:
        if isinstance(config, dict):
            result.append(config_cls.from_dict(config))
        elif isinstance(config, config_cls):
            result.append(config)
        else:
            raise ValueError(
                f"Module's __configs__ contains item of type {type(config)}, "
                f"but expected a dictionary or an instance of {config_cls.__name__}"
            )
    return result


def import_and_parse_python_file(path: str | Path, config_cls: type[T]) -> T:
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

    return parse_config_from_module(module, config_cls)


def import_and_parse_python_module(module_name: str, config_cls: type[T]) -> T:
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
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError(f"Could not import module {module_name}: {e}")

    return parse_config_from_module(module, config_cls)


def import_and_parse_configs_from_python_file(
    path: str | Path, config_cls: type[T]
) -> list[T]:
    """Import and parse multiple configs from a Python file.

    Args:
        path: Path to the Python file
        config_cls: The config class to parse into

    Returns:
        List of parsed configs

    Raises:
        FileNotFoundError: If the file does not exist
        ImportError: If the file cannot be imported
        ValueError: If the file does not export valid configs
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

    return parse_configs_from_module(module, config_cls)


def import_and_parse_configs_from_python_module(
    module_name: str, config_cls: type[T]
) -> list[T]:
    """Import and parse multiple configs from a Python module.

    Args:
        module_name: Name of the Python module
        config_cls: The config class to parse into

    Returns:
        List of parsed configs

    Raises:
        ImportError: If the module cannot be imported
        ValueError: If the module does not export valid configs
    """
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError(f"Could not import module {module_name}: {e}")

    return parse_configs_from_module(module, config_cls)
