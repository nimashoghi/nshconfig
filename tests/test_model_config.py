"""Global pydantic model_config defaults for Config subclasses."""

import pytest
from pydantic import ConfigDict, PydanticSchemaGenerationError, ValidationError

import nshconfig as C


@pytest.fixture(autouse=True)
def reset_model_config_defaults():
    C.set_model_config_defaults()
    yield
    C.set_model_config_defaults()


def test_builtin_defaults_forbid_extra_and_validate_strictly():
    class Builtin(C.Config):
        x: int

    with pytest.raises(ValidationError) as extra:
        Builtin.model_validate({"x": 1, "y": 2})
    assert extra.value.errors()[0]["type"] == "extra_forbidden"

    with pytest.raises(ValidationError) as strict:
        Builtin.model_validate({"x": "1"})
    assert strict.value.errors()[0]["type"] == "int_type"


def test_builtin_defaults_use_attribute_docstrings():
    class WithDoc(C.Config):
        x: int
        """The configured width."""

    assert WithDoc.model_fields["x"].description == "The configured width."


def test_global_defaults_apply_to_future_subclasses():
    class Weird:
        pass

    C.set_model_config_defaults(arbitrary_types_allowed=True)

    class UsesWeird(C.Config):
        value: Weird

    value = Weird()
    assert UsesWeird(value=value).value is value


def test_global_defaults_are_replaced_not_accumulated():
    class Weird:
        pass

    C.set_model_config_defaults(arbitrary_types_allowed=True)
    C.set_model_config_defaults(strict=False)

    with pytest.raises(PydanticSchemaGenerationError):
        class UsesWeird(C.Config):
            value: Weird


def test_global_defaults_can_be_reset_to_builtins():
    C.set_model_config_defaults(strict=False)
    C.set_model_config_defaults()

    class Reset(C.Config):
        x: int

    with pytest.raises(ValidationError) as strict:
        Reset.model_validate({"x": "1"})
    assert strict.value.errors()[0]["type"] == "int_type"


def test_global_defaults_only_affect_future_subclasses():
    class Before(C.Config):
        x: int

    C.set_model_config_defaults(strict=False)

    class After(C.Config):
        x: int

    with pytest.raises(ValidationError) as before:
        Before.model_validate({"x": "1"})
    assert before.value.errors()[0]["type"] == "int_type"
    assert After.model_validate({"x": "1"}).x == 1


def test_global_defaults_apply_to_future_indirect_subclasses():
    class ProjectBase(C.Config):
        pass

    C.set_model_config_defaults(strict=False)

    class Child(ProjectBase):
        x: int

    assert Child.model_validate({"x": "1"}).x == 1


def test_inherited_explicit_config_survives_global_defaults():
    class Weird:
        pass

    class ProjectBase(C.Config, arbitrary_types_allowed=True):
        pass

    C.set_model_config_defaults(strict=False)

    class Child(ProjectBase):
        value: Weird
        x: int

    value = Weird()
    child = Child.model_validate({"value": value, "x": "1"})
    assert child.value is value
    assert child.x == 1


def test_per_class_model_config_overrides_global_defaults():
    class Loose(C.Config, strict=False):
        x: int

    assert Loose.model_validate({"x": "1"}).x == 1


def test_per_class_model_config_attribute_overrides_global_defaults():
    class Loose(C.Config):
        model_config = ConfigDict(strict=False)
        x: int

    assert Loose.model_validate({"x": "1"}).x == 1
