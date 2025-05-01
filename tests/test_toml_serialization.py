from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from .conftest import SampleConfig


def requires_toml(func):
    """Decorator to skip tests if tomli or tomli_w is not installed."""
    try:
        import tomli  # noqa
        import tomli_w  # noqa

        return func
    except ImportError:
        return pytest.mark.skip(reason="tomli or tomli-w not installed")(func)


@requires_toml
def test_to_toml_str(sample_config):
    """Test converting config to TOML string."""
    toml_str = sample_config.to_toml_str()
    assert isinstance(toml_str, str)
    assert 'name = "test"' in toml_str
    assert "value = 42" in toml_str


@requires_toml
def test_toml_file_roundtrip(sample_config):
    """Test TOML file serialization roundtrip."""
    with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as f:
        temp_path = f.name

    try:
        sample_config.to_toml_file(temp_path)
        loaded = SampleConfig.from_toml_file(temp_path)
        assert loaded == sample_config
    finally:
        Path(temp_path).unlink(missing_ok=True)


@requires_toml
def test_from_toml_str():
    """Test creating config from TOML string."""
    toml_str = """
name = "test"
value = 42
"""
    config = SampleConfig.from_toml_str(toml_str)
    assert isinstance(config, SampleConfig)
    assert config.name == "test"
    assert config.value == 42


@requires_toml
def test_from_toml_str_invalid():
    """Test error handling for invalid TOML input."""
    with pytest.raises(ValidationError):
        SampleConfig.from_toml_str('name = "test"')

    with pytest.raises(ValidationError):
        SampleConfig.from_toml_str('name = "test"\nvalue = "not_an_int"')


def test_toml_not_installed():
    """Test error handling when tomli or tomli_w is not installed."""
    config = SampleConfig(name="test", value=42)
    with patch.dict(sys.modules, {"tomli_w": None}):
        with pytest.raises(ImportError, match="Tomli-w is required"):
            config.to_toml_str()

        with pytest.raises(ImportError, match="Tomli-w is required"):
            config.to_toml_file("test.toml")

    with patch.dict(sys.modules, {"tomli": None}):
        with pytest.raises(ImportError, match="Tomli is required"):
            SampleConfig.from_toml_str('name = "test"\nvalue = 42')

    with patch.dict(sys.modules, {"tomli": None}):
        # Create a temporary TOML file to reach from_toml_file import check
        with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as tmp:
            tmp.write(b'name = "test"\nvalue = 42')
            with pytest.raises(ImportError, match="Tomli is required"):
                SampleConfig.from_toml_file(tmp.name)
