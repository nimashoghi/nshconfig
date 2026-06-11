"""Draft/finalize lifecycle: construction, vivification, del, freezing, equality."""

import pytest
from pydantic import ValidationError

import nshconfig as C
from tests.scenario import DecoderConfig, EncoderConfig, LNConfig, ModelConfig, TrainConfig


def test_nested_helper_needs_no_ceremony():
    # The original v1 pain: helpers editing nested configs crashed unless the
    # caller pre-assigned child drafts. v2 auto-vivifies on access.
    def my_config_(config: TrainConfig) -> None:
        config.model.encoder.ln.eps = 1e-6

    cfg = TrainConfig.config_draft()
    my_config_(cfg)
    assert C.finalize(cfg).model.encoder.ln.eps == 1e-6


def test_draft_seeds_are_explicit_provenance():
    cfg = ModelConfig.config_draft(dim=512)
    assert cfg.dim == 512
    assert "dim" in cfg.__pydantic_fields_set__
    final = C.finalize(cfg)
    assert "dim" in final.__pydantic_fields_set__


def test_vivified_children_are_present_but_not_user_set():
    cfg = TrainConfig.config_draft()
    _ = cfg.model.encoder  # vivify two levels
    assert "model" not in cfg.__pydantic_fields_set__
    assert C.is_draft(cfg.model) and C.is_draft(cfg.model.encoder)


def test_del_restores_default_then_missing_error():
    class Strict(C.Config):
        steps: int  # required, no default

    d = Strict.config_draft()
    d.steps = 5
    del d.steps
    with pytest.raises(ValidationError) as ei:
        C.finalize(d)
    assert ei.value.errors()[0]["type"] == "missing"


def test_missing_errors_are_per_leaf_for_untouched_subtrees():
    class StrictLN(C.Config):
        dim: int  # required

    class Enc(C.Config):
        ln: StrictLN

    class Root(C.Config):
        enc: Enc

    with pytest.raises(ValidationError) as ei:
        C.finalize(Root.config_draft())
    locs = [e["loc"] for e in ei.value.errors()]
    assert ("enc", "ln", "dim") in locs  # leaf-level, not "enc: Field required"


def test_finalize_non_destructive_sweep_loop():
    cfg = TrainConfig.config_draft()
    cfg.model.dim = 256
    f1 = C.finalize(cfg)
    cfg.model.dim = 512  # the draft stays live
    f2 = C.finalize(cfg)
    assert (f1.model.head.dim, f2.model.head.dim) == (256, 512)


def test_reading_unset_required_is_loud():
    class Strict(C.Config):
        steps: int

    with pytest.raises(C.UnsetError, match="not set on this draft"):
        _ = Strict.config_draft().steps


def test_equality_and_hash_ignore_history():
    a = TrainConfig.config_draft()
    a.model.dim = 100
    b = TrainConfig.config_draft()
    with C.source("other-history"):
        b.model.dim = 50
        b.model.dim = 100
    fa, fb = C.finalize(a), C.finalize(b)
    assert fa == fb
    assert hash(fa) == hash(fb)
    assert C.provenance(fa) != C.provenance(fb)  # histories genuinely differ


def test_exclude_unset_dump_reflects_explicit_provenance_only():
    cfg = TrainConfig.config_draft()
    cfg.model.dim = 1024
    final = C.finalize(cfg)
    dump = final.model_dump(exclude_unset=True)
    assert dump == {"model": {"dim": 1024}}  # head.dim was interpolated, not user-set


def test_thaw_rederives_interpolation():
    cfg = TrainConfig.config_draft()
    cfg.model.dim = 1024
    cfg.model.decoder.ln.dim = 64  # explicit leaf override
    x = C.finalize(cfg)
    assert C.finalize(C.thaw(x)) == x  # invariant: round trip is identity

    t = C.thaw(x)
    t.model.dim = 2048  # bump the knob...
    y = C.finalize(t)
    assert y.model.head.dim == 2048  # ...interpolated values re-derive
    assert y.model.decoder.ln.dim == 64  # ...explicit overrides stick


def test_whole_subconfig_assignment_is_fully_explicit():
    cfg = TrainConfig.config_draft()
    cfg.model.encoder = EncoderConfig(ln=LNConfig(dim=8))
    final = C.finalize(cfg)
    assert final.model.encoder.ln.dim == 8
    assert "encoder" in final.model.__pydantic_fields_set__


def test_draft_equals_final_when_values_match():
    # Equality is data-equality; lifecycle stage is queried via is_draft.
    cfg = ModelConfig.config_draft()
    cfg.dim = 768
    cfg.encoder = EncoderConfig(ln=LNConfig())
    cfg.decoder = DecoderConfig(ln=LNConfig())
    final = C.finalize(cfg)
    cfg2 = C.thaw(final)
    assert C.is_draft(cfg2) and not C.is_draft(final)
