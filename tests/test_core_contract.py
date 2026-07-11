"""Public contract for Config drafts, finals, and supported value graphs."""

import heapq
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from pydantic import field_validator, model_validator
from pydantic_core import PydanticSerializationError
from typing_extensions import override

import nshconfig as C


def test_draft_construction_skips_every_pydantic_hook_until_finalize():
    calls: list[str] = []

    def make_generated() -> int:
        calls.append("factory")
        return 10

    class Probe(C.Config):
        required: int
        generated: int = Field(default_factory=make_generated)

        @model_validator(mode="before")
        @classmethod
        def before_model(cls, value: Any) -> Any:
            calls.append("model_before")
            return value

        @field_validator("required")
        @classmethod
        def validate_required(cls, value: int) -> int:
            calls.append("field")
            return value

        @override
        def model_post_init(self, context: Any) -> None:
            calls.append("post_init")

        @model_validator(mode="after")
        def after_model(self) -> "Probe":
            calls.append("model_after")
            return self

    work = C.draft(Probe)

    assert isinstance(work, Probe)
    assert C.is_draft(work)
    assert calls == []

    # Draft assignment is composition, not validation.
    work.required = "3"  # type: ignore[assignment]
    assert calls == []
    work.required = 3

    final = C.finalize(work)

    assert not C.is_draft(final)
    assert (final.required, final.generated) == (3, 10)
    assert calls == ["model_before", "field", "factory", "post_init", "model_after"]

    with pytest.raises(TypeError):
        C.draft(Probe, required=3)  # type: ignore[call-arg]


def test_unknown_writes_are_atomic_and_provenance_never_trusts_repr():
    class BadRepr:
        @override
        def __repr__(self) -> str:
            raise RuntimeError("repr exploded")

    class Probe(C.Config):
        value: Any = None

    work = C.draft(Probe)
    before = C.provenance(work)

    with pytest.raises(
        AttributeError, match="has no field 'vlaue'.*did you mean 'value'"
    ):
        work.vlaue = 1  # type: ignore[attr-defined]

    assert C.provenance(work) == before
    assert "vlaue" not in object.__getattribute__(work, "__dict__")

    value = BadRepr()
    work.value = value
    assert work.value is value
    event = C.provenance(work)["value"][-1]
    assert event.kind == "set"
    assert event.value == "<repr unavailable: RuntimeError>"


def test_required_config_fields_autovivify_but_other_required_fields_do_not():
    class Leaf(C.Config):
        width: int

    class Root(C.Config):
        leaf: Leaf
        count: int
        optional_leaf: Leaf | None

    work = C.draft(Root)
    leaf = work.leaf

    assert isinstance(leaf, Leaf)
    assert C.is_draft(leaf)
    assert "leaf" not in work.__pydantic_fields_set__

    leaf.width = 7
    work.count = 2
    work.optional_leaf = None
    final = C.finalize(work)
    assert final.leaf.width == 7

    other = C.draft(Root)
    with pytest.raises(C.UnsetError, match="Root.count is not set"):
        _ = other.count
    with pytest.raises(C.UnsetError, match="Root.optional_leaf is not set"):
        _ = other.optional_leaf


def test_explicit_interpolation_marker_is_pending_when_read_from_a_draft():
    class Values(C.Config):
        source: int = 2
        copied: int = 0

    work = C.draft(Values)
    work.copied = C.interp(lambda context: context.current().source)

    with pytest.raises(C.UnsetError, match="pending interpolation"):
        _ = work.copied
    assert C.finalize(work).copied == 2


