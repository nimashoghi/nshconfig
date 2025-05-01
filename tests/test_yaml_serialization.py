from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from .conftest import SampleConfig


def requires_yaml(func):
    """Decorator to skip tests if pydantic-yaml is not installed."""
    try:
        import pydantic_yaml  # noqa

        return func
    except ImportError:
        return pytest.mark.skip(reason="pydantic-yaml not installed")(func)


@requires_yaml
def test_to_yaml_str(sample_config):
    """Test converting config to YAML string."""
    yaml_str = sample_config.to_yaml_str()
    assert isinstance(yaml_str, str)
    assert "name: test" in yaml_str
    assert "value: 42" in yaml_str


@requires_yaml
def test_yaml_schema_inclusion():
    """Test YAML serialization with schema inclusion."""
    config = SampleConfig(name="test", value=42)

    # Test with schema
    yaml_with_schema = config.to_yaml_str(with_schema=True)
    assert "yaml-language-server: $schema=file:///test-schema.json" in yaml_with_schema

    # Test without schema
    yaml_without_schema = config.to_yaml_str(with_schema=False)
    assert "yaml-language-server: $schema=" not in yaml_without_schema


@requires_yaml
def test_yaml_file_roundtrip(sample_config):
    """Test YAML file serialization roundtrip."""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
        temp_path = f.name

    try:
        # Save to file
        sample_config.to_yaml_file(temp_path)

        # Load from file
        loaded_config = SampleConfig.from_yaml(temp_path)

        # Verify
        assert loaded_config == sample_config
    finally:
        Path(temp_path).unlink(missing_ok=True)


@requires_yaml
def test_from_yaml_str():
    """Test creating config from YAML string."""
    yaml_str = """
    name: test
    value: 42
    """
    config = SampleConfig.from_yaml_str(yaml_str)
    assert isinstance(config, SampleConfig)
    assert config.name == "test"
    assert config.value == 42


@requires_yaml
def test_from_yaml_str_invalid():
    """Test error handling for invalid YAML input."""
    with pytest.raises(ValidationError):
        SampleConfig.from_yaml_str("""
        name: test
        """)  # missing required field

    with pytest.raises(ValidationError):
        SampleConfig.from_yaml_str("""
        name: test
        value: not_an_int
        """)  # wrong type


def test_yaml_not_installed():
    """Test error handling when pydantic-yaml is not installed."""
    # Mock the import to raise ImportError
    with patch.dict(sys.modules, {"pydantic_yaml": None}):
        config = SampleConfig(name="test", value=42)

        with pytest.raises(
            ImportError, match="Pydantic-yaml is required for YAML support"
        ):
            config.to_yaml_str()

        with pytest.raises(
            ImportError, match="Pydantic-yaml is required for YAML support"
        ):
            config.to_yaml_file("test.yaml")

        with pytest.raises(
            ImportError, match="Pydantic-yaml is required for YAML support"
        ):
            SampleConfig.from_yaml_str("name: test\nvalue: 42")

        with pytest.raises(
            ImportError, match="Pydantic-yaml is required for YAML support"
        ):
            SampleConfig.from_yaml("test.yaml")
