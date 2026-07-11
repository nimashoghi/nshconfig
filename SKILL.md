---
name: using-nshconfig
description: Builds typed Python configuration with nshconfig drafts, interpolation, provenance, fingerprints, run records, and trusted notebook transport. Use when defining Config schemas, composing ML run settings, finalizing drafts, explaining values, or saving reproducible configuration records.
---

# Using nshconfig

Treat [DESIGN.md](DESIGN.md) as the semantic authority. `nshconfig` adds a small
lifecycle to Pydantic; it does not replace Pydantic schema or validation APIs.

## Canonical workflow

```python
from pydantic import Field

import nshconfig as C


class Optimizer(C.Config):
    learning_rate: float = Field(default=3e-4, gt=0)


class LayerNorm(C.Config):
    dim: int = C.interp(lambda context: context.parent(Model).dim)


class Model(C.Config):
    dim: int = 768
    norm: LayerNorm


class Run(C.Config):
    optimizer: Optimizer
    model: Model


work = C.draft(Run)
with C.source("sweep:large"):
    work.model.dim = 1024
    work.optimizer.learning_rate = 1e-4
final = C.finalize(work)
assert final.model.norm.dim == 1024
print(C.explain(final, "model.norm.dim"))
run_record = C.record(final)
restored = C.load_record(Run, run_record)
```

## Lifecycle rules

- Define schemas by subclassing `C.Config`. Import `Field`, `field_validator`,
  `ConfigDict`, and other authoring tools directly from `pydantic`.
- Create composition state only with `C.draft(ConfigType)`. `draft()` takes no
  seed values and skips validators, default factories, and `model_post_init`.
- Assign declared fields normally. Unknown attributes fail immediately. Deleting
  a field reactivates its class default, factory, interpolation, or missing state.
  Draft private-attribute writes and deletions are rejected; private factories run
  only when a final is validated.
- Required fields annotated as one concrete `Config` subclass auto-create on
  access. Required scalars and pending interpolation raise `C.UnsetError` on read.
- Keep every direct `Config` field required or give it an `interp()` default. Do
  not use a concrete `Config`, mapping, `None`, or default factory; each would create
  the child without an explicit parent interpolation context.
- Built-in `list`, `dict`, and `set` mutations on drafts are tracked and pin a
  provisional default-factory result as user input.
- Call `C.finalize(work)` at the validation boundary. It does not consume the
  draft; edit the same draft and finalize it again for a sweep.
- `C.finalize(final)` revalidates concrete values into a fresh final, preserves
  `model_fields_set` and provenance, and does not replay interpolation. Validators
  receive an isolated structural graph and must be idempotent on canonical values.
  A final with an identity-bearing mutable atomic value cannot be safely revalidated;
  rebuild it from its draft or a mapping.
- Finals are field-frozen, not deeply immutable. Every `Config` is unhashable.
  Use `C.fingerprint(final)` for deterministic content identity.
- Never serialize a draft. All Pydantic serialization paths reject it.
- Treat the instance visible in `model_post_init` and model-after validators as
  in-progress, not final. Final-only APIs reject it until validation returns.
- Do not use `model_construct()`, `Config.copy()`, `model_copy(update=...)`, or
  direct `__init__` reentry to bypass the lifecycle. Draft copy operations are
  rejected. An unchanged final may use normal shallow/deep copy or
  `model_copy()` without updates.

## Interpolation order

`C.interp(fn)` occupies one complete declared field value. The callable receives
a `C.Context` with `current()`, `parent()`, `root()`, and `nearest(ConfigType)`.
Pass a config class to a selector when static field access matters.
The context and all derived views expire when that resolver returns and are confined
to its originating thread. Read concrete inputs before starting worker threads.

Pydantic declaration order is dependency order. Interpolation may read an earlier
validated field on the same model, a validated ancestor field, or a completed
earlier branch on an ancestor. It may not read a later field, the ancestor field
whose child is still being built, or an incomplete container branch. Reorder the
source before the dependent field when such a read fails.

A completed nested `Config` is published through a read-only view of declared
fields. Interpolation cannot call Pydantic methods or inspect private, undeclared,
custom, or backing state through that view. Slicing or combining a published
container protects structured elements and keeps truthful whole-container and
origin dependencies. Returning a published branch or container materializes an
independent value graph.

An explicit input overrides an interpolated class default. Source aliases,
constraints, and field validators finish before the canonical source value becomes
visible. The derived target then runs its own validation once. Keep interpolation
callables deterministic and free of side effects.

Markers are illegal in container elements, mapping keys, set elements, and opaque
objects. A nested `Config` in a container needs a concrete structural annotation;
do not hide drafts under `Any` or `object`.

