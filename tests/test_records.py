"""Deterministic fingerprints and inert, provenance-bearing run records."""

import json
import re
from collections import defaultdict, deque
from dataclasses import FrozenInstanceError, field as dataclass_field
from datetime import datetime, time
from enum import Enum
from typing import Annotated, Any, ClassVar, Generic, TypeVar
from zoneinfo import ZoneInfo

import pytest
from annotated_types import Ge
from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    ValidationError,
    field_serializer,
    model_serializer,
    model_validator,
)
from pydantic.dataclasses import dataclass as pydantic_dataclass

import nshconfig as C

_DERIVATION_CALLS = 0
_DROPPED_DERIVATION_CALLS = 0
_DROP_RECORDED_FIELD = False


def _derive(ctx: C.Context) -> int:
    global _DERIVATION_CALLS
    _DERIVATION_CALLS += 1
    return ctx.current().seed * 2


class Run(C.Config):
    seed: int
    derived: int = C.interp(_derive)
    parameters: dict[str, int] = {}
    tags: set[str] = set()


class SameShape(C.Config):
    seed: int
    derived: int
    parameters: dict[str, int] = {}
    tags: set[str] = set()


def _derive_after_drop(ctx: C.Context) -> int:
    global _DROPPED_DERIVATION_CALLS
    _DROPPED_DERIVATION_CALLS += 1
    return ctx.current().source * 3


class DropsRecordedField(C.Config):
    source: int
    derived: int = C.interp(_derive_after_drop)

    @model_validator(mode="before")
    @classmethod
    def _drop_derived(cls, value: Any) -> Any:
        if not _DROP_RECORDED_FIELD:
            return value
        if not isinstance(value, dict):
            return value
        value = dict(value)
        value.pop("derived", None)
        return value


def _composed_run(seed: int = 5) -> Run:
    work = C.draft(Run)
    with C.source("test seed"):
        work.seed = seed
    work.parameters = {"z": 26, "a": 1}
    work.tags = {"later", "earlier"}
    return C.finalize(work)


def test_fingerprint_is_deterministic_type_aware_and_ignores_provenance():
    first_work = C.draft(Run)
    with C.source("first source"):
        first_work.seed = 7
    first_work.parameters = {"b": 2, "a": 1}
    first_work.tags = {"b", "a"}

    second_work = C.draft(Run)
    with C.source("second source"):
        second_work.seed = 7
    second_work.parameters = {"b": 2, "a": 1}
    second_work.tags = {"a", "b"}

    first = C.finalize(first_work)
    second = C.finalize(second_work)
    assert C.provenance(first) != C.provenance(second)
    assert C.fingerprint(first) == C.fingerprint(second)

    reordered_work = C.draft(Run)
    reordered_work.seed = 7
    reordered_work.parameters = {"a": 1, "b": 2}
    reordered_work.tags = {"a", "b"}
    assert C.fingerprint(C.finalize(reordered_work)) != C.fingerprint(first)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", C.fingerprint(first))

    same_values_different_type = SameShape.model_validate(first.model_dump())
    assert C.fingerprint(same_values_different_type) != C.fingerprint(first)


def test_record_is_immutable_canonical_json_data():
    run = _composed_run()
    run_record = C.record(run)

    assert run_record.format == "nshconfig.run-record.v4"
    assert run_record.config_type == f"{Run.__module__}:{Run.__qualname__}"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", run_record.schema_fingerprint)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", run_record.semantic_fingerprint)
    assert run_record.fingerprint == C.fingerprint(run)
    assert run_record.values["parameters"] == {"a": 1, "z": 26}
    assert run_record.values["tags"] == ["earlier", "later"]

    detached_values = run_record.values
    detached_values["seed"] = 999
    assert run_record.values["seed"] == 5
    with pytest.raises(FrozenInstanceError):
        run_record.fingerprint = "sha256:" + "0" * 64  # pyright: ignore[reportAttributeAccessIssue]

    envelope = run_record.to_dict()
    assert json.loads(json.dumps(envelope)) == envelope
    assert json.loads(run_record.to_json()) == envelope
    assert C.RunRecord.from_dict(envelope) == run_record


