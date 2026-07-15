# Failures and diagnostics

Let lifecycle and Pydantic errors surface at their natural boundary:

| Error | Meaning |
| --- | --- |
| `UnsetError` | A draft field was read before it had a usable value |
| `DraftError` | A draft was copied or sent through Pydantic serialization, or a final was finalized again |
| `TemplateError` | An unbound template was used as though it had validated field values |
| Pydantic `ValidationError` | Final input, interpolation output, or a structural Config position was invalid |
| `TypeError` | A Config class disabled a lifecycle invariant or declared an unsupported schema shape |
| `AttributeError` | A draft write used an unknown field or interpolation read unavailable context |

## Missing draft values

Required scalar reads fail immediately. Required direct Config fields create a
child draft, but a recursive required spine without a base case fails rather than
recursing forever. Pending interpolation can be assigned and finalized but cannot
be read from a draft.

## Interpolation failures

Interpolation errors report the target path and callable site. Common causes are:

- reading a later field;
- selecting an ancestor above the current root;
- requesting the wrong typed context model;
- returning an active incomplete branch;
- mutating a read-only built-in container view;
- raising inside the user callable.

Reorder source fields before dependent fields. For a default child that needs
parent context, use the direct `child: Child = Child()` spelling. The standalone
child constructor becomes an unbound template and binds in the parent's field
pipeline. Do not use a draft-returning default factory.

An unbound template is created only by normal `Config(...)` construction when at
least one interpolation lacks an ancestor/root/nearest context or raises
`NameError`, with no other errors except omitted required fields. A repeated
`NameError` during binding is reported as a normal interpolation failure. Other
Pydantic validation entry points never return templates.

## Structural graph failures

Draft collection and template binding follow concrete annotations through Config
fields and supported built-in containers. A Config under `Any`, `object`, a wrong
union branch, or an incompatible tuple or mapping position is rejected. Markers
inside built-in containers are also rejected.

Arbitrary user objects are opaque and are not crawled for drafts or markers. If
such an object needs lifecycle-aware children, move those children into directly
annotated Config fields.

## Native Pydantic footguns

`model_copy(update=...)` does not validate updates. Model-after validators and
`model_post_init` may mutate values after interpolation. Shallowly frozen finals
may still have mutable container contents. Revalidate current concrete values with
`type(final).model_validate(final)` when needed.
