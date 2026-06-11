# Quickstart

The whole user-facing vocabulary is three verbs and one value: `Cls.draft()`,
`C.finalize(draft)`, `C.thaw(final)`, and `C.interp(lambda c: ...)`.

## Define configs

```python
# myproj/configs.py
import nshconfig as C


class LNConfig(C.Config):
    dim: int = 32                  # plain default
    eps: float = 1e-5


class EncoderConfig(C.Config):
    ln: LNConfig                   # leave nested config fields bare


class HeadConfig(C.Config):
    # class-level interpolation: a value sitting in the default slot
    dim: int = C.interp(lambda c: c.nearest(ModelConfig).dim)


class ModelConfig(C.Config):
    dim: int = 768
    encoder: EncoderConfig
    head: HeadConfig


class TrainConfig(C.Config):
    batch: int = 8
    model: ModelConfig


def large(cfg: TrainConfig) -> None:
    """A Hydra config group is just a function that mutates a draft."""
    cfg.model.dim = 1024
```

`C.Config` is a pydantic `BaseModel` with `extra="forbid"`, `frozen=True` (finals are
immutable and hashable), and `validate_default=True`.

## Compose, finalize, run

```python
import nshconfig as C
from myproj.configs import TrainConfig, ModelConfig, large

cfg = TrainConfig.draft()          # a REAL TrainConfig instance, mutable, unvalidated
large(cfg)                         # helpers mutate drafts
cfg.model.encoder.ln.dim = C.interp(lambda c: c.nearest(ModelConfig).dim)  # this tree only
cfg.model.decoder = ...            # nested configs auto-create on access; no ceremony

final = C.finalize(cfg)            # resolve interpolation -> validate ONCE -> frozen
final.model_dump_json(indent=2)    # the run record: concrete values only
```

Explicit always beats interpolation: provide a value (constructor, dict, or draft write) and
the lambda never runs. `del cfg.model.encoder.ln.dim` re-arms whatever sits below (a class
rule, a static default, or a required-missing error at finalize).

## Sweeps

`finalize` is non-destructive and idempotent; the draft stays live:

```python
for lr in (1e-4, 3e-4, 1e-3):
    cfg.optim.lr = lr
    submit(train, C.finalize(cfg))
```

To tweak an existing final: `t = C.thaw(final)` gives a fresh draft seeded only from
explicitly-set values, so interpolated values re-derive after your tweak.

## Why did this run use that value?

```python
with C.source("sweep:lr"):
    cfg.optim.lr = 1e-4

print(C.explain(C.finalize(cfg), "optim.lr"))
# optim.lr = 0.0001
#   set to 0.0001 at sweep.py:12 in <module>  [sweep:lr]   | cfg.optim.lr = 1e-4
#   set to 0.0003 at configs/base.py:7 in base_config      | cfg.optim.lr = 3e-4
#   class default: 0.001 (OptimConfig)
```

See the [provenance guide](guides/provenance.md).
