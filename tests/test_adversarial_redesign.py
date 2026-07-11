"""Adversarial canaries for redesign invariants at trust boundaries."""

from collections.abc import (
    AsyncGenerator,
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Coroutine,
    Generator,
    Iterable,
    Iterator,
    Mapping,
)
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, ClassVar

import pytest
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    PrivateAttr,
    ValidationError,
    ValidationInfo,
    create_model,
    field_validator,
    model_validator,
)
from pydantic_core import CoreSchema, core_schema

import nshconfig as C
from nshconfig._src.provenance import provenance_token


def test_provenance_tokens_encode_container_boundaries_prefix_free() -> None:
    distinct_pairs = [
        ([[], []], [[[]]]),
        ({"a": {}, "b": {}}, {"a": {"b": {}}}),
        ({"a": {"b": 1}}, {"a.b": 1}),
    ]

    for left, right in distinct_pairs:
        left_token = provenance_token(left)
        right_token = provenance_token(right)
        assert left_token is not None
        assert right_token is not None
        assert left_token != right_token


def test_root_model_validate_preserves_history_without_laundering_nested_reuse() -> (
    None
):
    class Child(C.Config):
        source: int
        copied: int = C.interp(lambda context: context.current().source)

    class Parent(C.Config):
        child: Child

    work = C.draft(Child)
    with C.source("root input"):
        work.source = 3
    original = C.finalize(work)
    repeated = Child.model_validate(original)

    assert repeated == original
    assert repeated is not original
    assert repeated.model_fields_set == original.model_fields_set == {"source"}
    assert C.provenance(repeated) == C.provenance(original)

    with pytest.raises(ValidationError) as caught:
        Parent(child=repeated)
    assert caught.value.errors()[0]["type"] == "nshconfig_final_reuse"
    assert "interpolation history" in caught.value.errors()[0]["msg"]


def test_model_copy_rejects_every_update_mapping_without_testing_truthiness() -> None:
    class Value(C.Config):
        number: int

    class FalseyUpdate(Mapping[str, Any]):
        def __init__(self) -> None:
            self.truth_tests = 0

        def __getitem__(self, key: str) -> Any:
            if key != "number":
                raise KeyError(key)
            return 2

        def __iter__(self) -> Iterator[str]:
            yield "number"

        def __len__(self) -> int:
            return 1

        def __bool__(self) -> bool:
            self.truth_tests += 1
            return False

    final = Value(number=1)
    with pytest.raises(TypeError, match="ambiguous for interpolation and provenance"):
        final.model_copy(update={})

    update = FalseyUpdate()
    assert len(update) == 1
    with pytest.raises(TypeError, match="ambiguous for interpolation and provenance"):
        final.model_copy(update=update)
    assert update.truth_tests == 0


@pytest.mark.parametrize(
    "annotation",
    [
        AsyncIterable[int],
        AsyncIterator[int],
        AsyncGenerator[int, None],
        Awaitable[int],
        Coroutine[Any, Any, int],
        Generator[int, None, None],
        Iterator[int],
    ],
)
def test_lazy_annotations_are_rejected_when_the_config_class_is_defined(
    annotation: Any,
) -> None:
    with pytest.raises(TypeError, match="unsupported container annotation"):
        type(
            "InvalidLazyAnnotation",
            (C.Config,),
            {"__annotations__": {"values": annotation}},
        )


def test_custom_iterables_are_rejected_but_atomic_python_models_are_allowed() -> None:
    class CustomIterable(Iterable[int]):
        def __iter__(self) -> Iterator[int]:
            return iter(())

    with pytest.raises(
        TypeError, match="unsupported container annotation.*CustomIterable"
    ):
        type(
            "InvalidCustomIterable",
            (C.Config,),
            {
                "__annotations__": {"values": CustomIterable},
                "model_config": ConfigDict(arbitrary_types_allowed=True),
            },
        )

    class Plain(BaseModel):
        value: int

    @dataclass
    class Point:
        value: int

    class Choice(Enum):
        one = 1

    class AtomicValues(C.Config):
        plain: Plain
        point: Point
        choice: Choice

    final = AtomicValues(
        plain=Plain(value=1),
        point=Point(value=2),
        choice=Choice.one,
    )
    assert (final.plain.value, final.point.value, final.choice) == (1, 2, Choice.one)

    class RuntimeValue(C.Config):
        value: Any

    with pytest.raises(ValidationError, match="unsupported or lazy container"):
        RuntimeValue(value=CustomIterable())


