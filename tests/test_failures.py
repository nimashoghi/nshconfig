"""The failure inventory: every degradation is loud and names its location."""

from typing import Any

import pytest
from pydantic import ValidationError

import nshconfig as C
from tests.test_interp import EncoderConfig, Pair, TrainConfig


class PlainLeaf(C.Config):
    y: int = 5


class Mid(C.Config):
    x: int = 0
    leaf: PlainLeaf


def test_orphan_names_path_site_and_ancestors():
    with pytest.raises(ValidationError) as ei:
        C.finalize(EncoderConfig.config_draft())
    msg = str(ei.value)
    assert "cannot interpolate ln.dim" in msg
    assert "LNConfig.dim = interp(" in msg
    assert "no enclosing ModelConfig" in msg
    assert "ancestors here: EncoderConfig > LNConfig" in msg


def test_parent_at_validation_root_is_loud():
    class Root(C.Config):
        note: str = "x"

    d = Root.config_draft()
    d.note = C.interp(lambda c: c.parent.x)
    with pytest.raises(ValidationError, match="no parent: this model is the validation root"):
        C.finalize(d)


def test_mutual_cycle_names_both_ends():
    d = Pair.config_draft()
    d.a.x = C.interp(lambda c: c.root.b.y)
    d.b.y = C.interp(lambda c: c.root.a.x)
    with pytest.raises(ValidationError) as ei:
        C.finalize(d)
    msg = str(ei.value)
    assert msg.count("pending interpolation") == 2  # both ends, one report
    assert "cannot interpolate a.x" in msg and "cannot interpolate b.y" in msg


def test_self_cycle_is_loud():
    class Lvl(C.Config):
        a: int = 10

    d = Lvl.config_draft()
    d.a = C.interp(lambda c: c.data.a)
    with pytest.raises(ValidationError, match="pending interpolation"):
        C.finalize(d)


def test_updown_cycles_class_and_instance_flavor():
    class LeafUp(C.Config):
        y: int = C.interp(lambda c: c.parent.x)

    class MidDown(C.Config):
        x: int = 0
        leaf: LeafUp

    d = MidDown.config_draft()
    d.x = C.interp(lambda c: c.data.leaf.y)
    with pytest.raises(ValidationError, match="interpolated and not filled yet"):
        C.finalize(d)

    d2 = Mid.config_draft()
    d2.x = C.interp(lambda c: c.data.leaf.y)
    d2.leaf.y = C.interp(lambda c: c.parent.x)
    with pytest.raises(ValidationError, match="pending interpolation"):
        C.finalize(d2)


def test_conservative_chain_through_sibling_marker_with_workaround():
    d = Pair.config_draft()
    d.a.x = C.interp(lambda c: c.root.b.y)
    d.b.y = C.interp(lambda c: c.root.width)
    with pytest.raises(ValidationError):  # one-pass: loud, never wrong
        C.finalize(d)

    d = Pair.config_draft()
    d.a.x = C.interp(lambda c: c.root.width)  # the documented workaround:
    d.b.y = C.interp(lambda c: c.root.width)  # point both at the source
    f = C.finalize(d)
    assert (f.a.x, f.b.y) == (64, 64)


def test_smuggled_marker_caught_by_root_sweep():
    class Meta(C.Config):
        meta: Any = None

    d = Meta.config_draft()
    d.meta = [C.interp(lambda c: c.root)]
    with pytest.raises(ValidationError) as ei:
        C.finalize(d)
    msg = str(ei.value)
    assert "leaked into the final at Meta.meta[0]" in msg

    d = Meta.config_draft()
    d.meta = C.interp(lambda c: 42)  # directly in field position under Any: fine
    assert C.finalize(d).meta == 42


def test_marker_hygiene_dunders():
    m = C.interp(lambda c: c.root.width)
    with pytest.raises(C.DraftError, match="boolean context"):
        bool(m)
    with pytest.raises(C.DraftError, match="format"):
        f"{m}"
    with pytest.raises(TypeError):
        m * 2  # type: ignore[operator]
    # == is identity, documented sharp edge: distinct markers compare unequal.
    assert m != C.interp(lambda c: c.root.width)


def test_reentrancy_guard_nested_validation_is_fresh_root():
    class Inner(C.Config):
        v: int = C.interp(lambda c: c.root.q)

    def nested() -> int:
        try:
            Inner.model_validate({})
            return -1  # resolved against the OUTER tree: guard failed
        except ValidationError:
            return 42  # fresh root -> orphan error -> guard works

    class Outer(C.Config):
        q: int = 5
        z: int = C.interp(lambda c: nested())

    assert C.finalize(Outer.config_draft()).z == 42


def test_resolver_exceptions_are_wrapped_with_location():
    class Root(C.Config):
        n: int = C.interp(lambda c: 1 // 0)

    with pytest.raises(ValidationError) as ei:
        C.finalize(Root.config_draft())
    msg = str(ei.value)
    assert "cannot interpolate n" in msg and "division" in msg


def test_pending_repr_labels():
    d = TrainConfig.config_draft()
    d.model.dim = C.interp(lambda c: c.root.width * 2)
    r = repr(d.model)
    assert "pending: instance interp(" in r
    r2 = repr(TrainConfig.config_draft().model.encoder.ln)
    assert "pending: class default interp(" in r2

    class Req(C.Config):
        steps: int
        sub: PlainLeaf

    r3 = repr(Req.config_draft())
    assert "steps=[UNSET]" in r3 and "sub=<untouched PlainLeaf>" in r3
