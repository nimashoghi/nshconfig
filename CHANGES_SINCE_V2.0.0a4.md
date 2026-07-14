# Changes from v2.0.0a4 to the semantic-core redesign

## Comparison boundary

This document compares two exact repository states:

| Role | Version | Git object |
|---|---|---|
| Previous alpha | `2.0.0a4` | tag `v2.0.0a4`, commit `1f53da260c958832a55f4bccedd2210e314fef37` |
| Redesign implementation | `2.0.0a5` (staged) | commit `acb28968bc2be13544a8970999c46d18e7a456c9` |
| Public redesign release | `2.1.0a0` | tag `v2.1.0a0` |

The assumption in the question is therefore correct: `v2.0.0a4` is the direct
parent of the redesign commit. This is not a comparison with the 0.x series or with
the earlier v2 alphas.

The implementation range contains one deliberately breaking commit and, before
adding this document, had the following exact tree diff:

```text
79 files changed
34 files added
39 files modified
6 files removed
17,921 insertions
4,142 deletions
```

The comparison can be reproduced with:

```bash
git diff --stat v2.0.0a4 acb2896
git diff --name-status v2.0.0a4 acb2896
git show v2.0.0a4:<path>   # inspect an old file
git show acb2896:<path>    # inspect the redesigned file
```

The normative description of the new behavior is [DESIGN.md](DESIGN.md). The exact
new public signatures are in [the API reference](docs/source/api.md).

## Summary

The previous alpha was a Pydantic model with a draft flag, a set of convenience
verbs, pre-validation interpolation, and display-oriented provenance. The redesign
turns it into an explicit configuration lifecycle:

1. A `Config` class declares a schema.
2. A draft is incomplete, mutable Python composition state.
3. A final is a completely validated, field-frozen Pydantic model.
4. A `RunRecord` is inert JSON data describing exactly what ran.

There is also a private in-progress state while Pydantic validates a model. That
state prevents model hooks from treating a partially checked object as a final.

The largest semantic change is interpolation. In `2.0.0a4`, interpolation ran over
the raw input mapping before Pydantic validated any field. It could read later
fields, unvalidated aliases, raw sibling branches, and values that field validators
had not normalized. In the redesign, field declaration order is dependency order.
Interpolation reads only values that have completed their Pydantic field pipeline.

The other major addition is durable run identity. `model_dump_json()` remains normal
Pydantic serialization, but it is no longer described as a run record. The new
`fingerprint()`, `record()`, and `load_record()` APIs bind concrete type identity,
alias-aware schema identity, exact runtime meaning, deterministic JSON values, and
verifiable interpolation provenance.

No compatibility aliases or deprecation shims were added.

## Quick replacement table

| v2.0.0a4 | Redesign |
|---|---|
| `Run.config_draft()` | `C.draft(Run)` |
| `Run.config_draft(x=1)` | `work = C.draft(Run); work.x = 1` |
| `work.config_finalize()` | `C.finalize(work)` |
| `final.config_explain(path)` | `C.explain(final, path)` |
| `final.config_provenance()` | `C.provenance(final)` |
| `value.config_is_draft` | `C.is_draft(value)` |
| `C.Ctx` | `C.Context` |
| `context.self()` | `context.current()` |
| `C.thaw(final)` | Keep and edit the original draft recipe |
| `C.set_model_config_defaults(...)` | Define a project `Config` base with `model_config` |
| `C.Field`, `C.field_validator`, and other re-exports | Import directly from `pydantic` |
| `hash(final)` | `C.fingerprint(final)` for deterministic content identity |
| `final.model_dump_json()` used as a run archive | `C.record(final).to_json()` |
| Public `C.Interp` implementation type | Use only the `C.interp(...)` constructor |

For example:

```python
# v2.0.0a4
import nshconfig as C

work = Run.config_draft(batch_size=32)
final = work.config_finalize()
print(final.config_explain("model.dim"))
```

```python
# redesign
import nshconfig as C

work = C.draft(Run)
work.batch_size = 32
final = C.finalize(work)
print(C.explain(final, "model.dim"))
```

## Public API

### The export surface is library-owned now

`v2.0.0a4` declared 150 names in `nshconfig.__all__`:

- 134 Pydantic re-exports;
- 16 nshconfig-owned names.

The redesign declares exactly 20 names:

```text
Config
Context
DraftError
Event
Explanation
FingerprintError
RecordError
RunRecord
UnsetError
__version__
draft
explain
finalize
fingerprint
interp
is_draft
load_record
provenance
record
source
```

Across the two explicit lists, 138 names were removed, 8 were added, and 12 were
retained.

All 134 Pydantic re-exports were removed. This includes `Field`, `ConfigDict`,
`BaseModel`, validators, serializers, constrained types, URL types, and Pydantic
exceptions. Pydantic continues to own those APIs; applications import them from
`pydantic`.

The four removed nshconfig-native exports are:

- `Ctx`, replaced by `Context`;
- `Interp`, whose runtime marker type is now private;
- `set_model_config_defaults`, replaced by normal subclass configuration;
- `thaw`, removed because a final cannot recover the original composition recipe.

