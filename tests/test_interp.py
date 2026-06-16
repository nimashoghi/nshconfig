"""Resolution semantics: the one rule, the precedence ladder, the visibility matrix.

Ported from the design panel's verified T-series battery (semU), adapted to the
v2 API, plus the Field/Annotated composition checks from the playground session.
"""

from typing import Annotated

import pytest
from pydantic import Field, ValidationError

import nshconfig as C

# ---- family 1: class-default marker ----


class LNConfig(C.Config):
    dim: int = C.interp(lambda c: c.nearest(ModelConfig).dim)
    eps: float = 1e-5


class EncoderConfig(C.Config):
    ln: LNConfig


class ModelConfig(C.Config):
    dim: int = 768
    encoder: EncoderConfig


class TrainConfig(C.Config):
    width: int = 512
    model: ModelConfig


# ---- family 2: NO markers anywhere in class bodies (pure instance-level) ----


class PlainLN(C.Config):
    dim: int = 16
    eps: float = 1e-5


class PlainEncoder(C.Config):
    ln: PlainLN


class PlainModel(C.Config):
    dim: int = 768
    encoder: PlainEncoder


class PlainTrain(C.Config):
    width: int = 512
    model: PlainModel
    note: str = "x"


# ---- family 3: siblings ----


class A(C.Config):
    x: int = 0


class B(C.Config):
    y: int = 0


class Pair(C.Config):
    width: int = 64
    a: A
    b: B


class ReqX(C.Config):
    x: int  # required scalar


class PairReq(C.Config):
    width: int = 64
    r: ReqX


# ---- family 4: same level ----


class Lvl(C.Config):
    a: int = 10
    b: int = 2
    c2: int = 3


def test_t1_class_default_marker_fills_absent_field():
    d = TrainConfig.config_draft()
    d.model.dim = 1024
    assert C.finalize(d).model.encoder.ln.dim == 1024


def test_t2_instance_marker_on_plain_classes():
    d = PlainTrain.config_draft()
    d.model.encoder.ln.dim = C.interp(lambda c: c.nearest(PlainModel).dim)
    d.model.dim = 320
    assert C.finalize(d).model.encoder.ln.dim == 320


def test_t3_marker_as_plain_dict_input_value():
    f = PlainTrain.model_validate(
        {
            "model": {
                "dim": 256,
                "encoder": {"ln": {"dim": C.interp(lambda c: c.nearest(PlainModel).dim)}},
            }
        }
    )
    assert f.model.encoder.ln.dim == 256


def test_t5_precedence_ladder_and_del():
    d = TrainConfig.config_draft()
    d.model.encoder.ln.dim = 7
    assert C.finalize(d).model.encoder.ln.dim == 7  # concrete beats class marker
    del d.model.encoder.ln.dim
    assert C.finalize(d).model.encoder.ln.dim == 768  # del re-arms class marker

    d = TrainConfig.config_draft()
    d.model.dim = 100
    d.model.dim = 200
    assert C.finalize(d).model.dim == 200  # last write wins

    d = TrainConfig.config_draft()
    d.model.dim = 10
    d.model.encoder.ln.dim = C.interp(lambda c: c.nearest(ModelConfig).dim * 2)
    d.model.encoder.ln.dim = 5
    assert C.finalize(d).model.encoder.ln.dim == 5  # marker then concrete

    d = TrainConfig.config_draft()
    d.model.dim = 10
    d.model.encoder.ln.dim = 5
    d.model.encoder.ln.dim = C.interp(lambda c: c.nearest(ModelConfig).dim * 2)
    assert C.finalize(d).model.encoder.ln.dim == 20  # concrete then marker

    d = PlainTrain.config_draft()
    d.model.encoder.ln.eps = C.interp(lambda c: c.root().width / 100)
    del d.model.encoder.ln.eps
    assert C.finalize(d).model.encoder.ln.eps == 1e-5  # del marker -> static default

    d = PairReq.config_draft()
    d.r.x = C.interp(lambda c: c.root().width)
    del d.r.x
    with pytest.raises(ValidationError):  # del marker on required -> missing
        C.finalize(d)

    d = PlainTrain.config_draft()
    del d.model.dim  # never set: no-op
    assert C.finalize(d).model.dim == 768


def test_t6_marker_satisfies_required_field():
    d = PairReq.config_draft()
    d.r.x = C.interp(lambda c: c.root().width)
    assert C.finalize(d).r.x == 64

    d = PairReq.config_draft()
    d.r = ReqX.config_draft(x=C.interp(lambda c: c.root().width))  # marker via draft(**kwargs)
    assert C.finalize(d).r.x == 64


def test_t7_ancestor_frames_resolved_root_descent_raw():
    d = TrainConfig.config_draft()
    d.model.dim = C.interp(lambda c: c.root().width * 2)
    f = C.finalize(d)
    assert f.model.dim == 1024
    assert f.model.encoder.ln.dim == 1024  # class marker chains off RESOLVED ancestor

    d = PlainTrain.config_draft()
    d.model.dim = C.interp(lambda c: c.root().width * 2)
    d.model.encoder.ln.dim = C.interp(lambda c: c.nearest(PlainModel).dim)
    assert C.finalize(d).model.encoder.ln.dim == 1024  # chain via nearest() frame

    d = PlainTrain.config_draft()
    d.model.dim = C.interp(lambda c: c.root().width * 2)
    d.model.encoder.ln.dim = C.interp(lambda c: c.root().model.dim)  # raw frame!
    with pytest.raises(ValidationError, match="pending interpolation"):
        C.finalize(d)  # root dotted descent sees raw input: documented one-pass limit


