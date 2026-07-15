"""Unbound constructor templates bind only inside concrete Config structure."""

import copy
from typing import Any

from pydantic_core import PydanticSerializationError
import pytest
from typing_extensions import TypedDict

import nshconfig as C


def test_inline_parent_dependent_default_binds_without_a_factory() -> None:
    class Child(C.Config):
        copied: int = C.interp(lambda context: context.parent(Parent).source)
        doubled: int = C.interp(lambda context: context.current(Child).copied * 2)

    class Parent(C.Config):
        source: int = 3
        child: Child = Child()

    template = Parent.model_fields["child"].default
    assert isinstance(template, Child)
    assert C.is_template(template)
    assert not C.is_draft(template)

    defaulted = Parent()
    assert defaulted.child == Child(copied=3, doubled=6)
    assert "_nshconfig_binding_issues" not in defaulted.child.__pydantic_private__
    assert Parent(child=Child()).child == Child(copied=3, doubled=6)
    assert Parent.model_validate({"child": Child()}).child == Child(copied=3, doubled=6)


def test_template_may_carry_required_slots_until_a_parent_draft_fills_them() -> None:
    class Child(C.Config):
        required: int
        copied: int = C.interp(lambda context: context.parent(Parent).source)

    class Parent(C.Config):
        source: int = 3
        child: Child = Child()

    template = Parent.model_fields["child"].default
    assert C.is_template(template)
    assert "required=[missing]" in repr(template)

    with pytest.raises(C.ValidationError) as caught:
        Parent()
    assert [item["type"] for item in caught.value.errors(include_url=False)] == [
        "missing"
    ]

    work = Parent.config_draft()
    assert C.is_draft(work.child)
    work.child.required = 5
    assert work.config_finalize().child == Child(required=5, copied=3)


def test_parent_root_and_nearest_selectors_defer_and_publish_canonical_values() -> None:
    class ParentBase(C.Config):
        source: int = 4

    class ByParent(C.Config):
        value: int = C.interp(lambda context: context.parent(ParentBase).source)
        propagated: int = C.interp(lambda context: context.current().value + 1)

    class ByRoot(C.Config):
        value: int = C.interp(lambda context: context.root(ParentBase).source)

    class ByNearest(C.Config):
        value: int = C.interp(lambda context: context.nearest(ParentBase).source)

    parent_template = ByParent()
    root_template = ByRoot()
    nearest_template = ByNearest()
    assert all(
        C.is_template(value)
        for value in (parent_template, root_template, nearest_template)
    )

    class Parent(ParentBase):
        by_parent: ByParent = parent_template
        by_root: ByRoot = root_template
        by_nearest: ByNearest = nearest_template

    final = Parent()
    assert (final.by_parent.value, final.by_parent.propagated) == (4, 5)
    assert final.by_root.value == 4
    assert final.by_nearest.value == 4


def test_missing_grandparent_context_propagates_to_the_standalone_branch() -> None:
    class GrandparentBase(C.Config):
        source: int = 6

    class Leaf(C.Config):
        copied: int = C.interp(lambda context: context.root(GrandparentBase).source)

    class Branch(C.Config):
        leaf: Leaf = Leaf()

    branch_template = Branch()
    assert C.is_template(branch_template)

    class Grandparent(GrandparentBase):
        branch: Branch = branch_template

    assert Grandparent().branch.leaf.copied == 6


def test_wrong_existing_parent_type_is_not_deferrable() -> None:
    class ExpectedParent(C.Config):
        source: int = 1

    class Child(C.Config):
        copied: int = C.interp(lambda context: context.parent(ExpectedParent).source)

    template = Child()

    class WrongParent(C.Config):
        child: Child = template

    with pytest.raises(C.ValidationError) as caught:
        WrongParent()
    assert caught.value.errors(include_url=False)[0]["type"] == (
        "nshconfig_interpolation"
    )


