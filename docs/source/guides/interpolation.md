# Interpolation and declaration order

`C.interp(fn)` derives one complete declared config field. The callable receives a
`C.Context` and returns an ordinary Python value, which then goes through the
target field's Pydantic validation.

The [semantic contract](../contract.md) defines the normative validation order,
selector visibility, and marker-placement rules.

```python
class LayerNorm(C.Config):
    dim: int = C.interp(lambda context: context.parent(Model).dim)


class Model(C.Config):
    dim: int = 768
    norm: LayerNorm
```

The same marker may be assigned to a draft for one composition only:

```python
work.model.norm.dim = C.interp(
    lambda context: context.parent(Model).dim * 2
)
```

An explicit concrete value always overrides an interpolated class default. Delete
the draft field to reactivate the class rule.

## Context selectors

| Selector | Result |
|---|---|
| `context.current()` | The model whose field is being validated |
| `context.parent()` | The exact enclosing config level |
| `context.parent(2)` | The ancestor two levels above the current model |
| `context.root()` | The root of this validation session |
| `context.nearest(Model)` | The nearest enclosing ancestor compatible with `Model` |

Pass a class to `current()`, `parent()`, or `root()` for checked field access and a
runtime class assertion. `parent(2, Run)` combines an exact hop count with that
assertion. Selectors without a class are dynamic.

When a selector reaches a completed nested `Config`, interpolation sees a read-only
view containing only its declared fields. Field access records the precise
dependency. Pydantic methods such as `model_dump()`, private state, undeclared
attributes, custom access hooks, and the view's backing runtime state are deliberately
unavailable. Slicing, concatenating, repeating, or merging a published container
keeps structured elements behind protected views. The resulting provenance retains
the truthful whole-container read and each structured element's original path. If a
resolver returns a published branch or container, nshconfig materializes an
independent value graph rather than preserving a proxy or source alias.
An arbitrary mapping key has no canonical path. When its value contains no nested
`Config`, access remains available through the same protected views and records the
whole mapping as a conservative dependency.

`Context` and every derived view are valid only for the current resolver call on the
thread that entered it. They expire when the resolver returns and reject access from
worker threads. Capture concrete values before starting parallel work.

## Validation order

Pydantic field declaration order is interpolation dependency order. For each model:

1. model-before validators may change the native Pydantic input;
2. the next field's interpolation resolves, if that field contains a marker;
3. Pydantic validates the resolved value and runs its field validators;
4. the canonical field value becomes visible to later interpolation;
5. after every field is complete, `model_post_init` and model-after validators run.

The instance passed to `model_post_init` and model-after validators is still
in-progress. Final-only APIs reject it. It becomes a final only after the hooks
return and lifecycle integrity checks succeed.

Interpolation may therefore read:

- an earlier field on the same model after aliases, constraints, and field
  validators have run;
- an already validated field on an ancestor;
- a completed config branch declared earlier on an ancestor.

It cannot read a later field, an incomplete container branch, or the ancestor field
whose nested model is still being built. These reads raise a structured Pydantic
validation error on the dependent field. Declare the source before the dependent
field, or read the current branch through `parent()` or `nearest()` from a
descendant.

Both source and target field validators run once. A value that must feed downstream
interpolation should be normalized in a field validator. Model-after validators run
too late to publish a different source value; changing a value already observed by
interpolation is rejected.

Field validators may use `before` or `after` mode, and model validators may use
`before` or `after` mode. Config class creation rejects model `wrap`, field `plain`
and `wrap`, and deprecated `validator`/`root_validator` decorators. Those forms can
skip or repeat the canonical validation pass that declaration-ordered interpolation
depends on.

Model-before validators have Pydantic's native `Any` input contract. They commonly
receive a mapping, but revalidation and draft finalization may pass an existing
model or an internal carrier. Type the parameter as `Any` and branch on the input
shape instead of assuming `dict`. A model-before validator may normalize legacy
names or custom collection input. Its result must use exact finite built-in
structural containers. It may not silently drop a provided declared field and
substitute a default; that fails as ignored input. A field validator also may not
raise `PydanticUseDefault` to replace explicit input; that fails as default
substitution. `Config` validation accepts mappings and `Config` instances, not
attribute-based input, even when a caller requests `from_attributes=True`.

`Literal` fields are ordinary interpolation targets, including singleton literals.
The derived value goes through Pydantic's literal validation exactly once. If a
literal is the tag used to select a discriminated-union branch, however, the input
tag must be concrete: branch selection necessarily happens before validation inside
the selected model. Use a string field name as the discriminator. Callable union
discriminators are rejected because an incomplete draft cannot run an opaque
branch-selection protocol.

## Marker rules

An interpolation marker is legal only as the whole value or default of a declared
`Config` field. It is not legal as a list element, mapping key, set element, or a
value hidden inside an opaque object. Nested config objects inside containers may
still define interpolated fields of their own. After model-before normalization,
structural containers must be exact built-in `dict`, `list`, `tuple`, `set`, and
`frozenset` values. Lazy containers and custom collection classes that remain at
that boundary are rejected, including lazy sync or async iterables, generators,
awaitables, and coroutines. Materialize and await them before validation.

Keep callables deterministic and free of side effects. A resolver exception is
reported as a structured validation error with the dependent field location and
the callable's source site. A final may never contain a surviving marker.
