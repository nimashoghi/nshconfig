from __future__ import annotations

import os
import tempfile
from pathlib import Path
from textwrap import dedent

import pytest

from nshconfig import Config


class SampleConfig(Config):
    name: str
    value: int


def test_from_python_file_with_instance():
    # Create a temporary Python file with a config instance
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(
            dedent("""
            from tests.test_config import SampleConfig

            __config__ = SampleConfig(
                name="test_instance",
                value=123
            )
        """)
        )
        temp_path = f.name

    try:
        # Load and validate the config
        config = SampleConfig.from_python_file(temp_path)
        assert isinstance(config, SampleConfig)
        assert config.name == "test_instance"
        assert config.value == 123
    finally:
        # Clean up
        os.unlink(temp_path)


def test_from_python_file_with_relative_import():
    # Create a temporary directory for our test files
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a helper module
        helper_path = Path(temp_dir) / "helper.py"
        with open(helper_path, "w") as f:
            f.write(
                dedent("""
                TEST_NAME = "from_helper"
                TEST_VALUE = 999
            """)
            )

        # Create the config file that imports from helper
        config_path = Path(temp_dir) / "config.py"
        with open(config_path, "w") as f:
            f.write(
                dedent("""
                from tests.test_config import SampleConfig

                from helper import TEST_NAME, TEST_VALUE

                __config__ = SampleConfig(
                    name=TEST_NAME,
                    value=TEST_VALUE
                )
            """)
            )

        # Load and validate the config
        config = SampleConfig.from_python_file(config_path)
        assert isinstance(config, SampleConfig)
        assert config.name == "from_helper"
        assert config.value == 999


def test_from_python_file_missing_config():
    # Create a temporary Python file without __config__
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("x = 1")
        temp_path = f.name

    try:
        # Attempt to load the config
        with pytest.raises(
            ValueError, match="does not export `__config__` or `__create_config__`"
        ):
            SampleConfig.from_python_file(temp_path)
    finally:
        # Clean up
        os.unlink(temp_path)


def test_from_python_file_invalid_type():
    # Create a temporary Python file with invalid config type
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("__config__ = 42")  # Not a dict or SampleConfig instance
        temp_path = f.name

    try:
        # Attempt to load the config
        with pytest.raises(ValueError, match="Module exports a config of type"):
            SampleConfig.from_python_file(temp_path)
    finally:
        # Clean up
        os.unlink(temp_path)


def test_from_python_file_not_found():
    with pytest.raises(FileNotFoundError):
        SampleConfig.from_python_file("nonexistent.py")
