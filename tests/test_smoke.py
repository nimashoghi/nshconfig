"""The V2_CORE.md section-1 scenario, end to end (the first acceptance script)."""

import pytest
from pydantic import ValidationError

import nshconfig as C
from tests.scenario import ModelConfig, TrainConfig


def test_scenario_end_to_end():
    cfg = TrainConfig.config_draft()
    cfg.model.dim = 1024
    cfg.model.encoder.ln.dim = C.interp(lambda c: c.nearest(ModelConfig).dim)
    cfg.model.decoder.ln.dim = 64

    final = C.finalize(cfg)
    assert final.model.encoder.ln.dim == 1024  # instance marker followed the knob
    assert final.model.decoder.ln.dim == 64  # explicit leaf override won
    assert final.model.head.dim == 1024  # class-default marker, same machinery
    assert final.model.encoder.ln.eps == 1e-5
    dump = final.model_dump()
    assert dump["model"]["encoder"]["ln"] == {"dim": 1024, "eps": 1e-5}


def test_instance_marker_overridden_and_rearmed():
    cfg = TrainConfig.config_draft()
    cfg.model.dim = 512
    cfg.model.encoder.ln.dim = C.interp(lambda c: c.nearest(ModelConfig).dim)
    cfg.model.encoder.ln.dim = 17  # concrete write wins (last write)
    assert C.finalize(cfg).model.encoder.ln.dim == 17
    del cfg.model.encoder.ln.dim  # re-arms: back to the plain class default
    assert C.finalize(cfg).model.encoder.ln.dim == 32


def test_dict_input_marker_no_drafts():
    f = TrainConfig.model_validate(
        {
            "model": {
                "encoder": {"ln": {"dim": C.interp(lambda c: c.root.batch * 4)}},
                "decoder": {"ln": {}},
                "head": {},
            }
        }
    )
    assert f.model.encoder.ln.dim == 32  # 8 * 4


def test_untouched_and_zero_touch_trees_derive():
    cfg = TrainConfig.config_draft()
    cfg.model.dim = 4096  # touch ONLY the knob
    f = C.finalize(cfg)
    assert f.model.head.dim == 4096
    assert C.finalize(TrainConfig.config_draft()).model.head.dim == 768


def test_finalize_idempotent_and_frozen():
    f = C.finalize(TrainConfig.config_draft())
    assert C.finalize(f) is f
    with pytest.raises(ValidationError):
        f.batch = 1  # type: ignore[misc]  # frozen final


def test_draft_is_real_instance_with_loud_gates():
    cfg = TrainConfig.config_draft()
    assert isinstance(cfg, TrainConfig)
    assert C.is_draft(cfg) and not C.is_draft(C.finalize(cfg))
    with pytest.raises(C.UnsetError, match="interpolated"):
        _ = cfg.model.head.dim  # pending interp read is loud
    with pytest.raises(C.DraftError, match="not serializable"):
        cfg.model_dump()
    with pytest.raises(AttributeError, match="did you mean 'dim'"):
        cfg.model.dmi = 3  # type: ignore[attr-defined]


def test_shared_marker_object_in_both_slots():
    width = C.interp(lambda c: c.root.model.dim)

    class Probe(C.Config):
        a: int = width
        b: int = 0

    class Train2(C.Config):
        model: ModelConfig
        probe: Probe

    t = Train2.config_draft()
    t.model.dim = 96
    t.probe.b = width  # the very same object, instance slot
    f = C.finalize(t)
    assert (f.probe.a, f.probe.b) == (96, 96)
    assert "a" not in f.probe.__pydantic_fields_set__  # injected default scrubbed
    assert "b" in f.probe.__pydantic_fields_set__  # instance marker is user-set
