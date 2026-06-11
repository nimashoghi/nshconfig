"""Provenance: sites, chains, labels, explain, and the interp read-log."""

from __future__ import annotations

import pickle
from pathlib import Path

import nshconfig as C
from tests.scenario import ModelConfig, TrainConfig


def set_lr_helper(cfg: OptimRoot) -> None:
    cfg.optim.lr = 3e-4


class OptimConfig(C.Config):
    lr: float = 1e-3
    weight_decay: float = 0.0


class OptimRoot(C.Config):
    optim: OptimConfig


def test_override_chain_with_sites_and_labels():
    cfg = OptimRoot.draft()
    set_lr_helper(cfg)  # the helper IS the provenance unit, automatically
    with C.source("sweep:lr"):
        cfg.optim.lr = 1e-4

    ex = C.explain(C.finalize(cfg), "optim.lr")
    assert ex.current == "0.0001"
    assert [e.kind for e in ex.events] == ["set", "set"]
    helper_event, sweep_event = ex.events
    assert helper_event.func == "set_lr_helper"
    assert helper_event.value == "0.0003"
    assert Path(str(helper_event.file)).name == "test_provenance.py"
    assert helper_event.code == "cfg.optim.lr = 3e-4"
    assert sweep_event.label == "sweep:lr"
    assert "class default: 0.001" in str(ex)


def test_del_tombstone_in_chain():
    cfg = OptimRoot.draft()
    cfg.optim.lr = 5e-4
    del cfg.optim.lr
    ex = C.explain(cfg, "optim.lr")
    assert [e.kind for e in ex.events] == ["set", "del"]
    assert ex.current == "<pending/unset>"


def test_seed_events_recorded():
    cfg = OptimConfig.draft(lr=2e-4)
    (ev,) = C.explain(cfg, "lr").events
    assert ev.kind == "seed" and ev.value == "0.0002"


def test_interp_event_carries_site_and_reads():
    cfg = TrainConfig.draft()
    cfg.model.dim = 1024
    final = C.finalize(cfg)
    ex = C.explain(final, "model.head.dim")
    (ev,) = ex.events
    assert ev.kind == "interp" and ev.injected
    assert ev.site is not None and "scenario.py" in ev.site
    assert ("model.dim", "1024") in ev.reads
    assert "because model.dim = 1024" in str(ex)


def test_instance_marker_event_not_injected():
    cfg = TrainConfig.draft()
    cfg.model.dim = 256
    cfg.model.encoder.ln.dim = C.interp(lambda c: c.nearest(ModelConfig).dim)
    final = C.finalize(cfg)
    events = C.explain(final, "model.encoder.ln.dim").events
    assert [e.kind for e in events] == ["set", "interp"]  # marker write, then resolution
    assert events[0].value is not None and events[0].value.startswith("interp(")
    assert not events[1].injected


def test_rule_active_vs_shadowed_states():
    cfg = TrainConfig.draft()
    cfg.model.head.dim = 7
    assert "(shadowed)" in str(C.explain(cfg, "model.head.dim"))
    del cfg.model.head.dim
    assert "(active)" in str(C.explain(cfg, "model.head.dim"))
    assert "(active)" in str(C.explain(C.finalize(cfg), "model.head.dim"))


def test_explain_works_on_drafts_mid_composition():
    cfg = OptimRoot.draft()
    cfg.optim.lr = 9e-4
    ex = C.explain(cfg, "optim.lr")
    assert ex.current == "0.0009"
    assert len(ex.events) == 1


def test_provenance_table_full_tree():
    cfg = TrainConfig.draft()
    cfg.model.dim = 1024
    cfg.model.decoder.ln.dim = 64
    table = C.provenance(C.finalize(cfg))
    assert set(table) >= {"model.dim", "model.decoder.ln.dim", "model.head.dim"}
    assert table["model.head.dim"][-1].kind == "interp"


def test_provenance_survives_pickle():
    cfg = OptimRoot.draft()
    set_lr_helper(cfg)
    final = pickle.loads(pickle.dumps(C.finalize(cfg)))
    (ev,) = C.explain(final, "optim.lr").events
    assert ev.func == "set_lr_helper" and ev.code == "cfg.optim.lr = 3e-4"


def test_value_reprs_are_truncated_not_retained():
    big = list(range(10_000))

    class Holder(C.Config):
        items: list[int] = []

    cfg = Holder.draft()
    cfg.items = big
    (ev,) = C.explain(cfg, "items").events
    assert ev.value is not None and len(ev.value) <= 80  # repr, truncated, never the object
