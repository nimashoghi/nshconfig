"""The config_* verb family: methods over module verbs, with polite collisions."""

import warnings

import pytest

import nshconfig as C
from tests.scenario import ModelConfig, TrainConfig


def test_method_family_end_to_end():
    cfg = TrainConfig.config_draft()
    cfg.model.dim = 1024
    cfg.model.encoder.ln.dim = C.interp(lambda c: c.nearest(ModelConfig).dim)
    assert cfg.config_is_draft

    final = cfg.config_finalize()
    assert not final.config_is_draft
    assert final.model.encoder.ln.dim == 1024
    assert final.config_finalize() is final  # idempotent, like the module verb

    ex = final.config_explain("model.head.dim")
    assert "because model.dim = 1024" in str(ex)
    assert "model.head.dim" in final.config_provenance()

    t = final.config_thaw()
    t.model.dim = 2048
    assert t.config_finalize().model.head.dim == 2048


def test_methods_and_module_verbs_agree():
    cfg = TrainConfig.config_draft()
    cfg.model.dim = 256
    assert cfg.config_finalize() == C.finalize(cfg)
    assert cfg.config_is_draft == C.is_draft(cfg)


def test_collision_field_wins_exactly():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")

        class Finetune(C.Config):
            config_finalize: str = "soft"  # silly, but legal: the field wins

    # No parent attribute exists to shadow (verbs are __getattr__-dispatched),
    # so there is no pydantic shadow warning: the collision is silent and exact.
    assert [str(x.message) for x in w] == []

    ft = Finetune.config_draft()
    assert ft.config_finalize == "soft"  # declared field: reads are field reads
    ft.config_finalize = "hard"
    assert ft.config_finalize == "hard"
    final = C.finalize(ft)  # the module verb is the universal fallback
    assert final.config_finalize == "hard"

    class Strict(C.Config):
        config_thaw: int  # required, unset on a fresh draft

    with pytest.raises(C.UnsetError, match="not set on this draft"):
        _ = Strict.config_draft().config_thaw  # field semantics, not the verb


def test_prefix_is_not_policed():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")

        class HF(C.Config):  # config_* fields are everyday ML vocabulary
            config_name: str = "base"
            config_path: str = "/tmp"

    assert [str(x.message) for x in w] == []  # no protected-namespace noise
    assert C.finalize(HF.config_draft()).config_name == "base"


def test_verbs_unreachable_as_draft_writes():
    cfg = TrainConfig.config_draft()
    with pytest.raises(AttributeError, match="has no field"):
        cfg.config_finalize = lambda: None  # type: ignore[method-assign]
