from __future__ import annotations

__codegen__ = True

from my_module.configs.model import ModelConfig as ModelConfig

from .ModelConfig_typed_dict import CreateModelConfig as CreateModelConfig
from .ModelConfig_typed_dict import ModelConfigTypedDict as ModelConfigTypedDict

__all__ = [
    "CreateModelConfig",
    "ModelConfig",
    "ModelConfigTypedDict",
]
