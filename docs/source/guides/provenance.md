# Provenance

Every draft write is recorded with its frame: file, line, enclosing function, the assignment's
source text, and an optional semantic label. Plain assignment is the API; the interpreter's
frame is the source, and it cannot drift the way hand-written `source="..."` strings do.

```python
# configs/base.py
def base_config(cfg: TrainConfig) -> None:
    cfg.optim.lr = 3e-4

# notebook / sweep script
cfg = TrainConfig.draft()
base_config(cfg)                  # the helper IS a provenance unit, automatically
with C.source("sweep:lr"):        # optional label for a block of writes
    cfg.optim.lr = 1e-4
final = C.finalize(cfg)
```

## explain

```python
print(C.explain(final, "optim.lr"))
# optim.lr = 0.0001
#   set to 0.0001 at sweep.py:12 in <module>  [sweep:lr]   | cfg.optim.lr = 1e-4
#   set to 0.0003 at base.py:3 in base_config              | cfg.optim.lr = 3e-4
#   class default: 0.001 (OptimConfig)
```

`explain(cfg, "a.b.c")` works on drafts mid-composition and on finals, returns a structured
`Explanation` (a list of `Event` records with a pretty `__str__`), and renders newest-first.
`del` shows as a tombstone in the chain. Interpolated values record the marker's source site
*and what it read*, the "because" chain that bottoms out at human actions:

```python
print(C.explain(final, "model.head.dim"))
# model.head.dim = 1024
#   interpolated to 1024 by <lambda> @ configs.py:11 (class default)
#       because model.dim = 1024
#   class-default rule: interp(<<lambda> @ configs.py:11>)   (active)
```

The rule line reports `(active)` when the class-default interpolation governs the value and
`(shadowed)` when an explicit write does.

## The full table

`C.provenance(cfg)` returns `{dotted_path: [Event, ...]}` for the whole tree; useful for
logging the complete "who set what" story into a run record next to `config.json`.

## Properties

- Values in events are truncated reprs, never live objects (a recorded tensor will not be
  kept alive by its provenance).
- Provenance is plain data: it survives pickling, so cluster-side failures and `explain`
  calls cite your notebook lines.
- Histories do not affect identity: two value-identical finals with different provenance
  compare equal and hash equal.
- Overhead is one `sys._getframe` per draft write, roughly a microsecond.
