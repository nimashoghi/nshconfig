# Drafts and finalize

A draft is a **real instance** of your config class (`isinstance(draft, Cls)` is true),
created by `Cls.config_draft(**seeds)` without validation. It behaves like a plain mutable object:

```python
cfg = TrainConfig.config_draft()
cfg.model.encoder.ln.eps = 1e-6   # nested configs auto-create on first access
cfg.batch = 16
del cfg.batch                     # re-arms the default (or interpolation rule)
```

Properties worth knowing:

- **Auto-vivification**: accessing an untouched `Config`-typed field creates a child draft, so
  helpers can reach arbitrarily deep into a fresh draft with zero ceremony.
- **Seeds are explicit**: `Cls.config_draft(dim=1024)` counts as user-set provenance, like a write.
- **Reads are honest**: a set field reads back its value; an unset required field raises
  `UnsetError`; a field whose value would come from interpolation raises `UnsetError` telling
  you to finalize first. Reads never return placeholder objects.
- **Typos are immediate**: `cfg.model.dmi = 3` raises at the assignment line with a
  did-you-mean, and is also a *static* basedpyright error.
- **Drafts do not serialize**: `model_dump()` raises `DraftError` (a partial dump is a silently
  wrong artifact). Drafts *do* pickle (see [transport](transport.md)): work-in-progress travels.
- **The repr shows pending state**: user writes, `[pending: instance interp(<...>)]`,
  `[pending: class default interp(<...>)]`, `<untouched EncoderConfig>`, `[UNSET]`; untouched
  static defaults are omitted as noise.

## finalize

`draft.config_finalize()` is the one validation boundary, and does exactly two things: resolve
interpolation (inside pydantic's validation pass) and validate the whole tree once. The result
is frozen, hashable, equal-by-value, and contains concrete values only.

- **Idempotent**: `final.config_finalize() is final` (and `C.finalize(final) is final`; the
  module verbs are functional aliases of the methods), so boundary code can normalize
  defensively.
- **Non-destructive**: the draft stays live; tweak-and-refinalize is the sweep loop.
- **Per-leaf errors**: untouched required subtrees are expanded, so a missing field reports
  `model.encoder.ln.dim: Field required`, not `model: Field required`, annotated with
  provenance ("never assigned on this draft" semantics via `explain`).
- **`exclude_unset` means explicit**: the final's `model_fields_set` is restored from draft
  provenance, so `final.model_dump(exclude_unset=True)` is exactly "what the user chose".

## thaw

`final.config_thaw()` returns a fresh draft seeded *only* from explicitly-set values. Interpolated
and defaulted values are not seeded, so they re-derive against your tweaks:

```python
t = yesterdays_run.config_thaw()
t.model.dim = 2048           # bump the knob...
new = t.config_finalize()    # ...every interpolated value follows; overrides stick
```

Invariant: `C.finalize(C.thaw(x)) == x` when nothing changed.

## Direct construction

`Cls(...)` is untouched pydantic, validated eagerly. Interpolations that only read their own
level resolve fine; ones that need ancestors fail loudly (a bare instance is its own
validation root). Passing the value explicitly always works. The draft path is the supported
way to get tree-aware derived defaults.
