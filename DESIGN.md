# nshconfig semantic contract

`nshconfig` is a small lifecycle layer over Pydantic. Pydantic owns schemas,
aliases, validation, serialization, and JSON schema generation. `nshconfig` adds
four things: incomplete mutable drafts, Python interpolation, provenance, and
concrete run records.

The design has four distinct states:

1. A `Config` class declares the schema.
2. A draft is mutable Python composition state. It may be incomplete and invalid.
3. A final is the result of one explicit interpolation and validation boundary.
4. A run record is JSON-safe dead data. It preserves a concrete final and its
   provenance, but it is not an executable composition recipe.

These states are intentionally not interchangeable.

During ordinary validation, a `Config` instance also has a short-lived in-progress
state. It is visible to `model_post_init` and model-after validators, but it is
neither a draft nor a final. Public operations that require established draft or
final state, including serialization, copying, iteration, `finalize()`, `explain()`,
`provenance()`, `fingerprint()`, and `record()`, reject it. The final state is
established only after model hooks and lifecycle integrity checks finish
successfully.

## Public interface

The native API is function based:

```python
import nshconfig as C


class Optimizer(C.Config):
    learning_rate: float = 3e-4


class Run(C.Config):
    optimizer: Optimizer


work = C.draft(Run)
work.optimizer.learning_rate = 1e-4
run = C.finalize(work)
```

`nshconfig` does not re-export Pydantic. Applications import `Field`, validators,
and other Pydantic features from `pydantic`.

The lifecycle functions are `draft()`, `finalize()`, and `is_draft()`. Provenance
uses `explain()`, `provenance()`, and `source()`. Reproducibility uses
`fingerprint()`, `record()`, and `load_record()`.

There are no duplicate instance methods, dynamic verb dispatch, global model
configuration, registry, loader, or code generation layer.

## Config classes and finals

Calling a `Config` class normally creates a validated final. A final is a normal
Pydantic model with field assignment disabled. Pydantic's `frozen=True` is shallow:
it prevents field rebinding but does not turn a nested `list` or `dict` into an
immutable collection. The library describes this state as *field-frozen* and does
not claim deep immutability.

All `Config` instances are unhashable. The allowed value graph includes mutable
and user-defined objects, so a universal Python hash would be unsound. Use
`fingerprint()` when a deterministic content identifier is required.

Final equality compares the concrete class and declared field values. Provenance
and lifecycle metadata do not affect equality. Draft equality is identity equality.

Internal lifecycle integrity checks are deliberately separate from public Python
value equality. Finalization uses stricter structural snapshots to detect mutation,
replacement, and aliasing across validation. A lifecycle check may therefore reject
a change even when a user-defined `__eq__` reports equal values. This does not alter
the equality behavior exposed by `Config` itself.

The base class enforces the Pydantic settings required by the lifecycle:

- `extra="forbid"`
- `frozen=True`
- `validate_default=True`
- `revalidate_instances="always"`
- validation by aliases and canonical field names
- `from_attributes=False`

Projects may define a project base class to change policy settings such as
`strict` or `arbitrary_types_allowed`. They may not disable lifecycle invariants.
There is no process-global setter and no import-order-dependent metaclass state.
Per-call and `TypeAdapter` validation cannot opt back into attribute-based input:
`Config` accepts mappings and `Config` instances only.

A field whose direct annotation is one concrete `Config` type must be required or
have an `interp()` default. A concrete `Config`, mapping, `None`, or default factory
would construct the child without an explicit parent interpolation context, so class
creation rejects each form. Required direct `Config` fields still auto-create as
child drafts on access.

Named type aliases are resolved before lifecycle checks, including parameterized
PEP 695 aliases. An alias cannot hide validation-bypass metadata. String
discriminators are supported for tagged unions. Callable union discriminators are
rejected because an incomplete draft cannot run an opaque branch-selection
protocol.

`model_construct()` is not a supported way to create a `Config`.
`model_copy(update=...)` is also rejected: a concrete final no longer retains the
executable recipe needed to recompute dependent interpolation or truthful
provenance. Edit and finalize the original draft, or construct a new final
explicitly. The deprecated `copy()` API and reinitializing an existing instance by
calling its `__init__` are rejected. Drafts cannot be shallow-copied, deep-copied,
or copied with `model_copy()`. Plain shallow and deep copies of an unchanged final,
and `model_copy()` without updates, remain supported.

## Drafts

`draft(ConfigType)` returns a real instance of `ConfigType`, but it deliberately
does not run field validators, model validators, default factories, or
`model_post_init`. It takes no seed keyword arguments. Values enter a draft through
ordinary, statically checked field assignment, which also gives provenance one
unambiguous write path.

A draft supports:

- assignment and deletion of declared fields;
- immediate rejection of unknown attributes;
- automatic creation of required fields whose annotation is one concrete
  `Config` subclass;
