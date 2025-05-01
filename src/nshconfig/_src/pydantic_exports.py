# ruff: noqa: I001
from __future__ import annotations

# region Pydantic Exports
# full module export
import pydantic as pydantic

# dataclasses
from pydantic import dataclasses as dataclasses

# functional validators
from pydantic import field_validator as field_validator
from pydantic import model_validator as model_validator
from pydantic import AfterValidator as AfterValidator
from pydantic import BeforeValidator as BeforeValidator
from pydantic import PlainValidator as PlainValidator
from pydantic import WrapValidator as WrapValidator
from pydantic import SkipValidation as SkipValidation
from pydantic import InstanceOf as InstanceOf
from pydantic import ModelWrapValidatorHandler as ModelWrapValidatorHandler

# JSON Schema
from pydantic import WithJsonSchema as WithJsonSchema

# functional serializers
from pydantic import field_serializer as field_serializer
from pydantic import model_serializer as model_serializer
from pydantic import PlainSerializer as PlainSerializer
from pydantic import SerializeAsAny as SerializeAsAny
from pydantic import WrapSerializer as WrapSerializer

# config
# from pydantic import ConfigDict as ConfigDict # We have our own version of this.
# from pydantic import with_config as with_config # We have our own version of this.

# validate_call
from pydantic import validate_call as validate_call

# errors
from pydantic import PydanticErrorCodes as PydanticErrorCodes
from pydantic import PydanticUserError as PydanticUserError
from pydantic import PydanticSchemaGenerationError as PydanticSchemaGenerationError
from pydantic import PydanticImportError as PydanticImportError
from pydantic import PydanticUndefinedAnnotation as PydanticUndefinedAnnotation
from pydantic import PydanticInvalidForJsonSchema as PydanticInvalidForJsonSchema
from pydantic import PydanticForbiddenQualifier as PydanticForbiddenQualifier

# fields
from pydantic import Field as Field
from pydantic import computed_field as computed_field
from pydantic import PrivateAttr as PrivateAttr

# alias
from pydantic import AliasChoices as AliasChoices
from pydantic import AliasGenerator as AliasGenerator
from pydantic import AliasPath as AliasPath

# main
# from pydantic import BaseModel as BaseModel # Don't need this, we use C.Config.
# from pydantic import create_model as create_model # Don't need this.

# network
from pydantic import AnyUrl as AnyUrl
from pydantic import AnyHttpUrl as AnyHttpUrl
from pydantic import FileUrl as FileUrl
from pydantic import HttpUrl as HttpUrl
from pydantic import FtpUrl as FtpUrl
from pydantic import WebsocketUrl as WebsocketUrl
from pydantic import AnyWebsocketUrl as AnyWebsocketUrl
from pydantic import UrlConstraints as UrlConstraints
from pydantic import EmailStr as EmailStr
from pydantic import NameEmail as NameEmail
from pydantic import IPvAnyAddress as IPvAnyAddress
from pydantic import IPvAnyInterface as IPvAnyInterface
from pydantic import IPvAnyNetwork as IPvAnyNetwork
from pydantic import PostgresDsn as PostgresDsn
from pydantic import CockroachDsn as CockroachDsn
from pydantic import AmqpDsn as AmqpDsn
from pydantic import RedisDsn as RedisDsn
from pydantic import MongoDsn as MongoDsn
from pydantic import KafkaDsn as KafkaDsn
from pydantic import NatsDsn as NatsDsn
from pydantic import MySQLDsn as MySQLDsn
from pydantic import MariaDBDsn as MariaDBDsn
from pydantic import ClickHouseDsn as ClickHouseDsn
from pydantic import SnowflakeDsn as SnowflakeDsn
from pydantic import validate_email as validate_email

# root_model
from pydantic import RootModel as RootModel

