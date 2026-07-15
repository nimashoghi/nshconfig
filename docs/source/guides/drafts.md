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

## Final defaults are templates

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
after recipe capture is rejected as a template. This avoids guessing how to
recreate executable configuration.

Pydantic may copy a declared default. nshconfig preserves the template recipe
across that normal copy, including for Config values with unhashable fields.

## Parent-dependent default children

A direct `Child()` default is validated while the parent class body executes. It
cannot read a parent that does not yet exist. Defer such a child as a draft:

```python
from pydantic import Field


class Child(C.Config):
    copied: int = C.interp(lambda context: context.parent(Parent).source)


class Parent(C.Config):
    source: int = 3
    child: Child = Field(default_factory=Child.config_draft)
```

The factory runs in the parent's field pipeline. `Parent().child.copied` is `3`,
and changing `source` on a parent draft changes the finalized child.

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

Draft equality is identity equality and drafts are unhashable. Finals use value
equality and frozen-Pydantic field hashing. A final with a list or dictionary field
is therefore naturally unhashable. Field freezing is shallow and does not freeze
mutable contents.