def test_t8_sibling_reads_via_root():
    d = Pair.config_draft()
    d.b.y = 7
    d.a.x = C.interp(lambda c: c.root().b.y)  # a validates BEFORE b
    assert C.finalize(d).a.x == 7

    d = Pair.config_draft()
    d.a.x = 3
    d.b.y = C.interp(lambda c: c.root().a.x)  # b validates AFTER a
    assert C.finalize(d).b.y == 3

    d = Pair.config_draft()
    d.a.x = C.interp(lambda c: c.root().b.y)  # b untouched: static class default
    assert C.finalize(d).a.x == 0


def test_t10_same_level_declaration_order():
    d = Lvl.config_draft()
    d.b = C.interp(lambda c: c.self().a * 2)
    d.c2 = C.interp(lambda c: c.self().b + 1)  # reads the RESOLVED b (earlier in order)
    f = C.finalize(d)
    assert (f.b, f.c2) == (20, 21)

    d = Lvl.config_draft()
    d.a = C.interp(lambda c: c.self().b)  # later field, concrete
    d.b = 5
    assert C.finalize(d).a == 5

    d = Lvl.config_draft()
    d.a = C.interp(lambda c: c.self().b)  # later field untouched: static default
    assert C.finalize(d).a == 2


def test_t11_below_reads_into_own_subtree():
    class PlainLeaf(C.Config):
        y: int = 5
        z: int = 0

    class PlainMid(C.Config):
        x: int = 0
        leaf: PlainLeaf

    d = PlainMid.config_draft()
    d.x = C.interp(lambda c: c.self().leaf.z)
    d.leaf.z = 9
    assert C.finalize(d).x == 9  # below-read of user-set value

    d = PlainMid.config_draft()
    d.x = C.interp(lambda c: c.self().leaf.y)  # leaf untouched: static default
    assert C.finalize(d).x == 5


def test_field_and_annotated_composition():
    class Tunable(C.Config):
        dim: int = 555
        a: int = Field(default=C.interp(lambda c: c.parent().dim), gt=0)
        b: Annotated[int, Field(multiple_of=5)] = C.interp(lambda c: c.parent().dim)

    class Host(C.Config):
        dim: int = 555
        t: Tunable

    f = Host.model_validate({"t": {}})
    assert (f.t.a, f.t.b) == (555, 555)
    assert Tunable(dim=1, a=7, b=10).a == 7  # explicit beats marker everywhere

    class BadHost(C.Config):
        dim: int = -3  # violates a's gt=0 AFTER resolution
        t: Tunable

    with pytest.raises(ValidationError, match="greater than 0"):
        BadHost.model_validate({"t": {"b": 10}})  # interpolation feeds validation


def test_ctx_typed_and_untyped_selectors():
    class Leaf(C.Config):
        own: int = 2
        from_root_untyped: int = C.interp(lambda c: c.root().width)
        from_root_typed: int = C.interp(lambda c: c.root(PlainTrain).width)
        from_parent_typed: int = C.interp(lambda c: c.parent(PlainModel).dim)
        from_self_typed: int = C.interp(lambda c: c.self(Leaf).own)

    class Model(PlainModel):
        dim: int = 9
        leaf: Leaf

    class RootTrain(PlainTrain):
        model: Model

    d = RootTrain.config_draft()
    d.width = 23
    d.model.dim = 11
    f = C.finalize(d)
    assert f.model.leaf.from_root_untyped == 23
    assert f.model.leaf.from_root_typed == 23
    assert f.model.leaf.from_parent_typed == 11
    assert f.model.leaf.from_self_typed == 2

    class UntypedLeaf(C.Config):
        from_root: int = C.interp(lambda c: c.root().width)

    class UntypedRoot(C.Config):
        width: int = 17
        leaf: UntypedLeaf

    assert C.finalize(UntypedRoot.config_draft()).leaf.from_root == 17


def test_ctx_up_exact_hops_and_parent_field_names():
    class Leaf(C.Config):
        grandparent_dim: int = C.interp(lambda c: c.up(2).dim)
        grandparent_dim_typed: int = C.interp(lambda c: c.up(2, PlainModel).dim)

    class HasParentField(C.Config):
        parent: int = 31
        child_value: int = C.interp(lambda c: c.self().parent)

    class Mid(C.Config):
        leaf: Leaf
        named: HasParentField

    class Model(PlainModel):
        mid: Mid

    class Root(C.Config):
        model: Model

    d = Root.config_draft()
    d.model.dim = 41
    f = C.finalize(d)
    assert f.model.mid.leaf.grandparent_dim == 41
    assert f.model.mid.leaf.grandparent_dim_typed == 41
    assert f.model.mid.named.child_value == 31


def test_direct_construction_semantics():
    class SelfSufficient(C.Config):
        dim: int = 64
        run_name: str = C.interp(lambda c: f"run-d{c.self().dim}")

    # a marker reading only its own level resolves even standalone:
    assert SelfSufficient(dim=128).run_name == "run-d128"
    # explicit always works:
    assert LNConfig(dim=7).dim == 7
    # a marker needing ancestors fails loudly at direct construction (its own root):
    with pytest.raises(ValidationError, match="no enclosing ModelConfig"):
        LNConfig()


def test_nearest_disambiguates_per_subtree():
    class Distill(C.Config):
        teacher: ModelConfig
        student: ModelConfig

    d = Distill.config_draft()
    d.teacher.dim = 1024
    d.student.dim = 256
    f = C.finalize(d)
    assert f.teacher.encoder.ln.dim == 1024
    assert f.student.encoder.ln.dim == 256
