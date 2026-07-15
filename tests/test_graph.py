"""Collection, validation, and serialization of the supported value graph."""

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, Literal, TypeVar

import pytest
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from pydantic_core import PydanticSerializationError
from typing_extensions import NotRequired, ReadOnly, Required, TypeAliasType, TypedDict

import nshconfig as C


@pytest.mark.filterwarnings(
    "ignore:Item .* is using the `ReadOnly` qualifier:UserWarning"
)
def test_typed_dict_positions_preserve_nested_config_schema_and_context() -> None:
    class Child(C.Config):
        value: int

    class Payload(TypedDict):
        child: Required[Child]
        optional_children: NotRequired[list[Child]]
        readonly_child: ReadOnly[Child]

    class Root(C.Config):
        payload: Payload

    direct = Root(payload={"child": {"value": 1}, "readonly_child": {"value": 4}})
    assert type(direct.payload["child"]) is Child

    child = Child.config_draft()
    child.value = 2
    optional = Child.config_draft()
    optional.value = 3
    work = Root.config_draft()
    readonly = Child.config_draft()
    readonly.value = 4
    work.payload = {
        "child": child,
        "optional_children": [optional],
        "readonly_child": readonly,
    }
    final = work.config_finalize()

    assert type(final.payload["child"]) is Child
    assert final.payload["child"].value == 2
    assert final.payload["optional_children"][0].value == 3
    assert final.payload["readonly_child"].value == 4


def test_typed_config_containers_are_collected_recursively() -> None:
    class Node(C.Config):
        value: int

    class Graph(C.Config):
        sequence: list[Node]
        pair: tuple[Node, ...]
        by_name: dict[str, Node]

    shared = Node.config_draft()
    shared.value = 1
    other = Node.config_draft()
    other.value = 2

    work = Graph.config_draft()
    work.sequence = [shared, other]
    work.pair = (shared,)
    work.by_name = {"shared": shared}
    final = work.config_finalize()

    assert [node.value for node in final.sequence] == [1, 2]
    assert final.pair[0].value == 1
    assert final.by_name["shared"].value == 1
    assert not any(
        C.is_draft(node)
        for node in [*final.sequence, *final.pair, *final.by_name.values()]
    )
    assert final.sequence[0] is not final.pair[0]
    assert final.sequence[0] is not final.by_name["shared"]


def test_mapping_keys_follow_pydantic_semantics_with_config_values() -> None:
    class HostileKey:
        def __repr__(self) -> str:
            raise RuntimeError("repr is unavailable")

    class ScalarValues(C.Config, arbitrary_types_allowed=True):
        values: dict[HostileKey, int]

    key = HostileKey()
    direct_scalars = ScalarValues(values={key: 1})
    assert direct_scalars.values[key] == 1

    scalar_work = ScalarValues.config_draft()
    scalar_work.values = {key: 2}
    assert scalar_work.config_finalize().values[key] == 2

    class Child(C.Config):
        value: int

    class StructuralValues(C.Config):
        values: dict[object, Child]

    direct_structural = StructuralValues(values={key: Child(value=3)})
    assert direct_structural.values[key].value == 3

    child_work = Child.config_draft()
    child_work.value = 4
    structural_work = StructuralValues.config_draft()
    structural_work.values = {key: child_work}
    assert structural_work.config_finalize().values[key].value == 4


def test_config_in_a_container_can_interpolate_its_own_fields() -> None:
    class Node(C.Config):
        value: int = 2
        doubled: int = C.interp(lambda context: context.current().value * 2)

    class Graph(C.Config):
        nodes: list[Node]

    work = Graph.config_draft()
    work.nodes = [Node.config_draft()]
    assert work.config_finalize().nodes[0].doubled == 4


def test_untouched_required_config_spine_reports_the_missing_leaf() -> None:
    class Leaf(C.Config):
        width: int

    class Branch(C.Config):
        leaf: Leaf

    class Root(C.Config):
        branch: Branch

    with pytest.raises(ValidationError) as caught:
        Root.config_draft().config_finalize()

    errors = caught.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == ("branch", "leaf", "width")
    assert errors[0]["type"] == "missing"


