"""Provenance records Python composition without owning live values."""

import pickle
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, ValidationError, create_model, field_validator

import nshconfig as C


class Optimizer(C.Config):
    learning_rate: float = 1e-3
    weight_decay: float = 0.0


class Run(C.Config):
    scale: int = 2
    optimizer: Optimizer
    effective_rate: float = C.interp(
        lambda context: context.current(Run).optimizer.learning_rate
        * context.current(Run).scale
    )


def set_learning_rate(work: Run) -> None:
    work.optimizer.learning_rate = 3e-4


def test_assignment_chain_captures_site_source_and_label():
    work = C.draft(Run)
    set_learning_rate(work)
    with C.source("sweep:learning-rate"):
        work.optimizer.learning_rate = 1e-4

    explanation = C.explain(C.finalize(work), "optimizer.learning_rate")

    assert explanation.current == "0.0001"
    assert [event.kind for event in explanation.events] == ["set", "set"]
    helper, sweep = explanation.events
    assert helper.function == "set_learning_rate"
    assert helper.value == "0.0003"
    assert Path(str(helper.file)).name == "test_provenance.py"
    assert helper.code == "work.optimizer.learning_rate = 3e-4"
    assert sweep.label == "sweep:learning-rate"
    assert "class default: 0.001" in str(explanation)


def test_delete_is_a_tombstone_and_reactivates_the_default():
    work = C.draft(Run)
    work.optimizer.learning_rate = 5e-4
    del work.optimizer.learning_rate

    explanation = C.explain(work, "optimizer.learning_rate")

    assert [event.kind for event in explanation.events] == ["set", "delete"]
    assert explanation.current == "<pending/unset>"
    assert C.finalize(work).optimizer.learning_rate == 1e-3


def test_container_mutations_are_first_class_events():
    class Layers(C.Config):
        widths: list[int] = [64]

    work = C.draft(Layers)
    work.widths.extend([128, 256])

    (event,) = C.explain(work, "widths").events
    assert event.kind == "mutate"
    assert event.operation == "extend"
    assert event.value == "[64, 128, 256]"
    assert C.finalize(work).widths == [64, 128, 256]


def test_interpolation_records_canonical_reads_and_recursive_causes():
    work = C.draft(Run)
    work.scale = 4
    work.optimizer.learning_rate = 2e-4

    explanation = C.explain(C.finalize(work), "effective_rate")

    (event,) = explanation.events
    assert event.kind == "interpolate"
    assert event.from_default
    assert dict(event.reads) == {
        "optimizer.learning_rate": "0.0002",
        "scale": "4",
    }
    assert {cause.path for cause in explanation.causes} == {
        "optimizer.learning_rate",
        "scale",
    }
    rendered = str(explanation)
    assert "because optimizer.learning_rate = 0.0002" in rendered
    assert "set to 0.0002" in rendered


def test_validator_reordering_preserves_child_lineage_and_canonical_dependencies():
    class Item(C.Config):
        name: str
        source: int
        copied: int = C.interp(lambda context: context.current().source)

    class Reordered(C.Config):
        items: list[Item]

        @field_validator("items")
        @classmethod
        def reverse_items(cls, value: list[Item]) -> list[Item]:
            return list(reversed(value))

    first = C.draft(Item)
    first.name = "first"
    with C.source("first-input"):
        first.source = 10

    second = C.draft(Item)
    second.name = "second"
    with C.source("second-input"):
        second.source = 20

    work = C.draft(Reordered)
    work.items = [first, second]
    final = C.finalize(work)

    assert [item.name for item in final.items] == ["second", "first"]
    events = C.provenance(final)
    assert events["items[0].source"][-1].label == "second-input"
    assert events["items[1].source"][-1].label == "first-input"
    assert events["items[0].copied"][-1].reads == (("items[0].source", "20"),)
    assert events["items[1].copied"][-1].reads == (("items[1].source", "10"),)

    explanation = C.explain(final, "items[0].copied")
    assert explanation.current == "20"
    assert [cause.path for cause in explanation.causes] == ["items[0].source"]
    assert explanation.causes[0].events[-1].label == "second-input"


