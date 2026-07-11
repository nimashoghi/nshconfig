"""Deterministic content fingerprints and inert JSON run records."""

import hmac
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass, replace
from hashlib import sha256
from typing import (
    Annotated,
    Any,
    Literal,
    TypeAlias,
    TypeVar,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from pydantic import (
    AliasChoices,
    AliasPath,
    BaseModel,
    PydanticInvalidForJsonSchema,
    SecretBytes,
    SecretStr,
    ValidationError,
)
from pydantic.fields import FieldInfo

from .config import Config
from .errors import DraftError, FingerprintError, RecordError
from .provenance import (
    _parse_path,
    Event,
    canonicalize_path,
    event_from_dict,
    event_to_dict,
    explain,
    provenance_token,
    provenance_value,
    provenance,
    restore_provenance,
    safe_repr,
    safe_exception_text,
)
from .semantic import semantic_snapshot, stable_semantic_digest
from .scope import _record_load_scope
from .state import FinalState, RESERVED_PRIVATE_KEYS, is_draft, state_of

__all__ = ["RunRecord", "fingerprint", "load_record", "record"]

RUN_RECORD_FORMAT = "nshconfig.run-record.v4"
SCHEMA_FORMAT = "nshconfig.config-schema.v2"
_FINGERPRINT_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SCHEMA_PRESENTATION_KEYS = frozenset(
    {"$comment", "deprecated", "description", "examples", "title"}
)
_SCHEMA_NAME_MAP_KEYS = frozenset(
    {"$defs", "definitions", "dependentSchemas", "patternProperties", "properties"}
)
_SCHEMA_SINGLE_KEYS = frozenset(
    {
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)
_SCHEMA_ARRAY_KEYS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_TYPED_DICT_NON_SEMANTIC_CONFIG_KEYS = frozenset(
    {
        "cache_strings",
        "hide_input_in_errors",
        "loc_by_alias",
        "title",
        "validation_error_cause",
    }
)

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
C = TypeVar("C", bound=Config)


@dataclass(frozen=True, slots=True, init=False)
class RunRecord:
    """An immutable run-record envelope backed by canonical JSON.

    ``values`` and ``provenance`` return fresh JSON objects, so mutating a
    returned value cannot mutate the record. Use :meth:`to_dict` with ordinary
    JSON libraries, or :meth:`to_json` for the canonical representation.
    """

    format: str
    config_type: str
    schema_fingerprint: str
    semantic_fingerprint: str
    fingerprint: str
    _values_json: str = field(repr=False)
    _provenance_json: str = field(repr=False)

    def __init__(
        self,
        *,
        format: str,
        config_type: str,
        schema_fingerprint: str,
        semantic_fingerprint: str,
        fingerprint: str,
        values: Mapping[str, Any],
        provenance: Mapping[str, Any],
    ) -> None:
        if not isinstance(format, str):
            raise RecordError("run-record format must be a string")
        if not isinstance(config_type, str):
            raise RecordError("run-record config_type must be a string")
        if not isinstance(schema_fingerprint, str):
            raise RecordError("run-record schema_fingerprint must be a string")
        if not isinstance(semantic_fingerprint, str):
            raise RecordError("run-record semantic_fingerprint must be a string")
        if not isinstance(fingerprint, str):
            raise RecordError("run-record fingerprint must be a string")
        if format != RUN_RECORD_FORMAT:
            raise RecordError(
                f"unsupported run-record format {format!r}; expected {RUN_RECORD_FORMAT!r}"
            )
        module, separator, qualname_and_id = config_type.partition(":")
        if not separator or not module or not qualname_and_id:
            raise RecordError(
                "run-record config_type must use the non-empty 'module:qualname' form"
            )
        for name, digest in (
            ("schema_fingerprint", schema_fingerprint),
            ("semantic_fingerprint", semantic_fingerprint),
            ("fingerprint", fingerprint),
        ):
            if _FINGERPRINT_PATTERN.fullmatch(digest) is None:
                raise RecordError(
                    f"run-record {name} must be 'sha256:' followed by 64 lowercase hex digits"
                )
        canonical_values = _json_object(values, "$.values", RecordError)
        canonical_provenance = _json_object(provenance, "$.provenance", RecordError)
        object.__setattr__(self, "format", format)
        object.__setattr__(self, "config_type", config_type)
        object.__setattr__(self, "schema_fingerprint", schema_fingerprint)
        object.__setattr__(self, "semantic_fingerprint", semantic_fingerprint)
        object.__setattr__(self, "fingerprint", fingerprint)
        object.__setattr__(self, "_values_json", _encode_json(canonical_values))
        object.__setattr__(self, "_provenance_json", _encode_json(canonical_provenance))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunRecord":
        """Validate a JSON run-record mapping and freeze it."""
        if not isinstance(value, Mapping):
            raise RecordError("a run record must be a mapping")
        if not all(isinstance(key, str) for key in value):
            raise RecordError("run-record envelope keys must be strings")
        expected = {
            "format",
            "config_type",
            "schema_fingerprint",
            "semantic_fingerprint",
            "fingerprint",
            "values",
            "provenance",
        }
        unknown = set(value) - expected
        missing = expected - set(value)
        if unknown or missing:
            details: list[str] = []
            if missing:
                details.append(f"missing keys {sorted(missing)!r}")
            if unknown:
                details.append(f"unknown keys {sorted(unknown)!r}")
            raise RecordError(f"invalid run-record envelope: {', '.join(details)}")
        values = value["values"]
        record_provenance = value["provenance"]
        if not isinstance(values, Mapping):
            raise RecordError("run-record values must be a JSON object")
        if not isinstance(record_provenance, Mapping):
            raise RecordError("run-record provenance must be a JSON object")
        return cls(
            format=value["format"],
            config_type=value["config_type"],
            schema_fingerprint=value["schema_fingerprint"],
            semantic_fingerprint=value["semantic_fingerprint"],
            fingerprint=value["fingerprint"],
            values=values,
            provenance=record_provenance,
        )

    @property
    def values(self) -> dict[str, JsonValue]:
        """Return a detached copy of the recorded canonical field values."""
        value = json.loads(self._values_json)
        assert isinstance(value, dict)
        return cast(dict[str, JsonValue], value)

    @property
    def provenance(self) -> dict[str, JsonValue]:
        """Return a detached copy of the recorded provenance data."""
        value = json.loads(self._provenance_json)
        assert isinstance(value, dict)
        return cast(dict[str, JsonValue], value)

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a detached envelope accepted directly by ``json.dumps``."""
        return {
            "format": self.format,
            "config_type": self.config_type,
            "schema_fingerprint": self.schema_fingerprint,
            "semantic_fingerprint": self.semantic_fingerprint,
            "fingerprint": self.fingerprint,
            "values": self.values,
            "provenance": self.provenance,
        }

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize the envelope as deterministic JSON."""
        if indent is None:
            return _encode_json(self.to_dict())
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=True,
            indent=indent,
            sort_keys=False,
        )


def fingerprint(config: Config) -> str:
    """Return a deterministic, type-aware SHA-256 identifier for a final."""
    _require_final(config, "fingerprint")
    _, _, _, digest = _checked_fingerprint_parts(config)
    return digest


def record(config: Config) -> RunRecord:
    """Capture a final's values and provenance as inert JSON data."""
    _require_final(config, "record")
    before = _record_snapshot(config)
    (
        values,
        schema_fingerprint,
        semantic_fingerprint,
        record_fingerprint,
    ) = _checked_fingerprint_parts(config)
    config_type = _config_type(type(config))
    history = provenance(config)
    try:
        history = _validate_provenance_references(config, history)
    except RecordError as error:
        raise FingerprintError(
            f"{type(config).__qualname__} provenance is not self-contained under the "
            "recorded Config root"
        ) from error
    event_data = {
        path: [event_to_dict(event) for event in events]
        for path, events in history.items()
    }
    output = RunRecord(
        format=RUN_RECORD_FORMAT,
        config_type=config_type,
        schema_fingerprint=schema_fingerprint,
        semantic_fingerprint=semantic_fingerprint,
        fingerprint=record_fingerprint,
        values=values,
        provenance=event_data,
    )
    if _record_snapshot(config) != before:
        raise FingerprintError(
            "the Config final changed while its values and provenance were recorded"
        )
    return output


def load_record(cls: type[C], value: RunRecord | Mapping[str, Any]) -> C:
    """Validate and restore a final from an inert run record.

    Every declared field is present in a record, so validation consumes only
    concrete recorded values and never needs to evaluate interpolation defaults.
    """
    if not isinstance(cls, type) or not issubclass(cls, Config):
        raise TypeError("load_record() expects a Config subclass")
    run_record = value if isinstance(value, RunRecord) else RunRecord.from_dict(value)
    if run_record.format != RUN_RECORD_FORMAT:
        raise RecordError(
            f"unsupported run-record format {run_record.format!r}; expected {RUN_RECORD_FORMAT!r}"
        )

    try:
        config_type = _config_type(cls)
    except FingerprintError as error:
        raise RecordError(
            "requested Config type has no stable record identity"
        ) from error
    if run_record.config_type != config_type:
        raise RecordError(
            f"run record contains {run_record.config_type!r}, not requested type {config_type!r}"
        )
    if _FINGERPRINT_PATTERN.fullmatch(run_record.schema_fingerprint) is None:
        raise RecordError(
            "run-record schema_fingerprint must be 'sha256:' followed by 64 lowercase "
            "hex digits"
        )
    if _FINGERPRINT_PATTERN.fullmatch(run_record.semantic_fingerprint) is None:
        raise RecordError(
            "run-record semantic_fingerprint must be 'sha256:' followed by 64 lowercase "
            "hex digits"
        )
    if _FINGERPRINT_PATTERN.fullmatch(run_record.fingerprint) is None:
        raise RecordError(
            "run-record fingerprint must be 'sha256:' followed by 64 lowercase hex digits"
        )
    try:
        expected_schema_fingerprint = _schema_fingerprint(cls)
    except FingerprintError as error:
        raise RecordError(
            f"could not derive a deterministic JSON schema for {cls.__qualname__}"
        ) from error
    if not hmac.compare_digest(
        run_record.schema_fingerprint,
        expected_schema_fingerprint,
    ):
        raise RecordError(
            "run-record schema fingerprint does not match the requested Config schema"
        )

    values = run_record.values
    declared_fields = set(cls.__pydantic_fields__)
    provided_fields = set(values)
    if provided_fields != declared_fields:
        missing = declared_fields - provided_fields
        unknown = provided_fields - declared_fields
        details: list[str] = []
        if missing:
            details.append(f"missing fields {sorted(missing)!r}")
        if unknown:
            details.append(f"unknown fields {sorted(unknown)!r}")
        raise RecordError(
            "run-record values must contain every declared field exactly once: "
            + ", ".join(details)
        )
    expected_fingerprint = _fingerprint_values(
        config_type,
        expected_schema_fingerprint,
        run_record.semantic_fingerprint,
        values,
    )
    if not hmac.compare_digest(run_record.fingerprint, expected_fingerprint):
        raise RecordError(
            "run-record fingerprint does not match its concrete type and values"
        )
    restored_events = _decode_provenance(run_record.provenance)

    try:
        # JSON arrays are the wire representation of tuples, sets, and
        # frozensets. The field interpolation wrapper receives those arrays as
        # Python lists before the inner strict validator, so this boundary uses
        # lax parsing and then requires an exact fingerprint round trip below.
        # Any coercion that changes canonical values is therefore rejected.
        output = _validate_record_values(cls, values)
    except ValidationError as error:
        if any(
            item["type"] == "nshconfig_record_interpolation" for item in error.errors()
        ):
            raise RecordError(
                "run-record validation attempted to execute interpolation"
            ) from error
        ignored = [
            field
            for item in error.errors()
            if item["type"] == "nshconfig_ignored_input"
            for field in str(item.get("ctx", {}).get("fields", "")).split(", ")
            if field
        ]
        if ignored:
            raise RecordError(
                "run-record validation substituted defaults instead of consuming stored "
                f"fields: {', '.join(ignored)}"
            ) from error
        raise RecordError(
            f"run-record values do not validate as {cls.__qualname__}"
        ) from error
    except Exception as error:
        raise RecordError(
            f"could not load run-record values as {cls.__qualname__}"
        ) from error
    if type(output) is not cls:
        raise RecordError(
            f"validation returned {type(output).__qualname__}, expected exactly {cls.__qualname__}"
        )

    try:
        (
            round_trip_values,
            round_trip_schema_fingerprint,
            round_trip_semantic_fingerprint,
            round_trip_fingerprint,
        ) = _fingerprint_parts(output)
    except (DraftError, FingerprintError) as error:
        raise RecordError(
            "loaded values cannot be represented by the run-record format"
        ) from error
    if not hmac.compare_digest(
        round_trip_semantic_fingerprint,
        run_record.semantic_fingerprint,
    ):
        raise RecordError(
            "run-record values reconstructed different exact runtime semantics"
        )
    if not hmac.compare_digest(round_trip_fingerprint, run_record.fingerprint):
        raise RecordError(
            "run-record values changed during validation and do not round-trip"
        )
    if not hmac.compare_digest(
        round_trip_schema_fingerprint,
        run_record.schema_fingerprint,
    ):
        raise RecordError("Config schema changed while loading the run record")
    if round_trip_values != values:
        raise RecordError(
            "run-record values changed during validation and do not round-trip"
        )
    try:
        _assert_record_fields_consumed(output, "$", set())
    except FingerprintError as error:
        raise RecordError(safe_exception_text(error)) from error
    restored_events = _validate_provenance_references(output, restored_events)

    try:
        restore_provenance(output, restored_events)
    except Exception as error:
        raise RecordError(
            "run-record provenance does not match the restored config graph"
        ) from error
    return output


def _validate_record_values(cls: type[C], values: Mapping[str, Any]) -> C:
    with _record_load_scope():
        return cls.model_validate_json(
            _encode_json(values),
            strict=False,
            by_alias=True,
            by_name=True,
        )


def _fingerprint_parts(
    config: Config,
) -> tuple[dict[str, JsonValue], str, str, str]:
    values = _model_values(config)
    schema_fingerprint = _schema_fingerprint(type(config))
    semantic_fingerprint = _semantic_fingerprint(config)
    digest = _fingerprint_values(
        _config_type(type(config)),
        schema_fingerprint,
        semantic_fingerprint,
        values,
    )
    return values, schema_fingerprint, semantic_fingerprint, digest


def _checked_fingerprint_parts(
    config: Config,
) -> tuple[dict[str, JsonValue], str, str, str]:
    values, schema_fingerprint, semantic_fingerprint, digest = _fingerprint_parts(
        config
    )
    _verify_record_round_trip(
        config,
        values,
        schema_fingerprint,
        semantic_fingerprint,
        digest,
    )
    return values, schema_fingerprint, semantic_fingerprint, digest


def _verify_record_round_trip(
    config: Config,
    values: Mapping[str, Any],
    expected_schema_fingerprint: str,
    expected_semantic_fingerprint: str,
    expected_fingerprint: str,
) -> None:
    cls = type(config)
    try:
        candidate = _validate_record_values(cls, values)
    except ValidationError as error:
        ignored = [
            field
            for item in error.errors()
            if item["type"] == "nshconfig_ignored_input"
            for field in str(item.get("ctx", {}).get("fields", "")).split(", ")
            if field
        ]
        if ignored:
            raise FingerprintError(
                f"{cls.__qualname__} record validation substituted defaults instead of "
                f"consuming stored fields: {', '.join(ignored)}"
            ) from error
        raise FingerprintError(
            f"{cls.__qualname__} JSON values cannot reconstruct the recorded final"
        ) from error
    except Exception as error:
        raise FingerprintError(
            f"{cls.__qualname__} JSON values cannot reconstruct the recorded final"
        ) from error
    if type(candidate) is not cls:
        raise FingerprintError(
            f"{cls.__qualname__} validation returned {type(candidate).__qualname__}"
        )
    _assert_record_fields_consumed(candidate, "$", set())
    if _record_snapshot(candidate) != _record_snapshot(config):
        raise FingerprintError(
            f"{cls.__qualname__} JSON values reconstruct a different final and do not "
            "preserve exact runtime semantics"
        )
    try:
        candidate_values = _model_values(candidate)
        candidate_schema_fingerprint = _schema_fingerprint(cls)
        candidate_semantic_fingerprint = _semantic_fingerprint(candidate)
        candidate_fingerprint = _fingerprint_values(
            _config_type(cls),
            candidate_schema_fingerprint,
            candidate_semantic_fingerprint,
            candidate_values,
        )
    except FingerprintError as error:
        raise FingerprintError(
            f"{cls.__qualname__} JSON reconstruction is not deterministically serializable"
        ) from error
    if not hmac.compare_digest(
        candidate_schema_fingerprint,
        expected_schema_fingerprint,
    ):
        raise FingerprintError(f"{cls.__qualname__} JSON schema is not deterministic")
    if not hmac.compare_digest(
        candidate_semantic_fingerprint,
        expected_semantic_fingerprint,
    ):
        raise FingerprintError(
            f"{cls.__qualname__} JSON values reconstruct different exact runtime "
            "semantics"
        )
    if not hmac.compare_digest(candidate_fingerprint, expected_fingerprint):
        raise FingerprintError(
            f"{cls.__qualname__} JSON values are not stable across validation"
        )


def _require_final(config: Any, operation: str) -> None:
    if not isinstance(config, Config):
        raise TypeError(f"{operation}() expects a Config instance")
    if not isinstance(state_of(config), FinalState):
        raise DraftError(
            f"{operation}() requires validation to have completed and produced a final"
        )


def _record_snapshot(config: Config) -> Any:
    try:
        return semantic_snapshot(config)
    except Exception as error:
        raise FingerprintError(
            f"{type(config).__qualname__} runtime state cannot be inspected safely"
        ) from error


def _config_type(cls: type[Config]) -> str:
    generic = getattr(cls, "__pydantic_generic_metadata__", {})
    generic_arguments = generic.get("args", ()) if isinstance(generic, Mapping) else ()
    declared_id = cls.__dict__.get("record_schema_id")
    if generic_arguments and declared_id is None:
        raise FingerprintError(
            f"generic specialization {cls.__qualname__} needs an explicit stable "
            "record_schema_id; prefer a named concrete subclass"
        )
    schema_id = getattr(cls, "record_schema_id", None)
    if schema_id is not None and (not isinstance(schema_id, str) or not schema_id):
        raise FingerprintError(
            f"{cls.__qualname__}.record_schema_id must be a non-empty string or None"
        )
    identity = f"{cls.__module__}:{cls.__qualname__}"
    return identity if schema_id is None else f"{identity}#{schema_id}"


def _schema_fingerprint(cls: type[Config]) -> str:
    first = _schema_payload(cls)
    second = _schema_payload(cls)
    if first != second:
        raise FingerprintError(
            f"{cls.__qualname__} produced different JSON schemas on repeated calls"
        )
    digest = sha256(_encode_json(first).encode("ascii")).hexdigest()
    return f"sha256:{digest}"


def _schema_payload(cls: type[Config]) -> dict[str, JsonValue]:
    return {
        "format": SCHEMA_FORMAT,
        "config_type": _config_type(cls),
        "canonical_validation_schema": _json_schema_contract(
            cls, "validation", by_alias=False
        ),
        "canonical_serialization_schema": _json_schema_contract(
            cls, "serialization", by_alias=False
        ),
        "alias_validation_schema": _json_schema_contract(
            cls, "validation", by_alias=True
        ),
        "alias_serialization_schema": _json_schema_contract(
            cls, "serialization", by_alias=True
        ),
        "type_manifest": _schema_type_manifest(cls),
    }


def _json_schema_contract(
    cls: type[Config],
    mode: Literal["validation", "serialization"],
    *,
    by_alias: bool,
) -> dict[str, JsonValue]:
    try:
        schema = cls.model_json_schema(by_alias=by_alias, mode=mode)
        namespace = "alias" if by_alias else "canonical"
        return _json_object(
            _strip_schema_presentation(schema),
            f"$.schema.{namespace}.{mode}",
            FingerprintError,
        )
    except PydanticInvalidForJsonSchema as error:
        raise FingerprintError(
            f"{cls.__qualname__} has no deterministic {mode} JSON schema"
        ) from error
    except FingerprintError:
        raise
    except Exception as error:
        raise FingerprintError(
            f"{cls.__qualname__} could not produce its {mode} JSON schema"
        ) from error


def _schema_type_manifest(cls: type[Config]) -> dict[str, JsonValue]:
    """Capture compiled type semantics omitted by JSON Schema."""

    models: list[JsonValue] = []
    for model_type in _reachable_schema_types(cls):
        model_fields = getattr(model_type, "__pydantic_fields__", None)
        if not isinstance(model_fields, Mapping):
            continue
        aliases: dict[str, JsonValue] = {}
        for name, field_info in model_fields.items():
            aliases[name] = {
                "alias": _alias_descriptor(field_info.alias),
                "validation_alias": _alias_descriptor(field_info.validation_alias),
                "serialization_alias": _alias_descriptor(
                    field_info.serialization_alias
                ),
                "alias_priority": field_info.alias_priority,
            }
        is_config = issubclass(model_type, Config)
        models.append(
            {
                "type": f"{model_type.__module__}:{model_type.__qualname__}",
                "config_type": _config_type(model_type) if is_config else None,
                "aliases": aliases,
            }
        )
    return {
        "models": models,
        "typed_dicts": _typed_dict_schema_manifest(cls),
    }


def _typed_dict_schema_manifest(cls: type[Config]) -> list[JsonValue]:
    """Extract durable TypedDict semantics from Pydantic's compiled schema."""

    output: list[JsonValue] = []
    seen_nodes: set[int] = set()

    def visit(value: Any) -> None:
        if type(value) is dict:
            identity = id(value)
            if identity in seen_nodes:
                return
            seen_nodes.add(identity)
            if value.get("type") == "typed-dict":
                output.append(_typed_dict_schema_descriptor(value))
            for key in sorted(value):
                visit(value[key])
            return
        if type(value) in {list, tuple}:
            identity = id(value)
            if identity in seen_nodes:
                return
            seen_nodes.add(identity)
            for item in value:
                visit(item)

    visit(cls.__pydantic_core_schema__)
    output.sort(key=_encode_json)
    return output


def _typed_dict_schema_descriptor(schema: Mapping[str, Any]) -> dict[str, JsonValue]:
    raw_type = schema.get("cls")
    if raw_type is not None and not isinstance(raw_type, type):
        raise FingerprintError("compiled TypedDict schema has an invalid runtime type")
    if raw_type is None:
        raw_name = schema.get("cls_name")
        if raw_name is not None and not isinstance(raw_name, str):
            raise FingerprintError("compiled TypedDict schema has an invalid type name")
        type_name = raw_name
    else:
        type_name = f"{raw_type.__module__}:{raw_type.__qualname__}"

    raw_fields = schema.get("fields")
    if not isinstance(raw_fields, Mapping):
        raise FingerprintError("compiled TypedDict schema has no field mapping")
    field_descriptors: dict[str, JsonValue] = {}
    for name in sorted(raw_fields):
        if not isinstance(name, str):
            raise FingerprintError("compiled TypedDict field names must be strings")
        raw_field = raw_fields[name]
        if (
            not isinstance(raw_field, Mapping)
            or raw_field.get("type") != "typed-dict-field"
        ):
            raise FingerprintError(
                f"compiled TypedDict field {name!r} has an invalid schema"
            )
        required = raw_field.get("required")
        if not isinstance(required, bool):
            raise FingerprintError(
                f"compiled TypedDict field {name!r} has invalid required semantics"
            )
        serialization_alias = raw_field.get("serialization_alias")
        if serialization_alias is not None and not isinstance(serialization_alias, str):
            raise FingerprintError(
                f"compiled TypedDict field {name!r} has an invalid serialization alias"
            )
        serialization_exclude = raw_field.get("serialization_exclude", False)
        if not isinstance(serialization_exclude, bool):
            raise FingerprintError(
                f"compiled TypedDict field {name!r} has invalid exclusion semantics"
            )
        if raw_field.get("serialization_exclude_if") is not None:
            raise FingerprintError(
                "run records do not support conditional TypedDict field exclusion: "
                f"{type_name or '<anonymous>'}.{name}"
            )
        field_descriptors[name] = {
            "required": required,
            "validation_alias": _compiled_alias_descriptor(
                raw_field.get("validation_alias")
            ),
            "serialization_alias": serialization_alias,
            "serialization_exclude": serialization_exclude,
        }

    raw_config = schema.get("config", {})
    if not isinstance(raw_config, Mapping):
        raise FingerprintError("compiled TypedDict schema has an invalid core config")
    config = _json_object(
        {
            key: raw_config[key]
            for key in sorted(raw_config)
            if key not in _TYPED_DICT_NON_SEMANTIC_CONFIG_KEYS
        },
        "$.schema.type_manifest.typed_dict.config",
        FingerprintError,
    )

    extra_behavior = schema.get("extra_behavior")
    if extra_behavior not in {None, "allow", "forbid", "ignore"}:
        raise FingerprintError(
            "compiled TypedDict schema has invalid extra-field semantics"
        )
    strict = schema.get("strict")
    if strict is not None and not isinstance(strict, bool):
        raise FingerprintError(
            "compiled TypedDict schema has invalid strictness semantics"
        )
    total = schema.get("total")
    if total is not None and not isinstance(total, bool):
        raise FingerprintError(
            "compiled TypedDict schema has invalid totality semantics"
        )
    return {
        "type": type_name,
        "fields": field_descriptors,
        "config": config,
        "extra_behavior": extra_behavior,
        "strict": strict,
        "total": total,
    }


def _compiled_alias_descriptor(value: Any) -> JsonValue:
    """Canonicalize pydantic-core's compiled alias representation."""

    if value is None:
        return None
    if isinstance(value, str):
        return {"kind": "name", "value": value}
    if not isinstance(value, list) or not value:
        raise FingerprintError(
            "compiled validation aliases must be names or non-empty paths"
        )
    if all(isinstance(path, list) for path in value):
        return {
            "kind": "choices",
            "choices": [_compiled_alias_path(path) for path in value],
        }
    return _compiled_alias_path(value)


def _compiled_alias_path(value: list[Any]) -> dict[str, JsonValue]:
    path: list[JsonValue] = []
    if not value:
        raise FingerprintError("compiled validation alias paths may not be empty")
    for segment in value:
        if not isinstance(segment, (str, int)) or isinstance(segment, bool):
            raise FingerprintError(
                "compiled validation alias paths must contain only strings and integers"
            )
        path.append(segment)
    return {"kind": "path", "path": path}


def _reachable_schema_types(cls: type[Config]) -> tuple[type[Any], ...]:
    """Find Pydantic model/dataclass types in the compiled schema, cycle-safely."""

    output: list[type[Any]] = [cls]
    seen_types = {id(cls)}
    seen_nodes: set[int] = set()

    def visit(value: Any) -> None:
        if type(value) is dict:
            identity = id(value)
            if identity in seen_nodes:
                return
            seen_nodes.add(identity)
            candidate = value.get("cls")
            if (
                isinstance(candidate, type)
                and isinstance(getattr(candidate, "__pydantic_fields__", None), Mapping)
                and id(candidate) not in seen_types
            ):
                seen_types.add(id(candidate))
                output.append(candidate)
            for key in sorted(value):
                visit(value[key])
            return
        if type(value) in {list, tuple}:
            identity = id(value)
            if identity in seen_nodes:
                return
            seen_nodes.add(identity)
            for item in value:
                visit(item)

    visit(cls.__pydantic_core_schema__)
    return tuple(output)


def _alias_descriptor(value: Any) -> JsonValue:
    if value is None:
        return None
    if isinstance(value, str):
        return {"kind": "name", "value": value}
    if isinstance(value, AliasPath):
        path: list[JsonValue] = []
        for segment in value.path:
            if not isinstance(segment, (str, int)) or isinstance(segment, bool):
                raise FingerprintError(
                    "validation alias paths must contain only strings and integers"
                )
            path.append(segment)
        return {"kind": "path", "path": path}
    if isinstance(value, AliasChoices):
        return {
            "kind": "choices",
            "choices": [_alias_descriptor(choice) for choice in value.choices],
        }
    raise FingerprintError(
        f"unsupported validation alias descriptor {type(value).__qualname__}"
    )


def _strip_schema_presentation(value: Any) -> Any:
    """Strip presentation keywords only where a mapping is a schema object."""
    if not isinstance(value, Mapping):
        return _sort_json_value(value)
    output: dict[str, Any] = {}
    for key in sorted(value):
        if key in _SCHEMA_PRESENTATION_KEYS:
            continue
        child = value[key]
        if key in _SCHEMA_NAME_MAP_KEYS and isinstance(child, Mapping):
            output[key] = {
                name: _strip_schema_presentation(child[name]) for name in sorted(child)
            }
        elif key in _SCHEMA_SINGLE_KEYS and isinstance(child, Mapping):
            output[key] = _strip_schema_presentation(child)
        elif key in _SCHEMA_ARRAY_KEYS and isinstance(child, list):
            output[key] = [_strip_schema_presentation(item) for item in child]
        else:
            output[key] = _sort_json_value(child)
    return output


def _sort_json_value(value: Any) -> Any:
    """Canonicalize JSON object order without interpreting data as schema syntax."""
    if isinstance(value, Mapping):
        return {key: _sort_json_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sort_json_value(item) for item in value]
    return value


def _fingerprint_values(
    config_type: str,
    schema_fingerprint: str,
    semantic_fingerprint: str,
    values: Mapping[str, Any],
) -> str:
    payload = {
        "format": RUN_RECORD_FORMAT,
        "config_type": config_type,
        "schema_fingerprint": schema_fingerprint,
        "semantic_fingerprint": semantic_fingerprint,
        "values": values,
    }
    digest = sha256(_encode_json(payload).encode("ascii")).hexdigest()
    return f"sha256:{digest}"


def _semantic_fingerprint(config: Config) -> str:
    """Return the durable token for the exact validated runtime graph."""

    try:
        digest = stable_semantic_digest(config)
    except Exception as error:
        raise FingerprintError(
            f"{type(config).__qualname__} runtime state cannot be represented "
            "deterministically"
        ) from error
    if digest is None:
        raise FingerprintError(
            f"{type(config).__qualname__} contains runtime state without a durable "
            "semantic representation"
        )
    return digest


def _model_values(config: Config) -> dict[str, JsonValue]:
    before = _record_snapshot(config)
    _inspect_source(config, "$", set())
    try:
        first_dump = _dump_model(config)
        second_dump = _dump_model(config)
    except FingerprintError:
        raise
    except Exception as error:
        raise FingerprintError(
            f"{type(config).__qualname__} cannot be serialized deterministically"
        ) from error

    try:
        first = _json_object(
            _stabilize_dump(
                config,
                first_dump,
                "$",
                set(),
            ),
            "$",
            FingerprintError,
        )
        second = _json_object(
            _stabilize_dump(
                config,
                second_dump,
                "$",
                set(),
            ),
            "$",
            FingerprintError,
        )
        if _record_snapshot(config) != before:
            raise FingerprintError("JSON serialization mutated the live Config final")
        if first != second:
            raise FingerprintError(
                "JSON serialization produced different values on repeated calls"
            )
        return first
    except FingerprintError:
        raise
    except Exception as error:
        raise FingerprintError(
            f"{type(config).__qualname__} cannot be serialized deterministically"
        ) from error


def _assert_record_fields_consumed(value: Any, path: str, active: set[int]) -> None:
    """Require JSON validation to consume every serialized model field explicitly."""

    if isinstance(value, BaseModel):
        identity = id(value)
        if identity in active:
            return
        active.add(identity)
        try:
            names = set(type(value).__pydantic_fields__)
            consumed = set(object.__getattribute__(value, "__pydantic_fields_set__"))
            if consumed != names:
                missing = sorted(names - consumed)
                raise FingerprintError(
                    f"record validation substituted defaults instead of consuming fields at "
                    f"{path}: {missing!r}"
                )
            data = object.__getattribute__(value, "__dict__")
            for name in type(value).__pydantic_fields__:
                if name in data:
                    _assert_record_fields_consumed(data[name], f"{path}.{name}", active)
            extra = object.__getattribute__(value, "__pydantic_extra__") or {}
            for name, item in extra.items():
                _assert_record_fields_consumed(item, f"{path}[{name!r}]", active)
        finally:
            active.remove(identity)
        return
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            return
        active.add(identity)
        try:
            for key, item in value.items():
                _assert_record_fields_consumed(key, f"{path}.<key>", active)
                _assert_record_fields_consumed(item, f"{path}[{key!r}]", active)
        finally:
            active.remove(identity)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        identity = id(value)
        if identity in active:
            return
        active.add(identity)
        try:
            for index, item in enumerate(value):
                _assert_record_fields_consumed(item, f"{path}[{index}]", active)
        finally:
            active.remove(identity)


def _validate_provenance_references(
    config: Config, values: Mapping[str, tuple[Event, ...]]
) -> dict[str, tuple[Event, ...]]:
    """Reject event graphs that are not meaningful relative to this record root."""

    normalized: dict[str, tuple[Event, ...]] = {}
    for owner_path, events in values.items():
        try:
            canonical_owner = canonicalize_path(owner_path)
        except (TypeError, ValueError) as error:
            raise RecordError(f"invalid provenance path {owner_path!r}") from error
        if canonical_owner != owner_path:
            raise RecordError(
                f"provenance path {owner_path!r} is not canonical; use {canonical_owner!r}"
            )
        try:
            explain(config, owner_path)
            owner_value = provenance_value(config, owner_path)
        except (AttributeError, KeyError, IndexError, TypeError, ValueError) as error:
            raise RecordError(
                f"provenance path {owner_path!r} does not exist in the recorded Config"
            ) from error
        updated: list[Event] = []
        for index, event in enumerate(events):
            _validate_event(event, f"{owner_path}[{index}]")
            if event.kind != "interpolate" and (
                event.reads or event.read_tokens or event.value_token is not None
            ):
                raise RecordError(
                    f"only interpolate events may contain dependency integrity data at "
                    f"{owner_path!r}"
                )
            if event.kind != "interpolate":
                updated.append(event)
                continue
            if index != len(events) - 1:
                raise RecordError(
                    f"interpolation must be the final provenance event at {owner_path!r}"
                )
            if (
                event.value_token is None
                or len(event.read_tokens) != len(event.reads)
                or any(token is None for token in event.read_tokens)
            ):
                raise RecordError(
                    f"interpolation provenance at {owner_path!r} contains a value "
                    "without a durable integrity token"
                )
            normalized_reads: list[tuple[str, str]] = []
            for (read_path, _), recorded_token in zip(event.reads, event.read_tokens):
                try:
                    canonical_read = canonicalize_path(read_path)
                except (TypeError, ValueError) as error:
                    raise RecordError(
                        f"invalid provenance read path {read_path!r}"
                    ) from error
                if canonical_read != read_path:
                    raise RecordError(
                        f"provenance read {read_path!r} is not canonical; use "
                        f"{canonical_read!r}"
                    )
                try:
                    read_value = provenance_value(config, read_path)
                except (
                    AttributeError,
                    KeyError,
                    IndexError,
                    TypeError,
                    ValueError,
                ) as error:
                    raise RecordError(
                        f"provenance read {read_path!r} is outside the recorded Config root"
                    ) from error
                _assert_observable_read(config, owner_path, read_path)
                actual_token = provenance_token(read_value)
                if actual_token is None or recorded_token is None:
                    raise RecordError(
                        f"provenance read {read_path!r} has no durable integrity token"
                    )
                if not hmac.compare_digest(actual_token, recorded_token):
                    raise RecordError(
                        f"provenance read {read_path!r} does not match its restored value"
                    )
                normalized_reads.append((read_path, safe_repr(read_value, limit=80)))
            actual_value_token = provenance_token(owner_value)
            if actual_value_token is None or event.value_token is None:
                raise RecordError(
                    f"interpolation result at {owner_path!r} has no durable integrity token"
                )
            if not hmac.compare_digest(actual_value_token, event.value_token):
                raise RecordError(
                    f"interpolation provenance at {owner_path!r} does not match its "
                    "restored result"
                )
            updated.append(
                replace(
                    event,
                    reads=tuple(normalized_reads),
                    value=safe_repr(owner_value),
                )
            )
        normalized[owner_path] = tuple(updated)
    return normalized


def _assert_observable_read(
    config: Config,
    owner_path: str,
    read_path: str,
) -> None:
    """Reject dependency edges impossible under declaration-ordered publication."""

    owner = _parse_path(owner_path)
    read = _parse_path(read_path)
    common_length = 0
    for owner_part, read_part in zip(owner, read):
        if owner_part != read_part:
            break
        common_length += 1

    if common_length == len(owner) or common_length == len(read):
        raise RecordError(
            f"provenance read {read_path!r} for {owner_path!r} is a self or incomplete-branch "
            "dependency that was not observable during validation"
        )

    common_path = owner[:common_length]
    common_value = provenance_value(config, common_path)
    owner_part = owner[common_length]
    read_part = read[common_length]
    if (
        not isinstance(common_value, Config)
        or not isinstance(owner_part, str)
        or not isinstance(read_part, str)
    ):
        raise RecordError(
            f"provenance read {read_path!r} crosses an unpublished container branch of "
            f"{owner_path!r}"
        )

    fields = tuple(type(common_value).__pydantic_fields__)
    if owner_part not in fields or read_part not in fields:
        raise RecordError(
            f"provenance dependency between {owner_path!r} and {read_path!r} does not name "
            "declared Config fields"
        )
    if fields.index(read_part) >= fields.index(owner_part):
        raise RecordError(
            f"provenance read {read_path!r} was not published before interpolation field "
            f"{owner_path!r}"
        )


def _dump_model(
    model: BaseModel,
) -> Any:
    return type(model).__pydantic_serializer__.to_python(
        model,
        mode="json",
        by_alias=False,
        exclude_unset=False,
        exclude_defaults=False,
        exclude_none=False,
        exclude_computed_fields=True,
        round_trip=True,
        warnings="error",
        fallback=None,
        serialize_as_any=False,
    )


def _field_info_excludes_value(value: Any) -> bool:
    return isinstance(value, FieldInfo) and (
        value.exclude is True or value.exclude_if is not None
    )


def _dataclass_excluded_fields(value: Any) -> list[str]:
    pydantic_fields = getattr(type(value), "__pydantic_fields__", None)
    if isinstance(pydantic_fields, Mapping):
        return [
            name
            for name, model_field in pydantic_fields.items()
            if model_field.exclude is True or model_field.exclude_if is not None
        ]

    try:
        annotations = get_type_hints(type(value), include_extras=True)
    except Exception:
        annotations = getattr(type(value), "__annotations__", {})

    excluded: list[str] = []
    for dataclass_field in fields(value):
        candidates: list[Any] = [dataclass_field.default]
        annotation = annotations.get(dataclass_field.name)
        if get_origin(annotation) is Annotated:
            candidates.extend(get_args(annotation)[1:])
        if any(_field_info_excludes_value(candidate) for candidate in candidates):
            excluded.append(dataclass_field.name)
    return excluded


def _inspect_source(value: Any, path: str, active: set[int]) -> None:
    if isinstance(value, (SecretStr, SecretBytes)):
        raise FingerprintError(
            f"redacted secret serialization would reconstruct a different final at {path}"
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise FingerprintError(
            f"non-finite float is not legal in a run record at {path}"
        )
    if is_draft(value):
        raise FingerprintError(f"nested draft is not legal in a run record at {path}")

    if isinstance(value, BaseModel):
        data = object.__getattribute__(value, "__dict__")
        undeclared = set(data) - set(type(value).__pydantic_fields__)
        if undeclared:
            raise FingerprintError(
                f"run records do not support undeclared model instance state at {path}: "
                f"{sorted(map(str, undeclared))!r}"
            )
        excluded = [
            name
            for name, model_field in type(value).__pydantic_fields__.items()
            if model_field.exclude is True or model_field.exclude_if is not None
        ]
        if excluded:
            raise FingerprintError(
                f"run records require complete model state; field-level exclusion is not "
                f"supported at {path}: {excluded!r}"
            )
        declared_children = (
            (f".{name}", data[name])
            for name in type(value).__pydantic_fields__
            if name in data
        )
        extra = object.__getattribute__(value, "__pydantic_extra__")
        extra_children = (
            ()
            if extra is None
            else ((f"[{name!r}]", item) for name, item in extra.items())
        )
        private = object.__getattribute__(value, "__pydantic_private__") or {}
        internal_state = state_of(value) is not None
        private_children = (
            (f".{name}", item)
            for name, item in private.items()
            if not (internal_state and name in RESERVED_PRIVATE_KEYS)
        )
        children = (*declared_children, *extra_children, *private_children)
    elif is_dataclass(value) and not isinstance(value, type):
        excluded = _dataclass_excluded_fields(value)
        if excluded:
            raise FingerprintError(
                f"run records require complete dataclass state; field-level exclusion is "
                f"not supported at {path}: {excluded!r}"
            )
        children = (
            (
                f".{dataclass_field.name}",
                object.__getattribute__(value, dataclass_field.name),
            )
            for dataclass_field in fields(value)
        )
    elif isinstance(value, Mapping):
        for key in value:
            if not isinstance(key, str):
                raise FingerprintError(f"JSON object keys must be strings at {path}")
        children = ((f"[{key!r}]", item) for key, item in value.items())
    elif isinstance(value, (list, tuple, set, frozenset)):
        children = ((f"[{index}]", item) for index, item in enumerate(value))
    else:
        return

    identity = id(value)
    if identity in active:
        raise FingerprintError(
            f"configuration values must be acyclic; cycle found at {path}"
        )
    active.add(identity)
    try:
        for part, child in children:
            _inspect_source(child, f"{path}{part}", active)
    finally:
        active.remove(identity)


def _stabilize_dump(
    source: Any,
    dumped: Any,
    path: str,
    active: set[int],
) -> Any:
    if isinstance(source, BaseModel):
        data = object.__getattribute__(source, "__dict__")
        names = tuple(type(source).__pydantic_fields__)
        absent = set(names) - set(data)
        if absent:
            raise FingerprintError(
                f"model has unset fields at {path}: {sorted(absent)!r}"
            )

        if type(source).__pydantic_root_model__:
            if "root" not in data:
                raise FingerprintError(f"root model has no concrete value at {path}")
            return _stabilize_dump(
                data["root"],
                dumped,
                f"{path}.root",
                active,
            )

        if not isinstance(dumped, Mapping):
            raise FingerprintError(
                f"model serializer did not produce an object at {path}"
            )

        raw_extra = object.__getattribute__(source, "__pydantic_extra__") or {}
        raw_values = {name: data[name] for name in names}
        raw_values.update(raw_extra)
        allowed = set(raw_values)
        serialized = dict(dumped)
        if set(serialized) != allowed:
            missing = allowed - set(serialized)
            extra = set(serialized) - allowed
            details = []
            if missing:
                details.append(f"omitted fields {sorted(missing)!r}")
            if extra:
                details.append(f"undeclared fields {sorted(extra)!r}")
            raise FingerprintError(
                f"incomplete model serialization at {path}: {', '.join(details)}"
            )
        _enter(source, path, active)
        try:
            return {
                name: _stabilize_dump(
                    raw_values[name],
                    serialized[name],
                    f"{path}.{name}",
                    active,
                )
                for name in raw_values
            }
        finally:
            active.remove(id(source))

    if is_dataclass(source) and not isinstance(source, type):
        names = tuple(dataclass_field.name for dataclass_field in fields(source))
        raw_values: dict[str, Any] = {}
        unset_names: list[str] = []
        for name in names:
            try:
                raw_values[name] = object.__getattribute__(source, name)
            except AttributeError:
                unset_names.append(name)
        if unset_names:
            raise FingerprintError(
                f"dataclass has unset fields at {path}: {unset_names!r}"
            )
        if not isinstance(dumped, Mapping):
            raise FingerprintError(
                f"dataclass serializer did not produce an object at {path}"
            )
        serialized = dict(dumped)
        if set(serialized) != set(raw_values):
            omitted = set(raw_values) - set(serialized)
            undeclared = set(serialized) - set(raw_values)
            details: list[str] = []
            if omitted:
                details.append(f"omitted fields {sorted(omitted)!r}")
            if undeclared:
                details.append(f"undeclared fields {sorted(undeclared)!r}")
            raise FingerprintError(
                f"incomplete dataclass serialization at {path}: {', '.join(details)}"
            )
        _enter(source, path, active)
        try:
            return {
                name: _stabilize_dump(
                    raw_values[name],
                    serialized[name],
                    f"{path}.{name}",
                    active,
                )
                for name in names
            }
        finally:
            active.remove(id(source))

    if isinstance(source, Mapping) and isinstance(dumped, Mapping):
        if set(source) != set(dumped):
            raise FingerprintError(
                f"mapping keys collide or were omitted during JSON serialization at {path}"
            )
        _enter(source, path, active)
        try:
            output: dict[str, Any] = {}
            for source_key, source_value in source.items():
                if not isinstance(source_key, str):
                    raise FingerprintError(
                        f"JSON mapping key is not a string at {path}"
                    )
                output[source_key] = _stabilize_dump(
                    source_value,
                    dumped[source_key],
                    f"{path}[{source_key!r}]",
                    active,
                )
            return output
        finally:
            active.remove(id(source))

    if isinstance(source, (set, frozenset)):
        if not isinstance(dumped, (list, tuple)) or len(source) != len(dumped):
            raise FingerprintError(f"set serializer changed shape at {path}")
        _enter(source, path, active)
        try:
            items = [
                _canonical_json(item, f"{path}[{index}]", FingerprintError, set())
                for index, item in enumerate(dumped)
            ]
            items.sort(
                key=lambda item: _encode_json(
                    _canonical_json(item, path, FingerprintError, set())
                )
            )
            return items
        finally:
            active.remove(id(source))

    if isinstance(source, (list, tuple)) and isinstance(dumped, (list, tuple)):
        if len(source) != len(dumped):
            raise FingerprintError(f"sequence serializer changed length at {path}")
        _enter(source, path, active)
        try:
            return [
                _stabilize_dump(
                    source_item,
                    dumped_item,
                    f"{path}[{index}]",
                    active,
                )
                for index, (source_item, dumped_item) in enumerate(zip(source, dumped))
            ]
        finally:
            active.remove(id(source))

    return dumped


def _enter(value: Any, path: str, active: set[int]) -> None:
    identity = id(value)
    if identity in active:
        raise FingerprintError(
            f"configuration values must be acyclic; cycle found at {path}"
        )
    active.add(identity)


def _json_object(
    value: Mapping[str, Any],
    path: str,
    error_type: type[FingerprintError] | type[RecordError],
) -> dict[str, JsonValue]:
    canonical = _canonical_json(value, path, error_type, set())
    if not isinstance(canonical, dict):
        raise error_type(f"expected a JSON object at {path}")
    return canonical


def _canonical_json(
    value: Any,
    path: str,
    error_type: type[FingerprintError] | type[RecordError],
    active: set[int],
) -> JsonValue:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise error_type(f"non-finite float is not legal in a run record at {path}")
        return float(value)
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise error_type(f"JSON data must be acyclic; cycle found at {path}")
        active.add(identity)
        try:
            if not all(isinstance(key, str) for key in value):
                raise error_type(f"JSON object keys must be strings at {path}")
            return {
                key: _canonical_json(item, f"{path}[{key!r}]", error_type, active)
                for key, item in value.items()
            }
        finally:
            active.remove(identity)
    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            raise error_type(f"JSON data must be acyclic; cycle found at {path}")
        active.add(identity)
        try:
            return [
                _canonical_json(item, f"{path}[{index}]", error_type, active)
                for index, item in enumerate(value)
            ]
        finally:
            active.remove(identity)
    raise error_type(
        f"value of type {type(value).__qualname__} is not JSON-safe at {path}"
    )


def _encode_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=False,
    )


def _decode_provenance(value: Mapping[str, JsonValue]) -> dict[str, tuple[Event, ...]]:
    output: dict[str, tuple[Event, ...]] = {}
    for path, raw_events in value.items():
        if not isinstance(path, str) or not path:
            raise RecordError("provenance paths must be non-empty strings")
        if not isinstance(raw_events, list):
            raise RecordError(f"provenance events for {path!r} must be an array")
        events: list[Event] = []
        for index, raw_event in enumerate(raw_events):
            if not isinstance(raw_event, Mapping):
                raise RecordError(f"provenance event {path}[{index}] must be an object")
            raw_reads = raw_event.get("reads", [])
            if not isinstance(raw_reads, list) or any(
                not isinstance(read, list)
                or len(read) != 2
                or not all(isinstance(item, str) for item in read)
                for read in raw_reads
            ):
                raise RecordError(
                    f"provenance event reads are malformed at {path}[{index}]"
                )
            try:
                event = event_from_dict(raw_event)
            except (TypeError, ValueError) as error:
                raise RecordError(
                    f"invalid provenance event {path}[{index}]"
                ) from error
            _validate_event(event, f"{path}[{index}]")
            events.append(event)
        output[path] = tuple(events)
    return output


def _validate_event(event: Event, path: str) -> None:
    if not isinstance(event.kind, str) or event.kind not in {
        "set",
        "delete",
        "mutate",
        "interpolate",
    }:
        raise RecordError(f"unknown provenance event kind at {path}: {event.kind!r}")
    optional_strings = (
        event.value,
        event.file,
        event.function,
        event.code,
        event.label,
        event.operation,
        event.site,
    )
    if any(
        value is not None and not isinstance(value, str) for value in optional_strings
    ):
        raise RecordError(
            f"provenance event contains a non-string metadata value at {path}"
        )
    if event.line is not None and (
        not isinstance(event.line, int)
        or isinstance(event.line, bool)
        or event.line < 1
    ):
        raise RecordError(f"provenance event line must be a positive integer at {path}")
    if not isinstance(event.from_default, bool):
        raise RecordError(f"provenance event from_default must be a boolean at {path}")
    if any(
        not isinstance(read, tuple)
        or len(read) != 2
        or not all(isinstance(value, str) for value in read)
        for read in event.reads
    ):
        raise RecordError(f"provenance event reads are malformed at {path}")
    if event.value_token is not None and (
        not isinstance(event.value_token, str)
        or _FINGERPRINT_PATTERN.fullmatch(event.value_token) is None
    ):
        raise RecordError(f"provenance event value token is malformed at {path}")
    if not isinstance(event.read_tokens, tuple) or any(
        not isinstance(token, str) or _FINGERPRINT_PATTERN.fullmatch(token) is None
        for token in event.read_tokens
    ):
        raise RecordError(f"provenance event read tokens are malformed at {path}")

    source_fields = (event.file, event.line, event.function, event.code)
    if event.file is None:
        if any(value is not None for value in source_fields[1:]):
            raise RecordError(f"provenance source metadata is incomplete at {path}")
    elif event.line is None or not event.function:
        raise RecordError(f"provenance source metadata is incomplete at {path}")
    if event.label == "":
        raise RecordError(f"provenance event label must not be empty at {path}")

    if event.kind == "set":
        if event.value is None:
            raise RecordError(f"set provenance event has no value at {path}")
        if event.operation is not None:
            raise RecordError(
                f"set provenance event has a mutation operation at {path}"
            )
        _reject_non_interpolation_fields(event, path)
        return

    if event.kind == "delete":
        if event.value is not None:
            raise RecordError(f"delete provenance event contains a value at {path}")
        if event.operation is not None:
            raise RecordError(
                f"delete provenance event has a mutation operation at {path}"
            )
        _reject_non_interpolation_fields(event, path)
        return

    if event.kind == "mutate":
        if event.value is None:
            raise RecordError(f"mutate provenance event has no value at {path}")
        if not event.operation:
            raise RecordError(f"mutate provenance event has no operation at {path}")
        _reject_non_interpolation_fields(event, path)
        return

    if event.value is None:
        raise RecordError(f"interpolate provenance event has no value at {path}")
    if not event.site:
        raise RecordError(
            f"interpolate provenance event has no resolver site at {path}"
        )
    if event.operation is not None:
        raise RecordError(
            f"interpolate provenance event has a mutation operation at {path}"
        )
    if any(value is not None for value in source_fields):
        raise RecordError(
            f"interpolate provenance event contains operation source metadata at {path}"
        )
    read_paths = [read_path for read_path, _ in event.reads]
    if len(set(read_paths)) != len(read_paths):
        raise RecordError(
            f"interpolate provenance event contains duplicate reads at {path}"
        )


def _reject_non_interpolation_fields(event: Event, path: str) -> None:
    if (
        event.site is not None
        or event.reads
        or event.value_token is not None
        or event.read_tokens
        or event.from_default
    ):
        raise RecordError(
            f"only interpolate events may contain interpolation metadata at {path}"
        )
