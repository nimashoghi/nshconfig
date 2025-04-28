from __future__ import annotations

__codegen__ = True

from my_module.configs.model import ModelConfig as ModelConfig

from . import configs as configs

__all__ = [
    "ModelConfig",
    "configs",
]
