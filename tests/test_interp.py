"""Interpolation is a declaration-ordered read of canonical Pydantic values."""

from typing import Annotated, Any

import pytest
from pydantic import Field, ValidationError, field_validator, model_validator

import nshconfig as C


class BasicConfig(C.Config):
    source: int = 4
    class_derived: int = C.interp(lambda context: context.current().source * 2)
    replaceable: int = 7


def test_class_default_and_instance_markers_share_one_validation_boundary():
    assert BasicConfig().class_derived == 8

    work = BasicConfig.config_draft()
    work.source = 5
    work.replaceable = C.interp(lambda context: context.current().source + 1)

    final = work.config_finalize()
    assert final.class_derived == 10
    assert final.replaceable == 6


def test_explicit_values_override_markers_and_deletion_reactivates_defaults():
    work = BasicConfig.config_draft()
    work.source = 5
    work.class_derived = 99
    work.replaceable = C.interp(lambda context: context.current().source + 1)

    assert work.config_finalize().class_derived == 99
    assert work.config_finalize().replaceable == 6

    del work.class_derived
    del work.replaceable

    final = work.config_finalize()
    assert final.class_derived == 10
    assert final.replaceable == 7


def test_alias_selected_and_field_validator_normalized_source_is_canonical():
    calls: list[int] = []

    class AliasedConfig(C.Config):
        source: int = Field(alias="wire_source")
        copied: int = Field(
            default=C.interp(lambda context: context.current().source),
            alias="wire_copied",
        )

        @field_validator("source")
        @classmethod
        def normalize_source(cls, value: int) -> int:
            calls.append(value)
            return value * 10

    interpolated = AliasedConfig.model_validate({"wire_source": 3})
    assert interpolated.source == 30
    assert interpolated.copied == 30
    assert calls == [3]

    calls.clear()
    overridden = AliasedConfig.model_validate({"wire_source": 4, "wire_copied": 91})
    assert overridden.source == 40
    assert overridden.copied == 91
    assert calls == [4]


def test_interpolation_cannot_read_a_later_field():
    class WrongOrder(C.Config):
        early: int = C.interp(lambda context: context.current().later)
        later: int = 7

    with pytest.raises(ValidationError) as caught:
        WrongOrder()

    error = caught.value.errors(include_url=False)[0]
    assert error["loc"] == ("early",)
    assert error["type"] == "nshconfig_interpolation"
    assert error["ctx"]["path"] == "early"
    assert "later has not been validated yet" in error["ctx"]["error"]
    assert "declare the source field" in error["ctx"]["error"]


def test_interpolated_result_runs_the_target_field_pipeline():
    class ConstrainedConfig(C.Config):
        source: int = -3
        positive: Annotated[int, Field(gt=0)] = C.interp(
            lambda context: context.current().source
        )

    with pytest.raises(ValidationError) as caught:
        ConstrainedConfig()

    error = caught.value.errors(include_url=False)[0]
    assert error["loc"] == ("positive",)
    assert error["type"] == "greater_than"
    assert error["ctx"] == {"gt": 0}


def test_model_before_changes_input_before_fields_and_interpolation_run():
    calls: list[dict[str, Any]] = []

    class BeforeConfig(C.Config):
        source: int
        copied: int = C.interp(lambda context: context.current().source)

        @model_validator(mode="before")
        @classmethod
        def rewrite_input(cls, value: Any) -> Any:
            assert isinstance(value, dict)
            calls.append(dict(value))
            return {**value, "source": value["source"] * 10}

    final = BeforeConfig.model_validate({"source": 3})
    assert final.source == 30
    assert final.copied == 30
    assert calls == [{"source": 3}]


def test_model_after_can_change_a_published_field_with_native_pydantic_semantics():
    class MutatingAfterValidator(C.Config):
        source: int = 3
        copied: int = C.interp(lambda context: context.current().source)

        @model_validator(mode="after")
        def change_source(self) -> "MutatingAfterValidator":
            object.__setattr__(self, "source", self.source + 1)
            return self

    final = MutatingAfterValidator()
    assert (final.source, final.copied) == (4, 3)


