from __future__ import annotations

import json

import pytest

from nshconfig import Adapter, Config


class SampleConfig(Config):
    name: str
    value: int


def test_adapter_with_config_type():
    """Test adapter with basic Config type."""
    adapter = Adapter(SampleConfig)

    # Test validation from dict
    config = adapter.from_python({"name": "test", "value": 42})
    assert isinstance(config, SampleConfig)
    assert config.name == "test"
    assert config.value == 42

    # Test serialization to dict
    data = adapter.to_python(config)
    assert data == {"name": "test", "value": 42}


def test_adapter_with_tuple_type():
    """Test adapter with tuple of types."""
    adapter = Adapter(tuple[SampleConfig, int, str])

    data = ({"name": "test", "value": 42}, 123, "hello")

    # Test validation
    result = adapter.from_python(data)
    assert isinstance(result, tuple)
    assert len(result) == 3
    assert isinstance(result[0], SampleConfig)
    assert result[0].name == "test"
    assert result[0].value == 42
    assert result[1] == 123
    assert result[2] == "hello"

    # Test serialization
    serialized = adapter.to_python(result)
    assert serialized == data


def test_adapter_with_dict_type():
    """Test adapter with dictionary of Configs."""
    adapter = Adapter(dict[str, SampleConfig])

    data = {
        "config1": {"name": "first", "value": 1},
        "config2": {"name": "second", "value": 2},
    }

    # Test validation
    result = adapter.from_python(data)
    assert isinstance(result, dict)
    assert len(result) == 2
    assert isinstance(result["config1"], SampleConfig)
    assert result["config1"].name == "first"
    assert result["config1"].value == 1
    assert isinstance(result["config2"], SampleConfig)
    assert result["config2"].name == "second"
    assert result["config2"].value == 2

    # Test serialization
    serialized = adapter.to_python(result)
    assert serialized == data


def test_adapter_with_nested_types():
    """Test adapter with nested complex types."""
    adapter = Adapter(list[tuple[str, SampleConfig]])

    data = [
        ("first", {"name": "test1", "value": 1}),
        ("second", {"name": "test2", "value": 2}),
    ]

    # Test validation
    result = adapter.from_python(data)
    assert isinstance(result, list)
    assert len(result) == 2
    assert isinstance(result[0], tuple)
    assert result[0][0] == "first"
    assert isinstance(result[0][1], SampleConfig)
    assert result[0][1].name == "test1"
    assert result[0][1].value == 1

    # Test serialization
    serialized = adapter.to_python(result)
    assert serialized == data


def test_adapter_json_serialization():
    """Test JSON serialization with complex types."""
    adapter = Adapter(tuple[SampleConfig, list[str]])

    # Create test data
    config = SampleConfig(name="test", value=42)
    data = (config, ["a", "b", "c"])

    # Test JSON serialization
    json_str = adapter.to_json_str(data)
    loaded = json.loads(json_str)
    assert loaded == [{"name": "test", "value": 42}, ["a", "b", "c"]]

    # Test JSON deserialization
    result = adapter.from_json_str(json_str)
    assert isinstance(result, tuple)
    assert isinstance(result[0], SampleConfig)
    assert result[0].name == "test"
    assert result[0].value == 42
    assert result[1] == ["a", "b", "c"]


def test_adapter_validation_errors():
    """Test adapter validation error handling."""
    adapter = Adapter(tuple[SampleConfig, int])

    # Test invalid types
    with pytest.raises(ValueError):
        adapter.from_python([{"name": "test", "value": "not_an_int"}, 42])

    # Test invalid structure
    with pytest.raises(ValueError):
        adapter.from_python(
            [{"name": "test", "value": 42}]
        )  # Missing second tuple element

    # Test invalid nested validation
    with pytest.raises(ValueError):
        adapter.from_python([{"invalid_field": "test"}, 42])
