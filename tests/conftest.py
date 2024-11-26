from __future__ import annotations

import pytest

from nshconfig import Config


class TestConfig(Config):
    """A simple test configuration class."""
    name: str
    value: int

    # Add schema URI for testing
    __nshconfig_json_schema_uri__ = "file:///test-schema.json"


@pytest.fixture
def sample_config() -> TestConfig:
    """Return a sample TestConfig instance for testing."""
    return TestConfig(name="test", value=42)


@pytest.fixture
def sample_config_dict() -> dict:
    """Return a sample configuration dictionary for testing."""
    return {
        "name": "test",
        "value": 42
    }