def test_constructor_fallback_is_narrow_and_name_errors_defer_only_once() -> None:
    class ParentBase(C.Config):
        source: int = 1

    class Candidate(C.Config):
        required: int
        copied: int = C.interp(lambda context: context.parent(ParentBase).source)

    with pytest.raises(C.ValidationError) as bad_type:
        Candidate(required="1")
    assert {item["type"] for item in bad_type.value.errors(include_url=False)} == {
        "int_type",
        "nshconfig_unbound_interpolation",
    }

    with pytest.raises(C.ValidationError) as extra:
        Candidate(nope=1)
    assert "extra_forbidden" in {
        item["type"] for item in extra.value.errors(include_url=False)
    }

    class MissingOnly(C.Config):
        value: int

    with pytest.raises(C.ValidationError, match="Field required"):
        MissingOnly()

    class ValidatorNameError(C.Config):
        value: int = 1

        @C.field_validator("value")
        @classmethod
        def fail_outside_interpolation(cls, value: int) -> int:
            raise NameError("validator typo")

    with pytest.raises(NameError, match="validator typo"):
        ValidatorNameError()

    class TypoChild(C.Config):
        value: int = C.interp(lambda context: misspelled_parent.source)  # type: ignore[name-defined]  # noqa: F821

    typo_template = TypoChild()
    assert C.is_template(typo_template)

    class TypoParent(C.Config):
        child: TypoChild = typo_template

    with pytest.raises(C.ValidationError) as typo:
        TypoParent()
    assert [item["type"] for item in typo.value.errors(include_url=False)] == [
        "nshconfig_interpolation"
    ]

    class CopyingTypoParent(C.Config):
        child: TypoChild = typo_template

        @C.field_validator("child", mode="before")
        @classmethod
        def copy_input(cls, value: Any) -> Any:
            return copy.copy(value)

    with pytest.raises(C.ValidationError) as copied_typo:
        CopyingTypoParent()
    assert copied_typo.value.errors(include_url=False)[0]["type"] == (
        "nshconfig_interpolation"
    )

    class DeepCopyingTypoParent(C.Config):
        child: TypoChild = typo_template

        @C.field_validator("child", mode="before")
        @classmethod
        def copy_input(cls, value: Any) -> Any:
            return copy.deepcopy(value)

    with pytest.raises(C.ValidationError) as deep_copied_typo:
        DeepCopyingTypoParent()
    assert deep_copied_typo.value.errors(include_url=False)[0]["type"] == (
        "nshconfig_interpolation"
    )

    class AliasedTypoChild(C.Config):
        value: int = C.Field(
            default=C.interp(lambda context: another_misspelling.source),  # type: ignore[name-defined]  # noqa: F821
            alias="wire_value",
        )

    aliased_template = AliasedTypoChild()

    class AliasedTypoParent(C.Config):
        child: AliasedTypoChild = aliased_template

    with pytest.raises(C.ValidationError) as aliased_typo:
        AliasedTypoParent()
    assert not C.is_template(aliased_typo.value)
    assert aliased_typo.value.errors(include_url=False)[0]["type"] == (
        "nshconfig_interpolation"
    )

    class NestedTypoLeaf(C.Config):
        value: int = C.interp(lambda context: nested_misspelling.source)  # type: ignore[name-defined]  # noqa: F821

    class NestedTypoBranch(C.Config):
        leaf: NestedTypoLeaf

    nested_template = NestedTypoBranch(leaf={})
    assert C.is_template(nested_template)

    class NestedTypoRoot(C.Config):
        branch: NestedTypoBranch = nested_template

    with pytest.raises(C.ValidationError) as nested_typo:
        NestedTypoRoot()
    assert nested_typo.value.errors(include_url=False)[0]["type"] == (
        "nshconfig_interpolation"
    )


def test_only_normal_construction_can_create_a_template() -> None:
    class ParentBase(C.Config):
        source: int = 1

    class Child(C.Config):
        copied: int = C.interp(lambda context: context.parent(ParentBase).source)

    template = Child()
    for operation in (
        lambda: Child.model_validate(template),
        lambda: C.TypeAdapter(Child).validate_python(template),
        lambda: Child.model_validate({}),
        lambda: Child.model_validate_json("{}"),
        lambda: Child.model_validate_strings({}),
    ):
        with pytest.raises(C.ValidationError):
            operation()

    class Parent(ParentBase):
        child: Child = template

    final = Parent.model_validate({"child": template})
    assert not C.is_template(final)
    assert not C.is_template(final.child)
    assert final.child.copied == 1


def test_templates_bind_in_every_supported_concrete_annotation_position() -> None:
    class Child(C.Config):
        copied: int = C.interp(lambda context: context.parent(Parent).source)

    class Slot(TypedDict):
        child: Child

    class Parent(C.Config):
        source: int = 4
        direct: Child = Child()
        union: Child | None = Child()
        sequence: list[Child] = [Child()]
        tupled: tuple[Child, Child] = (Child(), Child())
        mapping: dict[str, Child] = {"child": Child()}
        typed: Slot = {"child": Child()}

    final = Parent()
    children = (
        final.direct,
        final.union,
        final.sequence[0],
        final.tupled[0],
        final.mapping["child"],
        final.typed["child"],
    )
    assert all(child is not None and child.copied == 4 for child in children)

    work = Parent.config_draft()
    draft_children = (
        work.direct,
        work.union,
        work.sequence[0],
        work.tupled[0],
        work.mapping["child"],
        work.typed["child"],
    )
    assert all(C.is_draft(child) for child in draft_children)
    assert len({id(child) for child in draft_children}) == len(draft_children)
    work.source = 9
    assert work.config_finalize().mapping["child"].copied == 9

    explicit = Parent(
        source=7,
        sequence=[Child()],
        mapping={"child": Child()},
        typed={"child": Child()},
    )
    assert explicit.sequence[0].copied == 7
    assert explicit.mapping["child"].copied == 7
    assert explicit.typed["child"].copied == 7

    schema = Parent.model_json_schema()
    for name in ("direct", "mapping", "sequence", "tupled", "typed", "union"):
        assert "default" not in schema["properties"][name]