def test_provisional_defaults_are_recomputed_unless_the_draft_mutates_them():
    factory_calls = 0

    def make_items() -> list[int]:
        nonlocal factory_calls
        factory_calls += 1
        return []

    def derive_double(data: dict[str, Any]) -> int:
        return data["base"] * 2

    class Defaults(C.Config):
        base: int = 2
        doubled: int = Field(default_factory=derive_double)
        items: list[int] = Field(default_factory=make_items)

    work = C.draft(Defaults)
    work.base = 3

    provisional_items = work.items
    assert provisional_items == []
    assert factory_calls == 1
    assert work.doubled == 6

    # Materialized-but-untouched values are only an interactive view. Pydantic
    # recomputes them from the final validated inputs.
    work.base = 4
    final = C.finalize(work)
    assert final.doubled == 8
    assert final.items == []
    assert final.items is not provisional_items
    assert factory_calls == 2

    pinned = C.draft(Defaults)
    pinned.items.append(9)
    assert C.finalize(pinned).items == [9]
    assert factory_calls == 3


def test_builtin_container_mutations_pin_values_and_record_operations():
    class Collections(C.Config):
        numbers: list[int] = Field(default_factory=list)
        groups: dict[str, list[int]] = Field(default_factory=dict)
        tags: set[str] = Field(default_factory=set)

    work = C.draft(Collections)
    work.numbers.extend([1, 2])
    work.groups["first"] = []
    work.groups["first"].append(3)
    work.tags.add("train")

    final = C.finalize(work)
    assert final.numbers == [1, 2]
    assert final.groups == {"first": [3]}
    assert final.tags == {"train"}

    events = C.provenance(final)
    assert [(event.kind, event.operation) for event in events["numbers"]] == [
        ("mutate", "extend")
    ]
    assert [(event.kind, event.operation) for event in events["groups"]] == [
        ("mutate", "setitem"),
        ("mutate", "append"),
    ]
    assert [(event.kind, event.operation) for event in events["tags"]] == [
        ("mutate", "add")
    ]


def test_augmented_container_assignment_records_only_the_in_place_operation():
    class Collections(C.Config):
        numbers: list[int] = Field(default_factory=list)
        lookup: dict[str, int] = Field(default_factory=dict)
        tags: set[int] = Field(default_factory=set)

    work = C.draft(Collections)
    work.numbers += [1]
    work.lookup |= {"one": 1}
    work.tags |= {1}

    events = C.provenance(work)
    assert [(event.kind, event.operation) for event in events["numbers"]] == [
        ("mutate", "extend")
    ]
    assert [(event.kind, event.operation) for event in events["lookup"]] == [
        ("mutate", "update")
    ]
    assert [(event.kind, event.operation) for event in events["tags"]] == [
        ("mutate", "update")
    ]


def test_failed_container_mutations_are_atomic_and_create_no_provenance():
    class Collections(C.Config):
        numbers: list[int] = Field(default_factory=list)
        tags: set[int] = Field(default_factory=set)
        values: dict[Any, int] = Field(default_factory=dict)

    def broken() -> Any:
        yield 1
        raise RuntimeError("boom")

    work = C.draft(Collections)
    with pytest.raises(RuntimeError, match="boom"):
        work.numbers.extend(broken())
    with pytest.raises(RuntimeError, match="boom"):
        work.tags.update(broken())

    class HostileKey:
        def __hash__(self) -> int:
            return 42

        def __eq__(self, other: object) -> bool:
            raise RuntimeError("key comparison failed")

    work.values = {HostileKey(): 0}
    values_before = dict(work.values)
    values_events = C.provenance(work)["values"]
    with pytest.raises(RuntimeError, match="key comparison failed"):
        work.values.update({"ok": 1, HostileKey(): 2})

    def bad_key(value: int) -> int:
        if value == 2:
            raise RuntimeError("bad sort key")
        return value

    work.numbers.extend([2, 1])
    before_events = C.provenance(work)["numbers"]
    with pytest.raises(RuntimeError, match="bad sort key"):
        work.numbers.sort(key=bad_key)

    assert work.numbers == [2, 1]
    assert work.tags == set()
    assert list(work.values.items()) == list(values_before.items())
    assert C.provenance(work)["numbers"] == before_events
    assert C.provenance(work)["values"] == values_events
    assert "tags" not in C.provenance(work)
    assert C.finalize(work).numbers == [2, 1]