def test_model_post_init_may_only_assign_declared_private_attributes() -> None:
    class Declared(C.Config):
        value: int
        _cache: list[int] = PrivateAttr(default_factory=list)

        def model_post_init(self, context: Any) -> None:
            self._cache = [self.value]

    assert Declared(value=4)._cache == [4]
    restored = C.load_record(Declared, C.record(Declared(value=4)))
    assert restored._cache == [4]

    class Undeclared(C.Config):
        value: int

        def model_post_init(self, context: Any) -> None:
            self._cache = [self.value]  # type: ignore[attr-defined]

    with pytest.raises(
        AttributeError, match="undeclared attribute '_cache'.*cannot be assigned"
    ):
        Undeclared(value=4)


def test_revalidation_detects_mapping_order_changes() -> None:
    class Reorders(C.Config):
        mutate: ClassVar[bool] = False
        values: dict[str, int]

        @field_validator("values")
        @classmethod
        def reorder(cls, value: dict[str, int]) -> dict[str, int]:
            if cls.mutate:
                first = next(iter(value))
                item = value.pop(first)
                value[first] = item
            return value

    final = Reorders(values={"first": 1, "second": 2})
    Reorders.mutate = True
    try:
        with pytest.raises(ValidationError) as caught:
            Reorders.model_validate(final)
    finally:
        Reorders.mutate = False
    assert caught.value.errors()[0]["type"] == "nshconfig_revalidation_change"


def test_revalidation_detects_mutable_identity_hashed_set_and_key_state() -> None:
    class IdentityHashed:
        __hash__ = object.__hash__

        def __init__(self, state: int) -> None:
            self.state = state

    class SetState(C.Config, arbitrary_types_allowed=True):
        mutate: ClassVar[bool] = False
        values: set[IdentityHashed]

        @field_validator("values")
        @classmethod
        def mutate_member(cls, value: set[IdentityHashed]) -> set[IdentityHashed]:
            if cls.mutate:
                next(iter(value)).state += 1
            return value

    class KeyState(C.Config, arbitrary_types_allowed=True):
        mutate: ClassVar[bool] = False
        values: dict[IdentityHashed, int]

        @field_validator("values")
        @classmethod
        def mutate_key(
            cls, value: dict[IdentityHashed, int]
        ) -> dict[IdentityHashed, int]:
            if cls.mutate:
                next(iter(value)).state += 1
            return value

    set_final = SetState(values={IdentityHashed(1)})
    key_final = KeyState(values={IdentityHashed(1): 2})
    for config_type, final in ((SetState, set_final), (KeyState, key_final)):
        config_type.mutate = True
        try:
            with pytest.raises(ValidationError) as caught:
                config_type.model_validate(final)
        finally:
            config_type.mutate = False
        assert caught.value.errors()[0]["type"] == (
            "nshconfig_revalidation_unsafe_atom"
        )
    assert next(iter(set_final.values)).state == 1
    assert next(iter(key_final.values)).state == 1


def test_revalidation_detects_bytearray_mutation() -> None:
    class MutableBytes(C.Config):
        mutate: ClassVar[bool] = False
        value: Any

        @field_validator("value")
        @classmethod
        def mutate_bytes(cls, value: Any) -> Any:
            if cls.mutate:
                value.extend(b"x")
            return value

    final = MutableBytes(value=bytearray(b"a"))
    MutableBytes.mutate = True
    try:
        with pytest.raises(ValidationError) as caught:
            MutableBytes.model_validate(final)
    finally:
        MutableBytes.mutate = False
    assert caught.value.errors()[0]["type"] == "nshconfig_revalidation_change"
    assert final.value == bytearray(b"a")


