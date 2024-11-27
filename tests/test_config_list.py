from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from nshconfig import Config

if TYPE_CHECKING:
    from typing import Protocol

    class TestConfig(Config):
        value: int
        name: str


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
""")

    # Add the parent directory to sys.path so we can import testpkg
    import sys

    sys.path.insert(0, str(tmp_path))
    yield pkg_dir
    # Clean up sys.path
    sys.path.pop(0)


def test_static_config_list(tmp_path):
    # Create a temporary module file with a static config list
    module_path = tmp_path / "test_config.py"
    module_path.write_text("""
__configs__ = [
    {
        "value": 100,
        "name": "config1"
    },
    {
        "value": 200,
        "name": "config2"
    }
]
""")

    # Test loading configs from the module
    configs = SimpleConfig.from_python_file_list(module_path)
    assert len(configs) == 2
    assert configs[0].value == 100
    assert configs[0].name == "config1"
    assert configs[1].value == 200
    assert configs[1].name == "config2"


def test_dynamic_config_list(tmp_path):
    # Create a temporary module file with a dynamic config list
    module_path = tmp_path / "test_config.py"
    module_path.write_text("""
def __create_configs__():
    for i in range(3):
        yield {
            "value": i * 100,
            "name": f"config{i}"
        }
""")

    # Test loading configs from the module
    configs = SimpleConfig.from_python_file_list(module_path)
    assert len(configs) == 3
    for i, config in enumerate(configs):
        assert config.value == i * 100
        assert config.name == f"config{i}"


def test_dynamic_config_list_with_imported_type(tmp_path, tmp_package):
    # Create a temporary module file with a dynamic config list using imported type
    module_path = tmp_path / "test_config.py"
    module_path.write_text("""
from testpkg.configs import TestConfig

def __create_configs__():
    for i in range(3):
        config = TestConfig()
        config.value = i * 100
        config.name = f"config{i}"
        yield config
""")

    # Test loading configs from the module
    from testpkg.configs import TestConfig  # type: ignore

    configs = TestConfig.from_python_file_list(module_path)
    assert len(configs) == 3
    for i, config in enumerate(configs):
        assert isinstance(config, TestConfig)
        assert config.value == i * 100
        assert config.name == f"config{i}"


def test_static_config_list_with_imported_type(tmp_path, tmp_package):
    # Create a temporary module file with a static config list using imported type
    module_path = tmp_path / "test_config.py"
    module_path.write_text("""
from testpkg.configs import TestConfig

__configs__ = [
    TestConfig(value=100, name="config1"),
    TestConfig(value=200, name="config2")
]
""")

    # Test loading configs from the module
    from testpkg.configs import TestConfig  # type: ignore

    configs = TestConfig.from_python_file_list(module_path)
    assert len(configs) == 2
    for config in configs:
        assert isinstance(config, TestConfig)
    assert configs[0].value == 100
    assert configs[0].name == "config1"
    assert configs[1].value == 200
    assert configs[1].name == "config2"


def test_invalid_config_list(tmp_path):
    # Create a temporary module file with an invalid config list
    module_path = tmp_path / "test_config.py"
    module_path.write_text("""
__configs__ = [
    {
        "value": 100,
        "name": "config1"
    },
    42  # Invalid config
]
""")

    # Test that loading fails with appropriate error
    with pytest.raises(
        ValueError, match="contains item of type <class 'int'>, but expected"
    ):
        SimpleConfig.from_python_file_list(module_path)


def test_invalid_dynamic_config_list(tmp_path):
    # Create a temporary module file with an invalid dynamic config list
    module_path = tmp_path / "test_config.py"
    module_path.write_text("""
def __create_configs__():
    yield {
        "value": 100,
        "name": "config1"
    }
    yield 42  # Invalid config
""")

    # Test that loading fails with appropriate error
    with pytest.raises(
        ValueError, match="returned item of type <class 'int'>, but expected"
    ):
        SimpleConfig.from_python_file_list(module_path)


def test_non_iterable_config_list(tmp_path):
    # Create a temporary module file with a non-iterable config list
    module_path = tmp_path / "test_config.py"
    module_path.write_text("""
__configs__ = 42  # Not a list or tuple
""")

    # Test that loading fails with appropriate error
    with pytest.raises(ValueError, match="must be a list or tuple"):
        SimpleConfig.from_python_file_list(module_path)