def test_recursive_required_spine_fails_before_unbounded_vivification() -> None:
    class Recursive(C.Config):
        child: "Recursive"

    Recursive.model_rebuild()
    with pytest.raises(
        ValueError, match=r"unbounded recursive spine.*Recursive\.child\.child"
    ):
        Recursive.config_draft().config_finalize()


def test_actual_config_cycle_names_the_path() -> None:
    class Node(C.Config):
        child: "Node | None" = None

    Node.model_rebuild()
    work = Node.config_draft()
    work.child = work

    with pytest.raises(ValueError, match=r"cycle found at Node\.child"):
        work.config_finalize()


def test_container_cycle_names_the_path() -> None:
    class Root(C.Config):
        payload: list[Any]

    cycle: list[Any] = []
    cycle.append(cycle)
    work = Root.config_draft()
    work.payload = cycle

    with pytest.raises(ValueError, match=r"cycle found at Root\.payload\[0\]"):
        work.config_finalize()


def test_cyclic_default_factory_fails_at_the_default_path() -> None:
    def make_cycle() -> list[Any]:
        value: list[Any] = []
        value.append(value)
        return value

    class Root(C.Config):
        payload: Any = Field(default_factory=make_cycle)

    with pytest.raises(ValidationError) as direct:
        Root()
    assert direct.value.errors()[0]["type"] == "nshconfig_default"
    assert "cycle found at payload[0]" in str(direct.value)

    work = Root.config_draft()
    with pytest.raises(C.UnsetError, match=r"Root\.payload\[0\].*cyclic"):
        _ = work.payload
    with pytest.raises(ValidationError) as finalized:
        work.config_finalize()
    assert finalized.value.errors()[0]["type"] == "nshconfig_default"


@pytest.mark.parametrize(
    ("make_value", "path"),
    [
        (lambda marker: [marker], r"Root\.payload\[0\]"),
        (lambda marker: {"nested": marker}, r"Root\.payload\['nested'\]"),
        (lambda marker: {marker: "value"}, r"Root\.payload\.<key>"),
        (lambda marker: {marker}, r"Root\.payload\[0\]"),
    ],
)
def test_interp_marker_is_illegal_inside_builtin_containers(
    make_value: Any, path: str
) -> None:
    class Root(C.Config):
        payload: Any

    marker = C.interp(lambda context: 1)
    work = Root.config_draft()
    work.payload = make_value(marker)

    with pytest.raises(ValueError, match=path):
        work.config_finalize()


def test_direct_interp_marker_is_legal_as_an_any_field_value() -> None:
    class Root(C.Config):
        payload: Any

    work = Root.config_draft()
    work.payload = C.interp(lambda context: 42)
    assert work.config_finalize().payload == 42


def test_pending_value_hidden_in_an_opaque_dataclass_remains_opaque() -> None:
    @dataclass
    class Box:
        value: Any

    class Root(C.Config):
        payload: Any

    work = Root.config_draft()
    marker = C.interp(lambda context: 42)
    box = Box(marker)
    work.payload = box

    assert work.config_finalize().payload is box
    assert box.value is marker


def test_config_values_inside_arbitrary_models_and_dataclasses_are_opaque() -> None:
    class Child(C.Config):
        value: int

    @dataclass
    class Box:
        child: "Child"

    class Wrapper(BaseModel):
        child: Child

    class DataclassRoot(C.Config):
        payload: Box

    class ModelRoot(C.Config):
        payload: Wrapper

    box = Box(Child(value=1))
    wrapper = Wrapper(child=Child(value=1))
    assert DataclassRoot(payload=box).payload.child.value == 1
    assert ModelRoot(payload=wrapper).payload.child.value == 1


def test_structural_annotations_are_builtins_but_opaque_values_are_not_crawled() -> (
    None
):
    with pytest.raises(TypeError, match="unsupported container annotation.*Iterable"):

        class Lazy(C.Config):
            values: Iterable[int]

    with pytest.raises(TypeError, match="unsupported container annotation.*deque"):

        class Queue(C.Config):
            values: deque[int]

    class Opaque(C.Config):
        value: Any

    queue = deque([1])
    lazy = iter([1])
    assert Opaque(value=queue).value is queue
    assert Opaque(value=lazy).value is lazy