The eight added exports are:

- `Context`;
- `draft`;
- `fingerprint`;
- `record`;
- `load_record`;
- `RunRecord`;
- `FingerprintError`;
- `RecordError`.

The dynamic `config_*` instance verb family was also removed. The old
`config_finalize`, `config_thaw`, `config_explain`, `config_provenance`, and
`config_is_draft` attributes no longer exist. The functions in `nshconfig.__all__`
are the only lifecycle surface.

### Exceptions

`DraftError` and `UnsetError` remain. Two public errors were added:

- `FingerprintError(ValueError)` means a final cannot be represented as exact,
  deterministic record data.
- `RecordError(ValueError)` means a record is malformed, does not match the
  requested type or schema, or cannot be verified.

Pydantic `ValidationError` remains the error at compiled field and model validation
boundaries. The redesign adds stable custom error categories for lifecycle failures
such as unavailable interpolation reads, draft input, structural cycles, invalid
aliases, revalidation changes, and model-hook mutation.

## Lifecycle and Pydantic integration

### State is explicit

The old implementation stored `__nshconfig_draft__ = True` in an instance
`__dict__`. Anything without that flag was treated as a final. A partially built
model inside `model_post_init` or a model-after validator was therefore
indistinguishable from a completed final.

The redesign stores explicit draft and final state and recognizes a third,
short-lived in-progress state during validation. In-progress instances are not
drafts, but serialization, copying, iteration, `finalize()`, `explain()`,
`provenance()`, `fingerprint()`, and `record()` reject them. Final state is published
only after model hooks return and lifecycle integrity checks succeed.

Normal `ConfigType(...)` and `ConfigType.model_validate(...)` still create validated
finals. Drafts are created only by `draft(ConfigType)`.

### Model configuration is local rather than process-global

`v2.0.0a4` had `set_model_config_defaults()` and a custom metaclass. Calling the
setter changed process-global defaults for classes defined later, so import order
could affect class behavior. It could also override settings needed by the
lifecycle.

The setter and custom default-injecting metaclass are gone. A project changes policy
with a normal project base class:

```python
from pydantic import ConfigDict

import nshconfig as C


class ProjectConfig(C.Config):
    model_config = ConfigDict(strict=False, arbitrary_types_allowed=True)
```

The base still defaults to `strict=True`, but strictness is project policy. The
following lifecycle settings are mandatory and are rechecked when schemas are
built or rebuilt:

```text
extra="forbid"
frozen=True
validate_default=True
revalidate_instances="always"
validate_by_alias=True
validate_by_name=True
from_attributes=False
```

This means both aliases and canonical field names are valid input forms. Per-call
`extra=` or `from_attributes=True` cannot bypass the compiled contract. Config
validation accepts mappings and `Config` instances, not arbitrary attribute-based
objects.

### Class definitions fail early when they break the lifecycle

The redesign rejects the following at class creation or schema rebuild:

- a `RootModel`-style `Config`;
- overrides of assignment, equality, validation, serialization, copying, schema,
  or field-set methods owned by the lifecycle;
- fields that shadow those lifecycle methods;
- private names reserved for lifecycle state;
- model validators in `wrap` mode;
- field validators in `plain` or `wrap` mode;
- deprecated `validator` and `root_validator` decorators;
- `SkipValidation`, `InstanceOf`, `PlainValidator`, `WrapValidator`, and
  `OnErrorOmit`, even when hidden in a named alias;
- callable union discriminators;
- lazy or unsupported container annotations;
- `validate_default=False` on a field;
- a direct concrete `Config` field with a concrete default, mapping default,
  `None`, or default factory.

A direct field such as `child: Child` must be required or have an `interp()`
default. This ensures the child is always built with an explicit parent validation
context. `v2.0.0a4` did not enforce these class-level restrictions.

## Drafts

### Creation has one input path

The old `ConfigType.config_draft(**values)` called Pydantic `model_construct()`.
That materialized ordinary defaults, ran default factories, could invoke
`model_post_init`, and accepted seed values through an untyped keyword mapping.
Unknown seeds could also inherit `model_construct()` behavior rather than the
normal `extra="forbid"` path.

The new `draft(ConfigType)` takes no seed keywords. It allocates the declared type
without running:

- field validators;
- model validators;
- default factories;
- private factories;
- `model_post_init`.

Values enter through ordinary declared-field assignment. This keeps assignment
visible to static checking, rejects typos immediately, and gives provenance one
unambiguous write boundary.

### Preserved draft behavior

The following behavior remains:

- a draft is a real instance of its declared `Config` type;
- assignment is intentionally unvalidated until finalization;
- required direct `Config` fields auto-create child drafts on access;
- required scalar reads raise `UnsetError`;
- deleting a field reactivates its schema default or missing state;
- finalization is non-destructive and may be repeated on the same draft.

### Defaults and default factories are now deliberate

Defaults are no longer eagerly installed by `model_construct()`.

- An ordinary static default may be read from a draft.
- A default factory may be evaluated for an interactive read, but its result is
  provisional.
- If a provisional factory result is untouched, finalization omits it and lets
  Pydantic recompute it from validated data.
