# nshconfig v2 semantic design

This document is the semantic authority for nshconfig 2.2. The library is a
small lifecycle layer over Pydantic for typed ML-run configuration. Pydantic
owns schemas, aliases, validation, serialization, and JSON Schema. nshconfig
re-exports Pydantic's non-deprecated authoring API so applications can use one
namespace, then adds an explicit mutable draft state, inert unbound templates,
and declaration-ordered Python interpolation.

## States and public surface

A `Config` subclass has three instance states:

1. An **unbound template** is an immutable constructor recipe whose interpolation
   still needs an enclosing Config context. It is not a partially validated value.
2. A **draft** is mutable, may be incomplete, and has not crossed the Pydantic
   validation boundary.
3. A **final** is the result of ordinary Pydantic validation and is field-frozen
   through `frozen=True`.

Calling a Config class normally creates a final:

```python
run = RunConfig(seed=1)
```

There is one narrow constructor-only exception. If interpolation cannot run
because an ancestor, typed root, or nearest enclosing model does not exist yet,
normal construction returns an unbound template. A `NameError` raised inside the
interpolation callable may also defer a forward parent name. Every other error
must be an omitted required field; otherwise the original Pydantic
`ValidationError` is raised. All partially validated values are discarded.

`model_validate()`, `TypeAdapter`, JSON validation, and string validation never
create templates. They either return finals or fail validation.

Draft composition uses collision-resistant Config methods:

```python
work = RunConfig.config_draft()
work.seed = 1
run = work.config_finalize()
```

`config_draft()` takes no values. Draft assignments are composition operations,
not validation operations. `config_finalize()` is non-destructive: the original
draft remains editable and may be finalized repeatedly. Calling it on a final
raises `DraftError`; calling it on a template raises `TemplateError`.

The nshconfig-native lifecycle surface is intentionally small:

- `Config`
- `Context`
- `interp()`
- `is_draft()`
- `is_template()`
- `DraftError`
- `TemplateError`
- `UnsetError`

The package also re-exports Pydantic's non-deprecated public authoring API at the
supported Pydantic floor, including `Field`, `ConfigDict`, validators,
serializers, constraints, `TypeAdapter`, and `ValidationError`. These names are
the identical Pydantic objects, not nshconfig wrappers. Deprecated Pydantic v1
compatibility names and Pydantic's version constants are not re-exported.

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

`strict=True` and `use_attribute_docstrings=True` are default policies and may be
changed by a project base class. Attribute docstrings become field descriptions
when Pydantic can inspect the class source; an explicit `Field(description=...)`
takes precedence. Lifecycle settings may not be disabled.

Apart from the constructor-only unbound fallback above, normal constructors and
`model_validate()` retain Pydantic semantics. Model validators are trusted code.
A model-after validator may mutate or replace its result exactly as Pydantic
permits. Such a validator can make types or interpolated relationships
inconsistent; nshconfig does not run a second validation or interpolation pass
afterward.

`model_copy()` is available on finals with Pydantic semantics. Its `update=`
values are not validated. Draft copying is rejected because copying incomplete
mutable composition state has no clear ownership semantics. Template
`model_copy()` is rejected because a template has no validated field values;
normal `copy.copy()` and `copy.deepcopy()` preserve its recipe.
`model_construct()` and Pydantic's deprecated `copy()` remain unsupported.

To revalidate the concrete contents of a final, use the native spelling:

```python
checked = type(final).model_validate(final)
```

This validates current concrete values. It does not reconstruct an old
interpolation recipe.

Finals use value equality over their concrete class and declared fields. Drafts
and templates use identity equality. Both are unhashable. Finals use the same
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

## Replayable defaults and unbound templates

A final created through normal `Config(...)` construction retains a private
construction recipe consisting of copied raw keyword input and an integrity
token. This metadata is not part of equality, hashing, schemas, or serialization.
It is not Python source or an AST. Such a final can be a **replayable default**:

```python
class Child(Config):
    width: int = 128


DEFAULT_CHILD = Child(width=256)


class Parent(Config):
    child: Child = DEFAULT_CHILD
```

Inline and named defaults follow the same rule. A recipe-less final, or a final
whose declared value graph changed after recipe capture, is rejected rather than
guessed from its validated values. Pydantic may copy a declared default; normal
shallow and deep copies preserve an intact recipe, including when the Config has
unhashable fields.

A child whose interpolation needs its parent uses the same direct syntax:

```python
class ParentDependentChild(Config):
    copied: int = interp(lambda context: context.parent(Parent).source)


class Parent(Config):
    source: int = 1
    child: ParentDependentChild = ParentDependentChild()
```

