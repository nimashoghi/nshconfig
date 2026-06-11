---
name: using-nshconfig
description: Build typed, provenance-aware Python configs with nshconfig v2 (Pydantic). Use when creating Config classes, composing drafts, wiring values with interp() interpolation (Hydra-style, in Python), finalizing to frozen configs, or tracing why a value was set with explain().
---

# nshconfig v2

Configuration library on Pydantic (>=2.13, Python >=3.10). Import as `import nshconfig as C`.
Three verbs and one value: `Cls.draft()`, `C.finalize(draft)`, and `C.interp(lambda c: ...)`.

## Config classes

```python
import nshconfig as C

class OptimConfig(C.Config):
    lr: float = 1e-3
    weight_decay: float = 0.0

class TrainConfig(C.Config):
    optim: OptimConfig
    steps: int            # required
```

`C.Config` is a pydantic BaseModel with `extra="forbid"`, `frozen=True` (finals are immutable
and hashable), `validate_default=True`. Leave nested config fields bare (`optim: OptimConfig`,
no default): drafts auto-create them, and finalize fills untouched required subtrees.

## Drafts and finalize

```python
cfg = TrainConfig.draft()      # a REAL TrainConfig instance, mutable, unvalidated
cfg.optim.lr = 1e-4            # nested configs auto-create on access; no ceremony
cfg.steps = 10_000
final = C.finalize(cfg)        # resolve interpolation -> validate ONCE -> frozen
```

- Helpers are plain functions mutating drafts (`def large(cfg): cfg.model.dim = 1024`); they
  replace Hydra config groups.
- `del cfg.optim.lr` re-arms the default (or interpolation rule). Last write wins.
- finalize is idempotent and non-destructive: tweak the draft and finalize again (sweep loop).
- `C.thaw(final)` gives a fresh draft seeded only from explicitly-set values, so interpolated
  values re-derive after a tweak.
- Drafts refuse `model_dump()` (loud `DraftError`); finals dump concrete values only.
- Reading an unset/pending draft field raises `UnsetError`; typos raise with a did-you-mean.

## Interpolation: `C.interp` is a value

```python
class LNConfig(C.Config):
    dim: int = C.interp(lambda c: c.nearest(ModelConfig).dim)   # class default slot
```

The SAME kind of value works at composition time (the Hydra move), per tree:

```python
cfg.model.encoder.ln.dim = C.interp(lambda c: c.nearest(ModelConfig).dim)  # draft slot
TrainConfig.model_validate({"optim": {"lr": C.interp(lambda c: c.root.steps / 100)}})
```

The lambda receives a `Ctx`:

- `c.data` -- own level (earlier-declared fields already resolved)
- `c.parent` -- one level up (Hydra `${..x}`), always resolved
- `c.root` -- the validation root (Hydra `${a.b}`), raw input + class defaults, incl. siblings
- `c.nearest(Cls)` -- nearest enclosing `Cls` instance; ancestors only; survives refactors

Rules: explicit values always beat interpolation (the lambda never runs if the field was
provided). The lambda body is arbitrary pure Python (conditionals, arithmetic, f-strings over
resolved values). Resolved values still pass field constraints (`Field(gt=0)` etc.).
`interp()` composes with `Field`: `a: int = C.Field(default=C.interp(...), gt=0)` -- use
pydantic's Field directly. One pass: a marker reading a still-pending SIBLING value fails
loudly (point both at the shared source instead). Markers refuse `bool()`/f-strings while
pending; `==` on markers is identity.

## Provenance: why did this run use that value?

```python
with C.source("sweep:lr"):       # optional semantic label
    cfg.optim.lr = 1e-4

print(C.explain(final, "optim.lr"))
# optim.lr = 0.0001
#   set to 0.0001 at sweep.py:12 in <module>  [sweep:lr]   | cfg.optim.lr = 1e-4
#   set to 0.0003 at configs/base.py:7 in base_config      | cfg.optim.lr = 3e-4
#   class default: 0.001 (OptimConfig)
```

Every draft write records file:line, function, and source text automatically (helpers become
provenance units). Interp events record the marker's site plus what it read ("because
model.dim = 1024"). `C.provenance(cfg)` returns the full path -> events table. Works on drafts
and finals; survives pickling.

## Transport

cloudpickle is the notebook-to-cluster channel: pending drafts (even with notebook-defined
classes) round-trip and finalize on the far side. Plain pickle works for finals and for
markers using named module-level functions (lambdas need cloudpickle). JSON is the run record:
`final.model_dump_json()`; reload with `Cls.model_validate_json(...)`.

## Failure model (all loud, all located)

Orphan (`no enclosing ModelConfig (ancestors here: ...)`), cycles (both ends named), markers
smuggled outside field slots (root sweep names the path), pending reads, draft dumps, and
resolver exceptions are all `ValidationError`/`UnsetError`/`DraftError` carrying the dotted
path, owning `Cls.field`, and the lambda's `file:line` site.

## Rules for generated code

- Do NOT use `from __future__ import annotations` (the library does not): annotations are
  eager, matching notebook semantics and pydantic's resolution; quote forward references
  explicitly (`def helper(cfg: "TrainConfig")`).
- Type checker: basedpyright. The lambda's RETURN type is checked at both slots; the lambda
  body is dynamically typed (`c` navigations return Any), like pydantic's `default_factory`.
- Direct construction does not see ancestors: `LNConfig()` with an ancestor-needing rule fails
  loudly; use the draft path or pass the value explicitly.