def test_named_type_aliases_preserve_structural_config_positions() -> None:
    class Child(C.Config):
        value: int

    Children = TypeAliasType("Children", list[Child])

    class Root(C.Config):
        children: Children

    child = Child.config_draft()
    child.value = 3
    work = Root.config_draft()
    work.children = [child]
    assert work.config_finalize().children == [Child(value=3)]


def test_parameterized_named_aliases_substitute_structural_type_parameters() -> None:
    child_type = TypeVar("child_type")

    class Child(C.Config):
        value: int

    Children = TypeAliasType("Children", list[child_type], type_params=(child_type,))

    class Root(C.Config):
        children: Children[Child]

    child = Child.config_draft()
    child.value = 7
    work = Root.config_draft()
    work.children = [child]
    assert work.config_finalize().children == [Child(value=7)]


def test_arbitrary_object_state_is_opaque() -> None:
    class Child(C.Config):
        value: int

    class Root(C.Config, arbitrary_types_allowed=True):
        value: Any

    class Box:
        pass

    hidden = Child.config_draft()
    box = Box()
    box.__dict__[1] = hidden
    box.__dict__["1"] = 0
    assert Root(value=box).value is box

    box.__dict__[1] = Child(value=2)
    assert Root(value=box).value is box

    class Plain(BaseModel):
        value: int

    plain = Plain(value=1)
    plain.__dict__["hidden"] = hidden
    assert Root(value=plain).value is plain

    class CustomMapping(dict[str, Any]):
        pass

    custom_mapping = CustomMapping(hidden=hidden)
    work = Root.config_draft()
    work.value = custom_mapping
    assert work.config_finalize().value is custom_mapping

    class Choice(Enum):
        one = 1

    Choice.one.hidden = hidden
    try:
        assert Root(value=Choice.one).value is Choice.one
    finally:
        del Choice.one.hidden


def test_enum_values_are_opaque() -> None:
    class Child(C.Config):
        value: int

    class Root(C.Config):
        value: Any

    hidden_values = (
        Child(value=1),
        Child.config_draft(),
        C.interp(lambda context: context.current()),
    )
    for index, hidden in enumerate(hidden_values):
        Choice = Enum(f"Choice{index}", {"item": hidden})
        assert Root(value=Choice.item).value is Choice.item


def test_atomic_values_preserve_identity_aliases_and_may_have_cyclic_state() -> None:
    class Box:
        pass

    class Root(C.Config, arbitrary_types_allowed=True):
        box: Box

    box = Box()
    box.self = box
    box.cache = [1, 2]
    final = Root(box=box)
    assert final.box is box
    assert final.box.self is box
    assert final.box.cache == [1, 2]

    class Plain(BaseModel):
        values: list[int]

    class Pair(C.Config):
        left: Plain
        right: Plain

    plain = Plain(values=[3])
    pair = Pair(left=plain, right=plain)
    assert pair.left is plain
    assert pair.right is plain

    @dataclass
    class DataclassNode:
        child: Any = None

    class ModelNode(BaseModel):
        child: Any = None

    class AtomicRoot(C.Config):
        value: Any

    dataclass_node = DataclassNode()
    dataclass_node.child = dataclass_node
    model_node = ModelNode()
    model_node.child = model_node

    for value in (dataclass_node, model_node):
        assert AtomicRoot(value=value).value is value
        work = AtomicRoot.config_draft()
        work.value = value
        assert work.config_finalize().value is value


def test_builtin_aliases_follow_pydantic_semantics_in_opaque_fields() -> None:
    class Pair(C.Config):
        left: Any
        right: Any

    shared = [{"value": 1}]
    direct = Pair(left=shared, right=shared)
    assert direct.left is direct.right is shared

    work = Pair.config_draft()
    work.left = shared
    work.right = shared
    final = work.config_finalize()
    assert final.left is final.right
    assert final.left is not shared

    default_shared: list[Any] = []

    class Defaulted(C.Config):
        value: Any = [default_shared, default_shared]

    defaulted = Defaulted()
    assert defaulted.value[0] is defaulted.value[1]
    default_work = Defaulted.config_draft()
    assert default_work.value[0] is default_work.value[1]
    default_final = default_work.config_finalize()
    assert default_final.value[0] is default_final.value[1]

    class Interpolated(C.Config):
        source: Any
        copied: Any = C.interp(lambda context: context.current().source)

    interpolated = Interpolated(source=[shared, shared])
    assert interpolated.source[0] is interpolated.source[1]
    assert interpolated.copied[0] is interpolated.copied[1]
    assert interpolated.copied is not interpolated.source

    recipe_shared: list[Any] = []

    class Child(C.Config):
        left: Any
        nested: Any

    class Parent(C.Config):
        child: Child = Child(left=recipe_shared, nested={"value": recipe_shared})

    parent = Parent()
    assert parent.child.left is parent.child.nested["value"]


