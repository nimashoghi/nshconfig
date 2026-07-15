"""The supported Pydantic authoring surface is available from nshconfig."""

from importlib.metadata import version
from typing import Annotated

import pydantic
import pytest

import nshconfig as C

_NSHCONFIG_NAMES = {
    "Config",
    "Context",
    "DraftError",
    "UnsetError",
    "__version__",
    "interp",
    "is_draft",
}
_EXCLUDED_PYDANTIC_NAMES = {
    "BaseConfig",
    "Extra",
    "PydanticDeprecatedSince20",
    "PydanticDeprecatedSince26",
    "PydanticDeprecatedSince29",
    "PydanticDeprecatedSince210",
    "PydanticDeprecatedSince211",
    "PydanticDeprecatedSince212",
    "PydanticDeprecationWarning",
    "PydanticExperimentalWarning",
    "VERSION",
    "__version__",
    "parse_obj_as",
    "root_validator",
    "schema_json_of",
    "schema_of",
    "validator",
}


def test_non_deprecated_pydantic_authoring_api_is_reexported() -> None:
    expected = set(pydantic.__all__) - _EXCLUDED_PYDANTIC_NAMES
    actual = set(C.__all__) - _NSHCONFIG_NAMES

    assert actual == expected
    for name in expected:
        assert getattr(C, name) is getattr(pydantic, name)


def test_deprecated_and_version_names_are_not_reexported() -> None:
    excluded = _EXCLUDED_PYDANTIC_NAMES - {"__version__"}
    assert excluded.isdisjoint(C.__all__)
    assert C.__version__ == version("nshconfig")


def test_reexports_cover_normal_config_authoring() -> None:
    class ReexportConfig(C.Config):
        model_config = C.ConfigDict(str_strip_whitespace=True)

        count: Annotated[int, C.Field(gt=0)]
        name: str = C.Field(default="default")

        @C.field_validator("name")
        @classmethod
        def validate_name(cls, value: str) -> str:
            if not value:
                raise ValueError("empty name")
            return value

        @C.model_validator(mode="after")
        def validate_model(self) -> "ReexportConfig":
            if self.name == "bad":
                raise ValueError("bad name")
            return self

    config = ReexportConfig(count=1, name=" ok ")
    assert config.name == "ok"
    assert C.TypeAdapter(C.PositiveInt).validate_python(3) == 3

    with pytest.raises(C.ValidationError):
        ReexportConfig(count=0)
    with pytest.raises(C.ValidationError):
        ReexportConfig(count=1, name="bad")
