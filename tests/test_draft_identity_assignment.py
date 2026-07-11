"""Identity assignment and augmented-assignment behavior for draft containers."""

import pytest
from pydantic import Field

import nshconfig as C


def test_same_object_assignment_pins_a_provisional_factory_default() -> None:
    factory_calls = 0

    def make_items() -> list[int]:
        nonlocal factory_calls
        factory_calls += 1
        return [factory_calls]

    class Values(C.Config):
        items: list[int] = Field(default_factory=make_items)

    work = C.draft(Values)
    provisional = work.items
    work.items = provisional

    assert work.model_fields_set == {"items"}
    assert [event.kind for event in C.provenance(work)["items"]] == ["set"]

    final = C.finalize(work)
    assert final.items == [1]
    assert factory_calls == 1


def test_same_object_assignment_to_explicit_container_records_another_write() -> None:
    class Values(C.Config):
        items: list[int] = Field(default_factory=list)

    work = C.draft(Values)
    work.items = [1]
    assigned = work.items
    work.items = assigned

    assert [event.kind for event in C.provenance(work)["items"]] == ["set", "set"]
    assert C.finalize(work).items == [1]


def test_augmented_assignment_records_one_mutation_for_each_builtin_operator() -> None:
    class Collections(C.Config):
        added: list[int] = Field(default_factory=list)
        repeated: list[int] = Field(default_factory=lambda: [2])
        mapped: dict[str, int] = Field(default_factory=dict)
        unioned: set[int] = Field(default_factory=lambda: {1})
        intersected: set[int] = Field(default_factory=lambda: {1, 2})
        subtracted: set[int] = Field(default_factory=lambda: {1, 2})
        symmetric: set[int] = Field(default_factory=lambda: {1, 2})

    work = C.draft(Collections)
    work.added += [1]
    work.repeated *= 2
    work.mapped |= {"one": 1}
    work.unioned |= {2}
    work.intersected &= {2, 3}
    work.subtracted -= {2}
    work.symmetric ^= {2, 3}

    expected_operations = {
        "added": "extend",
        "repeated": "repeat",
        "mapped": "update",
        "unioned": "update",
        "intersected": "intersection_update",
        "subtracted": "difference_update",
        "symmetric": "symmetric_difference_update",
    }
    events = C.provenance(work)
    assert {
        field_name: [(event.kind, event.operation) for event in events[field_name]]
        for field_name in expected_operations
    } == {
        field_name: [("mutate", operation)]
        for field_name, operation in expected_operations.items()
    }

    final = C.finalize(work)
    assert final.added == [1]
    assert final.repeated == [2, 2]
    assert final.mapped == {"one": 1}
    assert final.unioned == {1, 2}
    assert final.intersected == {2}
    assert final.subtracted == {1}
    assert final.symmetric == {1, 3}


def test_augmented_assignment_acknowledgment_is_consumed_once() -> None:
    class Values(C.Config):
        items: list[int] = Field(default_factory=list)

    work = C.draft(Values)
    work.items += [1]
    assigned = work.items
    work.items = assigned

    assert [event.kind for event in C.provenance(work)["items"]] == ["mutate", "set"]


def test_failed_inplace_operation_does_not_acknowledge_a_later_assignment() -> None:
    class Values(C.Config):
        items: list[int] = Field(default_factory=list)

    def broken_values():
        yield 1
        raise RuntimeError("boom")

    work = C.draft(Values)
    assigned = work.items
    with pytest.raises(RuntimeError, match="boom"):
        assigned.__iadd__(broken_values())
    work.items = assigned

    assert work.items == []
    assert [event.kind for event in C.provenance(work)["items"]] == ["set"]