def test_opaque_state_may_contain_arbitrary_collections() -> None:
    class Child(C.Config):
        value: int

    class Box:
        pass

    class Root(C.Config, arbitrary_types_allowed=True):
        value: Box

    box = Box()
    box.queue = deque([Child.config_draft()])
    assert Root(value=box).value is box


def test_config_draft_hidden_behind_any_requires_a_structural_annotation() -> None:
    class Node(C.Config):
        value: int = 1

    class Root(C.Config):
        payload: Any

    work = Root.config_draft()
    work.payload = Node.config_draft()
    with pytest.raises(
        ValueError, match="annotation does not describe that Config structure"
    ):
        work.config_finalize()

    class DefaultRoot(C.Config):
        payload: Any = Node()

    with pytest.raises(
        ValidationError, match="annotation does not describe that Config structure"
    ):
        DefaultRoot()
    with pytest.raises(
        C.UnsetError, match="annotation does not describe that Config structure"
    ):
        _ = DefaultRoot.config_draft().payload

    class NestedDefaultRoot(C.Config):
        payload: list[Any] = [Node()]

    with pytest.raises(
        ValidationError, match="annotation does not describe that Config structure"
    ):
        NestedDefaultRoot()


def test_union_collection_preserves_the_drafts_exact_config_class() -> None:
    class First(C.Config):
        value: int
        copied: int = 0

    class Second(C.Config):
        value: int
        copied: int = C.interp(lambda context: context.parent().seed)

    class Root(C.Config):
        seed: int
        child: First | Second

    child = Second.config_draft()
    child.value = 2
    work = Root.config_draft()
    work.seed = 7
    work.child = child

    final = work.config_finalize()
    assert type(final.child) is Second
    assert final.child.value == 2
    assert final.child.copied == 7


def test_template_defaults_preserve_the_exact_config_union_branch() -> None:
    class First(C.Config):
        kind: Literal["first"] = Field("first", alias="type")

    class Second(C.Config):
        kind: Literal["second"] = Field("second", alias="type")
        value: int = Field(2, alias="wire")

    Tagged = Annotated[First | Second, Field(discriminator="kind")]

    class Root(C.Config):
        plain: First | Second = Second(wire=7)
        tagged: Tagged = Second(wire=7)
        deferred: First | Second = Field(default_factory=Second.config_draft)

    direct = Root()
    assert all(
        type(value) is Second
        for value in (direct.plain, direct.tagged, direct.deferred)
    )
    assert (direct.plain.value, direct.tagged.value, direct.deferred.value) == (7, 7, 2)
    assert direct.plain.model_fields_set == direct.tagged.model_fields_set == {"value"}
    assert direct.deferred.model_fields_set == set()

    work = Root.config_draft()
    assert all(
        type(value) is Second and C.is_draft(value)
        for value in (work.plain, work.tagged, work.deferred)
    )
    final = work.config_finalize()
    assert all(
        type(value) is Second for value in (final.plain, final.tagged, final.deferred)
    )
    assert (final.plain.value, final.tagged.value, final.deferred.value) == (7, 7, 2)


def test_exact_config_branch_wins_over_any_in_a_union() -> None:
    class Child(C.Config):
        value: int

    class Root(C.Config):
        child: Any | Child

    child = Child.config_draft()
    child.value = 3
    work = Root.config_draft()
    work.child = child

    final = work.config_finalize()
    assert type(final.child) is Child
    assert final.child.value == 3


def test_heterogeneous_tuple_annotations_are_position_specific() -> None:
    class Child(C.Config):
        value: int = 1

    class Root(C.Config):
        children: tuple[Child, Any]

    legal = Root.config_draft()
    legal.children = (Child.config_draft(), "opaque")
    assert type(legal.config_finalize().children[0]) is Child

    hidden = Root.config_draft()
    hidden.children = (Child.config_draft(), Child.config_draft())
    with pytest.raises(
        ValueError,
        match=r"Root\.children\[1\].*annotation does not describe that Config structure",
    ):
        hidden.config_finalize()


