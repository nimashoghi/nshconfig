---
name: using-nshconfig
description: Builds typed Python configuration with explicit nshconfig drafts, nested default templates, declaration-ordered interpolation, and normal Pydantic validation. Use when defining Config schemas, composing ML run settings, or finalizing drafts.
---

# Using nshconfig

Treat [DESIGN.md](DESIGN.md) as the semantic authority. `nshconfig` is a small
lifecycle layer over Pydantic, not a replacement for Pydantic authoring APIs.

## Canonical workflow

```python
from pydantic import Field

import nshconfig as C


class Norm(C.Config):
    dim: int = C.interp(lambda context: context.parent(Model).dim)


class Model(C.Config):
    dim: int = 768
    norm: Norm = Field(default_factory=Norm.config_draft)


class Run(C.Config):
    seed: int
    model: Model = Model()


work = Run.config_draft()
work.seed = 7
work.model.dim = 1024
final = work.config_finalize()

assert final.model.norm.dim == 1024
assert C.is_draft(work)
```

## Lifecycle rules

- `ConfigType(...)` is ordinary Pydantic construction and returns a validated
  final. Use `ConfigType.config_draft()` only for mutable composition.
- `config_draft()` takes no input and skips validators, default factories,
  private factories, and `model_post_init`.
- Assign declared fields normally. Assignment is unvalidated. Deleting a field
  reactivates its declared default, factory, interpolation, or missing state.
- A required field with one concrete `Config` annotation auto-creates a child
  draft on access. Other missing required fields and pending interpolation raise
  `C.UnsetError` when read.
- `draft.config_finalize()` recursively collects the declared Config graph and
  returns a fresh final. It does not consume the draft. Calling it on a final
  raises `C.DraftError`.
- Draft copies and Pydantic or JSON serialization are rejected. Keep the original
  draft when another variant is needed. Trusted pickle transport is a separate,
  explicit mechanism.

## Nested defaults

A normal `Child(...)` final used as a field default is a template:

```python
class Child(C.Config):
    width: int = 128


DEFAULT_CHILD = Child(width=256)


class Parent(C.Config):
    child: Child = DEFAULT_CHILD
    stages: list[Child] = [Child(width=512)]
```

`Parent.config_draft()` projects the default-origin children above to fresh
drafts. Projection recurses through concrete Config fields, unions, lists, tuples,
mapping values, and TypedDict values. Mapping keys and set or frozenset members
stay final because drafts are unhashable. A final explicitly assigned to a draft
stays final.

Templates retain a minimal raw-constructor recipe. A final created by
`model_validate()`, changed through unchecked copy updates, or mutated after its
recipe was captured is not a valid template. Do not rely on source inspection or
factory-call analysis.

Use `Field(default_factory=Child.config_draft)` when the child has interpolation
that needs its parent. A direct `Child()` default must validate without a parent
while the class body executes. Defaults and factories may run again when a
template is realized, so keep them deterministic and free of external side
effects.

## Interpolation order

`C.interp(fn)` occupies one whole field. Its `C.Context` supports `current()`,
`parent()`, `root()`, and `nearest(ConfigType)`. Pass a Config class to a selector
for statically checked field access.

For each field, Pydantic chooses input or a default, nshconfig evaluates an
interpolation marker, the complete native field pipeline validates the result,
and only then is the canonical field visible to later interpolation. Model-before
validators run before this sequence. Model-after validators and `model_post_init`
run afterward with native Pydantic semantics.

Context views prevent mutation through Config fields and supported built-in
containers. A later field, the active field, and an incomplete ancestor branch are
unavailable. Arbitrary user objects are opaque and retain their normal identity
and behavior.

An explicit value overrides an interpolation default. Deleting that draft value
reactivates the marker. An interpolation marker is legal only as a complete Config
field value, not inside a built-in container.

## Pydantic rules and footguns

- Import `Field`, `ConfigDict`, validators, serializers, and other authoring APIs
  from `pydantic`.
- Validation is strict by default. Project bases may change policy settings such
  as strictness or `arbitrary_types_allowed`, but cannot disable lifecycle
  settings or override reserved lifecycle methods.
- Model-after validators may mutate or replace the result. This can make earlier
  interpolation stale; nshconfig does not run a second pass.
- `final.model_copy()` and `model_copy(update=...)` follow Pydantic. Updates are
  not validated. Draft copies are rejected.
- Revalidate concrete current values with
  `type(final).model_validate(final)`. This does not replay old interpolation.
- Finals are shallowly field-frozen. Mutable field contents remain mutable.
- Drafts are unhashable. Finals use frozen-Pydantic field hashing and are
  hashable exactly when their field values are hashable.
- Config structure must have concrete annotations. Do not hide a Config in
  `Any`, `object`, or an incompatible built-in container position. Ordinary user
  objects are opaque and are not crawled for lifecycle values.
- Do not use `from __future__ import annotations`. Quote only genuine forward
  references. Eager annotations preserve notebook and cloudpickle behavior across
  supported Python versions.

## Project composition

Reusable builders and finalized experiment files use the same mutator shape:

```python
def resnet50(cfg: ModelConfig, *, d_model: int = 256) -> ModelConfig:
    cfg.d_model = d_model
    return cfg


def __config__(cfg: TrainConfig) -> TrainConfig:
    resnet50(cfg.model)
    return cfg
```

Place reusable functions under `src/project/configs/` and root experiment files
under `configs/`. The application creates the expected root draft, calls
`__config__`, checks that it returned the same object, and finalizes once.
`nshconfig` does not load files or maintain a registry.

## Verification

```bash
uv run pytest
uv run basedpyright src
uv run ruff check src tests
uv run sphinx-build -W --keep-going -b html docs/source docs/build/html
uv run nox -s tests
```