def test_whole_values_membership_and_iteration_record_dependencies():
    class Child(C.Config):
        value: int
        numbers: list[int] = [5]

    class Reads(C.Config):
        child: Child
        values: list[int]
        names: dict[str, int]
        child_copy: Child = C.interp(lambda context: context.current().child)
        nested_first: int = C.interp(lambda context: context.current().child.numbers[0])
        values_copy: list[int] = C.interp(lambda context: context.current().values)
        contains_value: bool = C.interp(lambda context: 2 in context.current().values)
        contains_name: bool = C.interp(lambda context: "x" in context.current().names)
        total: int = C.interp(lambda context: sum(context.current().values))
        lookup: int = C.interp(lambda context: context.current().names.get("x", 0))
        position: int = C.interp(lambda context: context.current().values.index(2))
        count: int = C.interp(lambda context: context.current().values.count(2))
        keys: list[str] = C.interp(lambda context: list(context.current().names.keys()))
        name_values: list[int] = C.interp(
            lambda context: list(context.current().names.values())
        )
        name_items: dict[str, int] = C.interp(
            lambda context: dict(context.current().names.items())
        )
        values_equal: bool = C.interp(
            lambda context: context.current().values == [1, 2]
        )
        extended: list[int] = C.interp(lambda context: context.current().values + [3])
        repeated: list[int] = C.interp(lambda context: context.current().values * 2)
        merged: dict[str, int] = C.interp(
            lambda context: context.current().names | {"y": 5}
        )

    final = Reads(child={"value": 3}, values=[1, 2], names={"x": 4})
    events = C.provenance(final)

    assert events["child_copy"][-1].reads[0][0] == "child"
    assert events["nested_first"][-1].reads == (("child.numbers[0]", "5"),)
    assert events["values_copy"][-1].reads[0][0] == "values"
    assert events["contains_value"][-1].reads == (("values", "[1, 2]"),)
    assert events["contains_name"][-1].reads == (("names", "{'x': 4}"),)
    assert events["total"][-1].reads == (
        ("values", "[1, 2]"),
        ("values[0]", "1"),
        ("values[1]", "2"),
    )
    assert events["lookup"][-1].reads == (("names.x", "4"),)
    assert events["position"][-1].reads == (
        ("values[0]", "1"),
        ("values[1]", "2"),
    )
    assert events["count"][-1].reads == (
        ("values", "[1, 2]"),
        ("values[0]", "1"),
        ("values[1]", "2"),
    )
    assert events["keys"][-1].reads == (("names", "{'x': 4}"),)
    assert events["name_values"][-1].reads == (
        ("names", "{'x': 4}"),
        ("names.x", "4"),
    )
    assert events["name_items"][-1].reads == (
        ("names", "{'x': 4}"),
        ("names.x", "4"),
    )
    assert events["values_equal"][-1].reads == (("values", "[1, 2]"),)
    assert events["extended"][-1].reads == (("values", "[1, 2]"),)
    assert events["repeated"][-1].reads == (("values", "[1, 2]"),)
    assert events["merged"][-1].reads == (("names", "{'x': 4}"),)
    assert (final.lookup, final.position, final.count, final.values_equal) == (
        4,
        1,
        1,
        True,
    )
    assert final.keys == ["x"]
    assert final.name_values == [4]
    assert final.name_items == {"x": 4}
    assert final.extended == [1, 2, 3]
    assert final.repeated == [1, 2, 1, 2]
    assert final.merged == {"x": 4, "y": 5}


def test_explain_keeps_scalar_container_element_dependencies():
    class Indexed(C.Config):
        numbers: list[int]
        first: int = C.interp(lambda context: context.current().numbers[0])

    work = C.draft(Indexed)
    with C.source("input-numbers"):
        work.numbers = [7, 8]
    final = C.finalize(work)

    explanation = C.explain(final, "first")
    assert [cause.path for cause in explanation.causes] == ["numbers[0]"]
    assert explanation.causes[0].current == "7"
    assert explanation.causes[0].events[-1].label == "input-numbers"

    direct = C.explain(final, "numbers[1]")
    assert direct.current == "8"
    assert direct.events[-1].label == "input-numbers"


def test_plain_pydantic_models_use_read_only_dependency_views():
    class Payload(BaseModel):
        value: int
        numbers: list[int]

    class Holder(C.Config):
        payload: Payload
        copied: int = C.interp(lambda context: context.current().payload.value)
        first: int = C.interp(lambda context: context.current().payload.numbers[0])

    final = Holder(payload={"value": 9, "numbers": [4, 5]})
    events = C.provenance(final)

    assert events["copied"][-1].reads == (("payload.value", "9"),)
    assert events["first"][-1].reads == (("payload.numbers[0]", "4"),)


