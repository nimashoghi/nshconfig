from __future__ import annotations

from typing import Annotated

import pytest
from pydantic_core import PydanticCustomError

import nshconfig as C
from nshconfig._src.missing import MissingValue, validate_no_missing


def test_missing_constant_identity():
    """Test that MISSING is a distinct object with the expected properties."""
    # Using is in comparisons ensures we check for identity, not equality
    assert C.MISSING is C.MISSING


def test_allow_missing_basic_usage():
    """Test basic usage of AllowMissing annotation with a Config class."""

    class TestConfig(C.Config):
        required_field: int
        optional_field: Annotated[str, C.AllowMissing] = C.MISSING

    # Initialize with only required field
    config = TestConfig(required_field=42)
    assert config.required_field == 42
    assert config.optional_field is C.MISSING

    # Initialize with both fields
    config = TestConfig(required_field=42, optional_field="hello")
    assert config.required_field == 42
    assert config.optional_field == "hello"


def test_allow_missing_post_assignment():
    """Test that MISSING fields can be assigned later."""

    class TestConfig(C.Config):
        value: Annotated[str, C.AllowMissing] = C.MISSING

    config = TestConfig()
    assert config.value is C.MISSING

    # Should be able to assign a value later
    config.value = "assigned later"
    assert config.value == "assigned later"


def test_allow_missing_vs_none():
    """Test the difference between MISSING and None in fields."""

    class TestConfig(C.Config):
        missing_field: Annotated[str, C.AllowMissing] = C.MISSING
        none_field: str | None = None

    config = TestConfig()

    # Both are None internally, but behave differently
    assert config.missing_field is C.MISSING
    assert config.none_field is None

    # Distinction is in the validation and type checking behavior


def test_validate_no_missing_values():
    """Test the validation function for checking missing values."""

    class TestModel(C.Config):
        regular_field: int
        allowed_missing: Annotated[str, C.AllowMissing] = C.MISSING

    # Create instance with MISSING field
    model = TestModel(regular_field=42)

    # Should raise an error when validating missing values
    with pytest.raises(PydanticCustomError) as exc_info:
        validate_no_missing(model)

    # Get the PydanticCustomError instance
    err = exc_info.value

    assert err.type == "field_MISSING"
    assert "allowed_missing" in err.message()

    # After assigning a value, validation should pass
    model.allowed_missing = "not missing anymore"
    validate_no_missing(model)


def test_allow_missing_with_complex_types():
    """Test AllowMissing with complex types like lists and dicts."""

    class TestConfig(C.Config):
        list_field: Annotated[list[int], C.AllowMissing] = C.MISSING
        dict_field: Annotated[dict[str, float], C.AllowMissing] = C.MISSING

    config = TestConfig()
    assert config.list_field is C.MISSING
    assert config.dict_field is C.MISSING

    # Assign valid complex types
    config.list_field = [1, 2, 3]
    config.dict_field = {"a": 1.0, "b": 2.5}

    assert config.list_field == [1, 2, 3]
    assert config.dict_field == {"a": 1.0, "b": 2.5}


def test_post_init_with_missing():
    """Test using MISSING in __post_init__ method."""

    class TestConfig(C.Config):
        age: int
        age_str: Annotated[str, C.AllowMissing] = C.MISSING

        def __post_init__(self):
            if self.age_str is C.MISSING:
                self.age_str = str(self.age)

    # When age_str is not provided, it should be set in post_init
    config = TestConfig(age=42)
    assert config.age_str == "42"

    # When age_str is provided, the original value should be kept
    config = TestConfig(age=42, age_str="forty-two")
    assert config.age_str == "forty-two"


def test_allow_missing_in_nested_config():
    """Test AllowMissing in nested Config classes."""

    class NestedConfig(C.Config):
        value: Annotated[str, C.AllowMissing] = C.MISSING

    class ParentConfig(C.Config):
        name: str
        nested: Annotated[NestedConfig, C.AllowMissing] = C.MISSING

    # Create with MISSING nested config
    config = ParentConfig(name="test")
    assert config.nested is C.MISSING

    # Create with nested config that has MISSING field
    config = ParentConfig(name="test", nested=NestedConfig())
    assert config.nested.value is C.MISSING

    # Create with fully specified nested config
    config = ParentConfig(name="test", nested=NestedConfig(value="hello"))
    assert config.nested.value == "hello"