def test_load_record_validates_values_without_executing_interpolation_and_restores_provenance():
    global _DERIVATION_CALLS
    _DERIVATION_CALLS = 0
    final = _composed_run(seed=11)
    assert _DERIVATION_CALLS == 1
    original_provenance = C.provenance(final)

    wire_value = json.loads(json.dumps(C.record(final).to_dict()))
    loaded = C.load_record(Run, wire_value)

    assert _DERIVATION_CALLS == 1
    assert loaded == final
    assert not C.is_draft(loaded)
    assert C.fingerprint(loaded) == C.fingerprint(final)
    assert C.provenance(loaded) == original_provenance
    assert C.explain(loaded, "derived").events[-1].kind == "interpolate"


def test_provenance_integrity_is_stable_and_rejects_false_interpolation_results() -> (
    None
):
    class LongRead(C.Config):
        source: str
        length: int = C.interp(lambda context: len(context.current().source))

    long_final = LongRead(source="x" * 100)
    long_record = C.record(long_final)
    assert C.load_record(LongRead, long_record) == long_final

    class SetRead(C.Config):
        source: set[str]
        length: int = C.interp(lambda context: len(context.current().source))

    set_final = SetRead(source=set("abcdefgh"))
    set_record = C.record(set_final)
    assert C.load_record(SetRead, set_record) == set_final

    tampered = long_record.to_dict()
    raw_provenance = tampered["provenance"]
    assert isinstance(raw_provenance, dict)
    raw_events = raw_provenance["length"]
    assert isinstance(raw_events, list)
    assert isinstance(raw_events[-1], dict)
    raw_events[-1]["value"] = "999"
    normalized = C.load_record(LongRead, tampered)
    assert C.explain(normalized, "length").events[-1].value == "100"

    raw_events[-1]["value_token"] = "sha256:" + "0" * 64
    with pytest.raises(C.RecordError, match="restored result"):
        C.load_record(LongRead, tampered)


def test_load_record_fails_instead_of_interpolating_when_a_before_validator_drops_data():
    global _DROPPED_DERIVATION_CALLS, _DROP_RECORDED_FIELD
    _DROPPED_DERIVATION_CALLS = 0
    _DROP_RECORDED_FIELD = False
    final = DropsRecordedField(source=4)
    assert _DROPPED_DERIVATION_CALLS == 1
    run_record = C.record(final)

    _DROP_RECORDED_FIELD = True
    try:
        with pytest.raises(C.FingerprintError, match="substituted defaults"):
            C.record(final)
        assert _DROPPED_DERIVATION_CALLS == 1

        with pytest.raises(C.RecordError, match="substituted defaults"):
            C.load_record(DropsRecordedField, run_record)
        assert _DROPPED_DERIVATION_CALLS == 1

        incomplete = run_record.to_dict()
        raw_values = incomplete["values"]
        assert isinstance(raw_values, dict)
        del raw_values["derived"]
        with pytest.raises(C.RecordError, match="contain every declared field"):
            C.load_record(DropsRecordedField, incomplete)
        assert _DROPPED_DERIVATION_CALLS == 1
    finally:
        _DROP_RECORDED_FIELD = False