def test_detached_tracked_containers_cannot_corrupt_current_provenance() -> None:
    class Values(C.Config):
        numbers: list[int] = []

    work = C.draft(Values)
    work.numbers = [1]
    stale = work.numbers
    del work.numbers
    stale.append(2)

    assert work.model_fields_set == set()
    assert [event.kind for event in C.provenance(work)["numbers"]] == ["set", "delete"]
    assert C.finalize(work).numbers == []

    work.numbers = [9]
    stale.append(3)
    assert C.finalize(work).numbers == [9]
    assert C.provenance(work)["numbers"][-1].value == "[9]"


def test_c_level_container_mutation_cannot_silently_bypass_tracking() -> None:
    class Values(C.Config):
        numbers: list[int] = []

    work = C.draft(Values)
    heapq.heappush(work.numbers, 1)

    with pytest.raises(C.DraftError, match="mutated through an untracked operation"):
        work.numbers.append(2)
    with pytest.raises(C.DraftError, match="mutated through an untracked operation"):
        C.finalize(work)


def test_atomic_provisional_defaults_must_be_assigned_before_mutation():
    @dataclass
    class Box:
        items: list[int] = dataclass_field(default_factory=list)

        def __post_init__(self) -> None:
            self.cache = {"ready": True}

    class Child(BaseModel):
        items: list[int] = Field(default_factory=list)

    class Root(C.Config):
        box: Box = Field(default_factory=Box)
        child: Child = Field(default_factory=Child)

    work = C.draft(Root)
    with pytest.raises(C.UnsetError, match="atomic provisional default"):
        _ = work.box
    with pytest.raises(C.UnsetError, match="atomic provisional default"):
        _ = work.child

    work.box = Box()
    work.child = Child()
    assert work.box.cache == {"ready": True}  # type: ignore[attr-defined]
    work.box.items.append(1)
    work.child.items.append(2)

    assert work.model_fields_set == {"box", "child"}
    final = C.finalize(work)
    assert final.box.items == [1]
    assert final.child.items == [2]
    assert C.provenance(final)["box"][-1].kind == "set"
    assert C.provenance(final)["child"][-1].kind == "set"


def test_explicit_arbitrary_values_remain_atomic_draft_identity_values():
    @dataclass
    class Box:
        value: int

    class Plain(BaseModel):
        value: int

    class Root(C.Config):
        box: Box
        plain: Plain

    box = Box(1)
    plain = Plain(value=2)
    work = C.draft(Root)
    work.box = box
    work.plain = plain

    assert work.box is box
    assert work.plain is plain

    class CustomList(list[int]):
        pass

    with pytest.raises(TypeError, match="unsupported container annotation"):

        class WithCustomContainer(C.Config, arbitrary_types_allowed=True):
            values: CustomList


def test_undeclared_private_attribute_typos_are_rejected() -> None:
    class Value(C.Config):
        number: int = 1

    for value in (C.draft(Value), Value()):
        with pytest.raises(AttributeError, match="cannot be assigned"):
            value._typo = 2
        assert "_typo" not in object.__getattribute__(value, "__dict__")


def test_common_immutable_scalar_defaults_are_safe_to_read_provisionally() -> None:
    class Defaults(C.Config):
        output: Path = Path("runs/default")
        scale: Decimal = Decimal("1.25")
        started: datetime = datetime(2025, 1, 1, tzinfo=timezone.utc)
        interval: timedelta = timedelta(seconds=3)

    work = C.draft(Defaults)
    assert work.output == Path("runs/default")
    assert work.scale == Decimal("1.25")
    assert work.started == datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert work.interval == timedelta(seconds=3)
    assert work.model_fields_set == set()


def test_finalize_is_a_non_destructive_sweep_over_the_original_draft():
    class Sweep(C.Config):
        width: int = 1

    work = C.draft(Sweep)
    work.width = 10
    first = C.finalize(work)

    work.width = 20
    second = C.finalize(work)

    assert C.is_draft(work)
    assert first.width == 10
    assert second.width == 20


