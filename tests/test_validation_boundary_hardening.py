"""Class and input boundaries cannot bypass canonical Config validation."""

from typing import Annotated, Any, Literal

import pytest
from pydantic import PlainValidator, ValidationError, WrapValidator, model_validator

import nshconfig as C


@pytest.mark.parametrize(
    "metadata",
    [
        PlainValidator(lambda value: value),
        WrapValidator(lambda value, handler: handler(value)),
    ],
)
def test_annotated_plain_and_wrap_validators_are_rejected(
    metadata: Any,
) -> None:
    with pytest.raises(TypeError, match="can bypass or omit Config field validation"):

        class Unsafe(C.Config):
            value: Annotated[int, metadata]


@pytest.mark.parametrize("collection_type", [list, dict, set])
def test_custom_collection_inputs_are_rejected_before_pydantic_coercion(
    collection_type: type[Any],
) -> None:
    class Custom(collection_type):  # type: ignore[misc, valid-type]
        pass

    class Values(C.Config):
        value: Any

    raw = Custom([1]) if collection_type is not dict else Custom({"a": 1})
    with pytest.raises(ValidationError, match="unsupported or lazy container"):
        Values(value=raw)

    work = C.draft(Values)
    work.value = raw
    with pytest.raises(ValidationError, match="unsupported or lazy container"):
        C.finalize(work)


def test_model_before_can_intentionally_normalize_a_legacy_collection() -> None:
    class LegacyList(list[int]):
        pass

    class Values(C.Config):
        values: list[int]

        @model_validator(mode="before")
        @classmethod
        def normalize_legacy(cls, value: Any) -> Any:
            if isinstance(value, dict) and isinstance(value.get("legacy"), LegacyList):
                return {"values": list(value["legacy"])}
            return value

    raw = {"legacy": LegacyList([1, 2])}
    final = Values.model_validate(raw)
    assert final.values == [1, 2]
    assert raw == {"legacy": [1, 2]}
    assert type(raw["legacy"]) is LegacyList


def test_direct_config_fields_are_required_or_explicitly_interpolated() -> None:
    class Child(C.Config):
        value: int = 1

    with pytest.raises(TypeError, match="direct Config field a concrete default"):

        class ConcreteDefault(C.Config):
            child: Child = Child()

    with pytest.raises(TypeError, match="direct Config field a concrete default"):

        class MappingDefault(C.Config):
            child: Child = {"value": 2}  # type: ignore[assignment]

    class InterpolatedDefault(C.Config):
        source: Child
        child: Child = C.interp(lambda context: context.current().source)

    final = InterpolatedDefault(source={"value": 3})
    assert final.child.value == 3
    assert final.child is not final.source


def test_literal_default_tracking_preserves_public_json_schema_defaults() -> None:
    class Defaults(C.Config):
        mode: Literal["train", "eval"] = "train"

    schema = Defaults.model_json_schema()
    assert schema["properties"]["mode"]["default"] == "train"
    assert Defaults().mode == "train"
