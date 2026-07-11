# Quickstart

This example defines a schema, composes a draft, derives a value, validates the
result, explains it, and saves a run record.

## Define the schema

Use Pydantic directly for fields, constraints, aliases, and validators.

```python
# configs.py
from pydantic import Field, field_validator

import nshconfig as C


class Optimizer(C.Config):
    learning_rate: float = Field(default=3e-4, gt=0)
    weight_decay: float = Field(default=0.0, ge=0)


class LayerNorm(C.Config):
    dim: int = C.interp(lambda context: context.parent(Model).dim)
    eps: float = Field(default=1e-5, gt=0)


class Model(C.Config):
    dim: int = 768
    norm: LayerNorm


class Run(C.Config):
    optimizer: Optimizer
    model: Model
    epochs: int

    @field_validator("epochs")
    @classmethod
    def positive_epochs(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("epochs must be positive")
        return value
```

`LayerNorm.dim` is an interpolation rule stored as its class default. It runs only
when no explicit value is supplied for that field.

## Compose and finalize

A draft is a real `Run` instance, but it is intentionally mutable, incomplete,
and unvalidated.

```python
import nshconfig as C

from configs import Run


def large_model(work: Run) -> None:
    work.model.dim = 1024


work = C.draft(Run)
large_model(work)

with C.source("sweep:low-lr"):
    work.optimizer.learning_rate = 1e-4

work.epochs = 100
run = C.finalize(work)

assert run.model.dim == 1024
assert run.model.norm.dim == 1024
```

Required fields with one concrete `Config` annotation auto-create as child drafts,
which is why `work.model.dim` and `work.optimizer.learning_rate` can be assigned on
a fresh root. Required scalars still need an assignment.

Finalization is non-destructive. The draft remains the composition recipe for a
sweep:

```python
work.model.dim = 512
small = C.finalize(work)

work.model.dim = 2048
large = C.finalize(work)
```

## Explain and record

```python
import json
from pathlib import Path

import nshconfig as C


print(C.explain(run, "model.norm.dim"))

run_record = C.record(run)
record_path = Path("run-config.json")
record_path.write_text(run_record.to_json(indent=2), encoding="utf-8")

wire_value = json.loads(record_path.read_text(encoding="utf-8"))
restored = C.load_record(Run, wire_value)
assert restored == run
assert C.fingerprint(restored) == C.fingerprint(run)
```

`load_record()` validates concrete stored values and restores provenance. It does
not run interpolation or recover the original draft. See [run records](guides/records.md)
and the [semantic contract](contract.md) for the precise distinction.