- reads of ordinary class defaults;
- tracked mutation of built-in `list`, `dict`, and `set` values;
- repeated, non-destructive calls to `finalize()`.

Reading a required scalar that has not been assigned raises `UnsetError`. Reading
a pending interpolation raises `UnsetError`. A default factory may be evaluated to
support an interactive draft read, but an untouched provisional result is omitted
from finalization so Pydantic recomputes it from validated data. Mutating that result
through a tracked built-in container pins it as draft input and records a mutation
event. Arbitrary non-collection objects, dataclasses, and other Pydantic models are atomic values:
explicit assignment preserves their identity. Draft reads refuse provisional
atomic defaults because the library cannot observe their internal mutations or
know whether a data-aware factory must be recomputed; assign one explicitly or
read it after finalization.

Every serialization path rejects drafts, including `model_dump()`,
`model_dump_json()`, nested Pydantic serialization, and `TypeAdapter`. A draft may
be transported with cloudpickle when its executable Python composition must move
to another process.

The original draft is the sweep recipe. Finalization does not consume it. There is
no `thaw()` operation: a concrete final or JSON document cannot faithfully recover
an instance interpolation callable or the original sequence of Python mutations.

Calling `finalize()` on an existing final is a revalidation operation, not a recipe
replay. It builds a new final from the existing concrete field values, reruns the
normal Pydantic validation pipeline, and preserves the original `model_fields_set`
at every `Config` node plus its provenance. It does not execute interpolation
callables because every field already has a concrete input value. Revalidation uses
an inertly cloned structural input, so even a failing validator cannot mutate the
source final. It rejects any changed canonical value or explicit-field set. A final
containing an identity-bearing mutable atomic value cannot be isolated without
changing its identity and is therefore not revalidatable; rebuild it from a draft or
mapping. Validators must be idempotent on canonical input.

## Supported value graph

The structural graph is finite and acyclic. Its recursive nodes are `Config`
instances and exact built-in `dict`, `list`, `tuple`, `set`, and `frozenset`
containers. `TypedDict`, abstract mapping and sequence annotations, unions, and
named type aliases may describe those concrete built-in values. A model-before
validator may normalize custom input, but any custom collection left after that hook
is rejected even when finite; convert it to an exact supported built-in. Lazy
synchronous or asynchronous values and annotations, including iterators, generators,
iterable streams, async iterables, awaitables, and coroutines, are also rejected;
materialize and await them outside the config.

Dataclasses, ordinary Pydantic models, and arbitrary non-collection user objects are
also atomic rather than becoming nested lifecycle scopes. `nshconfig` does not copy
or expand an atomic object after Pydantic has validated it; Pydantic may itself
reconstruct a dataclass according to its schema. Ordinary Pydantic models and
arbitrary user objects normally retain input identity.
Lifecycle checks inspect accessible state only to reject a concealed draft,
interpolation marker, or `Config` node; they do not collect or validate a nested
`Config` through that object. Move such structure into a directly annotated
`Config` field or an exact built-in container. The inspection covers Python
`__dict__` and `__slots__` state plus known carriers such as functions, bound
methods, `functools.partial`, weak references, properties, built-in operator
callables, and class namespaces. An opaque callable whose captured state cannot be
inspected is rejected. Other truly opaque extension state cannot be proven safe and
must not conceal lifecycle values. Internal containers and cycles inside an atomic
object remain its implementation detail and do not inherit Config's structural
container or alias policy. Run
records generally reject atomic values unless Pydantic can serialize and reconstruct
them without changing their exact runtime meaning.

Arbitrary mapping keys are supported when their values do not contain structural
`Config` state. A mapping path that does contain `Config` structure requires each
key on that path to have exact type `str` or `int`, so the path is deterministic
and cannot execute user equality or rendering behavior. Interpolation through an
arbitrary key is supported when no `Config` lies below it; provenance records the
whole mapping as a conservative dependency because the key has no canonical path.

Repeated built-in containers and `Config` recipes are expanded by value while
entering validation. If a validator itself publishes the same mutable structural
value at two final paths, validation rejects the alias rather than letting one
mutation change two fields. Atomic user objects retain identity. Cycles fail with
the path at which they were found.

The annotation must describe every structural `Config` position. A `Config` draft
hidden under `Any`, `object`, or another opaque annotation is rejected with a path
and a request for a concrete annotation. Without that schema, Pydantic cannot
validate the node or give it the correct parent interpolation context.

Sets and frozensets may contain ordinary hashable config values, but not `Config`
instances because every `Config` is intentionally unhashable.

An `interp()` marker is legal only as the complete value or default of a declared
`Config` field. It is not legal as a mapping key, a set element, a nested container
element, or a value hidden inside an opaque object. Nested `Config` objects inside
containers may still declare interpolated fields of their own.

