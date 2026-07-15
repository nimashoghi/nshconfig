"""Public contract for canonical, declaration-ordered Python interpolation."""

from dataclasses import FrozenInstanceError
from typing import Annotated, Any

import pytest
from pydantic import (
    AliasChoices,
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    ValidationError,
)
from pydantic import field_validator, model_validator
from pydantic_core import PydanticUseDefault
from typing_extensions import override

import nshconfig as C


def test_interpolation_markers_are_immutable_recipe_values():
    marker = C.interp(lambda context: 1)

    with pytest.raises(FrozenInstanceError):
        marker.fn = lambda context: 2  # type: ignore[attr-defined]
    with pytest.raises(FrozenInstanceError):
        marker.site = "forged"  # type: ignore[attr-defined]


def test_pydantic_use_default_retains_native_default_control_flow() -> None:
    class Defaults(C.Config):
        value: int = 5

        @field_validator("value", mode="before")
        @classmethod
        def request_default(cls, value: Any) -> Any:
            if value == "default":
                raise PydanticUseDefault()
            return value

    assert Defaults(value="default").value == 5


def test_alias_inputs_feed_canonical_validated_values_and_explicit_targets_win():
    calls = {"source": 0, "target": 0, "interpolation": 0}

    def derive(context: C.Context) -> int:
        calls["interpolation"] += 1
        return context.current().source

    class Aliased(C.Config):
        source: int = Field(
            validation_alias=AliasChoices("wire_source", "legacy_source")
        )
        copied: int = Field(
            default=C.interp(derive),
            validation_alias=AliasChoices("wire_copied", "legacy_copied"),
        )

        @field_validator("source")
        @classmethod
        def normalize_source(cls, value: int) -> int:
            calls["source"] += 1
            return value * 2

        @field_validator("copied")
        @classmethod
        def normalize_target(cls, value: int) -> int:
            calls["target"] += 1
            return value + 1

    derived = Aliased.model_validate({"wire_source": 3})
    assert (derived.source, derived.copied) == (6, 7)
    assert calls == {"source": 1, "target": 1, "interpolation": 1}

    explicit = Aliased.model_validate({"legacy_source": 4, "wire_copied": 20})
    assert (explicit.source, explicit.copied) == (8, 21)
    assert calls == {"source": 2, "target": 2, "interpolation": 1}


def test_interpolation_observes_the_complete_source_field_validator_pipeline_once():
    calls = {"before": 0, "after": 0, "decorator": 0, "target": 0}

    def before(value: Any) -> int:
        calls["before"] += 1
        return int(value)

    def after(value: int) -> int:
        calls["after"] += 1
        return value * 2

    def target_after(value: int) -> int:
        calls["target"] += 1
        return value + 10

    class Pipeline(C.Config):
        source: Annotated[int, BeforeValidator(before), AfterValidator(after)]
        copied: Annotated[int, AfterValidator(target_after)] = C.interp(
            lambda context: context.current().source
        )

        @field_validator("source")
        @classmethod
        def final_source_step(cls, value: int) -> int:
            calls["decorator"] += 1
            return value + 1

    final = Pipeline.model_validate({"source": "2"})
    assert final.source == 5
    assert final.copied == 15
    assert calls == {"before": 1, "after": 1, "decorator": 1, "target": 1}


def test_model_before_input_changes_are_visible_to_interpolation():
    class Filled(C.Config):
        source: int
        copied: int = C.interp(lambda context: context.current().source)

        @model_validator(mode="before")
        @classmethod
        def fill_source(cls, value: Any) -> Any:
            if isinstance(value, dict) and "source" not in value:
                return {**value, "source": 12}
            return value

    final = Filled.model_validate({})
    assert (final.source, final.copied) == (12, 12)


def test_later_fields_are_unavailable_with_a_structured_target_location():
    class WrongOrder(C.Config):
        dependent: int = C.interp(lambda context: context.current().source)
        source: int = 5

    with pytest.raises(ValidationError) as caught:
        WrongOrder()

    (error,) = caught.value.errors()
    assert error["loc"] == ("dependent",)
    assert error["type"] == "nshconfig_interpolation"
    assert "source has not been validated yet" in error["msg"]
    assert "declare the source field" in error["msg"]