The `ParentDependentChild()` call first attempts ordinary standalone Pydantic
validation. The missing parent makes it an unbound template containing only the
raw constructor recipe and concise reasons it could not bind. It contains no
canonical field values. During `Parent()` validation, nshconfig replays that
recipe under the active parent and the result is a normal final child.

A missing ancestor, typed root, or nearest enclosing model is deferrable. A
`NameError` inside interpolation is also deferrable so a lambda may refer to the
parent class being defined later. When a template is bound, a repeated
`NameError` becomes an ordinary interpolation error; a misspelled name cannot
turn the enclosing parent into another template. Current/parent selector type
mismatches, later-field reads, validator failures, extra inputs, strict type
failures, and all other errors remain immediate.

Missing required fields may coexist with at least one unbound interpolation in a
template. A parent draft can project the template to a child draft and fill those
slots. Missing required fields by themselves do not create templates.

Both replayable final defaults and unbound templates become fresh drafts when a
parent draft materializes a default-origin graph. Projection and binding recurse
through concrete annotated positions:

- direct Config fields;
- supported unions;
- list and tuple elements;
- mapping values and TypedDict values.

Mapping keys and set or frozenset members cannot contain drafts or templates
because both lifecycle states are unhashable. Explicit finals assigned to a
draft or passed to a constructor never undergo default projection. An explicitly
passed template does bind in a concrete annotated Config position.

When a replayable final or template is realized, its recipe re-enters the
ordinary child Pydantic pipeline under the parent's active context. Validators,
default factories, `model_post_init`, and model validators may therefore run once
before constructor fallback and again during binding. Configuration hooks should
be deterministic and side-effect-free.

Default factories remain ordinary Pydantic factories only when they return
concrete values or validated finals. A factory that returns a draft or unbound
template is rejected. In particular,
`Field(default_factory=Child.config_draft)` is unsupported: use the direct
`child: Child = Child()` spelling.

An unbound template is an inert recipe, not a value. Declared field access,
mutation, deletion, iteration, finalization, `model_copy()`, and Pydantic or JSON
serialization raise `TemplateError` directly or a Pydantic serialization error at
the core serializer boundary. `repr()`, Treescope rendering, `is_template()`,
normal shallow/deep copy, and trusted pickle or cloudpickle transport are
supported. Template equality is identity equality.

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
If an earlier interpolation is unbound, later interpolation that reads it is also
marked unbound instead of producing a misleading declaration-order error.

Config fields and supported built-in containers use read-only context behavior.
Returning a container view materializes an independent value; returning an active
incomplete Config branch is rejected. Arbitrary user objects remain opaque and
retain their normal identity and behavior. An explicit field value overrides an
interpolation default. Deleting the explicit draft value reactivates the marker.

## Validation boundary and structural graph

Finalization recursively collects the declared Config graph, resolves defaults
and interpolation through Pydantic, and returns a fresh final. No draft, unbound
template, or interpolation marker may survive in an annotated Config position or
supported built-in container.

Config structure must be described by concrete annotations. A Config, including
an unbound template, hidden under `Any`, `object`, or an incompatible structural
position is rejected because Pydantic cannot validate it with the correct parent
context. Arbitrary user objects themselves are opaque; nshconfig does not crawl
their `__dict__`, slots, or private caches looking for lifecycle values.

Built-in input cycles are rejected with a path. Ordinary mutable aliasing is
allowed and follows Pydantic semantics. The validation boundary does not claim
deep immutability.

Draft and template serialization is rejected in the Config core schema,
including `TypeAdapter` and nested Pydantic serialization paths. A completed
final contains concrete values only. There is no `thaw()` operation: the
original draft is the only faithful executable recipe for later edits.

The optional trusted cloudpickle transport supports notebook-local Config
classes, drafts, finals, templates, and interpolation callables with eager
annotations, quoted forward references, or `from __future__ import annotations`.
Config validators and serializers defer their own reconstruction until
cloudpickle has restored the complete dynamic class. The behavior is local to
Config classes; importing nshconfig installs no global pickle reducer and does
not alter unrelated Pydantic models.

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

Pydantic's dataclass transform continues to describe `Config(...)` construction.
Its static return type remains the concrete Config type even when the narrow
runtime fallback creates an unbound template. `config_draft()` and
`config_finalize()` return `Self`, so fields and helper functions retain the
concrete Config type. Static typing does not distinguish a template, draft, or
final; lifecycle misuse is a runtime error. A typed selector may refer to a
parent class defined later, including under future annotations.

The library supports Python 3.10 through 3.14 and Pydantic 2.13 through the
latest Pydantic 2.x release. Eager annotations, explicit quoted forward
references, and `from __future__ import annotations` are supported. Annotation
resolution otherwise follows Pydantic: names must be resolvable when the schema
is built, or the application must call `model_rebuild()` after defining them.
