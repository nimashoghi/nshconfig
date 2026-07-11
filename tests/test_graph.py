"""Collection, validation, and serialization of the supported value graph."""

import asyncio
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


def test_typed_dict_positions_preserve_nested_config_schema_and_context() -> None:
    class Child(C.Config):
        value: int

    class Payload(TypedDict):
        child: Required[Child]
        optional_children: NotRequired[list[Child]]
        readonly_child: ReadOnly[Child]

    with pytest.warns(UserWarning, match="ReadOnly.*qualifier"):

        class Root(C.Config):
            payload: Payload

    direct = Root(payload={"child": {"value": 1}, "readonly_child": {"value": 4}})
    assert type(direct.payload["child"]) is Child

    child = C.draft(Child)
    child.value = 2
    optional = C.draft(Child)
    optional.value = 3
    work = C.draft(Root)
    readonly = C.draft(Child)
    readonly.value = 4
    work.payload = {
        "child": child,
        "optional_children": [optional],
        "readonly_child": readonly,
    }
    final = C.finalize(work)

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

    shared = C.draft(Node)
    shared.value = 1
    other = C.draft(Node)
    other.value = 2

    work = C.draft(Graph)
    work.sequence = [shared, other]
    work.pair = (shared,)
    work.by_name = {"shared": shared}
    final = C.finalize(work)

    assert [node.value for node in final.sequence] == [1, 2]
    assert final.pair[0].value == 1
    assert final.by_name["shared"].value == 1
    assert not any(
        C.is_draft(node)
        for node in [*final.sequence, *final.pair, *final.by_name.values()]
    )
    assert final.sequence[0] is not final.pair[0]
    assert final.sequence[0] is not final.by_name["shared"]


def test_mapping_keys_are_unrestricted_until_their_values_contain_configs() -> None:
    class HostileKey:
        def __repr__(self) -> str:
            raise RuntimeError("repr is unavailable")

    class ScalarValues(C.Config, arbitrary_types_allowed=True):
        values: dict[HostileKey, int]

    key = HostileKey()
    direct_scalars = ScalarValues(values={key: 1})
    assert direct_scalars.values[key] == 1

    scalar_work = C.draft(ScalarValues)
    scalar_work.values = {key: 2}
    assert C.finalize(scalar_work).values[key] == 2

    class Child(C.Config):
        value: int

    class StructuralValues(C.Config):
        values: dict[object, Child]

    with pytest.raises(ValidationError, match="str or int"):
        StructuralValues(values={key: Child(value=3)})

    child_work = C.draft(Child)
    child_work.value = 4
    structural_work = C.draft(StructuralValues)
    structural_work.values = {key: child_work}
    with pytest.raises(ValueError, match="str or int"):
        C.finalize(structural_work)


def test_config_in_a_container_can_interpolate_its_own_fields() -> None:
    class Node(C.Config):
        value: int = 2
        doubled: int = C.interp(lambda context: context.current().value * 2)

    class Graph(C.Config):
        nodes: list[Node]

    work = C.draft(Graph)
    work.nodes = [C.draft(Node)]
    assert C.finalize(work).nodes[0].doubled == 4


def test_untouched_required_config_spine_reports_the_missing_leaf() -> None:
    class Leaf(C.Config):
        width: int

    class Branch(C.Config):
        leaf: Leaf

    class Root(C.Config):
        branch: Branch

    with pytest.raises(ValidationError) as caught:
        C.finalize(C.draft(Root))

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
        C.finalize(C.draft(Recursive))


def test_actual_config_cycle_names_the_path() -> None:
    class Node(C.Config):
        child: "Node | None" = None

    Node.model_rebuild()
    work = C.draft(Node)
    work.child = work

    with pytest.raises(ValueError, match=r"cycle found at Node\.child"):
        C.finalize(work)


def test_container_cycle_names_the_path() -> None:
    class Root(C.Config):
        payload: list[Any]

    cycle: list[Any] = []
    cycle.append(cycle)
    work = C.draft(Root)
    work.payload = cycle

    with pytest.raises(ValueError, match=r"cycle found at Root\.payload\[0\]"):
        C.finalize(work)


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
    work = C.draft(Root)
    work.payload = make_value(marker)

    with pytest.raises(ValueError, match=path):
        C.finalize(work)


def test_direct_interp_marker_is_legal_as_an_any_field_value() -> None:
    class Root(C.Config):
        payload: Any

    work = C.draft(Root)
    work.payload = C.interp(lambda context: 42)
    assert C.finalize(work).payload == 42