def test_nested_interpolation_errors_keep_the_full_pydantic_field_location():
    class Leaf(C.Config):
        target: int = C.interp(lambda context: 1 // 0)

    class Root(C.Config):
        leaf: Leaf

    with pytest.raises(ValidationError) as caught:
        Root.model_validate({"leaf": {}})

    (error,) = caught.value.errors()
    assert error["loc"] == ("leaf", "target")
    assert error["type"] == "nshconfig_interpolation"
    assert "leaf.target" in error["msg"]
    assert "zero" in error["msg"]


def test_context_selectors_read_canonical_values_at_each_ancestor_level():
    class Leaf(C.Config):
        local: int = 2
        from_current: int = C.interp(lambda context: context.current(Leaf).local)
        from_parent: int = C.interp(lambda context: context.parent(Middle).middle_value)
        from_grandparent: int = C.interp(
            lambda context: context.parent(2, Root).root_value
        )
        from_root: int = C.interp(lambda context: context.root(Root).root_value)
        from_nearest: int = C.interp(
            lambda context: context.nearest(Middle).middle_value
        )

    class Middle(C.Config):
        middle_value: int = 5
        leaf: Leaf

    class Root(C.Config):
        root_value: int = 7
        middle: Middle

    final = Root.config_draft().config_finalize()
    leaf = final.middle.leaf
    assert (
        leaf.from_current,
        leaf.from_parent,
        leaf.from_grandparent,
        leaf.from_root,
        leaf.from_nearest,
    ) == (2, 5, 7, 7, 5)


def test_field_before_validator_can_clone_nested_input_without_losing_parent_context():
    class Child(C.Config):
        copied: int = C.interp(lambda context: context.parent(Parent).source)

    def clone(value: Any) -> dict[str, Any]:
        return dict(value)

    class Parent(C.Config):
        source: int = 5
        child: Annotated[Child, BeforeValidator(clone)]

    assert Parent(child={}).child.copied == 5


def test_model_before_input_is_authoritative_for_interpolation_origin():
    class ModelBefore(C.Config):
        source: int
        copied: int = C.interp(lambda context: context.current().source)

        @model_validator(mode="before")
        @classmethod
        def discard_explicit_copy(cls, value: Any) -> Any:
            if isinstance(value, dict):
                return {key: item for key, item in value.items() if key != "copied"}
            return value

    final = ModelBefore(source=4, copied=99)
    assert (final.source, final.copied) == (4, 4)


def test_default_factory_interpolation_is_pending_on_drafts():
    class FactoryDefault(C.Config):
        source: int = 4
        copied: int = Field(
            default_factory=lambda: C.interp(lambda context: context.current().source)
        )

    work = FactoryDefault.config_draft()
    with pytest.raises(
        C.UnsetError, match="pending interpolation from its default factory"
    ):
        _ = work.copied

    final = work.config_finalize()
    assert final.copied == 4


def test_root_can_descend_active_direct_branches_to_earlier_validated_fields():
    class Leaf(C.Config):
        earlier: int = 3
        copied: int = C.interp(lambda context: context.root(Root).middle.leaf.earlier)

    class Middle(C.Config):
        middle_value: int = 5
        leaf: Leaf

    class Root(C.Config):
        root_value: int = 7
        middle: Middle

    assert Root.config_draft().config_finalize().middle.leaf.copied == 3


def test_an_active_branch_view_cannot_be_returned_as_a_completed_value():
    class WholeBranchLeaf(C.Config):
        earlier: int = 3
        branch: Any = C.interp(lambda context: context.root(WholeBranchRoot).middle)

    class WholeBranchMiddle(C.Config):
        leaf: WholeBranchLeaf

    class WholeBranchRoot(C.Config):
        middle: WholeBranchMiddle

    with pytest.raises(
        ValidationError, match="still being validated|not a completed value"
    ):
        WholeBranchRoot.config_draft().config_finalize()


def test_container_elements_do_not_expose_a_partial_container_to_root_descent():
    class ContainerLeaf(C.Config):
        value: int = C.interp(lambda context: context.root(ContainerRoot).items[0].seed)

    class ContainerItem(C.Config):
        seed: int = 4
        leaf: ContainerLeaf

    class ContainerRoot(C.Config):
        items: list[ContainerItem]

    work = ContainerRoot.config_draft()
    item = ContainerItem.config_draft()
    work.items = [item]
    with pytest.raises(ValidationError) as caught:
        work.config_finalize()
    assert caught.value.errors()[0]["loc"] == ("items", 0, "leaf", "value")
    assert "items has not been validated yet" in caught.value.errors()[0]["msg"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda context: context.current().sequence[:][0].values.append(2),
        lambda context: context.current().by_key[("child",)].values.append(2),
        lambda context: (context.current().sequence + [])[0].values.append(2),
        lambda context: (context.current().sequence * 1)[0].values.append(2),
        lambda context: (context.current().by_key | {})[("child",)].values.append(2),
    ],
    ids=[
        "slice",
        "non-path-mapping-key",
        "concatenation",
        "multiplication",
        "mapping-union",
    ],
)
def test_context_container_access_cannot_mutate_canonical_config_values(
    mutate: Any,
) -> None:
    class Child(C.Config):
        values: list[int] = [1]

    def derive(context: C.Context) -> int:
        mutate(context)
        return 1

    class Root(C.Config):
        sequence: list[Child] = [Child()]
        by_key: dict[tuple[str, ...], Child] = {("child",): Child()}
        derived: int = C.interp(derive)

    with pytest.raises(ValidationError, match="read-only interpolation container view"):
        Root()