def test_field_mutation_is_detected_before_a_model_validator_can_revert_it() -> None:
    class AbaMutation(C.Config):
        after_calls: ClassVar[int] = 0
        items: list[int]
        trigger: int

        @field_validator("trigger")
        @classmethod
        def mutate_published_field(cls, value: int, info: ValidationInfo) -> int:
            info.data["items"].append(99)
            return value

        @model_validator(mode="after")
        def revert_mutation(self) -> "AbaMutation":
            type(self).after_calls += 1
            self.items.pop()
            return self

    with pytest.raises(ValidationError) as caught:
        AbaMutation(items=[], trigger=1)
    assert caught.value.errors()[0]["type"] == "nshconfig_model_mutation"
    assert AbaMutation.after_calls == 0


def test_mutable_atomic_defaults_are_not_exposed_as_provisional_draft_values() -> None:
    class Box:
        def __init__(self) -> None:
            self.values: list[int] = []

    class Choice(Enum):
        one = 1

    class MutableClass:
        pass

    class MutableTimezone(tzinfo):
        def __init__(self) -> None:
            self.offset = 0

        def utcoffset(self, value: datetime | None) -> timedelta:
            return timedelta(hours=self.offset)

        def dst(self, value: datetime | None) -> timedelta:
            return timedelta(0)

        def tzname(self, value: datetime | None) -> str:
            return "mutable"

    class Defaults(C.Config, arbitrary_types_allowed=True):
        box: Box = Box()
        choice: Choice = Choice.one
        kind: type = MutableClass
        window: slice = slice(Box(), None)
        timestamp: datetime = datetime(2025, 1, 1, tzinfo=MutableTimezone())

    work = C.draft(Defaults)
    for name in ("box", "choice", "kind", "window", "timestamp"):
        with pytest.raises(C.UnsetError, match="atomic provisional default"):
            getattr(work, name)


def test_record_rejects_stale_provenance_for_inspectable_atomic_values() -> None:
    class Box:
        def __init__(self, value: int) -> None:
            self.value = value

        def __repr__(self) -> str:
            return "Box(<constant>)"

        @classmethod
        def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> CoreSchema:
            del source, handler
            return core_schema.no_info_after_validator_function(
                cls,
                core_schema.int_schema(),
                serialization=core_schema.plain_serializer_function_ser_schema(
                    lambda box: box.value,
                    return_schema=core_schema.int_schema(),
                    when_used="json",
                ),
            )

    class Value(C.Config):
        box: Box
        copied: int = C.interp(lambda context: context.current().box.value)

    final = Value(box=1)
    final.box.value = 2
    with pytest.raises(C.FingerprintError, match="provenance is not self-contained"):
        C.record(final)


def test_record_rejects_mutation_across_values_and_provenance_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nshconfig._src.records as record_module

    class Value(C.Config):
        items: list[int]

    final = Value(items=[1])
    original = record_module.provenance

    def mutating_provenance(config: BaseModel) -> dict[str, tuple[C.Event, ...]]:
        final.items.append(2)
        return original(config)

    monkeypatch.setattr(record_module, "provenance", mutating_provenance)
    with pytest.raises(C.FingerprintError, match="changed while.*recorded"):
        C.record(final)


def test_schema_fingerprint_preserves_constraints_on_a_field_named_title() -> None:
    lower = create_model(
        "SameTitleSchema",
        __base__=C.Config,
        __module__=__name__,
        title=(Annotated[int, Field(ge=0)], ...),
    )
    higher = create_model(
        "SameTitleSchema",
        __base__=C.Config,
        __module__=__name__,
        title=(Annotated[int, Field(ge=10)], ...),
    )

    lower_record = C.record(lower(title=12))
    higher_record = C.record(higher(title=12))
    assert lower_record.config_type == higher_record.config_type
    assert lower_record.values == higher_record.values
    assert lower_record.schema_fingerprint != higher_record.schema_fingerprint


def test_custom_json_schema_failures_use_public_record_errors() -> None:
    class SchemaBomb:
        fail: ClassVar[bool] = False

        def __get_pydantic_json_schema__(
            self, schema: CoreSchema, handler: GetJsonSchemaHandler
        ) -> dict[str, Any]:
            if type(self).fail:
                raise RuntimeError("schema exploded")
            return handler(schema)

    bomb = SchemaBomb()

    class Value(C.Config):
        value: Annotated[int, bomb]

    final = Value(value=1)
    run_record = C.record(final)
    SchemaBomb.fail = True
    try:
        with pytest.raises(C.FingerprintError, match="could not produce.*JSON schema"):
            C.fingerprint(final)
        with pytest.raises(C.RecordError, match="could not derive.*JSON schema"):
            C.load_record(Value, run_record)
    finally:
        SchemaBomb.fail = False