- Mutating a provisional built-in container pins it as explicit draft input.
- A provisional atomic object cannot be read because the library cannot observe
  its internal mutation or know whether a data-aware factory should be recomputed.
  Assign it explicitly or read it after finalization.

This fixes a semantic problem in the old `model_construct()` path, where a factory
result created during draft construction could be forwarded as if it were final
input.

### Container mutation is tracked

The redesign wraps exact built-in draft `list`, `dict`, and `set` values with
tracking implementations. Their normal mutating methods:

- record a `mutate` provenance event;
- mark a provisional default as user input;
- preserve enough shadow state to detect mutation that bypassed the tracked method.

If code deliberately calls a base built-in method to bypass tracking, finalization
fails instead of publishing an unrecorded mutation. `v2.0.0a4` recorded assignment
and deletion, but not mutations inside a field value.

Structural built-ins are copied into draft-owned tracked values. Arbitrary atomic
objects retain their input identity.

### Other draft boundary changes

- Private assignment and deletion are rejected. In the old design a private draft
  write could be accepted and then silently omitted by final collection.
- `model_fields_set` and deprecated `__fields_set__` return detached sets, so a
  caller cannot mutate lifecycle bookkeeping through a public property.
- Draft iteration, shallow copy, deep copy, and `model_copy()` are rejected.
- An assigned interpolation marker is held in pending state. Reading an assigned or
  default marker raises `UnsetError` rather than exposing the marker object.
- Every direct and schema-level serialization route rejects a draft. This includes
  nested Pydantic serialization and `TypeAdapter`, which could bypass the two direct
  dump-method guards in `v2.0.0a4`.

## Finalization, reuse, equality, and copying

### `finalize(final)` changed meaning

In `v2.0.0a4`, `finalize(value)` returned any non-draft unchanged. For a final, it
returned the identical object and ran no checks. For an unrelated object, it also
returned that object unchanged.

The redesign requires a `Config`. For a draft it performs the normal interpolation
and validation boundary. For an existing final it performs transactional
revalidation:

1. Clone the exact built-in structural graph without invoking user copy hooks.
2. Re-run the normal Pydantic validation pipeline on concrete values.
3. Do not execute interpolation because every field is explicit input.
4. Preserve provenance and `model_fields_set` at every `Config` node.
5. Require every canonical value and explicit-field set to remain unchanged.
6. Return a fresh final.

Validators must therefore be idempotent on canonical input. A failing validator
cannot mutate the source final, including on exception paths. If an identity-bearing
mutable atomic value cannot be isolated without changing its meaning, revalidation
is rejected and the caller must rebuild from a draft or mapping.

### Reusing a final branch is conservative

`v2.0.0a4` normally reused a nested final directly under Pydantic's default
instance policy. A branch could therefore carry an interpolation result derived in
another parent context.

The redesign always revalidates instances. A final branch with no interpolation
history can be reused, and its ordinary provenance is rebased. A branch with any
interpolation history is rejected because shallowly mutable dependencies may have
changed and the executable recipe is no longer present. Reuse the original draft
instead.

Calling `finalize()` on the root final remains supported because that checks the
same historical object rather than inserting the branch into a new context.

### `thaw()` was removed

The old `thaw(final)` created a draft from fields marked explicit and omitted
defaulted or interpolated fields. That was useful ergonomically, but it could not
recover instance interpolation callables or the original sequence of Python
mutations. The redesign treats the original draft as the only faithful recipe.

### Freeze, equality, and hashing

Both versions use shallow Pydantic freezing: declared fields cannot be rebound or
deleted, but a nested list or dictionary remains mutable.

The redesign changes the surrounding semantics:

- subclasses cannot disable `frozen=True`;
- finals are consistently described as field-frozen, not deeply immutable;
- final equality compares concrete class and declared field values;
- provenance and lifecycle metadata remain excluded from final equality;
- two distinct drafts are never equal, even when their stored fields match;
- a draft and final are never equal;
- hostile or array-like field equality produces `False` rather than escaping with
  an exception or ambiguous truth value;
- every `Config` is unhashable.

`v2.0.0a4` implemented a Python hash over concrete type and field values. It was
available only when the values happened to be hashable and was unsound for a graph
that permits shallow mutation. The replacement for durable content identity is
`fingerprint(final)`.

### Unsafe Pydantic construction and copying are closed

- `Config.model_construct()` always raises.
- `Config.copy()` always raises.
- `model_copy()` without an update remains available for finals.
- `model_copy(deep=True)` remains available for finals.
- Any non-`None` update, including `{}`, is rejected because it would not recompute
  interpolation or truthful provenance.
- `copy.copy()` and `copy.deepcopy()` support finals and reject drafts.
- Re-entering `__init__` on an existing instance is rejected.

## Interpolation

### Resolution moved inside the field pipeline

The old `interpolation_scope()` was a model wrap validator. It iterated over the raw
input mapping, resolved every direct marker, and only then called Pydantic's native
handler. Its context views therefore exposed raw composition input.

The old tests deliberately allowed:

- an earlier field to read a later field;
- siblings to read each other in either declaration direction when raw input was
  present;
