# nshconfig v3

Typed Python configuration with editable drafts, live interpolation, and independent read-only snapshots. Config files, presets, and execution are ordinary Python modules and functions.

```bash
pip install 'nshconfig==3.0.0a0'
```

```python
import nshconfig as C

class Pairformer(C.Config):
    c_z: int = C.interp(lambda c: c.root(Run).c_z)

class Run(C.Config):
    project: str
    run_name: str
    c_z: int = 128
    pairformer: Pairformer = Pairformer()

config = Run.draft()
config.project = "af3"
config.run_name = "wide"
config.c_z = 256
assert config.pairformer.c_z == 256

ready = config.finalize()
config.c_z = 384
assert ready.pairformer.c_z == 256
assert config.pairformer.c_z == 384
```

For short configs, `Run(project="af3", run_name="wide", c_z=256)` also creates an editable draft. Constructors have checked keyword signatures; `.draft()` permits required fields to be populated later. Both use `.finalize()` before execution.

## Composition is Python

```python
def wide(config: Run) -> None:
    config.c_z = 256

def experiment() -> Run:
    config = Run(project="af3", run_name="wide")
    wide(config)
    return config

# In your ordinary Python entry point:
# train(experiment().finalize())
```

The runnable [AF3 example](examples/af3.py) demonstrates shared model dimensions, presets, nested configs, and training-set/weight validation in a reduced configuration.

## Validation and ownership

Writes defer validation. Reads validate requested values and their dependencies; declaration order does not matter. Use Pydantic `Annotated` metadata for field-local normalization and constraints, and `@C.check` for final cross-field checks:

```python
from typing import Annotated

class Data(C.Config):
    batch_size: Annotated[int, C.Field(gt=0)] = 1
    train_sets: list[str] = ["pdb"]
    weights: list[float] = [1.0]

    @C.check
    def aligned(self) -> None:
        if len(self.train_sets) != len(self.weights):
            raise ValueError("train_sets and weights must align")
```

Explicit child assignment preserves identity and requires a single owner. Use `.copy()` for reuse. Declared defaults are copied automatically per parent. Plain incoming lists/dicts become managed containers; aliases read from a config stay live. Final snapshots and computed subtrees reject nested mutation at runtime.

The same schema type describes drafts and finals. ty, Pyright, and mypy check names and value types; completeness and read-only state are runtime guarantees. Managed list/dict values implement sequence/mapping interfaces and are not built-in list/dict instances. Arbitrary mutable opaque leaves are unsupported.

Python 3.10-3.14 and Pydantic 2.13 through the latest 2.x are supported. Install `nshconfig[transport]` for trusted cloudpickle transport of notebook-defined schemas. v3 is an intentional breaking redesign of v2.

See [DESIGN.md](DESIGN.md) for the exact contract, [the documentation](https://nima.sh/nshconfig/) for guides, and [SKILL.md](SKILL.md) for agent usage.