def test_proxy_display_formatting_and_equality_use_canonical_values():
    class Plain(BaseModel):
        value: int

    class Observed(C.Config):
        values: list[int]
        plain: Plain
        list_text: str = C.interp(lambda context: str(context.current().values))
        plain_repr: str = C.interp(lambda context: repr(context.current().plain))
        list_format: str = C.interp(lambda context: format(context.current().values))
        plain_format: str = C.interp(lambda context: f"{context.current().plain}")
        plain_equal: bool = C.interp(
            lambda context: context.current().plain == Plain(value=3)
        )

    final = Observed(values=[1, 2], plain={"value": 3})
    assert final.list_text == "[1, 2]"
    assert final.plain_repr == "Plain(value=3)"
    assert final.list_format == "[1, 2]"
    assert final.plain_format == "value=3"
    assert final.plain_equal

    events = C.provenance(final)
    assert tuple(path for path, _ in events["list_text"][-1].reads) == ("values",)
    assert tuple(path for path, _ in events["plain_repr"][-1].reads) == ("plain",)
    assert tuple(path for path, _ in events["list_format"][-1].reads) == ("values",)
    assert tuple(path for path, _ in events["plain_format"][-1].reads) == ("plain",)
    assert tuple(path for path, _ in events["plain_equal"][-1].reads) == ("plain",)

    class Unsupported(C.Config):
        values: list[int]
        rendered: str = C.interp(
            lambda context: format(context.current().values, ">10")
        )

    with pytest.raises(ValidationError, match="unsupported format string"):
        Unsupported(values=[1, 2])


def test_nested_proxy_results_materialize_as_independent_value_graphs():
    class NestedLists(C.Config):
        source: list[list[int]]
        copied: list[list[int]] = C.interp(
            lambda context: list(context.current().source)
        )

    lists = NestedLists(source=[[1, 2], [3]])
    assert lists.copied == lists.source
    assert lists.copied is not lists.source
    assert all(
        copied is not source for copied, source in zip(lists.copied, lists.source)
    )
    lists.copied[0].append(99)
    assert lists.source[0] == [1, 2]

    class Plain(BaseModel):
        value: int

    class NestedModels(C.Config):
        source: list[Plain]
        copied: list[Plain] = C.interp(
            lambda context: [item for item in context.current().source]
        )

    models = NestedModels(source=[{"value": 4}, {"value": 5}])
    assert [item.value for item in models.copied] == [4, 5]
    assert all(
        copied is not source for copied, source in zip(models.copied, models.source)
    )
    models.copied[0].value = 40
    assert models.source[0].value == 4
    assert tuple(path for path, _ in C.provenance(models)["copied"][-1].reads) == (
        "source",
        "source[0]",
        "source[1]",
    )


def test_cyclic_interpolation_results_fail_before_target_validation():
    def cyclic_result(context: C.Context) -> list[object]:
        del context
        value: list[object] = []
        value.append(value)
        return value

    class Cyclic(C.Config):
        value: list[object] = C.interp(cyclic_result)

    with pytest.raises(ValidationError, match="interpolation result contains a cycle"):
        Cyclic()


def test_config_publication_is_provisional_until_an_operation_is_observed():
    class Child(C.Config):
        value: int

    class Observed(C.Config):
        child: Child
        field_read: int = C.interp(lambda context: context.current().child.value)
        rendered: str = C.interp(lambda context: repr(context.current().child))

    final = Observed(child={"value": 7})
    events = C.provenance(final)

    assert events["field_read"][-1].reads == (("child.value", "7"),)
    assert tuple(path for path, _ in events["rendered"][-1].reads) == ("child",)

    class MethodAccess(C.Config):
        child: Child
        dumped: dict[str, int] = C.interp(
            lambda context: context.current().child.model_dump()
        )

    with pytest.raises(ValidationError, match="has no field 'model_dump'"):
        MethodAccess(child={"value": 7})


def test_final_with_interpolation_history_cannot_be_reused_even_for_local_reads():
    class Child(C.Config):
        source: int
        copied: int = C.interp(lambda context: context.current().source)

    class Branch(C.Config):
        child: Child

    class Root(C.Config):
        branch: Branch

    branch_work = C.draft(Branch)
    with C.source("standalone-child"):
        branch_work.child.source = 12
    branch = C.finalize(branch_work)

    # Read-only provenance APIs must not alter whether a final subtree is reusable.
    before = C.provenance(branch)
    assert C.explain(branch, "child.copied").current == "12"

    with pytest.raises(ValidationError) as direct:
        Root(branch=branch)
    assert direct.value.errors()[0]["type"] == "nshconfig_final_reuse"
    assert "interpolation" in direct.value.errors()[0]["msg"]

    work = C.draft(Root)
    work.branch = branch
    with pytest.raises(ValueError, match="interpolation"):
        C.finalize(work)

    assert C.provenance(branch) == before