def test_allow_missing_json_schema():
    """Test that AllowMissing generates the correct JSON schema."""

    class TestConfig(C.Config):
        field: Annotated[str, C.AllowMissing] = C.MISSING

    # Get the JSON schema
    json_schema = TestConfig.model_json_schema()
    assert json_schema == {
        "$defs": {
            "MissingValue": {
                "additionalProperties": False,
                "properties": {
                    "NSHCONFIG___MISSING_SENTINEL": {
                        "const": "NSHCONFIG___MISSING_SENTINEL_VALUE",
                        "default": "NSHCONFIG___MISSING_SENTINEL_VALUE",
                        "title": "Missing",
                        "type": "string",
                    },
                },
                "title": "MissingValue",
                "type": "object",
            },
        },
        "properties": {
            "field": {
                "anyOf": [
                    {"type": "string"},
                    {"$ref": "#/$defs/MissingValue"},
                ],
                "default": {
                    "NSHCONFIG___MISSING_SENTINEL": "NSHCONFIG___MISSING_SENTINEL_VALUE"
                },
                "title": "Field",
            },
        },
        "title": "TestConfig",
        "type": "object",
    }


def test_missing_single_instance():
    """Test that MissingValue always returns the same instance."""
    assert MissingValue() is C.MISSING
    assert MissingValue.model_validate({}) is C.MISSING
    assert MissingValue.model_validate_json("{}") is C.MISSING
    assert MissingValue.model_validate_strings({}) is C.MISSING
    assert MissingValue.model_construct() is C.MISSING


def test_new_syntax_allow_missing_basic_usage():
    """Test basic usage of AllowMissing annotation with a Config class."""

    class TestConfig(C.Config):
        required_field: int
        optional_field: C.AllowMissing[str] = C.MISSING

    # Initialize with only required field
    config = TestConfig(required_field=42)
    assert config.required_field == 42
    assert config.optional_field is C.MISSING

    # Initialize with both fields
    config = TestConfig(required_field=42, optional_field="hello")
    assert config.required_field == 42
    assert config.optional_field == "hello"


def test_new_syntax_allow_missing_post_assignment():
    """Test that MISSING fields can be assigned later."""

    class TestConfig(C.Config):
        value: C.AllowMissing[str] = C.MISSING

    config = TestConfig()
    assert config.value is C.MISSING

    # Should be able to assign a value later
    config.value = "assigned later"
    assert config.value == "assigned later"


def test_new_syntax_allow_missing_vs_none():
    """Test the difference between MISSING and None in fields."""

    class TestConfig(C.Config):
        missing_field: C.AllowMissing[str] = C.MISSING
        none_field: str | None = None

    config = TestConfig()

    # Both are None internally, but behave differently
    assert config.missing_field is C.MISSING
    assert config.none_field is None

    # Distinction is in the validation and type checking behavior


def test_new_syntax_validate_no_missing_values():
    """Test the validation function for checking missing values."""

    class TestModel(C.Config):
        regular_field: int
        allowed_missing: C.AllowMissing[str] = C.MISSING

    # Create instance with MISSING field
    model = TestModel(regular_field=42)

    # Should raise an error when validating missing values
    with pytest.raises(PydanticCustomError) as exc_info:
        validate_no_missing(model)

    # Get the PydanticCustomError instance
    err = exc_info.value

    assert err.type == "field_MISSING"
    assert "allowed_missing" in err.message()

    # After assigning a value, validation should pass
    model.allowed_missing = "not missing anymore"
    validate_no_missing(model)


def test_new_syntax_allow_missing_with_complex_types():
    """Test AllowMissing with complex types like lists and dicts."""

    class TestConfig(C.Config):
        list_field: C.AllowMissing[list[int]] = C.MISSING
        dict_field: C.AllowMissing[dict[str, float]] = C.MISSING

    config = TestConfig()
    assert config.list_field is C.MISSING
    assert config.dict_field is C.MISSING

    # Assign valid complex types
    config.list_field = [1, 2, 3]
    config.dict_field = {"a": 1.0, "b": 2.5}

    assert config.list_field == [1, 2, 3]
    assert config.dict_field == {"a": 1.0, "b": 2.5}


