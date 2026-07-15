# Interpolation

`C.interp(fn)` stores a callable as one complete field value. The callable receives
a read-only `C.Context` and returns input for that field's ordinary Pydantic
pipeline.

```python
class Schedule(C.Config):
    warmup: int = 100
    total: int = 1000
    decay_steps: int = C.interp(
        lambda context: context.current(Schedule).total
        - context.current(Schedule).warmup
    )
```

## Declaration order

Field declaration order is dependency order. For each field:

1. Pydantic chooses explicit input, a default, or a factory result.
2. nshconfig evaluates an interpolation marker if present.
3. The complete native field pipeline validates the result.
4. The canonical value becomes visible to later fields.

A field validator can normalize a source before interpolation reads it. The
derived field then runs its own validators exactly once. Reading the active field
or a later field fails with the target path.

Model-before validators run before field sequencing and may transform the input
with native Pydantic semantics. `model_post_init` and model-after validators run
after interpolation. They may mutate or replace results, so they can intentionally
make an earlier relationship stale. nshconfig does not run a second pass.

## Context selectors

- `context.current()` returns the active Config view.
- `context.parent()` returns an ancestor by hop count.
- `context.root()` returns the validation root.
- `context.nearest(ConfigType)` finds the nearest compatible enclosing Config.

Passing a Config class to `current`, `parent`, or `root` exposes typed fields to a
static checker:

```python
copied: int = C.interp(lambda context: context.parent(Model).dim)
```

Nested validation maintains the root-to-current stack. A completed earlier branch
is visible to later siblings. An ancestor branch still being constructed is not a
completed value, but its already validated fields may be selected.

During direct `Child()` construction, a missing ancestor, typed root, or nearest
enclosing model may create an unbound template for later parent binding. A later
interpolation that reads an earlier unbound field propagates that state. Wrong
current/parent types and ordinary declaration-order errors remain immediate. See
[drafts and nested defaults](drafts.md).

## Read-only values

Config fields, ordinary Pydantic models, mappings, lists, tuples, sets, and
frozensets are exposed through read-only behavior during interpolation. Returning
a container view materializes an independent built-in value graph. Returning an
active Config branch itself is rejected because it is incomplete.

Arbitrary user objects are opaque. nshconfig does not copy, crawl, or proxy their
internal state, so they retain normal identity and behavior. Keep interpolation
callables deterministic and avoid mutating opaque objects.

## Overrides and structural positions

Explicit field input overrides an interpolation default. Deleting the explicit
value from a draft reactivates the marker.

An interpolation marker is legal only as a complete Config field value. A marker
inside a list, tuple, mapping, set, or frozenset is rejected with its structural
path. A string-discriminated union's discriminator must be concrete before branch
selection.

Config values used by interpolation or draft collection need concrete structural
annotations. Do not hide them under `Any`, `object`, or an incompatible union
branch.
