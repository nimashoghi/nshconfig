# nshconfig

Typed, provenance-aware configuration for ML runs, powered by Pydantic.

nshconfig v2 does three things, and tries to do them extremely well:

1. **Draft building**: compose configs with plain Python assignment in notebooks and helper
   functions, with validation deferred to one explicit boundary.
2. **Interpolation**: `interp(lambda c: ...)` is a *value* that resolves against the config
   tree at validation time. Hydra's `${..dim}` and `${model.dim}`, in Python, mostly
   type-checked, and usable both in class bodies and at composition time.
3. **Frozen, explainable finals**: the output of `finalize()` is an immutable, hashable,
   fully-concrete pydantic model that dumps clean run records and can answer
   *"why did this run use that value?"* down to file and line.

```python
import nshconfig as C

class LNConfig(C.Config):
    dim: int = C.interp(lambda c: c.nearest(ModelConfig).dim)

class ModelConfig(C.Config):
    dim: int = 768
    ln: LNConfig

cfg = ModelConfig.config_draft()
cfg.dim = 1024
final = cfg.config_finalize()
assert final.ln.dim == 1024
print(final.config_explain("ln.dim"))
```

```{toctree}
:maxdepth: 2

installation
quickstart
guides/drafts
guides/interpolation
guides/provenance
guides/transport
guides/failures
guides/typing
guides/migration
```