def test_new_syntax_post_init_with_missing():
    """Test using MISSING in __post_init__ method."""

    class TestConfig(C.Config):
        age: int
        age_str: C.AllowMissing[str] = C.MISSING

        def __post_init__(self):
            if self.age_str is C.MISSING:
                self.age_str = str(self.age)

    # When age_str is not provided, it should be set in post_init
    config = TestConfig(age=42)
    assert config.age_str == "42"

    # When age_str is provided, the original value should be kept
    config = TestConfig(age=42, age_str="forty-two")
    assert config.age_str == "forty-two"


def test_new_syntax_allow_missing_in_nested_config():
    """Test AllowMissing in nested Config classes."""

    class NestedConfig(C.Config):
        value: C.AllowMissing[str] = C.MISSING

    class ParentConfig(C.Config):
        name: str
        nested: C.AllowMissing[NestedConfig] = C.MISSING

    # Create with MISSING nested config
    config = ParentConfig(name="test")
    assert config.nested is C.MISSING

    # Create with nested config that has MISSING field
    config = ParentConfig(name="test", nested=NestedConfig())
    assert config.nested.value is C.MISSING

    # Create with fully specified nested config
    config = ParentConfig(name="test", nested=NestedConfig(value="hello"))
    assert config.nested.value == "hello"


def test_new_syntax_allow_missing_json_schema():
    """Test that AllowMissing generates the correct JSON schema."""

    class TestConfig(C.Config):
        field: C.AllowMissing[str] = C.MISSING

    # Get the JSON schema
    json_schema = TestConfig.model_json_schema()
    assert json_schema == {
        "$defs": {
            "MissingValue": {
                "additionalProperties": False,
                "properties": {
                    "NSHCONFIG___MISSING_SENTINEL": {
                        "const": "NSHCONFIG___MISSING_SENTINEL_VALUE",
                        "default": "NSHCONFIG___MISSING_SENTINEL_VALUE",
                        "title": "Missing",
                        "type": "string",
                    },
                },
                "title": "MissingValue",
                "type": "object",
            },
        },
        "properties": {
            "field": {
                "anyOf": [
                    {"type": "string"},
                    {"$ref": "#/$defs/MissingValue"},
                ],
                "default": {
                    "NSHCONFIG___MISSING_SENTINEL": "NSHCONFIG___MISSING_SENTINEL_VALUE"
                },
                "title": "Field",
            },
        },
        "title": "TestConfig",
        "type": "object",
    }


def test_old_syntax_allow_missing_basic_usage():
    """Test basic usage of AllowMissing annotation with a Config class."""

    class TestConfig(C.Config):
        required_field: int
        optional_field: Annotated[str, C.AllowMissing()] = C.MISSING  # pyright: ignore[reportCallIssue]

    # Initialize with only required field
    config = TestConfig(required_field=42)
    assert config.required_field == 42
    assert config.optional_field is C.MISSING

    # Initialize with both fields
    config = TestConfig(required_field=42, optional_field="hello")
    assert config.required_field == 42
    assert config.optional_field == "hello"


def test_old_syntax_allow_missing_post_assignment():
    """Test that MISSING fields can be assigned later."""

    class TestConfig(C.Config):
        value: Annotated[str, C.AllowMissing()] = C.MISSING  # pyright: ignore[reportCallIssue]

    config = TestConfig()
    assert config.value is C.MISSING

    # Should be able to assign a value later
    config.value = "assigned later"
    assert config.value == "assigned later"


def test_old_syntax_allow_missing_vs_none():
    """Test the difference between MISSING and None in fields."""

    class TestConfig(C.Config):
        missing_field: Annotated[str, C.AllowMissing()] = C.MISSING  # pyright: ignore[reportCallIssue]
        none_field: str | None = None

    config = TestConfig()

    # Both are None internally, but behave differently
    assert config.missing_field is C.MISSING
    assert config.none_field is None

    # Distinction is in the validation and type checking behavior


