# Quickstart

## Define Pydantic schemas

Import nshconfig once and subclass `C.Config`. Pydantic authoring APIs are
available from the same namespace:

```python
import nshconfig as C


class Optimizer(C.Config):
    learning_rate: float = C.Field(default=3e-4, gt=0)
    """Optimizer step size."""


class Norm(C.Config):
    dim: int = C.interp(lambda context: context.parent(Model).dim)


class Model(C.Config):
    dim: int = 768
    norm: Norm = Norm()


class Run(C.Config):
    seed: int
    optimizer: Optimizer = Optimizer()
    model: Model = Model()
```

`Norm()` first attempts ordinary validation. Its missing parent context produces
an inert unbound template, whose raw constructor recipe binds inside `Model`'s
normal Pydantic field pipeline. No factory syntax is needed. `Optimizer()` and
`Model()` are validated finals with replayable default recipes. All three become
fresh child drafts when their parent default is projected into a draft.

Validation is strict by default, and attribute docstrings become field
descriptions when class source is available. Factories returning drafts or
templates, including `C.Field(default_factory=Norm.config_draft)`, are rejected;
use direct `Norm()` defaults.

## Compose a draft

```python
work = Run.config_draft()
work.seed = 7
work.optimizer.learning_rate = 1e-4
work.model.dim = 1024

assert C.is_draft(work)
assert C.is_draft(work.model)
```

Draft assignment does not validate. Required Config fields lazily create child
drafts. Reading another missing required field raises `C.UnsetError`.

## Finalize once

```python
run = work.config_finalize()

assert run.seed == 7
assert run.model.dim == 1024
assert run.model.norm.dim == 1024
assert not C.is_draft(run)
```

Finalization recursively collects the declared Config graph, evaluates
interpolation, and runs normal Pydantic validation. It is non-destructive:

```python
work.model.dim = 2048
larger = work.config_finalize()

assert run.model.norm.dim == 1024
assert larger.model.norm.dim == 2048
```

## Use ordinary Pydantic construction

Users who do not need draft composition can construct the same schema normally:

```python
direct = Run(
    seed=7,
    optimizer=Optimizer(learning_rate=1e-4),
    model=Model(dim=1024),
)

assert direct == run
```

Pydantic owns aliases, validators, serialization, and JSON Schema:

```python
payload = run.model_dump()
schema = Run.model_json_schema()
checked = Run.model_validate(payload)
```

Draft and template serialization is rejected, including through `TypeAdapter`
and nested Pydantic serializers.

## Build project presets

Reusable presets and root experiment files are both ordinary mutators:

```python
def large_model(cfg: Model, *, dim: int = 2048) -> Model:
    cfg.dim = dim
    return cfg


def __config__(cfg: Run) -> Run:
    large_model(cfg.model)
    cfg.seed = 11
    return cfg
```

See [project composition](guides/project-layout.md) for the endorsed directory
layout and application-side identity check.
