# nshconfig

`nshconfig` is a small lifecycle layer over Pydantic for typed, Python-first ML
configuration. Pydantic defines and validates the schema, and nshconfig
re-exports its authoring API for a single import. `nshconfig` adds explicit
mutable drafts and declaration-ordered Python interpolation.

```python
import nshconfig as C


class Optimizer(C.Config):
    learning_rate: float = 3e-4


class Run(C.Config):
    optimizer: Optimizer
    epochs: int


work = Run.config_draft()
work.optimizer.learning_rate = 1e-4
work.epochs = 100
run = work.config_finalize()
```

Calling `Run(...)` directly remains ordinary validated Pydantic construction.
Drafts are explicit, mutable, and may be incomplete. Finalization returns a fresh,
field-frozen Pydantic model without consuming the draft.

There is no configuration language, registry, loader, provenance subsystem,
record format, or code-generation layer. The [semantic design](contract.md) is the
authority for lifecycle, ordering, and structural graph behavior.

```{toctree}
:maxdepth: 2

installation
quickstart
contract
guides/drafts
guides/interpolation
guides/project-layout
guides/transport
guides/failures
guides/typing
```
