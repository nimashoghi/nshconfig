from __future__ import annotations

__codegen__ = True

from my_module.configs.model import ModelConfig as ModelConfig

from . import model as model

__all__ = [
    "ModelConfig",
    "model",
]
