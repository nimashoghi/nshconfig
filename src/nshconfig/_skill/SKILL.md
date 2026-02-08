---
name: using-nshconfig
description: Build typed Python configs with nshconfig (Pydantic v2). Use when creating Config classes, setting up type registries for discriminated unions, using draft/finalize workflows, MISSING sentinel fields, serializing to JSON/YAML/TOML, or singleton config patterns.
---

# nshconfig

Configuration library built on Pydantic v2. Import as `import nshconfig as C`.

## Config Class

```python
from __future__ import annotations

import nshconfig as C

class MyConfig(C.Config):
    name: str
    lr: float = 1e-3
    epochs: int = 10
```

Extends Pydantic `BaseModel` with stricter defaults: `validate_assignment=True`, `strict=True`, `extra="forbid"`, `arbitrary_types_allowed=True`.

## Draft / Finalize

Create configs incrementally without validation, then validate:

```python
config = MyConfig.draft()
config.name = "experiment_1"
config.lr = 0.01
final = config.finalize()  # runs full Pydantic validation
```

Hooks: `__draft_pre_init__()` (before finalize), `__post_init__()` (after validation), `__after_post_init__()`.

## MISSING Fields

Fields that are required but provided later:

```python
class TrainConfig(C.Config):
    lr: float
    batch_size: C.AllowMissing[int] = C.MISSING

config = TrainConfig(lr=0.01)  # OK: batch_size is MISSING
config.batch_size = 32
config.model_validate_no_missing()  # raises if any field still MISSING
```

## Registry (Dynamic Discriminated Unions)

Plugin architecture where subtypes register at runtime:

```python
from abc import ABC, abstractmethod
from typing import Annotated, Literal

class OptimizerConfig(C.Config, ABC):
    type: str

    @abstractmethod
    def build(self): ...

registry = C.Registry(OptimizerConfig, "type")

@registry.register
class AdamConfig(OptimizerConfig):
    type: Literal["adam"] = "adam"
    lr: float = 1e-3

    def build(self):
        return Adam(lr=self.lr)

@registry.register
class SGDConfig(OptimizerConfig):
    type: Literal["sgd"] = "sgd"
    lr: float = 0.01
    momentum: float = 0.9

    def build(self):
        return SGD(lr=self.lr, momentum=self.momentum)

# Parent config — auto-rebuilds schema when new types register
class TrainConfig(C.Config):
    optimizer: Annotated[OptimizerConfig, registry]

config = TrainConfig(optimizer=AdamConfig())
```

**Key points:**
- Discriminator fields must be `Literal` with exactly one value
- `auto_rebuild=True` (default): parent models using `Annotated[Base, registry]` auto-update schemas
- For manual control: `@registry.rebuild_on_registers` + `RegistryConfig(auto_rebuild=False)`
- Use `registry.construct(data_dict)` to validate raw data against registered types

## Serialization

```python
# JSON
json_str = config.to_json_str()
config = MyConfig.from_json_str(json_str)
config.to_json_file("config.json")
config = MyConfig.from_json_file("config.json")

# YAML (requires nshconfig[yaml])
yaml_str = config.to_yaml_str()
config = MyConfig.from_yaml_str(yaml_str)
config.to_yaml_file("config.yaml")
config = MyConfig.from_yaml("config.yaml")

# TOML (requires nshconfig[toml])
toml_str = config.to_toml_str()
config = MyConfig.from_toml_str(toml_str)

# Python file (must export __config__ or __create_config__)
config = MyConfig.from_python_file("config.py")

# Dict
config = MyConfig.from_dict({"name": "exp", "lr": 0.01})
d = config.to_dict()
```

## Singleton

```python
from typing import ClassVar
from typing_extensions import Self

# As descriptor
class AppConfig(C.Config):
    singleton: ClassVar[C.Singleton[Self]] = C.Singleton()
    debug: bool = False

AppConfig.singleton.initialize(debug=True)
cfg = AppConfig.singleton.instance()

# As global
class DBConfig(C.Config):
    host: str
    port: int = 5432

db_singleton = C.singleton(DBConfig)
db_singleton.initialize(host="localhost")
cfg = db_singleton.instance()
```

Methods: `initialize(instance)` or `initialize(**kwargs)`, `instance()`, `try_instance()`, `reset()`.

## ConfigDict Extensions

| Option | Default | Description |
|--------|---------|-------------|
| `repr_diff_only` | `False` | Only show non-default fields in repr |
| `no_validate_assignment_for_draft` | `True` | Skip validation on draft assignment |
| `set_default_hash` | `True` | Add default `__hash__` |
| `disable_typed_dict_generation` | `False` | Skip export TypedDict generation |

## Other Utilities

- `C.Adapter` — Wrap/transform any type with nshconfig capabilities
- `C.Invalid` — Marker type for invalid values
- `C.deduplicate(items)` / `C.deduplicate_configs(configs)` — Remove duplicates
- `model_deep_validate()` — Recursive re-validation of nested configs
- `Config` implements `MutableMapping[str, Any]` (Lightning `hparams` compatible)

## Code Generation CLI

```bash
nshconfig-export mypackage.configs -o output/ --generate-typed-dicts --generate-json-schema --recursive
```

## Rules

- **Every file MUST start with `from __future__ import annotations`**
- Use `typing_extensions` for type backports (Python 3.9+ support)
- Check `PYDANTIC_VERSION` from `nshconfig._src.utils` for version-specific Pydantic code
