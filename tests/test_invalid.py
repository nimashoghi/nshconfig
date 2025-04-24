from __future__ import annotations

import pytest

import nshconfig as C


def test_invalid_instantiation():
    """Test that instantiating C.Invalid always raises a ValueError."""
    with pytest.raises(ValueError, match="This is an invalid configuration."):
        C.Invalid()

    with pytest.raises(ValueError, match="This is an invalid configuration."):
        C.Invalid(some_field="value")  # type: ignore


def test_invalid_model_validator():
    """Test that the model validator is correctly implemented and raises a ValueError."""
    with pytest.raises(ValueError, match="This is an invalid configuration."):
        C.Invalid.model_validate({})

    with pytest.raises(ValueError, match="This is an invalid configuration."):
        C.Invalid.model_validate({"some_field": "value"})
