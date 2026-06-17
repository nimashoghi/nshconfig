"""nshconfig v2: typed, provenance-aware configuration for ML runs.

One verb family and one value:

- ``Cls.config_draft()`` makes a mutable draft (plain assignment, auto-vivifying nesting).
- ``C.interp(lambda c: ...)`` is a VALUE that resolves against the config tree at
  validation, legal anywhere a value sits (draft assignment, input dict, class default).
- ``draft.config_finalize()`` resolves, validates once, and returns a frozen final;
  ``config_thaw`` / ``config_explain`` / ``config_provenance`` / ``config_is_draft``
  complete the family. Module functions (``finalize``, ``explain``, ...) are aliases.

Plus provenance: ``final.config_explain("optim.lr")`` answers "why did this run use
that value", down to file:line and the interpolation's "because" chain.
"""

from importlib.metadata import PackageNotFoundError, version

from pydantic import AfterValidator as AfterValidator
from pydantic import AliasChoices as AliasChoices
from pydantic import AliasGenerator as AliasGenerator
from pydantic import AliasPath as AliasPath
from pydantic import AllowInfNan as AllowInfNan
from pydantic import AmqpDsn as AmqpDsn
from pydantic import AnyHttpUrl as AnyHttpUrl
from pydantic import AnyUrl as AnyUrl
from pydantic import AnyWebsocketUrl as AnyWebsocketUrl
from pydantic import AwareDatetime as AwareDatetime
from pydantic import Base64Bytes as Base64Bytes
from pydantic import Base64Encoder as Base64Encoder
from pydantic import Base64Str as Base64Str
from pydantic import Base64UrlBytes as Base64UrlBytes
from pydantic import Base64UrlStr as Base64UrlStr
from pydantic import BaseModel as BaseModel
from pydantic import BeforeValidator as BeforeValidator
from pydantic import ByteSize as ByteSize
from pydantic import ClickHouseDsn as ClickHouseDsn
from pydantic import CockroachDsn as CockroachDsn
from pydantic import ConfigDict as ConfigDict
from pydantic import DirectoryPath as DirectoryPath
from pydantic import Discriminator as Discriminator
from pydantic import EmailStr as EmailStr
from pydantic import EncodedBytes as EncodedBytes
from pydantic import EncodedStr as EncodedStr
from pydantic import EncoderProtocol as EncoderProtocol
from pydantic import FailFast as FailFast
from pydantic import Field as Field
from pydantic import FieldSerializationInfo as FieldSerializationInfo
from pydantic import FilePath as FilePath
from pydantic import FileUrl as FileUrl
from pydantic import FiniteFloat as FiniteFloat
from pydantic import FtpUrl as FtpUrl
from pydantic import FutureDate as FutureDate
from pydantic import FutureDatetime as FutureDatetime
from pydantic import GetCoreSchemaHandler as GetCoreSchemaHandler
from pydantic import GetJsonSchemaHandler as GetJsonSchemaHandler
from pydantic import GetPydanticSchema as GetPydanticSchema
from pydantic import HttpUrl as HttpUrl
from pydantic import IPvAnyAddress as IPvAnyAddress
from pydantic import IPvAnyInterface as IPvAnyInterface
from pydantic import IPvAnyNetwork as IPvAnyNetwork
from pydantic import ImportString as ImportString
from pydantic import InstanceOf as InstanceOf
from pydantic import Json as Json
from pydantic import JsonValue as JsonValue
from pydantic import KafkaDsn as KafkaDsn
from pydantic import MariaDBDsn as MariaDBDsn
from pydantic import ModelWrapValidatorHandler as ModelWrapValidatorHandler
from pydantic import MongoDsn as MongoDsn
from pydantic import MySQLDsn as MySQLDsn
from pydantic import NaiveDatetime as NaiveDatetime
from pydantic import NameEmail as NameEmail
from pydantic import NatsDsn as NatsDsn
from pydantic import NegativeFloat as NegativeFloat
from pydantic import NegativeInt as NegativeInt
from pydantic import NewPath as NewPath
from pydantic import NonNegativeFloat as NonNegativeFloat
from pydantic import NonNegativeInt as NonNegativeInt
from pydantic import NonPositiveFloat as NonPositiveFloat
from pydantic import NonPositiveInt as NonPositiveInt
from pydantic import OnErrorOmit as OnErrorOmit
from pydantic import PastDate as PastDate
from pydantic import PastDatetime as PastDatetime
from pydantic import PaymentCardNumber as PaymentCardNumber
from pydantic import PlainSerializer as PlainSerializer
from pydantic import PlainValidator as PlainValidator
from pydantic import PositiveFloat as PositiveFloat
from pydantic import PositiveInt as PositiveInt
from pydantic import PostgresDsn as PostgresDsn
from pydantic import PrivateAttr as PrivateAttr
from pydantic import PydanticErrorCodes as PydanticErrorCodes
from pydantic import PydanticForbiddenQualifier as PydanticForbiddenQualifier
from pydantic import PydanticImportError as PydanticImportError
from pydantic import PydanticInvalidForJsonSchema as PydanticInvalidForJsonSchema
from pydantic import PydanticSchemaGenerationError as PydanticSchemaGenerationError
from pydantic import PydanticUndefinedAnnotation as PydanticUndefinedAnnotation
from pydantic import PydanticUserError as PydanticUserError
from pydantic import RedisDsn as RedisDsn
from pydantic import RootModel as RootModel
from pydantic import Secret as Secret
from pydantic import SecretBytes as SecretBytes
from pydantic import SecretStr as SecretStr
from pydantic import SerializationInfo as SerializationInfo
from pydantic import SerializeAsAny as SerializeAsAny
from pydantic import SerializerFunctionWrapHandler as SerializerFunctionWrapHandler
from pydantic import SkipValidation as SkipValidation
from pydantic import SnowflakeDsn as SnowflakeDsn
from pydantic import SocketPath as SocketPath
from pydantic import Strict as Strict
from pydantic import StrictBool as StrictBool
from pydantic import StrictBytes as StrictBytes
from pydantic import StrictFloat as StrictFloat
from pydantic import StrictInt as StrictInt
from pydantic import StrictStr as StrictStr
from pydantic import StringConstraints as StringConstraints
from pydantic import Tag as Tag
from pydantic import TypeAdapter as TypeAdapter
from pydantic import UUID1 as UUID1
from pydantic import UUID3 as UUID3
from pydantic import UUID4 as UUID4
from pydantic import UUID5 as UUID5
from pydantic import UUID6 as UUID6
from pydantic import UUID7 as UUID7
from pydantic import UUID8 as UUID8
from pydantic import UrlConstraints as UrlConstraints
from pydantic import ValidateAs as ValidateAs
from pydantic import ValidationError as ValidationError
from pydantic import ValidationInfo as ValidationInfo
from pydantic import ValidatorFunctionWrapHandler as ValidatorFunctionWrapHandler
from pydantic import WebsocketUrl as WebsocketUrl
from pydantic import WithJsonSchema as WithJsonSchema
from pydantic import WrapSerializer as WrapSerializer
from pydantic import WrapValidator as WrapValidator
from pydantic import computed_field as computed_field
from pydantic import conbytes as conbytes
from pydantic import condate as condate
from pydantic import condecimal as condecimal
from pydantic import confloat as confloat
from pydantic import confrozenset as confrozenset
from pydantic import conint as conint
from pydantic import conlist as conlist
from pydantic import conset as conset
from pydantic import constr as constr
from pydantic import create_model as create_model
from pydantic import dataclasses as dataclasses
from pydantic import field_serializer as field_serializer
from pydantic import field_validator as field_validator
from pydantic import model_serializer as model_serializer
from pydantic import model_validator as model_validator
from pydantic import validate_call as validate_call
from pydantic import validate_email as validate_email
from pydantic import with_config as with_config

from ._src.config import Config as Config
from ._src.config import is_draft as is_draft
from ._src.config import set_model_config_defaults as set_model_config_defaults
from ._src.errors import DraftError as DraftError
from ._src.errors import UnsetError as UnsetError
from ._src.finalize import finalize as finalize
from ._src.finalize import thaw as thaw
from ._src.interp import Ctx as Ctx
from ._src.interp import Interp as Interp
from ._src.interp import interp as interp
from ._src.provenance import Event as Event
from ._src.provenance import Explanation as Explanation
from ._src.provenance import explain as explain
from ._src.provenance import provenance as provenance
from ._src.provenance import source as source

try:
    __version__ = version(__name__)
except PackageNotFoundError:
    __version__ = "unknown"

__all__ = [
    # Pydantic v2 authoring API.
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
    # nshconfig-native API.
    "Config",
    "Ctx",
    "DraftError",
    "Event",
    "Explanation",
    "Interp",
    "UnsetError",
    "explain",
    "finalize",
    "interp",
    "is_draft",
    "provenance",
    "set_model_config_defaults",
    "source",
    "thaw",
    "__version__",
]
