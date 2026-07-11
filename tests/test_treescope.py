"""Treescope extra: pending state and default-dimming render in notebooks."""

import pytest

import nshconfig as C
from tests.scenario import TrainConfig

treescope = pytest.importorskip("treescope")


def test_draft_renders_pending_labels():
    cfg = C.draft(TrainConfig)
    cfg.model.dim = 1024
    _ = cfg.model.head  # vivify so the pending class-default rule is visible
    text = treescope.render_to_text(cfg)
    assert "draft" in text
    assert "pending: class default interp(" in text  # head.dim's rule
    assert "dim=1024" in text
    assert "<untouched EncoderConfig>" in text


def test_draft_renders_explicit_interpolation_from_pending_state():
    class Values(C.Config):
        source: int = 2
        copied: int = 0

    cfg = C.draft(Values)
    cfg.copied = C.interp(lambda context: context.current().source)

    text = treescope.render_to_text(cfg)
    assert "copied=" in text
    assert "pending: instance interp(" in text


def test_final_renders_with_concrete_values():
    final = C.finalize(C.draft(TrainConfig))
    text = treescope.render_to_text(final)
    assert "pending" not in text
    assert "768" in text