# types
from pydantic import Strict as Strict
from pydantic import StrictStr as StrictStr
from pydantic import conbytes as conbytes
from pydantic import conlist as conlist
from pydantic import conset as conset
from pydantic import confrozenset as confrozenset
from pydantic import constr as constr
from pydantic import StringConstraints as StringConstraints
from pydantic import ImportString as ImportString
from pydantic import conint as conint
from pydantic import PositiveInt as PositiveInt
from pydantic import NegativeInt as NegativeInt
from pydantic import NonNegativeInt as NonNegativeInt
from pydantic import NonPositiveInt as NonPositiveInt
from pydantic import confloat as confloat
from pydantic import PositiveFloat as PositiveFloat
from pydantic import NegativeFloat as NegativeFloat
from pydantic import NonNegativeFloat as NonNegativeFloat
from pydantic import NonPositiveFloat as NonPositiveFloat
from pydantic import FiniteFloat as FiniteFloat
from pydantic import condecimal as condecimal
from pydantic import condate as condate
from pydantic import UUID1 as UUID1
from pydantic import UUID3 as UUID3
from pydantic import UUID4 as UUID4
from pydantic import UUID5 as UUID5
from pydantic import UUID6 as UUID6
from pydantic import UUID7 as UUID7
from pydantic import UUID8 as UUID8
from pydantic import FilePath as FilePath
from pydantic import DirectoryPath as DirectoryPath
from pydantic import NewPath as NewPath
from pydantic import Json as Json
from pydantic import Secret as Secret
from pydantic import SecretStr as SecretStr
from pydantic import SecretBytes as SecretBytes
from pydantic import SocketPath as SocketPath
from pydantic import StrictBool as StrictBool
from pydantic import StrictBytes as StrictBytes
from pydantic import StrictInt as StrictInt
from pydantic import StrictFloat as StrictFloat
from pydantic import ByteSize as ByteSize
from pydantic import PastDate as PastDate
from pydantic import FutureDate as FutureDate
from pydantic import PastDatetime as PastDatetime
from pydantic import FutureDatetime as FutureDatetime
from pydantic import AwareDatetime as AwareDatetime
from pydantic import NaiveDatetime as NaiveDatetime
from pydantic import AllowInfNan as AllowInfNan
from pydantic import EncoderProtocol as EncoderProtocol
from pydantic import EncodedBytes as EncodedBytes
from pydantic import EncodedStr as EncodedStr
from pydantic import Base64Encoder as Base64Encoder
from pydantic import Base64Bytes as Base64Bytes
from pydantic import Base64Str as Base64Str
from pydantic import Base64UrlBytes as Base64UrlBytes
from pydantic import Base64UrlStr as Base64UrlStr
from pydantic import GetPydanticSchema as GetPydanticSchema
from pydantic import Tag as Tag
from pydantic import Discriminator as Discriminator
from pydantic import JsonValue as JsonValue
from pydantic import FailFast as FailFast

# type_adapter
# from pydantic import TypeAdapter as TypeAdapter # We have our own version of this.

# warnings
from pydantic import PydanticDeprecatedSince20 as PydanticDeprecatedSince20
from pydantic import PydanticDeprecatedSince26 as PydanticDeprecatedSince26
from pydantic import PydanticDeprecatedSince29 as PydanticDeprecatedSince29
from pydantic import PydanticDeprecatedSince210 as PydanticDeprecatedSince210
from pydantic import PydanticDeprecatedSince211 as PydanticDeprecatedSince211
from pydantic import PydanticDeprecationWarning as PydanticDeprecationWarning
from pydantic import PydanticExperimentalWarning as PydanticExperimentalWarning

# annotated handlers
from pydantic import GetCoreSchemaHandler as GetCoreSchemaHandler
from pydantic import GetJsonSchemaHandler as GetJsonSchemaHandler

# pydantic_core
from pydantic import ValidationError as ValidationError
from pydantic import ValidationInfo as ValidationInfo
from pydantic import SerializationInfo as SerializationInfo
from pydantic import ValidatorFunctionWrapHandler as ValidatorFunctionWrapHandler
from pydantic import FieldSerializationInfo as FieldSerializationInfo
from pydantic import SerializerFunctionWrapHandler as SerializerFunctionWrapHandler
from pydantic import OnErrorOmit as OnErrorOmit
# endregion

# region Pydantic Settings Exports
# full module export
import pydantic_settings as pydantic_settings

from pydantic_settings import CLI_SUPPRESS as CLI_SUPPRESS
from pydantic_settings import (
    AWSSecretsManagerSettingsSource as AWSSecretsManagerSettingsSource,
)
from pydantic_settings import AzureKeyVaultSettingsSource as AzureKeyVaultSettingsSource

# from pydantic_settings import BaseSettings as BaseSettings # We have our own version of this (RootConfig).
# from pydantic_settings import CliApp as CliApp # We have our own version of this.
from pydantic_settings import CliExplicitFlag as CliExplicitFlag
from pydantic_settings import CliImplicitFlag as CliImplicitFlag
from pydantic_settings import CliMutuallyExclusiveGroup as CliMutuallyExclusiveGroup
from pydantic_settings import CliPositionalArg as CliPositionalArg
from pydantic_settings import CliSettingsSource as CliSettingsSource
from pydantic_settings import CliSubCommand as CliSubCommand
from pydantic_settings import CliSuppress as CliSuppress
from pydantic_settings import CliUnknownArgs as CliUnknownArgs
from pydantic_settings import DotEnvSettingsSource as DotEnvSettingsSource
from pydantic_settings import EnvSettingsSource as EnvSettingsSource
from pydantic_settings import ForceDecode as ForceDecode
from pydantic_settings import (
    GoogleSecretManagerSettingsSource as GoogleSecretManagerSettingsSource,
)
from pydantic_settings import InitSettingsSource as InitSettingsSource
from pydantic_settings import JsonConfigSettingsSource as JsonConfigSettingsSource
from pydantic_settings import NoDecode as NoDecode
from pydantic_settings import PydanticBaseSettingsSource as PydanticBaseSettingsSource
from pydantic_settings import (
    PyprojectTomlConfigSettingsSource as PyprojectTomlConfigSettingsSource,
)
from pydantic_settings import SecretsSettingsSource as SecretsSettingsSource
from pydantic_settings import SettingsConfigDict as SettingsConfigDict
from pydantic_settings import SettingsError as SettingsError
from pydantic_settings import TomlConfigSettingsSource as TomlConfigSettingsSource
from pydantic_settings import YamlConfigSettingsSource as YamlConfigSettingsSource
from pydantic_settings import get_subcommand as get_subcommand
# endregion
