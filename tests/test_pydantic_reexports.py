"""Pydantic authoring API re-exported by nshconfig."""

from importlib.metadata import version
from typing import Annotated

import pydantic
import pytest

import nshconfig as C

PYDANTIC_REEXPORTS = [
    "AfterValidator",
    "AliasChoices",
    "AliasGenerator",
    "AliasPath",
    "AllowInfNan",
    "AmqpDsn",
    "AnyHttpUrl",
    "AnyUrl",
    "AnyWebsocketUrl",
    "AwareDatetime",
    "Base64Bytes",
    "Base64Encoder",
    "Base64Str",
    "Base64UrlBytes",
    "Base64UrlStr",
    "BaseModel",
    "BeforeValidator",
    "ByteSize",
    "ClickHouseDsn",
    "CockroachDsn",
    "ConfigDict",
    "DirectoryPath",
    "Discriminator",
    "EmailStr",
    "EncodedBytes",
    "EncodedStr",
    "EncoderProtocol",
    "FailFast",
    "Field",
    "FieldSerializationInfo",
    "FilePath",
    "FileUrl",
    "FiniteFloat",
    "FtpUrl",
    "FutureDate",
    "FutureDatetime",
    "GetCoreSchemaHandler",
    "GetJsonSchemaHandler",
    "GetPydanticSchema",
    "HttpUrl",
    "IPvAnyAddress",
    "IPvAnyInterface",
    "IPvAnyNetwork",
    "ImportString",
    "InstanceOf",
    "Json",
    "JsonValue",
    "KafkaDsn",
    "MariaDBDsn",
    "ModelWrapValidatorHandler",
    "MongoDsn",
    "MySQLDsn",
    "NaiveDatetime",
    "NameEmail",
    "NatsDsn",
    "NegativeFloat",
    "NegativeInt",
    "NewPath",
    "NonNegativeFloat",
    "NonNegativeInt",
    "NonPositiveFloat",
    "NonPositiveInt",
    "OnErrorOmit",
    "PastDate",
    "PastDatetime",
    "PaymentCardNumber",
    "PlainSerializer",
    "PlainValidator",
    "PositiveFloat",
    "PositiveInt",
    "PostgresDsn",
    "PrivateAttr",
    "PydanticErrorCodes",
    "PydanticForbiddenQualifier",
    "PydanticImportError",
    "PydanticInvalidForJsonSchema",
    "PydanticSchemaGenerationError",
    "PydanticUndefinedAnnotation",
    "PydanticUserError",
    "RedisDsn",
    "RootModel",
    "Secret",
    "SecretBytes",
    "SecretStr",
    "SerializationInfo",
    "SerializeAsAny",
    "SerializerFunctionWrapHandler",
    "SkipValidation",
    "SnowflakeDsn",
    "SocketPath",
    "Strict",
    "StrictBool",
    "StrictBytes",
    "StrictFloat",
    "StrictInt",
    "StrictStr",
    "StringConstraints",
    "Tag",
    "TypeAdapter",
    "UUID1",
    "UUID3",
    "UUID4",
    "UUID5",
    "UUID6",
    "UUID7",
    "UUID8",
    "UrlConstraints",
    "ValidateAs",
    "ValidationError",
    "ValidationInfo",
    "ValidatorFunctionWrapHandler",
    "WebsocketUrl",
    "WithJsonSchema",
    "WrapSerializer",
    "WrapValidator",
    "computed_field",
    "conbytes",
    "condate",
    "condecimal",
    "confloat",
    "confrozenset",
    "conint",
    "conlist",
    "conset",
    "constr",
    "create_model",
    "dataclasses",
    "field_serializer",
    "field_validator",
    "model_serializer",
    "model_validator",
    "validate_call",
    "validate_email",
    "with_config",
]

EXCLUDED_PYDANTIC_NAMES = [
    "BaseConfig",
    "Extra",
    "PydanticDeprecatedSince20",
    "PydanticDeprecatedSince210",
    "PydanticDeprecatedSince211",
    "PydanticDeprecatedSince212",
    "PydanticDeprecatedSince26",
    "PydanticDeprecatedSince29",
    "PydanticDeprecationWarning",
    "PydanticExperimentalWarning",
    "VERSION",
    "parse_obj_as",
    "root_validator",
    "schema_json_of",
    "schema_of",
    "validator",
]


def test_pydantic_reexports_are_same_objects():
    for name in PYDANTIC_REEXPORTS:
        assert name in C.__all__
        assert getattr(C, name) is getattr(pydantic, name)


def test_deprecated_and_version_pydantic_names_are_not_reexported():
    for name in EXCLUDED_PYDANTIC_NAMES:
        assert name not in C.__all__

    assert C.__version__ == version("nshconfig")


def test_reexports_support_config_authoring():
    class ReexportConfig(C.Config):
        model_config = C.ConfigDict(str_strip_whitespace=True)

        x: Annotated[int, C.Field(gt=0), C.AfterValidator(lambda v: v)]
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

    cfg = ReexportConfig(x=1, name=" ok ")
    assert cfg.name == "ok"
    assert C.TypeAdapter(C.PositiveInt).validate_python(3) == 3

    with pytest.raises(C.ValidationError):
        ReexportConfig(x=0)

    with pytest.raises(C.ValidationError):
        ReexportConfig(x=1, name="bad")
