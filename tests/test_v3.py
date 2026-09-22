from __future__ import annotations

from typing import Annotated, Any

import pytest

import nshconfig as C


class Pairformer(C.Config):
    c_z: int = C.interp(lambda c: c.root(AF3).c_z)
    n_blocks: int = C.interp(lambda c: c.nearest(Model).n_blocks)


class Model(C.Config):
    n_blocks: int = 48
    pairformer: Pairformer = Pairformer()


class Data(C.Config):
    train_sets: list[str] = ["weighted_pdb"]
    weights: list[float] = [1.0]

    @C.check
    def aligned(self) -> None:
        if len(self.train_sets) != len(self.weights):
            raise ValueError("train_sets and weights must align")


class AF3(C.Config):
    project: str
    run_name: str
    c_z: int = 128
    model: Model = Model()
    data: Data = Data()


def experiment() -> AF3:
    config = AF3.draft()
    config.project = "af3"
    config.run_name = "wide"
    config.c_z = 256
    return config


def test_af3_presets_and_independent_snapshots():
    draft = experiment()
    assert draft.model.pairformer.c_z == 256
    draft.model.n_blocks = 32
    assert draft.model.pairformer.n_blocks == 32
    first = draft.finalize()
    draft.c_z = 384
    draft.data.train_sets.append("distillation")
    draft.data.weights.append(0.5)
    second = draft.finalize()
    assert (first.model.pairformer.c_z, second.model.pairformer.c_z) == (256, 384)
    assert first.data.train_sets == ["weighted_pdb"]
    assert second.data.train_sets == ["weighted_pdb", "distillation"]
    assert C.is_draft(draft) and not C.is_draft(first)
    with pytest.raises(C.FrozenError):
        first.model.pairformer.c_z = 2
    with pytest.raises(C.FrozenError):
        first.data.weights.append(2.0)
    with pytest.raises(C.FrozenError):
        first.data.weights[0] = 2.0
    draft.data.weights.clear()
    with pytest.raises(ValueError, match="align"):
        draft.finalize()


def test_required_fields_are_deferred_and_defaults_independent():
    a, b = AF3.draft(), AF3.draft()
    a.model.n_blocks = 1
    a.data.train_sets.append("other")
    assert b.model.n_blocks == 48
    assert b.data.train_sets == ["weighted_pdb"]
    assert a.model.pairformer.c_z == 128
    with pytest.raises(C.MissingValueError, match="project"):
        _ = a.project
    with pytest.raises(C.MissingValueError):
        a.finalize()
    with pytest.raises(AttributeError):
        a.typo = 1
    with pytest.raises(TypeError):
        AF3(typo=1)


def test_declaration_order_canonical_reads_and_override():
    class Run(C.Config):
        doubled: int = C.interp(lambda c: c.current(Run).width * 2)
        width: Annotated[int, C.AfterValidator(lambda x: abs(x) + 1)]

    r = Run(width=-4)
    assert r.doubled == 10
    assert r.width == r.width == 5
    r.width = -5
    assert r.doubled == 12
    r.doubled += 1
    r.width = 100
    assert r.doubled == 13
    assert r.finalize().doubled == 13
    r.width = "bad"
    with pytest.raises(C.ConfigError, match="width"):
        _ = r.width


def test_cycles_missing_dependencies_and_context():
    class Cycle(C.Config):
        a: int = C.interp(lambda c: c.current(Cycle).b)
        b: int = C.interp(lambda c: c.current(Cycle).a)

    with pytest.raises(C.InterpolationError, match=r"Cycle.a.*Cycle.b.*Cycle.a"):
        _ = Cycle().a
    with pytest.raises(C.InterpolationError, match="AF3 root"):
        _ = Pairformer().c_z
    draft = AF3.draft()
    draft.c_z = C.interp(lambda c: len(c.root(AF3).project))
    with pytest.raises(C.MissingValueError, match="project"):
        _ = draft.model.pairformer.c_z


def test_children_preserve_identity_and_one_owner_with_atomic_failure():
    a, b = experiment(), experiment()
    pair = Pairformer()
    a.model.pairformer = pair
    pair.c_z = 777
    assert a.model.pairformer is pair
    assert a.model.pairformer.c_z == 777
    original = b.model.pairformer
    with pytest.raises(C.OwnershipError):
        b.model.pairformer = pair
    assert b.model.pairformer is original
    b.model.pairformer = pair.copy()
    assert b.model.pairformer is not pair
    a.model.pairformer = Pairformer()
    b.model.pairformer = pair
    assert b.model.pairformer is pair


