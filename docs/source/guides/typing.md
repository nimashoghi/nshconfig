# Typing

[basedpyright](https://docs.basedpyright.com/) is the supported type checker. The
repository keeps passing and intentionally failing probe files under
`tests/typing_probes/`; their diagnostics are part of the public contract.

The [semantic contract](../contract.md) defines the runtime checks that complement
the static guarantees.

## Statically checked

A draft has the same static type as its config class. Editors and basedpyright
therefore check ordinary field reads, assignments, and helper signatures:

```python
def large_model(work: Model) -> None:
    work.dim = 1024
    work.norm.dim = C.interp(lambda context: context.parent(Model).dim)


work = C.draft(Model)
large_model(work)
final: Model = C.finalize(work)
```

`interp()` is typed like Pydantic's default helpers: its public result type is the
callable's return type even though its runtime value is a pending marker until
validation. A wrong resolver return type is therefore reported both in a class
default and in a draft assignment.

Passing a config class to a context selector provides checked fields:

```python
context.current(LayerNorm).dim
context.parent(Model).dim
context.parent(2, Run).optimizer
context.root(Run).model
context.nearest(Model).dim
```

Import Pydantic authoring APIs directly so their native type information remains
visible:

```python
from pydantic import ConfigDict, Field, ValidationInfo, field_validator
```

## Runtime-only properties

The type checker does not prove:

- whether an instance is currently a draft or final;
- whether a requested ancestor exists in a particular validation tree;
- whether a source field has already completed validation;
- fields accessed through an untyped selector such as `context.root()`.

`C.is_draft()` handles lifecycle checks at runtime. Interpolation resolution and
Pydantic validation handle reachability, order, and dynamic access.

Drafts and finals intentionally share one static type. Finals use Pydantic's
`model_config`-level freeze, which basedpyright does not turn into a read-only class;
runtime assignment to a final still fails. Pydantic's mypy plugin applies different
frozen-model rules and is not the supported checker for the draft-mutation idiom.

## Annotation rule

Do not enable `from __future__ import annotations` in config modules. Use normal
eager annotations and quote only a true forward reference whose name is defined
later. This keeps Pydantic schemas reliable for notebook and cloudpickle transport
across the supported Python versions.