def test_templates_are_rejected_under_opaque_annotations() -> None:
    class ParentBase(C.Config):
        source: int = 1

    class Child(C.Config):
        copied: int = C.interp(lambda context: context.parent(ParentBase).source)

    class Opaque(C.Config):
        value: Any

    for value in (Child(), [Child()]):
        with pytest.raises(C.ValidationError) as caught:
            Opaque(value=value)
        assert caught.value.errors(include_url=False)[0]["type"] == (
            "nshconfig_pending_template"
        )


def test_draft_and_template_default_factories_are_rejected() -> None:
    class ParentBase(C.Config):
        source: int = 1

    class Child(C.Config):
        copied: int = C.interp(lambda context: context.parent(ParentBase).source)

    with pytest.raises(TypeError, match=r"direct Config\(\) default"):

        class DirectDraftFactory(C.Config):
            child: Child = C.Field(default_factory=Child.config_draft)

    class OpaqueDraftFactory(C.Config):
        child: Child = C.Field(default_factory=lambda: Child.config_draft())

    with pytest.raises(C.ValidationError, match="factory returned a draft"):
        OpaqueDraftFactory()
    with pytest.raises(C.UnsetError, match="factory that returned a draft"):
        _ = OpaqueDraftFactory.config_draft().child

    class TemplateFactory(C.Config):
        child: Child = C.Field(default_factory=Child)

    with pytest.raises(C.ValidationError, match="unbound template"):
        TemplateFactory()

    class OrdinaryChild(C.Config):
        value: int = 2

    class OrdinaryFactory(C.Config):
        child: OrdinaryChild = C.Field(default_factory=OrdinaryChild)

    assert OrdinaryFactory().child == OrdinaryChild()


def test_template_lifecycle_is_inert_except_for_repr_copy_and_binding() -> None:
    class ParentBase(C.Config):
        source: int = 3

    class Child(C.Config):
        copied: int = C.interp(lambda context: context.parent(ParentBase).source)

        @C.computed_field
        @property
        def constant(self) -> int:
            return 1

        def helper(self) -> int:
            return 1

    template = Child()

    assert template == template
    shallow = copy.copy(template)
    deep = copy.deepcopy(template)
    assert template != shallow != deep
    assert C.is_template(shallow)
    assert C.is_template(deep)
    assert repr(template).startswith("<template Child(")

    for operation in (
        lambda: template.copied,
        lambda: template.constant,
        lambda: template.helper(),
        lambda: setattr(template, "copied", 1),
        lambda: delattr(template, "copied"),
        lambda: template.model_fields_set,
        lambda: template.config_finalize(),
        lambda: template.model_copy(),
        lambda: list(template),
        lambda: template.model_dump(),
        lambda: template.model_dump_json(),
        lambda: hash(template),
    ):
        with pytest.raises((C.TemplateError, TypeError)):
            operation()

    for operation in (
        lambda: C.TypeAdapter(Child).dump_python(template),
        lambda: C.TypeAdapter(Child).dump_json(template),
        lambda: C.BaseModel.model_dump(template),
    ):
        with pytest.raises(PydanticSerializationError):
            operation()

    class Envelope(C.BaseModel):
        child: Child

    bypassed = Envelope.model_construct(child=template)
    with pytest.raises(PydanticSerializationError):
        bypassed.model_dump()

    class Parent(ParentBase):
        child: Child = template

    assert Parent(child=shallow).child.copied == 3
    assert Parent(child=deep).child.copied == 3
    assert "default" not in Parent.model_json_schema()["properties"]["child"]


def test_template_validation_hooks_may_run_before_fallback_and_again_on_binding() -> (
    None
):
    calls: list[str] = []

    class ParentBase(C.Config):
        source: int = 1

    def make_token() -> int:
        calls.append("factory")
        return len(calls)

    class Child(C.Config):
        token: int = C.Field(default_factory=make_token)
        copied: int = C.interp(lambda context: context.parent(ParentBase).source)

    template = Child()
    assert C.is_template(template)
    assert calls == ["factory"]

    class Parent(ParentBase):
        child: Child = template

    final = Parent()
    assert calls == ["factory", "factory"]
    assert final.child.token == 2
    assert final.child.copied == 1