def test_completed_earlier_branches_are_visible_but_later_siblings_are_not():
    class Source(C.Config):
        value: int = 8

    class Dependent(C.Config):
        copied: int = C.interp(lambda context: context.root(Root).source.value)

    class Root(C.Config):
        source: Source
        dependent: Dependent

    assert Root.config_draft().config_finalize().dependent.copied == 8

    class EarlyDependent(C.Config):
        copied: int = C.interp(lambda context: context.root(WrongRoot).source.value)

    class WrongRoot(C.Config):
        dependent: EarlyDependent
        source: Source

    with pytest.raises(ValidationError, match="source has not been validated yet"):
        WrongRoot.config_draft().config_finalize()


def test_nested_validation_starts_a_fresh_root_and_restores_the_outer_context():
    class InnerLeaf(C.Config):
        copied: int = C.interp(lambda context: context.root(InnerRoot).base)

    class InnerRoot(C.Config):
        base: int
        leaf: InnerLeaf

    def derive(context: C.Context) -> int:
        inner = InnerRoot.model_validate({"base": 4, "leaf": {}})
        return inner.leaf.copied + context.current().base

    class Outer(C.Config):
        base: int = 3
        result: int = C.interp(derive)

    assert Outer().result == 7


def test_post_init_and_model_after_hooks_observe_the_completed_interpolated_model_once():
    observations: list[tuple[str, int, int]] = []

    class Hooks(C.Config):
        source: int = 2
        copied: int = C.interp(lambda context: context.current().source * 2)

        @override
        def model_post_init(self, context: Any) -> None:
            observations.append(("post_init", self.source, self.copied))

        @model_validator(mode="after")
        def check_values(self) -> "Hooks":
            observations.append(("model_after", self.source, self.copied))
            if self.copied != self.source * 2:
                raise ValueError("copied must be twice source")
            return self

    assert Hooks().copied == 4
    assert observations == [("post_init", 2, 4), ("model_after", 2, 4)]

    with pytest.raises(ValidationError, match="copied must be twice source"):
        Hooks(copied=5)


@pytest.mark.parametrize("hook", ["post_init", "model_after"])
def test_model_level_hooks_follow_native_pydantic_semantics(hook: str):
    if hook == "post_init":

        class PostInitMutates(C.Config):
            source: int = 1
            copied: int = C.interp(lambda context: context.current().source)

            @override
            def model_post_init(self, context: Any) -> None:
                object.__setattr__(self, "source", 99)

        model_type = PostInitMutates

    else:

        class AfterMutates(C.Config):
            source: int = 1
            copied: int = C.interp(lambda context: context.current().source)

            @model_validator(mode="after")
            def mutate(self) -> "AfterMutates":
                object.__setattr__(self, "source", 99)
                return self

        model_type = AfterMutates

    final = model_type()
    assert (final.source, final.copied) == (99, 1)


def test_model_level_hooks_may_mutate_published_containers_in_place():
    class AfterMutates(C.Config):
        source: list[int] = [1]
        copied: int = C.interp(lambda context: context.current().source[0])

        @model_validator(mode="after")
        def mutate(self) -> "AfterMutates":
            self.source.append(2)
            return self

    final = AfterMutates()
    assert final.source == [1, 2]
    assert final.copied == 1


def test_interpolation_preserves_opaque_values_by_identity():
    class Token:
        pass

    class Box(BaseModel):
        values: list[int]

    class CustomDict(dict[str, int]):
        pass

    class Holder(C.Config, arbitrary_types_allowed=True):
        token: Token
        box: Box
        mapping: Any
        copied_token: Token = C.interp(lambda context: context.current().token)
        copied_box: Box = C.interp(lambda context: context.current().box)
        copied_mapping: Any = C.interp(lambda context: context.current().mapping)

    token = Token()
    box = Box(values=[1])
    mapping = CustomDict(value=2)
    final = Holder(token=token, box=box, mapping=mapping)
    assert final.copied_token is token
    assert final.copied_box is box
    assert final.copied_mapping is mapping


def test_interpolation_uses_canonical_field_names_behind_aliases():
    class Aliased(C.Config):
        source: int = Field(alias="wire_source")
        copied: int = C.interp(lambda context: context.current().source)

    final = Aliased.model_validate({"wire_source": 6})
    assert (final.source, final.copied) == (6, 6)