def test_load_record_rejects_tampering_type_mismatches_and_malformed_envelopes():
    run_record = C.record(_composed_run())

    tampered = run_record.to_dict()
    assert isinstance(tampered["values"], dict)
    tampered["values"]["seed"] = 123
    with pytest.raises(C.RecordError, match="fingerprint does not match"):
        C.load_record(Run, tampered)

    malformed_fingerprint = run_record.to_dict()
    malformed_fingerprint["fingerprint"] = "sha256:not-a-digest"
    with pytest.raises(C.RecordError, match="64 lowercase hex"):
        C.load_record(Run, malformed_fingerprint)

    malformed_schema = run_record.to_dict()
    malformed_schema["schema_fingerprint"] = "sha256:not-a-digest"
    with pytest.raises(C.RecordError, match="schema_fingerprint.*64 lowercase"):
        C.load_record(Run, malformed_schema)

    wrong_schema = run_record.to_dict()
    wrong_schema["schema_fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(C.RecordError, match="schema fingerprint does not match"):
        C.load_record(Run, wrong_schema)

    wrong_format = run_record.to_dict()
    wrong_format["format"] = "nshconfig.run-record.v2"
    with pytest.raises(C.RecordError, match="unsupported run-record format"):
        C.load_record(Run, wrong_format)

    with pytest.raises(C.RecordError, match="not requested type"):
        C.load_record(SameShape, run_record)

    malformed_provenance = run_record.to_dict()
    malformed_provenance["provenance"] = {"seed": [{"kind": "invented"}]}
    with pytest.raises(C.RecordError, match="unknown provenance event kind"):
        C.load_record(Run, malformed_provenance)

    non_string_event_kind = run_record.to_dict()
    non_string_event_kind["provenance"] = {"seed": [{"kind": []}]}
    with pytest.raises(C.RecordError, match="unknown provenance event kind"):
        C.load_record(Run, non_string_event_kind)

    malformed_reads = run_record.to_dict()
    raw_provenance = malformed_reads["provenance"]
    assert isinstance(raw_provenance, dict)
    raw_events = raw_provenance["derived"]
    assert isinstance(raw_events, list)
    raw_event = raw_events[-1]
    assert isinstance(raw_event, dict)
    raw_event["reads"] = ["ab"]
    with pytest.raises(C.RecordError, match="event reads are malformed"):
        C.load_record(Run, malformed_reads)

    dangling_read = run_record.to_dict()
    raw_provenance = dangling_read["provenance"]
    assert isinstance(raw_provenance, dict)
    raw_events = raw_provenance["derived"]
    assert isinstance(raw_events, list)
    raw_event = raw_events[-1]
    assert isinstance(raw_event, dict)
    raw_event["reads"] = [["does_not_exist", "5"]]
    with pytest.raises(C.RecordError, match="outside the recorded Config root"):
        C.load_record(Run, dangling_read)


def test_invalid_run_record_cannot_be_constructed() -> None:
    run_record = C.record(_composed_run())
    valid = run_record.to_dict()

    wrong_format = dict(valid)
    wrong_format["format"] = "nshconfig.run-record.v3"
    with pytest.raises(C.RecordError, match="unsupported run-record format"):
        C.RunRecord.from_dict(wrong_format)

    malformed_semantic = dict(valid)
    malformed_semantic["semantic_fingerprint"] = "sha256:not-a-digest"
    with pytest.raises(C.RecordError, match="semantic_fingerprint.*64 lowercase"):
        C.RunRecord.from_dict(malformed_semantic)

    malformed_type = dict(valid)
    malformed_type["config_type"] = "missing-separator"
    with pytest.raises(C.RecordError, match="module:qualname"):
        C.RunRecord.from_dict(malformed_type)

    missing_key = run_record.to_dict()
    del missing_key["values"]
    with pytest.raises(C.RecordError, match="missing keys"):
        C.load_record(Run, missing_key)

    invalid_envelope: dict[Any, Any] = run_record.to_dict()
    invalid_envelope[1] = "not a JSON object key"
    with pytest.raises(C.RecordError, match="envelope keys must be strings"):
        C.load_record(Run, invalid_envelope)


def test_load_record_rejects_provenance_attached_to_plain_pydantic_fields():
    class Plain(BaseModel):
        value: int

    class Outer(C.Config):
        plain: Plain

    wire = C.record(Outer(plain=Plain(value=1))).to_dict()
    wire["provenance"] = {"plain.value": [{"kind": "set", "value": "999", "reads": []}]}

    with pytest.raises(C.RecordError, match="provenance does not match"):
        C.load_record(Outer, wire)


def test_fingerprint_and_record_reject_drafts():
    work = C.draft(Run)
    for operation in (C.fingerprint, C.record):
        with pytest.raises(C.DraftError, match="produced a final"):
            operation(work)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_fingerprint_rejects_non_finite_floats(value: float):
    class FloatValue(C.Config):
        value: float

    with pytest.raises(C.FingerprintError, match="non-finite float"):
        C.fingerprint(FloatValue(value=value))


def test_fingerprint_rejects_cycles_opaque_values_and_non_json_mapping_keys():
    class Anything(C.Config):
        value: Any

    cyclic: list[Any] = []
    config = Anything(value=cyclic)
    config.value.append(config.value)
    with pytest.raises(C.FingerprintError, match="acyclic"):
        C.fingerprint(config)

    with pytest.raises(
        C.FingerprintError, match="cannot be serialized deterministically"
    ):
        C.fingerprint(Anything(value=object()))

    with pytest.raises(C.FingerprintError, match="keys must be strings"):
        C.fingerprint(Anything(value={1: "ambiguous JSON key"}))


def test_fingerprint_rejects_lossy_serializers_that_collapse_unequal_values():
    class Secret(C.Config):
        value: SecretStr

    first = Secret(value="alpha")
    second = Secret(value="beta")
    assert first != second

    with pytest.raises(C.FingerprintError, match="reconstruct a different final"):
        C.fingerprint(first)
    with pytest.raises(C.FingerprintError, match="reconstruct a different final"):
        C.fingerprint(second)


def test_fingerprint_and_record_reject_excluded_config_fields():
    class Hidden(C.Config):
        visible: int
        secret: int = Field(exclude=True)

    final = Hidden(visible=1, secret=2)
    with pytest.raises(C.FingerprintError, match="field-level exclusion.*secret"):
        C.fingerprint(final)
    with pytest.raises(C.FingerprintError, match="field-level exclusion.*secret"):
        C.record(final)


def test_nested_basemodel_exclusions_are_rejected_without_runtime_subclassing():
    class Inner(BaseModel):
        subclass_calls: ClassVar[int] = 0
        visible: int
        secret: int = Field(exclude=True)

        def __init_subclass__(cls, **kwargs: Any) -> None:
            Inner.subclass_calls += 1
            raise RuntimeError("Inner is sealed")

    class Outer(C.Config):
        inner: Inner

    final = Outer(inner=Inner(visible=1, secret=2))
    with pytest.raises(
        C.FingerprintError, match=r"field-level exclusion.*\$\.inner.*secret"
    ):
        C.fingerprint(final)
    assert Inner.subclass_calls == 0


def test_exclude_if_is_rejected_even_when_the_current_value_would_be_included():
    class Inner(BaseModel):
        value: int = Field(exclude_if=lambda value: value < 0)

    class Outer(C.Config):
        inner: Inner

    with pytest.raises(C.FingerprintError, match="field-level exclusion.*value"):
        C.fingerprint(Outer(inner=Inner(value=7)))


def test_fingerprint_rejects_unstable_serializers():
    class Unstable(C.Config):
        value: int
        toggle: ClassVar[bool] = False

        @field_serializer("value")
        def _unstable_value(self, value: int) -> int:
            type(self).toggle = not type(self).toggle
            return value + int(type(self).toggle)

    Unstable.toggle = False
    with pytest.raises(C.FingerprintError, match="different values"):
        C.fingerprint(Unstable(value=1))


def test_fingerprint_rejects_json_that_cannot_reconstruct_a_custom_serialized_field():
    class Encoded(C.Config):
        value: int

        @field_serializer("value")
        def _encode_value(self, value: int) -> str:
            return f"integer:{value}"

    final = Encoded(value=7)
    with pytest.raises(C.FingerprintError, match="cannot reconstruct"):
        C.fingerprint(final)


@pytest.mark.parametrize(
    ("annotation", "lossy_value", "stable_value"),
    [
        (list[int] | tuple[int, ...], (1, 2), [1, 2]),
        (list[int] | set[int], {1, 2}, [1, 2]),
    ],
)
def test_fingerprint_rejects_union_container_shapes_that_collapse_in_json(
    annotation: Any, lossy_value: Any, stable_value: Any
):
    class Container(C.Config):
        value: annotation  # pyright: ignore[reportInvalidTypeForm]

    lossy = Container(value=lossy_value)
    stable = Container(value=stable_value)
    assert type(lossy.value) is type(lossy_value)
    with pytest.raises(C.FingerprintError, match="reconstruct a different final"):
        C.fingerprint(lossy)
    assert C.load_record(Container, C.record(stable)) == stable


def test_record_round_trip_uses_canonical_field_names_and_restores_provenance():
    class Aliased(C.Config):
        value: int = Field(alias="external")

    work = C.draft(Aliased)
    with C.source("alias input"):
        work.value = 3
    final = C.finalize(work)

    run_record = C.record(final)
    assert run_record.values == {"value": 3}
    loaded = C.load_record(Aliased, run_record)
    assert loaded == final
    assert C.provenance(loaded) == C.provenance(final)


def test_fingerprint_rejects_equal_values_with_different_exact_runtime_types():
    class TextChoice(str, Enum):
        one = "one"

    class EnumValue(C.Config):
        value: Any

    enum_value = EnumValue(value=TextChoice.one)
    assert type(enum_value.value) is TextChoice
    with pytest.raises(C.FingerprintError, match="exact runtime semantics"):
        C.fingerprint(enum_value)

    class FrozenValue(C.Config):
        value: set[int] | frozenset[int]

    frozen_value = FrozenValue(value=frozenset({1, 2}))
    assert type(frozen_value.value) is frozenset
    with pytest.raises(C.FingerprintError, match="exact runtime semantics"):
        C.fingerprint(frozen_value)


@pytest.mark.parametrize(
    ("annotation", "value"),
    [
        (
            datetime,
            datetime(2025, 7, 1, 12, tzinfo=ZoneInfo("America/New_York")),
        ),
        (datetime, datetime(2025, 1, 1, 12, fold=1)),
        (time, time(1, 2, fold=1)),
    ],
)
def test_fingerprint_rejects_temporal_state_that_json_does_not_preserve(
    annotation: Any, value: Any
):
    class Temporal(C.Config):
        value: annotation  # pyright: ignore[reportInvalidTypeForm]

    final = Temporal(value=value)
    with pytest.raises(C.FingerprintError, match="exact runtime semantics"):
        C.fingerprint(final)


@pytest.mark.parametrize(
    "value",
    [
        deque([1, 2], maxlen=3),
        defaultdict(lambda: 99, {"value": 1}),
    ],
)
def test_hidden_state_containers_are_rejected_before_the_record_boundary(
    value: Any,
):
    class Container(C.Config):
        value: Any

    with pytest.raises(ValidationError, match="unsupported or lazy container"):
        Container(value=value)


def test_record_does_not_trust_nested_user_equality():
    class HostileEquality(BaseModel):
        value: int

        def __eq__(self, other: object) -> bool:
            return isinstance(other, HostileEquality)

        @field_serializer("value")
        def _collapse_value(self, value: int) -> int:
            del value
            return 0

    class Outer(C.Config):
        inner: HostileEquality

    final = Outer(inner=HostileEquality(value=7))
    with pytest.raises(C.FingerprintError, match="exact runtime semantics"):
        C.fingerprint(final)


def test_schema_fingerprint_distinguishes_colliding_generic_specializations():
    value_type = TypeVar("value_type")

    class Box(C.Config, Generic[value_type]):
        value: value_type

    lower_bound = Box[Annotated[int, Ge(0)]]
    higher_bound = Box[Annotated[int, Ge(10)]]
    assert lower_bound is not higher_bound
    assert lower_bound.__qualname__ == higher_bound.__qualname__

    with pytest.raises(C.FingerprintError, match="record_schema_id"):
        C.fingerprint(lower_bound(value=12))
    with pytest.raises(C.FingerprintError, match="record_schema_id"):
        C.fingerprint(higher_bound(value=12))

    class Lower(lower_bound):
        record_schema_id = "tests.box.lower.v1"

    class Higher(higher_bound):
        record_schema_id = "tests.box.higher.v1"

    lower = Lower(value=12)
    higher = Higher(value=12)
    lower_record = C.record(lower)
    higher_record = C.record(higher)

    assert lower_record.config_type != higher_record.config_type
    assert lower_record.values == higher_record.values
    assert lower_record.schema_fingerprint != higher_record.schema_fingerprint
    assert C.fingerprint(lower) != C.fingerprint(higher)
    with pytest.raises(C.RecordError, match="not requested type"):
        C.load_record(Higher, lower_record)


def test_pydantic_dataclass_exclusions_are_rejected():
    @pydantic_dataclass
    class Hidden:
        visible: int
        secret: Annotated[int, Field(exclude=True)] = dataclass_field(
            default=0,
            compare=False,
        )

    class Outer(C.Config):
        hidden: Hidden

    final = Outer(hidden=Hidden(visible=1, secret=99))
    with pytest.raises(C.FingerprintError, match="complete dataclass state.*secret"):
        C.fingerprint(final)


def test_pydantic_dataclass_serializers_must_preserve_declared_shape():
    @pydantic_dataclass
    class Expanded:
        value: int

        @model_serializer
        def _serialize(self) -> dict[str, int]:
            return {"value": self.value, "undeclared": 1}

    class Outer(C.Config):
        expanded: Expanded

    final = Outer(expanded=Expanded(value=7))
    with pytest.raises(C.FingerprintError, match="incomplete dataclass serialization"):
        C.fingerprint(final)


def test_record_validation_must_consume_every_stored_field():
    factory_calls: list[str] = []

    class ConsumesDefault(C.Config):
        drop_input: ClassVar[bool] = False
        value: int = Field(default_factory=lambda: factory_calls.append("factory") or 5)

        @model_validator(mode="before")
        @classmethod
        def _maybe_drop_value(cls, value: Any) -> Any:
            if not cls.drop_input or not isinstance(value, dict):
                return value
            updated = dict(value)
            updated.pop("value", None)
            return updated

    final = ConsumesDefault(value=5)
    run_record = C.record(final)
    assert factory_calls == []

    ConsumesDefault.drop_input = True
    try:
        with pytest.raises(C.FingerprintError, match="substituted defaults"):
            C.record(final)
        with pytest.raises(C.RecordError, match="substituted defaults"):
            C.load_record(ConsumesDefault, run_record)
        assert factory_calls == []
    finally:
        ConsumesDefault.drop_input = False


def test_fingerprint_rejects_serializers_that_mutate_the_live_final():
    class Mutating(C.Config):
        values: list[int]

        @field_serializer("values")
        def _sort_in_place(self, values: list[int]) -> list[int]:
            values.sort()
            return values

    final = Mutating(values=[2, 1])
    with pytest.raises(C.FingerprintError, match="mutated the live Config final"):
        C.fingerprint(final)
    assert final.values == [1, 2]


def test_record_rejects_nested_branch_provenance_outside_its_record_root():
    class Child(C.Config):
        source: int
        copied: int = C.interp(lambda context: context.current().source)

    class Parent(C.Config):
        child: Child

    final = Parent(child={"source": 3})
    assert C.provenance(final.child)["copied"][-1].reads == (("child.source", "3"),)
    with pytest.raises(C.FingerprintError, match="provenance is not self-contained"):
        C.record(final.child)
