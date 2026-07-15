import copy
from decimal import Decimal
from typing import Any

import pytest
from pydantic import (
    AliasPath,
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from typing_extensions import Self, assert_type

import nshconfig as C


def test_method_lifecycle_keeps_normal_pydantic_construction() -> None:
    class Value(C.Config):
        required: int

    final = Value(required=1)
    assert not C.is_draft(final)

    work = Value.config_draft()
    assert_type(work, Value)
    assert C.is_draft(work)
    work.required = 2

    completed = work.config_finalize()
    assert_type(completed, Value)
    assert completed == Value(required=2)
    assert C.is_draft(work)

    with pytest.raises(C.DraftError, match="expects a draft"):
        final.config_finalize()


def test_config_default_projects_to_an_editable_child_draft() -> None:
    class Child(C.Config):
        x: int = 1
        y: int = 2

    class Parent(C.Config):
        child: Child = Child()

    work = Parent.config_draft()
    assert C.is_draft(work.child)
    work.child.x = 10

    final = work.config_finalize()
    assert final == Parent(child=Child(x=10, y=2))
    assert not C.is_draft(final.child)


def test_standard_pydantic_nested_construction_stays_standard() -> None:
    class Child(C.Config):
        x: int
        y: int

    class Parent(C.Config):
        child: Child

    final = Parent(child=Child(x=10, y=20))
    assert (final.child.x, final.child.y) == (10, 20)
    assert not C.is_draft(final)
    assert not C.is_draft(final.child)


def test_named_and_recursive_config_defaults_use_the_same_replay_rule() -> None:
    class Child(C.Config):
        value: int = 1

    default_child = Child(value=2)

    class Parent(C.Config):
        direct: Child = default_child
        sequence: list[Child] = [Child(value=3)]
        mapping: dict[str, Child] = {"nested": Child(value=4)}

    work = Parent.config_draft()
    assert C.is_draft(work.direct)
    assert C.is_draft(work.sequence[0])
    assert C.is_draft(work.mapping["nested"])

    work.direct.value = 20
    work.sequence[0].value = 30
    work.mapping["nested"].value = 40
    final = work.config_finalize()

    assert final.direct.value == 20
    assert final.sequence[0].value == 30
    assert final.mapping["nested"].value == 40


def test_unhashable_config_defaults_survive_pydantic_default_copying() -> None:
    class Child(C.Config):
        values: list[int] = []

    class Parent(C.Config):
        child: Child = Child()

    assert Parent().child.values == []
    work = Parent.config_draft()
    assert C.is_draft(work.child)
    work.child.values.append(1)

    assert work.config_finalize().child.values == [1]
    assert Parent().child.values == []


def test_deep_copies_retain_the_copied_constructor_recipe() -> None:
    class Box:
        pass

    class Child(C.Config, arbitrary_types_allowed=True):
        box: Box

    original = Child(box=Box())
    copied = copy.deepcopy(original)
    model_copied = original.model_copy(deep=True)

    assert copied.box is not original.box
    assert model_copied.box is not original.box

    class CopiedParent(C.Config):
        child: Child = copied

    class ModelCopiedParent(C.Config):
        child: Child = model_copied

    assert CopiedParent().child.box is copied.box
    assert ModelCopiedParent().child.box is model_copied.box

    class CyclicNode(BaseModel):
        child: Any = None

    node = CyclicNode()
    node.child = node

    class Atomic(C.Config):
        value: Any

    atomic_copy = copy.deepcopy(Atomic(value=node))

    class AtomicParent(C.Config):
        atom: Atomic = atomic_copy

    assert AtomicParent().atom.value is not node


def test_mapping_keys_and_set_members_stay_final_during_default_projection() -> None:
    class Child(C.Config):
        value: int = 1

    key = Child(value=2)

    class Parent(C.Config):
        mapping: dict[Child, Child] = {key: Child(value=3)}
        members: set[Child] = {Child(value=4)}

    work = Parent.config_draft()
    projected_key = next(iter(work.mapping))
    projected_value = work.mapping[projected_key]
    member = next(iter(work.members))

    assert not C.is_draft(projected_key)
    assert C.is_draft(projected_value)
    assert not C.is_draft(member)

    projected_value.value = 30
    final = work.config_finalize()
    assert next(iter(final.mapping.values())).value == 30


def test_explicit_final_assigned_to_a_draft_stays_final() -> None:
    class Child(C.Config):
        value: int = 1

    class Parent(C.Config):
        child: Child = Child()

    child = Child(value=5)
    work = Parent.config_draft()
    work.child = child

    assert work.child is child
    assert not C.is_draft(work.child)
    assert work.config_finalize().child == child


def test_untouched_nested_factory_reads_are_recomputed_but_edits_are_pinned() -> None:
    calls: list[int] = []

    class Child(C.Config):
        value: int

    def make_child() -> Child:
        value = len(calls) + 1
        calls.append(value)
        return Child(value=value)

    class Parent(C.Config):
        child: Child = Field(default_factory=make_child)

    untouched = Parent.config_draft()
    assert untouched.child.value == 1
    assert untouched.config_finalize().child.value == 2
    assert calls == [1, 2]

    edited = Parent.config_draft()
    edited.child.value = 9
    assert edited.config_finalize().child.value == 9
    assert calls == [1, 2, 3]


def test_direct_unbound_default_establishes_parent_interpolation_context() -> None:
    class Child(C.Config):
        copied: int = C.interp(lambda context: context.root(Parent).source)

    class Parent(C.Config):
        source: int = 3
        child: Child = Child()

    assert C.is_template(Parent.model_fields["child"].default)
    assert Parent().child.copied == 3

    work = Parent.config_draft()
    assert C.is_draft(work.child)
    work.source = 7
    assert work.config_finalize().child.copied == 7


def test_default_recipe_replays_canonical_field_validation() -> None:
    class Child(C.Config):
        source: int = 1
        copied: int = C.interp(lambda context: context.current().source)

        @field_validator("source")
        @classmethod
        def normalize_source(cls, value: int) -> int:
            return value + 1

    class Parent(C.Config):
        child: Child = Child(source=2)

    assert Parent().child == Child(source=2)

    work = Parent.config_draft()
    work.child.source = 10
    final = work.config_finalize()
    assert final.child.source == 11
    assert final.child.copied == 11


def test_replayable_default_edits_rewrite_alias_paths_without_stale_input() -> None:
    class Child(C.Config):
        value: int = Field(5, validation_alias=AliasPath("payload", "value"))

    class Parent(C.Config):
        child: Child = Child(payload={"extra": "ignored", "value": 7})

    edited = Parent.config_draft()
    edited.child.value = 9
    assert edited.config_finalize().child.value == 9

    deleted = Parent.config_draft()
    del deleted.child.value
    assert deleted.config_finalize().child.value == 5

    for payload in ([7, 8], (7, 8)):

        class IndexedChild(C.Config):
            first: int = Field(1, validation_alias=AliasPath("payload", 0))
            second: int = Field(2, validation_alias=AliasPath("payload", 1))

        class IndexedParent(C.Config):
            child: IndexedChild = IndexedChild(payload=payload)

        indexed_edited = IndexedParent.config_draft()
        indexed_edited.child.first = 9
        assert indexed_edited.config_finalize().child == IndexedChild(first=9, second=8)

        indexed_deleted = IndexedParent.config_draft()
        del indexed_deleted.child.first
        assert indexed_deleted.config_finalize().child == IndexedChild(
            first=1, second=8
        )


def test_mutated_or_recipe_less_finals_are_rejected_as_replayable_defaults() -> None:
    class Child(C.Config):
        values: list[int] = []

    stale = Child()
    stale.values.append(1)
    with pytest.raises(TypeError, match="without an intact constructor recipe"):

        class StaleParent(C.Config):
            child: Child = stale

    recipe_less = Child.model_validate({"values": []})
    with pytest.raises(TypeError, match="without an intact constructor recipe"):

        class RecipeLessParent(C.Config):
            child: Child = recipe_less

    class DecimalChild(C.Config):
        values: list[Decimal] = [Decimal("1")]

    changed_atom = DecimalChild()
    changed_atom.values[0] = Decimal("2")
    with pytest.raises(TypeError, match="without an intact constructor recipe"):

        class ChangedAtomParent(C.Config):
            child: DecimalChild = changed_atom


def test_model_after_mutation_uses_native_pydantic_semantics() -> None:
    class Value(C.Config):
        source: int
        copied: int = C.interp(lambda context: context.current().source)

        @model_validator(mode="after")
        def mutate(self) -> Self:
            object.__setattr__(self, "source", self.source + 1)
            return self

    final = Value(source=2)
    assert (final.source, final.copied) == (3, 2)


def test_revalidation_uses_current_concrete_values_not_default_recipes() -> None:
    class Child(C.Config):
        values: list[int] = []

    class Parent(C.Config):
        child: Child = Child()

    final = Parent()
    final.child.values.append("bad")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        Parent.model_validate(final)


def test_model_copy_matches_pydantic_on_finals_and_is_blocked_on_drafts() -> None:
    class Value(C.Config):
        number: int

    final = Value(number=1)
    unchecked = final.model_copy(update={"number": "bad"})
    assert unchecked.number == "bad"

    with pytest.raises(ValidationError):
        Value.model_validate(unchecked)

    with pytest.raises(C.DraftError, match="drafts cannot be copied"):
        Value.config_draft().model_copy()


def test_final_hash_matches_frozen_pydantic_value_hashing() -> None:
    class Hashable(C.Config):
        value: int
        labels: tuple[str, ...] = ()

    class Unhashable(C.Config):
        values: list[int] = []

    first = Hashable(value=1)
    second = Hashable(value=1)
    assert hash(first) == hash(second)
    assert {first, second} == {first}

    with pytest.raises(TypeError, match="unhashable"):
        hash(Hashable.config_draft())
    with pytest.raises(TypeError, match="unhashable"):
        hash(Unhashable())


def test_interpolation_context_blocks_set_mutation() -> None:
    class Values(C.Config):
        source: set[int] = {1}
        copied: int = C.interp(
            lambda context: context.current().source.add(2)  # type: ignore[attr-defined]
        )

    with pytest.raises(ValidationError, match="read-only interpolation container"):
        Values()


def test_field_validators_may_publish_shared_mutable_values() -> None:
    shared: list[int] = []

    class Values(C.Config):
        first: list[int]
        second: list[int]

        @field_validator("first", "second")
        @classmethod
        def share(cls, value: list[int]) -> list[int]:
            return shared

    final = Values(first=[], second=[])
    assert final.first is final.second is shared


def test_plain_mutators_accept_and_return_the_same_draft() -> None:
    class Model(C.Config):
        d_model: int = 128

    def resnet50(cfg: Model, *, d_model: int = 256) -> Model:
        cfg.d_model = d_model
        return cfg

    work = Model.config_draft()
    assert resnet50(work, d_model=512) is work
    assert work.config_finalize().d_model == 512


def test_removed_noncore_api_is_not_exported() -> None:
    for name in (
        "draft",
        "finalize",
        "Event",
        "Explanation",
        "explain",
        "fingerprint",
        "load_record",
        "provenance",
        "record",
        "RunRecord",
        "source",
    ):
        assert not hasattr(C, name)
