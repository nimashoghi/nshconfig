# nshconfig v2 semantic design

This document is the semantic authority for nshconfig 2.2. The library is a
small lifecycle layer over Pydantic for typed ML-run configuration. Pydantic
owns schemas, aliases, validation, serialization, and JSON Schema. nshconfig
adds an explicit mutable draft state and declaration-ordered Python
interpolation.

## States and public surface

A `Config` subclass has two instance states:

1. A **draft** is mutable, may be incomplete, and has not crossed the Pydantic
   validation boundary.
2. A **final** is the result of ordinary Pydantic validation and is field-frozen
   through `frozen=True`.

Calling a Config class normally creates a final:

```python
run = RunConfig(seed=1)
```

Draft composition uses collision-resistant Config methods:

```python
work = RunConfig.config_draft()
work.seed = 1
run = work.config_finalize()
```

`config_draft()` takes no values. Draft assignments are composition operations,
not validation operations. `config_finalize()` is non-destructive: the original
draft remains editable and may be finalized repeatedly. Calling it on a final
raises `DraftError`.

The public package surface is intentionally small:

- `Config`
- `Context`
- `interp()`
- `is_draft()`
- `DraftError`
- `UnsetError`

There is no top-level draft or finalize function, provenance API, run-record
format, fingerprint API, loader, registry, decorator, code generator, or global
model configuration.

## Pydantic behavior

The base class enforces settings needed by the lifecycle:

- `extra="forbid"`
- `frozen=True`
- `validate_default=True`
- `revalidate_instances="always"`
- validation by aliases and canonical field names
- `from_attributes=False`

`strict=True` is the default policy and may be changed by a project base class.
Lifecycle settings may not be disabled.

Normal constructors and `model_validate()` retain Pydantic semantics. In
particular, model validators are trusted code. A model-after validator may mutate
or replace its result exactly as Pydantic permits. Such a validator can make
types or interpolated relationships inconsistent; nshconfig does not run a
second validation or interpolation pass afterward.

`model_copy()` is available on finals with Pydantic semantics. Its `update=`
values are not validated. Draft copying is rejected because copying incomplete
mutable composition state has no clear ownership semantics. `model_construct()`
and Pydantic's deprecated `copy()` remain unsupported.

To revalidate the concrete contents of a final, use the native spelling:

```python
checked = type(final).model_validate(final)
```

This validates current concrete values. It does not reconstruct an old
interpolation recipe.

Finals use value equality over their concrete class and declared fields.
Draft equality is identity equality. Drafts are unhashable. Finals use the same
field-value hashing rule as a frozen Pydantic model: a final is hashable exactly
when its field values are hashable. Hashes are ordinary process-local Python
hashes, not stable content identifiers.

Pydantic freezing is shallow. It blocks field rebinding but does not make a
nested list, dictionary, set, or arbitrary object immutable. Mutable aliases and
post-validation mutation have ordinary Pydantic consequences.

## Draft fields and provisional defaults

Reading a required scalar field that has not been assigned raises `UnsetError`.
A required field whose direct annotation is one concrete Config type lazily
creates a child draft. This gives required nested configurations an editable
draft spine without running Pydantic hooks.

Immutable scalar defaults may be read provisionally. Supported built-in
container defaults are copied before exposure. A materialized default records a
small structural baseline:

- If it is untouched, finalization omits it and lets Pydantic recompute the
  canonical default or factory result.
- If it is mutated, finalization supplies the current value as explicit input.
- If it contains child drafts, edits to those drafts make the branch explicit.

No operation history is retained. Built-in containers remain ordinary Python
`list`, `dict`, and `set` objects rather than tracking subclasses.

Arbitrary mutable objects are opaque. A provisional opaque default that cannot
be observed safely raises `UnsetError`; assign an explicit value instead.

Assignment stores an unvalidated value and marks the field explicit. Deletion
removes explicit state and reactivates the declared default. An `interp()` marker
may be assigned as the complete value of a Config field.

## Config defaults as templates

A Config final created through normal `Config(...)` construction retains a
private construction recipe consisting of copied raw keyword input and an
integrity token. This metadata is not part of equality, hashing, schemas, or
serialization. It is not Python source or an AST.

A recipe-bearing Config used as a field default is a **template**:

```python
class Child(Config):
    width: int = 128

DEFAULT_CHILD = Child()

class Parent(Config):
    child: Child = DEFAULT_CHILD
```