- a resolver to descend into its own raw child mapping;
- default factories and defaults to be observed before canonical validation;
- a source value to be read before its field validators ran.

The redesign publishes one canonical field at a time:

1. Model-before validators normalize the native Pydantic input.
2. The next field's interpolation marker resolves, if present.
3. Pydantic validates the result and runs that field's validators.
4. The completed canonical value becomes visible.
5. After all fields complete, `model_post_init` and model-after validators run.

Interpolation may now read:

- an earlier field on the current model after aliases, constraints, and before/after
  field validators;
- a validated field on an ancestor;
- a completed branch declared earlier on an ancestor;
- an already published field along the active direct `Config` branch.

It may not read:

- a later field on the current model;
- a later sibling branch;
- a whole branch or container that is still being built;
- the ancestor field whose nested model is currently under validation.

These reads fail with a structured Pydantic error on the dependent field. This makes
class declaration order the explicit dependency order and prevents raw and
validated values from being mixed.

Both source and target field validators run exactly once. A source needed by later
interpolation should be normalized in a field validator. A model-after hook that
changes an already published value is rejected rather than leaving a stale derived
field.

### Context API and capability lifetime

The public context type changed from `Ctx` to `Context`, and `self()` was renamed to
`current()`:

```text
current()
parent()
parent(levels)
root()
nearest(ConfigType)
```

Passing a `Config` class gives typed field access and a runtime class check. Typed
selectors no longer accept arbitrary `BaseModel` classes. Boolean values are also
rejected as parent hop counts rather than being accepted as integers.

`Context()` cannot be constructed by application code. A context and every view
derived from it are capabilities for one resolver call on the thread that entered
that call. They expire when the resolver returns and reject cross-thread use. A
`model_validate()` call made inside a resolver starts a separate validation root.

Typed selectors are statically typed as the requested `Config`, but the runtime
value is a restricted view, not the real model instance. Declared fields are
available; Pydantic methods, private state, undeclared attributes, custom access
hooks, and backing validation frames are not.

### Container reads are protected and recorded

The old `_View` returned raw lists, mappings, user objects, and nested values. Its
own backing attributes were accessible. Provenance generally captured only a dotted
path and a truncated `repr`, not sequence shape, order, membership, or the origin of
elements after a container operation.

The redesign adds operation-aware list, tuple, and mapping views:

- direct canonical element access records the element path;
- iteration records sequence shape and element reads;
- negative indices also depend on sequence shape;
- slicing, concatenation, repetition, and mapping merge retain whole-container and
  element-origin dependencies;
- structured elements remain protected after those operations;
- returning a view materializes an independent value graph rather than leaking a
  proxy or alias to the source;
- arbitrary mapping keys are supported when no `Config` lies below them, with a
  conservative whole-mapping dependency.

This is a strong API boundary, not a Python sandbox or a universal deep membrane.
Sets, frozensets, dataclasses, and arbitrary atomic values are published after a
whole-value read rather than wrapped at every method. Resolver code is trusted
Python and may still perform I/O, import modules, or deliberately call methods on an
atomic value.

### Aliases, literals, and marker placement

The old pre-validation lookup used only the canonical field name. An alias-supplied
marker could remain unresolved, while a canonical context read could miss an
alias-supplied source and observe its default.

The redesign handles canonical names, aliases, `AliasChoices`, and `AliasPath`
before resolving a field. Both canonical and alias forms are accepted, but supplying
the same field through more than one input candidate is rejected. Provenance and
records always use canonical field paths; the original alias spelling is not stored.

An explicit target value still overrides an interpolated class default. Deleting
the draft field reactivates the default marker.

An interpolation marker remains valid as the complete value or default of a
declared `Config` field, including a complete field supplied to `model_validate()`.
It is not valid inside a list, as a mapping key, as a set element, or hidden in an
opaque object. Nested `Config` objects inside containers may still define their own
interpolated fields.

Ordinary `Literal` fields may be interpolated and then pass through native literal
validation. A literal that selects an outer discriminated-union branch must be
concrete before branch selection. String discriminators are supported; callable
discriminators are rejected.

## Structural value graph

`v2.0.0a4` recursively collected only directly annotated draft-valued fields. Its
final pending-value scan walked BaseModels and common containers, but it did not
define a complete annotation-aware graph, guard every cycle, or inspect concealed
lifecycle values in ordinary object state.

The redesign defines the recursive structural graph as:

- `Config` instances;
- exact built-in `dict`;
- exact built-in `list`;
- exact built-in `tuple`;
- exact built-in `set`;
- exact built-in `frozenset`.

Annotations may describe those concrete values through `TypedDict`, abstract
mapping and sequence annotations, unions, string-discriminated unions, and named or
parameterized type aliases.

The enforced rules are:

- The graph must be finite and acyclic.
- Every structural `Config` position must have a compatible concrete annotation.
- A `Config` or draft hidden under `Any`, `object`, or another opaque position is
  rejected.
- A marker may occupy only a complete declared `Config` field.
- Lazy synchronous or asynchronous values are rejected, including iterators,
  generators, async iterables, awaitables, and coroutines.
