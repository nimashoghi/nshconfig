"""Canaries for exact runtime and alias-sensitive run identity."""

import os
from pathlib import Path
import subprocess
import sys
from textwrap import dedent
from typing import Any, Generic, TypeVar
import warnings

import pytest
from pydantic import AliasChoices, Field
from pydantic_core import core_schema

import nshconfig as C


_ENVIRONMENT_MODE = "first"


class _ModeBox:
    __slots__ = ("mode", "raw")

    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.mode = _ENVIRONMENT_MODE

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: Any,
    ) -> core_schema.CoreSchema:
        del source_type, handler
        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.str_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda value: value.raw,
                return_schema=core_schema.str_schema(),
            ),
        )


class _EnvironmentConfig(C.Config):
    value: _ModeBox


def test_fingerprint_includes_exact_runtime_semantics_beyond_json_values() -> None:
    global _ENVIRONMENT_MODE
    _ENVIRONMENT_MODE = "first"
    first = _EnvironmentConfig(value="same")  # pyright: ignore[reportArgumentType]
    first_record = C.record(first)
    first_fingerprint = C.fingerprint(first)

    _ENVIRONMENT_MODE = "second"
    try:
        second = _EnvironmentConfig(value="same")  # pyright: ignore[reportArgumentType]
        second_record = C.record(second)
        assert first_record.values == second_record.values
        assert first_record.semantic_fingerprint != second_record.semantic_fingerprint
        assert first_fingerprint != C.fingerprint(second)

        with pytest.raises(C.RecordError, match="runtime semantics"):
            C.load_record(_EnvironmentConfig, first_record)
    finally:
        _ENVIRONMENT_MODE = "first"


def _aliased_config(validation_alias: str) -> type[C.Config]:
    namespace = {
        "__module__": __name__,
        "__qualname__": "AliasStableIdentity",
        "__annotations__": {"value": int},
        "value": Field(
            validation_alias=AliasChoices("value", validation_alias),
            serialization_alias="wire_value",
        ),
    }
    return type("AliasStableIdentity", (C.Config,), namespace)


def test_schema_fingerprint_includes_all_validation_alias_choices() -> None:
    first_type = _aliased_config("legacy_first")
    second_type = _aliased_config("legacy_second")
    first = first_type.model_validate({"legacy_first": 7})
    second = second_type.model_validate({"legacy_second": 7})

    assert first.model_dump() == second.model_dump() == {"value": 7}
    assert C.record(first).config_type == C.record(second).config_type
    assert C.record(first).schema_fingerprint != C.record(second).schema_fingerprint
    assert C.fingerprint(first) != C.fingerprint(second)


def _nested_aliased_config(validation_alias: str) -> type[C.Config]:
    child = type(
        "NestedAliasChild",
        (C.Config,),
        {
            "__module__": __name__,
            "__qualname__": "NestedAliasChild",
            "__annotations__": {"value": int},
            "value": Field(validation_alias=AliasChoices("value", validation_alias)),
        },
    )
    return type(
        "NestedAliasRoot",
        (C.Config,),
        {
            "__module__": __name__,
            "__qualname__": "NestedAliasRoot",
            "__annotations__": {"child": child},
        },
    )


def test_schema_fingerprint_includes_nested_secondary_aliases() -> None:
    first_type = _nested_aliased_config("old_first")
    second_type = _nested_aliased_config("old_second")
    first = first_type.model_validate({"child": {"old_first": 7}})
    second = second_type.model_validate({"child": {"old_second": 7}})

    assert first.model_dump() == second.model_dump() == {"child": {"value": 7}}
    assert C.record(first).schema_fingerprint != C.record(second).schema_fingerprint
    assert C.fingerprint(first) != C.fingerprint(second)


def _versioned_nested_config(schema_id: str) -> type[C.Config]:
    child = type(
        "VersionedChild",
        (C.Config,),
        {
            "__module__": __name__,
            "__qualname__": "VersionedChild",
            "record_schema_id": schema_id,
            "__annotations__": {"value": int},
        },
    )
    return type(
        "VersionedRoot",
        (C.Config,),
        {
            "__module__": __name__,
            "__qualname__": "VersionedRoot",
            "__annotations__": {"child": child},
        },
    )


def test_schema_fingerprint_propagates_nested_config_schema_ids() -> None:
    first_type = _versioned_nested_config("tests.child.v1")
    second_type = _versioned_nested_config("tests.child.v2")
    first = first_type(child={"value": 7})
    second = second_type(child={"value": 7})

    assert first.model_dump() == second.model_dump()
    assert C.record(first).schema_fingerprint != C.record(second).schema_fingerprint
    assert C.fingerprint(first) != C.fingerprint(second)


def test_nested_generated_generic_requires_stable_record_identity() -> None:
    item_type = TypeVar("item_type")

    class Box(C.Config, Generic[item_type]):
        value: item_type

    generated = Box[int]

    class Root(C.Config):
        child: generated  # pyright: ignore[reportInvalidTypeForm]

    with pytest.raises(C.FingerprintError, match="record_schema_id"):
        C.fingerprint(Root(child={"value": 7}))


@pytest.mark.parametrize(
    "field_name",
    [
        "array",
        "atom",
        "bytearray",
        "dataclass",
        "defaultdict",
        "deque",
        "dict",
        "enum",
        "frozenset",
        "list",
        "mapping",
        "memoryview",
        "model",
        "object",
        "path",
        "pattern",
        "ref",
        "set",
        "tuple",
        "type",
        "zoneinfo",
    ],
)
def test_snapshot_tags_cannot_collide_with_field_names_or_path_parts(
    field_name: str,
) -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r'Field name "dict" .* shadows an attribute in parent "Config"',
            category=UserWarning,
        )
        config_type = type(
            f"TaggedField_{field_name}",
            (C.Config,),
            {
                "__module__": __name__,
                "record_schema_id": f"tests.tagged-field.{field_name}",
                "__annotations__": {field_name: Path},
            },
        )
    final = config_type.model_validate({field_name: Path(field_name) / "a" / "x"})
    run_record = C.record(final)

    assert run_record.semantic_fingerprint.startswith("sha256:")
    assert C.fingerprint(final).startswith("sha256:")
    assert C.load_record(config_type, run_record) == final


def test_durable_semantic_identity_is_hash_seed_independent() -> None:
    script = dedent(
        """
        from zoneinfo import ZoneInfo

        import nshconfig as C
        from pydantic import PrivateAttr

        class HashSeedConfig(C.Config):
            record_schema_id = "tests.hash-seed.v1"
            zone: ZoneInfo
            _unordered: set[tuple[str, int]] = PrivateAttr(
                default_factory=lambda: {
                    ("alpha", 1),
                    ("beta", 2),
                    ("gamma", 3),
                }
            )

        final = HashSeedConfig(zone=ZoneInfo("UTC"))
        run_record = C.record(final)
        print(run_record.semantic_fingerprint)
        print(C.fingerprint(final))
        """
    )
    outputs: set[str] = set()
    for seed in ("0", "1", "2", "17", "random"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            env=environment,
            text=True,
        )
        outputs.add(completed.stdout)

    assert len(outputs) == 1