Model-before validators follow Pydantic's native contract: type their input as
`Any` and handle either a mapping or a revalidated model/carrier. They may normalize
legacy input or custom collections, but their output must use exact finite built-in
structural containers. They must not discard a provided declared field and silently
use a default. Field validators must not use `PydanticUseDefault` to replace explicit
input. Config validation accepts mappings and `Config` instances only;
`from_attributes=True` is rejected even as a per-call override.

Use field validators only in `before` or `after` mode and model validators only in
`before` or `after` mode. Config class creation rejects model `wrap`, field `plain`
or `wrap`, and the deprecated `validator`/`root_validator` decorators because they
can bypass or repeat canonical validation.

Named type aliases preserve structural annotations. Use a string field name for a
tagged-union discriminator; callable discriminators are rejected. Mapping keys may
be arbitrary only when their values contain no `Config` structure. Structural
config mapping paths require exact `str` or `int` keys.
When no `Config` lies below an arbitrary key, interpolation is allowed and records a
coarse whole-mapping dependency.

## Provenance, records, and transport

Use `C.source(label)` to label draft operations. `C.explain(config, "path.to.field")`
follows interpolation reads. `C.provenance(config)` returns a defensive mapping
whose event sequences are immutable tuples.

`C.record(final)` is durable, inert JSON data. Save it with `to_json()` or
`to_dict()`. The seven-field `nshconfig.run-record.v4` envelope contains `format`,
`config_type`, `schema_fingerprint`, `semantic_fingerprint`, `fingerprint`, `values`,
and `provenance`. `C.load_record(ConfigType, value)` verifies all three fingerprints
and the concrete type without executing interpolation. The value fingerprint binds
canonical JSON values to the exact-runtime semantic token and schema fingerprint.
It preserves dictionary insertion order; changing that order changes identity. A
fingerprint is emitted only when every field is consumed and the JSON values
reconstruct the same runtime meaning, so exclusions and lossy serializers are
rejected. Provenance must be self-contained under the recorded `Config` root. A
record captures what ran; it cannot recreate the original draft or Python mutation
sequence.

The schema fingerprint covers presentation-stripped canonical and alias validation
and serialization schemas, every reachable model's aliases, and every reachable
`Config` record identity. It does not hash Python validator or serializer code. An
exact generated generic parameterization must declare a distinct non-empty
`record_schema_id`; a uniquely named concrete subclass already has a stable identity
and may use an ID to version behavior that schema output does not expose. Recorded
interpolation events use versioned structural tokens for their result and reads.
Opaque tokens, out-of-root reads, impossible self/later/incomplete-branch edges, or
an interpolation event followed by another event make provenance unrecordable.
Display strings are bounded presentation data and are normalized when loading.

Do not mutate a final concurrently with `C.fingerprint()` or `C.record()`. The
library compares repeated values and structural snapshots and raises when it
observes a change, but callers must provide synchronization.

Reusing a final as a branch rejects any interpolation history because shallow
freezing cannot prove that its recipe is still fresh. Other provenance is preserved;
field reads, `C.explain()`, and `C.provenance()` do not affect eligibility.

Use cloudpickle only for trusted, short-lived executable transport of drafts or
notebook-local classes. Sender and receiver need the same Python version and
compatible dependencies. Never load an untrusted pickle.

## Failure and code-generation rules

- Let `UnsetError`, `DraftError`, Pydantic `ValidationError`, `FingerprintError`,
  and `RecordError` surface; do not replace them with partial output or fallbacks.
- Keep structural config graphs finite and acyclic. Errors identify the field or
  graph path that violated the contract. Structural nodes are `Config` and exact
  built-in `dict`, `list`, `tuple`, `set`, and `frozenset` values. Dataclasses,
  ordinary Pydantic models, and other non-collection user values are atomic and
  cannot contain `Config` or pending lifecycle values. Known carrier objects are
  inspected; opaque callables with uninspectable captured state are rejected, while
  other truly opaque extension state cannot be proven safe. Atomic internal
  containers do not become Config structural nodes. A model-before
  validator may normalize custom input, but custom collections and lazy sync or
  async containers, iterators, generators, awaitables, and coroutines left afterward
  are rejected; materialize and await them before validation.
- Do not use `from __future__ import annotations`. Quote only forward references
  that are genuinely needed; this preserves notebook/cloudpickle behavior.
- Use basedpyright. Typed context selectors check field access; selector
  reachability and lifecycle stage remain runtime properties.

## Verification

```bash
uv run pytest
uv run basedpyright src
uv run ruff check src tests
uv run ruff format --check src tests
uv run sphinx-build -W --keep-going -b html docs/source docs/build/html
uv run nox -s tests
```