def test_equality_has_value_semantics_only_for_finals_and_all_configs_are_unhashable():
    class Value(C.Config):
        number: int = 0

    left_draft = C.draft(Value)
    right_draft = C.draft(Value)
    left_draft.number = 3
    with C.source("different history"):
        right_draft.number = 1
        right_draft.number = 3

    assert left_draft == left_draft
    assert left_draft != right_draft

    left = C.finalize(left_draft)
    right = C.finalize(right_draft)
    assert left == right
    assert C.provenance(left) != C.provenance(right)

    for value in (left_draft, left, right):
        with pytest.raises(TypeError, match="unhashable"):
            hash(value)


def test_equality_is_total_for_opaque_values_with_hostile_comparison() -> None:
    class Hostile:
        def __eq__(self, other: object) -> bool:
            raise RuntimeError("comparison unavailable")

    class Value(C.Config, arbitrary_types_allowed=True):
        item: Hostile

    item = Hostile()
    assert Value(item=item) == Value(item=item)
    assert (Value(item=Hostile()) == Value(item=Hostile())) is False


def test_final_equality_uses_totalized_ordinary_python_value_equality() -> None:
    class Comparable(C.Config):
        signed_zero: float
        amount: Decimal
        occurred_at: datetime

    left = Comparable(
        signed_zero=-0.0,
        amount=Decimal("1.0"),
        occurred_at=datetime(2025, 1, 1, 12, tzinfo=timezone.utc),
    )
    right = Comparable(
        signed_zero=0.0,
        amount=Decimal("1.00"),
        occurred_at=datetime(
            2025,
            1,
            1,
            13,
            tzinfo=timezone(timedelta(hours=1)),
        ),
    )

    assert left == right

    class Floating(C.Config):
        value: float

    assert Floating(value=float("nan")) != Floating(value=float("nan"))

    class AmbiguousTruth:
        def __bool__(self) -> bool:
            raise ValueError("truth value is ambiguous")

    class AmbiguousEquality:
        def __eq__(self, other: object) -> Any:
            del other
            return AmbiguousTruth()

    class Opaque(C.Config, arbitrary_types_allowed=True):
        value: AmbiguousEquality

    assert (
        Opaque(value=AmbiguousEquality()) == Opaque(value=AmbiguousEquality())
    ) is False


def test_finals_are_field_frozen_but_make_no_deep_immutability_claim():
    class Value(C.Config):
        number: int = 1
        items: list[int] = Field(default_factory=list)

    final = Value()
    with pytest.raises(ValidationError, match="frozen"):
        final.number = 2

    final.items.append(3)
    assert final.items == [3]


def test_nested_drafts_in_lists_and_mappings_are_collected_recursively_by_value():
    class Item(C.Config):
        value: int

    class Graph(C.Config):
        items: list[Item]
        named: dict[str, Item]

    shared = C.draft(Item)
    with C.source("shared item"):
        shared.value = 11

    work = C.draft(Graph)
    work.items = [shared]
    work.named = {"same": shared}

    final = C.finalize(work)
    from_list = final.items[0]
    from_mapping = final.named["same"]
    assert (from_list.value, from_mapping.value) == (11, 11)
    assert not C.is_draft(from_list)
    assert not C.is_draft(from_mapping)
    assert from_list == from_mapping
    assert from_list is not from_mapping

    events = C.provenance(final)
    assert events["items[0].value"][-1].label == "shared item"
    assert events["named.same.value"][-1].label == "shared item"


def test_direct_validation_expands_repeated_builtin_inputs_by_value():
    class Graph(C.Config):
        first: Any
        second: Any

    shared: list[int] = []
    final = Graph(first=shared, second=shared)

    assert final.first == final.second == []
    assert final.first is not final.second
    assert final.first is not shared


def test_repeated_immutable_value_objects_are_not_mistaken_for_mutable_aliases():
    class Color(Enum):
        RED = "red"

    @dataclass(frozen=True)
    class Point:
        x: int

    class Values(C.Config):
        first_color: Color
        second_color: Color
        first_point: Point
        second_point: Point

    point = Point(1)
    final = Values(
        first_color=Color.RED,
        second_color=Color.RED,
        first_point=point,
        second_point=point,
    )
    assert final.first_color is final.second_color is Color.RED
    assert final.first_point == final.second_point == point