- A model-before validator may normalize a custom collection, but any custom
  collection left afterward is rejected.
- Repeated input recipes and built-in containers are expanded by value.
- If a validator publishes the same mutable structural object at two final paths,
  the alias is rejected.
- Duplicate `Config` lineage published at multiple paths is rejected.
- Mapping paths that contain `Config` structure require exact `str` or `int` keys.
- Cycles fail with the path of the repeated node.
- Recursive required `Config` spines that cannot terminate are rejected.
- Sets and frozensets cannot contain `Config` because all `Config` values are
  unhashable.

Dataclasses, ordinary Pydantic models, and non-collection user objects are atomic
lifecycle values. Their internal containers, aliases, and cycles do not inherit the
structural graph policy. Input identity is normally preserved for ordinary Pydantic
models and arbitrary objects; Pydantic may reconstruct a dataclass according to its
own schema.

Accessible atomic state is inspected only to prevent a hidden draft, marker, or
`Config`. Inspection covers `__dict__`, `__slots__`, functions and closures, bound
methods, partials, weak references, properties, descriptors, built-in operator
callables, and class namespaces. An opaque callable with uninspectable captured
state is rejected.

Atomic does not mean immutable, side-effect-free, JSON-safe, or recordable. Truly
opaque non-callable extension state cannot be proven clean and remains a caller
contract. A valid final may therefore still fail `fingerprint()` or `record()`.

## Provenance and explanations

### Event schema

`Event` remains immutable plain data, but its public schema changed:

| v2.0.0a4 | Redesign |
|---|---|
| kinds `set`, `del`, `seed`, `interp` | kinds `set`, `delete`, `mutate`, `interpolate` |
| `func` | `function` |
| `injected` | `from_default` |
| no mutation operation | `operation` describes a built-in mutation |
| display-only result and read values | adds `value_token` and `read_tokens` |
| keyword draft seeds create `seed` | draft seeding and `seed` are removed |

`provenance()` now returns `dict[str, tuple[Event, ...]]` rather than lists. The
mapping is detached and event sequences are immutable.

Source file, line, function, code, and `source()` labels remain best-effort
presentation metadata. The redesign uses a bounded, exception-safe renderer, so a
hostile `repr()` cannot abort a successful write. Built-in set and frozenset display
is deterministic. Persisted provenance may contain local file paths and source
snippets and should be handled accordingly.

`source(label)` now requires a non-empty string.

### Canonical paths and `explain()`

Old provenance paths were dot-separated declared fields and collection recursed
only through direct BaseModels. The redesign uses canonical field names plus integer
and string subscripts. It follows models inside supported mappings and sequences and
tracks origin paths when validation reorders or reconstructs a container.

`Explanation` changed from a mutable dataclass with `events: list` and
`default_note` to a frozen dataclass with:

```text
path
current
events: tuple[Event, ...]
origin
causes: tuple[Explanation, ...]
```

`explain()` now recursively follows interpolation reads into `causes`. It can
explain a declared field or a value below the nearest field carrying provenance.
Ordinary defaults are described by `origin` rather than being synthesized as write
events.

### Integrity information

Each interpolation event now records:

- the canonical paths and bounded display values it read;
- a versioned SHA-256 structural token for the result;
- one structural token per read;
- whether the marker came from a default.

Run-record creation and loading verify those tokens against the concrete graph. They
also require interpolation to be the last event for its field and reject self,
later-field, incomplete-branch, duplicate, stale, or out-of-root reads.

Only interpolation dependency claims are value-token-verified. Historical
set/delete/mutate source metadata is shape-checked, but it cannot prove that a past
Python operation occurred. Provenance is also excluded from the main value
fingerprint.

## Fingerprints and run records

### These APIs did not exist in v2.0.0a4

The previous alpha had no `fingerprint`, schema fingerprint, semantic fingerprint,
`RunRecord`, `record()`, or `load_record()`. Its README called
`model_dump_json()` the run record, but that output contained only serialized field
values. It did not bind a type, schema, runtime meaning, or provenance.

The `nshconfig.run-record.v4` name is the format selected during the redesign. The
baseline alpha did not ship run-record v1 through v3 APIs.

### Value fingerprint

`fingerprint(final)` returns `sha256:<64 lowercase hex digits>`. It hashes:

1. the run-record format;
2. the concrete config identity;
3. the schema fingerprint;
4. the exact-runtime semantic fingerprint;
5. deterministic JSON-mode declared field values.

Provenance is intentionally excluded, so two finals with the same concrete runtime
graph but different assignment history have the same fingerprint. Different config
types remain different even when their field names and values match.

Before returning a digest, the library:

- serializes twice;
- validates the stored values back into the same exact config type;
- requires every declared value to be consumed;
- compares the reconstructed runtime graph, including state JSON cannot distinguish;
- compares JSON output again;
- checks structural snapshots around serialization and provenance capture.

It rejects drafts, cycles, non-finite floats, non-string JSON object keys, excluded
declared fields, lossy or unstable serializers, concurrent observed changes, and
values that cannot round-trip exactly. There is no `repr()` fallback.

