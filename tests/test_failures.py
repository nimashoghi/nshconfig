"""Failure messages identify the field, context boundary, and corrective action."""

from typing import Any

import pytest
from pydantic import ValidationError, model_validator

import nshconfig as C


def test_parent_at_constructor_root_defers_but_validation_stays_structured():
    class Root(C.Config):
        value: int = C.interp(lambda context: context.parent().missing)

    assert C.is_template(Root())

    with pytest.raises(ValidationError) as caught:
        Root.model_validate({})

    (error,) = caught.value.errors(include_url=False)
    assert error["loc"] == ("value",)
    assert error["type"] == "nshconfig_unbound_interpolation"
    assert "no ancestor exists" in error["msg"]


@pytest.mark.parametrize("levels", [0, -1, 3])
def test_invalid_parent_depth_explains_the_context_boundary(levels: int):
    class Leaf(C.Config):
        value: int = C.interp(lambda context: context.parent(levels).source)

    class Root(C.Config):
        source: int = 1
        leaf: Leaf

    with pytest.raises(ValidationError) as caught:
        Root.config_draft().config_finalize()

    message = caught.value.errors(include_url=False)[0]["msg"]
    expected = "at least 1" if levels < 1 else "no ancestor exists"
    assert expected in message


def test_typed_context_mismatch_names_expected_and_actual_models():
    class Expected(C.Config):
        source: int = 1

    class Leaf(C.Config):
        value: int = C.interp(lambda context: context.parent(Expected).source)

    class Actual(C.Config):
        leaf: Leaf

    with pytest.raises(ValidationError, match="expected Expected, found Actual"):
        Actual.config_draft().config_finalize()


def test_nearest_names_the_missing_ancestor_and_active_chain():
    class Missing(C.Config):
        source: int = 1

    class Leaf(C.Config):
        value: int = C.interp(lambda context: context.nearest(Missing).source)

    class Root(C.Config):
        leaf: Leaf

    with pytest.raises(ValidationError, match=r"no enclosing Missing.*Root > Leaf"):
        Root.config_draft().config_finalize()


def test_unknown_context_field_offers_a_close_match():
    class Config(C.Config):
        learning_rate: float = 1e-3
        copied: float = C.interp(lambda context: context.current().learningrate)

    with pytest.raises(ValidationError, match="did you mean 'learning_rate'"):
        Config()


def test_self_dependency_fails_as_an_unavailable_declaration_order_read():
    class Cycle(C.Config):
        value: int = C.interp(lambda context: context.current().value)

    with pytest.raises(ValidationError) as caught:
        Cycle()

    (error,) = caught.value.errors(include_url=False)
    assert error["loc"] == ("value",)
    assert "has not been validated yet" in error["msg"]


def test_resolver_exceptions_include_the_target_path_and_callable_site():
    def explode(context: C.Context) -> int:
        del context
        raise RuntimeError("resolver exploded")

    class Leaf(C.Config):
        value: int = C.interp(explode)

    class Root(C.Config):
        leaf: Leaf

    with pytest.raises(ValidationError) as caught:
        Root.config_draft().config_finalize()

    (error,) = caught.value.errors(include_url=False)
    assert error["loc"] == ("leaf", "value")
    assert error["type"] == "nshconfig_interpolation"
    assert "leaf.value" in error["msg"]
    assert "explode" in error["msg"]
    assert "resolver exploded" in error["msg"]


def test_pending_markers_refuse_boolean_and_string_formatting():
    marker = C.interp(lambda context: context.root())

    with pytest.raises(C.DraftError, match="cannot be used as a boolean"):
        bool(marker)
    with pytest.raises(C.DraftError, match="cannot be formatted"):
        f"{marker}"


def test_marker_nested_in_a_container_is_rejected_with_its_graph_path():
    class Graph(C.Config):
        payload: Any

    work = Graph.config_draft()
    work.payload = [C.interp(lambda context: 3)]

    with pytest.raises(ValueError, match=r"complete Config field value.*payload\[0\]"):
        work.config_finalize()

    with pytest.raises(ValidationError, match=r"Graph\.payload\[0\]"):
        Graph(payload=[C.interp(lambda context: 3)])


def test_config_draft_hidden_under_any_is_rejected_before_pydantic_can_keep_it():
    class Child(C.Config):
        value: int

    class Holder(C.Config):
        payload: Any

    child = Child.config_draft()
    child.value = 2
    work = Holder.config_draft()
    work.payload = {"child": child}

    with pytest.raises(
        ValueError, match="annotation does not describe that Config structure"
    ):
        work.config_finalize()


