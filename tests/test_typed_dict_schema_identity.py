"""Canaries for TypedDict semantics in durable schema identity."""

import json
import os
import subprocess
import sys
from textwrap import dedent
from typing import Annotated, Any

import pytest
from pydantic import AliasChoices, ConfigDict, Field
from typing_extensions import TypedDict

import nshconfig as C


def _nested_root(
    secondary_alias: str,
    *,
    strict: bool | None = None,
) -> type[C.Config]:
    payload = TypedDict(
        "_StablePayload",
        {
            "value": Annotated[
                int,
                Field(validation_alias=AliasChoices("value", secondary_alias)),
            ]
        },
    )
    payload.__module__ = __name__
    payload.__qualname__ = "_StablePayload"
    if strict is not None:
        payload.__pydantic_config__ = ConfigDict(strict=strict)

    envelope = TypedDict("_StableEnvelope", {"items": list[payload]})
    envelope.__module__ = __name__
    envelope.__qualname__ = "_StableEnvelope"
    return type(
        "_StableRoot",
        (C.Config,),
        {
            "__module__": __name__,
            "__qualname__": "_StableRoot",
            "record_schema_id": "tests.typed-dict-schema.v1",
            "__annotations__": {"envelope": envelope},
        },
    )


def _validate_with_alias(config_type: type[C.Config], alias: str) -> C.Config:
    return config_type.model_validate({"envelope": {"items": [{alias: 7}]}})


def test_nested_typed_dict_secondary_aliases_change_schema_identity() -> None:
    first_type = _nested_root("legacy_first")
    second_type = _nested_root("legacy_second")
    first = _validate_with_alias(first_type, "legacy_first")
    second = _validate_with_alias(second_type, "legacy_second")

    first_record = C.record(first)
    second_record = C.record(second)

    assert first_record.config_type == second_record.config_type
    assert first_record.values == second_record.values
    assert first_record.schema_fingerprint != second_record.schema_fingerprint
    assert first_record.fingerprint != second_record.fingerprint
    assert C.load_record(first_type, first_record) == first
    assert C.load_record(second_type, second_record) == second
    with pytest.raises(C.RecordError, match="schema fingerprint"):
        C.load_record(second_type, first_record)


def test_nested_typed_dict_core_config_changes_schema_identity() -> None:
    lax_type = _nested_root("legacy", strict=False)
    strict_type = _nested_root("legacy", strict=True)
    lax = _validate_with_alias(lax_type, "legacy")
    strict = _validate_with_alias(strict_type, "legacy")

    lax_record = C.record(lax)
    strict_record = C.record(strict)

    assert lax_record.values == strict_record.values
    assert lax_record.schema_fingerprint != strict_record.schema_fingerprint


def test_conditional_typed_dict_field_exclusion_is_not_recordable() -> None:
    payload = TypedDict(
        "_ConditionallyExcludedPayload",
        {
            "value": Annotated[
                int,
                Field(exclude_if=lambda value: value < 0),
            ]
        },
    )

    root_type = type(
        "_ConditionallyExcludedRoot",
        (C.Config,),
        {
            "__module__": __name__,
            "__annotations__": {"payload": payload},
        },
    )

    final = root_type(payload={"value": 7})
    with pytest.raises(
        C.FingerprintError, match="conditional TypedDict field exclusion"
    ):
        C.record(final)


def _fresh_process_result(alias: str, hash_seed: str) -> dict[str, Any]:
    script = dedent(
        """
        import json
        import os
        from typing import Annotated

        from pydantic import AliasChoices, Field
        from typing_extensions import TypedDict

        import nshconfig as C

        secondary_alias = os.environ["SECONDARY_ALIAS"]

        class Payload(TypedDict):
            value: Annotated[
                int,
                Field(validation_alias=AliasChoices("value", secondary_alias)),
            ]

        class Envelope(TypedDict):
            items: list[Payload]

        class Root(C.Config):
            record_schema_id = "tests.typed-dict-fresh-process.v1"
            envelope: Envelope

        final = Root.model_validate(
            {"envelope": {"items": [{secondary_alias: 7}]}}
        )
        run_record = C.record(final)
        restored = C.load_record(Root, run_record)
        print(
            json.dumps(
                {
                    "schema_fingerprint": run_record.schema_fingerprint,
                    "semantic_fingerprint": run_record.semantic_fingerprint,
                    "fingerprint": run_record.fingerprint,
                    "record_keys": sorted(run_record.to_dict()),
                    "restored": restored.model_dump(),
                },
                sort_keys=True,
            )
        )
        """
    )
    environment = os.environ.copy()
    environment["PYTHONHASHSEED"] = hash_seed
    environment["SECONDARY_ALIAS"] = alias
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert isinstance(result, dict)
    return result


def test_typed_dict_schema_identity_is_stable_across_fresh_processes() -> None:
    first = _fresh_process_result("legacy_first", "1")
    repeated = _fresh_process_result("legacy_first", "17")
    second = _fresh_process_result("legacy_second", "1")

    assert first == repeated
    assert first["schema_fingerprint"] != second["schema_fingerprint"]
    assert first["fingerprint"] != second["fingerprint"]
    assert first["semantic_fingerprint"] == second["semantic_fingerprint"]
    assert first["record_keys"] == [
        "config_type",
        "fingerprint",
        "format",
        "provenance",
        "schema_fingerprint",
        "semantic_fingerprint",
        "values",
    ]
    assert first["restored"] == {"envelope": {"items": [{"value": 7}]}}