Dictionary insertion order is part of recorded meaning and changes the fingerprint.
Sets and frozensets have no insertion-order contract and are sorted
deterministically. A generic JSON tool that sorts object members changes an
order-sensitive record.

### Schema and runtime identity

The schema fingerprint includes presentation-stripped canonical and alias variants
of both validation and serialization JSON Schema. Its type manifest also includes:

- aliases and alias priority for every reachable nested Pydantic model;
- logical identity and `record_schema_id` for every reachable nested `Config`;
- recursively compiled `TypedDict` identity, requiredness, totality, aliases,
  serialization exclusion, strictness, and extra-field behavior.

Conditional `exclude_if` behavior is not recordable.

The logical config identity defaults to `module:qualname`. A generated generic
specialization may collide with another generated class, so it must declare a
distinct stable `record_schema_id`. A uniquely named concrete subclass already has
a stable identity. Either may declare an ID to version behavior not visible in JSON
Schema; the identity then becomes `module:qualname#record_schema_id`.

The schema fingerprint is not a hash of executable Python. It does not automatically
detect a validator, serializer, interpolation callable, external resource, or class
implementation change that leaves the represented schemas unchanged. A project
must bump `record_schema_id` for incompatible behavior outside the schema contract.

The semantic fingerprint complements schema and JSON identity with a versioned
structural token for the complete validated runtime graph. It preserves exact runtime
types, container kinds and order, aliases/references, model and stored object state,
and float bit patterns when they are durably representable. Opaque values without a
process-independent token are not recordable.

### RunRecord envelope

`record(final)` returns an immutable, slotted `RunRecord` containing exactly seven
JSON fields:

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

Unknown or missing envelope keys are rejected. The digest syntax, config identity,
JSON closure, cycles, and finite-number requirements are checked when a `RunRecord`
is constructed or parsed. `values`, `provenance`, and `to_dict()` return detached
JSON objects, so mutating a returned object cannot mutate the record.

Records contain declared field state. Computed fields are not record values. A
declared field excluded by a Pydantic serializer is rejected because the record must
not silently omit state.

### Loading

`load_record(ConfigType, value)` verifies:

- the exact seven-field v4 envelope;
- concrete type identity;
- schema fingerprint;
- semantic fingerprint;
- value fingerprint;
- the complete declared field set;
- deterministic Pydantic reconstruction;
- provenance paths, event shapes, dependency order, and integrity tokens.

Normal trusted Pydantic validators on the requested class still run. Interpolation
does not. The prohibition is a private capability that validators cannot inspect or
turn off. If a model-before validator removes a stored field, loading fails before a
default or default factory can run.

Every record field is explicit loading input, so the restored model reports every
declared field in `model_fields_set`. The original constructor's distinction between
unset and defaulted fields is composition metadata and is not stored.

A record captures what ran. It does not recover the draft, interpolation callable,
original alias spelling, sequence of mutations, external environment, or source
code.

### Trust and concurrency limits

Fingerprints and provenance tokens are unkeyed hashes. They detect accidental or
uncoordinated changes but do not authenticate the author or origin of a record. An
adversary who controls the record and application code is outside this guarantee.

`load_record()` runs trusted class validators, and fingerprinting/recording runs
trusted serializers. These APIs are not sandboxes.

Finals are shallowly frozen, so nested containers may still be mutated. Recording
and fingerprinting compare repeated serialization and structural snapshots and fail
if they observe a change, but they are not locks or atomic snapshot operations. The
caller must synchronize concurrent access.

## Transport and notebook-defined classes

`v2.0.0a4` imported `_src/transport.py` whenever `Config` was imported. That module
registered process-global `copyreg` reducers for pydantic-core `SchemaValidator` and
`SchemaSerializer` objects.

The module and import were deleted. Importing `nshconfig` no longer mutates the
process-global pickle or cloudpickle reducer tables.

Transport is now an explicit optional dependency:

```bash
pip install 'nshconfig[transport]'
```

The extra pins `cloudpickle>=3.1.2`. Importable classes use ordinary
pickle-by-reference behavior. Notebook and local classes may use cloudpickle by
value. Draft callables and provenance survive the tested sender/receiver process
boundary.

Cloudpickle remains executable, trusted, short-lived transport. It is not a durable
record and must not be loaded from an untrusted source. Sender and receiver need the
same Python version and compatible dependencies.

The existing annotation rule remains: do not enable PEP 563 with
`from __future__ import annotations` on classes intended for by-value transport.
Use eager annotations and quote only genuine forward references. This rule was
already present in `v2.0.0a4`; it was not introduced by the redesign.

## Python, Pydantic, typing, and packaging

| Area | v2.0.0a4 | Redesign |
|---|---|---|
| Package version | `2.0.0a4` | `2.1.0a0` (`2.0.0a5` was the staged implementation version) |
| Python metadata | `>=3.10,<4.0` | `>=3.10,<3.15` |
| Explicit Python classifiers | 3.10 through 3.13 | Python 3 only, 3.10 through 3.14 |
| Nox Python matrix | 3.10 through 3.13 | 3.10 through 3.14 |
| Matrix sessions | 8 | 10 |
| Runtime Pydantic | `>=2.13` | `>=2.13,<3` |
| Matrix floor | floating `>=2.13,<2.14` | exact `==2.13.0` |
| Matrix latest | unbounded `pydantic` | `>=2.13,<3` |
| Build backend | `uv_build>=0.9.18,<0.10.0` | `uv_build==0.11.28` |
| Optional extras | `docs`, `treescope` | `docs`, `transport`, `treescope` |