Finalization recursively collects the same graph that provenance and pending-value
checks traverse. No final may contain a draft or interpolation marker in any
supported structural position.

A final `Config` may be reused as a branch only when its history contains no
interpolation event. This is conservative by design: finals are only shallowly
field-frozen, so a nested mutable dependency may have changed and the library no
longer has the recipe needed to prove that a previously interpolated value is
fresh. Non-interpolation provenance is preserved and rebased when a final branch is
reused. Read-only observation, including ordinary field access, `explain()`, and
`provenance()`, does not change reuse eligibility.

## Interpolation and validation order

`interp()` takes a Python callable. The callable receives a `Context` with typed
`current()`, `parent()`, `root()`, and `nearest()` selectors. There is no string
expression language. A context and every view derived from it are capabilities for
one live resolver invocation on its originating thread. They expire when the
callable returns and cannot cross a thread boundary. Capture concrete values first
if downstream computation needs worker threads.

Pydantic validates fields in declaration order. That order is also the dependency
order for interpolation. A context read can observe:

- an earlier field on the same model after all of that field's aliases,
  constraints, and field validators have run;
- a validated field on an ancestor;
- a completed `Config` branch that appears earlier on an ancestor.

A context read cannot observe a later field or the ancestor field whose nested
model is still being built. Such a read raises a structured validation error that
names the unavailable path and tells the author to declare the source before the
dependent field. This rule prevents raw and validated values from being mixed.
Use `parent()` or `nearest()` to read the current branch from inside a descendant.

For each field, the observable order is:

1. model-before validators may normalize the native Pydantic input;
2. interpolation resolves the field value, if present;
3. Pydantic validates the resolved value and runs all field validators;
4. the canonical result becomes visible to later interpolation;
5. after every field is complete, `model_post_init` and model-after validators run.

The object passed to `model_post_init` and model-after validators remains
in-progress. It becomes a final only after those hooks return and the lifecycle
verifies their output.

Both source and target field validators run exactly once. Normalization that must
feed interpolation belongs in a field validator, because model-after validators run
after all field dependencies have already been evaluated.

The lifecycle supports field validators in `before` and `after` mode and model
validators in `before` and `after` mode. A model `wrap` validator may call the
validation handler zero or multiple times, and field `plain` and `wrap` validators
may bypass or repeat canonical field validation, so class creation rejects those
modes. Pydantic's deprecated `validator` and `root_validator` decorators are also
rejected. These restrictions keep one observable validation pass per field.

As in native Pydantic, a model-before validator must accept `Any`. Its input is
usually a mapping, but instance revalidation and draft finalization may provide an
existing model or an internal revalidation carrier. It may normalize legacy names
or a custom collection into the supported representation. After the hook, every
structural container must be an exact finite built-in `dict`, `list`, `tuple`,
`set`, or `frozenset`. A validator may not silently discard a provided declared
field and substitute a default. Field validators likewise may not use
`PydanticUseDefault` to replace explicit input. `nshconfig` reports either case as a
structured validation error. A model-before validator must inspect the input shape
instead of assuming `dict`.

An explicit input value always overrides an interpolated class default. Deleting
that field from a draft reactivates the class default. Interpolation callables should
be deterministic and free of side effects.

A completed nested `Config` is exposed to interpolation through a read-only,
declared-field-only view. Field reads remain typed and create precise dependency
edges, but Pydantic methods, private state, undeclared attributes, custom access
hooks, and the view's backing state are not available. Slicing, concatenating,
repeating, or merging a published container keeps structured elements protected and
retains truthful whole-container and element-origin dependencies. Returning a view
materializes an independent value graph instead of leaking a proxy or alias to the
source.

`Literal` fields, including singleton literals, follow the same interpolation
rules. The resolved value still passes through Pydantic's native literal schema,
so an invalid result is a normal `literal_error`. A literal used as a discriminated
union tag is different at the containing union boundary: that tag must already be
concrete because Pydantic has to select a branch before it can validate the selected
model. Interpolating an ordinary literal field is supported; interpolating the tag
that performs outer union selection is rejected with a discriminator error.

## Provenance

Provenance events are immutable plain data in chronological order. Assignment,
deletion, built-in container mutation, and interpolation each create an event.
Source file, line, function, source text, and an optional `source()` label are
best-effort metadata. Values are rendered with a bounded, exception-safe function;
built-in container rendering is deterministic, and provenance never retains the
live value. Rendered text is presentation data rather than an integrity check.

Interpolation events record the canonical paths and values read by the callable.
Paths always use field names, never aliases. `explain()` follows those dependency
edges to show both the direct interpolation and the assignments or defaults it
depended on. `provenance()` returns copies with immutable event sequences.

