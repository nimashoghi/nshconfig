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

    work = Hooked.config_draft()
    assert C.is_draft(work)
    assert calls == []

    work.value = 10
    assert work.value == 10
    assert calls == []

    final = work.config_finalize()
    assert final.value == 11
    assert calls == ["model_before", "field", "post_init", "model_after"]

    untouched = Hooked.config_draft()
    assert calls == ["model_before", "field", "post_init", "model_after"]
    assert untouched.config_finalize().value == 3
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

    work = PrivateState.config_draft()
    assert calls == []
    with pytest.raises(AttributeError):
        _ = work._cache
    with pytest.raises(AttributeError):
        work._cache = []

    final = work.config_finalize()
    assert final._cache == []
    assert calls == ["private factory"]
    final._cache = [1]
    assert final._cache == [1]
    del final._cache
    with pytest.raises(AttributeError):
        _ = final._cache


def test_python_copy_protocol_rejects_drafts_but_preserves_final_value_semantics() -> (
    None
):
    class Value(C.Config):
        items: list[int] = Field(default_factory=list)

    work = Value.config_draft()
    work.items.append(1)
    with pytest.raises(C.DraftError, match="cannot be shallow-copied"):
        copy.copy(work)
    with pytest.raises(C.DraftError, match="cannot be deep-copied"):
        copy.deepcopy(work)

    final = work.config_finalize()
    shallow = copy.copy(final)
    deep = copy.deepcopy(final)
    assert shallow == deep == final
    assert shallow.items is final.items
    assert deep.items is not final.items


def test_assignment_deletion_defaults_and_required_autovivification() -> None:
    class Leaf(C.Config):
        width: int
        label: str = "default"

    class Root(C.Config):
        leaf: Leaf
        retries: int = 3

    work = Root.config_draft()

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
    assert work.config_finalize() == Root(leaf=Leaf(width=512), retries=8)

    del work.retries
    assert work.retries == 3
    del work.leaf.width
    with pytest.raises(C.UnsetError, match=r"Leaf\.width is not set"):
        _ = work.leaf.width

    with pytest.raises(ValidationError) as caught:
        work.config_finalize()
    assert caught.value.errors()[0]["loc"] == ("leaf", "width")
    assert caught.value.errors()[0]["type"] == "missing"


def test_public_model_fields_set_is_a_detached_view() -> None:
    class Values(C.Config):
        value: int = 1

    work = Values.config_draft()
    work.value = 5
    exposed = work.model_fields_set
    exposed.clear()

    legacy = work.__fields_set__
    legacy.clear()

    assert work.model_fields_set == {"value"}
    assert work.config_finalize().value == 5


def test_mutating_materialized_collection_defaults_promotes_them_to_input() -> None:
    class Collections(C.Config):
        items: list[int] = []
        lookup: dict[str, list[int]] = {}
        flags: set[str] = set()

    work = Collections.config_draft()
    items = work.items
    lookup = work.lookup
    flags = work.flags
    assert work.model_fields_set == set()

    items.append(1)
    lookup.setdefault("chosen", []).append(2)
    flags.add("fast")

    assert work.model_fields_set == set()

    final = work.config_finalize()
    assert final.items == [1]
    assert final.lookup == {"chosen": [2]}
    assert final.flags == {"fast"}
    assert final.model_fields_set == {"items", "lookup", "flags"}


def test_untouched_data_aware_factory_is_recomputed_from_final_input() -> None:
    class Derived(C.Config):
        base: int = 1
        doubled: int = Field(default_factory=lambda data: data["base"] * 2)

    work = Derived.config_draft()
    work.base = 2
    assert work.doubled == 4
    assert "doubled" not in work.model_fields_set

    work.base = 7
    final = work.config_finalize()
    assert final.base == 7
    assert final.doubled == 14


def test_provisional_factory_does_not_reuse_deleted_or_pending_base_input() -> None:
    class Derived(C.Config):
        base: int = 1
        observed: list[int] = Field(default_factory=lambda data: [data["base"]])

    class Parent(C.Config):
        derived: Derived = Derived(base=2)

    deleted = Parent.config_draft()
    del deleted.derived.base
    assert deleted.derived.observed == [1]
    deleted.derived.observed.append(9)
    assert deleted.config_finalize().derived == Derived(base=1, observed=[1, 9])

    pending = Parent.config_draft()
    pending.derived.base = C.interp(lambda context: 3)
    with pytest.raises(C.UnsetError, match="cannot materialize provisional default"):
        _ = pending.derived.observed
    assert pending.config_finalize().derived == Derived(base=3, observed=[3])


def test_draft_equality_is_identity_and_hashing_is_final_only() -> None:
    class Value(C.Config):
        number: int = 1

    first = Value.config_draft()
    second = Value.config_draft()
    assert first == first
    assert first != second
    assert first != Value()

    left = Value()
    right = Value()
    assert left == right
    with pytest.raises(TypeError, match="unhashable"):
        hash(first)
    assert hash(left) == hash(right)


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


def test_model_copy_has_pydantic_semantics_on_finals_and_rejects_drafts() -> None:
    class Copyable(C.Config):
        count: int
        values: list[int]

    work = Copyable.config_draft()
    work.count = 1
    work.values = [2]
    final = work.config_finalize()
    copied = final.model_copy()
    deep = final.model_copy(deep=True)

    assert copied == deep == final
    assert copied is not final
    assert deep.values is not final.values

    unchecked = final.model_copy(update={"count": "not validated"})
    assert unchecked.count == "not validated"

    with pytest.raises(C.DraftError, match="drafts cannot be copied"):
        Copyable.config_draft().model_copy()

    with pytest.raises(TypeError, match=r"Config\.copy\(\) is unsafe"):
        final.copy(update={"count": 3})
    with pytest.raises(TypeError, match=r"Config\.copy\(\) is unsafe"):
        Copyable.config_draft().copy()


def test_existing_instances_cannot_be_reinitialized_in_place() -> None:
    class Value(C.Config):
        number: int

    final = Value(number=1)
    with pytest.raises(TypeError, match="cannot be reinitialized"):
        final.__init__(number=2)
    assert final.number == 1

    work = Value.config_draft()
    work.number = 3
    with pytest.raises(TypeError, match="cannot be reinitialized"):
        work.__init__(number=4)
    assert work.number == 3


def test_finalize_rejects_existing_finals_without_blessing_untracked_mutation() -> None:
    class Value(C.Config):
        values: list[int]

    final = Value(values=[1])
    final.values.append(2)
    with pytest.raises(C.DraftError, match="already final"):
        final.config_finalize()
    assert final.values == [1, 2]
