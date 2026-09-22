"""AF3 configuration example with shared model dimensions and dataset weights.

This reduced example demonstrates composition without importing a training stack.
"""

from __future__ import annotations

from typing import Annotated

import nshconfig as C


class Pairformer(C.Config):
    c_z: int = C.interp(lambda c: c.root(AF3Config).c_z)
    n_blocks: int = C.interp(lambda c: c.root(AF3Config).n_blocks)


class Diffusion(C.Config):
    c_z: int = C.interp(lambda c: c.root(AF3Config).c_z)


class Model(C.Config):
    pairformer: Pairformer = Pairformer()
    diffusion: Diffusion = Diffusion()


class Data(C.Config):
    train_sets: list[str] = ["weighted_pdb"]
    train_sample_weights: list[float] = [1.0]
    test_sets: list[str] = []

    @C.check
    def aligned_training_weights(self) -> None:
        if len(self.train_sets) != len(self.train_sample_weights):
            raise ValueError("train_sets and train_sample_weights must align")


class AF3Config(C.Config):
    project: str
    run_name: str
    c_z: Annotated[int, C.Field(gt=0)] = 128
    n_blocks: Annotated[int, C.Field(gt=0)] = 48
    model: Model = Model()
    data: Data = Data()


def wide(config: AF3Config) -> None:
    """One root edit propagates to both model branches."""
    config.c_z = 256


def experiment() -> AF3Config:
    config = AF3Config.draft()
    config.project = "af3"
    config.run_name = "wide"
    wide(config)
    config.data.train_sets.append("distillation")
    config.data.train_sample_weights.append(0.5)
    return config


def run(config: AF3Config) -> None:
    """Replace this body with the project's ordinary training function."""
    assert config.model.pairformer.c_z == config.model.diffusion.c_z == 256
    assert config.data.train_sample_weights == [1.0, 0.5]
    assert not C.is_draft(config)


if __name__ == "__main__":
    run(experiment().finalize())
