from __future__ import annotations

import typing_extensions as typ

if typ.TYPE_CHECKING:
    from my_module.configs.model import ModelConfig


__codegen__ = True


# Schema entries
class ModelConfigTypedDict(typ.TypedDict):
    hidden_size: int

    num_layers: int


@typ.overload
def CreateModelConfig(**dict: typ.Unpack[ModelConfigTypedDict]) -> ModelConfig: ...


@typ.overload
def CreateModelConfig(data: ModelConfigTypedDict | ModelConfig, /) -> ModelConfig: ...


def CreateModelConfig(*args, **kwargs):
    from my_module.configs.model import ModelConfig

    if not args and kwargs:
        # Called with keyword arguments
        return ModelConfig.from_dict(kwargs)
    elif len(args) == 1:
        return ModelConfig.from_dict_or_instance(args[0])
    else:
        raise TypeError(
            f"CreateModelConfig accepts either a ModelConfigTypedDict, "
            f"keyword arguments, or a ModelConfig instance"
        )