def test_nested_mapping_and_tuple_errors_name_the_exact_opaque_position() -> None:
    class Child(C.Config):
        value: int = 1

    class Root(C.Config):
        children: dict[str, tuple[Child, Any]]

    work = Root.config_draft()
    work.children = {"group": (Child.config_draft(), Child.config_draft())}

    with pytest.raises(
        ValueError,
        match=r"Root\.children\['group'\]\[1\].*annotation does not describe",
    ):
        work.config_finalize()


def test_optional_and_annotated_config_positions_collect_drafts() -> None:
    class Child(C.Config):
        value: int = 1

    class Root(C.Config):
        optional: Child | None
        annotated: Annotated[Child, Field(description="a child")]

    work = Root.config_draft()
    work.optional = None
    work.annotated = Child.config_draft()
    final = work.config_finalize()

    assert final.optional is None
    assert type(final.annotated) is Child


def test_discriminated_union_uses_the_selected_config_default_tag() -> None:
    class First(C.Config):
        kind: Literal["first"] = "first"
        value: int = 1

    class Second(C.Config):
        kind: Literal["second"] = "second"
        value: int = 2

    Variant = Annotated[First | Second, Field(discriminator="kind")]

    class Root(C.Config):
        child: Variant
        children: list[Variant]

    work = Root.config_draft()
    work.child = Second.config_draft()
    work.children = [First.config_draft(), Second.config_draft()]
    final = work.config_finalize()

    assert type(final.child) is Second
    assert [type(child) for child in final.children] == [First, Second]
    assert [child.kind for child in final.children] == ["first", "second"]


def test_wrong_config_class_is_rejected_at_its_annotation_position() -> None:
    class Expected(C.Config):
        value: int = 1

    class Actual(C.Config):
        value: int = 1

    class Root(C.Config):
        children: list[Expected]

    work = Root.config_draft()
    work.children = [Actual.config_draft()]  # type: ignore[list-item]

    with pytest.raises(ValueError, match=r"Root\.children\[0\].*exact Actual type"):
        work.config_finalize()


def test_ambiguous_structural_union_requires_one_concrete_container_branch() -> None:
    class Child(C.Config):
        value: int = 1

    class Root(C.Config):
        children: list[Child] | Sequence[Child]

    work = Root.config_draft()
    work.children = [Child.config_draft()]

    with pytest.raises(ValueError, match="ambiguous non-discriminated annotation"):
        work.config_finalize()


def test_union_selection_uses_the_concrete_builtin_container_shape() -> None:
    class Child(C.Config):
        value: int = 1

    class Root(C.Config):
        children: list[Child] | tuple[Child, ...]

    work = Root.config_draft()
    work.children = (Child.config_draft(),)
    final = work.config_finalize()

    assert isinstance(final.children, tuple)
    assert type(final.children[0]) is Child


def test_direct_draft_serialization_is_rejected() -> None:
    class Value(C.Config):
        number: int = 1

    work = Value.config_draft()
    with pytest.raises(
        C.DraftError, match=r"not serializable; call config_finalize\(\) first"
    ):
        work.model_dump()
    with pytest.raises(
        C.DraftError, match=r"not serializable; call config_finalize\(\) first"
    ):
        work.model_dump_json()


def test_type_adapter_draft_serialization_is_rejected() -> None:
    class Value(C.Config):
        number: int = 1

    work = Value.config_draft()
    adapter = TypeAdapter(Value)
    with pytest.raises(PydanticSerializationError, match="drafts are not serializable"):
        adapter.dump_python(work)
    with pytest.raises(PydanticSerializationError, match="drafts are not serializable"):
        adapter.dump_json(work)


def test_nested_pydantic_draft_serialization_is_rejected() -> None:
    class Value(C.Config):
        number: int = 1

    class Envelope(BaseModel):
        value: Value

    envelope = Envelope.model_construct(value=Value.config_draft())
    with pytest.raises(PydanticSerializationError, match="drafts are not serializable"):
        envelope.model_dump()
    with pytest.raises(PydanticSerializationError, match="drafts are not serializable"):
        envelope.model_dump_json()
