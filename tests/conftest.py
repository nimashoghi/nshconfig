from __future__ import annotations

import pytest

from nshconfig import Config


class SampleConfig(Config):
    """A simple test configuration class."""

    name: str
    value: int

    # Add schema URI for testing
    __nshconfig_json_schema_uri__ = "file:///test-schema.json"


@pytest.fixture
def sample_config() -> SampleConfig:
    """Return a sample SampleConfig instance for testing."""
    return SampleConfig(name="test", value=42)


@pytest.fixture
def sample_config_dict() -> dict:
    """Return a sample configuration dictionary for testing."""
    return {"name": "test", "value": 42}
