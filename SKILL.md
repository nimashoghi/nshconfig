---
name: using-nshconfig
description: Builds typed Python configuration with explicit nshconfig drafts, replayable defaults, unbound parent-dependent templates, declaration-ordered interpolation, and normal Pydantic validation. Use when defining Config schemas, composing ML run settings, or finalizing drafts.
---

# Using nshconfig

Treat [DESIGN.md](DESIGN.md) as the semantic authority. `nshconfig` is a small
lifecycle layer over Pydantic and re-exports Pydantic's authoring APIs unchanged.

## Canonical workflow

```python
import nshconfig as C


class Norm(C.Config):
    dim: int = C.interp(lambda context: context.parent(Model).dim)


class Model(C.Config):
    dim: int = 768
    norm: Norm = Norm()


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

- `ConfigType(...)` first performs ordinary Pydantic construction and normally
  returns a validated final. It returns an inert unbound template only for the
  narrow parent-context failure described below. Use `ConfigType.config_draft()`
  only for mutable composition.
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
- `C.is_template(value)` identifies an unbound template. Its fields are not
  values: field access, mutation, finalization, iteration, model copying, and
  serialization raise `C.TemplateError` or Pydantic's serializer error. Normal
  Python copy, `repr`, Treescope, and trusted pickle/cloudpickle are supported.

## Nested defaults

A normal `Child(...)` final used as a field default is a replayable default:

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

Replayable finals retain a minimal raw-constructor recipe. A final created by
`model_validate()`, changed through unchecked copy updates, or mutated after its
recipe was captured is not a valid default recipe. Do not rely on source
inspection or factory-call analysis.

A direct `Child()` whose interpolation needs a parent becomes an **unbound
template**:

```python
class ParentDependentChild(C.Config):
    copied: int = C.interp(lambda context: context.parent(Parent).source)


class Parent(C.Config):
    source: int = 1
    child: ParentDependentChild = ParentDependentChild()
```

The constructor first tries normal Pydantic validation. It creates a template
only if at least one interpolation lacks ancestor/root/nearest context or raises
`NameError`, and all other failures are omitted required fields. Partial values
are discarded. `model_validate()`, `TypeAdapter`, and JSON/string validation
never create templates. A repeated `NameError` during binding fails normally, so
misspelled names are not hidden.

Use direct `Child()` defaults for ordinary and parent-dependent children.
Factories returning drafts or templates are rejected; do not use
`C.Field(default_factory=Child.config_draft)`. Validators, factories, and other
hooks may run once before fallback and again when a recipe binds, so keep them
deterministic and free of external side effects.

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

- Use `C.Field`, `C.ConfigDict`, validators, serializers, constraints, and
  `C.TypeAdapter` from the nshconfig namespace. These are direct Pydantic
  re-exports; importing them from `pydantic` is equivalent.
- Validation is strict by default. Project bases may change policy settings such
  as strictness or `arbitrary_types_allowed`, but cannot disable lifecycle
  settings or override reserved lifecycle methods.
- Attribute docstrings are field descriptions by default when class source is
  available. `C.Field(description=...)` takes precedence and works without source
  inspection.
- Model-after validators may mutate or replace the result. This can make earlier
  interpolation stale; nshconfig does not run a second pass.
- `final.model_copy()` and `model_copy(update=...)` follow Pydantic. Updates are
  not validated. Draft and template model copies are rejected.
- Revalidate concrete current values with
  `type(final).model_validate(final)`. This does not replay old interpolation.
- Finals are shallowly field-frozen. Mutable field contents remain mutable.
- Drafts and templates are unhashable. Finals use frozen-Pydantic field hashing and are
  hashable exactly when their field values are hashable.
- Config structure must have concrete annotations. Do not hide a Config in
  `Any`, `object`, or an incompatible built-in container position. Ordinary user
  objects are opaque and are not crawled for lifecycle values.
- Eager annotations, quoted forward references, and
  `from __future__ import annotations` are supported, including trusted
  cloudpickle transport of notebook-local Config classes. Unresolved names follow
  normal Pydantic `model_rebuild()` rules.

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