def test_old_syntax_validate_no_missing_values():
    """Test the validation function for checking missing values."""

    class TestModel(C.Config):
        regular_field: int
        allowed_missing: Annotated[str, C.AllowMissing()] = C.MISSING  # pyright: ignore[reportCallIssue]

    # Create instance with MISSING field
    model = TestModel(regular_field=42)

    # Should raise an error when validating missing values
    with pytest.raises(PydanticCustomError) as exc_info:
        validate_no_missing(model)

    # Get the PydanticCustomError instance
    err = exc_info.value

    assert err.type == "field_MISSING"
    assert "allowed_missing" in err.message()

    # After assigning a value, validation should pass
    model.allowed_missing = "not missing anymore"
    validate_no_missing(model)


def test_old_syntax_allow_missing_with_complex_types():
    """Test AllowMissing with complex types like lists and dicts."""

    class TestConfig(C.Config):
        list_field: Annotated[list[int], C.AllowMissing()] = C.MISSING  # pyright: ignore[reportCallIssue]
        dict_field: Annotated[dict[str, float], C.AllowMissing()] = C.MISSING  # pyright: ignore[reportCallIssue]

    config = TestConfig()
    assert config.list_field is C.MISSING
    assert config.dict_field is C.MISSING

    # Assign valid complex types
    config.list_field = [1, 2, 3]
    config.dict_field = {"a": 1.0, "b": 2.5}

    assert config.list_field == [1, 2, 3]
    assert config.dict_field == {"a": 1.0, "b": 2.5}


def test_old_syntax_post_init_with_missing():
    """Test using MISSING in __post_init__ method."""

    class TestConfig(C.Config):
        age: int
        age_str: Annotated[str, C.AllowMissing()] = C.MISSING  # pyright: ignore[reportCallIssue]

        def __post_init__(self):
            if self.age_str is C.MISSING:
                self.age_str = str(self.age)

    # When age_str is not provided, it should be set in post_init
    config = TestConfig(age=42)
    assert config.age_str == "42"

    # When age_str is provided, the original value should be kept
    config = TestConfig(age=42, age_str="forty-two")
    assert config.age_str == "forty-two"


def test_old_syntax_allow_missing_in_nested_config():
    """Test AllowMissing in nested Config classes."""

    class NestedConfig(C.Config):
        value: Annotated[str, C.AllowMissing()] = C.MISSING  # pyright: ignore[reportCallIssue]

    class ParentConfig(C.Config):
        name: str
        nested: Annotated[NestedConfig, C.AllowMissing()] = C.MISSING  # pyright: ignore[reportCallIssue]

    # Create with MISSING nested config
    config = ParentConfig(name="test")
    assert config.nested is C.MISSING

    # Create with nested config that has MISSING field
    config = ParentConfig(name="test", nested=NestedConfig())
    assert config.nested.value is C.MISSING

    # Create with fully specified nested config
    config = ParentConfig(name="test", nested=NestedConfig(value="hello"))
    assert config.nested.value == "hello"


def test_old_syntax_allow_missing_json_schema():
    """Test that AllowMissing generates the correct JSON schema."""

    class TestConfig(C.Config):
        field: Annotated[str, C.AllowMissing()] = C.MISSING  # pyright: ignore[reportCallIssue]

    # Get the JSON schema
    json_schema = TestConfig.model_json_schema()
    assert json_schema == {
        "$defs": {
            "MissingValue": {
                "additionalProperties": False,
                "properties": {
                    "NSHCONFIG___MISSING_SENTINEL": {
                        "const": "NSHCONFIG___MISSING_SENTINEL_VALUE",
                        "default": "NSHCONFIG___MISSING_SENTINEL_VALUE",
                        "title": "Missing",
                        "type": "string",
                    },
                },
                "title": "MissingValue",
                "type": "object",
            },
        },
        "properties": {
            "field": {
                "anyOf": [
                    {"type": "string"},
                    {"$ref": "#/$defs/MissingValue"},
                ],
                "default": {
                    "NSHCONFIG___MISSING_SENTINEL": "NSHCONFIG___MISSING_SENTINEL_VALUE"
                },
                "title": "Field",
            },
        },
        "title": "TestConfig",
        "type": "object",
    }