def test_typed_and_untyped_selectors_read_the_active_root_to_current_path():
    class Constants(C.Config):
        scale: int

    class Leaf(C.Config):
        own: int
        current_untyped: int = C.interp(lambda context: context.current().own)
        current_typed: int = C.interp(lambda context: context.current(Leaf).own)
        parent_untyped: int = C.interp(lambda context: context.parent().branch_value)
        parent_typed: int = C.interp(
            lambda context: context.parent(Branch).branch_value
        )
        root_untyped: int = C.interp(lambda context: context.root().constants.scale)
        root_typed: int = C.interp(lambda context: context.root(Root).root_value)
        grandparent_untyped: int = C.interp(
            lambda context: context.parent(2).root_value
        )
        grandparent_typed: int = C.interp(
            lambda context: context.parent(2, Root).root_value
        )
        nearest_branch: int = C.interp(
            lambda context: context.nearest(Branch).branch_value
        )
        through_active_path: int = C.interp(
            lambda context: context.root().branch.leaf.own
        )

    class Branch(C.Config):
        branch_value: int
        leaf: Leaf

    class Root(C.Config):
        root_value: int
        constants: Constants
        branch: Branch

    final = Root.model_validate(
        {
            "root_value": 7,
            "constants": {"scale": 11},
            "branch": {"branch_value": 13, "leaf": {"own": 17}},
        }
    )

    leaf = final.branch.leaf
    assert leaf.current_untyped == 17
    assert leaf.current_typed == 17
    assert leaf.parent_untyped == 13
    assert leaf.parent_typed == 13
    assert leaf.root_untyped == 11
    assert leaf.root_typed == 7
    assert leaf.grandparent_untyped == 7
    assert leaf.grandparent_typed == 7
    assert leaf.nearest_branch == 13
    assert leaf.through_active_path == 17


def test_config_children_in_typed_lists_and_dicts_keep_parent_and_root_context():
    class Item(C.Config):
        value: int
        from_parent: int = C.interp(lambda context: context.parent().scale)
        from_typed_parent: int = C.interp(
            lambda context: context.parent(Collection).scale
        )
        from_root: int = C.interp(lambda context: context.root().scale)
        from_typed_root: int = C.interp(lambda context: context.root(Collection).scale)

    class Collection(C.Config):
        scale: int
        items: list[Item]
        indexed: dict[str, Item]

    first = Item.config_draft()
    first.value = 1
    second = Item.config_draft()
    second.value = 2

    work = Collection.config_draft()
    work.scale = 9
    work.items = [first]
    work.indexed = {"second": second}

    final = work.config_finalize()
    assert not C.is_draft(final.items[0])
    assert not C.is_draft(final.indexed["second"])
    assert final.items[0].value == 1
    assert final.indexed["second"].value == 2
    for item in [final.items[0], final.indexed["second"]]:
        assert item.from_parent == 9
        assert item.from_typed_parent == 9
        assert item.from_root == 9
        assert item.from_typed_root == 9


def test_model_validate_called_inside_a_resolver_starts_a_fresh_root():
    class Independent(C.Config):
        source: int
        copied: int = C.interp(lambda context: context.root(Independent).source)

    class Outer(C.Config):
        source: int
        nested_result: int = C.interp(
            lambda context: Independent.model_validate({"source": 11}).copied
        )
        stack_was_restored: int = C.interp(
            lambda context: context.root(Outer).source + context.current().nested_result
        )

    final = Outer.model_validate({"source": 7})
    assert final.nested_result == 11
    assert final.stack_was_restored == 18


def _raise_resolver_error(context: C.Context) -> int:
    del context
    raise RuntimeError("resolver exploded")


def test_resolver_failure_has_a_structured_nested_path_and_callable_site():
    class BrokenLeaf(C.Config):
        value: int = C.interp(_raise_resolver_error)

    class BrokenRoot(C.Config):
        leaf: BrokenLeaf

    with pytest.raises(ValidationError) as caught:
        BrokenRoot.model_validate({"leaf": {}})

    error = caught.value.errors(include_url=False)[0]
    assert error["loc"] == ("leaf", "value")
    assert error["type"] == "nshconfig_interpolation"
    assert error["ctx"]["path"] == "leaf.value"
    assert "_raise_resolver_error" in error["ctx"]["marker"]
    assert "test_interp.py" in error["ctx"]["marker"]
    assert error["ctx"]["error"] == "resolver exploded"

    # A failed resolver must restore every ContextVar before the next validation.
    assert BasicConfig(source=6).class_derived == 12