def test_pending_value_hidden_in_an_opaque_dataclass_is_rejected() -> None:
    @dataclass
    class Box:
        value: Any

    class Root(C.Config):
        payload: Any

    work = C.draft(Root)
    work.payload = Box(C.interp(lambda context: 42))

    with pytest.raises(ValueError, match=r"opaque object at Root\.payload\.value"):
        C.finalize(work)


def test_config_values_cannot_hide_inside_atomic_model_or_dataclass_values() -> None:
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

    with pytest.raises(ValidationError, match="hidden inside an atomic object"):
        DataclassRoot(payload=Box(Child(value=1)))
    with pytest.raises(ValidationError, match="hidden inside an atomic object"):
        ModelRoot(payload=Wrapper(child=Child(value=1)))


def test_lazy_and_non_builtin_containers_are_rejected() -> None:
    with pytest.raises(TypeError, match="unsupported container annotation.*Iterable"):

        class Lazy(C.Config):
            values: Iterable[int]

    with pytest.raises(TypeError, match="unsupported container annotation.*deque"):

        class Queue(C.Config):
            values: deque[int]

    class Opaque(C.Config):
        value: Any

    with pytest.raises(ValidationError) as queued:
        Opaque(value=deque([1]))
    assert queued.value.errors()[0]["type"] == "nshconfig_unsupported_container"

    with pytest.raises(ValidationError) as lazy:
        Opaque(value=iter([1]))
    assert lazy.value.errors()[0]["type"] == "nshconfig_unsupported_container"

    async def async_values():
        yield 1

    async_value = async_values()
    try:
        with pytest.raises(ValidationError) as async_lazy:
            Opaque(value=async_value)
        assert async_lazy.value.errors()[0]["type"] == "nshconfig_unsupported_container"
    finally:
        asyncio.run(async_value.aclose())

    async def deferred() -> int:
        return 1

    coroutine = deferred()
    try:
        with pytest.raises(ValidationError) as awaitable:
            Opaque(value=coroutine)
        assert awaitable.value.errors()[0]["type"] == "nshconfig_unsupported_container"
    finally:
        coroutine.close()


def test_named_type_aliases_preserve_structural_config_positions() -> None:
    class Child(C.Config):
        value: int

    Children = TypeAliasType("Children", list[Child])

    class Root(C.Config):
        children: Children

    child = C.draft(Child)
    child.value = 3
    work = C.draft(Root)
    work.children = [child]
    assert C.finalize(work).children == [Child(value=3)]


def test_parameterized_named_aliases_substitute_structural_type_parameters() -> None:
    child_type = TypeVar("child_type")

    class Child(C.Config):
        value: int

    Children = TypeAliasType("Children", list[child_type], type_params=(child_type,))

    class Root(C.Config):
        children: Children[Child]

    child = C.draft(Child)
    child.value = 7
    work = C.draft(Root)
    work.children = [child]
    assert C.finalize(work).children == [Child(value=7)]


def test_inspectable_hidden_state_cannot_conceal_pending_values() -> None:
    class Child(C.Config):
        value: int

    class Root(C.Config, arbitrary_types_allowed=True):
        value: Any

    class Box:
        pass

    hidden = C.draft(Child)
    box = Box()
    box.__dict__[1] = hidden
    box.__dict__["1"] = 0
    with pytest.raises(ValidationError, match=r"nested draft.*Root\.value\[stored 1\]"):
        Root(value=box)

    class Plain(BaseModel):
        value: int

    plain = Plain(value=1)
    plain.__dict__["hidden"] = hidden
    with pytest.raises(ValidationError, match=r"nested draft.*Root\.value\.hidden"):
        Root(value=plain)

    class Choice(Enum):
        one = 1

    Choice.one.hidden = hidden
    try:
        with pytest.raises(ValidationError, match=r"nested draft.*Root\.value\.hidden"):
            Root(value=Choice.one)
    finally:
        del Choice.one.hidden


def test_config_draft_hidden_behind_any_requires_a_structural_annotation() -> None:
    class Node(C.Config):
        value: int = 1

    class Root(C.Config):
        payload: Any

    work = C.draft(Root)
    work.payload = C.draft(Node)
    with pytest.raises(
        ValueError, match="annotation does not describe that Config structure"
    ):
        C.finalize(work)


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

    child = C.draft(Second)
    child.value = 2
    work = C.draft(Root)
    work.seed = 7
    work.child = child

    final = C.finalize(work)
    assert type(final.child) is Second
    assert final.child.value == 2
    assert final.child.copied == 7

    revalidated = C.finalize(final)
    assert type(revalidated.child) is Second


