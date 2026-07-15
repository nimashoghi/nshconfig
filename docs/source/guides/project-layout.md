# Project composition

`nshconfig` endorses one small convention without providing a loader.

## Reusable mutators

Place reusable configuration builders under your package:

```text
src/
  project/
    configs/
      models.py
      optimizers.py
```

Each builder mutates and returns the same typed draft:

```python
def resnet50(cfg: ModelConfig, *, d_model: int = 256) -> ModelConfig:
    cfg.architecture = "resnet50"
    cfg.d_model = d_model
    return cfg
```

This is an ordinary Python function. It composes naturally with other builders and
requires no decorator, registry, or wrapper type.

## Root experiment files

Keep finalized experiment choices in a root `configs/` directory:

```text
configs/
  baseline.py
  large_model.py
```

Each file exports the same mutator contract through `__config__`:

```python
from project.configs.models import resnet50


def __config__(cfg: TrainConfig) -> TrainConfig:
    resnet50(cfg.model, d_model=512)
    cfg.optimizer.learning_rate = 1e-4
    cfg.seed = 7
    return cfg
```

There is intentionally no semantic difference between a reusable helper and a
root experiment configuration. Both are `(draft, ...) -> draft` functions.

## Application boundary

The application decides how a file is imported and which root type it expects. At
the boundary it should:

```python
cfg = TrainConfig.config_draft()
returned = module.__config__(cfg)
if returned is not cfg:
    raise TypeError("__config__ must return the draft it received")
final = cfg.config_finalize()
```

Create one root draft and finalize exactly once after all mutators have run. The
identity check catches helpers that accidentally construct or return a different
object.

Keeping file loading in the application avoids global registries, import-time
side effects, hidden schema selection, and a second configuration language. A
project may choose any import mechanism or command-line interface without changing
the nshconfig lifecycle.
