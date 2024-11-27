from __future__ import annotations

import pytest

from .conftest import SampleConfig


def test_from_dict(sample_config_dict):
    """Test creating config from dictionary."""
    config = SampleConfig.from_dict(sample_config_dict)
    assert isinstance(config, SampleConfig)
    assert config.name == "test"
    assert config.value == 42


def test_to_dict(sample_config):
    """Test converting config to dictionary."""
    config_dict = sample_config.to_dict()
    assert isinstance(config_dict, dict)
    assert config_dict["name"] == "test"
    assert config_dict["value"] == 42


def test_from_dict_invalid():
    """Test error handling for invalid dictionary input."""
    with pytest.raises(ValueError):
        SampleConfig.from_dict({"name": "test"})  # missing required field

    with pytest.raises(ValueError):
        SampleConfig.from_dict({"name": "test", "value": "not_an_int"})  # wrong type


def test_dict_roundtrip(sample_config):
    """Test dictionary serialization roundtrip."""
    config_dict = sample_config.to_dict()
    new_config = SampleConfig.from_dict(config_dict)
    assert new_config == sample_config