Interpolation events also carry SHA-256 tokens produced by a versioned structural
encoding of the result and every read. A run record verifies those tokens against
the restored graph and normalizes the bounded display strings; it never treats a
display string as proof. Recording rejects interpolation provenance with an opaque
value that has no process-independent structural token. It also requires an
interpolation event to be the final event for its field, because a later event would
make the recorded dependency claim stale. Loading also rejects self, later-field,
and incomplete-branch dependencies that could not have been observed under
declaration-order publication.

A failed write cannot leave a changed field without its event. A failing value
`repr` is represented as an unavailable rendering and never aborts the write.

## Fingerprints and run records

`fingerprint(final)` returns a versioned SHA-256 digest over the run-record format,
concrete config identity, schema fingerprint, exact-runtime semantic fingerprint,
and deterministic JSON-mode values. The semantic fingerprint is a versioned
structural token for the complete validated runtime graph, including state that
canonical JSON may not distinguish. The value fingerprint binds the canonical JSON
values to both contract fingerprints. Provenance is excluded.

The schema fingerprint covers presentation-stripped canonical and alias forms of
both validation and serialization JSON Schema. It also contains a manifest of every
reachable nested Pydantic model's field aliases and every reachable nested
`Config`'s record identity, including its `record_schema_id`. This prevents alias or
nested identity changes from sharing a schema fingerprint even when Pydantic's
rendered schema would otherwise omit the distinction.

The schema fingerprint is a schema contract guard, not a hash of Python validator,
serializer, or interpolation implementation. The default logical type identity is
`module:qualname`. A generated Pydantic generic parameterization can have a colliding
generated name, so the exact generated class must declare a non-empty, distinct
`record_schema_id`. A uniquely named concrete subclass already has a stable
`module:qualname` and does not require an ID. Either kind may declare an ID to
version behavior that schema output cannot express. The ID is appended to
`module:qualname`; bump it for incompatible custom validator or serializer behavior
that does not change the schema fingerprint.

Dictionary insertion order is part of the recorded value. Run-record serialization
preserves that order and the fingerprint changes when it changes. Sets and
frozensets instead use a deterministic sorted JSON representation because they
have no insertion-order contract. Tools that sort JSON object members therefore
change the meaning of an order-sensitive record and cause fingerprint verification
to fail.

Before returning a digest, the library validates the values back into the same
concrete type, requires every stored field to be consumed, and checks that the
reconstructed runtime semantics and JSON values match exactly. It rejects drafts,
non-finite floats, cycles, lossy or unstable serializers, and values that cannot be
converted and reconstructed deterministically; it never falls back to `repr`.
Field-level Pydantic exclusions (`exclude=True` or `exclude_if`) are rejected on
every nested model because a record must not silently omit declared state, and
dynamically overriding an explicit exclusion would make serialization execute a
schema different from the application's real model.

`record(final)` creates this envelope:

```json
{
  "format": "nshconfig.run-record.v4",
  "config_type": "package.module:Run",
  "schema_fingerprint": "sha256:...",
  "semantic_fingerprint": "sha256:...",
  "fingerprint": "sha256:...",
  "values": {},
  "provenance": {}
}
```

`load_record(Run, value)` verifies the v4 format, concrete type, schema fingerprint,
semantic fingerprint, and value fingerprint. It validates the stored values,
requires the reconstructed exact runtime graph to match the semantic fingerprint,
then restores verified provenance. It does not execute interpolation. Every stored
value is explicit input during record loading, so Pydantic's `model_fields_set` is
not composition metadata preserved by the record. Provenance must be self-contained
under the recorded `Config` root: a detached branch whose interpolation reads
outside that branch cannot form a run record on its own. Record the containing root
instead. A run record captures what ran, not how Python composed it. Record loading
uses a private capability that model validators cannot inspect or downgrade. If a
model-before validator removes a stored field, validation fails before its default
or default factory can run.

Fingerprinting and recording are not atomic snapshots of a concurrently mutated
final. The implementation compares structural snapshots around serialization,
serializes twice, and compares the final again after provenance capture; an observed
change raises `FingerprintError`. Concurrent mutation is nevertheless unsupported:
callers must synchronize access rather than relying on detection as a lock.

## Transport

Importing `nshconfig` does not change global pickle or copyreg behavior.
Importable classes use normal pickle-by-reference semantics. Notebook and local
classes may use cloudpickle-by-value transport. The sender and receiver need the
same Python version and compatible installed dependencies, including packages used
by annotations, validators, and interpolation callables.

Cloudpickle payloads are trusted, ephemeral executable transport. They are not
long-term run records and must never be loaded from an untrusted source.

Classes intended for by-value transport must follow this repository's annotation
rule: do not enable PEP 563 with `from __future__ import annotations`. Forward
references are quoted explicitly. This keeps Pydantic's compiled schema transport
reliable across Python 3.10 through 3.14 without a process-global reducer shim.