The old metadata admitted untested future Python versions and a future Pydantic
major release. The new bounds match the tested contract. Python 3.15 is excluded
until it is explicitly supported; this is not a claim that it can never work.

The alpha installation examples now use `--pre`. The old `pip install nshconfig`
example could select a stable 0.x release rather than the requested v2 alpha.

### Typing contract

Both revisions pass `basedpyright src` with zero errors and warnings under their
locked environments. A direct locked comparison measured public type completeness
as:

| Gate | v2.0.0a4 | Redesign |
|---|---:|---:|
| `basedpyright --verifytypes nshconfig --ignoreexternal` | 98.7%, exit 1 | 100%, exit 0 |
| Reported exported symbols | 147 known, 2 ambiguous | 19 known, 0 ambiguous |

The new CI and publish script enforce the 100% result. `__version__` is in
`__all__`, but basedpyright's verifytypes report counts 19 exported typed symbols.

The golden negative typing probe grew from 7 to 9 deliberate errors. It now checks
exact `(line, diagnostic rule)` pairs and exact count, rather than only line numbers.
It includes canaries proving that `C.Field` and legacy instance verbs are absent.
The clean probe uses `assert_type()` for `draft()` and `finalize()`.

### Source and wheel manifests

The old source distribution contained the package, metadata, license, README, and
`pyproject.toml`, but not the tests, docs, noxfile, or lockfile.

The redesigned source distribution additionally contains:

```text
CHANGELOG.md
DESIGN.md
docs/
noxfile.py
tests/
uv.lock
```

`docs/build` is explicitly excluded. The wheel remains limited to runtime package
code and distribution metadata; tests and documentation do not leak into it.

## CI, publishing, documentation, and tests

### CI

`v2.0.0a4` had no general CI workflow. It had only a documentation workflow with
floating action tags, workflow-wide Pages permissions, unlocked dependency sync,
Python 3.10, and a non-strict Sphinx build.

The redesign adds `.github/workflows/ci.yml` with five jobs:

1. tests;
2. static analysis;
3. the 10-combination Python/Pydantic support matrix;
4. strict documentation;
5. package build, manifest validation, and isolated artifact smoke tests.

The workflows now use SHA-pinned actions, disable persisted checkout credentials,
start with empty workflow-wide permissions, grant least privilege per job, pass
matrix values through environment variables, and use locked uv 0.11.28
environments. Documentation is rebuilt with warnings as errors when source, design,
README, packaging, lock, or documentation inputs change.

Static CI enforces:

```text
ruff check
ruff format --check
basedpyright src
basedpyright --verifytypes nshconfig --ignoreexternal
```

Package CI checks exact wheel and source-distribution names and manifests, installs
each artifact in isolation, and exercises draft, interpolation, finalization,
fingerprinting, record creation, and record loading.

### Publishing

The old 17-line script performed an unlocked sync, Ruff, basedpyright, pytest, a
build, and an implicit publish. It assumed `.env`, had no release-tag or clean-tree
check, accepted arbitrary arguments, and did not verify artifacts.

The new script:

- requires exact uv 0.11.28;
- uses locked dependencies;
- requires a clean tree before checks and immediately before upload;
- requires exact tag `v${project_version}` to resolve to `HEAD`;
- allows only known publish options;
- runs pytest, Ruff, basedpyright, verifytypes, strict Sphinx, and the full nox
  matrix;
- requires exactly one correctly named wheel and source distribution;
- installs and smoke-tests both artifacts;
- passes only the verified artifact paths to `uv publish`;
- treats `.env` as optional.

The implementation commit was staged as `2.0.0a5` and was deliberately not tagged.
The public redesign release advances the version to `2.1.0a0`; tag `v2.1.0a0`
identifies the exact release commit and satisfies the hardened script's release-tag
guard.

### Documentation

Documentation changed from scattered design/review material to a hierarchy with one
semantic authority.

Added:

- [DESIGN.md](DESIGN.md), the complete semantic contract;
- [CHANGELOG.md](CHANGELOG.md);
- [docs/source/api.md](docs/source/api.md), the explicit API reference;
- `docs/source/contract.md`, which includes the semantic contract;
- `docs/source/changelog.md`, which includes the changelog;
- [docs/source/guides/records.md](docs/source/guides/records.md).

Removed:

- `PYDANTIC_UPGRADE.md`, a planning study;
- `REVIEW.md`, an earlier architecture review;
- `docs/source/guides/migration.md`, because the redesign does not preserve the old
  surface.

Sphinx autodoc, Napoleon, viewcode, and intersphinx were removed in favor of a
handwritten public contract. Strict builds use:

```bash
sphinx-build -E -a -W --keep-going -b html docs/source docs/build/html
```

