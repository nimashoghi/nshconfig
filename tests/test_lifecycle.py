"""The public draft-to-final lifecycle contract."""

import copy
from typing import Any

import pytest
from pydantic import (
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

import nshconfig as C


def test_draft_construction_and_writes_do_not_run_pydantic_hooks() -> None:
    calls: list[str] = []

    def make_value() -> int:
        calls.append("factory")
        return 2

    class Hooked(C.Config):
        value: int = Field(default_factory=make_value)

        @model_validator(mode="before")
        @classmethod
        def prepare_input(cls, value: Any) -> Any:
            calls.append("model_before")
            return value

        @field_validator("value")
        @classmethod
        def normalize_value(cls, value: int) -> int:
            calls.append("field")
            return value + 1

        def model_post_init(self, context: Any) -> None:
            calls.append("post_init")

        @model_validator(mode="after")
        def check_model(self) -> "Hooked":
            calls.append("model_after")
            return self

    work = C.draft(Hooked)
    assert C.is_draft(work)
    assert calls == []

    work.value = 10
    assert work.value == 10
    assert calls == []

    final = C.finalize(work)
    assert final.value == 11
    assert calls == ["model_before", "field", "post_init", "model_after"]

    untouched = C.draft(Hooked)
    assert calls == ["model_before", "field", "post_init", "model_after"]
    assert C.finalize(untouched).value == 3
    assert calls == [
        "model_before",
        "field",
        "post_init",
        "model_after",
        "model_before",
        "factory",
        "field",
        "post_init",
        "model_after",
    ]


def test_draft_construction_does_not_run_private_attribute_factories() -> None:
    calls: list[str] = []

    class PrivateState(C.Config):
        value: int = 1
        _cache: list[int] = PrivateAttr(
            default_factory=lambda: calls.append("private factory") or []
        )

    work = C.draft(PrivateState)
    assert calls == []
    with pytest.raises(AttributeError):
        _ = work._cache

    final = C.finalize(work)
    assert final._cache == []
    assert calls == ["private factory"]


def test_python_copy_protocol_rejects_drafts_but_preserves_final_value_semantics() -> (
    None
):
    class Value(C.Config):
        items: list[int] = Field(default_factory=list)

    work = C.draft(Value)
    work.items.append(1)
    with pytest.raises(C.DraftError, match="requires a completed Config final"):
        copy.copy(work)
    with pytest.raises(C.DraftError, match="requires a completed Config final"):
        copy.deepcopy(work)

    final = C.finalize(work)
    shallow = copy.copy(final)
    deep = copy.deepcopy(final)
    assert shallow == deep == final
    assert shallow.items is final.items
    assert deep.items is not final.items


def test_refinalizing_a_final_preserves_fields_set_and_exclude_unset_behavior() -> None:
    class Child(C.Config):
        required: int
        defaulted: int = 2

    class Root(C.Config):
        required: int
        child: Child
        defaulted: int = 3

    original = Root(required=1, child=Child(required=4))
    repeated = C.finalize(original)

    assert repeated == original
    assert (
        repeated.model_fields_set == original.model_fields_set == {"required", "child"}
    )
    assert (
        repeated.child.model_fields_set
        == original.child.model_fields_set
        == {"required"}
    )
    assert repeated.model_dump(exclude_unset=True) == original.model_dump(
        exclude_unset=True
    )


def test_assignment_deletion_defaults_and_required_autovivification() -> None:
    class Leaf(C.Config):
        width: int
        label: str = "default"

    class Root(C.Config):
        leaf: Leaf
        retries: int = 3

    work = C.draft(Root)

    assert work.retries == 3
    assert "retries" not in work.model_fields_set
    assert work.leaf is work.leaf
    assert C.is_draft(work.leaf)
    assert "leaf" not in work.model_fields_set
    assert work.leaf.label == "default"

    work.retries = 8
    work.leaf.width = 512
    assert work.model_fields_set == {"retries"}
    assert work.leaf.model_fields_set == {"width"}
    assert C.finalize(work) == Root(leaf=Leaf(width=512), retries=8)

    del work.retries
    assert work.retries == 3
    del work.leaf.width
    assert [event.kind for event in C.provenance(work)["retries"]] == ["set", "delete"]
    assert [event.kind for event in C.provenance(work)["leaf.width"]] == [
        "set",
        "delete",
    ]
    with pytest.raises(C.UnsetError, match=r"Leaf\.width is not set"):
        _ = work.leaf.width

    with pytest.raises(ValidationError) as caught:
        C.finalize(work)
    assert caught.value.errors()[0]["loc"] == ("leaf", "width")
    assert caught.value.errors()[0]["type"] == "missing"


def test_public_model_fields_set_is_a_detached_view() -> None:
    class Values(C.Config):
        value: int = 1

    work = C.draft(Values)
    work.value = 5
    exposed = work.model_fields_set
    exposed.clear()

    legacy = work.__fields_set__
    legacy.clear()

    assert work.model_fields_set == {"value"}
    assert C.finalize(work).value == 5


def test_mutating_materialized_collection_defaults_promotes_them_to_input() -> None:
    class Collections(C.Config):
        items: list[int] = []
        lookup: dict[str, list[int]] = {}
        flags: set[str] = set()

    work = C.draft(Collections)
    items = work.items
    lookup = work.lookup
    flags = work.flags
    assert work.model_fields_set == set()

    items.append(1)
    lookup.setdefault("chosen", []).append(2)
    flags.add("fast")

    assert work.model_fields_set == {"items", "lookup", "flags"}
    events = C.provenance(work)
    assert events["items"][-1].kind == "mutate"
    assert events["items"][-1].operation == "append"
    assert [event.operation for event in events["lookup"][-2:]] == [
        "setdefault",
        "append",
    ]
    assert events["flags"][-1].operation == "add"

    final = C.finalize(work)
    assert final.items == [1]
    assert final.lookup == {"chosen": [2]}
    assert final.flags == {"fast"}
    assert final.model_fields_set == {"items", "lookup", "flags"}
    final_events = C.provenance(final)
    assert final_events["items"][-1].operation == "append"
    assert [event.operation for event in final_events["lookup"][-2:]] == [
        "setdefault",
        "append",
    ]
    assert final_events["flags"][-1].operation == "add"


def test_untouched_data_aware_factory_is_recomputed_from_final_input() -> None:
    class Derived(C.Config):
        base: int = 1
        doubled: int = Field(default_factory=lambda data: data["base"] * 2)

    work = C.draft(Derived)
    work.base = 2
    assert work.doubled == 4
    assert "doubled" not in work.model_fields_set

    work.base = 7
    final = C.finalize(work)
    assert final.base == 7
    assert final.doubled == 14


def test_draft_equality_is_identity_and_every_config_is_unhashable() -> None:
    class Value(C.Config):
        number: int = 1

    first = C.draft(Value)
    second = C.draft(Value)
    assert first == first
    assert first != second
    assert first != Value()

    left = Value()
    right = Value()
    assert left == right
    with pytest.raises(TypeError, match="unhashable"):
        hash(first)
    with pytest.raises(TypeError, match="unhashable"):
        hash(left)


def test_finals_are_field_frozen_but_not_deeply_immutable() -> None:
    class Frozen(C.Config):
        count: int = 1
        values: list[int] = []

    final = Frozen()

    with pytest.raises(ValidationError) as assignment:
        final.count = 2
    assert assignment.value.errors()[0]["type"] == "frozen_instance"

    with pytest.raises(ValidationError) as deletion:
        del final.count
    assert deletion.value.errors()[0]["type"] == "frozen_instance"

    final.values.append(3)
    assert final.values == [3]


def test_model_copy_preserves_finals_but_rejects_updates_and_drafts() -> None:
    class Copyable(C.Config):
        count: int
        values: list[int]

    work = C.draft(Copyable)
    with C.source("copy source"):
        work.count = 1
    work.values = [2]
    final = C.finalize(work)
    copied = final.model_copy()
    deep = final.model_copy(deep=True)

    assert copied == deep == final
    assert copied is not final
    assert deep.values is not final.values
    assert C.provenance(copied) == C.provenance(final)

    with pytest.raises(TypeError, match="ambiguous for interpolation and provenance"):
        final.model_copy(update={"count": 2})

    with pytest.raises(C.DraftError, match="requires a completed Config final"):
        C.draft(Copyable).model_copy()

    with pytest.raises(TypeError, match=r"Config\.copy\(\) is unsafe"):
        final.copy(update={"count": 3})
    with pytest.raises(TypeError, match=r"Config\.copy\(\) is unsafe"):
        C.draft(Copyable).copy()


def test_existing_instances_cannot_be_reinitialized_in_place() -> None:
    class Value(C.Config):
        number: int

    final = Value(number=1)
    with pytest.raises(TypeError, match="cannot be reinitialized"):
        final.__init__(number=2)
    assert final.number == 1

    work = C.draft(Value)
    work.number = 3
    before = C.provenance(work)
    with pytest.raises(TypeError, match="cannot be reinitialized"):
        work.__init__(number=4)
    assert work.number == 3
    assert C.provenance(work) == before


def test_finalize_revalidates_a_final_by_value() -> None:
    class Revalidated(C.Config):
        values: list[int]

    original = Revalidated(values=[1, 2])
    checked = C.finalize(original)
    assert checked == original
    assert checked is not original
    assert checked.values is not original.values

    original.values.append("bad")  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as caught:
        C.finalize(original)
    error = caught.value.errors()[0]
    assert error["loc"] == ("values", 2)
    assert error["type"] == "int_type"


def test_finalize_preserves_provenance_and_rejects_non_idempotent_validators() -> None:
    class Stable(C.Config):
        value: int

    work = C.draft(Stable)
    with C.source("stable input"):
        work.value = 3
    original = C.finalize(work)
    checked = C.finalize(original)

    assert checked == original
    assert C.provenance(checked) == C.provenance(original)

    class Reversing(C.Config):
        values: list[int]

        @field_validator("values")
        @classmethod
        def reverse(cls, value: list[int]) -> list[int]:
            return list(reversed(value))

    final = Reversing(values=[1, 2])
    with pytest.raises(ValueError, match="revalidation changed"):
        C.finalize(final)


def test_finalize_does_not_trust_opaque_equality_or_in_place_validator_mutation() -> (
    None
):
    class Box:
        def __init__(self, value: int):
            self.value = value

        def __eq__(self, other: object) -> bool:
            return True

    class Replacing(C.Config, arbitrary_types_allowed=True):
        box: Box

        @field_validator("box")
        @classmethod
        def replace(cls, value: Box) -> Box:
            return Box(value.value + 1)

    replaced = Replacing(box=Box(0))
    with pytest.raises(ValidationError) as replacing_error:
        C.finalize(replaced)
    assert replacing_error.value.errors()[0]["type"] == (
        "nshconfig_revalidation_unsafe_atom"
    )
    assert replaced.box.value == 1

    class Mutating(C.Config, arbitrary_types_allowed=True):
        box: Box

        @field_validator("box")
        @classmethod
        def mutate(cls, value: Box) -> Box:
            value.value += 1
            return value

    mutated = Mutating(box=Box(0))
    assert mutated.box.value == 1
    with pytest.raises(ValidationError) as mutating_error:
        C.finalize(mutated)
    assert mutating_error.value.errors()[0]["type"] == (
        "nshconfig_revalidation_unsafe_atom"
    )
    assert mutated.box.value == 1


def test_draft_private_attribute_writes_and_deletions_are_rejected() -> None:
    class PrivateValue(C.Config):
        value: int
        _secret: int = PrivateAttr(default=1)

    work = C.draft(PrivateValue)
    work.value = 3

    with pytest.raises(C.DraftError, match="declared fields only"):
        work._secret = 9
    with pytest.raises(C.DraftError, match="private attributes cannot be deleted"):
        del work._secret

    assert C.finalize(work)._secret == 1
