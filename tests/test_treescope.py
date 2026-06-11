"""Treescope extra: pending state and default-dimming render in notebooks."""

from __future__ import annotations

import pytest

import nshconfig as C
from tests.scenario import TrainConfig

treescope = pytest.importorskip("treescope")


def test_draft_renders_pending_labels():
    cfg = TrainConfig.draft()
    cfg.model.dim = 1024
    _ = cfg.model.head  # vivify so the pending class-default rule is visible
    text = treescope.render_to_text(cfg)
    assert "draft" in text
    assert "pending: class default interp(" in text  # head.dim's rule
    assert "dim=1024" in text
    assert "<untouched EncoderConfig>" in text


def test_final_renders_with_concrete_values():
    final = C.finalize(TrainConfig.draft())
    text = treescope.render_to_text(final)
    assert "pending" not in text
    assert "768" in text
