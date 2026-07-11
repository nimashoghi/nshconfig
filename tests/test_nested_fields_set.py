"""Explicit-field metadata across generated required Config spines."""

import nshconfig as C


class Leaf(C.Config):
    value: int = 1


class Branch(C.Config):
    leaf: Leaf


class Root(C.Config):
    branch: Branch


def _nested_field_sets(value: Root) -> tuple[set[str], set[str], set[str]]:
    return (
        value.model_fields_set,
        value.branch.model_fields_set,
        value.branch.leaf.model_fields_set,
    )


def test_untouched_required_spine_remains_unset_after_each_validation() -> None:
    # Do not access the draft's spine: finalize() must preserve metadata even
    # when it has to generate the entire required structure internally.
    final = C.finalize(C.draft(Root))
    repeated = C.finalize(final)

    assert _nested_field_sets(final) == (set(), set(), set())
    assert _nested_field_sets(repeated) == (set(), set(), set())
    assert final.model_dump(exclude_unset=True) == {}
    assert repeated.model_dump(exclude_unset=True) == {}


def test_explicit_nested_drafts_preserve_each_nodes_fields_set() -> None:
    leaf = C.draft(Leaf)
    leaf.value = 7
    branch = C.draft(Branch)
    branch.leaf = leaf
    root = C.draft(Root)
    root.branch = branch

    final = C.finalize(root)
    repeated = C.finalize(final)

    expected = ({"branch"}, {"leaf"}, {"value"})
    assert _nested_field_sets(final) == expected
    assert _nested_field_sets(repeated) == expected
    assert final.model_dump(exclude_unset=True) == {"branch": {"leaf": {"value": 7}}}
    assert repeated.model_dump(exclude_unset=True) == {"branch": {"leaf": {"value": 7}}}
