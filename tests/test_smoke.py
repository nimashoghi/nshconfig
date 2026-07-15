"""The small public API and one complete notebook-style workflow."""

import pytest
from pydantic import ValidationError

import nshconfig as C
from tests.scenario import ModelConfig, TrainConfig


def test_public_api_is_deliberately_small():
    assert C.__version__ == "2.2.0a0"
    assert C.__all__ == [
        "Config",
        "Context",
        "DraftError",
        "UnsetError",
        "__version__",
        "interp",
        "is_draft",
    ]
    assert not hasattr(C, "Field")
    assert not hasattr(C, "BaseModel")
    assert not hasattr(C, "thaw")
    assert not hasattr(C, "draft")
    assert not hasattr(C, "finalize")


def test_scenario_end_to_end():
    work = TrainConfig.config_draft()
    work.model.dim = 1024
    work.model.encoder.ln.dim = C.interp(
        lambda context: context.nearest(ModelConfig).dim
    )
    work.model.decoder.ln.dim = 64

    final = work.config_finalize()

    assert final.model.encoder.ln.dim == 1024
    assert final.model.decoder.ln.dim == 64
    assert final.model.head.dim == 1024
    assert final.model.encoder.ln.eps == 1e-5
    assert final.model_dump()["model"]["encoder"]["ln"] == {
        "dim": 1024,
        "eps": 1e-5,
    }


def test_direct_construction_produces_a_final():
    final = TrainConfig.model_validate(
        {
            "model": {
                "encoder": {"ln": {}},
                "decoder": {"ln": {}},
                "head": {},
            }
        }
    )

    assert not C.is_draft(final)
    assert final.model.head.dim == 768
    with pytest.raises(ValidationError, match="frozen"):
        final.batch = 4


def test_one_draft_supports_a_non_destructive_sweep():
    work = TrainConfig.config_draft()
    work.model.dim = 128
    first = work.config_finalize()
    work.model.dim = 256
    second = work.config_finalize()

    assert first.model.head.dim == 128
    assert second.model.head.dim == 256
    assert C.is_draft(work)