### Tests

The suite expanded substantially:

| Metric | v2.0.0a4 | Redesign |
|---|---:|---:|
| `tests/test_*.py` modules | 12 | 32 |
| Executed tests in a locked Python 3.13 comparison | 84 | 377 |
| Measured production statements | 751 | 4,749 |
| Statement coverage | 95% | 87% |

The percentage decreased because measured production code grew by more than six
times, while executed cases grew by roughly 4.5 times. The redesigned suite was
also verified with all 377 tests on Python 3.10 through 3.14 against both Pydantic
2.13.0 and the latest Pydantic 2.x.

New dedicated suites cover:

- adversarial lifecycle behavior;
- atomic versus structural boundaries;
- context expiry and thread confinement;
- graph cycles, aliases, annotations, and unions;
- interpolation ordering, container paths, and proxy security;
- hostile diagnostics;
- in-progress state;
- nested `model_fields_set` preservation;
- arbitrary mapping keys;
- revalidation isolation;
- record event and dependency contracts;
- record loading capabilities;
- exact runtime, alias, nested-model, and `TypedDict` schema identity;
- validation-boundary hardening.

## Exact added and removed files

The 34 additions consist of 12 implementation/workflow/documentation files and 22
test modules.

Added outside `tests/`:

```text
.github/workflows/ci.yml
CHANGELOG.md
DESIGN.md
docs/source/api.md
docs/source/changelog.md
docs/source/contract.md
docs/source/guides/records.md
src/nshconfig/_src/annotations.py
src/nshconfig/_src/draft.py
src/nshconfig/_src/records.py
src/nshconfig/_src/semantic.py
src/nshconfig/_src/state.py
```

Added test modules:

```text
tests/test_adversarial_redesign.py
tests/test_atomic_boundary.py
tests/test_context_capability.py
tests/test_core_contract.py
tests/test_draft_identity_assignment.py
tests/test_graph.py
tests/test_hostile_diagnostics.py
tests/test_in_progress_lifecycle.py
tests/test_interpolation_container_paths.py
tests/test_interpolation_contract.py
tests/test_interpolation_proxy_security.py
tests/test_known_carrier_graph.py
tests/test_nested_fields_set.py
tests/test_noncanonical_mapping_interpolation.py
tests/test_record_dependency_order.py
tests/test_record_event_contract.py
tests/test_record_load_capability.py
tests/test_record_semantic_identity.py
tests/test_records.py
tests/test_revalidation_isolation.py
tests/test_typed_dict_schema_identity.py
tests/test_validation_boundary_hardening.py
```

Removed:

```text
PYDANTIC_UPGRADE.md
REVIEW.md
docs/source/guides/migration.md
src/nshconfig/_src/transport.py
tests/test_pydantic_reexports.py
tests/test_verbs.py
```

The remaining 39 files were modified in place.

## What intentionally stayed the same

Despite the size of the rewrite, several core choices are continuous with
`v2.0.0a4`:

- `Config` subclasses are Pydantic models.
- Normal construction creates a validated, shallowly field-frozen value.
- Draft assignment is normal Python attribute assignment and remains statically
  checked.
- Required direct `Config` fields auto-create as drafts.
- `interp()` remains a whole-field Python callable, not a string expression
  language.
- Explicit field input overrides an interpolated class default.
- `parent()`, `root()`, and `nearest()` selectors remain, with stronger visibility
  rules.
- Finalization remains explicit, non-destructive, and repeatable from the draft.
- `source()`, `explain()`, and `provenance()` remain the provenance concepts.
- Notebook/local class transport remains based on trusted cloudpickle-by-value.
- Python 3.10 and Pydantic 2.13 remain the support floors.
- Eager annotations remain required for reliable notebook/cloudpickle transport;
  the repository still forbids `from __future__ import annotations`.
- There is still no YAML language, registry, loader, or code generator.

## Intentional limits of the redesigned guarantee

The redesign is stricter, but it does not claim more than it can verify:

- Interpolation callables and Pydantic validators are trusted Python, not sandboxed
  expressions.
- A fingerprint is a deterministic identifier, not a signature.
- Provenance integrity tokens do not authenticate historical authorship.
- Schema fingerprints do not hash arbitrary validator or serializer code.
- `record_schema_id` must version incompatible behavior invisible to schema output.
- A run record describes concrete values and verified provenance, not the executable
  composition recipe or external environment.
- Cloudpickle payloads are executable and suitable only for trusted, short-lived
  transport.
- Final freezing is shallow.
- Fingerprinting and recording require caller synchronization around nested
  mutation.
- Truly opaque non-callable extension state cannot be proven free of concealed
  lifecycle values.
- Read-only interpolation views protect declared structural values but do not turn
  every arbitrary Python object into a deep immutable proxy.
- There is no compatibility loader, migration registry, or automatic conversion for
  the removed alpha API.

The resulting library is narrower than `v2.0.0a4`, but its claims are now explicit:
composition is reusable Python state, interpolation consumes canonical validated
dependencies, finals are live Pydantic values, and durable records are inert data
that can be checked without replaying interpolation.
