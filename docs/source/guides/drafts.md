# Drafts and finals

`C.draft(ConfigType)` creates a real instance of `ConfigType` without running
field validators, model validators, default factories, or `model_post_init`. It
takes no seed keyword arguments. Assignment is the initial input path; tracked
built-in container mutations can then update an assigned or materialized value.

The [semantic contract](../contract.md) defines the complete lifecycle and
supported value graph.

```python
from pydantic import Field

import nshconfig as C


class Optimizer(C.Config):
    learning_rate: float = 3e-4


class Run(C.Config):
    optimizer: Optimizer
    epochs: int
    tags: set[str] = Field(default_factory=set)
    parameters: dict[str, int] = Field(default_factory=dict)


work = C.draft(Run)
work.epochs = 100
work.optimizer.learning_rate = 1e-4
assert C.is_draft(work)
```

## Draft behavior

A draft supports ordinary assignment and deletion of declared fields. An unknown
attribute fails immediately, with a close-name suggestion when one is available.
Private attributes are intentionally initialized only during final validation, so
private writes and deletions on a draft are rejected instead of being silently lost.

```python
work.epochs = 200
del work.epochs        # restore the class default or missing state

try:
    work.epochs
except C.UnsetError:
    pass

work.epochs = 100
# work.epohcs = 200    # AttributeError, with a suggestion for "epochs"
```

Reading a required scalar before assignment raises `UnsetError`. Reading a pending
interpolation also raises `UnsetError`; derived values become available on the
final. A required field whose annotation is one concrete `Config` subclass
auto-creates as a child draft when accessed. Unions and optional config fields do
not auto-create because the intended type is ambiguous.

Keep a direct `Config` field required or give it an `interp()` default. Class
creation rejects a concrete `Config`, mapping, `None`, or default factory because
each would create the child without an explicit parent interpolation context.

Ordinary class defaults are readable. A default factory may run to provide an
interactive draft value. That value is provisional: if it remains untouched,
`finalize()` omits it and lets Pydantic recompute it from validated inputs. Mutating
a provisional built-in `list`, `dict`, or `set` pins it as user input and records a
mutation event.

Arbitrary non-collection user objects, dataclasses, and plain Pydantic models are
atomic rather than recursively proxied. Assigning one preserves its identity. A draft refuses to
expose one from a provisional default because internal mutation cannot be tracked
and a data-aware factory may need recomputation. Assign it explicitly or read it
after finalization.

```python
work.tags.add("ablation")
work.parameters["layers"] = 24
```

Drafts cannot be serialized. `model_dump()`, `model_dump_json()`, nested Pydantic
serialization, and `TypeAdapter` serialization all reject them. Use trusted
cloudpickle transport when executable draft state must cross a process boundary;
see [transport, security, and environments](transport.md).

Drafts also reject `copy.copy()`, `copy.deepcopy()`, `model_copy()`, the deprecated
`Config.copy()`, and direct `__init__` reentry. These paths could duplicate or
replace values without preserving a truthful composition history.

## Finalization

`C.finalize(work)` recursively collects the supported graph, resolves
interpolation, and asks Pydantic to validate the result. The original draft is not
consumed and can be finalized repeatedly after further edits.

The recursive structural graph contains `Config` nodes and exact built-in `dict`,
`list`, `tuple`, `set`, and `frozenset` values. It must be finite and acyclic.
`TypedDict`, abstract mapping and sequence annotations, unions, and named type
aliases may describe those concrete built-ins. Every structural config position
needs a concrete annotation; hiding a config draft under `Any` or `object` fails
with its graph path. An interpolation marker may occupy one complete config field,
not a nested container position or mapping key.

Dataclasses, ordinary Pydantic models, and arbitrary non-collection user values are
atomic. Pydantic may reconstruct a dataclass according to its schema; ordinary
models and arbitrary objects normally keep input identity. Atomic values cannot hold
`Config` nodes or pending lifecycle values. A model-before validator may normalize
custom input, but any custom
collection left after that hook is rejected even when finite. Lazy synchronous and
asynchronous containers, iterators, generators, awaitables, and coroutines are also
rejected; materialize and await them before validation.

Arbitrary mapping keys are supported only when the corresponding values contain no
`Config` structure. A mapping path containing a config requires exact `str` or
`int` keys. Lifecycle checks inspect accessible Python object state and known
carrier objects, including functions, bound methods, partials, and weak references.
Opaque callables are rejected when their captured state cannot be inspected. Other
truly opaque extension state cannot be proven safe and must not conceal lifecycle
state. Containers internal to an atomic object remain implementation detail rather
than Config structural nodes.

Repeated built-in containers and child drafts are expanded by value. If the same
child draft appears twice, the final contains two equal, independently validated
config values. Arbitrary non-collection user objects remain atomic identity values.

A previously final config can be inserted as a branch only if it has no
interpolation history. Since finals are shallowly frozen, the library cannot prove
that a mutable dependency of an old interpolation is unchanged or recompute the
recipe. Assignment and other non-interpolation provenance is preserved and rebased.
Reading the final or calling `C.explain()` or `C.provenance()` does not affect this
decision.

## Final behavior

Calling a `Config` class normally also creates a validated final. Finals are
ordinary Pydantic models with field rebinding disabled. Pydantic's freeze is
shallow, so contained lists and dictionaries remain normal mutable Python objects.

The instance visible inside `model_post_init` or a model-after validator is still
in-progress. It becomes a final only after model hooks return and lifecycle checks
succeed. Serialization, copying, iteration, `finalize()`, provenance queries,
fingerprinting, and recording reject that in-progress instance.

All configs are unhashable because their allowed values may be mutable or
user-defined. Final equality compares the concrete class and declared values;
provenance does not affect it. Draft equality is identity equality. Use
`C.fingerprint(final)` when deterministic content identity is required.

Finals reject `model_copy(update=...)`, the deprecated `Config.copy()`, and direct
`__init__` reentry. An unchanged final may use `copy.copy()`, `copy.deepcopy()`, or
`model_copy()` without updates.

`C.finalize(final)` revalidates the final's concrete values into a new object. It
preserves `model_fields_set` at every config node and copies provenance, but it does
not execute interpolation callables. Validators run again and must be idempotent on
canonical input. Revalidation rejects a validator that mutates the source final or
changes its values or explicit-field metadata. The structural input is isolated
before validators run, so a failure cannot mutate the source. A final containing an
identity-bearing mutable atomic value is not revalidatable without changing identity;
rebuild it from a draft or mapping.

The internal integrity snapshots used during finalization are stricter than public
Python equality. They detect structural replacement, mutation, and aliasing even
when a user-defined value compares equal; they do not change the equality behavior
of the returned final.

The original draft is the only composition recipe. There is no operation that can
recover interpolation callables or a sequence of Python mutations from a concrete
final or JSON record.