def test_copy_preserves_rules_but_final_copy_has_concrete_values():
    a = experiment()
    b = a.copy()
    b.c_z = 2560
    assert b.model.pairformer.c_z == 2560
    assert a.model.pairformer.c_z == 256
    pinned = a.finalize().copy()
    pinned.c_z = 42
    assert pinned.model.pairformer.c_z == 256


def test_plain_containers_are_imported_and_config_aliases_stay_live():
    class Items(C.Config):
        values: list[list[int]]
        models: dict[str, Pairformer] = {}

    raw = [[1]]
    c = Items(values=raw)
    raw[0].append(2)
    assert list(c.values[0]) == [1]
    alias = c.values
    nested = alias[0]
    nested.append(3)
    assert list(c.values[0]) == [1, 3]
    alias.append([4])
    assert list(c.values[1]) == [4]
    child = Pairformer(c_z=64, n_blocks=3)
    c.models["x"] = child
    assert c.models["x"] is child
    with pytest.raises(C.OwnershipError):
        c.models.update({"y": Pairformer(), "z": child})
    assert set(c.models) == {"x"}
    del c.models["x"]
    c.models["z"] = child
    assert c.models["z"] is child


def test_container_normalization_keeps_raw_inputs_and_aliases():
    class Values(C.Config):
        xs: list[Annotated[int, C.AfterValidator(lambda x: x + 1)]] = [1]

    c = Values()
    alias = c.xs
    assert alias[0] == 2
    alias.append(5)
    assert list(alias) == [2, 6]
    assert list(c.xs) == [2, 6]
    alias[0] = 10
    assert list(alias) == [11, 6]
    assert list(c.finalize().xs) == [11, 6]


def test_computed_subtrees_are_readonly_and_source_remains_editable():
    class Child(C.Config):
        value: int = 1
        items: list[int] = [1]

    class Parent(C.Config):
        source: Child = Child()
        computed: Child = C.interp(lambda c: c.current(Parent).source)
        xs: list[int] = C.interp(lambda c: c.current(Parent).source.items)

    c = Parent()
    view = c.computed
    assert view is not c.source
    with pytest.raises(C.FrozenError):
        view.value = 2
    with pytest.raises(C.FrozenError):
        c.xs.append(2)
    c.source.value = 4
    c.source.items.append(2)
    assert c.computed.value == 4 and view.value == 1
    assert c.xs == [1, 2]
    c.computed = Child(value=5)
    c.computed.value = 6
    assert c.finalize().computed.value == 6


def test_new_computed_child_gets_destination_context():
    class Child(C.Config):
        value: int = C.interp(lambda c: c.root(Parent).value)

    class Parent(C.Config):
        value: int = 5
        child: Child = C.interp(lambda c: Child())

    assert Parent().child.value == 5
    assert Parent().finalize().child.value == 5


def test_callbacks_cannot_mutate_source_and_checks_cannot_rewrite():
    def change(c: C.Context) -> int:
        c.current(Bad).value = 5
        return 1

    class Bad(C.Config):
        value: int = 1
        derived: int = C.interp(change)

    b = Bad()
    with pytest.raises(C.FrozenError):
        _ = b.derived
    assert b.value == 1

    class Invalid(C.Config):
        value: int = 1

        @C.check
        def rewrite(self) -> None:
            self.value = 2

    with pytest.raises(C.FrozenError):
        Invalid().finalize()


def test_final_snapshot_freezes_dicts_and_nested_container_values():
    class Values(C.Config):
        mapping: dict[str, list[int]] = {"x": [1]}
        tuples: tuple[list[int], ...] = ([1],)

    c = Values()
    f = c.finalize()
    with pytest.raises(C.FrozenError):
        f.mapping["x"].append(2)
    with pytest.raises(C.FrozenError):
        f.mapping.update(y=[])
    with pytest.raises(C.FrozenError):
        f.tuples[0].append(2)
    c.tuples[0].append(3)
    assert list(c.tuples[0]) == [1, 3]
    assert f.to_dict() == {"mapping": {"x": [1]}, "tuples": ([1],)}


def test_unsupported_opaque_mutability_fails_explicitly():
    class Values(C.Config):
        thing: Any

    with pytest.raises(C.ConfigError, match="immutable leaf"):
        Values(thing=bytearray(b"x")).finalize()


def test_list_reorder_slice_and_duplicate_config_rejection():
    class Child(C.Config):
        value: int

    class Parent(C.Config):
        children: list[Child] = []

    a, b = Child(value=1), Child(value=2)
    c = Parent(children=[a, b])
    c.children.reverse()
    assert list(c.children) == [b, a]
    with pytest.raises(C.OwnershipError):
        c.children *= 2
    assert list(c.children) == [b, a]
    c.children[:] = [a, b]
    assert list(c.children) == [a, b]
    c.children.pop()
    c.children.append(b)
    assert list(c.children) == [a, b]