def test_nested_final_without_interpolation_reuses_provenance_exactly_once():
    class Leaf(C.Config):
        value: int

    class Branch(C.Config):
        leaf: Leaf

    class Root(C.Config):
        branch: Branch

    leaf_work = C.draft(Leaf)
    with C.source("standalone-leaf"):
        leaf_work.value = 12
    leaf = C.finalize(leaf_work)
    branch = Branch(leaf=leaf)

    # Observation is inert for a reusable, non-interpolated final too.
    branch_events = C.provenance(branch)
    assert C.explain(branch, "leaf.value").current == "12"
    assert len(branch_events["leaf.value"]) == 1

    direct = Root(branch=branch)
    work = C.draft(Root)
    work.branch = branch
    from_draft = C.finalize(work)

    assert direct.branch.leaf.value == from_draft.branch.leaf.value == 12
    reused_events = [C.provenance(final) for final in (direct, from_draft)]
    assert [len(events["branch.leaf.value"]) for events in reused_events] == [1, 1]
    for events in reused_events:
        assert events["branch.leaf.value"][0].label == "standalone-leaf"

    assert C.provenance(branch) == branch_events


def test_final_with_external_interpolation_dependencies_cannot_move_to_new_context():
    class Child(C.Config):
        copied: int = C.interp(lambda context: context.parent(Parent).source)

    class Parent(C.Config):
        source: int
        child: Child

    original = Parent(source=1, child={})

    with pytest.raises(ValidationError) as direct:
        Parent(source=2, child=original.child)
    assert direct.value.errors()[0]["type"] == "nshconfig_final_reuse"
    assert "interpolation" in direct.value.errors()[0]["msg"]

    work = C.draft(Parent)
    work.source = 2
    work.child = original.child
    with pytest.raises(ValueError, match="interpolation"):
        C.finalize(work)


def test_instance_interpolation_keeps_the_write_then_resolution():
    work = C.draft(Run)
    work.optimizer.learning_rate = 2e-4
    work.effective_rate = C.interp(
        lambda context: context.current(Run).optimizer.learning_rate * 10
    )

    events = C.explain(C.finalize(work), "effective_rate").events

    assert [event.kind for event in events] == ["set", "interpolate"]
    assert not events[-1].from_default


def test_failing_repr_never_breaks_assignment_or_event_recording():
    class BadRepr:
        def __repr__(self) -> str:
            raise RuntimeError("no repr")

    class Holder(C.Config):
        value: object

    work = C.draft(Holder)
    value = BadRepr()
    work.value = value

    assert work.value is value
    (event,) = C.explain(work, "value").events
    assert event.value == "<repr unavailable: RuntimeError>"


def test_provenance_returns_detached_tables_with_immutable_chains():
    work = C.draft(Run)
    work.scale = 3
    final = C.finalize(work)

    first = C.provenance(final)
    first["scale"] = ()
    second = C.provenance(final)

    assert isinstance(second["scale"], tuple)
    assert [event.kind for event in second["scale"]] == ["set"]
    assert "effective_rate" in second


def test_provenance_survives_standard_pickle_for_importable_classes():
    work = C.draft(Run)
    set_learning_rate(work)

    restored = pickle.loads(pickle.dumps(C.finalize(work)))

    (event,) = C.explain(restored, "optimizer.learning_rate").events
    assert event.function == "set_learning_rate"
    assert event.code == "work.optimizer.learning_rate = 3e-4"


def test_explain_names_default_factories_instead_of_pydantic_undefined():
    def make_width() -> int:
        return 32

    class FactoryConfig(C.Config):
        width: int = Field(default_factory=make_width)

    explanation = C.explain(FactoryConfig(), "width")

    assert (
        explanation.origin
        == "default factory: test_explain_names_default_factories_instead_of_pydantic_undefined.<locals>.make_width"
    )
    assert "PydanticUndefined" not in str(explanation)


def test_non_identifier_root_field_paths_round_trip_through_explain_and_records():
    KeywordField = create_model(
        "KeywordField",
        __base__=C.Config,
        **{"class": (int, 1)},
    )
    work = C.draft(KeywordField)
    setattr(work, "class", 2)
    final = C.finalize(work)

    assert "['class']" in C.provenance(final)
    assert C.explain(final, "['class']").current == "2"
    restored = C.load_record(KeywordField, C.record(final))
    assert C.explain(restored, "['class']").current == "2"