def test_field_validators_cannot_publish_shared_mutable_values():
    shared: list[int] = []

    class Graph(C.Config):
        first: list[int]
        second: list[int]

        @field_validator("first", "second")
        @classmethod
        def return_shared(cls, value: list[int]) -> list[int]:
            return shared

    with pytest.raises(ValidationError) as caught:
        Graph(first=[], second=[])

    assert caught.value.errors()[0]["type"] == "nshconfig_alias"
    assert "also referenced" in caught.value.errors()[0]["msg"]


def test_cycles_and_nested_interpolation_markers_fail_with_their_graph_path():
    class Graph(C.Config):
        payload: Any

    cycle: list[Any] = []
    cycle.append(cycle)
    cyclic_work = C.draft(Graph)
    cyclic_work.payload = cycle

    with pytest.raises(ValueError, match=r"cycle found at Graph\.payload\[0\]"):
        C.finalize(cyclic_work)

    marker_work = C.draft(Graph)
    marker_work.payload = [C.interp(lambda context: 1)]
    with pytest.raises(
        ValueError,
        match=r"interp\(\) is only legal as a complete Config field value: Graph\.payload\[0\]",
    ):
        C.finalize(marker_work)


def test_unsafe_pydantic_construction_copy_and_serialization_paths_are_gated():
    class Value(C.Config):
        number: int

    with pytest.raises(TypeError, match=r"model_construct\(\) is unsafe"):
        Value.model_construct(number=1)

    final = Value(number=1)
    with pytest.raises(TypeError, match="ambiguous for interpolation and provenance"):
        final.model_copy(update={"number": 2})
    with pytest.raises(TypeError, match="ambiguous for interpolation and provenance"):
        final.model_copy(update={"number": "invalid"})

    work = C.draft(Value)
    work.number = 1
    with pytest.raises(C.DraftError, match="requires a completed Config final"):
        work.model_copy()
    with pytest.raises(ValidationError, match="draft cannot be validated"):
        Value.model_validate(work)
    with pytest.raises(C.DraftError, match="requires a completed Config final"):
        work.model_dump()
    with pytest.raises(C.DraftError, match="requires a completed Config final"):
        work.model_dump_json()

    adapter = TypeAdapter(Value)
    with pytest.raises(
        PydanticSerializationError, match="after validation has completed"
    ):
        adapter.dump_python(work)
    with pytest.raises(
        PydanticSerializationError, match="after validation has completed"
    ):
        TypeAdapter(list[Value]).dump_python([work])
    with pytest.raises(C.DraftError, match="requires a completed Config final"):
        dict(work)
    with pytest.raises(C.DraftError, match="requires a completed Config final"):
        list(work)

    assert dict(final) == {"number": 1}

    class Envelope(BaseModel):
        value: Value

    envelope = Envelope.model_construct(value=work)
    with pytest.raises(
        PydanticSerializationError, match="after validation has completed"
    ):
        TypeAdapter(Envelope).dump_python(envelope)


def test_projects_may_change_policy_but_not_lifecycle_invariants():
    class Lenient(C.Config):
        model_config = ConfigDict(strict=False)

    class Value(Lenient):
        number: int

    assert Value.model_validate({"number": "3"}).number == 3

    with pytest.raises(TypeError, match="overrides nshconfig lifecycle settings"):

        class Mutable(C.Config):
            model_config = ConfigDict(frozen=False)

            number: int


def test_provenance_returns_immutable_event_sequences_and_defensive_mappings():
    class Value(C.Config):
        number: int = 1

    work = C.draft(Value)
    with C.source("sweep:number"):
        work.number = 3
    del work.number

    snapshot = C.provenance(work)
    assert isinstance(snapshot["number"], tuple)
    assert [event.kind for event in snapshot["number"]] == ["set", "delete"]
    assert snapshot["number"][0].label == "sweep:number"

    snapshot.clear()
    assert [event.kind for event in C.provenance(work)["number"]] == ["set", "delete"]
