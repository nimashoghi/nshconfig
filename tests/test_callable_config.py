from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from nshconfig import Config

if TYPE_CHECKING:
    # Type hints for dynamically created modules
    class TestConfig(Config):
        value: int
        name: str

    class NestedConfig(Config):
        inner_value: int

    class OuterConfig(Config):
        value: int
        name: str
        nested: NestedConfig


class SimpleConfig(Config):
    value: int = 42
    name: str = "test"


@pytest.fixture
def tmp_package(tmp_path):
    """Create a temporary package with a config module."""
    pkg_dir = tmp_path / "testpkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")

    # Create a module with config classes
    config_module = pkg_dir / "configs.py"
    config_module.write_text("""
from nshconfig import Config

class TestConfig(Config):
    value: int = 42
    name: str = "test"

class NestedConfig(Config):
    inner_value: int = 0

class OuterConfig(Config):
    value: int = 42
    name: str = "test"
    nested: NestedConfig = NestedConfig()
""")

    # Add the parent directory to sys.path so we can import testpkg
    sys.path.insert(0, str(tmp_path))
    yield pkg_dir
    # Clean up sys.path
    sys.path.pop(0)


def test_callable_config_with_imported_type(tmp_path, tmp_package):
    # Create a temporary module file that imports and uses the config type
    module_path = tmp_path / "test_config.py"
    module_path.write_text("""
from testpkg.configs import TestConfig

def __create_config__():
    config = TestConfig()
    config.value = 100
    config.name = "from_callable"
    return config  # Return actual instance
""")

    # Test loading config from the module
    from testpkg.configs import TestConfig  # type: ignore

    config = TestConfig.from_python_file(module_path)
    assert isinstance(config, TestConfig)  # Check actual type
    assert config.value == 100
    assert config.name == "from_callable"


def test_static_config_with_imported_type(tmp_path, tmp_package):
    # Create a temporary module file that imports and uses the config type
    module_path = tmp_path / "test_config.py"
    module_path.write_text("""
from testpkg.configs import TestConfig

__config__ = TestConfig(value=300, name="from_static")
""")

    # Test loading config from the module
    from testpkg.configs import TestConfig  # type: ignore

    config = TestConfig.from_python_file(module_path)
    assert isinstance(config, TestConfig)  # Check actual type
    assert config.value == 300
    assert config.name == "from_static"


def test_nested_config_with_imported_types(tmp_path, tmp_package):
    # Create a temporary module file that imports and uses nested config types
    module_path = tmp_path / "test_config.py"
    module_path.write_text("""
from testpkg.configs import OuterConfig, NestedConfig

def __create_config__():
    nested = NestedConfig(inner_value=400)
    config = OuterConfig(value=300, nested=nested)
    return config  # Return actual instance
""")

    # Test loading config from the module
    from testpkg.configs import NestedConfig, OuterConfig  # type: ignore

    config = OuterConfig.from_python_file(module_path)
    assert isinstance(config, OuterConfig)  # Check actual type
    assert isinstance(config.nested, NestedConfig)  # Check nested type
    assert config.value == 300
    assert config.nested.inner_value == 400


def test_invalid_callable_config(tmp_path):
    # Create a temporary module file with an invalid callable config
    module_path = tmp_path / "test_config.py"
    module_path.write_text("""
def __create_config__():
    return 42  # Not a valid config or dict
""")

    # Test that loading fails with appropriate error
    with pytest.raises(ValueError, match="Module exports a config of type"):
        SimpleConfig.from_python_file(module_path)


def test_callable_with_args_in_module(tmp_path):
    # Create a temporary module file with a callable that requires arguments
    module_path = tmp_path / "test_config.py"
    module_path.write_text("""
from testpkg.configs import TestConfig

def __create_config__(x):  # Invalid: requires an argument
    return TestConfig(value=x, name="from_callable")
""")

    # Test that loading fails with appropriate error
    with pytest.raises(TypeError):
        SimpleConfig.from_python_file(module_path)


def test_static_config_in_module(tmp_path):
    # Create a temporary module file with a static config
    module_path = tmp_path / "test_config.py"
    module_path.write_text("""
from testpkg.configs import TestConfig

__config__ = TestConfig(value=300, name="from_static")
""")
    from testpkg.configs import TestConfig  # type: ignore

    # Test loading config from the module
    config = TestConfig.from_python_file(module_path)
    assert config.value == 300
    assert config.name == "from_static"
