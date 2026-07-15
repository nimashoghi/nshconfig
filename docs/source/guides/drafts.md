# Drafts and nested defaults

## Explicit lifecycle

`ConfigType(...)` returns a validated final. `ConfigType.config_draft()` returns a
mutable incomplete instance of the same static type:

```python
class Job(C.Config):
    workers: int
    labels: list[str] = []


final = Job(workers=8)
work = Job.config_draft()
work.workers = 8
```

Draft creation does not run field validators, model validators, default factories,
private factories, or `model_post_init`. Assignment stores an unvalidated value.
Unknown attributes fail immediately.

`work.config_finalize()` crosses the Pydantic validation boundary and returns a
fresh final. The original draft remains editable and can produce another variant.
Calling `config_finalize()` on a final raises `DraftError`.

## Required child spines

A missing required field whose direct annotation is one concrete Config type
creates a child draft on first access:

```python
class Leaf(C.Config):
    width: int


class Root(C.Config):
    leaf: Leaf


work = Root.config_draft()
work.leaf.width = 256
```

A required scalar does not invent a value. Reading it raises `UnsetError` until it
is assigned. Recursive required Config spines stop with a clear error instead of
recursing without a base case.

## Replayable final defaults

A normally constructed Config final used as a default retains a minimal copy of
its raw constructor input:

```python
class Child(C.Config):
    width: int = 128


DEFAULT_CHILD = Child(width=256)


class Parent(C.Config):
    child: Child = DEFAULT_CHILD
    stages: list[Child] = [Child(width=512)]
```

On `Parent.config_draft()`, default-origin children become fresh drafts. Inline
and named defaults follow the same rule. Projection recurses through:

- direct concrete Config fields;
- supported union branches;
- list and tuple elements;
- mapping values and TypedDict values.

Mapping keys and set or frozenset members stay final because drafts are
unhashable. A final explicitly assigned to a draft also stays final:

```python
chosen = Child(width=1024)
work = Parent.config_draft()
work.child = chosen
assert work.child is chosen
```

The recipe is raw input, not Python source or an AST. A final created without a
normal constructor recipe, changed through unchecked copy updates, or mutated
after recipe capture is rejected as a replayable default. This avoids guessing
how to recreate executable configuration.

Pydantic may copy a declared default. nshconfig preserves the recipe across that
normal copy, including for Config values with unhashable fields.

## Parent-dependent default children

A direct `Child()` default first attempts ordinary validation while the parent
class body executes. Missing parent context turns that constructor call into an
inert unbound template:

```python
class Child(C.Config):
    copied: int = C.interp(lambda context: context.parent(Parent).source)


class Parent(C.Config):
    source: int = 3
    child: Child = Child()
```

The template contains raw constructor input and binding diagnostics, not partial
field values. Its recipe runs in the parent's normal Pydantic field pipeline.
`Parent().child.copied` is `3`, and changing `source` on a parent draft changes
the finalized child.

Fallback is narrow. At least one interpolation must lack an ancestor, typed root,
or nearest enclosing model, or raise `NameError`; every other error must be an
omitted required field. Missing fields alone do not create a template. Strict
type errors, extras, validators, wrong current/parent types, later-field reads,
and other failures remain normal validation errors. `model_validate()`,
`TypeAdapter`, and JSON/string validation never create templates. A forward name
may defer once, but a repeated `NameError` during binding fails normally.

Use direct `Child()` defaults for ordinary and parent-dependent children. A
default factory returning a draft or unbound template is rejected, including
`C.Field(default_factory=Child.config_draft)`.

When a parent draft materializes an unbound template, it becomes a fresh editable
child draft. Declared field access, mutation, deletion, iteration, finalization,
model copying, and Pydantic/JSON serialization are invalid on the template itself.
Use `C.is_template()` to inspect the state. `repr`, Treescope, normal shallow/deep
copy, and trusted pickle/cloudpickle preserve the inert recipe.

Validators, factories, `model_post_init`, and model validators may run before
constructor fallback and again during binding. Keep configuration hooks
deterministic and side-effect-free.

## Provisional defaults

Safe scalar and built-in container defaults may be read from a draft. Built-in
containers are copied before exposure. The draft remembers a structural baseline:

- an untouched value is omitted at finalization, so Pydantic recomputes the
  canonical default or factory result;
- an in-place mutation is supplied as explicit input;
- editing a projected child draft makes its enclosing default branch explicit.

Simply reading a nested child's own default does not pin the branch. Built-in
containers are ordinary Python values, not tracking subclasses.

Arbitrary mutable defaults are opaque and cannot be observed provisionally. Assign
an explicit value or read the value from the final instead.

## Copy, equality, and hashing

Draft copying is rejected. Finals support `model_copy()` with native Pydantic
semantics, including unvalidated `update=` values. Use
`type(final).model_validate(final)` to revalidate current concrete contents.

Draft and template equality is identity equality and both are unhashable.
Templates support normal Python shallow/deep copy but not `model_copy()`. Finals
use value equality and frozen-Pydantic field hashing. A final with a list or
dictionary field is therefore naturally unhashable. Field freezing is shallow
and does not freeze mutable contents.