def test_exact_config_branch_wins_over_any_in_a_union() -> None:
    class Child(C.Config):
        value: int

    class Root(C.Config):
        child: Any | Child

    child = C.draft(Child)
    child.value = 3
    work = C.draft(Root)
    work.child = child

    final = C.finalize(work)
    assert type(final.child) is Child
    assert final.child.value == 3


def test_heterogeneous_tuple_annotations_are_position_specific() -> None:
    class Child(C.Config):
        value: int = 1

    class Root(C.Config):
        children: tuple[Child, Any]

    legal = C.draft(Root)
    legal.children = (C.draft(Child), "opaque")
    assert type(C.finalize(legal).children[0]) is Child

    hidden = C.draft(Root)
    hidden.children = (C.draft(Child), C.draft(Child))
    with pytest.raises(
        ValueError,
        match=r"Root\.children\[1\].*annotation does not describe that Config structure",
    ):
        C.finalize(hidden)


def test_nested_mapping_and_tuple_errors_name_the_exact_opaque_position() -> None:
    class Child(C.Config):
        value: int = 1

    class Root(C.Config):
        children: dict[str, tuple[Child, Any]]

    work = C.draft(Root)
    work.children = {"group": (C.draft(Child), C.draft(Child))}

    with pytest.raises(
        ValueError,
        match=r"Root\.children\['group'\]\[1\].*annotation does not describe",
    ):
        C.finalize(work)


def test_optional_and_annotated_config_positions_collect_drafts() -> None:
    class Child(C.Config):
        value: int = 1

    class Root(C.Config):
        optional: Child | None
        annotated: Annotated[Child, Field(description="a child")]

    work = C.draft(Root)
    work.optional = None
    work.annotated = C.draft(Child)
    final = C.finalize(work)

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

    work = C.draft(Root)
    work.child = C.draft(Second)
    work.children = [C.draft(First), C.draft(Second)]
    final = C.finalize(work)

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

    work = C.draft(Root)
    work.children = [C.draft(Actual)]  # type: ignore[list-item]

    with pytest.raises(ValueError, match=r"Root\.children\[0\].*exact Actual type"):
        C.finalize(work)


def test_ambiguous_structural_union_requires_one_concrete_container_branch() -> None:
    class Child(C.Config):
        value: int = 1

    class Root(C.Config):
        children: list[Child] | Sequence[Child]

    work = C.draft(Root)
    work.children = [C.draft(Child)]

    with pytest.raises(ValueError, match="ambiguous non-discriminated annotation"):
        C.finalize(work)


def test_union_selection_uses_the_concrete_builtin_container_shape() -> None:
    class Child(C.Config):
        value: int = 1

    class Root(C.Config):
        children: list[Child] | tuple[Child, ...]

    work = C.draft(Root)
    work.children = (C.draft(Child),)
    final = C.finalize(work)

    assert isinstance(final.children, tuple)
    assert type(final.children[0]) is Child


def test_direct_draft_serialization_is_rejected() -> None:
    class Value(C.Config):
        number: int = 1

    work = C.draft(Value)
    with pytest.raises(C.DraftError, match="requires a completed Config final"):
        work.model_dump()
    with pytest.raises(C.DraftError, match="requires a completed Config final"):
        work.model_dump_json()


def test_type_adapter_draft_serialization_is_rejected() -> None:
    class Value(C.Config):
        number: int = 1

    work = C.draft(Value)
    adapter = TypeAdapter(Value)
    with pytest.raises(
        PydanticSerializationError, match="after validation has completed"
    ):
        adapter.dump_python(work)
    with pytest.raises(
        PydanticSerializationError, match="after validation has completed"
    ):
        adapter.dump_json(work)


def test_nested_pydantic_draft_serialization_is_rejected() -> None:
    class Value(C.Config):
        number: int = 1

    class Envelope(BaseModel):
        value: Value

    envelope = Envelope.model_construct(value=C.draft(Value))
    with pytest.raises(
        PydanticSerializationError, match="after validation has completed"
    ):
        envelope.model_dump()
    with pytest.raises(
        PydanticSerializationError, match="after validation has completed"
    ):
        envelope.model_dump_json()
