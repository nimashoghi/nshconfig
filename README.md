# nshconfig

Typed, provenance-aware configuration for ML runs, powered by [Pydantic](https://github.com/pydantic/pydantic).

**[Documentation](https://nima.sh/nshconfig/)**

One verb family and one value:

- `Cls.config_draft()` gives a mutable draft: plain Python assignment, nested configs auto-create,
  validation deferred.
- `C.interp(lambda c: ...)` is a **value** that resolves against the config tree at validation.
  It is legal anywhere a value sits: assigned on a draft, inside a `model_validate` dict, or as
  a class default. This is Hydra-style interpolation, in Python, mostly type-checked.
- `draft.config_finalize()` resolves interpolation, validates once, and returns a frozen, hashable,
  fully-concrete config.

Plus provenance: `final.config_explain("optim.lr")` answers *"why did this run use that value?"* down
to file, line, function, source text, and the interpolation's "because" chain.

## Install

```bash
pip install nshconfig            # pydantic>=2.13, Python>=3.10
pip install nshconfig[treescope] # optional rich notebook rendering
```

## Quickstart

```python
import nshconfig as C

class LNConfig(C.Config):
    dim: int = 32                  # plain default; leaf classes need no interpolation
    eps: float = 1e-5

class EncoderConfig(C.Config):
    ln: LNConfig

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

# compose in a notebook; helpers are plain functions (your "config groups")
def large(cfg: TrainConfig) -> None:
    cfg.model.dim = 1024

cfg = TrainConfig.config_draft()
large(cfg)
# instance-level interpolation: wire THIS tree only, at composition time
cfg.model.encoder.ln.dim = C.interp(lambda c: c.nearest(ModelConfig).dim)

final = cfg.config_finalize()            # the one validation boundary
assert final.model.encoder.ln.dim == 1024   # followed the knob
assert final.model.head.dim == 1024         # class-default rule, same machinery
final.model_dump_json()                     # concrete values only: the run record

# why did this run use that value?
print(final.config_explain("model.head.dim"))
# model.head.dim = 1024
#   interpolated to 1024 by <lambda> @ configs.py:11 (class default)
#       because model.dim = 1024
#   class-default rule: interp(<<lambda> @ configs.py:11>)   (active)
```

Explicit always beats interpolation (presence in the input slot beats the default slot; last
write wins; `del` re-arms). Nothing pending can reach a final, a dump, an f-string, or an `if`
without a loud error naming the dotted path and the source line. Drafts cloudpickle to clusters
mid-composition and finalize on the far side, provenance included.

## The Ctx API (what the lambda sees)

| Accessor | Hydra equivalent | Sees |
|---|---|---|
| `c.data` | same level | own fields, earlier markers already resolved |
| `c.parent` | `${..x}` | one level up, resolved |
| `c.root` | `${a.b}` | the validation root, incl. sibling subtrees |
| `c.nearest(Cls)` | (none: better) | nearest enclosing `Cls`; survives restructuring |

## License

MIT
