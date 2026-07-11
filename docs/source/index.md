# nshconfig

`nshconfig` is a small lifecycle layer over Pydantic for Python-first ML
configuration. Pydantic defines and validates the schema. `nshconfig` adds mutable
incomplete drafts, Python interpolation, provenance, and verified run records.

```python
import nshconfig as C


class Optimizer(C.Config):
    learning_rate: float = 3e-4


class Run(C.Config):
    optimizer: Optimizer
    epochs: int


work = C.draft(Run)
work.optimizer.learning_rate = 1e-4
work.epochs = 100
run = C.finalize(work)
```

The four states are deliberately distinct:

1. A `Config` class declares a Pydantic schema.
2. A draft is mutable Python composition state and may be incomplete.
3. A final is the validated result of one explicit interpolation boundary.
4. A run record is inert JSON data describing the concrete final.

There is no configuration language, registry, loader, or code-generation layer.
The [semantic contract](contract.md) is the authority for lifecycle, ordering,
value-graph, and reproducibility behavior. The [API reference](api.md) lists the
complete exported surface and exact signatures.

```{toctree}
:maxdepth: 2

installation
quickstart
contract
api
changelog
guides/drafts
guides/interpolation
guides/provenance
guides/records
guides/transport
guides/failures
guides/typing
```