def test_required_recursive_config_spine_fails_instead_of_recursing_forever():
    class Node(C.Config):
        child: "Node"

    Node.model_rebuild()

    with pytest.raises(ValueError, match="unbounded recursive spine"):
        Node.config_draft().config_finalize()


def test_cyclic_any_values_fail_with_the_first_repeated_path():
    class Graph(C.Config):
        payload: Any

    values: list[Any] = []
    values.append(values)
    work = Graph.config_draft()
    work.payload = values

    with pytest.raises(ValueError, match=r"Graph\.payload\[0\]"):
        work.config_finalize()


def test_draft_repr_distinguishes_pending_unset_and_autovivified_fields():
    class Leaf(C.Config):
        required: int
        derived: int = C.interp(lambda context: context.current().required)

    class Root(C.Config):
        leaf: Leaf
        scalar: int

    work = Root.config_draft()
    work.leaf
    rendered = repr(work)

    assert rendered.startswith("<draft Root(")
    assert "leaf=<draft Leaf(" in rendered
    assert "required=[UNSET]" in rendered
    assert "derived=[pending interp(" in rendered
    assert "scalar=[UNSET]" in rendered


def test_model_post_init_runs_only_for_a_validated_final() -> None:
    observations: list[tuple[int, bool]] = []

    class Hooked(C.Config):
        value: int

        def model_post_init(self, context: Any) -> None:
            observations.append((self.value, C.is_draft(self)))

    work = Hooked.config_draft()
    assert observations == []
    work.value = 3
    assert observations == []

    final = work.config_finalize()
    assert final.value == 3
    assert observations == [(3, False)]


def test_model_after_validator_may_replace_the_validated_instance() -> None:
    class Replacing(C.Config):
        value: int

        @model_validator(mode="after")
        def replace(self) -> "Replacing":
            return self.model_copy(update={"value": self.value + 1})

    assert Replacing.model_validate({"value": 1}).value == 2


@pytest.mark.parametrize("in_place", [False, True])
def test_model_after_validator_may_mutate_validated_fields(in_place: bool) -> None:
    class Mutating(C.Config):
        values: list[int]

        @model_validator(mode="after")
        def mutate(self) -> "Mutating":
            if in_place:
                self.values.append(2)
            else:
                object.__setattr__(self, "values", [*self.values, 2])
            return self

    assert Mutating(values=[1]).values == [1, 2]


def test_model_post_init_may_mutate_a_validated_field() -> None:
    class Mutating(C.Config):
        values: list[int]

        def model_post_init(self, context: Any) -> None:
            self.values.append(2)

    assert Mutating(values=[1]).values == [1, 2]


def test_unknown_draft_assignment_is_rejected_with_a_spelling_hint() -> None:
    class Value(C.Config):
        width: int = 1

    work = Value.config_draft()
    with pytest.raises(AttributeError, match=r"no field 'widht'.*did you mean 'width'"):
        work.widht = 2  # type: ignore[attr-defined]
    assert work.width == 1
    assert work.model_fields_set == set()


def test_unknown_final_assignment_is_rejected() -> None:
    class Value(C.Config):
        width: int = 1

    final = Value()
    with pytest.raises(ValidationError) as caught:
        final.unknown = 2  # type: ignore[attr-defined]
    assert caught.value.errors()[0]["type"] == "frozen_instance"


def test_invalid_draft_input_becomes_a_structured_pydantic_error() -> None:
    class Value(C.Config):
        count: int

    work = Value.config_draft()
    work.count = "not an integer"  # type: ignore[assignment]

    with pytest.raises(ValidationError) as caught:
        work.config_finalize()
    error = caught.value.errors()[0]
    assert error["loc"] == ("count",)
    assert error["type"] == "int_type"


def test_draft_cannot_enter_normal_pydantic_validation() -> None:
    class Child(C.Config):
        value: int = 1

    class Parent(C.Config):
        child: Child

    with pytest.raises(ValidationError) as caught:
        Parent(child=Child.config_draft())
    error = caught.value.errors()[0]
    assert error["loc"] == ("child",)
    assert error["type"] == "nshconfig_draft_input"


@pytest.mark.parametrize("pending_kind", ["draft", "interpolation"])
def test_pending_values_hidden_in_opaque_instance_state_remain_opaque(
    pending_kind: str,
) -> None:
    class Child(C.Config):
        value: int = 1

    class Box:
        def __init__(self, value: Any):
            self.value = value

    class Root(C.Config):
        payload: Any

    pending = (
        Child.config_draft()
        if pending_kind == "draft"
        else C.interp(lambda context: context.current().payload)
    )
    box = Box(pending)
    assert Root(payload=box).payload is box