def test_instance_dict_cannot_shadow_a_slot_that_contains_a_draft() -> None:
    class Child(C.Config):
        value: int = 1

    class Box:
        __slots__ = ("hidden", "__dict__")

    class Root(C.Config, arbitrary_types_allowed=True):
        value: Any

    box = Box()
    box.hidden = C.draft(Child)
    box.__dict__["hidden"] = 0

    with pytest.raises(ValidationError) as caught:
        Root(value=box)
    error = caught.value.errors()[0]
    assert error["type"] == "nshconfig_pending_draft"
    assert "stored slot" in error["msg"]


def test_run_records_reject_noncanonical_provenance_paths_and_event_order() -> None:
    class Value(C.Config):
        source: int
        copied: int = C.interp(lambda context: context.current().source)

    work = C.draft(Value)
    work.source = 3
    run_record = C.record(C.finalize(work))

    noncanonical_owner = run_record.to_dict()
    owner_provenance = noncanonical_owner["provenance"]
    assert isinstance(owner_provenance, dict)
    owner_provenance["['copied']"] = owner_provenance.pop("copied")
    with pytest.raises(C.RecordError, match="provenance path.*is not canonical"):
        C.load_record(Value, noncanonical_owner)

    noncanonical_read = run_record.to_dict()
    read_provenance = noncanonical_read["provenance"]
    assert isinstance(read_provenance, dict)
    copied_events = read_provenance["copied"]
    assert isinstance(copied_events, list)
    copied_event = copied_events[-1]
    assert isinstance(copied_event, dict)
    reads = copied_event["reads"]
    assert isinstance(reads, list)
    assert isinstance(reads[0], list)
    reads[0][0] = "['source']"
    with pytest.raises(C.RecordError, match="provenance read.*is not canonical"):
        C.load_record(Value, noncanonical_read)

    interpolate_not_last = run_record.to_dict()
    order_provenance = interpolate_not_last["provenance"]
    assert isinstance(order_provenance, dict)
    order_events = order_provenance["copied"]
    assert isinstance(order_events, list)
    source_events = order_provenance["source"]
    assert isinstance(source_events, list)
    assert isinstance(source_events[0], dict)
    order_events.append(dict(source_events[0]))
    with pytest.raises(C.RecordError, match="interpolation must be the final"):
        C.load_record(Value, interpolate_not_last)


def test_nested_config_interpolation_exposes_declared_fields_only() -> None:
    class Child(C.Config):
        value: int
        _cache: int = PrivateAttr(default=7)

    class ReadsPrivate(C.Config):
        child: Child
        copied: int = C.interp(lambda context: context.current().child._cache)

    with pytest.raises(ValidationError, match="no declared field '_cache'"):
        ReadsPrivate(child={"value": 1})


def test_context_is_agent_created_and_selectors_reject_ambiguous_inputs() -> None:
    with pytest.raises(TypeError, match="created only while interp"):
        C.Context()

    class InvalidLevel(C.Config):
        value: int = C.interp(lambda context: context.parent(True).value)

    with pytest.raises(ValidationError, match=r"parent\(\) expects a Config class"):
        InvalidLevel()


def test_interpolation_rejects_lazy_results_instead_of_consuming_them() -> None:
    class LazyResult(C.Config):
        values: list[int] = C.interp(lambda context: iter((1, 2)))

    with pytest.raises(ValidationError, match="unsupported or lazy container"):
        LazyResult()


def test_set_provenance_and_record_bytes_are_stable_across_loading() -> None:
    class SetRead(C.Config):
        source: set[str]
        count: int = C.interp(lambda context: len(context.current().source))

    original = SetRead(source={"gamma", "alpha", "beta"})
    run_record = C.record(original)
    restored = C.load_record(SetRead, run_record)

    assert C.provenance(restored) == C.provenance(original)
    assert C.record(restored) == run_record


def test_source_never_enables_legacy_postponed_annotations() -> None:
    source_root = Path(__file__).parents[1] / "src"
    offenders = [
        str(path.relative_to(source_root))
        for path in sorted(source_root.rglob("*.py"))
        if "from __future__ import annotations" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
