from __future__ import annotations

import pytest

from nshconfig import Config


class SampleConfig(Config):
    name: str
    value: int


def test_draft_config_success():
    config = SampleConfig.draft()
    config.name = "test_instance"
    config.value = 123
    config = config.finalize()

    assert isinstance(config, SampleConfig)
    assert config.name == "test_instance"
    assert config.value == 123


def test_draft_config_missing_name():
    config = SampleConfig.draft()
    # Only set value, leave name missing
    config.value = 123

    with pytest.raises(ValueError):
        config.finalize()


def test_draft_config_missing_value():
    config = SampleConfig.draft()
    # Only set name, leave value missing
    config.name = "test_instance"

    with pytest.raises(ValueError):
        config.finalize()


def test_draft_config_missing_all_fields():
    config = SampleConfig.draft()
    # Don't set any fields

    with pytest.raises(ValueError):
        config.finalize()
