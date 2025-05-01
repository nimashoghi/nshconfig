from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from .conftest import SampleConfig


def test_to_json_str(sample_config):
    """Test converting config to JSON string."""
    json_str = sample_config.to_json_str()
    assert isinstance(json_str, str)

    # Verify it's valid JSON
    data = json.loads(json_str)
    assert data["name"] == "test"
    assert data["value"] == 42


def test_from_json_str():
    """Test creating config from JSON string."""
    json_str = json.dumps({"name": "test", "value": 42})
    config = SampleConfig.from_json_str(json_str)
    assert isinstance(config, SampleConfig)
    assert config.name == "test"
    assert config.value == 42


def test_json_file_roundtrip(sample_config):
    """Test JSON file serialization roundtrip."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        temp_path = f.name

    try:
        # Save to file
        sample_config.to_json_file(temp_path)

        # Load from file
        loaded_config = SampleConfig.from_json_file(temp_path)

        # Verify
        assert loaded_config == sample_config
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_json_schema_inclusion():
    """Test JSON serialization with schema inclusion."""
    config = SampleConfig(name="test", value=42)

    # Test with schema
    json_with_schema = config.to_json_str(with_schema=True)
    data = json.loads(json_with_schema)
    assert "$schema" in data
    assert data["$schema"] == "file:///test-schema.json"

    # Test without schema
    json_without_schema = config.to_json_str(with_schema=False)
    data = json.loads(json_without_schema)
    assert "$schema" not in data


def test_json_indentation():
    """Test JSON indentation options."""
    config = SampleConfig(name="test", value=42)

    # Test with custom indent
    json_indented = config.to_json_str(indent=2)
    assert "\n  " in json_indented  # Check for 2-space indentation

    # Test with no indent
    json_compact = config.to_json_str(indent=None)
    assert "\n" not in json_compact  # Should be a single line


def test_from_json_str_invalid():
    """Test error handling for invalid JSON input."""
    with pytest.raises(ValidationError):
        SampleConfig.from_json_str('{"name": "test"}')  # missing required field

    with pytest.raises(ValidationError):
        SampleConfig.from_json_str(
            '{"name": "test", "value": "not_an_int"}'
        )  # wrong type

    with pytest.raises(ValidationError):
        SampleConfig.from_json_str("invalid json")
