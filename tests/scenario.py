"""The canonical scenario tree, importable by test modules."""

import nshconfig as C


class LNConfig(C.Config):
    dim: int = 32
    eps: float = 1e-5


class EncoderConfig(C.Config):
    ln: LNConfig


class DecoderConfig(C.Config):
    ln: LNConfig


class HeadConfig(C.Config):
    dim: int = C.interp(lambda c: c.nearest(ModelConfig).dim)


class ModelConfig(C.Config):
    dim: int = 768
    encoder: EncoderConfig
    decoder: DecoderConfig
    head: HeadConfig


class TrainConfig(C.Config):
    batch: int = 8
    model: ModelConfig