Inline and named values follow the same rule. A recipe-less final, or a final
whose declared value graph changed after recipe capture, is rejected as a
template rather than guessed from its validated values.

Pydantic may copy a declared default. Normal shallow and deep copies preserve an
intact recipe so Config defaults with unhashable fields remain valid templates.

When a parent draft materializes a default-origin graph, recipe-bearing Config
values become fresh drafts recursively through concrete annotated positions:

- direct Config fields
- supported unions
- list and tuple elements
- mapping values and TypedDict values

Mapping keys and set or frozenset elements remain final because drafts are
unhashable. Explicit finals assigned to a draft or passed to a constructor never
undergo this projection.

When a missing default is validated into a final parent, its recipe is replayed
as input inside the parent's active Pydantic field pipeline. Child interpolation
therefore sees the canonical parent context. The validated child default object
is not treated as an opaque completed branch.

Pydantic default factories are supported. A factory result receives the same
recursive default-origin handling. A child whose interpolation requires a parent
should use a deferred draft factory:

```python
class Parent(Config):
    source: int = 1
    child: Child = Field(default_factory=Child.config_draft)
```

Factories must return fresh values. A direct `Child()` default must validate
standalone while the parent class body executes. Its validators and factories
may run again when the template is realized, so configuration hooks should be
deterministic and side-effect-free.

## Interpolation

`interp()` stores a whole-field Python callable. The callable receives a
read-only `Context` and returns the input for that field's normal Pydantic
pipeline.

Field declaration order is dependency order. For each field:

1. Pydantic chooses explicit input, a default, or a default factory.
2. An interpolation marker is evaluated if present.
3. The complete Pydantic field pipeline validates the resulting input.
4. The canonical field value becomes visible to later interpolation.

Model-before validators run before this field sequence. Field validators retain
their native order. Post-init and model-after hooks run after all fields.

Interpolation may read only canonical fields whose validation is complete.
Reading the active field or a later field fails with a path-bearing validation
error. Nested Config validation establishes parent and root context. Independent
nested validation starts a fresh root and restores the outer context afterward.

Config fields and supported built-in containers use read-only context behavior.
Returning a container view materializes an independent value; returning an active
incomplete Config branch is rejected. Arbitrary user objects remain opaque and
retain their normal identity and behavior. An explicit field value overrides an
interpolation default. Deleting the explicit draft value reactivates the marker.

## Validation boundary and structural graph

Finalization recursively collects the declared Config graph, resolves defaults
and interpolation through Pydantic, and returns a fresh final. No draft or
interpolation marker may survive in an annotated Config position or supported
built-in container.

Config structure must be described by concrete annotations. A Config hidden
under `Any`, `object`, or an incompatible structural position is rejected because
Pydantic cannot validate it with the correct parent context. Arbitrary user
objects themselves are opaque; nshconfig does not crawl their `__dict__`, slots,
or private caches looking for lifecycle values.

Built-in input cycles are rejected with a path. Ordinary mutable aliasing is
allowed and follows Pydantic semantics. The validation boundary does not claim
deep immutability.

Draft serialization is rejected in the Config core schema, including
`TypeAdapter` and nested Pydantic serialization paths. A completed final contains
concrete values only. There is no `thaw()` operation: the original draft is the
only faithful executable recipe for later edits.

## Project composition convention

Reusable project helpers are ordinary in-place mutators that return the same
draft for composition:

```python
def resnet50(cfg: ModelConfig, *, d_model: int = 256) -> ModelConfig:
    cfg.d_model = d_model
    return cfg
```

Projects should place reusable mutators under `src/project/configs/`. Root files
under `configs/` expose the same contract through `__config__`:

```python
def __config__(cfg: TrainConfig) -> TrainConfig:
    resnet50(cfg.model)
    return cfg
```

The application owns file loading. It creates the expected root draft, calls
`__config__`, verifies that the returned object is the identical draft, and
finalizes exactly once. nshconfig does not provide a loader or registry.

## Typing contract

Pydantic's dataclass transform continues to describe normal `Config(...)`
construction. `config_draft()` and `config_finalize()` return `Self`, so fields
and helper functions retain the concrete Config type. Static typing does not
distinguish a draft from a final; lifecycle misuse is a runtime error.

The library supports Python 3.10 through 3.14 and Pydantic 2.13 through the
latest Pydantic 2.x release. Eager annotations are required. Do not use
`from __future__ import annotations`; quote only names defined later.
