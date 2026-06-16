# The typing story, honestly

The supported checker is basedpyright. The contract is enforced by golden probe files in the
test suite: a clean probe that must stay clean, and a seeded-mistakes probe whose every error
must keep firing.

## What basedpyright checks

- **The interp lambda's return type, at both slots.** `derive`-style factories are typed
  `(Callable[[Ctx], T]) -> T`, so `dim: int = C.interp(lambda c: "oops")` and
  `cfg.ln.dim = C.interp(lambda c: "oops")` are edit-time errors.
- **Typed selector field access.** `c.root(TrainConfig).model`,
  `c.parent(ModelConfig).dim`, `c.parent(2, TrainConfig).batch`, and
  `c.self(LNConfig).dim` expose the selected config type to basedpyright, so misspelled
  fields and incompatible return types are edit-time errors.
- **Draft writes and reads.** Drafts are typed as the real class and the draft machinery is
  hidden from the checker, so `cfg.model.dmi = 3` and `cfg.dim = "1024"` are static errors.
- **Helper signatures** (`def large(cfg: TrainConfig) -> None`), `finalize`'s `(C) -> C`, and
  everything else around the lambda.

## What it cannot check (loud at runtime instead)

- **Untyped selector bodies.** `c.root()`, `c.parent()`, `c.parent(n)`, and `c.self()` return
  dynamic views. This is the same accepted trade as pydantic's data-aware
  `default_factory`. The runtime backstop is pydantic itself: every resolved value is
  validated against the field's annotation and constraints.
- **Anchor reachability.** `c.nearest(ModelConfig)` where no `ModelConfig` encloses
  type-checks and fails at finalize with the searched ancestor chain. Typed selectors also
  type-check structurally but assert the selected frame at runtime.
- **Lifecycle stage.** Drafts and finals share a static type; `C.is_draft()` exists for
  boundaries that care, and `C.finalize()` is idempotent so boundary code can normalize.

## The contained lies, and their gates

| Lie | Gate |
|---|---|
| `interp(...)` claims type `T` while being a marker (the `Field()` precedent) | hygiene dunders (`bool`/format raise), the root no-survivors sweep |
| Draft mutation on a frozen-typed class typechecks | runtime frozen check rejects writes to finals; pyright does not enforce `model_config`-level frozen (only the class-kwarg spelling), and the draft idiom deliberately relies on that — a golden-probe canary guards the assumption |

Note for mypy users: pydantic's mypy plugin flags writes to frozen models regardless of
spelling, so the draft idiom reports errors under it. basedpyright is the supported checker.
